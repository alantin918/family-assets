#!/usr/bin/env python3
"""
從 Google 試算表（透過 Apps Script Web App）自動抓取「財務」分頁的最新每月資產快照與共同基金帳本，
更新本機的 balances.csv、summary.csv、ledger.csv，並連同旅途記帳 travel_expenses.csv
一併產生加密後的 data.enc.json 供 GitHub Pages 網站讀取。
"""

import base64
import csv
import datetime
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.parse
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / ".env"
BALANCES_CSV = BASE_DIR / "balances.csv"
SUMMARY_CSV = BASE_DIR / "summary.csv"
LEDGER_CSV = BASE_DIR / "ledger.csv"
TRAVEL_CSV = BASE_DIR / "travel_expenses.csv"
ENCRYPTED_JSON = BASE_DIR / "data.enc.json"

# 帳戶固定順序與分類（與原 balances.csv 完全一致）
ACCOUNT_ORDER = [
    ("Line bank", "銀行存款"),
    ("iPass Money", "電子支付"),
    ("土地", "銀行存款"),
    ("玉山", "銀行存款"),
    ("街口", "電子支付"),
    ("彰化", "銀行存款"),
    ("永豐", "銀行存款"),
    ("股票", "證券"),
    ("股票在途款項", "證券"),
    ("將來", "銀行存款"),
    ("中信", "銀行存款"),
    ("台新", "銀行存款"),
    ("國泰", "銀行存款"),
    ("台灣", "銀行存款"),
    ("星展", "銀行存款"),
    ("聯邦", "銀行存款"),
    ("樂天", "銀行存款"),
]


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


def parse_nt(val: str) -> int:
    if not val:
        return 0
    cleaned = re.sub(r"[^0-9\-]", "", str(val))
    return int(cleaned) if cleaned and cleaned != "-" else 0


def normalize_date(val: str) -> str:
    val = str(val).strip()
    m = re.match(r"^(\d{4})[/\-](\d{1,2})[/\-](\d{1,2})$", val)
    if not m:
        return ""
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    return f"{y:04d}-{mo:02d}-{d:02d}"


def encrypt_payload(plaintext_bytes: bytes, passcode: str) -> dict:
    salt = os.urandom(16).hex()
    verify = hashlib.sha256(f"{salt}:{passcode}:verify".encode("utf-8")).hexdigest()[:16]
    out = bytearray(len(plaintext_bytes))
    block_count = (len(plaintext_bytes) + 31) // 32
    for i in range(block_count):
        ks = hashlib.sha256(f"{salt}:{passcode}:{i}".encode("utf-8")).digest()
        start = i * 32
        end = min(start + 32, len(plaintext_bytes))
        for j in range(start, end):
            out[j] = plaintext_bytes[j] ^ ks[j - start]
    return {
        "salt": salt,
        "verify": verify,
        "cipher": base64.b64encode(bytes(out)).decode("ascii"),
    }


def read_travel_rows() -> list:
    if not TRAVEL_CSV.exists():
        return []
    with open(TRAVEL_CSV, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_encrypted_json(summary_rows: list, balance_rows: list, ledger_rows: list, env: dict):
    payload_obj = {
        "updated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "summary": summary_rows,
        "balances": balance_rows,
        "ledger": ledger_rows,
        "travel": read_travel_rows(),
    }
    plaintext_bytes = json.dumps(payload_obj, ensure_ascii=False).encode("utf-8")

    passcodes = []
    for k in [env.get("WEB_PASSCODE", ""), env.get("APPS_SCRIPT_KEY", ""), "alan2026"]:
        k = k.strip()
        if k and k not in passcodes:
            passcodes.append(k)

    locks = [encrypt_payload(plaintext_bytes, code) for code in passcodes]
    with open(ENCRYPTED_JSON, "w", encoding="utf-8") as f:
        json.dump({"version": 1, "locks": locks}, f, ensure_ascii=False, indent=2)


def fetch_sheet_data(url: str, key: str) -> dict:
    keys_to_try = ["alan2026"] if key == "alan2026" else ["alan2026", key]
    for k in keys_to_try:
        full_url = f"{url}?{urllib.parse.urlencode({'key': k, 'sheet': '財務'})}"
        body = subprocess.check_output(["curl", "-4", "-sL", full_url], timeout=30).decode("utf-8")
        if body.strip() != "Unauthorized":
            return json.loads(body)
    raise RuntimeError("Apps Script 回傳 Unauthorized，請確認 .env 中的 APPS_SCRIPT_KEY 與部署版本一致。")


def git_push_update(commit_msg: str):
    if not (BASE_DIR / ".git").exists():
        return
    subprocess.run(["git", "add", "data.enc.json", "index.html", "README.md", "sync.py", "bot.py", ".gitignore"], cwd=BASE_DIR, check=False)
    status = subprocess.check_output(["git", "status", "--porcelain"], cwd=BASE_DIR).decode("utf-8").strip()
    if status:
        subprocess.run(["git", "commit", "-m", commit_msg], cwd=BASE_DIR, check=True)
        subprocess.run(["git", "push"], cwd=BASE_DIR, check=True)
        print("🚀 已自動推送加密資料並更新 GitHub Pages 網站！")
    else:
        print("ℹ️ GitHub Pages 資料已是最新，無需推送。")


def main():
    env = load_env(ENV_PATH)

    if "--encrypt-only" in sys.argv:
        with open(SUMMARY_CSV, encoding="utf-8") as f:
            summary_rows = [
                {"date": r["date"], "total": int(r["total"]), "after_joint": int(r["after_joint"])}
                for r in csv.DictReader(f)
            ]
        with open(BALANCES_CSV, encoding="utf-8") as f:
            balance_rows = [
                {"date": r["date"], "account": r["account"], "category": r["category"], "amount": int(r["amount"])}
                for r in csv.DictReader(f)
            ]
        ledger_rows = []
        if LEDGER_CSV.exists():
            with open(LEDGER_CSV, encoding="utf-8") as f:
                ledger_rows = list(csv.DictReader(f))
        write_encrypted_json(summary_rows, balance_rows, ledger_rows, env)
        print("🔒 已由本機 CSV 重新產生加密檔 data.enc.json")
        if "--push" in sys.argv:
            git_push_update("更新加密資料檔 data.enc.json")
        return

    url = env.get("APPS_SCRIPT_URL", "")
    key = env.get("APPS_SCRIPT_KEY", "alan2026")
    if not url or "請貼在這裡" in url:
        print("❌ 請先在 .env 設定 APPS_SCRIPT_URL", file=sys.stderr)
        sys.exit(1)

    print("🔄 正在從 Google 試算表抓取最新「財務」分頁資料...")
    data = fetch_sheet_data(url, key)
    if "財務" not in data:
        raise RuntimeError(f"找不到「財務」分頁，目前分頁：{list(data.keys())}")

    rows = data["財務"]["display"]

    # 1. 解析左半部「夢想撲滿」共同基金收支歷史帳本 (Cols 0..6)
    ledger_rows = []
    for idx, r in enumerate(rows[1:], start=1):
        if len(r) < 5:
            continue
        item = r[0].strip()
        if item == "房租列表":
            break
        tot_str = r[4].strip()
        if not item and not tot_str:
            continue
        if tot_str.startswith("NT$") or tot_str.startswith("-NT$"):
            note = (r[5].strip() if len(r) > 5 else "") or (r[6].strip() if len(r) > 6 else "")
            ledger_rows.append({
                "item": item,
                "alan": parse_nt(r[1]) if len(r) > 1 else 0,
                "anne": parse_nt(r[2]) if len(r) > 2 else 0,
                "expense": parse_nt(r[3]) if len(r) > 3 else 0,
                "balance": parse_nt(tot_str),
                "note": note,
            })

    # 2. 解析右下半部「每月帳戶餘額快照」
    after_joint_row_idx = None
    for idx, r in enumerate(rows):
        if len(r) > 7 and r[7].strip() == "扣除共同基金後":
            after_joint_row_idx = idx
            break

    if after_joint_row_idx is None:
        raise RuntimeError("在「財務」分頁中找不到『扣除共同基金後』一列")

    date_row_idx = after_joint_row_idx + 1
    date_row = rows[date_row_idx]

    block_start_idx = max(0, after_joint_row_idx - 25)
    label_to_row = {}
    for idx in range(block_start_idx, after_joint_row_idx + 1):
        r = rows[idx]
        if len(r) > 7 and r[7].strip():
            label_to_row[r[7].strip()] = r

    valid_cols = []
    for col_idx in range(8, len(date_row)):
        norm_d = normalize_date(date_row[col_idx])
        if norm_d:
            valid_cols.append((col_idx, norm_d))

    if not valid_cols:
        raise RuntimeError("找不到任何有效的快照日期欄")

    summary_rows = []
    balance_rows = []

    total_row = label_to_row.get("總共", [])
    after_row = label_to_row.get("扣除共同基金後", [])

    for col_idx, norm_d in valid_cols:
        tot_val = parse_nt(total_row[col_idx]) if col_idx < len(total_row) else 0
        aft_val = parse_nt(after_row[col_idx]) if col_idx < len(after_row) else 0
        summary_rows.append({
            "date": norm_d,
            "total": tot_val,
            "after_joint": aft_val,
        })

        for acct_name, category in ACCOUNT_ORDER:
            acct_row = label_to_row.get(acct_name, [])
            amt = parse_nt(acct_row[col_idx]) if col_idx < len(acct_row) else 0
            balance_rows.append({
                "date": norm_d,
                "account": acct_name,
                "category": category,
                "amount": amt,
            })

    with open(SUMMARY_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["date", "total", "after_joint"])
        writer.writeheader()
        writer.writerows(summary_rows)

    with open(BALANCES_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["date", "account", "category", "amount"])
        writer.writeheader()
        writer.writerows(balance_rows)

    with open(LEDGER_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["item", "alan", "anne", "expense", "balance", "note"])
        writer.writeheader()
        writer.writerows(ledger_rows)

    write_encrypted_json(summary_rows, balance_rows, ledger_rows, env)

    latest = summary_rows[-1]
    joint = latest["total"] - latest["after_joint"]
    print(f"✅ 同步完成！共抓取 {len(summary_rows)} 期快照（{summary_rows[0]['date']} ～ {latest['date']}）與 {len(ledger_rows)} 筆共同基金帳本紀錄")
    print(f"📊 最新一期 ({latest['date']})：")
    print(f"   • 總資產：NT${latest['total']:,}")
    print(f"   • 共同基金：NT${joint:,}")
    print(f"   • Alan 可用：NT${latest['after_joint']:,}")

    if "--push" in sys.argv:
        git_push_update(f"更新家庭資產快照與參數 ({latest['date']})")


if __name__ == "__main__":
    main()
