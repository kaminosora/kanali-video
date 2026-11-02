"""Read-only, bounded discovery of external executables; only project settings are writable."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

DOWNLOADS = {'blender':'https://www.blender.org/download/', 'ffmpeg':'https://ffmpeg.org/download.html#build-windows'}
TOOL_KEYS = {'blender':'blender_path', 'ffmpeg':'ffmpeg_path'}
MAX_CANDIDATES = 12


def normalize_path(value, root):
    value = os.path.expandvars(str(value).strip().strip('"'))
    if not value:
        return ''
    path = Path(value).expanduser()
    return str((path if path.is_absolute() else Path(root) / path).resolve())


def registry_locations():
    """Read known App Paths/uninstall keys only. Never execute registry command strings."""
    if os.name != 'nt':
        return []
    import winreg
    locations = []
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            access = winreg.KEY_READ | view
            for tool in TOOL_KEYS:
                try:
                    with winreg.OpenKey(hive, rf'SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{tool}.exe', 0, access) as key:
                        value = winreg.QueryValueEx(key, '')[0]
                        if isinstance(value, str):
                            locations.append((tool, value))
                except OSError:
                    pass
            try:
                with winreg.OpenKey(hive, r'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall', 0, access) as parent:
                    for index in range(min(winreg.QueryInfoKey(parent)[0], 512)):
                        try:
                            with winreg.OpenKey(parent, winreg.EnumKey(parent, index)) as key:
                                name = str(winreg.QueryValueEx(key, 'DisplayName')[0]).lower()
                                tool = 'blender' if 'blender' in name else 'ffmpeg' if 'ffmpeg' in name else None
                                if tool:
                                    folder = Path(str(winreg.QueryValueEx(key, 'InstallLocation')[0]).strip('"'))
                                    locations.extend([(tool, str(folder/f'{tool}.exe')), (tool, str(folder/'bin'/f'{tool}.exe'))])
                        except OSError:
                            continue
            except OSError:
                pass
    return locations


def candidate_paths(root, settings, *, environ=None, which=None, registry=None):
    """At most twelve candidates per tool; no recursive walks or drive enumeration."""
    root = Path(root)
    environ = os.environ if environ is None else environ
    which = shutil.which if which is None else which
    registry = registry_locations if registry is None else registry
    result, seen = {tool:[] for tool in TOOL_KEYS}, {tool:set() for tool in TOOL_KEYS}

    def add(tool, path, origin, explicit=False):
        if not path or len(result[tool]) >= MAX_CANDIDATES:
            return
        path = normalize_path(path, root)
        identity = os.path.normcase(path)
        if identity in seen[tool] or not explicit and not Path(path).is_file():
            return
        seen[tool].add(identity)
        result[tool].append({'path':path,'origin':origin})

    def children(folder, limit=32):
        try:
            # islice bounds even large installation directories; no recursive enumeration.
            from itertools import islice
            return list(islice(Path(folder).iterdir(), limit))
        except OSError:
            return []

    for tool, key in TOOL_KEYS.items():
        add(tool, settings.get(key), 'settings', explicit=True)
        add(tool, which(tool), 'PATH')
        for relative in (f'tools/{tool}.exe', f'tools/bin/{tool}.exe', f'tools/{tool}/{tool}.exe', f'tools/{tool}/bin/{tool}.exe'):
            add(tool, root/relative, 'project tools')
    for folder in children(root/'tools'):
        if folder.is_dir():
            for tool in TOOL_KEYS:
                if folder.name.lower().startswith(tool):
                    add(tool, folder/f'{tool}.exe', 'project tools')
                    add(tool, folder/'bin'/f'{tool}.exe', 'project tools')
    common = []
    for key in ('ProgramFiles','ProgramFiles(x86)','ProgramW6432'):
        if environ.get(key):
            common.append(Path(environ[key]))
    if environ.get('LOCALAPPDATA'):
        common.append(Path(environ['LOCALAPPDATA'])/'Programs')
    for base in common:
        for tool in TOOL_KEYS:
            add(tool, base/tool/f'{tool}.exe', 'common install')
            add(tool, base/tool/'bin'/f'{tool}.exe', 'common install')
        for folder in children(base/'Blender Foundation'):
            add('blender', folder/'blender.exe', 'common install')
        for folder in children(base):
            if folder.name.lower().startswith('ffmpeg'):
                add('ffmpeg', folder/'bin'/'ffmpeg.exe', 'common install')
    for tool, path in registry():
        if tool in result:
            add(tool, path, 'Windows registry')
    return result


def probe_executable(tool, path, *, runner=None):
    if tool not in TOOL_KEYS:
        raise ValueError('未知工具')
    path = str(path)
    result = {'path':path,'status':'missing','version':None,'error':'未找到可执行文件','h264_nvenc':None}
    if not path or not Path(path).is_file():
        return result
    result['status'] = 'invalid'
    runner = subprocess.run if runner is None else runner
    options = dict(capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=4,
                   stdin=subprocess.DEVNULL, creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    try:
        response = runner([path, '--version' if tool == 'blender' else '-version'], **options)
        text = (response.stdout or '') + '\n' + (response.stderr or '')
        pattern = r'^Blender\s+(\d+\.\d+(?:\.\d+)?)\b' if tool == 'blender' else r'^ffmpeg version\s+(\S+)'
        match = re.search(pattern, text, re.MULTILINE | re.IGNORECASE)
        if response.returncode or not match:
            result['error'] = f'文件不能确认为 {"Blender" if tool=="blender" else "FFmpeg"}，请选择对应的 exe'
            return result
        result.update(status='ready',version=match.group(1),error='')
        if tool == 'ffmpeg':
            encoders = runner([path,'-hide_banner','-encoders'], **options)
            listing = (encoders.stdout or '') + '\n' + (encoders.stderr or '')
            result['h264_nvenc'] = bool(re.search(r'^\s*V\S*\s+h264_nvenc\s',listing,re.MULTILINE)) if encoders.returncode == 0 else None
            if encoders.returncode:
                result['warning'] = '编码器列表读取失败，GPU 编码能力待确认'
    except subprocess.TimeoutExpired:
        result['error'] = '工具响应超时，请确认选择了正确的可执行文件'
        if result['status'] == 'ready':
            result['warning'] = result.pop('error')
            result['error'] = ''
    except OSError as exc:
        result['error'] = f'工具无法启动：{exc}'
    return result


class DependencyManager:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.settings_path = self.root/'settings.json'
        self.lock = threading.RLock()
        self.settings, self.settings_error = {}, ''
        try:
            if self.settings_path.exists():
                self.settings = json.loads(self.settings_path.read_text(encoding='utf-8-sig'))
                if not isinstance(self.settings, dict):
                    raise ValueError('settings.json 必须为对象')
        except (OSError, ValueError) as exc:
            self.settings = {}
            self.settings_error = f'本机设置读取失败，修正并保存后生效：{exc}'
        self.tools = {}
        self.discover()

    def _detect(self, settings):
        candidates = candidate_paths(self.root, settings)
        result = {}
        def probe(item):
            tool, candidate = item
            return tool, {**probe_executable(tool,candidate['path']), 'origin':candidate['origin']}
        entries = [(tool,candidate) for tool,items in candidates.items() for candidate in items]
        checked = {tool:[] for tool in TOOL_KEYS}
        with ThreadPoolExecutor(max_workers=4) as workers:
            for tool, candidate in workers.map(probe, entries):
                checked[tool].append(candidate)
        for tool, key in TOOL_KEYS.items():
            explicit = normalize_path(settings.get(key,''), self.root)
            selected = next((c for c in checked[tool] if c['path'] == explicit), None) if explicit else next((c for c in checked[tool] if c['status']=='ready'),None)
            if selected is None:
                selected = probe_executable(tool,explicit) if explicit else {'path':'','status':'missing','version':None,'error':'尚未找到工具，请扫描或手动指定 exe 路径','h264_nvenc':None}
            result[tool] = {**selected,'download_url':DOWNLOADS[tool],'candidates':checked[tool]}
        return result

    def discover(self):
        with self.lock:
            self.tools = self._detect(self.settings)
            return self.environment()

    def environment(self):
        with self.lock:
            return {'tools':copy.deepcopy(self.tools),
                    'settings':{key:str(self.settings.get(key,'') or '') for key in TOOL_KEYS.values()},
                    'ready':bool(self.tools) and all(t['status']=='ready' for t in self.tools.values()),
                    'settings_error':self.settings_error, 'settings_path':str(self.settings_path),
                    'python':{'version':sys.version.split()[0],'executable':sys.executable,
                              'bundled':bool(getattr(sys,'frozen',False)),
                              'download_url':'https://www.python.org/downloads/windows/',
                              'help':'此分发包含 Python 运行时，无需另装 Python。' if getattr(sys,'frozen',False) else '源码启动需 Python 3.13 64 位。无法启动面板时先检查 Python，再运行 Start-Panorama.cmd；Python 库由 scripts/setup_deps.py 安装到项目 .deps。'}}

    def save_paths(self, values):
        with self.lock:
            settings = dict(self.settings)
            for key in TOOL_KEYS.values():
                value = values.get(key,settings.get(key,''))
                if not isinstance(value,str) or len(value)>4096 or '\x00' in value:
                    raise ValueError('工具路径必须为有效字符串')
                settings[key] = normalize_path(value,self.root)
            detected = self._detect(settings)
            for tool,key in TOOL_KEYS.items():
                if settings[key] and detected[tool]['status'] != 'ready':
                    raise ValueError(f'{"Blender" if tool=="blender" else "FFmpeg"} 路径验证失败：{detected[tool]["error"]}')
            temporary = self.settings_path.with_suffix('.json.tmp')
            try:
                temporary.write_text(json.dumps(settings,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
                os.replace(temporary,self.settings_path)
            finally:
                temporary.unlink(missing_ok=True)
            self.settings,self.tools,self.settings_error = settings,detected,''
            return self.environment()
