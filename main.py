import os
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
import telebot
from telebot import types
import psycopg2
from psycopg2.extras import DictCursor

# Получаем настройки из переменных окружения
BOT_TOKEN = os.environ.get('BOT_TOKEN')
DATABASE_URL = os.environ.get('DATABASE_URL')
PORT = int(os.environ.get('PORT', 8080))

bot = telebot.TeleBot(BOT_TOKEN)

# --- ВЕБ-СЕРВЕР ДЛЯ RENDER (Health Check) ---
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"Game server running")

    def do_HEAD(self):
        # Добавляем поддержку HEAD-запросов, которые шлет Render при деплое
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()

    def log_message(self, format, *args):
        # Отключаем лишний спам логов в консоль Render
        return

def run_web_server():
    try:
        server = HTTPServer(('0.0.0.0', PORT), HealthCheckHandler)
        print(f"Веб-сервер успешно запущен на порту {PORT}")
        server.serve_forever()
    except Exception as e:
        print(f"Ошибка запуска веб-сервера: {e}")

# Мгновенный запуск сервера в отдельном потоке
threading.Thread(target=run_web_server, daemon=True).start()

# --- РАБОТА С БАЗОЙ ДАННЫХ (PostgreSQL / Neon) ---
def get_db_connection():
    return psycopg2.connect(DATABASE_URL, sslmode='require', cursor_factory=DictCursor)

def init_db():
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                username TEXT,
                children_count INT DEFAULT 0,
                is_breeding INT DEFAULT 0,
                ready_time DOUBLE PRECISION DEFAULT 0,
                temp_count INT DEFAULT 0,
                seed_multiplier INT DEFAULT 1,
                speed_level INT DEFAULT 1
            );
        ''')
        conn.commit()
        cur.close()
        conn.close()
        print("База данных успешно проверена/создана!")
    except Exception as e:
        print(f"Ошибка инициализации БД: {e}")

init_db()

def get_user(user_id, username):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('SELECT * FROM users WHERE user_id = %s', (user_id,))
    user = cur.fetchone()
    if not user:
        clean_username = username or f"User_{user_id}"
        cur.execute(
            'INSERT INTO users (user_id, username) VALUES (%s, %s) ON CONFLICT (user_id) DO NOTHING', 
            (user_id, clean_username)
        )
        conn.commit()
        cur.execute('SELECT * FROM users WHERE user_id = %s', (user_id,))
        user = cur.fetchone()
    cur.close()
    conn.close()
    return user

def update_user(user_id, column, value):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(f'UPDATE users SET {column} = %s WHERE user_id = %s', (value, user_id))
    conn.commit()
    cur.close()
    conn.close()

def get_top_players():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('SELECT username, children_count FROM users ORDER BY children_count DESC LIMIT 10')
    top = cur.fetchall()
    cur.close()
    conn.close()
    return top

# --- ЛОГИКА ИГРЫ В TELEGRAM ---
def main_menu_keyboard():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(types.KeyboardButton("🍉 Запустить семена"), types.KeyboardButton("👤 Профиль"))
    markup.add(types.KeyboardButton("🛒 Магазин Грейдов"), types.KeyboardButton("🏆 Топ игроков"))
    return markup

@bot.message_handler(commands=['start'])
def start_game(message):
    get_user(message.from_user.id, message.from_user.username)
    bot.send_message(
        message.chat.id, 
        "🍉 Добро пожаловать в симулятор запуска семян!\nВыбирай действие на панели ниже:", 
        reply_markup=main_menu_keyboard()
    )

@bot.message_handler(func=lambda m: m.text == "👤 Профиль")
def show_profile(message):
    user = get_user(message.from_user.id, message.from_user.username)
    current_time = time.time()
    
    if user['is_breeding'] == 1:
        if current_time >= user['ready_time']:
            new_children = user['children_count'] + (user['temp_count'] * user['seed_multiplier'])
            update_user(message.from_user.id, 'children_count', new_children)
            update_user(message.from_user.id, 'is_breeding', 0)
            update_user(message.from_user.id, 'temp_count', 0)
            user = get_user(message.from_user.id, message.from_user.username)
            bot.send_message(message.chat.id, "🎉 Потомство успешно созрело! Дети добавлены в профиль.")

    status = "Свободен" if user['is_breeding'] == 0 else f"⏳ Ожидание родов... ({int(max(0, user['ready_time'] - current_time))} сек)"
    
    profile_text = (
        f"👤 *Ваш профиль:*\n\n"
        f"🍼 Рождено детей: {user['children_count']}\n"
        f"⚡ Множитель семян: x{user['seed_multiplier']}\n"
        f"🏃 Уровень скорости: {user['speed_level']}\n"
        f"⚙️ Статус: {status}"
    )
    bot.send_message(message.chat.id, profile_text, parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text == "🍉 Запустить семена")
def choose_target(message):
    user = get_user(message.from_user.id, message.from_user.username)
    if user['is_breeding'] == 1:
        bot.send_message(message.chat.id, "❌ Процесс уже запущен! Дождитесь рождения детей в профиле.")
        return

    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("🍉 В Арбуз", callback_data="target_арбуз"))
    markup.add(types.InlineKeyboardButton("👳 В Индуса", callback_data="target_индус"))
    markup.add(types.InlineKeyboardButton("👩 В Маму", callback_data="target_маму"))
    bot.send_message(message.chat.id, "🎯 Кому запускаем семена?", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith("target_"))
def choose_amount(call):
    target = call.data.split("_")
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("🌱 10 семян", callback_data=f"spawn_{target}_10"))
    markup.add(types.InlineKeyboardButton("🌿 50 семян", callback_data=f"spawn_{target}_50"))
    markup.add(types.InlineKeyboardButton("🌳 100 семян", callback_data=f"spawn_{target}_100"))
    bot.edit_message_text(f"Цель: {target.capitalize()}.\nСколько семян запустить?", 
                          call.message.chat.id, call.message.message_id, reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith("spawn_"))
def start_timer(call):
    _, target, amount = call.data.split("_")
    amount = int(amount)
    user_id = call.from_user.id
    user = get_user(user_id, call.from_user.username)
    
    base_duration = 15 if amount == 10 else (45 if amount == 50 else 90)
    duration = max(5, base_duration - (user['speed_level'] * 2))  
    
    ready_time = time.time() + duration
    
    update_user(user_id, 'is_breeding', 1)
    update_user(user_id, 'ready_time', ready_time)
    update_user(user_id, 'temp_count', amount)
    
    bot.edit_message_text(f"🚀 Запустили {amount} семян в объект: {target.capitalize()}!\n"
                          f"⏳ Роды через {duration} сек. Проверить результат можно во вкладке '👤 Профиль'.", 
                          call.message.chat.id, call.message.message_id)

@bot.message_handler(func=lambda m: m.text == "🛒 Магазин Грейдов")
def show_shop(message):
    user = get_user(message.from_user.id, message.from_user.username)
    
    cost_mult = user['seed_multiplier'] * 150
    cost_speed = user['speed_level'] * 100
    
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton(f"🧬 Множитель (х{user['seed_multiplier']+1}) — 💰 {cost_mult} детей", callback_data="buy_mult"))
    markup.add(types.InlineKeyboardButton(f"🏃 Скорость (+1 ур) — 💰 {cost_speed} детей", callback_data="buy_speed"))
    
    bot.send_message(message.chat.id, f"🛒 *МАГАЗИН УЛУЧШЕНИЙ*\n\nБаланс детей: {user['children_count']}\nПокупай апгрейды за детей!", 
                     reply_markup=markup, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data.startswith("buy_"))
def item_purchase(call):
    item = call.data.split("_")
    user_id = call.from_user.id
    user = get_user(user_id, call.from_user.username)
    
    if item == "mult":
        cost = user['seed_multiplier'] * 150
        if user['children_count'] >= cost:
            update_user(user_id, 'children_count', user['children_count'] - cost)
            update_user(user_id, 'seed_multiplier', user['seed_multiplier'] + 1)
            bot.answer_callback_query(call.id, "✅ Множитель увеличен!")
        else:
            bot.answer_callback_query(call.id, "❌ Недостаточно детей!", show_alert=True)
            
    elif item == "speed":
        cost = user['speed_level'] * 100
        if user['children_count'] >= cost:
            update_user(user_id, 'children_count', user['children_count'] - cost)
            update_user(user_id, 'speed_level', user['speed_level'] + 1)
            bot.answer_callback_query(call.id, "✅ Скорость инкубации повышена!")
        else:
            bot.answer_callback_query(call.id, "❌ Недостаточно детей!", show_alert=True)

    user = get_user(user_id, call.from_user.username)
    cost_mult = user['seed_multiplier'] * 150
    cost_speed = user['speed_level'] * 100
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton(f"🧬 Множитель (х{user['seed_multiplier']+1}) — 💰 {cost_mult} детей", callback_data="buy_mult"))
    markup.add(types.InlineKeyboardButton(f"🏃 Скорость (+1 ур) — 💰 {cost_speed} детей", callback_data="buy_speed"))
    
    bot.edit_message_text(f"🛒 *МАГАЗИН УЛУЧШЕНИЙ*\n\nБаланс детей: {user['children_count']}\nПокупай апгрейды за детей!", 
    
