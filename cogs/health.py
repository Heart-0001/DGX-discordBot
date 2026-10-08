import asyncio
import logging
import os
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

log = logging.getLogger(__name__)

# 健檢腳本由 Hermes 每日排程執行，執行後會把報告寫到 REPORT_PATH。
HEALTHCHECK_SCRIPT = '/home/heart/discordbot/scripts/healthcheck.sh'
REPORT_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           'data', 'healthcheck_latest.txt')
MAX_REPORT_CHARS = 3800  # embed description 上限 4096，扣掉 code block 圍欄與省略提示


def _humanize_age(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 60:
        return f'{seconds} 秒前'
    if seconds < 3600:
        return f'{seconds // 60} 分鐘前'
    if seconds < 86400:
        return f'{seconds // 3600} 小時 {(seconds % 3600) // 60} 分鐘前'
    return f'{seconds // 86400} 天前'


class HealthCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @staticmethod
    def _build_embed(report: str, mtime: float | None, live: bool) -> discord.Embed:
        healthy = 'HEALTHY' in report
        problems = [ln for ln in report.splitlines() if ln.startswith('PROBLEM ')]

        if healthy:
            colour, title = discord.Colour.green(), '✅ discordbot 正常'
        else:
            colour = discord.Colour.red()
            title = f'⚠️ discordbot 有 {len(problems)} 個問題'

        body = report.strip()
        if len(body) > MAX_REPORT_CHARS:
            body = body[:MAX_REPORT_CHARS] + '\n…（報告過長已截斷）'

        embed = discord.Embed(
            title=title,
            description=f'```\n{body}\n```',
            colour=colour,
        )

        if live:
            embed.set_footer(text='本次為即時重新檢測')
        elif mtime is not None:
            age = _humanize_age(datetime.now(timezone.utc).timestamp() - mtime)
            stamp = datetime.fromtimestamp(mtime).strftime('%Y-%m-%d %H:%M:%S')
            embed.set_footer(text=f'最近一次檢測：{stamp}（{age}）')

        return embed

    async def _run_healthcheck(self) -> str | None:
        """即時跑一次健檢腳本，回傳輸出；失敗回 None。"""
        if not os.path.isfile(HEALTHCHECK_SCRIPT):
            return None
        try:
            proc = await asyncio.create_subprocess_exec(
                '/usr/bin/env', 'bash', HEALTHCHECK_SCRIPT,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=60)
        except asyncio.TimeoutError:
            log.error('健檢腳本執行逾時')
            return None
        except Exception as e:
            log.error(f'健檢腳本執行失敗: {e}')
            return None
        return stdout.decode('utf-8', errors='replace')

    @app_commands.command(name='check', description='顯示 bot 最近一次的健康檢測報告')
    @app_commands.describe(重新檢測='立即重跑一次健檢，而不是讀最近一次的報告')
    async def check(self, interaction: discord.Interaction, 重新檢測: bool = False):
        await interaction.response.defer()

        if 重新檢測:
            report = await self._run_healthcheck()
            if report is None:
                await interaction.followup.send('❌ 健檢腳本執行失敗，請看 bot.log。')
                return
            log.info('健檢：即時重新檢測完成')
            await interaction.followup.send(embed=self._build_embed(report, None, live=True))
            return

        if not os.path.isfile(REPORT_PATH):
            await interaction.followup.send(
                '📭 還沒有任何檢測報告。\n'
                '每日排程跑過之後就會有，或用 `/check 重新檢測:True` 立即產生一份。'
            )
            return

        try:
            with open(REPORT_PATH, encoding='utf-8') as f:
                report = f.read()
            mtime = os.path.getmtime(REPORT_PATH)
        except OSError as e:
            log.error(f'讀取健檢報告失敗: {e}')
            await interaction.followup.send(f'❌ 讀取報告失敗：{e}')
            return

        log.info('健檢：回傳最近一次報告')
        await interaction.followup.send(embed=self._build_embed(report, mtime, live=False))


async def setup(bot: commands.Bot):
    await bot.add_cog(HealthCog(bot))
