"""LRCLIB 以外的同步歌詞來源(中文歌比較齊全)：網易雲音樂、QQ 音樂、酷狗。

都是非官方 API，隨時可能改版失效 → 每個函式失敗一律回空結果，不往外丟例外，
由 LyricsCog 依序 fallback。搜尋結果統一成 {'artist','title','duration','ref'}，
ref 交給同來源的 fetch 取 LRC 文字。
"""
import base64
import html
import json
import logging
from typing import Optional

import aiohttp

log = logging.getLogger(__name__)

UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36'
SEARCH_LIMIT = 5


async def _get_json(http: aiohttp.ClientSession, url: str, params: dict, headers: dict) -> Optional[dict]:
    try:
        async with http.get(url, params=params, headers={'User-Agent': UA, **headers}) as r:
            if r.status != 200:
                return None
            return json.loads(await r.text())   # 有些回 text/plain，不能用 r.json()
    except Exception as e:
        log.warning(f'歌詞來源請求失敗 {url}: {e!r}')
        return None


# ── 網易雲音樂 ──
_NE = {'Referer': 'https://music.163.com'}


async def netease_search(http, q: str) -> list[dict]:
    d = await _get_json(http, 'https://music.163.com/api/search/get/web',
                        {'s': q, 'type': 1, 'limit': SEARCH_LIMIT}, _NE)
    songs = ((d or {}).get('result') or {}).get('songs') or []
    return [{'artist': ','.join(a.get('name', '') for a in s.get('artists') or []),
             'title': s.get('name', ''), 'duration': (s.get('duration') or 0) / 1000, 'ref': s['id']}
            for s in songs if s.get('id')]


async def netease_fetch(http, ref) -> Optional[str]:
    d = await _get_json(http, 'https://music.163.com/api/song/lyric',
                        {'id': ref, 'lv': 1, 'kv': 1, 'tv': -1}, _NE)
    return ((d or {}).get('lrc') or {}).get('lyric')


# ── QQ 音樂 ──
_QQ = {'Referer': 'https://y.qq.com/'}


async def qq_search(http, q: str) -> list[dict]:
    d = await _get_json(http, 'https://c.y.qq.com/splcloud/fcgi-bin/smartbox_new.fcg',
                        {'key': q, 'format': 'json'}, _QQ)
    items = (((d or {}).get('data') or {}).get('song') or {}).get('itemlist') or []
    out = []
    for it in items[:SEARCH_LIMIT]:
        if not it.get('mid'):
            continue
        # smartbox 沒給長度，另外查(擋 Live / 剪輯版要靠它)
        info = await _get_json(http, 'https://c.y.qq.com/v8/fcg-bin/fcg_play_single_song.fcg',
                               {'songmid': it['mid'], 'format': 'json'}, _QQ)
        data = (info or {}).get('data') or [{}]
        out.append({'artist': it.get('singer', ''), 'title': it.get('name', ''),
                    'duration': data[0].get('interval') or 0, 'ref': it['mid']})
    return out


async def qq_fetch(http, ref) -> Optional[str]:
    d = await _get_json(http, 'https://c.y.qq.com/lyric/fcgi-bin/fcg_query_lyric_new.fcg',
                        {'songmid': ref, 'format': 'json', 'nobase64': 1}, _QQ)
    lrc = (d or {}).get('lyric')
    return html.unescape(lrc) if lrc else None   # 會有 &apos; &#58; 之類的 entity


# ── 酷狗 ──
async def kugou_search(http, q: str, duration: float = 0) -> list[dict]:
    params = {'ver': 1, 'man': 'yes', 'client': 'pc', 'keyword': q}
    if duration:
        params['duration'] = int(duration * 1000)
    d = await _get_json(http, 'http://lyrics.kugou.com/search', params, {})
    return [{'artist': c.get('singer', ''), 'title': c.get('song', ''),
             'duration': (c.get('duration') or 0) / 1000, 'ref': (c['id'], c['accesskey'])}
            for c in ((d or {}).get('candidates') or [])[:SEARCH_LIMIT] if c.get('id') and c.get('accesskey')]


async def kugou_fetch(http, ref) -> Optional[str]:
    cid, key = ref
    d = await _get_json(http, 'http://lyrics.kugou.com/download',
                        {'ver': 1, 'client': 'pc', 'id': cid, 'accesskey': key, 'fmt': 'lrc', 'charset': 'utf8'}, {})
    content = (d or {}).get('content')
    if not content:
        return None
    try:
        return base64.b64decode(content).decode('utf-8', 'ignore')
    except ValueError:
        return None


# (名稱, search(http, q, duration), fetch(http, ref))，順序 = 優先順序
SOURCES = [
    ('網易雲', lambda http, q, dur: netease_search(http, q), netease_fetch),
    ('QQ音樂', lambda http, q, dur: qq_search(http, q), qq_fetch),
    ('酷狗', kugou_search, kugou_fetch),
]
