# -*- coding: utf-8 -*-
"""
PVM.core - Business Analytics Mixin
====================================
Dashboard: revenue, cost, profit, margin, top products, sales insights.
Read-only sources: receipts, receipt_items, goods.purchase_price.
"""

import tkinter as tk
from tkinter import ttk
from datetime import datetime, date, timedelta

from ui_lang import get_text
from ui_dialogs import AutoScrollbar


class BizAnalyticsMixin:
    """Business analytics dashboard methods for GreenLeafApp."""

    # =========================================================================
    # TAB CREATION
    # =========================================================================
    def create_biz_analytics_tab(self, parent):
        """Create the business analytics dashboard inside the given frame."""
        c = self.colors
        self.biz_analytics_frame = parent

        parent.grid_rowconfigure(0, weight=1)
        parent.grid_columnconfigure(0, weight=1)

        self.biz_canvas = tk.Canvas(parent, highlightthickness=0, bg=c['bg'])
        self.biz_scroll = AutoScrollbar(parent, orient="vertical", command=self.biz_canvas.yview)
        self.biz_scrollable = tk.Frame(self.biz_canvas, bg=c['bg'])
        self.biz_canvas.configure(yscrollcommand=self.biz_scroll.set)
        self.biz_canvas_window_id = self.biz_canvas.create_window(
            (0, 0), window=self.biz_scrollable, anchor="nw")

        def _on_scrollable_cfg(e):
            self.biz_canvas.configure(scrollregion=self.biz_canvas.bbox("all"))

        def _on_canvas_cfg(e):
            try:
                self.biz_canvas.itemconfig(self.biz_canvas_window_id, width=e.width)
            except Exception:
                pass
            self.biz_canvas.configure(scrollregion=self.biz_canvas.bbox("all"))

        self.biz_scrollable.bind('<Configure>', _on_scrollable_cfg)
        self.biz_canvas.bind('<Configure>', _on_canvas_cfg)

        self.biz_canvas.pack(side="left", fill="both", expand=True)
        self.biz_scroll.pack(side="right", fill="y")

        self.biz_filter_bar = tk.Frame(self.biz_scrollable, bg=c['bg'])
        self.biz_filter_bar.pack(fill="x", padx=12, pady=(10, 4))
        self.biz_content = tk.Frame(self.biz_scrollable, bg=c['bg'])
        self.biz_content.pack(fill="x", pady=(0, 10))

        self._biz_build_filters()
        self._biz_rebuild()

    # =========================================================================
    # FILTERS
    # =========================================================================
    def _biz_cashier_list(self):
        try:
            with self._db_manager.get_connection() as conn:
                cur = conn.cursor()
                cur.execute("SELECT DISTINCT cashier_user FROM receipts "
                            "WHERE cashier_user IS NOT NULL AND cashier_user != '' "
                            "ORDER BY cashier_user")
                return ['all'] + [r['cashier_user'] for r in cur.fetchall()]
        except Exception as e:
            print(f"[BIZ] cashiers: {e}")
            return ['all']

    def _biz_build_filters(self):
        c = self.colors
        bar = self.biz_filter_bar
        for w in bar.winfo_children():
            w.destroy()

        row1 = tk.Frame(bar, bg=c['bg'])
        row1.pack(fill="x", pady=(0, 4))
        row2 = tk.Frame(bar, bg=c['bg'])
        row2.pack(fill="x")

        tk.Label(row1, text="📈 " + get_text('biz_period', self.lang) + ":",
                 font=self.font_normal_tuple, bg=c['bg'],
                 fg=c['fg_secondary']).pack(side="left")

        def _set_preset(days):
            today = date.today()
            frm = today - timedelta(days=days)
            self.biz_range_var.set(f"{frm.strftime('%d.%m.%Y')} - {today.strftime('%d.%m.%Y')}")
            self._biz_rebuild()

        for label, back in [("Сегодня", 0), ("Неделя", 6), ("Месяц", 29), ("Год", 364)]:
            self._btn(row1, text=label, command=lambda b=back: _set_preset(b),
                      style='neutral', compact=True, cursor="hand2").pack(side="left", padx=(6, 0))

        self._btn(row1, text="🔄 Обновить", command=self._biz_rebuild,
                  style='accent', compact=True, cursor="hand2").pack(side="right")

        self.biz_range_var = tk.StringVar(
            value=f"{date.today().strftime('%d.%m.%Y')} - {date.today().strftime('%d.%m.%Y')}")
        self.biz_range_entry = tk.Entry(row2, textvariable=self.biz_range_var,
                                        font=self.font_normal_tuple, width=26,
                                        relief="solid", bd=1)
        self.biz_range_entry.pack(side="left", padx=(0, 6), ipady=3)
        self.biz_range_entry.bind('<Button-1>', lambda e: self.show_date_range_picker(
            range_var=self.biz_range_var, callback=self._biz_rebuild))
        self.biz_range_entry.bind('<Return>', lambda e: self._biz_rebuild())
        tk.Label(row2, text="(нажмите на поле — календарь)",
                 font=self.font_small_tuple, bg=c['bg'],
                 fg=c['fg_muted']).pack(side="left", padx=(0, 14))

        cashiers = self._biz_cashier_list()
        tk.Label(row2, text="Кассир:", font=self.font_normal_tuple, bg=c['bg'],
                 fg=c['fg_secondary']).pack(side="left", padx=(0, 4))
        self.biz_cashier_var = tk.StringVar(value='all')
        combo = ttk.Combobox(row2, textvariable=self.biz_cashier_var, values=cashiers,
                             width=16, state='readonly', font=self.font_small_tuple)
        combo.pack(side="left")
        self.biz_cashier_var.trace_add('write', lambda *a: self._biz_rebuild())

    # =========================================================================
    # DATA
    # =========================================================================
    def _biz_goods_catalog(self):
        catalog = {}
        try:
            with self._db_manager.get_connection() as conn:
                cur = conn.cursor()
                cur.execute('SELECT code, name, purchase_price FROM goods WHERE is_deleted = 0')
                for row in cur.fetchall():
                    try:
                        catalog[row['code']] = {
                            'name': row['name'],
                            'purchase_price': float(row['purchase_price'] or 0),
                        }
                    except (TypeError, ValueError):
                        pass
        except Exception as e:
            print(f"[BIZ] goods catalog: {e}")
        return catalog

    def _biz_compute(self, d_from, d_to, cashier=None):
        """Compute dashboard metrics for a date range. Read-only."""
        catalog = self._biz_goods_catalog()
        receipts = self.receipts_manager.get_all_receipts()

        revenue = 0.0
        cost = 0.0
        checks = 0
        units = 0
        discounts = 0.0
        by_day = {}
        items = {}
        goods_missing = 0
        payment = {'cash': 0.0, 'card': 0.0, 'internal': 0.0}
        refunds = {'count': 0, 'amount': 0.0}
        by_hour = {}
        by_weekday = {}
        by_cashier = {}
        no_disc = {'checks': 0, 'units': 0.0, 'revenue': 0.0, 'profit': 0.0}

        def _safe_float(v):
            try:
                return float(v or 0)
            except (TypeError, ValueError):
                return 0.0

        for r in receipts:
            try:
                dt_full = datetime.fromisoformat(r['datetime'])
                dt = dt_full.date()
            except Exception:
                continue
            if not (d_from <= dt <= d_to):
                continue
            if cashier and cashier != 'all' and r.get('cashier_user') != cashier:
                continue
            if str(r.get('status', 'completed')) == 'refunded':
                refunds['count'] += 1
                refunds['amount'] += _safe_float(r.get('total'))
                continue

            checks += 1
            day_key = dt.isoformat()
            d = by_day.setdefault(day_key, {'revenue': 0.0, 'profit': 0.0, 'cost': 0.0,
                                            'checks': 0, 'checks_full': 0, 'checks_disc': 0})
            d['checks'] += 1

            h = by_hour.setdefault(dt_full.hour, {'revenue': 0.0, 'profit': 0.0, 'cost': 0.0,
                                                  'checks': 0, 'checks_full': 0, 'checks_disc': 0})
            h['checks'] += 1

            w = by_weekday.setdefault(dt.weekday(), {'revenue': 0.0, 'profit': 0.0, 'checks': 0})
            w['checks'] += 1

            ch_key = (r.get('cashier_user') or '').strip() or 'Без кассира'
            ch = by_cashier.setdefault(ch_key, {'revenue': 0.0, 'checks': 0})
            ch['checks'] += 1

            pay = r.get('payment') or {}
            payment['cash'] += _safe_float(pay.get('cash'))
            payment['card'] += _safe_float(pay.get('card'))
            payment['internal'] += _safe_float(pay.get('internal'))

            disc_amount = _safe_float(r.get('discount'))
            discounts += disc_amount
            subtotal = _safe_float(r.get('subtotal'))
            if subtotal <= 0:
                subtotal = disc_amount + _safe_float(r.get('total'))
            disc_pct = (disc_amount / subtotal * 100) if subtotal > 0 else 0.0
            is_full = disc_amount <= 0
            is_act = disc_pct >= 50.0
            if is_full:
                no_disc['checks'] += 1
                d['checks_full'] += 1
                h['checks_full'] += 1
            if is_act:
                d['checks_disc'] += 1
                h['checks_disc'] += 1

            for it in r.get('items', []):
                try:
                    qty_sold = float(it.get('quantity', 0) or 0)
                    qty = qty_sold - float(it.get('refunded_qty', 0) or 0)
                except (TypeError, ValueError):
                    continue
                if qty <= 0:
                    continue
                code = it.get('good_code', '')
                name = it.get('name', '') or code
                try:
                    # Revenue must reflect what the buyer actually paid: item['sum']
                    # already includes any discount (price is the pre-discount unit
                    # price). Recompute per-unit from sum and apply the effective
                    # qty (refunds excluded), matching the sales Excel export.
                    line_sum = it.get('sum')
                    if line_sum is None:
                        line_sum = float(it.get('price', 0) or 0) * qty_sold
                    unit_rev = float(line_sum or 0) / qty_sold if qty_sold else 0.0
                    line_rev = unit_rev * qty
                except (TypeError, ValueError):
                    continue

                g = catalog.get(code)
                # Real cost is the card's purchase price. On top of the classic
                # margin, the shop gets a weekly 10% internal balance from the
                # supplier website (10% of the purchase amount), so:
                # profit = (revenue - cost) + 10% * cost.
                unit_cost = g['purchase_price'] if g else 0.0
                if g and g['purchase_price'] <= 0:
                    goods_missing += 1
                line_cost = unit_cost * qty
                line_profit = line_rev - line_cost + 0.10 * line_cost

                revenue += line_rev
                cost += line_cost
                units += qty
                if is_full:
                    no_disc['units'] += qty
                    no_disc['revenue'] += line_rev
                    no_disc['profit'] += line_profit

                agg = items.setdefault(code, {'name': name, 'qty': 0.0, 'revenue': 0.0, 'cost': 0.0})
                agg['qty'] += qty
                agg['revenue'] += line_rev
                agg['cost'] += line_cost
                d['revenue'] += line_rev
                d['profit'] += line_profit
                d['cost'] += line_cost
                h['revenue'] += line_rev
                h['profit'] += line_profit
                h['cost'] += line_cost
                w['revenue'] += line_rev
                w['profit'] += line_profit
                ch['revenue'] += line_rev

        profit = revenue - cost + 0.10 * cost
        margin = (profit / revenue * 100) if revenue else 0.0
        avg_check = revenue / checks if checks else 0.0

        return {
            'revenue': round(revenue, 2),
            'cost': round(cost, 2),
            'profit': round(profit, 2),
            'margin': round(margin, 1),
            'checks': checks,
            'avg_check': round(avg_check, 2),
            'units': round(units, 1),
            'units_per_check': round(units / checks, 1) if checks else 0.0,
            'discounts': round(discounts, 2),
            'payment': {k: round(v, 2) for k, v in payment.items()},
            'refunds': {'count': refunds['count'], 'amount': round(refunds['amount'], 2)},
            'no_disc': {
                'checks': no_disc['checks'],
                'units': round(no_disc['units'], 1),
                'revenue': round(no_disc['revenue'], 2),
                'profit': round(no_disc['profit'], 2),
            },
            'by_day': by_day,
            'by_hour': by_hour,
            'by_weekday': by_weekday,
            'by_cashier': by_cashier,
            'items': items,
            'goods_missing': goods_missing,
        }

    # =========================================================================
    # RENDER
    # =========================================================================
    def _biz_rebuild(self):
        if not hasattr(self, 'biz_content') or not self.biz_content.winfo_exists():
            return
        if not getattr(self, 'receipts_manager', None):
            return
        for w in self.biz_content.winfo_children():
            w.destroy()

        raw = self.biz_range_var.get().strip()
        parts = [p.strip() for p in raw.split('-')]
        try:
            d_from = datetime.strptime(parts[0], "%d.%m.%Y").date()
            d_to = datetime.strptime(parts[1] if len(parts) > 1 else parts[0], "%d.%m.%Y").date()
        except Exception:
            d_from = d_to = date.today()
        if d_to < d_from:
            d_from, d_to = d_to, d_from
        span = (d_to - d_from).days + 1

        cashier = self.biz_cashier_var.get() if hasattr(self, 'biz_cashier_var') else 'all'

        data = self._biz_compute(d_from, d_to, cashier)
        prev = None
        if span > 0:
            prev_to = d_from - timedelta(days=1)
            prev_from = d_from - timedelta(days=span)
            prev = self._biz_compute(prev_from, prev_to, cashier)

        c = self.colors
        content = self.biz_content

        if data['checks'] == 0:
            empty = tk.Frame(content, bg=c['bg'])
            empty.pack(fill="x", pady=40)
            tk.Label(empty, text="📭", font=("Arial", 36), bg=c['bg'], fg=c['fg_muted']).pack()
            tk.Label(empty, text="Нет продаж за выбранный период",
                     font=self.font_normal_tuple, bg=c['bg'], fg=c['fg_muted']).pack(pady=6)
            self.enable_scroll_area(self.biz_canvas, self.biz_scrollable)
            return

        self._biz_render_kpis(content, data, prev, span)
        self._biz_render_charts(content, data, prev, d_from, d_to, span)
        self._biz_render_products(content, data, d_from, span)
        self._biz_render_sales(content, data, span, d_from)

        if data['goods_missing'] > 0:
            tk.Label(content, text="⚠️ Себестоимость части товаров не заполнена (0) — "
                                   "прибыль и маржа посчитаны по данным карточки товара.",
                     font=self.font_small_tuple, bg=c['bg'], fg=c['fg_muted'],
                     anchor="w").pack(fill="x", padx=14, pady=(8, 0))

        self.enable_scroll_area(self.biz_canvas, self.biz_scrollable)

    def _biz_delta(self, data, prev, key):
        if not prev:
            return ""
        dkey = key[4:] if key.startswith('biz_') else key
        cur = data.get(dkey, 0)
        old = prev.get(dkey, 0)
        if isinstance(cur, (int, float)) and isinstance(old, (int, float)) and dkey == 'margin':
            diff = cur - old
            if diff > 0.1:
                return f"▲ {diff:+.1f} п.п."
            if diff < -0.1:
                return f"▼ {diff:+.1f} п.п."
            return "—"
        if old == 0:
            return "—"
        pct = (cur - old) / old * 100
        if pct > 0.1:
            return f"▲ {pct:+.0f}%"
        if pct < -0.1:
            return f"▼ {pct:+.0f}%"
        return "—"

    def _biz_render_kpis(self, parent, data, prev, span):
        c = self.colors
        grid = tk.Frame(parent, bg=c['bg'])
        grid.pack(fill="x", pady=(6, 4))
        for col in range(3):
            grid.grid_columnconfigure(col, weight=1, uniform='kpi')

        days = span if span > 0 else 1
        rev_day = data['revenue'] / days
        profit_day = data['profit'] / days
        checks_day = data['checks'] / days
        cost_share = (data['cost'] / data['revenue'] * 100) if data['revenue'] else 0.0
        nd_units = data['no_disc']['units']
        nd_share = (nd_units / data['units'] * 100) if data['units'] else 0.0

        rows = [
            [
                ('biz_revenue', data['revenue'], c['accent'], 'money', False,
                 self.format_amount(rev_day) + " ₸", "в день"),
                ('biz_cost', data['cost'], c['fg_secondary'], 'money', False,
                 f"{cost_share:.0f}%", "от выручки"),
                ('biz_profit', data['profit'], c['success'], 'money', True,
                 self.format_amount(profit_day) + " ₸", "в день"),
            ],
            [
                ('biz_checks', data['checks'], c['fg'], 'count', False,
                 f"{checks_day:.1f}", "в день"),
                ('biz_margin', data['margin'], c['warning'], 'percent', False,
                 f"{nd_share:.0f}%", "продаж без скидки"),
                ('biz_avg_check', data['avg_check'], c['accent'], 'money', False,
                 f"{data['units_per_check']:.1f} шт", "в чеке"),
            ],
        ]
        for r, row_cards in enumerate(rows):
            for col, (key, val, color, fmt, is_main, sub_val, sub_label) in enumerate(row_cards):
                card = tk.Frame(grid, bg=c['frame_bg'], padx=14, pady=8,
                                highlightbackground=c['accent'] if is_main else c['border'],
                                highlightthickness=1)
                card.grid(row=r, column=col, sticky="nsew", padx=3, pady=3)
                card.grid_columnconfigure(1, weight=1)
                tk.Label(card, text=get_text(key, self.lang), font=self.font_small_tuple,
                         bg=c['frame_bg'], fg=c['fg_muted']).grid(row=0, column=0,
                                                                  columnspan=2, sticky="w")
                if fmt == 'money':
                    text = self.format_amount(val) + " ₸"
                elif fmt == 'percent':
                    text = f"{val:.1f}%"
                else:
                    text = f"{int(val)}"
                fsize = 20 if is_main else 15
                tk.Label(card, text=text, font=(self.font_family, fsize, "bold"),
                         bg=c['frame_bg'], fg=color).grid(row=1, column=0, sticky="w", pady=(2, 0))
                tk.Label(card, text=sub_val, font=(self.font_family, 12, "bold"),
                         bg=c['frame_bg'], fg=c['success'] if is_main else c['fg_secondary']
                         ).grid(row=1, column=1, sticky="e", pady=(2, 0))
                delta = self._biz_delta(data, prev, key)
                dcolor = c['fg_muted']
                if delta.startswith('▲'):
                    dcolor = c['success']
                elif delta.startswith('▼'):
                    dcolor = c['error']
                tk.Label(card, text=(delta if delta else "—") + " к прошлому периоду",
                         font=self.font_small_tuple, bg=c['frame_bg'],
                         fg=dcolor).grid(row=2, column=0, sticky="w", pady=(2, 0))
                tk.Label(card, text=sub_label, font=self.font_small_tuple,
                         bg=c['frame_bg'], fg=c['fg_muted']).grid(row=2, column=1,
                                                                  sticky="e", pady=(2, 0))

    # =========================================================================
    # CHARTS (Canvas)
    # =========================================================================
    def _biz_bucket_key(self, day, span):
        if span <= 31:
            return day.isoformat()
        if span <= 120:
            s = day - timedelta(days=day.weekday())
            return f"W{s.isocalendar()[0]}-{s.isocalendar()[1]}"
        return day.strftime("%Y-%m")

    def _biz_bucket_label(self, day, span):
        if span <= 31:
            return day.strftime("%d.%m")
        if span <= 120:
            s = day - timedelta(days=day.weekday())
            return s.strftime("%d.%m")
        return day.strftime("%m.%y")

    def _biz_buckets(self, by_day, d_from, d_to, span_override=None):
        span = span_override or (d_to - d_from).days + 1
        seen = []
        cur = d_from
        while cur <= d_to:
            k = self._biz_bucket_key(cur, span)
            if not seen or seen[-1][0] != k:
                seen.append((k, self._biz_bucket_label(cur, span)))
            cur += timedelta(days=1)
        result = []
        for k, label in seen:
            d = by_day.get(k) or {}
            result.append((
                label,
                d.get('revenue', 0.0),
                d.get('profit', 0.0),
                d.get('cost', 0.0),
                d.get('checks', 0),
                d.get('checks_full', 0),
                d.get('checks_disc', 0),
            ))
        return result

    def _biz_short(self, v):
        v = float(v or 0)
        if v >= 1000000:
            return f"{v / 1000000:.1f}M"
        if v >= 1000:
            return f"{v / 1000:.0f}K"
        return f"{v:.0f}"

    def _biz_shade(self, hex_color, factor):
        """Darken (factor < 1) or lighten (factor > 1) a #RRGGBB color."""
        try:
            hex_color = hex_color.lstrip('#')
            r, g, b = (int(hex_color[i:i + 2], 16) for i in (0, 2, 4))
        except (ValueError, AttributeError):
            return hex_color
        r = max(0, min(255, int(r * factor)))
        g = max(0, min(255, int(g * factor)))
        b = max(0, min(255, int(b * factor)))
        return f'#{r:02x}{g:02x}{b:02x}'

    def _biz_combo_buckets(self, data, d_from, d_to, span):
        mode = self.biz_bucket_var.get()
        if mode == 'По часам':
            out = []
            for k in range(24):
                d = data['by_hour'].get(k) or {}
                out.append((
                    f"{k:02d}ч",
                    d.get('revenue', 0.0),
                    d.get('profit', 0.0),
                    d.get('cost', 0.0),
                    d.get('checks', 0),
                    d.get('checks_full', 0),
                    d.get('checks_disc', 0),
                ))
            return out
        limits = {'По дням': 31, 'По неделям': 120, 'По месяцам': 9999}
        return self._biz_buckets(data['by_day'], d_from, d_to,
                                 span_override=limits.get(mode, 31))

    def _biz_draw_combo(self, canvas, buckets, metric):
        state = {'w': 620, 'pad_r': 34, 'pad_t': 28}
        info = []
        c = self.colors
        dark = self._biz_shade(c['accent'], 0.5)
        light = self._biz_shade(c['accent'], 1.4)
        idx = {'Продажи': 4, 'Продажи 100%': 5, 'Продажи 50%': 6}.get(metric)
        bar_color = {'Продажи': c['accent'], 'Продажи 100%': c['success'],
                     'Продажи 50%': c['warning']}.get(metric)

        def _draw():
            canvas.delete("all")
            w = canvas.winfo_width()
            if w < 60:
                w = 620
            state['w'] = w
            h = 260
            pad_l, pad_r, pad_t, pad_b = 56, 34, 28, 30
            state['pad_r'] = pad_r
            state['pad_t'] = pad_t
            inner_w = w - pad_l - pad_r
            inner_h = h - pad_t - pad_b
            n = max(len(buckets), 1)
            slot = inner_w / n
            step = max(1, (n + 13) // 14)
            info.clear()

            if metric == 'Выручка':
                vals = [b[1] for b in buckets]
                plot_vals = [max(b[2] + b[3], b[1]) for b in buckets]
            else:
                vals = [float(b[idx]) for b in buckets]
                plot_vals = vals
            max_v = max(plot_vals + [1.0])

            for g in range(5):
                gy = pad_t + inner_h - (g / 4) * inner_h
                canvas.create_line(pad_l, gy, w - pad_r, gy, fill="#e2e2e2", width=1)
                canvas.create_text(pad_l - 5, gy, text=self._biz_short(max_v * g / 4),
                                   anchor="e", font=("Arial", 8), fill=c['fg_muted'])
            canvas.create_line(pad_l, pad_t + inner_h, w - pad_r, pad_t + inner_h,
                               fill="#c9c9c9", width=1)

            mean = sum(plot_vals) / len(plot_vals)
            bar_w = max(4, min(40, slot * 0.5))

            for i, b in enumerate(buckets):
                if len(b[0]) == 5 and '.' in b[0]:
                    try:
                        if datetime.strptime(b[0], "%d.%m").date().weekday() >= 5:
                            cx = pad_l + slot * i + slot / 2
                            canvas.create_rectangle(cx - slot / 2, pad_t, cx + slot / 2,
                                                    pad_t + inner_h, fill=c['warning_bg'],
                                                    outline="")
                    except Exception:
                        pass

            if metric == 'Выручка':
                for i, b in enumerate(buckets):
                    cx = pad_l + slot * i + slot / 2
                    rev, prof, cost_v = b[1], b[2], b[3]
                    stack_total = max(cost_v + prof, rev)
                    stack_h = (stack_total / max_v) * inner_h
                    cost_h = (min(cost_v, stack_total) / max_v) * inner_h
                    y_top = pad_t + inner_h - stack_h
                    y_cost = pad_t + inner_h - cost_h
                    canvas.create_rectangle(cx - bar_w / 2, y_cost, cx + bar_w / 2,
                                            pad_t + inner_h, fill=dark, outline="")
                    if prof > 0:
                        canvas.create_rectangle(cx - bar_w / 2, y_top, cx + bar_w / 2,
                                                y_cost, fill=light, outline="")
                    info.append((cx, b[0], 'rev', rev, prof, cost_v, mean))
                    if i % step == 0:
                        canvas.create_text(cx, y_top - 8, text=self._biz_short(stack_total),
                                           font=("Arial", 8), fill=c['fg_secondary'])
                        canvas.create_text(cx, h - 12, text=b[0], font=("Arial", 8),
                                           fill=c['fg_muted'])
            else:
                for i, b in enumerate(buckets):
                    cx = pad_l + slot * i + slot / 2
                    v = b[idx]
                    vh = (v / max_v) * inner_h
                    y0 = pad_t + inner_h - vh
                    canvas.create_rectangle(cx - bar_w / 2, y0, cx + bar_w / 2,
                                            pad_t + inner_h, fill=bar_color, outline="")
                    info.append((cx, b[0], 'cnt', v, mean))
                    if i % step == 0:
                        canvas.create_text(cx, y0 - 8, text=f"{v:.0f}",
                                           font=("Arial", 8), fill=c['fg_secondary'])
                        canvas.create_text(cx, h - 12, text=b[0], font=("Arial", 8),
                                           fill=c['fg_muted'])

            total = sum(vals)
            if metric == 'Выручка':
                total_txt = f"Выручка всего: {self._biz_short(total)} ₸"
            else:
                total_txt = f"Всего: {total:.0f}"
            canvas.create_text(pad_l, pad_t - 14, anchor="nw", font=("Arial", 8),
                               fill=c['fg_muted'],
                               text=f"{total_txt} · прогноз (средн.): {self._biz_short(mean)}")

            # Forecast line (прогноз по средним показателям)
            if mean > 0:
                my = pad_t + inner_h - (mean / max_v) * inner_h
                canvas.create_line(pad_l, my, w - pad_r, my, fill=c['fg'], width=1,
                                   dash=(4, 3))
                canvas.create_text(w - pad_r + 6, my, text="прогноз", anchor="w",
                                   font=("Arial", 7), fill=c['fg_muted'])

            # Legend (top-right)
            lx = w - pad_r
            if metric == 'Выручка':
                canvas.create_rectangle(lx - 96, pad_t - 12, lx - 84, pad_t - 2,
                                        fill=dark, outline="")
                canvas.create_text(lx - 100, pad_t - 7, text="Себестоимость", anchor="e",
                                   font=("Arial", 8), fill=c['fg_muted'])
                canvas.create_rectangle(lx - 40, pad_t - 12, lx - 28, pad_t - 2,
                                        fill=light, outline="")
                canvas.create_text(lx - 44, pad_t - 7, text="Прибыль", anchor="e",
                                   font=("Arial", 8), fill=c['fg_muted'])
            else:
                canvas.create_rectangle(lx - 62, pad_t - 12, lx - 50, pad_t - 2,
                                        fill=bar_color, outline="")
                canvas.create_text(lx - 66, pad_t - 7, text=metric, anchor="e",
                                   font=("Arial", 8), fill=c['fg_muted'])

        def _on_motion(e):
            canvas.delete("biz_tip")
            if not info:
                return
            best = min(info, key=lambda t: abs(t[0] - e.x))
            x = min(best[0], state['w'] - state['pad_r'] - 12)
            if best[2] == 'rev':
                _, label, _, rev, prof, cost_v, mean = best
                m = (prof / rev * 100) if rev else 0
                text = (f"{label}: выручка {self.format_amount(rev)} ₸ · "
                        f"себестоимость {self.format_amount(cost_v)} ₸ · "
                        f"прибыль {self.format_amount(prof)} ₸ (+10% бонус) · "
                        f"маржа {m:.0f}% · "
                        f"прогноз {self.format_amount(mean)} ₸")
            else:
                _, label, _, v, mean = best
                text = f"{label}: {v:.0f} · прогноз {mean:.0f}"
            canvas.create_text(x, state['pad_t'] + 4, anchor="nw", tags="biz_tip",
                               text=text, font=("Arial", 8), fill=self.colors['fg'])

        canvas.after(50, _draw)
        canvas.bind('<Configure>', lambda e: _draw())
        canvas.bind('<Motion>', _on_motion)
        canvas.bind('<Leave>', lambda e: canvas.delete("biz_tip"))

    def _biz_draw_weekday(self, canvas, by_weekday, wd_counts):
        names = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс']
        c = self.colors

        def _draw():
            canvas.delete("all")
            w = canvas.winfo_width()
            if w < 60:
                w = 620
            h = 210
            pad_l, pad_r, pad_t, pad_b = 40, 16, 26, 30
            inner_w = w - pad_l - pad_r
            inner_h = h - pad_t - pad_b
            avgs = []
            for wd in range(7):
                cnt = wd_counts.get(wd, 0) or 1
                d = by_weekday.get(wd) or {'revenue': 0.0}
                avgs.append(d['revenue'] / cnt)
            max_v = max(avgs + [1.0])
            for g in range(5):
                gy = pad_t + inner_h - (g / 4) * inner_h
                canvas.create_line(pad_l, gy, w - pad_r, gy, fill="#e2e2e2", width=1)
                canvas.create_text(pad_l - 5, gy, text=self._biz_short(max_v * g / 4),
                                   anchor="e", font=("Arial", 8), fill=c['fg_muted'])
            canvas.create_line(pad_l, pad_t + inner_h, w - pad_r, pad_t + inner_h,
                               fill="#c9c9c9", width=1)
            mean = sum(avgs) / 7
            if mean > 0:
                my = pad_t + inner_h - (mean / max_v) * inner_h
                canvas.create_line(pad_l, my, w - pad_r, my, fill=c['warning'], width=1,
                                   dash=(3, 3))
                canvas.create_text(w - pad_r - 4, my - 2, text=f"среднее {self._biz_short(mean)}",
                                   anchor="se", font=("Arial", 8), fill=c['warning'])
            slot = inner_w / 7
            bar_w = max(4, min(40, slot * 0.5))
            for i, avg in enumerate(avgs):
                cx = pad_l + slot * i + slot / 2
                bh = (avg / max_v) * inner_h
                y0 = pad_t + inner_h - bh
                if i >= 5:
                    canvas.create_rectangle(cx - slot / 2, pad_t, cx + slot / 2,
                                            pad_t + inner_h, fill=c['warning_bg'], outline="")
                canvas.create_rectangle(cx - bar_w / 2, y0, cx + bar_w / 2, pad_t + inner_h,
                                        fill=c['warning'] if i >= 5 else c['accent'], outline="")
                canvas.create_text(cx, y0 - 8, text=self._biz_short(avg), font=("Arial", 8),
                                   fill=c['fg_secondary'])
                canvas.create_text(cx, h - 12, text=names[i], font=("Arial", 8),
                                   fill=c['fg_muted'])
        canvas.after(50, _draw)
        canvas.bind('<Configure>', lambda e: _draw())

    def _biz_render_charts(self, parent, data, prev, d_from, d_to, span):
        c = self.colors
        if not hasattr(self, 'biz_bucket_var'):
            self.biz_bucket_var = tk.StringVar(value='По часам')
            self.biz_metric_var = tk.StringVar(value='Выручка')

        f = tk.LabelFrame(parent, text=" 📈 График ",
                          font=self.font_bold_tuple, bg=c['frame_bg'], fg=c['fg'])
        f.pack(fill="x", padx=8, pady=4)

        bar = tk.Frame(f, bg=c['frame_bg'])
        bar.pack(fill="x", padx=10, pady=(6, 0))
        tk.Label(bar, text="Период:", font=self.font_small_tuple,
                 bg=c['frame_bg'], fg=c['fg_secondary']).pack(side="left")
        b1 = ttk.Combobox(bar, textvariable=self.biz_bucket_var,
                          values=['По часам', 'По дням', 'По неделям', 'По месяцам'],
                          width=12, state='readonly', font=self.font_small_tuple)
        b1.pack(side="left", padx=(4, 14))
        tk.Label(bar, text="Показатель:", font=self.font_small_tuple,
                 bg=c['frame_bg'], fg=c['fg_secondary']).pack(side="left")
        b2 = ttk.Combobox(bar, textvariable=self.biz_metric_var,
                          values=['Выручка', 'Продажи', 'Продажи 100%', 'Продажи 50%'],
                          width=14, state='readonly', font=self.font_small_tuple)
        b2.pack(side="left", padx=(4, 0))

        cv = tk.Canvas(f, height=260, bg=c['frame_bg'], highlightthickness=0)
        cv.pack(fill="x", padx=8, pady=6)

        def _redraw(*_a):
            cv.delete("all")
            buckets = self._biz_combo_buckets(data, d_from, d_to, span)
            self._biz_draw_combo(cv, buckets, self.biz_metric_var.get())

        b1.bind('<<ComboboxSelected>>', _redraw)
        b2.bind('<<ComboboxSelected>>', _redraw)
        _redraw()

    # =========================================================================
    # PRODUCTS & SALES
    # =========================================================================
    def _biz_render_products(self, parent, data, d_from, span):
        c = self.colors
        items = sorted(data['items'].values(), key=lambda x: x['revenue'], reverse=True)[:10]

        row = tk.Frame(parent, bg=c['bg'])
        row.pack(fill="x", padx=8, pady=4)
        row.grid_columnconfigure(0, weight=3)
        row.grid_columnconfigure(1, weight=2)

        f = tk.LabelFrame(row, text=" 🏆 Товары (топ по выручке) ",
                          font=self.font_bold_tuple, bg=c['frame_bg'], fg=c['fg'])
        f.grid(row=0, column=0, sticky="nsew", padx=(0, 3))
        if not items:
            tk.Label(f, text="Нет данных о товарах за выбранный период",
                     font=self.font_normal_tuple, bg=c['frame_bg'],
                     fg=c['fg_muted']).pack(pady=10)
        else:
            cols = ('name', 'qty', 'revenue', 'profit', 'margin')
            tree = ttk.Treeview(f, columns=cols, show='headings', height=min(len(items), 8))
            tree.heading('name', text='Товар')
            tree.heading('qty', text='Кол-во')
            tree.heading('revenue', text='Выручка')
            tree.heading('profit', text='Прибыль')
            tree.heading('margin', text='Маржа %')
            tree.column('name', width=180, anchor='w', stretch=True)
            tree.column('qty', width=60, anchor='center', stretch=False)
            tree.column('revenue', width=110, anchor='e', stretch=False)
            tree.column('profit', width=110, anchor='e', stretch=False)
            tree.column('margin', width=70, anchor='center', stretch=False)
            sb = AutoScrollbar(f, orient="vertical", command=tree.yview)
            tree.configure(yscrollcommand=sb.set)
            tree.pack(side="left", fill="both", expand=True, padx=6, pady=6)
            sb.pack(side="right", fill="y")
            tree.tag_configure('even', background=c.get('bg_tertiary', c['bg']))
            for i, it in enumerate(items):
                profit = it['revenue'] - 0.9 * it['cost']
                margin = (profit / it['revenue'] * 100) if it['revenue'] else 0
                tree.insert('', 'end', values=(
                    it['name'][:45],
                    f"{it['qty']:.0f}",
                    self.format_amount(it['revenue']) + " ₸",
                    self.format_amount(profit) + " ₸",
                    f"{margin:.0f}%"
                ), tags=('even',) if i % 2 else ())

        ins_col = tk.Frame(row, bg=c['bg'])
        ins_col.grid(row=0, column=1, sticky="nsew", padx=(3, 0))

        # --- Payment methods ---
        f_pay = tk.LabelFrame(ins_col, text=" 💳 Способы оплаты ",
                              font=self.font_small_bold_tuple, bg=c['frame_bg'], fg=c['fg'])
        f_pay.pack(fill="x", pady=(0, 3))
        pay = data['payment']
        total_pay = pay['cash'] + pay['card'] + pay['internal']
        for name, val, color in [("Наличные", pay['cash'], c['success']),
                                 ("Карта", pay['card'], c['accent']),
                                 ("Внутр. счёт", pay['internal'], c['warning'])]:
            prow = tk.Frame(f_pay, bg=c['frame_bg'])
            prow.pack(fill="x", padx=8, pady=3)
            tk.Label(prow, text=name, font=self.font_small_tuple, bg=c['frame_bg'],
                     fg=c['fg'], width=10, anchor="w").pack(side="left")
            pct = (val / total_pay * 100) if total_pay else 0
            bar = tk.Frame(prow, bg=c['bg_tertiary'], width=90, height=6)
            bar.pack_propagate(False)
            bar.pack(side="left", padx=(4, 8))
            tk.Frame(bar, bg=color, width=max(2, int(90 * pct / 100)),
                     height=6).pack(side="left")
            tk.Label(prow, text=f"{self.format_amount(val)} ₸ · {pct:.0f}%",
                     font=self.font_small_tuple, bg=c['frame_bg'],
                     fg=c['fg_secondary']).pack(side="right")

        # --- Discounts & refunds ---
        f_disc = tk.LabelFrame(ins_col, text=" 🏷 Скидки и возвраты ",
                               font=self.font_small_bold_tuple, bg=c['frame_bg'], fg=c['fg'])
        f_disc.pack(fill="x", pady=(0, 3))
        disc = data['discounts']
        disc_share = (disc / data['revenue'] * 100) if data['revenue'] else 0
        rf = data['refunds']
        tk.Label(f_disc, text=f"• Скидки: {self.format_amount(disc)} ₸ ({disc_share:.0f}% от выручки)",
                 font=self.font_small_tuple, bg=c['frame_bg'], fg=c['warning'],
                 anchor="w").pack(fill="x", padx=8, pady=2)
        tk.Label(f_disc, text=f"• Возвратов: {rf['count']} чеков на "
                              f"{self.format_amount(rf['amount'])} ₸",
                 font=self.font_small_tuple, bg=c['frame_bg'],
                 fg=c['error'] if rf['count'] else c['fg_muted'],
                 anchor="w").pack(fill="x", padx=8, pady=2)
        if data['checks']:
            tk.Label(f_disc, text=f"• Скидка в среднем на чек: "
                                  f"{self.format_amount(disc / data['checks'])} ₸",
                     font=self.font_small_tuple, bg=c['frame_bg'], fg=c['fg_muted'],
                     anchor="w").pack(fill="x", padx=8, pady=2)

        # --- Sales without discounts + peaks ---
        nd = data['no_disc']
        nd_share = (nd['units'] / data['units'] * 100) if data['units'] else 0.0
        nd_avg = (nd['revenue'] / nd['checks']) if nd['checks'] else 0.0
        wd_counts = {}
        cur = d_from
        while cur <= d_from + timedelta(days=span - 1):
            wd_counts[cur.weekday()] = wd_counts.get(cur.weekday(), 0) + 1
            cur += timedelta(days=1)
        best_wd = None
        best_avg = -1.0
        for wd, cnt in wd_counts.items():
            d = data['by_weekday'].get(wd) or {'revenue': 0.0}
            a = d['revenue'] / cnt
            if a > best_avg:
                best_avg = a
                best_wd = wd
        peak_hour = max(range(24),
                        key=lambda k: (data['by_hour'].get(k) or {}).get('revenue', 0))
        wd_names = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс']
        f_nd = tk.LabelFrame(ins_col, text=" 💰 Продажи без скидок ",
                             font=self.font_small_bold_tuple, bg=c['frame_bg'], fg=c['fg'])
        f_nd.pack(fill="x")
        tk.Label(f_nd, text=f"• Без скидки: {nd_share:.0f}% продаж (по шт)",
                 font=self.font_small_tuple, bg=c['frame_bg'], fg=c['success'],
                 anchor="w").pack(fill="x", padx=8, pady=2)
        tk.Label(f_nd, text=f"• Средний чек: {self.format_amount(nd_avg)} ₸",
                 font=self.font_small_tuple, bg=c['frame_bg'], fg=c['fg'],
                 anchor="w").pack(fill="x", padx=8, pady=2)
        tk.Label(f_nd, text=f"• Прибыль без скидок: {self.format_amount(nd['profit'])} ₸",
                 font=self.font_small_tuple, bg=c['frame_bg'], fg=c['fg'],
                 anchor="w").pack(fill="x", padx=8, pady=2)
        tk.Label(f_nd, text=f"• Пик: {wd_names[best_wd] if best_wd is not None else '—'} · "
                            f"{peak_hour:02d}:00–{peak_hour + 1:02d}:59",
                 font=self.font_small_tuple, bg=c['frame_bg'], fg=c['fg_secondary'],
                 anchor="w").pack(fill="x", padx=8, pady=2)

    def _biz_render_sales(self, parent, data, span, d_from):
        c = self.colors
        f = tk.LabelFrame(parent, text=" 📅 Продажи и лучшие дни ",
                          font=self.font_bold_tuple, bg=c['frame_bg'], fg=c['fg'])
        f.pack(fill="x", padx=8, pady=4)

        info = tk.Frame(f, bg=c['frame_bg'])
        info.pack(fill="x", padx=10, pady=(6, 4))
        avg_checks = data['checks'] / span if span else 0
        days_with = len([1 for d in data['by_day'].values() if d['revenue'] > 0])
        tk.Label(info, text=f"Среднее чеков в день: {avg_checks:.1f} · "
                            f"Средний чек: {self.format_amount(data['avg_check'])} ₸ · "
                            f"Товаров в чеке: {data['units_per_check']} · "
                            f"Дней с продажами: {days_with} из {span}",
                 font=self.font_normal_tuple, bg=c['frame_bg'], fg=c['fg']).pack(anchor="w", pady=1)

        body = tk.Frame(f, bg=c['frame_bg'])
        body.pack(fill="x", padx=10, pady=(0, 8))
        body.grid_columnconfigure(0, weight=1)
        body.grid_columnconfigure(1, weight=1)

        f_left = tk.LabelFrame(body, text=" 🏆 Лучшие дни ",
                               font=self.font_small_bold_tuple, bg=c['frame_bg'], fg=c['fg'])
        f_left.grid(row=0, column=0, sticky="nsew", padx=(0, 4))
        days = sorted(data['by_day'].items(), key=lambda x: x[1]['revenue'], reverse=True)[:5]
        if days:
            cols = ('date', 'checks', 'revenue', 'profit', 'margin')
            tree = ttk.Treeview(f_left, columns=cols, show='headings', height=5)
            tree.heading('date', text='Дата')
            tree.heading('checks', text='Чеков')
            tree.heading('revenue', text='Выручка')
            tree.heading('profit', text='Прибыль')
            tree.heading('margin', text='Маржа %')
            tree.column('date', width=118, anchor='w', stretch=True)
            tree.column('checks', width=52, anchor='center', stretch=False)
            tree.column('revenue', width=95, anchor='e', stretch=False)
            tree.column('profit', width=95, anchor='e', stretch=False)
            tree.column('margin', width=62, anchor='center', stretch=False)
            tree.pack(fill="both", expand=True, padx=6, pady=6)
            tree.tag_configure('even', background=c.get('bg_tertiary', c['bg']))
            tree.tag_configure('total', background=c.get('bg_tertiary', c['bg']),
                               font=self.font_small_bold_tuple)
            for i, (dkey, d) in enumerate(days):
                medal = ["🥇", "🥈", "🥉"][i] if i < 3 else f"{i + 1}."
                try:
                    dt = datetime.strptime(dkey, "%Y-%m-%d").date()
                    label = f"{medal} {dt.strftime('%d.%m.%Y')}"
                except Exception:
                    label = f"{medal} {dkey}"
                profit = d['profit']
                margin = (profit / d['revenue'] * 100) if d['revenue'] else 0
                tree.insert('', 'end', values=(
                    label,
                    d['checks'],
                    self.format_amount(d['revenue']) + " ₸",
                    self.format_amount(profit) + " ₸",
                    f"{margin:.0f}%",
                ), tags=('even',) if i % 2 else ())
            tot_margin = (data['profit'] / data['revenue'] * 100) if data['revenue'] else 0
            tree.insert('', 'end', values=(
                "Общая",
                data['checks'],
                self.format_amount(data['revenue']) + " ₸",
                self.format_amount(data['profit']) + " ₸",
                f"{tot_margin:.0f}%",
            ), tags=('total',))

        wd_counts = {}
        cur = d_from
        while cur <= d_from + timedelta(days=span - 1):
            wd_counts[cur.weekday()] = wd_counts.get(cur.weekday(), 0) + 1
            cur += timedelta(days=1)

        f_right = tk.LabelFrame(body, text=" 📊 Средняя выручка по дням недели ",
                                font=self.font_small_bold_tuple, bg=c['frame_bg'], fg=c['fg'])
        f_right.grid(row=0, column=1, sticky="nsew", padx=(4, 0))
        cv = tk.Canvas(f_right, height=190, bg=c['frame_bg'], highlightthickness=0)
        cv.pack(fill="x", padx=4, pady=4)
        self._biz_draw_weekday(cv, data['by_weekday'], wd_counts)
