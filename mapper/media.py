"""Read-only local media inspection and disposable browser proxies.

``width``/``height`` describe upright, square-pixel display rasters. ``aspect``
retains the exact SAR-derived ratio before integer raster rounding. No HDR tone
mapping is implicit: PQ, HLG, Dolby Vision and unsupported wide-gamut media fail.
"""
from __future__ import annotations

from fractions import Fraction
import hashlib
import io
import math
from pathlib import Path
import struct
import subprocess
import sys
import time
import uuid

from mapper.paths import app_root
PROJECT_ROOT = app_root()
LOCAL_DEPS = PROJECT_ROOT / '.deps'
if LOCAL_DEPS.is_dir() and str(LOCAL_DEPS) not in sys.path:
    sys.path.insert(0, str(LOCAL_DEPS))

try:
    import av
    from PIL import Image, ImageCms, ImageDraw, ImageFont, ImageOps, UnidentifiedImageError
except ImportError as exc:
    raise ImportError('缺少项目依赖，请运行 python scripts/setup_deps.py。') from exc


def _source(path: str | Path) -> Path:
    path = Path(path).expanduser().resolve(strict=True)
    if not path.is_file():
        raise ValueError('请选择本地图片或视频文件。')
    return path


def _still_image(path: Path) -> Image.Image | None:
    """Load supported stills, orient, convert ICC to sRGB, composite onto black."""
    # Pillow 11 can discard PNG cICP and down-convert 16-bit RGB without exposing
    # either in Image.mode/info. Inspect encoded declarations before decoding.
    with path.open('rb') as handle:
        if handle.read(8) == b'\x89PNG\r\n\x1a\n':
            while header := handle.read(8):
                if len(header) != 8:
                    raise ValueError('PNG 文件头损坏。')
                length, chunk = struct.unpack('>I4s', header)
                if chunk in (b'IDAT', b'IEND'):
                    break
                if chunk == b'IHDR':
                    data = handle.read(length)
                    if len(data) != 13:
                        raise ValueError('PNG 尺寸头损坏。')
                    if data[8] > 8:
                        raise ValueError('暂不支持高位深/HDR PNG；请先明确转换为 8 位 SDR sRGB。')
                elif chunk == b'cICP':
                    data = handle.read(length)
                    if len(data) != 4 or data[1] in (16, 18) or data[0] not in (0, 1, 2, 4, 5, 6, 7):
                        raise ValueError('HDR/PQ/HLG 或广色域 PNG 暂不支持，请先转换为 SDR。')
                else:
                    handle.seek(length, 1)
                handle.seek(4, 1)  # CRC validation remains the image decoder's job.
    try:
        image = Image.open(path)
    except UnidentifiedImageError:
        return None
    try:
        if getattr(image, 'is_animated', False):
            return None  # PyAV/FFmpeg preserves animated GIF/APNG timing.
        image.load()
        if image.mode in ('I', 'F', 'I;16', 'I;16B', 'I;16L'):
            raise ValueError('暂不支持高位深/HDR 图片；请先明确转换为 SDR sRGB。')
        # New PNG cICP chunks can declare PQ/HLG even for an 8-bit pixel mode.
        cicp = image.info.get('cicp')
        if cicp and (len(cicp) < 2 or cicp[1] in (16, 18) or cicp[0] == 9):
            raise ValueError('HDR/PQ/HLG 或 BT.2020 图片尚不支持，请先转换为 SDR。')
        oriented = ImageOps.exif_transpose(image)
        alpha = oriented.convert('RGBA').getchannel('A')
        profile = image.info.get('icc_profile')
        if profile:
            try:
                colour = ImageCms.profileToProfile(
                    oriented.convert('RGB'), ImageCms.ImageCmsProfile(io.BytesIO(profile)),
                    ImageCms.createProfile('sRGB'), outputMode='RGB')
            except (ImageCms.PyCMSError, OSError, ValueError) as exc:
                raise ValueError('图片 ICC 色彩配置无法转换为 sRGB。') from exc
        else:
            colour = oriented.convert('RGB')
        black = Image.new('RGB', colour.size, 'black')
        black.paste(colour, (0, 0), alpha)
        return black
    finally:
        image.close()


def _rotation(frame, stream) -> int:
    value = float(getattr(frame, 'rotation', 0) or stream.metadata.get('rotate', 0))
    for side in frame.side_data:
        if side.type.name == 'DISPLAYMATRIX':
            matrix = struct.unpack('=9i', bytes(side))
            a, b, u, c, d, v, x, y, w = matrix
            if a * d - b * c <= 0:
                raise ValueError('暂不支持带镜像的显示矩阵，请先烘焙视频方向。')
            if any((u, v, x, y)) or abs(w - (1 << 30)) > 1:
                raise ValueError('暂不支持包含位移或透视变换的显示矩阵。')
            if not all(abs(abs(t) - 65536) <= 2 or abs(t) <= 2 for t in (a, b, c, d)):
                raise ValueError('暂只支持 0/90/180/270 度的纯旋转显示矩阵。')
    quarter = round(value / 90)
    if abs(value - quarter * 90) > 0.05:
        raise ValueError('暂只支持 0/90/180/270 度的视频旋转。')
    return (quarter * 90) % 360  # positive is counter-clockwise


def probe_media(path: str | Path) -> dict:
    """Return display/timing metadata; reject unreadable, audio-only and HDR input."""
    path = _source(path)
    try:
        still = _still_image(path)
    except (OSError, Image.DecompressionBombError) as exc:
        raise ValueError(f'图片无法读取：{exc}') from exc
    if still is not None:
        width, height = still.size
        still.close()
        return dict(width=width, height=height, aspect=width / height,
                    duration=0.0, fps=0.0, fps_num=0, fps_den=1, frame_count=1,
                    kind='image', warnings=[], rotation=0, sar_num=1, sar_den=1,
                    encoded_width=width, encoded_height=height,
                    square_width=width, square_height=height,
                    color_primaries=1, color_trc=13, colorspace=0, color_range=2,
                    frame_count_estimated=False)
    warnings = []
    try:
        with av.open(str(path), mode='r', options={'protocol_whitelist': 'file'}) as container:
            streams = [s for s in container.streams.video
                       if not bool(s.disposition & av.stream.Disposition.attached_pic)]
            if not streams:
                raise ValueError('文件没有可播放的视频流（只有音频或封面）。')
            stream = streams[0]
            frame = next(container.decode(stream), None)
            if frame is None or frame.width <= 0 or frame.height <= 0:
                raise ValueError('视频没有可解码的画面。')
            codec = stream.codec_context
            colours = {name: int(getattr(codec, name, 2)) for name in
                       ('color_primaries', 'color_trc', 'colorspace', 'color_range')}
            side_types = {s.type.name for s in frame.side_data}
            bits = max(component.bits for component in frame.format.components)
            if colours['color_trc'] in (16, 18) or side_types.intersection({
                    'MASTERING_DISPLAY_METADATA', 'CONTENT_LIGHT_LEVEL', 'DYNAMIC_HDR_PLUS',
                    'DOVI_RPU_BUFFER', 'DOVI_METADATA', 'DYNAMIC_HDR_VIVID'}):
                raise ValueError('HDR/PQ/HLG/Dolby Vision 视频暂不支持；请先转换为 SDR sRGB/BT.709。')
            if colours['color_primaries'] not in (0, 1, 2, 4, 5, 6, 7) or colours['colorspace'] in (9, 10, 14):
                raise ValueError('暂不支持 BT.2020 / P3 等广色域视频，请先进行 SDR BT.709 转换。')
            if colours['color_trc'] not in (0, 1, 2, 4, 5, 6, 7, 13, 14, 15):
                raise ValueError('视频传递函数不受支持，请先转换为 SDR BT.709。')
            if bits > 8 and colours['color_trc'] in (0, 2):
                raise ValueError('高位深视频没有可靠色彩标签，无法排除 HDR；请先明确转换为 SDR。')
            if colours['color_trc'] in (0, 2):
                warnings.append('输入未标注传递函数；按常规 8 位 SDR 处理。')
            if any(c.is_alpha for c in frame.format.components):
                raise ValueError('暂不支持含 Alpha 的视频；请先合成到黑底（透明图片支持）。')
            if getattr(frame, 'interlaced_frame', False):
                raise ValueError('暂不支持隔行视频；请先去隔行转为逐行 SDR。')
            rotation = _rotation(frame, stream)
            sar = Fraction(stream.sample_aspect_ratio or codec.sample_aspect_ratio or 1)
            if sar <= 0 or not (Fraction(1, 100) <= sar <= 100):
                raise ValueError('视频像素宽高比无效。')
            width, height = frame.width, frame.height
            square_width = max(1, round(width * sar))
            square_height = height
            aspect = Fraction(width, height) * sar
            if rotation in (90, 270):
                display_width, display_height = square_height, square_width
                aspect = 1 / aspect
            else:
                display_width, display_height = square_width, square_height
            if sar != 1:
                warnings.append(f'已将非方形像素 SAR={sar} 规整为方形像素；整数栅格可能有不足 1 像素的取整。')
            if rotation:
                warnings.append(f'已应用显示矩阵逆时针 {rotation}°，导出应关闭 FFmpeg 自动旋转。')
            rate = stream.average_rate or stream.guessed_rate or stream.base_rate
            if rate is None or float(rate) <= 0 or not math.isfinite(float(rate)):
                raise ValueError('无法确定视频帧率。')
            rate = Fraction(rate)
            duration = (float(stream.duration * stream.time_base) if stream.duration is not None
                        else float(container.duration / av.time_base) if container.duration is not None else 0)
            if not math.isfinite(duration) or duration <= 0:
                raise ValueError('无法确定有限的视频时长。')
            frame_count = int(stream.frames or 0)
            estimated = not bool(frame_count)
            if estimated:
                frame_count = max(1, round(duration * rate))
                warnings.append('容器没有精确帧数；显示的帧数按时长和平均帧率估算。')
            warnings.append('预览和导出会按指定有理帧率规整为恒定帧率；可变帧率源可能补帧或丢帧。')
            return dict(width=display_width, height=display_height, aspect=float(aspect),
                        duration=duration, fps=float(rate), fps_num=rate.numerator,
                        fps_den=rate.denominator, frame_count=frame_count, kind='video',
                        warnings=warnings, encoded_width=width, encoded_height=height,
                        square_width=square_width, square_height=square_height,
                        sar_num=sar.numerator, sar_den=sar.denominator, rotation=rotation,
                        video_stream_index=stream.index, pixel_format=frame.format.name,
                        time_base_num=stream.time_base.numerator,
                        time_base_den=stream.time_base.denominator,
                        frame_count_estimated=estimated, **colours)
    except (av.error.FFmpegError, OSError, EOFError) as exc:
        raise ValueError(f'文件不是有效或受支持的图片/视频：{exc}') from exc


def normalize_filter(metadata: dict, fps_num: int | None = None,
                     fps_den: int | None = None) -> str:
    """FFmpeg filter for ``-noautorotate`` input; positive rotation is CCW.

    Does not trim. For time-range exports use export_normalization_filter;
    pre-trimming here would discard the frame spanning an arbitrary cut point.
    Output raster permits odd sizes (FFV1 RGB); proxies later make them even.
    Image exports should use ``normalized_image`` produced by prepare_preview,
    which has already baked EXIF, ICC and alpha at full original dimensions.
    """
    width = int(metadata.get('square_width', metadata['width']))
    height = int(metadata.get('square_height', metadata['height']))
    if width <= 0 or height <= 0:
        raise ValueError('无效输出尺寸。')
    filters = ['setpts=PTS-STARTPTS', f'scale={width}:{height}:flags=lanczos', 'setsar=1']
    rotation = int(metadata.get('rotation', 0)) % 360
    if rotation == 90:
        filters.append('transpose=cclock')
    elif rotation == 180:
        filters.extend(('hflip', 'vflip'))
    elif rotation == 270:
        filters.append('transpose=clock')
    elif rotation:
        raise ValueError('不支持的旋转。')
    # A baked rotation must not be inherited by the output MP4/Matroska stream.
    # -map_metadata -1 and rotate=0 alone do not clear frame display side data.
    filters.append('sidedata=mode=delete:type=DISPLAYMATRIX')
    if fps_num is not None:
        rate = Fraction(fps_num, fps_den if fps_den is not None else 1)
        if rate <= 0 or rate > 240:
            raise ValueError('输出帧率必须位于 0 到 240 fps 之间。')
        filters.append(f'fps={rate.numerator}/{rate.denominator}:round=near')
    return ','.join(filters)


def export_normalization_filter(metadata: dict, start: float, fps_num: int,
                                fps_den: int, frames: int) -> str:
    """Sample the displayed frame at ``start + n/rate``, for exactly N frames.

    Decode from the beginning: do not combine with input -ss/-t or a pre-trim.
    Rounding each input timestamp *up* maps a new source frame to the first
    output sample at or after it, keeping the previous frame alive meanwhile.
    Negative timestamps preserve the frame covering the requested cut point.
    A common rational time base plus upward rounding of the shifted timestamp
    avoids truncating a tiny positive offset to zero (showing a future frame).
    ``-frames:v N`` is still useful as a second bound; verify the decoded output.
    """
    if isinstance(start, bool) or not isinstance(start, (int, float)) or not math.isfinite(start) or start < 0:
        raise ValueError('导出起点必须为有限的非负秒数。')
    if any(isinstance(v, bool) or not isinstance(v, int) for v in (fps_num, fps_den, frames)):
        raise ValueError('有理帧率和帧数必须为整数。')
    if fps_num <= 0 or fps_den <= 0 or frames < 1:
        raise ValueError('导出帧率和帧数必须大于零。')
    rate = Fraction(fps_num, fps_den)
    if rate > 240:
        raise ValueError('导出帧率不得超过 240 fps。')
    if metadata.get('kind') == 'video':
        duration = float(metadata['duration'])
        if start >= duration:
            raise ValueError('导出起点必须早于视频结束时间。')
        if frames > math.ceil((duration - start) * float(rate)):
            raise ValueError('导出帧数超出该起点之后的视频时长。')
    input_tb = Fraction(metadata.get('time_base_num', 1), metadata.get('time_base_den', 1_000_000))
    common_den = math.lcm(input_tb.denominator, rate.numerator)
    common_num = math.gcd(input_tb.numerator * (common_den // input_tb.denominator),
                          rate.denominator * (common_den // rate.numerator))
    time_base = Fraction(common_num, common_den)
    if time_base <= 0 or max(time_base.numerator, time_base.denominator) > 2_147_483_647:
        raise ValueError('输入时间基与输出帧率无法在 FFmpeg 整数范围内精确组合。')
    filters = [f'settb={time_base.numerator}/{time_base.denominator}',
               f'setpts=ceil(PTS-STARTPTS-{start:.17g}/TB)',
               f'fps={rate.numerator}/{rate.denominator}:start_time=0:round=up:eof_action=pass',
               'tpad=stop_mode=clone:stop=1', f'trim=end_frame={frames}',
               normalize_filter(metadata)]
    return ','.join(filters)


def intermediate_pixel_format(metadata: dict) -> str:
    """Keep native 8-bit YUV samples when no spatial conversion is necessary.

    Expanding decoded YUV420 to RGB16 adds no source detail, increases raw
    storage fourfold, and makes FFV1 compress poorly. Spatial conversions still
    use the original high precision path; never subsample RGB/YUV444 sources.
    """
    native = metadata.get('pixel_format')
    if (metadata.get('sar_num', 1) == metadata.get('sar_den', 1)
            and metadata.get('rotation', 0) % 360 == 0
            and native in {'yuv420p', 'yuv422p', 'yuv444p', 'gray'}):
        return native
    return 'gbrp16le'


def verify_normalized(path: str | Path, frames: int, *, progress=None, cancelled=None) -> dict:
    """Decode a generated intermediate and require exactly N real video frames.

    Intentionally bypasses probe_media's input HDR policy: normalized FFV1 RGB
    stores SDR samples at 16 bits and is a trusted pipeline product, not input.
    """
    if isinstance(frames, bool) or not isinstance(frames, int) or frames < 1:
        raise ValueError('预期帧数必须为正整数。')
    count = 0
    size = None
    pixel_format = None
    last_time = None
    last_report = 0
    try:
        with av.open(str(_source(path)), mode='r', options={'protocol_whitelist': 'file'}) as container:
            if len(container.streams.video) != 1:
                raise ValueError('规整文件必须包含且只包含一个视频流。')
            for frame in container.decode(container.streams.video[0]):
                if cancelled and cancelled():
                    raise InterruptedError('已取消中间文件校验')
                count += 1
                if count > frames:
                    raise ValueError(f'规整文件帧数过多：预期 {frames} 帧，实际至少 {count} 帧。')
                if size is None:
                    size = (frame.width, frame.height)
                    pixel_format = frame.format.name
                elif size != (frame.width, frame.height):
                    raise ValueError('规整文件的画面尺寸在播放过程中变化。')
                if frame.pts is None:
                    raise ValueError('规整文件缺少帧时间戳。')
                timestamp = frame.pts * frame.time_base
                if count == 1 and timestamp != 0:
                    raise ValueError('规整文件的第一帧时间戳不是零。')
                if last_time is not None and timestamp <= last_time:
                    raise ValueError('规整文件的帧时间戳没有严格递增。')
                last_time = timestamp
                if frame.rotation:
                    raise ValueError('规整文件仍保留会导致二次旋转的显示矩阵。')
                now = time.monotonic()
                if progress and (count == 1 or count == frames or now - last_report >= 0.5):
                    progress(count)
                    last_report = now
    except InterruptedError:
        raise
    except (av.error.FFmpegError, OSError, EOFError) as exc:
        raise ValueError(f'无法解码规整文件：{exc}') from exc
    if count != frames:
        raise ValueError(f'规整文件帧数不足：预期 {frames} 帧，实际 {count} 帧。')
    return dict(frame_count=count, width=size[0], height=size[1], pixel_format=pixel_format)


def _ffmpeg(ffmpeg_path: str | Path, args: list[str], timeout: float = 600) -> None:
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
    result = subprocess.run([str(ffmpeg_path), '-hide_banner', '-loglevel', 'error',
                             '-nostdin', '-y', *args], stdout=subprocess.DEVNULL,
                            stderr=subprocess.PIPE, creationflags=flags, timeout=timeout)
    if result.returncode:
        message = result.stderr.decode('utf-8', errors='replace')[-3000:]
        raise ValueError(f'FFmpeg 无法准备媒体预览：{message}')


def prepare_preview(path: str | Path, cache_dir: str | Path,
                    ffmpeg_path: str | Path) -> dict:
    """Create a <=960-pixel browser preview plus thumbnail, without editing input.

    ``normalized_image_path`` is a full-resolution SDR PNG for still exports.
    The caller must use it (not the small proxy) to preserve EXIF/ICC/alpha fixes.
    """
    source = _source(path)
    metadata = probe_media(source)
    directory = Path(cache_dir).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    # Unique prefix prevents imported files colliding with their own output.
    prefix = hashlib.sha256(str(source).encode('utf-8')).hexdigest()[:12] + '-' + uuid.uuid4().hex[:8]
    preview = directory / (prefix + ('.png' if metadata['kind'] == 'image' else '.mp4'))
    thumbnail = directory / (prefix + '-thumb.png')
    if metadata['kind'] == 'image':
        image = _still_image(source)
        normalized = directory / (prefix + '-normalized.png')
        image.save(normalized, 'PNG')
        proxy = image.copy()
        proxy.thumbnail((960, 960), Image.Resampling.LANCZOS)
        proxy.save(preview, 'PNG')
        proxy.thumbnail((320, 320), Image.Resampling.LANCZOS)
        proxy.save(thumbnail, 'PNG')
        image.close()
        proxy.close()
        return dict(preview_path=str(preview), thumbnail_path=str(thumbnail),
                    normalized_image_path=str(normalized))
    rate = min(Fraction(metadata['fps_num'], metadata['fps_den']), Fraction(30))
    vf = normalize_filter(metadata, rate.numerator, rate.denominator)
    # Bound both axes, then round the browser yuv420p raster down to even values.
    scale = min(1.0, 960 / metadata['width'], 960 / metadata['height'])
    out_width = max(2, int(metadata['width'] * scale) // 2 * 2)
    out_height = max(2, int(metadata['height'] * scale) // 2 * 2)
    vf += f',scale={out_width}:{out_height}:flags=lanczos,setsar=1,format=yuv420p'
    _ffmpeg(ffmpeg_path, ['-noautorotate', '-i', str(source), '-map', f"0:{metadata['video_stream_index']}",
                         '-an', '-sn', '-dn', '-vf', vf, '-c:v', 'libx264', '-preset', 'fast',
                         '-crf', '23', '-pix_fmt', 'yuv420p', '-map_metadata', '-1',
                         '-metadata:s:v:0', 'rotate=0', '-movflags', '+faststart', str(preview)])
    _ffmpeg(ffmpeg_path, ['-i', str(preview), '-frames:v', '1', '-vf',
                         'scale=320:320:force_original_aspect_ratio=decrease', str(thumbnail)], timeout=60)
    return dict(preview_path=str(preview), thumbnail_path=str(thumbnail))


def create_brightness_calibration(path: str | Path) -> str:
    """Neutral encoded-SDR mid-gray for manual angular luminance calibration."""
    from mapper.output import WIDTH, HEIGHT
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError(f'不会覆盖已有文件：{path}')
    Image.new('RGB', (WIDTH, HEIGHT), (128, 128, 128)).save(path)
    return str(path)


def create_calibration(path: str | Path) -> str:
    """Generate KANALI 2:1 SDR alignment artwork with asymmetric corners."""
    from mapper.output import WIDTH as width, HEIGHT as height
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError(f'不会覆盖已有文件：{path}')
    image = Image.new('RGB', (width, height), '#111827')
    draw = ImageDraw.Draw(image)
    title = ImageFont.load_default(size=38)
    label = ImageFont.load_default(size=24)
    small = ImageFont.load_default(size=18)
    for x in range(0, width + 1, 80):
        draw.line((x, 0, x, height - 1), fill='#45546a', width=2)
    for y in range(0, height + 1, 80):
        draw.line((0, y, width - 1, y), fill='#45546a', width=2)
    draw.line((width // 2, 0, width // 2, height - 1), fill='#ffffff', width=4)
    draw.line((0, 540, width - 1, 540), fill='#ffffff', width=4)
    for cx in (width * (2 * i + 1) // 8 for i in range(4)):
        draw.ellipse((cx - 140, 400, cx + 140, 680), outline='#e2e8f0', width=5)
        draw.line((cx - 150, 540, cx + 150, 540), fill='#ffffff', width=2)
        draw.text((cx - 70, 700), f'({cx}, 540)', font=label, fill='white')
    for x, y, colour, text in ((16, 16, '#ef4444', 'TOP LEFT'), (width - 280, 16, '#22c55e', 'TOP RIGHT'),
                               (16, 980, '#3b82f6', 'BOTTOM LEFT'), (width - 280, 980, '#facc15', 'BOTTOM RIGHT')):
        draw.rectangle((x, y, x + 260, y + 84), fill=colour)
        draw.text((x + 12, y + 28), text, font=label, fill='black')
    draw.text((680, 115), 'PANORAMA SE - VIEWPOINT CALIBRATION', font=title, fill='white')
    draw.text((680, 170), f'{width} x {height} | 2:1 upload | circles must look round', font=label, fill='#cbd5e1')
    for x in range(160, width, 160):
        draw.text((x + 5, 250), f'X={x}', font=small, fill='#cbd5e1')
    for y in range(320, 960, 160):
        draw.text((12, y + 5), f'Y={y}', font=small, fill='#cbd5e1')
    draw.rectangle((2, 2, width - 3, height - 3), outline='white', width=5)
    image.save(path, 'PNG')
    image.close()
    return str(path)
