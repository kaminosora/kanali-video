"""Runtime paths for source checkouts and movable standalone distributions."""
from pathlib import Path
import sys


def app_root():
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def worker_command(mode, *arguments):
    if getattr(sys, 'frozen', False):
        return [sys.executable, '--' + mode, *map(str, arguments)]
    return [sys.executable, str(app_root() / 'entrypoint.py'), '--' + mode, *map(str, arguments)]
