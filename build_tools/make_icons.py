"""Draws icons/music_studio.ico and icons/simple_chat.ico in the style of the other WorkflowPad icons (rounded square, soft gradient, white glyph, small sparkle).

    python build_tools/make_icons.py

Only Pillow is needed. Drawn at 1024 px and scaled down, so the edges stay clean at 16-256 px.
"""
import math
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
N = 1024
SIZES = [(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]


def gradient(top, bottom) -> Image.Image:
    g = Image.new("RGBA", (N, N))
    px = g.load()
    for y in range(N):
        t = y / (N - 1)
        c = tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3)) + (255,)
        for x in range(N):
            px[x, y] = c
    return g


def tile(top, bottom) -> Image.Image:
    """Rounded square with a vertical gradient and a thin lighter rim."""
    m = Image.new("L", (N, N), 0)
    ImageDraw.Draw(m).rounded_rectangle((40, 40, N - 40, N - 40), radius=230, fill=255)
    img = Image.new("RGBA", (N, N), (0, 0, 0, 0))
    img.paste(gradient(top, bottom), (0, 0), m)
    rim = Image.new("L", (N, N), 0)
    ImageDraw.Draw(rim).rounded_rectangle((40, 40, N - 40, N - 40), radius=230, outline=255, width=10)
    light = Image.new("RGBA", (N, N), (255, 255, 255, 70))
    img.paste(light, (0, 0), ImageChops.multiply(rim, m))
    return img


def sparkle(draw: ImageDraw.ImageDraw, cx, cy, r, fill, outline=None):
    """Four-point star: long tips, short inner corners."""
    pts = []
    for i in range(8):
        a = math.pi / 4 * i
        rr = r if i % 2 == 0 else r * 0.26
        pts.append((cx + rr * math.sin(a), cy - rr * math.cos(a)))
    draw.polygon(pts, fill=fill, outline=outline)


def shadow(layer: Image.Image, dy=14, blur=18, alpha=70) -> Image.Image:
    a = layer.split()[3].point(lambda v: v * alpha // 255)
    sh = Image.new("RGBA", layer.size, (0, 0, 0, 0))
    sh.putalpha(a)
    sh = sh.filter(ImageFilter.GaussianBlur(blur))
    out = Image.new("RGBA", layer.size, (0, 0, 0, 0))
    out.paste(sh, (0, dy), sh)
    return out


def music_studio() -> Image.Image:
    base = tile((52, 124, 255), (22, 46, 140))
    g = Image.new("RGBA", (N, N), (0, 0, 0, 0))
    d = ImageDraw.Draw(g)
    white = (255, 255, 255, 255)
    # two beamed eighth notes
    hx1, hx2, hy = 330, 640, 700
    for hx in (hx1, hx2):
        d.ellipse((hx - 105, hy - 78, hx + 105, hy + 78), fill=white)
    sx1, sx2 = hx1 + 88, hx2 + 88
    d.rectangle((sx1 - 22, 330, sx1 + 22, hy), fill=white)
    d.rectangle((sx2 - 22, 275, sx2 + 22, hy), fill=white)
    d.polygon([(sx1 - 22, 330), (sx2 + 22, 275), (sx2 + 22, 405), (sx1 - 22, 460)], fill=white)
    base.alpha_composite(shadow(g))
    base.alpha_composite(g)
    s = Image.new("RGBA", (N, N), (0, 0, 0, 0))
    sd = ImageDraw.Draw(s)
    sparkle(sd, 790, 300, 120, (140, 215, 255, 255))
    sparkle(sd, 880, 450, 52, (140, 215, 255, 255))
    base.alpha_composite(shadow(s, 10, 12, 60))
    base.alpha_composite(s)
    return base


def simple_chat() -> Image.Image:
    base = tile((124, 92, 255), (60, 36, 150))
    g = Image.new("RGBA", (N, N), (0, 0, 0, 0))
    d = ImageDraw.Draw(g)
    white = (255, 255, 255, 255)
    d.rounded_rectangle((190, 250, 770, 650), radius=120, fill=white)
    d.polygon([(300, 640), (300, 790), (450, 640)], fill=white)                       # the bubble's tail
    for cx in (350, 480, 610):
        d.ellipse((cx - 38, 420, cx + 38, 496), fill=(92, 66, 200, 255))              # three dots
    base.alpha_composite(shadow(g))
    base.alpha_composite(g)
    s = Image.new("RGBA", (N, N), (0, 0, 0, 0))
    sd = ImageDraw.Draw(s)
    sparkle(sd, 790, 300, 125, (255, 214, 120, 255))
    sparkle(sd, 880, 470, 52, (255, 214, 120, 255))
    base.alpha_composite(shadow(s, 10, 12, 60))
    base.alpha_composite(s)
    return base


def main():
    for name, fn in (("music_studio", music_studio), ("simple_chat", simple_chat)):
        img = fn()
        out = ROOT / "icons" / f"{name}.ico"
        img.resize((256, 256), Image.LANCZOS).save(out, sizes=SIZES)
        print("wrote", out)


if __name__ == "__main__":
    main()
