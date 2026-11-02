"""PANORAMA Mapper — offline localhost control panel."""
from __future__ import annotations

import argparse
import hmac
import json
import logging
from logging.handlers import RotatingFileHandler
import mimetypes
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit
from mapper.paths import app_root, worker_command

ROOT = app_root()
sys.path.insert(0, str(ROOT / ".deps"))
VERSION = "1.2.0"
LOGGER = logging.getLogger("panorama.server")
LOGGER.addHandler(logging.NullHandler())
CLIENT_DISCONNECTS = (ConnectionAbortedError, ConnectionResetError, BrokenPipeError)


def setup_service_logging(directory=None, *, max_bytes=5 * 1024 * 1024, backup_count=3):
    """Keep service diagnostics locally, including after the console is closed."""
    directory = Path(directory) if directory is not None else ROOT / ".cache" / "logs"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "server.log"
    for handler in LOGGER.handlers[:]:
        if isinstance(handler, RotatingFileHandler):
            LOGGER.removeHandler(handler)
            handler.close()
    handler = RotatingFileHandler(path, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(threadName)s] %(message)s"))
    LOGGER.addHandler(handler)
    LOGGER.setLevel(logging.INFO)
    LOGGER.propagate = False
    return path


class LocalHTTPServer(ThreadingHTTPServer):
    def handle_error(self, request, client_address):
        # This fallback also covers setup/finish failures outside Handler.handle.
        if isinstance(sys.exc_info()[1], CLIENT_DISCONNECTS):
            LOGGER.debug("Client disconnected: %s", client_address)
        else:
            LOGGER.exception("Unhandled HTTP request error from %s", client_address)


def safe_child(root, relative):
    root = Path(root).resolve()
    result = (root / relative).resolve()
    if not result.is_relative_to(root) or result == root:
        raise ValueError("文件路径无效")
    return result


def parse_range(header, size):
    match = re.fullmatch(r"bytes=(\d*)-(\d*)", header or "")
    if not match or not size:
        raise ValueError("Invalid byte range")
    left, right = match.groups()
    if not left:
        count = int(right or 0)
        if count <= 0:
            raise ValueError("Invalid suffix range")
        return max(0, size-count), size-1
    start, end = int(left), int(right) if right else size-1
    if start >= size or end < start:
        raise ValueError("Unsatisfiable byte range")
    return start, min(size-1, end)


class Application:
    def __init__(self):
        from mapper.geometry import normalize_config
        from mapper.jobs import JobManager
        from mapper.dependencies import DependencyManager
        self.config = normalize_config({})
        self.token = secrets.token_urlsafe(32)
        self.sources = {}
        self.active_source = None
        self.lock = threading.RLock()
        self.cache = ROOT / ".cache"
        self.presets = ROOT / "presets"
        self.inputs = ROOT / "inputs"
        for folder in (self.cache, self.presets, self.inputs):
            folder.mkdir(exist_ok=True)
        self.dependencies = DependencyManager(ROOT)
        environment = self.dependencies.environment()
        self.blender_path = environment['tools']['blender']['path']
        self.ffmpeg_path = environment['tools']['ffmpeg']['path']
        self.jobs = JobManager(self.blender_path, self.ffmpeg_path)
        try:
            for source in json.loads((self.cache / "sources.json").read_text(encoding="utf-8")):
                if all(Path(source[k]).is_file() for k in ("path", "preview_path", "thumbnail_path")):
                    self.sources[source["id"]] = source
            if self.sources:
                self.active_source = next(reversed(self.sources))
        except (OSError, ValueError, KeyError):
            pass

    def source_public(self, source):
        return {k: v for k, v in source.items() if k not in {"path", "preview_path", "thumbnail_path", "normalized_image_path"}}

    def environment(self):
        from mapper.jobs import ACTIVE
        with self.jobs.lock:
            result = self.dependencies.environment()
            result['busy'] = any(job['status'] in ACTIVE for job in self.jobs.jobs.values())
            return result

    def _apply_tool_paths(self, environment):
        self.blender_path = environment['tools']['blender']['path']
        self.ffmpeg_path = environment['tools']['ffmpeg']['path']
        self.jobs.blender_path = self.blender_path
        self.jobs.ffmpeg_path = self.ffmpeg_path

    def update_settings(self, values):
        from mapper.jobs import ACTIVE
        with self.lock, self.jobs.lock:
            if any(job['status'] in ACTIVE for job in self.jobs.jobs.values()):
                raise ValueError('任务正在进行，完成或取消后才能修改工具路径')
            result = self.dependencies.save_paths(values)
            self._apply_tool_paths(result)
            result['busy'] = False
            return result

    def discover_tools(self):
        from mapper.jobs import ACTIVE
        with self.lock, self.jobs.lock:
            if any(job['status'] in ACTIVE for job in self.jobs.jobs.values()):
                raise ValueError('任务正在进行，完成或取消后才能扫描并更换工具')
            result = self.dependencies.discover()
            self._apply_tool_paths(result)
            result['busy'] = False
            return result

    def require_tools(self):
        if not self.dependencies.environment()['ready']:
            raise ValueError('Blender 或 FFmpeg 尚未就绪，请展开面板中的“运行环境与工具设置”完成配置')

    def state(self):
        from mapper.geometry import normalize_config
        from mapper.output import profile
        return {"version": VERSION, "config": self.config, "defaults": normalize_config({}),
                "output_profile": profile(),
                "source": self.source_public(self.sources[self.active_source]) if self.active_source else None,
                "sources": [self.source_public(s) for s in self.sources.values()], "jobs": self.jobs.list(),
                "presets": sorted(p.stem for p in self.presets.glob("*.json")),
                "blender_path": self.blender_path, "ffmpeg_path": self.ffmpeg_path,
                "output_dir": str(ROOT / "outputs"),
                "service_log_path": str(ROOT / ".cache" / "logs" / "server.log"),
                "session_token": self.token}

    def import_source(self, path):
        from mapper.media import probe_media, prepare_preview
        from mapper.jobs import write_json
        if self.dependencies.environment()['tools']['ffmpeg']['status'] != 'ready':
            raise ValueError('FFmpeg 尚未就绪，请在“运行环境与工具设置”中扫描或指定 ffmpeg.exe')
        path = Path(str(path).strip().strip('"')).expanduser().resolve()
        if not path.is_file():
            raise ValueError("找不到视频或图片文件")
        metadata = probe_media(path)
        identifier = uuid.uuid4().hex[:12]
        folder = self.cache / "media" / identifier
        folder.mkdir(parents=True)
        previews = prepare_preview(path, folder, self.ffmpeg_path)
        source = {**metadata, "id": identifier, "name": path.name, "path": str(path),
                  "preview_path": str(previews["preview_path"]), "thumbnail_path": str(previews["thumbnail_path"]),
                  "preview_url": f"/media/{identifier}/preview", "thumbnail_url": f"/media/{identifier}/thumbnail"}
        if previews.get("normalized_image_path"):
            source["normalized_image_path"] = str(previews["normalized_image_path"])
        with self.lock:
            self.sources[identifier] = source
            self.active_source = identifier
            write_json(self.cache / "sources.json", list(self.sources.values()))
        return self.source_public(source)

    def calibration(self):
        from mapper.output import WIDTH, HEIGHT
        from mapper.media import create_calibration
        path = ROOT / "examples" / f"calibration-{WIDTH}x{HEIGHT}.png"
        path.parent.mkdir(exist_ok=True)
        if not path.exists():
            create_calibration(path)
        return self.import_source(path)

    def brightness_calibration(self):
        from mapper.media import create_brightness_calibration
        path = self.cache / "brightness-gray-128.png"
        if not path.exists():
            create_brightness_calibration(path)
        return self.import_source(path)


class Handler(BaseHTTPRequestHandler):
    server_version = "PanoramaMapper/" + VERSION
    protocol_version = "HTTP/1.1"

    @property
    def app(self):
        return self.server.app

    def log_message(self, fmt, *args):
        if args and str(args[1] if len(args) > 1 else "").startswith(("4", "5")):
            LOGGER.warning("HTTP %s", fmt % args)

    def handle(self):
        # Browser seeking, replacing a video, or closing a tab can abort either
        # the current response or the next keep-alive request-line read.
        try:
            super().handle()
        except CLIENT_DISCONNECTS:
            self.close_connection = True
            LOGGER.debug("Client disconnected during HTTP read/write")

    def _valid_host(self):
        return self.headers.get("Host", "") == f"127.0.0.1:{self.server.server_port}"

    def json(self, value, status=200):
        payload = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(payload)

    def file(self, path, download=False, head=False):
        path = Path(path)
        if not path.is_file():
            self.json({"error": "文件不存在"}, 404); return
        size = path.stat().st_size
        start, end, status = 0, size-1, 200
        if self.headers.get("Range"):
            try:
                start, end = parse_range(self.headers["Range"], size); status = 206
            except ValueError:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers(); return
        media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if path.suffix == ".js":
            media_type = "application/javascript"
        self.send_response(status)
        self.send_header("Content-Type", media_type)
        self.send_header("Content-Length", str(end-start+1))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        if download:
            from urllib.parse import quote
            self.send_header("Content-Disposition", "attachment; filename*=UTF-8''" + quote(path.name))
        self.end_headers()
        if head:
            return
        try:
            with path.open("rb") as stream:
                stream.seek(start)
                remaining = end-start+1
                while remaining > 0:
                    chunk = stream.read(min(1024*1024, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except CLIENT_DISCONNECTS:
            self.close_connection = True

    def do_HEAD(self):
        self.do_GET(head=True)

    def do_GET(self, head=False):
        if not self._valid_host():
            self.json({"error": "请使用启动器显示的 127.0.0.1 地址"}, 403); return
        parts = urlsplit(self.path)
        path = unquote(parts.path)
        try:
            if path == "/api/state":
                self.json(self.app.state())
            elif path == "/api/environment":
                self.json(self.app.environment())
            elif path == "/api/jobs":
                self.json({"jobs": self.app.jobs.list()})
            elif path == '/api/capabilities':
                self.json(self.app.jobs.capabilities())
            elif path == "/api/preset":
                name = parse_qs(parts.query).get("name", [""])[0]
                preset = safe_child(self.app.presets, name + ".json")
                self.json(json.loads(preset.read_text(encoding="utf-8")))
            elif path.startswith("/media/"):
                _, _, identifier, kind = path.split("/")
                source = self.app.sources[identifier]
                if kind not in {"preview", "thumbnail"}:
                    raise ValueError("资源类型无效")
                self.file(source[kind + "_path"], head=head)
            elif path.startswith("/output/"):
                _, _, identifier, filename = path.split("/", 3)
                job = self.app.jobs.jobs[identifier]
                if filename != "render.log" and (job["status"] != "completed" or filename not in {Path(job["output_path"]).name, "panorama.blend", "first-frame.png"}):
                    raise ValueError("文件尚未完成或不可下载")
                self.file(safe_child(job["directory"], filename), download=True, head=head)
            elif path == "/":
                self.file(ROOT / "web" / "index.html", head=head)
            elif path.startswith("/web/"):
                self.file(safe_child(ROOT / "web", path[5:]), head=head)
            elif path.startswith("/decoration/"):
                self.file(safe_child(self.app.cache / "decorations", path[12:]), head=head)
            elif path == "/favicon.ico":
                self.send_response(204); self.send_header("Content-Length", "0"); self.end_headers()
            else:
                self.json({"error": "地址不存在"}, 404)
        except CLIENT_DISCONNECTS:
            self.close_connection = True
        except (ValueError, KeyError, OSError) as exc:
            if isinstance(exc, OSError):
                LOGGER.exception("GET resource failure: %s", parts.path)
            self.json({"error": str(exc)}, 400)

    def do_POST(self):
        origin = self.headers.get("Origin")
        expected_origin = f"http://127.0.0.1:{self.server.server_port}"
        token = self.headers.get("X-Session-Token", "")
        if not self._valid_host() or (origin and origin != expected_origin) or not hmac.compare_digest(token, self.app.token):
            self.close_connection = True
            self.json({"error": "会话已过期，请刷新面板"}, 403); return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 0:
                raise ValueError("请求长度无效")
            if self.path == "/api/upload":
                if not 0 < length <= 20 * 1024**3:
                    raise ValueError("请选择小于20GB的本地文件，较大文件可使用路径导入")
                if shutil.disk_usage(self.app.inputs).free < length + 100 * 1024**2:
                    raise ValueError("项目所在磁盘空间不足")
                raw_name = unquote(self.headers.get("X-Filename", "video.mp4"))
                name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", raw_name)[-120:]
                target = self.app.inputs / (uuid.uuid4().hex[:10] + "-" + name)
                with target.open("wb") as file:
                    remaining = length
                    while remaining:
                        chunk = self.rfile.read(min(1024*1024, remaining))
                        if not chunk:
                            raise ValueError("文件上传被中断")
                        file.write(chunk); remaining -= len(chunk)
                self.json(self.app.import_source(target)); return
            if length > 1024*1024:
                raise ValueError("请求过大")
            raw = self.rfile.read(length)
            data = json.loads(raw) if raw else {}
            if not isinstance(data, dict):
                raise ValueError("请求必须是JSON对象")
            from mapper.geometry import normalize_config, mapping_mesh
            from mapper.jobs import write_json
            if self.path == "/api/settings":
                self.json(self.app.update_settings(data))
            elif self.path == "/api/discover":
                self.json(self.app.discover_tools())
            elif self.path == "/api/import":
                self.json(self.app.import_source(data["path"]))
            elif self.path == "/api/pick":
                result = subprocess.run(worker_command('pick-file'),
                                        capture_output=True, encoding="utf-8", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                if result.returncode:
                    raise ValueError("文件选择器未能打开，可使用拖入或路径导入")
                chosen = result.stdout.strip()
                self.json(self.app.import_source(chosen) if chosen else {"cancelled": True})
            elif self.path == "/api/calibration":
                self.json(self.app.calibration())
            elif self.path == "/api/brightness-calibration":
                self.json(self.app.brightness_calibration())
            elif self.path == "/api/mesh":
                config = normalize_config(data.get("config", {}))
                mesh = mapping_mesh(config, data.get("source_aspect", 16/9))
                config = mesh.get("config", config)
                self.app.config = config
                decoration_url = None
                if config.get("presentation") == "standee" and config["fit"] == "contain":
                    from mapper.decoration import standee_asset
                    asset = standee_asset(data.get("source_aspect", 16/9), self.app.cache / "decorations")
                    decoration_url = "/decoration/" + asset.name
                self.json({"config": config, "mesh": mesh, "decoration_url": decoration_url})
            elif self.path == "/api/preset":
                name = str(data.get("name", "")).strip()
                if not name or len(name) > 80 or re.search(r'[<>:"/\\|?*\x00-\x1f]', name) or name.endswith((".", " ")):
                    raise ValueError("请输入有效预设名称")
                result = {"name": name, "config": normalize_config(data["config"])}
                write_json(safe_child(self.app.presets, name + ".json"), result)
                self.json(result)
            elif self.path == "/api/estimate":
                from mapper.jobs import validate_export
                from mapper.delivery import MAX_MP4_BYTES, budget_bitrate
                options = validate_export(data, self.app.sources[data["source_id"]])
                enforced = options["quality"] == "high"
                self.json({"limit_bytes": MAX_MP4_BYTES, "target_bytes": 480_000_000,
                           "budget_bitrate": budget_bitrate(options) if enforced else None,
                           "frames": options["frames"],
                           "duration": options["frames"] * options["fps_den"] / options["fps_num"],
                           "enforced": enforced})
            elif self.path == "/api/render":
                self.app.require_tools()
                source = self.app.sources[data["source_id"]]
                self.json(self.app.jobs.start(source, data))
            elif self.path == "/api/cancel":
                self.json(self.app.jobs.cancel(data["job_id"]))
            elif self.path == "/api/shutdown":
                self.json({"stopping": True})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
            else:
                self.json({"error": "地址不存在"}, 404)
        except CLIENT_DISCONNECTS:
            self.close_connection = True
        except (ValueError, KeyError, OSError, RuntimeError) as exc:
            self.close_connection = True
            if isinstance(exc, (OSError, RuntimeError)):
                LOGGER.exception("POST operation failure: %s", self.path)
            self.json({"error": str(exc)}, 400)
        except Exception as exc:
            self.close_connection = True
            LOGGER.exception("Unexpected POST failure: %s", self.path)
            self.json({"error": "操作失败：" + str(exc)}, 500)


def main():
    log_path = setup_service_logging()
    parser = argparse.ArgumentParser(description="PANORAMA Mapper")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args()
    LOGGER.info("Starting PANORAMA Mapper %s pid=%s project=%s", VERSION, os.getpid(), ROOT)
    # Reuse only a live instance belonging to this exact project.
    if args.port == 0:
        try:
            from urllib.request import urlopen
            previous = json.loads((ROOT / ".cache" / "server.json").read_text(encoding="utf-8"))
            with urlopen(previous["url"] + "/api/state", timeout=1) as response:
                state = json.load(response)
            if state.get("output_dir") == str(ROOT / "outputs") and state.get("version") == VERSION:
                LOGGER.info("Reusing existing service: %s", previous["url"])
                print("复用已运行面板：" + previous["url"], flush=True)
                if not args.no_browser:
                    webbrowser.open(previous["url"])
                return
        except (OSError, ValueError, KeyError):
            pass
    application = Application()
    server = LocalHTTPServer(("127.0.0.1", args.port), Handler)
    server.daemon_threads = True
    server.app = application
    url = f"http://127.0.0.1:{server.server_port}"
    (ROOT / ".cache" / "server.json").write_text(json.dumps({"url": url, "pid": os.getpid()}), encoding="utf-8")
    print("PANORAMA Mapper " + VERSION + "\n" + url + "\n关闭此窗口会停止服务。", flush=True)
    print("服务日志：" + str(log_path), flush=True)
    LOGGER.info("Listening on %s", url)
    if not args.no_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        LOGGER.info("Stopping service; cancelling unfinished jobs")
        application.jobs.shutdown()
        server.server_close()
        LOGGER.info("Service stopped")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        LOGGER.exception("Service terminated unexpectedly")
        raise
