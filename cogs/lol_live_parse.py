"""/lol-live：Riot 官方 spectator-v5 的進行中對局 → 文字 embed 用的資料。純函式，方便測試。

資料來源分兩層：
  API 層(不需要玩家 PC)：spectator-v5 對局本體 + league/summoner/mastery/match-v5 補每個人
  客戶端層(PC 開著、而且這場就是玩家自己在打的那場)：Live Client API 的 K/D/A、CS、裝備
"""
from datetime import datetime, timezone
from typing import Optional

from cogs.lol_parse import queue_name

TIER_ZH = {'IRON': '鐵牌', 'BRONZE': '銅牌', 'SILVER': '銀牌', 'GOLD': '金牌', 'PLATINUM': '白金',
           'EMERALD': '翡翠', 'DIAMOND': '鑽石', 'MASTER': '大師', 'GRANDMASTER': '宗師', 'CHALLENGER': '菁英'}
MAP_ZH = {11: '召喚峽谷', 12: '嚎哭深淵', 21: '極限閃擊', 30: '鬥魂競技場', 33: '群體屠殺'}
TEAM_ZH = {100: '藍方', 200: '紅方'}
RECENT_N = 3   # 每人補幾場 match-v5(10 人 × 3 場 + 每人 4 次基本查詢 ≈ 70 次請求，個人 key 兩分鐘 100 次)


def parse_spectator(d: dict, static: dict) -> dict:
    """spectator-v5 active-games → {gameId, queue, map, mode, started, length, bans:{teamId:[name]}, teams:[{id, name, players}]}。
    players 只放對局本體給的東西(英雄、召喚師技能、主符文、Riot ID)，其他欄位由呼叫端補。"""
    champ = static.get('champNames') or {}
    spell = static.get('spellKeys') or {}
    spell_id = static.get('spellIdToKey') or {}
    perk = static.get('perkNames') or {}
    style = static.get('perkStyles') or {}

    def spell_name(sid) -> str:
        key = spell_id.get(str(sid))
        return spell.get(key) or (f'技能{sid}' if sid else '')

    bans: dict[int, list[str]] = {}
    for b in d.get('bannedChampions') or []:
        cid = b.get('championId')
        bans.setdefault(b.get('teamId'), []).append(champ.get(str(cid), str(cid)) if cid and cid > 0 else '無')
    teams: dict[int, list] = {}
    for p in d.get('participants') or []:
        perks = p.get('perks') or {}
        ids = perks.get('perkIds') or []
        teams.setdefault(p.get('teamId'), []).append({
            'puuid': p.get('puuid', ''),
            'riotId': p.get('riotId') or '',
            'bot': bool(p.get('bot')),
            'championId': p.get('championId'),
            'champion': champ.get(str(p.get('championId')), str(p.get('championId'))),
            'spells': [s for s in (spell_name(p.get('spell1Id')), spell_name(p.get('spell2Id'))) if s],
            'keystone': perk.get(str(ids[0]), '') if ids else '',
            'style': style.get(str(perks.get('perkStyle')), ''),
            'subStyle': style.get(str(perks.get('perkSubStyle')), ''),
            'icon': p.get('profileIconId'),
        })
    start_ms = d.get('gameStartTime') or 0   # 開局前幾分鐘 Riot 常給 0
    qid = d.get('gameQueueConfigId')
    return {
        'gameId': d.get('gameId'),
        'queueId': qid,
        'queue': queue_name(qid, d.get('gameMode') or ''),
        'mode': d.get('gameMode') or '',
        'map': MAP_ZH.get(d.get('mapId'), f"地圖 {d.get('mapId')}"),
        'started': datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc) if start_ms > 0 else None,
        'length': d.get('gameLength') or 0,
        'bans': bans,
        'teams': [{'id': tid, 'name': TEAM_ZH.get(tid, f'隊伍 {tid}'), 'players': pl}
                  for tid, pl in sorted(teams.items())],
    }


def rank_text(r: Optional[dict]) -> str:
    if not r:
        return '未排名'
    wr = r['w'] * 100 // max(1, r['w'] + r['l'])
    div = f" {r['division']}" if r.get('division') else ''
    return f"{TIER_ZH.get(r['tier'], r['tier'])}{div} {r['lp']}LP（{r['w']}勝{r['l']}敗 {wr}%）"


def mastery_text(m: Optional[dict]) -> str:
    if not m:
        return ''
    return f"熟練 Lv{m.get('championLevel', 0)} {m.get('championPoints', 0):,}"


def recent_text(matches: list, puuid: str, champ_names: dict) -> str:
    """match-v5 幾場 → '近3 2勝1敗 KDA 2.1（亞菲利歐, 伊澤瑞爾, 犽宿）'。沒有資料回空字串。"""
    w = l = k = d = a = 0
    champs = []
    for m in matches:
        info = (m or {}).get('info') or {}
        me = next((x for x in info.get('participants') or [] if x.get('puuid') == puuid), None)
        if not me:
            continue
        if (info.get('gameDuration') or 0) < 300:
            continue   # 重開不算
        w += 1 if me.get('win') else 0
        l += 0 if me.get('win') else 1
        k, d, a = k + (me.get('kills') or 0), d + (me.get('deaths') or 0), a + (me.get('assists') or 0)
        champs.append(champ_names.get(str(me.get('championId')), me.get('championName') or '?'))
    if not w + l:
        return ''
    return f"近{w + l} {w}勝{l}敗 KDA {(k + a) / max(1, d):.1f}（{', '.join(champs)}）"


def live_text(lp: Optional[dict], item_names: dict) -> str:
    """Live Client(客戶端層)一位玩家 → 'Lv12 4/1/6 CS 120 ⚔ 裝備…'。"""
    if not lp:
        return ''
    sc = lp.get('scores') or {}
    items = [item_names.get(str(i.get('itemID')), {}).get('name', '') for i in lp.get('items') or []
             if i.get('itemID') and i.get('itemID') not in (3340, 3363, 3364)]   # 飾品不列
    items = [x for x in items if x]
    s = f"Lv{lp.get('level', '?')} {sc.get('kills', 0)}/{sc.get('deaths', 0)}/{sc.get('assists', 0)} CS {sc.get('creepScore', 0)}"
    if lp.get('isDead'):
        s += f"（死亡，{int(lp.get('respawnTimer') or 0)} 秒）"
    if items:
        s += '　' + '、'.join(items)
    return s


def match_live_players(live: Optional[dict], players: list[dict]) -> dict[str, dict]:
    """Live Client allgamedata 的 allPlayers 用 Riot ID 對到 spectator 的玩家 → {puuid: liveplayer}。"""
    if not live:
        return {}
    by_rid = {}
    for lp in live.get('allPlayers') or []:
        rid = (lp.get('riotIdGameName') or lp.get('summonerName') or '').lower()
        tag = (lp.get('riotIdTagLine') or '').lower()
        by_rid[f'{rid}#{tag}' if tag else rid] = lp
    out = {}
    for p in players:
        rid = p['riotId'].lower()
        lp = by_rid.get(rid) or by_rid.get(rid.split('#')[0])
        if lp:
            out[p['puuid']] = lp
    return out


def fmt_length(secs: int) -> str:
    return f'{secs // 60} 分 {secs % 60:02d} 秒'
