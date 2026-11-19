import logging
import os
import random
import sqlite3
import threading
from flask import Flask
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
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

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

# ================= কনফিগারেশন =================
BOT_TOKEN = os.getenv(
    "BOT_TOKEN", "9178685196:AAGnHHXm7at9Pt8IWIW-zPdiOPkl564dl3U"
)
ADMIN_ID = int(os.getenv("ADMIN_ID", "2068530616"))
DB_PATH = "otp_bot.db"

# ================= ডাটাবেস =================
conn = sqlite3.connect(DB_PATH, check_same_thread=False)
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
    CREATE TABLE IF NOT EXISTS stock (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        service_name TEXT,
        country TEXT,
        price TEXT,
        quantity INTEGER DEFAULT 10
    )
    """
)
conn.commit()


def get_setting(key: str, default: str) -> str:
    cursor.execute("SELECT value FROM settings WHERE key = ?", (key,))
    row = cursor.fetchone()
    return row[0] if row else default


def set_setting(key: str, value: str) -> None:
    cursor.execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
        (key, value),
    )
    conn.commit()


def ensure_default_stock() -> None:
    cursor.execute("SELECT COUNT(*) FROM stock")
    count = cursor.fetchone()[0]
    if count == 0:
        sample_items = [
            ("Telegram OTP", "Bangladesh (+880)", "$0.35", 25),
            ("WhatsApp OTP", "Indonesia (+62)", "$0.25", 40),
            ("Gmail / Google OTP", "USA (+1)", "$0.40", 15),
        ]
        cursor.executemany(
            "INSERT INTO stock (service_name, country, price, quantity) VALUES (?, ?, ?, ?)",
            sample_items,
        )
        conn.commit()


ensure_default_stock()


# ================= কিবোর্ড ও কার্ড =================
def main_menu_keyboard() -> ReplyKeyboardMarkup:
    keyboard = [
        ["📱 নম্বর নিন", "📦 স্টক দেখুন"],
        ["💰 ব্যালেন্স", "🆘 সাপোর্ট"],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)


async def send_stock_cards(update: Update) -> None:
    cursor.execute(
        "SELECT id, service_name, country, price, quantity FROM stock WHERE quantity > 0"
    )
    rows = cursor.fetchall()

    if not rows:
        await update.message.reply_text("বর্তমানে কোনো নম্বর স্টকে নেই।")
        return

    for item_id, service, country, price, qty in rows:
        text = (
            f"🔹 *সার্ভিস:* {service}\n"
            f"🌍 *দেশ:* {country}\n"
            f"💵 *মূল্য:* {price}\n"
            f"📊 *স্টক:* {qty} টি"
        )
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "✅ নম্বর কিনুন", callback_data=f"buy_{item_id}"
                    )
                ]
            ]
        )
        await update.message.reply_text(
            text, reply_markup=keyboard, parse_mode="Markdown"
        )


# ================= কমান্ড হ্যান্ডলার =================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    bot_name = get_setting("bot_name", "RSP OTP BOT")

    await update.message.reply_text(
        f"স্বাগতম {user.first_name}! *{bot_name}* চালু হয়েছে।\n"
        f"নিচের মেনু থেকে যেকোনো অপশন বেছে নিন:",
        reply_markup=main_menu_keyboard(),
        parse_mode="Markdown",
    )
    await send_stock_cards(update)


async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text(
            "⛔ এই কমান্ডটি শুধুমাত্র অ্যাডমিনের জন্য।"
        )
        return

    bot_name = get_setting("bot_name", "RSP OTP BOT")
    api_url = get_setting("api_url", "সেট করা হয়নি")
    api_key = get_setting("api_key", "সেট করা হয়নি")

    msg = (
        "🛠 *অ্যাডমিন প্যানেল*\n\n"
        f"🤖 বটের নাম: *{bot_name}*\n"
        f"🔗 API URL: `{api_url}`\n"
        f"🔑 API Key: `{api_key}`\n\n"
        "*কমান্ডসমূহ:*\n"
        "• `/setname নতুন নাম` — বটের নাম পরিবর্তন\n"
        "• `/setapiurl https://...` — API URL সেট\n"
        "• `/setapikey YOUR_KEY` — API Key সেট"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")


async def set_name_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    if update.effective_user.id != ADMIN_ID:
        return
    if not context.args:
        await update.message.reply_text("ব্যবহার: `/setname আপনার বটের নাম`", parse_mode="Markdown")
        return
    new_name = " ".join(context.args)
    set_setting("bot_name", new_name)
    await update.message.reply_text(f"✅ বটের নাম পরিবর্তন করে *{new_name}* করা হয়েছে।", parse_mode="Markdown")


async def set_api_url_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    if update.effective_user.id != ADMIN_ID:
        return
    if not context.args:
        await update.message.reply_text("ব্যবহার: `/setapiurl https://example.com`", parse_mode="Markdown")
        return
    url = context.args[0].strip()
    set_setting("api_url", url)
    await update.message.reply_text(f"✅ API URL সেট করা হয়েছে:\n`{url}`", parse_mode="Markdown")


async def set_api_key_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    if update.effective_user.id != ADMIN_ID:
        return
    if not context.args:
        await update.message.reply_text("ব্যবহার: `/setapikey YOUR_API_KEY`", parse_mode="Markdown")
        return
    key = context.args[0].strip()
    set_setting("api_key", key)
    await update.message.reply_text("✅ API Key সফলভাবে সংরক্ষণ করা হয়েছে।")


async def button_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    await query.answer()

    if query.data and query.data.startswith("buy_"):
        item_id = query.data.split("_", 1)[1]
        cursor.execute(
            "SELECT service_name, country, price, quantity FROM stock WHERE id = ?",
            (item_id,),
        )
        row = cursor.fetchone()
        if not row or row[3] <= 0:
            await query.edit_message_text("❌ দুঃখিত, এই নম্বরটি স্টক আউট হয়ে গেছে।")
            return

        service, country, price, qty = row
        cursor.execute(
            "UPDATE stock SET quantity = quantity - 1 WHERE id = ?", (item_id,)
        )
        conn.commit()

        sample_number = f"+8801{random.randint(700000000, 999999999)}"
        sample_otp = f"{random.randint(100000, 999999)}"

        await query.edit_message_text(
            f"✅ *অর্ডার কনফার্ম হয়েছে!*\n\n"
            f"🔹 সার্ভিস: {service}\n"
            f"🌍 দেশ: {country}\n"
            f"📱 নম্বর: `{sample_number}`\n"
            f"🔐 ডেমো OTP: `{sample_otp}`\n"
            f"💵 মূল্য: {price}",
            parse_mode="Markdown",
        )


async def text_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    text = update.message.text
    if text in ["📱 নম্বর নিন", "📦 স্টক দেখুন"]:
        await send_stock_cards(update)
    elif text == "💰 ব্যালেন্স":
        await update.message.reply_text("💰 আপনার বর্তমান ব্যালেন্স: *$0.00*", parse_mode="Markdown")
    elif text == "🆘 সাপোর্ট":
        await update.message.reply_text("🆘 যেকোনো প্রয়োজনে অ্যাডমিনের সাথে যোগাযোগ করুন।")
    else:
        await update.message.reply_text(
            "মেনু থেকে অপশন বেছে নিন অথবা `/start` পাঠান।",
            reply_markup=main_menu_keyboard(),
        )


# ================= Flask ওয়েব সার্ভার =================
web_app = Flask(__name__)


@web_app.route("/")
def home():
    return "RSP OTP Bot is running!", 200


def run_web_server() -> None:
    port = int(os.environ.get("PORT", 10000))
    web_app.run(host="0.0.0.0", port=port)


# ================= বট রান =================
def main() -> None:
    threading.Thread(target=run_web_server, daemon=True).start()

    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("admin", admin_panel))
    app.add_handler(CommandHandler("setname", set_name_command))
    app.add_handler(CommandHandler("setapiurl", set_api_url_command))
    app.add_handler(CommandHandler("setapikey", set_api_key_command))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    print("বট চালু হয়েছে...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
