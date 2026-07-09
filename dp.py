import asyncio
import random
import time
import math
import html
import re
from datetime import datetime, timedelta, timezone
import aiosqlite
from aiogram import Bot, Dispatcher, F, BaseMiddleware
from aiogram.types import (
    Message, ReplyKeyboardMarkup, KeyboardButton, 
    InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery,
    LabeledPrice, PreCheckoutQuery, TelegramObject
)
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from typing import Callable, Dict, Any, Awaitable

# --- НАСТРОЙКИ ---
BOT_TOKEN = "8636079893:AAH92Dl2QO8Pc0lLeqE0jbis_ACdB3p5IMM"
DB_PATH = "bot_db.sqlite"
ADMIN_ID = 1515703037

PAY_COOLDOWNS = {}
USER_CASE_MODS = {} 

# МАКСИМАЛЬНО РАСШИРЕННЫЙ СПИСОК ЗАПРЕЩЕННЫХ КОРНЕЙ И ПОДСТРОК (МАТ-ФИЛЬТР)
BAD_WORDS = [
    # --- Английская лексика (English profanity & slurs) ---
    "fuck", "shit", "bitch", "cunt", "asshole", "dick", "pussy", "bastard", "slut", "whore", 
    "wanker", "prick", "twat", "faggot", "cock", "motherf", "nigga", "nigger", "crap",
    "retard", "dumbass", "jackass", "arse", "bollocks", "scum", "coock", "diick", "suck",
    # --- Русская лексика (Матерные корни, производные и оскорбления) ---
    "хуй", "хуе", "хуя", "хуи", "пизд", "ебат", "ебан", "ебон", "ебу", "бля", "блиа",
    "сука", "суч", "гандон", "гондон", "мудак", "муди", "пидор", "пидар", "залуп", 
    "манд", "хуес", "шлюх", "проститут", "дроч", "хуил", "охуе", "ахуе", "ниху", 
    "поху", "долбо", "ублюд", "мразь", "тварь", "говн", "гавн", "жоп", "чмо", "лошар", 
    "лох", "трах", "заеб", "выеб", "перееб", "подъеб", "сволоч", "паскуд", "гнида", 
    "скотин", "выродок", "курва", "шалав", "даун", "дебил", "залуп", "педик", "гомик",
    "сосать", "отсос", "сиськи", "залупен", "хмырь", "падла", "выпердыш", "мразота"
]

# Функция глубокой проверки на плохие слова
def contains_bad_words(text: str) -> bool:
    cleaned_text = text.lower().replace(" ", "").replace("_", "").replace("-", "")
    for bad_word in BAD_WORDS:
        if bad_word in cleaned_text:
            return True
    return bool(re.search(r"[хx][уy][йӣeеяаио]|п[ииее]зд|[еe][бб][ааууооее]|бл[яя][дть]", cleaned_text))

TOKENS_INFO = {
    "btc": {"name": "Bitcoin", "price": 50000.0, "init_price": 50000.0, "min": 30000.0, "max": 150000.0},
    "eth": {"name": "Ethereum", "price": 1500.0, "init_price": 1500.0, "min": 750.0, "max": 2500.0},
    "sol": {"name": "Solana", "price": 75.0, "init_price": 75.0, "min": 50.0, "max": 150.0},
    "ltc": {"name": "Litecoin", "price": 50.0, "init_price": 50.0, "min": 25.0, "max": 75.0},
    "doge": {"name": "Dogecoin", "price": 0.1, "init_price": 0.1, "min": 0.1, "max": 0.5},
    "gold": {"name": "Gold", "price": 4000.0, "init_price": 4000.0, "min": 3500.0, "max": 4500.0},
    "silver": {"name": "Silver", "price": 100.0, "init_price": 100.0, "min": 75.0, "max": 125.0},
}

CASES_INFO = {
    "wood": {"name": "Деревянный", "price": 1000.0, "prizes": [100, 250, 500, 750, 1000, 1250, 1500, 1750, 2500]},
    "bronze": {"name": "Бронзовый", "price": 5000.0, "prizes": [250, 500, 1000, 1750, 2500, 5000, 5750, 6250, 7000, 7500]},
    "silver": {"name": "Серебряный", "price": 10000.0, "prizes": [500, 1000, 1750, 2500, 5000, 5500, 6250, 7500, 10000, 11250, 12500, 13750, 15000]},
    "gold": {"name": "Золотой", "price": 25000.0, "prizes": [5000, 6250, 7500, 10000, 12500, 15000, 16250, 17500, 20000, 25000, 27500, 30000, 32500, 35000, 37500, 40000]},
    "summer": {"name": "Летний", "price": 50000.0, "prizes": [7500, 10000, 15000, 17500, 20000, 25000, 30000, 37500, 45000, 50000, 57500, 62500, 67500, 70000, 75000]},
}

class TradeState(StatesGroup):
    waiting_for_nickname = State()
    waiting_for_buy_amount = State()
    waiting_for_sell_amount = State()
    waiting_for_donate_amount = State()
    waiting_for_promocode = State()

# --- АНТИСПАМ СИСТЕМА (1.5 СЕКУНДЫ) ---
class AntiSpamMiddleware(BaseMiddleware):
    def __init__(self):
        self.last_action = {}
        
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        user = None
        if hasattr(event, 'from_user') and event.from_user:
            user = event.from_user
        elif hasattr(event, 'message') and event.message:
            user = event.message.from_user
            
        if user:
            now = time.time()
            last = self.last_action.get(user.id, 0)
            if now - last < 1.5:
                return 
            self.last_action[user.id] = now
            
        return await handler(event, data)

# --- РЕАЛИСТИЧНЫЙ РЫНОК ---
async def market_updater():
    while True:
        await asyncio.sleep(15)
        for token, config in TOKENS_INFO.items():
            price = config["price"]
            volatility = 0.008
            
            drift = 0.0
            if price > config["max"] * 0.85:
                drift = -0.004
            elif price < config["min"] * 1.15:
                drift = 0.004
            
            shock = random.gauss(drift, volatility)
            new_price = price * math.exp(shock)
            
            if new_price < config["min"]:
                new_price = config["min"]
            elif new_price > config["max"]:
                new_price = config["max"]
                
            config["price"] = new_price

# --- ЕЖЕДНЕВНЫЙ ЛИДЕРБОРД И ВЫДАЧА КЕЙСОВ ---
async def daily_reward_task():
    msk_tz = timezone(timedelta(hours=3))
    while True:
        now_msk = datetime.now(msk_tz)
        next_midnight = (now_msk + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        seconds_until_midnight = (next_midnight - now_msk).total_seconds()
        
        await asyncio.sleep(seconds_until_midnight)
        
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("SELECT user_id, balance, start_of_day_nw, nickname FROM users") as cursor:
                users_data = await cursor.fetchall()
            
            async with db.execute("SELECT user_id, token, amount FROM portfolio") as cursor:
                portfolio_data = await cursor.fetchall()

            portfolios = {}
            for uid, token, amount in portfolio_data:
                portfolios.setdefault(uid, {})[token] = amount

            daily_ranking = []
            new_starts = []

            for uid, balance, start_nw, nick in users_data:
                net_worth = balance
                for token, amount in portfolios.get(uid, {}).items():
                    if token in TOKENS_INFO:
                        net_worth += amount * TOKENS_INFO[token]["price"]
                
                profit = net_worth - (start_nw if start_nw is not None else 100.0)
                if uid != ADMIN_ID:
                    daily_ranking.append((uid, profit))
                
                new_starts.append((net_worth, uid))

            daily_ranking.sort(key=lambda x: x[1], reverse=True)
            if daily_ranking and daily_ranking[0][1] > 0:
                winner_id = daily_ranking[0][0]
                win_amount = float(random.choice(CASES_INFO["summer"]["prizes"]))
                
                await db.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (win_amount, winner_id))
                
                try:
                    await bot.send_message(
                        winner_id,
                        f"Вы получили приз в виде одного летнего кейса, так как заработали больше всего PTK за день. Ваш выигрыш составил {win_amount:g} PTK."
                    )
                except Exception:
                    pass

            await db.executemany("UPDATE users SET start_of_day_nw = ? WHERE user_id = ?", new_starts)
            await db.commit()

# --- БАЗА ДАННЫХ ---
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                nickname TEXT DEFAULT NULL,
                reg_date TEXT,
                balance REAL DEFAULT 100.0,
                last_bonus REAL DEFAULT NULL,
                start_of_day_nw REAL DEFAULT 100.0
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS portfolio (
                user_id INTEGER,
                token TEXT,
                amount REAL DEFAULT 0.0,
                PRIMARY KEY (user_id, token)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS used_promocodes (
                user_id INTEGER,
                promocode TEXT,
                PRIMARY KEY (user_id, promocode)
            )
        """)
        
        async with db.execute("PRAGMA table_info(users)") as cursor:
            columns = [info[1] for info in await cursor.fetchall()]
        if "nickname" not in columns:
            await db.execute("ALTER TABLE users ADD COLUMN nickname TEXT DEFAULT NULL;")
        if "reg_date" not in columns:
            await db.execute("ALTER TABLE users ADD COLUMN reg_date TEXT;")
        if "start_of_day_nw" not in columns:
            await db.execute("ALTER TABLE users ADD COLUMN start_of_day_nw REAL DEFAULT 100.0;")
            
        await db.commit()

async def init_user(user_id: int, username: str, full_name: str, db: aiosqlite.Connection):
    async with db.execute("SELECT user_id, nickname FROM users WHERE user_id = ?", (user_id,)) as cursor:
        row = await cursor.fetchone()
        
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if row is None:
        await db.execute(
            "INSERT INTO users (user_id, username, first_name, balance, start_of_day_nw, reg_date) VALUES (?, ?, ?, 100.0, 100.0, ?)", 
            (user_id, username, full_name, current_time)
        )
        for token in TOKENS_INFO:
            await db.execute("INSERT INTO portfolio (user_id, token, amount) VALUES (?, ?, 0.0)", (user_id, token))
        await db.commit()
        return None
    else:
        await db.execute("UPDATE users SET username = ?, first_name = ? WHERE user_id = ?", (username, full_name, user_id))
        await db.commit()
        return row[1]

# --- КЛАВИАТУРЫ ---
def get_main_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Курс токенов")],
            [KeyboardButton(text="🟢 Купить"), KeyboardButton(text="🔴 Продать")],
            [KeyboardButton(text="Кейсы")],
            [KeyboardButton(text="Портфель"), KeyboardButton(text="Рейтинг")],
            [KeyboardButton(text="Донат"), KeyboardButton(text="Поддержка")],
            [KeyboardButton(text="Бонус"), KeyboardButton(text="Промокод")]
        ],
        resize_keyboard=True
    )

def get_tokens_inline_keyboard(action: str):
    buttons = []
    for key, data in TOKENS_INFO.items():
        buttons.append([InlineKeyboardButton(text=data["name"], callback_data=f"{action}_{key}")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_cases_inline_keyboard():
    buttons = []
    for key, data in CASES_INFO.items():
        buttons.append([InlineKeyboardButton(text=f"{data['name']} • {int(data['price'])} PTK", callback_data=f"case_select_{key}")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_case_confirm_keyboard(case_key: str):
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Да", callback_data=f"case_yes_{case_key}"), InlineKeyboardButton(text="Нет", callback_data="case_no")]]
    )

def is_valid_amount(text: str):
    try:
        val = float(text)
        if val >= 0.01 and round(val, 2) == val: return val
        return None
    except ValueError: return None

# --- ИНИЦИАЛИЗАЦИЯ БОТА ---
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
dp.message.middleware(AntiSpamMiddleware())
dp.callback_query.middleware(AntiSpamMiddleware())

# --- КОМАНДЫ СТАРТА И ПОМОЩИ ---
@dp.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    async with aiosqlite.connect(DB_PATH) as db:
        nickname = await init_user(message.from_user.id, message.from_user.username, message.from_user.full_name, db)
        
    await message.answer(
        "Приветсвую, начинающий трейдер! Это игровой бот \"Биржа ПЯТАЧОК\", основанный для базового погружения в огромный мир трейдинга. Удачи в начинаниях! Лови приветственный промокод BATEK", 
        reply_markup=get_main_keyboard()
    )
    
    if not nickname:
        await message.answer("Введите никнейм:")
        await state.set_state(TradeState.waiting_for_nickname)

@dp.message(StateFilter(TradeState.waiting_for_nickname))
async def process_set_nickname(message: Message, state: FSMContext):
    nick = message.text.strip() if message.text else ""
    
    if not nick:
        await message.answer("Никнейм не может быть пустым. Введите никнейм:")
        return
        
    if not re.match(r"^[A-Za-zА-Яа-яЁё0-9 ]+$", nick):
        await message.answer("Никнейм может быть только на русском или английском. Введите никнейм:")
        return
        
    if contains_bad_words(nick):
        await message.answer("В никнейме присутствуют запрещенные слова. Введите другой никнейм:")
        return
        
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET nickname = ? WHERE user_id = ?", (nick, message.from_user.id))
        await db.commit()
        
    await state.clear()
    await message.answer(f"✅ Никнейм успешно установлен: {nick}")

@dp.message(Command("help"))
async def cmd_help(message: Message):
    text = (
        "<b>Команды бота</b>\n"
        "/profile - Просмотр вашего игрового профиля.\n"
        "/pay {id ТГ} {сумма} - перевести PTK пользователю (не более 10.000, раз в 5 секунд).\n"
        "/help - данное меню."
    )
    await message.answer(text, parse_mode="HTML")

# --- ОБНОВЛЕННАЯ СИСТЕМА ПРОФИЛЯ С РЕЙТИНГОМ И ГОСУСЛУГАМИ ---
@dp.message(Command("profile"))
async def cmd_profile(message: Message):
    user_id = message.from_user.id
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT nickname, reg_date, balance FROM users WHERE user_id = ?", (user_id,)) as cursor:
            user_row = await cursor.fetchone()
        async with db.execute("SELECT token, amount FROM portfolio WHERE user_id = ?", (user_id,)) as cursor:
            portfolio_rows = await cursor.fetchall()
            
    if not user_row:
        await message.answer("Вы не зарегистрированы. Напишите /start")
        return
        
    nickname, reg_date_str, balance = user_row
    if not nickname:
        nickname = "Не установлен"
        
    days = 0
    if reg_date_str:
        try:
            reg_date = datetime.strptime(reg_date_str, "%Y-%m-%d %H:%M:%S")
            days = (datetime.now() - reg_date).days
        except Exception: pass

    # Считаем общий капитал трейдера (баланс + активы)
    total_ptk = balance
    for token, amount in portfolio_rows:
        if token in TOKENS_INFO:
            total_ptk += amount * TOKENS_INFO[token]["price"]
            
    # Определяем уровень рейтинга
    if total_ptk >= 1000000:
        rating = "5+"
    elif total_ptk >= 500000:
        rating = "5"
    elif total_ptk >= 250000:
        rating = "4"
    elif total_ptk >= 100000:
        rating = "3"
    elif total_ptk >= 50000:
        rating = "2"
    elif total_ptk >= 15000:
        rating = "1"
    else:
        rating = "0"
            
    profile_text = (
        f"Ваш профиль:\n"
        f"Имя - {nickname}\n"
        f"Рейтинг трейдера - {rating} ⭐\n"
        f"ID - {user_id}\n"
        f"Дни с Пятачком - {days}\n"
        f"Баланс в PTK - {total_ptk:.2f}\n\n"
        f"Статус аккаунта - не верифицирован\n"
        f"Госуслуги - не привязано"
    )
    
    markup = InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(
                text="Верифицировать аккаунт - привязать госуслуги", 
                url="https://esia.gosuslugi.ru/login/"
            )
        ]]
    )
    
    await message.answer(profile_text, reply_markup=markup)

# --- АДМИНИСТРАТИВНЫЕ КОМАНДЫ ---
@dp.message(Command("all"))
async def admin_broadcast_all(message: Message):
    if message.from_user.id != ADMIN_ID: return
    
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer("❌ Использование: `/all {текст рассылки}`", parse_mode="Markdown")
        return
        
    broadcast_text = args[1]
    
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id FROM users") as cursor:
            rows = await cursor.fetchall()
            
    if not rows:
        await message.answer("В базе данных нет пользователей.")
        return
        
    sent_count = 0
    for row in rows:
        user_id = row[0]
        try:
            await message.bot.send_message(chat_id=user_id, text=broadcast_text)
            sent_count += 1
            await asyncio.sleep(0.05)  # Защита от флуда ТГ
        except Exception:
            continue
            
    await message.answer(f"📢 Рассылка окончена. Успешно доставлено: {sent_count} из {len(rows)} пользователям.")

@dp.message(Command("nick"))
async def admin_change_nick(message: Message):
    if message.from_user.id != ADMIN_ID: return
    
    args = message.text.split(maxsplit=2)
    if len(args) < 3:
        await message.answer("❌ Пример использования: `/nick 1515703037 firidi`", parse_mode="Markdown")
        return
        
    try:
        target_id = int(args[1])
        new_nick = args[2].strip()
    except ValueError:
        await message.answer("Неверный ID")
        return
        
    # КРИТИЧЕСКАЯ ПРОВЕРКА: Даже ADMIN_ID не может ставить запрещенные ники
    if contains_bad_words(new_nick):
        await message.answer("❌ Ошибка! Данный никнейм содержит запрещенные слова. Даже администратор не может его установить.")
        return
        
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET nickname = ? WHERE user_id = ?", (new_nick, target_id))
        await db.commit()
        
    await message.answer(f"✅ Никнейм для ID {target_id} изменен на {new_nick}")

@dp.message(Command("give"))
async def admin_give(message: Message):
    if message.from_user.id != ADMIN_ID: return
    args = message.text.split()
    if len(args) != 3: return
    try:
        target_id, amount = int(args[1]), float(args[2])
    except ValueError: return
    
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amount, target_id))
        await db.commit()
    await message.answer(f"✅ Успешно выдано {amount:g} ПЯТАКОВ.")

@dp.message(Command("take"))
async def admin_take(message: Message):
    if message.from_user.id != ADMIN_ID: return
    args = message.text.split()
    if len(args) != 3: return
    try:
        target_id, amount = int(args[1]), float(args[2])
    except ValueError: return

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT balance FROM users WHERE user_id = ?", (target_id,)) as cursor:
            row = await cursor.fetchone()
        if not row: return
        new_balance = max(0.0, row[0] - amount)
        await db.execute("UPDATE users SET balance = ? WHERE user_id = ?", (new_balance, target_id))
        await db.commit()
    await message.answer(f"✅ Успешно забрано. Баланс: {new_balance:.2f}")

# --- СИСТЕМА ДЕНЕЖНЫХ ПЕРЕВОДОВ МЕЖДУ ЮЗЕРАМИ ---
@dp.message(Command("pay"))
async def cmd_pay(message: Message):
    sender_id = message.from_user.id
    now = time.time()
    
    if sender_id in PAY_COOLDOWNS and now - PAY_COOLDOWNS[sender_id] < 5:
        await message.answer(f"⏳ Подождите {5 - (now - PAY_COOLDOWNS[sender_id]):.1f} сек.")
        return

    args = message.text.split()
    if len(args) != 3:
        await message.answer("❌ Использование: `/pay {id ТГ} {сумма}`", parse_mode="Markdown")
        return

    try:
        target_id, amount = int(args[1]), float(args[2])
    except ValueError: return

    if target_id == sender_id or amount <= 0 or amount > 10000:
        await message.answer("❌ Ошибка перевода.")
        return

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT balance FROM users WHERE user_id = ?", (sender_id,)) as cursor:
            s_bal = (await cursor.fetchone())[0]
        if s_bal < amount:
            await message.answer("❌ Недостаточно средств.")
            return
        async with db.execute("SELECT user_id FROM users WHERE user_id = ?", (target_id,)) as cursor:
            if not await cursor.fetchone(): return

        await db.execute("UPDATE users SET balance = balance - ? WHERE user_id = ?", (amount, sender_id))
        await db.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amount, target_id))
        await db.commit()

    PAY_COOLDOWNS[sender_id] = now
    await message.answer(f"✅ Переведено {amount:g} PTK пользователю ID {target_id}.")

# --- ТЕКСТОВЫЕ КНОПКИ ---
@dp.message(F.text == "Курс токенов")
async def show_rates(message: Message):
    text = "📈 <b>Текущий курс токенов (в PTK):</b>\n\n"
    for data in TOKENS_INFO.values():
        text += f"<b>{data['name']}</b>: {data['price']:.2f}\n"
    await message.answer(text, parse_mode="HTML")

@dp.message(F.text == "Портфель")
async def show_portfolio(message: Message):
    user_id = message.from_user.id
    async with aiosqlite.connect(DB_PATH) as db:
        await init_user(user_id, message.from_user.username, message.from_user.full_name, db)
        async with db.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,)) as cursor:
            balance = (await cursor.fetchone())[0]
        async with db.execute("SELECT token, amount FROM portfolio WHERE user_id = ?", (user_id,)) as cursor:
            portfolio = {row[0]: row[1] for row in await cursor.fetchall()}
            
    text = f"<b>Ваш баланс:</b>\nПЯТАК - {balance:.2f}\n"
    for key, data in TOKENS_INFO.items():
        text += f"{data['name']} - {portfolio.get(key, 0.0):.2f}\n"
    await message.answer(text, parse_mode="HTML")

@dp.message(F.text == "Поддержка")
async def support(message: Message):
    await message.answer("Официальный ТГК бота - https://t.me/lavkapyatak")

@dp.message(F.text == "Бонус")
async def get_bonus(message: Message):
    user_id = message.from_user.id
    now = time.time()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT last_bonus FROM users WHERE user_id = ?", (user_id,)) as cursor:
            last = (await cursor.fetchone())[0]
        if last is None or now - last >= 86400:
            await db.execute("UPDATE users SET balance = balance + 100.0, last_bonus = ? WHERE user_id = ?", (now, user_id))
            await db.commit()
            await message.answer("🎁 Вы успешно получили ежедневный бонус: 100 ПЯТАКОВ!")
        else:
            left = 86400 - (now - last)
            await message.answer(f"⏳ Бонус доступен через {int(left//3600)} ч. {int((left%3600)//60)} мин.")

@dp.message(F.text == "Рейтинг")
async def show_rating(message: Message):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id, balance, start_of_day_nw, nickname, first_name FROM users") as cursor:
            users_data = await cursor.fetchall()
        async with db.execute("SELECT user_id, token, amount FROM portfolio") as cursor:
            portfolio_data = await cursor.fetchall()

    portfolios = {}
    for uid, token, amount in portfolio_data:
        portfolios.setdefault(uid, {})[token] = amount

    global_ranking = []
    daily_ranking = []

    for uid, balance, start_nw, nick, f_name in users_data:
        net_worth = balance
        for token, amount in portfolios.get(uid, {}).items():
            if token in TOKENS_INFO:
                net_worth += amount * TOKENS_INFO[token]["price"]
        
        display_name = html.escape(nick if nick else (f_name if f_name else f"ID {uid}"))
        global_ranking.append((display_name, net_worth))
        
        if uid != ADMIN_ID:
            profit = net_worth - (start_nw if start_nw is not None else 100.0)
            daily_ranking.append((display_name, profit))

    global_ranking.sort(key=lambda x: x[1], reverse=True)
    daily_ranking.sort(key=lambda x: x[1], reverse=True)

    text_daily = "🔥 <b>Ежедневный ТОП-3 по профиту:</b>\n\n"
    for i, (name, profit) in enumerate(daily_ranking[:3], start=1):
        text_daily += f"{i}. {name} — { '+' if profit >= 0 else ''}{profit:.2f} PTK\n"
    await message.answer(text_daily, parse_mode="HTML")

    text_global = "🏆 <b>Общий ТОП-10 Игроков:</b>\n\n"
    for i, (name, nw) in enumerate(global_ranking[:10], start=1):
        text_global += f"{i}. {name} — {nw:.2f} PTK\n"
    await message.answer(text_global, parse_mode="HTML")

# --- ПРОМОКОДЫ ---
@dp.message(F.text == "Промокод")
async def promo_start(message: Message, state: FSMContext):
    await message.answer("Введите промокод:")
    await state.set_state(TradeState.waiting_for_promocode)

@dp.message(TradeState.waiting_for_promocode)
async def process_promocode(message: Message, state: FSMContext):
    code = message.text.strip().upper() if message.text else ""
    user_id = message.from_user.id
    if code not in ["BATEK", "VIKTOROWWWICH", "MITYA_IZ_SELA"]:
        await message.answer("❌ Неверный промокод.")
        await state.clear()
        return

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT 1 FROM used_promocodes WHERE user_id = ? AND promocode = ?", (user_id, code)) as cursor:
            if await cursor.fetchone():
                await message.answer("❌ Промокод уже использован.")
                await state.clear()
                return

        if code == "BATEK":
            await db.execute("UPDATE users SET balance = balance + 1000 WHERE user_id = ?", (user_id,))
            await message.answer("✅ Получено 1000 ПЯТАКОВ.")
        elif code == "VIKTOROWWWICH":
            await db.execute("UPDATE users SET balance = balance + 50000 WHERE user_id = ?", (user_id,))
            await message.answer("✅ Получено 50000 ПЯТАКОВ.")
        elif code == "MITYA_IZ_SELA":
            win = float(random.choice(CASES_INFO["summer"]["prizes"]))
            await db.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (win, user_id))
            await message.answer(f"✅ Бесплатный летний кейс!\n🎉 Выигрыш: {win:g} PTK")

        await db.execute("INSERT INTO used_promocodes (user_id, promocode) VALUES (?, ?)", (user_id, code))
        await db.commit()
    await state.clear()

# --- КУПЛЯ / ПРОДАЖА ТОКЕНОВ ---
@dp.message(F.text == "🟢 Купить")
async def buy_start(message: Message):
    await message.answer("Выберите токен для покупки:", reply_markup=get_tokens_inline_keyboard("buy"))

@dp.message(F.text == "🔴 Продать")
async def sell_start(message: Message):
    await message.answer("Выберите токен для продажи:", reply_markup=get_tokens_inline_keyboard("sell"))

@dp.callback_query(F.data.startswith("buy_"))
async def buy_token_selected(callback: CallbackQuery, state: FSMContext):
    token = callback.data.split("_")[1]
    await state.update_data(selected_token=token)
    await state.set_state(TradeState.waiting_for_buy_amount)
    await callback.message.answer(f"Выбран: {TOKENS_INFO[token]['name']}.\nВведите количество:")
    await callback.answer()

@dp.callback_query(F.data.startswith("sell_"))
async def sell_token_selected(callback: CallbackQuery, state: FSMContext):
    token = callback.data.split("_")[1]
    await state.update_data(selected_token=token)
    await state.set_state(TradeState.waiting_for_sell_amount)
    await callback.message.answer(f"Выбран: {TOKENS_INFO[token]['name']}.\nВведите количество:")
    await callback.answer()

@dp.message(TradeState.waiting_for_buy_amount)
async def process_buy_amount(message: Message, state: FSMContext):
    amount = is_valid_amount(message.text)
    if not amount:
        await message.answer("❌ Введите число >= 0.01.")
        return
    data = await state.get_data()
    t_key = data['selected_token']
    cost = amount * TOKENS_INFO[t_key]["price"]
    
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT balance FROM users WHERE user_id = ?", (message.from_user.id,)) as cursor:
            bal = (await cursor.fetchone())[0]
        if bal >= cost:
            await db.execute("UPDATE users SET balance = balance - ? WHERE user_id = ?", (cost, message.from_user.id))
            await db.execute("UPDATE portfolio SET amount = amount + ? WHERE user_id = ? AND token = ?", (amount, message.from_user.id, t_key))
            await db.commit()
            await message.answer(f"✅ Куплено {amount} {TOKENS_INFO[t_key]['name']} за {cost:.2f} PTK.")
        else:
            await message.answer("❌ Недостаточно средств.")
    await state.clear()

@dp.message(TradeState.waiting_for_sell_amount)
async def process_sell_amount(message: Message, state: FSMContext):
    amount = is_valid_amount(message.text)
    if not amount:
        await message.answer("❌ Введите число >= 0.01.")
        return
    data = await state.get_data()
    t_key = data['selected_token']
    
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT amount FROM portfolio WHERE user_id = ? AND token = ?", (message.from_user.id, t_key)) as cursor:
            p_amt = (await cursor.fetchone())[0]
        if p_amt >= amount:
            rev = amount * TOKENS_INFO[t_key]["price"]
            await db.execute("UPDATE portfolio SET amount = amount - ? WHERE user_id = ? AND token = ?", (amount, message.from_user.id, t_key))
            await db.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (rev, message.from_user.id))
            await db.commit()
            await message.answer(f"✅ Продано {amount} {TOKENS_INFO[t_key]['name']} за {rev:.2f} PTK.")
        else:
            await message.answer("❌ Недостаточно токенов.")
    await state.clear()

# --- СИСТЕМА КЕЙСОВ С АЛГОРИТМОМ СБАЛАНСИРОВАННОГО ВИНРЕЙТА ---
@dp.message(F.text == "Кейсы")
async def show_cases(message: Message):
    await message.answer("Выберите кейс:", reply_markup=get_cases_inline_keyboard())

@dp.callback_query(F.data.startswith("case_select_"))
async def process_case_selection(callback: CallbackQuery):
    case_key = callback.data.replace("case_select_", "")
    await callback.message.edit_text(f"Приобрести кейс *{CASES_INFO[case_key]['name']}* за *{int(CASES_INFO[case_key]['price'])}* PTK?", parse_mode="Markdown", reply_markup=get_case_confirm_keyboard(case_key))
    await callback.answer()

@dp.callback_query(F.data == "case_no")
async def cancel_case(callback: CallbackQuery):
    await callback.message.edit_text("Покупка отменена.")
    await callback.answer()

@dp.callback_query(F.data.startswith("case_yes_"))
async def confirm_case_purchase(callback: CallbackQuery):
    case_key = callback.data.replace("case_yes_", "")
    price = CASES_INFO[case_key]["price"]
    user_id = callback.from_user.id
    
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,)) as cursor:
            balance = (await cursor.fetchone())[0]
        if balance < price:
            await callback.message.edit_text("❌ Недостаточно средств.")
            await callback.answer()
            return

        if user_id not in USER_CASE_MODS:
            USER_CASE_MODS[user_id] = {'more_bonus': 0.0, 'less_bonus': 0.0}
        mods = USER_CASE_MODS[user_id]

        is_fake = random.random() < 0.01
        final_case_key = case_key
        fake_msg = ""
        if is_fake:
            final_case_key = random.choice([k for k in CASES_INFO.keys() if k != case_key])
            fake_msg = f"Вот попадос, вам подсунули фуфло и продали *{CASES_INFO[final_case_key]['name']}* кейс!\n\n"
            
        prizes = CASES_INFO[final_case_key]["prizes"]
        less_i, more_i, eq_i = [p for p in prizes if p < price], [p for p in prizes if p > price], [p for p in prizes if p == price]
        
        tot = len(prizes)
        w_less = ((len(less_i) / tot) * 100 + mods['less_bonus']) if less_i else 0
        w_more = ((len(more_i) / tot) * 100 + mods['more_bonus']) if more_i else 0
        w_eq = ((len(eq_i) / tot) * 100) if eq_i else 0
        
        cat = random.choices(["less", "more", "eq"], weights=[w_less, w_more, w_eq])[0]
        if cat == "less":
            win = float(random.choice(less_i))
            mods['less_bonus'], mods['more_bonus'] = 0.0, mods['more_bonus'] + 1.0
        elif cat == "more":
            win = float(random.choice(more_i))
            mods['more_bonus'], mods['less_bonus'] = 0.0, mods['less_bonus'] + 5.0
        else:
            win = float(random.choice(eq_i)) if eq_i else price
            mods['more_bonus'], mods['less_bonus'] = 0.0, 0.0
            
        await db.execute("UPDATE users SET balance = balance - ? + ? WHERE user_id = ?", (price, win, user_id))
        await db.commit()
        
    await callback.message.edit_text(f"{fake_msg}🎉 Выигрыш: *{win:g}* PTK", parse_mode="Markdown")
    await callback.answer()

# --- ДОНАТЫ ЧЕРЕЗ TELEGRAM STARS ---
@dp.message(F.text == "Донат")
async def donat_start(message: Message, state: FSMContext):
    await message.answer("Введите количество Stars (от 10, кратно 5):")
    await state.set_state(TradeState.waiting_for_donate_amount)

@dp.message(TradeState.waiting_for_donate_amount)
async def process_donate_amount(message: Message, state: FSMContext):
    try:
        stars = int(message.text)
        if stars < 10 or stars % 5 != 0: raise ValueError
    except ValueError:
        await message.answer("❌ Ошибка формата.")
        return
    await state.clear()
    await bot.send_invoice(
        chat_id=message.chat.id, title="Донат", description=f"Покупка {stars * 10} ПЯТАКОВ",
        payload=f"donate_{stars}", currency="XTR", prices=[LabeledPrice(label="Пополнение", amount=stars)]
    )

@dp.pre_checkout_query()
async def pre_checkout_handler(pre_checkout_query: PreCheckoutQuery):
    await pre_checkout_query.answer(ok=True)

@dp.message(F.successful_payment)
async def successful_payment_handler(message: Message):
    ptk = message.successful_payment.total_amount * 10
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (ptk, message.from_user.id))
        await db.commit()
    await message.answer(f"🎉 Начислено {ptk} ПЯТАКОВ.")

# --- ЗАПУСК БОТА ---
async def main():
    await init_db()
    asyncio.create_task(market_updater())
    asyncio.create_task(daily_reward_task())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())