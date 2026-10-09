# 🎮 DGX Discord Bot

個人多功能 Discord Bot，整合 **音樂播放**（含 Apple Music 風格的**同步歌詞**畫面）、**LoL 即時對戰資訊**畫面、**Valorant 戰績**查詢與追蹤、**大老二** 與 **UNO** 兩款多人卡牌遊戲，全部以 slash command 操作。

---

## 功能特色

### 🎵 音樂播放
- **YouTube Music 優先搜尋** — 使用 ytmusicapi 在 YTMusic 歌曲庫搜尋，自動評分挑選最官方的版本（官方 Audio / MV 優先，過濾翻唱、字幕版、卡拉OK）
- **Autoplay 自動推薦** — 歌曲播完後依 YouTube Music Radio 自動接下一首，背景預載零間隔接播
- **播放清單支援** — 貼上 YouTube 播放清單連結即可整批加入 queue（無數量上限）
- **隨機播放清單** — 播放清單可自動打亂順序
- **插播下一首** — 將歌曲插入到 queue 第一位
- **循環模式** — 單曲循環 / 整個 queue 循環
- **斷線自動重連** — 語音被踢掉或網路斷線時自動重連（指數退避），並從斷點續播
- **起播失敗自動重試** — YouTube 偶發對串流 URL 回 403 時，自動重抓 URL 再播一次，不會無聲跳過

### 🎤 同步歌詞（Discord Activity）
- `/lyrics` 在語音頻道開啟內嵌歌詞畫面，所有人一起看
- 歌詞來源依序：**YouTube Music**（官方 LyricFind / Musixmatch）→ [LRCLIB](https://lrclib.net) → 網易雲 / QQ 音樂 / 酷狗
- 自動清理標題（Official MV、括號、中英並列），歌手、歌名（簡繁通用）、歌曲長度都要對得上才採用，避免配到翻唱、試聽片段或同歌手的別首歌
- 播放位置以 bot **實際送出的音框**計算，暫停 / 續播 / 換歌都即時跟上
- 模糊流動封面背景、目前這句高亮、間奏顯示三個點；右下角 ± 可微調延遲
- 縮成右上角小視窗（PiP）時自動切成精簡版面，長句自動縮字最多兩行

### ⚔️ LoL 對戰資訊（Discord Activity）
- `/lol` 在語音頻道開啟即時畫面（跟 `/lyrics` 共用同一個 Activity），跟著玩家的客戶端自動切換：
  - **選角**：隊友的牌位（含本季完整勝敗）、英雄熟練度、近期勝率（客戶端上限 100 場）、**最近 10 場**對局
  - **遊戲中**：雙方計分板（KDA、裝備、裝備總價推算的經濟）、雙方總經濟與差距、即時事件、自己的血量/魔力
  - **我方推薦出裝**：只從該英雄 op.gg ARAM 常出清單挑，再依敵方實際裝備的物理/魔法比例、護甲/魔抗/吸血佔比把針對裝往前排；每件都附根據（選用率、勝率、觸發條件的數字）
  - **敵方下一件預測**：手上零件湊齊度為主，op.gg 常出裝決定順序
  - **結算**：輸出、承受、治療、護盾、控場時間、金錢、裝備、海克斯強化，各欄最高者標示
- `/lol 名字#tag` 不開畫面，改成查這位玩家的牌位與熟練度（Riot API）
- 資料來源是**玩家 PC 上的 LoL 客戶端**（經 SSH 只送 GET，不會在 PC 上寫任何東西），原因見「注意事項」

### 🔫 Valorant 戰績
- `/valo 名字#tag` 查牌位、歷史最高與最近對戰
- `/valo-track` 追蹤玩家，打完一場自動把戰績發到指定頻道

### 🩺 健康檢查
- `scripts/healthcheck.sh` 每日診斷 service 狀態、重啟抖動、403 / 播放失敗、OOM、yt-dlp 版本、log 大小
- `/check` 在 Discord 查看最近一次報告

### 🃏 大老二（Big Two）
- 2~4 人多人對戰，圖片化手牌顯示
- 按鈕點選出牌、勝負統計排行榜

### 🎴 UNO
- 2~10 人，可加入電腦（CPU）對手
- 圖片化手牌、萬能牌／萬能+4 的選色**私下顯示給該玩家**（不公開）
- 房主可強制結束、勝負統計排行榜

---

## 安裝與設定

### 1. 環境需求
- Python 3.10+
- Node.js 18+（只有要重新 build 歌詞畫面時需要，`activity/dist/` 已附編好的版本）
- FFmpeg（音樂功能需要）
  - Linux / macOS：裝好並加入 PATH 即可，程式會自動偵測（`shutil.which('ffmpeg')`）
  - Windows：若不在 PATH，可修改 `cogs/music.py` 的 `FFMPEG_PATH` 後備路徑

### 2. 安裝依賴套件
```bash
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 3. 設定 Bot Token
複製範本並填入你的 Token：
```bash
cp .env.example .env
```
編輯 `.env`：
```env
DISCORD_TOKEN=你的_Bot_Token
# LYRICS_PORT=8765   # 選填：歌詞畫面伺服器的本機 port
```
> Bot Token 從 [Discord Developer Portal](https://discord.com/developers/applications) 取得。
> 需開啟 **Message Content Intent** 與 **Voice States Intent**。

### 4. 啟動 Bot
```bash
python bot.py
```
> 啟動後會自動把 slash command 同步到所在的每個伺服器；也可在伺服器內用 `!sync` 手動重新同步。

### 5.（選用）設定同步歌詞 `/lyrics`
Discord Activity 必須透過**公開的 HTTPS 網址**載入，bot 內建的歌詞伺服器只聽 `127.0.0.1:8765`，需要用 [Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/) 之類的工具對外：

```
Discord ──→ https://lyrics.你的網域 ──(Cloudflare Tunnel)──→ 本機 127.0.0.1:8765
```

1. **Tunnel**（網域需託管在 Cloudflare，免費方案即可）
   ```bash
   cloudflared tunnel login                       # 會給一個網址，用任何裝置的瀏覽器授權
   cloudflared tunnel create discordbot-lyrics
   cloudflared tunnel route dns discordbot-lyrics lyrics.你的網域
   ```
   `~/.cloudflared/config.yml`：
   ```yaml
   tunnel: <tunnel id>
   credentials-file: /home/<user>/.cloudflared/<tunnel id>.json
   ingress:
     - hostname: lyrics.你的網域
       service: http://127.0.0.1:8765
     - service: http_status:404
   ```
   再用 `cloudflared tunnel run discordbot-lyrics` 常駐（建議做成 systemd 服務）。
2. **Developer Portal** → 你的 App → Activities
   - Settings：開啟 **Enable Activities**
   - URL Mappings：Root Mapping `/` → `lyrics.你的網域`
3. 重啟 bot，在語音頻道用 `/lyrics` 開啟。

> 開啟 Activities 後 Discord 會自動建立一個全域的 Entry Point 指令（`launch`），`bot.py` 清理全域指令時會跳過它。

### 6.（選用）設定 LoL `/lol` 與 Valorant `/valo`
`/lol` 的即時畫面跟 `/lyrics` 共用同一個 Activity，先完成步驟 5。

1. **讓 bot 主機能 SSH 到玩家的 Windows PC**（免密碼金鑰登入），在 `~/.ssh/config` 設一個別名（預設叫 `pc`）。
   bot 會在 PC 上用 `curl` 讀客戶端的 lockfile 與本機 API（LCU、`127.0.0.1:2999` Live Client Data），**只讀不寫**。
2. `.env` 加上：
   ```env
   RIOT_API_KEY=...          # 選填：Riot Developer Portal 的 key，用來補牌位、熟練度、/lol 查人
   HENRIK_API_KEY=...        # /valo 需要：HenrikDev 非官方 Valorant API 的 key
   # LOL_SSH_HOST=pc         # 選填：~/.ssh/config 裡玩家 PC 的別名
   # LOL_INSTALL_DIR=D:\Riot Games\League of Legends   # 選填：PC 上的 LoL 安裝路徑
   # RIOT_PLATFORM=tw2  RIOT_REGIONAL=sea  RIOT_ACCOUNT_REGION=asia   # 選填：伺服器區域
   ```
3. 重啟 bot。英雄、裝備圖示與出裝統計第一次用到時才下載，快取在 `data/lol_assets/`。

修改歌詞畫面前端後重新 build（不用重啟 bot，伺服器直接讀 `activity/dist/`）：
```bash
cd activity && npm install && npm run build
```

---

## 指令一覽

### 🎵 音樂
| 指令 | 說明 |
|------|------|
| `/play <歌名或連結>` | 搜尋歌曲或貼上 YouTube / YouTube Music 連結播放（支援播放清單）。 |
| `/randomlist <連結或歌名>` | 同 `/play`，但會先隨機打亂順序再加入 queue。 |
| `/nextplay <歌名或連結>` | 將歌曲**插入 queue 第一位**，目前這首播完立刻接它。 |
| `/uwu` | 隨機播放預設清單。 |
| `/pause` / `/resume` | 暫停 / 繼續播放。 |
| `/skip` | 跳過目前歌曲。 |
| `/stop` | 停止並清空 queue（Bot 留在語音頻道）。 |
| `/disconnect` | 停止、清空 queue 並離開語音頻道。 |
| `/queue` | 查看播放中與 queue 清單（含 Autoplay 預載的下一首）。 |
| `/nowplaying` | 查看目前歌曲詳細資訊。 |
| `/remove <位置>` 或 `/remove <起> <迄>` | 移除 queue 中單首或範圍歌曲（從 1 起算）。 |
| `/repeat` | 切換循環模式：off → one（單曲）→ all（整個 queue）→ off。 |
| `/autoplay` | 開啟 / 關閉 Autoplay。 |
| `/skipautoplay` | 換一首 Autoplay 推薦（不跳掉目前歌曲）。 |
| `/volume <0-100>` | 調整音量。 |
| `/info` | 顯示 Bot 連線與播放狀態。 |
| `/come` | 把 Bot 移到你所在的語音頻道（不中斷播放）。 |
| `/lyrics` | 在語音頻道開啟同步歌詞畫面（需先完成上方步驟 5）。 |

### ⚔️ LoL / 🔫 Valorant
| 指令 | 說明 |
|------|------|
| `/lol` | 在語音頻道開啟 LoL 即時對戰資訊畫面（需完成步驟 5、6）。 |
| `/lol <名字#tag>` | 查這位玩家的牌位與英雄熟練度。 |
| `/valo <名字#tag>` | 查 Valorant 玩家的牌位與最近戰績。 |
| `/valo-track add <名字#tag>` | 追蹤玩家，打完一場自動把戰績發到這個頻道。 |
| `/valo-track remove <名字#tag>` | 取消追蹤。 |
| `/valo-track list` | 目前追蹤中的玩家。 |

### 🩺 其他
| 指令 | 說明 |
|------|------|
| `/check` | 顯示最近一次健康檢測報告。 |
| `!sync` | 手動重新同步 slash command 到目前伺服器。 |

### 🃏 大老二
| 指令 | 說明 |
|------|------|
| `/bigtwo` | 開一局大老二（2~4 人）。 |
| `/bigtwo_stats` | 查看大老二勝負統計。 |

### 🎴 UNO
| 指令 | 說明 |
|------|------|
| `/uno` | 開一局 Uno（2~10 人，可加入電腦）。 |
| `/uno_leave` | 離開正在進行的 Uno。 |
| `/uno_forfeit` | 房主強制結束並宣布遊戲結束。 |
| `/uno_stats` | 查看 Uno 勝負統計。 |

---

## 搜尋邏輯說明（音樂）

```
輸入文字（歌名）
  └─ YouTube Music 搜尋（取前 5 個候選）
       └─ 評分挑最佳：
            + 標題與關鍵字重疊率
            + 頻道名含 official / vevo
            + 標題含「official」或「audio」
            - 標題含 cover / remix / lyrics / live / karaoke 等
       └─ 選出最高分 → 播放
       （若 YouTube Music 完全失敗 → 退回 YouTube 搜尋）

輸入連結（YouTube / YouTube Music）
  └─ 直接抓取該影片 / 播放清單

Autoplay 推薦
  └─ 直接呼叫 YouTube Music innertube `next` 端點取 Radio 清單
     （ytmusicapi.get_watch_playlist 在 YT Music 改版後已失效）
       └─ 過濾已播過的歌（依標題比對）→ 預載串流 URL → 零間隔接播
```

## 歌詞同步原理

```
MusicCog（TrackedSource）
  └─ 每送出一個 20ms 音框 +1 → 播放位置 = 續播起點 + 音框數 × 0.02 秒
LyricsCog（aiohttp，127.0.0.1:8765）
  ├─ /ws         每 0.25 秒推一次 {歌曲, 位置, 是否暫停}；換歌時推歌詞
  ├─ /api/thumb  代抓封面（Activity 的 CSP 不允許直接載外部圖片）
  └─ 查歌詞（快取 200 首）：YTM 這首 → YTM 搜歌曲版 → LRCLIB → 網易雲 / QQ / 酷狗 → LRCLIB 純文字
activity/（前端）
  └─ 「位置 − 本地時間」在播放中是常數，網路延遲只會讓它變小
     → 取最近 6 秒內最大值當基準，抗網路抖動；跳轉 / 卡頓時重新取樣
```

---

## 檔案結構

```
discordbot/
├── bot.py                  # Bot 主程式：啟動、載入 cogs、slash command 同步
├── cogs/
│   ├── music.py            # 音樂播放
│   ├── lyrics.py           # /lyrics：歌詞伺服器、WebSocket、歌詞查詢流程
│   ├── lyrics_sources.py   # 網易雲 / QQ 音樂 / 酷狗 歌詞來源
│   ├── lyrics_match.py     # 歌名清理、歌手比對、LRC 解析（純函式）
│   ├── lol.py              # /lol：輪詢玩家 PC 客戶端、op.gg 出裝、Activity 資料推送
│   ├── lol_parse.py        # LoL 資料整理、經濟、出裝預測與推薦（純函式）
│   ├── riot_api.py         # Riot API（牌位、熟練度、帳號查詢）
│   ├── valo.py             # /valo、/valo-track
│   ├── valo_parse.py       # Valorant 戰績整理（純函式）
│   ├── health.py           # /check 健康報告
│   ├── bigtwo.py           # 大老二
│   └── uno.py              # UNO
├── activity/               # Discord Activity 前端（同步歌詞 + LoL 畫面）
│   ├── src/                # 原始碼（main.js 歌詞 / lol.js LoL / style.css / index.html）
│   └── dist/               # build 結果，bot 直接提供
├── scripts/
│   └── healthcheck.sh      # 每日健檢（只診斷不修復）
├── games/
│   ├── bigtwo_logic.py     # 大老二規則邏輯
│   ├── uno_logic.py        # UNO 規則邏輯
│   ├── card_image.py       # 大老二手牌圖片繪製
│   └── uno_image.py        # UNO 手牌圖片繪製
├── tests/                  # 單元測試（UNO、repeat、起播重試、歌詞比對、LoL/Valorant 解析）
├── data/                   # 執行時遊戲統計、健檢報告、LoL 快取（不納入版控）
├── requirements.txt        # Python 依賴套件
├── .env.example            # 環境變數範本
└── .env                    # Bot Token（不要上傳到 GitHub）
```

---

## 測試
```bash
pytest tests/
```

---

## 注意事項
- `.env` 內的 Bot Token 請勿上傳至 GitHub，`.gitignore` 已排除 `.env`。
- FFmpeg 需另外安裝；Windows 可從 [gyan.dev](https://www.gyan.dev/ffmpeg/builds/) 下載。
- YouTube 串流 URL 有時效性，長時間暫停後可能需要重新播放。
- ytmusicapi 不需登入帳號即可使用搜尋與 Radio 推薦。
- 同步歌詞查不到時會顯示封面、歌名與進度條；歌詞只有逐行時間軸，所以是整行亮起，不是逐字卡拉 OK。
- 播放 MV 影片時，若 MV 長度跟歌曲版差太多（多了前奏 / 劇情），為避免時間軸錯位會不顯示歌詞。
- 網易雲 / QQ 音樂 / 酷狗為非官方 API，可能隨時失效；失效時會自動略過。
- **LoL 為什麼讀 PC 客戶端而不是 Riot API**：實測台服（tw2）的公開 API 資料很殘缺，近期對局、牌位常拿不到，所以即時資料都以玩家 PC 的客戶端為準，Riot API 只用來補牌位、熟練度與查人。
- LoL 客戶端對戰紀錄**最多只給最近 100 場**，所以近期勝率最多看 100 場；積分模式另外顯示本季完整勝敗。
- 遊戲中查其他玩家的資料，客戶端要向伺服器拿，常要十幾到二十幾秒；查不到會每 30 秒自動重試。
- 出裝統計來自 op.gg 的 **ARAM** 數據（沒有大混戰專屬統計），每隻英雄快取 12 小時。推薦出裝的觸發門檻（例如敵方吸血裝佔 10%）是自訂的，畫面上會附實際數字讓人自己判斷。
- 遊戲中的 Live Client API **拿不到海克斯強化**，只有結算畫面看得到；其他玩家的金錢也拿不到，經濟是用裝備合成總價推算。
- 名字被隱藏的玩家不會去查牌位或戰績。
- Valorant 官方 API 不開放給 personal key，改用 [HenrikDev](https://docs.henrikdev.xyz/) 非官方 API（每分鐘 30 次）。
