#!/usr/bin/env bash
# discordbot 每日健檢：只做「診斷」，不做任何修復動作。
# 輸出給 Hermes agent 判讀，對應 ~/.hermes/skills/discordbot-watchdog/SKILL.md 的處理方向。
set -uo pipefail

SVC=discordbot.service
DIR=/home/heart/discordbot
LOG=$DIR/service.log
VENV=$DIR/venv/bin

problems=0
note() { echo "$*"; }
bad()  { echo "$*"; problems=$((problems+1)); }

_run() {
echo "===== DISCORDBOT HEALTHCHECK $(date '+%F %T') ====="

# ── 1. service 狀態 ──
state=$(systemctl is-active $SVC 2>/dev/null)
nrestarts=$(systemctl show $SVC -p NRestarts --value 2>/dev/null)
since=$(systemctl show $SVC -p ActiveEnterTimestamp --value 2>/dev/null)
uptime_s=$(( $(date +%s) - $(date -d "${since:-now}" +%s 2>/dev/null || date +%s) ))

note "[service] state=$state  restarts=$nrestarts  uptime=$((uptime_s/3600))h$(( (uptime_s%3600)/60 ))m  since=$since"
[ "$state" = active ] || bad "PROBLEM service_down: systemd 狀態為 '$state'，不是 active"
starts_1h=$(journalctl -u $SVC --since "1 hour ago" --no-pager 2>/dev/null | grep -c "Started discordbot")
if [ "${uptime_s:-99999}" -lt 600 ] && [ "${starts_1h:-0}" -ge 3 ]; then
  bad "PROBLEM flapping: uptime 僅 $((uptime_s/60)) 分鐘，且近 1 小時啟動了 $starts_1h 次，正在反覆重啟"
elif [ "${uptime_s:-99999}" -lt 600 ]; then
  note "[service] 註：剛啟動不久（$((uptime_s/60)) 分鐘），近 1 小時啟動 $starts_1h 次 — 尚不算異常"
fi

# ── 2. 進程是否真的活著 ──
pid=$(systemctl show $SVC -p MainPID --value 2>/dev/null)
if [ "${pid:-0}" -gt 0 ] && [ -d "/proc/$pid" ]; then
  note "[proc] PID $pid 存活  rss=$(awk '/VmRSS/{print $2" "$3}' /proc/$pid/status 2>/dev/null)"
else
  bad "PROBLEM no_process: 找不到存活的主進程 (MainPID=$pid)"
fi

# ── 3. 上線標記（最近一次啟動後有沒有成功連上 Discord）──
last_start_line=$(grep -n "logging in using static token" "$LOG" 2>/dev/null | tail -1 | cut -d: -f1)
if [ -n "$last_start_line" ]; then
  tail_after=$(tail -n +"$last_start_line" "$LOG")
  # 注意：pipefail 下 `echo|grep -q` 會在 grep 早期命中時因 SIGPIPE(141) 誤判失敗，
  # 改用 grep -c（讀完全部輸入）避免。
  if [ "$(echo "$tail_after" | grep -c "已上線")" -gt 0 ]; then
    note "[login] $(echo "$tail_after" | grep '已上線' | tail -1)"
  else
    bad "PROBLEM login_failed: 最後一次啟動之後沒有出現『已上線』"
  fi
  [ "$(echo "$tail_after" | grep -ciE "LoginFailure|Improper token|401 Unauthorized")" -gt 0 ] \
    && bad "PROBLEM bad_token: log 出現 token 失效訊息"
else
  bad "PROBLEM no_startup_log: log 裡找不到啟動記錄"
fi

# ── 4. 近期播放錯誤（只看尾端 3000 行，log 很大）──
total_lines=$(wc -l < "$LOG" 2>/dev/null || echo 0)
tail_start=$(( total_lines > 3000 ? total_lines - 3000 : 1 ))
[ -n "$last_start_line" ] && [ "$last_start_line" -gt "$tail_start" ] && tail_start=$last_start_line
recent=$(tail -n +"$tail_start" "$LOG" 2>/dev/null)
# 一次 403 會印 3~4 行，只數 ffmpeg 收尾那行才是「次數」
n403=$(echo "$recent" | grep -c "Error opening input files: Server returned 403")
nretry=$(echo "$recent" | grep -c "起播即結束（串流 URL 可能被拒），重抓 URL 重試")
nskip=$(echo "$recent" | grep -c "重試後串流仍被拒，跳過")
nffmpeg=$(echo "$recent" | grep -cE "ffmpeg process .* terminated with return code of [1-9]")
nplayfail=$(echo "$recent" | grep -c "播放失敗")
note "[playback] 本次啟動後：403=$n403 次（重試=$nretry  重試仍失敗跳過=$nskip）  ffmpeg非零退出=$nffmpeg  播放失敗=$nplayfail"
# 偶發 403 由 music.py 自動重抓 URL 救回，不算問題；重試後仍跳過太多首才算
if [ "$nskip" -gt 3 ]; then
  bad "PROBLEM yt_dlp_403: 重抓 URL 後仍 403、已跳過 $nskip 首（先看下方 yt-dlp 是否過期，已最新則是 YouTube 端擋）"
elif [ "$n403" -gt 0 ]; then
  note "[playback] 註：403 為 YouTube 偶發拒絕，已自動重試（跳過 $nskip 首 ≤ 3）— 不算異常"
fi
[ "$nplayfail" -gt 3 ] && bad "PROBLEM playback_failing: 播放失敗 $nplayfail 次"

# ── 5. OOM / 被 kill ──
[ "$(journalctl -u $SVC --since "24 hours ago" --no-pager 2>/dev/null \
    | grep -cE "code=killed, status=9|out-of-memory")" -gt 0 ] \
  && bad "PROBLEM oom_killed: 過去 24h 內被 SIGKILL（很可能 OOM）"

# ── 6. yt-dlp 版本 ──
cur=$($VENV/python -m yt_dlp --version 2>/dev/null)
latest=$($VENV/pip index versions yt-dlp 2>/dev/null | awk -F'LATEST:' '/LATEST/{gsub(/ /,"",$2);print $2}')
note "[yt-dlp] 目前=$cur  最新=${latest:-查詢失敗}"
norm() { echo "$1" | awk -F. '{for(i=1;i<=NF;i++){printf "%d%s", $i+0, (i<NF?".":"")}}'; }
if [ -n "$latest" ] && [ "$(norm "$cur")" != "$(norm "$latest")" ]; then
  bad "PROBLEM yt_dlp_outdated: yt-dlp 落後（$cur → $latest）"
fi

# ── 7. 依賴與磁碟 ──
command -v ffmpeg >/dev/null || bad "PROBLEM no_ffmpeg: 找不到 ffmpeg"
logmb=$(( $(stat -c%s "$LOG" 2>/dev/null || echo 0) / 1048576 ))
diskpct=$(df --output=pcent / | tail -1 | tr -dc '0-9')
note "[disk] service.log=${logmb}MB  根分割區已用=${diskpct}%"
[ "$logmb" -gt 500 ] && bad "PROBLEM log_bloat: service.log 已達 ${logmb}MB"
[ "$diskpct" -gt 90 ] && bad "PROBLEM disk_full: 磁碟已用 ${diskpct}%"

echo "===== 結論：$problems 個問題 ====="
[ "$problems" -eq 0 ] && echo "HEALTHY — 一切正常，不需要任何動作，直接回報正常即可。"
}

# 產生報告 → 同時輸出到 stdout（給 Hermes cron 讀）與存檔（給 bot 的 /check 讀）
REPORT=/home/heart/discordbot/data/healthcheck_latest.txt
out=$(_run)
echo "$out"
if [ -d "$(dirname "$REPORT")" ]; then
  printf '%s\n' "$out" > "$REPORT.tmp" && mv -f "$REPORT.tmp" "$REPORT"
fi
exit 0
