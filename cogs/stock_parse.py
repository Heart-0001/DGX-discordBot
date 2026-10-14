"""/stock 的純函式：代號/名稱解析、報價整理、開收盤時間判斷、走勢圖。(不碰網路，方便測試)

市場只分兩個：
  TW  台股(上市 tse / 上櫃 otc)，報價用證交所 mis 即時資料，走勢圖用 Yahoo
  US  美股，報價與走勢圖都用 Yahoo Finance chart API
"""
import io
import re
from datetime import datetime, time as dtime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

MARKETS = {
    'TW': {'tz': ZoneInfo('Asia/Taipei'), 'open': dtime(9, 0), 'close': dtime(13, 30), 'label': '台股'},
    'US': {'tz': ZoneInfo('America/New_York'), 'open': dtime(9, 30), 'close': dtime(16, 0), 'label': '美股'},
}
OPEN_DELAY = timedelta(minutes=1)      # 開盤後多久發開盤訊息(等第一筆成交)
CLOSE_DELAY = timedelta(minutes=2)     # 收盤後多久發收盤訊息(等最後一盤撮合)
HOLIDAY_AFTER = timedelta(minutes=40)  # 開盤這麼久還沒有今天的資料 → 當天休市
CLOSE_GIVEUP = timedelta(hours=3)      # 收盤訊息最晚補發到收盤後幾小時(bot 重啟用)

# 指數：中文名 → (市場, 代號)。台股指數的 mis 代號 t00/o00，Yahoo 用 ^TWII/^TWOII
INDICES = {
    '加權指數': ('TW', 't00'), '加權': ('TW', 't00'), '大盤': ('TW', 't00'), '台股': ('TW', 't00'), 'TAIEX': ('TW', 't00'),
    '櫃買指數': ('TW', 'o00'), '櫃買': ('TW', 'o00'), 'OTC': ('TW', 'o00'),
    '道瓊': ('US', '^DJI'), '道瓊指數': ('US', '^DJI'),
    '那斯達克': ('US', '^IXIC'), '納斯達克': ('US', '^IXIC'), '那指': ('US', '^IXIC'),
    '標普': ('US', '^GSPC'), '標普500': ('US', '^GSPC'), 'S&P500': ('US', '^GSPC'), 'S&P 500': ('US', '^GSPC'),
    '費半': ('US', '^SOX'), '費城半導體': ('US', '^SOX'), '羅素2000': ('US', '^RUT'),
    '恐慌指數': ('US', '^VIX'), 'VIX': ('US', '^VIX'),
}
TW_INDEX = {'t00': {'name': '加權指數', 'ex': 'tse', 'yahoo': '^TWII'},
            'o00': {'name': '櫃買指數', 'ex': 'otc', 'yahoo': '^TWOII'}}
# 常見美股的中文叫法(Yahoo 搜尋不吃中文)
US_ALIASES = {
    '蘋果': 'AAPL', '輝達': 'NVDA', '英偉達': 'NVDA', '特斯拉': 'TSLA', '微軟': 'MSFT', '谷歌': 'GOOGL',
    '字母': 'GOOGL', '亞馬遜': 'AMZN', '臉書': 'META', '台積電ADR': 'TSM', '台積ADR': 'TSM', '博通': 'AVGO',
    '超微': 'AMD', '英特爾': 'INTC', '美光': 'MU', '高通': 'QCOM', '網飛': 'NFLX', '波克夏': 'BRK-B',
    '甲骨文': 'ORCL', '超微電腦': 'SMCI', '美超微': 'SMCI', '安謀': 'ARM', '可口可樂': 'KO', '麥當勞': 'MCD',
    '好市多': 'COST', '迪士尼': 'DIS', '摩根大通': 'JPM', '星巴克': 'SBUX', '耐吉': 'NKE', '波音': 'BA',
    '輝瑞': 'PFE', '嬌生': 'JNJ', '禮來': 'LLY', '帕蘭泰爾': 'PLTR', '應用材料': 'AMAT', '艾司摩爾': 'ASML',
    '日月光ADR': 'ASX', '聯電ADR': 'UMC', '中華電ADR': 'CHT', '沃爾瑪': 'WMT', '威士': 'V', '萬事達': 'MA',
}
US_ZH = {}
for _zh, _sym in US_ALIASES.items():
    US_ZH.setdefault(_sym, _zh)

CJK_RE = re.compile(r'[㐀-鿿]')
TW_CODE_RE = re.compile(r'^(\d{4,6}[A-Z]?)(?:\.(TW|TWO))?$', re.I)
US_SYM_RE = re.compile(r'^\^?[A-Za-z][A-Za-z0-9.\-=]{0,11}$')
TW_LIST_CODE_RE = re.compile(r'^(\d{4}|00\d{2,4}[A-Z]?)$')   # 股票(4 碼)與 ETF(00 開頭)；排除權證、債券


# ── 台股清單 ──
def build_tw_list(twse: list, tpex: list) -> list[dict]:
    """證交所 STOCK_DAY_ALL + 櫃買 mainboard_daily_close_quotes → [{code, name, ex}]。"""
    out, seen = [], set()
    for rows, ex, ck, nk in ((twse, 'tse', 'Code', 'Name'), (tpex, 'otc', 'SecuritiesCompanyCode', 'CompanyName')):
        for r in rows or []:
            code, name = str(r.get(ck) or '').strip(), str(r.get(nk) or '').strip()
            if code and name and TW_LIST_CODE_RE.match(code) and code not in seen:
                seen.add(code)
                out.append({'code': code, 'name': name, 'ex': ex})
    return out


def _norm(s: str) -> str:
    return re.sub(r'\s+', '', s).upper().replace('臺', '台')


def tw_matches(q: str, tw_list: list[dict], limit: int = 25) -> list[dict]:
    """名稱/代號模糊比對，好的排前面：完全相同 > 代號開頭 > 名稱開頭 > 名稱包含；同級短名字、股票優先。"""
    nq = _norm(q)
    if not nq:
        return []
    scored = []
    for s in tw_list:
        code, name = s['code'], _norm(s['name'])
        if code == nq or name == nq:
            rank = 0
        elif code.startswith(nq):
            rank = 1
        elif name.startswith(nq):
            rank = 2
        elif nq in name:
            rank = 3
        else:
            continue
        scored.append((rank, len(code) != 4, len(name), code, s))
    scored.sort(key=lambda x: x[:4])
    return [x[-1] for x in scored[:limit]]


def resolve_local(q: str, tw_list: list[dict]) -> tuple[Optional[dict], list[dict]]:
    """不用網路能確定的解析。回傳 (目標, 其他候選)；目標 None = 要問 Yahoo(美股代號/英文名)。
    目標格式：{'market': 'TW', 'code', 'name', 'ex'} 或 {'market': 'US', 'symbol', 'zh'}。"""
    raw = q.strip()
    if not raw:
        return None, []
    nq = _norm(raw)
    for k, (mk, code) in INDICES.items():
        if _norm(k) == nq:
            return index_target(mk, code), []
    for k, sym in US_ALIASES.items():
        if _norm(k) == nq:
            return {'market': 'US', 'symbol': sym, 'zh': k}, []
    m = TW_CODE_RE.match(nq)
    if m:
        code = m.group(1)
        hit = next((s for s in tw_list if s['code'] == code), None)
        if hit:
            return {'market': 'TW', **hit}, []
        ex = {'TW': 'tse', 'TWO': 'otc'}.get((m.group(2) or '').upper())
        return {'market': 'TW', 'code': code, 'name': '', 'ex': ex}, []   # 清單沒有(新上市)：ex 未知就讓 mis 兩邊都試
    if CJK_RE.search(raw) or not US_SYM_RE.match(raw):
        hits = tw_matches(raw, tw_list, 6)
        if hits:
            return {'market': 'TW', **hits[0]}, hits[1:]
    return None, []


def index_target(market: str, code: str) -> dict:
    if market == 'TW':
        i = TW_INDEX[code]
        return {'market': 'TW', 'code': code, 'name': i['name'], 'ex': i['ex'], 'index': True}
    zh = next((k for k, (mk, c) in INDICES.items() if c == code), '')
    return {'market': 'US', 'symbol': code, 'zh': zh}


def us_symbol(q: str) -> str:
    """BRK.B → BRK-B(Yahoo 的寫法)。"""
    return q.strip().upper().replace('.', '-')


def yahoo_symbol(t: dict) -> str:
    if t['market'] == 'US':
        return t['symbol']
    if t.get('index'):
        return TW_INDEX[t['code']]['yahoo']
    return f"{t['code']}.{'TWO' if t.get('ex') == 'otc' else 'TW'}"


def track_key(t: dict) -> str:
    return f"TW:{t['code']}" if t['market'] == 'TW' else f"US:{t['symbol']}"


def pick_search(quotes: list[dict]) -> Optional[str]:
    """Yahoo 搜尋結果挑第一個美國掛牌的股票/ETF/指數。"""
    us_ex = {'NMS', 'NYQ', 'NGM', 'NCM', 'ASE', 'PCX', 'BTS', 'NAS', 'NYS', 'PNK', 'OQB', 'OQX', 'CBO', 'SNP', 'DJI', 'NIM'}
    for x in quotes or []:
        if x.get('quoteType') in ('EQUITY', 'ETF', 'INDEX', 'MUTUALFUND') and x.get('exchange') in us_ex and x.get('symbol'):
            return x['symbol']
    return None


# ── 報價 ──
def _num(v) -> Optional[float]:
    try:
        f = float(str(v).split('_')[0])
        return f if f == f and f > 0 else None
    except (TypeError, ValueError):
        return None


def parse_mis(row: dict) -> dict:
    """證交所 mis getStockInfo 的一筆 → 統一報價格式。v 單位是張，指數沒有量。"""
    tz = MARKETS['TW']['tz']
    d, t = row.get('d') or '', row.get('t') or '00:00:00'
    try:
        at = datetime.strptime(f'{d} {t}', '%Y%m%d %H:%M:%S').replace(tzinfo=tz)
    except ValueError:
        at = None
    bid, ask = _num(row.get('b')), _num(row.get('a'))
    price = _num(row.get('z')) or _num(row.get('pz')) or (round((bid + ask) / 2, 2) if bid and ask else bid or ask)
    vol = _num(row.get('v'))
    return {
        'market': 'TW', 'code': row.get('c') or '', 'name': row.get('n') or '', 'ex': row.get('ex') or '',
        'price': price, 'prev': _num(row.get('y')), 'open': _num(row.get('o')),
        'high': _num(row.get('h')), 'low': _num(row.get('l')),
        'volume': int(vol) if vol else None, 'vol_unit': '張',
        'limit_up': _num(row.get('u')), 'limit_down': _num(row.get('w')),
        'at': at, 'currency': 'TWD',
    }


def parse_yahoo(res: dict) -> dict:
    """Yahoo chart API 的 result[0] → 統一報價格式 + 當日走勢點。"""
    m = res.get('meta') or {}
    tz = ZoneInfo(m.get('exchangeTimezoneName') or 'America/New_York')
    reg = (m.get('currentTradingPeriod') or {}).get('regular') or {}
    ts = res.get('timestamp') or []
    q = ((res.get('indicators') or {}).get('quote') or [{}])[0]
    closes, opens = q.get('close') or [], q.get('open') or []
    points = [(t, c) for t, c in zip(ts, closes) if c is not None]
    # 開盤價 = 資料裡最後一天的第一根 K 棒(currentTradingPeriod 在盤前會指向還沒開始的今天，不能拿來切)
    last_day = datetime.fromtimestamp(points[-1][0], tz).date() if points else None
    first_open = next((o for t, o in zip(ts, opens)
                       if o is not None and datetime.fromtimestamp(t, tz).date() == last_day), None)
    at = datetime.fromtimestamp(m['regularMarketTime'], tz) if m.get('regularMarketTime') else None
    return {
        'market': 'US' if str(tz) == 'America/New_York' else 'TW' if str(tz) == 'Asia/Taipei' else 'OTHER',
        'symbol': m.get('symbol') or '', 'name': m.get('longName') or m.get('shortName') or m.get('symbol') or '',
        'price': _num(m.get('regularMarketPrice')),
        'prev': _num(m.get('previousClose')) or _num(m.get('chartPreviousClose')),
        'open': first_open, 'high': _num(m.get('regularMarketDayHigh')), 'low': _num(m.get('regularMarketDayLow')),
        'volume': int(m['regularMarketVolume']) if m.get('regularMarketVolume') else None, 'vol_unit': '股',
        'w52': (_num(m.get('fiftyTwoWeekLow')), _num(m.get('fiftyTwoWeekHigh'))),
        'at': at, 'currency': m.get('currency') or 'USD', 'type': m.get('instrumentType') or '',
        'session': (reg.get('start'), reg.get('end')), 'points': points,
    }


def change(q: dict, ref: str = 'price') -> tuple[Optional[float], Optional[float]]:
    p, y = q.get(ref), q.get('prev')
    if p is None or not y:
        return None, None
    return p - y, (p - y) / y * 100


def fmt_price(v: Optional[float]) -> str:
    if v is None:
        return '—'
    if v >= 100 and abs(v - round(v)) < 1e-9:
        return f'{v:,.0f}'
    return f'{v:,.4f}' if v < 1 else f'{v:,.2f}'


def fmt_change(d: Optional[float], pct: Optional[float]) -> str:
    if d is None:
        return '—'
    if abs(d) < 1e-9:
        return '平盤 0.00%'
    arrow = '▲' if d > 0 else '▼'
    a = abs(d)
    return f"{arrow} {a:,.{4 if a < 0.01 else 2}f} ({pct:+.2f}%)"


def fmt_volume(v: Optional[int], unit: str) -> str:
    if not v:
        return '—'
    if unit == '張' or v < 10_000:
        return f'{v:,} {unit}'
    if v < 100_000_000:
        return f'{v / 10_000:,.1f} 萬{unit}'
    return f'{v / 100_000_000:,.2f} 億{unit}'


def limit_tag(q: dict) -> str:
    p = q.get('price')
    if p and q.get('limit_up') and abs(p - q['limit_up']) < 1e-6:
        return ' 🔺漲停'
    if p and q.get('limit_down') and abs(p - q['limit_down']) < 1e-6:
        return ' 🔻跌停'
    return ''


# ── 開收盤時間 ──
def session_bounds(market: str, day, close_at: Optional[datetime] = None) -> tuple[datetime, datetime]:
    mk = MARKETS[market]
    start = datetime.combine(day, mk['open'], mk['tz'])
    end = close_at if close_at and close_at.date() == day else datetime.combine(day, mk['close'], mk['tz'])
    return start, end


def due_events(market: str, now: datetime, sent: dict, close_at: Optional[datetime] = None) -> list[str]:
    """照時鐘判斷這檔股票現在該發哪些訊息(還沒確認今天有沒有開市)。sent = {'open': 日期, 'close': 日期}。"""
    now = now.astimezone(MARKETS[market]['tz'])
    if now.weekday() >= 5:
        return []
    today = now.date().isoformat()
    start, end = session_bounds(market, now.date(), close_at)
    ev = []
    if start + OPEN_DELAY <= now < end and sent.get('open') != today:
        ev.append('open')
    if end + CLOSE_DELAY <= now < end + CLOSE_GIVEUP and sent.get('close') != today:
        ev.append('close')
    return ev


def already_past(market: str, now: datetime) -> dict:
    """新加入追蹤時：今天已經過了的事件先標成已發，免得一加就補發今天的開盤。"""
    now = now.astimezone(MARKETS[market]['tz'])
    today = now.date().isoformat()
    start, end = session_bounds(market, now.date())
    sent = {}
    if now >= start + OPEN_DELAY:
        sent['open'] = today
    if now >= end + CLOSE_DELAY:
        sent['close'] = today
    return sent


def traded_today(q: dict, now: datetime) -> bool:
    at = q.get('at')
    if not at:
        return False
    tz = now.tzinfo
    return at.astimezone(tz).date() == now.date()


def market_state(market: str, now: datetime, q: dict) -> str:
    mk = MARKETS[market]
    now = now.astimezone(mk['tz'])
    start, end = session_bounds(market, now.date())
    sess_end = (q.get('session') or (None, None))[1]
    if sess_end and datetime.fromtimestamp(sess_end, mk['tz']).date() == now.date():
        end = datetime.fromtimestamp(sess_end, mk['tz'])   # 美股提早收盤日
    if now.weekday() >= 5:
        return '休市'
    if now < start:
        return '盤前'
    if not traded_today(q, now):
        return '休市' if now >= start + HOLIDAY_AFTER else '盤前'
    return '盤中' if now < end else '已收盤'


# ── 走勢圖 ──
UP, DOWN, FLAT = (229, 56, 59), (45, 181, 93), (153, 170, 181)   # 台灣習慣：紅漲綠跌
FONT = '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'


def trend_color(d: Optional[float]) -> tuple:
    if d is None or abs(d) < 1e-9:
        return FLAT
    return UP if d > 0 else DOWN


def render_chart(points: list[tuple[int, float]], prev: Optional[float], start: int, end: int,
                 tz: ZoneInfo, last: Optional[float] = None) -> Optional[bytes]:
    """當日走勢(x 軸固定整個交易時段，盤中只畫到現在)；虛線是昨收。回傳 PNG。"""
    from PIL import Image, ImageDraw, ImageFont
    pts = [(t, p) for t, p in points if start <= t <= end]
    if len(pts) < 2:
        return None
    W, H, R, T, B = 720, 260, 12, 16, 28
    S = 2   # 先畫 2 倍再縮，線條比較平滑
    try:
        font = ImageFont.truetype(FONT, 12 * S)
    except OSError:
        font = ImageFont.load_default()
    vals = [p for _, p in pts] + ([prev] if prev else [])
    lo, hi = min(vals), max(vals)
    pad = (hi - lo) * 0.08 or hi * 0.01 or 1
    lo, hi = lo - pad, hi + pad
    labels = [max(v for _, v in pts), min(v for _, v in pts)] + ([prev] if prev else [])
    L = max(font.getlength(fmt_price(v)) for v in labels) / S + 14   # 左邊留給價格標籤
    img = Image.new('RGB', (W * S, H * S), (43, 45, 49))
    dr = ImageDraw.Draw(img, 'RGBA')
    X = lambda t: (L + (t - start) / max(1, end - start) * (W - L - R)) * S
    Y = lambda p: (T + (hi - p) / (hi - lo) * (H - T - B)) * S
    col = trend_color((last if last is not None else pts[-1][1]) - prev if prev else None)
    xy = [(X(t), Y(p)) for t, p in pts]
    base = Y(prev) if prev else (H - B) * S
    dr.polygon(xy + [(xy[-1][0], base), (xy[0][0], base)], fill=col + (48,))
    if prev:   # 昨收虛線
        x = L * S
        while x < (W - R) * S:
            dr.line([(x, base), (min(x + 8 * S, (W - R) * S), base)], fill=(150, 150, 150), width=S)
            x += 14 * S
    dr.line(xy, fill=col, width=2 * S, joint='curve')
    drawn = []
    for p in sorted(labels, key=lambda v: v != prev):   # 昨收優先，太近的標籤跳過
        y = Y(p)
        if all(abs(y - d) > 14 * S for d in drawn):
            dr.text((L * S - 6 * S, y), fmt_price(p), fill=(200, 200, 200), font=font, anchor='rm')
            drawn.append(y)
    for t, anchor in ((start, 'lt'), ((start + end) // 2, 'mt'), (end, 'rt')):
        dr.text((X(t), (H - B + 8) * S), datetime.fromtimestamp(t, tz).strftime('%H:%M'),
                fill=(170, 170, 170), font=font, anchor=anchor)
    img = img.resize((W, H), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, 'PNG', optimize=True)
    return buf.getvalue()
