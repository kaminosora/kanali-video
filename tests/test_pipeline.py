"""Job commit, supervisor cleanup and raw-pipe lifecycle regressions."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / '.deps'))
import av
import numpy as np
from PIL import Image
from mapper.jobs import JobManager
from mapper.worker import render_gpu
from tests.tool_paths import find_tool

FFMPEG = find_tool('ffmpeg')


class PipelineTests(unittest.TestCase):
    def setUp(self):
        cache = ROOT / '.cache' / 'tests-pipeline'
        cache.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=cache)
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)

    def manager_and_job(self):
        manager = JobManager.__new__(JobManager)
        manager.lock, manager.processes, manager.threads = threading.RLock(), {}, {}
        manager.ffmpeg_path, manager.blender_path = 'fake-ffmpeg', 'fake-blender'
        manager.capabilities = lambda: {'gpu': {'available': False}, 'encoders': {'libx264': {'available': True}}}
        source = self.folder / 'original.png'
        source.write_bytes(b'untouched source')
        job = dict(id='pipeline', directory=str(self.folder), log_path=str(self.folder/'render.log'),
            status='queued', source={'path': str(source), 'kind': 'image', 'aspect': 1},
            config={}, warnings=[], total_frames=2, created=time.time(),
            options=dict(start=0, end=1, frames=2, fps_num=2, fps_den=1, quality='high',
                         supersample=1, name='delivery', renderer='blender', encoder='libx264'))
        manager.jobs = {job['id']: job}
        def render(job, command, log):
            settings = json.loads((self.folder/'render-settings.json').read_text(encoding='utf-8'))
            Path(settings['candidate_path']).write_bytes(b'candidate video')
            return 0
        manager._process = render
        return manager, job

    def test_cancel_after_delivery_return_cannot_be_overwritten_by_completed(self):
        manager, job = self.manager_and_job()
        def finalize(candidate, final, options, **kwargs):
            Path(final).write_bytes(b'finished video')
            manager.cancel(job['id'])
            return {'output_path': str(final), 'size_bytes': 14}
        with patch('mapper.delivery.finalize_mp4', side_effect=finalize):
            manager._run(job)
        self.assertEqual(job['status'], 'cancelled')
        self.assertIsNone(job.get('output_path'))
        self.assertFalse((self.folder/'delivery.mp4').exists())
        self.assertTrue((self.folder/'work/render-candidate.mp4').is_file())

    def test_equal_or_larger_decimal_limit_never_reaches_completed(self):
        for size in (500_000_000, 500_000_001):
            with self.subTest(size=size):
                manager, job = self.manager_and_job()
                final = self.folder/'delivery.mp4'
                def finalize(candidate, final, options, **kwargs):
                    Path(final).write_bytes(b'oversized result represented by patched stat')
                    return {'output_path': str(final), 'size_bytes': size}
                actual_stat = Path.stat
                def reported_stat(path, *args, **kwargs):
                    result = actual_stat(path, *args, **kwargs)
                    if path == final:
                        fields = list(result)
                        fields[6] = size
                        return os.stat_result(fields)
                    return result
                with patch('mapper.delivery.finalize_mp4', side_effect=finalize), patch.object(Path, 'stat', reported_stat):
                    manager._run(job)
                self.assertEqual(job['status'], 'failed')
                self.assertIsNone(job.get('output_path'))
                self.assertFalse(final.exists())

    def test_supervisor_exception_reaps_child_and_removes_registration(self):
        manager = JobManager.__new__(JobManager)
        manager.lock, manager.processes = threading.RLock(), {}
        job = {'id': 'supervisor', 'status': 'encoding', 'directory': str(self.folder)}
        children = []
        original_popen = subprocess.Popen
        def spawn(*args, **kwargs):
            child = original_popen(*args, **kwargs)
            children.append(child)
            if len(children) > 1:
                return child
            real_wait = child.wait
            first = [True]
            def wait(*args, **kwargs):
                if first.pop() if first else False:
                    raise RuntimeError('supervision interrupted')
                return real_wait(*args, **kwargs)
            child.wait = wait
            return child
        try:
            with patch('mapper.jobs.subprocess.Popen', side_effect=spawn), (self.folder/'supervision.log').open('wb') as log:
                with self.assertRaisesRegex(RuntimeError, 'supervision interrupted'):
                    manager._process(job, [sys.executable, '-c', 'import time; time.sleep(30)'], log)
            self.assertIsNotNone(children[0].poll(), 'The failed supervisor left a live child')
            self.assertNotIn(job['id'], manager.processes)
        finally:
            for child in children:
                if child.poll() is None:
                    child.kill()
                child.wait(timeout=3)

    def test_cancel_stops_unresponsive_render_process_after_grace_period(self):
        manager = JobManager.__new__(JobManager)
        manager.lock, manager.processes = threading.RLock(), {}
        job = {'id': 'blocked-render', 'status': 'rendering', 'directory': str(self.folder)}
        errors = []
        def execute():
            try:
                with (self.folder/'blocked-render.log').open('wb') as log:
                    manager._process(job, [sys.executable, '-c', 'import time; time.sleep(30)'], log)
            except BaseException as exc:
                errors.append(exc)
        with patch('mapper.jobs.RENDER_CANCEL_GRACE_SECONDS', .1, create=True):
            thread = threading.Thread(target=execute, daemon=True)
            thread.start()
            deadline = time.monotonic()+3
            while not manager.processes and time.monotonic()<deadline:
                time.sleep(.01)
            self.assertTrue(manager.processes)
            child = manager.processes[job['id']]
            (self.folder/'cancel.request').touch()
            thread.join(timeout=2)
            stopped_on_cancel = not thread.is_alive()
            if child.poll() is None:
                child.kill()
            thread.join(timeout=3)
        self.assertTrue(stopped_on_cancel, 'Blocked renderer ignored cancellation indefinitely')
        self.assertEqual(errors, [])
        self.assertIsNotNone(child.poll())
        self.assertNotIn(job['id'], manager.processes)

    def settings(self):
        source = self.folder/'still.png'
        Image.new('RGB', (16, 16), (120, 80, 40)).save(source)
        settings = {'directory': str(self.folder), 'config': {}, 'source_kind': 'image',
                    'source_path': str(source), 'source_aspect': 1,
                    'candidate_path': str(self.folder/'work/render-candidate.mp4'),
                    'ffmpeg_path': FFMPEG or 'unused-ffmpeg', 'options': {'supersample': 1, 'frames': 3,
                    'fps_num': 30000, 'fps_den': 1001, 'quality': 'high', 'encoder': 'libx264'}}
        path = self.folder/'settings.json'
        path.write_text(json.dumps(settings), encoding='utf-8')
        return path, settings

    @unittest.skipUnless(FFMPEG, 'FFmpeg unavailable: real raw-pipe encoding test')
    def test_raw_worker_closes_pipe_and_finishes_exact_frame_count(self):
        path, settings = self.settings()
        with patch('mapper.gpu.GpuMapper', SolidMapper):
            render_gpu(path)
        with av.open(settings['candidate_path']) as container:
            stream = container.streams.video[0]
            self.assertEqual(len(list(container.decode(stream))), 3)
            self.assertEqual(str(stream.average_rate), '30000/1001')
        self.assertFalse(list(self.folder.rglob('*.partial.mp4')))

    def test_cancel_interrupts_a_blocked_rawvideo_write_without_orphan_encoder(self):
        path, settings = self.settings()
        children, errors = [], []
        original_popen = subprocess.Popen
        def spawn(*args, **kwargs):
            child = original_popen(*args, **kwargs)
            children.append(child)
            return child
        def execute():
            try:
                render_gpu(path)
            except BaseException as exc:
                errors.append(exc)
        with patch('mapper.gpu.GpuMapper', SolidMapper), \
             patch('mapper.encoding.encoder_command', return_value=[sys.executable, '-c', 'import time; time.sleep(30)']), \
             patch('mapper.worker.subprocess.Popen', side_effect=spawn):
            thread = threading.Thread(target=execute, daemon=True)
            thread.start()
            deadline = time.monotonic()+3
            while not children and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertTrue(children)
            time.sleep(.15)  # the >7 MB frame cannot fit in an unread pipe
            (self.folder/'cancel.request').touch()
            thread.join(timeout=2)
            stopped_on_cancel = not thread.is_alive()
            for child in children:
                if child.poll() is None:
                    child.kill()
            thread.join(timeout=3)
        self.assertTrue(stopped_on_cancel, 'Cancellation remained blocked in stdin.write')
        self.assertTrue(errors)
        self.assertIsInstance(errors[0], InterruptedError)
        self.assertTrue(all(child.poll() is not None for child in children))
        self.assertFalse(Path(settings['candidate_path']).exists())


class SolidMapper:
    """Replace only hardware GL work; keep actual worker and FFmpeg pipe code."""
    def __init__(self, config, aspect, width, height, **kwargs):
        self.width, self.height = width, height

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def render(self, rgb):
        output = np.empty((self.height, self.width, 3), dtype=np.uint8)
        output[:] = (120, 80, 40)
        return output


if __name__ == '__main__':
    unittest.main()
