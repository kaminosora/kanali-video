"""Read back actual OpenGL pixels for the experimental linear-light filter."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / '.deps'), str(ROOT)]
import numpy as np
from PIL import Image
from mapper import gpu
from mapper.geometry import mapping_mesh


def linear(rgb):
    encoded = np.asarray(rgb, dtype=np.float64) / 255
    return np.where(encoded <= .04045, encoded / 12.92, ((encoded + .055) / 1.055) ** 2.4)


def encoded(rgb):
    value = np.asarray(rgb, dtype=np.float64)
    return np.rint(np.where(value <= .0031308, value * 12.92, 1.055 * value ** (1 / 2.4) - .055) * 255)


class GpuBrightnessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        result = gpu.probe_gpu()
        if not result['available']:
            raise unittest.SkipTest('OpenGL 3.3 unavailable: ' + result['error'])
        cls.directory = ROOT / '.cache' / 'tests-gpu-brightness'
        cls.directory.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def constant_gain_mesh(gain, source_uv=None):
        atlas = [[0, 0], [1, 0], [0, 1], [1, 1]]
        return dict(config={}, atlas=atlas, uv=atlas if source_uv is None else [source_uv] * 4,
                    triangles=[[0, 1, 2], [1, 3, 2]], brightness_gain=[gain] * 4)

    def test_scalar_gain_is_applied_in_linear_light_to_all_colour_channels(self):
        for rgb in [(128, 128, 128), (68, 128, 220), (0, 0, 0)]:
            source = np.full((16, 32, 3), rgb, dtype=np.uint8)
            with self.subTest(rgb=rgb), mock.patch.object(gpu, 'mapping_mesh', return_value=self.constant_gain_mesh(.5)):
                with gpu.GpuMapper({}, 2, 32, 16) as mapper:
                    result = mapper.render(source)
            np.testing.assert_allclose(result[8, 16], encoded(linear(rgb) * .5), atol=1)

    def test_standee_decoration_receives_the_same_gain(self):
        rgb = (68, 128, 220)
        with tempfile.TemporaryDirectory(dir=self.directory) as temporary:
            path = Path(temporary) / 'decoration.png'
            Image.new('RGBA', (32, 32), (*rgb, 255)).save(path)
            mesh = self.constant_gain_mesh(.5, source_uv=[1.05, .5])
            with mock.patch.object(gpu, 'mapping_mesh', return_value=mesh):
                with gpu.GpuMapper({}, 1, 32, 16, decoration_path=path) as mapper:
                    result = mapper.render(np.full((16, 16, 3), 255, dtype=np.uint8))
            np.testing.assert_allclose(result[8, 16], encoded(linear(rgb) * .5), atol=1)

    def test_disabled_and_neutral_settings_are_pixel_identical(self):
        source = np.random.default_rng(1847).integers(0, 256, (32, 64, 3), dtype=np.uint8)
        with gpu.GpuMapper({}, 2, 256, 128) as mapper:
            baseline = mapper.render(source)
        for config in ({'brightness_enabled': False, 'brightness_floor': .1},
                       {'brightness_enabled': True, 'brightness_floor': 1},
                       {'brightness_enabled': True, 'brightness_strength': 0}):
            with self.subTest(config=config), gpu.GpuMapper(config, 2, 256, 128) as mapper:
                np.testing.assert_array_equal(mapper.render(source), baseline)

    def test_gray_card_equalizes_the_simulated_angular_response(self):
        config = {'brightness_enabled': True}
        with gpu.GpuMapper(config, 2, 512, 128) as mapper:
            result = mapper.render(np.full((64, 128, 3), 180, dtype=np.uint8))
        # Pixel centres lie on each cell's diagonal. Interpolate the independently
        # supplied physical response on that diagonal and reverse top-down rows.
        mesh = mapping_mesh(config, 2, columns=512, rows=128)
        response = np.asarray(mesh['angular_response']).reshape(129, 513)
        response = ((response[:-1, 1:] + response[1:, :-1]) / 2)[::-1]
        apparent = linear(result[:, :, 0]) * response
        expected = linear(180) * mesh['brightness_summary']['target_response']
        self.assertLess(int(result[64, 256, 0]), int(result[64, 10, 0]) - 5)
        np.testing.assert_allclose(apparent, expected, atol=.004)


if __name__ == '__main__':
    unittest.main()
