import os
import logging
from flask import Flask, request
import requests
import telebot
from telebot import types

# Логи TeleBot в DEBUG
telebot.logger.setLevel(logging.DEBUG)

BOT_TOKEN = os.environ["BOT_TOKEN"]
PUBLIC_URL = os.environ.get("PUBLIC_URL", "").rstrip("/")

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")
app = Flask(__name__)

# -------- Bot handlers --------
@bot.message_handler(commands=["start", "help"])
def on_start(message):
    bot.reply_to(message, "Привет! Я живой 🤖. Напиши мне что-нибудь — я повторю.")

@bot.message_handler(func=lambda m: True)
def echo_all(message):
    bot.send_message(message.chat.id, f"Ты написал: {message.text}")

# -------- Healthcheck --------
@app.route("/", methods=["GET"], strict_slashes=False)
def root():
    return "OK", 200

# --- Webhook основной (и /webhook и /webhook/ принимаем)
@app.route("/webhook", methods=["GET", "POST"], strict_slashes=False)
def webhook():
    if request.method == "GET":
        return "Webhook here", 200

    ct = request.headers.get("content-type", "")
    if "application/json" not in ct.lower():
        print("Unexpected Content-Type:", ct)
        return "OK", 200

    try:
        json_str = request.get_data(as_text=True)
        print("Incoming update:", json_str[:1000])
        update = types.Update.de_json(json_str)
        bot.process_new_updates([update])
        return "OK", 200
    except Exception as e:
        print("webhook error:", e)
        return "OK", 200

# --- Алиасы под редирект Replit: /@<owner>/<project>/webhook и корень проекта
@app.route("/@<owner>/<project>/webhook", methods=["GET", "POST"], strict_slashes=False)
def webhook_alias(owner, project):
    return webhook()

@app.route("/@<owner>/<project>/", methods=["GET"], strict_slashes=False)
def project_root(owner, project):
    return root()

def ensure_webhook():
    if not PUBLIC_URL:
        print("PUBLIC_URL пустой — пропускаю setWebhook")
        return
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook"
        data = {"url": f"{PUBLIC_URL}/webhook"}
        r = requests.post(url, data=data, timeout=10)
        print("setWebhook:", r.json())
    except Exception as e:
        print("setWebhook error:", e)

if __name__ == "__main__":
    ensure_webhook()
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)



# ============ ENV ============
BOT_TOKEN      = os.getenv("BOT_TOKEN")
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "").strip()  # пример: "@yourusername"
_admin_raw     = os.getenv("ADMIN_GROUP_ID", "").strip()

try:
    ADMIN_GROUP_ID_INT = int(_admin_raw) if _admin_raw else None
except ValueError:
    ADMIN_GROUP_ID_INT = None

if not BOT_TOKEN:
    raise RuntimeError("Нет BOT_TOKEN в Secrets.")

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")

# ============ Платёжный шаблон ============
PAYMENT_INSTRUCTIONS = (
    "<b>Оплата услуги TripBuddy</b>\n"
    "Сумма к оплате: <b>{total} {currency}</b>\n"
    "Состав: базовая стоимость {base} + сервисный сбор {fee}\n"
    "Назначение: Перевод средств по договору, НДС не облагается\n\n"
    "<b>РФ (Т-Банк)</b>\n"
    "Получатель: Эльдяева Юлия Николаевна\n"
    "Счёт: 40817810600139601690\n"
    "БИК: 044525974\n"
    "ИНН: 7710140679   КПП: 771301001\n\n"
    "<i>После оплаты нажмите в боте «✅ Я оплатил(а)» и пришлите квитанцию.</i>"
)

# ============ SQLite ============
DB_PATH = "tripbuddy.db"
conn = sqlite3.connect(DB_PATH, check_same_thread=False)
conn.row_factory = sqlite3.Row

def _exec(sql, params=None):
    cur = conn.cursor()
    cur.execute(sql, params or [])
    conn.commit()
    return cur

def table_info(name: str):
    cur = _exec(f"PRAGMA table_info({name})")
    return [dict(row) for row in cur.fetchall()]

def has_table(name: str):
    cur = _exec(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (name,)
    )
    return cur.fetchone() is not None

def ensure_tables():
    # requests
    _exec("""
    CREATE TABLE IF NOT EXISTS requests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id INTEGER,
        type TEXT,
        route TEXT,
        dates TEXT,
        guests TEXT,
        rooms TEXT,
        stars TEXT,
        breakfast TEXT,
        location_pref TEXT,
        budget TEXT,
        class TEXT,
        baggage TEXT,
        carriers TEXT,
        fullname TEXT,
        dob TEXT,
        gender TEXT,
        citizenship TEXT,
        passport_no TEXT,
        passport_exp TEXT,
        contact TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """)
    # request_attachments
    _exec("""
    CREATE TABLE IF NOT EXISTS request_attachments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        request_id INTEGER,
        kind TEXT,          -- 'photo' | 'doc'
        file_id TEXT
    )
    """)
    # payments
    _exec("""
    CREATE TABLE IF NOT EXISTS payments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id INTEGER,
        amount TEXT,
        currency TEXT,
        pay_method TEXT,
        pay_date TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """)
    # payment_files
    _exec("""
    CREATE TABLE IF NOT EXISTS payment_files (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        payment_id INTEGER,
        kind TEXT,          -- 'photo' | 'doc'
        file_id TEXT
    )
    """)

def add_missing_columns():
    # requests — проверим обязательные поля (если вдруг таблица старая)
    must_requests = {
        "chat_id":"INTEGER", "type":"TEXT", "route":"TEXT", "dates":"TEXT",
        "guests":"TEXT", "rooms":"TEXT", "stars":"TEXT", "breakfast":"TEXT",
        "location_pref":"TEXT", "budget":"TEXT", "class":"TEXT", "baggage":"TEXT",
        "carriers":"TEXT", "fullname":"TEXT", "dob":"TEXT", "gender":"TEXT",
        "citizenship":"TEXT", "passport_no":"TEXT", "passport_exp":"TEXT",
        "contact":"TEXT", "created_at":"TEXT"
    }
    if has_table("requests"):
        cols = {c["name"] for c in table_info("requests")}
        for name, typ in must_requests.items():
            if name not in cols:
                _exec(f"ALTER TABLE requests ADD COLUMN {name} {typ}")

    # payments — добавим отсутствующие поля (вот тут обычно проблема)
    must_payments = {
        "chat_id":"INTEGER", "amount":"TEXT", "currency":"TEXT",
        "pay_method":"TEXT", "pay_date":"TEXT", "created_at":"TEXT"
    }
    if has_table("payments"):
        cols = {c["name"] for c in table_info("payments")}
        for name, typ in must_payments.items():
            if name not in cols:
                _exec(f"ALTER TABLE payments ADD COLUMN {name} {typ}")

    # request_attachments
    must_req_att = {"request_id":"INTEGER", "kind":"TEXT", "file_id":"TEXT"}
    if has_table("request_attachments"):
        cols = {c["name"] for c in table_info("request_attachments")}
        for name, typ in must_req_att.items():
            if name not in cols:
                _exec(f"ALTER TABLE request_attachments ADD COLUMN {name} {typ}")

    # payment_files
    must_pay_files = {"payment_id":"INTEGER", "kind":"TEXT", "file_id":"TEXT"}
    if has_table("payment_files"):
        cols = {c["name"] for c in table_info("payment_files")}
        for name, typ in must_pay_files.items():
            if name not in cols:
                _exec(f"ALTER TABLE payment_files ADD COLUMN {name} {typ}")

def init_db():
    ensure_tables()
    add_missing_columns()

init_db()

# ============ FSM (в памяти) ============
user_step = {}   # chat_id -> step
user_data = {}   # chat_id -> dict

def reset_flow(cid):
    user_step.pop(cid, None)
    user_data.pop(cid, None)

# ============ Утилиты ============


def parse_amount_currency(raw: str):
    # "60000 RUB" | "70,5 USD" | "1000" -> ('60000','RUB') etc.
    if not raw:
        return ("", "")
    m = re.search(r"([\d\s.,]+)\s*([A-Za-zА-Яа-я]{3})?", raw.strip())
    if not m:
        return (raw.strip(), "")
    amount = m.group(1).replace(" ", "")
    cur = (m.group(2) or "").upper()
    if cur == "РУБ": cur = "RUB"
    return (amount, cur)

def to_num(amount_str: str) -> float:
    try:
        return float(amount_str.replace(",", "."))
    except Exception:
        return 0.0

# ============ Клавиатуры ============
def main_menu():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("📝 Оставить заявку", "✅ Я оплатил(а)")
    kb.add("📄 Оферта", "💬 Администратор")
    return kb

def type_menu():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    kb.add("🏨 Отель", "✈️ Билеты")
    kb.add("❌ Отмена")
    return kb

def cancel_menu():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    kb.add("❌ Отмена")
    return kb

def yes_no_menu():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    kb.add("Да ✅", "Нет ❌")
    kb.add("❌ Отмена")
    return kb

def class_menu():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    kb.add("Эконом", "Премиум-эконом")
    kb.add("Бизнес", "Первый")
    kb.add("❌ Отмена")
    return kb

def rooms_menu():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    kb.add("1 комната", "2 комнаты", "3+ комнат")
    kb.add("❌ Отмена")
    return kb

def stars_menu():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    kb.add("⭐️", "⭐️⭐️", "⭐️⭐️⭐️", "⭐️⭐️⭐️⭐️", "⭐️⭐️⭐️⭐️⭐️")
    kb.add("❌ Отмена")
    return kb

def gender_menu():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    kb.add("М", "Ж")
    kb.add("❌ Отмена")
    return kb

def attachments_menu():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    kb.add("Пропустить ⏭️", "Готово ✅")
    kb.add("❌ Отмена")
    return kb

def pay_finish_menu():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    kb.add("Пропустить ⏭️", "Отправить ✅")
    kb.add("❌ Отмена")
    return kb

# ============ БАЗОВЫЕ КОМАНДЫ ============
@bot.message_handler(commands=['start'])
def start_command(message: types.Message):
    reset_flow(message.chat.id)
    bot.send_message(
        message.chat.id,
        "Привет! Это <b>TripBuddy</b> — ваш помощник по путешествиям ✈️\nВыберите действие:",
        reply_markup=main_menu()
    )

@bot.message_handler(commands=['groupid'])
def send_group_id(message: types.Message):
    bot.send_message(message.chat.id, f"ID этой переписки: {message.chat.id}")

@bot.message_handler(commands=['admin_debug'])
def admin_debug(message: types.Message):
    bot.send_message(
        message.chat.id,
        f"Эта переписка chat_id: {message.chat.id}\n"
        f"ADMIN_GROUP_ID_INT: {ADMIN_GROUP_ID_INT}\n"
        f"ADMIN_USERNAME: {ADMIN_USERNAME or '—'}\n"
        f"Group Privacy должен быть Disabled в BotFather."
    )

@bot.message_handler(func=lambda m: m.text in ["📄 Оферта", "/offer"])
def send_offer(message: types.Message):
    pdf_path = "public_offer_tripbuddy.pdf"
    if os.path.exists(pdf_path):
        with open(pdf_path, "rb") as f:
            bot.send_document(message.chat.id, f, visible_file_name="TripBuddy_Offer.pdf")
        bot.send_message(message.chat.id, "Оплачивая услугу, вы подтверждаете согласие с условиями публичной оферты.")
    else:
        bot.send_message(message.chat.id, "Не нашла файл оферты. Загрузите <b>public_offer_tripbuddy.pdf</b> в корень проекта.")

@bot.message_handler(func=lambda m: m.text == "💬 Администратор")
def admin_flow(message: types.Message):
    who = ADMIN_USERNAME or "—"
    bot.send_message(message.chat.id, f"Связаться с администратором: {who}\nОтветим быстро 🙂")

@bot.message_handler(commands=['cancel'])
@bot.message_handler(func=lambda m: m.text == "❌ Отмена")
def cancel_flow(message: types.Message):
    reset_flow(message.chat.id)
    bot.send_message(message.chat.id, "Окей, остановила. Чем ещё помочь?", reply_markup=main_menu())

# ============ ПРИЁМ ВЛОЖЕНИЙ (общий) ============
@bot.message_handler(content_types=['photo', 'document'])
def handle_any_attachments(message: types.Message):
    step = user_step.get(message.chat.id)
    if step is None:
        return  # вне сценария — игнор
    data = user_data.setdefault(message.chat.id, {})
    bucket = "attachments" if not str(step).startswith("pay_") else "pay_attachments"
    data.setdefault(bucket, [])
    if message.photo:
        data[bucket].append(("photo", message.photo[-1].file_id))
        bot.send_message(message.chat.id, "📸 Фото принято ✅")
    elif message.document:
        data[bucket].append(("doc", message.document.file_id))
        bot.send_message(message.chat.id, "📎 Документ принят ✅")

# ============ ЗАЯВКА ============
@bot.message_handler(func=lambda m: m.text == "📝 Оставить заявку")
def request_start(message: types.Message):
    reset_flow(message.chat.id)
    user_data[message.chat.id] = {"attachments": []}
    user_step[message.chat.id] = "type"
    bot.send_message(message.chat.id, "Какой тип заявки? Выберите, пожалуйста:", reply_markup=type_menu())

@bot.message_handler(func=lambda m: user_step.get(m.chat.id) == "type")
def step_type(message: types.Message):
    t = (message.text or "").strip()
    if t not in ["🏨 Отель", "✈️ Билеты", "❌ Отмена"]:
        return bot.send_message(message.chat.id, "Пожалуйста, выберите на клавиатуре.", reply_markup=type_menu())
    if t == "❌ Отмена": return cancel_flow(message)
    user_data[message.chat.id]["type"] = "Отель" if "Отель" in t else "Билеты"
    user_step[message.chat.id] = "route"
    prompt = "🏙️ Город/страна назначения:" if user_data[message.chat.id]["type"] == "Отель" else "🛫 Маршрут (откуда → куда):"
    bot.send_message(message.chat.id, prompt, reply_markup=cancel_menu())

@bot.message_handler(func=lambda m: user_step.get(m.chat.id) == "route")
def step_route(message: types.Message):
    if message.text == "❌ Отмена": return cancel_flow(message)
    user_data[message.chat.id]["route"] = message.text.strip()
    user_step[message.chat.id] = "dates"
    bot.send_message(message.chat.id, "🗓️ Даты (например 01.09–07.09 или «гибко ±2 дня»):", reply_markup=cancel_menu())

@bot.message_handler(func=lambda m: user_step.get(m.chat.id) == "dates")
def step_dates(message: types.Message):
    if message.text == "❌ Отмена": return cancel_flow(message)
    user_data[message.chat.id]["dates"] = message.text.strip()
    if user_data[message.chat.id]["type"] == "Отель":
        user_step[message.chat.id] = "guests"
        bot.send_message(message.chat.id, "👥 Кол-во гостей и детей (например «2 взрослых, 1 ребёнок 5 лет»):", reply_markup=cancel_menu())
    else:
        user_step[message.chat.id] = "class"
        bot.send_message(message.chat.id, "🪑 Класс перелёта:", reply_markup=class_menu())

# --- Отель ---
@bot.message_handler(func=lambda m: user_step.get(m.chat.id) == "guests")
def step_guests(message: types.Message):
    if message.text == "❌ Отмена": return cancel_flow(message)
    user_data[message.chat.id]["guests"] = message.text.strip()
    user_step[message.chat.id] = "rooms"
    bot.send_message(message.chat.id, "🛏️ Нужное кол-во комнат:", reply_markup=rooms_menu())

@bot.message_handler(func=lambda m: user_step.get(m.chat.id) == "rooms")
def step_rooms(message: types.Message):
    if message.text == "❌ Отмена": return cancel_flow(message)
    user_data[message.chat.id]["rooms"] = message.text.strip()
    user_step[message.chat.id] = "stars"
    bot.send_message(message.chat.id, "⭐️ Предпочитаемая звёздность:", reply_markup=stars_menu())

@bot.message_handler(func=lambda m: user_step.get(m.chat.id) == "stars")
def step_stars(message: types.Message):
    if message.text == "❌ Отмена": return cancel_flow(message)
    user_data[message.chat.id]["stars"] = message.text.strip()
    user_step[message.chat.id] = "breakfast"
    bot.send_message(message.chat.id, "🍳 Нужен ли завтрак?", reply_markup=yes_no_menu())

@bot.message_handler(func=lambda m: user_step.get(m.chat.id) == "breakfast")
def step_breakfast(message: types.Message):
    if message.text == "❌ Отмена": return cancel_flow(message)
    user_data[message.chat.id]["breakfast"] = message.text.strip()
    user_step[message.chat.id] = "location_pref"
    bot.send_message(message.chat.id, "📍 Пожелания по расположению (центр, у моря, район):", reply_markup=cancel_menu())

# --- Билеты ---
@bot.message_handler(func=lambda m: user_step.get(m.chat.id) == "class")
def step_class(message: types.Message):
    if message.text == "❌ Отмена": return cancel_flow(message)
    user_data[message.chat.id]["class"] = message.text.strip()
    user_step[message.chat.id] = "baggage"
    bot.send_message(message.chat.id, "🧳 Нужен багаж?", reply_markup=yes_no_menu())

@bot.message_handler(func=lambda m: user_step.get(m.chat.id) == "baggage")
def step_baggage(message: types.Message):
    if message.text == "❌ Отмена": return cancel_flow(message)
    user_data[message.chat.id]["baggage"] = message.text.strip()
    user_step[message.chat.id] = "carriers"
    bot.send_message(message.chat.id, "✈️ Предпочтительные авиакомпании (если есть):", reply_markup=cancel_menu())

# --- Общие шаги ---
@bot.message_handler(func=lambda m: user_step.get(m.chat.id) == "location_pref")
def step_location_pref(message: types.Message):
    if message.text == "❌ Отмена": return cancel_flow(message)
    user_data[message.chat.id]["location_pref"] = message.text.strip()
    user_step[message.chat.id] = "budget"
    bot.send_message(message.chat.id, "💰 Бюджет (за ночь / общий):", reply_markup=cancel_menu())

@bot.message_handler(func=lambda m: user_step.get(m.chat.id) == "carriers")
def step_carriers(message: types.Message):
    if message.text == "❌ Отмена": return cancel_flow(message)
    user_data[message.chat.id]["carriers"] = message.text.strip()
    user_step[message.chat.id] = "budget"
    bot.send_message(message.chat.id, "💰 Бюджет на перелёт:", reply_markup=cancel_menu())

@bot.message_handler(func=lambda m: user_step.get(m.chat.id) == "budget")
def step_budget(message: types.Message):
    if message.text == "❌ Отмена": return cancel_flow(message)
    user_data[message.chat.id]["budget"] = message.text.strip()
    user_step[message.chat.id] = "contact"
    bot.send_message(message.chat.id, "📞 Как с вами связаться? (телеграм @ник или номер):", reply_markup=cancel_menu())

@bot.message_handler(func=lambda m: user_step.get(m.chat.id) == "contact")
def step_contact(message: types.Message):
    if message.text == "❌ Отмена": return cancel_flow(message)
    user_data[message.chat.id]["contact"] = message.text.strip()
    user_step[message.chat.id] = "fullname"
    bot.send_message(message.chat.id, "🪪 ФИО латиницей (как в паспорте):", reply_markup=cancel_menu())

@bot.message_handler(func=lambda m: user_step.get(m.chat.id) == "fullname")
def step_fullname(message: types.Message):
    if message.text == "❌ Отмена": return cancel_flow(message)
    user_data[message.chat.id]["fullname"] = message.text.strip()
    user_step[message.chat.id] = "dob"
    bot.send_message(message.chat.id, "🎂 Дата рождения (ДД.ММ.ГГГГ):", reply_markup=cancel_menu())

@bot.message_handler(func=lambda m: user_step.get(m.chat.id) == "dob")
def step_dob(message: types.Message):
    if message.text == "❌ Отмена": return cancel_flow(message)
    user_data[message.chat.id]["dob"] = message.text.strip()
    if user_data[message.chat.id]["type"] == "Билеты":
        user_step[message.chat.id] = "gender"
        bot.send_message(message.chat.id, "👤 Пол:", reply_markup=gender_menu())
    else:
        user_step[message.chat.id] = "citizenship"
        bot.send_message(message.chat.id, "🌍 Гражданство:", reply_markup=cancel_menu())

@bot.message_handler(func=lambda m: user_step.get(m.chat.id) == "gender")
def step_gender(message: types.Message):
    if message.text == "❌ Отмена": return cancel_flow(message)
    user_data[message.chat.id]["gender"] = message.text.strip()
    user_step[message.chat.id] = "citizenship"
    bot.send_message(message.chat.id, "🌍 Гражданство:", reply_markup=cancel_menu())

@bot.message_handler(func=lambda m: user_step.get(m.chat.id) == "citizenship")
def step_citizenship(message: types.Message):
    if message.text == "❌ Отмена": return cancel_flow(message)
    user_data[message.chat.id]["citizenship"] = message.text.strip()
    user_step[message.chat.id] = "passport_no"
    bot.send_message(message.chat.id, "🔢 Номер паспорта:", reply_markup=cancel_menu())

@bot.message_handler(func=lambda m: user_step.get(m.chat.id) == "passport_no")
def step_passport_no(message: types.Message):
    if message.text == "❌ Отмена": return cancel_flow(message)
    user_data[message.chat.id]["passport_no"] = message.text.strip()
    user_step[message.chat.id] = "passport_exp"
    bot.send_message(message.chat.id, "📅 Срок действия паспорта (ДД.ММ.ГГГГ):", reply_markup=cancel_menu())

@bot.message_handler(func=lambda m: user_step.get(m.chat.id) == "passport_exp")
def step_passport_exp(message: types.Message):
    if message.text == "❌ Отмена": return cancel_flow(message)
    user_data[message.chat.id]["passport_exp"] = message.text.strip()
    user_step[message.chat.id] = "attachments"
    bot.send_message(
        message.chat.id,
        "📎 Прикрепите скриншоты/документы (паспорт, примеры билетов/отелей) — по одному за сообщение.\n"
        "Когда закончите, нажмите <b>«Готово ✅»</b> или «Пропустить ⏭️».",
        reply_markup=attachments_menu()
    )

@bot.message_handler(func=lambda m: user_step.get(m.chat.id) == "attachments")
def finish_attachments(message: types.Message):
    t = (message.text or "").strip()

    # 🔹 Анти-дубли: если уже отправляли эту заявку — игнорируем повтор
    if user_data.get(message.chat.id, {}).get("submitted_request"):
        return bot.send_message(
            message.chat.id,
            "Эта заявка уже была отправлена ✅",
            reply_markup=main_menu()
        )

    if t == "❌ Отмена":
        return cancel_flow(message)
    if t not in ["Пропустить ⏭️", "Готово ✅"]:
        return bot.send_message(
            message.chat.id,
            "Добавьте файл(ы) или нажмите «Готово ✅» / «Пропустить ⏭️».",
            reply_markup=attachments_menu()
        )

    cid = message.chat.id
    d = user_data.get(cid, {})
    username = f"@{message.from_user.username}" if message.from_user.username else "—"

    # Сохраним в БД
    cur = _exec("""
        INSERT INTO requests
        (chat_id, type, route, dates, guests, rooms, stars, breakfast, location_pref, budget,
         class, baggage, carriers, fullname, dob, gender, citizenship, passport_no, passport_exp, contact)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        cid,
        d.get("type"),
        d.get("route"),
        d.get("dates"),
        d.get("guests"),
        d.get("rooms"),
        d.get("stars"),
        d.get("breakfast"),
        d.get("location_pref"),
        d.get("budget"),
        d.get("class"),
        d.get("baggage"),
        d.get("carriers"),
        d.get("fullname"),
        d.get("dob"),
        d.get("gender"),
        d.get("citizenship"),
        d.get("passport_no"),
        d.get("passport_exp"),
        d.get("contact"),
    ))
    request_id = cur.lastrowid

    # attachments -> request_attachments
    for kind, fid in d.get("attachments", []):
        _exec(
            "INSERT INTO request_attachments (request_id, kind, file_id) VALUES (?, ?, ?)",
            (request_id, kind, fid)
        )

    # Сообщение в админ-группу
    if d.get("type") == "Отель":
        body = (
            "<b>Новая заявка TripBuddy</b> 🏨\n"
            f"Город: {d.get('route','—')}\n"
            f"Даты: {d.get('dates','—')}\n"
            f"Гости: {d.get('guests','—')}\n"
            f"Комнаты: {d.get('rooms','—')}\n"
            f"⭐️ Звёздность: {d.get('stars','—')}\n"
            f"Завтрак: {d.get('breakfast','—')}\n"
            f"Локация: {d.get('location_pref','—')}\n"
            f"Бюджет: {d.get('budget','—')}\n"
            "— — — Данные гостя — — —\n"
            f"ФИО (латиницей): {d.get('fullname','—')}\n"
            f"Дата рождения: {d.get('dob','—')}\n"
            f"Гражданство: {d.get('citizenship','—')}\n"
            f"Паспорт №: {d.get('passport_no','—')}\n"
            f"Паспорт действ. до: {d.get('passport_exp','—')}\n"
            f"Контакт: {d.get('contact','—')}\n\n"
            f"От пользователя: {username} (id {message.from_user.id})"
        )
    else:
        body = (
            "<b>Новая заявка TripBuddy</b> ✈️\n"
            f"Маршрут: {d.get('route','—')}\n"
            f"Даты: {d.get('dates','—')}\n"
            f"Класс: {d.get('class','—')}\n"
            f"Багаж: {d.get('baggage','—')}\n"
            f"Авиакомпании: {d.get('carriers','—')}\n"
            f"Бюджет: {d.get('budget','—')}\n"
            "— — — Данные пассажира — — —\n"
            f"ФИО (латиницей): {d.get('fullname','—')}\n"
            f"Дата рождения: {d.get('dob','—')}\n"
            f"Пол: {d.get('gender','—')}\n"
            f"Гражданство: {d.get('citizenship','—')}\n"
            f"Паспорт №: {d.get('passport_no','—')}\n"
            f"Паспорт действ. до: {d.get('passport_exp','—')}\n"
            f"Контакт: {d.get('contact','—')}\n\n"
            f"От пользователя: {username} (id {message.from_user.id})"
        )

    if ADMIN_GROUP_ID_INT is not None:
        try:
            admin_id = ADMIN_GROUP_ID_INT
            sent = bot.send_message(admin_id, body)
            photos = [fid for kind, fid in d.get("attachments", []) if kind == "photo"]
            docs   = [fid for kind, fid in d.get("attachments", []) if kind == "doc"]
            if photos:
                media = [types.InputMediaPhoto(fid) for fid in photos[:10]]
                bot.send_media_group(admin_id, media, reply_to_message_id=sent.message_id)
            for fid in docs:
                bot.send_document(admin_id, fid, reply_to_message_id=sent.message_id)
        except Exception as e:
            bot.send_message(cid, f"Не удалось отправить заявку в админ-группу: {e}")

    bot.send_message(cid, "Спасибо! Заявка отправлена. Мы скоро свяжемся 🤝", reply_markup=main_menu())

    # ✅ помечаем как отправленную — чтобы второй раз не ушла
    user_data.setdefault(cid, {})["submitted_request"] = True
    reset_flow(cid)

# ============ ОПЛАТА ============

@bot.message_handler(commands=['whereami'])
def cmd_whereami(message: types.Message):
    step = user_step.get(message.chat.id)
    d = user_data.get(message.chat.id)
    bot.reply_to(message, f"Текущий шаг: {step or '—'}\nКлючи user_data: {list(d.keys()) if d else '—'}")

@bot.message_handler(func=lambda m: m.text == "✅ Я оплатил(а)")
def pay_start(message: types.Message):
    # старт оплаты
    reset_flow(message.chat.id)
    user_data[message.chat.id] = {"pay_attachments": []}
    user_step[message.chat.id] = "pay_amount"
    bot.send_message(
        message.chat.id,
        "💳 Укажите сумму и валюту (например: 60000 RUB):",
        reply_markup=cancel_menu()
    )

@bot.message_handler(func=lambda m: user_step.get(m.chat.id) == "pay_amount")
def pay_amount(message: types.Message):
    if (message.text or "") == "❌ Отмена":
        return cancel_flow(message)
    user_data[message.chat.id]["pay_amount_raw"] = (message.text or "").strip()
    user_step[message.chat.id] = "pay_date"
    bot.send_message(
        message.chat.id,
        "📅 Дата/время оплаты (например 17.08.2025 15:40):",
        reply_markup=cancel_menu()
    )

@bot.message_handler(func=lambda m: user_step.get(m.chat.id) == "pay_date")
def pay_date(message: types.Message):
    if (message.text or "") == "❌ Отмена":
        return cancel_flow(message)
    user_data[message.chat.id]["pay_date"] = (message.text or "").strip()
    user_step[message.chat.id] = "pay_method"
    bot.send_message(
        message.chat.id,
        "🏦 Способ: Т-Банк перевод / другой банк / наличные / иное:",
        reply_markup=cancel_menu()
    )

@bot.message_handler(func=lambda m: user_step.get(m.chat.id) == "pay_method")
def pay_method(message: types.Message):
    if (message.text or "") == "❌ Отмена":
        return cancel_flow(message)
    user_data[message.chat.id]["pay_method"] = (message.text or "").strip()
    user_step[message.chat.id] = "pay_attach"
    bot.send_message(
        message.chat.id,
        "📎 Прикрепите чек/скрин перевода (можно несколько). Когда закончите — нажмите «Отправить ✅» или «Пропустить ⏭️».",
        reply_markup=pay_finish_menu()
    )

# Приём файлов (фото/док) у вас общий — он уже кладёт во "pay_attachments", когда step начинается с "pay_".
# Здесь завершаем оплату.

@bot.message_handler(func=lambda m: user_step.get(m.chat.id) == "pay_attach")
def pay_finish(message: types.Message):
    t = (message.text or "").strip()

    # Разрешаем только две кнопки или отмену
    if t == "❌ Отмена":
        return cancel_flow(message)
    if t not in ["Пропустить ⏭️", "Отправить ✅"]:
        return bot.send_message(
            message.chat.id,
            "Пришлите файл(ы) или нажмите «Отправить ✅» / «Пропустить ⏭️».",
            reply_markup=pay_finish_menu()
        )

    return _complete_payment(message)

# «Страховка»: если по какой-то причине шаг потерялся, но человек нажал «Отправить ✅»/«Пропустить ⏭️» —
# попробуем всё равно завершить оплату.
@bot.message_handler(func=lambda m: (m.text or "").strip() in ["Пропустить ⏭️", "Отправить ✅"])
def pay_finish_fallback(message: types.Message):
    step = user_step.get(message.chat.id)
    if step == "pay_attach":
        # основной обработчик выше отработает, сюда не попадём
        return
    # если шаг потерян, но в user_data есть следы оплаты — завершим
    d = user_data.get(message.chat.id) or {}
    if any(k.startswith("pay_") for k in d.keys()):
        return _complete_payment(message)
    # иначе ничего не делаем — пусть сработает fallback ниже


def _complete_payment(message: types.Message):
    cid = message.chat.id
    d = user_data.get(cid, {})
    username = f"@{message.from_user.username}" if message.from_user.username else "—"

    # Анти-дубли: если уже завершали оплату в этом диалоге
    if d.get("_pay_done"):
        bot.send_message(cid, "Уведомление об оплате уже отправлено ✅", reply_markup=main_menu())
        return

    # Разбор суммы/валюты
    amt, cur = parse_amount_currency(d.get("pay_amount_raw", ""))

    # Сохранение в БД
    try:
        curc = _exec("""
            INSERT INTO payments (chat_id, amount, currency, pay_method, pay_date)
            VALUES (?, ?, ?, ?, ?)
        """, (cid, amt, cur, d.get("pay_method"), d.get("pay_date")))
        payment_id = curc.lastrowid

        for kind, fid in d.get("pay_attachments", []):
            _exec(
                "INSERT INTO payment_files (payment_id, kind, file_id) VALUES (?, ?, ?)",
                (payment_id, kind, fid)
            )
    except Exception as e:
        bot.send_message(cid, f"⚠️ Не удалось сохранить оплату в БД: {e}")

    # Сообщение в админ-группу
    body = (
        "<b>Уведомление об оплате</b> ✅\n"
        f"Сумма: {d.get('pay_amount_raw','—')}\n"
        f"Дата/время: {d.get('pay_date','—')}\n"
        f"Способ: {d.get('pay_method','—')}\n\n"
        f"От пользователя: {username} (id {message.from_user.id})"
    )

    if ADMIN_GROUP_ID_INT is not None:
        try:
            admin_id = ADMIN_GROUP_ID_INT
            sent = bot.send_message(admin_id, body)
            photos = [fid for kind, fid in d.get("pay_attachments", []) if kind == "photo"]
            docs   = [fid for kind, fid in d.get("pay_attachments", []) if kind == "doc"]
            if photos:
                media = [types.InputMediaPhoto(fid) for fid in photos[:10]]
                bot.send_media_group(admin_id, media, reply_to_message_id=sent.message_id)
            for fid in docs:
                bot.send_document(admin_id, fid, reply_to_message_id=sent.message_id)
        except Exception as e:
            bot.send_message(cid, f"Не удалось отправить уведомление в админ-группу: {e}")

    d["_pay_done"] = True
    bot.send_message(
        cid,
        "Спасибо! Получили уведомление об оплате. Проверим и вернёмся с подтверждением 🙌",
        reply_markup=main_menu()
    )
    reset_flow(cid)


# ============ АДМИН-КОМАНДЫ И ДИАГНОСТИКА ============

def _norm_username(u: str) -> str:
    """Нормализуем username: убираем @ и приводим к нижнему регистру."""
    if not u:
        return ""
    return u.lstrip("@").lower()


def is_admin(message: types.Message) -> bool:
    """
    Правила:
    1) Если задан ADMIN_GROUP_ID_INT — все сообщения из этого чата считаем админскими.
    2) Если задан ADMIN_USERNAME — владелец этого username (без учёта регистра и @) считается админом.
    3) Если ни ADMIN_GROUP_ID_INT ни ADMIN_USERNAME не заданы — разрешаем всем (режим разработки).
    """
    # 1) Чат-админка
    if ADMIN_GROUP_ID_INT is not None and message.chat.id == ADMIN_GROUP_ID_INT:
        return True

    # 2) Юзер-админка по username
    if ADMIN_USERNAME:
        my = _norm_username(message.from_user.username or "")
        adm = _norm_username(ADMIN_USERNAME)
        if my and adm and my == adm:
            return True

    # 3) Фоллбэк (если ничего не задано) — разрешаем всем
    if ADMIN_GROUP_ID_INT is None and not ADMIN_USERNAME:
        return True

    return False


def admin_only(func):
    """Декоратор для команд только для админа."""
    def wrapper(message: types.Message):
        if not is_admin(message):
            return bot.reply_to(message, "Команда доступна только администратору.")
        return func(message)
    return wrapper


# Быстрый пинг — удобно проверять, «жив» ли хэндлер команд
@bot.message_handler(commands=['ping'])
def cmd_ping(message: types.Message):
    bot.reply_to(message, "pong")


# Короткий просмотр внутреннего состояния FSM (аналог /whereami)
@bot.message_handler(commands=['state'])
def cmd_state(message: types.Message):
    step = user_step.get(message.chat.id)
    d = user_data.get(message.chat.id)
    bot.reply_to(
        message,
        f"Текущий шаг: {step or '—'}\nКлючи user_data: {list(d.keys()) if d else '—'}"
    )


# Проверка админ-прав и окружения
@bot.message_handler(commands=['iamadmin'])
def cmd_iamadmin(message: types.Message):
    ok = is_admin(message)
    who = f"@{message.from_user.username}" if message.from_user.username else f"id {message.from_user.id}"
    where = f"chat_id={message.chat.id}"
    bot.reply_to(
        message,
        f"is_admin={ok}\nwho={who}\nwhere={where}\n"
        f"ADMIN_USERNAME={ADMIN_USERNAME or '—'}\nADMIN_GROUP_ID_INT={ADMIN_GROUP_ID_INT}"
    )


# /migrate — добавить недостающие колонки в БД (без удаления файла)
@bot.message_handler(commands=['migrate'])
@admin_only
def cmd_migrate(message: types.Message):
    try:
        add_missing_columns()
        bot.reply_to(message, "Миграция: ок ✅")
    except Exception as e:
        bot.reply_to(message, f"Миграция: ошибка: {e}")


# /stats — статистика
@bot.message_handler(commands=['stats'])
@admin_only
def cmd_stats(message: types.Message):
    today = datetime.utcnow().date()
    start_today = f"{today} 00:00:00"
    start_week  = f"{(today - timedelta(days=6))} 00:00:00"

    c1 = _exec("SELECT COUNT(*) as c FROM requests WHERE datetime(created_at) >= datetime(?)", (start_today,)).fetchone()["c"]
    c2 = _exec("SELECT COUNT(*) as c FROM requests WHERE datetime(created_at) >= datetime(?)", (start_week,)).fetchone()["c"]

    p1 = _exec("SELECT COUNT(*) as c FROM payments WHERE datetime(created_at) >= datetime(?)", (start_today,)).fetchone()["c"]
    p2 = _exec("SELECT COUNT(*) as c FROM payments WHERE datetime(created_at) >= datetime(?)", (start_week,)).fetchone()["c"]

    rows = _exec("SELECT amount, currency FROM payments WHERE datetime(created_at) >= datetime(?)", (start_week,)).fetchall()
    sums = {}
    for r in rows:
        cur = (r["currency"] or "").upper() or "RUB"
        sums.setdefault(cur, 0.0)
        try:
            sums[cur] += float((r["amount"] or "0").replace(",", "."))
        except:
            pass
    sums_str = ", ".join([f"{round(v,2)} {k}" for k, v in sums.items()]) if sums else "—"

    bot.reply_to(message,
        "<b>Статистика</b>\n\n"
        f"Заявок: сегодня {c1} / за 7 дней {c2}\n"
        f"Оплат: сегодня {p1} / за 7 дней {p2}\n"
        f"Сумма (7д): {sums_str}"
    )


# /export_csv — выгрузка таблиц
@bot.message_handler(commands=['export_csv'])
@admin_only
def cmd_export_csv(message: types.Message):
    try:
        # requests.csv
        req_rows = _exec("""
            SELECT id, chat_id, type, route, dates, guests, rooms, stars, breakfast, location_pref, budget,
                   class, baggage, carriers, fullname, dob, gender, citizenship, passport_no, passport_exp, contact, created_at
            FROM requests ORDER BY id DESC
        """).fetchall()
        with open("requests.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f, delimiter=";")
            w.writerow([c for c in req_rows[0].keys()] if req_rows else
                       ["id","chat_id","type","route","dates","guests","rooms","stars","breakfast","location_pref","budget",
                        "class","baggage","carriers","fullname","dob","gender","citizenship","passport_no","passport_exp","contact","created_at"])
            for r in req_rows:
                w.writerow([r[k] for k in r.keys()])
        with open("requests.csv", "rb") as f:
            bot.send_document(message.chat.id, f, visible_file_name="requests.csv")

        # payments.csv
        pay_rows = _exec("""
            SELECT id, chat_id, amount, currency, pay_method, pay_date, created_at
            FROM payments ORDER BY id DESC
        """).fetchall()
        with open("payments.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f, delimiter=";")
            w.writerow([c for c in pay_rows[0].keys()] if pay_rows else
                       ["id","chat_id","amount","currency","pay_method","pay_date","created_at"])
            for r in pay_rows:
                w.writerow([r[k] for k in r.keys()])
        with open("payments.csv", "rb") as f:
            bot.send_document(message.chat.id, f, visible_file_name="payments.csv")

        # payment_files.csv
        pfiles = _exec("""
            SELECT pf.id, pf.payment_id, p.chat_id, pf.kind, pf.file_id, p.created_at
            FROM payment_files pf
            LEFT JOIN payments p ON p.id = pf.payment_id
            ORDER BY pf.id DESC
        """).fetchall()
        with open("payment_files.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f, delimiter=";")
            w.writerow([c for c in pfiles[0].keys()] if pfiles else
                       ["id","payment_id","chat_id","kind","file_id","created_at"])
            for r in pfiles:
                w.writerow([r[k] for k in r.keys()])
        with open("payment_files.csv", "rb") as f:
            bot.send_document(message.chat.id, f, visible_file_name="payment_files.csv")

    except Exception as e:
        bot.reply_to(message, f"Ошибка экспорта: {e}")


# /find <chat_id> — показать последнюю заявку по chat_id
@bot.message_handler(commands=['find'])
@admin_only
def cmd_find(message: types.Message):
    parts = message.text.split(maxsplit=1)
    chat_id = None
    if len(parts) == 2 and parts[1].strip():
        arg = parts[1].strip()
        if arg.lstrip("-").isdigit():
            try:
                chat_id = int(arg)
            except:
                pass
    if chat_id is None and message.reply_to_message:
        m = re.search(r"id (\-?\d+)\)", message.reply_to_message.text or "")
        if m:
            chat_id = int(m.group(1))
    if chat_id is None:
        return bot.reply_to(message, "Укажите chat_id числом или ответьте командой на карточку заявки.")

    row = _exec("SELECT * FROM requests WHERE chat_id=? ORDER BY id DESC LIMIT 1", (chat_id,)).fetchone()
    if not row:
        return bot.reply_to(message, "Заявок не найдено.")

    text = (
        "<b>Последняя заявка</b>\n"
        f"chat_id: {row['chat_id']}\n"
        f"Тип: {row['type']}\n"
        f"Маршрут/Город: {row['route']}\n"
        f"Даты: {row['dates']}\n"
        f"Контакт: {row['contact']}\n"
        f"Создано: {row['created_at']}"
    )
    bot.reply_to(message, text)


# /invoice — гибкий парсер:
# 1) /invoice <chat_id> <base> [currency] [fee] [note...]
# 2) (reply на карточку заявки) /invoice <base> [currency] [fee] [note...]
@bot.message_handler(commands=['invoice'])
@admin_only
def cmd_invoice(message: types.Message):
    # Нормализация чисел: убираем пробелы/неразрывные пробелы, заменяем запятую на точку
    def _norm_num_token(s: str) -> str:
        if not s:
            return ""
        s = s.replace("\u00A0", " ")  # NBSP -> space
        s = s.replace(" ", "")
        s = s.replace(",", ".")
        return s

    def _is_number_token(s: str) -> bool:
        s = _norm_num_token(s)
        return bool(re.fullmatch(r"-?\d+(\.\d+)?", s))

    def _to_float(s: str) -> float:
        return float(_norm_num_token(s))

    tokens = message.text.split()[1:]  # без самого "/invoice"
    if not tokens:
        return bot.reply_to(message, "Использование: /invoice <chat_id> <base> [currency] [fee] [note...] или ответом на карточку: /invoice <base> [currency] [fee] [note]")

    chat_id = None
    base_tok = None
    currency = "RUB"
    fee_tok = None
    note_parts = []

    # 1) Если первый токен — chat_id (число, в т.ч. отрицательное для супергрупп)
    i = 0
    if tokens and re.fullmatch(r"-?\d+", tokens[0]):
        chat_id = int(tokens[0])
        i = 1  # дальше парсим сумму/валюту/fee/ноту
    # иначе работаем в режиме reply — chat_id возьмём из карточки ниже

    # 2) Проходим оставшиеся токены в порядке появления:
    #    первая цифра -> base; одна из валют -> currency; следующая цифра -> fee; всё остальное -> note
    while i < len(tokens):
        t = tokens[i]
        up = t.upper()

        if base_tok is None and _is_number_token(t):
            base_tok = t
        elif up in ["RUB", "USD", "EUR", "GEL"]:
            currency = up
        elif fee_tok is None and _is_number_token(t):
            fee_tok = t
        else:
            note_parts.append(t)
        i += 1

    # 3) Если chat_id не указан параметром — пробуем вытащить его из reply на карточку
    if chat_id is None:
        if not message.reply_to_message:
            return bot.reply_to(message, "Либо укажи: /invoice <chat_id> <base> [currency] [fee] [note], либо ответь командой на карточку заявки.")
        m = re.search(r"id (\-?\d+)\)", message.reply_to_message.text or "")
        if not m:
            return bot.reply_to(message, "Не нашла user_id в карточке. Ответьте командой /invoice на карточку заявки.")
        chat_id = int(m.group(1))

    # 4) Проверяем, что есть базовая сумма
    if not base_tok or not _is_number_token(base_tok):
        return bot.reply_to(message, "Сумма base некорректна. Пример: 65000 или 65000.00")

    # 5) Числа -> float, по умолчанию fee = 0
    try:
        base = _to_float(base_tok)
    except:
        return bot.reply_to(message, "Сумма base некорректна. Пример: 65000 или 65000.00")
    fee = 0.0
    if fee_tok and _is_number_token(fee_tok):
        try:
            fee = _to_float(fee_tok)
        except:
            fee = 0.0

    note = " ".join(note_parts).strip()

    # 6) Формируем и отправляем инвойс
    total = int(round(base + fee)) if currency in ["RUB", "GEL"] else round(base + fee, 2)
    text = ("🧾 <b>Инвойс</b>\n" + (f"{note}\n\n" if note else "\n")) + PAYMENT_INSTRUCTIONS.format(
        base=int(base) if currency in ["RUB","GEL"] else base,
        fee=int(fee) if currency in ["RUB","GEL"] else fee,
        total=total,
        currency=currency
    )
    try:
        bot.send_message(chat_id, text, parse_mode="HTML")
        bot.send_message(
            chat_id,
            "После оплаты нажмите «✅ Я оплатил(а)» и пришлите чек.",
            reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add("✅ Я оплатил(а)")
        )
        bot.reply_to(message, f"Инвойс отправлен клиенту: {total} {currency}.")
    except Exception as e:
        bot.reply_to(message, f"Не удалось отправить инвойс: {e}")


# /confirmpaid <chat_id>  ИЛИ reply на карточку оплаты
@bot.message_handler(commands=['confirmpaid'])
@admin_only
def cmd_confirmpaid(message: types.Message):
    parts = message.text.split(maxsplit=1)
    user_id = None
    if len(parts) == 2 and parts[1].lstrip("-").isdigit():
        user_id = int(parts[1])
    elif message.reply_to_message:
        m = re.search(r"id (\-?\d+)\)", message.reply_to_message.text or "")
        if m:
            user_id = int(m.group(1))
    if user_id is None:
        return bot.reply_to(message, "Укажи: /confirmpaid <chat_id> или ответь командой на карточку оплаты/заявки.")
    try:
        bot.send_message(user_id, "✅ Оплата подтверждена. Спасибо! Пришлём документы по брони в ближайшее время.")
        bot.reply_to(message, "Клиент уведомлён о подтверждении оплаты.")
    except Exception as e:
        bot.reply_to(message, f"Не удалось отправить клиенту: {e}")


# /pm <user_id> <текст> — личное сообщение клиенту от админа
@bot.message_handler(commands=['pm'])
@admin_only
def cmd_pm(message: types.Message):
    parts = message.text.split(maxsplit=2)
    if len(parts) < 3:
        return bot.reply_to(message, "Пример: /pm 123456789 Привет! Мы готовы к брони.")
    try:
        user_id = int(parts[1])
    except:
        return bot.reply_to(message, "user_id должен быть числом.")
    text = parts[2]
    try:
        bot.send_message(user_id, text, parse_mode="HTML")
        bot.reply_to(message, "Отправила клиенту.")
    except Exception as e:
        bot.reply_to(message, f"Ошибка отправки: {e}")


# /senddoc <user_id> — ответьте этой командой на сообщение с файлом/фото в группе
@bot.message_handler(commands=['senddoc'])
@admin_only
def cmd_senddoc(message: types.Message):
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        return bot.reply_to(message, "Пример: ответьте на сообщение с PDF/фото и введите /senddoc 123456789")
    try:
        user_id = int(parts[1])
    except:
        return bot.reply_to(message, "user_id должен быть числом.")
    if not message.reply_to_message:
        return bot.reply_to(message, "Нужно ответить этой командой на сообщение с вложением.")
    rm = message.reply_to_message
    try:
        if rm.document:
            bot.send_document(user_id, rm.document.file_id, caption=rm.caption or "")
        elif rm.photo:
            bot.send_photo(user_id, rm.photo[-1].file_id, caption=rm.caption or "")
        else:
            return bot.reply_to(message, "В ответном сообщении не найден документ/фото.")
        bot.reply_to(message, "Вложение отправлено клиенту.")
    except Exception as e:
        bot.reply_to(message, f"Не удалось отправить: {e}")


# ============ Fallback (последним!) ============
@bot.message_handler(func=lambda m: m.content_type == 'text' and not ((m.text or "").startswith("/")))
def fallback(message: types.Message):
    bot.send_message(
        message.chat.id,
        "Пока не понял запрос 😅\nВыберите действие из меню ниже:",
        reply_markup=main_menu()
    )

# ============ ЗАПУСК ============
PUBLIC_URL = os.getenv("PUBLIC_URL", "").strip()
PORT = int(os.getenv("PORT", "8080"))  # В проде Replit подставляет PORT, локально дефолт 8080

if PUBLIC_URL:
    # ---- ПРОД / DEPLOY: режим WEBHOOK ----
    print(f"Starting in WEBHOOK mode. PUBLIC_URL={PUBLIC_URL}")
    try:
        bot.remove_webhook()
    except Exception as e:
        print(f"remove_webhook error (ok to ignore): {e}")

    webhook_url = f"{PUBLIC_URL}/webhook/{BOT_TOKEN}"
    ok = bot.set_webhook(url=webhook_url, drop_pending_updates=True)
    print(f"Webhook set to {webhook_url}: {ok}")

    # Запускаем Flask — на порт PORT (в Replit это 8080)
    app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)

else:
    # ---- ЛОКАЛЬНО / WORKSPACE: режим POLLING + мини-Flask (для health-check) ----
    print("Starting in POLLING mode (no PUBLIC_URL).")
    try:
        bot.remove_webhook()
    except Exception as e:
        print(f"remove_webhook error (ok to ignore): {e}")

    # Поднимем Flask в фоне, чтобы / health-check работал и локально
    def _run_web_bg():
        app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)
    Thread(target=_run_web_bg, daemon=True).start()

    # И запустим long polling
    bot.infinity_polling(skip_pending=True, timeout=30, long_polling_timeout=30)

