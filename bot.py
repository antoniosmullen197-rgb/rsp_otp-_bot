import asyncio
import base64
import datetime
import json
import logging
import os
import re
import threading
import urllib.error
import urllib.parse
import urllib.request
import certifi
from flask import Flask
from pymongo import ASCENDING, DESCENDING, MongoClient, ReturnDocument
from pymongo.errors import DuplicateKeyError
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

MONGO_URI = os.environ.get("MONGO_URI", "").strip()
MONGO_DB_NAME = os.environ.get("MONGO_DB_NAME", "rsp_otp_bot").strip()

ENV_API_URL = os.environ.get("VOLTEX_API_URL", "")
ENV_API_KEY = os.environ.get("VOLTEX_API_KEY", "")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

BASE_COUNTRIES: dict[str, dict] = {}

_mongo_client: MongoClient | None = None
_db = None


def get_db():
    global _mongo_client, _db
    if _db is not None:
        return _db
    if not MONGO_URI:
        raise RuntimeError(
            "MONGO_URI environment variable is not set! Add MONGO_URI in Render Environment Variables."
        )
    _mongo_client = MongoClient(
        MONGO_URI,
        tls=True,
        tlsAllowInvalidCertificates=True,
        serverSelectionTimeoutMS=10000,
    )
    _db = _mongo_client[MONGO_DB_NAME]
    return _db


def get_next_sequence(seq_name: str) -> int:
    db = get_db()
    doc = db.counters.find_one_and_update(
        {"_id": seq_name},
        {"$inc": {"seq": 1}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return int(doc["seq"])


def init_db() -> None:
    db = get_db()

    db.settings.create_index([("key", ASCENDING)], unique=True)
    db.admins.create_index([("user_id", ASCENDING)], unique=True)
    db.custom_countries.create_index([("country_code", ASCENDING)], unique=True)
    db.country_rates.create_index([("country_code", ASCENDING)], unique=True)
    db.users.create_index([("user_id", ASCENDING)], unique=True)
    db.daily_stats.create_index(
        [("stat_date", ASCENDING), ("user_id", ASCENDING)], unique=True
    )
    db.orders.create_index([("id", ASCENDING)], unique=True)
    db.orders.create_index([("status", ASCENDING), ("created_at", ASCENDING)])
    db.numbers.create_index([("phone", ASCENDING)], unique=True)
    db.numbers.create_index([("country", ASCENDING), ("status", ASCENDING)])
    db.api_panels.create_index([("id", ASCENDING)], unique=True)

    db.admins.update_one(
        {"user_id": int(MAIN_ADMIN_ID)},
        {"$setOnInsert": {"user_id": int(MAIN_ADMIN_ID)}},
        upsert=True,
    )

    removed_codes = ["it", "bd", "us", "gb", "in"]
    db.country_rates.delete_many({"country_code": {"$in": removed_codes}})
    db.custom_countries.delete_many({"country_code": {"$in": removed_codes}})
    db.users.update_many(
        {"selected_country": {"$in": removed_codes}},
        {"$set": {"selected_country": ""}},
    )

    for code, info in BASE_COUNTRIES.items():
        db.country_rates.update_one(
            {"country_code": str(code).lower()},
            {"$setOnInsert": {"country_code": str(code).lower(), "rate": float(info["rate"])}},
            upsert=True,
        )

    default_settings = {
        "bot_name": "RSP SMS",
        "api_url": ENV_API_URL,
        "api_key": ENV_API_KEY,
        "voltex_rid": "",
        "default_service": "Facebook",
        "otp_group_link": "https://t.me/reyad0045",
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
        "text_get_num": "🟢 **Ready • নিচের অপশন থেকে নম্বর নিন:**",
        "text_traffic": "📊 **Real-Time Stock & Order Stats:**",
        "text_refer": "🎁 **রেফার করে আয় করুন!**\nপ্রতি সফল রেফারে পাবেন `৳{refer_reward}`।\n\nআপনার রেফার লিংক:\n`{refer_link}`",
        "text_withdraw": "💳 **টাকা উত্তোলন (Withdrawal):**\nমিনিমাম উইথড্র: `৳{min_withdraw}`\nআপনার ব্যালেন্স: `৳{balance}`",
        "text_support": "🎧 **সাপোর্ট সেন্টার:**\nযেকোনো সমস্যায় নিচের অপশন ব্যবহার করুন।",
        "last_winner_date": "",
    }
    for key, val in default_settings.items():
        db.settings.update_one(
            {"key": key},
            {"$setOnInsert": {"key": key, "value": val}},
            upsert=True,
        )

    if os.environ.get("VOLTEX_API_URL"):
        db.settings.update_one(
            {"key": "api_url"},
            {"$set": {"key": "api_url", "value": os.environ["VOLTEX_API_URL"].strip()}},
            upsert=True,
        )
    if os.environ.get("VOLTEX_API_KEY"):
        db.settings.update_one(
            {"key": "api_key"},
            {"$set": {"key": "api_key", "value": os.environ["VOLTEX_API_KEY"].strip()}},
            upsert=True,
        )


def get_all_countries() -> dict[str, dict]:
    db = get_db()
    countries = {k: dict(v) for k, v in BASE_COUNTRIES.items()}

    for doc in db.custom_countries.find():
        c_key = str(doc.get("country_code", "")).strip().lower()
        if not c_key:
            continue
        countries[c_key] = {
            "name": str(doc.get("name") or c_key.upper()),
            "flag": str(doc.get("flag") or "🌍"),
            "dial": str(doc.get("dial") or "+1"),
            "service": str(doc.get("service") or "Facebook"),
            "rate": float(doc.get("rate") if doc.get("rate") is not None else 15.0),
            "is_custom": True,
        }

    for doc in db.country_rates.find():
        c_key = str(doc.get("country_code", "")).strip().lower()
        rate = doc.get("rate")
        if c_key in countries and rate is not None:
            countries[c_key]["rate"] = float(rate)

    return countries


def add_or_update_country(
    country_code: str,
    name: str,
    dial: str = "+1",
    rate: float = 15.0,
    flag: str = "🌍",
    service: str = "Facebook",
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
    clean_service = str(service or "Facebook").strip() or "Facebook"
    clean_rate = max(0.0, float(rate))

    db = get_db()
    db.custom_countries.update_one(
        {"country_code": clean_code},
        {
            "$set": {
                "country_code": clean_code,
                "name": clean_name,
                "flag": clean_flag,
                "dial": clean_dial,
                "service": clean_service,
                "rate": clean_rate,
            }
        },
        upsert=True,
    )
    db.country_rates.update_one(
        {"country_code": clean_code},
        {"$set": {"country_code": clean_code, "rate": clean_rate}},
        upsert=True,
    )
    return True, clean_code


def delete_custom_country(country_code: str) -> bool:
    c_key = str(country_code or "").strip().lower()
    if not c_key or c_key in BASE_COUNTRIES:
        return False
    db = get_db()
    res = db.custom_countries.delete_one({"country_code": c_key})
    db.country_rates.delete_one({"country_code": c_key})
    return res.deleted_count > 0


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
    db = get_db()
    doc = db.settings.find_one({"key": key})
    if doc and doc.get("value") is not None and doc.get("value") != "":
        return str(doc["value"])
    if key == "api_url" and ENV_API_URL:
        return ENV_API_URL
    if key == "api_key" and ENV_API_KEY:
        return ENV_API_KEY
    return default


def set_setting(key: str, value: str) -> None:
    db = get_db()
    db.settings.update_one(
        {"key": key},
        {"$set": {"key": key, "value": str(value)}},
        upsert=True,
    )


def get_all_admin_ids() -> list[int]:
    db = get_db()
    admin_ids = {int(MAIN_ADMIN_ID)}
    for doc in db.admins.find():
        uid = doc.get("user_id")
        if uid is not None:
            admin_ids.add(int(uid))
    return list(admin_ids)


def is_admin(user_id: int) -> bool:
    return int(user_id) in set(get_all_admin_ids())


def add_admin_id(new_admin_id: int) -> None:
    db = get_db()
    db.admins.update_one(
        {"user_id": int(new_admin_id)},
        {"$setOnInsert": {"user_id": int(new_admin_id)}},
        upsert=True,
    )


def get_country_rate(country_code: str) -> float:
    countries = get_all_countries()
    code = resolve_country_code(country_code)
    if not code or code not in countries:
        return 0.0
    db = get_db()
    doc = db.country_rates.find_one({"country_code": code})
    if doc and doc.get("rate") is not None:
        return float(doc["rate"])
    return float(countries[code]["rate"])


def set_country_rate(country_code: str, rate: float) -> None:
    code = resolve_country_code(country_code)
    if not code:
        return
    db = get_db()
    db.country_rates.update_one(
        {"country_code": code},
        {"$set": {"country_code": code, "rate": float(rate)}},
        upsert=True,
    )
    db.custom_countries.update_one(
        {"country_code": code},
        {"$set": {"rate": float(rate)}},
    )


def ensure_user(
    user_id: int,
    username: str = "",
    full_name: str = "",
    referred_by: int | None = None,
) -> bool:
    db = get_db()
    default_c = resolve_country_code("")
    uid = int(user_id)
    exists = db.users.find_one({"user_id": uid}) is not None
    if not exists:
        ref_val = (
            int(referred_by)
            if referred_by and int(referred_by) != uid
            else None
        )
        db.users.insert_one(
            {
                "user_id": uid,
                "username": username or "",
                "full_name": full_name or "",
                "balance": 0.0,
                "otp_count": 0,
                "selected_country": default_c,
                "referred_by": ref_val,
                "last_req": 0.0,
                "joined_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            }
        )
    else:
        update_fields = {}
        if username:
            update_fields["username"] = username
        if full_name:
            update_fields["full_name"] = full_name
        if update_fields:
            db.users.update_one({"user_id": uid}, {"$set": update_fields})
    return not exists


def add_user_balance(user_id: int, amount: float) -> None:
    db = get_db()
    uid = int(user_id)
    db.users.update_one({"user_id": uid}, {"$inc": {"balance": float(amount)}})
    doc = db.users.find_one({"user_id": uid}, {"balance": 1})
    if doc is not None:
        rounded = round(float(doc.get("balance") or 0.0), 2)
        db.users.update_one({"user_id": uid}, {"$set": {"balance": rounded}})


def deduct_all_user_balance(user_id: int) -> float:
    db = get_db()
    uid = int(user_id)
    doc = db.users.find_one_and_update(
        {"user_id": uid},
        {"$set": {"balance": 0.0}},
    )
    return float(doc.get("balance") or 0.0) if doc else 0.0


def get_user_profile(user_id: int):
    countries = get_all_countries()
    fallback_c = resolve_country_code("")
    db = get_db()
    doc = db.users.find_one(
        {"user_id": int(user_id)},
        {"balance": 1, "otp_count": 1, "selected_country": 1},
    )
    if not doc:
        return 0.0, 0, fallback_c
    raw_c = str(doc.get("selected_country") or "").strip().lower()
    country = raw_c if raw_c in countries else fallback_c
    return (
        float(doc.get("balance") or 0.0),
        int(doc.get("otp_count") or 0),
        country,
    )


def set_user_country(user_id: int, country_code: str) -> None:
    code = resolve_country_code(country_code)
    db = get_db()
    db.users.update_one(
        {"user_id": int(user_id)},
        {"$set": {"selected_country": code}},
    )


def create_active_order(user_id: int, country_code: str, phone_number: str) -> int:
    code = resolve_country_code(country_code) or "custom"
    order_id = get_next_sequence("orders")
    db = get_db()
    db.orders.insert_one(
        {
            "id": int(order_id),
            "user_id": int(user_id),
            "country_code": code,
            "phone_number": phone_number,
            "otp_code": None,
            "status": "ACTIVE",
            "created_at": datetime.datetime.now(datetime.timezone.utc),
        }
    )
    return int(order_id)


def complete_order_and_reward(
    order_id: int, user_id: int, country_code: str, otp_code: str
) -> float:
    code = resolve_country_code(country_code)
    rate = float(get_country_rate(code) if code else 0.0)
    today_str = datetime.datetime.now().strftime("%Y-%m-%d")
    db = get_db()

    res = db.orders.update_one(
        {"id": int(order_id), "status": "ACTIVE"},
        {"$set": {"status": "COMPLETED", "otp_code": otp_code}},
    )
    if res.modified_count > 0:
        uid = int(user_id)
        db.users.update_one(
            {"user_id": uid},
            {"$inc": {"balance": rate, "otp_count": 1}},
        )
        udoc = db.users.find_one({"user_id": uid}, {"balance": 1})
        if udoc is not None:
            db.users.update_one(
                {"user_id": uid},
                {"$set": {"balance": round(float(udoc.get("balance") or 0.0), 2)}},
            )

        db.daily_stats.update_one(
            {"stat_date": today_str, "user_id": uid},
            {
                "$inc": {"otp_count": 1, "earned": rate},
                "$setOnInsert": {"stat_date": today_str, "user_id": uid},
            },
            upsert=True,
        )
        sdoc = db.daily_stats.find_one(
            {"stat_date": today_str, "user_id": uid}, {"earned": 1}
        )
        if sdoc is not None:
            db.daily_stats.update_one(
                {"stat_date": today_str, "user_id": uid},
                {"$set": {"earned": round(float(sdoc.get("earned") or 0.0), 2)}},
            )
    return rate


def get_daily_top_users(date_str: str, limit: int = 5):
    db = get_db()
    rows = []
    stats_cursor = (
        db.daily_stats.find({"stat_date": date_str, "otp_count": {"$gt": 0}})
        .sort([("otp_count", DESCENDING), ("earned", DESCENDING)])
        .limit(int(limit))
    )
    for st in stats_cursor:
        uid = int(st["user_id"])
        udoc = db.users.find_one(
            {"user_id": uid}, {"full_name": 1, "username": 1}
        ) or {}
        rows.append(
            (
                uid,
                udoc.get("full_name", ""),
                udoc.get("username", ""),
                int(st.get("otp_count") or 0),
                float(st.get("earned") or 0.0),
            )
        )
    return rows


def add_api_panel(
    panel_name: str, api_key: str, get_num_url: str, get_otp_url: str = ""
) -> None:
    pid = get_next_sequence("api_panels")
    db = get_db()
    db.api_panels.insert_one(
        {
            "id": int(pid),
            "panel_name": panel_name.strip(),
            "api_key": api_key.strip(),
            "get_num_url": get_num_url.strip(),
            "get_otp_url": get_otp_url.strip(),
            "status": "ACTIVE",
        }
    )


def list_api_panels():
    db = get_db()
    rows = []
    for doc in db.api_panels.find().sort("id", ASCENDING):
        rows.append(
            (
                int(doc["id"]),
                doc.get("panel_name", ""),
                doc.get("api_key", ""),
                doc.get("get_num_url", ""),
                doc.get("get_otp_url", ""),
                doc.get("status", "ACTIVE"),
            )
        )
    return rows


def get_api_panel(panel_id: int):
    db = get_db()
    doc = db.api_panels.find_one({"id": int(panel_id)})
    if not doc:
        return None
    return (
        int(doc["id"]),
        doc.get("panel_name", ""),
        doc.get("api_key", ""),
        doc.get("get_num_url", ""),
        doc.get("get_otp_url", ""),
        doc.get("status", "ACTIVE"),
    )


def delete_api_panel(panel_id: int) -> None:
    db = get_db()
    db.api_panels.delete_one({"id": int(panel_id)})


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

    db = get_db()
    added = 0
    for item in items:
        phone = str(item or "").strip()
        if not phone:
            continue
        try:
            db.numbers.insert_one(
                {
                    "country": c_name,
                    "phone": phone,
                    "status": "unused",
                    "used_by": None,
                }
            )
            added += 1
        except DuplicateKeyError:
            pass
    return added


def get_number_stock_summary(status: str):
    db = get_db()
    pipeline = [
        {"$match": {"status": status}},
        {"$group": {"_id": "$country", "count": {"$sum": 1}}},
    ]
    return [(doc["_id"], int(doc["count"])) for doc in db.numbers.aggregate(pipeline)]


def clear_all_manual_numbers() -> None:
    db = get_db()
    db.numbers.delete_many({})


def pick_manual_number(country: str, user_id: int):
    db = get_db()
    doc = db.numbers.find_one_and_update(
        {"country": country, "status": "unused"},
        {"$set": {"status": "used", "used_by": int(user_id)}},
        return_document=ReturnDocument.BEFORE,
    )
    if not doc:
        return None
    num_id = doc.get("_id")
    return int(hash(str(num_id)) & 0x7FFFFFFF), str(doc["phone"])


def _try_decode_base64_text(text: str) -> str:
    compact = re.sub(r"\s+", "", text or "")
    if len(compact) < 16 or not re.fullmatch(r"[A-Za-z0-9+/=_-]+", compact):
        return ""
    padded = compact + ("=" * ((4 - len(compact) % 4) % 4))
    for decoder in (base64.b64decode, base64.urlsafe_b64decode):
        try:
            raw = decoder(padded)
            decoded = raw.decode("utf-8", errors="ignore").strip()
            if decoded and any(
                ch in decoded
                for ch in ("{", "[", "+", "0", "1", "2", "3", "4", "5", "6", "7", "8", "9")
            ):
                return decoded
        except Exception:
            continue
    return ""


def _parse_panel_number_details(text: str) -> dict[str, str]:
    result = {
        "phone": "",
        "country": "",
        "operator": "",
        "service": get_setting("default_service", "Facebook") or "Facebook",
    }
    if not text:
        return result

    candidates = [text]
    b64_text = _try_decode_base64_text(text)
    if b64_text:
        candidates.append(b64_text)

    for item_text in candidates:
        try:
            data = json.loads(item_text)
            if isinstance(data, dict):
                container = data.get("data") if isinstance(data.get("data"), dict) else data
                if isinstance(container, dict):
                    for key in (
                        "full_number",
                        "phone",
                        "number",
                        "phone_number",
                        "mobile",
                        "msisdn",
                        "no_plus_number",
                        "national_number",
                    ):
                        val = container.get(key)
                        if val is not None and str(val).strip():
                            s_val = str(val).strip()
                            if key in ("no_plus_number", "national_number") and not s_val.startswith("+"):
                                s_val = "+" + s_val
                            result["phone"] = s_val
                            break

                    for c_key in ("country", "country_name", "range", "range_name"):
                        c_val = container.get(c_key)
                        if c_val is not None and str(c_val).strip():
                            result["country"] = str(c_val).strip()
                            break

                    for op_key in ("operator", "carrier", "network"):
                        op_val = container.get(op_key)
                        if op_val is not None and str(op_val).strip():
                            result["operator"] = str(op_val).strip()
                            break

                    for srv_key in ("service", "sid", "app"):
                        srv_val = container.get(srv_key)
                        if srv_val is not None and str(srv_val).strip():
                            result["service"] = str(srv_val).strip()
                            break

                if result["phone"]:
                    return result
        except json.JSONDecodeError:
            pass

        match = re.search(r"\+\d{8,15}", item_text)
        if match and not result["phone"]:
            result["phone"] = match.group(0)
            return result

    return result


def _extract_phone_from_text(text: str) -> str:
    return _parse_panel_number_details(text).get("phone", "")


def _extract_otp_from_text(text: str, expected_phone: str = "") -> str | None:
    if not text:
        return None

    clean_target = re.sub(r"\D", "", expected_phone or "")
    if not clean_target:
        return None

    try:
        data = json.loads(text)
        if isinstance(data, dict):
            container = data.get("data") if isinstance(data.get("data"), dict) else data
            otps_list = container.get("otps") if isinstance(container, dict) else None

            if isinstance(otps_list, list):
                for item in otps_list:
                    if not isinstance(item, dict):
                        continue
                    item_num = re.sub(r"\D", "", str(item.get("number", "")))
                    if not item_num:
                        continue
                    if (
                        item_num == clean_target
                        or item_num.endswith(clean_target)
                        or clean_target.endswith(item_num)
                    ):
                        msg_val = str(item.get("message", "")).strip()
                        if not msg_val:
                            continue
                        code_match = re.search(r"\b\d{4,8}\b", msg_val)
                        if code_match:
                            return code_match.group(0)
                        return msg_val
                return None

            resp_num = re.sub(
                r"\D",
                "",
                str(
                    container.get("number")
                    or container.get("phone")
                    or container.get("full_number")
                    or ""
                ),
            )
            if resp_num and not (
                resp_num == clean_target
                or resp_num.endswith(clean_target)
                or clean_target.endswith(resp_num)
            ):
                return None

            for key in ("code", "otp", "sms", "message"):
                val = container.get(key) if isinstance(container, dict) else None
                if val is not None and str(val).strip():
                    s_val = str(val).strip()
                    m = re.search(r"\b\d{4,8}\b", s_val)
                    return m.group(0) if m else s_val
    except json.JSONDecodeError:
        return None

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
        with urllib.request.urlopen(req, timeout=6) as resp:
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


def _resolve_voltex_rid(country_code: str = "") -> str:
    countries = get_all_countries()
    if country_code and country_code in countries:
        specific_rid = get_setting(f"voltex_rid_{country_code}", "")
        if specific_rid:
            return specific_rid.strip()
    configured_rid = get_setting("voltex_rid", "").strip()
    if configured_rid:
        return configured_rid
    if country_code:
        digits_only = re.sub(r"\D", "", str(country_code))
        if digits_only:
            return digits_only
    return ""


def call_custom_panel_get_number(
    panel_row, country_code: str = ""
) -> tuple[bool, dict, str]:
    _, _, api_key, get_num_url, _, _ = panel_row
    if not get_num_url:
        return False, {}, "Get Number URL set kora nei."

    rid_val = _resolve_voltex_rid(country_code)
    target_url = (
        get_num_url.replace("{api_key}", api_key or "")
        .replace("{key}", api_key or "")
        .replace("{country}", country_code or "")
        .replace("{rid}", rid_val or "")
    )
    payload: dict[str, str] = {}
    if rid_val:
        payload["rid"] = rid_val
    if country_code:
        payload["country"] = country_code

    ok, body = _send_panel_http_request(
        target_url, api_key or "", method="POST", payload=payload
    )
    if not ok:
        q_params = {}
        if rid_val:
            q_params["rid"] = rid_val
        if country_code:
            q_params["country"] = country_code
        sep = "&" if "?" in target_url else "?"
        get_url = (
            f"{target_url}{sep}{urllib.parse.urlencode(q_params)}"
            if q_params
            else target_url
        )
        ok_get, body_get = _send_panel_http_request(
            get_url, api_key or "", method="GET"
        )
        if ok_get:
            ok, body = True, body_get
        else:
            return False, {}, body[:200]

    details = _parse_panel_number_details(body)
    if details.get("phone"):
        return True, details, body[:200]
    return False, {}, body[:200] if body else "Panel theke number pawa jayni."


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
            otp = _extract_otp_from_text(body, expected_phone=phone_number)
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
        with urllib.request.urlopen(req, timeout=6) as resp:
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
        otp_direct = _extract_otp_from_text(
            json.dumps(data), expected_phone=phone_number
        )
        if otp_direct:
            return otp_direct
    return fetch_sms_from_custom_panels(phone_number, country_code)


def get_real_completed_order_counts() -> dict[str, int]:
    db = get_db()
    pipeline = [
        {"$match": {"status": "COMPLETED"}},
        {"$group": {"_id": "$country_code", "cnt": {"$sum": 1}}},
    ]
    return {str(doc["_id"]): int(doc["cnt"]) for doc in db.orders.aggregate(pipeline)}


def get_user_active_orders(user_id: int):
    db = get_db()
    rows = []
    cursor = (
        db.orders.find({"user_id": int(user_id), "status": "ACTIVE"})
        .sort("id", DESCENDING)
        .limit(10)
    )
    for doc in cursor:
        rows.append(
            (
                int(doc["id"]),
                doc.get("country_code", ""),
                doc.get("phone_number", ""),
                doc.get("created_at", ""),
            )
        )
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
    v_rid = get_setting("voltex_rid", "") or "Auto"
    d_srv = get_setting("default_service", "Facebook") or "Facebook"
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    f"💳 WITHDRAW: {w_stat}", callback_data="adm_tog_withdraw"
                ),
                InlineKeyboardButton(
                    f"🔢 VOLTEX RID: {v_rid}", callback_data="adm_set_voltex_rid"
                ),
            ],
            [
                InlineKeyboardButton(
                    f"📲 SERVICE: {d_srv}", callback_data="adm_set_default_service"
                ),
                InlineKeyboardButton(
                    f"🎁 REFER: {ref_r}", callback_data="adm_set_refer_reward"
                ),
            ],
            [
                InlineKeyboardButton(
                    f"💸 MIN W.: {min_w}", callback_data="adm_set_min_withdraw"
                ),
                InlineKeyboardButton(
                    f"⏳ COOLDOWN: {cd}s", callback_data="adm_set_cooldown"
                ),
            ],
            [
                InlineKeyboardButton(
                    f"📞 NUM/REQ: {n_req}", callback_data="adm_set_num_per_req"
                ),
                InlineKeyboardButton(
                    f"🔢 NUM/SHARE: {n_shr}", callback_data="adm_set_num_per_share"
                ),
            ],
            [
                InlineKeyboardButton(
                    "🏦 W. METHODS", callback_data="adm_set_withdraw_methods"
                ),
                InlineKeyboardButton(
                    "💬 OTP GROUP", callback_data="adm_set_otp_group_link"
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
                    f"☎️ Stock • {c_name} ({cnt})", callback_data=f"pickstock_{c_name}"
                )
            ]
        )

    for pid, pname, _, _, _, _ in list_api_panels():
        rows.append(
            [
                InlineKeyboardButton(
                    f"☎️ {pname} (Panel API)", callback_data=f"pickpanel_{pid}"
                )
            ]
        )

    change_target = country_code if country_code else "none"
    rows.append(
        [
            InlineKeyboardButton("🔄 Change", callback_data=f"change_{change_target}"),
            InlineKeyboardButton("🌍 Country", callback_data="countries_menu"),
        ]
    )

    otp_group = get_setting("otp_group_link", "").strip()
    if otp_group.startswith("http"):
        rows.append([InlineKeyboardButton("💬 OTP Group", url=otp_group)])

    return InlineKeyboardMarkup(rows)


def order_action_keyboard(order_id: int, phone_number: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "📲 Check OTP Now", callback_data=f"checkotp_{order_id}"
                ),
                InlineKeyboardButton(
                    "🔄 New Number", callback_data="change_none"
                ),
            ]
        ]
    )


def countries_keyboard() -> InlineKeyboardMarkup:
    countries = get_all_countries()
    rows = []
    for code, info in countries.items():
        rate = get_country_rate(code)
        rows.append(
            [
                InlineKeyboardButton(
                    f"☎️ {info['flag']} {info['name']} ({info['dial']}) — ৳{rate:.2f}",
                    callback_data=f"setcountry_{code}",
                )
            ]
        )
    return InlineKeyboardMarkup(rows)


def _format_number_card_header(
    country_label: str,
    flag: str,
    service_name: str,
    refreshed_str: str,
    tag_text: str = "10M",
) -> str:
    clean_service = service_name.strip() or "Facebook"
    clean_flag = f"{flag} " if flag else ""
    expiry_line = f"│ 🕒 {tag_text}\n" if tag_text else ""
    return (
        f"┌───────────────────────\n"
        f"│ {clean_flag}**{country_label}**\n"
        f"│ 📘 **{clean_service}**\n"
        f"{expiry_line}"
        f"│ 🔄 **Refreshed:** `{refreshed_str}`\n"
        f"└───────────────────────\n"
    )


def _format_roxy_order_card(
    country_label: str,
    flag: str,
    service_name: str,
    refreshed_str: str,
    order_id: int,
    phone_number: str,
    rate: float,
) -> str:
    header = _format_number_card_header(
        country_label=country_label,
        flag=flag,
        service_name=service_name,
        refreshed_str=refreshed_str,
        tag_text="10M",
    )
    return (
        f"{header}"
        f"☎️ **Phone:** `{phone_number}`\n"
        f"🧾 **Order ID:** `#{order_id}`\n"
        f"💰 **Rate:** `৳{rate:.2f}`\n"
        f"⏳ **Status:** *Waiting for OTP...*"
    )


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
    default_service = get_setting("default_service", "Facebook") or "Facebook"

    active_orders = get_user_active_orders(user_id)
    active_note = ""
    if active_orders:
        active_items = [
            f"☎️ `{ph}` • `#{oid}` ({resolve_country_code(cc).upper() or 'CUSTOM'})"
            for oid, cc, ph, _ in active_orders[:3]
        ]
        active_note = "\n\n⏳ **Active Numbers:**\n" + "\n".join(active_items)

    custom_header = get_setting("text_get_num", "")
    if not stock_rows and not panels:
        body_note = "⚠️ **No Stock / Panel:** বর্তমানে কোনো নম্বর বা প্যানেল যুক্ত নেই।"
    else:
        body_note = (
            custom_header
            if custom_header
            else "🟢 **Ready • নিচের অপশন থেকে নম্বর নিন:**"
        )

    if code and code in countries:
        info = countries[code]
        header_block = _format_number_card_header(
            country_label=f"{info['name']} MIX Number",
            flag=info["flag"],
            service_name=info.get("service") or default_service,
            refreshed_str=now_str,
            tag_text="10M",
        )
    else:
        rid_val = get_setting("voltex_rid", "").strip()
        c_title = f"Range {rid_val}" if rid_val else "Select Country"
        header_block = _format_number_card_header(
            country_label=c_title,
            flag="🌍",
            service_name=default_service,
            refreshed_str=now_str,
            tag_text="10M",
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
        f"🔄 **Refreshed:** `{now_str}`",
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
                f"`৳{rate:.2f}` • OTPs: `{done_cnt}`{api_stat}"
            )
    else:
        lines.append("")
        lines.append(
            "ℹ️ কোনো country যোগ করা হয়নি। অ্যাডমিন `/addcountry` বা Manage Countries থেকে যোগ করতে পারেন।"
        )

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
    db = get_db()
    user_ids = [
        doc["user_id"]
        for doc in db.users.find({}, {"user_id": 1})
        if doc.get("user_id") is not None
    ]

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
        f"🏆 **TOP 5 OTP LEADERBOARD** (`{target_date}`)\n\n"
        f"🥇 **{w_label}** — `{w_cnt}` OTPs (`৳{float(w_earned):.2f}`)"
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
    bot_name = get_setting("bot_name", "RSP SMS")
    api_url = get_setting("api_url", "")
    api_key = get_setting("api_key", "")
    voltex_rid = get_setting("voltex_rid", "") or "Auto"
    default_service = get_setting("default_service", "Facebook") or "Facebook"
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
        f"📲 **Service:** `{default_service}`\n"
        f"🔢 **Voltex RID:** `{voltex_rid}`\n"
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
    bot_name = get_setting("bot_name", "RSP SMS")
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
            f"📩 **OTPs:** `{otp_count}` • 💰 **Balance:** `৳{balance:.2f}`\n"
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
                "5️⃣ Flag এবং Service লিখুন (যেমন: `🇧🇪 Facebook` অথবা `skip` লিখুন):",
                parse_mode="Markdown",
            )
            return
        elif country_step == "flag_service":
            draft = context.user_data.pop("country_draft", {})
            context.user_data.pop("country_add_step", None)
            flag_val = "🌍"
            service_val = get_setting("default_service", "Facebook") or "Facebook"
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
                    f"🎉 **{draft.get('name')}** (`{res}`) সফলভাবে যুক্ত হয়েছে! Service: `{service_val}` • Rate: `৳{float(draft.get('rate', 15.0)):.2f}`",
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
                "3️⃣ **Get Number API URL** লিখুন (`{api_key}`, `{country}`, `{rid}` ব্যবহার করতে পারেন):",
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
            f"**Balance:** `৳{balance:.2f}`\n"
            f"**Total OTPs:** `{otp_count}`",
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
        top_users = get_daily_top_users(today_str, limit=5)

        lines = [f"🏆 **TOP 5 OTP LEADERBOARD** (`{today_str}`)", ""]
        if not top_users:
            lines.append("No completed OTPs recorded today yet.")
        else:
            for idx, (_, fname, uname, count_val, earned_val) in enumerate(
                top_users, start=1
            ):
                label = fname or (f"@{uname}" if uname else "User")
                medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(idx, f"#{idx}")
                lines.append(
                    f"{medal} **{label}** — `{count_val}` OTPs (`৳{float(earned_val):.2f}`)"
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
    default_service = get_setting("default_service", "Facebook") or "Facebook"
    now_str = datetime.datetime.now().strftime("%I:%M:%S %p")

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
        if s_key == "voltex_rid":
            await query.message.reply_text(
                "🔢 Voltex range ID (`rid`, যেমন `2246566`) লিখুন অথবা `clear` লিখুন:",
                parse_mode="Markdown",
            )
        elif s_key == "default_service":
            await query.message.reply_text(
                "📱 Service নাম লিখুন (যেমন: `Facebook`):",
                parse_mode="Markdown",
            )
        elif s_key == "otp_group_link":
            await query.message.reply_text(
                "💬 OTP Group link লিখুন (`https://t.me/...`) অথবা `clear` লিখুন:",
                parse_mode="Markdown",
            )
        else:
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
            "1️⃣ প্যানেলের নাম লিখুন (যেমন: `Voltex` বা `5sim`):", parse_mode="Markdown"
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
            country_display = f"{c_info['name']} MIX Number"
            flag_display = c_info["flag"]
            service_display = c_info.get("service") or default_service
        else:
            country_display = f"{c_name} MIX Number"
            flag_display = "🌍"
            service_display = default_service

        rate = get_country_rate(matched_code)
        order_id = create_active_order(user.id, matched_code, phone_num)
        card_text = _format_roxy_order_card(
            country_label=country_display,
            flag=flag_display,
            service_name=service_display,
            refreshed_str=now_str,
            order_id=order_id,
            phone_number=phone_num,
            rate=rate,
        )
        await query.message.reply_text(
            card_text,
            parse_mode="Markdown",
            reply_markup=order_action_keyboard(order_id, phone_num),
        )

    elif data.startswith("pickpanel_"):
        pid = int(data.replace("pickpanel_", "", 1))
        panel = get_api_panel(pid)
        if not panel:
            await query.message.reply_text("❌ Panel পাওয়া যায়নি।")
            return
        _, _, active_code = get_user_profile(user.id)
        ok, details, resp_text = call_custom_panel_get_number(panel, active_code)
        phone_num = details.get("phone", "")
        if not ok or not phone_num:
            await query.message.reply_text(
                f"❌ **{panel[1]} Panel Error:**\n`{resp_text}`",
                parse_mode="Markdown",
            )
            return

        raw_api_country = details.get("country", "")
        detected_code = resolve_country_code(
            raw_api_country or (phone_num if phone_num.startswith("+") else active_code)
        )
        if detected_code and detected_code in countries:
            c_info = countries[detected_code]
            country_display = f"{c_info['name']} MIX Number"
            flag_display = c_info["flag"]
            service_display = c_info.get("service") or details.get("service") or default_service
        else:
            rid_val = get_setting("voltex_rid", "").strip()
            base_c = raw_api_country or (f"Range {rid_val}" if rid_val else panel[1])
            country_display = f"{base_c} MIX Number"
            flag_display = "🌍"
            service_display = details.get("service") or default_service

        rate = get_country_rate(detected_code)
        order_id = create_active_order(user.id, detected_code, phone_num)
        card_text = _format_roxy_order_card(
            country_label=country_display,
            flag=flag_display,
            service_name=service_display,
            refreshed_str=now_str,
            order_id=order_id,
            phone_number=phone_num,
            rate=rate,
        )
        await query.message.reply_text(
            card_text,
            parse_mode="Markdown",
            reply_markup=order_action_keyboard(order_id, phone_num),
        )

    elif data.startswith("checkotp_"):
        raw_oid = data.replace("checkotp_", "", 1)
        if not raw_oid.isdigit():
            return
        oid = int(raw_oid)
        db = get_db()
        order_doc = db.orders.find_one({"id": oid, "user_id": int(user.id)})
        if not order_doc:
            await query.message.reply_text("❌ Order পাওয়া যায়নি।")
            return

        if order_doc.get("status") == "COMPLETED" and order_doc.get("otp_code"):
            await query.message.reply_text(
                f"✅ **OTP Already Received:** `{order_doc['otp_code']}`",
                parse_mode="Markdown",
            )
            return

        phone_num = str(order_doc.get("phone_number", ""))
        c_code = resolve_country_code(order_doc.get("country_code", ""))
        otp_code = await asyncio.to_thread(fetch_sms_from_panel, phone_num, c_code)

        if otp_code:
            reward = complete_order_and_reward(oid, int(user.id), c_code, otp_code)
            await query.message.reply_text(
                f"🟢 **OTP Received!**\n\n"
                f"🧾 **Order ID:** `#{oid}`\n"
                f"☎️ **Phone:** `{phone_num}`\n"
                f"🔑 **Code:** `{otp_code}`\n"
                f"💰 **Earned:** `+৳{reward:.2f}`",
                parse_mode="Markdown",
            )
        else:
            await query.message.reply_text(
                f"⏳ `{phone_num}` (`#{oid}`) এর জন্য এখনো OTP আসেনি। একটু পর আবার চেষ্টা করুন।",
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
                f"🌍 Country set to {info['flag']} **{info['name']} (`{info['dial']}`)** • 📘 **{info.get('service', default_service)}** — 💰 **৳{rate:.2f}**",
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

    db = get_db()
    doc = db.orders.find_one({"id": order_id})

    if not doc:
        await update.message.reply_text(
            f"❌ Order `#{order_id}` পাওয়া যায়নি।", parse_mode="Markdown"
        )
        return

    user_id = doc.get("user_id")
    c_code = doc.get("country_code", "")
    phone_number = doc.get("phone_number", "")
    status = doc.get("status", "")

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
                f"🟢 **OTP Received!**\n\n"
                f"🧾 **Order ID:** `#{order_id}`\n"
                f"{country_line}"
                f"☎️ **Phone:** `{phone_number}`\n"
                f"🔑 **Code:** `{otp_code}`\n"
                f"💰 **Earned:** `+৳{reward:.2f}`"
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
            "Example: `/addcountry be Belgium +32 18.00 🇧🇪 Facebook`",
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
    c_service = (
        " ".join(context.args[5:]).strip()
        if len(context.args) >= 6
        else (get_setting("default_service", "Facebook") or "Facebook")
    )

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
            f"✅ Country **{c_name}** (`{res}`) added/updated — Service: `{c_service}` • Rate: **৳{c_rate:.2f}**",
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
            "Usage: `/setname RSP SMS`", parse_mode="Markdown"
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
    try:
        countries = get_all_countries()
        db = get_db()
        cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(
            seconds=10
        )
        active_cursor = db.orders.find(
            {
                "status": "ACTIVE",
                "$or": [
                    {"created_at": {"$lte": cutoff}},
                    {"created_at": {"$type": "string"}},
                ],
            }
        ).limit(15)
        active_rows = [
            (
                int(doc["id"]),
                int(doc["user_id"]),
                doc.get("country_code", ""),
                doc.get("phone_number", ""),
            )
            for doc in active_cursor
        ]

        for order_id, user_id, c_code, phone_number in active_rows:
            resolved_code = resolve_country_code(c_code)
            otp_code = await asyncio.to_thread(
                fetch_sms_from_panel, phone_number, resolved_code
            )
            if not otp_code:
                continue

            reward = complete_order_and_reward(
                int(order_id), int(user_id), resolved_code, otp_code
            )
            if reward >= 0:
                if resolved_code and resolved_code in countries:
                    c_info = countries[resolved_code]
                    country_line = f"🌍 **Country:** {c_info['flag']} {c_info['name']}\n"
                else:
                    country_line = ""
                try:
                    await context.bot.send_message(
                        chat_id=int(user_id),
                        text=(
                            f"🟢 **OTP Received!**\n\n"
                            f"🧾 **Order ID:** `#{order_id}`\n"
                            f"{country_line}"
                            f"☎️ **Phone:** `{phone_number}`\n"
                            f"🔑 **Code:** `{otp_code}`\n"
                            f"💰 **Earned:** `+৳{reward:.2f}`"
                        ),
                        parse_mode="Markdown",
                    )
                except Exception as exc:
                    logger.warning("Failed to send OTP message to %s: %s", user_id, exc)
    except Exception as exc:
        logger.error("job_poll_otps error: %s", exc)


async def job_daily_winner_announce(context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        now = datetime.datetime.now()
        today_str = now.strftime("%Y-%m-%d")
        if now.hour == 23 and now.minute >= 55:
            if get_setting("last_winner_date", "") == today_str:
                return
            announced, _ = await announce_daily_winner(context, today_str)
            if announced:
                set_setting("last_winner_date", today_str)
    except Exception as exc:
        logger.error("job_daily_winner_announce error: %s", exc)


web_app = Flask(__name__)


@web_app.route("/")
def home():
    return "RSP OTP Bot is running!", 200


@web_app.route("/health")
@web_app.route("/ping")
def health():
    return "OK", 200


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
