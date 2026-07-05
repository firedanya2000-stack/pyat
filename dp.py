import asyncio
import random
import time
import html
import aiosqlite
from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message, ReplyKeyboardMarkup, KeyboardButton, 
    InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery,
    LabeledPrice, PreCheckoutQuery
)
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

# --- НАСТРОЙКИ ---
BOT_TOKEN = "8636079893:AAH92Dl2QO8Pc0lLeqE0jbis_ACdB3p5IMM"
DB_PATH = "bot_db.sqlite"
ADMIN_ID = 1515703037 # Твой ID администратора

# Словари для КД и модификаторов удачи
PAY_COOLDOWNS = {}
USER_CASE_MODS = {} # {user_id: {'more_bonus': 0.0, 'less_bonus': 0.0}}

# Структура токенов с начальными ценами и лимитами
TOKENS_INFO = {
    "btc": {"name": "Bitcoin", "price": 50000.0, "init_price": 50000.0, "min": 30000.0, "max": 150000.0},
    "eth": {"name": "Ethereum", "price": 1500.0, "init_price": 1500.0, "min": 750.0, "max": 2500.0},
    "sol": {"name": "Solana", "price": 75.0, "init_price": 75.0, "min": 50.0, "max": 150.0},
    "ltc": {"name": "Litecoin", "price": 50.0, "init_price": 50.0, "min": 25.0, "max": 75.0},
    "doge": {"name": "Dogecoin", "price": 0.1, "init_price": 0.1, "min": 0.1, "max": 0.5},
    "gold": {"name": "Gold", "price": 4000.0, "init_price": 4000.0, "min": 3500.0, "max": 4500.0},
    "silver": {"name": "Silver", "price": 100.0, "init_price": 100.0, "min": 75.0, "max": 125.0},
}

# Тренды рынка для динамической вероятности
TOKEN_TRENDS = {key: {'up_bonus': 0.0, 'down_bonus': 0.0} for key in TOKENS_INFO}

# --- КЕЙСЫ (Фиксированные призы) ---
CASES_INFO = {
    "wood": {"name": "Деревянный", "price": 1000.0, "prizes": [100, 250, 500, 750, 1000, 1250, 1500, 1750, 2500]},
    "bronze": {"name": "Бронзовый", "price": 5000.0, "prizes": [250, 500, 1000, 1750, 2500, 5000, 5750, 6250, 7000, 7500]},
    "silver": {"name": "Серебряный", "price": 10000.0, "prizes": [500, 1000, 1750, 2500, 5000, 5500, 6250, 7500, 10000, 11250, 12500, 13750, 15000]},
    "gold": {"name": "Золотой", "price": 25000.0, "prizes": [5000, 6250, 7500, 10000, 12500, 15000, 16250, 17500, 20000, 25000, 27500, 30000, 32500, 35000, 37500, 40000]},
    "summer": {"name": "Летний", "price": 50000.0, "prizes": [7500, 10000, 15000, 17500, 20000, 25000, 30000, 37500, 45000, 50000, 57500, 62500, 67500, 70000, 75000]},
}

# --- СОСТОЯНИЯ FSM ---
class TradeState(StatesGroup):
    waiting_for_buy_amount = State()
    waiting_for_sell_amount = State()
    waiting_for_donate_amount = State()
    waiting_for_promocode = State()

# --- ФОНОВАЯ ЗАДАЧА ДЛЯ КУРСОВ ---
async def market_updater():
    """Каждые 15 секунд меняет цены на токены с учетом шансов тренда (от 0.975 до 1.025)"""
    while True:
        await asyncio.sleep(15)
        for token, config in TOKENS_INFO.items():
            trend = TOKEN_TRENDS[token]
            base_up_chance = 50.0 + trend['up_bonus'] - trend['down_bonus']
            roll = random.uniform(0, 100)
            
            if roll <= base_up_chance: 
                # Цена растет
                change = random.uniform(1.000, 1.025)
                trend['down_bonus'] += 0.75
                trend['up_bonus'] = 0.0
            else: 
                # Цена падает
                change = random.uniform(0.975, 1.000)
                trend['up_bonus'] += 1.0
                trend['down_bonus'] = 0.0
                
            new_price = config["price"] * change
            
            # Проверка лимитов
            if new_price < config["min"] or new_price > config["max"]:
                config["price"] = config["init_price"]
            else:
                config["price"] = new_price

# --- РАБОТА С БАЗОЙ ДАННЫХ (AIOSQLITE) ---
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                balance REAL DEFAULT 100.0,
                last_bonus REAL DEFAULT NULL
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
            columns_info = await cursor.fetchall()
            existing_columns = [info[1] for info in columns_info]
            
        if "username" not in existing_columns:
            await db.execute("ALTER TABLE users ADD COLUMN username TEXT;")
        if "first_name" not in existing_columns:
            await db.execute("ALTER TABLE users ADD COLUMN first_name TEXT;")
            
        await db.commit()

async def init_user(user_id: int, username: str, full_name: str, db: aiosqlite.Connection):
    async with db.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,)) as cursor:
        if await cursor.fetchone() is None:
            await db.execute(
                "INSERT INTO users (user_id, username, first_name, balance) VALUES (?, ?, ?, 100.0)", 
                (user_id, username, full_name)
            )
            for token in TOKENS_INFO:
                await db.execute("INSERT INTO portfolio (user_id, token, amount) VALUES (?, ?, 0.0)", (user_id, token))
            await db.commit()
        else:
            await db.execute(
                "UPDATE users SET username = ?, first_name = ? WHERE user_id = ?", 
                (username, full_name, user_id)
            )
            await db.commit()

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---
def get_main_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Курс токенов")],
            [KeyboardButton(text="Купить"), KeyboardButton(text="Продать")],
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
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Да", callback_data=f"case_yes_{case_key}"),
                InlineKeyboardButton(text="Нет", callback_data="case_no")
            ]
        ]
    )

def is_valid_amount(text: str):
    try:
        val = float(text)
        if val >= 0.01 and round(val, 2) == val:
            return val
        return None
    except ValueError:
        return None

# --- ИНИЦИАЛИЗАЦИЯ БОТА ---
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# --- ОБЩИЕ КОМАНДЫ ---
@dp.message(Command("help"))
async def cmd_help(message: Message):
    text = (
        "<b>Команды бота</b>\n"
        "/pay {id ТГ} {сумма} - перевести PTK пользователю (не более 10.000, раз в 5 секунд).\n"
        "/help - данное меню."
    )
    await message.answer(text, parse_mode="HTML")

@dp.message(Command("pay"))
async def cmd_pay(message: Message):
    sender_id = message.from_user.id
    now = time.time()
    
    if sender_id in PAY_COOLDOWNS:
        time_passed = now - PAY_COOLDOWNS[sender_id]
        if time_passed < 5:
            time_left = 5 - time_passed
            await message.answer(f"⏳ Подождите {time_left:.1f} сек. перед следующим переводом.")
            return

    args = message.text.split()
    if len(args) != 3:
        await message.answer("❌ Использование: `/pay {id ТГ} {сумма}`", parse_mode="Markdown")
        return

    try:
        target_id = int(args[1])
        amount = float(args[2])
    except ValueError:
        await message.answer("❌ Ошибка: ID и сумма должны быть числами.")
        return

    if target_id == sender_id:
        await message.answer("❌ Вы не можете перевести PTK самому себе.")
        return

    if amount <= 0 or amount > 10000:
        await message.answer("❌ Ошибка: Сумма перевода за раз должна быть больше 0 и не более 10 000 PTK.")
        return

    async with aiosqlite.connect(DB_PATH) as db:
        await init_user(sender_id, message.from_user.username, message.from_user.full_name, db)
        
        async with db.execute("SELECT balance FROM users WHERE user_id = ?", (sender_id,)) as cursor:
            sender_balance = (await cursor.fetchone())[0]
            
        if sender_balance < amount:
            await message.answer(f"❌ Недостаточно средств. Ваш баланс: {sender_balance:.2f} PTK.")
            return

        async with db.execute("SELECT user_id FROM users WHERE user_id = ?", (target_id,)) as cursor:
            if await cursor.fetchone() is None:
                await message.answer("❌ Пользователь с таким ID не найден в базе данных бота.")
                return

        await db.execute("UPDATE users SET balance = balance - ? WHERE user_id = ?", (amount, sender_id))
        await db.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amount, target_id))
        await db.commit()

    PAY_COOLDOWNS[sender_id] = now
    await message.answer(f"✅ Вы успешно перевели {amount:g} PTK пользователю ID {target_id}.")

# --- ХЭНДЛЕРЫ АДМИНИСТРАТОРА ---
@dp.message(Command("all"))
async def admin_all_users(message: Message):
    if message.from_user.id != ADMIN_ID:
        return

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id, first_name, balance FROM users") as cursor:
            users_data = await cursor.fetchall()

    if not users_data:
        await message.answer("Пользователей пока нет.")
        return

    text = "👥 <b>Список всех пользователей:</b>\n\n"
    for uid, full_name, balance in users_data:
        safe_name = html.escape(full_name) if full_name else "Без имени"
        line = f"{safe_name} ({uid}) - {balance:.2f} PTK\n"
        
        if len(text) + len(line) > 4000:
            await message.answer(text, parse_mode="HTML")
            text = ""
        
        text += line

    if text:
        await message.answer(text, parse_mode="HTML")

@dp.message(Command("give"))
async def admin_give(message: Message):
    if message.from_user.id != ADMIN_ID:
        return

    args = message.text.split()
    if len(args) != 3:
        await message.answer("❌ Использование: `/give {id ТГ} {Кол-во}`", parse_mode="Markdown")
        return

    try:
        target_id = int(args[1])
        amount = float(args[2])
    except ValueError:
        await message.answer("❌ Ошибка: ID и количество должны быть числами.")
        return

    if amount <= 0 or amount > 100000:
        await message.answer("❌ Ошибка: Количество должно быть больше 0 и не превышать 100 000 за раз.")
        return

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT balance FROM users WHERE user_id = ?", (target_id,)) as cursor:
            if await cursor.fetchone() is None:
                await message.answer("❌ Пользователь с таким ID не найден в базе данных.")
                return

        await db.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amount, target_id))
        await db.commit()
        await message.answer(f"✅ Успешно выдано {amount:g} ПЯТАКОВ пользователю ID {target_id}.")

@dp.message(Command("take"))
async def admin_take(message: Message):
    if message.from_user.id != ADMIN_ID:
        return

    args = message.text.split()
    if len(args) != 3:
        await message.answer("❌ Использование: `/take {id ТГ} {Кол-во}`", parse_mode="Markdown")
        return

    try:
        target_id = int(args[1])
        amount = float(args[2])
    except ValueError:
        await message.answer("❌ Ошибка: ID и количество должны быть числами.")
        return

    if amount <= 0 or amount > 100000:
        await message.answer("❌ Ошибка: Количество должно быть больше 0 и не превышать 100 000 за раз.")
        return

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT balance FROM users WHERE user_id = ?", (target_id,)) as cursor:
            row = await cursor.fetchone()
            if row is None:
                await message.answer("❌ Пользователь с таким ID не найден в базе данных.")
                return
            
            current_balance = row[0]

        new_balance = max(0.0, current_balance - amount)
        
        await db.execute("UPDATE users SET balance = ? WHERE user_id = ?", (new_balance, target_id))
        await db.commit()
        await message.answer(f"✅ Успешно забрано ПЯТАКОВ у пользователя ID {target_id}.\nНовый баланс: {new_balance:.2f} PTK.")

# --- БАЗОВЫЕ ХЭНДЛЕРЫ ТЕКСТА ---
@dp.message(Command("start"))
async def cmd_start(message: Message):
    async with aiosqlite.connect(DB_PATH) as db:
        await init_user(message.from_user.id, message.from_user.username, message.from_user.full_name, db)
    await message.answer("Привет!", reply_markup=get_main_keyboard())

@dp.message(F.text == "Курс токенов")
async def show_rates(message: Message):
    text = "📈 <b>Текущий курс токенов (в PTK):</b>\n\n"
    for data in TOKENS_INFO.values():
        text += f"<b>{data['name']}</b>: {data['price']:.2f}\n"
    await message.answer(text, parse_mode="HTML")

@dp.message(F.text == "Портфель")
async def show_portfolio(message: Message):
    user_id = message.from_user.id
    text = "<b>Ваш баланс:</b>\n"
    
    async with aiosqlite.connect(DB_PATH) as db:
        await init_user(user_id, message.from_user.username, message.from_user.full_name, db)
        async with db.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,)) as cursor:
            balance = (await cursor.fetchone())[0]
        text += f"ПЯТАК - {balance:.2f}\n"
        
        async with db.execute("SELECT token, amount FROM portfolio WHERE user_id = ?", (user_id,)) as cursor:
            portfolio = {row[0]: row[1] for row in await cursor.fetchall()}
            
        for key, data in TOKENS_INFO.items():
            amount = portfolio.get(key, 0.0)
            text += f"{data['name']} - {amount:.2f}\n"
            
    await message.answer(text, parse_mode="HTML")

@dp.message(F.text == "Поддержка")
async def support(message: Message):
    await message.answer("Официальный ТГК бота - https://t.me/lavkapyatak")

@dp.message(F.text == "Бонус")
async def get_bonus(message: Message):
    user_id = message.from_user.id
    now = time.time()

    async with aiosqlite.connect(DB_PATH) as db:
        await init_user(user_id, message.from_user.username, message.from_user.full_name, db)
        async with db.execute("SELECT last_bonus FROM users WHERE user_id = ?", (user_id,)) as cursor:
            last_bonus = (await cursor.fetchone())[0]

        if last_bonus is None or now - last_bonus >= 86400:
            await db.execute("UPDATE users SET balance = balance + 100.0, last_bonus = ? WHERE user_id = ?", (now, user_id))
            await db.commit()
            await message.answer("🎁 Вы успешно получили ежедневный бонус: 100 ПЯТАКОВ!")
        else:
            time_left = 86400 - (now - last_bonus)
            hours = int(time_left // 3600)
            minutes = int((time_left % 3600) // 60)
            await message.answer(f"⏳ Бонус уже был получен. Следующий бонус будет доступен через {hours} ч. {minutes} мин.")

@dp.message(F.text == "Рейтинг")
async def show_rating(message: Message):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id, balance, username, first_name FROM users") as cursor:
            users_data = await cursor.fetchall()
        async with db.execute("SELECT user_id, token, amount FROM portfolio") as cursor:
            portfolio_data = await cursor.fetchall()

    portfolios = {}
    for uid, token, amount in portfolio_data:
        if uid not in portfolios:
            portfolios[uid] = {}
        portfolios[uid][token] = amount

    ranking = []
    for uid, balance, username, full_name in users_data:
        net_worth = balance
        user_port = portfolios.get(uid, {})
        for token, amount in user_port.items():
            if token in TOKENS_INFO:
                net_worth += amount * TOKENS_INFO[token]["price"]
        
        if full_name:
            display_name = full_name
        else:
            display_name = f"ID {uid}"
            
        display_name = html.escape(display_name)
        ranking.append((display_name, net_worth))

    ranking.sort(key=lambda x: x[1], reverse=True)
    text = "🏆 <b>ТОП-10 Игроков:</b>\n\n"
    for i, (name, net_worth) in enumerate(ranking[:10], start=1):
        text += f"{i}. {name} — {net_worth:.2f} PTK\n"
    
    if not ranking:
        text += "Рейтинг пока пуст."

    await message.answer(text, parse_mode="HTML")

# --- ПРОМОКОДЫ ---
@dp.message(F.text == "Промокод")
async def promo_start(message: Message, state: FSMContext):
    await message.answer("Введите промокод:")
    await state.set_state(TradeState.waiting_for_promocode)

@dp.message(TradeState.waiting_for_promocode)
async def process_promocode(message: Message, state: FSMContext):
    code = message.text.strip().upper()
    user_id = message.from_user.id
    
    valid_codes = ["BATEK", "VIKTOROWWWICH", "MITYA_IZ_SELA"]
    if code not in valid_codes:
        await message.answer("❌ Неверный или несуществующий промокод.")
        await state.clear()
        return

    async with aiosqlite.connect(DB_PATH) as db:
        await init_user(user_id, message.from_user.username, message.from_user.full_name, db)
        
        async with db.execute("SELECT 1 FROM used_promocodes WHERE user_id = ? AND promocode = ?", (user_id, code)) as cursor:
            if await cursor.fetchone() is not None:
                await message.answer("❌ Вы уже использовали этот промокод.")
                await state.clear()
                return

        if code == "BATEK":
            reward = 1000.0
            await db.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (reward, user_id))
            await db.execute("INSERT INTO used_promocodes (user_id, promocode) VALUES (?, ?)", (user_id, code))
            await db.commit()
            await message.answer(f"✅ Промокод успешно активирован! Вы получили {int(reward)} ПЯТАКОВ.")
            
        elif code == "VIKTOROWWWICH":
            reward = 50000.0
            await db.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (reward, user_id))
            await db.execute("INSERT INTO used_promocodes (user_id, promocode) VALUES (?, ?)", (user_id, code))
            await db.commit()
            await message.answer(f"✅ Промокод успешно активирован! Вы получили {int(reward)} ПЯТАКОВ.")
            
        elif code == "MITYA_IZ_SELA":
            case_data = CASES_INFO["summer"]
            win_amount = float(random.choice(case_data["prizes"]))
            
            await db.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (win_amount, user_id))
            await db.execute("INSERT INTO used_promocodes (user_id, promocode) VALUES (?, ?)", (user_id, code))
            await db.commit()
            
            await message.answer(
                f"✅ Вы получили 1 бесплатный летний кейс.\n🎉 Ваш выигрыш составил *{win_amount:g}* PTK", 
                parse_mode="Markdown"
            )

    await state.clear()

# --- ПОКУПКА / ПРОДАЖА ТОКЕНОВ ---
@dp.message(F.text == "Купить")
async def buy_start(message: Message):
    async with aiosqlite.connect(DB_PATH) as db:
        await init_user(message.from_user.id, message.from_user.username, message.from_user.full_name, db)
    await message.answer(
        "Выберите токен, который желаете приобрести:",
        reply_markup=get_tokens_inline_keyboard("buy")
    )

@dp.message(F.text == "Продать")
async def sell_start(message: Message):
    async with aiosqlite.connect(DB_PATH) as db:
        await init_user(message.from_user.id, message.from_user.username, message.from_user.full_name, db)
    await message.answer(
        "Выберите токен, который желаете продать:",
        reply_markup=get_tokens_inline_keyboard("sell")
    )

@dp.callback_query(F.data.startswith("buy_"))
async def buy_token_selected(callback: CallbackQuery, state: FSMContext):
    token = callback.data.split("_")[1]
    await state.update_data(selected_token=token)
    await state.set_state(TradeState.waiting_for_buy_amount)
    await callback.message.answer(f"Выбран: {TOKENS_INFO[token]['name']}.\nВведите количество, которое желаете приобрести (не менее 0.01, максимум 2 знака после запятой):")
    await callback.answer()

@dp.callback_query(F.data.startswith("sell_"))
async def sell_token_selected(callback: CallbackQuery, state: FSMContext):
    token = callback.data.split("_")[1]
    await state.update_data(selected_token=token)
    await state.set_state(TradeState.waiting_for_sell_amount)
    await callback.message.answer(f"Выбран: {TOKENS_INFO[token]['name']}.\nВведите количество, которое желаете продать (не менее 0.01, максимум 2 знака после запятой):")
    await callback.answer()

@dp.message(TradeState.waiting_for_buy_amount)
async def process_buy_amount(message: Message, state: FSMContext):
    amount = is_valid_amount(message.text)
    if not amount:
        await message.answer("❌ Неверный формат! Введите число не менее 0.01 и не выходите за разряд сотых (например, 1.50).")
        return

    data = await state.get_data()
    token_key = data['selected_token']
    price = TOKENS_INFO[token_key]["price"]
    total_cost = amount * price
    user_id = message.from_user.id

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,)) as cursor:
            balance = (await cursor.fetchone())[0]

        if balance >= total_cost:
            await db.execute("UPDATE users SET balance = balance - ? WHERE user_id = ?", (total_cost, user_id))
            await db.execute("UPDATE portfolio SET amount = amount + ? WHERE user_id = ? AND token = ?", (amount, user_id, token_key))
            await db.commit()
            await message.answer(f"✅ Успешно куплено {amount} {TOKENS_INFO[token_key]['name']} за {total_cost:.2f} PTK.")
        else:
            await message.answer(f"❌ Недостаточно средств. Для покупки нужно {total_cost:.2f} PTK, а у вас {balance:.2f} PTK.")
            
    await state.clear()

@dp.message(TradeState.waiting_for_sell_amount)
async def process_sell_amount(message: Message, state: FSMContext):
    amount = is_valid_amount(message.text)
    if not amount:
        await message.answer("❌ Неверный формат! Введите число не менее 0.01 и не выходите за разряд сотых (например, 1.50).")
        return

    data = await state.get_data()
    token_key = data['selected_token']
    user_id = message.from_user.id

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT amount FROM portfolio WHERE user_id = ? AND token = ?", (user_id, token_key)) as cursor:
            portfolio_amount = (await cursor.fetchone())[0]

        if portfolio_amount >= amount:
            price = TOKENS_INFO[token_key]["price"]
            total_revenue = amount * price
            await db.execute("UPDATE portfolio SET amount = amount - ? WHERE user_id = ? AND token = ?", (amount, user_id, token_key))
            await db.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (total_revenue, user_id))
            await db.commit()
            await message.answer(f"✅ Успешно продано {amount} {TOKENS_INFO[token_key]['name']} за {total_revenue:.2f} PTK.")
        else:
            await message.answer(f"❌ Недостаточно токенов для продажи. У вас есть {portfolio_amount:.2f}.")
            
    await state.clear()

# --- КЕЙСЫ (ЛОГИКА УДАЧИ И ФЕЙКОВ) ---
@dp.message(F.text == "Кейсы")
async def show_cases(message: Message):
    async with aiosqlite.connect(DB_PATH) as db:
        await init_user(message.from_user.id, message.from_user.username, message.from_user.full_name, db)
    await message.answer(
        "Выберите кейс:",
        reply_markup=get_cases_inline_keyboard()
    )

@dp.callback_query(F.data.startswith("case_select_"))
async def process_case_selection(callback: CallbackQuery):
    case_key = callback.data.replace("case_select_", "")
    case_data = CASES_INFO[case_key]
    
    text = f"Вы действительно хотите приобрести *{case_data['name']}* кейс за *{int(case_data['price'])}* PTK?"
    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=get_case_confirm_keyboard(case_key))
    await callback.answer()

@dp.callback_query(F.data == "case_no")
async def cancel_case(callback: CallbackQuery):
    await callback.message.edit_text("Покупка кейса отменена.")
    await callback.answer()

@dp.callback_query(F.data.startswith("case_yes_"))
async def confirm_case_purchase(callback: CallbackQuery):
    case_key = callback.data.replace("case_yes_", "")
    bought_case_data = CASES_INFO[case_key]
    price = bought_case_data["price"]
    user_id = callback.from_user.id
    
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,)) as cursor:
            balance = (await cursor.fetchone())[0]
            
        if balance < price:
            await callback.message.edit_text(f"❌ Недостаточно средств для покупки кейса.\n\nВаш баланс: {balance:.2f} PTK.\nСтоимость кейса: {int(price)} PTK.")
            await callback.answer()
            return

        # Инициализируем модификаторы удачи пользователя, если их нет
        if user_id not in USER_CASE_MODS:
            USER_CASE_MODS[user_id] = {'more_bonus': 0.0, 'less_bonus': 0.0}
        mods = USER_CASE_MODS[user_id]

        # 1% шанс на фейковый кейс
        is_fake = random.random() < 0.01
        final_case_key = case_key
        fake_msg = ""
        
        if is_fake:
            other_keys = [k for k in CASES_INFO.keys() if k != case_key]
            final_case_key = random.choice(other_keys)
            fake_msg = f"Вот попадос, вам подсунули фуфло и продали *{CASES_INFO[final_case_key]['name']}* кейс!\n\n"
            
        final_case_data = CASES_INFO[final_case_key]
        prizes = final_case_data["prizes"]
        
        # Разделяем призы на категории относительно потраченной цены
        less_items = [p for p in prizes if p < price]
        more_items = [p for p in prizes if p > price]
        eq_items = [p for p in prizes if p == price]
        
        tot = len(prizes)
        
        # Вычисляем шансы выпадения категорий с учетом накопленных бонусов
        w_less = ((len(less_items) / tot) * 100 + mods['less_bonus']) if less_items else 0
        w_more = ((len(more_items) / tot) * 100 + mods['more_bonus']) if more_items else 0
        w_eq = ((len(eq_items) / tot) * 100) if eq_items else 0
        
        # Защита на случай непредвиденных ошибок весов
        if w_less + w_more + w_eq <= 0:
            w_less, w_more, w_eq = (1 if less_items else 0), (1 if more_items else 0), (1 if eq_items else 0)
            
        # Выбор категории
        category = random.choices(["less", "more", "eq"], weights=[w_less, w_more, w_eq])[0]
        
        if category == "less":
            win_amount = float(random.choice(less_items))
            mods['less_bonus'] = 0.0  # Обнуляем в ее пользу
            mods['more_bonus'] += 1.0 # Увеличиваем на 1% шанс выиграть больше
        elif category == "more":
            win_amount = float(random.choice(more_items))
            mods['more_bonus'] = 0.0  # Обнуляем в ее пользу
            mods['less_bonus'] += 5.0 # Увеличиваем на 5% шанс проиграть
        else:
            win_amount = float(random.choice(eq_items)) if eq_items else price
            mods['more_bonus'] = 0.0  # При ничьей обнуляем обе стороны
            mods['less_bonus'] = 0.0
            
        # Проводим транзакцию
        await db.execute("UPDATE users SET balance = balance - ? + ? WHERE user_id = ?", (price, win_amount, user_id))
        await db.commit()
        
    final_text = f"{fake_msg}🎉 Ваш выигрыш составил *{win_amount:g}* PTK"
    await callback.message.edit_text(final_text, parse_mode="Markdown")
    await callback.answer()

# --- ДОНАТ (TELEGRAM STARS) ---
@dp.message(F.text == "Донат")
async def donat_start(message: Message, state: FSMContext):
    await message.answer("Введите количество Telegram Stars для доната (не менее 10, число должно делиться на 5 нацело):")
    await state.set_state(TradeState.waiting_for_donate_amount)

@dp.message(TradeState.waiting_for_donate_amount)
async def process_donate_amount(message: Message, state: FSMContext):
    try:
        stars = int(message.text)
        if stars < 10 or stars % 5 != 0:
            raise ValueError
    except ValueError:
        await message.answer("❌ Ошибка! Введите целое число от 10, которое делится на 5.")
        return

    await state.clear()
    
    prices = [LabeledPrice(label="Пополнение ПЯТАКОВ", amount=stars)]
    await bot.send_invoice(
        chat_id=message.chat.id,
        title="Донат",
        description=f"Покупка {stars * 10} ПЯТАКОВ",
        payload=f"donate_{stars}",
        currency="XTR",
        prices=prices
    )

@dp.pre_checkout_query()
async def pre_checkout_handler(pre_checkout_query: PreCheckoutQuery):
    await pre_checkout_query.answer(ok=True)

@dp.message(F.successful_payment)
async def successful_payment_handler(message: Message):
    stars = message.successful_payment.total_amount
    user_id = message.from_user.id
    ptk_reward = stars * 10
    
    async with aiosqlite.connect(DB_PATH) as db:
        await init_user(user_id, message.from_user.username, message.from_user.full_name, db)
        await db.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (ptk_reward, user_id))
        await db.commit()
    
    await message.answer(f"🎉 Спасибо за поддержку! Вам начислено {ptk_reward} ПЯТАКОВ.")

# --- ЗАПУСК БОТА ---
async def main():
    await init_db()
    asyncio.create_task(market_updater())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())