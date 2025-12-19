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
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# === Загрузка переменных окружения ===
load_dotenv()

# === Конфигурация ===
BOT_TOKEN = os.getenv("BOT_TOKEN")
MODERATOR_ID = int(os.getenv("MODERATOR_ID"))
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

# Установка дедлайна (до 26 декабря 2025, включительно)
DEADLINE = datetime(2025, 12, 26, 23, 59, 59, tzinfo=timezone.utc)

# === Подключение к Supabase ===
# Убрали аннотацию типа, так как она не обязательна и может вызвать ошибки
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# === Инициализация бота ===
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# === FSM состояния ===
class ModerationComment(StatesGroup):
    waiting_for_comment = State()

# === Вспомогательные функции ===
def hash_user_id(user_id: int) -> str:
    """Создаёт короткий хеш от ID пользователя."""
    return hashlib.sha256(str(user_id).encode()).hexdigest()[:16]

def get_all_pending_tracks():
    """Получает все треки на модерации, отсортированные по времени создания."""
    try:
        response = supabase.table("pending_tracks").select("*").order("created_at", desc=False).execute()
        return response.data
    except Exception as e:
        logging.error(f"Ошибка получения треков на модерации: {e}")
        return []

def get_track_by_id(track_id: str):
    """Находит трек в pending_tracks по его уникальному id."""
    try:
        response = supabase.table("pending_tracks").select("*").eq("id", track_id).execute()
        tracks = response.data
        return tracks[0] if tracks else None
    except Exception as e:
        logging.error(f"Ошибка поиска трека по ID {track_id}: {e}")
        return None


# === Команда /start ===
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user_hash = hash_user_id(message.from_user.id)
    try:
        pending_response = supabase.table("pending_tracks").select("id").eq("user_hash", user_hash).execute()
        approved_response = supabase.table("approved_tracks").select("id").eq("user_hash", user_hash).execute()
        pending_count = len(pending_response.data)
        approved_count = len(approved_response.data)
    except Exception as e:
        logging.error(f"Ошибка получения статуса треков пользователя {message.from_user.id}: {e}")
        await message.answer("❌ Произошла ошибка при загрузке данных. Попробуй позже.")
        return

    total_sent = pending_count + approved_count
    remaining = max(0, 3 - total_sent)

    keyboard = InlineKeyboardBuilder()
    keyboard.add(
        InlineKeyboardButton(
            text="📜 Правила тусовки",
            url="https://teletype.in/@artem2601/8pDqOmM9g4X"
        )
    )

    welcome_text = (
        f"✨ Привет, <b>{message.from_user.first_name}</b>! 🎧\n\n"
        f"Я — <b>Party Music Bot</b> 🎵, и я помогу тебе составить плейлист к новогодней тусовке! 🎄🎉\n\n"
        f"<b>Ты можешь прислать до 3 треков</b> — это могут быть аудиофайлы или ссылки на YouTube, Spotify, Яндекс.Музыку и другие платформы.\n\n"
        f"✅ <b>Одобренный</b> трек закрепляется.\n"
        f"❌ <b>Отклонённый</b> трек — можно заменить новым!\n\n"
        f"📊 <b>Ты уже отправил:</b> {total_sent}/3\n"
        f"🎵 <b>Осталось отправить:</b> {remaining}\n\n"
        f"🔐 Всё анонимно!\n\n"
        f"Если возникнут вопросы — писать в <a href='https://t.me/ligr5'><b>поддержку</b></a>! 📩"
    )

    # Добавлен параметр disable_web_page_preview=True
    await message.answer(welcome_text, reply_markup=keyboard.as_markup(), parse_mode="HTML", disable_web_page_preview=True)


# === Команды для модератора ===
@dp.message(Command("check"))
async def cmd_check(message: types.Message):
    if message.from_user.id != MODERATOR_ID:
        await message.answer("❌ У тебя нет прав модератора.")
        return

    pending = get_all_pending_tracks()
    if not pending:
        await message.answer("📋 Нет треков, ожидающих проверки.")
        return

    response_lines = ["📋 <b>Треки на модерации:</b>\n"]
    for i, t in enumerate(pending, 1):
        h = t.get("user_hash", "???")[:8]
        track_type = "🎵 Аудио" if t["type"] == "audio" else "🔗 Ссылка"
        response_lines.append(f"{i}. {track_type} (пользователь: <code>{h}...</code>)")

    await message.answer("\n".join(response_lines), parse_mode="HTML")

@dp.message(Command("moderate"))
async def cmd_moderate(message: types.Message):
    if message.from_user.id != MODERATOR_ID:
        await message.answer("❌ У тебя нет прав модератора.")
        return

    pending = get_all_pending_tracks()
    if pending:
        first_track = pending[0]
        await send_moderation_message(first_track, first_track['id'])
    else:
        await message.answer("📋 Нет треков для модерации.")

@dp.message(Command("tracks"))
async def cmd_tracks(message: types.Message):
    if message.from_user.id != MODERATOR_ID:
        await message.answer("❌ У тебя нет прав модератора.")
        return

    try:
        approved_response = supabase.table("approved_tracks").select("*").execute()
        rejected_response = supabase.table("rejected_tracks").select("*").execute()
        approved = approved_response.data
        rejected = rejected_response.data
    except Exception as e:
        logging.error(f"Ошибка получения списка треков: {e}")
        await message.answer("❌ Произошла ошибка при получении списка треков.")
        return

    response_parts = []

    # 🎧 Одобренные
    if approved:
        response_parts.append("🎧 <b>Одобренные треки:</b>")
        for i, t in enumerate(approved, 1):
            if t.get("url"):
                title = t.get("url_title", "Ссылка")
                response_parts.append(f"{i}. <a href='{t['url']}'>{title}</a>")
            elif t.get("file_id"):
                response_parts.append(f"{i}. [Аудио файл]")
    else:
        response_parts.append("🎧 Нет одобренных треков.")

    # ❌ Отклонённые
    if rejected:
        response_parts.append("\n❌ <b>Отклонённые треки:</b>")
        for i, t in enumerate(rejected, 1):
            if t.get("url"):
                title = t.get("url_title", "Ссылка")
                response_parts.append(f"{i}. <a href='{t['url']}'>{title}</a>")
            elif t.get("file_id"):
                response_parts.append(f"{i}. [Аудио файл]")
    else:
        response_parts.append("\n❌ Нет отклонённых треков.")

    await message.answer("\n".join(response_parts), parse_mode="HTML")


# === FSM: обработка комментария модератора ===
@dp.message(ModerationComment.waiting_for_comment)
async def process_moderation_comment(message: types.Message, state: FSMContext):
    data = await state.get_data()
    track_db_id = data["track_db_id"]
    action = data["action"]
    user_hash = data["user_hash"]

    comment = message.text or "Без комментария."

    track = get_track_by_id(track_db_id)
    if not track:
        await message.answer("❌ Ошибка: трек не найден или уже обработан.")
        await state.clear()
        return

    user_id = track.get("user_id")
    safe_track = {k: v for k, v in track.items() if k not in ("id", "user_id", "created_at")}

    success = False
    try:
        if action == "approve":
            supabase.table("approved_tracks").insert(safe_track).execute()
            await message.answer("✅ Трек <b>одобрен</b>! 🎉", parse_mode="HTML")
            success = True
        elif action == "reject":
            supabase.table("rejected_tracks").insert(safe_track).execute()
            await message.answer("❌ Трек <b>отклонён</b>. 😔", parse_mode="HTML")
            success = True
    except Exception as e:
        logging.error(f"Ошибка сохранения трека (ID: {track_db_id}, action: {action}): {e}")
        await message.answer("❌ Произошла ошибка при сохранении решения. Попробуй снова.")

    if success and user_id:
        try:
            if action == "approve":
                notification_text = f"🎶 Твой трек <b>одобрен</b>! 🎉\n\n💬 Комментарий модератора:\n<blockquote>{comment}</blockquote>"
            else: # reject
                notification_text = f"😔 Твой трек <b>отклонён</b>.\n\n💬 Комментарий модератора:\n<blockquote>{comment}</blockquote>\n\n🎵 Можешь прислать другой трек!"

            await bot.send_message(user_id, notification_text, parse_mode="HTML")
        except Exception as e:
            logging.warning(f"Не удалось отправить уведомление пользователю {user_id}: {e}")

    # Удаляем трек из pending_tracks по его уникальному ID
    try:
        supabase.table("pending_tracks").delete().eq("id", track["id"]).execute()
    except Exception as e:
        logging.error(f"Ошибка при удалении трека из pending_tracks (ID: {track['id']}): {e}")

    await state.clear()

    # Следующий трек
    pending = get_all_pending_tracks()
    if pending:
        next_track = pending[0]
        await message.answer("➡️ Следующий трек для модерации:")
        await send_moderation_message(next_track, next_track['id'])
    else:
        await message.answer("🎉 Все треки обработаны! 👏")


# === Нажатие кнопок модерации ===
@dp.callback_query(lambda c: c.data.startswith(("approve_", "reject_")))
async def handle_moderation(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != MODERATOR_ID:
        await callback.answer("❌ Только модератор может это сделать.", show_alert=True)
        return

    action, track_db_id = callback.data.split("_", 1)
    track = get_track_by_id(track_db_id)

    if not track:
        await callback.answer("❌ Трек не найден или уже обработан.", show_alert=True)
        return

    user_hash = track["user_hash"]
    await state.set_state(ModerationComment.waiting_for_comment)
    await state.update_data(track_db_id=track_db_id, action=action, user_hash=user_hash)

    action_text = "одобрить" if action == "approve" else "отклонить"
    await callback.message.answer(f"📝 Введи комментарий для пользователя (для действия '<b>{action_text}</b>'):", parse_mode="HTML")

    try:
        await callback.message.delete()
    except Exception as e:
        logging.debug(f"Не удалось удалить сообщение с кнопками: {e}")


# === Модерация: отправка трека модератору ===
async def send_moderation_message(track, track_db_id: str):
    keyboard = InlineKeyboardBuilder()
    keyboard.row(
        InlineKeyboardButton(text="✅ Одобрить", callback_data=f"approve_{track_db_id}"),
        InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject_{track_db_id}")
    )
    reply_markup = keyboard.as_markup()

    try:
        if track["type"] == "audio":
            await bot.send_audio(
                MODERATOR_ID,
                track["file_id"],
                caption="🎵 <b>Трек на модерации</b>",
                reply_markup=reply_markup,
                parse_mode="HTML"
            )
        else: # url
            url = track["url"]
            title = track.get("url_title", "Ссылка")
            await bot.send_message(
                MODERATOR_ID,
                f"🔗 <b>Ссылка на модерации:</b>\n\n<a href='{url}'>{title}</a>",
                reply_markup=reply_markup,
                parse_mode="HTML"
            )
    except Exception as e:
        logging.error(f"Ошибка при отправке трека модератору (ID: {track_db_id}): {e}")
        await bot.send_message(MODERATOR_ID, f"❌ Ошибка при отправке трека для модерации. ID: {track_db_id}\n{e}")


# === Универсальный обработчик (всё, кроме команд и FSM) ===
@dp.message()
async def handle_message(message: types.Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state == ModerationComment.waiting_for_comment.state:
        # FSM уже обработает — не попадает сюда
        return

    if message.text and message.text.startswith("/"):
        # Не отвечаем на команды — пусть обрабатываются другими хендлерами
        return

    # --- ПРОВЕРКА ДЕДЛАЙНА ---
    message_time = datetime.fromisoformat(message.date.isoformat()).replace(tzinfo=timezone.utc)
    if message_time > DEADLINE:
        await message.answer("⏰ Извини, <b>срок отправки треков</b> для новогодней тусовки закончился! 🎄\n\nСпасибо за участие! 🎉", parse_mode="HTML")
        return
    # --- /ПРОВЕРКА ДЕДЛАЙНА ---

    # Проверяем, ссылка ли это
    text_content = None
    if message.text:
        text_content = message.text.strip()
        platforms = ["youtube.com", "youtu.be", "spotify.com", "apple.co", "music.yandex", "vk.com"]
        if not any(p in text_content for p in platforms):
            await message.answer("❌ Пожалуйста, отправь <b>аудиофайл</b> или <b>ссылку</b> на трек с одной из поддерживаемых платформ (YouTube, Spotify и др.). 🎧", parse_mode="HTML")
            return

    user_id = message.from_user.id
    user_hash = hash_user_id(user_id)

    try:
        pending_response = supabase.table("pending_tracks").select("id").eq("user_hash", user_hash).execute()
        approved_response = supabase.table("approved_tracks").select("id").eq("user_hash", user_hash).execute()
        pending_count = len(pending_response.data)
        approved_count = len(approved_response.data)
    except Exception as e:
        logging.error(f"Ошибка получения статуса треков пользователя {user_id} при отправке: {e}")
        await message.answer("❌ Произошла ошибка при проверке лимита. Попробуй позже.")
        return

    total_sent = pending_count + approved_count

    if total_sent >= 3:
        await message.answer("❌ Извини, ты <b>уже отправил 3 трека</b> — лимит исчерпан! 🎶", parse_mode="HTML")
        return

    # Формируем данные трека
    track_data = None
    if message.audio:
        track_data = {"type": "audio", "file_id": message.audio.file_id}
    elif text_content:
        track_data = {"type": "url", "url": text_content, "url_title": "Ссылка на трек"}

    if not track_data:
        await message.answer("❌ Отправь, пожалуйста, <b>аудиофайл</b> или <b>ссылку</b> на трек. 🎧", parse_mode="HTML")
        return

    # Сохраняем в pending (с user_id временно)
    track_data["user_id"] = user_id
    track_data["user_hash"] = user_hash
    try:
        response = supabase.table("pending_tracks").insert(track_data).execute()
        inserted_track_id = response.data[0]['id']
    except Exception as e:
        logging.error(f"Ошибка при сохранении трека пользователя {user_id} в pending_tracks: {e}")
        await message.answer("❌ Произошла ошибка при сохранении трека. Попробуй снова.")
        return

    remaining = 3 - (total_sent + 1)
    await message.answer(f"✅ Твой трек <b>получен</b> и отправлен на модерацию! 🎵\n\n📊 <b>Осталось отправить:</b> {remaining}", parse_mode="HTML")

    try:
        await bot.send_message(
            MODERATOR_ID,
            f"🎵 Новый трек от пользователя <code>{user_hash[:8]}...</code> на модерации!",
            parse_mode="HTML"
        )
    except Exception as e:
        logging.warning(f"Не удалось отправить уведомление модератору: {e}")


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
    # Используем PORT из переменных окружения Render
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, threaded=True)

# === Запуск ===
if __name__ == "__main__":
    flask_thread = Thread(target=run_flask, daemon=True)
    flask_thread.start()
    print("🤖 Party Music Bot запускается...")
    asyncio.run(dp.start_polling(bot))