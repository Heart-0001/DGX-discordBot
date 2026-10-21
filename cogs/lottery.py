"""樂透：每個伺服器各一期，每天 21:00(台灣時間)開獎。

玩家沒有指令：Activity 的活動面板透過 cogs/fun_web.py 呼叫 info / buy。
管理員：/lottery prize 設定本期額外獎品說明(預設只有管理伺服器的人看得到)。
1 張券 = 1 注，每注讓彩金 +100 代幣(系統出錢)。每注機率相同，抽頭獎 / 二獎 / 三獎，同一人不會中兩個獎。
開獎時間、彩金、注數都存檔(data/lottery.json)；開獎先把結果寫檔、再發代幣、最後才公告，所以重啟不會漏開或重開。
"""
import asyncio
import json
import logging
import os
import random
from datetime import datetime
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from cogs.fun import FunError, launch_view
from games.fun_score import TW
from games.lottery_logic import (BET_POOL, RANK_NAME, SHARES, first_prize_chance, is_due, new_state, pull_in_draw,
                                 settle_period)

log = logging.getLogger(__name__)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FILE = os.path.join(ROOT, 'data', 'lottery.json')
GOLD = 0xF1C40F
CHECK_MIN, CHECK_MAX = 30, 60     # 秒：背景迴圈多久檢查一次
MAX_PRIZE_TEXT = 200
MAX_BUY = 20
RULES = (f'每張券 1 注，每注讓彩金 +{BET_POOL}（系統出錢）。每注機率相同；'
         f'頭獎 {SHARES[0]}% / 二獎 {SHARES[1]}% / 三獎 {SHARES[2]}%，同一人不會中兩個獎。不足 3 人時沒分出去的彩金滾到下期。')


class LotteryCog(commands.Cog):
    lottery = app_commands.Group(name='lottery', description='樂透管理', guild_only=True,
                                 default_permissions=discord.Permissions(manage_guild=True))

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.rng = random.Random()
        self.data = self._load()
        self._task: Optional[asyncio.Task] = None

    async def cog_load(self):
        self._task = asyncio.ensure_future(self._loop())
        log.info(f'🎫 樂透：{len(self.data["guilds"])} 個伺服器有進行中的期數')

    async def cog_unload(self):
        if self._task:
            self._task.cancel()

    # ── 存檔 ──
    @staticmethod
    def _load() -> dict:
        try:
            with open(FILE, encoding='utf-8') as f:
                d = json.load(f)
        except (OSError, ValueError):
            d = {}
        d.setdefault('guilds', {})
        now = datetime.now(TW)
        for st in d['guilds'].values():   # 從每週改成每天：已經排好的週日開獎提前到下一個 21:00
            pull_in_draw(st, now)
        return d

    def save(self):
        os.makedirs(os.path.dirname(FILE), exist_ok=True)
        with open(FILE + '.tmp', 'w', encoding='utf-8') as f:
            json.dump(self.data, f, ensure_ascii=False, indent=1)
        os.replace(FILE + '.tmp', FILE)

    def state(self, gid: int) -> dict:
        """這個伺服器目前這一期；沒有就開新的一期。"""
        key = str(gid)
        if key not in self.data['guilds']:
            self.data['guilds'][key] = new_state(datetime.now(TW))
            self.save()
        return self.data['guilds'][key]

    def _fun(self):
        return self.bot.get_cog('FunCog')

    # ── 服務 ──
    def info(self, gid: int, uid: int) -> dict:
        st = self.state(gid)
        fun = self._fun()
        total = sum(st['bets'].values())
        mine = st['bets'].get(str(uid), 0)
        last = st['last']
        if last:
            last = {'period': last['period'], 'pot': last['pot'], 'bets': last['bets'], 'players': last['players'],
                    'carry': last['carry'], 'prize': last.get('prize') or '',
                    'winners': [{'rank': w['rank'], 'rank_name': RANK_NAME[w['rank']], 'uid': w['uid'],
                                 'name': fun.name_of(int(w['uid'])), 'prize': w['prize']} for w in last['winners']]}
        return {'period': st['period'], 'pot': st['pot'], 'bets': total,
                'players': sum(1 for n in st['bets'].values() if n > 0), 'mine': mine,
                'chance': first_prize_chance(mine, total), 'next_draw': st['next_draw'], 'prize': st['prize'],
                'rules': RULES, 'last': last}

    def buy(self, gid: int, uid: int, count: int) -> dict:
        count = max(1, min(MAX_BUY, int(count)))
        st = self.state(gid)
        if is_due(st, datetime.now(TW)):
            raise FunError('這一期正在開獎，等一下公告出來就可以買下一期了')
        self._fun().need_tickets(uid, count)
        st['bets'][str(uid)] = st['bets'].get(str(uid), 0) + count
        st['pot'] += count * BET_POOL
        self.save()
        log.info(f'🎫 {self._fun().name_of(uid)} 買了 {count} 注，伺服器 {gid} 彩金 {st["pot"]}')
        return self.info(gid, uid)

    # ── 管理 ──
    @lottery.command(name='prize', description='設定本期額外獎品說明，開獎後清空')
    @app_commands.describe(text='例如「頭獎送身分組」；填「無」可清除')
    async def lottery_prize(self, interaction: discord.Interaction, text: str):
        st = self.state(interaction.guild_id)
        text = text.strip()[:MAX_PRIZE_TEXT]
        st['prize'] = '' if text in ('無', '清除', '') else text
        self.save()
        await interaction.response.send_message(
            f"✅ 本期額外獎品：{discord.utils.escape_markdown(st['prize'])}" if st['prize'] else '✅ 已清除額外獎品',
            ephemeral=True)

    # ── 開獎 ──
    def _result_text(self, last: dict) -> str:
        if last['bets'] <= 0:
            return f"沒有人買，彩金 🪙 {last['carry']:,} 滾到下期"
        lines = [f"{RANK_NAME[w['rank']]}：<@{w['uid']}> 🪙 **{w['prize']:,}**" for w in last['winners']]
        s = f"彩金 🪙 {last['pot']:,}・{last['bets']} 注・{last['players']} 人\n" + '\n'.join(lines)
        if last['carry']:
            s += f"\n🪙 {last['carry']:,} 滾到下期"
        if last.get('prize'):
            s += f"\n🎁 額外獎品：{discord.utils.escape_markdown(last['prize'])}"
        return s

    async def _loop(self):
        await self.bot.wait_until_ready()
        while True:
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning(f'🎫 樂透檢查例外: {e!r}')
            await asyncio.sleep(random.uniform(CHECK_MIN, CHECK_MAX))

    async def _tick(self):
        for gid in list(self.data['guilds']):
            try:
                await self._process(int(gid))
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning(f'🎫 樂透 {gid} 處理例外: {e!r}')

    async def _process(self, gid: int):
        st = self.data['guilds'][str(gid)]
        fun = self._fun()
        if not fun:
            return
        if settle_period(st, datetime.now(TW), self.rng):
            self.save()                # 先把結果與新一期寫檔，之後才發錢、公告
            log.info(f"🎫 伺服器 {gid} 開獎（第 {st['period'] - 1} 期）")
        # 發代幣：每發一筆就從待發清單拿掉並存檔(重啟後接著發，不重複)
        while st['pay']:
            w = st['pay'][0]
            if w['prize'] > 0:
                fun.add_coins(int(w['uid']), w['prize'])
                fun.note(int(w['uid']), 'lottery_wins')
            st['pay'].pop(0)
            self.save()
        if st['announce']:
            st['announce'] = False     # 不管成功與否都不再重發公告
            self.save()
            await self._announce(gid, st['last'])

    async def _announce(self, gid: int, last: dict):
        fun = self._fun()
        web = self.bot.get_cog('FunWebCog')
        top = next((w for w in last['winners'] if w['rank'] == 0), None)
        if web and top:
            await web.celebrate(gid, 'lottery', int(top['uid']),
                                f"樂透第 {last['period']} 期頭獎！🪙 {top['prize']:,}", big=True)
        ch = fun.channel_for(gid)
        if not ch:
            log.warning(f'🎫 伺服器 {gid} 沒設定活動頻道(/fun channel)，只存結果不公告')
            return
        emb = discord.Embed(title=f"🎫 第 {last['period']} 期樂透開獎！", color=GOLD,
                            description=self._result_text(last))
        mentions = [discord.Object(int(w['uid'])) for w in last['winners']]
        try:
            await ch.send(embed=emb, view=launch_view(), allowed_mentions=discord.AllowedMentions(users=mentions))
        except discord.HTTPException as e:
            log.warning(f'🎫 開獎公告發送失敗 <#{ch.id}>: {e!r}')


async def setup(bot: commands.Bot):
    await bot.add_cog(LotteryCog(bot))
