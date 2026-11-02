"""Shared entry for a source checkout and the portable Windows executable."""
import json
import runpy
import sys
from mapper.paths import app_root

ROOT=app_root()
sys.path.insert(0,str(ROOT/'.deps'))


def main():
    if hasattr(sys.stdout,'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    if len(sys.argv)>1 and sys.argv[1]=='--probe-capabilities':
        from mapper.capabilities import probe_local
        print(json.dumps(probe_local(sys.argv[2]),ensure_ascii=False))
    elif len(sys.argv)>1 and sys.argv[1]=='--install-media':
        from scripts.install_media import main as install_media
        sys.exit(install_media(ROOT))
    elif len(sys.argv)>1 and sys.argv[1]=='--gpu-worker':
        from mapper.worker import render_gpu
        render_gpu(sys.argv[2])
    elif len(sys.argv)>1 and sys.argv[1]=='--pick-file':
        runpy.run_path(str(ROOT/'scripts'/'pick_file.py'),run_name='__main__')
    elif len(sys.argv)>1 and sys.argv[1]=='--stop-server':
        runpy.run_path(str(ROOT/'scripts'/'stop_server.py'),run_name='__main__')
    else:
        if getattr(sys, 'frozen', False):
            from scripts.install_media import main as install_media
            result = install_media(ROOT)
            if result:
                sys.exit(result)
        from app import main as serve
        serve()


if __name__=='__main__':
    try:
        main()
    except Exception:
        import logging
        logging.getLogger('panorama.server').exception('Application or worker failed')
        raise
