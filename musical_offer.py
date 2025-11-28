import asyncio
import hashlib
import json
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
import aiofiles
from dotenv import load_dotenv
import os

# Загрузка переменных окружения
load_dotenv()

# Константы
BOT_TOKEN = os.getenv("BOT_TOKEN")
MODERATOR_ID = int(os.getenv("MODERATOR_ID"))

# Пути к файлам
APPROVED_FILE = "approved_tracks.json"
REJECTED_FILE = "rejected_tracks.json"
PENDING_FILE = "pending_tracks.json"
HASHES_FILE = "user_hashes.json"

# Инициализация бота
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# 🛠️ Вспомогательные функции

async def load_json_file(filepath: str, default_value=None):
    """Загрузка JSON-файла"""
    if default_value is None:
        default_value = []
    try:
        async with aiofiles.open(filepath, "r", encoding="utf-8") as f:
            content = await f.read()
            return json.loads(content)
    except FileNotFoundError:
        return default_value
    except json.JSONDecodeError:
        return default_value

async def save_json_file(filepath: str, data):
    """Сохранение JSON-файла"""
    async with aiofiles.open(filepath, "w", encoding="utf-8") as f:
        await f.write(json.dumps(data, ensure_ascii=False, indent=2))

def hash_user_id(user_id: int) -> str:
    """Создание необратимого хеша от ID пользователя"""
    return hashlib.sha256(str(user_id).encode()).hexdigest()

# 🧩 Обработчики команд

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    keyboard = InlineKeyboardBuilder()
    keyboard.button(text="🎧 Предложить песню", callback_data="propose_track")
    keyboard.adjust(1)
    reply_markup = keyboard.as_markup()

    await message.answer(
        "Привет! 🎧✨\n\nОтправь мне трек — аудио или ссылку на YouTube/Spotify и др. 🎶\n\n⚠️ Ты можешь прислать **только один** трек. После этого возможность исчезнет! 🔒",
        reply_markup=reply_markup
    )

@dp.message(Command("tracks"))
async def cmd_tracks(message: types.Message):
    if message.from_user.id != MODERATOR_ID:
        await message.answer("❌ У тебя нет прав для просмотра треков.")
        return

    tracks = await load_json_file(APPROVED_FILE)
    if not tracks:
        await message.answer("📋 Пока нет одобренных треков.")
        return

    response = "🎧 Список одобренных треков:\n\n"
    for idx, track in enumerate(tracks, 1):
        if 'file_id' in track:
            response += f"{idx}. Аудио файл (file_id: {track['file_id']})\n"
        elif 'url' in track:
            import html
            title = html.escape(track['url_title'])
            safe_url = html.escape(track['url'])
            response += f"{idx}. &lt;a href='{safe_url}'&gt;{title}&lt;/a&gt;\n"
    await message.answer(response, parse_mode="HTML")

@dp.message(Command("all"))
async def cmd_all(message: types.Message):
    if message.from_user.id != MODERATOR_ID:
        await message.answer("❌ У тебя нет прав для просмотра всех треков.")
        return

    approved = await load_json_file(APPROVED_FILE)
    rejected = await load_json_file(REJECTED_FILE)

    total_approved = len(approved)
    total_rejected = len(rejected)

    response = f"📋 Всего треков:\n\n✅ Одобрено: {total_approved}\n❌ Отклонено: {total_rejected}\n\n"
    
    if approved:
        response += "✅ Одобренные:\n"
        for idx, track in enumerate(approved, 1):
            if 'file_id' in track:
                response += f"  {idx}. Аудио файл (file_id: {track['file_id']})\n"
            elif 'url' in track:
                # В HTML разметке нужно экранировать только &, <, >
                import html
                title = html.escape(track['url_title'])
                safe_url = html.escape(track['url'])
                response += f"  {idx}. &lt;a href='{safe_url}'&gt;{title}&lt;/a&gt;\n"
    
    if rejected:
        response += "\n❌ Отклонённые:\n"
        for idx, track in enumerate(rejected, 1):
            if 'file_id' in track:
                response += f"  {idx}. Аудио файл (file_id: {track['file_id']})\n"
            elif 'url' in track:
                # В HTML разметке нужно экранировать только &, <, >
                import html
                title = html.escape(track['url_title'])
                safe_url = html.escape(track['url'])
                response += f"  {idx}. &lt;a href='{safe_url}'&gt;{title}&lt;/a&gt;\n"

    await message.answer(response, parse_mode="HTML")

@dp.message(Command("moder"))
async def cmd_moder(message: types.Message):
    if message.from_user.id != MODERATOR_ID:
        await message.answer("❌ У тебя нет прав модератора.")
        return

    pending_tracks = await load_json_file(PENDING_FILE)
    if not pending_tracks:
        await message.answer("📋 Нет треков на модерации.")
        return

    for idx, track in enumerate(pending_tracks):
        await send_moderation_message(track, idx)

# 🧩 Обработчики нажатий на кнопки

@dp.callback_query(lambda c: c.data == "propose_track")
async def cb_propose_track(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    user_hash = hash_user_id(user_id)

    # Проверка, отправлял ли пользователь трек
    sent_hashes = await load_json_file(HASHES_FILE)
    if user_hash in sent_hashes:
        await callback.answer("⚠️ Ты уже отправил(а) свой трек! 🎶", show_alert=True)
        return

    await callback.answer()
    await callback.message.answer("🎵 Отправь мне трек — аудио или ссылку на YouTube/Spotify и др.")

@dp.callback_query(lambda c: c.data.startswith("edit_"))
async def cb_edit_track(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    user_hash = hash_user_id(user_id)

    # Проверяем, есть ли у пользователя отправленный трек
    pending_tracks = await load_json_file(PENDING_FILE)
    track_idx = -1
    for idx, track in enumerate(pending_tracks):
        if track.get("user_hash") == user_hash and track.get("status") == "pending":
            track_idx = idx
            break

    if track_idx == -1:
        await callback.answer("❌ У тебя нет активных треков для редактирования.", show_alert=True)
        return

    await callback.answer()
    await callback.message.answer("🎵 Отправь новый трек — аудио или ссылку. Текущий будет заменён.")

    # Помечаем, что пользователь хочет изменить трек
    # Это можно реализовать через временное состояние, но для простоты просто удалим старый и разрешим отправить новый
    pending_tracks.pop(track_idx)
    await save_json_file(PENDING_FILE, pending_tracks)

    # Удаляем хеш, чтобы пользователь мог отправить новый трек
    sent_hashes = await load_json_file(HASHES_FILE)
    if user_hash in sent_hashes:
        sent_hashes.remove(user_hash)
        await save_json_file(HASHES_FILE, sent_hashes)

# 🧩 Обработчики сообщений

@dp.message()
async def handle_message(message: types.Message):
    user_id = message.from_user.id
    user_hash = hash_user_id(user_id)

    # Проверка, отправлял ли пользователь трек
    sent_hashes = await load_json_file(HASHES_FILE)
    if user_hash in sent_hashes:
        await message.answer("⚠️ Ты уже отправил(а) свой трек! 🎶")
        return

    # Проверка на аудио или ссылку
    track_data = None

    if message.audio:
        track_data = {"type": "audio", "file_id": message.audio.file_id}
    elif message.text:
        text = message.text.strip()
        # Простая проверка на ссылку
        if any(platform in text for platform in ["youtube.com", "youtu.be", "spotify.com", "apple.co", "music.yandex", "vk.com"]):
            track_data = {"type": "url", "url": text, "url_title": f"Ссылка от {user_hash[:8]}..."}  # Анонимная подпись

    if not track_data:  # ✅ Исправлено
        await message.answer("❌ Отправь аудиофайл или ссылку на трек. 🎧")
        return

    # Добавляем в очередь на модерацию
    track_data["status"] = "pending"
    track_data["user_hash"] = user_hash

    pending_tracks = await load_json_file(PENDING_FILE, [])
    pending_tracks.append(track_data)
    await save_json_file(PENDING_FILE, pending_tracks)

    # Запоминаем, что пользователь отправил трек
    sent_hashes.append(user_hash)
    await save_json_file(HASHES_FILE, sent_hashes)

    await message.answer("✅ Твой трек отправлен на модерацию! ✨")

# 🧑‍💻 Функция отправки сообщения с кнопками модерации

async def send_moderation_message(track, track_id: int):
    keyboard = InlineKeyboardBuilder()
    keyboard.button(text="✅ Одобрить", callback_data=f"approve_{track_id}")
    keyboard.button(text="❌ Отклонить", callback_data=f"reject_{track_id}")
    reply_markup = keyboard.as_markup()

    if track["type"] == "audio":
        sent_message = await bot.send_audio(MODERATOR_ID, track["file_id"], caption=f"🎵 Трек от {track['user_hash'][:8]}...", reply_markup=reply_markup)
    elif track["type"] == "url":
        import html
        title = html.escape(track['url_title'])
        safe_url = html.escape(track['url'])
        sent_message = await bot.send_message(MODERATOR_ID, f"🔗 &lt;a href='{safe_url}'&gt;{title}&lt;/a&gt;\n\nПользователь: {track['user_hash'][:8]}...", reply_markup=reply_markup, parse_mode="HTML")

    # Сохраняем ID сообщения для последующего удаления
    track["message_id"] = sent_message.message_id
    pending_tracks = await load_json_file(PENDING_FILE)
    if track_id < len(pending_tracks):
        pending_tracks[track_id]["message_id"] = sent_message.message_id
        await save_json_file(PENDING_FILE, pending_tracks)

# 🧑‍💻 Обработчик нажатий на кнопки модерации

@dp.callback_query(lambda c: c.data.startswith("approve_") or c.data.startswith("reject_"))
async def handle_moderation(callback: types.CallbackQuery):
    if callback.from_user.id != MODERATOR_ID:
        await callback.answer("❌ У тебя нет прав.", show_alert=True)
        return

    action, track_id_str = callback.data.split("_", 1)
    track_id = int(track_id_str)

    pending_tracks = await load_json_file(PENDING_FILE)
    if track_id >= len(pending_tracks):
        await callback.answer("❌ Ошибка: трек не найден.", show_alert=True)
        return

    track = pending_tracks[track_id]

    # Удаляем сообщение с кнопками
    try:
        await bot.delete_message(MODERATOR_ID, track["message_id"])
    except Exception:
        pass  # Сообщение могло быть удалено вручную

    # Удаляем message_id из трека перед сохранением
    track.pop("message_id", None)

    if action == "approve":
        # Переносим в одобренные
        approved_tracks = await load_json_file(APPROVED_FILE)
        approved_tracks.append(track)
        await save_json_file(APPROVED_FILE, approved_tracks)
        await callback.answer("✅ Трек одобрен!")
    elif action == "reject":
        # Переносим в отклонённые
        rejected_tracks = await load_json_file(REJECTED_FILE)
        rejected_tracks.append(track)
        await save_json_file(REJECTED_FILE, rejected_tracks)
        await callback.answer("❌ Трек отклонён.")

    # Удаляем из очереди
    pending_tracks.pop(track_id)
    await save_json_file(PENDING_FILE, pending_tracks)

if __name__ == "__main__":
    print("🤖 Бот запускается...")
    asyncio.run(dp.start_polling(bot))