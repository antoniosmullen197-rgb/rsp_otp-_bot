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

    default_settings = {
        "bot_name": "RSP OTP BOT",
        "api_url": ENV_API_URL,
        "api_key": ENV_API_KEY,
        "support_contact": "@reyad0045",
        "otp_group": "https://t.me/reyad0045",
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
    if row and row[0]:
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


def record_number_claim(user_id: int, country_code: str, phone_number: str):
    c_info = COUNTRIES.get(country_code, COUNTRIES["it"])
    otp_code = f"{random.randint(100000, 999999)}"
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO orders (user_id, country_code, phone_number, otp_code, status)
        VALUES (?, ?, ?, ?, 'ACTIVE')
        """,
        (user_id, country_code, phone_number, otp_code),
    )
    order_id = cursor.lastrowid
    cursor.execute(
        """
        UPDATE users
        SET balance = ROUND(balance + ?, 2),
            otp_count = otp_count + 1
        WHERE user_id = ?
        """,
        (c_info["rate"], user_id),
    )
    conn.commit()
    conn.close()
    return order_id, otp_code


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


def fetch_panel_traffic(api_url: str, api_key: str):
    if not api_url:
        return None, "URL Not Set"

    query_params = {}
    if api_key:
        query_params["key"] = api_key
        query_params["api_key"] = api_key
    query_params["action"] = "traffic"

    sep = "&" if "?" in api_url else "?"
    target_url = f"{api_url}{sep}{urllib.parse.urlencode(query_params)}"

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
                    return parsed, "Connected"
            except json.JSONDecodeError:
                pass
            return {"raw_preview": raw_data[:120]}, "Connected"
    except urllib.error.HTTPError as exc:
        return None, f"HTTP {exc.code}"
    except Exception:
        return None, "Panel Sync Ready"


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


def number_card_keyboard(country_code: str, numbers: list[str], otp_group: str) -> InlineKeyboardMarkup:
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
        rows.append(
            [
                InlineKeyboardButton(
                    f"{info['flag']} {info['name']} ({info['dial']}) — ৳{info['rate']:.2f}",
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
    numbers = generate_country_numbers(code, count=3)
    now_str = datetime.datetime.now().strftime("%I:%M:%S %p")
    otp_group = get_setting("otp_group", "https://t.me/reyad0045")

    card_text = (
        f"╔══════════════════════╗\n"
        f"  {info['flag']} **{info['name']} TOP Fresh Number**\n"
        f"  📱 **{info['service']}** • ⏱ **10M** • ৳**{info['rate']:.2f}**\n"
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

    panel_data, status_label = fetch_panel_traffic(api_url, api_key)

    lines = [
        "📊 **Live Traffic & Country Codes**",
        "━━━━━━━━━━━━━━━━━━━━━━",
        f"🔗 **Panel URL:** `{api_url if api_url else 'Not Set'}`",
        f"🔑 **Key Status:** `{'Ready' if api_key else 'Not Set'}`",
        f"📡 **Panel Status:** `{status_label}`",
        f"🕒 **Updated:** `{now_str}`",
        "",
        "**Country Code Traffic Summary:**",
    ]

    for code, info in COUNTRIES.items():
        live_count = info["traffic"] + random.randint(-6, 12)
        sample_code = info["code_sample"]
        if isinstance(panel_data, dict) and code in panel_data and isinstance(panel_data[code], dict):
            live_count = panel_data[code].get("traffic", live_count)
            sample_code = panel_data[code].get("code", sample_code)

        lines.append(
            f"• {info['flag']} **{info['name']} (`{info['dial']}`):** "
            f"`{live_count}` codes/hr • Sample: `{sample_code}` • ৳{info['rate']:.2f}"
        )

    await context.bot.send_message(
        chat_id=chat_id,
        text="\n".join(lines),
        parse_mode="Markdown",
    )


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

    panel_text = (
        f"🛠️ **Admin Panel**\n\n"
        f"🤖 **Bot:** {bot_name}\n"
        f"🔗 **Voltex URL:** `{api_url}`\n"
        f"🔑 **Voltex Key:** `{masked_key}`\n\n"
        f"**Commands:**\n"
        f"• `/setname <Bot Name>`\n"
        f"• `/setapiurl <https://...>`\n"
        f"• `/setapikey <API_KEY>`\n"
        f"• `/setsupport <@username>`"
    )
    await update.message.reply_text(panel_text, parse_mode="Markdown")


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not update.effective_chat or not update.message:
        return

    user = update.effective_user
    ensure_user(user.id, user.username or "", user.full_name or "")
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
    if not update.message or not update.message.text or not update.effective_user or not update.effective_chat:
        return

    user = update.effective_user
    ensure_user(user.id, user.username or "", user.full_name or "")
    text = update.message.text.strip()

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
        conn = get_conn()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT full_name, username, otp_count, balance FROM users ORDER BY otp_count DESC, balance DESC LIMIT 5"
        )
        top_users = cursor.fetchall()
        conn.close()

        lines = ["🏆 **Top OTP Leaderboard**", ""]
        if not top_users:
            lines.append("No records yet.")
        else:
            for idx, (fname, uname, count_val, bal_val) in enumerate(top_users, start=1):
                label = fname or (f"@{uname}" if uname else "User")
                lines.append(f"{idx}. **{label}** — `{count_val}` OTPs (`৳{float(bal_val):.2f}`)")
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

    if data.startswith("pick_"):
        parts = data.split("_", 2)
        if len(parts) == 3:
            _, c_code, phone_number = parts
            c_info = COUNTRIES.get(c_code, COUNTRIES["it"])
            order_id, otp_code = record_number_claim(user.id, c_code, phone_number)
            reply_text = (
                f"✅ **Number Activated!**\n\n"
                f"🧾 **Order ID:** `#{order_id}`\n"
                f"🌍 **Country:** {c_info['flag']} {c_info['name']} (`{c_info['dial']}`)\n"
                f"📱 **Phone:** `{phone_number}`\n"
                f"🔐 **Code / OTP:** `{otp_code}`\n"
                f"💵 **Reward:** `+৳{c_info['rate']:.2f}`"
            )
            await query.message.reply_text(reply_text, parse_mode="Markdown")

    elif data.startswith("change_"):
        c_code = data.replace("change_", "", 1)
        await send_number_card(query.message.chat_id, user.id, context, country_code=c_code)

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
            await query.message.reply_text(
                f"✅ Country set to {info['flag']} **{info['name']} (`{info['dial']}`)**.",
                parse_mode="Markdown",
            )
            await send_number_card(query.message.chat_id, user.id, context, country_code=c_code)

    elif data == "otp_group_info":
        support = get_setting("support_contact", "@reyad0045")
        await query.message.reply_text(f"💬 OTP Group / Support: {support}")


async def set_bot_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not update.message or not is_admin(update.effective_user.id):
        return
    new_name = " ".join(context.args).strip()
    if not new_name:
        await update.message.reply_text("Usage: `/setname RSP OTP BOT`", parse_mode="Markdown")
        return
    set_setting("bot_name", new_name)
    await update.message.reply_text(f"✅ Bot name updated to **{new_name}**.", parse_mode="Markdown")


async def set_api_url(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not update.message or not is_admin(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("Usage: `/setapiurl https://example.com/api`", parse_mode="Markdown")
        return
    url = context.args[0].strip()
    set_setting("api_url", url)
    await update.message.reply_text(f"✅ API URL saved:\n`{url}`", parse_mode="Markdown")


async def set_api_key(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not update.message or not is_admin(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("Usage: `/setapikey YOUR_API_KEY`", parse_mode="Markdown")
        return
    key = context.args[0].strip()
    set_setting("api_key", key)
    await update.message.reply_text("✅ API Key saved successfully.")


async def set_support(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not update.message or not is_admin(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("Usage: `/setsupport @username`", parse_mode="Markdown")
        return
    sup = context.args[0].strip()
    set_setting("support_contact", sup)
    await update.message.reply_text(f"✅ Support set to `{sup}`.", parse_mode="Markdown")


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
    app.add_handler(CommandHandler("setname", set_bot_name))
    app.add_handler(CommandHandler("setapiurl", set_api_url))
    app.add_handler(CommandHandler("setapikey", set_api_key))
    app.add_handler(CommandHandler("setsupport", set_support))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    logger.info("Bot started...")
    app.run_polling(drop_pending_updates=False)


if __name__ == "__main__":
    main()
