# 📋 ПОШАГОВЫЙ ГАЙД ПО ИСПРАВЛЕНИЯМ

## Как применить исправления к вашему боту

---

## ШАГ 1: Замена функций JSON (КРИТИЧНО)

### Текущий код (ПРОБЛЕМА):
```python
def safe_load_json(path: str, default):
    # БЕЗ БЛОКИРОВКИ - race condition!
    if not os.path.exists(path):
        return default
    ...
```

### Действие:
1. Удалить функции `safe_load_json()` и `safe_save_json()` (текущие)
2. **Скопировать из `FIXES_CRITICAL.py`:**
   - `get_lock()`
   - `safe_load_json()` (новый вариант)
   - `safe_save_json()` (новый вариант)

### Где находятся:
```python
# В bot4_FINAL_CHANNELS_PRO_BROADCAST_FIXED.py найти:
def safe_load_json(path: str, default):
    """Читает JSON. Если файл пустой/битый — сохраняет .broken и возвращает default."""

def safe_save_json(path: str, data, ensure_ascii: bool = False):
    """Атомарно сохраняет JSON..."""
```

### Что добавить (ПЕРЕД этими функциями):
```python
# ══════════════════════════════════════════════════════════
# Глобальные блокировки для потокобезопасности
# ══════════════════════════════════════════════════════════

JSON_LOCKS = {}
CREDITS_LOCK = threading.RLock()

def get_lock(path: str) -> threading.RLock:
    """Получить блокировку для конкретного JSON файла"""
    if path not in JSON_LOCKS:
        JSON_LOCKS[path] = threading.RLock()
    return JSON_LOCKS[path]
```

---

## ШАГ 2: Исправление race condition в кредитах

### Текущий код (ПРОБЛЕМА):
```python
def add_user_credits(user_id: int, amount: int) -> int | None:
    if user_id == OWNER_ID:
        return None
    new_balance = max(0, get_user_credits(user_id) + int(amount))  # ← LOAD
    set_user_credits(user_id, new_balance)  # ← SAVE
    return new_balance
```

### Исправленный код:
```python
CREDITS_LOCK = threading.RLock()  # ← Добавить ВСЕ ДВЕ функции!

def add_user_credits(user_id: int, amount: int) -> int | None:
    if user_id == OWNER_ID:
        return None
    
    with CREDITS_LOCK:  # ← ОБЯЗАТЕЛЬНО добавить
        credits = load_credits()
        current = int(credits.get(str(user_id), 0))
        new_balance = max(0, current + int(amount))
        credits[str(user_id)] = new_balance
        save_credits(credits)
        return new_balance


def set_user_credits(user_id: int, amount: int):
    if user_id == OWNER_ID:
        return
    
    with CREDITS_LOCK:  # ← ОБЯЗАТЕЛЬНО добавить
        credits = load_credits()
        credits[str(user_id)] = max(0, int(amount))
        save_credits(credits)
```

---

## ШАГ 3: Безопасное удаление сообщений в каналах

### Текущий код (ПРОБЛЕМА):
```python
await ctx.bot.delete_message(chat_id, post.message_id)  # ← Может вызвать исключение!
```

### Действие:
1. **Скопировать функцию из `FIXES_CRITICAL.py`:**
   ```python
   async def safe_delete_message(ctx, chat_id, message_id, notify_owner=True) -> bool:
       """Безопасное удаление сообщения с обработкой ошибок"""
   ```

2. **Найти в коде ВСЕ места, где есть `delete_message` и заменить:**
   ```python
   # БЫЛО:
   await ctx.bot.delete_message(chat_id, post.message_id)
   
   # СТАЛО:
   await safe_delete_message(ctx, chat_id, post.message_id)
   ```

3. **Найти эти строки:**
   - В `_handle_channel_post_impl` (строка ~1450):
     ```python
     await ctx.bot.delete_message(chat_id, post.message_id)
     ```
   - В `_handle_group_media_impl` (строка ~1530):
     ```python
     await ctx.bot.delete_message(chat_id, msg.message_id)
     await ctx.bot.delete_message(chat_id, status.message_id)
     ```

---

## ШАГ 4: Timeout при скачивании файлов

### Текущий код (ПРОБЛЕМА):
```python
raw = bytes(await file.download_as_bytearray())  # ← Может висеть 5+ минут!
```

### Действие:
1. **Скопировать функцию из `FIXES_CRITICAL.py`:**
   ```python
   async def safe_download_file(file, timeout_sec: int = 30) -> Optional[bytes]:
       """Скачать файл с timeout"""
   ```

2. **Найти ВСЕ `download_as_bytearray()` и заменить:**
   ```python
   # БЫЛО:
   raw = bytes(await file.download_as_bytearray())
   
   # СТАЛО:
   raw = await safe_download_file(file, timeout_sec=30)
   if not raw:
       await msg.reply_text("❌ Ошибка скачивания файла (timeout)")
       return
   ```

3. **Места где есть эта ошибка:**
   - В `_route_private_media_impl` (несколько мест с фото и видео)
   - В `_handle_channel_post_impl` (фото и видео канала)
   - В `_handle_group_media_impl` (фото и видео группы)

---

## ШАГ 5: Исправление рассылки (параллельная обработка)

### Текущий код (ПРОБЛЕМА):
```python
async def send_broadcast_to_users(ctx, text: str) -> dict:
    # ...
    for user_id in recipients:
        try:
            await ctx.bot.send_message(user_id, text)  # ← По одному!
            sent += 1
            await asyncio.sleep(0.05)  # ← Медленно
        except Forbidden:
            # ...
```

### Действие:
1. **Полностью заменить функцию из `FIXES_CRITICAL.py`:**
   - `async def send_message_with_retry(...)` — новая
   - `async def send_broadcast_to_users(...)` — новая

2. **Код в handler должен остаться прежним:**
   ```python
   if uid == OWNER_ID and ctx.user_data.get("waiting_broadcast_confirm"):
       if text == BTN_BROADCAST_SEND:
           broadcast_text = ctx.user_data.pop("broadcast_text", "")
           result = await send_broadcast_to_users(ctx, broadcast_text)  # ← Эта строка работает
   ```

---

## ШАГ 6: Path traversal валидация

### Текущий код (ПРОБЛЕМА):
```python
def get_user_watermark_path(user_id: int):
    p = os.path.join(USER_WM_DIR, f"{user_id}.png")
    # Если user_id = "../../../etc/passwd", можно прочитать чужие файлы!
```

### Действие:
1. **Заменить `get_user_watermark_path()` из `FIXES_CRITICAL.py`**
2. **Добавить функцию валидации:**
   ```python
   def validate_user_id(user_id):
       """Валидировать user_id перед использованием в путях"""
       if not isinstance(user_id, int):
           raise ValueError(f"user_id must be int, got {type(user_id)}")
       if user_id <= 0:
           raise ValueError(f"user_id must be positive, got {user_id}")
       return user_id
   ```

3. **В `save_user_watermark()` добавить:**
   ```python
   def save_user_watermark(user_id: int, img_bytes: bytes) -> str:
       user_id = validate_user_id(user_id)  # ← Добавить
       p = os.path.join(USER_WM_DIR, f"{user_id}.png")
       img = Image.open(io.BytesIO(img_bytes))
       img.save(p, format="PNG")
       return p
   ```

---

## ШАГ 7: Проверка ffmpeg при запуске

### Действие:
В функции `main()`, **ПЕРЕД `app.run_polling()`**, добавить:

```python
def main():
    token = os.environ.get("BOT_TOKEN")
    if not token:
        raise ValueError("Укажи BOT_TOKEN в .env файле")
    if OWNER_ID == 0:
        logger.warning("⚠️ OWNER_ID = 0! Впиши свой Telegram ID")
    
    # ← ДОБАВИТЬ ЭТО:
    try:
        import subprocess
        result = subprocess.run(["ffmpeg", "-version"], capture_output=True, timeout=5)
        if result.returncode != 0:
            logger.warning("⚠️ ffmpeg может быть не установлен или не работает")
    except FileNotFoundError:
        logger.error("❌ ffmpeg НЕ установлен! Установи: apt-get install ffmpeg")
    except Exception as e:
        logger.warning(f"⚠️ Ошибка проверки ffmpeg: {e}")
    
    # ... остальной код main()
```

---

## ШАГ 8: Обновить requirements.txt

Добавить:
```
python-telegram-bot>=20.1
pillow>=9.0
python-dotenv>=0.20
moviepy>=1.0.3
```

Установить:
```bash
pip install -r requirements.txt
```

---

## 🔍 ПРОВЕРКА: Где найти все места для исправления

### Поиск в IDE или grep:

```bash
# Найти все delete_message
grep -n "delete_message" bot4_FINAL_CHANNELS_PRO_BROADCAST_FIXED.py

# Найти все download_as_bytearray
grep -n "download_as_bytearray" bot4_FINAL_CHANNELS_PRO_BROADCAST_FIXED.py

# Найти все send_message (в рассылке)
grep -n "send_message" bot4_FINAL_CHANNELS_PRO_BROADCAST_FIXED.py
```

---

## ✅ ФИНАЛЬНЫЙ ЧЕКЛИСТ

После применения всех исправлений:

- [ ] Заменены `safe_load_json()` и `safe_save_json()`
- [ ] Добавлены глобальные блокировки `JSON_LOCKS` и `CREDITS_LOCK`
- [ ] Исправлены `add_user_credits()` и `set_user_credits()`
- [ ] Заменены ВСЕ `delete_message()` на `safe_delete_message()`
- [ ] Заменены ВСЕ `download_as_bytearray()` на `safe_download_file()`
- [ ] Переписана функция `send_broadcast_to_users()`
- [ ] Добавлена валидация в `get_user_watermark_path()`
- [ ] Добавлена проверка ffmpeg в `main()`
- [ ] Обновлен `requirements.txt`
- [ ] Тестирование:
  - [ ] Бот запускается без ошибок
  - [ ] Отправка фото в личку работает
  - [ ] Отправка видео в личку работает
  - [ ] Рассылка отправляется нескольким пользователям
  - [ ] Обработка сообщений в канале/группе работает
  - [ ] Кредиты корректно вычитаются

---

## 🆘 Если что-то не работает

1. **Проверить import:**
   ```python
   import threading
   import asyncio
   from typing import Optional
   ```

2. **Проверить в начало файла после imports:**
   ```python
   JSON_LOCKS = {}
   CREDITS_LOCK = threading.RLock()
   ```

3. **Полная исправленная версия** — см. файл `FIXES_CRITICAL.py`

4. **Логирование:** Все функции пишут ошибки в logger, проверить лог

---

## 📞 Вопросы?

Если функция не работает, проверьте:
- Правильно ли скопирована вся функция?
- Не пропущены ли импорты в начале файла?
- Остались ли старые версии функции?

**Рекомендуется:** Полностью переписать файл, используя `FIXES_CRITICAL.py` как шаблон.
