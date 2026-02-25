import asyncio
import logging
import aiosqlite
import os
import urllib.parse
from typing import Dict, Optional, List, Callable, Any, Awaitable
from dataclasses import dataclass

from aiogram import Bot, Dispatcher, types, F, Router, BaseMiddleware
from aiogram.filters import Command, BaseFilter
from aiogram.filters.callback_data import CallbackData
from aiogram.utils.keyboard import ReplyKeyboardBuilder, InlineKeyboardBuilder
from aiogram.exceptions import TelegramBadRequest
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.context import FSMContext

@dataclass
class Config:
    TOKEN = os.getenv("TOKEN") or os.getenv("BOT_TOKEN")
    ADMIN_USERNAME: str = "maksimpchelka"
    DB_PATH: str = "users.db"
    START_IMAGE_URL: str = "https://i.pinimg.com/736x/d0/5f/d3/d05fd38e32cd0a08e61fec7f91f15605.jpg"

class MenuTexts:
    CABINET = "💼 Личный Кабинет"
    PROXIES = "🌐 Прокси"
    INFO = "📃 Информация"
    BACK = "⬅️ Назад в меню"
    FAQ = "📌 FAQ"

class ProxyCallback(CallbackData, prefix="proxy"):
    id: int



class Database:
    def __init__(self, db: aiosqlite.Connection):
        self._db = db

    async def add_user(self, user_id: int, username: Optional[str]):
        await self._db.execute(
            "INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)",
            (user_id, username)
        )
        await self._db.commit()

    async def get_user_info(self, user_id: int):
        async with self._db.execute("SELECT reg_date, purchases FROM users WHERE user_id = ?", (user_id,)) as cursor:
            result = await cursor.fetchone()
            return result if result else None

    async def set_purchases(self, user_id: int, purchases: int) -> bool:
        async with self._db.execute("UPDATE users SET purchases = ? WHERE user_id = ?", (purchases, user_id)) as cursor:
            await self._db.commit()
            return cursor.rowcount > 0

    async def add_proxy(self, name: str, desc: str, price: str, msg: str):
        await self._db.execute(
            "INSERT INTO proxies (name, desc, price, msg) VALUES (?, ?, ?, ?)",
            (name, desc, price, msg)
        )
        await self._db.commit()

    async def delete_proxy(self, proxy_id: int) -> bool:
        async with self._db.execute("DELETE FROM proxies WHERE id = ?", (proxy_id,)) as cursor:
            await self._db.commit()
            return cursor.rowcount > 0

    async def get_all_proxies(self) -> List[aiosqlite.Row]:
        async with self._db.execute("SELECT * FROM proxies") as cursor:
            return await cursor.fetchall()

    async def get_proxy(self, proxy_id: int) -> Optional[aiosqlite.Row]:
        async with self._db.execute("SELECT * FROM proxies WHERE id = ?", (proxy_id,)) as cursor:
            return await cursor.fetchone()


async def init_db(db: aiosqlite.Connection):
    await db.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            reg_date DATETIME DEFAULT CURRENT_TIMESTAMP,
            purchases INTEGER DEFAULT 0
        )
    """)
    try:
        await db.execute("ALTER TABLE users ADD COLUMN purchases INTEGER DEFAULT 0")
    except aiosqlite.OperationalError:
        pass  

    await db.execute("""
        CREATE TABLE IF NOT EXISTS proxies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            desc TEXT,
            price TEXT,
            msg TEXT
        )
    """)
    
    async with db.execute("SELECT COUNT(*) as count FROM proxies") as cursor:
        result = await cursor.fetchone()
        if result and result[0] == 0:
            default_proxies = [
                ("🇺🇸 США", "SOCKS5 IPv4 🚀 | 1 Месяц", "39₽", 'за покупкой 🇺🇸 SOCKS5 IPv4'),
                ("🇩🇪 Германия", "SOCKS5 IPv4 🚀 | 1 Месяц", "36₽", 'за покупкой 🇩🇪 SOCKS5 IPv4'),
                ("🇬🇧 Великобритания", "SOCKS5 IPv4 🚀 | 1 Месяц", "36₽", 'за покупкой 🇬🇧 SOCKS5 IPv4'),
                ("🇳🇱 Нидерланды", "SOCKS5 IPv4 🚀 | 1 Месяц", "36₽", 'за покупкой 🇳🇱 SOCKS5 IPv4'),
                ("🇵🇱 Польша", "SOCKS5 IPv4 🚀 | 1 Месяц", "36₽", 'за покупкой 🇵🇱 SOCKS5 IPv4'),
                ("🇰🇿 Казахстан", "SOCKS5 IPv4 🚀 | 1 Месяц", "30₽", 'за покупкой 🇰🇿 SOCKS5 IPv4'),
            ]
            await db.executemany(
                "INSERT INTO proxies (name, desc, price, msg) VALUES (?, ?, ?, ?)",
                default_proxies
            )
    await db.commit()



class DbSessionMiddleware(BaseMiddleware):
    def __init__(self, db: Database):
        self.db = db

    async def __call__(
        self,
        handler: Callable[[types.TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: types.TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        data["db"] = self.db
        return await handler(event, data)

class IsAdmin(BaseFilter):
    async def __call__(self, message: types.Message) -> bool:
        return message.from_user.username == Config.ADMIN_USERNAME


class Keyboards:
    @staticmethod
    def main_menu():
        builder = ReplyKeyboardBuilder()
        builder.row(types.KeyboardButton(text=MenuTexts.CABINET), types.KeyboardButton(text=MenuTexts.PROXIES))
        builder.row(types.KeyboardButton(text=MenuTexts.INFO)),
        builder.row(types.KeyboardButton(text=MenuTexts.FAQ))
        return builder.as_markup(resize_keyboard=True)

    @staticmethod
    def back_button():
        builder = ReplyKeyboardBuilder()
        builder.add(types.KeyboardButton(text=MenuTexts.BACK))
        return builder.as_markup(resize_keyboard=True)

    @staticmethod
    def proxy_list(proxies: List[aiosqlite.Row]):
        builder = InlineKeyboardBuilder()
        for p in proxies:
            builder.button(text=f"{p['name']} — {p['price']}", callback_data=ProxyCallback(id=p['id']).pack())
        return builder.adjust(1).as_markup()

    @staticmethod
    def payment(proxy_msg: str):
        builder = InlineKeyboardBuilder()
        clean_msg = urllib.parse.quote(proxy_msg)
        url = f"https://t.me/{Config.ADMIN_USERNAME}?text=привет,%20хочу%20{clean_msg}"
        builder.button(text="💳 Оплатить", url=url)
        builder.button(text="⬅️ К списку стран", callback_data="back_to_list")

        return builder.adjust(1).as_markup()



router = Router()
async def smart_send(message: types.Message, text: str, state: FSMContext, reply_markup=None, photo: Optional[str] = None):
    user_id = message.from_user.id
    bot = message.bot
    
    state_data = await state.get_data()
    last_msg_id = state_data.get("last_msg_id")
    
    if last_msg_id:
        try:
            await bot.delete_message(user_id, last_msg_id)
        except TelegramBadRequest:
            pass  
    
    if photo:
        new_msg = await message.answer_photo(photo=photo, caption=text, reply_markup=reply_markup)
    else:
        new_msg = await message.answer(text, reply_markup=reply_markup)
        
    await state.update_data(last_msg_id=new_msg.message_id)


@router.message(Command("add_proxy"), IsAdmin())

async def cmd_add_proxy(message: types.Message, db: Database):
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer(
            "<code>/add_proxy name:desc:price:msg</code>\n"
            "пример <code>/add_proxy 🇫🇷 Франция:SOCKS5 IPv4 | 1 Мес:39₽:за покупкой 🇫🇷</code>"
        )
        return
        
    text = parts[1].strip()
    proxy_parts = text.split(":", 3)
    
    if len(proxy_parts) != 4:
        await message.answer("нужно 4 параметра")
        return
        
    name, desc, price, msg = [p.strip() for p in proxy_parts]
    await db.add_proxy(name, desc, price, msg)
    await message.answer(f"прокси <b>{name}</b> добавлен")

@router.message(Command("list_proxies"), IsAdmin())
async def cmd_list_proxies(message: types.Message, db: Database):
    proxies = await db.get_all_proxies()
    if not proxies:
        await message.answer("📭 Список прокси пуст")
        return
    
    text = "<b>список всех прокси ::</b>\n\n"
    for p in proxies:
        text += f"▪️ <b>ID:</b> <code>{p['id']}</code> | <b>Название::</b> {p['name']} | <b>Цена ::</b> {p['price']}\n"
    
    text += "\nудалить прокси <code>/delete_proxy [ID]</code>"
    await message.answer(text)

@router.message(Command("delete_proxy"), IsAdmin())
async def cmd_delete_proxy(message: types.Message, db: Database):
    args = message.text.split()
    if len(args) != 2:
        await message.answer("<b>использование</b> <code>/delete_proxy [ID_прокси]</code>\n /list_proxies")
        return       
    try:
        proxy_id = int(args[1])
    except ValueError:
        return
    
    success = await db.delete_proxy(proxy_id)
    if success:
        await message.answer(f"прокси <code>{proxy_id}</code> успешно удален")
    else:
        await message.answer(f"прокси <code>{proxy_id}</code> не найден")

@router.message(Command("update_bd"), IsAdmin())
async def cmd_update_bd(message: types.Message, db: Database):
    args = message.text.split()

    if len(args) != 3:
        await message.answer("<code>/update_bd [ID_пользователя] [колво_покупок]</code>")
        return
        
    try:
        target_id = int(args[1])
        purchases = int(args[2])
    except ValueError:
        return
    
    success = await db.set_purchases(target_id, purchases)
    if success:
        await message.answer(f"жля пользователя <code>{target_id}</code> установлено число покупок <b>{purchases}</b>")
    else:
        await message.answer(f"пользователь <code>{target_id}</code> не найден в базе данных")


@router.message(Command("start"))
@router.message(F.text == MenuTexts.BACK)
async def cmd_start(message: types.Message, db: Database, state: FSMContext):
    await db.add_user(message.from_user.id, message.from_user.username)
    await smart_send(
        message,
        f"<b>👻 Добро Пожаловать в Ghost Proxy, {message.from_user.first_name}</b>!\n❤️ Самые дешевые и надежные прокси только у нас",
        state,
        reply_markup=Keyboards.main_menu(),
        photo=Config.START_IMAGE_URL
    )

@router.message(F.text == MenuTexts.CABINET)
async def profile(message: types.Message, db: Database, state: FSMContext):
    user_info = await db.get_user_info(message.from_user.id)
    reg_date = user_info['reg_date'] if user_info else "Неизвестно"
    purchases = user_info['purchases'] if user_info else 0
    text = (
        f"<b>👤 Личный кабинет</b>\n\n"
        f"<b>🆔 ID ::</b> <code>{message.from_user.id}</code>\n"
        f"<b>📅 Регистрация ::</b> {reg_date}\n"
        f"<b>🛍 Покупок ::</b> {purchases}"
    )
    await smart_send(message, text, state, reply_markup=Keyboards.back_button())

@router.message(F.text == MenuTexts.PROXIES)
async def proxy_catalog(message: types.Message, db: Database, state: FSMContext):
    proxies = await db.get_all_proxies()

    if not proxies:
        await smart_send(message, "🛒 <b>Нет доступных локаций</b>", state, reply_markup=Keyboards.back_button())
    else:
        text = "🌍 Доступные локации ::"
        await smart_send(message, text, state, reply_markup=Keyboards.proxy_list(proxies))


@router.message(F.text == MenuTexts.INFO)
async def info_page(message: types.Message, state: FSMContext):
    await smart_send(
        message,
        "<b>ℹ️ О Ghosty Proxy</b>\n\n🚀 SOCKS5 Прокси на разный цвет и вкус\n◦ Качественные Proxy-Сервера, которые можно подключить к Telegram, гайд см. в FAQ",
        state,
        reply_markup=Keyboards.back_button()
    )

@router.message(F.text == MenuTexts.FAQ)
async def info_page(message: types.Message, state: FSMContext):
    await smart_send(
        message,
        "📌 Часто Задаваемые Вопросы ::\n\n ◦ 'На Сколько выдается Proxy?' - На месяц.\n ◦ 'Есть ли гарантии?' - Гарантия 24ч.\n" \
        "◦ 'Как использовать для Telegram?' - см. <a href='https://t.me/твой_канал/123'>здесь</a>",
        state,
        reply_markup=Keyboards.back_button()
    )

@router.callback_query(ProxyCallback.filter())
async def proxy_detail(callback: types.CallbackQuery, callback_data: ProxyCallback, db: Database):
    proxy_id = callback_data.id
    p = await db.get_proxy(proxy_id)
    
    if not p:
        await callback.answer("прокси недоступен", show_alert=True)
        return
        
    await callback.answer() 
    
    text = (
        f"<b>📦 {p['name']}</b> (ID: <code>{p['id']}</code>)\n"
        f"⚙️ <b>Тип:</b> {p['desc']}\n"
        f"💰 <b>Цена:</b> {p['price']}"
    )
    
    try:
        await callback.message.edit_text(text, reply_markup=Keyboards.payment(p['msg']))
    except TelegramBadRequest:
        pass

@router.callback_query(F.data == "back_to_list")
async def back_to_list(callback: types.CallbackQuery, db: Database):

    await callback.answer() 

    proxies = await db.get_all_proxies()
    
    if not proxies:
        try:
            await callback.message.edit_text("🛒 <b>Нет доступных локаций</b>")
        except TelegramBadRequest:
            pass
        return
        
    try:
        await callback.message.edit_text(
            "🌍 Доступные локации ::", 
            reply_markup=Keyboards.proxy_list(proxies)
        )
    except TelegramBadRequest:
        pass

async def main():
    logging.basicConfig(level=logging.INFO)
    
    #бд
    async with aiosqlite.connect(Config.DB_PATH) as db_conn:
        db_conn.row_factory = aiosqlite.Row
        
        #иницилизация
        await init_db(db_conn)
        
        database = Database(db_conn)
        
        bot = Bot(token=Config.TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
        dp = Dispatcher()
        
        dp.update.middleware(DbSessionMiddleware(database))
        
        dp.include_router(router)
        
        print("+")
        await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:

        pass

