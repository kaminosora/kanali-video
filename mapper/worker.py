"""GPU mapping worker: decoded full-resolution frames -> GL -> FFmpeg pipe."""
import json
import os
from pathlib import Path
import subprocess
import threading
import time

from mapper.storage import write_json


def render_gpu(settings_path):
    import av
    import numpy as np
    from PIL import Image
    from mapper.gpu import GpuMapper
    from mapper.encoding import encoder_command
    settings=json.loads(Path(settings_path).read_text(encoding='utf-8'))
    folder=Path(settings['directory']);options=dict(settings['options'])
    width,height=2240*options['supersample'],1080*options['supersample']
    options.update(input_format='rawvideo',width=width,height=height)
    candidate=Path(settings['candidate_path'])
    partial=candidate.with_name(candidate.stem+'.partial.mp4')
    candidate.parent.mkdir(parents=True,exist_ok=True)
    command=encoder_command(settings['ffmpeg_path'],partial,options)
    flag=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0
    movie=None
    count=0
    started=time.monotonic()
    try:
        if settings['source_kind']=='image':
            with Image.open(settings['source_path']) as image:still=np.asarray(image.convert('RGB'))
            frames=(still for _ in range(options['frames']))
        else:
            movie=av.open(settings['source_path'],options={'protocol_whitelist':'file'})
            movie.streams.video[0].thread_type='AUTO'
            from mapper.gpu import frame_rgb
            frames=(frame_rgb(frame) for frame in movie.decode(video=0))
        with GpuMapper(settings['config'],settings['source_aspect'],width,height,
                       decoration_path=settings.get('decoration_path')) as mapper:
            with (folder/'encode.log').open('wb') as log:
                encoder=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=log,stderr=log,creationflags=flag)
                stop_watcher=threading.Event()
                def watch_cancel():
                    # The main thread may be blocked writing a full raw frame to
                    # an encoder that stopped reading. Killing the reader unblocks
                    # that write; the main thread then reaps it before returning.
                    while not stop_watcher.wait(.1):
                        if encoder.poll() is not None:
                            return
                        if (folder/'cancel.request').exists():
                            try:
                                encoder.kill()
                                return
                            except OSError as exc:
                                if encoder.poll() is None:
                                    log.write(f'Encoder cancellation failed: {exc}\n'.encode('utf-8'))
                                    log.flush()
                watcher=threading.Thread(target=watch_cancel,daemon=True,name='encoder-cancel')
                watcher.start()
                try:
                    last_report=0;static_result=None
                    for count,rgb in enumerate(frames,1):
                        if count>options['frames']:break
                        if (folder/'cancel.request').exists():raise InterruptedError('已取消 GPU 渲染')
                        if static_result is None:
                            mapped=mapper.render(rgb)
                            if settings['source_kind']=='image':static_result=mapped
                        else:mapped=static_result
                        if count==1:Image.fromarray(mapped).save(folder/'first-frame.png',compress_level=1)
                        encoder.stdin.write(mapped.tobytes())
                        now=time.monotonic()
                        if count==1 or count==options['frames'] or now-last_report>=0.2:
                            write_json(folder/'progress.json',{'frame':count,'message':f"GPU 映射 {count} / {options['frames']} · {options['encoder']}"})
                            last_report=now
                    if count<options['frames']:raise RuntimeError('GPU 输入帧数不足')
                    encoder.stdin.close()
                    while encoder.poll() is None:
                        if (folder/'cancel.request').exists():raise InterruptedError('已取消视频编码')
                        time.sleep(.1)
                    if encoder.returncode:raise RuntimeError('视频编码失败，详见 encode.log')
                    if (folder/'cancel.request').exists():raise InterruptedError('已取消 GPU 渲染')
                    partial.replace(candidate)
                    write_json(folder/'gpu-result.json',{'frames':options['frames'],'seconds':time.monotonic()-started,
                               'encoder':options['encoder'],'width':width,'height':height})
                except BaseException as exc:
                    if encoder.poll() is None:encoder.terminate()
                    try:encoder.wait(timeout=5)
                    except subprocess.TimeoutExpired:encoder.kill();encoder.wait()
                    if (folder/'cancel.request').exists():
                        raise InterruptedError('已取消 GPU 渲染') from exc
                    raise
                finally:
                    stop_watcher.set()
                    watcher.join(timeout=1)
                    if not encoder.stdin.closed:
                        try:encoder.stdin.close()
                        except (BrokenPipeError, ConnectionResetError):pass
    finally:
        if movie is not None:movie.close()
        partial.unlink(missing_ok=True)
