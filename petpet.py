"""Head-pat GIF, same hand sprite + frame offsets as petpetgen.com."""
import io
import math
import sys
from pathlib import Path

from PIL import Image, ImageSequence

HAND = Path(__file__).parent / "assets" / "petpet-hand.png"  # 5 frames of 112x112
SIZE = 112
STEP = 60           # ms per frame, petpetgen default
MAX_FRAMES = 100    # 6 s cap keeps uploads small

# petpetgen defaults
SQUISH, SCALE = 1.25, 0.875
SPRITE_X, SPRITE_Y, SPRITE_W = 14, 20, 112
OFFSETS = [(0, 0, 0, 0), (-4, 12, 4, -12), (-12, 18, 12, -18), (-8, 12, 4, -12), (-4, 0, 0, 0)]


def _layout(frame: int) -> tuple[int, int, int, int, int]:
    ox, oy, ow, oh = OFFSETS[frame]
    dx = int(SPRITE_X + ox * SQUISH * 0.4)
    dy = int(SPRITE_Y + oy * SQUISH * 0.9)
    dw = int((SPRITE_W + ow * SQUISH) * SCALE)
    dh = int((SPRITE_W + oh * SQUISH) * SCALE)
    hand_y = max(0, int(dy * 0.75 - SPRITE_Y - 0.5))
    return dx, dy, dw, dh, hand_y


def _frames(src: Image.Image) -> tuple[list[Image.Image], list[int]]:
    frames, durs = [], []
    for f in ImageSequence.Iterator(src):
        im = f.convert("RGBA")
        side = min(im.size)
        # tall images crop near the top so her head stays in
        left, top = (im.width - side) // 2, (im.height - side) // 4
        frames.append(im.crop((left, top, left + side, top + side)).resize((SIZE * 2, SIZE * 2), Image.LANCZOS))
        durs.append(max(20, f.info.get("duration") or 100))
    return frames, durs


def _quantize(im: Image.Image) -> Image.Image:
    # 255 colours + index 255 reserved for transparent
    p = im.convert("RGB").quantize(255, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
    p.putpalette(p.getpalette()[: 255 * 3] + [0, 0, 0])
    p.paste(255, mask=im.getchannel("A").point(lambda a: 255 if a < 128 else 0))
    return p


def petpet(data: bytes) -> bytes:
    frames, durs = _frames(Image.open(io.BytesIO(data)))
    sheet = Image.open(HAND).convert("RGBA")
    hands = [sheet.crop((i * SIZE, 0, (i + 1) * SIZE, SIZE)) for i in range(5)]
    total = sum(durs)
    starts = [sum(durs[:i]) for i in range(len(durs))]
    # whole pat cycles, long enough to play the source once
    n = min(math.ceil(max(total, STEP * 5) / (STEP * 5)) * 5, MAX_FRAMES)
    out = []
    for i in range(n):
        t = (i * STEP) % total
        src = frames[max(j for j, s in enumerate(starts) if s <= t)]
        dx, dy, dw, dh, hand_y = _layout(i % 5)
        canvas = Image.new("RGBA", (SIZE, SIZE))
        canvas.alpha_composite(src.resize((dw, dh), Image.LANCZOS), (dx, dy))
        canvas.alpha_composite(hands[i % 5], (0, hand_y))
        out.append(_quantize(canvas))
    buf = io.BytesIO()
    out[0].save(buf, "GIF", save_all=True, append_images=out[1:], duration=STEP,
                loop=0, disposal=2, transparency=255, optimize=False)
    return buf.getvalue()


if __name__ == "__main__":
    # python petpet.py in.gif out.gif
    with open(sys.argv[1], "rb") as f:
        result = petpet(f.read())
    with open(sys.argv[2], "wb") as f:
        f.write(result)
