# -*- coding: utf-8 -*-
"""
PVM.core - Arrival Tab Mixin
===============================
Warehouse: invoices, goods, purchases, writeoffs, goods history, cancelled.
"""

import re
import time
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog, filedialog
from datetime import datetime, date

from ui_lang import get_text
from ui_dialogs import AutocompleteEntry, AutoScrollbar


class ArrivalTabMixin:
    """Arrival/warehouse tab methods for GreenLeafApp."""

    def create_arrival_tab(self):
        """Create new arrival/goods management tab with invoice support."""
        c = self.colors
        
        # Use notebook for sub-tabs
        arrival_notebook = ttk.Notebook(self.arrival_frame)
        arrival_notebook.pack(fill="both", expand=True, padx=5, pady=5)
        
        # Initialize frames
        goods_frame = None
        writeoffs_frame = None
        
        # === SUB-TAB 1: CREATE INVOICE ===
        invoice_frame = ttk.Frame(arrival_notebook)
        arrival_notebook.add(invoice_frame, text=f"  📝 {get_text('create_invoice', self.lang)}  ")
        self.create_invoice_subtab(invoice_frame)
        
        # === SUB-TAB 2: GOODS LIST ===
        if self.has_permission('arrival_edit'):
            goods_frame = ttk.Frame(arrival_notebook)
            arrival_notebook.add(goods_frame, text=f"  📦 {get_text('goods_list', self.lang)}  ")
            self.create_goods_subtab(goods_frame)
            
        # === SUB-TAB 3: WRITEOFFS (Списание) ===
        writeoffs_frame = None
        if self.has_permission('writeoff_view'):
            writeoffs_frame = ttk.Frame(arrival_notebook)
            arrival_notebook.add(writeoffs_frame, text=f"  🗑 {get_text('writeoff', self.lang)}  ")
            self.create_writeoff_subtab(writeoffs_frame)
        
        # === SUB-TAB 4: INVENTORY AUDIT (Ревизия) ===
        inventory_frame = None
        if self.has_permission('inventory_view'):
            inventory_frame = ttk.Frame(arrival_notebook)
            arrival_notebook.add(inventory_frame, text=f"  📋 {get_text('inventory_audit', self.lang)}  ")
            self.create_inventory_subtab(inventory_frame)
        
        # Save reference for tab events
        self.arrival_notebook = arrival_notebook
        self.invoice_frame = invoice_frame
        self.goods_frame = goods_frame
        self.writeoffs_frame = writeoffs_frame
        self.inventory_frame = inventory_frame
        
        # Inner notebook tab changes
        def on_arrival_tab_changed(event):
            inner_tab_id = self.arrival_notebook.select()
            if not inner_tab_id: return
            inner_tab_widget = self.arrival_notebook.nametowidget(inner_tab_id)
            
            if hasattr(self, 'invoice_frame') and inner_tab_widget == self.invoice_frame:
                self.master.after(50, self._focus_search_field)
            elif hasattr(self, 'goods_frame') and inner_tab_widget == self.goods_frame:
                self.refresh_goods_list()
                self.master.after(50, self._focus_search_field)
            elif hasattr(self, 'writeoffs_frame') and inner_tab_widget == self.writeoffs_frame:
                self.refresh_writeoffs_history()
            elif hasattr(self, 'inventory_frame') and inner_tab_widget == self.inventory_frame:
                self.refresh_inventory_view()
            elif hasattr(self, 'purchases_frame') and inner_tab_widget == self.purchases_frame:
                self.refresh_purchases_history()
                self.master.after(50, self._focus_search_field)
            elif hasattr(self, 'cancelled_frame') and inner_tab_widget == self.cancelled_frame:
                self.refresh_cancelled_items()
                self.master.after(50, self._focus_search_field)

        self.arrival_notebook.bind('<<NotebookTabChanged>>', on_arrival_tab_changed)

    def create_history_tab(self):
        """Create history tabs for purchases and cancellations."""
        c = self.colors
        
        history_notebook = ttk.Notebook(self.history_frame)
        history_notebook.pack(fill="both", expand=True, padx=5, pady=5)
        
        # === SUB-TAB 1: PURCHASES HISTORY ===
        purchases_frame = ttk.Frame(history_notebook)
        history_notebook.add(purchases_frame, text=f"  📜 {get_text('arrival_history_tab', self.lang)}  ")
        self.create_purchases_history_subtab(purchases_frame)
        
        # === SUB-TAB 2: CANCELLED ITEMS (Касса) ===
        cancelled_frame = None
        if self.has_permission('cancellations_view'):
            cancelled_frame = ttk.Frame(history_notebook)
            history_notebook.add(cancelled_frame, text=f"  🛒 {get_text('cancellations_tab', self.lang)}  ")
            self.create_cancelled_items_subtab(cancelled_frame)
            
        # Save reference for tab events
        self.history_notebook = history_notebook
        self.purchases_frame = purchases_frame
        self.cancelled_frame = cancelled_frame
        
        def on_history_tab_changed(event):
            inner_tab_id = self.history_notebook.select()
            if not inner_tab_id: return
            inner_tab_widget = self.history_notebook.nametowidget(inner_tab_id)
            
            if hasattr(self, 'purchases_frame') and inner_tab_widget == self.purchases_frame:
                self.refresh_purchases_history()
            elif hasattr(self, 'cancelled_frame') and inner_tab_widget == self.cancelled_frame:
                self.refresh_cancelled_items()
                
        self.history_notebook.bind('<<NotebookTabChanged>>', on_history_tab_changed)

    def create_cancelled_items_subtab(self, parent):
        """Create a subtab showing tracked cancelled items from POS."""
        c = self.colors
        
        main_frame = tk.Frame(parent, bg=c['bg'])
        main_frame.pack(fill="both", expand=True, padx=5, pady=5)
        
        # Top controls row
        top_frame = tk.Frame(main_frame, bg=c['bg'])
        top_frame.pack(fill="x", padx=5, pady=5)
        
        self.cancelled_search = self._build_search_bar(top_frame, c['bg'])
        self.cancelled_search.bind('<KeyRelease>', lambda e: self.refresh_cancelled_items())
        # Down arrow from search moves focus to tree
        self.cancelled_search.bind('<Down>', lambda e: self._cancelled_focus_first())
        
        # Action type filter
        tk.Label(top_frame, text="Тип:", bg=c['bg'], fg=c['fg'], font=self.font_small_tuple).pack(side="left", padx=(15, 5))
        self.cancelled_action_var = tk.StringVar(value="Все")
        action_combo = ttk.Combobox(top_frame, textvariable=self.cancelled_action_var, 
                                     values=["Все", "Удален из корзины", "Уменьшено кол-во", "Корзина очищена"],
                                     state='readonly', width=18, font=self.font_small_tuple)
        action_combo.pack(side="left", padx=5)
        action_combo.bind('<<ComboboxSelected>>', lambda e: self.refresh_cancelled_items())
        
        # Date range
        today_str = date.today().strftime("%d.%m.%Y")
        self.cancelled_range_var = tk.StringVar(value=f"{today_str} - {today_str}")
        self.cancelled_range_entry = tk.Entry(top_frame, textvariable=self.cancelled_range_var,
                                              font=self.font_small_tuple, width=23)
        self.cancelled_range_entry.pack(side="left", padx=5)
        self.cancelled_range_entry.bind('<Button-1>', lambda e: self.show_date_range_picker(
            range_var=self.cancelled_range_var, callback=self.refresh_cancelled_items))
        self.cancelled_range_entry.bind('<KeyRelease>', lambda e: self.refresh_cancelled_items())


        # Treeview with full-width stretched columns
        tree_frame = tk.Frame(main_frame, bg=c['bg'])
        tree_frame.pack(fill="both", expand=True, padx=5, pady=5)
        
        columns = ('date', 'code', 'name', 'quantity', 'action', 'cashier')
        self.cancelled_tree = ttk.Treeview(tree_frame, columns=columns, show='headings', height=20)
        
        headers = ['Дата / Время', 'Код товара', 'Название', 'Кол-во', 'Действие', 'Кассир']
        widths = [140, 100, 250, 80, 170, 120]
        stretches = [False, False, True, False, False, False]
        
        for col, hdr, w, st in zip(columns, headers, widths, stretches):
            self.cancelled_tree.heading(col, text=hdr)
            self.cancelled_tree.column(col, width=w, minwidth=w, anchor='w', stretch=st)
            
        scrollbar = AutoScrollbar(tree_frame, orient="vertical", command=self.cancelled_tree.yview)
        self.cancelled_tree.configure(yscrollcommand=scrollbar.set)
        self.cancelled_tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        
        # Prevent column resize
        self.cancelled_tree.bind('<Button-1>', self.prevent_treeview_resize)
        
        # Keyboard navigation
        self.setup_universal_navigation(self.cancelled_tree)
        self.cancelled_tree.bind('<Return>', lambda e: None)
        self.cancelled_tree.bind('<Escape>', lambda e: self.cancelled_search.focus_set())
        
        self.master.after(100, self.refresh_cancelled_items)
    
    def _cancelled_focus_first(self):
        """Move focus to first item in cancelled tree."""
        if hasattr(self, 'cancelled_tree') and self.cancelled_tree.get_children():
            self.cancelled_tree.focus_set()
            first = self.cancelled_tree.get_children()[0]
            self.cancelled_tree.selection_set(first)
            self.cancelled_tree.focus(first)
            self.cancelled_tree.see(first)
        return "break"

    def refresh_cancelled_items(self):
        """Refresh cancelled items view with filters."""
        if not hasattr(self, 'cancelled_tree'): return
        
        for item in self.cancelled_tree.get_children():
            self.cancelled_tree.delete(item)
            
        search_query = self.cancelled_search.get().strip()
        action_filter = getattr(self, 'cancelled_action_var', None)
        action_filter = action_filter.get() if action_filter else "Все"
        
        # Parse date filters
        date_from = None
        date_to = None
        try:
            parts = [p.strip() for p in self.cancelled_range_var.get().split('-')]
            if parts[0]:
                dp = parts[0].split('.')
                d, m, y = dp[0], dp[1], '20' + dp[2] if len(dp[2]) == 2 else dp[2]
                date_from = f"{y}-{m.zfill(2)}-{d.zfill(2)}T00:00:00"
            if len(parts) > 1 and parts[1]:
                dp = parts[1].split('.')
                d, m, y = dp[0], dp[1], '20' + dp[2] if len(dp[2]) == 2 else dp[2]
                date_to = f"{y}-{m.zfill(2)}-{d.zfill(2)}T23:59:59"
        except:
            pass
        
        items, _ = self._db_manager.get_cancelled_items(
            limit=500, search_query=search_query,
            action_filter=action_filter if action_filter != "Все" else "",
            date_from=date_from, date_to=date_to
        )
        
        for row in items:
            dt = row.get('timestamp', '')
            if dt:
                try:
                    dt = datetime.fromisoformat(dt).strftime("%d.%m.%y %H:%M")
                except:
                    pass
                    
            user_label = row.get('cashier', '')

            self.cancelled_tree.insert('', 'end', values=(
                dt,
                row.get('good_code', ''),
                row.get('name', ''),
                f"{row.get('quantity', 0):g}",
                row.get('action', ''),
                user_label
            ))

    def create_invoice_subtab(self, parent):
        """Create invoice creation subtab."""
        c = self.colors
        
        main_frame = tk.Frame(parent, bg=c['bg'])
        main_frame.pack(fill="both", expand=True, padx=5, pady=5)
        
        # TOP - Invoice header
        header_frame = tk.Frame(main_frame, bg=c['frame_bg'])
        header_frame.pack(fill="x", padx=5, pady=2)
        
        header_grid = tk.Frame(header_frame, bg=c['frame_bg'])
        header_grid.pack(fill="x", padx=10, pady=6)
        header_grid.grid_columnconfigure(1, weight=1)
        header_grid.grid_columnconfigure(3, weight=2)
        
        # Invoice number
        tk.Label(header_grid, text=f"{get_text('invoice_number', self.lang)}:", 
                font=self.font_normal_tuple, bg=c['frame_bg']).grid(row=0, column=0, sticky="w", pady=1)
        self.invoice_number_var = tk.StringVar(value=self._next_invoice_number())
        tk.Entry(header_grid, textvariable=self.invoice_number_var, font=self.font_normal_tuple,
                width=25).grid(row=0, column=1, sticky="ew", pady=1, padx=10)

        # Supplier
        tk.Label(header_grid, text=f"{get_text('supplier', self.lang)}:",
                font=self.font_normal_tuple, bg=c['frame_bg']).grid(row=0, column=2, sticky="w", pady=1, padx=(20, 0))
        self.invoice_supplier_var = tk.StringVar()
        tk.Entry(header_grid, textvariable=self.invoice_supplier_var, font=self.font_normal_tuple,
                width=30).grid(row=0, column=3, sticky="ew", pady=1, padx=10)

        # Notes (full-width row below, always visible)
        tk.Label(header_grid, text=f"{get_text('purchase_notes', self.lang)}:", 
                font=self.font_normal_tuple, bg=c['frame_bg']).grid(row=1, column=0, sticky="w", pady=1)
        self.invoice_notes_var = tk.StringVar()
        tk.Entry(header_grid, textvariable=self.invoice_notes_var, font=self.font_normal_tuple,
                width=25).grid(row=1, column=1, columnspan=3, sticky="ew", pady=1, padx=10)
        
        # MIDDLE - Add items to invoice
        add_frame = tk.Frame(main_frame, bg=c['frame_bg'])
        add_frame.pack(fill="x", padx=5, pady=2)

        # Search for a good
        search_frame = tk.Frame(add_frame, bg=c['frame_bg'])
        search_frame.pack(fill='x', pady=5, padx=5)

        def fetch_arrival_goods(query):
            results = self.goods_manager.search_goods(query)
            res_list = []
            for g in results:
                res_list.append(f"{g['code']} | {g['name']} | {self.format_amount(g['sale_price'])}₸")
            
            # Only enable the 'Create good' button if no exact match is found
            if not results:
                self.arrival_add_new_btn.config(state='normal')
            else:
                self.arrival_add_new_btn.config(state='disabled')
            
            # Logic: If item not found but query looks like barcode, detect fast input
            if not results and query and query.isdigit() and len(query) >= 8:
                # Get time since last key
                if hasattr(self, '_last_arrival_key_time'):
                    elapsed = time.time() - self._last_arrival_key_time
                    if elapsed < 0.05: # Fast input (scanner)
                        # Open dialog after a short delay to ensure entry is finished
                        self.master.after(100, lambda: self.add_new_good_from_arrival() if not self._active_modal else None)
            
            self._last_arrival_key_time = time.time()
                
            return res_list
            
        def select_arrival_good(text):
            if '|' in text:
                code = text.split('|')[0].strip()
                _, good = self.goods_manager.get_good(code)
                if good:
                    self.add_good_to_arrival_invoice(good)
            else:
                query = text.strip().lower()
                if not query:
                    return
                # Try code lookup
                _, good = self.goods_manager.get_good(query.upper())
                if good:
                    self.add_good_to_arrival_invoice(good)
                    return
                # Try barcode lookup
                if query.isdigit():
                    _, good = self.goods_manager.get_good_by_barcode(query)
                    if good:
                        self.add_good_to_arrival_invoice(good)
                        return
                # Try partial name match
                try:
                    with self._db_manager.get_connection() as conn:
                        cursor = conn.cursor()
                        cursor.execute('SELECT * FROM goods WHERE LOWER(name) LIKE ? LIMIT 1', (f'%{query}%',))
                        row = cursor.fetchone()
                        if row:
                            self.add_good_to_arrival_invoice(dict(row))
                            return
                except Exception:
                    pass
                # Nothing found — open create dialog
                self.add_new_good_from_arrival()
        
        self.arrival_add_new_btn = self._btn(search_frame, text="Создать товар", command=self.add_new_good_from_arrival, style='accent', state='disabled')
        self.arrival_add_new_btn.pack(side='right', padx=5)

        # MIDDLE - Invoice items table
        items_frame = tk.Frame(main_frame, bg=c['frame_bg'])
        items_frame.pack(fill="both", expand=True, padx=5, pady=2)
        items_frame.pack_propagate(False)
        
        columns = ('barcode', 'code', 'name', 'pv', 'qty', 'purchase', 'sale', 'sum')
        self.invoice_items_tree = ttk.Treeview(items_frame, columns=columns, show='headings', height=12)
        
        headers = ['Штрих-код', get_text('code', self.lang), get_text('name', self.lang), 'ПВ',
                  get_text('quantity', self.lang), get_text('purchase_price', self.lang),
                  get_text('sale_price', self.lang), get_text('sum', self.lang)]
        widths = [120, 80, 200, 50, 50, 80, 80, 90]
        
        for col, hdr, w in zip(columns, headers, widths):
            self.invoice_items_tree.heading(col, text=hdr)
            self.invoice_items_tree.column(col, width=w, anchor='center' if col != 'name' else 'w')
            
        self.setup_treeview_sorting(self.invoice_items_tree, columns, numeric_cols=['pv', 'qty', 'purchase', 'sale', 'sum'])
        self.setup_universal_navigation(self.invoice_items_tree, lambda: self.edit_invoice_item(None))
        self.invoice_items_tree.bind('<Button-1>', self.prevent_treeview_resize)
        
        tree_scrollbar = AutoScrollbar(items_frame, orient="vertical", command=self.invoice_items_tree.yview)
        self.invoice_items_tree.configure(yscrollcommand=tree_scrollbar.set)
        self.invoice_items_tree.pack(side="left", fill="both", expand=True)
        tree_scrollbar.pack(side="right", fill="y")
        
        self.invoice_items_tree.bind('<Delete>', lambda e: self.remove_invoice_item())
        self.invoice_items_tree.bind('<BackSpace>', lambda e: self.remove_invoice_item())
        self.invoice_items_tree.bind('<Double-1>', self.edit_invoice_item)
        self.invoice_items_tree.bind('<Button-3>', self.show_invoice_item_menu)
        self.invoice_items_tree.bind('<Key-Menu>', lambda e: self.show_invoice_item_menu(None))

        # Now create search entry (it needs self.invoice_items_tree)
        self.arrival_search_entry = self._build_search_bar(
            search_frame, c['frame_bg'], entry_cls=AutocompleteEntry,
            get_results_callback=fetch_arrival_goods, on_select_callback=select_arrival_good,
            list_font=self.font_normal_tuple, target_tree=self.invoice_items_tree
        )
        
        # BOTTOM - Total and finalize (outside scrollable area, always visible)
        bottom_frame = tk.Frame(main_frame, bg=c['bg'])
        bottom_frame.pack(side="bottom", fill="x", padx=5, pady=5)

        # Action buttons anchored LEFT so they are always visible
        btn_frame = tk.Frame(bottom_frame, bg=c['bg'])
        btn_frame.pack(side="left", fill="y")

        btn_finalize = self._btn(btn_frame, text=f"✅ {get_text('finalize_invoice', self.lang)}", command=self.finalize_invoice, style='success')
        btn_finalize.pack(side="left", padx=5)

        # Permission gating
        if not self.has_permission('arrival_create'):
            btn_finalize.config(state='disabled', bg=c['bg_tertiary'])

        # Info anchored RIGHT
        self.invoice_items_count_label = tk.Label(bottom_frame,
                                                  text=f"{get_text('items_count', self.lang)}: 0",
                                                  font=self.font_normal_tuple, bg=c['bg'])
        self.invoice_items_count_label.pack(side="right", padx=20)

        self.invoice_total_label = tk.Label(bottom_frame,
                                            text=f"{get_text('purchase_total', self.lang)}: 0,00",
                                            font=self.font_bold_tuple, bg=c['bg'])
        self.invoice_total_label.pack(side="right", padx=20)

                 
        # Auto-focus search field (the invoice subtab's own search entry —
        # goods_search only exists on the goods subtab and may not be built)
        if hasattr(self, 'arrival_search_entry'):
            self.master.after(100, self.arrival_search_entry.focus_set)

    def remove_invoice_item(self):
        """Remove selected item from invoice."""
        sel = self.invoice_items_tree.selection()
        if sel:
            idx = self.invoice_items_tree.index(sel[0])
            del self.current_invoice_items[idx]
            self.invoice_items_tree.delete(sel[0])
            self.update_invoice_totals()

    def edit_invoice_item(self, event=None):
        """Edit selected invoice item on double-click."""
        sel = self.invoice_items_tree.selection()
        if not sel:
            return
        idx = self.invoice_items_tree.index(sel[0])
        if idx < 0 or idx >= len(self.current_invoice_items):
            return
        item = self.current_invoice_items[idx]
        good = self.goods_manager.get_good(item['code'])[1] if self.goods_manager.get_good(item['code'])[1] else item
        self.add_good_to_arrival_invoice(good, edit_idx=idx)

    def show_invoice_item_menu(self, event):
        """Show right-click menu for invoice items."""
        sel = self.invoice_items_tree.selection()
        if not sel:
            return
        idx = self.invoice_items_tree.index(sel[0])
        menu = tk.Menu(self.master, tearoff=0, bg=self.colors['bg'], fg=self.colors['fg'])
        menu.add_command(label="✏️ Редактировать", command=lambda: self.edit_invoice_item())
        menu.add_command(label="🗑️ Удалить", command=self.remove_invoice_item)
        if event:
            menu.post(event.x_root, event.y_root)
        else:
            x, y = self.invoice_items_tree.winfo_pointerxy()
            menu.post(x, y)

    def update_invoice_totals(self):
        """Update invoice totals."""
        total = sum(i['quantity'] * i['purchase_price'] for i in self.current_invoice_items)
        items_count = sum(i['quantity'] for i in self.current_invoice_items)
        self.invoice_total_label.config(text=f"{get_text('purchase_total', self.lang)}: {self.format_amount(total)}")
        self.invoice_items_count_label.config(text=f"{get_text('items_count', self.lang)}: {items_count}")

    def _next_invoice_number(self):
        """Generate the next free invoice number for today: INV-YYYYMMDD-NNN."""
        today = datetime.now().strftime('%Y%m%d')
        try:
            with self._db_manager.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT invoice_number FROM purchases WHERE invoice_number LIKE ?",
                               (f"INV-{today}-%",))
                nums = {row['invoice_number'] for row in cursor.fetchall()}
        except Exception:
            nums = set()
        n = 1
        while f"INV-{today}-{n:03d}" in nums:
            n += 1
        return f"INV-{today}-{n:03d}"

    def finalize_invoice(self):
        """Finalize and save purchase invoice."""
        if not self.current_invoice_items:
            messagebox.showwarning(get_text('warning', self.lang), get_text('add_items_to_invoice', self.lang))
            return
        
        invoice_number = self.invoice_number_var.get().strip()
        supplier = self.invoice_supplier_var.get().strip()
        notes = self.invoice_notes_var.get().strip()
        total = sum(i['quantity'] * i['purchase_price'] for i in self.current_invoice_items)
        
        # Duplicate invoice number check (INV-...-001 stays the same all day otherwise)
        try:
            with self._db_manager.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT id FROM purchases WHERE invoice_number = ?", (invoice_number,))
                if cursor.fetchone():
                    suggestion = self._next_invoice_number()
                    if messagebox.askyesno(
                            "Дубликат накладной",
                            f"Накладная с номером '{invoice_number}' уже существует.\n\n"
                            f"Использовать следующий номер '{suggestion}'?"):
                        invoice_number = suggestion
                        self.invoice_number_var.set(suggestion)
                    else:
                        return
        except Exception:
            pass
        
        try:
            # C4: atomic purchase — invoice, items and stock updates commit
            # (or roll back) as ONE transaction.
            try:
                purchase = self.inventory_ops.purchase(
                    invoice_number, supplier, self.current_invoice_items, total, notes,
                    cashier_user=self._get_user_device_label()
                )
            except ValueError as e:
                messagebox.showerror(get_text('error', self.lang), str(e))
                return
            
            if not purchase:
                messagebox.showerror(get_text('error', self.lang), "Ошибка создания накладной. Проверьте данные.")
                return
            
            self.show_toast(f"✅ {purchase['id']} {get_text('saved', self.lang)} ({purchase['items_count']})", "inventory")
            
            self.clear_invoice()
            self.refresh_goods_list()
            self.refresh_purchases_history()
            
        except Exception as e:
            print(f"❌ finalize_invoice error: {e}")
            messagebox.showerror(
                get_text('error', self.lang),
                f"Ошибка при проведении накладной:\n{e}"
            )

    def clear_invoice(self):
        """Clear current invoice."""
        self.current_invoice_items = []
        for item in self.invoice_items_tree.get_children():
            self.invoice_items_tree.delete(item)
        self.invoice_number_var.set(self._next_invoice_number())
        self.invoice_supplier_var.set("")
        self.invoice_notes_var.set("")
        self.update_invoice_totals()

    def add_new_good_from_arrival(self):
        query = self.arrival_search_entry.get().strip()
        
        # Определить: штрихкод (цифры) или название
        if query.isdigit():
            barcode = query
            name = ""
        else:
            barcode = ""
            name = query
        
        # Открыть диалог создания товара с предзаполненными полями
        self.show_create_good_dialog(prefill_barcode=barcode, prefill_name=name)

    def add_good_to_arrival_invoice(self, good, edit_idx=None):
        """Show dialog to add or edit a good in invoice."""
        c = self.colors
        
        is_edit = edit_idx is not None
        item_data = self.current_invoice_items[edit_idx] if is_edit else {}
        title_text = f"✏️ {good['code']} — {good['name']}" if is_edit else f"📦 {good['code']} — {good['name']}"
        
        dialog = self.create_modal_dialog(title_text, width=420, height=300, scrollable=False)
        main = dialog.container
        
        tk.Label(main, text=title_text, font=self.font_bold_tuple, fg=c['fg'], bg=c['bg'],
                wraplength=int(390 * self.interface_scale), justify="center").pack(pady=(8, 6))
        
        vars_dict = {}
        fields = [
            ('quantity', get_text('quantity', self.lang), str(item_data.get('quantity', '1'))),
            ('purchase_price', get_text('purchase_price', self.lang), self.fmt_num(item_data.get('purchase_price', good['purchase_price']))),
            ('sale_price', get_text('sale_price', self.lang), self.fmt_num(item_data.get('sale_price', good['sale_price']))),
            ('pv', get_text('pv_amount', self.lang), self.fmt_num(item_data.get('pv', good.get('pv', 0)))),
        ]
        
        def save_item():
            try:
                q = float(vars_dict['quantity'].get() or 0)
                p = int(vars_dict['purchase_price'].get() or 0)
                s = int(vars_dict['sale_price'].get() or 0)
                pv = float(vars_dict['pv'].get() or 0)
            except ValueError:
                self.show_toast("Введите корректные числа", "error")
                return
            
            if q <= 0:
                self.show_toast("Количество должно быть > 0", "warning")
                return
            
            new_item = {
                'code': good['code'],
                'name': good['name'],
                'barcode': good.get('barcode', ''),
                'quantity': q,
                'purchase_price': p,
                'sale_price': s,
                'pv': pv
            }
            
            if is_edit:
                self.current_invoice_items[edit_idx] = new_item
                children = self.invoice_items_tree.get_children()
                self.invoice_items_tree.item(children[edit_idx], values=(
                    good.get('barcode', ''), good['code'], good['name'],
                    pv, q, self.format_amount(p), self.format_amount(s), self.format_amount(q*p)
                ))
            else:
                self.current_invoice_items.append(new_item)
                self.invoice_items_tree.insert('', 'end', values=(
                    good.get('barcode', ''), good['code'], good['name'],
                    pv, q, self.format_amount(p), self.format_amount(s), self.format_amount(q*p)
                ))
            
            self.update_invoice_totals()
            self.arrival_search_entry.delete(0, tk.END)
            dialog.destroy()
            self.arrival_search_entry.focus_set()
        
        content_frame = tk.Frame(main, bg=c['bg'])
        content_frame.pack(fill="x", padx=30, pady=(2, 8))
        content_frame.columnconfigure(1, weight=1)

        for i, (key, label, default) in enumerate(fields):
            tk.Label(content_frame, text=label + ":", font=self.font_normal_tuple,
                     bg=c['bg'], anchor='w').grid(row=i, column=0, pady=4, sticky="w", padx=(0, 10))
            var = tk.StringVar(value=default)
            vars_dict[key] = var
            entry = tk.Entry(content_frame, textvariable=var, font=self.font_normal_tuple)
            entry.grid(row=i, column=1, pady=4, sticky="ew")
            # Prices are whole numbers: reject kopecks at input
            if key in ('purchase_price', 'sale_price'):
                vcmd = (dialog.register(self._validate_int_input), '%P')
                entry.config(validate='key', validatecommand=vcmd)
            if key == 'quantity':
                entry.focus_set()
                entry.selection_range(0, tk.END)

        self._add_dialog_button(dialog, f"💾 {get_text('save', self.lang)}", save_item, 'primary', 'left')
        self._add_dialog_button(dialog, get_text('cancel', self.lang), dialog.destroy, 'neutral', 'right')
        self.bind_dialog_keys(dialog, confirm_callback=save_item, cancel_callback=dialog.destroy)
        
        # Auto-fit window height to the actual content: no clipped bottom row
        # on Small preset, no dead space on Large.
        dialog.update_idletasks()
        w = int(420 * self.interface_scale)
        req_h = main.winfo_reqheight() + dialog.btn_frame.winfo_reqheight() + 20
        dialog.geometry(f"{w}x{req_h}")

    def show_create_good_dialog(self, prefill_barcode="", prefill_name="", edit_good=None):
        """Show dialog to create or edit a product."""
        c = self.colors
        is_edit = edit_good is not None
        title = "✏️ Редактировать товар" if is_edit else "➕ Добавить товар"
        
        dialog = self.create_modal_dialog(title, width=650, height=520, scrollable=True)
        main = dialog.container
        main.pack(fill="both", expand=True)
        
        vars_dict = {}
        # Short, clean field labels
        fields = [
            ('barcode', 'Штрих-код',     edit_good['barcode'] if is_edit else prefill_barcode),
            ('code',    'Код товара',     edit_good['code'] if is_edit else ''),
            ('name',    'Наименование',   edit_good['name'] if is_edit else prefill_name),
            ('pv',      'PV',             self.fmt_num(edit_good.get('pv', 0)) if is_edit else '0'),
            ('purchase_price', 'Закуп. цена', self.fmt_num(edit_good.get('purchase_price', 0)) if is_edit else '0'),
            ('sale_price',     'Прод. цена',  self.fmt_num(edit_good.get('sale_price', 0)) if is_edit else '0'),
        ]
        
        form = tk.Frame(main, bg=c['bg'])
        form.pack(fill="x")
        form.columnconfigure(1, weight=1)
        
        first_entry = None
        for i, (key, label, default) in enumerate(fields):
            tk.Label(form, text=label + ":", font=self.font_normal_tuple,
                     bg=c['bg'], anchor='w', width=14).grid(row=i, column=0, pady=6, sticky="w")
            var = tk.StringVar(value=default)
            entry = tk.Entry(form, textvariable=var, font=self.font_normal_tuple,
                             bg=c['bg_secondary'], relief='flat', insertbackground=c['fg'])
            entry.grid(row=i, column=1, padx=(8, 0), pady=6, sticky="ew", ipady=5)
            if key == 'code' and is_edit and not self.has_permission('can_edit_ids'):
                entry.config(state='readonly')
            elif key == 'barcode' and is_edit and not self.has_permission('goods_code_edit'):
                entry.config(state='readonly')
            # Prices are whole numbers: reject kopecks at input
            if key in ('purchase_price', 'sale_price'):
                vcmd = (dialog.register(self._validate_int_input), '%P')
                entry.config(validate='key', validatecommand=vcmd)
            vars_dict[key] = var
            if first_entry is None and not is_edit:
                first_entry = entry
        
        if first_entry:
            first_entry.focus_set()
        
        def save_good():
            try:
                code = vars_dict['code'].get().strip()
                name = vars_dict['name'].get().strip()
                pv_str = vars_dict['pv'].get().strip()
                if not code or not name:
                    self.show_toast("Код и имя обязательны", "error")
                    return
                if not pv_str:
                    self.show_toast("Поле PV обязательно для заполнения", "error")
                    return
                
                try:
                    pv_val = float(pv_str)
                except ValueError:
                    self.show_toast("Поле PV должно быть числом", "error")
                    return
                
                barcode = vars_dict['barcode'].get().strip()
                if barcode:
                    _, dup = self.goods_manager.get_good_by_barcode(barcode)
                    if dup and (not is_edit or dup['code'] != edit_good['code']):
                        self.show_toast(f"Штрих-код уже используется: {dup['code']} — {dup['name']}", "error")
                        return
                    
                qty = edit_good.get('quantity', 0) if is_edit else 0
                ok = self.goods_manager.add_good(
                    code, name,
                    pv_val,
                    int(vars_dict['purchase_price'].get() or 0),
                    int(vars_dict['sale_price'].get() or 0),
                    qty, barcode,
                    set_quantity=is_edit,
                    user_name=self._get_user_device_label(),
                    old_code=edit_good['code'] if is_edit else None
                )
                if not ok:
                    self.show_toast("Товар с таким кодом уже существует", "error")
                    return
                self.show_toast(f"✅ '{name}' {get_text('saved', self.lang)}", "inventory")
                try:
                    self.refresh_goods_list()
                    if hasattr(self, 'search_good_for_arrival'):
                        self.search_good_for_arrival()
                except Exception as e:
                    print(f"UI refresh after save_good failed: {e}")
                finally:
                    dialog.destroy()
            except ValueError:
                self.show_toast("Ошибка в числовых полях", "error")
        
        def delete_good():
            if not is_edit: return
            stock = float(edit_good.get('quantity') or 0)
            stock_warn = (f"\n\n⚠️ На складе: {stock:g} шт. — товар скроется из продажи, "
                          f"остаток будет недоступен.") if stock > 0 else ""
            if messagebox.askyesno("Удалить", f"Вы уверены, что хотите удалить товар '{edit_good['name']}'?\n\n"
                                   f"Это действие скроет товар из продажи, но сохранит его в истории.{stock_warn}"):
                if self.goods_manager.delete_good(edit_good['code']):
                    self.show_toast("Товар удален", "success")
                    self.refresh_goods_list()
                    dialog.destroy()
                else:
                    messagebox.showerror("Ошибка", "Не удалось удалить товар")

        # Buttons in pinned zone
        self._add_dialog_button(dialog, "Отмена", dialog.destroy, 'neutral', 'right')
        
        if is_edit and self.current_role in ('admin', 'superadmin'):
            self._add_dialog_button(dialog, "🗑 Удалить", delete_good, 'danger', 'right')

        self._add_dialog_button(dialog, "💾 Сохранить", save_good, 'primary', 'left')
        
        self.bind_dialog_keys(dialog, confirm_callback=save_good, cancel_callback=dialog.destroy)

    def edit_good_popup(self, event=None):
        """Handle double click on goods list to open edit popup."""
        sel = self.goods_tree.selection()
        if not sel:
            return
        item_values = self.goods_tree.item(sel[0])['values']
        code = str(item_values[1])
        _, good = self.goods_manager.get_good(code)
        if good:
            self.show_create_good_dialog(edit_good=good)


    def create_goods_subtab(self, parent):
        """Create goods list subtab."""
        c = self.colors
        
        main_frame = tk.Frame(parent, bg=c['bg'])
        main_frame.pack(fill="both", expand=True, padx=5, pady=5)
        
        # LEFT - Goods list
        left_panel = tk.Frame(main_frame, bg=c['bg'])
        left_panel.pack(side="left", fill="both", expand=True, padx=5, pady=5)
        left_panel.pack_propagate(False)
        
        list_frame = tk.LabelFrame(left_panel, text=f" {get_text('goods_list', self.lang)} ",
                                  font=self.font_bold_tuple, bg=c['frame_bg'], fg=c['fg'])
        list_frame.pack(fill="both", expand=True, padx=5, pady=5)
        
        # Search
        search_frame = tk.Frame(list_frame, bg=c['frame_bg'])
        search_frame.pack(fill="x", padx=5, pady=5)
        
        self.goods_search = self._build_search_bar(search_frame, c['frame_bg'])
        self.goods_search.bind('<KeyRelease>', self.filter_goods_list)
        self.goods_search.bind('<Down>', lambda e: (self.goods_tree.focus_set(), self.goods_tree.selection_set(self.goods_tree.get_children()[0]) if self.goods_tree.get_children() else None))
        # Reliable autofocus: if the goods sub-tab is the visible one, focus its search
        self.master.after(250, lambda: self._focus_search_field() if self.goods_search.winfo_exists() else None)
        
        # Goods table
        columns = ('barcode', 'code', 'name', 'pv', 'purchase', 'sale', 'qty')
        self.goods_tree = ttk.Treeview(list_frame, columns=columns, show='headings', height=18)
        
        headers = ['Штрих-код', get_text('code', self.lang), get_text('name', self.lang), 'ПВ',
                   get_text('purchase_price', self.lang), get_text('sale_price', self.lang), get_text('quantity', self.lang)]
        widths = [140, 90, 210, 55, 80, 80, 80]

        for col, hdr, w in zip(columns, headers, widths):
            self.goods_tree.heading(col, text=hdr)
            self.goods_tree.column(col, width=w, anchor='center' if col != 'name' else 'w', stretch=(col == 'name'))
            
        self.setup_treeview_sorting(self.goods_tree, columns, numeric_cols=['pv', 'purchase', 'sale', 'qty'])
        self.goods_tree.bind('<Button-1>', self.prevent_treeview_resize)
        
        scrollbar = AutoScrollbar(list_frame, orient="vertical", command=self.goods_tree.yview)
        self.goods_tree.configure(yscrollcommand=scrollbar.set)
        self.goods_tree.pack(side="left", fill="both", expand=True, padx=5, pady=5)
        scrollbar.pack(side="right", fill="y")
        
        self.bind_mousewheel(self.goods_tree)
        self.goods_tree.bind('<<TreeviewSelect>>', self.on_good_select)
        self.setup_universal_navigation(self.goods_tree, lambda: self.edit_good_popup(None))
        
        # Stats at the bottom
        stats_frame = tk.Frame(left_panel, bg=c['bg'])
        stats_frame.pack(side="bottom", fill="x", padx=5, pady=5)
        
        self.goods_stats = tk.Label(stats_frame, text=f"{get_text('total_goods', self.lang)}: 0 | {get_text('stock_value', self.lang)} (закуп.): 0 | {get_text('stock_value', self.lang)} (продаж.): 0",
                                   font=self.font_normal_tuple, bg=c['bg'], justify="left")
        self.goods_stats.pack(anchor="w")
        
        # Load data
        self.refresh_goods_list()

    def create_purchases_history_subtab(self, parent):
        """Create purchases history subtab."""
        c = self.colors
        
        main_frame = tk.Frame(parent, bg=c['bg'])
        main_frame.pack(fill="both", expand=True, padx=5, pady=5)
        
        # TOP FILTER FRAME
        filter_frame = tk.LabelFrame(main_frame, text=" Фильтры ", font=self.font_bold_tuple, bg=c['frame_bg'], fg=c['fg'])
        filter_frame.pack(fill="x", padx=5, pady=5)
        
        filter_inner = tk.Frame(filter_frame, bg=c['frame_bg'])
        filter_inner.pack(anchor="w", padx=5, pady=5)
        
        # Search Entry
        self.purchases_search_var = tk.StringVar()
        self.purchases_search_entry = self._build_search_bar(
            filter_inner, c['frame_bg'], textvariable=self.purchases_search_var)
        self.purchases_search_entry.bind('<KeyRelease>', lambda e: self.refresh_purchases_history())
        self.purchases_search_entry.bind('<Down>', lambda e: (self.purchases_tree.focus_set(), self.purchases_tree.selection_set(self.purchases_tree.get_children()[0]) if self.purchases_tree.get_children() else None))
        
        # Date range
        first_day = date.today().replace(day=1).strftime("%d.%m.%Y")
        today_str = date.today().strftime("%d.%m.%Y")
        self.purchases_range_var = tk.StringVar(value=f"{first_day} - {today_str}")
        self.purchases_range_entry = tk.Entry(filter_inner, textvariable=self.purchases_range_var,
                                              font=self.font_normal_tuple, width=23)
        self.purchases_range_entry.pack(side="left", padx=5)
        self.purchases_range_entry.bind('<Button-1>', lambda e: self.show_date_range_picker(
            range_var=self.purchases_range_var, callback=self.refresh_purchases_history))
        self.purchases_range_entry.bind('<KeyRelease>', lambda e: self.refresh_purchases_history())

        # Purchases table
        columns = ('id', 'invoice', 'date', 'supplier', 'items', 'total', 'user', 'status', 'notes')
        
        table_frame = tk.Frame(main_frame, bg=c['bg'])
        table_frame.pack(fill="both", expand=True, padx=5, pady=5)
        
        self.purchases_tree = ttk.Treeview(table_frame, columns=columns, show='headings', height=20)
        
        headers = ['ID', get_text('invoice_number', self.lang), get_text('purchase_date', self.lang),
                  get_text('supplier', self.lang), get_text('items_count', self.lang), 
                  get_text('purchase_total', self.lang), 'Пользователь', get_text('status', self.lang), 'Заметки']
        widths = [80, 130, 130, 160, 70, 90, 140, 80, 180]
        
        self.purchases_tree.tag_configure('cancelled', foreground='red')
        
        for col, hdr, w in zip(columns, headers, widths):
            self.purchases_tree.heading(col, text=hdr)
            self.purchases_tree.column(col, width=w, anchor='center' if col not in ['supplier'] else 'w')
            
        self.setup_treeview_sorting(self.purchases_tree, columns, numeric_cols=['id', 'items', 'total'])
        self.setup_universal_navigation(self.purchases_tree, lambda: self.show_purchase_details(None)) # Added
        self.purchases_tree.bind('<Button-1>', self.prevent_treeview_resize)
        
        scrollbar = AutoScrollbar(table_frame, orient="vertical", command=self.purchases_tree.yview)
        self.purchases_tree.configure(yscrollcommand=scrollbar.set)
        self.purchases_tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        
        # Double-click to open invoice details
        self.purchases_tree.bind('<Double-1>', self.show_purchase_details)
        
        self.refresh_purchases_history()

    def show_purchase_details(self, event=None):
        """Show details of selected purchase invoice. Standardized adaptive popup."""
        sel = self.purchases_tree.selection()
        if not sel: return
        
        purchase_id = self.purchases_tree.item(sel[0])['values'][0]
        purchase = self.purchases_manager.get_purchase(purchase_id)
        if not purchase: return
        
        c = self.colors
        dialog = self.create_modal_dialog(f"{get_text('invoice_number', self.lang)} {purchase['invoice_number']}", 850, 620, scrollable=False)
        
        main = dialog.container
        
        # Header info
        header = tk.Frame(main, bg=c['frame_bg'], padx=10, pady=10)
        header.pack(fill="x", pady=(0, 10))
        
        dt = purchase['datetime'][:16].replace('T', ' ')
        info_text = f"№ {purchase['invoice_number']}  |  {dt}\n"
        info_text += f"{get_text('supplier', self.lang)}: {purchase.get('supplier', '-')}\n"
        info_text += f"{get_text('purchase_total', self.lang)}: {self.format_amount(purchase['total_amount'])}"
        
        tk.Label(header, text=info_text, font=self.font_bold_tuple, bg=c['frame_bg'], justify="left").pack(anchor="w")
        
        if purchase.get('notes'):
            tk.Label(main, text=f"Примечание: {purchase['notes']}", font=self.font_small_tuple, bg=c['bg']).pack(anchor="w", pady=(0, 10))
        
        # Status display
        status_val = purchase.get('status', 'completed')
        status_text = get_text(status_val, self.lang)
        status_color = c['error'] if status_val == 'cancelled' else c['success']
        tk.Label(main, text=f"{get_text('status', self.lang)}: {status_text}", 
                 font=self.font_bold_tuple, bg=c['bg'], fg=status_color).pack(anchor="w", pady=(0, 10))
        
        # Items table (fills all space)
        items_frame = tk.Frame(main, bg=c['bg'], bd=1, relief="flat")
        items_frame.pack(fill="both", expand=True)
        
        columns = ('barcode', 'code', 'name', 'qty', 'purchase', 'sale', 'sum')
        tree = ttk.Treeview(items_frame, columns=columns, show='headings')
        
        headers = ['Штрих-код', 'Код', 'Наименование', 'Кол-во', 'Закуп.', 'Продаж.', 'Сумма']
        widths = [110, 70, 220, 55, 80, 80, 90]
        
        for col, hdr, w in zip(columns, headers, widths):
            tree.heading(col, text=hdr)
            # Stretch the last column to fill the gap
            tree.column(col, width=w, anchor='center' if col != 'name' else 'w', stretch=(col == columns[-1]))
        
        tree.bind('<Button-1>', self.prevent_treeview_resize)
        
        sb = AutoScrollbar(items_frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        tree.pack(side="left", fill="both", expand=True)
        
        for item in purchase.get('items', []):
            purchase_price = item.get('purchase_price', 0)
            quantity = item.get('quantity', 0)
            item_sum = purchase_price * quantity
            barcode = str(item.get('barcode') or item.get('good_barcode') or '')
            
            tree.insert('', 'end', values=(
                barcode, item['code'], item['name'],
                quantity, self.format_amount(purchase_price),
                self.format_amount(item.get('sale_price', 0)), self.format_amount(item_sum)
            ))
        
        # Buttons
        if status_val != 'cancelled' and self.has_permission('purchase_cancel'):
            self._add_dialog_button(dialog, get_text('cancel_arrival', self.lang), 
                                   lambda: self._cancel_arrival(purchase_id, dialog), 'error', 'left')
        
        self._add_dialog_button(dialog, get_text('close', self.lang), dialog.destroy, 'neutral', 'right')

    def _cancel_arrival(self, purchase_id, dialog):
        """Handle arrival cancellation."""
        if not messagebox.askyesno(get_text('confirm', self.lang), get_text('confirm_cancel_arrival', self.lang)):
            return
            
        if self.purchases_manager.cancel_purchase(purchase_id):
            self.show_toast(f"✅ {get_text('cancelled', self.lang)}")
            dialog.destroy()
            self.refresh_purchases_history()
            self.refresh_goods_list() # Revert inventory in UI
        else:
            messagebox.showerror(get_text('error_title', self.lang), get_text('could_not_cancel_purchase', self.lang))
        
    # =========================================================================
    # WRITEOFFS TAB
    # =========================================================================
    def create_writeoff_subtab(self, parent):
        """Create write-offs subtab."""
        c = self.colors
        
        main_frame = tk.Frame(parent, bg=c['bg'])
        main_frame.pack(fill="both", expand=True, padx=5, pady=5)
        
        # Top controls: Filtering
        top_frame = tk.Frame(main_frame, bg=c['bg'])
        top_frame.pack(fill="x", padx=5, pady=5)
        
        # Search
        self.writeoffs_search = self._build_search_bar(top_frame, c['bg'])
        self.writeoffs_search.bind('<KeyRelease>', lambda e: self.refresh_writeoffs_history())
        self.writeoffs_search.bind('<Down>', lambda e: (self.writeoffs_tree.focus_set(), self.writeoffs_tree.selection_set(self.writeoffs_tree.get_children()[0]) if self.writeoffs_tree.get_children() else None))
        
        # Date range
        first_day = date.today().replace(day=1).strftime("%d.%m.%Y")
        today_str = date.today().strftime("%d.%m.%Y")
        self.writeoffs_range_var = tk.StringVar(value=f"{first_day} - {today_str}")
        self.writeoffs_range_entry = tk.Entry(top_frame, textvariable=self.writeoffs_range_var,
                                              font=self.font_normal_tuple, width=23)
        self.writeoffs_range_entry.pack(side="left", padx=5)
        self.writeoffs_range_entry.bind('<Button-1>', lambda e: self.show_date_range_picker(
            range_var=self.writeoffs_range_var, callback=self.refresh_writeoffs_history))
        self.writeoffs_range_entry.bind('<KeyRelease>', lambda e: self.refresh_writeoffs_history())

        # New Write-off button (Aligned in top_frame)
        btn_writeoff = self._btn(top_frame, text=" Новое списание + ", command=self.show_writeoff_dialog, style='danger')
        btn_writeoff.pack(side="right", padx=10)
        
        if not self.has_permission('writeoff_create'):
            btn_writeoff.config(state='disabled', bg=c['bg_tertiary'])
        

        # Table
        columns = ('id', 'datetime', 'reason', 'items', 'cashier')
        self.writeoffs_tree = ttk.Treeview(main_frame, columns=columns, show='headings', height=15)
        
        headers = ['ID', 'Дата / Время', 'Причина', 'Кол-во товаров', 'Пользователь']
        widths = [80, 160, 300, 120, 150]
        
        for col, hdr, w in zip(columns, headers, widths):
            self.writeoffs_tree.heading(col, text=hdr)
            self.writeoffs_tree.column(col, width=w, anchor='center' if col in ['id', 'items'] else 'w')
            
        self.setup_treeview_sorting(self.writeoffs_tree, columns, numeric_cols=['id', 'items'])
        self.setup_universal_navigation(self.writeoffs_tree, lambda: self.show_writeoff_details_dialog()) # Added
        self.writeoffs_tree.bind('<Button-1>', self.prevent_treeview_resize)
            
        scrollbar = AutoScrollbar(main_frame, orient="vertical", command=self.writeoffs_tree.yview)
        self.writeoffs_tree.configure(yscrollcommand=scrollbar.set)
        self.writeoffs_tree.pack(side="left", fill="both", expand=True, padx=5, pady=5)
        scrollbar.pack(side="right", fill="y")
        self.bind_mousewheel(self.writeoffs_tree)
        
        self.writeoffs_tree.bind('<Double-1>', self.show_writeoff_details_dialog)
        self.refresh_writeoffs_history()

    def refresh_writeoffs_history(self):
        """Refresh write-offs history list with filters."""
        if not hasattr(self, 'writeoffs_tree'): return
        
        search = self.writeoffs_search.get().strip() if hasattr(self, 'writeoffs_search') else None
        
        # Parse dates to ISO
        d_from = None
        d_to = None
        try:
            parts = [p.strip() for p in self.writeoffs_range_var.get().split('-')]
            if parts[0]:
                d_from = datetime.strptime(parts[0], "%d.%m.%Y").strftime("%Y-%m-%d") + "T00:00:00"
            if len(parts) > 1 and parts[1]:
                d_to = datetime.strptime(parts[1], "%d.%m.%Y").strftime("%Y-%m-%d") + "T23:59:59"
        except:
            pass
            
        for item in self.writeoffs_tree.get_children():
            self.writeoffs_tree.delete(item)
            
        for w in self.writeoffs_manager.get_all_writeoffs(search_query=search, date_from=d_from, date_to=d_to):
            dt = w['datetime'][:16].replace('T', ' ')
            user_label = w.get('cashier_user', '')

            self.writeoffs_tree.insert('', 'end', values=(
                w['id'], dt, w.get('reason', ''),
                w['items_count'], user_label
            ))

    def show_writeoff_dialog(self):
        """Show dialog to create a new write-off."""
        dialog = self.create_modal_dialog("Списание товара", width=900, height=620, scrollable=False)
        c = self.colors
        
        main = dialog.container
        
        # Reason
        tk.Label(main, text="Причина списания:", font=self.font_bold_tuple, bg=c['bg']).pack(anchor="w")
        reason_var = tk.StringVar()
        tk.Entry(main, textvariable=reason_var, font=self.font_normal_tuple).pack(fill="x", pady=(5, 15))
        
        # Search Good (Autocompletion)
        tk.Label(main, text="Поиск товара:", font=self.font_bold_tuple, bg=c['bg']).pack(anchor="w")
        
        writeoff_items = []
        
        def fetch_goods(query):
            res = []
            for g in self.goods_manager.get_all_goods():
                if query in g['code'].lower() or query in g['name'].lower() or query in g.get('barcode', ''):
                    res.append(f"{g['code']} | {g['name']} ({g.get('quantity', 0)} шт.)")
            return res
            
        def add_item_from_search(text):
            code = text.split('|')[0].strip()
            
            # Ask for quantity
            qty = self.ask_float_dialog("Количество", f"Сколько единиц {code} списать?", minvalue=0.1)
            if qty is None: return
            
            _, good = self.goods_manager.get_good(code)
            if not good: return
            
            # Check if already in list
            for i, existing in enumerate(writeoff_items):
                if existing['code'] == code:
                    writeoff_items[i]['quantity'] += qty
                    # Update tree
                    for item_id in tree.get_children():
                        if tree.item(item_id)['values'][0] == code:
                            tree.item(item_id, values=(code, good['name'], writeoff_items[i]['quantity']))
                    return
            
            writeoff_items.append({'code': code, 'name': good['name'], 'quantity': qty})
            tree.insert('', 'end', values=(code, good['name'], qty))
        
        # Search bar (autocomplete) right after the label
        AutocompleteEntry(main, fetch_goods, add_item_from_search,
                          list_font=self.font_normal_tuple, font=self.font_normal_tuple,
                          target_tree=None).pack(fill="x", pady=5)
        
        # Table of items to write off
        tk.Label(main, text="Товары к списанию:", font=self.font_bold_tuple, bg=c['bg']).pack(anchor="w", pady=(10, 0))
        table_frame = tk.Frame(main, bg=c['bg'])
        table_frame.pack(fill="both", expand=True, pady=5)
        
        columns = ('code', 'name', 'qty')
        tree = ttk.Treeview(table_frame, columns=columns, show='headings', height=8)
        tree.heading('code', text='Код')
        tree.heading('name', text='Наименование')
        tree.heading('qty', text='Кол-во')
        tree.column('code', width=100)
        tree.column('name', width=350)
        tree.column('qty', width=80, anchor='center')
        
        sb = AutoScrollbar(table_frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        tree.pack(side="left", fill="both", expand=True)
        
        def remove_selected_row(event=None):
            sel = tree.selection()
            if not sel:
                return
            idx = tree.index(sel[0])
            tree.delete(sel[0])
            if 0 <= idx < len(writeoff_items):
                writeoff_items.pop(idx)
        
        tree.bind('<Delete>', remove_selected_row)
        tree.bind('<BackSpace>', remove_selected_row)
        row_menu = tk.Menu(self.master, tearoff=0, bg=self.colors['bg'], fg=self.colors['fg'])
        row_menu.add_command(label="🗑️ Удалить строку", command=remove_selected_row)
        tree.bind('<Button-3>', lambda e: (tree.selection_set(tree.identify_row(e.y)),
                                           row_menu.post(e.x_root, e.y_root)))
        
        def perform_save():
            reason = reason_var.get().strip()
            if not reason:
                messagebox.showwarning("Внимание", "Укажите причину списания", parent=dialog)
                return
            if not writeoff_items:
                messagebox.showwarning("Внимание", "Добавьте товары для списания", parent=dialog)
                return
                
            if messagebox.askyesno("Подтверждение", f"Списать {len(writeoff_items)} поз.? Это изменит остатки на складе.", parent=dialog):
                # C4: atomic write-off — document and stock updates commit
                # (or roll back) as ONE transaction with stock validation.
                try:
                    self.inventory_ops.writeoff(reason, writeoff_items, self._get_user_device_label())
                    self.show_toast("✅ Списание успешно выполнено", "inventory")
                    self.refresh_goods_list()
                    self.refresh_writeoffs_history()
                    dialog.destroy()
                except ValueError as e:
                    messagebox.showerror("Ошибка", str(e), parent=dialog)
                except Exception as e:
                    messagebox.showerror("Ошибка", f"Не удалось выполнить списание: {e}", parent=dialog)

        # Buttons in pinned zone (always visible)
        self._add_dialog_button(dialog, "✅ Провести списание", perform_save, 'success', 'right')
        self._add_dialog_button(dialog, "Отмена", dialog.destroy, 'neutral', 'right')

    def show_writeoff_details_dialog(self, event=None):
        """Show details of a specific write-off."""
        sel = self.writeoffs_tree.selection()
        if not sel: return
        
        w_id = self.writeoffs_tree.item(sel[0])['values'][0]
        writeoff = self.writeoffs_manager.get_writeoff_by_id(w_id)
        if not writeoff: return
        
        dialog = self.create_modal_dialog(f"Детали списания №{w_id}", width=620, height=470, scrollable=False)
        c = self.colors
        
        main = dialog.container
        
        header = f"Дата: {writeoff['datetime'][:16].replace('T', ' ')}\nПричина: {writeoff['reason']}\nПользователь: {writeoff.get('cashier_user', '')}"
        tk.Label(main, text=header, font=self.font_bold_tuple, bg=c['bg'], justify="left").pack(anchor="w", pady=(0, 15))
        
        # Items table
        items_frame = tk.Frame(main, bg=c['bg'])
        items_frame.pack(fill="both", expand=True)
        
        columns = ('code', 'name', 'qty')
        tree = ttk.Treeview(items_frame, columns=columns, show='headings', height=10)
        tree.heading('code', text='Код')
        tree.heading('name', text='Наименование')
        tree.heading('qty', text='Кол-во')
        tree.column('code', width=100)
        tree.column('name', width=350)
        tree.column('qty', width=80, anchor='center')
        tree.pack(side="left", fill="both", expand=True)
        
        scrollbar = AutoScrollbar(items_frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        
        for item in writeoff.get('items', []):
            tree.insert('', 'end', values=(item['good_code'], item['name'], item['quantity']))
        
        # Close in pinned zone
        self._add_dialog_button(dialog, get_text('close', self.lang), dialog.destroy, 'neutral', 'right')

    def refresh_purchases_history(self):
        """Refresh purchases history list with filters."""
        for item in self.purchases_tree.get_children():
            self.purchases_tree.delete(item)
            
        # Get filter values securely
        search_query = ""
        if hasattr(self, 'purchases_search_var'):
            search_query = self.purchases_search_var.get().strip().lower()
            
        dt_from = None
        dt_to = None
        try:
            parts = [p.strip() for p in self.purchases_range_var.get().split('-')]
            if parts[0]: dt_from = datetime.strptime(parts[0], "%d.%m.%Y").date()
            if len(parts) > 1 and parts[1]: dt_to = datetime.strptime(parts[1], "%d.%m.%Y").date()
        except (ValueError, AttributeError):
            pass
        
        all_purchases = self.purchases_manager.get_all_purchases()
        
        for p in all_purchases:
            # 1. Date Filter
            if dt_from or dt_to:
                try:
                    p_date = datetime.strptime(p['datetime'][:10], "%Y-%m-%d").date()
                    if dt_from and p_date < dt_from: continue
                    if dt_to and p_date > dt_to: continue
                except Exception:
                    pass
            
            # 2. Search Filter (by Invoice number, Supplier, or Notes)
            if search_query:
                inv = str(p.get('invoice_number', '')).lower()
                sup = str(p.get('supplier', '')).lower()
                notes = str(p.get('notes', '')).lower()
                if search_query not in inv and search_query not in sup and search_query not in notes:
                    continue
                    
            dt = p['datetime'][:16].replace('T', ' ')
            status = p.get('status', 'completed')
            tags = ('cancelled',) if status == 'cancelled' else ()
            
            user_label = p.get('cashier_user', '')

            self.purchases_tree.insert('', 'end', values=(
                p['id'], p['invoice_number'], dt, p.get('supplier', ''),
                p['items_count'], self.format_amount(p['total_amount']), user_label, get_text(status, self.lang), p.get('notes', '')[:50]
            ), tags=tags)

    def refresh_goods_list(self):
        # Always respect the current search query if one exists
        if hasattr(self, 'goods_search') and self.goods_search.get().strip():
            self.filter_goods_list()
            return
            
        for item in self.goods_tree.get_children():
            self.goods_tree.delete(item)
        
        goods = self.goods_manager.get_all_goods()
        total_purchase_value = 0
        total_sale_value = 0
        
        for g in goods:
            self.goods_tree.insert('', 'end', values=(
                g.get('barcode', ''), g['code'], g['name'], g.get('pv', 0),
                self.format_amount(g['purchase_price']), self.format_amount(g['sale_price']),
                g.get('quantity', 0)
            ))
            total_purchase_value += g['purchase_price'] * g.get('quantity', 0)
            total_sale_value += g['sale_price'] * g.get('quantity', 0)
        
        if hasattr(self, 'goods_stats'):
            self.goods_stats.config(text=f"{get_text('total_goods', self.lang)}: {len(goods)} | {get_text('stock_value', self.lang)} (закуп.): {self.format_amount(total_purchase_value)} | {get_text('stock_value', self.lang)} (продаж.): {self.format_amount(total_sale_value)}")

    def filter_goods_list(self, event=None):
        query = self.goods_search.get().lower()
        for item in self.goods_tree.get_children():
            self.goods_tree.delete(item)
        
        for g in self.goods_manager.get_all_goods():
            if query in g['code'].lower() or query in g['name'].lower() or query in g.get('barcode', ''):
                self.goods_tree.insert('', 'end', values=(
                    g.get('barcode', ''), g['code'], g['name'], g.get('pv', 0),
                    self.format_amount(g['purchase_price']), self.format_amount(g['sale_price']),
                    g.get('quantity', 0)
                ))

    def on_good_select(self, event=None):
        sel = self.goods_tree.selection()
        if sel:
            barcode = self.goods_tree.item(sel[0])['values'][0]
            code = self.goods_tree.item(sel[0])['values'][1]
            _, g_obj = self.goods_manager.get_good(code)
            if g_obj:
                # self.goods_vars logic removed - using dialogs for editing
                pass

    def save_good(self):
        barcode = self.goods_vars['g_barcode'].get().strip()
        code = self.goods_vars['g_code'].get().strip()
        name = self.goods_vars['g_name'].get().strip()
        
        if not code or not name:
            messagebox.showwarning(get_text('error_title', self.lang), get_text('fill_code_name', self.lang))
            return
        
        try:
            pv = float(self.goods_vars['g_pv'].get() or 0)
            purchase = float(self.goods_vars['g_purchase'].get() or 0)
            sale = float(self.goods_vars['g_sale'].get() or 0)
        except ValueError:
            messagebox.showwarning(get_text('error_title', self.lang), get_text('check_numeric_values', self.lang))
            return
        
        # Check if good exists — preserve quantity
        _, existing = self.goods_manager.get_good(code)
        existing_qty = existing.get('quantity', 0) if existing else 0
        
        self.goods_manager.add_good(code, name, pv, purchase, sale, 0, barcode, user_name=self._get_user_device_label())
        # Restore quantity if it was overwritten
        if existing:
            with self._db_manager.get_connection() as conn:
                conn.cursor().execute('UPDATE goods SET quantity = ? WHERE code = ?', (existing_qty, code))
        
        self.show_toast(f"✅ '{name}' {get_text('saved', self.lang)}", "inventory")
        self.refresh_goods_list()
        self.clear_goods_form()

    def delete_good(self):
        code = self.goods_vars['g_code'].get().strip()
        if not code:
            return
        if messagebox.askyesno(get_text('confirm_delete', self.lang), f"{code}?"):
            self.goods_manager.delete_good(code)
            self.refresh_goods_list()
            self.clear_goods_form()

    def clear_goods_form(self):
        for var in self.goods_vars.values():
            var.set("")



    # =========================================================================
    # INVENTORY AUDIT (РЕВИЗИЯ) SUBTAB
    # =========================================================================
    def create_inventory_subtab(self, parent):
        """Create inventory audit subtab."""
        c = self.colors
        t = lambda k: get_text(k, self.lang)
        
        self._inv_parent = parent
        main_frame = tk.LabelFrame(parent, text=f" 📋 {t('inventory_audit_title')} ",
                                   font=self.font_bold_tuple, bg=c['frame_bg'], fg=c['fg'])
        main_frame.pack(fill="both", expand=True, padx=5, pady=5)

        # Container
        self._inv_container = tk.Frame(main_frame, bg=c['bg'])
        self._inv_container.pack(fill="both", expand=True, padx=5, pady=5)
        
        self._inv_build_list_view()
        self._inv_build_active_view()
        self._inv_show_list()
    
    def _inv_build_list_view(self):
        """Audit history list."""
        c = self.colors
        t = lambda k: get_text(k, self.lang)
        self._inv_list_frame = tk.Frame(self._inv_container, bg=c['bg'])
        
        cols = ('date', 'created_by', 'type', 'items', 'progress', 'status', 'surplus', 'shortage', 'diff_money')
        tree_frame = tk.Frame(self._inv_list_frame, bg=c['bg'])
        tree_frame.pack(fill="both", expand=True)
        
        self._inv_tree = ttk.Treeview(tree_frame, columns=cols, show="headings", height=15,
                                       style="Custom.Treeview")
        
        headers = {
            'date': ('Дата', 140), 'created_by': ('Создал', 100), 'type': ('Тип', 100),
            'items': ('Товаров', 70), 'progress': ('Прогресс', 90), 'status': ('Статус', 110),
            'surplus': ('Излишки', 80), 'shortage': ('Недостача', 80),
            'diff_money': ('Разница ₸', 110)
        }
        for col, (header, width) in headers.items():
            self._inv_tree.heading(col, text=header)
            self._inv_tree.column(col, width=width, anchor="center")
        
        scrollbar = AutoScrollbar(tree_frame, orient="vertical", command=self._inv_tree.yview)
        self._inv_tree.configure(yscrollcommand=scrollbar.set)
        self._inv_tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        
        self._inv_tree.bind("<Double-1>", self._heading_guard, add="+")
        self._inv_tree.bind("<Double-1>", self._inv_on_audit_select)
        
        btn_frame = tk.Frame(self._inv_list_frame, bg=c['bg'], pady=8)
        btn_frame.pack(fill="x")

        if self.has_permission('inventory_conduct'):
            self._inv_btn_new = self._btn(btn_frame, text=f"➕ {t('start_new_audit')}",
                                          command=self._inv_start_new_audit, style='accent',
                                          cursor="hand2")
            self._inv_btn_new.pack(side="left", padx=5)
            self._btn(btn_frame, text="🗑 Удалить", command=self._inv_delete_selected,
                      style='neutral', cursor="hand2").pack(side="right", padx=5)
    
    def _inv_build_active_view(self):
        """Active audit counting view with autocomplete search."""
        c = self.colors
        t = lambda k: get_text(k, self.lang)
        
        self._inv_active_frame = tk.Frame(self._inv_container, bg=c['bg'])
        
        # Status bar
        status_bar = tk.Frame(self._inv_active_frame, bg=c['frame_bg'], padx=10, pady=6)
        status_bar.pack(fill="x", pady=(0, 5))
        
        self._inv_status_label = tk.Label(status_bar, text="", font=self.font_small_tuple,
                                           bg=c['frame_bg'], fg=c['fg'])
        self._inv_status_label.pack(side="left")
        
        self._inv_progress_label = tk.Label(status_bar, text="", font=self.font_small_tuple,
                                             bg=c['frame_bg'], fg=c['accent'])
        self._inv_progress_label.pack(side="left", padx=20)
        
        self._inv_progress_bar = ttk.Progressbar(status_bar, length=200, mode='determinate')
        self._inv_progress_bar.pack(side="left", padx=5)
        
        # Quick count bar with autocomplete
        quick_frame = tk.Frame(self._inv_active_frame, bg=c['frame_bg'], padx=10, pady=6)
        quick_frame.pack(fill="x", pady=(0, 5))
        
        tk.Label(quick_frame, text=f"🔍 {t('quick_count')}:", font=self.font_small_tuple,
                 bg=c['frame_bg'], fg=c['fg']).pack(side="left")
        
        # Autocomplete search (like POS)
        def _inv_fetch_goods(query):
            if not self._inv_current_audit_id:
                return []
            items = self.audit_manager.get_audit_items(
                self._inv_current_audit_id, search_query=query)
            return [f"{it['good_code']} | {it['good_name']} | Ожид: {it['expected_qty']:g}" for it in items[:10]]
        
        def _inv_on_select(text):
            code = text.split('|')[0].strip()
            self._inv_quick_found_code = code
            items = self.audit_manager.get_audit_items(
                self._inv_current_audit_id, search_query=code)
            if items:
                it = items[0]
                exp = it['expected_qty']
                self._inv_quick_result.config(
                    text=f"✅ {it['good_name']} (ожид: {exp:g})", fg='#2E7D32')
            self._inv_quick_qty.delete(0, tk.END)
            self._inv_quick_qty.focus_set()
        
        self._inv_quick_search = AutocompleteEntry(
            quick_frame, _inv_fetch_goods, _inv_on_select,
            list_font=self.font_small_tuple,
            font=self.font_normal_tuple, width=30)
        self._inv_quick_search.pack(side="left", padx=(5, 10))
        
        tk.Label(quick_frame, text=f"Кол-во:", font=self.font_small_tuple,
                 bg=c['frame_bg'], fg=c['fg']).pack(side="left")
        
        self._inv_quick_qty = tk.Entry(quick_frame, font=self.font_normal_tuple, width=8)
        self._inv_quick_qty.pack(side="left", padx=(5, 10))
        self._inv_quick_qty.bind("<Return>", self._inv_quick_submit)
        
        self._btn(quick_frame, text="✅ OK", command=self._inv_quick_submit, style='accent', compact=True).pack(side="left")
        
        self._inv_quick_result = tk.Label(quick_frame, text="", font=self.font_small_tuple,
                                           bg=c['frame_bg'], fg=c['fg'])
        self._inv_quick_result.pack(side="left", padx=15)
        
        # ── Bottom buttons (packed FIRST with side=bottom to always stay visible) ──
        btn_frame = tk.Frame(self._inv_active_frame, bg=c['bg'], pady=8)
        btn_frame.pack(side="bottom", fill="x")
        
        self._btn(btn_frame, text="⬅ Назад", command=self._inv_back_to_list, style='neutral', compact=True).pack(side="left", padx=5)
        
        self._inv_btn_cancel = self._btn(btn_frame, text=f"❌ Отменить ревизию", command=self._inv_cancel_audit, style='danger', compact=True)
        self._inv_btn_cancel.pack(side="left", padx=5)
        
        self._inv_btn_finalize = self._btn(btn_frame, text=f"✅ Завершить ревизию", command=self._inv_finalize_audit, style='success', compact=True)
        self._inv_btn_finalize.pack(side="right", padx=5)
        
        self._inv_btn_apply = self._btn(btn_frame, text=f"📥 Применить изменения", command=self._inv_apply_audit, style='success', compact=True)
        self._inv_btn_apply.pack(side="right", padx=5)

        self._inv_btn_accept_all = self._btn(btn_frame, text=f"✨ Приравнять непросчитанные", command=self._inv_accept_uncounted_items, style='neutral', compact=True)
        self._inv_btn_accept_all.pack(side="right", padx=5)
        
        self._inv_btn_export = self._btn(btn_frame, text="📊 Excel", command=self._inv_export_excel, style='neutral', compact=True)
        self._inv_btn_export.pack(side="right", padx=5)
        
        # ── Filter bar ──
        filter_frame = tk.Frame(self._inv_active_frame, bg=c['bg'], pady=3)
        filter_frame.pack(fill="x")
        
        self._inv_filter_var = tk.StringVar(value="all")
        for val, label in [("all", "Все"), ("uncounted", "Не подсчитано"), 
                           ("counted", "Подсчитано"), ("discrepancies", "Расхождения")]:
            tk.Radiobutton(filter_frame, text=label, variable=self._inv_filter_var, value=val,
                           font=self.font_small_tuple, bg=c['bg'], fg=c['fg'],
                           selectcolor=c['frame_bg'], activebackground=c['bg'],
                           command=self._inv_refresh_items).pack(side="left", padx=8)
        
        self._inv_item_search = tk.Entry(filter_frame, font=self.font_small_tuple, width=20)
        self._inv_item_search.pack(side="right", padx=5)
        self._inv_item_search.bind("<KeyRelease>", lambda e: self._inv_refresh_items())
        tk.Label(filter_frame, text="🔍", bg=c['bg'], fg=c['fg']).pack(side="right")
        
        # ── Items treeview (fills ALL remaining space) ──
        cols = ('code', 'name', 'expected', 'sold', 'adj_expected', 'actual', 'diff', 'diff_money', 'last_counted')
        tree_frame = tk.Frame(self._inv_active_frame, bg=c['bg'])
        tree_frame.pack(fill="both", expand=True)
        
        self._inv_items_tree = ttk.Treeview(tree_frame, columns=cols, show="headings", height=18,
                                             style="Custom.Treeview")
        
        hdrs = {
            'code': ('Код', 90), 'name': ('Наименование', 250), 
            'expected': ('Ожид.', 70), 'sold': ('Продано', 70),
            'adj_expected': ('Скорр.', 70), 'actual': ('Факт.', 70),
            'diff': ('Разн.', 70), 'diff_money': ('Сумма', 90), 'last_counted': ('⏱', 50)
        }
        for col, (header, width) in hdrs.items():
            self._inv_items_tree.heading(col, text=header, 
                command=lambda c=col: self._inv_sort_column(c))
            anchor = "w" if col == "name" else "center"
            self._inv_items_tree.column(col, width=width, anchor=anchor)
        
        scrollbar = AutoScrollbar(tree_frame, orient="vertical", command=self._inv_items_tree.yview)
        self._inv_items_tree.configure(yscrollcommand=scrollbar.set)
        self._inv_items_tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        
        self._inv_items_tree.bind("<Double-1>", self._heading_guard, add="+")
        self._inv_items_tree.bind("<Double-1>", self._inv_on_item_double_click)
        
        self._inv_current_audit_id = None
        self._inv_quick_found_code = None
        self._inv_sort_reverse = {}
        self._inv_last_sort_col = None
    
    # ── Sorting ──────────────────────────────────────────────────────────
    def _inv_sort_column(self, col):
        """Sort treeview by column. Last_counted sorts by most recent first."""
        reverse = self._inv_sort_reverse.get(col, False)
        
        items = [(self._inv_items_tree.set(k, col), k) for k in self._inv_items_tree.get_children()]
        
        # Numeric columns
        num_cols = ('expected', 'sold', 'adj_expected', 'actual', 'diff', 'diff_money')
        if col in num_cols:
            def sort_key(x):
                v = x[0].replace('+', '').replace(' ', '').replace(',', '').replace('—', '')
                try: return float(v) if v else -999999
                except: return -999999
            items.sort(key=sort_key, reverse=reverse)
        elif col == 'last_counted':
            # Sort by counted_at timestamp — most recent first by default
            audit_items = self.audit_manager.get_audit_items(self._inv_current_audit_id) if self._inv_current_audit_id else []
            counted_map = {str(it['id']): it.get('counted_at', '') or '' for it in audit_items}
            items.sort(key=lambda x: counted_map.get(x[1], ''), reverse=not reverse)
        else:
            items.sort(key=lambda x: x[0].lower(), reverse=reverse)
        
        for idx, (_, k) in enumerate(items):
            self._inv_items_tree.move(k, '', idx)
        
        self._inv_sort_reverse[col] = not reverse
        self._inv_last_sort_col = col
    
    # ── View switching ───────────────────────────────────────────────────
    def _inv_show_list(self):
        self._inv_active_frame.pack_forget()
        self._inv_list_frame.pack(fill="both", expand=True)
        if hasattr(self, '_inv_btn_new'):
            self._inv_btn_new.configure(state="normal")
        self._inv_refresh_list()
    
    def _inv_show_active(self, audit_id):
        self._inv_list_frame.pack_forget()
        self._inv_current_audit_id = audit_id
        self._inv_active_frame.pack(fill="both", expand=True)
        if hasattr(self, '_inv_btn_new'):
            self._inv_btn_new.configure(state="disabled")
        self._inv_refresh_active()
    
    def _inv_back_to_list(self):
        self._inv_current_audit_id = None
        self._inv_show_list()
    
    def refresh_inventory_view(self):
        active = self.audit_manager.get_active_audit()
        if active:
            self._inv_show_active(active['id'])
        else:
            self._inv_show_list()
    
    def _inv_refresh_list(self):
        for row in self._inv_tree.get_children():
            self._inv_tree.delete(row)
        
        t = lambda k: get_text(k, self.lang)
        audits = self.audit_manager.get_all_audits(limit=100)
        status_map = {
            'active': ('🟡', t('audit_active')),
            'completed': ('🟢', t('audit_completed')),
            'cancelled': ('⚫', t('audit_cancelled')),
        }
        
        for a in audits:
            st_icon, st_text = status_map.get(a['status'], ('?', a['status']))
            if a['applied']:
                st_text = f"✅ {t('audit_applied')}"
            
            type_text = t('audit_type_full') if a['audit_type'] == 'full' else t('audit_type_filtered')
            progress = f"{a.get('counted_items', 0)}/{a.get('snapshot_total_items', 0)}"
            surplus = f"+{a.get('total_surplus', 0):.0f}" if a.get('total_surplus') else ""
            shortage = f"-{a.get('total_shortage', 0):.0f}" if a.get('total_shortage') else ""
            diff = self.format_amount(a.get('total_difference_money', 0)) if a.get('total_difference_money') else ""
            date_str = a['created_at'][:16] if a.get('created_at') else ''
            created_by = a.get('created_by', '')
            
            self._inv_tree.insert('', 'end', iid=a['id'], values=(
                date_str, created_by, type_text, a.get('snapshot_total_items', 0),
                progress, f"{st_icon} {st_text}", surplus, shortage, diff
            ))
    
    def _inv_refresh_active(self):
        if not self._inv_current_audit_id:
            return
        
        audit = self.audit_manager.get_audit(self._inv_current_audit_id)
        if not audit:
            self._inv_show_list()
            return
        
        t = lambda k: get_text(k, self.lang)
        counted = audit.get('counted_items', 0)
        total = audit.get('snapshot_total_items', 0)
        pct = int(counted / total * 100) if total > 0 else 0
        
        status = t('audit_active') if audit['status'] == 'active' else t('audit_completed')
        self._inv_status_label.config(text=f"📋 {audit['id']}  |  {audit['created_at'][:16]}  |  {status}  |  Создал: {audit.get('created_by', '')}")
        self._inv_progress_label.config(text=f"{t('counted')}: {counted}/{total} ({pct}%)")
        self._inv_progress_bar['maximum'] = total
        self._inv_progress_bar['value'] = counted
        
        is_active = audit['status'] == 'active'
        is_completed = audit['status'] == 'completed'
        is_applied = bool(audit.get('applied'))
        can_conduct = self.has_permission('inventory_conduct')
        
        self._inv_btn_cancel.configure(state="normal" if is_active and can_conduct else "disabled")
        self._inv_btn_finalize.configure(state="normal" if is_active and can_conduct else "disabled")
        self._inv_btn_apply.configure(state="normal" if is_completed and not is_applied and can_conduct else "disabled")
        
        # Disable quick count for non-active or read-only
        qs_state = "normal" if is_active and can_conduct else "disabled"
        self._inv_quick_search.configure(state=qs_state)
        self._inv_quick_qty.configure(state=qs_state)
        
        self._inv_refresh_items()
    
    def _inv_refresh_items(self):
        if not self._inv_current_audit_id:
            return
        
        for row in self._inv_items_tree.get_children():
            self._inv_items_tree.delete(row)
        
        flt = self._inv_filter_var.get()
        search = self._inv_item_search.get().strip()
        
        items = self.audit_manager.get_audit_items(
            self._inv_current_audit_id,
            only_counted=(flt == 'counted'),
            only_uncounted=(flt == 'uncounted'),
            show_discrepancies=(flt == 'discrepancies'),
            search_query=search
        )
        
        c = self.colors
        for item in items:
            actual = item['actual_qty']
            diff = item.get('difference', 0)
            diff_money = item.get('difference_money', 0)
            sold = item.get('sold_during_audit', 0)
            adj = item.get('adjusted_expected', item['expected_qty'])
            counted_at = item.get('counted_at', '')
            
            if actual is None:
                actual_str = "—"
                diff_str = ""
                diff_money_str = ""
                time_str = ""
            else:
                actual_str = f"{actual:g}"
                diff_str = f"{diff:+g}" if diff != 0 else "0"
                diff_money_str = self.format_amount(diff_money) if diff_money != 0 else ""
                time_str = counted_at[11:16] if counted_at else ""
            
            tag = 'surplus' if diff > 0 else ('shortage' if diff < 0 else 'match')
            
            self._inv_items_tree.insert('', 'end', iid=str(item['id']), values=(
                item['good_code'], item['good_name'],
                f"{item['expected_qty']:g}", f"{sold:g}" if sold else "",
                f"{adj:g}", actual_str, diff_str, diff_money_str, time_str
            ), tags=(tag,))
        
        self._inv_items_tree.tag_configure('surplus', foreground='#2E7D32')
        self._inv_items_tree.tag_configure('shortage', foreground='#C62828')
        self._inv_items_tree.tag_configure('match', foreground=c.get('fg', '#333'))
        
        # Re-apply last sort if any
        if hasattr(self, '_inv_last_sort_col') and self._inv_last_sort_col:
            col = self._inv_last_sort_col
            # Temporarily flip reverse so _inv_sort_column flips it back to desired state
            self._inv_sort_reverse[col] = not self._inv_sort_reverse.get(col, False)
            self._inv_sort_column(col)
    
    # ── Actions ──────────────────────────────────────────────────────────
    def _inv_start_new_audit(self):
        t = lambda k: get_text(k, self.lang)
        
        active = self.audit_manager.get_active_audit()
        if active:
            self.show_toast(t('audit_already_active'), "warning")
            self._inv_show_active(active['id'])
            return
        
        # Simple type selection dialog
        # Increased height from 420 to 500 to ensure all fields are visible on all screens
        dialog = self.create_modal_dialog(t('start_new_audit'), width=530, height=500, scrollable=False)
        c = self.colors
        content = dialog.container if hasattr(dialog, 'container') else dialog
        
        type_var = tk.StringVar(value="full")
        
        # Full audit option
        full_frame = tk.Frame(content, bg=c['frame_bg'], padx=15, pady=12, relief="groove", bd=1)
        full_frame.pack(fill="x", padx=15, pady=(15, 5))
        
        tk.Radiobutton(full_frame, text=f"📦 {t('audit_type_full')}", variable=type_var, value="full",
                       font=self.font_normal_tuple, bg=c['frame_bg'], fg=c['fg'],
                       selectcolor=c['bg'], anchor="w").pack(anchor="w")
        tk.Label(full_frame, text="Все товары из базы будут включены в ревизию.",
                 font=self.font_small_tuple, bg=c['frame_bg'], fg=c['fg_secondary'],
                 wraplength=440).pack(anchor="w", padx=20)
        
        # Filtered audit option
        filt_frame = tk.Frame(content, bg=c['frame_bg'], padx=15, pady=12, relief="groove", bd=1)
        filt_frame.pack(fill="x", padx=15, pady=5)
        
        tk.Radiobutton(filt_frame, text=f"🔍 {t('audit_type_filtered')}", variable=type_var, value="filtered",
                       font=self.font_normal_tuple, bg=c['frame_bg'], fg=c['fg'],
                       selectcolor=c['bg'], anchor="w").pack(anchor="w")
        tk.Label(filt_frame, text="Только товары, подходящие под фильтр (по названию/коду).",
                 font=self.font_small_tuple, bg=c['frame_bg'], fg=c['fg_secondary'],
                 wraplength=440).pack(anchor="w", padx=20)
        
        filter_entry = tk.Entry(filt_frame, font=self.font_normal_tuple, width=35)
        filter_entry.pack(anchor="w", padx=20, pady=(5, 0))
        
        # Zero Negatives option
        zero_neg_var = tk.BooleanVar(value=False)
        opts_frame = tk.Frame(content, bg=c['bg'], padx=15, pady=5)
        opts_frame.pack(fill="x")
        
        tk.Checkbutton(opts_frame, text="🗑 Обнулять также и отрицательные остатки", 
                       variable=zero_neg_var, font=self.font_small_tuple,
                       bg=c['bg'], fg=c['fg'], selectcolor=c['bg'],
                       activebackground=c['bg']).pack(anchor="w")
        
        # Notes
        notes_frame = tk.Frame(content, bg=c['bg'], padx=15, pady=8)
        notes_frame.pack(fill="x")
        tk.Label(notes_frame, text="Комментарий (необязательно):", font=self.font_small_tuple,
                 bg=c['bg'], fg=c['fg_secondary']).pack(anchor="w")
        notes_entry = tk.Entry(notes_frame, font=self.font_normal_tuple, width=40)
        notes_entry.pack(anchor="w", pady=3)
        
        def on_create():
            audit_type = type_var.get()
            zero_neg = zero_neg_var.get()
            criteria = {}
            if audit_type == 'filtered':
                search = filter_entry.get().strip()
                if not search:
                    self.show_toast("Введите фильтр для выборочной ревизии", "warning")
                    return
                criteria['search'] = search
            
            # Confirmation for full audit
            if audit_type == 'full':
                neg_warn = "\n• Отрицательные остатки ТАКЖЕ будут обнулены" if zero_neg else ""
                if not messagebox.askyesno("⚠️ Полная ревизия",
                    "Начать полную ревизию?\n\n"
                    "• Все товары будут включены\n"
                    "• Непросчитанные позиции при применении\n"
                    f"  будут ОБНУЛЕНЫ{neg_warn}\n\n"
                    "Продолжить?"):
                    return
            
            user_name = self.current_user.get('name', '') if self.current_user else ''
            device_name = getattr(self, 'device_name', '')
            audit = self.audit_manager.create_audit(
                audit_type=audit_type, filter_criteria=criteria,
                created_by=user_name, created_device=device_name,
                notes=notes_entry.get().strip(),
                zero_negatives=zero_neg)
            
            if audit:
                dialog.destroy()
                self.show_toast(f"Ревизия начата: {audit['snapshot_total_items']} товаров", "inventory")
                self._inv_show_active(audit['id'])
            else:
                self.show_toast(t('audit_no_items'), "error")
        
        self._add_dialog_button(dialog, "▶ Начать", on_create, style='accent')
        self._add_dialog_button(dialog, "Отмена", dialog.destroy, style='neutral')
    
    def _inv_on_item_double_click(self, event=None):
        sel = self._inv_items_tree.selection()
        if not sel:
            return
        
        audit = self.audit_manager.get_audit(self._inv_current_audit_id)
        if not audit or audit['status'] != 'active' or not self.has_permission('inventory_conduct'):
            return
        
        item_id = sel[0]
        values = self._inv_items_tree.item(item_id, 'values')
        good_code = values[0]
        good_name = values[1]
        current_actual = values[5]
        
        initial = current_actual if current_actual != "—" else ""
        result = self.ask_string_dialog(
            "Ввод количества",
            f"{good_name}\n({good_code})\n\nФактическое количество:",
            initial=initial)
        
        if result is not None:
            try:
                qty = float(result.replace(',', '.'))
                if qty < 0:
                    raise ValueError
            except ValueError:
                self.show_toast("Некорректное количество", "error")
                return
            
            user_name = self.current_user.get('name', '') if self.current_user else ''
            self.audit_manager.update_item_count(
                self._inv_current_audit_id, good_code, qty, counted_by=user_name)
            self._inv_refresh_active()
    
    def _inv_quick_submit(self, event=None):
        # Always derive the code from the current search text when it has the
        # "code | name" format — prevents recording the fact against a stale
        # previously selected good when the user typed a different one.
        search_text = self._inv_quick_search.get().strip()
        if '|' in search_text:
            self._inv_quick_found_code = search_text.split('|')[0].strip()
        if not getattr(self, '_inv_quick_found_code', ''):
            return
        
        qty_str = self._inv_quick_qty.get().strip()
        if qty_str == '':
            return
        
        try:
            qty = float(qty_str.replace(',', '.'))
            if qty < 0:
                raise ValueError
        except ValueError:
            self.show_toast("Некорректное количество", "error")
            return
        
        code = self._inv_quick_found_code
        user_name = self.current_user.get('name', '') if self.current_user else ''
        
        result = self.audit_manager.update_item_count(
            self._inv_current_audit_id, code, qty, counted_by=user_name)
        
        self.show_toast(f"✅ {code}: {qty:g}", "success", duration=1500)
        
        self._inv_quick_search.delete(0, tk.END)
        self._inv_quick_qty.delete(0, tk.END)
        self._inv_quick_result.config(text="")
        self._inv_quick_found_code = None
        self._inv_quick_search.focus_set()
        self._inv_refresh_active()

    def _inv_accept_uncounted_items(self):
        """Mass-fill uncounted items as correct (match system)."""
        if not self._inv_current_audit_id: return
        
        audit = self.audit_manager.get_audit(self._inv_current_audit_id)
        if not audit or audit['status'] != 'active': return
        
        items = self.audit_manager.get_audit_items(self._inv_current_audit_id, only_uncounted=True)
        uncounted = len(items)
        if uncounted == 0:
            self.show_toast("Все товары уже просчитаны", "info")
            return
            
        if not messagebox.askyesno("✨ Приравнять непросчитанные", 
            f"Вы хотите отметить оставшиеся {uncounted} позиций как верные?\n\n"
            "• Для них будет выставлено 'Факт = Ожидаемо'\n"
            "• Разница по ним станет нулевой\n\n"
            "Продолжить?"):
            return
            
        user_name = self.current_user.get('name', '') if self.current_user else ''
        count = self.audit_manager.set_uncounted_to_expected(self._inv_current_audit_id, user_name)
        self.show_toast(f"✅ Обновлено {count} позиций", "success")
        self._inv_refresh_active()

    def _inv_finalize_audit(self):
        """Finalize AND apply audit in one step."""
        if not self._inv_current_audit_id:
            return
            
        try:
            audit = self.audit_manager.get_audit(self._inv_current_audit_id)
            if not audit: return
            
            # Device Lock Check
            curr_device = getattr(self, 'device_name', '')
            created_device = audit.get('created_device') or ''
            if created_device and curr_device and created_device != curr_device:
                messagebox.showerror("Ошибка устройства", 
                    f"Эту ревизию может завершить только устройство: {created_device}\n"
                    f"Ваше устройство: {curr_device}")
                return
                
            # Use SQL-optimized summary (Fast!)
            summary = self.audit_manager.get_audit_active_summary(self._inv_current_audit_id)
            if not summary: return
            
            counted = summary['counted']
            total = summary['total_items']
            uncounted = summary['uncounted']
            is_full = audit.get('audit_type') == 'full'
            sum_shortage = summary['potential_shortage_money']
            
            if uncounted > 0:
                if is_full:
                    zero_neg = bool(audit.get('zero_negatives'))
                    neg_clause = " (включая минусовые остатки)" if zero_neg else ""
                    msg = (f"⚠️ ВНИМАНИЕ: {uncounted} товаров НЕ ПРОСЧИТАНО.\n\n"
                           f"Так как это ПОЛНАЯ ревизия, эти товары будут ОБНУЛЕНЫ{neg_clause}.\n"
                           f"Общая сумма списания (примерно): {self.format_amount(sum_shortage)}\n\n"
                           f"Вы уверены, что хотите продолжить? (Или нажмите 'Отмена' и используйте 'Приравнять непросчитанные')")
                else:
                    msg = (f"Завершить ревизию?\n\n"
                           f"Подсчитано {counted} из {total}. Непросчитанные товары ({uncounted} шт) изменены НЕ БУДУТ.\n\n"
                           f"Продолжить?")
                
                if not messagebox.askyesno("Завершение ревизии", msg):
                    return
            else:
                if not messagebox.askyesno("Применить ревизию", "Все товары просчитаны. Применить изменения?"):
                    return
            
            user_name = self.current_user.get('name', '') if self.current_user else ''
            device_name = getattr(self, 'device_name', '')
            
            # Step 1: Finalize
            result = self.audit_manager.finalize_audit(self._inv_current_audit_id, 
                                                     completed_by=user_name,
                                                     completed_device=device_name)
            if isinstance(result, dict) and result.get('error') == 'device_mismatch':
                messagebox.showerror("Ошибка устройства", 
                    f"Эту ревизию может завершить только устройство: {result.get('required_device')}")
                return
                
            if not result:
                self.show_toast("Ошибка завершения", "error")
                return
            
            # Step 2: Apply immediately
            updated = self.audit_manager.apply_audit(self._inv_current_audit_id, 
                                                   applied_by=user_name,
                                                   applied_device=device_name)
            if updated is not False:
                self.show_toast(f"✅ Ревизия завершена: {updated} товаров обновлено", "inventory")
                self._inv_show_summary(self._inv_current_audit_id)
                self._inv_refresh_active()
                if hasattr(self, 'refresh_goods_list'):
                    self.refresh_goods_list()
                
                # Sync immediately
                if hasattr(self, 'sync_engine') and self.sync_engine:
                    self.sync_engine.request_sync()
        except Exception as e:
            messagebox.showerror("Ошибка ревизии", f"Произошла ошибка при завершении ревизии:\n{str(e)}")
            import traceback
            traceback.print_exc()

    def _inv_apply_audit(self):
        """Manual apply — for already-completed audits."""
        if not self._inv_current_audit_id: return
        if not messagebox.askyesno("Применить", "Применить результаты ревизии к остаткам?"): return
        
        user_name = self.current_user.get('name', '') if self.current_user else ''
        device_name = getattr(self, 'device_name', '')
        
        updated = self.audit_manager.apply_audit(self._inv_current_audit_id, 
                                               applied_by=user_name,
                                               applied_device=device_name)
        if updated == "device_mismatch":
             messagebox.showerror("Ошибка устройства", "Эту ревизию может применить только устройство, которое её начало.")
             return
             
        if updated is not False:
            self.show_toast(f"Применено: {updated} товаров", "success")
            self._inv_refresh_active()
            if hasattr(self, 'refresh_goods_list'): self.refresh_goods_list()
        else:
            self.show_toast("Ошибка применения", "error")

    def _inv_cancel_audit(self):
        if not self._inv_current_audit_id:
            return
        if not messagebox.askyesno("Отмена ревизии",
                get_text('audit_confirm_cancel', self.lang)):
            return
        self.audit_manager.cancel_audit(self._inv_current_audit_id)
        self.show_toast("Ревизия отменена", "info")
        self._inv_back_to_list()
    
    def _inv_on_audit_select(self, event=None):
        sel = self._inv_tree.selection()
        if not sel:
            return
        audit = self.audit_manager.get_audit(sel[0])
        if not audit:
            return
        if audit['status'] in ('completed', 'cancelled') or audit.get('applied'):
            self._inv_show_summary(sel[0])
        else:
            self._inv_show_active(sel[0])
    
    def _inv_delete_selected(self):
        sel = self._inv_tree.selection()
        if not sel:
            return
        
        audit = self.audit_manager.get_audit(sel[0])
        if not audit:
            return
        
        if audit.get('applied'):
            # Only admin/superadmin can delete an applied audit (rollback changes)
            user_role = (self.current_user.get('role', '') if self.current_user else '').lower()
            if user_role not in ['admin', 'superadmin']:
                self.show_toast(
                    "Только администратор может удалить применённую ревизию",
                    "error"
                )
                return
            msg = ("Удалить применённую ревизию?\n\n"
                   "⚠️ Количество товаров будет ОТКАТАНО\n"
                   "с учётом продаж после ревизии.\n\n"
                   "Пример: если до ревизии было 50 шт, ревизия выставила 40, \n"
                   "потом продано 20 — остаток будет 30 (не 20 и не 50).\n\n"
                   "Это действие необратимо!")
        else:
            msg = "Удалить выбранную ревизию?"
        
        if not messagebox.askyesno("Удаление ревизии", msg):
            return
        
        if self.audit_manager.delete_audit(sel[0]):
            self.show_toast("Ревизия удалена" + (" (остатки откатаны)" if audit.get('applied') else ""), "info")
            self._inv_refresh_list()
            if hasattr(self, 'refresh_goods_list'):
                self.refresh_goods_list()
            # Always sync after delete so clients receive AuditQtyRollback
            if hasattr(self, 'sync_engine') and self.sync_engine:
                self.sync_engine.request_sync()
        else:
            self.show_toast("Ошибка удаления", "error")

    
    def _inv_show_summary(self, audit_id):
        """Full-screen-like summary dialog."""
        summary = self.audit_manager.get_audit_summary(audit_id)
        if not summary:
            return
        
        t = lambda k: get_text(k, self.lang)
        c = self.colors
        audit = summary['audit']
        
        # Use larger dialog, non-scrollable for full-width layout
        dialog = self.create_modal_dialog(t('audit_summary'), width=900, height=650, scrollable=False)
        content = dialog.container if hasattr(dialog, 'container') else dialog
        
        # Header
        hdr = tk.Frame(content, bg=c['frame_bg'], padx=15, pady=10)
        hdr.pack(fill="x")
        
        tk.Label(hdr, text=f"📋 Ревизия {audit['id']}", font=self.font_large_tuple,
                 bg=c['frame_bg'], fg=c['fg']).pack(anchor="w")
        
        info_parts = [
            f"📅 Начало: {audit['created_at'][:16]} ({audit.get('created_device', '—')})",
            f"| 👤 Создал: {audit.get('created_by', '—')}"
        ]
        if audit.get('completed_at'):
            info_parts.append(f"| 🏁 Финиш: {audit['completed_at'][:16]} ({audit.get('completed_device', '—')})")
        
        if audit.get('applied'):
            info_parts.append(f"| ✅ Применил: {audit.get('applied_by', '—')} ({audit.get('applied_at', '')[:16]}) на {audit.get('applied_device', '—')}")
        
        tk.Label(hdr, text="  ".join(info_parts), font=self.font_small_tuple,
                 bg=c['frame_bg'], fg=c['fg_secondary']).pack(anchor="w")
        
        # Summary cards - use full width with grid
        cards_frame = tk.Frame(content, bg=c['bg'])
        cards_frame.pack(fill="x", padx=10, pady=10)
        
        card_data = [
            ("📦", "Всего", str(summary.get('total_items', 0)), c['fg']),
            ("✅", "Подсчитано", str(summary.get('counted', 0)), c['fg']),
            ("🟰", "Совпадения", str(summary.get('matches', 0)), '#2E7D32'),
            ("📈", "Излишки", f"+{summary.get('surplus_qty', 0):.0f} шт\n+{self.format_amount(summary.get('surplus_money', 0))}", '#2E7D32'),
            ("📉", "Недостача", f"-{summary.get('shortage_qty', 0):.0f} шт\n-{self.format_amount(summary.get('shortage_money', 0))}", '#C62828'),
            ("💰", "Итого", self.format_amount(summary.get('net_difference_money', 0)), 
             '#C62828' if summary.get('net_difference_money', 0) < 0 else '#2E7D32'),
        ]
        
        for i, (icon, label, value, color) in enumerate(card_data):
            card = tk.Frame(cards_frame, bg=c['bg_tertiary'], padx=12, pady=10, relief="groove", bd=1)
            card.grid(row=0, column=i, padx=4, pady=4, sticky="nsew")
            cards_frame.grid_columnconfigure(i, weight=1)
            
            tk.Label(card, text=f"{icon} {label}", font=self.font_small_tuple,
                     bg=c['bg_tertiary'], fg=c['fg_secondary']).pack(anchor="center")
            tk.Label(card, text=value, font=self.font_small_bold_tuple,
                     bg=c['bg_tertiary'], fg=color).pack(anchor="center")
        
        # Discrepancies table - full width
        tk.Label(content, text="Расхождения:", font=self.font_normal_tuple,
                 bg=c['frame_bg'], fg=c['fg']).pack(anchor="w", padx=10, pady=(5, 3))
        
        disc_frame = tk.Frame(content, bg=c['frame_bg'])
        disc_frame.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        
        disc_cols = ('code', 'name', 'expected', 'sold', 'adj_expected', 'actual', 'diff', 'money')
        disc_tree = ttk.Treeview(disc_frame, columns=disc_cols, show="headings", height=14,
                                  style="Custom.Treeview")
        
        disc_hdrs = [('Код', 80), ('Наименование', 220), ('Ожид.', 65), ('Продано', 65),
                     ('Скорр.', 65), ('Факт.', 65), ('Разница', 70), ('Сумма ₸', 100)]
        for col, (hdr_text, w) in zip(disc_cols, disc_hdrs):
            disc_tree.heading(col, text=hdr_text)
            disc_tree.column(col, width=w, anchor="center" if col != "name" else "w")
        
        disc_sb = AutoScrollbar(disc_frame, orient="vertical", command=disc_tree.yview)
        disc_tree.configure(yscrollcommand=disc_sb.set)
        disc_tree.pack(side="left", fill="both", expand=True)
        disc_sb.pack(side="right", fill="y")
        
        disc_items = self.audit_manager.get_audit_items(audit_id, show_discrepancies=True)
        disc_items.sort(key=lambda x: abs(x.get('difference_money', 0)), reverse=True)
        
        for item in disc_items:
            diff = item.get('difference', 0)
            tag = 'surplus' if diff > 0 else 'shortage'
            disc_tree.insert('', 'end', values=(
                item['good_code'], item['good_name'],
                f"{item['expected_qty']:g}",
                f"{item.get('sold_during_audit', 0):g}",
                f"{item.get('adjusted_expected', item['expected_qty']):g}",
                f"{item['actual_qty']:g}" if item['actual_qty'] is not None else "—",
                f"{diff:+g}", self.format_amount(item.get('difference_money', 0))
            ), tags=(tag,))
        
        disc_tree.tag_configure('surplus', foreground='#2E7D32')
        disc_tree.tag_configure('shortage', foreground='#C62828')
        
        # Completed but not applied audits can be applied right from the summary
        audit_obj = summary['audit']
        self._inv_current_audit_id = audit_id
        if audit_obj.get('status') == 'completed' and not audit_obj.get('applied'):
            if self.has_permission('inventory_conduct'):
                def apply_and_close():
                    self._inv_apply_audit()
                    dialog.destroy()
                self._add_dialog_button(dialog, "📥 Применить изменения", apply_and_close, style='success')
        
        self._add_dialog_button(dialog, "Закрыть", dialog.destroy, style='neutral')
    
    def _inv_export_excel(self):
        if not self._inv_current_audit_id:
            return
        try:
            import pandas as pd
            items = self.audit_manager.get_audit_items(self._inv_current_audit_id)
            if not items:
                self.show_toast("Нет данных", "warning")
                return
            
            data = []
            for it in items:
                data.append({
                    'Код': it['good_code'], 'Наименование': it['good_name'],
                    'Штрихкод': it.get('good_barcode', ''),
                    'Цена': int(round(it.get('sale_price', 0) or 0)),
                    'Ожидаемое': it['expected_qty'],
                    'Продано': it.get('sold_during_audit', 0),
                    'Скорр.': it.get('adjusted_expected', 0),
                    'Фактическое': it.get('actual_qty', ''),
                    'Разница': it.get('difference', 0) if it.get('actual_qty') is not None else '',
                    'Сумма ₸': it.get('difference_money', 0) if it.get('actual_qty') is not None else '',
                })
            
            df = pd.DataFrame(data)
            filepath = filedialog.asksaveasfilename(
                defaultextension=".xlsx", filetypes=[("Excel", "*.xlsx")],
                initialfile=f"Ревизия_{self._inv_current_audit_id}.xlsx")
            if filepath:
                df.to_excel(filepath, index=False, sheet_name="Ревизия")
                self.show_toast(f"Экспорт: {filepath}", "success")
        except Exception as e:
            self.show_toast(f"Ошибка экспорта: {e}", "error")
