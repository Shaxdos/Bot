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
LOG_GROUP_ID = -1003225370008 # Guruh ID (Telegramda doim minus bilan yoziladi)

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
    cursor.execute("DELETE FROM channels WHERE url NOT LIKE 'http%' AND url NOT LIKE 't.me%'")
    
    # Yangi jadval: Ishlatilgan raqamlarni saqlash uchun
    cursor.execute('''CREATE TABLE IF NOT EXISTS used_phones (phone TEXT PRIMARY KEY)''')
    
    cursor.execute('''CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)''')
    
    default_start = ("<b>BOT AKTIV ISHLAMOQDA ✅</b>\n\n"
                     "⁉️ BOT ORQALI QANDAY QILIB OVOZ BERISH VIDEODA KO'RSATILGAN.\n\n"
                     "🎉 To'g'ri ovoz berganlarga pul shu zahoti o'tkazilmoqda!\n\n"
                     "🥳 Aziz {name}! 🗳 Ovoz berish tugmasini bosib, ovoz bering!")
    
    # MANA SHU YERDA START BILAN BIRGA TO'G'RIDAN-TO'G'RI LOYIHA LINKI QO'YILDI
    sets =[('vote_price', '5000'), ('ref_price', '1000'), 
            ('min_withdraw', '15000'), ('vote_link', 'https://t.me/ochiqbudjetbot?start=053465392013'),
            ('payment_channel', 'O\'rnatilmagan'),
            ('start_text', default_start)]
    for k, v in sets:
        cursor.execute("INSERT OR IGNORE INTO settings VALUES (?, ?)", (k, v))
        
    # Agar bazada oldin boshqa link saqlangan bo'lsa, uni avtomatik shunga yangilaymiz
    cursor.execute("UPDATE settings SET value='https://t.me/ochiqbudjetbot?start=053465392013' WHERE key='vote_link'")

    cursor.execute("SELECT value FROM settings WHERE key='start_text'")
    res = cursor.fetchone()
    if res and res[0] == '🏠 <b>Asosiy menyu:</b>\nSalom, {name}! Botimizga xush kelibsiz.':
        cursor.execute("UPDATE settings SET value=? WHERE key='start_text'", (default_start,))
        
    conn.commit()

db_setup()

# --- YORDAMCHI FUNKSIYALAR ---
def get_config(key):
    cursor.execute("SELECT value FROM settings WHERE key=?", (key,))
    res = cursor.fetchone()
    return res[0] if res else ""

def set_config(key, value):
    cursor.execute("SELECT key FROM settings WHERE key=?", (key,))
    if cursor.fetchone():
        cursor.execute("UPDATE settings SET value=? WHERE key=?", (value, key))
    else:
        cursor.execute("INSERT INTO settings VALUES (?, ?)", (key, value))
    conn.commit()

async def log_to_group(text):
    try: await bot.send_message(LOG_GROUP_ID, text)
    except Exception as e: logging.error(f"Guruhga yozishda xato: {e}")

async def check_sub(user_id):
    cursor.execute("SELECT channel_id FROM channels")
    for (ch_id,) in cursor.fetchall():
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
        try:
            await bot.send_message(ref_id, f"🎉 <b>Yangi referal!</b>\nSizning taklifingiz orqali do'stingiz botga kirdi va sizga {ref_price} so'm berildi.", parse_mode="HTML")
        except: pass

# --- FSM STATES ---
class AdminState(StatesGroup):
    broadcast_text = State()
    broadcast_forward = State()
    add_ch_id = State()
    add_ch_title = State()
    add_ch_url = State()
    change_sett = State()
    pay_channel = State()

class UserStates(StatesGroup):
    get_phone = State()
    get_screenshot = State()
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
    kb.row(types.KeyboardButton(text="📊 Statistika"), types.KeyboardButton(text="⚙️ Sozlama"))
    kb.row(types.KeyboardButton(text="/110 - To'liq statistika"), types.KeyboardButton(text="🏠 Orqaga"))
    return kb.as_markup(resize_keyboard=True)

# --- GLOBAL ORQAGA TUGMASI ---
@dp.message(F.text == "🏠 Orqaga")
async def back_main_handler(message: types.Message, state: FSMContext):
    await state.clear()
    start_msg = get_config('start_text').replace("{name}", html.escape(message.from_user.full_name))
    await message.answer(start_msg, reply_markup=main_menu(message.from_user.id), parse_mode="HTML")

# --- START VA REFERAL ---
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
            potential_ref = int(parts[1])
            if potential_ref != u_id: ref_id = potential_ref

        cursor.execute("INSERT INTO users (user_id, username, name, referrer_id) VALUES (?, ?, ?, ?)", (u_id, username, name, ref_id))
        conn.commit()
        
        # Agar odam kimgadir referal bo'lib kirsa, guruhga yozish
        if ref_id:
            cursor.execute("SELECT name FROM users WHERE user_id=?", (ref_id,))
            referrer = cursor.fetchone()
            ref_name = referrer[0] if (referrer and referrer[0]) else "Noma'lum"
            
            log_text = (f"foydalanuvchi ismi : {name}\n"
                        f"id :{u_id}\n"
                        f"Taklif qildi: {ref_name} ({ref_id})\n"
                        f"Yangi referal qo'shildi")
            await log_to_group(log_text)

    if not await check_sub(u_id):
        kb = InlineKeyboardBuilder()
        cursor.execute("SELECT title, url FROM channels")
        for t, u in cursor.fetchall(): kb.button(text=t, url=u)
        kb.button(text="✅ Tasdiqlash", callback_data="recheck")
        kb.adjust(1)
        return await message.answer("❌ <b>Botdan foydalanish uchun quyidagi kanallarga obuna bo'lishingiz shart:</b>", reply_markup=kb.as_markup(), parse_mode="HTML")

    await reward_referrer(u_id)
    
    start_msg = get_config('start_text').replace("{name}", html.escape(message.from_user.full_name))
    vid_id = get_config('start_video_id')
    
    # 1-FIX: VIDEO YUBORISH TRY-EXCEPT BILAN
    try:
        if vid_id:
            await message.answer_video(vid_id, caption=start_msg, reply_markup=main_menu(u_id), parse_mode="HTML")
        elif os.path.exists("11.mp4"):
            msg = await message.answer_video(FSInputFile("11.mp4"), caption=start_msg, reply_markup=main_menu(u_id), parse_mode="HTML", request_timeout=300)
            set_config('start_video_id', msg.video.file_id)
        else:
            await message.answer(start_msg, reply_markup=main_menu(u_id), parse_mode="HTML")
    except Exception as e:
        logging.warning(f"Keshdagi video xato berdi, qayta yuklanmoqda: {e}")
        if os.path.exists("11.mp4"):
            msg = await message.answer_video(FSInputFile("11.mp4"), caption=start_msg, reply_markup=main_menu(u_id), parse_mode="HTML", request_timeout=300)
            set_config('start_video_id', msg.video.file_id)
        else:
            await message.answer(start_msg, reply_markup=main_menu(u_id), parse_mode="HTML")

@dp.callback_query(F.data == "recheck")
async def recheck_sub(call: types.CallbackQuery):
    if not await check_sub(call.from_user.id):
        return await call.answer("❌ Hali barcha kanallarga obuna bo'lmagansiz!", show_alert=True)
    
    await call.message.delete()
    await reward_referrer(call.from_user.id)
    
    start_msg = get_config('start_text').replace("{name}", html.escape(call.from_user.full_name))
    vid_id = get_config('start_video_id')
    
    # 2-FIX: VIDEO YUBORISH TRY-EXCEPT BILAN
    try:
        if vid_id:
            await call.message.answer_video(vid_id, caption=f"✅ <b>Obuna tasdiqlandi!</b>\n\n{start_msg}", reply_markup=main_menu(call.from_user.id), parse_mode="HTML")
        elif os.path.exists("11.mp4"):
            msg = await call.message.answer_video(FSInputFile("11.mp4"), caption=f"✅ <b>Obuna tasdiqlandi!</b>\n\n{start_msg}", reply_markup=main_menu(call.from_user.id), parse_mode="HTML", request_timeout=300)
            set_config('start_video_id', msg.video.file_id)
        else:
            await call.message.answer(f"✅ <b>Obuna tasdiqlandi!</b>\n\n{start_msg}", reply_markup=main_menu(call.from_user.id), parse_mode="HTML")
    except Exception as e:
        logging.warning(f"Keshdagi video xato berdi, qayta yuklanmoqda: {e}")
        if os.path.exists("11.mp4"):
            msg = await call.message.answer_video(FSInputFile("11.mp4"), caption=f"✅ <b>Obuna tasdiqlandi!</b>\n\n{start_msg}", reply_markup=main_menu(call.from_user.id), parse_mode="HTML", request_timeout=300)
            set_config('start_video_id', msg.video.file_id)
        else:
            await call.message.answer(f"✅ <b>Obuna tasdiqlandi!</b>\n\n{start_msg}", reply_markup=main_menu(call.from_user.id), parse_mode="HTML")

# --- USER MENYU FUNKSIYALARI ---
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
    
    text = f"🔗 <b>Sizning referal havolangiz:</b>\n{ref_link}\n\n👥 Taklif qilinganlar: {ref_count} ta\n💵 Har bir do'stingiz uchun: {get_config('ref_price')} so'm olasiz!"
    await message.answer(text, parse_mode="HTML", disable_web_page_preview=True)

# Yutuqlar bo'limi (TOP ovoz to'plaganlar)
@dp.message(F.text == "🏆 Yutuqlar")
async def leaderboard_menu(message: types.Message):
    cursor.execute("SELECT name, votes FROM users WHERE votes > 0 ORDER BY votes DESC LIMIT 10")
    top_users = cursor.fetchall()
    
    text = "🏆 <b>TOP OVOZ TO'PLAGANLAR:</b>\n\n"
    
    if not top_users:
        text += "<i>Hali hech kim ovoz yig'magan. Birinchi bo'lish imkoniyati sizda!</i>\n"
    else:
        for i, (name, votes) in enumerate(top_users, 1):
            medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"{i}."
            safe_name = name if name else "Noma'lum"
            text += f"{medal} <b>{html.escape(safe_name)}</b> — {votes} ta ovoz\n"
            
    text += (
        "\n🎁 <b>OPEN BUDGET TUGAGACH TOP-1 DA TURGAN ISHTIROKCHIGA:</b>\n"
        "📱 <b>iPhone 17 Pro Max</b> sovg'a qilinadi!\n\n"
        "🚀 <i>Ko'proq ovoz to'plang va g'olib bo'ling!</i>"
    )
    
    await message.answer(text, parse_mode="HTML")

# --- PUL YECHISH TIZIMI ---
@dp.message(F.text == "💸 Pul yechib olish")
async def withdraw_1(message: types.Message, state: FSMContext):
    cursor.execute("SELECT balance FROM users WHERE user_id=?", (message.from_user.id,))
    balance = cursor.fetchone()[0]
    min_w = int(get_config('min_withdraw'))
    
    if balance < min_w:
        return await message.answer(f"❌ Hisobingizda yetarli mablag' yo'q.\n📉 Minimal yechish: {min_w} so'm\n💰 Balansingiz: {balance} so'm")
    
    kb = ReplyKeyboardBuilder()
    kb.row(types.KeyboardButton(text="💳 Karta"), types.KeyboardButton(text="📱 Paynet"))
    kb.row(types.KeyboardButton(text="🏠 Orqaga"))
    
    await message.answer("💸 <b>Pul yechish usulini tanlang:</b>", reply_markup=kb.as_markup(resize_keyboard=True), parse_mode="HTML")
    await state.set_state(UserStates.withdraw_method)
    await state.update_data(balance=balance)

@dp.message(UserStates.withdraw_method, F.text.in_(["💳 Karta", "📱 Paynet"]))
async def withdraw_2(message: types.Message, state: FSMContext):
    await state.update_data(method=message.text)
    if message.text == "💳 Karta":
        text = "💳 <b>Karta raqamingizni yuboring (Masalan: 8600...):</b>"
    else:
        text = "📱 <b>Telefon raqamingizni yuboring (Masalan: +998901234567):</b>"
    
    await message.answer(text, reply_markup=types.ReplyKeyboardMarkup(keyboard=[[types.KeyboardButton(text="🏠 Orqaga")]], resize_keyboard=True), parse_mode="HTML")
    await state.set_state(UserStates.withdraw_details)

@dp.message(UserStates.withdraw_details)
async def withdraw_3(message: types.Message, state: FSMContext):
    await state.update_data(details=message.text)
    data = await state.get_data()
    balance = data['balance']
    
    await message.answer(f"💰 <b>Qancha pul yechmoqchisiz?</b>\n\nSizning balansingiz: {balance} so'm\n📉 Minimal yechish: {get_config('min_withdraw')} so'm\n\n<i>Faqat raqam bilan kiriting (Masalan: 15000):</i>", parse_mode="HTML")
    await state.set_state(UserStates.withdraw_amount)

@dp.message(UserStates.withdraw_amount)
async def withdraw_4(message: types.Message, state: FSMContext):
    if not message.text.isdigit():
        return await message.answer("❌ Iltimos, faqat raqam kiriting!")
    
    amount = int(message.text)
    data = await state.get_data()
    balance = data['balance']
    min_w = int(get_config('min_withdraw'))
    
    if amount < min_w:
        return await message.answer(f"❌ Minimal yechish miqdori: {min_w} so'm")
    if amount > balance:
        return await message.answer(f"❌ Hisobingizda buncha mablag' yo'q. Balansingiz: {balance} so'm")
    
    uid = message.from_user.id
    name = message.from_user.full_name
    method = data['method']
    details = data['details']
    
    # Adminga jo'natish (Tasdiqlash uchun)
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Tasdiqlash", callback_data=f"w_ok_{uid}_{amount}")
    kb.button(text="❌ Rad etish", callback_data=f"w_no_{uid}")
    kb.adjust(1)
    
    text = f"💸 <b>Yangi pul yechish so'rovi!</b>\n\n👤 Foydalanuvchi: <a href='tg://user?id={uid}'>{html.escape(name)}</a>\nUsul: {method}\nRekvizit: <code>{details}</code>\n💰 Summa so'ralgan: {amount} so'm"
    await bot.send_message(ADMIN_ID, text, reply_markup=kb.as_markup(), parse_mode="HTML")
    
    await message.answer("✅ <b>So'rovingiz adminga yuborildi. Tasdiqlangandan so'ng hisobingizdan yechiladi!</b>", reply_markup=main_menu(uid), parse_mode="HTML")
    await state.clear()

# --- ADMIN PANEL FUNKSIYALARI ---
@dp.message(F.text == "🚀 So'rovlar", F.from_user.id == ADMIN_ID)
async def admin_panel(message: types.Message):
    await message.answer("🚀 <b>Admin Paneliga xush kelibsiz!</b>", reply_markup=admin_panel_kb(), parse_mode="HTML")

@dp.message(F.text == "📊 Statistika", F.from_user.id == ADMIN_ID)
async def admin_stats(message: types.Message):
    cursor.execute("SELECT COUNT(*), SUM(balance), SUM(votes) FROM users")
    u, b, v = cursor.fetchone()
    await message.answer(f"📈 <b>Statistika:</b>\n\n👤 Foydalanuvchilar: {u}\n💰 Umumiy balanslar: {b or 0} so'm\n🗳 Jami ovozlar: {v or 0} ta", parse_mode="HTML")

@dp.message(F.text == "/110 - To'liq statistika", F.from_user.id == ADMIN_ID)
async def full_stats(message: types.Message):
    cursor.execute("SELECT COUNT(*), SUM(balance), SUM(votes), SUM(withdrawn) FROM users")
    u, b, v, w = cursor.fetchone()
    db_file = FSInputFile("open_budget_pro.db")
    text = (f"📈 <b>To'liq statistika:</b>\n\n👤 Foydalanuvchilar: {u}\n💰 Qoldiq balanslar: {b or 0} so'm\n"
            f"🗳 Jami ovozlar: {v or 0} ta\n💸 Jami to'langan: {w or 0} so'm\n\n📂 <b>Baza fayli:</b>")
    await message.answer_document(document=db_file, caption=text, parse_mode="HTML")

@dp.message(F.text == "✉️ Oddiy xabar", F.from_user.id == ADMIN_ID)
async def br_1(message: types.Message, state: FSMContext):
    await message.answer("Barcha foydalanuvchilarga yuboriladigan matnni kiriting:", reply_markup=types.ReplyKeyboardMarkup(keyboard=[[types.KeyboardButton(text="🏠 Orqaga")]], resize_keyboard=True))
    await state.set_state(AdminState.broadcast_text)

@dp.message(AdminState.broadcast_text)
async def br_2(message: types.Message, state: FSMContext):
    cursor.execute("SELECT user_id FROM users")
    users = cursor.fetchall()
    count = 0
    await message.answer("Xabar yuborilmoqda...")
    for (uid,) in users:
        try:
            await bot.send_message(uid, message.text, parse_mode="HTML")
            count += 1
            await asyncio.sleep(0.05)
        except: continue
    await message.answer(f"✅ {count} kishiga yuborildi.", reply_markup=admin_panel_kb())
    await state.clear()

@dp.message(F.text == "📩 Forward xabar", F.from_user.id == ADMIN_ID)
async def br_fwd_1(message: types.Message, state: FSMContext):
    await message.answer("Barcha foydalanuvchilarga forward qilinadigan xabarni yuboring:", reply_markup=types.ReplyKeyboardMarkup(keyboard=[[types.KeyboardButton(text="🏠 Orqaga")]], resize_keyboard=True))
    await state.set_state(AdminState.broadcast_forward)

@dp.message(AdminState.broadcast_forward)
async def br_fwd_2(message: types.Message, state: FSMContext):
    cursor.execute("SELECT user_id FROM users")
    users = cursor.fetchall()
    count = 0
    await message.answer("Xabar yuborilmoqda...")
    for (uid,) in users:
        try:
            await message.forward(chat_id=uid)
            count += 1
            await asyncio.sleep(0.05)
        except: continue
    await message.answer(f"✅ {count} kishiga forward qilindi.", reply_markup=admin_panel_kb())
    await state.clear()

@dp.message(F.text == "📤 To'lovlar kanali", F.from_user.id == ADMIN_ID)
async def set_pay_ch_1(message: types.Message, state: FSMContext):
    await message.answer("To'lovlar kanali linkini yuboring (Masalan: @tolovlar yoki https://...):", reply_markup=types.ReplyKeyboardMarkup(keyboard=[[types.KeyboardButton(text="🏠 Orqaga")]], resize_keyboard=True))
    await state.set_state(AdminState.pay_channel)

@dp.message(AdminState.pay_channel)
async def set_pay_ch_2(message: types.Message, state: FSMContext):
    set_config('payment_channel', message.text)
    await message.answer("✅ To'lovlar kanali saqlandi!", reply_markup=admin_panel_kb())
    await state.clear()

@dp.message(F.text == "📄 Ulangan kanallar", F.from_user.id == ADMIN_ID)
async def ch_list(message: types.Message):
    cursor.execute("SELECT title, channel_id FROM channels")
    rows = cursor.fetchall()
    if not rows: return await message.answer("Hali kanallar ulanmagan.")
    text = "🔗 <b>Ulangan kanallar:</b>\n\n"
    for t, cid in rows: text += f"🔹 {t} ({cid})\n"
    await message.answer(text, parse_mode="HTML")

@dp.message(F.text == "📢 Kanal ulash", F.from_user.id == ADMIN_ID)
async def ch_add_1(message: types.Message, state: FSMContext):
    await message.answer("Kanal ID sini yuboring (Masalan: -100...):", reply_markup=types.ReplyKeyboardMarkup(keyboard=[[types.KeyboardButton(text="🏠 Orqaga")]], resize_keyboard=True))
    await state.set_state(AdminState.add_ch_id)

@dp.message(AdminState.add_ch_id)
async def ch_add_2(message: types.Message, state: FSMContext):
    await state.update_data(cid=message.text)
    await message.answer("Kanal nomini kiriting:")
    await state.set_state(AdminState.add_ch_title)

@dp.message(AdminState.add_ch_title)
async def ch_add_3(message: types.Message, state: FSMContext):
    await state.update_data(title=message.text)
    await message.answer("Kanal linkini yuboring (https://t.me/...):")
    await state.set_state(AdminState.add_ch_url)

@dp.message(AdminState.add_ch_url)
async def ch_add_fin(message: types.Message, state: FSMContext):
    if not (message.text.startswith("http://") or message.text.startswith("https://") or message.text.startswith("t.me/")):
        return await message.answer("❌ <b>Xato havola!</b>\nIltimos, havola <code>https://...</code> yoki <code>t.me/...</code> ko'rinishida bo'lishiga ishonch hosil qiling va qayta yuboring:", parse_mode="HTML")

    data = await state.get_data()
    cursor.execute("INSERT INTO channels (channel_id, title, url) VALUES (?, ?, ?)", (data['cid'], data['title'], message.text))
    conn.commit()
    await message.answer("✅ Kanal muvaffaqiyatli qo'shildi!", reply_markup=admin_panel_kb())
    await state.clear()

@dp.message(F.text == "🔇 Kanal uzish", F.from_user.id == ADMIN_ID)
async def ch_del_list(message: types.Message):
    cursor.execute("SELECT id, title FROM channels")
    chans = cursor.fetchall()
    if not chans: return await message.answer("Kanal topilmadi.")
    kb = InlineKeyboardBuilder()
    for id, title in chans: kb.button(text=f"❌ {title}", callback_data=f"del_{id}")
    kb.adjust(1)
    await message.answer("O'chirmoqchi bo'lgan kanalni tanlang:", reply_markup=kb.as_markup())

@dp.callback_query(F.data.startswith("del_"))
async def ch_del_confirm(call: types.CallbackQuery):
    idx = call.data.split("_")[1]
    cursor.execute("DELETE FROM channels WHERE id=?", (idx,))
    conn.commit()
    await call.message.delete()
    await call.message.answer("✅ Kanal o'chirildi.")

# CHALA QOLGAN ADMIN SOZLAMALAR QISMI TO'LDIRILDI
@dp.message(F.text == "⚙️ Sozlama", F.from_user.id == ADMIN_ID)
async def admin_settings(message: types.Message):
    kb = InlineKeyboardBuilder()
    kb.button(text="🗳 Ovoz narxi", callback_data="set_vote_price")
    kb.button(text="🔗 Referal narxi", callback_data="set_ref_price")
    kb.button(text="📉 Min yechish", callback_data="set_min_withdraw")
    kb.button(text="📝 Start matni", callback_data="set_start_text")
    kb.adjust(1)
    
    current_start = get_config('start_text')
    if len(current_start) > 100: current_start = current_start[:100] + "..."
    
    text = (f"⚙️ <b>Hozirgi sozlamalar:</b>\n\n"
            f"🗳 Ovoz: {get_config('vote_price')} so'm\n"
            f"🔗 Referal: {get_config('ref_price')} so'm\n"
            f"📉 Minimal yechish: {get_config('min_withdraw')} so'm\n\n"
            f"📝 <b>Start matni:</b>\n{current_start}")
            
    await message.answer(text, reply_markup=kb.as_markup(), parse_mode="HTML")

# BOTNI ISHGA TUSHIRISH FUNKSIYASI QO'SHILDI
async def main():
    # Eski webhooklarni o'chirib tashlash (bot ishga tushganda konflikt bermasligi uchun)
    await bot.delete_webhook(drop_pending_updates=True)
    # Botni polling rejimida ishga tushirish
    await dp.start_polling(bot)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Bot to'xtatildi!")