"""Shared encoding arguments; importable by Blender's bundled Python.

Quality numbers are backend-specific, not promises of equal quality or size.
The delivery module's actual-byte gate remains mandatory for compatible MP4.
"""

from mapper.output import WIDTH, HEIGHT


def encoder_args(encoder='libx264', options=None):
    """Return the same codec arguments used for capability probes and encoding.

    FFmpeg source references: libavcodec/nvenc_h264.c, qsvenc.c and
    amfenc_h264.c. QSV ICQ deliberately has no maxrate/lookahead requirement;
    AMF uses the broadly supported explicit CQP controls. Hardware support must
    be established with a real encode, not just ffmpeg -encoders enumeration.
    options['budget_maxrate'] optionally constrains x264/NVENC in bits/second;
    it is a VBV hint, never a strict total-size guarantee.
    """
    options = options or {}
    if encoder == 'libx264':
        args = ['-c:v', encoder, '-preset', 'slow', '-crf', '14']
    elif encoder == 'h264_nvenc':
        args = ['-c:v', encoder, '-preset', 'p7', '-tune', 'hq', '-rc', 'vbr', '-cq', '14', '-b:v', '0']
    elif encoder == 'h264_qsv':
        args = ['-c:v', encoder, '-preset', 'slow', '-global_quality', '14', '-b:v', '0']
    elif encoder == 'h264_amf':
        args = ['-c:v', encoder, '-usage', 'transcoding', '-quality', 'quality',
                '-rc', 'cqp', '-qp_i', '14', '-qp_p', '14', '-qp_b', '14']
    else:
        raise ValueError(f'Unsupported H.264 encoder: {encoder}')
    args += ['-pix_fmt', 'nv12' if encoder == 'h264_qsv' else 'yuv420p', '-profile:v', 'high']
    maxrate = options.get('budget_maxrate')
    if maxrate is not None:
        if isinstance(maxrate, bool) or not isinstance(maxrate, int) or maxrate <= 0:
            raise ValueError('budget_maxrate must be positive integer bits per second')
        if encoder in ('libx264', 'h264_nvenc'):
            args += ['-maxrate', str(maxrate), '-bufsize', str(2 * maxrate)]
    return args


def encoder_command(ffmpeg_path, output_path, options):
    rate = f"{options['fps_num']}/{options['fps_den']}"
    command = [str(ffmpeg_path), "-hide_banner", "-nostdin", "-y"]
    input_format = options.get('input_format', 'image2pipe')
    if input_format == 'rawvideo':
        width, height = options.get('width'), options.get('height')
        if any(isinstance(value, bool) or not isinstance(value, int) or value <= 0 for value in (width, height)):
            raise ValueError('rawvideo input requires positive integer width and height')
        command += ['-f', 'rawvideo', '-pixel_format', 'rgb24', '-video_size', f'{width}x{height}']
    elif input_format == 'image2pipe':
        command += ['-f', 'image2pipe']
    else:
        raise ValueError('input_format must be image2pipe or rawvideo')
    command += ['-framerate', rate, '-i', 'pipe:0', '-an', '-frames:v', str(options['frames'])]
    if options["quality"] == "high":
        command += ["-vf", f"scale={WIDTH}:{HEIGHT}:flags=lanczos:out_color_matrix=bt709:out_range=tv,setsar=1,setparams=range=limited:color_primaries=bt709:color_trc=bt709:colorspace=bt709"]
        command += encoder_args(options.get('encoder', 'libx264'), options)
        command += ["-color_range", "tv",
                    "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-movflags", "+faststart"]
    else:
        command += ["-vf", f"scale={WIDTH}:{HEIGHT}:flags=lanczos,setsar=1", "-c:v", "ffv1", "-level", "3", "-pix_fmt", "gbrp16le", "-slicecrc", "1"]
    return command + [str(output_path)]
