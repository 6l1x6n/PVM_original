# -*- coding: utf-8 -*-
"""
PVM.core - Autoreview (Autowarehouse) Mixin
=============================================
Warehouse auto-reconciliation: scrapes Greenleaf goods via Playwright
and syncs them to the local database.
"""

import re
import time
import json
import uuid
import threading
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
from datetime import datetime
from typing import Optional

import settings
from ui_dialogs import AutoScrollbar

PRODUCT_CODE_STRICT_RE = re.compile(r"^[A-Z]{3}\d{3}$")
BOX_PREFIX_RE = re.compile(
    r"""^[\s()]*
        (?:Кол[-\s]?во?\s+)?
        (?:в\s+)?коробк[аеиу]
        \s*[:\.]?\s*\d+\s*шт\.?\s*[\)\.:,]?\s*""",
    re.IGNORECASE | re.VERBOSE
)
AVAILABLE_QTY_RE = re.compile(r"Доступно для продажи:\s*(\d+)")
PRICE_VALUE_RE = re.compile(r"([\d\s]+)")
SKIP_CODE_PATTERN = re.compile(r"^[A-Z]{1,2}\d{3,}$")


def clean_product_name(raw_name: str) -> str:
    name = re.sub(r'&nbsp;', ' ', raw_name)
    name = re.sub(r'\s+', ' ', name).strip()
    name = BOX_PREFIX_RE.sub('', name).strip()
    name = re.sub(r'\s*Доступно\s+для\s+продажи:\s*\d+\s*$', '', name, flags=re.IGNORECASE).strip()
    name = re.sub(r'\s*Доступно:\s*\d+\s*$', '', name, flags=re.IGNORECASE).strip()
    name = re.sub(r'^[\s)]+', '', name).strip()
    return name


class AutoreviewMixin:
    """Autoreview tab methods for GreenLeafApp."""

    def __init__(self):
        self._ar_lock = threading.RLock()
        self._ar_running = False
        self._ar_current_step = "idle"
        self._ar_current_code = ""
        self._ar_started_at = None
        self._ar_items_total = 0
        try:
            self._ar_price_multiplier = float(
                settings.load_settings().get('ar_price_multiplier', 2.0) or 2.0)
        except Exception:
            self._ar_price_multiplier = 2.0
        self._ar_items_parsed = 0
        self._ar_items_created = 0
        self._ar_items_updated = 0
        self._ar_items_skipped = 0
        self._ar_skipped_codes = []
        self._ar_error_message = ""
        self._ar_stats = {}
        self._ar_log_buffer = []
        self._ar_log_idx = 0
        self._ar_stop_event = threading.Event()
        self._ar_thread = None
        self._ar_browser = None
        self._ar_playwright_ctx = None

    def _ar_log(self, level: str, text: str) -> int:
        ts = datetime.now().strftime("%H:%M:%S")
        with self._ar_lock:
            self._ar_log_idx += 1
            idx = self._ar_log_idx
            self._ar_log_buffer.append({
                "idx": idx,
                "ts": ts,
                "level": level,
                "text": text,
            })
        if level != "debug":
            msg = f"[{ts}] [AUTOREVIEW {level}] {text}"
            try:
                print(msg, flush=True)
            except UnicodeEncodeError:
                print(msg.encode("ascii", errors="replace").decode("ascii"), flush=True)
        return idx

    def _ar_get_log(self, since: int = 0) -> list:
        with self._ar_lock:
            return [e for e in self._ar_log_buffer if e["idx"] > since]

    def _ar_clear_log(self) -> None:
        with self._ar_lock:
            self._ar_log_buffer.clear()
            self._ar_log_idx = 0

    def _ar_snapshot(self) -> dict:
        with self._ar_lock:
            elapsed = 0
            if self._ar_started_at:
                try:
                    elapsed = int((datetime.now() - self._ar_started_at).total_seconds())
                except Exception:
                    pass
            return {
                "running": self._ar_running,
                "current_step": self._ar_current_step,
                "current_code": self._ar_current_code,
                "started_at": self._ar_started_at.isoformat() if self._ar_started_at else None,
                "elapsed_sec": elapsed,
                "items_total": self._ar_items_total,
                "items_parsed": self._ar_items_parsed,
                "items_created": self._ar_items_created,
                "items_updated": self._ar_items_updated,
                "items_skipped": self._ar_items_skipped,
                "skipped_codes": self._ar_skipped_codes[-50:],
                "error_message": self._ar_error_message,
            }

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------
    def create_autoreview_tab(self):
        """Create the autoreview UI within the given frame."""
        c = self.colors
        frame = self.autoreview_frame

        frame.grid_rowconfigure(0, weight=0)
        frame.grid_rowconfigure(1, weight=1)
        frame.grid_columnconfigure(0, weight=3)
        frame.grid_columnconfigure(1, weight=2)

        # --- Control bar (start button + status + progress) ---
        control_bar = tk.Frame(frame, bg=c['bg'])
        control_bar.grid(row=0, column=0, columnspan=2, sticky="ew", padx=10, pady=(10, 5))

        self._ar_start_btn = self._btn(control_bar, text="▶ Запустить автоскладирование", command=self._ar_start, style='success', cursor="hand2")
        self._ar_start_btn.pack(side="left", padx=(0, 8))

        self._ar_status_label = tk.Label(
            control_bar, text="⏹ Ожидание", font=self.font_normal_tuple,
            bg=c['bg'], fg=c['fg_muted'],
        )
        self._ar_status_label.pack(side="left", fill="x", padx=15)

        tk.Label(control_bar, text="Наценка ×", font=self.font_normal_tuple,
                 bg=c['bg'], fg=c['fg_secondary']).pack(side="left")
        self._ar_multiplier_var = tk.StringVar(value=str(self._ar_price_multiplier))

        def on_multiplier_change(*args):
            try:
                val = float(self._ar_multiplier_var.get().replace(',', '.'))
                if val <= 0:
                    raise ValueError
                val = round(val, 1)
                self._ar_price_multiplier = val
                s = settings.load_settings()
                s['ar_price_multiplier'] = val
                settings.save_settings(s)
            except (ValueError, TypeError):
                pass

        mult_spin = ttk.Spinbox(control_bar, from_=0.5, to=10.0, increment=0.1,
                                textvariable=self._ar_multiplier_var, width=5)
        mult_spin.pack(side="left", padx=(4, 12))
        self._ar_multiplier_var.trace_add('write', on_multiplier_change)

        self._ar_progress = ttk.Progressbar(control_bar, mode='determinate', value=0)
        # Hidden by default, shown via pack during _ar_poll

        # --- Row 1: history (left) + log/detail (right) ---

        # History panel (left)
        hist_frame = tk.LabelFrame(
            frame, text=" 📜 История сессий ",
            font=self.font_bold_tuple, bg=c['frame_bg'], fg=c['fg'],
            padx=5, pady=5,
        )
        hist_frame.grid(row=1, column=0, sticky="nsew", padx=(10, 5), pady=(0, 10))
        hist_frame.grid_rowconfigure(0, weight=1)
        hist_frame.grid_columnconfigure(0, weight=1)

        columns = ("date", "status", "total", "created", "updated", "skipped", "error")
        self._ar_history_tree = ttk.Treeview(
            hist_frame, columns=columns, show="headings",
            height=6, selectmode="browse",
        )
        self._ar_history_tree.heading("date", text="Дата")
        self._ar_history_tree.heading("status", text="Статус")
        self._ar_history_tree.heading("total", text="Всего")
        self._ar_history_tree.heading("created", text="Создано")
        self._ar_history_tree.heading("updated", text="Обновлено")
        self._ar_history_tree.heading("skipped", text="Пропущено")
        self._ar_history_tree.heading("error", text="Ошибка")
        self._ar_history_tree.column("date", minwidth=100, stretch=True)
        self._ar_history_tree.column("status", width=70, minwidth=60, stretch=False, anchor="center")
        self._ar_history_tree.column("total", width=60, minwidth=50, stretch=False, anchor="center")
        self._ar_history_tree.column("created", width=60, minwidth=50, stretch=False, anchor="center")
        self._ar_history_tree.column("updated", width=60, minwidth=50, stretch=False, anchor="center")
        self._ar_history_tree.column("skipped", width=60, minwidth=50, stretch=False, anchor="center")
        self._ar_history_tree.column("error", width=120, minwidth=80, stretch=False)

        hist_scroll = AutoScrollbar(hist_frame, orient="vertical", command=self._ar_history_tree.yview)
        self._ar_history_tree.configure(yscrollcommand=hist_scroll.set)
        self._ar_history_tree.pack(side="left", fill="both", expand=True)
        hist_scroll.pack(side="right", fill="y")

        self._ar_history_tree.bind("<<TreeviewSelect>>", self._ar_show_session_detail)

        # Right panel: log + detail (share column 1, only one visible at a time)
        right_pane = tk.Frame(frame, bg=c['bg'])
        right_pane.grid(row=1, column=1, sticky="nsew", padx=(5, 10), pady=(0, 10))
        right_pane.grid_columnconfigure(0, weight=1)
        right_pane.grid_rowconfigure(0, weight=1)

        # Log panel (visible by default)
        self._ar_log_frame = tk.LabelFrame(
            right_pane, text=" 📋 Лог автоскладирования ",
            font=self.font_bold_tuple, bg=c['frame_bg'], fg=c['fg'],
            padx=5, pady=5,
        )
        self._ar_log_frame.grid(row=0, column=0, sticky="nsew")
        self._ar_log_frame.grid_rowconfigure(0, weight=1)
        self._ar_log_frame.grid_columnconfigure(0, weight=1)

        self._ar_log_text = scrolledtext.ScrolledText(
            self._ar_log_frame, wrap=tk.WORD, font=("Consolas", self.font_small),
            bg=c['input_bg'], fg=c['input_fg'], insertbackground=c['fg'],
            relief="solid", bd=1,
        )
        self._ar_log_text.grid(row=0, column=0, sticky="nsew", padx=2, pady=2)
        self._ar_log_text.config(state="disabled")

        self._ar_log_text.tag_config("error", foreground=c['error'])
        self._ar_log_text.tag_config("warning", foreground=c['warning'])
        self._ar_log_text.tag_config("info", foreground=c['input_fg'])
        self._ar_log_text.tag_config("success", foreground=c['success'])
        self._ar_log_text.tag_config("debug", foreground=c['fg_muted'])

        # Detail panel (hidden by default, shown on session select)
        self._ar_detail_frame = tk.Frame(right_pane, bg=c['bg'])
        self._ar_detail_frame.grid(row=0, column=0, sticky="nsew")
        self._ar_detail_frame.grid_rowconfigure(2, weight=1)
        self._ar_detail_frame.grid_columnconfigure(0, weight=1)
        self._ar_detail_frame.grid_remove()

        # Detail title bar with close button
        detail_title = tk.Frame(self._ar_detail_frame, bg=c['frame_bg'], padx=8, pady=4)
        detail_title.grid(row=0, column=0, sticky="ew")
        tk.Label(detail_title, text="📋 Детали сессии",
                 font=self.font_bold_tuple, bg=c['frame_bg'], fg=c['fg']).pack(side="left")
        self._ar_detail_close_btn = self._btn(
            detail_title, text="✕ Закрыть",
            command=self._ar_close_detail, style='neutral', compact=True, cursor="hand2",
        )
        self._ar_detail_close_btn.pack(side="right")

        ttk.Separator(self._ar_detail_frame, orient='horizontal').grid(row=1, column=0, sticky="ew")

        # Detail header (summary text)
        self._ar_detail_header = tk.Text(
            self._ar_detail_frame, height=5, wrap=tk.WORD,
            font=("Consolas", self.font_small),
            bg=c['input_bg'], fg=c['input_fg'], relief="solid", bd=1,
        )
        self._ar_detail_header.grid(row=2, column=0, sticky="nsew", padx=2, pady=(4, 2))
        self._ar_detail_header.config(state="disabled")

        # Detail log
        self._ar_detail_log = scrolledtext.ScrolledText(
            self._ar_detail_frame, wrap=tk.WORD, font=("Consolas", self.font_small),
            bg=c['input_bg'], fg=c['input_fg'], relief="solid", bd=1,
        )
        self._ar_detail_log.grid(row=3, column=0, sticky="nsew", padx=2, pady=2)
        self._ar_detail_log.config(state="disabled")

        # Permission gating
        if not self.has_permission('autoreview_start'):
            self._ar_start_btn.config(state="disabled", bg=c['bg_tertiary'])

        # Load session history
        self._ar_refresh_history()
        # Auto-select first session to populate detail panel
        self.master.after(200, self._ar_select_first_session)

        # Start polling
        self._ar_poll()

    def _ar_log_ui(self, level: str, text: str) -> None:
        idx = self._ar_log(level, text)
        if hasattr(self, '_ar_log_text') and self._ar_log_text.winfo_exists():
            # Called from the worker thread — dispatch via the C3 queue
            self._ui_call(self._ar_append_log_ui, level, text, idx)

    def _ar_append_log_ui(self, level: str, text: str, idx: int) -> None:
        try:
            self._ar_last_log_idx = idx
            self._ar_log_text.config(state="normal")
            tag_map = {"error": "error", "warning": "warning", "info": "info",
                       "success": "success", "debug": "debug"}
            tag = tag_map.get(level, "info")
            prefix = {"error": "❌ ", "warning": "⚠️ ", "success": "✅ ", "info": "  "}.get(level, "")
            self._ar_log_text.insert(tk.END, f"{prefix}{text}\n", tag)
            self._ar_log_text.see(tk.END)
            self._ar_log_text.config(state="disabled")
        except Exception:
            pass

    def _ar_update_ui(self, snapshot: dict) -> None:
        if not hasattr(self, '_ar_stats_labels'):
            return
        labels = self._ar_stats_labels
        try:
            if labels["step"].winfo_exists():
                labels["step"].config(text=snapshot.get("current_step", "—"))
            if labels["elapsed"].winfo_exists():
                elapsed = snapshot.get("elapsed_sec", 0)
                labels["elapsed"].config(text=f"{elapsed // 60}:{elapsed % 60:02d}")
            if labels["total"].winfo_exists():
                labels["total"].config(text=str(snapshot.get("items_total", 0)))
            if labels["parsed"].winfo_exists():
                labels["parsed"].config(text=str(snapshot.get("items_parsed", 0)))
            if labels["created"].winfo_exists():
                labels["created"].config(text=str(snapshot.get("items_created", 0)))
            if labels["updated"].winfo_exists():
                labels["updated"].config(text=str(snapshot.get("items_updated", 0)))
            if labels["skipped"].winfo_exists():
                labels["skipped"].config(text=str(snapshot.get("items_skipped", 0)))
        except Exception:
            pass

    def _ar_poll(self) -> None:
        if getattr(self, '_shutting_down', False):
            return
        if not hasattr(self, '_ar_status_label') or not self._ar_status_label.winfo_exists():
            return
        snap = self._ar_snapshot()
        running = snap["running"]
        self._ar_update_ui(snap)

        if running:
            step = snap.get("current_step", "")
            step_label = {
                "init": "Подключение",
                "browser_setup": "Подготовка",
                "navigate": "Получение каталога",
                "login_buy": "Получение каталога",
                "scraping": "Получение каталога",
                "applying": "Синхронизация",
            }.get(step, "Работа")
            self._ar_status_label.config(text=f"🔄 {step_label}", fg=self.colors['success'])
            self._ar_start_btn.config(text="⏸ Приостановить", state="normal", bg=self.colors['warning'])
            # Honest progress: 0% while the catalog is still being parsed
            # (items_total unknown), real percent during apply, done afterwards.
            total = snap.get("items_total", 0) or 0
            if total > 0:
                progress = min(100, int((snap.get("items_parsed", 0) or 0) / total * 100))
            else:
                progress = 0
            self._ar_progress["value"] = progress
            try:
                self._ar_progress.pack(side="right", padx=(10, 5), fill="x", expand=True)
            except:
                pass
        else:
            self._ar_status_label.config(text="⏹ Ожидание", fg=self.colors['fg_muted'])
            can_start = self.has_permission('autoreview_start')
            self._ar_start_btn.config(
                text="▶ Запустить автоскладирование",
                state="normal" if can_start else "disabled",
                bg=self.colors['success'] if can_start else self.colors['bg_tertiary'],
            )
            self._ar_progress["value"] = 0
            try:
                self._ar_progress.pack_forget()
            except:
                pass

        # Refresh new log lines
        self._ar_flush_log_ui()

        if not getattr(self, '_shutting_down', False):
            self._schedule(1000, self._ar_poll)

    def _ar_flush_log_ui(self) -> None:
        if not hasattr(self, '_ar_log_text') or not self._ar_log_text.winfo_exists():
            return
        try:
            last_idx = getattr(self, '_ar_last_log_idx', 0)
            new_lines = self._ar_get_log(since=last_idx)
            if not new_lines:
                return
            self._ar_last_log_idx = new_lines[-1]["idx"]
            self._ar_log_text.config(state="normal")
            tag_map = {"error": "error", "warning": "warning", "info": "info",
                       "success": "success", "debug": "debug"}
            for entry in new_lines:
                tag = tag_map.get(entry["level"], "info")
                prefix = {"error": "❌ ", "warning": "⚠️ ", "success": "✅ ", "info": "  "}.get(
                    entry["level"], "")
                self._ar_log_text.insert(tk.END, f"{prefix}{entry['text']}\n", tag)
            self._ar_log_text.see(tk.END)
            self._ar_log_text.config(state="disabled")
        except Exception:
            pass

    def _ar_refresh_history(self) -> None:
        if not hasattr(self, '_ar_history_tree') or not self._ar_history_tree.winfo_exists():
            return
        try:
            for row in self._ar_history_tree.get_children():
                self._ar_history_tree.delete(row)
            sessions = self._ar_get_sessions()
            for s in sessions:
                status_icon = {"done": "✅", "error": "❌", "stopped": "⏹", "running": "🔄"}.get(
                    s.get("status", ""), s.get("status", ""))
                self._ar_history_tree.insert("", "end", values=(
                    s.get("started_at", "")[:19] if s.get("started_at") else "",
                    status_icon,
                    s.get("items_total", 0),
                    s.get("items_created", 0),
                    s.get("items_updated", 0),
                    s.get("items_skipped", 0),
                    (s.get("error_message", "") or "")[:50],
                ), tags=(s.get("id", ""),))
        except Exception as e:
            print(f"Error refreshing autoreview history: {e}")

    def _ar_select_first_session(self):
        if not hasattr(self, '_ar_history_tree') or not self._ar_history_tree.winfo_exists():
            return
        children = self._ar_history_tree.get_children()
        if children:
            self._ar_history_tree.selection_set(children[0])
            self._ar_show_session_detail()

    def _ar_close_detail(self):
        self._ar_detail_frame.grid_remove()
        self._ar_log_frame.grid()

    def _ar_show_session_detail(self, event=None):
        sel = self._ar_history_tree.selection()
        if not sel:
            return
        item = self._ar_history_tree.item(sel[0])
        session_id = item.get("tags", ("",))[0] if item.get("tags") else ""
        if not session_id:
            return
        session = self._ar_get_session(session_id)
        if not session:
            return

        # Hide log, show detail
        self._ar_log_frame.grid_remove()
        self._ar_detail_frame.grid()

        # Fill detail header
        self._ar_detail_header.config(state="normal")
        self._ar_detail_header.delete("1.0", tk.END)
        self._ar_detail_header.insert("1.0", "\n".join([
            f"ID: {session.get('id', '')}",
            f"Начало: {session.get('started_at', '')}",
            f"Конец: {session.get('finished_at', '')}",
            f"Статус: {session.get('status', '')}",
            f"Длительность: {session.get('duration_sec', 0)} сек",
            f"Всего: {session.get('items_total', 0)} | Создано: {session.get('items_created', 0)} | "
            f"Обновлено: {session.get('items_updated', 0)} | Пропущено: {session.get('items_skipped', 0)}",
            f"Ошибка: {session.get('error_message', '')}",
        ]))
        self._ar_detail_header.config(state="disabled")

        # Fill detail log (takes most of the panel)
        self._ar_detail_log.config(state="normal")
        self._ar_detail_log.delete("1.0", tk.END)
        self._ar_detail_log.insert("1.0", session.get("log_text", ""))
        self._ar_detail_log.config(state="disabled")

    # ------------------------------------------------------------------
    # Database helpers
    # ------------------------------------------------------------------
    def _ar_create_session(self, session_id: str) -> None:
        try:
            with self._db_manager.get_connection() as conn:
                conn.execute('''
                    INSERT INTO autoreview_sessions (id, started_at, status, items_total,
                        items_parsed, items_created, items_updated, items_skipped,
                        error_message, log_text, skipped_codes, stats)
                    VALUES (?, ?, ?, 0, 0, 0, 0, 0, '', '', '[]', '{}')
                ''', (session_id, datetime.now().isoformat(), "running"))
        except Exception as e:
            print(f"Error creating autoreview session: {e}")

    def _ar_finish_session(self, session_id: str, status: str, error: str = "") -> None:
        try:
            with self._ar_lock:
                log_text = "\n".join(
                    f"[{e['ts']}] [{e['level'].upper()}] {e['text']}"
                    for e in self._ar_log_buffer
                )
            duration = 0
            if self._ar_started_at:
                duration = int((datetime.now() - self._ar_started_at).total_seconds())
            with self._db_manager.get_connection() as conn:
                conn.execute('''
                    UPDATE autoreview_sessions SET
                        finished_at = ?, duration_sec = ?, status = ?,
                        items_total = ?, items_parsed = ?, items_created = ?,
                        items_updated = ?, items_skipped = ?,
                        error_message = ?, log_text = ?,
                        skipped_codes = ?, stats = ?
                    WHERE id = ?
                ''', (
                    datetime.now().isoformat(), duration, status,
                    self._ar_items_total, self._ar_items_parsed,
                    self._ar_items_created, self._ar_items_updated,
                    self._ar_items_skipped, error,
                    log_text[-50000:] if len(log_text) > 50000 else log_text,
                    json.dumps(self._ar_skipped_codes, ensure_ascii=False),
                    json.dumps(self._ar_stats, ensure_ascii=False),
                    session_id,
                ))
        except Exception as e:
            print(f"Error finishing autoreview session: {e}")

    def _ar_get_sessions(self, limit: int = 50) -> list:
        try:
            with self._db_manager.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT * FROM autoreview_sessions
                    ORDER BY started_at DESC LIMIT ?
                ''', (limit,))
                return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            print(f"Error getting autoreview sessions: {e}")
            return []

    def _ar_get_session(self, session_id: str) -> Optional[dict]:
        try:
            with self._db_manager.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT * FROM autoreview_sessions WHERE id = ?', (session_id,))
                row = cursor.fetchone()
                return dict(row) if row else None
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Start / Stop
    # ------------------------------------------------------------------
    def _ar_confirm_rerun(self) -> bool:
        """Confirm a re-run of autoreview. Asks whether existing goods should
        be updated or left untouched (add-only mode). Returns True to proceed."""
        c = self.colors
        dlg = self.create_modal_dialog("Автоскладирование", 500, 270, scrollable=False)
        main = dlg.container

        tk.Label(main, text="⚠️ Внимание: Автоскладирование синхронизирует каталог GreenLeaf!",
                 font=self.font_bold_tuple, bg=c['bg'], fg=c['warning']).pack(pady=(18, 6))
        tk.Label(main, text="Бот добавит только новые товары (по коду ABC123),\n"
                            "существующие товары изменены не будут.",
                 font=self.font_normal_tuple, bg=c['bg'], fg=c['fg_secondary'],
                 justify="left").pack(pady=(0, 12))

        update_var = tk.BooleanVar(value=False)
        tk.Checkbutton(main, text="Обновить кол-ва товаров (нежелательно!)",
                       variable=update_var, font=self.font_normal_tuple,
                       bg=c['bg'], fg=c['error'], activebackground=c['bg'],
                       selectcolor=c['bg']).pack(pady=(0, 6))

        result = {}

        def on_start():
            result['add_only'] = not update_var.get()
            dlg.destroy()

        btn_f = tk.Frame(main, bg=c['bg'])
        btn_f.pack(pady=14)
        self._btn(btn_f, text="▶ Начать", command=on_start, style='success',
                  width=14, cursor='hand2').pack(side="left", padx=10)
        self._btn(btn_f, text="Отмена", command=dlg.destroy, style='neutral',
                  width=14, cursor='hand2').pack(side="left", padx=10)
        self.bind_dialog_keys(dlg, confirm_callback=on_start, cancel_callback=dlg.destroy)

        self.master.wait_window(dlg)
        if 'add_only' not in result:
            return False
        self._ar_add_only = result['add_only']
        if self._ar_add_only:
            self._ar_log_ui("warning", "Режим: только новые товары (существующие не меняются)")
        return True

    def _ar_start(self) -> None:
        # Do not allow a second worker to be started while the previous one is
        # still shutting down.
        if self._ar_thread and self._ar_thread.is_alive() and not self._ar_running:
            self._ar_log_ui("warning", "Предыдущий процесс еще завершается")
            return

        # If already running → confirm and stop
        if self._ar_running:
            if not messagebox.askyesno(
                "Приостановить",
                "Вы действительно хотите приостановить автоскладирование?\n\n"
                "Текущий процесс будет остановлен."
            ):
                return
            self._ar_stop()
            return

        # Check PV Bot concurrency — only true if bot thread is actively running
        if self._is_bot_running():
            messagebox.showwarning(
                "Автоскладирование",
                "PV Бот сейчас запущен. Остановите его перед запуском автоскладирования."
            )
            return

        # Always ask how existing goods are handled — including the FIRST run.
        # Previously the first run silently overwrote stock of existing goods.
        if not self._ar_confirm_rerun():
            return

        self._ar_clear_log()
        self._ar_log_ui("info", "Запуск автоскладирования...")
        self._ar_log_ui("info", "Проверка подключения...")
        self._ar_running = True
        self._ar_current_step = "init"
        self._ar_current_code = ""
        self._ar_error_message = ""
        self._ar_items_total = 0
        self._ar_items_parsed = 0
        self._ar_items_created = 0
        self._ar_items_updated = 0
        self._ar_items_skipped = 0
        self._ar_skipped_codes = []
        self._ar_stats = {}
        self._ar_started_at = datetime.now()
        self._ar_stop_event.clear()
        self._ar_add_only = getattr(self, '_ar_add_only', False)
        # Tk variables are read here, in the UI thread, before the worker
        # starts. The worker may still fetch missing credentials remotely.
        self._ar_credentials = (self.login.get(), self.password.get())

        self._ar_thread = threading.Thread(target=self._ar_run, daemon=True)
        self._track_worker(self._ar_thread)
        self._ar_thread.start()

    def _is_bot_running(self) -> bool:
        if hasattr(self, 'live_bot_thread') and self.live_bot_thread and self.live_bot_thread.is_alive():
            return True
        return False

    def _ar_stop(self) -> None:
        self._ar_stop_event.set()
        self._ar_start_btn.config(text="⏹ Останавливается...", state="disabled", bg=self.colors['bg_tertiary'])
        self._ar_log_ui("warning", "⏹ Остановка по запросу...")

    def _ar_wait(self, seconds: float) -> bool:
        """Wait without making stop requests wait for a long sleep."""
        return not self._ar_stop_event.wait(max(0, seconds))

    def _ar_close_browser(self) -> None:
        try:
            if self._ar_browser:
                self._ar_browser.close()
        except Exception:
            pass
        self._ar_browser = None
        try:
            if self._ar_playwright_ctx:
                self._ar_playwright_ctx.stop()
        except Exception:
            pass
        self._ar_playwright_ctx = None

    # ------------------------------------------------------------------
    # Main run loop
    # ------------------------------------------------------------------
    def _ar_run(self) -> None:
        session_id = str(uuid.uuid4())
        self._ar_create_session(session_id)

        try:
            login, password = getattr(self, "_ar_credentials", ("", ""))
            if not login or not password:
                import db
                dev_key = settings.get_or_create_device_key()
                login, password = db.get_credentials_from_supabase(dev_key)
            if not login or not password:
                raise RuntimeError("Учетные данные не настроены")

            url = "https://greenleaf-global.com/office/login?goto=%2Fdashboard"

            from pvm_core import ensure_playwright_browsers
            from playwright.sync_api import sync_playwright

            # Browser installation can take minutes on a slow connection. It
            # must never run in Tk's event loop.
            self._ar_current_step = "browser_setup"
            self._ar_log_ui("info", "Подготовка компонентов...")
            if not ensure_playwright_browsers():
                raise RuntimeError("Не удалось подготовить компоненты приложения")

            t_mult = float(getattr(self, 'timeout_multiplier', 1.0))

            with sync_playwright() as p:
                self._ar_playwright_ctx = p
                browser = p.chromium.launch(headless=True)
                self._ar_browser = browser
                page = browser.new_page()
                page.set_viewport_size({"width": 1280, "height": 720})

                if self._ar_stop_event.is_set():
                    raise InterruptedError("stopped")

                if not self._ar_login(page, url, login, password, t_mult=t_mult):
                    self._ar_log_ui("error", "Ошибка входа в кабинет поставщика")
                    self._ar_finish(session_id, "error", "Login failed")
                    return

                if self._ar_stop_event.is_set():
                    raise InterruptedError("stopped")

                scraped = self._ar_scrape_goods(page, t_mult=t_mult)
                if scraped is None:
                    self._ar_finish(session_id, "error", self._ar_error_message)
                    return

                self._ar_log_ui("success", f"Каталог получен: {len(scraped)} позиций")

                if self._ar_stop_event.is_set():
                    raise InterruptedError("stopped")

                self._ar_apply_changes(session_id, scraped)

                if not self._ar_stop_event.is_set():
                    try:
                        self._ar_log_ui("info", "Очистка названий товаров...")
                        cleaned = self._ar_auto_clean_names()
                        self._ar_log_ui("success", f"Очищено {cleaned} названий")
                    except Exception as cle:
                        self._ar_log_ui("warning", f"Ошибка очистки названий: {cle}")

        except InterruptedError:
            self._ar_log_ui("warning", "Автоскладирование остановлено")
            self._ar_finish(session_id, "stopped")
            return
        except Exception as e:
            err = str(e)[:200]
            self._ar_log_ui("error", f"Критическая ошибка: {err}")
            self._ar_error_message = err
            self._ar_finish(session_id, "error", err)
            return

        self._ar_finish(session_id, "done")

    def _ar_finish(self, session_id: str, status: str, error: str = "") -> None:
        self._ar_running = False
        self._ar_current_step = "idle"
        self._ar_current_code = ""
        self._ar_add_only = False
        if error:
            self._ar_error_message = error
        self._ar_finish_session(session_id, status, error)
        if self._ar_items_total == 0:
            try:
                with self._db_manager.get_connection() as conn:
                    conn.execute('DELETE FROM autoreview_sessions WHERE id = ?', (session_id,))
            except Exception as e:
                print(f"Error deleting empty autoreview session: {e}")
        self._ar_close_browser()
        if status == "error":
            self._ar_log_ui("error", "Завершено с ошибкой")
        elif status == "stopped":
            self._ar_log_ui("warning", "Остановлено")
        else:
            self._ar_log_ui("success", "Готово")
        try:
            # Database writes happen in the worker. Refresh Tk widgets only in
            # the main thread, so newly imported goods appear immediately.
            self._ui_call(self._ar_refresh_after_finish, delay_ms=500)
        except Exception:
            pass

    def _ar_refresh_after_finish(self) -> None:
        try:
            self._ar_refresh_history()
            if hasattr(self, "refresh_goods_list"):
                self.refresh_goods_list()
        except Exception as e:
            print(f"Error refreshing UI after autoreview: {e}")

    # ------------------------------------------------------------------
    # Login
    # ------------------------------------------------------------------
    def _ar_login(self, page, url: str, login: str, password: str,
                  retry: int = 0, max_retries: int = 6,
                  t_mult: float = 1.0) -> bool:
        if self._ar_stop_event.is_set():
            return False

        base_timeout_ms = 60000
        base_timeout = int(base_timeout_ms * t_mult)
        click_timeout = int(15000 * t_mult)

        try:
            if retry % 2 == 0:
                self._ar_log_ui("info", "Выполняется вход...")
                page.goto(url, timeout=base_timeout)
                page.wait_for_selector('input[name="login"]', state='visible', timeout=base_timeout)
                page.fill('input[name="login"]', login)
                page.fill('input[name="passwd"]', password)
                page.click(
                    'button[type="submit"], input[type="submit"], .btn-login, button:has-text("Войти")',
                    timeout=click_timeout,
                )
            else:
                self._ar_log_ui("info", "Выполняется вход...")
                target_url = "https://greenleaf-global.com/do.control/login"
                page.goto(target_url, timeout=base_timeout)
                page.wait_for_selector('input[name="login"]', state='visible', timeout=base_timeout)
                page.fill('input[name="login"]', login)
                page.fill('input[name="passwd"]', password)
                page.click('input[type="submit"]', timeout=click_timeout)

            page.wait_for_url(
                re.compile(r"https://greenleaf-global.com/.*(dashboard|do\.vshow#|office)"),
                timeout=base_timeout,
            )
            if not self._ar_wait(2 * t_mult):
                raise InterruptedError("stopped")
            self._ar_log_ui("success", "Вход выполнен")
            return True
        except Exception as e:
            err = str(e)
            if "timeout" in err.lower():
                err = f"Превышено время ожидания"
            self._ar_log_ui("error", f"Ошибка входа, повтор...")
            if retry < max_retries:
                wait = 30 * t_mult
                self._ar_log_ui("warning", f"Повторная попытка входа...")
                print(f"[AUTOREVIEW DEBUG] Login retry {retry + 1}/{max_retries}, wait {wait}s, error: {err}")
                if not self._ar_wait(wait):
                    return False
                return self._ar_login(page, url, login, password, retry + 1, max_retries, t_mult)
            self._ar_log_ui("error", "Вход не удался после всех попыток")
            return False

    # ------------------------------------------------------------------
    # Scrape goods
    # ------------------------------------------------------------------
    def _ar_dump_diag_text(self, page) -> None:
        """Краткая диагностика текущей страницы в лог."""
        try:
            url = page.url
            try:
                title = page.title()
            except Exception:
                title = ""
            snippet = ""
            try:
                snippet = re.sub(r'\s+', ' ', page.inner_text('body')).strip()[:300]
            except Exception:
                pass
            parts = [p for p in (title, url, snippet) if p]
            self._ar_log_ui("warning", f"Диагностика: {' | '.join(parts)}")
        except Exception as e:
            print(f"[AUTOREVIEW DEBUG] diag failed: {e}")

    @staticmethod
    def _ar_find_goods_rows(page):
        for sel in ('tr.goods-item', 'tr.good_item'):
            rows = page.query_selector_all(sel)
            if rows:
                return rows
        return []

    def _ar_wait_goods_rows(self, page, t_mult: float = 1.0):
        deadline = time.time() + 30 * t_mult
        while time.time() < deadline and not self._ar_stop_event.is_set():
            rows = self._ar_find_goods_rows(page)
            if rows:
                return rows
            if not self._ar_wait(1.5 * t_mult):
                raise InterruptedError("stopped")
        return self._ar_find_goods_rows(page)

    def _ar_wait_new_rows(self, page, prev_count: int, t_mult: float = 1.0, max_wait: float = 20.0):
        deadline = time.time() + max_wait * t_mult
        while time.time() < deadline and not self._ar_stop_event.is_set():
            if len(self._ar_find_goods_rows(page)) > prev_count:
                return True
            if not self._ar_wait(1.5 * t_mult):
                raise InterruptedError("stopped")
        return False

    def _ar_enter_partner(self, page, t_mult: float = 1.0,
                          base_timeout: int = 30000, click_timeout: int = 15000) -> bool:
        try:
            page.wait_for_selector('input[check_query="login_buy"]', state='attached', timeout=base_timeout)
            page.fill('input[check_query="login_buy"]', "kz44326234")
            ok = False
            deadline = time.time() + 20 * t_mult
            while time.time() < deadline and not self._ar_stop_event.is_set():
                if page.locator('text="Не найдено"').count() > 0:
                    self._ar_dump_diag_text(page)
                    return False
                if page.locator('input[type="submit"][value="Далее"]:not([disabled])').count() > 0:
                    ok = True
                    break
                if not self._ar_wait(0.5 * t_mult):
                    raise InterruptedError("stopped")
            if not ok:
                self._ar_dump_diag_text(page)
                return False
            page.click('input[type="submit"][value="Далее"]', timeout=click_timeout)
            try:
                page.wait_for_load_state("networkidle", timeout=int(20000 * t_mult))
            except Exception:
                pass
            return True
        except InterruptedError:
            raise
        except Exception as e:
            self._ar_log_ui("warning", f"Ошибка подключения: {e}")
            return False

    def _ar_scrape_goods(self, page, t_mult: float = 1.0):
        try:
            base_timeout = int(30000 * t_mult)
            click_timeout = int(15000 * t_mult)

            self._ar_current_step = "navigate"
            self._ar_log_ui("info", "Получение каталога...")
            try:
                page.wait_for_selector('a[href="#admin/shop/buy"]', state='attached', timeout=base_timeout)
                page.click('a[href="#admin/shop/buy"]', timeout=click_timeout)
            except Exception as e1:
                print(f"[AUTOREVIEW DEBUG] primary nav failed: {e1}")
                try:
                    page.click('a:has-text("Новая продажа")', timeout=click_timeout)
                except Exception as e2:
                    print(f"[AUTOREVIEW DEBUG] text nav failed: {e2}")
                    page.goto("https://greenleaf-global.com/do.vshow#admin/shop/buy", timeout=base_timeout)
            if not self._ar_wait(2 * t_mult):
                raise InterruptedError("stopped")

            self._ar_current_step = "login_buy"

            rows = []
            for attempt in range(1, 4):
                if self._ar_stop_event.is_set():
                    raise InterruptedError("stopped")
                if attempt > 1:
                    self._ar_log_ui("warning", f"Повторная попытка получения каталога ({attempt}/3)...")
                    try:
                        page.reload(timeout=base_timeout)
                    except Exception:
                        pass
                    if not self._ar_wait(2 * t_mult):
                        raise InterruptedError("stopped")

                if not self._ar_enter_partner(page, t_mult, base_timeout, click_timeout):
                    if attempt >= 3:
                        self._ar_log_ui("error", "Не удалось получить каталог поставщика")
                        self._ar_error_message = "Не удалось получить каталог поставщика"
                        return None
                    continue

                try:
                    page.wait_for_selector('input[name="query"]', state='visible', timeout=base_timeout)
                except Exception:
                    if attempt < 3:
                        self._ar_log_ui("warning", f"Повторная попытка получения каталога ({attempt + 1}/3)...")
                        continue
                    self._ar_dump_diag_text(page)
                    self._ar_log_ui("error", "Не удалось получить каталог поставщика")
                    self._ar_error_message = "Не удалось получить каталог поставщика"
                    return None

                rows = self._ar_wait_goods_rows(page, t_mult)
                if rows:
                    break
                if attempt < 3:
                    self._ar_log_ui("warning", f"Повторная попытка получения каталога ({attempt + 1}/3)...")

            if not rows:
                self._ar_dump_diag_text(page)
                self._ar_log_ui("error", "Не удалось получить каталог поставщика")
                self._ar_error_message = "Не удалось получить каталог поставщика"
                return None

            self._ar_current_step = "scraping"

            all_items = []
            page_num = 0
            max_pages = 200

            while not self._ar_stop_event.is_set() and page_num < max_pages:
                page_num += 1

                rows = self._ar_find_goods_rows(page)
                if len(rows) == 0:
                    self._ar_log_ui("info", "Каталог пуст")
                    break

                for row in rows:
                    if self._ar_stop_event.is_set():
                        break
                    try:
                        row_html = row.inner_html()
                        parsed = self._ar_parse_row(row_html)
                        if parsed:
                            all_items.append(parsed)
                            self._ar_items_parsed += 1
                            self._ar_current_code = parsed["code"]
                            if parsed.get("skipped"):
                                self._ar_items_skipped += 1
                                self._ar_skipped_codes.append(parsed["code"])
                    except Exception:
                        pass

                show_more = page.query_selector('a:has-text("Показать еще...")')
                if not show_more or not show_more.is_visible():
                    break

                try:
                    prev_count = len(self._ar_find_goods_rows(page))
                    show_more.click(timeout=click_timeout)
                    if not self._ar_wait(1.5 * t_mult):
                        raise InterruptedError("stopped")
                    if not self._ar_wait_new_rows(page, prev_count, t_mult):
                        break
                except Exception:
                    break

            self._ar_items_total = len(all_items)
            self._ar_log_ui("success", f"Каталог получен: {len(all_items)} позиций")
            if self._ar_skipped_codes:
                self._ar_log_ui("warning", f"Пропущено позиций: {len(self._ar_skipped_codes)}")

            return all_items

        except Exception as e:
            self._ar_log_ui("error", f"Ошибка получения каталога: {e}")
            self._ar_error_message = str(e)[:200]
            return None

    # ------------------------------------------------------------------
    # Row parsing
    # ------------------------------------------------------------------
    def _ar_parse_row(self, html: str):
        cells = re.findall(r'<td[^>]*>(.*?)</td>', html, re.DOTALL | re.IGNORECASE)
        if len(cells) < 6:
            return None

        parts = re.split(r'<br\s*/?>', cells[0], maxsplit=1, flags=re.IGNORECASE)
        if len(parts) < 2:
            return None
        raw_code = re.sub(r'<[^>]+>', '', parts[1]).strip()
        if not raw_code:
            return None

        if not PRODUCT_CODE_STRICT_RE.match(raw_code):
            return {
                "code": raw_code, "name": "", "sale_price": 0, "pv": 0,
                "quantity": 0, "barcode": "", "skipped": True,
            }

        code = raw_code

        name_match = re.search(
            r'<div[^>]*class="data-title"[^>]*>(.*?)</div>', cells[1], re.DOTALL | re.IGNORECASE
        )
        name_raw = name_match.group(1) if name_match else cells[1]
        name_raw = re.sub(r'<[^>]+>', ' ', name_raw)
        name = clean_product_name(name_raw)

        cell2_text = re.sub(r'<[^>]+>', ' ', cells[1]).strip()
        qty_match = AVAILABLE_QTY_RE.search(cell2_text)
        available_qty = int(qty_match.group(1)) if qty_match else 0

        price_match = re.search(r'<b>\s*([\d\s]+)', cells[3], re.DOTALL)
        if not price_match:
            price_match = re.search(r'([\d\s]+)', cells[3])
        discount_price = 0
        if price_match:
            try:
                discount_price = int(
                    price_match.group(1).replace(' ', '').replace('\u00a0', '')
                )
            except ValueError:
                discount_price = 0
        # Catalog price is the discounted price — sale price = price × multiplier
        sale_price = int(round(discount_price * self._ar_price_multiplier))

        cell5_clean = re.sub(r'<[^>]+>', ' ', cells[4]).strip()
        try:
            pv = float(cell5_clean)
        except ValueError:
            pv = 0

        return {
            "code": code, "name": name, "sale_price": sale_price,
            "pv": pv, "quantity": available_qty, "barcode": "",
            "skipped": False,
        }

    # ------------------------------------------------------------------
    # Apply changes to local DB
    # ------------------------------------------------------------------
    def _ar_apply_changes(self, session_id: str, items: list) -> None:
        self._ar_current_step = "applying"
        self._ar_log_ui("info", "Синхронизация данных...")

        valid_items = [it for it in items if not it.get("skipped")]
        if not valid_items:
            self._ar_log_ui("warning", "Каталог пуст, синхронизация не требуется")
            return

        created_count = 0
        updated_count = 0
        stats = {
            "new_codes": [],
            "updated_codes": [],
            "qty_decreased": [],
            "qty_increased": [],
            "price_changed": [],
            "unchanged": 0,
        }

        for item in valid_items:
            if self._ar_stop_event.is_set():
                self._ar_log_ui("warning", "Остановлено во время сохранения")
                break

            self._ar_current_code = item["code"]
            _, existing = self.goods_manager.get_good(item["code"])

            try:
                if existing:
                    if self._ar_add_only:
                        # Add-only mode: never modify existing goods
                        stats["unchanged"] += 1
                        self._ar_log_ui("debug", f"Пропущен существующий (режим: только новые): {item['code']}")
                        continue
                    need_update = (
                        existing.get("name") != item["name"]
                        or existing.get("sale_price", 0) != item["sale_price"]
                        or existing.get("pv", 0) != item["pv"]
                        or existing.get("quantity", 0) != item["quantity"]
                    )
                    if need_update:
                        self.goods_manager.add_good(
                            code=item["code"], name=item["name"],
                            pv=item["pv"], purchase_price=existing.get("purchase_price", 0),
                            sale_price=item["sale_price"], quantity=item["quantity"],
                            barcode=existing.get("barcode", ""),
                            set_quantity=True, user_name="Autoreview",
                        )
                        updated_count += 1
                        stats["updated_codes"].append(item["code"])
                        if existing.get("quantity", 0) != item["quantity"]:
                            diff = {
                                "code": item["code"],
                                "name": (item["name"] or existing.get("name", ""))[:60],
                                "was": existing.get("quantity", 0),
                                "became": item["quantity"],
                            }
                            if item["quantity"] < existing.get("quantity", 0):
                                stats["qty_decreased"].append(diff)
                            else:
                                stats["qty_increased"].append(diff)
                        if existing.get("sale_price", 0) != item["sale_price"]:
                            stats["price_changed"].append({
                                "code": item["code"],
                                "name": (item["name"] or existing.get("name", ""))[:60],
                                "was": existing.get("sale_price", 0),
                                "became": item["sale_price"],
                            })
                    else:
                        stats["unchanged"] += 1
                else:
                    self.goods_manager.add_good(
                        code=item["code"], name=item["name"],
                        pv=item["pv"], purchase_price=0,
                        sale_price=item["sale_price"], quantity=item["quantity"],
                        barcode="", set_quantity=True, user_name="Autoreview",
                    )
                    created_count += 1
                    stats["new_codes"].append({
                        "code": item["code"],
                        "name": item.get("name", "")[:60],
                        "quantity": item.get("quantity", 0),
                    })
            except Exception as e:
                self._ar_log_ui("error", f"Ошибка синхронизации {item['code']}: {e}")

        self._ar_items_created = created_count
        self._ar_items_updated = updated_count
        self._ar_stats = stats
        self._ar_log_ui("success",
                        f"Синхронизация завершена: создано {created_count}, обновлено {updated_count}, "
                        f"пропущено {self._ar_items_skipped}")

    # ------------------------------------------------------------------
    # Auto clean names
    # ------------------------------------------------------------------
    def _ar_auto_clean_names(self) -> int:
        updated = 0
        try:
            with self._db_manager.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT code, name FROM goods WHERE is_deleted = 0')
                rows = cursor.fetchall()
                for row in rows:
                    cleaned = clean_product_name(row['name'])
                    if cleaned and cleaned != row['name']:
                        cursor.execute(
                            'UPDATE goods SET name = ?, updated_at = ? WHERE code = ?',
                            (cleaned, datetime.now().isoformat(), row['code']),
                        )
                        updated += 1
        except Exception as e:
            self._ar_log_ui("error", f"Ошибка очистки названий: {e}")
        return updated
