"""Install pinned binary dependencies into this project, never into global Python."""
from __future__ import annotations

import argparse
import importlib
import os
from pathlib import Path
import subprocess
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='Check dependencies without installing.')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    target = root / '.deps'
    sys.path.insert(0, str(target))
    if not args.check:
        cache = root / '.cache' / 'pip'
        temp = root / '.cache' / 'pip-tmp'
        for path in (target, cache, temp):
            path.mkdir(parents=True, exist_ok=True)
        environment = dict(os.environ, TEMP=str(temp), TMP=str(temp), TMPDIR=str(temp),
                           PIP_CACHE_DIR=str(cache), PIP_DISABLE_PIP_VERSION_CHECK='1')
        subprocess.run([sys.executable, '-m', 'pip', 'install', '--upgrade', '--only-binary=:all:',
                        '--target', str(target), '--cache-dir', str(cache), '-r', str(root / 'requirements.txt')],
                       env=environment, check=True, cwd=root)
    for name in ('numpy', 'PIL', 'av', 'moderngl', 'glcontext'):
        module = importlib.import_module(name)
        print(f'{name} {getattr(module, "__version__", "installed")}: {module.__file__}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
