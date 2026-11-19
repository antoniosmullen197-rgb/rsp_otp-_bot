import os
import random
import sqlite3
import threading
from flask import Flask
from telegram import (
    Update,
    ReplyKeyboardMarkup,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# ================= কনফিগারেশন =================
BOT_TOKEN = os.getenv("BOT_TOKEN", "9178685196:AAGnHHXm7at9Pt8IWIW-zPdiOPkl564dl3U")
ADMIN_ID = int(os.getenv("ADMIN_ID", "2068530616"))
PORT = int(os.getenv("PORT", "8080"))
DB_NAME = "bot_data.db"

STOCK_ITEMS = [
    {"id": "fb_1", "flag": "🇪🇹", "app": "FACEBOOK", "rate": 0.0020},
    {"id": "fb_2", "flag": "🇷🇺", "app": "FACEBOOK", "rate": 0.0020},
    {"id": "ig_1", "flag": "🇸🇩", "app": "INSTAGRAM", "rate": 0.0035},
]


# ================= ডাটাবেজ ও সেটিংস =================
def init_db():
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            balance REAL DEFAULT 0.0,
            referred_by INTEGER,
            ref_count INTEGER DEFAULT 0,
            last_number TEXT DEFAULT '',
            last_otp TEXT DEFAULT ''
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
        """
    )

    defaults = {
        "bot_name": "RSP OTP BOT",
        "sms_api_key": "NOT_SET",
        "sms_api_url": "https://api.example.com/stubs/handler_api.php",
    }
    for k, v in defaults.items():
        cur.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, v)
        )

    conn.commit()
    conn.close()


def get_setting(key: str, default: str = "") -> str:
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT value FROM settings WHERE key = ?", (key,))
    row = cur.fetchone()
    conn.close()
    return row[0] if row else default


def set_setting(key: str, value: str):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    conn.commit()
    conn.close()


def get_or_create_user(user_id: int, username: str, ref_by: int | None = None):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
    row = cur.fetchone()

    if row is None:
        valid_ref = ref_by if (ref_by and ref_by != user_id) else None
        cur.execute(
            "INSERT INTO users (user_id, username, balance, referred_by) VALUES (?, ?, ?, ?)",
            (user_id, username or "User", 0.0, valid_ref),
        )
        if valid_ref:
            cur.execute(
                "UPDATE users SET ref_count = ref_count + 1 WHERE user_id = ?",
                (valid_ref,),
            )
        conn.commit()
        cur.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        row = cur.fetchone()

    conn.close()
    return {
        "user_id": row[0],
        "username": row[1],
        "balance": row[2],
        "referred_by": row[3],
        "ref_count": row[4],
        "last_number": row[5],
        "last_otp": row[6],
    }


def add_user_balance(target_user_id: int, amount: float) -> bool:
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM users WHERE user_id = ?", (target_user_id,))
    if cur.fetchone() is None:
        conn.close()
        return False
    cur.execute(
        "UPDATE users SET balance = balance + ? WHERE user_id = ?",
        (amount, target_user_id),
    )
    conn.commit()
    conn.close()
    return True


def get_total_users() -> int:
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM users")
    count = cur.fetchone()[0]
    conn.close()
    return count


def update_user_number_otp(user_id: int, number: str, otp: str):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute(
        "UPDATE users SET last_number = ?, last_otp = ? WHERE user_id = ?",
        (number, otp, user_id),
    )
    conn.commit()
    conn.close()


def get_leaderboard(limit: int = 5):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute(
        "SELECT username, ref_count, balance FROM users ORDER BY ref_count DESC, balance DESC LIMIT ?",
        (limit,),
    )
    rows = cur.fetchall()
    conn.close()
    return rows


# ================= ফ্রি হোস্টিং Keep-Alive সার্ভার =================
flask_app = Flask(__name__)


@flask_app.route("/")
def home():
    bot_name = get_setting("bot_name", "Telegram Bot")
    return f"{bot_name} is alive and running!", 200


def run_flask():
    flask_app.run(host="0.0.0.0", port=PORT)


# ================= ইউজার মেনু ও কার্ড =================
def main_menu_keyboard():
    keyboard = [
        ["📞 Get Number", "📦 Bulk Numbers"],
        ["💰 Balance", "📡 Status"],
        ["📩 Download OTP", "🏆 Leaderboard"],
        ["🎁 Referral", "📊 Live Traffic"],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)


def stock_card_keyboard(stock_id: str):
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("📞 GET NUMBER", callback_data=f"buy_{stock_id}")]]
    )


async def send_stock_cards(update: Update):
    for item in STOCK_ITEMS:
        msg = (
            f"🆕 New Stock Added ☀️\n\n"
            f"{item['flag']} | 📱 {item['app']}\n"
            f"💲 Rate per OTP: ${item['rate']:.4f}"
        )
        await update.message.reply_text(
            msg, reply_markup=stock_card_keyboard(item["id"])
        )


# ================= কমান্ডসমূহ =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ref_id = None
    if context.args:
        try:
            ref_id = int(context.args[0])
        except ValueError:
            ref_id = None

    get_or_create_user(user.id, user.username or user.first_name, ref_id)
    bot_name = get_setting("bot_name", "RSP OTP BOT")

    await update.message.reply_text(
        f"স্বাগতম {user.first_name}! *{bot_name}* চালু হয়েছে।\n"
        f"নিচের মেনু থেকে যেকোনো অপশন বেছে নিন:",
        reply_markup=main_menu_keyboard(),
        parse_mode="Markdown",
    )
    await send_stock_cards(update)


async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ এই কমান্ডটি শুধুমাত্র অ্যাডমিনের জন্য।")
        return

    bot_name = get_setting("bot_name", "RSP OTP BOT")
