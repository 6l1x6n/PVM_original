# AGENTS.md — Инструкции для ИИ-агента (PVM.core)

Этот файл — стартовая точка для работы агента в любом новом разделе репозитория.
Сначала прочитайте `AGENTS.md`, затем `PVM_GUIDE.md`, при работе с облаком — `Supabase.md`.

Текущая версия: **v3.11.76** (см. `version.json`).

---

## 1. Карта репозитория

| Файл | Назначение |
|---|---|
| `code.py` | Точка входа: лицензия → цикл сессий (вход → приложение → смена пользователя) |
| `ui.py` | GreenLeafApp: вкладки, трей, модальные окна, глобальные хоткеи, `format_amount`/`fmt_num`, `_current_search_input`, `_schedule`/`_cancel_pending_afters`, `_stop_bot_process`, shutdown-координатор `_stop_events`/`_stop_all_workers`, toast-настройки (`save_all_settings` → `appearance_settings`) |
| `ui_settings.py` | Настройки: пользователи и права, планировщик, интеграции, база данных, внешний вид (toast size/alpha/position) |
| `ui_main_tab.py` | PV Бот вкладка (поиск, кнопки запуска), SyncBar |
| `ui_bot.py` | PV Bot автоматизация (Playwright): `_login`, `_run_step1/_run_step2`, `_process_unpaid_order`, `_wait_visible`, планировщик `scheduler_loop` (in-memory маркер + поколения), `_mark_scheduler_no_run` |
| `ui_autoreview.py` | Автоскладирование (парсинг каталога GreenLeaf) |
| `ui_pos.py` | Касса (POS): корзина, поиск, оплата, `inventory_ops.sale` |
| `ui_sales.py` | Продажи, возвраты, отчёты, Excel-экспорт |
| `ui_arrival.py` | Склад: накладные, товары, списания, ревизия, история |
| `ui_partners.py` | Партнёры |
| `ui_analytics.py` / `ui_bizanalytics.py` | Аналитика / Бизнес-аналитика |
| `ui_dialogs.py` | Логин, мастер первого входа (суперадмин), OTP-флоу |
| `ui_lang.py` | Переводы ru/en + `MODULE_VERSION` |
| `db.py` | Supabase-клиент: лицензии, уведомления, `session_history` (без idempotency-ключа), очередь `_uq.bin` |
| `db_sqlite.py` | Локальная БД: менеджеры, `UsersManagerSQL` (роли/права, `ensure_superadmin`), `InventoryOpsManagerSQL` (атомарные sale/purchase/refund/writeoff), sync-схема v2 (`sync_log`/`sync_inbox`/`sync_applied_files`), шаблоны ролей |
| `market.py` | Compatibility facade: реэкспорт менеджеров из `db_sqlite` (используется `ui.py`) |
| `settings.py` | Глобальные настройки, `ROLE_LABELS`, `get_appearance_settings`/`get_integration_settings`, лицензионная подпись |
| `pvm_core.py` | Email/Telegram/OTP (`OTPManager`: secrets, привязка к пользователю, лимит попыток), `send_exit_report` |
| `sync_*.py` + `transport_local.py` | MEGA-фолдер синхронизация (единственный путь синка), протокол v2: `event_id` + `sync_inbox`, stock-дельты, tombstones, checksum, LWW по `updated_at`, 7 сущностей, rebase |
| `deploy.py` | Деплой модулей в Supabase Storage + `version.json` |
| `install.py` | Лаунчер (Windows, обфускация, in-memory exec) |

## 2. Золотые правила

1. **Деплой ТОЛЬКО** через `python3 deploy.py --version X.Y.Z` из корня. Версию брать
   следующей от текущей в `version.json` (сейчас 3.11.76 → следующая 3.11.77).
   Никогда не загружать файлы в Supabase вручную.
2. **После любых правок Python** — `python3 -m py_compile <файлы>`.
3. **Перед правкой** читать контекст (минимум ~50 строк вокруг места изменения),
   мимикрировать стиль кода (русские тексты UI, `self._btn`, `self.colors` и т.д.).
4. **Не добавлять новые зависимости** без явного запроса пользователя
   (venv на машине разработки: `~/.venv`, `tkcalendar`, `playwright`, `supabase`, `pandas`).
5. **Не ломать совместимость БД** — миграции только через ALTER TABLE в `db_sqlite.py`.
6. **Не оставлять комментарии в коде по мелочи** — код пишется без пояснительных
   комментариев (стиль проекта), но важные нетривиальные блоки можно помечать кратко.
7. **Тестировать логику БД** через временную БД (`tempfile.mkdtemp`) — см. паттерны ниже.
8. **UI-проект запускается как** `code.py`; полный импорт `ui.*` локально невозможен
   (нет `tkcalendar` в системном python) — для проверок использовать AST/`py_compile`.

## 3. Ключевые архитектурные факты (v3.11.76)

### Роли и права
- Роли: `superadmin` (единственный, первый созданный; несменяем, неудаляем),
  `admin`, `cashier`, `viewer`. Ярлыки в `settings.ROLE_LABELS`.
- `UsersManagerSQL.ensure_superadmin()` — миграция: при отсутствии суперадмина
  старейший активный `admin` повышается (вызов в `code.py` при старте).
- Права хранятся в `app_users.permissions` (JSON), мержатся с шаблоном роли
  (`ROLE_TEMPLATES` в `db_sqlite.py`) через `_merge_permissions` — новые ключи
  автоматически подтягиваются к существующим пользователям.
- Панель прав: Настройки → Пользователи и права → «Права доступа».
  Категории и порядок заданы в `PERM_CATEGORIES` (ui.py). «Настройки» — в самом низу.
- **PV Бот**: вкладка видна всем при подписке 3/4; ручной запуск гейтится только
  правом `pvbot_use` («PV Бот: Запуск»); **автозапуск планировщика правами НЕ гейтится**.
- `has_permission()`: `admin`/`superadmin` всегда True.
- **Известное ограничение**: проверки прав — на уровне кнопок; клавиатурные
  shortcuts (например, Enter в finalize_invoice) могут обходить disabled-кнопки.

### Атомарные бизнес-операции
- `InventoryOpsManagerSQL` (db_sqlite.py) — единая транзакционная точка для
  `sale`, `purchase`, `refund`, `writeoff`: чек/документ + остатки + партнёр
  в одном соединении, валидация остатков `_check_stock`, rollback при ошибке.
- Вызывается из `ui_pos.py`, `ui_arrival.py`, `ui_sales.py` — НЕ писать
  поэтапные изменения остатков в новых правках.

### Планировщик (автозапуск PV)
- Старт отложен на 5с после запуска mainloop (`_start_scheduler_deferred`).
- **Один запуск в день — in-memory**: `scheduler_loop` (ui_bot.py) хранит
  `last_run_date`; `initial_check` помечает день выполненным, если время уже прошло
  на старте сессии; `config_generation`/`_scheduler_config_generation` сбрасывают
  маркер при изменении времени/папки; `_scheduler_generation` — рестарт-поколение.
- **Persistent-маркер `cache/pvbot_last_run.json` в коде НЕ используется** (файл —
  сирота от старых версий). Не документировать его как работающий механизм.
- `_mark_scheduler_no_run` пишет статус «нет файла» в память (`_pvbot_last_run`),
  показывается в quick status вкладки PV Бот.
- Поток планировщика НЕ трогает Tk напрямую: только `_tk_after`/`_sched_notify`/
  кэши (`cached_scheduled_time`, `cached_watch_directory`, `cached_shutdown_after`).
- Выход из цикла: `scheduler_running=False` + стоп-ивент `_stop_events['scheduler']`
  + проверка поколения.

### Shutdown-координатор (C3)
- `_stop_events` (ui.py) — 5 ивентов: `scheduler`/`autoreview`/`live_bot`/
  `integration`/`sync`. Все выставляются в `_stop_all_workers()`.
- Механизмы остановки воркеров: планировщик — ивент + `scheduler_running=False` +
  бамп поколения; autoreview — `_ar_stop_event`; live bot — poison pill в
  `live_bot_queue`; integration — `integration_bot.stop()`; SyncEngine —
  `sync_engine.stop()` (внутренний флаг, `sync_once` выходит между стадиями).
- `_track_worker(t)` регистрирует потоки; `_stop_all_workers` джойнит их с
  timeout=3с и отменяет таймеры (`_cancel_pending_afters`).
- Sync-циклы запускаются через `_run_sync_cycle` (отслеживаемый поток, сам
  снимает себя из `_workers` по завершении).
- `_send_exit_report` выполняется ПОСЛЕ `_stop_all_workers` и джойнится (10с) —
  отчёт не обрывается на выходе.
- Воркеры → Tk только через `_ui_call`/`_ui_queue_pump` (не `master.after` из потока).

### Модальные окна
- Единый фабричный метод `create_modal_dialog(title, width, height, scrollable, dismiss_on_outside)`.
- Все диалоги: `transient` + `grab_set` + `lift()` (статичные; `-topmost` НЕ используется).
- `dismiss_on_outside=True` — для просмотров/лёгких окон (клик мимо = закрыть);
  формы и подтверждения — `False` (клик мимо игнорируется, «настаивают» на решении).
- `simpledialog` НЕ используется (импорты в ui_pos.py/ui_arrival.py — мёртвые,
  не трогать без необходимости); замены: `ask_float_dialog`, `ask_string_dialog`.

### Цены — только целые числа
- Нормализация `int(round())` для `purchase_price`/`sale_price` в `GoodsManagerSQL.add_good`
  (единая точка записи; покрывает диалоги, синк, импорт, автоскладирование).
- Поля цен в диалогах: валидация `_validate_int_input` (только цифры) — копейки не вводятся.
- Отображение: `format_amount` (всегда целые, пробелы-разделители), `fmt_num` (без «.0» у целых).

### Единый поиск/автофокус
- Один источник правды: `_current_search_input()` — карта «вкладка → поле поиска»
  (Касса, Накладная, Список товаров, Отмены, Поступления, Партнёры).
- `on_global_keypress` перехватывает ввод только когда нет открытых модалок
  (`grab_current()` guard) и фокус не в другом поле.
- Автофокус при смене вкладок через `_focus_search_field()`.

### Остановка бота (чистая)
- `stop_processing` НЕ вызывает playwright-API с главного потока (иначе
  greenlet «Cannot switch to a different thread»). Только `stop_event.set()`.
- `_stop_bot_process` (ui.py): стоп-ивент + `join(timeout=3)` воркера.
- Все длинные ожидания — через `_wait_visible(page, sel, timeout)` (полит 0.4с,
  проверяет `stop_event`) — воркер выходит за ~0.5с и сам закрывает браузер.

### OTP (вход после 3 неверных PIN)
- `OTPManager` (pvm_core.py): генерация через `secrets.randbelow`, привязка кода
  к имени пользователя, TTL 10 минут, лимит 5 попыток (после — аннулирование).
- Флоу ui_dialogs.py: при успешном OTP пользователь входит сразу (OTP —
  recovery-фактор владения почтой/Telegram; неверный PIN повторно не проверяется).

### Смена пользователя / выход
- Кнопка «⇄ Сменить» (Настройки → Главная) и пункт трея «👤 Сменить пользователя».
- Флоу: `request_switch_user` → стоп бота с подтверждением → `_stop_all_workers` →
  destroy → цикл сессий в `code.py` → снова экран входа.
- Трей (Windows): Показать окно / Сменить пользователя / Выход.
- При работающем боте: подтверждение «PV Бот сейчас выполняет операции…».
- Exit-report отправляется только при выходе (не при смене пользователя).

### Фоновые таймеры
- Все периодические `after`-таймеры регистрируются через `_schedule()` и отменяются
  через `_cancel_pending_afters()` перед destroy (иначе bgerror «invalid command name»).

### Уведомления
- `db.fetch_notifications()` — ОДИН раз на процесс (`_notifications_fetched_once`, db.py).
- **`db.reset_notifications_cache()` НЕ существует** — кэш уведомлений не сбрасывается
  при смене пользователя (известное ограничение; при починке — добавить сброс флага
  в цикл сессий `code.py` и поправить этот раздел).

### Toast-уведомления и внешний вид
- `show_toast` (ui_settings.py): стек до 3 слотов, позиции top_center/bottom_center,
  `-alpha` (clamp ≥0.65), фильтр по вкладкам (`filtered_tabs`) и типам.
- Настройки читаются из `appearance_settings` (settings.py) и сохраняются ТУДА ЖЕ
  в `save_all_settings` (ui.py) — toast_size/toast_alpha/toast_position и все
  show_*_toast. Не писать эти ключи в корень настроек.
- Цвета по типам: `DEFAULT_APPEARANCE_SETTINGS.toast_colors` — 10 типов
  (success/error/warning/info/print_success/print_error/sync_info/bot_status/
  inventory/sales); deep-merge при чтении сохраняет кастомные цвета.

### Синхронизация (протокол v2, дельты)
- Единственный путь синка — MEGA-фолдер. Движок: `SyncEngine.sync_once`
  (flush outbox → consume → snapshots → rebase → apply → janitor).
- JSONL-файлы с манифестом+checksum; битые файлы отбраковываются целиком.
- `sync_inbox` с `event_id` (INSERT OR IGNORE) — дедупликация; apply до 10 попыток
  со stale-маркировкой (`applied=-1`).
- Зарегистрировано 7 сущностей: goods, stock_delta, partners, receipts,
  purchases, writeoffs, audits.
- **Остатки = аддитивные дельты**: триггер `trg_goods_stock_delta`
  (`AFTER UPDATE OF quantity`) пушит `NEW.quantity - OLD.quantity` как
  `stock_delta`; приход/продажа/списание/возврат/ревизия/автоскладирование на
  любом устройстве дают ±дельту — все устройства сходятся (сложение
  коммутативно, idempotent по event_id). `_apply_goods` — только каталог-поля
  (quantity существующих товаров НЕ трогается нигде); новый товар создаётся с
  quantity из payload как база.
- Stock-мутации НЕ бампают `goods.updated_at`; каталог-триггер сужен до
  `AFTER UPDATE OF name, pv, barcode, purchase_price, sale_price, is_deleted` —
  продажи больше не перетирают каталог-правки (LWW только для каталога).
- Tombstones: delete товара — soft-delete с мержем, purchase — `cancelled`,
  партнёр — `is_blocked=1` + `block_reason='Удалён'` (жёсткое удаление партнёра
  с историей чеков невозможно).
- LWW по `updated_at` (datetime; aware→naive UTC в `_parse_ts`; fallback на
  строковое сравнение).
- **Ребаза** (bootstrap дельт): маркер `stock_rebase_v2`; первый goods-снапшот от
  `device_type=cashier` выставляет абсолютные остатки; `stock_rebase_cutoff`
  (= `generated_at_utc` снапшота) — дельты со временем движения раньше cutoff
  пропускаются в `_apply_inbox` (`last_error='rebase'`). `request_full_resync`
  сбрасывает маркеры ребазы, но НЕ переприменяет stock_delta (иначе двойной
  счёт остатков). При ошибке ребазы — повтор через «Полную пересинхронизацию».
- Снапшоты: goods, partners, receipts, purchases, writeoffs, audits (полные
  дампы, еженедельно, keep=2 **на сущность**) — устройство, офлайн >35 дней,
  восстанавливает документы из снапшота.
- Janitor: outbox 35 дней; удаление только при ack от всех живых устройств,
  без pending-событий этого файла в `sync_inbox` (`_has_pending_events`) и
  только для своих файлов, если других устройств нет; реестр устройств
  протухает за 60 дней (`last_seen`; ack-устройства — по mtime файла);
  чистка sync_log/inbox.
- ID приходов/списаний: `{device_key[:4]}-{counter}` — без коллизий между
  устройствами; `_get_next_counter` парсит числовую часть (легаси-совместимость).
- `_collapse` (sync_queue) НЕ коллапсит `stock_delta`; `flush()` помечает synced
  ВСЕ строки батча (иначе промежуточные дельты переслались бы повторно).
- Риск: клон диска с тем же `device_key` → устройства молча игнорируют файлы
  друг друга (self-skip по `source_device`). Лечится `reset_device_key`.

### Сироты v2 (не используются — не «чинить» без запроса)
- `sync_log_applied` (легаси-идемпотентность), `sync_suppress` (schema-stability);
- легаси-флаги `goods.synced`/`partners.synced` + `get_unsynced_goods`/
  `mark_goods_synced`/`get_unsynced_partners`/`mark_partner_synced`;
- ТЗ-маркеры `last_goods_sync`, `last_partners_sync`, `last_receipts_sync`,
  `wipe_goods`, `cloud_master_pull_goods_edit`; pull-хелперы `*_after()`
  (get_all_partners_after/get_all_receipts_after/get_all_purchases_after/
  get_all_writeoffs_after) и миграция `deep_sync_v35`.

## 4. Паттерны тестирования

```bash
# Синтаксис
python3 -m py_compile ui.py ui_bot.py ui_settings.py db_sqlite.py

# Логика БД (временная БД)
python3 - <<'EOF'
import os, tempfile, sys
sys.path.insert(0, '.')
from db_sqlite import DatabaseManager, UsersManagerSQL
um = UsersManagerSQL(DatabaseManager(os.path.join(tempfile.mkdtemp(), 't.db')))
um.create_user('cash', 'Кассир', 'cashier', '1111')
print(um.get_user_by_username('cash')['role'])
EOF
```

AST-проверки (когда полный импорт невозможен): парсить файл через `ast`,
проверять наличие методов/строк-паттернов в нужных участках.

## 5. Чек-лист перед деплоем

1. `python3 -m py_compile` всех изменённых файлов.
2. Функциональные проверки изменений (БД-логика — временная БД; UI-логика — AST).
3. Убедиться, что не осталось `simpledialog`, прямых `master.after` из потоков,
   playwright-вызовов с главного потока (в новых правках).
4. `python3 deploy.py --version <следующий патч>` — скрипт сам синхронизирует
   версии в ui.py/ui_lang.py/code.py/settings.py, пересчитает хэши и загрузит
   только изменённые модули.
5. После деплоя: `py_compile` заново (версии меняются скриптом) и проверить
   `version.json` (`python3 -c "import json; print(json.load(open('version.json'))['version'])"`).
6. Известные артефакты версий (не «чинить» без запроса): `ui.py:1` содержит
   `MODULE_VERSION = "3.9.51"` (перекрывается inject'ом), `code.py` — "3.9.50".
