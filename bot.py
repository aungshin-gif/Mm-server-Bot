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
CONTACT_ADMIN_USERNAME = "angsthtun"
DIAMOND_PRODUCTS = {
    "d202": {"name": "Diamond 202", "diamonds": 202, "price": 10200},
    "d404": {"name": "Diamond 404", "diamonds": 404, "price": 20000},
    "d606": {"name": "Diamond 606", "diamonds": 606, "price": 30000},
    "d829": {"name": "Diamond 829", "diamonds": 829, "price": 38000},
    "d2157": {"name": "Diamond 2157", "diamonds": 2157, "price": 97000},
}
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
    "rainbow": "5456140674028019486",
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
        con.execute("""
            CREATE TABLE IF NOT EXISTS product_stock (
                code TEXT PRIMARY KEY,
                available INTEGER NOT NULL DEFAULT 1,
                updated_at TEXT NOT NULL
            )
        """)
        now = datetime.utcnow().isoformat(timespec="seconds") + "Z"
        for code in ["weekly", *DIAMOND_PRODUCTS.keys()]:
            con.execute(
                "INSERT OR IGNORE INTO product_stock(code, available, updated_at) VALUES (?, 1, ?)",
                (code, now),
            )


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
        [InlineKeyboardButton(text="Products  •  MLBB Diamond Store", callback_data="menu_products", style="danger", icon_custom_emoji_id=CUSTOM_EMOJIS["products"])],
        [InlineKeyboardButton(text="Region Check  •  ID / Zone", callback_data="menu_region", style="success", icon_custom_emoji_id=CUSTOM_EMOJIS["region"])],
        [InlineKeyboardButton(text="Support  •  Contact Admin", callback_data="menu_support", style="primary", icon_custom_emoji_id=CUSTOM_EMOJIS["welcome"])],
        [InlineKeyboardButton(text="Feedback  •  Send Message", callback_data="menu_feedback", style="primary", icon_custom_emoji_id=CUSTOM_EMOJIS["feedback"])],
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


def get_product_stock(code: str) -> int:
    with db() as con:
        row = con.execute("SELECT available FROM product_stock WHERE code = ?", (code,)).fetchone()
    return max(0, int(row["available"])) if row else 0


def is_product_available(code: str) -> bool:
    return get_product_stock(code) > 0


def set_product_stock(code: str, quantity: int):
    quantity = max(0, int(quantity))
    now = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    with db() as con:
        con.execute(
            "INSERT INTO product_stock(code, available, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(code) DO UPDATE SET available=excluded.available, updated_at=excluded.updated_at",
            (code, quantity, now),
        )


def change_product_stock(code: str, delta: int) -> int:
    now = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    with db() as con:
        row = con.execute("SELECT available FROM product_stock WHERE code = ?", (code,)).fetchone()
        current = int(row["available"]) if row else 0
        new_value = max(0, current + int(delta))
        con.execute(
            "INSERT INTO product_stock(code, available, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(code) DO UPDATE SET available=excluded.available, updated_at=excluded.updated_at",
            (code, new_value, now),
        )
        return new_value


def decrement_product_stock(code: str) -> bool:
    now = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    with db() as con:
        cursor = con.execute(
            "UPDATE product_stock SET available = available - 1, updated_at = ? "
            "WHERE code = ? AND available > 0",
            (now, code),
        )
        return cursor.rowcount == 1


def product_code_from_name(product_name: str):
    if product_name == PRODUCT_NAME:
        return "weekly"
    return next((code for code, item in DIAMOND_PRODUCTS.items() if item["name"] == product_name), None)


def restore_stock_for_order(order):
    code = product_code_from_name(order["product"])
    if code:
        change_product_stock(code, 1)


def product_keyboard():
    rows = []
    weekly_stock = get_product_stock("weekly")
    weekly_label = f"Weekly Pass • 6,000 Ks • Stock: {weekly_stock}" if weekly_stock > 0 else "Weekly Pass • Unavailable"
    rows.append([InlineKeyboardButton(
        text=weekly_label,
        callback_data="buy:weekly" if weekly_stock > 0 else "unavailable",
        style="danger",
        icon_custom_emoji_id=CUSTOM_EMOJIS["weekly_pass"],
    )])
    for code, item in DIAMOND_PRODUCTS.items():
        stock = get_product_stock(code)
        label = f"Diamond {item['diamonds']} • {item['price']:,} Ks • Stock: {stock}" if stock > 0 else f"Diamond {item['diamonds']} • Unavailable"
        rows.append([InlineKeyboardButton(
            text=label,
            callback_data=f"buy:{code}" if stock > 0 else "unavailable",
            style="danger" if stock > 0 else "primary",
            icon_custom_emoji_id=CUSTOM_EMOJIS["diamond"],
        )])
    rows.append([InlineKeyboardButton(text="Contact Admin", url=f"https://t.me/{CONTACT_ADMIN_USERNAME}", style="primary", icon_custom_emoji_id=CUSTOM_EMOJIS["welcome"])])
    rows.append([InlineKeyboardButton(text="Main Menu", callback_data="back_menu", style="primary", icon_custom_emoji_id=CUSTOM_EMOJIS["welcome"])])
    return InlineKeyboardMarkup(inline_keyboard=rows)


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
        "rainbow",
        "Gamepay Hub ရဲ့ အပျင်းပြေ Bot မှ ကြိုဆိုပါတယ်။\n\n"
        "ဒီ Bot မှာ MLBB Myanmar Server diamond နှင့် Region စစ်ဆေးခြင်း service ရရှိနိုင်ပါတယ်။\n\n"
        "အောက်က Start ခလုတ်ကိုနှိပ်ပြီး ဝန်ဆောင်မှုများကို ရွေးချယ်ပါ။",
        "",
    )
    await message.answer(welcome_text, entities=welcome_entities, reply_markup=welcome_keyboard(), parse_mode=None)


@router.callback_query(F.data == "go_start")
async def go_start(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    await callback.message.answer(
        "GAMEPAY HUB MAIN MENU\n\nဝန်ဆောင်မှုတစ်ခုကို အောက်က ခလုတ်များမှ ရွေးချယ်ပါ။",
        reply_markup=main_keyboard(),
    )


@router.callback_query(F.data == "menu_products")
async def menu_products(callback: CallbackQuery):
    await callback.answer()
    await products(callback.message)


@router.callback_query(F.data == "back_menu")
async def back_menu(callback: CallbackQuery):
    await callback.answer()
    await callback.message.answer(
        "GAMEPAY HUB MAIN MENU\n\nဝန်ဆောင်မှုတစ်ခုကို အောက်က ခလုတ်များမှ ရွေးချယ်ပါ။",
        reply_markup=main_keyboard(),
    )


@router.message(F.text == "Products")
@router.message(Command("products"))
async def products(message: Message):
    product_lines = [
        ("catalogue", "GAMEPAY HUB CATALOGUE", ""),
        ("products", "ဝယ်ယူလိုသော product ကို အောက်က card မှာရွေးပါ။", ""),
        ("fast_delivery", "Fast delivery", ""),
        ("stock", "Stock quantity ကို catalogue card မှာကြည့်နိုင်ပါတယ်။", ""),
        ("secure_checkout", "Secure checkout", ""),
    ]
    weekly_stock = get_product_stock("weekly")
    product_lines.append(("weekly_pass", f"Weekly Pass • 6,000 Ks • Stock: {weekly_stock}" if weekly_stock > 0 else "Weekly Pass • Unavailable", ""))
    for code, item in DIAMOND_PRODUCTS.items():
        stock = get_product_stock(code)
        product_lines.append(("diamond", f"Diamond {item['diamonds']} • {item['price']:,} Ks • Stock: {stock}" if stock > 0 else f"Diamond {item['diamonds']} • Unavailable", ""))
    product_text, product_entities = custom_lines(product_lines)
    await message.answer(product_text, entities=product_entities, reply_markup=product_keyboard(), parse_mode=None)


@router.callback_query(F.data == "unavailable")
async def unavailable_product(callback: CallbackQuery):
    await callback.answer("ဒီ product လက်ရှိမရသေးပါ။ Contact Admin ကို ဆက်သွယ်ပါ။", show_alert=True)


@router.callback_query(F.data.startswith("buy:"))
async def buy_product(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    code = callback.data.split(":", 1)[1]
    if code == "weekly":
        product_name, product_price = PRODUCT_NAME, PRODUCT_PRICE
    elif code in DIAMOND_PRODUCTS:
        item = DIAMOND_PRODUCTS[code]
        product_name, product_price = item["name"], item["price"]
    else:
        await callback.answer("Product မတွေ့ပါ။", show_alert=True)
        return
    if not is_product_available(code):
        await callback.answer("ဒီ product လက်ရှိမရသေးပါ။", show_alert=True)
        return
    await state.update_data(product_code=code, product_name=product_name, product_price=product_price)
    await state.set_state(UserFlow.waiting_player_id)
    await callback.message.answer(
        f"{product_name} order စတင်ပါမယ်။\n\n"
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
    product_name = data.get("product_name", PRODUCT_NAME)
    product_price = int(data.get("product_price", PRODUCT_PRICE))
    await state.update_data(zone_id=zone)
    await state.set_state(UserFlow.waiting_payment_screenshot)
    order_text, order_entities = custom_lines([
        ("order_info", "Order အချက်အလက်", "🧾"),
        ("weekly_pass", f"Product: {product_name}", ""),
        ("name", f"Player ID: {player_id}", "👤"),
        ("region", f"Zone ID: {zone}", "🌍"),
        ("amount", f"Amount: {product_price:,} Ks", ""),
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
    product_code = data.get("product_code", "weekly")
    if not decrement_product_stock(product_code):
        await state.clear()
        await message.answer("ဒီ product stock ကုန်သွားပါပြီ။ Unavailable ဖြစ်နေပါတယ်။", reply_markup=main_keyboard())
        return
    photo_id = message.photo[-1].file_id
    now = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    with db() as con:
        cursor = con.execute(
            """INSERT INTO orders
               (user_id, username, player_id, zone_id, product, amount, payment_file_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (message.from_user.id, message.from_user.username or "", data["player_id"],
             data["zone_id"], data.get("product_name", PRODUCT_NAME), int(data.get("product_price", PRODUCT_PRICE)), photo_id, now),
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
            f"Product: {data.get('product_name', PRODUCT_NAME)}\n"
            f"Amount: {int(data.get('product_price', PRODUCT_PRICE)):,} Ks\n\n"
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
    prompt_text, prompt_entities = custom_prefix(
        "region",
        "MLBB User ID နဲ့ Server/Zone ID ကို space ခြားပြီးပို့ပါ။\nဥပမာ: 651256402 8592",
        "",
    )
    await message.answer(prompt_text, entities=prompt_entities, reply_markup=back_keyboard(), parse_mode=None)


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


def admin_panel_keyboard():
    rows = []
    products = [("weekly", PRODUCT_NAME)] + [(code, item["name"]) for code, item in DIAMOND_PRODUCTS.items()]
    for code, name in products:
        stock = get_product_stock(code)
        rows.append([
            InlineKeyboardButton(text=f"{name} • Stock: {stock}", callback_data=f"admin:noop:{code}", style="primary"),
        ])
        rows.append([
            InlineKeyboardButton(text="−1", callback_data=f"admin:dec:{code}", style="danger"),
            InlineKeyboardButton(text="+1", callback_data=f"admin:inc:{code}", style="success"),
        ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(Command("admin"))
async def admin_panel(message: Message):
    if not admin_only(message.from_user.id):
        return
    await message.answer(
        "ADMIN STOCK PANEL\n\n"
        "−1 / +1 နဲ့ stock quantity ပြောင်းပါ။\n"
        "အတိအကျထည့်ရန် /stock CODE NUMBER ကိုသုံးပါ။\n\n"
        "Commands list: /adminhelp",
        reply_markup=admin_panel_keyboard(),
    )


@router.callback_query(F.data.startswith("admin:"))
async def admin_stock_callback(callback: CallbackQuery):
    if not admin_only(callback.from_user.id):
        await callback.answer("Admin only", show_alert=True)
        return
    _, action, code = callback.data.split(":", 2)
    valid_codes = {"weekly", *DIAMOND_PRODUCTS.keys()}
    if code not in valid_codes:
        await callback.answer("Product code မမှန်ပါ။", show_alert=True)
        return
    if action == "inc":
        change_product_stock(code, 1)
    elif action == "dec":
        change_product_stock(code, -1)
    await callback.message.edit_text(
        "ADMIN STOCK PANEL\n\n"
        "−1 / +1 နဲ့ stock quantity ပြောင်းပါ။\n"
        "အတိအကျထည့်ရန် /stock CODE NUMBER ကိုသုံးပါ။\n\n"
        "Commands list: /adminhelp",
        reply_markup=admin_panel_keyboard(),
    )
    await callback.answer("Stock updated")


@router.callback_query(F.data.startswith("admin:noop:"))
async def admin_noop_callback(callback: CallbackQuery):
    await callback.answer()


@router.message(Command("stock"))
async def stock_command(message: Message):
    if not admin_only(message.from_user.id):
        return
    args = (message.text or "").split()
    valid_codes = {"weekly", *DIAMOND_PRODUCTS.keys()}
    if len(args) != 3 or args[1] not in valid_codes or not args[2].isdigit():
        await message.answer("အသုံးပြုပုံ: /stock CODE NUMBER\nဥပမာ: /stock d202 10")
        return
    code, quantity = args[1], int(args[2])
    set_product_stock(code, quantity)
    label = PRODUCT_NAME if code == "weekly" else DIAMOND_PRODUCTS[code]["name"]
    await message.answer(f"{label}: Stock {quantity}")


@router.message(Command("stocks"))
async def stocks_command(message: Message):
    if not admin_only(message.from_user.id):
        return
    lines = ["CURRENT STOCKS", ""]
    products = [("weekly", PRODUCT_NAME, PRODUCT_PRICE)] + [
        (code, item["name"], item["price"]) for code, item in DIAMOND_PRODUCTS.items()
    ]
    for code, name, price in products:
        quantity = get_product_stock(code)
        status = "Available" if quantity > 0 else "Unavailable"
        lines.append(f"{code} — {name} — {price:,} Ks — Stock: {quantity} — {status}")
    await message.answer("\n".join(lines))


@router.message(Command("adminhelp"))
async def admin_help(message: Message):
    if not admin_only(message.from_user.id):
        return
    await message.answer(
        "ADMIN COMMANDS\n\n"
        "/admin — Stock panel ဖွင့်ရန်\n"
        "/stocks — Stock အားလုံးကြည့်ရန်\n"
        "/stock CODE NUMBER — Stock အတိအကျသတ်မှတ်ရန်\n"
        "/stock d202 10 — Diamond 202 ကို 10 ခုထားရန်\n"
        "/stock d202 0 — Diamond 202 ကို Unavailable လုပ်ရန်\n"
        "/saveemoji LABEL — Emoji ID သိမ်းရန်\n"
        "/emojis — သိမ်းထားသော emoji IDs ကြည့်ရန်\n"
        "/approve ORDER_ID — Order approveရန်\n"
        "/reject ORDER_ID — Order rejectရန်"
    )


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
    await bot.send_message(order["user_id"], f"Order #{order_id} ကို approve လုပ်ပြီးပါပြီ။\n{order['product']} top-up ကို admin က ဆက်လက်လုပ်ဆောင်ပေးပါမယ်။")
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
        if order and order["status"] not in {"rejected", "approved"}:
            con.execute("UPDATE orders SET status='rejected' WHERE id=?", (order_id,))
            restore_stock_for_order(order)
    if not order:
        await callback.answer("Order မတွေ့ပါ", show_alert=True)
        return
    await bot.send_message(order["user_id"], f"Order #{order_id} ကို reject လုပ်ထားပါတယ်။\nPayment အချက်အလက် မရှင်းလင်းပါက Support ကို ဆက်သွယ်ပါ။")
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
    await bot.send_message(order["user_id"], f"Order #{order_id} ကို approve လုပ်ပြီးပါပြီ။\n{order['product']} top-up ကို admin က ဆက်လက်လုပ်ဆောင်ပေးပါမယ်။")
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
        if order and order["status"] not in {"rejected", "approved"}:
            con.execute("UPDATE orders SET status='rejected' WHERE id=?", (order_id,))
            restore_stock_for_order(order)
    if not order:
        await message.answer("Order မတွေ့ပါ။")
        return
    await bot.send_message(order["user_id"], f"Order #{order_id} ကို reject လုပ်ထားပါတယ်။ Payment အချက်အလက် မရှင်းလင်းပါက Support ကို ဆက်သွယ်ပါ။")
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
