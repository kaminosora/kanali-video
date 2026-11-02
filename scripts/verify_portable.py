"""Acceptance in a disposable relocated bundle; requires external Blender/FFmpeg.

Run: python scripts/verify_portable.py --folder <extracted-folder> --settings <local-settings>
The folder must be inside this checkout's .cache/package-acceptance directory.
This script installs the official media wheel after the caller has chosen this test,
launches without system Python in PATH, and leaves only local validation artifacts.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import time
from urllib.request import Request, build_opener, ProxyHandler

ROOT=Path(__file__).resolve().parents[1]
CLIENT=build_opener(ProxyHandler({}))


def brightness_samples(frame, width=2160, height=1080, center_u=.5, expected_center=113):
    """Sample neutral patches from a decoded top-down RGB24 delivery frame."""
    assert len(frame)==width*height*3,('Incomplete decoded RGB24 frame',len(frame))
    samples={}
    for name,x in (('left',32),('center',round(width*center_u)),('right',width-33)):
        y=height//2
        pixels=[frame[(row*width+column)*3:(row*width+column)*3+3]
                for row in range(y-3,y+4) for column in range(x-3,x+4)]
        samples[name]=[round(sum(pixel[channel] for pixel in pixels)/len(pixels),3) for channel in range(3)]
    # The exported texture must dim the curved centre, while leaving the wings
    # near 128. Accidentally exporting gain*angular_response would dim both and
    # fail the side samples and the required centre-to-side difference.
    for name,expected in (('center',expected_center),('left',128),('right',128)):
        assert all(abs(value-expected)<=3 for value in samples[name]),samples
    assert max(samples['center'])<min(samples['left']+samples['right'])-8,samples
    return samples


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--folder',required=True,type=Path)
    parser.add_argument('--settings',required=True,type=Path)
    args=parser.parse_args()
    folder=args.folder.resolve()
    if not folder.is_relative_to(ROOT/'.cache'/'package-acceptance'):
        raise ValueError('Use a disposable bundle beneath .cache/package-acceptance')
    executable=folder/'PanoramaMapper.exe'
    environment=dict(os.environ,PATH=os.pathsep.join([os.path.join(os.environ['SystemRoot'],'System32'),os.environ['SystemRoot']]))
    environment.pop('PYTHONPATH',None);environment.pop('PYTHONHOME',None)
    flags=getattr(subprocess,'CREATE_NO_WINDOW',0)
    install=subprocess.run([str(executable),'--install-media'],input='y\n',capture_output=True,
                           encoding='utf-8',errors='replace',env=environment,cwd=ROOT,timeout=240,creationflags=flags)
    (folder/'bootstrap-test.log').write_text(install.stdout+'\n'+install.stderr,encoding='utf-8')
    if install.returncode:raise RuntimeError('Media bootstrap failed; see bootstrap-test.log')
    with (folder/'acceptance-service.log').open('wb') as log:
        server=subprocess.Popen([str(executable),'--no-browser','--port','0'],stdout=log,stderr=log,
                                env=environment,cwd=ROOT,creationflags=flags)
        token='';url=''
        def api(path,data=None):
            request=Request(url+path,data=None if data is None else json.dumps(data).encode(),
                            headers={'Content-Type':'application/json','X-Session-Token':token})
            with CLIENT.open(request,timeout=90) as response:return json.load(response)
        try:
            deadline=time.monotonic()+45
            while time.monotonic()<deadline:
                if server.poll() is not None:raise RuntimeError('Portable server exited; see acceptance-service.log')
                state_path=folder/'.cache'/'server.json'
                if state_path.is_file():
                    try:
                        url=json.loads(state_path.read_text(encoding='utf-8'))['url']
                        state=api('/api/state');token=state['session_token'];break
                    except (OSError,ValueError):pass
                time.sleep(.25)
            else:raise TimeoutError('Portable panel did not become ready')
            assert Path(state['output_dir']).resolve()==folder/'outputs',state['output_dir']
            assert state['defaults']['brightness_enabled'] is False,state['defaults']
            environment_result=api('/api/environment')
            assert environment_result['python']['bundled']
            configured=api('/api/settings',json.loads(args.settings.read_text(encoding='utf-8')))
            assert configured['ready'],configured
            caps=api('/api/capabilities')
            assert caps['encoders']['libx264']['available'],caps
            source=api('/api/calibration',{})
            estimate=api('/api/estimate',dict(source_id=source['id'],start=0,end=60,fps=30,quality='high'))
            assert estimate['frames']==1800 and estimate['limit_bytes']==500_000_000
            video=api('/api/import',{'path':str(folder/'examples'/'motion-demo.mp4')})
            gray=api('/api/brightness-calibration',{})
            assert state['defaults']['bend_center_u']==.5 and state['defaults']['brightness_shoulder']==1
            line_source=api('/api/axis-calibration',{})
            assert line_source['width']==2160 and line_source['height']==1080
            preview=api('/api/mesh',dict(config=state['defaults'],source_aspect=2,
                        axis_preview={'actual_bend_center_u':.54}))
            assert preview['config']['bend_center_u']==.5
            assert abs(preview['axis_preview']['delta_pixels']-86.4)<1e-8
            assert len(preview['mesh']['uv'])==len(preview['axis_preview']['projected'])
            assert 'uv' not in preview['axis_preview'] and 'brightness_gain' not in preview['axis_preview']
            results=[]
            cases=[(source,'auto','auto',False),(source,'blender','libx264',False),(video,'auto','auto',False)]
            if caps['encoders'].get('h264_amf',{}).get('available'):
                cases.append((video,'auto','h264_amf',False))
            cases.extend(((gray,'gpu','auto',True),(gray,'blender','libx264',True)))
            cases.extend(((gray,'gpu','auto','axis'),(gray,'blender','libx264','axis')))
            for media,renderer,encoder,brightness_case in cases:
                brightness_enabled=bool(brightness_case)
                config=dict(state['defaults'],brightness_enabled=brightness_enabled)
                if brightness_case=='axis':
                    config.update(bend_center_u=.54,brightness_shoulder=2)
                job=api('/api/render',dict(source_id=media['id'],config=config,
                    start=0,end=.2,fps=30,quality='high',supersample=1,
                    name='portable-'+renderer+('-brightness' if brightness_enabled else ''),
                    renderer=renderer,encoder=encoder))
                deadline=time.monotonic()+180
                while time.monotonic()<deadline:
                    current=next(item for item in api('/api/jobs')['jobs'] if item['id']==job['id'])
                    if current['status'] not in {'queued','preparing','rendering','encoding'}:break
                    time.sleep(.4)
                assert current['status']=='completed',current
                assert current['total_frames']==6,current
                if brightness_enabled:
                    assert current['backend']['renderer']==renderer,current['backend']
                output=Path(current['output_path']).resolve()
                assert output.is_relative_to(folder/'outputs')
                assert 0<output.stat().st_size<500_000_000
                assert Path(current['blend_path']).is_file()
                decoded=subprocess.run([configured['tools']['ffmpeg']['path'],'-hide_banner','-nostdin',
                    '-i',str(output),'-frames:v','1','-vf','showinfo','-f','null','-'],capture_output=True,
                    encoding='utf-8',errors='replace',timeout=30,creationflags=flags)
                assert decoded.returncode==0,decoded.stderr[-1500:]
                assert 'sar:1/1 s:2160x1080' in decoded.stderr,decoded.stderr[-2500:]
                assert 'DAR 2:1' in decoded.stderr,decoded.stderr[-2500:]
                gray_samples=None
                if brightness_enabled:
                    first_frame=subprocess.run([configured['tools']['ffmpeg']['path'],'-hide_banner','-nostdin',
                        '-i',str(output),'-frames:v','1','-pix_fmt','rgb24','-f','rawvideo','pipe:1'],
                        capture_output=True,timeout=30,creationflags=flags)
                    assert first_frame.returncode==0,first_frame.stderr.decode('utf-8',errors='replace')[-1500:]
                    gray_samples=brightness_samples(first_frame.stdout,center_u=config['bend_center_u'],
                                                    expected_center=110 if brightness_case=='axis' else 113)
                results.append(dict(source_kind=media['kind'],renderer=current['backend']['renderer'],encoder=current['backend']['encoder'],
                                    size_bytes=output.stat().st_size,frames=current['total_frames'],
                                    width=2160,height=1080,sar='1:1',dar='2:1',
                                    brightness_enabled=brightness_enabled,bend_center_u=config['bend_center_u'],
                                    brightness_shoulder=config['brightness_shoulder'],gray_samples=gray_samples))
            report={'version':state['version'],'bundled_python':True,'system_python_path_removed':True,
                    'relocated_unicode_space_directory':True,'media_bootstrap':True,'estimate':estimate,'exports':results}
            (folder/'acceptance.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps(report,ensure_ascii=False),flush=True)
        finally:
            if url and token:
                try:api('/api/shutdown',{})
                except OSError:pass
            try:server.wait(timeout=15)
            except subprocess.TimeoutExpired:server.terminate();server.wait(timeout=5)


if __name__=='__main__':main()
