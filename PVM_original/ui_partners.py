# -*- coding: utf-8 -*-
"""
PVM.core - Partners Tab Mixin
================================
Partner management, partner history, add/edit dialogs.
"""

import re
import json
import tkinter as tk
from tkinter import ttk, messagebox

from ui_lang import get_text
from ui_dialogs import AutoScrollbar


class PartnersTabMixin:
    """Partners tab methods for GreenLeafApp."""

    def create_partners_tab(self):
        """Create partners management tab with Notebook."""
        c = self.colors
        
        self.partners_notebook = ttk.Notebook(self.partners_frame)
        self.partners_notebook.pack(fill="both", expand=True)
        
        # Tab 1: List
        list_tab = tk.Frame(self.partners_notebook, bg=c['bg'])
        self.partners_notebook.add(list_tab, text="  👥 Список партнеров  ")
        self._create_partners_list_ui(list_tab)
        
        # Tab 2: History
        history_tab = None
        if self.has_permission('partner_history'):
            history_tab = tk.Frame(self.partners_notebook, bg=c['bg'])
            self.partners_notebook.add(history_tab, text="  ⏱ История изменений  ")
            self.create_partners_history_subtab(history_tab)
        
        self.partner_history_tab_ref = history_tab
        
        def on_partners_tab_changed(event):
            tab_id = self.partners_notebook.select()
            if not tab_id: return
            tab_widget = self.partners_notebook.nametowidget(tab_id)
            if hasattr(self, 'partner_history_tab_ref') and tab_widget == self.partner_history_tab_ref:
                self.refresh_partners_history()
        
        self.partners_notebook.bind('<<NotebookTabChanged>>', on_partners_tab_changed)

    def _create_partners_list_ui(self, parent):
        """Internal method to build the partners list UI."""
        c = self.colors
        main_frame = tk.Frame(parent, bg=c['bg'])
        main_frame.pack(fill="both", expand=True)
        
        # TOP - Info + Search + Add
        top_frame = tk.Frame(main_frame, bg=c['bg'])
        top_frame.pack(fill="x", padx=10, pady=5)
        
        # Info banner
        info_frame = tk.Frame(top_frame, bg=c['success_bg'])
        info_frame.pack(fill="x", pady=5)
        tk.Label(info_frame, text="ℹ️ " + get_text('partner_discount_info', self.lang),
                font=self.font_bold_tuple, bg=c['success_bg'], fg=c['success']).pack(pady=5)
        
        # Search with + button
        search_frame = tk.Frame(top_frame, bg=c['bg'])
        search_frame.pack(fill="x", pady=5)
        
        self.partners_search_var = tk.StringVar()
        self.partners_search = self._build_search_bar(
            search_frame, c['bg'], textvariable=self.partners_search_var)
        self.partners_search.bind('<KeyRelease>', self.filter_partners_list)
        self.partners_search.bind('<Down>', lambda e: (self.partners_tree.focus_set(), self.partners_tree.selection_set(self.partners_tree.get_children()[0]) if self.partners_tree.get_children() else None))
        
        btn_add = self._btn(search_frame, text="+ Добавить", command=self.show_add_partner_dialog, style='success', cursor='hand2')
        btn_add.pack(side="right", padx=5)
        
        if not self.has_permission('partner_create'):
            btn_add.config(state='disabled', bg=c['bg_tertiary'])
        
        # BOTTOM - Stats bar (pack this first so center frame takes remaining space)
        stats_frame = tk.Frame(main_frame, bg=c['bg_secondary'], pady=5)
        stats_frame.pack(side="bottom", fill="x")
        
        self.partners_stats_label = tk.Label(stats_frame, text="Всего партнеров: 0", 
                                            font=self.font_small_tuple, bg=c['bg_secondary'], fg=c['fg_muted'])
        self.partners_stats_label.pack(side="left", padx=20)

        # CENTER - Tree + Scrollbar Frame
        tree_container = tk.Frame(main_frame, bg=c['bg'])
        tree_container.pack(fill="both", expand=True, padx=0, pady=5)

        # Partners table - parented to tree_container
        columns = ('num', 'id', 'name', 'phone', 'status', 'purchases', 'spent')
        self.partners_tree = ttk.Treeview(tree_container, columns=columns, show='headings', height=18)
        
        headers = ['#', 'ID', get_text('name', self.lang), get_text('partner_phone', self.lang),
                  'Статус', 'Покупок', 'Потрачено']
        widths = [50, 125, 240, 165, 190, 110, 155]
        minwidths = [40, 100, 200, 150, 150, 80, 120]
        
        for col, hdr, w, mw in zip(columns, headers, widths, minwidths):
            self.partners_tree.heading(col, text=hdr)
            self.partners_tree.column(col, width=w, minwidth=mw, anchor='center' if col not in ['name'] else 'w')
            
        self.setup_treeview_sorting(self.partners_tree, columns, numeric_cols=['num', 'purchases', 'spent'])
        self.setup_universal_navigation(self.partners_tree, lambda: self.show_edit_partner_dialog(None)) # Added
        self.partners_tree.bind('<Button-1>', self.prevent_treeview_resize)
        
        # Style for blocked partners
        self.partners_tree.tag_configure('blocked', foreground=c['error'], font=self.font_bold_tuple)

        scrollbar = AutoScrollbar(tree_container, orient="vertical", command=self.partners_tree.yview)
        self.partners_tree.configure(yscrollcommand=scrollbar.set)
        
        self.partners_tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        
        self.bind_mousewheel(self.partners_tree)
        
        # Double-click to edit
        self.partners_tree.bind('<Double-1>', self.show_edit_partner_dialog)
        
        self.refresh_partners_list()

    def _validate_partner_id_format(self, text):
        """Validate partner ID format: strictly AA12345678 Name Surname.
        Auto-lowercases ID letters, auto-capitalizes name words.
        Returns (is_valid, partner_id, display_name, error_msg)."""
        import re
        text = text.strip()
        match = re.match(r'^([a-zA-Z]{2}\d{8}) (.+)$', text)
        if not match:
            return False, None, None, (
                "Неверный формат ID. Требуется: AA12345678 Имя Фамилия\n"
                "(2 латинские буквы + 8 цифр, ровно один пробел, минимум 2 слова)"
            )
        pid = match.group(1).lower()
        rest = match.group(2).strip()
        if '  ' in rest or not rest or len(rest.split()) < 2:
            return False, None, None, (
                "Неверный формат. После ID должен быть ровно один пробел "
                "и минимум два слова (имя и фамилия)"
            )
        rest = ' '.join(word.capitalize() for word in rest.split())
        return True, pid, rest, None

    def show_add_partner_dialog(self):
        """Show standardized dialog to add new partner."""
        self.open_add_partner_dialog()

    def show_edit_partner_dialog(self, event=None):
        """Show dialog to edit existing partner (double-click) - simplified."""
        try:
            sel = self.partners_tree.selection()
            if not sel:
                return
            
            # Get partner ID from tree tag
            item_tags = self.partners_tree.item(sel[0]).get('tags', ())
            if not item_tags:
                messagebox.showerror(get_text('error_title', self.lang), get_text('internal_error', self.lang))
                return
            # Treeview may return a numeric-only tag as int; partner IDs are
            # always handled as strings in the editor and validation logic.
            pid = str(item_tags[0])
            partner = self.partners_manager.get_partner(pid)
            if not partner:
                messagebox.showerror(get_text('error_title', self.lang), get_text('partner_not_found', self.lang).format(id=pid))
                return
            
            is_blocked = partner.get('is_blocked', 0)
        except Exception as e:
            messagebox.showerror(get_text('error_title', self.lang), get_text('failed_to_open_partner', self.lang).format(error=e))
            return
        
        c = self.colors
        dialog = self.create_modal_dialog(f"Редактировать: {partner['name']}", 550, 420, scrollable=True)
        
        main = dialog.container
        
        # Center content frame
        center_frame = tk.Frame(main, bg=c['bg'])
        center_frame.pack(expand=True, fill="both", padx=20, pady=5)
        center_frame.columnconfigure(1, weight=1)
        
        row = 0
        def add_row(label_text, var, state='normal'):
            nonlocal row
            tk.Label(center_frame, text=label_text, font=self.font_normal_tuple, bg=c['bg']).grid(row=row, column=0, sticky="w", pady=4)
            entry = tk.Entry(center_frame, textvariable=var, font=self.font_normal_tuple, width=35)
            entry.grid(row=row, column=1, padx=10, pady=4, sticky="ew")
            if state != 'normal':
                entry.config(state=state)
            row += 1
            return entry

        # Name (combined format: AA12345678 Иванов Иван)
        initial_name = partner.get('name', '')
        if pid:
            if not initial_name:
                initial_name = f"{pid} "
            elif pid not in initial_name:
                initial_name = f"{pid} {initial_name}".strip()
        name_var = tk.StringVar(value=initial_name)
        name_entry = add_row("Имя партнёра:", name_var)

        # Phone
        phone_var = tk.StringVar(value=partner.get('phone', ''))
        phone_entry = add_row("Телефон:", phone_var)
        
        # Notes (multiline)
        tk.Label(center_frame, text="Заметка:", font=self.font_normal_tuple, bg=c['bg']).grid(row=row, column=0, sticky="w", pady=4)
        notes_text = tk.Text(center_frame, height=3, width=35, font=self.font_normal_tuple, wrap=tk.WORD,
                             relief="solid", bd=1, bg=c['input_bg'], fg=c['input_fg'])
        notes_text.grid(row=row, column=1, padx=10, pady=4, sticky="ew")
        notes_text.insert("1.0", partner.get('notes', ''))
        row += 1

        # Block Status Toggle
        self._is_blocked_var = tk.IntVar(value=is_blocked)
        block_frame = tk.Frame(center_frame, bg=c['bg'])
        block_frame.grid(row=row, column=0, columnspan=2, pady=4)
        
        def update_block_lbl():
            blocked = self._is_blocked_var.get()
            lbl = "🔒 ЗАБЛОКИРОВАН" if blocked else "🔓 Активен"
            clr = c['error'] if blocked else c['success']
            self.block_status_label.config(text=lbl, fg=clr)

        cb = tk.Checkbutton(block_frame, text=" Заблокировать", variable=self._is_blocked_var, 
                            font=self.font_bold_tuple, bg=c['bg'], activebackground=c['bg'],
                            fg=c['error'], command=update_block_lbl)
        if not self.has_permission('partner_block'):
            cb.config(state='disabled')
        cb.pack(side="left", padx=5)

        block_lbl_text = "🔒 ЗАБЛОКИРОВАН" if is_blocked else "🔓 Активен"
        block_lbl_color = c['error'] if is_blocked else c['success']
        self.block_status_label = tk.Label(block_frame, text=block_lbl_text, font=self.font_bold_tuple,
                                           bg=c['bg'], fg=block_lbl_color, width=18)
        self.block_status_label.pack(side="left", padx=5)
        row += 1
        
        if not self.has_permission('partner_edit'):
            name_entry.config(state='readonly')
            phone_entry.config(state='readonly')
            notes_text.config(state='disabled')
        
        # Info block (non-editable) — 3 rows with stats
        info_frame = tk.Frame(center_frame, bg=c['bg_secondary'], pady=3, highlightbackground=c['bg_tertiary'], highlightthickness=1)
        info_frame.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        for col in range(4):
            info_frame.grid_columnconfigure(col, weight=1)
        
        def fmt_dt(s):
            if not s: return "—"
            try: return s[:10]
            except: return "—"

        purchases = partner.get('total_purchases', 0) or 0
        spent = partner.get('total_spent', 0) or 0
        avg_cheque = int(spent / purchases) if purchases > 0 else 0

        # Row 0
        tk.Label(info_frame, text="Создан:", font=self.font_small_tuple, bg=c['bg_secondary'], fg=c['fg_muted']).grid(row=0, column=0, sticky="w", padx=(8, 2))
        tk.Label(info_frame, text=fmt_dt(partner.get('created_at')),
                font=self.font_small_tuple, bg=c['bg_secondary'], fg=c['fg'], anchor="w").grid(row=0, column=1, sticky="ew", padx=(0, 4))
        tk.Label(info_frame, text="Изменён:", font=self.font_small_tuple, bg=c['bg_secondary'], fg=c['fg_muted']).grid(row=0, column=2, sticky="w", padx=(4, 2))
        tk.Label(info_frame, text=fmt_dt(partner.get('updated_at')),
                font=self.font_small_tuple, bg=c['bg_secondary'], fg=c['fg'], anchor="w").grid(row=0, column=3, sticky="ew", padx=(0, 8))

        # Row 1
        tk.Label(info_frame, text="Посл.PV:", font=self.font_small_tuple, bg=c['bg_secondary'], fg=c['fg_muted']).grid(row=1, column=0, sticky="w", padx=(8, 2))
        tk.Label(info_frame, text=fmt_dt(partner.get('last_purchase_at')),
                font=self.font_small_tuple, bg=c['bg_secondary'], fg=c['fg'], anchor="w").grid(row=1, column=1, sticky="ew", padx=(0, 4))
        tk.Label(info_frame, text="Покупок:", font=self.font_small_tuple, bg=c['bg_secondary'], fg=c['fg_muted']).grid(row=1, column=2, sticky="w", padx=(4, 2))
        tk.Label(info_frame, text=str(purchases),
                font=self.font_small_tuple, bg=c['bg_secondary'], fg=c['fg'], anchor="w").grid(row=1, column=3, sticky="ew", padx=(0, 8))

        # Row 2: sum + avg cheque (or block reason if blocked)
        if is_blocked:
            blk_reason = partner.get('block_reason', '')
            tk.Label(info_frame, text="Причина:", font=self.font_small_tuple, bg=c['bg_secondary'], fg=c['fg_muted']).grid(row=2, column=0, sticky="w", padx=(8, 2))
            tk.Label(info_frame, text=blk_reason,
                    font=("Segoe UI", 9), bg=c['bg_secondary'], fg=c['error'], anchor="w", wraplength=200).grid(row=2, column=1, columnspan=3, sticky="ew", padx=(0, 8))
        else:
            tk.Label(info_frame, text="Сумма:", font=self.font_small_tuple, bg=c['bg_secondary'], fg=c['fg_muted']).grid(row=2, column=0, sticky="w", padx=(8, 2))
            tk.Label(info_frame, text=f"{spent:,} ₸".replace(',', ' '),
                    font=self.font_small_tuple, bg=c['bg_secondary'], fg=c['fg'], anchor="w").grid(row=2, column=1, sticky="ew", padx=(0, 4))
            tk.Label(info_frame, text="Сред.чек:", font=self.font_small_tuple, bg=c['bg_secondary'], fg=c['fg_muted']).grid(row=2, column=2, sticky="w", padx=(4, 2))
            tk.Label(info_frame, text=f"{avg_cheque:,} ₸".replace(',', ' '),
                    font=self.font_small_tuple, bg=c['bg_secondary'], fg=c['fg'], anchor="w").grid(row=2, column=3, sticky="ew", padx=(0, 8))

        def warn_once(message):
            messagebox.showwarning("Внимание", message, parent=dialog)

        def save():
            raw = name_var.get().strip()
            if not raw:
                warn_once("Введите имя партнёра в формате: AA12345678 Иванов Иван")
                return
            valid, extracted_id, clean_name, err = self._validate_partner_id_format(raw)
            if not valid:
                warn_once(err or "Неверный формат. Требуется: AA12345678 Иванов Иван")
                return
            
            user_name = self._get_user_device_label()
            new_pid = extracted_id
            normalized_name = f"{new_pid} {clean_name}"
            
            # Check uniqueness only if ID changed
            if new_pid != pid and self.partners_manager.get_partner(new_pid):
                warn_once(f"Партнёр с ID '{new_pid}' уже существует")
                return
            
            success = self.partners_manager.update_partner(
                pid, name=normalized_name, phone=phone_var.get(),
                email=partner.get('email', '') or '',
                notes=notes_text.get("1.0", tk.END).strip(),
                user_name=user_name, new_id=new_pid, is_blocked=self._is_blocked_var.get(),
                full_name=clean_name,
                dob=partner.get('dob'),
                discount=partner.get('discount', 0.5),
            )
            
            if success:
                self.show_toast(f"✅ Изменения сохранены", "sales")
            self.refresh_partners_list()
            dialog.destroy()
        
        def delete():
            if messagebox.askyesno("Удалить", f"Удалить партнёра {partner['name']}?"):
                try:
                    user_name = self._get_user_device_label()
                    self.partners_manager.delete_partner(pid, user_name=user_name)
                except Exception as e:
                    messagebox.showerror("Ошибка удаления", str(e))
                    return
                self.refresh_partners_list()
                dialog.destroy()
        
        # Buttons in pinned zone (33% each for uniform look)
        # Buttons in pinned zone (50% each for clean look)
        btn_save = self._add_dialog_button(dialog, "💾 Сохранить", save, 'primary', use_grid=True, column=0)
        
        btn_delete = self._add_dialog_button(dialog, "🗑 Удалить", delete, 'danger', use_grid=True, column=1)
        self._add_dialog_button(dialog, get_text('cancel', self.lang), dialog.destroy, 'neutral', use_grid=True, column=1)
        
        # Permission gating for edit/delete
        if not self.has_permission('partner_edit'):
            btn_save.config(state='disabled')
        if not self.has_permission('partner_delete') and self.current_role not in ('admin', 'superadmin'):
            btn_delete.config(state='disabled')

    def create_partners_history_subtab(self, parent):
        """Create a subtab showing the edit history of partners."""
        c = self.colors
        
        main_frame = tk.Frame(parent, bg=c['bg'])
        main_frame.pack(fill="both", expand=True, padx=5, pady=5)
        main_frame.grid_rowconfigure(1, weight=1)
        main_frame.grid_columnconfigure(0, weight=1)
        
        # Top controls
        top_frame = tk.Frame(main_frame, bg=c['bg'])
        top_frame.grid(row=0, column=0, sticky="ew", pady=(0, 5))
        
        tk.Label(top_frame, text="🔍 Поиск:", bg=c['bg']).pack(side="left", padx=5)
        self.partners_history_search = tk.Entry(top_frame, font=self.font_normal_tuple, width=25)
        self.partners_history_search.pack(side="left", padx=5)
        self.partners_history_search.bind('<KeyRelease>', lambda e: self.refresh_partners_history())
        self.partners_history_search.bind('<Down>', lambda e: (self.partners_history_tree.focus_set(), self.partners_history_tree.selection_set(self.partners_history_tree.get_children()[0]) if self.partners_history_tree.get_children() else None))

        # Table area: fills all remaining space; tree stretches with the window
        tree_container = tk.Frame(main_frame, bg=c['bg'])
        tree_container.grid(row=1, column=0, sticky="nsew")
        tree_container.grid_rowconfigure(0, weight=1)
        tree_container.grid_columnconfigure(0, weight=1)

        columns = ('date', 'partner_id', 'created_at', 'user', 'action', 'details')
        self.partners_history_tree = ttk.Treeview(tree_container, columns=columns, show='headings')
        
        headers = [get_text('receipt_date', self.lang), 'ID', get_text('creation_date', self.lang), 
                  get_text('cashier', self.lang), get_text('status', self.lang), get_text('receipt_details', self.lang)]
        widths = [130, 100, 130, 110, 90, 400]
        
        for col, hdr, w in zip(columns, headers, widths):
            self.partners_history_tree.heading(col, text=hdr)
            self.partners_history_tree.column(col, width=w, minwidth=60, anchor='w', stretch=(col == columns[-1]))
            
        self.setup_treeview_sorting(self.partners_history_tree, columns, numeric_cols=['partner_id'])
        self.setup_universal_navigation(self.partners_history_tree, lambda: self.show_partner_history_details(None)) # Added
        self.partners_history_tree.bind('<Button-1>', self.prevent_treeview_resize)
        
        # Both scrollbars are always available: vertical for long lists,
        # horizontal for the wide columns (Windows fixes).
        vsb = tk.Scrollbar(tree_container, orient="vertical", command=self.partners_history_tree.yview)
        hsb = tk.Scrollbar(tree_container, orient="horizontal", command=self.partners_history_tree.xview)
        self.partners_history_tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        
        self.partners_history_tree.grid(row=0, column=0, sticky="nsew", padx=(5, 0))
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew", padx=(5, 0), pady=(2, 0))
        
        self.bind_mousewheel(self.partners_history_tree)
        # Wheel + drag-pan over the container's free zones scrolls the tree
        self.enable_scroll_target(tree_container, self.partners_history_tree)
        
        self.partners_tree.tag_configure('blocked', foreground='#c62828', font=self.font_bold_tuple)
        
        self.partners_history_tree.bind('<Double-1>', self.show_partner_history_details)

    def refresh_partners_history(self):
        """Refresh the partners history view."""
        if not hasattr(self, 'partners_history_tree'):
            return
            
        for item in self.partners_history_tree.get_children():
            self.partners_history_tree.delete(item)
            
        search_query = self.partners_history_search.get().strip().lower()
        history = self.partners_manager.get_partner_history(search_query)
        
        for record in history:
            dt = record['timestamp'][:16].replace('T', ' ')
            created_at = record.get('partner_created_at', '')
            if created_at:
                created_at = created_at[:10]
            
            action = record['action']
            if action == 'Created': action = get_text('action_created', self.lang)
            elif action == 'Updated': action = get_text('action_updated', self.lang)

            # Format details for quick view
            details_str = ""
            try:
                import json
                details = json.loads(record['details']) if isinstance(record['details'], str) else record['details']
                if isinstance(details, dict):
                    parts = []
                    # Field labels mapping
                    lbls = {'name': 'ФИО', 'phone': 'Телефон', 'email': 'Email', 'notes': 'Заметка'}
                    for k, v in details.items():
                        field = lbls.get(k, k)
                        if isinstance(v, dict) and 'old' in v:
                            parts.append(f"{field}: {v['old']} -> {v['new']}")
                        else:
                            parts.append(f"{field}: {v}")
                    details_str = " | ".join(parts)
                else:
                    details_str = str(details)
            except:
                details_str = str(record['details'])

            self.partners_history_tree.insert('', 'end', values=(
                dt, record['partner_id'], created_at, record['user_name'], 
                action, details_str
            ), tags=(str(record['id']),))

    def show_partner_history_details(self, event=None):
        """Show a clear 'Before → After' diff dialog for a partner history record."""
        sel = self.partners_history_tree.selection()
        if not sel: return
        
        try:
            item_data = self.partners_history_tree.item(sel[0])
            item_id = item_data['tags'][0]
            timestamp = item_data['values'][0]
            partner_id = item_data['values'][1]
            action_text = item_data['values'][4]
            
            import json
            details = None
            action = ''
            try:
                record = self.partners_manager.get_partner_history_record(item_id)
                if record:
                    details = json.loads(record['details']) if isinstance(record['details'], str) else record['details']
                    action = record.get('action', '')
            except Exception:
                pass
            if details is None:
                details = item_data['values'][5]
        except Exception as e:
            self.log_message(f"Error opening history details: {e}", "error")
            return
            
        dialog = self.create_modal_dialog(f"Детали изменений - {partner_id}", 900, 620, scrollable=True)
        main = dialog.container
        c = self.colors
        
        # Header: partner ID + timestamp + action badge
        header = tk.Frame(main, bg=c['bg'], pady=6)
        header.pack(fill="x", padx=16)
        
        tk.Label(header, text=f"👤 {partner_id}", font=(self.font_family, 16, "bold"),
                 bg=c['bg'], fg=c['fg']).pack(side="left")
        
        badge_map = {
            'Created': (c['success_bg'], c['success'], '➕ Создан'),
            'Updated': (c['warning_bg'], c['warning'], '✏️ Изменён'),
            'Deleted': (c['error_bg'], c['error'], '🗑 Удалён'),
        }
        abg, afg, atxt = badge_map.get(action, (c['bg_secondary'], c['fg_muted'], action_text))
        badge = tk.Label(header, text=f" {atxt} ", font=self.font_small_bold_tuple,
                         bg=abg, fg=afg, padx=8, pady=2)
        badge.pack(side="right", padx=(8, 0), pady=(4, 0))
        tk.Label(header, text=timestamp, font=self.font_small_tuple,
                 bg=c['bg'], fg=c['fg_muted']).pack(side="right", pady=(6, 0))
        
        ttk.Separator(main, orient='horizontal').pack(fill="x", padx=16, pady=8)
        
        if isinstance(details, dict):
            self._render_partner_history_diff(main, details, action, c)
        else:
            # Fallback for plain text details
            msg_frame = tk.Frame(main, bg=c['bg_secondary'], padx=20, pady=20)
            msg_frame.pack(fill="both", expand=True, padx=20, pady=10)
            tk.Label(msg_frame, text=str(details), font=self.font_normal_tuple,
                     bg=c['bg_secondary'], wraplength=650).pack()
        
        self._add_dialog_button(dialog, "Закрыть", dialog.destroy, 'neutral', 'right')

    def _render_partner_history_diff(self, parent, details, action, c=None):
        """Render a readable 'Before → After' diff table for partner history."""
        c = c or self.colors
        
        lbls = {
            'name': 'Имя',
            'full_name': 'ФИО',
            'phone': 'Телефон',
            'email': 'Email',
            'notes': 'Заметка',
            'is_blocked': 'Статус',
            'block_reason': 'Причина блокировки',
            'blocked_by': 'Заблокировал',
            'blocked_at': 'Дата блокировки',
            'dob': 'Дата рождения',
            'discount': 'Скидка %',
        }
        
        def disp(field, val):
            if val is None:
                return ''
            val = str(val)
            if field == 'is_blocked':
                return '🔒 Заблокирован' if val == '1' else ('Активен' if val != '' else '')
            if field == 'discount' and val not in ('', '0', '0.0'):
                return f"{val}%"
            return val
        
        grid = tk.Frame(parent, bg=c['bg'])
        grid.pack(fill="x", padx=16, pady=(0, 8))
        grid.grid_columnconfigure(0, weight=0, minsize=150)
        grid.grid_columnconfigure(1, weight=1, uniform='half')
        grid.grid_columnconfigure(2, weight=0)
        grid.grid_columnconfigure(3, weight=1, uniform='half')
        
        # Column headers
        hdr_font = self.font_small_bold_tuple
        tk.Label(grid, text="Поле", font=hdr_font, bg=c['bg'], fg=c['fg_secondary'],
                 anchor="w").grid(row=0, column=0, sticky="ew", padx=(4, 8), pady=(0, 6))
        tk.Label(grid, text="Было (до)", font=hdr_font, bg=c['bg'], fg=c['error'],
                 anchor="w").grid(row=0, column=1, sticky="ew", pady=(0, 6))
        tk.Label(grid, text=" ", font=hdr_font, bg=c['bg']).grid(row=0, column=2, pady=(0, 6))
        tk.Label(grid, text="Стало (после)", font=hdr_font, bg=c['bg'], fg=c['success'],
                 anchor="w").grid(row=0, column=3, sticky="ew", pady=(0, 6))
        
        def cell(row, col, text, bg, fg, bold=False):
            font = self.font_bold_tuple if bold else self.font_normal_tuple
            lbl = tk.Label(grid, text=text, font=font, bg=bg, fg=fg,
                           anchor="w", justify="left", padx=8, pady=6, wraplength=330)
            lbl.grid(row=row, column=col, sticky="ew", padx=(0, 4), pady=2)
            return lbl
        
        def arrow(row, color=None):
            tk.Label(grid, text="→", font=self.font_bold_tuple, bg=c['bg'],
                     fg=color or c['fg_muted']).grid(row=row, column=2)
        
        r = 1
        for field, vals in details.items():
            fname = lbls.get(field, field)
            if isinstance(vals, dict) and 'old' in vals:
                old_txt = disp(field, vals.get('old', ''))
                new_txt = disp(field, vals.get('new', ''))
            elif action == 'Deleted':
                old_txt = disp(field, vals)
                new_txt = ''
            else:
                old_txt = ''
                new_txt = disp(field, vals)
            
            tk.Label(grid, text=fname, font=self.font_normal_tuple, bg=c['bg'],
                     fg=c['fg_secondary'], anchor="w").grid(row=r, column=0, sticky="ew",
                                                            padx=(4, 8), pady=2)
            
            if old_txt and not new_txt:
                # Deleted value — red on the left
                cell(r, 1, old_txt, c['error_bg'], c['error'], bold=True)
                arrow(r)
                cell(r, 3, "—", c['bg_secondary'], c['fg_muted'])
            elif new_txt and not old_txt:
                # Added value — green on the right
                cell(r, 1, "—", c['bg_secondary'], c['fg_muted'])
                arrow(r, c['success'])
                cell(r, 3, new_txt, c['success_bg'], c['success'], bold=True)
            elif old_txt == new_txt:
                # Unchanged — neutral
                cell(r, 1, old_txt, c['bg_secondary'], c['fg_muted'])
                arrow(r)
                cell(r, 3, new_txt, c['bg_secondary'], c['fg_muted'])
            else:
                # Changed — old red, new green
                cell(r, 1, old_txt, c['error_bg'], c['error'], bold=True)
                arrow(r, c['warning'])
                cell(r, 3, new_txt, c['success_bg'], c['success'], bold=True)
            r += 1

    def filter_partners_list(self, event=None):
        """Filter partners list based on search."""
        query = self.partners_search_var.get().lower()
        self.refresh_partners_list(query)

    def refresh_partners_list(self, query=""):
        for item in self.partners_tree.get_children():
            self.partners_tree.delete(item)
        
        all_partners = self.partners_manager.get_all_partners()
        
        # Filter first to ensure dynamic numbering works for visible items
        filtered_partners = []
        query = query.lower().strip()
        
        if query:
            for p in all_partners:
                if (query in str(p.get('id', '')).lower() or
                    query in str(p.get('name', '')).lower() or
                    query in str(p.get('phone', '')).lower() or
                    query in str(p.get('notes', '')).lower()):
                    filtered_partners.append(p)
        else:
            filtered_partners = all_partners

        for i, partner in enumerate(filtered_partners, 1):
            pid = str(partner.get('id', ''))
            display_id = pid if (len(pid) <= 20 and not self._is_uuid(pid)) else ''
            is_blocked = partner.get('is_blocked')
            tags = ('blocked',) if is_blocked else ()
            name_display = partner.get('full_name') or partner.get('name', '')
            if pid and name_display.startswith(pid):
                name_display = name_display[len(pid):].strip()
            blk_by = partner.get('blocked_by', '')
            blk_reason = partner.get('block_reason', '')
            if is_blocked:
                name_display = f"🔒 {name_display}"
            
            status_text = "🟢 Активен" if not is_blocked else "🔒 Заблокирован"
            self.partners_tree.insert('', 'end', values=(
                i, display_id, name_display, partner.get('phone', ''),
                status_text,
                partner.get('total_purchases', 0), self.format_amount(partner.get('total_spent', 0))
            ), tags=(str(partner['id']),) + tags)
        
        # Update stats
        total_p = len(filtered_partners)
        total_spent = sum(p.get('total_spent', 0) for p in filtered_partners)
        self.partners_stats_label.config(text=f"Найдено партнеров: {total_p} | Общая сумма покупок: {self.format_amount(total_spent)}")

    @staticmethod
    def _is_uuid(text):
        """Check if text looks like a UUID."""
        import re
        return bool(re.match(r'^[0-9a-f]{8}-[0-9a-f]{4}-', str(text), re.IGNORECASE)) or len(str(text)) == 36
