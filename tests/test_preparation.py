"""Regression coverage for invisible preparation and oversized intermediates."""
import json
from pathlib import Path
import tempfile
import time
import threading
import unittest
from unittest.mock import patch
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class PreparationTests(unittest.TestCase):
    def setUp(self):
        cache = ROOT / '.cache' / 'tests-preparation-contract'
        cache.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=cache)
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)

    def test_preparing_reports_real_completed_frames_before_blender_starts(self):
        from mapper.jobs import JobManager
        (self.folder / 'prepare-progress.txt').write_text(
            'frame=8549\nout_time_us=142483333\ntotal_size=51211141120\nprogress=continue\n', encoding='utf-8')
        job = dict(id='progress-test', status='preparing', phase='normalize',
                   directory=str(self.folder), progress=0, frame=0, total_frames=12426,
                   created=time.time()-277, phase_started=time.time()-277,
                   message='准备素材', options={'fps_num':60,'fps_den':1})
        info = JobManager.__new__(JobManager).public(job)
        self.assertGreater(info.get('phase_progress', 0), 0.68,
                           'FFmpeg completed 68% of preparation but the UI still reports zero')
        self.assertLess(info['phase_progress'], 0.70)
        self.assertIn('8549', info['message'])
        self.assertIn('12426', info['message'])
        self.assertGreater(info.get('elapsed_seconds', 0), 270)
        self.assertEqual(info.get('log_url'), '/output/progress-test/render.log')
        self.assertEqual(job['frame'], 0, 'Source preparation must not count as Blender rendered frames')

    def test_progress_reader_ignores_partial_last_record(self):
        import mapper.jobs as jobs
        self.assertTrue(hasattr(jobs, 'read_ffmpeg_progress'), 'No FFmpeg progress parser')
        p=self.folder/'prepare-progress.txt'
        p.write_text('frame=15\nprogress=continue\nframe=30\ntotal_size=',encoding='utf-8')
        self.assertEqual(jobs.read_ffmpeg_progress(p)['frame'], '15')

    def test_terminal_elapsed_does_not_keep_growing(self):
        from mapper.jobs import JobManager
        job=dict(id='old',status='cancelled',directory=str(self.folder),created=100,updated_at=110)
        with patch('mapper.jobs.time.time',return_value=99999):
            self.assertEqual(JobManager.__new__(JobManager).public(job)['elapsed_seconds'],10)

    def test_native_8bit_yuv_does_not_expand_to_16bit_rgb(self):
        import mapper.media as media
        self.assertTrue(hasattr(media, 'intermediate_pixel_format'), 'No source-aware lossless format selection')
        source=dict(pixel_format='yuv420p', sar_num=1, sar_den=1, rotation=0)
        self.assertEqual(media.intermediate_pixel_format(source), 'yuv420p')
        self.assertEqual(media.intermediate_pixel_format(dict(source, pixel_format='yuv444p')), 'yuv444p')
        # Geometric/color conversions continue using the high precision fallback.
        self.assertEqual(media.intermediate_pixel_format(dict(source, sar_num=4,sar_den=3)), 'gbrp16le')
        self.assertEqual(media.intermediate_pixel_format(dict(source,rotation=90)), 'gbrp16le')

    def manager_and_job(self):
        from mapper.jobs import JobManager
        manager=JobManager.__new__(JobManager)
        manager.lock=threading.RLock()
        manager.ffmpeg_path='unused-ffmpeg'
        manager.blender_path='unused-blender'
        manager.capabilities=lambda: {'gpu': {'available': False},
                                      'encoders': {'libx264': {'available': True}}}
        original=self.folder/'original.mp4'
        original.write_bytes(b'original must stay intact')
        source=dict(kind='video',path=str(original),width=96,height=64,aspect=1.5,duration=1,
                    pixel_format='yuv420p')
        job=dict(id='failed-test',directory=str(self.folder),log_path=str(self.folder/'render.log'),
                 status='queued',source=source,config={},warnings=[],options=dict(start=0,end=1,
                 fps_num=60,fps_den=1,frames=60,quality='high',supersample=1,name='test'))
        return manager,job,original

    def test_log_disk_error_does_not_leave_failed_job_active(self):
        manager,job,original=self.manager_and_job()
        def fail_process(job,command,log):
            Path(command[-1]).write_bytes(b'partial intermediate')
            return 1
        manager._process=fail_process
        real_open=Path.open
        def failing_diagnostic(path, mode='r', *args, **kwargs):
            if path.name=='render.log' and mode=='a':
                raise OSError(28,'No space left on device')
            return real_open(path,mode,*args,**kwargs)
        with patch.object(Path,'open',failing_diagnostic):
            manager._run(job)
        self.assertEqual(job['status'],'failed')
        self.assertFalse((self.folder/'work/source.mkv').exists())
        self.assertEqual(original.read_bytes(),b'original must stay intact')
        self.assertEqual(json.loads((self.folder/'job.json').read_text(encoding='utf-8'))['status'],'failed')

    def test_cancel_cleans_only_unverified_generated_intermediate(self):
        manager,job,original=self.manager_and_job()
        def cancel_process(job,command,log):
            Path(command[-1]).write_bytes(b'partial intermediate')
            (self.folder/'cancel.request').touch()
            return -1
        manager._process=cancel_process
        manager._run(job)
        self.assertEqual(job['status'],'cancelled')
        self.assertFalse((self.folder/'work/source.mkv').exists())
        self.assertTrue(original.exists())


if __name__ == '__main__':
    unittest.main()
