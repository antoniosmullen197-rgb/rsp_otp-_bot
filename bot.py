import asyncio
import datetime
import html
import json
import logging
import os
import random
import sqlite3
import threading
import urllib.error
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

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

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "2068530616"))
DB_NAME = os.environ.get("DB_NAME", "rsp_otp_bot.db")

ENV_API_URL = os.environ.get(
    "VOLTEX_API_URL", "https://api.2oo9.cloud/MXS47FLFX0U/tnevs/@public/api"
)
ENV_API_KEY = os.environ.get("VOLTEX_API_KEY", "")

BD_TZ = ZoneInfo("Asia/Dhaka")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

COUNTRIES = {
    "it": {
        "name": "Italy",
        "flag": "🇮🇹",
        "dial": "+39",
        "service": "Facebook",
        "rate": 16.50,
        "traffic": 148,
        "code_sample": "FB-84920",
    },
    "bd": {
        "name": "Bangladesh",
        "flag": "🇧🇩",
        "dial": "+880",
        "service": "Telegram",
        "rate": 15.00,
        "traffic": 212,
        "code_sample": "TG-51094",
    },
    "us": {
        "name": "United States",
        "flag": "🇺🇸",
        "dial": "+1",
        "service": "WhatsApp",
        "rate": 20.00,
        "traffic": 185,
        "code_sample": "WA-73918",
    },
    "gb": {
        "name": "United Kingdom",
        "flag": "🇬🇧",
        "dial": "+44",
        "service": "Instagram",
        "rate": 14.00,
        "traffic": 96,
        "code_sample": "IG-30284",
    },
    "in": {
        "name": "India",
        "flag": "🇮🇳",
        "dial": "+91",
        "service": "Gmail",
        "rate": 10.00,
        "traffic": 264,
        "code_sample": "GM-66412",
    },
}


def now_bd() -> datetime.datetime:
    return datetime.datetime.now(BD_TZ)


def today_bd_str() -> str:
    return now_bd().strftime("%Y-%m-%d")


def esc(value: object) -> str:
    return html.escape(str(value if value is not None else ""))


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_NAME, timeout=15, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    return conn


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
            selected_country TEXT DEFAULT 'it',
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

    cursor.execute("PRAGMA table_info(users)")
    user_cols = {row[1] for row in cursor.fetchall()}
    if "otp_count" not in user_cols:
        cursor.execute("ALTER TABLE users ADD COLUMN otp_count INTEGER DEFAULT 0")
    if "selected_country" not in user_cols:
        cursor.execute("ALTER TABLE users ADD COLUMN selected_country TEXT DEFAULT 'it'")

    for code, info in COUNTRIES.items():
        cursor.execute(
            "INSERT OR IGNORE INTO country_rates (country_code, rate) VALUES (?, ?)",
            (code, float(info["rate"])),
        )

    default_settings = {
        "bot_name": "RSP OTP BOT",
        "api_url": ENV_API_URL,
        "api_key": ENV_API_KEY,
        "support_contact": "@reyad0045",
        "otp_group": "https://t.me/reyad0045",
        "last_traffic_alert": "",
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
        (key, value),
    )
    conn.commit()
    conn.close()


def get_country_rate(country_code: str) -> float:
    code = country_code if country_code in COUNTRIES else "it"
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT rate FROM country_rates WHERE country_code = ?", (code,))
    row = cursor.fetchone()
    conn.close()
    if row and row[0] is not None:
        return float(row[0])
    return float(COUNTRIES[code]["rate"])


def set_country_rate(country_code: str, rate: float) -> None:
    code = country_code if country_code in COUNTRIES else "it"
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR REPLACE INTO country_rates (country_code, rate) VALUES (?, ?)",
        (code, float(rate)),
    )
    conn.commit()
    conn.close()


def ensure_user(user_id: int, username: str = "", full_name: str = "") -> None:
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT OR IGNORE INTO users (user_id, username, full_name, balance, otp_count, selected_country)
        VALUES (?, ?, ?, 0.0, 0, 'it')
        """,
        (user_id, username or "", full_name or ""),
    )
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


def get_user_profile(user_id: int):
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT balance, otp_count, selected_country FROM users WHERE user_id = ?",
        (user_id,),
    )
    row = cursor.fetchone()
    conn.close()
    if not row:
        return 0.0, 0, "it"
    country = row[2] if row[2] in COUNTRIES else "it"
    return float(row[0] or 0.0), int(row[1] or 0), country


def set_user_country(user_id: int, country_code: str) -> None:
    if country_code not in COUNTRIES:
        country_code = "it"
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE users SET selected_country = ? WHERE user_id = ?",
        (country_code, user_id),
    )
    conn.commit()
    conn.close()


def create_active_order(user_id: int, country_code: str, phone_number: str) -> int:
    code = country_code if country_code in COUNTRIES else "it"
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
    code = country_code if country_code in COUNTRIES else "it"
    rate = get_country_rate(code)
    today_str = today_bd_str()
    rewarded = 0.0

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
    if cursor.rowcount > 0:
        rewarded = rate
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
    return rewarded


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


def generate_country_numbers(country_code: str, count: int = 3):
    c_info = COUNTRIES.get(country_code, COUNTRIES["it"])
    dial = c_info["dial"]
    numbers = []
    for _ in range(count):
        if dial == "+39":
            num = f"+393{random.randint(200000000, 999999999)}"
        elif dial == "+880":
            prefix = random.choice(["3", "4", "5", "6", "7", "8", "9"])
            num = f"+8801{prefix}{random.randint(10000000, 99999999)}"
        elif dial == "+1":
            num = f"+1{random.randint(201, 989)}{random.randint(2000000, 9999999)}"
        elif dial == "+44":
            num = f"+447{random.randint(100000000, 999999999)}"
        elif dial == "+91":
            prefix = random.choice(["6", "7", "8", "9"])
            num = f"+91{prefix}{random.randint(100000000, 999999999)}"
        else:
            num = f"{dial}{random.randint(100000000, 999999999)}"
        numbers.append(num)
    return numbers


def call_voltex_api(extra_params: dict):
    api_url = get_setting("api_url", "")
    api_key = get_setting("api_key", "")
    if not api_url:
        return None, "URL Not Set"

    params = dict(extra_params)
    if api_key:
        params.setdefault("key", api_key)
        params.setdefault("api_key", api_key)

    sep = "&" if "?" in api_url else "?"
    target_url = f"{api_url}{sep}{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(
        target_url,
        headers={"User-Agent": "Mozilla/5.0 RSP-OTP-Bot/1.0"},
    )
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
        logger.warning("Voltex API HTTP error: %s", exc.code)
        return None, f"HTTP {exc.code}"
    except Exception as exc:
        logger.warning("Voltex API request failed: %s", exc)
        return None, "Panel Sync Ready"


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
    return None


def compute_traffic_scores():
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
    order_counts = {row[0]: int(row[1]) for row in cursor.fetchall()}
    conn.close()

    scores = {}
    for code, info in COUNTRIES.items():
        base_score = (
            int(info["traffic"])
            + (order_counts.get(code, 0) * 15)
            + random.randint(-4, 10)
        )
        scores[code] = base_score
    return scores


def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID


def main_menu_keyboard(user_id: int) -> ReplyKeyboardMarkup:
    keyboard = [
        [KeyboardButton("Get Number"), KeyboardButton("Live Traffic")],
        [KeyboardButton("Wallet"), KeyboardButton("Leaderboard")],
        [KeyboardButton("Support")],
    ]
    if is_admin(user_id):
        keyboard.append([KeyboardButton("Admin Panel")])
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)


def admin_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("💵 Set Country Rates", callback_data="admin_rates_menu")],
            [
                InlineKeyboardButton(
                    "👑 Announce Today's Winner", callback_data="admin_announce_winner"
                )
            ],
        ]
    )


def admin_rate_countries_keyboard() -> InlineKeyboardMarkup:
    rows = []
    for code, info in COUNTRIES.items():
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


def number_card_keyboard(
    country_code: str, numbers: list[str], otp_group: str
) -> InlineKeyboardMarkup:
    rows = []
    for num in numbers:
        rows.append(
            [InlineKeyboardButton(f"☎️ {num}", callback_data=f"pick_{country_code}_{num}")]
        )
    rows.append(
        [
            InlineKeyboardButton("🔄 Change", callback_data=f"change_{country_code}"),
            InlineKeyboardButton("🌍 Country", callback_data="countries_menu"),
        ]
    )
    if otp_group.startswith("http"):
        rows.append([InlineKeyboardButton("💬 OTP Group", url=otp_group)])
    else:
        rows.append([InlineKeyboardButton("💬 OTP Group", callback_data="otp_group_info")])
    return InlineKeyboardMarkup(rows)


def countries_keyboard() -> InlineKeyboardMarkup:
    rows = []
    for code, info in COUNTRIES.items():
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
    _, _, current_country = get_user_profile(user_id)
    code = country_code if country_code in COUNTRIES else current_country
    set_user_country(user_id, code)

    info = COUNTRIES[code]
    rate = get_country_rate(code)
    numbers = generate_country_numbers(code, count=3)
    now_str = now_bd().strftime("%I:%M:%S %p")
    otp_group = get_setting("otp_group", "https://t.me/reyad0045")

    card_text = (
        f"╔══════════════════════╗\n"
        f"  {esc(info['flag'])} <b>{esc(info['name'])} TOP Fresh Number</b>\n"
        f"  📱 <b>{esc(info['service'])}</b> • ⏱ <b>10M</b> • ৳<b>{rate:.2f}</b>\n"
        f"  🔄 <b>Refreshed:</b> <code>{esc(now_str)}</code>\n"
        f"╚══════════════════════╝\n"
        f"নিচের যেকোনো নম্বরে চাপ দিয়ে কোড/অর্ডার সক্রিয় করুন:"
    )
    await context.bot.send_message(
        chat_id=chat_id,
        text=card_text,
        parse_mode="HTML",
        reply_markup=number_card_keyboard(code, numbers, otp_group),
    )


async def send_live_traffic(chat_id: int, context: ContextTypes.DEFAULT_TYPE) -> None:
    api_url = get_setting("api_url", "")
    api_key = get_setting("api_key", "")
    now_str = now_bd().strftime("%I:%M:%S %p")

    panel_data, status_label = fetch_panel_traffic()
    scores = compute_traffic_scores()
    best_code = max(scores, key=scores.get)
    best_info = COUNTRIES[best_code]

    lines = [
        "📊 <b>Live Traffic &amp; Country Codes</b>",
        "━━━━━━━━━━━━━━━━━━━━━━",
        f"🔥 <b>Top Traffic:</b> {esc(best_info['flag'])} <b>{esc(best_info['name'])}</b> (<code>{scores[best_code]}</code> codes/hr)",
        f"🔗 <b>Panel URL:</b> <code>{esc(api_url if api_url else 'Not Set')}</code>",
        f"🔑 <b>Key Status:</b> <code>{'Ready' if api_key else 'Not Set'}</code>",
        f"📡 <b>Panel Status:</b> <code>{esc(status_label)}</code>",
        f"🕒 <b>Updated:</b> <code>{esc(now_str)}</code>",
        "",
        "<b>Country Code Traffic Summary:</b>",
    ]

    for code, info in COUNTRIES.items():
        rate = get_country_rate(code)
        live_count = scores[code]
        sample_code = info["code_sample"]
        if (
            isinstance(panel_data, dict)
            and code in panel_data
            and isinstance(panel_data[code], dict)
        ):
            live_count = panel_data[code].get(
                "traffic", panel_data[code].get("count", live_count)
            )
            sample_code = panel_data[code].get("code", sample_code)

        lines.append(
            f"• {esc(info['flag'])} <b>{esc(info['name'])} (<code>{esc(info['dial'])}</code>):</b> "
            f"<code>{esc(live_count)}</code> codes/hr • Sample: <code>{esc(sample_code)}</code> • ৳{rate:.2f}"
        )

    await context.bot.send_message(
        chat_id=chat_id,
        text="\n".join(lines),
        parse_mode="HTML",
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
                chat_id=int(uid), text=text, parse_mode="HTML"
            )
            sent += 1
            await asyncio.sleep(0.05)
        except Exception:
            pass
    return sent


async def announce_daily_winner(
    context: ContextTypes.DEFAULT_TYPE, date_str: str | None = None
) -> tuple[bool, str]:
    target_date = date_str or today_bd_str()
    top_users = get_daily_top_users(target_date, limit=1)
    if not top_users:
        return False, f"No OTP winner found for <code>{esc(target_date)}</code>."

    winner_id, w_fname, w_uname, w_cnt, w_earned = top_users[0]
    w_label = w_fname or (f"@{w_uname}" if w_uname else f"User {winner_id}")
    msg = (
        f"👑 <b>Daily OTP Winner ({esc(target_date)})!</b>\n\n"
        f"🏆 Winner: <b>{esc(w_label)}</b>\n"
        f"📩 Total OTPs Today: <code>{w_cnt}</code>\n"
        f"💰 Earned Today: <code>৳{float(w_earned):.2f}</code>\n\n"
        f"অভিনন্দন! আগামীকালও সেরা হতে <b>Get Number</b> চেপে চালিয়ে যান!"
    )
    sent = await broadcast_message(context, msg)
    return True, f"Winner announced to <code>{sent}</code> users for <code>{esc(target_date)}</code>."


async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not update.message:
        return
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ This command is for admin only.")
        
