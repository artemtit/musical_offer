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

# =======================
# ЛОГИРОВАНИЕ
# =======================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

# =======================
# ENV
# =======================
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
MODERATOR_ID = int(os.getenv("MODERATOR_ID"))
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

DEADLINE = datetime(2025, 12, 26, 23, 59, 59, tzinfo=timezone.utc)

# =======================
# SUPABASE
# =======================
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# =======================
# BOT
# =======================
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# =======================
# FSM
# =======================
class ModerationComment(StatesGroup):
    waiting_for_comment = State()

# =======================
# UTILS
# =======================
def hash_user_id(user_id: int) -> str:
    return hashlib.sha256(str(user_id).encode()).hexdigest()[:16]


def get_all_pending_tracks():
    try:
        return supabase.table("pending_tracks") \
            .select("*") \
            .order("created_at", desc=False) \
            .execute().data
    except Exception as e:
        logging.error(f"get_all_pending_tracks error: {e}")
        return []


def get_track_by_id(track_id: str):
    try:
        res = supabase.table("pending_tracks") \
            .select("*") \
            .eq("id", track_id) \
            .execute()
        return res.data[0] if res.data else None
    except Exception as e:
        logging.error(f"get_track_by_id error: {e}")
        return None

# =======================
# /start
# =======================
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    user_hash = hash_user_id(user_id)

    try:
        pending = supabase.table("pending_tracks") \
            .select("id") \
            .eq("user_hash", user_hash) \
            .execute().data

        approved = supabase.table("approved_tracks") \
            .select("id") \
            .eq("user_hash", user_hash) \
            .execute().data
    except Exception as e:
        logging.error(f"/start load error: {e}")
        await message.answer("❌ Не удалось загрузить данные. Попробуй позже.")
        return

    sent = len(pending) + len(approved)
    remaining = max(0, 3 - sent)

    rules_url = "https://teletype.in/@artem2601/8pDqOmM9g4X"

    text = (
        f"🎧 <b>Party Music Bot</b>\n\n"
        f"Привет, <b>{message.from_user.first_name}</b>! 👋\n\n"
        f"Ты участвуешь в создании общего плейлиста для тусовки 🎉\n\n"

        f"📜 <b>Правила:</b>\n"
        f"• можно отправить <b>до 3 треков</b>\n"
        f"• принимаются 🎵 аудиофайлы и 🔗 ссылки\n"
        f"• треки должны соответствовать атмосфере мероприятия\n"
        f"• полный список правил — по ссылке ниже\n\n"

        f"🧑‍⚖️ <b>Модерация:</b>\n"
        f"• каждый трек проверяется вручную\n"
        f"• <b>одобренный</b> — попадает в плейлист\n"
        f"• <b>отклонённый</b> — можно заменить новым\n"
        f"• модератор не видит твой профиль\n\n"

        f"📊 <b>Твой прогресс:</b>\n"
        f"• отправлено: {sent}/3\n"
        f"• осталось: {remaining}\n\n"

        f"🔐 Анонимность гарантирована\n"
        f"⏰ Дедлайн: <b>22 декабря</b>"
    )

    keyboard = InlineKeyboardBuilder()
    keyboard.add(
        InlineKeyboardButton(
            text="📜 Правила тусовки",
            url=rules_url
        )
    )

    await message.answer(
        text,
        reply_markup=keyboard.as_markup(),
        parse_mode="HTML",
        disable_web_page_preview=True
    )


# =======================
# MODERATOR COMMANDS
# =======================
@dp.message(Command("check"))
async def cmd_check(message: types.Message):
    if message.from_user.id != MODERATOR_ID:
        return

    pending = get_all_pending_tracks()
    if not pending:
        await message.answer("📭 Нет треков")
        return

    text = ["📋 Треки на модерации:"]
    for i, t in enumerate(pending, 1):
        text.append(f"{i}. {t['type']} ({t['user_hash'][:8]}...)")

    await message.answer("\n".join(text))


@dp.message(Command("moderate"))
async def cmd_moderate(message: types.Message):
    if message.from_user.id != MODERATOR_ID:
        return

    pending = get_all_pending_tracks()
    if not pending:
        await message.answer("📭 Очередь пуста")
        return

    await send_moderation_message(pending[0], pending[0]["id"])


@dp.message(Command("tracks"))
async def cmd_tracks(message: types.Message):
    if message.from_user.id != MODERATOR_ID:
        await message.answer("❌ Нет прав.")
        return

    approved = supabase.table("approved_tracks").select("*").order("created_at").execute().data
    rejected = supabase.table("rejected_tracks").select("*").order("created_at").execute().data

    # 🎧 Одобренные
    await message.answer("🎧 <b>Одобренные треки:</b>", parse_mode="HTML")

    if not approved:
        await message.answer("— нет")
    else:
        for t in approved:
            if t["type"] == "audio" and t.get("file_id"):
                await message.answer_audio(
                    audio=t["file_id"],
                    caption="🎵 Одобренный трек"
                )
            elif t["type"] == "url":
                title = t.get("url_title", "Ссылка")
                await message.answer(
                    f"🔗 <a href='{t['url']}'>{title}</a>",
                    parse_mode="HTML"
                )

    # ❌ Отклонённые
    await message.answer("\n❌ <b>Отклонённые треки:</b>", parse_mode="HTML")

    if not rejected:
        await message.answer("— нет")
    else:
        for t in rejected:
            if t["type"] == "audio" and t.get("file_id"):
                await message.answer_audio(
                    audio=t["file_id"],
                    caption="🎵 Отклонённый трек"
                )
            elif t["type"] == "url":
                title = t.get("url_title", "Ссылка")
                await message.answer(
                    f"🔗 <a href='{t['url']}'>{title}</a>",
                    parse_mode="HTML"
                )


# =======================
# MODERATION FLOW
# =======================
@dp.callback_query(lambda c: c.data.startswith(("approve_", "reject_")))
async def handle_moderation(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != MODERATOR_ID:
        return

    action, track_id = callback.data.split("_", 1)
    track = get_track_by_id(track_id)

    if not track:
        await callback.answer("Трек не найден", show_alert=True)
        return

    await state.set_state(ModerationComment.waiting_for_comment)
    await state.update_data(action=action, track_id=track_id)

    await callback.message.answer("📝 Введи комментарий:")
    await callback.message.delete()


@dp.message(ModerationComment.waiting_for_comment)
async def process_comment(message: types.Message, state: FSMContext):
    data = await state.get_data()
    action = data["action"]
    track_id = data["track_id"]

    track = get_track_by_id(track_id)
    if not track:
        await message.answer("❌ Трек уже обработан")
        await state.clear()
        return

    safe_track = {
        "type": track["type"],
        "file_id": track.get("file_id"),
        "url": track.get("url"),
        "url_title": track.get("url_title"),
        "user_hash": track["user_hash"],
    }

    try:
        if action == "approve":
            res = supabase.table("approved_tracks").insert(safe_track).execute()
        else:
            res = supabase.table("rejected_tracks").insert(safe_track).execute()

        if not res.data:
            raise Exception("Insert failed")

        supabase.table("pending_tracks").delete().eq("id", track_id).execute()

        await message.answer("✅ Решение сохранено")

        if track.get("user_id"):
            await bot.send_message(
                track["user_id"],
                "🎶 Твой трек обработан!",
                parse_mode="HTML"
            )

    except Exception as e:
        logging.error(f"MODERATION ERROR: {e}")
        logging.error(f"TRACK DATA: {safe_track}")
        await message.answer("❌ Ошибка сохранения решения")

    await state.clear()

    pending = get_all_pending_tracks()
    if pending:
        await send_moderation_message(pending[0], pending[0]["id"])
    else:
        await message.answer("🎉 Очередь пуста")

# =======================
# SEND TO MODERATOR
# =======================
async def send_moderation_message(track, track_id):
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text="✅ Одобрить", callback_data=f"approve_{track_id}"),
        InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject_{track_id}")
    )

    if track["type"] == "audio":
        await bot.send_audio(
            MODERATOR_ID,
            track["file_id"],
            caption="🎵 Трек на модерации",
            reply_markup=kb.as_markup()
        )
    else:
        await bot.send_message(
            MODERATOR_ID,
            track["url"],
            reply_markup=kb.as_markup()
        )

# =======================
# USER HANDLER
# =======================
@dp.message()
async def handle_user_message(message: types.Message):
    if message.text and message.text.startswith("/"):
        return

    now = datetime.fromisoformat(message.date.isoformat()).replace(tzinfo=timezone.utc)
    if now > DEADLINE:
        await message.answer("⏰ Приём завершён")
        return

    user_id = message.from_user.id
    user_hash = hash_user_id(user_id)

    pending = supabase.table("pending_tracks").select("id").eq("user_hash", user_hash).execute().data
    approved = supabase.table("approved_tracks").select("id").eq("user_hash", user_hash).execute().data

    if len(pending) + len(approved) >= 3:
        await message.answer("❌ Лимит исчерпан")
        return

    track_data = None
    if message.audio:
        track_data = {"type": "audio", "file_id": message.audio.file_id}
    elif message.text:
        track_data = {"type": "url", "url": message.text, "url_title": "Ссылка"}

    if not track_data:
        await message.answer("❌ Отправь аудио или ссылку")
        return

    track_data["user_id"] = user_id
    track_data["user_hash"] = user_hash

    supabase.table("pending_tracks").insert(track_data).execute()
    await message.answer("✅ Трек отправлен на модерацию")

# =======================
# FLASK (RENDER)
# =======================
app = Flask(__name__)

@app.route("/")
def home():
    return {"status": "ok"}

def run_flask():
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))

# =======================
# START
# =======================
if __name__ == "__main__":
    Thread(target=run_flask, daemon=True).start()
    asyncio.run(dp.start_polling(bot))
