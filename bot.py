
import asyncio
import logging
import os
import re
import sqlite3
from datetime import datetime
from html import escape

from aiohttp import web
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

CUSTOM_EMOJIS = {
    "region": "5447410659077661506",
    "checked": "6172491616423514581",
    "products": "5231012545799666522",
    "weekly_pass": "6172491616423514581",
    "diamond": "5427168083074628963",
    "kbzpay": "6217312653879024991",
    "welcome": "5409109841538994759",
    "order_info": "5397916757333654639",
    "amount": "5402186569006210455",
    "region_result": "5206607081334906820",
    "name": "5391112412445288650",
    "fast_delivery": "5456140674028019486",
    "stock": "5416081784641168838",
    "secure_checkout": "5296369303661067030",
    "catalogue": "5461117441612462242",
    "feedback": "5253742260054409879",
    "cancel": "5210952531676504517",
}

router = Router()


class UserFlow(StatesGroup):
    waiting_region_input = State()
    waiting_player_id = State()
    waiting_zone_id = State()
    waiting_payment_screenshot = State()
    waiting_feedback = State()


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


def welcome_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="Start",
            callback_data="go_start",
            style="primary",
            icon_custom_emoji_id=CUSTOM_EMOJIS["welcome"],
        )],
    ])


def main_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Products", callback_data="menu_products", style="danger", icon_custom_emoji_id=CUSTOM_EMOJIS["products"])],
        [InlineKeyboardButton(text="Region Check", callback_data="menu_region", style="success", icon_custom_emoji_id=CUSTOM_EMOJIS["region"])],
        [InlineKeyboardButton(text="Support", callback_data="menu_support", style="primary", icon_custom_emoji_id=CUSTOM_EMOJIS["welcome"])],
        [InlineKeyboardButton(text="Feedback", callback_data="menu_feedback", style="primary", icon_custom_emoji_id=CUSTOM_EMOJIS["feedback"])],
    ])


def back_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Back", callback_data="back_menu", style="primary", icon_custom_emoji_id=CUSTOM_EMOJIS["welcome"])],
    ])


def order_admin_keyboard(order_id: int):
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Approve", callback_data=f"approve_order:{order_id}", style="success", icon_custom_emoji_id=CUSTOM_EMOJIS["checked"]),
            InlineKeyboardButton(text="Reject", callback_data=f"reject_order:{order_id}", style="danger", icon_custom_emoji_id=CUSTOM_EMOJIS["cancel"]),
        ]
    ])


def product_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Weekly Pass • 6,000 Ks • Stock: 1", callback_data="buy_weekly", style="danger", icon_custom_emoji_id=CUSTOM_EMOJIS["weekly_pass"])],
        [InlineKeyboardButton(text="Main Menu", callback_data="back_menu", style="primary", icon_custom_emoji_id=CUSTOM_EMOJIS["welcome"])],
    ])


def cancel_order_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Cancel Order", callback_data="cancel_order", style="danger", icon_custom_emoji_id=CUSTOM_EMOJIS["cancel"])],
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
    custom_id = CUSTOM_EMOJIS.get(label) or get_custom_emoji(label)
    if not custom_id:
        return "", None
    text = "😀"
    entity = MessageEntity(type="custom_emoji", offset=0, length=2, custom_emoji_id=str(custom_id))
    return text, [entity]


def custom_prefix(label: str, body: str, fallback: str):
    prefix, entities = emoji_text(label, fallback)
    if entities:
        return f"{prefix} {body}", entities
    return body, None


def custom_lines(lines):
    """Build plain text lines with one custom emoji entity at each line start."""
    text_parts = []
    entities = []
    offset_units = 0
    for label, body, fallback in lines:
        prefix, _ = emoji_text(label, fallback)
        line = f"{prefix} {body}" if prefix else body
        text_parts.append(line)
        custom_id = CUSTOM_EMOJIS.get(label) or get_custom_emoji(label)
        if custom_id:
            entities.append(MessageEntity(
                type="custom_emoji",
                offset=offset_units,
                length=2,
                custom_emoji_id=str(custom_id),
            ))
        offset_units += len((line + "\n").encode("utf-16-le")) // 2
    return "\n".join(text_parts), entities


@router.message(Command("start"))
async def start(message: Message, state: FSMContext):
    await state.clear()
    welcome_text, welcome_entities = custom_prefix(
        "welcome",
        "<b>Gamepay Hub ရဲ့ အပျင်းပြေ Bot မှ ကြိုဆိုပါတယ်။</b>\n\n"
        "ဒီ Bot မှာ MLBB Myanmar Server diamond နှင့် Region စစ်ဆေးခြင်း service ရရှိနိုင်ပါတယ်။\n\n"
        "အောက်က card menu မှာ လိုချင်တဲ့ service ကိုရွေးပါ။",
        "🎮",
    )
    await message.answer(welcome_text, entities=welcome_entities, reply_markup=welcome_keyboard())


@router.callback_query(F.data == "go_start")
async def go_start(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    menu_text, menu_entities = custom_lines([
        ("products", "Products", ""),
        ("region", "Region Check", ""),
        ("welcome", "Support", ""),
        ("feedback", "Feedback", ""),
    ])
    await callback.message.answer(menu_text, entities=menu_entities, reply_markup=main_keyboard(), parse_mode=None)


@router.callback_query(F.data == "menu_products")
async def menu_products(callback: CallbackQuery):
    await callback.answer()
    await products(callback.message)


@router.callback_query(F.data == "back_menu")
async def back_menu(callback: CallbackQuery):
    await callback.answer()
    menu_text, menu_entities = custom_prefix("welcome", "Main Menu", "🎮")
    await callback.message.answer(menu_text, entities=menu_entities, reply_markup=main_keyboard(), parse_mode=None)


@router.message(F.text == "Products")
@router.message(Command("products"))
async def products(message: Message):
    product_text, product_entities = custom_lines([
        ("catalogue", "GAMEPAY HUB CATALOGUE", "📦"),
        ("products", "━━━━━━━━━━━━━━━━", "•"),
        ("products", "ဝယ်ယူလိုသော product ကို အောက်က card မှာရွေးပါ။", "📦"),
        ("fast_delivery", "Fast delivery", "⚡"),
        ("stock", "Stock ရှိပါသည်", "🟢"),
        ("secure_checkout", "Secure checkout", ""),
        ("weekly_pass", "Weekly Pass • 6,000 Ks • Stock: 1", ""),
    ])
    await message.answer(product_text, entities=product_entities, reply_markup=product_keyboard(), parse_mode=None)


@router.callback_query(F.data == "buy_weekly")
async def buy_weekly(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(UserFlow.waiting_player_id)
    await callback.message.answer(
        f"{PRODUCT_NAME} order စတင်ပါမယ်။\n\n"
        "MLBB User ID ကို ဂဏန်းသီးသန့်ပို့ပါ။\n"
        "ဥပမာ: 651256402",
        reply_markup=cancel_order_keyboard(),
    )


@router.message(UserFlow.waiting_player_id)
async def receive_player_id(message: Message, state: FSMContext):
    value = (message.text or "").strip()
    if not value.isdigit():
        await message.answer("User ID က ဂဏန်းသီးသန့် ဖြစ်ရပါမယ်။ ပြန်ပို့ပါ။", reply_markup=cancel_order_keyboard())
        return
    await state.update_data(player_id=value)
    await state.set_state(UserFlow.waiting_zone_id)
    await message.answer("MLBB Server ID / Zone ID ကို ဂဏန်းသီးသန့်ပို့ပါ။\nဥပမာ: 8592", reply_markup=cancel_order_keyboard())


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
    order_text, order_entities = custom_lines([
        ("order_info", "Order အချက်အလက်", "🧾"),
        ("weekly_pass", f"Product: {PRODUCT_NAME}", "💎"),
        ("name", f"Player ID: {player_id}", "👤"),
        ("region", f"Zone ID: {zone}", "🌍"),
        ("amount", f"Amount: {PRODUCT_PRICE:,} Ks", "💰"),
        ("kbzpay", "KBZPay ဖြင့် ငွေလွှဲရန်", "💳"),
        ("kbzpay", f"ဖုန်း: {KBZPAY_NUMBER}", "📱"),
        ("kbzpay", f"အမည်: {KBZPAY_NAME}", "👤"),
        ("amount", "ငွေလွှဲပြီးပါက screenshot ကို ဒီ chat ထဲ ပို့ပါ။", "💰"),
    ])
    await message.answer(order_text, entities=order_entities, reply_markup=cancel_order_keyboard(), parse_mode=None)


@router.callback_query(F.data == "cancel_order")
async def cancel_order(callback: CallbackQuery, state: FSMContext):
    await callback.answer("Order cancelled")
    await state.clear()
    await callback.message.answer("Order ကို cancel လုပ်ပြီးပါပြီ။", reply_markup=main_keyboard())


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
        await bot.send_photo(ADMIN_ID, photo_id, caption=admin_text, reply_markup=order_admin_keyboard(order_id))


@router.message(UserFlow.waiting_payment_screenshot)
async def payment_not_photo(message: Message):
    await message.answer("Payment screenshot ကို ပုံအဖြစ်ပို့ပေးပါ။")


@router.callback_query(F.data == "menu_region")
async def menu_region(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await region_start(callback.message, state)


@router.message(F.text == "Region စစ်ရန်")
@router.message(Command("region"))
async def region_start(message: Message, state: FSMContext):
    await state.set_state(UserFlow.waiting_region_input)
    region_text, region_entities = custom_prefix(
        "region",
        "MLBB User ID နဲ့ Server/Zone ID ကို space ခြားပြီးပို့ပါ။\n"
        "ဥပမာ: 651256402 8592",
        "🌍",
    )
    await message.answer(region_text, entities=region_entities, reply_markup=back_keyboard())


@router.message(UserFlow.waiting_region_input)
async def region_check(message: Message, state: FSMContext):
    parts = (message.text or "").strip().split()
    if len(parts) != 2 or not all(part.isdigit() for part in parts):
        await message.answer("ပုံစံမှားနေပါတယ်။ ဥပမာ 651256402 8592 လို့ပို့ပါ။", reply_markup=back_keyboard())
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
            await message.answer("ဒီ ID/Zone ကို စစ်ဆေးလို့မရပါ။ ID နဲ့ Zone ပြန်စစ်ပြီး ထပ်ကြိုးစားပါ။", reply_markup=back_keyboard())
            return
        player = payload.get("data", {}).get("player", {})
        stats = payload.get("data", {}).get("double_diamond_stats", {})
        region = player.get("region", "Unknown")
        region_display = "Myanmar (MM)" if str(region).upper() in {"MM", "MYANMAR"} else str(region)
        result_text, result_entities = custom_lines([
            ("region_result", "Region Check Result", "✅"),
            ("name", f"Name: {player.get('name', 'Unknown')}", "👤"),
            ("name", f"User ID: {player.get('id', player_id)}", "🆔"),
            ("region", f"Zone ID: {player.get('zone', zone_id)}", "🌍"),
            ("region", f"Region: {region_display}", "🌍"),
            ("checked", f"Double Diamond: {stats.get('overall', 'Unknown')}", "✅"),
        ])
        await message.answer(result_text, entities=result_entities, reply_markup=back_keyboard(), parse_mode=None)
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, RuntimeError):
        await message.answer("Region API ခဏမရနိုင်ပါ။ ခဏနားပြီး ပြန်စမ်းပါ။", reply_markup=back_keyboard())
    finally:
        await state.clear()


@router.callback_query(F.data == "menu_support")
async def menu_support(callback: CallbackQuery):
    await callback.answer()
    await support(callback.message)


@router.message(F.text == "Support")
async def support(message: Message):
    if SUPPORT_USERNAME:
        support_text, support_entities = custom_prefix("welcome", f"Support: https://t.me/{SUPPORT_USERNAME.lstrip('@')}", "🛟")
    else:
        support_text, support_entities = custom_prefix("welcome", "Support အတွက် Admin ကို Telegram မှာ ဆက်သွယ်ပါ။", "🛟")
    await message.answer(support_text, entities=support_entities, reply_markup=back_keyboard(), parse_mode=None)


@router.callback_query(F.data == "menu_feedback")
async def menu_feedback(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(UserFlow.waiting_feedback)
    feedback_text, feedback_entities = custom_lines([
        ("feedback", "Feedback ပို့ချင်တာကို ဒီ chat ထဲမှာ စာ၊ ပုံ သို့မဟုတ် video အဖြစ် ပို့ပါ။", "✍️"),
        ("feedback", "သင့်အမည်နဲ့ Telegram ID ကို admin ဆီ အတူပို့ပေးပါမယ်။", "📩"),
    ])
    await callback.message.answer(feedback_text, entities=feedback_entities, reply_markup=back_keyboard(), parse_mode=None)


@router.message(UserFlow.waiting_feedback)
async def receive_feedback(message: Message, state: FSMContext, bot: Bot):
    if not ADMIN_ID:
        await state.clear()
        await message.answer("Feedback လက်ခံမယ့် admin မသတ်မှတ်ရသေးပါ။")
        return
    username = f"@{message.from_user.username}" if message.from_user.username else "မရှိပါ"
    name = escape(message.from_user.full_name or "မသိရပါ")
    info = (
        "📩 <b>Feedback အသစ်</b>\n\n"
        f"Name: <b>{name}</b>\n"
        f"Username: {escape(username)}\n"
        f"Telegram ID: <code>{message.from_user.id}</code>\n"
        f"Chat ID: <code>{message.chat.id}</code>"
    )
    await bot.send_message(ADMIN_ID, info)
    await bot.copy_message(chat_id=ADMIN_ID, from_chat_id=message.chat.id, message_id=message.message_id)
    await state.clear()
    await message.answer("✅ Feedback ကို admin ဆီ ပို့ပြီးပါပြီ။ ကျေးဇူးတင်ပါတယ်။", reply_markup=main_keyboard())


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


@router.callback_query(F.data.startswith("approve_order:"))
async def approve_order_button(callback: CallbackQuery, bot: Bot):
    if not admin_only(callback.from_user.id):
        await callback.answer("Admin only", show_alert=True)
        return
    order_id = int(callback.data.split(":", 1)[1])
    with db() as con:
        order = con.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
        if order:
            con.execute("UPDATE orders SET status='approved', approved_at=? WHERE id=?", (datetime.utcnow().isoformat(timespec="seconds") + "Z", order_id))
    if not order:
        await callback.answer("Order မတွေ့ပါ", show_alert=True)
        return
    await bot.send_message(order["user_id"], f"✅ Order #{order_id} ကို approve လုပ်ပြီးပါပြီ။\\n{PRODUCT_NAME} top-up ကို admin က ဆက်လက်လုပ်ဆောင်ပေးပါမယ်။")
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer("Approved")


@router.callback_query(F.data.startswith("reject_order:"))
async def reject_order_button(callback: CallbackQuery, bot: Bot):
    if not admin_only(callback.from_user.id):
        await callback.answer("Admin only", show_alert=True)
        return
    order_id = int(callback.data.split(":", 1)[1])
    with db() as con:
        order = con.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
        if order:
            con.execute("UPDATE orders SET status='rejected' WHERE id=?", (order_id,))
    if not order:
        await callback.answer("Order မတွေ့ပါ", show_alert=True)
        return
    await bot.send_message(order["user_id"], f"❌ Order #{order_id} ကို reject လုပ်ထားပါတယ်။\\nPayment အချက်အလက် မရှင်းလင်းပါက Support ကို ဆက်သွယ်ပါ။")
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer("Rejected")


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


@router.message()
async def admin_emoji_id_reply(message: Message):
    if not admin_only(message.from_user.id):
        return
    emoji_id = extract_custom_emoji_id(message)
    if emoji_id:
        await message.answer(
            "🆔 Custom emoji ID\n\n"
            f"<code>{emoji_id}</code>\n\n"
            "ဒီ ID ကို copy လုပ်ပြီး emoji system မှာသုံးနိုင်ပါတယ်။"
        )


async def health_handler(request):
    return web.json_response({"status": "ok", "service": "mlbb-topup-bot"})


async def start_health_server():
    app = web.Application()
    app.router.add_get("/", health_handler)
    app.router.add_get("/health", health_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv("PORT", "10000"))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logging.info("Health server listening on port %s", port)
    return runner


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
    await asyncio.gather(
        dp.start_polling(bot),
        start_health_server(),
    )


if __name__ == "__main__":
    asyncio.run(main())
