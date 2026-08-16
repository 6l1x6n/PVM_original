# ТЕХНИЧЕСКОЕ ЗАДАНИЕ — PVM Original (v3.10.64)

---

## 1. Обзор системы

### 1.1 Назначение
PVM.core — гибридная POS-кассовая система + управление складом + бот-автоматизация для торговых точек GreenLeaf в Казахстане. Система объединяет функционал кассового аппарата (ККМ), учёта товаров, синхронизации нескольких устройств и автоматического выкупа через Playwright.

### 1.2 Технологический стек

| Компонент | Технология |
|-----------|-----------|
| Язык | Python 3.12+ |
| GUI | Tkinter / ttk |
| Локальная БД | SQLite (WAL mode) |
| Облачная БД | Supabase (PostgreSQL) |
| Синхронизация | Общая папка MEGA (JSONL-файлы, SyncEngine) |
| Бот-автоматизация | Playwright (Chromium) |
| Печать | ESC/POS (термопринтеры 58/80 мм) |
| Трей (фон) | pystray (Windows) |
| Шифрование | cryptography (Fernet) |
| Экспорт | pandas, openpyxl |

### 1.3 Текущая версия
**v3.10.64** (версионная схема — трёхкомпонентная: MAJOR.MINOR.PATCH)

### 1.4 Тип приложения
- Десктопное приложение (Tkinter-окно)
- Windows-only (трей, установщик, автозапуск)
- macOS/Linux — базовая поддержка без трея, без установщика

---

## 2. Архитектура приложения

### 2.1 Миксин-композиция (Mixin pattern)
`GreenLeafApp` (ui.py) собирается из миксов по одному на каждую вкладку:

```
GreenLeafApp (ui.py)
  ├── MainTabMixin          (ui_main_tab.py)     — Главная панель
  ├── POSTabMixin           (ui_pos.py)          — Касса
  ├── SalesTabMixin         (ui_sales.py)        — Продажи
  ├── ArrivalTabMixin       (ui_arrival.py)      — Приход/Склад
  ├── PartnersTabMixin      (ui_partners.py)     — Партнёры
  ├── AnalyticsTabMixin     (ui_analytics.py)    — Аналитика
  ├── AutoreviewMixin       (ui_autoreview.py)   — Автообзор
  ├── BotAutomationMixin    (ui_bot.py)          — Бот-автоматизация
  └── SettingsTabMixin      (ui_settings.py)     — Настройки
```

### 2.2 Поток запуска (code.py)

```
code.py (входная точка — скачивается из Supabase)
  │
  ├── 1. Проверка/установка pip-зависимостей
  │     (requests, pandas, supabase, playwright, ...)
  │
  ├── 2. Загрузка version.json из Supabase
  │     (с ETag-кэшированием)
  │
  ├── 3. Сравнение SHA256-хэшей модулей
  │     (скачиваются только изменённые модули)
  │
  ├── 4. exec() каждого модуля в память
  │     (types.ModuleType + exec — код не сохраняется на диск)
  │
  ├── 5. Настройка путей (settings.BASE_DIR, LOGS_DIR, ...)
  │
  ├── 6. Создание SQLite БД (если отсутствует)
  │
  ├── 7. Проверка лицензии (Supabase + 72h grace offline)
  │
  ├── 8. Аутентификация пользователя (AdminSetupWizard / UserLoginScreen)
  │
  └── 9. Запуск tkinter (GreenLeafApp) + фоновые потоки
```

### 2.3 Исходные файлы (в репозитории разработчика)

| Файл | Назначение |
|------|-----------|
| `code.py` | Входная точка / загрузчик |
| `ui.py` | Главный класс `GreenLeafApp`, трей, темы |
| `ui_settings.py` | Миксин настроек (принтер, синхронизация, пользователи) |
| `ui_pos.py` | Миксин POS-кассы |
| `ui_sales.py` | Миксин продаж и истории |
| `ui_arrival.py` | Миксин прихода товаров |
| `ui_partners.py` | Миксин партнёров |
| `ui_main_tab.py` | Миксин главной панели |
| `ui_analytics.py` | Миксин аналитики |
| `ui_autoreview.py` | Миксин автообзора |
| `ui_bot.py` | Миксин бот-автоматизации |
| `ui_dialogs.py` | Переиспользуемые диалоги (WaitingScreen, AutoScrollbar, ...) |
| `ui_lang.py` | Система локализации (RU/EN/KK) |
| `receipt_printer.py` | ESC/POS драйвер + генератор превью |
| `db.py` | Слой Supabase (лицензии, уведомления) |
| `db_sqlite.py` | Слой SQLite (DatabaseManager + все SQL manager'ы) |
| `market.py` | Реэкспорт SQL manager'ов из db_sqlite (обратная совместимость) |
| `settings.py` | Конфигурация приложения, пути, дефолты |
| `pvm_core.py` | Ядро Playwright-автоматизации + Email/Telegram-уведомления |
| `sync_engine.py` | Transport-agnostic движок синхронизации (SyncEngine → SyncQueue → Transport) |
| `sync_queue.py` | Очередь изменений с дедупликацией, ретраями и упорядочиванием |
| `sync_registry.py` | Регистрация бизнес-сущностей (goods, partners) для SyncEngine |
| `sync_setup_wizard.py` | Диалог настройки папки синхронизации |
| `sync_transport.py` | Абстрактный базовый класс SyncTransport (ABC) |
| `transport_local.py` | Реализация SyncTransport через локальную файловую систему |
| `deploy.py` | Скрипт деплоя (загрузка модулей в Supabase) |
| `install.py` | Установщик (обфускация, разброс фрагментов) |
| `version.json` | Манифест версий (хэши SHA256 всех модулей) |

### 2.4 Transport-Agnostic Sync Engine

```
          ┌──────────────┐
          │  SyncEngine  │  (sync_engine.py) — оркестрация
          └──────┬───────┘
                 │
          ┌──────▼───────┐
          │  SyncQueue   │  (sync_queue.py) — дедупликация + ретраи
          └──────┬───────┘
                 │
          ┌──────▼──────────┐
          │  SyncTransport  │  (sync_transport.py) — абстрактный интерфейс
          │  (ABC)          │
          └──────┬──────────┘
                 │
          ┌──────▼──────────┐
          │  LocalTransport │  (transport_local.py) — файловая система
          └─────────────────┘
```

- `SyncEngine` — транспортно-независимый движок: регистрация сущностей через `register_entity()`, работа с очередью, JSON Lines-формат
- `SyncQueue` — буфер между детектором изменений и транспортом: дедупликация (collaps одинаковых entity_type+entity_id), ретраи (до 5 попыток), упорядочивание по change_id
- `SyncTransport` — ABC с методами `connect/disconnect/upload/download/list_files/delete/move/exists/make_dir/stat/health`
- `LocalTransport` — реализация через локальную папку (используется для тестов и отладки)
- `sync_registry.py` — регистрация сущностей `goods` и `partners` с лямбдами `apply_insert/apply_update/apply_delete`
- `sync_setup_wizard.py` — ttk-диалог выбора папки синхронизации и диагностики (write/read/delete tests)

---

## 3. Система установки

Установка — двухэтапный пайплайн:

```
Этап 1: SystemConfig.bat (пользовательский — запуск с флешки)
  │
  ├── Проверка прав администратора (UAC)
  ├── Установка VC++ Redistributable 2015-2022
  ├── Поиск Python 3.12+ (реестр / py -3 / автозагрузка)
  ├── Установка pip-зависимостей
  ├── playwright install chromium
  ├── Запуск install.py
  ├── Создание ярлыка на рабочем столе
  └── Самоудаление
```

```
Этап 2: install.py (обфускатор — Python-скрипт)
  │
  ├── 1. Очистка старых файлов (предыдущие версии)
  ├── 2. Генерация мастер-ключа (XOR, 32 символа)
  ├── 3. Генерация Fernet-ключа (44-byte base64)
  ├── 4. Разбивка Supabase URL на 8 фрагментов
  ├── 5. Разбивка Supabase API Key на 4 фрагмента
  ├── 6. Шифрование фрагментов XOR-ом и запись в файлы
  │     (пути: %LOCALAPPDATA%\Microsoft\Office\Spw\... и т.д.)
  ├── 7. Создание XOR-зашифрованного индекс-файла:
  │     %LOCALAPPDATA%\Microsoft\Office\SmartBridge\office_cache.bin
  ├── 8. Генерация лаунчера (outlook_telemetry.pyw):
  │     - Читает индекс, дешифрует фрагменты
  │     - Собирает Supabase URL + Key
  │     - Скачивает code.py из Supabase и exec()
  │     - Офлайн-кэш через Fernet
  ├── 9. Создание декоев (фейковые файлы в PVMGroup\PVM.core\)
  └── 10. Настройка автозапуска, проверка (PVM_DEBUG=1)
```

---

## 4. Файловая структура на клиенте

Все файлы размещаются в `%LOCALAPPDATA%\Microsoft\*` под видом системных компонентов Office/Windows.

### 4.1 Путь запуска

| Файл | Путь | Назначение |
|------|------|-----------|
| Лаунчер | `...\Microsoft\Office\SmartBridge\outlook_telemetry.pyw` | Стартовый скрипт (pythonw) |
| Ярлык | `%USERPROFILE%\Desktop\PVM.core.lnk` | Ярлык → лаунчер через pythonw |

### 4.2 Фрагменты URL Supabase (8 штук)

| ID | Путь |
|----|------|
| url1 | `...\Microsoft\Office\Spw\spw0000.osd` |
| url2 | `...\Microsoft\Office\OTele\telemetry.otel` |
| url3 | `...\Microsoft\Windows\SettingSync\metastore\settingsync_meta.db` |
| url4 | `...\Microsoft\Windows\Ringtones\metadata.mta` |
| url5 | `...\Microsoft\InputPersonalization\TextHarvester\WaitList.dat` |
| url6 | `...\Microsoft\Windows Security\Logs\Operational.evtx` |
| url7 | `...\Microsoft\Edge\Recovery\Recovery.dat` |
| url8 | `...\Microsoft\Windows Mail\Stationery\Compose.hdr` |

### 4.3 Фрагменты API-ключа Supabase (4 штуки)

| ID | Путь |
|----|------|
| key1 | `...\Microsoft\Feeds\Cache\~Feeds{3A42F}.tmp` |
| key2 | `...\Microsoft\Windows Photo Viewer\PhotoAcq.log` |
| key3 | `...\Microsoft\GameDVR\GameDVR.etl` |
| key4 | `...\Microsoft\MSOIdentityCRL\Tracing\TokenBroker.log` |

### 4.4 Ключи и конфигурация

| Файл | Путь | Содержимое |
|------|------|-----------|
| Индекс | `...\SmartBridge\office_cache.bin` | XOR-зашифрованная карта фрагментов |
| Fernet ключ | `...\Microsoft\Crypto\RSA\MachineKeys\container.p12` | Ключ для шифрования кэша |
| Device key | `...\Microsoft\Vault\UserData\vpnconfig.dat` | Уникальный ключ устройства |
| Креды | `...\Microsoft\Vault\UserData\credcache.dat` | Локально-кэшированные учётные данные |
| Настройки | `...\Microsoft\Vault\UserData\AadTokenBroker.db` | Конфигурация приложения |

### 4.5 Кэш модулей и БД

| Файл | Путь |
|------|------|
| БД SQLite | `...\Microsoft\WindowsApps\RuntimeBroker\cache\pvmcore.db` |
| Кэш модулей | `...\Microsoft\WindowsApps\RuntimeBroker\cache\modules\*.bin` |
| Конфиг | `...\Microsoft\WindowsApps\RuntimeBroker\cache\_cfg.bin` |
| Очередь загрузок | `...\Microsoft\WindowsApps\RuntimeBroker\cache\_uq.bin` |
| Прогресс | `...\Microsoft\WindowsApps\RuntimeBroker\cache\_prg.bin` |
| Версия | `...\Microsoft\WindowsApps\RuntimeBroker\cache\_vrs.bin` |

### 4.6 Декои — фейковые файлы (2-3 на каждый реальный)

| Путь | Тип |
|------|-----|
| `...\Microsoft\CLR_v4.0\UsageLogs\` | Папка с .log, .bak, .xml — случайные бинарные данные |
| `...\SmartBridge\UsageLogs\UsageLogs\` | Суб-папка логов |
| `%LOCALAPPDATA%\PVMGroup\PVM.core\main.py` | Фейковый entry point |
| `%LOCALAPPDATA%\PVMGroup\PVM.core\pvmcore.dll` | Фейковая DLL |
| `%LOCALAPPDATA%\PVMGroup\PVM.core\crypto.dll` | Фейковая DLL |
| `%LOCALAPPDATA%\PVMGroup\PVM.core\license.dll` | Фейковая DLL |

### 4.7 Автозапуск

| Файл | Путь |
|------|------|
| Ярлык автозагрузки | `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\PVM.core.lnk` |

### 4.8 Удаление

`Uninstall.bat` — очищает все перечисленные выше пути, удаляет папки, ярлыки, автозапуск.

---

## 5. Система обновления

### 5.1 Многоуровневая проверка версий

**Уровень A — code.py (каждый запуск):**
- Скачивает `version.json` из Supabase Storage с ETag-кэшированием
- Сравнивает SHA256 хэши каждого модуля с локальным кэшем
- Скачивает только изменившиеся модули
- Интервал проверки: 1 час

**Уровень B — ui.py (фоновый):**
- `fetch_remote_version()` — асинхронный HTTP-запрос к Supabase
- Сравнение семантических версий: `(3,9,78) > (3,9,77)`
- Выставляет флаг `UPDATE_AVAILABLE`

**Уровень C — ui.py (попап):**
- При изменении версии — показ диалога "Что нового" (UpdateNotificationDialog)
- Отложенный запуск: 2 секунды после старта GUI

### 5.2 Механизм самообновления

При нажатии кнопки "Update":
1. Создаётся временный `pvm_updater.bat`
2. Скачивается свежий `SystemConfig.bat` с GitHub
3. Текущее приложение закрывается
4. Запускается полная переустановка

### 5.3 Структура version.json

```json
{
  "version": "3.10.64",
  "last_updated": "2026-07-20T14:49:19+00:00Z",
  "modules": {
    "ui_settings.py": { "hash": "a24c0006...", "size": 166439 },
    "ui.py": { "hash": "cc344575...", "size": 175939 },
    ...
  },
  "cache_paths": {
    "settings.py": "%LOCALAPPDATA%\\Microsoft\\Edge\\User Data\\ShaderCache",
    ...
  }
}
```

---

## 6. Облачная архитектура (Supabase)

### 6.1 Роль Supabase
Supabase используется как:
- Реестр устройств и лицензий
- Хранилище файлов (Storage): version.json + модули .py
- Система уведомлений (notifications)
- Реестр сессий бот-аналитики

### 6.2 Таблицы Supabase

| Таблица | Назначение | Ключевые колонки |
|---------|-----------|------------------|
| `users` | Реестр устройств + лицензии | `device_key` (PK), `login`, `status`, `activation_end`, `subscription_level` |
| `notifications` | Системные уведомления | `notification_type`, `target_device`, `is_active`, `color_status` |
| `session_history` | Сессии бот-аналитики | `device_key`, `session_date`, `total_orders`, `total_sales` |

> Облачный релей (pvm_sync_* таблицы) удалён: синхронизация идёт только через общую папку MEGA.

## 7. Синхронизация устройств

### 7.1 Топология

```
                ┌───────────────────────────────┐
                │   Общая папка MEGA (P MEGA Sync)│
                │   outbox/*.jsonl — изменения   │
                │   snapshots/*.jsonl — снимки   │
                └───────────────┬───────────────┘
                 ┌──────────────┴──────────────┐
        ┌────────▼────────┐          ┌─────────▼─────────┐
        │  CASHIER (Касса) │          │  WAREHOUSE (Склад) │
        │  SQLite (локальн.)│          │  SQLite (локальн.) │
        │  SyncEngine        │          │  SyncEngine         │
        └──────────────────┘          └───────────────────┘
```

Все устройства магазина синхронизируют **одну и ту же папку** в одном аккаунте MEGA (2 устройства на магазин). Движок пишет изменения в `outbox/` и читает изменения других устройств; конфликты решаются по `updated_at` (LWW).

### 7.2 Типы устройств (функциональные)

| device_type | Описание |
|-------------|---------|
| `cashier` | Касса. Продажи, возвраты. Владеет полем `goods.quantity` (остатки не перезаписываются с других устройств). |
| `warehouse` | Склад. Приходы, списания, инвентаризации, редактирование каталога/партнёров. Продажи заблокированы в UI. |

### 7.3 Transport-Agnostic Sync Engine

Folder-based синхронизация через общую папку MEGA:

- **SyncEngine** (`sync_engine.py`) — оркестратор: регистрирует сущности, управляет очередью, сериализует изменения в JSON Lines
- **SyncQueue** (`sync_queue.py`) — буфер на базе `sync_log` с дедупликацией (collaps множественных изменений одной сущности) и упорядочиванием
- **SyncTransport** (`sync_transport.py`) — ABC с методами `connect/download/upload/list_files/delete/stat/health`
- **LocalFolderTransport** (`transport_local.py`) — реализация через локальную папку (синхронизируется MEGA)
- **sync_registry.py** — регистрирует сущности `goods`, `partners`, `receipts` с apply-обработчиками
- **sync_setup_wizard.py** — визард выбора папки и диагностики (Настройки → Синхронизация → «⚙ Настроить»)

### 7.4 Протокол (JSONL-файлы в папке MEGA)

```
outbox/{device_key}_{ts}_{uuid}.jsonl      — пакет изменений (JSONL)
snapshots/snapshot_{entity}_{ts}.jsonl     — полный снимок каталога (еженедельно)
```

- строка 0 — манифест (`__manifest__`: type, source_device, generated_at)
- строки 1..N — изменения `{entity, operation, entity_id, updated_at, data}`

Цикл `sync_once()`:
1. **Flush** — несинхронизированные строки `sync_log` (+ чеки) сериализуются в `outbox/`-файл; пометка `synced` только после успешной загрузки
2. **Consume** — чтение чужих `outbox/`-файлов, применение изменений (LWW)
3. **Snapshots** — раз в 7 дней полные снимки goods/partners; применяются устройствами после первого подключения
4. **Janitor** — удаление outbox старше 14 дней, хранение 2 последних снимков на сущность

Безопасность: атомарная запись (tmp+rename), потребители никогда не удаляют чужие файлы, идемпотентность через `sync_applied_files` (реестр применённых файлов).

### 7.5 Конфликты

Единственная стратегия — **Last-Write-Wins** по `updated_at` (аналог `merge`). Исключение: `goods.quantity` на кассе не перезаписывается входящими изменениями (`preserve_quantity`).

### 7.6 Офлайн-режим

- **Лицензия**: 72-часовой грейс-период после последнего подключения к Supabase.
- **Анти-откат часов**: отрицательные дельты времени блокируются.
- **Синхронизация**: работает офлайн, пока MEGA синхронизирует папку (MEGA сама накапливает изменения и применяет их при появлении сети).
- **Экспоненциальный backoff**: 30с → 70с → 150с ... сброс до 5с при успехе.
- **Upload queue**: неудачные загрузки сессий сохраняются в `cache/_uq.bin` (XOR), до 14 ретраев.

## 8. Базы данных

### 8.1 Локальная SQLite (`db_sqlite.py`)

Файл БД: `%BASE_DIR%/cache/pvmcore.db`
Режим: WAL, thread-local соединения, foreign keys.

**Бизнес-таблицы:**

| Таблица | PK | Назначение |
|---------|----|-----------|
| `goods` | `id` (TEXT, md5) | Каталог товаров. `code` UNIQUE. `synced`, `is_deleted`, `updated_at` |
| `partners` | `id` (TEXT) | Справочник партнёров/клиентов. `synced` |
| `receipts` | `id` (TEXT: `{PREFIX}-{YYYY-MM-DD}-{NNNNN}`) | Чеки продаж. `status`: completed/refunded |
| `receipt_items` | `id` (INTEGER AI) | Строки чеков. FK → `receipts(id)` |
| `purchases` | `id` (TEXT) | Приходы/накладные |
| `purchase_items` | `id` (INTEGER AI) | Строки приходов |
| `writeoffs` | `id` (TEXT) | Списания |
| `writeoff_items` | `id` (INTEGER AI) | Строки списаний |
| `inventory_audits` | `id` (TEXT) | Инвентаризации |
| `inventory_audit_items` | `id` (INTEGER AI) | Строки инвентаризаций |

**Служебные таблицы:**

| Таблица | Назначение |
|---------|-----------|
| `partners_history` | Аудит изменений партнёров |
| `cancelled_items` | Лог отменённых позиций |
| `receipt_refund_logs` | Детальные логи возвратов |
| `quick_items` | 20 быстрых кнопок POS (slot_index 0-19) |
| `app_users` | Пользователи (username, role, PIN hash, permissions JSON) |
| `autoreview_sessions` | Сессии автообзора |

**Таблицы синхронизации:**

| Таблица | Назначение |
|---------|-----------|
| `sync_markers` | Key-value для watermarks (`last_goods_sync`, `wipe_goods`, ...) |
| `sync_log` | Транзакционный ченджлог — `entity_type`, `entity_id`, `operation`, `data` (JSON), `created_at` |
| `sync_applied_files` | Реестр применённых файлов (идемпотентность folder-based sync) |

SQLite-триггеры на бизнес-таблицах автоматически пишут изменения в `sync_log` при INSERT/UPDATE/DELETE.

### 8.2 Supabase (PostgreSQL)

**Релейных таблиц нет** — облако используется только для лицензий, уведомлений и обновлений (см. раздел 6).

### 8.3 Ключи синхронизации (Watermarks / Sync Markers)

Хранятся в `sync_markers` как ключ-значение:

| Ключ | Тип | Назначение |
|------|-----|-----------|
| `last_goods_sync` | timestamp | Последний успешный пуш товаров |
| `last_partners_sync` | timestamp | Последний успешный пуш партнёров |
| `last_receipts_sync` | timestamp | Последний успешный пуш чеков |
| `wipe_goods` | boolean | Глобальная перезапись товаров |
| `cloud_master_pull_goods_edit` | timestamp | Cloud-only метки pull |

Маркеры инициализируются текущим временем (`now()`) при первом использовании, чтобы избежать сканирования всей истории.

---

## 9. Функциональные модули (вкладки)

### 9.1 POS — Касса (`ui_pos.py`)
- Корзина покупок
- Поиск товаров по штрихкоду
- 20 быстрых кнопок (quick_items)
- Выбор партнёра
- Оплата: наличные / карта / внутренний счёт
- Сдача
- Печать чека (58/80 мм)
- Автоматическая печать

### 9.2 Продажи (`ui_sales.py`)
- История продаж с фильтрацией по датам
- Детали чека (3 вкладки: данные, PDF, Чек)
- Возвраты
- Отчёты продавцов
- Экспорт в Excel
- Перепечать чека

### 9.3 Приход / Склад (`ui_arrival.py`)
- Создание накладных (приход)
- Управление списком товаров
- Списание товаров
- Отменённые позиции
- Инвентаризация
- Аудит склада
- Экспорт инвойсов

### 9.4 Партнёры (`ui_partners.py`)
- Список партнёров
- Добавление / редактирование
- История партнёра
- Блокировка партнёра

### 9.5 Аналитика (`ui_analytics.py`)
- Статистика продаж (подписка 2+)
- PV Bot аналитика (подписка 3+)
- Парсинг локальных .dat-файлов с сессиями
- Сводная статистика: всего сессий/заказов/продаж,成功率, avg/день
- График за 7 дней
- Топ товаров и продуктов по продажам
- Топ клиентов по тратам
- Отслеживание неудачных позиций и недостатка средств
- Прогноз на завтра (среднее по тому же дню недели)
- Система алертов (мало заказов, недоступные товары)

### 9.6 Бот-автоматизация (`ui_bot.py`)
- **Step 1**: Playwright — автоматический заказ на сайте GreenLeaf
- **Step 2**: Сверка данных заказа с локальной БД
- Планировщик (scheduler) по времени
- Управление сессиями и OTP
- Логирование и прогресс

### 9.7 Автообзор (`ui_autoreview.py`)
- Playwright — автоматический сбор данных о товарах с сайта GreenLeaf
- Синхронизация с локальной БД
- Реконсиляция остатков

### 9.8 Настройки (`ui_settings.py`)
Вкладки настроек (боковое меню):
- **Главная** — статус, уведомления, операционный лог
- **Внешний вид** — язык, тема, масштаб
- **Принтер / Чек** — реквизиты, редактор блоков, предпросмотр, автопечать
- **Автоматизация** — планировщик, watch directory (мониторинг папки с .xlsx)
- **Пользователи** — управление правами
- **Система** — синхронизация, роли, пиринг, база данных
- **Синхронизация БД** — управление облачной синхронизацией

### 9.9 Главная (`ui_main_tab.py`)
- Статус активации
- Панель уведомлений
- Операционный лог
- Кнопки Старт / Стоп (бот-автоматизация)

---

## 10. Фоновый режим

### 10.1 System Tray (Windows only)
- Библиотека: `pystray`
- Условный импорт: `TRAY_AVAILABLE` — только Windows, только при наличии pystray

**Состояния иконки:**

| Состояние | Цвет |
|-----------|------|
| `ready` | Зелёный (#4CAF50) |
| `working` | Фиолетовый (#9C27B0) |
| `error` | Красный (#f44336) |
| `paused` | Серый (#9E9E9E) |

**Контекстное меню:**
```
PVM.core (заголовок)
─────────────
▶️ Запустить
⏹ Остановить
─────────────
📊 Открыть
⚙️ Настройки
─────────────
❌ Выход
```

### 10.2 Минимизация в трей

- Windows: кнопка закрытия (`WM_DELETE_WINDOW`) → `master.withdraw()` (скрыть в трей)
- `tray_icon.notify("PVM.core свёрнут в трей")`
- macOS/Linux: обычное закрытие (`master.destroy()`)

### 10.3 Фоновые сервисы

| Сервис | Период | Описание |
|--------|--------|---------|
| SyncEngine | 10-30 с | Цикл folder-based sync: flush outbox → consume чужих файлов → снимки → janitor |
| Update Checker | 1 час | ETag-запрос version.json из Supabase |
| Watch Directory | По расписанию | Мониторинг папки на .xlsx файлы |

---

## 11. Безопасность и обфускация

### 11.1 Три слоя защиты

**Слой 1 — XOR Fragment Mapping:**
- Индексный файл (`office_cache.bin`) хранит пути ко всем фрагментам
- Зашифрован multi-key XOR с мастер-ключом (32 символа)
- Формула: `bytes([d[i] ^ k[i % len(k)] for i in range(len(d))])`
- Защита: одиночный `.osd`-файл бесполезен без индекса

**Слой 2 — Caesar Shifting (+3):**
- Для сокрытия строк (Supabase URL, ключи) в лаунчере
- Пример: `http` → `kwws`
- Дешифровка: `chr(ord(c) - 3)`

**Слой 3 — In-Memory Execution:**
- Модули загружаются через `types.ModuleType` + `exec()`
- **Никакой читаемый код не сохраняется на диск**
- Локальный кэш — Fernet-зашифрованные `.bin` файлы

### 11.2 Декои (приманки)

На каждый реальный фрагмент создаётся 2-3 декоя:
- Случайные имена: `clr_v4.5_x64.log`, `metahost_cache.bak`, `mscordbi.xml`
- Содержимое: `os.urandom()` — случайные байты
- Эффект: папка `SmartBridge` выглядит как мусор из системных кэшей

### 11.3 Лицензирование

- Проверка лицензии при каждом запуске (Supabase `users` table)
- Офлайн-грейс: 72 часа после последнего успешного подключения к Supabase
- Анти-откат времени: отрицательные дельты между запусками блокируются
- Device key генерируется из MAC-адреса + hostname → SHA256 → 16 hex-символов
- Кэш учётных данных после первого успешного подключения (файл `credcache.dat`)
- Upload queue: при неудаче сессии сохраняются в `_uq.bin` (XOR), до 14 ретраев
- `max_devices` — лимит secondary-устройств, задаётся в Supabase, проверяется при пиринге

---

## 12. Локализация

### 12.1 Система переводов (`ui_lang.py`)

Поддерживаемые языки:
- **RU** — Русский (по умолчанию)
- **EN** — Английский
- **KK** — Казахский

Структура:
```python
TRANSLATIONS = {
    'en': {
        'pos_tab': 'POS',
        'sales_tab': 'Sales',
        ...
    },
    'ru': { ... },
    'kk': { ... }
}

def get_text(key, lang='ru'):
    return TRANSLATIONS.get(lang, {}).get(key, key)
```

`MODULE_VERSION` (строка версии) также хранится в `ui_lang.py` для доступа из всех модулей.

### 12.2 Настройка языка
- Вкладка "Внешний вид" → выбор языка
- Перезагрузка интерфейса при смене языка

---

## 13. Деплой (deploy.py)

### 13.1 Назначение
Скрипт для разработчика: загружает изменённые модули в Supabase Storage.

### 13.2 Использование
```bash
python3 deploy.py --version 3.10.64
```

### 13.3 Pipeline

```
1. Обновление version-строк в исходных файлах
   (ui.py, code.py, settings.py, ui_lang.py)

2. Генерация version.json
   (SHA256 хэши + размеры для всех модулей)

3. Сравнение с текущим version.json на Supabase

4. Загрузка только изменённых модулей
   (бакет: backend/, upsert: true)

5. Загрузка version.json (только после успеха всех модулей)
```

### 13.4 Флаги

| Флаг | Описание |
|------|---------|
| `--version X.Y.Z` | Версия для деплоя (обязательный) |
| `--skip-modules` | Обновить только version.json без загрузки модулей |
| `--tech-works` | Включить режим технических работ (уведомление) |
| `--no-tech-works` | Выключить режим технических работ |

### 13.5 Требования

```bash
export PVM_SUPABASE_URL="https://your-project.supabase.co/"
export PVM_SUPABASE_KEY="your-service-role-key"
```

---

## 14. Лицензирование и роли

### 14.1 Уровни подписки (subscription_level)

| Уровень | Описание |
|---------|----------|
| 1 | Базовая касса (POS + Продажи + Склад) |
| 2 | + Аналитика + синхронизация |
| 3 | + Бот-автоматизация + Telegram/Email |
| 4 | Полный пакет (всё включено) |

### 14.2 Система пользователей (`app_users`)

- **admin** — полные права
- **cashier** — касса, продажи
- **manager** — склад, партнёры, аналитика
- **readonly** — только просмотр

Аутентификация: PIN-код (4-6 цифр).
Первый запуск: `AdminSetupWizard` (создание admin).
Последующие: `UserLoginScreen` (PIN-логин).

### 14.3 Device Key

- Генерация: MAC-адрес + hostname → SHA256 → 16 hex символов
- Хранение: настройки (`_cfg.bin`) + файл + OS-specific fallback
- Используется как идентификатор устройства в системе лицензирования и синхронизации

### 14.4 Ограничения по подписке

- `max_devices` — максимальное количество Secondary-устройств (задаётся в Supabase)
- Уровень подписки проверяется при каждом запуске и при попытке доступа к функциям

---

## Приложение A: Карта файлов установки

```
%LOCALAPPDATA%\Microsoft\
├── Office\
│   ├── SmartBridge\
│   │   ├── outlook_telemetry.pyw    [ЛАУНЧЕР]
│   │   ├── office_cache.bin         [XOR-ИНДЕКС]
│   │   ├── office_net.dat           [ОБФУСЦИРОВАННЫЙ КОД]
│   │   ├── *.osd / *.xml            [ФРАГМЕНТЫ]
│   │   ├── *.bak / *.log            [ДЕКОИ]
│   │   └── v4.0\                    [СКРИПТЫ]
│   ├── Spw\spw0000.osd              [URL фрагмент 1]
│   └── OTele\telemetry.otel         [URL фрагмент 2]
│
├── Windows\
│   ├── SettingSync\metastore\       [URL фрагмент 3]
│   ├── Ringtones\                   [URL фрагмент 4]
│   └── ...
│
├── WindowsApps\RuntimeBroker\cache\
│   ├── pvmcore.db                   [SQLite БД]
│   ├── modules\                     [Fernet-кэш модулей]
│   ├── _cfg.bin                     [Настройки]
│   ├── _uq.bin                      [Очередь загрузок]
│   ├── _prg.bin                     [Прогресс]
│   └── _vrs.bin                     [Кэш версии]
│
└── [другие Microsoft-пути...]        [Остальные фрагменты + ключи]
```

---

## Приложение B: Архитектура синхронизации (диаграмма данных)

```
                    ┌─────────────────────────────────┐
                    │   Общая папка MEGA (P MEGA Sync)  │
                    │   outbox/*.jsonl   snapshots/*.jsonl │
                    └──────────────┬──────────────────┘
                       ┌───────────┴───────────┐
           ┌───────────▼──────────┐   ┌─────────▼───────────┐
           │   CASHIER (Касса)    │   │  WAREHOUSE (Склад)  │
           │                      │   │                      │
           │  goods.quantity ◄────┼───┼─┐  purchases ────────┤
           │      (authorit.)     │   │ │  writeoffs ────────┤
           │  catalog (LWW) ──────┼───┼─┤  audits ───────────┤
           │  receipts ───────►───┤   │ │  goods (preserve   │
           │                      │   │ │  quantity)         │
           │  SyncEngine:         │   │ │  SyncEngine:       │
           │  flush → consume →  │   │ │  flush → consume → │
           │  snapshots → janitor │   │ │  snapshots → janitor│
           └──────────────────────┘   └──────────────────────┘
```

---

## Приложение C: Используемые pip-зависимости

```
requests
pandas
supabase
playwright
openpyxl
pystray          (Windows only)
Pillow
cryptography
tkcalendar
pydantic
pywin32          (Windows only)
python-dotenv
```

---

*Документ составлен на основе анализа исходного кода PVM.core v3.10.64. Июль 2026.*
