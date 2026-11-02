"""Resolve test tools from explicit environment, PATH, or project configuration."""
import json
import os
from pathlib import Path
import shutil


def find_tool(name, *, root=None, environ=None, which=None):
    root = Path(root) if root is not None else Path(__file__).resolve().parents[1]
    environ = os.environ if environ is None else environ
    which = shutil.which if which is None else which
    settings = {}
    try:
        value = json.loads((root / 'settings.json').read_text(encoding='utf-8-sig'))
        if isinstance(value, dict):
            settings = value
    except (OSError, ValueError):
        pass
    for candidate in (environ.get(name.upper() + '_PATH'), which(name), settings.get(name + '_path')):
        if not isinstance(candidate, str) or not candidate.strip():
            continue
        candidate = candidate.strip().strip('"')
        resolved_command = which(candidate) if '/' not in candidate and '\\' not in candidate else None
        path = Path(resolved_command or candidate).expanduser()
        if not path.is_absolute():
            path = root / path
        if path.is_file():
            return str(path.resolve())
    return None


def ffmpeg_path():
    """Compatibility helper for suites requiring FFmpeg at import time."""
    found = find_tool('ffmpeg')
    if found is None:
        raise RuntimeError('Set FFMPEG_PATH, add ffmpeg to PATH, or configure project settings.json')
    return found
