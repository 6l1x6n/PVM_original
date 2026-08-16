# -*- coding: utf-8 -*-
"""
PVM.core - POS Tab Mixin
==========================
Cash register, cart, checkout, payment, partner selection, quick items.
"""

import time
import queue
import re
import threading
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog

import settings
from ui_lang import get_text
from ui_dialogs import AutoScrollbar, AutocompleteEntry


class POSTabMixin:
    """POS (cash register) tab methods for GreenLeafApp."""

    @property
    def cart(self):
        return self.pos_carts[self.pos_cart_idx]

    @cart.setter
    def cart(self, value):
        self.pos_carts[self.pos_cart_idx] = value

    @property
    def current_partner(self):
        return self.pos_cart_partners[self.pos_cart_idx]

    @current_partner.setter
    def current_partner(self, value):
        self.pos_cart_partners[self.pos_cart_idx] = value

    def create_pos_tab(self):
        """Create the POS/Cash Register tab like GBS.Market."""
        c = self.colors
        
        # Main container: the cart gets the flexible space, while checkout stays
        # in a calm, fixed-width panel on the right.
        main_frame = tk.Frame(self.pos_frame, bg=c['bg'])
        main_frame.pack(fill="both", expand=True, padx=8, pady=8)
        main_frame.grid_columnconfigure(0, weight=1)
        main_frame.grid_columnconfigure(1, weight=0, minsize=286)
        main_frame.grid_rowconfigure(0, weight=1)

        left_frame = tk.Frame(main_frame, bg=c['bg'])
        left_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        left_frame.grid_rowconfigure(1, weight=1)
        left_frame.grid_columnconfigure(0, weight=1)

        checkout_panel = tk.Frame(main_frame, bg=c['frame_bg'],
                                  highlightthickness=1, highlightbackground=c['border'])
        checkout_panel.grid(row=0, column=1, sticky="nsew")
        # Fixed width: the panel must not shrink when the partner card content
        # changes. Children stretch to fill it, so no empty zone appears.
        checkout_panel.grid_propagate(False)
        checkout_panel.config(width=310)
        checkout_panel.grid_columnconfigure(0, weight=1)
        
        # Search bar
        search_frame = tk.Frame(left_frame, bg=c['frame_bg'], padx=10, pady=8,
                                highlightthickness=1, highlightbackground=c['border'])
        search_frame.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        
        def fetch_pos_goods(query):
            results = self.goods_manager.search_goods(query)
            res_list = []
            for g in results:
                qty = g.get('quantity', 0)
                res_list.append(f"{g['code']} | {g['name']} | В наличии: {qty} | {self.format_amount(g['sale_price'])}₸")
            return res_list
            
        def select_pos_good(text):
            code = text.split('|')[0].strip()
            self.pos_add_by_code(code)
            
        self.pos_search_entry = self._build_search_bar(
            search_frame, c['frame_bg'], entry_cls=AutocompleteEntry,
            get_results_callback=fetch_pos_goods, on_select_callback=select_pos_good,
            list_font=self.font_normal_tuple, target_tree=self.cart_tree
        )
        self.pos_search_entry.pack(side="left", padx=0, pady=0, fill="x", expand=True)
        self.pos_search_entry.bind('<Return>', self.pos_on_search, add="+")
        self.pos_search_entry.bind('<Down>', self.pos_focus_cart, add="+")
        self.pos_search_entry.bind('<Up>', self.pos_focus_cart, add="+")
        self.pos_search_entry.focus_set()
        
        # Cart table
        cart_frame = tk.LabelFrame(left_frame, text=f"  {get_text('cart', self.lang)}  ",
                                  font=self.font_bold_tuple, bg=c['frame_bg'], fg=c['fg'],
                                  padx=6, pady=6, bd=1, relief="solid")
        cart_frame.grid(row=1, column=0, sticky="nsew")

        # Custom larger style for cart tree
        cart_style = ttk.Style()
        cart_font = (self.font_family, self.font_small + 1)
        cart_style.configure('Cart.Treeview', font=cart_font, rowheight=self.font_small + 12)
        cart_style.configure('Cart.Treeview.Heading', font=(self.font_family, self.font_small + 1, "bold"))

        columns = ('barcode', 'code', 'name', 'qty', 'price', 'discount', 'sum', 'pv')
        headers = [get_text('barcode', self.lang), get_text('code', self.lang), get_text('name', self.lang),
                   get_text('quantity', self.lang), get_text('price', self.lang),
                   get_text('discount_pct', self.lang), get_text('sum', self.lang), 'ПВ']

        self.cart_tree = ttk.Treeview(cart_frame, columns=columns, show='headings', height=15, style='Cart.Treeview')

        col_widths = [105, 70, 200, 38, 60, 48, 70, 38]
        for col, header, width in zip(columns, headers, col_widths):
            self.cart_tree.heading(col, text=header)
            self.cart_tree.column(col, width=width, anchor='center' if col not in ('name', 'barcode', 'code') else 'w',
                                  stretch=(col == 'name'))
        
        self.setup_treeview_sorting(self.cart_tree, columns, numeric_cols=['qty', 'price', 'discount', 'sum', 'pv'])
        self.setup_universal_navigation(self.cart_tree, lambda: self.pos_edit_qty_dialog(None), enable_multi_select=True)
        self.cart_tree.bind('<Button-1>', self.prevent_treeview_resize)
        
        scrollbar = AutoScrollbar(cart_frame, orient="vertical", command=self.cart_tree.yview)
        self.cart_tree.configure(yscrollcommand=scrollbar.set)
        self.cart_tree.pack(side="left", fill="both", expand=True, padx=2, pady=2)
        scrollbar.pack(side="right", fill="y")
        
        self.bind_mousewheel(self.cart_tree)
        
        self.cart_tree.bind('<Delete>', lambda e: self.pos_remove_item())
        self.cart_tree.bind('<BackSpace>', lambda e: self.pos_remove_item() or "break")
        
        # Cart tabs bar (replaces summary line)
        cart_tab_bar = tk.Frame(left_frame, bg=c['frame_bg'], height=34)
        cart_tab_bar.grid(row=2, column=0, sticky="ew", pady=(6, 0))
        cart_tab_bar.pack_propagate(False)
        self.pos_cart_tab_frame = cart_tab_bar
        self.pos_cart_tab_inner = tk.Frame(cart_tab_bar, bg=c['frame_bg'])
        self.pos_cart_tab_inner.pack(side="left", fill="x", expand=True)
        tk.Button(cart_tab_bar, text="  +  ", font=("Arial", 11, "bold"), width=3,
                  relief="flat", bg=c['bg_tertiary'], fg=c['fg'],
                  activebackground=c['accent'], cursor="hand2",
                  command=self._add_new_cart).pack(side="right", padx=(2, 0))
        

        # ── RIGHT PANEL: quick items, partner and fixed checkout actions ─────
        checkout_panel.grid_rowconfigure(4, weight=1)

        quick_frame = tk.LabelFrame(checkout_panel, text=f"  {get_text('quick_items', self.lang)}  ",
                                    font=self.font_small_bold_tuple, bg=c['frame_bg'], fg=c['fg'],
                                    padx=7, pady=7, bd=1, relief="solid")
        quick_frame.grid(row=2, column=0, sticky="ew", padx=10, pady=(0, 6))

        self.quick_grid = tk.Frame(quick_frame, bg=c['frame_bg'])
        self.quick_grid.pack(fill="both", expand=True, padx=1, pady=1)

        self.quick_buttons = []
        self.create_quick_buttons()

        partner_card = tk.Frame(checkout_panel, bg=c['bg_secondary'], padx=10, pady=8,
                                highlightthickness=1, highlightbackground=c['border'])
        partner_card.grid(row=3, column=0, sticky="ew", padx=10, pady=(0, 6))
        self.pos_partner_card = partner_card
        partner_header = tk.Frame(partner_card, bg=c['bg_secondary'])
        partner_header.pack(fill="x")
        self.pos_partner_badge = tk.Label(partner_header, text="", font=self.font_bold_tuple, width=2,
                                          bg=c['bg_tertiary'], fg='white', padx=4, pady=2)
        self.pos_partner_badge.pack(side="left")
        name_col = tk.Frame(partner_header, bg=c['bg_secondary'])
        name_col.pack(side="left", fill="x", expand=True, padx=(8, 0))
        self.pos_partner_name = tk.Label(name_col, text="Покупатель не выбран",
                                         font=self.font_small_bold_tuple, bg=c['bg_secondary'], fg=c['fg_muted'],
                                         anchor="w", justify="left")
        self.pos_partner_name.pack(fill="x")
        self.pos_partner_clear_btn = self._btn(partner_header, text="×", command=self.pos_clear_partner,
                                               style='neutral', compact=True, width=2, cursor='hand2')
        self.pos_partner_clear_btn.pack(side="right", padx=(4, 0))
        self.pos_partner_details = tk.Label(partner_card, text="Скидка будет применена автоматически",
                                            font=self.font_small_tuple, bg=c['bg_secondary'], fg=c['fg_muted'],
                                            anchor="w", justify="left")
        self.pos_partner_details.pack(fill="x", pady=(6, 5))
        self.pos_partner_action_btn = self._btn(partner_card, text="＋ Добавить покупателя",
                                                command=self.pos_select_partner, style='neutral', compact=True,
                                                cursor='hand2')
        self.pos_partner_action_btn.pack(fill="x")

        # This spacer absorbs all extra height, keeping totals and actions at the bottom.
        tk.Frame(checkout_panel, bg=c['frame_bg']).grid(row=4, column=0, sticky="nsew")

        totals_frame = tk.Frame(checkout_panel, bg=c['bg_secondary'], padx=12, pady=10,
                                highlightthickness=1, highlightbackground=c['border'])
        totals_frame.grid(row=5, column=0, sticky="ew", padx=10, pady=(0, 6))
        tk.Label(totals_frame, text=get_text('total', self.lang).upper(),
                 font=self.font_small_bold_tuple, bg=c['bg_secondary'], fg=c['fg_muted']).pack(anchor="w")
        total_row = tk.Frame(totals_frame, bg=c['bg_secondary'])
        total_row.pack(fill="x", pady=(2, 8))
        self.pos_total_label = tk.Label(total_row, text="0,00", font=self.font_title_tuple,
                                        bg=c['bg_secondary'], fg=c['accent'])
        self.pos_total_label.pack(side="left")
        tk.Label(total_row, text=" ₸", font=self.font_large_tuple,
                 bg=c['bg_secondary'], fg=c['accent']).pack(side="left")
        details_row = tk.Frame(totals_frame, bg=c['bg_secondary'])
        details_row.pack(fill="x")
        self.pos_total_pv_label = tk.Label(details_row, text="ПВ: 0", font=self.font_small_bold_tuple,
                                           bg=c['bg_secondary'], fg=c['success'])
        self.pos_total_pv_label.pack(side="left")
        self.pos_subtotal = tk.Label(details_row, text="0,00", font=self.font_small_tuple,
                                     bg=c['bg_secondary'], fg=c['fg_secondary'])
        self.pos_subtotal.pack(side="right")
        tk.Label(details_row, text=f"{get_text('subtotal', self.lang)}: ", font=self.font_small_tuple,
                 bg=c['bg_secondary'], fg=c['fg_muted']).pack(side="right")

        row2 = tk.Frame(checkout_panel, bg=c['frame_bg'])
        row2.grid(row=6, column=0, sticky="ew", padx=10, pady=(0, 5))

        self._btn(row2, text="+", command=self.pos_increase_qty, style='accent', width=3).pack(side="left", padx=1)
        self._btn(row2, text="-", command=self.pos_decrease_qty, style='accent', width=3).pack(side="left", padx=1)
        self._btn(row2, text="🗑️", command=self.pos_remove_item, style='danger', width=3).pack(side="left", padx=1)

        bot_label = tk.Label(checkout_panel, textvariable=self.live_bot_status_var,
                             font=self.font_small_bold_tuple, bg=c['frame_bg'], fg=c['accent'])
        bot_label.grid(row=7, column=0, sticky="w", padx=14, pady=(0, 5))

        self.pos_partner_var = tk.BooleanVar(value=False)

        btn_frame = tk.Frame(checkout_panel, bg=c['frame_bg'])
        btn_frame.grid(row=8, column=0, sticky="ew", padx=10, pady=(0, 8))
        self.pos_checkout_btn = self._btn(btn_frame, text=get_text('checkout', self.lang),
                                          command=self.pos_checkout, style='success', cursor='hand2')
        self.pos_checkout_btn.pack(side="top", fill="x", pady=(0, 6))
        self.pos_cancel_btn = self._btn(btn_frame, text=get_text('clear_cart', self.lang),
                                        command=self.pos_clear_cart, style='danger', cursor='hand2')
        self.pos_cancel_btn.pack(side="top", fill="x")
        self.pos_refresh_partner_card()
        # Wheel + touch-drag pan over any free zone scrolls the cart
        # (bound at the end — all panels must already exist)
        self.enable_scroll_target(main_frame, self.cart_tree)

    def pos_focus_cart(self, event=None):
        """Move focus to cart and select first item if available."""
        # Don't steal focus if autocomplete is open
        if hasattr(self, 'pos_search_entry') and self.pos_search_entry and hasattr(self.pos_search_entry, 'listbox_window') and self.pos_search_entry.listbox_window:
            return
        
        if self.cart:
            self.cart_tree.focus_set()
            children = self.cart_tree.get_children()
            if children:
                self.cart_tree.selection_set(children[0])
                self.cart_tree.focus(children[0])

    def pos_on_search(self, event=None):
        """Handle manual barcode entry (without selecting from dropdown)."""
        query = self.pos_search_entry.get().strip()
        if query:
            # If it's a direct barcode scan, it usually hits <Return> immediately.
            self.pos_add_by_code(query)
            # Access underlying entry directly or via method
            if hasattr(self.pos_search_entry, 'entry'):
                self.pos_search_entry.entry.delete(0, tk.END)
            elif hasattr(self.pos_search_entry, 'delete'):
                self.pos_search_entry.delete(0, tk.END)
                
            if hasattr(self.pos_search_entry, 'hide_listbox'):
                self.pos_search_entry.hide_listbox()

    def pos_add_by_code(self, code):
        """Add product to cart by code."""
        if not code: return

        now = time.time()
        last = getattr(self, '_pos_last_code_time', 0)
        last_code = getattr(self, '_pos_last_code', '')
        if code == last_code and now - last < 0.5:
            return
        self._pos_last_code = code
        self._pos_last_code_time = now
        
        # Add basic logging to trace adding issues
        # print(f"DEBUG: pos_add_by_code called with '{code}'")
        
        _, good_obj = self.goods_manager.get_good(code)
        if not good_obj:
            results = self.goods_manager.search_goods(code)
            if len(results) == 1:
                good_obj = results[0]
            elif results:
                names = ", ".join(f"{g['code']} — {g['name']}" for g in results[:3])
                messagebox.showwarning(
                    get_text('product_not_found', self.lang),
                    f"'{code}'\n\nНайдено несколько товаров ({len(results)}). Уточните поиск:\n{names}")
                return
            else:
                # If no good found, maybe it was a partial/corrupt barcode scan
                messagebox.showwarning(get_text('product_not_found', self.lang), f"'{code}'")
                return
        
        if not good_obj.get('code'):
            print(f"ERROR: Found good object for '{code}' but it has no code: {good_obj}")
            return
        
        # Check if already in cart
        existing_idx = None
        for i, item in enumerate(self.cart):
            if item['code'] == good_obj['code']:
                existing_idx = i
                break

        if existing_idx is not None:
            self.cart[existing_idx]['quantity'] += 1
            self.cart[existing_idx]['sum'] = self.cart[existing_idx]['quantity'] * self.cart[existing_idx]['price'] * (1 - self.cart[existing_idx]['discount']/100)
            self.pos_refresh_cart()
            # AUTO-SELECTION: find the item in tree and select it
            for child in self.cart_tree.get_children():
                if self.cart_tree.item(child)['values'][0] == good_obj['code']:
                    self.cart_tree.selection_set(child)
                    self.cart_tree.see(child)
                    break
            self.pos_search_entry.delete(0, tk.END)
            return
        
        # New item
        discount = 50 if self.current_partner else 0
        item = {
            'code': good_obj['code'],
            'barcode': good_obj.get('barcode', ''),
            'category': 'Товары',
            'name': good_obj['name'],
            'price': good_obj['sale_price'],
            'quantity': 1,
            'pv': good_obj.get('pv', 0),
            'discount': discount,
            'sum': good_obj['sale_price'] * (1 - discount/100)
        }
        self.cart.append(item)
        self.pos_refresh_cart()
        
        # AUTO-SELECTION: select the last item (just added)
        children = self.cart_tree.get_children()
        if children:
            last_child = children[-1]
            self.cart_tree.selection_set(last_child)
            self.cart_tree.see(last_child)
            
        self.pos_search_entry.delete(0, tk.END)

    def pos_refresh_cart(self):
        """Refresh cart display and maintain selection."""
        # Remember selection
        sel = self.cart_tree.selection()
        selected_idx = self.cart_tree.index(sel[0]) if sel else None

        for item in self.cart_tree.get_children():
            self.cart_tree.delete(item)
        
        total = total_items = total_discount = total_pv = 0
        
        for i, item in enumerate(self.cart):
            self.cart_tree.insert('', 'end', values=(
                item.get('barcode', ''), item['code'], item['name'],
                item['quantity'], self.format_amount(item['price']),
                f"{item['discount']}", self.format_amount(item['sum']),
                item.get('pv', 0)
            ), tags=(str(i),))
            total += item['sum']
            total_items += item['quantity']
            total_pv += item.get('pv', 0) * item['quantity']
            total_discount += item['price'] * item['quantity'] * item['discount'] / 100
        
        subtotal = total + total_discount
        self.pos_total_label.config(text=self.format_amount(total))
        self.pos_total_pv_label.config(text=f"Итого ПВ: {total_pv:g}")
        self.pos_subtotal.config(text=self.format_amount(subtotal))
        self._rebuild_cart_tabs()
        summary = f"Товаров: {total_items}  |  Наименований: {len(self.cart)}  |  Скидка: {self.format_amount(total_discount)}"
        if hasattr(self, 'status_bar') and self.status_bar and self.status_bar.winfo_exists():
            current = self.status_bar.cget('text')
            if '| Продажа' in current:
                base = current.split('  |  Товаров')[0] if '  |  Товаров' in current else current
                self.status_bar.config(text=f"{base}  |  {summary}")
        
        # Restore selection — select item above if original index is now out of bounds
        if selected_idx is not None:
            children = self.cart_tree.get_children()
            if children:
                restore_idx = min(selected_idx, len(children) - 1)
                new_sel = children[restore_idx]
                self.cart_tree.selection_set(new_sel)
                self.cart_tree.see(new_sel)

    def pos_refresh_partner_card(self):
        """Render the selected buyer as a compact card in the checkout panel."""
        partner = self.current_partner
        self.pos_partner_var.set(partner is not None)
        c = self.colors
        if partner:
            name = partner.get('name', 'Покупатель')
            pid = partner.get('id', '')
            display_name = name[len(pid):].strip() if pid and name.startswith(pid) else name
            details = [f"🆔 ID: {pid}"] if pid else ["🆔 ID: —"]
            if partner.get('phone'):
                details.append(f"📞 {partner['phone']}")
            self.pos_partner_badge.config(text=(display_name[:1].upper() if display_name else '?'), bg=c['accent'])
            self.pos_partner_name.config(text=display_name, fg=c['fg'])
            self.pos_partner_details.config(text="\n".join(details), fg=c['fg_secondary'])
            self.pos_partner_action_btn.config(text="Изменить покупателя", command=self.pos_select_partner)
            self.pos_partner_clear_btn.config(state='normal')
            self.pos_partner_card.config(highlightbackground=c['accent'], highlightthickness=2)
        else:
            self.pos_partner_badge.config(text="", bg=c['bg_tertiary'])
            self.pos_partner_name.config(text="Покупатель не выбран", fg=c['fg_muted'])
            self.pos_partner_details.config(text="Скидка будет применена автоматически", fg=c['fg_muted'])
            self.pos_partner_action_btn.config(text="＋ Добавить покупателя", command=self.pos_select_partner)
            self.pos_partner_clear_btn.config(state='disabled')
            self.pos_partner_card.config(highlightbackground=c['border'], highlightthickness=1)

    def pos_clear_partner(self):
        """Remove the buyer from the active cart and reset its discounts."""
        self.current_partner = None
        self.pos_partner_var.set(False)
        self.pos_refresh_partner_card()
        for item in self.cart:
            item['discount'] = 0
            item['sum'] = item['quantity'] * item['price']
        self.pos_refresh_cart()
        self.pos_search_entry.delete(0, tk.END)

    def pos_select_partner(self):
        """Show partner selection dialog with search and add new."""
        c = self.colors
        w = int(580 * self.interface_scale)
        h = int(500 * self.interface_scale)
        
        dialog = tk.Toplevel(self.master)
        dialog.resizable(False, False)
        dialog.title(get_text('select_partner', self.lang))
        dialog.withdraw()
        dialog.configure(bg=c['bg'], highlightbackground=c['accent'], highlightthickness=1)
        dialog.transient(self.master)
        dialog.grab_set()
        dialog.resizable(True, True)
        
        # Center it
        self._center_window(dialog, w, h)
        
        # ── BOTTOM: Confirm always visible ──────────────────────────────────
        btn_frame = tk.Frame(dialog, bg=c['bg'], pady=10)
        btn_frame.pack(side="bottom", fill="x", padx=15)
        
        def on_select(event=None):
            sel = listbox.curselection()
            if sel and filtered_partners:
                self.current_partner = filtered_partners[sel[0]]
                self.pos_partner_var.set(True)
                self.pos_refresh_partner_card()
                for item in self.cart:
                    item['discount'] = 50
                    item['sum'] = item['quantity'] * item['price'] * 0.5
                self.pos_refresh_cart()
                self.pos_search_entry.delete(0, tk.END)
                dialog.destroy()
        
        self._btn(btn_frame, text=get_text('confirm', self.lang), command=on_select, style='success', height=2, cursor='hand2').pack(fill="x")
        
        # ── TOP: Search bar ──────────────────────────────────────────────────
        search_frame = tk.Frame(dialog, bg=c['bg'], pady=8)
        search_frame.pack(side="top", fill="x", padx=15)
        
        search_var = tk.StringVar()
        search_entry = self._build_search_bar(search_frame, c['bg'], textvariable=search_var)
        
        def add_new_partner():
            search_text = search_var.get().strip()
            dialog.destroy()
            self.pos_partner_var.set(False)
            self.open_add_partner_dialog(search_text)
        
        add_btn = self._btn(search_frame, text="＋", command=add_new_partner, style='success', width=3, cursor='hand2')
        add_btn.pack(side="right", padx=5)
        
        # ── MIDDLE: Listbox fills all remaining space ────────────────────────
        list_frame = tk.Frame(dialog, bg=c['bg'])
        list_frame.pack(side="top", fill="both", expand=True, padx=15, pady=(0, 5))
        
        sb = AutoScrollbar(list_frame)
        sb.pack(side="right", fill="y")
        
        listbox = tk.Listbox(list_frame, font=self.font_normal_tuple,
                             yscrollcommand=sb.set,
                             bg=c.get('list_bg', c['bg_secondary']), fg=c['fg'],
                             selectbackground=c['accent'], selectforeground='white',
                             relief='flat', bd=0, activestyle='none')
        listbox.pack(side="left", fill="both", expand=True)
        sb.config(command=listbox.yview)
        self.bind_mousewheel(listbox)
        
        filtered_partners = []
        
        def refresh_list(*args):
            nonlocal filtered_partners
            listbox.delete(0, tk.END)
            current_partners = self.partners_manager.get_all_partners()
            query = search_var.get().lower()
            filtered_partners = [p for p in current_partners
                                 if (query in p['name'].lower()
                                 or query in p.get('phone', '').lower()
                                 or query in p.get('id', '').lower())
                                 and not p.get('is_blocked', 0)]
            for p in filtered_partners:
                pid = p.get('id', '')
                display = p['name']
                if pid and display.startswith(pid):
                    display = display[len(pid):].strip()
                if pid and len(pid) <= 20 and not self._is_uuid(pid):
                    listbox.insert(tk.END, f"  {pid}  {display}  ({p.get('phone', '')})")
                else:
                    listbox.insert(tk.END, f"  {display}  ({p.get('phone', '')})")
        
        search_var.trace_add('write', refresh_list)
        refresh_list()
        
        def on_search_enter(event):
            if listbox.size() > 0:
                listbox.selection_clear(0, tk.END)
                listbox.selection_set(0)
                on_select()
            else:
                add_new_partner()
        
        def on_entry_down(event):
            if listbox.size() > 0:
                listbox.focus_set()
                listbox.selection_set(0)
        
        def on_listbox_up(event):
            if listbox.curselection() and listbox.curselection()[0] == 0:
                search_entry.focus_set()
                return "break"

        listbox.bind('<Double-Button-1>', on_select)
        listbox.bind('<Return>', lambda e: on_select())
        listbox.bind('<Up>', on_listbox_up)
        search_entry.bind('<Return>', on_search_enter)
        search_entry.bind('<Down>', on_entry_down)
        dialog.bind('<Escape>', lambda e: dialog.destroy())
        
        dialog.deiconify()
        search_entry.focus_set()

    def open_add_partner_dialog(self, prefill_name=""):
        """Open dialog to add new partner with optional pre-filled name."""
        c = self.colors
        
        dialog = self.create_modal_dialog(get_text('add_partner', self.lang), width=460, height=470, scrollable=False)
        main = dialog.container
        main.columnconfigure(1, weight=1)
        
        # Name field with hint about ID format
        tk.Label(main, text="ФИО (с ID):", font=self.font_normal_tuple, bg=c['bg']).grid(row=0, column=0, sticky="w", pady=(15, 6), padx=(5, 10))
        name_var = tk.StringVar(value=prefill_name)
        name_entry = tk.Entry(main, textvariable=name_var, font=self.font_normal_tuple)
        name_entry.grid(row=0, column=1, padx=(0, 10), pady=(15, 6), sticky="ew")
        
        tk.Label(main, text="Формат: aa12345678 Имя Фамилия", 
                font=self.font_small_tuple, fg=c['fg_muted'], bg=c['bg']).grid(row=1, column=1, sticky="w", padx=0) # Align with entry
        
        # Phone
        tk.Label(main, text="Телефон:", font=self.font_normal_tuple, bg=c['bg']).grid(row=2, column=0, sticky="w", pady=10, padx=(5, 10))
        phone_var = tk.StringVar()
        tk.Entry(main, textvariable=phone_var, font=self.font_normal_tuple).grid(row=2, column=1, padx=(0, 10), pady=10, sticky="ew")
        
        # Notes
        tk.Label(main, text="Заметка:", font=self.font_normal_tuple, bg=c['bg']).grid(row=3, column=0, sticky="w", pady=6, padx=(5, 10))
        notes_var = tk.StringVar()
        tk.Entry(main, textvariable=notes_var, font=self.font_normal_tuple).grid(row=3, column=1, padx=(0, 10), pady=6, sticky="ew")
        
        validation_lbl = tk.Label(main, text="", font=self.font_small_tuple, fg=c['error'], bg=c['bg'])
        validation_lbl.grid(row=4, column=1, sticky="w", padx=0, pady=(0, 5))
        
        def validate_partner_name(*args):
            val = name_var.get().strip()
            if not val:
                validation_lbl.config(text="")
                return
            valid, pid, display_name, err = self._validate_partner_id_format(val)
            if not valid:
                validation_lbl.config(text="⚠️ " + (err or "Неверный формат"))
            elif self.partners_manager.get_partner(pid):
                validation_lbl.config(text="⚠️ Этот ID уже используется другим партнёром")
            else:
                validation_lbl.config(text="✅ Формат ID корректен", fg=c['success'])
        
        name_var.trace_add('write', validate_partner_name)
        
        tk.Label(main, text="🏷️ " + get_text('partner_discount_info', self.lang),
                font=self.font_small_tuple, fg=c['success'], bg=c['bg']).grid(row=5, column=0, columnspan=2, pady=(10, 5), sticky="ew")
        
        def save_partner():
            name = name_var.get().strip()
            valid, extracted_id, clean_name, err = self._validate_partner_id_format(name)
            
            if not valid:
                self.show_toast(err or "Неверный формат: требуется KZ12345678 Имя Фамилия", "error")
                return
                
            if self.partners_manager.get_partner(extracted_id):
                self.show_toast(f"Партнёр с ID '{extracted_id}' уже существует", "error")
                return
                
            discount = 0.5
            try:
                pid = self.partners_manager.add_partner(
                    name=name, phone=phone_var.get(),
                    email='', notes=notes_var.get(),
                    dob=None, discount=discount,
                    user_name=self._get_user_device_label())
            except ValueError as e:
                self.show_toast(str(e), "error")
                return
            
            if pid and hasattr(self, '_db_manager') and self._db_manager:
                self._db_manager.sync_log.log('partner', extracted_id, 'create', {
                    'id': extracted_id, 'name': name, 'phone': phone_var.get(),
                    'email': '', 'notes': notes_var.get(), 'discount': discount
                })
            
            # Fallback for auto-select if extracted_id failed
            final_id = extracted_id or pid
            new_partner = self.partners_manager.get_partner(final_id)
            
            if new_partner:
                self.current_partner = new_partner
                self.pos_partner_var.set(True)
                self.pos_refresh_partner_card()
                for item in self.cart:
                    item['discount'] = 50
                    item['sum'] = item['quantity'] * item['price'] * 0.5
                self.pos_refresh_cart()
                self.pos_search_entry.delete(0, tk.END)
            
            # Refresh partners list if tab exists
            if hasattr(self, 'partners_tree'):
                self.refresh_partners_list()
            
            # Instant Sync Trigger
            if hasattr(self, 'sync_engine') and self.sync_engine:
                self.sync_engine.request_sync()
            
            self.show_toast(f"✅ {get_text('partner', self.lang)} {extracted_id} {get_text('saved', self.lang)}", "sales")
            dialog.destroy()
            
        # Add standardized buttons to the bottom frame
        self._add_dialog_button(dialog, text=f"💾 {get_text('save', self.lang)}", 
                               command=save_partner, style='primary', use_grid=True, column=0)
        self._add_dialog_button(dialog, text=get_text('cancel', self.lang), 
                               command=dialog.destroy, style='neutral', use_grid=True, column=1)
        
        self.bind_dialog_keys(dialog, confirm_callback=save_partner, cancel_callback=dialog.destroy)
        name_entry.focus_set()

    def _cart_idx(self, sel_item):
        """Get cart list index from tree item tags."""
        return int(self.cart_tree.item(sel_item, 'tags')[0])

    def pos_edit_qty_dialog(self, event=None):
        """Allow manual quantity entry via double-click on an item in the cart."""
        # Re-entry guard: never open a second qty dialog over an open one
        try:
            if self.master.grab_current() is not None:
                return
        except Exception:
            pass
        sel = self.cart_tree.selection()
        if not sel: return
        idx = self._cart_idx(sel[0])
        item = self.cart[idx]
        current_qty = item['quantity']
        
        new_qty = self.ask_float_dialog(
            get_text('quantity', self.lang),
            f"Текущее количество: {self.fmt_num(current_qty)}\nВведите новое количество:",
            initial=current_qty,
            minvalue=0
        )
        
        if new_qty is not None:
            if new_qty <= 0:
                self.pos_remove_item()
            else:
                item['quantity'] = new_qty
                item['sum'] = item['quantity'] * item['price'] * (1 - item['discount']/100)
                self.pos_refresh_cart()
                self.pos_search_entry.delete(0, tk.END)

    def pos_increase_qty(self):
        sel = self.cart_tree.selection()
        if sel:
            idx = self._cart_idx(sel[0])
            self.cart[idx]['quantity'] += 1
            self.cart[idx]['sum'] = self.cart[idx]['quantity'] * self.cart[idx]['price'] * (1 - self.cart[idx]['discount']/100)
            self.pos_refresh_cart()
            self.pos_search_entry.delete(0, tk.END)

    def pos_decrease_qty(self):
        sel = self.cart_tree.selection()
        if sel:
            idx = self._cart_idx(sel[0])
            item = self.cart[idx]
            if item['quantity'] > 1:
                item['quantity'] -= 1
                item['sum'] = item['quantity'] * item['price'] * (1 - item['discount']/100)
                if hasattr(self, '_db_manager') and hasattr(self._db_manager, 'log_cancelled_item'):
                    self._db_manager.log_cancelled_item(item['code'], item['name'], 1, "Уменьшено кол-во", self._get_user_device_label())
                self.pos_refresh_cart()
                self.pos_search_entry.delete(0, tk.END)
            else:
                self.pos_remove_item()

    def pos_remove_item(self):
        sel = self.cart_tree.selection()
        if not sel:
            return
        indices = sorted([int(self.cart_tree.item(i, 'tags')[0]) for i in sel], reverse=True)
        if len(indices) == len(self.cart):
            self.pos_clear_cart()
            return
        for idx in indices:
            item = self.cart[idx]
            if hasattr(self, '_db_manager') and hasattr(self._db_manager, 'log_cancelled_item'):
                self._db_manager.log_cancelled_item(item['code'], item['name'], item['quantity'], "Удален из корзины", self._get_user_device_label())
            del self.cart[idx]
        self.pos_refresh_cart()
        self.pos_search_entry.delete(0, tk.END)

    def pos_clear_cart(self):
        if self.cart and messagebox.askyesno(get_text('confirm_delete', self.lang), get_text('confirm_clear', self.lang)):
            if hasattr(self, '_db_manager') and hasattr(self._db_manager, 'log_cancelled_item'):
                for item in self.cart:
                    self._db_manager.log_cancelled_item(item['code'], item['name'], item['quantity'], "Корзина очищена", self._get_user_device_label())
            self.pos_carts[self.pos_cart_idx] = []
            self.pos_cart_partners[self.pos_cart_idx] = None
            self.pos_partner_var.set(False)
            self.pos_refresh_partner_card()
            self.pos_refresh_cart()
            self.pos_search_entry.delete(0, tk.END)

    def _rebuild_cart_tabs(self):
        c = self.colors
        for w in self.pos_cart_tab_inner.winfo_children():
            w.destroy()
        for i, cart in enumerate(self.pos_carts):
            item_count = sum(item.get('quantity', 0) for item in cart)
            label = f"#{i + 1}"
            if item_count:
                total = sum(item.get('sum', 0) for item in cart)
                label += f" [{item_count}]"
            is_active = i == self.pos_cart_idx
            bg = c['accent'] if is_active else c['bg_tertiary']
            fg = 'white' if is_active else c['fg']
            btn = tk.Button(self.pos_cart_tab_inner, text=label, font=("Arial", 10),
                            width=7, relief="flat", bg=bg, fg=fg,
                            cursor="hand2",
                            command=lambda idx=i: self._switch_cart(idx))
            btn.pack(side="left", padx=1)
            if not is_active and len(cart) == 0 and len(self.pos_carts) > 1:
                tk.Button(self.pos_cart_tab_inner, text="×", font=("Arial", 8),
                          width=1, relief="flat", bg=bg, fg=fg,
                          command=lambda idx=i: self._remove_cart(idx)).pack(side="left", padx=(0, 1))

    def _switch_cart(self, idx):
        if idx == self.pos_cart_idx:
            return
        self.pos_cart_idx = idx
        self._rebuild_cart_tabs()
        self.pos_refresh_cart()
        self.pos_refresh_partner_card()
        self.pos_search_entry.delete(0, tk.END)
        self.pos_search_entry.focus_set()

    def _add_new_cart(self):
        self.pos_carts.append([])
        self.pos_cart_partners.append(None)
        self._switch_cart(len(self.pos_carts) - 1)

    def _remove_cart(self, idx):
        if len(self.pos_carts) <= 1:
            return
        del self.pos_carts[idx]
        del self.pos_cart_partners[idx]
        if self.pos_cart_idx >= len(self.pos_carts):
            self.pos_cart_idx = len(self.pos_carts) - 1
        elif self.pos_cart_idx == idx:
            self.pos_cart_idx = min(idx, len(self.pos_carts) - 1)
        self._rebuild_cart_tabs()
        self.pos_refresh_cart()
        self.pos_refresh_partner_card()

    def pos_checkout(self):
        if not self.cart:
            messagebox.showwarning(get_text('cart', self.lang), get_text('cart_empty', self.lang))
            return

        # Warehouse device mode gate (Phase 3.1): warehouse must not ring sales.
        # Refunds and every other tab/module remain enabled. Only the new-sale
        # checkout entrypoint is blocked. The cashier PC is authoritative for
        # receipts.
        try:
            device_type = settings.get_device_type()
        except Exception:
            device_type = 'cashier'
        if device_type == 'warehouse':
            messagebox.showwarning(
                get_text('warehouse_mode_title', self.lang),
                get_text('warehouse_mode_msg', self.lang)
            )
            return

        try:
            total = sum(item['sum'] for item in self.cart)
            discount = sum(item['price'] * item['quantity'] * item['discount'] / 100 for item in self.cart)
            
            # Get payment information from dialog
            payment_results = self.show_payment_dialog(total)
            
            if payment_results is None: # User cancelled payment
                return

            payment_cash = payment_results['cash']
            payment_card = payment_results['card']
            payment_internal = payment_results['internal']
            change_given = payment_results['change']
            payment_method = payment_results['method'] # Store method for success message
            
            items_for_receipt = [{'code': i['code'], 'name': i['name'], 'quantity': i['quantity'],
                                 'price': i['price'], 'pv': i.get('pv', 0), 'sum': i['sum']} for i in self.cart]
            
            # Stock availability check: warn (not block) when selling above stock
            low_stock_lines = []
            for item in self.cart:
                _, good = self.goods_manager.get_good(item['code'])
                available = float((good or {}).get('quantity') or 0)
                if available < item['quantity']:
                    low_stock_lines.append(
                        f"{item['code']} • {item['name']} — остаток {available:g}, продаётся {item['quantity']:g}")
            if low_stock_lines and not settings.get_appearance_settings().get('skip_low_stock_warning', False):
                c = self.colors
                dialog = self.create_modal_dialog("Недостаточно остатков", width=540, height=400, scrollable=False)
                main = dialog.container
                header_lbl = tk.Label(main, text="По этим позициям остаток меньше продаваемого количества:",
                         font=self.font_normal_tuple, bg=c['bg'], fg=c['fg'],
                         justify="center", anchor="center", wraplength=1)
                header_lbl.pack(pady=(12, 6))
                lines_frame = tk.Frame(main, bg=c['bg'])
                lines_frame.pack(fill="x", padx=25, pady=(0, 6))
                line_labels = []
                for line in low_stock_lines[:10]:
                    lbl = tk.Label(lines_frame, text=line, font=self.font_small_tuple, bg=c['bg'],
                                   fg=c['warning'], justify="left", anchor="w", wraplength=1)
                    lbl.pack(fill="x", pady=1)
                    line_labels.append(lbl)
                if len(low_stock_lines) > 10:
                    tk.Label(lines_frame, text=f"... и ещё {len(low_stock_lines) - 10} позиций",
                             font=self.font_small_tuple, bg=c['bg'], fg=c['fg_muted'],
                             anchor="w").pack(fill="x", pady=1)
                tk.Label(main, text="Продать всё равно? (остаток уйдёт в минус)",
                         font=self.font_bold_tuple, bg=c['bg'], fg=c['fg']).pack(pady=(6, 4))
                skip_var = tk.BooleanVar(value=False)
                tk.Checkbutton(main, text="Больше не спрашивать", variable=skip_var,
                               font=self.font_normal_tuple, bg=c['bg'], fg=c['fg'],
                               activebackground=c['bg'], selectcolor=c['bg_tertiary']).pack(pady=(4, 10))
                confirmed = [False]
                def do_sell():
                    if skip_var.get():
                        try:
                            s = settings.load_settings()
                            app = s.get('appearance_settings') or {}
                            app['skip_low_stock_warning'] = True
                            s['appearance_settings'] = app
                            settings.save_settings(s)
                            if hasattr(self, 'skip_low_stock_warning_var'):
                                self.skip_low_stock_warning_var.set(True)
                        except Exception:
                            pass
                    confirmed[0] = True
                    dialog.destroy()
                self._add_dialog_button(dialog, "❌ Отмена", dialog.destroy, 'neutral', 'right')
                self._add_dialog_button(dialog, "✅ Продать всё равно", do_sell, 'danger', 'left')
                self.bind_dialog_keys(dialog, confirm_callback=do_sell, cancel_callback=dialog.destroy)

                dialog.update_idletasks()
                wrap_w = max(main.winfo_width() - 90, 220)
                header_lbl.config(wraplength=wrap_w)
                for lbl in line_labels:
                    lbl.config(wraplength=wrap_w)
                dialog.update_idletasks()
                w = int(540 * self.interface_scale)
                req_h = main.winfo_reqheight() + dialog.btn_frame.winfo_reqheight() + 20
                dialog.geometry(f"{w}x{req_h}")

                dialog.wait_window(dialog)
                if not confirmed[0]:
                    return
            
            # C4: atomic sale — receipt, stock and partner stats commit (or
            # roll back) as ONE transaction, with stock validation.
            try:
                receipt = self.inventory_ops.sale(
                    items_for_receipt, total, discount,
                    self.current_partner['id'] if self.current_partner else None,
                    payment_cash, payment_card, payment_internal, change_given,
                    cashier_user=self._get_user_device_label()
                )
            except ValueError as e:
                messagebox.showerror(get_text('checkout_error_title', self.lang), str(e))
                return
            
            if not receipt:
                raise Exception("Failed to create receipt record")
            
            # Success message with payment info
            payment_info = f"\n{get_text('payment_method', self.lang)}: "
            if payment_method == 'cash':
                payment_info += get_text('cash', self.lang)
                if change_given > 0:
                    payment_info += f"\n{get_text('change', self.lang)}: {self.format_amount(change_given)}"
            elif payment_method == 'card':
                payment_info += get_text('card', self.lang)
            elif payment_method == 'internal':
                payment_info += get_text('internal', self.lang)
            else:
                payment_info += get_text('mixed', self.lang)
                if change_given > 0:
                    payment_info += f"\n{get_text('change', self.lang)}: {self.format_amount(change_given)}"
            
            # Add to Live Bot Queue if enabled
            if self.live_bot_v2_var.get():
                self.live_bot_queue.put(receipt)
                self.log_message(get_text('live_bot_queued', self.lang).format(number=receipt['number']), "info")

            msg = get_text('sale_success', self.lang)
            if change_given > 0:
                msg += f" • {get_text('change', self.lang)}: {self.format_amount(change_given)} ₸"
            self.show_toast(msg, duration=4000)
            
            # Instant Sync Trigger
            if hasattr(self, 'sync_engine') and self.sync_engine:
                self.sync_engine.request_sync()
            
            self.cart = []
            self.current_partner = None
            self.pos_partner_var.set(False)
            self.pos_refresh_partner_card()
            
            # Switch to next non-empty cart, or stay on current
            non_empty = [i for i, c in enumerate(self.pos_carts) if c]
            if non_empty:
                self._switch_cart(non_empty[0])
            else:
                self.pos_refresh_cart()
            self.pos_search_entry.focus_set()
            
            # Update status bar
            self.status_bar.config(text=f"  {get_text('ready', self.lang)}  |  {get_text('receipt_no', self.lang)} {self.receipts_manager.counter}")
            
            # Refresh sales history if tab exists
            if hasattr(self, 'sales_tree') and self.sales_tree:
                self.refresh_sales_history()
            
            # Instant stock refresh
            if hasattr(self, 'goods_tree'):
                self.refresh_goods_list()
            
            # Print receipt if checked in the payment dialog
            if payment_results.get('print_receipt', False):
                self._print_receipt_for_sale(receipt, force_print=True)
        except Exception as e:
            import traceback
            error_details = traceback.format_exc()
            self.log_message(f"Checkout ERROR: {e}\n{error_details}", "error")
            messagebox.showerror(get_text('checkout_error_title', self.lang), get_text('checkout_error_msg', self.lang).format(error=e))

    def show_payment_dialog(self, total_amount):
        """Simplified checkout dialog with direct input fields for Cash, Card, Internal."""
        c = self.colors
        
        # Result container (mutable to be accessible in callbacks)
        cancelled = [True] 
        payment_details = {}
        
        # Standard modal layout
        dialog = self.create_modal_dialog(get_text('payment_method', self.lang), width=500, height=450, scrollable=False)
        main = dialog.container
        
        # Total header
        tk.Label(main, text=f"💰 {get_text('total_to_pay', self.lang)}", font=self.font_normal_tuple, bg=c['bg'], fg=c['fg_secondary']).pack(pady=(5, 0))
        tk.Label(main, text=self.format_amount(total_amount), font=self.font_title_tuple, bg=c['bg'], fg=c['accent']).pack(pady=(0, 5))

        # Fields frame
        fields_frame = tk.Frame(main, bg=c['bg'])
        fields_frame.pack(padx=20, pady=5)
        
        lbl_font = self.font_normal_tuple
        ent_font = self.font_large_tuple
        
        # Define variables first so they are accessible by completion buttons
        # Use localized zero and initial values
        zero_val = self.format_amount(0, force_decimal=True).replace('\xa0', '').replace(' ', '')
        cash_val = self.format_amount(total_amount).replace('\xa0', '').replace(' ', '')
        
        cash_var = tk.StringVar(value=cash_val)
        card_var = tk.StringVar(value=zero_val)
        internal_var = tk.StringVar(value=zero_val)

        def parse(v):
            s = v.get().replace(' ', '').replace('\xa0', '').replace('\u202f', '')
            if self.lang == 'ru':
                # RU uses ',' as decimal separator
                s = s.replace(',', '.')
            else:
                # EN uses ',' as thousands separator
                s = s.replace(',', '')
            s = s.strip()
            if not s:
                return 0.0
            # Only non-negative numbers are valid (no '-', no letters)
            if not re.match(r'^\d+(\.\d+)?$', s):
                return None
            try:
                return float(s)
            except ValueError:
                return None

        # Helper to create rows with "=" button
        def create_field_row(row, text, var):
            tk.Label(fields_frame, text=text, font=lbl_font, bg=c['bg']).grid(row=row, column=0, sticky="w", pady=4)
            ent = tk.Entry(fields_frame, textvariable=var, font=ent_font, width=10, justify='right', relief="solid", bd=1)
            ent.grid(row=row, column=1, sticky="w", padx=5)
            def fill_all():
                cash_var.set(zero_val)
                card_var.set(zero_val)
                internal_var.set(zero_val)
                var.set(self.format_amount(total_amount).replace('\xa0', '').replace(' ', ''))
                ent.focus_set()
                ent.selection_range(0, tk.END)

            btn = self._btn(fields_frame, text="=", command=fill_all, style='neutral', width=3, cursor="hand2")
            btn.grid(row=row, column=2, sticky="w")
            return ent

        # Input Rows
        cash_ent = create_field_row(0, f"{get_text('cash', self.lang)}:", cash_var)
        card_ent = create_field_row(1, f"{get_text('card', self.lang)}:", card_var)
        internal_ent = create_field_row(2, f"{get_text('internal', self.lang)}:", internal_var)

        # Status/Summary frame
        summary_frame = tk.Frame(main, bg=c['bg_secondary'], padx=15, pady=8)
        summary_frame.pack(fill="x", pady=(5, 0))
        
        info_label = tk.Label(summary_frame, text="", font=self.font_bold_tuple, bg=c['bg_secondary'])
        info_label.pack(side="left")
        
        change_label = tk.Label(summary_frame, text="", font=self.font_large_tuple, bg=c['bg_secondary'])
        change_label.pack(side="right")

        def update_calc(*args):
            p_cash = parse(cash_var)
            p_card = parse(card_var)
            p_int = parse(internal_var)
            
            if p_cash is None or p_card is None or p_int is None:
                info_label.config(text="❌ " + get_text('invalid_amount', self.lang), fg=c['error'])
                change_label.config(text="", fg=c['fg'])
                return
            
            total_paid = p_cash + p_card + p_int
            diff = total_paid - total_amount
            methods_count = (p_cash > 0.01) + (p_card > 0.01) + (p_int > 0.01)
            
            # Change allowed only for 100% Cash
            change_allowed = (methods_count == 1 and p_cash > 0.01)
            
            if abs(diff) < 0.01:
                info_label.config(text="✅ " + get_text('completed', self.lang), fg=c['success'])
                change_label.config(text="", fg=c['fg'])
            elif diff < 0:
                info_label.config(text="⏳ " + get_text('remaining', self.lang) + f": {self.format_amount(abs(diff))}", fg=c['error'])
                change_label.config(text="", fg=c['fg'])
            else:
                if change_allowed:
                    info_label.config(text="⚠️ " + get_text('overpaid', self.lang), fg=c['warning'])
                    change_label.config(text=f"{get_text('change', self.lang)}: {self.format_amount(diff)}", fg=c['success'])
                else:
                    info_label.config(text=f"❌ Переплата {self.format_amount(diff)}", fg=c['error'])
                    change_label.config(text="", fg=c['fg'])

        for v in [cash_var, card_var, internal_var]:
            v.trace_add("write", update_calc)
        update_calc()
        
        # Print receipt checkbox override
        print_var = tk.BooleanVar(value=settings.get_receipt_config().get('auto_print', False))
        tk.Checkbutton(main, text="Печатать чек", variable=print_var, 
                       font=self.font_normal_tuple, bg=c['bg'], fg=c['fg'], 
                       activebackground=c['bg'], selectcolor=c['bg_tertiary']).pack(pady=(10, 5))

        def confirm():
            p_cash_raw = parse(cash_var)
            p_card = parse(card_var)
            p_int = parse(internal_var)
            
            if p_cash_raw is None or p_card is None or p_int is None:
                self.show_toast(get_text('invalid_amount', self.lang), "error")
                return
            
            total_paid = p_cash_raw + p_card + p_int
            
            methods_count = (p_cash_raw > 0.01) + (p_card > 0.01) + (p_int > 0.01)
            
            if total_paid < total_amount - 0.01:
                self.show_toast(get_text('insufficient_funds', self.lang), "error")
                return
            
            # Logic: Strictly equal for mixed or non-cash single method.
            change_allowed = (methods_count == 1 and p_cash_raw > 0.01)
            if not change_allowed and abs(total_paid - total_amount) > 0.01:
                self.show_toast("Сумма должна быть точной (без сдачи)!", "error")
                return
            
            # Logic: Record raw amounts as paid. Sum(cash,card,internal) - total = change.
            change = total_paid - total_amount if change_allowed else 0.0
            
            payment_details.update({
                'method': 'mixed' if methods_count > 1 else ('cash' if p_cash_raw > 0.01 else ('card' if p_card > 0.01 else 'internal')),
                'cash': p_cash_raw,
                'card': p_card,
                'internal': p_int,
                'change': change,
                'print_receipt': print_var.get()
            })
            cancelled[0] = False
            dialog.destroy()

        # Buttons in dialog frame
        self._add_dialog_button(dialog, get_text('cancel', self.lang), dialog.destroy, 'neutral', 'right')
        self._add_dialog_button(dialog, f"✅ {get_text('confirm', self.lang)}", confirm, 'primary', 'left')
        
        self.bind_dialog_keys(dialog, confirm_callback=confirm, cancel_callback=dialog.destroy)
        
        cash_ent.focus_set()
        cash_ent.selection_range(0, tk.END)
        
        dialog.wait_window(dialog)
        return payment_details if not cancelled[0] else None

    
    def create_quick_buttons(self):
        """Create quick item buttons - 6 slots in a 3x2 grid."""
        c = self.colors
        
        # Clear existing widgets
        for widget in self.quick_grid.winfo_children():
            widget.destroy()
        self.quick_buttons = []
        
        # Three columns fit the narrower checkout panel and expose more slots
        # without making the labels unreadably small.
        cols = 3
        for i in range(cols):
            self.quick_grid.grid_columnconfigure(i, weight=1, uniform='quick')
        
        # Fixed layout: 6 visible slots (max assignable quick items)
        items = []
        for i in range(6):
            item = self.quick_items_manager.get_item(i)
            items.append(item)
        
        # Scaling based on settings
        btn_scale = self.button_size_var.get() / 50.0  # 50.0 is the base middle value
        base_h = max(2, int(2 * btn_scale))
        base_w = max(7, int(9 * btn_scale))
        # Slightly smaller font so longer names fit inside the quick buttons
        quick_font = (self.font_family, max(8, self.font_small - 1))
        
        for i in range(6):
            item = items[i]
            row, col = i // cols, i % cols
            
            if item:
                # Truncate name for small buttons
                display_name = (item['name'][:10] + '..') if len(item['name']) > 12 else item['name']
                btn = self._btn(self.quick_grid, text=display_name, style='accent', compact=True, width=base_w, height=base_h, font=quick_font, cursor='hand2') # Scale from settings
                btn.configure(command=lambda cd=item['code']: self.pos_add_by_code(cd))
                btn.bind('<Button-3>', lambda e, sidx=i: self.quick_item_context_menu(e, sidx))
                btn.bind('<Button-2>', lambda e, sidx=i: self.quick_item_context_menu(e, sidx))  # macOS
                btn.grid(row=row, column=col, sticky="nsew", padx=1, pady=2)
                self.quick_buttons.append(btn)
            else:
                # Empty slot - smaller "+" button
                btn = self._btn(self.quick_grid, text="+", style='neutral', compact=True, width=base_w, height=base_h, font=quick_font, cursor='hand2') # Scale from settings
                btn.grid(row=row, column=col, sticky="nsew", padx=1, pady=2)
                btn.configure(command=lambda s=i: self.assign_quick_item(s))
                self.quick_buttons.append(btn)
            
            # Remove row weight to keep it compact at the top
            self.quick_grid.grid_rowconfigure(row, weight=0)

    def quick_item_context_menu(self, event, slot_index):
        """Show context menu for quick item (right-click)."""
        menu = tk.Menu(self.master, tearoff=0)
        menu.add_command(label=get_text('clear_quick_item', self.lang), 
                        command=lambda: self.clear_quick_item(slot_index))
        menu.add_command(label=get_text('assign_quick_item', self.lang), 
                        command=lambda: self.assign_quick_item(slot_index))
        menu.tk_popup(event.x_root, event.y_root)

    def assign_quick_item(self, slot_index):
        """Show window to assign product to quick slot."""
        goods = self.goods_manager.get_all_goods()
        if not goods:
            messagebox.showinfo(get_text('quick_items', self.lang), get_text('no_goods', self.lang))
            return
            
        dialog = self.create_modal_dialog(get_text('assign_quick_item', self.lang), 450, 480, scrollable=False)
        main = dialog.container
        
        tk.Label(main, text=get_text('select_product', self.lang), 
                font=self.font_bold_tuple, bg=self.colors['bg']).pack(pady=10)
        
        # Search
        search_var = tk.StringVar()
        search_entry = tk.Entry(main, textvariable=search_var, font=self.font_normal_tuple)
        search_entry.pack(fill="x", padx=20, pady=5)
        
        # Listbox
        list_frame = tk.Frame(main, bg=self.colors['bg'])
        list_frame.pack(fill="both", expand=True, padx=20, pady=5)
        
        scrollbar = AutoScrollbar(list_frame)
        scrollbar.pack(side="right", fill="y")
        
        listbox = tk.Listbox(list_frame, font=self.font_normal_tuple, height=12,
                            yscrollcommand=scrollbar.set, selectmode="single",
                            bg=self.colors.get('list_bg', self.colors['bg_secondary']), fg=self.colors['fg'],
                            selectbackground=self.colors['accent'], selectforeground='white',
                            relief='flat', bd=0, activestyle='none')
        listbox.pack(fill="both", expand=True)
        scrollbar.config(command=listbox.yview)
        self.bind_mousewheel(listbox)
        
        # Pre-calculate assigned codes to hide them from the selection list
        assigned_codes = set()
        for i in range(20):
            item = self.quick_items_manager.get_item(i)
            if item and item.get('code'):
                assigned_codes.add(item['code'])
        
        def refresh_list(*args):
            listbox.delete(0, tk.END)
            query = search_var.get().lower()
            for g in goods:
                if g['code'] in assigned_codes:
                    continue  # Hide already assigned items
                if query in g['code'].lower() or query in g['name'].lower():
                    listbox.insert(tk.END, f"{g['code']} - {g['name']}")
            if listbox.size() > 0:
                listbox.selection_clear(0, tk.END)
                listbox.selection_set(0)
        
        search_var.trace_add('write', refresh_list)
        refresh_list()
        
        def on_select(event=None):
            sel = listbox.curselection()
            if sel:
                selected_text = listbox.get(sel[0])
                code = selected_text.split(' - ')[0]
                
                # Prevent duplicate assignment in other slots
                for i in range(20):
                    existing = self.quick_items_manager.get_item(i)
                    if existing and existing.get('code') == code and i != slot_index:
                        messagebox.showwarning(get_text('quick_items', self.lang), 
                                             get_text('quick_items_exists', self.lang))
                        return
                
                result = self.goods_manager.get_good(code)
                if result:
                    _, g_obj = result
                    self.quick_items_manager.set_item(slot_index, g_obj)
                    self.create_quick_buttons()
            dialog.destroy()
        
        # Buttons frame (inside container)
        btn_frame = tk.Frame(main, bg=self.colors['bg'])
        btn_frame.pack(fill="x", pady=15)

        self._btn(btn_frame, text=get_text('save', self.lang), command=on_select, style='success').pack(side="left", expand=True, padx=10)
                 
        self._btn(btn_frame, text=get_text('cancel', self.lang), command=dialog.destroy, style='danger').pack(side="right", expand=True, padx=10)

        # Keyboard shortcuts
        listbox.bind('<Double-Button-1>', on_select)
        listbox.bind('<Return>', on_select)
        search_entry.bind('<Return>', on_select)
        search_entry.bind('<Down>', lambda e: listbox.focus_set())
        search_entry.bind('<Up>', lambda e: listbox.focus_set())
        dialog.bind('<Escape>', lambda e: dialog.destroy())
        
        search_entry.focus_set()

    def clear_quick_item(self, slot_index):
        """Clear quick item slot."""
        self.quick_items_manager.clear_item(slot_index)
        self.create_quick_buttons()
