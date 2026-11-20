import asyncio
import datetime
import json
import logging
import os
import re
import sqlite3
import threading
import urllib.error
import urllib.parse
import urllib.request
from flask import Flask
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    Update,
)
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

BOT_TOKEN = os.environ.get(
    "BOT_TOKEN", "9178685196:AAGnHHXm7at9Pt8IWIW-zPdiOPkl564dl3U"
)
MAIN_ADMIN_ID = int(os.environ.get("ADMIN_ID", "2068530616"))
DB_NAME = os.environ.get("DB_NAME", "rsp_otp_bot.db")

ENV_API_URL = os.environ.get("VOLTEX_API_URL", "")
ENV_API_KEY = os.environ.get("VOLTEX_API_KEY", "")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

BASE_COUNTRIES: dict[str, dict] = {}


def get_conn() -> sqlite3.Connection:
    return sqlite3.connect(DB_NAME, timeout=15)


def init_db() -> None:
    conn = get_conn()
    cursor = conn.cursor()

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS admins (
            user_id INTEGER PRIMARY KEY
        )
        """
    )
    cursor.execute(
        "INSERT OR IGNORE INTO admins (user_id) VALUES (?)",
        (MAIN_ADMIN_ID,),
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS custom_countries (
            country_code TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            flag TEXT DEFAULT '🌍',
            dial TEXT DEFAULT '+1',
            service TEXT DEFAULT 'OTP',
            rate REAL DEFAULT 15.0
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS country_rates (
            country_code TEXT PRIMARY KEY,
            rate REAL NOT NULL
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            full_name TEXT,
            balance REAL DEFAULT 0.0,
            otp_count INTEGER DEFAULT 0,
            selected_country TEXT DEFAULT '',
            referred_by INTEGER,
            last_req REAL DEFAULT 0,
            joined_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS daily_stats (
            stat_date TEXT NOT NULL,
            user_id INTEGER NOT NULL,
            otp_count INTEGER DEFAULT 0,
            earned REAL DEFAULT 0.0,
            PRIMARY KEY (stat_date, user_id)
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            country_code TEXT,
            phone_number TEXT,
            otp_code TEXT,
            status TEXT DEFAULT 'ACTIVE',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS numbers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            country TEXT,
            phone TEXT UNIQUE,
            status TEXT DEFAULT 'unused',
            used_by INTEGER
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS api_panels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            panel_name TEXT,
            api_key TEXT,
            get_num_url TEXT,
            get_otp_url TEXT,
            status TEXT DEFAULT 'ACTIVE'
        )
        """
    )

    cursor.execute("PRAGMA table_info(users)")
    user_cols = {row[1] for row in cursor.fetchall()}
    if "otp_count" not in user_cols:
        cursor.execute("ALTER TABLE users ADD COLUMN otp_count INTEGER DEFAULT 0")
    if "selected_country" not in user_cols:
        cursor.execute("ALTER TABLE users ADD COLUMN selected_country TEXT DEFAULT ''")
    if "referred_by" not in user_cols:
        cursor.execute("ALTER TABLE users ADD COLUMN referred_by INTEGER")
    if "last_req" not in user_cols:
        cursor.execute("ALTER TABLE users ADD COLUMN last_req REAL DEFAULT 0")

    for removed_code in ("it", "bd", "us", "gb", "in"):
        cursor.execute("DELETE FROM country_rates WHERE country_code = ?", (removed_code,))
        cursor.execute("DELETE FROM custom_countries WHERE country_code = ?", (removed_code,))

    cursor.execute(
        "UPDATE users SET selected_country = '' WHERE selected_country IN ('it', 'bd', 'us', 'gb', 'in')"
    )

    for code, info in BASE_COUNTRIES.items():
        cursor.execute(
            "INSERT OR IGNORE INTO country_rates (country_code, rate) VALUES (?, ?)",
            (code, float(info["rate"])),
        )

    default_settings = {
        "bot_name": "RSP OTP BOT",
        "api_url": ENV_API_URL,
        "api_key": ENV_API_KEY,
        "support_contact": "@reyad0045",
        "support_mode": "LIVE",
        "support_link": "https://t.me/reyad0045",
        "withdraw_status": "ON",
        "min_withdraw": "50.0",
        "refer_reward": "0.2",
        "cooldown": "10",
        "num_per_req": "3",
        "num_per_share": "1",
        "withdraw_methods": "Bkash,Nagad,Binance",
        "auto_range": "ON",
        "text_start": "",
        "text_get_num": "📱 **স্টক বা প্যানেল থেকে নম্বর সিলেক্ট করুন:**",
        "text_traffic": "📊 **রিয়েল-টাইম স্টক ও অর্ডার স্ট্যাটাস:**",
        "text_refer": "🎁 **রেফার করে আয় করুন!**\nপ্রতি সফল রেফারে পাবেন `৳{refer_reward}`।\n\nআপনার রেফার লিংক:\n`{refer_link}`",
        "text_withdraw": "💳 **টাকা উত্তোলন (Withdrawal):**\nমিনিমাম উইথড্র: `৳{min_withdraw}`\nআপনার ব্যালেন্স: `৳{balance}`",
        "text_support": "🎧 **সাপোর্ট সেন্টার:**\nযেকোনো সমস্যায় নিচের অপশন ব্যবহার করুন।",
        "last_winner_date": "",
    }
    for key, val in default_settings.items():
        cursor.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
            (key, val),
        )

    if os.environ.get("VOLTEX_API_URL"):
        cursor.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES ('api_url', ?)",
            (os.environ["VOLTEX_API_URL"].strip(),),
        )
    if os.environ.get("VOLTEX_API_KEY"):
        cursor.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES ('api_key', ?)",
            (os.environ["VOLTEX_API_KEY"].strip(),),
        )

    conn.commit()
    conn.close()


def get_all_countries() -> dict[str, dict]:
    countries = {k: dict(v) for k, v in BASE_COUNTRIES.items()}
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT country_code, name, flag, dial, service, rate
        FROM custom_countries
        """
    )
    for code, name, flag, dial, service, rate in cursor.fetchall():
        c_key = str(code).strip().lower()
        if not c_key:
            continue
        countries[c_key] = {
            "name": str(name or c_key.upper()),
            "flag": str(flag or "🌍"),
            "dial": str(dial or "+1"),
            "service": str(service or "OTP"),
            "rate": float(rate if rate is not None else 15.0),
            "is_custom": True,
        }

    cursor.execute("SELECT country_code, rate FROM country_rates")
    for code, rate in cursor.fetchall():
        c_key = str(code).strip().lower()
        if c_key in countries and rate is not None:
            countries[c_key]["rate"] = float(rate)

    conn.close()
    return countries


def add_or_update_country(
    country_code: str,
    name: str,
    dial: str = "+1",
    rate: float = 15.0,
    flag: str = "🌍",
    service: str = "OTP",
) -> tuple[bool, str]:
    clean_code = re.sub(r"[^a-z0-9_]", "", str(country_code or "").strip().lower())
    clean_name = str(name or "").strip()
    if not clean_code or not clean_name:
        return False, "সঠিক Country Code এবং Country Name দিন।"

    clean_dial = str(dial or "+1").strip()
    if not clean_dial.startswith("+"):
        clean_dial = "+" + clean_dial
    clean_dial = re.sub(r"[^+\d]", "", clean_dial) or "+1"

    clean_flag = str(flag or "🌍").strip() or "🌍"
    clean_service = str(service or "OTP").strip() or "OTP"
    clean_rate = max(0.0, float(rate))

    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO custom_countries
            (country_code, name, flag, dial, service, rate)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(country_code) DO UPDATE SET
            name = excluded.name,
            flag = excluded.flag,
            dial = excluded.dial,
            service = excluded.service,
            rate = excluded.rate
        """,
        (clean_code, clean_name, clean_flag, clean_dial, clean_service, clean_rate),
    )
    cursor.execute(
        "INSERT OR REPLACE INTO country_rates (country_code, rate) VALUES (?, ?)",
        (clean_code, clean_rate),
    )
    conn.commit()
    conn.close()
    return True, clean_code


def delete_custom_country(country_code: str) -> bool:
    c_key = str(country_code or "").strip().lower()
    if not c_key or c_key in BASE_COUNTRIES:
        return False
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM custom_countries WHERE country_code = ?", (c_key,))
    cursor.execute("DELETE FROM country_rates WHERE country_code = ?", (c_key,))
    deleted = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return deleted


def resolve_country_code(raw_country) -> str:
    countries = get_all_countries()
    if not countries:
        return ""
    if isinstance(raw_country, str):
        val = raw_country.strip().lower()
        if val in countries:
            return val
        for code, info in countries.items():
            if info["name"].strip().lower() == val:
                return code
        dial_digits = re.sub(r"\D", "", val)
        if dial_digits:
            for code, info in countries.items():
                c_dial_digits = re.sub(r"\D", "", str(info.get("dial", "")))
                if c_dial_digits and dial_digits.startswith(c_dial_digits):
                    return code
    return next(iter(countries.keys()))


def get_setting(key: str, default: str = "") -> str:
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT value FROM settings WHERE key = ?", (key,))
    row = cursor.fetchone()
    conn.close()
    if row and row[0] is not None and row[0] != "":
        return row[0]
    if key == "api_url" and ENV_API_URL:
        return ENV_API_URL
    if key == "api_key" and ENV_API_KEY:
        return ENV_API_KEY
    return default


def set_setting(key: str, value: str) -> None:
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
        (key, str(value)),
    )
    conn.commit()
    conn.close()


def get_all_admin_ids() -> list[int]:
    admin_ids = {int(MAIN_ADMIN_ID)}
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM admins")
    for (uid,) in cursor.fetchall():
        if uid is not None:
            admin_ids.add(int(uid))
    conn.close()
    return list(admin_ids)


def is_admin(user_id: int) -> bool:
    return int(user_id) in set(get_all_admin_ids())


def add_admin_id(new_admin_id: int) -> None:
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR IGNORE INTO admins (user_id) VALUES (?)",
        (int(new_admin_id),),
    )
    conn.commit()
    conn.close()


def get_country_rate(country_code: str) -> float:
    countries = get_all_countries()
    code = resolve_country_code(country_code)
    if not code or code not in countries:
        return 0.0
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT rate FROM country_rates WHERE country_code = ?", (code,))
    row = cursor.fetchone()
    conn.close()
    if row and row[0] is not None:
        return float(row[0])
    return float(countries[code]["rate"])


def set_country_rate(country_code: str, rate: float) -> None:
    code = resolve_country_code(country_code)
    if not code:
        return
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR REPLACE INTO country_rates (country_code, rate) VALUES (?, ?)",
        (code, float(rate)),
    )
    cursor.execute(
        "UPDATE custom_countries SET rate = ? WHERE country_code = ?",
        (float(rate), code),
    )
    conn.commit()
    conn.close()


def ensure_user(
    user_id: int,
    username: str = "",
    full_name: str = "",
    referred_by: int | None = None,
) -> bool:
    default_c = resolve_country_code("")
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
    exists = cursor.fetchone() is not None
    if not exists:
        ref_val = (
            int(referred_by)
            if referred_by and int(referred_by) != int(user_id)
            else None
        )
        cursor.execute(
            """
            INSERT INTO users (user_id, username, full_name, balance, otp_count, selected_country, referred_by)
            VALUES (?, ?, ?, 0.0, 0, ?, ?)
            """,
            (user_id, username or "", full_name or "", default_c, ref_val),
        )
    else:
        cursor.execute(
            """
            UPDATE users
            SET username = COALESCE(NULLIF(?, ''), username),
                full_name = COALESCE(NULLIF(?, ''), full_name)
            WHERE user_id = ?
            """,
            (username or "", full_name or "", user_id),
        )
    conn.commit()
    conn.close()
    return not exists


def add_user_balance(user_id: int, amount: float) -> None:
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute(
        """
        UPDATE users
        SET balance = ROUND(balance + ?, 2)
        WHERE user_id = ?
        """,
        (float(amount), int(user_id)),
    )
    conn.commit()
    conn.close()


def deduct_all_user_balance(user_id: int) -> float:
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT balance FROM users WHERE user_id = ?", (int(user_id),))
    row = cursor.fetchone()
    bal = float(row[0] or 0.0) if row else 0.0
    cursor.execute("UPDATE users SET balance = 0.0 WHERE user_id = ?", (int(user_id),))
    conn.commit()
    conn.close()
    return bal


def get_user_profile(user_id: int):
    countries = get_all_countries()
    fallback_c = resolve_country_code("")
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT balance, otp_count, selected_country FROM users WHERE user_id = ?",
        (user_id,),
    )
    row = cursor.fetchone()
    conn.close()
    if not row:
        return 0.0, 0, fallback_c
    raw_c = str(row[2] or "").strip().lower()
    country = raw_c if raw_c in countries else fallback_c
    return float(row[0] or 0.0), int(row[1] or 0), country


def set_user_country(user_id: int, country_code: str) -> None:
    code = resolve_country_code(country_code)
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE users SET selected_country = ? WHERE user_id = ?",
        (code, user_id),
    )
    conn.commit()
    conn.close()


def create_active_order(user_id: int, country_code: str, phone_number: str) -> int:
    code = resolve_country_code(country_code) or "custom"
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO orders (user_id, country_code, phone_number, otp_code, status)
        VALUES (?, ?, ?, NULL, 'ACTIVE')
        """,
        (user_id, code, phone_number),
    )
    order_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return int(order_id)


def complete_order_and_reward(
    order_id: int, user_id: int, country_code: str, otp_code: str
) -> float:
    code = resolve_country_code(country_code)
    rate = get_country_rate(code) if code else 0.0
    today_str = datetime.datetime.now().strftime("%Y-%m-%d")

    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute(
        """
        UPDATE orders
        SET status = 'COMPLETED', otp_code = ?
        WHERE id = ? AND status = 'ACTIVE'
        """,
        (otp_code, order_id),
    )
    updated = cursor.rowcount
    if updated > 0:
        cursor.execute(
            """
            UPDATE users
            SET balance = ROUND(balance + ?, 2),
                otp_count = otp_count + 1
            WHERE user_id = ?
            """,
            (rate, user_id),
        )
        cursor.execute(
            """
            INSERT INTO daily_stats (stat_date, user_id, otp_count, earned)
            VALUES (?, ?, 1, ?)
            ON CONFLICT(stat_date, user_id) DO UPDATE SET
                otp_count = otp_count + 1,
                earned = ROUND(earned + excluded.earned, 2)
            """,
            (today_str, user_id, rate),
        )
    conn.commit()
    conn.close()
    return rate


def get_daily_top_users(date_str: str, limit: int = 10):
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT u.user_id, u.full_name, u.username, d.otp_count, d.earned
        FROM daily_stats d
        JOIN users u ON u.user_id = d.user_id
        WHERE d.stat_date = ? AND d.otp_count > 0
        ORDER BY d.otp_count DESC, d.earned DESC
        LIMIT ?
        """,
        (date_str, limit),
    )
    rows = cursor.fetchall()
    conn.close()
    return rows


def add_api_panel(
    panel_name: str, api_key: str, get_num_url: str, get_otp_url: str = ""
) -> None:
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO api_panels (panel_name, api_key, get_num_url, get_otp_url, status)
        VALUES (?, ?, ?, ?, 'ACTIVE')
        """,
        (panel_name.strip(), api_key.strip(), get_num_url.strip(), get_otp_url.strip()),
    )
    conn.commit()
    conn.close()


def list_api_panels():
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, panel_name, api_key, get_num_url, get_otp_url, status FROM api_panels ORDER BY id ASC"
    )
    rows = cursor.fetchall()
    conn.close()
    return rows


def get_api_panel(panel_id: int):
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, panel_name, api_key, get_num_url, get_otp_url, status FROM api_panels WHERE id = ?",
        (int(panel_id),),
    )
    row = cursor.fetchone()
    conn.close()
    return row


def delete_api_panel(panel_id: int) -> None:
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM api_panels WHERE id = ?", (int(panel_id),))
    conn.commit()
    conn.close()


def upload_manual_numbers(country, raw_numbers) -> int:
    if isinstance(country, str) and country.strip():
        c_name = country.strip()
    elif country not in (None, False, True):
        c_name = str(country).strip() or "General"
    else:
        c_name = "General"

    if isinstance(raw_numbers, str):
        items = raw_numbers.splitlines()
    elif isinstance(raw_numbers, (list, tuple, set)):
        items = list(raw_numbers)
    else:
        items = []

    conn = get_conn()
    cursor = conn.cursor()
    added = 0
    for item in items:
        phone = str(item or "").strip()
        if not phone:
            continue
        try:
            cursor.execute(
                "INSERT INTO numbers (country, phone, status) VALUES (?, ?, 'unused')",
                (c_name, phone),
            )
            added += 1
        except sqlite3.IntegrityError:
            pass
    conn.commit()
    conn.close()
    return added


def get_number_stock_summary(status: str):
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT country, COUNT(*) FROM numbers WHERE status = ? GROUP BY country",
        (status,),
    )
    rows = cursor.fetchall()
    conn.close()
    return rows


def clear_all_manual_numbers() -> None:
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM numbers")
    conn.commit()
    conn.close()


def pick_manual_number(country: str, user_id: int):
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, phone FROM numbers WHERE country = ? AND status = 'unused' LIMIT 1",
        (country,),
    )
    row = cursor.fetchone()
    if not row:
        conn.close()
        return None
    num_id, phone = row
    cursor.execute(
        "UPDATE numbers SET status = 'used', used_by = ? WHERE id = ?",
        (int(user_id), int(num_id)),
    )
    conn.commit()
    conn.close()
    return int(num_id), str(phone)


def _extract_phone_from_text(text: str) -> str:
    if not text:
        return ""
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            for key in ("phone", "number", "phone_number", "mobile", "msisdn"):
                val = data.get(key)
                if val:
                    return str(val).strip()
            if isinstance(data.get("data"), dict):
                for key in ("phone", "number", "phone_number", "mobile", "msisdn"):
                    val = data["data"].get(key)
                    if val:
                        return str(val).strip()
    except json.JSONDecodeError:
        pass
    match = re.search(r"\+?\d{8,15}", text)
    return match.group(0) if match else ""


def _extract_otp_from_text(text: str) -> str | None:
    if not text:
        return None
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            for key in ("code", "otp", "sms", "message"):
                val = data.get(key)
                if val is not None and str(val).strip():
                    return str(val).strip()
            if isinstance(data.get("data"), dict):
                for key in ("code", "otp", "sms", "message"):
                    val = data["data"].get(key)
                    if val is not None and str(val).strip():
                        return str(val).strip()
    except json.JSONDecodeError:
        pass
    cleaned = text.strip()
    if re.fullmatch(r"[A-Za-z0-9\-]{4,10}", cleaned):
        return cleaned
    return None


def _send_panel_http_request(
    url: str, api_key: str, method: str = "POST", payload: dict | None = None
) -> tuple[bool, str]:
    headers = {
        "User-Agent": "Mozilla/5.0 RSP-OTP-Bot/1.0",
        "Accept": "application/json, text/plain, */*",
    }
    if api_key:
        headers["mauthapi"] = api_key

    data_bytes = None
    if method.upper() == "POST":
        headers["Content-Type"] = "application/json"
        data_bytes = json.dumps(payload or {}).encode("utf-8")

    req = urllib.request.Request(
        url, data=data_bytes, headers=headers, method=method.upper()
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode("utf-8", errors="ignore").strip()
            return True, body
    except urllib.error.HTTPError as exc:
        err_body = ""
        try:
            err_body = exc.read().decode("utf-8", errors="ignore").strip()
        except Exception:
            pass
        return False, err_body or f"HTTP {exc.code}"
    except Exception as exc:
        return False, f"Request Error: {str(exc)[:120]}"


def call_custom_panel_get_number(
    panel_row, country_code: str = ""
) -> tuple[bool, str, str]:
    _, _, api_key, get_num_url, _, _ = panel_row
    if not get_num_url:
        return False, "", "Get Number URL set kora nei."

    target_url = (
        get_num_url.replace("{api_key}", api_key or "")
        .replace("{key}", api_key or "")
        .replace("{country}", country_code or "")
    )
    payload = {"country": country_code} if country_code else {}

    ok, body = _send_panel_http_request(
        target_url, api_key or "", method="POST", payload=payload
    )
    if not ok:
        sep = "&" if "?" in target_url else "?"
        get_url = (
            f"{target_url}{sep}{urllib.parse.urlencode({'country': country_code})}"
            if country_code
            else target_url
        )
        ok_get, body_get = _send_panel_http_request(
            get_url, api_key or "", method="GET"
        )
        if ok_get:
            ok, body = True, body_get
        else:
            return False, "", body[:200]

    phone = _extract_phone_from_text(body)
    if phone:
        return True, phone, body[:200]
    return False, "", body[:200] if body else "Panel theke number pawa jayni."


def fetch_sms_from_custom_panels(phone_number: str, country_code: str) -> str | None:
    for _, _, api_key, _, get_otp_url, status in list_api_panels():
        if str(status).upper() != "ACTIVE" or not get_otp_url:
            continue

        target_url = (
            get_otp_url.replace("{api_key}", api_key or "")
            .replace("{key}", api_key or "")
            .replace("{phone}", urllib.parse.quote(phone_number))
            .replace("{country}", urllib.parse.quote(country_code))
        )
        payload = {"phone": phone_number, "country": country_code}
        ok, body = _send_panel_http_request(
            target_url, api_key or "", method="POST", payload=payload
        )
        if not ok:
            sep = "&" if "?" in target_url else "?"
            get_url = f"{target_url}{sep}{urllib.parse.urlencode({'phone': phone_number, 'country': country_code})}"
            ok, body = _send_panel_http_request(get_url, api_key or "", method="GET")

        if ok:
            otp = _extract_otp_from_text(body)
            if otp:
                return otp
    return None


def call_voltex_api(extra_params: dict):
    api_url = get_setting("api_url", "")
    api_key = get_setting("api_key", "")
    if not api_url:
        return None, "Not Configured"

    params = dict(extra_params)
    if api_key:
        params.setdefault("key", api_key)
        params.setdefault("api_key", api_key)

    sep = "&" if "?" in api_url else "?"
    target_url = f"{api_url}{sep}{urllib.parse.urlencode(params)}"
    headers = {"User-Agent": "Mozilla/5.0 RSP-OTP-Bot/1.0"}
    if api_key:
        headers["mauthapi"] = api_key

    req = urllib.request.Request(target_url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            raw_data = resp.read().decode("utf-8", errors="ignore").strip()
            if not raw_data:
                return None, "Empty Response"
            try:
                parsed = json.loads(raw_data)
                if isinstance(parsed, dict):
                    if isinstance(parsed.get("data"), dict):
                        return parsed["data"], "Connected"
                    return parsed, "Connected"
            except json.JSONDecodeError:
                pass
            return {"raw_preview": raw_data[:120]}, "Connected"
    except urllib.error.HTTPError as exc:
        return None, f"HTTP {exc.code}"
    except Exception as exc:
        return None, f"Error: {str(exc)[:60]}"


def fetch_panel_traffic():
    return call_voltex_api({"action": "traffic"})


def fetch_sms_from_panel(phone_number: str, country_code: str):
    data, _ = call_voltex_api(
        {"action": "get_sms", "phone": phone_number, "country": country_code}
    )
    if isinstance(data, dict):
        for key in ("code", "otp", "sms", "message"):
            val = data.get(key)
            if val is not None and str(val).strip():
                return str(val).strip()
        if isinstance(data.get("data"), dict):
            for key in ("code", "otp", "sms", "message"):
                val = data["data"].get(key)
                if val is not None and str(val).strip():
                    return str(val).strip()
    return fetch_sms_from_custom_panels(phone_number, country_code)


def get_real_completed_order_counts() -> dict[str, int]:
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT country_code, COUNT(*) as cnt
        FROM orders
        WHERE status = 'COMPLETED'
        GROUP BY country_code
        """
    )
    counts = {str(row[0]): int(row[1]) for row in cursor.fetchall()}
    conn.close()
    return counts


def get_user_active_orders(user_id: int):
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT id, country_code, phone_number, created_at
        FROM orders
        WHERE user_id = ? AND status = 'ACTIVE'
        ORDER BY id DESC
        LIMIT 10
        """,
        (int(user_id),),
    )
    rows = cursor.fetchall()
    conn.close()
    return rows


def main_menu_keyboard(user_id: int) -> ReplyKeyboardMarkup:
    keyboard = [
        [KeyboardButton("Get Number"), KeyboardButton("Live Traffic")],
        [KeyboardButton("Wallet"), KeyboardButton("Leaderboard")],
        [KeyboardButton("Refer"), KeyboardButton("Withdraw")],
        [KeyboardButton("Support")],
    ]
    if is_admin(user_id):
        keyboard.append([KeyboardButton("Admin Panel")])
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)


def admin_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "🌍 Manage Countries", callback_data="admin_countries_menu"
                ),
                InlineKeyboardButton(
                    "💵 Set Country Rates", callback_data="admin_rates_menu"
                ),
            ],
            [
                InlineKeyboardButton(
                    "⚙️ System Settings", callback_data="admin_sys_settings"
                )
            ],
            [
                InlineKeyboardButton(
                    "🌐 API Panel Manager", callback_data="admin_api_panels"
                )
            ],
            [
                InlineKeyboardButton(
                    "📤 Stock & Numbers", callback_data="admin_stock_menu"
                )
            ],
            [
                InlineKeyboardButton(
                    "🎧 Support Mode", callback_data="admin_support_menu"
                )
            ],
            [
                InlineKeyboardButton(
                    "🎨 Edit Bot Texts", callback_data="admin_texts_menu"
                )
            ],
            [
                InlineKeyboardButton(
                    "📢 Broadcast Message", callback_data="admin_broadcast"
                )
            ],
            [
                InlineKeyboardButton(
                    "👑 Announce Today's Winner",
                    callback_data="admin_announce_winner",
                )
            ],
        ]
    )


def admin_system_settings_keyboard() -> InlineKeyboardMarkup:
    w_stat = get_setting("withdraw_status", "ON")
    min_w = get_setting("min_withdraw", "50.0")
    ref_r = get_setting("refer_reward", "0.2")
    cd = get_setting("cooldown", "10")
    n_req = get_setting("num_per_req", "3")
    n_shr = get_setting("num_per_share", "1")
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    f"💳 WITHDRAW: {w_stat}", callback_data="adm_tog_withdraw"
                )
            ],
            [
                InlineKeyboardButton(
                    f"💸 MIN W.: {min_w}", callback_data="adm_set_min_withdraw"
                ),
                InlineKeyboardButton(
                    f"🎁 REFER: {ref_r}", callback_data="adm_set_refer_reward"
                ),
            ],
            [
                InlineKeyboardButton(
                    f"⏳ COOLDOWN: {cd}s", callback_data="adm_set_cooldown"
                ),
                InlineKeyboardButton(
                    f"📞 NUM/REQ: {n_req}", callback_data="adm_set_num_per_req"
                ),
            ],
            [
                InlineKeyboardButton(
                    f"🔢 NUM/SHARE: {n_shr}", callback_data="adm_set_num_per_share"
                ),
                InlineKeyboardButton(
                    "🏦 W. METHODS", callback_data="adm_set_withdraw_methods"
                ),
            ],
            [InlineKeyboardButton("➕ Add Admin ID", callback_data="adm_add_admin")],
        ]
    )


def admin_rate_countries_keyboard() -> InlineKeyboardMarkup:
    countries = get_all_countries()
    rows = []
    for code, info in countries.items():
        rate = get_country_rate(code)
        rows.append(
            [
                InlineKeyboardButton(
                    f"{info['flag']} {info['name']} ({code}) — ৳{rate:.2f}",
                    callback_data=f"admin_setrate_{code}",
                )
            ]
        )
    return InlineKeyboardMarkup(rows)


def admin_manage_countries_keyboard() -> InlineKeyboardMarkup:
    countries = get_all_countries()
    rows = [
        [
            InlineKeyboardButton(
                "➕ Add New Country", callback_data="add_country_start"
            )
        ]
    ]
    for code, info in countries.items():
        rate = get_country_rate(code)
        if info.get("is_custom"):
            rows.append(
                [
                    InlineKeyboardButton(
                        f"🗑 Delete {info['flag']} {info['name']} ({code}) — ৳{rate:.2f}",
                        callback_data=f"del_country_{code}",
                    )
                ]
            )
    return InlineKeyboardMarkup(rows)


def number_card_keyboard(country_code: str) -> InlineKeyboardMarkup:
    rows = []

    stock_rows = get_number_stock_summary("unused")
    for c_name, cnt in stock_rows:
        rows.append(
            [
                InlineKeyboardButton(
                    f"📦 Stock {c_name} ({cnt})", callback_data=f"pickstock_{c_name}"
                )
            ]
        )

    for pid, pname, _, _, _, _ in list_api_panels():
        rows.append(
            [
                InlineKeyboardButton(
                    f"⚡ {pname} (Panel API)", callback_data=f"pickpanel_{pid}"
                )
            ]
        )

    if get_all_countries():
        rows.append(
            [
                InlineKeyboardButton(
                    "🔄 Refresh Stock", callback_data=f"change_{country_code}"
                ),
                InlineKeyboardButton("🌍 Country", callback_data="countries_menu"),
            ]
        )
    else:
        rows.append(
            [
                InlineKeyboardButton(
                    "🔄 Refresh Stock", callback_data="change_none"
                )
            ]
        )
    return InlineKeyboardMarkup(rows)


def countries_keyboard() -> InlineKeyboardMarkup:
    countries = get_all_countries()
    rows = []
    for code, info in countries.items():
        rate = get_country_rate(code)
        rows.append(
            [
                InlineKeyboardButton(
                    f"{info['flag']} {info['name']} ({info['dial']}) — ৳{rate:.2f}",
                    callback_data=f"setcountry_{code}",
                )
            ]
        )
    return InlineKeyboardMarkup(rows)


async def send_number_card(
    chat_id: int,
    user_id: int,
    context: ContextTypes.DEFAULT_TYPE,
    country_code: str | None = None,
) -> None:
    countries = get_all_countries()
    _, _, current_country = get_user_profile(user_id)
    candidate = country_code if country_code else current_country
    code = resolve_country_code(candidate)
    if code:
        set_user_country(user_id, code)

    now_str = datetime.datetime.now().strftime("%I:%M:%S %p")
    stock_rows = get_number_stock_summary("unused")
    panels = list_api_panels()

    active_orders = get_user_active_orders(user_id)
    active_note = ""
    if active_orders:
        active_items = [
            f"• Order `#{oid}`: `{ph}` ({resolve_country_code(cc).upper() or 'CUSTOM'})"
            for oid, cc, ph, _ in active_orders[:3]
        ]
        active_note = "\n\n⏳ **চলমান নম্বর (OTP অপেক্ষমাণ):**\n" + "\n".join(
            active_items
        )

    custom_header = get_setting("text_get_num", "")
    if not stock_rows and not panels:
        body_note = "❌ বর্তমানে স্টকে কোনো নম্বর বা কাস্টম প্যানেল নেই। অ্যাডমিন নম্বর আপলোড বা প্যানেল যুক্ত করলে এখানে দেখাবে।"
    else:
        body_note = (
            custom_header if custom_header else "নিচের স্টক বা প্যানেল থেকে নম্বর নিন:"
        )

    if code and code in countries:
        info = countries[code]
        rate = get_country_rate(code)
        header_block = (
            f"╔══════════════════════╗\n"
            f"  {info['flag']} **{info['name']} ({info['dial']})**\n"
            f"  📱 **{info['service']}** • 💵 **৳{rate:.2f}**\n"
            f"  🕒 **Updated:** `{now_str}`\n"
            f"╚══════════════════════╝\n"
        )
    else:
        header_block = (
            f"╔══════════════════════╗\n"
            f"  🌍 **No Country Added Yet**\n"
            f"  🕒 **Updated:** `{now_str}`\n"
            f"╚══════════════════════╝\n"
        )

    await context.bot.send_message(
        chat_id=chat_id,
        text=f"{header_block}{body_note}{active_note}",
        parse_mode="Markdown",
        reply_markup=number_card_keyboard(code),
    )


async def send_live_traffic(chat_id: int, context: ContextTypes.DEFAULT_TYPE) -> None:
    countries = get_all_countries()
    api_url = get_setting("api_url", "")
    api_key = get_setting("api_key", "")
    now_str = datetime.datetime.now().strftime("%I:%M:%S %p")

    panel_data, status_label = fetch_panel_traffic()
    completed_counts = get_real_completed_order_counts()
    custom_traffic_hdr = get_setting(
        "text_traffic", "📊 **Real-Time Stock & Order Stats**"
    )

    unused_rows = get_number_stock_summary("unused")
    used_rows = get_number_stock_summary("used")
    unused_total = sum(cnt for _, cnt in unused_rows)
    used_total = sum(cnt for _, cnt in used_rows)
    custom_panels_cnt = len(list_api_panels())

    lines = [
        custom_traffic_hdr,
        "━━━━━━━━━━━━━━━━━━━━━━",
        f"📦 **Unused Stock:** `{unused_total}` • **Used:** `{used_total}` • **Panels:** `{custom_panels_cnt}`",
        f"🔗 **API URL:** `{api_url if api_url else 'Not Set'}`",
        f"🔑 **API Key:** `{'Set' if api_key else 'Not Set'}`",
        f"📡 **API Status:** `{status_label}`",
        f"🕒 **Updated:** `{now_str}`",
    ]

    if countries:
        lines.append("")
        lines.append("**Country Rates & Completed OTPs:**")
        for code, info in countries.items():
            rate = get_country_rate(code)
            done_cnt = completed_counts.get(code, 0)
            api_stat = ""
            if (
                isinstance(panel_data, dict)
                and code in panel_data
                and isinstance(panel_data[code], dict)
            ):
                val = panel_data[code].get("count", panel_data[code].get("traffic"))
                if val is not None:
                    api_stat = f" • API: `{val}`"

            lines.append(
                f"• {info['flag']} **{info['name']} (`{info['dial']}`):** "
                f"৳`{rate:.2f}` • Completed OTPs: `{done_cnt}`{api_stat}"
            )
    else:
        lines.append("")
        lines.append("ℹ️ কোনো country যোগ করা হয়নি। অ্যাডমিন `/addcountry` বা Manage Countries থেকে যোগ করতে পারেন।")

    if unused_rows:
        lines.append("")
        lines.append("**Available Stock by Name:**")
        for c_name, cnt in unused_rows:
            lines.append(f"• `{c_name}`: `{cnt}` numbers")

    await context.bot.send_message(
        chat_id=chat_id,
        text="\n".join(lines),
        parse_mode="Markdown",
    )


async def broadcast_message(context: ContextTypes.DEFAULT_TYPE, text: str) -> int:
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM users")
    user_ids = [row[0] for row in cursor.fetchall()]
    conn.close()

    sent = 0
    for uid in user_ids:
        try:
            await context.bot.send_message(
                chat_id=int(uid), text=text, parse_mode="Markdown"
            )
            sent += 1
            await asyncio.sleep(0.05)
        except Exception:
            pass
    return sent


async def announce_daily_winner(
    context: ContextTypes.DEFAULT_TYPE, date_str: str | None = None
) -> tuple[bool, str]:
    target_date = date_str or datetime.datetime.now().strftime("%Y-%m-%d")
    top_users = get_daily_top_users(target_date, limit=1)
    if not top_users:
        return False, f"No OTP winner found for `{target_date}`."

    winner_id, w_fname, w_uname, w_cnt, w_earned = top_users[0]
    w_label = w_fname or (f"@{w_uname}" if w_uname else f"User {winner_id}")
    msg = (
        f"👑 **Daily OTP Winner ({target_date})!**\n\n"
        f"🏆 Winner: **{w_label}**\n"
        f"📩 Total OTPs Today: `{w_cnt}`\n"
        f"💰 Earned Today: `৳{float(w_earned):.2f}`"
    )
    sent = await broadcast_message(context, msg)
    return True, f"Winner announced to `{sent}` users for `{target_date}`."


async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not update.message:
        return
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ This command is for admin only.")
        return

    countries = get_all_countries()
    bot_name = get_setting("bot_name", "RSP OTP BOT")
    api_url = get_setting("api_url", "")
    api_key = get_setting("api_key", "")
    masked_key = (
        f"{api_key[:4]}***{api_key[-4:]}"
        if len(api_key) >= 8
        else ("Set" if api_key else "Not Set")
    )

    rate_lines = (
        [
            f"  • `{code}` ({info['name']}): **৳{get_country_rate(code):.2f}**"
            for code, info in countries.items()
        ]
        if countries
        else ["  • No country added yet"]
    )

    panel_text = (
        f"🛠️ **Admin Panel**\n\n"
        f"🤖 **Bot:** {bot_name}\n"
        f"🌍 **Total Countries:** `{len(countries)}`\n"
        f"🔗 **API URL:** `{api_url if api_url else 'Not Set'}`\n"
        f"🔑 **API Key:** `{masked_key}`\n"
        f"🌐 **Custom Panels:** `{len(list_api_panels())}`\n"
        f"🎧 **Support Mode:** `{get_setting('support_mode', 'LIVE')}`\n\n"
        f"💵 **Country Rates:**\n"
        + "\n".join(rate_lines)
        + "\n\n"
        f"**Commands:**\n"
        f"• `/sendotp <order_id> <code>`\n"
        f"• `/addcountry <code> <name> <dial> <rate> [flag] [service]`\n"
        f"• `/setrate <code> <amount>`\n"
        f"• `/setname <Bot Name>`\n"
        f"• `/setapiurl <https://...>`\n"
        f"• `/setapikey <API_KEY>`\n"
        f"• `/setsupport <@username>`"
    )
    await update.message.reply_text(
        panel_text, parse_mode="Markdown", reply_markup=admin_menu_keyboard()
    )


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not update.effective_chat or not update.message:
        return

    user = update.effective_user
    ref_id = None
    if context.args and context.args[0].isdigit():
        ref_id = int(context.args[0])

    is_new_user = ensure_user(
        user.id,
        user.username or "",
        user.full_name or "",
        referred_by=ref_id,
    )
    if is_new_user and ref_id and ref_id != user.id:
        ref_bonus = float(get_setting("refer_reward", "0.2") or 0.2)
        add_user_balance(ref_id, ref_bonus)
        try:
            await context.bot.send_message(
                chat_id=ref_id,
                text=f"🎉 নতুন রেফার সফল হয়েছে! আপনার ব্যালেন্সে `৳{ref_bonus:.2f}` যোগ হয়েছে।",
                parse_mode="Markdown",
            )
        except Exception:
            pass

    context.user_data.clear()
    countries = get_all_countries()
    bot_name = get_setting("bot_name", "RSP OTP BOT")
    balance, otp_count, country_code = get_user_profile(user.id)
    custom_start = get_setting("text_start", "")

    if custom_start:
        welcome_text = custom_start
    else:
        if country_code and country_code in countries:
            c_info = countries[country_code]
            c_line = f"🌍 **Selected Country:** {c_info['flag']} {c_info['name']} (`{c_info['dial']}`)"
        else:
            c_line = "🌍 **Selected Country:** Not Added Yet"
        welcome_text = (
            f"👋 Welcome to **{bot_name}**!\n\n"
            f"📊 **OTPs:** `{otp_count}` • 💰 **Balance:** `৳{balance:.2f}`\n"
            f"{c_line}"
        )
    await update.message.reply_text(
        welcome_text,
        parse_mode="Markdown",
        reply_markup=main_menu_keyboard(user.id),
    )


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not update.message or not update.message.document:
        return
    user = update.effective_user
    if not is_admin(user.id):
        return

    country_target = context.user_data.pop("awaiting_upload_numbers_for", None)
    if not country_target:
        country_target = context.user_data.pop("awaiting_upload_country", None)

    if country_target:
        tg_file = await context.bot.get_file(update.message.document.file_id)
        file_bytes = await tg_file.download_as_bytearray()
        raw_lines = file_bytes.decode("utf-8", errors="ignore").splitlines()
        added = upload_manual_numbers(country_target, raw_lines)
        await update.message.reply_text(
            f"✅ **{country_target}** এর জন্য `{added}` টি নতুন নম্বর ফাইল থেকে স্টকে যুক্ত হয়েছে।",
            parse_mode="Markdown",
        )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if (
        not update.message
        or not update.message.text
        or not update.effective_user
        or not update.effective_chat
    ):
        return

    user = update.effective_user
    ensure_user(user.id, user.username or "", user.full_name or "")
    text = update.message.text.strip()

    if is_admin(user.id) and update.message.reply_to_message:
        orig_txt = update.message.reply_to_message.text or ""
        if "User ID:" in orig_txt:
            try:
                target_str = (
                    orig_txt.split("User ID:", 1)[1].strip().split()[0].strip("`*")
                )
                target_uid = int(target_str)
                await context.bot.send_message(
                    chat_id=target_uid,
                    text=f"🎧 **Admin Reply:**\n\n{text}",
                    parse_mode="Markdown",
                )
                await update.message.reply_text("✅ ইউজারকে রিপ্লাই পাঠানো হয়েছে।")
                return
            except Exception:
                pass

    if is_admin(user.id):
        country_step = context.user_data.get("country_add_step")
        if country_step == "code":
            cleaned_code = re.sub(r"[^a-z0-9_]", "", text.lower())
            if not cleaned_code:
                await update.message.reply_text(
                    "⚠️ সঠিক short code দিন (যেমন: `be`, `de`, `ca`):"
                )
                return
            context.user_data["country_draft"] = {"code": cleaned_code}
            context.user_data["country_add_step"] = "name"
            await update.message.reply_text(
                "2️⃣ দেশের নাম লিখুন (যেমন: `Belgium`):", parse_mode="Markdown"
            )
            return
        elif country_step == "name":
            context.user_data["country_draft"]["name"] = text
            context.user_data["country_add_step"] = "dial"
            await update.message.reply_text(
                "3️⃣ Dial Code লিখুন (যেমন: `+32`):", parse_mode="Markdown"
            )
            return
        elif country_step == "dial":
            context.user_data["country_draft"]["dial"] = text
            context.user_data["country_add_step"] = "rate"
            await update.message.reply_text(
                "4️⃣ প্রতি OTP-তে কত টাকা (৳) দিবেন? সংখ্যা লিখুন (যেমন: `18.00`):",
                parse_mode="Markdown",
            )
            return
        elif country_step == "rate":
            try:
                rate_val = float(text)
                if rate_val < 0:
                    raise ValueError()
            except ValueError:
                await update.message.reply_text("⚠️ সঠিক rate লিখুন (যেমন: `18.00`):")
                return
            context.user_data["country_draft"]["rate"] = rate_val
            context.user_data["country_add_step"] = "flag_service"
            await update.message.reply_text(
                "5️⃣ Flag এবং Service লিখুন (যেমন: `🇧🇪 Telegram` অথবা `skip` লিখুন):",
                parse_mode="Markdown",
            )
            return
        elif country_step == "flag_service":
            draft = context.user_data.pop("country_draft", {})
            context.user_data.pop("country_add_step", None)
            flag_val = "🌍"
            service_val = "OTP"
            if text.lower() != "skip" and text:
                parts = text.split(maxsplit=1)
                if len(parts) == 2:
                    flag_val, service_val = parts[0], parts[1]
                elif len(parts) == 1:
                    if any(ord(ch) > 127 for ch in parts[0]):
                        flag_val = parts[0]
                    else:
                        service_val = parts[0]

            ok, res = add_or_update_country(
                country_code=draft.get("code", ""),
                name=draft.get("name", ""),
                dial=draft.get("dial", "+1"),
                rate=draft.get("rate", 15.0),
                flag=flag_val,
                service=service_val,
            )
            if ok:
                await update.message.reply_text(
                    f"🎉 **{draft.get('name')}** (`{res}`) সফলভাবে যুক্ত হয়েছে! Rate: `৳{float(draft.get('rate', 15.0)):.2f}`",
                    parse_mode="Markdown",
                )
            else:
                await update.message.reply_text(f"⚠️ {res}")
            return

        if context.user_data.get("awaiting_rate_country"):
            target_code = context.user_data.pop("awaiting_rate_country")
            countries = get_all_countries()
            try:
                amount = float(text)
                if amount < 0:
                    raise ValueError()
                set_country_rate(target_code, amount)
                info = countries.get(target_code, {"flag": "🌍", "name": target_code})
                await update.message.reply_text(
                    f"✅ Rate set for {info['flag']} **{info['name']}** (`{target_code}`): **৳{amount:.2f}**",
                    parse_mode="Markdown",
                )
                return
            except ValueError:
                context.user_data["awaiting_rate_country"] = target_code
                await update.message.reply_text(
                    "⚠️ Invalid amount. Shudhu number likhun (e.g. `18.50`).",
                    parse_mode="Markdown",
                )
                return

        if context.user_data.get("awaiting_setting_key"):
            s_key = context.user_data.pop("awaiting_setting_key")
            val_to_save = "" if text.lower() == "clear" else text
            set_setting(s_key, val_to_save)
            await update.message.reply_text(
                f"✅ `{s_key}` আপডেট হয়ে `{val_to_save or '(empty)'}` সেট হয়েছে।",
                parse_mode="Markdown",
            )
            return

        if context.user_data.get("awaiting_new_admin"):
            context.user_data.pop("awaiting_new_admin")
            if text.isdigit():
                add_admin_id(int(text))
                await update.message.reply_text(
                    f"✅ `{text}` কে নতুন অ্যাডমিন করা হয়েছে।",
                    parse_mode="Markdown",
                )
            else:
                await update.message.reply_text("⚠️ সঠিক numeric User ID দিন।")
            return

        if context.user_data.get("awaiting_broadcast"):
            context.user_data.pop("awaiting_broadcast")
            sent = await broadcast_message(context, f"📢 **Notice:**\n\n{text}")
            await update.message.reply_text(
                f"✅ `{sent}` জন ইউজারকে ব্রডকাস্ট মেসেজ পাঠানো হয়েছে।",
                parse_mode="Markdown",
            )
            return

        if context.user_data.get("awaiting_upload_country"):
            context.user_data.pop("awaiting_upload_country")
            c_name = text.strip() or "General"
            context.user_data["awaiting_upload_numbers_for"] = c_name
            await update.message.reply_text(
                f"📥 **{c_name}** এর নম্বরগুলো নিচে পেস্ট করুন (প্রতি লাইনে একটি নম্বর) অথবা `.txt` ফাইল পাঠান:",
                parse_mode="Markdown",
            )
            return

        if context.user_data.get("awaiting_upload_numbers_for"):
            c_name = context.user_data.pop("awaiting_upload_numbers_for")
            added = upload_manual_numbers(c_name, text.splitlines())
            await update.message.reply_text(
                f"✅ **{c_name}** এর জন্য `{added}` টি নতুন নম্বর স্টকে যুক্ত হয়েছে।",
                parse_mode="Markdown",
            )
            return

        panel_step = context.user_data.get("panel_add_step")
        if panel_step == "name":
            context.user_data["panel_draft"] = {"name": text}
            context.user_data["panel_add_step"] = "key"
            await update.message.reply_text(
                "2️⃣ প্যানেলের **API Key** লিখুন:", parse_mode="Markdown"
            )
            return
        elif panel_step == "key":
            context.user_data["panel_draft"]["key"] = text
            context.user_data["panel_add_step"] = "num_url"
            await update.message.reply_text(
                "3️⃣ **Get Number API URL** লিখুন (`{api_key}`, `{country}` ব্যবহার করতে পারেন):",
                parse_mode="Markdown",
            )
            return
        elif panel_step == "num_url":
            context.user_data["panel_draft"]["num_url"] = text
            context.user_data["panel_add_step"] = "otp_url"
            await update.message.reply_text(
                "4️⃣ **Get OTP URL** লিখুন (`{phone}`, `{country}`, `{api_key}` ব্যবহার করতে পারেন) অথবা `skip` লিখুন:",
                parse_mode="Markdown",
            )
            return
        elif panel_step == "otp_url":
            draft = context.user_data.pop("panel_draft", {})
            context.user_data.pop("panel_add_step", None)
            otp_url = "" if text.lower() == "skip" else text
            add_api_panel(
                draft.get("name", "Custom Panel"),
                draft.get("key", ""),
                draft.get("num_url", ""),
                otp_url,
            )
            await update.message.reply_text(
                f"🎉 **{draft.get('name', 'Custom Panel')}** প্যানেলটি যুক্ত হয়েছে!",
                parse_mode="Markdown",
            )
            return

    if context.user_data.get("awaiting_wd_method"):
        method = context.user_data.pop("awaiting_wd_method")
        bal = deduct_all_user_balance(user.id)
        if bal <= 0:
            await update.message.reply_text("⚠️ আপনার অ্যাকাউন্টে ব্যালেন্স নেই।")
            return
        await update.message.reply_text(
            "✅ আপনার উইথড্র রিকোয়েস্ট অ্যাডমিনের কাছে পাঠানো হয়েছে।"
        )
        for aid in get_all_admin_ids():
            try:
                await context.bot.send_message(
                    chat_id=aid,
                    text=(
                        f"💸 **New Withdrawal Request**\n\n"
                        f"• User ID: `{user.id}`\n"
                        f"• User: {user.full_name} (@{user.username or 'N/A'})\n"
                        f"• Method: **{method}**\n"
                        f"• Amount: `৳{bal:.2f}`\n"
                        f"• Details: `{text}`"
                    ),
                    parse_mode="Markdown",
                )
            except Exception:
                pass
        return

    if context.user_data.get("in_live_support"):
        for aid in get_all_admin_ids():
            try:
                await context.bot.send_message(
                    chat_id=aid,
                    text=(
                        f"📩 **Live Support Message**\n"
                        f"User ID: `{user.id}` (@{user.username or 'N/A'})\n\n"
                        f"💬 {text}\n\n"
                        f"*রিপ্লাই দিতে এই মেসেজটি Reply করে লিখুন।*"
                    ),
                    parse_mode="Markdown",
                )
            except Exception:
                pass
        await update.message.reply_text(
            "✅ আপনার মেসেজ সাপোর্ট টিমের কাছে পাঠানো হয়েছে।"
        )
        return

    if text in ("Get Number", "🟢 নম্বর নিন", "📱 নম্বর নিন"):
        await send_number_card(update.effective_chat.id, user.id, context)
    elif text in ("Live Traffic", "🔵 স্টক দেখুন", "📦 স্টক দেখুন"):
        await send_live_traffic(update.effective_chat.id, context)
    elif text in ("Wallet", "🟡 ব্যালেন্স", "💰 ব্যালেন্স"):
        balance, otp_count, _ = get_user_profile(user.id)
        await update.message.reply_text(
            f"💰 **Wallet**\n\n"
            f"• **Balance:** `৳{balance:.2f}`\n"
            f"• **Total OTPs:** `{otp_count}`",
            parse_mode="Markdown",
        )
    elif text in ("Refer", "🎁 REFER"):
        bot_info = await context.bot.get_me()
        ref_link = f"https://t.me/{bot_info.username}?start={user.id}"
        ref_reward = get_setting("refer_reward", "0.2")
        tpl = get_setting(
            "text_refer",
            "🎁 **Refer & Earn**\nReward per refer: `৳{refer_reward}`\nLink: `{refer_link}`",
        )
        await update.message.reply_text(
            tpl.format(refer_reward=ref_reward, refer_link=ref_link),
            parse_mode="Markdown",
        )
    elif text in ("Withdraw", "💳 WITHDRAWAL"):
        if get_setting("withdraw_status", "ON") != "ON":
            await update.message.reply_text("⚠️ বর্তমানে উইথড্র সাময়িকভাবে বন্ধ আছে।")
            return
        balance, _, _ = get_user_profile(user.id)
        min_w = float(get_setting("min_withdraw", "50.0") or 50.0)
        tpl = get_setting(
            "text_withdraw",
            "💳 **Withdraw**\nMin: `৳{min_withdraw}`\nBalance: `৳{balance}`",
        )
        msg_txt = tpl.format(min_withdraw=f"{min_w:.2f}", balance=f"{balance:.2f}")
        if balance < min_w:
            await update.message.reply_text(
                f"{msg_txt}\n\n❌ আপনার পর্যাপ্ত ব্যালেন্স নেই।",
                parse_mode="Markdown",
            )
            return
        methods = [
            m.strip()
            for m in get_setting("withdraw_methods", "Bkash,Nagad,Binance").split(",")
            if m.strip()
        ]
        kb = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton(f"💳 {m}", callback_data=f"wdmeth_{m}")]
                for m in methods
            ]
        )
        await update.message.reply_text(
            f"{msg_txt}\n\nপেমেন্ট মেথড সিলেক্ট করুন:",
            parse_mode="Markdown",
            reply_markup=kb,
        )
    elif text in ("Leaderboard", "🟣 লিডারবোর্ড"):
        today_str = datetime.datetime.now().strftime("%Y-%m-%d")
        top_users = get_daily_top_users(today_str, limit=10)

        lines = [f"🏆 **Daily OTP Leaderboard ({today_str})**", ""]
        if not top_users:
            lines.append("No completed OTPs recorded today yet.")
        else:
            winner_id, w_fname, w_uname, w_cnt, _ = top_users[0]
            w_label = w_fname or (f"@{w_uname}" if w_uname else f"User {winner_id}")
            lines.append(
                f"👑 **Current Top User:** **{w_label}** (`{w_cnt}` OTPs)\n"
            )
            for idx, (_, fname, uname, count_val, earned_val) in enumerate(
                top_users, start=1
            ):
                label = fname or (f"@{uname}" if uname else "User")
                lines.append(
                    f"{idx}. **{label}** — `{count_val}` OTPs (`৳{float(earned_val):.2f}`)"
                )
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
    elif text in ("Support", "🟠 সাপোর্ট", "🆘 সাপোর্ট"):
        mode = get_setting("support_mode", "LIVE").upper()
        link = get_setting("support_link", "https://t.me/reyad0045")
        support_txt = get_setting("text_support", "🎧 **Support Center**")
        buttons = []
        if mode in ("NORMAL", "BOTH") and link.startswith("http"):
            buttons.append([InlineKeyboardButton("📞 Contact Support", url=link)])
        if mode in ("LIVE", "BOTH"):
            buttons.append(
                [
                    InlineKeyboardButton(
                        "💬 Start Live Chat", callback_data="user_live_chat"
                    )
                ]
            )
        if mode == "LIVE" and not buttons:
            context.user_data["in_live_support"] = True
            await update.message.reply_text(
                "💬 **Live Chat চালু হয়েছে!** আপনার সমস্যা লিখে পাঠান:",
                parse_mode="Markdown",
            )
            return
        await update.message.reply_text(
            support_txt,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(buttons) if buttons else None,
        )
    elif text in ("Admin Panel", "🔴 অ্যাডমিন প্যানেল"):
        await admin_panel(update, context)
    else:
        await update.message.reply_text(
            "ℹ️ Please choose an option from the menu below or send `/start`.",
            parse_mode="Markdown",
            reply_markup=main_menu_keyboard(user.id),
        )


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data or not query.from_user or not query.message:
        return

    await query.answer()
    user = query.from_user
    ensure_user(user.id, user.username or "", user.full_name or "")
    data = query.data
    countries = get_all_countries()

    if data == "user_live_chat":
        context.user_data["in_live_support"] = True
        await query.message.reply_text(
            "💬 **Live Chat চালু হয়েছে!** আপনার মেসেজ লিখে পাঠান:",
            parse_mode="Markdown",
        )
        return

    if data.startswith("wdmeth_"):
        method = data.replace("wdmeth_", "", 1)
        context.user_data["awaiting_wd_method"] = method
        await query.message.reply_text(
            f"📝 আপনার **{method}** অ্যাকাউন্ট নম্বর ও পরিমাণ লিখে পাঠান:",
            parse_mode="Markdown",
        )
        return

    if data == "admin_countries_menu":
        if not is_admin(user.id):
            return
        await query.message.reply_text(
            "🌍 **Manage Countries** (নতুন দেশ যোগ করতে নিচের বাটনে চাপ দিন):",
            parse_mode="Markdown",
            reply_markup=admin_manage_countries_keyboard(),
        )

    elif data == "add_country_start":
        if not is_admin(user.id):
            return
        context.user_data["country_add_step"] = "code"
        await query.message.reply_text(
            "1️⃣ দেশের short code লিখুন (যেমন: `be`, `de`, `ca`, `fr`):",
            parse_mode="Markdown",
        )

    elif data.startswith("del_country_"):
        if not is_admin(user.id):
            return
        c_code = data.replace("del_country_", "", 1)
        if delete_custom_country(c_code):
            await query.message.reply_text(
                f"✅ `{c_code}` দেশটি ডিলিট করা হয়েছে।", parse_mode="Markdown"
            )
        else:
            await query.message.reply_text("⚠️ ডিফল্ট দেশ ডিলিট করা যাবে না।")

    elif data == "admin_rates_menu":
        if not is_admin(user.id):
            return
        if not countries:
            await query.message.reply_text("⚠️ এখনো কোনো country যোগ করা হয়নি।")
            return
        await query.message.reply_text(
            "💵 **Select a country to set rate (৳):**",
            parse_mode="Markdown",
            reply_markup=admin_rate_countries_keyboard(),
        )

    elif data.startswith("admin_setrate_"):
        if not is_admin(user.id):
            return
        c_code = data.replace("admin_setrate_", "", 1)
        if c_code in countries:
            context.user_data["awaiting_rate_country"] = c_code
            info = countries[c_code]
            curr = get_country_rate(c_code)
            await query.message.reply_text(
                f"💵 {info['flag']} **{info['name']}** (`{c_code}`) current rate: `৳{curr:.2f}`\n"
                f"Notun rate (e.g. `17.50`) type kore send korun:",
                parse_mode="Markdown",
            )

    elif data == "admin_sys_settings":
        if not is_admin(user.id):
            return
        await query.message.reply_text(
            "⚙️ **System Control Settings**",
            parse_mode="Markdown",
            reply_markup=admin_system_settings_keyboard(),
        )

    elif data == "adm_tog_withdraw":
        if not is_admin(user.id):
            return
        curr = get_setting("withdraw_status", "ON")
        set_setting("withdraw_status", "OFF" if curr == "ON" else "ON")
        await query.message.edit_reply_markup(
            reply_markup=admin_system_settings_keyboard()
        )

    elif data.startswith("adm_set_"):
        if not is_admin(user.id):
            return
        s_key = data.replace("adm_set_", "", 1)
        context.user_data["awaiting_setting_key"] = s_key
        await query.message.reply_text(
            f"✏️ `{s_key}` এর নতুন মান লিখে পাঠান:", parse_mode="Markdown"
        )

    elif data == "adm_add_admin":
        if not is_admin(user.id):
            return
        context.user_data["awaiting_new_admin"] = True
        await query.message.reply_text("👤 নতুন অ্যাডমিনের Numeric User ID পাঠান:")

    elif data == "admin_api_panels":
        if not is_admin(user.id):
            return
        panels = list_api_panels()
        rows = [
            [
                InlineKeyboardButton(
                    "➕ Add New Panel API", callback_data="panel_add_start"
                )
            ]
        ]
        for pid, pname, _, _, _, st in panels:
            rows.append(
                [
                    InlineKeyboardButton(
                        f"🗑 Delete {pname} ({st})", callback_data=f"panel_del_{pid}"
                    )
                ]
            )
        await query.message.reply_text(
            f"🌐 **API Panel Manager** (`{len(panels)}` panels)",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(rows),
        )

    elif data == "panel_add_start":
        if not is_admin(user.id):
            return
        context.user_data["panel_add_step"] = "name"
        await query.message.reply_text(
            "1️⃣ প্যানেলের নাম লিখুন (যেমন: `Voltx` বা `5sim`):", parse_mode="Markdown"
        )

    elif data.startswith("panel_del_"):
        if not is_admin(user.id):
            return
        pid = int(data.replace("panel_del_", "", 1))
        delete_api_panel(pid)
        await query.message.reply_text("✅ প্যানেলটি ডিলিট করা হয়েছে।")

    elif data == "admin_stock_menu":
        if not is_admin(user.id):
            return
        unused_rows = get_number_stock_summary("unused")
        used_rows = get_number_stock_summary("used")
        u_txt = ", ".join(f"{c}: {n}" for c, n in unused_rows) or "None"
        d_txt = ", ".join(f"{c}: {n}" for c, n in used_rows) or "None"
        kb = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "📤 Upload Numbers", callback_data="stock_upload_start"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "🗑 Delete All Stock", callback_data="stock_clear_all"
                    )
                ],
            ]
        )
        await query.message.reply_text(
            f"📦 **Number Stock Summary**\n\n• Unused: `{u_txt}`\n• Used: `{d_txt}`",
            parse_mode="Markdown",
            reply_markup=kb,
        )

    elif data == "stock_upload_start":
        if not is_admin(user.id):
            return
        context.user_data["awaiting_upload_country"] = True
        await query.message.reply_text(
            "🌍 কোন দেশের নম্বর আপলোড করবেন? দেশের নাম লিখুন:"
        )

    elif data == "stock_clear_all":
        if not is_admin(user.id):
            return
        clear_all_manual_numbers()
        await query.message.reply_text("🗑 সব ম্যানুয়াল নম্বর স্টক মুছে ফেলা হয়েছে।")

    elif data == "admin_support_menu":
        if not is_admin(user.id):
            return
        mode = get_setting("support_mode", "LIVE").upper()
        kb = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        f"Normal {'✅' if mode=='NORMAL' else ''}",
                        callback_data="supmode_NORMAL",
                    ),
                    InlineKeyboardButton(
                        f"Live {'✅' if mode=='LIVE' else ''}",
                        callback_data="supmode_LIVE",
                    ),
                    InlineKeyboardButton(
                        f"Both {'✅' if mode=='BOTH' else ''}",
                        callback_data="supmode_BOTH",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        "🔗 Set Support Link", callback_data="adm_set_support_link"
                    )
                ],
            ]
        )
        await query.message.reply_text(
            f"🎧 **Support System** — Mode: `{mode}`",
            parse_mode="Markdown",
            reply_markup=kb,
        )

    elif data.startswith("supmode_"):
        if not is_admin(user.id):
            return
        new_m = data.replace("supmode_", "", 1)
        set_setting("support_mode", new_m)
        await query.message.reply_text(
            f"✅ Support Mode set to `{new_m}`.", parse_mode="Markdown"
        )

    elif data == "admin_texts_menu":
        if not is_admin(user.id):
            return
        kb = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "✏️ /start Text", callback_data="adm_set_text_start"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "📱 Get Number Text", callback_data="adm_set_text_get_num"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "📊 Traffic Text", callback_data="adm_set_text_traffic"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "🎁 Refer Text", callback_data="adm_set_text_refer"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "💳 Withdraw Text", callback_data="adm_set_text_withdraw"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "🎧 Support Text", callback_data="adm_set_text_support"
                    )
                ],
            ]
        )
        await query.message.reply_text(
            "🎨 **Edit Bot Texts** (খালি রাখতে `clear` লিখুন):",
            parse_mode="Markdown",
            reply_markup=kb,
        )

    elif data == "admin_broadcast":
        if not is_admin(user.id):
            return
        context.user_data["awaiting_broadcast"] = True
        await query.message.reply_text("📢 সব ইউজারকে পাঠানোর মেসেজটি লিখুন:")

    elif data == "admin_announce_winner":
        if not is_admin(user.id):
            return
        _, status_msg = await announce_daily_winner(context)
        await query.message.reply_text(status_msg, parse_mode="Markdown")

    elif data.startswith("pickstock_"):
        c_name = data.replace("pickstock_", "", 1)
        picked = pick_manual_number(c_name, user.id)
        if not picked:
            await query.message.reply_text("❌ এই স্টকের নম্বর শেষ হয়ে গেছে।")
            return
        _, phone_num = picked
        matched_code = resolve_country_code(c_name)
        if matched_code and matched_code in countries:
            c_info = countries[matched_code]
            country_line = f"🌍 **Country:** {c_info['flag']} {c_info['name']} (`{c_info['dial']}`)\n"
        else:
            country_line = ""
        rate = get_country_rate(matched_code)
        order_id = create_active_order(user.id, matched_code, phone_num)
        await query.message.reply_text(
            f"✅ **Stock Number Assigned!**\n\n"
            f"🧾 **Order ID:** `#{order_id}`\n"
            f"{country_line}"
            f"📦 **Stock:** **{c_name}**\n"
            f"📱 **Phone:** `{phone_num}`\n"
            f"💵 **Rate:** `৳{rate:.2f}`\n"
            f"⏳ *এই নম্বরের OTP কোডের জন্য অপেক্ষা করুন।*",
            parse_mode="Markdown",
        )

    elif data.startswith("pickpanel_"):
        pid = int(data.replace("pickpanel_", "", 1))
        panel = get_api_panel(pid)
        if not panel:
            await query.message.reply_text("❌ Panel পাওয়া যায়নি।")
            return
        _, _, active_code = get_user_profile(user.id)
        ok, phone_num, resp_text = call_custom_panel_get_number(panel, active_code)
        if not ok or not phone_num:
            await query.message.reply_text(
                f"❌ **{panel[1]} Panel Error:**\n`{resp_text}`",
                parse_mode="Markdown",
            )
            return
        detected_code = (
            resolve_country_code(phone_num) if phone_num.startswith("+") else active_code
        )
        if detected_code and detected_code in countries:
            c_info = countries[detected_code]
            country_line = f"🌍 **Country:** {c_info['flag']} {c_info['name']} (`{c_info['dial']}`)\n"
        else:
            country_line = ""
        rate = get_country_rate(detected_code)
        order_id = create_active_order(user.id, detected_code, phone_num)
        await query.message.reply_text(
            f"✅ **Panel Number Assigned!**\n\n"
            f"🧾 **Order ID:** `#{order_id}`\n"
            f"{country_line}"
            f"⚡ **Panel:** **{panel[1]}**\n"
            f"📱 **Phone:** `{phone_num}`\n"
            f"💵 **Rate:** `৳{rate:.2f}`\n"
            f"⏳ *এই নম্বরের OTP কোডের জন্য অপেক্ষা করুন।*",
            parse_mode="Markdown",
        )

    elif data.startswith("change_"):
        raw_c = data.replace("change_", "", 1)
        c_code = "" if raw_c == "none" else resolve_country_code(raw_c)
        await send_number_card(
            query.message.chat_id, user.id, context, country_code=c_code
        )

    elif data == "countries_menu":
        if not countries:
            await query.message.reply_text("⚠️ এখনো কোনো country যোগ করা হয়নি।")
            return
        await query.message.reply_text(
            "🌍 **Select Country & Dial Code:**",
            parse_mode="Markdown",
            reply_markup=countries_keyboard(),
        )

    elif data.startswith("setcountry_"):
        c_code = resolve_country_code(data.replace("setcountry_", "", 1))
        if c_code in countries:
            set_user_country(user.id, c_code)
            info = countries[c_code]
            rate = get_country_rate(c_code)
            await query.message.reply_text(
                f"✅ Country set to {info['flag']} **{info['name']} (`{info['dial']}`)** — **৳{rate:.2f}**.",
                parse_mode="Markdown",
            )
            await send_number_card(
                query.message.chat_id, user.id, context, country_code=c_code
            )


async def send_otp_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if (
        not update.effective_user
        or not update.message
        or not is_admin(update.effective_user.id)
    ):
        return
    if len(context.args) < 2 or not context.args[0].isdigit():
        await update.message.reply_text(
            "Usage: `/sendotp <order_id> <otp_code>`", parse_mode="Markdown"
        )
        return

    order_id = int(context.args[0])
    otp_code = " ".join(context.args[1:]).strip()
    countries = get_all_countries()

    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT user_id, country_code, phone_number, status FROM orders WHERE id = ?",
        (order_id,),
    )
    row = cursor.fetchone()
    conn.close()

    if not row:
        await update.message.reply_text(
            f"❌ Order `#{order_id}` পাওয়া যায়নি।", parse_mode="Markdown"
        )
        return

    user_id, c_code, phone_number, status = row
    if status != "ACTIVE":
        await update.message.reply_text(
            f"⚠️ Order `#{order_id}` already `{status}`.", parse_mode="Markdown"
        )
        return

    resolved_code = resolve_country_code(c_code)
    reward = complete_order_and_reward(order_id, int(user_id), resolved_code, otp_code)
    if resolved_code and resolved_code in countries:
        c_info = countries[resolved_code]
        country_line = f"🌍 **Country:** {c_info['flag']} {c_info['name']}\n"
    else:
        country_line = ""

    try:
        await context.bot.send_message(
            chat_id=int(user_id),
            text=(
                f"📩 **OTP Received!**\n\n"
                f"🧾 **Order ID:** `#{order_id}`\n"
                f"{country_line}"
                f"📱 **Phone:** `{phone_number}`\n"
                f"🔐 **Code:** `{otp_code}`\n"
                f"💵 **Earned:** `+৳{reward:.2f}`"
            ),
            parse_mode="Markdown",
        )
        await update.message.reply_text(
            f"✅ Order `#{order_id}` (`{phone_number}`)-এ OTP `{otp_code}` পাঠানো হয়েছে (`+৳{reward:.2f}`)।",
            parse_mode="Markdown",
        )
    except Exception as exc:
        await update.message.reply_text(
            f"⚠️ মেসেজ পাঠাতে সমস্যা: `{exc}`", parse_mode="Markdown"
        )


async def add_country_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if (
        not update.effective_user
        or not update.message
        or not is_admin(update.effective_user.id)
    ):
        return
    if len(context.args) < 4:
        await update.message.reply_text(
            "Usage: `/addcountry <code> <name> <dial> <rate> [flag] [service]`\n"
            "Example: `/addcountry be Belgium +32 18.00 🇧🇪 Telegram`",
            parse_mode="Markdown",
        )
        return
    c_code = context.args[0].strip()
    c_name = context.args[1].strip()
    c_dial = context.args[2].strip()
    try:
        c_rate = float(context.args[3].strip())
        if c_rate < 0:
            raise ValueError()
    except ValueError:
        await update.message.reply_text("⚠️ Rate must be a valid non-negative number.")
        return
    c_flag = context.args[4].strip() if len(context.args) >= 5 else "🌍"
    c_service = " ".join(context.args[5:]).strip() if len(context.args) >= 6 else "OTP"

    ok, res = add_or_update_country(
        country_code=c_code,
        name=c_name,
        dial=c_dial,
        rate=c_rate,
        flag=c_flag,
        service=c_service,
    )
    if ok:
        await update.message.reply_text(
            f"✅ Country **{c_name}** (`{res}`) added/updated — Rate: **৳{c_rate:.2f}**",
            parse_mode="Markdown",
        )
    else:
        await update.message.reply_text(f"⚠️ {res}")


async def set_rate_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if (
        not update.effective_user
        or not update.message
        or not is_admin(update.effective_user.id)
    ):
        return
    countries = get_all_countries()
    if len(context.args) < 2:
        avail = ", ".join(f"`{k}`" for k in countries.keys()) or "None"
        await update.message.reply_text(
            f"Usage: `/setrate <code> <amount>`\nAvailable: {avail}",
            parse_mode="Markdown",
        )
        return
    c_code = context.args[0].strip().lower()
    if c_code not in countries:
        avail = ", ".join(f"`{k}`" for k in countries.keys()) or "None"
        await update.message.reply_text(
            f"Invalid country code. Available: {avail}",
            parse_mode="Markdown",
        )
        return
    try:
        amount = float(context.args[1].strip())
        if amount < 0:
            raise ValueError()
    except ValueError:
        await update.message.reply_text("Amount must be a valid non-negative number.")
        return

    set_country_rate(c_code, amount)
    info = countries[c_code]
    await update.message.reply_text(
        f"✅ Rate updated for {info['flag']} **{info['name']}** (`{c_code}`): **৳{amount:.2f}**",
        parse_mode="Markdown",
    )


async def set_bot_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if (
        not update.effective_user
        or not update.message
        or not is_admin(update.effective_user.id)
    ):
        return
    new_name = " ".join(context.args).strip()
    if not new_name:
        await update.message.reply_text(
            "Usage: `/setname RSP OTP BOT`", parse_mode="Markdown"
        )
        return
    set_setting("bot_name", new_name)
    await update.message.reply_text(
        f"✅ Bot name updated to **{new_name}**.", parse_mode="Markdown"
    )


async def set_api_url(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if (
        not update.effective_user
        or not update.message
        or not is_admin(update.effective_user.id)
    ):
        return
    if not context.args:
        await update.message.reply_text(
            "Usage: `/setapiurl https://example.com/api`", parse_mode="Markdown"
        )
        return
    url = context.args[0].strip()
    set_setting("api_url", url)
    await update.message.reply_text(f"✅ API URL saved:\n`{url}`", parse_mode="Markdown")


async def set_api_key(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if (
        not update.effective_user
        or not update.message
        or not is_admin(update.effective_user.id)
    ):
        return
    if not context.args:
        await update.message.reply_text(
            "Usage: `/setapikey YOUR_API_KEY`", parse_mode="Markdown"
        )
        return
    key = context.args[0].strip()
    set_setting("api_key", key)
    await update.message.reply_text("✅ API Key saved successfully.")


async def set_support(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if (
        not update.effective_user
        or not update.message
        or not is_admin(update.effective_user.id)
    ):
        return
    if not context.args:
        await update.message.reply_text(
            "Usage: `/setsupport @username`", parse_mode="Markdown"
        )
        return
    sup = context.args[0].strip()
    set_setting("support_contact", sup)
    await update.message.reply_text(f"✅ Support set to `{sup}`.", parse_mode="Markdown")


async def job_poll_otps(context: ContextTypes.DEFAULT_TYPE) -> None:
    countries = get_all_countries()
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, user_id, country_code, phone_number FROM orders WHERE status = 'ACTIVE' LIMIT 25"
    )
    active_rows = cursor.fetchall()
    conn.close()

    for order_id, user_id, c_code, phone_number in active_rows:
        resolved_code = resolve_country_code(c_code)
        otp_code = fetch_sms_from_panel(phone_number, resolved_code)
        if not otp_code:
            continue

        reward = complete_order_and_reward(
            int(order_id), int(user_id), resolved_code, otp_code
        )
        if reward > 0:
            if resolved_code and resolved_code in countries:
                c_info = countries[resolved_code]
                country_line = f"🌍 **Country:** {c_info['flag']} {c_info['name']}\n"
            else:
                country_line = ""
            try:
                await context.bot.send_message(
                    chat_id=int(user_id),
                    text=(
                        f"📩 **OTP Received!**\n\n"
                        f"🧾 **Order ID:** `#{order_id}`\n"
                        f"{country_line}"
                        f"📱 **Phone:** `{phone_number}`\n"
                        f"🔐 **Code:** `{otp_code}`\n"
                        f"💵 **Earned:** `+৳{reward:.2f}`"
                    ),
                    parse_mode="Markdown",
                )
            except Exception as exc:
                logger.warning("Failed to send OTP message to %s: %s", user_id, exc)


async def job_daily_winner_announce(context: ContextTypes.DEFAULT_TYPE) -> None:
    now = datetime.datetime.now()
    today_str = now.strftime("%Y-%m-%d")
    if now.hour == 23 and now.minute >= 55:
        if get_setting("last_winner_date", "") == today_str:
            return
        announced, _ = await announce_daily_winner(context, today_str)
        if announced:
            set_setting("last_winner_date", today_str)


web_app = Flask(__name__)


@web_app.route("/")
def home():
    return "RSP OTP Bot is running!", 200


def run_web_server() -> None:
    port = int(os.environ.get("PORT", 10000))
    web_app.run(host="0.0.0.0", port=port, use_reloader=False)


def main() -> None:
    init_db()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    threading.Thread(target=run_web_server, daemon=True).start()

    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("admin", admin_panel))
    app.add_handler(CommandHandler("sendotp", send_otp_cmd))
    app.add_handler(CommandHandler("addcountry", add_country_cmd))
    app.add_handler(CommandHandler("setrate", set_rate_cmd))
    app.add_handler(CommandHandler("setname", set_bot_name))
    app.add_handler(CommandHandler("setapiurl", set_api_url))
    app.add_handler(CommandHandler("setapikey", set_api_key))
    app.add_handler(CommandHandler("setsupport", set_support))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    if app.job_queue:
        app.job_queue.run_repeating(job_poll_otps, interval=5, first=5)
        app.job_queue.run_repeating(job_daily_winner_announce, interval=60, first=60)

    logger.info("Bot started...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
