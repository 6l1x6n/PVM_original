# -*- coding: utf-8 -*-
"""
PVM.core - Analytics Tab Mixin
=================================
Analytics dashboard with charts, predictions, top items/clients.
"""

import tkinter as tk
import threading
from tkinter import ttk

import settings
from ui_lang import get_text
from ui_dialogs import AutoScrollbar, ToolTip


class AnalyticsTabMixin:
    """Analytics tab methods for GreenLeafApp."""

    def create_analytics_tab_group(self):
        """Create a nested notebook for Analytics (Statistics and PV Bot)."""
        c = self.colors
        
        # Nested notebook
        analytics_notebook = ttk.Notebook(self.analytics_frame)
        analytics_notebook.pack(fill="both", expand=True, padx=5, pady=(0, 5))

        # Tariff badge placed over the notebook header area (hidden unless subscription ending)
        self._tariff_frame = tk.Frame(analytics_notebook, bg=c['bg'])
        self._tariff_frame.place(relx=1.0, x=-5, y=0, anchor="ne")
        self._tariff_price_lbl = tk.Label(self._tariff_frame, text="",
            font=("Segoe UI", 10, "bold"), bg=c['bg'], fg=c['success'])
        self._tariff_price_lbl.pack(side="left", padx=(6, 0))
        self._tariff_info_btn = tk.Label(self._tariff_frame, text="ⓘ",
            font=("Segoe UI", 9), bg=c['bg'], fg=c['fg_muted'], cursor="hand2")
        self._tariff_info_btn.pack(side="left", padx=(1, 4))
        self._analytics_tariff_tooltip = ToolTip(self._tariff_info_btn, get_text('pricing_tooltip_body', self.lang), title=get_text('pricing_tooltip_title', self.lang))
        self._tariff_date_lbl = tk.Label(self._tariff_frame, text="",
            font=("Segoe UI", 9), bg=c['bg'], fg=c['fg_muted'])
        self._tariff_date_lbl.pack(side="left", padx=(4, 0))
        self._fetch_analytics_pricing_data()

        # --- SUB-TAB 0: BUSINESS ANALYTICS ---
        if self.has_permission('bizanalytics_view') or self.has_permission('analytics_view'):
            biz_sub_frame = ttk.Frame(analytics_notebook)
            analytics_notebook.add(biz_sub_frame, text="  📈 Бизнес-аналитика  ")
            self.biz_analytics_frame = biz_sub_frame
            self.create_biz_analytics_tab(biz_sub_frame)

        # --- SUB-TAB 1: STATISTICS (gated by analytics_view) ---
        stats_frame = None
        if self.has_permission('analytics_view'):
            stats_frame = ttk.Frame(analytics_notebook)
            analytics_notebook.add(stats_frame, text=f"  📊 {get_text('statistics', self.lang)}  ")
            
            # We need to temporarily set self.analytics_frame to this sub-frame 
            # so that existing create_analytics_tab works correctly.
            old_analytics_frame = getattr(self, 'analytics_frame', None)
            self.analytics_frame = stats_frame
            self.create_analytics_tab()
            self.analytics_frame = old_analytics_frame # Restore
        
        # --- SUB-TAB 2: PV BOT (always available with PV subscription,
        # regardless of user rights — manual PV must work for every user) ---
        if self.subscription_level in [3, 4]:
            bot_sub_frame = ttk.Frame(analytics_notebook)
            analytics_notebook.add(bot_sub_frame, text=f"  🤖 {get_text('pv_bot_tab', self.lang)}  ")
            self.pvbot_inner_frame = bot_sub_frame
            
            # Temporarily set self.main_frame to this sub-frame
            old_main_frame = getattr(self, 'main_frame', None)
            self.main_frame = bot_sub_frame
            self.create_main_tab(show_status_header=False)
            self.main_frame = old_main_frame # Restore
        
        # --- SUB-TAB 3: AUTOREVIEW ---
        if self.has_permission('autoreview_view') and self.subscription_level in [3, 4]:
            autoreview_sub_frame = ttk.Frame(analytics_notebook)
            analytics_notebook.add(autoreview_sub_frame, text="  🔄 Автоскладирование  ")
            
            old_ar_frame = getattr(self, 'autoreview_frame', None)
            self.autoreview_frame = autoreview_sub_frame
            self.create_autoreview_tab()
            self.autoreview_frame = old_ar_frame
        
        # Save reference
        self.analytics_notebook = analytics_notebook
        
        def on_analytics_group_tab_changed(event):
            inner_tab_id = self.analytics_notebook.select()
            if not inner_tab_id: return
            inner_tab_widget = self.analytics_notebook.nametowidget(inner_tab_id)
            
            if hasattr(self, 'biz_analytics_frame') and inner_tab_widget == self.biz_analytics_frame:
                self._biz_rebuild()
            if hasattr(self, 'pvbot_inner_frame') and inner_tab_widget == self.pvbot_inner_frame:
                self._refresh_problem_center()
            if hasattr(self, 'autoreview_frame') and inner_tab_widget == self.autoreview_frame:
                self._ar_refresh_history()
            
        self.analytics_notebook.bind('<<NotebookTabChanged>>', on_analytics_group_tab_changed)

    def create_analytics_tab(self):
        """Create the analytics tab with charts and statistics from local logs."""
        c = self.colors
        
        # Configure frame for expansion
        self.analytics_frame.grid_rowconfigure(0, weight=1)
        self.analytics_frame.grid_columnconfigure(0, weight=1)
        
        # Main container with scrollbar
        analytics_canvas = tk.Canvas(self.analytics_frame, highlightthickness=0, bg=c['bg'])
        scrollbar = AutoScrollbar(self.analytics_frame, orient="vertical", command=analytics_canvas.yview)
        analytics_scrollable = tk.Frame(analytics_canvas, bg=c['bg'])
        
        # Create window
        analytics_canvas.create_window((0, 0), window=analytics_scrollable, anchor="nw")
        analytics_canvas.configure(yscrollcommand=scrollbar.set)
        
        def configure_scroll(event):
            analytics_canvas.configure(scrollregion=analytics_canvas.bbox("all"))
            analytics_canvas.itemconfig(analytics_canvas.find_withtag("all")[0], width=event.width)
        
        analytics_scrollable.bind('<Configure>', configure_scroll)
        analytics_canvas.bind('<Configure>', lambda e: analytics_canvas.itemconfig(
            analytics_canvas.find_withtag("all")[0], width=e.width))
        
        analytics_canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        
        # Store reference for refresh
        self.analytics_scrollable = analytics_scrollable
        self.analytics_canvas = analytics_canvas
        
        # Load and display analytics
        self.refresh_analytics()
    
    def refresh_analytics(self):
        """Refresh analytics display with current log data."""
        c = self.colors
        
        # Clear existing content
        for widget in self.analytics_scrollable.winfo_children():
            widget.destroy()
        
        # Get analytics data from local logs
        from ui import get_analytics_data
        analytics = get_analytics_data(settings.LOGS_DIR)
        
        if not analytics:
            # No data message
            no_data_frame = tk.Frame(self.analytics_scrollable, bg=c['bg'])
            no_data_frame.pack(fill="both", expand=True, pady=50)
            tk.Label(no_data_frame, text="📊", font=("Arial", 48), bg=c['bg'], fg=c['fg_muted']).pack()
            tk.Label(no_data_frame, text=get_text('no_data', self.lang), 
                    font=self.font_normal_tuple, bg=c['bg'], fg=c['fg_muted']).pack(pady=10)
            self.enable_scroll_area(self.analytics_canvas, self.analytics_scrollable)
            return
        
        # === REFRESH BUTTON ===
        refresh_frame = tk.Frame(self.analytics_scrollable, bg=c['bg'])
        refresh_frame.pack(fill="x", padx=self.padding_medium, pady=self.padding_small)
        self._btn(refresh_frame, text=f"🔄 {get_text('refresh_analytics', self.lang)}", command=self.refresh_analytics, style='accent', compact=True, cursor="hand2").pack(side="right")
        
        # === SUMMARY CARDS ===
        summary_frame = tk.Frame(self.analytics_scrollable, bg=c['bg'])
        summary_frame.pack(fill="x", padx=self.padding_medium, pady=self.padding_small)
        
        # Create 4 summary cards in a row
        cards_data = [
            (get_text('total_sessions', self.lang), str(analytics['total_sessions']), c['accent']),
            (get_text('total_orders_all', self.lang), str(analytics['total_orders']), c['success']),
            (get_text('success_rate', self.lang), f"{analytics['success_rate']:.1f}%", 
             c['success'] if analytics['success_rate'] > 90 else c['warning']),
            (get_text('avg_orders_per_day', self.lang), f"{analytics['avg_orders_per_day']:.0f}", c['accent']),
        ]
        
        for i, (label, value, color) in enumerate(cards_data):
            card = tk.Frame(summary_frame, bg=c['frame_bg'], padx=15, pady=10)
            card.pack(side="left", fill="x", expand=True, padx=5)
            tk.Label(card, text=value, font=("Arial", 24, "bold"), bg=c['frame_bg'], fg=color).pack()
            tk.Label(card, text=label, font=self.font_small_tuple, bg=c['frame_bg'], fg=c['fg_muted']).pack()
        
        # === PREDICTION CARD ===
        pred_frame = tk.LabelFrame(self.analytics_scrollable, text=f" 🔮 {get_text('prediction', self.lang)} ",
                                   padx=self.padding_medium, pady=self.padding_medium,
                                   font=self.font_bold_tuple, bg=c['frame_bg'], fg=c['fg'])
        pred_frame.pack(fill="x", padx=self.padding_medium, pady=self.padding_small)
        
        pred_inner = tk.Frame(pred_frame, bg=c['frame_bg'])
        pred_inner.pack(fill="x")
        
        tk.Label(pred_inner, text=f"{get_text('predicted_tomorrow', self.lang)}:",
                font=self.font_normal_tuple, bg=c['frame_bg'], fg=c['fg_secondary']).pack(side="left")
        tk.Label(pred_inner, text=f"  ~{analytics['prediction']} заказов  ",
                font=self.font_bold_tuple, bg=c['success_bg'], fg=c['success']).pack(side="left", padx=10)
        tk.Label(pred_inner, text=f"({get_text('based_on_history', self.lang)}, точность: {analytics['prediction_confidence']}%)",
                font=self.font_small_tuple, bg=c['frame_bg'], fg=c['fg_muted']).pack(side="left")
        
        # === ALERTS ===
        if analytics['alerts']:
            alerts_frame = tk.LabelFrame(self.analytics_scrollable, text=f" ⚠️ {get_text('alerts', self.lang)} ",
                                        padx=self.padding_medium, pady=self.padding_medium,
                                        font=self.font_bold_tuple, bg=c['frame_bg'], fg=c['fg'])
            alerts_frame.pack(fill="x", padx=self.padding_medium, pady=self.padding_small)
            
            for alert in analytics['alerts'][:5]:  # Max 5 alerts
                alert_color = c['error'] if alert['type'] == 'error' else c['warning']
                alert_bg = c['error_bg'] if alert['type'] == 'error' else c['warning_bg']
                
                # Parse and translate alert message
                msg = alert['message']
                for key in ['alert_low_orders', 'alert_item_unavailable', 'alert_days']:
                    msg = msg.replace(key, get_text(key, self.lang))
                
                tk.Label(alerts_frame, text=f"• {msg}", font=self.font_small_tuple,
                        bg=alert_bg, fg=alert_color, padx=8, pady=4, anchor="w").pack(fill="x", pady=2)
        
        # === CHART: Orders by Day (Last 7 days) ===
        chart_frame = tk.LabelFrame(self.analytics_scrollable, text=f" 📈 {get_text('orders_chart', self.lang)} ({get_text('last_7_days', self.lang)}) ",
                                    padx=self.padding_medium, pady=self.padding_medium,
                                    font=self.font_bold_tuple, bg=c['frame_bg'], fg=c['fg'])
        chart_frame.pack(fill="x", padx=self.padding_medium, pady=self.padding_small)
        
        # Simple bar chart using Canvas
        chart_canvas = tk.Canvas(chart_frame, height=150, bg=c['frame_bg'], highlightthickness=0)
        chart_canvas.pack(fill="x", padx=10, pady=10)
        
        # Draw bars after canvas is rendered
        def draw_chart():
            chart_canvas.delete("all")
            width = chart_canvas.winfo_width()
            if width < 50:
                width = 600
            height = 150
            padding = 40
            bar_width = (width - 2 * padding) / 7 - 10
            
            max_orders = max((d['orders'] for d in analytics['last_7_days']), default=1)
            if max_orders == 0:
                max_orders = 1
            
            for i, day_data in enumerate(analytics['last_7_days']):
                x = padding + i * (bar_width + 10)
                bar_height = (day_data['orders'] / max_orders) * (height - 60)
                y = height - 30 - bar_height
                
                # Bar
                color = c['success'] if day_data['orders'] > 0 else c['border']
                chart_canvas.create_rectangle(x, y, x + bar_width, height - 30, fill=color, outline="")
                
                # Value on top
                if day_data['orders'] > 0:
                    chart_canvas.create_text(x + bar_width/2, y - 8, text=str(day_data['orders']),
                                            font=self.font_small_tuple, fill=c['fg'])
                
                # Day label below
                chart_canvas.create_text(x + bar_width/2, height - 15, text=day_data['date'][:5],
                                        font=("Arial", 8), fill=c['fg_muted'])
        
        chart_canvas.after(100, draw_chart)
        chart_canvas.bind('<Configure>', lambda e: draw_chart())
        
        # === TOP ITEMS & FAILED ITEMS ===
        items_row = tk.Frame(self.analytics_scrollable, bg=c['bg'])
        items_row.pack(fill="x", padx=self.padding_medium, pady=self.padding_small)
        items_row.grid_columnconfigure(0, weight=1)
        items_row.grid_columnconfigure(1, weight=1)
        
        # Top Items
        top_frame = tk.LabelFrame(items_row, text=f" 🏆 {get_text('top_items', self.lang)} ",
                                  padx=self.padding_medium, pady=self.padding_medium,
                                  font=self.font_bold_tuple, bg=c['frame_bg'], fg=c['fg'])
        top_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 5))
        
        if analytics['top_items']:
            for i, (item, count) in enumerate(analytics['top_items']):
                medal = ["🥇", "🥈", "🥉", "4.", "5."][i] if i < 5 else f"{i+1}."
                tk.Label(top_frame, text=f"{medal} {item} ({count}x)",
                        font=self.font_small_tuple, bg=c['frame_bg'], fg=c['fg'], anchor="w").pack(fill="x")
        else:
            tk.Label(top_frame, text="-", font=self.font_small_tuple, 
                    bg=c['frame_bg'], fg=c['fg_muted']).pack()
        
        # Failed Items
        failed_frame = tk.LabelFrame(items_row, text=f" ❌ {get_text('failed_items_chart', self.lang)} ",
                                     padx=self.padding_medium, pady=self.padding_medium,
                                     font=self.font_bold_tuple, bg=c['frame_bg'], fg=c['fg'])
        failed_frame.grid(row=0, column=1, sticky="nsew", padx=(5, 0))
        
        if analytics['failed_items']:
            for item, data in analytics['failed_items']:
                tk.Label(failed_frame, text=f"⚠️ {item} ({data['total']}x)",
                        font=self.font_small_tuple, bg=c['frame_bg'], fg=c['warning'], anchor="w").pack(fill="x")
        else:
            tk.Label(failed_frame, text=get_text('no_alerts', self.lang),
                    font=self.font_small_tuple, bg=c['frame_bg'], fg=c['fg_muted']).pack()
        
        # === TOP CLIENTS & INSUFFICIENT FUNDS ===
        clients_row = tk.Frame(self.analytics_scrollable, bg=c['bg'])
        clients_row.pack(fill="x", padx=self.padding_medium, pady=self.padding_small)
        clients_row.grid_columnconfigure(0, weight=1)
        clients_row.grid_columnconfigure(1, weight=1)
        
        # Top Clients
        top_clients_frame = tk.LabelFrame(clients_row, text=f" 👥 {get_text('top_clients', self.lang)} ",
                                          padx=self.padding_medium, pady=self.padding_medium,
                                          font=self.font_bold_tuple, bg=c['frame_bg'], fg=c['fg'])
        top_clients_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 5))
        
        if analytics.get('top_clients'):
            for i, (user_id, stats) in enumerate(analytics['top_clients']):
                medal = ["🥇", "🥈", "🥉", "4.", "5."][i] if i < 5 else f"{i+1}."
                purchases_text = get_text('client_purchases', self.lang)
                items_text = get_text('client_items', self.lang)
                spent_text = get_text('client_spent', self.lang)
                tk.Label(top_clients_frame, 
                        text=f"{medal} {user_id}",
                        font=self.font_small_tuple, bg=c['frame_bg'], fg=c['fg'], anchor="w").pack(fill="x")
                tk.Label(top_clients_frame, 
                        text=f"     {stats['purchases']} {purchases_text}, {stats['items']} {items_text}, {self.format_amount(stats['spent'])} тг",
                        font=("Arial", 9), bg=c['frame_bg'], fg=c['fg_muted'], anchor="w").pack(fill="x")
        else:
            tk.Label(top_clients_frame, text="-", font=self.font_small_tuple, 
                    bg=c['frame_bg'], fg=c['fg_muted']).pack()
        
        # Insufficient Funds Clients
        insuff_frame = tk.LabelFrame(clients_row, text=f" 💳 {get_text('insufficient_funds_clients', self.lang)} ",
                                     padx=self.padding_medium, pady=self.padding_medium,
                                     font=self.font_bold_tuple, bg=c['frame_bg'], fg=c['fg'])
        insuff_frame.grid(row=0, column=1, sticky="nsew", padx=(5, 0))
        
        if analytics.get('insufficient_funds'):
            for order_id, data in list(analytics['insufficient_funds'].items())[:5]:
                last_seen = data.get('last_seen', 'N/A')
                tk.Label(insuff_frame, text=f"⚠️ Order {order_id}",
                        font=self.font_small_tuple, bg=c['frame_bg'], fg=c['warning'], anchor="w").pack(fill="x")
                tk.Label(insuff_frame, text=f"     Last seen: {last_seen}",
                        font=("Arial", 9), bg=c['frame_bg'], fg=c['fg_muted'], anchor="w").pack(fill="x")
        else:
            tk.Label(insuff_frame, text=get_text('no_issues', self.lang),
                    font=self.font_small_tuple, bg=c['frame_bg'], fg=c['success']).pack()

        # Universal wheel + drag-pan (children were just rebuilt)
        try:
            self.enable_scroll_area(self.analytics_canvas, self.analytics_scrollable)
        except Exception:
            pass

    def _fetch_analytics_pricing_data(self):
        if not self._should_show_pricing():
            tf = getattr(self, '_tariff_frame', None)
            if tf:
                try: tf.place_forget()
                except: pass
            return
        tf = getattr(self, '_tariff_frame', None)
        if tf:
            try: tf.place(relx=1.0, x=-5, y=0, anchor="ne")
            except: pass
        self._analytics_pricing_data = None
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
                    self._analytics_pricing_data = result.data[0]
            except Exception as e:
                print(f"Error fetching analytics pricing: {e}")
        threading.Thread(target=_fetch, daemon=True).start()
        def _poll():
            if getattr(self, '_analytics_pricing_data', None):
                data = self._analytics_pricing_data
                self._analytics_pricing_data = None
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

    
