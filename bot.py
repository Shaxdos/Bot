import asyncio
import logging
import sqlite3
import html
import os
from datetime import datetime
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.types import FSInputFile

# --- KONFIGURATSIYA ---
API_TOKEN = "8773028400:AAGBWrajqsRhTqp3nYsLTTaTtfRqGAHkyyY"
ADMIN_ID = 6774058619
LOG_GROUP_ID = -1003225370008

bot = Bot(token=API_TOKEN)
dp = Dispatcher()

# --- BAZA BILAN ISHLASH ---
conn = sqlite3.connect("open_budget_pro.db", check_same_thread=False)
cursor = conn.cursor()

def db_setup():
    cursor.execute('''CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY, username TEXT, name TEXT, phone TEXT,
        balance INTEGER DEFAULT 0, votes INTEGER DEFAULT 0,
        withdrawn INTEGER DEFAULT 0, referrer_id INTEGER, ref_paid INTEGER DEFAULT 0)''')
    
    cursor.execute('''CREATE TABLE IF NOT EXISTS channels (
        id INTEGER PRIMARY KEY AUTOINCREMENT, channel_id TEXT, title TEXT, url TEXT)''')

    cursor.execute('''CREATE TABLE IF NOT EXISTS used_phones (phone TEXT PRIMARY KEY)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)''')

    default_start = ("<b>BOT AKTIV ISHLAMOQDA ✅</b>\n\n"
                     "⁉️ BOT ORQALI QANDAY QILIB OVOZ BERISH VIDEODA KO'RSATILGAN.\n\n"
                     "🎉 To'g'ri ovoz berganlarga pul shu zahoti o'tkazilmoqda!\n\n"
                     "🥳 Aziz {name}! 🗳 Ovoz berish tugmasini bosib, ovoz bering!")

    sets = [('vote_price', '5000'), ('ref_price', '1000'), 
            ('min_withdraw', '15000'), ('vote_link', 'https://t.me/ochiqbudjetbot?start=053465392013'),
            ('payment_channel', 'O\'rnatilmagan'),
            ('start_text', default_start),
            ('start_video_id', '')]
    
    for k, v in sets:
        cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, v))
    conn.commit()

db_setup()

# --- YORDAMCHI FUNKSIYALAR ---
def get_config(key):
    cursor.execute("SELECT value FROM settings WHERE key=?", (key,))
    res = cursor.fetchone()
    return res[0] if res else ""

async def check_sub(user_id):
    cursor.execute("SELECT channel_id FROM channels")
    rows = cursor.fetchall()
    for (ch_id,) in rows:
        try:
            m = await bot.get_chat_member(ch_id, user_id)
            if m.status in ['left', 'kicked', 'member_not_found']: return False
        except: return False
    return True

# --- STATES ---
class UserStates(StatesGroup):
    get_phone_for_vote = State()
    waiting_for_screenshot = State()

# --- KLAVIATURALAR ---
def main_menu(user_id):
    kb = ReplyKeyboardBuilder()
    kb.button(text="🗳 Ovoz berish")
    kb.row(types.KeyboardButton(text="💰 Hisobim"), types.KeyboardButton(text="💸 Pul yechib olish"))
    kb.row(types.KeyboardButton(text="🔗 Referal"), types.KeyboardButton(text="🏆 Yutuqlar"))
    if user_id == ADMIN_ID: kb.row(types.KeyboardButton(text="🚀 So'rovlar"))
    return kb.as_markup(resize_keyboard=True)

# --- HANDLERS ---
@dp.message(F.text == "🏠 Orqaga")
async def back_main_handler(message: types.Message, state: FSMContext):
    await state.clear()
    start_msg = get_config('start_text').replace("{name}", html.escape(message.from_user.full_name))
    await message.answer(start_msg, reply_markup=main_menu(message.from_user.id), parse_mode="HTML")

@dp.message(CommandStart())
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    u_id = message.from_user.id
    name = message.from_user.full_name
    username = message.from_user.username or "—"
    
    cursor.execute("SELECT user_id FROM users WHERE user_id=?", (u_id,))
    if not cursor.fetchone():
        ref_id = None
        parts = message.text.split()
        if len(parts) > 1 and parts[1].isdigit():
            p_ref = int(parts[1])
            if p_ref != u_id: ref_id = p_ref
        cursor.execute("INSERT INTO users (user_id, username, name, referrer_id) VALUES (?, ?, ?, ?)", (u_id, username, name, ref_id))
        conn.commit()

    if not await check_sub(u_id):
        kb = InlineKeyboardBuilder()
        cursor.execute("SELECT title, url FROM channels")
        for t, u in cursor.fetchall(): kb.button(text=t, url=u)
        kb.button(text="✅ Tasdiqlash", callback_data="recheck")
        kb.adjust(1)
        return await message.answer("❌ <b>Obuna bo'ling:</b>", reply_markup=kb.as_markup(), parse_mode="HTML")

    start_msg = get_config('start_text').replace("{name}", html.escape(name))
    await message.answer(start_msg, reply_markup=main_menu(u_id), parse_mode="HTML")

# --- OVOZ BERISH MANTIQI ---
@dp.message(F.text == "🗳 Ovoz berish")
async def vote_step_1(message: types.Message, state: FSMContext):
    await message.answer("📞 Ovoz berish uchun telefon raqamingizni yuboring:\n(Masalan: 998901234567)",
                         reply_markup=ReplyKeyboardBuilder().button(text="🏠 Orqaga").as_markup(resize_keyboard=True))
    await state.set_state(UserStates.get_phone_for_vote)

@dp.message(UserStates.get_phone_for_vote)
async def vote_step_2(message: types.Message, state: FSMContext):
    if message.text == "🏠 Orqaga": return await back_main_handler(message, state)
    
    phone = message.text.strip().replace("+", "")
    if not phone.isdigit() or len(phone) < 9:
        return await message.answer("❌ Noto'g'ri raqam formati.")

    cursor.execute("SELECT phone FROM used_phones WHERE phone=?", (phone,))
    if cursor.fetchone():
        return await message.answer("❌ Bu raqam ishlatilgan!")

    await state.update_data(vote_phone=phone)
    
    kb = InlineKeyboardBuilder()
    kb.button(text="🔗 Ovoz berish (Saytga o'tish)", url=get_config('vote_link'))
    kb.button(text="✅ Ovoz berdim", callback_data="voted_done")
    kb.adjust(1)

    await message.answer(f"✅ Raqam qabul qilindi: {phone}\n\nEndi quyidagi tugma orqali ovoz bering va skrinshotni tayyorlang.", 
                         reply_markup=kb.as_markup())

@dp.callback_query(F.data == "voted_done")
async def vote_step_3(call: types.CallbackQuery, state: FSMContext):
    await call.message.answer("📸 Endi ovoz berganingizni tasdiqlovchi skrinshotni yuboring:")
    await state.set_state(UserStates.waiting_for_screenshot)
    await call.answer()

@dp.message(UserStates.waiting_for_screenshot, F.photo)
async def vote_step_4(message: types.Message, state: FSMContext):
    data = await state.get_data()
    phone = data.get('vote_phone')
    u_id = message.from_user.id

    kb = InlineKeyboardBuilder()
    # callback_data uzunligi chegaralangan, shuning uchun formatni qisqartiramiz
    kb.button(text="✅ Tasdiqlash", callback_data=f"vok_{u_id}_{phone}")
    kb.button(text="❌ Rad etish", callback_data=f"vno_{u_id}")
    kb.adjust(2)

    await bot.send_photo(ADMIN_ID, message.photo[-1].file_id, 
                         caption=f"🗳 <b>Yangi ovoz!</b>\n\n👤: {message.from_user.full_name}\n🆔: {u_id}\n📞: {phone}", 
                         reply_markup=kb.as_markup(), parse_mode="HTML")

    await message.answer("✅ Rahmat! Skrinshot adminga yuborildi. Tekshirilgach pul tushadi.", reply_markup=main_menu(u_id))
    await state.clear()

# --- ADMIN TASDIQLASHI ---
@dp.callback_query(F.data.startswith("vok_"))
async def admin_confirm_vote(call: types.CallbackQuery):
    # vok_ID_PHONE formatidan ajratib olish
    parts = call.data.split("_")
    u_id = int(parts[1])
    phone = parts[2]
    
    price = int(get_config('vote_price'))
    
    cursor.execute("UPDATE users SET balance = balance + ?, votes = votes + 1 WHERE user_id=?", (price, u_id))
    cursor.execute("INSERT OR IGNORE INTO used_phones (phone) VALUES (?)", (phone,))
    conn.commit()

    try: await bot.send_message(u_id, f"✅ Tabriklaymiz! Ovozingiz tasdiqlandi. Hisobingizga {price} so'm qo'shildi.")
    except: pass
    
    await call.message.edit_caption(caption=call.message.caption + "\n\n✅ <b>TASDIQLANDI</b>", parse_mode="HTML")

@dp.callback_query(F.data.startswith("vno_"))
async def admin_reject_vote(call: types.CallbackQuery):
    u_id = int(call.data.split("_")[1])
    try: await bot.send_message(u_id, "❌ Ovozingiz admin tomonidan rad etildi. Skrinshot xato bo'lishi mumkin.")
    except: pass
    await call.message.edit_caption(caption=call.message.caption + "\n\n❌ <b>RAD ETILDI</b>", parse_mode="HTML")

@dp.message(F.text == "💰 Hisobim")
async def my_account(message: types.Message):
    cursor.execute("SELECT balance, votes FROM users WHERE user_id=?", (message.from_user.id,))
    u = cursor.fetchone()
    if u:
        await message.answer(f"👤 <b>Foydalanuvchi:</b> {message.from_user.full_name}\n💰 Balans: {u[0]} so'm\n🗳 Ovozlar: {u[1]} ta", parse_mode="HTML")

async def main():
    logging.basicConfig(level=logging.INFO)
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

