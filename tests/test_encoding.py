import io
import sys
import tempfile
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / ".deps"))
import av
from PIL import Image
from tests.tool_paths import find_tool

FFMPEG = find_tool('ffmpeg')


class EncodingTests(unittest.TestCase):
    @unittest.skipUnless(FFMPEG, 'FFmpeg unavailable: configure FFMPEG_PATH, PATH or settings.json')
    def test_raw_rgb_pipe_keeps_native_size_fractional_rate_and_bt709(self):
        from mapper.encoding import encoder_command
        root = ROOT / '.cache' / 'tests-encoding'
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as directory:
            output = Path(directory) / 'raw.mp4'
            options = {'fps_num': 30000, 'fps_den': 1001, 'frames': 2, 'quality': 'high',
                       'input_format': 'rawvideo', 'width': 64, 'height': 32, 'encoder': 'libx264'}
            command = encoder_command(FFMPEG, output, options)
            result = subprocess.run(command, input=bytes((120, 80, 40))*64*32*2,
                                    capture_output=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))
            with av.open(str(output)) as container:
                stream = container.streams.video[0]
                frames = list(container.decode(stream))
                self.assertEqual((stream.width, stream.height, len(frames)), (2160, 1080, 2))
                self.assertEqual(stream.sample_aspect_ratio, 1)
                self.assertEqual(stream.display_aspect_ratio, 2)
                self.assertEqual(str(stream.average_rate), '30000/1001')
                self.assertEqual((stream.codec_context.colorspace, stream.codec_context.color_primaries,
                                  stream.codec_context.color_trc), (1, 1, 1))
            # Decode through the actual FFmpeg colour-managed path. PyAV's
            # to_image reformatter in the pinned runtime defaults differently
            # even when the AVFrame has a BT.709 declaration.
            decoded = subprocess.run([FFMPEG, '-v', 'error',
                '-i', str(output), '-frames:v', '1', '-vf', 'crop=2:2:0:0',
                '-pix_fmt', 'rgb24', '-f', 'rawvideo', 'pipe:1'], capture_output=True, timeout=30)
            self.assertEqual(decoded.returncode, 0, decoded.stderr.decode(errors='replace'))
            for actual, expected in zip(decoded.stdout[:3], (120, 80, 40)):
                self.assertLess(abs(actual-expected), 5)

    def test_encoder_selection_has_backend_specific_quality_controls(self):
        import mapper.encoding as encoding
        self.assertTrue(hasattr(encoding, 'encoder_args'), 'Capability probes need shared encoder arguments')
        for codec, quality_option, pixel_format in (
                ('libx264', '-crf', 'yuv420p'), ('h264_nvenc', '-cq', 'yuv420p'),
                ('h264_qsv', '-global_quality', 'nv12'), ('h264_amf', '-qp_i', 'yuv420p')):
            with self.subTest(codec=codec):
                args = encoding.encoder_args(codec)
                self.assertEqual(args[args.index('-c:v')+1], codec)
                self.assertEqual(args[args.index(quality_option)+1], '14')
                self.assertEqual(args[args.index('-pix_fmt')+1], pixel_format)
                self.assertNotIn('-pass', args, 'NVENC multipass must not be confused with file-level passes')
        for codec in ('libx264', 'h264_nvenc'):
            args = encoding.encoder_args(codec, {'budget_maxrate': 5_000_000})
            self.assertEqual(args[args.index('-maxrate')+1], '5000000')
        for codec in ('h264_qsv', 'h264_amf'):
            self.assertNotIn('-maxrate', encoding.encoder_args(codec, {'budget_maxrate': 5_000_000}))

    def test_unknown_encoder_or_incomplete_raw_raster_is_rejected_before_launch(self):
        from mapper.encoding import encoder_command
        base = {'fps_num': 30, 'fps_den': 1, 'frames': 2, 'quality': 'high'}
        for extra in ({'encoder': 'invented'}, {'input_format': 'unknown'},
                      {'input_format': 'rawvideo'}, {'input_format': 'rawvideo', 'width': -1, 'height': 32}):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                encoder_command('ffmpeg', 'out.mp4', dict(base, **extra))

    @unittest.skipUnless(FFMPEG, 'FFmpeg unavailable: configure FFMPEG_PATH, PATH or settings.json')
    def test_mp4_has_explicit_bt709_colour_and_fractional_timebase(self):
        from mapper.encoding import encoder_command
        root = ROOT / ".cache" / "tests-encoding"
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as directory:
            output = Path(directory) / "test.mp4"
            options = {"fps_num": 30000, "fps_den": 1001, "frames": 2, "quality": "high"}
            command = encoder_command(FFMPEG, output, options)
            png = io.BytesIO()
            Image.new("RGB", (64, 36), (120, 80, 40)).save(png, format="PNG")
            result = subprocess.run(command, input=png.getvalue()*2, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
            with av.open(str(output)) as container:
                stream = container.streams.video[0]
                frames = list(container.decode(stream))
                self.assertEqual((stream.width, stream.height), (2160, 1080))
                self.assertEqual(stream.sample_aspect_ratio, 1)
                self.assertEqual(stream.display_aspect_ratio, 2)
                self.assertEqual(str(stream.average_rate), "30000/1001")
                self.assertEqual(len(frames), 2)
                ctx = stream.codec_context
                self.assertEqual((ctx.colorspace, ctx.color_primaries, ctx.color_trc), (1, 1, 1))
                self.assertEqual(ctx.format.name, "yuv420p")


if __name__ == "__main__":
    unittest.main()
