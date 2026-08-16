# -*- coding: utf-8 -*-
"""
PVM.core - Main (Bot) Tab Mixin
==================================
Main tab with activation status, notifications, log, controls.
"""

import sys
import threading
import shutil
import tkinter as tk
from tkinter import ttk, scrolledtext

import os
from datetime import datetime
import settings
from db import fetch_notifications, get_supabase_client
from ui_lang import get_text
from ui_dialogs import AutoScrollbar, ToolTip


class MainTabMixin:
    """Main/Bot tab methods for GreenLeafApp."""

    def create_main_tab(self, show_status_header=True):
        """Create the main operation tab with responsive layout."""
        c = self.colors  # Shorthand
        
        # Configure main_frame for full expansion
        self.main_frame.grid_rowconfigure(0, weight=1)
        self.main_frame.grid_columnconfigure(0, weight=1)
        
        # Main container
        self.main_container = tk.Frame(self.main_frame, bg=c['bg'])
        self.main_container.pack(fill="both", expand=True)
        
        # Check for Technical Works mode
        # Fetch notifications in background to avoid blocking UI on Supabase delays
        self.notifications = []  # default empty until loaded
        
        tech_works_msg = None
        for n in self.notifications:
            if n.get('notification_type') == 'technical_works':
                tech_works_msg = n.get('message', 'System maintenance in progress.')
                break
                
        # 2. Check if pvm_core module is missing
        pvm_core_missing = 'pvm_core' not in sys.modules
        
        if tech_works_msg or pvm_core_missing:
            # Show FULL SCREEN technical works message on this tab
            tw_frame = tk.Frame(self.main_container, bg=c['bg'])
            tw_frame.pack(fill="both", expand=True)
            
            # Center content
            inner = tk.Frame(tw_frame, bg=c['bg'])
            inner.place(relx=0.5, rely=0.45, anchor="center")
            
            tk.Label(inner, text="⚠️", font=("Segoe UI", 48), bg=c['bg'], fg=c['warning']).pack(pady=10)
            
            title = get_text('technical_works_title', self.lang)
            tk.Label(inner, text=title, font=("Segoe UI", 24, "bold"), bg=c['bg'], fg=c['fg']).pack(pady=5)
            
            display_msg = tech_works_msg if tech_works_msg else get_text('automation_unavailable', self.lang)
            tk.Label(inner, text=display_msg, font=("Segoe UI", 14), bg=c['bg'], fg=c['fg_secondary'], 
                     wraplength=600, justify="center").pack(pady=20)
            
            # Simple button to refresh/check again
            def refresh_status():
                if self.notebook:
                    self.notebook.select(self.settings_frame) # Switch to settings to force refresh on return or just reload
                    self.master.after(100, lambda: self.show_toast(get_text('refreshing_status', self.lang), "info"))
                
            self._btn(inner, text=" OK ", command=refresh_status, style='accent').pack(pady=10)
            
            # Since we showed the TW screen, we don't build the rest of the tab
            return

        # Normal tab construction continues...
        # Progress / stage tracking state
        self._total_stages = 0
        self._completed_stages = 0
        self._stage_timestamps = []
        self._stage_durations = []
        self._process_start_time = None
        self._process_status = "waiting"

        # Scrollable wrapper (main tab) or direct frame (analytics sub-tab)
        if show_status_header:
            self._main_canvas = tk.Canvas(self.main_container, highlightthickness=0, bg=c['bg'])
            self._main_scroll = AutoScrollbar(self.main_container, orient="vertical", command=self._main_canvas.yview)
            self._main_canvas.configure(yscrollcommand=self._main_scroll.set)
            self.main_scrollable = tk.Frame(self._main_canvas, bg=c['bg'])
            self._main_cw = self._main_canvas.create_window((0, 0), window=self.main_scrollable, anchor="nw")
            def _on_main_cfg(e, cv=self._main_canvas, i=self._main_cw):
                cv.itemconfig(i, width=e.width)
            self._main_canvas.bind('<Configure>', _on_main_cfg)
            self._main_prev_bbox = [None]
            def _on_main_sr(e, cv=self._main_canvas, sc=self.main_scrollable):
                sc.update_idletasks()
                bb = cv.bbox("all")
                if bb != self._main_prev_bbox[0]:
                    self._main_prev_bbox[0] = bb
                    cv.configure(scrollregion=bb)
            self.main_scrollable.bind('<Configure>', _on_main_sr)
            def _mw_main(e, c=self._main_canvas):
                c.yview_scroll(-1 * (e.delta // 120), "units")
            self._main_canvas.bind('<MouseWheel>', _mw_main)
            self.main_scrollable.bind('<MouseWheel>', _mw_main)
            self._main_canvas.pack(side="left", fill="both", expand=True)
            self._main_scroll.pack(side="right", fill="y")
            # Universal wheel + drag-pan over empty zones (bound after build)
            self._main_canvas.after(100, lambda: self.enable_scroll_area(self._main_canvas, self.main_scrollable))
        else:
            self.main_scrollable = tk.Frame(self.main_container, bg=c['bg'])
            self.main_scrollable.pack(fill="both", expand=True)

        self.main_scrollable.grid_columnconfigure(0, weight=1)
        
        # Notification containers (may be 0, 1, or more)
        self._notif_containers = []

        # === TOP ROW: ACTIVATION STATUS (30%) + NOTIFICATIONS (70%) ===
        if show_status_header:
            top_row = tk.Frame(self.main_scrollable, bg=c['bg'])
            top_row.grid(row=0, column=0, sticky="ew", pady=(self.padding_medium, self.padding_small), padx=self.padding_medium)
            top_row.grid_columnconfigure(0, weight=3)   # 30% activation
            top_row.grid_columnconfigure(1, weight=7)   # 70% notifications
            top_row.grid_columnconfigure(2, weight=0)   # tariff badge (fixed)
            
            # === ACTIVATION STATUS FRAME (LEFT - 30%) ===
            status_frame = tk.LabelFrame(top_row, text=f" {get_text('activation_status', self.lang)} ", 
                                         padx=self.padding_medium, pady=self.padding_medium, 
                                         font=self.font_bold_tuple, bg=c['frame_bg'], fg=c['fg'])
            status_frame.grid(row=0, column=0, sticky="nsew", padx=(0, self.padding_small))

            # Status content with nice spacing
            status_inner = tk.Frame(status_frame, bg=c['frame_bg'])
            status_inner.pack(fill="x", padx=5, pady=5)
            
            # Device key - CLICKABLE to copy
            tk.Label(status_inner, text=f"{get_text('device_key', self.lang)}:", 
                    font=self.font_small_tuple, bg=c['frame_bg'], fg=c['fg_secondary']).grid(row=0, column=0, sticky="w", pady=2, padx=(0, 5))
            
            device_key_display = self.device_key[:16] + "..." if self.device_key and len(self.device_key) > 16 else (self.device_key or "Unknown")
            device_key_label = tk.Label(status_inner, text=device_key_display,
                    font=("Courier", self.font_small), fg=c['key_fg'], bg=c['key_bg'],
                    padx=4, pady=2, relief="flat", cursor="hand2")
            device_key_label.grid(row=0, column=1, sticky="w", pady=2)
            
            # Bind click to copy device key
            def copy_device_key(event=None):
                self.master.clipboard_clear()
                self.master.clipboard_append(self.device_key)
                # Show brief feedback
                original_bg = device_key_label.cget('bg')
                device_key_label.config(bg=c['success_bg'])
                self.master.after(300, lambda: device_key_label.config(bg=original_bg))
            
            device_key_label.bind("<Button-1>", copy_device_key)

            tk.Label(status_inner, text=f"{get_text('status', self.lang)}:", 
                    font=self.font_small_tuple, bg=c['frame_bg'], fg=c['fg_secondary']).grid(row=1, column=0, sticky="w", pady=2, padx=(0, 5))
            status_color = c['success'] if self.status.lower() == "active" else c['error']
            status_bg = c['success_bg'] if self.status.lower() == "active" else c['error_bg']
            status_text = get_text('active', self.lang) if self.status.lower() == "active" else get_text('inactive', self.lang)
            tk.Label(status_inner, text=f" {status_text} ",
                    font=self.font_bold_tuple, fg=status_color, bg=status_bg,
                    padx=4, pady=2).grid(row=1, column=1, sticky="w", pady=2)

            # Show Period (start - end) + remaining days if dates exist
            if self.activation_start and self.activation_start.strip() and self.activation_end and self.activation_end.strip():
                tk.Label(status_inner, text=f"{get_text('period', self.lang)}:", 
                        font=self.font_small_tuple, bg=c['frame_bg'], fg=c['fg_secondary']).grid(row=2, column=0, sticky="w", pady=2, padx=(0, 5))
                period_text = f" {self.activation_start} - {self.activation_end} "
                tk.Label(status_inner, text=period_text,
                        font=self.font_small_tuple, fg=c['warning'], bg=c['warning_bg'],
                        padx=4, pady=2).grid(row=2, column=1, sticky="w", pady=2)
                # Remaining days
                try:
                    end = datetime.strptime(self.activation_end.strip(), "%d.%m.%Y")
                    remaining = (end - datetime.now()).days
                    if remaining > 0:
                        days_text = f"⏳ Осталось: {remaining} дн."
                        days_color, days_bg = c['warning'], c['warning_bg']
                    elif remaining == 0:
                        days_text = "⏳ Последний день"
                        days_color, days_bg = c['warning'], c['warning_bg']
                    else:
                        days_text = f"❌ Просрочено на {-remaining} дн."
                        days_color, days_bg = c['error'], c['error_bg']
                    tk.Label(status_inner, text=days_text,
                            font=self.font_small_tuple, fg=days_color, bg=days_bg,
                            padx=4, pady=2).grid(row=3, column=0, columnspan=2, sticky="w", pady=2)
                except:
                    pass

            # === NOTIFICATIONS FRAME (RIGHT, fills remaining space) ===
            notif_frame = tk.Frame(top_row, bg=c['frame_bg'], bd=1, relief="solid")
            notif_frame.grid(row=0, column=1, sticky="nsew", padx=(self.padding_small, 0))
            tk.Label(notif_frame, text=f" {get_text('notifications', self.lang)} ",
                    font=self.font_bold_tuple, bg=c['frame_bg'], fg=c['fg']).pack(anchor="w", padx=4, pady=(2, 0))

            notif_container = tk.Frame(notif_frame, bg=c['frame_bg'])
            notif_container.pack(fill="both", expand=True, padx=4, pady=(1, 3))

            notif_canvas = tk.Canvas(notif_container, highlightthickness=0, bg=c['frame_bg'], height=1)
            notif_scroll = tk.Scrollbar(notif_container, orient="vertical", command=notif_canvas.yview, width=14)
            notif_canvas.configure(yscrollcommand=notif_scroll.set)

            notif_inner = tk.Frame(notif_canvas, bg=c['frame_bg'])
            ncw = notif_canvas.create_window((0, 0), window=notif_inner, anchor="nw")
            def _on_cfg_h(e, cv=notif_canvas, i=ncw):
                cv.itemconfig(i, width=e.width)
                if self._notif_after_id:
                    cv.after_cancel(self._notif_after_id)
                self._notif_after_id = cv.after_idle(lambda: self._update_notif_width())
            notif_canvas.bind('<Configure>', _on_cfg_h)
            def _mw_h(e, c=notif_canvas):
                c.yview_scroll(-1 * (e.delta // 120), "units")
            notif_canvas.bind('<MouseWheel>', _mw_h)
            notif_inner.bind('<MouseWheel>', _mw_h)

            notif_canvas.pack(side="left", fill="both", expand=True)
            notif_scroll.pack(side="right", fill="y")

            self._notif_containers.append(notif_inner)

            # Tariff badge at rightmost column (hidden unless subscription ending)
            tariff_frame = tk.Frame(top_row, bg=c['bg'])
            tariff_frame.grid(row=0, column=2, sticky="e")
            self._tariff_price_lbl = tk.Label(tariff_frame, text="",
                font=("Segoe UI", 10, "bold"), bg=c['bg'], fg=c['success'])
            self._tariff_price_lbl.pack(side="left")
            self._tariff_info_btn = tk.Label(tariff_frame, text="ⓘ",
                font=("Segoe UI", 9), bg=c['bg'], fg=c['fg_muted'], cursor="hand2")
            self._tariff_info_btn.pack(side="left", padx=(1, 4))
            self._tariff_tooltip = ToolTip(self._tariff_info_btn, get_text('pricing_tooltip_body', self.lang), title=get_text('pricing_tooltip_title', self.lang))
            self._tariff_date_lbl = tk.Label(tariff_frame, text="",
                font=("Segoe UI", 9), bg=c['bg'], fg=c['fg_muted'])
            self._tariff_date_lbl.pack(side="left", padx=(4, 0))
            self._tariff_frame = tariff_frame
            self._fetch_pricing_data()
            
        # === NOTIFICATION SYSTEM (always runs, renders to all containers) ===
        self.notifications = getattr(self, 'notifications', [])
        if not hasattr(self, '_notif_label_refs'):
            self._notif_label_refs = {}
        if not hasattr(self, '_notif_after_id'):
            self._notif_after_id = None

        self._render_all_notifications(self.notifications)

        import queue
        notif_queue = queue.Queue()

        def _fetch_and_render_thread(dev_key, is_active):
            try:
                notifs = fetch_notifications(dev_key, is_active)
                notif_queue.put(notifs)
            except Exception as e:
                print(f"Error fetching notifications on Main tab: {e}")

        def _poll_notifications():
            try:
                notifs = notif_queue.get_nowait()
                if self.master and self.master.winfo_exists():
                    self.notifications = notifs
                    self._render_all_notifications(notifs)
            except queue.Empty:
                if self.master and self.master.winfo_exists():
                    self.master.after(100, _poll_notifications)

        dev_key = self.device_key
        is_active = (self.status.get() if hasattr(self.status, 'get') else str(self.status)).lower() == "active"

        self.master.after(100, _poll_notifications)
        threading.Thread(target=_fetch_and_render_thread, args=(dev_key, is_active), daemon=True).start()

        # === ROW 1: SETTINGS + NOTIFICATIONS ===
        if not show_status_header:
            row1 = tk.Frame(self.main_scrollable, bg=c['bg'])
            row1.grid(row=0, column=0, sticky="ew", pady=self.padding_small, padx=self.padding_medium)
            row1.grid_columnconfigure(0, weight=7)
            row1.grid_columnconfigure(1, weight=5)
        row1_parent = row1 if not show_status_header else self.main_scrollable

        # === SETTINGS (left column / full width) ===
        if not show_status_header:
            file_settings_frame = tk.Frame(row1, bg=c['frame_bg'])
            file_settings_frame.grid(row=0, column=0, sticky="nsew", pady=0, padx=(0, self.padding_small))

            # Header with title
            header_frame = tk.Frame(file_settings_frame, bg=c['frame_bg'])
            header_frame.pack(fill="x", padx=4, pady=(2, 0))
            tk.Label(header_frame, text=f" {get_text('settings_title', self.lang)} ",
                    font=self.font_bold_tuple, bg=c['frame_bg'], fg=c['fg']).pack(side="left")

            file_container = tk.Frame(file_settings_frame, bg=c['frame_bg'])
            file_container.pack(fill="x", padx=4, pady=(1, 3))
            file_container.grid_columnconfigure(1, weight=1)

            # Row 0: Report file
            tk.Label(file_container, text=get_text('report_file', self.lang),
                    font=self.font_small_tuple, bg=c['frame_bg'], fg=c['fg_secondary']).grid(row=0, column=0, sticky="w", pady=1, padx=(0, 5))
            self.report_entry = tk.Entry(file_container, textvariable=self.report_file_path,
                    font=self.font_small_tuple, relief="solid", bd=1, bg=c['input_bg'], fg=c['input_fg'],
                    insertbackground=c['fg'])
            self.report_entry.grid(row=0, column=1, pady=1, padx=(0, 4), sticky="ew")
            self.browse_btn = self._btn(file_container, text=get_text('browse', self.lang), command=self.browse_report_file, style='accent', compact=True, cursor="hand2")
            self.browse_btn.grid(row=0, column=2, pady=1)

            # Row 1: Сервис Центр (full width)
            tk.Label(file_container, text="Сервис Центр:", font=self.font_small_tuple, bg=c['frame_bg'], fg=c['fg_secondary']).grid(row=1, column=0, sticky="w", pady=1, padx=(0, 5))
            login_entry = tk.Entry(file_container, textvariable=self.login, state="readonly",
                                  fg=c['fg_muted'], readonlybackground=c['bg_tertiary'], font=self.font_small_tuple,
                                  relief="solid", bd=1)
            login_entry.grid(row=1, column=1, columnspan=2, pady=1, padx=(0, 4), sticky="ew")

            # Row 2: Пароль (full width)
            tk.Label(file_container, text="Пароль:", font=self.font_small_tuple, bg=c['frame_bg'], fg=c['fg_secondary']).grid(row=2, column=0, sticky="w", pady=1, padx=(0, 5))
            password_entry = tk.Entry(file_container, textvariable=self.password, show="●",
                                     state="readonly", readonlybackground=c['bg_tertiary'], font=self.font_small_tuple,
                                     relief="solid", bd=1)
            password_entry.grid(row=2, column=1, columnspan=2, pady=1, padx=(0, 4), sticky="ew")

            # Row 3: Quick status (left)
            row3_frame = tk.Frame(file_container, bg=c['frame_bg'])
            row3_frame.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(1, 0))

            qs_font = self.font_small_tuple
            quick_status_frame = tk.Frame(row3_frame, bg=c['frame_bg'])
            quick_status_frame.pack(side="left", fill="x", expand=True)
            self.quick_status_frame = quick_status_frame

            def _qs_p(text, color):
                tk.Label(quick_status_frame, text=text, font=qs_font, fg=color, bg=c['frame_bg']).pack(side="left", padx=1)
            def _qs_s():
                tk.Label(quick_status_frame, text="|", font=qs_font, fg=c['fg_muted'], bg=c['frame_bg']).pack(side="left", padx=2)

            sched_on = self.scheduler_enabled_var.get()
            slow_on = self.slow_network_var.get()
            shut_on = self.shutdown_after_var.get()
            auto_on = self.autorun_var.get()

            sched_text = f"{'✓' if sched_on else '✗'} {get_text('scheduler_short', self.lang)}"
            if sched_on:
                sched_text += f" ({self.scheduled_time_var.get()})"
            _qs_p(sched_text, c['success'] if sched_on else c['fg_muted'])
            _qs_s()
            _qs_p(f"{'✓' if slow_on else '✗'} {get_text('slow_mode_short', self.lang)}", c['success'] if slow_on else c['fg_muted'])
            _qs_s()
            _qs_p(f"{'✓' if shut_on else '✗'} {get_text('auto_shutdown_short', self.lang)}", c['success'] if shut_on else c['fg_muted'])
            _qs_s()
            _qs_p(f"{'✓' if auto_on else '✗'} {get_text('autorun_short', self.lang)}", c['success'] if auto_on else c['fg_muted'])
            _qs_s()
            _qs_p(f"📄{self.max_empty_pages_var.get()}", c['fg_secondary'])

        else:
            file_settings_frame = tk.LabelFrame(row1_parent, text=f" {get_text('settings_title', self.lang)} ",
                                               padx=self.padding_medium, pady=self.padding_medium,
                                               font=self.font_bold_tuple, bg=c['frame_bg'], fg=c['fg'])
            file_settings_frame.grid(row=0, column=0, sticky="ew", pady=0, padx=self.padding_medium)

            file_container = tk.Frame(file_settings_frame, bg=c['frame_bg'])
            file_container.pack(fill="x", padx=5, pady=5)
            file_container.grid_columnconfigure(1, weight=1)

            tk.Label(file_container, text=get_text('report_file', self.lang),
                    font=self.font_small_tuple, bg=c['frame_bg'], fg=c['fg_secondary']).grid(row=0, column=0, sticky="w", pady=2, padx=(0, 5))
            self.report_entry = tk.Entry(file_container, textvariable=self.report_file_path,
                    font=self.font_small_tuple, relief="solid", bd=1, bg=c['input_bg'], fg=c['input_fg'],
                    insertbackground=c['fg'])
            self.report_entry.grid(row=0, column=1, pady=2, padx=(0, 4), sticky="ew")
            self.browse_btn = self._btn(file_container, text=get_text('browse', self.lang), command=self.browse_report_file, style='accent', compact=True, cursor="hand2")
            self.browse_btn.grid(row=0, column=2, pady=2)

            tk.Label(file_container, text="Сервис Центр:", font=self.font_small_tuple, bg=c['frame_bg'], fg=c['fg_secondary']).grid(row=1, column=0, sticky="w", pady=2, padx=(0, 5))
            login_entry = tk.Entry(file_container, textvariable=self.login, state="readonly",
                                  fg=c['fg_muted'], readonlybackground=c['bg_tertiary'], font=self.font_small_tuple,
                                  relief="solid", bd=1)
            login_entry.grid(row=1, column=1, columnspan=2, pady=2, padx=(0, 4), sticky="ew")

            tk.Label(file_container, text="Пароль:", font=self.font_small_tuple, bg=c['frame_bg'], fg=c['fg_secondary']).grid(row=2, column=0, sticky="w", pady=2, padx=(0, 5))
            password_entry = tk.Entry(file_container, textvariable=self.password, show="●",
                                     state="readonly", readonlybackground=c['bg_tertiary'], font=self.font_small_tuple,
                                     relief="solid", bd=1)
            password_entry.grid(row=2, column=1, columnspan=2, pady=2, padx=(0, 4), sticky="ew")

            qs_font = self.font_small_tuple
            quick_status_frame = tk.Frame(file_container, bg=c['frame_bg'])
            quick_status_frame.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(2, 0))
            self.quick_status_frame = quick_status_frame

            def _qs_p(text, color):
                tk.Label(quick_status_frame, text=text, font=qs_font, fg=color, bg=c['frame_bg']).pack(side="left", padx=1)
            def _qs_s():
                tk.Label(quick_status_frame, text="|", font=qs_font, fg=c['fg_muted'], bg=c['frame_bg']).pack(side="left", padx=2)

            sched_on = self.scheduler_enabled_var.get()
            slow_on = self.slow_network_var.get()
            shut_on = self.shutdown_after_var.get()
            auto_on = self.autorun_var.get()

            sched_text = f"{'✓' if sched_on else '✗'} {get_text('scheduler_short', self.lang)}"
            if sched_on:
                sched_text += f" ({self.scheduled_time_var.get()})"
            _qs_p(sched_text, c['success'] if sched_on else c['fg_muted'])
            _qs_s()
            _qs_p(f"{'✓' if slow_on else '✗'} {get_text('slow_mode_short', self.lang)}", c['success'] if slow_on else c['fg_muted'])
            _qs_s()
            _qs_p(f"{'✓' if shut_on else '✗'} {get_text('auto_shutdown_short', self.lang)}", c['success'] if shut_on else c['fg_muted'])
            _qs_s()
            _qs_p(f"{'✓' if auto_on else '✗'} {get_text('autorun_short', self.lang)}", c['success'] if auto_on else c['fg_muted'])
            _qs_s()
            _qs_p(f"📄{self.max_empty_pages_var.get()}", c['fg_secondary'])

        self.file_inner = file_container

        # === NOTIFICATIONS (right column, PV Bot mode only) ===
        if not show_status_header:
            notif_pv_frame = tk.Frame(row1, bg=c['frame_bg'])
            notif_pv_frame.grid(row=0, column=1, sticky="nsew", padx=(self.padding_small, 0))
            tk.Label(notif_pv_frame, text=f" {get_text('notifications', self.lang)} ",
                    font=self.font_bold_tuple, bg=c['frame_bg'], fg=c['fg']).pack(anchor="w", padx=4, pady=(2, 0))

            notif_container = tk.Frame(notif_pv_frame, bg=c['frame_bg'])
            notif_container.pack(fill="both", expand=True, padx=4, pady=(1, 3))

            notif_canvas = tk.Canvas(notif_container, highlightthickness=0, bg=c['frame_bg'], height=1)
            notif_scroll = tk.Scrollbar(notif_container, orient="vertical", command=notif_canvas.yview, width=14)
            notif_canvas.configure(yscrollcommand=notif_scroll.set)

            notif_inner_pv = tk.Frame(notif_canvas, bg=c['frame_bg'])
            ncw = notif_canvas.create_window((0, 0), window=notif_inner_pv, anchor="nw")

            def _on_cfg_pv(e, cv=notif_canvas, i=ncw):
                cv.itemconfig(i, width=e.width)
                if self._notif_after_id:
                    cv.after_cancel(self._notif_after_id)
                self._notif_after_id = cv.after_idle(lambda: self._update_notif_width())
            notif_canvas.bind('<Configure>', _on_cfg_pv)
            def _mw_pv(e, c=notif_canvas):
                c.yview_scroll(-1 * (e.delta // 120), "units")
            notif_canvas.bind('<MouseWheel>', _mw_pv)
            notif_inner_pv.bind('<MouseWheel>', _mw_pv)

            notif_canvas.pack(side="left", fill="both", expand=True)
            notif_scroll.pack(side="right", fill="y")

            self._notif_containers.append(notif_inner_pv)

        # === CONTROL BUTTONS ===
        control_frame = tk.Frame(self.main_scrollable, bg=c['bg'])
        control_frame.grid(row=1, column=0, sticky="ew", pady=(self.padding_small, self.padding_small), padx=self.padding_medium)

        self.start_button = self._btn(control_frame, text="▶ Запустить", command=self.start_full_process_thread, style='accent', compact=True, cursor="hand2")
        self.start_button.pack(side="left", padx=(0, 4))

        self.step2_button = self._btn(control_frame, text="💳 Только Шаг 2", command=self.start_step2_only_thread, style='neutral', compact=True, cursor="hand2")
        self.step2_button.pack(side="left", padx=4)

        self.delete_unpaid_button = self._btn(control_frame, text="🗑 Удалить", command=self.start_delete_unpaid_thread, style='danger', compact=True, cursor="hand2")
        self.delete_unpaid_button.pack(side="left", padx=4)

        self.stop_button = self._btn(control_frame, text=get_text('stop', self.lang), command=self.stop_processing, style='neutral', compact=True, state=tk.DISABLED, cursor="hand2")
        self.stop_button.pack(side="left", padx=4)
        
        # View toggle buttons at the right edge
        self._pvbot_view = "main"
        self._pvbot_history_btn = self._btn(control_frame, text="📜 История сессий", command=lambda: self._switch_pvbot_view("history"), style='neutral', compact=True, cursor="hand2")
        self._pvbot_history_btn.pack(side="right", padx=(2, 4))
        self._pvbot_main_btn = self._btn(control_frame, text="🏠 Главная", command=lambda: self._switch_pvbot_view("main"), style='accent', compact=True, cursor="hand2")
        self._pvbot_main_btn.pack(side="right", padx=(0, 2))

        # Permission gating for PV bot use
        if not self.has_permission('pvbot_use'):
            for btn in (self.start_button, self.step2_button, self.delete_unpaid_button):
                btn.config(state='disabled', bg=c['bg_tertiary'])
            # View-only users must not be able to pick a file either
            self._cfg('browse_btn', state='disabled', bg=c['bg_tertiary'])
            self._cfg('report_entry', state='readonly',
                      readonlybackground=c['bg_tertiary'], fg=c['fg_muted'])

        # === LOG AND RESULTS (Expands vertically) - MAIN VIEW ===
        log_results_frame = tk.Frame(self.main_scrollable, padx=self.padding_medium, bg=c['bg'])
        self._pvbot_main_view = log_results_frame
        log_results_frame.grid(row=2, column=0, sticky="nsew", pady=(self.padding_small, self.padding_small), padx=self.padding_medium)
        self.main_scrollable.grid_rowconfigure(2, weight=1)
        log_results_frame.grid_columnconfigure(0, weight=4, uniform='lr')   # Log     40%
        log_results_frame.grid_columnconfigure(1, weight=4, uniform='lr')   # Results 40%
        log_results_frame.grid_columnconfigure(2, weight=2, uniform='lr')   # Problem 20%
        log_results_frame.grid_rowconfigure(0, weight=0)  # Progress bar (fixed)
        log_results_frame.grid_rowconfigure(1, weight=1)  # Content (expands)

        # ── Progress bar / Status bar (above Results + Problem) ──
        progress_frame = tk.Frame(log_results_frame, bg=c['bg_secondary'], height=32)
        progress_frame.grid(row=0, column=1, columnspan=2, sticky="ew", padx=self.padding_small)
        progress_frame.grid_propagate(False)

        # Stage count (leftmost)
        self._progress_label = tk.Label(progress_frame, text="—/—", font=self.font_small_tuple,
                                        bg=c['bg_secondary'], fg=c['fg'])
        self._progress_label.pack(side="left", padx=(6, 4))

        # Bar background (fills remaining space between label and ETA)
        bar_bg_frame = tk.Frame(progress_frame, bg=c['bg_tertiary'], height=14)
        bar_bg_frame.pack(side="left", fill="x", expand=True, padx=(0, 4))
        bar_bg_frame.pack_propagate(False)
        self._progress_fill = tk.Frame(bar_bg_frame, bg=c['success'], width=0, height=14)
        self._progress_fill.pack(side="left")

        # Status indicator (rightmost)
        self._progress_status = tk.Label(progress_frame, text="⚪ Ожидание", font=self.font_small_tuple,
                                         bg=c['bg_secondary'], fg=c['fg_muted'])
        self._progress_status.pack(side="right", padx=(2, 2))

        # ETA (between bar and status)
        self._progress_eta = tk.Label(progress_frame, text="", font=self.font_small_tuple,
                                      bg=c['bg_secondary'], fg=c['fg_muted'], anchor="center")
        self._progress_eta.pack(side="right", padx=(0, 4))

        # Log frame (left) — row 1, col 0
        log_frame = tk.LabelFrame(log_results_frame, text=f" {get_text('operation_log', self.lang)} ", 
                                  padx=self.padding_small, pady=self.padding_small, 
                                  font=self.font_bold_tuple, bg=c['frame_bg'], fg=c['fg'])
        log_frame.grid(row=0, column=0, rowspan=2, sticky="nsew", padx=(0, self.padding_small))

        log_height = max(12, int(18 * (self.interface_size_var.get() / 50)))
        self.log_text = scrolledtext.ScrolledText(log_frame, wrap=tk.WORD, height=log_height, 
                                                  font=("Consolas", self.font_small),
                                                  bg=c['input_bg'], fg=c['input_fg'], relief="solid", bd=1,
                                                  insertbackground=c['fg'])
        self.log_text.pack(fill="both", expand=True, padx=2, pady=2)
        self.log_text.config(state="disabled")

        self.log_text.tag_config("error", foreground=c['error'])
        self.log_text.tag_config("warning", foreground=c['warning'])
        self.log_text.tag_config("info", foreground=c['input_fg'])
        self.log_text.tag_config("success", foreground=c['success'])

        # Results frame (middle) — row 1, col 1
        results_frame = tk.LabelFrame(log_results_frame, text=f" {get_text('results', self.lang)} ", 
                                      padx=self.padding_small, pady=self.padding_small, 
                                      font=self.font_bold_tuple, bg=c['frame_bg'], fg=c['fg'])
        results_frame.grid(row=1, column=1, sticky="nsew", padx=(0, self.padding_small))
        results_frame.grid_rowconfigure(1, weight=1)
        results_frame.grid_columnconfigure(0, weight=1)
        results_frame.grid_columnconfigure(1, weight=1)

        tk.Label(results_frame, text=get_text('successful_ids', self.lang), 
                font=self.font_small_tuple, bg=c['frame_bg'], fg=c['success']).grid(row=0, column=0, sticky="w", pady=(0, 2))
        self.successful_ids_text = scrolledtext.ScrolledText(results_frame, wrap=tk.WORD,
                                                             font=("Consolas", self.font_small), 
                                                             bg=c['success_bg'], fg=c['success'], relief="flat", bd=0)
        self.successful_ids_text.grid(row=1, column=0, sticky="nsew", pady=(0, 0), padx=(0, 2))

        tk.Label(results_frame, text=get_text('failed_attempts', self.lang),
                font=self.font_small_tuple, bg=c['frame_bg'], fg=c['error']).grid(row=0, column=1, sticky="w", pady=(0, 2))
        self.failed_attempts_text = scrolledtext.ScrolledText(results_frame, wrap=tk.WORD,
                                                              font=("Consolas", self.font_small), 
                                                              bg=c['error_bg'], fg=c['error'], relief="flat", bd=0)
        self.failed_attempts_text.grid(row=1, column=1, sticky="nsew", padx=(2, 0), pady=(0, 0))

        # Problem Center frame (right) — row 1, col 2
        problem_frame = tk.LabelFrame(log_results_frame, text=f" 🎯 {get_text('problem_center', self.lang)} ", 
                                      padx=self.padding_small, pady=self.padding_small, 
                                      font=self.font_bold_tuple, bg=c['frame_bg'], fg=c['fg'])
        problem_frame.grid(row=1, column=2, sticky="nsew")
        
        self._problem_canvas = tk.Canvas(problem_frame, highlightthickness=0, bg=c['input_bg'])
        _prob_scroll = AutoScrollbar(problem_frame, orient="vertical", command=self._problem_canvas.yview)
        self._problem_inner = tk.Frame(self._problem_canvas, bg=c['input_bg'])
        
        self._problem_canvas.create_window((0, 0), window=self._problem_inner, anchor="nw")
        self._problem_canvas.configure(yscrollcommand=_prob_scroll.set)
        
        def _on_prob_cfg(e):
            self._problem_canvas.configure(scrollregion=self._problem_canvas.bbox("all"))
            self._problem_canvas.itemconfig(self._problem_canvas.find_withtag("all")[0], width=e.width)
        
        self._problem_inner.bind('<Configure>', _on_prob_cfg)
        self._problem_canvas.pack(side="left", fill="both", expand=True)
        _prob_scroll.pack(side="right", fill="y")
        
        # Keep old order list references for backward compat with bot status updates
        self.order_canvas = self._problem_canvas
        self.order_list_frame = self._problem_inner
        self.bot_order_widgets = {}
        
        # Initial empty state
        self._refresh_problem_center()
        
        # === HISTORY VIEW (hidden by default) ===
        hist_frame = tk.Frame(self.main_scrollable, bg=c['bg'])
        self._pvbot_history_view = hist_frame
        hist_frame.grid(row=2, column=0, sticky="nsew", pady=(0, self.padding_small), padx=self.padding_medium)
        hist_frame.grid_rowconfigure(0, weight=1)
        hist_frame.grid_columnconfigure(0, weight=1)
        hist_frame.grid_remove()

        # ── View 1: session list (full width by default) ──
        list_view = tk.LabelFrame(hist_frame, text="📜 Список сессий",
                                  font=self.font_bold_tuple, bg=c['frame_bg'], fg=c['fg'],
                                  padx=5, pady=5)
        list_view.grid(row=0, column=0, sticky="nsew")
        list_view.grid_rowconfigure(0, weight=1)
        list_view.grid_columnconfigure(0, weight=1)
        self._pvbot_session_list_view = list_view
        self._pvbot_session_list_lf = list_view

        self._pvbot_session_tree = ttk.Treeview(
            list_view, columns=("date", "status", "total", "successful", "duration"),
            show="headings", height=6, selectmode="browse",
        )
        self._pvbot_session_tree.heading("date", text="Дата")
        self._pvbot_session_tree.heading("status", text="Статус")
        self._pvbot_session_tree.heading("total", text="Всего")
        self._pvbot_session_tree.heading("successful", text="Успешно")
        self._pvbot_session_tree.heading("duration", text="Длительность")
        self._pvbot_session_tree.column("date", width=130, minwidth=100, stretch=True)
        self._pvbot_session_tree.column("status", width=90, minwidth=70)
        self._pvbot_session_tree.column("total", width=80, minwidth=60, anchor="center")
        self._pvbot_session_tree.column("successful", width=90, minwidth=70, anchor="center")
        self._pvbot_session_tree.column("duration", width=100, minwidth=80)

        tree_scroll = AutoScrollbar(list_view, orient="vertical", command=self._pvbot_session_tree.yview)
        self._pvbot_session_tree.configure(yscrollcommand=tree_scroll.set)
        self._pvbot_session_tree.grid(row=0, column=0, sticky="nsew")
        tree_scroll.grid(row=0, column=1, sticky="ns")
        self._pvbot_session_tree.bind("<<TreeviewSelect>>", self._show_pvbot_session_detail)

        # ── View 2: session details (full width, shown on selection) ──
        detail_view = tk.LabelFrame(hist_frame, text="📄 Детали сессии",
                                    font=self.font_bold_tuple, bg=c['frame_bg'], fg=c['fg'],
                                    padx=5, pady=5)
        detail_view.grid(row=0, column=0, sticky="nsew")
        detail_view.grid_columnconfigure(0, weight=1)
        self._pvbot_session_detail_view = detail_view
        detail_view.grid_remove()

        detail_title = tk.Frame(detail_view, bg=c['frame_bg'])
        detail_title.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        self._pvbot_session_close_btn = self._btn(
            detail_title, text="✕ Закрыть",
            command=self._close_pvbot_session_detail, style='neutral', compact=True, cursor="hand2",
        )
        self._pvbot_session_close_btn.pack(side="right")
        self._pvbot_session_download_btn = self._btn(
            detail_title, text="⬇ Скачать лог",
            command=self._download_pvbot_session_log, style='accent', compact=True, cursor="hand2",
        )
        self._pvbot_session_download_btn.pack(side="right", padx=(0, 6))

        detail_view.grid_rowconfigure(1, weight=1)
        self._pvbot_session_detail = scrolledtext.ScrolledText(
            detail_view, wrap=tk.WORD,
            font=("Consolas", self.font_small),
            bg=c['input_bg'], fg=c['input_fg'],
        )
        self._pvbot_session_detail.grid(row=1, column=0, sticky="nsew")
        self._pvbot_session_detail.config(state="disabled")

        # ── SyncBar ─────────────────────────────────────────────────────
        self.sync_bar_frame = tk.Frame(self.main_container, bg=c['bg_secondary'], bd=1, relief='flat')
        self.sync_bar_frame.pack(fill='x', side='bottom', padx=5, pady=2)
        
        self.sync_bar_status = tk.Label(self.sync_bar_frame, text='⏹ Автономный режим',
            font=self.font_small_tuple, bg=c['bg_secondary'], fg=c['fg_muted'])
        self.sync_bar_status.pack(side='left', padx=8)
        
        self.sync_bar_time = tk.Label(self.sync_bar_frame, text='',
            font=self.font_small_tuple, bg=c['bg_secondary'], fg=c['fg_muted'])
        self.sync_bar_time.pack(side='right', padx=8)
        
        self.sync_bar_out = tk.Label(self.sync_bar_frame, text='', font=self.font_small_tuple,
            bg=c['bg_secondary'], fg=c.get('warning', '#E65100'))
        self.sync_bar_out.pack(side='right', padx=8)
        
        self.sync_bar_in = tk.Label(self.sync_bar_frame, text='', font=self.font_small_tuple,
            bg=c['bg_secondary'], fg=c.get('accent', '#1565C0'))
        self.sync_bar_in.pack(side='right', padx=8)

        # Poll sync status every 3 seconds
        self._poll_sync_bar()

    def _render_all_notifications(self, notifs):
        c = self.colors
        if not hasattr(self, '_notif_label_refs'):
            self._notif_label_refs = {}
        if not hasattr(self, '_notif_containers'):
            self._notif_containers = []
        for container in self._notif_containers:
            try:
                if not container.winfo_exists():
                    continue
            except:
                continue
            cvs = container.master
            scroll_frac = None
            try:
                if cvs and cvs.winfo_exists():
                    scroll_frac = cvs.yview()
            except:
                pass
            for widget in container.winfo_children():
                widget.destroy()
            refs = []
            if notifs:
                try:
                    if cvs and cvs.winfo_exists():
                        cvs.update_idletasks()
                        cw = cvs.winfo_width()
                    else:
                        container.update_idletasks()
                        cw = container.winfo_width()
                except:
                    container.update_idletasks()
                    cw = container.winfo_width()
                wl = max(80, cw - 14) if cw > 20 else 300
                for notif in notifs:
                    nc = {1: c['success'], 2: c['warning'], 3: c['error']}.get(notif.get('color', 0), c['warning'])
                    nb = {1: c['success_bg'], 2: c['warning_bg'], 3: c['error_bg']}.get(notif.get('color', 0), c['warning_bg'])
                    msg = notif['message']
                    lbl = tk.Label(container, text=f"• {msg}",
                            font=self.font_small_tuple, fg=nc, bg=nb,
                            padx=6, pady=3, anchor="w", wraplength=wl, justify="left")
                    lbl.pack(fill="x", pady=2)
                    refs.append(lbl)
            else:
                lbl = tk.Label(container, text=get_text('no_notifications', self.lang),
                        font=self.font_small_tuple, fg=c['fg_muted'], bg=c['frame_bg'])
                lbl.pack(pady=10)
                refs.append(lbl)
            self._notif_label_refs[container] = refs
            try:
                container.update_idletasks()
                if cvs and cvs.winfo_exists():
                    cvs.configure(scrollregion=cvs.bbox("all"))
                    if scroll_frac:
                        cvs.yview_moveto(scroll_frac[0])
            except:
                pass

    def _update_notif_width(self):
        self._notif_after_id = None
        if not hasattr(self, '_notif_label_refs'):
            self._notif_label_refs = {}
        for container in self._notif_containers:
            try:
                if not container.winfo_exists():
                    continue
            except:
                continue
            cvs = container.master
            try:
                if cvs and cvs.winfo_exists():
                    cvs.update_idletasks()
                    cw = cvs.winfo_width()
                else:
                    container.update_idletasks()
                    cw = container.winfo_width()
            except:
                container.update_idletasks()
                cw = container.winfo_width()
            wl = max(80, cw - 14) if cw > 20 else 300
            for lbl in self._notif_label_refs.get(container, []):
                try:
                    lbl.config(wraplength=wl)
                except:
                    pass
            try:
                container.update_idletasks()
                if cvs and cvs.winfo_exists():
                    cvs.configure(scrollregion=cvs.bbox("all"))
            except:
                pass

    def _poll_sync_bar(self):
        """Periodically update the SyncBar with latest sync status."""
        if getattr(self, '_shutting_down', False):
            return
        if getattr(self, '_poll_sync_bar_running', False):
            return
        self._poll_sync_bar_running = True
        try:
            eng = getattr(self, 'sync_engine', None)
            if eng:
                if eng.last_error:
                    self.sync_bar_status.config(text=f'❌ {str(eng.last_error)[:50]}', fg=self.colors.get('error', '#c62828'))
                elif eng.last_sync:
                    self.sync_bar_status.config(text='✅ MEGA-синхронизация активна', fg=self.colors.get('success', '#2e7d32'))
                    self.sync_bar_time.config(text=eng.last_sync.strftime('%H:%M:%S'))
                else:
                    self.sync_bar_status.config(text='⏳ Ожидание...', fg=self.colors.get('warning', '#E65100'))
                pending = eng.pending_count()
                self.sync_bar_out.config(text=f'📤 {pending}' if pending else '')
            else:
                self.sync_bar_status.config(text='⏹ Автономный режим', fg=self.colors.get('fg_muted', '#999'))
                self.sync_bar_time.config(text='')
                self.sync_bar_out.config(text='')
                self.sync_bar_in.config(text='')
        except: pass
        self._poll_sync_bar_running = False
        if not getattr(self, '_shutting_down', False):
            self._schedule(3000, self._poll_sync_bar)

    def update_bot_order_status(self, order_id, status, text=""):
        """Update or add an order to the bot order list UI."""
        c = self.colors
        
        # Status configurations: (bg_color, fg_color, label_text_key)
        status_map = {
            'pending': (c.get('bg_tertiary', '#f0f0f0'), c.get('fg_muted', '#666666'), 'status_pending'),
            'processing': ('#E1BEE7', '#7B1FA2', 'status_processing'), # Purple
            'finished': (c.get('success_bg', '#e8f5e9'), c.get('success', '#2e7d32'), 'status_finished'),
            'failed': (c.get('error_bg', '#ffebee'), c.get('error', '#c62828'), 'status_failed'),
            'partial': (c.get('warning_bg', '#fff3e0'), c.get('warning', '#ef6c00'), 'status_partial')
        }
        
        bg, fg, lang_key = status_map.get(status, (c['bg'], c['fg'], 'status_pending'))
        status_label_text = get_text(lang_key, self.lang)
        
        # If order exists, update it
        if order_id in self.bot_order_widgets:
            w = self.bot_order_widgets[order_id]
            w['frame'].config(bg=bg)
            w['title'].config(bg=bg, fg=fg)
            w['status'].config(bg=bg, fg=fg, text=status_label_text)
            if text:
                w['info'].config(bg=bg, text=text)
            
            # If finished/failed, move to bottom of its group
            if status in ['finished', 'failed', 'partial']:
                w['frame'].pack_forget()
                w['frame'].pack(fill="x", side="bottom", padx=2, pady=1)
        else:
            # Create new order widget
            f = tk.Frame(self.order_list_frame, bg=bg, padx=5, pady=3, relief="flat")
            # Pack new orders at the top (side="top" default, but we want most recent on top)
            # Actually, pending/processing should be on top.
            f.pack(fill="x", side="top", padx=2, pady=1, before=None)
            
            title_lbl = tk.Label(f, text=f"#{order_id}", font=self.font_bold_tuple, bg=bg, fg=fg, anchor="w")
            title_lbl.pack(side="left")
            
            status_lbl = tk.Label(f, text=status_label_text, font=self.font_small_tuple, bg=bg, fg=fg, padx=10)
            status_lbl.pack(side="right")
            
            info_lbl = tk.Label(f, text=text if text else "", font=("Arial", 8), bg=bg, fg=c['fg_muted'], anchor="w")
            info_lbl.pack(fill="x", pady=(2, 0))
            
            self.bot_order_widgets[order_id] = {
                'frame': f,
                'title': title_lbl,
                'status': status_lbl,
                'info': info_lbl,
                'status_val': status
            }
            
            # Re-sort pack order: Processing > Pending > Rest
            # This is complex with pack, but we can just use the provided instructions:
            # "most recent on top ... failed/finished go down"
            f.pack_forget()
            if status in ['finished', 'failed', 'partial']:
                f.pack(fill="x", side="bottom", padx=2, pady=1)
            else:
                # Put at the very top
                packed_items = self.order_list_frame.pack_slaves()
                f.pack(fill="x", side="top", padx=2, pady=1, before=packed_items[0] if packed_items else None)
        self._update_pvbot_progress()

    def refresh_quick_status(self):
        """Refresh the quick status indicators in the PV Bot tab."""
        if not hasattr(self, 'quick_status_frame'):
            return
        c = self.colors
        for w in self.quick_status_frame.winfo_children():
            w.destroy()
        sep_bg = c.get('frame_bg', '#f0f0f0')

        def _qs_pack(text, color):
            tk.Label(self.quick_status_frame, text=text, font=self.font_small_tuple, fg=color, bg=sep_bg).pack(side="left", padx=1)
        def _qs_sep():
            tk.Label(self.quick_status_frame, text="|", font=self.font_small_tuple, fg=c['fg_muted'], bg=sep_bg).pack(side="left", padx=2)

        sched_on = self.scheduler_enabled_var.get()
        slow_on = self.slow_network_var.get()
        shut_on = self.shutdown_after_var.get()
        auto_on = self.autorun_var.get()

        sched_text = f"{'✓' if sched_on else '✗'} {get_text('scheduler_short', self.lang)}"
        if sched_on:
            sched_text += f" ({self.scheduled_time_var.get()})"
        _qs_pack(sched_text, c['success'] if sched_on else c['fg_muted'])
        _qs_sep()
        _qs_pack(f"{'✓' if slow_on else '✗'} {get_text('slow_mode_short', self.lang)}", c['success'] if slow_on else c['fg_muted'])
        _qs_sep()
        _qs_pack(f"{'✓' if shut_on else '✗'} {get_text('auto_shutdown_short', self.lang)}", c['success'] if shut_on else c['fg_muted'])
        _qs_sep()
        _qs_pack(f"{'✓' if auto_on else '✗'} {get_text('autorun_short', self.lang)}", c['success'] if auto_on else c['fg_muted'])
        _qs_sep()
        _qs_pack(f"📄{self.max_empty_pages_var.get()}", c['fg_secondary'])

        lr = getattr(self, '_pvbot_last_run', None)
        if lr and lr.get('date'):
            _qs_sep()
            st = lr.get('status', '')
            icon = {'ok': '✅', 'error': '❌', 'no_file': '⚠️', 'no_run': '⏭️'}.get(st, 'ℹ️')
            color = c['success'] if st == 'ok' else (c['error'] if st == 'error' else c['warning'])
            _qs_pack(f"{icon} Посл. запуск: {lr.get('date')} {lr.get('time', '')} — {lr.get('status_text', st)}",
                     color)

    def _init_progress(self, total_stages):
        """Initialize/reset the progress tracking for a new automation run."""
        self._total_stages = total_stages
        self._completed_stages = 0
        self._stage_timestamps = []
        self._stage_durations = []
        self._process_start_time = datetime.now()
        self._set_process_status("preparing")
        self._refresh_progress_display()
        self._update_bot_progress_legacy()

    def _advance_stage(self, stage_name=None):
        """Advance progress by one stage and refresh the display."""
        self._completed_stages += 1
        now = datetime.now()
        if self._stage_timestamps:
            elapsed = (now - self._stage_timestamps[-1]).total_seconds()
            self._stage_durations.append(elapsed)
        self._stage_timestamps.append(now)
        self._set_process_status("in_progress")
        self._refresh_progress_display()

    def _set_process_status(self, status):
        """Update the status indicator label."""
        self._process_status = status
        if not hasattr(self, '_progress_status') or not self._progress_status.winfo_exists():
            return
        c = self.colors
        status_config = {
            "waiting":     ("⚪ Ожидание",      c.get('fg_muted', '#999')),
            "preparing":   ("🔵 Подготовка",    c.get('accent', '#1976D2')),
            "in_progress": ("🟢 В процессе",    c.get('success', '#388E3C')),
            "completed":   ("🟢 Завершено",     c.get('success', '#388E3C')),
            "paused":      ("🟡 Приостановлено", c.get('warning', '#F57F17')),
            "error":       ("🔴 Ошибка",        c.get('error', '#D32F2F')),
            "aborted":     ("🔴 Прервано",      c.get('error', '#D32F2F')),
        }
        text, color = status_config.get(status, ("⚪ Ожидание", c.get('fg_muted', '#999')))
        self._progress_status.config(text=text, fg=color)

    def _refresh_progress_display(self):
        """Refresh the progress bar, stage count, ETA, and status."""
        if not hasattr(self, '_progress_label') or not self._progress_label.winfo_exists():
            return
        c = self.colors
        total = max(self._total_stages, 1)
        done = min(self._completed_stages, total)

        # Update bar
        pct = done / total
        if hasattr(self, '_progress_fill') and self._progress_fill.winfo_exists():
            bar_width = max(2, int(self._progress_fill.master.winfo_width() * pct))
            self._progress_fill.config(width=bar_width)

        # Update label
        self._progress_label.config(text=f"{done}/{total} этапов")

        # Update ETA or completion time
        if self._process_status == "completed":
            elapsed_total = sum(self._stage_durations)
            mins = int(elapsed_total // 60)
            secs = int(elapsed_total % 60)
            if mins > 0:
                self._progress_eta.config(text=f"✔ Завершено за {mins} мин {secs} сек", fg=c.get('success', '#388E3C'))
            else:
                self._progress_eta.config(text=f"✔ Завершено за {secs} сек", fg=c.get('success', '#388E3C'))
        elif self._process_status in ("error", "aborted"):
            self._progress_eta.config(text="", fg=c.get('fg_muted', '#999'))
        elif done > 0 and len(self._stage_durations) >= 2:
            avg = sum(self._stage_durations[1:]) / len(self._stage_durations[1:])
            remaining = avg * (total - done)
            if remaining < 60:
                self._progress_eta.config(text=f"ETA < 1 мин", fg=c.get('fg_muted', '#999'))
            else:
                mins = int(remaining // 60)
                secs = int(remaining % 60)
                self._progress_eta.config(text=f"ETA ≈ {mins:02d}:{secs:02d}", fg=c.get('fg_muted', '#999'))
        else:
            self._progress_eta.config(text="ETA: расчет...", fg=c.get('fg_muted', '#999'))

    def _update_pvbot_progress(self):
        """Legacy method — called by update_bot_order_status, now delegates to stage-based tracking."""
        if hasattr(self, '_total_stages') and self._total_stages > 0:
            self._refresh_progress_display()
        else:
            self._update_bot_progress_legacy()

    def _update_bot_progress_legacy(self):
        """Original order-count progress (fallback when stage tracking not initialized)."""
        if not hasattr(self, '_progress_label') or not self._progress_label.winfo_exists():
            return
        total = len(self.bot_order_widgets)
        done = sum(1 for w in self.bot_order_widgets.values() if w['status_val'] in ('finished', 'failed', 'partial'))
        self._progress_label.config(text=f"{done}/{total}")
        if total > 0:
            pct = done / total
            if hasattr(self, '_progress_fill') and self._progress_fill.winfo_exists():
                bar_width = max(2, int(self._progress_fill.master.winfo_width() * pct))
                self._progress_fill.config(width=bar_width)

    def _switch_pvbot_view(self, view):
        c = self.colors
        if view == "history":
            self._pvbot_main_view.grid_remove()
            self._pvbot_history_view.grid()
            # List opens at full width; details shown only after selecting a session
            if hasattr(self, '_pvbot_session_list_view'):
                self._pvbot_session_list_view.grid()
            if hasattr(self, '_pvbot_session_detail_view'):
                self._pvbot_session_detail_view.grid_remove()
            self._pvbot_history_btn.config(bg=c['accent'], fg='white')
            self._pvbot_main_btn.config(bg=c['bg_tertiary'], fg=c['fg'])
            self.main_scrollable.grid_rowconfigure(2, weight=1)
            self.master.update()
            self._refresh_pvbot_history()
        else:
            self._pvbot_history_view.grid_remove()
            self._pvbot_main_view.grid()
            self._pvbot_main_btn.config(bg=c['accent'], fg='white')
            self._pvbot_history_btn.config(bg=c['bg_tertiary'], fg=c['fg'])
            self.main_scrollable.grid_rowconfigure(2, weight=1)
            self.master.update()
        self._pvbot_view = view

    def _get_latest_session_data(self):
        """Get summary data from the most recent session .dat file."""
        logs_dir = settings.LOGS_DIR
        if not logs_dir:
            if sys.platform == 'darwin':
                logs_dir = os.path.expanduser("~/Library/Application Support/PVM/Logs")
            elif sys.platform == 'win32':
                logs_dir = os.path.join(os.environ.get('LOCALAPPDATA', os.path.expanduser("~")), 'Microsoft', 'Office', 'SmartBridge', 'cache')
            if logs_dir and not os.path.isdir(logs_dir):
                try:
                    os.makedirs(logs_dir)
                except:
                    pass
        if not logs_dir or not os.path.isdir(logs_dir):
            return None

        sessions = []
        for fname in os.listdir(logs_dir):
            if not fname.endswith('.dat'):
                continue
            fpath = os.path.join(logs_dir, fname)
            try:
                content = open(fpath, 'r', encoding='utf-8').read()
                mtime = os.path.getmtime(fpath)
            except:
                continue

            date = ""
            duration = ""
            total = 0
            successful = 0
            total_sales = 0.0
            avg_time = 0.0

            for line in content.split('\n'):
                line = line.strip()
                if line.startswith('Date:'):
                    date = line.split(':', 1)[1].strip()
                elif line.startswith('Duration:'):
                    duration = line.split(':', 1)[1].strip()
                elif line.startswith('Total Orders:'):
                    try: total = int(line.split(':')[1].strip())
                    except: pass
                elif line.startswith('✅ Successful:'):
                    try: successful = int(line.split(':')[1].strip())
                    except: pass
                elif line.startswith('💰 Total Sales:'):
                    try:
                        val = line.split(':', 1)[1].strip().replace(' ', '').replace(',', '')
                        total_sales = float(val)
                    except: pass
                elif line.startswith('Avg/Order:'):
                    try:
                        avg_time = float(line.split(':')[1].strip().replace('sec', '').strip())
                    except: pass

            if total < 10:
                continue

            sessions.append({
                'date': date, 'duration': duration,
                'total': total, 'successful': successful,
                'total_sales': total_sales, 'avg_time': avg_time,
                'mtime': mtime,
            })

        if not sessions:
            return None
        sessions.sort(key=lambda x: x['mtime'], reverse=True)
        return sessions[0]

    def _refresh_problem_center(self):
        c = self.colors
        for w in self._problem_inner.winfo_children():
            w.destroy()
        pw = self._problem_canvas.winfo_width() - 16
        if pw < 80:
            pw = 160

        failed = getattr(self, 'failed_attempts_text', None)
        has_problems = False
        if failed:
            failed_text = failed.get(1.0, tk.END).strip()
            has_problems = bool(failed_text)
        has_partial = any(
            w.get('status_val') == 'partial'
            for w in getattr(self, 'bot_order_widgets', {}).values()
        )

        has_issues = has_problems or has_partial
        session = self._get_latest_session_data()

        if has_issues:
            problem_count = len(getattr(self, 'failed_attempts', [])) + sum(
                1 for w in getattr(self, 'bot_order_widgets', {}).values()
                if w.get('status_val') == 'partial'
            )
            action_key = ('problem_center_requires_action_one'
                          if problem_count == 1 else
                          'problem_center_requires_action_many')
            lbl = tk.Label(self._problem_inner, text=get_text(action_key, self.lang),
                           font=self.font_normal_tuple, bg=c['input_bg'], fg=c['error'],
                           anchor="w", wraplength=pw)
            lbl.pack(fill="x", padx=6, pady=(4, 2))
            for wid in getattr(self, 'bot_order_widgets', {}).values():
                if wid.get('status_val') in ('failed', 'partial'):
                    order_id = wid.get('title', '').cget('text') if hasattr(wid.get('title'), 'cget') else ''
                    info = wid.get('info', '').cget('text') if hasattr(wid.get('info'), 'cget') else ''
                    entry = tk.Frame(self._problem_inner, bg=c['error_bg'], padx=4, pady=2, cursor="hand2")
                    entry.pack(fill="x", padx=2, pady=1)
                    tk.Label(entry, text=f"#{order_id}", font=self.font_bold_tuple,
                             bg=c['error_bg'], fg=c['error']).pack(anchor="w")
                    if info:
                        tk.Label(entry, text=info, font=("Arial", 8),
                                 bg=c['error_bg'], fg=c['fg_muted'], wraplength=pw - 8,
                                 anchor="w").pack(anchor="w")

            sep = tk.Frame(self._problem_inner, bg=c['border'], height=1)
            sep.pack(fill="x", padx=6, pady=(6, 4))
            tk.Label(self._problem_inner,
                     text=get_text('problem_center_current_run', self.lang).format(count=problem_count),
                     font=self.font_small_tuple, bg=c['input_bg'], fg=c['fg_muted'],
                     anchor="w", wraplength=pw).pack(fill="x", padx=6, pady=(0, 4))
            try:
                self.enable_scroll_area(self._problem_canvas, self._problem_inner)
            except Exception:
                pass
            return

        # ── No issues: show status + last session analytics ──
        tk.Label(self._problem_inner, text=get_text('problem_center_clean', self.lang),
                 font=self.font_normal_tuple, bg=c['input_bg'], fg=c['success'],
                 anchor="w", wraplength=pw).pack(fill="x", padx=6, pady=(8, 2))

        if session:
            tk.Label(self._problem_inner, text="Последняя сессия",
                     font=self.font_small_bold_tuple, bg=c['input_bg'], fg=c['fg'],
                     anchor="w").pack(fill="x", padx=6, pady=(8, 1))

            tk.Label(self._problem_inner,
                     text=f"{session['date']} · {session['total']} заказов",
                     font=self.font_small_tuple, bg=c['input_bg'], fg=c['fg_secondary'],
                     anchor="w", wraplength=pw).pack(fill="x", padx=6, pady=1)

            tk.Label(self._problem_inner,
                     text=f"✅ {session['successful']} успешно",
                     font=self.font_small_tuple, bg=c['input_bg'], fg=c['success'],
                     anchor="w").pack(fill="x", padx=6, pady=1)

            if session['duration']:
                tk.Label(self._problem_inner,
                         text=f"⏱ {session['duration']}",
                         font=self.font_small_tuple, bg=c['input_bg'], fg=c['fg_secondary'],
                         anchor="w").pack(fill="x", padx=6, pady=1)

            if session['avg_time']:
                tk.Label(self._problem_inner,
                         text=f"⚡ {session['avg_time']:.1f} сек / заказ",
                         font=self.font_small_tuple, bg=c['input_bg'], fg=c['fg_secondary'],
                         anchor="w").pack(fill="x", padx=6, pady=1)

            if session['total_sales']:
                formatted = self.format_amount(session['total_sales']) + " ₸"
                tk.Label(self._problem_inner,
                         text=f"💰 {formatted}",
                         font=self.font_small_tuple, bg=c['input_bg'], fg=c['fg_secondary'],
                         anchor="w").pack(fill="x", padx=6, pady=1)
        else:
            last_scan = getattr(self, '_process_start_time', None)
            last_scan_str = last_scan.strftime("%d.%m.%Y %H:%M") if last_scan else ''
            if last_scan_str:
                tk.Label(self._problem_inner, text=get_text('last_scan', self.lang),
                         font=self.font_small_tuple, bg=c['input_bg'], fg=c['fg_muted'],
                         anchor="w", wraplength=pw).pack(fill="x", padx=6, pady=(8, 0))
                tk.Label(self._problem_inner, text=last_scan_str,
                         font=self.font_small_tuple, bg=c['input_bg'], fg=c['fg'],
                         anchor="w", wraplength=pw).pack(fill="x", padx=6, pady=(0, 2))
            op_count = len(getattr(self, 'bot_order_widgets', {}))
            tk.Label(self._problem_inner, text=get_text('operations_checked', self.lang),
                     font=self.font_small_tuple, bg=c['input_bg'], fg=c['fg_muted'],
                     anchor="w", wraplength=pw).pack(fill="x", padx=6, pady=(4, 0))
            tk.Label(self._problem_inner, text=str(op_count) if op_count else '0',
                     font=self.font_small_tuple, bg=c['input_bg'], fg=c['fg'],
                     anchor="w", wraplength=pw).pack(fill="x", padx=6, pady=(0, 2))

        # Re-apply wheel + pan (children were just rebuilt)
        try:
            self.enable_scroll_area(self._problem_canvas, self._problem_inner)
        except Exception:
            pass

    def _refresh_pvbot_history(self):
        if not hasattr(self, '_pvbot_session_tree') or not self._pvbot_session_tree.winfo_exists():
            return
        for row in self._pvbot_session_tree.get_children():
            self._pvbot_session_tree.delete(row)
        logs_dir = settings.LOGS_DIR
        if not logs_dir:
            if sys.platform == 'darwin':
                logs_dir = os.path.expanduser("~/Library/Application Support/PVM/Logs")
            elif sys.platform == 'win32':
                logs_dir = os.path.join(os.environ.get('LOCALAPPDATA', os.path.expanduser("~")), 'Microsoft', 'Office', 'SmartBridge', 'cache')
            if logs_dir and not os.path.isdir(logs_dir):
                try:
                    os.makedirs(logs_dir)
                except:
                    pass
        if not logs_dir or not os.path.isdir(logs_dir):
            if hasattr(self, '_pvbot_session_list_lf'):
                self._pvbot_session_list_lf.config(text="📜 Список сессий (нет сессий)")
            return
        sessions = []
        for fname in os.listdir(logs_dir):
            if not fname.endswith('.dat'):
                continue
            fpath = os.path.join(logs_dir, fname)
            try:
                content = open(fpath, 'r', encoding='utf-8').read()
            except:
                continue
            date = ""
            duration = ""
            total = 0
            successful = 0
            for line in content.split('\n'):
                line = line.strip()
                if line.startswith('Date:'):
                    date = line.split(':', 1)[1].strip()
                elif line.startswith('Duration:'):
                    duration = line.split(':', 1)[1].strip()
                elif line.startswith('Total Orders:'):
                    try:
                        total = int(line.split(':')[1].strip())
                    except:
                        pass
                elif line.startswith('✅ Successful:'):
                    try:
                        successful = int(line.split(':')[1].strip())
                    except:
                        pass
            if total < 10:
                continue
            status = "✅" if successful == total else ("⚠️" if successful > 0 else "❌")
            try:
                mtime = os.path.getmtime(fpath)
            except:
                mtime = 0
            sessions.append((fpath, date, status, total, successful, duration, mtime))
        sessions.sort(key=lambda x: x[6], reverse=True)
        if not sessions:
            if hasattr(self, '_pvbot_session_list_lf'):
                self._pvbot_session_list_lf.config(text="📜 Список сессий (нет сессий)")
            return
        if hasattr(self, '_pvbot_session_list_lf'):
            self._pvbot_session_list_lf.config(text="📜 Список сессий")
        for fpath, date, status, total, successful, duration, _ in sessions:
            self._pvbot_session_tree.insert("", "end", values=(
                date, status, total, successful, duration
            ), tags=(fpath,))

    def _show_pvbot_session_detail(self, event=None):
        sel = self._pvbot_session_tree.selection()
        if not sel:
            # No selection — back to the full-width list
            self._close_pvbot_session_detail()
            return
        # Show details full width, hide the list
        if hasattr(self, '_pvbot_session_list_view'):
            self._pvbot_session_list_view.grid_remove()
        if hasattr(self, '_pvbot_session_detail_view'):
            self._pvbot_session_detail_view.grid()
        item = self._pvbot_session_tree.item(sel[0])
        tags = item.get("tags", ())
        if not tags:
            return
        fpath = tags[0]
        self._pvbot_selected_log_path = fpath
        try:
            content = open(fpath, 'r', encoding='utf-8').read()
        except:
            content = "Не удалось прочитать файл сессии."
        self._pvbot_session_detail.config(state="normal")
        self._pvbot_session_detail.delete("1.0", tk.END)
        self._pvbot_session_detail.insert("1.0", content)
        self._pvbot_session_detail.config(state="disabled")

    def _download_pvbot_session_log(self):
        """Copy the selected local session log to the user's Downloads folder."""
        fpath = getattr(self, '_pvbot_selected_log_path', '')
        if not fpath or not os.path.isfile(fpath):
            self.show_toast("Сначала выберите сессию", "warning")
            return
        downloads = os.path.expanduser('~/Downloads')
        desktop = os.path.expanduser('~/Desktop')
        target_dir = downloads if os.path.isdir(downloads) else desktop
        try:
            os.makedirs(target_dir, exist_ok=True)
            target = os.path.join(target_dir, os.path.basename(fpath))
            if os.path.abspath(target) != os.path.abspath(fpath):
                shutil.copy2(fpath, target)
            self.show_toast(f"Лог сохранён: {target}", "success")
        except Exception as e:
            self.show_toast(f"Не удалось скачать лог: {e}", "error")

    def _close_pvbot_session_detail(self):
        """Close session details and return to the full-width session list."""
        try:
            self._pvbot_session_tree.selection_remove(self._pvbot_session_tree.selection())
        except Exception:
            pass
        self._pvbot_selected_log_path = None
        if hasattr(self, '_pvbot_session_detail_view'):
            self._pvbot_session_detail_view.grid_remove()
        if hasattr(self, '_pvbot_session_list_view'):
            self._pvbot_session_list_view.grid()

    def _fetch_pricing_data(self):
        if not self._should_show_pricing():
            tf = getattr(self, '_tariff_frame', None)
            if tf:
                try: tf.grid_remove()
                except: pass
            return
        tf = getattr(self, '_tariff_frame', None)
        if tf:
            try: tf.grid()
            except: pass
        self._pricing_data = None
        def _fetch():
            try:
                supabase = get_supabase_client()
                if supabase is None:
                    return
                login = self.login.get().strip()
                if not login:
                    return
                result = supabase.table('pricing').select('*').eq('login', login).order('year', desc=True).order('month', desc=True).limit(1).execute()
                if result.data:
                    self._pricing_data = result.data[0]
            except Exception as e:
                print(f"Error fetching pricing: {e}")
        threading.Thread(target=_fetch, daemon=True).start()
        def _poll():
            if getattr(self, '_pricing_data', None):
                data = self._pricing_data
                self._pricing_data = None
                self._display_pricing(data)
            else:
                try:
                    if self.master and self.master.winfo_exists():
                        self.master.after(200, _poll)
                except:
                    pass
        try:
            if self.master and self.master.winfo_exists():
                self.master.after(200, _poll)
        except:
            pass

    def _display_pricing(self, row):
        try:
            fee = max(row.get('final_fee', 0) or 0, 1)
            sub_end = self.activation_end if getattr(self, 'activation_end', None) else ''
            for attr, text in [('_tariff_price_lbl', f"{int(fee):,} ₸".replace(',', ' ')),
                               ('_tariff_date_lbl', f"• До {sub_end}" if sub_end else '')]:
                lbl = getattr(self, attr, None)
                if lbl and lbl.winfo_exists():
                    lbl.config(text=text)
        except Exception as e:
            print(f"Error displaying pricing: {e}")
