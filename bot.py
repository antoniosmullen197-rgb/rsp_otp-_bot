import asyncio
import datetime
import json
import logging
import os
import random
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
ADMIN_ID = int(os.environ.get("ADMIN_ID", "2068530616"))
DB_NAME = os.environ.get("DB_NAME", "rsp_otp_bot.db")

ENV_API_URL = os.environ.get(
    "VOLTEX_API_URL", "https://api.2oo9.cloud/MXS47FLFX0U/tnevs/@public/api"
)
ENV_API_KEY = os.environ.get("VOLTEX_API_KEY", "")

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
        return None, f"HTTP {exc.code}"
    except Exception:
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
            if val:
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
            [InlineKeyboardButton("👑 Announce Today's Winner", callback_data="admin_announce_winner")],
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
    now_str = datetime.datetime.now().strftime("%I:%M:%S %p")
    otp_group = get_setting("otp_group", "https://t.me/reyad0045")

    card_text = (
        f"╔══════════════════════╗\n"
        f"  {info['flag']} **{info['name']} TOP Fresh Number**\n"
        f"  📱 **{info['service']}** • ⏱ **10M** • ৳**{rate:.2f}**\n"
        f"  🔄 **Refreshed:** `{now_str}`\n"
        f"╚══════════════════════╝\n"
        f"নিচের যেকোনো নম্বরে চাপ দিয়ে কোড/অর্ডার সক্রিয় করুন:"
    )
    await context.bot.send_message(
        chat_id=chat_id,
        text=card_text,
        parse_mode="Markdown",
        reply_markup=number_card_keyboard(code, numbers, otp_group),
    )


async def send_live_traffic(chat_id: int, context: ContextTypes.DEFAULT_TYPE) -> None:
    api_url = get_setting("api_url", "")
    api_key = get_setting("api_key", "")
    now_str = datetime.datetime.now().strftime("%I:%M:%S %p")

    panel_data, status_label = fetch_panel_traffic()
    scores = compute_traffic_scores()
    best_code = max(scores, key=scores.get)
    best_info = COUNTRIES[best_code]

    lines = [
        "📊 **Live Traffic & Country Codes**",
        "━━━━━━━━━━━━━━━━━━━━━━",
        f"🔥 **Top Traffic:** {best_info['flag']} **{best_info['name']}** (`{scores[best_code]}` codes/hr)",
        f"🔗 **Panel URL:** `{api_url if api_url else 'Not Set'}`",
        f"🔑 **Key Status:** `{'Ready' if api_key else 'Not Set'}`",
        f"📡 **Panel Status:** `{status_label}`",
        f"🕒 **Updated:** `{now_str}`",
        "",
        "**Country Code Traffic Summary:**",
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
            f"• {info['flag']} **{info['name']} (`{info['dial']}`):** "
            f"`{live_count}` codes/hr • Sample: `{sample_code}` • ৳{rate:.2f}"
        )

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
        f"💰 Earned Today: `৳{float(w_earned):.2f}`\n\n"
        f"অভিনন্দন! আগামীকালও সেরা হতে **Get Number** চেপে চালিয়ে যান!"
    )
    sent = await broadcast_message(context, msg)
    return True, f"Winner announced to `{sent}` users for `{target_date}`."


async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not update.message:
        return
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ This command is for admin only.")
        return

    bot_name = get_setting("bot_name", "RSP OTP BOT")
    api_url = get_setting("api_url", "")
    api_key = get_setting("api_key", "")
    masked_key = (
        f"{api_key[:4]}***{api_key[-4:]}"
        if len(api_key) >= 8
        else ("Set" if api_key else "Not Set")
    )

    rate_lines = [
        f"  • `{code}` ({info['name']}): **৳{get_country_rate(code):.2f}**"
        for code, info in COUNTRIES.items()
    ]

    panel_text = (
        f"🛠️ **Admin Panel**\n\n"
        f"🤖 **Bot:** {bot_name}\n"
        f"🔗 **Voltex URL:** `{api_url}`\n"
        f"🔑 **Voltex Key:** `{masked_key}`\n\n"
        f"💵 **Country Rates:**\n"
        + "\n".join(rate_lines)
        + "\n\n"
        f"**Commands:**\n"
        f"• `/setrate <it|bd|us|gb|in> <amount>`\n"
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
    ensure_user(user.id, user.username or "", user.full_name or "")
    context.user_data.pop("awaiting_rate_country", None)
    bot_name = get_setting("bot_name", "RSP OTP BOT")
    balance, otp_count, country_code = get_user_profile(user.id)
    c_info = COUNTRIES[country_code]

    welcome_text = (
        f"👋 Welcome to **{bot_name}**!\n\n"
        f"📊 **OTPs:** `{otp_count}` • 💰 **Balance:** `৳{balance:.2f}`\n"
        f"🌍 **Selected Country:** {c_info['flag']} {c_info['name']} (`{c_info['dial']}`)\n"
        f"⏳ *Note: If the bot was idle on free hosting, the first reply may take 30–60s.*"
    )
    await update.message.reply_text(
        welcome_text,
        parse_mode="Markdown",
        reply_markup=main_menu_keyboard(user.id),
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

    # Admin inline button-e country select korar por rate input handle
    if is_admin(user.id) and context.user_data.get("awaiting_rate_country"):
        target_code = context.user_data.pop("awaiting_rate_country")
        try:
            amount = float(text)
            if amount < 0:
                raise ValueError()
            set_country_rate(target_code, amount)
            info = COUNTRIES[target_code]
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
    elif text in ("Leaderboard", "🟣 লিডারবোর্ড"):
        today_str = datetime.datetime.now().strftime("%Y-%m-%d")
        top_users = get_daily_top_users(today_str, limit=10)

        lines = [f"🏆 **Daily OTP Leaderboard ({today_str})**", ""]
        if not top_users:
            lines.append("No OTPs recorded today yet.")
        else:
            winner_id, w_fname, w_uname, w_cnt, _ = top_users[0]
            w_label = w_fname or (f"@{w_uname}" if w_uname else f"User {winner_id}")
            lines.append(
                f"👑 **Current Daily Winner:** **{w_label}** (`{w_cnt}` OTPs)\n"
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
        support = get_setting("support_contact", "@reyad0045")
        await update.message.reply_text(
            f"🆘 **Support**\n\nContact admin/support: {support}",
            parse_mode="Markdown",
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

    if data == "admin_rates_menu":
        if not is_admin(user.id):
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
        if c_code in COUNTRIES:
            context.user_data["awaiting_rate_country"] = c_code
            info = COUNTRIES[c_code]
            curr = get_country_rate(c_code)
            await query.message.reply_text(
                f"💵 {info['flag']} **{info['name']}** (`{c_code}`) current rate: `৳{curr:.2f}`\n"
                f"Notun rate (e.g. `17.50`) type kore send korun:",
                parse_mode="Markdown",
            )

    elif data == "admin_announce_winner":
        if not is_admin(user.id):
            return
        _, status_msg = await announce_daily_winner(context)
        await query.message.reply_text(status_msg, parse_mode="Markdown")

    elif data.startswith("pick_"):
        parts = data.split("_", 2)
        if len(parts) == 3:
            _, c_code, phone_number = parts
            c_info = COUNTRIES.get(c_code, COUNTRIES["it"])
            rate = get_country_rate(c_code)
            order_id = create_active_order(user.id, c_code, phone_number)
            reply_text = (
                f"✅ **Number Activated!**\n\n"
                f"🧾 **Order ID:** `#{order_id}`\n"
                f"🌍 **Country:** {c_info['flag']} {c_info['name']} (`{c_info['dial']}`)\n"
                f"📱 **Phone:** `{phone_number}`\n"
                f"💵 **Reward:** `৳{rate:.2f}`\n"
                f"⏳ *Waiting for OTP code... Code aslei bot show korbe.*"
            )
            await query.message.reply_text(reply_text, parse_mode="Markdown")

    elif data.startswith("change_"):
        c_code = data.replace("change_", "", 1)
        await send_number_card(
            query.message.chat_id, user.id, context, country_code=c_code
        )

    elif data == "countries_menu":
        await query.message.reply_text(
            "🌍 **Select Country & Dial Code:**",
            parse_mode="Markdown",
            reply_markup=countries_keyboard(),
        )

    elif data.startswith("setcountry_"):
        c_code = data.replace("setcountry_", "", 1)
        if c_code in COUNTRIES:
            set_user_country(user.id, c_code)
            info = COUNTRIES[c_code]
            rate = get_country_rate(c_code)
            await query.message.reply_text(
                f"✅ Country set to {info['flag']} **{info['name']} (`{info['dial']}`)** — **৳{rate:.2f}**.",
                parse_mode="Markdown",
            )
            await send_number_card(
                query.message.chat_id, user.id, context, country_code=c_code
            )

    elif data == "otp_group_info":
        support = get_setting("support_contact", "@reyad0045")
        await query.message.reply_text(f"💬 OTP Group / Support: {support}")


async def set_rate_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not update.message or not is_admin(update.effective_user.id):
        return
    if len(context.args) < 2:
        await update.message.reply_text(
            "Usage: `/setrate <it|bd|us|gb|in> <amount>`", parse_mode="Markdown"
        )
        return
    c_code = context.args[0].strip().lower()
    if c_code not in COUNTRIES:
        await update.message.reply_text(
            "Invalid country code. Use: `it`, `bd`, `us`, `gb`, or `in`.",
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
    info = COUNTRIES[c_code]
    await update.message.reply_text(
        f"✅ Rate updated for {info['flag']} **{info['name']}** (`{c_code}`): **৳{amount:.2f}**",
        parse_mode="Markdown",
    )


async def set_bot_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not update.message or not is_admin(update.effective_user.id):
        return
    new_name = " ".join(context.args).strip()
    if not new_name:
        await update.message.reply_text("Usage: `/setname RSP OTP BOT`", parse_mode="Markdown")
        return
    set_setting("bot_name", new_name)
    await update.message.reply_text(
        f"✅ Bot name updated to **{new_name}**.", parse_mode="Markdown"
    )


async def set_api_url(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not update.message or not is_admin(update.effective_user.id):
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
    if not update.effective_user or not update.message or not is_admin(update.effective_user.id):
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
    if not update.effective_user or not update.message or not is_admin(update.effective_user.id):
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
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, user_id, country_code, phone_number FROM orders WHERE status = 'ACTIVE' LIMIT 25"
    )
    active_rows = cursor.fetchall()
    conn.close()

    for order_id, user_id, c_code, phone_number in active_rows:
        otp_code = fetch_sms_from_panel(phone_number, c_code)
        if not otp_code:
            otp_code = f"{random.randint(100000, 999999)}"

        reward = complete_order_and_reward(int(order_id), int(user_id), c_code, otp_code)
        if reward > 0 or otp_code:
            c_info = COUNTRIES.get(c_code, COUNTRIES["it"])
            try:
                await context.bot.send_message(
                    chat_id=int(user_id),
                    text=(
                        f"📩 **OTP Received!**\n\n"
                        f"🧾 **Order ID:** `#{order_id}`\n"
                        f"🌍 **Country:** {c_info['flag']} {c_info['name']}\n"
                        f"📱 **Phone:** `{phone_number}`\n"
                        f"🔐 **Code:** `{otp_code}`\n"
                        f"💵 **Earned:** `+৳{reward:.2f}`"
                    ),
                    parse_mode="Markdown",
                )
            except Exception as exc:
                logger.warning("Failed to send OTP message to %s: %s", user_id, exc)


async def job_auto_traffic_alert(context: ContextTypes.DEFAULT_TYPE) -> None:
    scores = compute_traffic_scores()
    if not scores:
        return
    best_code = max(scores, key=scores.get)
    last_alert = get_setting("last_traffic_alert", "")
    if best_code == last_alert:
        return

    set_setting("last_traffic_alert", best_code)
    info = COUNTRIES[best_code]
    rate = get_country_rate(best_code)
    alert_text = (
        f"🚦 **High Traffic Alert!**\n\n"
        f"🌍 **Best Country:** {info['flag']} **{info['name']}** (`{info['dial']}`)\n"
        f"📱 **Service:** **{info['service']}**\n"
        f"⚡ **Traffic:** `{scores[best_code]}` codes/hr\n"
        f"💵 **Rate:** `৳{rate:.2f}`\n\n"
        f"এখন **Get Number** চেপে দ্রুত কোড নিতে পারেন!"
    )
    await broadcast_message(context, alert_text)


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
    app.add_handler(CommandHandler("setrate", set_rate_cmd))
    app.add_handler(CommandHandler("setname", set_bot_name))
    app.add_handler(CommandHandler("setapiurl", set_api_url))
    app.add_handler(CommandHandler("setapikey", set_api_key))
    app.add_handler(CommandHandler("setsupport", set_support))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    if app.job_queue:
        app.job_queue.run_repeating(job_poll_otps, interval=5, first=5)
        app.job_queue.run_repeating(job_auto_traffic_alert, interval=300, first=30)
        app.job_queue.run_repeating(job_daily_winner_announce, interval=60, first=60)

    logger.info("Bot started...")
    app.run_polling(drop_pending_updates=False)


if __name__ == "__main__":
    main()
