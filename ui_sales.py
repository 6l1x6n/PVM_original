# -*- coding: utf-8 -*-
"""
PVM.core - Sales Tab Mixin
============================
Sales history, receipt details, refunds, seller reports, Excel export.
"""

import json
import threading
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from datetime import datetime, date, timedelta

import pandas as pd
from tkcalendar import Calendar

from ui_lang import get_text
from ui_dialogs import AutoScrollbar


class SalesTabMixin:
    """Sales tab methods for GreenLeafApp."""

    def create_sales_tab(self):
        """Create the Sales History tab."""
        c = self.colors
        
        main_frame = tk.Frame(self.sales_frame, bg=c['bg'])
        main_frame.pack(fill="both", expand=True, padx=10, pady=10)
        
        # Top: Filters and Export
        top_frame = tk.Frame(main_frame, bg=c['frame_bg'])
        top_frame.pack(fill="x", pady=(0, 10))
        
        filter_frame = tk.Frame(top_frame, bg=c['frame_bg'])
        filter_frame.pack(side="left", padx=10, pady=10)
        
        self.sales_range_var = tk.StringVar(value=f"{date.today().strftime('%d.%m.%Y')} - {date.today().strftime('%d.%m.%Y')}")
        self.sales_range_entry = tk.Entry(filter_frame, textvariable=self.sales_range_var,
                                          font=self.font_normal_tuple, width=23)
        self.sales_range_entry.pack(side="left", padx=5)
        self.sales_range_entry.config(takefocus=0)
        self.sales_range_entry.bind('<Button-1>', lambda e: self.show_date_range_picker(
            range_var=self.sales_range_var, callback=self.filter_sales))
        self.sales_range_entry.bind('<KeyRelease>', lambda e: self.filter_sales())
        
        export_frame = tk.Frame(top_frame, bg=c['frame_bg'])
        export_frame.pack(side="right", padx=(10, 20), pady=10)
        
        self._btn(export_frame, text=f"📊 Excel", command=self.export_sales_to_excel, style='success', cursor='hand2').pack(side="left", padx=5)
                 
        self._btn(export_frame, text=f"📋 Отчет", command=self.show_seller_report_dialog, style='accent', cursor='hand2').pack(side="left", padx=(0, 5))
        
        # Middle: Sales List
        list_frame = tk.Frame(main_frame, bg=c['frame_bg'])
        list_frame.pack(fill="both", expand=True)
        
        columns = ('number', 'date', 'total', 'partner', 'discount_pct', 'payment', 'status', 'cashier')
        cashier_label = get_text('cashier_label', self.lang)
        headers = [
            get_text('receipt_number', self.lang),
            get_text('receipt_date', self.lang),
            'Итог',
            get_text('receipt_partner', self.lang),
            '% скидки',
            get_text('payment_method', self.lang),
            get_text('receipt_status', self.lang),
            cashier_label,
        ]
        
        self.sales_tree = ttk.Treeview(list_frame, columns=columns, show='headings', height=20)
        
        col_widths = [80, 140, 100, 160, 80, 100, 90, 100]
        for col, header, width in zip(columns, headers, col_widths):
            self.sales_tree.heading(col, text=header)
            anchor = 'w' if col in ('partner', 'cashier') else 'center'
            if col == 'total': anchor = 'center'
            self.sales_tree.column(col, width=width, anchor=anchor)
            
        self.setup_treeview_sorting(self.sales_tree, columns, numeric_cols=['number', 'total', 'discount_pct'])
        self.setup_universal_navigation(self.sales_tree, lambda: self.show_receipt_details(None)) # Added
        self.sales_tree.bind('<Button-1>', self.prevent_treeview_resize)
        
        # Tags for status colors
        self.sales_tree.tag_configure('refunded', foreground='red')
        self.sales_tree.tag_configure('partial_refund', foreground='#FFA500')
        self.sales_tree.tag_configure('completed', foreground=c['fg'])
        
        # Live Bot status tags (Background colors)
        self.sales_tree.tag_configure('live_success', background='#e6ffed') # Soft Green
        self.sales_tree.tag_configure('live_partial', background='#f5f0ff') # Soft Purple
        self.sales_tree.tag_configure('live_error', background='#fff0f0')   # Soft Red
        
        scrollbar_y = AutoScrollbar(list_frame, orient="vertical", command=self.sales_tree.yview)
        scrollbar_x = AutoScrollbar(list_frame, orient="horizontal", command=self.sales_tree.xview)
        self.sales_tree.configure(yscrollcommand=scrollbar_y.set, xscrollcommand=scrollbar_x.set)
        
        self.sales_tree.grid(row=0, column=0, sticky="nsew", padx=5, pady=5)
        scrollbar_y.grid(row=0, column=1, sticky="ns")
        scrollbar_x.grid(row=1, column=0, sticky="ew")
        
        self.bind_mousewheel(self.sales_tree)
        
        list_frame.grid_rowconfigure(0, weight=1)
        list_frame.grid_columnconfigure(0, weight=1)
        
        self.sales_tree.grid_rowconfigure(0, weight=1)
        self.sales_tree.grid_columnconfigure(0, weight=1)
        
        # Default to showing today's sales
        self.filter_sales()
    
    def show_date_range_picker(self, from_entry=None, to_entry=None, callback=None, range_var=None):
        """Modern single-calendar date range picker.

        Tap a day to set the start, tap another to set the end. Tapping the
        same day again completes a single-day range. Visual tags highlight
        the selected span.

        If range_var is provided (StringVar with "dd.MM.yyyy - dd.MM.yyyy" format),
        from_entry/to_entry are ignored and the single var is used.
        """
        if getattr(self, '_date_range_picker', None):
            try:
                if self._date_range_picker.winfo_exists():
                    self._date_range_picker.lift()
                    self._date_range_picker.focus_set()
                    return
            except:
                pass
        c = self.colors
        dlg = tk.Toplevel(self.master)
        self._date_range_picker = dlg
        dlg.title("Выбор периода")
        dlg.transient(self.master)
        dlg.resizable(False, False)
        dlg.configure(bg=c['bg'])

        def close_popup(event=None):
            try: self.master.unbind_all("<Button-1>")
            except: pass
            try:
                if dlg.grab_current():
                    dlg.grab_release()
            except: pass
            self._date_range_picker = None
            if dlg.winfo_exists():
                dlg.destroy()

        def check_click_outside(event):
            if not dlg.winfo_exists(): return
            try:
                x, y = event.x_root, event.y_root
                target = dlg.winfo_containing(x, y)
                is_inside = False
                curr = target
                while curr:
                    if curr == dlg:
                        is_inside = True
                        break
                    curr = curr.master
                if not is_inside:
                    close_popup()
            except:
                px, py = dlg.winfo_rootx(), dlg.winfo_rooty()
                pw, ph = dlg.winfo_width(), dlg.winfo_height()
                if not (px <= x <= px + pw and py <= y <= py + ph):
                    close_popup()

        def _on_dlg_close():
            close_popup()
        dlg.protocol("WM_DELETE_WINDOW", _on_dlg_close)

        dlg.focus_set()
        dlg.after(100, lambda: self.master.bind_all("<Button-1>", check_click_outside, add="+"))

        inner = tk.Frame(dlg, bg=c['bg'], padx=16, pady=12)
        inner.pack(fill="both", expand=True)

        display_var = tk.StringVar(value="Диапазон не выбран")
        range_label = tk.Label(inner, textvariable=display_var,
                               font=self.font_bold_tuple, bg=c['bg'], fg=c['accent'])
        range_label.pack(pady=(0, 8))

        cal = Calendar(inner, font=self.font_large_tuple, selectmode='day',
                       locale='ru_RU' if self.lang == 'ru' else 'en_US',
                       cursor='hand2', date_pattern='dd.MM.yyyy',
                       background=c['frame_bg'], foreground=c['fg'],
                       headersbackground=c['accent'], headersforeground='white',
                       bordercolor=c['border'], selectbackground=c['accent'],
                       normalbackground=c['bg_secondary'], weekendbackground=c['bg_tertiary'])
        cal.pack(pady=4)

        # Range state
        self._range_start = None
        self._range_end = None
        self._prog = False  # guard against programmatic selection events

        def fmt(d):
            return d.strftime("%d.%m.%Y")

        def refresh_highlight():
            cal.calevent_remove(*cal.get_calevents())
            if self._range_start:
                cal.calevent_create(self._range_start, '', 'start')
            if self._range_end and self._range_end != self._range_start:
                cur = self._range_start
                while cur <= self._range_end:
                    if cur == self._range_start:
                        pass
                    elif cur == self._range_end:
                        cal.calevent_create(cur, '', 'end')
                    else:
                        cal.calevent_create(cur, '', 'range')
                    cur += timedelta(days=1)
            # Update label
            if self._range_start and self._range_end:
                if self._range_start == self._range_end:
                    display_var.set(f"📅 {fmt(self._range_start)}")
                else:
                    display_var.set(f"📅 {fmt(self._range_start)}  —  {fmt(self._range_end)}")
            elif self._range_start:
                display_var.set(f"От: {fmt(self._range_start)}  (выберите конец)")
            else:
                display_var.set("Диапазон не выбран")

        cal.tag_config('start', background=c['accent'], foreground='white')
        cal.tag_config('end', background=c['accent'], foreground='white')
        cal.tag_config('range', background=c['accent_hover'], foreground='white')

        def on_select(event=None):
            if self._prog:
                return
            d = cal.selection_get()
            if self._range_start is None or (self._range_start and self._range_end):
                self._range_start = d
                self._range_end = None
            else:
                if d < self._range_start:
                    self._range_end = self._range_start
                    self._range_start = d
                elif d == self._range_start:
                    self._range_end = d
                else:
                    self._range_end = d
                # Auto-apply on second date selection
                self._prog = True
                dlg.after(100, apply_range)
                return
            refresh_highlight()

        cal.bind("<<CalendarSelected>>", on_select)

        def apply_range():
            s = self._range_start or date.today()
            e = self._range_end or s
            if range_var is not None:
                if s == e:
                    range_var.set(fmt(s))
                else:
                    range_var.set(f"{fmt(s)} - {fmt(e)}")
            else:
                from_entry.delete(0, tk.END)
                from_entry.insert(0, fmt(s))
                to_entry.delete(0, tk.END)
                to_entry.insert(0, fmt(e))
            close_popup()
            if callback:
                callback()

        # Preset from existing entry values, if valid
        if range_var is not None:
            try:
                parts = [p.strip() for p in range_var.get().split('-')]
                if len(parts) == 2:
                    pf = datetime.strptime(parts[0], "%d.%m.%Y").date()
                    pt = datetime.strptime(parts[1], "%d.%m.%Y").date()
                    self._range_start = pf
                    self._range_end = pt
                elif len(parts) == 1 and parts[0]:
                    pf = datetime.strptime(parts[0], "%d.%m.%Y").date()
                    self._range_start = pf
                    self._range_end = pf
            except Exception:
                pass
        else:
            try:
                pf = datetime.strptime(from_entry.get().strip(), "%d.%m.%Y").date()
                pt = datetime.strptime(to_entry.get().strip(), "%d.%m.%Y").date()
                self._range_start = pf
                self._range_end = pt
            except Exception:
                pass
        self._prog = True
        if self._range_start:
            cal.selection_set(self._range_start)
        self._prog = False
        refresh_highlight()

        dlg.update_idletasks()
        dw = max(dlg.winfo_reqwidth(), 340)
        dh = dlg.winfo_reqheight()
        sw = dlg.winfo_screenwidth()
        sh = dlg.winfo_screenheight()
        try:
            toplevel = self.master.winfo_toplevel()
            mx = toplevel.winfo_rootx()
            my = toplevel.winfo_rooty()
            mw = toplevel.winfo_width()
            mh = toplevel.winfo_height()
            if mw > 100 and mh > 100:
                tx = mx + (mw // 2) - (dw // 2)
                ty = my + (mh // 2) - (dh // 2)
            else:
                tx = (sw - dw) // 2
                ty = (sh - dh) // 2
        except:
            tx = (sw - dw) // 2
            ty = (sh - dh) // 2
        tx = max(10, min(tx, sw - dw - 10))
        ty = max(10, min(ty, sh - dh - 10))
        dlg.geometry(f"{dw}x{dh}+{tx}+{ty}")
        # Grab the calendar so it stays interactive even when opened from a
        # modal dialog (which holds its own grab). Released in close_popup.
        try:
            dlg.grab_set()
        except Exception:
            pass
        dlg.lift()
        dlg.focus_set()

    def refresh_sales_history(self, filtered_receipts=None):
        """Refresh sales history list."""
        for item in self.sales_tree.get_children():
            self.sales_tree.delete(item)
        
        receipts = filtered_receipts if filtered_receipts is not None else self.receipts_manager.get_all_receipts()
        
        for receipt in receipts:
            try:
                dt = datetime.fromisoformat(receipt['datetime'])
                date_str = dt.strftime("%d.%m.%Y %H:%M")
                
                partner_name = get_text('no_partner', self.lang)
                if receipt.get('partner_id'):
                    partner = self.partners_manager.get_partner_by_id(receipt['partner_id'])
                    if partner:
                        partner_name = partner['name']
                
                payment = receipt.get('payment', {})
                if payment.get('cash', 0) > 0 and payment.get('card', 0) == 0 and payment.get('internal', 0) == 0:
                    payment_str = get_text('cash', self.lang)
                elif payment.get('card', 0) > 0 and payment.get('cash', 0) == 0 and payment.get('internal', 0) == 0:
                    payment_str = get_text('card', self.lang)
                elif payment.get('internal', 0) > 0 and payment.get('cash', 0) == 0 and payment.get('card', 0) == 0:
                    payment_str = get_text('internal', self.lang)
                else:
                    payment_str = get_text('mixed', self.lang)
                
                total_str = self.format_amount(receipt.get('total', 0))
                
                # Discount %
                subtotal = receipt.get('subtotal', 0) or 0
                discount = receipt.get('discount', 0) or 0
                if subtotal > 0 and discount > 0:
                    discount_pct = f"{discount / subtotal * 100:.0f}%"
                else:
                    discount_pct = "—"
                
                # Translation for status
                status = receipt.get('status', 'completed') or 'completed'
                status_text = get_text(status, self.lang)
                
                # Use status as part of tags for coloring
                tags = [receipt['id'], status]
                
                # Apply Live Bot status tags
                live_status = receipt.get('live_status', 0)
                if live_status == 1:
                    tags.append('live_success')
                elif live_status == 2:
                    tags.append('live_partial')
                elif live_status == -1:
                    tags.append('live_error')
                
                self.sales_tree.insert('', 'end', values=(
                    receipt['number'], date_str, total_str,
                    partner_name, discount_pct,
                    payment_str, status_text,
                    receipt.get('cashier_user', '')
                ), tags=tuple(tags))
            except Exception as e:
                self.log_message(f"Error displaying receipt {receipt.get('id', '???')}: {e}", "error")

        # Focus on top item
        children = self.sales_tree.get_children()
        if children:
            self.sales_tree.selection_set(children[0])
            self.sales_tree.focus(children[0])
            self.sales_tree.see(children[0])
            
    def filter_sales(self):
        """Filter sales by date range."""
        try:
            parts = [p.strip() for p in self.sales_range_var.get().split('-')]
            date_from = datetime.strptime(parts[0], "%d.%m.%Y").date()
            date_to = datetime.strptime(parts[1] if len(parts) > 1 else parts[0], "%d.%m.%Y").date()
            
            all_receipts = self.receipts_manager.get_all_receipts()
            filtered = []
            
            for receipt in all_receipts:
                receipt_date = datetime.fromisoformat(receipt['datetime']).date()
                if date_from <= receipt_date <= date_to:
                    filtered.append(receipt)
            
            self.refresh_sales_history(filtered)
        except ValueError:
            messagebox.showerror("Ошибка", "Неверный формат даты. Используйте ДД.ММ.ГГГГ")
    
    def reset_sales_filter(self):
        """Reset date filter to today."""
        today_str = date.today().strftime("%d.%m.%Y")
        self.sales_range_var.set(f"{today_str} - {today_str}")
        self.filter_sales()

    def show_seller_report_dialog(self):
        """Show Seller Report dialog with stats for a given date range."""
        dialog = self.create_modal_dialog(get_text('seller_report', self.lang), 960, 700, scrollable=False)
        dialog.resizable(True, True)
        c = self.colors
        
        main = dialog.container
        
        # ── Date Selection (unified range picker) ──
        date_frame = tk.Frame(main, bg=c['bg'])
        date_frame.pack(fill="x", pady=(6, 4))
        
        date_inner = tk.Frame(date_frame, bg=c['bg'])
        date_inner.pack(anchor="w")
        
        tk.Label(date_inner, text="📅", font=self.font_large_tuple, bg=c['bg']).pack(side="left", padx=(4, 6))
        tk.Label(date_inner, text=f"{get_text('period', self.lang)}:", font=self.font_normal_tuple,
                 bg=c['bg'], fg=c['fg_secondary']).pack(side="left")
        self.seller_range_var = tk.StringVar(value=f"{date.today().strftime('%d.%m.%Y')} - {date.today().strftime('%d.%m.%Y')}")
        self.seller_range_entry = tk.Entry(date_inner, textvariable=self.seller_range_var,
                                           font=self.font_normal_tuple, width=26, relief="solid", bd=1)
        self.seller_range_entry.pack(side="left", padx=8, ipady=3)
        self.seller_range_entry.bind('<Button-1>', lambda e: self.show_date_range_picker(
            range_var=self.seller_range_var, callback=calculate_report))
        self.seller_range_entry.bind('<Return>', lambda e: calculate_report())
        self.seller_range_entry.bind('<KeyRelease>', lambda e: calculate_report())
        tk.Label(date_inner, text="💡 Нажмите на поле для календаря", font=self.font_small_tuple,
                 bg=c['bg'], fg=c['fg_muted']).pack(side="left", padx=(4, 0))
        
        # ── Results ──
        res_frame = tk.LabelFrame(main, text=f" {get_text('results', self.lang)} ",
                                  font=self.font_bold_tuple, bg=c['frame_bg'], fg=c['fg'])
        res_frame.pack(fill="both", expand=True, pady=(6, 4), padx=4)
        
        summary_frame = tk.Frame(res_frame, bg=c['frame_bg'])
        summary_frame.pack(fill="x", padx=14, pady=(6, 2))
        
        period_lbl = tk.Label(summary_frame, text="", font=self.font_large_tuple,
                              bg=c['frame_bg'], fg=c['accent'])
        period_lbl.pack(anchor="w", pady=(0, 4))
        
        # Metric cards: Чеков / Выручка / Средний чек
        metrics = tk.Frame(summary_frame, bg=c['frame_bg'])
        metrics.pack(fill="x")
        
        def make_card(parent, title, color):
            card = tk.Frame(parent, bg=c['bg_secondary'], padx=12, pady=6,
                            highlightbackground=c['border'], highlightthickness=1)
            card.pack(side="left", padx=(0, 10))
            tk.Label(card, text=title, font=self.font_small_tuple,
                     bg=c['bg_secondary'], fg=c['fg_muted']).pack(anchor="w")
            val = tk.Label(card, text="0", font=(self.font_family, 18, "bold"),
                           bg=c['bg_secondary'], fg=color)
            val.pack(anchor="w", pady=(2, 0))
            return val
        
        count_lbl = make_card(metrics, "🧾 Количество чеков", c['fg'])
        total_lbl = make_card(metrics, "💰 Выручка за период", c['success'])
        refunds_lbl = make_card(metrics, "↩ Возвраты", c['error'])
        
        # Payment breakdown
        brk_frame = tk.Frame(summary_frame, bg=c['frame_bg'])
        brk_frame.pack(fill="x", pady=(10, 2))
        
        def make_brk_row(parent, label, icon, color):
            row = tk.Frame(parent, bg=c['frame_bg'])
            row.pack(fill="x", pady=1)
            tk.Label(row, text=f"{icon} {label}", font=self.font_normal_tuple,
                     bg=c['frame_bg'], fg=c.get(color, c['fg_secondary'])).pack(side="left")
            lbl = tk.Label(row, text="0 ₸", font=self.font_bold_tuple,
                           bg=c['frame_bg'], fg=c.get(color, c['fg']))
            lbl.pack(side="left", padx=(10, 0))
            return lbl
        
        brk_inner = tk.Frame(brk_frame, bg=c['frame_bg'])
        brk_inner.pack(fill="x")
        
        cash_lbl = make_brk_row(brk_inner, "Наличными", "💵", 'success')
        card_lbl = make_brk_row(brk_inner, "Картой", "💳", 'accent')
        internal_lbl = make_brk_row(brk_inner, "Внутренние", "🪙", 'fg_secondary')
        
        ttk.Separator(summary_frame, orient='horizontal').pack(fill="x", pady=(6, 4))
        
        # ── Cashier analytics table ──
        tree_frame = tk.LabelFrame(res_frame, text=" Аналитика по кассирам ",
                                   font=self.font_normal_tuple, bg=c['frame_bg'], fg=c['fg'])
        tree_frame.pack(fill="both", expand=True, padx=10, pady=(0, 6))
        
        columns = ('cashier', 'count', 'sum')
        cashier_tree = ttk.Treeview(tree_frame, columns=columns, show='headings', height=15)
        cashier_tree.heading('cashier', text='Кассир')
        cashier_tree.heading('count', text='Чеков')
        cashier_tree.heading('sum', text='Выручка')
        cashier_tree.column('cashier', width=220, anchor='w', stretch=True)
        cashier_tree.column('count', width=80, anchor='center')
        cashier_tree.column('sum', width=150, anchor='e')
        
        cashier_tree.tag_configure('even', background=c.get('bg_tertiary', c['bg']))
        
        sb = AutoScrollbar(tree_frame, orient="vertical", command=cashier_tree.yview)
        cashier_tree.configure(yscrollcommand=sb.set)
        cashier_tree.pack(side="left", fill="both", expand=True, padx=5, pady=5)
        sb.pack(side="right", fill="y")
        
        def compute_report():
            raw = self.seller_range_var.get().strip()
            parts = [p.strip() for p in raw.split('-')]
            if len(parts) < 1 or not parts[0]:
                raise ValueError("Укажите период в формате дд.мм.гггг - дд.мм.гггг")
            df_str = parts[0]
            dt_from = datetime.strptime(df_str, "%d.%m.%Y").date()
            # A single date (e.g. picked once from the calendar) means that one day
            dt_to = datetime.strptime(parts[1], "%d.%m.%Y").date() if len(parts) > 1 else dt_from
            
            all_rc = self.receipts_manager.get_all_receipts()
            
            count = 0
            refunds = 0
            total_sum = 0.0
            cash = 0.0
            card = 0.0
            internal = 0.0
            cashier_stats = {}
            
            for r in all_rc:
                try:
                    date_str = r.get('datetime', '')[:10]
                    if not date_str:
                        continue
                    r_date = datetime.strptime(date_str, "%Y-%m-%d").date()
                    
                    if not (dt_from <= r_date <= dt_to):
                        continue
                    
                    r_status = str(r.get('status', 'completed'))
                    
                    # Refund count: receipts refunded within the period
                    if r_status in ('refunded', 'partial_refund'):
                        rfd_str = r.get('refund_datetime', '') or ''
                        rfd_date = None
                        if len(rfd_str) >= 10:
                            try:
                                rfd_date = datetime.strptime(rfd_str[:10], "%Y-%m-%d").date()
                            except Exception:
                                rfd_date = None
                        if rfd_date is None:
                            rfd_date = r_date
                        if dt_from <= rfd_date <= dt_to:
                            refunds += 1
                        continue
                    
                    if r_status == 'completed':
                        count += 1
                        total_sum += float(r.get('total', 0) or 0)
                        
                        pay = r.get('payment') or {}
                        if isinstance(pay, dict):
                            r_cash = float(pay.get('cash', 0) or 0)
                            r_change = float(pay.get('change', 0) or 0)
                            net_cash = max(0, r_cash - r_change)
                            cash += net_cash
                            
                            card += float(pay.get('card', 0) or 0)
                            internal += float(pay.get('internal', 0) or 0)
                        
                        c_user = r.get('cashier_user', 'System') or 'System'
                        if c_user not in cashier_stats:
                            cashier_stats[c_user] = {'count': 0, 'sum': 0.0}
                        cashier_stats[c_user]['count'] += 1
                        cashier_stats[c_user]['sum'] += float(r.get('total', 0) or 0)
                except Exception as item_ex:
                    print(f"Error parsing receipt {r.get('id', '?')}: {item_ex}")
                    continue
            
            return {
                'dt_from': dt_from, 'dt_to': dt_to,
                'count': count, 'refunds': refunds, 'total': total_sum,
                'cash': cash, 'card': card, 'internal': internal,
                'cashiers': cashier_stats,
            }


        def calculate_report():
            try:
                data = compute_report()
            except Exception as e:
                messagebox.showerror("Ошибка", f"Проверьте правильность дат: {e}")
                return
            
            period_lbl.config(text=f"📅 {data['dt_from'].strftime('%d.%m.%Y')} — {data['dt_to'].strftime('%d.%m.%Y')}")
            count_lbl.config(text=str(data['count']))
            total_lbl.config(text=self.format_amount(data['total']) + " ₸")
            refunds_lbl.config(text=str(data['refunds']))
            cash_lbl.config(text=self.format_amount(data['cash']) + " ₸")
            card_lbl.config(text=self.format_amount(data['card']) + " ₸")
            internal_lbl.config(text=self.format_amount(data['internal']) + " ₸")
            
            # Update cashier tree
            for item in cashier_tree.get_children():
                cashier_tree.delete(item)
                
            sorted_cashiers = sorted(data['cashiers'].items(), key=lambda x: x[1]['sum'], reverse=True)
            for i, (c_name, stats) in enumerate(sorted_cashiers):
                prefix = "👑 " if i == 0 and len(sorted_cashiers) > 1 else ""
                cashier_tree.insert('', 'end', values=(
                    f"{prefix}{c_name}",
                    stats['count'],
                    self.format_amount(stats['sum']) + " ₸"
                ), tags=('even',) if i % 2 else ())

        def export_report_to_excel():
            try:
                data = compute_report()
            except Exception as e:
                messagebox.showerror("Ошибка", f"Проверьте правильность дат: {e}")
                return
            try:
                import pandas as pd
                summary_df = pd.DataFrame(
                    [
                        ('Период', f"{data['dt_from'].strftime('%d.%m.%Y')} — {data['dt_to'].strftime('%d.%m.%Y')}"),
                        ('Чеков', data['count']),
                        ('Выручка, ₸', data['total']),
                        ('Возвраты', data['refunds']),
                        ('Наличными, ₸', data['cash']),
                        ('Картой, ₸', data['card']),
                        ('Внутренние, ₸', data['internal']),
                    ],
                    columns=['Показатель', 'Значение'])
                cashiers = sorted(data['cashiers'].items(), key=lambda x: x[1]['sum'], reverse=True)
                cashiers_df = pd.DataFrame(
                    [{'Кассир': n, 'Чеков': s['count'], 'Выручка, ₸': s['sum']} for n, s in cashiers],
                    columns=['Кассир', 'Чеков', 'Выручка, ₸'])
                filepath = filedialog.asksaveasfilename(
                    defaultextension=".xlsx",
                    filetypes=[("Excel files", "*.xlsx")],
                    initialfile=f"Отчет_продавца_{data['dt_from'].strftime('%d.%m.%Y')}.xlsx")
                if not filepath:
                    return
                with pd.ExcelWriter(filepath, engine='openpyxl') as writer:
                    summary_df.to_excel(writer, index=False, sheet_name='Отчет продавца')
                    cashiers_df.to_excel(writer, index=False, sheet_name='По кассирам')
                self.show_toast(f"Экспорт: {filepath}", "success")
            except Exception as e:
                self.show_toast(f"Ошибка экспорта: {e}", "error")
        
        # Buttons in pinned zone (always visible, never scrollable)
        # Report recalculates automatically when the date range changes
        self._add_dialog_button(dialog, get_text('close', self.lang), dialog.destroy, 'neutral', 'right')
        self._add_dialog_button(dialog, "📊 Экспорт в Excel", export_report_to_excel, 'accent', 'right')
                 
        # Initial calculation
        calculate_report()
    
    def show_receipt_details(self, event):
        """Show full receipt details in popup window with tabs for Items and Payment."""
        selection = self.sales_tree.selection()
        if not selection:
            return
        
        item = self.sales_tree.item(selection[0])
        receipt_id = item['tags'][0]
        receipt = self.receipts_manager.get_receipt_by_id(receipt_id)
        
        if not receipt:
            return
        
        dialog = self.create_modal_dialog(f"Чек №{receipt['number']}", 750, 600, scrollable=False)
        c = self.colors
        main = dialog.container
        
        dt = datetime.fromisoformat(receipt['datetime'])
        status = receipt.get('status', 'completed')
        status_color = c['success'] if status == 'completed' else c['error'] if status == 'refunded' else '#FFA500'
        status_text = 'Завершён' if status == 'completed' else 'Возврат' if status == 'refunded' else 'Частичный возврат'
        
        # Header row
        header_frame = tk.Frame(main, bg=c['bg'])
        header_frame.pack(fill="x", pady=(0, 2))
        tk.Label(header_frame, text=f"ЧЕК #{receipt['number']}",
                 font=self.font_bold_tuple, bg=c['bg'], fg=c['fg']).pack(side="left")
        tk.Label(header_frame, text=f" [{status_text}]",
                 font=self.font_small_bold_tuple, bg=c['bg'], fg=status_color).pack(side="left")
        
        # Live Bot status indicator in header
        live_sent = receipt.get('live_sent', 0)
        if live_sent != 0:
            l_processed_at = receipt.get('live_processed_at', '')
            l_status = receipt.get('live_status', 0)
            l_color = c['success'] if l_status == 1 else '#FFA500' if l_status == 2 else c['error']
            l_key = 'live_status_ok' if l_status == 1 else 'live_status_partial' if l_status == 2 else 'live_status_error'
            l_msg = get_text(l_key, self.lang)
            if l_processed_at:
                l_msg += f" ({l_processed_at})"
            tk.Label(header_frame, text=f" {l_msg}", font=("Arial", 9, "bold"), bg=c['bg'], fg=l_color).pack(side="left")
            
            # Show specific error if exists
            l_err_text = receipt.get('live_error', '')
            if l_err_text:
                err_frame = tk.Frame(main, bg=c['error_bg'], bd=0)
                err_frame.pack(fill="x", pady=(0, 4))
                tk.Label(err_frame, text=f"⚠️ {l_err_text}", font=self.font_small_tuple, 
                         bg=c['error_bg'], fg=c['error'], wraplength=700, justify="left").pack(anchor="w", padx=10, pady=2)

        tk.Label(header_frame, text=dt.strftime("%d.%m.%Y %H:%M"),
                 font=self.font_small_tuple, bg=c['bg'], fg=c['fg_muted']).pack(side="right")
        
        # ── Notebook ──
        notebook = ttk.Notebook(main)
        notebook.pack(fill="both", expand=True, pady=5)
        
        # Tab 1: Товары
        tab_items = tk.Frame(notebook, bg=c['bg'])
        notebook.add(tab_items, text=f" {get_text('cart', self.lang)} ")
        
        # Tab 2: Платеж
        tab_payment = tk.Frame(notebook, bg=c['bg'], padx=30, pady=15)
        notebook.add(tab_payment, text=f" {get_text('payment_method', self.lang)} ")

        # Tab 3: Чек (placeholder — removed paper display)
        
        # --- Content for Tab 1 (Items) ---
        # Refund info block (Detailed Logs)
        refund_logs = receipt.get('refund_logs', [])
        if refund_logs:
            history_frame = tk.Frame(tab_items, bg='#FFF8E1', bd=1, relief="solid")
            history_frame.pack(fill="x", pady=(2, 4), padx=10)
            tk.Label(history_frame, text="📜 Возвраты:", font=("Arial", 9, "bold"), bg='#FFF8E1', fg=c['error']).pack(anchor="w", padx=8, pady=(2, 0))
            for log in refund_logs:
                ldt = datetime.fromisoformat(log['datetime']).strftime("%d.%m.%Y %H:%M")
                reason = log.get('reason', '-')
                user_info = f" ({log.get('user_name', '')})" if log.get('user_name') else ""
                items_str = ", ".join([f"{it['name']} ({it['qty']})" for it in log.get('items', [])])
                tk.Label(history_frame, text=f"• {ldt} | {reason}{user_info} | {items_str}", justify="left", font=("Arial", 9), bg='#FFF8E1', fg=c['fg'], anchor="w").pack(fill="x", padx=15, pady=1)

        # Partner
        if receipt.get('partner_id'):
            partner = self.partners_manager.get_partner_by_id(receipt['partner_id'])
            if partner:
                tk.Label(tab_items, text=f"Партнёр: {partner['name']}", font=self.font_small_bold_tuple, bg=c['bg']).pack(anchor="w", pady=(2, 5), padx=15)

        # Items treeview
        items_frame = tk.Frame(tab_items, bg=c['frame_bg'], bd=1, relief="flat")
        items_frame.pack(fill="both", expand=True, padx=8, pady=2)

        # Only show the "Возврат" column when this receipt actually has refunds
        has_refund = status in ('refunded', 'partial_refund') or any(
            float(ri.get('refunded_qty', 0) or 0) > 0 for ri in receipt['items'])
        if has_refund:
            columns = ('code', 'name', 'qty', 'price', 'sum', 'refunded')
            headers = ['Код', 'Название', 'Кол-во', 'Цена', 'Сумма', 'Возврат']
            widths  = [80, 230, 45, 90, 90, 70]
        else:
            columns = ('code', 'name', 'qty', 'price', 'sum')
            headers = ['Код', 'Название', 'Кол-во', 'Цена', 'Сумма']
            widths  = [80, 230, 45, 90, 90]
        tree = ttk.Treeview(items_frame, columns=columns, show='headings')
        for col, hdr, w in zip(columns, headers, widths):
            tree.heading(col, text=hdr)
            tree.column(col, width=w, anchor='center' if col not in ('name', 'code') else 'w')
        
        tree_sb = AutoScrollbar(items_frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=tree_sb.set)
        tree_sb.pack(side="right", fill="y")
        tree.pack(side="left", fill="both", expand=True)
        
        tree.tag_configure('refunded', foreground='red')
        tree.tag_configure('partial_refund', foreground='#FFA500')
        tree.tag_configure('live_success', background='#e6ffed')
        tree.tag_configure('live_error', background='#fff0f0')
        
        for ri_item in receipt['items']:
            tag, rtxt = (), ""
            if has_refund:
                rfq = ri_item.get('refunded_qty', 0)
                if rfq >= ri_item['quantity']: tag, rtxt = ('refunded',), f"✗ {rfq}"
                elif rfq > 0: tag, rtxt = ('partial_refund',), f"↩ {rfq}"
            if ri_item.get('live_status') == 1: tag += ('live_success',)
            elif ri_item.get('live_status') == -1: tag += ('live_error',)

            code = ri_item.get('good_code') or ri_item.get('code', '')
            qty = ri_item['quantity']
            qty_str = str(int(qty)) if float(qty).is_integer() else str(qty)
            row_vals = (code, ri_item['name'], qty_str, self.format_amount(ri_item['price']), self.format_amount(ri_item['sum']))
            if has_refund:
                row_vals = row_vals + (rtxt,)
            tree.insert('', 'end', values=row_vals, tags=tag)

        # --- Content for Tab 2 (Payment) ---
        tk.Label(tab_payment, text=get_text('payment_info', self.lang).upper(), font=self.font_bold_tuple, bg=c['bg']).pack(anchor="w", pady=(0, 10))
        
        p = receipt.get('payment', {})
        def add_p_row(lbl, val, color=None, is_total=False):
            if not is_total and val <= 0.01: return
            f = tk.Frame(tab_payment, bg=c['bg'])
            f.pack(fill="x", pady=4)
            tk.Label(f, text=lbl, font=self.font_bold_tuple if is_total else self.font_small_bold_tuple, bg=c['bg']).pack(side="left")
            tk.Label(f, text=self.format_amount(val), font=self.font_large_tuple if is_total else self.font_bold_tuple, bg=c['bg'], fg=color or c['fg']).pack(side="right")

        add_p_row(f"{get_text('total', self.lang).upper()}:", receipt['total'], color=c['accent'], is_total=True)
        ttk.Separator(tab_payment, orient='horizontal').pack(fill="x", pady=8)
        
        add_p_row(f"🪙 {get_text('cash', self.lang)}:", p.get('cash', 0))
        add_p_row(f"💳 {get_text('card', self.lang)}:", p.get('card', 0))
        add_p_row(f"🏦 {get_text('internal', self.lang)}:", p.get('internal', 0))
        
        if p.get('change', 0) > 0.01:
            ttk.Separator(tab_payment, orient='horizontal').pack(fill="x", pady=8)
            add_p_row(f"↩ {get_text('change', self.lang)}:", p.get('change', 0), color=c['success'])
            
        if receipt.get('cashier_user'):
            cashier_disp = str(receipt['cashier_user']).split('/')[-1].strip()
            tk.Label(tab_payment, text=f"👤 Пользователь: {cashier_disp}", font=self.font_small_tuple, bg=c['bg'], fg=c['fg_muted']).pack(side="bottom", anchor="w")

        # ── Pinned bottom zone ──
        bf = dialog.btn_frame
        tk.Label(bf, text=f"ИТОГО: {self.format_amount(receipt['total'])}", font=self.font_bold_tuple, bg=c['bg_secondary'], fg=c['success']).pack(side="left", padx=15)
        
        self._add_dialog_button(dialog, get_text('close', self.lang), dialog.destroy, 'neutral', 'right')
        
        if status != 'refunded' and (self.has_permission('sales_refund_full') or self.has_permission('sales_refund_partial')):
            is_live = receipt.get('live_sent') == 1
            if is_live:
                self._add_dialog_button(dialog, "↩ Возврат", lambda: None, 'danger', 'right', state='disabled')
                tk.Label(bf, text="Live Bot processed", font=self.font_small_tuple, fg=c['error'], bg=c['bg_secondary']).pack(side="right", padx=5)
            else:
                self._add_dialog_button(dialog, "↩ Возврат", lambda: self._show_refund_dialog(receipt, dialog), 'danger', 'right')
            
        self._add_dialog_button(dialog, "🖨 Печать", lambda: self._print_single_receipt(receipt), 'accent', 'right')
        self.bind_dialog_keys(dialog, cancel_callback=dialog.destroy)


    def _show_refund_dialog(self, receipt, parent_popup):
        """Show refund dialog with item-level partial refund support."""
        c = self.colors
        dialog = self.create_modal_dialog(f"Возврат — Чек #{receipt['number']}", width=700, height=600)
        main = dialog.container
        
        tk.Label(main, text="↩ Возврат товаров", font=self.font_title_tuple, fg=c['fg'], bg=c['bg']).pack(pady=(10, 5))
        tk.Label(main, text=f"Чек #{receipt['number']} от {receipt.get('datetime', receipt.get('date', '???'))}", 
                 font=self.font_small_tuple, fg=c['fg_secondary'], bg=c['bg']).pack(pady=(0, 10))
        
        tk.Label(main, text="Выберите товары для возврата:", font=self.font_bold_tuple, bg=c['bg']).pack(anchor="w", padx=20, pady=(0, 10))
        
        # Items container
        items_frame = tk.Frame(main, bg=c['bg'])
        items_frame.pack(fill="x", padx=15)
        
        refund_vars = []  # (item_index, spinbox_var, max_qty, ri_item)

        for i, ri_item in enumerate(receipt.get('items', [])):
            try:
                qty = float(ri_item.get('quantity', 0))
                already_refunded = float(ri_item.get('refunded_qty') or 0)
                max_refundable = qty - already_refunded
                if max_refundable <= 0:
                    continue
            except (ValueError, TypeError):
                continue
            
            row = tk.Frame(items_frame, bg=c['bg_secondary'], relief='flat', bd=1)
            row.pack(fill="x", pady=3)
            
            # Use inner frame for better padding control
            row_inner = tk.Frame(row, bg=c['bg_secondary'], padx=10, pady=8)
            row_inner.pack(fill="x")
            
            tk.Label(row_inner, text=ri_item['name'], font=self.font_normal_tuple, bg=c['bg_secondary'],
                    width=30, anchor="w").pack(side="left")
            tk.Label(row_inner, text=f"(макс: {max_refundable:g})", font=self.font_small_tuple,
                    bg=c['bg_secondary'], fg=c['fg_muted']).pack(side="left", padx=5)
            
            var = tk.DoubleVar(value=0)
            spin = tk.Spinbox(row_inner, from_=0, to=max_refundable, increment=0.5, textvariable=var,
                            font=self.font_normal_tuple, width=6, justify='center')
            spin.pack(side="right")
            var.trace_add('write', lambda *a: update_refund_amount())
            refund_vars.append((i, var, max_refundable, ri_item))
        
        if not refund_vars:
            tk.Label(main, text="Все позиции уже возвращены", font=self.font_bold_tuple,
                    bg=c['bg'], fg=c['error']).pack(pady=40)
            self._add_dialog_button(dialog, get_text('close', self.lang), dialog.destroy, 'neutral', use_grid=True, column=0)
            return
        
        # Reason
        tk.Label(main, text="Причина возврата:", font=self.font_normal_tuple, bg=c['bg']).pack(anchor="w", padx=20, pady=(15, 2))
        reason_var = tk.StringVar()
        reason_entry = tk.Entry(main, textvariable=reason_var, font=self.font_normal_tuple)
        reason_entry.pack(fill="x", padx=20, pady=5)

        # Refund method + live amount
        method_row = tk.Frame(main, bg=c['bg'])
        method_row.pack(fill="x", padx=20, pady=(10, 0))
        tk.Label(method_row, text="Метод возврата:", font=self.font_normal_tuple, bg=c['bg']).pack(side="left")
        method_var = tk.StringVar(value='Наличные')
        method_combo = ttk.Combobox(method_row, textvariable=method_var, state="readonly",
                                    values=['Наличные', 'Карта', 'Внутренний'], width=12)
        method_combo.pack(side="left", padx=8)
        
        amount_label = tk.Label(main, text="", font=self.font_large_tuple, bg=c['bg'], fg=c['error'])
        amount_label.pack(anchor="w", padx=20, pady=(8, 0))

        METHOD_MAP = {'Наличные': 'cash', 'Карта': 'card', 'Внутренний': 'internal'}

        def calc_refund_amount(refund_all=False):
            total = 0.0
            for idx, var, max_qty, ri_item in refund_vars:
                qty = max_qty if refund_all else var.get()
                try:
                    qty = float(qty)
                except (ValueError, TypeError):
                    qty = 0.0
                if qty <= 0:
                    continue
                line_qty = float(ri_item.get('quantity') or 0)
                line_sum = float(ri_item.get('sum') or 0)
                total += line_sum * (qty / line_qty) if line_qty > 0 else 0.0
            return round(total, 2)

        def update_refund_amount():
            total = calc_refund_amount()
            if total > 0:
                amount_label.config(text=f"К возврату: {self.format_amount(total)} ₸")
            else:
                amount_label.config(text="")

        def do_refund(refund_all=False):
            items_to_refund = []
            for idx, var, max_qty, ri_item in refund_vars:
                qty = max_qty if refund_all else var.get()
                try:
                    qty = float(qty)
                except (ValueError, TypeError):
                    qty = 0.0
                if qty > 0:
                    items_to_refund.append((idx, qty))
            
            if not items_to_refund:
                self.show_toast(get_text('select_at_least_one', self.lang), "warning")
                return
            
            refund_amount = calc_refund_amount(refund_all=refund_all)
            
            if refund_all:
                if not messagebox.askyesno(
                        "Возврат всех",
                        f"Вернуть все {len(items_to_refund)} поз. на сумму "
                        f"{self.format_amount(refund_amount)} ₸?\n\n"
                        "Действие изменит остатки и статус чека.",
                        parent=dialog):
                    return
            
            reason = reason_var.get().strip()
            if not reason:
                self.show_toast(get_text('specify_refund_reason', self.lang), "warning")
                reason_entry.focus_set()
                return
            
            # Process refund
            method = METHOD_MAP.get(method_var.get(), 'cash')
            self._process_item_refund(receipt, items_to_refund, reason, refund_method=method,
                                      refund_amount=refund_amount)
            dialog.destroy()
            if parent_popup: parent_popup.destroy()
            self.refresh_sales_history()
        
        # Buttons in pinned zone
        self._add_dialog_button(dialog, get_text('cancel', self.lang), dialog.destroy, 'neutral', use_grid=True, column=2)
        btn_all = self._add_dialog_button(dialog, "↩ Возврат ВСЕХ", lambda: do_refund(True), 'danger', use_grid=True, column=1)
        btn_sel = self._add_dialog_button(dialog, "↩ Возврат выбранных", lambda: do_refund(False), 'primary', use_grid=True, column=0)
        
        # Permission gating
        if not self.has_permission('sales_refund_full'):
            btn_all.config(state='disabled')
        if not self.has_permission('sales_refund_partial'):
            btn_sel.config(state='disabled')
        
        self.bind_dialog_keys(dialog, confirm_callback=lambda: do_refund(False), cancel_callback=dialog.destroy)
        reason_entry.focus_set()

    def _process_item_refund(self, receipt, items_to_refund, reason, refund_method='cash', refund_amount=0.0):
        """Process partial refund with robust error handling and logging.

        C4: the whole refund (item refunded_qty, stock return, receipt
        status, refund log, refund money) commits as ONE transaction — any
        failure rolls everything back."""
        receipt_id = receipt['id']
        now = datetime.now().isoformat()
        self.log_message(f"Starting refund for receipt {receipt_id}. Items: {items_to_refund}", "info")
        
        try:
            # Atomic refund (validates + updates everything in one transaction)
            new_status = self.inventory_ops.refund(
                receipt, items_to_refund, reason,
                refunded_by=self.device_prefix,
                user_name=self._get_user_device_label(),
                device_name=self.sync_name_var.get() if hasattr(self, 'sync_name_var') else self.device_prefix,
                refund_method=refund_method,
            )

            self.log_message(f"Receipt {receipt_id} new status: {new_status}", "info")

            # UI Refresh
            self.refresh_goods_list() if hasattr(self, 'goods_tree') else None
            self.refresh_sales_history() # Ensure history is updated
            
            # Instant Sync Trigger
            if hasattr(self, 'sync_engine') and self.sync_engine:
                self.sync_engine.request_sync()
            messagebox.showinfo("Возврат", f"Возврат оформлен. Статус: {get_text(new_status, self.lang)}"
                                          + (f"\nК возврату: {self.format_amount(refund_amount)} ₸" if refund_amount > 0 else ""))
            
        except ValueError as e:
            self.log_message(f"REFUND REJECTED for receipt {receipt_id}: {e}", "warning")
            messagebox.showerror("Ошибка возврата", str(e))
        except Exception as e:
            import traceback
            error_details = traceback.format_exc()
            self.log_message(f"REFUND ERROR for receipt {receipt_id}: {e}\n{error_details}", "error")
            messagebox.showerror("Ошибка возврата", f"Произошла ошибка при оформлении возврата:\n{e}")
    
    def export_sales_to_excel(self):
        """Export sales data to Excel file in item-level detail."""
        try:
            parts = [p.strip() for p in self.sales_range_var.get().split('-')]
            date_from = datetime.strptime(parts[0], "%d.%m.%Y").date()
            date_to = datetime.strptime(parts[1] if len(parts) > 1 else parts[0], "%d.%m.%Y").date()
            
            df = self.build_sales_export_df(date_from, date_to)
            if df is None:
                messagebox.showinfo(get_text('export', self.lang), get_text('no_data_for_export', self.lang))
                return
            
            today_str = datetime.now().strftime("%d.%m.%Y")
            filename = f"{today_str}.xlsx"
            filepath = filedialog.asksaveasfilename(
                defaultextension=".xlsx",
                filetypes=[("Excel files", "*.xlsx")],
                initialfile=filename
            )
            
            if filepath:
                self.save_sales_excel_file(df, filepath)
                messagebox.showinfo(get_text('export_success', self.lang), 
                                  f"Данные экспортированы:\n{filepath}")
        
        except Exception as e:
            messagebox.showerror(get_text('export_error', self.lang), str(e))

    def build_sales_export_df(self, date_from, date_to):
        """Build item-level sales DataFrame for a date range. Returns None if no data."""
        all_receipts = self.receipts_manager.get_all_receipts()
        filtered = []
        
        for receipt in all_receipts:
            receipt_date = datetime.fromisoformat(receipt['datetime']).date()
            if date_from <= receipt_date <= date_to:
                filtered.append(receipt)
        
        if not filtered:
            return None
        
        export_data = []
        
        # Define headers matching the desired output
        headers = ['№', 'Дата/время', 'Штрих-код', 'Наименование', 'Цена', 'Скидка, %', 'Кол-во', 'Сумма', 'Покупатель']

        for receipt in filtered:
            receipt_number = receipt['number']
            datetime_str = datetime.fromisoformat(receipt['datetime']).strftime("%d.%m.%Y %H:%M")
            
            customer_info = get_text('no_partner', self.lang)
            if receipt.get('partner_id'):
                partner = self.partners_manager.get_partner_by_id(receipt['partner_id'])
                if partner:
                    pid = partner.get('id', '')
                    pname = partner.get('full_name', partner.get('name', ''))
                    if pid and pname:
                        customer_info = f"{pid} {pname}".strip()
                    elif pname:
                        customer_info = pname
            
            for item in receipt['items']:
                item_code = item.get('good_code', item.get('code'))
                item_name = item['name']
                item_display = f"{item_code} {item_name}" if item_code else item_name
                item_qty = item['quantity']
                item_price_per_unit_before_discount = int(round(item['price'] or 0))
                item_sum_total_line = int(round(item['sum'] or 0))
                refunded_qty = item.get('refunded_qty', 0)

                barcode = ""
                _, good_obj = self.goods_manager.get_good(item_code)
                if good_obj:
                    barcode = good_obj.get('barcode', '')

                # If item is fully refunded: price=0, sum=0, buyer=empty
                if refunded_qty >= item_qty:
                    export_data.append({
                        '№': receipt_number,
                        'Дата/время': datetime_str,
                        'Штрих-код': barcode,
                        'Наименование': item_display,
                        'Цена': 0,
                        'Скидка, %': 0,
                        'Кол-во': item_qty,
                        'Сумма': 0,
                        'Покупатель': ''
                    })
                elif refunded_qty > 0:
                    # Partially refunded: export remaining qty normally, refunded qty as zero
                    remaining_qty = item_qty - refunded_qty
                    # Calculate discount percentage
                    total_price_before = item_qty * item_price_per_unit_before_discount
                    discount_percentage = 0.0
                    if total_price_before > 0 and item_sum_total_line < total_price_before:
                        discount_percentage = round((1 - (item_sum_total_line / total_price_before)) * 100, 2)
                    
                    remaining_sum = remaining_qty * item_price_per_unit_before_discount * (1 - discount_percentage / 100)
                    export_data.append({
                        '№': receipt_number,
                        'Дата/время': datetime_str,
                        'Штрих-код': barcode,
                        'Наименование': item_display,
                        'Цена': item_price_per_unit_before_discount,
                        'Скидка, %': discount_percentage,
                        'Кол-во': remaining_qty,
                        'Сумма': round(remaining_sum, 2),
                        'Покупатель': customer_info
                    })
                    # Refunded portion
                    export_data.append({
                        '№': receipt_number,
                        'Дата/время': datetime_str,
                        'Штрих-код': barcode,
                        'Наименование': item_display,
                        'Цена': 0,
                        'Скидка, %': 0,
                        'Кол-во': refunded_qty,
                        'Сумма': 0,
                        'Покупатель': ''
                    })
                else:
                    # Normal item — no refund
                    total_price_before = item_qty * item_price_per_unit_before_discount
                    discount_percentage = 0.0
                    if total_price_before > 0 and item_sum_total_line < total_price_before:
                        discount_percentage = round((1 - (item_sum_total_line / total_price_before)) * 100, 2)
                    
                    export_data.append({
                        '№': receipt_number,
                        'Дата/время': datetime_str,
                        'Штрих-код': barcode,
                        'Наименование': item_display,
                        'Цена': item_price_per_unit_before_discount,
                        'Скидка, %': discount_percentage,
                        'Кол-во': item_qty,
                        'Сумма': item_sum_total_line,
                        'Покупатель': customer_info
                    })
        
        return pd.DataFrame(export_data, columns=headers)  # Ensure column order

    def save_sales_excel_file(self, df, filepath):
        """Write sales DataFrame to an Excel file (same format as manual export)."""
        with pd.ExcelWriter(filepath, engine='openpyxl', mode='w') as writer:
            df.to_excel(writer, index=False, sheet_name='Sales Detailed')

