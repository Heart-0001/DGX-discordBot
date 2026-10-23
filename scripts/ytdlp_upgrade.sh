#!/usr/bin/env bash
# 升級 yt-dlp → 用 bot 實際的抓法測真的歌 → 不能播就自動降回原版本。
# bot 每首歌都另開 yt-dlp 子行程，換版本立即生效，不用重啟 discordbot。
# 只測 ytsearch1:lofi 不夠：2026.10.21 搜尋 lofi 能播，但正式歌全部 403。
#
# 用法：ytdlp_upgrade.sh            升到 PyPI 最新版並驗證
#       ytdlp_upgrade.sh --test     只測目前版本
# exit 0 = 已是最新 / 升級成功 / 最新版先前測過是壞的(略過)
#      1 = 新版不能播，已降回舊版，且舊版實測 OK
#      2 = 降回舊版後還是不能播(不是 yt-dlp 版本問題) → 交給 Claude Code
set -uo pipefail

DIR=/home/heart/discordbot
VENV=$DIR/venv/bin
BAD=$DIR/data/ytdlp_bad_versions.txt
FFMPEG=/usr/bin/ffmpeg
# 跟 cogs/music.py 的 _stream_args / FFMPEG_BEFORE_OPTS 一致
FMT='bestaudio[abr<=96]/bestaudio/best'
BEFORE='-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 -thread_queue_size 4096 -probesize 32'
# 真的有人點過的歌(YouTube Music 網址，bot 主要走這條)
SONGS=(
  https://music.youtube.com/watch?v=HhZTYsojXdA
  https://music.youtube.com/watch?v=IscPlLsh6iU
  https://music.youtube.com/watch?v=xfNDApZ1aGA
)

ver() { $VENV/python -m yt_dlp --version 2>/dev/null; }
norm() { echo "$1" | awk -F. '{for(i=1;i<=NF;i++){printf "%d%s", $i+0, (i<NF?".":"")}}'; }

# 每首：yt-dlp 抽串流網址 → ffmpeg 實際解 5 秒。全部過才算 OK。
test_play() {
  local ok=0 url song
  for song in "${SONGS[@]}"; do
    url=$($VENV/python -m yt_dlp --dump-json --quiet --no-warnings --no-playlist \
            --format "$FMT" --ffmpeg-location $FFMPEG "$song" 2>/dev/null \
          | $VENV/python -c 'import sys,json;print(json.loads(sys.stdin.readline())["url"])' 2>/dev/null)
    # shellcheck disable=SC2086
    if [ -n "$url" ] && timeout 60 $FFMPEG -v error $BEFORE -i "$url" -vn -t 5 -f null - >/dev/null 2>&1; then
      echo "  OK   $song"; ok=$((ok+1))
    else
      echo "  FAIL $song"
    fi
  done
  [ "$ok" -eq "${#SONGS[@]}" ]
}

if [ "${1:-}" = --test ]; then
  echo "[yt-dlp] 測試目前版本 $(ver)"
  test_play; exit $?
fi

old=$(ver)
latest=$($VENV/pip index versions yt-dlp 2>/dev/null | awk -F'LATEST:' '/LATEST/{gsub(/ /,"",$2);print $2}')
[ -n "$latest" ] || { echo "[yt-dlp] 查不到最新版本(網路?)，不動"; exit 0; }
if [ "$(norm "$old")" = "$(norm "$latest")" ]; then
  echo "[yt-dlp] 已是最新 $old"; exit 0
fi
if grep -qx "$(norm "$latest")" "$BAD" 2>/dev/null; then
  echo "[yt-dlp] 最新版 $latest 先前實測不能播，維持 $old"; exit 0
fi

echo "[yt-dlp] 升級 $old → $latest"
$VENV/pip install -q "yt-dlp==$latest" 2>&1 | grep -v '^\[notice\]'
if test_play; then
  echo "[yt-dlp] ✅ $latest 實測能播，升級完成"; exit 0
fi

echo "[yt-dlp] ❌ $latest 實測不能播，降回 $old"
echo "$(norm "$latest")" >> "$BAD"
$VENV/pip install -q "yt-dlp==$old" 2>&1 | grep -v '^\[notice\]'
if test_play; then
  echo "[yt-dlp] 已降回 $old，實測能播。$latest 記進 $BAD，之後不再自動升到這版"; exit 1
fi
echo "[yt-dlp] 🚨 降回 $old 也不能播 → 不是版本問題，要交給 Claude Code"; exit 2
