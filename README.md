# 🎮 DGX Discord Bot

個人多功能 Discord Bot，整合 **音樂播放**、**大老二** 與 **UNO** 兩款多人卡牌遊戲，全部以 slash command 操作。

---

## 功能特色

### 🎵 音樂播放
- **YouTube Music 優先搜尋** — 使用 ytmusicapi 在 YTMusic 歌曲庫搜尋，自動評分挑選最官方的版本（官方 Audio / MV 優先，過濾翻唱、字幕版、卡拉OK）
- **Autoplay 自動推薦** — 歌曲播完後依 YouTube Music Radio 自動接下一首，背景預載零間隔接播
- **播放清單支援** — 貼上 YouTube 播放清單連結即可整批加入 queue（無數量上限）
- **隨機播放清單** — 播放清單可自動打亂順序
- **插播下一首** — 將歌曲插入到 queue 第一位

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
```
> Bot Token 從 [Discord Developer Portal](https://discord.com/developers/applications) 取得。
> 需開啟 **Message Content Intent** 與 **Voice States Intent**。

### 4. 啟動 Bot
```bash
python bot.py
```
> 啟動後會自動把 slash command 同步到所在的每個伺服器；也可在伺服器內用 `!sync` 手動重新同步。

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
| `/autoplay` | 開啟 / 關閉 Autoplay。 |
| `/skipautoplay` | 換一首 Autoplay 推薦（不跳掉目前歌曲）。 |
| `/volume <0-100>` | 調整音量。 |
| `/info` | 顯示 Bot 連線與播放狀態。 |

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
  └─ ytmusicapi.get_watch_playlist(radio=True)
       └─ 過濾已播過的歌（依標題比對）→ 預載串流 URL → 零間隔接播
```

---

## 檔案結構

```
discordbot/
├── bot.py                  # Bot 主程式：啟動、載入 cogs、slash command 同步
├── cogs/
│   ├── music.py            # 音樂播放
│   ├── bigtwo.py           # 大老二
│   └── uno.py              # UNO
├── games/
│   ├── bigtwo_logic.py     # 大老二規則邏輯
│   ├── uno_logic.py        # UNO 規則邏輯
│   ├── card_image.py       # 大老二手牌圖片繪製
│   └── uno_image.py        # UNO 手牌圖片繪製
├── tests/                  # UNO 測試（含 CPU 對手）
├── data/                   # 執行時遊戲統計（不納入版控）
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
