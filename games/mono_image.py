"""大富翁棋盤圖(Pillow)：9×9 的外圈，32 格；中間放賽季資訊與淨值前 5 名。"""
import io
import math
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFont

from games import mono_logic as ml

CELL = 100
PAD = 12
SIZE = CELL * 9 + PAD * 2
FONT_PATHS = ['/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc',
              'C:/Windows/Fonts/msjhbd.ttc', 'C:/Windows/Fonts/msjh.ttc']

GROUP_COLORS = [(150, 90, 50), (90, 190, 230), (200, 80, 170), (240, 150, 40),
                (220, 60, 60), (240, 210, 50), (60, 160, 90)]
SPECIAL = {'start': ((90, 190, 110), '起點'), 'jail': ((120, 120, 130), '停權中'),
           'rest': ((110, 200, 190), '泉水休息'), 'report': ((200, 70, 70), '被檢舉'),
           'chance': ((150, 110, 210), '海克斯機會'), 'tax': ((160, 160, 120), ''),
           'portal': ((60, 130, 220), '傳送門')}
PLAYER_COLORS = [(231, 76, 60), (52, 152, 219), (46, 204, 113), (241, 196, 15),
                 (155, 89, 182), (230, 126, 34), (26, 188, 156), (236, 120, 190)]
BG = (245, 240, 228)


@lru_cache(maxsize=32)
def _font(size: int):
    for path in FONT_PATHS:
        best = None
        for idx in range(8):
            try:
                f = ImageFont.truetype(path, size, index=idx)
            except OSError:
                break
            best = best or f
            if 'TC' in (f.getname()[0] or ''):
                return f
        if best:
            return best
    try:
        return ImageFont.load_default(size)
    except TypeError:
        return ImageFont.load_default()


def player_color(season: dict, uid: int) -> tuple:
    keys = list(season['players'])
    i = keys.index(str(uid)) if str(uid) in keys else 0
    return PLAYER_COLORS[i % len(PLAYER_COLORS)]


def cell_xy(i: int) -> tuple[int, int]:
    """格子序號 → 在 9×9 外圈的 (欄, 列)；0 在右下，順時針(往左、往上、往右、往下)。"""
    if i <= 8:
        return 8 - i, 8
    if i <= 16:
        return 0, 8 - (i - 8)
    if i <= 24:
        return i - 16, 0
    return 8, i - 24


def _fit(draw, text, max_w, size, min_size=11):
    while size > min_size and draw.textlength(text, font=_font(size)) > max_w:
        size -= 1
    return _font(size)


def _center(draw, cx, y, text, font, fill):
    w = draw.textlength(text, font=font)
    draw.text((cx - w / 2, y), text, font=font, fill=fill)


def _star(draw, cx, cy, r, fill):
    pts = []
    for k in range(10):
        a = -math.pi / 2 + k * math.pi / 5
        rad = r if k % 2 == 0 else r * 0.45
        pts.append((cx + rad * math.cos(a), cy + rad * math.sin(a)))
    draw.polygon(pts, fill=fill, outline=(90, 70, 0))


def _short(name: str) -> str:
    return (name or '?')[:1]


def render_board(season: dict, names: dict, days_left: float) -> bytes:
    """畫棋盤；names: {uid(int 或字串): 顯示名稱}。回傳 PNG bytes。"""
    names = {str(k): v for k, v in names.items()}
    img = Image.new('RGB', (SIZE, SIZE), BG)
    d = ImageDraw.Draw(img)

    for t in ml.TILES:
        cx_, cy_ = cell_xy(t['i'])
        x0, y0 = PAD + cx_ * CELL, PAD + cy_ * CELL
        x1, y1 = x0 + CELL, y0 + CELL
        d.rectangle([x0, y0, x1, y1], fill=(252, 250, 244), outline=(60, 50, 40), width=2)
        mid = (x0 + x1) / 2
        if t['type'] == 'land':
            band = GROUP_COLORS[t['group']]
            d.rectangle([x0 + 2, y0 + 2, x1 - 2, y0 + 26], fill=band)
            o = season['owners'].get(str(t['i']))
            if o:
                col = player_color(season, o['owner'])
                d.ellipse([x1 - 22, y0 + 5, x1 - 6, y0 + 21], fill=col, outline=(255, 255, 255), width=2)
                for s in range(o['level']):
                    _star(d, x0 + 14 + s * 17, y0 + 14, 7, (255, 225, 60))
            _center(d, mid, y0 + 30, t['name'], _fit(d, t['name'], CELL - 8, 18), (30, 30, 30))
            _center(d, mid, y0 + 54, f"${t['price']}", _font(16), (80, 60, 30))
        else:
            col, label = SPECIAL[t['type']]
            d.rectangle([x0 + 2, y0 + 2, x1 - 2, y0 + 26], fill=col)
            label = label or t['name']
            _center(d, mid, y0 + 30, label, _fit(d, label, CELL - 8, 18), (30, 30, 30))
            if t['type'] == 'start':
                _center(d, mid, y0 + 54, f'領 ${ml.PASS_START}', _font(15), (30, 100, 50))
            elif t['type'] == 'tax':
                _center(d, mid, y0 + 54, f"-${t['price']}", _font(16), (150, 40, 40))
            elif t['type'] == 'jail':
                _center(d, mid, y0 + 54, '探監', _font(15), (80, 80, 80))

    # 棋子
    by_pos: dict[int, list] = {}
    for k, p in season['players'].items():
        by_pos.setdefault(p['pos'], []).append(k)
    for pos, ks in by_pos.items():
        cx_, cy_ = cell_xy(pos)
        x0, y0 = PAD + cx_ * CELL, PAD + cy_ * CELL
        for n, k in enumerate(ks[:8]):
            r = 11
            px = x0 + 14 + (n % 4) * 24
            py = y0 + 74 if len(ks) <= 4 else y0 + 66 + (n // 4) * 26
            col = PLAYER_COLORS[list(season['players']).index(k) % len(PLAYER_COLORS)]
            d.ellipse([px - r, py - r, px + r, py + r], fill=col, outline=(255, 255, 255), width=2)
            f = _font(14)
            ch = _short(names.get(k, '?'))
            w = d.textlength(ch, font=f)
            d.text((px - w / 2, py - 9), ch, font=f, fill=(255, 255, 255))

    # 中間
    cx0, cy0 = PAD + CELL, PAD + CELL
    cx1, cy1 = PAD + CELL * 8, PAD + CELL * 8
    d.rectangle([cx0 + 6, cy0 + 6, cx1 - 6, cy1 - 6], fill=(232, 226, 208), outline=(60, 50, 40), width=2)
    mx = (cx0 + cx1) / 2
    _center(d, mx, cy0 + 40, '海克斯大富翁', _font(44), (60, 40, 20))
    _center(d, mx, cy0 + 105, f"{ml.season_name(season)}・剩餘 {max(0, days_left):.1f} 天", _font(28), (90, 60, 30))
    _center(d, mx, cy0 + 170, '淨值排行 TOP 5', _font(26), (60, 40, 20))
    for n, (uid, nw) in enumerate(ml.standings(season)[:5]):
        y = cy0 + 215 + n * 52
        col = player_color(season, uid)
        d.ellipse([cx0 + 120, y + 2, cx0 + 148, y + 30], fill=col, outline=(255, 255, 255), width=2)
        nm = names.get(str(uid), '玩家')
        _center(d, cx0 + 134, y + 4, _short(nm), _font(18), (255, 255, 255))
        d.text((cx0 + 165, y), f'{n + 1}. {nm[:8]}', font=_font(24), fill=(40, 30, 20))
        txt = f'${nw:,}'
        d.text((cx1 - 130 - d.textlength(txt, font=_font(24)), y), txt, font=_font(24), fill=(40, 100, 50))
    if not season['players']:
        _center(d, mx, cy0 + 260, '還沒有人加入，用 /mono join', _font(24), (90, 60, 30))

    buf = io.BytesIO()
    img.save(buf, 'PNG')
    return buf.getvalue()
