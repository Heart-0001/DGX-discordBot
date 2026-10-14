"""/stock：查股價(台股、美股自動判斷)；/stock-track：追蹤的股票在開盤、收盤各發一則訊息。

  /stock 6669 | 緯穎 | 台積 | VTI | apple | 蘋果 | 大盤 | 費半
  /stock-track add/remove/list
資料來源(都免費、免 key)：
  台股報價  證交所 mis.twse.com.tw 即時資料(上市上櫃都有，約 5 秒延遲)
  台股清單  證交所/櫃買 OpenAPI(代號↔名稱)，每天更新一次，快取在 data/stock_tw_list.json
  美股報價、走勢圖、英文名搜尋  Yahoo Finance(要瀏覽器 UA，不然 429)
休市判斷不用假日表：開盤後去看今天有沒有成交資料，40 分鐘還沒有就當休市。
"""
import asyncio
import io
import json
import logging
import os
import ssl
import time
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import quote

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

from cogs import stock_parse as sp

log = logging.getLogger(__name__)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TRACK_FILE = os.path.join(ROOT, 'data', 'stock_tracked.json')
LIST_FILE = os.path.join(ROOT, 'data', 'stock_tw_list.json')
UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) '
      'Chrome/124.0 Safari/537.36')
MIS = 'https://mis.twse.com.tw/stock'
YAHOO_HOSTS = ('https://query2.finance.yahoo.com', 'https://query1.finance.yahoo.com')
TWSE_LIST = 'https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL'
TPEX_LIST = 'https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes'
LOOP_EVERY = 30      # 秒：背景檢查開收盤
MAX_TRACKED = 40
LIST_TTL = 86400


def _esc(s) -> str:
    return discord.utils.escape_markdown(str(s or ''))


class StockCog(commands.Cog):
    track = app_commands.Group(name='stock-track', description='追蹤股票：開盤、收盤各發一則訊息')

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._http: Optional[aiohttp.ClientSession] = None
        self._task: Optional[asyncio.Task] = None
        self.tw_list: list[dict] = []
        self._list_at = 0.0
        self._cache: dict[str, tuple[float, object]] = {}
        self.tracked: dict[str, dict] = self._load_tracked()
        self._holiday: dict[str, str] = {}   # 市場 → 已確認休市的日期
        self._close_at: dict[str, datetime] = {}   # 市場 → 今天實際收盤時間(美股提早收盤日由 Yahoo 時段得知)

    async def cog_load(self):
        # aiohttp 預設的 SSL context 連 mis.twse.com.tw 會被 reset(2026-10-14 實測)，標準 context 才連得上
        self._http = aiohttp.ClientSession(headers={'User-Agent': UA}, timeout=aiohttp.ClientTimeout(total=15),
                                           connector=aiohttp.TCPConnector(ssl=ssl.create_default_context()))
        self._load_list_cache()
        self._task = asyncio.ensure_future(self._loop())
        log.info(f'📈 股票模組：台股清單 {len(self.tw_list)} 檔，追蹤 {len(self.tracked)} 檔')

    async def cog_unload(self):
        if self._task:
            self._task.cancel()
        if self._http:
            await self._http.close()

    # ── 存檔 ──
    @staticmethod
    def _load_tracked() -> dict:
        try:
            with open(TRACK_FILE, encoding='utf-8') as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def _save_tracked(self):
        with open(TRACK_FILE + '.tmp', 'w', encoding='utf-8') as f:
            json.dump(self.tracked, f, ensure_ascii=False, indent=1)
        os.replace(TRACK_FILE + '.tmp', TRACK_FILE)

    def _load_list_cache(self):
        try:
            with open(LIST_FILE, encoding='utf-8') as f:
                d = json.load(f)
            self.tw_list, self._list_at = d['list'], d['at']
        except (OSError, ValueError, KeyError):
            pass

    async def _refresh_list(self, force=False):
        if not force and time.time() - self._list_at < LIST_TTL and self.tw_list:
            return
        twse, tpex = await asyncio.gather(self._get_json(TWSE_LIST), self._get_json(TPEX_LIST))
        lst = sp.build_tw_list(twse or [], tpex or [])
        if len(lst) < 500:   # 抓失敗或只抓到一邊：沿用舊的，1 小時後再試
            log.warning(f'📈 台股清單更新失敗(只拿到 {len(lst)} 檔)，沿用舊清單 {len(self.tw_list)} 檔')
            self._list_at = time.time() - LIST_TTL + 3600
            return
        self.tw_list, self._list_at = lst, time.time()
        with open(LIST_FILE + '.tmp', 'w', encoding='utf-8') as f:
            json.dump({'at': self._list_at, 'list': lst}, f, ensure_ascii=False)
        os.replace(LIST_FILE + '.tmp', LIST_FILE)
        log.info(f'📈 台股清單更新：{len(lst)} 檔')

    # ── HTTP ──
    async def _get_json(self, url: str, ttl: float = 0):
        hit = self._cache.get(url)
        if ttl and hit and time.time() < hit[0]:
            return hit[1]
        try:
            async with self._http.get(url) as r:
                if r.status != 200:
                    if r.status != 404:
                        log.warning(f'📈 {r.status} {url.split("?")[0]}')
                    return None
                data = await r.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as e:
            log.warning(f'📈 連線失敗 {url.split("?")[0]}: {e!r}')
            return None
        if ttl:
            self._cache[url] = (time.time() + ttl, data)
            if len(self._cache) > 300:
                now = time.time()
                for k in [k for k, (exp, _) in self._cache.items() if exp < now]:
                    self._cache.pop(k, None)
        return data

    async def _yahoo(self, path: str, ttl: float = 15):
        for host in YAHOO_HOSTS:   # query2 被限流(429)時換 query1
            d = await self._get_json(host + path, ttl)
            if d is not None:
                return d
        return None

    async def yahoo_chart(self, symbol: str, ttl: float = 15) -> Optional[dict]:
        d = await self._yahoo(f'/v8/finance/chart/{quote(symbol, safe="^=")}?range=1d&interval=2m', ttl)
        res = ((d or {}).get('chart') or {}).get('result') or []
        return sp.parse_yahoo(res[0]) if res and (res[0].get('meta') or {}).get('regularMarketPrice') else None

    async def yahoo_search(self, q: str) -> Optional[str]:
        d = await self._yahoo(f'/v1/finance/search?q={quote(q)}&quotesCount=8&newsCount=0', 600)
        return sp.pick_search((d or {}).get('quotes'))

    async def mis_quotes(self, targets: list[dict]) -> dict[str, dict]:
        """一次查多檔台股 → {code: 報價}。不知道上市還上櫃的兩邊都問。"""
        chans = []
        for t in targets:
            exs = [t['ex']] if t.get('ex') else ['tse', 'otc']
            chans += [f"{ex}_{t['code']}.tw" for ex in exs]
        out = {}
        for i in range(0, len(chans), 40):
            url = f"{MIS}/api/getStockInfo.jsp?ex_ch={quote('|'.join(chans[i:i + 40]), safe='|_.')}&json=1&delay=0"
            d = await self._get_json(url)
            if not d or d.get('rtcode') != '0000':   # mis 偶爾要先拿首頁 cookie
                await self._get_json(f'{MIS}/index.jsp')
                d = await self._get_json(url)
            for row in (d or {}).get('msgArray') or []:
                if row.get('c') and row.get('d'):
                    out[row['c']] = sp.parse_mis(row)
        return out

    # ── 解析輸入 ──
    async def resolve(self, q: str) -> tuple[Optional[dict], list[dict]]:
        await self._refresh_list()
        t, others = sp.resolve_local(q, self.tw_list)
        if t and t['market'] == 'TW' and not t.get('ex'):   # 清單沒有的台股代號：問 mis 是上市還上櫃
            qs = await self.mis_quotes([t])
            hit = qs.get(t['code'])
            if not hit:
                return None, []
            t.update(ex=hit['ex'], name=hit['name'])
        if t:
            return t, others
        if not sp.US_SYM_RE.match(q.strip()):
            return None, []
        sym = sp.us_symbol(q)
        c = await self.yahoo_chart(sym)
        if not c or c['market'] != 'US':
            sym = await self.yahoo_search(q.strip())
            c = await self.yahoo_chart(sym) if sym else None
        if not c or c['market'] != 'US':
            return None, []
        return {'market': 'US', 'symbol': c['symbol'], 'zh': sp.US_ZH.get(c['symbol'], ''), 'name': c['name']}, []

    async def quote(self, t: dict, chart=True) -> tuple[Optional[dict], Optional[dict]]:
        """(報價, Yahoo 走勢)。台股報價用 mis，走勢圖用 Yahoo；美股兩個都是 Yahoo。"""
        if t['market'] == 'US':
            c = await self.yahoo_chart(t['symbol'])
            return c, c
        qs, c = await asyncio.gather(self.mis_quotes([t]), self.yahoo_chart(sp.yahoo_symbol(t)) if chart else _none())
        q = qs.get(t['code'])
        if q and t.get('name'):
            q['name'] = t['name']
        return q, c

    # ── 畫面 ──
    def _title(self, t: dict, q: dict) -> str:
        if t['market'] == 'TW':
            return f"{q.get('name') or t.get('name')}" + ('' if t.get('index') else f" {t['code']}")
        name = q.get('name') or t.get('name') or ''
        zh = t.get('zh') or sp.US_ZH.get(t['symbol'], '')
        return f"{zh + ' ' if zh else ''}{name} ({t['symbol']})"

    def _chart_file(self, t: dict, q: dict, c: Optional[dict]) -> Optional[discord.File]:
        if not c or not c.get('points'):
            return None
        mk = sp.MARKETS[t['market']]
        day = datetime.fromtimestamp(c['points'][-1][0], mk['tz']).date()
        start, end = sp.session_bounds(t['market'], day)
        s, e = c.get('session') or (None, None)
        if s and e and datetime.fromtimestamp(s, mk['tz']).date() == day:
            start, end = datetime.fromtimestamp(s, mk['tz']), datetime.fromtimestamp(e, mk['tz'])
        # 昨收以報價為準：Yahoo 的 ^TWII 昨收是錯的(2026-10-14 實測比證交所多 960 點)，盤中走勢倒是對的
        png = sp.render_chart(c['points'], q.get('prev') or c.get('prev'), int(start.timestamp()),
                              int(end.timestamp()), mk['tz'], last=q.get('price'))
        return discord.File(io.BytesIO(png), filename='chart.png') if png else None

    def _embed(self, t: dict, q: dict, c: Optional[dict], kind: str = 'query') -> discord.Embed:
        """kind: query(/stock)、open(開盤通知)、close(收盤通知)。"""
        mk = sp.MARKETS[t['market']]
        now = datetime.now(mk['tz'])
        d, pct = sp.change(q)
        cur = '' if t['market'] == 'TW' or q.get('currency') in (None, 'USD') else f" {q['currency']}"
        if kind == 'open':
            od, opct = sp.change(q, 'open')
            title = f"🔔 開盤｜{self._title(t, q)}"
            desc = (f"開盤 **{sp.fmt_price(q.get('open'))}**{cur}　{sp.fmt_change(od, opct)}\n"
                    f"現價 {sp.fmt_price(q.get('price'))}　{sp.fmt_change(d, pct)}{sp.limit_tag(q)}")
            color = sp.trend_color(od)
        else:
            head = '🔔 收盤｜' if kind == 'close' else ''
            state = '' if kind == 'close' else f"　`{sp.market_state(t['market'], now, q)}`"
            title = f"{head}{self._title(t, q)}"
            label = '收盤 ' if kind == 'close' else ''
            desc = (f"{label}**{sp.fmt_price(q.get('price'))}**{cur}　{sp.fmt_change(d, pct)}"
                    f"{sp.limit_tag(q)}{state}")
            color = sp.trend_color(d)
        emb = discord.Embed(title=title, description=desc, color=discord.Color.from_rgb(*color))
        emb.add_field(name='開盤', value=sp.fmt_price(q.get('open')))
        emb.add_field(name='最高', value=sp.fmt_price(q.get('high')))
        emb.add_field(name='最低', value=sp.fmt_price(q.get('low')))
        emb.add_field(name='昨收', value=sp.fmt_price(q.get('prev')))
        if q.get('volume'):
            emb.add_field(name='成交量', value=sp.fmt_volume(q['volume'], q['vol_unit']))
        w52 = (c or {}).get('w52') or (None, None)
        if kind != 'open' and w52[0] and w52[1]:
            emb.add_field(name='52 週區間', value=f'{sp.fmt_price(w52[0])} – {sp.fmt_price(w52[1])}')
        at = q.get('at')
        src = '證交所' if t['market'] == 'TW' else 'Yahoo Finance'
        tzname = '台北' if t['market'] == 'TW' else '美東'
        emb.set_footer(text=f"{mk['label']} · {src}" + (f" · {at.strftime('%m/%d %H:%M')} {tzname}" if at else ''))
        return emb

    # ── 指令 ──
    @app_commands.command(name='stock', description='查股價：台股代號/名稱或美股代號都可以，例如 6669、緯穎、VTI、蘋果、大盤')
    @app_commands.describe(symbol='代號或名稱，例如 2330、台積電、0050、AAPL、apple、費半')
    async def stock(self, interaction: discord.Interaction, symbol: str):
        await interaction.response.defer(thinking=True)
        t, others = await self.resolve(symbol)
        if not t:
            await interaction.followup.send(f'❌ 找不到「{_esc(symbol)}」：台股請用代號或中文名，美股請用代號或英文名')
            return
        q, c = await self.quote(t)
        if not q or q.get('price') is None:
            await interaction.followup.send(f'❌ 暫時拿不到 **{_esc(self._title(t, q or {}))}** 的報價，稍後再試')
            return
        emb = self._embed(t, q, c)
        f = self._chart_file(t, q, c)
        if f:
            emb.set_image(url='attachment://chart.png')
        if others:
            emb.add_field(name='也符合', value='、'.join(f"{o['name']} {o['code']}" for o in others[:5]), inline=False)
        await interaction.followup.send(embed=emb, **({'file': f} if f else {}))

    @stock.autocomplete('symbol')
    async def _ac_symbol(self, interaction: discord.Interaction, current: str):
        return self._suggest(current)

    def _suggest(self, current: str) -> list[app_commands.Choice[str]]:
        cur = current.strip()
        if not cur:
            return []
        out = []
        if sp.US_SYM_RE.match(cur) and not sp.TW_CODE_RE.match(cur):
            out.append(app_commands.Choice(name=f'{cur.upper()}（美股代號）', value=cur.upper()))
        nq = cur.upper()
        for k, (mk, code) in sp.INDICES.items():
            if nq in k.upper() and len(out) < 6:
                out.append(app_commands.Choice(name=f"{k}（{'台股' if mk == 'TW' else '美股'}指數）", value=k))
        for s in sp.tw_matches(cur, self.tw_list, 18 - len(out)):
            out.append(app_commands.Choice(name=f"{s['code']} {s['name']}（{'上市' if s['ex'] == 'tse' else '上櫃'}）",
                                           value=s['code']))
        for k, sym in sp.US_ALIASES.items():
            if nq in k.upper() and len(out) < 25:
                out.append(app_commands.Choice(name=f'{k} {sym}（美股）', value=sym))
        return out[:25]

    @track.command(name='add', description='追蹤股票：每天開盤、收盤各發一則訊息到這個頻道')
    @app_commands.describe(symbol='代號或名稱，例如 6669、緯穎、VTI')
    async def track_add(self, interaction: discord.Interaction, symbol: str):
        await interaction.response.defer(thinking=True)
        t, _ = await self.resolve(symbol)
        if not t:
            await interaction.followup.send(f'❌ 找不到「{_esc(symbol)}」')
            return
        q, _ = await self.quote(t, chart=False)
        key, gid = sp.track_key(t), str(interaction.guild_id or 0)
        title = self._title(t, q or {})
        if key not in self.tracked and len(self.tracked) >= MAX_TRACKED:
            await interaction.followup.send(f'❌ 最多追蹤 {MAX_TRACKED} 檔')
            return
        entry = self.tracked.setdefault(key, {**t, 'channels': {},
                                              'sent': sp.already_past(t['market'], datetime.now(timezone.utc))})
        if q and q.get('name') and not entry.get('name'):
            entry['name'] = q['name']
        entry['channels'][gid] = interaction.channel_id
        self._save_tracked()
        mk = sp.MARKETS[t['market']]
        await interaction.followup.send(
            f"✅ 追蹤 **{_esc(title)}**：每個交易日開盤（{mk['open'].strftime('%H:%M')}）、"
            f"收盤（{mk['close'].strftime('%H:%M')}）{'台北' if t['market'] == 'TW' else '美東'}時間會發到 <#{interaction.channel_id}>"
            + ('' if t['market'] == 'TW' else '（美股換算台灣時間約 21:30/04:00，冬令時間晚一小時）'))

    @track_add.autocomplete('symbol')
    async def _ac_add(self, interaction: discord.Interaction, current: str):
        return self._suggest(current)

    @track.command(name='remove', description='取消追蹤股票')
    @app_commands.describe(symbol='要取消的股票')
    async def track_remove(self, interaction: discord.Interaction, symbol: str):
        gid = str(interaction.guild_id or 0)
        key = symbol if symbol in self.tracked else None
        if not key:
            t, _ = sp.resolve_local(symbol, self.tw_list)
            if not t and sp.US_SYM_RE.match(symbol.strip()):
                t = {'market': 'US', 'symbol': sp.us_symbol(symbol)}
            key = sp.track_key(t) if t else None
        if key not in self.tracked:   # 打英文名/部分名字：在這個伺服器的名單裡找唯一符合的
            hits = [k for k, e in self.tracked.items()
                    if gid in e.get('channels', {}) and symbol.strip().upper() in self._label(e).upper()]
            key = hits[0] if len(hits) == 1 else key
        if not key or gid not in (self.tracked.get(key) or {}).get('channels', {}):
            await interaction.response.send_message('❌ 這個伺服器沒有追蹤這檔，用 `/stock-track list` 看名單', ephemeral=True)
            return
        e = self.tracked[key]
        e['channels'].pop(gid, None)
        if not e['channels']:
            self.tracked.pop(key)
        self._save_tracked()
        await interaction.response.send_message(f"✅ 已取消追蹤 **{_esc(self._label(e))}**")

    @track_remove.autocomplete('symbol')
    async def _ac_remove(self, interaction: discord.Interaction, current: str):
        gid = str(interaction.guild_id or 0)
        cur = current.strip().upper()
        return [app_commands.Choice(name=self._label(e), value=k) for k, e in self.tracked.items()
                if gid in e.get('channels', {}) and cur in self._label(e).upper()][:25]

    @staticmethod
    def _label(e: dict) -> str:
        if e['market'] == 'TW':
            return f"{e.get('name', '')} {'' if e.get('index') else e['code']}".strip()
        zh = e.get('zh') or sp.US_ZH.get(e['symbol'], '')
        return f"{e['symbol']}{' ' + zh if zh else ''}"

    @track.command(name='list', description='這個伺服器追蹤中的股票')
    async def track_list(self, interaction: discord.Interaction):
        gid = str(interaction.guild_id or 0)
        rows = [(e['market'], f"・{_esc(self._label(e))} → <#{e['channels'][gid]}>")
                for e in self.tracked.values() if gid in e.get('channels', {})]
        if not rows:
            await interaction.response.send_message('這個伺服器還沒有追蹤股票，用 `/stock-track add` 加入', ephemeral=True)
            return
        parts = []
        for mk in ('TW', 'US'):
            lines = [r for m, r in rows if m == mk]
            if lines:
                m = sp.MARKETS[mk]
                parts.append(f"**{m['label']}**（開盤 {m['open'].strftime('%H:%M')}、收盤 {m['close'].strftime('%H:%M')} "
                             f"{'台北' if mk == 'TW' else '美東'}）\n" + '\n'.join(lines))
        await interaction.response.send_message('\n\n'.join(parts), ephemeral=True)

    # ── 背景：開收盤通知 ──
    async def _loop(self):
        await self.bot.wait_until_ready()
        while True:
            try:
                await self._refresh_list()
                for mk in ('TW', 'US'):
                    await self._tick(mk)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.exception(f'📈 開收盤檢查例外: {e!r}')
            await asyncio.sleep(LOOP_EVERY)

    async def _tick(self, market: str, now: Optional[datetime] = None):
        mk = sp.MARKETS[market]
        now = (now or datetime.now(timezone.utc)).astimezone(mk['tz'])
        today = now.date().isoformat()
        if self._holiday.get(market) == today:
            return
        keys = [k for k, e in self.tracked.items() if e['market'] == market]
        close_at = self._close_at.get(market)
        close_at = close_at if close_at and close_at.date() == now.date() else None
        due = {k: sp.due_events(market, now, self.tracked[k].get('sent', {}), close_at) for k in keys}
        if not any(due.values()):
            return
        quotes, charts, probe = await self._fetch(market, [self.tracked[k] for k in keys if due[k]])
        if market == 'US':   # 美股提早收盤日(感恩節隔天等)：收盤時間用 Yahoo 給的今天時段
            sess = (probe or {}).get('session') or (None, None)
            if sess[1] and datetime.fromtimestamp(sess[1], mk['tz']).date() == now.date():
                self._close_at[market] = datetime.fromtimestamp(sess[1], mk['tz'])
        start, end = sp.session_bounds(market, now.date(), close_at)
        if not probe or not sp.traded_today(probe, now):   # 用大盤(台股加權/美股 SPY)判斷今天有沒有開市
            if probe and now >= start + sp.HOLIDAY_AFTER:
                self._holiday[market] = today
                log.info(f"📈 {mk['label']} {today} 休市，不發開收盤訊息")
            return
        for k in keys:
            e = self.tracked[k]
            q = quotes.get(k)
            if not q or not due[k]:
                continue
            if (not sp.traded_today(q, now) or q.get('open') is None) and now < start + timedelta(minutes=15):
                continue   # 這檔今天還沒成交(冷門股/資料還沒到)：等一下，開盤 15 分鐘後照發
            for ev in due[k]:
                if ev == 'close' and market == 'TW' and q.get('at') and q['at'] < end \
                        and now < end + timedelta(minutes=20):
                    continue   # 收盤集合競價還沒公布
                if await self._post(e, q, charts.get(k), ev):
                    e.setdefault('sent', {})[ev] = today
                    self._save_tracked()

    async def _fetch(self, market: str, entries: list[dict]) -> tuple[dict, dict, Optional[dict]]:
        """({track_key: 報價}, {track_key: Yahoo 走勢}, 大盤報價)。"""
        quotes, charts = {}, {}
        if market == 'TW':
            idx = sp.index_target('TW', 't00')
            qs = await self.mis_quotes(entries + [idx])
            ys = await asyncio.gather(*(self.yahoo_chart(sp.yahoo_symbol(e), 30) for e in entries))
            for e, c in zip(entries, ys):
                k = sp.track_key(e)
                quotes[k], charts[k] = qs.get(e['code']), c
                if quotes[k] and e.get('name'):
                    quotes[k]['name'] = e['name']
            return quotes, charts, qs.get('t00')
        ys = await asyncio.gather(*(self.yahoo_chart(e['symbol'], 30) for e in entries + [{'symbol': 'SPY'}]))
        for e, c in zip(entries, ys):
            quotes[sp.track_key(e)] = charts[sp.track_key(e)] = c
        return quotes, charts, ys[-1]

    async def _post(self, e: dict, q: dict, c: Optional[dict], ev: str) -> bool:
        """發到每個伺服器的頻道；至少一個成功才算發過(頻道全掛了也算，免得每 30 秒重試)。"""
        ok = False
        for gid, chid in list(e['channels'].items()):
            ch = self.bot.get_channel(chid)
            if ch is None:
                try:
                    ch = await self.bot.fetch_channel(chid)
                except discord.HTTPException as ex:
                    log.warning(f'📈 頻道 {chid} 不見了({ex.status})，{self._label(e)} 不發')
                    ok = True
                    continue
            emb = self._embed(e, q, c, ev)
            f = self._chart_file(e, q, c) if ev == 'close' else None
            if f:
                emb.set_image(url='attachment://chart.png')
            try:
                await ch.send(embed=emb, **({'file': f} if f else {}))
                ok = True
            except discord.Forbidden:
                log.warning(f'📈 沒有權限發到頻道 {chid}')
                ok = True
            except discord.HTTPException as ex:
                log.warning(f'📈 發送失敗 {chid}: {ex!r}')
        if ok:
            log.info(f'📈 {self._label(e)} {ev} 通知已發')
        return ok


async def _none():
    return None


async def setup(bot: commands.Bot):
    await bot.add_cog(StockCog(bot))
