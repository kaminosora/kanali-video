"""Local, single-job Blender exports with cancellable subprocesses."""
from __future__ import annotations

import json
import math
import os
import re
import subprocess
import threading
import time
import traceback
import uuid
from datetime import datetime
from fractions import Fraction
from pathlib import Path
from mapper.storage import write_json
from mapper.paths import app_root, worker_command

ROOT = app_root()
ACTIVE = {"queued", "preparing", "rendering", "encoding"}
NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
RENDER_CANCEL_GRACE_SECONDS = 5


def _number(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{label}必须是有限数值")
    return float(value)


def validate_export(raw: dict, source: dict) -> dict:
    from mapper.output import WIDTH, HEIGHT
    start = _number(raw.get("start", 0), "开始时间")
    default_end = 5 if source["kind"] == "image" else source["duration"]
    end = _number(default_end if raw.get("end") is None else raw["end"], "结束时间")
    if start < 0 or end <= start:
        raise ValueError("时间范围应满足 0 ≤ 开始 < 结束")
    if source["kind"] != "image" and end > source["duration"] + 0.001:
        raise ValueError("结束时间超过原片时长")
    if end - start > 3600:
        raise ValueError("单次最多导出一小时，请分段导出")
    fps = _number(raw.get("fps", 0), "帧率")
    if fps < 0 or fps > 60 or (0 < fps < 1):
        raise ValueError("帧率应为 1–60，或 0 自动匹配")
    if fps == 0:
        rate = Fraction(source.get("fps_num", 30), source.get("fps_den", 1)) if source["kind"] == "video" else Fraction(30)
        if rate <= 0:
            rate = Fraction(30)
        rate = min(rate, Fraction(60))
    else:
        rate = Fraction(str(fps)).limit_denominator(1001)
    quality = raw.get("quality", "high")
    if quality not in {"high", "master"}:
        raise ValueError("画质模式无效")
    renderer = raw.get('renderer','auto')
    encoder = raw.get('encoder','auto')
    if renderer not in {'auto','gpu','blender'} or encoder not in {'auto','libx264','h264_nvenc','h264_qsv','h264_amf'}:
        raise ValueError('渲染或编码模式无效')
    supersample = raw.get("supersample", 2)
    if isinstance(supersample, bool) or supersample not in (1, 2):
        raise ValueError("超采样仅支持 1× 或 2×")
    name = str(raw.get("name", "panorama")).strip() or "panorama"
    if len(name) > 70 or re.search(r'[<>:"/\\|?*\x00-\x1f]', name) or name in {".", ".."} or name.endswith((".", " ")):
        raise ValueError("输出名称不能包含路径或 Windows 特殊字符")
    return {"start": start, "end": end, "duration": end - start,
            "fps_num": rate.numerator, "fps_den": rate.denominator,
            "frames": max(1, math.ceil((end-start) * float(rate) - 1e-9)),
            "quality": quality, "supersample": int(supersample), "name": name,
            'renderer':renderer,'encoder':encoder,'output_width':WIDTH,'output_height':HEIGHT}


def read_ffmpeg_progress(path):
    """Read only the bounded tail, accepting complete FFmpeg progress records."""
    try:
        with Path(path).open('rb') as stream:
            stream.seek(0, 2)
            start = max(0, stream.tell() - 16384)
            stream.seek(start)
            lines = stream.read().decode('utf-8', errors='replace').splitlines()
        if start:
            lines = lines[1:]
        latest, record = {}, {}
        for line in lines:
            key, sep, value = line.partition('=')
            if sep:
                record[key.strip()] = value.strip()
                if key.strip() == 'progress':
                    latest, record = record, {}
        return latest
    except OSError:
        return {}


def _duration_text(seconds):
    seconds = max(0, int(seconds))
    return f'{seconds // 60}分{seconds % 60:02d}秒'


class JobManager:
    def __init__(self, blender_path, ffmpeg_path):
        self.blender_path, self.ffmpeg_path = str(blender_path), str(ffmpeg_path)
        self.output_dir = ROOT / "outputs"
        self.output_dir.mkdir(exist_ok=True)
        self.lock = threading.RLock()
        self.jobs = {}
        self.processes = {}
        self.threads = {}
        self._capability_cache = None
        for path in self.output_dir.glob("*/job.json"):
            try:
                job = json.loads(path.read_text(encoding="utf-8"))
                if job["status"] in ACTIVE:
                    job.update(status="failed", error="上次服务关闭时任务未完成，请重新导出", output_path=None, output_url=None,
                               finished_at=time.time())
                elif 'finished_at' not in job:
                    # Older manifests predate explicit timestamps; freeze them
                    # at their final on-disk update instead of counting forever.
                    job['finished_at'] = path.stat().st_mtime
                self.jobs[job["id"]] = job
            except (OSError, ValueError, KeyError):
                continue

    def _save(self, job):
        write_json(Path(job["directory"]) / "job.json", job)

    def capabilities(self):
        from mapper.capabilities import detect_capabilities
        try:
            stamp=Path(self.ffmpeg_path).stat().st_mtime_ns
        except OSError:
            stamp=0
        key=(self.ffmpeg_path,stamp)
        cached=getattr(self,'_capability_cache',None)
        if cached is None or cached[0]!=key:
            self._capability_cache=(key,detect_capabilities(self.ffmpeg_path))
        return self._capability_cache[1]

    def _update(self, job, **changes):
        with self.lock:
            changes['updated_at'] = time.time()
            if changes.get('status') in {'completed', 'cancelled', 'failed'}:
                changes['finished_at'] = time.time()
            job.update(changes)
            self._save(job)

    def start(self, source, request):
        from mapper.geometry import constrain_framing
        config = constrain_framing(request.get("config", {}), source["aspect"])
        options = validate_export(request, source)
        if not Path(self.blender_path).is_file() or not Path(self.ffmpeg_path).is_file():
            raise ValueError("未找到 Blender 或 FFmpeg，请检查 settings.json")
        with self.lock:
            if any(j["status"] in ACTIVE for j in self.jobs.values()):
                raise ValueError("已有任务正在导出，请等待完成或取消")
            identifier = uuid.uuid4().hex[:12]
            folder = self.output_dir / (datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + options["name"] + "-" + identifier[:6])
            folder.mkdir()
            job = {"id": identifier, "status": "queued", "progress": 0, "message": "等待准备",
                   "frame": 0, "total_frames": options["frames"], "output_path": None, "output_url": None,
                   "blend_path": None, "log_path": str(folder / "render.log"), "error": None,
                   "warnings": list(source.get("warnings", [])), "directory": str(folder),
                   "config": config, "options": options, "source": source, "created": time.time()}
            self.jobs[identifier] = job
            self._save(job)
            thread = threading.Thread(target=self._run, args=(job,), daemon=True, name="render-"+identifier)
            self.threads[identifier] = thread
            thread.start()
            return self.public(job)

    def public(self, job):
        result = {k: v for k, v in job.items() if k not in {"source", "directory", "config", "options"}}
        folder = Path(job['directory'])
        result['log_url'] = f"/output/{job['id']}/render.log"
        end_time = time.time() if job['status'] in ACTIVE else job.get('finished_at', job.get('updated_at', job.get('created', time.time())))
        result['elapsed_seconds'] = max(0, end_time - job.get('created', end_time))
        if job['status'] == 'preparing' and job.get('phase') == 'normalize':
            progress_path = folder / 'prepare-progress.txt'
            data = read_ffmpeg_progress(progress_path)
            try:
                frame = max(0, min(job['total_frames'], int(data.get('frame', 0))))
                size = max(0, int(data.get('total_size', 0)))
            except (ValueError, TypeError):
                frame, size = 0, 0
            fraction = frame / max(1, job['total_frames'])
            elapsed = max(0, time.time() - job.get('phase_started', job.get('created', time.time())))
            result.update(phase_progress=fraction, progress=0.01 + 0.11*fraction,
                          prepared_frames=frame, intermediate_bytes=size)
            result['message'] = f"准备素材 {frame} / {job['total_frames']} 帧 · 已用 {_duration_text(elapsed)}"
            if size:
                result['message'] += f' · 中间文件 {size / (1024**3):.2f} GiB'
            if frame > 0 and elapsed >= 2:
                result['message'] += f' · 本阶段预计剩余 {_duration_text(elapsed * (1-fraction) / fraction)}'
            elif job.get('options', {}).get('start', 0) > 0:
                result['message'] += ' · 正在解码到所选入点'
            try:
                result['updated_at'] = progress_path.stat().st_mtime
            except OSError:
                pass
        if job["status"] == "rendering":
            try:
                info = json.loads((Path(job["directory"]) / "progress.json").read_text(encoding="utf-8"))
                result.update(frame=info.get("frame", 0), message=info.get("message", "Blender 渲染中"))
                result["progress"] = 0.15 + 0.80 * min(1, info.get("frame", 0) / max(1, job["total_frames"]))
                result['updated_at'] = (folder / 'progress.json').stat().st_mtime
            except (OSError, ValueError):
                pass
        if job['status']=='encoding' and job.get('delivery_progress_file'):
            data=read_ffmpeg_progress(job['delivery_progress_file'])
            try:
                fraction=min(1,max(0,int(data.get('frame',0))/max(1,job['total_frames'])))
                step=job.get('delivery_pass',1)
                result['progress']=.95+.04*((step-1+fraction)/2)
                result['message']=f"自动控制文件大小 · 第 {job.get('delivery_attempt',1)} 轮 / 第 {step} 遍 · {fraction:.0%}"
            except (ValueError,TypeError):
                pass
        return result

    def list(self):
        with self.lock:
            return [self.public(j) for j in sorted(self.jobs.values(), key=lambda j: j.get("created", 0), reverse=True)]

    def cancel(self, identifier):
        with self.lock:
            if identifier not in self.jobs:
                raise ValueError("任务不存在")
            job = self.jobs[identifier]
            if job["status"] in ACTIVE:
                (Path(job["directory"]) / "cancel.request").touch()
                job["message"] = "正在取消，当前帧完成后停止"
                if job["status"] in {'preparing','encoding'} and identifier in self.processes:
                    self.processes[identifier].terminate()
                self._save(job)
            return self.public(job)

    def shutdown(self):
        for job in list(self.jobs.values()):
            if job["status"] in ACTIVE:
                self.cancel(job["id"])
        for thread in list(self.threads.values()):
            thread.join(timeout=10)
        for proc in list(self.processes.values()):
            self._stop_process(proc)

    @staticmethod
    def _stop_process(proc, log=None):
        if proc.poll() is not None:
            return
        # Workers own encoder children; on Windows stop the whole tree before
        # reaping its parent so its descendants retain no output file handles.
        if os.name == 'nt':
            try:
                subprocess.run(['taskkill', '/PID', str(proc.pid), '/T', '/F'],
                               stdout=log if log is not None else subprocess.DEVNULL,
                               stderr=subprocess.STDOUT, creationflags=NO_WINDOW, timeout=5)
            except (OSError, subprocess.SubprocessError) as exc:
                if log is not None:
                    log.write(f'Process-tree cleanup failed: {exc}\n'.encode('utf-8'))
                    log.flush()
        if proc.poll() is None:
            proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()

    def _process(self, job, command, log):
        environment = os.environ.copy()
        temp = ROOT / ".cache" / "tmp"
        temp.mkdir(parents=True, exist_ok=True)
        environment.update(TEMP=str(temp), TMP=str(temp), PYTHONUTF8="1")
        log.write(("\nCOMMAND: " + repr(command) + "\n").encode("utf-8")); log.flush()
        with self.lock:
            if (Path(job["directory"]) / "cancel.request").exists():
                return -1
            proc = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, env=environment, creationflags=NO_WINDOW)
            self.processes[job["id"]] = proc
        cancellation_started = None
        try:
            while proc.poll() is None:
                if (Path(job["directory"]) / "cancel.request").exists():
                    if job["status"] in {'preparing','encoding'}:
                        proc.terminate()
                    elif cancellation_started is None:
                        cancellation_started = time.monotonic()
                    elif time.monotonic() - cancellation_started >= RENDER_CANCEL_GRACE_SECONDS:
                        # Blender/GPU normally cancel between frames. A blocked
                        # pipe or driver must not make cancellation unbounded.
                        self._stop_process(proc, log)
                try:
                    proc.wait(timeout=0.2)
                except subprocess.TimeoutExpired:
                    pass
            return proc.returncode
        finally:
            try:
                self._stop_process(proc, log)
            finally:
                with self.lock:
                    self.processes.pop(job["id"], None)

    def _run(self, job):
        folder = Path(job["directory"])
        options, source = job["options"], job["source"]
        cancelled = lambda: (folder / "cancel.request").exists()
        normalized = None
        normalized_verified = False
        output = candidate = None
        output_owned = False
        try:
            with Path(job["log_path"]).open("wb") as log:
                if cancelled():
                    raise InterruptedError()
                from mapper.capabilities import select_backend
                from mapper.delivery import budget_bitrate, finalize_mp4, MAX_MP4_BYTES
                self._update(job,status='preparing',phase='capabilities',phase_progress=0,message='检测显卡与视频编码能力')
                backend=select_backend(self.capabilities(),options.get('renderer','auto'),options.get('encoder','auto'),options['quality'])
                options['encoder']=backend['encoder']
                if options['quality']=='high':
                    options['budget_maxrate']=min(80_000_000,budget_bitrate(options))
                self._update(job,backend=backend,options=options)
                self._update(job, status="preparing", phase='normalize', phase_started=time.time(),
                             phase_progress=0, message="准备无损素材")
                source_path = source.get("normalized_image_path", source["path"])
                if source["kind"] == "video":
                    from mapper.media import export_normalization_filter, verify_normalized, intermediate_pixel_format
                    work = folder / "work"
                    work.mkdir(exist_ok=True)
                    normalized = work / "source.mkv"
                    filters = export_normalization_filter(source, options["start"], options["fps_num"], options["fps_den"], options["frames"])
                    pixel_format = intermediate_pixel_format(source)
                    command = [self.ffmpeg_path, "-hide_banner", "-nostdin", "-y", "-noautorotate",
                               '-nostats', '-stats_period', '0.5', '-progress', str(folder / 'prepare-progress.txt'),
                               "-i", source_path, "-map", f"0:{source.get('video_stream_index', 0)}",
                               "-vf", filters, "-an", "-sn", "-dn", "-c:v", "ffv1", "-level", "3", "-pix_fmt", pixel_format]
                    if pixel_format != 'gbrp16le':
                        for name in ('color_primaries', 'color_trc', 'colorspace', 'color_range'):
                            if name in source:
                                command.extend(['-' + name, str(source[name])])
                    command.extend(["-frames:v", str(options["frames"]), str(normalized)])
                    self._update(job, intermediate_pixel_format=pixel_format)
                    code = self._process(job, command, log)
                    if cancelled():
                        raise InterruptedError()
                    if code:
                        raise RuntimeError("FFmpeg 无损准备失败，请查看 render.log")
                    self._update(job, phase='verify', phase_progress=0, progress=0.12,
                                 message='校验中间文件帧数与时间戳')
                    def verification_progress(count):
                        self._update(job, phase_progress=count/options['frames'],
                                     progress=0.12 + 0.03*count/options['frames'],
                                     message=f"校验中间文件 {count} / {options['frames']} 帧")
                    verify_normalized(normalized, options["frames"], progress=verification_progress, cancelled=cancelled)
                    normalized_verified = True
                    source_path = str(normalized)
                if cancelled():
                    raise InterruptedError()
                render_config = {"config": job["config"], "options": options, "source_path": source_path,
                                 "source_aspect": source["aspect"], "source_kind": source["kind"],
                                 "directory": str(folder), "ffmpeg_path": self.ffmpeg_path}
                extension = '.mp4' if options['quality']=='high' else '.mkv'
                output=folder/(options['name']+extension)
                candidate=folder/'work'/('render-candidate'+extension)
                candidate.parent.mkdir(exist_ok=True)
                render_config['candidate_path']=str(candidate)
                render_config['build_only']=backend['renderer']=='gpu'
                if job["config"].get("presentation") == "standee" and job["config"]["fit"] == "contain":
                    from mapper.decoration import standee_asset
                    render_config["decoration_path"] = str(standee_asset(source["aspect"], ROOT / ".cache" / "decorations"))
                write_json(folder / "render-settings.json", render_config)
                self._update(job, status="rendering", phase='render', progress=0.15, message="Blender 正在建立映射工程")
                command = [self.blender_path, "--background", "--factory-startup", "--disable-autoexec", "--python-exit-code", "1",
                           "--python", str(ROOT / "blender" / "render.py"), "--", str(folder / "render-settings.json")]
                code = self._process(job, command, log)
                if cancelled():
                    raise InterruptedError()
                if code:
                    raise RuntimeError("Blender 渲染失败，请查看 render.log")
                if backend['renderer']=='gpu':
                    code=self._process(job,worker_command('gpu-worker',folder/'render-settings.json'),log)
                    if cancelled():raise InterruptedError()
                    if code and job['options'].get('renderer','auto')=='auto':
                        # Failed GL context/readback/encoder: retain the configured
                        # geometry, rebuild via Blender with verified CPU encoding.
                        if not self.capabilities().get('encoders',{}).get('libx264',{}).get('available'):
                            raise RuntimeError('GPU 导出失败且软件编码不可用；请查看 render.log')
                        options['encoder']='libx264';render_config['build_only']=False
                        self._update(job,backend={'renderer':'blender','encoder':'libx264'},
                                     message='GPU 快速导出失败，自动改用 Blender 与软件编码')
                        write_json(folder/'render-settings.json',render_config)
                        code=self._process(job,command,log)
                    if cancelled():raise InterruptedError()
                    if code:raise RuntimeError('GPU/回退渲染失败，请查看 render.log 和 encode.log')
                if not candidate.is_file() or candidate.stat().st_size==0:
                    raise RuntimeError('渲染未生成完整候选视频')
                if options['quality']=='high':
                    self._update(job,status='encoding',phase='delivery',progress=.95,message='验证成片大小；必要时自动调整码率')
                    def delivery_progress(event):
                        changes={}
                        if event['phase']=='encoding':
                            changes.update(delivery_progress_file=event.get('progress_file'),
                                           delivery_attempt=event.get('attempt',1),delivery_pass=event.get('pass_number',1),
                                           message='成片超过限制，正在自动调整码率')
                        if changes:self._update(job,**changes)
                    result=finalize_mp4(candidate,output,options,ffmpeg_path=self.ffmpeg_path,
                                         runner=lambda cmd:self._process(job,cmd,log),cancelled=cancelled,progress=delivery_progress)
                    output_owned = True
                    write_json(folder/'delivery.json',result)
                    self._update(job,delivery=result)
                else:
                    candidate.replace(output)
                    output_owned = True
                warnings = job["warnings"][:]
                if options["quality"] == "master":
                    warnings.append("FFV1 RGB 母版用于归档；导入 KANALI 请另导出兼容 MP4。")
                # cancel() uses this same lock: once accepted, cancellation can
                # never be overwritten by completion, including after delivery.
                with self.lock:
                    if cancelled():
                        raise InterruptedError()
                    if not output.is_file() or output.stat().st_size == 0:
                        raise RuntimeError("渲染进程未生成完整视频")
                    if options['quality']=='high' and output.stat().st_size>=MAX_MP4_BYTES:
                        raise RuntimeError('成片未通过严格 500 MB 验收')
                    self._update(job, status="completed", progress=1, frame=options["frames"], message="导出完成",
                                 output_path=str(output), output_url=f"/output/{job['id']}/{output.name}",
                                 blend_path=str(folder / "panorama.blend"), warnings=warnings)
                try:
                    candidate.unlink(missing_ok=True)
                except OSError as exc:
                    # The accepted delivery remains valid if scratch cleanup fails.
                    try:
                        log.write(f'Candidate cleanup failed: {exc}\n'.encode('utf-8'))
                        log.flush()
                    except OSError:
                        pass
        except InterruptedError:
            self._update(job, status="cancelled", message="已取消；原视频保持原样", output_path=None, output_url=None)
        except Exception as exc:
            try:
                with Path(job['log_path']).open('a', encoding='utf-8') as log:
                    traceback.print_exc(file=log)
            except OSError:
                pass  # A full disk must not prevent the in-memory terminal state.
            try:
                self._update(job, status="failed", message="导出失败", error=str(exc), output_path=None, output_url=None)
            except OSError:
                pass  # Retry persistence after releasing the partial intermediate.
        finally:
            if output_owned and job['status'] != 'completed':
                try:
                    if options['quality']=='master' and output.exists():
                        output.replace(candidate)
                    else:
                        output.unlink(missing_ok=True)
                except OSError as exc:
                    try:
                        with Path(job['log_path']).open('a', encoding='utf-8') as log:
                            log.write(f'无法清理未交付成片：{exc}\n')
                    except OSError:
                        pass
            # Only this job's unverified generated intermediate is disposable.
            # A verified movie referenced by an editable .blend remains intact.
            if normalized is not None and not normalized_verified and normalized.exists():
                try:
                    normalized.unlink()
                except OSError as exc:
                    try:
                        with Path(job['log_path']).open('a', encoding='utf-8') as log:
                            log.write(f'无法清理未完成中间文件：{exc}\n')
                    except OSError:
                        pass
            if job['status'] not in ACTIVE:
                try:
                    with self.lock:
                        self._save(job)
                except OSError:
                    pass

