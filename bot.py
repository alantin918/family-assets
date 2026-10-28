#!/usr/bin/env python3
"""
家庭資產 Telegram Bot (@ALANANNE_FAMILY_bot)
支援功能：
  • 📊 資產總覽 (/start, /summary)
  • 🏦 帳戶明細 (/accounts)
  • 📈 歷月趨勢 (/history)
  • 🔄 同步試算表＋更新網站 (/sync)
  • 🌐 線上儀表板連結 (/web)
"""

import csv
import html
import json
import os
import socket
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

# 優先使用 IPv4，避免 macOS IPv6 超時造成回應延遲
_orig_getaddrinfo = socket.getaddrinfo


def _ipv4_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    return _orig_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)


socket.getaddrinfo = _ipv4_getaddrinfo

BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / ".env"
SUMMARY_CSV = BASE_DIR / "summary.csv"
BALANCES_CSV = BASE_DIR / "balances.csv"
AUTH_FILE = BASE_DIR / ".authorized_users.json"
PID_FILE = BASE_DIR / ".bot.pid"
WEB_URL = "https://alantin918.github.io/family-assets/"


def load_env(path: Path) -> dict:
    env = {}
    if not path.exists():
        return env
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def ensure_single_instance():
    if PID_FILE.exists():
        try:
            old_pid = int(PID_FILE.read_text().strip())
            if old_pid != os.getpid():
                cmd = subprocess.check_output(["ps", "-p", str(old_pid), "-o", "command="], text=True).strip()
                if "bot.py" in cmd and "家庭資產" in cmd:
                    print(f"⚠️ 另一個 bot 實例已在執行中 (PID {old_pid})，結束本次啟動。")
                    sys.exit(0)
        except Exception:
            pass
    PID_FILE.write_text(str(os.getpid()))


def load_allowed_users(env: dict) -> set:
    ids = set()
    raw = env.get("ALLOWED_USER_IDS", "")
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit():
            ids.add(int(part))
    if AUTH_FILE.exists():
        try:
            saved = json.loads(AUTH_FILE.read_text(encoding="utf-8"))
            for uid in saved:
                ids.add(int(uid))
        except Exception:
            pass
    return ids


def save_authorized_user(uid: int, allowed: set):
    allowed.add(uid)
    AUTH_FILE.write_text(json.dumps(sorted(list(allowed))), encoding="utf-8")


def tg_api(token: str, method: str, payload: dict = None, timeout: int = 35) -> dict:
    url = f"https://api.telegram.org/bot{token}/{method}"
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main_keyboard() -> dict:
    return {
        "keyboard": [
            [{"text": "📊 資產總覽"}, {"text": "🏦 帳戶明細"}],
            [{"text": "📈 歷月趨勢"}, {"text": "🔄 同步試算表"}],
            [{"text": "🌐 線上儀表板"}],
        ],
        "resize_keyboard": True,
    }


def inline_web_button() -> dict:
    return {
        "inline_keyboard": [
            [{"text": "🌐 開啟家庭資產視覺化儀表板", "url": WEB_URL}]
        ]
    }


def fmt_nt(v: int) -> str:
    return f"NT${v:,}"


def fmt_signed(v: int) -> str:
    if v > 0:
        return f"+NT${v:,}"
    elif v < 0:
        return f"-NT${abs(v):,}"
    return "±NT$0"


def read_data():
    with open(SUMMARY_CSV, encoding="utf-8") as f:
        summary = [
            {"date": r["date"], "total": int(r["total"]), "after_joint": int(r["after_joint"])}
            for r in csv.DictReader(f)
        ]
    with open(BALANCES_CSV, encoding="utf-8") as f:
        balances = [
            {"date": r["date"], "account": r["account"], "category": r["category"], "amount": int(r["amount"])}
            for r in csv.DictReader(f)
        ]
    return summary, balances


def build_summary_text() -> str:
    summary, balances = read_data()
    cur = summary[-1]
    prev = summary[-2] if len(summary) > 1 else cur

    cur_joint = cur["total"] - cur["after_joint"]
    prev_joint = prev["total"] - prev["after_joint"]

    d_total = cur["total"] - prev["total"]
    d_joint = cur_joint - prev_joint
    d_personal = cur["after_joint"] - prev["after_joint"]

    cur_bals = [b for b in balances if b["date"] == cur["date"]]
    bank_sum = sum(b["amount"] for b in cur_bals if b["category"] == "銀行存款")
    stock_sum = sum(b["amount"] for b in cur_bals if b["category"] == "證券")
    epay_sum = sum(b["amount"] for b in cur_bals if b["category"] == "電子支付")
    cash_sum = bank_sum + epay_sum
    joint_in_stock = max(0, cur_joint - cash_sum)

    lines = [
        f"📊 <b>家庭資產最新總覽</b>（截至 <code>{cur['date']}</code>）",
        "",
        f"💰 <b>總資產</b>：<code>{fmt_nt(cur['total'])}</code>（較上期 {fmt_signed(d_total)}）",
        f"💑 <b>夫妻共同基金</b>：<code>{fmt_nt(cur_joint)}</code>（占 {cur_joint / cur['total'] * 100:.1f}%｜{fmt_signed(d_joint)}）",
        f"🧑 <b>Alan 可用</b>：<code>{fmt_nt(cur['after_joint'])}</code>（較上期 {fmt_signed(d_personal)}）",
        "",
        "─── 資金存放結構 ───",
        f"📈 <b>證券股票（含在途）</b>：<code>{fmt_nt(stock_sum)}</code>（{stock_sum / cur['total'] * 100:.1f}%）",
        f"🏦 <b>銀行存款＋電支現金</b>：<code>{fmt_nt(cash_sum)}</code>（{cash_sum / cur['total'] * 100:.1f}%）",
    ]
    if joint_in_stock > 0:
        lines.append(f"⚠️ 共同基金約有 <code>{fmt_nt(joint_in_stock)}</code> 放在股票部位中")
    else:
        lines.append("✅ 現金部位足以完全涵蓋整筆共同基金")

    lines.append(f'\n🔗 <a href="{WEB_URL}">點此開啟線上圖表儀表板</a>')
    return "\n".join(lines)


def build_accounts_text() -> str:
    summary, balances = read_data()
    cur_date = summary[-1]["date"]
    prev_date = summary[-2]["date"] if len(summary) > 1 else cur_date

    cur_map = {b["account"]: b for b in balances if b["date"] == cur_date}
    prev_map = {b["account"]: b["amount"] for b in balances if b["date"] == prev_date}
    total = sum(b["amount"] for b in cur_map.values()) or 1

    items = []
    for acct, b in cur_map.items():
        amt = b["amount"]
        delta = amt - prev_map.get(acct, 0)
        if amt == 0 and delta == 0:
            continue
        items.append((amt, acct, b["category"], delta))
    items.sort(reverse=True)

    lines = [f"🏦 <b>各帳戶最新餘額</b>（<code>{cur_date}</code> vs <code>{prev_date}</code>）\n"]
    for amt, acct, cat, delta in items:
        pct = amt / total * 100
        d_str = "—" if delta == 0 else fmt_signed(delta)
        icon = "📈" if cat == "證券" else ("📱" if cat == "電子支付" else "🏦")
        lines.append(f"{icon} <b>{html.escape(acct)}</b>：<code>{fmt_nt(amt)}</code>（{pct:.1f}%｜{d_str}）")

    return "\n".join(lines)


def build_history_text() -> str:
    summary, _ = read_data()
    lines = ["📈 <b>歷月資產與共同基金趨勢</b>\n"]
    for r in summary:
        joint = r["total"] - r["after_joint"]
        lines.append(
            f"• <code>{r['date']}</code>｜總 <code>{fmt_nt(r['total'])}</code>｜共同 <code>{fmt_nt(joint)}</code>｜可用 <code>{fmt_nt(r['after_joint'])}</code>"
        )
    return "\n".join(lines)


def send_msg(token: str, chat_id: int, text: str, reply_markup: dict = None):
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        tg_api(token, "sendMessage", payload)
    except Exception as e:
        print(f"sendMessage error: {e}", file=sys.stderr)


def main():
    ensure_single_instance()
    env = load_env(ENV_PATH)
    token = env.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        print("❌ .env 尚未設定 TELEGRAM_BOT_TOKEN", file=sys.stderr)
        sys.exit(1)

    allowed_users = load_allowed_users(env)
    valid_passcodes = {
        k.strip()
        for k in [env.get("WEB_PASSCODE", ""), env.get("APPS_SCRIPT_KEY", ""), "alan2026"]
        if k.strip()
    }

    try:
        tg_api(token, "setMyCommands", {
            "commands": [
                {"command": "summary", "description": "📊 查看最新家庭資產與共同基金總覽"},
                {"command": "accounts", "description": "🏦 查看各銀行與證券帳戶最新餘額"},
                {"command": "history", "description": "📈 查看歷月共同基金與資產成長趨勢"},
                {"command": "sync", "description": "🔄 從 Google 試算表同步最新資料並更新網站"},
                {"command": "web", "description": "🌐 開啟 GitHub Pages 視覺化圖表儀表板"},
            ]
        })
    except Exception as e:
        print(f"Warning: setMyCommands failed: {e}", file=sys.stderr)

    print("🤖 家庭資產 Telegram Bot (@ALANANNE_FAMILY_bot) 已啟動！", flush=True)
    offset = 0

    while True:
        try:
            res = tg_api(token, "getUpdates", {"offset": offset, "timeout": 25}, timeout=35)
            for upd in res.get("result", []):
                offset = upd["update_id"] + 1
                msg = upd.get("message") or upd.get("edited_message")
                if not msg:
                    continue
                chat_id = msg["chat"]["id"]
                user_id = msg.get("from", {}).get("id", 0)
                text = (msg.get("text") or "").strip()
                if not text:
                    continue

                # 權限檢查：若尚未在白名單，輸入通關密碼即可永久解鎖（方便太太 Anne 直接加入使用）
                if allowed_users and user_id not in allowed_users:
                    if text in valid_passcodes:
                        save_authorized_user(user_id, allowed_users)
                        send_msg(
                            token,
                            chat_id,
                            "🎉 <b>驗證成功！歡迎使用家庭資產小幫手</b>\n下方按鈕可隨時查詢最新共同基金與帳戶明細：",
                            main_keyboard(),
                        )
                        send_msg(token, chat_id, build_summary_text(), inline_web_button())
                    else:
                        send_msg(
                            token,
                            chat_id,
                            "🔐 請先輸入家庭資產<b>通關密碼</b>以啟用查詢權限（只需驗證這一次）：",
                        )
                    continue

                if text in ("/start", "/summary", "📊 資產總覽"):
                    send_msg(token, chat_id, build_summary_text(), main_keyboard())
                elif text in ("/accounts", "🏦 帳戶明細"):
                    send_msg(token, chat_id, build_accounts_text(), main_keyboard())
                elif text in ("/history", "📈 歷月趨勢"):
                    send_msg(token, chat_id, build_history_text(), main_keyboard())
                elif text in ("/web", "🌐 線上儀表板"):
                    send_msg(
                        token,
                        chat_id,
                        "🌐 <b>家庭資產資金流視覺化儀表板</b>\n請點擊下方按鈕開啟（首次開啟輸入通關密碼後會自動記住）：",
                        inline_web_button(),
                    )
                elif text in ("/sync", "🔄 同步試算表"):
                    send_msg(token, chat_id, "⏳ 正在從 Google 試算表抓取最新「財務」資料並同步至 GitHub 網站，請稍候約 15 秒...")
                    try:
                        proc = subprocess.run(
                            [sys.executable, str(BASE_DIR / "sync.py"), "--push"],
                            cwd=BASE_DIR,
                            capture_output=True,
                            text=True,
                            timeout=60,
                        )
                        if proc.returncode == 0:
                            send_msg(token, chat_id, "✅ <b>Google 試算表與 GitHub 網站同步完成！</b>\n\n" + build_summary_text(), main_keyboard())
                        else:
                            err_msg = html.escape(proc.stderr[-500:] or proc.stdout[-500:])
                            send_msg(token, chat_id, f"❌ 同步失敗：\n<pre>{err_msg}</pre>", main_keyboard())
                    except Exception as e:
                        send_msg(token, chat_id, f"❌ 同步發生錯誤：<code>{html.escape(str(e))}</code>", main_keyboard())
                else:
                    send_msg(
                        token,
                        chat_id,
                        "請點選下方按鈕或使用指令：\n• <code>/summary</code> 資產總覽\n• <code>/accounts</code> 帳戶明細\n• <code>/history</code> 歷月趨勢\n• <code>/sync</code> 同步試算表\n• <code>/web</code> 線上儀表板",
                        main_keyboard(),
                    )
        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"Polling error: {e}", file=sys.stderr, flush=True)
            time.sleep(5)


if __name__ == "__main__":
    main()
