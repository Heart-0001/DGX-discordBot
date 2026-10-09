import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
import discord
from discord.ext import commands
from dotenv import load_dotenv

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    handlers=[
        logging.FileHandler('bot.log', encoding='utf-8'),
        logging.StreamHandler(sys.stdout),
    ]
)
log = logging.getLogger(__name__)

load_dotenv()

intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True

bot = commands.Bot(command_prefix='!', intents=intents)
EXTENSIONS = ('cogs.music', 'cogs.lyrics', 'cogs.lol', 'cogs.valo', 'cogs.bigtwo', 'cogs.uno', 'cogs.health')

# Activity 只有一個(同一個 App)：記住每個語音頻道最後用 /lyrics 還是 /lol 開的，前端照這個切畫面
# 存檔：bot 重啟後開著的 Activity 每 3 秒還會來問，不能全部退回歌詞
MODES_FILE = os.path.join('data', 'activity_modes.json')
try:
    with open(MODES_FILE, encoding='utf-8') as f:
        bot.activity_modes = {int(k): v for k, v in json.load(f).items()}
except (OSError, ValueError):
    bot.activity_modes = {}


bot.activity_seen = {}   # 頻道 → 最後一次有開著的 Activity 來問模式的時間


def activity_open(interaction) -> bool:
    """這個頻道(或使用者所在語音頻道)的 Activity 是不是正開著(畫面每 3 秒會來問一次模式)。"""
    keys = {interaction.channel_id}
    if interaction.user.voice and interaction.user.voice.channel:
        keys.add(interaction.user.voice.channel.id)
    now = time.monotonic()
    return any(now - bot.activity_seen.get(k, -1e9) < 10 for k in keys)


bot.activity_open = activity_open


def set_activity_mode(interaction, mode: str):
    """Activity 可能開在語音頻道或打指令的文字頻道 → 兩個都記。"""
    keys = {interaction.channel_id}
    if interaction.user.voice and interaction.user.voice.channel:
        keys.add(interaction.user.voice.channel.id)
    for k in keys - {None}:
        bot.activity_modes[k] = mode
    log.info(f'Activity 模式 {mode}: 頻道 {sorted(keys - {None})}')
    try:
        with open(MODES_FILE + '.tmp', 'w', encoding='utf-8') as f:
            json.dump(bot.activity_modes, f)
        os.replace(MODES_FILE + '.tmp', MODES_FILE)
    except OSError as e:
        log.warning(f'Activity 模式存檔失敗: {e!r}')


bot.set_activity_mode = set_activity_mode


bot.start_time = datetime.now(timezone.utc)


@bot.event
async def on_ready():
    # 1. 把 global 指令複製進每個 guild，即時生效
    for guild in bot.guilds:
        bot.tree.copy_global_to(guild=guild)
        synced = await bot.tree.sync(guild=guild)
        log.info(f'已同步 {len(synced)} 個指令到伺服器: {guild.name}')
    # 2. 清掉 Discord 上的 global 指令，避免與 guild 版重複出現。
    #    逐一刪除並跳過 Activity 的 Entry Point 指令(type 4)：它不能用 bulk 清掉，也不該刪。
    bot.tree.clear_commands(guild=None)
    for cmd in await bot.http.get_global_commands(bot.application_id):
        if cmd.get('type') != 4:
            await bot.http.delete_global_command(bot.application_id, cmd['id'])
    log.info(f'✅ {bot.user} 已上線！連接到 {len(bot.guilds)} 個伺服器')


@bot.command(name='sync')
async def sync_commands(ctx):
    """強制重新同步 slash commands 到目前伺服器（!sync）"""
    bot.tree.copy_global_to(guild=ctx.guild)
    synced = await bot.tree.sync(guild=ctx.guild)
    await ctx.send(f'✅ 已重新同步 {len(synced)} 個指令到 **{ctx.guild.name}**')


async def main():
    async with bot:
        for extension in EXTENSIONS:
            try:
                await bot.load_extension(extension)
            except Exception as e:
                log.exception(f'模組 {extension} 載入失敗，其他功能照常啟動: {e!r}')
        await bot.start(os.getenv('DISCORD_TOKEN'))


if __name__ == '__main__':
    asyncio.run(main())
