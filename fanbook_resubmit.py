#!/usr/bin/env python3
"""
从 fanbook_review.py 输出的 CSV 重新提交未成功的审核结果。
用法: python3 fanbook_resubmit.py /tmp/fanbook_result_14675.csv
"""
import sys, csv, json, time, requests
from pathlib import Path

TOKEN_CACHE = Path(__file__).parent / ".fanbook_token_cache.json"
BASE_URL    = "https://open.fanbook.cn/mp/138519745866498048/374546854160891904/activity/api/admin"
GUILD_ID    = "387575305969086464"

def get_token():
    d = json.loads(TOKEN_CACHE.read_text())
    return d['token'], d.get('guild', GUILD_ID)

def submit(token, guild, art_id, passed, reject_msg):
    h = {"Authorization": token, "guildId": guild,
         "Content-Type": "application/json", "Accept": "application/json"}
    if passed:
        body = {"artId": int(art_id), "status": 2}
    else:
        body = {"artId": int(art_id), "status": 1, "refuseMsg": reject_msg[:200]}
    r = requests.post(f"{BASE_URL}/artAudit/commit", headers=h, json=body,
                      timeout=15, proxies={'http': None, 'https': None})
    d = r.json()
    if d.get('code') != 0:
        raise RuntimeError(d.get('msg', '未知错误'))

csv_path = sys.argv[1] if len(sys.argv) > 1 else "/tmp/fanbook_result_14675.csv"
token, guild = get_token()

with open(csv_path, encoding='utf-8-sig') as f:
    rows = list(csv.DictReader(f))

ok = fail = skip = 0
for r in rows:
    passed = r['passed'].lower() == 'true'
    result_str = '✅' if passed else '❌'
    try:
        submit(token, guild, r['id'], passed, r.get('reject_msg', ''))
        print(f"  {result_str} {r['id']} {r['nick']} — {r['title'][:30]}")
        ok += 1
        time.sleep(0.3)
    except Exception as e:
        msg = str(e)
        if '已审核' in msg or 'already' in msg.lower():
            print(f"  ⏭  {r['id']} 已审核，跳过")
            skip += 1
        else:
            print(f"  ⚠  {r['id']} 失败: {msg}")
            fail += 1

print(f"\n完成: 提交{ok} / 跳过{skip} / 失败{fail}")
