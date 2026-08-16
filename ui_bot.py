# -*- coding: utf-8 -*-
"""
PVM.core - Bot/Automation Mixin
==================================
Scheduler, automation (Step1+Step2), Playwright, sessions, logging.
"""

import os
import re
import sys
import time
import threading
import subprocess
import tkinter as tk
from tkinter import messagebox, filedialog
from datetime import datetime, date, time as dtime

import pandas as pd

import settings
from pvm_core import load_progress, save_progress, clear_progress
from db import upload_session_to_supabase, queue_failed_session
from ui_lang import get_text, TRANSLATIONS


class BotAutomationMixin:
    """Bot/automation methods for GreenLeafApp."""

    # =========================================================================
    # SCHEDULER FUNCTIONS
    # =========================================================================
    def start_scheduler(self):
        """Start the scheduler thread."""
        if self.scheduler_running:
            return
            
        self.scheduler_running = True
        self._scheduler_generation = getattr(self, '_scheduler_generation', 0) + 1
        generation = self._scheduler_generation
        self.scheduler_thread = threading.Thread(
            target=self.scheduler_loop, args=(generation,), daemon=True)
        self._track_worker(self.scheduler_thread)
        self.scheduler_thread.start()
        self.log_message(f"📅 Планировщик запущен. Время: {self.scheduled_time_var.get()}", "info")

    def _start_scheduler_deferred(self):
        """Start the scheduler after the Tk mainloop is running
        (prevents 'main thread is not in main loop' races)."""
        if getattr(self, '_shutting_down', False):
            return
        if self.scheduler_enabled_var.get() and not self.scheduler_running:
            self.start_scheduler()

    def _tk_after(self, ms, func):
        """Thread-safe master.after wrapper — never raises on shutdown."""
        try:
            self.master.after(ms, func)
        except Exception:
            pass

    def _cfg(self, attr, **opts):
        """Configure a widget by attribute name; no-op when the widget
        does not exist (e.g. PV Bot UI not built for restricted users)."""
        w = getattr(self, attr, None)
        if w is not None:
            try:
                w.config(**opts)
            except Exception:
                pass

    def _restore_pvbot_buttons(self):
        """Restore PV Bot manual buttons to their permission-based state
        after an automation run finishes (scheduled runs must not unlock
        buttons for users without the 'pvbot_use' right)."""
        can_use = self.has_permission('pvbot_use')
        for attr in ('start_button', 'step2_button', 'delete_unpaid_button'):
            self._cfg(attr, state=tk.NORMAL if can_use else tk.DISABLED)
        self._cfg('stop_button', state=tk.DISABLED,
                  bg=self.colors['bg_tertiary'], fg=self.colors['fg_muted'])

    def stop_scheduler(self):
        """Stop the scheduler thread."""
        self.scheduler_running = False
        self._scheduler_generation = getattr(self, '_scheduler_generation', 0) + 1
        self.log_message("📅 Планировщик остановлен.", "info")

    def scheduler_loop(self, generation=None):
        """Main scheduler loop - checks every minute.

        C3: exits promptly when the central shutdown coordinator sets the
        'scheduler' stop event (user switch / app close)."""
        last_run_date = None
        last_retry_ts = 0.0
        config_generation = getattr(self, '_scheduler_config_generation', 0)
        initial_check = True
        stop_events = getattr(self, '_stop_events', None) or {}
        sched_stop = stop_events.get('scheduler')

        while (self.scheduler_running and
               (sched_stop is None or not sched_stop.is_set()) and
               generation == getattr(self, '_scheduler_generation', generation)):
            try:
                current_config_generation = getattr(self, '_scheduler_config_generation', 0)
                if current_config_generation != config_generation:
                    last_run_date = None
                    last_retry_ts = 0.0
                    config_generation = current_config_generation

                now = datetime.now()
                current_time = now.strftime("%H:%M")
                today = now.date()

                scheduled_time = self.cached_scheduled_time
                self._prevent_sleep()

                sched_dt = None
                try:
                    sched_dt = dtime(*[int(x) for x in scheduled_time.split(':')][:2])
                except Exception:
                    pass

                if initial_check and sched_dt is not None:
                    if now.time() >= sched_dt:
                        last_run_date = today
                    initial_check = False

                # Time to run and hasn't run today
                if last_run_date != today and sched_dt is not None and now.time() >= sched_dt:
                    # Retry pacing: attempt immediately, then every 5 minutes
                    if last_retry_ts and time.time() - last_retry_ts < 300:
                        time.sleep(30)
                        continue
                    last_retry_ts = time.time()

                    # Check watch directory first
                    watch_dir = self.cached_watch_directory
                    if not watch_dir:
                        self._sched_notify(
                            "⚠️ Папка отслеживания не настроена! Задайте её в Настройках → Автоматизация.",
                            "warning")
                        last_run_date = today
                        continue

                    if not os.path.isdir(watch_dir):
                        self._sched_notify(f"⚠️ Папка отслеживания недоступна: {watch_dir}", "warning")
                        continue

                    # Find today's file
                    file_path = self.find_todays_file()

                    # Auto-download stage: generate today's receipts Excel if enabled and file is missing
                    if not file_path and getattr(self, 'cached_auto_download_receipts', False):
                        self._sched_notify(
                            f"📥 Файл за {date.today().strftime('%d.%m.%Y')} не найден. Автозагрузка чеков за сегодня...",
                            "info")
                        file_path = self.download_todays_receipts()

                    if file_path:
                        self._sched_notify(f"📅 Настало время автозапуска. Найден файл: {os.path.basename(file_path)}",
                                           "success")
                        # Mark the day only when the automation is actually started
                        last_run_date = today
                        self._tk_after(0, lambda p=file_path: (self.report_file_path.set(p), self.run_scheduled_automation()))
                    else:
                        if now.hour >= 23:
                            self._sched_notify("⏰ Файл за сегодня так и не появился — попытки завершены.", "warning")
                            self._mark_scheduler_no_run("no_file")
                            last_run_date = today
                        else:
                            self._sched_notify("⏳ Файл за сегодня не найден — повтор через 5 минут", "info")

                # Sleep for 30 seconds before checking again
                time.sleep(30)

            except Exception as e:
                try:
                    self._sched_notify("⚠️ Ошибка планировщика", "error")
                except Exception:
                    pass
                print(f"[SCHEDULER ERROR] {e}")
                time.sleep(60)

    def _sched_notify(self, message, level="info"):
        """Non-blocking scheduler notification: journal + toast (no modal dialogs).

        Runs on the worker thread — dispatch via the C3 worker→Tk queue."""
        def _do():
            try:
                self.log_message(message, level, source="pv_bot")
                if hasattr(self, 'show_toast'):
                    ttype = {'success': 'success', 'warning': 'warning',
                             'error': 'error'}.get(level, 'info')
                    self.show_toast(message, ttype, duration=6000)
            except Exception:
                pass
        self._ui_call(_do)

    def _prevent_sleep(self):
        """Keep the PC awake while the scheduler is enabled (Windows)."""
        if sys.platform != 'win32':
            return
        if not getattr(self, '_prevent_sleep_ok', True):
            return
        try:
            import ctypes
            ES_CONTINUOUS = 0x80000000
            ES_SYSTEM_REQUIRED = 0x00000001
            ES_AWAKE = 0x00000002
            ctypes.windll.kernel32.SetThreadExecutionState(
                ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_AWAKE)
        except Exception:
            self._prevent_sleep_ok = False

    def show_scheduler_error(self, message, title):
        """Show a scheduler error message to the user."""
        self.log_message(f"⚠️ {message}", "warning")
        messagebox.showwarning(title, message)

    def test_scheduler(self):
        """Test the scheduler by running file check immediately."""
        self.log_message("🧪 Проверка планировщика...", "info")
        
        # Check watch directory
        watch_dir = self.watch_directory_var.get()
        if not watch_dir:
            self.show_scheduler_error(
                get_text('no_watch_dir', self.lang),
                get_text('scheduler_error_title', self.lang)
            )
            return
        
        if not os.path.isdir(watch_dir):
            self.show_scheduler_error(
                get_text('watch_dir_not_exist', self.lang).format(dir=watch_dir),
                get_text('scheduler_error_title', self.lang)
            )
            return
        
        # List all files in directory
        self.log_message(f"📂 Сканирование папки: {watch_dir}", "info")
        files = [f for f in os.listdir(watch_dir) if f.endswith(('.xlsx', '.xls'))]
        self.log_message(f"   Найдено {len(files)} Excel файл(ов)", "info")
        for f in files[:5]:
            self.log_message(f"   - {f}", "info")
        if len(files) > 5:
            self.log_message(f"   ... и ещё {len(files) - 5}", "info")
        
        # Try to find today's file
        file_path = self.find_todays_file()
        
        if file_path:
            self.log_message(f"✅ Найден файл: {os.path.basename(file_path)}", "success")
            
            # Ask if user wants to run automation
            if messagebox.askyesno(
                get_text('file_found_title', self.lang),
                get_text('run_automation_now', self.lang).format(file=os.path.basename(file_path))
            ):
                self.report_file_path.set(file_path)
                self.run_scheduled_automation()
        else:
            today_str = date.today().strftime("%d.%m.%Y")
            files_str = ', '.join(files[:3]) if files else 'none'
            if len(files) > 3:
                files_str += '...'
            self.show_scheduler_error(
                get_text('bot_file_not_found_detail', self.lang).format(date=today_str, dir=watch_dir, files=files_str),
                get_text('error_file_not_found_title', self.lang)
            )

    def find_todays_file(self):
        """Find a file with today's date in the watch directory."""
        watch_dir = self.watch_directory_var.get()
        if not watch_dir or not os.path.isdir(watch_dir):
            return None
        
        today_patterns = [
            date.today().strftime("%d.%m.%Y"),  # 04.01.2026
            date.today().strftime("%d-%m-%Y"),  # 04-01-2026
            date.today().strftime("%Y-%m-%d"),  # 2026-01-04
            date.today().strftime("%d.%m.%y"),  # 04.01.26
        ]
        
        for filename in os.listdir(watch_dir):
            if filename.endswith(('.xlsx', '.xls')):
                for pattern in today_patterns:
                    if pattern in filename:
                        return os.path.join(watch_dir, filename)
        
        return None

    def download_todays_receipts(self):
        """Download today's receipts as an Excel file into the watch directory.

        First stage of the PV Bot scheduler: generates the same Excel that the
        user manually exports via Sales → Excel, directly into the watch directory.
        Returns the saved file path or None.
        """
        try:
            watch_dir = self.cached_watch_directory or self.watch_directory_var.get()
            if not watch_dir or not os.path.isdir(watch_dir):
                self.log_message(f"⚠️ Автозагрузка чеков: папка отслеживания не существует: {watch_dir}", "warning", source="pv_bot")
                return None
            
            today = date.today()
            today_str = today.strftime("%d.%m.%Y")
            
            self.log_message(f"📥 Автозагрузка чеков за {today_str}...", "info", source="pv_bot")
            df = self.build_sales_export_df(today, today)
            if df is None or df.empty:
                self.log_message(f"⚠️ Автозагрузка чеков: продаж за {today_str} нет", "warning", source="pv_bot")
                return None
            
            filepath = os.path.join(watch_dir, f"{today_str}.xlsx")
            self.save_sales_excel_file(df, filepath)
            self.log_message(f"✅ Чек за {today_str} загружен: {filepath}", "success", source="pv_bot")
            return filepath
        except Exception as e:
            self.log_message(f"❌ Ошибка автозагрузки чеков: {e}", "error", source="pv_bot")
            print(f"[BOT DEBUG] Auto-download receipts error: {e}")
            return None

    def run_scheduled_automation(self):
        """Run automation from scheduler (with shutdown option)."""
        self.is_scheduled_run = True
        self.start_full_process_thread()

    def on_automation_complete(self):
        """Called when automation completes."""
        if hasattr(self, 'is_scheduled_run') and self.is_scheduled_run:
            self.is_scheduled_run = False
            shutdown_after = bool(self.shutdown_after_var.get())

            if shutdown_after:
                # Only shut down when the session report is confirmed saved
                # (uploaded to Supabase or persisted in the local queue).
                status = getattr(self, '_last_session_upload_status', None)
                if status in (None, 'uploaded', 'queued'):
                    self.log_message("💤 Выключение компьютера через 60 секунд...", "warning")
                    self._schedule(60000, self.shutdown_computer)
                else:
                    self.log_message("⛔ Автовыключение отменено: отчёт сессии не сохранён", "error")
                    try:
                        self.show_toast("Автовыключение отменено: отчёт сессии не отправлен", "error", duration=8000)
                    except Exception:
                        pass

    def _mark_scheduler_no_run(self, status):
        """Scheduler gave up for today (no file / not configured)."""
        self._pvbot_last_run = {
            'date': date.today().isoformat(),
            'time': datetime.now().strftime('%H:%M'),
            'status': status,
            'status_text': 'файл не найден' if status == 'no_file' else 'не запущен',
        }

    def shutdown_computer(self):
        """Shutdown the computer automatically."""
        self.log_message("💤 Выключение...", "warning")
        try:
            if sys.platform == 'win32':
                # Force shutdown on Windows
                subprocess.run(['shutdown', '/s', '/f', '/t', '0'], check=False)
            elif sys.platform == 'darwin':
                # macOS shutdown
                subprocess.run(['osascript', '-e', 'tell app "System Events" to shut down'], check=False)
            else:
                # Linux shutdown
                subprocess.run(['shutdown', '-h', 'now'], check=False)
        except Exception as e:
            self.log_message("⚠️ Ошибка выключения компьютера", "error")
            print(f"[BOT ERROR] Shutdown failed: {e}")
            # Fallback to os.system
            try:
                if sys.platform == 'win32':
                    os.system('shutdown /s /f /t 0')
                elif sys.platform == 'darwin':
                    os.system('osascript -e \'tell app "System Events" to shut down\'')
                else:
                    os.system('shutdown -h now')
            except:
                pass

    def browse_report_file(self):
        """File dialog."""
        if not self.has_permission('pvbot_use'):
            return
        file_path = filedialog.askopenfilename(filetypes=[("Excel files", "*.xlsx *.xls")])
        if file_path:
            self.report_file_path.set(file_path)

    def log_message(self, message, level="info", source="system"):
        """Log message to UI journal and track for session history. Terminal output suppressed for bot logs."""
        timestamp = datetime.now().strftime("%H:%M:%S")
        
        # Add to session logs (always enabled)
        if hasattr(self, 'session_start_time') and self.session_start_time:
            if hasattr(self, 'session_logs'):
                self.session_logs.append(f"[{timestamp}] {message}")
        
        # Detect if this is a bot message (PV Bot tab)
        is_pv_msg = source in ("pv_bot", "pvbot") or "Live Bot:" in message or "PV Bot:" in message

        if hasattr(self, "log_text") and self.log_text is not None and is_pv_msg:
            def _update():
                try:
                    if self.log_text.winfo_exists():
                        self.log_text.config(state="normal")
                        self.log_text.insert(tk.END, f"[{timestamp}] {message}\n", level)
                        self.log_text.see(tk.END)
                        self.log_text.config(state="disabled")
                except Exception:
                    pass
            # C3: called from worker threads — dispatch via the UI queue so
            # no Tk call ever happens during/after shutdown
            self._ui_call(_update)
        elif not is_pv_msg:
            # Only print non-bot system messages to terminal to reduce clutter;
            # debug-level noise (e.g. per-item refund logs) is suppressed.
            if level != 'debug':
                print(f"[{timestamp}] [LOG {level}] {message}")

    def update_status(self, message):
        """Update status bar."""
        self.status_bar.config(text=message)

    def track_purchase(self, user_id, items, success, has_discount=True):
        """Track a purchase for session history (always enabled)."""
        self.session_purchases.append({
            'user_id': user_id,
            'items': items,  # List of {code, qty, price}
            'success': success,
            'has_discount': has_discount
        })

    def add_successful_id(self, user_id):
        """Add to successful list."""
        self.successful_ids.append(user_id)
        try:
            self.master.after(0, self._render_successful_ids)
        except RuntimeError:
            self._render_successful_ids()

    def _render_successful_ids(self):
        """Re-render successful IDs widget (main thread only)."""
        try:
            if self.successful_ids_text.winfo_exists():
                self.successful_ids_text.config(state="normal")
                self.successful_ids_text.delete('1.0', tk.END)
                for uid in self.successful_ids:
                    self.successful_ids_text.insert(tk.END, f"{uid}\n")
                self.successful_ids_text.see(tk.END)
                self.successful_ids_text.config(state="disabled")
        except Exception:
            pass

    def add_failed_attempt(self, user_id, reason):
        """Add to failed list (deduplicated, thread-safe)."""
        if (user_id, reason) in self.failed_attempts:
            return
        self.failed_attempts.append((user_id, reason))
        try:
            self.master.after(0, self._render_failed_attempts)
        except RuntimeError:
            self._render_failed_attempts()

    def _render_failed_attempts(self):
        """Re-render failed attempts widget (main thread only)."""
        try:
            if self.failed_attempts_text.winfo_exists():
                self.failed_attempts_text.config(state="normal")
                self.failed_attempts_text.delete('1.0', tk.END)
                for uid, rsn in self.failed_attempts:
                    self.failed_attempts_text.insert(tk.END, f"{uid} ({rsn})\n")
                self.failed_attempts_text.see(tk.END)
                self.failed_attempts_text.config(state="disabled")
        except Exception:
            pass

    def show_confirm_popup(self, title, message):
        """Standardized confirmation modal. Returns True if confirmed."""
        result = [False]
        dialog = self.create_modal_dialog(title, width=450, height=250, scrollable=False)
        main = dialog.container
        c = self.colors
        
        bg = c['warning_bg']
        fg = c['warning']
        
        dialog.configure(bg=bg)
        main.configure(bg=bg)
        
        tk.Label(main, text=f"❓ {title}", font=self.font_bold_tuple, bg=bg, fg=fg).pack(pady=(25, 10))
        tk.Label(main, text=message, font=self.font_normal_tuple, bg=bg, fg=fg, wraplength=400, justify="center").pack(pady=5, padx=20)
        
        btn_frame = tk.Frame(main, bg=bg)
        btn_frame.pack(pady=20)
        
        def on_yes():
            result[0] = True
            dialog.destroy()
        
        def on_no():
            result[0] = False
            dialog.destroy()
        
        btn_yes = self._btn(btn_frame, text=get_text('yes', self.lang), command=on_yes, style='danger', width=12, cursor="hand2")
        btn_yes.pack(side="left", padx=10)
        
        btn_no = self._btn(btn_frame, text=get_text('no', self.lang), command=on_no, style='neutral', width=12, cursor="hand2")
        btn_no.pack(side="left", padx=10)
        
        def on_e_yes(e): btn_yes.config(bg=c['accent'])
        def on_l_yes(e): btn_yes.config(bg=c['error'])
        btn_yes.bind("<Enter>", on_e_yes)
        btn_yes.bind("<Leave>", on_l_yes)
        
        btn_yes.focus_set()
        self.bind_dialog_keys(dialog, confirm_callback=on_yes, cancel_callback=on_no)
        dialog.wait_window()
        return result[0]

    def stop_processing(self):
        """Stop automation - with confirmation popup."""
        # Show confirmation
        title = get_text('confirm_stop_title', self.lang) if 'confirm_stop_title' in TRANSLATIONS.get(self.lang, {}) else "⚠️ Confirm Stop"
        message = get_text('confirm_stop_message', self.lang) if 'confirm_stop_message' in TRANSLATIONS.get(self.lang, {}) else "Are you sure you want to stop the process?"
        
        if not self.show_confirm_popup(title, message):
            return  # User cancelled
        
        self.stop_event.set()
        self.is_processing = False
        self.log_message("⏹ Остановка...", "warning")
        
        # Do NOT close the page from the main thread: Playwright's sync context
        # belongs to the worker thread, and cross-thread calls produce the
        # greenlet "Cannot switch to a different thread" crash. Instead the
        # worker exits by itself within ~0.5s via stop-aware polling waits.

    def _wait_visible(self, page, selector, timeout):
        """wait_for_selector replacement that polls in small steps and exits
        fast when a stop was requested (no cross-thread playwright calls).

        Returns True when the element is visible; returns False on stop;
        raises TimeoutError on timeout (keeps original error semantics)."""
        # Playwright timeouts in this module are milliseconds.
        deadline = time.time() + (timeout / 1000.0)
        while time.time() < deadline:
            if self.stop_event.is_set():
                return False
            try:
                if page.locator(selector).first.is_visible():
                    return True
            except Exception:
                if self.stop_event.is_set():
                    return False
            time.sleep(0.4)
        raise TimeoutError(f"Element not visible: {selector}")

    # =========================================================================
    # FULL PROCESS: STEP 1 → STEP 2
    # =========================================================================
    def start_full_process_thread(self):
        """Start full process in thread."""
        # Prevent double start
        if self.is_processing:
            # Self-heal: if the process thread died without cleanup, reset state
            th = getattr(self, '_processing_thread', None)
            if th is None or not th.is_alive():
                self.is_processing = False
                self.log_message("🩹 Обнаружен зависший процесс — состояние сброшено", "warning", source="pv_bot")
            else:
                self.log_message("⚠️ Автоматизация уже запущена!", "warning")
                return
        
        # Concurrency guard: prevent running alongside autoreview
        if getattr(self, '_ar_running', False):
            self.log_message("⚠️ Автоскладирование сейчас запущено. Остановите его сначала.", "warning")
            return
        
        # Validate file is selected
        report_path = self.report_file_path.get().strip()
        if not report_path:
            if getattr(self, 'is_scheduled_run', False):
                self.log_message("⚠️ Файл Excel не выбран для запланированного запуска", "warning", source="pv_bot")
                self.is_scheduled_run = False
            else:
                title = get_text('error_no_file_title', self.lang)
                message = get_text('error_no_file_message', self.lang)
                messagebox.showerror(title, message)
            return
        
        # Validate file exists
        if not os.path.exists(report_path):
            if getattr(self, 'is_scheduled_run', False):
                self.log_message(f"⚠️ Файл Excel не найден: {report_path}", "warning", source="pv_bot")
                self.is_scheduled_run = False
            else:
                title = get_text('error_file_not_found_title', self.lang)
                message = get_text('error_file_not_found_message', self.lang).format(path=report_path)
                messagebox.showerror(title, message)
            return
        
        self.clear_logs()
        self.stop_event.clear()
        self.is_processing = True  # Track that automation is running
        self._cfg('start_button', state="disabled")
        self._cfg('step2_button', state="disabled")
        self._cfg('delete_unpaid_button', state="disabled")
        self._cfg('stop_button', state="normal", bg=self.colors['error'], fg="white")
        
        # Update tray icon
        self.update_tray_status('working', 'PVM.core - Запуск...')
        
        # Start session tracking
        self.start_session()

        thread = threading.Thread(target=self.run_full_process)
        thread.daemon = True
        self._processing_thread = thread
        self._track_worker(thread)
        thread.start()

    def start_session(self):
        """Initialize session tracking for history logs."""
        self.session_start_time = datetime.now()
        self.session_logs = []
        self.session_purchases = []
        self.was_interrupted = False
        self.recovered_orders = 0
        self.failed_items = {}
        self.insufficient_funds_orders = set()
        self.insufficient_funds_partners = set()
        self.session_blacklist = {}  # Clear smart retry blacklist for new session
        self._last_session_upload_status = None  # None/uploaded/queued/failed

    def end_session(self):
        """End session and save history log (always enabled)."""
        report_text = None
        if self.session_start_time:
            report_text = self.save_session_history()
        self.session_start_time = None
        return report_text

    def save_session_history(self):
        """Save detailed session history to file."""
        # Use settings.LOGS_DIR from front (cache folder) - always enabled
        logs_dir = settings.LOGS_DIR
            
        if not os.path.exists(logs_dir):
            try:
                os.makedirs(logs_dir)
            except:
                logs_dir = settings.BASE_DIR  # Fallback to launcher directory
        
        end_time = datetime.now()
        start_time = self.session_start_time
        
        # Calculate stats
        total_purchases = len(self.session_purchases)
        successful = sum(1 for p in self.session_purchases if p['success'])
        failed = total_purchases - successful
        
        duration_seconds = (end_time - start_time).total_seconds()
        duration_minutes = duration_seconds / 60
        avg_time_per_purchase = duration_seconds / max(successful, 1)
        
        # Count items and gather user stats
        item_counts = {}
        user_stats = {}
        total_items = 0
        total_sales = 0.0  # Total sales amount
        items_per_order = []  # List of item counts per successful order
        
        for purchase in self.session_purchases:
            if not purchase['success']:
                continue
            user_id = purchase['user_id']
            if user_id not in user_stats:
                user_stats[user_id] = {'purchases': 0, 'items': 0, 'total_price': 0}
            user_stats[user_id]['purchases'] += 1
            
            order_item_count = 0
            for item in purchase.get('items', []):
                code = item.get('code', 'UNKNOWN')
                qty = item.get('qty', 1)
                price = item.get('price', 0)
                
                item_counts[code] = item_counts.get(code, 0) + qty
                user_stats[user_id]['items'] += qty
                user_stats[user_id]['total_price'] += price * qty
                total_items += qty
                total_sales += price * qty
                order_item_count += qty
            
            items_per_order.append(order_item_count)
        
        # Calculate item statistics
        avg_items_per_order = total_items / max(successful, 1)
        min_items_per_order = min(items_per_order) if items_per_order else 0
        max_items_per_order = max(items_per_order) if items_per_order else 0
        unique_items_count = len(item_counts)
        
        # Find most/least purchased items
        most_item = max(item_counts.items(), key=lambda x: x[1]) if item_counts else ('N/A', 0)
        least_item = min(item_counts.items(), key=lambda x: x[1]) if item_counts else ('N/A', 0)
        
        # Find users with most/least purchases
        most_user = max(user_stats.items(), key=lambda x: x[1]['purchases']) if user_stats else (None, {})
        least_user = min(user_stats.items(), key=lambda x: x[1]['purchases']) if user_stats else (None, {})
        
        # Get login ID (e.g., s240408)
        login_id = self.login.get().strip() or 'unknown'
        
        # Generate cache-like filename: hexcode.dat (disguised as cache file)
        import hashlib
        hash_input = f"{end_time.isoformat()}_{login_id}_{total_purchases}"
        digest: str = hashlib.md5(hash_input.encode()).hexdigest()
        file_hash = digest[:12].upper()
        filename = f"{file_hash}.dat"
        filepath = os.path.join(logs_dir, filename)
        
        # Build report
        report = []
        report.append("╔══════════════════════════════════════════════════════════════╗")
        report.append("║               PVM.CORE - SESSION REPORT                      ║")
        report.append("╚══════════════════════════════════════════════════════════════╝")
        report.append("")
        report.append(f"  Login: {login_id}")
        report.append(f"  Device: {self.device_key}")
        report.append("")
        report.append("──────────────────────────────────────────────────────────────────")
        report.append("  TIME & DURATION")
        report.append("──────────────────────────────────────────────────────────────────")
        report.append(f"  Date:       {start_time.strftime('%d.%m.%Y')}")
        report.append(f"  Start:      {start_time.strftime('%H:%M:%S')}")
        report.append(f"  End:        {end_time.strftime('%H:%M:%S')}")
        report.append(f"  Duration:   {int(duration_minutes)} min {int(duration_seconds % 60)} sec")
        report.append(f"  Avg/Order:  {avg_time_per_purchase:.1f} sec")
        report.append("")
        report.append("──────────────────────────────────────────────────────────────────")
        report.append("  ORDERS SUMMARY")
        report.append("──────────────────────────────────────────────────────────────────")
        report.append(f"  Total Orders:      {total_purchases}")
        report.append(f"  ✅ Successful:     {successful}")
        report.append(f"  ❌ Failed:         {failed}")
        if self.recovered_orders > 0:
            report.append(f"  🔄 Recovered:      {self.recovered_orders}")
        report.append(f"  💰 Total Sales:    {total_sales:.2f}")
        report.append("")
        report.append("──────────────────────────────────────────────────────────────────")
        report.append("  ITEM STATISTICS")
        report.append("──────────────────────────────────────────────────────────────────")
        report.append(f"  Total Items:       {total_items}")
        report.append(f"  Unique Products:   {unique_items_count}")
        report.append(f"  Avg Items/Order:   {avg_items_per_order:.1f}")
        report.append(f"  Min Items/Order:   {min_items_per_order}")
        report.append(f"  Max Items/Order:   {max_items_per_order}")
        report.append(f"  Most Purchased:    {most_item[0]} ({most_item[1]} pcs)")
        report.append(f"  Least Purchased:   {least_item[0]} ({least_item[1]} pcs)")
        
        # Add failed items section if any
        if self.failed_items:
            report.append("")
            report.append("──────────────────────────────────────────────────────────────────")
            report.append("  ⚠️ FAILED ITEMS (Out of Stock / Invalid)")
            report.append("──────────────────────────────────────────────────────────────────")
            # Sort by count descending
            sorted_failed = sorted(self.failed_items.items(), key=lambda x: x[1], reverse=True)
            for item_code, count in sorted_failed:
                report.append(f"  {item_code}: {count} order(s) affected")
        
        report.append("")
        report.append("──────────────────────────────────────────────────────────────────")
        report.append("  USER STATISTICS")
        report.append("──────────────────────────────────────────────────────────────────")
        report.append(f"  Unique Users:      {len(user_stats)}")
        if most_user[0]:
            report.append(f"  Top User:          {most_user[0]}")
            report.append(f"                     {most_user[1]['purchases']} orders, {most_user[1]['items']} items")
        if least_user[0] and least_user[0] != most_user[0]:
            report.append(f"  Bottom User:       {least_user[0]}")
            report.append(f"                     {least_user[1]['purchases']} orders, {least_user[1]['items']} items")
        
        # Top 5 clients by spending (for analytics parsing)
        if user_stats:
            sorted_by_spending = sorted(user_stats.items(), key=lambda x: x[1]['total_price'], reverse=True)[:5]
            report.append("")
            report.append("──────────────────────────────────────────────────────────────────")
            report.append("  TOP CLIENTS (by spending)")
            report.append("──────────────────────────────────────────────────────────────────")
            for rank, (user_id, stats) in enumerate(sorted_by_spending, 1):
                report.append(f"  {rank}. {user_id}: {stats['purchases']} purchases, {stats['items']} items, {stats['total_price']:.0f} тг")
        
        # Insufficient funds clients (for analytics parsing)
        if hasattr(self, 'insufficient_funds_orders') and self.insufficient_funds_orders:
            report.append("")
            report.append("──────────────────────────────────────────────────────────────────")
            report.append("  ⚠️ INSUFFICIENT FUNDS ORDERS")
            report.append("──────────────────────────────────────────────────────────────────")
            for order_id in self.insufficient_funds_orders:
                report.append(f"  Order: {order_id}")
        
        report.append("")
        report.append("══════════════════════════════════════════════════════════════════")
        report.append("                        DETAILED LOGS")
        report.append("══════════════════════════════════════════════════════════════════")
        report.append("")
        
        for log in self.session_logs:
            report.append(log)
        
        report.append("")
        report.append("══════════════════════════════════════════════════════════════════")
        report.append("                       END OF REPORT")
        report.append("══════════════════════════════════════════════════════════════════")
        
        # Write to file (silently - don't show to user)
        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write('\n'.join(report))
            # Silent save - don't log to user
        except Exception as e:
            pass  # Silent fail
        
        # Upload to Supabase - columns grouped logically:
        # 1. Identity → 2. Time → 3. Orders → 4. Items → 5. Users → 6. Status
        # Upload status is explicit (uploaded/queued/failed) so auto-shutdown
        # can be gated on the report actually being saved.
        if total_purchases == 0:
            # Nothing to report (bot was stopped before any order)
            self._last_session_upload_status = None
            clear_progress()
            return None
        try:
            session_data = {
                # 1. IDENTITY (device_key added in upload function)
                'login': login_id,
                
                # 2. TIME
                'date': start_time.strftime('%Y-%m-%d'),
                'start_time': start_time.strftime('%H:%M:%S'),
                'end_time': end_time.strftime('%H:%M:%S'),
                'duration_seconds': int(duration_seconds),
                'avg_seconds_per_order': round(avg_time_per_purchase, 2),
                
                # 3. ORDERS
                'total_orders': total_purchases,
                'successful': successful,
                'failed': failed,
                'recovered': self.recovered_orders,
                'total_sales': round(total_sales, 2),
                
                # 4. ITEMS
                'total_items': total_items,
                'unique_items': unique_items_count,
                'avg_items_per_order': round(avg_items_per_order, 2),
                'min_items_per_order': min_items_per_order,
                'max_items_per_order': max_items_per_order,
                'most_item': most_item[0] if most_item else '',
                'most_item_count': most_item[1] if most_item else 0,
                'least_item': least_item[0] if least_item else '',
                'least_item_count': least_item[1] if least_item else 0,
                
                # 5. USERS
                'unique_users': len(user_stats),
                'top_user': most_user[0] if most_user[0] else '',
                'top_user_orders': most_user[1].get('purchases', 0) if most_user[0] else 0,
                'top_user_items': most_user[1].get('items', 0) if most_user[0] else 0,
                
                # 6. SESSION STATUS
                'interrupted': hasattr(self, 'was_interrupted') and self.was_interrupted,
                'resumed_from': self.resumed_from or '',
            }
            
            # Retry a few times; if it still fails, persist the report in the
            # local queue (retried on next launch) so nothing is lost.
            ok = upload_session_to_supabase(self.device_key, session_data, attempts=3)
            if ok:
                self._last_session_upload_status = 'uploaded'
                self.log_message("📤 Отчёт сессии отправлен на сервер", "info", source="pv_bot")
            else:
                queued = queue_failed_session(self.device_key, session_data)
                if queued:
                    self._last_session_upload_status = 'queued'
                    self.log_message("⚠️ Отчёт сохранён локально, будет отправлен при следующем запуске", "warning", source="pv_bot")
                else:
                    self._last_session_upload_status = 'failed'
                    self.log_message("❌ Не удалось сохранить отчёт сессии", "error", source="pv_bot")
                    print("[BOT ERROR] Session upload failed AND local queue write failed")
        except Exception as e:
            self._last_session_upload_status = 'failed'
            self.log_message("❌ Не удалось сохранить отчёт сессии", "error", source="pv_bot")
            print(f"[BOT ERROR] Session upload: {e}")
        
        # Clear progress after successful completion
        clear_progress()
        return self._last_session_upload_status

    def _upload_resumed_session(self, summary):
        """Upload partial/interrupted session data to Supabase on resume."""
        try:
            session_data = {
                'login': self.login.get().strip() or 'unknown',
                'date': datetime.now().strftime('%Y-%m-%d'),
                'start_time': summary.get('start_time', '00:00:00'),
                'end_time': datetime.now().strftime('%H:%M:%S'),
                'duration_seconds': 0,
                'avg_seconds_per_order': 0,
                'total_orders': summary.get('total_orders', 0),
                'successful': summary.get('successful', 0),
                'failed': summary.get('failed', 0),
                'recovered': summary.get('recovered_orders', 0),
                'total_sales': summary.get('total_sales', 0),
                'total_items': summary.get('total_items', 0),
                'unique_items': 0,
                'avg_items_per_order': 0,
                'min_items_per_order': 0,
                'max_items_per_order': 0,
                'most_item': '',
                'most_item_count': 0,
                'least_item': '',
                'least_item_count': 0,
                'unique_users': summary.get('unique_users', 0),
                'top_user': '',
                'top_user_orders': 0,
                'top_user_items': 0,
                'interrupted': True,
                'resumed_from': getattr(self, 'resumed_from', '') or '',
            }
            ok = upload_session_to_supabase(self.device_key, session_data, attempts=3)
            if ok:
                self.log_message("📤 Данные прерванной сессии отправлены на сервер", "info")
            else:
                queued = queue_failed_session(self.device_key, session_data)
                if queued:
                    self.log_message("⚠️ Данные прерванной сессии сохранены локально, отправка при следующем запуске", "warning")
                else:
                    self.log_message("⚠️ Не удалось сохранить данные прерванной сессии", "warning")
                    print("[BOT ERROR] Upload session (resume) failed AND queue write failed")
        except Exception as e:
            self.log_message(f"⚠️ Не удалось отправить данные сессии", "warning")
            print(f"[BOT ERROR] Upload session: {e}")

    def run_full_process(self):
        """Run Step 1 then Step 2 automatically with REAL automation."""
        try:
            from playwright.sync_api import sync_playwright
        except Exception as e:
            print(f"[BOT ERROR] Playwright import failed: {e}")
            self.log_message("❌ Ошибка инициализации браузера (Playwright)", "error", source="pv_bot")
            self.was_interrupted = True
            self._finish_automation_run()
            return

        try:
            with sync_playwright() as p:
                browser = None
                self.current_browser = None  # Track for force stop
                self.current_page = None
                try:
                    # Check stop before starting
                    if self.stop_event.is_set():
                        self.log_message("⏹ Остановка перед запуском.", "warning", source="pv_bot")
                        return

                    is_headless = self.headless_var.get()
                    mode_text = "фоновый" if is_headless else "видимый"
                    self.log_message(f"🚀 Запуск браузера ({mode_text} режим)...", "info", source="pv_bot")
                    browser = p.chromium.launch(headless=is_headless)
                    self.current_browser = browser  # Save reference for force stop
                    page = browser.new_page()
                    self.current_page = page
                    page.set_viewport_size({"width": 1280, "height": 720})

                    # Suppress print dialogs
                    page.add_init_script("Object.defineProperty(window, 'print', { value: function() {} });")

                    # === LOGIN ===
                    self.log_message("🔐 Авторизация в GreenLeaf...", "info", source="pv_bot")
                    if not self._login(page):
                        if self.stop_event.is_set():
                            return
                        self.was_interrupted = True
                        self.log_message("❌ Ошибка входа! Проверьте логин/пароль.", "error", source="pv_bot")
                        return

                    # ===================================================================
                    # STEP 1: PROCESS EXCEL REPORT
                    # ===================================================================
                    self.log_message("\n" + "="*60, "info", source="pv_bot")
                    self.log_message("=== ЭТАП 1: Формирование корзин из Excel ===", "success", source="pv_bot")
                    self.log_message("="*60, "info", source="pv_bot")
                    self.update_status("Этап 1: Обработка Excel...")

                    file_path = self.report_file_path.get()
                    if not file_path or not os.path.exists(file_path):
                        self.log_message("⚠️ Файл Excel не выбран - Пропуск Этапа 1", "warning", source="pv_bot")
                    else:
                        self._run_step1(page, file_path)
                    if self.stop_event.is_set():
                        self.log_message("⏸ Процесс остановлен пользователем.", "warning", source="pv_bot")
                        return

                    # ===================================================================
                    # STEP 2: PROCESS UNPAID ORDERS
                    # ===================================================================
                    self.log_message("\n" + "="*60, "info", source="pv_bot")
                    self.log_message("🚀 ПЕРЕХОД К ЭТАПУ 2: Оплата заказов...", "success", source="pv_bot")
                    self.log_message("="*60, "info", source="pv_bot")
                    self.update_status("Этап 2: Оплата заказов...")

                    # Force navigation to dashboard to reset state after Step 1
                    try:
                        self.log_message("🔄 Возврат на главную для сброса состояния...", "info", source="pv_bot")
                        page.goto("https://greenleaf-global.com/office/dashboard", timeout=30000)
                        time.sleep(2 * self.delay_multiplier)
                    except:
                        pass

                    self._run_step2(page)

                    self.log_message("\n" + "="*60, "success", source="pv_bot")
                    self.log_message("🎉 ВСЕ ЭТАПЫ АВТОМАТИЗАЦИИ ЗАВЕРШЕНЫ!", "success", source="pv_bot")
                    self.log_message("="*60, "success", source="pv_bot")

                    # Clear progress on successful completion
                    clear_progress()
                    self.was_interrupted = False

                except Exception as e:
                    self.log_message(f"❌ Критическая ошибка", "error", source="pv_bot")
                    print(f"[BOT ERROR] Critical: {e}")
                    self.was_interrupted = True
                    # traceback.print_exc() # Removed as per user request to keep terminal clean
                finally:
                    self.current_browser = None
                    self.current_page = None
                    if browser:
                        try:
                            browser.close()
                        except:
                            pass
                    self._finish_automation_run()
        except Exception as e:
            print(f"[BOT ERROR] Launch failed: {e}")
            self.log_message("❌ Ошибка запуска браузера", "error", source="pv_bot")
            self.was_interrupted = True
            self._finish_automation_run()

    def _finish_automation_run(self):
        """Reset state, re-enable UI and fire completion after a run (always called)."""
        try:
            self.end_session()
            if hasattr(self, '_set_process_status'):
                if self.was_interrupted or self.stop_event.is_set():
                    self._tk_after(0, lambda: self._set_process_status("aborted"))
                else:
                    self._tk_after(0, lambda: self._set_process_status("completed"))
            if hasattr(self, '_refresh_progress_display'):
                self._tk_after(0, self._refresh_progress_display)
            self.is_processing = False
            self.update_tray_status('ready')
            self.current_progress = None
            self.resumed_from = None
            self._step2_progress_init = False
            self._tk_after(0, self._restore_pvbot_buttons)
            self._tk_after(0, lambda: self.update_status("Автоматизация завершена."))
            self._tk_after(0, self.on_automation_complete)
        except Exception as e:
            print(f"[BOT ERROR] Finish: {e}")

    def start_step2_only_thread(self):
        """Start Step 2 (payment processing) only - no Excel file needed."""
        if self.is_processing:
            self.log_message("⚠️ Автоматизация уже запущена!", "warning")
            return
        
        if getattr(self, '_ar_running', False):
            self.log_message("⚠️ Автоскладирование сейчас запущено. Остановите его сначала.", "warning")
            return
        
        self.clear_logs()
        self.stop_event.clear()
        self.is_processing = True
        self._cfg('start_button', state="disabled")
        self._cfg('step2_button', state="disabled")
        self._cfg('delete_unpaid_button', state="disabled")
        self._cfg('stop_button', state="normal", bg=self.colors['error'], fg="white")
        
        self.update_tray_status('working', 'PVM.core - Шаг 2...')
        self.start_session()

        thread = threading.Thread(target=self.run_step2_only)
        thread.daemon = True
        thread.start()

    def run_step2_only(self):
        """Run only Step 2 (pay unpaid orders) - skip Excel processing."""
        try:
            from playwright.sync_api import sync_playwright
        except Exception as e:
            print(f"[BOT ERROR] Playwright import failed: {e}")
            self.log_message("❌ Ошибка инициализации браузера (Playwright)", "error", source="pv_bot")
            self.was_interrupted = True
            self._finish_automation_run()
            return

        try:
            with sync_playwright() as p:
                browser = None
                self.current_browser = None
                self.current_page = None
                try:
                    if self.stop_event.is_set():
                        self.log_message("⏹ Остановка перед запуском.", "warning", source="pv_bot")
                        return
                    
                    is_headless = self.headless_var.get()
                    mode_text = "фоновый" if is_headless else "видимый"
                    self.log_message(f"🚀 Запуск браузера ({mode_text} режим)...", "info", source="pv_bot")
                    browser = p.chromium.launch(headless=is_headless)
                    self.current_browser = browser
                    page = browser.new_page()
                    self.current_page = page
                    page.set_viewport_size({"width": 1280, "height": 720})
                    page.add_init_script("Object.defineProperty(window, 'print', { value: function() {} });")

                    self.log_message("🔐 Авторизация в GreenLeaf...", "info", source="pv_bot")
                    if not self._login(page):
                        if self.stop_event.is_set():
                            return
                        self.log_message("❌ Ошибка входа! Проверьте логин/пароль.", "error", source="pv_bot")
                        return

                    self.log_message("\n" + "="*60, "info", source="pv_bot")
                    self.log_message("=== ШАГ 2: Оплата заказов ===", "success", source="pv_bot")
                    self.log_message("="*60, "info", source="pv_bot")
                    self.update_status("Шаг 2: Оплата заказов...")

                    try:
                        page.goto("https://greenleaf-global.com/office/dashboard", timeout=30000)
                        time.sleep(2 * self.delay_multiplier)
                    except:
                        pass

                    self._run_step2(page)

                    self.log_message("\n" + "="*60, "success", source="pv_bot")
                    self.log_message("🎉 ШАГ 2 ЗАВЕРШЁН!", "success", source="pv_bot")
                    self.log_message("="*60, "success", source="pv_bot")
                    
                    clear_progress()
                    self.was_interrupted = False

                except Exception as e:
                    self.log_message(f"❌ Критическая ошибка", "error", source="pv_bot")
                    print(f"[BOT ERROR] Critical: {e}")
                    self.was_interrupted = True
                finally:
                    self.current_browser = None
                    self.current_page = None
                    if browser:
                        try:
                            browser.close()
                        except:
                            pass
                    self._finish_automation_run()
        except Exception as e:
            print(f"[BOT ERROR] Launch failed: {e}")
            self.log_message("❌ Ошибка запуска браузера", "error", source="pv_bot")
            self.was_interrupted = True
            self._finish_automation_run()

    def start_delete_unpaid_thread(self):
        """Start thread to delete all unpaid orders."""
        if self.is_processing:
            self.log_message("⚠️ Автоматизация уже запущена!", "warning")
            return
        
        if getattr(self, '_ar_running', False):
            self.log_message("⚠️ Автоскладирование сейчас запущено. Остановите его сначала.", "warning")
            return
        
        if not messagebox.askyesno(
                "Удаление заказов",
                "Удалить ВСЕ неоплаченные заказы в аккаунте GreenLeaf?\n\n"
                "Это действие выполняется на сайте поставщика и необратимо."):
            return
        
        self.clear_logs()
        self.stop_event.clear()
        self.is_processing = True
        self._cfg('start_button', state="disabled")
        self._cfg('step2_button', state="disabled")
        self._cfg('delete_unpaid_button', state="disabled")
        self._cfg('stop_button', state="normal", bg=self.colors['error'], fg="white")
        
        self.update_tray_status('working', 'PVM.core - Удаление...')
        self.start_session()

        thread = threading.Thread(target=self.run_delete_unpaid_orders)
        thread.daemon = True
        thread.start()

    def run_delete_unpaid_orders(self):
        """Delete all unpaid orders from the purchases page."""
        from playwright.sync_api import sync_playwright        
        with sync_playwright() as p:
            browser = None
            self.current_browser = None
            self.current_page = None
            try:
                if self.stop_event.is_set():
                    self.log_message("⏹ Остановка перед запуском.", "warning", source="pv_bot")
                    return
                
                is_headless = self.headless_var.get()
                self.log_message(f"🚀 Запуск браузера ({'фоновый' if is_headless else 'видимый'} режим)...", "info", source="pv_bot")
                browser = p.chromium.launch(headless=is_headless)
                self.current_browser = browser
                page = browser.new_page()
                self.current_page = page
                page.set_viewport_size({"width": 1280, "height": 720})
                page.add_init_script("Object.defineProperty(window, 'print', { value: function() {} });")

                self.log_message("🔐 Авторизация в GreenLeaf...", "info", source="pv_bot")
                if not self._login(page):
                    if self.stop_event.is_set():
                        return
                    self.log_message("❌ Ошибка входа! Проверьте логин/пароль.", "error", source="pv_bot")
                    return

                self.log_message("🗑 Удаление неоплаченных заказов...", "info", source="pv_bot")
                self.update_status("Удаление неоплаченных заказов...")

                base_timeout = 15000 * self.timeout_multiplier
                long_timeout = 30000 * self.timeout_multiplier

                try:
                    page.goto("https://greenleaf-global.com/office/dashboard", timeout=base_timeout)
                    time.sleep(2 * self.delay_multiplier)
                except:
                    pass

                page.goto("https://greenleaf-global.com/office/dashboard", timeout=base_timeout)
                page.wait_for_url("**/dashboard", timeout=base_timeout)
                time.sleep(1 * self.delay_multiplier)

                page.click('a[href="#admin/shop/buy"]', timeout=base_timeout)
                if not self._wait_visible(page, 'input[check_query="login_buy"]', base_timeout):
                    return
                time.sleep(1 * self.delay_multiplier)

                page.goto("https://greenleaf-global.com/office/history?page=1&payment_status=not_paid&type=purchase", timeout=long_timeout)
                page.wait_for_load_state("networkidle", timeout=long_timeout)
                time.sleep(2 * self.delay_multiplier)

                total_deleted = 0
                max_pages = (self.max_empty_pages_var.get() if hasattr(self, 'max_empty_pages_var') else 3) * 10

                for page_num in range(1, max_pages + 1):
                    if self.stop_event.is_set():
                        self.log_message("⏹ Удаление прервано пользователем.", "warning", source="pv_bot")
                        break

                    self.log_message(f"📄 Страница {page_num}...", "info", source="pv_bot")

                    orders = page.query_selector_all('a[href*="/office/history/detail/"]')
                    if not orders:
                        self.log_message("✅ Нет неоплаченных заказов.", "success", source="pv_bot")
                        break

                    order_urls = []
                    for a in orders:
                        href = a.get_attribute('href')
                        if href:
                            full_url = f"https://greenleaf-global.com{href}" if href.startswith('/') else href
                            order_urls.append(full_url)

                    self.log_message(f"   Найдено {len(order_urls)} заказов на странице {page_num}", "info", source="pv_bot")

                    for url in order_urls:
                        if self.stop_event.is_set():
                            break
                        try:
                            self.log_message(f"   Открытие: {url}", "debug", source="pv_bot")
                            page.goto(url, timeout=long_timeout)
                            page.wait_for_load_state("networkidle", timeout=long_timeout)
                            time.sleep(1 * self.delay_multiplier)

                            delete_btn = page.query_selector('button:has-text("Удалить"), a:has-text("Удалить"), input[value="Удалить"]')
                            if delete_btn:
                                delete_btn.click()
                                time.sleep(1 * self.delay_multiplier)
                                total_deleted += 1
                                self.log_message(f"   ✅ Удалён заказ", "success", source="pv_bot")
                            else:
                                self.log_message(f"   ⏭️ Кнопка удаления не найдена", "warning", source="pv_bot")
                        except Exception as e:
                            self.log_message(f"   ⚠️ Ошибка при удалении", "warning", source="pv_bot")

                    if self.stop_event.is_set():
                        break

                    next_url = f"https://greenleaf-global.com/office/history?page={page_num + 1}&payment_status=not_paid&type=purchase"
                    try:
                        page.goto(next_url, timeout=long_timeout)
                        page.wait_for_load_state("networkidle", timeout=long_timeout)
                        time.sleep(1 * self.delay_multiplier)
                    except:
                        break

                self.log_message(f"\n{'='*60}", "info", source="pv_bot")
                self.log_message(f"🗑 Удалено заказов: {total_deleted}", "success" if total_deleted > 0 else "info", source="pv_bot")
                self.log_message('='*60, "info", source="pv_bot")
                
                clear_progress()
                self.was_interrupted = False

            except Exception as e:
                self.log_message(f"❌ Критическая ошибка", "error", source="pv_bot")
                print(f"[BOT ERROR] Critical: {e}")
                self.was_interrupted = True
            finally:
                self.current_browser = None
                self.current_page = None
                if browser:
                    try:
                        browser.close()
                    except:
                        pass
                self.end_session()
                self.is_processing = False
                self.update_tray_status('ready')
                self.current_progress = None
                self.resumed_from = None
                self._tk_after(0, self._restore_pvbot_buttons)
                self._tk_after(0, lambda: self.update_status("Удаление завершено."))
                self._tk_after(0, self.on_automation_complete)
    def _login(self, page, retry_count=0, max_retries=6, credentials=None):
        """Login to GreenLeaf with retry logic and adaptive timeouts."""
        # Check stop event
        if self.stop_event.is_set():
            return False
        
        # Use provided credentials or fallback to instance variables
        if credentials:
            l_url = credentials['url']
            l_user = credentials['login']
            l_pass = credentials['password']
            t_mult = credentials['timeout_mult']
            d_mult = credentials['delay_mult']
        else:
            l_url = self.url.get()
            l_user = self.login.get()
            l_pass = self.password.get()
            t_mult = self.timeout_multiplier
            d_mult = self.delay_multiplier

        # Detect 'Slow Internet Mode' directly from settings to be safe
        import settings as st
        s_data = st.load_settings()
        is_slow_mode = s_data.get('slow_network_mode', False)
        
        # User requested: 30s for normal, 60s for slow mode
        # We also respect the multiplier if it's already high
        base_timeout_ms = 60000 if (is_slow_mode or t_mult > 1.5) else 30000
        
        base_timeout = base_timeout_ms * t_mult
        url_timeout = base_timeout_ms * t_mult
        click_timeout = 15000 * t_mult
        
        try:
            # --- METHOD 1: Standard Login (on attempts 1, 3, 5...) ---
            if retry_count % 2 == 0:
                self.log_message(f"🔐 Авторизация (Метод 1: Стандарт)... (ожидание: {int(base_timeout//1000)}с)", "info", source="pv_bot")
                
                self.log_message(f"  🌐 Переход на {l_url}...", "debug", source="pv_bot")
                page.goto(l_url, timeout=base_timeout)
                
                self.log_message(f"  🔍 Ожидание поля ввода...", "debug", source="pv_bot")
                if not self._wait_visible(page, 'input[name="login"]', base_timeout):
                    return False
                
                self.log_message(f"  ⌨️ Ввод учетных данных...", "debug", source="pv_bot")
                page.fill('input[name="login"]', l_user)
                page.fill('input[name="passwd"]', l_pass)
                
                self.log_message(f"  🖱️ Нажатие 'Войти'...", "debug", source="pv_bot")
                page.click('button[type="submit"], input[type="submit"], .btn-login, button:has-text("Войти")', timeout=click_timeout)

            # --- METHOD 2: Alternative Login (on attempts 2, 4, 6... Fallback for technical works) ---
            else:
                self.log_message(f"🔐 Авторизация (Метод 2: Резервный)... (ожидание: {int(base_timeout//1000)}с)", "info", source="pv_bot")
                
                # Navigate directly to the control login which we verified works better during maintenance
                target_url = "https://greenleaf-global.com/do.control/login"
                self.log_message(f"  🌐 Прямой переход на {target_url}...", "debug", source="pv_bot")
                page.goto(target_url, timeout=base_timeout)
                
                self.log_message(f"  🔍 Ожидание формы (login/passwd)...", "debug", source="pv_bot")
                if not self._wait_visible(page, 'input[name="login"]', base_timeout):
                    return False
                
                self.log_message(f"  ⌨️ Ввод учетных данных...", "debug", source="pv_bot")
                page.fill('input[name="login"]', l_user)
                page.fill('input[name="passwd"]', l_pass)
                
                self.log_message(f"  🖱️ Нажатие 'Enter'...", "debug", source="pv_bot")
                page.click('input[type="submit"]', timeout=click_timeout)

            # Wait for redirect
            page.wait_for_url(re.compile(r"https://greenleaf-global.com/.*(dashboard|do\.vshow#|office)"), timeout=url_timeout)
            
            time.sleep(3 * d_mult) 
            self.log_message("✅ Вход в GreenLeaf выполнен успешно!", "success", source="pv_bot")
            return True
            
        except Exception as e:
            err_str = str(e)
            if "timeout" in err_str.lower():
                err_str = f"Timeout {int(base_timeout//1000)}s exceeded"
            
            self.log_message(f"❌ Ошибка входа, повтор...", "error", source="pv_bot")
            print(f"[BOT DEBUG] Login error (attempt {retry_count + 1}): {err_str[:100]}")
            
            if retry_count < max_retries:
                wait_time = 30 * d_mult
                self.log_message(f"⏳ Повторная попытка через {int(wait_time)} сек... (попытка {retry_count + 2}/{max_retries + 1})", "warning", source="pv_bot")
                time.sleep(wait_time)
                return self._login(page, retry_count + 1, max_retries, credentials=credentials)
            else:
                self.log_message(f"❌ Не удалось войти в систему после {max_retries + 1} попыток", "error", source="pv_bot")
                return False

    def _run_step1(self, page, file_path):
        """Step 1: Process Excel and add to cart."""
        self.log_message(f"📄 Открытие файла Excel: {os.path.basename(file_path)}", "info", source="pv_bot")

        try:
            df = pd.read_excel(file_path)
            df.columns = [col.strip().lower() for col in df.columns]

            column_map = {
                "hash": "№",
                "customer": "покупатель",
                "product_name": "наименование",
                "quantity": "кол-во",
                "price": "цена",
                "discount": "скидка, %"
            }

            df[column_map["hash"]] = df[column_map["hash"]].ffill()
            df_filtered = df.dropna(subset=[column_map["hash"]])

            orders = []
            for order_id, group in df_filtered.groupby(column_map["hash"]):
                user_id = None
                products = []

                # Extract user ID
                customer_info = group[column_map["customer"]].dropna()
                if not customer_info.empty:
                    customer_str = str(customer_info.iloc[0]).strip()
                    id_match = self.re_id_pattern.search(customer_str)
                    if id_match:
                        user_id = id_match.group(1).lower()

                # Extract products with 50% discount
                for _, row in group.iterrows():
                    discount = pd.to_numeric(row.get(column_map["discount"]), errors='coerce')
                    if pd.isna(discount) or abs(discount - 50) > 1:
                        continue

                    product_name = str(row[column_map["product_name"]]).strip()
                    qty = int(row[column_map["quantity"]])
                    price_full = float(row.get(column_map["price"], 0))

                    code_match = self.re_product_code_pattern_xlsx.search(product_name)
                    if code_match:
                        products.append({
                            "code": code_match.group(1).upper(),
                            "name": product_name,
                            "qty": qty,
                            "price": price_full * 0.5
                        })

                if user_id and products:
                    orders.append({"order_id": order_id, "user_id": user_id, "products": products})

            self.log_message(f"✅ Найдено заказов для обработки: {len(orders)}", "success", source="pv_bot")
            # One progress stage per order — advanced regardless of outcome
            if hasattr(self, '_init_progress'):
                self.master.after(0, self._init_progress, len(orders))

            # Check for previously completed orders (from interrupted session)
            completed_ids = set(self.successful_ids) if self.current_progress else set()
            if completed_ids:
                self.log_message(f"📋 Пропускаем {len(completed_ids)} уже выполненных заказов", "info", source="pv_bot")

            # Process each order
            for i, order in enumerate(orders):
                if self.stop_event.is_set():
                    # Save progress on stop
                    self.save_current_progress(file_path, 'step1', i, order['user_id'])
                    break
                
                # Skip already completed orders
                if order['user_id'] in completed_ids:
                    self.log_message(f"⏭️ Пропуск {order['user_id']} (уже выполнен)", "info", source="pv_bot")
                    continue

                self.log_message(f"\n--- Заказ {i+1}/{len(orders)}: {order['user_id']} ---", "info", source="pv_bot")
                
                # Try to process with recovery logic
                success = self._process_order_with_recovery(page, order, file_path, i)
                
                if success:
                    self.add_successful_id(order['user_id'])
                    self.save_current_progress(file_path, 'step1', i, order['user_id'])
                else:
                    if self.stop_event.is_set():
                        break
                    self.add_failed_attempt(
                        order['user_id'],
                        get_text('failed_after_recovery_attempts', self.lang))

                # Progress advances per processed order (success, fail or skip)
                if hasattr(self, '_advance_stage'):
                    self.master.after(0, self._advance_stage, f"Заказ {i+1}/{len(orders)}")

        except Exception as e:
            self.log_message(f"❌ Ошибка обработки файла Excel", "error")
            print(f"[BOT ERROR] Excel processing: {e}")
            self.was_interrupted = True

    def _process_order_with_recovery(self, page, order, file_path, order_index):
        """Process order with automatic page recovery on failure."""
        user_id = order['user_id']
        products = order['products']
        
        # Attempt 1: Normal processing
        try:
            if self._process_order(page, user_id, products):
                return True
        except Exception as e:
            if self.stop_event.is_set():
                return False
            error_msg = str(e).lower()
            self.log_message(f"⚠️ Ошибка обработки заказа", "warning")
            print(f"[BOT DEBUG] Order error for {user_id}: {e}")
            
            # Check if it's a recoverable error (timeout, navigation, etc.)
            if any(keyword in error_msg for keyword in ['timeout', 'navigation', 'closed', 'context', 'target']):
                
                # Attempt 2: Refresh page and retry
                self.log_message("🔄 Восстановление соединения...", "warning")
                if self._recover_page_refresh(page):
                    self.log_message("✅ Соединение восстановлено, повторная попытка...", "success")
                    try:
                        if self._process_order(page, user_id, products):
                            self.recovered_orders += 1
                            return True
                    except Exception as e2:
                        if self.stop_event.is_set():
                            return False
                        self.log_message(f"⚠️ Ошибка после восстановления", "warning")
                        print(f"[BOT DEBUG] Still failing after refresh: {e2}")
                
                # Attempt 3: Full re-login and retry
                self.log_message("🔄 Перезапуск сессии...", "warning")
                self.save_current_progress(file_path, 'step1', order_index, user_id)
                
                if self._recover_page_relogin(page):
                    self.log_message("✅ Повторный вход выполнен, продолжение...", "success")
                    try:
                        if self._process_order(page, user_id, products):
                            self.recovered_orders += 1
                            return True
                    except Exception as e3:
                        if self.stop_event.is_set():
                            return False
                        self.log_message(f"❌ Ошибка после повторного входа", "error")
                        print(f"[BOT DEBUG] Failed after re-login: {e3}")
                else:
                    self.log_message("❌ Ошибка повторного входа", "error")
            
            return False
        
        return False

    def _recover_page_refresh(self, page):
        """Try to recover by refreshing the page."""
        try:
            page.reload(timeout=30000 * self.timeout_multiplier)
            time.sleep(3 * self.delay_multiplier)
            # Pause sync during bot activity            
            # Short timeouts for responsiveness
            page.wait_for_load_state('domcontentloaded', timeout=15000 * self.timeout_multiplier)
            return True
        except Exception as e:
            print(f"[BOT DEBUG] Refresh failed: {e}")
            return False

    def _recover_page_relogin(self, page):
        """Try to recover by logging out first, then re-logging in."""
        try:
            self.log_message("🔄 Выход из системы...", "info")
            
            # Step 1: Try to logout properly
            try:
                # Click on profile dropdown button
                page.click('button.visitor-photo-img', timeout=5000)
                time.sleep(0.5)
                
                # Click logout link
                page.click('a[href="#logout"]', timeout=5000)
                time.sleep(2 * self.delay_multiplier)
                self.log_message("✅ Выход выполнен", "info")
            except Exception as logout_error:
                self.log_message(f"⚠️ Кнопка выхода не найдена, переход к авторизации...", "info")
            
            # Step 2: Navigate to login page
            page.goto("https://greenleaf-global.com/office/login?goto=/dashboard", timeout=30000 * self.timeout_multiplier)
            time.sleep(2 * self.delay_multiplier)
            
            # Step 3: Perform login
            return self._login(page)
        except Exception as e:
            self.log_message("⚠️ Ошибка перехода на страницу входа", "error")
            print(f"[BOT DEBUG] Re-login navigation failed: {e}")
            return False

    def save_current_progress(self, file_path, step, order_index, last_user_id):
        """Save current progress for resume capability."""
        progress = {
            'file_path': file_path,
            'step': step,
            'order_index': order_index,
            'last_user_id': last_user_id,
            'completed_ids': list(self.successful_ids),
            'timestamp': datetime.now().isoformat(),
        }
        # Save session aggregate data for Supabase upload on resume
        if self.session_purchases:
            successful = sum(1 for p in self.session_purchases if p['success'])
            total_items = sum(len(p.get('items', [])) for p in self.session_purchases)
            total_sales = sum(
                sum(item.get('price', 0) * item.get('qty', 1) for item in p.get('items', []))
                for p in self.session_purchases if p['success']
            )
            unique_users = set(p['user_id'] for p in self.session_purchases if p['success'])
            progress['session_summary'] = {
                'total_orders': len(self.session_purchases),
                'successful': successful,
                'failed': len(self.session_purchases) - successful,
                'recovered_orders': self.recovered_orders,
                'failed_items': self.failed_items,
                'total_items': total_items,
                'total_sales': round(total_sales, 2),
                'unique_users': len(unique_users),
                'start_time': self.session_start_time.isoformat() if self.session_start_time else None,
            }
        save_progress(progress)

    def _process_order(self, page, user_id, products):
        """Process a single order with adaptive timeouts."""
        # Adaptive timeouts
        base_timeout = 15000 * self.timeout_multiplier
        long_timeout = 30000 * self.timeout_multiplier
        search_timeout = 7000 * self.timeout_multiplier
        base_delay = 2 * self.delay_multiplier
        
        added_items = []  # Track items for history
        
        try:
            self.log_message(f"👤 Выбор пользователя: {user_id}", "info", source="pv_bot")
            page.goto("https://greenleaf-global.com/office/dashboard", timeout=base_timeout)
            page.wait_for_url("**/dashboard", timeout=base_timeout)
            time.sleep(base_delay)
            
            page.click('a[href="#admin/shop/buy"]')
            if not self._wait_visible(page, 'input[check_query="login_buy"]', base_timeout):
                return False
            time.sleep(1 * self.delay_multiplier)
            
            page.fill('input[check_query="login_buy"]', user_id)
            time.sleep(base_delay)
            
            # Check if user was found (look for "Не найдено" or disabled button)
            time.sleep(1)  # Wait for validation
            not_found = page.locator('text="Не найдено"').count() > 0
            button_disabled = page.locator('input[type="submit"][value="Далее"][disabled]').count() > 0
            
            if not_found or button_disabled:
                autoblock_mode = getattr(self, 'cached_partner_autoblock', 'all')
                if autoblock_mode in ('all', 'not_found'):
                    self.log_message(f"⚠️ Пользователь {user_id} не найден в системе - блокировка и пропуск", "warning", source="pv_bot")
                    try:
                        if hasattr(self, 'partners_manager'):
                            from datetime import datetime
                            # Keep existing partner data (ФИ, phone, email, notes) intact
                            existing = self.partners_manager.get_partner(user_id) or {}
                            self.partners_manager.update_partner(
                                user_id,
                                name=existing.get('name', '') or '',
                                phone=existing.get('phone', '') or '',
                                email=existing.get('email', '') or '',
                                notes=existing.get('notes', '') or '',
                                user_name='System',
                                is_blocked=1,
                                block_reason="Пользователь не найден в GreenLeaf (авто-блокировка PV Bot)",
                                blocked_by="PV Bot",
                                blocked_at=datetime.now().isoformat()
                            )
                            self.log_message(f"  🔒 Заблокирован: {user_id}", "warning", source="pv_bot")
                    except Exception as e:
                                self.log_message(f"  ❌ Ошибка блокировки {user_id}", "error", source="pv_bot")
                                print(f"[BOT DEBUG] Block error {user_id}: {e}")
                else:
                    self.log_message(f"⚠️ Пользователь {user_id} не найден в системе - пропуск (блокировка отключена в настройках)", "warning", source="pv_bot")
                return False
            
            page.click('input[type="submit"][value="Далее"]')
            if not self._wait_visible(page, 'input[name="query"]', long_timeout):
                return False
            time.sleep(1 * self.delay_multiplier)

            added_any = False
            for product in products:
                # Check stop event before each product
                if self.stop_event.is_set():
                    self.log_message("⏹ Автоматизация остановлена пользователем.", "warning", source="pv_bot")
                    return False
                
                item_code = product['code']
                
                # SMART RETRY: Skip items that failed multiple times this session
                if item_code in self.session_blacklist:
                    if self.session_blacklist[item_code] >= self.BLACKLIST_THRESHOLD:
                        self.log_message(f"  ⏭️ Пропуск {item_code} (нет в наличии)", "info", source="pv_bot")
                        continue
                
                try:
                    self.log_message(f"  🔍 Поиск товара: {item_code}", "info", source="pv_bot")
                    page.fill('input[name="query"]', product["code"])
                    time.sleep(1.5 * self.delay_multiplier)
                    page.click('input.chgoods-search-btn')
                    time.sleep(1.5 * self.delay_multiplier)

                    if not self._wait_visible(page, 'tr.goods-item', search_timeout):
                        return False
                    rows = page.locator('tr.goods-item')

                    for i in range(rows.count()):
                        row = rows.nth(i)
                        price_text = row.locator('td:nth-child(4)').inner_text()
                        price_match = re.search(r'(\d[\d\s\.]*)', price_text)
                        price = float(price_match.group(1).replace(' ', '')) if price_match else 0

                        if abs(price - product["price"]) < 1.0:
                            for _ in range(product["qty"]):
                                row.locator('div.add').click()
                                time.sleep(0.3 * self.delay_multiplier)
                            added_any = True
                            added_items.append({
                                'code': product['code'],
                                'qty': product['qty'],
                                'price': product['price']
                            })
                            self.log_message(f"  ✅ Добавлено: {product['name']} (x{product['qty']})", "success", source="pv_bot")
                            break
                except Exception as e:
                    if self.stop_event.is_set():
                        return False
                    # Track failed item (out of stock or invalid)
                    self.failed_items[item_code] = self.failed_items.get(item_code, 0) + 1
                    # Add to session blacklist for smart retry
                    self.session_blacklist[item_code] = self.session_blacklist.get(item_code, 0) + 1
                    
                    if self.session_blacklist[item_code] >= self.BLACKLIST_THRESHOLD:
                        self.log_message(f"  ⚠️ {item_code} в черном списке сессии ({self.BLACKLIST_THRESHOLD}+ неудач)", "warning", source="pv_bot")
                    else:
                            self.log_message(f"  ⚠️ Ошибка добавления {item_code}", "warning", source="pv_bot")
                            print(f"[BOT DEBUG] Add error {item_code}: {e}")

            if added_any:
                if not self._wait_visible(page, 'input.btn-next:enabled', long_timeout):
                    return False
                page.click('input.btn-next')
                self.log_message(f"✅ Заказ для {user_id} успешно сформирован", "success", source="pv_bot")
                # Track purchase for history
                self.track_purchase(user_id, added_items, success=True, has_discount=True)
                time.sleep(3 * self.delay_multiplier)
                return True

            # Track failed purchase
            self.track_purchase(user_id, products, success=False, has_discount=True)

            return False

        except Exception as e:
            if self.stop_event.is_set():
                return False
            self.log_message(f"⚠️ Ошибка обработки {user_id}", "warning")
            print(f"[BOT DEBUG] Process error {user_id}: {e}")
            # Re-raise timeout errors so recovery logic can handle them
            if 'timeout' in str(e).lower():
                raise
            return False

    def _run_step2(self, page):
        """Step 2: Process unpaid orders with adaptive timeouts."""
        consecutive_empty_pages = 0
        max_empty_pages = self.max_empty_pages_var.get() if hasattr(self, 'max_empty_pages_var') else 3
        current_page = 1
        
        # Adaptive timeouts
        base_timeout = 15000 * self.timeout_multiplier
        long_timeout = 30000 * self.timeout_multiplier
        click_timeout = 10000 * self.timeout_multiplier
        base_delay = 2 * self.delay_multiplier
        
        try:
            # Navigate via menu link (more reliable for SPA)
            self.log_message("💰 Переход в раздел 'Покупки клиентов'...", "info", source="pv_bot")
            try:
                if not self._wait_visible(page, '.page-sidebar-menu', base_timeout):
                    return
                purchase_link = page.locator('a:has-text("Покупки клиентов")')
                purchase_link.wait_for(state='visible', timeout=click_timeout)
                purchase_link.click(timeout=click_timeout)
            except Exception as e:
                # Fallback to direct URL navigation
                self.log_message(f"⚠️ Ошибка навигации, использую прямой переход...", "warning", source="pv_bot")
                page.goto(settings.PURCHASES_URL, timeout=long_timeout)

            time.sleep(base_delay)
            self.log_message("🔍 Ожидание загрузки таблицы заказов...", "info", source="pv_bot")
            if not self._wait_visible(page, 'table', long_timeout):
                return
            time.sleep(1 * self.delay_multiplier)

            keep_scanning = True

            while not self.stop_event.is_set() and keep_scanning:
                self.log_message(f"📂 Сканирование страницы {current_page}...", "info", source="pv_bot")

                try:
                    unpaid_orders = []
                    rows = page.query_selector_all('table tr')
                    
                    # Log total rows found for debugging
                    if len(rows) <= 1: # Only header or empty
                        self.log_message(f"⚠️ Таблица на стр. {current_page} пуста или не загружена", "warning", source="pv_bot")
                    
                    for row in rows:
                        if self.stop_event.is_set():
                            return

                        row_html = row.inner_html()
                        # Extract order ID for logging even if not unpaid
                        order_id_match = re.search(r'>(\d{8})<', row_html)
                        row_order_id = order_id_match.group(1) if order_id_match else "???"
                        
                        if "Неоплаченный" in row_html:
                            # Verify if it's the red status text
                            is_unpaid = 'color: red;' in row_html or 'text-danger' in row_html
                            
                            if is_unpaid:
                                try:
                                    # Check if this is a 188,000 registration package (skip these)
                                    price_element = row.query_selector('div.price-format, td:nth-child(4)')
                                    if price_element:
                                        price_text = price_element.inner_text()
                                        # Extract numeric price (remove currency, spaces, etc.)
                                        price_match = re.search(r'([\d\s]+(?:[.,]\d+)?)', price_text)
                                        if price_match:
                                            price_str = price_match.group(1).replace(' ', '').replace(',', '.')
                                            try:
                                                price_value = float(price_str)
                                                # Skip registration packages (188,000 tenge)
                                                if 187000 <= price_value <= 189000:
                                                    self.log_message(f"  ⏭️ Пропуск регистрационного пакета (188,000 тг)", "info", source="pv_bot")
                                                    continue
                                            except ValueError:
                                                pass
                                    
                                    # Try multiple selectors for the link (robustness)
                                    link = row.query_selector("td.key a, a[href*='shop/delivery'], td:first-child a")
                                    if link:
                                        order_id = link.inner_text().strip()
                                        href = link.get_attribute('href')
                                        if order_id and href:
                                            unpaid_orders.append({'id': order_id, 'href': href})
                                        else:
                                            self.log_message(f"  ⚠️ Заказ {row_order_id}: Ссылка найдена, но ID или HREF пусты", "warning", source="pv_bot")
                                    else:
                                        self.log_message(f"  ⚠️ Заказ {row_order_id}: Не удалось найти кликабельную ссылку", "warning", source="pv_bot")
                                except Exception as e:
                                    self.log_message(f"  ⚠️ Ошибка анализа заказа {row_order_id}", "warning", source="pv_bot")
                                    print(f"[BOT DEBUG] Order analysis error {row_order_id}: {e}")
                                    continue
                        elif "Выданный" in row_html or "Оплачен" in row_html:
                             # Just skip silently or log at info if needed
                             pass

                    if unpaid_orders:
                        # Reset empty pages counter when orders found
                        consecutive_empty_pages = 0
                        self.log_message(f"✅ Найдено неоплаченных заказов: {len(unpaid_orders)}", "success", source="pv_bot")
                        if not getattr(self, '_step2_progress_init', False):
                            self._step2_progress_init = True
                            if hasattr(self, '_init_progress'):
                                self.master.after(0, self._init_progress, len(unpaid_orders))

                        for order in unpaid_orders:
                            if self.stop_event.is_set():
                                return

                            order_id = order['id']
                            
                            # Skip orders that are blacklisted (insufficient funds, repeated failures)
                            if hasattr(self, 'insufficient_funds_orders') and order_id in self.insufficient_funds_orders:
                                self.log_message(f"  ⏭️ Пропуск заказа из черного списка: {order_id}", "info", source="pv_bot")
                                continue
                            
                            detail_url = f"https://greenleaf-global.com/do.vshow#{order['href'].split('#')[-1]}"

                            self.log_message(f"💳 Обработка оплаты: {order_id}", "info", source="pv_bot")
                            if self._process_unpaid_order(page, order_id, detail_url):
                                self.add_successful_id(order_id)
                            else:
                                if self.stop_event.is_set():
                                    return
                                self.add_failed_attempt(order_id, "Оплата не удалась")

                            if hasattr(self, '_advance_stage'):
                                self.master.after(0, self._advance_stage, f"Заказ {order_id}")

                            # Return to purchases page after processing
                            self.log_message("↩️ Возврат к списку покупок...", "info", source="pv_bot")
                            try:
                                # Try clicking "Личный офис" first, then "Покупки клиентов"
                                if not self._wait_visible(page, 'a:has-text("Личный офис")', click_timeout):
                                    return
                                page.click('a:has-text("Личный офис")')
                                time.sleep(1 * self.delay_multiplier)
                                if not self._wait_visible(page, '.page-sidebar-menu', click_timeout):
                                    return
                                page.click('a:has-text("Покупки клиентов")')
                                time.sleep(1 * self.delay_multiplier)
                            except:
                                # Fallback to direct navigation
                                page.goto(settings.PURCHASES_URL, timeout=long_timeout)
                                time.sleep(1 * self.delay_multiplier)

                            if not self._wait_visible(page, 'table', long_timeout):
                                return
                            time.sleep(1 * self.delay_multiplier)
                    else:
                        # No orders found on this page
                        consecutive_empty_pages += 1
                        self.log_message(f"ℹ️ Неоплаченных заказов на стр. {current_page} не найдено", "info", source="pv_bot")
                        
                        # Check if we should stop
                        if consecutive_empty_pages >= max_empty_pages:
                            self.log_message(f"✅ Завершено: {max_empty_pages} страниц подряд без заказов", "success")
                            keep_scanning = False
                            continue

                    # Check for next page
                    next_btn = page.locator('a.btn.btn-primary:has-text("Далее >>")')
                    if next_btn.is_visible():
                        next_btn.click(timeout=click_timeout)
                        if not self._wait_visible(page, 'table', long_timeout):
                            return
                        current_page += 1
                        time.sleep(base_delay)
                    else:
                        self.log_message("✅ Достигнут конец списка", "success")
                        keep_scanning = False

                except Exception as page_error:
                    if self.stop_event.is_set():
                        return
                    error_msg = str(page_error)
                    self.log_message(f"⚠️ Ошибка загрузки страницы", "warning", source="pv_bot")
                    print(f"[BOT DEBUG] Page error: {error_msg}")
                    
                    # Check if it's a navigation/context error - need to re-login
                    if "context was destroyed" in error_msg.lower() or "navigation" in error_msg.lower() or "target closed" in error_msg.lower():
                        self.log_message("🔄 Соединение потеряно, восстановление...", "warning")
                        
                        try:
                            if self.stop_event.is_set():
                                return
                            # Re-login
                            if self._login(page):
                                self.log_message(f"✅ Восстановлено, продолжаем со страницы {current_page}...", "success")
                                
                                # Navigate back to purchases
                                page.goto(settings.PURCHASES_URL)
                                time.sleep(2)
                                if not self._wait_visible(page, 'table', 30000):
                                    return
                                
                                # Navigate to the page we were on
                                for _ in range(current_page - 1):
                                    next_btn = page.locator('a.btn.btn-primary:has-text("Далее >>")')
                                    if next_btn.is_visible():
                                        next_btn.click(timeout=10000)
                                        if not self._wait_visible(page, 'table', 30000):
                                            return
                                        time.sleep(1)
                                    else:
                                        break
                                
                                self.log_message(f"✅ Продолжение со страницы {current_page}", "success")
                                continue  # Continue the main loop
                            else:
                                self.log_message("❌ Ошибка восстановления, этап 2 остановлен", "error")
                                keep_scanning = False
                        except Exception as relogin_error:
                            self.log_message(f"❌ Ошибка восстановления", "error")
                            print(f"[BOT DEBUG] Re-login error: {relogin_error}")
                            keep_scanning = False
                    else:
                        # Other error - just continue to next page
                        self.log_message("Попытка продолжить...", "warning")
                        try:
                            next_btn = page.locator('a.btn.btn-primary:has-text("Далее >>")')
                            if next_btn.is_visible():
                                next_btn.click(timeout=10000)
                                if not self._wait_visible(page, 'table', 30000):
                                    return
                                current_page += 1
                                time.sleep(2)
                            else:
                                keep_scanning = False
                        except:
                            keep_scanning = False

                # Auto-block partners that had insufficient funds
                if hasattr(self, 'insufficient_funds_partners') and self.insufficient_funds_partners:
                    autoblock_mode = getattr(self, 'cached_partner_autoblock', 'all')
                    if autoblock_mode not in ('all', 'insufficient'):
                        self.log_message(f"🚫 Партнёров с недостатком средств: {len(self.insufficient_funds_partners)} (блокировка отключена в настройках)", "warning", source="pv_bot")
                    else:
                        self.log_message(f"🚫 Блокировка {len(self.insufficient_funds_partners)} партнёров с недостатком средств", "warning", source="pv_bot")
                        from datetime import datetime
                        now = datetime.now().isoformat()
                        for partner_id in sorted(self.insufficient_funds_partners):
                            try:
                                # Keep existing partner data (ФИ, phone, email, notes) intact
                                existing = self.partners_manager.get_partner(partner_id) or {}
                                self.partners_manager.update_partner(
                                    partner_id,
                                    name=existing.get('name', '') or '',
                                    phone=existing.get('phone', '') or '',
                                    email=existing.get('email', '') or '',
                                    notes=existing.get('notes', '') or '',
                                    user_name='System',
                                    is_blocked=1,
                                    block_reason="Недостаточно купонов (авто-блокировка PV Bot)",
                                    blocked_by="PV Bot",
                                    blocked_at=now
                                )
                                self.log_message(f"  🔒 Заблокирован: {partner_id}", "warning", source="pv_bot")
                            except Exception as e:
                                self.log_message(f"  ❌ Ошибка блокировки {partner_id}", "error", source="pv_bot")
                                print(f"[BOT DEBUG] Block error {partner_id}: {e}")

        except Exception as e:
            self.log_message(f"❌ Ошибка этапа 2", "error")
            print(f"[BOT DEBUG] Step 2 error: {e}")

    def _process_unpaid_order(self, page, order_id, detail_url):
        """Process single unpaid order with adaptive timeouts."""
        base_timeout = 15000 * self.timeout_multiplier
        base_delay = 1 * self.delay_multiplier
        
        try:
            page.goto(detail_url, timeout=base_timeout)
            time.sleep(base_delay)

            if not self._wait_visible(page, 'input[type="button"][value="Оплатить"], button:has-text("Оплатить")', base_timeout):
                return False
            page.click('input[type="button"][value="Оплатить"], button:has-text("Оплатить")')
            time.sleep(base_delay)

            page.click('input[type="button"][value="Далее"], button:has-text("Далее")')
            time.sleep(base_delay)
            
            # CHECKPOINT 1: Insufficient funds after "Далее"
            if self._check_insufficient_funds(page, order_id):
                return False

            page.click('input[type="submit"][value="Готово"], button:has-text("Готово")')
            time.sleep(base_delay)

            # CHECKPOINT 2: Insufficient funds after "Готово"
            if self._check_insufficient_funds(page, order_id):
                return False

            try:
                page.click('input[type="button"][value="Выдать"], button:has-text("Выдать")')
                time.sleep(base_delay)
            except:
                self.log_message(f"  ⚠️ Кнопка 'Выдать' не найдена для {order_id}", "warning", source="pv_bot")

            try:
                page.click('input[type="submit"][value="Подтвердить"], button:has-text("Подтвердить")')
                time.sleep(2 * self.delay_multiplier)
            except:
                pass

            # CHECKPOINT 3: Late-stage insufficient funds
            if self._check_insufficient_funds(page, order_id):
                return False

            self.log_message(f"✅ Заказ {order_id} успешно оплачен!", "success", source="pv_bot")
            self.track_purchase(order_id, [], success=True, has_discount=False)
            return True

        except Exception as e:
            if self.stop_event.is_set():
                return False
            self.log_message(f"⚠️ Ошибка заказа {order_id}", "warning", source="pv_bot")
            print(f"[BOT DEBUG] Order error {order_id}: {e}")
            self.track_purchase(order_id, [], success=False, has_discount=False)

            if not hasattr(self, 'order_fail_count'):
                self.order_fail_count = {}
            self.order_fail_count[order_id] = self.order_fail_count.get(order_id, 0) + 1

            if self.order_fail_count[order_id] >= 2:
                if not hasattr(self, 'insufficient_funds_orders'):
                    self.insufficient_funds_orders = set()
                self.insufficient_funds_orders.add(order_id)
                self.log_message(f"🚫 Заказ {order_id} в черном списке после {self.order_fail_count[order_id]} неудач", "warning", source="pv_bot")

            return False

    def _check_insufficient_funds(self, page, order_id):
        """Check page content for insufficient funds error. Returns True if found."""
        try:
            page_content = page.content()
            if "недостаточно средств" in page_content.lower():
                user_match = re.search(r'У пользователя (\w+) недостаточно', page_content)
                user_login = user_match.group(1) if user_match else "unknown"
                self.log_message(f"⚠️ Заказ {order_id}: У пользователя {user_login} недостаточно средств - ПРОПУСК", "warning", source="pv_bot")
                if not hasattr(self, 'insufficient_funds_orders'):
                    self.insufficient_funds_orders = set()
                self.insufficient_funds_orders.add(order_id)
                if user_login and user_login != "unknown":
                    if not hasattr(self, 'insufficient_funds_partners'):
                        self.insufficient_funds_partners = set()
                    self.insufficient_funds_partners.add(user_login)
                self.track_purchase(order_id, [], success=False, has_discount=False)
                return True
        except:
            pass
        return False

    def clear_logs(self):
        """Clear all logs and results."""
        if hasattr(self, "log_text") and self.log_text is not None:
            self.log_text.config(state="normal")
            self.log_text.delete('1.0', tk.END)
            self.log_text.config(state="disabled")

        for attr in ('successful_ids_text', 'failed_attempts_text'):
            w = getattr(self, attr, None)
            if w is not None:
                try:
                    w.config(state="normal")
                    w.delete('1.0', tk.END)
                    w.config(state="disabled")
                except Exception:
                    pass

        # Preserve restored resume data so already-completed orders stay marked
        # and are skipped instead of being reprocessed (and re-failed).
        if getattr(self, 'current_progress', None):
            self.successful_ids = list(self.current_progress.get('completed_ids', []))
            try:
                self.master.after(0, self._render_successful_ids)
            except Exception:
                pass
        else:
            self.successful_ids = []
        self.failed_attempts = []
