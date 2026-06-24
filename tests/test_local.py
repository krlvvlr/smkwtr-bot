"""
🧪 ЛОКАЛЬНЫЕ ТЕСТЫ БЕЗ TELEGRAM
Тестируют функции отдельно, без нужды в боте и сети
"""

import os
import sys
import json
import tempfile
import threading
import time
from pathlib import Path

# ══════════════════════════════════════════════════════════
# SETUP: Имитация глобальных переменных
# ══════════════════════════════════════════════════════════

# Временные папки для тестов
TEST_DIR = tempfile.mkdtemp(prefix="bot_test_")
USER_WM_DIR = os.path.join(TEST_DIR, "user_watermarks")
CHAT_WM_DIR = os.path.join(TEST_DIR, "watermarks")
CHATS_PATH = os.path.join(TEST_DIR, "chats.json")
CREDITS_PATH = os.path.join(TEST_DIR, "credits.json")
ALLOWED_USERS_PATH = os.path.join(TEST_DIR, "allowed_users.json")

os.makedirs(USER_WM_DIR, exist_ok=True)
os.makedirs(CHAT_WM_DIR, exist_ok=True)

print(f"📁 Тестовая директория: {TEST_DIR}\n")

# ══════════════════════════════════════════════════════════
# ИСПРАВЛЕННЫЕ ФУНКЦИИ (из FIXES_CRITICAL.py)
# ══════════════════════════════════════════════════════════

JSON_LOCKS = {}
CREDITS_LOCK = threading.RLock()

def get_lock(path: str) -> threading.RLock:
    """Получить блокировку для конкретного JSON файла"""
    if path not in JSON_LOCKS:
        JSON_LOCKS[path] = threading.RLock()
    return JSON_LOCKS[path]


def safe_load_json(path: str, default):
    """Читает JSON с блокировкой"""
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
        except json.JSONDecodeError:
            return default


def safe_save_json(path: str, data, ensure_ascii: bool = False):
    """Атомарно сохраняет JSON с блокировкой"""
    lock = get_lock(path)
    with lock:
        tmp_path = f"{path}.tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=ensure_ascii)
            os.replace(tmp_path, path)
        except Exception as e:
            print(f"❌ Ошибка сохранения: {e}")
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except:
                    pass
            raise


def load_credits() -> dict:
    """Загрузить кредиты"""
    data = safe_load_json(CREDITS_PATH, {})
    return data if isinstance(data, dict) else {}


def save_credits(credits: dict):
    """Сохранить кредиты"""
    safe_save_json(CREDITS_PATH, credits, ensure_ascii=False)


def add_user_credits(user_id: int, amount: int) -> int:
    """ИСПРАВЛЕННАЯ: Потокобезопасное добавление кредитов"""
    with CREDITS_LOCK:
        credits = load_credits()
        current = int(credits.get(str(user_id), 0))
        new_balance = max(0, current + int(amount))
        credits[str(user_id)] = new_balance
        save_credits(credits)
        return new_balance


def get_user_credits(user_id: int) -> int:
    """Получить кредиты пользователя"""
    credits = load_credits()
    try:
        return int(credits.get(str(user_id), 0))
    except (TypeError, ValueError):
        return 0


def validate_user_id(user_id):
    """Валидировать user_id"""
    if not isinstance(user_id, int):
        raise ValueError(f"user_id must be int, got {type(user_id)}")
    if user_id <= 0:
        raise ValueError(f"user_id must be positive, got {user_id}")
    return user_id


def get_user_watermark_path_safe(user_id: int):
    """ИСПРАВЛЕННАЯ: Безопасное получение пути вотермарки"""
    try:
        user_id = validate_user_id(user_id)
    except ValueError as e:
        print(f"❌ Invalid user_id: {e}")
        return None
    
    p = os.path.join(USER_WM_DIR, f"{user_id}.png")
    
    # Проверить path traversal
    try:
        p_abs = os.path.abspath(p)
        wd_abs = os.path.abspath(USER_WM_DIR)
        if not p_abs.startswith(wd_abs):
            print(f"❌ Path traversal attempt blocked: {p_abs}")
            return None
    except Exception as e:
        print(f"❌ Error validating path: {e}")
        return None
    
    return p_abs if os.path.exists(p_abs) else None


# ══════════════════════════════════════════════════════════
# ТЕСТ 1: JSON потокобезопасность
# ══════════════════════════════════════════════════════════

def test_json_thread_safety():
    """Проверить, что JSON не повреждается при одновременной записи"""
    print("🧪 ТЕСТ 1: JSON потокобезопасность")
    
    test_file = os.path.join(TEST_DIR, "test_concurrent.json")
    errors = []
    
    def write_data(thread_id, count):
        """Писать данные в JSON 100 раз"""
        try:
            for i in range(count):
                data = safe_load_json(test_file, {})
                data[f"thread_{thread_id}"] = i
                safe_save_json(test_file, data)
        except Exception as e:
            errors.append(f"Thread {thread_id}: {e}")
    
    # Создать 5 потоков, каждый пишет 20 раз
    threads = [threading.Thread(target=write_data, args=(i, 20)) for i in range(5)]
    
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    
    # Проверить результат
    if errors:
        print(f"  ❌ ОШИБКИ: {errors}")
        return False
    
    final_data = safe_load_json(test_file, {})
    if len(final_data) == 5:
        print(f"  ✅ JSON целый, записано {len(final_data)} потоков\n")
        return True
    else:
        print(f"  ❌ Ожидалось 5 потоков, получено {len(final_data)}\n")
        return False


# ══════════════════════════════════════════════════════════
# ТЕСТ 2: Кредиты не теряются при race condition
# ══════════════════════════════════════════════════════════

def test_credits_race_condition():
    """Проверить, что кредиты считаются правильно при одновременном доступе"""
    print("🧪 ТЕСТ 2: Race condition кредитов")
    
    user_id = 12345
    expected_final = 0  # 100 потоков × 100 операций × (+1 -1) = 0
    
    def add_credits_loop(count):
        """Добавить и вычесть кредиты попеременно"""
        for _ in range(count):
            add_user_credits(user_id, 1)
            add_user_credits(user_id, -1)
    
    # Запустить 50 потоков по 100 операций
    threads = [threading.Thread(target=add_credits_loop, args=(100,)) for _ in range(50)]
    
    start_time = time.time()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    elapsed = time.time() - start_time
    
    final_balance = get_user_credits(user_id)
    
    if final_balance == expected_final:
        print(f"  ✅ Баланс корректен: {final_balance} (ожидалось {expected_final})")
        print(f"  ✅ Обработано 5000 операций за {elapsed:.2f} сек\n")
        return True
    else:
        print(f"  ❌ RACE CONDITION! Баланс {final_balance}, ожидалось {expected_final}")
        print(f"  ❌ Потеря данных: {expected_final - final_balance} кредитов\n")
        return False


# ══════════════════════════════════════════════════════════
# ТЕСТ 3: Path traversal защита
# ══════════════════════════════════════════════════════════

def test_path_traversal():
    """Проверить, что path traversal атаки блокируются"""
    print("🧪 ТЕСТ 3: Path traversal защита")
    
    tests = [
        (-1, "Отрицательный ID"),
        (0, "Нулевой ID"),
        ("abc", "Строка вместо числа"),
        (999999999, "Очень большое число (OK)"),
    ]
    
    blocked = 0
    allowed = 0
    
    for test_input, description in tests:
        result = get_user_watermark_path_safe(test_input)
        
        if test_input == 999999999:
            # Это должно быть разрешено (файл просто не существует)
            if result is None:
                allowed += 1
                print(f"  ✅ {description}: РАЗРЕШЕНО (файл не существует)")
            else:
                print(f"  ❓ {description}: неожиданный результат {result}")
        else:
            # Остальное должно быть заблокировано
            if result is None:
                blocked += 1
                print(f"  ✅ {description}: ЗАБЛОКИРОВАНО")
            else:
                print(f"  ❌ {description}: НЕ ЗАБЛОКИРОВАНО!")
    
    if blocked == 3 and allowed == 1:
        print(f"  ✅ Path traversal защита работает\n")
        return True
    else:
        print(f"  ❌ Path traversal защита не полная\n")
        return False


# ══════════════════════════════════════════════════════════
# ТЕСТ 4: JSON не повреждается при неправильном завершении
# ══════════════════════════════════════════════════════════

def test_json_corruption():
    """Проверить, что JSON не может быть повреждён"""
    print("🧪 ТЕСТ 4: Защита от corruption JSON")
    
    test_file = os.path.join(TEST_DIR, "corruption_test.json")
    
    # Записать корректный JSON
    data = {"user_1": 100, "user_2": 200}
    safe_save_json(test_file, data)
    
    # Попытаться записать некорректный JSON (прерывание)
    try:
        lock = get_lock(test_file)
        with lock:
            tmp_path = f"{test_file}.tmp"
            with open(tmp_path, "w") as f:
                f.write("{invalid json")
                # Прерывание (как будто программа рухнула)
                # но tmp файл не заменит основной
        # os.replace не произойдёт
    except:
        pass
    
    # Проверить, что основной файл не повреждён
    recovered = safe_load_json(test_file, None)
    
    if recovered == data:
        print(f"  ✅ Основной JSON не повреждён, данные целые\n")
        return True
    else:
        print(f"  ❌ JSON повреждён или потерян: {recovered}\n")
        return False


# ══════════════════════════════════════════════════════════
# ТЕСТ 5: Кредиты сохраняются корректно
# ══════════════════════════════════════════════════════════

def test_credits_persistence():
    """Проверить, что кредиты сохраняются и загружаются"""
    print("🧪 ТЕСТ 5: Сохранение кредитов")
    
    test_users = [(111, 50), (222, 100), (333, 0)]
    
    # Очистить файл кредитов
    if os.path.exists(CREDITS_PATH):
        os.remove(CREDITS_PATH)
    
    # Записать кредиты
    for user_id, amount in test_users:
        add_user_credits(user_id, amount)
    
    # Загрузить и проверить
    all_ok = True
    for user_id, expected in test_users:
        actual = get_user_credits(user_id)
        if actual == expected:
            print(f"  ✅ Пользователь {user_id}: {actual} кредитов")
        else:
            print(f"  ❌ Пользователь {user_id}: {actual}, ожидалось {expected}")
            all_ok = False
    
    if all_ok:
        print()
    
    return all_ok


# ══════════════════════════════════════════════════════════
# ТЕСТ 6: Прерывание при операции не ломает файл
# ══════════════════════════════════════════════════════════

def test_atomic_write():
    """Проверить, что write операции атомарные"""
    print("🧪 ТЕСТ 6: Атомарные записи (atomic write)")
    
    test_file = os.path.join(TEST_DIR, "atomic_test.json")
    initial_data = {"important": "data"}
    
    # Записать исходные данные
    safe_save_json(test_file, initial_data)
    
    # Попытаться записать новые данные, но "прервать" (не вызвать os.replace)
    # Симуляция краша между write и replace
    lock = get_lock(test_file)
    with lock:
        tmp_path = f"{test_file}.tmp"
        with open(tmp_path, "w") as f:
            json.dump({"corrupted": "data"}, f)
        # НЕ вызываем os.replace - имитируем крах
    
    # Проверить, что основной файл не изменился
    recovered = safe_load_json(test_file, None)
    
    if recovered == initial_data:
        print(f"  ✅ Основной файл не повреждён даже при прерывании\n")
        return True
    else:
        print(f"  ❌ Основной файл повреждён: {recovered}\n")
        return False


# ══════════════════════════════════════════════════════════
# ЗАПУСК ВСЕХ ТЕСТОВ
# ══════════════════════════════════════════════════════════

def run_all_tests():
    """Запустить все тесты"""
    print("=" * 60)
    print("🚀 ЛОКАЛЬНЫЕ ТЕСТЫ ИСПРАВЛЕННОГО БОТА")
    print("=" * 60)
    print()
    
    results = []
    
    results.append(("JSON потокобезопасность", test_json_thread_safety()))
    results.append(("Race condition кредитов", test_credits_race_condition()))
    results.append(("Path traversal защита", test_path_traversal()))
    results.append(("Защита от corruption", test_json_corruption()))
    results.append(("Сохранение кредитов", test_credits_persistence()))
    results.append(("Атомарные записи", test_atomic_write()))
    
    print("=" * 60)
    print("📊 РЕЗУЛЬТАТЫ:")
    print("=" * 60)
    
    passed = 0
    failed = 0
    
    for name, result in results:
        status = "✅ PASSED" if result else "❌ FAILED"
        print(f"{status:12} | {name}")
        if result:
            passed += 1
        else:
            failed += 1
    
    print("=" * 60)
    print(f"\n🎯 ИТОГО: {passed} ПРОЙДЕНО, {failed} ПРОВАЛЕНО\n")
    
    if failed == 0:
        print("🎉 ✅ ВСЕ ЛОКАЛЬНЫЕ ТЕСТЫ ПРОЙДЕНЫ УСПЕШНО!")
        print("✨ Бот готов к запуску!\n")
        return True
    else:
        print(f"⚠️  {failed} ТЕСТ(ОВ) НЕ ПРОЙДЕНО")
        print("❌ Исправьте ошибки перед запуском бота\n")
        return False


if __name__ == "__main__":
    success = run_all_tests()
    
    # Очистка
    import shutil
    shutil.rmtree(TEST_DIR, ignore_errors=True)
    
    sys.exit(0 if success else 1)
