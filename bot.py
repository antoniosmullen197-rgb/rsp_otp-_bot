import asyncio
import logging
import os
import random
import sqlite3
import threading
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

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


def init_db() -> None:
    conn = sqlite3.connect(DB_NAME)
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
            joined_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS services (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT UNIQUE,
            name TEXT,
            price REAL,
            stock INTEGER DEFAULT 0
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            service_code TEXT,
            phone_number TEXT,
            otp_code TEXT,
            status TEXT DEFAULT 'PENDING',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    default_settings = {
        "bot_name": "RSP OTP BOT",
        "api_url": "https://api.example.com/stubs/handler_api.php",
        "api_key": "",
        "support_contact": "@admin",
    }
    for key, val in default_settings.items():
        cursor.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
            (key, val),
        )

    default_services = [
        ("tg", "Telegram", 15.0, 25),
        ("wa", "WhatsApp", 20.0, 18),
        ("fb", "Facebook", 12.0, 30),
        ("ig", "Instagram", 10.0, 40),
        ("gm", "Gmail", 14.0, 22),
    ]
    for code, name, price, stock in default_services:
        cursor.execute(
            "INSERT OR IGNORE INTO services (code, name, price, stock) VALUES (?, ?, ?, ?)",
            (code, name, price, stock),
        )

    conn.commit()
    conn.close()


def get_setting(key: str, default: str = "") -> str:
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT value FROM settings WHERE key = ?", (key,))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row and row[0] is not None else default


def set_setting(key: str, value: str) -> None:
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
        (key, value),
    )
    conn.commit()
    conn.close()


def ensure_user(user_id: int, username: str = "", full_name: str = "") -> None:
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT OR IGNORE INTO users (user_id, username, full_name, balance)
        VALUES (?, ?, ?, 0.0)
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


def get_user_balance(user_id: int) -> float:
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    return float(row[0]) if row else 0.0


def get_all_services():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT id, code, name, price, stock FROM services ORDER BY id ASC")
    rows = cursor.fetchall()
    conn.close()
    return rows


def get_service_by_code(code: str):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, code, name, price, stock FROM services WHERE code = ?",
        (code,),
    )
    row = cursor.fetchone()
    conn.close()
    return row


def create_demo_order(user_id: int, service_code: str):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT stock FROM services WHERE code = ? AND stock > 0",
        (service_code,),
    )
    row = cursor.fetchone()
    if not row:
        conn.close()
        return None

    cursor.execute(
        "UPDATE services SET stock = stock - 1 WHERE code = ? AND stock > 0",
        (service_code,),
    )
    phone_number = f"+8801{random.choice(['3', '4', '5', '6', '7', '8', '9'])}{random.randint(10000000, 99999999)}"
    otp_code = f"{random.randint(100000, 999999)}"
    cursor.execute(
        """
        INSERT INTO orders (user_id, service_code, phone_number, otp_code, status)
        VALUES (?, ?, ?, ?, 'ACTIVE')
        """,
        (user_id, service_code, phone_number, otp_code),
    )
    order_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return order_id, phone_number, otp_code


def main_menu_keyboard() -> ReplyKeyboardMarkup:
    keyboard = [
        [KeyboardButton("📱 নম্বর নিন"), KeyboardButton("📦 স্টক দেখুন")],
        [KeyboardButton("💰 ব্যালেন্স"), KeyboardButton("🆘 সাপোর্ট")],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)


def service_card_keyboard(code: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("✅ নম্বর কিনুন", callback_data=f"buy_{code}")]]
    )


async def send_stock_cards(chat_id: int, context: ContextTypes.DEFAULT_TYPE) -> None:
    services = get_all_services()
    if not services:
        await context.bot.send_message(
            chat_id=chat_id,
            text="⚠️ বর্তমানে কোনো সার্ভিস স্টকে নেই।",
        )
        return

    for _, code, name, price, stock in services:
        text = (
            f"📌 **সার্ভিস:** {name} (`{code.upper()}`)\n"
            f"💵 **মূল্য:** ৳{price:.2f}\n"
            f"📦 **উপলব্ধ স্টক:** {stock} টি"
        )
        await context.bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode="Markdown",
            reply_markup=service_card_keyboard(code),
        )


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not update.effective_chat:
        return

    user = update.effective_user
    ensure_user(user.id, user.username or "", user.full_name or "")
    bot_name = get_setting("bot_name", "RSP OTP BOT")

    welcome_text = (
        f"👋 স্বাগতম **{bot_name}**-এ!\n\n"
        f"নিচের মেনু থেকে আপনার প্রয়োজনীয় অপশন বেছে নিন অথবা যেকোনো সার্ভিসের কার্ড থেকে নম্বর সংগ্রহ করুন।"
    )
    await update.message.reply_text(
        welcome_text,
        parse_mode="Markdown",
        reply_markup=main_menu_keyboard(),
    )
    await send_stock_cards(update.effective_chat.id, context)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text or not update.effective_user or not update.effective_chat:
        return

    user = update.effective_user
    ensure_user(user.id, user.username or "", user.full_name or "")
    text = update.message.text.strip()

    if text == "📱 নম্বর নিন" or text == "📦 স্টক দেখুন":
        await send_stock_cards(update.effective_chat.id, context)
    elif text == "💰 ব্যালেন্স":
        balance = get_user_balance(user.id)
        await update.message.reply_text(
            f"💰 আপনার বর্তমান ব্যালেন্স: **৳{balance:.2f}**",
            parse_mode="Markdown",
        )
    elif text == "🆘 সাপোর্ট":
        support = get_setting("support_contact", "@admin")
        await update.message.reply_text(
            f"🆘 যেকোনো সহায়তার জন্য যোগাযোগ করুন: {support}",
        )
    else:
        await update.message.reply_text(
            "ℹ️ অনুগ্রহ করে নিচের মেনু থেকে একটি অপশন বেছে নিন অথবা `/start` লিখুন।",
            parse_mode="Markdown",
            reply_markup=main_menu_keyboard(),
        )


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data or not query.from_user:
        return

    await query.answer()
    user = query.from_user
    ensure_user(user.id, user.username or "", user.full_name or "")

    if query.data.startswith("buy_"):
        code = query.data.replace("buy_", "", 1)
        service = get_service_by_code(code)
        if not service:
            await query.message.reply_text("❌ এই সার্ভিসটি খুঁজে পাওয়া যায়নি।")
            return

        _, _, name, price, stock = service
        if stock <= 0:
            await query.message.reply_text(
                f"⚠️ **{name}** সার্ভিসের স্টক শেষ হয়ে গেছে!",
                parse_mode="Markdown",
            )
            return

        order = create_demo_order(user.id, code)
        if not order:
            await query.message.reply_text("⚠️ স্টক আপডেট করা যায়নি, আবার চেষ্টা করুন।")
            return

        order_id, phone_number, otp_code = order
        reply_text = (
            f"✅ **নম্বর জেনারেট হয়েছে!**\n\n"
            f"🧾 **অর্ডার ID:** `#{order_id}`\n"
            f"📌 **সার্ভিস:** {name}\n"
            f"📱 **নম্বর:** `{phone_number}`\n"
            f"🔐 **ডেমো OTP কোড:** `{otp_code}`\n"
            f"💵 **মূল্য:** ৳{price:.2f}"
        )
        await query.message.reply_text(reply_text, parse_mode="Markdown")


def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID


async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not update.message:
        return
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ এই কমান্ডটি শুধুমাত্র অ্যাডমিনের জন্য।")
        return

    bot_name = get_setting("bot_name", "RSP OTP BOT")
    api_url = get_setting("api_url", "")
    api_key = get_setting("api_key", "")
    masked_key = (
        f"{api_key[:4]}***{api_key[-4:]}"
        if len(api_key) >= 8
        else ("সেট করা আছে" if api_key else "সেট করা নেই")
    )

    panel_text = (
        f"🛠️ **অ্যাডমিন প্যানেল**\n\n"
        f"🤖 **বটের নাম:** {bot_name}\n"
        f"🔗 **API URL:** `{api_url}`\n"
        f"🔑 **API Key:** `{masked_key}`\n\n"
        f"**ব্যবহারযোগ্য কমান্ডসমূহ:**\n"
        f"• `/setname নতুন নাম`\n"
        f"• `/setapiurl https://...`\n"
        f"• `/setapikey আপনার_কি`"
    )
    await update.message.reply_text(panel_text, parse_mode="Markdown")


async def set_bot_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not update.message:
        return
    if not is_admin(update.effective_user.id):
        return

    new_name = " ".join(context.args).strip()
    if not new_name:
        await update.message.reply_text("ব্যবহার: `/setname RSP OTP BOT`", parse_mode="Markdown")
        return

    set_setting("bot_name", new_name)
    await update.message.reply_text(
        f"✅ বটের নাম পরিবর্তন করে **{new_name}** করা হয়েছে।",
        parse_mode="Markdown",
    )


async def set_api_url(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not update.message:
        return
    if not is_admin(update.effective_user.id):
        return

    if not context.args:
        await update.message.reply_text(
            "ব্যবহার: `/setapiurl https://example.com/api`",
            parse_mode="Markdown",
        )
        return

    url = context.args[0].strip()
    set_setting("api_url", url)
    await update.message.reply_text(f"✅ API URL সেট করা হয়েছে:\n`{url}`", parse_mode="Markdown")


async def set_api_key(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not update.message:
        return
    if not is_admin(update.effective_user.id):
        return

    if not context.args:
        await update.message.reply_text(
            "ব্যবহার: `/setapikey আপনার_api_key`",
            parse_mode="Markdown",
        )
        return

    key = context.args[0].strip()
    set_setting("api_key", key)
    await update.message.reply_text("✅ API Key সফলভাবে সংরক্ষণ করা হয়েছে।")


web_app = Flask(__name__)


@web_app.route("/")
def home():
    return "RSP OTP Bot is running!", 200


def run_web_server() -> None:
    port = int(os.environ.get("PORT", 10000))
    web_app.run(host="0.0.0.0", port=port)


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
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    logger.info("বট চালু হয়েছে...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
