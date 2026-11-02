"""Real fixture tests: orientation, SAR, timing, colour safety and source integrity."""
from __future__ import annotations

import hashlib
import importlib
import os
from pathlib import Path
import subprocess
import struct
import sys
import tempfile
import unittest
import zlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / '.deps'))
sys.path.insert(0, str(ROOT))
from PIL import Image, ImageDraw
import av
from tests.tool_paths import find_tool

FFMPEG = find_tool('ffmpeg', root=ROOT)
try:
    media = importlib.import_module('mapper.media')
except ModuleNotFoundError:
    media = None


class MediaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if FFMPEG is None:
            raise unittest.SkipTest('FFmpeg unavailable: set FFMPEG_PATH, PATH, or local settings.json.')
        cls.cache = ROOT / '.cache' / 'tests-media'
        cls.cache.mkdir(parents=True, exist_ok=True)
        cls.temp = tempfile.TemporaryDirectory(prefix='case-', dir=cls.cache)
        cls.directory = Path(cls.temp.name)
        cls.png = cls.directory / '中文 colour blocks.png'
        image = Image.new('RGB', (160, 80))
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 0, 79, 39), fill=(255, 0, 0))
        draw.rectangle((80, 0, 159, 39), fill=(0, 255, 0))
        draw.rectangle((0, 40, 79, 79), fill=(0, 0, 255))
        draw.rectangle((80, 40, 159, 79), fill=(255, 255, 0))
        image.save(cls.png)
        cls.video = cls.directory / '中文 rational rate.mp4'
        cls.ffmpeg('-loop', '1', '-i', cls.png, '-t', '0.4004', '-r', '30000/1001',
                   '-c:v', 'libx264', '-pix_fmt', 'yuv420p', cls.video)
        cls.rotated = cls.directory / 'rotated 90.mp4'
        cls.ffmpeg('-display_rotation:v:0', '90', '-i', cls.video, '-c', 'copy', cls.rotated)
        cls.sar = cls.directory / 'non square.mp4'
        cls.ffmpeg('-i', cls.video, '-vf', 'setsar=4/3', '-c:v', 'libx264', cls.sar)
        cls.hdr = cls.directory / 'hdr pq.mp4'
        cls.ffmpeg('-i', cls.video, '-c', 'copy', '-color_trc', 'smpte2084',
                   '-color_primaries', 'bt2020', '-colorspace', 'bt2020nc', cls.hdr)
        cls.audio = cls.directory / 'sound only.wav'
        cls.ffmpeg('-f', 'lavfi', '-i', 'sine=frequency=1000:duration=0.2', cls.audio)
        cls.bad = cls.directory / 'broken.mp4'
        cls.bad.write_bytes(b'This is not a media file.')
        for index, colour in enumerate(('red', '#00ff00', 'blue')):
            Image.new('RGB', (64, 32), colour).save(cls.directory / f'time-{index:02d}.png')
        cls.cfr_tail = cls.directory / 'CFR tail 0.1s.mp4'
        cls.ffmpeg('-framerate', '30', '-i', cls.directory / 'time-%02d.png', '-frames:v', '3',
                   '-c:v', 'libx264', '-bf', '0', '-crf', '0', '-pix_fmt', 'yuv444p', cls.cfr_tail)
        cls.vfr_hold = cls.directory / 'VFR long hold.mp4'
        cls.ffmpeg('-framerate', '10', '-i', cls.directory / 'time-%02d.png', '-frames:v', '3',
                   '-vf', "settb=1/1000,setpts='if(eq(N,0),0,if(eq(N,1),100,900))'",
                   '-fps_mode', 'vfr', '-enc_time_base', '1:1000', '-video_track_timescale', '1000',
                   '-c:v', 'libx264', '-bf', '0', '-crf', '0', '-pix_fmt', 'yuv444p', cls.vfr_hold)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    @classmethod
    def ffmpeg(cls, *args):
        result = subprocess.run([str(FFMPEG), '-hide_banner', '-loglevel', 'error', '-y',
                                 *map(str, args)], capture_output=True, timeout=30)
        if result.returncode:
            raise AssertionError(result.stderr.decode('utf-8', errors='replace'))

    def setUp(self):
        self.assertIsNotNone(media, 'mapper.media is not implemented yet')

    def test_image_probe_and_preview_keep_original_and_composite_alpha_black(self):
        """Catches overwriting source pixels and dropping alpha without black compositing."""
        source = self.directory / 'transparent 中文.png'
        Image.new('RGBA', (1200, 600), (255, 0, 0, 128)).save(source)
        before = hashlib.sha256(source.read_bytes()).digest()
        info = media.probe_media(source)
        self.assertEqual((info['width'], info['height'], info['kind']), (1200, 600, 'image'))
        self.assertEqual(info['aspect'], 2.0)
        result = media.prepare_preview(source, self.directory / 'image cache', str(FFMPEG))
        with Image.open(result['preview_path']) as preview:
            self.assertEqual(preview.size, (960, 480))
            self.assertEqual(preview.convert('RGB').getpixel((20, 20)), (128, 0, 0))
        self.assertEqual(hashlib.sha256(source.read_bytes()).digest(), before)

    def test_exif_rotation_is_applied_before_dimensions_and_preview(self):
        """Catches encoded JPEG width being mistaken for displayed width."""
        path = self.directory / 'phone portrait.jpg'
        image = Image.open(self.png)
        exif = image.getexif()
        exif[274] = 6
        image.save(path, exif=exif)
        info = media.probe_media(path)
        self.assertEqual((info['width'], info['height']), (80, 160))
        result = media.prepare_preview(path, self.directory / 'exif cache', str(FFMPEG))
        with Image.open(result['preview_path']) as preview:
            self.assertEqual(preview.size, (80, 160))
            pixel = preview.getpixel((15, 15))
            self.assertGreater(pixel[2], 220)  # clockwise EXIF orientation 6: blue at top-left

    def test_probe_preserves_rational_rate(self):
        """Catches integer rounding of 29.97 fps and wrong stream duration."""
        info = media.probe_media(self.video)
        self.assertEqual((info['fps_num'], info['fps_den']), (30000, 1001))
        self.assertAlmostEqual(info['fps'], 29.97002997002997)
        self.assertAlmostEqual(info['duration'], 0.4004, places=3)
        self.assertEqual(info['frame_count'], 12)
        self.assertEqual((info['width'], info['height'], info['kind']), (160, 80, 'video'))

    def test_export_cfr_tail_keeps_frame_covering_cut_start(self):
        """The last 0.01s of a 0.1s/30fps video must retain its last blue frame."""
        self.assertTrue(hasattr(media, 'export_normalization_filter'), 'Export timing filter is missing')
        info = media.probe_media(self.cfr_tail)
        self.assertAlmostEqual(info['duration'], 0.1, places=5)
        output = self.directory / 'CFR tail normalized.mkv'
        vf = media.export_normalization_filter(info, 0.09, 30, 1, 1)
        self.ffmpeg('-noautorotate', '-i', self.cfr_tail, '-vf', vf, '-frames:v', '1',
                    '-c:v', 'ffv1', '-pix_fmt', 'gbrp16le', output)
        with av.open(str(output)) as container:
            frames = list(container.decode(video=0))
        self.assertEqual(len(frames), 1)
        pixel = frames[0].to_image().getpixel((10, 10))
        self.assertGreater(pixel[2], 245)
        self.assertLess(max(pixel[:2]), 10)
        self.assertEqual(frames[0].pts, 0)
        self.assertEqual(media.verify_normalized(output, 1)['frame_count'], 1)

    def test_export_vfr_holds_previous_frame_until_next_presentation_time(self):
        """A cut inside the green frame's [0.1,0.9) span must not jump to blue."""
        self.assertTrue(hasattr(media, 'export_normalization_filter'), 'Export timing filter is missing')
        with av.open(str(self.vfr_hold)) as container:
            timestamps = [float(frame.pts * frame.time_base) for frame in container.decode(video=0)]
        self.assertEqual(timestamps, [0.0, 0.1, 0.9])  # independently validate real VFR fixture
        info = media.probe_media(self.vfr_hold)
        cases = ((0.25, 8, [1, 1, 1, 1, 1, 1, 1, 2]),
                 (0.2, 8, [1, 1, 1, 1, 1, 1, 1, 2]),
                 (0.95, 1, [2]))
        for start, count, dominant_channels in cases:
            with self.subTest(start=start):
                output = self.directory / f'VFR normalized {start}.mkv'
                vf = media.export_normalization_filter(info, start, 10, 1, count)
                self.ffmpeg('-noautorotate', '-i', self.vfr_hold, '-vf', vf, '-frames:v', str(count),
                            '-c:v', 'ffv1', '-pix_fmt', 'gbrp16le', output)
                with av.open(str(output)) as container:
                    frames = list(container.decode(video=0))
                pixels = [frame.to_image().getpixel((10, 10)) for frame in frames]
                actual = [max(range(3), key=pixel.__getitem__) for pixel in pixels]
                self.assertEqual(actual, dominant_channels)
                self.assertEqual(media.verify_normalized(output, count)['frame_count'], count)

    def test_export_does_not_advance_to_frame_when_start_is_just_before_its_pts(self):
        """Subtracting a fractional start in a coarse input TB must not truncate to the future frame."""
        self.assertTrue(hasattr(media, 'export_normalization_filter'), 'Export timing filter is missing')
        cases = ((self.cfr_tail, 1 / 30 - 0.000001, 30, 0),
                 (self.cfr_tail, 1 / 30 + 0.000001, 30, 1),
                 (self.vfr_hold, 0.1 - 0.000001, 10, 0),
                 (self.vfr_hold, 0.1, 10, 1))
        for index, (source, start, rate, expected_channel) in enumerate(cases):
            with self.subTest(start=start):
                info = media.probe_media(source)
                output = self.directory / f'boundary normalized {index}.mkv'
                vf = media.export_normalization_filter(info, start, rate, 1, 1)
                self.ffmpeg('-noautorotate', '-i', source, '-vf', vf, '-frames:v', '1',
                            '-c:v', 'ffv1', '-pix_fmt', 'gbrp16le', output)
                with av.open(str(output)) as container:
                    frame = next(container.decode(video=0))
                    pixel = frame.to_image().getpixel((10, 10))
                self.assertEqual(max(range(3), key=pixel.__getitem__), expected_channel)

    def test_verify_normalized_rejects_short_long_and_empty_inputs(self):
        """A zero exit code from FFmpeg cannot substitute for checking actual frames."""
        self.assertTrue(hasattr(media, 'verify_normalized'), 'Intermediate verification is missing')
        self.assertEqual(media.verify_normalized(self.cfr_tail, 3)['frame_count'], 3)
        for path, expected in ((self.cfr_tail, 2), (self.cfr_tail, 4), (self.bad, 1), (self.audio, 1)):
            with self.subTest(path=path.name, expected=expected), self.assertRaises(ValueError):
                media.verify_normalized(path, expected)

    def test_export_rational_rate_keeps_exact_rate_and_requested_frame_count(self):
        """Do not round 30000/1001 to 30 when constructing an export timeline."""
        self.assertTrue(hasattr(media, 'export_normalization_filter'), 'Export timing filter is missing')
        info = media.probe_media(self.video)
        output = self.directory / 'rational normalized.mkv'
        vf = media.export_normalization_filter(info, 0.05, 30000, 1001, 10)
        self.ffmpeg('-noautorotate', '-i', self.video, '-vf', vf, '-frames:v', '10',
                    '-c:v', 'ffv1', '-pix_fmt', 'gbrp16le', output)
        with av.open(str(output)) as container:
            stream = container.streams.video[0]
            self.assertEqual((stream.average_rate.numerator, stream.average_rate.denominator), (30000, 1001))
            frames = list(container.decode(stream))
        self.assertEqual(len(frames), 10)
        for index, frame in enumerate(frames):
            self.assertAlmostEqual(float(frame.pts * frame.time_base), index * 1001 / 30000, delta=0.00051)
        self.assertEqual(media.verify_normalized(output, 10)['frame_count'], 10)

    def test_export_rejects_frame_count_past_source_end(self):
        self.assertTrue(hasattr(media, 'export_normalization_filter'), 'Export timing filter is missing')
        info = media.probe_media(self.cfr_tail)
        with self.assertRaises(ValueError):
            media.export_normalization_filter(info, 0, 30, 1, 4)

    def test_intermediate_verification_reports_progress_and_can_be_cancelled(self):
        import inspect
        self.assertIn('cancelled', inspect.signature(media.verify_normalized).parameters,
                      'Verification currently ignores cancellation for the entire file')
        self.assertIn('progress', inspect.signature(media.verify_normalized).parameters)
        updates=[]
        with self.assertRaises(InterruptedError):
            media.verify_normalized(self.cfr_tail, 3, progress=lambda n: updates.append(n),
                                    cancelled=lambda: bool(updates))
        self.assertTrue(updates)
        self.assertLess(updates[-1], 3)
        updates.clear()
        result=media.verify_normalized(self.cfr_tail,3,progress=lambda n:updates.append(n))
        self.assertEqual(result['frame_count'],3)
        self.assertEqual(updates[-1],3)

    def test_native_yuv_intermediate_preserves_decoded_samples_and_colour_tags(self):
        self.assertTrue(hasattr(media,'intermediate_pixel_format'))
        info=media.probe_media(self.video)
        output=self.directory/'native-yuv.mkv'
        self.ffmpeg('-noautorotate','-i',self.video,'-vf',media.export_normalization_filter(info,0,30000,1001,12),
                    '-an','-c:v','ffv1','-level','3','-pix_fmt',media.intermediate_pixel_format(info),output)
        with av.open(str(self.video)) as source, av.open(str(output)) as intermediate:
            original=list(source.decode(video=0)); normalized=list(intermediate.decode(video=0))
        self.assertEqual(len(original),len(normalized))
        for a,b in zip(original,normalized):
            self.assertEqual(b.format.name,'yuv420p')
            self.assertTrue((a.to_ndarray(format='yuv420p')==b.to_ndarray(format='yuv420p')).all())

    def test_sar_changes_display_geometry_and_normalized_pixels(self):
        """Catches silent anamorphic stretching after setsar=1."""
        info = media.probe_media(self.sar)
        self.assertEqual((info['sar_num'], info['sar_den']), (4, 3))
        self.assertAlmostEqual(info['aspect'], 8 / 3)
        self.assertEqual((info['width'], info['height']), (213, 80))
        output = self.directory / 'normalized sar.mkv'
        self.ffmpeg('-noautorotate', '-i', self.sar, '-vf', media.normalize_filter(info, 30000, 1001),
                    '-c:v', 'ffv1', '-pix_fmt', 'gbrp16le', output)
        with av.open(str(output)) as container:
            stream = container.streams.video[0]
            frame = next(container.decode(stream))
            self.assertEqual((frame.width, frame.height), (213, 80))
            self.assertEqual(stream.sample_aspect_ratio, 1)

    def test_video_rotation_matches_display_matrix_without_double_rotation(self):
        """Catches wrong display-matrix sign or applying auto-rotation twice."""
        before = hashlib.sha256(self.rotated.read_bytes()).digest()
        info = media.probe_media(self.rotated)
        self.assertEqual((info['width'], info['height']), (80, 160))
        result = media.prepare_preview(self.rotated, self.directory / 'video cache', str(FFMPEG))
        with av.open(str(result['preview_path'])) as container:
            stream = container.streams.video[0]
            frame = next(container.decode(stream))
            self.assertEqual((frame.width, frame.height), (80, 160))
            self.assertEqual(frame.rotation, 0, 'Browser proxy must not keep the already-baked display matrix')
            pixel = frame.to_image().getpixel((15, 15))
            self.assertGreater(pixel[1], 220)  # CCW 90: green at top-left
            self.assertLess(pixel[0], 40)
        self.assertEqual(hashlib.sha256(self.rotated.read_bytes()).digest(), before)
        self.assertTrue(Path(result['thumbnail_path']).is_file())

    def test_hdr_is_rejected_before_proxy_conversion(self):
        """Catches HDR pixels being relabelled as SDR."""
        with self.assertRaisesRegex(ValueError, 'HDR|PQ|HLG'):
            media.probe_media(self.hdr)
        with self.assertRaisesRegex(ValueError, 'HDR|PQ|HLG'):
            media.prepare_preview(self.hdr, self.directory / 'hdr cache', str(FFMPEG))

    def test_png_hdr_cicp_and_16_bit_rgb_are_not_silently_read_as_sdr(self):
        """Catches Pillow ignoring cICP or silently reducing 16-bit RGB to 8-bit."""
        data = self.png.read_bytes()
        declaration = b'cICP' + bytes((9, 16, 0, 1))  # BT.2020 + PQ + RGB + full range
        chunk = struct.pack('>I', 4) + declaration + struct.pack('>I', zlib.crc32(declaration))
        tagged = self.directory / 'HDR tagged.png'
        tagged.write_bytes(data[:33] + chunk + data[33:])
        high_depth = self.directory / '16-bit RGB.png'
        self.ffmpeg('-i', self.png, '-frames:v', '1', '-pix_fmt', 'rgb48be', high_depth)
        for path in (tagged, high_depth):
            with self.subTest(path=path.name), self.assertRaisesRegex(ValueError, 'HDR|高位深'):
                media.probe_media(path)

    def test_rejects_audio_only_bad_and_missing_files(self):
        for path in (self.audio, self.bad, self.directory / 'missing.mp4'):
            with self.subTest(path=path.name), self.assertRaises((ValueError, FileNotFoundError)):
                media.probe_media(path)

    def test_calibration_contains_2160x1080_grid_and_asymmetric_colour_marks(self):
        """Catches blank or wrong-size calibration output and lost orientation cues."""
        path = self.directory / 'calibration 中文.png'
        media.create_calibration(path)
        with Image.open(path) as image:
            self.assertEqual(image.size, (2160, 1080))
            self.assertGreater(len(image.getcolors(maxcolors=1_000_000)), 20)
            self.assertNotEqual(image.getpixel((40, 40)), image.getpixel((2120, 40)))
        self.assertEqual(media.probe_media(path)['kind'], 'image')


if __name__ == '__main__':
    unittest.main()
