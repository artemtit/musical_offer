import asyncio
import hashlib
import os
import logging
from threading import Thread
from datetime import datetime, timezone
from flask import Flask
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import InlineKeyboardButton
from dotenv import load_dotenv
from supabase import create_client

# === Логирование ===
logging.basicConfig(level=logging.INFO)

# === Загрузка переменных окружения ===
load_dotenv()

# === Конфигурация ===
BOT_TOKEN = os.getenv("BOT_TOKEN")
MODERATOR_ID = int(os.getenv("MODERATOR_ID"))
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

# === Подключение к Supabase ===
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# === Инициализация бота ===
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# === FSM состояния ===
class ModerationComment(StatesGroup):
    waiting_for_comment = State()

# === Вспомогательные функции ===
def hash_user_id(user_id: int) -> str:
    return hashlib.sha256(str(user_id).encode()).hexdigest()[:16]

def get_all_pending_tracks():
    response = supabase.table("pending_tracks").select("*").order("created_at", desc=False).execute()
    return response.data

def get_track_by_index(index: int):
    tracks = get_all_pending_tracks()
    if 0 <= index < len(tracks):
        return tracks[index], index
    return None, None

# === Команда /start (без кнопки, с inline-ссылкой) ===
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user_hash = hash_user_id(message.from_user.id)
    pending = supabase.table("pending_tracks").select("id").eq("user_hash", user_hash).execute().data
    approved = supabase.table("approved_tracks").select("id").eq("user_hash", user_hash).execute().data
    total_sent = len(pending) + len(approved)
    remaining = max(0, 3 - total_sent)

    # Inline-кнопка с правилами
    keyboard = InlineKeyboardBuilder()
    keyboard.add(
        InlineKeyboardButton(
            text="📄 Правила новогодней тусовки",
            url="https://teletype.in/@artem2601/8pDqOmM9g4X"
        )
    )

    await message.answer(
        "✨ Привет! 🎧\n\n"
        "Я — Party Music Bot 🎵 — твой DJ-помощник для новогодней тусовки! 🎉\n\n"
        f"Ты можешь прислать до 3 треков — аудио или ссылку (YouTube, Spotify, Яндекс.Музыка и др.).\n"
        f"Осталось отправить: {remaining} ✨\n\n"
        "⚠️ После одобрения трека — он фиксируется. Если отклонён — можно прислать новый вместо него!\n\n"
        "Всё анонимно — твои данные в безопасности! 🛡️",
        reply_markup=keyboard.as_markup()
    )

# === Команды для модератора ===
@dp.message(Command("check"))
async def cmd_check(message: types.Message):
    if message.from_user.id != MODERATOR_ID:
        await message.answer("❌ У тебя нет прав для просмотра треков на модерации.")
        return
    pending = get_all_pending_tracks()
    if not pending:
        await message.answer("📋 Нет треков на модерации.")
        return
    response = "📋 Треки на модерации:\n\n"
    for i, t in enumerate(pending, 1):
        h = t.get("user_hash", "???")[:8]
        if t["type"] == "audio":
            response += f"{i}. Аудио (hash: {h}...)\n"
        else:
            response += f"{i}. Ссылка (hash: {h}...)\n"
    await message.answer(response)

@dp.message(Command("moderate"))
async def cmd_moderate(message: types.Message):
    if message.from_user.id != MODERATOR_ID:
        await message.answer("❌ У тебя нет прав для модерации.")
        return
    pending = get_all_pending_tracks()
    if pending:
        await send_moderation_message(pending[0], 0)
    else:
        await message.answer("📋 Нет треков на модерации.")

@dp.message(Command("tracks"))
async def cmd_tracks(message: types.Message):
    if message.from_user.id != MODERATOR_ID:
        await message.answer("❌ У тебя нет прав для просмотра списка треков.")
        return

    approved = supabase.table("approved_tracks").select("*").execute().data
    rejected = supabase.table("rejected_tracks").select("*").execute().data

    response = ""

    # 🎧 Одобренные
    if approved:
        response += "🎧 Одобренные треки:\n\n"
        for i, t in enumerate(approved, 1):
            if t.get("url"):
                title = t.get("url_title", "Ссылка")
                response += f"{i}. [{title}]({t['url']})\n"
            elif t.get("file_id"):
                response += f"{i}. [Аудио файл]({t['file_id']})\n"
    else:
        response += "🎧 Нет одобренных треков.\n\n"

    # ❌ Отклонённые
    if rejected:
        response += "\n❌ Отклонённые треки:\n\n"
        for i, t in enumerate(rejected, 1):
            if t.get("url"):
                title = t.get("url_title", "Ссылка")
                response += f"{i}. [{title}]({t['url']})\n"
            elif t.get("file_id"):
                response += f"{i}. [Аудио файл]({t['file_id']})\n"
    else:
        response += "\n❌ Нет отклонённых треков.\n"

    await message.answer(response, parse_mode="Markdown")

# === FSM: обработка комментария модератора ===
@dp.message(ModerationComment.waiting_for_comment)
async def process_moderation_comment(message: types.Message, state: FSMContext):
    data = await state.get_data()
    track_id = data["track_id"]
    action = data["action"]
    user_hash = data["user_hash"]
    comment = message.text or "Без комментария."

    track, idx = get_track_by_index(track_id)
    if not track:
        await message.answer("❌ Трек уже обработан.")
        await state.clear()
        return

    user_id = track.get("user_id")
    safe_track = {k: v for k, v in track.items() if k not in ("id", "user_id")}

    if action == "approve":
        supabase.table("approved_tracks").insert(safe_track).execute()
        await message.answer("✅ Трек одобрен!")
        if user_id:
            try:
                await bot.send_message(user_id, f"✅ Твой трек одобрен! 🎶\n💬 {comment}")
            except Exception as e:
                logging.warning(f"Не отправлено {user_id}: {e}")

    elif action == "reject":
        supabase.table("rejected_tracks").insert(safe_track).execute()
        await message.answer("❌ Трек отклонён.")
        if user_id:
            try:
                await bot.send_message(
                    user_id,
                    f"❌ Твой трек отклонён. 😔\n💬 {comment}\n\nМожешь прислать другой трек!"
                )
            except Exception as e:
                logging.warning(f"Не отправлено {user_id}: {e}")

    # Удаляем из pending
    supabase.table("pending_tracks").delete().eq("id", track["id"]).execute()
    await state.clear()

    # Следующий трек
    pending = get_all_pending_tracks()
    if pending:
        await message.answer("➡️ Следующий трек:")
        await send_moderation_message(pending[0], 0)
    else:
        await message.answer("🎉 Все треки обработаны!")

# === Нажатие кнопок модерации ===
@dp.callback_query(lambda c: c.data.startswith(("approve_", "reject_")))
async def handle_moderation(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != MODERATOR_ID:
        await callback.answer("❌ У тебя нет прав.", show_alert=True)
        return

    action, track_id_str = callback.data.split("_", 1)
    track_id = int(track_id_str)
    track, idx = get_track_by_index(track_id)

    if not track:
        await callback.answer("Трек уже обработан.", show_alert=True)
        return

    user_hash = track["user_hash"]
    await state.set_state(ModerationComment.waiting_for_comment)
    await state.update_data(track_id=track_id, action=action, user_hash=user_hash)
    await callback.message.answer("📝 Введи комментарий для пользователя:")
    try:
        await callback.message.delete()
    except:
        pass

# === Модерация: отправка трека модератору ===
async def send_moderation_message(track, track_id: int):
    keyboard = InlineKeyboardBuilder()
    keyboard.add(InlineKeyboardButton(text="✅ Одобрить", callback_data=f"approve_{track_id}"))
    keyboard.add(InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject_{track_id}"))
    reply_markup = keyboard.as_markup()

    if track["type"] == "audio":
        await bot.send_audio(
            MODERATOR_ID,
            track["file_id"],
            caption="🎵 Трек на модерации",
            reply_markup=reply_markup
        )
    else:
        url = track["url"]
        title = track.get("url_title", "Ссылка")
        await bot.send_message(
            MODERATOR_ID,
            f"🔗 Ссылка на модерации:\n\n[{title}]({url})",
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )

# === Универсальный обработчик (всё, кроме команд и FSM) ===
@dp.message()
async def handle_message(message: types.Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state == ModerationComment.waiting_for_comment.state:
        # FSM уже обработает — не попадает сюда
        return

    # Игнорируем команды (они обрабатываются отдельно)
    if message.text and message.text.startswith("/"):
        # Не отвечаем на команды — пусть обрабатываются другими хендлерами
        return

    # Проверяем, ссылка ли это
    text_content = None
    if message.text:
        text_content = message.text.strip()
        platforms = ["youtube.com", "youtu.be", "spotify.com", "apple.co", "music.yandex", "vk.com"]
        if not any(p in text_content for p in platforms):
            await message.answer("❌ Отправь аудиофайл или ссылку на трек (YouTube, Spotify и др.). 🎧")
            return

    user_id = message.from_user.id
    user_hash = hash_user_id(user_id)

    # Считаем использованные слоты: pending + approved
    pending = supabase.table("pending_tracks").select("id").eq("user_hash", user_hash).execute().data
    approved = supabase.table("approved_tracks").select("id").eq("user_hash", user_hash).execute().data
    total_sent = len(pending) + len(approved)

    if total_sent >= 3:
        await message.answer("❌ Ты уже отправил 3 трека — лимит исчерпан! 🎶")
        return

    # Формируем данные трека
    track_data = None
    if message.audio:
        track_data = {"type": "audio", "file_id": message.audio.file_id}
    elif text_content:
        track_data = {"type": "url", "url": text_content, "url_title": "Ссылка на трек"}

    if not track_data:
        await message.answer("❌ Отправь аудиофайл или ссылку на трек. 🎧")
        return

    # Сохраняем в pending (с user_id временно)
    track_data["user_id"] = user_id
    track_data["user_hash"] = user_hash
    supabase.table("pending_tracks").insert(track_data).execute()

    remaining = 3 - (total_sent + 1)
    await message.answer(f"✅ Трек получен! Осталось отправить: {remaining}")

    await bot.send_message(
        MODERATOR_ID,
        f"🎵 Новый трек на модерации!\nПользователь: {user_hash[:8]}..."
    )

# === Flask сервер для Render ===
app = Flask(__name__)

@app.route("/")
def home():
    return {
        "status": "online",
        "service": "musical_offer_bot",
        "message": "🎧✨ Party Music Bot is awake!"
    }

@app.route("/health")
def health():
    return {"ok": True}

def run_flask():
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port, threaded=True)

# === Запуск ===
if __name__ == "__main__":
    flask_thread = Thread(target=run_flask, daemon=True)
    flask_thread.start()
    print("🤖 Party Music Bot запускается...")
    asyncio.run(dp.start_polling(bot))