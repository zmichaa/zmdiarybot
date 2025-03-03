import sqlite3
import logging
import asyncio
import json
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types, Router, F
from aiogram.filters import Command, BaseFilter
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from aiogram.types import BotCommand, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from config import TOKEN, ADMIN_CHAT_ID
import aiocron

bot = Bot(token=TOKEN)
dp = Dispatcher()
router = Router()
dp.include_router(router)
logging.basicConfig(level=logging.INFO)

# Инициализация базы данных
def init_db():
    with sqlite3.connect("homework.db") as conn:
        cur = conn.cursor()
        cur.execute('''CREATE TABLE IF NOT EXISTS homework (
                        id INTEGER PRIMARY KEY,
                        user_id INTEGER,
                        date TEXT,
                        group_number TEXT,
                        class TEXT,
                        school TEXT,
                        subject TEXT,
                        task TEXT)''')
        cur.execute('''CREATE TABLE IF NOT EXISTS users (
                        user_id INTEGER PRIMARY KEY,
                        username TEXT,
                        class TEXT,
                        school TEXT,
                        group_number TEXT,
                        role TEXT DEFAULT 'viewer',
                        balance INTEGER DEFAULT 0,
                        referrer_id INTEGER DEFAULT NULL,
                        editor_request BOOLEAN DEFAULT FALSE)''')
        cur.execute('''CREATE TABLE IF NOT EXISTS schedule (
                        id INTEGER PRIMARY KEY,
                        user_id INTEGER,
                        class TEXT,
                        school TEXT,
                        schedule_json TEXT)''')
        cur.execute('''CREATE TABLE IF NOT EXISTS schools (
                        id INTEGER PRIMARY KEY,
                        name TEXT UNIQUE)''')
        conn.commit()
init_db()


# Состояния
class UserState(StatesGroup):
    waiting_for_school = State()
    waiting_for_class_number = State()
    waiting_for_class_letter = State()
    waiting_for_new_school = State()
    waiting_for_group = State()

class AdminState(StatesGroup):
    waiting_for_school_approval = State()

class AdminPanelState(StatesGroup):
    waiting_for_user_search = State()
    waiting_for_user_action = State()
    waiting_for_role_change = State()
    waiting_for_balance_change = State()

class HomeworkState(StatesGroup):
    waiting_for_date = State()
    waiting_for_subject = State()
    waiting_for_task = State()
    waiting_for_view_date = State()

class ScheduleState(StatesGroup):
    waiting_for_day = State()
    waiting_for_subject = State()


# Фильтры
@router.message(F.chat.type != "private")
async def handle_group_messages(message: types.Message):
    await message.answer("🚫 Бот работает только в лс.")

class IsBannedFilter(BaseFilter):
    async def __call__(self, message: types.Message) -> bool:
        with sqlite3.connect("homework.db") as conn:
            cur = conn.cursor()
            cur.execute("SELECT role FROM users WHERE user_id = ?", (message.from_user.id,))
            result = cur.fetchone()
            return result and result[0] == "ban"

class HasSchoolAndClassFilter(BaseFilter):
    async def __call__(self, message: types.Message) -> bool:
        with sqlite3.connect("homework.db") as conn:
            cur = conn.cursor()
            cur.execute("SELECT school, class FROM users WHERE user_id = ?", (message.from_user.id,))
            result = cur.fetchone()
            
            if result and result[0] is not None and result[1] is not None:
                return True
            else:
                await message.answer("❌ Вы не зарегистрировали школу и класс.\n/start")
                return False

class IsEditorOrVipOrAdminFilter(BaseFilter):
    async def __call__(self, message: types.Message, bot: Bot) -> bool:
        with sqlite3.connect("homework.db") as conn:
            cur = conn.cursor()
            cur.execute("SELECT role FROM users WHERE user_id = ?", (message.from_user.id,))
            result = cur.fetchone()
            
            if result and result[0] in ["editor", "vip", "admin"]:
                return True
            else:
                await message.answer(
                    "❌ У вас нет прав для выполнения этой команды.",
                    reply_markup=create_request_editor_keyboard()
                )
                return False

class IsAdminFilter(BaseFilter):
    async def __call__(self, message: types.Message) -> bool:
        with sqlite3.connect("homework.db") as conn:
            cur = conn.cursor()
            cur.execute("SELECT role FROM users WHERE user_id = ?", (message.from_user.id,))
            result = cur.fetchone()
            return result and result[0] == "admin"




# Вспомогательные функции
async def check_user_role(user_id):
    with sqlite3.connect("homework.db") as conn:
        cur = conn.cursor()
        cur.execute("SELECT role FROM users WHERE user_id = ?", (user_id,))
        result = cur.fetchone()
        return result[0] if result else None

async def is_editor_or_vip(user_id):
    role = await check_user_role(user_id)
    return role in ["editor", "vip", "admin"]

async def count_editors_in_class(user_class, user_school):
    with sqlite3.connect("homework.db") as conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM users WHERE class = ? AND school = ? AND role = 'editor'", (user_class, user_school))
        result = cur.fetchone()
        return result[0] if result else 0

def find_next_lesson_date(user_class, user_school, subject, user_group=None):
    today = datetime.now()
    with sqlite3.connect("homework.db") as conn:
        cur = conn.cursor()
        cur.execute("SELECT schedule_json FROM schedule WHERE class = ? AND school = ?", (user_class, user_school))
        result = cur.fetchone()
        
        if result:
            schedule = json.loads(result[0])
            for i in range(1, 14):
                date = today + timedelta(days=i)
                day_of_week = date.strftime("%A")
                
                if day_of_week in schedule:
                    for s in schedule[day_of_week]:
                        if "/" in s:
                            s_parts = s.split("/")
                            if user_group and s_parts[int(user_group) - 1] == subject:
                                return date.strftime("%y %m %d")
                        elif s == subject:
                            return date.strftime("%y %m %d")
    return None

async def get_schedule(user_class, user_school):
    with sqlite3.connect("homework.db") as conn:
        cur = conn.cursor()
        cur.execute("SELECT schedule_json FROM schedule WHERE class = ? AND school = ?", (user_class, user_school))
        result = cur.fetchone()
        if result:
            return json.loads(result[0])
        return None

async def update_schedule(user_class, user_school, schedule):
    with sqlite3.connect("homework.db") as conn:
        cur = conn.cursor()
        schedule_json = json.dumps(schedule, ensure_ascii=False)
        cur.execute("SELECT id FROM schedule WHERE class = ? AND school = ?", (user_class, user_school))
        result = cur.fetchone()
        if result:
            cur.execute("UPDATE schedule SET schedule_json = ? WHERE class = ? AND school = ?", (schedule_json, user_class, user_school))
        else:
            cur.execute("INSERT INTO schedule (user_id, class, school, schedule_json) VALUES (?, ?, ?, ?)", 
                         (user_id, user_class, user_school, schedule_json))
        conn.commit()

async def update_schedule(user_id, user_class, user_school, schedule):
    with sqlite3.connect("homework.db") as conn:
        cur = conn.cursor()
        schedule_json = json.dumps(schedule, ensure_ascii=False)
        cur.execute("SELECT id FROM schedule WHERE class = ? AND school = ?", (user_class, user_school))
        result = cur.fetchone()
        if result:
            cur.execute("UPDATE schedule SET schedule_json = ? WHERE class = ? AND school = ?", (schedule_json, user_class, user_school))
        else:
            cur.execute("INSERT INTO schedule (user_id, class, school, schedule_json) VALUES (?, ?, ?, ?)", 
                         (user_id, user_class, user_school, schedule_json))
        conn.commit()


# клавиатуры
def create_main_keyboard():
    button_add_hw = KeyboardButton(text="/addhw")
    button_view_hw = KeyboardButton(text="/viewhw")
    button_view_schedule = KeyboardButton(text="/viewschedule")
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [button_add_hw, button_view_hw],
            [button_view_schedule],
        ],
        resize_keyboard=True,
        selective=True,
    )
    return keyboard

def create_class_number_keyboard():
    builder = InlineKeyboardBuilder()
    for grade in range(1, 12):
        builder.button(text=f"{grade} класс", callback_data=f"class_{grade}")
    builder.adjust(2)
    return builder.as_markup()

def create_class_letter_keyboard(grade):
    builder = InlineKeyboardBuilder()
    for letter in ['А', 'Б', 'В', 'Г', 'Д']:
        builder.button(text=f"{grade} {letter}", callback_data=f"classn_{grade} {letter}")
    builder.adjust(2)
    return builder.as_markup()

def create_school_keyboard():
    with sqlite3.connect("homework.db") as conn:
        cur = conn.cursor()
        cur.execute("SELECT name FROM schools")
        schools = cur.fetchall()
    
    builder = InlineKeyboardBuilder()
    for school in schools:
        builder.button(text=school[0], callback_data=f"school_{school[0]}")
    
    builder.button(text="➕ Предложить новую школу", callback_data="new_school")
    builder.adjust(2)
    return builder.as_markup()

def create_school_approval_keyboard(user_id, school_name):
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Добавить", callback_data=f"approve_{user_id}_{school_name}")
    builder.button(text="❌ Забанить", callback_data=f"reject_{user_id}")
    builder.button(text="⏩ Пропустить", callback_data="skip_")
    builder.adjust(2)
    return builder.as_markup()

def create_date_keyboard(user_class, user_school, include_next_lesson_button=True):
    today = datetime.now()
    builder = InlineKeyboardBuilder()

    special_dates = {
        "Сегодня": today,
        "Завтра": today + timedelta(days=1),
        "Послезавтра": today + timedelta(days=2),
    }
    for label, date in special_dates.items():
        if date.weekday() < 5:
            builder.button(text=label, callback_data=f"date_{date.strftime('%y %m %d')}")
    
    for i in range(3, 7):
        date = today + timedelta(days=i)
        if date.weekday() < 5:
            formatted_label = date.strftime("%a %d.%m").replace("Mon", "ПН").replace("Tue", "ВТ").replace("Wed", "СР").replace("Thu", "ЧТ").replace("Fri", "ПТ")
            builder.button(text=formatted_label, callback_data=f"date_{date.strftime('%y %m %d')}")

    if include_next_lesson_button:
        builder.button(text="➕ Добавить на следующий урок", callback_data="next_lesson")
    
    builder.button(text="📅 Ввести дату вручную", callback_data="manual_date")
    builder.adjust(1)
    return builder.as_markup()

async def create_subject_keyboard(user_class, user_group=None, day=None, include_all_subjects=True):
    with sqlite3.connect("homework.db") as conn:
        cur = conn.cursor()
        cur.execute("SELECT schedule_json FROM schedule WHERE class = ?", (user_class,))
        result = cur.fetchone()
        
        if result:
            schedule = json.loads(result[0])
            if day:
                subjects = schedule.get(day, [])
            else:
                subjects = set()
                for day_subjects in schedule.values():
                    subjects.update(day_subjects)
        else:
            subjects = []
    
    builder = InlineKeyboardBuilder()
    for subject in subjects:
        if "/" in subject:
            subject_parts = subject.split("/")
            if user_group:
                subject = subject_parts[int(user_group) - 1]
            else:
                continue
        builder.button(text=subject, callback_data=f"subject_{subject}")
    if include_all_subjects:
        builder.button(text="📚 Все предметы", callback_data="all_subjects")
    builder.button(text="➕ Новый предмет", callback_data="new_subject")
    builder.adjust(2)
    return builder.as_markup()

def create_day_keyboard():
    builder = InlineKeyboardBuilder()
    days = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница"]
    for day in days:
        builder.button(text=day, callback_data=f"day_{day}")
    builder.adjust(1)
    return builder.as_markup()

def create_admin_user_actions_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="👤 Изменить роль", callback_data="admin_changerole")
    builder.button(text="💰 Изменить баланс", callback_data="admin_changebalance")
    builder.adjust(2)
    return builder.as_markup()

def create_role_selection_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="👀 Viewer", callback_data="role_viewer")
    builder.button(text="✏️ Editor", callback_data="role_editor")
    builder.button(text="🛡️ Admin", callback_data="role_admin")
    builder.button(text="🌟 VIP", callback_data="role_vip")
    builder.button(text="🚫 Ban", callback_data="role_ban")
    builder.adjust(2)
    return builder.as_markup()

def create_request_editor_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="✏️ Подать заявку на редактора", callback_data="request_editor")
    return builder.as_markup()

def create_group_selection_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="Группа 1", callback_data="group_1")
    builder.button(text="Группа 2", callback_data="group_2")
    builder.adjust(1)
    return builder.as_markup()


# Обработчики команд
@router.message(Command("start"), F.chat.type == "private", ~IsBannedFilter())
async def cmd_start(message: types.Message, state: FSMContext):
    referrer_id = None
    if len(message.text.split()) > 1:
        referrer_id = int(message.text.split()[1])
    
    with sqlite3.connect("homework.db") as conn:
        cur = conn.cursor()
        cur.execute("SELECT class, school FROM users WHERE user_id = ?", (message.from_user.id,))
        result = cur.fetchone()
    
    if result:
        user_class, user_school = result
        if user_school is None:
            await message.answer("Выберите свою школу:", reply_markup=create_school_keyboard())
            await state.set_state(UserState.waiting_for_school)
            return
        
        if user_class is None:
            await message.answer("Выберите свой класс:", reply_markup=create_class_number_keyboard())
            await state.set_state(UserState.waiting_for_class_number)
            return
        
        await message.answer(
            f"👋 Привет! Ты из {user_class} класса школы {user_school}.\n\n"
            "🎒 Команды:\n"
            "📝 /addhw – Добавить домашку\n"
            "📖 /viewhw – Посмотреть домашку\n\n"
            "✏️ /editschedule – Изменить расписание\n"
            "📅 /viewschedule – Посмотреть расписание\n\n"
            "📋 /menu – Информация о пользователе\n"
            "💖 /donate – Поддержать проект",
            reply_markup=create_main_keyboard()
        )
    else:
        with sqlite3.connect("homework.db") as conn:
            cur = conn.cursor()
            cur.execute("INSERT INTO users (user_id, username, referrer_id) VALUES (?, ?, ?)", 
                        (message.from_user.id, message.from_user.username, referrer_id))
            conn.commit()
        await message.answer("Выберите свою школу:", reply_markup=create_school_keyboard())
        await state.set_state(UserState.waiting_for_school)

@router.message(Command("addhw"), F.chat.type == "private", ~IsBannedFilter(), HasSchoolAndClassFilter(), IsEditorOrVipOrAdminFilter())
async def add_homework(message: types.Message, state: FSMContext):
    with sqlite3.connect("homework.db") as conn:
        cur = conn.cursor()
        cur.execute("SELECT class, school FROM users WHERE user_id = ?", (message.from_user.id,))
        result = cur.fetchone()
    
    if result:
        user_class, user_school = result
        await state.update_data(user_class=user_class, user_school=user_school)
        await message.answer("Выберите дату:", reply_markup=create_date_keyboard(user_class, user_school))
        await state.set_state(HomeworkState.waiting_for_date)
    else:
        await message.answer("Сначала выберите свой класс и школу с помощью команды /start")

@router.message(Command("viewhw"), F.chat.type == "private", ~IsBannedFilter(), HasSchoolAndClassFilter())
async def view_homework(message: types.Message, state: FSMContext):
    with sqlite3.connect("homework.db") as conn:
        cur = conn.cursor()
        cur.execute("SELECT class, school FROM users WHERE user_id = ?", (message.from_user.id,))
        result = cur.fetchone()
    
    if result:
        user_class, user_school = result
        await state.update_data(user_class=user_class, user_school=user_school)
        await message.answer("Выберите дату:", reply_markup=create_date_keyboard(user_class, user_school, include_next_lesson_button=False))
        await state.set_state(HomeworkState.waiting_for_view_date)
    else:
        await message.answer("Сначала выберите свой класс и школу с помощью команды /start")

@router.message(Command("editschedule"), F.chat.type == "private", ~IsBannedFilter(), HasSchoolAndClassFilter(), IsEditorOrVipOrAdminFilter())
async def edit_schedule(message: types.Message, state: FSMContext):
    with sqlite3.connect("homework.db") as conn:
        cur = conn.cursor()
        cur.execute("SELECT class, school FROM users WHERE user_id = ?", (message.from_user.id,))
        result = cur.fetchone()
    
    if result:
        user_class, user_school = result
        await state.update_data(user_class=user_class, user_school=user_school)
        await message.answer("Выберите день недели для изменения:", reply_markup=create_day_keyboard())
        await state.set_state(ScheduleState.waiting_for_day)
    else:
        await message.answer("Сначала выберите свой класс и школу с помощью команды /start")

@router.message(Command("viewschedule"), F.chat.type == "private", ~IsBannedFilter(), HasSchoolAndClassFilter())
async def view_schedule(message: types.Message):
    with sqlite3.connect("homework.db") as conn:
        cur = conn.cursor()
        cur.execute("SELECT class, school FROM users WHERE user_id = ?", (message.from_user.id,))
        result = cur.fetchone()
    
    if result:
        user_class, user_school = result
        schedule = await get_schedule(user_class, user_school)
        
        if schedule:
            text = f"📌 Расписание для {user_class} класса {user_school}:\n"
            for day, subjects in schedule.items():
                if subjects:
                    text += f"\n<b>{day}</b>\n"  # Используем <b> для жирного текста
                    text += "\n".join([f"{i+1}. {subject}" for i, subject in enumerate(subjects)]) + "\n"
            await message.answer(text, parse_mode="HTML")  # Указываем parse_mode="HTML"
        else:
            await message.answer(f"Нет расписания для {user_class} ({user_school}).\nДобавить: /editschedule")
    else:
        await message.answer("Сначала выберите свой класс и школу с помощью команды /start")

@router.message(Command("menu"), F.chat.type == "private", ~IsBannedFilter(), HasSchoolAndClassFilter())
async def cmd_menu(message: types.Message):
    with sqlite3.connect("homework.db") as conn:
        cur = conn.cursor()
        cur.execute("SELECT class, school, role, balance, referrer_id FROM users WHERE user_id = ?", (message.from_user.id,))
        result = cur.fetchone()
    
    if result:
        user_class, user_school, role, balance, referrer_id = result
        ref_link = f"https://t.me/zmdiarybot?start={message.from_user.id}"
        menu_text = (
            f"📋 *Меню пользователя:*\n\n"
            f"🏫 *Школа:* {user_school}\n"
            f"🎒 *Класс:* {user_class}\n"
            f"👤 *Роль:* {role}\n"
            f"💰 *Баланс:* {balance} баллов\n"
            f"🔗 *Реферальная ссылка* (нажмите, чтобы скопировать):\n"
            f"`{ref_link}`\n\n"
            f"💖 *Поддержать проект:* `/donate`"
        )
        await message.answer(menu_text, parse_mode="Markdown")
    else:
        await message.answer("❌ Вы не зарегистрированы. Используйте команду /start для регистрации.")

@router.message(Command("admin"), F.chat.type == "private", IsAdminFilter())
async def cmd_admin(message: types.Message, state: FSMContext):
    await message.answer("🔍 Введите имя пользователя или Telegram ID для поиска:")
    await state.set_state(AdminPanelState.waiting_for_user_search)

@router.message(Command("donate"), F.chat.type == "private", ~IsBannedFilter(), HasSchoolAndClassFilter())
async def cmd_donate(message: types.Message):
    donate_text = (
        "💖 Поддержать проект:\n\n"
        "Если вам нравится бот и вы хотите поддержать его развитие, "
        "вы можете сделать пожертвование на карту:\n\n"
        f"💳 <code>2200 7019 1503 8563</code>\n\n"
        "Спасибо за вашу поддержку! 🙏"
    )
    await message.answer(donate_text, parse_mode="HTML")

@router.message(Command("hide"), F.chat.type == "private", ~IsBannedFilter(), HasSchoolAndClassFilter())
async def cmd_hide(message: types.Message):
    await message.answer("Клавиатура скрыта.", reply_markup=types.ReplyKeyboardRemove())




# Планировщики
@aiocron.crontab('0 4 * * 6')
async def check_editors_activity():
    with sqlite3.connect("homework.db") as conn:
        cur = conn.cursor()
        cur.execute("SELECT user_id, class, school FROM users WHERE role = 'editor'")
        editors = cur.fetchall()
        
        for editor in editors:
            user_id, user_class, user_school = editor
            cur.execute("SELECT COUNT(*) FROM homework WHERE user_id = ? AND date >= date('now', '-7 days')", (user_id,))
            hw_count = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM users WHERE class = ? AND school = ? AND role = 'editor'", (user_class, user_school))
            editor_count = cur.fetchone()[0]
            if hw_count < 4 and editor_count > 3:
                cur.execute("UPDATE users SET role = 'viewer' WHERE user_id = ?", (user_id,))
                cur.execute("SELECT user_id FROM users WHERE class = ? AND school = ? AND editor_request = TRUE ORDER BY RANDOM() LIMIT 1", (user_class, user_school))
                new_editor = cur.fetchone()
                
                if new_editor:
                    new_editor_id = new_editor[0]
                    cur.execute("UPDATE users SET role = 'editor', editor_request = FALSE WHERE user_id = ?", (new_editor_id,))
                    await bot.send_message(new_editor_id, "🎉 Поздравляем! Вы стали редактором.")
        conn.commit()





# Вспомогательные обработчики команд
@router.callback_query(UserState.waiting_for_class_number, F.data.startswith("class_"))
async def process_class_number_selection(callback: types.CallbackQuery, state: FSMContext):
    grade = callback.data.split("_")[1]
    await state.update_data(grade=grade)
    await callback.message.edit_text("Выбери букву класса:", reply_markup=create_class_letter_keyboard(grade))
    await state.set_state(UserState.waiting_for_class_letter)
    await callback.answer()

@router.callback_query(UserState.waiting_for_class_letter, F.data.startswith("classn_"))
async def process_class_letter_selection(callback: types.CallbackQuery, state: FSMContext):
    user_class = callback.data.split("_")[1]
    data = await state.get_data()
    school = data.get("school")
    
    await state.update_data(user_class=user_class)
    await callback.message.edit_text("Выберите свою группу для предметов, которые изучаются по группам (например, Английский/Информатика):", reply_markup=create_group_selection_keyboard())
    await state.set_state(UserState.waiting_for_group)
    await callback.answer()


@router.callback_query(ScheduleState.waiting_for_day, F.data.startswith("day_"))
async def process_day_selection(callback: types.CallbackQuery, state: FSMContext):
    day = callback.data.split("_")[1]
    await state.update_data(day=day)
    data = await state.get_data()
    user_class = data.get("user_class")
    user_school = data.get("user_school")
    
    schedule = await get_schedule(user_class, user_school)
    text = f"📅 *Текущее расписание на {day}:*\n\n"
    
    if schedule and day in schedule and schedule[day]:
        current_schedule = ", ".join(schedule[day])
        text += f"`{current_schedule}`\n\n"
    else:
        text += "❌ _Расписание отсутствует._\n\n"
    
    text += "📚 *Список предметов* (нажмите, чтобы скопировать):\n\n"
    example_subjects = [
        "Алгебра", "Английский", "Биология", "ВИС", "География", "Геометрия", "История",
        "Информатика", "ИЗО", "Литература", "Математика", "Музыка", "МХК", "Общество",
        "ОБЖ", "РОВ", "Русский", "Технология", "Физика", "Физ-ра", "Химия", "Английский/Информатика"
    ]

    for subject in example_subjects:
        text += f"`{subject}`\n"

    text += "\n✏️ *Введите новые предметы через запятую:*"

    await callback.message.edit_text(text, parse_mode="Markdown")
    await state.set_state(ScheduleState.waiting_for_subject)
    await callback.answer()


@router.callback_query(F.data.startswith("date_"))
async def process_date_selection(callback: types.CallbackQuery, state: FSMContext):
    selected_date = callback.data.split("_")[1]
    current_state = await state.get_state()

    if current_state == HomeworkState.waiting_for_date:
        try:
            date_obj = datetime.strptime(selected_date, "%y %m %d")
            formatted_date = date_obj.strftime("%d.%m.%Y")
        except ValueError:
            await callback.message.answer("Ошибка: неверный формат даты.")
            await callback.answer()
            return

        days = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
        day_of_week = days[date_obj.weekday()]

        await state.update_data(date=selected_date)
        data = await state.get_data()
        user_class = data.get("user_class")
        await callback.message.edit_text(
            f"Вы выбрали дату: {formatted_date}\nВыберите предмет:",
            reply_markup=create_subject_keyboard(user_class, day=day_of_week)
        )
        await state.set_state(HomeworkState.waiting_for_subject)

    elif current_state == HomeworkState.waiting_for_view_date:
        with sqlite3.connect("homework.db") as conn:
            cur = conn.cursor()
            cur.execute("SELECT class, school FROM users WHERE user_id = ?", (callback.from_user.id,))
            user_data = cur.fetchone()
            if user_data:
                user_class, user_school = user_data 
                date_obj = datetime.strptime(selected_date, "%y %m %d")
                formatted_date = date_obj.strftime("%d.%m.%Y")
                days = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница"]
                day_of_week = days[date_obj.weekday()]
                schedule = await get_schedule(user_class, user_school)
                if schedule and day_of_week in schedule:
                    subjects = schedule[day_of_week]
                else:
                    subjects = []
                cur.execute("SELECT subject, task FROM homework WHERE date = ? AND class = ? AND school = ?", 
                            (selected_date, user_class, user_school))
                homework_rows = cur.fetchall()
                text = f"📅 Расписание для {user_class} на ({formatted_date}):\n"
                if subjects:
                    text += "\n".join([f"{i+1}. {subject}" for i, subject in enumerate(subjects)])
                else:
                    text += "Расписание на этот день отсутствует.\n"
                text += "\n📚 Домашнее задание:\n"
                if homework_rows:
                    text += "\n".join([f"{row[0]}: {row[1]}" for row in homework_rows])
                else:
                    text += "Нет заданий на этот день."
                await callback.message.edit_text(text)
                await state.clear()
            else:
                await callback.message.edit_text("❌ Не удалось найти данные о вашем классе и школе.")
    
    await callback.answer()

@router.callback_query(HomeworkState.waiting_for_date, F.data.startswith("date_"))
async def process_date_selection(callback: types.CallbackQuery, state: FSMContext):
    selected_date = callback.data.split("_")[1]
    date_obj = datetime.strptime(selected_date, "%y %m %d")
    formatted_date = date_obj.strftime("%d.%m.%Y")
    
    await state.update_data(date=formatted_date)
    data = await state.get_data()
    user_class = data.get("user_class")
    await callback.message.edit_text(
        f"Вы выбрали дату: {formatted_date}\nВыберите предмет:",
        reply_markup=create_subject_keyboard(user_class)
    )
    await state.set_state(HomeworkState.waiting_for_subject)
    await callback.answer()

@router.callback_query(F.data == "manual_date")
async def process_manual_date(callback: types.CallbackQuery):
    today = datetime.now().strftime("%y %m %d")
    await callback.message.edit_text(
        f"Сегодня {today}.\nВведите дату в формате ММ ДД или ГГ ММ ДД:"
    )

@router.message(HomeworkState.waiting_for_view_date, F.text.regexp(r"(\d{2} \d{2} \d{2})|(\d{2} \d{2})"))
async def process_manual_view_date_input(message: types.Message, state: FSMContext):
    try:
        input_date = message.text.strip()
        today = datetime.now()

        if len(input_date.split()) == 2:
            month, day = input_date.split()
            year = today.strftime("%y")
            input_date = f"{year} {month} {day}"

        datetime.strptime(input_date, "%y %m %d")

        data = await state.get_data()
        user_class = data.get("user_class")
        user_school = data.get("user_school")

        with sqlite3.connect("homework.db") as conn:
            cur = conn.cursor()
            cur.execute("SELECT group_number FROM users WHERE user_id = ?", (message.from_user.id,))
            user_group = cur.fetchone()[0]
            
            date_obj = datetime.strptime(input_date, "%y %m %d")
            days = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница"]
            day_of_week = days[date_obj.weekday()]
            
            schedule = await get_schedule(user_class, user_school)
            if schedule and day_of_week in schedule:
                subjects = schedule[day_of_week]
            else:
                subjects = []
            
            cur.execute("SELECT subject, task FROM homework WHERE date = ? AND class = ? AND school = ? AND (group_number IS NULL OR group_number = ?)", 
                        (input_date, user_class, user_school, user_group))
            homework_rows = cur.fetchall()
        
        formatted_date = date_obj.strftime("%d.%m.%Y")
        
        text = f"📅 Расписание для {user_class} на ({formatted_date}):\n"
        if subjects:
            text += "\n".join([f"{i+1}. {subject}" for i, subject in enumerate(subjects)])
        else:
            text += "Расписание на этот день отсутствует.\n"
        
        text += "\n📚 Домашнее задание:\n"
        if homework_rows:
            text += "\n".join([f"{row[0]}: {row[1]}" for row in homework_rows])
        else:
            text += "Нет заданий на этот день."
        
        await message.answer(text)
        await state.clear()

    except ValueError:
        await message.answer("Некорректный формат даты. Используйте ММ ДД (например, 04 15) или ГГ ММ ДД (например, 24 04 15).")


@router.callback_query(HomeworkState.waiting_for_subject, F.data == "all_subjects")
async def process_all_subjects(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    user_class = data.get("user_class")
    
    await callback.message.edit_text(
        "Выберите предмет из всех доступных:",
        reply_markup=create_subject_keyboard(user_class, include_all_subjects=False)
    )
    await callback.answer()

@router.callback_query(HomeworkState.waiting_for_subject, F.data.startswith("subject_"))
async def process_subject_selection(callback: types.CallbackQuery, state: FSMContext):
    subject = callback.data.split("_")[1]
    await state.update_data(subject=subject)
    await callback.message.edit_text(f"Вы выбрали предмет: {subject}\nТеперь введите задание:")
    await state.set_state(HomeworkState.waiting_for_task)
    await callback.answer()

@router.message(ScheduleState.waiting_for_subject)
async def process_subject_input(message: types.Message, state: FSMContext):
    data = await state.get_data()
    day = data.get("day")
    user_class = data.get("user_class")
    user_school = data.get("user_school")
    user_id = message.from_user.id
    subjects = [subject.strip() for subject in message.text.split(",")]

    schedule = await get_schedule(user_class, user_school)
    if not schedule:
        schedule = {
            "Понедельник": [],
            "Вторник": [],
            "Среда": [],
            "Четверг": [],
            "Пятница": []
        }
    
    schedule[day] = subjects
    await update_schedule(user_id, user_class, user_school, schedule)
    await message.reply(f"✅ Расписание на {day} обновлено: {', '.join(subjects)}")
    await state.clear()

@router.callback_query(HomeworkState.waiting_for_subject, F.data == "new_subject")
async def process_new_subject(callback: types.CallbackQuery, state: FSMContext):
    await state.update_data(is_new_subject=True)
    await callback.message.edit_text("Введите название нового предмета:")
    await state.set_state(HomeworkState.waiting_for_task)
    await callback.answer()

@router.message(HomeworkState.waiting_for_task)
async def process_task_input(message: types.Message, state: FSMContext):
    data = await state.get_data()
    date = data.get("date")
    user_class = data.get("user_class")
    user_school = data.get("user_school")
    subject = data.get("subject")
    task = message.text

    with sqlite3.connect("homework.db") as conn:
        cur = conn.cursor()
        cur.execute("SELECT group_number FROM users WHERE user_id = ?", (message.from_user.id,))
        user_group = cur.fetchone()[0]
        
        cur.execute("INSERT INTO homework (user_id, date, class, school, subject, task, group_number) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (message.from_user.id, date, user_class, user_school, subject, task, user_group))
        conn.commit()

    await message.answer(f"✅ Добавлено: {subject} на {date} для {user_class} — {task}")
    await state.clear()

@router.message(ScheduleState.waiting_for_subject)
async def process_subject_input(message: types.Message, state: FSMContext):
    data = await state.get_data()
    day = data.get("day")
    user_class = data.get("user_class")
    subjects = message.text.split(",")

    with sqlite3.connect("homework.db") as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM schedule WHERE day = ? AND class = ?", (day, user_class))
        for subject in subjects:
            cur.execute("INSERT INTO schedule (user_id, class, day, subject) VALUES (?, ?, ?, ?)", (message.from_user.id, user_class, day, subject.strip()))
        conn.commit()
    
    await message.reply(f"✅ Расписание на {day} обновлено: {', '.join(subjects)}")
    await state.clear()


@router.callback_query(UserState.waiting_for_school, F.data.startswith("school_"))
async def process_school_selection(callback: types.CallbackQuery, state: FSMContext):
    school = callback.data.split("_")[1]
    
    with sqlite3.connect("homework.db") as conn:
        cur = conn.cursor()
        cur.execute("SELECT id FROM schools WHERE name = ?", (school,))
        result = cur.fetchone()
        
        if result:
            cur.execute("UPDATE users SET school = ? WHERE user_id = ?", (school, callback.from_user.id))
            conn.commit()
            await callback.message.edit_text("Выберите свой класс:", reply_markup=create_class_number_keyboard())
            await state.set_state(UserState.waiting_for_class_number)
        else:
            admin_chat_id = ADMIN_CHAT_ID
            await bot.send_message(
                admin_chat_id,
                f"Новое предложение школы:\n\nШкола: {school}\nПользователь: @{callback.from_user.username}\n\nВыберите действие:",
                reply_markup=create_school_approval_keyboard(callback.from_user.id, school)
            )
            await callback.message.edit_text(f"✅ Ваше предложение о добавлении школы '{school}' отправлено на рассмотрение.")
            await state.clear()
    await callback.answer()

@router.callback_query(UserState.waiting_for_school, F.data == "new_school")
async def process_new_school(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.edit_text(f"Введите название школы пример:\n«Школа №12».")
    await state.set_state(UserState.waiting_for_new_school)
    await callback.answer()

@router.message(UserState.waiting_for_new_school)
async def process_new_school_input(message: types.Message, state: FSMContext):
    school_name = message.text.strip()
    data = await state.get_data()
    user_class = data.get("user_class")
    admin_chat_id = ADMIN_CHAT_ID
    await bot.send_message(
        admin_chat_id,
        f"Новое предложение школы:\n\nШкола: {school_name}\nПользователь: @{message.from_user.username}\n\nВыберите действие:",
        reply_markup=create_school_approval_keyboard(message.from_user.id, school_name)
    )
    await message.answer(f"✅ Школа «{school_name}» отправлена на модерацию.")
    await state.clear()

@router.callback_query(F.data.startswith("approve_"))
async def process_school_approval(callback: types.CallbackQuery):
    _, user_id, school_name = callback.data.split("_")
    user_id = int(user_id)
    
    with sqlite3.connect("homework.db") as conn:
        cur = conn.cursor()
        cur.execute("INSERT INTO schools (name) VALUES (?)", (school_name,))
        cur.execute("UPDATE users SET school = ?, username = ? WHERE user_id = ?", 
                    (school_name, callback.from_user.username, user_id))
        conn.commit()
    
    await callback.message.edit_text(f"✅ Школа '{school_name}' одобрена и добавлена в список. Пользователь @{callback.from_user.username} теперь может выбрать класс.")
    await bot.send_message(user_id, f"✅ Школа «{school_name}» одобрена!\n Выберите класс. /start")
    await callback.answer()

@router.callback_query(F.data.startswith("reject_"))
async def process_school_rejection(callback: types.CallbackQuery):
    _, user_id = callback.data.split("_")
    user_id = int(user_id)
    
    with sqlite3.connect("homework.db") as conn:
        cur = conn.cursor()
        cur.execute("UPDATE users SET role = 'ban' WHERE user_id = ?", (user_id,))
        conn.commit()
    
    await callback.message.edit_text(f"❌ Предложение школы отклонено.\nПользователь @{callback.from_user.username} забанен.")
    await bot.send_message(user_id, "❌ Ваше предложение школы отклонено.")
    await callback.answer()

@router.callback_query(F.data == "skip_")
async def process_skip_request(callback: types.CallbackQuery):
    message_text = callback.message.text
    username = message_text.split("@")[1].split("\n")[0]
    await bot.send_message(
        chat_id=callback.from_user.id,
        text=f"❌ Ваша заявка на добавление школы была пропущена."
    )
    await callback.message.edit_text(
        f"Заявка от @{username} пропущена.",
        reply_markup=None
    )
    await callback.answer()

@router.callback_query(F.data == "next_lesson")
async def process_next_lesson(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    user_class = data.get("user_class")
    user_school = data.get("user_school")
    today = datetime.now()
    days = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница"]
    today_day = days[today.weekday()]
    schedule = await get_schedule(user_class, user_school)
    if not schedule or today_day not in schedule:
        await callback.answer("❌ На сегодня нет расписания.", show_alert=True)
        return

    subjects = schedule[today_day]
    builder = InlineKeyboardBuilder()
    for subject in subjects:
        builder.button(text=subject, callback_data=f"next_subject_{subject}")
    builder.adjust(2)
    
    await callback.message.edit_text("Выберите предмет для добавления на следующий урок:", reply_markup=builder.as_markup())
    await callback.answer()

@router.callback_query(F.data.startswith("next_subject_"))
async def process_next_subject(callback: types.CallbackQuery, state: FSMContext):
    subject = callback.data.split("_")[2]
    data = await state.get_data()
    user_class = data.get("user_class")
    user_school = data.get("user_school")
    today = datetime.now()
    days = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница"]
    schedule = await get_schedule(user_class, user_school)
    if not schedule:
        await callback.answer("❌ Расписание не найдено.", show_alert=True)
        return

    for i in range(1, 8):
        next_day = today + timedelta(days=i)
        next_day_weekday = next_day.weekday()
        if next_day_weekday >= 5:
            continue

        next_day_name = days[next_day_weekday]
        if next_day_name in schedule and subject in schedule[next_day_name]:
            next_date = next_day.strftime("%y %m %d")
            await state.update_data(date=next_date, subject=subject)
            await callback.message.edit_text(f"Следующий урок по {subject} будет {next_day.strftime('%d.%m.%Y')}.\nВведите задание:")
            await state.set_state(HomeworkState.waiting_for_task)
            await callback.answer()
            return
    await callback.answer("❌ Следующий урок по этому предмету не найден.", show_alert=True)

@router.callback_query(HomeworkState.waiting_for_date, F.data == "next_lesson")
async def process_next_lesson(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    user_class = data.get("user_class")
    user_school = data.get("user_school")
    subject = data.get("subject")
    
    if not subject:
        await callback.answer("❌ Сначала выберите предмет.")
        return
   
    next_lesson_date = find_next_lesson_date(user_class, user_school, subject)
    
    if not next_lesson_date:
        await callback.answer("❌ Следующий урок по этому предмету не найден.")
        return
    
    await state.update_data(date=next_lesson_date)
    await callback.message.edit_text(f"Следующий урок по {subject} будет {next_lesson_date}. Введите задание:")
    await state.set_state(HomeworkState.waiting_for_task)
    await callback.answer()


@router.message(AdminPanelState.waiting_for_user_search)
async def process_user_search(message: types.Message, state: FSMContext):
    search_query = message.text.strip()
    
    try:
        with sqlite3.connect("homework.db") as conn:
            cur = conn.cursor()
            
            if search_query.isdigit():
                cur.execute("SELECT user_id, username, class, school, role, balance FROM users WHERE user_id = ?", (int(search_query),))
            else:
                cur.execute("SELECT user_id, username, class, school, role, balance FROM users WHERE user_id IN (SELECT user_id FROM users WHERE class LIKE ? OR school LIKE ? OR username LIKE ?)", 
                             (f"%{search_query}%", f"%{search_query}%", f"%{search_query}%"))
            
            users = cur.fetchall()
        
        if not users:
            await message.answer("❌ Пользователь не найден.")
            await state.clear()
            return
        
        if len(users) > 1:
            builder = InlineKeyboardBuilder()
            for user in users:
                user_id, username, user_class, user_school, role, balance = user
                builder.button(text=f"@{username} - {user_class} {user_school} ({role})", callback_data=f"admin_user_{user_id}")
            builder.adjust(1)
            await message.answer("🔍 Найдено несколько пользователей. Выберите одного:", reply_markup=builder.as_markup())
        else:
            user_id, username, user_class, user_school, role, balance = users[0]
            await state.update_data(user_id=user_id)
            if role == "admin":
                await message.answer(
                    f"🔍 Информация о пользователе (админ):\n\n"
                    f"🆔 ID: {user_id}\n"
                    f"👤 Username: @{username}\n"
                    f"🏫 Школа: {user_school}\n"
                    f"🎒 Класс: {user_class}\n"
                    f"👤 Роль: {role}\n"
                    f"💰 Баланс: {balance}\n\n"
                    f"❌ Вы не можете изменять данные другого админа."
                )
                await state.clear()
            else:
                await message.answer(
                    f"🔍 Найден пользователь:\n\n"
                    f"🆔 ID: {user_id}\n"
                    f"👤 Username: @{username}\n"
                    f"🏫 Школа: {user_school}\n"
                    f"🎒 Класс: {user_class}\n"
                    f"👤 Роль: {role}\n"
                    f"💰 Баланс: {balance}\n\n"
                    f"Выберите действие:",
                    reply_markup=create_admin_user_actions_keyboard()
                )
                await state.set_state(AdminPanelState.waiting_for_user_action)
    except Exception as e:
        logger.error(f"Ошибка при поиске пользователя: {e}")
        await message.answer("❌ Произошла ошибка при поиске пользователя.")

@router.callback_query(AdminPanelState.waiting_for_user_action, F.data.startswith("admin_"))
async def process_admin_action(callback: types.CallbackQuery, state: FSMContext):
    try:
        action = callback.data.split("_")[1]
        data = await state.get_data()
        user_id = data.get("user_id")
        with sqlite3.connect("homework.db") as conn:
            cur = conn.cursor()
            cur.execute("SELECT role FROM users WHERE user_id = ?", (user_id,))
            user_role = cur.fetchone()[0]
        
        if user_role == "admin":
            await callback.message.edit_text("❌ Вы не можете изменять данные другого админа.")
            await state.clear()
            return
        
        if action == "changerole":
            await callback.message.edit_text("Выберите новую роль:", reply_markup=create_role_selection_keyboard())
            await state.set_state(AdminPanelState.waiting_for_role_change)
        elif action == "changebalance":
            await callback.message.edit_text("Введите новый баланс:")
            await state.set_state(AdminPanelState.waiting_for_balance_change)
    except Exception as e:
        logger.error(f"Ошибка при обработке действия: {e}")
        await callback.answer("❌ Произошла ошибка при обработке запроса.")
    finally:
        await callback.answer()

@router.callback_query(AdminPanelState.waiting_for_role_change, F.data.startswith("role_"))
async def process_role_change(callback: types.CallbackQuery, state: FSMContext):
    try:
        role = callback.data.split("_")[1]
        data = await state.get_data()
        user_id = data.get("user_id")
        
        with sqlite3.connect("homework.db") as conn:
            cur = conn.cursor()
            cur.execute("SELECT role FROM users WHERE user_id = ?", (user_id,))
            user_role = cur.fetchone()[0]
        
        if user_role == "admin":
            await callback.answer("❌ Вы не можете изменять данные другого администратора.", show_alert=True)
            return
        
        with sqlite3.connect("homework.db") as conn:
            cur = conn.cursor()
            cur.execute("UPDATE users SET role = ? WHERE user_id = ?", (role, user_id))
            conn.commit()
        
        await callback.message.edit_text(f"✅ Роль пользователя {user_id} изменена на {role}.")
        await state.clear()
    except Exception as e:
        logger.error(f"Ошибка при изменении роли: {e}")
        await callback.answer("❌ Произошла ошибка при изменении роли.")
    finally:
        await callback.answer()

@router.message(AdminPanelState.waiting_for_balance_change)
async def process_balance_change(message: types.Message, state: FSMContext):
    try:
        balance = int(message.text.strip())
        data = await state.get_data()
        user_id = data.get("user_id")
        
        with sqlite3.connect("homework.db") as conn:
            cur = conn.cursor()
            cur.execute("SELECT role FROM users WHERE user_id = ?", (user_id,))
            user_role = cur.fetchone()[0]
        
        if user_role == "admin":
            await message.answer("❌ Вы не можете изменять данные другого администратора.")
            return
        
        with sqlite3.connect("homework.db") as conn:
            cur = conn.cursor()
            cur.execute("UPDATE users SET balance = ? WHERE user_id = ?", (balance, user_id))
            conn.commit()
        
        await message.answer(f"✅ Баланс пользователя {user_id} изменен на {balance}.")
        await state.clear()
    except ValueError:
        await message.answer("❌ Некорректное значение баланса. Введите число.")
    except Exception as e:
        logger.error(f"Ошибка при изменении баланса: {e}")
        await message.answer("❌ Произошла ошибка при изменении баланса.")

@router.callback_query(F.data == "request_editor")
async def process_request_editor(callback: types.CallbackQuery):
    role = await check_user_role(callback.from_user.id)
    if role == "ban":
        await callback.answer("❌ Вы забанены и не можете пользоваться ботом.", show_alert=True)
        return
    
    with sqlite3.connect("homework.db") as conn:
        cur = conn.cursor()
        cur.execute("SELECT class, school, editor_request FROM users WHERE user_id = ?", (callback.from_user.id,))
        result = cur.fetchone()
    
    if result:
        user_class, user_school, editor_request = result
        
        if editor_request:
            await callback.answer("❌ Вы уже подавали заявку на роль редактора.", show_alert=True)
            return

        with sqlite3.connect("homework.db") as conn:
            cur = conn.cursor()
            cur.execute("UPDATE users SET editor_request = TRUE WHERE user_id = ?", (callback.from_user.id,))
            conn.commit()
        
        await callback.answer("✅ Ваша заявка на роль редактора отправлена.", show_alert=True)
    else:
        await callback.answer("❌ Сначала выберите свой класс и школу с помощью команды /start.", show_alert=True)
    await callback.message.delete()

@router.callback_query(UserState.waiting_for_group, F.data.startswith("group_"))
async def process_group_selection(callback: types.CallbackQuery, state: FSMContext):
    group = callback.data.split("_")[1]
    data = await state.get_data()
    user_class = data.get("user_class")
    school = data.get("school")
    
    with sqlite3.connect("homework.db") as conn:
        cur = conn.cursor()
        cur.execute("UPDATE users SET class = ?, group_number = ?, username = ? WHERE user_id = ?", 
                    (user_class, group, callback.from_user.username, callback.from_user.id))
        conn.commit()

    with sqlite3.connect("homework.db") as conn:        
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM schedule WHERE class = ? AND school = ?", (user_class, school))        
        schedule_count = cur.fetchone()[0]
    
    if schedule_count == 0:
        await callback.message.edit_text(
            f"✅ Вы выбрали {user_class} и группу {group}."
            "⚠️ Расписания нет. Добавьте его: /editschedule."        
    	)
    else:        
        await callback.message.edit_text(f"✅ Вы выбрали {user_class} и группу {group}.\n/start")
    await state.clear()
    await callback.answer()

async def set_bot_commands(bot: Bot):
    commands = [
	BotCommand(command="start", description="Запуск бота"),
        BotCommand(command="addhw", description="добавить домашку"),
        BotCommand(command="viewhw", description="посмотреть домашку"),
        BotCommand(command="editschedule", description="изменить расписание"),
        BotCommand(command="viewschedule", description="посмотреть расписание"),
        BotCommand(command="menu", description="информация о пользователе"),
        BotCommand(command="donate", description="поддержать проект"),
    ]
    await bot.set_my_commands(commands)

async def main():
    try:
        await set_bot_commands(bot)
        await dp.start_polling(bot)
    except Exception as e:
        logger.error(f"Ошибка в основном цикле: {e}")

if __name__ == "__main__":
    asyncio.run(main())