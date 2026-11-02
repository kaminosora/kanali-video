"""Manual calibration assets and observer-only fold-axis hypotheses."""
from pathlib import Path


def axis_preview_response(config, source_aspect, request):
    if request is None:
        return None
    if not isinstance(request, dict) or 'actual_bend_center_u' not in request:
        raise ValueError('axis_preview requires an actual_bend_center_u number')
    from .geometry import axis_preview_mesh
    from .output import WIDTH
    result = axis_preview_mesh(config, source_aspect, request['actual_bend_center_u'])
    result.update(delta_pixels=result['delta_u'] * WIDTH,
                  actual_center_px=result['actual_bend_center_u'] * WIDTH,
                  render_center_px=result['render_bend_center_u'] * WIDTH)
    return result


def create_parallel_lines(path):
    """Dense native-canvas straight lines for observing physical S-bend errors."""
    from PIL import Image, ImageDraw, ImageFont
    from .output import WIDTH, HEIGHT
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError(f'不会覆盖已有文件：{path}')
    image = Image.new('RGB', (WIDTH, HEIGHT), (28, 32, 38))
    draw = ImageDraw.Draw(image)
    small = ImageFont.load_default(size=20)
    label = ImageFont.load_default(size=29)
    # Horizontal families are particularly sensitive to axis mismatch when the
    # observer is above the screen. Keep the central region clear of labels.
    for y in range(72, HEIGHT, 72):
        draw.line((0,y,WIDTH-1,y),fill=(225,230,235),width=4)
        draw.text((12,y+7),f'Y {y}',fill=(185,195,205),font=small)
    for x in range(144,WIDTH,144):
        draw.line((x,0,x,HEIGHT-1),fill=(90,130,150),width=2)
        draw.text((x+5,112),f'{x}',fill=(185,195,205),font=small)
    # Offset diagonals expose a different line family without covering the
    # central horizontal reference used by automated and visual validation.
    for x in (-1080,-540,0,540,1080,1620,2160):
        draw.line((x,HEIGHT-1,x+WIDTH,0),fill=(100,90,70),width=2)
    # Restore horizontal references on top of diagonal intersections.
    for y in range(72,HEIGHT,72):
        draw.line((150,y,WIDTH-151,y),fill=(225,230,235),width=4)
    draw.line((WIDTH//2,0,WIDTH//2,HEIGHT-1),fill=(80,220,210),width=3)
    draw.rectangle((2,2,WIDTH-3,HEIGHT-3),outline=(230,230,230),width=4)
    draw.rectangle((18,18,685,62),fill=(28,32,38))
    draw.text((28,24),'LEFT  |  PARALLEL LINE CALIBRATION',fill=(255,145,120),font=label)
    draw.rectangle((WIDTH-410,HEIGHT-61,WIDTH-18,HEIGHT-18),fill=(28,32,38))
    draw.text((WIDTH-395,HEIGHT-53),'RIGHT  |  2160 x 1080',fill=(120,220,180),font=label)
    image.save(path,'PNG')
    image.close()
    return str(path)
