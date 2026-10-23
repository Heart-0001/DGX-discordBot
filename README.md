# 🎮 DGX Discord Bot

個人多功能 Discord Bot，整合 **音樂播放**（含 Apple Music 風格的**同步歌詞**畫面）、**LoL / Valorant 即時對戰資訊**畫面、**Valorant 戰績**查詢與追蹤、**大老二** 與 **UNO** 兩款多人卡牌遊戲，以及**打遊戲換券的活動**（轉蛋、釣魚、小遊戲、樂透、大富翁）。功能大多以 slash command 操作，活動則全部在 Activity 的面板裡點。

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
- `/act` 在語音頻道開啟內嵌 Activity（歌詞／LoL／Valorant 畫面自動切換），所有人一起看
- 歌詞來源依序：**YouTube Music**（官方 LyricFind / Musixmatch）→ [LRCLIB](https://lrclib.net) → 網易雲 / QQ 音樂 / 酷狗
- 自動清理標題（Official MV、括號、中英並列），歌手、歌名（簡繁通用）、歌曲長度都要對得上才採用，避免配到翻唱、試聽片段或同歌手的別首歌
- 播放位置以 bot **實際送出的音框**計算，暫停 / 續播 / 換歌都即時跟上
- 模糊流動封面背景、目前這句高亮、間奏顯示三個點；右下角 ± 可微調延遲
- 縮成右上角小視窗（PiP）時自動切成精簡版面，長句自動縮字最多兩行
- `/act` **自動模式**：同一個 Activity，偵測到 LoL 或 Valorant 進入對局就切到對應畫面（賽後畫面保留 3 分鐘），沒有就顯示歌詞；遊戲畫面時歌詞縮成頂列的一小條（封面、歌名、目前那句）

### ⚔️ LoL 對戰資訊（Discord Activity）
- Activity 的 LoL 畫面（`/act`）跟著玩家的客戶端自動切換：
  - **選角**：隊友的牌位（含本季完整勝敗）、英雄熟練度、近期勝率（客戶端上限 100 場）、**最近 10 場**對局
  - **遊戲中**：雙方計分板（KDA、裝備、裝備總價推算的經濟）、雙方總經濟與差距、即時事件、自己的血量/魔力
  - **計時列**：小龍、巢蟲、先鋒、巴龍、兵營重生（Live Client API 的擊殺事件推算），大混戰的 4 個補包（讀 PC 上 Porofessor 的 Overwolf log）
  - **聊天室**：遊戲內聊天（同樣來自 Porofessor log），我方藍、敵方紅，舊的在上、新的在下；停在最底時自動跟著新訊息，往上翻舊訊息時不會被拉回去
  - **我方推薦出裝**：只從該英雄 op.gg ARAM 常出清單挑，再依敵方實際裝備的物理/魔法比例、護甲/魔抗/吸血佔比把針對裝往前排；每件都附根據（選用率、勝率、觸發條件的數字）
  - **敵方下一件預測**：手上零件湊齊度為主，op.gg 常出裝決定順序
  - **結算**：輸出、承受、治療、護盾、控場時間、金錢、裝備、海克斯強化，各欄最高者標示
- `/lol 名字#tag` 查這位玩家的牌位、熟練度與最近對戰（Riot API）
- `/lol-live [名字#tag]` 查任何玩家**正在打的對局**（Riot spectator API）：雙方英雄、召喚師技能、主符文、禁用、每人牌位／等級／該英雄熟練度／近 3 場；玩家 PC 的客戶端剛好在打同一場時再補即時 K/D/A、CS、裝備
- 資料來源是**玩家 PC 上的 LoL 客戶端**（經 SSH 只送 GET，不會在 PC 上寫任何東西），原因見「注意事項」

### 🔫 Valorant 對戰資訊與戰績
- Activity 的 Valorant 畫面（`/act`）借用玩家 PC 上 Riot Client 的登入直接查 Riot 的對局資料：
  - **選角**：隊友 5 人；**讀取畫面後**：雙方 10 人
  - 每人一列（像遊戲內 TAB，但更多）：特務、名字、等級、競技牌位 + RR、歷史最高、**競技那組**（本 act 勝敗 + 本 act 最近 20 場競技的 KDA / ACS / ADR / 爆頭率 / 首殺）、**目前模式最近 10 場**勝率、**近 5 場那組**（不分模式的勝敗 / KDA / ACS / ADR / 爆頭率 / 首殺），右側貼出近 5 場對局
  - 頂列顯示即時回合比數；**賽後**自動變成計分板（K/D/A、ACS、ADR、MVP）
  - 開隱藏名字的玩家只藏名字，牌位與戰績照查
- `/valo-live [名字#tag]` 純文字版：PC 的 Riot Client 有登入就查**自己**的對局（雙方 10 人牌位、近況；Riot 不給查別人的對局，填別人只在他跟你同場時顯示）；查不到對局或電腦沒開就退回 HenrikDev 的牌位與近況
- `/valo 名字#tag` 查牌位、歷史最高與最近對戰（有設定小帳就直接查 Riot，否則 HenrikDev；名字→puuid 一律靠 HenrikDev）
- `/valo-track` 追蹤玩家，打完一場自動把完整計分板發到指定頻道（每個伺服器各自設一個頻道，在哪個伺服器 add 就發到哪；有小帳就直接查 Riot，打完幾秒就有，否則等 HenrikDev）

### 📈 股票
- `/stock` 查股價：台股代號、中文名（可打一部分）、美股代號、英文名、常見中文名（蘋果、輝達）、指數（大盤、櫃買、費半、標普500）都可以，自動判斷台股或美股；附當日走勢圖（紅漲綠跌）
- `/stock-track` 追蹤股票，每個交易日開盤、收盤各發一則訊息到指定頻道；休市日（看當天有沒有成交資料判斷）不發
- 資料來源（免費、免 key）：台股報價用證交所 mis 即時資料，名稱清單用證交所／櫃買 OpenAPI，美股與走勢圖用 Yahoo Finance

### 🎟️ 活動：打遊戲換券
- **不用打指令**：全部在 Activity(`/act`)右上角的「🎮 活動」面板裡點；LoL / Valorant 結算畫面也有「🎟️ 去玩活動」按鈕
- 面板首頁綁定自己的 LoL / Valorant 帳號（每個遊戲最多 3 個）；之後**每打完一場自動換券**，Activity 右下角會跳出換券卡片
  - Riot API / HenrikDev：打完約 1–3 分鐘
  - LoL 另外看玩家 PC 客戶端的對戰紀錄（台服 API 查不到的大混戰、競技場也算）：對局一結束每 5 秒看一次、最多 90 秒，打完馬上關客戶端也抓得到；同場有綁定的人一起算，兩條路用 gameId 去重
- 表現分 0–8（LoL：勝負、KDA、參團率、**傷害 / 承傷 / 控制取佔比最高的一項**（坦克不用拚傷害）；競技場看名次。Valorant：勝負、ACS、K/D、MVP；死鬥看名次）→ 1 / 2 / 3 / 4 張券
- **打得好只會拿比較多張券，每張券在活動裡的機率完全一樣**；打得再爛也保底 1 張；**沒有每日上限**（大家偶爾有空才打）；每日簽到另送 1 張
- 換券公告不會 @ 玩家（名字照顯示）
- 券的用途（1 張券期望值約 100 代幣）：
  - 🎰 轉蛋：抽 LoL 英雄 / Valorant 特務卡（普通 60%、稀有 28%、史詩 10%、傳說 2%），收集圖鑑、賣重複
  - 🎣 釣魚：32 種魚、背包、圖鑑、伺服器最重紀錄
  - 🃏 小遊戲：21 點、拉霸、踩地雷、猜正反面
  - 🎫 樂透：1 張券 1 注，**每天 21:00 開獎**，開獎會 @ 中獎者
  - 🎲 大富翁：每個伺服器一張長駐棋盤（32 格，含機會、傳送門、稅、停權），14 天一季，第一個人加入時開季；1 張券擲 1 次骰，動畫照每一站演（走到的格子 → 機會卡後退 / 傳送門 → 停下）；賽季結束依淨值發代幣
- 代幣花在 🛒 商店（稱號、個人卡片外框與背景，純外觀）；🪪 個人卡片、🏆 排行
- 抽到傳說卡、釣到傳說魚、拉霸大獎、樂透頭獎、賽季冠軍 → 同伺服器所有開著 Activity 的人都會看到慶祝特效
- 管理員：`/fun channel` 設定公告頻道（換券、開獎、賽季結算，附「開啟活動」按鈕）、`/lottery prize` 設定額外獎品

### 🩺 健康檢查
- `scripts/healthcheck.sh` 每日診斷 service 狀態、重啟抖動、403 / 播放失敗、OOM、yt-dlp 版本、log 大小
- `scripts/ytdlp_upgrade.sh` 升級 yt-dlp：升到最新版後，用 bot 實際的 yt-dlp / ffmpeg 參數抓 3 首真的歌、實際解 5 秒，**全部能播才算成功，否則自動降回原版本**，壞版本記進 `data/ytdlp_bad_versions.txt`（之後不再自動升到那版，健檢也不再報「落後」）。bot 每首歌另開 yt-dlp 子行程，換版本不用重啟
  - 只測 `ytsearch1:lofi` 不夠：yt-dlp 2026.10.21 搜 lofi 能播，但 YouTube Music 的正式歌全部 403
  - `--test` 只測目前版本
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

### 5.（選用）設定 Activity `/act`（同步歌詞／對局畫面）
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
3. 重啟 bot，在語音頻道用 `/act` 開啟。

> 開啟 Activities 後 Discord 會自動建立一個全域的 Entry Point 指令（`launch`），`bot.py` 清理全域指令時會跳過它。

### 6.（選用）設定 LoL `/lol` 與 Valorant `/valo`、`/valo-live`
即時畫面都在同一個 Activity（`/act`），先完成步驟 5。

1. **讓 bot 主機能 SSH 到玩家的 Windows PC**（免密碼金鑰登入），在 `~/.ssh/config` 設一個別名（預設叫 `pc`）。
   bot 會在 PC 上用 `curl` 讀客戶端的 lockfile 與本機 API（LoL：LCU、`127.0.0.1:2999` Live Client Data；Valorant：Riot Client 的 token 與 presence），**只讀不寫**。
2. `.env` 加上：
   ```env
   RIOT_API_KEY=...          # 選填：Riot Developer Portal 的 key，用來補牌位、熟練度、/lol 查人
   HENRIK_API_KEY=...        # /valo 需要：HenrikDev 非官方 Valorant API 的 key
   # LOL_SSH_HOST=pc         # 選填：~/.ssh/config 裡玩家 PC 的別名
   # LOL_INSTALL_DIR=D:\Riot Games\League of Legends   # 選填：PC 上的 LoL 安裝路徑
   # VALO_SHARD=ap           # 選填：/valo-live 用的 Riot 伺服器分區（台灣 ap）
   # RIOT_PLATFORM=tw2  RIOT_REGIONAL=sea  RIOT_ACCOUNT_REGION=asia   # 選填：伺服器區域
   ```
3.（選用）Valorant 小帳：bot 用這隻帳號的登入查牌位、對戰紀錄等所有資料（PC 關機也能查）；只有「玩家本人正在打的對局」還是要玩家 PC 的 client。
   建一隻只給 bot 用的 Riot 帳號（先在 PC 開一次 Valorant 進大廳，分區才會定在 AP；不要開兩步驟驗證），把帳密放進 `.env`：
   ```
   RIOT_ALT_USER=...
   RIOT_ALT_PASS=...
   ```
   bot 啟動時自己登入（實測從機房 IP 不用驗證碼），拿到的 ssid 存在 `data/riot_alt_cookie.json`（權限 600、不進 git），之後每 12 小時用 ssid 換 token 續命、不再送密碼；ssid 失效會自動用帳密重登。
   注意：不要拿 PC 上 Riot Client 的登入來共用（`scripts/import_alt_cookie.py` 那條路）——PC 的 client 一續期或登出，Riot 就把整個 session 作廢。
4. 重啟 bot。英雄、裝備、特務、牌位圖示與出裝統計第一次用到時才下載，快取在 `data/lol_assets/`、`data/valo_assets/`。

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
| `/act` | 開啟 Activity（需先完成上方步驟 5）：有 LoL / Valorant 對局就顯示對局，否則顯示歌詞。 |

### ⚔️ LoL / 🔫 Valorant
| 指令 | 說明 |
|------|------|
| `/lol <名字#tag>` | 查這位玩家的牌位、英雄熟練度與最近對戰。 |
| `/lol-live [名字#tag]` | 查玩家正在打的 LoL 對局（不填 = 你電腦上登入的帳號）。 |
| `/valo-live [名字#tag]` | 查你目前的 Valorant 對局（需 PC 的 Riot Client 登入；Riot 不給查別人的對局）；查不到就顯示 HenrikDev 的牌位與近況。 |
| `/valo <名字#tag>` | 查 Valorant 玩家的牌位與最近戰績。 |
| `/valo-track add <名字#tag>` | 追蹤玩家，打完一場自動把戰績發到這個頻道。 |
| `/valo-track remove <名字#tag>` | 取消追蹤。 |
| `/valo-track list` | 目前追蹤中的玩家。 |

### 📈 股票
| 指令 | 說明 |
|------|------|
| `/stock <代號或名稱>` | 查股價，例如 `6669`、`緯穎`、`VTI`、`apple`、`大盤`。 |
| `/stock-track add <代號或名稱>` | 追蹤股票：開盤（台股 09:00／美股 09:30 美東）、收盤（13:30／16:00）各發一則到這個頻道。 |
| `/stock-track remove <股票>` | 取消追蹤。 |
| `/stock-track list` | 這個伺服器追蹤中的股票。 |

### 🎟️ 活動（管理員）
玩家不用指令，活動都在 `/act` 的「🎮 活動」面板裡。以下指令預設只有管理員看得到。

| 指令 | 說明 |
|------|------|
| `/fun channel` | 把活動公告（換券、開獎、賽季結算）發到這個頻道。 |
| `/fun give` | （bot 擁有者）發券或代幣給某人。 |
| `/lottery prize` | 設定本期額外獎品說明，開獎後清空。 |

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
│   ├── lyrics.py           # /act：Activity 伺服器、WebSocket、歌詞查詢流程
│   ├── lyrics_sources.py   # 網易雲 / QQ 音樂 / 酷狗 歌詞來源
│   ├── lyrics_match.py     # 歌名清理、歌手比對、LRC 解析（純函式）
│   ├── lol.py              # /lol：輪詢玩家 PC 客戶端、op.gg 出裝、Activity 資料推送
│   ├── lol_parse.py        # LoL 資料整理、經濟、出裝預測與推薦（純函式）
│   ├── lol_timers.py       # LoL 計時列（野怪 / 兵營 / 補包）與聊天室
│   ├── riot_api.py         # Riot API（牌位、熟練度、帳號查詢）
│   ├── valo.py             # /valo、/valo-track、/valo-live（對局輪詢、Activity 資料推送）
│   ├── lol_live_parse.py   # /lol-live：spectator-v5 對局整理
│   ├── valo_parse.py       # Valorant 戰績整理（純函式）
│   ├── valo_live.py        # Riot Client token → GLZ / PD 對局、牌位、對戰紀錄
│   ├── valo_live_parse.py  # 對局 / 賽後資料整理（純函式）
│   ├── valo_gep.py         # Valorant 進階 TAB（讀 PC 上 Overwolf GEP 的 log）
│   ├── riot_cookie_auth.py # Valorant 小帳登入（ssid 續命）
│   ├── act_live.py         # Activity 的 WebSocket：Discord 登入、點歌 / 控制、表情反應，活動操作也走這條
│   ├── fun.py              # 活動：錢包、綁帳號、打完對局換券、商店、/fun 管理指令
│   ├── fun_web.py          # 活動的 Activity 操作、圖片路由 api/fun/*、慶祝特效廣播
│   ├── gacha.py            # 🎰 轉蛋
│   ├── fishing.py          # 🎣 釣魚
│   ├── minigames.py        # 🃏 21 點 / 拉霸 / 踩地雷 / 猜正反
│   ├── lottery.py          # 🎫 樂透（每天 21:00 開獎）、/lottery 管理指令
│   ├── mono.py             # 🎲 大富翁（賽季、棋盤圖）
│   ├── stock.py            # /stock、/stock-track（報價抓取、開收盤通知）
│   ├── stock_parse.py      # 股票代號解析、報價整理、開收盤時間、走勢圖（純函式）
│   ├── health.py           # /check 健康報告
│   ├── bigtwo.py           # 大老二
│   └── uno.py              # UNO
├── activity/               # Discord Activity 前端（同步歌詞 + LoL + Valorant 畫面 + 活動面板）
│   ├── src/                # 原始碼（main.js 歌詞與模式切換 / lol.js / valo.js / live.js 連線 / fun.js 活動面板 / style.css / index.html）
│   │                       #   fun_mock.js：假後端，網址加 ?mock=1 可在一般瀏覽器測活動面板（&mono=empty = 大富翁還沒開季）
│   └── dist/               # build 結果，bot 直接提供
├── scripts/
│   ├── healthcheck.sh      # 每日健檢（只診斷不修復）
│   ├── ytdlp_upgrade.sh    # 升級 yt-dlp → 用真的歌實測 → 不能播就自動降回
│   └── import_alt_cookie.py # （不建議）從 PC 的 Riot Client 匯入小帳 cookie
├── games/
│   ├── bigtwo_logic.py     # 大老二規則邏輯
│   ├── uno_logic.py        # UNO 規則邏輯
│   ├── card_image.py       # 大老二手牌圖片繪製
│   ├── uno_image.py        # UNO 手牌圖片繪製
│   ├── fun_score.py        # 對局表現分 0–8 → 1–4 張券
│   ├── fun_shop.py         # 商店品項
│   ├── gacha_logic.py      # 轉蛋機率與卡池
│   ├── gacha_image.py      # 轉蛋卡圖
│   ├── fishing_logic.py    # 魚種與釣魚機率
│   ├── minigames_logic.py  # 21 點 / 拉霸 / 踩地雷 / 猜正反規則
│   ├── lottery_logic.py    # 樂透開獎
│   ├── mono_logic.py       # 大富翁棋盤、擲骰、機會卡、賽季結算
│   └── mono_image.py       # 大富翁棋盤圖
├── tests/                  # 單元測試（UNO、repeat、起播重試、歌詞比對、LoL/Valorant 解析、計時列、各活動與換券評分）
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
- 客戶端會記住上一次查「自己」對戰紀錄的範圍，所以 bot 查完 100 場後，**玩家自己客戶端的對戰紀錄頁也會列出 100 場**（上面的標題還是寫近 20 場）。只影響那台 PC 的顯示，不影響戰績，也不影響其他人。
- 遊戲中查其他玩家的資料，客戶端要向伺服器拿，常要十幾到二十幾秒；查不到會每 30 秒自動重試。
- 出裝統計來自 op.gg 的 **ARAM** 數據（沒有大混戰專屬統計），每隻英雄快取 12 小時。推薦出裝的觸發門檻（例如敵方吸血裝佔 10%）是自訂的，畫面上會附實際數字讓人自己判斷。
- 遊戲中的 Live Client API **拿不到海克斯強化**，只有結算畫面看得到；其他玩家的金錢也拿不到，經濟是用裝備合成總價推算。
- 名字被隱藏的玩家不會去查牌位或戰績。
- Valorant 官方 API 不開放給 personal key，`/valo` 改用 [HenrikDev](https://docs.henrikdev.xyz/) 非官方 API（每分鐘 30 次）。
- `/valo-live` 用的是 Riot 內部端點（跟市面 rank 工具同一套：lockfile → entitlements token → GLZ / PD），不在官方 API 範圍，只讀、不碰遊戲程式與記憶體。進行中**沒有即時 K/D、金錢**（Valorant 沒有本機遊戲 API），只有回合比數（Riot Client presence）；個人數據要等賽後。
- Riot 的 mmr 資料裡**非競技模式的本季勝場是壞的**（每個模式都 0 勝、場數相同），所以「目前模式」改用對戰紀錄算最近 10 場。對戰詳情一次抓太多會被 429 限速，已限成同時 2 個、每個間隔 0.25 秒（10 人約 20 秒，先推牌位再補戰績）。
