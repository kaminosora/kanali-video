"""Portable OpenGL 3.3 mapping for the 8-bit SDR fast export path.

No CUDA or machine-specific paths are required. Windows standalone contexts use
WGL through glcontext, so NVIDIA, AMD and Intel drivers can provide the context.
Only devices actually probed/rendered on the user's machine are reported usable.

Create, render and close a mapper on one worker thread. Input/output are top-down
uint8 RGB arrays. Source images must already be upright, square-pixel SDR; HDR,
ICC, timing and video decoding belong to the media layer. This is an 8-bit path:
16-bit lossless master jobs should keep the Blender renderer.
"""
from __future__ import annotations

from numbers import Integral, Real
import math
import threading

import numpy as np

from mapper.geometry import mapping_mesh


class GpuUnavailableError(RuntimeError):
    """The requested offscreen hardware renderer cannot be created."""


_FRAME_CONVERTERS = threading.local()


def frame_rgb(frame) -> np.ndarray:
    """Convert an already validated SDR PyAV frame to full-range uint8 RGB.

    Respect 709, 601 (both AV matrix codes 5/6), FCC and SMPTE240M; do not relabel
    SD footage as 709. Unspecified YUV matrix uses swscale's conventional 601
    fallback, not a fabricated 709 tag. Unspecified ordinary YUV range is TV;
    RGB, gray and legacy yuvj formats default to full range. Explicit range wins.
    Transfer/primaries/HDR policy remains with probe_media: PyAV VideoFrame does
    not expose those tags, and trusted normalized RGB16 SDR must remain usable.
    """
    import av
    pixel_format = frame.format
    rgb_like = pixel_format.is_rgb or len(pixel_format.components) == 1
    matrix_code = int(getattr(frame, 'colorspace', 2) or 0)
    matrices = {0: 'ITU601', 1: 'ITU709', 2: 'ITU601', 4: 'FCC',
                5: 'ITU601', 6: 'ITU601', 7: 'SMPTE240M'}
    if not rgb_like and matrix_code not in matrices:
        raise ValueError(f'GPU 路径不支持视频色彩矩阵 {matrix_code}；请先转为受支持的 SDR。')
    matrix = 'ITU601' if rgb_like else matrices[matrix_code]
    range_code = int(getattr(frame, 'color_range', 0) or 0)
    if range_code not in (0, 1, 2):
        raise ValueError('视频颜色范围无效。')
    if range_code == 1 and pixel_format.name in ('gray', 'gray16le', 'gray16be'):
        # swscale's gray->RGB fast path ignores range flags. Expand legal-range
        # luma explicitly; this is a range conversion, not a transfer curve.
        bits = pixel_format.components[0].bits
        factor = 1 << (bits - 8)
        luma = frame.to_ndarray().astype(np.float64)
        full = np.clip(np.rint((luma - 16 * factor) * (255 / (219 * factor))), 0, 255).astype(np.uint8)
        return np.repeat(full[:, :, None], 3, axis=2)
    full_range = range_code == 2 or (range_code == 0 and (rgb_like or pixel_format.name.startswith('yuvj')))
    source_range = 'JPEG' if full_range else 'MPEG'
    # PyAV 16.0.1 only sets swscale's matrix/range when source and destination
    # differ. For YUV->RGB the destination YUV coefficient table is unused; a
    # distinct table forces configuration even for full->full range. Otherwise
    # full-range yuv420p is silently treated as TV and 709 may use default 601.
    destination_matrix = matrix if rgb_like else ('ITU601' if matrix != 'ITU601' else 'ITU709')
    key = (frame.width, frame.height, pixel_format.name, matrix, source_range)
    converters = getattr(_FRAME_CONVERTERS, 'items', None)
    if converters is None:
        converters = _FRAME_CONVERTERS.items = {}
    if key not in converters:
        if len(converters) >= 8:
            converters.clear()
        converters[key] = av.video.reformatter.VideoReformatter()
    converted = converters[key].reformat(frame, format='rgb24', src_colorspace=matrix,
                                         dst_colorspace=destination_matrix, src_color_range=source_range,
                                         dst_color_range='JPEG')
    return np.ascontiguousarray(converted.to_ndarray())


def _create_context():
    try:
        import moderngl
    except ImportError as error:
        raise GpuUnavailableError('缺少 GPU 依赖 moderngl/glcontext；请安装项目依赖或选择 Blender。') from error
    return moderngl.create_standalone_context(require=330)


def _context_info(context):
    result = {key: str(context.info.get(gl_key, '')) for key, gl_key in
              (('renderer', 'GL_RENDERER'), ('vendor', 'GL_VENDOR'), ('version', 'GL_VERSION'))}
    renderer = result['renderer'].lower()
    if any(name in renderer for name in ('llvmpipe', 'softpipe', 'software rasterizer', 'gdi generic',
                                         'swiftshader', 'swrast', 'swr (', 'basic render driver')):
        raise GpuUnavailableError('当前 OpenGL 使用软件渲染器，无法启用 GPU 加速：' + result['renderer'])
    return result


def probe_gpu() -> dict:
    """Create a real context and read a drawn pixel; never raise on probe failure.

    Run before creating a mapper, or in a dedicated probe process/thread: context
    creation changes the calling thread's current OpenGL context.
    """
    result = dict(available=False, renderer='', vendor='', version='', error='')
    context = None
    try:
        context = _create_context()
        result.update(_context_info(context))
        texture = context.texture((1, 1), 3)
        framebuffer = context.framebuffer([texture])
        framebuffer.use()
        framebuffer.clear(1, 0, 0, 1)
        if framebuffer.read(components=3, alignment=1) != bytes((255, 0, 0)):
            raise GpuUnavailableError('GPU 帧缓冲读回检查失败。')
        framebuffer.release()
        texture.release()
        result['available'] = True
    except Exception as error:
        result['error'] = str(error) or type(error).__name__
    finally:
        if context is not None:
            try:
                context.release()
            except Exception as error:
                result['available'] = False
                if not result['error']:
                    result['error'] = str(error) or type(error).__name__
    return result


_VERTEX_SHADER = '''#version 330
in vec2 atlas;
in vec2 source;
out vec2 uv;
void main() {
    uv = source;
    // OpenGL readback starts at its bottom row. Flip raster placement, leaving
    // SourceUV v-up, so returned bytes are top-down without a CPU array flip.
    gl_Position = vec4(2.0 * atlas.x - 1.0, 1.0 - 2.0 * atlas.y, 0.0, 1.0);
}
'''

_FRAGMENT_SHADER = '''#version 330
uniform sampler2D source_texture;
uniform sampler2D decoration_texture;
uniform bool has_decoration;
uniform float source_aspect;
in vec2 uv;
out vec4 color;

vec3 to_srgb(vec3 x) {
    return mix(12.92 * x, 1.055 * pow(max(x, vec3(0)), vec3(1.0 / 2.4)) - 0.055,
               greaterThan(x, vec3(0.0031308)));
}
vec4 cubic_weights(float f) {
    float a = 1.0 - f;
    return vec4(a*a*a, 3.0*f*f*f - 6.0*f*f + 4.0,
                -3.0*f*f*f + 3.0*f*f + 3.0*f + 1.0, f*f*f) / 6.0;
}
vec4 sample_cubic(sampler2D image, vec2 coordinates) {
    if (any(lessThan(coordinates, vec2(0))) || any(greaterThan(coordinates, vec2(1))))
        return vec4(0);
    ivec2 size = textureSize(image, 0);
    vec2 position = vec2(coordinates.x, 1.0 - coordinates.y) * vec2(size) - 0.5;
    ivec2 base = ivec2(floor(position));
    vec2 f = fract(position);
    vec4 wx = cubic_weights(f.x), wy = cubic_weights(f.y);
    vec4 value = vec4(0);
    // sRGB textures decode RGB to linear light before filtering. Alpha is linear.
    for (int j = 0; j < 4; j++) for (int i = 0; i < 4; i++)
        value += texelFetch(image, clamp(base + ivec2(i-1,j-1), ivec2(0), size-1), 0) * wx[i] * wy[j];
    return value;
}
void main() {
    vec4 source = sample_cubic(source_texture, uv);
    vec3 background = vec3(0);
    if (has_decoration) {
        vec2 decoration_uv = (uv + vec2(0.15 / source_aspect, 0.30))
                             / vec2(1.0 + 0.30 / source_aspect, 1.44);
        vec4 decoration = sample_cubic(decoration_texture, decoration_uv);
        background = decoration.rgb * decoration.a;
    }
    color = vec4(to_srgb(mix(background, source.rgb, source.a)), 1.0);
}
'''


class GpuMapper:
    """Render the shared 512×128 SourceUV mesh to a native or supersampled FBO.

    ``width`` and ``height`` are the actual output raster (e.g. 4320×2160 for 2×).
    Returned arrays are C-contiguous, read-only views over owned immutable bytes.
    Encoding and final downsampling are deliberately outside this class.
    """

    def __init__(self, config, aspect, width, height, decoration_path=None):
        if any(isinstance(v, bool) or not isinstance(v, Integral) or v < 1 for v in (width, height)):
            raise ValueError('GPU 输出宽高必须为正整数。')
        if isinstance(aspect, bool) or not isinstance(aspect, Real) or not math.isfinite(aspect) or aspect <= 0:
            raise ValueError('GPU 源画面宽高比必须为有限正数。')
        mesh = mapping_mesh(config, aspect, columns=512, rows=128)
        self.config = mesh['config']
        self.width, self.height = int(width), int(height)
        self._thread = threading.get_ident()
        self._context = None
        self._closed = False
        self._source = None
        self._source_size = None
        self._decoration = None
        try:
            self._context = _create_context()
            context = self._context
            self.info = _context_info(context)
            maximum = int(context.info['GL_MAX_TEXTURE_SIZE'])
            viewport = context.info.get('GL_MAX_VIEWPORT_DIMS', (maximum, maximum))
            if width > min(maximum, viewport[0]) or height > min(maximum, viewport[1]):
                raise ValueError('输出尺寸超过当前 GPU 的纹理或视口限制。')
            self._maximum_texture_size = maximum
            self._output = context.texture((self.width, self.height), 3, dtype='f1')
            self._framebuffer = context.framebuffer([self._output])
            self._program = context.program(vertex_shader=_VERTEX_SHADER, fragment_shader=_FRAGMENT_SHADER)
            vertices = np.concatenate((np.asarray(mesh['atlas'], dtype='f4'), np.asarray(mesh['uv'], dtype='f4')), axis=1)
            self._vertices = context.buffer(vertices.tobytes())
            self._indices = context.buffer(np.asarray(mesh['triangles'], dtype='i4').tobytes())
            self._array = context.vertex_array(self._program, [(self._vertices, '2f 2f', 'atlas', 'source')], self._indices)
            self._program['source_texture'].value = 0
            self._program['decoration_texture'].value = 1
            self._program['has_decoration'].value = decoration_path is not None
            self._program['source_aspect'].value = float(aspect)
            if decoration_path is not None:
                from PIL import Image
                with Image.open(decoration_path) as image:
                    image = image.convert('RGBA')
                    if max(image.size) > maximum:
                        raise ValueError('装饰图片超过当前 GPU 的纹理限制。')
                    self._decoration = context.texture(image.size, 4, image.tobytes(), dtype='f1',
                                                       internal_format=0x8C43)  # GL_SRGB8_ALPHA8
            else:
                # A complete dummy texture also avoids undefined unused sampler state.
                self._decoration = context.texture((1, 1), 4, bytes(4), dtype='f1', internal_format=0x8C43)
        except Exception as error:
            if self._context is not None:
                self._context.release()
            self._closed = True
            if isinstance(error, (ValueError, FileNotFoundError)):
                raise
            raise GpuUnavailableError('无法建立 GPU 渲染器；可改用 Blender。详细原因：' + str(error)) from error

    def _check_open(self):
        if self._closed:
            raise RuntimeError('GPU 渲染器已关闭。')
        if threading.get_ident() != self._thread:
            raise RuntimeError('GPU 渲染器必须在创建它的同一线程使用。')

    def render(self, rgb_array) -> np.ndarray:
        self._check_open()
        if (not isinstance(rgb_array, np.ndarray) or rgb_array.dtype != np.uint8
                or rgb_array.ndim != 3 or rgb_array.shape[2] != 3
                or min(rgb_array.shape[:2]) < 1):
            raise ValueError('GPU 输入必须是非空的 uint8 RGB H×W×3 数组。')
        size = (rgb_array.shape[1], rgb_array.shape[0])
        if max(size) > self._maximum_texture_size:
            raise ValueError('源画面超过当前 GPU 的纹理限制。')
        if size != self._source_size:
            if self._source is not None:
                self._source.release()
            self._source = self._context.texture(size, 3, dtype='f1', internal_format=0x8C41)  # GL_SRGB8
            self._source_size = size
        self._source.write(np.ascontiguousarray(rgb_array).tobytes(), alignment=1)
        self._source.use(0)
        self._decoration.use(1)
        self._framebuffer.use()
        self._context.viewport = (0, 0, self.width, self.height)
        self._array.render()
        data = self._framebuffer.read(components=3, alignment=1)
        return np.frombuffer(data, dtype=np.uint8).reshape(self.height, self.width, 3)

    def close(self):
        if self._closed:
            return
        self._check_open()
        self._closed = True
        self._context.release()

    def __enter__(self):
        self._check_open()
        return self

    def __exit__(self, *exception):
        self.close()
