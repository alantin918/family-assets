#!/usr/bin/env python3
"""
家庭資產 Telegram Bot (@ALANANNE_FAMILY_bot)
支援功能：
  • 📊 資產總覽 (/start, /summary)
  • 💑 共同基金分析 (/joint) — 現金覆蓋率、股票壓力測試、50/60/100萬目標倒數
  • 🏦 帳戶明細 (/accounts)
  • 💸 本期資金流向 (/flows) — 支援切換各期區間流入/流出排行
  • 📐 財務健康指標 (/stats) — 累計成長率、月均淨增、歷史最高/最低極值、集中度
  • 🔍 單一帳戶查詢 (/acct) — 點選按鈕查看單一帳戶歷月水位
  • 📒 共同基金帳本 (/ledger) — 試算表左側「夢想撲滿」近期收支紀錄
  • 📈 歷月趨勢 (/history)
  • 🔄 同步試算表＋更新網站 (/sync)
  • 🌐 線上儀表板連結 (/web)
"""

import csv
import html
import json
import math
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
LEDGER_CSV = BASE_DIR / "ledger.csv"
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
            [{"text": "📊 資產總覽"}, {"text": "💑 共同基金分析"}],
            [{"text": "🏦 帳戶明細"}, {"text": "💸 本期資金流向"}],
            [{"text": "📐 財務健康指標"}, {"text": "🔍 單一帳戶查詢"}],
            [{"text": "📒 共同基金帳本"}, {"text": "📈 歷月趨勢"}],
            [{"text": "🔄 同步試算表"}, {"text": "🌐 線上儀表板"}],
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
    if v < 0:
        return f"-NT${abs(v):,}"
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
    ledger = []
    if LEDGER_CSV.exists():
        with open(LEDGER_CSV, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                ledger.append({
                    "item": r["item"],
                    "alan": int(r["alan"] or 0),
                    "anne": int(r["anne"] or 0),
                    "expense": int(r["expense"] or 0),
                    "balance": int(r["balance"] or 0),
                    "note": r.get("note", ""),
                })
    return summary, balances, ledger


def build_summary_text() -> str:
    summary, balances, _ = read_data()
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
    safety_pct = (cur["after_joint"] / stock_sum * 100) if stock_sum > 0 else 100.0

    lines = [
        f"📊 <b>家庭資產最新總覽</b>（截至 <code>{cur['date']}</code>）",
        "",
        f"💰 <b>總資產</b>：<code>{fmt_nt(cur['total'])}</code>（較上期 {fmt_signed(d_total)}）",
        f"💑 <b>夫妻共同基金</b>：<code>{fmt_nt(cur_joint)}</code>（占 {cur_joint / cur['total'] * 100:.1f}%｜{fmt_signed(d_joint)}）",
        f"🧑 <b>Alan 可用</b>：<code>{fmt_nt(cur['after_joint'])}</code>（較上期 {fmt_signed(d_personal)}）",
        "",
        "─── 資金存放與安全指標 ───",
        f"📈 <b>證券股票（含在途）</b>：<code>{fmt_nt(stock_sum)}</code>（{stock_sum / cur['total'] * 100:.1f}%）",
        f"🏦 <b>銀行存款＋電支現金</b>：<code>{fmt_nt(cash_sum)}</code>（{cash_sum / cur['total'] * 100:.1f}%）",
        f"🛡️ <b>股票可承受回檔緩衝</b>：<code>-{safety_pct:.1f}%</code>（不傷及共同基金）",
    ]
    if joint_in_stock > 0:
        lines.append(f"⚠️ 共同基金約有 <code>{fmt_nt(joint_in_stock)}</code>（{joint_in_stock / cur_joint * 100:.1f}%）放在股票中")
    else:
        lines.append("✅ 現金部位足以完全涵蓋整筆共同基金")

    lines.append(f'\n🔗 <a href="{WEB_URL}">點此開啟線上圖表儀表板</a>')
    return "\n".join(lines)


def build_joint_analysis_text() -> str:
    summary, balances, _ = read_data()
    cur = summary[-1]
    first = summary[0]
    cur_joint = cur["total"] - cur["after_joint"]
    first_joint = first["total"] - first["after_joint"]
    personal = cur["after_joint"]

    cur_bals = [b for b in balances if b["date"] == cur["date"]]
    stock_sum = sum(b["amount"] for b in cur_bals if b["category"] == "證券")
    cash_sum = sum(b["amount"] for b in cur_bals if b["category"] != "證券")

    joint_in_stock = max(0, cur_joint - cash_sum)
    cash_cov_pct = min(100.0, cash_sum / cur_joint * 100) if cur_joint else 100.0
    stock_exp_pct = (joint_in_stock / cur_joint * 100) if cur_joint else 0.0
    max_drop_pct = (personal / stock_sum * 100) if stock_sum else 100.0

    lines = [
        f"💑 <b>夫妻共同基金深度分析</b>（<code>{cur['date']}</code>）",
        "",
        f"• <b>目前累積總額</b>：<code>{fmt_nt(cur_joint)}</code>",
        f"• <b>每月固定投入</b>：<code>+NT$20,000</code>（Alan 1萬 + Anne 1萬）",
        f"• <b>今年累計增加</b>：<code>{fmt_signed(cur_joint - first_joint)}</code>（自 {first['date']} 起）",
        "",
        "─── 💧 現金覆蓋率 vs 股票曝險 ───",
        f"• <b>全戶現金部位</b>：<code>{fmt_nt(cash_sum)}</code>（可覆蓋共同基金 <b>{cash_cov_pct:.1f}%</b>）",
        f"• <b>共同基金在股票部位</b>：<code>{fmt_nt(joint_in_stock)}</code>（占共同基金 <b>{stock_exp_pct:.1f}%</b>）",
        "",
        "─── 🛡️ 股票波動壓力測試 ───",
        f"目前股票市值 <code>{fmt_nt(stock_sum)}</code>，Alan 可用緩衝墊為 <code>{fmt_nt(personal)}</code>：",
        f"• <b>安全邊際臨界點</b>：股票跌 <b>-{max_drop_pct:.1f}%</b>（<code>-{fmt_nt(personal)}</code>）內，共同基金本金 100% 不受損！",
    ]

    for drop in (10, 20, 30):
        loss = round(stock_sum * drop / 100)
        rem_personal = personal - loss
        if rem_personal >= 0:
            lines.append(f"  ▫ 若股市跌 <b>-{drop}%</b>（-{fmt_nt(loss)}）➔ Alan 可用剩 <code>{fmt_nt(rem_personal)}</code> ✅")
        else:
            lines.append(f"  ▫ 若股市跌 <b>-{drop}%</b>（-{fmt_nt(loss)}）➔ 侵蝕共同基金 <code>{fmt_nt(abs(rem_personal))}</code> ⚠️")

    lines.append("\n─── 🎯 里程碑達標倒數（按月存 2 萬推算）───")
    for target in (500_000, 600_000, 800_000, 1_000_000):
        label = f"{target // 10000} 萬"
        diff = target - cur_joint
        if diff <= 0:
            lines.append(f"• 🏆 <b>{label}</b>：已達成！")
        else:
            months = math.ceil(diff / 20_000)
            lines.append(f"• 🏁 <b>{label}</b>：還差 <code>{fmt_nt(diff)}</code>（預計約 <b>{months} 個月</b>）")

    return "\n".join(lines)


def build_accounts_text() -> str:
    summary, balances, _ = read_data()
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


def build_flows_text(period_idx: int = -1) -> tuple:
    summary, balances, _ = read_data()
    dates = [s["date"] for s in summary]
    if len(dates) < 2:
        return "目前只有一期資料，尚無資金流向比較。", None

    pairs = [(dates[i - 1], dates[i]) for i in range(1, len(dates))]
    if period_idx < 0 or period_idx >= len(pairs):
        period_idx = len(pairs) - 1

    d_from, d_to = pairs[period_idx]
    m_from = {b["account"]: b["amount"] for b in balances if b["date"] == d_from}
    m_to = {b["account"]: b for b in balances if b["date"] == d_to}

    inflows = []
    outflows = []
    for acct, b in m_to.items():
        delta = b["amount"] - m_from.get(acct, 0)
        if delta > 0:
            inflows.append((delta, acct, b["category"], b["amount"]))
        elif delta < 0:
            outflows.append((delta, acct, b["category"], b["amount"]))

    inflows.sort(reverse=True)
    outflows.sort()

    tot_in = sum(x[0] for x in inflows)
    tot_out = sum(x[0] for x in outflows)
    net_delta = tot_in + tot_out

    lines = [
        f"💸 <b>期間資金流向分析</b>（<code>{d_from}</code> ➔ <code>{d_to}</code>）",
        f"📊 期間總資產淨變動：<code>{fmt_signed(net_delta)}</code>（流入 <code>+{fmt_nt(tot_in)}</code>／流出 <code>{fmt_nt(tot_out)}</code>）",
        "",
        "🟢 <b>資金流入（餘額增加）</b>：",
    ]
    if inflows:
        for delta, acct, cat, now_amt in inflows:
            lines.append(f"  • <b>{html.escape(acct)}</b>：<code>{fmt_signed(delta)}</code>（現餘 <code>{fmt_nt(now_amt)}</code>）")
    else:
        lines.append("  • 無")

    lines.append("\n🔴 <b>資金流出（餘額減少）</b>：")
    if outflows:
        for delta, acct, cat, now_amt in outflows:
            lines.append(f"  • <b>{html.escape(acct)}</b>：<code>{fmt_signed(delta)}</code>（現餘 <code>{fmt_nt(now_amt)}</code>）")
    else:
        lines.append("  • 無")

    # 建立期間切換按鈕
    btn_rows = []
    row = []
    for idx, (p1, p2) in enumerate(pairs):
        label = f"{p1[5:].replace('-', '/')}→{p2[5:].replace('-', '/')}"
        if idx == period_idx:
            label = "✅ " + label
        row.append({"text": label, "callback_data": f"flow:{idx}"})
        if len(row) == 3:
            btn_rows.append(row)
            row = []
    if row:
        btn_rows.append(row)

    return "\n".join(lines), {"inline_keyboard": btn_rows}


def build_stats_text() -> str:
    summary, balances, _ = read_data()
    first = summary[0]
    cur = summary[-1]

    d1 = [int(x) for x in first["date"].split("-")]
    d2 = [int(x) for x in cur["date"].split("-")]
    months = max(1, (d2[0] - d1[0]) * 12 + (d2[1] - d1[1]))

    first_joint = first["total"] - first["after_joint"]
    cur_joint = cur["total"] - cur["after_joint"]

    g_total = cur["total"] - first["total"]
    g_joint = cur_joint - first_joint
    g_personal = cur["after_joint"] - first["after_joint"]

    max_tot = max(summary, key=lambda r: r["total"])
    min_tot = min(summary, key=lambda r: r["total"])
    max_per = max(summary, key=lambda r: r["after_joint"])
    min_per = min(summary, key=lambda r: r["after_joint"])

    # 各類別與集中度
    cur_bals = [b for b in balances if b["date"] == cur["date"]]
    cur_bals.sort(key=lambda x: x["amount"], reverse=True)
    top3_sum = sum(b["amount"] for b in cur_bals[:3])
    top3_names = "＋".join(b["account"] for b in cur_bals[:3])

    bank_sum = sum(b["amount"] for b in cur_bals if b["category"] == "銀行存款")
    stock_sum = sum(b["amount"] for b in cur_bals if b["category"] == "證券")
    epay_sum = sum(b["amount"] for b in cur_bals if b["category"] == "電子支付")
    active_count = sum(1 for b in cur_bals if b["amount"] > 0)

    lines = [
        f"📐 <b>家庭財務健康度與統計指標</b>",
        f"🗓 統計區間：<code>{first['date']}</code> ～ <code>{cur['date']}</code>（{months} 個月、{len(summary)} 次快照）",
        "",
        "─── 🚀 累計成長與月均儲蓄 ───",
        f"• <b>總資產成長</b>：<code>{fmt_signed(g_total)}</code>（<b>{g_total / first['total'] * 100:+.1f}%</b>｜月均 <code>{fmt_signed(round(g_total / months))}</code>）",
        f"• <b>共同基金成長</b>：<code>{fmt_signed(g_joint)}</code>（<b>{g_joint / first_joint * 100:+.1f}%</b>｜月均 <code>{fmt_signed(round(g_joint / months))}</code>）",
        f"• <b>Alan 可用成長</b>：<code>{fmt_signed(g_personal)}</code>（<b>{g_personal / first['after_joint'] * 100:+.1f}%</b>｜月均 <code>{fmt_signed(round(g_personal / months))}</code>）",
        "",
        "─── 🏔 歷史水位極值紀錄 ───",
        f"• <b>總資產最高</b>：<code>{fmt_nt(max_tot['total'])}</code>（{max_tot['date']}）",
        f"• <b>總資產最低</b>：<code>{fmt_nt(min_tot['total'])}</code>（{min_tot['date']}）",
        f"• <b>Alan 可用最高</b>：<code>{fmt_nt(max_per['after_joint'])}</code>（{max_per['date']}）",
        f"• <b>Alan 可用最低</b>：<code>{fmt_nt(min_per['after_joint'])}</code>（{min_per['date']}）",
        "",
        "─── 🥧 資產結構與集中度 ───",
        f"• <b>銀行存款</b>：<code>{fmt_nt(bank_sum)}</code>（{bank_sum / cur['total'] * 100:.1f}%）",
        f"• <b>證券投資</b>：<code>{fmt_nt(stock_sum)}</code>（{stock_sum / cur['total'] * 100:.1f}%）",
        f"• <b>電子支付</b>：<code>{fmt_nt(epay_sum)}</code>（{epay_sum / cur['total'] * 100:.1f}%）",
        f"• <b>前三大集中度（{top3_names}）</b>：<code>{fmt_nt(top3_sum)}</code>（占 <b>{top3_sum / cur['total'] * 100:.1f}%</b>）",
        f"• <b>有餘額帳戶數</b>：{active_count} / {len(cur_bals)} 個",
    ]
    return "\n".join(lines)


def build_account_selector_keyboard() -> dict:
    _, balances, _ = read_data()
    accts = []
    seen = set()
    for b in balances:
        a = b["account"]
        if a not in seen:
            seen.add(a)
            accts.append(a)

    # 依最新餘額排序，過濾掉永遠為 0 的帳戶
    latest_date = max(b["date"] for b in balances)
    latest_map = {b["account"]: b["amount"] for b in balances if b["date"] == latest_date}
    ever_nonzero = {b["account"] for b in balances if b["amount"] > 0}
    accts = [a for a in accts if a in ever_nonzero]
    accts.sort(key=lambda a: latest_map.get(a, 0), reverse=True)

    rows = []
    row = []
    for a in accts:
        row.append({"text": f"{a} ({latest_map.get(a, 0) // 1000}k)" if latest_map.get(a, 0) >= 1000 else a, "callback_data": f"acct:{a}"})
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return {"inline_keyboard": rows}


def build_single_account_text(acct_name: str) -> str:
    _, balances, _ = read_data()
    rows = [b for b in balances if b["account"] == acct_name]
    if not rows:
        return f"找不到帳戶「{html.escape(acct_name)}」的紀錄。"

    cat = rows[0]["category"]
    amts = [r["amount"] for r in rows]
    cur_amt = amts[-1]
    first_amt = amts[0]
    max_r = max(rows, key=lambda r: r["amount"])
    min_r = min(rows, key=lambda r: r["amount"])
    avg_amt = round(sum(amts) / len(amts))

    lines = [
        f"🔍 <b>單一帳戶歷月追蹤：{html.escape(acct_name)}</b>（{cat}）",
        "",
        f"• <b>最新餘額</b>：<code>{fmt_nt(cur_amt)}</code>（累計變化 <code>{fmt_signed(cur_amt - first_amt)}</code>）",
        f"• <b>期間平均</b>：<code>{fmt_nt(avg_amt)}</code>",
        f"• <b>最高水位</b>：<code>{fmt_nt(max_r['amount'])}</code>（{max_r['date']}）",
        f"• <b>最低水位</b>：<code>{fmt_nt(min_r['amount'])}</code>（{min_r['date']}）",
        "",
        "─── 🗓 歷次快照紀錄 ───",
    ]
    for i, r in enumerate(rows):
        delta_str = ""
        if i > 0:
            d = r["amount"] - rows[i - 1]["amount"]
            delta_str = f"（{fmt_signed(d)}）" if d != 0 else "（—）"
        lines.append(f"• <code>{r['date']}</code>：<code>{fmt_nt(r['amount'])}</code> {delta_str}")

    return "\n".join(lines)


def build_ledger_text() -> str:
    _, _, ledger = read_data()
    if not ledger:
        return "目前尚無共同基金帳本資料，請點選「🔄 同步試算表」抓取。"

    recent = ledger[-20:]
    lines = [
        "📒 <b>共同基金收支帳本（夢想撲滿近期紀錄）</b>",
        "（取自 Google 試算表「財務」分頁左半部）\n",
    ]
    for r in recent:
        parts = []
        if r["alan"]:
            parts.append(f"Alan {fmt_signed(r['alan'])}")
        if r["anne"]:
            parts.append(f"Anne {fmt_signed(r['anne'])}")
        if r["expense"]:
            parts.append(f"共同 {fmt_signed(r['expense'])}")
        change_str = " / ".join(parts) if parts else "—"
        note_str = f"（{html.escape(r['note'])}）" if r["note"] else ""
        lines.append(
            f"• <b>{html.escape(r['item'])}</b>：{change_str} ➔ 餘額 <code>{fmt_nt(r['balance'])}</code>{note_str}"
        )

    return "\n".join(lines)


def build_history_text() -> str:
    summary, _, _ = read_data()
    lines = ["📈 <b>歷月資產與共同基金趨勢</b>\n"]
    for i, r in enumerate(summary):
        joint = r["total"] - r["after_joint"]
        d_tot = f" ({fmt_signed(r['total'] - summary[i-1]['total'])})" if i > 0 else ""
        lines.append(
            f"• <code>{r['date']}</code>｜總 <code>{fmt_nt(r['total'])}</code>{d_tot}\n"
            f"  └ 共同基金 <code>{fmt_nt(joint)}</code>｜Alan 可用 <code>{fmt_nt(r['after_joint'])}</code>"
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
        print(f"sendMessage error: {e}", file=sys.stderr, flush=True)


def edit_msg(token: str, chat_id: int, message_id: int, text: str, reply_markup: dict = None):
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        tg_api(token, "editMessageText", payload)
    except Exception as e:
        print(f"editMessageText error: {e}", file=sys.stderr, flush=True)


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
                {"command": "summary", "description": "📊 最新家庭資產與共同基金總覽"},
                {"command": "joint", "description": "💑 共同基金深度分析（壓力測試＋達標倒數）"},
                {"command": "accounts", "description": "🏦 各銀行與證券帳戶最新餘額"},
                {"command": "flows", "description": "💸 各期間資金流向（流入/流出排行）"},
                {"command": "stats", "description": "📐 財務健康度、累計成長率與極值"},
                {"command": "acct", "description": "🔍 查詢單一帳戶歷月水位變化"},
                {"command": "ledger", "description": "📒 共同基金收支帳本（夢想撲滿近期紀錄）"},
                {"command": "history", "description": "📈 歷月共同基金與資產成長趨勢"},
                {"command": "sync", "description": "🔄 從 Google 試算表同步最新資料並更新網站"},
                {"command": "web", "description": "🌐 開啟 GitHub Pages 視覺化圖表儀表板"},
            ]
        })
    except Exception as e:
        print(f"Warning: setMyCommands failed: {e}", file=sys.stderr)

    print("🤖 家庭資產 Telegram Bot (@ALANANNE_FAMILY_bot) 已啟動（進階指標版）！", flush=True)
    offset = 0

    while True:
        try:
            res = tg_api(token, "getUpdates", {"offset": offset, "timeout": 25}, timeout=35)
            for upd in res.get("result", []):
                offset = upd["update_id"] + 1

                # 處理 Inline 按鈕點擊 (CallbackQuery)
                cb = upd.get("callback_query")
                if cb:
                    cb_id = cb["id"]
                    chat_id = cb["message"]["chat"]["id"]
                    msg_id = cb["message"]["message_id"]
                    user_id = cb.get("from", {}).get("id", 0)
                    data_str = cb.get("data", "")
                    try:
                        tg_api(token, "answerCallbackQuery", {"callback_query_id": cb_id})
                    except Exception:
                        pass
                    if allowed_users and user_id not in allowed_users:
                        continue
                    if data_str.startswith("flow:"):
                        idx = int(data_str.split(":", 1)[1])
                        txt, kb = build_flows_text(idx)
                        edit_msg(token, chat_id, msg_id, txt, kb)
                    elif data_str.startswith("acct:"):
                        acct_name = data_str.split(":", 1)[1]
                        txt = build_single_account_text(acct_name)
                        edit_msg(token, chat_id, msg_id, txt, build_account_selector_keyboard())
                    continue

                msg = upd.get("message") or upd.get("edited_message")
                if not msg:
                    continue
                chat_id = msg["chat"]["id"]
                user_id = msg.get("from", {}).get("id", 0)
                text = (msg.get("text") or "").strip()
                if not text:
                    continue

                # 權限檢查：若尚未在白名單，輸入通關密碼即可永久解鎖
                if allowed_users and user_id not in allowed_users:
                    if text in valid_passcodes:
                        save_authorized_user(user_id, allowed_users)
                        send_msg(
                            token,
                            chat_id,
                            "🎉 <b>驗證成功！歡迎使用家庭資產小幫手</b>\n下方按鈕可隨時查詢最新共同基金與各項進階指標：",
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
                elif text in ("/joint", "💑 共同基金分析"):
                    send_msg(token, chat_id, build_joint_analysis_text(), main_keyboard())
                elif text in ("/accounts", "🏦 帳戶明細"):
                    send_msg(token, chat_id, build_accounts_text(), main_keyboard())
                elif text in ("/flows", "💸 本期資金流向"):
                    txt, kb = build_flows_text(-1)
                    send_msg(token, chat_id, txt, kb)
                elif text in ("/stats", "📐 財務健康指標"):
                    send_msg(token, chat_id, build_stats_text(), main_keyboard())
                elif text in ("/acct", "🔍 單一帳戶查詢"):
                    send_msg(
                        token,
                        chat_id,
                        "🔍 <b>請點選下方按鈕選擇要查看歷月水位的帳戶</b>（括號為最新餘額）：",
                        build_account_selector_keyboard(),
                    )
                elif text.startswith("/acct "):
                    acct_q = text.split(" ", 1)[1].strip()
                    send_msg(token, chat_id, build_single_account_text(acct_q), build_account_selector_keyboard())
                elif text in ("/ledger", "📒 共同基金帳本"):
                    send_msg(token, chat_id, build_ledger_text(), main_keyboard())
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
                        "請點選下方 10 宮格選單或使用指令：\n"
                        "• <code>/summary</code> 資產總覽\n"
                        "• <code>/joint</code> 共同基金深度分析\n"
                        "• <code>/accounts</code> 帳戶明細\n"
                        "• <code>/flows</code> 期間資金流向\n"
                        "• <code>/stats</code> 財務健康指標\n"
                        "• <code>/acct</code> 單一帳戶查詢\n"
                        "• <code>/ledger</code> 共同基金帳本\n"
                        "• <code>/history</code> 歷月趨勢\n"
                        "• <code>/sync</code> 同步試算表\n"
                        "• <code>/web</code> 線上儀表板",
                        main_keyboard(),
                    )
        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"Polling error: {e}", file=sys.stderr, flush=True)
            time.sleep(5)


if __name__ == "__main__":
    main()
