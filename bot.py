import asyncio
import datetime
import json
import logging
import os
import random
import sqlite3
import threading
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
# এখানে প্রয়োজনে কমা দিয়ে আরও অ্যাডমিন আইডি সরাসরি যুক্ত করতে পারবেন
ADMIN_IDS: set[int] = {ADMIN_ID}

SUPPORT_USERNAME = os.environ.get("SUPPORT_USERNAME", "@reyad0045")

VOLTEX_API_KEY = os.environ.get("VOLTEX_API_KEY", "MOFUE6IU9NF")
VOLTEX_API_URL = os.environ.get("VOLTEX_API_URL", "")

DB_NAME = os.environ.get("DB_NAME", "rsp_otp_bot.db")

COUNTRIES = [
    ("Italy", "it", "+39"),
    ("Bangladesh", "bd", "+880"),
    ("Indonesia", "id", "+62"),
    ("United Kingdom", "gb", "+44"),
]

SERVICES = [
    ("Facebook", "fb"),
    ("Telegram", "tg"),
    ("WhatsApp", "wa"),
    ("Gmail", "gm"),
]

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
        CREATE TABLE IF NOT EXISTS admins (
            user_id INTEGER PRIMARY KEY,
            added_by INTEGER,
            added_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            full_name TEXT,
            balance REAL DEFAULT 21.30,
            otp_count INTEGER DEFAULT 0,
            joined_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            country_name TEXT,
            service_name TEXT,
            phone_number TEXT,
            status TEXT DEFAULT 'ACTIVE',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    for aid in ADMIN_IDS:
        cursor.execute(
            "INSERT OR IGNORE INTO admins (user_id, added_by) VALUES (?, ?)",
            (aid, ADMIN_ID),
        )

    default_settings = {
        "bot_name": "VOLTEX SMS",
        "api_url": VOLTEX_API_URL,
        "api_key": VOLTEX_API_KEY,
        "rate_per_otp": "0.30",
        "level_name": "Diamond",
        "otp_group": "https://t.me/",
        "support_contact": SUPPORT_USERNAME,
    }
    for key, val in default_settings.items():
        cursor.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
            (key, val),
        )

    cursor.execute(
        "UPDATE settings SET value = ? WHERE key = 'api_key' AND (value IS NULL OR value = '')",
        (VOLTEX_API_KEY,),
    )

    cursor.execute(
        "UPDATE settings SET value = ? WHERE key = 'support_contact'",
        (SUPPORT_USERNAME,),
    )

    conn.commit()
    conn.close()


def is_admin(user_id: int) -> bool:
    uid = int(user_id)
    if uid in ADMIN_IDS:
        return True
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM admins WHERE user_id = ?", (uid,))
    found = cursor.fetchone() is not None
    conn.close()
    return found


def add_admin_db(new_admin_id: int, added_by: int) -> bool:
    uid = int(new_admin_id)
    ADMIN_IDS.add(uid)
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR IGNORE INTO admins (user_id, added_by) VALUES (?, ?)",
        (uid, int(added_by)),
    )
    changed = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return changed or (uid in ADMIN_IDS)


def remove_admin_db(target_admin_id: int) -> bool:
    uid = int(target_admin_id)
    if uid == ADMIN_ID:
        return False
    ADMIN_IDS.discard(uid)
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM admins WHERE user_id = ?", (uid,))
    changed = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return changed


def get_all_admins_db() -> list[int]:
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM admins ORDER BY user_id ASC")
    db_ids = {int(r[0]) for r in cursor.fetchall()}
    conn.close()
    return sorted(db_ids | ADMIN_IDS)


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
        INSERT OR IGNORE INTO users (user_id, username, full_name, balance, otp_count)
        VALUES (?, ?, ?, 21.30, 0)
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


def get_user_stats(user_id: int) -> tuple[float, int]:
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT balance, otp_count FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    if row:
        return float(row[0]), int(row[1])
    return 0.0, 0


def get_leaderboard(limit: int = 10):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT full_name, username, otp_count, balance
        FROM users
        ORDER BY otp_count DESC, balance DESC
        LIMIT ?
        """,
        (limit,),
    )
    rows = cursor.fetchall()
    conn.close()
    return rows


def fetch_voltex_numbers(
    country_name: str,
    country_code: str,
    dial_code: str,
    service_name: str,
    service_code: str,
) -> list[str]:
    api_url = get_setting("api_url", VOLTEX_API_URL).strip()
    api_key = get_setting("api_key", VOLTEX_API_KEY).strip()

    if api_url and api_key:
        try:
            params = {
                "api_key": api_key,
                "key": api_key,
                "action": "getNumber",
                "country": country_code,
                "service": service_code,
            }
            sep = "&" if "?" in api_url else "?"
            full_url = f"{api_url}{sep}{urllib.parse.urlencode(params)}"
            req = urllib.request.Request(full_url, headers={"User-Agent": "VoltexBot/1.0"})
            with urllib.request.urlopen(req, timeout=8) as resp:
                raw = resp.read().decode("utf-8", errors="ignore").strip()

            try:
                data = json.loads(raw)
                if isinstance(data, dict):
                    for k in ("number", "phone", "data"):
                        val = data.get(k)
                        if isinstance(val, str) and len(val) >= 7:
                            return [val if val.startswith("+") else f"+{val}"]
                        if isinstance(val, dict) and val.get("number"):
                            num = str(val["number"])
                            return [num if num.startswith("+") else f"+{num}"]
            except json.JSONDecodeError:
                pass

            for token in raw.replace(":", " ").replace(",", " ").split():
                clean = token.strip().lstrip("+")
                if clean.isdigit() and 8 <= len(clean) <= 15:
                    return [f"+{clean}"]
        except Exception as exc:
            logger.warning("Voltex API fetch fallback: %s", exc)

    return [
        f"{dial_code}{random.randint(3000000000, 3999999999)}",
        f"{dial_code}{random.randint(3000000000, 3999999999)}",
        f"{dial_code}{random.randint(3000000000, 3999999999)}",
    ]


def save_order(user_id: int, country_name: str, service_name: str, phone_number: str) -> None:
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO orders (user_id, country_name, service_name, phone_number, status)
        VALUES (?, ?, ?, ?, 'ACTIVE')
        """,
        (user_id, country_name, service_name, phone_number),
    )
    conn.commit()
    conn.close()


def main_reply_keyboard(user_id: int = 0) -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton("📞 Get Number"), KeyboardButton("📊 Live Traffic")],
        [KeyboardButton("💳 Wallet"), KeyboardButton("🏆 Leaderboard")],
        [KeyboardButton("🙋‍♂️ Support")],
    ]
    if is_admin(user_id):
        rows.append([KeyboardButton("🛠️ Admin Panel")])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def number_card_keyboard(numbers: list[str], c_idx: int, s_idx: int) -> InlineKeyboardMarkup:
    otp_group = get_setting("otp_group", "https://t.me/")
    rows = []
    for num in numbers:
        rows.append([InlineKeyboardButton(f"☎️ 📋 {num}", callback_data=f"pick_{num}")])

    rows.append(
        [
            InlineKeyboardButton("🔄 Change", callback_data=f"chg_{c_idx}_{s_idx}"),
            InlineKeyboardButton("🌍 Country", callback_data=f"cnt_{c_idx}_{s_idx}"),
        ]
    )
    if otp_group and otp_group.startswith(("http://", "https://")):
        rows.append([InlineKeyboardButton("💬 OTP Group", url=otp_group)])
    else:
        rows.append([InlineKeyboardButton("💬 OTP Group", callback_data="otp_group_info")])

    return InlineKeyboardMarkup(rows)


def country_picker_keyboard(c_idx: int, s_idx: int) -> InlineKeyboardMarkup:
    rows = []
    for idx, (cname, _, dial) in enumerate(COUNTRIES):
        mark = "✅ " if idx == c_idx else ""
        rows.append(
            [InlineKeyboardButton(f"{mark}{cname} ({dial})", callback_data=f"setcnt_{idx}_{s_idx}")]
        )
    return InlineKeyboardMarkup(rows)


def admin_inline_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("👥 Admins", callback_data="adm_list"),
                InlineKeyboardButton("🔌 Voltex API", callback_data="adm_api"),
            ],
            [
                InlineKeyboardButton("📖 Commands", callback_data="adm_cmds"),
            ],
        ]
    )


async def build_number_card(c_idx: int, s_idx: int) -> tuple[str, list[str]]:
    country_name, country_code, dial_code = COUNTRIES[c_idx % len(COUNTRIES)]
    service_name, service_code = SERVICES[s_idx % len(SERVICES)]
    now_str = datetime.datetime.now().strftime("%I:%M:%S %p")
    numbers = fetch_voltex_numbers(
        country_name, country_code, dial_code, service_name, service_code
    )

    card_text = (
        f"┏━━━━━━━━━━━━━━━━━━┓\n"
        f"   🌍 **{country_name} TOP Fresh Number**\n"
        f"   📱 **{service_name} • ⏱️ 10M**\n"
        f"   🔄 **Refreshed:** `{now_str}`\n"
        f"┗━━━━━━━━━━━━━━━━━━┛"
    )
    return card_text, numbers


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not update.message:
        return

    user = update.effective_user
    ensure_user(user.id, user.username or "", user.full_name or "")
    bot_name = get_setting("bot_name", "VOLTEX SMS")
    rate = get_setting("rate_per_otp", "0.30")
    level = get_setting("level_name", "Diamond")
    balance, otp_count = get_user_stats(user.id)

    text = (
        f"⚡ **{bot_name}**\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 **OTPs:** `{otp_count}`  |  💳 **৳{balance:.2f} TK**\n"
        f"💎 **Rate:** `৳{rate}/OTP` ({level})\n\n"
        f"📌 **Important Method:** নিচের মেনু থেকে **Get Number** চাপুন।"
    )
    await update.message.reply_text(
        text,
        parse_mode="Markdown",
        reply_markup=main_reply_keyboard(user.id),
    )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text or not update.effective_user or not update.effective_chat:
        return

    user = update.effective_user
    ensure_user(user.id, user.username or "", user.full_name or "")
    text = update.message.text.strip()
    balance, otp_count = get_user_stats(user.id)
    rate = get_setting("rate_per_otp", "0.30")
    level = get_setting("level_name", "Diamond")

    if text == "📞 Get Number":
        c_idx = int(context.user_data.get("c_idx", 0))
        s_idx = int(context.user_data.get("s_idx", 0))
        card_text, numbers = await build_number_card(c_idx, s_idx)
        await update.message.reply_text(
            card_text,
            parse_mode="Markdown",
            reply_markup=number_card_keyboard(numbers, c_idx, s_idx),
        )

    elif text == "📊 Live Traffic":
        now_str = datetime.datetime.now().strftime("%I:%M:%S %p")
        api_status = "Connected" if get_setting("api_url", "") else "Key Ready (URL Pending)"
        await update.message.reply_text(
            f"📊 **Live Traffic**\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🔌 **Voltex Panel:** `{api_status}`\n"
            f"🕒 **Updated:** `{now_str}`\n"
            f"• Facebook: `Active`\n"
            f"• Telegram: `Active`\n"
            f"• WhatsApp: `Active`",
            parse_mode="Markdown",
        )

    elif text == "💳 Wallet":
        await update.message.reply_text(
            f"💳 **Wallet**\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 **Balance:** `৳{balance:.2f} TK`\n"
            f"📨 **Total OTPs:** `{otp_count}`\n"
            f"👑 **Level:** `{level}` (`৳{rate}/OTP`)",
            parse_mode="Markdown",
        )

    elif text == "🏆 Leaderboard":
        top_users = get_leaderboard(10)
        if not top_users:
            await update.message.reply_text("🏆 এখনো কোনো লিডারবোর্ড ডেটা নেই।")
            return
        lines = ["🏆 **Top OTP Leaderboard**\n"]
        for i, (fname, uname, count_val, bal) in enumerate(top_users, start=1):
            label = fname or (f"@{uname}" if uname else "User")
            lines.append(f"{i}. **{label}** — `{count_val}` OTP (`৳{bal:.2f}`)")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

    elif text == "🙋‍♂️ Support":
        support = get_setting("support_contact", SUPPORT_USERNAME)
        await update.message.reply_text(f"🙋‍♂️ **Support:** {support}", parse_mode="Markdown")

    elif text == "🛠️ Admin Panel":
        if not is_admin(user.id):
            await update.message.reply_text("⛔ শুধুমাত্র অ্যাডমিনের জন্য।")
            return
        await send_admin_panel_message(update.message)

    else:
        await update.message.reply_text(
            "নিচের মেনু থেকে অপশন বেছে নিন অথবা `/start` লিখুন।",
            reply_markup=main_reply_keyboard(user.id),
        )


async def send_admin_panel_message(message) -> None:
    bot_name = get_setting("bot_name", "VOLTEX SMS")
    api_url = get_setting("api_url", "") or "Not Set"
    api_key = get_setting("api_key", VOLTEX_API_KEY)
    support = get_setting("support_contact", SUPPORT_USERNAME)
    masked_key = (
        f"{api_key[:4]}***{api_key[-4:]}"
        if len(api_key) >= 8
        else ("Set" if api_key else "Not Set")
    )
    admins = get_all_admins_db()

    panel_text = (
        f"🛠️ **Admin Panel**\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🤖 **Bot:** {bot_name}\n"
        f"🙋‍♂️ **Support:** {support}\n"
        f"👮 **Admins:** `{len(admins)}`\n"
        f"🔗 **Voltex URL:** `{api_url}`\n"
        f"🔑 **Voltex Key:** `{masked_key}`\n\n"
        f"**Commands:**\n"
        f"• `/setsupport @username`\n"
        f"• `/setapiurl <url>`\n"
        f"• `/setapikey <key>`\n"
        f"• `/addadmin <user_id>`\n"
        f"• `/removeadmin <user_id>`\n"
        f"• `/admins`"
    )
    await message.reply_text(
        panel_text, parse_mode="Markdown", reply_markup=admin_inline_keyboard()
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
        phone_number = data.replace("pick_", "", 1)
        c_idx = int(context.user_data.get("c_idx", 0))
        s_idx = int(context.user_data.get("s_idx", 0))
        country_name, _, _ = COUNTRIES[c_idx % len(COUNTRIES)]
        service_name, _ = SERVICES[s_idx % len(SERVICES)]
        save_order(user.id, country_name, service_name, phone_number)

        await query.message.reply_text(
            f"✅ **Number Selected**\n"
            f"📱 **Number:** `{phone_number}`\n"
            f"🌍 **Country:** {country_name}\n"
            f"📌 **Service:** {service_name}\n"
            f"⏳ **Status:** Waiting for OTP...",
            parse_mode="Markdown",
        )

    elif data.startswith("chg_"):
        parts = data.split("_")
        c_idx = int(parts[1]) if len(parts) > 1 else 0
        s_idx = (int(parts[2]) + 1) % len(SERVICES) if len(parts) > 2 else 1
        context.user_data["c_idx"] = c_idx
        context.user_data["s_idx"] = s_idx
        card_text, numbers = await build_number_card(c_idx, s_idx)
        await query.message.edit_text(
            card_text,
            parse_mode="Markdown",
            reply_markup=number_card_keyboard(numbers, c_idx, s_idx),
        )

    elif data.startswith("cnt_"):
        parts = data.split("_")
        c_idx = int(parts[1]) if len(parts) > 1 else 0
        s_idx = int(parts[2]) if len(parts) > 2 else 0
        await query.message.reply_text(
            "🌍 **Select Country:**",
            parse_mode="Markdown",
            reply_markup=country_picker_keyboard(c_idx, s_idx),
        )

    elif data.startswith("setcnt_"):
        parts = data.split("_")
        c_idx = int(parts[1]) if len(parts) > 1 else 0
        s_idx = int(parts[2]) if len(parts) > 2 else 0
        context.user_data["c_idx"] = c_idx
        context.user_data["s_idx"] = s_idx
        card_text, numbers = await build_number_card(c_idx, s_idx)
        await query.message.reply_text(
            card_text,
            parse_mode="Markdown",
            reply_markup=number_card_keyboard(numbers, c_idx, s_idx),
        )

    elif data == "otp_group_info":
        await query.message.reply_text("💬 OTP Group লিংক সেট করা হয়নি।")

    elif data.startswith("adm_"):
        if not is_admin(user.id):
            await query.message.reply_text("⛔ শুধুমাত্র অ্যাডমিন।")
            return
        if data == "adm_list":
            admins = get_all_admins_db()
            lines = [
                f"• `{aid}` {'👑 Main' if aid == ADMIN_ID else '🛡️ Admin'}"
                for aid in admins
            ]
            await query.message.reply_text(
                "👮 **Admins:**\n\n" + "\n".join(lines), parse_mode="Markdown"
            )
        elif data == "adm_api":
            key_val = get_setting("api_key", VOLTEX_API_KEY)
            await query.message.reply_text(
                f"🔌 **Voltex API Config**\n"
                f"URL: `{get_setting('api_url', '') or 'Not Set'}`\n"
                f"Key: `{key_val}`",
                parse_mode="Markdown",
            )
        elif data == "adm_cmds":
            await query.message.reply_text(
                "`/setsupport @reyad0045`\n"
                "`/setapiurl https://voltex-api-url`\n"
                "`/setapikey MOFUE6IU9NF`\n"
                "`/addadmin 123456789`\n"
                "`/removeadmin 123456789`\n"
                "`/admins`",
                parse_mode="Markdown",
            )


async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not update.message:
        return
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ শুধুমাত্র অ্যাডমিনের জন্য।")
        return
    await send_admin_panel_message(update.message)


async def add_admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not update.message:
        return
    if not is_admin(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("ব্যবহার: `/addadmin 123456789`", parse_mode="Markdown")
        return
    try:
        new_id = int(context.args[0].strip())
    except ValueError:
        await update.message.reply_text("❌ সঠিক User ID দিন।")
        return
    add_admin_db(new_id, update.effective_user.id)
    await update.message.reply_text(f"✅ অ্যাডমিন যুক্ত হয়েছে: `{new_id}`", parse_mode="Markdown")


async def remove_admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not update.message:
        return
    if not is_admin(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("ব্যবহার: `/removeadmin 123456789`", parse_mode="Markdown")
        return
    try:
        target_id = int(context.args[0].strip())
    except ValueError:
        await update.message.reply_text("❌ সঠিক User ID দিন।")
        return
    if target_id == ADMIN_ID:
        await update.message.reply_text("⛔ প্রধান অ্যাডমিন সরানো যাবে না।")
        return
    if remove_admin_db(target_id):
        await update.message.reply_text(
            f"🗑️ `{target_id}` সরানো হয়েছে।", parse_mode="Markdown"
        )
    else:
        await update.message.reply_text("❌ পাওয়া যায়নি।")


async def list_admins_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not update.message:
        return
    if not is_admin(update.effective_user.id):
        return
    admins = get_all_admins_db()
    lines = [
        f"• `{aid}` {'👑 Main' if aid == ADMIN_ID else '🛡️ Admin'}" for aid in admins
    ]
    await update.message.reply_text("👮 **Admins:**\n\n" + "\n".join(lines), parse_mode="Markdown")


async def set_support_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not update.message:
        return
    if not is_admin(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text(
            "ব্যবহার: `/setsupport @reyad0045`", parse_mode="Markdown"
        )
        return
    uname = context.args[0].strip()
    if not uname.startswith("@"):
        uname = f"@{uname}"
    set_setting("support_contact", uname)
    await update.message.reply_text(
        f"✅ Support ইউজারনেম সেট হয়েছে: {uname}", parse_mode="Markdown"
    )


async def set_api_url(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not update.message:
        return
    if not is_admin(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text(
            "ব্যবহার: `/setapiurl https://example.com/api`", parse_mode="Markdown"
        )
        return
    url = context.args[0].strip()
    set_setting("api_url", url)
    await update.message.reply_text(
        f"✅ Voltex API URL সেট হয়েছে:\n`{url}`", parse_mode="Markdown"
    )


async def set_api_key(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not update.message:
        return
    if not is_admin(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text(
            "ব্যবহার: `/setapikey MOFUE6IU9NF`", parse_mode="Markdown"
        )
        return
    key = context.args[0].strip()
    set_setting("api_key", key)
    await update.message.reply_text("✅ Voltex API Key সেট হয়েছে।")


web_app = Flask(__name__)


@web_app.route("/")
def home():
    return "Bot is running!", 200


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
    app.add_handler(CommandHandler("addadmin", add_admin_cmd))
    app.add_handler(CommandHandler("removeadmin", remove_admin_cmd))
    app.add_handler(CommandHandler("admins", list_admins_cmd))
    app.add_handler(CommandHandler("setsupport", set_support_cmd))
    app.add_handler(CommandHandler("setapiurl", set_api_url))
    app.add_handler(CommandHandler("setapikey", set_api_key))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    logger.info("বট চালু হয়েছে...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
