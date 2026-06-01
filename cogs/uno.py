from __future__ import annotations

import asyncio
import io
import json
import os
import logging
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from games.uno_logic import UNOGame, Card, Color, Action, COLOR_EMOJI, is_cpu, cpu_name, ai_pick_card, ai_choose_color
from games.uno_image import render_hand

log = logging.getLogger(__name__)

STATS_FILE = 'data/uno_stats.json'
LOBBY_TIMEOUT = 300
TURN_TIMEOUT = 60
CPU_PLAY_DELAY = 1.5  # seconds

# ── Global bot reference (set at setup) ──
_bot = None


def random_color() -> Color:
    import random
    return random.choice([Color.RED, Color.YELLOW, Color.GREEN, Color.BLUE])


def _member_name(guild: discord.Guild, uid: int) -> str:
    """Resolve player name — guild member cache → bot global cache → mention."""
    if is_cpu(uid):
        return cpu_name(uid)
    member = guild.get_member(uid)
    if member:
        return member.display_name
    # Fallback if member not in guild cache (e.g. after bot restart)
    if _bot:
        user = _bot.get_user(uid)
        if user:
            return user.display_name
    # Last resort: mention (still renders as name for server members)
    return f'<@{uid}>'


def _hand_file(cards: list, selected: set) -> discord.File:
    img_bytes = render_hand(cards, selected)
    return discord.File(io.BytesIO(img_bytes), filename='uno_hand.png')


def _deal_fresh_hands(game: UNOGame):
    """Return all cards to the deck, reshuffle, reset every player's per-turn
    state, keep the current discard top, and deal 7 cards to each player.
    Used when the lobby roster changes (human/CPU joins)."""
    import random
    game.deck.extend([c for hand in game.hands.values() for c in hand])
    random.shuffle(game.deck)
    game.hands = {pid: [] for pid in game.players}
    game.selected = {pid: set() for pid in game.players}
    game.drawn_cards = {pid: [] for pid in game.players}
    game.uno_called = {pid: False for pid in game.players}
    if game.discard:
        game.discard = [game.discard[-1]]
    for _ in range(7):
        for pid in game.players:
            if game.deck:
                game.hands[pid].append(game.deck.pop())


# ── Stats ──

def _load_stats() -> dict:
    if os.path.exists(STATS_FILE):
        with open(STATS_FILE, encoding='utf-8') as f:
            return json.load(f)
    return {}


def _save_stats(data: dict):
    os.makedirs('data', exist_ok=True)
    with open(STATS_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _record_result(winner_id: int, player_ids: list[int]):
    stats = _load_stats()
    for pid in player_ids:
        stats.setdefault(str(pid), {'wins': 0, 'games': 0})
        stats[str(pid)]['games'] += 1
    stats[str(winner_id)]['wins'] += 1
    _save_stats(stats)


# ── LobbyView ──

class LobbyView(discord.ui.View):
    def __init__(self, game: UNOGame, cog: 'Uno'):
        super().__init__(timeout=LOBBY_TIMEOUT)
        self.game = game
        self.cog = cog
        self.message: Optional[discord.Message] = None

    def _embed(self, guild: discord.Guild) -> discord.Embed:
        lines = []
        host_id = self.cog._host_ids.get(self.game.guild_id)
        for pid in self.game.players:
            name = _member_name(guild, pid)
            host = ' 👑' if pid == host_id else ''
            cpu_tag = ' 🤖' if is_cpu(pid) else ''
            lines.append(f'• {name}{cpu_tag}{host}')
        embed = discord.Embed(title='🎴 Uno — 等待玩家加入', color=0xe74c3c)
        embed.add_field(name=f'玩家 ({len(self.game.players)}/{Uno.MAX_PLAYERS})', value='\n'.join(lines))
        embed.set_footer(text=f'最多 {Uno.MAX_PLAYERS} 人 • 5 分鐘未開始自動取消')
        return embed

    @discord.ui.button(label='➕ 加入', style=discord.ButtonStyle.success, custom_id='uno_join')
    async def join_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        uid = interaction.user.id
        if uid in self.game.players:
            await interaction.response.send_message('你已經在房間裡了', ephemeral=True)
            return
        if len(self.game.players) >= Uno.MAX_PLAYERS:
            await interaction.response.send_message('房間已滿', ephemeral=True)
            return
        self._add_human(uid)
        for child in self.children:
            if getattr(child, 'custom_id', '') == 'uno_start':
                child.disabled = len(self.game.players) < 2
                break
        await interaction.response.edit_message(embed=self._embed(interaction.guild), view=self)

    @discord.ui.button(label='🤖 + 電腦', style=discord.ButtonStyle.secondary, custom_id='uno_add_cpu')
    async def add_cpu_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        human_count = sum(1 for p in self.game.players if not is_cpu(p))
        if human_count >= Uno.MAX_PLAYERS:
            await interaction.response.send_message(f'人類已滿 ({Uno.MAX_PLAYERS})', ephemeral=True)
            return
        if len(self.game.players) >= Uno.MAX_PLAYERS:
            await interaction.response.send_message(f'總人數上限 {Uno.MAX_PLAYERS}', ephemeral=True)
            return
        new_cpu = self.cog._add_cpu_player(self.game)
        for child in self.children:
            if getattr(child, 'custom_id', '') == 'uno_start':
                child.disabled = len(self.game.players) < 2
                break
        await interaction.response.edit_message(embed=self._embed(interaction.guild), view=self)
        await interaction.followup.send(f'已加入 {cpu_name(new_cpu)} 🤖', ephemeral=True)

    @discord.ui.button(label='▶️ 開始', style=discord.ButtonStyle.primary,
                       custom_id='uno_start', disabled=True)
    async def start_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        host_id = self.cog._host_ids.get(self.game.guild_id)
        if interaction.user.id != host_id:
            await interaction.response.send_message('只有房主可以開始', ephemeral=True)
            return
        if len(self.game.players) < 2:
            await interaction.response.send_message('至少需要 2 人', ephemeral=True)
            return
        self.stop()
        self.game.current_idx = 0
        sv = StatusView(self.game, self.cog)
        self.cog.status_views[self.game.guild_id] = sv
        # Close the lobby message (consume the interaction)
        await interaction.response.edit_message(content='🎴 遊戲開始！', embed=None, view=None)
        self.cog.start_turn_timer(self.game, interaction.channel)
        # Post the board + (if human) the turn @mention as ONE message at the
        # bottom — same as every later turn, so it gets cleaned up next repost.
        await self.cog.update_status(
            self.game, interaction.guild, content=self.cog._turn_ping(self.game))
        await self.cog._check_cpu_turn(interaction.channel)

    async def on_timeout(self):
        self.cog.games.pop(self.game.guild_id, None)
        self.cog._host_ids.pop(self.game.guild_id, None)
        self.cog._cancel_cpu_task(self.game.guild_id)
        if self.message:
            try:
                await self.message.edit(content='⏰ Uno 部屋已逾時取消', embed=None, view=None)
            except Exception:
                pass

    def _add_human(self, uid: int):
        self.game.players.append(uid)
        _deal_fresh_hands(self.game)


# ── ColorPickerView ──

class ColorPickerView(discord.ui.View):
    """Color picker for wild cards — shown in the player's ephemeral message."""
    def __init__(self, game: UNOGame, player_id: int, cog: 'Uno'):
        super().__init__(timeout=30)
        self.game = game
        self.player_id = player_id
        self.cog = cog
        self._chosen: Optional[Color] = None

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True

    def _color_btn(self, label: str, color: discord.ButtonStyle, pick: Color):
        btn = discord.ui.Button(label=label, style=color, custom_id=f'color_{pick}')
        
        async def cb(interaction: discord.Interaction):
            if interaction.user.id != self.player_id:
                await interaction.response.send_message('不是你選的牌', ephemeral=True)
                return
            self._chosen = pick
            for c in self.children:
                c.disabled = True
            await interaction.response.edit_message(view=self)
            self.stop()
        
        btn.callback = cb
        return btn

    def add_color_buttons(self):
        self.add_item(self._color_btn('🔴 紅', discord.ButtonStyle.danger, Color.RED))
        self.add_item(self._color_btn('🟡 黃', discord.ButtonStyle.secondary, Color.YELLOW))
        self.add_item(self._color_btn('🟢 綠', discord.ButtonStyle.success, Color.GREEN))
        self.add_item(self._color_btn('🔵 藍', discord.ButtonStyle.primary, Color.BLUE))


# ── StatusView ──

class StatusView(discord.ui.View):
    def __init__(self, game: UNOGame, cog: 'Uno'):
        super().__init__(timeout=None)
        self.game = game
        self.cog = cog

    @discord.ui.button(label='🎴 看牌 / 出牌', style=discord.ButtonStyle.primary)
    async def show_hand_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.game.game_over:
            await interaction.response.send_message('遊戲已結束', ephemeral=True)
            return
        uid = interaction.user.id
        if uid not in self.game.players or is_cpu(uid):
            await interaction.response.send_message('輪到電腦出牌中…', ephemeral=True)
            return
        if uid != self.game.current_player:
            hand = self.game.hands.get(uid, [])
            file = _hand_file(hand, set())
            await interaction.response.send_message(
                f'你有 **{len(hand)}** 張牌，還沒輪到你', file=file, ephemeral=True)
            return
        hand = self.game.hands[uid]
        sel = self.game.selected.get(uid, set())
        file = _hand_file(hand, sel)
        hv = HandView(self.game, uid, self.cog)
        await interaction.response.send_message(
            embed=hv.build_embed(), file=file, view=hv, ephemeral=True)
        self.cog.reset_turn_timer(self.game, interaction.channel)

    @discord.ui.button(label='🏆 排行榜', style=discord.ButtonStyle.secondary)
    async def stats_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _send_stats(interaction)


# ── HandView ──

class HandView(discord.ui.View):
    PAGE_SIZE = 15  # 3 rows of 5 card buttons; leaves a nav row + an action row

    def __init__(self, game: UNOGame, uid: int, cog: 'Uno'):
        super().__init__(timeout=None)
        self.game = game
        self.uid = uid
        self.cog = cog
        self.page = 0
        self._rebuild()

    def _card_label(self, card: Card) -> str:
        val = card.value
        if val == Action.WILD:
            return '🌈 萬能'
        if val == Action.WILD_DRAW_FOUR:
            return '🌈 萬能+4'
        emoji = COLOR_EMOJI.get(card.color, '⚫')
        if val == Action.SKIP:
            sym = '跳轉'
        elif val == Action.REVERSE:
            sym = '反轉'
        elif val == Action.DRAW_TWO:
            sym = '+2'
        else:
            sym = str(val)
        return f'{emoji} {sym}'

    def _rebuild(self):
        self.clear_items()
        hand = self.game.hands.get(self.uid, [])
        selected = self.game.selected.get(self.uid, set())
        n = len(hand)
        pages = max(1, (n + self.PAGE_SIZE - 1) // self.PAGE_SIZE)
        self.page = max(0, min(self.page, pages - 1))
        start = self.page * self.PAGE_SIZE
        # Card buttons for the current page (rows 0–2), using GLOBAL indices
        for j, card in enumerate(hand[start:start + self.PAGE_SIZE]):
            gidx = start + j
            style = discord.ButtonStyle.success if gidx in selected else discord.ButtonStyle.secondary
            btn = discord.ui.Button(label=self._card_label(card), style=style,
                                    custom_id=f'uno_card_{gidx}', row=j // 5)
            btn.callback = self._make_toggle(gidx)
            self.add_item(btn)
        # Pagination row (row 3) — only when the hand spans multiple pages
        if pages > 1:
            prev_btn = discord.ui.Button(label='◀ 上一頁', style=discord.ButtonStyle.secondary,
                                         row=3, disabled=self.page == 0)
            prev_btn.callback = self._make_page(-1)
            info_btn = discord.ui.Button(label=f'第 {self.page + 1}/{pages} 頁',
                                         style=discord.ButtonStyle.secondary, row=3, disabled=True)
            next_btn = discord.ui.Button(label='下一頁 ▶', style=discord.ButtonStyle.secondary,
                                         row=3, disabled=self.page >= pages - 1)
            next_btn.callback = self._make_page(1)
            self.add_item(prev_btn)
            self.add_item(info_btn)
            self.add_item(next_btn)
        # Action row (row 4). After drawing this turn the 摸牌 button becomes 過牌.
        drew = bool(self.game.drawn_cards.get(self.uid))
        play_btn = discord.ui.Button(label='出牌 ✅', style=discord.ButtonStyle.primary, row=4)
        play_btn.callback = self._play_cb
        self.add_item(play_btn)
        if drew:
            pass_btn = discord.ui.Button(label='過牌 ⏭️', style=discord.ButtonStyle.secondary, row=4)
            pass_btn.callback = self._pass_cb
            self.add_item(pass_btn)
        else:
            draw_btn = discord.ui.Button(label='摸牌 🎴', style=discord.ButtonStyle.secondary, row=4)
            draw_btn.callback = self._draw_cb
            self.add_item(draw_btn)

    def _make_page(self, delta: int):
        async def cb(interaction: discord.Interaction):
            if interaction.user.id != self.uid:
                await interaction.response.send_message('這不是你的手牌', ephemeral=True)
                return
            self.page += delta
            self._rebuild()
            hand = self.game.hands[self.uid]
            file = _hand_file(hand, self.game.selected[self.uid])
            await interaction.response.edit_message(
                embed=self.build_embed(), attachments=[file], view=self)
        return cb

    def _make_toggle(self, idx: int):
        async def cb(interaction: discord.Interaction):
            if interaction.user.id != self.uid:
                await interaction.response.send_message('這不是你的手牌', ephemeral=True)
                return
            # Single-select: clicking a card selects only it (re-click to clear)
            sel = self.game.selected[self.uid]
            if idx in sel:
                sel.clear()
            else:
                sel.clear()
                sel.add(idx)
            self._rebuild()
            hand = self.game.hands[self.uid]
            file = _hand_file(hand, self.game.selected[self.uid])
            await interaction.response.edit_message(
                embed=self.build_embed(), attachments=[file], view=self)
        return cb

    async def _pick_color(self, interaction) -> 'Color':
        """Show color-picker in the player's own ephemeral message, wait for the
        result, and return the chosen Color. Kept private so other players can't
        see the colour being chosen for a wild card."""
        color_view = ColorPickerView(self.game, self.uid, self.cog)
        color_view.add_color_buttons()
        # Replace the ephemeral hand picker with the colour buttons (private)
        await interaction.response.edit_message(
            content='🌈 萬能牌！請選顏色：',
            embed=None, attachments=[], view=color_view)
        try:
            await color_view.wait()
        except asyncio.TimeoutError:
            return random_color()
        chosen = color_view._chosen
        return chosen if chosen else random_color()

    async def _ack_edit(self, interaction, already_responded: bool, **kwargs):
        """Edit the ephemeral picker, acknowledging the interaction correctly.
        If the interaction was already responded to (wild colour flow), edit the
        original response; otherwise respond now so Discord doesn't show
        'interaction failed'."""
        try:
            if already_responded:
                await interaction.edit_original_response(**kwargs)
            else:
                await interaction.response.edit_message(**kwargs)
        except Exception:
            pass

    async def _play_cb(self, interaction: discord.Interaction):
        if interaction.user.id != self.uid:
            await interaction.response.send_message('不是你的回合', ephemeral=True)
            return
        sel = sorted(self.game.selected.get(self.uid, set()))
        if not sel:
            await interaction.response.send_message('請先點選要出的牌', ephemeral=True)
            return
        idx = sel[0]
        card = self.game.hands[self.uid][idx]
        player_name = _member_name(interaction.guild, self.uid)
        # Stop the turn timer now so it can't fire while the player picks a
        # wild colour (which can take up to 30s).
        self.cog.cancel_turn_timer(self.game.guild_id)
        chosen_color = None
        responded = False
        # If wild card, ask player to pick a color via channel (this responds)
        if card.is_wild():
            chosen_color = await self._pick_color(interaction)
            responded = True
        # Actually play the card
        ok, msg = self.game.play_card(self.uid, idx, chosen_color=chosen_color)
        if not ok:
            # Play failed — restart the timer since it's still this player's turn
            self.cog.start_turn_timer(self.game, interaction.channel)
            await self._ack_edit(interaction, responded, content=f'❌ {msg}',
                                 embed=self.build_embed(), attachments=[], view=self)
            return
        # Close the ephemeral hand picker
        await self._ack_edit(interaction, responded, content='✅ 出牌成功！',
                             embed=None, attachments=[], view=None)
        color_note = ''
        if card.is_wild() and chosen_color:
            color_note = f' → {COLOR_EMOJI.get(chosen_color, "?")}'
        effect_desc = self.cog._format_effect(msg)
        # Update persistent action message with play result
        channel_msg = f'🎴 **{player_name}** → 出 {card.display()}{color_note}{effect_desc}'
        await self.cog._update_action(interaction.channel, self.game, channel_msg)
        if msg == 'win':
            await self.cog._update_action(interaction.channel, self.game, f'🏆 **{player_name}** 贏了！')
            await self.cog.finish_game(interaction.guild, interaction.channel, self.game)
        else:
            await self.cog.after_action(interaction.guild, interaction.channel, self.game)

    async def _draw_cb(self, interaction: discord.Interaction):
        if interaction.user.id != self.uid:
            await interaction.response.send_message('不是你的回合', ephemeral=True)
            return
        if self.game.drawn_cards.get(self.uid):
            await interaction.response.send_message('你這回合已經摸過牌了', ephemeral=True)
            return
        drawn = self.game.draw(self.uid, 1)
        # Keep the turn open: the player may now play the drawn card or 過牌.
        self.cog.reset_turn_timer(self.game, interaction.channel)
        hand = self.game.hands[self.uid]
        self.game.clear_sel(self.uid)
        self._rebuild()
        file = _hand_file(hand, set())
        player_name = _member_name(interaction.guild, self.uid)
        await interaction.response.edit_message(
            content=f'🎴 摸了 **{len(drawn)}** 張牌，可以出牌或過牌',
            embed=self.build_embed(), attachments=[file], view=self)
        await self.cog._update_action(interaction.channel, self.game,
            f'🎴 **{player_name}** 摸了一張牌')

    async def _pass_cb(self, interaction: discord.Interaction):
        if interaction.user.id != self.uid:
            await interaction.response.send_message('不是你的回合', ephemeral=True)
            return
        if not self.game.drawn_cards.get(self.uid):
            await interaction.response.send_message('要先摸牌才能過牌', ephemeral=True)
            return
        player_name = _member_name(interaction.guild, self.uid)
        self.cog.cancel_turn_timer(self.game.guild_id)
        # End the turn: clear this player's drawn/selected state, then advance.
        self.game.drawn_cards[self.uid] = []
        self.game.clear_sel(self.uid)
        self.game._next()
        await interaction.response.edit_message(
            content='⏭️ 已過牌', embed=None, attachments=[], view=None)
        await self.cog._update_action(interaction.channel, self.game,
            f'⏭️ **{player_name}** 過牌')
        await self.cog.after_action(interaction.guild, interaction.channel, self.game)

    def build_embed(self) -> discord.Embed:
        hand = self.game.hands.get(self.uid, [])
        sel = self.game.selected.get(self.uid, set())
        sel_cards = [hand[i] for i in sorted(sel) if i < len(hand)]
        sel_str = '  '.join(c.display() for c in sel_cards) if sel_cards else '（尚未選擇）'
        top_color = COLOR_EMOJI.get(self.game.current_color, '?')
        val = self.game.current_value
        if val is None:
            top_display = '? 未知'
        elif val == Action.SKIP:
            top_display = f'{top_color} ⊘ 跳轉'
        elif val == Action.REVERSE:
            top_display = f'{top_color} ⟲ 反轉'
        elif val == Action.DRAW_TWO:
            top_display = f'{top_color} +2 摸2'
        elif val == Action.WILD:
            top_display = f'{top_color} W 萬能'
        elif val == Action.WILD_DRAW_FOUR:
            top_display = f'{top_color} +4 萬能+4'
        else:
            top_display = f'{top_color} **{val}**'
        embed = discord.Embed(title=f'你的手牌（{len(hand)} 張）', color=0xe74c3c)
        embed.add_field(name='🏔 頂牌', value=top_display, inline=False)
        embed.add_field(name='已選擇', value=sel_str, inline=False)
        embed.set_image(url='attachment://uno_hand.png')
        return embed


# ── Stats helper ──

async def _send_stats(interaction: discord.Interaction):
    stats = _load_stats()
    if not stats:
        await interaction.response.send_message('還沒有任何紀錄', ephemeral=True)
        return
    ranked = sorted(stats.items(), key=lambda x: x[1]['wins'], reverse=True)
    lines = []
    medals = ['🥇', '🥈', '🥉']
    for rank, (uid_str, data) in enumerate(ranked[:10]):
        wins = data['wins']
        games = data['games']
        rate = f"{wins / games * 100:.0f}%" if games > 0 else "0%"
        name = _member_name(interaction.guild, int(uid_str))
        medal = medals[rank] if rank < 3 else f'{rank + 1}.'
        lines.append(f'{medal} **{name}**：{wins} 勝 / {games} 場 ({rate})')
    embed = discord.Embed(title='🏆 Uno 排行榜', color=0xe74c3c, description='\n'.join(lines))
    await interaction.response.send_message(embed=embed, ephemeral=True)


# ── Cog ──

class Uno(commands.Cog):
    MAX_PLAYERS = 10
    _cpu_slot_counter = 0

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.games: dict[int, UNOGame] = {}
        self._host_ids: dict[int, int] = {}
        self.status_msgs: dict[int, discord.Message] = {}
        self.status_views: dict[int, StatusView] = {}
        self.turn_tasks: dict[int, asyncio.Task] = {}
        self._cpu_tasks: dict[int, asyncio.Task] = {}
        self.action_msgs: dict[int, discord.Message] = {}  # persistent action message per game

    def _top_card_display(self, game: UNOGame) -> str:
        top_color = COLOR_EMOJI.get(game.current_color, '?')
        val = game.current_value
        if val is None:
            return '? 未知'
        if val == Action.SKIP:
            sym = '⊘ 跳轉'
        elif val == Action.REVERSE:
            sym = '⟲ 反轉'
        elif val == Action.DRAW_TWO:
            sym = '+2 摸2'
        elif val == Action.WILD:
            sym = '萬能'
        elif val == Action.WILD_DRAW_FOUR:
            sym = '萬能+4'
        else:
            sym = str(val)
        return f'{top_color} **{sym}**'

    def build_status_embed(self, game: UNOGame, guild: discord.Guild) -> discord.Embed:
        counts = game.card_counts()
        lines = []
        for pid in game.players:
            name = _member_name(guild, pid)
            n = counts.get(pid, 0)
            arrow = ' ◀' if pid == game.current_player else ''
            cpu_tag = ' 🤖' if is_cpu(pid) else ''
            # Card count on the right; show UNO! automatically at 1 card
            status = '📢 **UNO!**' if n == 1 else f'{n} 張'
            lines.append(f'• {name}{cpu_tag}{arrow} ｜ {status}')
        embed = discord.Embed(title='🎴 Uno 進行中', color=0xe74c3c)
        embed.add_field(name='玩家手牌數', value='\n'.join(lines), inline=False)
        embed.add_field(name='🏔 頂牌', value=self._top_card_display(game), inline=False)
        cur_name = _member_name(guild, game.current_player)
        if game.is_current_cpu:
            emb = f'🤖 **{cur_name}** 思考中…'
        else:
            emb = f'**{cur_name}**，請點下方按鈕出牌'
        embed.add_field(name='輪到', value=emb, inline=False)
        return embed

    async def update_status(self, game: UNOGame, guild: discord.Guild, content: str = None):
        """Re-post the status board at the BOTTOM of the channel so the running
        game log never buries it, then delete the previous board message.
        *content* (e.g. a turn @mention) rides on the same message."""
        gid = game.guild_id
        sv = self.status_views.get(gid)
        if sv is None:
            return
        old = self.status_msgs.get(gid)
        channel = old.channel if old else self.bot.get_channel(game.channel_id)
        if channel is None:
            return
        try:
            new_msg = await channel.send(
                content=content, embed=self.build_status_embed(game, guild), view=sv)
        except Exception as e:
            log.error(f'update_status repost error: {e}')
            return
        self.status_msgs[gid] = new_msg
        if old:
            try:
                await old.delete()
            except Exception:
                pass

    # ── Turn timer ──

    def start_turn_timer(self, game: UNOGame, channel):
        gid = game.guild_id
        self.cancel_turn_timer(gid)
        self.turn_tasks[gid] = asyncio.create_task(self._turn_timeout(game, channel))

    def reset_turn_timer(self, game: UNOGame, channel):
        self.start_turn_timer(game, channel)

    def cancel_turn_timer(self, gid: int):
        t = self.turn_tasks.pop(gid, None)
        if t:
            t.cancel()

    async def _turn_timeout(self, game: UNOGame, channel):
        await asyncio.sleep(TURN_TIMEOUT)
        if game.game_over:
            return
        uid = game.current_player
        name = _member_name(channel.guild, uid)
        game.pass_turn(uid)
        await self._update_action(channel, game, f'⏱️ {name} 逾時，自動摸牌過牌')
        self.cancel_turn_timer(game.guild_id)
        self.start_turn_timer(game, channel)
        # Re-post the board LAST (with the next player's @mention) so it sits at the bottom
        await self.update_status(game, channel.guild, content=self._turn_ping(game))
        await self._check_cpu_turn(channel)

    # ── Effect formatting ──

    @staticmethod
    def _format_effect(msg: str) -> str:
        if 'effect_skip:' in msg:
            return ' — 跳過下一位！⊘'
        if 'effect_reverse:' in msg:
            return ' — 方向反轉！⟲'
        if 'effect_draw2:' in msg:
            return ' — 下一位摸 2 張！+2'
        if 'effect_wild4:' in msg:
            return ' — 下一位摸 4 張！+4'
        return ''

    async def _update_action(self, channel, game: UNOGame, text: str):
        """Append a new line to the running game log (one message per event)."""
        new_msg = await channel.send(text)
        self.action_msgs[game.guild_id] = new_msg
        return new_msg

    # ── CPU auto-play ──

    def _add_cpu_player(self, game: UNOGame) -> int:
        Uno._cpu_slot_counter += 1
        cpu_id = -1000 - Uno._cpu_slot_counter
        game.players.append(cpu_id)
        _deal_fresh_hands(game)
        return cpu_id

    def _cancel_cpu_task(self, gid: int):
        t = self._cpu_tasks.pop(gid, None)
        if t:
            t.cancel()

    async def _cpu_play_once(self, game: UNOGame, channel):
        """Run one CPU turn — sends all CPU activity as a SINGLE message."""
        if game.game_over or not game.is_current_cpu:
            return

        cpu_id = game.current_player
        name = _member_name(channel.guild, cpu_id)
        # "Thinking" is shown in the live status embed, not the log
        await asyncio.sleep(CPU_PLAY_DELAY)

        idx = ai_pick_card(game, cpu_id)

        if idx is not None:
            card = game.hands[cpu_id][idx]
            chosen = None
            color_note = ''
            if card.is_wild():
                chosen = ai_choose_color(game, cpu_id)
                emoji = COLOR_EMOJI.get(chosen, '?')
                color_note = f' → {emoji}'
            ok, msg = game.play_card(cpu_id, idx, chosen_color=chosen)
            if ok:
                effect = ''
                if msg != 'win':
                    effect = self._format_effect(msg)
                await self._update_action(channel, game,
                    f'🤖 **{name}** → 出 {card.display()}{color_note}{effect}')
                if msg == 'win':
                    await self._update_action(channel, game, f'🏆 **{name}** 🤖 贏了！')
                    self.cancel_turn_timer(game.guild_id)
                    self._cancel_cpu_task(game.guild_id)
                    await self.finish_game(channel.guild, channel, game)
                    return
        else:
            game.pass_turn(cpu_id)
            await self._update_action(channel, game, f'🤖 **{name}** 沒有可出的牌，摸牌過牌')

        if game.game_over:
            return
        # Hand off exactly like a human turn: start timer + ping next human
        # (or chain into the next CPU), then re-post the board at the bottom.
        await self.after_action(channel.guild, channel, game)

    async def _check_cpu_turn(self, channel):
        gid = channel.guild.id
        game = self.games.get(gid)
        if not game or game.game_over:
            return
        if game.is_current_cpu:
            self.cancel_turn_timer(gid)
            self._cancel_cpu_task(gid)
            self._cpu_tasks[gid] = asyncio.create_task(self._cpu_play_once(game, channel))

    # ── After action / finish ──

    async def after_action(self, guild: discord.Guild, channel, game: UNOGame):
        self.start_turn_timer(game, channel)
        # Re-post the board LAST (with the next player's @mention) at the bottom
        await self.update_status(game, guild, content=self._turn_ping(game))
        await self._check_cpu_turn(channel)

    def _turn_ping(self, game: UNOGame) -> Optional[str]:
        """@mention text for the current human's turn (None for CPU/over)."""
        if game.game_over or game.is_current_cpu:
            return None
        return f'🎴 輪到 <@{game.current_player}>，請點下方狀態列的「看牌 / 出牌」'

    async def finish_game(self, guild: discord.Guild, channel, game: UNOGame, record: bool = True):
        self.cancel_turn_timer(game.guild_id)
        self._cancel_cpu_task(game.guild_id)
        winner_id = game.winner
        # Only record stats for a real win (not a forfeit)
        if record and winner_id is not None:
            _record_result(winner_id, game.players)
        if winner_id is not None:
            wname = _member_name(guild, winner_id)
            cpu_tag = ' 🤖' if is_cpu(winner_id) else ''
            embed = discord.Embed(title='🎉 Uno 遊戲結束！', color=0xf39c12)
            embed.add_field(name='勝者', value=f'🥇 **{wname}**{cpu_tag}', inline=False)
        else:
            embed = discord.Embed(title='🏳️ Uno 遊戲已強制結束', color=0xf39c12)
        lines = []
        for pid in game.players:
            name = _member_name(guild, pid)
            remaining = len(game.hands.get(pid, []))
            tag = ' 🤖' if is_cpu(pid) else ''
            lines.append(f'• {name}{tag}：剩 {remaining} 張')
        embed.add_field(name='各玩家結果', value='\n'.join(lines), inline=False)
        sv = self.status_views.get(game.guild_id)
        if sv:
            sv.stop()
        msg = self.status_msgs.get(game.guild_id)
        if msg:
            try:
                await msg.edit(embed=embed, view=None)
            except Exception:
                pass
        gid = game.guild_id
        self.games.pop(gid, None)
        self.status_msgs.pop(gid, None)
        self.status_views.pop(gid, None)
        self._host_ids.pop(gid, None)
        self._cpu_tasks.pop(gid, None)
        self.turn_tasks.pop(gid, None)
        # Keep the game log in the channel — don't delete it on finish
        self.action_msgs.pop(gid, None)

    # ── Commands ──

    @app_commands.command(name='uno', description='開一局 Uno（2~10 人，可加入電腦）')
    @app_commands.describe(cpu_count='要加入的電腦玩家數量（0~8）')
    async def uno_cmd(self, interaction: discord.Interaction, cpu_count: int = 0):
        if not interaction.guild:
            await interaction.response.send_message('請在伺服器頻道中使用', ephemeral=True)
            return
        gid = interaction.guild_id
        if gid in self.games:
            await interaction.response.send_message('這個伺服器已有 Uno 進行中', ephemeral=True)
            return
        cpu_count = max(0, min(cpu_count, self.MAX_PLAYERS - 1))
        players = [interaction.user.id]
        for _ in range(cpu_count):
            Uno._cpu_slot_counter += 1
            players.append(-1000 - Uno._cpu_slot_counter)

        game = UNOGame(players=players, guild_id=gid, channel_id=interaction.channel_id)
        self.games[gid] = game
        self._host_ids[gid] = interaction.user.id

        view = LobbyView(game, self)
        embed = view._embed(interaction.guild)
        for child in view.children:
            if getattr(child, 'custom_id', '') == 'uno_start':
                child.disabled = len(game.players) < 2
        cpu_info = f' + {cpu_count} 位電腦 🤖' if cpu_count else ''
        await interaction.response.send_message(
            content=f'房主 {_member_name(interaction.guild, interaction.user.id)} 開了局{cpu_info}',
            embed=embed, view=view)
        view.message = await interaction.original_response()

    @app_commands.command(name='uno_leave', description='離開正在進行的 Uno')
    async def leave_cmd(self, interaction: discord.Interaction):
        gid = interaction.guild_id
        game = self.games.get(gid)
        if not game:
            await interaction.response.send_message('沒有進行中的 Uno', ephemeral=True)
            return
        uid = interaction.user.id
        if uid not in game.players:
            await interaction.response.send_message('你不在遊戲中', ephemeral=True)
            return
        human_count = sum(1 for p in game.players if not is_cpu(p))
        if human_count <= 1:
            await interaction.response.send_message('只剩你一個人了，不能離開', ephemeral=True)
            return
        host_id = self._host_ids.get(gid)
        if uid == host_id:
            new_host = [p for p in game.players if not is_cpu(p) and p != uid]
            if new_host:
                self._host_ids[gid] = new_host[0]

        # Remove the player and keep the turn pointer valid
        idx = game.players.index(uid)
        was_current = (idx == game.current_idx)
        game.players.remove(uid)
        game.deck.extend(game.hands.pop(uid, []))
        game.selected.pop(uid, None)
        game.drawn_cards.pop(uid, None)
        game.uno_called.pop(uid, None)
        if idx < game.current_idx:
            game.current_idx -= 1
        game.current_idx %= len(game.players)

        await interaction.response.send_message('已離開 Uno', ephemeral=True)

        if was_current and not game.game_over:
            # The turn now belongs to whoever slid into this slot — start them.
            self.cancel_turn_timer(gid)
            self.start_turn_timer(game, interaction.channel)
            await self.update_status(game, interaction.guild, content=self._turn_ping(game))
            await self._check_cpu_turn(interaction.channel)
        else:
            await self.update_status(game, interaction.guild)

    @app_commands.command(name='uno_stats', description='查看 Uno 勝負統計')
    async def stats_cmd(self, interaction: discord.Interaction):
        await _send_stats(interaction)

    @app_commands.command(name='uno_forfeit', description='房主強制結束並宣布遊戲結束')
    async def forfeit_cmd(self, interaction: discord.Interaction):
        gid = interaction.guild_id
        game = self.games.get(gid)
        if not game:
            await interaction.response.send_message('沒有進行中的 Uno', ephemeral=True)
            return
        host_id = self._host_ids.get(gid)
        if interaction.user.id != host_id:
            await interaction.response.send_message('只有房主可以強制結束', ephemeral=True)
            return
        await interaction.response.send_message('🏳️ 房主宣布結束遊戲！', ephemeral=True)
        game.game_over = True
        # Forfeit has no winner — don't record stats
        game.winner = None
        await self.finish_game(interaction.guild, interaction.channel, game, record=False)


async def setup(bot: commands.Bot):
    global _bot
    _bot = bot
    Uno._cpu_slot_counter = 0
    await bot.add_cog(Uno(bot))
