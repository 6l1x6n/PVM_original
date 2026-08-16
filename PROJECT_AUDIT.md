# Полный технический аудит PVM.core — v3.11.76

Дата аудита: 09.08.2026
Фактическая версия manifest: 3.11.76 (version.json, файлы от 08.08.2026)

## Статус аудита

Аудит выполнен по исходному коду, конфигурации, SQL, документации, локальной SQLite-БД,
установщику, деплою и фоновой автоматизации. Проверено: 28 Python-файлов (~30 000 строк),
6 MD-файлов, 10 SQL-скриптов, `version.json`, SQLite-схема, AST-проверка всех файлов
(синтаксических ошибок нет), хэши модулей сверены с manifest.

Полный импорт UI в текущем macOS-окружении не выполнялся: отсутствует `tkcalendar`.

Git-baseline отсутствует (ветка без коммитов) — история изменений восстановлению не подлежит.

> Документ переписан под v3.11.76 с новой структурой по 4 частям:
> 1. Безопасность · 2. Функционал · 3. Supabase и БД/облако · 4. Визуал.
> Статусы проблем C/H/M обновлены относительно аудита v3.11.89.

## Как программа реально работает

1. Windows-инсталлятор устанавливает зависимости и запускает `install.py`.
2. `install.py` создаёт фрагменты конфигурации, лаунчер и кэш под именами системных каталогов Windows.
3. Лаунчер скачивает `code.py` из Supabase Storage и выполняет его через `exec`.
4. `code.py` проверяет зависимости, загружает `version.json`, скачивает отсутствующие или изменённые модули и выполняет их в памяти.
5. Создаётся SQLite-БД (при повреждении — backup + ошибка, НЕ удаление), затем лицензирование через Supabase.
6. Загружаются GreenLeaf-учётные данные, выполняется локальная PIN-аутентификация (OTP — после 3 ошибок).
7. Запускается Tkinter `GreenLeafApp` (maximized): планировщик, SyncEngine, Live Bot, Autoreview, heartbeat, уведомления.
8. POS, склад, возвраты и ревизии пишут через атомарные `InventoryOpsManagerSQL`-операции; изменения — в JSONL-файлы MEGA-папки (sync v2).
9. PV Bot пишет локальные `.dat`-логи и отправляет итог сессии в `session_history` Supabase.
10. При закрытии: `_stop_bot_process` → `_stop_all_workers` (стоп-ивенты + join) → exit-report (join 10с) → destroy.

---

# Часть 1. Безопасность

## Лицензирование и auth — ЧАСТИЧНО (H1 не исправлен)

- HMAC-секрет захардкожен в клиенте: `settings.py:324`.
- Vault — обычный JSON `lic_vault.dat` (путь маскируется под `Microsoft\SystemCertificates\My\CRLs`).
- `subscription_level` default = 4 (`settings.py:319`, `db.py:211,296,474`).
- Fail-open: при сетевой ошибке grace-период 72ч (`code.py:786-793`, анти-тампер есть);
  legacy-путь сам продлевает истёкшую лицензию на месяц (`db.py:336-360`).
- `max_devices` возвращается, но нигде не проверяется.
- Регистрация устройства шлёт login/password в `users`; GreenLeaf-пароль кэшируется в настройки (`db.py:83`).

## PIN и OTP

- **H7 ИСПРАВЛЕН (09.08.2026)**: `OTPManager` — генерация `secrets.randbelow(10)`,
  привязка кода к имени пользователя, TTL 10 минут, лимит 5 попыток (после — аннулирование);
  флоу ui_dialogs.py: неверный PIN больше не сохраняется (`saved_pin` удалён), успешный OTP =
  вход без повторной проверки PIN (OTP — recovery-фактор владения почтой/Telegram).
- Осталось: PIN хранится несолёным `sha256(pin)` (`db_sqlite.py:3480`) — 10К комбинаций,
  брутфорс тривиален; миграция на соль не делалась (сломает существующие хэши).
- `create_user` позволяет роль `superadmin` без проверок (`db_sqlite.py:3514`); `reset_pin`
  без ограничений — меняет PIN суперадмина. UI-заглушки есть (`ui_settings.py:2686,2713`),
  но менеджер не защищён.

## Хранение секретов — ОТСУТСТВУЕТ (H13)

- GreenLeaf creds: plain JSON/Base64 в settings (`install.py:197-214`, `db.py:83`).
- SMTP-пароль и TG-токен: plain JSON (`settings.py:70-84`), попадают в бэкап-экспорт.
- **`install.py:154` — полный Supabase anon JWT захардкожен в исходнике** (фрагментирован:
  b64/hex/XOR^7/сдвиг+5); project ref `kjndukfmrapsmpzwmvw` (`install.py:131`).
- Обфускация: XOR `MASTER_KEY`, Caesar ±3, in-memory exec, декои.
- **«пример лога.txt» содержит реальный login (`s240534`) и device key** — необезличенный
  operational log в репозитории; ротировать устройство и убрать/обезличить файл.

## Update pipeline — ОТСУТСТВУЕТ (C5, H14)

- Unsigned RCE: `code.py:448-470` скачивает модуль при расхождении хэша с НЕподписанным
  `version.json`; `exec` — `code.py:300,514`, `install.py:322,334`.
- `save_local_version_json` определён (`code.py:232-253`) и передан в namespace, но НИГДЕ не вызывается.
- Offline bootstrap сломан: `install.py:329` ищет `97a764485014.dat`, а
  `_cache_filename("code.py")` = `9d3abcd877c2` — не совпадает.
- `deploy.py:214-218` — remove-перед-upload без rollback; `--skip-modules` публикует manifest
  со ссылками на несуществующие удалённые модули; `deploy.py:138` — двойной таймзона-суффикс (`+00:00Z`).
- **C6 не исправлен**: `Uninstall.bat` удаляет реальные системные каталоги
  (`Edge\Recovery`, `Windows Security\Logs`, `Feeds\Cache`, `GameDVR`, `RuntimeBroker`);
  маскировка `install.py:66-106`.

## Права — ЧАСТИЧНО (H6)

- `has_permission()`: admin/superadmin всегда True (`ui.py:2593-2599`).
- Проверки в основном на кнопках: `finalize_invoice` (ui_arrival.py) вызывается по Enter
  через `ui.py:2830-2834` без проверки права внутри; `save_partner` без `partner_create`;
  `reset_pin` в менеджере без role-проверки.
- Известные обходы: создание товаров/партнёров и оплата через shortcuts.

## SQL-инъекции — ОК

Значения везде параметризованы; f-string SQL только для идентификаторов из хардкод-списков
(миграции) и LIKE с параметрами. Инъекций пользовательских данных не найдено.

## Что исправлено (C1, C3 — из аудита v3.11.89)

- **C1 ИСПРАВЛЕН**: auto-delete БД убран — backup + `RuntimeError` (`db_sqlite.py:29-48`),
  single-instance lock до инициализации (`code.py:683-687`).
- **C3 ЧАСТИЧНО → дополнен 09.08.2026**: `_stop_events` (ui.py:431-437), `_stop_all_workers`
  (join ≤3с), `_ui_queue_pump`; **SyncEngine подключён к координатору** — `stop()` + выход
  `sync_once` между стадиями + отслеживаемые sync-потоки (`_run_sync_cycle`); exit-report
  перенесён ПОСЛЕ остановки воркеров и джойнится (10с).

---

# Часть 2. Функционал

## Инвентарь вкладок (ui.py:3578-3661)

Касса → Продажи → Склад/Накладная → История → Партнёры → Аналитика → Настройки.
Отдельной «Главной» вкладки нет. Внутри Аналитики — вложенный notebook (ui_analytics.py:20-101):
📈 Бизнес-аналитика, 📊 Статистика, 🤖 PV Бот, 🔄 Автоскладирование (гейтинг правами/подпиской).
Склад: Накладная/Товары/Списание/Ревизия; История: Поступления/Отмены.
Настройки (ui_settings.py:149-209): Главная, Внешний вид, Принтер и Чек, Автоматизация,
Пользователи и права, Интеграции, Система, База данных (гейтинг `_apply_settings_permissions`).

## PV Бот

- `_run_step1`/`_run_step2`, `_process_unpaid_order`, `_check_insufficient_funds`,
  `_wait_visible` (поллинг 0.4с, stop-aware), resume-диалог, `completed_ids` при clear_logs.
- `stop_processing`: только `stop_event.set()` + join 3с (fallback-закрытия через 1.5с НЕТ).
- Планировщик `scheduler_loop` (ui_bot.py:87-181): **in-memory** `last_run_date` +
  `initial_check` + `config_generation` + `_scheduler_generation`; `cached_*` для потокобезопасности;
  автозагрузка чеков `download_todays_receipts`.
- **Persistent-маркер `cache/pvbot_last_run.json` в коде НЕ используется** (файл — сирота).

## Атомарные операции

- `InventoryOpsManagerSQL` (db_sqlite.py:2975+): `sale`/`purchase`/`refund`/`writeoff` —
  документ + остатки + партнёр в одном соединении, `_check_stock`, rollback.
- Вызовы: ui_pos.py:832, ui_arrival.py:490/1142, ui_sales.py:918.

## Модальные окна / поиск / цены / смена пользователя

- `create_modal_dialog(title, 600, 450, scrollable=True, dismiss_on_outside=False)` —
  `transient`+`grab_set`+`lift()` (без `-topmost`); `ask_float_dialog`/`ask_string_dialog`;
  `simpledialog` импортирован (ui_pos.py:12, ui_arrival.py:11), но не используется.
- Единый поиск: `_current_search_input()`, `_focus_search_field()`, `on_global_keypress` + guard.
- Цены: `int(round())` в `add_good`; `format_amount`/`fmt_num`; `_validate_int_input`.
- Смена пользователя: `request_switch_user` → `_stop_all_workers` → destroy → цикл сессий code.py.

## Уведомления

- `db.fetch_notifications()` — один раз на процесс (`_notifications_fetched_once`, db.py:917-920).
- **`db.reset_notifications_cache()` НЕ существует** — кэш не сбрасывается при смене пользователя
  (известное ограничение, задокументировано в AGENTS.md).

## Синхронизация (протокол v2, C2 ИСПРАВЛЕН)

- MEGA-фолдер — единственный путь; `sync_once`: flush outbox → consume → snapshots → apply → janitor.
- `sync_inbox` + `event_id` (INSERT OR IGNORE), apply ≤10 попыток со stale-маркировкой;
  JSONL манифест+checksum, битые файлы отбраковываются целиком; tombstones; LWW по `updated_at`
  (datetime, Z→+00:00); 6 сущностей (goods, partners, receipts, purchases, writeoffs, audits);
  janitor: outbox 35 дней, snapshots keep=2 (>45 дней).
- Остатки: касса владеет `goods.quantity`; ready-marker отсутствует.

## Прочее

- Live Bot — daemon-поток с poison pill; IntegrationBot — Telegram `/today`/`/stats` (receipts_manager).
- Dead code: `app_old.ico`, `simpledialog`-импорты, `_uq.bin` — очередь живая (ретраи <14).
- `market.py` — рабочий facade (не мёртвый): импортируется ui.py:43-44, `ui.py:573`.
- Локализация: `TRANSLATIONS` ru/en; отсутствуют ключи `error`, `warning`, `export`,
  `insufficient_funds`; часть строк захардкожена по-русски (диалоги, вкладки Аналитики).
- Печать: CP866-транслитерация (`_encode`, `CP866_TEXT_MAP`), KKM-реквизиты (ИИН/БИН),
  телефон партнёра, ширина 58/80мм, text_scale/item_layout.

---

# Часть 3. Supabase и БД/облако

## Таблицы, реально используемые клиентом (db.py)

| Таблица | Операции | Примечание |
|---|---|---|
| `users` | SELECT/INSERT/UPDATE | heartbeat `last_seen` раз в 2ч + проверка лицензии (2 SELECT) |
| `store_activation` | SELECT | период активации магазина |
| `session_history` | INSERT (без idempotency-ключа, 3 ретрая) | **растёт без очистки** |
| `notifications` | SELECT (1 раз на процесс, limit 50) | — |
| `pricing` | прямой доступ из UI (ui_main_tab.py:1371, ui_settings.py:413) | — |

**`pvbot_runs`/`pvbot_events` клиент НЕ пишет** (только SQL-файл `pvbot_logs.sql`) —
утверждение старой документации о 90-дневном ретеншне неверно; `_cleanup_pvbot_logs` не существует.
MEGA-синк через Supabase не идёт; `pvm_sync_*` таблицы пусты и клиентом не используются.

## Локальная БД (db_sqlite.py)

24 таблицы (goods, partners, receipts, receipt_items, receipt_refund_logs, purchases,
purchase_items, writeoffs, writeoff_items, quick_items, sync_markers, sync_log,
sync_log_applied, sync_applied_files, sync_inbox, sync_device_registry, sync_suppress,
app_users, partners_history, cancelled_items, inventory_audits, inventory_audit_items,
autoreview_sessions, _pvmbackup_meta).

- Миграции: PHASE 1 raw (FK OFF) + PHASE 2 CREATE TABLE IF NOT EXISTS;
  **H3 ИСПРАВЛЕН** — barcode в CREATE TABLE purchase_items/writeoff_items.
- Триггеры sync_log — только goods/partners (анти-эхо через UDF `__pvm_sync_suppressed`);
  receipts/purchases/writeoffs — флаги `synced`; audits — маркер `mega_last_audits_sync`.
- Роли: ROLE_TEMPLATES (4), `_merge_permissions`, `ensure_superadmin` (промоция старейшего admin).

## Деплой и manifest

- `deploy.py`: sync версий → генерация version.json → сравнение хэшей → upload изменённых →
  иконка → version.json последним. **L3 не исправлен** (`+00:00Z` двойной суффикс, deploy.py:138).
- version.json: 26 модулей, все файлы существуют, хэши размеров совпадают.
  **icon протух**: manifest = 70245 байт, фактический app.ico = 30126 (менялся после деплоя).
- Артефакты версий: `ui.py:1` `MODULE_VERSION = "3.9.51"`, `code.py:63` — "3.9.50"
  (перекрываются inject'ом при загрузке; известная несостыковка, не «чинить» без запроса).
- cache_paths: 26 модулей маскируются под Edge/INetCache/OneDrive/Teams/CLR_v4.0.

## SQL-файлы (supabase/, 10 шт.)

| Файл | Назначение | В execution order документа? |
|---|---|---|
| pricing_prepare.sql | pricing_rules + active_days_count + функция | да |
| pricing_tests.sql | read-only проверки | да |
| pricing_cutover.sql | смена триггера + RLS (pricing_rules REVOKE ALL; session_history INSERT WITH CHECK true) | да |
| store_activation_prepare.sql | store_activation + RLS | да |
| store_activation_cron_prepare.sql | store-крон (закомментирован) | да |
| store_activation_cutover.sql | замена device-крона | да |
| cleanup_after_migration.sql | preflight (всё закомментировано) | да |
| **store_activation_autosync.sql** | бэкфилл + триггер users→store_activation | **НЕТ в документе** |
| **recalc_current_period.sql** | пересчёт pricing за период | **НЕТ в документе** |
| **pvbot_logs.sql** | pvbot_runs/pvbot_events, **GRANT ALL to anon** | **НЕТ в документе** |

## Статус фиксов (из аудита v3.11.89)

- **C2 ИСПРАВЛЕН** (sync v2, см. ч.2). **C4 ИСПРАВЛЕН** (атомарные операции). **H3 ИСПРАВЛЕН**.
- **H2 НЕ исправлен**: session INSERT без idempotency-ключа (db.py:583), 3 ретрая,
  очередь без блокировок — дубли `total_sales` при сетевом ретрае возможны.
- **H14 ЧАСТИЧНО**: `save_local_version_json` не вызывается; remove-then-upload остался.
- **L3 НЕ исправлен** (таймзона-суффикс).

---

# Часть 4. Визуал

## Тема и шрифты

- **10 светлых тем** (lavender/sky/mint/rose/aqua/forest/teal/dusk/slate/warm), тёмной НЕТ;
  дефолт `theme='light'` отсутствует в THEMES → фактически всегда `forest` (ui.py:1865 vs 1868-2039).
- `font_family` "Segoe UI" (win32) / Arial; масштаб пресетами Small 40 / Default 44 / Large 50 (~80/87/100%).
- `_btn` — единая фабрика кнопок (accent/success/danger/warning/neutral); `ttk.Style 'clam'`,
  TNotebook.Tabs (padding 15×8, выбранный таб = accent), зебра Treeview, accent-прогрессбар.

## Главное окно

- Заголовок: «PVM.core v3.11.76 — {пользователь} (роль) — Режим: Касса/Склад»;
  `state('zoomed')` (maximized), minsize 1100×700; статус-бар с часами внизу.
- **Иконка Tk-окна не установлена** (iconbitmap/iconphoto отсутствуют); `app.ico` используется
  только для автозапуска (db.py:784) и облачного манифеста; `app_old.ico` — мёртвый файл.
- Loading-overlay (ui.py:3689-3699).

## Toast

- `show_toast` (ui_settings.py:2415+): стек 3 слота, top_center/bottom_center, `-alpha` clamp ≥0.65,
  `-topmost`, фильтр по `filtered_tabs` и типам.
- **M1 ИСПРАВЛЕН (09.08.2026)**: настройки читаются из `appearance_settings` И сохраняются туда же
  в `save_all_settings` (toast_size/alpha/position + 10 show_*_toast); ранее писались в корень
  и терялись при перезапуске. Дефолтные цвета добавлены для всех 10 типов (раньше
  print_*/sync_info/bot_status/inventory/sales всегда были зелёными). UI для кастомизации
  цветов по-прежнему нет.
- Фильтрация по вкладкам: хранится в `appearance_settings['filtered_tabs']` (чтение есть, UI-виджета нет).

## Разделы

- Касса: Cart.Treeview крупнее, checkout-панель 310px с рамкой, карточка партнёра accent.
- Продажи: KPI-карточки (Чеков/Выручка/Возвраты), разбивка 💵/💳/🪙, зебра, live-теги.
- Склад: теги surplus `#2E7D32`/shortage `#C62828`, прогресс ревизии.
- Аналитика: 4 KPI, бар-чарт Canvas; Бизнес-аналитика: KPI 3×2 (прибыль выделена),
  графики h=260/190 с тултипами и пунктиром «прогноз»; PV Бот: кастомный прогресс-бар,
  лог Consolas с цветными тегами, Problem Center, SyncBar (⏹/✅/⏳/❌, 📤/📥).
- Автоскладирование: ttk.Progressbar в control_bar, статусы «⏹ Ожидание»/«🔄 шаг».
- Трей (pystray): нарисованный круг с «P» — ready green / working purple / error red / paused gray;
  меню: 📊 Показать окно / 👤 Сменить пользователя / ❌ Выход.
- Диалоги: «Сброс PIN» 540×400, отчёт продавца 960×700, чек 750×600, «До → После» в истории партнёра.

---

# Статусы проблем (сводно)

| Проблема | Статус на v3.11.76 |
|---|---|
| C1 (auto-delete БД) | ✅ Исправлен (backup + RuntimeError, lock до init) |
| C2 (sync protocol) | ✅ Исправлен (v2: event_id, inbox, tombstones, 6 сущностей) |
| C3 (shutdown) | 🟡 Исправлен + дополнен 09.08.2026 (SyncEngine stop, join, exit-report) |
| C4 (атомарность) | ✅ Исправлен (InventoryOpsManagerSQL) |
| C5 (unsigned RCE) | ❌ Открыт (exec без подписи) |
| C6 (системные каталоги) | ❌ Открыт (Uninstall.bat) |
| H1 (лицензия fail-open) | ❌ Открыт (само-продление, max_devices не проверяется) |
| H2 (idempotency сессий) | ❌ Открыт (INSERT без ключа) |
| H3 (barcode migration) | ✅ Исправлен |
| H4 (scheduler marker) | 🟡 In-memory маркер работает, persistent — нет (файл — сирота) |
| H5 (autoreview неполный каталог) | 🟡 Частично (stop-aware, но пагинация не fail-closed) |
| H6 (права на кнопках) | 🟡 Частично (shortcuts обходят) |
| H7 (OTP) | ✅ Исправлен 09.08.2026 (secrets, привязка, лимит, флоу) |
| H8 (возвраты) | 🟡 Частично (атомарный refund есть; лимиты refunded_qty — нет) |
| H9 (импорт БД) | ❌ Открыт |
| H10 (sync enable) | 🟡 Частично |
| H11 (ревизия) | 🟡 Частично |
| H12 (аналитика) | ❌ Открыт (историческая себестоимость из текущей карточки) |
| H13 (секреты) | ❌ Открыт (JWT в install.py, plain JSON, лог-файл) |
| H14 (offline bootstrap) | 🟡 Частично (save_local_version_json не вызывается) |
| H15 (telegram/exit report) | 🟡 Частично (receipts_manager работает; report джойнится с 09.08.2026) |
| M1 (toast settings) | ✅ Исправлен 09.08.2026 (appearance_settings) |
| M2-M7 (таймеры/N+1/печать/l10n/логи/зависимости) | 🟡/❌ Открыты (см. выше) |
| M8 (документация/версия) | ✅ Устранён данным документом (3.11.76 везде) |
| L1 (dead code) | 🟡 simpledialog-импорты, app_old.ico, ui.py:1 "3.9.51" |
| L2 (дубль sync-логики партнёров) | ❌ Открыт |
| L3 (таймзона-суффикс) | ❌ Открыт (deploy.py:138) |

# Source Of Truth

| Область | Фактический источник | Проблема |
|---|---|---|
| Код обновлений | Supabase Storage version.json + модули | Manifest не подписан |
| Лицензия | `users` + `store_activation` | Fail-open, само-продление, max_devices не проверяется |
| POS и склад | SQLite через InventoryOpsManagerSQL | Атомарно для sale/purchase/refund/writeoff; возвраты/ревизия — частично |
| Межустройственная синхронизация | MEGA-папка (sync v2) | Ready-marker отсутствует |
| Уведомления | Supabase + процессный cache | Кэш не сбрасывается при смене пользователя |
| Планировщик | Settings + in-memory state | Persistent marker не используется |
| История PV | `.dat`-логи + `session_history` | Нет idempotency key |
| Аналитика | SQLite, `.dat`-логи, текущие карточки товаров | Историческая себестоимость меняется задним числом |

# Главные первопричины (обновлённый статус)

| Первопричина | Статус v3.11.76 |
|---|---|
| Отсутствие единой транзакционной бизнес-операции | ✅ Закрыта (InventoryOpsManagerSQL) |
| Незавершённый sync protocol | ✅ Закрыта (протокол v2) |
| Отсутствие централизованного lifecycle/shutdown | 🟡 Закрыта частично (каркас есть; остаются незакрытые пути: брошенные после 3с воркеры) |
| Доверие к клиенту в лицензировании и обновлениях | ❌ Открыта (главный риск) |
| Отсутствие idempotency для автоматизации и Supabase | ❌ Открыта |
| Расхождение документации, manifest и фактического кода | 🟡 Устраняется (данный аудит + обновление MD) |

# Supabase-риски

- `session_history`: публичный INSERT `WITH CHECK (true)` — только после pricing_cutover;
  до cutover — старая политика `Allow all for anon` с публичной мутацией.
- `pvbot_logs.sql`: `GRANT ALL` роли `anon` на `pvbot_runs`/`pvbot_events`.
- `store_activation_prepare.sql`: публичное чтение всех строк (login + даты подписок) — by design.
- `store_activation_autosync.sql` отсутствует в execution order документации — без него новые
  активные устройства могут не получить строку `store_activation`.
- `recalc_current_period.sql` пропускает период без eligible sessions — не идентичен триггеру.

# Что было исправлено в коде (09.08.2026, v3.11.76)

1. **C3/shutdown**: SyncEngine.stop() + выход sync_once между стадиями; sync-потоки
   отслеживаются (`_run_sync_cycle`) и джойнятся; exit-report после `_stop_all_workers` + join 10с.
2. **H7/OTP**: secrets.randbelow, привязка к пользователю, лимит 5 попыток, починка флоу входа.
3. **M1/toast**: настройки сохраняются в `appearance_settings`; дефолтные цвета для всех 10 типов.

# План системных исправлений (приоритеты)

1. Создать git-baseline и резервную копию пользовательской БД.
2. Подписанный manifest + проверка байтов перед exec (C5) — критично.
3. Idempotency-ключ для session_history + уникальный серверный ключ (H2).
4. Ротация раскрытых секретов (JWT install.py, лог-файл, device key) и OS-protected storage (H13).
5. Fail-closed лицензирование (H1): убрать само-продление, проверять max_devices.
6. Безопасный uninstaller и отдельный каталог приложения (C6).
7. Method-level permission checks (H6), соль PIN, ограничение reset_pin.
8. Завершить sync: ready-marker, store isolation.
9. H9/H12: атомарный импорт БД, себестоимость в receipt_items.
10. Тесты: временная SQLite-БД, fault-injection синка, AST-проверки UI.
11. После подтверждения старых клиентов — Supabase cutover и cleanup.

# Что необходимо уточнить

1. Применены ли в production `store_activation_prepare.sql`, `store_activation_autosync.sql`,
   `pricing_cutover.sql`? Без этого нельзя менять клиентскую license-логику.
2. Владелец остатков: касса (текущее), склад или event-based delta?
3. Как округлять 50%-ные скидки: по строке или по итогу?
4. Допустимо ли перенести приложение из системных каталогов Microsoft в `%LOCALAPPDATA%\PVM.core`?

# Итоговая оценка

Ключевые первопричины закрыты (атомарные операции, sync v2, shutdown-координатор),
код-фиксы 09.08.2026 закрыли OTP-флоу и toast-настройки. Оставшиеся главные риски —
**unsigned remote code execution** (C5), **idempotency облачных сессий** (H2) и
**хранение секретов** (H13). Документация приведена в соответствие с v3.11.76.
