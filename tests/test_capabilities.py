import sys
from pathlib import Path
import unittest
import subprocess
import tempfile
from unittest import mock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))


class CapabilitySelectionTests(unittest.TestCase):
    def select(self,*args,**kwargs):
        import mapper.capabilities as c
        return c.select_backend(*args,**kwargs)

    def test_advertised_hardware_encoder_is_not_selected_if_real_probe_failed(self):
        caps={'gpu':{'available':True},'encoders':{'h264_nvenc':{'available':False,'advertised':True},
              'libx264':{'available':True}}}
        result=self.select(caps)
        self.assertEqual(result['renderer'],'gpu')
        self.assertEqual(result['encoder'],'libx264')

    def test_no_standalone_gpu_uses_blender_and_cpu_encoding(self):
        caps={'gpu':{'available':False},'encoders':{'libx264':{'available':True}}}
        self.assertEqual(self.select(caps)['renderer'],'blender')
        with self.assertRaises(ValueError):self.select(caps,renderer='gpu')
        with self.assertRaises(ValueError):self.select(caps,encoder='h264_amf')

    def test_master_keeps_sixteen_bit_blender_path(self):
        caps={'gpu':{'available':True},'encoders':{'h264_nvenc':{'available':True}}}
        self.assertEqual(self.select(caps,quality='master'),{'renderer':'blender','encoder':'ffv1'})

    def test_non_nvidia_adapters_are_not_forced_to_nvenc(self):
        for encoder in ['h264_qsv','h264_amf']:
            caps={'gpu':{'available':True},'encoders':{encoder:{'available':True},'libx264':{'available':True}}}
            self.assertEqual(self.select(caps)['encoder'],encoder)


class CapabilityFallbackTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parents[1]
        cache = self.root / '.cache' / 'tests-capabilities'
        cache.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=cache)
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)

    def check_fallback(self, failure, encoder_succeeds=True):
        import mapper.capabilities as capabilities
        calls = []
        def run(command, **kwargs):
            calls.append(command)
            if command[0] == 'gpu-worker':
                if isinstance(failure, Exception):
                    raise failure
                return subprocess.CompletedProcess(command, failure, b'', b'graphics driver crashed')
            self.assertEqual(command[0], 'portable-ffmpeg')
            self.assertIn('libx264', command)
            self.assertFalse(any(item in command for item in ('h264_nvenc', 'h264_amf', 'h264_qsv')))
            if encoder_succeeds:
                Path(command[-1]).write_bytes(b'encoded fixture')
            return subprocess.CompletedProcess(command, 0 if encoder_succeeds else 1, b'', b'CPU encoder unavailable')
        with mock.patch.object(capabilities, 'app_root', return_value=self.directory), \
             mock.patch.object(capabilities, 'worker_command', return_value=['gpu-worker']), \
             mock.patch.object(capabilities.subprocess, 'run', side_effect=run), \
             mock.patch.dict(sys.modules, {'mapper.gpu': None, 'moderngl': None, 'glcontext': None}):
            result = capabilities.detect_capabilities('portable-ffmpeg')
        self.assertEqual(len(calls), 2, 'A failed GPU worker must be followed by one independent CPU encoder probe')
        self.assertFalse(result['gpu']['available'])
        self.assertEqual(result['encoders']['libx264']['available'], encoder_succeeds)
        self.assertTrue(result['gpu']['error'])
        return result

    def test_native_graphics_crash_still_allows_blender_x264(self):
        import mapper.capabilities as capabilities
        result = self.check_fallback(0xC0000005)
        self.assertEqual(capabilities.select_backend(result), {'renderer': 'blender', 'encoder': 'libx264'})

    def test_gpu_worker_timeout_uses_same_isolated_cpu_fallback(self):
        self.check_fallback(subprocess.TimeoutExpired(['gpu-worker'], 65))

    def test_failed_cpu_probe_is_not_reported_available(self):
        import mapper.capabilities as capabilities
        result = self.check_fallback(1, encoder_succeeds=False)
        with self.assertRaises(ValueError):
            capabilities.select_backend(result)

    def test_real_cpu_probe_after_worker_crash(self):
        import mapper.capabilities as capabilities
        from tests.tool_paths import find_tool
        executable = find_tool('ffmpeg')
        if executable is None:
            self.skipTest('FFmpeg unavailable for bounded CPU fallback encoding')
        real_run = subprocess.run
        def run(command, **kwargs):
            if command[0] == 'gpu-worker':
                return subprocess.CompletedProcess(command, 1, b'', b'GPU worker failed')
            return real_run(command, **kwargs)
        with mock.patch.object(capabilities, 'app_root', return_value=self.directory), \
             mock.patch.object(capabilities, 'worker_command', return_value=['gpu-worker']), \
             mock.patch.object(capabilities.subprocess, 'run', side_effect=run):
            result = capabilities.detect_capabilities(executable)
        self.assertTrue(result['encoders']['libx264']['available'], result)
