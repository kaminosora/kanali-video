"""KANALI's upload canvas is 2:1; resizing must preserve all atlas edges."""
import io
from fractions import Fraction
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / '.deps')]

import av
import numpy as np
from PIL import Image

from mapper import output
from mapper.encoding import encoder_command
from tests.tool_paths import find_tool

FFMPEG = find_tool('ffmpeg')


class OutputCanvasTests(unittest.TestCase):
    def test_upload_profile_and_supersampling_are_exactly_two_to_one(self):
        self.assertEqual((output.WIDTH, output.HEIGHT, output.ASPECT), (2160, 1080, 2))
        self.assertEqual(output.render_size(1), (2160, 1080))
        self.assertEqual(output.render_size(2), (4320, 2160))
        self.assertEqual(output.profile(), {'width': 2160, 'height': 1080,
                                          'aspect': 2, 'label': 'KANALI 2:1'})

    @unittest.skipUnless(FFMPEG, 'FFmpeg unavailable: configure FFMPEG_PATH, PATH or settings.json')
    def test_real_png_and_raw_video_preserve_four_edges_with_square_pixels(self):
        # The 24px side markers would disappear if 2240 were centre-cropped
        # down to 2160. They must survive full-width atlas resampling.
        width, height = 2240, 1080
        source = np.full((height, width, 3), 35, dtype=np.uint8)
        colours = {'left': (230, 25, 35), 'right': (25, 40, 225),
                   'top': (25, 225, 45), 'bottom': (230, 210, 25)}
        source[:, :24] = colours['left']
        source[:, -24:] = colours['right']
        source[:24] = colours['top']
        source[-24:] = colours['bottom']
        png = io.BytesIO()
        Image.fromarray(source).save(png, format='PNG')
        cache = ROOT / '.cache' / 'aspect-check'
        cache.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=cache) as temporary:
            for quality in ('high', 'master'):
                for input_format in ('image2pipe', 'rawvideo'):
                    with self.subTest(quality=quality, input_format=input_format):
                        extension = 'mp4' if quality == 'high' else 'mkv'
                        path = Path(temporary) / f'{quality}-{input_format}.{extension}'
                        options = {'fps_num': 30, 'fps_den': 1, 'frames': 1,
                                   'quality': quality, 'input_format': input_format,
                                   'width': width, 'height': height, 'encoder': 'libx264'}
                        process = subprocess.run(encoder_command(FFMPEG, path, options),
                            input=source.tobytes() if input_format == 'rawvideo' else png.getvalue(),
                            capture_output=True, timeout=45,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                        self.assertEqual(process.returncode, 0, process.stderr.decode(errors='replace'))
                        with av.open(str(path)) as container:
                            stream = container.streams.video[0]
                            self.assertEqual((stream.width, stream.height), (2160, 1080))
                            self.assertEqual(stream.sample_aspect_ratio, 1)
                            self.assertEqual(Fraction(stream.width, stream.height) * stream.sample_aspect_ratio, 2)
                            # PyAV may leave DAR metadata unset for FFV1/MKV;
                            # coded dimensions plus SAR still define its DAR.
                            if stream.display_aspect_ratio is not None:
                                self.assertEqual(stream.display_aspect_ratio, 2)
                            self.assertEqual(len(list(container.decode(stream))), 1)
                        decoded = subprocess.run([FFMPEG, '-v', 'error', '-i', str(path),
                            '-frames:v', '1', '-pix_fmt', 'rgb24', '-f', 'rawvideo', 'pipe:1'],
                            capture_output=True, timeout=45,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                        self.assertEqual(decoded.returncode, 0, decoded.stderr.decode(errors='replace'))
                        frame = np.frombuffer(decoded.stdout, dtype=np.uint8).reshape(1080, 2160, 3)
                        samples = {'left': frame[530:550, 4:16], 'right': frame[530:550, -16:-4],
                                   'top': frame[4:16, 1070:1090], 'bottom': frame[-16:-4, 1070:1090]}
                        for edge, sample in samples.items():
                            np.testing.assert_allclose(sample.mean(axis=(0, 1)), colours[edge], atol=6,
                                err_msg=f'{quality}/{input_format}: {edge} marker cropped or changed')


if __name__ == '__main__':
    unittest.main()
