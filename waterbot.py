

import os
import io
import json
import logging
import tempfile
import asyncio
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from datetime import datetime
from PIL import Image, ImageEnhance
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes, ChatMemberHandler
)
from telegram.error import TimedOut, NetworkError, RetryAfter, TelegramError, Forbidden, BadRequest

load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ── Пути ──────────────────────────────────────────────────
WATERMARK_PATH     = "watermark.png"
ALLOWED_USERS_PATH = "allowed_users.json"
SETTINGS_PATH      = "settings.json"
CHATS_PATH         = "chats.json"
USER_WM_DIR        = "user_watermarks"
CHAT_WM_DIR        = "watermarks"
ACTIVITY_LOG_PATH  = "activity.json"
CREDITS_PATH       = "credits.json"
BLOCKED_USERS_PATH = "blocked_users.json"
BROADCAST_LOG_PATH = "broadcast_log.json"
CREDIT_COST_PHOTO  = 1
CREDIT_COST_VIDEO  = 1

# ── Лимиты и защита от перегруза ─────────────────────────
MAX_PHOTO_MB       = int(os.environ.get("MAX_PHOTO_MB", "20"))
MAX_VIDEO_MB       = int(os.environ.get("MAX_VIDEO_MB", "80"))
MAX_VIDEO_SECONDS  = int(os.environ.get("MAX_VIDEO_SECONDS", "60"))
LOW_CREDITS_ALERT  = int(os.environ.get("LOW_CREDITS_ALERT", "5"))
PROCESS_CONCURRENCY = int(os.environ.get("PROCESS_CONCURRENCY", "2"))
PROCESS_SEMAPHORE = asyncio.Semaphore(PROCESS_CONCURRENCY)


OWNER_ID = 1234567890
DEFAULT_SETTINGS = {"opacity": 0.5, "scale": 0.3, "position": "bottom-right"}
POSITIONS = ["top-left", "top-right", "bottom-left", "bottom-right", "center"]
POSITION_LABELS = {
    "top-left":     "↖ Верхний левый",
    "top-right":    "↗ Верхний правый",
    "bottom-left":  "↙ Нижний левый",
    "bottom-right": "↘ Нижний правый",
    "center":       "⊙ По центру",
}
CHAT_TYPE_EMOJI = {"channel": "📢", "group": "👥", "supergroup": "👥", "private": "👤"}


# ── Обычная клавиатура под строкой ввода ─────────────────
BTN_MAIN       = "🏠 Главное"
BTN_WM         = "🖼 Вотермарка"
BTN_PREVIEW    = "👁 Превью"
BTN_CHATS      = "📋 Чаты"
BTN_CHANNELS   = "📢 Каналы"
BTN_USERS      = "👥 Пользователи"
BTN_ACTIVITY   = "📊 Активность"
BTN_HELP       = "❓ Помощь"
BTN_ADD_USER   = "➕ Добавить пользователя"
BTN_ADD_CREDITS = "💳 Начислить кредиты"
BTN_BIND_CHANNEL = "🔗 Привязать канал"
BTN_BUY_CREDITS = "💳 Купить кредиты"
BTN_ADMIN_STATS = "📈 Статистика"
BTN_BROADCAST  = "📣 Рассылка"
BTN_BROADCAST_SEND = "✅ Отправить рассылку"
BTN_BROADCAST_CANCEL = "❌ Отменить рассылку"
BTN_BLOCKED_USERS = "🚫 Отписались"
BTN_BACK       = "⬅️ Назад"

ADMIN_BUTTONS = {
    BTN_MAIN, BTN_WM, BTN_PREVIEW, BTN_CHATS, BTN_CHANNELS, BTN_USERS,
    BTN_ACTIVITY, BTN_HELP, BTN_ADD_USER, BTN_ADD_CREDITS, BTN_BIND_CHANNEL, BTN_BUY_CREDITS, BTN_ADMIN_STATS, BTN_BROADCAST, BTN_BROADCAST_SEND, BTN_BROADCAST_CANCEL, BTN_BLOCKED_USERS, BTN_BACK,
}


def admin_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton(BTN_MAIN), KeyboardButton(BTN_WM)],
            [KeyboardButton(BTN_CHATS), KeyboardButton(BTN_CHANNELS)],
            [KeyboardButton(BTN_USERS), KeyboardButton(BTN_ACTIVITY)],
            [KeyboardButton(BTN_ADMIN_STATS), KeyboardButton(BTN_ADD_CREDITS)],
            [KeyboardButton(BTN_BROADCAST), KeyboardButton(BTN_BLOCKED_USERS)],
            [KeyboardButton(BTN_PREVIEW), KeyboardButton(BTN_BIND_CHANNEL)],
            [KeyboardButton(BTN_BUY_CREDITS), KeyboardButton(BTN_HELP)],
        ],
        resize_keyboard=True,
        one_time_keyboard=False,
        input_field_placeholder="Выбери действие"
    )


def user_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton(BTN_MAIN), KeyboardButton(BTN_WM)],
            [KeyboardButton(BTN_PREVIEW), KeyboardButton(BTN_BIND_CHANNEL)],
            [KeyboardButton(BTN_HELP)],
        ],
        resize_keyboard=True,
        one_time_keyboard=False,
        input_field_placeholder="Выбери действие"
    )


def chats_keyboard() -> ReplyKeyboardMarkup:
    chats = load_chats()
    rows = []
    for cid, info in chats.items():
        if not info.get("active"):
            continue
        emoji = CHAT_TYPE_EMOJI.get(info.get("type", ""), "💬")
        title = info.get("title", cid)
        rows.append([KeyboardButton(f"{emoji} {title[:28]} | {cid}")])
    rows.append([KeyboardButton(BTN_BACK), KeyboardButton(BTN_MAIN)])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True, one_time_keyboard=False)




def channels_keyboard() -> ReplyKeyboardMarkup:
    chats = load_chats()
    rows = []
    for cid, info in chats.items():
        if not info.get("active") or info.get("type") != "channel":
            continue
        title = info.get("title", cid)
        rows.append([KeyboardButton(f"📢 {title[:28]} | {cid}")])
    rows.append([KeyboardButton(BTN_BACK), KeyboardButton(BTN_MAIN)])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True, one_time_keyboard=False)

def get_keyboard(uid: int) -> ReplyKeyboardMarkup:
    return admin_keyboard() if uid == OWNER_ID else user_keyboard()

os.makedirs(USER_WM_DIR, exist_ok=True)
os.makedirs(CHAT_WM_DIR, exist_ok=True)


# ══════════════════════════════════════════════════════════
# Безопасное чтение/запись JSON
# ══════════════════════════════════════════════════════════

def safe_load_json(path: str, default):
    """Читает JSON. Если файл пустой/битый — сохраняет .broken и возвращает default."""
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read().strip()
        if not content:
            return default
        return json.loads(content)
    except json.JSONDecodeError as e:
        broken_path = f"{path}.broken_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        try:
            os.replace(path, broken_path)
            logger.error(f"Повреждён JSON {path}: {e}. Файл перенесён в {broken_path}")
        except Exception as move_error:
            logger.error(f"Повреждён JSON {path}: {e}. Не удалось перенести: {move_error}")
        return default
    except Exception as e:
        logger.error(f"Ошибка чтения JSON {path}: {e}")
        return default


def safe_save_json(path: str, data, ensure_ascii: bool = False):
    """Атомарно сохраняет JSON, чтобы файл не обрезался при сбое."""
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=ensure_ascii)
    os.replace(tmp_path, path)


# ══════════════════════════════════════════════════════════
# Лог активности
# ══════════════════════════════════════════════════════════

def log_activity(user_id: int, username: str, action: str, detail: str = ""):
    try:
        log = []
        if os.path.exists(ACTIVITY_LOG_PATH):
            log = safe_load_json(ACTIVITY_LOG_PATH, [])
        log.append({
            "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "user_id": user_id,
            "username": username or str(user_id),
            "action": action,
            "detail": detail
        })
        log = log[-200:]
        safe_save_json(ACTIVITY_LOG_PATH, log, ensure_ascii=False)
    except Exception as e:
        logger.warning(f"log_activity error: {e}")

def get_activity_log(limit: int = 20) -> list:
    data = safe_load_json(ACTIVITY_LOG_PATH, [])
    return data[-limit:] if isinstance(data, list) else []


# ══════════════════════════════════════════════════════════
# Отписавшиеся / заблокировавшие бота
# ══════════════════════════════════════════════════════════

def load_blocked_users() -> dict:
    data = safe_load_json(BLOCKED_USERS_PATH, {})
    return data if isinstance(data, dict) else {}

def save_blocked_users(data: dict):
    safe_save_json(BLOCKED_USERS_PATH, data, ensure_ascii=False)

def mark_blocked_user(user_id: int, reason: str = "blocked"):
    blocked = load_blocked_users()
    blocked[str(user_id)] = {
        "user_id": user_id,
        "reason": reason,
        "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    save_blocked_users(blocked)

def unmark_blocked_user(user_id: int):
    blocked = load_blocked_users()
    if str(user_id) in blocked:
        blocked.pop(str(user_id), None)
        save_blocked_users(blocked)

def blocked_users_text() -> str:
    blocked = load_blocked_users()
    if not blocked:
        return "🚫 *Отписавшиеся / заблокировавшие бота*\n\nПока никого нет."
    lines = [f"🚫 *Отписавшиеся / заблокировавшие бота:* {len(blocked)}\n"]
    for uid, info in list(blocked.items())[-50:]:
        lines.append(f"• `{uid}` — {info.get('ts', '—')} — {info.get('reason', 'blocked')}")
    return "\n".join(lines)

def save_broadcast_log(entry: dict):
    log = safe_load_json(BROADCAST_LOG_PATH, [])
    if not isinstance(log, list):
        log = []
    log.append(entry)
    log = log[-100:]
    safe_save_json(BROADCAST_LOG_PATH, log, ensure_ascii=False)

async def send_broadcast_to_users(ctx: ContextTypes.DEFAULT_TYPE, text: str) -> dict:
    users = sorted(load_allowed_users())
    sent = 0
    blocked = 0
    failed = 0
    recipients = [u for u in users if u != OWNER_ID]
    for user_id in recipients:
        try:
            await ctx.bot.send_message(user_id, text)
            sent += 1
            unmark_blocked_user(user_id)
            await asyncio.sleep(0.05)
        except Forbidden as e:
            blocked += 1
            mark_blocked_user(user_id, "blocked_bot")
        except BadRequest as e:
            msg = str(e).lower()
            if "chat not found" in msg or "bot was blocked" in msg or "user is deactivated" in msg:
                blocked += 1
                mark_blocked_user(user_id, str(e)[:120])
            else:
                failed += 1
                logger.warning(f"broadcast badrequest {user_id}: {e}")
        except RetryAfter as e:
            await asyncio.sleep(min(int(e.retry_after), 30))
            failed += 1
        except Exception as e:
            failed += 1
            logger.warning(f"broadcast failed {user_id}: {e}")
    result = {
        "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "total": len(recipients),
        "sent": sent,
        "blocked": blocked,
        "failed": failed,
        "text_preview": text[:120],
    }
    save_broadcast_log(result)
    return result


# ══════════════════════════════════════════════════════════
# Реестр чатов
# ══════════════════════════════════════════════════════════

def load_chats() -> dict:
    data = safe_load_json(CHATS_PATH, {})
    return data if isinstance(data, dict) else {}

def save_chats(chats: dict):
    safe_save_json(CHATS_PATH, chats, ensure_ascii=False)

def register_chat(chat_id: int, title: str, chat_type: str, owner_user_id: int | None = None):
    chats = load_chats()
    key = str(chat_id)
    if key not in chats:
        chats[key] = {
            "title": title, "type": chat_type,
            "watermark": None,
            "settings": DEFAULT_SETTINGS.copy(),
            "active": True,
            "allowed_users": [],
            # Владелец/клиент, который добавил бота в свой чат/канал.
            # Нужен для каналов: у channel_post нет from_user, поэтому иначе
            # невозможно понять, чью личную вотермарку ставить.
            "owner_user_id": owner_user_id
        }
    else:
        chats[key]["title"] = title
        chats[key]["type"] = chat_type
        chats[key]["active"] = True
        chats[key].setdefault("allowed_users", [])
        chats[key].setdefault("settings", DEFAULT_SETTINGS.copy())
        # Не перетираем владельца, если он уже был задан.
        # Но если владелец пустой — привязываем к пользователю, который добавил бота.
        if owner_user_id and not chats[key].get("owner_user_id"):
            chats[key]["owner_user_id"] = owner_user_id
    save_chats(chats)

def unregister_chat(chat_id: int):
    chats = load_chats()
    key = str(chat_id)
    if key in chats:
        chats[key]["active"] = False
        save_chats(chats)

def get_chat_info(chat_id: int):
    return load_chats().get(str(chat_id))

def get_chat_watermark_path(chat_id: int) -> str:
    info = get_chat_info(chat_id)
    if info and info.get("watermark") and os.path.exists(info["watermark"]):
        return info["watermark"]
    return WATERMARK_PATH

def get_chat_owner_id(chat_id: int):
    info = get_chat_info(chat_id)
    if not info:
        return None
    owner_id = info.get("owner_user_id")
    try:
        return int(owner_id) if owner_id else None
    except (TypeError, ValueError):
        return None

def get_watermark_for_user(user_id: int) -> str | None:
    """
    Правило безопасности:
    • OWNER_ID может использовать свою личную или глобальную watermark.png;
    • добавленные пользователи НЕ получают глобальную вотермарку;
    • добавленные пользователи работают только со своей user_watermarks/<id>.png.
    """
    user_wm = get_user_watermark_path(user_id)
    if user_wm and os.path.exists(user_wm):
        return user_wm
    if user_id == OWNER_ID and os.path.exists(WATERMARK_PATH):
        return WATERMARK_PATH
    return None

def get_watermark_for_chat_or_owner(chat_id: int) -> str | None:
    """
    Для каналов Telegram не отдаёт автора поста, поэтому используем owner_user_id,
    который сохраняется при добавлении бота в канал/чат.

    ВАЖНО: если канал принадлежит добавленному пользователю, глобальная вотермарка
    владельца бота НЕ используется. Пользователь обязан загрузить свою.
    """
    owner_id = get_chat_owner_id(chat_id)
    if owner_id:
        return get_watermark_for_user(owner_id)

    # Если владелец канала не определён, не подставляем глобальную,
    # чтобы чужие каналы случайно не получили watermark владельца бота.
    return None



def get_channel_effective_watermark(chat_id: int) -> str | None:
    """
    Приоритет для канала:
    1) отдельная вотермарка канала из админ-панели;
    2) личная вотермарка владельца/клиента канала;
    3) ничего. Глобальную вотермарку владельца чужим каналам не ставим.
    """
    info = get_chat_info(chat_id)
    if info and info.get("watermark") and os.path.exists(info["watermark"]):
        return info["watermark"]
    return get_watermark_for_chat_or_owner(chat_id)

def get_chat_settings(chat_id: int) -> dict:
    info = get_chat_info(chat_id)
    if info and info.get("settings"):
        s = DEFAULT_SETTINGS.copy()
        s.update(info["settings"])
        return s
    return load_settings()

def save_chat_settings(chat_id: int, settings: dict):
    chats = load_chats()
    key = str(chat_id)
    if key in chats:
        chats[key]["settings"] = settings
        save_chats(chats)

def save_chat_watermark(chat_id: int, path: str):
    chats = load_chats()
    key = str(chat_id)
    if key in chats:
        chats[key]["watermark"] = path or None
        save_chats(chats)

def get_chat_allowed(chat_id: int) -> list:
    info = get_chat_info(chat_id)
    return info.get("allowed_users", []) if info else []

def add_chat_allowed(chat_id: int, user_id: int):
    chats = load_chats()
    key = str(chat_id)
    if key in chats:
        chats[key].setdefault("allowed_users", [])
        if user_id not in chats[key]["allowed_users"]:
            chats[key]["allowed_users"].append(user_id)
        save_chats(chats)

def remove_chat_allowed(chat_id: int, user_id: int):
    chats = load_chats()
    key = str(chat_id)
    if key in chats:
        chats[key].setdefault("allowed_users", [])
        chats[key]["allowed_users"] = [u for u in chats[key]["allowed_users"] if u != user_id]
        save_chats(chats)

def is_allowed_in_chat(user_id: int, chat_id: int) -> bool:
    if user_id == OWNER_ID:
        return True
    if is_allowed(user_id):
        return True
    return user_id in get_chat_allowed(chat_id)


# ══════════════════════════════════════════════════════════
# Вотермарка пользователя
# ══════════════════════════════════════════════════════════

def get_user_watermark_path(user_id: int):
    p = os.path.join(USER_WM_DIR, f"{user_id}.png")
    return p if os.path.exists(p) else None

def save_user_watermark(user_id: int, img_bytes: bytes) -> str:
    p = os.path.join(USER_WM_DIR, f"{user_id}.png")
    img = Image.open(io.BytesIO(img_bytes))
    img.save(p, format="PNG")
    return p

def delete_user_watermark(user_id: int) -> bool:
    """Удаляет личную вотермарку пользователя."""
    p = os.path.join(USER_WM_DIR, f"{user_id}.png")
    if os.path.exists(p):
        os.remove(p)
        return True
    return False


# ══════════════════════════════════════════════════════════
# Глобальные настройки
# ══════════════════════════════════════════════════════════

def load_settings() -> dict:
    s = safe_load_json(SETTINGS_PATH, DEFAULT_SETTINGS.copy())
    if not isinstance(s, dict):
        s = DEFAULT_SETTINGS.copy()
    for k, v in DEFAULT_SETTINGS.items():
        s.setdefault(k, v)
    return s

def save_settings(s: dict):
    safe_save_json(SETTINGS_PATH, s, ensure_ascii=False)


# ══════════════════════════════════════════════════════════
# Доступ
# ══════════════════════════════════════════════════════════

def load_allowed_users() -> set:
    data = safe_load_json(ALLOWED_USERS_PATH, [])
    return set(data) if isinstance(data, list) else set()

def save_allowed_users(users: set):
    safe_save_json(ALLOWED_USERS_PATH, list(users), ensure_ascii=False)

def is_allowed(user_id: int) -> bool:
    if OWNER_ID != 0 and user_id == OWNER_ID:
        return True
    return user_id in load_allowed_users()


# ══════════════════════════════════════════════════════════
# Кредиты пользователей
# ══════════════════════════════════════════════════════════

def load_credits() -> dict:
    data = safe_load_json(CREDITS_PATH, {})
    return data if isinstance(data, dict) else {}

def save_credits(credits: dict):
    safe_save_json(CREDITS_PATH, credits, ensure_ascii=False)

def get_user_credits(user_id: int) -> int | None:
    # Владелец пользуется без лимита.
    if user_id == OWNER_ID:
        return None
    credits = load_credits()
    try:
        return int(credits.get(str(user_id), 0))
    except (TypeError, ValueError):
        return 0

def set_user_credits(user_id: int, amount: int):
    if user_id == OWNER_ID:
        return
    credits = load_credits()
    credits[str(user_id)] = max(0, int(amount))
    save_credits(credits)

def add_user_credits(user_id: int, amount: int) -> int:
    if user_id == OWNER_ID:
        return 999999999
    new_balance = max(0, get_user_credits(user_id) + int(amount))
    set_user_credits(user_id, new_balance)
    return new_balance

def has_credits(user_id: int, cost: int = 1) -> bool:
    if user_id == OWNER_ID:
        return True
    return get_user_credits(user_id) >= cost

def deduct_credits(user_id: int, cost: int = 1) -> int | None:
    if user_id == OWNER_ID:
        return None
    return add_user_credits(user_id, -abs(int(cost)))

def credits_label(user_id: int) -> str:
    if user_id == OWNER_ID:
        return "∞"
    return str(get_user_credits(user_id))

async def notify_no_credits_message(msg, uid: int):
    await msg.reply_text(
        "⛔ Кредиты закончились. Доступ к обработке заблокирован.\n"
        "Обратись к владельцу бота, чтобы он начислил новые кредиты.",
        reply_markup=get_keyboard(uid)
    )

async def notify_no_credits_channel(ctx: ContextTypes.DEFAULT_TYPE, channel_id: int, owner_id: int | None):
    text = (
        f"⛔ Канал `{channel_id}` не обработан: "
        f"у пользователя `{owner_id or 'не определён'}` закончились кредиты."
    )
    if OWNER_ID:
        try:
            await ctx.bot.send_message(OWNER_ID, text, parse_mode="Markdown")
        except Exception:
            pass
    if owner_id and owner_id != OWNER_ID:
        try:
            await ctx.bot.send_message(
                owner_id,
                "⛔ Кредиты закончились. Пост в канале не обработан. Обратись к владельцу бота."
            )
        except Exception:
            pass



async def notify_low_credits(ctx: ContextTypes.DEFAULT_TYPE, user_id: int, balance: int | None):
    if user_id == OWNER_ID or balance is None:
        return
    if balance == LOW_CREDITS_ALERT:
        try:
            await ctx.bot.send_message(
                user_id,
                f"⚠️ Осталось {balance} кредитов. Скоро доступ к обработке остановится. Нажми «{BTN_BUY_CREDITS}» для пополнения."
            )
        except Exception:
            pass


def _size_mb(size_bytes: int | None) -> float:
    return (size_bytes or 0) / 1024 / 1024


def _get_media_file_size(msg, is_doc: bool) -> int | None:
    try:
        if is_doc and getattr(msg, "document", None):
            return msg.document.file_size
        if getattr(msg, "photo", None):
            return msg.photo[-1].file_size
        if getattr(msg, "video", None):
            return msg.video.file_size
    except Exception:
        return None
    return None


def _get_video_duration(msg, is_doc: bool) -> int | None:
    try:
        if getattr(msg, "video", None):
            return msg.video.duration
    except Exception:
        return None
    return None


async def validate_media_limits(msg, media_type: str, is_doc: bool) -> bool:
    size = _get_media_file_size(msg, is_doc)
    if media_type == "photo" and size and _size_mb(size) > MAX_PHOTO_MB:
        try:
            await msg.reply_text(f"❌ Фото слишком большое: {_size_mb(size):.1f} MB. Лимит: {MAX_PHOTO_MB} MB.")
        except Exception:
            pass
        return False
    if media_type == "video":
        duration = _get_video_duration(msg, is_doc)
        if duration and duration > MAX_VIDEO_SECONDS:
            try:
                await msg.reply_text(f"❌ Видео слишком длинное: {duration} сек. Лимит: {MAX_VIDEO_SECONDS} сек.")
            except Exception:
                pass
            return False
        if size and _size_mb(size) > MAX_VIDEO_MB:
            try:
                await msg.reply_text(f"❌ Видео слишком большое: {_size_mb(size):.1f} MB. Лимит: {MAX_VIDEO_MB} MB.")
            except Exception:
                pass
            return False
    return True


async def send_purchase_request(ctx: ContextTypes.DEFAULT_TYPE, user_id: int, username: str | None):
    uname = f"@{username}" if username else str(user_id)
    if OWNER_ID:
        try:
            await ctx.bot.send_message(
                OWNER_ID,
                f"💳 Заявка на покупку кредитов\n👤 {uname} (`{user_id}`)\nБаланс: *{credits_label(user_id)}*",
                parse_mode="Markdown"
            )
        except Exception:
            pass


def build_admin_stats_text() -> str:
    users = load_allowed_users()
    chats = load_chats()
    credits = load_credits()
    log = get_activity_log(200)
    photo_count = sum(1 for e in log if "photo" in e.get("action", ""))
    video_count = sum(1 for e in log if "video" in e.get("action", ""))
    channels_count = sum(1 for i in chats.values() if i.get("active") and i.get("type") == "channel")
    groups_count = sum(1 for i in chats.values() if i.get("active") and i.get("type") in ("group", "supergroup"))
    blocked_count = len(load_blocked_users())
    zero_users = []
    low_users = []
    for u in sorted(users):
        bal = int(credits.get(str(u), 0))
        if bal <= 0:
            zero_users.append(str(u))
        elif bal <= LOW_CREDITS_ALERT:
            low_users.append(f"{u}: {bal}")
    low_text = "\n".join(low_users[:20]) if low_users else "—"
    return (
        "📈 *Статистика бота*\n\n"
        f"👥 Пользователей: *{len(users)}*\n"
        f"📢 Каналов: *{channels_count}*\n"
        f"👥 Групп: *{groups_count}*\n"
        f"📸 Фото в последних 200 действиях: *{photo_count}*\n"
        f"🎬 Видео в последних 200 действиях: *{video_count}*\n"
        f"⛔ С нулём кредитов: *{len(zero_users)}*\n"
        f"⚠️ Низкий баланс: *{len(low_users)}*\n"
        f"🚫 Отписались/заблокировали: *{blocked_count}*\n\n"
        f"Низкий баланс:\n{low_text}"
    )

# ══════════════════════════════════════════════════════════
# Наложение вотермарки — фото
# ══════════════════════════════════════════════════════════

def _build_watermarked_image(photo_bytes: bytes, wm_path: str, settings: dict) -> bytes:
    if not os.path.exists(wm_path):
        raise FileNotFoundError(f"Вотермарка не найдена: {wm_path}")
    s = settings
    photo = Image.open(io.BytesIO(photo_bytes)).convert("RGBA")
    wm_orig = Image.open(wm_path).convert("RGBA")
    wm_w = int(photo.width * s["scale"])
    wm_h = int(wm_orig.height * (wm_w / wm_orig.width))
    watermark = wm_orig.resize((wm_w, wm_h), Image.LANCZOS)
    r, g, b, a = watermark.split()
    a = ImageEnhance.Brightness(a).enhance(s["opacity"])
    watermark.putalpha(a)
    margin = 20
    pos_map = {
        "top-left":     (margin, margin),
        "top-right":    (photo.width - wm_w - margin, margin),
        "bottom-left":  (margin, photo.height - wm_h - margin),
        "bottom-right": (photo.width - wm_w - margin, photo.height - wm_h - margin),
        "center":       ((photo.width - wm_w) // 2, (photo.height - wm_h) // 2),
    }
    position = pos_map.get(s["position"], pos_map["bottom-right"])
    layer = Image.new("RGBA", photo.size, (0, 0, 0, 0))
    layer.paste(watermark, position, watermark)
    result = Image.alpha_composite(photo, layer).convert("RGB")
    out = io.BytesIO()
    result.save(out, format="JPEG", quality=95)
    return out.getvalue()

def apply_watermark_global(photo_bytes: bytes) -> bytes:
    return _build_watermarked_image(photo_bytes, WATERMARK_PATH, load_settings())

def apply_watermark_for_chat(photo_bytes: bytes, chat_id: int) -> bytes:
    return _build_watermarked_image(
        photo_bytes, get_chat_watermark_path(chat_id), get_chat_settings(chat_id)
    )


# ══════════════════════════════════════════════════════════
# Наложение вотермарки — видео
# ══════════════════════════════════════════════════════════

async def apply_watermark_video(video_bytes: bytes, wm_path: str, settings: dict) -> bytes:
    try:
        from moviepy.editor import VideoFileClip, ImageClip, CompositeVideoClip
    except ImportError:
        raise RuntimeError("moviepy не установлен. Выполни: pip install moviepy")

    if not os.path.exists(wm_path):
        raise FileNotFoundError(f"Вотермарка не найдена: {wm_path}")

    s = settings
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp_in:
        tmp_in.write(video_bytes)
        tmp_in_path = tmp_in.name
    tmp_out_path = tmp_in_path.replace(".mp4", "_wm.mp4")
    wm_tmp_path  = tmp_in_path.replace(".mp4", "_wm_logo.png")

    def _process():
        clip = VideoFileClip(tmp_in_path)
        w, h = clip.size
        wm_w = int(w * s["scale"])
        wm_img = Image.open(wm_path).convert("RGBA")
        wm_h = int(wm_img.height * (wm_w / wm_img.width))
        wm_img = wm_img.resize((wm_w, wm_h), Image.LANCZOS)
        r, g, b, a = wm_img.split()
        a = ImageEnhance.Brightness(a).enhance(s["opacity"])
        wm_img.putalpha(a)
        wm_img.save(wm_tmp_path)
        margin = 20
        pos_map = {
            "top-left":     (margin, margin),
            "top-right":    (w - wm_w - margin, margin),
            "bottom-left":  (margin, h - wm_h - margin),
            "bottom-right": (w - wm_w - margin, h - wm_h - margin),
            "center":       ((w - wm_w) // 2, (h - wm_h) // 2),
        }
        px, py = pos_map.get(s["position"], pos_map["bottom-right"])
        wm_clip = (
            ImageClip(wm_tmp_path)
            .set_duration(clip.duration)
            .set_position((px, py))
        )
        final = CompositeVideoClip([clip, wm_clip])
        final.write_videofile(
            tmp_out_path, codec="libx264", audio_codec="aac",
            logger=None, threads=2
        )
        clip.close()
        final.close()

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _process)

    with open(tmp_out_path, "rb") as f:
        result = f.read()
    for p in (tmp_in_path, tmp_out_path, wm_tmp_path):
        try:
            os.unlink(p)
        except Exception:
            pass
    return result


# ══════════════════════════════════════════════════════════
# Отслеживание чатов
# ══════════════════════════════════════════════════════════

async def track_chat_member(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    result = update.my_chat_member
    if not result:
        return
    chat   = result.chat
    status = result.new_chat_member.status

    if status in ("member", "administrator"):
        added_by = result.from_user.id if result.from_user else None
        # Привязываем канал/чат только к пользователю с доступом.
        # Если бота добавил посторонний — чат зарегистрируется, но без owner_user_id.
        if added_by and not is_allowed(added_by):
            added_by = None
        register_chat(chat.id, chat.title or str(chat.id), chat.type, added_by)
        logger.info(f"Бот добавлен: {chat.title} ({chat.id})")
        if OWNER_ID:
            emoji = CHAT_TYPE_EMOJI.get(chat.type, "💬")
            try:
                await ctx.bot.send_message(
                    OWNER_ID,
                    f"✅ Бот добавлен!\n{emoji} *{chat.title}*\n🆔 `{chat.id}`\n📁 {chat.type}",
                    parse_mode="Markdown"
                )
            except Exception:
                pass
    elif status in ("left", "kicked", "banned"):
        unregister_chat(chat.id)
        if OWNER_ID:
            try:
                await ctx.bot.send_message(
                    OWNER_ID,
                    f"❌ Бот удалён из *{chat.title}* (`{chat.id}`)",
                    parse_mode="Markdown"
                )
            except Exception:
                pass


# ══════════════════════════════════════════════════════════
# /start — главная панель (единственная команда)
# ══════════════════════════════════════════════════════════

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    unmark_blocked_user(uid)
    if not is_allowed(uid):
        await update.message.reply_text("⛔ У тебя нет доступа к этому боту.")
        return
    ctx.user_data.pop("keyboard_mode", None)
    await update.message.reply_text(
        "✅ Панель открыта. Теперь управляй ботом кнопками под строкой ввода.",
        reply_markup=get_keyboard(uid)
    )
    await _show_main_panel(update.message.reply_text, uid)


async def _show_main_panel(send_fn, uid: int):
    is_owner = (uid == OWNER_ID)
    wm_ok    = "✅" if os.path.exists(WATERMARK_PATH) else "❌"
    balance_line = "∞" if is_owner else credits_label(uid)
    text = (
        "🤖 *Панель управления*\n\n"
        f"🌐 Глобальная вотермарка: {wm_ok}\n"
        f"💳 Кредиты: *{balance_line}*\n\n"
        "Выбери раздел:"
    )
    kb = [
        [InlineKeyboardButton("🖼 Вотермарка & настройки", callback_data="panel_settings")],
        [InlineKeyboardButton("👁 Превью", callback_data="panel_preview")],
        [InlineKeyboardButton("🔗 Привязать канал", callback_data="panel_bind_channel")],
    ]
    if is_owner:
        kb += [
            [InlineKeyboardButton("📋 Чаты и каналы",  callback_data="panel_chats")],
            [InlineKeyboardButton("📢 Каналы", callback_data="panel_channels")],
            [InlineKeyboardButton("👥 Пользователи",   callback_data="panel_users")],
            [InlineKeyboardButton("📊 Активность",     callback_data="panel_activity")],
            [InlineKeyboardButton("📣 Рассылка", callback_data="panel_broadcast")],
        ]
    await send_fn(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")


# ══════════════════════════════════════════════════════════
# Callback handler
# ══════════════════════════════════════════════════════════

async def callback_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid      = query.from_user.id
    is_owner = (uid == OWNER_ID)

    if not is_allowed(uid):
        await query.answer("⛔ Нет доступа", show_alert=True)
        return

    data = query.data

    # ── Главная ───────────────────────────────────────────
    if data == "panel_main":
        await query.edit_message_text(
            "🤖 *Панель управления*\n\nВыбери раздел:",
            reply_markup=_main_keyboard(uid),
            parse_mode="Markdown"
        )
        return


    if data == "noop":
        return

    if data == "panel_bind_channel":
        ctx.user_data["waiting_channel_bind"] = True
        await query.edit_message_text(
            "🔗 *Привязка канала*\n\n"
            "1. Добавь бота админом в свой канал.\n"
            "2. Дай права: публиковать и удалять сообщения.\n"
            "3. Перешли сюда любой пост из этого канала.\n\n"
            "После этого бот будет ставить в канале твою личную вотермарку.",
            parse_mode="Markdown"
        )
        return

    # ── Настройки вотермарки ──────────────────────────────
    if data == "panel_settings":
        await _edit_settings_panel(query, uid)
        return

    if data in ("scale_up","scale_down","opacity_up","opacity_down","position_next"):
        s = load_settings()
        if data == "scale_up":      s["scale"]   = round(min(1.0, s["scale"]   + 0.05), 2)
        elif data == "scale_down":  s["scale"]   = round(max(0.05, s["scale"]  - 0.05), 2)
        elif data == "opacity_up":  s["opacity"] = round(min(1.0, s["opacity"] + 0.1),  1)
        elif data == "opacity_down":s["opacity"] = round(max(0.1, s["opacity"] - 0.1),  1)
        elif data == "position_next":
            idx = POSITIONS.index(s["position"]) if s["position"] in POSITIONS else 0
            s["position"] = POSITIONS[(idx + 1) % len(POSITIONS)]
        save_settings(s)
        await _edit_settings_panel(query, uid)
        return

    if data == "panel_setwm":
        ctx.user_data["waiting_watermark"] = True
        ctx.user_data.pop("waiting_chat_wm", None)
        ctx.user_data.pop("waiting_add_chat_user", None)
        ctx.user_data.pop("waiting_global_adduser", None)
        save_scope = "личная и глобальная" if uid == OWNER_ID else "личная"
        await query.edit_message_text(
            "📎 Отправь PNG/JPG вотермарку (с прозрачным фоном — лучший вариант).\n"
            f"Она заменит прошлую и будет сохранена как {save_scope}."
        )
        return

    if data == "panel_deletewm":
        deleted = delete_user_watermark(uid)
        if uid == OWNER_ID and os.path.exists(WATERMARK_PATH):
            # Глобальную watermark.png не удаляем, удаляется только личная копия владельца.
            pass
        if deleted:
            log_activity(uid, query.from_user.username or "", "delete_watermark", "личная вотермарка удалена")
            await query.answer("✅ Личная вотермарка удалена", show_alert=True)
        else:
            await query.answer("У тебя нет личной вотермарки", show_alert=True)
        await _edit_settings_panel(query, uid)
        return

    if data == "panel_preview":
        if uid != OWNER_ID:
            wm = get_watermark_for_user(uid)
            if not wm:
                await query.answer("❌ Сначала загрузи свою вотермарку", show_alert=True)
                return
            await _send_preview(query, wm, load_settings(), "Превью (твоя вотермарка)")
            return
        await _send_preview(query, WATERMARK_PATH, load_settings(), "Превью (глобальная)")
        return

    if data == "panel_preview_user":
        wm = get_watermark_for_user(uid)
        if not wm:
            await query.answer("❌ Сначала загрузи свою вотермарку", show_alert=True)
            return
        await _send_preview(query, wm, load_settings(), "Превью (твоя вотермарка)")
        return

    # ── Каналы ───────────────────────────────────────────
    if data == "panel_channels":
        if not is_owner:
            await query.answer("⛔ Только владелец", show_alert=True)
            return
        await _edit_channels_panel(query)
        return

    if data.startswith("channel_menu:"):
        if not is_owner:
            await query.answer("⛔ Только владелец", show_alert=True)
            return
        channel_id = data.split(":", 1)[1]
        await _edit_channel_menu(query, channel_id)
        return

    if data.startswith("channel_setwm:"):
        if not is_owner:
            await query.answer("⛔ Только владелец", show_alert=True)
            return
        channel_id = data.split(":", 1)[1]
        ctx.user_data["waiting_channel_wm"] = channel_id
        ctx.user_data.pop("waiting_watermark", None)
        ctx.user_data.pop("waiting_chat_wm", None)
        await query.edit_message_text(
            f"📎 Отправь PNG/JPG вотермарку для канала `{channel_id}`.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅ Назад", callback_data=f"channel_menu:{channel_id}")]])
        )
        return

    if data.startswith("channel_delwm:"):
        if not is_owner:
            await query.answer("⛔ Только владелец", show_alert=True)
            return
        channel_id = data.split(":", 1)[1]
        chats = load_chats()
        info = chats.get(channel_id)
        if info:
            old_path = info.get("watermark")
            info["watermark"] = None
            save_chats(chats)
            if old_path and os.path.exists(old_path):
                try:
                    os.remove(old_path)
                except Exception:
                    pass
        await query.answer("✅ Вотермарка канала удалена", show_alert=True)
        await _edit_channel_menu(query, channel_id)
        return

    if data.startswith("channel_preview:"):
        if not is_owner:
            await query.answer("⛔ Только владелец", show_alert=True)
            return
        channel_id = int(data.split(":", 1)[1])
        wm = get_channel_effective_watermark(channel_id)
        if not wm:
            await query.answer("❌ Для канала нет вотермарки", show_alert=True)
            return
        await _send_preview(query, wm, get_chat_settings(channel_id), f"Превью канала {channel_id}")
        return

    # ── Чаты ─────────────────────────────────────────────
    if data == "panel_chats":
        if not is_owner:
            await query.answer("⛔ Только владелец", show_alert=True)
            return
        await _edit_chats_list(query)
        return

    if data.startswith("chat_menu:"):
        chat_id = data.split(":",1)[1]
        await _edit_chat_menu(query, chat_id)
        return

    if data.startswith("chat_setwm:"):
        chat_id = data.split(":",1)[1]
        ctx.user_data["waiting_chat_wm"] = chat_id
        ctx.user_data.pop("waiting_watermark", None)
        await query.edit_message_text(
            f"📎 Отправь PNG вотермарку для чата `{chat_id}`.",
            parse_mode="Markdown"
        )
        return

    if data.startswith("chat_resetwm:"):
        chat_id = data.split(":",1)[1]
        chats = load_chats()
        if chat_id in chats:
            chats[chat_id]["watermark"] = None
            save_chats(chats)
        await query.answer("✅ Сброшено → глобальная вотермарка", show_alert=True)
        await _edit_chat_menu(query, chat_id)
        return

    if data.startswith("chat_preview:"):
        cid = int(data.split(":",1)[1])
        await _send_preview(query, get_chat_watermark_path(cid), get_chat_settings(cid),
                            f"Превью чата {cid}")
        return

    if data.startswith("cs_"):
        parts  = data.split(":",1)
        action = parts[0]
        chat_id= parts[1] if len(parts) > 1 else None
        if not chat_id:
            return
        chats = load_chats()
        if chat_id not in chats:
            return
        s = chats[chat_id].get("settings", DEFAULT_SETTINGS.copy())
        for k, v in DEFAULT_SETTINGS.items():
            s.setdefault(k, v)
        if action == "cs_scale_up":     s["scale"]   = round(min(1.0, s["scale"]   + 0.05), 2)
        elif action == "cs_scale_down": s["scale"]   = round(max(0.05, s["scale"]  - 0.05), 2)
        elif action == "cs_opacity_up": s["opacity"] = round(min(1.0, s["opacity"] + 0.1),  1)
        elif action=="cs_opacity_down": s["opacity"] = round(max(0.1, s["opacity"] - 0.1),  1)
        elif action == "cs_pos":
            idx = POSITIONS.index(s["position"]) if s["position"] in POSITIONS else 0
            s["position"] = POSITIONS[(idx + 1) % len(POSITIONS)]
        save_chat_settings(int(chat_id), s)
        await _edit_chat_menu(query, chat_id)
        return

    # ── Разрешения в чате ─────────────────────────────────
    if data.startswith("chat_users:"):
        chat_id = data.split(":",1)[1]
        await _edit_chat_users(query, chat_id)
        return

    if data.startswith("chat_adduser:"):
        chat_id = data.split(":",1)[1]
        ctx.user_data["waiting_add_chat_user"] = chat_id
        await query.edit_message_text(
            f"✍️ Введи Telegram ID пользователя для чата `{chat_id}`.\n"
            "Отправь число следующим сообщением.",
            parse_mode="Markdown"
        )
        return

    if data.startswith("chat_removeuser:"):
        _, chat_id, user_id = data.split(":",2)
        remove_chat_allowed(int(chat_id), int(user_id))
        await query.answer(f"✅ Пользователь {user_id} удалён из чата", show_alert=True)
        await _edit_chat_users(query, chat_id)
        return

    # ── Глобальные пользователи ───────────────────────────
    if data == "panel_users":
        if not is_owner:
            await query.answer("⛔ Только владелец", show_alert=True)
            return
        await _edit_users_panel(query)
        return

    if data == "users_add":
        ctx.user_data["waiting_global_adduser"] = True
        await query.edit_message_text(
            "✍️ Введи Telegram ID для глобального доступа.\n"
            "Отправь число следующим сообщением."
        )
        return

    if data.startswith("users_addcredits:"):
        credit_uid = int(data.split(":", 1)[1])
        ctx.user_data["waiting_credit_user"] = credit_uid
        await query.edit_message_text(
            f"💳 Введи количество кредитов для начисления пользователю `{credit_uid}`.\n"
            "Например: `10`. Для списания можно ввести `-5`.",
            parse_mode="Markdown"
        )
        return

    if data.startswith("users_remove:"):
        rem_id = int(data.split(":",1)[1])
        users  = load_allowed_users()
        users.discard(rem_id)
        save_allowed_users(users)
        await query.answer(f"✅ {rem_id} удалён", show_alert=True)
        await _edit_users_panel(query)
        return

    if data.startswith("users_view_wm:"):
        view_id = int(data.split(":",1)[1])
        wm = get_user_watermark_path(view_id)
        if wm:
            await query.message.reply_photo(
                photo=open(wm, "rb"),
                caption=f"🖼 Вотермарка пользователя `{view_id}`",
                parse_mode="Markdown"
            )
        else:
            await query.answer("У этого пользователя нет личной вотермарки", show_alert=True)
        return

    if data.startswith("users_view_activity:"):
        view_id = int(data.split(":",1)[1])
        log     = get_activity_log(50)
        entries = [e for e in log if e["user_id"] == view_id][-10:]
        if not entries:
            await query.answer("Нет активности", show_alert=True)
            return
        lines = [f"📊 *Активность `{view_id}`:*\n"]
        for e in reversed(entries):
            lines.append(f"• {e['ts']} — {e['action']}: {e['detail'][:40]}")
        await query.message.reply_text("\n".join(lines), parse_mode="Markdown")
        return

    # ── Лог активности ────────────────────────────────────
    if data == "panel_activity":
        if not is_owner:
            await query.answer("⛔ Только владелец", show_alert=True)
            return
        await _edit_activity_panel(query)
        return

    if data == "panel_broadcast":
        if not is_owner:
            await query.answer("⛔ Только владелец", show_alert=True)
            return
        ctx.user_data["waiting_broadcast_text"] = True
        await query.message.reply_text(
            "📣 Введи текст рассылки следующим сообщением.",
            reply_markup=ReplyKeyboardMarkup([[KeyboardButton(BTN_BROADCAST_CANCEL)], [KeyboardButton(BTN_BACK), KeyboardButton(BTN_MAIN)]], resize_keyboard=True)
        )
        return


# ══════════════════════════════════════════════════════════
# Строители inline-панелей
# ══════════════════════════════════════════════════════════

def _main_keyboard(uid: int) -> InlineKeyboardMarkup:
    is_owner = (uid == OWNER_ID)
    kb = [
        [InlineKeyboardButton("🖼 Вотермарка & настройки", callback_data="panel_settings")],
        [InlineKeyboardButton("👁 Превью", callback_data="panel_preview")],
        [InlineKeyboardButton("🔗 Привязать канал", callback_data="panel_bind_channel")],
    ]
    if is_owner:
        kb += [
            [InlineKeyboardButton("📋 Чаты и каналы",  callback_data="panel_chats")],
            [InlineKeyboardButton("📢 Каналы", callback_data="panel_channels")],
            [InlineKeyboardButton("👥 Пользователи",   callback_data="panel_users")],
            [InlineKeyboardButton("📊 Активность",     callback_data="panel_activity")],
            [InlineKeyboardButton("📣 Рассылка", callback_data="panel_broadcast")],
        ]
    return InlineKeyboardMarkup(kb)

async def _edit_settings_panel(query, uid: int):
    s       = load_settings()
    is_owner = uid == OWNER_ID
    wm_glob = "✅" if os.path.exists(WATERMARK_PATH) else "❌"
    uwm     = get_user_watermark_path(uid)
    wm_user = "✅ есть" if uwm else "❌ нет"

    if is_owner:
        text = (
            "⚙️ *Настройки вотермарки*\n\n"
            f"🌐 Глобальная: {wm_glob}\n"
            f"👤 Твоя личная: {wm_user}\n\n"
            f"📐 Размер: *{int(s['scale']*100)}%*\n"
            f"🔆 Прозрачность: *{int(s['opacity']*100)}%*\n"
            f"📍 Позиция: *{POSITION_LABELS.get(s['position'])}*"
        )
    else:
        text = (
            "⚙️ *Твоя вотермарка*\n\n"
            f"👤 Личная: {wm_user}\n\n"
            "⚠️ Для добавленных пользователей глобальная вотермарка отключена.\n"
            "Фото/видео будут обрабатываться только с твоей личной вотермаркой.\n\n"
            f"📐 Размер: *{int(s['scale']*100)}%*\n"
            f"🔆 Прозрачность: *{int(s['opacity']*100)}%*\n"
            f"📍 Позиция: *{POSITION_LABELS.get(s['position'])}*"
        )

    kb = [
        [InlineKeyboardButton("📤 Загрузить / заменить вотермарку", callback_data="panel_setwm")],
    ]
    if uwm:
        kb.append([InlineKeyboardButton("🗑 Удалить мою вотермарку", callback_data="panel_deletewm")])
    kb += [
        [
            InlineKeyboardButton("📐 −", callback_data="scale_down"),
            InlineKeyboardButton(f"{int(s['scale']*100)}%", callback_data="noop"),
            InlineKeyboardButton("📐 +", callback_data="scale_up"),
        ],
        [
            InlineKeyboardButton("🔆 −", callback_data="opacity_down"),
            InlineKeyboardButton(f"{int(s['opacity']*100)}%", callback_data="noop"),
            InlineKeyboardButton("🔆 +", callback_data="opacity_up"),
        ],
        [InlineKeyboardButton(
            f"📍 {POSITION_LABELS.get(s['position'])} →", callback_data="position_next"
        )],
    ]
    if is_owner:
        kb.append([
            InlineKeyboardButton("👁 Превью (глобальная)",  callback_data="panel_preview"),
            InlineKeyboardButton("👁 Превью (моя)", callback_data="panel_preview_user"),
        ])
    else:
        kb.append([InlineKeyboardButton("👁 Превью моей", callback_data="panel_preview_user")])
    kb.append([InlineKeyboardButton("« Главная", callback_data="panel_main")])
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")

async def _edit_chats_list(query):
    chats  = load_chats()
    active = {cid: info for cid, info in chats.items() if info.get("active")}
    text   = "📋 *Чаты и каналы с ботом:*\n\n"
    kb     = []
    if active:
        for cid, info in active.items():
            emoji = CHAT_TYPE_EMOJI.get(info.get("type",""), "💬")
            wm    = "✅" if (info.get("watermark") and os.path.exists(info["watermark"])) else "🌐"
            ucnt  = len(info.get("allowed_users", []))
            text += f"{emoji} *{info['title']}*  вотермарка:{wm}  юзеров:{ucnt}\n"
            kb.append([InlineKeyboardButton(
                f"{emoji} {info['title'][:28]}", callback_data=f"chat_menu:{cid}"
            )])
    else:
        text += "Бот ещё не добавлен ни в один чат."
    kb.append([InlineKeyboardButton("« Главная", callback_data="panel_main")])
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")

async def _edit_channels_panel(query):
    chats = load_chats()
    active = {cid: info for cid, info in chats.items() if info.get("active") and info.get("type") == "channel"}
    text = "📢 *Каналы*\n\n"
    kb = []
    if active:
        for cid, info in active.items():
            title = info.get("title", cid)
            wm = "✅ своя" if (info.get("watermark") and os.path.exists(info["watermark"])) else "👤 пользователя/нет"
            owner = info.get("owner_user_id") or "не привязан"
            text += f"📢 *{title}*\n🆔 `{cid}` · WM: {wm} · владелец: `{owner}`\n\n"
            kb.append([InlineKeyboardButton(f"📢 {title[:28]}", callback_data=f"channel_menu:{cid}")])
    else:
        text += "Каналов пока нет. Добавь бота админом в канал или привяжи канал через кнопку 🔗."
    kb.append([InlineKeyboardButton("⬅ Назад", callback_data="panel_main")])
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")


async def _edit_channel_menu(query, channel_id: str):
    chats = load_chats()
    info = chats.get(str(channel_id), {})
    if not info:
        await query.edit_message_text(
            "❌ Канал не найден.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅ Назад", callback_data="panel_channels")]])
        )
        return
    title = info.get("title", channel_id)
    owner = info.get("owner_user_id") or "не привязан"
    custom_wm = info.get("watermark") and os.path.exists(info["watermark"])
    effective = get_channel_effective_watermark(int(channel_id))
    if custom_wm:
        wm_status = "✅ отдельная вотермарка канала"
    elif effective:
        wm_status = "👤 личная вотермарка владельца канала"
    else:
        wm_status = "❌ нет вотермарки"
    text = (
        f"📢 *{title}*\n"
        f"🆔 `{channel_id}`\n"
        f"👤 Владелец/клиент: `{owner}`\n\n"
        f"🖼 Вотермарка: {wm_status}\n\n"
        "Если загрузить вотермарку здесь — она будет применяться только в этом канале."
    )
    kb = [
        [InlineKeyboardButton("📤 Загрузить/заменить вотермарку", callback_data=f"channel_setwm:{channel_id}")],
        [InlineKeyboardButton("🗑 Удалить вотермарку канала", callback_data=f"channel_delwm:{channel_id}")],
        [InlineKeyboardButton("👁 Превью", callback_data=f"channel_preview:{channel_id}")],
        [InlineKeyboardButton("⬅ Назад к каналам", callback_data="panel_channels")],
    ]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")


async def _edit_chat_menu(query, chat_id: str):
    chats = load_chats()
    info  = chats.get(chat_id, {})
    if not info:
        await query.edit_message_text("❌ Чат не найден.")
        return
    emoji = CHAT_TYPE_EMOJI.get(info.get("type",""), "💬")
    s     = info.get("settings", DEFAULT_SETTINGS.copy())
    wm_ok = "✅ Своя" if (info.get("watermark") and os.path.exists(info["watermark"])) else "🌐 Глобальная"
    ucnt  = len(info.get("allowed_users", []))
    pos   = s.get("position","bottom-right")
    text  = (
        f"{emoji} *{info['title']}*\n🆔 `{chat_id}`\n\n"
        f"🖼 Вотермарка: {wm_ok}\n"
        f"📐 Размер: *{int(s.get('scale',0.3)*100)}%*\n"
        f"🔆 Прозрачность: *{int(s.get('opacity',0.5)*100)}%*\n"
        f"📍 Позиция: *{POSITION_LABELS.get(pos)}*\n"
        f"👥 Разрешённых в чате: *{ucnt}*"
    )
    kb = [
        [InlineKeyboardButton("📤 Загрузить вотермарку",   callback_data=f"chat_setwm:{chat_id}")],
        [InlineKeyboardButton("🗑 Сбросить → глобальная", callback_data=f"chat_resetwm:{chat_id}")],
        [
            InlineKeyboardButton("📐 −", callback_data=f"cs_scale_down:{chat_id}"),
            InlineKeyboardButton(f"{int(s.get('scale',0.3)*100)}%", callback_data="noop"),
            InlineKeyboardButton("📐 +", callback_data=f"cs_scale_up:{chat_id}"),
        ],
        [
            InlineKeyboardButton("🔆 −", callback_data=f"cs_opacity_down:{chat_id}"),
            InlineKeyboardButton(f"{int(s.get('opacity',0.5)*100)}%", callback_data="noop"),
            InlineKeyboardButton("🔆 +", callback_data=f"cs_opacity_up:{chat_id}"),
        ],
        [InlineKeyboardButton(f"📍 {POSITION_LABELS.get(pos)} →", callback_data=f"cs_pos:{chat_id}")],
        [InlineKeyboardButton("👁 Превью", callback_data=f"chat_preview:{chat_id}")],
        [InlineKeyboardButton(f"👥 Разрешения в чате ({ucnt})", callback_data=f"chat_users:{chat_id}")],
        [InlineKeyboardButton("« К списку чатов", callback_data="panel_chats")],
    ]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")

async def _edit_chat_users(query, chat_id: str):
    chats   = load_chats()
    info    = chats.get(chat_id, {})
    title   = info.get("title", chat_id)
    allowed = info.get("allowed_users", [])
    text    = f"👥 *Разрешения в {title}*\n🆔 `{chat_id}`\n\n"
    kb      = []
    if allowed:
        for u in allowed:
            has_wm = "🖼" if get_user_watermark_path(u) else "  "
            text  += f"• `{u}` {has_wm}\n"
            kb.append([
                InlineKeyboardButton(f"🖼 WM",          callback_data=f"users_view_wm:{u}"),
                InlineKeyboardButton(f"📊 Активность",  callback_data=f"users_view_activity:{u}"),
                InlineKeyboardButton(f"❌ {u}",         callback_data=f"chat_removeuser:{chat_id}:{u}"),
            ])
    else:
        text += "_Список пуст — работают только глобально разрешённые._"
    kb.append([InlineKeyboardButton("➕ Добавить пользователя", callback_data=f"chat_adduser:{chat_id}")])
    kb.append([InlineKeyboardButton("« Назад",                  callback_data=f"chat_menu:{chat_id}")])
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")

async def _edit_users_panel(query):
    users = load_allowed_users()
    text  = f"👥 *Глобальные пользователи*\n\n👑 Владелец: `{OWNER_ID}`\n\n"
    kb    = []
    if users:
        text += "Дополнительные:\n"
        for u in sorted(users):
            has_wm = "🖼" if get_user_watermark_path(u) else "  "
            text  += f"• `{u}` {has_wm} · 💳 *{credits_label(u)}*\n"
            kb.append([
                InlineKeyboardButton(f"💳 +/−",        callback_data=f"users_addcredits:{u}"),
                InlineKeyboardButton(f"🖼 WM",         callback_data=f"users_view_wm:{u}"),
                InlineKeyboardButton(f"📊 Действия",   callback_data=f"users_view_activity:{u}"),
                InlineKeyboardButton(f"❌ {u}",        callback_data=f"users_remove:{u}"),
            ])
    else:
        text += "_Дополнительных нет._"
    kb.append([InlineKeyboardButton("➕ Добавить пользователя", callback_data="users_add")])
    kb.append([InlineKeyboardButton("« Главная", callback_data="panel_main")])
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")

async def _edit_activity_panel(query):
    log = get_activity_log(15)
    if not log:
        await query.edit_message_text(
            "📊 *Активность*\n\nЛог пуст.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("« Главная", callback_data="panel_main")]]),
            parse_mode="Markdown"
        )
        return
    text = "📊 *Последняя активность:*\n\n"
    for e in reversed(log):
        text += f"`{e['ts']}` @{e['username']} `{e['user_id']}`\n  ➜ {e['action']}: {e['detail'][:50]}\n\n"
    kb = [[InlineKeyboardButton("« Главная", callback_data="panel_main")]]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")

async def _send_preview(query, wm_path: str, settings: dict, caption: str):
    if not os.path.exists(wm_path):
        await query.answer("❌ Вотермарка не найдена", show_alert=True)
        return
    try:
        img = Image.new("RGB", (800, 600), color=(160, 160, 200))
        buf = io.BytesIO()
        img.save(buf, format="JPEG")
        result = _build_watermarked_image(buf.getvalue(), wm_path, settings)
        s = settings
        await query.message.reply_photo(
            photo=io.BytesIO(result),
            caption=(
                f"{caption}\n"
                f"Размер: {int(s['scale']*100)}% | "
                f"Прозрачность: {int(s['opacity']*100)}% | "
                f"{POSITION_LABELS.get(s['position'])}"
            )
        )
    except Exception as e:
        await query.message.reply_text(f"❌ Ошибка превью: {e}")



async def show_keyboard_main(msg, uid: int, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.pop("keyboard_mode", None)
    role = "админ" if uid == OWNER_ID else "пользователь"
    await msg.reply_text(
        f"🏠 Главное меню ({role}).\n"
        f"💳 Кредиты: {credits_label(uid)}\n\n"
        "Кнопки снизу — основная панель. Inline-кнопки в сообщениях оставлены для точечных настроек.",
        reply_markup=get_keyboard(uid)
    )


async def show_keyboard_help(msg, uid: int):
    if uid == OWNER_ID:
        text = (
            "❓ *Помощь по админ-панели*\n\n"
            "🖼 Вотермарка — загрузка глобальной/личной вотермарки и настройки.\n"
            "📋 Чаты — список чатов/каналов, выбор чата и его настройки.\n"
            "👥 Пользователи — глобальный доступ и кредиты.\n"
            "💳 1 обработанное фото/видео = 1 кредит. При 0 кредитов обработка блокируется.\n"
            "📊 Активность — последние действия.\n\n"
            "Для загрузки вотермарки нажми 🖼 Вотермарка → 📤 Загрузить вотермарку, потом отправь картинку."
        )
    else:
        text = (
            "❓ *Помощь*\n\n"
            "🖼 Вотермарка — загрузи свою личную вотермарку.\n"
            "👁 Превью — проверь, как она выглядит.\n"
            f"Фото/видео отправляй в личку — бот вернёт файл с вотермаркой.\n\n💳 Твои кредиты: *{credits_label(uid)}*"
        )
    await msg.reply_text(text, parse_mode="Markdown", reply_markup=get_keyboard(uid))


async def show_keyboard_chats(msg, ctx: ContextTypes.DEFAULT_TYPE):
    chats = load_chats()
    active = {cid: info for cid, info in chats.items() if info.get("active")}
    ctx.user_data["keyboard_mode"] = "select_chat"
    if not active:
        await msg.reply_text("📋 Бот ещё не добавлен ни в один чат.", reply_markup=admin_keyboard())
        return
    lines = ["📋 *Выбери чат кнопкой снизу:*\n"]
    for cid, info in active.items():
        emoji = CHAT_TYPE_EMOJI.get(info.get("type", ""), "💬")
        wm = "✅ своя" if (info.get("watermark") and os.path.exists(info["watermark"])) else "🌐 глобальная"
        users = len(info.get("allowed_users", []))
        lines.append(f"{emoji} *{info.get('title', cid)}*\n`{cid}` · {wm} · юзеров: {users}")
    await msg.reply_text("\n\n".join(lines), parse_mode="Markdown", reply_markup=chats_keyboard())


async def show_keyboard_users(msg):
    users = load_allowed_users()
    lines = [f"👥 *Глобальные пользователи*\n\n👑 Владелец: `{OWNER_ID}`"]
    if users:
        lines.append("\nДополнительные:")
        for u in sorted(users):
            mark = "🖼" if get_user_watermark_path(u) else ""
            lines.append(f"• `{u}` {mark} · 💳 *{credits_label(u)}*")
    else:
        lines.append("\nДополнительных пользователей нет.")
    lines.append("\nЧтобы добавить — нажми кнопку ➕ Добавить пользователя.")
    kb = ReplyKeyboardMarkup(
        [[KeyboardButton(BTN_ADD_USER)], [KeyboardButton(BTN_BACK), KeyboardButton(BTN_MAIN)]],
        resize_keyboard=True,
        one_time_keyboard=False
    )
    await msg.reply_text("\n".join(lines), parse_mode="Markdown", reply_markup=kb)


async def show_keyboard_activity(msg):
    log = get_activity_log(15)
    if not log:
        await msg.reply_text("📊 Активность пока пустая.", reply_markup=admin_keyboard())
        return
    lines = ["📊 *Последняя активность:*\n"]
    for e in reversed(log):
        lines.append(f"`{e['ts']}` @{e['username']} `{e['user_id']}`\n➜ {e['action']}: {e['detail'][:50]}")
    await msg.reply_text("\n\n".join(lines), parse_mode="Markdown", reply_markup=admin_keyboard())


async def handle_keyboard_button(update: Update, ctx: ContextTypes.DEFAULT_TYPE, text: str) -> bool:
    msg = update.message
    uid = update.effective_user.id
    is_owner = uid == OWNER_ID

    if text in (BTN_MAIN, BTN_BACK):
        await show_keyboard_main(msg, uid, ctx)
        return True


    if text == BTN_HELP:
        await show_keyboard_help(msg, uid)
        return True

    if text == BTN_BUY_CREDITS:
        await send_purchase_request(ctx, uid, update.effective_user.username)
        await msg.reply_text(
            "✅ Заявка отправлена админу. Он начислит кредиты вручную.\n"
            f"Твой текущий баланс: {credits_label(uid)}",
            reply_markup=get_keyboard(uid)
        )
        return True

    if text == BTN_ADMIN_STATS:
        if not is_owner:
            await msg.reply_text("⛔ Только владелец.", reply_markup=get_keyboard(uid))
            return True
        await msg.reply_text(build_admin_stats_text(), parse_mode="Markdown", reply_markup=get_keyboard(uid))
        return True

    if text == BTN_BLOCKED_USERS:
        if not is_owner:
            await msg.reply_text("⛔ Только владелец.", reply_markup=get_keyboard(uid))
            return True
        await msg.reply_text(blocked_users_text(), parse_mode="Markdown", reply_markup=get_keyboard(uid))
        return True

    if text == BTN_BROADCAST:
        if not is_owner:
            await msg.reply_text("⛔ Только владелец.", reply_markup=get_keyboard(uid))
            return True
        ctx.user_data["waiting_broadcast_text"] = True
        ctx.user_data.pop("keyboard_mode", None)
        await msg.reply_text(
            "📣 Введи текст рассылки.\n\n"
            "Поддерживается обычный текст и HTML-теги Telegram: <b>жирный</b>, <i>курсив</i>, <code>код</code>.\n"
            "Чтобы отменить — нажми «❌ Отменить рассылку».",
            reply_markup=ReplyKeyboardMarkup([[KeyboardButton(BTN_BROADCAST_CANCEL)], [KeyboardButton(BTN_BACK), KeyboardButton(BTN_MAIN)]], resize_keyboard=True)
        )
        return True

    if text == BTN_WM:
        ctx.user_data.pop("keyboard_mode", None)
        await _show_main_panel(msg.reply_text, uid)
        await msg.reply_text("Открыл раздел вотермарки. Используй кнопки в сообщении выше.", reply_markup=get_keyboard(uid))
        return True

    if text == BTN_BIND_CHANNEL:
        ctx.user_data["waiting_channel_bind"] = True
        await msg.reply_text(
            "🔗 Привязка канала\n\n"
            "1. Добавь бота админом в свой канал.\n"
            "2. Дай права: публиковать и удалять сообщения.\n"
            "3. Перешли сюда любой пост из этого канала.\n\n"
            "После этого бот будет ставить в канале твою личную вотермарку.",
            reply_markup=get_keyboard(uid)
        )
        return True

    if text == BTN_PREVIEW:
        wm = get_watermark_for_user(uid)
        if not wm or not os.path.exists(wm):
            await msg.reply_text("❌ Сначала загрузи свою вотермарку.", reply_markup=get_keyboard(uid))
            return True
        try:
            img = Image.new("RGB", (800, 600), color=(160, 160, 200))
            buf = io.BytesIO()
            img.save(buf, format="JPEG")
            result = _build_watermarked_image(buf.getvalue(), wm, load_settings())
            await msg.reply_photo(io.BytesIO(result), caption="👁 Превью", reply_markup=get_keyboard(uid))
        except Exception as e:
            await msg.reply_text(f"❌ Ошибка превью: {e}", reply_markup=get_keyboard(uid))
        return True

    if text == BTN_CHATS:
        if not is_owner:
            await msg.reply_text("⛔ Только владелец.", reply_markup=get_keyboard(uid))
            return True
        await show_keyboard_chats(msg, ctx)
        return True

    if text == BTN_CHANNELS:
        if not is_owner:
            await msg.reply_text("⛔ Только владелец.", reply_markup=get_keyboard(uid))
            return True
        ctx.user_data["keyboard_mode"] = "select_channel"
        channels = load_chats()
        count = sum(1 for i in channels.values() if i.get("active") and i.get("type") == "channel")
        await msg.reply_text(
            f"📢 Выбери канал кнопкой ниже. Найдено каналов: {count}",
            reply_markup=channels_keyboard()
        )
        return True

    if text == BTN_USERS:
        if not is_owner:
            await msg.reply_text("⛔ Только владелец.", reply_markup=get_keyboard(uid))
            return True
        ctx.user_data.pop("keyboard_mode", None)
        await show_keyboard_users(msg)
        return True

    if text == BTN_ADD_USER:
        if not is_owner:
            await msg.reply_text("⛔ Только владелец.", reply_markup=get_keyboard(uid))
            return True
        ctx.user_data["waiting_global_adduser"] = True
        ctx.user_data.pop("keyboard_mode", None)
        await msg.reply_text("✍️ Введи Telegram ID пользователя числом.")
        return True

    if text == BTN_ADD_CREDITS:
        if not is_owner:
            await msg.reply_text("⛔ Только владелец.", reply_markup=get_keyboard(uid))
            return True
        ctx.user_data["waiting_credit_input"] = True
        ctx.user_data.pop("keyboard_mode", None)
        await msg.reply_text(
            "💳 Введи ID и количество кредитов через пробел.\n"
            "Пример: `123456789 25`\n"
            "Для списания: `123456789 -5`",
            parse_mode="Markdown"
        )
        return True

    if text == BTN_ACTIVITY:
        if not is_owner:
            await msg.reply_text("⛔ Только владелец.", reply_markup=get_keyboard(uid))
            return True
        ctx.user_data.pop("keyboard_mode", None)
        await show_keyboard_activity(msg)
        return True

    if is_owner and ctx.user_data.get("keyboard_mode") == "select_channel" and " | " in text:
        channel_id = text.rsplit(" | ", 1)[-1].strip()
        info = get_chat_info(int(channel_id)) if channel_id.lstrip("-").isdigit() else None
        if channel_id.lstrip("-").isdigit() and info and info.get("type") == "channel":
            ctx.user_data.pop("keyboard_mode", None)
            await msg.reply_text("Открыл меню выбранного канала ниже.", reply_markup=admin_keyboard())
            class FakeQuery:
                def __init__(self, message):
                    self.message = message
                async def edit_message_text(self, *args, **kwargs):
                    return await self.message.reply_text(*args, **kwargs)
            await _edit_channel_menu(FakeQuery(msg), channel_id)
            return True

    if is_owner and ctx.user_data.get("keyboard_mode") == "select_chat" and " | " in text:
        chat_id = text.rsplit(" | ", 1)[-1].strip()
        if chat_id.lstrip("-").isdigit() and get_chat_info(int(chat_id)):
            ctx.user_data.pop("keyboard_mode", None)
            await msg.reply_text("Открыл меню выбранного чата ниже.", reply_markup=admin_keyboard())
            class FakeQuery:
                def __init__(self, message):
                    self.message = message
                async def edit_message_text(self, *args, **kwargs):
                    return await self.message.reply_text(*args, **kwargs)
            await _edit_chat_menu(FakeQuery(msg), chat_id)
            return True

    return False



def get_forwarded_channel_from_message(msg):
    """Возвращает (chat_id, title) канала из пересланного поста, если Telegram отдал источник."""
    # Новые версии python-telegram-bot / Bot API
    origin = getattr(msg, "forward_origin", None)
    chat = getattr(origin, "chat", None) if origin else None
    if chat and getattr(chat, "type", None) == "channel":
        return chat.id, (chat.title or str(chat.id))

    # Старые версии Bot API
    fchat = getattr(msg, "forward_from_chat", None)
    if fchat and getattr(fchat, "type", None) == "channel":
        return fchat.id, (fchat.title or str(fchat.id))

    return None, None


async def bind_channel_from_forward(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> bool:
    msg = update.message
    uid = update.effective_user.id
    if not msg or not is_allowed(uid):
        return False

    if not ctx.user_data.get("waiting_channel_bind"):
        return False

    channel_id, title = get_forwarded_channel_from_message(msg)
    if not channel_id:
        await msg.reply_text(
            "❌ Не вижу канал в пересланном сообщении.\n\n"
            "Перешли именно пост из канала, куда добавлен бот. "
            "Если Telegram скрыл источник пересылки — пришли числовой ID канала, например `-1001234567890`.",
            parse_mode="Markdown",
            reply_markup=get_keyboard(uid)
        )
        return True

    register_chat(channel_id, title, "channel", uid)
    ctx.user_data.pop("waiting_channel_bind", None)
    await msg.reply_text(
        f"✅ Канал *{title}* привязан к тебе.\n"
        "Теперь посты в этом канале будут обрабатываться твоей личной вотермаркой.",
        parse_mode="Markdown",
        reply_markup=get_keyboard(uid)
    )
    return True

# ══════════════════════════════════════════════════════════
# Обработка текстовых сообщений (ввод ID)
# ══════════════════════════════════════════════════════════

async def handle_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        return
    uid  = update.effective_user.id
    text = (update.message.text or "").strip()

    # Админская рассылка
    if uid == OWNER_ID and ctx.user_data.get("waiting_broadcast_text"):
        if text in (BTN_BROADCAST_CANCEL, BTN_BACK, BTN_MAIN):
            ctx.user_data.pop("waiting_broadcast_text", None)
            await update.message.reply_text("❌ Рассылка отменена.", reply_markup=admin_keyboard())
            return
        if not text:
            await update.message.reply_text("❌ Текст пустой. Введи сообщение для рассылки.")
            return
        ctx.user_data["waiting_broadcast_text"] = False
        ctx.user_data["waiting_broadcast_confirm"] = True
        ctx.user_data["broadcast_text"] = text
        users_count = len([u for u in load_allowed_users() if u != OWNER_ID])
        kb = ReplyKeyboardMarkup(
            [[KeyboardButton(BTN_BROADCAST_SEND)], [KeyboardButton(BTN_BROADCAST_CANCEL)]],
            resize_keyboard=True,
            one_time_keyboard=False
        )
        await update.message.reply_text(
            f"📣 Подтверди рассылку для {users_count} пользователей:\n\n{text[:1000]}",
            reply_markup=kb
        )
        return

    if uid == OWNER_ID and ctx.user_data.get("waiting_broadcast_confirm"):
        if text == BTN_BROADCAST_SEND:
            broadcast_text = ctx.user_data.pop("broadcast_text", "")
            ctx.user_data.pop("waiting_broadcast_confirm", None)
            status = await update.message.reply_text("⏳ Отправляю рассылку...", reply_markup=admin_keyboard())
            result = await send_broadcast_to_users(ctx, broadcast_text)
            result_text = (
                "✅ Рассылка завершена.\n\n"
                f"👥 Всего: {result['total']}\n"
                f"📨 Отправлено: {result['sent']}\n"
                f"🚫 Отписались/заблокировали: {result['blocked']}\n"
                f"⚠️ Ошибок: {result['failed']}"
            )
            try:
                await status.edit_text(result_text)
            except Exception:
                await update.message.reply_text(result_text, reply_markup=admin_keyboard())
            return
        ctx.user_data.pop("broadcast_text", None)
        ctx.user_data.pop("waiting_broadcast_confirm", None)
        await update.message.reply_text("❌ Рассылка отменена.", reply_markup=admin_keyboard())
        return

    # Привязка канала по пересланному посту или по числовому ID.
    if ctx.user_data.get("waiting_channel_bind"):
        if await bind_channel_from_forward(update, ctx):
            return
        if text.lstrip("-").isdigit():
            channel_id = int(text)
            try:
                chat = await ctx.bot.get_chat(channel_id)
                title = chat.title or str(channel_id)
            except Exception:
                title = str(channel_id)
            register_chat(channel_id, title, "channel", uid)
            ctx.user_data.pop("waiting_channel_bind", None)
            await update.message.reply_text(
                f"✅ Канал `{channel_id}` привязан к тебе.\n"
                "Теперь посты в этом канале будут обрабатываться твоей личной вотермаркой.",
                parse_mode="Markdown",
                reply_markup=get_keyboard(uid)
            )
            return
        await update.message.reply_text(
            "❌ Перешли пост из канала или введи числовой ID канала вида `-100...`.",
            parse_mode="Markdown",
            reply_markup=get_keyboard(uid)
        )
        return

    # Сначала обрабатываем состояния, где бот ждёт число/ID.
    # Кнопки меню обрабатываются ниже, чтобы не ломать ввод ID.
    if ctx.user_data.get("waiting_credit_user") and uid == OWNER_ID:
        credit_uid = int(ctx.user_data.pop("waiting_credit_user"))
        try:
            amount = int(text)
            balance = add_user_credits(credit_uid, amount)
            await update.message.reply_text(
                f"✅ Пользователю `{credit_uid}` начислено: `{amount}`.\n"
                f"Текущий баланс: *{balance}* кредитов.",
                parse_mode="Markdown",
                reply_markup=admin_keyboard()
            )
        except ValueError:
            await update.message.reply_text("❌ Введи число, например `10` или `-5`.", parse_mode="Markdown")
        return

    if ctx.user_data.get("waiting_credit_input") and uid == OWNER_ID:
        ctx.user_data.pop("waiting_credit_input")
        try:
            parts = text.split()
            if len(parts) != 2:
                raise ValueError
            credit_uid = int(parts[0])
            amount = int(parts[1])
            balance = add_user_credits(credit_uid, amount)
            await update.message.reply_text(
                f"✅ Пользователю `{credit_uid}` начислено: `{amount}`.\n"
                f"Текущий баланс: *{balance}* кредитов.",
                parse_mode="Markdown",
                reply_markup=admin_keyboard()
            )
        except ValueError:
            await update.message.reply_text(
                "❌ Формат: `ID количество`, например `123456789 25`.",
                parse_mode="Markdown",
                reply_markup=admin_keyboard()
            )
        return

    if ctx.user_data.get("waiting_add_chat_user"):
        chat_id = ctx.user_data.pop("waiting_add_chat_user")
        try:
            new_uid = int(text)
            add_chat_allowed(int(chat_id), new_uid)
            chats = load_chats()
            title = chats.get(chat_id, {}).get("title", chat_id)
            await update.message.reply_text(
                f"✅ Пользователь `{new_uid}` добавлен в *{title}*",
                parse_mode="Markdown"
            )
        except ValueError:
            await update.message.reply_text("❌ Введи числовой ID.")
        return

    if ctx.user_data.get("waiting_global_adduser") and uid == OWNER_ID:
        ctx.user_data.pop("waiting_global_adduser")
        try:
            new_uid = int(text)
            users   = load_allowed_users()
            users.add(new_uid)
            save_allowed_users(users)
            set_user_credits(new_uid, get_user_credits(new_uid))
            await update.message.reply_text(
                f"✅ Пользователь `{new_uid}` добавлен глобально.\n"
                f"💳 Баланс: *{credits_label(new_uid)}* кредитов. Начисли кредиты через меню пользователей.",
                parse_mode="Markdown",
                reply_markup=admin_keyboard()
            )
        except ValueError:
            await update.message.reply_text("❌ Введи числовой ID.")
        return

    if await handle_keyboard_button(update, ctx, text):
        return


# ══════════════════════════════════════════════════════════
# Приём файлов (фото/видео/документы) в личке
# ══════════════════════════════════════════════════════════

async def _route_private_media_impl(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_allowed(uid):
        await update.message.reply_text("⛔ У тебя нет доступа.")
        return

    msg    = update.message
    is_doc = bool(msg.document and msg.document.mime_type)

    if ctx.user_data.get("waiting_channel_bind"):
        if await bind_channel_from_forward(update, ctx):
            return


    # ── Ожидаем вотермарку для канала ───────────────────
    if ctx.user_data.get("waiting_channel_wm"):
        if not (msg.photo or (is_doc and msg.document.mime_type.startswith("image"))):
            await msg.reply_text("❌ Нужен PNG/JPG файл.")
            return
        await _do_save_channel_wm(update, ctx)
        return

    # ── Ожидаем вотермарку для чата ──────────────────────
    if ctx.user_data.get("waiting_chat_wm"):
        if not (msg.photo or (is_doc and msg.document.mime_type.startswith("image"))):
            await msg.reply_text("❌ Нужен PNG/JPG файл.")
            return
        await _do_save_chat_wm(update, ctx)
        return

    # ── Ожидаем личную/глобальную вотермарку ─────────────
    if ctx.user_data.get("waiting_watermark"):
        if not (msg.photo or (is_doc and msg.document.mime_type.startswith("image"))):
            await msg.reply_text("❌ Нужен PNG/JPG файл.")
            return
        await _do_save_user_wm(update, ctx)
        return

    # ── Обработка фото ────────────────────────────────────
    if msg.photo or (is_doc and msg.document.mime_type.startswith("image")):
        wm = get_watermark_for_user(uid)
        if not wm:
            await msg.reply_text("❌ Сначала загрузи свою вотермарку через /start → Вотермарка & настройки")
            return
        if not has_credits(uid, CREDIT_COST_PHOTO):
            await notify_no_credits_message(msg, uid)
            return
        try:
            file = await ctx.bot.get_file(
                msg.document.file_id if (is_doc and msg.document.mime_type.startswith("image"))
                else msg.photo[-1].file_id
            )
            raw    = bytes(await file.download_as_bytearray())
            result = _build_watermarked_image(raw, wm, load_settings())
            new_balance = deduct_credits(uid, CREDIT_COST_PHOTO)
            caption_done = "✅ Готово!" if new_balance is None else f"✅ Готово! Осталось кредитов: {new_balance}"
            await msg.reply_photo(io.BytesIO(result), caption=caption_done)
            log_activity(uid, update.effective_user.username or "", "photo_processed", f"личка · −{CREDIT_COST_PHOTO} кредит")
            await _notify_owner_upload(ctx, uid, update.effective_user.username, "фото", raw)
        except Exception as e:
            logger.error(f"Ошибка фото личка: {e}", exc_info=True)
            await msg.reply_text(f"❌ Ошибка: {e}")
        return

    # ── Обработка видео ───────────────────────────────────
    if msg.video or (is_doc and msg.document.mime_type and msg.document.mime_type.startswith("video")):
        wm = get_watermark_for_user(uid)
        if not wm or not os.path.exists(wm):
            await msg.reply_text("❌ Сначала загрузи свою вотермарку через /start → Вотермарка & настройки")
            return
        if not has_credits(uid, CREDIT_COST_VIDEO):
            await notify_no_credits_message(msg, uid)
            return
        status = await msg.reply_text("⏳ Обрабатываю видео...")
        try:
            file = await ctx.bot.get_file(
                msg.document.file_id if (is_doc and msg.document.mime_type.startswith("video"))
                else msg.video.file_id
            )
            raw    = bytes(await file.download_as_bytearray())
            result = await apply_watermark_video(raw, wm, load_settings())
            new_balance = deduct_credits(uid, CREDIT_COST_VIDEO)
            caption_done = "✅ Готово!" if new_balance is None else f"✅ Готово! Осталось кредитов: {new_balance}"
            await status.delete()
            await msg.reply_video(io.BytesIO(result), caption=caption_done)
            log_activity(uid, update.effective_user.username or "", "video_processed", f"личка · −{CREDIT_COST_VIDEO} кредит")
            await _notify_owner_text(ctx, uid, update.effective_user.username, "видео", "личка")
        except Exception as e:
            logger.error(f"Ошибка видео личка: {e}", exc_info=True)
            await status.edit_text(f"❌ Ошибка: {e}")
        return


async def _do_save_user_wm(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    msg = update.message
    is_doc = bool(msg.document and msg.document.mime_type)
    try:
        file = await ctx.bot.get_file(
            msg.document.file_id if is_doc else msg.photo[-1].file_id
        )
        raw = bytes(await file.download_as_bytearray())
        img = Image.open(io.BytesIO(raw))

        # ВАЖНО: личная вотермарка пользователя не должна перезаписывать
        # основную глобальную вотермарку владельца.
        # Глобальную watermark.png может обновлять только OWNER_ID.
        if uid == OWNER_ID:
            img.save(WATERMARK_PATH, format="PNG")

        # У каждого пользователя своя отдельная вотермарка:
        # user_watermarks/<telegram_id>.png
        save_user_watermark(uid, raw)

        ctx.user_data.pop("waiting_watermark", None)
        log_activity(uid, update.effective_user.username or "", "set_watermark",
                     f"{img.width}x{img.height}")
        # Уведомление владельцу с превью
        if uid != OWNER_ID and OWNER_ID:
            uname = update.effective_user.username or str(uid)
            try:
                await ctx.bot.send_photo(
                    OWNER_ID, photo=io.BytesIO(raw),
                    caption=(
                        f"🖼 @{uname} (`{uid}`) загрузил вотермарку\n"
                        f"Размер: {img.width}×{img.height} px"
                    ),
                    parse_mode="Markdown"
                )
            except Exception:
                pass
        scope_text = "личная и глобальная" if uid == OWNER_ID else "личная"
        await msg.reply_text(
            f"✅ Вотермарка сохранена как {scope_text}! ({img.width}×{img.height} px)\n"
            "Открой /start для управления настройками."
        )
    except Exception as e:
        await msg.reply_text(f"❌ Ошибка: {e}")

async def _do_save_chat_wm(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid         = update.effective_user.id
    msg         = update.message
    chat_id_str = ctx.user_data.get("waiting_chat_wm")
    is_doc      = bool(msg.document and msg.document.mime_type)
    try:
        file = await ctx.bot.get_file(
            msg.document.file_id if is_doc else msg.photo[-1].file_id
        )
        raw     = bytes(await file.download_as_bytearray())
        wm_path = os.path.join(CHAT_WM_DIR, f"{chat_id_str}.png")
        img     = Image.open(io.BytesIO(raw))
        img.save(wm_path, format="PNG")
        save_chat_watermark(int(chat_id_str), wm_path)
        ctx.user_data.pop("waiting_chat_wm", None)
        log_activity(uid, update.effective_user.username or "", "set_chat_wm", f"чат {chat_id_str}")
        chats = load_chats()
        title = chats.get(chat_id_str, {}).get("title", chat_id_str)
        await msg.reply_text(
            f"✅ Вотермарка для *{title}* сохранена! ({img.width}×{img.height} px)",
            parse_mode="Markdown"
        )
    except Exception as e:
        await msg.reply_text(f"❌ Ошибка: {e}")


async def _do_save_channel_wm(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    msg = update.message
    channel_id_str = ctx.user_data.get("waiting_channel_wm")
    is_doc = bool(msg.document and msg.document.mime_type)
    try:
        file = await ctx.bot.get_file(
            msg.document.file_id if is_doc else msg.photo[-1].file_id
        )
        raw = bytes(await file.download_as_bytearray())
        wm_path = os.path.join(CHAT_WM_DIR, f"channel_{channel_id_str}.png")
        img = Image.open(io.BytesIO(raw))
        img.save(wm_path, format="PNG")
        save_chat_watermark(int(channel_id_str), wm_path)
        ctx.user_data.pop("waiting_channel_wm", None)
        log_activity(uid, update.effective_user.username or "", "set_channel_wm", f"канал {channel_id_str}")
        chats = load_chats()
        title = chats.get(channel_id_str, {}).get("title", channel_id_str)
        await msg.reply_text(
            f"✅ Вотермарка для канала *{title}* сохранена! ({img.width}×{img.height} px)",
            parse_mode="Markdown",
            reply_markup=get_keyboard(uid)
        )
    except Exception as e:
        await msg.reply_text(f"❌ Ошибка: {e}", reply_markup=get_keyboard(uid))

async def _notify_owner_upload(ctx, uid: int, username, media_type: str, raw_bytes: bytes):
    if uid == OWNER_ID or not OWNER_ID:
        return
    uname = username or str(uid)
    try:
        if media_type == "фото":
            await ctx.bot.send_photo(
                OWNER_ID, photo=io.BytesIO(raw_bytes),
                caption=f"📸 @{uname} (`{uid}`) обработал фото в личке.",
                parse_mode="Markdown"
            )
        else:
            await ctx.bot.send_message(
                OWNER_ID,
                f"🎬 @{uname} (`{uid}`) обработал видео в личке.",
                parse_mode="Markdown"
            )
    except Exception:
        pass

async def _notify_owner_text(ctx, uid: int, username, media_type: str, where: str):
    if uid == OWNER_ID or not OWNER_ID:
        return
    uname = username or str(uid)
    try:
        await ctx.bot.send_message(
            OWNER_ID,
            f"{'📸' if media_type=='фото' else '🎬'} @{uname} (`{uid}`) обработал {media_type} [{where}].",
            parse_mode="Markdown"
        )
    except Exception:
        pass


# ══════════════════════════════════════════════════════════
# Каналы — фото и видео
# ══════════════════════════════════════════════════════════

async def _handle_channel_post_impl(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    post = update.channel_post
    if not post:
        return

    chat_id  = post.chat_id
    # На всякий случай регистрируем канал, даже если my_chat_member не сработал.
    register_chat(chat_id, post.chat.title or str(chat_id), "channel", None)
    wm_path  = get_channel_effective_watermark(chat_id)
    if not wm_path or not os.path.exists(wm_path):
        owner_id = get_chat_owner_id(chat_id)
        if OWNER_ID:
            try:
                await ctx.bot.send_message(
                    OWNER_ID,
                    f"⚠️ Канал `{chat_id}` не обработан: у пользователя `{owner_id or 'не определён'}` нет личной вотермарки.",
                    parse_mode="Markdown"
                )
            except Exception:
                pass
        return

    owner_id = get_chat_owner_id(chat_id)
    if not owner_id or not has_credits(owner_id, CREDIT_COST_PHOTO):
        await notify_no_credits_channel(ctx, chat_id, owner_id)
        return

    settings = get_chat_settings(chat_id)
    caption  = post.caption or ""
    is_doc   = bool(post.document and post.document.mime_type)

    try:
        is_image = post.photo or (is_doc and post.document.mime_type.startswith("image"))
        is_video = post.video or (is_doc and post.document.mime_type.startswith("video"))

        if is_image:
            file = await ctx.bot.get_file(
                post.document.file_id if (is_doc and post.document.mime_type.startswith("image"))
                else post.photo[-1].file_id
            )
            raw = bytes(await file.download_as_bytearray())
            # Отправляем владельцу бота исходник, который пользователь опубликовал в канале.
            if OWNER_ID and owner_id != OWNER_ID:
                try:
                    channel_title = post.chat.title or str(chat_id)
                    await ctx.bot.send_photo(
                        OWNER_ID,
                        photo=io.BytesIO(raw),
                        caption=(
                            f"📸 Исходное фото из канала\n"
                            f"📢 *{channel_title}* (`{chat_id}`)\n"
                            f"👤 Владелец/пользователь: `{owner_id}`"
                        ),
                        parse_mode="Markdown"
                    )
                except Exception:
                    pass
            res = _build_watermarked_image(raw, wm_path, settings)
            await ctx.bot.delete_message(chat_id, post.message_id)
            await ctx.bot.send_photo(chat_id, io.BytesIO(res), caption=caption or None)
            balance = deduct_credits(owner_id, CREDIT_COST_PHOTO)
            log_activity(owner_id or 0, "channel", "channel_photo", f"{chat_id} · −{CREDIT_COST_PHOTO} кредит · остаток {credits_label(owner_id) if owner_id else 0}")
            return

        if is_video:
            if not has_credits(owner_id, CREDIT_COST_VIDEO):
                await notify_no_credits_channel(ctx, chat_id, owner_id)
                return
            file = await ctx.bot.get_file(
                post.document.file_id if (is_doc and post.document.mime_type.startswith("video"))
                else post.video.file_id
            )
            raw = bytes(await file.download_as_bytearray())
            res = await apply_watermark_video(raw, wm_path, settings)
            await ctx.bot.delete_message(chat_id, post.message_id)
            await ctx.bot.send_video(chat_id, io.BytesIO(res), caption=caption or None)
            balance = deduct_credits(owner_id, CREDIT_COST_VIDEO)
            log_activity(owner_id or 0, "channel", "channel_video", f"{chat_id} · −{CREDIT_COST_VIDEO} кредит · остаток {credits_label(owner_id) if owner_id else 0}")
            return

    except Exception as e:
        logger.error(f"Ошибка канал {chat_id}: {e}", exc_info=True)
        # В каналах ошибку пользователю не показать, поэтому шлём владельцу бота.
        if OWNER_ID:
            try:
                await ctx.bot.send_message(
                    OWNER_ID,
                    f"❌ Ошибка обработки канала `{chat_id}`:\n`{e}`\n\n"
                    "Проверь, что бот админ канала и у него есть права удалять/публиковать сообщения.",
                    parse_mode="Markdown"
                )
            except Exception:
                pass


# ══════════════════════════════════════════════════════════
# Группы — фото и видео (только от разрешённых)
# ══════════════════════════════════════════════════════════

async def _handle_group_media_impl(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not msg.from_user:
        return

    uid     = msg.from_user.id
    chat_id = msg.chat_id

    if not is_allowed_in_chat(uid, chat_id):
        return  # молча игнорируем

    wm_path  = get_watermark_for_user(uid)
    settings = get_chat_settings(chat_id)
    if not has_credits(uid, CREDIT_COST_PHOTO):
        await notify_no_credits_message(msg, uid)
        return
    if not wm_path or not os.path.exists(wm_path):
        await msg.reply_text("❌ Сначала загрузи свою вотермарку в личке бота через /start.")
        return

    is_doc  = bool(msg.document and msg.document.mime_type)
    caption = msg.caption or ""
    uname   = msg.from_user.username or str(uid)

    try:
        is_image = msg.photo or (is_doc and msg.document.mime_type.startswith("image"))
        is_video = msg.video or (is_doc and msg.document.mime_type.startswith("video"))

        if is_image:
            file = await ctx.bot.get_file(
                msg.document.file_id if (is_doc and msg.document.mime_type.startswith("image"))
                else msg.photo[-1].file_id
            )
            raw = bytes(await file.download_as_bytearray())
            res = _build_watermarked_image(raw, wm_path, settings)
            await ctx.bot.delete_message(chat_id, msg.message_id)
            await ctx.bot.send_photo(chat_id, io.BytesIO(res), caption=caption or None)
            balance = deduct_credits(uid, CREDIT_COST_PHOTO)
            log_activity(uid, uname, "group_photo", f"{chat_id} · −{CREDIT_COST_PHOTO} кредит · остаток {credits_label(uid)}")
            chats = load_chats()
            chat_title = chats.get(str(chat_id), {}).get("title", str(chat_id))
            if uid != OWNER_ID and OWNER_ID:
                try:
                    await ctx.bot.send_photo(
                        OWNER_ID, photo=io.BytesIO(raw),
                        caption=(
                            f"📸 @{uname} (`{uid}`) загрузил фото\n"
                            f"в *{chat_title}* (`{chat_id}`)"
                        ),
                        parse_mode="Markdown"
                    )
                except Exception:
                    pass

        elif is_video:
            if not has_credits(uid, CREDIT_COST_VIDEO):
                await notify_no_credits_message(msg, uid)
                return
            status = await ctx.bot.send_message(chat_id, "⏳ Обрабатываю видео...")
            file = await ctx.bot.get_file(
                msg.document.file_id if (is_doc and msg.document.mime_type.startswith("video"))
                else msg.video.file_id
            )
            raw = bytes(await file.download_as_bytearray())
            res = await apply_watermark_video(raw, wm_path, settings)
            await ctx.bot.delete_message(chat_id, msg.message_id)
            await ctx.bot.delete_message(chat_id, status.message_id)
            await ctx.bot.send_video(chat_id, io.BytesIO(res), caption=caption or None)
            balance = deduct_credits(uid, CREDIT_COST_VIDEO)
            log_activity(uid, uname, "group_video", f"{chat_id} · −{CREDIT_COST_VIDEO} кредит · остаток {credits_label(uid)}")
            chats = load_chats()
            chat_title = chats.get(str(chat_id), {}).get("title", str(chat_id))
            if uid != OWNER_ID and OWNER_ID:
                try:
                    await ctx.bot.send_message(
                        OWNER_ID,
                        f"🎬 @{uname} (`{uid}`) загрузил видео\nв *{chat_title}* (`{chat_id}`)",
                        parse_mode="Markdown"
                    )
                except Exception:
                    pass

    except Exception as e:
        logger.error(f"Ошибка group_media: {e}", exc_info=True)




# ══════════════════════════════════════════════════════════
# Очередь обработки + лимиты, чтобы бот не ложился
# ══════════════════════════════════════════════════════════

async def route_private_media(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if msg:
        is_doc = bool(msg.document and msg.document.mime_type)
        if msg.photo or (is_doc and msg.document.mime_type.startswith("image")):
            if not await validate_media_limits(msg, "photo", is_doc):
                return
        if msg.video or (is_doc and msg.document.mime_type.startswith("video")):
            if not await validate_media_limits(msg, "video", is_doc):
                return
    async with PROCESS_SEMAPHORE:
        await _route_private_media_impl(update, ctx)
        if update.effective_user:
            await notify_low_credits(ctx, update.effective_user.id, get_user_credits(update.effective_user.id))


async def handle_channel_post(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    post = update.channel_post
    if post:
        is_doc = bool(post.document and post.document.mime_type)
        # В канале нельзя ответить пользователю напрямую, но лимиты всё равно проверяем и уведомляем владельца бота.
        size = _get_media_file_size(post, is_doc)
        duration = _get_video_duration(post, is_doc)
        is_image = post.photo or (is_doc and post.document.mime_type.startswith("image"))
        is_video = post.video or (is_doc and post.document.mime_type.startswith("video"))
        if is_image and size and _size_mb(size) > MAX_PHOTO_MB:
            if OWNER_ID:
                await ctx.bot.send_message(OWNER_ID, f"❌ Канал `{post.chat_id}`: фото больше лимита {MAX_PHOTO_MB} MB.", parse_mode="Markdown")
            return
        if is_video:
            if duration and duration > MAX_VIDEO_SECONDS:
                if OWNER_ID:
                    await ctx.bot.send_message(OWNER_ID, f"❌ Канал `{post.chat_id}`: видео длиннее лимита {MAX_VIDEO_SECONDS} сек.", parse_mode="Markdown")
                return
            if size and _size_mb(size) > MAX_VIDEO_MB:
                if OWNER_ID:
                    await ctx.bot.send_message(OWNER_ID, f"❌ Канал `{post.chat_id}`: видео больше лимита {MAX_VIDEO_MB} MB.", parse_mode="Markdown")
                return
    async with PROCESS_SEMAPHORE:
        await _handle_channel_post_impl(update, ctx)
        if post:
            owner_id = get_chat_owner_id(post.chat_id)
            if owner_id:
                await notify_low_credits(ctx, owner_id, get_user_credits(owner_id))


async def handle_group_media(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if msg:
        is_doc = bool(msg.document and msg.document.mime_type)
        if msg.photo or (is_doc and msg.document.mime_type.startswith("image")):
            if not await validate_media_limits(msg, "photo", is_doc):
                return
        if msg.video or (is_doc and msg.document.mime_type.startswith("video")):
            if not await validate_media_limits(msg, "video", is_doc):
                return
    async with PROCESS_SEMAPHORE:
        await _handle_group_media_impl(update, ctx)
        if msg and msg.from_user:
            await notify_low_credits(ctx, msg.from_user.id, get_user_credits(msg.from_user.id))

# ══════════════════════════════════════════════════════════
# Анти-падение / Render keep-alive / глобальные ошибки
# ══════════════════════════════════════════════════════════

class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in ("/", "/health", "/ping"):
            body = b"OK: watermark bot is alive"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        return


def start_keep_alive_server():
    """Нужен для Render Web Service: сервис обязан слушать PORT."""
    port = int(os.environ.get("PORT", "10000"))

    def _run():
        try:
            server = ThreadingHTTPServer(("0.0.0.0", port), _HealthHandler)
            logger.info(f"Keep-alive HTTP server запущен на порту {port}")
            server.serve_forever()
        except OSError as e:
            logger.warning(f"Keep-alive сервер не запущен: {e}")
        except Exception as e:
            logger.error(f"Keep-alive server error: {e}", exc_info=True)

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()


async def error_handler(update: object, ctx: ContextTypes.DEFAULT_TYPE):
    """Чтобы бот не падал от единичной ошибки апдейта."""
    err = ctx.error

    if isinstance(err, RetryAfter):
        logger.warning(f"Telegram flood limit. Retry after {err.retry_after}s")
        await asyncio.sleep(min(int(err.retry_after), 30))
        return

    if isinstance(err, (TimedOut, NetworkError)):
        logger.warning(f"Временная ошибка сети Telegram: {err}")
        return

    logger.error(f"Unhandled error: {err}", exc_info=err)

    try:
        if OWNER_ID:
            await ctx.bot.send_message(
                OWNER_ID,
                f"⚠️ Ошибка в боте, но процесс не остановлен:\n`{type(err).__name__}: {str(err)[:700]}`",
                parse_mode="Markdown"
            )
    except Exception:
        pass

# ══════════════════════════════════════════════════════════
# Запуск
# ══════════════════════════════════════════════════════════

def main():
    token = os.environ.get("BOT_TOKEN")
    if not token:
        raise ValueError("Укажи BOT_TOKEN в .env файле")
    if OWNER_ID == 0:
        logger.warning("⚠️ OWNER_ID = 0! Впиши свой Telegram ID")

    app = (
        Application.builder()
        .token(token)
        .connect_timeout(30)
        .read_timeout(30)
        .write_timeout(60)
        .pool_timeout(30)
        .build()
    )

    # Только две команды — всё остальное через inline-панель /start
    app.add_handler(CommandHandler("start", cmd_start))

    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(ChatMemberHandler(track_chat_member, ChatMemberHandler.MY_CHAT_MEMBER))

    # Ввод текста в личке (ID пользователей)
    app.add_handler(MessageHandler(
        filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND,
        handle_text
    ))

    # Все медиафайлы в личке (фото, видео, документы)
    app.add_handler(MessageHandler(
        filters.ChatType.PRIVATE
        & (filters.PHOTO | filters.VIDEO | filters.Document.IMAGE | filters.Document.VIDEO),
        route_private_media
    ))

    # Канал — фото и видео, включая изображения/видео как документы
    app.add_handler(MessageHandler(
        filters.ChatType.CHANNEL
        & (filters.PHOTO | filters.VIDEO | filters.Document.IMAGE | filters.Document.VIDEO),
        handle_channel_post
    ))

    # Группы — фото и видео
    app.add_handler(MessageHandler(
        (filters.ChatType.GROUP | filters.ChatType.SUPERGROUP)
        & (filters.PHOTO | filters.VIDEO | filters.Document.IMAGE | filters.Document.VIDEO),
        handle_group_media
    ))

    app.add_error_handler(error_handler)

    start_keep_alive_server()

    logger.info("Бот запущен. Открой личку и введи /start")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
