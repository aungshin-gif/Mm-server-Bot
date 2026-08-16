import asyncio
import logging
import os
import re
import sqlite3
from datetime import datetime
from html import escape

import aiohttp
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    MessageEntity,
    ReplyKeyboardMarkup,
)

logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
SUPPORT_USERNAME = os.getenv("SUPPORT_USERNAME", "")
DB_PATH = os.getenv("DB_PATH", "bot.db")
ROLECHECK_URL = "https://www.gameshopbot.online/mlbb_checkrole-main/api/games/mlbb_checkrole"

PRODUCT_NAME = "Weekly Pass"
PRODUCT_PRICE = 6000
KBZPAY_NUMBER = "09795687480"
KBZPAY_NAME = "Aung Shin Thant Htun"

router = Router()


class UserFlow(StatesGroup):
    waiting_region_input = State()
    waiting_player_id = State()
    waiting_zone_id = State()
    waiting_payment_screenshot = State()


def db():
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def init_db():
    with db() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                username TEXT,
                player_id TEXT NOT NULL,
                zone_id TEXT NOT NULL,
                product TEXT NOT NULL,
                amount INTEGER NOT NULL,
                payment_file_id TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL,
                approved_at TEXT
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS emojis (
                label TEXT PRIMARY KEY,
                custom_emoji_id TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)


def main_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Products"), KeyboardButton(text="Region စစ်ရန်")],
            [KeyboardButton(text="Support")],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


def product_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="ဝယ်မည် - 6,000 Ks", callback_data="buy_weekly")],
    ])


def admin_only(user_id: int) -> bool:
    return ADMIN_ID != 0 and user_id == ADMIN_ID


def get_custom_emoji(label: str):
    with db() as con:
        row = con.execute("SELECT custom_emoji_id FROM emojis WHERE label = ?", (label,)).fetchone()
    return row["custom_emoji_id"] if row else None


def extract_custom_emoji_id(message: Message):
    source = message.reply_to_message or message
    entities = source.entities or []
    for entity in entities:
        if entity.type == "custom_emoji":
            return entity.custom_emoji_id
    if source.caption_entities:
        for entity in source.caption_entities:
            if entity.type == "custom_emoji":
                return entity.custom_emoji_id
    return None


def emoji_text(label: str, fallback: str):
    custom_id = get_custom_emoji(label)
    if not custom_id:
        return fallback, None
    text = "●"
    entity = MessageEntity(type="custom_emoji", offset=0, length=1, custom_emoji_id=custom_id)
    return text, [entity]


@router.message(Command("start"))
async def start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "မင်္ဂလာပါ။ MLBB Myanmar Server Top-up Bot မှ ကြိုဆိုပါတယ်။\n\n"
        "အောက်က menu ကနေ Products, Region စစ်ရန် သို့မဟုတ် Support ကိုရွေးပါ။",
        reply_markup=main_keyboard(),
    )


@router.message(F.text == "Products")
@router.message(Command("products"))
async def products(message: Message):
    await message.answer(
        "📦 Available Product\n\n"
        f"• {PRODUCT_NAME}\n"
        f"• Price: {PRODUCT_PRICE:,} Ks\n\n"
        "ဝယ်ယူရန် အောက်က button ကိုနှိပ်ပါ။",
        reply_markup=product_keyboard(),
    )


@router.callback_query(F.data == "buy_weekly")
async def buy_weekly(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(UserFlow.waiting_player_id)
    await callback.message.answer(
        f"{PRODUCT_NAME} order စတင်ပါမယ်။\n\n"
        "MLBB User ID ကို ဂဏန်းသီးသန့်ပို့ပါ။\n"
        "ဥပမာ: 651256402"
    )


@router.message(UserFlow.waiting_player_id)
async def receive_player_id(message: Message, state: FSMContext):
    value = (message.text or "").strip()
    if not value.isdigit():
        await message.answer("User ID က ဂဏန်းသီးသန့် ဖြစ်ရပါမယ်။ ပြန်ပို့ပါ။")
        return
    await state.update_data(player_id=value)
    await state.set_state(UserFlow.waiting_zone_id)
    await message.answer("MLBB Server ID / Zone ID ကို ဂဏန်းသီးသန့်ပို့ပါ။\nဥပမာ: 8592")


@router.message(UserFlow.waiting_zone_id)
async def receive_zone_id(message: Message, state: FSMContext):
    zone = (message.text or "").strip()
    if not zone.isdigit():
        await message.answer("Zone ID က ဂဏန်းသီးသန့် ဖြစ်ရပါမယ်။ ပြန်ပို့ပါ။")
        return
    data = await state.get_data()
    player_id = data["player_id"]
    await state.update_data(zone_id=zone)
    await state.set_state(UserFlow.waiting_payment_screenshot)
    await message.answer(
        f"Order အချက်အလက်\n\n"
        f"Product: {PRODUCT_NAME}\n"
        f"Player ID: {player_id}\n"
        f"Zone ID: {zone}\n"
        f"Amount: {PRODUCT_PRICE:,} Ks\n\n"
        f"KBZPay ဖြင့် ငွေလွှဲရန်\n"
        f"ဖုန်း: <code>{KBZPAY_NUMBER}</code>\n"
        f"အမည်: <b>{escape(KBZPAY_NAME)}</b>\n\n"
        "ငွေလွှဲပြီးပါက screenshot ကို ဒီ chat ထဲ ပို့ပါ။ Screenshot မပို့မချင်း order မစစ်ဆေးနိုင်ပါ။"
    )


@router.message(UserFlow.waiting_payment_screenshot, F.photo)
async def receive_payment(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    if not data.get("player_id") or not data.get("zone_id"):
        await state.clear()
        await message.answer("Order session မတွေ့တော့ပါ။ /start နဲ့ ပြန်စပါ။")
        return
    photo_id = message.photo[-1].file_id
    now = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    with db() as con:
        cursor = con.execute(
            """INSERT INTO orders
               (user_id, username, player_id, zone_id, product, amount, payment_file_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (message.from_user.id, message.from_user.username or "", data["player_id"],
             data["zone_id"], PRODUCT_NAME, PRODUCT_PRICE, photo_id, now),
        )
        order_id = cursor.lastrowid
    await state.clear()
    await message.answer(
        f"ငွေပေးချေမှု screenshot လက်ခံရရှိပါပြီ။\nOrder ID: <code>#{order_id}</code>\n\n"
        "Admin စစ်ဆေးပြီးနောက် အကြောင်းပြန်ပါမယ်။ ထပ်မံငွေမလွှဲပါနဲ့။",
        reply_markup=main_keyboard(),
    )
    if ADMIN_ID:
        admin_text = (
            f"အသစ်ဝင်လာသော Order #{order_id}\n"
            f"User: @{escape(message.from_user.username or 'no_username')}\n"
            f"Telegram ID: <code>{message.from_user.id}</code>\n"
            f"Player ID: <code>{data['player_id']}</code>\n"
            f"Zone ID: <code>{data['zone_id']}</code>\n"
            f"Amount: {PRODUCT_PRICE:,} Ks\n\n"
            f"Approve: <code>/approve {order_id}</code>\n"
            f"Reject: <code>/reject {order_id}</code>"
        )
        await bot.send_photo(ADMIN_ID, photo_id, caption=admin_text)


@router.message(UserFlow.waiting_payment_screenshot)
async def payment_not_photo(message: Message):
    await message.answer("Payment screenshot ကို ပုံအဖြစ်ပို့ပေးပါ။")


@router.message(F.text == "Region စစ်ရန်")
@router.message(Command("region"))
async def region_start(message: Message, state: FSMContext):
    await state.set_state(UserFlow.waiting_region_input)
    await message.answer(
        "MLBB User ID နဲ့ Server/Zone ID ကို space ခြားပြီးပို့ပါ။\n"
        "ဥပမာ: <code>651256402 8592</code>"
    )


@router.message(UserFlow.waiting_region_input)
async def region_check(message: Message, state: FSMContext):
    parts = (message.text or "").strip().split()
    if len(parts) != 2 or not all(part.isdigit() for part in parts):
        await message.answer("ပုံစံမှားနေပါတယ်။ ဥပမာ <code>651256402 8592</code> လို့ပို့ပါ။")
        return
    player_id, zone_id = parts
    await message.answer("စစ်ဆေးနေပါတယ်။ ခဏစောင့်ပါ…")
    timeout = aiohttp.ClientTimeout(total=12)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(ROLECHECK_URL, params={"id": player_id, "zone": zone_id}) as response:
                if response.status != 200:
                    raise RuntimeError(f"HTTP {response.status}")
                payload = await response.json(content_type=None)
        if payload.get("status") != "success":
            await message.answer("ဒီ ID/Zone ကို စစ်ဆေးလို့မရပါ။ ID နဲ့ Zone ပြန်စစ်ပြီး ထပ်ကြိုးစားပါ။")
            return
        player = payload.get("data", {}).get("player", {})
        stats = payload.get("data", {}).get("double_diamond_stats", {})
        region = player.get("region", "Unknown")
        region_display = "Myanmar (MM)" if str(region).upper() in {"MM", "MYANMAR"} else str(region)
        await message.answer(
            "✅ Region Check Result\n\n"
            f"Name: <b>{escape(str(player.get('name', 'Unknown')))}</b>\n"
            f"User ID: <code>{escape(str(player.get('id', player_id)))}</code>\n"
            f"Zone ID: <code>{escape(str(player.get('zone', zone_id)))}</code>\n"
            f"Region: <b>{escape(region_display)}</b>\n\n"
            f"Double Diamond: {escape(str(stats.get('overall', 'Unknown')))}"
        )
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, RuntimeError):
        await message.answer("Region API ခဏမရနိုင်ပါ။ ခဏနားပြီး ပြန်စမ်းပါ။")
    finally:
        await state.clear()


@router.message(F.text == "Support")
async def support(message: Message):
    if SUPPORT_USERNAME:
        await message.answer(f"Support: https://t.me/{SUPPORT_USERNAME.lstrip('@')}")
    else:
        await message.answer("Support အတွက် Admin ကို Telegram မှာ ဆက်သွယ်ပါ။")


@router.message(Command("saveemoji"))
async def save_emoji(message: Message):
    if not admin_only(message.from_user.id):
        return
    args = (message.text or "").split(maxsplit=1)
    if len(args) != 2 or not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", args[1].strip()):
        await message.answer("အသုံးပြုပုံ: custom emoji message ကို reply လုပ်ပြီး <code>/saveemoji success</code> လို့ပို့ပါ။")
        return
    emoji_id = extract_custom_emoji_id(message)
    if not emoji_id:
        await message.answer("Reply လုပ်ထားတဲ့ message ထဲမှာ Premium custom emoji မတွေ့ပါ။")
        return
    label = args[1].strip().lower()
    now = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    with db() as con:
        con.execute(
            "INSERT INTO emojis(label, custom_emoji_id, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(label) DO UPDATE SET custom_emoji_id=excluded.custom_emoji_id, updated_at=excluded.updated_at",
            (label, emoji_id, now),
        )
    await message.answer(f"Emoji သိမ်းပြီးပါပြီ။ label: <code>{label}</code> id: <code>{emoji_id}</code>")


@router.message(Command("emojis"))
async def list_emojis(message: Message):
    if not admin_only(message.from_user.id):
        return
    with db() as con:
        rows = con.execute("SELECT label, custom_emoji_id FROM emojis ORDER BY label").fetchall()
    if not rows:
        await message.answer("Emoji မသိမ်းရသေးပါ။")
        return
    text = "Saved emojis\n\n" + "\n".join(f"{row['label']}: <code>{row['custom_emoji_id']}</code>" for row in rows)
    await message.answer(text)


@router.message(Command("approve"))
async def approve_order(message: Message, bot: Bot):
    if not admin_only(message.from_user.id):
        return
    args = (message.text or "").split(maxsplit=1)
    if len(args) != 2 or not args[1].isdigit():
        await message.answer("အသုံးပြုပုံ: <code>/approve ORDER_ID</code>")
        return
    order_id = int(args[1])
    with db() as con:
        order = con.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
        if order:
            con.execute("UPDATE orders SET status='approved', approved_at=? WHERE id=?", (datetime.utcnow().isoformat(timespec="seconds") + "Z", order_id))
    if not order:
        await message.answer("Order မတွေ့ပါ။")
        return
    await bot.send_message(order["user_id"], f"✅ Order #{order_id} ကို approve လုပ်ပြီးပါပြီ။\n{PRODUCT_NAME} top-up ကို admin က ဆက်လက်လုပ်ဆောင်ပေးပါမယ်။")
    await message.answer(f"Order #{order_id} approved ပါပြီ။")


@router.message(Command("reject"))
async def reject_order(message: Message, bot: Bot):
    if not admin_only(message.from_user.id):
        return
    args = (message.text or "").split(maxsplit=1)
    if len(args) != 2 or not args[1].isdigit():
        await message.answer("အသုံးပြုပုံ: <code>/reject ORDER_ID</code>")
        return
    order_id = int(args[1])
    with db() as con:
        order = con.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
        if order:
            con.execute("UPDATE orders SET status='rejected' WHERE id=?", (order_id,))
    if not order:
        await message.answer("Order မတွေ့ပါ။")
        return
    await bot.send_message(order["user_id"], f"❌ Order #{order_id} ကို reject လုပ်ထားပါတယ်။ Payment အချက်အလက် မရှင်းလင်းပါက Support ကို ဆက်သွယ်ပါ။")
    await message.answer(f"Order #{order_id} rejected ပါပြီ။")


async def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is missing. Put it in .env or server environment.")
    if ADMIN_ID == 0:
        raise RuntimeError("ADMIN_ID is missing. Put your Telegram numeric ID in .env.")
    init_db()
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_router(router)
    await bot.delete_webhook(drop_pending_updates=True)
    logging.info("Bot started")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
