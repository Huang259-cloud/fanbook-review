#!/usr/bin/env python3
"""
Fanbook 花园创作者审稿预审脚本

用法:
  python3 fanbook_review.py --task-id 14675 [--theme '主题'] [--dry-run] [--auto-submit]
  python3 fanbook_review.py --list-tasks          # 列出所有活动
  python3 fanbook_review.py --task-id 14675 --dry-run --limit 3  # 测试前3条

认证: 自动从 Edge CDP 9223 的 Fanbook 标签读取 newToken
视频分析: yt-dlp 获取时长 + Edge CDP 截图 + 视觉模型判断内容
"""
import sys
import json
import time
import argparse
import os
import re
from pathlib import Path
from typing import Optional, Tuple, Set, List, Dict, Any

import requests

CDP_PORT   = 9223
BASE_URL   = "https://open.fanbook.cn/mp/138519745866498048/374546854160891904/activity/api/admin"
GUILD_ID   = "387575305969086464"
SCRIPT_DIR = Path(__file__).parent
TOKEN_CACHE = SCRIPT_DIR / ".fanbook_token_cache.json"

REJECT: Dict[str, str] = {
    "duration":    "视频时长不足10秒，不符合投稿要求",
    "duplicate":   "一稿多投，同一视频已在其他活动中投稿，不予通过",
    "caption":     "文案无实质内容，纯数字文案不予通过",
    "no_creation": "未见明显二次创作痕迹，需有贴纸/字幕/特效/滤镜/剪辑等个人创作元素",
    "not_garden":  "视频内容与梦幻花园游戏无关，不符合投稿要求",
    "weibo_topic": "微博投稿含引流话题，仅允许活动话题和#梦幻花园#",
    "mini_game":   "小游戏投稿视频内容需为小游戏实际画面或官方素材库内容",
    "theme":       "视频内容与本期活动主题不符",
}

# 国内视频平台域名（用于识别平台类型）
DOMESTIC: List[str] = ['weibo.com', 'bilibili.com', 'b23.tv', 'douyin.com', 'xiaohongshu.com',
                       'xhslink.com', 'kuaishou.com', 'ixigua.com']

# 默认超时配置（秒）
DEFAULT_TIMEOUT = 15
CDP_TIMEOUT = 5
WS_RECV_MAX_RETRIES = 200

TG_BOT_TOKEN = "8795816152:AAHr4za9I_Fc4lxapfwG6ayQYwfEYPOJNlc"
TG_CHAT_ID   = "8303098474"

def tg_notify(text: str) -> None:
    """发送 Telegram 通知（失败不抛出异常）"""
    try:
        requests.post(
            f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT_ID, "text": text, "parse_mode": "HTML"},
            timeout=10
        )
    except requests.exceptions.RequestException:
        # 静默失败，避免干扰主流程
        pass

# ─── Token ────────────────────────────────────────────────────────────────────

def get_token() -> Tuple[str, str]:
    """从 Edge CDP 获取 Fanbook token，失败则使用缓存"""
    ws = None
    try:
        import websocket
        # 检查 CDP 是否可用
        try:
            resp = requests.get(f"http://localhost:{CDP_PORT}/json/list", timeout=CDP_TIMEOUT)
            resp.raise_for_status()
            tabs = resp.json()
        except requests.exceptions.RequestException as e:
            print(f"⚠  CDP 连接失败: {e}", file=sys.stderr)
            return _load_cached_token()

        tab = next((t for t in tabs if 'fb-mp-inner' in t.get('url', '')), None)
        if not tab:
            print("⚠  未找到 Fanbook 审稿标签", file=sys.stderr)
            return _load_cached_token()

        ws = websocket.create_connection(tab['webSocketDebuggerUrl'], suppress_origin=True)
        ws.send(json.dumps({"id": 999, "method": "Runtime.enable", "params": {}}))
        ctx_id = None
        ws.settimeout(3)
        for _ in range(80):
            try:
                msg = json.loads(ws.recv())
                if msg.get('method') == 'Runtime.executionContextCreated':
                    ctx = msg['params']['context']
                    if ctx.get('auxData', {}).get('isDefault') and 'open.fanbook.cn' in ctx.get('origin', ''):
                        ctx_id = ctx['id']
                        break
            except websocket.WebSocketTimeoutException:
                break
            except Exception:
                continue

        if not ctx_id:
            return _load_cached_token()

        ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate", "params": {
            "expression": "JSON.stringify({token:localStorage.getItem('newToken'),guild:localStorage.getItem('guild_id')})",
            "contextId": ctx_id, "returnByValue": True}}))
        ws.settimeout(5)

        while True:
            try:
                msg = json.loads(ws.recv())
                if msg.get('id') == 1:
                    result_value = msg.get('result', {}).get('result', {}).get('value', '{}')
                    d = json.loads(result_value) if result_value else {}
                    token, guild = d.get('token', ''), d.get('guild') or GUILD_ID
                    if token:
                        # 写入缓存时设置文件权限（仅限当前用户读写）
                        cache_data = json.dumps({"token": token, "guild": guild, "ts": time.time()})
                        TOKEN_CACHE.write_text(cache_data)
                        try:
                            os.chmod(TOKEN_CACHE, 0o600)
                        except OSError:
                            pass  # 某些系统可能不支持 chmod
                        print("✓ Token 读取成功", file=sys.stderr)
                        return token, guild
                    break
            except (json.JSONDecodeError, websocket.WebSocketTimeoutException):
                break
            except Exception:
                continue

        return _load_cached_token()
    except ImportError:
        print("⚠  websocket-client 未安装，使用缓存 token", file=sys.stderr)
        return _load_cached_token()
    except Exception as e:
        print(f"⚠  CDP 失败: {e}", file=sys.stderr)
        return _load_cached_token()
    finally:
        if ws:
            try:
                ws.close()
            except Exception:
                pass

def _load_cached_token() -> Tuple[str, str]:
    """从缓存文件加载 token，缓存有效期20小时"""
    try:
        if not TOKEN_CACHE.exists():
            raise RuntimeError("缓存文件不存在")
        d = json.loads(TOKEN_CACHE.read_text())
        token = d.get('token')
        if not token:
            raise RuntimeError("缓存中无有效 token")
        if (time.time() - d.get('ts', 0)) / 3600 >= 20:
            raise RuntimeError("token 缓存已过期（超过20小时）")
        return token, d.get('guild', GUILD_ID)
    except (json.JSONDecodeError, KeyError, RuntimeError) as e:
        raise RuntimeError(f"无法获取 token: {e}，请在 Edge 打开 Fanbook 审稿页面（CDP 9223）")

# ─── API ──────────────────────────────────────────────────────────────────────

class FanbookAPI:
    """Fanbook API 客户端"""
    def __init__(self, token: str, guild: str):
        self.headers = {
            "Authorization": token,
            "guildId": guild,
            "Content-Type": "application/json",
            "Accept": "application/json"
        }

    def _post(self, path: str, body: Dict[str, Any]) -> Any:
        """发送 POST 请求，处理常见错误"""
        try:
            r = requests.post(
                f"{BASE_URL}/{path}",
                headers=self.headers,
                json=body,
                timeout=DEFAULT_TIMEOUT,
                proxies={'http': None, 'https': None}
            )
            r.raise_for_status()
        except requests.exceptions.Timeout:
            raise RuntimeError(f"/{path} 请求超时（{DEFAULT_TIMEOUT}秒）")
        except requests.exceptions.ConnectionError as e:
            raise RuntimeError(f"/{path} 连接失败: {e}")
        except requests.exceptions.RequestException as e:
            raise RuntimeError(f"/{path} 请求失败: {e}")

        try:
            d = r.json()
        except json.JSONDecodeError:
            raise RuntimeError(f"/{path} 返回非 JSON 数据")

        if d.get('code') != 0:
            raise RuntimeError(f"/{path} 失败: {d.get('msg', '未知错误')}")
        return d.get('data')

    def list_tasks(self):
        return self._post("task/page", {"pageSize": 20, "pageNum": 1}).get('records', [])

    def get_pending_works(self, task_id):
        all_records, page = [], 1
        while True:
            data = self._post("task/queryTaskArt", {
                "taskId": task_id, "pageSize": 50, "pageNo": page, "auditState": 0
            })
            records = data.get('records', [])
            all_records.extend(records)
            total = data.get('total', 0)
            pages = data.get('pages', 1)
            print(f"  拉取第{page}/{pages}页，待审{len(all_records)}/{total}条", file=sys.stderr)
            if page >= pages or not records:
                break
            page += 1
        seen = set()
        pending = [r for r in all_records
                   if r.get('auditStatus') == 0 and not (r['id'] in seen or seen.add(r['id']))]
        print(f"  待审: {len(pending)} 条（共{len(all_records)}条含已审）", file=sys.stderr)
        return pending

    def approve(self, art_id):
        self._post("artAudit/commit", {"artId": art_id, "status": 2})

    def reject(self, art_id, reason):
        self._post("artAudit/commit", {"artId": art_id, "status": 1, "refuseMsg": reason})

# ─── Edge CDP 多帧截图 ────────────────────────────────────────────────────────

def _ws_recv_until(ws, msg_id, timeout=12):
    """从 WebSocket 读消息直到收到指定 id，返回该消息或 None"""
    ws.settimeout(timeout)
    for _ in range(200):
        try:
            msg = json.loads(ws.recv())
            if msg.get('id') == msg_id:
                return msg
        except: break
    return None

def _ws_capture(ws, msg_id):
    """发截图命令，返回 base64 数据或 None"""
    ws.send(json.dumps({"id": msg_id, "method": "Page.captureScreenshot",
                        "params": {"format": "jpeg", "quality": 40}}))
    msg = _ws_recv_until(ws, msg_id, timeout=15)
    return (msg or {}).get('result', {}).get('data') or None

def take_screenshots_cdp(url: str) -> List[str]:
    """
    在 Edge CDP 9223 打开 URL，截取多帧（封面 + 视频内多个时间点）。
    返回 base64 JPEG 列表（至少1张封面，若有视频元素则附加多帧）。
    """
    tab_id = None
    ws = None
    screenshots: List[str] = []

    try:
        import websocket
    except ImportError:
        print("  ⚠  websocket-client 未安装，无法截图", file=sys.stderr)
        return screenshots

    try:
        # 创建新标签页
        try:
            resp = requests.put(f"http://localhost:{CDP_PORT}/json/new", timeout=CDP_TIMEOUT)
            resp.raise_for_status()
            tab_info = resp.json()
            tab_id = tab_info['id']
            ws_url = tab_info['webSocketDebuggerUrl']
        except requests.exceptions.RequestException as e:
            print(f"  ⚠  创建 CDP 标签页失败: {e}", file=sys.stderr)
            return screenshots

        # 激活标签页
        try:
            requests.get(f"http://localhost:{CDP_PORT}/json/activate/{tab_id}", timeout=CDP_TIMEOUT)
        except requests.exceptions.RequestException:
            pass  # 非关键错误，继续执行

        time.sleep(0.5)

        ws = websocket.create_connection(ws_url, suppress_origin=True)
        ws.send(json.dumps({"id": 1, "method": "Page.enable", "params": {}}))
        time.sleep(0.3)
        ws.send(json.dumps({"id": 2, "method": "Page.navigate", "params": {"url": url}}))

        # 等待页面加载
        ws.settimeout(32)
        for _ in range(300):
            try:
                msg = json.loads(ws.recv())
                if msg.get('method') == 'Page.loadEventFired':
                    break
            except websocket.WebSocketTimeoutException:
                break
            except Exception:
                continue
        time.sleep(3)

        # 封面帧（页面初始状态）
        cover = _ws_capture(ws, 10)
        if cover:
            screenshots.append(cover)

        # 尝试获取视频时长以确定 seek 时间点
        ws.send(json.dumps({"id": 45, "method": "Runtime.evaluate", "params": {
            "expression": "(function(){var v=document.querySelector('video');return v ? {duration: v.duration, hasVideo: true} : {duration: 0, hasVideo: false};})()",
            "returnByValue": True
        }}))
        video_info_msg = _ws_recv_until(ws, 45, timeout=5)
        video_info = (video_info_msg or {}).get('result', {}).get('result', {}).get('value', {})
        video_duration = video_info.get('duration', 0) if isinstance(video_info, dict) else 0
        has_video = video_info.get('hasVideo', False) if isinstance(video_info, dict) else False

        if has_video and video_duration > 0:
            # 计算中间帧时间点（确保不超过视频时长）
            candidates = [min(10, video_duration / 2)]

            # 静音并尝试播放以解锁 seek
            ws.send(json.dumps({"id": 50, "method": "Runtime.evaluate", "params": {
                "expression": "(function(){var v=document.querySelector('video');if(v){v.muted=true;v.play().catch(function(){});}return true;})()",
                "returnByValue": True
            }}))
            _ws_recv_until(ws, 50, timeout=5)

            for i, t in enumerate(candidates):
                # Seek 到指定时间点
                ws.send(json.dumps({"id": 60 + i, "method": "Runtime.evaluate", "params": {
                    "expression": f"(function(){{var v=document.querySelector('video');if(v){{v.currentTime={t};}}return true;}})()",
                    "returnByValue": True
                }}))
                _ws_recv_until(ws, 60 + i, timeout=5)
                time.sleep(0.8)

                frame = _ws_capture(ws, 70 + i)
                if frame:
                    screenshots.append(frame)
        elif has_video:
            print("  ⚠  无法获取视频时长，仅返回封面帧", file=sys.stderr)
        else:
            print("  ⚠  未找到 video 元素，仅封面帧", file=sys.stderr)

    except websocket.WebSocketException as e:
        print(f"  ✗ CDP WebSocket 错误: {e}", file=sys.stderr)
    except Exception as e:
        print(f"  ✗ CDP 截图失败: {e}", file=sys.stderr)
    finally:
        if ws:
            try:
                ws.close()
            except Exception:
                pass
        if tab_id:
            try:
                requests.get(f"http://localhost:{CDP_PORT}/json/close/{tab_id}", timeout=CDP_TIMEOUT)
            except Exception:
                pass

    return screenshots

# ─── 视觉模型内容判断 ──────────────────────────────────────────────────────────

def _get_vision_client():
    """读取视觉模型配置，优先读同目录 config.json，其次读 openclaw.json"""
    try:
        import openai, httpx

        # 优先：同目录 config.json
        local_cfg = SCRIPT_DIR / "config.json"
        if local_cfg.exists():
            cfg = json.loads(local_cfg.read_text())
            api_key  = cfg.get("api_key", "")
            base_url = cfg.get("base_url", "https://coding.dashscope.aliyuncs.com/v1")
            model    = cfg.get("model", "qwen-vl-max")
            if api_key:
                client = openai.OpenAI(api_key=api_key, base_url=base_url,
                                       http_client=httpx.Client(trust_env=False,
                                           timeout=httpx.Timeout(30.0)))
                return client, model

        # 兜底：openclaw.json（原有逻辑）
        cfg_path = Path.home() / ".openclaw/openclaw.json"
        cfg = json.loads(cfg_path.read_text())
        provider = cfg.get("models", {}).get("providers", {}).get("aliyun-coding", {})
        api_key  = provider.get("apiKey", "")
        base_url = provider.get("baseUrl", "")
        if not api_key or not base_url:
            return None
        model = next(
            (m["id"] for m in provider.get("models", [])
             if "image" in m.get("input", [])),
            None
        )
        if not model:
            return None
        client = openai.OpenAI(api_key=api_key, base_url=base_url,
                               http_client=httpx.Client(trust_env=False,
                                   timeout=httpx.Timeout(30.0)))
        return client, model
    except Exception as e:
        print(f"  ⚠  读取 openclaw 配置失败: {e}", file=sys.stderr)
        return None

def judge_with_vision(screenshots: List[str], title: str, url: str, theme: Optional[str]) -> List[str]:
    """发多帧截图给视觉模型，返回违规原因列表"""
    try:
        vc = _get_vision_client()
        if not vc:
            print("  ⚠  无视觉模型配置，跳过内容规则", file=sys.stderr)
            return []
        client, model = vc
        print(f"  🤖 {model} ({len(screenshots)}帧)", end='', flush=True)

        is_weibo = 'weibo.com' in url
        is_mini_game = any(k in title for k in ['小游戏', '蛋仔'])
        is_daily_theme = theme and any(k in theme for k in ['班味', '日常', '消除班味'])

        weibo_rule = '4. 微博话题：页面中的话题标签是否只有活动话题+#梦幻花园#（有其他引流话题→weibo_topic_fail:true）\n' if is_weibo else ''
        mini_rule = '8. 小游戏：视频内容是否为小游戏画面或官方素材库内容（不符→mini_game_fail:true）\n' if is_mini_game else ''

        if is_daily_theme:
            theme_rule = f'9. 主题「{theme}」：这是极宽泛的日常主题，只要内容与游戏/休闲/放松/日常生活沾边均算相关，仅当内容完全无关（如明显广告/政治等）才theme_fail:true，否则一律false。\n'
        elif theme:
            theme_rule = f'9. 主题：视频内容是否与本期主题「{theme}」相关（不相关→theme_fail:true）\n'
        else:
            theme_rule = ''

        frame_desc = "第1张是页面截图（含话题/标题），后续是视频不同时间点的帧。" if len(screenshots) > 1 else "仅有封面帧，判断要宽松。"
        daily_note = '本期为日常活动，not_garden 标准极宽：只要有游戏画面、游戏相关元素、或日常休闲内容均算通过，仅当内容完全与游戏无关（纯生活vlog、纯其他游戏）才考虑拒绝。' if is_daily_theme else ''

        prompt = f"""你是梦幻花园（Gardenscapes）创作者活动审稿员，根据多张截图综合判断投稿合规性。
{frame_desc}

投稿标题：{title}

梦幻花园是 Playrix 出品的花园改造手游，卡通风格，有花园场景、管家 Austin、三消关卡等元素。

【重要】审核标准非常宽松，默认通过，只在极端情况下拒绝。有疑问一律false。{daily_note}

0. 视频时长不足10秒（too_short）：从截图中视频播放器显示的时长读取，若时长明确显示<10秒→true，否则false。

1. 无二创（no_creation）：所有帧均为100%未加工原始游戏画面（无任何贴纸/文字/特效/滤镜/转场/水印）→true。有哪怕一丁点叠加元素→false。

2. 与游戏无关（not_garden）：内容明显与梦幻花园毫无关联（完全是其他游戏/日常vlog等）→true。有游戏画面或相关元素→false。
{weibo_rule}{mini_rule}{theme_rule}
只返回一行JSON，不要解释：{{"too_short":false,"no_creation":false,"not_garden":false,"weibo_topic_fail":false,"mini_game_fail":false,"theme_fail":false}}"""

        content: List[Dict[str, Any]] = [
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}
            for b64 in screenshots
        ]
        content.append({"type": "text", "text": prompt})

        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": content}],
            max_tokens=200,
            temperature=0
        )
        text = resp.choices[0].message.content.strip()

        # 提取 JSON
        m = re.search(r'\{.*?\}', text, re.DOTALL)
        if not m:
            print(f"  ⚠  视觉模型输出异常（无JSON）: {text[:100]}", file=sys.stderr)
            return []

        try:
            v = json.loads(m.group())
        except json.JSONDecodeError as e:
            print(f"  ⚠  视觉模型 JSON 解析失败: {e}, 内容: {text[:100]}", file=sys.stderr)
            return []

        out: List[str] = []
        if v.get('too_short'):
            out.append(REJECT["duration"])
        if v.get('no_creation'):
            out.append(REJECT["no_creation"])
        if v.get('not_garden'):
            out.append(REJECT["not_garden"])
        if v.get('weibo_topic_fail'):
            out.append(REJECT["weibo_topic"])
        if v.get('mini_game_fail'):
            out.append(REJECT["mini_game"])
        if v.get('theme_fail'):
            out.append(REJECT["theme"])
        return out
    except Exception as e:
        print(f"  ⚠  视觉判断失败: {e}", file=sys.stderr)
        return []

# ─── 规则引擎 ─────────────────────────────────────────────────────────────────

def check_rules(work: Dict[str, Any], screenshots: List[str], theme: Optional[str], seen_urls: Set[str]) -> Tuple[Optional[bool], List[str]]:
    """
    检查投稿是否符合规则。
    返回: (passed, reasons)
    - passed=True: 通过
    - passed=False: 拒绝（reasons 包含原因）
    - passed=None: 需要人工复核（如截图失败）
    """
    url = work.get('artUrl', '')
    title = (work.get('artTitle') or '').strip()
    reasons: List[str] = []

    # 一稿多投检测
    norm = url.split('?')[0].rstrip('/')
    if norm in seen_urls:
        reasons.append(REJECT["duplicate"])
    seen_urls.add(norm)

    # 纯数字文案检测（长度≥3才判，避免误杀连载编号如"7"、"8"）
    if title and len(title.strip()) >= 3 and re.fullmatch(r'[\d\s\.\-\_\/\\]+', title):
        reasons.append(REJECT["caption"])

    # 视觉模型内容判断（含时长）
    if screenshots:
        vision_reasons = judge_with_vision(screenshots, title, url, theme)
        reasons.extend(vision_reasons)
    else:
        print("  ⚠  无截图，标记人工复核", file=sys.stderr)
        return None, []  # None 表示需要人工复核，不自动提交

    return len(reasons) == 0, reasons

# ─── 主流程 ───────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(description='Fanbook 花园审稿预审')
    p.add_argument('--task-id', type=int)
    p.add_argument('--list-tasks', action='store_true')
    p.add_argument('--theme', help='本期活动主题关键词，如"春日"')
    p.add_argument('--dry-run', action='store_true')
    p.add_argument('--auto-submit', action='store_true')
    p.add_argument('--output', help='CSV 输出路径')
    p.add_argument('--limit', type=int)
    args = p.parse_args()

    print("🔑 获取 token...")
    token, guild = get_token()
    api = FanbookAPI(token, guild)

    if args.list_tasks or not args.task_id:
        print("\n📋 活动列表:")
        for t in api.list_tasks():
            print(f"  [{t['id']}] {t['taskTitle']}  ({t.get('startDatetime','')[:10]} ~ {t.get('endDatetime','')[:10]})")
        if args.list_tasks: return
        task_id = int(input("\n请输入活动 ID: ").strip())
    else:
        task_id = args.task_id

    print(f"\n📥 拉取活动 {task_id} 待审稿...")
    works = api.get_pending_works(task_id)
    if args.limit: works = works[:args.limit]
    total = len(works)
    print(f"共 {total} 条\n")
    if total == 0: print("✅ 无待审稿"); return

    results, seen_urls = [], set()

    for i, work in enumerate(works, 1):
        art_id = work['id']
        title  = (work.get('artTitle') or '').strip()
        url    = work.get('artUrl', '')
        nick   = work.get('nickName', '')

        print(f"[{i}/{total}] {nick} — {title[:35]}")
        print(f"  🔗 {url[:90]}")

        # Edge CDP 多帧截图（时长由视觉模型从播放器读取）
        print(f"  📸 截帧...", end='', flush=True)
        screenshots = take_screenshots_cdp(url)
        print(f" {len(screenshots)}帧{'✓' if screenshots else ' (失败，标记复核)'}")

        passed, reasons = check_rules(work, screenshots, args.theme, seen_urls)
        needs_review = (passed is None)  # 截图失败，无法视觉判断

        if needs_review:
            print(f"  ⚠️  截图失败，人工复核")
            passed = True  # 不自动提交（auto-submit 跳过复核项）
        elif passed:
            print(f"  ✅ 通过")
        else:
            for j, r in enumerate(reasons):
                print(f"  {'❌' if j==0 else '  '} {'拒绝: ' if j==0 else '+ '}{r[:65]}")

        results.append({'id': art_id, 'nick': nick, 'title': title, 'url': url,
                        'passed': passed, 'needs_review': needs_review,
                        'reasons': reasons, 'reject_msg': '；'.join(reasons)})
        print()

    pass_n   = sum(1 for r in results if r['passed'])
    reject_n = sum(1 for r in results if not r['passed'])
    print("=" * 60)
    print(f"📊 共 {total} 条 | ✅ {pass_n} 通过 | ❌ {reject_n} 拒绝\n")
    review_n = sum(1 for r in results if r.get('needs_review'))
    print("| # | 昵称 | 标题 | 结果 | 原因 |")
    print("|---|------|------|------|------|")
    for i, r in enumerate(results, 1):
        flag = '⚠️复核' if r.get('needs_review') else ('✅' if r['passed'] else '❌')
        print(f"| {i} | {r['nick'][:12]} | {r['title'][:20]} | {flag} | {r['reject_msg'][:40]} |")

    if args.output:
        import csv
        with open(args.output, 'w', newline='', encoding='utf-8-sig') as f:
            w = csv.DictWriter(f, fieldnames=['id','nick','title','url','passed','reject_msg'])
            w.writeheader()
            for r in results:
                w.writerow({k: r[k] for k in ['id','nick','title','url','passed','reject_msg']})
        print(f"\n📄 已保存到 {args.output}")

    if args.dry_run:
        print("\n🔍 --dry-run，未提交"); return

    if not args.auto_submit:
        if input(f"\n确认提交？({pass_n} 通过/{reject_n} 拒绝) [y/N]: ").lower() != 'y':
            print("已取消"); return

    print("\n🚀 提交中...")
    ok, fail, skipped = 0, 0, 0
    for r in results:
        if r.get('needs_review'):
            print(f"  ⏭  {r['id']} {r['nick']} — 截图失败，跳过（需人工复核）")
            skipped += 1
            continue
        try:
            if r['passed']: api.approve(r['id'])
            else: api.reject(r['id'], r['reject_msg'][:200])
            ok += 1
        except Exception as e:
            print(f"  ⚠  {r['id']} 失败: {e}"); fail += 1
    print(f"✅ 完成 {ok}/{total}{'，跳过(复核)'+str(skipped) if skipped else ''}{'，失败'+str(fail) if fail else ''}")

if __name__ == '__main__':
    main()
