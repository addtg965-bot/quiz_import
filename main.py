"""
AI Quiz Bot
- Groq AI orqali avtomatik test tuzadi
- @QuizBot ga yuboradi va havola beradi
- Knopkali interfeys
- Ko'p akkaunt pool
"""

import asyncio
import json
import logging
import os
import random
import re
import sqlite3
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from groq import Groq
from telethon import TelegramClient, events, Button
from telethon.errors import SessionPasswordNeededError, PhoneCodeInvalidError
from telethon.tl.functions.messages import SendMediaRequest
from telethon.tl.types import InputMediaPoll, Poll, PollAnswer, TextWithEntities

# ============================================================
#  SOZLAMALAR — Railway environment variables dan o'qiladi
# ============================================================
import os as _os

BOT_TOKEN    = _os.environ["BOT_TOKEN"]
API_ID       = int(_os.environ["API_ID"])
API_HASH     = _os.environ["API_HASH"]
GROQ_API_KEY = _os.environ["GROQ_API_KEY"]
ADMIN_IDS    = [int(x) for x in _os.environ.get("ADMIN_IDS", "0").split(",") if x.strip()]
LOG_GROUP_ID = int(_os.environ.get("LOG_GROUP_ID", "0"))  # loglar yuboriladigan guruh ID
NOTIFY_PHONE = _os.environ.get("NOTIFY_PHONE", "")

# Telefon raqamlar: PHONE_NUMBERS env da vergul bilan yoziladi
# Misol: +998901234567,+998901234568
PHONE_NUMBERS = [
    p.strip() for p in _os.environ.get("PHONE_NUMBERS", "").split(",")
    if p.strip()
]

# Humo kartalar: HUMO_CARDS env da vergul bilan
# Misol: 9860 1234 5678 9001,9860 1234 5678 9002
HUMO_CARDS = [
    c.strip() for c in _os.environ.get("HUMO_CARDS", "").split(",")
    if c.strip()
]

AD_EVERY   = int(_os.environ.get("AD_EVERY", "6"))
AD_TEXT    = _os.environ.get("AD_TEXT", "📢 @quiz_import_bot — AI yordamida @QuizBot testi yarating!")
GROQ_MODEL = _os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")

ACCOUNTS_FILE = _os.environ.get("ACCOUNTS_FILE", "/data/accounts.json")
DB_FILE       = _os.environ.get("DB_FILE",       "/data/bot.db")

# /data papkasi bo'lmasa — local papkada saqlaymiz
if not _os.path.exists("/data"):
    _os.makedirs("data", exist_ok=True)
    ACCOUNTS_FILE = "data/accounts.json"
    DB_FILE       = "data/bot.db"


# ============================================================
#  TO'LOV TIZIMI
# ============================================================

# Humo kartalar ro'yxati — navbat bilan beriladi
# ⬇️ O'z karta raqamlaringizni shu yerga yozing!
HUMO_CARDS = [
   "9860 3501 4339 8906",   # karta 1 — o'zgartiring
    "9860 3566 0573 8935",   # karta 2 — o'zgartiring
    "9860 3466 0594 5705",   # kart
]
AI_PRICE           = 2000    # 1 ta AI test narxi (so'm)
FILE_PRICE_PER_25  = 1500    # har 25 savol uchun narx (fayl orqali)
PAYMENT_TIMEOUT    = 600     # sekund (10 daqiqa)
HUMOCARD_BOT    = "@humocardbot"
NOTIFY_PHONE    = "+998934897111"  # @humocardbot xabar keladigan raqam

# Kartalar band/bo'sh holati: card_num -> user_id yoki None
card_assignments: dict = {card: None for card in HUMO_CARDS}


def get_free_card(user_id: int) -> Optional[str]:
    """Bo'sh karta berish — navbat bilan"""
    busy = set(card_assignments.values())
    for card in HUMO_CARDS:
        if card_assignments.get(card) is None:
            card_assignments[card] = user_id
            return card
    return None   # hammasi band


def calc_file_price(q_count: int) -> int:
    """Fayl uchun narx: har 25 savolga 2000 so'm, qisman bo'lsa ham to'liq hisoblanadi"""
    import math
    blocks = math.ceil(q_count / 25)
    return blocks * FILE_PRICE_PER_25


def release_card(card_num: str):
    """Kartani bo'shatish"""
    card_assignments[card_num] = None


def get_db():
    return sqlite3.connect(DB_FILE)


def db_save_user(user_id: int, first_name: str = "",
                 last_name: str = "", username: str = ""):
    """Foydalanuvchini saqlash yoki yangilash"""
    con = get_db()
    con.execute("""
        INSERT INTO users (user_id, first_name, last_name, username, created_at, last_seen)
        VALUES (?, ?, ?, ?, datetime('now'), datetime('now'))
        ON CONFLICT(user_id) DO UPDATE SET
            first_name = excluded.first_name,
            last_name  = excluded.last_name,
            username   = excluded.username,
            last_seen  = datetime('now')
    """, (user_id, first_name or "", last_name or "", username or ""))
    con.commit()
    con.close()


def db_count_users() -> int:
    """Jami foydalanuvchilar soni"""
    con = get_db()
    n = con.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    con.close()
    return n


def db_get_users(limit: int = 20, offset: int = 0) -> list:
    """Foydalanuvchilar ro'yxati"""
    con = get_db()
    rows = con.execute("""
        SELECT user_id, first_name, last_name, username, created_at, last_seen
        FROM users ORDER BY last_seen DESC LIMIT ? OFFSET ?
    """, (limit, offset)).fetchall()
    con.close()
    return rows


def db_get_user(user_id: int):
    """Bitta foydalanuvchi"""
    con = get_db()
    row = con.execute(
        "SELECT * FROM users WHERE user_id=?", (user_id,)
    ).fetchone()
    con.close()
    return row


def db_get_balance(user_id: int) -> int:
    con = get_db()
    row = con.execute(
        "SELECT balance FROM users WHERE user_id=?", (user_id,)
    ).fetchone()
    con.close()
    return row[0] if row else 0



def db_add_balance(user_id: int, amount: int, reason: str = ""):
    con = get_db()
    con.execute(
        "UPDATE users SET balance = balance + ? WHERE user_id=?",
        (amount, user_id)
    )
    con.execute(
        """INSERT INTO balance_log (user_id, amount, reason, created_at)
           VALUES (?, ?, ?, datetime('now'))""",
        (user_id, amount, reason)
    )
    con.commit()
    con.close()


def db_deduct_balance(user_id: int, amount: int, reason: str = "") -> bool:
    con = get_db()
    row = con.execute(
        "SELECT balance FROM users WHERE user_id=?", (user_id,)
    ).fetchone()
    if not row or row[0] < amount:
        con.close()
        return False
    con.execute(
        "UPDATE users SET balance = balance - ? WHERE user_id=?",
        (amount, user_id)
    )
    con.execute(
        """INSERT INTO balance_log (user_id, amount, reason, created_at)
           VALUES (?, ?, ?, datetime('now'))""",
        (user_id, -amount, reason)
    )
    con.commit()
    con.close()
    return True


def db_create_payment(user_id: int, card_num: str, amount: int) -> int:
    con = get_db()
    cur = con.cursor()
    cur.execute(
        """INSERT INTO payments
           (user_id, card_num, amount, status, created_at, expires_at)
           VALUES (?, ?, ?, 'pending',
                   datetime('now'),
                   datetime('now','+3 minutes'))""",
        (user_id, card_num, amount)
    )
    pay_id = cur.lastrowid
    con.commit()
    con.close()
    return pay_id


def db_get_pending(user_id: int) -> Optional[tuple]:
    """Foydalanuvchining kutilayotgan to'lovi (id, card, amount, expires)"""
    con = get_db()
    row = con.execute(
        """SELECT id, card_num, amount, expires_at FROM payments
           WHERE user_id=? AND status='pending'
           AND expires_at > datetime('now')
           ORDER BY id DESC LIMIT 1""",
        (user_id,)
    ).fetchone()
    con.close()
    return row


def db_confirm_payment(pay_id: int) -> Optional[tuple]:
    """To'lovni tasdiqlash → (user_id, amount, card_num)"""
    con = get_db()
    row = con.execute(
        "SELECT user_id, amount, card_num FROM payments WHERE id=? AND status='pending'",
        (pay_id,)
    ).fetchone()
    if row:
        con.execute(
            """UPDATE payments SET status='confirmed',
               paid_at=datetime('now') WHERE id=?""",
            (pay_id,)
        )
        con.commit()
    con.close()
    return row


def db_expire_old():
    """Muddati o'tgan to'lovlarni bekor qilish"""
    con = get_db()
    expired = con.execute(
        """SELECT id, card_num FROM payments WHERE status='pending'
           AND expires_at < datetime('now')"""
    ).fetchall()
    for pay_id, card_num in expired:
        con.execute(
            "UPDATE payments SET status='expired' WHERE id=?", (pay_id,)
        )
        release_card(card_num)
    con.commit()
    con.close()
    return len(expired)


def db_payment_stats() -> dict:
    con = get_db()
    total = con.execute(
        "SELECT COALESCE(SUM(amount),0) FROM payments WHERE status='confirmed'"
    ).fetchone()[0]
    today = con.execute(
        """SELECT COALESCE(SUM(amount),0) FROM payments
           WHERE status='confirmed' AND DATE(paid_at)=DATE('now')"""
    ).fetchone()[0]
    pending = con.execute(
        "SELECT COUNT(*) FROM payments WHERE status='pending'"
    ).fetchone()[0]
    con.close()
    return {"total": total, "today": today, "pending": pending}


def db_init():
    """Barcha jadvallarni yaratish"""
    con = get_db()
    cur = con.cursor()
    cur.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            user_id     INTEGER PRIMARY KEY,
            first_name  TEXT DEFAULT '',
            last_name   TEXT DEFAULT '',
            username    TEXT DEFAULT '',
            balance     INTEGER DEFAULT 0,
            invited_by  INTEGER DEFAULT NULL,
            created_at  TEXT DEFAULT '',
            last_seen   TEXT DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS payments (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL,
            card_num    TEXT NOT NULL,
            amount      INTEGER NOT NULL,
            status      TEXT DEFAULT 'pending',
            created_at  TEXT DEFAULT '',
            paid_at     TEXT DEFAULT '',
            expires_at  TEXT DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS balance_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL,
            amount      INTEGER NOT NULL,
            reason      TEXT DEFAULT '',
            created_at  TEXT DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS quizzes (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL,
            fan_name    TEXT DEFAULT '',
            q_count     INTEGER DEFAULT 0,
            variant_num INTEGER DEFAULT 1,
            url         TEXT DEFAULT '',
            time_choice TEXT DEFAULT '30',
            order_type  TEXT DEFAULT 'order',
            source      TEXT DEFAULT 'ai',
            created_at  TEXT DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS sessions (
            phone       TEXT PRIMARY KEY,
            session_data TEXT NOT NULL,
            updated_at  TEXT DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS referrals (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            inviter_id  INTEGER NOT NULL,
            invited_id  INTEGER NOT NULL,
            bonus       INTEGER DEFAULT 500,
            created_at  TEXT DEFAULT ''
        );
    """)
    # Eski DB lar uchun migration
    migrations = [
        "ALTER TABLE users ADD COLUMN balance INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN invited_by INTEGER DEFAULT NULL",
        """CREATE TABLE IF NOT EXISTS referrals (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            inviter_id  INTEGER NOT NULL,
            invited_id  INTEGER NOT NULL,
            bonus       INTEGER DEFAULT 500,
            created_at  TEXT DEFAULT ''
        )""",
    ]
    for sql in migrations:
        try:
            cur.execute(sql)
            con.commit()
        except Exception:
            pass
    con.commit()
    con.close()


def db_save_session(phone: str, session_path: str):
    """Sessiya faylini DB ga saqlash (base64)"""
    import base64
    if not _os.path.exists(session_path + ".session"):
        return
    with open(session_path + ".session", "rb") as f:
        data = base64.b64encode(f.read()).decode()
    con = get_db()
    con.execute("""
        INSERT INTO sessions (phone, session_data, updated_at)
        VALUES (?, ?, datetime('now'))
        ON CONFLICT(phone) DO UPDATE SET
            session_data = excluded.session_data,
            updated_at   = excluded.updated_at
    """, (phone, data))
    con.commit()
    con.close()
    log.info(f"Sessiya DB ga saqlandi: {phone}")


def db_load_session(phone: str, session_path: str) -> bool:
    """DB dan sessiya faylini tiklash"""
    import base64
    con = get_db()
    row = con.execute(
        "SELECT session_data FROM sessions WHERE phone=?", (phone,)
    ).fetchone()
    con.close()
    if not row:
        return False
    try:
        data = base64.b64decode(row[0].encode())
        sess_dir = _os.path.dirname(session_path)
        if sess_dir:
            _os.makedirs(sess_dir, exist_ok=True)
        with open(session_path + ".session", "wb") as f:
            f.write(data)
        log.info(f"Sessiya DB dan tiklandi: {phone}")
        return True
    except Exception as e:
        log.error(f"Sessiya tiklash xato: {e}")
        return False


def db_save_quiz(user_id: int, fan_name: str, q_count: int,
                 variant_num: int, url: str, time_choice: str,
                 order_type: str, source: str = "ai") -> int:
    """Quiz ma'lumotlarini saqlash, id qaytaradi"""
    con = get_db()
    cur = con.cursor()
    cur.execute("""
        INSERT INTO quizzes
        (user_id, fan_name, q_count, variant_num, url, time_choice, order_type, source, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
    """, (user_id, fan_name, q_count, variant_num, url, time_choice, order_type, source))
    quiz_id = cur.lastrowid
    con.commit()
    con.close()
    return quiz_id


def db_get_user_quizzes(user_id: int, limit: int = 20) -> list:
    """Foydalanuvchining quizlari"""
    con = get_db()
    rows = con.execute("""
        SELECT id, fan_name, q_count, variant_num, url, source, created_at
        FROM quizzes
        WHERE user_id = ?
        ORDER BY id DESC
        LIMIT ?
    """, (user_id, limit)).fetchall()
    con.close()
    return rows


def db_count_user_quizzes(user_id: int) -> int:
    con = get_db()
    n = con.execute(
        "SELECT COUNT(*) FROM quizzes WHERE user_id=?", (user_id,)
    ).fetchone()[0]
    con.close()
    return n


# ============================================================
#  REFERAL TIZIMI
# ============================================================
REFERRAL_BONUS = 1000  # har ikki tomonga beriladigan so'm

def db_is_new_user(user_id: int) -> bool:
    """Foydalanuvchi avval kelganmi?"""
    con = get_db()
    row = con.execute("SELECT user_id FROM users WHERE user_id=?", (user_id,)).fetchone()
    con.close()
    return row is None

def db_save_referral(inviter_id: int, invited_id: int):
    """Referal munosabatini saqlash"""
    con = get_db()
    con.execute("""
        INSERT OR IGNORE INTO referrals (inviter_id, invited_id, bonus, created_at)
        VALUES (?, ?, ?, datetime('now'))
    """, (inviter_id, invited_id, REFERRAL_BONUS))
    con.execute(
        "UPDATE users SET invited_by=? WHERE user_id=?",
        (inviter_id, invited_id)
    )
    con.commit()
    con.close()

def db_get_referral_count(user_id: int) -> int:
    """Foydalanuvchi nechta odam taklif qilgani"""
    con = get_db()
    n = con.execute(
        "SELECT COUNT(*) FROM referrals WHERE inviter_id=?", (user_id,)
    ).fetchone()[0]
    con.close()
    return n

def db_get_referral_list(user_id: int, limit: int = 20) -> list:
    """Taklif qilinganlar ro'yxati"""
    con = get_db()
    rows = con.execute("""
        SELECT r.invited_id, u.first_name, u.last_name, u.username, r.created_at
        FROM referrals r
        LEFT JOIN users u ON u.user_id = r.invited_id
        WHERE r.inviter_id = ?
        ORDER BY r.id DESC
        LIMIT ?
    """, (user_id, limit)).fetchall()
    con.close()
    return rows

def db_already_referred(inviter_id: int, invited_id: int) -> bool:
    """Bu juft allaqachon referalda bormi?"""
    con = get_db()
    row = con.execute(
        "SELECT id FROM referrals WHERE inviter_id=? AND invited_id=?",
        (inviter_id, invited_id)
    ).fetchone()
    con.close()
    return row is not None


# ============================================================
#  LOGGING
# ============================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    handlers=[
        logging.FileHandler("bot.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger("AIQuizBot")

# ============================================================
#  GROQ AI — TEST TUZISH
# ============================================================
groq_client = Groq(api_key=GROQ_API_KEY)

LANGUAGES = {
    "uz": "O'zbek tilida",
    "ru": "Rus tilida",
    "en": "Ingliz tilida",
}

def build_prompt(fan: str, count: int, lang: str = "uz",
                 difficulty: str = "o'rta", topic: str = "") -> str:
    lang_text = LANGUAGES.get(lang, "O'zbek tilida")
    mavzu_text = f'Mavzu: "{topic}"' if topic else ""
    return f"""Siz {lang_text} test tuzuvchi mutaxassissiz.
"{fan}" fanidan {count} ta test savoli tuz.
{mavzu_text}
Qiyinlik darajasi: {difficulty}.

MUHIM QOIDALAR:
1. Faqat JSON formatda qaytar, boshqa hech narsa yozma
2. Har savolda 4 ta variant bo'lsin
3. Faqat bitta to'g'ri javob bo'lsin
4. Savollar mantiqli va aniq bo'lsin
5. Variantlar bir-biridan farqli bo'lsin
{f'6. Faqat "{topic}" mavzusidan savol tuz' if topic else ""}

JSON format (qat'iy shu ko'rinishda):
[
  {{
    "q": "savol matni",
    "opts": ["variant A", "variant B", "variant C", "variant D"],
    "ans": 0
  }}
]

ans — to'g'ri javob indeksi (0=A, 1=B, 2=C, 3=D)

Hozir {fan} fanidan{f' ({topic} mavzusidan)' if topic else ''} {count} ta savol yoz:"""


async def generate_questions(fan: str, count: int, lang: str = "uz",
                              difficulty: str = "o'rta", topic: str = "") -> list:
    """Groq AI orqali savollar generatsiya qilish — xatolarga chidamli"""
    prompt = build_prompt(fan, count, lang, difficulty, topic)
    log.info(f"AI so'rov: {fan} | mavzu: {topic or 'yo\'q'} | {count} ta | {lang} | {difficulty}")

    loop = asyncio.get_event_loop()

    def _call():
        return groq_client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Siz faqat JSON formatda javob beradigan test tuzuvchisiz. "
                        "Hech qanday izoh, markdown, kod bloki yozma. "
                        "Faqat [ ... ] JSON massivi."
                    )
                },
                {"role": "user", "content": prompt}
            ],
            temperature=0.5,
            max_tokens=4000,
        )

    resp = await loop.run_in_executor(None, _call)
    raw = resp.choices[0].message.content.strip()
    log.info(f"AI xom javob (dastlabki 200 belgi): {raw[:200]}")

    questions = _safe_parse_json(raw)

    if not questions:
        log.warning("Birinchi urinish muvaffaqiyatsiz, qayta so'rov yuborilmoqda...")
        # Qayta so'rov — yanada qattiqroq
        def _call2():
            return groq_client.chat.completions.create(
                model=GROQ_MODEL,
                messages=[
                    {
                        "role": "system",
                        "content": "Faqat sof JSON massivi qaytar. Hech narsa boshqa yo'q."
                    },
                    {"role": "user", "content": prompt},
                    {"role": "assistant", "content": "["},
                ],
            temperature=0.3,
            max_tokens=4000,
            )
        resp2 = await loop.run_in_executor(None, _call2)
        raw2 = "[" + resp2.choices[0].message.content.strip()
        log.info(f"AI 2-javob: {raw2[:200]}")
        questions = _safe_parse_json(raw2)

    # Tekshirish va tozalash
    valid = []
    for q in (questions or []):
        if (isinstance(q, dict) and
                "q" in q and "opts" in q and "ans" in q and
                len(q["opts"]) >= 2 and
                0 <= int(q.get("ans", 0)) < len(q["opts"])):
            valid.append({
                "q": str(q["q"])[:255],
                "opts": [str(o)[:100] for o in q["opts"]],
                "ans": int(q["ans"])
            })

    log.info(f"AI {len(valid)} ta savol yaratdi")
    return valid


def _safe_parse_json(text: str) -> Optional[list]:
    """JSON ni xavfsiz parse qilish — bir necha usul bilan"""
    if not text:
        return None

    # 1. Markdown code block olib tashlash
    text = re.sub(r'```(?:json)?', '', text)
    text = re.sub(r'```', '', text)
    text = text.strip().strip('`').strip()

    # 2. [ ... ] qismni ajratib olish
    match = re.search(r'\[.*\]', text, re.DOTALL)
    if match:
        text = match.group(0)

    # 3. To'g'ridan parse
    try:
        data = json.loads(text)
        if isinstance(data, list):
            return data
    except Exception:
        pass

    # 4. Har bir { } blokni alohida parse qilish
    try:
        items = []
        for m in re.finditer(r'\{[^{}]+\}', text, re.DOTALL):
            try:
                obj = json.loads(m.group(0))
                if isinstance(obj, dict):
                    items.append(obj)
            except Exception:
                # Yaroqsiz qatorlarni tuzatib ko'rish
                fixed = _fix_json_obj(m.group(0))
                try:
                    obj = json.loads(fixed)
                    if isinstance(obj, dict):
                        items.append(obj)
                except Exception:
                    pass
        if items:
            return items
    except Exception:
        pass

    return None


def _fix_json_obj(s: str) -> str:
    """Oddiy JSON xatolarini tuzatish"""
    # Oxirgi vergulni olib tashlash
    s = re.sub(r',\s*}', '}', s)
    s = re.sub(r',\s*\]', ']', s)
    # Yagona qo'shtirnoqni ikkilikka almashtirish
    s = re.sub(r"(?<!\\)'", '"', s)
    return s


# ============================================================
#  AKKAUNTLAR SAQLASH
# ============================================================
def load_phones() -> list:
    phones = list(PHONE_NUMBERS)
    if os.path.exists(ACCOUNTS_FILE):
        try:
            with open(ACCOUNTS_FILE) as f:
                for p in json.load(f):
                    if p not in phones:
                        phones.append(p)
        except Exception:
            pass
    return phones

def save_extra_phones(all_phones: list):
    extra = [p for p in all_phones if p not in PHONE_NUMBERS]
    with open(ACCOUNTS_FILE, "w") as f:
        json.dump(extra, f, ensure_ascii=False, indent=2)

# ============================================================
#  MA'LUMOT TUZILMALARI
# ============================================================
@dataclass
class QuizRequest:
    user_id: int
    chat_id: int
    questions: list
    fan_name: str
    variant_num: int
    time_choice: str
    order_choice: str
    total_variants: int = 1
    source: str = "file"   # "ai" yoki "file"

@dataclass
class UserState:
    step: str = "idle"
    # AI rejim
    fan_name: str = ""
    topic: str = ""          # mavzu (ixtiyoriy)
    q_count: int = 10
    lang: str = "uz"
    difficulty: str = "o'rta"
    questions: list = field(default_factory=list)
    total_questions: int = 0
    per_variant: int = 25
    time_choice: str = "30"
    order_choice: str = "order"

# ============================================================
#  GLOBAL
# ============================================================
user_states: dict = {}
admin_states: dict = {}
request_queue: deque = deque()
queue_lock = asyncio.Lock()
account_pool: list = []
account_busy: dict = {}
account_phones: dict = {}
all_clients: list = []   # barcha ulanган клиентлар (pool + notify)
bot_client: TelegramClient = None

# ============================================================
#  VAQT HISOBLASH
# ============================================================
SETUP_SECONDS = 20
SECONDS_PER_QUESTION = 2

def estimate_seconds(n: int) -> int:
    return SETUP_SECONDS + n * SECONDS_PER_QUESTION

def format_wait(s: int) -> str:
    if s < 60:   return f"{s} soniya"
    elif s < 3600:
        m, sec = divmod(s, 60)
        return f"{m} daq {sec} sek" if sec else f"{m} daqiqa"
    else:
        h, m2 = divmod(s, 3600)
        return f"{h} soat {m2//60} daq" if m2 else f"{h} soat"

def calc_wait(new_reqs: list) -> str:
    total_acc = len(account_pool)
    if not total_acc: return "?"
    slots = [0] * total_acc
    for req in list(request_queue):
        slots[slots.index(min(slots))] += estimate_seconds(len(req.questions))
    if not new_reqs:
        return format_wait(min(slots)) if any(slots) else "0 soniya"
    start = min(slots)
    total_new = sum(estimate_seconds(len(r.questions)) for r in new_reqs)
    return format_wait(int(start + total_new))

# ============================================================
#  POOL
# ============================================================
async def pool_add(client, phone):
    account_pool.append(client)
    account_busy[id(client)] = False
    account_phones[id(client)] = phone
    # Sessiyani DB ga saqlash
    sess_dir = _os.path.dirname(DB_FILE)
    session  = _os.path.join(sess_dir, f"userbot_{phone.replace('+','').replace(' ','')}")
    db_save_session(phone, session)

async def pool_remove(phone) -> bool:
    for c in account_pool:
        if account_phones.get(id(c)) == phone:
            if account_busy.get(id(c)): return False
            await c.disconnect()
            account_pool.remove(c)
            account_busy.pop(id(c), None)
            account_phones.pop(id(c), None)
            return True
    return False

async def get_free():
    """Bo'sh va ulangan akkaunt olish — uzilgan bo'lsa qayta ulaydi"""
    while True:
        for c in account_pool:
            if account_busy.get(id(c)):
                continue
            try:
                if not c.is_connected():
                    log.info(f"Akkaunt uzilgan, qayta ulanmoqda: {account_phones.get(id(c))}")
                    await c.connect()
                if await c.is_user_authorized():
                    account_busy[id(c)] = True
                    return c
            except Exception as e:
                log.error(f"Akkaunt tekshirishda xato: {e}")
        await asyncio.sleep(3)

def release(c): account_busy[id(c)] = False
def is_admin(uid): return uid in ADMIN_IDS

# ============================================================
#  QUIZ YARATISH (@QuizBot ga yuborish)
# ============================================================
async def send_poll(userbot, peer, q, opts, ans):
    answers = [PollAnswer(
        text=TextWithEntities(text=o[:100], entities=[]),
        option=bytes([i])
    ) for i, o in enumerate(opts)]
    poll = Poll(
        id=random.randint(1, 2**31),
        question=TextWithEntities(text=q[:255], entities=[]),
        answers=answers, quiz=True,
        public_voters=False, multiple_choice=False, closed=False,
    )
    await userbot(SendMediaRequest(
        peer=peer,
        media=InputMediaPoll(poll=poll, correct_answers=[bytes([ans])]),
        message="", random_id=random.randint(1, 2**63),
    ))

async def make_quiz(userbot: TelegramClient, req: QuizRequest) -> Optional[str]:
    try:
        qbot = await userbot.get_entity("@QuizBot")
        title = f"{req.fan_name} — Variant {req.variant_num}"

        # Oldingi sessiyani tugatish
        try:
            await userbot.send_message(qbot, "/cancel")
            await asyncio.sleep(3)
        except Exception:
            pass

        # Boshlash oldidan oxirgi xabar ID ni eslab qolamiz
        try:
            start_msgs = await userbot.get_messages(qbot, limit=1)
            start_msg_id = start_msgs[0].id if start_msgs else 0
        except Exception:
            start_msg_id = 0
        log.info(f"Start msg ID: {start_msg_id}")

        await userbot.send_message(qbot, "/newquiz"); await asyncio.sleep(4)
        await userbot.send_message(qbot, title);     await asyncio.sleep(3)
        await userbot.send_message(qbot, "/skip");   await asyncio.sleep(3)

        for i, q in enumerate(req.questions):
            try:
                if AD_EVERY > 0 and i > 0 and i % AD_EVERY == 0:
                    await userbot.send_message(qbot, AD_TEXT)
                    await asyncio.sleep(2)

                await send_poll(userbot, qbot, q["q"], q["opts"], q["ans"])
                log.info(f"  [{i+1}/{len(req.questions)}] OK")
                await asyncio.sleep(2)
            except Exception as e:
                log.error(f"  [{i+1}] xato: {e}")
                await asyncio.sleep(3)

        await userbot.send_message(qbot, "/done")
        await asyncio.sleep(6)

        # Vaqt — eng yangi tugmali xabarni topamiz
        msgs = await userbot.get_messages(qbot, limit=5)
        msg = next((m for m in msgs if m.reply_markup), None)
        if msg:
            tmap = {"15": "15", "30": "30", "60": "60", "0": "No limit"}
            target = tmap.get(req.time_choice, "30")
            clicked = False
            for row in msg.reply_markup.rows:
                for btn in row.buttons:
                    if target in btn.text:
                        await msg.click(text=btn.text); clicked = True; break
                if clicked: break
            if not clicked:
                await msg.click(text=msg.reply_markup.rows[0].buttons[0].text)
        await asyncio.sleep(4)

        # Tartib — eng yangi tugmali xabarni topamiz
        msgs = await userbot.get_messages(qbot, limit=5)
        msg = next((m for m in msgs if m.reply_markup), None)
        if msg:
            clicked = False
            for row in msg.reply_markup.rows:
                for btn in row.buttons:
                    if req.order_choice.lower() in btn.text.lower():
                        await msg.click(text=btn.text); clicked = True; break
                if clicked: break
            if not clicked:
                await msg.click(text=msg.reply_markup.rows[0].buttons[0].text)
        await asyncio.sleep(6)

        # Havola olish — faqat yangi xabarlardan (start_msg_id dan keyin)
        raw_url = None
        for attempt in range(5):
            await asyncio.sleep(4)
            msgs = await userbot.get_messages(qbot, limit=10)
            # Faqat start_msg_id dan keyin kelgan xabarlar
            new_msgs = [m for m in msgs if m.id > start_msg_id]
            log.info(f"Havola qidirilmoqda (urinish {attempt+1}), {len(new_msgs)} ta yangi xabar")

            for m in new_msgs:
                # Tugma ichidagi URL — eng ishonchli
                if m.reply_markup:
                    for row in m.reply_markup.rows:
                        for btn in row.buttons:
                            if hasattr(btn, "url") and btn.url and "t.me" in btn.url:
                                raw_url = btn.url; break
                        if raw_url: break
                if raw_url: break

                # Matn ichidagi URL
                if m.text:
                    urls = re.findall(r"https?://t\.me/\S+", m.text)
                    for url in urls:
                        if "start" in url or "startgroup" in url:
                            raw_url = url; break
                    if not raw_url and urls:
                        raw_url = urls[0]
                if raw_url: break

                # Entity ichidagi URL
                if m.entities:
                    for ent in m.entities:
                        if hasattr(ent, "url") and ent.url and "t.me" in ent.url:
                            raw_url = ent.url; break
                if raw_url: break

            if raw_url:
                log.info(f"Havola topildi: {raw_url}")
                break
            log.warning(f"Havola topilmadi, {attempt+1}-urinish")

        if not raw_url:
            log.error("Havola 5 urinishdan keyin ham topilmadi")
            return None

        fixed = re.sub(r"(https?://t\.me/)([^?/]+)",
                       lambda m: m.group(1) + "QuizBot", raw_url, count=1)
        fixed = fixed.replace("?startgroup=", "?start=")
        return fixed

    except Exception as e:
        log.error(f"make_quiz xato: {e}")
        return None


# ============================================================
#  ADMIN NOTIFY — global funksiya
# ============================================================
async def notify_admin(text: str):
    """Barcha adminlarga xabar yuborish"""
    for admin_id in ADMIN_IDS:
        try:
            await bot_client.send_message(admin_id, text)
        except Exception as e:
            log.error(f"Admin notify xato: {e}")


async def log_action(uid: int, username: str, full_name: str, action: str):
    """User harakatini log guruhiga yuborish"""
    if not LOG_GROUP_ID:
        return
    try:
        uname = f"@{username}" if username else f"#{uid}"
        text = (
            f"👤 {full_name} ({uname})\n"
            f"🆔 `{uid}`\n"
            f"📌 {action}"
        )
        await bot_client.send_message(LOG_GROUP_ID, text)
    except Exception as e:
        log.error(f"Log yuborishda xato: {e}")


# ============================================================
#  NAVBAT ISHLOVCHISI
# ============================================================
async def queue_worker():
    log.info("Navbat ishlovchisi ishga tushdi")
    while True:
        try:
            if request_queue and account_pool:
                async with queue_lock:
                    if request_queue:  # Lock ichida qayta tekshiramiz
                        req = request_queue.popleft()
                        userbot = await get_free()
                        asyncio.create_task(run_request(userbot, req))
        except Exception as e:
            log.error(f"queue_worker xato: {e}")
        await asyncio.sleep(1)

async def run_request(userbot, req: QuizRequest):
    import time
    phone = account_phones.get(id(userbot), "?")
    started = time.time()
    try:
        await bot_client.send_message(
            req.chat_id,
            f"⏳ **Yaratilmoqda...**\n📚 {req.fan_name} V{req.variant_num}\n"
            f"❓ {len(req.questions)} savol | 📱 `{phone}`\n"
            f"🕐 ~{format_wait(estimate_seconds(len(req.questions)))}"
        )
        url = await make_quiz(userbot, req)
        elapsed = int(time.time() - started)
        tl = {"15": "15s", "30": "30s", "60": "60s", "0": "Chegarasiz"}

        if url:
            # DB ga quiz saqlash
            db_save_quiz(
                user_id    = req.user_id,
                fan_name   = req.fan_name,
                q_count    = len(req.questions),
                variant_num= req.variant_num,
                url        = url,
                time_choice= req.time_choice,
                order_type = req.order_choice,
                source     = getattr(req, 'source', 'file'),
            )
            # Admin ga xabar
            src = "🤖 AI" if getattr(req, 'source', 'file') == 'ai' else "📂 Fayl"
            await notify_admin(
                f"✅ **Quiz yaratildi**\n\n"
                f"👤 user: `{req.user_id}`\n"
                f"{src} | 📚 {req.fan_name} V{req.variant_num}\n"
                f"❓ {len(req.questions)} savol | 🕐 {format_wait(elapsed)}\n"
                f"🔗 {url}"
            )
            await bot_client.send_message(
                req.chat_id,
                f"✅ **Quiz tayyor!**\n\n"
                f"📚 {req.fan_name} — Variant {req.variant_num}\n"
                f"❓ {len(req.questions)} savol\n"
                f"⏱ {tl.get(req.time_choice, req.time_choice)} | "
                f"🔀 {'Aralash' if req.order_choice=='shuffle' else 'Ketma-ket'}\n"
                f"🕐 {format_wait(elapsed)}\n\n"
                f"🔗 {url}"
            )
        else:
            # Havola topilmadi — pulni qaytaramiz
            refund = calc_file_price(len(req.questions))
            db_add_balance(req.user_id, refund, f"Qaytarildi: quiz V{req.variant_num} xato")
            bal_left = db_get_balance(req.user_id)
            await bot_client.send_message(
                req.chat_id,
                f"❌ **Quiz yaratishda xato!**\n\n"
                f"Havola olinmadi — @QuizBot javob bermadi.\n"
                f"💰 **{refund:,} so'm qaytarildi** | Balans: {bal_left:,} so'm\n\n"
                f"Qayta urinib ko'ring:"
            )
    except Exception as e:
        # Xato — pulni qaytaramiz
        try:
            refund = calc_file_price(len(req.questions))
            db_add_balance(req.user_id, refund, f"Qaytarildi: xato — {str(e)[:50]}")
            bal_left = db_get_balance(req.user_id)
            await bot_client.send_message(
                req.chat_id,
                f"❌ **Xato yuz berdi!**\n\n`{e}`\n\n"
                f"💰 **{refund:,} so'm qaytarildi** | Balans: {bal_left:,} so'm"
            )
        except Exception as e2:
            log.error(f"Qaytarish xatosi: {e2}")
        log.error(f"run_request xato: {e}")
    finally:
        release(userbot)

# ============================================================
#  MAIN
# ============================================================
async def main():
    global bot_client

    bot_client = TelegramClient(
        _os.path.join(_os.path.dirname(DB_FILE), "bot_session"),
        API_ID, API_HASH
    )
    await bot_client.start(bot_token=BOT_TOKEN)
    log.info("Bot ulandi!")

    # DB ishga tushirish
    db_init()
    log.info(f"DB tayyor: {DB_FILE} | Jami users: {db_count_users()} ta")

    for phone in load_phones():
        try:
            sess_dir = _os.path.dirname(DB_FILE)
            session  = _os.path.join(sess_dir, f"userbot_{phone.replace('+','').replace(' ','')}")

            # DB dan sessiyani tiklash (sessiya fayli yo'q bo'lsa)
            if not _os.path.exists(session + ".session"):
                if db_load_session(phone, session):
                    log.info(f"Sessiya DB dan tiklandi: {phone}")

            client = TelegramClient(session, API_ID, API_HASH)

            async def password_input():
                pwd = input(f"🔐 2FA paroli ({phone}): ")
                return pwd

            await client.start(phone=phone, password=password_input)

            # Sessiyani DB ga saqlash — keyingi restart uchun
            db_save_session(phone, session)

            all_clients.append(client)
            account_phones[id(client)] = phone

            if phone == NOTIFY_PHONE:
                log.info(f"Notify akkaunt ulandi: {phone}")
            else:
                account_pool.append(client)
                account_busy[id(client)] = False
                log.info(f"Quiz akkaunt ulandi: {phone}")
        except Exception as e:
            log.error(f"Ulanmadi {phone}: {e}")

    # ============================================================
    #  KNOPKALAR
    # ============================================================
    def main_menu(adm=False):
        btns = [
            [Button.text("🤖 AI test tuzish",       resize=True)],
            [Button.text("📂 Fayldan quiz yaratish", resize=True),
             Button.text("✏️ Matn kiritish",         resize=True)],
            [Button.text("📋 Mening quizlarim",      resize=True)],
            [Button.text("💳 To'lov qilish",         resize=True),
             Button.text("💰 Balansni ko'rish",      resize=True)],
            [Button.text("🎁 Referal",               resize=True),
             Button.text("❓ Yordam",                resize=True)],
        ]
        if adm: btns.append([Button.text("🔧 Admin panel", resize=True)])
        return btns

    def ai_settings_btns(state: UserState):
        topic_show = state.topic if state.topic else "Barcha mavzu"
        return [
            [Button.text("📝 Fan nomini o'zgartirish"),
             Button.text("📌 Mavzuni o'zgartirish")],
            [Button.text("🔢 5 ta"),  Button.text("🔢 10 ta"),
             Button.text("🔢 15 ta"), Button.text("🔢 20 ta"),
             Button.text("🔢 25 ta")],
            [Button.text("🟢 Oson"), Button.text("🟡 O'rta"), Button.text("🔴 Qiyin")],
            [Button.text("🇺🇿 O'zbek"), Button.text("🇷🇺 Rus"), Button.text("🇬🇧 Ingliz")],
            [Button.text(f"✅ Yaratish — {state.q_count} ta savol")],
            [Button.text("🔙 Bosh menyu")],
        ]

    def time_btns():
        return [
            [Button.text("⏱ 15s"), Button.text("⏱ 30s")],
            [Button.text("⏱ 60s"), Button.text("⏱ Chegarasiz")],
        ]

    def order_btns():
        return [[Button.text("📋 Ketma-ket"), Button.text("🔀 Aralash")]]

    def variant_btns(total):
        rows, row = [], []
        for n in [5, 10, 15, 20, 25, 30, 50]:
            if total // n >= 1:
                row.append(Button.text(f"{n} ta"))
                if len(row) == 4: rows.append(row); row = []
        if row: rows.append(row)
        rows.append([Button.text("Hammasi bitta quiz")])
        return rows

    def answer_btns(opts):
        letters = ["A","B","C","D","E","F"]
        rows, row = [], []
        for i, opt in enumerate(opts):
            row.append(Button.text(f"{letters[i] if i<6 else i+1}. {opt[:18]}"))
            if len(row) == 2: rows.append(row); row = []
        if row: rows.append(row)
        rows.append([Button.text("⏭ O'tkazib yuborish"), Button.text("🔙 Bosh menyu")])
        return rows

    # ============================================================
    #  HANDLERLAR
    # ============================================================

    @bot_client.on(events.NewMessage(pattern="/start"))
    async def cmd_start(event):
        uid = event.sender_id
        is_new = db_is_new_user(uid)
        user_states[uid] = UserState()

        sender = await event.get_sender()
        first  = getattr(sender, 'first_name', '') or ''
        last   = getattr(sender, 'last_name',  '') or ''
        uname  = getattr(sender, 'username',   '') or ''
        full_name = f"{first} {last}".strip() or uname or str(uid)
        db_save_user(user_id=uid, first_name=first, last_name=last, username=uname)
        track_user(uid, full_name, "idle", "/start")

        # Referal tekshirish — /start ref_123456789
        ref_bonus_msg = ""
        raw = event.raw_text.strip()
        ref_match = re.match(r'^/start\s+ref_(\d+)$', raw)
        if ref_match and is_new:
            inviter_id = int(ref_match.group(1))
            if inviter_id != uid and not db_already_referred(inviter_id, uid):
                db_save_referral(inviter_id, uid)
                db_add_balance(uid, REFERRAL_BONUS, f"Referal bonusi — {inviter_id} taklif qildi")
                db_add_balance(inviter_id, REFERRAL_BONUS, f"Referal bonusi — {uid} qo'shildi")
                ref_count = db_get_referral_count(inviter_id)
                ref_bonus_msg = f"\n\n🎁 **Referal bonus: +{REFERRAL_BONUS:,} so'm** balansga qo'shildi!"
                try:
                    await bot_client.send_message(
                        inviter_id,
                        f"🎉 **Yangi referal!**\n\n"
                        f"👤 **{full_name}** sizning havolangiz orqali qo'shildi!\n"
                        f"💰 +{REFERRAL_BONUS:,} so'm balansga qo'shildi\n"
                        f"👥 Jami referallar: **{ref_count} ta**"
                    )
                except Exception:
                    pass
                await notify_admin(
                    f"🎁 **Referal**\n\n"
                    f"👤 {full_name} (`{uid}`) → `{inviter_id}` havolasidan keldi\n"
                    f"💰 Ikkalasiga +{REFERRAL_BONUS:,} so'm"
                )

        # Yangi foydalanuvchi bo'lsa admin ga xabar
        if is_new:
            total = db_count_users()
            uname_str = f"@{uname}" if uname else f"`{uid}`"
            await notify_admin(
                f"👤 **Yangi foydalanuvchi**\n\n"
                f"Ism: **{full_name}**\n"
                f"ID: `{uid}` | {uname_str}\n"
                f"Jami: {total} ta"
            )

        await event.respond(
            f"👋 **Salom! AI Quiz Bot**\n\n"
            f"🤖 AI yordamida istalgan fandan test tuzing!\n"
            f"📁 Fayl yuklang yoki matn kiriting{ref_bonus_msg}\n\n"
            f"Boshlash uchun tugmani bosing 👇",
            buttons=main_menu(is_admin(uid))
        )

    @bot_client.on(events.NewMessage(func=lambda e: e.file))
    async def on_file(event):
        uid = event.sender_id
        adm = is_admin(uid)
        log.info(f"Fayl keldi: user={uid}")

        # Log guruhiga xabar
        sender = await event.get_sender()
        first = getattr(sender, 'first_name', '') or ''
        last  = getattr(sender, 'last_name',  '') or ''
        uname = getattr(sender, 'username',   '') or ''
        full_name = f"{first} {last}".strip() or uname or str(uid)
        fname_attr = getattr(event.file, 'name', None) or getattr(event.file, 'mime_type', 'fayl')
        await log_action(uid, uname, full_name, f"📎 Fayl yubordi: {fname_attr}")

        # Faqat fayl kutilayotgan holatlarda davom etamiz
        # Boshqa admin holatlarda (masalan wait_phone) — ignore
        astate_step = admin_states.get(uid, {}).get("step", "")

        # Broadcast — fayl yuborganda ham ishlashi uchun
        if astate_step == "wait_broadcast" and is_admin(uid):
            admin_states.pop(uid, None)
            all_users = get_db().execute("SELECT user_id FROM users").fetchall()
            get_db().close()
            ok, fail = 0, 0
            prog = await event.respond(f"📤 Yuborilmoqda... 0/{len(all_users)}")
            for i, (target_id,) in enumerate(all_users):
                try:
                    await bot_client.forward_messages(target_id, event.message)
                    ok += 1
                except Exception as e:
                    log.warning(f"Broadcast xato user={target_id}: {e}")
                    fail += 1
                if (i + 1) % 20 == 0:
                    try:
                        await prog.edit(f"📤 Yuborilmoqda... {i+1}/{len(all_users)}")
                    except Exception:
                        pass
            await prog.edit(f"✅ Broadcast tugadi!\n✅ {ok} ta | ❌ {fail} ta")
            await event.respond("🔙", buttons=[[Button.text("🔙 Admin panel")]])
            return

        # User ID ga fayl yuborish
        if astate_step == "wait_user_msg_text" and is_admin(uid):
            target_id = admin_states[uid].get("target_id")
            admin_states.pop(uid, None)
            try:
                await bot_client.send_message(target_id, event.message)
                await event.respond(f"✅ User `{target_id}` ga yuborildi!", buttons=[[Button.text("🔙 Admin panel")]])
            except Exception as e:
                await event.respond(f"❌ Xato: {e}", buttons=[[Button.text("🔙 Admin panel")]])
            return

        if astate_step and astate_step not in ("wait_session_file", "wait_db_file"):
            log.info(f"Admin holati aktiv ({astate_step}), fayl ignore: user={uid}")
            return

        msg = await event.respond("📥 O'qilmoqda...")
        log.info(f"Fayl yuklanmoqda: user={uid}")

        try:
            import io

            buf = io.BytesIO()
            await event.download_media(file=buf)
            buf.seek(0)
            data = buf.read()
            log.info(f"Fayl yuklandi: {len(data)} bayt, user={uid}")

            if not data:
                await msg.edit("❌ Fayl bo'sh yoki yuklanmadi!")
                return

            # Kengaytma va MIME
            fname = ""
            mime  = ""
            try:
                fname = (event.file.name or "").lower()
                mime  = str(getattr(event.file, 'mime_type', '') or '')
            except Exception:
                pass

            log.info(f"Fayl: name={fname}, mime={mime}, user={uid}")

            content = ""

            if fname.endswith(".docx") or "officedocument.wordprocessingml" in mime:
                try:
                    from docx import Document
                    doc = Document(io.BytesIO(data))
                    content = "\n".join(p.text for p in doc.paragraphs)
                    log.info(f"DOCX o'qildi: {len(content)} belgi")
                except Exception as e:
                    log.error(f"DOCX xato: {e}")
                    await msg.edit(f"❌ DOCX o'qishda xato: {e}")
                    return

            elif fname.endswith(".pdf") or "pdf" in mime:
                try:
                    import PyPDF2
                    reader = PyPDF2.PdfReader(io.BytesIO(data))
                    content = "".join(p.extract_text() or "" for p in reader.pages)
                    log.info(f"PDF o'qildi: {len(content)} belgi")
                except Exception as e:
                    log.error(f"PDF xato: {e}")
                    await msg.edit(f"❌ PDF o'qishda xato: {e}")
                    return

            else:
                # TXT yoki boshqa
                content = data.decode("utf-8", errors="ignore")
                log.info(f"TXT o'qildi: {len(content)} belgi")

            if not content.strip():
                await msg.edit(
                    "❌ Fayldan matn o'qib bo'lmadi!\n\n"
                    "Qo'llab-quvvatlanadigan: **DOCX, PDF, TXT**"
                )
                return

            qs = _parse_questions(content)
            log.info(f"Parse natija: {len(qs)} savol, user={uid}")

            if qs:
                q_count = len(qs)
                price   = calc_file_price(q_count)
                bal     = db_get_balance(uid)
                blocks  = (q_count + 24) // 25

                # Savollarni RAM da saqlaymiz
                state = UserState(
                    step="wait_payment_file" if bal < price else "ask_fan_name",
                    questions=qs,
                    total_questions=q_count
                )
                user_states[uid] = state
                log.info(f"State saqlandi: step={state.step}, {q_count} savol, user={uid}")

                if bal < price:
                    await msg.edit(
                        f"📂 **{q_count} ta savol topildi!**\n\n"
                        f"💰 Narx: {blocks} × 2 000 = **{price:,} so'm**\n"
                        f"💼 Balansda: {bal:,} so'm\n"
                        f"❌ Yetishmaydi: **{price - bal:,} so'm**\n\n"
                        f"📌 Savollar saqlanib qoldi"
                    )
                    needed = price - bal
                    await event.respond(
                        "To'lov qiling:",
                        buttons=[
                            [Button.text(f"💳 {needed:,} so'm to'lash")],
                            [Button.text("🔙 Bosh menyu")],
                        ]
                    )
                else:
                    db_deduct_balance(uid, price, f"Fayl quiz: {q_count} ta savol")
                    bal_left = db_get_balance(uid)
                    await msg.edit(
                        f"📂 **{q_count} ta savol topildi!**\n"
                        f"💰 -{price:,} so'm | Qoldi: **{bal_left:,} so'm**"
                    )
                    await event.respond(
                        "Fan nomini yozing:",
                        buttons=[[Button.text("🔙 Bosh menyu")]]
                    )

            else:
                # Shablon topilmadi — manual (bepul)
                lines = [l.strip() for l in content.splitlines() if l.strip()]
                if not lines:
                    await msg.edit("❌ Faylda matn topilmadi!")
                    return

                state = UserState(step="manual_start")
                state.__dict__['raw_lines']     = lines
                state.__dict__['manual_q_idx']  = 0
                state.__dict__['manual_q_text'] = ""
                state.__dict__['manual_opts']   = []
                user_states[uid] = state
                log.info(f"Manual rejim: {len(lines)} qator, user={uid}")

                await msg.edit(
                    f"⚠️ **Shablon aniqlanmadi**\n\n"
                    f"{len(lines)} ta qator topildi.\n"
                    f"To'g'ri javoblarni siz belgilaysiz 👇\n"
                    f"_(Bu rejim bepul)_"
                )
                await event.respond(
                    "Davom etamizmi?",
                    buttons=[
                        [Button.text("▶️ Davom etish")],
                        [Button.text("🔙 Bosh menyu")],
                    ]
                )

        except Exception as e:
            log.error(f"on_file xato: {e}", exc_info=True)
            try:
                await msg.edit(f"❌ Xato: {e}")
            except Exception:
                await event.respond(f"❌ Xato: {e}", buttons=main_menu(adm))

    @bot_client.on(events.NewMessage(func=lambda e: not e.file and not e.text.startswith("/")))
    async def on_msg(event):
        uid = event.sender_id
        text = event.text.strip()
        adm = is_admin(uid)
        astate = admin_states.get(uid, {})

        # Har xabarda last_seen yangilash
        sender = await event.get_sender()
        first  = getattr(sender, 'first_name', '') or ''
        last   = getattr(sender, 'last_name',  '') or ''
        uname  = getattr(sender, 'username',   '') or ''
        full_name = f"{first} {last}".strip() or uname or str(uid)
        db_save_user(user_id=uid, first_name=first, last_name=last, username=uname)

        # Faol foydalanuvchini kuzatish
        state_now = user_states.get(uid, UserState())
        track_user(uid, full_name, state_now.step, text[:50])

        # User harakatini log guruhiga yuborish
        await log_action(uid, uname, full_name, f"✉️ {text[:150]}")

        # Admin oraliq holat
        if astate.get("step") == "wait_phone":
            await _admin_add_phone(event, uid, text); return
        if astate.get("step") == "wait_code":
            await _admin_enter_code(event, uid, text); return
        if astate.get("step") == "wait_password":
            await _admin_enter_pass(event, uid, text); return
        if astate.get("step") == "wait_remove":
            await _admin_do_remove(event, uid, text); return
        if astate.get("step") == "wait_bonus_user_id":
            await _admin_bonus_user_id(event, uid, text); return
        if astate.get("step") == "wait_bonus_amount":
            await _admin_bonus_amount(event, uid, text); return

        # ---- BROADCAST ----
        if astate.get("step") == "wait_broadcast":
            if not adm: return
            if text == "❌ Bekor qilish":
                admin_states.pop(uid, None)
                await _show_admin(event); return
            admin_states.pop(uid, None)
            all_users = get_db().execute("SELECT user_id FROM users").fetchall()
            get_db().close()
            ok, fail = 0, 0
            broadcast_text = event.message.text or text
            prog = await event.respond(f"📤 Yuborilmoqda... 0/{len(all_users)}")
            for i, (target_id,) in enumerate(all_users):
                try:
                    await bot_client.send_message(target_id, broadcast_text, parse_mode='md')
                    ok += 1
                except Exception as e:
                    log.warning(f"Broadcast xato user={target_id}: {e}")
                    fail += 1
                if (i + 1) % 20 == 0:
                    try:
                        await prog.edit(f"📤 Yuborilmoqda... {i+1}/{len(all_users)}")
                    except Exception:
                        pass
            await prog.edit(
                f"✅ Broadcast tugadi!\n\n"
                f"✅ Muvaffaqiyatli: {ok} ta\n"
                f"❌ Xato: {fail} ta"
            )
            await event.respond("🔙", buttons=[[Button.text("🔙 Admin panel")]])
            return

        # ---- USER ID GA XABAR ----
        if astate.get("step") == "wait_user_id_msg":
            if not adm: return
            if text == "❌ Bekor qilish":
                admin_states.pop(uid, None)
                await _show_admin(event); return
            try:
                target_id = int(text.strip())
                admin_states[uid] = {"step": "wait_user_msg_text", "target_id": target_id}
                await event.respond(
                    f"📨 User ID: `{target_id}`\n\nYubormoqchi bo'lgan xabarni yozing:",
                    buttons=[[Button.text("❌ Bekor qilish")]]
                )
            except ValueError:
                await event.respond("❌ Noto'g'ri ID! Faqat raqam yozing:", buttons=[[Button.text("❌ Bekor qilish")]])
            return

        if astate.get("step") == "wait_user_msg_text":
            if not adm: return
            if text == "❌ Bekor qilish":
                admin_states.pop(uid, None)
                await _show_admin(event); return
            target_id = astate.get("target_id")
            admin_states.pop(uid, None)
            try:
                await bot_client.send_message(target_id, event.message)
                await event.respond(f"✅ User `{target_id}` ga xabar yuborildi!", buttons=[[Button.text("🔙 Admin panel")]])
            except Exception as e:
                await event.respond(f"❌ Xato: {e}", buttons=[[Button.text("🔙 Admin panel")]])
            return

        state = user_states.get(uid, UserState())

        # ---- BOSH MENYU KNOPKALARI ----
        if text == "🔙 Bosh menyu":
            user_states[uid] = UserState()
            admin_states.pop(uid, None)
            await event.respond("🏠 Bosh menyu", buttons=main_menu(adm))
            return

        if text == "📋 Mening quizlarim":
            quizzes = db_get_user_quizzes(uid, limit=20)
            total_q = db_count_user_quizzes(uid)
            if not quizzes:
                await event.respond(
                    "📋 **Mening quizlarim**\n\n"
                    "Hali quiz yaratmagansiz.\n\n"
                    "🤖 AI yoki 📂 fayl orqali quiz tuzing!",
                    buttons=main_menu(adm)
                )
                return

            lines = [f"📋 **Mening quizlarim ({total_q} ta)**\n"]
            for q in quizzes:
                qid, fan, q_count, variant, url, source, created = q
                src_icon = "🤖" if source == "ai" else "📂"
                date = created[:10] if created else ""
                lines.append(
                    f"{src_icon} **{fan}** V{variant} — {q_count} savol\n"
                    f"   📅 {date} | [▶️ Ochish]({url})"
                )

            await event.respond(
                "\n\n".join(lines),
                buttons=[[Button.text("🔙 Bosh menyu")]],
                link_preview=False
            )
            return

        if text == "🎁 Referal":
            ref_count = db_get_referral_count(uid)
            ref_list  = db_get_referral_list(uid, limit=10)
            me = await event.get_sender()
            bot_me = await bot_client.get_me()
            bot_username = bot_me.username
            ref_link = f"https://t.me/{bot_username}?start=ref_{uid}"
            bal = db_get_balance(uid)

            lines = [
                f"🎁 **Referal dasturi**\n",
                f"👥 Siz taklif qilganlar: **{ref_count} ta**",
                f"💰 Har bir referal uchun: **{REFERRAL_BONUS:,} so'm** (ikkalangizga)\n",
                f"🔗 **Sizning havolangiz:**",
                f"`{ref_link}`\n",
                f"📌 Do'stingizga shu havolani yuboring. U ro'yxatdan o'tganda ikkalangizga **{REFERRAL_BONUS:,} so'm** beriladi!",
            ]

            if ref_list:
                lines.append(f"\n👤 **So'nggi referallar:**")
                for r in ref_list:
                    r_id, r_first, r_last, r_uname, r_date = r
                    r_name = f"{r_first or ''} {r_last or ''}".strip() or r_uname or str(r_id)
                    r_date_short = r_date[:10] if r_date else ""
                    lines.append(f"  • {r_name} — {r_date_short}")

            await event.respond(
                "\n".join(lines),
                buttons=[[Button.text("🔙 Bosh menyu")]],
                link_preview=False
            )
            return

        if text == "❓ Yordam":
            await event.respond(
                "📋 **YORDAM**\n\n"
                "**🤖 AI test tuzish** — 2 000 so'm\n"
                "Istalgan fan va mavzudan AI avtomatik test tuzadi\n\n"
                "**📂 Fayldan quiz yaratish** — har 25 savolga 2 000 so'm\n"
                "Tayyor testingizni yuklang, bot quizga aylantiradi\n\n"
                "━━━━━━━━━━━━━━━\n"
                "**📌 Fayl formatlari:** DOCX, PDF, TXT\n\n"
                "**Shablon 1:**\n```\n1.Savol\na.Variant\n#b.To'g'ri\nc.Variant\n```\n\n"
                "**Shablon 2:**\n```\nSavol\na.Variant\n#b.To'g'ri\nc.Variant\n```\n\n"
                "**Shablon 3:**\n```\nSavol\n=====\n#To'g'ri\nVariant\n+++++\n```\n\n"
                "**# belgisi** = to'g'ri javob",
                buttons=main_menu(adm)
            )
            return

        if text == "🔧 Admin panel":
            if not adm: await event.respond("⛔ Admin emassiz!"); return
            await _show_admin(event); return

        if text == "📂 Fayldan quiz yaratish":
            user_states[uid] = UserState(step="wait_file")
            await event.respond(
                "📂 **Fayldan quiz yaratish**\n\n"
                "DOCX, PDF yoki TXT fayl yuboring\n\n"
                "📌 **Narx:** Har 25 savolga 1 500 so'm\n"
                "_(50 savol = 3 000, 100 savol = 6 000)_\n\n"
                "❓ /yordam — fayl formati haqida",
                buttons=[[Button.text("🔙 Bosh menyu")]]
            ); return

        if text == "✏️ Matn kiritish":
            user_states[uid] = UserState(step="wait_text")
            await event.respond("✏️ Savollarni yuboring:\n/yordam — shablonlar",
                buttons=[[Button.text("🔙 Bosh menyu")]]); return

        # ---- AI TEST TUZISH ----
        if text == "🤖 AI test tuzish":
            state = UserState(step="ai_ask_fan")
            user_states[uid] = state
            await event.respond(
                "🤖 **AI Test Tuzish**\n\nQaysi fandan test kerak?\n\n"
                "_(Misol: Matematika, Fizika, Tarix, Python...)_",
                buttons=[[Button.text("🔙 Bosh menyu")]]
            ); return

        if state.step == "ai_ask_fan":
            state.fan_name = text
            state.step = "ai_ask_topic"
            user_states[uid] = state
            await event.respond(
                f"📚 Fan: **{text}**\n\n"
                f"Qaysi mavzudan savol tuzish kerak?\n\n"
                f"_(Misol: Kasrlar, Fotosintez, II Jahon urushi...)_\n\n"
                f"Barcha mavzudan bo'lsa 👇 tugmani bosing:",
                buttons=[
                    [Button.text("📖 Barcha mavzudan")],
                    [Button.text("🔙 Bosh menyu")],
                ]
            ); return

        if state.step == "ai_ask_topic":
            if text == "📖 Barcha mavzudan":
                state.topic = ""
            else:
                state.topic = text
            state.step = "ai_settings"
            user_states[uid] = state
            topic_show = state.topic if state.topic else "Barcha mavzu"
            await event.respond(
                f"📝 Fan: **{state.fan_name}**\n"
                f"📌 Mavzu: **{topic_show}**\n"
                f"🔢 Savol: {state.q_count} ta | 🎯 {state.difficulty} | 🌐 {state.lang}\n\n"
                f"Sozlash yoki yaratish:",
                buttons=ai_settings_btns(state)
            ); return

        if state.step == "ai_settings":
            topic_show = state.topic if state.topic else "Barcha mavzu"

            def _show_settings():
                return (
                    f"📝 Fan: **{state.fan_name}**\n"
                    f"📌 Mavzu: **{topic_show}**\n"
                    f"🔢 {state.q_count} ta | 🎯 {state.difficulty} | 🌐 {state.lang}"
                )

            # Fan nomini o'zgartirish
            if text == "📝 Fan nomini o'zgartirish":
                state.step = "ai_ask_fan"
                user_states[uid] = state
                await event.respond("Yangi fan nomini yozing:",
                    buttons=[[Button.text("🔙 Bosh menyu")]]); return

            # Mavzuni o'zgartirish
            if text == "📌 Mavzuni o'zgartirish":
                state.step = "ai_ask_topic"
                user_states[uid] = state
                await event.respond(
                    f"📚 Fan: **{state.fan_name}**\n\nYangi mavzuni yozing:",
                    buttons=[
                        [Button.text("📖 Barcha mavzudan")],
                        [Button.text("🔙 Bosh menyu")],
                    ]
                ); return

            # Savol soni
            if re.match(r'^🔢 (\d+) ta$', text):
                state.q_count = int(re.match(r'^🔢 (\d+) ta$', text).group(1))
                user_states[uid] = state
                await event.respond(_show_settings(), buttons=ai_settings_btns(state)); return

            # Qiyinlik
            diff_map = {"🟢 Oson": "oson", "🟡 O'rta": "o'rta", "🔴 Qiyin": "qiyin"}
            if text in diff_map:
                state.difficulty = diff_map[text]
                user_states[uid] = state
                await event.respond(_show_settings(), buttons=ai_settings_btns(state)); return

            # Til
            lang_map = {"🇺🇿 O'zbek": "uz", "🇷🇺 Rus": "ru", "🇬🇧 Ingliz": "en"}
            if text in lang_map:
                state.lang = lang_map[text]
                user_states[uid] = state
                await event.respond(_show_settings(), buttons=ai_settings_btns(state)); return

            # YARATISH
            if text.startswith("✅ Yaratish"):
                # Balans tekshirish
                bal = db_get_balance(uid)
                if bal < AI_PRICE:
                    # Stateni saqlab qo'yamiz — to'lovdan keyin qaytamiz
                    state.step = "wait_payment"
                    user_states[uid] = state
                    await event.respond(
                        f"❌ **Balans yetarli emas!**\n\n"
                        f"💰 Balans: {bal:,} so'm\n"
                        f"💳 Kerak: {AI_PRICE:,} so'm\n\n"
                        f"📌 Sozlamalaringiz saqlanib qoldi!\n"
                        f"To'lov qilib, qaytib keling — boshidan kiritmasiz:",
                        buttons=[
                            [Button.text(f"💳 {AI_PRICE:,} so'm to'lash")],
                            [Button.text("🔙 Bosh menyu")],
                        ]
                    )
                    return

                state.step = "ai_generating"
                user_states[uid] = state
                topic_label = f" | 📌 {state.topic}" if state.topic else ""
                await event.respond(
                    f"🤖 **AI test tuzmoqda...**\n\n"
                    f"📚 {state.fan_name}{topic_label}\n"
                    f"🔢 {state.q_count} ta | 🎯 {state.difficulty} | 🌐 {state.lang}\n\n"
                    f"⏳ Bir oz kuting..."
                )
                try:
                    qs = await generate_questions(
                        state.fan_name, state.q_count,
                        state.lang, state.difficulty, state.topic
                    )
                    if not qs:
                        await event.respond("❌ AI savol yarata olmadi! Qayta urining.",
                            buttons=main_menu(adm)); return

                    state.questions = qs
                    state.total_questions = len(qs)
                    state.per_variant = len(qs)
                    state.step = "ask_time"
                    state.__dict__['source'] = "ai"   # manba belgisi
                    user_states[uid] = state

                    # Balansdan yechish faqat savollar muvaffaqiyatli yaratilganda
                    db_deduct_balance(uid, AI_PRICE, f"AI test: {state.fan_name}")
                    bal_left = db_get_balance(uid)

                    await event.respond(
                        f"✅ **{len(qs)} ta savol tayyor!**\n"
                        f"💰 -{AI_PRICE:,} so'm | Qoldi: {bal_left:,} so'm\n\n"
                        f"⏱ Vaqt:",
                        buttons=time_btns()
                    )
                except Exception as e:
                    log.error(f"AI xato: {e}")
                    await event.respond(
                        f"❌ AI xato: {e}\n\nGROQ_API_KEY ni tekshiring!",
                        buttons=main_menu(adm)
                    )
                return

        # ---- MATN HOLAT ----
        if state.step == "wait_text":
            qs = _parse_questions(text)
            if qs:
                state.questions = qs
                state.total_questions = len(qs)
                state.step = "ask_fan_name"
                user_states[uid] = state
                await event.respond(f"✅ **{len(qs)} ta savol!**\n\nFan nomini yozing:",
                    buttons=[[Button.text("🔙 Bosh menyu")]]); return
            else:
                lines = [l.strip() for l in text.splitlines() if l.strip()]
                state.step = "manual_start"
                state.__dict__['raw_lines'] = lines
                state.__dict__['manual_q_idx'] = 0
                state.__dict__['manual_q_text'] = ""
                state.__dict__['manual_opts'] = []
                user_states[uid] = state
                await event.respond(
                    f"⚠️ Shablon aniqlanmadi. {len(lines)} ta qator.\nJavoblarni siz ko'rsatasiz:",
                    buttons=[[Button.text("▶️ Davom etish")], [Button.text("🔙 Bosh menyu")]]
                ); return

        # ---- MANUAL REJIM ----
        if state.step == "manual_start" and text == "▶️ Davom etish":
            state.step = "manual_detect"
            state.questions = []
            state.__dict__['manual_q_idx'] = 0
            user_states[uid] = state
            await _ask_manual(event, uid, state); return

        if state.step == "manual_answer":
            await _handle_manual(event, uid, state, text); return

        # ---- FAN NOMI ----
        if state.step == "ask_fan_name":
            state.fan_name = text
            state.step = "ask_split"
            user_states[uid] = state
            total = state.total_questions
            await event.respond(
                f"📚 **{text}** | ❓ {total} savol\n\nHar variantda necha ta?",
                buttons=variant_btns(total)); return

        # ---- VARIANT SONI ----
        if state.step == "ask_split":
            n = 0 if text == "Hammasi bitta quiz" else int(re.sub(r'\D', '', text) or 0)
            state.per_variant = state.total_questions if n == 0 else max(1, min(n, state.total_questions))
            pv, total = state.per_variant, state.total_questions
            nv = (total + pv - 1) // pv
            state.step = "ask_time"
            user_states[uid] = state
            await event.respond(
                f"✅ **{nv} ta variant** × {pv} savol\n\n⏱ Vaqt:",
                buttons=time_btns()); return

        # ---- VAQT ----
        if state.step == "ask_time":
            tm = {"⏱ 15s": "15", "⏱ 30s": "30", "⏱ 60s": "60", "⏱ Chegarasiz": "0"}
            if text not in tm:
                await event.respond("Tugmadan tanlang!", buttons=time_btns()); return
            state.time_choice = tm[text]
            state.step = "ask_order"
            user_states[uid] = state
            await event.respond("🔀 Tartib:", buttons=order_btns()); return

        # ---- TARTIB ----
        if state.step == "ask_order":
            if text == "📋 Ketma-ket":   state.order_choice = "order"
            elif text == "🔀 Aralash":   state.order_choice = "shuffle"
            else:
                await event.respond("Tugmadan tanlang!", buttons=order_btns()); return

            total, pv = state.total_questions, state.per_variant
            nv = (total + pv - 1) // pv
            state.step = "idle"
            user_states[uid] = state
            qs = state.questions
            tl = {"15": "15s", "30": "30s", "60": "60s", "0": "Chegarasiz"}
            new_reqs = [
                QuizRequest(
                    user_id=uid, chat_id=event.chat_id,
                    questions=qs[v*pv:min((v+1)*pv, total)],
                    fan_name=state.fan_name, variant_num=v+1,
                    time_choice=state.time_choice,
                    order_choice=state.order_choice, total_variants=nv,
                    source=getattr(state, 'source', 'file'),
                ) for v in range(nv)
            ]
            pos = len(request_queue) + 1
            free_acc = sum(1 for v in account_busy.values() if not v)
            wait_str = calc_wait(new_reqs)

            await event.respond(
                f"📋 **Xulosa:**\n\n"
                f"📚 {state.fan_name}\n"
                f"❓ {total} savol → {nv} ta variant × {pv}\n"
                f"⏱ {tl.get(state.time_choice)} | "
                f"🔀 {'Aralash' if state.order_choice=='shuffle' else 'Ketma-ket'}\n\n"
                f"📍 Navbat: #{pos}\n"
                f"🟢 Bo'sh: {free_acc}/{len(account_pool)}\n"
                f"⏳ ~{wait_str}",
                buttons=main_menu(adm)
            )
            async with queue_lock:
                for req in new_reqs:
                    request_queue.append(req)
            return

    # ============================================================
    #  MANUAL REJIM FUNKSIYALARI
    # ============================================================
    async def _ask_manual(event, uid, state):
        lines = state.__dict__.get('raw_lines', [])
        idx   = state.__dict__.get('manual_q_idx', 0)
        if idx >= len(lines):
            if not state.questions:
                await event.respond("❌ Savol qo'shilmadi!", buttons=main_menu(is_admin(uid)))
                user_states[uid] = UserState(); return
            state.step = "ask_fan_name"
            state.total_questions = len(state.questions)
            user_states[uid] = state
            await event.respond(
                f"✅ **{len(state.questions)} ta savol tayyor!**\n\nFan nomini yozing:",
                buttons=[[Button.text("🔙 Bosh menyu")]]); return

        q_text = lines[idx]
        opts = []
        i = idx + 1
        while i < len(lines) and len(opts) < 6:
            opts.append(lines[i]); i += 1

        if len(opts) < 2:
            state.__dict__['manual_q_idx'] = i
            user_states[uid] = state
            await _ask_manual(event, uid, state); return

        state.__dict__['manual_q_text'] = q_text
        state.__dict__['manual_opts']   = opts
        state.__dict__['manual_q_idx']  = i
        state.step = "manual_answer"
        user_states[uid] = state

        letters = ["A","B","C","D","E","F"]
        opts_txt = "\n".join(f"  {letters[j] if j<6 else j+1}. {o}" for j, o in enumerate(opts))
        done = len(state.questions)
        approx = len(lines) // (len(opts) + 1)

        await event.respond(
            f"📝 **Savol {done+1}** (~{approx} ta):\n\n❓ {q_text}\n\n{opts_txt}\n\n✅ To'g'ri javob:",
            buttons=answer_btns(opts)
        )

    async def _handle_manual(event, uid, state, text):
        if text == "⏭ O'tkazib yuborish":
            await _ask_manual(event, uid, state); return
        letters = ["A","B","C","D","E","F"]
        opts = state.__dict__.get('manual_opts', [])
        correct = None
        for i, opt in enumerate(opts):
            ltr = letters[i] if i < 6 else str(i+1)
            if text.startswith(f"{ltr}."):
                correct = i; break
        if correct is None:
            await event.respond("Tugmadan tanlang!", buttons=answer_btns(opts)); return
        state.questions.append({
            "q": state.__dict__.get('manual_q_text', ''),
            "opts": opts, "ans": correct
        })
        user_states[uid] = state
        await _ask_manual(event, uid, state)

    # ============================================================
    #  ADMIN PANEL
    # ============================================================
    # ============================================================
    #  ADMIN NOTIFY — muhim hodisalarda xabar
    # ============================================================
    # Faol foydalanuvchilar: {user_id: {name, step, last_action, time}}
    active_users: dict = {}

    def track_user(uid: int, name: str, step: str, action: str):
        """Faol foydalanuvchini kuzatish"""
        active_users[uid] = {
            "name":   name,
            "step":   step,
            "action": action,
            "time":   datetime.now().strftime("%H:%M:%S"),
        }

    async def _show_admin(event):
        busy = sum(1 for v in account_busy.values() if v)
        free = len(account_pool) - busy
        rows = [f"  {i+1}. {'🔴' if account_busy.get(id(c)) else '🟢'} `{account_phones.get(id(c),'?')}`"
                for i, c in enumerate(account_pool)]
        total_users = db_count_users()
        stats = db_payment_stats()
        await event.respond(
            f"🔧 **ADMIN PANEL**\n\n"
            f"👥 Foydalanuvchilar: **{total_users} ta**\n"
            f"👀 Faol hozir: **{len(active_users)} ta**\n"
            f"💰 Bugungi daromad: **{stats['today']:,} so'm**\n"
            f"💵 Jami daromad: **{stats['total']:,} so'm**\n"
            f"⏳ Kutilayotgan to'lov: **{stats['pending']} ta**\n\n"
            f"📱 Akkauntlar: **{len(account_pool)} ta**\n"
            f"  🟢 Bo'sh: {free} | 🔴 Band: {busy}\n"
            f"📋 Navbat: **{len(request_queue)} ta**\n\n" +
            ("\n".join(rows) if rows else "  (yo'q)"),
            buttons=[
                [Button.text("👥 Userlar ro'yxati"), Button.text("💳 To'lovlar")],
                [Button.text("👀 Faol foydalanuvchilar"), Button.text("📋 Navbat")],
                [Button.text("➕ Akkaunt qo'shish"), Button.text("➖ Akkaunt o'chirish")],
                [Button.text("💸 Userga pul yuborish"), Button.text("📊 Holat")],
                [Button.text("📤 Sessiya yuklash"), Button.text("⬇️ DB yuklash")],
                [Button.text("⬆️ DB yuklash (yangi)"), Button.text("🗑 Navbatni tozalash")],
                [Button.text("📢 Barchaga xabar"), Button.text("📨 ID ga xabar")],
                [Button.text("🔙 Bosh menyu")],
            ]
        )

    @bot_client.on(events.NewMessage(pattern="/cancel"))
    async def cmd_cancel(event):
        uid = event.sender_id
        if uid in admin_states:
            admin_states.pop(uid)
            await event.respond("❌ Bekor qilindi.", buttons=main_menu(is_admin(uid)))
        else:
            await event.respond("Hech narsa bekor qilinmadi.")

    @bot_client.on(events.NewMessage(pattern="/admin"))
    async def cmd_admin(event):
        if not is_admin(event.sender_id):
            await event.respond("⛔ Admin emassiz!"); return
        await _show_admin(event)

    # ============================================================
    #  DB YUKLAB OLISH VA YUKLASH
    # ============================================================

    @bot_client.on(events.NewMessage(pattern="/dbyuklash"))
    async def cmd_db_download(event):
        """DB faylini foydalanuvchiga yuborish"""
        if not is_admin(event.sender_id):
            await event.respond("⛔ Admin emassiz!"); return

        if not _os.path.exists(DB_FILE):
            await event.respond("❌ DB fayl topilmadi!")
            return

        size = _os.path.getsize(DB_FILE)
        await event.respond(
            f"📦 **Ma'lumotlar bazasi**\n\n"
            f"📁 `{_os.path.basename(DB_FILE)}`\n"
            f"💾 Hajm: {size / 1024:.1f} KB\n\n"
            f"⬇️ Yuklanmoqda..."
        )
        await bot_client.send_file(
            event.chat_id,
            DB_FILE,
            caption=(
                f"🗄 **bot.db** — {size / 1024:.1f} KB\n"
                f"📅 {__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"
                f"Bu faylni tahrir qilib /newdbyuklash orqali qayta yuboring."
            ),
            force_document=True
        )
        log.info(f"DB yuklandi: admin={event.sender_id}, size={size}")

    @bot_client.on(events.NewMessage(pattern="/newdbyuklash"))
    async def cmd_db_upload_prompt(event):
        """Yangi DB yuklash uchun ko'rsatma"""
        if not is_admin(event.sender_id):
            await event.respond("⛔ Admin emassiz!"); return

        admin_states[event.sender_id] = {"step": "wait_db_file"}
        await event.respond(
            "📤 **Yangi DB yuklash**\n\n"
            "⚠️ **Diqqat!** Joriy ma'lumotlar bazasi almashtiriladi!\n\n"
            "1. /dbyuklash orqali eski DB ni yuklang\n"
            "2. SQLite editor bilan tahrirlang\n"
            "3. Tahrirlangan `.db` faylni shu yerga yuboring\n\n"
            "Bot fayl qabul qilgach avtomatik qayta ishga tushadi.\n\n"
            "/cancel — bekor qilish",
            buttons=[[Button.text("🔙 Bosh menyu")]]
        )

    @bot_client.on(events.NewMessage(
        func=lambda e: e.file and
        admin_states.get(e.sender_id, {}).get("step") == "wait_db_file"
    ))
    async def cmd_db_receive(event):
        """Yangi DB faylini qabul qilish va almashtirish"""
        uid = event.sender_id
        if not is_admin(uid):
            return

        fname = getattr(event.file, 'name', '') or ''
        if not fname.lower().endswith('.db') and not fname.lower().endswith('.sqlite'):
            await event.respond(
                "❌ Faqat `.db` yoki `.sqlite` fayl yuboring!\n"
                "Qayta urinib ko'ring yoki /cancel."
            )
            return

        try:
            msg = await event.respond("📥 Yangi DB yuklanmoqda...")

            import io, shutil

            # Yangi DB ni olish
            buf = io.BytesIO()
            await event.download_media(file=buf)
            buf.seek(0)
            new_data = buf.read()

            if len(new_data) < 100:
                await msg.edit("❌ Fayl juda kichik yoki buzilgan!")
                return

            # SQLite fayl ekanligini tekshirish
            if not new_data.startswith(b'SQLite format 3'):
                await msg.edit("❌ Bu SQLite fayl emas!")
                return

            # Eski DB ni zaxiralash
            backup_path = DB_FILE + ".backup"
            if _os.path.exists(DB_FILE):
                shutil.copy2(DB_FILE, backup_path)
                log.info(f"DB zaxira: {backup_path}")

            # Yangi DB ni yozish
            with open(DB_FILE, 'wb') as f:
                f.write(new_data)

            admin_states.pop(uid, None)

            # Statistika
            import sqlite3 as _sq
            con = _sq.connect(DB_FILE)
            users_n  = con.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            quizzes_n = con.execute("SELECT COUNT(*) FROM quizzes").fetchone()[0]
            pays_n   = con.execute("SELECT COUNT(*) FROM payments WHERE status='confirmed'").fetchone()[0]
            con.close()

            await msg.edit(
                f"✅ **DB muvaffaqiyatli yangilandi!**\n\n"
                f"👥 Foydalanuvchilar: {users_n} ta\n"
                f"🎯 Quizlar: {quizzes_n} ta\n"
                f"💰 To'lovlar: {pays_n} ta\n\n"
                f"💾 Zaxira: `{_os.path.basename(backup_path)}`\n\n"
                f"✅ Bot yangi DB bilan ishlaydi!"
            )
            log.info(f"DB yangilandi: admin={uid}, size={len(new_data)}")

        except Exception as e:
            log.error(f"DB yuklash xato: {e}")
            admin_states.pop(uid, None)
            await event.respond(f"❌ Xato: {e}")

    @bot_client.on(events.NewMessage(
        func=lambda e: not e.file and not e.text.startswith("/")
        and e.sender_id in ADMIN_IDS
        and e.text.strip() in ["➕ Akkaunt qo'shish","➖ Akkaunt o'chirish",
                                "📊 Holat","📋 Navbat","🗑 Navbatni tozalash",
                                "🔙 Admin panel", "👥 Userlar ro'yxati",
                                "💳 To'lovlar", "📤 Sessiya yuklash",
                                "⬇️ DB yuklash", "⬆️ DB yuklash (yangi)",
                                "👀 Faol foydalanuvchilar", "💸 Userga pul yuborish",
                                "💸 Yana yuborish", "📢 Barchaga xabar", "📨 ID ga xabar"]
    ))
    async def admin_btns(event):
        uid = event.sender_id
        text = event.text.strip()

        if text == "👀 Faol foydalanuvchilar":
            if not active_users:
                await event.respond(
                    "👀 **Faol foydalanuvchilar**\n\nHozir hech kim faol emas.",
                    buttons=[[Button.text("🔙 Admin panel")]]
                )
                return
            step_names = {
                "idle": "🏠 Bosh menyu",
                "ai_ask_fan": "🤖 Fan nomi yozmoqda",
                "ai_ask_topic": "🤖 Mavzu yozmoqda",
                "ai_settings": "🤖 AI sozlamalar",
                "ai_generating": "🤖 AI generatsiya kutmoqda",
                "wait_file": "📂 Fayl kutmoqda",
                "wait_text": "✏️ Matn yozmoqda",
                "wait_payment": "💳 To'lov kutmoqda (AI)",
                "wait_payment_file": "💳 To'lov kutmoqda (fayl)",
                "ask_fan_name": "📚 Fan nomi kiritmoqda",
                "ask_split": "🔢 Variant soni tanlayapti",
                "ask_time": "⏱ Vaqt tanlayapti",
                "ask_order": "🔀 Tartib tanlayapti",
                "manual_start": "✋ Manual rejim boshladi",
                "manual_detect": "✋ Manual savol ko'rib chiqmoqda",
                "manual_answer": "✋ Javob ko'rsatmoqda",
            }
            lines = [f"👀 **Faol foydalanuvchilar: {len(active_users)} ta**\n"]
            for u_id, info in list(active_users.items()):
                step_label = step_names.get(info['step'], info['step'])
                lines.append(
                    f"• **{info['name']}** (`{u_id}`)\n"
                    f"  {step_label}\n"
                    f"  📝 {info['action']}\n"
                    f"  🕐 {info['time']}"
                )
            await event.respond(
                "\n\n".join(lines),
                buttons=[[Button.text("🔄 Yangilash"), Button.text("🔙 Admin panel")]]
            )
            return

        if text == "🔙 Admin panel":
            admin_states.pop(uid, None)
            await _show_admin(event)

        elif text == "📤 Sessiya yuklash":
            admin_states[uid] = {"step": "wait_session_file"}
            # Mavjud akkauntlarni ko'rsatish
            existing = [account_phones.get(id(c), "?") for c in account_pool]
            notify_p = account_phones.get(id(notify_client_holder.get("client")), "") if notify_client_holder.get("client") else ""
            lines = ["📤 **Sessiya fayli yuklash**\n"]
            lines.append("Hozirgi akkauntlar:")
            for p in existing:
                lines.append(f"  🟢 {p}")
            if notify_p:
                lines.append(f"  🔔 {notify_p} (notify)")
            if not existing and not notify_p:
                lines.append("  (yo'q)")
            lines.append("\n`.session` faylini yuboring:")
            lines.append("_(Misol: userbot_998901234567.session)_\n")
            lines.append("/cancel — bekor")
            await event.respond(
                "\n".join(lines),
                buttons=[[Button.text("🔙 Admin panel")]]
            )

        elif text == "⬇️ DB yuklash":
            # /dbyuklash bilan bir xil
            if not _os.path.exists(DB_FILE):
                await event.respond("❌ DB fayl topilmadi!",
                    buttons=[[Button.text("🔙 Admin panel")]]); return
            size = _os.path.getsize(DB_FILE)
            await bot_client.send_file(
                event.chat_id,
                DB_FILE,
                caption=(
                    f"🗄 **bot.db** — {size/1024:.1f} KB\n"
                    f"📅 {__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"
                    f"Tahrir qilib /newdbyuklash orqali qayta yuboring."
                ),
                force_document=True
            )

        elif text == "⬆️ DB yuklash (yangi)":
            admin_states[uid] = {"step": "wait_db_file"}
            await event.respond(
                "📤 **Yangi DB yuklash**\n\n"
                "⚠️ Joriy ma'lumotlar bazasi almashtiriladi!\n\n"
                "Tahrirlangan `.db` faylni yuboring:\n\n"
                "/cancel — bekor",
                buttons=[[Button.text("🔙 Admin panel")]]
            )

        elif text == "👥 Userlar ro'yxati":
            total = db_count_users()
            users = db_get_users(limit=20)
            if not users:
                await event.respond("👥 Hali foydalanuvchi yo'q.",
                    buttons=[[Button.text("🔙 Admin panel")]]); return
            lines = [f"👥 **Foydalanuvchilar: {total} ta**\n"]
            for u in users:
                user_id, first, last, uname, created, last_seen = u
                name = f"{first} {last}".strip() or "Nomsiz"
                uname_str = f"@{uname}" if uname else "username yo'q"
                lines.append(
                    f"• {name} ({uname_str})\n"
                    f"  ID: `{user_id}` | {last_seen[:10]}"
                )
            if total > 20:
                lines.append(f"\n_...va yana {total-20} ta_")
            await event.respond(
                "\n".join(lines),
                buttons=[[Button.text("🔙 Admin panel")]]
            )

        elif text == "➕ Akkaunt qo'shish":
            admin_states[uid] = {"step": "wait_phone"}
            await event.respond("📱 Telefon raqam:\n_(+998901234567)_",
                buttons=[[Button.text("🔙 Admin panel")]])

        elif text == "➖ Akkaunt o'chirish":
            if not account_pool:
                await event.respond("❌ Akkaunt yo'q!"); return
            btns = [[Button.text(account_phones.get(id(c), "?"))] for c in account_pool]
            btns.append([Button.text("🔙 Admin panel")])
            admin_states[uid] = {"step": "wait_remove"}
            await event.respond("Qaysi raqamni o'chirish?", buttons=btns)

        elif text == "💸 Yana yuborish":
            admin_states[uid] = {"step": "wait_bonus_user_id"}
            await event.respond(
                "💸 **Userga bonus yuborish**\n\nUser ID ni yozing:",
                buttons=[[Button.text("🔙 Admin panel")]]
            )

        elif text == "💸 Userga pul yuborish":
            admin_states[uid] = {"step": "wait_bonus_user_id"}
            await event.respond(
                "💸 **Userga bonus yuborish**\n\n"
                "User ID ni yozing:\n"
                "_(Misol: 7693087447)_\n\n"
                "/cancel — bekor",
                buttons=[[Button.text("🔙 Admin panel")]]
            )

        elif text == "📊 Holat":
            lines = ["📊 **HOLAT**\n"]
            for i, c in enumerate(account_pool):
                ph = account_phones.get(id(c), "?")
                bs = "🔴 Band" if account_busy.get(id(c)) else "🟢 Bo'sh"
                au = "✅" if await c.is_user_authorized() else "❌"
                lines.append(f"{i+1}. `{ph}` {bs} {au}")
            lines.append(f"\n📋 Navbat: {len(request_queue)} ta")
            await event.respond("\n".join(lines), buttons=[[Button.text("🔙 Admin panel")]])

        elif text == "📋 Navbat":
            if not request_queue:
                await event.respond("📋 Bo'sh!", buttons=[[Button.text("🔙 Admin panel")]]); return
            lines = [f"📋 **{len(request_queue)} ta**\n"]
            for i, req in enumerate(list(request_queue)[:15]):
                secs = estimate_seconds(len(req.questions))
                lines.append(f"{i+1}. `{req.user_id}` {req.fan_name} V{req.variant_num} "
                             f"({len(req.questions)}ta) ~{format_wait(secs)}")
            lines.append(f"\n⏳ Umumiy: ~{calc_wait([])}")
            await event.respond("\n".join(lines), buttons=[[Button.text("🔙 Admin panel")]])

        elif text == "🗑 Navbatni tozalash":
            async with queue_lock:
                n = len(request_queue); request_queue.clear()
            await event.respond(f"🗑 {n} ta bekor qilindi.",
                buttons=[[Button.text("🔙 Admin panel")]])

        elif text == "📢 Barchaga xabar":
            if not adm: return
            admin_states[uid] = {"step": "wait_broadcast"}
            await event.respond(
                "📢 **Barchaga xabar yuborish**\n\n"
                "Yubormoqchi bo'lgan xabarni yozing:\n"
                "_(matn, rasm, video — hammasi bo'ladi)_",
                buttons=[[Button.text("❌ Bekor qilish")]]
            )

        elif text == "📨 ID ga xabar":
            if not adm: return
            admin_states[uid] = {"step": "wait_user_id_msg"}
            await event.respond(
                "📨 **User ID ga xabar yuborish**\n\n"
                "Avval user ID ni yozing:",
                buttons=[[Button.text("❌ Bekor qilish")]]
            )

        elif text == "💳 To'lovlar":
            stats = db_payment_stats()
            # Oxirgi 10 to'lov
            con = get_db()
            last_pays = con.execute(
                """SELECT p.id, p.user_id, p.amount, p.card_num, p.status, p.paid_at
                   FROM payments p ORDER BY p.id DESC LIMIT 10"""
            ).fetchall()
            con.close()

            # Kartalar holati
            card_lines = []
            for card in HUMO_CARDS:
                assigned = card_assignments.get(card)
                if assigned:
                    card_lines.append(f"  🔴 `{card[-9:]}` → user `{assigned}`")
                else:
                    card_lines.append(f"  🟢 `{card[-9:]}` bo'sh")

            pay_lines = []
            for p in last_pays:
                pid, uid2, amt, card, status, paid = p
                icon = {"confirmed": "✅", "pending": "⏳", "expired": "❌"}.get(status, "❓")
                pay_lines.append(
                    f"{icon} #{pid} | `{uid2}` | {amt:,} so'm | ...{card[-9:]}"
                )

            lines = [
                f"💳 **TO'LOV STATISTIKASI**\n",
                f"💵 Jami daromad: **{stats['total']:,} so'm**",
                f"📅 Bugun: **{stats['today']:,} so'm**",
                f"⏳ Kutilayotgan: **{stats['pending']} ta**\n",
                f"**Kartalar:**",
                *card_lines,
                f"\n**Oxirgi to'lovlar:**",
                *(pay_lines if pay_lines else ["  (yo'q)"]),
            ]
            await event.respond(
                "\n".join(lines),
                buttons=[[Button.text("🔙 Admin panel")]]
            )

    # ============================================================
    #  ADMIN: AKKAUNT QO'SHISH
    # ============================================================
    async def _admin_add_phone(event, uid, phone):
        phone = phone.strip()
        if not re.match(r'^\+\d{10,15}$', phone):
            await event.respond("❌ Format: +998901234567"); return
        if phone in [account_phones.get(id(c)) for c in account_pool]:
            await event.respond(f"⚠️ `{phone}` allaqachon bor!")
            admin_states.pop(uid, None); return
        await event.respond(f"📲 `{phone}` ga kod...")
        try:
            sess_dir = _os.path.dirname(DB_FILE)
            session = _os.path.join(sess_dir, f"userbot_{phone.replace('+','').replace(' ','')}")
            client = TelegramClient(session, API_ID, API_HASH)
            await client.connect()
            result = await client.send_code_request(phone)
            admin_states[uid] = {"step": "wait_code", "phone": phone,
                                  "client": client, "hash": result.phone_code_hash}
            await event.respond("✅ Kod yuborildi!\n\nKodni yuboring:\n_(12345)_",
                buttons=[[Button.text("🔙 Admin panel")]])
        except Exception as e:
            await event.respond(f"❌ {e}")
            admin_states.pop(uid, None)

    async def _admin_enter_code(event, uid, code):
        astate = admin_states.get(uid, {})
        client, phone, ph_hash = astate.get("client"), astate.get("phone"), astate.get("hash")
        if not client:
            await event.respond("❌ Sessiya tugadi."); admin_states.pop(uid, None); return
        try:
            await client.sign_in(phone=phone, code=code.strip().replace(" ",""),
                                  phone_code_hash=ph_hash)
            await pool_add(client, phone)
            save_extra_phones([account_phones.get(id(c)) for c in account_pool
                               if account_phones.get(id(c))])
            admin_states.pop(uid, None)
            await event.respond(f"✅ `{phone}` qo'shildi! Jami: **{len(account_pool)} ta**",
                buttons=[[Button.text("🔙 Admin panel")]])
        except SessionPasswordNeededError:
            admin_states[uid]["step"] = "wait_password"
            await event.respond("🔐 Parol kerak:", buttons=[[Button.text("🔙 Admin panel")]])
        except PhoneCodeInvalidError:
            await event.respond("❌ Kod noto'g'ri! Qayta:")
        except Exception as e:
            await event.respond(f"❌ {e}")
            try: await client.disconnect()
            except: pass
            admin_states.pop(uid, None)

    async def _admin_enter_pass(event, uid, password):
        astate = admin_states.get(uid, {})
        client, phone = astate.get("client"), astate.get("phone")
        if not client: admin_states.pop(uid, None); return
        try:
            await client.sign_in(password=password)
            await pool_add(client, phone)
            save_extra_phones([account_phones.get(id(c)) for c in account_pool
                               if account_phones.get(id(c))])
            admin_states.pop(uid, None)
            await event.respond(f"✅ `{phone}` qo'shildi! Jami: **{len(account_pool)} ta**",
                buttons=[[Button.text("🔙 Admin panel")]])
        except Exception as e:
            await event.respond(f"❌ Parol xato: {e}")
            try: await client.disconnect()
            except: pass
            admin_states.pop(uid, None)

    async def _admin_do_remove(event, uid, phone):
        phone = phone.strip()
        if await pool_remove(phone):
            save_extra_phones([account_phones.get(id(c)) for c in account_pool
                               if account_phones.get(id(c))])
            await event.respond(f"✅ `{phone}` o'chirildi! Qoldi: **{len(account_pool)} ta**",
                buttons=[[Button.text("🔙 Admin panel")]])
        else:
            await event.respond(f"❌ `{phone}` topilmadi yoki band!",
                buttons=[[Button.text("🔙 Admin panel")]])
        admin_states.pop(uid, None)

    async def _admin_bonus_user_id(event, uid, text):
        """Admin user ID kiritdi"""
        try:
            target_id = int(text.strip())
        except ValueError:
            await event.respond(
                "❌ Noto'g'ri format! Faqat raqam yozing:\n_(Misol: 7693087447)_",
                buttons=[[Button.text("🔙 Admin panel")]]
            )
            return
        user = db_get_user(target_id)
        if not user:
            await event.respond(
                f"❌ `{target_id}` ID li foydalanuvchi topilmadi!\n\n"
                f"User botga /start bosgan bo'lishi kerak.",
                buttons=[[Button.text("🔙 Admin panel")]]
            )
            return
        first  = user[1] or ""
        last   = user[2] or ""
        uname  = user[3] or ""
        bal    = user[4] or 0
        name   = f"{first} {last}".strip() or uname or str(target_id)
        uname_str = f"@{uname}" if uname else ""
        admin_states[uid] = {
            "step": "wait_bonus_amount",
            "target_id": target_id,
            "target_name": name,
        }
        await event.respond(
            f"👤 **Foydalanuvchi topildi:**\n\n"
            f"Ism: **{name}** {uname_str}\n"
            f"ID: `{target_id}`\n"
            f"💰 Hozirgi balans: **{bal:,} so'm**\n\n"
            f"Qancha so'm yuborasiz?\n_(Manfiy son ham bo'lishi mumkin, masalan: -1000)_",
            buttons=[[Button.text("🔙 Admin panel")]]
        )

    async def _admin_bonus_amount(event, uid, text):
        """Admin miqdor kiritdi"""
        astate = admin_states.get(uid, {})
        target_id   = astate.get("target_id")
        target_name = astate.get("target_name", str(target_id))
        try:
            amount = int(text.strip().replace(" ", "").replace(",", ""))
        except ValueError:
            await event.respond(
                "❌ Noto'g'ri miqdor! Faqat raqam yozing:\n_(Misol: 5000 yoki -1000)_",
                buttons=[[Button.text("🔙 Admin panel")]]
            )
            return
        if amount == 0:
            await event.respond("❌ 0 yuborib bo'lmaydi!", buttons=[[Button.text("🔙 Admin panel")]])
            return
        # Balansi yetarlimi (ayirish uchun)
        if amount < 0:
            bal = db_get_balance(target_id)
            if bal + amount < 0:
                await event.respond(
                    f"❌ Balans yetarli emas!\n"
                    f"Hozirgi balans: **{bal:,} so'm**\n"
                    f"Ayirilmoqchi: **{abs(amount):,} so'm**",
                    buttons=[[Button.text("🔙 Admin panel")]]
                )
                return
        db_add_balance(target_id, amount, f"Admin bonusi — {uid}")
        new_bal = db_get_balance(target_id)
        admin_states.pop(uid, None)
        icon = "💸" if amount > 0 else "➖"
        # Foydalanuvchiga xabar
        try:
            if amount > 0:
                await bot_client.send_message(
                    target_id,
                    f"🎁 **Sizga bonus yuborildi!**\n\n"
                    f"💰 +{amount:,} so'm\n"
                    f"💼 Yangi balans: **{new_bal:,} so'm**"
                )
            else:
                await bot_client.send_message(
                    target_id,
                    f"ℹ️ **Balans o'zgartirildi**\n\n"
                    f"💰 {amount:,} so'm\n"
                    f"💼 Yangi balans: **{new_bal:,} so'm**"
                )
        except Exception as e:
            log.warning(f"Foydalanuvchiga xabar yuborilmadi: {e}")
        await event.respond(
            f"{icon} **Muvaffaqiyatli!**\n\n"
            f"👤 {target_name} (`{target_id}`)\n"
            f"💰 {'+' if amount > 0 else ''}{amount:,} so'm\n"
            f"💼 Yangi balans: **{new_bal:,} so'm**",
            buttons=[[Button.text("💸 Yana yuborish"), Button.text("🔙 Admin panel")]]
        )
        log.info(f"Admin bonus: {uid} → {target_id}, {amount} so'm")

    # ============================================================
    #  PARSER (ichki)
    # ============================================================
    def _parse_questions(text: str) -> list:
        text = text.strip()

        # Ajratgich satrlarni aniqlash
        def is_separator(s: str) -> bool:
            """===== yoki +++++ yoki --- kabi satrlar"""
            return bool(re.match(r'^[=+\-_*]{3,}$', s.strip()))

        if "=====" in text and "+++++" in text:
            qs = []
            for block in re.split(r'\+{3,}', text):
                block = block.strip()
                if not block: continue
                parts = re.split(r'={3,}', block, maxsplit=1)
                if len(parts) < 2: continue
                q_text = parts[0].strip()
                # Ajratgich satrlarni o'tkazib yuborish
                opts_raw = [
                    l.strip() for l in parts[1].strip().splitlines()
                    if l.strip() and not is_separator(l.strip())
                ]
                if not q_text or not opts_raw: continue
                options, correct, idx = [], 0, 0
                for opt in opts_raw:
                    if opt.startswith("#"):
                        correct = idx; options.append(opt[1:].strip())
                    else:
                        options.append(opt)
                    idx += 1
                if len(options) >= 2:
                    qs.append({"q": q_text, "opts": options, "ans": correct})
            return qs

        qs = []
        lines = [l.rstrip() for l in text.splitlines()]
        i = 0
        while i < len(lines):
            line = lines[i].strip()
            if not line:
                i += 1; continue

            # Ajratgich satrni o'tkazib yuborish
            if is_separator(line):
                i += 1; continue

            is_q = bool(re.match(r'^\d+[\.\)]\s*.+', line)) or \
                   not re.match(r'^[a-zA-Z#][\.\)]\s*', line)
            if is_q:
                q_text = re.sub(r'^\d+[\.\)]\s*', '', line).strip()
                if not q_text: i += 1; continue
                options, correct, opt_idx = [], 0, 0
                i += 1
                while i < len(lines):
                    vline = lines[i].strip()
                    if not vline:
                        i += 1; break

                    # Variant ichidagi ajratgich satrni o'tkazib yuborish
                    if is_separator(vline):
                        i += 1; continue

                    if re.match(r'^\d+[\.\)]\s*.+', vline) and options:
                        break
                    m = re.match(r'^(#?)([a-zA-Z]?)[\.\)]\s*(.*)', vline)
                    if m:
                        is_correct = bool(m.group(1))
                        opt_text = m.group(3).strip()
                        if opt_text and not is_separator(opt_text):
                            if is_correct: correct = opt_idx
                            options.append(opt_text); opt_idx += 1
                        i += 1
                    elif vline.startswith("#"):
                        clean = re.sub(r'^#[a-dA-D]?[\.\)]\s*', '', vline[1:]).strip() or vline[1:].strip()
                        if clean and not is_separator(clean):
                            correct = opt_idx; options.append(clean); opt_idx += 1
                        i += 1
                    else:
                        i += 1
                if q_text and len(options) >= 2:
                    qs.append({"q": q_text, "opts": options, "ans": correct})
            else:
                i += 1
        return qs

    # ============================================================
    #  TO'LOV HANDLERLARI
    # ============================================================

    @bot_client.on(events.NewMessage(pattern="💳 To'lov qilish"))
    async def cmd_pay(event):
        uid = event.sender_id
        bal = db_get_balance(uid)
        await event.respond(
            f"💳 **To'lov**\n\n"
            f"💰 Hozirgi balans: **{bal:,} so'm**\n"
            f"🤖 1 ta AI test narxi: **{AI_PRICE:,} so'm**\n\n"
            f"Karta orqali to'lash uchun tugmani bosing:",
            buttons=[
                [Button.text(f"💳 {AI_PRICE:,} so'm to'lash")],
                [Button.text("💰 Balansni ko'rish")],
                [Button.text("🔙 Bosh menyu")],
            ]
        )

    @bot_client.on(events.NewMessage(func=lambda e: not e.file
                                     and e.text.strip() == "💰 Balansni ko'rish"))
    async def cmd_balance(event):
        uid = event.sender_id
        bal = db_get_balance(uid)
        tests = bal // AI_PRICE
        await event.respond(
            f"💰 **Balans: {bal:,} so'm**\n"
            f"🤖 Yozib olish mumkin: **{tests} ta** AI test\n\n"
            f"{'✅ Test tuzish mumkin!' if bal >= AI_PRICE else '❌ Balans yetarli emas. To\'lash kerak.'}",
            buttons=[
                [Button.text(f"💳 {AI_PRICE:,} so'm to'lash")],
                [Button.text("🔙 Bosh menyu")],
            ]
        )

    @bot_client.on(events.NewMessage(
        func=lambda e: not e.file and
        bool(re.match(r'^💳 [\d\s,.]+ so\'m to\'lash$', e.text.strip()))
    ))
    async def pay_request(event):
        uid  = event.sender_id
        text = event.text.strip()

        # Summani xabar matnidan ajratib olamiz
        m = re.search(r'([\d\s,.]+)\s*so\'m', text)
        try:
            pay_amount = int(m.group(1).replace(' ','').replace(',','').replace('.','')) if m else AI_PRICE
        except Exception:
            pay_amount = AI_PRICE

        # Avvalgi kutilayotgan to'lov bormi?
        pending = db_get_pending(uid)
        if pending:
            pay_id, card_num, amount, expires = pending
            await event.respond(
                f"⏳ **Kutilayotgan to'lov mavjud**\n\n"
                f"💳 Karta: `{card_num}`\n"
                f"💰 Summa: **{amount:,} so'm**\n"
                f"⏰ Muddat: {expires[11:16]}\n\n"
                f"Shu kartaga {amount:,} so'm o'tkazing!",
                buttons=[[Button.text("🔙 Bosh menyu")]]
            )
            return

        # Bo'sh karta olish
        card = get_free_card(uid)
        if not card:
            await event.respond(
                "⚠️ Hozir barcha kartalar band!\n"
                "Bir daqiqadan so'ng qayta urining.",
                buttons=[[Button.text("🔙 Bosh menyu")]]
            )
            return

        # To'lov yaratish
        pay_id = db_create_payment(uid, card, pay_amount)

        await event.respond(
            f"💳 **To'lov ma'lumotlari**\n\n"
            f"🏦 Bank: **Humo**\n"
            f"💳 Karta: `{card}`\n"
            f"💰 Summa: **{pay_amount:,} so'm**\n"
            f"⏰ Muddat: **3 daqiqa**\n\n"
            f"⚡ Pul o'tkazganingizdan so'ng\n"
            f"**avtomatik** tasdiqlanadi!\n\n"
            f"❗ Faqat shu kartaga va aynan\n"
            f"**{pay_amount:,} so'm** o'tkazing!",
            buttons=[[Button.text("🔙 Bosh menyu")]]
        )
        log.info(f"To'lov yaratildi: user={uid}, karta={card}, summa={pay_amount}, id={pay_id}")

    # ============================================================
    #  @HUMOCARDBOT XABAR TINGLOVCHI
    # ============================================================
    # notify_client global — /notify_ulash orqali o'rnatilishi mumkin
    notify_client_holder = {"client": None}

    def setup_notify_listener(client):
        """Notify client ga @humocardbot handler o'rnatish"""
        @client.on(events.NewMessage(from_users="humocardbot"))
        async def on_humo_notify(event):
            text = event.text or ""
            log.info(f"humocardbot xabari: {text[:150]}")
            amount = _parse_amount(text)
            card   = _parse_card(text)
            log.info(f"Parse natijasi: summa={amount}, karta={card}")
            if not amount:
                log.warning(f"Summa aniqlanmadi: {text[:80]}")
                return
            if not card:
                log.warning(f"Karta aniqlanmadi: {text[:80]}")
                return
            log.info(f"To'lov aniqlandi: karta={card}, summa={amount}")
            con = get_db()
            row = con.execute(
                """SELECT id, user_id, amount FROM payments
                   WHERE card_num=? AND status='pending'
                   AND expires_at > datetime('now')
                   ORDER BY id DESC LIMIT 1""",
                (card,)
            ).fetchone()
            con.close()
            if not row:
                log.warning(f"Mos to'lov topilmadi: karta={card}")
                return
            pay_id, user_id, expected_amount = row
            if amount < expected_amount:
                await bot_client.send_message(
                    user_id,
                    f"⚠️ **Noto'g'ri summa!**\n\n"
                    f"Kerak: **{expected_amount:,} so'm**\n"
                    f"Tushgan: **{amount:,} so'm**\n\n"
                    f"Farq: {expected_amount - amount:,} so'm qo'shimcha yuboring!"
                )
                return
            db_confirm_payment(pay_id)
            db_add_balance(user_id, amount, f"To'lov #{pay_id} tasdiqlandi")
            release_card(card)
            bal = db_get_balance(user_id)
            tests = bal // AI_PRICE
            prev_state = user_states.get(user_id)
            has_pending_ai   = prev_state and prev_state.step == "wait_payment" and prev_state.fan_name
            has_pending_file = prev_state and prev_state.step == "wait_payment_file" and prev_state.questions
            if has_pending_file:
                q_count = prev_state.total_questions
                price   = calc_file_price(q_count)
                if bal >= price:
                    db_deduct_balance(user_id, price, f"Fayl quiz: {q_count} ta savol")
                    bal_left = db_get_balance(user_id)
                    prev_state.step = "ask_fan_name"
                    user_states[user_id] = prev_state
                    await bot_client.send_message(
                        user_id,
                        f"✅ **To'lov tasdiqlandi! +{amount:,} so'm**\n\n"
                        f"📂 {q_count} ta savol tayyor\n"
                        f"💰 -{price:,} so'm | Balans: {bal_left:,} so'm\n\n"
                        f"Fan nomini yozing:",
                        buttons=[[Button.text("🔙 Bosh menyu")]]
                    )
                else:
                    await bot_client.send_message(
                        user_id,
                        f"✅ +{amount:,} so'm | Balans: {bal:,} so'm\n"
                        f"⚠️ Hali yetarli emas. Kerak: {price:,} so'm",
                        buttons=[[Button.text(f"💳 {price-bal:,} so'm to'lash"),
                                  Button.text("🔙 Bosh menyu")]]
                    )
            elif has_pending_ai:
                await bot_client.send_message(
                    user_id,
                    f"✅ **To'lov tasdiqlandi! +{amount:,} so'm**\n\n"
                    f"💼 Balans: **{bal:,} so'm**\n\n"
                    f"🤖 Oldingi sozlamalar:\n"
                    f"📚 {prev_state.fan_name}"
                    f"{f' | 📌 {prev_state.topic}' if prev_state.topic else ''}\n"
                    f"🔢 {prev_state.q_count} ta | 🎯 {prev_state.difficulty}\n\n"
                    f"⏳ AI test tuzilmoqda..."
                )
                try:
                    qs = await generate_questions(
                        prev_state.fan_name, prev_state.q_count,
                        prev_state.lang, prev_state.difficulty, prev_state.topic
                    )
                    if not qs:
                        await bot_client.send_message(user_id, "❌ AI savol yarata olmadi!")
                        return
                    db_deduct_balance(user_id, AI_PRICE, f"AI test: {prev_state.fan_name}")
                    bal_left = db_get_balance(user_id)
                    prev_state.questions = qs
                    prev_state.total_questions = len(qs)
                    prev_state.per_variant = len(qs)
                    prev_state.step = "ask_time"
                    user_states[user_id] = prev_state
                    await bot_client.send_message(
                        user_id,
                        f"✅ **{len(qs)} ta savol tayyor!**\n"
                        f"💰 Balans: {bal_left:,} so'm\n\n⏱ Vaqt:",
                        buttons=[[Button.text("⏱ 15s"), Button.text("⏱ 30s")],
                                 [Button.text("⏱ 60s"), Button.text("⏱ Chegarasiz")]]
                    )
                except Exception as e:
                    log.error(f"AI xato (to'lovdan keyin): {e}")
                    await bot_client.send_message(user_id, f"❌ AI xato: {e}")
            else:
                await bot_client.send_message(
                    user_id,
                    f"✅ **To'lov tasdiqlandi!**\n\n"
                    f"💰 +{amount:,} so'm\n"
                    f"💼 Balans: **{bal:,} so'm**\n"
                    f"🤖 {tests} ta AI test mumkin 🎉",
                    buttons=[[Button.text("🤖 AI test tuzish", resize=True),
                              Button.text("🔙 Bosh menyu", resize=True)]]
                )
            log.info(f"✅ To'lov tasdiqlandi: user={user_id}, +{amount} so'm")
        log.info(f"✅ @humocardbot tinglash aktiv: {client}")

    # Mavjud ulangan notify clientni sozlash
    for c in all_clients:
        if account_phones.get(id(c)) == NOTIFY_PHONE:
            notify_client_holder["client"] = c
            setup_notify_listener(c)
            log.info(f"✅ Notify aktiv: {NOTIFY_PHONE}")
            break
    else:
        log.warning(f"⚠️ Notify akkaunt topilmadi: {NOTIFY_PHONE} — /notify_ulash buyrug'ini ishlating")

    # ============================================================
    #  /session_yuklash — sessiya faylini bot orqali yuklash
    # ============================================================
    @bot_client.on(events.NewMessage(pattern="/session_yuklash"))
    async def cmd_session_upload(event):
        if not is_admin(event.sender_id): return
        admin_states[event.sender_id] = {"step": "wait_session_file"}
        await event.respond(
            "📤 **Sessiya fayli yuklash**\n\n"
            "`.session` faylini yuboring\n"
            "_(Misol: userbot_998934897111.session)_\n\n"
            "Fayl DB ga saqlanadi va bot uni ishlatadi.\n\n"
            "/cancel — bekor"
        )

    @bot_client.on(events.NewMessage(
        func=lambda e: e.file and
        admin_states.get(e.sender_id, {}).get("step") == "wait_session_file"
        and e.sender_id in ADMIN_IDS
    ))
    async def cmd_session_receive(event):
        uid = event.sender_id
        if not is_admin(uid): return

        fname = getattr(event.file, 'name', '') or ''
        if not fname.lower().endswith('.session'):
            await event.respond("❌ Faqat `.session` fayl yuboring!")
            return

        try:
            import io, base64
            buf = io.BytesIO()
            await event.download_media(file=buf)
            buf.seek(0)
            data = buf.read()

            if len(data) < 10:
                await event.respond("❌ Fayl bo'sh!")
                return

            # Telefon raqamini fayl nomidan ajratish
            # userbot_998934897111.session → +998934897111
            name = fname.replace('.session', '')
            digits = name.replace('userbot_', '').strip()
            if digits.startswith('998') and len(digits) >= 12:
                phone = '+' + digits
            elif digits.startswith('+'):
                phone = digits
            else:
                phone = '+' + digits

            # Sessiya faylini diskka yozish
            sess_dir = _os.path.dirname(DB_FILE)
            if sess_dir:
                _os.makedirs(sess_dir, exist_ok=True)
            session_path = _os.path.join(sess_dir, f"userbot_{phone.replace('+','').replace(' ','')}")
            with open(session_path + ".session", "wb") as f:
                f.write(data)

            # DB ga ham saqlash
            encoded = base64.b64encode(data).decode()
            con = get_db()
            con.execute("""
                INSERT INTO sessions (phone, session_data, updated_at)
                VALUES (?, ?, datetime('now'))
                ON CONFLICT(phone) DO UPDATE SET
                    session_data = excluded.session_data,
                    updated_at   = excluded.updated_at
            """, (phone, encoded))
            con.commit()
            con.close()

            admin_states.pop(uid, None)

            # Sessiya faylini yuklab, darhol ulanib ko'ramiz
            is_notify = (phone == NOTIFY_PHONE)
            try:
                client = TelegramClient(session_path, API_ID, API_HASH)
                await client.connect()
                if await client.is_user_authorized():
                    me = await client.get_me()
                    all_clients.append(client)
                    account_phones[id(client)] = phone
                    if is_notify:
                        notify_client_holder["client"] = client
                        setup_notify_listener(client)
                        await event.respond(
                            f"✅ **Notify akkaunt ulandi!**\n\n"
                            f"📱 `{phone}` (@{me.username or me.first_name})\n"
                            f"🔔 @humocardbot endi tinglanadi!",
                            buttons=[[Button.text("🔙 Admin panel")]]
                        )
                    else:
                        # Quiz pool ga qo'shamiz
                        already = any(account_phones.get(id(c)) == phone for c in account_pool)
                        if not already:
                            account_pool.append(client)
                            account_busy[id(client)] = False
                        await event.respond(
                            f"✅ **Akkaunt ulandi!**\n\n"
                            f"📱 `{phone}` (@{me.username or me.first_name})\n"
                            f"🎯 Quiz pool ga qo'shildi!\n"
                            f"Jami akkaunt: {len(account_pool)} ta",
                            buttons=[[Button.text("🔙 Admin panel")]]
                        )
                    log.info(f"Sessiya yuklandi va ulandi: {phone}")
                else:
                    await client.disconnect()
                    await event.respond(
                        f"💾 Sessiya saqlandi, lekin avtorizatsiya eski.\n"
                        f"📱 `{phone}`\n\n"
                        f"{'Endi /notify_ulash ni bosing' if is_notify else '➕ Akkaunt qo\'shish orqali qayta ulang'}",
                        buttons=[[Button.text("🔙 Admin panel")]]
                    )
            except Exception as conn_err:
                log.error(f"Sessiya ulanish xato: {conn_err}")
                await event.respond(
                    f"💾 Sessiya saqlandi!\n📱 `{phone}`\n\n"
                    f"Ulanishda xato: {conn_err}\n"
                    f"{'Qayta /notify_ulash bosing' if is_notify else 'Admin panel → ➕ Akkaunt qo\'shish'}",
                    buttons=[[Button.text("🔙 Admin panel")]]
                )
            log.info(f"Sessiya yuklandi: {phone}, {len(data)} bayt")

        except Exception as e:
            log.error(f"session_receive xato: {e}")
            admin_states.pop(uid, None)
            await event.respond(f"❌ Xato: {e}")

    # ============================================================
    #  /notify_ulash — bot orqali notify akkauntni ulash
    # ============================================================
    @bot_client.on(events.NewMessage(pattern="/notify_ulash"))
    async def cmd_notify_connect(event):
        if not is_admin(event.sender_id): return

        if notify_client_holder["client"]:
            phone = account_phones.get(id(notify_client_holder["client"]), "?")
            await event.respond(f"✅ Notify akkaunt allaqachon ulangan: `{phone}`")
            return
        if not NOTIFY_PHONE:
            await event.respond("❌ NOTIFY_PHONE environment variable o'rnatilmagan!")
            return

        # Agar avvalgi urinish hali aktiv bo'lsa — faqat kod so'raymiz
        existing = admin_states.get(event.sender_id, {})
        if existing.get("step") == "wait_notify_code" and existing.get("client"):
            await event.respond(
                f"⏳ Oldingi kod hali aktiv!\n\n"
                f"`{NOTIFY_PHONE}` ga kelgan kodni yuboring:\n"
                f"_(yoki /notify_yangi_kod — yangi kod olish)_"
            )
            return

        await event.respond(f"📲 `{NOTIFY_PHONE}` ga kod yuborilmoqda...")

        try:
            sess_dir = _os.path.dirname(DB_FILE)
            session  = _os.path.join(sess_dir, f"userbot_{NOTIFY_PHONE.replace('+','').replace(' ','')}")

            # Eski sessiya bo'lsa — avval undan urinib ko'ramiz
            if db_load_session(NOTIFY_PHONE, session):
                client = TelegramClient(session, API_ID, API_HASH)
                await client.connect()
                if await client.is_user_authorized():
                    all_clients.append(client)
                    account_phones[id(client)] = NOTIFY_PHONE
                    notify_client_holder["client"] = client
                    setup_notify_listener(client)
                    await event.respond(
                        f"✅ **Sessiya tiklandi! Kod shart emas.**\n\n"
                        f"📱 `{NOTIFY_PHONE}`\n"
                        f"🔔 @humocardbot xabarlari qabul qilinadi!"
                    )
                    return
                await client.disconnect()

            client = TelegramClient(session, API_ID, API_HASH)
            await client.connect()
            result = await client.send_code_request(NOTIFY_PHONE)

            admin_states[event.sender_id] = {
                "step":   "wait_notify_code",
                "phone":  NOTIFY_PHONE,
                "client": client,
                "hash":   result.phone_code_hash,
            }
            await event.respond(
                f"✅ **Kod yuborildi!**\n\n"
                f"📱 `{NOTIFY_PHONE}` ga kelgan kodni yuboring\n"
                f"_(bo'shliqsiz: 12345)_\n\n"
                f"⚠️ Kodni **2 daqiqa ichida** yuboring!\n"
                f"/cancel — bekor qilish"
            )
        except Exception as e:
            await event.respond(f"❌ Xato: {e}\n\nQayta urinish: /notify_ulash")
            admin_states.pop(event.sender_id, None)

    @bot_client.on(events.NewMessage(
        func=lambda e: not e.file and not e.text.startswith("/")
        and admin_states.get(e.sender_id, {}).get("step") == "wait_notify_code"
        and e.sender_id in ADMIN_IDS
    ))
    async def on_notify_code(event):
        uid    = event.sender_id
        code   = event.text.strip().replace(" ", "")
        astate = admin_states.get(uid, {})
        client  = astate.get("client")
        phone   = astate.get("phone")
        ph_hash = astate.get("hash")
        if not client:
            admin_states.pop(uid, None); return
        try:
            await client.sign_in(phone=phone, code=code, phone_code_hash=ph_hash)
            all_clients.append(client)
            account_phones[id(client)] = phone
            notify_client_holder["client"] = client
            setup_notify_listener(client)
            sess_dir = _os.path.dirname(DB_FILE)
            session  = _os.path.join(sess_dir, f"userbot_{phone.replace('+','').replace(' ','')}")
            db_save_session(phone, session)
            admin_states.pop(uid, None)
            await event.respond(
                f"✅ **Notify akkaunt ulandi!**\n\n"
                f"📱 `{phone}`\n"
                f"🔔 @humocardbot xabarlari endi qabul qilinadi!"
            )
            log.info(f"Notify akkaunt ulandi: {phone}")
        except SessionPasswordNeededError:
            admin_states[uid]["step"] = "wait_notify_pass"
            await event.respond("🔐 2FA parol kerak. Parolni yuboring:")
        except PhoneCodeInvalidError:
            await event.respond("❌ Kod noto'g'ri! Qayta yuboring:")
        except Exception as e:
            err = str(e).lower()
            # Kod eskirgan bo'lsa — avtomatik yangi kod yuboramiz
            if any(x in err for x in ["expired", "signinrequest", "phone_code_expired", "code_expired"]):
                try:
                    result = await client.send_code_request(phone)
                    admin_states[uid]["hash"] = result.phone_code_hash
                    await event.respond(
                        f"⚠️ **Kod eskirdi — yangi kod yuborildi!**\n\n"
                        f"`{phone}` ga kelgan yangi kodni yuboring:"
                    )
                except Exception as e2:
                    await event.respond(f"❌ Yangi kod yuborishda xato: {e2}\n\nQayta: /notify_ulash")
                    try: await client.disconnect()
                    except: pass
                    admin_states.pop(uid, None)
            else:
                await event.respond(f"❌ Xato: {e}\n\nQayta urinish: /notify_ulash")
                try: await client.disconnect()
                except: pass
                admin_states.pop(uid, None)

    @bot_client.on(events.NewMessage(
        func=lambda e: not e.file and not e.text.startswith("/")
        and admin_states.get(e.sender_id, {}).get("step") == "wait_notify_pass"
        and e.sender_id in ADMIN_IDS
    ))
    async def on_notify_pass(event):
        uid    = event.sender_id
        astate = admin_states.get(uid, {})
        client = astate.get("client")
        phone  = astate.get("phone")
        if not client:
            admin_states.pop(uid, None); return
        try:
            await client.sign_in(password=event.text.strip())
            all_clients.append(client)
            account_phones[id(client)] = phone
            notify_client_holder["client"] = client
            setup_notify_listener(client)
            # Sessiyani DB ga saqlash
            sess_dir = _os.path.dirname(DB_FILE)
            session  = _os.path.join(sess_dir, f"userbot_{phone.replace('+','').replace(' ','')}")
            db_save_session(phone, session)
            admin_states.pop(uid, None)
            await event.respond(
                f"✅ **Notify akkaunt ulandi!**\n📱 `{phone}`\n"
                f"🔔 @humocardbot endi tinglanadi!"
            )
        except Exception as e:
            await event.respond(f"❌ Parol xato: {e}")
            try: await client.disconnect()
            except: pass
            admin_states.pop(uid, None)

            amount = _parse_amount(text)
            card   = _parse_card(text)

            log.info(f"Parse natijasi: summa={amount}, karta={card}")

            if not amount:
                log.warning(f"Summa aniqlanmadi: {text[:80]}")
                return
            if not card:
                log.warning(f"Karta aniqlanmadi: {text[:80]}")
                return

    # ============================================================
    #  YORDAMCHI: XABARDAN SUMMA VA KARTA AJRATIB OLISH
    # ============================================================
    def _parse_amount(text: str) -> Optional[int]:
        """
        Xabardan summani topish.
        Humo formatlari:
          ➕ 2.000,00 UZS
          ➕ **2.000,00 UZS**
          +2000 UZS
          2 000,00 UZS
        """
        # Markdown bold va belgilarni tozalash
        clean = text.replace('*', '').replace('_', '').replace('`', '')

        patterns = [
            # "2.000,00 UZS" — nuqta minglik, vergul kasr (Humo asosiy format)
            r'[➕\+]?\s*([\d]{1,3}(?:\.[\d]{3})*),\d{2}\s*UZS',
            # "2 000,00 UZS"
            r'[➕\+]?\s*([\d]{1,3}(?:\s[\d]{3})*),\d{2}\s*UZS',
            # "2000 UZS" yoki "2000,00 UZS"
            r'[➕\+]?\s*(\d+)(?:,\d+)?\s*UZS',
            # Umumiy fallback
            r'(\d[\d\.\s]+\d)\s*UZS',
        ]

        for pat in patterns:
            m = re.search(pat, clean, re.IGNORECASE)
            if m:
                raw = m.group(1)
                # Nuqta va bo'shliqlarni olib tashlaymiz (minglik ajratgich)
                raw = raw.replace('.', '').replace(' ', '').replace('\xa0', '')
                try:
                    return int(raw)
                except Exception:
                    continue
        return None

    def _parse_card(text: str) -> Optional[str]:
        """
        Xabardan karta oxirgi 4 raqamini topib, DB dan to'liq raqamni olish.
        Humo format: HUMOCARD *8906
        """
        # Oxirgi 4 raqamni topish: "*8906", "* 8906", "**8906"
        m = re.search(r'\*+\s*(\d{4})\b', text)
        if not m:
            return None
        last4 = m.group(1)

        # DB dagi kartalar ichidan oxirgi 4 raqami mos keladiganni topish
        for card in HUMO_CARDS:
            if card.replace(' ', '').endswith(last4):
                return card
        return None

    # ============================================================
    #  MUDDATI O'TGAN TO'LOVLARNI BEKOR QILISH (fon task)
    # ============================================================
    async def expire_checker():
        while True:
            await asyncio.sleep(60)
            expired = db_expire_old()
            if expired:
                log.info(f"⏰ {expired} ta to'lov muddati o'tdi, bekor qilindi")

    asyncio.create_task(expire_checker())
    asyncio.create_task(queue_worker())
    log.info(f"✅ AI Quiz Bot tayyor! {len(account_pool)} ta akkaunt.")
    await bot_client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())


                     
