# -*- coding: utf-8 -*-
"""
PVM.core - Settings Tab Mixin
================================
Settings tab: general, appearance, printer/receipt, automation,
users/permissions, system.
"""

import os
import sys
import time
import threading
import subprocess
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from datetime import datetime
from typing import Optional

import requests

import settings
import receipt_printer
from ui_lang import get_text, MODULE_VERSION
from ui_dialogs import AutoScrollbar, ToolTip
from pvm_core import EmailService, TelegramService


class SettingsTabMixin:
    """Settings tab methods for GreenLeafApp."""

    def _track_changes(self, *args):
        self._unsaved_changes = True
        if hasattr(self, 'btn_save_settings') and self.btn_save_settings.winfo_exists():
            self.btn_save_settings.config(text="💾 Сохранить изменения ●", state="normal",
                                          bg=self.colors['warning'], fg="white")

    def _mark_saved(self):
        self._unsaved_changes = False
        if hasattr(self, 'btn_save_settings') and self.btn_save_settings.winfo_exists():
            self.btn_save_settings.config(text="💾 Сохранить изменения", state="normal",
                                          bg=self.colors['success'], fg="white")

    def _attach_settings_traces(self):
        vars_to_trace = [
            self.language_var, self.theme_var,
            self.scale_preset_var, self.scheduler_enabled_var,
            self.scheduled_time_var, self.watch_directory_var,
            self.shutdown_after_var, self.autorun_var, self.slow_network_var,
            self.sync_name_var, self.live_bot_v2_var, self.live_bot_delay_var,
            self._printer_var, self._paper_width_var,
            self._auto_print_var, self._auto_cut_var,
            self._text_scale_var,
            self._show_partner_var, self._partial_id_var,
            self._show_partner_phone_var, self._show_pv_var,
            self.toast_size_var, self.toast_alpha_var, self.toast_position_var,
            self.skip_low_stock_warning_var,
            self.email_enabled_var, self.smtp_server_var, self.smtp_port_var,
            self.smtp_user_var, self.smtp_pwd_var, self.email_recipient_var,
            self.tg_enabled_var, self.tg_token_var, self.tg_chat_id_var,
            self.send_report_on_exit_var, self.require_otp_var
        ]
        if hasattr(self, '_receipt_vars'):
            vars_to_trace.extend(self._receipt_vars.values())
        for v in vars_to_trace:
            v.trace_add('write', self._track_changes)

    def create_settings_tab(self):
        """Create the settings tab with all configuration options in a side-menu layout."""
        c = self.colors
        self._unsaved_changes = False
        self._receipt_config = settings.get_receipt_config()
        
        self.settings_frame.grid_rowconfigure(0, weight=1)
        self.settings_frame.grid_rowconfigure(1, weight=0)
        self.settings_frame.grid_columnconfigure(1, weight=1)

        nav_frame = tk.Frame(self.settings_frame, bg=c['bg_secondary'], width=220)
        nav_frame.grid(row=0, column=0, sticky="ns")
        nav_frame.pack_propagate(False)

        self.settings_content_frame = tk.Frame(self.settings_frame, bg=c['bg'])
        self.settings_content_frame.grid(row=0, column=1, sticky="nsew")

        bottom_bar = tk.Frame(self.settings_frame, bg=c['bg_secondary'], height=60)
        bottom_bar.grid(row=1, column=0, columnspan=2, sticky="ew")
        bottom_bar.pack_propagate(False)
        
        self.btn_save_settings = self._btn(bottom_bar, text="💾 Сохранить изменения", command=self.save_all_settings, style='success', state="normal", cursor="hand2")
        self.btn_save_settings.pack(side="right", pady=10, padx=20)

        self.settings_pages = {}
        self.nav_buttons = {}

        def create_scrollable_page():
            canvas = tk.Canvas(self.settings_content_frame, highlightthickness=0, bg=c['bg'])
            scrollbar = AutoScrollbar(self.settings_content_frame, orient="vertical", command=canvas.yview)
            scrollable = tk.Frame(canvas, bg=c['bg'])
            cw = canvas.create_window((0, 0), window=scrollable, anchor="nw")
            
            _prev_bbox = [None]
            _scroll_timer = [None]
            def configure_scroll_region(event):
                if _scroll_timer[0]:
                    canvas.after_cancel(_scroll_timer[0])
                _scroll_timer[0] = canvas.after_idle(lambda: _update_sr())
            def _update_sr():
                scrollable.update_idletasks()
                bb = canvas.bbox("all")
                if bb != _prev_bbox[0]:
                    _prev_bbox[0] = bb
                    canvas.configure(scrollregion=bb)
            def configure_canvas_width(event):
                canvas.itemconfig(cw, width=event.width)
            
            scrollable.bind("<Configure>", configure_scroll_region)
            canvas.bind("<Configure>", configure_canvas_width)
            canvas.configure(yscrollcommand=scrollbar.set, highlightthickness=0, borderwidth=0)
            
            # Universal mousewheel + drag-pan (bind after the page is built)
            canvas.after(100, lambda: self.enable_scroll_area(canvas, scrollable))
            return canvas, scrollbar, scrollable

        def switch_page(page_id):
            # Do NOT discard changes on tab switch anymore.
            # The global Save button at the bottom handles everything.
            
            for pid, (canv, sb, sf) in self.settings_pages.items():
                canv.pack_forget()
                sb.pack_forget()
                if pid in self.nav_buttons:
                    self.nav_buttons[pid].config(bg=c['bg_secondary'], fg=c['fg_secondary'], font=self.font_normal_tuple)
            
            canv, sb, sf = self.settings_pages[page_id]
            canv.pack(side="left", fill="both", expand=True)
            sb.pack(side="right", fill="y")
            self.nav_buttons[page_id].config(bg=c['bg'], fg=c['accent'], font=self.font_bold_tuple)
            
            # Auto-refresh certain data
            if page_id == 'system':
                try:
                    self._update_sync_health_panel()
                except Exception:
                    pass
            elif page_id == 'printer':
                self._update_receipt_preview()
                self._refresh_printers(silent=True)
            elif page_id == 'users':
                self._refresh_users_list()
            elif page_id == 'database':
                self._refresh_db_statistics()

        c_mai, sb_mai, sf_mai = create_scrollable_page()
        self.settings_pages['main'] = (c_mai, sb_mai, sf_mai)
        self._build_settings_main(sf_mai)

        c_app, sb_app, sf_app = create_scrollable_page()
        self.settings_pages['appearance'] = (c_app, sb_app, sf_app)
        self._build_settings_appearance(sf_app)

        c_prn, sb_prn, sf_prn = create_scrollable_page()
        self.settings_pages['printer'] = (c_prn, sb_prn, sf_prn)
        self._build_settings_printer(sf_prn)

        c_aut, sb_aut, sf_aut = create_scrollable_page()
        self.settings_pages['automation'] = (c_aut, sb_aut, sf_aut)
        self._build_settings_automation(sf_aut)

        if self.has_permission('user_management'):
            c_usr, sb_usr, sf_usr = create_scrollable_page()
            self.settings_pages['users'] = (c_usr, sb_usr, sf_usr)
            self._build_settings_users(sf_usr)

        c_sys, sb_sys, sf_sys = create_scrollable_page()
        self.settings_pages['system'] = (c_sys, sb_sys, sf_sys)
        self._build_settings_system(sf_sys)

        c_int, sb_int, sf_int = create_scrollable_page()
        self.settings_pages['integrations'] = (c_int, sb_int, sf_int)
        self._build_settings_integrations(sf_int)

        c_db, sb_db, sf_db = create_scrollable_page()
        self.settings_pages['database'] = (c_db, sb_db, sf_db)
        self._build_settings_database(sf_db)

        tk.Label(nav_frame, text="Настройки", font=self.font_title_tuple, bg=c['bg_secondary'], fg=c['fg']).pack(pady=(20, 20), anchor="w", padx=15)

        nav_items = [
            ('main', 'Главная'),
            ('appearance', 'Внешний вид'),
            ('printer', 'Принтер и Чек'),
            ('automation', 'Автоматизация'),
        ]

        if self.has_permission('user_management'):
            nav_items.append(('users', 'Пользователи\nи права'))

        nav_items.extend([
            ('integrations', 'Интеграции'),
            ('system', get_text('system_tab', self.lang))
        ])
        
        # Database management — permission-gated
        if self.has_permission('settings_database'):
            nav_items.insert(-1, ('database', 'База данных'))

        for pid, text in nav_items:
            btn = self._btn(nav_frame, text=text, command=lambda p=pid: switch_page(p), style='neutral')
            btn.pack(fill="x", pady=2)
            self.nav_buttons[pid] = btn

        self._attach_settings_traces()
        switch_page('main')
        self.master.after(500, lambda: self._refresh_printers(silent=True))

    def _build_settings_main(self, parent):
        """Create the main settings dashboard (Главная) with consolidated info and notifications."""
        c = self.colors
        from ui_lang import get_text
        import re

        # 1. CONSOLIDATED TOP HEADER: DEVICE + USER + UPDATE STATUS
        lf_header = tk.LabelFrame(parent, text=f" ⚙️ {get_text('system_tab', self.lang)} ", padx=15, pady=15, 
                                 font=self.font_bold_tuple, bg=c['bg'], fg=c['accent'])
        lf_header.pack(fill="x", padx=20, pady=(20, 10))
        
        header_grid = tk.Frame(lf_header, bg=c['bg'])
        header_grid.pack(fill="x", padx=5)
        header_grid.grid_columnconfigure(0, weight=2) # Device name gets more space
        header_grid.grid_columnconfigure(1, weight=1)
        header_grid.grid_columnconfigure(2, weight=1)
        
        # --- COLUMN 0: DEVICE NAME ---
        dev_name = getattr(settings, 'SYNC_NAME', getattr(self, 'sync_name', 'Unnamed Device'))
        name_container = tk.Frame(header_grid, bg=c['bg'])
        name_container.grid(row=0, column=0, sticky="w")
        
        tk.Label(name_container, text=get_text('device_name', self.lang), 
                 font=self.font_small_tuple, bg=c['bg'], fg=c['fg_muted']).pack(side="top", anchor="w")
        
        inner_name = tk.Frame(name_container, bg=c['bg'])
        inner_name.pack(side="top", anchor="w", pady=(2, 0))
        
        # Display name with a fallback logic
        sync_meta = settings.get_sync_settings()
        display_name = sync_meta.get('sync_name', dev_name)
        if not display_name or display_name == "Unnamed Device":
            display_name = f"Device {settings.get_or_create_device_key()[:4]}"
            
        self.dev_name_label = tk.Label(inner_name, text=display_name, 
                                       font=self.font_bold_tuple, bg=c['bg'], fg=c['fg'])
        self.dev_name_label.pack(side="left")
        
        # Reactive Update: Ensure label updates when sync_name_var changes
        def _update_header_label(*args):
            new_name = self.sync_name_var.get()
            if not new_name.strip():
                new_name = f"Device {settings.get_or_create_device_key()[:4]}"
            self.dev_name_label.config(text=new_name)
        
        self.sync_name_var.trace_add('write', _update_header_label)
        
        self.dev_name_edit_btn = self._btn(inner_name, text=" ✎ ", command=self._edit_device_name, style='neutral', compact=True, cursor="hand2")
        self.dev_name_edit_btn.pack(side="left", padx=8)
        
        # --- COLUMN 1: USER INFO ---
        u_name = getattr(self, 'current_username', 'admin')
        u_role = getattr(self, 'current_role', 'admin')
        u_role_label = settings.ROLE_LABELS.get(u_role, u_role)
        
        user_container = tk.Frame(header_grid, bg=c['bg'])
        user_container.grid(row=0, column=1, sticky="w")
        
        tk.Label(user_container, text="Пользователь", font=self.font_small_tuple, bg=c['bg'], fg=c['fg_muted']).pack(anchor="w")
        user_row = tk.Frame(user_container, bg=c['bg'])
        user_row.pack(anchor="w", pady=(2, 0))
        tk.Label(user_row, text=f"{u_name} ({u_role_label})", font=self.font_normal_tuple, bg=c['bg'], fg=c['fg_secondary']).pack(side="left")
        self._btn(user_row, text="⇄ Сменить", command=self.request_switch_user, style='neutral', compact=True, cursor='hand2').pack(side="left", padx=(8, 0))
        
        # Updates handled automatically at startup

        # --- COLUMN 2: UPDATE STATUS ---
        update_container = tk.Frame(header_grid, bg=c['bg'])
        update_container.grid(row=0, column=2, sticky="w")
        tk.Label(update_container, text="Версия", font=self.font_small_tuple, bg=c['bg'], fg=c['fg_muted']).pack(anchor="w")
        tk.Label(update_container, text=f"v{MODULE_VERSION}", font=self.font_normal_tuple, bg=c['bg'], fg=c['fg_secondary']).pack(anchor="w", pady=(2, 0))

        # --- COLUMN 3: TARIFF BADGE (right-aligned, hidden unless subscription ending) ---
        header_grid.grid_columnconfigure(3, weight=0)
        tariff_badge = tk.Frame(header_grid, bg=c['bg'])
        tariff_badge.grid(row=0, column=3, sticky="ne", padx=(20, 0))
        price_row = tk.Frame(tariff_badge, bg=c['bg'])
        price_row.pack(anchor="e")
        self._settings_tariff_price = tk.Label(price_row, text="",
            font=("Segoe UI", self.font_small, "bold"), bg=c['bg'], fg=c['success'], anchor="e")
        self._settings_tariff_price.pack(side="left")
        self._settings_tariff_info = tk.Label(price_row, text="ⓘ",
            font=("Segoe UI", self.font_small - 2), bg=c['bg'], fg=c['fg_muted'], cursor="hand2")
        self._settings_tariff_info.pack(side="left", padx=(1, 0))
        self._settings_tariff_tooltip = ToolTip(self._settings_tariff_info, get_text('pricing_tooltip_body', self.lang), title=get_text('pricing_tooltip_title', self.lang))
        self._settings_tariff_date = tk.Label(tariff_badge, text="",
            font=("Segoe UI", self.font_small - 2), bg=c['bg'], fg=c['fg_muted'], anchor="e")
        self._settings_tariff_date.pack(anchor="e")
        self._settings_tariff_badge = tariff_badge

        # 2. MIDDLE ROW: ACTIVATION STATUS (30%) + NOTIFICATIONS (70%)
        top_row = tk.Frame(parent, bg=c['bg'])
        top_row.pack(fill="x", pady=(5, 10), padx=20)
        top_row.grid_columnconfigure(0, weight=3)
        top_row.grid_columnconfigure(1, weight=7)
        
        # --- ACTIVATION STATUS ---
        status_frame = tk.LabelFrame(top_row, text=f" {get_text('activation_status', self.lang)} ", 
                                     padx=15, pady=15, font=self.font_bold_tuple, bg=c['bg'], fg=c['fg'])
        status_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 5))

        status_inner = tk.Frame(status_frame, bg=c['bg'])
        status_inner.pack(fill="x", padx=5, pady=5)
        
        tk.Label(status_inner, text=f"{get_text('device_key', self.lang)}:", 
                font=self.font_small_tuple, bg=c['bg'], fg=c['fg_secondary']).grid(row=0, column=0, sticky="w", pady=2, padx=(0, 5))
        
        device_key_display = self.device_key[:16] + "..." if hasattr(self, 'device_key') and len(self.device_key) > 16 else (getattr(self, 'device_key', 'Unknown'))
        device_key_label = tk.Label(status_inner, text=device_key_display,
                font=("Courier", self.font_small), fg=c['key_fg'], bg=c['key_bg'],
                padx=4, pady=2, relief="flat", cursor="hand2")
        device_key_label.grid(row=0, column=1, sticky="w", pady=2)
        
        def copy_device_key(event=None):
            self.master.clipboard_clear()
            self.master.clipboard_append(self.device_key)
            original_bg = device_key_label.cget('bg')
            device_key_label.config(bg=c['success_bg'])
            self.master.after(300, lambda: device_key_label.config(bg=original_bg))
        device_key_label.bind("<Button-1>", copy_device_key)

        tk.Label(status_inner, text=f"{get_text('status', self.lang)}:", 
                font=self.font_small_tuple, bg=c['bg'], fg=c['fg_secondary']).grid(row=1, column=0, sticky="w", pady=2, padx=(0, 5))
        is_active = getattr(self, 'status', '').lower() == "active"
        status_color = c['success'] if is_active else c['error']
        status_bg = c['success_bg'] if is_active else c['error_bg']
        status_text = get_text('active', self.lang) if is_active else get_text('inactive', self.lang)
        tk.Label(status_inner, text=f" {status_text} ",
                font=self.font_bold_tuple, fg=status_color, bg=status_bg,
                padx=4, pady=2).grid(row=1, column=1, sticky="w", pady=2)

        if getattr(self, 'activation_start', None) and getattr(self, 'activation_end', None):
            tk.Label(status_inner, text=f"{get_text('period', self.lang)}:", 
                    font=self.font_small_tuple, bg=c['bg'], fg=c['fg_secondary']).grid(row=2, column=0, sticky="w", pady=2, padx=(0, 5))
            period_text = f" {self.activation_start} - {self.activation_end} "
            tk.Label(status_inner, text=period_text,
                    font=self.font_small_tuple, fg=c['warning'], bg=c['warning_bg'],
                    padx=4, pady=2).grid(row=2, column=1, sticky="w", pady=2)

        # --- NOTIFICATIONS (same adaptive Canvas+Scrollbar as PV Bot) ---
        notif_frame = tk.LabelFrame(top_row, text=f" {get_text('notifications', self.lang)} ", 
                                    padx=5, pady=5, font=self.font_bold_tuple, bg=c['bg'], fg=c['fg'])
        notif_frame.grid(row=0, column=1, sticky="nsew", padx=(5, 0))
        notif_frame.grid_rowconfigure(0, weight=1)
        notif_frame.grid_columnconfigure(0, weight=1)
        
        notif_n_container = tk.Frame(notif_frame, bg=c['bg'])
        notif_n_container.pack(fill="both", expand=True)
        
        notif_n_canvas = tk.Canvas(notif_n_container, highlightthickness=0, bg=c['bg'])
        notif_n_scroll = AutoScrollbar(notif_n_container, orient="vertical", command=notif_n_canvas.yview)
        notif_n_canvas.configure(yscrollcommand=notif_n_scroll.set)
        
        notif_inner = tk.Frame(notif_n_canvas, bg=c['bg'])
        ncw = notif_n_canvas.create_window((0, 0), window=notif_inner, anchor="nw")

        def _on_cfg_settings(e, cv=notif_n_canvas, i=ncw):
            cv.itemconfig(i, width=e.width)
            if hasattr(self, '_notif_after_id') and self._notif_after_id:
                cv.after_cancel(self._notif_after_id)
            self._notif_after_id = cv.after_idle(lambda: self._update_notif_width())
        notif_n_canvas.bind('<Configure>', _on_cfg_settings)
        def _mw_s(e, c=notif_n_canvas):
            c.yview_scroll(-1 * (e.delta // 120), "units")
        notif_n_canvas.bind('<MouseWheel>', _mw_s)
        notif_inner.bind('<MouseWheel>', _mw_s)

        notif_n_canvas.pack(side="left", fill="both", expand=True)
        notif_n_scroll.pack(side="right", fill="y")

        if not hasattr(self, '_notif_containers'):
            self._notif_containers = []
        self._notif_containers.append(notif_inner)

        # Render existing notifications (fetched by main tab)
        self._render_all_notifications(getattr(self, 'notifications', []))

        # Fetch tariff data for badge
        self._schedule(500, self._fetch_settings_pricing_data)

    def _fetch_settings_pricing_data(self):
        if not self._should_show_pricing():
            tb = getattr(self, '_settings_tariff_badge', None)
            if tb:
                try: tb.grid_remove()
                except: pass
            return
        tb = getattr(self, '_settings_tariff_badge', None)
        if tb:
            try: tb.grid()
            except: pass
        self._settings_pricing_data = None
        def _fetch():
            try:
                from db import get_supabase_client
                supabase = get_supabase_client()
                if supabase is None:
                    return
                login = self.login.get().strip() if hasattr(self, 'login') else ''
                if not login:
                    return
                result = supabase.table('pricing').select('*').eq('login', login).order('year', desc=True).order('month', desc=True).limit(1).execute()
                if result.data:
                    self._settings_pricing_data = result.data[0]
            except Exception as e:
                print(f"Error fetching settings pricing: {e}")
        threading.Thread(target=_fetch, daemon=True).start()
        def _poll():
            if getattr(self, '_settings_pricing_data', None):
                data = self._settings_pricing_data
                self._settings_pricing_data = None
                self._display_settings_pricing(data)
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

    def _display_settings_pricing(self, row):
        try:
            fee = max(row.get('final_fee', 0) or 0, 1)
            sub_end = self.activation_end if getattr(self, 'activation_end', None) else ''
            if hasattr(self, '_settings_tariff_price') and self._settings_tariff_price.winfo_exists():
                self._settings_tariff_price.config(text=f"💰 {int(fee):,} ₸".replace(',', ' '))
            if hasattr(self, '_settings_tariff_date') and self._settings_tariff_date.winfo_exists():
                self._settings_tariff_date.config(text=f"До {sub_end}" if sub_end else '')
        except Exception as e:
            print(f"Error displaying settings pricing: {e}")

    def _edit_device_name(self):
        """Prompt the user to rename this device and update history records."""
        old_name = self.sync_name_var.get()
        new_name = self.ask_string_dialog("Переименование устройства",
                                          "Введите новое имя устройства:",
                                          initial=old_name)
        if new_name and new_name.strip() and new_name.strip() != old_name:
            new_name = new_name.strip()
            
            # 1. Update local database records (History, Receipts, etc)
            # This makes the change "apply everywhere" as requested.
            updated_db = False
            if hasattr(self, '_db_manager'):
                updated_db = self._db_manager.update_device_name_in_history(old_name, new_name)
            
            # 2. Save to global settings
            settings.SYNC_NAME = new_name
            import settings as s_mod
            if hasattr(s_mod, 'save_sync_settings'):
                s_mod.save_sync_settings({'name': new_name})
            
            # 3. Update sync_name_var. This triggers the trace in ui.py
            # which updates labels (dev_name_label) and refreshes active tabs.
            self.sync_name_var.set(new_name)
            
            msg = f"Device renamed to '{new_name}'."
            if updated_db:
                msg += " Historical records updated."
            self.show_toast(msg, "success")
            
            from tkinter import messagebox
            messagebox.showinfo(get_text('success_title', self.lang), get_text('device_renamed', self.lang).format(name=new_name))

    def _build_settings_appearance(self, parent):
        c = self.colors
        lf = tk.LabelFrame(parent, text=f" {get_text('appearance', self.lang)} ", padx=25, pady=20, font=self.font_bold_tuple, bg=c['bg'], fg=c['fg'])
        lf.pack(fill="x", padx=20, pady=20)

        theme_section = tk.Frame(lf, bg=c['bg'])
        theme_section.pack(fill="x")

        tk.Label(theme_section, text="Выразительные:", font=self.font_normal_tuple, bg=c['bg'], fg=c['fg']).pack(anchor="w", pady=(5, 0))
        exp_frame = tk.Frame(theme_section, bg=c['bg'])
        exp_frame.pack(fill="x", padx=(10, 0))
        expressive_themes = [
            ('lavender', 'Лаванда'), ('rose', 'Роза'), ('sky', 'Небо'),
            ('mint', 'Мята'), ('aqua', 'Аква'),
        ]
        for theme_id, label in expressive_themes:
            is_selected = self.theme_var.get() == theme_id
            bg_color = c['accent'] if is_selected else c['bg_tertiary']
            btn = tk.Button(exp_frame, text=label, font=("Arial", 14), width=8, height=1, relief="flat",
                           bg=bg_color, fg=self._theme_btn_fg(bg_color), cursor="hand2",
                           command=lambda t=theme_id: self.select_theme(t))
            btn.pack(side="left", padx=3)
            if not hasattr(self, 'theme_buttons'): self.theme_buttons = {}
            self.theme_buttons[theme_id] = btn

        tk.Label(theme_section, text="Сдержанные:", font=self.font_normal_tuple, bg=c['bg'], fg=c['fg']).pack(anchor="w", pady=(5, 0))
        res_frame = tk.Frame(theme_section, bg=c['bg'])
        res_frame.pack(fill="x", padx=(10, 0))
        restrained_themes = [
            ('forest', 'Лес'), ('dusk', 'Сумерки'), ('teal', 'Бирюза'),
            ('slate', 'Сланец'), ('warm', 'Тёплая'),
        ]
        for theme_id, label in restrained_themes:
            is_selected = self.theme_var.get() == theme_id
            bg_color = c['accent'] if is_selected else c['bg_tertiary']
            btn = tk.Button(res_frame, text=label, font=("Arial", 14), width=8, height=1, relief="flat",
                           bg=bg_color, fg=self._theme_btn_fg(bg_color), cursor="hand2",
                           command=lambda t=theme_id: self.select_theme(t))
            btn.pack(side="left", padx=3)
            if not hasattr(self, 'theme_buttons'): self.theme_buttons = {}
            self.theme_buttons[theme_id] = btn

        lang_frame = tk.Frame(lf, bg=c['bg'])
        lang_frame.pack(fill="x", pady=self.padding_small)
        tk.Label(lang_frame, text=f"{get_text('language', self.lang)}:", font=self.font_normal_tuple, bg=c['bg'], fg=c['fg_secondary']).pack(side="left")
        tk.Label(lang_frame, text="Русский", font=self.font_normal_tuple, bg=c['bg'], fg=c['fg']).pack(side="left", padx=self.padding_medium)

        preset_frame = tk.Frame(lf, bg=c['bg'])
        preset_frame.pack(fill="x", pady=self.padding_small)
        tk.Label(preset_frame, text=f"{get_text('scale_presets', self.lang)}:", font=self.font_normal_tuple, bg=c['bg']).pack(side="left")
        preset_options = {'Small': get_text('scale_small', self.lang), 'Default': get_text('scale_default', self.lang), 'Large': get_text('scale_large', self.lang)}
        
        def on_preset_change(event):
            selected = preset_combo.get()
            for key, val in preset_options.items():
                if val == selected:
                    self.scale_preset_var.set(key)
                    break
        
        preset_combo = ttk.Combobox(preset_frame, values=list(preset_options.values()), font=self.font_normal_tuple, state="readonly", width=25)
        preset_combo.set(preset_options.get(self.scale_preset_var.get(), preset_options['Default']))
        preset_combo.pack(side="left", padx=self.padding_medium)
        preset_combo.bind("<<ComboboxSelected>>", on_preset_change)

        # Notification Settings (Moved back from Main)
        lf_notif = tk.LabelFrame(parent, text=f" 🔔 {get_text('toast_settings', self.lang)} ", padx=20, pady=20, font=self.font_bold_tuple, bg=c['bg'], fg=c['fg'])
        lf_notif.pack(fill="x", padx=20, pady=10)
        
        scale_frame = tk.Frame(lf_notif, bg=c['bg'])
        scale_frame.pack(fill="x", pady=2)
        tk.Label(scale_frame, text=f"{get_text('toast_size', self.lang)}:", font=self.font_normal_tuple, bg=c['bg'], width=20, anchor="w").pack(side="left")
        tk.Scale(scale_frame, from_=0.5, to=2.0, resolution=0.1, orient="horizontal", variable=self.toast_size_var, 
                 bg=c['bg'], highlightthickness=0, length=200).pack(side="left", padx=10)
                 
        alpha_frame = tk.Frame(lf_notif, bg=c['bg'])
        alpha_frame.pack(fill="x", pady=2)
        tk.Label(alpha_frame, text=f"{get_text('toast_opacity', self.lang)}:", font=self.font_normal_tuple, bg=c['bg'], width=20, anchor="w").pack(side="left")
        tk.Scale(alpha_frame, from_=0.1, to=1.0, resolution=0.05, orient="horizontal", variable=self.toast_alpha_var, 
                 bg=c['bg'], highlightthickness=0, length=200).pack(side="left", padx=10)
                 
        type_frame = tk.Frame(lf_notif, bg=c['bg'])
        type_frame.pack(fill="x", pady=10)
        types = [
            ('toast_type_success', self.toast_show_success_var),
            ('toast_type_error', self.toast_show_error_var),
            ('toast_type_warning', self.toast_show_warning_var),
            ('toast_type_info', self.toast_show_info_var),
            ('toast_type_print_success', self.toast_show_print_success_var),
            ('toast_type_print_error', self.toast_show_print_error_var),
            ('toast_type_sync', self.toast_show_sync_var),
            ('toast_type_bot', self.toast_show_bot_var),
            ('toast_type_inventory', self.toast_show_inventory_var),
            ('toast_type_sales', self.toast_show_sales_var)
        ]
        for i, (text_key, var) in enumerate(types):
            row = i // 4
            col = i % 4
            tk.Checkbutton(type_frame, text=get_text(text_key, self.lang), variable=var,
                          bg=c['bg'], activebackground=c['bg'], font=self.font_small_tuple).grid(row=row, column=col, padx=(0, 15), pady=2, sticky='w')

        pos_frame = tk.Frame(lf_notif, bg=c['bg'])
        pos_frame.pack(fill="x", pady=5)
        tk.Label(pos_frame, text=f"{get_text('toast_position', self.lang)}:", font=self.font_normal_tuple, bg=c['bg'], width=20, anchor="w").pack(side="left")
        
        for val, label in [('top_center', get_text('toast_position_top', self.lang)), 
                          ('bottom_center', get_text('toast_position_bottom', self.lang))]:
            tk.Radiobutton(pos_frame, text=label, variable=self.toast_position_var, value=val,
                           bg=c['bg'], activebackground=c['bg'], font=self.font_small_tuple).pack(side="left", padx=10)

        # Warning dialogs (Касса): low-stock confirm
        lf_warn = tk.LabelFrame(parent, text=" ⚠️ Предупреждения ", padx=20, pady=15, font=self.font_bold_tuple, bg=c['bg'], fg=c['fg'])
        lf_warn.pack(fill="x", padx=20, pady=10)
        warn_row = tk.Frame(lf_warn, bg=c['bg'])
        warn_row.pack(fill="x", pady=2)
        tk.Checkbutton(warn_row, text="Не спрашивать при продаже товара в минус (Касса)",
                       variable=self.skip_low_stock_warning_var,
                       bg=c['bg'], activebackground=c['bg'], font=self.font_normal_tuple,
                       selectcolor=c['bg_tertiary']).pack(side="left")
        tk.Label(lf_warn, text="Продажа будет проходить без предупреждения, пока галочка включена.",
                 font=self.font_small_tuple, bg=c['bg'], fg=c['fg_muted']).pack(anchor="w", padx=25)

    def _build_settings_printer(self, parent):
        c = self.colors
        lf = tk.LabelFrame(parent, text=" Настройки чека / Принтер ", padx=self.padding_large, pady=self.padding_large, font=self.font_bold_tuple, bg=c['bg'], fg=c['fg'])
        lf.pack(fill="x", padx=self.padding_medium, pady=self.padding_medium)

        # ── Top horizontal block: extended settings (left) + requisites (right) ──
        top_row = tk.Frame(lf, bg=c['bg'])
        top_row.pack(fill="x", pady=(0, 6))

        # LEFT: extended settings
        adv_panel = tk.LabelFrame(top_row, text=" ⚙️ Расширенные настройки ",
                                  font=self.font_small_bold_tuple, bg=c['bg'], fg=c['fg'],
                                  padx=10, pady=6)
        adv_panel.pack(side="left", fill="y", padx=(0, 8), anchor="n")

        self._text_scale_var = tk.DoubleVar(value=self._receipt_config.get("text_scale", 1.0))
        self._show_partner_var = tk.BooleanVar(value=self._receipt_config.get("show_partner", True))
        self._partial_id_var = tk.BooleanVar(value=self._receipt_config.get("partial_id", False))
        self._show_partner_phone_var = tk.BooleanVar(value=self._receipt_config.get("show_partner_phone", False))
        self._show_pv_var = tk.BooleanVar(value=self._receipt_config.get("show_pv", True))
        self._item_layout_var = tk.StringVar(value=self._receipt_config.get("item_layout", "compact"))

        ts_row = tk.Frame(adv_panel, bg=c['bg'])
        ts_row.pack(fill="x", pady=2)
        tk.Label(ts_row, text=get_text("text_scale", self.lang), font=self.font_small_tuple,
                 bg=c["bg"], fg=c['fg_secondary']).pack(side="left")
        tk.Scale(ts_row, from_=0.8, to=1.5, resolution=0.1, orient="horizontal", variable=self._text_scale_var,
                 bg=c["bg"], highlightthickness=0, length=130, command=self._preview_and_track).pack(side="left", padx=5)

        adv_checks = tk.Frame(adv_panel, bg=c['bg'])
        adv_checks.pack(fill="x", pady=2)
        tk.Checkbutton(adv_checks, text=get_text("show_partner", self.lang), variable=self._show_partner_var,
                       font=self.font_small_tuple, bg=c["bg"], command=self._preview_and_track).pack(side="left")
        tk.Checkbutton(adv_checks, text=get_text("partial_id", self.lang), variable=self._partial_id_var,
                       font=self.font_small_tuple, bg=c["bg"], command=self._preview_and_track).pack(side="left", padx=12)

        pphone_row = tk.Frame(adv_panel, bg=c['bg'])
        pphone_row.pack(fill="x", pady=2)
        tk.Checkbutton(pphone_row, text="Показывать тел. партнёра в чеке", variable=self._show_partner_phone_var,
                       font=self.font_small_tuple, bg=c["bg"], command=self._preview_and_track).pack(side="left")

        pv_row = tk.Frame(adv_panel, bg=c['bg'])
        pv_row.pack(fill="x", pady=2)
        tk.Checkbutton(pv_row, text="Баллы по товарам + Итого в чеке", variable=self._show_pv_var,
                       font=self.font_small_tuple, bg=c["bg"], command=self._preview_and_track).pack(side="left")

        layout_row = tk.Frame(adv_panel, bg=c['bg'])
        layout_row.pack(fill="x", pady=2)
        tk.Label(layout_row, text="Формат товаров:", font=self.font_small_tuple, bg=c["bg"], fg=c['fg_secondary']).pack(side="left")
        self._item_layout_combo = ttk.Combobox(layout_row, values=["compact", "wide"], state="readonly",
                                                font=self.font_small_tuple, width=10,
                                                textvariable=self._item_layout_var)
        self._item_layout_combo.pack(side="left", padx=5)
        self._item_layout_combo.bind('<<ComboboxSelected>>', self._preview_and_track)
        tk.Label(layout_row, text="58мм | 80мм", font=self.font_small_tuple,
                 bg=c["bg"], fg=c["fg_muted"]).pack(side="left", padx=8)

        tk.Frame(adv_panel, height=1, bg=c['border']).pack(fill="x", pady=4)

        auto_row = tk.Frame(adv_panel, bg=c['bg'])
        auto_row.pack(fill="x", pady=2)
        self._auto_print_var = tk.BooleanVar(value=self._receipt_config.get('auto_print', False))
        tk.Checkbutton(auto_row, text="Авто-печать", variable=self._auto_print_var, font=self.font_small_tuple,
                       bg=c['bg'], command=self._track_changes).pack(side="left")
        self._auto_cut_var = tk.BooleanVar(value=self._receipt_config.get("auto_cut", True))
        tk.Checkbutton(auto_row, text="Авто-отрезка", variable=self._auto_cut_var, font=self.font_small_tuple,
                       bg=c['bg'], command=self._track_changes).pack(side="left", padx=16)

        width_row = tk.Frame(adv_panel, bg=c['bg'])
        width_row.pack(fill="x", pady=2)
        tk.Label(width_row, text="Ширина:", font=self.font_small_tuple, bg=c['bg'], fg=c['fg_secondary']).pack(side="left")
        self._paper_width_var = tk.IntVar(value=self._receipt_config.get('paper_width', 58))
        tk.Radiobutton(width_row, text="58 мм", variable=self._paper_width_var, value=58, font=self.font_small_tuple,
                       bg=c['bg'], command=self._preview_and_track).pack(side="left", padx=10)
        tk.Radiobutton(width_row, text="80 мм", variable=self._paper_width_var, value=80, font=self.font_small_tuple,
                       bg=c['bg'], command=self._preview_and_track).pack(side="left", padx=10)

        printer_row = tk.Frame(adv_panel, bg=c['bg'])
        printer_row.pack(fill="x", pady=2)
        tk.Label(printer_row, text="Принтер:", font=self.font_small_tuple, bg=c['bg'], fg=c['fg_secondary']).pack(side="left")
        self._printer_var = tk.StringVar(value=self._receipt_config.get('printer_name', ''))
        self._printer_combo = ttk.Combobox(printer_row, textvariable=self._printer_var,
                                           font=self.font_small_tuple, width=24, state='readonly')
        self._printer_combo.pack(side="left", padx=5, fill="x", expand=True)

        refresh_row = tk.Frame(adv_panel, bg=c['bg'])
        refresh_row.pack(fill="x", pady=(2, 2))
        self._printer_refresh_btn = self._btn(refresh_row, text="🔄 Обновить список принтеров",
                                              command=lambda: self._refresh_printers(silent=False),
                                              style='neutral', compact=True, cursor='hand2')
        self._printer_refresh_btn.pack(side="left")

        # RIGHT: requisites fields (single column, stacked so they fit on POS screens)
        right_panel = tk.LabelFrame(top_row, text=" Реквизиты ",
                                    font=self.font_small_bold_tuple, bg=c['bg'], fg=c['fg'],
                                    padx=10, pady=6)
        right_panel.pack(side="left", fill="both", expand=True, anchor="n")
        right_panel.grid_columnconfigure(0, weight=1)

        self._receipt_vars = {}

        def _make_field(parent, key, label, row=0):
            f = tk.Frame(parent, bg=c['bg'])
            f.grid(row=row, column=0, sticky="ew", padx=(0, 12), pady=2)
            tk.Label(f, text=label, font=self.font_small_tuple, bg=c['bg'], fg=c['fg_secondary'],
                     width=13, anchor='w').pack(side="left")
            var = tk.StringVar(value=self._receipt_config.get(key, ''))
            var.trace_add('write', lambda *a: self._track_changes())
            tk.Entry(f, textvariable=var, font=self.font_small_tuple,
                     width=18).pack(side="left", padx=5, fill="x", expand=True)
            self._receipt_vars[key] = var

        _make_field(right_panel, 'taxpayer_name', 'Налогоплательщик:', row=0)
        _make_field(right_panel, 'iin_bin', 'ИИН/БИН:', row=1)
        _make_field(right_panel, 'address', 'Адрес:', row=2)
        _make_field(right_panel, 'phone', 'Телефон:', row=3)

        footer_row = tk.Frame(right_panel, bg=c['bg'])
        footer_row.grid(row=4, column=0, sticky="ew", pady=2)
        tk.Label(footer_row, text="Внизу чека:", font=self.font_small_tuple, bg=c['bg'], fg=c['fg_secondary'],
                 width=13, anchor='w').pack(side="left")
        self._receipt_vars['footer_text'] = tk.StringVar(value=self._receipt_config.get('footer_text', ''))
        self._receipt_vars['footer_text'].trace_add('write', lambda *a: self._track_changes())
        tk.Entry(footer_row, textvariable=self._receipt_vars['footer_text'],
                 font=self.font_small_tuple).pack(side="left", padx=5, fill="x", expand=True)

        def _on_paper_width_change(*args):
            if self._paper_width_var.get() == 58:
                self._item_layout_var.set('compact')
                self._item_layout_combo.configure(state='disabled')
            else:
                # 80 mm: only the wide item layout is supported
                self._item_layout_var.set('wide')
                self._item_layout_combo.configure(state='disabled')
            self._preview_and_track()
        self._paper_width_var.trace_add('write', _on_paper_width_change)
        self.master.after(50, _on_paper_width_change)

        editor_label = tk.Label(lf, text="📐 Редактор блоков (перетаскивайте ↕):", font=self.font_bold_tuple, bg=c['bg'], fg=c['fg'])
        editor_label.pack(anchor="w", pady=(10, 5))

        editor_container = tk.Frame(lf, bg=c['bg'])
        editor_container.pack(fill="both", expand=True, pady=5)

        left_frame = tk.Frame(editor_container, bg=c['bg'])
        left_frame.pack(side="left", fill="y", padx=(0, 5))
        self._block_listbox = tk.Listbox(left_frame, font=self.font_normal_tuple, height=20, width=28, selectmode='single', bg=c.get('list_bg', c['bg_secondary']), fg=c['fg'], relief='flat', bd=0)
        self._block_listbox.pack(fill="both", expand=True, pady=2)
        
        self._block_names = {
            'taxpayer': get_text('taxpayer', self.lang),
            'address': get_text('address', self.lang),
            'separator1': get_text('separator', self.lang) + ' 1',
            'datetime': get_text('datetime', self.lang),
            'receipt_number': get_text('receipt_number', self.lang),
            'cashier_info': get_text('cashier', self.lang),
            'kkm_info': get_text('kkm_info', self.lang),
            'separator2': get_text('separator', self.lang) + ' 2',
            'items_table': get_text('items_table', self.lang),
            'separator3': get_text('separator', self.lang) + ' 3',
            'partner_info': get_text('partner_info', self.lang),
            'totals': get_text('totals', self.lang),
            'payment_info': get_text('payment_info', self.lang),
            'separator4': get_text('separator', self.lang) + ' 4',
            'space_separator1': get_text('space', self.lang) + ' 1',
            'footer': get_text('footer', self.lang),
        }
        import settings as _s
        saved_order = list(self._receipt_config.get('block_order', _s.DEFAULT_RECEIPT_CONFIG['block_order']))
        # Build block_order from saved, excluding 'logo', 'qr_code' and legacy 'space_sep'
        self._block_order = [b for b in saved_order if b != 'logo' and b != 'qr_code' and b != 'space_sep']
        # Ensure any new blocks added to DEFAULT are included (handles old saved configs)
        for b in _s.DEFAULT_RECEIPT_CONFIG['block_order']:
            if b != 'logo' and b not in self._block_order:
                self._block_order.append(b)
                
        self._block_align = dict(self._receipt_config.get('block_align', _s.DEFAULT_RECEIPT_CONFIG.get('block_align', {})))
        # Reset stale reference from a previous UI build BEFORE populating the list,
        # otherwise _refresh_available_blocks() touches a destroyed combobox during rebuild
        self._add_block_combo = None
        self._populate_block_list()

        self._drag_data = {'index': None}
        self._block_listbox.bind('<Button-1>', self._block_drag_start)
        self._block_listbox.bind('<B1-Motion>', self._block_drag_motion)
        self._block_listbox.bind('<ButtonRelease-1>', self._block_drag_end)
        



        right_frame = tk.Frame(editor_container, bg=c['bg'])
        right_frame.pack(side="left", fill="both", expand=True, padx=(5, 0))
        self._preview_right_frame = right_frame
        # Paper shadow wrapper — fixed pixel size, sized exactly to the receipt content
        pw = self._paper_width_var.get()
        char_w = 48 if pw >= 80 else 32
        paper_shadow = tk.Frame(right_frame, bg=c['bg_tertiary'], bd=0)
        paper_shadow.pack(padx=6, pady=4, anchor="n")
        paper_shadow.pack_propagate(False)
        self._paper_shadow = paper_shadow
        self._receipt_preview = tk.Text(paper_shadow, font=("Courier", 10), bg='white', fg='black',
                                         relief='solid', bd=1, state='disabled', wrap='none',
                                         width=char_w + 2, height=24)
        self._receipt_preview.tag_config('bold', font=("Courier", 10, "bold"))
        self._receipt_preview.pack(padx=10, pady=8)

        # Re-fit the preview whenever the panel is resized (throttled)
        self._preview_fit_after = None
        right_frame.bind('<Configure>', self._on_preview_area_configure)

        move_frame = tk.Frame(lf, bg=c['bg'])
        move_frame.pack(fill="x", pady=2)

        self._btn(move_frame, text="⬆", command=lambda: self._move_block_wrapper(-1), style='neutral', compact=True, width=2, cursor='hand2').pack(side="left", padx=2)
        self._btn(move_frame, text="⬇", command=lambda: self._move_block_wrapper(1), style='neutral', compact=True, width=2, cursor='hand2').pack(side="left", padx=2)

        tk.Label(move_frame, text=" | ", bg=c['bg'], fg=c['fg_secondary']).pack(side="left")

        self._btn(move_frame, text="⬅", command=lambda: self._set_block_align('left'), style='neutral', compact=True, width=2, cursor='hand2').pack(side="left", padx=2)
        self._btn(move_frame, text="↔", command=lambda: self._set_block_align('center'), style='neutral', compact=True, width=2, cursor='hand2').pack(side="left", padx=2)
        self._btn(move_frame, text="➡", command=lambda: self._set_block_align('right'), style='neutral', compact=True, width=2, cursor='hand2').pack(side="left", padx=2)

        tk.Label(move_frame, text=" | ", bg=c['bg'], fg=c['fg_secondary']).pack(side="left")

        self._btn(move_frame, text="➖", command=self._remove_block, style='neutral', compact=True, width=2, cursor='hand2').pack(side="left", padx=2)

        tk.Label(move_frame, text=" | ", bg=c['bg'], fg=c['fg_secondary']).pack(side="left")

        available_blocks = [
            'Налогоплательщик', 'Адрес', 'Дата/время', 'Номер чека', 'Кассир',
            'Инфо ККМ', 'Таблица товаров', 'Партнёр/Клиент', 'Итого', 'Оплата',
            'Подвал', 'Разделитель', 'Пробел (Пустое место)'
        ]
        self._add_block_combo = ttk.Combobox(move_frame, values=available_blocks, width=12, state='readonly', font=self.font_small_tuple)
        self._add_block_combo.set('Разделитель')
        self._add_block_combo.pack(side="left", padx=2)
        self._btn(move_frame, text="➕", command=self._add_block, style='neutral', compact=True, width=2, cursor='hand2').pack(side="left", padx=2)
        self._refresh_available_blocks()

        tk.Label(move_frame, text=" | ", bg=c['bg'], fg=c['fg_secondary']).pack(side="left")
        self._btn(move_frame, text="🖨 Тест", command=self._test_print_receipt, style='accent', compact=True, cursor='hand2').pack(side="left", padx=2)
        self._update_receipt_preview()
        self.master.after(300, self._update_receipt_preview)

    def _on_preview_area_configure(self, event=None):
        """Throttled re-fit of the receipt preview when the panel is resized."""
        if getattr(self, '_preview_fit_after', None) is not None:
            try:
                self.master.after_cancel(self._preview_fit_after)
            except Exception:
                pass
        self._preview_fit_after = self.master.after(300, self._update_receipt_preview)

    def _move_block_wrapper(self, dir):
        self._move_block(dir)
        self._track_changes()

    def _build_settings_automation(self, parent):
        c = self.colors
        
        # Check subscription level
        if self.subscription_level not in [3, 4]:
            lf_warn = tk.LabelFrame(parent, text=" Доступ ограничен ", padx=20, pady=20, font=self.font_bold_tuple, bg=c['bg'], fg=c['warning'])
            lf_warn.pack(fill="x", padx=20, pady=20)
            tk.Label(lf_warn, text="Автоматизация и PV Bot доступны в подписках\n'3 - Кассовый пос + Бот' и '4 - Полный пакет'.", 
                     font=self.font_normal_tuple, bg=c['bg'], fg=c['fg_secondary'], justify="left").pack(pady=10)
            return

        # 1. Automation
        lf1 = tk.LabelFrame(parent, text=f" {get_text('automation', self.lang)} ", padx=self.padding_large, pady=self.padding_large, font=self.font_bold_tuple, bg=c['bg'], fg=c['fg'])
        lf1.pack(fill="x", padx=self.padding_medium, pady=self.padding_medium)
        tk.Checkbutton(lf1, text=get_text('enable_scheduler', self.lang), variable=self.scheduler_enabled_var, font=self.font_bold_tuple, command=self.toggle_scheduler_fields, bg=c['bg'], fg=c['fg'], activebackground=c['bg'], selectcolor=c['key_bg']).pack(anchor="w", pady=self.padding_small)

        self.scheduler_settings_frame = tk.Frame(lf1, bg=c['bg'])
        self.scheduler_settings_frame.pack(fill="x", padx=self.padding_large, pady=self.padding_small)
        
        time_frame = tk.Frame(self.scheduler_settings_frame, bg=c['bg'])
        time_frame.pack(fill="x", pady=self.padding_small)
        tk.Label(time_frame, text=get_text('scheduled_time', self.lang), font=self.font_normal_tuple, bg=c['bg'], fg=c['fg_secondary']).pack(side="left")
        self.hour_var = tk.StringVar(value=self.scheduled_time_var.get().split(':')[0])
        ttk.Spinbox(time_frame, from_=0, to=23, width=3, textvariable=self.hour_var, format="%02.0f").pack(side="left", padx=self.padding_small)
        tk.Label(time_frame, text=":", font=self.font_bold_tuple, bg=c['bg']).pack(side="left")
        self.minute_var = tk.StringVar(value=self.scheduled_time_var.get().split(':')[1] if ':' in self.scheduled_time_var.get() else '00')
        ttk.Spinbox(time_frame, from_=0, to=59, width=3, textvariable=self.minute_var, format="%02.0f").pack(side="left", padx=self.padding_small)
        tk.Checkbutton(time_frame, text=get_text('auto_download_receipts', self.lang), variable=self.auto_download_receipts_var, font=self.font_normal_tuple, bg=c['bg'], fg=c['fg_secondary'], activebackground=c['bg'], selectcolor=c['key_bg']).pack(side="left", padx=self.padding_large)
        
        # Bind spinboxes to track changes — write only VALID time into
        # scheduled_time_var (letters/garbage are silently ignored)
        def _update_scheduled_time(*args):
            try:
                h = int(self.hour_var.get())
                m = int(self.minute_var.get())
                if 0 <= h <= 23 and 0 <= m <= 59:
                    self.scheduled_time_var.set(f"{h:02d}:{m:02d}")
            except (ValueError, TypeError):
                pass

        self.hour_var.trace_add("write", _update_scheduled_time)
        self.minute_var.trace_add("write", _update_scheduled_time)

        dir_frame = tk.Frame(self.scheduler_settings_frame, bg=c['bg'])
        dir_frame.pack(fill="x", pady=self.padding_small)
        tk.Label(dir_frame, text=get_text('watch_directory', self.lang), font=self.font_normal_tuple, bg=c['bg'], fg=c['fg_secondary']).pack(side="left")
        tk.Entry(dir_frame, textvariable=self.watch_directory_var, font=self.font_normal_tuple, relief="solid", bd=1).pack(side="left", padx=self.padding_small, fill="x", expand=True)
        self._btn(dir_frame, text=get_text('browse', self.lang), command=self.browse_watch_directory, style='neutral', compact=True, cursor="hand2").pack(side="left")

        tk.Checkbutton(self.scheduler_settings_frame, text=get_text('shutdown_after', self.lang), variable=self.shutdown_after_var, font=self.font_normal_tuple, bg=c['bg'], fg=c['fg_secondary'], activebackground=c['bg'], selectcolor=c['key_bg']).pack(anchor="w", pady=self.padding_small)
        
        test_frame = tk.Frame(self.scheduler_settings_frame, bg=c['bg'])
        test_frame.pack(fill="x", pady=self.padding_small)
        self._btn(test_frame, text=get_text('test_now', self.lang), command=self.test_scheduler, style='warning', compact=True, cursor="hand2").pack(side="left")
        
        tk.Frame(lf1, height=2, bg=c['border']).pack(fill="x", pady=self.padding_medium)
        live_header = tk.Frame(lf1, bg=c['bg'])
        live_header.pack(anchor="w", pady=(5, 0))
        tk.Label(live_header, text="Live PV Bot", font=self.font_bold_tuple, bg=c['bg'], fg=c['fg_muted']).pack(side="left")
        tk.Label(live_header, text="(в разработке)", font=self.font_small_tuple, bg=c['bg'], fg=c['warning']).pack(side="left", padx=(5, 0))
        tk.Checkbutton(lf1, text="Автоматическая отправка после продажи", variable=self.live_bot_v2_var, font=self.font_normal_tuple, bg=c['bg'], fg=c['fg_muted'], activebackground=c['bg'], selectcolor=c['key_bg'], state="disabled").pack(anchor="w", pady=self.padding_small)
        
        delay_frame = tk.Frame(lf1, bg=c['bg'])
        delay_frame.pack(fill="x", pady=self.padding_small)
        tk.Label(delay_frame, text="Задержка (сек):", font=self.font_normal_tuple, bg=c['bg'], fg=c['fg_muted']).pack(side="left")
        sb = ttk.Spinbox(delay_frame, from_=5, to=3600, width=6, textvariable=self.live_bot_delay_var)
        sb.configure(state="disabled")
        sb.pack(side="left", padx=self.padding_small)
        tk.Label(lf1, text="⚠️ Продажи через Live Bot нельзя будет вернуть!", font=self.font_small_tuple, fg=c['fg_muted'], bg=c['bg'], justify="left").pack(anchor="w")
        self.toggle_scheduler_fields()

        # Partner auto-block policy (PV Bot)
        lf_autoblock = tk.LabelFrame(parent, text=f" {get_text('partner_autoblock', self.lang)} ", padx=self.padding_large, pady=self.padding_large, font=self.font_bold_tuple, bg=c['bg'], fg=c['fg'])
        lf_autoblock.pack(fill="x", padx=self.padding_medium, pady=self.padding_medium)
        for val, text_key in [('all', 'partner_autoblock_all'),
                              ('not_found', 'partner_autoblock_not_found'),
                              ('insufficient', 'partner_autoblock_insufficient'),
                              ('off', 'partner_autoblock_off')]:
            tk.Radiobutton(lf_autoblock, text=get_text(text_key, self.lang), variable=self.partner_autoblock_var,
                           value=val, bg=c['bg'], fg=c['fg'], activebackground=c['bg'],
                           selectcolor=c['key_bg'], font=self.font_normal_tuple,
                           anchor="w").pack(anchor="w", pady=1)

        # 2. Autorun
        lf2 = tk.LabelFrame(parent, text=f" {get_text('autorun_short', self.lang)} ", padx=self.padding_large, pady=self.padding_large, font=self.font_bold_tuple, bg=c['bg'], fg=c['fg'])
        lf2.pack(fill="x", padx=self.padding_medium, pady=self.padding_medium)
        self._autorun_frame = lf2
        tk.Checkbutton(lf2, text=get_text('autorun', self.lang), variable=self.autorun_var, font=self.font_normal_tuple, bg=c['bg'], fg=c['fg'], activebackground=c['bg'], selectcolor=c['key_bg']).pack(anchor="w", pady=self.padding_small)
        tk.Label(lf2, text=get_text('autorun_description', self.lang), font=self.font_small_tuple, fg=c['fg_muted'], bg=c['bg'], justify="left").pack(anchor="w")

        # 3. Performance
        lf3 = tk.LabelFrame(parent, text=f" {get_text('performance', self.lang)} ", padx=self.padding_large, pady=self.padding_large, font=self.font_bold_tuple, bg=c['bg'], fg=c['fg'])
        lf3.pack(fill="x", padx=self.padding_medium, pady=self.padding_medium)
        self._perf_frame = lf3
        tk.Checkbutton(lf3, text=get_text('slow_network_mode', self.lang), variable=self.slow_network_var, font=self.font_bold_tuple, bg=c['bg'], fg=c['fg'], activebackground=c['bg'], selectcolor=c['key_bg']).pack(anchor="w", pady=self.padding_small)

        # Max empty pages
        pp_frame = tk.Frame(lf3, bg=c['bg'])
        pp_frame.pack(anchor="w", pady=self.padding_small)
        tk.Label(pp_frame, text=get_text('max_empty_pages', self.lang), font=self.font_normal_tuple, bg=c['bg'], fg=c['fg']).pack(side="left", padx=(0, 8))
        tk.Spinbox(pp_frame, from_=1, to=20, textvariable=self.max_empty_pages_var, width=5, font=self.font_normal_tuple, justify="center").pack(side="left")

    def _build_settings_users(self, parent):
        c = self.colors
        lf = tk.LabelFrame(parent, text=" Управление пользователями ", padx=25, pady=20, font=self.font_bold_tuple, bg=c['bg'], fg=c['fg'])
        lf.pack(fill="both", expand=True, padx=20, pady=20)
        
        users_list_frame = tk.Frame(lf, bg=c['bg'])
        users_list_frame.pack(fill="x", pady=5)
        
        self._users_listbox = tk.Listbox(users_list_frame, font=self.font_normal_tuple, height=8, bg=c.get('list_bg', c['bg_secondary']), fg=c['fg'], relief='flat', bd=0, exportselection=False)
        self._users_listbox.pack(side="left", fill="both", expand=True, padx=(0, 5))
        self.bind_mousewheel(self._users_listbox)
        
        users_btn_frame = tk.Frame(users_list_frame, bg=c['bg'])
        users_btn_frame.pack(side="right", fill="y")
        
        self._btn(users_btn_frame, text="➕ Создать", command=self._show_create_user_dialog, style='success', compact=True, width=14, cursor='hand2').pack(pady=2)
        self._btn(users_btn_frame, text="🔑 Сброс PIN", command=self._show_reset_pin_dialog, style='warning', compact=True, width=14, cursor='hand2').pack(pady=2)
        self._btn(users_btn_frame, text="🗑 Удалить", command=self._delete_selected_user, style='danger', compact=True, width=14, cursor='hand2').pack(pady=2)
        
        # Inlined Permissions Frame
        self._perm_container = tk.LabelFrame(lf, text=" Права доступа ", padx=10, pady=10, font=self.font_small_bold_tuple, bg=c['bg'], fg=c['fg_secondary'])
        self._perm_container.pack(fill="both", expand=True, pady=(10, 0))
        
        self._perm_content = tk.Frame(self._perm_container, bg=c['bg'])
        self._perm_content.pack(fill="both", expand=True)
        
        self.lbl_select_user_perm = tk.Label(self._perm_content, text="Выберите пользователя для редактирования прав", font=self.font_small_tuple, bg=c['bg'], fg=c['fg_muted'])
        self.lbl_select_user_perm.pack(pady=20)
        
        self._users_listbox.bind('<<ListboxSelect>>', lambda e: self._on_user_selected_for_perms())
        self._refresh_users_list()

    def _build_settings_integrations(self, parent):
        """Build the Integrations settings page."""
        c = self.colors
        
        # --- EMAIL SECTION ---
        mail_frame = tk.LabelFrame(parent, text="📧 Электронная почта (SMTP)", font=self.font_bold_tuple, bg=c['bg'], fg=c['accent'], padx=20, pady=20)
        mail_frame.pack(fill="x", padx=30, pady=(30, 15))
        
        tk.Checkbutton(mail_frame, text="Включить уведомления на Email", variable=self.email_enabled_var,
                      bg=c['bg'], activebackground=c['bg'], font=self.font_normal_tuple).pack(anchor="w", pady=(0, 10))
        
        # SMTP Presets
        presets_frame = tk.Frame(mail_frame, bg=c['bg'])
        presets_frame.pack(fill="x", pady=(0, 10))
        tk.Label(presets_frame, text="Быстрая настройка:", font=self.font_small_tuple, bg=c['bg'], fg=c['fg_muted']).pack(side="left", padx=(0, 10))
        
        def set_preset(provider):
            if provider == "gmail":
                self.smtp_server_var.set("smtp.gmail.com")
                self.smtp_port_var.set("465")
            elif provider == "mailru":
                self.smtp_server_var.set("smtp.mail.ru")
                self.smtp_port_var.set("465")
            self._track_changes()

        self._btn(presets_frame, text="Gmail", command=lambda: set_preset("gmail"), style='neutral', compact=True).pack(side="left", padx=5)
        self._btn(presets_frame, text="Mail.ru", command=lambda: set_preset("mailru"), style='neutral', compact=True).pack(side="left", padx=5)

        # Grid for SMTP settings
        grid = tk.Frame(mail_frame, bg=c['bg'])
        grid.pack(fill="x")
        grid.columnconfigure(1, weight=1)
        
        labels_vars = [
            ("SMTP Сервер:", self.smtp_server_var),
            ("Порт:", self.smtp_port_var),
            ("Логин/Email:", self.smtp_user_var),
            ("Пароль:", self.smtp_pwd_var),
            ("Получатель отчетов:", self.email_recipient_var),
        ]
        
        for i, (label, var) in enumerate(labels_vars):
            tk.Label(grid, text=label, font=self.font_normal_tuple, bg=c['bg'], fg=c['fg_muted']).grid(row=i, column=0, sticky="w", pady=5)
            entry = tk.Entry(grid, textvariable=var, font=self.font_normal_tuple, relief="flat", bg=c['bg_tertiary'], fg=c['fg'])
            if "Пароль" in label: entry.config(show="*")
            entry.grid(row=i, column=1, sticky="ew", pady=5, padx=(10, 0))

        tk.Label(mail_frame, text="💡 Совет: Для Google (Gmail) используйте 'Пароли приложений'\nвместо основного пароля аккаунта.", 
                 font=self.font_small_tuple, bg=c['bg'], fg=c['fg_muted'], justify="left").pack(anchor="w", pady=(10, 0))

        def test_email():
            current_config = {
                'email_enabled': True, # Explicitly True for testing
                'smtp_server': self.smtp_server_var.get(),
                'smtp_port': self.smtp_port_var.get(),
                'smtp_user': self.smtp_user_var.get(),
                'smtp_password': self.smtp_pwd_var.get(),
                'email_recipient': self.email_recipient_var.get(),
            }
            test_email_btn.config(state="disabled", text="⏳ Отправка...")
            def _worker():
                try:
                    success, msg = EmailService.send_email(
                        "Тест PVM.core", "Это тестовое сообщение из PVM.core.", config=current_config)
                except Exception as e:
                    success, msg = False, str(e)
                def _finish():
                    try:
                        test_email_btn.config(state="normal", text="🧪 Тест Email")
                    except Exception:
                        pass
                    if success:
                        messagebox.showinfo("Email Test", "Тестовое письмо успешно отправлено!")
                    else:
                        messagebox.showerror("Email Error", f"Ошибка отправки:\n{msg}")
                try:
                    self._tk_after(0, _finish)
                except Exception:
                    pass
            threading.Thread(target=_worker, daemon=True).start()

        test_email_btn = self._btn(mail_frame, text="🧪 Тест Email", command=test_email, style='neutral', compact=True)
        test_email_btn.pack(anchor="e", pady=(10, 0))

        # --- TELEGRAM SECTION ---
        tg_frame = tk.LabelFrame(parent, text="🤖 Telegram Bot", font=self.font_bold_tuple, bg=c['bg'], fg=c['accent'], padx=20, pady=20)
        tg_frame.pack(fill="x", padx=30, pady=15)
        
        tk.Checkbutton(tg_frame, text="Включить Telegram-бота", variable=self.tg_enabled_var,
                      bg=c['bg'], activebackground=c['bg'], font=self.font_normal_tuple).pack(anchor="w", pady=(0, 10))
        
        grid_tg = tk.Frame(tg_frame, bg=c['bg'])
        grid_tg.pack(fill="x")
        grid_tg.columnconfigure(1, weight=1)
        
        tk.Label(grid_tg, text="Bot Token:", font=self.font_normal_tuple, bg=c['bg'], fg=c['fg_muted']).grid(row=0, column=0, sticky="w", pady=5)
        tk.Entry(grid_tg, textvariable=self.tg_token_var, font=self.font_normal_tuple, relief="flat", bg=c['bg_tertiary'], fg=c['fg']).grid(row=0, column=1, sticky="ew", pady=5, padx=(10, 0))
        
        tk.Label(grid_tg, text="Chat ID:", font=self.font_normal_tuple, bg=c['bg'], fg=c['fg_muted']).grid(row=1, column=0, sticky="w", pady=5)
        tk.Entry(grid_tg, textvariable=self.tg_chat_id_var, font=self.font_normal_tuple, relief="flat", bg=c['bg_tertiary'], fg=c['fg']).grid(row=1, column=1, sticky="ew", pady=5, padx=(10, 0))
        
        tk.Label(tg_frame, text="ℹ️ Чтобы узнать свой Chat ID, отправьте /start боту.", font=self.font_small_tuple, bg=c['bg'], fg=c['fg_muted']).pack(anchor="w", pady=5)
        tk.Label(tg_frame, text="ℹ️ Команды: /today — отчёт за сегодня, /yesterday — за вчера, /stats — сводка за 7 дней.", font=self.font_small_tuple, bg=c['bg'], fg=c['fg_muted']).pack(anchor="w", pady=(0, 5))

        self._tg_status_lbl = tk.Label(tg_frame, text="", font=self.font_small_tuple, bg=c['bg'], fg=c['fg_muted'], anchor="w")
        self._tg_status_lbl.pack(fill="x", pady=(0, 5))
        self._refresh_tg_status()

        def test_tg():
            current_config = {
                'telegram_enabled': True, # Explicitly True for testing
                'tg_bot_token': self.tg_token_var.get(),
                'tg_chat_id': self.tg_chat_id_var.get(),
            }
            test_tg_btn.config(state="disabled", text="⏳ Отправка...")
            def _worker():
                try:
                    success, msg = TelegramService.send_message(
                        "<b>Тест PVM.core</b>\nЭто тестовое уведомление.", config=current_config)
                except Exception as e:
                    success, msg = False, str(e)
                def _finish():
                    try:
                        test_tg_btn.config(state="normal", text="🧪 Тест Telegram")
                    except Exception:
                        pass
                    if success:
                        messagebox.showinfo("Telegram Test", "Тестовое сообщение отправлено!")
                    else:
                        messagebox.showerror("Telegram Error", f"Ошибка отправки:\n{msg}")
                try:
                    self._tk_after(0, _finish)
                except Exception:
                    pass
            threading.Thread(target=_worker, daemon=True).start()

        test_tg_btn = self._btn(tg_frame, text="🧪 Тест Telegram", command=test_tg, style='neutral', compact=True)
        test_tg_btn.pack(anchor="e", pady=(10, 0))

        # --- ADDITIONAL OPTIONS ---
        opt_frame = tk.LabelFrame(parent, text="⚙️ Дополнительно", font=self.font_bold_tuple, bg=c['bg'], fg=c['accent'], padx=20, pady=20)
        opt_frame.pack(fill="x", padx=30, pady=15)
        
        tk.Checkbutton(opt_frame, text="Отправлять отчет владельцу перед выключением", variable=self.send_report_on_exit_var,
                      bg=c['bg'], activebackground=c['bg'], font=self.font_normal_tuple).pack(anchor="w")
        
        tk.Checkbutton(opt_frame, text="Требовать код подтверждения (OTP) при неудачном входе", variable=self.require_otp_var,
                      bg=c['bg'], activebackground=c['bg'], font=self.font_normal_tuple).pack(anchor="w", pady=(5, 0))

    def _refresh_tg_status(self):
        """Update the Telegram bot status label on the Integrations page."""
        lbl = getattr(self, '_tg_status_lbl', None)
        if not lbl or not lbl.winfo_exists():
            return
        c = self.colors
        try:
            enabled = self.tg_enabled_var.get()
        except Exception:
            enabled = False
        try:
            token = self.tg_token_var.get().strip()
        except Exception:
            token = ''
        running = False
        bot = getattr(self, 'integration_bot', None)
        if bot is not None:
            running = bool(getattr(bot, 'running', False))
        if not enabled:
            text, color = "● Бот выключен", c['fg_muted']
        elif not token:
            text, color = "● Укажите токен бота", c['warning']
        elif running:
            text, color = "● Бот работает, слушает команды", c['success']
        else:
            text, color = "● Бот не запущен — проверьте настройки", c['warning']
        try:
            lbl.config(text=text, fg=color)
        except Exception:
            pass

    # =========================================================================
    # DATABASE MANAGEMENT PAGE
    # =========================================================================

    def _build_settings_database(self, parent):
        """Build the Database Management settings page with statistics, export, and import."""
        c = self.colors
        from db_sqlite import DatabaseManager

        # ─── SECTION 1: DATABASE STATISTICS ─────────────────────────────
        lf_stats = tk.LabelFrame(parent, text=" Статистика базы данных ",
                                  padx=20, pady=15, font=self.font_bold_tuple,
                                  bg=c['bg'], fg=c['accent'])
        lf_stats.pack(fill="x", padx=20, pady=(20, 10))

        # Stats grid container
        self._db_stats_frame = tk.Frame(lf_stats, bg=c['bg'])
        self._db_stats_frame.pack(fill="x", pady=5)

        # Refresh button
        self._btn(lf_stats, text="Обновить статистику", command=self._refresh_db_statistics, style='neutral', compact=True, cursor='hand2').pack(anchor="e", pady=(5, 0))

        # ─── SECTION 2: EXPORT ──────────────────────────────────────────
        lf_export = tk.LabelFrame(parent, text=" Экспорт данных ",
                                   padx=20, pady=15, font=self.font_bold_tuple,
                                   bg=c['bg'], fg=c['accent'])
        lf_export.pack(fill="x", padx=20, pady=10)

        tk.Label(lf_export, text="Выберите данные для экспорта:",
                 font=self.font_small_tuple, bg=c['bg'], fg=c['fg_secondary']).pack(anchor="w", pady=(0, 8))

        # Checkboxes for sections
        self._export_vars = {}
        export_grid = tk.Frame(lf_export, bg=c['bg'])
        export_grid.pack(fill="x")

        sections_list = list(DatabaseManager.EXPORTABLE_SECTIONS.items())
        # Add settings as a virtual section
        sections_list.append(('app_settings', {'label': 'Настройки приложения'}))

        for i, (key, info) in enumerate(sections_list):
            var = tk.BooleanVar(value=True)
            self._export_vars[key] = var
            row, col = divmod(i, 3)
            tk.Checkbutton(export_grid, text=info['label'], variable=var,
                           font=self.font_small_tuple, bg=c['bg'],
                           activebackground=c['bg']).grid(row=row, column=col,
                                                          sticky='w', padx=(0, 20), pady=2)

        # Select All / Clear All buttons
        btn_row = tk.Frame(lf_export, bg=c['bg'])
        btn_row.pack(fill="x", pady=(8, 5))

        def _select_all_export():
            for v in self._export_vars.values():
                v.set(True)

        def _clear_all_export():
            for v in self._export_vars.values():
                v.set(False)

        self._btn(btn_row, text="Выбрать все", command=_select_all_export, style='success', compact=True, cursor='hand2').pack(side="left", padx=(0, 5))
        self._btn(btn_row, text="Очистить все", command=_clear_all_export, style='danger', compact=True, cursor='hand2').pack(side="left")

        # Export button
        self._btn(lf_export, text="  ЭКСПОРТИРОВАТЬ  ", command=self._do_export_database, style='accent', cursor='hand2').pack(pady=(10, 5))

        tk.Label(lf_export,
                 text="Формат: .pvmbackup (сжатый SQLite) — оптимально для больших баз",
                 font=self.font_small_tuple, bg=c['bg'], fg=c['fg_muted']).pack(anchor="w")

        # ─── SECTION 3: IMPORT ──────────────────────────────────────────
        lf_import = tk.LabelFrame(parent, text=" Импорт данных ",
                                   padx=20, pady=15, font=self.font_bold_tuple,
                                   bg=c['bg'], fg=c['accent'])
        lf_import.pack(fill="x", padx=20, pady=10)

        # Warning
        warn_frame = tk.Frame(lf_import, bg=c.get('error_bg', '#FFEBEE'), padx=12, pady=8)
        warn_frame.pack(fill="x", pady=(0, 10))
        tk.Label(warn_frame,
                 text="ВНИМАНИЕ: Импорт УДАЛИТ все текущие данные в выбранных категориях!",
                 font=self.font_small_bold_tuple, bg=c.get('error_bg', '#FFEBEE'),
                 fg=c.get('error', '#C62828'), wraplength=500, justify="left").pack(anchor="w")
        tk.Label(warn_frame,
                 text="Перед импортом будет предложено создать резервную копию.",
                 font=self.font_small_tuple, bg=c.get('error_bg', '#FFEBEE'),
                 fg=c.get('fg_secondary', '#555')).pack(anchor="w", pady=(4, 0))

        self._btn(lf_import, text="  ИМПОРТИРОВАТЬ ИЗ ФАЙЛА  ", command=self._do_import_database, style='warning', cursor='hand2').pack(pady=10)

        # ─── SECTION 4: QUICK BACKUP ────────────────────────────────────
        lf_backup = tk.LabelFrame(parent, text=" Резервная копия ",
                                   padx=20, pady=15, font=self.font_bold_tuple,
                                   bg=c['bg'], fg=c['fg'])
        lf_backup.pack(fill="x", padx=20, pady=(10, 20))

        tk.Label(lf_backup,
                 text="Быстрое создание полной копии файла базы данных (.db)",
                 font=self.font_small_tuple, bg=c['bg'], fg=c['fg_secondary']).pack(anchor="w")

        self._btn(lf_backup, text="Создать резервную копию", command=self._do_quick_backup, style='success', cursor='hand2').pack(anchor="w", pady=(10, 5))

        # ─── SECTION 5: CLEAR DATA ────────────────────────────────────
        lf_clear = tk.LabelFrame(parent, text=" Очистка базы данных ",
                                   padx=20, pady=15, font=self.font_bold_tuple,
                                   bg=c['bg'], fg=c['error'])
        lf_clear.pack(fill="x", padx=20, pady=(10, 20))

        tk.Label(lf_clear,
                 text="Безвозвратное удаление выбранных данных из базы.",
                 font=self.font_small_tuple, bg=c['bg'], fg=c['fg_secondary']).pack(anchor="w")

        self._clear_vars = {}
        clear_grid = tk.Frame(lf_clear, bg=c['bg'])
        clear_grid.pack(fill="x", pady=10)

        for i, (key, info) in enumerate(DatabaseManager.EXPORTABLE_SECTIONS.items()):
            var = tk.BooleanVar(value=False)
            self._clear_vars[key] = var
            row, col = divmod(i, 3)
            tk.Checkbutton(clear_grid, text=info['label'], variable=var,
                           font=self.font_small_tuple, bg=c['bg'], fg=c['error'],
                           activebackground=c['bg']).grid(row=row, column=col,
                                                          sticky='w', padx=(0, 20), pady=2)

        self._btn(lf_clear, text="УДАЛИТЬ ДАННЫЕ", command=self._do_clear_database, style='danger', cursor='hand2').pack(pady=(10, 0))

        # Initial statistics load
        self.master.after(300, self._refresh_db_statistics)

    def _refresh_db_statistics(self):
        """Refresh the database statistics display."""
        if not hasattr(self, '_db_stats_frame') or not self._db_stats_frame.winfo_exists():
            return

        c = self.colors

        # Clear existing
        for w in self._db_stats_frame.winfo_children():
            w.destroy()

        try:
            stats = self._db_manager.get_database_statistics()
        except Exception as e:
            tk.Label(self._db_stats_frame, text=f"Ошибка: {e}",
                     font=self.font_small_tuple, bg=c['bg'], fg=c['error']).pack()
            return

        # Header row
        header = tk.Frame(self._db_stats_frame, bg=c['bg_tertiary'])
        header.pack(fill="x", pady=(0, 2))
        tk.Label(header, text="Параметр", font=self.font_small_bold_tuple,
                 bg=c['bg_tertiary'], fg=c['fg'], width=28, anchor="w").pack(side="left", padx=10)
        tk.Label(header, text="Кол-во", font=self.font_small_bold_tuple,
                 bg=c['bg_tertiary'], fg=c['fg'], width=12, anchor="e").pack(side="right", padx=10)

        # Data rows
        file_size = stats.pop('_file_size', 0)
        for i, (table_name, info) in enumerate(stats.items()):
            bg = c['bg_secondary'] if i % 2 == 0 else c['bg']
            row = tk.Frame(self._db_stats_frame, bg=bg)
            row.pack(fill="x")
            tk.Label(row, text=info['label'], font=self.font_small_tuple,
                     bg=bg, fg=c['fg'], width=28, anchor="w").pack(side="left", padx=10, pady=1)
            count_text = f"{info['count']:,}".replace(',', ' ')
            fg_color = c['fg'] if info['count'] > 0 else c['fg_muted']
            tk.Label(row, text=count_text, font=self.font_small_tuple,
                     bg=bg, fg=fg_color, width=12, anchor="e").pack(side="right", padx=10, pady=1)

        # DB Size row
        size_row = tk.Frame(self._db_stats_frame, bg=c['bg_tertiary'])
        size_row.pack(fill="x", pady=(2, 0))
        tk.Label(size_row, text="Размер базы данных", font=self.font_small_bold_tuple,
                 bg=c['bg_tertiary'], fg=c['accent'], width=28, anchor="w").pack(side="left", padx=10)
        from db_sqlite import DatabaseManager
        tk.Label(size_row, text=DatabaseManager._format_size(file_size),
                 font=self.font_small_bold_tuple, bg=c['bg_tertiary'], fg=c['accent'],
                 width=12, anchor="e").pack(side="right", padx=10)

    def _do_export_database(self):
        """Handle the export button: sync → select file → export."""
        # 1. Gather selected sections
        selected = [k for k, v in self._export_vars.items() if v.get() and k != 'app_settings']
        include_settings = self._export_vars.get('app_settings', tk.BooleanVar(value=False)).get()

        if not selected and not include_settings:
            messagebox.showwarning("Экспорт", "Выберите хотя бы одну категорию для экспорта!")
            return

        # 2. Ask for sync before export
        do_sync = messagebox.askyesno(
            "Синхронизация перед экспортом",
            "Рекомендуется выполнить полную синхронизацию перед экспортом,\n"
            "чтобы гарантировать актуальность всех данных.\n\n"
            "Выполнить синхронизацию сейчас?"
        )

        # 3. Choose save location
        from datetime import datetime as dt
        default_name = f"pvm_backup_{dt.now().strftime('%Y-%m-%d_%H%M')}.pvmbackup"
        file_path = filedialog.asksaveasfilename(
            title="Сохранить экспорт как",
            defaultextension=".pvmbackup",
            initialfile=default_name,
            filetypes=[("PVM Backup", "*.pvmbackup"), ("All Files", "*.*")]
        )
        if not file_path:
            return

        # 4. Run in background thread
        overlay = self.show_loading_overlay("Экспорт данных...\nПожалуйста подождите")
        self._export_progress_label = tk.Label(overlay, text="",
                                                font=self.font_small_tuple,
                                                bg=self.colors['bg_secondary'],
                                                fg=self.colors['fg_muted'])
        self._export_progress_label.pack(pady=(0, 5))

        def _progress(step, total, msg):
            def _update():
                if hasattr(self, '_export_progress_label') and self._export_progress_label.winfo_exists():
                    self._export_progress_label.config(text=f"[{step}/{total}] {msg}")
            self.master.after(0, _update)

        def _run():
            try:
                # Sync first if requested
                if do_sync:
                    _progress(0, 1, "Синхронизация...")
                    eng = getattr(self, 'sync_engine', None)
                    if eng:
                        eng.request_sync()

                # Collect settings if requested
                settings_data = None
                if include_settings:
                    settings_data = settings.get_all_settings_for_export()

                # Export
                success, msg = self._db_manager.export_database(
                    file_path, selected,
                    settings_data=settings_data,
                    progress_callback=_progress
                )

                def _done():
                    try:
                        if overlay.winfo_exists():
                            overlay.destroy()
                    except: pass
                    if success:
                        self.show_toast(f"✅ {msg}", "success")
                        messagebox.showinfo("Экспорт завершён",
                                            f"{msg}\n\nФайл: {file_path}")
                    else:
                        self.show_toast(f"❌ {msg}", "error")
                        messagebox.showerror("Ошибка экспорта", msg)

                self.master.after(0, _done)
            except Exception as e:
                def _err():
                    try:
                        if overlay.winfo_exists():
                            overlay.destroy()
                    except: pass
                    messagebox.showerror("Ошибка", str(e))
                self.master.after(0, _err)

        threading.Thread(target=_run, daemon=True).start()

    def _do_import_database(self):
        """Handle the import button: warn → backup offer → select file → preview → sync → import."""
        # 1. First warning
        if not messagebox.askyesno(
            "⚠️ Импорт данных",
            "ВНИМАНИЕ!\n\n"
            "Импорт ЗАМЕНИТ все текущие данные в выбранных категориях!\n"
            "Это действие НЕЛЬЗЯ отменить.\n\n"
            "Продолжить?"
        ):
            return

        # 2. Offer backup
        make_backup = messagebox.askyesno(
            "Резервная копия",
            "Рекомендуется создать резервную копию текущей базы перед импортом.\n\n"
            "Создать резервную копию сейчас?"
        )

        if make_backup:
            success = self._do_quick_backup(silent=False)
            if not success:
                if not messagebox.askyesno("Ошибка",
                    "Не удалось создать резервную копию.\nПродолжить импорт без резервной копии?"):
                    return

        # 3. Select file
        file_path = filedialog.askopenfilename(
            title="Выберите файл для импорта",
            filetypes=[("PVM Backup", "*.pvmbackup"), ("All Files", "*.*")]
        )
        if not file_path:
            return

        # 4. Read and preview backup contents
        overlay = self.show_loading_overlay("Чтение файла...")
        try:
            ok, info = self._db_manager.read_backup_info(file_path)
        except Exception as e:
            overlay.destroy()
            messagebox.showerror("Ошибка", f"Не удалось прочитать файл: {e}")
            return
        finally:
            try:
                if overlay.winfo_exists():
                    overlay.destroy()
            except: pass

        if not ok:
            messagebox.showerror("Ошибка", str(info))
            return

        # 5. Show preview dialog with section selection
        self._show_import_preview(file_path, info)

    def _show_import_preview(self, file_path, info):
        """Show a dialog with backup contents and let user select which sections to import."""
        c = self.colors
        from db_sqlite import DatabaseManager

        dialog = self.create_modal_dialog("Содержимое файла", width=550, height=550, scrollable=False)
        main = dialog.container

        # Header: file info
        meta = info.get('meta', {})
        file_size = DatabaseManager._format_size(info.get('file_size', 0))
        created = meta.get('created_at', '—')
        device = meta.get('device_name', '—') or meta.get('device_key', '—')

        info_frame = tk.Frame(main, bg=c['bg'])
        info_frame.pack(fill="x", padx=20, pady=(15, 10))
        tk.Label(info_frame, text=f"Размер: {file_size}   |   Создан: {created[:19]}   |   Устройство: {device}",
                 font=self.font_small_tuple, bg=c['bg'], fg=c['fg_secondary'],
                 wraplength=500, justify="left").pack(anchor="w")

        # Sections with counts
        sections_frame = tk.LabelFrame(main, text=" Доступные данные в файле ",
                                        padx=15, pady=10, font=self.font_small_bold_tuple,
                                        bg=c['bg'], fg=c['fg'])
        sections_frame.pack(fill="both", expand=True, padx=20, pady=5)

        import_vars = {}
        available_sections = info.get('sections', [])
        has_settings = bool(meta.get('app_settings'))

        for section_key in available_sections:
            sec_info = DatabaseManager.EXPORTABLE_SECTIONS.get(section_key, {})
            label = sec_info.get('label', section_key)

            # Count records for this section's tables
            tables = sec_info.get('tables', [])
            total_count = sum(info['stats'].get(t, 0) for t in tables)

            var = tk.BooleanVar(value=True)
            import_vars[section_key] = var

            row = tk.Frame(sections_frame, bg=c['bg'])
            row.pack(fill="x", pady=1)
            tk.Checkbutton(row, text=label, variable=var,
                           font=self.font_small_tuple, bg=c['bg'],
                           activebackground=c['bg']).pack(side="left")
            count_str = f"{total_count:,}".replace(',', ' ') + " записей"
            fg_c = c['fg'] if total_count > 0 else c['fg_muted']
            tk.Label(row, text=count_str, font=self.font_small_tuple,
                     bg=c['bg'], fg=fg_c).pack(side="right", padx=10)

        # Settings option
        if has_settings:
            var = tk.BooleanVar(value=True)
            import_vars['app_settings'] = var
            row = tk.Frame(sections_frame, bg=c['bg'])
            row.pack(fill="x", pady=1)
            tk.Checkbutton(row, text="⚙️ Настройки приложения", variable=var,
                           font=self.font_small_tuple, bg=c['bg'],
                           activebackground=c['bg']).pack(side="left")

        # Bottom buttons
        btn_frame = tk.Frame(main, bg=c['bg'])
        btn_frame.pack(fill="x", padx=20, pady=15)

        def _do_import():
            selected_sections = [k for k, v in import_vars.items()
                                 if v.get() and k != 'app_settings']
            import_app_settings = import_vars.get('app_settings', tk.BooleanVar(value=False)).get()

            if not selected_sections and not import_app_settings:
                messagebox.showwarning("Импорт", "Выберите хотя бы одну категорию!")
                return

            # Final confirmation
            if not messagebox.askyesno(
                "Подтверждение импорта",
                "ВЫ УВЕРЕНЫ?\n\n"
                "Все текущие данные в выбранных категориях будут УДАЛЕНЫ\n"
                "и заменены данными из файла.\n\n"
                "Это действие НЕЛЬЗЯ отменить!"
            ):
                return

            dialog.destroy()

            # Run import in background
            overlay = self.show_loading_overlay("Импорт данных...\nЭто может занять время")
            progress_label = tk.Label(overlay, text="",
                                       font=self.font_small_tuple,
                                       bg=self.colors['bg_secondary'],
                                       fg=self.colors['fg_muted'])
            progress_label.pack(pady=(0, 5))

            def _progress(step, total, msg):
                def _u():
                    if progress_label.winfo_exists():
                        progress_label.config(text=f"[{step}/{total}] {msg}")
                self.master.after(0, _u)

            def _run():
                try:
                    # Sync before import
                    eng = getattr(self, 'sync_engine', None)
                    if eng:
                        _progress(0, 1, "Синхронизация перед импортом...")
                        try:
                            eng.request_sync()
                        except Exception:
                            pass

                    success, msg, imported_settings = self._db_manager.import_database(
                        file_path, selected_sections, progress_callback=_progress
                    )

                    # Import app settings if requested
                    if success and import_app_settings and imported_settings:
                        settings.import_all_settings(imported_settings)

                    def _done():
                        try:
                            if overlay.winfo_exists():
                                overlay.destroy()
                        except: pass

                        if success:
                            self.show_toast(f"✅ {msg}", "success")
                            self._refresh_db_statistics()
                            messagebox.showinfo(
                                "Импорт завершён",
                                f"{msg}\n\nРекомендуется перезагрузить приложение\n"
                                "для применения всех изменений."
                            )
                        else:
                            self.show_toast(f"❌ {msg}", "error")
                            messagebox.showerror("Ошибка импорта", msg)

                    self.master.after(0, _done)
                except Exception as e:
                    def _err():
                        try:
                            if overlay.winfo_exists():
                                overlay.destroy()
                        except: pass
                        messagebox.showerror("Ошибка", str(e))
                    self.master.after(0, _err)

            threading.Thread(target=_run, daemon=True).start()

        self._btn(btn_frame, text="  📥 ИМПОРТИРОВАТЬ  ", command=_do_import, style='warning', cursor='hand2').pack(side="left", padx=(0, 10))
        self._btn(btn_frame, text="Отмена", command=dialog.destroy, style='neutral', cursor='hand2').pack(side="left")

        self.bind_dialog_keys(dialog, confirm_callback=_do_import, cancel_callback=dialog.destroy)



    def _do_clear_database(self):
        """Handle selective database clearance."""
        if self.current_role not in ('admin', 'superadmin'):
            messagebox.showerror("Доступ запрещен", "Только администратор может удалять базу данных.")
            return
            
        selected_sections = [k for k, v in self._clear_vars.items() if v.get()]
        if not selected_sections:
            messagebox.showwarning("Очистка", "Выберите категории для очистки!")
            return
            
        # Double confirmation for deletion
        if not messagebox.askyesno("Очистка базы данных", 
                                   "Вы уверены, что хотите БЕЗВОЗВРАТНО УДАЛИТЬ данные в выбранных категориях?\n\nОтменить это действие будет невозможно!", 
                                   icon='warning'):
            return
            
        if not messagebox.askyesno("ПОСЛЕДНЕЕ ПРЕДУПРЕЖДЕНИЕ", 
                                   "Вы осознаете, что удаляемые данные нельзя восстановить без резервной копии?\nУдалить сейчас?", 
                                   icon='warning'):
            return
            
        # Optional: Auto-create backup before delete
        if messagebox.askyesno("Резервная копия", "Создать полную резервную копию перед удалением? (Рекомендуется)"):
            self._do_quick_backup()
            
        success, msg = self._db_manager.clear_database(selected_sections)
        
        if success:
            messagebox.showinfo("Готово", msg)
            self._refresh_db_statistics()
            # Uncheck all after success
            for var in self._clear_vars.values():
                var.set(False)
        else:
            messagebox.showerror("Ошибка", msg)

    def _do_quick_backup(self, silent=False):
        """Create a quick backup of the raw database file."""
        from datetime import datetime as dt
        import os

        backup_dir = os.path.join(settings.BASE_DIR, 'backups')
        os.makedirs(backup_dir, exist_ok=True)
        backup_name = f"pvmcore_backup_{dt.now().strftime('%Y%m%d_%H%M%S')}.db"
        backup_path = os.path.join(backup_dir, backup_name)

        success, msg = self._db_manager.create_backup(backup_path)
        if success:
            size = os.path.getsize(backup_path)
            from db_sqlite import DatabaseManager
            size_str = DatabaseManager._format_size(size)
            if not silent:
                self.show_toast(f"✅ Резервная копия создана ({size_str})", "success")
                messagebox.showinfo("Резервная копия",
                                    f"Файл: {backup_path}\nРазмер: {size_str}")
            return True
        else:
            if not silent:
                self.show_toast(f"❌ {msg}", "error")
                messagebox.showerror("Ошибка", msg)
            return False

    def _build_settings_system(self, parent):
        c = self.colors
        
        # Base info is always visible
        top_info = tk.Frame(parent, bg=c['bg'], padx=20, pady=10)
        top_info.pack(fill="x")
        tk.Label(top_info, text=f"ID Устройства: {getattr(self, 'device_key', 'Unknown')}", font=self.font_small_bold_tuple, bg=c['bg'], fg=c['fg']).pack(side="left")
        tk.Label(top_info, text=f" | v{MODULE_VERSION}", font=self.font_small_tuple, bg=c['bg'], fg=c['fg_muted']).pack(side="left")
        self._btn(top_info, text="🔄 Перезапустить", command=self.restart_app, style='neutral', compact=True).pack(side="right", padx=5)
        self._btn(top_info, text="⚙️ Исправить остатки", command=self.repair_inventory_discrepancies, style='neutral', compact=True).pack(side="right", padx=5)
        self._btn(top_info, text="❌ Сбросить ID", command=self._reset_device_id, style='neutral', compact=True).pack(side="right", padx=5)

        # Check subscription level for Sync
        if self.subscription_level not in [2, 4]:
            lf_warn = tk.LabelFrame(parent, text=" Синхронизация ", padx=20, pady=20, font=self.font_bold_tuple, bg=c['bg'], fg=c['warning'])
            lf_warn.pack(fill="x", padx=20, pady=10)
            tk.Label(lf_warn, text="Синхронизация доступна в подписках\n'2 - Кассовый пос + Синх' и '4 - Полный пакет'.", 
                     font=self.font_normal_tuple, bg=c['bg'], fg=c['fg_secondary'], justify="left").pack(pady=10)
            return

        lf = tk.LabelFrame(parent, text=f" {get_text('sync_tab', self.lang)} ", padx=15, pady=15, font=self.font_bold_tuple, bg=c['bg'], fg=c['fg'])
        lf.pack(fill="x", padx=self.padding_medium, pady=10)

        # --- 1. STATUS ---
        self.status_row = tk.Frame(lf, bg=c['bg'])
        self.status_row.pack(fill="x", pady=(0, 10))

        right_status = tk.LabelFrame(self.status_row, text=" 📡 Статус синхронизации ", font=self.font_small_bold_tuple, bg=c['bg'], fg=c['fg_secondary'], padx=10, pady=10)
        right_status.pack(fill="x")

        self.lbl_last_sync = tk.Label(right_status, text="...", font=self.font_small_tuple, bg=c['bg'], fg=c['fg'])
        self.lbl_last_sync.pack(side="left", padx=5)

        self._btn(right_status, text="🔄 Синхронизировать сейчас", command=self.force_sync_now, style='accent', compact=True).pack(side="right", padx=5)

        # --- 2. SYNC FOLDER & DIAGNOSTICS ---
        tk.Frame(lf, height=1, bg=c['border']).pack(fill="x", pady=self.padding_medium)
        tk.Label(lf, text=get_text('sync_folder', self.lang), font=self.font_small_bold_tuple, bg=c['bg'], fg=c['fg_secondary']).pack(anchor="w", pady=(0, 5))

        folder_row = tk.Frame(lf, bg=c['bg'])
        folder_row.pack(fill="x", padx=5, pady=2)

        sync_cfg = settings.get_sync_settings()
        self.sync_folder_var = tk.StringVar(value=sync_cfg.get('sync_folder_path', ''))

        tk.Label(folder_row, text=get_text('sync_folder', self.lang) + ":",
                 font=self.font_small_tuple, bg=c['bg'], fg=c['fg_secondary']
                 ).pack(side="left", padx=(5, 5))

        folder_entry = tk.Entry(folder_row, textvariable=self.sync_folder_var,
                                font=self.font_small_tuple, width=40)
        folder_entry.pack(side="left", fill="x", expand=True, padx=(0, 5))

        def browse_sync_folder():
            path = filedialog.askdirectory(
                title=get_text('select_sync_folder', self.lang),
                initialdir=self.sync_folder_var.get() or os.path.expanduser('~')
            )
            if path:
                self.sync_folder_var.set(path)
                settings.update_sync_settings(sync_folder_path=path)

        self._btn(folder_row, text="...", command=browse_sync_folder,
                  style='neutral', compact=True, width=3).pack(side="right", padx=2)

        diag_row = tk.Frame(lf, bg=c['bg'])
        diag_row.pack(fill="x", padx=5, pady=5)

        def run_diagnostics():
            try:
                from sync_engine import run_transport_diagnostics
            except ImportError:
                from tkinter import messagebox as _mb
                _mb.showinfo("Mega Sync", "Функция находится в разработке")
                return
            folder = self.sync_folder_var.get().strip()
            if not folder:
                messagebox.showwarning(get_text('warning_title', self.lang),
                                       "Сначала укажите папку синхронизации")
                return
            checks = run_transport_diagnostics(folder)
            lines = []
            labels = {"write_test": "Запись работает",
                      "read_test": "Чтение работает",
                      "delete_test": "Удаление работает",
                      "cloud_sync": "Папка облачной синхронизации"}
            for name, ok, detail in checks:
                label = labels.get(name, name)
                icon = "✓" if ok else "✗"
                lines.append(f"{icon} {label}: {detail}")
            all_ok = all(ok for _, ok, _ in checks)
            lines.append("")
            lines.append("✓ Синхронизация готова" if all_ok else "✗ Исправьте ошибки выше")
            messagebox.showinfo(
                "Результат проверки" if all_ok else "Ошибка синхронизации",
                "\n".join(lines)
            )

        self._btn(diag_row, text="🔍 Проверить синхронизацию",
                  command=run_diagnostics, style='neutral').pack(side="left", padx=2)

        def open_wizard():
            try:
                from sync_setup_wizard import run_sync_wizard
            except ImportError:
                from tkinter import messagebox as _mb
                _mb.showinfo("Mega Sync", "Функция находится в разработке")
                return
            configured = run_sync_wizard(self.master)
            if configured:
                self.master.after(500, self._init_sync_engine)

        self._btn(diag_row, text="⚙ Настроить",
                  command=open_wizard, style='accent').pack(side="right", padx=2)

        # --- 3. SERVICE BUTTONS ---
        service_row = tk.Frame(lf, bg=c['bg'])
        service_row.pack(fill="x", padx=5, pady=5)
        self._btn(service_row, text="📦 Полная пересинхронизация", command=self._trigger_full_resync, style='accent', compact=True).pack(side="left", padx=2)

        self.lbl_sync_error = tk.Label(lf, text="", font=self.font_small_tuple, bg=c['bg'], fg=c['error'])
        self.lbl_sync_error.pack(fill="x")

        # ── Sync Health panel ────────────────────────────────────────────────
        sync_health_frame = tk.LabelFrame(
            lf, text="Состояние синхронизации",
            bg=c['bg_secondary'], fg=c['fg'],
            font=self.font_small_bold_tuple, padx=8, pady=6,
        )
        sync_health_frame.pack(fill="x", pady=(8, 0))
        self._sync_health_label = tk.Label(
            sync_health_frame, text="…",
            justify="left", anchor="w",
            font=self.font_small_tuple, bg=c['bg_secondary'], fg=c['fg'],
        )
        self._sync_health_label.pack(fill="x")

        # Initial UI state
        self.update_sync_status_label()
        try:
            self._update_sync_health_panel()
        except Exception:
            pass

    def _update_sync_health_panel(self):
        """Render the compact Sync Health summary (MEGA folder engine).

        Shows: device_type, folder state, last sync time and buffered outbound
        count. Reuses the app's own db_manager — no DatabaseManager is created
        per poll.
        """
        try:
            meta = settings.get_sync_settings()
            device_type = (meta.get('device_type') or 'cashier').lower()
            dev_type_label = 'Склад' if device_type == 'warehouse' else 'Касса'

            eng = getattr(self, 'sync_engine', None)
            pending = eng.pending_count() if eng else 0
            if pending < 0:
                pending = 0
            if eng and eng.last_error:
                folder_state = f"❌ {str(eng.last_error)[:50]}"
            elif eng and eng.last_sync:
                folder_state = f"✅ {eng.last_sync.strftime('%d.%m %H:%M:%S')}"
            elif eng:
                folder_state = "⏳ ожидание..."
            else:
                folder_state = "не настроена"

            text = (
                f"Тип устройства: {dev_type_label}\n"
                f"MEGA-синхронизация: {folder_state}\n"
                f"Буфер отправки: {pending} изменений"
            )
            label = getattr(self, '_sync_health_label', None)
            if label is not None and label.winfo_exists():
                label.config(text=text)
        except Exception as _e:
            print(f"⚠️ Sync Health panel: {_e}")

    def restart_app(self):
        """Restart the application."""
        import sys
        import os
        try:
            python = sys.executable
            os.execl(python, python, *sys.argv)
        except Exception as e:
            print(f"Failed to restart: {e}")

    def start_data_polling(self):
        """Poll for data updates from background sync client."""
        if getattr(self, '_shutting_down', False):
            return
        if getattr(self, '_data_polling_running', False):
            return
        self._data_polling_running = True
        try:
            eng = getattr(self, 'sync_engine', None)
            if eng:
                stamp = (getattr(eng, 'last_sync', None), eng.last_applied)
                if stamp != getattr(self, '_last_engine_stamp', None):
                    self._last_engine_stamp = stamp
                    if eng.last_applied > 0:
                        # Remote data arrived — refresh catalog views
                        self.filter_goods_list()
                        if hasattr(self, 'partners_search'):
                            self.refresh_partners_list(self.partners_search.get())
                        else:
                            self.refresh_partners_list()
        except Exception as e:
            # print(f"Polling error: {e}")
            pass
            
        self._data_polling_running = False
        # Re-schedule
        if not getattr(self, '_shutting_down', False) and self.master:
            self._schedule(5000, self.start_data_polling)

    def force_sync_now(self):
        """Force immediate synchronization (MEGA folder engine)."""
        import time
        now = time.time()
        if hasattr(self, '_last_manual_sync') and now - self._last_manual_sync < 15:
            self.show_toast("Подождите немного перед следующей синхронизацией", "warning")
            return
        self._last_manual_sync = now

        eng = getattr(self, 'sync_engine', None)
        if eng:
            eng.request_sync()
            self.show_toast("🔄 Синхронизация запущена", "sync_info")
            self._schedule(100, self.update_sync_status_label)
        else:
            self.show_toast("Синхронизация не настроена: укажите папку MEGA", "warning")

    def update_sync_status_label(self):
        """Update the sync status label in the UI (runs every 2 seconds)."""
        if getattr(self, '_shutting_down', False):
            return
        if getattr(self, '_sync_status_poll_running', False):
            return
        self._sync_status_poll_running = True

        if not hasattr(self, 'lbl_last_sync') or not self.lbl_last_sync.winfo_exists():
            self._sync_status_poll_running = False
            if not getattr(self, '_shutting_down', False):
                self._schedule(2000, self.update_sync_status_label)
            return

        # Refresh Sync Health panel opportunistically (Phase 3.2). Wrapped so
        # any DB read failure can never break the 2s UI tick.
        try:
            if hasattr(self, '_update_sync_health_panel'):
                self._update_sync_health_panel()
        except Exception:
            pass

        eng = getattr(self, 'sync_engine', None)
        if eng:
            st = eng.status()
            if eng.last_error:
                self.lbl_last_sync.config(text=f"❌ {str(eng.last_error)[:60]}",
                                          fg=self.colors['error'])
                self.lbl_sync_error.config(text=str(eng.last_error)[:80])
            elif eng.last_sync:
                ts = eng.last_sync.strftime("%H:%M:%S")
                pending = st.get('pending', 0)
                self.lbl_last_sync.config(
                    text=f"MEGA: ok {ts} (📤 {pending})",
                    fg=self.colors['success'])
                self.lbl_sync_error.config(text="")
            else:
                self.lbl_last_sync.config(text="MEGA: ожидание...",
                                          fg=self.colors['fg_secondary'])
                self.lbl_sync_error.config(text="")
        else:
            self.lbl_last_sync.config(text="Папка синхронизации не настроена",
                                      fg=self.colors['fg_secondary'])

        self._sync_status_poll_running = False
        # Schedule next update
        if not getattr(self, '_shutting_down', False):
            self._schedule(2000, self.update_sync_status_label)

    def _trigger_full_resync(self):
        """Полная пересинхронизация — повторно применить все файлы из папки."""
        from tkinter import messagebox as mb
        eng = getattr(self, 'sync_engine', None)
        if not eng:
            mb.showerror("Ошибка", "Папка синхронизации не настроена")
            return
        if not mb.askyesno(
            "Полная пересинхронизация",
            "Все файлы синхронизации из папки будут применены заново "
            "(по последнему времени изменения).\nПродолжить?"
        ):
            return
        eng.request_full_resync()
        self.show_toast("🔄 Полная пересинхронизация запущена", "info")
        self._schedule(100, self.update_sync_status_label)

    def repair_inventory_discrepancies(self):
        """Wipe local goods data and re-download it from the MEGA sync folder."""
        title = get_text('repair_inventory', self.lang)
        msg = ("Это действие удалит весь локальный список товаров на этом устройстве "
               "и заново скачает его из папки синхронизации. "
               "\n\nЭто поможет исправить несостыковки в количестве товаров. "
               "\n\nВы уверены?") if self.lang == 'ru' else \
              ("This will wipe the local goods list on this device and re-download "
               "everything from the sync folder. \n\nThis helps fix stock discrepancies. "
               "\n\nAre you sure?")
        
        if not messagebox.askyesno(title, msg):
            return
            
        try:
            self.show_toast("🧹 Очистка базы товаров...", "info")
            # 1. Clear local goods cache
            import db_sqlite
            # The app instance has '_db_manager' as the DatabaseManager
            goods_mgr = db_sqlite.GoodsManagerSQL(self._db_manager)
            goods_mgr.clear_goods_cache()
            
            # 2. Reset UI state
            self.last_ui_goods_sync = "2000-01-01 00:00:00"
            
            # 3. Trigger full re-sync from the sync folder
            eng = getattr(self, 'sync_engine', None)
            if eng:
                eng.request_full_resync()
                self.show_toast("🔄 Полная пересинхронизация запущена", "info")
                self._schedule(100, self.update_sync_status_label)
                if hasattr(self, 'filter_goods_list'):
                    self.master.after(500, self.filter_goods_list)
            else:
                self.show_toast("Синхронизация не настроена: укажите папку MEGA", "warning")
            
        except Exception as e:
            messagebox.showerror(get_text('error_title', self.lang), get_text('failed_repair', self.lang).format(error=e))


    def _refresh_printers(self, silent=False):
        """Scan for available printers in a background thread (never freezes UI)."""
        try:
            btn = getattr(self, '_printer_refresh_btn', None)
            if btn and btn.winfo_exists():
                btn.config(state='disabled', text="⏳ Поиск принтеров...")
        except Exception:
            pass
        threading.Thread(target=self._refresh_printers_worker, args=(silent,), daemon=True).start()

    def _refresh_printers_worker(self, silent):
        try:
            import receipt_printer
            printers = receipt_printer.find_usb_printers()
            names = [p['name'] for p in printers]
            self.master.after(0, lambda: self._apply_printers_list(names, printers, silent, None))
        except Exception as e:
            err = str(e)
            self.master.after(0, lambda: self._apply_printers_list([], [], silent, err))

    def _apply_printers_list(self, names, printers, silent, error):
        try:
            btn = getattr(self, '_printer_refresh_btn', None)
            if btn and btn.winfo_exists():
                btn.config(state='normal', text="🔄 Обновить список принтеров")
        except Exception:
            pass
        if error:
            if not silent:
                self.show_toast(f"Ошибка поиска принтеров: {error}", "print_error")
            return
        try:
            self._printer_combo['values'] = names
            if not names:
                if not silent:
                    self.show_toast("Принтеры не найдены. Убедитесь, что принтер подключён.", "warning")
                return
            if not self._printer_var.get():
                # Auto-select first thermal printer
                for p in printers:
                    if p['type'] == 'usb_pos':
                        self._printer_var.set(p['name'])
                        break
                if not self._printer_var.get():
                    self._printer_var.set(names[0])
            if not silent:
                self.show_toast(f"Найдено принтеров: {len(names)}", "success")
        except Exception:
            pass

    def _populate_block_list(self):
        """Fill block listbox from current order."""
        self._block_listbox.delete(0, tk.END)
        for bid in self._block_order:
            align_val = self._block_align.get(bid, 'left')
            align_char = '⬅' if align_val == 'left' else '➡' if align_val == 'right' else '↔'
            if str(bid).startswith('separator'):
                self._block_listbox.insert(tk.END, f'Разделитель [{align_char}]')
            elif str(bid).startswith('space_sep'):
                self._block_listbox.insert(tk.END, f'Пробел/Отступ [ ]')
            else:
                name = self._block_names.get(bid, bid)
                self._block_listbox.insert(tk.END, f'{name} [{align_char}]')
        self._refresh_available_blocks()

    def _set_block_align(self, align):
        """Set horizontal alignment for the selected block."""
        sel = self._block_listbox.curselection()
        if not sel: return
        bid = self._block_order[sel[0]]
        self._block_align[bid] = align
        self._populate_block_list()
        self._block_listbox.selection_set(sel[0]) # re-select
        self._track_changes()
        self._update_receipt_preview()

    def _block_drag_start(self, event):
        """Start dragging a block."""
        idx = self._block_listbox.nearest(event.y)
        self._drag_data['index'] = idx
        self._block_listbox.selection_clear(0, tk.END)
        self._block_listbox.selection_set(idx)

    def _block_drag_motion(self, event):
        src = self._drag_data.get('index')
        if src is None:
            return
        dst = self._block_listbox.nearest(event.y)
        if dst != src and 0 <= dst < len(self._block_order):
            self._block_order.insert(dst, self._block_order.pop(src))
            self._drag_data['index'] = dst
            self._populate_block_list()
            self._block_listbox.selection_set(dst)
            self._track_changes()
            self._update_receipt_preview()

    def _block_drag_end(self, event):
        """End drag — update preview."""
        self._drag_data['index'] = None
        self._update_receipt_preview()

    def _refresh_available_blocks(self):
        """Fill the add-block combo with blocks not yet present in the check.
        Separators and blank spaces stay available (can be added any number of times)."""
        combo = getattr(self, '_add_block_combo', None)
        if combo is None or not combo.winfo_exists():
            return
        present = set(self._block_order)
        names = []
        for bid, name in self._block_names.items():
            if bid not in present and not str(bid).startswith('separator') and not str(bid).startswith('space_sep'):
                names.append(name)
        names.extend(['Разделитель', 'Пробел (Пустое место)'])
        self._add_block_combo.configure(values=names)
        current = self._add_block_combo.get()
        if current not in names:
            self._add_block_combo.set('Разделитель')

    def _add_block(self):
        """Add new block to order."""
        selected_name = self._add_block_combo.get()
        if not selected_name: return
        
        new_bid = None
        if selected_name == 'Разделитель':
            import uuid
            new_bid = f"separator_{uuid.uuid4().hex[:6]}"
        elif selected_name == 'Пробел (Пустое место)':
            import uuid
            new_bid = f"space_sep_{uuid.uuid4().hex[:6]}"
        else:
            # Находим ключ по значению
            for key, val in self._block_names.items():
                if val == selected_name:
                    new_bid = key
                    break
                    
        if new_bid:
            # Уникальные блоки не добавляем дважды (кроме разделителей)
            if not new_bid.startswith('separator') and new_bid in self._block_order:
                self.show_toast(f"Блок '{selected_name}' уже добавлен", "warning")
                return
            self._block_order.append(new_bid)
            self._populate_block_list()
            self._refresh_available_blocks()
            self._track_changes()
            self._update_receipt_preview()
            # Выделяем добавленный элемент
            self._block_listbox.selection_clear(0, tk.END)
            self._block_listbox.selection_set(tk.END)
            self._block_listbox.see(tk.END)

    def _remove_block(self):
        """Remove selected block."""
        sel = self._block_listbox.curselection()
        if not sel: return
        idx = sel[0]
        bid = self._block_order[idx]
        if bid in ['items_table']:
            self.show_toast("Нельзя удалить базовые блоки", "error")
            return
        del self._block_order[idx]
        self._populate_block_list()
        self._refresh_available_blocks()
        self._track_changes()
        self._update_receipt_preview()
        if self._block_order:
            new_idx = min(idx, len(self._block_order) - 1)
            self._block_listbox.selection_set(new_idx)


    def _move_block(self, direction):
        """Move selected block up (-1) or down (+1)."""
        sel = self._block_listbox.curselection()
        if not sel:
            return
        idx = sel[0]
        new_idx = idx + direction
        if 0 <= new_idx < len(self._block_order):
            self._block_order[idx], self._block_order[new_idx] = \
                self._block_order[new_idx], self._block_order[idx]
            self._populate_block_list()
            self._block_listbox.selection_set(new_idx)
            self._update_receipt_preview()

    def _get_current_receipt_config(self):
        """Build config dict from current UI state."""
        config = settings.get_receipt_config()
        for key, var in self._receipt_vars.items():
            config[key] = var.get()
        config['printer_name'] = self._printer_var.get()
        config['paper_width'] = self._paper_width_var.get()
        config['char_width'] = 48 if self._paper_width_var.get() >= 80 else 32
        config['auto_print'] = self._auto_print_var.get()
        config['auto_cut'] = self._auto_cut_var.get()
        config["text_scale"] = self._text_scale_var.get()
        config["show_partner"] = self._show_partner_var.get()
        config["partial_id"] = self._partial_id_var.get()
        config["show_partner_phone"] = getattr(self, '_show_partner_phone_var', tk.BooleanVar()).get()
        config["show_pv"] = getattr(self, '_show_pv_var', tk.BooleanVar(value=True)).get()
        config["item_layout"] = self._item_layout_var.get() if hasattr(self, '_item_layout_var') else 'compact'
        if config['paper_width'] >= 80:
            config["item_layout"] = 'wide'
        config["block_order"] = list(self._block_order)
        if hasattr(self, '_block_align'):
            config["block_align"] = dict(self._block_align)
        return config

    def _preview_and_track(self, *args):
        """Refresh the receipt preview and flag unsaved changes (advanced receipt options)."""
        self._update_receipt_preview(*args)
        self._track_changes()

    def _update_receipt_preview(self, *args):
        """Update the live receipt preview text."""
        try:
            import receipt_printer
            config = self._get_current_receipt_config()

            # Sample receipt data for preview
            sample_receipt = {
                'number': 42,
                'datetime': datetime.now().isoformat(),
                'items': [
                    {'name': 'Зеленый чай 100г', 'quantity': 2, 'price': 1500, 'sum': 3000, 'pv': 15},
                    {'name': 'Витамин С', 'quantity': 1, 'price': 4200, 'sum': 4200, 'pv': 42},
                ],
                'subtotal': 7200,
                'discount': 200,
                'total': 7000,
                'partner_name': 'Айдос К.',
                'partner_id': '12345678',
                'cashier_user': 'Кассир',
                'payment': {'cash': 10000, 'card': 0, 'internal': 0, 'change': 2800},
            }

            pw = config.get('paper_width', 58)
            char_w = 48 if pw >= 80 else 32
            self._receipt_preview.config(state='normal')
            self._receipt_preview.delete('1.0', tk.END)
            preview_lines = receipt_printer.generate_preview_text(sample_receipt, config)
            scale = float(config.get('text_scale', 1.0))
            desired_size = max(8, int(10 * scale))

            import tkinter.font as _tkfont
            f = _tkfont.Font(font=("Courier", desired_size))
            font_size = desired_size
            avail_w = self._preview_right_frame.winfo_width() - 40
            if avail_w <= 40:
                avail_w = self._paper_shadow.master.winfo_width() - 40
            if avail_w > 40:
                # Auto-fit font so the paper always fits inside the available width
                while font_size > 7 and f.measure("M") * (char_w + 2) + 22 > avail_w:
                    font_size -= 1
                    f.configure(size=font_size)

            # Single font: every generated line already ends at the paper edge
            self._receipt_preview.config(font=("Courier", font_size), width=char_w + 2,
                                         height=len(preview_lines) + 1)
            self._receipt_preview.tag_config('bold', font=("Courier", font_size, "bold"))
            for i, (line_text, is_bold) in enumerate(preview_lines):
                tag = 'bold' if is_bold else None
                self._receipt_preview.insert(tk.END, line_text + '\n', tag)
            self._receipt_preview.config(state='disabled')

            # Size the grey template exactly around the paper (pixels)
            char_px = f.measure("M")
            line_h = f.metrics("linespace")
            px_w = int(char_px * (char_w + 2)) + 22
            px_h = int(line_h * (len(preview_lines) + 1)) + 22
            self._paper_shadow.config(width=px_w, height=px_h)
        except Exception as e:
            print(f"Preview error: {e}")

    def _test_print_receipt(self):
        """Print a test receipt."""
        try:
            import receipt_printer
            config = self._get_current_receipt_config()

            if not config.get('printer_name'):
                messagebox.showwarning("Принтер", "Выберите принтер!")
                return

            test_data = {
                'number': 0,
                'datetime': datetime.now().isoformat(),
                'items': [
                    {'name': 'Тестовый товар', 'quantity': 1, 'price': 100, 'sum': 100},
                ],
                'subtotal': 100,
                'discount': 0,
                'total': 100,
                'payment': {'cash': 100, 'card': 0, 'internal': 0, 'change': 0},
            }

            success, error = receipt_printer.print_receipt(test_data, config)
            if success:
                self.show_toast("Тест-печать отправлена", "print_success")
            else:
                messagebox.showerror("Ошибка печати", error)
        except Exception as e:
            messagebox.showerror("Ошибка", str(e))

    def _print_receipt_for_sale(self, receipt, force_print=False):
        """Print receipt after sale if auto-print is enabled or forced via payment dialog."""
        try:
            import receipt_printer
            config = settings.get_receipt_config()
            # Abort only if it's NOT forced via UI and auto-print is OFF globally
            if not force_print and not config.get('auto_print'):
                return
            if not config.get('printer_name'):
                self.show_toast("Принтер не настроен — чек не напечатан", "warning")
                return

            # Add partner name if exists
            receipt_data = dict(receipt)
            receipt_data['cashier_user'] = self._get_user_device_label()
            if receipt.get('partner_id'):
                partner = self.partners_manager.get_partner_by_id(receipt['partner_id'])
                if partner:
                    receipt_data['partner_name'] = partner.get('full_name', partner.get('name', ''))
                    receipt_data['partner_phone'] = partner.get('phone', '')

            success, error = receipt_printer.print_receipt(receipt_data, config)
            if not success:
                self.show_toast(f"Ошибка авто-печати: {error}", "print_error")
        except Exception as e:
            self.show_toast(f"Ошибка авто-печати: {e}", "print_error")

    def _print_single_receipt(self, receipt):
        """Manually print a specific receipt."""
        try:
            import receipt_printer
            config = settings.get_receipt_config()
            if not config.get('printer_name'):
                messagebox.showwarning("Принтер", "Принтер не настроен. Перейдите в Настройки → Чек.")
                return

            receipt_data = dict(receipt)
            if receipt.get('partner_id'):
                partner = self.partners_manager.get_partner_by_id(receipt['partner_id'])
                if partner:
                    receipt_data['partner_name'] = partner.get('full_name', partner.get('name', ''))

            success, error = receipt_printer.print_receipt(receipt_data, config)
            if success:
                self.show_toast(f"Чек #{receipt['number']} отправлен на печать", "print_success")
            else:
                messagebox.showerror("Ошибка печати", error)
        except Exception as e:
            messagebox.showerror("Ошибка", str(e))

    def toggle_scheduler_fields(self):
        """Enable/disable scheduler fields based on checkbox."""
        state = "normal" if self.scheduler_enabled_var.get() else "disabled"
        for child in self.scheduler_settings_frame.winfo_children():
            self.set_widget_state(child, state)
        

    def _reset_device_id(self):
        """Reset the unique device key to resolve cloning issues."""
        if not messagebox.askyesno("Сброс ID устройства", 
                                   "Это действие сбросит уникальный ключ этой кассы. \n"
                                   "Синхронизированные данные останутся, но касса получит новый ID.\n\n"
                                   "Продолжить?"):
            return
            
        try:
            settings.reset_device_key()
            messagebox.showinfo("Готово", "ID устройства сброшен. Приложение будет перезагружено.")
            self.restart_app()
        except Exception as e:
            messagebox.showerror("Ошибка", f"Не удалось сбросить ID: {e}")

    def set_widget_state(self, widget, state):
        """Recursively set state for widget and children."""
        try:
            widget.configure(state=state)
        except tk.TclError:
            pass
        for child in widget.winfo_children():
            self.set_widget_state(child, state)

    def browse_watch_directory(self):
        """Browse for watch directory."""
        directory = filedialog.askdirectory(title=get_text('select_directory', self.lang))
        if directory:
            self.watch_directory_var.set(directory)

    def show_toast(self, message: str, toast_type: str = "success", duration: int = 2500, color: Optional[str] = None) -> None:
        """Show in-app toast notification with customization and filtering."""
        app_set = settings.get_appearance_settings()
        
        # 1. Filter by active tab
        try:
            if hasattr(self, 'notebook'):
                active_tab_id = self.notebook.select()
                if active_tab_id:
                    # We identify tab by index or name. Let's use index as it's more stable
                    try:
                        curr_tab_idx = self.notebook.index("current")
                        filtered = app_set.get('filtered_tabs', [])
                        # Ensure we compare against integers if that's what we store
                        if curr_tab_idx in filtered:
                            return # Notification filtered for this tab
                    except:
                        pass
        except:
            pass

        # 2. Filter by Granular Type
        if toast_type == "success" and not self.toast_show_success_var.get(): return
        if toast_type == "error" and not self.toast_show_error_var.get(): return
        if toast_type == "warning" and not self.toast_show_warning_var.get(): return
        if toast_type == "info" and not self.toast_show_info_var.get(): return
        if toast_type == "print_success" and not self.toast_show_print_success_var.get(): return
        if toast_type == "print_error" and not self.toast_show_print_error_var.get(): return
        if toast_type == "sync_info" and not self.toast_show_sync_var.get(): return
        if toast_type == "bot_status" and not self.toast_show_bot_var.get(): return
        if toast_type == "inventory" and not self.toast_show_inventory_var.get(): return
        if toast_type == "sales" and not self.toast_show_sales_var.get(): return

        scale = self.toast_size_var.get()
        alpha = self.toast_alpha_var.get()
        pos_pref = self.toast_position_var.get()
        
        # Colors from settings or defaults
        colors_cfg = app_set.get('toast_colors', {}).get(toast_type, {})
        bg_color = colors_cfg.get('bg', "#E8F5E9")
        fg_color = colors_cfg.get('fg', "#2E7D32")
        border_color = colors_cfg.get('border', "#81C784")

        # Create Toplevel for real alpha/transparency support.
        # Build it hidden first: on Windows an overrideredirect window that is
        # mapped before geometry() may render at (0,0) — withdraw prevents it.
        toast = tk.Toplevel(self.master)
        toast.overrideredirect(True)
        toast.transient(self.master)
        toast.withdraw()
        
        # Clamp alpha to ensure readability even if setting is extremely low
        effective_alpha = max(0.65, alpha)
        toast.attributes("-alpha", effective_alpha)
        # Always keep the toast above the app window and modal dialogs
        # (without -topmost Windows may render it behind a grab dialog).
        try:
            toast.attributes("-topmost", True)
        except Exception:
            pass
        toast.configure(bg=border_color)
        
        # Refined Font Scaling: Base 11 + dynamic range
        # This prevents "unreasonably large" results at 2.0 while keeping 0.5 legible
        font_size = int(11 + 8 * (scale - 1.0))
        font_name = self.font_normal_tuple[0] if hasattr(self, 'font_normal_tuple') else "Arial"
        scaled_font = (font_name, font_size, "bold" if scale > 1.4 else "normal")
        
        # Refined Padding
        px = int(18 + 12 * (scale - 1.0))
        py = int(10 + 6 * (scale - 1.0))
        
        inner = tk.Frame(toast, bg=bg_color, padx=px, pady=py)
        inner.pack(padx=1, pady=1)
        
        display_msg = message if len(message) < 500 else message[:497] + '...'
        toast_label = tk.Label(inner, text=display_msg, font=scaled_font,
                              bg=bg_color, fg=fg_color, wraplength=int(600 * scale),
                              justify="left")
        toast_label.pack()
        
        # Positioning Logic
        toast.update_idletasks() # Critical for winfo_reqwidth
        try:
            self.master.update_idletasks()
        except Exception:
            pass
        # If the main window is hidden or not laid out yet (winfo sizes are
        # still 1x1 on Windows during early startup), fall back to screen
        # coordinates so the toast is centered on the display, not at (0,0).
        try:
            master_visible = (self.master.winfo_viewable() == 1
                              and self.master.winfo_width() > 1
                              and self.master.winfo_height() > 1)
        except Exception:
            master_visible = False
        if master_visible:
            rx = self.master.winfo_rootx()
            ry = self.master.winfo_rooty()
            rw = self.master.winfo_width()
            rh = self.master.winfo_height()
        else:
            rx = 0
            ry = 0
            rw = self.master.winfo_screenwidth()
            rh = self.master.winfo_screenheight()
        
        tw = toast.winfo_reqwidth()
        th = toast.winfo_reqheight()
        
        # Horizontal Center (clamped to screen bounds so Windows never
        # pushes the toast off-screen or into the top-left corner)
        sw = self.master.winfo_screenwidth()
        sh = self.master.winfo_screenheight()
        x = max(0, min(rx + (rw // 2) - (tw // 2), sw - tw - 4))
        
        # Vertical Position based on preference + Stacking offset
        if not hasattr(self.master, '_active_toasts'):
            self.master._active_toasts = []
            
        # Clean up any destroyed toasts from the tracking list
        self.master._active_toasts = [t for t in self.master._active_toasts if t.winfo_exists()]
        
        # Max 3 visible toasts — destroy the oldest
        while len(self.master._active_toasts) >= 3:
            oldest = self.master._active_toasts.pop(0)
            try: oldest.destroy()
            except: pass
        
        # Calculate cumulative offset
        offset = 0
        for t in self.master._active_toasts:
            offset += t.winfo_reqheight() + 10 # 10px gap
            
        if pos_pref == 'bottom_center':
            y = max(0, min(ry + rh - th - 60 - offset, sh - th - 4))
        else:
            y = max(0, min(ry + 60 + offset, sh - th - 4))
            
        toast.geometry(f"{tw}x{th}+{x}+{y}")
        # Show now that the position is final (no flash at 0,0 on Windows)
        toast.deiconify()
        self.master._active_toasts.append(toast)
        # Lift above the main window AND any open modal dialogs so the toast
        # is never hidden behind them.
        try:
            toast.lift()
        except Exception:
            pass

        def hide_toast():
            try:
                if toast in self.master._active_toasts:
                    self.master._active_toasts.remove(toast)
                toast.destroy()
            except:
                pass
        
        # Track the hide timer so it is cancelled before window destroy
        # (prevents bgerror "invalid command name" on user switch / quit).
        try:
            self._schedule(duration, hide_toast)
        except Exception:
            try:
                self.master.after(duration, hide_toast)
            except Exception:
                pass

    # =========================================================================
    # SETTINGS PERMISSION GATING
    # =========================================================================
    
    def _apply_settings_permissions(self):
        """Hide settings sections that the current user doesn't have access to.

        After the side-menu refactor, sections live as nav-button pages
        (self.nav_buttons[page_id]). Per-section sub-frames inside a page
        (e.g. the Autorun / Performance frames inside the automation page)
        are gated separately by hiding the sub-frame directly.
        """
        if not getattr(self, 'nav_buttons', None):
            return

        # Map permission key -> nav-button page id to gate.
        page_perm_map = {
            'settings_appearance':   'appearance',
            'settings_printer':      'printer',
            'settings_automation':   'automation',
            'settings_sync':         'system',
            'settings_integrations': 'integrations',
            'settings_database':     'database',
            'user_management':       'users',
        }
        # Map permission key -> sub-frame attribute (inside another page).
        frame_perm_map = {
            'settings_autorun':     '_autorun_frame',
            'settings_performance': '_perf_frame',
        }

        hidden_pages = []
        for perm_key, page_id in page_perm_map.items():
            if not self.has_permission(perm_key):
                btn = self.nav_buttons.get(page_id)
                if btn and btn.winfo_exists():
                    btn.pack_forget()
                    hidden_pages.append(page_id)

        for perm_key, attr_name in frame_perm_map.items():
            if not self.has_permission(perm_key):
                frame = getattr(self, attr_name, None)
                if frame and frame.winfo_exists():
                    frame.pack_forget()

        # If the currently-visible page was just hidden, fall back to 'main'.
        if hidden_pages and getattr(self, 'settings_pages', None):
            try:
                active = None
                for pid, (canv, sb, sf) in self.settings_pages.items():
                    if canv.winfo_ismapped():
                        active = pid
                        break
                if active in hidden_pages:
                    self.nav_buttons.get('main', None) and self.nav_buttons['main'].invoke()
            except Exception:
                pass

    # =========================================================================
    # USER MANAGEMENT METHODS (Settings tab, admin only)
    # =========================================================================
    
    def _refresh_users_list(self):
        """Refresh the users listbox in settings."""
        if not hasattr(self, '_users_listbox'):
            return
        self._users_listbox.delete(0, tk.END)
        users = self.users_manager.get_all_users()
        for u in users:
            role_label = settings.ROLE_LABELS.get(u['role'], u['role'])
            self._users_listbox.insert(tk.END, f"{u['display_name']}  [{role_label}]")
    
    def _select_user_in_list(self, username):
        """Highlight the user with the given username in the users listbox."""
        if not hasattr(self, '_users_listbox'):
            return
        users = self.users_manager.get_all_users()
        for idx, u in enumerate(users):
            if u['username'] == username:
                self._users_listbox.selection_clear(0, tk.END)
                self._users_listbox.selection_set(idx)
                self._users_listbox.see(idx)
                return
    
    def _get_selected_user(self, silent=False):
        """Get selected user from listbox."""
        sel = self._users_listbox.curselection()
        if not sel:
            if not silent:
                messagebox.showwarning("Выбор", "Выберите пользователя из списка")
            return None
        users = self.users_manager.get_all_users()
        if sel[0] < len(users):
            return users[sel[0]]
        return None
    
    def _validate_pin_input(self, P):
        """Validation for PIN entries: digits only, max 4."""
        if P == "": return True
        return P.isdigit() and len(P) <= 4

    def _show_create_user_dialog(self):
        """Show dialog to create a new user (admin or superadmin only)."""
        if self.current_role not in ('admin', 'superadmin'):
            return
            
        c = self.colors
        dialog = self.create_modal_dialog("➕ Новый пользователь", width=540, height=460, scrollable=False)
        main = dialog.container
        try:
            self._build_create_user_form(dialog, main, c)
        except Exception as e:
            try:
                dialog.destroy()
            except Exception:
                pass
            messagebox.showerror("Ошибка", f"Не удалось открыть окно создания пользователя:\n{e}")

    def _build_create_user_form(self, dialog, main, c):
        tk.Label(main, text="➕ Создание пользователя", font=self.font_bold_tuple, fg=c['fg'], bg=c['bg']).pack(pady=15)
        
        frame = tk.Frame(main, bg=c['bg'])
        frame.pack(pady=10, padx=20, fill="x")
        frame.columnconfigure(1, weight=1)
        
        tk.Label(frame, text="Имя (Display Name):", font=self.font_normal_tuple, bg=c['bg']).grid(row=0, column=0, sticky="e", pady=6, padx=5)
        name_entry = tk.Entry(frame, font=self.font_normal_tuple, width=26)
        name_entry.grid(row=0, column=1, pady=6, sticky="ew")
        
        tk.Label(frame, text="Роль:", font=self.font_normal_tuple, bg=c['bg']).grid(row=1, column=0, sticky="e", pady=6, padx=5)
        role_display = {v: k for k, v in settings.ROLE_LABELS.items() if k != 'superadmin'}
        role_var = tk.StringVar(value=settings.ROLE_LABELS.get('cashier', 'Кассир'))
        ttk.Combobox(frame, textvariable=role_var, values=list(role_display.keys()),
                     state='readonly', width=24, font=self.font_normal_tuple).grid(row=1, column=1, pady=6, sticky="ew")
        
        vcmd = (dialog.register(self._validate_pin_input), '%P')
        tk.Label(frame, text="PIN-код (4 цифры):", font=self.font_normal_tuple, bg=c['bg']).grid(row=2, column=0, sticky="e", pady=5, padx=5)
        pin_entry = tk.Entry(frame, font=self.font_normal_tuple, width=10, show="●", justify="center", validate="key", validatecommand=vcmd)
        pin_entry.grid(row=2, column=1, pady=5, sticky="w")
        
        tk.Label(frame, text="Повторите PIN:", font=self.font_normal_tuple, bg=c['bg']).grid(row=3, column=0, sticky="e", pady=5, padx=5)
        pin2_entry = tk.Entry(frame, font=self.font_normal_tuple, width=10, show="●", justify="center", validate="key", validatecommand=vcmd)
        pin2_entry.grid(row=3, column=1, pady=5, sticky="w")
        
        tk.Label(frame, text="Подсказка:", font=self.font_normal_tuple, bg=c['bg']).grid(row=4, column=0, sticky="e", pady=6, padx=5)
        hint_entry = tk.Entry(frame, font=self.font_normal_tuple, width=26)
        hint_entry.grid(row=4, column=1, pady=6)
        
        error_label = tk.Label(frame, text="", font=self.font_small_tuple, fg="red", bg=c['bg'])
        error_label.grid(row=5, column=0, columnspan=2, pady=5)
        
        def _filter(entry):
            val = entry.get()
            filtered = ''.join(ch for ch in val if ch.isdigit())[:4]
            if val != filtered:
                entry.delete(0, tk.END)
                entry.insert(0, filtered)
        for e in (pin_entry, pin2_entry):
            e.bind('<KeyRelease>', lambda ev, ent=e: _filter(ent))
        
        def on_save():
            name = name_entry.get().strip()
            pin = pin_entry.get().strip()
            pin2 = pin2_entry.get().strip()
            if not name:
                error_label.config(text="Введите имя"); return
            if len(pin) != 4 or not pin.isdigit():
                error_label.config(text="PIN: 4 цифры"); return
            if pin != pin2:
                error_label.config(text="PIN не совпадают"); return
            user = self.users_manager.create_user(
                username=name.lower(), display_name=name,
                role=role_display[role_var.get()], pin=pin, pin_hint=hint_entry.get().strip())
            if user:
                self._refresh_users_list()
                dialog.destroy()
            else:
                error_label.config(text="Имя уже занято")
        
        self._add_dialog_button(dialog, "Создать", on_save, 'primary', side='left')
        self._add_dialog_button(dialog, "Отмена", dialog.destroy, 'neutral', side='right')
        self.bind_dialog_keys(dialog, confirm_callback=on_save, cancel_callback=dialog.destroy)
        name_entry.focus_set()
    
    def _show_reset_pin_dialog(self):
        """Reset PIN for selected user."""
        user = self._get_selected_user()
        if not user:
            return
        c = self.colors
        dialog = self.create_modal_dialog(f"Сброс PIN — {user['display_name']}", width=540, height=400, scrollable=False)
        main = dialog.container

        tk.Label(main, text="🔑 Сброс PIN-кода", font=self.font_bold_tuple, fg=c['fg'], bg=c['bg']).pack(pady=(15, 4))
        tk.Label(main, text=f"Пользователь: {user['display_name']}", font=self.font_normal_tuple, bg=c['bg'], fg=c['fg_secondary']).pack(pady=(0, 10))

        frame = tk.Frame(main, bg=c['bg'])
        frame.pack(pady=10, padx=30)
        frame.grid_columnconfigure(1, weight=1)

        PIN_PLACEHOLDER = "4 цифры"

        tk.Label(frame, text="Новый PIN:", font=self.font_normal_tuple, bg=c['bg']).grid(row=0, column=0, sticky="e", pady=8, padx=(0, 10))
        pin_entry = tk.Entry(frame, font=self.font_normal_tuple, width=12, show="", justify="center", fg=c['fg_muted'])
        pin_entry.grid(row=0, column=1, sticky="w", padx=10)

        tk.Label(frame, text="Подсказка:", font=self.font_normal_tuple, bg=c['bg']).grid(row=1, column=0, sticky="e", pady=8, padx=(0, 10))
        hint_entry = tk.Entry(frame, font=self.font_normal_tuple, width=26)
        hint_entry.grid(row=1, column=1, sticky="w", padx=10)

        # Placeholder: shown greyed until the user types the first digit
        pin_entry.insert(0, PIN_PLACEHOLDER)

        def _on_pin_key(_e):
            if pin_entry.get() == PIN_PLACEHOLDER:
                ch = _e.char
                if ch and ch.isdigit():
                    pin_entry.delete(0, tk.END)
                    pin_entry.config(show="●", fg=c['fg'])
                    pin_entry.insert(0, ch)

        def _filter_pin(_e):
            if pin_entry.get() == PIN_PLACEHOLDER:
                return
            val = pin_entry.get()
            filtered = ''.join(ch for ch in val if ch.isdigit())[:4]
            if val != filtered:
                pin_entry.delete(0, tk.END)
                pin_entry.insert(0, filtered)

        def _restore_placeholder(_e):
            if not pin_entry.get():
                pin_entry.config(show="")
                pin_entry.insert(0, PIN_PLACEHOLDER)
                pin_entry.config(fg=c['fg_muted'])

        pin_entry.bind('<Key>', _on_pin_key)
        pin_entry.bind('<KeyRelease>', _filter_pin)
        pin_entry.bind('<FocusOut>', _restore_placeholder)

        # Explicit Tab traversal: keep focus inside the dialog
        # (PIN → hint → save button), never reaching the users listbox behind it
        pin_entry.bind('<Tab>', lambda ev: (hint_entry.focus_set(), "break")[1])
        hint_entry.bind('<Tab>', lambda ev: (save_btn.focus_set(), "break")[1])

        def on_save():
            pin = pin_entry.get().strip()
            if pin == PIN_PLACEHOLDER:
                pin = ''
            if len(pin) != 4 or not pin.isdigit():
                messagebox.showerror("Ошибка", "PIN должен состоять из 4 цифр")
                return
            self.users_manager.reset_pin(user['username'], pin, hint_entry.get().strip())
            self.show_toast(f"PIN для {user['display_name']} изменен", "success")
            dialog.destroy()

        btn_f = tk.Frame(main, bg=c['bg'])
        btn_f.pack(pady=18)
        save_btn = self._btn(btn_f, text="Сохранить", command=on_save, style='success', width=14, cursor='hand2')
        save_btn.pack(side="left", padx=10)
        self._btn(btn_f, text="Отмена", command=dialog.destroy, style='neutral', width=14, cursor='hand2').pack(side="left", padx=10)
        self.bind_dialog_keys(dialog, confirm_callback=on_save, cancel_callback=dialog.destroy)
        pin_entry.focus_set()
    
    def _on_user_selected_for_perms(self):
        """Update permissions pane when a user is selected."""
        user = self._get_selected_user(silent=True)
        for widget in self._perm_content.winfo_children():
            widget.destroy()
            
        if not user:
            self.lbl_select_user_perm = tk.Label(self._perm_content, text="Выберите пользователя для редактирования прав", font=self.font_small_tuple, bg=self.colors['bg'], fg=self.colors['fg_muted'])
            self.lbl_select_user_perm.pack(pady=20)
            return

        c = self.colors
        
        # Header with username
        header_f = tk.Frame(self._perm_content, bg=c['bg'])
        header_f.pack(fill="x", pady=(0, 10))
        role_label = settings.ROLE_LABELS.get(user['role'], user['role'])
        tk.Label(header_f, text=f"👤 {user['display_name']} ({role_label})", font=self.font_small_bold_tuple, bg=c['bg'], fg=c['accent']).pack(side="left")
        
        if user['role'] == 'superadmin':
            warn_f = tk.Frame(self._perm_content, bg=c.get('warning_bg', '#FFF9C4'), pady=15, padx=15)
            warn_f.pack(fill="x", pady=20)
            tk.Label(warn_f, text="👑 Суперадмин", font=self.font_bold_tuple, bg=c.get('warning_bg', '#FFF9C4'), fg=c.get('warning', '#FFA000')).pack(pady=(0, 5))
            tk.Label(warn_f, text="Этот пользователь имеет полный доступ ко всем функциям системы.\nЕго права и роль не могут быть изменены.", 
                     font=self.font_small_tuple, bg=c.get('warning_bg', '#FFF9C4'), fg=c['fg'], justify="center").pack()
            return
            
        # Role selector
        role_display = {v: k for k, v in settings.ROLE_LABELS.items() if k != 'superadmin'}
        role_f = tk.Frame(self._perm_content, bg=c['bg'])
        role_f.pack(fill="x", pady=5)
        tk.Label(role_f, text="Шаблон роли:", font=self.font_small_tuple, bg=c['bg'], fg=c['fg_secondary']).pack(side="left")
        
        role_var = tk.StringVar(value=settings.ROLE_LABELS.get(user['role'], user['role']))
        role_combo = ttk.Combobox(role_f, textvariable=role_var, values=list(role_display.keys()), state='readonly', width=16, font=self.font_small_tuple)
        role_combo.pack(side="left", padx=10)
        
        perm_vars = {}
        current_perms = user.get('permissions', {})

        def _role_key():
            return role_display.get(role_var.get(), user['role'])

        def apply_template(*_):
            from db_sqlite import UsersManagerSQL
            template = UsersManagerSQL.ROLE_TEMPLATES.get(_role_key(), {})
            for key, var in perm_vars.items():
                var.set(template.get(key, False))

        role_combo.bind('<<ComboboxSelected>>', apply_template)
        self._btn(role_f, text="Применить", command=apply_template, style='neutral', compact=True, cursor='hand2').pack(side="left")

        # Permissions Grid - WRAP IN SCROLLABLE CANVAS (fixed height, width-synced)
        scroll_wrapper = tk.Frame(self._perm_content, bg=c['bg'], height=420)
        scroll_wrapper.pack(fill="both", expand=True, pady=10)
        scroll_wrapper.pack_propagate(False)
        
        canvas = tk.Canvas(scroll_wrapper, bg=c['bg'], highlightthickness=0)
        scrollbar = AutoScrollbar(scroll_wrapper, orient="vertical", command=canvas.yview)
        scrollable_frame = tk.Frame(canvas, bg=c['bg'])

        canvas_win = canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")

        def _sync_scroll_region(_e):
            canvas.configure(scrollregion=canvas.bbox("all"))

        def _sync_canvas_width(e):
            canvas.itemconfig(canvas_win, width=e.width)

        scrollable_frame.bind("<Configure>", _sync_scroll_region)
        canvas.bind("<Configure>", _sync_canvas_width)
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        
        # Categories stacked vertically; permissions inside in 2 columns
        for idx, (cat_name, items) in enumerate(self.PERM_CATEGORIES):
            cat_lf = tk.LabelFrame(scrollable_frame, text=f" {cat_name} ", bg=c['bg'], fg=c['accent'], pady=5, padx=10, font=self.font_small_bold_tuple)
            cat_lf.pack(fill="x", pady=5, padx=5)
            cat_lf.grid_columnconfigure(0, weight=1)
            cat_lf.grid_columnconfigure(1, weight=1)
            
            for i, (key, icon, label) in enumerate(items):
                var = tk.BooleanVar(value=current_perms.get(key, False))
                perm_vars[key] = var
                cb = tk.Checkbutton(cat_lf, text=f"{icon} {label}", variable=var, 
                                   font=self.font_small_tuple, bg=c['bg'], fg=c['fg'], 
                                   activebackground=c['bg'], selectcolor=c['bg'], anchor="w")
                cb.grid(row=i // 2, column=i % 2, sticky="w", pady=1, padx=(0, 8))
                
        # Snapshot of the state as rendered: the global "Сохранить" button in
        # Settings invokes this hook on every save, so skip the write/rerender/
        # toast when nothing was actually changed by the operator.
        initial_perms = {k: var.get() for k, var in perm_vars.items()}
        initial_role = _role_key()

        def save_perms():
            new_perms = {k: v.get() for k, v in perm_vars.items()}
            new_role = _role_key()
            if new_perms == initial_perms and new_role == initial_role:
                return
            self.users_manager.update_permissions(user['username'], new_perms)
            if new_role != initial_role:
                self.users_manager.update_role(user['username'], new_role)
            self._refresh_users_list()
            # Restore selection on the saved user and re-render the pane so the
            # header/template immediately show the updated role.
            self._select_user_in_list(user['username'])
            self._on_user_selected_for_perms()
            self.show_toast(f"Права {user['display_name']} сохранены", "success")

        self.current_permissions_hook = save_perms

        # Universal mousewheel + drag-pan for the permissions grid
        canvas.after(100, lambda: self.enable_scroll_area(canvas, scrollable_frame))
    
    def _delete_selected_user(self):
        """Delete selected user (with confirmation)."""
        user = self._get_selected_user()
        if not user:
            return
        if user['username'] == self.current_user.get('username', ''):
            messagebox.showwarning("Удаление", "Нельзя удалить себя!")
            return
        if user.get('role') == 'superadmin':
            messagebox.showwarning("Удаление", "Суперадмина удалить нельзя!")
            return
        if messagebox.askyesno("Подтверждение", f"Удалить пользователя {user['display_name']}?"):
            if self.users_manager.delete_user(user['username']):
                self._refresh_users_list()
                self.show_toast(f"Пользователь {user['display_name']} удалён", "success")
            else:
                messagebox.showerror("Ошибка", "Невозможно удалить последнего администратора")
