"""轉蛋結果圖(Pillow)：卡片排成格子，角色圖示、名字、稀有度色框與星數，SSR 有光暈。"""
import io
import math

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .gacha_logic import RARITY_NAME, STARS

CARD_W, CARD_H, GAP, PAD, COLS = 150, 210, 12, 20, 5
BG = (24, 26, 36)
COLORS = {'N': (150, 156, 168), 'R': (70, 150, 235), 'SR': (170, 90, 230), 'SSR': (255, 200, 40)}
FONT_PATH = '/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc'
_fonts: dict = {}


def _font(size: int):
    if size in _fonts:
        return _fonts[size]
    f = None
    for i in range(8):                       # ttc 裡找繁中 TC
        try:
            c = ImageFont.truetype(FONT_PATH, size, index=i)
        except Exception:
            break
        if f is None:
            f = c
        if 'TC' in ' '.join(c.getname()):
            f = c
            break
    _fonts[size] = f or ImageFont.load_default()
    return _fonts[size]


def _fit(draw, text: str, font, width: int) -> str:
    if draw.textlength(text, font=font) <= width:
        return text
    while text and draw.textlength(text + '…', font=font) > width:
        text = text[:-1]
    return text + '…'


def _star(draw, cx, cy, r, fill):
    pts = []
    for i in range(10):
        a = -math.pi / 2 + i * math.pi / 5
        rr = r if i % 2 == 0 else r * 0.45
        pts.append((cx + rr * math.cos(a), cy + rr * math.sin(a)))
    draw.polygon(pts, fill=fill)


def _card(c: dict) -> Image.Image:
    r = c['rarity']
    col = COLORS.get(r, COLORS['N'])
    im = Image.new('RGBA', (CARD_W, CARD_H), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    top = tuple(int(x * 0.35 + 20) for x in col)
    d.rounded_rectangle((0, 0, CARD_W - 1, CARD_H - 1), 14, fill=top + (255,), outline=col,
                        width=5 if r == 'SSR' else 4)
    box = (14, 18, CARD_W - 14, 18 + CARD_W - 28)
    d.rounded_rectangle(box, 10, fill=(15, 16, 24, 255))
    icon = c.get('icon')
    ok = False
    if icon:
        try:
            src = Image.open(io.BytesIO(icon) if isinstance(icon, (bytes, bytearray)) else icon).convert('RGBA')
            side = box[2] - box[0]
            src = src.resize((side, side), Image.LANCZOS)
            mask = Image.new('L', (side, side), 0)
            ImageDraw.Draw(mask).rounded_rectangle((0, 0, side - 1, side - 1), 10, fill=255)
            im.paste(src, (box[0], box[1]), mask)
            ok = True
        except Exception:
            ok = False
    if not ok:
        ch = (c.get('name') or '?')[:1]
        d.text(((box[0] + box[2]) / 2, (box[1] + box[3]) / 2), ch, font=_font(54), fill=col, anchor='mm')
    d.text((CARD_W / 2, 150), _fit(d, c.get('name') or '?', _font(18), CARD_W - 16), font=_font(18),
           fill=(255, 255, 255), anchor='mm')
    n = STARS.get(r, 1)
    x0 = CARD_W / 2 - (n - 1) * 9
    for i in range(n):
        _star(d, x0 + i * 18, 174, 8, col)
    d.text((CARD_W / 2, 194), RARITY_NAME.get(r, r), font=_font(13), fill=col, anchor='mm')
    if c.get('new'):
        d.rounded_rectangle((8, 8, 52, 28), 6, fill=(230, 50, 70, 255))
        d.text((30, 18), 'NEW', font=_font(13), fill=(255, 255, 255), anchor='mm')
    return im


def render_pulls(cards: list, cols: int = COLS) -> bytes:
    """cards: [{'name','rarity','icon'(路徑/bytes/None),'new'}] → PNG bytes。"""
    n = max(1, len(cards))
    cols = min(cols, n)
    rows = (n + cols - 1) // cols
    w = PAD * 2 + cols * CARD_W + (cols - 1) * GAP
    h = PAD * 2 + rows * CARD_H + (rows - 1) * GAP
    base = Image.new('RGBA', (w, h), BG + (255,))
    glow = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    pos = [(PAD + (i % cols) * (CARD_W + GAP), PAD + (i // cols) * (CARD_H + GAP)) for i in range(len(cards))]
    for (x, y), c in zip(pos, cards):
        if c['rarity'] == 'SSR':
            ImageDraw.Draw(glow).rounded_rectangle((x - 6, y - 6, x + CARD_W + 6, y + CARD_H + 6), 20,
                                                   fill=COLORS['SSR'] + (200,))
    if glow.getbbox():
        base = Image.alpha_composite(base, glow.filter(ImageFilter.GaussianBlur(10)))
    for (x, y), c in zip(pos, cards):
        base.alpha_composite(_card(c), (x, y))
        if c['rarity'] == 'SSR':                       # 小閃光
            d = ImageDraw.Draw(base)
            for dx, dy, r in ((CARD_W - 22, 30, 9), (24, 120, 6), (CARD_W - 30, 130, 5)):
                _star(d, x + dx, y + dy, r, (255, 250, 200, 255))
    out = io.BytesIO()
    base.convert('RGB').save(out, 'PNG')
    return out.getvalue()
