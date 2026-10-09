"""把 LoL 客戶端(LCU)與 Live Client Data API 的原始 JSON 整理成 Activity 要的格式。

全部是純函式，不碰網路，方便用錄下來的資料測試。
"""
import re
from typing import Optional

# 佇列名稱：客戶端給的 description 有時是空的，常見的自己補
QUEUE_NAMES = {
    400: '一般對戰(選秀)', 420: '單/雙積分', 430: '一般對戰(盲選)', 440: '彈性積分', 450: '隨機單中',
    490: '快速對戰', 700: '衝突', 900: '阿福快打', 1700: '鬥魂競技場', 1900: '阿福快打', 2400: '隨機單中：大混戰',
}
APEX = {'MASTER', 'GRANDMASTER', 'CHALLENGER'}

# 對局進行中的 gameflow phase
INGAME_PHASES = {'GameStart', 'InProgress', 'Reconnect'}
EOG_PHASES = {'WaitingForStats', 'PreEndOfGame', 'EndOfGame'}

_SPELL_RE = re.compile(r'GeneratedTip_SummonerSpell_(\w+?)_DisplayName')
_CHAMP_RE = re.compile(r'game_character_displayname_(\w+)')


def queue_name(qid: Optional[int], desc: str = '') -> str:
    if qid in QUEUE_NAMES:
        return QUEUE_NAMES[qid]
    return desc or (f'佇列 {qid}' if qid else '')


def riot_id(name: str, tag: str) -> str:
    return f'{name}#{tag}' if name else ''


# ── 選角 ──
def parse_champselect(sess: dict, my_puuid: str) -> dict:
    team = []
    for p in sess.get('myTeam') or []:
        hidden = p.get('nameVisibilityType') == 'HIDDEN' or not p.get('gameName')
        team.append({
            # 被隱藏的人連 puuid 都不帶，避免拿去查牌位/戰績(等於破解匿名)
            'puuid': None if hidden else (p.get('puuid') or None),
            'name': '' if hidden else p.get('gameName', ''),
            'tag': '' if hidden else p.get('tagLine', ''),
            'hidden': hidden,
            'championId': p.get('championId') or p.get('championPickIntent') or 0,
            'me': p.get('cellId') == sess.get('localPlayerCellId'),
        })
    timer = sess.get('timer') or {}
    return {
        'bench': [b.get('championId') for b in sess.get('benchChampions') or [] if b.get('championId')],
        'benchEnabled': bool(sess.get('benchEnabled')),
        'team': team,
        'enemyCount': len(sess.get('theirTeam') or []),
        'phase': timer.get('phase', ''),
        'rerolls': sess.get('rerollsRemaining'),
    }


def session_puuids(sess: dict) -> list[str]:
    """遊戲進行中的 gameflow session：雙方可見玩家的 puuid(被隱藏的人不會出現)。"""
    g = (sess or {}).get('gameData') or {}
    out = []
    for t in ('teamOne', 'teamTwo'):
        for p in g.get(t) or []:
            if p.get('puuid'):
                out.append(p['puuid'])
    return out


# ── 遊戲中 ──
def _spell(s: dict, static: dict) -> dict:
    raw = s.get('rawDisplayName', '')
    m = _SPELL_RE.search(raw)
    key = m.group(1) if m else ''
    return {'key': key if key in static.get('spellKeys', {}) else '', 'name': s.get('displayName', '')}


def _champ_from_raw(p: dict, static: dict) -> dict:
    m = _CHAMP_RE.search(p.get('rawChampionName', ''))
    key = m.group(1) if m else ''
    cid = static.get('champKeyToId', {}).get(key.lower(), 0)
    return {'id': cid, 'key': key, 'name': p.get('championName', '')}


def _event_text(e: dict, names: dict, my_team: str = 'ORDER') -> Optional[dict]:
    """names: 召喚師名/riotId 遊戲名 → 英雄名(敵我)。回 None 表示不顯示。"""
    n = e.get('EventName')
    t = round(e.get('EventTime', 0))

    def who(x: str) -> str:
        if not x:
            return '?'
        if x.startswith('Turret_'):
            return '防禦塔'
        if x.startswith('Minion_') or x.startswith('SRU_'):
            return '小兵' if x.startswith('Minion_') else '野怪'
        return names.get(x, x)

    if n == 'ChampionKill':
        txt = f"{who(e.get('KillerName'))} 擊殺 {who(e.get('VictimName'))}"
        return {'t': t, 'kind': 'kill', 'text': txt, 'killer': who(e.get('KillerName')),
                'victim': who(e.get('VictimName'))}
    if n == 'Multikill':
        label = {2: '雙殺', 3: '三殺', 4: '四殺', 5: '五殺'}.get(e.get('KillStreak'), f"{e.get('KillStreak')}連殺")
        return {'t': t, 'kind': 'multikill', 'text': f"{who(e.get('KillerName'))} {label}"}
    if n == 'FirstBlood':
        return {'t': t, 'kind': 'first', 'text': f"{who(e.get('Recipient'))} 拿下首殺"}
    if n == 'FirstBrick':
        return {'t': t, 'kind': 'first', 'text': f"{who(e.get('KillerName'))} 推倒第一座塔"}
    if n == 'TurretKilled':
        owner = 'CHAOS' if '_TChaos_' in e.get('TurretKilled', '') else 'ORDER'
        side = '我方' if owner == my_team else '敵方'
        return {'t': t, 'kind': 'turret', 'text': f"{who(e.get('KillerName'))} 推倒防禦塔", 'side': side}
    if n == 'InhibKilled':
        return {'t': t, 'kind': 'inhib', 'text': f"{who(e.get('KillerName'))} 摧毀兵營"}
    if n == 'InhibRespawned':
        return {'t': t, 'kind': 'inhib', 'text': '兵營重生'}
    if n == 'Ace':
        return {'t': t, 'kind': 'ace', 'text': f"{who(e.get('Acer'))} 團滅對手(ACE)", 'team': e.get('AcingTeam')}
    if n in ('DragonKill', 'BaronKill', 'HeraldKill', 'HordeKill', 'AtakhanKill'):
        label = {'DragonKill': '小龍', 'BaronKill': '巴龍', 'HeraldKill': '預示者',
                 'HordeKill': '虛空幼蟲', 'AtakhanKill': '阿塔坎'}[n]
        return {'t': t, 'kind': 'objective', 'text': f"{who(e.get('KillerName'))} 擊殺{label}"}
    if n == 'GameStart':
        return {'t': t, 'kind': 'other', 'text': '遊戲開始'}
    if n == 'GameEnd':
        return {'t': t, 'kind': 'other', 'text': '遊戲結束'}
    return None


def parse_live(d: dict, static: dict, name_to_puuid: dict) -> dict:
    ap = d.get('activePlayer') or {}
    my_rid = ap.get('riotId', '')
    players = d.get('allPlayers') or []
    me = next((p for p in players if p.get('riotId') == my_rid), None)
    my_team = me.get('team') if me else 'ORDER'

    out_players = []
    names = {}
    for p in players:
        champ = _champ_from_raw(p, static)
        ally = p.get('team') == my_team
        label = f"{champ['name']}({'我方' if ally else '敵方'})"
        for k in (p.get('riotIdGameName'), p.get('summonerName'), p.get('riotId'), champ['name']):
            if k:
                names.setdefault(k, label)
        rid = p.get('riotId') or ''
        hidden = not p.get('riotIdGameName') or rid in ('', '#')
        sc = p.get('scores') or {}
        out_players.append({
            'team': p.get('team'), 'ally': ally, 'me': p is me,
            'name': '' if hidden else p.get('riotIdGameName', ''),
            'tag': '' if hidden else p.get('riotIdTagLine', ''),
            'hidden': hidden,
            'puuid': None if hidden else name_to_puuid.get(rid.lower()),
            'champ': champ,
            'skin': p.get('skinName') or '',
            'level': p.get('level', 0),
            'k': sc.get('kills', 0), 'd': sc.get('deaths', 0), 'a': sc.get('assists', 0),
            'cs': sc.get('creepScore', 0), 'ward': round(sc.get('wardScore', 0) or 0),
            'dead': bool(p.get('isDead')), 'respawn': round(p.get('respawnTimer') or 0),
            'items': sorted(({'id': i.get('itemID'), 'name': i.get('displayName', ''), 'slot': i.get('slot', 0),
                              'count': i.get('count', 1)} for i in p.get('items') or []),
                            key=lambda i: i['slot']),
            'spells': [_spell(s, static) for s in (p.get('summonerSpells') or {}).values()],
        })
    # 我方在前；同隊依原順序
    out_players.sort(key=lambda p: not p['ally'])

    self_info = None
    if ap.get('championStats'):
        cs = ap['championStats']
        self_info = {
            'hp': round(cs.get('currentHealth', 0)), 'maxHp': round(cs.get('maxHealth', 0)),
            'res': round(cs.get('resourceValue', 0)), 'maxRes': round(cs.get('resourceMax', 0)),
            'resType': cs.get('resourceType', ''), 'gold': round(ap.get('currentGold', 0)),
            'level': ap.get('level', 0),
        }

    evs = []
    for e in (d.get('events') or {}).get('Events') or []:
        x = _event_text(e, names, my_team)
        if x:
            evs.append(x)
    gd = d.get('gameData') or {}
    return {
        'time': round(gd.get('gameTime', 0)),
        'mode': gd.get('gameMode', ''),
        'players': out_players,
        'self': self_info,
        'kills': {'ally': sum(p['k'] for p in out_players if p['ally']),
                  'enemy': sum(p['k'] for p in out_players if not p['ally'])},
        'events': list(reversed(evs[-40:])),
    }


# ── 結算 ──
def parse_eog(d: dict, static: dict) -> dict:
    """結算畫面。隱藏名字的玩家在結算時客戶端會顯示真名，這裡照客戶端顯示。"""
    teams = []
    me_win = None
    for t in d.get('teams') or []:
        ps = []
        for p in t.get('players') or []:
            st = p.get('stats') or {}
            cid = p.get('championId', 0)
            augs = [st.get(f'PLAYER_AUGMENT_{i}') for i in range(1, 7)]
            ps.append({
                'puuid': p.get('puuid'), 'name': p.get('riotIdGameName', ''), 'tag': p.get('riotIdTagLine', ''),
                'me': bool(p.get('isLocalPlayer')),
                'champ': {'id': cid, 'key': static.get('champIdToKey', {}).get(str(cid), ''),
                          'name': p.get('championName', '')},
                'level': st.get('LEVEL', 0),
                'k': st.get('CHAMPIONS_KILLED', 0), 'd': st.get('NUM_DEATHS', 0), 'a': st.get('ASSISTS', 0),
                'cs': st.get('MINIONS_KILLED', 0) + st.get('NEUTRAL_MINIONS_KILLED', 0),
                'gold': st.get('GOLD_EARNED', 0),
                'dmg': st.get('TOTAL_DAMAGE_DEALT_TO_CHAMPIONS', 0),
                'taken': st.get('TOTAL_DAMAGE_TAKEN', 0),
                'heal': st.get('TOTAL_HEAL', 0),
                'items': [i for i in p.get('items') or [] if i],
                'augments': [a for a in augs if a],
                'spells': [p.get('spell1Id'), p.get('spell2Id')],
            })
            if p.get('isLocalPlayer'):
                me_win = bool(t.get('isWinningTeam'))
        teams.append({
            'ally': bool(t.get('isPlayerTeam')),
            'win': bool(t.get('isWinningTeam')),
            'kills': sum(p['k'] for p in ps),
            'players': ps,
        })
    teams.sort(key=lambda t: not t['ally'])
    return {
        'gameId': d.get('gameId'),
        'win': me_win,
        'length': d.get('gameLength', 0),
        'mode': d.get('gameMode', ''),   # 結算資料沒有 queueId，佇列由呼叫端用 gameflow session 補
        'teams': teams,
    }


# ── 玩家資料(等級、牌位、近期勝率) ──
def rank(q: Optional[dict], division_key: str = 'division') -> Optional[dict]:
    """客戶端(division)與 Riot API(rank)的牌位都整理成同一格式。"""
    if not q or not q.get('tier') or q.get('tier') in ('NONE', ''):
        return None
    return {'tier': q['tier'], 'division': '' if q['tier'] in APEX else q.get(division_key, ''),
            'lp': q.get('leaguePoints', 0), 'w': q.get('wins', 0), 'l': q.get('losses', 0)}


def parse_profile(summoner: Optional[dict], ranked: Optional[dict], history: Optional[dict],
                  queue_id: Optional[int]) -> dict:
    s = summoner or {}
    queues = {q.get('queueType'): q for q in (ranked or {}).get('queues') or []}
    games = ((history or {}).get('games') or {}).get('games') or []
    same = [g for g in games if queue_id and g.get('queueId') == queue_id]
    w = sum(1 for g in same if (g.get('participants') or [{}])[0].get('stats', {}).get('win'))
    recent_all = len(games)
    w_all = sum(1 for g in games if (g.get('participants') or [{}])[0].get('stats', {}).get('win'))
    return {
        'name': s.get('gameName', ''), 'tag': s.get('tagLine', ''),
        'level': s.get('summonerLevel', 0), 'icon': s.get('profileIconId', 0),
        'solo': rank(queues.get('RANKED_SOLO_5x5')),
        'flex': rank(queues.get('RANKED_FLEX_SR')),
        'recent': {'queueId': queue_id, 'w': w, 'l': len(same) - w} if same else None,
        'recentAll': {'w': w_all, 'l': recent_all - w_all} if recent_all else None,
        'private': s.get('privacy') == 'PRIVATE',
    }
