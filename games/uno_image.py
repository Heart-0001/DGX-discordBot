"""
Authentic UNO card renderer (Pillow).

Real UNO card face (NOT the back — the front has no "UNO" wordmark):
  - Solid colored rounded card with a thin white border.
  - A white ellipse tilted ~20° across the centre.
  - A large central number/symbol (card colour) sitting upright on the oval.
  - Small white number/symbol in the top-left corner and a 180°-rotated copy
    in the bottom-right corner.
  - Wild cards: black card, the oval is split into the four colours.
  - Wild Draw Four: same four-colour oval with a white "+4" on top.

Skip (⊘) and Reverse (⇅) are drawn as primitives so we don't depend on glyph
coverage; numbers / +2 / +4 use DejaVuSans-Bold.
"""
from __future__ import annotations

import io
import math
from PIL import Image, ImageDraw, ImageFont

from .uno_logic import Card, Color, Action

# ── Dimensions (base size; render_hand rescales) ────────────────────────
CARD_W, CARD_H = 200, 300
GAP = 10
LIFT = 18
PAD = 28
CORNER_RADIUS = 22

# ── Colours ─────────────────────────────────────────────────────────────
FELT = (28, 82, 50)
WHITE = (255, 255, 255)
BLACK = (0, 0, 0)

UNO_COLORS = {
    Color.RED:    (213, 32, 38),
    Color.YELLOW: (247, 196, 28),
    Color.GREEN:  (39, 156, 73),
    Color.BLUE:   (12, 100, 192),
    Color.WILD:   (24, 24, 24),
}
# Darker shade for outlines / borders
DARKER = {
    Color.RED:    (150, 18, 22),
    Color.YELLOW: (180, 130, 0),
    Color.GREEN:  (22, 105, 46),
    Color.BLUE:   (8, 64, 130),
    Color.WILD:   (0, 0, 0),
}
# Central numeral fill (yellow gets a gold so it reads on white)
CENTER_FILL = dict(UNO_COLORS)
CENTER_FILL[Color.YELLOW] = (224, 170, 0)

QUAD_RED, QUAD_YELLOW, QUAD_GREEN, QUAD_BLUE = (
    UNO_COLORS[Color.RED], UNO_COLORS[Color.YELLOW],
    UNO_COLORS[Color.GREEN], UNO_COLORS[Color.BLUE],
)

OVAL_TILT = 20  # degrees

# ── Fonts ───────────────────────────────────────────────────────────────
_FONT_PATHS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "DejaVuSans-Bold.ttf",
    "arialbd.ttf",
]
_font_cache: dict[int, ImageFont.FreeTypeFont] = {}


def _font(size: int) -> ImageFont.FreeTypeFont:
    size = int(size)
    if size in _font_cache:
        return _font_cache[size]
    for path in _FONT_PATHS:
        try:
            f = ImageFont.truetype(path, size)
            _font_cache[size] = f
            return f
        except Exception:
            continue
    f = ImageFont.load_default()
    _font_cache[size] = f
    return f


# ── Glyph drawing (numbers / +2 / +4 / skip / reverse) ──────────────────

def _draw_skip(d: ImageDraw.ImageDraw, center, size, color, width):
    cx, cy = center
    r = size * 0.5
    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=color, width=width)
    a = math.radians(45)
    dx, dy = r * math.cos(a), r * math.sin(a)
    d.line([cx - dx, cy - dy, cx + dx, cy + dy], fill=color, width=width)


def _draw_reverse(d: ImageDraw.ImageDraw, center, size, color):
    """Two parallel arrows pointing opposite ways (↑↓) — the reverse icon."""
    cx, cy = center
    r = size * 0.52
    w = max(3, int(size * 0.13))
    hw = size * 0.17   # arrowhead half-width
    hh = size * 0.24   # arrowhead height
    off = size * 0.24  # horizontal offset of each shaft
    # Left arrow: pointing UP
    lx = cx - off
    d.line([lx, cy + r, lx, cy - r + hh * 0.5], fill=color, width=w)
    d.polygon([(lx - hw, cy - r + hh), (lx + hw, cy - r + hh), (lx, cy - r)], fill=color)
    # Right arrow: pointing DOWN
    rx = cx + off
    d.line([rx, cy - r, rx, cy + r - hh * 0.5], fill=color, width=w)
    d.polygon([(rx - hw, cy + r - hh), (rx + hw, cy + r - hh), (rx, cy + r)], fill=color)


def _draw_quad_circle(d: ImageDraw.ImageDraw, center, size):
    """Small four-colour circle (used on Wild corners)."""
    cx, cy = center
    r = size * 0.5
    box = [cx - r, cy - r, cx + r, cy + r]
    d.pieslice(box, 180, 270, fill=QUAD_RED)
    d.pieslice(box, 270, 360, fill=QUAD_YELLOW)
    d.pieslice(box, 90, 180, fill=QUAD_GREEN)
    d.pieslice(box, 0, 90, fill=QUAD_BLUE)


def _draw_glyph(d: ImageDraw.ImageDraw, card: Card, center, size,
                fill, stroke_fill=None, stroke_w=0, max_w=None):
    """Render a card's value at *center* with nominal height *size*.
    If *max_w* is given, the text is shrunk to fit that width."""
    val = card.value
    if val == Action.SKIP:
        _draw_skip(d, center, size, fill, max(3, int(size * 0.13)))
    elif val == Action.REVERSE:
        _draw_reverse(d, center, size, fill)
    else:
        if val == Action.DRAW_TWO:
            text = "+2"
        elif val == Action.WILD_DRAW_FOUR:
            text = "+4"
        elif val == Action.WILD:
            text = ""
        else:
            text = str(val)
        if not text:
            return
        fsize = size * 1.18
        font = _font(fsize)
        if max_w:
            try:
                w = font.getlength(text)
            except Exception:
                w = font.getbbox(text)[2]
            if w > max_w:
                fsize = max(10, fsize * max_w / w)
                font = _font(fsize)
        d.text(center, text, font=font, fill=fill, anchor="mm",
               stroke_width=stroke_w, stroke_fill=stroke_fill)


# ── Oval (tilted white ellipse, four-colour for wild) ───────────────────

def _oval_layer(card: Card) -> Image.Image:
    layer = Image.new("RGBA", (CARD_W, CARD_H), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    ow, oh = int(CARD_W * 0.70), int(CARD_H * 0.58)
    cx, cy = CARD_W // 2, CARD_H // 2
    box = [cx - ow // 2, cy - oh // 2, cx + ow // 2, cy + oh // 2]
    d.ellipse(box, fill=WHITE)

    if card.is_wild():
        quad = Image.new("RGBA", (CARD_W, CARD_H), (0, 0, 0, 0))
        qd = ImageDraw.Draw(quad)
        x0, y0, x1, y1 = box
        mx, my = (x0 + x1) // 2, (y0 + y1) // 2
        qd.rectangle([x0, y0, mx, my], fill=QUAD_RED)
        qd.rectangle([mx, y0, x1, my], fill=QUAD_YELLOW)
        qd.rectangle([x0, my, mx, y1], fill=QUAD_GREEN)
        qd.rectangle([mx, my, x1, y1], fill=QUAD_BLUE)
        emask = Image.new("L", (CARD_W, CARD_H), 0)
        ImageDraw.Draw(emask).ellipse(box, fill=255)
        layer.paste(quad, (0, 0), emask)

    return layer.rotate(OVAL_TILT, resample=Image.BICUBIC, expand=False)


# ── Corner indicator (small glyph, top-left; rotated for bottom-right) ──

def _corner_layer(card: Card, flip: bool) -> Image.Image:
    layer = Image.new("RGBA", (CARD_W, CARD_H), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    size = CARD_W * 0.20
    center = (int(CARD_W * 0.20), int(CARD_H * 0.135))
    if card.value == Action.WILD:
        _draw_quad_circle(d, center, size)
    else:
        # white glyph with a thin dark outline so it reads on any colour
        _draw_glyph(d, card, center, size, fill=WHITE,
                    stroke_fill=DARKER.get(card.color, BLACK), stroke_w=2,
                    max_w=CARD_W * 0.30)
    if flip:
        layer = layer.rotate(180, expand=False)
    return layer


# ── Full card ───────────────────────────────────────────────────────────

def _make_card_img(card: Card) -> Image.Image:
    # White card with a coloured inner face (= white border)
    img = Image.new("RGB", (CARD_W, CARD_H), WHITE)
    d = ImageDraw.Draw(img)
    border = 8
    d.rounded_rectangle([border, border, CARD_W - border, CARD_H - border],
                        radius=CORNER_RADIUS, fill=UNO_COLORS[card.color])

    # Tilted oval
    oval = _oval_layer(card)
    img.paste(oval, (0, 0), oval)
    d = ImageDraw.Draw(img)

    # Central symbol (upright)
    center = (CARD_W // 2, CARD_H // 2)
    if card.value == Action.WILD_DRAW_FOUR:
        _draw_glyph(d, card, center, CARD_H * 0.30, fill=WHITE,
                    stroke_fill=BLACK, stroke_w=4, max_w=CARD_W * 0.60)
    elif card.value == Action.WILD:
        pass  # four-colour oval is the design
    else:
        _draw_glyph(d, card, center, CARD_H * 0.34,
                    fill=CENTER_FILL[card.color],
                    stroke_fill=DARKER[card.color], stroke_w=3,
                    max_w=CARD_W * 0.60)

    # Corners
    tl = _corner_layer(card, flip=False)
    img.paste(tl, (0, 0), tl)
    br = _corner_layer(card, flip=True)
    img.paste(br, (0, 0), br)
    return img


# ── Hand layout ─────────────────────────────────────────────────────────

def render_hand(cards: list, selected: set, height: int = 512) -> bytes:
    """Render a hand as a PNG, cards fanned horizontally, auto-scaled to fit."""
    n = len(cards)
    if n == 0:
        img = Image.new("RGB", (320, 300), FELT)
        d = ImageDraw.Draw(img)
        d.text((160, 150), "No Cards", fill=(210, 210, 210),
               font=_font(44), anchor="mm")
        buf = io.BytesIO()
        img.save(buf, "PNG")
        return buf.getvalue()

    max_w = 1920
    avail = max_w - PAD * 2
    card_w = max(96, min(CARD_W, int((avail - GAP * (n - 1)) / n)))
    card_h = int(card_w * CARD_H / CARD_W)
    gap = max(4, int(GAP * card_w / CARD_W))
    cr = max(8, int(CORNER_RADIUS * card_w / CARD_W))
    lift = max(8, int(LIFT * card_h / CARD_H))

    actual_total = card_w * n + gap * (n - 1)
    total_w = PAD * 2 + actual_total
    total_h = PAD * 2 + card_h + lift
    canvas = Image.new("RGB", (total_w, total_h), FELT)

    start_x = PAD
    for i, card in enumerate(cards):
        sel = i in selected
        x = start_x + i * (card_w + gap)
        y = PAD + (0 if sel else lift)

        card_img = _make_card_img(card).resize((card_w, card_h), Image.LANCZOS)

        # Drop shadow
        shadow = Image.new("RGBA", (card_w + 8, card_h + 8), (0, 0, 0, 0))
        ImageDraw.Draw(shadow).rounded_rectangle(
            [4, 6, card_w + 4, card_h + 6], radius=cr, fill=(0, 0, 0, 90))
        canvas.paste(shadow, (x - 2, y), shadow)

        canvas.paste(card_img, (x, y))

        if sel:
            ring = Image.new("RGBA", (card_w + 10, card_h + 10), (0, 0, 0, 0))
            ImageDraw.Draw(ring).rounded_rectangle(
                [0, 0, card_w + 9, card_h + 9], radius=cr + 4,
                outline=(255, 238, 90, 255), width=5)
            canvas.paste(ring, (x - 5, y - 5), ring)

    buf = io.BytesIO()
    canvas.save(buf, "PNG")
    buf.seek(0)
    return buf.getvalue()
