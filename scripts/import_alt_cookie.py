"""把 PC 上 Riot Client 的登入 cookie 存成 data/riot_alt_cookie.json(權限 600)。只印 cookie 名稱，不印值。
用法(在 DGX)：
  ssh pc 'type "%LOCALAPPDATA%\\Riot Games\\Riot Client\\Data\\RiotGamesPrivateSettings.yaml"' | venv/bin/python scripts/import_alt_cookie.py
"""
import json
import os
import re
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'data', 'riot_alt_cookie.json')

text = sys.stdin.read()
cookies = {}
for block in re.split(r'\n\s*-\s+', text):
    name = re.search(r'\bname:\s*"?([\w\-]+)"?', block)
    value = re.search(r'\bvalue:\s*"?([^"\n]+)"?', block)
    if name and value and 'domain' in block:
        cookies[name.group(1)] = value.group(1).strip()
if not cookies:
    print('沒讀到任何 cookie（Riot Client 沒登入？或 ssh 讀不到檔案）')
    sys.exit(1)
d = {'cookies': cookies, 'ssid': cookies.get('ssid', ''), 'imported_at': time.time()}
tmp = OUT + '.tmp'
with open(tmp, 'w', encoding='utf-8') as f:
    json.dump(d, f)
os.chmod(tmp, 0o600)
os.replace(tmp, OUT)
print('已存到', OUT, '；cookie 名稱：', sorted(cookies), '；有 ssid：', bool(d['ssid']))
