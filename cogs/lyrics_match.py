"""歌詞比對的純函式(不依賴 discord，方便測試)：LRC 解析、歌名清理、LRCLIB 結果挑選。"""
import re
from typing import Optional

DURATION_TOLERANCE = 5   # 秒：LRCLIB 結果長度跟實際歌曲差太多就不收

_LRC_TIME = re.compile(r'\[(\d+):(\d+(?:\.\d+)?)\]')
# 標題裡這些括號段落通常是版本標記(Official MV、Lyrics…)，查歌詞時拿掉
_TITLE_NOISE = re.compile(
    r'[\(\[（【「『][^\)\]）】」』]*'
    r'(official|mv|m/v|music\s*video|audio|lyric|歌詞|字幕|動態|完整版|高音質|hd|4k|visualizer|video)'
    r'[^\)\]）】」』]*[\)\]）】」』]',
    re.IGNORECASE,
)
_BRACKETS = re.compile(r'[\(\[（【「『]([^\)\]）】」』]+)[\)\]）】」』]')
# 沒加括號的版本標記
_BARE_NOISE = re.compile(
    r'(official\s*(music\s*)?(video|mv|audio)|music\s*video|lyrics?\s*video|\bm/?v\b|官方完整版|官方\s*mv|動態歌詞)',
    re.IGNORECASE,
)
_CJK = re.compile(r'[぀-ヿ㐀-鿿가-힯]+(?:\s*[぀-ヿ㐀-鿿가-힯]+)*')


def parse_lrc(text: str) -> list[dict]:
    """LRC → [{'t': 秒, 'text': 歌詞}]，依時間排序；一行多個時間標籤會展開。"""
    lines = []
    for raw in text.splitlines():
        stamps = _LRC_TIME.findall(raw)
        if not stamps:
            continue
        lyric = _LRC_TIME.sub('', raw).strip()
        for m, s in stamps:
            lines.append({'t': round(int(m) * 60 + float(s), 3), 'text': lyric})
    lines.sort(key=lambda x: x['t'])
    return lines


def clean_artist(uploader: str) -> str:
    a = re.sub(r'\s*-\s*Topic$', '', uploader or '').strip()
    return re.sub(r'(VEVO|官方頻道|Official)$', '', a, flags=re.IGNORECASE).strip()


def _squash(s: str) -> str:
    return re.sub(r'\s+', ' ', s).strip(' -|/')


def name_variants(s: str) -> list[str]:
    """「告五人 Accusefive」→ [原字串, 告五人, Accusefive]：中英並列時分開試。"""
    out = [_squash(s)]
    cjk = _CJK.findall(s)
    if cjk:
        out.append(_squash(' '.join(cjk)))
        latin = _squash(_CJK.sub(' ', s))
        if latin:
            out.append(latin)
    seen, res = set(), []
    for v in out:
        if v and v.lower() not in seen:
            seen.add(v.lower())
            res.append(v)
    return res


def title_candidates(title: str, artist: str) -> list[str]:
    """產生幾種歌名查詢字串，由精確到寬鬆。"""
    base = _squash(_BARE_NOISE.sub(' ', _TITLE_NOISE.sub(' ', title or '')))
    cands = []
    # 周杰倫 Jay Chou【晴天 Sunny Day】：括號裡才是歌名，優先
    for inner in _BRACKETS.findall(base):
        cands += name_variants(inner)
    if ' - ' in base:   # 「歌手 - 歌名」
        cands += name_variants(base.split(' - ', 1)[1])
    cands += name_variants(_BRACKETS.sub(' ', base))
    cands += name_variants(base)
    for a in name_variants(artist) if artist else []:
        cands = [_squash(re.sub(re.escape(a), ' ', c, flags=re.IGNORECASE)) or c for c in cands]
    seen, out = set(), []
    for c in cands:
        if c and c.lower() not in seen:
            seen.add(c.lower())
            out.append(c)
    return out


def artist_matches(result: dict, artist: str) -> bool:
    got = (result.get('artistName') or '').lower()
    return bool(artist) and any(v.lower() in got or got in v.lower()
                                for v in name_variants(artist) if len(v) >= 2 and got)


def pick_result(results: list[dict], duration: float, artist: str = '') -> Optional[dict]:
    """從 LRCLIB 結果挑：長度要對得上；歌手對得上 > 有時間軸 > 長度最接近。
    沒有歌手資訊可比時，只收長度誤差 2 秒內的，避免同長度的別首歌被誤配。"""
    def ok(r):
        if not (r.get('syncedLyrics') or r.get('plainLyrics')):
            return False
        diff = abs((r.get('duration') or 0) - duration) if duration else 0
        if diff > DURATION_TOLERANCE:
            return False
        return artist_matches(r, artist) or diff <= 2
    good = [r for r in results if ok(r)]
    if not good:
        return None
    good.sort(key=lambda r: (not artist_matches(r, artist), not r.get('syncedLyrics'),
                             abs((r.get('duration') or 0) - (duration or 0))))
    return good[0]
