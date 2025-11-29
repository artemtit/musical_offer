import asyncio
import hashlib
import json
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
import aiofiles
from dotenv import load_dotenv
import os

# 🌱 Загрузка переменных окружения
load_dotenv()

# 🔑 Конфигурация
BOT_TOKEN = os.getenv("BOT_TOKEN")
MODERATOR_ID = int(os.getenv("MODERATOR_ID"))

# 📁 Пути к файлам
APPROVED_TRACKS_FILE = "approved_tracks.json"
PENDING_TRACKS_FILE = "pending_tracks.json"
USER_STATUS_FILE = "user_status.json"
REJECTED_TRACKS_FILE = "rejected_tracks.json"  # ✅ Новый файл

# 🤖 Инициализация
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# 🧠 Состояния FSM для модератора
class ModerationComment(StatesGroup):
    waiting_for_comment = State()

# 🛠️ Вспомогательные функции

async def load_json_file(filepath: str, default_value=None):
    """Безопасная загрузка JSON-файла."""
    if default_value is None:
        default_value = []
    try:
        async with aiofiles.open(filepath, "r", encoding="utf-8") as f:
            content = await f.read()
            return json.loads(content) if content.strip() else default_value
    except FileNotFoundError:
        return default_value
    except json.JSONDecodeError:
        return default_value

async def save_json_file(filepath: str, data):
    """Сохранение данных в JSON-файл."""
    async with aiofiles.open(filepath, "w", encoding="utf-8") as f:
        await f.write(json.dumps(data, ensure_ascii=False, indent=2))

def hash_user_id(user_id: int) -> str:
    """Хеширование user_id для анонимности."""
    return hashlib.sha256(str(user_id).encode()).hexdigest()

# 💬 FSM: обработка комментария модератора
@dp.message(ModerationComment.waiting_for_comment)
async def process_moderation_comment(message: types.Message, state: FSMContext):
    data = await state.get_data()
    track_id = data["track_id"]
    action = data["action"]
    user_hash = data["user_hash"]
    comment = message.text or "Без комментария."

    pending_tracks = await load_json_file(PENDING_TRACKS_FILE)
    if track_id >= len(pending_tracks):
        await message.answer("❌ Ошибка: трек уже обработан или удалён.")
        await state.clear()
        return

    track = pending_tracks[track_id]
    user_id = track.get("user_id")

    # Загружаем статусы
    user_status = await load_json_file(USER_STATUS_FILE, {})

    if action == "approve":
        # Сохраняем в одобренные
        approved_tracks = await load_json_file(APPROVED_TRACKS_FILE, [])
        approved_tracks.append(track)
        await save_json_file(APPROVED_TRACKS_FILE, approved_tracks)

        user_status[user_hash] = "approved"
        await save_json_file(USER_STATUS_FILE, user_status)

        await message.answer("✅ Трек одобрен и добавлен в список!")
        if user_id:
            try:
                await bot.send_message(
                    user_id,
                    f"✅ Твой трек был одобрен! 🎶\n\n💬 Комментарий: {comment}"
                )
            except Exception as e:
                print(f"⚠️ Не удалось отправить сообщение пользователю {user_id}: {e}")

    elif action == "reject":
        # Удаляем временный user_id из трека перед сохранением (для анонимности)
        track_to_save = track.copy()
        track_to_save.pop("user_id", None)

        print(f"DEBUG: Сохраняем в отклонённые: {track_to_save}")

        # ✅ Сохраняем в отклонённые
        rejected_tracks = await load_json_file(REJECTED_TRACKS_FILE, [])
        rejected_tracks.append(track_to_save)
        await save_json_file(REJECTED_TRACKS_FILE, rejected_tracks)

        print(f"DEBUG: Трек сохранён. Теперь в файле: {len(rejected_tracks)} треков.")

        user_status[user_hash] = "rejected"
        await save_json_file(USER_STATUS_FILE, user_status)

        await message.answer("❌ Трек отклонён и добавлен в список отклонённых.")
        if user_id:
            try:
                await bot.send_message(
                    user_id,
                    f"❌ Твой трек был отклонён. 😔\n\n💬 Комментарий: {comment}\n\n"
                    "Ты можешь прислать новый трек!"
                )
            except Exception as e:
                print(f"⚠️ Не удалось отправить сообщение пользователю {user_id}: {e}")

    # Удаляем из очереди
    pending_tracks.pop(track_id)
    await save_json_file(PENDING_TRACKS_FILE, pending_tracks)
    await state.clear()

    # Автоматически показываем следующий трек, если есть
    if pending_tracks:
        await message.answer("➡️ Следующий трек на модерации:")
        await send_moderation_message(pending_tracks[0], 0)
    else:
        await message.answer("🎉 Все треки модерированы! 🎶")

# 🧩 Обработчики команд

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        "Привет! 🎧✨\n\nОтправь мне трек — аудио или ссылку на YouTube/Spotify и др. 🎶\n\n"
        "⚠️ Ты можешь прислать только один трек, который будет одобрен. "
        "Если он отклонён, можно прислать новый. 🔒"
    )

# ✅ Обновлённая команда: /tracks — теперь показывает одобренные и отклонённые
@dp.message(Command("tracks"))
async def cmd_tracks(message: types.Message):
    if message.from_user.id != MODERATOR_ID:
        await message.answer("❌ У тебя нет прав для просмотра треков.")
        return

    approved_tracks = await load_json_file(APPROVED_TRACKS_FILE)
    rejected_tracks = await load_json_file(REJECTED_TRACKS_FILE)

    response = ""

    # Одобрённые
    if approved_tracks:
        response += "🎧 Список одобренных треков:\n\n"
        for idx, track in enumerate(approved_tracks, 1):
            if 'file_id' in track:
                response += f"{idx}. [Аудио файл]\n"
            elif 'url' in track:
                title = track.get('url_title', 'Ссылка')
                response += f"{idx}. [{title}]({track['url']})\n"
    else:
        response += "🎧 Нет одобренных треков.\n\n"

    # Отклонённые
    if rejected_tracks:
        response += "❌ Список отклонённых треков:\n\n"
        for idx, track in enumerate(rejected_tracks, 1):
            user_hash = track.get("user_hash", "unknown")[:8]
            if 'file_id' in track:
                response += f"{idx}. [Аудио файл] (пользователь: {user_hash}...)\n"
            elif 'url' in track:
                title = track.get('url_title', 'Ссылка')
                response += f"{idx}. [{title}]({track['url']}) (пользователь: {user_hash}...)\n"
    else:
        response += "❌ Нет отклонённых треков.\n"

    await message.answer(response, parse_mode="Markdown")

@dp.message(Command("check"))
async def cmd_check(message: types.Message):
    if message.from_user.id != MODERATOR_ID:
        await message.answer("❌ У тебя нет прав для просмотра треков на модерации.")
        return

    pending_tracks = await load_json_file(PENDING_TRACKS_FILE)
    if not pending_tracks:
        await message.answer("📋 Нет треков на модерации.")
        return

    response = "📋 Треки на модерации:\n\n"
    for idx, track in enumerate(pending_tracks, 1):
        user_hash = track.get("user_hash", "unknown")[:8]
        if track["type"] == "audio":
            response += f"{idx}. Аудио (пользователь: {user_hash}...)\n"
        elif track["type"] == "url":
            title = track.get('url_title', 'Ссылка')
            response += f"{idx}. [{title}]({track['url']}) (пользователь: {user_hash}...)\n"
    await message.answer(response, parse_mode="Markdown")

@dp.message(Command("moderate"))
async def cmd_moderate(message: types.Message):
    if message.from_user.id != MODERATOR_ID:
        await message.answer("❌ У тебя нет прав для модерации.")
        return

    pending_tracks = await load_json_file(PENDING_TRACKS_FILE)
    if not pending_tracks:
        await message.answer("📋 Нет треков на модерации.")
        return

    await send_moderation_message(pending_tracks[0], 0)

# 📥 Обработка входящих сообщений от пользователей
@dp.message()
async def handle_message(message: types.Message, state: FSMContext):
    # Проверяем, находится ли модератор в состоянии ввода комментария
    current_state = await state.get_state()
    if current_state == ModerationComment.waiting_for_comment.state:
        return

    user_id = message.from_user.id
    user_hash = hash_user_id(user_id)

    # Проверка статуса пользователя
    user_status = await load_json_file(USER_STATUS_FILE, {})
    status = user_status.get(user_hash, "none")

    if status == "approved":
        await message.answer("✅ Твой трек уже одобрен. Больше нельзя присылать. 🎶")
        return

    # Определение типа трека
    track_data = None
    if message.audio:
        track_data = {"type": "audio", "file_id": message.audio.file_id}
    elif message.text:
        text = message.text.strip()
        platforms = ["youtube.com", "youtu.be", "spotify.com", "apple.co", "music.yandex", "vk.com"]
        if any(p in text for p in platforms):
            track_data = {
                "type": "url",
                "url": text,
                "url_title": "Ссылка на трек"
            }

    if not track_data:
        await message.answer("❌ Отправь аудиофайл или ссылку на трек. 🎧")
        return

    # Если статус "rejected", можно снова прислать трек
    if status == "pending":
        await message.answer("⏳ Твой предыдущий трек ещё на модерации. Подожди результата. 🎧")
        return

    # Сохраняем временно user_id для уведомления (удалится после модерации!)
    track_data["user_id"] = user_id
    track_data["user_hash"] = user_hash

    # Добавляем в очередь
    pending_tracks = await load_json_file(PENDING_TRACKS_FILE, [])
    pending_tracks.append(track_data)
    await save_json_file(PENDING_TRACKS_FILE, pending_tracks)

    # Обновляем статус
    user_status[user_hash] = "pending"
    await save_json_file(USER_STATUS_FILE, user_status)

    # ✅ Отправляем уведомление пользователю
    await message.answer("⏳ Твой трек на модерации... Ожидай результата! 🎶")

    await bot.send_message(
        MODERATOR_ID,
        f"🎵 Новый трек на модерации!\nПользователь: {user_hash[:8]}..."
    )

# 🎛️ Отправка трека модератору с кнопками
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

# ⚖️ Обработка нажатия кнопок модерации
@dp.callback_query(lambda c: c.data.startswith(("approve_", "reject_")))
async def handle_moderation(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != MODERATOR_ID:
        await callback.answer("❌ У тебя нет прав.")
        return

    action, track_id_str = callback.data.split("_", 1)
    track_id = int(track_id_str)

    pending_tracks = await load_json_file(PENDING_TRACKS_FILE)
    if track_id >= len(pending_tracks):
        await callback.answer("Ошибка: трек не найден.")
        return

    track = pending_tracks[track_id]
    user_hash = track["user_hash"]

    # Переключаем в состояние ввода комментария
    await state.set_state(ModerationComment.waiting_for_comment)
    await state.update_data(
        track_id=track_id,
        action=action,
        user_hash=user_hash
    )
    await callback.message.answer("📝 Введи комментарий для пользователя (например, причину отклонения):")
    await callback.message.delete()

# 🚀 Запуск
if __name__ == "__main__":
    print("🤖 Бот запускается...")
    asyncio.run(dp.start_polling(bot))