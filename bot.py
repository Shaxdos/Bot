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
ADMIN_ID = 7957774091
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
    
    cursor.execute("PRAGMA table_info(users)")
    cols = [c[1] for c in cursor.fetchall()]
    if 'name' not in cols: cursor.execute("ALTER TABLE users ADD COLUMN name TEXT")
    if 'ref_paid' not in cols: cursor.execute("ALTER TABLE users ADD COLUMN ref_paid INTEGER DEFAULT 0")

    cursor.execute('''CREATE TABLE IF NOT EXISTS channels (
        id INTEGER PRIMARY KEY AUTOINCREMENT, channel_id TEXT, title TEXT, url TEXT)''')
    
    cursor.execute('''CREATE TABLE IF NOT EXISTS used_phones (phone TEXT PRIMARY KEY)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)''')
    
    default_start = ("<b>BOT AKTIV ISHLAMOQDA ✅</b>\n\n"
                     "⁉️ BOT ORQALI QANDAY QILIB OVOZ BERISH VIDEODA KO'RSATILGAN.\n\n"
                     "🎉 To'g'ri ovoz berganlarga pul shu zahoti o'tkazilmoqda!\n\n"
                     "🥳 Aziz {name}! 🗳 Ovoz berish tugmasini bosib, ovoz bering!")
    
    sets =[('vote_price', '5000'), ('ref_price', '1000'), 
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

def set_config(key, value):
    cursor.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, str(value)))
    conn.commit()

async def log_to_group(text):
    try: await bot.send_message(LOG_GROUP_ID, text)
    except Exception as e: logging.error(f"Guruhga yozishda xato: {e}")

async def check_sub(user_id):
    cursor.execute("SELECT channel_id FROM channels")
    rows = cursor.fetchall()
    for (ch_id,) in rows:
        try:
            m = await bot.get_chat_member(ch_id, user_id)
            if m.status in['left', 'kicked']: return False
        except: return False
    return True

async def reward_referrer(user_id):
    cursor.execute("SELECT referrer_id, ref_paid FROM users WHERE user_id=?", (user_id,))
    row = cursor.fetchone()
    if row and row[0] and row[1] == 0:
        ref_id = row[0]
        ref_price = int(get_config('ref_price'))
        cursor.execute("UPDATE users SET balance = balance + ? WHERE user_id=?", (ref_price, ref_id))
        cursor.execute("UPDATE users SET ref_paid = 1 WHERE user_id=?", (user_id,))
        conn.commit()
        try: await bot.send_message(ref_id, f"🎉 <b>Yangi referal!</b>\nSizga {ref_price} so'm berildi.", parse_mode="HTML")
        except: pass

# --- STATES ---
class AdminState(StatesGroup):
    broadcast_text = State()
    broadcast_forward = State()
    add_ch_id = State()
    add_ch_title = State()
    add_ch_url = State()
    pay_channel = State()

class UserStates(StatesGroup):
    withdraw_method = State()
    withdraw_details = State()
    withdraw_amount = State()

# --- KLAVIATURALAR ---
def main_menu(user_id):
    kb = ReplyKeyboardBuilder()
    kb.button(text="🗳 Ovoz berish")
    kb.row(types.KeyboardButton(text="💰 Hisobim"), types.KeyboardButton(text="💸 Pul yechib olish"))
    kb.row(types.KeyboardButton(text="🔗 Referal"), types.KeyboardButton(text="🏆 Yutuqlar"))
    if user_id == ADMIN_ID: kb.row(types.KeyboardButton(text="🚀 So'rovlar"))
    return kb.as_markup(resize_keyboard=True)

def admin_panel_kb():
    kb = ReplyKeyboardBuilder()
    kb.row(types.KeyboardButton(text="✉️ Oddiy xabar"), types.KeyboardButton(text="📩 Forward xabar"))
    kb.row(types.KeyboardButton(text="📄 Ulangan kanallar"), types.KeyboardButton(text="📤 To'lovlar kanali"))
    kb.row(types.KeyboardButton(text="📢 Kanal ulash"), types.KeyboardButton(text="🔇 Kanal uzish"))
    kb.row(types.KeyboardButton(text="📊 Statistika"), types.KeyboardButton(text="🏠 Orqaga"))
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
        if ref_id: await log_to_group(f"👤 {name}\n🆔 {u_id}\nTaklif qildi: {ref_id}\n✅ Yangi foydalanuvchi")

    if not await check_sub(u_id):
        kb = InlineKeyboardBuilder()
        cursor.execute("SELECT title, url FROM channels")
        for t, u in cursor.fetchall(): kb.button(text=t, url=u)
        kb.button(text="✅ Tasdiqlash", callback_data="recheck")
        kb.adjust(1)
        return await message.answer("❌ <b>Kanallarga obuna bo'ling:</b>", reply_markup=kb.as_markup(), parse_mode="HTML")

    await reward_referrer(u_id)
    start_msg = get_config('start_text').replace("{name}", html.escape(name))
    vid_id = get_config('start_video_id')
    
    try:
        if vid_id and vid_id != "":
            await message.answer_video(vid_id, caption=start_msg, reply_markup=main_menu(u_id), parse_mode="HTML")
        elif os.path.exists("11.mp4"):
            msg = await message.answer_video(FSInputFile("11.mp4"), caption=start_msg, reply_markup=main_menu(u_id), parse_mode="HTML")
            set_config('start_video_id', msg.video.file_id)
        else:
            await message.answer(start_msg, reply_markup=main_menu(u_id), parse_mode="HTML")
    except:
        await message.answer(start_msg, reply_markup=main_menu(u_id), parse_mode="HTML")

@dp.callback_query(F.data == "recheck")
async def recheck_sub(call: types.CallbackQuery, state: FSMContext):
    if not await check_sub(call.from_user.id):
        return await call.answer("❌ Hali obuna bo'lmagansiz!", show_alert=True)
    await call.message.delete()
    await cmd_start(call.message, state)

@dp.message(F.text == "💰 Hisobim")
async def my_account(message: types.Message):
    cursor.execute("SELECT balance, votes, withdrawn FROM users WHERE user_id=?", (message.from_user.id,))
    user = cursor.fetchone()
    if user:
        text = f"👤 <b>Kabinet:</b> {html.escape(message.from_user.full_name)}\n\n💰 Balans: {user[0]} so'm\n🗳 Ovozlar: {user[1]} ta\n💸 Yechib olingan: {user[2]} so'm"
        await message.answer(text, parse_mode="HTML")

@dp.message(F.text == "🔗 Referal")
async def my_referral(message: types.Message):
    bot_me = await bot.get_me()
    ref_link = f"https://t.me/{bot_me.username}?start={message.from_user.id}"
    cursor.execute("SELECT COUNT(*) FROM users WHERE referrer_id=?", (message.from_user.id,))
    ref_count = cursor.fetchone()[0]
    text = f"🔗 <b>Havolangiz:</b>\n{ref_link}\n\n👥 Takliflar: {ref_count} ta\n💵 Har biri uchun: {get_config('ref_price')} so'm"
    await message.answer(text, parse_mode="HTML", disable_web_page_preview=True)

@dp.message(F.text == "🗳 Ovoz berish")
async def vote_link_handler(message: types.Message):
    link = get_config('vote_link')
    await message.answer(f"🗳 <b>Ovoz berish uchun quyidagi havolaga o'ting:</b>\n\n{link}\n\n<i>Ovoz bergach, skrinshotni adminga yuboring.</i>", parse_mode="HTML")

@dp.message(F.text == "💸 Pul yechib olish")
async def withdraw_1(message: types.Message, state: FSMContext):
    cursor.execute("SELECT balance FROM users WHERE user_id=?", (message.from_user.id,))
    balance = cursor.fetchone()[0]
    min_w = int(get_config('min_withdraw'))
    if balance < min_w:
        return await message.answer(f"❌ Minimal yechish: {min_w} so'm\n💰 Balansingiz: {balance} so'm")
    kb = ReplyKeyboardBuilder()
    kb.row(types.KeyboardButton(text="💳 Karta"), types.KeyboardButton(text="📱 Paynet"))
    kb.row(types.KeyboardButton(text="🏠 Orqaga"))
    await message.answer("💸 <b>Usulni tanlang:</b>", reply_markup=kb.as_markup(resize_keyboard=True), parse_mode="HTML")
    await state.set_state(UserStates.withdraw_method)

@dp.message(UserStates.withdraw_method, F.text.in_(["💳 Karta", "📱 Paynet"]))
async def withdraw_2(message: types.Message, state: FSMContext):
    await state.update_data(method=message.text)
    await message.answer("💳 <b>Raqamingizni yuboring:</b>", reply_markup=ReplyKeyboardBuilder().button(text="🏠 Orqaga").as_markup(resize_keyboard=True))
    await state.set_state(UserStates.withdraw_details)

@dp.message(UserStates.withdraw_details)
async def withdraw_3(message: types.Message, state: FSMContext):
    if message.text == "🏠 Orqaga": return await back_main_handler(message, state)
    await state.update_data(details=message.text)
    await message.answer(f"💰 <b>Summani kiriting (Faqat raqam):</b>")
    await state.set_state(UserStates.withdraw_amount)

@dp.message(UserStates.withdraw_amount)
async def withdraw_4(message: types.Message, state: FSMContext):
    if not message.text.isdigit(): return await message.answer("❌ Faqat raqam kiriting!")
    amount = int(message.text)
    data = await state.get_data()
    cursor.execute("SELECT balance FROM users WHERE user_id=?", (message.from_user.id,))
    balance = cursor.fetchone()[0]
    if amount > balance: return await message.answer("❌ Balansda mablag' yetarli emas!")
    
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Tasdiqlash", callback_data=f"pay_{message.from_user.id}_{amount}")
    kb.button(text="❌ Rad etish", callback_data=f"reject_{message.from_user.id}")
    await bot.send_message(ADMIN_ID, f"💸 <b>Yangi so'rov!</b>\nID: {message.from_user.id}\nSumma: {amount}\nRekvizit: {data['details']}", reply_markup=kb.as_markup())
    await message.answer("✅ So'rov yuborildi.", reply_markup=main_menu(message.from_user.id))
    await state.clear()

@dp.message(F.text == "🚀 So'rovlar", F.from_user.id == ADMIN_ID)
async def admin_p(message: types.Message):
    await message.answer("🚀 Admin Panel:", reply_markup=admin_panel_kb())

@dp.message(F.text == "📊 Statistika", F.from_user.id == ADMIN_ID)
async def admin_st(message: types.Message):
    cursor.execute("SELECT COUNT(*) FROM users")
    u = cursor.fetchone()[0]
    await message.answer(f"📈 Foydalanuvchilar: {u}")

# --- BOTNI ISHGA TUSHIRISH ---
async def main():
    logging.basicConfig(level=logging.INFO)
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
