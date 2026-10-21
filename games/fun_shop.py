"""代幣商店：稱號 / 個人卡片外框 / 個人卡片背景。全部是外觀，不影響任何機率。

外框、背景的樣子由 Activity 前端依 id 決定(activity/src/style.css)。
"""
ITEMS = [
    # (id, 種類, 名稱, 說明, 價格)
    ('title_rookie', 'title', '峽谷新手', '每個人都是從這裡開始的', 100),
    ('title_fisher', 'title', '釣魚達人', '河道蟹看到你都會繞路', 800),
    ('title_gacha', 'title', '轉蛋之神', '抽卡前會先洗手', 1500),
    ('title_gambler', 'title', '賭神', '21 點只停 17', 2000),
    ('title_tycoon', 'title', '大富翁', '符文大地有一半是你的', 3000),
    ('title_rich', 'title', '富可敵國', '錢多到可以買下德瑪西亞', 8000),
    ('title_legend', 'title', '傳說中的玩家', '大家都聽過你的名字', 15000),
    ('frame_bronze', 'frame', '青銅外框', '樸實的銅色外框', 300),
    ('frame_silver', 'frame', '白銀外框', '閃亮的銀色外框', 800),
    ('frame_gold', 'frame', '黃金外框', '金光閃閃', 2000),
    ('frame_rainbow', 'frame', '彩虹外框', '會流動的彩虹光', 5000),
    ('frame_galaxy', 'frame', '星河外框', '裡面有星星在轉', 10000),
    ('bg_rift', 'bg', '峽谷綠', '召喚師峽谷的草地', 500),
    ('bg_ice', 'bg', '冰原青', '弗雷爾卓德的冰原', 1500),
    ('bg_void', 'bg', '虛空紫', '別看太久', 1000),
    ('bg_arcane', 'bg', '雙城藍粉', '皮爾托福的霓虹', 1500),
    ('bg_lava', 'bg', '熔岩紅橘', '熱到可以烤魚', 2000),
]
BY_ID = {i[0]: {'id': i[0], 'kind': i[1], 'name': i[2], 'desc': i[3], 'price': i[4]} for i in ITEMS}
SLOTS = {'title': 'title', 'frame': 'frame', 'bg': 'bg'}


def catalog(owned: list, equipped: dict) -> list[dict]:
    return [{**it, 'owned': it['id'] in owned, 'equipped': equipped.get(it['kind']) == it['id']}
            for it in BY_ID.values()]
