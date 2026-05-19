# Исправления для bot4_FINAL_CHANNELS_PRO_BROADCAST_FIXED.py
# Вставить эти функции в основной файл

import threading
import fcntl
from typing import Optional

# ══════════════════════════════════════════════════════════
# ИСПРАВЛЕНИЕ 1: Файловые блокировки для JSON
# ══════════════════════════════════════════════════════════

JSON_LOCKS = {}

def get_lock(path: str) -> threading.RLock:
    """Получить блокировку для конкретного JSON файла"""
    if path not in JSON_LOCKS:
        JSON_LOCKS[path] = threading.RLock()
    return JSON_LOCKS[path]


def safe_load_json(path: str, default):
    """Читает JSON с блокировкой. Если файл пустой/битый — сохраняет .broken и возвращает default."""
    lock = get_lock(path)
    with lock:
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
    """Атомарно сохраняет JSON с блокировкой, чтобы файл не обрезался при сбое."""
    lock = get_lock(path)
    with lock:
        tmp_path = f"{path}.tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=ensure_ascii)
            os.replace(tmp_path, path)
        except Exception as e:
            logger.error(f"Ошибка сохранения JSON {path}: {e}")
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass
            raise


# ══════════════════════════════════════════════════════════
# ИСПРАВЛЕНИЕ 2: Race condition в credits
# ══════════════════════════════════════════════════════════

CREDITS_LOCK = threading.RLock()

def add_user_credits(user_id: int, amount: int) -> int | None:
    """Потокобезопасное добавление кредитов"""
    if user_id == OWNER_ID:
        return None
    
    with CREDITS_LOCK:
        credits = load_credits()
        current = int(credits.get(str(user_id), 0))
        new_balance = max(0, current + int(amount))
        credits[str(user_id)] = new_balance
        save_credits(credits)
        return new_balance


def set_user_credits(user_id: int, amount: int):
    """Потокобезопасное установление кредитов"""
    if user_id == OWNER_ID:
        return
    
    with CREDITS_LOCK:
        credits = load_credits()
        credits[str(user_id)] = max(0, int(amount))
        save_credits(credits)


# ══════════════════════════════════════════════════════════
# ИСПРАВЛЕНИЕ 3: Path traversal валидация
# ══════════════════════════════════════════════════════════

def validate_user_id(user_id):
    """Валидировать user_id перед использованием в путях"""
    if not isinstance(user_id, int):
        raise ValueError(f"user_id must be int, got {type(user_id)}")
    if user_id <= 0:
        raise ValueError(f"user_id must be positive, got {user_id}")
    return user_id


def get_user_watermark_path(user_id: int) -> Optional[str]:
    """Безопасное получение пути вотермарки с валидацией"""
    try:
        user_id = validate_user_id(user_id)
    except ValueError as e:
        logger.error(f"Invalid user_id for watermark: {e}")
        return None
    
    p = os.path.join(USER_WM_DIR, f"{user_id}.png")
    
    # Проверить path traversal
    try:
        p_abs = os.path.abspath(p)
        wd_abs = os.path.abspath(USER_WM_DIR)
        if not p_abs.startswith(wd_abs):
            logger.error(f"Path traversal attempt blocked: {p_abs}")
            return None
    except Exception as e:
        logger.error(f"Error validating path: {e}")
        return None
    
    return p_abs if os.path.exists(p_abs) else None


def get_chat_id_path(chat_id: int, prefix: str = "") -> str:
    """Безопасное получение пути для файлов чата"""
    if not isinstance(chat_id, int):
        raise ValueError(f"chat_id must be int, got {type(chat_id)}")
    
    filename = f"{prefix}{chat_id}.png" if prefix else f"{chat_id}.png"
    p = os.path.join(CHAT_WM_DIR, filename)
    
    p_abs = os.path.abspath(p)
    wd_abs = os.path.abspath(CHAT_WM_DIR)
    if not p_abs.startswith(wd_abs):
        raise ValueError(f"Path traversal attempt blocked: {p_abs}")
    
    return p_abs


# ══════════════════════════════════════════════════════════
# ИСПРАВЛЕНИЕ 4: Безопасное удаление сообщений
# ══════════════════════════════════════════════════════════

async def safe_delete_message(ctx: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int, notify_owner: bool = True) -> bool:
    """Безопасное удаление сообщения с обработкой ошибок"""
    try:
        await ctx.bot.delete_message(chat_id, message_id)
        return True
    except BadRequest as e:
        msg_str = str(e).lower()
        if "message to delete not found" in msg_str or "message text is empty" in msg_str:
            logger.warning(f"Message {message_id} in {chat_id} already deleted or empty")
        else:
            logger.error(f"BadRequest deleting message {message_id} in {chat_id}: {e}")
        return False
    except Forbidden as e:
        logger.error(f"Bot is not admin in {chat_id}, cannot delete message {message_id}")
        if notify_owner and OWNER_ID:
            try:
                await ctx.bot.send_message(
                    OWNER_ID,
                    f"⚠️ Bot не админ в чате/канале `{chat_id}`. "
                    f"Не могу удалять сообщения.",
                    parse_mode="Markdown"
                )
            except Exception:
                pass
        return False
    except Exception as e:
        logger.error(f"Unexpected error deleting message {message_id}: {e}", exc_info=True)
        return False


# ══════════════════════════════════════════════════════════
# ИСПРАВЛЕНИЕ 5: Timeout для скачивания файлов
# ══════════════════════════════════════════════════════════

async def safe_download_file(file, timeout_sec: int = 30) -> Optional[bytes]:
    """Скачать файл с timeout"""
    try:
        raw = bytes(await asyncio.wait_for(
            file.download_as_bytearray(),
            timeout=timeout_sec
        ))
        return raw
    except asyncio.TimeoutError:
        logger.error(f"Timeout downloading file (>{timeout_sec}s)")
        return None
    except Exception as e:
        logger.error(f"Error downloading file: {e}")
        return None


# ══════════════════════════════════════════════════════════
# ИСПРАВЛЕНИЕ 6: Улучшенная рассылка (параллельная с retry)
# ══════════════════════════════════════════════════════════

async def send_message_with_retry(
    ctx: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    text: str,
    max_retries: int = 2
) -> str:
    """Отправить сообщение с повторными попытками"""
    for attempt in range(max_retries):
        try:
            await ctx.bot.send_message(user_id, text, parse_mode="HTML")
            return "sent"
        except RetryAfter as e:
            wait_time = min(int(e.retry_after), 30)
            logger.warning(f"RetryAfter for {user_id}, waiting {wait_time}s")
            if attempt < max_retries - 1:
                await asyncio.sleep(wait_time)
            else:
                return "retry_failed"
        except Forbidden as e:
            msg = str(e).lower()
            if "bot was blocked" in msg or "chat not found" in msg:
                mark_blocked_user(user_id, "blocked_bot")
                return "blocked"
            return "forbidden"
        except BadRequest as e:
            msg = str(e).lower()
            if "chat not found" in msg or "user is deactivated" in msg:
                mark_blocked_user(user_id, str(e)[:120])
                return "blocked"
            logger.warning(f"BadRequest for {user_id}: {e}")
            return "badrequest"
        except Exception as e:
            logger.warning(f"Unexpected error sending to {user_id}: {e}")
            if attempt == max_retries - 1:
                return "failed"
            await asyncio.sleep(1)
    
    return "failed"


async def send_broadcast_to_users(ctx: ContextTypes.DEFAULT_TYPE, text: str) -> dict:
    """Отправить рассылку всем пользователям (параллельно)"""
    users = sorted(load_allowed_users())
    recipients = [u for u in users if u != OWNER_ID]
    
    sent = blocked = failed = 0
    batch_size = 10  # Одновременно 10 пользователей
    
    for i in range(0, len(recipients), batch_size):
        batch = recipients[i:i+batch_size]
        tasks = [
            send_message_with_retry(ctx, uid, text)
            for uid in batch
        ]
        results = await asyncio.gather(*tasks, return_exceptions=False)
        
        for result in results:
            if result == "sent":
                sent += 1
            elif result == "blocked":
                blocked += 1
            else:
                failed += 1
        
        # Пауза между батчами
        if i + batch_size < len(recipients):
            await asyncio.sleep(1)
    
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


# ════════════���═════════════════════════════════════════════
# ИСПРАВЛЕНИЕ 7: Callback data не превышает 64 байта
# ══════════════════════════════════════════════════════════

def shorten_callback_data(data: str) -> str:
    """Сократить callback_data если он > 64 байт, используя хеш"""
    if len(data.encode('utf-8')) > 60:  # Оставить запас
        import hashlib
        hash_str = hashlib.sha256(data.encode()).hexdigest()[:8]
        # Сохранить в ctx.user_data если нужно восстановить
        return f"cb_{hash_str}"
    return data


# Пример использования в callback_handler:
# if data.startswith("cb_"):
#     # Прочитать из ctx.user_data
#     real_data = ctx.user_data.get(data)
# else:
#     real_data = data


# ══════════════════════════════════════════════════════════
# ИСПРАВЛЕНИЕ 8: Безопасная обработка видео с проверкой ffmpeg
# ══════════════════════════════════════════════════════════

async def check_video_dependencies() -> bool:
    """Проверить наличие moviepy и ffmpeg"""
    try:
        import moviepy
        logger.info("✓ moviepy установлен")
    except ImportError:
        logger.error("✗ moviepy не установлен: pip install moviepy")
        return False
    
    try:
        import subprocess
        result = subprocess.run(
            ["ffmpeg", "-version"],
            capture_output=True,
            timeout=5
        )
        if result.returncode == 0:
            logger.info("✓ ffmpeg установлен")
            return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        logger.error("✗ ffmpeg не установлен: apt-get install ffmpeg")
        return False
    
    return False


async def apply_watermark_video(video_bytes: bytes, wm_path: str, settings: dict) -> bytes:
    """Наложить вотермарку на видео с проверками"""
    try:
        from moviepy.editor import VideoFileClip, ImageClip, CompositeVideoClip
    except ImportError as e:
        raise RuntimeError("moviepy не установлен: pip install moviepy") from e

    if not os.path.exists(wm_path):
        raise FileNotFoundError(f"Вотермарка не найдена: {wm_path}")

    s = settings
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp_in:
        tmp_in.write(video_bytes)
        tmp_in_path = tmp_in.name
    tmp_out_path = tmp_in_path.replace(".mp4", "_wm.mp4")
    wm_tmp_path  = tmp_in_path.replace(".mp4", "_wm_logo.png")

    def _process():
        try:
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
            # Использовать verbose=0 чтобы не заспамить логи
            final.write_videofile(
                tmp_out_path, codec="libx264", audio_codec="aac",
                logger=None, threads=2, verbose=False
            )
            clip.close()
            final.close()
        except OSError as e:
            if "libx264" in str(e):
                raise RuntimeError("ffmpeg не установлен или нет кодека libx264")
            raise

    try:
        loop = asyncio.get_event_loop()
        await asyncio.wait_for(
            loop.run_in_executor(None, _process),
            timeout=300  # 5 минут максимум
        )
    except asyncio.TimeoutError:
        raise RuntimeError("Обработка видео заняла слишком много времени (>5 мин)")

    try:
        with open(tmp_out_path, "rb") as f:
            result = f.read()
    finally:
        for p in (tmp_in_path, tmp_out_path, wm_tmp_path):
            try:
                if os.path.exists(p):
                    os.unlink(p)
            except Exception:
                pass
    
    return result


# ══════════════════════════════════════════════════════════
# ИСПРАВЛЕНИЕ 9: Безопасный download в приватные чаты
# ══════════════════════════════════════════════════════════

async def _route_private_media_impl_SAFE(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_allowed(uid):
        await update.message.reply_text("⛔ У тебя нет доступа.")
        return

    msg = update.message
    is_doc = bool(msg.document and msg.document.mime_type)

    # ── Обработка фото ────────────────────────────────────
    if msg.photo or (is_doc and msg.document.mime_type.startswith("image")):
        wm = get_watermark_for_user(uid)
        if not wm:
            await msg.reply_text("❌ Сначала загрузи свою вотермарку через /start")
            return
        if not has_credits(uid, CREDIT_COST_PHOTO):
            await notify_no_credits_message(msg, uid)
            return
        try:
            file = await ctx.bot.get_file(
                msg.document.file_id if (is_doc and msg.document.mime_type.startswith("image"))
                else msg.photo[-1].file_id
            )
            raw = await safe_download_file(file, timeout_sec=30)  # ← Добавлен timeout
            if not raw:
                await msg.reply_text("❌ Ошибка скачивания файла (timeout)")
                return
            
            result = _build_watermarked_image(raw, wm, load_settings())
            new_balance = deduct_credits(uid, CREDIT_COST_PHOTO)
            caption_done = "✅ Готово!" if new_balance is None else f"✅ Готово! Осталось: {new_balance}"
            await msg.reply_photo(io.BytesIO(result), caption=caption_done)
            log_activity(uid, update.effective_user.username or "", "photo_processed", f"−{CREDIT_COST_PHOTO} кредит")
        except Exception as e:
            logger.error(f"Ошибка фото: {e}", exc_info=True)
            await msg.reply_text(f"❌ Ошибка: {e}")
        return

    # ── Обработка видео ───────────────────────────────────
    if msg.video or (is_doc and msg.document.mime_type and msg.document.mime_type.startswith("video")):
        wm = get_watermark_for_user(uid)
        if not wm or not os.path.exists(wm):
            await msg.reply_text("❌ Сначала загрузи свою вотермарку")
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
            raw = await safe_download_file(file, timeout_sec=60)  # ← Timeout для видео
            if not raw:
                await status.edit_text("❌ Ошибка скачивания видео (timeout)")
                return
            
            result = await apply_watermark_video(raw, wm, load_settings())
            new_balance = deduct_credits(uid, CREDIT_COST_VIDEO)
            caption_done = "✅ Готово!" if new_balance is None else f"✅ Готово! Осталось: {new_balance}"
            await status.delete()
            await msg.reply_video(io.BytesIO(result), caption=caption_done)
            log_activity(uid, update.effective_user.username or "", "video_processed", f"−{CREDIT_COST_VIDEO} кредит")
        except Exception as e:
            logger.error(f"Ошибка видео: {e}", exc_info=True)
            await status.edit_text(f"❌ Ошибка: {e}")
        return
