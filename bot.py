#!/usr/bin/env python3
"""
家庭資產 ＆ 英冰旅途快記 Telegram Bot (@ALANANNE_FAMILY_bot)
支援功能：
  • ✈️ 直接輸入「品項 金額幣別 [卡別] [共同/個人]」5 秒快記（如：龍蝦湯 4500isk Al星展）
  • 💳 刷卡額度雷達 (/cards) — 追蹤星展 eco 1.5萬、永豐幣倍 2萬、傳說對決 1.25萬、Chase 免手續費
  • ✈️ 旅途分帳與明細 (/trip) — 自動算 1.5% 海外手續費、共同 vs 個人、誰墊多少錢
  • 📊 資產總覽 (/start, /summary)
  • 💑 共同基金分析 (/joint)
  • 🏦 帳戶明細 (/accounts)
  • 💸 本期資金流向 (/flows)
  • 📐 財務健康指標 (/stats)
  • 🔍 單一帳戶查詢 (/acct)
  • 📒 共同基金帳本 (/ledger)
  • 📈 歷月趨勢 (/history)
  • 🔄 同步試算表＋更新網站 (/sync)
  • 🌐 線上儀表板連結 (/web)
"""

import base64
import csv
import datetime
import html
import json
import math
import os
import re
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
TRAVEL_CSV = BASE_DIR / "travel_expenses.csv"
TRAVEL_CFG = BASE_DIR / "travel_config.json"
AUTH_FILE = BASE_DIR / ".authorized_users.json"
PID_FILE = BASE_DIR / ".bot.pid"
WEB_URL = "https://alantin918.github.io/family-assets/"

# 預設匯率（依據歐洲旅行計劃書 web-v6 設定）
DEFAULT_RATES = {
    "GBP": 43.43,
    "ISK": 0.2632,
    "USD": 31.83,
    "EUR": 34.50,
    "JPY": 0.215,
    "TWD": 1.0,
}

# 行前已付未結代墊差額（依據 HANDOFF.md：Anne 需給 Alan 36,488 元）
PRE_TRIP_ANNE_OWES_ALAN = 36488

# 旅途信用卡設定：(持卡人, 顯示名稱, 回饋說明, 封頂上限TWD(0表無上限), 海外手續費率)
CARDS_INFO = {
    "Al_星展eco": {
        "owner": "Alan",
        "label": "👨 Al星展eco",
        "desc": "歐洲實體 5%",
        "limit": 15000,
        "fee_rate": 0.015,
    },
    "Al_永豐幣倍": {
        "owner": "Alan",
        "label": "👨 Al永豐幣倍",
        "desc": "國外實體 6%",
        "limit": 20000,
        "fee_rate": 0.015,
    },
    "Al_星展傳說": {
        "owner": "Alan",
        "label": "👨 Al星展傳說",
        "desc": "海外 5%",
        "limit": 12500,
        "fee_rate": 0.015,
    },
    "Al_聯邦賴點": {
        "owner": "Alan",
        "label": "👨 Al聯邦賴點",
        "desc": "外幣 3% 無上限",
        "limit": 0,
        "fee_rate": 0.015,
    },
    "Al_其他卡": {
        "owner": "Alan",
        "label": "👨 Al台新/玉山",
        "desc": "海外 3.3~4.5%",
        "limit": 0,
        "fee_rate": 0.015,
    },
    "An_星展eco": {
        "owner": "Anne",
        "label": "👩 An星展eco",
        "desc": "歐洲實體 5%",
        "limit": 15000,
        "fee_rate": 0.015,
    },
    "An_Chase": {
        "owner": "Anne",
        "label": "👩 An Chase",
        "desc": "美國卡免手續費/餐廳3x",
        "limit": 0,
        "fee_rate": 0.0,
    },
    "Cash_Al": {
        "owner": "Alan",
        "label": "💵 Al現金",
        "desc": "免手續費",
        "limit": 0,
        "fee_rate": 0.0,
    },
    "Cash_An": {
        "owner": "Anne",
        "label": "💵 An現金",
        "desc": "免手續費",
        "limit": 0,
        "fee_rate": 0.0,
    },
}


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


def load_rates() -> dict:
    rates = dict(DEFAULT_RATES)
    if TRAVEL_CFG.exists():
        try:
            cfg = json.loads(TRAVEL_CFG.read_text(encoding="utf-8"))
            rates.update(cfg.get("rates", {}))
        except Exception:
            pass
    return rates


def save_rate(cur_code: str, val: float):
    cfg = {}
    if TRAVEL_CFG.exists():
        try:
            cfg = json.loads(TRAVEL_CFG.read_text(encoding="utf-8"))
        except Exception:
            pass
    rates = load_rates()
    rates[cur_code.upper()] = val
    cfg["rates"] = rates
    TRAVEL_CFG.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def get_now_str() -> str:
    tz_mode = "TW"
    if TRAVEL_CFG.exists():
        try:
            cfg = json.loads(TRAVEL_CFG.read_text(encoding="utf-8"))
            tz_mode = cfg.get("tz", "TW")
        except Exception:
            pass
    utc_now = datetime.datetime.now(datetime.timezone.utc)
    if tz_mode.upper() in ("UK", "IS", "UTC", "GMT"):
        local_dt = utc_now
        return local_dt.strftime("%Y-%m-%d %H:%M") + " (英冰)"
    else:
        local_dt = utc_now + datetime.timedelta(hours=8)
        return local_dt.strftime("%Y-%m-%d %H:%M")


def save_tz(tz_mode: str):
    cfg = {}
    if TRAVEL_CFG.exists():
        try:
            cfg = json.loads(TRAVEL_CFG.read_text(encoding="utf-8"))
        except Exception:
            pass
    cfg["tz"] = tz_mode.upper()
    TRAVEL_CFG.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


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
            [{"text": "✈️ 旅途記帳＆結算"}, {"text": "💳 刷卡額度雷達"}],
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


def progress_bar(used: int, limit: int, width: int = 10) -> str:
    if limit <= 0:
        return "∞ 無上限"
    ratio = min(1.0, max(0.0, used / limit))
    filled = round(ratio * width)
    bar = "█" * filled + "░" * (width - filled)
    return f"{bar} {used / limit * 100:.0f}%"


# ─── 旅途記帳讀寫與解析 ───

TRAVEL_FIELDS = [
    "id", "date", "item", "orig_amount", "currency",
    "base_twd", "fee_twd", "total_twd", "card_key", "payer", "split"
]


def read_travel_expenses() -> list:
    if not TRAVEL_CSV.exists():
        return []
    with open(TRAVEL_CSV, encoding="utf-8") as f:
        rows = []
        for r in csv.DictReader(f):
            rows.append({
                "id": int(r["id"]),
                "date": r["date"],
                "item": r["item"],
                "orig_amount": float(r["orig_amount"]),
                "currency": r["currency"],
                "base_twd": int(r["base_twd"]),
                "fee_twd": int(r["fee_twd"]),
                "total_twd": int(r["total_twd"]),
                "card_key": r["card_key"],
                "payer": r["payer"],
                "split": r["split"],  # 'joint', 'alan', 'anne'
            })
        return rows


def write_travel_expenses(rows: list):
    with open(TRAVEL_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=TRAVEL_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    # 背景非阻塞更新本機 data.enc.json，讓 Telegram 按鈕秒回
    try:
        subprocess.Popen(
            [sys.executable, str(BASE_DIR / "sync.py"), "--encrypt-only"],
            cwd=BASE_DIR,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass


def calc_twd_and_fee(orig_amount: float, currency: str, card_key: str) -> tuple:
    rates = load_rates()
    rate = rates.get(currency.upper(), 1.0)
    base_twd = round(orig_amount * rate)
    card = CARDS_INFO.get(card_key, CARDS_INFO["Al_星展eco"])
    # 台幣消費或免手續費卡不收 1.5%
    fee_rate = 0.0 if currency.upper() == "TWD" else card["fee_rate"]
    fee_twd = round(base_twd * fee_rate)
    return base_twd, fee_twd, base_twd + fee_twd, card["owner"]


def parse_quick_expense(text: str) -> dict:
    """
    解析自然語言記帳：
      例如：
        「龍蝦湯 4500isk Al星展」
        「外套 85gbp An個人 Chase」
        「咖啡 £4.5」
        「記 停車 1200isk」
    回傳 dict 或 None（若非記帳訊息）
    """
    s = text.strip()
    if s.startswith("/"):
        if s.startswith("/add "):
            s = s[5:].strip()
        else:
            return None

    if s.startswith("記 ") or s.startswith("記帳 "):
        s = re.sub(r"^記帳?\s+", "", s)

    # 匹配金額與幣別：支援 £45, $20, 4500isk, 85.5gbp, 1200克朗, 50英鎊, 300台幣
    cur_pattern = re.compile(
        r"(?:(?P<sym>[£$€¥])\s*(?P<num1>\d+(?:\.\d+)?))|"
        r"(?:(?P<num2>\d+(?:\.\d+)?)\s*(?P<unit>isk|kr|克朗|冰島幣|gbp|英鎊|鎊|usd|美金|美元|eur|歐元|jpy|日圓|日幣|twd|ntd|nt|台幣|元)\b)",
        re.IGNORECASE,
    )
    m = cur_pattern.search(s)
    if not m:
        return None

    if m.group("sym"):
        sym = m.group("sym")
        orig_amount = float(m.group("num1"))
        sym_map = {"£": "GBP", "$": "USD", "€": "EUR", "¥": "JPY"}
        currency = sym_map.get(sym, "USD")
    else:
        orig_amount = float(m.group("num2"))
        u = m.group("unit").lower()
        if u in ("isk", "kr", "克朗", "冰島幣"):
            currency = "ISK"
        elif u in ("gbp", "英鎊", "鎊"):
            currency = "GBP"
        elif u in ("usd", "美金", "美元"):
            currency = "USD"
        elif u in ("eur", "歐元"):
            currency = "EUR"
        elif u in ("jpy", "日圓", "日幣"):
            currency = "JPY"
        else:
            currency = "TWD"

    # 移除金額與幣別字串
    rem = (s[:m.start()] + " " + s[m.end():]).strip()

    # 識別卡片關鍵字
    card_key = "Al_星展eco"
    card_patterns = [
        (r"\b(chase|美國卡|an\s*chase)\b", "An_Chase"),
        (r"(an|anne|安|老婆|太太)\s*(星展|eco)", "An_星展eco"),
        (r"(an|anne|安|老婆|太太)\s*現金", "Cash_An"),
        (r"(al|alan|倫)?\s*現金", "Cash_Al"),
        (r"(al|alan|倫)?\s*(永豐|幣倍|大戶|dawho)", "Al_永豐幣倍"),
        (r"(al|alan|倫)?\s*傳說", "Al_星展傳說"),
        (r"(al|alan|倫)?\s*(聯邦|賴點)", "Al_聯邦賴點"),
        (r"(al|alan|倫)?\s*(台新|richart|玉山|unicard|cube|國泰)", "Al_其他卡"),
        (r"(al|alan|倫)?\s*(星展|eco)", "Al_星展eco"),
    ]
    for pat, ckey in card_patterns:
        cm = re.search(pat, rem, re.IGNORECASE)
        if cm:
            card_key = ckey
            rem = (rem[:cm.start()] + " " + rem[cm.end():]).strip()
            break

    # 識別分攤方式 (joint / alan / anne)
    split_type = "joint"
    split_patterns = [
        (r"(an|anne|安|老婆|太太)\s*(個人|自己|自付|獨享)", "anne"),
        (r"(al|alan|倫)\s*(個人|自己|自付|獨享)", "alan"),
        (r"\b(個人|自己|自付)\b", "owner"),
        (r"\b(共同|平分|公費|一起)\b", "joint"),
    ]
    for pat, stype in split_patterns:
        sm = re.search(pat, rem, re.IGNORECASE)
        if sm:
            if stype == "owner":
                owner = CARDS_INFO[card_key]["owner"]
                split_type = "anne" if owner == "Anne" else "alan"
            else:
                split_type = stype
            rem = (rem[:sm.start()] + " " + rem[sm.end():]).strip()
            break

    item_name = re.sub(r"\s+", " ", rem).strip() or "旅途消費"
    base_twd, fee_twd, total_twd, payer = calc_twd_and_fee(orig_amount, currency, card_key)

    return {
        "date": get_now_str(),
        "item": item_name,
        "orig_amount": orig_amount,
        "currency": currency,
        "base_twd": base_twd,
        "fee_twd": fee_twd,
        "total_twd": total_twd,
        "card_key": card_key,
        "payer": payer,
        "split": split_type,
    }


def extract_card_and_split(hint_text: str) -> tuple:
    rem = hint_text.strip()
    card_key = "Al_星展eco"
    card_patterns = [
        (r"\b(chase|美國卡|an\s*chase)\b", "An_Chase"),
        (r"(an|anne|安|老婆|太太)\s*(星展|eco)", "An_星展eco"),
        (r"(an|anne|安|老婆|太太)\s*現金", "Cash_An"),
        (r"(al|alan|倫)?\s*現金", "Cash_Al"),
        (r"(al|alan|倫)?\s*(永豐|幣倍|大戶|dawho)", "Al_永豐幣倍"),
        (r"(al|alan|倫)?\s*傳說", "Al_星展傳說"),
        (r"(al|alan|倫)?\s*(聯邦|賴點)", "Al_聯邦賴點"),
        (r"(al|alan|倫)?\s*(台新|richart|玉山|unicard|cube|國泰)", "Al_其他卡"),
        (r"(al|alan|倫)?\s*(星展|eco)", "Al_星展eco"),
    ]
    for pat, ckey in card_patterns:
        cm = re.search(pat, rem, re.IGNORECASE)
        if cm:
            card_key = ckey
            rem = (rem[:cm.start()] + " " + rem[cm.end():]).strip()
            break

    split_type = "joint"
    split_patterns = [
        (r"(an|anne|安|老婆|太太)\s*(個人|自己|自付|獨享)", "anne"),
        (r"(al|alan|倫)\s*(個人|自己|自付|獨享)", "alan"),
        (r"\b(個人|自己|自付)\b", "owner"),
        (r"\b(共同|平分|公費|一起)\b", "joint"),
    ]
    for pat, stype in split_patterns:
        sm = re.search(pat, rem, re.IGNORECASE)
        if sm:
            if stype == "owner":
                owner = CARDS_INFO[card_key]["owner"]
                split_type = "anne" if owner == "Anne" else "alan"
            else:
                split_type = stype
            rem = (rem[:sm.start()] + " " + rem[sm.end():]).strip()
            break
    return card_key, split_type, re.sub(r"\s+", " ", rem).strip()


def ocr_receipt_photo(token: str, gemini_key: str, file_id: str, caption: str = "") -> dict:
    f_info = tg_api(token, "getFile", {"file_id": file_id})
    file_path = f_info["result"]["file_path"]
    dl_url = f"https://api.telegram.org/file/bot{token}/{file_path}"
    with urllib.request.urlopen(dl_url, timeout=25) as resp:
        img_bytes = resp.read()

    b64_img = base64.b64encode(img_bytes).decode("ascii")
    prompt = (
        "請辨識這張旅遊收據、發票、菜單或刷卡通知截圖，並只回傳純 JSON 格式（不要加 markdown 區塊）：\n"
        '{"item": "繁體中文店家或品項簡稱(例如：Bónus超市採買、龍蝦湯、倫敦地鐵)", "amount": 數字總額, "currency": "ISK或GBP或USD或EUR或JPY或TWD"}\n'
        "注意：若看到 kr / ISK 為冰島克朗 ISK；看到 £ / GBP 為英鎊 GBP；看到 NT$ / TWD 為 TWD。請抓最終總計金額 (Total)。"
    )

    api_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={gemini_key}"
    payload = {
        "contents": [{
            "parts": [
                {"text": prompt},
                {"inline_data": {"mime_type": "image/jpeg", "data": b64_img}},
            ]
        }]
    }
    req = urllib.request.Request(api_url, data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=25) as resp:
        res = json.loads(resp.read().decode("utf-8"))

    raw_txt = res["candidates"][0]["content"]["parts"][0]["text"].strip()
    raw_txt = re.sub(r"^```(?:json)?|```$", "", raw_txt, flags=re.MULTILINE).strip()
    parsed_json = json.loads(raw_txt)

    orig_amount = float(parsed_json.get("amount") or 0)
    currency = str(parsed_json.get("currency") or "GBP").upper()
    if currency not in DEFAULT_RATES:
        currency = "GBP"

    card_key, split_type, extra_note = extract_card_and_split(caption)
    ai_item = str(parsed_json.get("item") or "拍照收據").strip()
    item_name = f"{extra_note} ({ai_item})" if extra_note else f"📸 {ai_item}"

    base_twd, fee_twd, total_twd, payer = calc_twd_and_fee(orig_amount, currency, card_key)

    return {
        "date": get_now_str(),
        "item": item_name,
        "orig_amount": orig_amount,
        "currency": currency,
        "base_twd": base_twd,
        "fee_twd": fee_twd,
        "total_twd": total_twd,
        "card_key": card_key,
        "payer": payer,
        "split": split_type,
        "_img_bytes": img_bytes,
    }


def expense_action_keyboard(exp_id: int, cur_card: str, cur_split: str) -> dict:
    def c_lbl(k, short):
        return f"✅{short}" if cur_card == k else short

    def s_lbl(k, short):
        return f"✅{short}" if cur_split == k else short

    return {
        "inline_keyboard": [
            [
                {"text": c_lbl("Al_星展eco", "👨Al星展"), "callback_data": f"tcard:{exp_id}:Al_星展eco"},
                {"text": c_lbl("Al_永豐幣倍", "👨Al永豐"), "callback_data": f"tcard:{exp_id}:Al_永豐幣倍"},
                {"text": c_lbl("Al_星展傳說", "👨Al傳說"), "callback_data": f"tcard:{exp_id}:Al_星展傳說"},
                {"text": c_lbl("Al_聯邦賴點", "👨Al賴點"), "callback_data": f"tcard:{exp_id}:Al_聯邦賴點"},
            ],
            [
                {"text": c_lbl("An_星展eco", "👩An星展"), "callback_data": f"tcard:{exp_id}:An_星展eco"},
                {"text": c_lbl("An_Chase", "👩Chase(0%)"), "callback_data": f"tcard:{exp_id}:An_Chase"},
                {"text": c_lbl("Cash_Al", "💵Al現金"), "callback_data": f"tcard:{exp_id}:Cash_Al"},
                {"text": c_lbl("Cash_An", "💵An現金"), "callback_data": f"tcard:{exp_id}:Cash_An"},
            ],
            [
                {"text": s_lbl("joint", "💑共同平分"), "callback_data": f"tsplit:{exp_id}:joint"},
                {"text": s_lbl("alan", "👨Alan個人"), "callback_data": f"tsplit:{exp_id}:alan"},
                {"text": s_lbl("anne", "👩Anne個人"), "callback_data": f"tsplit:{exp_id}:anne"},
                {"text": "🗑撤銷", "callback_data": f"tdel:{exp_id}"},
            ],
        ]
    }


def calc_trip_settlement(rows: list) -> dict:
    alan_paid = 0
    anne_paid = 0
    alan_should = 0.0
    anne_should = 0.0
    joint_total = 0
    alan_personal = 0
    anne_personal = 0
    card_used = {k: 0 for k in CARDS_INFO}

    for r in rows:
        tot = r["total_twd"]
        base = r["base_twd"]
        ck = r["card_key"]
        if ck in card_used:
            # 信用卡回饋上限以未含手續費之消費本金 base_twd 計算
            card_used[ck] += base

        if r["payer"] == "Alan":
            alan_paid += tot
        else:
            anne_paid += tot

        if r["split"] == "joint":
            joint_total += tot
            alan_should += tot / 2.0
            anne_should += tot / 2.0
        elif r["split"] == "alan":
            alan_personal += tot
            alan_should += tot
        else:
            anne_personal += tot
            anne_should += tot

    # 正數代表 Anne 需給 Alan，負數代表 Alan 需給 Anne
    trip_anne_owes_alan = round(alan_paid - alan_should)
    combined_anne_owes_alan = trip_anne_owes_alan + PRE_TRIP_ANNE_OWES_ALAN

    return {
        "count": len(rows),
        "total_spend": alan_paid + anne_paid,
        "joint_total": joint_total,
        "alan_personal": alan_personal,
        "anne_personal": anne_personal,
        "alan_paid": alan_paid,
        "anne_paid": anne_paid,
        "alan_should": round(alan_should),
        "anne_should": round(anne_should),
        "trip_net": trip_anne_owes_alan,
        "combined_net": combined_anne_owes_alan,
        "card_used": card_used,
    }


def format_expense_receipt(exp: dict, rows: list) -> str:
    stats = calc_trip_settlement(rows)
    ck = exp["card_key"]
    cinfo = CARDS_INFO.get(ck, CARDS_INFO["Al_星展eco"])
    used = stats["card_used"].get(ck, 0)
    limit = cinfo["limit"]

    split_names = {"joint": "💑 共同平分", "alan": "👨 Alan 個人", "anne": "👩 Anne 個人"}
    orig_str = f"{exp['orig_amount']:g} {exp['currency']}"
    fee_str = f"（含 1.5% 手續費 <code>NT${exp['fee_twd']:,}</code>）" if exp["fee_twd"] > 0 else "（免手續費 ✨）"

    lines = [
        f"✅ <b>已記帳 #{exp['id']}：{html.escape(exp['item'])}</b>",
        f"• <b>時間</b>：<code>{exp['date']}</code>",
        f"• <b>金額</b>：<code>{orig_str}</code> ➔ <b><code>{fmt_nt(exp['total_twd'])}</code></b> {fee_str}",
        f"• <b>付款與歸屬</b>：{cinfo['label']} ｜ {split_names.get(exp['split'], '共同')}",
    ]

    if limit > 0:
        rem = max(0, limit - used)
        warn = " ⚠️ <b>快滿了！</b>" if rem < 2500 else ""
        lines.append(
            f"• <b>此卡額度 ({cinfo['desc']})</b>：已刷 <code>{fmt_nt(used)}</code> / <code>{fmt_nt(limit)}</code>（剩 <b><code>{fmt_nt(rem)}</code></b>）{warn}"
        )

    net = stats["trip_net"]
    if net > 0:
        settle_str = f"Anne 需給 Alan <code>{fmt_nt(net)}</code>"
    elif net < 0:
        settle_str = f"Alan 需給 Anne <code>{fmt_nt(abs(net))}</code>"
    else:
        settle_str = "雙方剛好兩清 NT$0"

    lines.append(f"• <b>旅途現場代墊累計</b>：{settle_str}")
    lines.append("\n💡 <i>點選下方按鈕可一鍵切換「刷哪張卡」或「共同/個人」：</i>")
    return "\n".join(lines)


def trip_summary_inline_kb() -> dict:
    rows = read_travel_expenses()
    if not rows:
        return None
    return {
        "inline_keyboard": [
            [{"text": "🧹 清空測試資料 / 重置旅途帳本", "callback_data": "tclear:ask"}]
        ]
    }


def build_trip_summary_text() -> str:
    rows = read_travel_expenses()
    stats = calc_trip_settlement(rows)
    rates = load_rates()

    net = stats["trip_net"]
    if net > 0:
        trip_settle = f"👉 <b>Anne 需給 Alan <code>{fmt_nt(net)}</code></b>"
    elif net < 0:
        trip_settle = f"👉 <b>Alan 需給 Anne <code>{fmt_nt(abs(net))}</code></b>"
    else:
        trip_settle = "👉 <b>雙方兩清 NT$0</b>"

    c_net = stats["combined_net"]
    if c_net > 0:
        comb_settle = f"Anne 需給 Alan <code>{fmt_nt(c_net)}</code>"
    elif c_net < 0:
        comb_settle = f"Alan 需給 Anne <code>{fmt_nt(abs(c_net))}</code>"
    else:
        comb_settle = "雙方兩清 NT$0"

    lines = [
        "✈️ <b>英國＋冰島旅途記帳＆分帳結算</b>",
        f"💱 當前匯率：<code>GBP {rates['GBP']}</code>｜<code>ISK {rates['ISK']}</code>｜<code>USD {rates['USD']}</code>",
        "",
        "─── 💰 旅途現場花費統計 ───",
        f"• <b>旅途現場總支出</b>：<code>{fmt_nt(stats['total_spend'])}</code>（共 {stats['count']} 筆）",
        f"  ├ 💑 共同花費合計：<code>{fmt_nt(stats['joint_total'])}</code>（各半 <code>{fmt_nt(round(stats['joint_total']/2))}</code>）",
        f"  ├ 👨 Alan 個人花費：<code>{fmt_nt(stats['alan_personal'])}</code>",
        f"  └ 👩 Anne 個人花費：<code>{fmt_nt(stats['anne_personal'])}</code>",
        "",
        "─── ⚖️ 誰刷多少 ＆ 代墊結算 ───",
        f"• 👨 Alan 現場實付：<code>{fmt_nt(stats['alan_paid'])}</code>（應負擔 <code>{fmt_nt(stats['alan_should'])}</code>）",
        f"• 👩 Anne 現場實付：<code>{fmt_nt(stats['anne_paid'])}</code>（應負擔 <code>{fmt_nt(stats['anne_should'])}</code>）",
        f"• <b>旅途現場結算</b>：{trip_settle}",
        f"• <b>若加計行前未結 (+NT$36,488)</b>：{comb_settle}",
        "",
        "─── 📝 最近 10 筆旅途明細 ───",
    ]

    if not rows:
        lines.append("<i>尚無旅途記帳紀錄！直接傳送如：</i>\n<code>龍蝦湯 4500isk Al星展</code>\n<code>風衣 85gbp An個人 Chase</code>\n<i>即可 5 秒記帳！</i>")
    else:
        s_icon = {"joint": "💑", "alan": "👨", "anne": "👩"}
        for r in rows[-10:]:
            c_short = CARDS_INFO.get(r["card_key"], {}).get("label", r["card_key"]).split()[-1]
            lines.append(
                f"• <code>#{r['id']}</code> <code>[{r['date']}]</code> {s_icon.get(r['split'], '💑')} <b>{html.escape(r['item'])}</b>｜"
                f"<code>{r['orig_amount']:g}{r['currency']}</code> (<code>{fmt_nt(r['total_twd'])}</code>)｜{c_short}"
            )

    lines.append("\n💡 <i>直接打字「品項 金額幣別」即可記帳；輸入 <code>/del 編號</code> 可刪除指定筆數</i>")
    return "\n".join(lines)


def build_cards_radar_text() -> str:
    rows = read_travel_expenses()
    stats = calc_trip_settlement(rows)
    used_map = stats["card_used"]

    lines = [
        "💳 <b>英冰旅途：信用卡高回饋額度雷達</b>",
        "（自動累計旅途記帳本金，站在櫃檯前 1 秒決定掏哪張卡！）\n",
        "─── 👨 <b>Alan 的神卡額度</b> ───",
    ]

    for ck in ("Al_星展eco", "Al_永豐幣倍", "Al_星展傳說", "Al_聯邦賴點"):
        info = CARDS_INFO[ck]
        used = used_map.get(ck, 0)
        lim = info["limit"]
        if lim > 0:
            rem = max(0, lim - used)
            bar = progress_bar(used, lim, 8)
            lines.append(
                f"• <b>{info['label']}</b>（{info['desc']}）\n"
                f"  <code>{bar}</code> 已刷 <code>{fmt_nt(used)}</code> / 剩 <b><code>{fmt_nt(rem)}</code></b>"
            )
        else:
            lines.append(f"• <b>{info['label']}</b>（{info['desc']}）：已刷 <code>{fmt_nt(used)}</code>（無上限）")

    lines.append("\n─── 👩 <b>Anne 的神卡額度</b> ───")
    for ck in ("An_星展eco", "An_Chase"):
        info = CARDS_INFO[ck]
        used = used_map.get(ck, 0)
        lim = info["limit"]
        if lim > 0:
            rem = max(0, lim - used)
            bar = progress_bar(used, lim, 8)
            lines.append(
                f"• <b>{info['label']}</b>（{info['desc']}）\n"
                f"  <code>{bar}</code> 已刷 <code>{fmt_nt(used)}</code> / 剩 <b><code>{fmt_nt(rem)}</code></b>"
            )
        else:
            lines.append(f"• <b>{info['label']}</b>（{info['desc']}）：已刷 <code>{fmt_nt(used)}</code>（免手續費無上限 ✨）")

    lines.append(
        "\n─── 💡 <b>旅途刷卡優先順序小抄</b> ───\n"
        "1️⃣ <b>當地實體感應/插卡</b>：先刷滿雙方 <b>星展 eco (5%, 各 1.5萬)</b> ➔ 再刷 <b>Alan 永豐幣倍 (6%, 2萬)</b> ➔ <b>星展傳說 (5%, 1.25萬)</b>\n"
        "2️⃣ <b>線上購票 / 餐廳 / 額度滿後</b>：刷 <b>Anne Chase 美國卡</b>（免 1.5% 海外手續費、餐廳 3x）"
    )
    return "\n".join(lines)


# ─── 原有家庭資產查詢邏輯 ───

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
                {"command": "trip", "description": "✈️ 英冰旅途記帳＆分帳結算總覽"},
                {"command": "cards", "description": "💳 信用卡高回饋額度雷達（剩餘額度進度條）"},
                {"command": "summary", "description": "📊 最新家庭資產與共同基金總覽"},
                {"command": "joint", "description": "💑 共同基金深度分析（壓力測試＋達標倒數）"},
                {"command": "accounts", "description": "🏦 各銀行與證券帳戶最新餘額"},
                {"command": "flows", "description": "💸 各期間資金流向（流入/流出排行）"},
                {"command": "stats", "description": "📐 財務健康度、累計成長率與極值"},
                {"command": "acct", "description": "🔍 查詢單一帳戶歷月水位變化"},
                {"command": "ledger", "description": "📒 共同基金收支帳本（夢想撲滿近期紀錄）"},
                {"command": "sync", "description": "🔄 從 Google 試算表同步最新資料並更新網站"},
            ]
        })
    except Exception as e:
        print(f"Warning: setMyCommands failed: {e}", file=sys.stderr)

    print("🤖 家庭資產 ＆ 英冰旅途快記 Bot (@ALANANNE_FAMILY_bot) 已啟動！", flush=True)
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

                    if allowed_users and user_id not in allowed_users:
                        try:
                            tg_api(token, "answerCallbackQuery", {"callback_query_id": cb_id, "text": "請先驗證通關密碼"})
                        except Exception:
                            pass
                        continue

                    toast_text = ""
                    if data_str.startswith("flow:"):
                        idx = int(data_str.split(":", 1)[1])
                        txt, kb = build_flows_text(idx)
                        edit_msg(token, chat_id, msg_id, txt, kb)
                        toast_text = "已切換期間"
                    elif data_str.startswith("acct:"):
                        acct_name = data_str.split(":", 1)[1]
                        txt = build_single_account_text(acct_name)
                        edit_msg(token, chat_id, msg_id, txt, build_account_selector_keyboard())
                        toast_text = f"已載入 {acct_name}"
                    elif data_str.startswith("tcard:") or data_str.startswith("tsplit:"):
                        parts = data_str.split(":")
                        action, exp_id, new_val = parts[0], int(parts[1]), parts[2]
                        rows = read_travel_expenses()
                        target = None
                        changed = False
                        for r in rows:
                            if r["id"] == exp_id:
                                if action == "tcard":
                                    if r["card_key"] != new_val:
                                        r["card_key"] = new_val
                                        b_twd, f_twd, tot_twd, payer = calc_twd_and_fee(r["orig_amount"], r["currency"], new_val)
                                        r["base_twd"] = b_twd
                                        r["fee_twd"] = f_twd
                                        r["total_twd"] = tot_twd
                                        r["payer"] = payer
                                        changed = True
                                    toast_text = f"已切換為 {CARDS_INFO.get(new_val, {}).get('label', new_val)}"
                                else:
                                    if r["split"] != new_val:
                                        r["split"] = new_val
                                        changed = True
                                    s_map = {"joint": "💑 共同平分", "alan": "👨 Alan 個人", "anne": "👩 Anne 個人"}
                                    toast_text = f"已切換為 {s_map.get(new_val, new_val)}"
                                target = r
                                break
                        if target and changed:
                            write_travel_expenses(rows)
                            txt = format_expense_receipt(target, rows)
                            kb = expense_action_keyboard(target["id"], target["card_key"], target["split"])
                            edit_msg(token, chat_id, msg_id, txt, kb)
                        elif not target:
                            toast_text = "此筆紀錄已刪除"
                    elif data_str.startswith("tdel:"):
                        exp_id = int(data_str.split(":", 1)[1])
                        rows = read_travel_expenses()
                        deleted = next((r for r in rows if r["id"] == exp_id), None)
                        if deleted:
                            rows = [r for r in rows if r["id"] != exp_id]
                            write_travel_expenses(rows)
                            edit_msg(
                                token,
                                chat_id,
                                msg_id,
                                f"🗑 已撤銷第 <code>#{exp_id}</code> 筆旅途記帳（{html.escape(deleted['item'])} {deleted['orig_amount']:g}{deleted['currency']}）",
                            )
                            toast_text = f"🗑 已刪除 #{exp_id}"
                        else:
                            edit_msg(
                                token,
                                chat_id,
                                msg_id,
                                f"🗑 第 <code>#{exp_id}</code> 筆旅途記帳已撤銷",
                            )
                            toast_text = "此筆已撤銷"
                    elif data_str == "tclear:ask":
                        rows = read_travel_expenses()
                        edit_msg(
                            token,
                            chat_id,
                            msg_id,
                            f"⚠️ <b>確定要清空目前全部 {len(rows)} 筆旅途記帳嗎？</b>\n（清空前會自動幫你在電腦備份一份 <code>travel_expenses.bak.csv</code>）",
                            {
                                "inline_keyboard": [
                                    [
                                        {"text": "⚠️ 確定清空歸零", "callback_data": "tclear:confirm"},
                                        {"text": "✖️ 取消返回", "callback_data": "tclear:cancel"},
                                    ]
                                ]
                            },
                        )
                        toast_text = "請確認是否清空"
                    elif data_str == "tclear:confirm":
                        if TRAVEL_CSV.exists():
                            (BASE_DIR / "travel_expenses.bak.csv").write_bytes(TRAVEL_CSV.read_bytes())
                        write_travel_expenses([])
                        edit_msg(token, chat_id, msg_id, "🧹 <b>已清空全部旅途記帳資料！</b>（帳本與刷卡額度已歸零）")
                        toast_text = "已清空歸零"
                    elif data_str == "tclear:cancel":
                        edit_msg(token, chat_id, msg_id, build_trip_summary_text(), trip_summary_inline_kb())
                        toast_text = "已取消"

                    try:
                        tg_api(token, "answerCallbackQuery", {"callback_query_id": cb_id, "text": toast_text})
                    except Exception:
                        pass
                    continue

                msg = upd.get("message") or upd.get("edited_message")
                if not msg:
                    continue
                chat_id = msg["chat"]["id"]
                user_id = msg.get("from", {}).get("id", 0)
                text = (msg.get("text") or "").strip()
                photos = msg.get("photo") or []
                caption = (msg.get("caption") or "").strip()

                if not text and not photos:
                    continue

                # 權限檢查
                if allowed_users and user_id not in allowed_users:
                    if text in valid_passcodes or caption in valid_passcodes:
                        save_authorized_user(user_id, allowed_users)
                        send_msg(
                            token,
                            chat_id,
                            "🎉 <b>驗證成功！歡迎使用家庭資產＆旅途快記小幫手</b>\n下方按鈕可隨時查詢，或直接打字／拍收據 5 秒記帳：",
                            main_keyboard(),
                        )
                        send_msg(token, chat_id, build_trip_summary_text(), inline_web_button())
                    else:
                        send_msg(
                            token,
                            chat_id,
                            "🔐 請先輸入家庭資產<b>通關密碼</b>以啟用查詢權限（只需驗證這一次）：",
                        )
                    continue

                # 0. 拍照記帳（收到收據或刷卡截圖照片）
                if photos:
                    gemini_key = env.get("GEMINI_API_KEY", "").strip()
                    if not gemini_key:
                        send_msg(token, chat_id, "❌ .env 尚未設定 GEMINI_API_KEY，無法辨識照片。", main_keyboard())
                        continue
                    best_photo = photos[-1]
                    send_msg(token, chat_id, "📸 <i>收到收據照片！正在用 AI 自動辨識品項與外幣金額...</i>")
                    try:
                        parsed = ocr_receipt_photo(token, gemini_key, best_photo["file_id"], caption)
                        img_bytes = parsed.pop("_img_bytes", b"")
                        rows = read_travel_expenses()
                        next_id = (max(r["id"] for r in rows) + 1) if rows else 1
                        parsed["id"] = next_id
                        rows.append(parsed)
                        write_travel_expenses(rows)

                        if img_bytes:
                            rec_dir = BASE_DIR / "receipts"
                            rec_dir.mkdir(exist_ok=True)
                            safe_name = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff_\-]+", "_", parsed["item"])[:30]
                            (rec_dir / f"receipt_{next_id:03d}_{safe_name}.jpg").write_bytes(img_bytes)

                        receipt_txt = format_expense_receipt(parsed, rows)
                        kb = expense_action_keyboard(next_id, parsed["card_key"], parsed["split"])
                        send_msg(token, chat_id, receipt_txt, kb)
                    except Exception as e:
                        send_msg(
                            token,
                            chat_id,
                            f"❌ 照片辨識失敗（<code>{html.escape(str(e))}</code>）\n你可以直接打字輸入：<code>品項 金額幣別</code>",
                            main_keyboard(),
                        )
                    continue

                # 1. 刪除旅途記帳指令 (/del 3)
                if text.startswith("/del "):
                    arg = text.split(" ", 1)[1].strip().lstrip("#")
                    if arg.isdigit():
                        exp_id = int(arg)
                        rows = read_travel_expenses()
                        deleted = next((r for r in rows if r["id"] == exp_id), None)
                        if deleted:
                            rows = [r for r in rows if r["id"] != exp_id]
                            write_travel_expenses(rows)
                            send_msg(token, chat_id, f"🗑 已刪除 <code>#{exp_id}</code>：{html.escape(deleted['item'])}（{fmt_nt(deleted['total_twd'])}）", main_keyboard())
                        else:
                            send_msg(token, chat_id, f"找不到編號 #{exp_id} 的旅途記帳紀錄。", main_keyboard())
                        continue

                # 2. 調整匯率指令 (/rate gbp 43.5) 與時區切換 (/tz uk | /tz tw)
                if text.startswith("/rate "):
                    parts = text.split()
                    if len(parts) == 3:
                        try:
                            cur_c = parts[1].upper()
                            val = float(parts[2])
                            save_rate(cur_c, val)
                            send_msg(token, chat_id, f"✅ 已更新 <b>{cur_c}</b> 匯率為 <code>{val}</code>", main_keyboard())
                        except Exception:
                            send_msg(token, chat_id, "格式範例：<code>/rate gbp 43.5</code> 或 <code>/rate isk 0.2632</code>", main_keyboard())
                        continue

                if text.startswith("/tz"):
                    parts = text.split()
                    mode = parts[1].upper() if len(parts) > 1 else ""
                    if mode in ("UK", "IS", "UTC", "GMT"):
                        save_tz("UK")
                        send_msg(token, chat_id, f"🕒 已切換為 <b>英國／冰島當地時間 (UTC+0)</b>！目前時間：<code>{get_now_str()}</code>", main_keyboard())
                    elif mode in ("TW", "TPE"):
                        save_tz("TW")
                        send_msg(token, chat_id, f"🕒 已切換為 <b>台灣時間 (UTC+8)</b>！目前時間：<code>{get_now_str()}</code>", main_keyboard())
                    else:
                        send_msg(token, chat_id, f"🕒 目前紀錄時間為：<code>{get_now_str()}</code>\n• 切換英冰當地時間：<code>/tz uk</code>\n• 切換台灣時間：<code>/tz tw</code>", main_keyboard())
                    continue

                # 3. 選單與固定指令
                if text in ("/start", "/summary", "📊 資產總覽"):
                    send_msg(token, chat_id, build_summary_text(), main_keyboard())
                elif text in ("/trip", "✈️ 旅途記帳＆結算"):
                    send_msg(token, chat_id, build_trip_summary_text(), trip_summary_inline_kb() or main_keyboard())
                elif text in ("/clear", "/clear_trip", "清空", "清空旅途"):
                    rows = read_travel_expenses()
                    send_msg(
                        token,
                        chat_id,
                        f"⚠️ <b>確定要清空目前全部 {len(rows)} 筆旅途記帳嗎？</b>",
                        {
                            "inline_keyboard": [
                                [
                                    {"text": "⚠️ 確定清空歸零", "callback_data": "tclear:confirm"},
                                    {"text": "✖️ 取消返回", "callback_data": "tclear:cancel"},
                                ]
                            ]
                        },
                    )
                elif text in ("/cards", "💳 刷卡額度雷達"):
                    send_msg(token, chat_id, build_cards_radar_text(), main_keyboard())
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
                    # 4. 嘗試解析為「一句話旅途快記」
                    parsed = parse_quick_expense(text)
                    if parsed:
                        rows = read_travel_expenses()
                        next_id = (max(r["id"] for r in rows) + 1) if rows else 1
                        parsed["id"] = next_id
                        rows.append(parsed)
                        write_travel_expenses(rows)
                        receipt_txt = format_expense_receipt(parsed, rows)
                        kb = expense_action_keyboard(next_id, parsed["card_key"], parsed["split"])
                        send_msg(token, chat_id, receipt_txt, kb)
                    else:
                        send_msg(
                            token,
                            chat_id,
                            "✈️ <b>旅途 5 秒快記怎麼打？</b>\n"
                            "直接傳送「<b>品項 金額幣別 [誰的卡] [個人]</b>」，例如：\n"
                            "• <code>龍蝦湯 4500isk Al星展</code>（預設共同平分＋自動算1.5%手續費）\n"
                            "• <code>風衣 85gbp An個人 Chase</code>（Anne個人＋Chase免手續費）\n"
                            "• <code>熱狗 750isk</code>（只打金額也能記！跳出按鈕讓你點選哪張卡）\n\n"
                            "或點選下方快捷鍵查詢：",
                            main_keyboard(),
                        )
        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"Polling error: {e}", file=sys.stderr, flush=True)
            time.sleep(5)


if __name__ == "__main__":
    main()
