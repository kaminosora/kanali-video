"""Portable tool discovery must be bounded and keep settings project-local."""
import json
import io
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class DependencyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / '.cache')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def executable(self, relative):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'fake binary for injected version runner')
        return path

    def test_probe_recognizes_real_product_version_and_encoder_listing(self):
        from mapper.dependencies import probe_executable
        executable = self.executable('tools/ffmpeg/bin/ffmpeg.exe')
        calls = []
        def runner(command, **kwargs):
            calls.append((command, kwargs))
            return SimpleNamespace(returncode=0, stdout='ffmpeg version 7.1.1 Copyright\n' if '-version' in command else ' V....D h264_nvenc NVIDIA NVENC H.264 encoder\n V....D libx264 libx264 H.264\n', stderr='')
        result = probe_executable('ffmpeg', executable, runner=runner)
        self.assertEqual(result['status'], 'ready')
        self.assertEqual(result['version'], '7.1.1')
        self.assertTrue(result['h264_nvenc'])
        self.assertEqual(calls[1][0][-1], '-encoders')
        self.assertTrue(all(call[1]['timeout'] <= 5 for call in calls))
        self.assertTrue(all(not call[1].get('shell', False) for call in calls))

    def test_wrong_executable_or_timeout_never_counts_as_ready(self):
        from mapper.dependencies import probe_executable
        executable = self.executable('wrong.exe')
        result = probe_executable('blender', executable, runner=lambda *a, **k: SimpleNamespace(returncode=0,stdout='Not Blender utility 1.0',stderr=''))
        self.assertEqual(result['status'], 'invalid')
        def timeout(*args, **kwargs):
            raise subprocess.TimeoutExpired(args[0], 4)
        self.assertEqual(probe_executable('blender', executable, runner=timeout)['status'], 'invalid')

    def test_local_tools_are_found_without_recursive_scan(self):
        from mapper.dependencies import candidate_paths
        blender = self.executable('tools/blender-4.5/blender.exe')
        ffmpeg = self.executable('tools/ffmpeg/bin/ffmpeg.exe')
        with patch.object(Path, 'rglob', side_effect=AssertionError('No recursive scan')):
            result = candidate_paths(self.root, {}, environ={}, which=lambda _:None, registry=lambda:[])
        self.assertIn(str(blender.resolve()), [item['path'] for item in result['blender']])
        self.assertIn(str(ffmpeg.resolve()), [item['path'] for item in result['ffmpeg']])
        self.assertLessEqual(len(result['blender']), 12)

    def test_missing_tools_and_corrupt_settings_still_return_environment(self):
        from mapper.dependencies import DependencyManager
        (self.root/'settings.json').write_text('{broken',encoding='utf-8')
        with patch('mapper.dependencies.candidate_paths', return_value={'blender':[],'ffmpeg':[]}):
            manager = DependencyManager(self.root)
        state = manager.environment()
        self.assertFalse(state['ready'])
        self.assertEqual(state['tools']['blender']['status'], 'missing')
        self.assertTrue(state['settings_error'])
        self.assertTrue(state['tools']['ffmpeg']['download_url'].startswith('https://ffmpeg.org/'))
        self.assertEqual((self.root/'settings.json').read_text(), '{broken')

    def test_invalid_save_is_atomic_and_empty_paths_allow_auto_discovery(self):
        from mapper.dependencies import DependencyManager
        blender = self.executable('tools/blender/blender.exe')
        ffmpeg = self.executable('tools/ffmpeg/bin/ffmpeg.exe')
        initial = {'blender_path':'','ffmpeg_path':'','keep_this':'user-option'}
        settings_path = self.root/'settings.json'
        settings_path.write_text(json.dumps(initial),encoding='utf-8')
        candidates = {'blender':[{'path':str(blender),'origin':'project'}],'ffmpeg':[{'path':str(ffmpeg),'origin':'project'}]}
        def probe(tool, path):
            valid = Path(path).is_file()
            return {'path':str(path),'status':'ready' if valid else 'missing','version':'4.5.3' if tool=='blender' else '7.1', 'error':'' if valid else 'not found','h264_nvenc':tool=='ffmpeg'}
        with patch('mapper.dependencies.candidate_paths',return_value=candidates), patch('mapper.dependencies.probe_executable',side_effect=probe):
            manager = DependencyManager(self.root)
            with self.assertRaisesRegex(ValueError, 'Blender'):
                manager.save_paths({'blender_path':str(self.root/'missing.exe'),'ffmpeg_path':''})
            self.assertEqual(json.loads(settings_path.read_text()), initial)
            result = manager.save_paths({'blender_path':'','ffmpeg_path':''})
        self.assertTrue(result['ready'])
        self.assertEqual(json.loads(settings_path.read_text())['keep_this'], 'user-option')

    def test_active_job_rejects_settings_before_any_write(self):
        import app
        application = app.Application.__new__(app.Application)
        application.lock = threading.RLock()
        application.jobs = SimpleNamespace(lock=threading.RLock(),jobs={'one':{'status':'preparing'}})
        application.dependencies = SimpleNamespace(save_paths=lambda _: self.fail('No settings writes during an active job'))
        with self.assertRaisesRegex(ValueError, '任务'):
            application.update_settings({'blender_path':'','ffmpeg_path':''})
        with self.assertRaisesRegex(ValueError, '任务'):
            application.discover_tools()

    def test_application_can_open_when_external_tools_are_missing(self):
        import app
        missing = {'tools':{'blender':{'path':'','status':'missing'},'ffmpeg':{'path':'','status':'missing'}},'ready':False}
        dependencies = SimpleNamespace(environment=lambda:missing)
        jobs = SimpleNamespace(lock=threading.RLock(),jobs={},list=lambda:[])
        with patch.object(app,'ROOT',self.root), patch('mapper.dependencies.DependencyManager',return_value=dependencies), patch('mapper.jobs.JobManager',return_value=jobs):
            application = app.Application()
        self.assertFalse(application.environment()['ready'])
        self.assertFalse(application.environment()['busy'])
        self.assertEqual(application.state()['blender_path'], '')

    def test_http_environment_and_settings_use_existing_session_token_boundary(self):
        import app
        saves = []
        application = SimpleNamespace(token='test-token',environment=lambda:{'ready':False},
            update_settings=lambda data:saves.append(data) or {'ready':True}, discover_tools=lambda:{'ready':True})
        def request(method,path,body=None,token='test-token'):
            data = json.dumps(body or {}).encode()
            headers = f'{method} {path} HTTP/1.1\r\nHost: 127.0.0.1:18765\r\nConnection: close\r\n'
            if method == 'POST':
                headers += f'X-Session-Token: {token}\r\nContent-Length: {len(data)}\r\nContent-Type: application/json\r\n'
            handler = app.Handler.__new__(app.Handler)
            handler.rfile = io.BytesIO(headers.encode()+b'\r\n'+(data if method=='POST' else b''))
            handler.wfile = io.BytesIO()
            handler.client_address = ('127.0.0.1',12345)
            handler.server = SimpleNamespace(server_port=18765,app=application)
            handler.handle()
            return handler.wfile.getvalue()
        self.assertIn(b'200 OK',request('GET','/api/environment'))
        self.assertIn(b'403 Forbidden',request('POST','/api/settings',{'blender_path':'wrong'},token='wrong'))
        self.assertEqual(saves,[])
        self.assertIn(b'200 OK',request('POST','/api/settings',{'blender_path':'picked'}))
        self.assertEqual(saves,[{'blender_path':'picked'}])
        self.assertIn(b'200 OK',request('POST','/api/discover'))


if __name__ == '__main__':
    unittest.main()
