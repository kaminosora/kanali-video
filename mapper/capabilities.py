"""Real, bounded GPU/encoder capability probes with portable software fallback."""
import json
import os
from pathlib import Path
import subprocess
import tempfile

from mapper.paths import app_root, worker_command

ENCODERS = ('h264_nvenc','h264_qsv','h264_amf','libx264')
NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0


def select_backend(capabilities, renderer='auto', encoder='auto', quality='high'):
    if quality == 'master':
        return {'renderer':'blender','encoder':'ffv1'}
    if renderer not in {'auto','gpu','blender'} or encoder not in {'auto',*ENCODERS}:
        raise ValueError('渲染或编码模式无效')
    gpu = capabilities.get('gpu',{}).get('available',False)
    if renderer == 'gpu' and not gpu:
        raise ValueError('未检测到可用的硬件 OpenGL 上下文；请选择自动或 Blender 模式')
    selected_renderer = ('gpu' if gpu else 'blender') if renderer == 'auto' else renderer
    available = capabilities.get('encoders',{})
    if encoder == 'auto':
        encoder = next((name for name in ENCODERS if available.get(name,{}).get('available')),None)
    if not encoder or not available.get(encoder,{}).get('available'):
        raise ValueError('所选编码器未通过实际编码测试；请选择自动或检查 FFmpeg/显卡驱动')
    return {'renderer':selected_renderer,'encoder':encoder}


def probe_local(ffmpeg_path):
    """Executed in a worker, so GL contexts and vendor DLLs cannot crash the UI."""
    try:
        from mapper.gpu import probe_gpu
        gpu=probe_gpu()
    except Exception as exc:
        gpu={'available':False,'error':str(exc),'renderer':'','vendor':'','version':''}
    result={'gpu':gpu,'encoders':{}}
    for encoder in ENCODERS:
        result['encoders'][encoder] = _probe_encoder(ffmpeg_path, encoder)
    return result


def _probe_encoder(ffmpeg_path, encoder):
    """Bounded FFmpeg subprocess only: safe to call without importing any GL code."""
    from mapper.encoding import encoder_args
    try:
        cache=app_root()/'.cache'/'capabilities'
        cache.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='probe-',dir=cache) as directory:
            output=Path(directory)/'probe.mp4'
            command=[str(ffmpeg_path),'-hide_banner','-loglevel','error','-nostdin','-y',
                     '-f','lavfi','-i','color=c=black:s=2240x1080:r=60','-frames:v','2','-an',
                     *encoder_args(encoder),str(output)]
            process=subprocess.run(command,capture_output=True,timeout=12,creationflags=NO_WINDOW)
            ok=process.returncode==0 and output.is_file() and output.stat().st_size>0
            error = '' if ok else process.stderr.decode('utf-8',errors='replace')[-900:]
            if not ok and not error:
                error = f'FFmpeg 未生成有效探测样片（退出码 {process.returncode}）'
            return {'available':ok,'error':error}
    except (OSError,ValueError,subprocess.TimeoutExpired) as exc:
        return {'available':False,'error':str(exc)}


def detect_capabilities(ffmpeg_path):
    try:
        result=subprocess.run(worker_command('probe-capabilities',str(ffmpeg_path)),capture_output=True,
                              timeout=65,creationflags=NO_WINDOW,cwd=app_root())
        if result.returncode:
            raise RuntimeError(result.stderr.decode('utf-8',errors='replace')[-1200:])
        capabilities = json.loads(result.stdout.decode('utf-8'))
        if (not isinstance(capabilities, dict) or not isinstance(capabilities.get('gpu'), dict)
                or not isinstance(capabilities.get('encoders'), dict)):
            raise ValueError('能力探测进程返回了无效结果')
        return capabilities
    except (OSError,ValueError,RuntimeError,subprocess.TimeoutExpired) as exc:
        # A native driver crash can kill the worker before any encoder tests.
        # Never retry/import its GL code here; independently test only CPU x264.
        error = str(exc) or type(exc).__name__
        encoders = {name:{'available':False,'error':'GPU 探测进程失败，硬件编码能力未确认'}
                    for name in ENCODERS if name != 'libx264'}
        encoders['libx264'] = _probe_encoder(ffmpeg_path, 'libx264')
        return {'gpu':{'available':False,'error':error,'renderer':'','vendor':'','version':''},
                'encoders':encoders,'error':error,'fallback':'blender/libx264'}
