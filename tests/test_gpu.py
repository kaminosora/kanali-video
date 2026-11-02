"""GPU contract tests and small synthetic pixel tests; no vendor-specific API."""
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / '.deps'), str(ROOT)]
import numpy as np
from PIL import Image
import av
from mapper import gpu


class FrameRgbTests(unittest.TestCase):
    @staticmethod
    def yuv(y, u=128, v=128, matrix=1, color_range=1):
        frame = av.VideoFrame(16, 16, 'yuv420p')
        for plane, value in zip(frame.planes, (y, u, v)):
            plane.update(bytes((value,)) * plane.buffer_size)
        frame.colorspace = matrix
        frame.color_range = color_range
        return frame

    def test_limited_and_full_yuv_ranges(self):
        np.testing.assert_allclose(gpu.frame_rgb(self.yuv(16))[8, 8], (0, 0, 0), atol=1)
        np.testing.assert_allclose(gpu.frame_rgb(self.yuv(235))[8, 8], (255, 255, 255), atol=1)
        np.testing.assert_allclose(gpu.frame_rgb(self.yuv(100, color_range=2))[8, 8], (100, 100, 100), atol=1)

    def test_matrix_709_and_601_stay_distinct(self):
        hd = gpu.frame_rgb(self.yuv(100, 90, 200, matrix=1))[8, 8]
        sd = gpu.frame_rgb(self.yuv(100, 90, 200, matrix=5))[8, 8]
        sd_170m = gpu.frame_rgb(self.yuv(100, 90, 200, matrix=6))[8, 8]
        # Analytic limited-range YCbCr conversion; swscale's integer path rounds.
        np.testing.assert_allclose(hd, (227, 68, 18), atol=2)
        np.testing.assert_allclose(sd, (213, 54, 21), atol=2)
        np.testing.assert_array_equal(sd, sd_170m)

    def test_rgb16_fallback_is_not_treated_as_limited_yuv(self):
        original = np.full((16, 16, 3), (23, 127, 231), dtype=np.uint8)
        packed = av.VideoFrame.from_ndarray(original, format='rgb24')
        packed.color_range = 2
        frame = packed.reformat(format='gbrp16le', src_color_range='JPEG', dst_color_range='JPEG')
        frame.colorspace = 0
        frame.color_range = 2
        np.testing.assert_allclose(gpu.frame_rgb(frame), original, atol=1)

    def test_unsupported_matrix_is_not_silently_bt709(self):
        with self.assertRaises(ValueError):
            gpu.frame_rgb(self.yuv(100, matrix=9))

    def test_gray_limited_range_is_expanded_without_altering_full_range(self):
        for dtype, pixel_format, values in [(np.uint8, 'gray', (16, 235)),
                                            (np.uint16, 'gray16le', (4096, 60160))]:
            frame = av.VideoFrame.from_ndarray(np.array([values], dtype=dtype), format=pixel_format)
            frame.color_range = 1
            np.testing.assert_array_equal(gpu.frame_rgb(frame)[0], ((0, 0, 0), (255, 255, 255)))
        frame = av.VideoFrame.from_ndarray(np.array([[16, 235]], dtype=np.uint8), format='gray')
        frame.color_range = 2
        np.testing.assert_array_equal(gpu.frame_rgb(frame)[0], ((16, 16, 16), (235, 235, 235)))


class GpuContractTests(unittest.TestCase):
    @staticmethod
    def context(vendor='NVIDIA Corporation', renderer='NVIDIA test adapter'):
        context = mock.Mock()
        context.info = {'GL_VENDOR': vendor, 'GL_RENDERER': renderer, 'GL_VERSION': '4.6 test'}
        context.framebuffer.return_value.read.return_value = bytes((255, 0, 0))
        return context

    def test_probe_is_vendor_neutral_after_real_readback_contract(self):
        for vendor, renderer in [('NVIDIA Corporation', 'GeForce test'),
                                 ('ATI Technologies Inc.', 'AMD Radeon test'),
                                 ('Intel', 'Intel Iris Xe test')]:
            context = self.context(vendor, renderer)
            with self.subTest(vendor=vendor), mock.patch.object(gpu, '_create_context', return_value=context):
                result = gpu.probe_gpu()
                self.assertTrue(result['available'])
                self.assertEqual(result['vendor'], vendor)
                context.framebuffer.return_value.read.assert_called_once()
                context.release.assert_called_once()

    def test_software_opengl_is_not_advertised_as_hardware_acceleration(self):
        for renderer in ['llvmpipe (LLVM)', 'softpipe', 'GDI Generic', 'Google SwiftShader',
                         'SWR (LLVM)', 'Microsoft Basic Render Driver']:
            with self.subTest(renderer=renderer), mock.patch.object(gpu, '_create_context', return_value=self.context(renderer=renderer)):
                result = gpu.probe_gpu()
                self.assertFalse(result['available'])
                self.assertIn(renderer, result['error'])

    def test_probe_release_error_is_reported_without_escaping(self):
        context = self.context()
        context.release.side_effect = RuntimeError('context lost during close')
        with mock.patch.object(gpu, '_create_context', return_value=context):
            result = gpu.probe_gpu()
        self.assertFalse(result['available'])
        self.assertIn('context lost', result['error'])

    def test_probe_reports_context_failure_without_crashing(self):
        with mock.patch.object(gpu, '_create_context', side_effect=RuntimeError('no graphics driver')):
            result = gpu.probe_gpu()
        self.assertFalse(result['available'])
        self.assertIn('no graphics driver', result['error'])
        self.assertEqual(set(result), {'available', 'renderer', 'vendor', 'version', 'error'})

    def test_invalid_dimensions_fail_before_opening_gpu(self):
        for width, height in [(0, 10), (10, -1), (True, 10), (1.5, 10)]:
            with self.subTest(size=(width, height)), mock.patch.object(gpu, '_create_context') as create:
                with self.assertRaises(ValueError):
                    gpu.GpuMapper({}, 16 / 9, width, height)
                create.assert_not_called()

    def test_missing_gpu_has_actionable_error(self):
        with mock.patch.object(gpu, '_create_context', side_effect=RuntimeError('missing driver')):
            with self.assertRaisesRegex(gpu.GpuUnavailableError, 'missing driver'):
                gpu.GpuMapper({}, 16 / 9, 64, 32)


class GpuPixelsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        result = gpu.probe_gpu()
        if not result['available']:
            raise unittest.SkipTest('OpenGL 3.3 unavailable: ' + result['error'])
        cls.directory = ROOT / '.cache' / 'tests-gpu'
        cls.directory.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def flat(aspect=1):
        return dict(bend_deg=0, view_elevation_deg=0, screen_width_mm=100 * aspect,
                    screen_height_mm=100)

    def test_top_down_orientation_and_srgb_colour(self):
        source = np.zeros((64, 128, 3), dtype=np.uint8)
        source[:32, :64] = (234, 14, 20)
        source[:32, 64:] = (18, 217, 37)
        source[32:, :64] = (15, 25, 231)
        source[32:, 64:] = (239, 198, 16)
        with gpu.GpuMapper(self.flat(2), 2, 128, 64) as mapper:
            result = mapper.render(source)
        self.assertEqual(result.shape, source.shape)
        self.assertEqual(result.dtype, np.uint8)
        self.assertTrue(result.flags.c_contiguous)
        for y, x in ((16, 32), (16, 96), (48, 32), (48, 96)):
            np.testing.assert_allclose(result[y, x], source[y, x], atol=1)

    def test_cubic_interpolation_is_in_linear_light(self):
        source = np.zeros((2, 2, 3), dtype=np.uint8)
        source[1] = 255
        with gpu.GpuMapper(self.flat(), 1, 1, 1) as mapper:
            value = mapper.render(source)[0, 0]
        np.testing.assert_allclose(value, [188, 188, 188], atol=1)

    def test_texture_updates_and_input_is_read_only(self):
        source = np.full((32, 64, 3), (34, 77, 145), dtype=np.uint8)
        original = source.copy()
        with gpu.GpuMapper(self.flat(2), 2, 64, 32) as mapper:
            first = mapper.render(source)
            second = mapper.render(np.full_like(source, (174, 21, 108)))
        np.testing.assert_array_equal(source, original)
        np.testing.assert_allclose(first[16, 32], (34, 77, 145), atol=1)
        np.testing.assert_allclose(second[16, 32], (174, 21, 108), atol=1)

    def test_contain_black_background_and_decoration(self):
        source = np.full((64, 64, 3), (0, 255, 0), dtype=np.uint8)
        config = {'fit': 'contain', 'presentation': 'standee'}
        with tempfile.TemporaryDirectory(dir=self.directory) as temporary:
            path = Path(temporary) / 'decoration test.png'
            Image.new('RGBA', (128, 128), (255, 0, 0, 255)).save(path)
            with gpu.GpuMapper(config, 1, 224, 108, decoration_path=path) as mapper:
                decorated = mapper.render(source)
            with gpu.GpuMapper(config, 1, 224, 108) as mapper:
                plain = mapper.render(source)
        self.assertGreater(np.count_nonzero(np.all(plain == 0, axis=2)), 100)
        self.assertGreater(np.count_nonzero(np.all(decorated == (255, 0, 0), axis=2)), 100)
        self.assertGreater(np.count_nonzero(np.all(decorated == (0, 255, 0), axis=2)), 100)
        np.testing.assert_array_equal(decorated[54, 112], (0, 255, 0))

    def test_invalid_frames_closed_mapper_and_thread_affinity(self):
        mapper = gpu.GpuMapper({}, 2, 64, 32)
        for bad in [np.zeros((2, 3), dtype=np.uint8), np.zeros((2, 3, 4), dtype=np.uint8),
                    np.zeros((2, 3, 3), dtype=np.float32), np.zeros((0, 3, 3), dtype=np.uint8)]:
            with self.subTest(shape=bad.shape, dtype=bad.dtype), self.assertRaises(ValueError):
                mapper.render(bad)
        errors = []
        def other_thread():
            try:
                mapper.render(np.zeros((32, 64, 3), dtype=np.uint8))
            except RuntimeError as error:
                errors.append(str(error))
        thread = threading.Thread(target=other_thread)
        thread.start(); thread.join()
        self.assertEqual(len(errors), 1)
        mapper.close()
        mapper.close()
        with self.assertRaises(RuntimeError):
            mapper.render(np.zeros((32, 64, 3), dtype=np.uint8))


if __name__ == '__main__':
    unittest.main()
