import telebot
from telebot.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
import sqlite3
import requests
import time

# ================= CONFIGURATION =================
BOT_TOKEN = "9178685196:AAGnHHXm7at9Pt8IWIW-zPdiOPkl564dl3U"
MAIN_ADMIN_ID = 2068530616
# =================================================

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")
DB_NAME = "otp_bot.db"

user_states = {}
temp_data = {}

# ================= DATABASE SETUP =================
def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()

    c.execute('''CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        balance REAL DEFAULT 0.0,
        referred_by INTEGER,
        total_otp INTEGER DEFAULT 0,
        last_req REAL DEFAULT 0
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS admins (
        user_id INTEGER PRIMARY KEY
    )''')
    c.execute("INSERT OR IGNORE INTO admins (user_id) VALUES (?)", (MAIN_ADMIN_ID,))

    c.execute('''CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT
    )''')

    default_settings = {
        "withdraw_status": "ON",
        "min_withdraw": "50.0",
        "otp_reward": "0.7",
        "refer_reward": "0.2",
        "cooldown": "10",
        "num_per_req": "3",
        "num_per_share": "1",
        "support_mode": "LIVE",
        "support_link": "https://t.me/YourSupportUsername",
        "withdraw_methods": "Bkash,Nagad,Binance",
        "auto_range": "ON",
        "text_start": "👋 <b>স্বাগতম আমাদের OTP Number Bot-এ!</b>\n\nনিচের মেনু থেকে আপনার পছন্দের অপশন বেছে নিন।",
        "text_get_num": "📱 <b>নম্বর নিতে নিচের দেশ বা প্যানেল সিলেক্ট করুন:</b>",
        "text_traffic": "📊 <b>লাইভ ট্রাফিক এবং স্টক স্ট্যাটাস:</b>",
        "text_refer": "🎁 <b>রেফার করে আয় করুন!</b>\nপ্রতি সফল রেফারে পাবেন {refer_reward} টাকা।\n\nআপনার রেফার লিংক:\n{refer_link}",
        "text_withdraw": "💳 <b>টাকা উত্তোলন (Withdrawal):</b>\nমিনিমাম উইথড্র: {min_withdraw} টাকা।\nআপনার ব্যালেন্স: {balance} টাকা।",
        "text_support": "🎧 <b>সাপোর্ট সেন্টার:</b>\nযেকোনো সমস্যায় নিচের অপশন ব্যবহার করুন।"
    }
    for k, v in default_settings.items():
        c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, v))

    c.execute('''CREATE TABLE IF NOT EXISTS numbers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        country TEXT,
        phone TEXT UNIQUE,
        status TEXT DEFAULT 'unused',
        used_by INTEGER
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS api_panels (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        panel_name TEXT,
        api_key TEXT,
        get_num_url TEXT,
        get_otp_url TEXT,
        status TEXT DEFAULT 'ACTIVE'
    )''')

    conn.commit()
    conn.close()

def get_setting(key):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT value FROM settings WHERE key=?", (key,))
    res = c.fetchone()
    conn.close()
    return res[0] if res else ""

def set_setting(key, value):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("REPLACE INTO settings (key, value) VALUES (?, ?)", (key, str(value)))
    conn.commit()
    conn.close()

def is_admin(user_id):
    if int(user_id) == MAIN_ADMIN_ID:
        return True
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT 1 FROM admins WHERE user_id=?", (user_id,))
    res = c.fetchone()
    conn.close()
    return res is not None

# ================= KEYBOARDS =================
def main_menu_kb(user_id):
    kb = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add(
        KeyboardButton("📱 GET NUMBER"),
        KeyboardButton("🔍 Search Number"),
        KeyboardButton("📊 TRAFFIC"),
        KeyboardButton("💰 BALANCE"),
        KeyboardButton("🎁 REFER"),
        KeyboardButton("💳 WITHDRAWAL"),
        KeyboardButton("🎧 SUPPORT")
    )
    if is_admin(user_id):
        kb.add(KeyboardButton("⚙️ Admin Control Panel"))
    return kb

def admin_main_kb():
    kb = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add(
        KeyboardButton("📤 Upload Number"),
        KeyboardButton("📢 Broadcast Message"),
        KeyboardButton("✅ Used Numbers"),
        KeyboardButton("📥 Unused Numbers"),
        KeyboardButton("⚙️ System Settings"),
        KeyboardButton("🗑 Delete Files"),
        KeyboardButton("🌐 API Panel Manager"),
        KeyboardButton("🎨 Edit Bot Texts"),
        KeyboardButton("🎧 Support System"),
        KeyboardButton("🏆 Leaderboard System"),
        KeyboardButton("🔙 User Menu")
    )
    return kb

# ================= START & USER HANDLERS =================
@bot.message_handler(commands=['start'])
def cmd_start(message):
    user_id = message.from_user.id
    username = message.from_user.username or "NoUsername"
    user_states[user_id] = None

    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT user_id FROM users WHERE user_id=?", (user_id,))
    if not c.fetchone():
        args = message.text.split()
        ref_id = int(args[1]) if len(args) > 1 and args[1].isdigit() else None
        c.execute("INSERT INTO users (user_id, username, referred_by) VALUES (?, ?, ?)", (user_id, username, ref_id))
        if ref_id and ref_id != user_id:
            ref_bonus = float(get_setting("refer_reward"))
            c.execute("UPDATE users SET balance = balance + ? WHERE user_id=?", (ref_bonus, ref_id))
            try:
                bot.send_message(ref_id, f"🎉 নতুন রেফার সফল হয়েছে! আপনার ব্যালেন্সে <b>{ref_bonus}</b> টাকা যোগ হয়েছে।")
            except:
                pass
    conn.commit()
    conn.close()

    bot.send_message(user_id, get_setting("text_start"), reply_markup=main_menu_kb(user_id))

@bot.message_handler(func=lambda m: m.text == "🔙 User Menu")
def back_to_user(message):
    user_states[message.from_user.id] = None
    bot.send_message(message.chat.id, "ইউজার মেনুতে ফিরে আসা হয়েছে।", reply_markup=main_menu_kb(message.from_user.id))

# --- GET NUMBER ---
@bot.message_handler(func=lambda m: m.text == "📱 GET NUMBER")
def user_get_number(message):
    user_id = message.from_user.id
    cooldown = int(get_setting("cooldown"))

    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT last_req FROM users WHERE user_id=?", (user_id,))
    row = c.fetchone()
    last_req = row[0] if row else 0

    if time.time() - last_req < cooldown:
        wait = int(cooldown - (time.time() - last_req))
        conn.close()
        return bot.send_message(user_id, f"⏳ অনুগ্রহ করে <b>{wait} সেকেন্ড</b> অপেক্ষা করুন।")

    c.execute("SELECT country, COUNT(*) FROM numbers WHERE status='unused' GROUP BY country")
    countries = c.fetchall()
    c.execute("SELECT id, panel_name FROM api_panels WHERE status='ACTIVE'")
    panels = c.fetchall()
    conn.close()

    kb = InlineKeyboardMarkup(row_width=2)
    for country, count in countries:
        kb.add(InlineKeyboardButton(f"🌍 {country} ({count})", callback_data=f"get_local_{country}"))
    for pid, pname in panels:
        kb.add(InlineKeyboardButton(f"⚡ {pname} (API)", callback_data=f"get_api_{pid}"))

    if not countries and not panels:
        return bot.send_message(user_id, "❌ বর্তমানে কোনো নম্বর স্টকে নেই।")

    bot.send_message(user_id, get_setting("text_get_num"), reply_markup=kb)

@bot.callback_query_handler(func=lambda call: call.data.startswith("get_local_"))
def handle_local_number(call):
    country = call.data.replace("get_local_", "")
    user_id = call.from_user.id

    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT id, phone FROM numbers WHERE country=? AND status='unused' LIMIT 1", (country,))
    row = c.fetchone()
    if not row:
        conn.close()
        return bot.answer_callback_query(call.id, "❌ এই দেশের নম্বর শেষ হয়ে গেছে!", show_alert=True)

    num_id, phone = row
    c.execute("UPDATE numbers SET status='used', used_by=? WHERE id=?", (user_id, num_id))
    c.execute("UPDATE users SET last_req=? WHERE user_id=?", (time.time(), user_id))
    conn.commit()
    conn.close()

    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("✅ OTP Received (Claim Reward)", callback_data=f"claim_local_{num_id}"))
    bot.edit_message_text(
        f"✅ <b>নম্বর পাওয়া গেছে!</b>\n\n🌍 দেশ: <b>{country}</b>\n📞 নম্বর: <code>{phone}</code>\n\nকোড পাওয়ার পর নিচের বাটনে চাপুন:",
        call.message.chat.id, call.message.message_id, reply_markup=kb
    )

@bot.callback_query_handler(func=lambda call: call.data.startswith("get_api_"))
def handle_api_number(call):
    pid = call.data.replace("get_api_", "")
    user_id = call.from_user.id

    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT panel_name, api_key, get_num_url, get_otp_url FROM api_panels WHERE id=?", (pid,))
    panel = c.fetchone()
    if not panel:
        conn.close()
        return bot.answer_callback_query(call.id, "❌ প্যানেল পাওয়া যায়নি!")

    pname, api_key, get_num_url, get_otp_url = panel
    url = get_num_url.replace("{api_key}", api_key)

    try:
        res = requests.get(url, timeout=10).text
        c.execute("UPDATE users SET last_req=? WHERE user_id=?", (time.time(), user_id))
        conn.commit()

        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("🔄 Check OTP", callback_data=f"chk_api_{pid}"))
        bot.edit_message_text(
            f"⚡ <b>প্যানেল: {pname}</b>\n\n📡 <b>API Response:</b>\n<code>{res}</code>\n\n<i>(নম্বর কপি করে কোড পাঠান এবং Check OTP চাপুন)</i>",
            call.message.chat.id, call.message.message_id, reply_markup=kb
        )
    except Exception as e:
        bot.answer_callback_query(call.id, f"API Error: {str(e)[:40]}", show_alert=True)
    conn.close()

@bot.callback_query_handler(func=lambda call: call.data.startswith("claim_local_"))
def claim_local_otp(call):
    reward = float(get_setting("otp_reward"))
    user_id = call.from_user.id
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("UPDATE users SET balance = balance + ?, total_otp = total_otp + 1 WHERE user_id=?", (reward, user_id))
    conn.commit()
    conn.close()
    bot.edit_message_text(f"🎉 অভিনন্দন! আপনার ব্যালেন্সে <b>{reward}</b> টাকা যুক্ত হয়েছে।", call.message.chat.id, call.message.message_id)

# --- BALANCE, REFER, TRAFFIC, WITHDRAWAL ---
@bot.message_handler(func=lambda m: m.text == "💰 BALANCE")
def user_balance(message):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT balance, total_otp FROM users WHERE user_id=?", (message.from_user.id,))
    row = c.fetchone()
    bal, otps = row if row else (0.0, 0)
    conn.close()
    bot.send_message(message.chat.id, f"👤 <b>আপনার প্রোফাইল:</b>\n\n💰 বর্তমান ব্যালেন্স: <b>{bal:.2f} টাকা</b>\n📩 মোট সফল OTP: <b>{otps} টি</b>")

@bot.message_handler(func=lambda m: m.text == "🎁 REFER")
def user_refer(message):
    bot_username = bot.get_me().username
    ref_link = f"https://t.me/{bot_username}?start={message.from_user.id}"
    txt = get_setting("text_refer").format(refer_reward=get_setting("refer_reward"), refer_link=ref_link)
    bot.send_message(message.chat.id, txt)

@bot.message_handler(func=lambda m: m.text == "📊 TRAFFIC")
def user_traffic(message):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM numbers WHERE status='unused'")
    unused = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM numbers WHERE status='used'")
    used = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM users")
    total_users = c.fetchone()[0]
    conn.close()

    txt = (f"{get_setting('text_traffic')}\n\n"
           f"📥 স্টকে ফ্রি নম্বর: <b>{unused}</b>\n"
           f"✅ ব্যবহৃত নম্বর: <b>{used}</b>\n"
           f"👥 মোট ইউজার: <b>{total_users}</b>")
    bot.send_message(message.chat.id, txt)

@bot.message_handler(func=lambda m: m.text == "💳 WITHDRAWAL")
def user_withdraw(message):
    if get_setting("withdraw_status") != "ON":
        return bot.send_message(message.chat.id, "⚠️ বর্তমানে উইথড্র সাময়িকভাবে বন্ধ আছে।")

    user_id = message.from_user.id
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT balance FROM users WHERE user_id=?", (user_id,))
    row = c.fetchone()
    bal = row[0] if row else 0.0
    conn.close()

    min_w = float(get_setting("min_withdraw"))
    txt = get_setting("text_withdraw").format(min_withdraw=min_w, balance=f"{bal:.2f}")

    if bal < min_w:
        return bot.send_message(user_id, f"{txt}\n\n❌ আপনার পর্যাপ্ত ব্যালেন্স নেই।")

    methods = get_setting("withdraw_methods").split(",")
    kb = InlineKeyboardMarkup()
    for m in methods:
        kb.add(InlineKeyboardButton(f"💳 {m.strip()}", callback_data=f"wd_{m.strip()}"))
    bot.send_message(user_id, f"{txt}\n\nপেমেন্ট মেথড সিলেক্ট করুন:", reply_markup=kb)

@bot.callback_query_handler(func=lambda call: call.data.startswith("wd_"))
def process_wd_method(call):
    method = call.data.replace("wd_", "")
    user_id = call.from_user.id
    user_states[user_id] = f"wd_number_{method}"
    bot.send_message(user_id, f"📝 আপনার <b>{method}</b> অ্যাকাউন্ট নম্বর এবং পরিমাণ লিখুন (যেমন: <code>017XXXXXXXX - 50</code>):")

# --- SUPPORT SYSTEM ---
@bot.message_handler(func=lambda m: m.text == "🎧 SUPPORT")
def user_support(message):
    mode = get_setting("support_mode")
    link = get_setting("support_link")
    kb = InlineKeyboardMarkup()

    if mode == "NORMAL":
        kb.add(InlineKeyboardButton("📞 Contact Support", url=link))
        bot.send_message(message.chat.id, get_setting("text_support"), reply_markup=kb)
    elif mode == "LIVE":
        user_states[message.from_user.id] = "live_support"
        bot.send_message(message.chat.id, "💬 <b>Live Chat চালু হয়েছে!</b>\nআপনার সমস্যা লিখে পাঠান, সরাসরি অ্যাডমিনের কাছে চলে যাবে:")
    else:
        kb.add(InlineKeyboardButton("💬 Live Chat (বটের ভেতরে)", callback_data="start_live_chat"))
        kb.add(InlineKeyboardButton("📞 Contact Support (সরাসরি আইডি)", url=link))
        bot.send_message(message.chat.id, get_setting("text_support"), reply_markup=kb)

@bot.callback_query_handler(func=lambda call: call.data == "start_live_chat")
def start_live_chat_cb(call):
    user_states[call.from_user.id] = "live_support"
    bot.send_message(call.message.chat.id, "💬 আপনার মেসেজটি লিখুন:")

# ================= ADMIN CONTROL PANEL =================
@bot.message_handler(func=lambda m: m.text == "⚙️ Admin Control Panel" and is_admin(m.from_user.id))
def admin_panel(message):
    bot.send_message(message.chat.id, "👑 <b>Admin Control Panel-এ স্বাগতম!</b>", reply_markup=admin_main_kb())

@bot.message_handler(func=lambda m: m.text == "⚙️ System Settings" and is_admin(m.from_user.id))
def system_settings_menu(message):
    w_stat = get_setting("withdraw_status")
    min_w = get_setting("min_withdraw")
    otp_r = get_setting("otp_reward")
    ref_r = get_setting("refer_reward")
    cd = get_setting("cooldown")
    n_req = get_setting("num_per_req")
    n_shr = get_setting("num_per_share")

    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(InlineKeyboardButton(f"💳 WITHDRAW: {w_stat}", callback_data="tog_withdraw"))
    kb.add(
        InlineKeyboardButton(f"💸 MIN WITHDRAW: {min_w}", callback_data="set_min_withdraw"),
        InlineKeyboardButton(f"💎 OTP REWARD: {otp_r}", callback_data="set_otp_reward"),
        InlineKeyboardButton(f"🎁 REFER REWARD: {ref_r}", callback_data="set_refer_reward"),
        InlineKeyboardButton(f"⏳ COOLDOWN: {cd}s", callback_data="set_cooldown"),
        InlineKeyboardButton(f"📞 NUM/REQ: {n_req}", callback_data="set_num_per_req"),
        InlineKeyboardButton(f"🔢 NUM/SHARE: {n_shr}", callback_data="set_num_per_share"),
        InlineKeyboardButton("🏦 W. METHODS", callback_data="set_withdraw_methods"),
        InlineKeyboardButton("➕ Add Admin", callback_data="add_new_admin")
    )
    bot.send_message(message.chat.id, "⚙️ <b>SYSTEM CONTROL PANEL</b>\nযেকোনো বাটনে ক্লিক করে ভ্যালু পরিবর্তন করুন:", reply_markup=kb)

@bot.callback_query_handler(func=lambda call: call.data == "tog_withdraw" and is_admin(call.from_user.id))
def toggle_withdraw(call):
    curr = get_setting("withdraw_status")
    new_val = "OFF" if curr == "ON" else "ON"
    set_setting("withdraw_status", new_val)
    bot.answer_callback_query(call.id, f"Withdraw {new_val} করা হয়েছে!")
    bot.delete_message(call.message.chat.id, call.message.message_id)
    system_settings_menu(call.message)

@bot.callback_query_handler(func=lambda call: call.data.startswith("set_") and is_admin(call.from_user.id))
def ask_setting_value(call):
    key = call.data.replace("set_", "")
    user_states[call.from_user.id] = f"edit_setting_{key}"
    bot.send_message(call.message.chat.id, f"✏️ <b>{key.upper()}</b> এর নতুন মান (Value) লিখে পাঠান:")

@bot.callback_query_handler(func=lambda call: call.data == "add_new_admin" and is_admin(call.from_user.id))
def ask_new_admin(call):
    user_states[call.from_user.id] = "adding_admin"
    bot.send_message(call.message.chat.id, "👤 নতুন অ্যাডমিনের <b>Telegram User ID</b> পাঠান:")

@bot.message_handler(func=lambda m: m.text == "🌐 API Panel Manager" and is_admin(m.from_user.id))
def api_panel_manager(message):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT id, panel_name, status FROM api_panels")
    panels = c.fetchall()
    conn.close()

    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("➕ Add New Panel API", callback_data="api_add_new"),
        InlineKeyboardButton("🗑 View/Del Panels", callback_data="api_view_del")
    )
    txt = f"⚡ <b>Universal Number Panel Manager</b>\n\nমোট যুক্ত করা প্যানেল: <b>{len(panels)} টি</b>\nএখান থেকে আপনি যেকোনো প্যানেলের API যুক্ত করতে পারবেন।"
    bot.send_message(message.chat.id, txt, reply_markup=kb)

@bot.callback_query_handler(func=lambda call: call.data == "api_add_new" and is_admin(call.from_user.id))
def start_add_panel(call):
    user_states[call.from_user.id] = "panel_step_1_name"
    bot.send_message(call.message.chat.id, "1️⃣ প্যানেলের নাম লিখুন (যেমন: <code>Voltx</code> বা <code>5sim</code>):")

@bot.callback_query_handler(func=lambda call: call.data == "api_view_del" and is_admin(call.from_user.id))
def view_del_panels(call):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT id, panel_name FROM api_panels")
    panels = c.fetchall()
    conn.close()

    if not panels:
        return bot.answer_callback_query(call.id, "কোনো প্যানেল যুক্ত করা নেই!", show_alert=True)
    kb = InlineKeyboardMarkup()
    for pid, pname in panels:
        kb.add(InlineKeyboardButton(f"❌ Delete {pname}", callback_data=f"del_panel_{pid}"))
    bot.send_message(call.message.chat.id, "ডিলিট করতে প্যানেলের নামে ক্লিক করুন:", reply_markup=kb)

@bot.callback_query_handler(func=lambda call: call.data.startswith("del_panel_") and is_admin(call.from_user.id))
def delete_panel(call):
    pid = call.data.replace("del_panel_", "")
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("DELETE FROM api_panels WHERE id=?", (pid,))
    conn.commit()
    conn.close()
    bot.edit_message_text("✅ প্যানেলটি সফলভাবে ডিলিট করা হয়েছে।", call.message.chat.id, call.message.message_id)

@bot.message_handler(func=lambda m: m.text == "🎧 Support System" and is_admin(m.from_user.id))
def admin_support_system(message):
    mode = get_setting("support_mode")
    kb = InlineKeyboardMarkup(row_width=3)
    kb.add(
        InlineKeyboardButton(f"Normal {'✅' if mode=='NORMAL' else ''}", callback_data="sup_mode_NORMAL"),
        InlineKeyboardButton(f"Live {'✅' if mode=='LIVE' else ''}", callback_data="sup_mode_LIVE"),
        InlineKeyboardButton(f"Both {'✅' if mode=='BOTH' else ''}", callback_data="sup_mode_BOTH")
    )
    kb.add(InlineKeyboardButton("🔗 Edit Support Link", callback_data="set_support_link"))
    txt = (f"🎧 <b>Support System</b>\nMode: <b>{mode}</b>\n\n"
           "• <b>Normal:</b> ইউজার সরাসরি সাপোর্ট লিংক পাবে।\n"
           "• <b>Live:</b> বটের ভেতরেই মেসেজ আদান-প্রদান হবে।\n"
           "• <b>Both:</b> দুটি বাটনই দেখাবে।")
    bot.send_message(message.chat.id, txt, reply_markup=kb)

@bot.callback_query_handler(func=lambda call: call.data.startswith("sup_mode_") and is_admin(call.from_user.id))
def change_sup_mode(call):
    new_mode = call.data.replace("sup_mode_", "")
    set_setting("support_mode", new_mode)
    bot.answer_callback_query(call.id, f"Support Mode: {new_mode}")
    bot.delete_message(call.message.chat.id, call.message.message_id)
    admin_support_system(call.message)

@bot.message_handler(func=lambda m: m.text == "🎨 Edit Bot Texts" and is_admin(m.from_user.id))
def edit_texts_menu(message):
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("✏️ Edit /start Menu", callback_data="set_text_start"),
        InlineKeyboardButton("📱 Edit GET NUMBER", callback_data="set_text_get_num"),
        InlineKeyboardButton("📊 Edit TRAFFIC", callback_data="set_text_traffic"),
        InlineKeyboardButton("🎁 Edit Refer", callback_data="set_text_refer"),
        InlineKeyboardButton("💳 Edit WITHDRAWAL", callback_data="set_text_withdraw"),
        InlineKeyboardButton("🎧 Edit SUPPORT", callback_data="set_text_support")
    )
    bot.send_message(message.chat.id, "🎨 <b>বটের যেকোনো মেনুর লেখা পরিবর্তন করুন:</b>\n(HTML ট্যাগ ও ইমোজি ব্যবহার করতে পারবেন)", reply_markup=kb)

@bot.message_handler(func=lambda m: m.text == "📤 Upload Number" and is_admin(m.from_user.id))
def upload_number_start(message):
    user_states[message.from_user.id] = "upload_country"
    bot.send_message(message.chat.id, "🌍 কোন দেশের নম্বর আপলোড করবেন? দেশের নাম লিখুন (যেমন: <code>USA</code> বা <code>Bangladesh</code>):")

@bot.message_handler(func=lambda m: m.text in ["✅ Used Numbers", "📥 Unused Numbers"] and is_admin(m.from_user.id))
def check_stock_numbers(message):
    status = "used" if "Used" in message.text else "unused"
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT country, COUNT(*) FROM numbers WHERE status=? GROUP BY country", (status,))
    rows = c.fetchall()
    conn.close()

    if not rows:
        return bot.send_message(message.chat.id, f"কোনো {status} নম্বর পাওয়া যায়নি।")
    report = "\n".join([f"• <b>{r[0]}:</b> {r[1]} টি" for r in rows])
    bot.send_message(message.chat.id, f"📊 <b>{status.upper()} NUMBERS REPORT:</b>\n\n{report}")

@bot.message_handler(func=lambda m: m.text == "🗑 Delete Files" and is_admin(m.from_user.id))
def delete_all_numbers(message):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("DELETE FROM numbers")
    conn.commit()
    conn.close()
    bot.send_message(message.chat.id, "🗑 ডাটাবেজ থেকে সব নম্বর স্টক মুছে ফেলা হয়েছে।")

@bot.message_handler(func=lambda m: m.text == "📢 Broadcast Message" and is_admin(m.from_user.id))
def broadcast_prompt(message):
    user_states[message.from_user.id] = "broadcasting"
    bot.send_message(message.chat.id, "📢 সব ইউজারকে পাঠানোর জন্য আপনার মেসেজটি লিখুন:")

@bot.message_handler(func=lambda m: m.text == "🏆 Leaderboard System" and is_admin(m.from_user.id))
def show_leaderboard(message):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT user_id, username, total_otp, balance FROM users ORDER BY total_otp DESC LIMIT 10")
    top = c.fetchall()
    conn.close()

    txt = "🏆 <b>TOP 10 OTP EARNERS:</b>\n\n"
    for idx, (uid, uname, otps, bal) in enumerate(top, 1):
        txt += f"{idx}. @{uname} (<code>{uid}</code>) — <b>{otps} OTP</b> ({bal:.2f}৳)\n"
    bot.send_message(message.chat.id, txt)

# ================= DYNAMIC STATE & INPUT HANDLER =================
@bot.message_handler(content_types=['text', 'document'])
def handle_all_states(message):
    user_id = message.from_user.id
    state = user_states.get(user_id)

    if is_admin(user_id) and message.reply_to_message:
        orig_text = message.reply_to_message.text or ""
        if "User ID:" in orig_text:
            try:
                target_uid = int(orig_text.split("User ID:")[1].split()[0])
                bot.send_message(target_uid, f"🎧 <b>Admin Reply:</b>\n\n{message.text}")
                return bot.send_message(user_id, "✅ ইউজারকে রিপ্লাই পাঠানো হয়েছে।")
            except:
                pass

    if not state:
        return

    if state.startswith("edit_setting_") and is_admin(user_id):
        key = state.replace("edit_setting_", "")
        set_setting(key, message.text)
        user_states[user_id] = None
        bot.send_message(user_id, f"✅ <b>{key.upper()}</b> আপডেট হয়ে <code>{message.text}</code> হয়েছে!")

    elif state == "adding_admin" and is_admin(user_id):
        if message.text.isdigit():
            conn = sqlite3.connect(DB_NAME)
            c = conn.cursor()
            c.execute("INSERT OR IGNORE INTO admins (user_id) VALUES (?)", (int(message.text),))
            conn.commit()
            conn.close()
            bot.send_message(user_id, f"✅ <code>{message.text}</code> কে নতুন অ্যাডমিন করা হয়েছে।")
        user_states[user_id] = None

    elif state == "panel_step_1_name" and is_admin(user_id):
        temp_data[user_id] = {"name": message.text}
        user_states[user_id] = "panel_step_2_key"
        bot.send_message(user_id, "2️⃣ প্যানেলের <b>API Key</b> দিন:")

    elif state == "panel_step_2_key" and is_admin(user_id):
        temp_data[user_id]["key"] = message.text
        user_states[user_id] = "panel_step_3_num_url"
        bot.send_message(user_id, "3️⃣ <b>Get Number API URL</b> দিন:\n<i>(API Key এর জায়গায় <code>{api_key}</code> লিখুন।)</i>")

    elif state == "panel_step_3_num_url" and is_admin(user_id):
        temp_data[user_id]["num_url"] = message.text
        user_states[user_id] = "panel_step_4_otp_url"
        bot.send_message(user_id, "4️⃣ <b>Get OTP Status URL</b> দিন (অথবা <code>skip</code> লিখুন):")

    elif state == "panel_step_4_otp_url" and is_admin(user_id):
        data = temp_data[user_id]
        otp_url = "" if message.text.lower() == "skip" else message.text
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("INSERT INTO api_panels (panel_name, api_key, get_num_url, get_otp_url) VALUES (?, ?, ?, ?)",
                  (data["name"], data["key"], data["num_url"], otp_url))
        conn.commit()
        conn.close()
        user_states[user_id] = None
        bot.send_message(user_id, f"🎉 <b>{data['name']}</b> প্যানেলটি সফলভাবে যুক্ত হয়েছে!")

    elif state == "upload_country" and is_admin(user_id):
        temp_data[user_id] = message.text.strip()
        user_states[user_id] = "upload_numbers_list"
        bot.send_message(user_id, f"📥 <b>{message.text}</b> এর নম্বরগুলো নিচে পেস্ট করুন অথবা একটি <code>.txt</code> ফাইল আপলোড করুন:")

    elif state == "upload_numbers_list" and is_admin(user_id):
        country = temp_data[user_id]
        raw_text = ""
        if message.document:
            file_info = bot.get_file(message.document.file_id)
            raw_text = bot.download_file(file_info.file_path).decode("utf-8", errors="ignore")
        elif message.text:
            raw_text = message.text

        nums = [line.strip() for line in raw_text.splitlines() if line.strip()]
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        added = 0
        for n in nums:
            try:
                c.execute("INSERT INTO numbers (country, phone) VALUES (?, ?)", (country, n))
                added += 1
            except sqlite3.IntegrityError:
                pass
        conn.commit()
        conn.close()
        user_states[user_id] = None
        bot.send_message(user_id, f"✅ সফলভাবে <b>{added} টি</b> নতুন নম্বর ({country}) স্টকে যোগ করা হয়েছে!")

    elif state == "broadcasting" and is_admin(user_id):
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("SELECT user_id FROM users")
        users = c.fetchall()
        conn.close()
        sent = 0
        for (uid,) in users:
            try:
                bot.send_message(uid, f"📢 <b>নোটিশ:</b>\n\n{message.text}")
                sent += 1
            except:
                pass
        user_states[user_id] = None
        bot.send_message(user_id, f"✅ মোট <b>{sent} জন</b> ইউজারকে মেসেজ পাঠানো হয়েছে।")

    elif state.startswith("wd_number_"):
        method = state.replace("wd_number_", "")
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("SELECT balance FROM users WHERE user_id=?", (user_id,))
        row = c.fetchone()
        bal = row[0] if row else 0.0
        c.execute("UPDATE users SET balance = 0 WHERE user_id=?", (user_id,))
        c.execute("SELECT user_id FROM admins")
        admins = c.fetchall()
        conn.commit()
        conn.close()

        user_states[user_id] = None
        bot.send_message(user_id, "✅ আপনার উইথড্র রিকোয়েস্ট অ্যাডমিনের কাছে পাঠানো হয়েছে।")
        for (aid,) in admins:
            try:
                bot.send_message(aid, f"💸 <b>নতুন উইথড্র রিকোয়েস্ট!</b>\n\n👤 User ID: <code>{user_id}</code>\n🏦 Method: <b>{method}</b>\n💰 Balance: <b>{bal:.2f}</b>\n📝 Details: <code>{message.text}</code>")
            except:
                pass

    elif state == "live_support":
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("SELECT user_id FROM admins")
        admins = c.fetchall()
        conn.close()

        for (aid,) in admins:
            try:
                bot.send_message(aid, f"📩 <b>New Support Message</b>\nUser ID: <code>{user_id}</code>\n\n💬 {message.text}\n\n<i>(রিপ্লাই দিতে এই মেসেজটি Swipe/Reply করে লিখুন)</i>")
            except:
                pass
        bot.send_message(user_id, "✅ আপনার মেসেজ অ্যাডমিনের কাছে পাঠানো হয়েছে।")

if __name__ == "__main__":
    init_db()
    print("Bot is running...")
    bot.infinity_polling()
