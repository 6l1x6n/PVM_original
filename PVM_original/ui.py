MODULE_VERSION = "3.9.51"
"""
PVM.core v3.11.83 - User Interface
=================================
Main UI module. GreenLeafApp is composed from tab mixins.
"""

import os
import sys

# Ensure current directory is in sys.path for local imports
# (Needed when running via exec from another script)
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)
import re
import time
import json
import queue
import threading
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple, Union

# Common libs
import requests  # type: ignore
from PIL import Image  # type: ignore

# Tray icon support (Windows only)
TRAY_AVAILABLE = False
if sys.platform == 'win32':
    try:
        import pystray
        from PIL import ImageDraw
        TRAY_AVAILABLE = True
    except ImportError:
        pass

# Import refactored modules
import settings
from settings import load_settings
import market
from market import GoodsManager, PartnersManager, ReceiptsManager, PurchasesManager, WriteoffManager, QuickItemsManager
from pvm_core import load_progress, clear_progress, IntegrationBot
from db_sqlite import DatabaseManager, UsersManagerSQL
from db import (
    get_device_key, get_activation_status, check_license_status_only,
    enable_autorun, disable_autorun, is_autorun_enabled
)

# Localization (shared with all ui_*.py mixins)
from ui_lang import get_text

# Dialog classes
from ui_dialogs import (
    AutoScrollbar,
    DeviceTypePickerDialog,
    WaitingScreen,
    AdminSetupWizard,
    UserLoginScreen,
)

# Tab mixins
from ui_sales import SalesTabMixin
from ui_pos import POSTabMixin
from ui_arrival import ArrivalTabMixin
from ui_partners import PartnersTabMixin
from ui_main_tab import MainTabMixin
from ui_analytics import AnalyticsTabMixin
from ui_bizanalytics import BizAnalyticsMixin
from ui_autoreview import AutoreviewMixin
from ui_settings import SettingsTabMixin
from ui_bot import BotAutomationMixin

# =============================================================================
# LOCAL LOG PARSER (For Analytics)
# =============================================================================
# =============================================================================
# LOCAL LOG PARSER (For Analytics - no Supabase connection!)
# =============================================================================
def parse_logs_dir(logs_dir: str) -> Tuple[List[Dict[str, Any]], Dict[str, Dict[str, Any]], Dict[str, Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    """Parse all local .dat log files and extract session data."""
    sessions: List[Dict[str, Any]] = []
    failed_items_history: Dict[str, Dict[str, Any]] = {}  # Track failed items across all sessions
    client_stats: Dict[str, Dict[str, Any]] = {}  # Track client spending across sessions
    insufficient_funds_history: Dict[str, Dict[str, Any]] = {}  # Track insufficient funds orders
    
    if not os.path.exists(logs_dir):
        return sessions, failed_items_history, client_stats, insufficient_funds_history
    
    for filename in os.listdir(logs_dir):
        if not filename.endswith('.dat'):
            continue
        
        filepath = os.path.join(logs_dir, filename)
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                content = f.read()
            
            session = parse_single_log(content, filename)
            if session:
                sessions.append(session)
                
                # Collect failed items
                for item, count in session.get('failed_items', {}).items():
                    if item not in failed_items_history:
                        failed_items_history[item] = {'total': 0, 'dates': []}
                    failed_items_history[item]['total'] += count
                    failed_items_history[item]['dates'].append(session.get('date', ''))
                
                # Collect client stats from top clients
                for client in session.get('top_clients', []):
                    user_id = client.get('user_id')
                    if user_id:
                        if user_id not in client_stats:
                            client_stats[user_id] = {'purchases': 0, 'items': 0, 'spent': 0, 'sessions': 0}
                        client_stats[user_id]['purchases'] += client.get('purchases', 0)
                        client_stats[user_id]['items'] += client.get('items', 0)
                        client_stats[user_id]['spent'] += client.get('spent', 0)
                        client_stats[user_id]['sessions'] += 1
                
                # Collect insufficient funds orders
                for order_id in session.get('insufficient_funds_orders', []):
                    if order_id not in insufficient_funds_history:
                        insufficient_funds_history[order_id] = {'dates': [], 'last_seen': ''}
                    insufficient_funds_history[order_id]['dates'].append(session.get('date', ''))
                    insufficient_funds_history[order_id]['last_seen'] = session.get('date', '')
        except:
            continue
    
    # Sort by date descending
    sessions.sort(key=lambda x: x.get('date', ''), reverse=True)
    return sessions, failed_items_history, client_stats, insufficient_funds_history

def parse_single_log(content: str, filename: str) -> Dict[str, Any]:
    """Parse a single log file content."""
    session: Dict[str, Any] = {'filename': filename, 'failed_items': {}, 'top_clients': [], 'insufficient_funds_orders': []}
    
    lines = content.split('\n')
    in_top_clients = False
    in_insufficient_funds = False
    
    for line in lines:
        line = line.strip()
        
        # Detect section changes
        if 'TOP CLIENTS' in line:
            in_top_clients = True
            in_insufficient_funds = False
            continue
        elif 'INSUFFICIENT FUNDS' in line:
            in_top_clients = False
            in_insufficient_funds = True
            continue
        elif line.startswith('══') or line.startswith('──'):
            in_top_clients = False
            in_insufficient_funds = False
            continue
        
        # Parse top clients (format: "1. kz12345678: 5 purchases, 20 items, 50000 тг")
        if in_top_clients and line and line[0].isdigit():
            match = re.match(r'\d+\.\s*(\w+):\s*(\d+)\s*purchases?,\s*(\d+)\s*items?,\s*([\d.]+)', line)
            if match:
                session['top_clients'].append({
                    'user_id': match.group(1),
                    'purchases': int(match.group(2)),
                    'items': int(match.group(3)),
                    'spent': float(match.group(4))
                })
        
        # Parse insufficient funds orders
        if in_insufficient_funds and line.startswith('Order:'):
            order_id = line.split(':')[1].strip()
            session['insufficient_funds_orders'].append(order_id)
        
        # Extract date
        if line.startswith('Date:'):
            session['date'] = line.split(':', 1)[1].strip()
        
        # Extract times
        elif line.startswith('Start:'):
            session['start_time'] = line.split(':', 1)[1].strip()
        elif line.startswith('End:'):
            session['end_time'] = line.split(':', 1)[1].strip()
        
        # Extract duration
        elif line.startswith('Duration:'):
            session['duration'] = line.split(':', 1)[1].strip()
        
        # Extract orders
        elif line.startswith('Total Orders:'):
            try:
                session['total_orders'] = int(line.split(':')[1].strip())
            except:
                session['total_orders'] = 0
        elif line.startswith('✅ Successful:'):
            try:
                session['successful'] = int(line.split(':')[1].strip())
            except:
                session['successful'] = 0
        elif line.startswith('❌ Failed:'):
            try:
                session['failed'] = int(line.split(':')[1].strip())
            except:
                session['failed'] = 0
        
        # Extract sales
        elif line.startswith('💰 Total Sales:'):
            try:
                session['total_sales'] = float(line.split(':')[1].strip())
            except:
                session['total_sales'] = 0
        
        # Extract items
        elif line.startswith('Total Items:'):
            try:
                session['total_items'] = int(line.split(':')[1].strip())
            except:
                session['total_items'] = 0
        elif line.startswith('Unique Products:'):
            try:
                session['unique_items'] = int(line.split(':')[1].strip())
            except:
                session['unique_items'] = 0
        elif line.startswith('Most Purchased:'):
            parts = line.split(':')[1].strip()
            if '(' in parts:
                item = parts.split('(')[0].strip()
                session['top_item'] = item
        
        # Extract failed items
        elif ': ' in line and 'order(s) affected' in line:
            parts = line.split(':')
            if len(parts) >= 2:
                item_code = parts[0].strip()
                try:
                    count = int(parts[1].split()[0])
                    session['failed_items'][item_code] = count
                except:
                    pass
        
        # Extract unique users
        elif line.startswith('Unique Users:'):
            try:
                session['unique_users'] = int(line.split(':')[1].strip())
            except:
                session['unique_users'] = 0
    
    # Only return if we have valid data
    if 'date' in session and 'total_orders' in session:
        return session
    return None

def get_analytics_data(logs_dir):
    """Get comprehensive analytics from local logs."""
    sessions, failed_items_history, client_stats, insufficient_funds_history = parse_logs_dir(logs_dir)
    
    if not sessions:
        return None
    
    # Calculate summary stats
    total_sessions = len(sessions)
    total_orders = sum(s.get('total_orders', 0) for s in sessions)
    total_successful = sum(s.get('successful', 0) for s in sessions)
    total_failed = sum(s.get('failed', 0) for s in sessions)
    total_sales = sum(s.get('total_sales', 0) for s in sessions)
    
    success_rate = (total_successful / max(total_orders, 1)) * 100
    
    # Get unique dates
    unique_dates = set(s.get('date', '') for s in sessions if s.get('date'))
    avg_orders_per_day = total_orders / max(len(unique_dates), 1)
    
    # Last 7 days data
    today = datetime.now().date()
    today_str = today.strftime('%d.%m.%Y')
    last_7_days = []
    for i in range(7):
        day = today - timedelta(days=i)
        day_str = day.strftime('%d.%m.%Y')
        day_orders = sum(s.get('successful', 0) for s in sessions if s.get('date') == day_str)
        last_7_days.append({'date': day_str, 'orders': day_orders, 'weekday': day.strftime('%a')})
    last_7_days.reverse()
    
    # Top items (from recent sessions)
    item_counts: Dict[str, int] = {}
    recent_sessions: Any = list(sessions)[:30] # type: ignore
    for s in recent_sessions:
        top_item = s.get('top_item')
        if top_item and top_item != 'N/A':
            item_counts[top_item] = item_counts.get(top_item, 0) + 1
    top_items = sorted(item_counts.items(), key=lambda x: x[1], reverse=True)[:5] # type: ignore
    
    # Failed items (persistent problems)
    failed_items_sorted = sorted(failed_items_history.items(), 
                                  key=lambda x: x[1]['total'], reverse=True)[:5] # type: ignore
    
    # TOP CLIENTS - sorted by total spent
    top_clients = sorted(client_stats.items(), key=lambda x: x[1]['spent'], reverse=True)[:5] # type: ignore
    
    # INSUFFICIENT FUNDS - filter to only recent (last 7 days)
    recent_insufficient = {}
    for order_id, data in insufficient_funds_history.items():
        last_seen = data.get('last_seen', '')
        if last_seen:
            try:
                last_date = datetime.strptime(last_seen, '%d.%m.%Y').date()
                if (today - last_date).days <= 7:
                    recent_insufficient[order_id] = data
            except:
                pass
    
    # Prediction (simple average of same weekday)
    tomorrow = today + timedelta(days=1)
    tomorrow_weekday = tomorrow.weekday()
    same_weekday_orders = []
    for s in sessions:
        try:
            s_date = datetime.strptime(s.get('date', ''), '%d.%m.%Y').date()
            if s_date.weekday() == tomorrow_weekday:
                same_weekday_orders.append(s.get('successful', 0))
        except:
            continue
    
    if same_weekday_orders:
        predicted = int(sum(same_weekday_orders) / len(same_weekday_orders))
        prediction_confidence = min(len(same_weekday_orders) * 20, 100)  # More data = higher confidence
    else:
        predicted = int(avg_orders_per_day)
        prediction_confidence = 30
    
    # Alerts
    alerts = []
    
    # Alert: Low orders today
    today_orders = [s for s in recent_sessions if s.get('date') == today_str]
    if len(today_orders) < avg_orders_per_day * 0.5 and avg_orders_per_day > 5:
        alerts.append({
            'type': 'warning',
            'message': f"Низкое количество заказов сегодня ({len(today_orders)} при среднем {int(avg_orders_per_day)})"
        })
        
    return {
        'total_sessions': total_sessions,
        'total_orders': total_orders,
        'total_sales': total_sales,
        'success_rate': success_rate,
        'avg_orders_per_day': avg_orders_per_day,
        'top_items': top_items,
        'failed_items': failed_items_sorted,
        'top_clients': top_clients,
        'recent_insufficient': recent_insufficient,
        'last_7_days': last_7_days,
        'prediction': predicted,
        'prediction_confidence': prediction_confidence,
        'alerts': alerts
    }




# =============================================================================
# MAIN APPLICATION CLASS (composed from tab mixins)
# =============================================================================
class GreenLeafApp(
    SalesTabMixin,
    POSTabMixin,
    ArrivalTabMixin,
    PartnersTabMixin,
    MainTabMixin,
    AnalyticsTabMixin,
    BizAnalyticsMixin,
    AutoreviewMixin,
    SettingsTabMixin,
    BotAutomationMixin,
):
    """GreenLeaf POS Application - composed from tab mixins."""

    def __init__(self, master, login, password, current_user=None, subscription_level=4):
        _cl("GreenLeafApp __init__ entered")
        self.master = master
        self.subscription_level = subscription_level
        # --- UI COMPONENTS (initialized in create_widgets) ---
        self.notebook: Optional[ttk.Notebook] = None
        self.pos_frame: Optional[ttk.Frame] = None
        self.sales_frame: Optional[ttk.Frame] = None
        self.arrival_frame: Optional[ttk.Frame] = None
        self.partners_frame: Optional[ttk.Frame] = None
        self.main_frame: Optional[ttk.Frame] = None
        self.analytics_frame: Optional[ttk.Frame] = None
        self.settings_frame: Optional[ttk.Frame] = None
        self.partners_search_var: Optional[tk.StringVar] = None
        self.partners_search: Optional[ttk.Entry] = None
        self.analytics_scrollable: Optional[tk.Frame] = None

        # Ensure log_text attribute always exists, even if PV bot tab is hidden
        self.log_text = None
        self.settings = load_settings()
        _cl("GreenLeafApp settings loaded")
        self.lang = 'ru'

        # First-run device_type picker (Phase 3 first-run).
        # If device_type is not yet set, ask the operator once. The choice is
        # persisted to sync_settings.json and gates UI features + sync topology.
        # Wrap defensively so any dialog subsystem failure can never block app boot.
        if 'device_type' not in self.settings:
            try:
                picker = DeviceTypePickerDialog(master, lang=(self.lang or 'ru'))
                choice = picker.show()
                if choice in ('cashier', 'warehouse'):
                    settings.set_device_type(choice)
                    self.settings = load_settings()  # refresh cache
            except Exception as _e:
                print(f"⚠ DeviceTypePickerDialog skipped: {_e}")
        
        # Current user & permissions
        self.current_user = current_user or {}
        self.current_username = self.current_user.get('display_name', 'admin')
        self.current_role = self.current_user.get('role', 'admin')
        self.permissions = self.current_user.get('permissions', UsersManagerSQL.ROLE_TEMPLATES.get('admin', {}))
        
        # Switch-user flag: when set, code.py shows the login screen again
        self.switch_user_requested = False
        # Shutdown flag: stops periodic Tk timers cleanly before window destroy
        self._shutting_down = False
        # Tracked after-ids of periodic timers (cancelled before destroy)
        self._pending_afters = []
        # C3: central shutdown coordinator — one stop event per worker
        # subsystem, a worker→Tk event queue and the worker registry.
        self._stop_events = {
            'scheduler': threading.Event(),
            'autoreview': threading.Event(),
            'live_bot': threading.Event(),
            'integration': threading.Event(),
            'sync': threading.Event(),
        }
        self._ui_queue = queue.Queue()
        self._workers = []
        
        # Notification system state — initialized here so it exists regardless
        # of which tabs/permissions the current user has (the main tab that
        # used to initialize it may not be created for some roles).
        self.notifications = []
        self._notif_label_refs = {}
        self._notif_containers = []
        self._notif_after_id = None
        
        # Setup theme colors (light mode only)
        self.setup_theme_colors()
        
        # --- UI Scale & Font Attributes (initialized by calculate_sizes) ---
        self.interface_scale: float = 1.0
        self.padding_small: int = 4
        self.padding_medium: int = 8
        self.padding_large: int = 15
        self.button_height: int = 2
        self.entry_width: int = 30
        
        # Windows-specific font optimization for 'Premium' look
        self.font_family = "Segoe UI" if sys.platform == 'win32' else "Arial"
        
        self.font_small: int = 12
        self.font_normal: int = 14
        self.font_large: int = 18
        self.font_title: int = 24
        
        self.font_small_tuple: Tuple = (self.font_family, 12)
        self.font_small_bold_tuple: Tuple = (self.font_family, 12, "bold")
        self.font_normal_tuple: Tuple = (self.font_family, 14)
        self.font_bold_tuple: Tuple = (self.font_family, 14, "bold")
        self.font_large_tuple: Tuple = (self.font_family, 18, "bold")
        self.font_title_tuple: Tuple = (self.font_family, 24, "bold")
        
        self.btn_padx: int = 12
        self.btn_pady: int = 6
        # ------------------------------------------------------------------
        
        # Title with username
        title = get_text('app_title', self.lang)
        title += f"  —  {self.current_username}"
        if self.current_role != 'admin':
            role_label = settings.ROLE_LABELS.get(self.current_role, self.current_role)
            title += f" ({role_label})"
        # Window title mode badge (Phase 3.3): distinct from the "Склад" tab name
        # so operators glancing at the taskbar can immediately tell whether this
        # physical PC is running as the cashier (server) or the warehouse (client).
        try:
            _device_type = settings.get_device_type()
            if _device_type == 'warehouse':
                title += "  —  Режим: Склад"
            else:
                title += "  —  Режим: Касса"
        except Exception:
            pass
        master.title(title)
        master.state('zoomed')
        master.minsize(1100, 700)  # Minimum window size for POS
        
        # Configure grid weights for responsive layout
        master.grid_rowconfigure(0, weight=1)
        master.grid_columnconfigure(0, weight=1)

        # Get device key and activation status
        self.device_key = get_device_key()
        _cl("GreenLeafApp before get_activation_status")
        self.status, self.activation_start, self.activation_end = get_activation_status(self.device_key)
        _cl("GreenLeafApp after get_activation_status")

        # Store credentials
        self.stored_login = login
        self.stored_password = password
        
        # --- UI Component Placeholders (initialized by create_widgets) ---
        self.notebook: Optional[ttk.Notebook] = None
        self.pos_frame: Optional[ttk.Frame] = None
        self.sales_frame: Optional[ttk.Frame] = None
        self.goods_frame: Optional[ttk.Frame] = None
        self.partners_frame: Optional[ttk.Frame] = None
        self.arrival_frame: Optional[ttk.Frame] = None
        self.invoice_frame: Optional[ttk.Frame] = None
        self.purchases_frame: Optional[ttk.Frame] = None
        self.writeoffs_frame: Optional[ttk.Frame] = None
        self.cancelled_frame: Optional[ttk.Frame] = None

        
        # POS specific
        self.cart_tree: Optional[ttk.Treeview] = None
        self.pos_search_entry: Optional[ttk.Entry] = None
        self.pos_total_label: Optional[tk.Label] = None
        self.pos_total_pv_label: Optional[tk.Label] = None
        self.pos_subtotal: Optional[tk.Label] = None
        self.pos_summary_label: Optional[tk.Label] = None
        self.pos_received_entry: Optional[tk.Entry] = None
        self.pos_change: Optional[tk.Label] = None
        self.pos_checkout_btn: Optional[tk.Button] = None
        self.pos_cancel_btn: Optional[tk.Button] = None
        self.quick_grid: Optional[tk.Frame] = None
        self.quick_buttons: List[tk.Button] = []
        self.pos_cart_tab_frame: Optional[tk.Frame] = None
        self.pos_cart_tab_inner: Optional[tk.Frame] = None
        
        # Additional common UI components
        self.main_container: Optional[tk.Frame] = None
        self.status_bar: Optional[Union[tk.Frame, tk.Label]] = None
        self.arrival_search_entry: Optional[ttk.Entry] = None
        self.partners_search: Optional[ttk.Entry] = None
        self.pos_partner_var: Optional[tk.StringVar] = None
        self.main_frame: Optional[ttk.Frame] = None
        self.analytics_frame: Optional[ttk.Frame] = None
        self.autoreview_frame: Optional[ttk.Frame] = None
        self.arrival_notebook: Optional[ttk.Notebook] = None
        # -----------------------------------------------------------------
        # -----------------------------------------------------------------
        
        # Generate unique prefix for this register (from device key)
        dev_key = settings.get_or_create_device_key()
        self.device_prefix = dev_key[:6].upper() if dev_key else ''
        
        # === POS SYSTEM INITIALIZATION (SQLite) ===
        _base_dir = settings.BASE_DIR
        db_path = os.path.join(_base_dir, 'cache', 'pvmcore.db')
        self._db_manager = DatabaseManager(db_path)
        self.goods_manager = GoodsManager(self._db_manager)
        self.partners_manager = PartnersManager(self._db_manager)
        self.receipts_manager = ReceiptsManager(self._db_manager, device_prefix=self.device_prefix)
        self.purchases_manager = PurchasesManager(self._db_manager, device_prefix=self.device_prefix)
        self.writeoffs_manager = WriteoffManager(self._db_manager, device_prefix=self.device_prefix)
        self.quick_items_manager = QuickItemsManager(self._db_manager)
        self.users_manager = UsersManagerSQL(self._db_manager)
        self.audit_manager = market.InventoryAuditManager(self._db_manager, device_prefix=self.device_prefix)
        # C4: atomic business operations (sale/purchase/writeoff/refund)
        self.inventory_ops = market.InventoryOpsManager(self._db_manager, device_prefix=self.device_prefix)
        self.pos_carts = [[]]  # Multi-cart: list of carts, each [{code, name, price, quantity, pv, discount, sum}]
        self.pos_cart_idx = 0
        self.pos_cart_partners = [None]
        self.current_invoice_items = []  # Items for current purchase invoice
        _cl("GreenLeafApp db init done")

        # Sales history tracking
        self.sales_tree = None
        self.sales_details_text = None

        # Configuration variables
        self.report_file_path = tk.StringVar(value="")
        self.login = tk.StringVar(value=login)
        self.password = tk.StringVar(value=password)
        self.url = tk.StringVar(value="https://greenleaf-global.com/office/login?goto=/dashboard")  # Internal use only

        # Settings variables
        self.language_var = tk.StringVar(value='ru')
        self.interface_size_var = tk.IntVar(value=self.settings.get('interface_size', 87))
        self.font_size_var = tk.IntVar(value=self.settings.get('font_size', 87))
        self.button_size_var = tk.IntVar(value=self.settings.get('button_size', 87))
        self.scale_preset_var = tk.StringVar(value=self.settings.get('scale_preset', 'Default'))
        self.theme_var = tk.StringVar(value=self.settings.get('theme', 'forest'))
        self.scheduler_enabled_var = tk.BooleanVar(value=self.settings.get('scheduler_enabled', False))
        self.scheduled_time_var = tk.StringVar(value=self.settings.get('scheduled_time', '09:00'))
        self.watch_directory_var = tk.StringVar(value=self.settings.get('watch_directory', ''))
        self.auto_download_receipts_var = tk.BooleanVar(value=self.settings.get('auto_download_receipts', False))
        self.partner_autoblock_var = tk.StringVar(value=self.settings.get('partner_autoblock', 'all'))
        
        # Add traces for dynamic cache update (replaces restart requirement)
        self.scheduled_time_var.trace_add("write", lambda *args: self.update_scheduler_cache())
        self.watch_directory_var.trace_add("write", lambda *args: self.update_scheduler_cache())
        self.auto_download_receipts_var.trace_add("write", lambda *args: self.update_scheduler_cache())
        self.partner_autoblock_var.trace_add("write", lambda *args: self.update_scheduler_cache())
        self.scheduler_enabled_var.trace_add("write", lambda *args: self.toggle_scheduler_dynamic())
        
        self.shutdown_after_var = tk.BooleanVar(value=self.settings.get('shutdown_after_done', False))
        self.autorun_var = tk.BooleanVar(value=is_autorun_enabled())
        self.slow_network_var = tk.BooleanVar(value=self.settings.get('slow_network_mode', False))
        self.max_empty_pages_var = tk.IntVar(value=self.settings.get('max_empty_pages', 3))
        
        # Live PV Bot v2 variables
        self.live_bot_v2_var = tk.BooleanVar(value=self.settings.get('live_bot_v2', False))
        self.live_bot_delay_var = tk.IntVar(value=self.settings.get('live_bot_delay', 30))
        # Logs always enabled - hardcoded to settings.LOGS_DIR from front (cache folder)
        self.history_directory_var = tk.StringVar(value=settings.LOGS_DIR)
        # Headless mode always ON (hardcoded)
        self.headless_var = tk.BooleanVar(value=True)
        # auto_retry_var removed - built-in retry handles this
        
        # Appearance context settings (Toasts, etc)
        app_set = settings.get_appearance_settings()
        self.toast_size_var = tk.DoubleVar(value=app_set.get('toast_size', 1.0))
        self.toast_alpha_var = tk.DoubleVar(value=app_set.get('toast_alpha', 0.95))
        self.toast_position_var = tk.StringVar(value=app_set.get('toast_position', 'top_center'))
        self.toast_show_success_var = tk.BooleanVar(value=app_set.get('show_success_toast', True))
        self.toast_show_error_var = tk.BooleanVar(value=app_set.get('show_error_toast', True))
        self.toast_show_warning_var = tk.BooleanVar(value=app_set.get('show_warning_toast', True))
        self.toast_show_info_var = tk.BooleanVar(value=app_set.get('show_info_toast', True))
        self.toast_show_print_success_var = tk.BooleanVar(value=app_set.get('show_print_success_toast', True))
        self.toast_show_print_error_var = tk.BooleanVar(value=app_set.get('show_print_error_toast', True))
        self.toast_show_sync_var = tk.BooleanVar(value=app_set.get('show_sync_toast', True))
        self.toast_show_bot_var = tk.BooleanVar(value=app_set.get('show_bot_toast', True))
        self.toast_show_inventory_var = tk.BooleanVar(value=app_set.get('show_inventory_toast', True))
        self.toast_show_sales_var = tk.BooleanVar(value=app_set.get('show_sales_toast', True))
        self.skip_low_stock_warning_var = tk.BooleanVar(value=app_set.get('skip_low_stock_warning', False))
        
        # Integration variables
        int_set = settings.get_integration_settings()
        self.email_enabled_var = tk.BooleanVar(value=int_set.get('email_enabled', False))
        self.smtp_server_var = tk.StringVar(value=int_set.get('smtp_server', 'smtp.gmail.com'))
        self.smtp_port_var = tk.IntVar(value=int_set.get('smtp_port', 465))
        self.smtp_user_var = tk.StringVar(value=int_set.get('smtp_user', ''))
        self.smtp_pwd_var = tk.StringVar(value=int_set.get('smtp_password', ''))
        self.email_recipient_var = tk.StringVar(value=int_set.get('email_recipient', ''))
        
        self.tg_enabled_var = tk.BooleanVar(value=int_set.get('telegram_enabled', False))
        self.tg_token_var = tk.StringVar(value=int_set.get('tg_bot_token', ''))
        self.tg_chat_id_var = tk.StringVar(value=int_set.get('tg_chat_id', ''))
        
        self.send_report_on_exit_var = tk.BooleanVar(value=int_set.get('send_report_on_exit', True))
        self.require_otp_var = tk.BooleanVar(value=int_set.get('require_otp_on_failure', True))
        
        self.toast_show_success_var.trace_add('write', self._track_changes)
        self.toast_show_error_var.trace_add('write', self._track_changes)
        self.toast_show_warning_var.trace_add('write', self._track_changes)
        self.toast_show_info_var.trace_add('write', self._track_changes)
        self.toast_show_print_success_var.trace_add('write', self._track_changes)
        self.toast_show_print_error_var.trace_add('write', self._track_changes)
        self.toast_show_sync_var.trace_add('write', self._track_changes)
        self.toast_show_bot_var.trace_add('write', self._track_changes)
        self.toast_show_inventory_var.trace_add('write', self._track_changes)
        self.toast_show_sales_var.trace_add('write', self._track_changes)
        self.skip_low_stock_warning_var.trace_add('write', self._track_changes)
        self.toast_size_var.trace_add('write', self._track_changes)
        self.toast_alpha_var.trace_add('write', self._track_changes)
        self.toast_position_var.trace_add('write', self._track_changes)
        
        # New: Remote version info for "Main" settings page
        self.remote_version_info = None
        self.update_check_done = False
        
        # Live Bot status for POS feedback
        self.live_bot_status_var = tk.StringVar(value="")
        
        # Cache for scheduler (thread-safe access)
        self._scheduler_config_generation = 0
        self.cached_scheduled_time = self.scheduled_time_var.get()
        self.cached_watch_directory = self.watch_directory_var.get()
        self.cached_auto_download_receipts = self.auto_download_receipts_var.get()
        self.cached_shutdown_after = self.shutdown_after_var.get()
        self.cached_partner_autoblock = self.partner_autoblock_var.get()
        
        # Sync variables
        sync_meta = settings.get_sync_settings()
        
        # Global binding for barcode scanner focus
        self.master.bind('<Key>', self.on_global_keypress)
        self.sync_name_var = tk.StringVar(value=sync_meta.get('sync_name') or f"Касса №{self.device_key[:4]}")
        self.sync_name_var.trace_add('write', self._on_device_name_changed)
        
        # Timeout multiplier based on network mode
        self.PERM_CATEGORIES = [
            ("Касса", [
                ('pos_view', '', 'Доступ')
            ]),
            ("Склад", [
                ('arrival_view',   '', 'Просмотр'),
                ('arrival_create', '', 'Создание'),
                ('arrival_edit',   '', 'Редактирование'),
                ('arrival_delete', '', 'Удаление'),
                ('purchase_cancel', '', 'Отмена поставки'),
                ('goods_code_edit', '', 'Код/штрихкод (ред.)')
            ]),
            ("История", [
                ('cancellations_view', '', 'Отмены (касса)')
            ]),
            ("Списания", [
                ('writeoff_view',   '', 'Просмотр'),
                ('writeoff_create', '', 'Создание')
            ]),
            ("Ревизия", [
                ('inventory_view',    '', 'Просмотр'),
                ('inventory_conduct', '', 'Проведение')
            ]),
            ("Продажи", [
                ('sales_view',           '', 'Просмотр'),
                ('sales_refund_partial', '', 'Частичный возврат'),
                ('sales_refund_full',    '', 'Полный возврат')
            ]),
            ("Партнеры", [
                ('partner_view',    '', 'Просмотр'),
                ('partner_create',  '', 'Создание'),
                ('partner_edit',    '', 'Редактирование'),
                ('partner_delete',  '', 'Удаление'),
                ('partner_block',   '', 'Блокировка'),
                ('partner_history', '', 'История изменений')
            ]),
            ("Аналитика", [
                ('analytics_view',    '', 'Просмотр'),
                ('bizanalytics_view', '', 'Бизнес-аналитика: Просмотр'),
                ('pvbot_use',         '', 'PV Бот: Запуск'),
                ('autoreview_view',   '', 'Автоскладирование: Просмотр'),
                ('autoreview_start',  '', 'Автоскладирование: Запуск')
            ]),
            ("Системные", [
                ('can_edit_ids', '', 'Редактирование ID товаров')
            ]),
            ("Настройки", [
                ('settings_visible',       '', 'Доступ'),
                ('settings_appearance',    '', 'Внешний вид'),
                ('settings_printer',       '', 'Принтер и Чек'),
                ('settings_automation',    '', 'Автоматизация'),
                ('settings_integrations',  '', 'Интеграции'),
                ('settings_database',      '', 'База данных'),
                ('settings_sync',          '', 'Система'),
                ('user_management',        '', 'Пользователи и права')
            ]),
        ]
        self.timeout_multiplier = 3 if self.slow_network_var.get() else 1
        self.delay_multiplier = 3 if self.slow_network_var.get() else 1
        
        # Calculate sizes based on settings (Early to avoid missing style attributes)
        self.calculate_sizes()
        
        # === LIVE BOT MANAGER ===
        self.live_bot_queue = queue.Queue()
        self.live_bot_retry_list = []
        self.live_bot_retry_lock = threading.Lock()
        self.live_bot_thread = None
        # _init_live_bot_manager called deferred after create_widgets
        
        # Session tracking for history logs (always enabled)
        self.session_start_time = None
        self.session_logs = []
        self.session_purchases = []  # List of {user_id, items: [{code, qty, price}], success, has_discount}
        self.recovered_orders = 0  # Orders recovered after page refresh/re-login
        self.failed_items = {}  # Track out-of-stock items: {code: count}
        self.session_blacklist = {}  # Smart retry: items to skip this session {code: fail_count}
        self.BLACKLIST_THRESHOLD = 2  # Skip item after this many failures
        
        # Database sync tracking - optimized for Supabase free tier (500K/month)
        # 2 hours = 7200 seconds → 12 syncs/day × 1000 users × 31 days = 372,000/month
        # Force sync on startup by setting last sync to past
        self.db_sync_interval = 7200  # 2 hours in seconds
        self.last_db_sync = datetime.now() - timedelta(seconds=self.db_sync_interval + 100)
        self.is_processing = False  # Track if automation is running

        # Progress tracking for resume capability
        self.current_progress = None  # {step, order_index, user_id, file_path, completed_ids}
        self.resumed_from = None  # Track if session was resumed
        
        # Start polling for data updates (UI Refresh)
        self.last_ui_goods_sync = 0
        self.last_ui_partners_sync = 0
        if self.master:
            self._schedule(5000, self.start_data_polling)

        # Patterns
        self.re_id_pattern = re.compile(r'([a-zA-Z]{2}\d{8})', re.IGNORECASE)
        self.re_product_code_pattern_xlsx = re.compile(r'([A-Z0-9]{6})\s.*', re.IGNORECASE)

        # Session state
        self.successful_ids = []
        self.failed_attempts = []
        self.stop_event = threading.Event()
        
        # Scheduler thread
        self.scheduler_thread = None
        self.scheduler_running = False

        # Init autoreview mixin
        AutoreviewMixin.__init__(self)

        # Create UI
        _cl("GreenLeafApp before create_widgets")
        self.create_widgets()
        _cl("GreenLeafApp after create_widgets")
        
        # Deferred init for live bot manager (after event loop is running)
        self.master.after(100, self._init_live_bot_manager)
        
        # === GLOBAL KEYBINDINGS ===
        master.bind('<Escape>', self._global_escape)
        master.bind('<BackSpace>', self._global_backspace)
        
        # Tab switching (Alt+1, Alt+2...)
        for i in range(1, 10):
            master.bind(f'<Alt-Key-{i}>', lambda e, idx=i-1: self._switch_tab(idx))
            master.bind(f'<Command-Key-{i}>' if sys.platform == 'darwin' else f'<Control-Key-{i}>', 
                        lambda e, idx=i-1: self._switch_tab(idx))
        
        # Sequential tab switching
        master.bind('<Control-Tab>', self._next_tab)
        master.bind('<Control-Shift-Tab>', self._prev_tab)
        
        # Global Actions
        master.bind('<F1>', self._global_action_search)
        master.bind('<Control-f>', self._global_action_search)
        master.bind('<Command-f>' if sys.platform == 'darwin' else '<Control-f>', self._global_action_search)
        
        master.bind('<F5>', self._global_action_refresh)
        master.bind('<Control-r>', self._global_action_refresh)
        
        master.bind('<Alt-Return>', self._global_action_primary)
        master.bind('<Control-Return>', self._global_action_primary)
        
        master.bind('<Control-p>', self._global_action_partner)
        master.bind('<Control-n>', self._global_action_new)
        
        # Bind resize event for responsive layout
        master.bind('<Configure>', self.on_window_resize)
        
        # Check for interrupted progress
        self.check_interrupted_progress()
        
        # Start scheduler if enabled — deferred until after mainloop starts,
        # so the scheduler thread never touches Tk before the event loop runs.
        if self.scheduler_enabled_var.get():
            self.master.after(5000, self._start_scheduler_deferred)

        # C3: worker→Tk event queue pump (thread-safe UI dispatch)
        self._schedule(50, self._ui_queue_pump)
        
        # Start periodic DB sync checker
        self.start_db_sync_checker()
        
        # Start real-time remote version check
        self.fetch_remote_version()
        
        # Setup tray icon (Windows only - causes issues on Mac)
        _cl("GreenLeafApp before tray_icon")
        self.tray_icon = None
        self.tray_status = 'ready'  # ready, working, error, paused
        if sys.platform == 'win32' and TRAY_AVAILABLE:
            self.setup_tray_icon()
            _cl("GreenLeafApp tray_icon done")
            # Override window close to minimize to tray
            master.protocol("WM_DELETE_WINDOW", self.on_window_close)
        else:
            # C3: On Mac/Linux the close button must run the same cleanup
            # path (stop workers, cancel timers) — a bare master.destroy
            # would bypass it and leave old-user processes running.
            master.protocol("WM_DELETE_WINDOW", self.on_window_close)
        _cl("GreenLeafApp protocol done")

        # --- Integration Bot ---
        _cl("GreenLeafApp before IntegrationBot")
        self.integration_bot = IntegrationBot(self)
        self.integration_bot.start()
        try:
            self._refresh_tg_status()
        except Exception:
            pass
        
        # --- Sync Engine ---
        _cl("GreenLeafApp before SyncEngine")
        self.sync_engine = None
        self._init_sync_engine()
        _cl("GreenLeafApp __init__ done")

    def _init_sync_engine(self):
        """Create and start the folder-based Sync Engine (MEGA transport)."""
        try:
            sync_cfg = settings.get_sync_settings()
            folder = (sync_cfg.get('sync_folder_path') or '').strip()
            if not folder or not os.path.isdir(folder):
                _cl("SyncEngine: папка не настроена — движок отключён")
                self.sync_engine = None
                return
            device_key = getattr(self, 'device_key', '') or settings.get_or_create_device_key()
            device_type = settings.get_device_type()
            interval = sync_cfg.get('sync_interval') or 10
            from sync_engine import SyncEngine
            self.sync_engine = SyncEngine(
                self._db_manager, device_key, folder,
                device_type=device_type, sync_interval=interval)
            self._sync_engine_tick()
            _cl(f"SyncEngine: папка {folder}")
        except Exception as e:
            self.sync_engine = None
            _cl(f"SyncEngine init failed: {e}")

    def _sync_engine_tick(self):
        """Periodic worker for the folder Sync Engine (runs sync in background)."""
        if getattr(self, '_shutting_down', False):
            return
        if self._stop_events['sync'].is_set():
            return
        if getattr(self, '_sync_engine_tick_running', False):
            return
        self._sync_engine_tick_running = True
        try:
            eng = getattr(self, 'sync_engine', None)
            if eng and eng.due():
                t = threading.Thread(target=self._run_sync_cycle, args=(eng,),
                                     daemon=True)
                self._track_worker(t)
                t.start()
        except Exception:
            pass
        finally:
            self._sync_engine_tick_running = False
            if (not getattr(self, '_shutting_down', False)
                    and not self._stop_events['sync'].is_set()):
                self._schedule(5000, self._sync_engine_tick)

    def _run_sync_cycle(self, eng):
        """Run one sync cycle in a tracked worker thread, then deregister."""
        try:
            eng.sync_once()
        except Exception:
            pass
        finally:
            try:
                self._workers.remove(threading.current_thread())
            except (ValueError, AttributeError):
                pass

    def fetch_remote_version(self):
        """Fetch latest version and features from Supabase on launch."""
        def _task():
            try:
                import settings
                base_url = settings.SUPABASE_URL.rstrip('/')
                url = f"{base_url}/storage/v1/object/public/backend/version.json"
                resp = requests.get(url, timeout=5)
                if resp.status_code == 200:
                    info = resp.json()
                    self.remote_version_info = info
                    print(f"☁️ Remote version fetched: {info.get('version')}")
                    
                    # If current version is lower, signal update
                    from ui_lang import MODULE_VERSION
                    remote_v = info.get('version', '0.0.0')
                    
                    def ver_to_tuple(v):
                        return tuple(map(int, (re.sub(r'[^0-9.]', '', v).split('.'))))
                    
                    try:
                        if ver_to_tuple(remote_v) > ver_to_tuple(MODULE_VERSION):
                            print("🚀 New version available!")
                            # Optional: auto-trigger popup here or just wait for user to see in settings
                    except: pass
                else:
                    print(f"☁️ Remote version check failed: HTTP {resp.status_code}")
                    if resp.status_code == 400:
                        print(f"   URL used: {url}")
                        try:
                            print(f"   Response: {resp.text[:200]}")
                        except: pass
            except Exception as e:
                print(f"☁️ Remote version check error: {e}")
            finally:
                self.update_check_done = True
        threading.Thread(target=_task, daemon=True).start()


    def get_local_ip(self):
        return settings.get_local_ip()

    def _get_user_device_label(self):
        """Get formatted label: 'Device/User' for history/audit trails."""
        device_name = self.sync_name_var.get() if hasattr(self, 'sync_name_var') else ''
        if not device_name:
            device_name = f"Касса №{self.device_key[:4]}" if hasattr(self, 'device_key') and self.device_key else ''
        username = getattr(self, 'current_username', 'System')
        if device_name:
            return f"{device_name}/{username}"
        return username

    def prevent_treeview_resize(self, event):
        """Prevent user from resizing Treeview columns or triggering row actions from headers."""
        region = event.widget.identify_region(event.x, event.y)
        if region in ("separator", "heading"):
            return "break"

    def setup_treeview_sorting(self, tree, columns, numeric_cols=None):
        """Bind sorting to all headers: 1st click asc, 2nd click desc (▲/▼ marker).
        Also blocks double-clicks on headers so row actions never fire from them."""
        if numeric_cols is None:
            numeric_cols = []
        tree._sort_state = {
            'reverse': {c: False for c in columns},
            'active': None,
            'base': {c: (tree.heading(c)['text'] or '').strip() for c in columns},
            'numeric_cols': list(numeric_cols),
        }
        for col in columns:
            tree.heading(col, command=lambda c=col: self._treeview_sort(tree, c))
        tree.bind('<Double-Button-1>', self._heading_guard)

    def _heading_guard(self, event):
        """Prevent double-clicks on headings/separators from firing row actions."""
        try:
            region = event.widget.identify_region(event.x, event.y)
            if region in ("heading", "separator"):
                return "break"
        except Exception:
            pass
        return None

    def _treeview_sort(self, tree, col):
        st = getattr(tree, '_sort_state', None)
        if not st:
            return
        reverse = st['reverse'].get(col, False)
        hdr = st['base'].get(col, (tree.heading(col)['text'] or '')).lower()
        is_num = (col in st['numeric_cols']
                  or any(k in hdr for k in ('цена', 'сумма', 'итог', 'выручка', 'кол-во', 'скидка',
                                            '№', 'пв', 'балл', 'чек', 'шт', 'остаток', 'номер',
                                            'закуп', 'продаж', 'возврат')))
        is_date = any(k in hdr for k in ('дата', 'время')) or col in ('date', 'datetime', 'date_time')

        def sort_key(row):
            raw = row[0] or ''
            if is_num:
                cleaned = re.sub(r'[^\d.,\-]', '', raw).replace(' ', '')
                if not cleaned:
                    return (1, 0.0)
                try:
                    if re.match(r'^\d{1,3}(,\d{3})+$', cleaned):
                        cleaned = cleaned.replace(',', '')
                    return (0, float(cleaned.replace(',', '.')))
                except ValueError:
                    return (1, 0.0)
            if is_date:
                s = raw.strip()
                for fmt in ("%d.%m.%Y %H:%M", "%d.%m.%Y", "%Y-%m-%d %H:%M:%S",
                            "%Y-%m-%d", "%d.%m.%y %H:%M", "%d.%m.%y"):
                    try:
                        return (0, datetime.strptime(s, fmt).timestamp())
                    except ValueError:
                        continue
                return (1, 0.0)
            return (0, raw.lower())

        items = sorted([(tree.set(k, col), k) for k in tree.get_children('')],
                       key=sort_key, reverse=reverse)
        for idx, (_v, kid) in enumerate(items):
            tree.move(kid, '', idx)

        if st.get('active') and st['active'] != col:
            prev = st['active']
            tree.heading(prev, text=st['base'].get(prev, ''))
        tree.heading(col, text=f"{st['base'].get(col, '')} {'▼' if reverse else '▲'}")
        st['active'] = col
        st['reverse'][col] = not reverse

    def refresh_current_tab(self):
        """Identify active tab and trigger its refresh method."""
        try:
            tab_id = self.notebook.select()
            if not tab_id: return
            tab_widget = self.notebook.nametowidget(tab_id)
            
            if hasattr(self, 'pos_frame') and tab_widget == self.pos_frame:
                self.pos_refresh_cart()
                self.pos_search_entry.delete(0, tk.END)
                self.create_quick_buttons() # Refresh quick items if they changed on another register
            elif hasattr(self, 'sales_frame') and tab_widget == self.sales_frame:
                self.refresh_sales_history()
            elif hasattr(self, 'goods_frame') and tab_widget == self.goods_frame:
                self.refresh_goods_list()
                if hasattr(self, 'goods_notebook'):
                    sub_id = self.goods_notebook.select()
                    if sub_id:
                        sub_widget = self.goods_notebook.nametowidget(sub_id)
                        # Specific subtab refresh
                        if hasattr(sub_widget, 'refresh_cmd'):
                             sub_widget.refresh_cmd()
            elif hasattr(self, 'partners_frame') and tab_widget == self.partners_frame:
                self.refresh_partners_list()
            elif hasattr(self, 'arrival_frame') and tab_widget == self.arrival_frame:
                if hasattr(self, 'arrival_notebook'):
                    # Force refresh by re-triggering tab change logic
                    self.arrival_notebook.event_generate('<<NotebookTabChanged>>', when='tail')
        except Exception as e:
            print(f"Refresh error: {e}")



    def toggle_scheduler_dynamic(self):
        """Start or stop scheduler based on current setting."""
        if self.scheduler_enabled_var.get():
            if not getattr(self, 'scheduler_running', False):
                self.start_scheduler()
        else:
            if getattr(self, 'scheduler_running', False):
                self.stop_scheduler()

    def update_scheduler_cache(self):
        """Update cached settings for the background scheduler thread."""
        old_time = getattr(self, 'cached_scheduled_time', '??:??')
        old_dir = getattr(self, 'cached_watch_directory', None)
        self.cached_scheduled_time = self.scheduled_time_var.get()
        self.cached_watch_directory = self.watch_directory_var.get()
        self.cached_auto_download_receipts = self.auto_download_receipts_var.get()
        self.cached_partner_autoblock = self.partner_autoblock_var.get()
        
        if old_time != self.cached_scheduled_time or old_dir != self.cached_watch_directory:
            self._scheduler_config_generation += 1
        
    def setup_tray_icon(self):
        """Setup system tray icon with menu."""
        if not TRAY_AVAILABLE:
            return
        
        # Create tray icon in separate thread
        def run_tray():
            self.tray_icon = pystray.Icon(
                "PVM.core",
                self.create_tray_image('ready'),
                "PVM.core - Готов",
                menu=self.create_tray_menu()
            )
            self.tray_icon.run()
        
        self.tray_thread = threading.Thread(target=run_tray, daemon=True)
        self.tray_thread.start()
    
    def create_tray_image(self, status='ready'):
        """Create tray icon image based on status."""
        # Colors: ready=green, working=purple, error=red, paused=gray
        colors = {
            'ready': '#4CAF50',    # Green
            'working': '#9C27B0',  # Purple
            'error': '#f44336',    # Red
            'paused': '#9E9E9E',   # Gray
        }
        color = colors.get(status, colors['ready'])
        
        # Create circular icon
        size = 64
        image = Image.new('RGBA', (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        
        # Draw filled circle
        margin = 4
        draw.ellipse([margin, margin, size-margin, size-margin], fill=color)
        
        # Draw "P" letter in center
        try:
            from PIL import ImageFont
            # Try to use a font, fallback to default
            try:
                font = ImageFont.truetype("arial.ttf", 32)
            except:
                font = ImageFont.load_default()
            draw.text((size//2, size//2), "P", fill='white', anchor='mm', font=font)
        except:
            # Simple fallback - just the circle
            pass
        
        return image
    
    def create_tray_menu(self):
        """Create tray icon right-click menu."""
        return pystray.Menu(
            pystray.MenuItem("PVM.core", None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("📊 Показать окно", self.tray_show_window),
            pystray.MenuItem("👤 Сменить пользователя", self.tray_switch_user),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("❌ Выход", self.tray_quit),
        )
    
    def update_tray_status(self, status, tooltip=None):
        """Update tray icon status and tooltip."""
        if not TRAY_AVAILABLE or not self.tray_icon:
            return
        
        self.tray_status = status
        
        # Update icon
        self.tray_icon.icon = self.create_tray_image(status)
        
        # Update tooltip
        if tooltip:
            self.tray_icon.title = tooltip
        else:
            tooltips = {
                'ready': 'PVM.core - Готов',
                'working': 'PVM.core - Работает...',
                'error': 'PVM.core - Ошибка!',
                'paused': 'PVM.core - Приостановлен',
            }
            self.tray_icon.title = tooltips.get(status, 'PVM.core')
        
        # Refresh menu so visible lambdas re-evaluate
        if self.tray_icon:
            try:
                self.tray_icon.menu = self.create_tray_menu()
            except:
                pass
    
    def _restore_from_tray(self):
        """Restore the main window to its previous (zoomed/fullscreen) state."""
        self.master.after(0, self.master.deiconify)
        self.master.after(0, self.master.lift)
        self.master.after(0, self.master.focus_force)
        # Re-apply the zoomed state — it is lost after withdraw()
        self.master.after(50, lambda: self.master.state('zoomed'))
        self.master.after(80, self.master.update_idletasks)

    def tray_show_window(self, icon=None, item=None):
        """Show main window from tray."""
        self._restore_from_tray()
    
    def tray_switch_user(self, icon=None, item=None):
        """Switch user from tray."""
        self._restore_from_tray()
        self.master.after(200, self.request_switch_user)
    
    def tray_quit(self, icon=None, item=None):
        """Quit application completely from tray."""
        def _quit():
            if not self._confirm_bot_busy("Выйти из программы"):
                return
            self._stop_bot_process()
            # Stop tray icon
            if self.tray_icon:
                try:
                    self.tray_icon.stop()
                except:
                    pass
            self._stop_all_workers()
            self._send_exit_report()
            # Destroy main window
            self.master.destroy()
        self.master.after(0, _quit)
    
    def on_window_close(self):
        """Handle window close button - minimize to tray instead of quitting."""
        if TRAY_AVAILABLE and self.tray_icon:
            # Hide window, keep running in tray
            self.master.withdraw()
            # Show notification
            if hasattr(self.tray_icon, 'notify'):
                self.tray_icon.notify(
                    "PVM.core свёрнут в трей",
                    "Приложение продолжает работать в фоне"
                )
        else:
            # No tray - just quit
            if not self._confirm_bot_busy("Закрыть программу"):
                return
            self._stop_bot_process()
            self._stop_all_workers()
            self._send_exit_report()
            self.master.destroy()

    def _send_exit_report(self):
        """Send the daily report before quitting (if enabled in settings)."""
        try:
            cfg = settings.get_integration_settings()
            if not cfg.get('send_report_on_exit'):
                return
            from pvm_core import send_exit_report
            t = threading.Thread(target=lambda: send_exit_report(self, cfg), daemon=True)
            t.start()
            # Workers are already stopped; wait for the report to finish so
            # the process does not exit mid-send.
            t.join(timeout=10)
        except Exception as e:
            print(f"[EXIT REPORT] {e}")

    def _bot_busy(self):
        """Check whether PV Bot (or related automation) is currently working."""
        if getattr(self, 'is_processing', False):
            return True
        if getattr(self, '_ar_running', False):
            return True
        live_var = getattr(self, 'live_bot_v2_var', None)
        if live_var is not None and live_var.get():
            queue = getattr(self, 'live_bot_queue', None)
            if queue is not None and queue.qsize() > 0:
                return True
            if getattr(self, 'live_bot_retry_list', None):
                return True
        return False

    def _confirm_bot_busy(self, action_text):
        """Ask for confirmation when the PV Bot is busy.
        Returns True when the action may proceed."""
        if not self._bot_busy():
            return True
        return messagebox.askyesno(
            "PV Бот работает",
            f"PV Бот сейчас выполняет операции.\n\n{action_text}?\n\n"
            "При продолжении работа бота будет остановлена.",
            icon='warning')

    def _schedule(self, ms, func):
        """Schedule a Tk after callback and track its id so it can be
        cancelled before the window is destroyed (prevents bgerror spam
        during user switch / quit)."""
        try:
            aid = self.master.after(ms, func)
        except Exception:
            return None
        pending = getattr(self, '_pending_afters', None)
        if pending is None:
            pending = []
            self._pending_afters = pending
        pending.append(aid)
        return aid

    def _cancel_pending_afters(self):
        """Cancel all tracked periodic timers (call before destroy)."""
        pending = getattr(self, '_pending_afters', None)
        if not pending:
            return
        self._pending_afters = []
        for aid in pending:
            try:
                self.master.after_cancel(aid)
            except Exception:
                pass

    def _stop_bot_process(self):
        """Stop PV Bot automation and wait briefly for the worker thread
        to finish (avoids Playwright greenlet crashes during teardown)."""
        if self.is_processing:
            self.stop_event.set()
            self.is_processing = False
            # Do NOT close the browser from the Tk thread: Playwright's sync
            # context belongs to the worker thread and cross-thread calls
            # produce the greenlet "Cannot switch to a different thread"
            # crash. The worker exits by itself via stop-aware polling.
            th = getattr(self, '_processing_thread', None)
            if th is not None and th.is_alive():
                try:
                    th.join(timeout=3)
                except Exception:
                    pass

    def _track_worker(self, thread):
        """Register a worker thread for the shutdown coordinator."""
        if thread is not None:
            self._workers.append(thread)

    def _ui_call(self, func, *args, delay_ms=None, **kwargs):
        """Thread-safe dispatch of a callback onto the Tk thread.

        Workers must never call master.after / touch Tk variables directly;
        they enqueue here and the pump executes on the main thread."""
        if getattr(self, '_shutting_down', False):
            return
        try:
            self._ui_queue.put((func, args, kwargs, delay_ms))
        except Exception:
            pass

    def _ui_queue_pump(self):
        """Execute callbacks queued by worker threads (main thread only)."""
        if getattr(self, '_shutting_down', False):
            return
        try:
            for _ in range(50):
                func, args, kwargs, delay_ms = self._ui_queue.get_nowait()
                try:
                    if delay_ms:
                        self._schedule(delay_ms,
                                       lambda f=func, a=args, k=kwargs: f(*a, **k))
                    else:
                        func(*args, **kwargs)
                except Exception:
                    pass
        except queue.Empty:
            pass
        self._schedule(50, self._ui_queue_pump)

    def _stop_all_workers(self):
        """Central shutdown: signal every subsystem, join worker threads and
        cancel timers. Never touches Playwright from the Tk thread."""
        self._shutting_down = True
        for ev in self._stop_events.values():
            try:
                ev.set()
            except Exception:
                pass
        # Scheduler: flag + generation bump kills the loop fast
        try:
            self.scheduler_running = False
        except Exception:
            pass
        self._scheduler_generation = getattr(self, '_scheduler_generation', 0) + 1
        # Integration bot
        try:
            if getattr(self, 'integration_bot', None) is not None:
                self.integration_bot.stop()
        except Exception:
            pass
        # Autoreview
        try:
            if getattr(self, '_ar_stop_event', None) is not None:
                self._ar_stop_event.set()
        except Exception:
            pass
        # Live bot: poison pill so its queue.get() exits promptly
        try:
            q = getattr(self, 'live_bot_queue', None)
            if q is not None:
                q.put(None)
        except Exception:
            pass
        # Sync engine: internal stop flag so an in-flight cycle exits early
        try:
            if getattr(self, 'sync_engine', None) is not None:
                self.sync_engine.stop()
        except Exception:
            pass
        # Join worker threads (bounded wait)
        for t in list(getattr(self, '_workers', [])):
            try:
                if t.is_alive():
                    t.join(timeout=3)
            except Exception:
                pass
        for attr in ('scheduler_thread', 'live_bot_thread', '_ar_thread',
                     '_processing_thread'):
            t = getattr(self, attr, None)
            if t is not None:
                try:
                    t.join(timeout=3)
                except Exception:
                    pass
        self._cancel_pending_afters()

    def request_switch_user(self, event=None):
        """Request switching to another user: stops bot, closes app,
        the login screen is shown again on the next session."""
        def _do_switch():
            if not self._confirm_bot_busy("Остановить бота и сменить пользователя"):
                return
            self._stop_bot_process()
            # Stop tray icon
            if self.tray_icon:
                try:
                    self.tray_icon.stop()
                except:
                    pass
            self._stop_all_workers()
            self.switch_user_requested = True
            self.master.destroy()
        self.master.after(0, _do_switch)

    def _init_live_bot_manager(self):
        """Start background thread for Live PV Bot processing."""
        if not self.live_bot_v2_var.get():
            return
            
        # Snapshot initial settings for the thread
        initial_settings = {
            'url': self.url.get(),
            'login': self.login.get(),
            'password': self.password.get(),
            'timeout_mult': self.timeout_multiplier,
            'delay_mult': self.delay_multiplier,
            'bot_delay': self.live_bot_delay_var.get()
        }
            
        def live_bot_worker(settings_snapshot):
            from playwright.sync_api import sync_playwright
            self.log_message(get_text('live_bot_manager_started', self.lang), "info")
            
            with sync_playwright() as p:
                print("DEBUG: sync_playwright() context entered")
                # TEMPORARY: Disabled headless for debugging
                browser = p.chromium.launch(headless=True)
                print("DEBUG: Browser launched (Headless)")
                page = browser.new_page()
                print("DEBUG: Page created")
                page.set_viewport_size({"width": 1280, "height": 720})
                
                # Suppress print dialogs
                page.add_init_script("Object.defineProperty(window, 'print', { value: function() {} });")
                
                # Fetch fresh multipliers for login
                import settings as _st
                fresh_t_mult = _st.get_timeout_multiplier()
                fresh_d_mult = _st.get_delay_multiplier()
                
                credentials = {
                    'url': settings_snapshot['url'],
                    'login': settings_snapshot['login'],
                    'password': settings_snapshot['password'],
                    'timeout_mult': fresh_t_mult,
                    'delay_mult': fresh_d_mult
                }
                
                # Increase retries for slow networks
                max_retries = 8 if fresh_t_mult > 1.5 else 5
                
                # Initial login
                print("DEBUG: Starting initial login...")
                if not self._login(page, max_retries=max_retries, credentials=credentials):
                    self.log_message(get_text('live_bot_login_failed', self.lang), "error")
                    print("DEBUG: Initial login returned False")
                    browser.close()
                    return
                print("DEBUG: Initial login successful")
                
                while True:
                    try:
                        # 1. Check retry list for ready receipts
                        with self.live_bot_retry_lock:
                            now = time.time()
                            for i in range(len(self.live_bot_retry_list) - 1, -1, -1):
                                rd = self.live_bot_retry_list[i]
                                if now >= rd.get('_retry_at', 0):
                                    retry_rd = self.live_bot_retry_list.pop(i)
                                    count = retry_rd.get('_retry_count', 0) + 1
                                    retry_rd['_retry_count'] = count
                                    self.log_message(get_text('live_bot_retrying', self.lang).format(number=retry_rd['number'], count=count), "info")
                                    self.live_bot_queue.put(retry_rd)

                        # print("DEBUG: Waiting for receipt in queue...")
                        receipt_data = self.live_bot_queue.get(timeout=5)
                        print(f"DEBUG: Got receipt #{receipt_data.get('number')} from queue")
                        if receipt_data is None: break # Shutdown signal
                        
                        # We use the initial bot_delay for now to avoid cross-thread .get()
                        # If the user changes it, they usually restart or it's not critical
                        delay_mult = self.delay_multiplier # Float is safe
                        delay = settings_snapshot['bot_delay'] * delay_mult
                        
                        self._ui_call(lambda m=get_text('ready', self.lang): self.live_bot_status_var.set(m))
                        # Add to UI as Pending if not already there
                        self._ui_call(lambda n=receipt_data['number']: self.update_bot_order_status(n, 'pending'))
                        
                        self.log_message(get_text('live_bot_wait', self.lang).format(delay=delay, number=receipt_data['number']), "info")
                        time.sleep(delay)
                        
                        # Process single order
                        self.log_message(get_text('live_bot_processing', self.lang).format(number=receipt_data['number']), "info")
                        
                        # SKIP non-partner sales immediately
                        if not receipt_data.get('partner_id'):
                            self.log_message(f"ℹ️ Live Bot: {get_text('live_bot_no_partner', self.lang)} - {get_text('skipped', self.lang)} #{receipt_data['number']}", "info")
                            self._db_manager.mark_receipt_live_sent(receipt_id=receipt_data['id'], status=1, error="Skipped (No Partner)")
                            self.live_bot_queue.task_done()
                            continue

                        # Use internal _process_order_data from pv_bot logic (refactored for reuse)
                        status, processed_items, error_msg = self._process_single_receipt_live(page, receipt_data)
                        
                        if status != -1:
                            self._db_manager.mark_receipt_live_sent(receipt_id=receipt_data['id'], status=status, processed_items=processed_items, error=error_msg)
                            if status == 1:
                                self._ui_call(lambda: self.live_bot_status_var.set("✅ " + get_text('ready', self.lang)))
                                self.log_message(get_text('live_bot_processed', self.lang).format(number=receipt_data['number']), "success")
                                self._ui_call(lambda n=receipt_data['number']: self.show_toast(get_text('live_bot_processed', self.lang).format(number=n), "bot_status"))
                            else:
                                self._ui_call(lambda: self.live_bot_status_var.set("⚠️ " + get_text('partial_refund', self.lang)))
                                self.log_message(get_text('live_bot_partial', self.lang).format(number=receipt_data['number']) + f" {error_msg}", "warning")
                                self._ui_call(lambda n=receipt_data['number']: self.show_toast(get_text('live_bot_partial', self.lang).format(number=n), "bot_status"))
                        else:
                            # Special handling for timeouts and broken pipes: add to retry list
                            if any(k in error_msg.lower() for k in ["таймаут", "timeout", "broken pipe", "errno 32"]):
                                current_retries = receipt_data.get('_retry_count', 0)
                                if current_retries < 3:
                                    self._ui_call(lambda c=current_retries: self.live_bot_status_var.set("⏳ Retry #" + str(c+1)))
                                    receipt_data['_retry_at'] = time.time() + 120 # Wait 2 minutes
                                    with self.live_bot_retry_lock:
                                        self.live_bot_retry_list.append(receipt_data)
                                    self.log_message(get_text('live_bot_timeout_retry', self.lang).format(number=receipt_data['number']), "warning")
                                    self.live_bot_queue.task_done()
                                    continue

                            self._ui_call(lambda: self.live_bot_status_var.set("❌ " + get_text('status', self.lang)))
                            self._db_manager.mark_receipt_live_sent(receipt_id=receipt_data['id'], status=-1, error=error_msg)
                            self.log_message(get_text('live_bot_failed', self.lang).format(number=receipt_data['number']) + f" Причина: {error_msg}", "error")
                            self._ui_call(lambda n=receipt_data['number']: self.show_toast(get_text('live_bot_failed', self.lang).format(number=n), "error"))
                        
                        # Clear status after 5s
                        self._ui_call(lambda: self.live_bot_status_var.set(""), delay_ms=5000)
                            
                        self.live_bot_queue.task_done()
                    except queue.Empty:
                        continue
                    except Exception as e:
                        self.log_message(f"⚠️ Live Bot error: {e}", "error")
                        
                browser.close()

        self.live_bot_thread = threading.Thread(target=live_bot_worker, args=(initial_settings,), daemon=True)
        self._track_worker(self.live_bot_thread)
        self.live_bot_thread.start()

    def _process_single_receipt_live(self, page, receipt_data):
        """Process a single receipt in the live browser session.
        Returns (status, processed_items, error_msg) where:
        - status: 1 (success), 2 (partial), -1 (failed)
        - processed_items: list of good_codes successfully added
        - error_msg: string containing the error reason
        """
        # Thread-safe settings from worker snapshot or direct if simple
        t_mult = self.timeout_multiplier
        d_mult = self.delay_multiplier
        
        # Adaptive timeouts (Increased for slow network stability)
        base_timeout = 25000 * t_mult
        long_timeout = 60000 * t_mult
        search_timeout = 15000 * t_mult
        base_delay = 2 * d_mult
        
        partner_id = receipt_data.get('partner_id')
        if not partner_id:
            msg = get_text('live_bot_no_partner', self.lang)
            self.log_message(f"❌ Live Bot: {msg}", "error")
            return -1, [], msg

        try:
            # Navigate to dashboard first (more stable than direct goto)
            receipt_num = receipt_data.get('number', 'Unknown')
            self._ui_call(lambda: self.update_bot_order_status(receipt_num, 'processing'))
            self._ui_call(lambda: self.live_bot_status_var.set("🤖 Навигирую..."))
            
            # Dashboard navigation with retry
            for attempt in range(2):
                try:
                    self.log_message(f"  🌐 Переход в личный кабинет (попытка {attempt+1})...", "debug")
                    page.goto("https://greenleaf-global.com/office/dashboard", timeout=base_timeout)
                    page.wait_for_url("**/dashboard", timeout=base_timeout)
                    break
                except Exception as dash_err:
                    if attempt == 1: raise dash_err
                    self.log_message(f"  ⚠️ Ошибка перехода, пробую обновить страницу...", "warning")
                    page.reload(timeout=base_timeout)
            
            time.sleep(base_delay)
            
            # Click purchase link
            page.click('a[href="#admin/shop/buy"]')
            page.wait_for_selector('input[check_query="login_buy"]', timeout=base_timeout)
            time.sleep(1 * d_mult)
            
            # Fill partner ID
            self._ui_call(lambda: self.live_bot_status_var.set(f"🤖 Партнер: {partner_id}"))
            page.fill('input[check_query="login_buy"]', partner_id)
            time.sleep(2 * d_mult)
            
            # Check if partner exists (UI validation)
            # Some environments shows "Не найдено" text, others might disable button
            time.sleep(1) # wait for validation
            not_found = page.locator('text="Не найдено"').count() > 0 or page.locator('text="Not found"').count() > 0
            button_disabled = page.locator('input[type="submit"][value="Далее"][disabled]').count() > 0
            
            if not_found or button_disabled:
                msg = get_text('live_bot_partner_not_found', self.lang).format(id=partner_id)
                self.log_message(f"❌ Live Bot: {msg}", "error")
                return -1, [], msg
            
            page.click('input[type="submit"][value="Далее"]')
            page.wait_for_selector('input[name="query"]', timeout=long_timeout)
            time.sleep(base_delay)

            processed_items = []
            any_success = False
            any_failure = False
            
            for item in receipt_data['items']:
                item_code = item['good_code']
                self._ui_call( lambda c=item_code: self.live_bot_status_var.set(f"🤖 Товар: {c}"))
                try:
                    # RACE CONDITION PREVENTION: Ensure previous search results are gone
                    # Wait for any existing rows or error text to disappear
                    try:
                        # Find all existing results
                        old_results = page.locator('tr.goods-item, text="Не найдено", text="Not found"')
                        if old_results.count() > 0:
                            # Fill empty and search or just wait for navigation/refresh if the site does that
                            # or more reliably: wait for them to go away after we fill the NEW query
                            pass
                    except:
                        pass

                    page.locator('input[name="query"]').fill("")
                    time.sleep(0.5 * d_mult)
                    page.locator('input[name="query"]').fill(item_code)
                    time.sleep(0.5 * d_mult)
                    page.click('input.chgoods-search-btn')
                    
                    # Wait for UI to confirm search started (optional, depends on site)
                    time.sleep(1 * d_mult) 
                    
                    # Wait for NEW items table OR NEW "not found" message
                    try:
                        page.wait_for_selector('tr.goods-item, text="Не найдено", text="Not found"', timeout=search_timeout)
                    except: 
                        pass 
                    
                    rows = page.locator('tr.goods-item')
                    found_any_variant = rows.count() > 0
                    
                    found = False
                    for i in range(rows.count()):
                        row = rows.nth(i)
                        # Match by price if multiple results
                        price_text = row.locator('td:nth-child(4)').inner_text()
                        
                        # GREENLEAF-SPECIFIC: Sometimes prices look like "3000 (1500)" 
                        # where 3000 is full price and 1500 is discounted.
                        # We should try to match BOTH.
                        all_prices = []
                        for val in re.findall(r'(\d[\d\s\.]*)', price_text):
                            try:
                                p_clean = val.replace(' ', '').replace(',', '.')
                                if p_clean: all_prices.append(float(p_clean))
                            except: continue
                            
                        price_matched = False
                        target_price = float(item['price'])
                        for p in all_prices:
                            # Match exactly OR match as partner price (50% of site price) 
                            # OR match retail (receipt has 1500, site has 3000)
                            if (abs(p - target_price) < 2.0 or 
                                abs(p * 0.5 - target_price) < 2.0 or 
                                abs(p - target_price * 0.5) < 2.0):
                                price_matched = True
                                break
                        
                        if price_matched:
                            qty = int(item['quantity'])
                            for _ in range(qty):
                                row.locator('div.add').click()
                                time.sleep(0.3 * d_mult)
                            processed_items.append(item_code)
                            any_success = True
                            found = True
                            self.log_message(f"  ✅ Live Bot: Added {item_code} x{qty}", "info")
                            break
                    
                    if not found:
                        any_failure = True
                        if found_any_variant:
                            msg = get_text('live_bot_price_mismatch', self.lang).format(code=item_code)
                            self.log_message(f"  ⚠️ Live Bot: {msg}", "warning")
                        else:
                            msg = get_text('live_bot_item_not_found', self.lang).format(code=item_code)
                            self.log_message(f"  ⚠️ Live Bot: {msg}", "warning")
                        
                except Exception as e:
                    any_failure = True
                    item_error = f"Error adding {item_code}: {str(e)[:50]}"
                    self.log_message(f"  ⚠️ Live Bot: {item_error}", "warning")

            if any_success:
                # --- HARDENED DIRECT GLUED STEP 2 (Payment Confirmation) ---
                try:
                    # 1. Click 'Next' in cart (to go to payment method)
                    self.log_message(f"🛒 Live Bot: Clicking 'Next' in cart...", "info")
                    self._ui_call(lambda: self.live_bot_status_var.set("🤖 Оформляю..."))
                    
                    cart_url = page.url
                    page.wait_for_selector('input.btn-next:enabled', timeout=long_timeout)
                    page.click('input.btn-next')
                    
                    # User requested a small pause here
                    time.sleep(3 * d_mult) 
                    
                    # Verify we actually moved from the cart page
                    if page.url == cart_url or "buy?client=" in page.url:
                        # Check if there's an error message on the page (e.g. balance/stock)
                        body_text = page.inner_text("body").lower()
                        if "ошибка" in body_text or "error" in body_text or "недостаточно" in body_text:
                            err_site = body_text[:100].strip()
                            msg = get_text('live_bot_cart_error', self.lang).format(error=err_site)
                            self.log_message(f"⚠️ Live Bot: {msg}", "warning")
                            return (1 if not any_failure else 2), processed_items, msg
                        else:
                            msg = get_text('live_bot_nav_error', self.lang)
                            self.log_message(f"⚠️ Live Bot: {msg}", "warning")
                            return (1 if not any_failure else 2), processed_items, msg

                    # Security: Check if we were redirected to login page (session expired)
                    if "login" in page.url.lower() and "dashboard" not in page.url.lower():
                        self.log_message("🔐 Live Bot: Session expired during checkout, attempting re-login...", "warning")
                        if self._login(page):
                            pass
                        else:
                            raise Exception("Failed to re-login during checkout")

                    # 2. Select Payment Method & Click 'Далее' (Payment Page)
                    self.log_message(f"💳 Live Bot: Waiting for payment/next button...", "info")
                    # Wait for the "Далее" button (the one in payment is btn-success)
                    payment_next = 'input[type="button"][value="Далее"].btn-success'
                    page.wait_for_selector(payment_next, timeout=long_timeout)
                    
                    # Skipping payment method selection as the site lags and it's not strictly necessary.
                    # (User note: "выбор метода оплаты не нужно трогать! бот из за этого застывает")

                    # Click "Далее" on payment page
                    page.click(payment_next)
                    time.sleep(2.5 * d_mult)
                    
                    # 3. Click "Готово" (Confirmation page)
                    self.log_message(f"💳 Live Bot: Clicking 'Готово'...", "info")
                    btn_gotovo = 'input[type="submit"][value="Готово"][shop_goods_button="next"]'
                    page.wait_for_selector(btn_gotovo, timeout=long_timeout)
                    page.click(btn_gotovo)
                    time.sleep(2.5 * d_mult)
                        
                    # 4. Click "Выдать" (Issue page)
                    self.log_message(f"✅ Live Bot: Clicking 'Выдать'...", "info")
                    btn_vidat = 'input[type="button"][value="Выдать"]'
                    page.wait_for_selector(btn_vidat, timeout=long_timeout)
                    page.click(btn_vidat)
                    time.sleep(2.5 * d_mult)

                    # 5. Final Confirmation "Подтвердить" (Approval page)
                    self.log_message(f"✅ Live Bot: Clicking 'Подтвердить'...", "info")
                    btn_confirm = 'input[type="submit"][value="Подтвердить"]'
                    page.wait_for_selector(btn_confirm, timeout=long_timeout)
                    page.click(btn_confirm)
                    
                    self.log_message(f"🎉 Live Bot: Processing COMPLETE for #{receipt_data['number']}", "success")
                    self._ui_call(lambda: self.update_bot_order_status(receipt_num, 'finished', "Processed successfully"))
                    time.sleep(3 * d_mult)
                    
                    # CRITICAL: Return to initial shop page for next receipt to clear state
                    self.log_message(f"🔄 Live Bot: Returning to shop page...", "info")
                    page.goto("https://greenleaf-global.com/do.vshow#admin/shop/buy", timeout=base_timeout)
                    time.sleep(1.5 * d_mult)

                except Exception as step_err:
                    self.log_message(f"⚠️ Live Bot: Finishing steps failed (might need manual check): {step_err}", "warning")
                    if "target closed" in str(step_err).lower() or "context destroyed" in str(step_err).lower():
                        raise
                
                # Check for success
                status = 1 if not any_failure else 2
                error_summary = ""
                if any_failure:
                    error_summary = get_text('live_bot_partial_summary', self.lang)
                return status, processed_items, error_summary
            else:
                return -1, [], get_text('live_bot_no_items', self.lang)

        except Exception as e:
            err_msg = str(e)
            if "Timeout" in err_msg and "exceeded" in err_msg:
                err_msg = "Превышено время ожидания ответа сайта (таймаут)."
            
            self._ui_call(lambda: self.update_bot_order_status(receipt_num, 'failed', err_msg[:60]))
            
            if "target closed" in err_msg.lower():
                err_msg = "Сайт закрыл соединение."
            elif "net::ERR_NAME_NOT_RESOLVED" in err_msg:
                err_msg = "Нет подключения к интернету или сайту."
            elif "Call log:" in err_msg:
                err_msg = err_msg.split("Call log:")[0].strip()
            
            # Additional cleanup for long playwright traces
            if "\n" in err_msg:
                err_msg = err_msg.split("\n")[0].strip()
                
            self.log_message(f"❌ Live Bot: Ошибка для чека #{receipt_data['number']}: {err_msg}", "error", source="pvbot")
            return -1, [], err_msg

    def setup_theme_colors(self):
        """Setup color scheme based on selected theme."""
        # Get theme from settings (default: light)
        theme_name = self.settings.get('theme', 'light')
        
        # Theme definitions with soft/muted colors
        THEMES = {
            'lavender': {
                'name': '💜 Lavender',
                'bg': '#cbd0eb',
                'bg_secondary': '#e0e4f5',
                'bg_tertiary': '#b8bfe0',
                'fg': '#333333',
                'fg_secondary': '#555555',
                'fg_muted': '#666688',
                'accent': '#5c6bc0',
                'accent_hover': '#3f51b5',
                'frame_bg': '#e0e4f5',
                'list_bg': '#FFFFFF',
            },
            'sky': {
                'name': '🌊 Sky',
                'bg': '#cbe0eb',
                'bg_secondary': '#e0eff5',
                'bg_tertiary': '#b8d0e0',
                'fg': '#333333',
                'fg_secondary': '#555555',
                'fg_muted': '#668888',
                'accent': '#0288d1',
                'accent_hover': '#0277bd',
                'frame_bg': '#e0eff5',
                'list_bg': '#FFFFFF',
            },
            'mint': {
                'name': '🌿 Mint',
                'bg': '#e0ebcb',
                'bg_secondary': '#eff5e0',
                'bg_tertiary': '#d0e0b8',
                'fg': '#333333',
                'fg_secondary': '#555555',
                'fg_muted': '#668866',
                'accent': '#558b2f',
                'accent_hover': '#33691e',
                'frame_bg': '#eff5e0',
                'list_bg': '#FFFFFF',
            },
            'rose': {
                'name': '🌸 Rose',
                'bg': '#ebcbd0',
                'bg_secondary': '#f5e0e4',
                'bg_tertiary': '#e0b8c0',
                'fg': '#333333',
                'fg_secondary': '#555555',
                'fg_muted': '#886666',
                'accent': '#c2185b',
                'accent_hover': '#ad1457',
                'frame_bg': '#f5e0e4',
                'list_bg': '#FFFFFF',
            },
            'aqua': {
                'name': '🐬 Aqua',
                'bg': '#cbebe6',
                'bg_secondary': '#e0f5f0',
                'bg_tertiary': '#b8e0d8',
                'fg': '#333333',
                'fg_secondary': '#555555',
                'fg_muted': '#668880',
                'accent': '#00897b',
                'accent_hover': '#00796b',
                'frame_bg': '#e0f5f0',
                'list_bg': '#FFFFFF',
            },
            'forest': {
                'name': '🌲 Forest Green',
                'bg': '#F4F6F1',
                'bg_secondary': '#FFFFFF',
                'bg_tertiary': '#E8ECE2',
                'fg': '#1F1F1F',
                'fg_secondary': '#4A4A4A',
                'fg_muted': '#6B6B6B',
                'accent': '#82AD51',
                'accent_hover': '#6E9543',
                'frame_bg': '#FFFFFF',
                'card_bg': '#FFFFFF',
                'border': '#D7DDD1',
                'input_bg': '#FAFAFA',
                'success': '#4CAF50',
                'success_bg': '#E8F5E9',
                'error': '#D96B6B',
                'error_bg': '#FBE9E9',
                'warning': '#F4C28A',
                'warning_bg': '#FFF3E0',
            },
            'teal': {
                'name': '🌊 Deep Teal',
                'bg': '#F4F8F8',
                'bg_secondary': '#FFFFFF',
                'bg_tertiary': '#E4EDED',
                'fg': '#1E1E1E',
                'fg_secondary': '#4A5252',
                'fg_muted': '#6E7477',
                'accent': '#F4AC5C',
                'accent_hover': '#E0994A',
                'frame_bg': '#FFFFFF',
                'card_bg': '#FFFFFF',
                'border': '#D7E4E5',
                'input_bg': '#FAFAFA',
                'success': '#43A047',
                'success_bg': '#E8F5E9',
                'error': '#D96B6B',
                'error_bg': '#FBE9E9',
                'warning': '#ED9954',
                'warning_bg': '#FFF3E0',
            },
            'dusk': {
                'name': '🌆 Dusk',
                'bg': '#F4F2F8',
                'bg_secondary': '#FFFFFF',
                'bg_tertiary': '#E8E4F0',
                'fg': '#1E1C24',
                'fg_secondary': '#4A4752',
                'fg_muted': '#6C6878',
                'accent': '#9B8EC4',
                'accent_hover': '#8578B0',
                'frame_bg': '#FFFFFF',
                'card_bg': '#FFFFFF',
                'border': '#D8D4E2',
                'input_bg': '#F8F6FC',
                'success': '#4CAF50',
                'success_bg': '#E8F5E9',
                'error': '#C96464',
                'error_bg': '#FBE9E9',
                'warning': '#D4A85A',
                'warning_bg': '#FEF3E0',
            },
            'slate': {
                'name': '🔷 Blue Slate',
                'bg': '#F3F6FA',
                'bg_secondary': '#FFFFFF',
                'bg_tertiary': '#E2E8F0',
                'fg': '#20242A',
                'fg_secondary': '#4A5058',
                'fg_muted': '#68717C',
                'accent': '#A3BCDC',
                'accent_hover': '#8DA8CC',
                'frame_bg': '#FFFFFF',
                'card_bg': '#FFFFFF',
                'border': '#D8E2EC',
                'input_bg': '#F8FAFC',
                'success': '#4CAF50',
                'success_bg': '#E8F5E9',
                'error': '#D96B6B',
                'error_bg': '#FBE9E9',
                'warning': '#F4C28A',
                'warning_bg': '#FFF3E0',
            },
            'warm': {
                'name': '🏜️ Warm Earth',
                'bg': '#FAF8F5',
                'bg_secondary': '#FFFFFF',
                'bg_tertiary': '#F0EBE5',
                'fg': '#2A2A2A',
                'fg_secondary': '#544E48',
                'fg_muted': '#726C66',
                'accent': '#F4C28A',
                'accent_hover': '#E0AE76',
                'frame_bg': '#FFFFFF',
                'card_bg': '#FFFFFF',
                'border': '#E6DDD5',
                'input_bg': '#FDFCFB',
                'success': '#5D8C3B',
                'success_bg': '#EDF5E6',
                'error': '#C96464',
                'error_bg': '#F9E8E8',
                'warning': '#F4C28A',
                'warning_bg': '#FEF3E0',
            },
        }
        
        theme = THEMES.get(theme_name, THEMES['forest'])
        
        def _t(key, fallback):
            return theme.get(key, fallback)
        
        self.colors = {
            'bg': theme['bg'],
            'bg_secondary': theme['bg_secondary'],
            'bg_tertiary': theme['bg_tertiary'],
            'fg': theme['fg'],
            'fg_secondary': theme['fg_secondary'],
            'fg_muted': theme['fg_muted'],
            'accent': theme['accent'],
            'accent_hover': theme['accent_hover'],
            'frame_bg': theme['frame_bg'],
            'card_bg': _t('card_bg', theme['bg_secondary']),
            'success': _t('success', '#2e7d32'),
            'success_bg': _t('success_bg', '#e8f5e9'),
            'error': _t('error', '#c62828'),
            'error_bg': _t('error_bg', '#ffebee'),
            'warning': _t('warning', '#e65100'),
            'warning_bg': _t('warning_bg', '#fff3e0'),
            'border': _t('border', '#cccccc'),
            'input_bg': _t('input_bg', theme['bg_secondary']),
            'input_fg': _t('input_fg', theme['fg']),
            'button_bg': theme['accent'],
            'button_fg': '#ffffff',
            'button_hover': theme['accent_hover'],
            'save_button_bg': theme['accent'],
            'save_button_fg': '#ffffff',
            'key_bg': theme['bg_tertiary'],
            'key_fg': theme['accent'],
            'checkbox_bg': theme['bg_secondary'],
        }
        
        # Store available themes for settings UI
        self.available_themes = THEMES

    def calculate_sizes(self):
        """Calculate font and padding sizes based on preset settings."""
        preset = self.scale_preset_var.get()
        
        # Simplified presets (80%, 87%, 100%)
        # Base scale 50.0 corresponds to 100% (Large)
        if preset == 'Small':     # ~80%
            val = 40
        elif preset == 'Large':   # 100%
            val = 50
        else:                     # Default/Standard ~87%
            val = 44
            self.scale_preset_var.set('Default')
            
        self.interface_size_var.set(val)
        self.font_size_var.set(val)
        self.button_size_var.set(val)

        # Interface scale: affects padding, widget sizes
        # Extreme baseline for POS: 50 is the new 100% (Large)
        val = self.interface_size_var.get()
        self.interface_scale = val / 50.0
        interface_scale = self.interface_scale
        
        self.padding_small = max(2, int(4 * interface_scale))
        self.padding_medium = max(4, int(8 * interface_scale))
        self.padding_large = max(8, int(15 * interface_scale))
        self.button_height = max(1, int(1.8 * interface_scale))
        self.entry_width = max(15, int(30 * interface_scale))
        
        # Font scale: unified with interface scale
        f_val = self.font_size_var.get()
        font_scale = f_val / 50.0
        
        # Massive Base font sizes for POS
        self.font_small = max(10, int(12 * font_scale))
        self.font_normal = max(12, int(14 * font_scale))
        self.font_large = max(16, int(18 * font_scale))
        self.font_title = max(20, int(24 * font_scale))
        
        # Selective bold: keep only large/title bold for visual hierarchy
        ff = self.font_family
        self.font_small_tuple = (ff, self.font_small)
        self.font_small_bold_tuple = (ff, self.font_small, "bold")
        self.font_normal_tuple = (ff, self.font_normal)
        self.font_bold_tuple = (ff, self.font_normal, "bold")
        self.font_large_tuple = (ff, self.font_large, "bold")
        self.font_title_tuple = (ff, self.font_title, "bold")
    
        # Button scale: unified with interface scale
        b_val = self.button_size_var.get()
        btn_scale = b_val / 50.0
        self.btn_padx = max(8, int(12 * btn_scale))
        self.btn_pady = max(4, int(6 * btn_scale))
        self.btn_padx_mini = max(3, int(4 * btn_scale))
        self.btn_pady_mini = max(1, int(2 * btn_scale))

    def start_db_sync_checker(self):
        """Start periodic database sync checker."""
        self.check_db_sync()
    
    def check_db_sync(self):
        """Check if it's time to sync with database."""
        if getattr(self, '_shutting_down', False):
            return
        if getattr(self, '_db_sync_running', False):
            return
        self._db_sync_running = True
        try:
            now = datetime.now()
            elapsed = (now - self.last_db_sync).total_seconds()
            
            # Sync every 1.5 hours (optimized for free tier)
            if elapsed >= self.db_sync_interval:
                self.sync_with_database()
                self.last_db_sync = now
        except Exception as e:
            pass  # Silent fail, don't interrupt user
        
        self._db_sync_running = False
        # Schedule next check in 5 minutes (300000ms) - no need to check more often
        if not getattr(self, '_shutting_down', False):
            self._schedule(300000, self.check_db_sync)
    
    def sync_with_database(self):
        """Sync status and activity with database - optimized for 1 request only."""
        try:
            from supabase import create_client
            supabase = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
            
            # Single request: Update heartbeat AND get current status back
            # Single request: Update heartbeat (SDK returns updated row by default in execute())
            try:
                # Update with local_ip
                result = supabase.table('users').update({
                    'last_seen': datetime.now().isoformat(),
                    'local_ip': self.get_local_ip()
                }).eq('device_key', self.device_key).execute()
            except:
                # Fallback if columns missing - try old schema
                result = supabase.table('users').update({
                    'last_seen': datetime.now().isoformat()
                }).eq('device_key', self.device_key).execute()
            
            # Check if status changed from the returned data
            if result.data and len(result.data) > 0:
                row = result.data[0]
                # Subscription dates/status belong to the store. Keep the
                # device row for heartbeat and sync configuration only.
                new_status = row.get('status', 'inactive')
                new_start = row.get('activation_start', '')
                new_end = row.get('activation_end', '')
                try:
                    license_state = check_license_status_only(self.device_key)
                    if license_state and not any(
                        marker in str(license_state[1]).lower()
                        for marker in ('нет связи', 'ошибка')
                    ):
                        new_status = 'active' if license_state[0] else 'inactive'
                        new_start = license_state[2]
                        new_end = license_state[3]
                except Exception:
                    pass
                
                if new_status.lower() != self.status.lower():
                    self.status = new_status
                    self.activation_start = new_start
                    self.activation_end = new_end
                    # Update UI if status changed
                    try:
                        self.master.after(0, self.rebuild_ui)
                    except:
                        pass
                    if new_status.lower() != 'active':
                        self.log_message("⚠️ License status changed. Please check your subscription.", "warning")
        except Exception as e:
            print(f"DB Sync error details: {e}")
            import traceback
            traceback.print_exc()

    def on_window_resize(self, event):
        """Handle window resize for responsive layout."""
        # Only respond to root window resize
        if event.widget == self.master:
            self.update_responsive_layout()

    def check_interrupted_progress(self):
        """Check if there's interrupted progress to resume."""
        progress = load_progress()
        if progress:
            self._show_resume_dialog(progress)

    def _navigate_to_pvbot(self):
        """Switch to Analytics tab and select PV Bot sub-tab."""
        if hasattr(self, 'analytics_frame') and self.analytics_frame.winfo_exists():
            self.notebook.select(self.analytics_frame)
        if hasattr(self, 'analytics_notebook') and hasattr(self, 'pvbot_inner_frame'):
            for idx, tab_id in enumerate(self.analytics_notebook.tabs()):
                if self.analytics_notebook.nametowidget(tab_id) == self.pvbot_inner_frame:
                    self.analytics_notebook.select(idx)
                    break

    def _show_resume_dialog(self, progress):
        """Show actions for an interrupted PV Bot session."""
        c = self.colors
        ff = self.font_family

        title = get_text('resume_session_title', self.lang)
        filename = os.path.basename(progress.get('file_path', 'Unknown'))
        completed = len(progress.get('completed_ids', []))
        summary = progress.get('session_summary') or {}
        total = int(summary.get('total_orders', 0) or 0)
        last_user_id = progress.get('last_user_id', '') or ''

        dialog = self.create_modal_dialog(title, width=560, height=450, scrollable=False)
        main = dialog.container
        main.configure(padx=24, pady=18)

        header = tk.Frame(main, bg=c['warning_bg'], padx=14, pady=12)
        header.pack(fill="x", pady=(0, 14))
        tk.Label(header, text=get_text('resume_session_badge', self.lang),
                 font=(ff, 9, "bold"), bg=c['warning_bg'], fg=c['warning'],
                 anchor="w").pack(fill="x")
        tk.Label(header, text=get_text('resume_session_description', self.lang),
                 font=(ff, 10), bg=c['warning_bg'], fg=c['fg'],
                 justify="left", anchor="w", wraplength=480).pack(fill="x", pady=(5, 0))

        info_card = tk.Frame(main, bg=c['bg_secondary'], padx=16, pady=12,
                             highlightbackground=c['border'], highlightthickness=1)
        info_card.pack(fill="x", pady=(0, 14))

        file_label = tk.Label(info_card, text=filename, font=(ff, 11, "bold"),
                              bg=c['bg_secondary'], fg=c['fg'], anchor="w",
                              justify="left", wraplength=460)
        file_label.pack(fill="x")
        tk.Label(info_card, text=get_text('resume_session_file', self.lang),
                 font=(ff, 9), bg=c['bg_secondary'], fg=c['fg_muted'],
                 anchor="w").pack(fill="x", pady=(2, 9))

        progress_text = get_text('resume_session_progress', self.lang).format(
            completed=completed, total=total) if total else get_text(
                'resume_session_completed', self.lang).format(completed=completed)
        tk.Label(info_card, text=progress_text, font=(ff, 10),
                 bg=c['bg_secondary'], fg=c['fg'], anchor="w").pack(fill="x")

        if last_user_id:
            tk.Label(info_card,
                     text=get_text('resume_session_last_id', self.lang).format(id=last_user_id),
                     font=(ff, 9), bg=c['bg_secondary'], fg=c['fg_muted'],
                     anchor="w").pack(fill="x", pady=(8, 0))

        tk.Label(main, text=get_text('resume_session_question', self.lang),
                 font=(ff, 10), bg=c['bg'], fg=c['fg_muted'],
                 anchor="w").pack(fill="x", pady=(0, 10))

        def restore():
            self.current_progress = progress
            self.resumed_from = progress.get('last_user_id', '')
            self.successful_ids = progress.get('completed_ids', [])
            if 'session_summary' in progress:
                self._upload_resumed_session(progress['session_summary'])
                self.log_message("   Partial session data uploaded to server", "info")
            self.log_message(f"📂 Resuming interrupted session...", "info")
            self.log_message(f"   Completed orders: {len(self.successful_ids)}", "info")

        def start_now():
            restore()
            if progress.get('file_path'):
                self.report_file_path.set(progress['file_path'])
            self._navigate_to_pvbot()
            self._switch_pvbot_view("main")
            self.master.after(500, self.start_full_process_thread)
            dialog.destroy()

        def schedule_later():
            restore()
            dialog.destroy()
            self._show_scheduler_picker(progress)

        def view_history():
            restore()
            self._navigate_to_pvbot()
            self.master.after(200, lambda: self._switch_pvbot_view("history"))
            dialog.destroy()

        def skip():
            if messagebox.askyesno(
                    get_text('resume_session_skip_title', self.lang),
                    get_text('resume_session_skip_message', self.lang),
                    parent=dialog):
                clear_progress()
                self.log_message("🗑️ Interrupted session cleared.", "info")
                dialog.destroy()

        btn_row = tk.Frame(dialog.btn_frame, bg=c['bg_secondary'])
        btn_row.pack(fill="x", padx=14)
        btn_row.grid_columnconfigure(0, weight=1)
        btn_row.grid_columnconfigure(1, weight=1)
        self._btn(btn_row, text=get_text('resume_session_continue', self.lang),
                  command=start_now, style='success').grid(row=0, column=0, padx=4, sticky="ew")
        self._btn(btn_row, text=get_text('resume_session_schedule', self.lang),
                  command=schedule_later, style='accent').grid(row=0, column=1, padx=4, sticky="ew")

        secondary = tk.Frame(main, bg=c['bg'])
        secondary.pack(fill="x")
        self._btn(secondary, text=get_text('resume_session_history', self.lang),
                  command=view_history, style='neutral', compact=True).pack(side="left")
        self._btn(secondary, text=get_text('resume_session_skip', self.lang),
                  command=skip, style='neutral', compact=True).pack(side="right")

        dialog.focus_set()

    def _show_scheduler_picker(self, progress):
        """Show a time picker dialog to schedule automation run."""
        c = self.colors
        ff = self.font_family
        dialog = tk.Toplevel(self.master)
        dialog.resizable(False, False)
        dialog.title("Запуск по расписанию")
        dialog.configure(bg=c['bg'])
        dialog.transient(self.master)
        dialog.grab_set()

        main = tk.Frame(dialog, bg=c['bg'], padx=20, pady=15)
        main.pack(fill="both", expand=True)

        tk.Label(main, text="Выберите время для запуска:",
                 font=(ff, 11), bg=c['bg'], fg=c['fg']).pack(pady=(0, 12))

        time_frame = tk.Frame(main, bg=c['bg'])
        time_frame.pack(pady=(0, 12))

        hour_var = tk.StringVar(value="09")
        min_var = tk.StringVar(value="00")

        tk.Label(time_frame, text="Час:", font=(ff, 10), bg=c['bg'], fg=c['fg_secondary']).pack(side="left", padx=(0, 5))
        ttk.Spinbox(time_frame, from_=0, to=23, width=4, textvariable=hour_var, format="%02.0f").pack(side="left", padx=(0, 10))
        tk.Label(time_frame, text="Мин:", font=(ff, 10), bg=c['bg'], fg=c['fg_secondary']).pack(side="left", padx=(0, 5))
        ttk.Spinbox(time_frame, from_=0, to=59, width=4, textvariable=min_var, format="%02.0f").pack(side="left")

        def confirm():
            time_str = f"{hour_var.get().zfill(2)}:{min_var.get().zfill(2)}"
            self.scheduled_time_var.set(time_str)
            self.scheduler_enabled_var.set(True)
            self.cached_scheduled_time = time_str
            if not self.scheduler_running:
                self.start_scheduler()
            self.log_message(f"⏰ Автоматизация запланирована на {time_str}", "success")
            if progress.get('file_path'):
                self.report_file_path.set(progress['file_path'])
            dialog.destroy()

        def cancel():
            clear_progress()
            self.log_message("🗑️ Interrupted session cleared.", "info")
            dialog.destroy()

        btn_row = tk.Frame(main, bg=c['bg'])
        btn_row.pack(fill="x")
        btn_row.grid_columnconfigure(0, weight=1)
        btn_row.grid_columnconfigure(1, weight=1)

        self._btn(btn_row, text="Подтвердить", command=confirm, style='success').grid(row=0, column=0, padx=4, sticky="ew")
        self._btn(btn_row, text="Отмена", command=cancel, style='neutral').grid(row=0, column=1, padx=4, sticky="ew")

        main.update_idletasks()
        content_h = main.winfo_reqheight()
        w = int(350 * self.interface_scale)
        h = content_h + 45
        self._center_window(dialog, w, h)

        dialog.bind("<Escape>", lambda e: cancel())
        dialog.focus_set()

    def update_responsive_layout(self):
        """Update layout based on window size."""
        try:
            width = self.master.winfo_width()
            # Compact mode for narrow windows
            self.compact_mode = width < 800
        except:
            self.compact_mode = False

    def _on_device_name_changed(self, *args):
        """Triggered when SYNC_NAME changes via sync_name_var. Refreshes active UI components."""
        new_name = self.sync_name_var.get()
        # Update global settings variable immediately for consistency
        settings.SYNC_NAME = new_name
        
        # 1. Update Settings Header (if it exists on this instance)
        if hasattr(self, 'dev_name_label'):
            self.dev_name_label.config(text=f"💻 {new_name}")
            
        # 2. Refresh current tab data if it displays cashier names (so historical rows update)
        if not hasattr(self, 'notebook'): return
        
        try:
            current_tab_id = self.notebook.select()
            if not current_tab_id: return
            tab_widget = self.notebook.nametowidget(current_tab_id)
            
            # Refresh Sales history if active
            if hasattr(self, 'sales_frame') and tab_widget == self.sales_frame:
                if hasattr(self, 'filter_sales'): self.filter_sales()
                
            # Refresh Arrival history/subtabs if active
            elif hasattr(self, 'arrival_frame') and tab_widget == self.arrival_frame:
                if hasattr(self, 'arrival_notebook'):
                    sub_tab_id = self.arrival_notebook.select()
                    if sub_tab_id:
                        sub_widget = self.arrival_notebook.nametowidget(sub_tab_id)
                        if hasattr(self, 'purchases_frame') and sub_widget == self.purchases_frame:
                            self.refresh_purchases_history()
                        elif hasattr(self, 'writeoffs_frame') and sub_widget == self.writeoffs_frame:
                            self.refresh_writeoffs_history()
                        elif hasattr(self, 'cancelled_frame') and sub_widget == self.cancelled_frame:
                            self.refresh_cancelled_items()
            
            # Refresh Partners history if active
            elif hasattr(self, 'partners_frame') and tab_widget == self.partners_frame:
                if hasattr(self, 'refresh_partners_history'): self.refresh_partners_history()
        except:
            pass # Avoid crashing UI on trace callback

    def _current_search_input(self):
        """Return the search widget for the currently visible tab/sub-tab.
        Single source of truth used by global key redirection and autofocus."""
        try:
            if not hasattr(self, 'notebook'):
                return None
            current_tab = self.notebook.select()
            if not current_tab:
                return None
            tab_widget = self.notebook.nametowidget(current_tab)

            # POS
            if tab_widget == getattr(self, 'pos_frame', None):
                return getattr(self, 'pos_search_entry', None)

            # Arrival (nested notebook)
            if tab_widget == getattr(self, 'arrival_frame', None) and hasattr(self, 'arrival_notebook'):
                sub_id = self.arrival_notebook.select()
                if not sub_id:
                    return None
                sub_widget = self.arrival_notebook.nametowidget(sub_id)
                if sub_widget == getattr(self, 'invoice_frame', None):
                    return getattr(self, 'arrival_search_entry', None)
                if sub_widget == getattr(self, 'goods_frame', None):
                    return getattr(self, 'goods_search', None)
                if sub_widget == getattr(self, 'cancelled_frame', None):
                    return getattr(self, 'cancelled_search', None)
                if sub_widget == getattr(self, 'purchases_frame', None):
                    return getattr(self, 'purchases_search_entry', None)
                return None

            # History (purchases/cancelled) nested notebook
            if tab_widget == getattr(self, 'history_frame', None) and hasattr(self, 'history_notebook'):
                sub_id = self.history_notebook.select()
                if not sub_id:
                    return None
                sub_widget = self.history_notebook.nametowidget(sub_id)
                if sub_widget == getattr(self, 'purchases_frame', None):
                    return getattr(self, 'purchases_search_entry', None)
                if sub_widget == getattr(self, 'cancelled_frame', None):
                    return getattr(self, 'cancelled_search', None)
                return None

            # Partners
            if tab_widget == getattr(self, 'partners_frame', None):
                return getattr(self, 'partners_search', None)

            return None
        except Exception:
            return None

    def _focus_search_field(self):
        """Focus the search field of the currently visible tab (no-op if none)."""
        target = self._current_search_input()
        if target is not None:
            try:
                target.focus_set()
            except Exception:
                pass

    def on_global_keypress(self, event):
        # Ignore modifier keys and unprintable chars like Tab/Return/Esc/Arrows
        if event.keysym in ('Shift_L', 'Shift_R', 'Control_L', 'Control_R', 'Alt_L', 'Alt_R', 'Caps_Lock', 'Tab', 'Return', 'Escape', 'Up', 'Down', 'Left', 'Right'):
            return
            
        # Never hijack keys while a modal dialog is open
        try:
            if self.master.grab_current() is not None:
                return
        except Exception:
            return
            
        # Check what is currently focused
        focused = self.master.focus_get()
        
        # If a Toplevel is focused, don't interfere
        if isinstance(focused, tk.Toplevel) or (focused and focused.winfo_toplevel() != self.master):
            return

        target_input = self._current_search_input()
        if not target_input:
            return
            
        # If focused element is already an entry (other than the target one) we don't interfere.
        if isinstance(focused, (tk.Entry, tk.Text, ttk.Entry)) and focused != target_input:
            return
            
        # Redirect focus to context-specific search entry
        if focused != target_input:
            target_input.focus_set()
            # If the character is printable, insert it since it might have been missed by the redirect
            if event.char and event.char.isprintable():
                target_input.insert(tk.END, event.char)
                return "break"

    def rebuild_ui(self):
        """Rebuild the entire UI (for language/size/theme changes)."""
        # Save current tab index
        try:
            current_tab = self.notebook.index(self.notebook.select())
        except:
            current_tab = 0
        
        # Destroy existing widgets
        for widget in self.master.winfo_children():
            widget.destroy()
        
        # Reload theme colors (important for theme changes!)
        self.setup_theme_colors()
        
        # Recalculate sizes
        self.calculate_sizes()
        
        # Recreate widgets
        self.create_widgets()
        
        # Restore tab selection
        try:
            self.notebook.select(current_tab)
        except:
            pass
        
        # Restart scheduler if needed
        if self.scheduler_enabled_var.get() and not self.scheduler_running:
            self.start_scheduler()

    # =========================================================================
    # PERMISSION HELPERS
    # =========================================================================
    
    def has_permission(self, perm_key):
        """Check if current user has a specific permission."""
        # Role-based permissions are now the primary source of truth,
        # allowing decentralized management (e.g. Arrivals on Secondary)
        if self.current_role in ('admin', 'superadmin'):
            return True
        return self.permissions.get(perm_key, False)
    
    # =========================================================================
    # GLOBAL KEYBINDING HANDLERS
    # =========================================================================
    
    def _global_escape(self, event=None):
        """Handle Escape key globally."""
        # If a Toplevel is focused, don't interfere (dialogs handle their own Esc)
        focused = self.master.focus_get()
        # If focus is within a Toplevel dialog, let the dialog handle it
        if focused:
            try:
                if focused.winfo_toplevel() != self.master:
                    return
            except:
                pass
        # On POS tab: clear cart
        try:
            current_tab = self.notebook.nametowidget(self.notebook.select())
            if current_tab == self.pos_frame and self.cart:
                self.pos_clear_cart()
        except:
            pass
    
    def _global_backspace(self, event=None):
        """Handle Backspace key globally — only acts as cancel when no text widget is focused."""
        focused = self.master.focus_get()
        # Don't interfere with text/entry widgets
        if isinstance(focused, (tk.Entry, tk.Text, ttk.Entry, scrolledtext.ScrolledText)):
            return
        
        # POS-specific: if cart item is selected, delete ONLY that item
        try:
            current_tab = self.notebook.nametowidget(self.notebook.select())
            if current_tab == self.pos_frame:
                if hasattr(self, 'cart_tree') and self.cart_tree.selection():
                    self.pos_remove_item()
                    return "break"
        except:
            pass
            
        # Same as Escape (fallback)
        self._global_escape(event)

    def _should_show_pricing(self):
        if not self.activation_end:
            return False
        try:
            end = datetime.strptime(self.activation_end.strip(), "%d.%m.%Y").date()
            remaining = (end - datetime.now().date()).days
            return remaining <= 3
        except:
            return False

    def setup_universal_navigation(self, tree, details_callback=None, enable_multi_select=False):
        """Bind keyboard navigation (Up/Down/Left/Right/Enter/Space + Shift variants).
        
        Plain navigation always picks a single item and clears multi-selection.
        Shift (+Up/Down/Left/Right) extends/reduces range from a fixed anchor.
        Shift+Left → anchor to first; Shift+Right → anchor to last.
        Mouse clicks update the anchor. Behavior is similar to Finder/Explorer.
        """
        if not tree: return
        tree._nav_anchor = None

        if details_callback:
            tree.bind('<Return>', lambda e: details_callback(), add="+")
            tree.bind('<Double-1>', lambda e: details_callback(), add="+")
            tree.bind('<space>', lambda e: details_callback(), add="+")

        # Mouse click updates anchor (for predictable Shift+click range)
        def _on_click(event):
            item = tree.identify_row(event.y)
            if item:
                tree._nav_anchor = item
        tree.bind('<Button-1>', _on_click, add="+")

        def _select_single(tree, item):
            tree.selection_set(item)
            tree._nav_anchor = item
            tree.focus(item)
            tree.see(item)

        def _select_range(tree, anchor, target, children):
            if anchor is None or anchor not in children:
                _select_single(tree, target)
                return
            aidx = list(children).index(anchor)
            tidx = list(children).index(target)
            start, end = (aidx, tidx) if aidx <= tidx else (tidx, aidx)
            tree.selection_set(children[start:end + 1])
            tree.focus(target)
            tree.see(target)

        def _next(tree, children, current):
            idx = list(children).index(current) if current in children else -1
            return children[idx + 1] if 0 <= idx < len(children) - 1 else None

        def _prev(tree, children, current):
            idx = list(children).index(current) if current in children else -1
            return children[idx - 1] if idx > 0 else None

        def _nav_down(e):
            ch = tree.get_children()
            if not ch:
                return "break"
            cur = tree.focus()
            target = _next(tree, ch, cur) if cur in ch else ch[0]
            if target:
                _select_single(tree, target)
            return "break"

        def _nav_up(e):
            ch = tree.get_children()
            if not ch:
                return "break"
            cur = tree.focus()
            target = _prev(tree, ch, cur) if cur in ch else ch[-1]
            if target:
                _select_single(tree, target)
            return "break"

        tree.bind('<Down>', _nav_down, add="+")
        tree.bind('<Up>', _nav_up, add="+")
        tree.bind('<Right>', lambda e: _select_single(tree, tree.get_children()[-1]) if tree.get_children() else None, add="+")
        tree.bind('<Left>', lambda e: _select_single(tree, tree.get_children()[0]) if tree.get_children() else None, add="+")

        if enable_multi_select:
            def _shift_down(e):
                ch = tree.get_children()
                if not ch:
                    return "break"
                cur = tree.focus()
                target = _next(tree, ch, cur) if cur in ch else ch[-1]
                if target:
                    _select_range(tree, tree._nav_anchor, target, ch)
                return "break"

            def _shift_up(e):
                ch = tree.get_children()
                if not ch:
                    return "break"
                cur = tree.focus()
                target = _prev(tree, ch, cur) if cur in ch else ch[0]
                if target:
                    _select_range(tree, tree._nav_anchor, target, ch)
                return "break"

            tree.bind('<Shift-Down>', _shift_down, add="+")
            tree.bind('<Shift-Up>', _shift_up, add="+")
            tree.bind('<Shift-Left>', lambda e: _select_range(tree, tree._nav_anchor, tree.get_children()[0], tree.get_children()) if tree.get_children() else None, add="+")
            tree.bind('<Shift-Right>', lambda e: _select_range(tree, tree._nav_anchor, tree.get_children()[-1], tree.get_children()) if tree.get_children() else None, add="+")

    def _switch_tab(self, index):
        """Switch to tab by index."""
        try:
            if index < self.notebook.index("end"):
                self.notebook.select(index)
        except:
            pass

    def _next_tab(self, event=None):
        """Switch to next tab."""
        try:
            curr = self.notebook.index("current")
            last = self.notebook.index("end") - 1
            next_idx = 0 if curr == last else curr + 1
            self.notebook.select(next_idx)
        except:
            pass
        return "break"

    def _prev_tab(self, event=None):
        """Switch to previous tab."""
        try:
            curr = self.notebook.index("current")
            last = self.notebook.index("end") - 1
            prev_idx = last if curr == 0 else curr - 1
            self.notebook.select(prev_idx)
        except:
            pass
        return "break"

    def _global_action_search(self, event=None):
        """Focus search field in current tab."""
        try:
            if self.notebook:
                tab_id = self.notebook.select()
                tab_widget = self.notebook.nametowidget(tab_id)
            
            if tab_widget == self.pos_frame and hasattr(self, 'pos_search_entry'):
                self.pos_search_entry.focus_set()
            elif hasattr(self, 'arrival_frame') and tab_widget == self.arrival_frame:
                if hasattr(self, 'arrival_search_entry'):
                    self.arrival_search_entry.focus_set()
            elif hasattr(self, 'partners_frame') and tab_widget == self.partners_frame:
                if hasattr(self, 'partners_search'):
                    self.partners_search.focus_set()
            elif hasattr(self, 'sales_frame') and tab_widget == self.sales_frame:
                # If there's a search in sales, focus it (need to check attribute)
                pass
        except:
            pass
        return "break"

    def _global_action_refresh(self, event=None):
        """Refresh data in current tab."""
        try:
            if self.notebook:
                tab_id = self.notebook.select()
                tab_widget = self.notebook.nametowidget(tab_id)
            
            if tab_widget == self.sales_frame:
                self.refresh_sales_history()
            elif hasattr(self, 'partners_frame') and tab_widget == self.partners_frame:
                self.refresh_partners_list()
            elif hasattr(self, 'arrival_frame') and tab_widget == self.arrival_frame:
                if hasattr(self, 'refresh_goods_list'):
                    self.refresh_goods_list()
        except:
            pass
        return "break"

    def _global_action_primary(self, event=None):
        """Trigger primary action (Checkout/Finalize)."""
        try:
            if self.notebook:
                tab_id = self.notebook.select()
                tab_widget = self.notebook.nametowidget(tab_id)
            
            if tab_widget == self.pos_frame:
                self.pos_checkout()
            elif hasattr(self, 'arrival_frame') and tab_widget == self.arrival_frame:
                if hasattr(self, 'finalize_invoice'):
                    self.finalize_invoice()
        except:
            pass
        return "break"

    def _global_action_partner(self, event=None):
        """Open partner selection in POS."""
        try:
            if self.notebook:
                tab_id = self.notebook.select()
                tab_widget = self.notebook.nametowidget(tab_id)
            if tab_widget == self.pos_frame:
                self.pos_select_partner()
        except:
            pass
        return "break"

    def _global_action_new(self, event=None):
        """Contextual 'Add New' action."""
        try:
            if self.notebook:
                tab_id = self.notebook.select()
                tab_widget = self.notebook.nametowidget(tab_id)
            
            if tab_widget == self.pos_frame:
                self.open_add_partner_dialog()
            elif hasattr(self, 'partners_frame') and tab_widget == self.partners_frame:
                self.show_add_partner_dialog()
            elif hasattr(self, 'arrival_frame') and tab_widget == self.arrival_frame:
                # Add new good from arrival
                if hasattr(self, 'add_new_good_from_arrival'):
                    self.add_new_good_from_arrival()
        except:
            pass
        return "break"

    def format_amount(self, amount, force_decimal=False):
        """Unified number formatting: always whole numbers (no decimals)."""
        if amount is None: return "0"
        try:
            val = float(amount)
        except (ValueError, TypeError):
            return str(amount)
            
        s = f"{int(round(val)):,}"
            
        # Localize separators
        if self.lang == 'ru':
            return s.replace(',', ' ')
        return s

    # =========================================================================
    # DIALOG KEYBINDING HELPER
    # =========================================================================
    
    def _center_window(self, win, width, height):
        """Center a window relative to the master application window."""
        win.update_idletasks()
        mx, my = self.master.winfo_rootx(), self.master.winfo_rooty()
        mw, mh = self.master.winfo_width(), self.master.winfo_height()
        x = max(0, min(mx + mw // 2 - width // 2, win.winfo_screenwidth() - width))
        y = max(0, min(my + mh // 2 - height // 2, win.winfo_screenheight() - height))
        win.geometry(f"{width}x{height}+{x}+{y}")

    def create_modal_dialog(self, title, width=600, height=450, scrollable=True, dismiss_on_outside=False):
        """Create a standardized modal dialog with pinned buttons and adaptive content.

        dismiss_on_outside=True: the dialog closes when the user clicks outside
        it (for trivial content). False (default): the dialog stays on top and
        insists on a decision (forms, warnings, confirmations).
        """
        c = self.colors
        w = int(width * self.interface_scale)
        h = int(height * self.interface_scale)
        
        dialog = tk.Toplevel(self.master)
        dialog.resizable(False, False)
        dialog.title(title)
        dialog.withdraw()
        dialog.minsize(int(w * 0.7), int(h * 0.7))
        dialog.configure(bg=c['bg'])
        dialog.transient(self.master)
        dialog.grab_set()
        
        # Center relative to master
        self._center_window(dialog, w, h)
        
        # ── Zone 1: Button strip (Always at bottom) ──
        btn_frame = tk.Frame(dialog, bg=c['bg_secondary'], pady=8)
        btn_frame.pack(side="bottom", fill="x")
        dialog.btn_frame = btn_frame

        # Separator line above buttons for a premium feel
        ttk.Separator(dialog, orient='horizontal').pack(side="bottom", fill="x")
        
        # ── Zone 2: Content area (fills everything above buttons) ──
        if scrollable:
            canvas = tk.Canvas(dialog, bg=c['bg'], highlightthickness=0)
            scrollbar = AutoScrollbar(dialog, orient="vertical", command=canvas.yview, auto_hide=False)
            scroll_frame = tk.Frame(canvas, bg=c['bg'], padx=20, pady=10)
            
            dialog.container = scroll_frame
            dialog.canvas = canvas
            
            window_id = canvas.create_window((0, 0), window=scroll_frame, anchor="nw")
            
            def _on_canvas_resize(e):
                canvas.itemconfig(window_id, width=e.width)
            canvas.bind('<Configure>', _on_canvas_resize)
            
            def _on_frame_resize(e):
                canvas.configure(scrollregion=canvas.bbox("all"))
            scroll_frame.bind('<Configure>', _on_frame_resize)
            
            canvas.configure(yscrollcommand=scrollbar.set)
            # Pack scrollbar before canvas so it spans the full dialog height
            scrollbar.pack(side="right", fill="y")
            canvas.pack(side="left", fill="both", expand=True)
            
            # Use bind_all for modal usability (scrolling anywhere in window)
            def _on_mousewheel(event):
                if canvas.winfo_exists():
                    # Standard cross-platform scroll
                    if sys.platform == 'darwin':
                        delta = -event.delta
                    else:
                        delta = int(-1 * (event.delta / 120))
                    canvas.yview_scroll(delta, "units")
            
            dialog.bind_all("<MouseWheel>", _on_mousewheel)
            dialog.bind_all("<Button-4>", lambda e: canvas.yview_scroll(-1, "units") if canvas.winfo_exists() else None)
            dialog.bind_all("<Button-5>", lambda e: canvas.yview_scroll(1, "units") if canvas.winfo_exists() else None)
            # Drag-pan on empty zones inside the dialog (bound after content is added)
            canvas.after(100, lambda: self._bind_scroll_target(scroll_frame, canvas))
        else:
            dialog.container = tk.Frame(dialog, bg=c['bg'], padx=20, pady=5)
            dialog.container.pack(side="top", fill="both", expand=True)
            
        dialog.deiconify()
        # Keep modal dialogs above the application window without pinning them
        # above unrelated applications.
        try:
            dialog.lift()
        except Exception:
            pass
        dialog.bind('<Escape>', lambda e: dialog.destroy())
        dialog.bind('<Return>', lambda e: self._trigger_primary_button(dialog))
        dialog.bind('<KP_Enter>', lambda e: self._trigger_primary_button(dialog))
        
        if dismiss_on_outside:
            # Trivial dialog: clicking outside closes it (cancel semantics).
            def _on_dialog_click(e):
                if not dialog.winfo_exists():
                    return
                try:
                    dw, dh = dialog.winfo_width(), dialog.winfo_height()
                    if e.x < 0 or e.y < 0 or e.x >= dw or e.y >= dh:
                        dialog.destroy()
                except Exception:
                    pass
            dialog.bind('<Button-1>', _on_dialog_click, add="+")

            def _on_outside_click(e):
                if not dialog.winfo_exists():
                    return
                try:
                    if e.widget.winfo_toplevel() != dialog:
                        dialog.destroy()
                except Exception:
                    pass
            dialog.bind_all('<Button-1>', _on_outside_click, add="+")

            def _on_focus_out(e):
                # Destroy only when focus moved OUTSIDE the dialog (user clicked
                # away); internal moves (entry → OK button) must keep it open.
                if not dialog.winfo_exists():
                    return
                try:
                    f = self.master.focus_get()
                    if f is None or f.winfo_toplevel() != dialog:
                        dialog.destroy()
                except Exception:
                    pass
            dialog.bind('<FocusOut>', _on_focus_out)
        
        # Add BackSpace as cancel if not in entry
        def on_backspace(e):
            f = dialog.focus_get()
            if not isinstance(f, (tk.Entry, tk.Text, ttk.Entry)):
                dialog.destroy()
        dialog.bind('<BackSpace>', on_backspace)
        
        # Scroll listener cleanup is handled by dialog destruction (bind_all is local to hierarchy usually or handled by if exists)

        return dialog


        return dialog

    def fmt_num(self, value):
        """Format a number without a trailing decimal part when it is whole
        (1500.0 → '1500', 1.5 → '1.5')."""
        try:
            v = float(value)
        except (ValueError, TypeError):
            return str(value)
        if v == int(v):
            return str(int(v))
        return str(v)

    def _validate_decimal_input(self, P):
        """Validation for decimal entries: digits plus at most one dot/comma."""
        if P == "":
            return True
        if P.count('.') + P.count(',') > 1:
            return False
        return P.replace('.', '').replace(',', '').isdigit()

    def _validate_int_input(self, P):
        """Validation for integer entries: digits only (prices must be whole)."""
        if P == "":
            return True
        return P.isdigit() and len(P) <= 9

    def ask_float_dialog(self, title, prompt, initial=0.0, minvalue=None):
        """Custom modal float input (dismiss-on-outside). Returns float or None."""
        result = [None]
        dialog = self.create_modal_dialog(title, width=420, height=250,
                                          scrollable=False, dismiss_on_outside=True)
        main = dialog.container
        c = self.colors

        tk.Label(main, text=prompt, font=self.font_normal_tuple, bg=c['bg'], fg=c['fg_secondary'],
                 wraplength=360, justify="center").pack(pady=(25, 10))

        var = tk.StringVar(value=self.fmt_num(initial))
        entry = tk.Entry(main, textvariable=var, font=self.font_normal_tuple, width=16, justify="center")
        entry.pack(pady=5)
        vcmd = (dialog.register(self._validate_decimal_input), '%P')
        entry.config(validate='key', validatecommand=vcmd)

        def on_ok():
            try:
                val = float(var.get().replace(',', '.'))
            except ValueError:
                self.show_toast("Введите корректное число", "error")
                return
            if minvalue is not None and val < minvalue:
                self.show_toast(f"Значение должно быть ≥ {self.fmt_num(minvalue)}", "warning")
                return
            result[0] = val
            dialog.destroy()

        btn_f = tk.Frame(main, bg=c['bg'])
        btn_f.pack(pady=15)
        ok_btn = self._btn(btn_f, text="ОК", command=on_ok, style='success', width=10, cursor='hand2')
        ok_btn.pack(side="left", padx=8)
        self._btn(btn_f, text="Отмена", command=dialog.destroy, style='neutral', width=10, cursor='hand2').pack(side="left", padx=8)
        dialog.bind('<Return>', lambda e: on_ok())
        dialog.bind('<KP_Enter>', lambda e: on_ok())
        entry.focus_set()
        entry.selection_range(0, tk.END)
        self.master.wait_window(dialog)
        return result[0]

    def ask_string_dialog(self, title, prompt, initial=""):
        """Custom modal string input (dismiss-on-outside). Returns str or None."""
        result = [None]
        dialog = self.create_modal_dialog(title, width=420, height=240,
                                          scrollable=False, dismiss_on_outside=True)
        main = dialog.container
        c = self.colors

        tk.Label(main, text=prompt, font=self.font_normal_tuple, bg=c['bg'], fg=c['fg_secondary'],
                 wraplength=360, justify="center").pack(pady=(25, 10))

        var = tk.StringVar(value=initial)
        entry = tk.Entry(main, textvariable=var, font=self.font_normal_tuple, width=26)
        entry.pack(pady=5)

        def on_ok():
            result[0] = var.get().strip()
            dialog.destroy()

        btn_f = tk.Frame(main, bg=c['bg'])
        btn_f.pack(pady=15)
        ok_btn = self._btn(btn_f, text="ОК", command=on_ok, style='success', width=10, cursor='hand2')
        ok_btn.pack(side="left", padx=8)
        self._btn(btn_f, text="Отмена", command=dialog.destroy, style='neutral', width=10, cursor='hand2').pack(side="left", padx=8)
        dialog.bind('<Return>', lambda e: on_ok())
        dialog.bind('<KP_Enter>', lambda e: on_ok())
        entry.focus_set()
        entry.selection_range(0, tk.END)
        self.master.wait_window(dialog)
        return result[0]

    def bind_mousewheel(self, widget, orient="vertical"):
        """Universal mousewheel binding for different platforms."""
        def _on_mousewheel(event):
            # Windows/MacOS use event.delta
            if sys.platform == 'darwin':
                # MacOS: delta is small, 1 or -1 usually
                delta = -event.delta
            else:
                # Windows: delta is large, 120 or -120
                delta = int(-1 * (event.delta / 120))
            
            if orient == "vertical":
                widget.yview_scroll(delta, "units")
            else:
                widget.xview_scroll(delta, "units")

        # Linux/Unix use Button-4/5
        def _on_button4(event):
            if orient == "vertical":
                widget.yview_scroll(-1, "units")
            else:
                widget.xview_scroll(-1, "units")

        def _on_button5(event):
            if orient == "vertical":
                widget.yview_scroll(1, "units")
            else:
                widget.xview_scroll(1, "units")

        # Bind to the widget itself
        widget.bind("<MouseWheel>", _on_mousewheel)
        widget.bind("<Button-4>", _on_button4)
        widget.bind("<Button-5>", _on_button5)
        
        # Also bind to self and all children so scrolling works when hovering children
        # But this can be aggressive, so we usually bind to children only if needed.
        # For now, let's keep it on the widget.

    # =========================================================================
    # UNIVERSAL SCROLL AREA (mousewheel + touch-drag pan)
    # =========================================================================

    def enable_scroll_area(self, canvas, scrollable):
        """Universal mousewheel + drag-pan (finger/touch) for a Canvas-based
        scroll area: wheel and pan over any empty zone scroll the canvas."""
        try:
            if not canvas.winfo_exists() or not scrollable.winfo_exists():
                return
        except Exception:
            return
        self._bind_scroll_target(canvas, canvas)
        self._bind_scroll_target(scrollable, canvas)

    def enable_scroll_target(self, root, target):
        """Wheel + drag-pan over `root` and its empty zones, scrolling `target`
        (works for Treeview/Listbox/Canvas — anything with yview)."""
        try:
            if not root.winfo_exists() or not target.winfo_exists():
                return
        except Exception:
            return
        self._bind_scroll_target(root, target)

    def _bind_scroll_target(self, root, target):
        """Bind mousewheel + drag-pan on a widget subtree, scrolling `target`."""
        try:
            if not root.winfo_exists() or not target.winfo_exists():
                return
        except Exception:
            return

        def _on_mousewheel(event):
            try:
                if sys.platform == 'darwin':
                    delta = event.delta
                else:
                    delta = event.delta / 120
                y_start, y_end = target.yview()
                if delta > 0 and y_start <= 0:
                    return "break"
                if delta < 0 and y_end >= 1.0:
                    return "break"
                target.yview_scroll(int(-1 * delta), "units")
                return "break"
            except Exception:
                return None

        def _on_btn4(event):
            try:
                target.yview_scroll(-1, "units")
            except Exception:
                pass
            return "break"

        def _on_btn5(event):
            try:
                target.yview_scroll(1, "units")
            except Exception:
                pass
            return "break"

        def _bind_wheel(widget):
            if getattr(widget, '_pv_wheel_bound', False):
                return
            widget._pv_wheel_bound = True
            widget.bind("<MouseWheel>", _on_mousewheel, add="+")
            widget.bind("<Button-4>", _on_btn4, add="+")
            widget.bind("<Button-5>", _on_btn5, add="+")

        skip_types = (tk.Entry, ttk.Entry, tk.Text, ttk.Treeview, tk.Listbox,
                      tk.Scale, ttk.Scale, ttk.Combobox, tk.Spinbox, ttk.Spinbox,
                      tk.Scrollbar, ttk.Scrollbar, tk.Button, ttk.Button)

        def _recursive(widget):
            if isinstance(widget, skip_types):
                return
            _bind_wheel(widget)
            for child in widget.winfo_children():
                _recursive(child)

        _recursive(root)

        # ---- Drag-pan: press on an EMPTY frame/canvas and drag to scroll ----
        # Gesture state lives in a closure (NOT on event.widget) and motion
        # uses root coordinates: the finger crossing child frames used to
        # reset the anchor and cause screen jitter.
        pan_state = {'active': False, 'y0': 0.0, 'v0': 0.0}

        def _pan_start(event):
            if pan_state['active']:
                return
            w = event.widget
            if not isinstance(w, (tk.Frame, tk.LabelFrame, tk.Canvas)):
                return
            if isinstance(w, tk.Canvas) and w is not target:
                return
            pan_state['active'] = True
            pan_state['y0'] = event.y_root
            try:
                pan_state['v0'] = target.yview()[0]
            except Exception:
                pan_state['v0'] = 0.0

        def _pan_motion(event):
            if not pan_state['active']:
                return
            try:
                dy = event.y_root - pan_state['y0']
                v0 = pan_state['v0']
                h = target.winfo_height()
                if h > 0:
                    target.yview_moveto(max(0.0, min(1.0, v0 - dy / float(h))))
            except Exception:
                pass

        def _pan_end(event):
            pan_state['active'] = False

        def _bind_pan(widget):
            if getattr(widget, '_pv_pan_bound', False):
                return
            widget._pv_pan_bound = True
            widget.bind("<Button-1>", _pan_start, add="+")

        def _recursive_pan(widget):
            if isinstance(widget, (tk.Frame, tk.LabelFrame, tk.Canvas)):
                _bind_pan(widget)
            for child in widget.winfo_children():
                _recursive_pan(child)

        _recursive_pan(root)

        # Motion/release are handled at the window level (bound once per
        # toplevel) so the gesture survives the finger moving over child
        # widgets. Dead entries (destroyed targets) are pruned automatically.
        try:
            top = root.winfo_toplevel()
        except Exception:
            top = None
        if top is not None:
            dispatch = getattr(top, '_pv_pan_dispatch', None)
            if dispatch is None:
                dispatch = []
                top._pv_pan_dispatch = dispatch

                def _dispatch_motion(event):
                    live = []
                    for h in dispatch:
                        try:
                            alive = h[2] is None or h[2].winfo_exists()
                        except Exception:
                            alive = False
                        if alive:
                            try:
                                h[0](event)
                            except Exception:
                                pass
                            live.append(h)
                    dispatch[:] = live

                def _dispatch_release(event):
                    live = []
                    for h in dispatch:
                        try:
                            alive = h[2] is None or h[2].winfo_exists()
                        except Exception:
                            alive = False
                        if alive:
                            try:
                                h[1](event)
                            except Exception:
                                pass
                            live.append(h)
                    dispatch[:] = live

                try:
                    top.bind("<B1-Motion>", _dispatch_motion, add="+")
                    top.bind("<ButtonRelease-1>", _dispatch_release, add="+")
                except Exception:
                    pass
            dispatch.append((_pan_motion, _pan_end, target))

    def _add_dialog_button(self, dialog, text, command, style='neutral', side='right', use_grid=False, column=0, width=None, state='normal'):
        """Add a standardized button (via the canonical _btn) with optional grid layout."""
        # Map legacy style names onto the canonical palette
        style_map = {'primary': 'success', 'accent': 'accent',
                     'danger': 'danger', 'neutral': 'neutral'}
        canon = style_map.get(style, 'neutral')

        btn_width = width if width is not None else (16 if use_grid else None)

        btn = self._btn(dialog.btn_frame, text=text, command=command,
                        style=canon, width=btn_width, state=state)

        if use_grid:
            # sticky="ew" ensures the button fills the 33% or 50% column width perfectly
            btn.grid(row=0, column=column, sticky="ew", padx=10, pady=5)
            dialog.btn_frame.grid_columnconfigure(column, weight=1)
        else:
            btn.pack(side=side, padx=12, pady=5)
        return btn

    def _build_search_bar(self, parent, bg, entry_cls=tk.Entry, width=28, **entry_kwargs):
        """Unified search row for all tabs: «🔍 Поиск:» label + styled Entry.
        Only appearance — logic/bindings stay at the call site."""
        tk.Label(parent, text="🔍 Поиск:", font=self.font_small_tuple, bg=bg,
                 fg=self.colors['fg_muted']).pack(side="left", padx=(0, 4))
        entry = entry_cls(parent, font=self.font_normal_tuple, width=width,
                          relief="solid", bd=1, **entry_kwargs)
        entry.pack(side="left", padx=5, fill="x", expand=True, ipady=2)
        return entry

    def _infer_btn_style(self, bg, fg):
        """Infer a canonical button style from an explicit bg/fg color."""
        c = self.colors
        if bg is not None:
            pairs = [
                ('success', c.get('success')),
                ('danger',  c.get('error')),
                ('accent',  c.get('accent')),
                ('warning', c.get('warning')),
                ('warning', c.get('warning_bg')),
            ]
            for name, val in pairs:
                if val is not None and str(bg) == str(val):
                    return name
        return 'neutral'

    def _btn(self, parent, text='', command=None, style=None, bg=None, fg=None,
             font=None, width=None, height=None, relief=None, cursor=None,
             state='normal', padx=None, pady=None, compact=False, **kwargs):
        """Canonical, theme-aware, adaptive button factory.

        Replaces ad-hoc tk.Button(...) calls so every button shares one
        look (flat, hover, scaled padding) and adapts to the active theme.
        Accepts the same kwargs as tk.Button and does NOT auto-pack, so
        existing .pack()/.grid() chains keep working.

        Style is the single source of truth for colors:
            'accent' | 'success' | 'danger' | 'warning' | 'neutral'
        Pass compact=True for small utility buttons (icons, day cells,
        nav arrows) to use the smaller font/padding.
        """
        c = self.colors
        if style is None:
            style = self._infer_btn_style(bg, fg)
        styles = {
            'accent':  (c['accent'], 'white', c['accent_hover']),
            'success': (c['success'], 'white', c.get('success_hover', c['success'])),
            'danger':  (c['error'], 'white', c.get('error_hover', c['error'])),
            'warning': (c['warning'], c.get('warning_fg', c['fg']), c.get('warning_hover', c['warning'])),
            'neutral': (c['bg_tertiary'], c['fg'], c.get('bg_secondary', c['bg_tertiary'])),
        }
        bbg, bfg, hov = styles.get(style, styles['neutral'])

        # Single standard: bold font everywhere, small font for compact utility buttons.
        if compact:
            f = font or self.font_small_tuple
            px = padx if padx is not None else self.btn_padx_mini
            py = pady if pady is not None else self.btn_pady_mini
        else:
            f = font or self.font_bold_tuple
            px = padx if padx is not None else self.btn_padx
            py = pady if pady is not None else self.btn_pady

        # Drop any kwargs that _btn sets explicitly (avoids "multiple values" errors
        # from legacy calls that passed e.g. activebackground/relief/bg directly).
        for _k in ('bg', 'fg', 'font', 'relief', 'cursor', 'width', 'height',
                   'state', 'padx', 'pady', 'text', 'command',
                   'activebackground', 'activeforeground', 'highlightthickness',
                   'highlightbackground', 'bd', 'borderwidth'):
            kwargs.pop(_k, None)

        btn = tk.Button(parent, text=text, command=command, bg=bbg, fg=bfg,
                        activebackground=hov, activeforeground=bfg,
                        font=f, relief='flat',
                        cursor='hand2' if state == 'normal' else 'arrow',
                        width=width, height=height, padx=px, pady=py,
                        state=state, **kwargs)

        def on_enter(e):
            if str(btn.cget('state')) == 'normal':
                btn.config(bg=hov)
        def on_leave(e):
            btn.config(bg=bbg)
        btn.bind('<Enter>', on_enter)
        btn.bind('<Leave>', on_leave)
        return btn

    def _trigger_primary_button(self, dialog):
        """Find and trigger the first active primary-action button in a dialog."""
        primary_texts = [
            "ok", "confirm", "да", "принять", "войти",
            "добавить", "сохранить", "save", "add", "yes",
            "выбрать", "применить", "закрыть", "close"
        ]

        def find_btn(parent):
            for child in parent.winfo_children():
                if isinstance(child, tk.Button):
                    txt = str(child.cget("text")).strip().lower()
                    if any(p in txt for p in primary_texts):
                        if str(child.cget("state")) != "disabled":
                            return child
                result = find_btn(child)
                if result:
                    return result
            return None

        btn = find_btn(dialog)
        if btn:
            btn.invoke()

    @staticmethod
    def bind_dialog_keys(dialog, confirm_callback=None, cancel_callback=None):
        """Bind Enter→confirm and Esc→cancel to a Toplevel dialog."""
        if confirm_callback:
            dialog.bind('<Return>', lambda e: confirm_callback())
            dialog.bind('<KP_Enter>', lambda e: confirm_callback())
        if cancel_callback:
            dialog.bind('<Escape>', lambda e: cancel_callback())
            dialog.bind('<BackSpace>', lambda e: cancel_callback() 
                        if not isinstance(dialog.focus_get(), (tk.Entry, tk.Text)) else None)
        
        # Add visual feedback (flash) if user clicks outside or needs to close
        def on_main_click(event):
            if dialog.winfo_exists():
                dialog.focus_force()
                # Optional: subtle flash effect
                orig_bg = dialog.cget('bg')
                dialog.configure(bg=c.get('bg_secondary', '#3d3d3d'))
                dialog.after(50, lambda: dialog.configure(bg=orig_bg))
        
        # We can't easily capture outside clicks in Tkinter without complex hooks, 
        # but grab_set() handles the blocking part. 
        # The user requested "blinking" - we'll simulate it on focus_force.


    # =========================================================================
    # SALES HISTORY TAB METHODS
    # =========================================================================
    
    @staticmethod
    def _theme_btn_fg(hex_color):
        h = hex_color.lstrip('#')
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255
        return 'black' if luminance > 0.5 else 'white'

    def select_theme(self, theme_id):
        """Select a theme and update button visuals."""
        self.theme_var.set(theme_id)
        c = self.colors
        for tid, btn in self.theme_buttons.items():
            if tid == theme_id:
                btn.configure(bg=c['accent'], fg=self._theme_btn_fg(c['accent']))
            else:
                btn.configure(bg=c['bg_tertiary'], fg=self._theme_btn_fg(c['bg_tertiary']))

    def create_widgets(self):
        """Create the tabbed UI with modern styling - POS first."""
        c = self.colors  # Shorthand
        
        # Apply background to master
        self.master.configure(bg=c['bg'])
        
        # Configure ttk styles with theme colors
        style = ttk.Style()
        try:
            style.theme_use('clam')
        except:
            pass
        
        style.configure('TNotebook', background=c['bg'], padding=(0, 5, 0, 5))
        style.configure('TNotebook.Tab', background=c['bg_tertiary'], foreground=c['fg'],
                       padding=[15, 8], font=self.font_normal_tuple)
        style.map('TNotebook.Tab', 
                 background=[('selected', c['bg_secondary'])],
                 foreground=[('selected', c['accent'])])
        style.configure('TFrame', background=c['bg'])
        style.configure('TScrollbar', background=c['bg_tertiary'], troughcolor=c['bg'],
                       arrowcolor=c['fg_muted'])
        style.configure('TScale', background=c['bg'], troughcolor=c['bg_tertiary'])
        style.configure('Horizontal.TProgressbar', background=c['accent'],
                       troughcolor=c['bg_tertiary'], lightcolor=c['accent'],
                       darkcolor=c['accent_hover'])
        style.configure('TCheckbutton', background=c['bg'], foreground=c['fg'])
        
        # TREEVIEW SCALING
        list_bg = c.get('list_bg', c['bg_secondary'])
        row_h = int(self.font_small * 2.8)
        style.configure('Treeview', font=self.font_small_tuple, rowheight=row_h,
                       background=list_bg, foreground=c['fg'], fieldbackground=list_bg)
        style.configure('Treeview.Heading', font=self.font_small_bold_tuple, background=c['bg_tertiary'], foreground=c['fg'])
        style.map('Treeview', background=[('selected', c['accent'])], foreground=[('selected', 'white')])
        # Striped row colors via tags (applied when populating trees)
        style.configure('Treeview', rowheight=row_h)
        # Configure tag for alternating rows (applied in each refresh_* method)
        # 'even' tag is already default bg; 'odd' tag gets alt color
        
        # Create main container frame
        self.main_container = tk.Frame(self.master, bg=c['bg'])
        self.main_container.pack(fill="both", expand=True, padx=8, pady=(2, 8))
        
        # Create notebook (tabbed interface)
        self.notebook = ttk.Notebook(self.main_container)
        self.notebook.pack(fill="both", expand=True)

        # === POS (CASH REGISTER) TAB - FIRST (permission-gated) ===
        if self.has_permission('pos_view'):
            self.pos_frame = ttk.Frame(self.notebook)
            self.notebook.add(self.pos_frame, text=f"  {get_text('pos_tab', self.lang)}  ")
            self.create_pos_tab()
        
        # === SALES HISTORY TAB - SECOND ===
        if self.has_permission('sales_view'):
            self.sales_frame = ttk.Frame(self.notebook)
            self.notebook.add(self.sales_frame, text=f"  {get_text('sales_tab', self.lang)}  ")
            self.create_sales_tab()
        
        # === NEW ARRIVAL TAB (permission-gated) ===
        if self.has_permission('arrival_view'):
            self.arrival_frame = ttk.Frame(self.notebook)
            self.notebook.add(self.arrival_frame, text=f"  {get_text('new_arrival_tab', self.lang)}  ")
            self.create_arrival_tab()
            
        # === HISTORY TAB (New Section) ===
        if self.has_permission('arrival_view'): # Usually shares same permission or similar
            self.history_frame = ttk.Frame(self.notebook)
            self.notebook.add(self.history_frame, text=f"  {get_text('history_tab', self.lang)}  ")
            self.create_history_tab()
        
        # === PARTNERS TAB (permission-gated) ===
        if self.has_permission('partner_view'):
            self.partners_frame = ttk.Frame(self.notebook)
            self.notebook.add(self.partners_frame, text=f"  {get_text('partners_tab', self.lang)}  ")
            self.create_partners_tab()
    
        # === ANALYTICS SECTION (Combined PV Bot & Statistics & Autoreview) ===
        # Visible when any analytics sub-tab is permitted OR the PV Bot is
        # available via subscription (PV Бот must be reachable regardless of rights).
        if (self.has_permission('analytics_view') or self.has_permission('pvbot_use')
                or self.has_permission('autoreview_view')
                or self.subscription_level in (3, 4)):
            self.analytics_frame = ttk.Frame(self.notebook)
            self.notebook.add(self.analytics_frame, text=f"  {get_text('analytics_tab', self.lang)}  ")
            self.create_analytics_tab_group()
        
        # Tab change handler — auto-focus and refresh
        def on_tab_changed(event):
            if self.notebook:
                tab_id = self.notebook.select()
                tab_widget = self.notebook.nametowidget(tab_id)
            
            # Unified search autofocus: focus the search field of the active tab
            self.master.after(60, self._focus_search_field)
            
            # --- ARRIVALS / WAREHOUSE TAB ---
            if hasattr(self, 'arrival_frame') and tab_widget == self.arrival_frame and hasattr(self, 'arrival_notebook'):
                # Trigger the inner notebook's event handler to focus/refresh
                self.arrival_notebook.event_generate("<<NotebookTabChanged>>")

            # --- HISTORY TAB ---
            if hasattr(self, 'history_frame') and tab_widget == self.history_frame and hasattr(self, 'history_notebook'):
                # Trigger the inner notebook's event handler
                self.history_notebook.event_generate("<<NotebookTabChanged>>")
                
            # --- ANALYTICS TAB ---
            if hasattr(self, 'analytics_frame') and tab_widget == self.analytics_frame and hasattr(self, 'analytics_notebook'):
                # Trigger the inner notebook's event handler
                self.analytics_notebook.event_generate("<<NotebookTabChanged>>")
                # Refresh autoreview session history
                if hasattr(self, '_ar_refresh_history'):
                    self.master.after(200, self._ar_refresh_history)
                            
            # --- SALES TAB ---
            if tab_widget == self.sales_frame and hasattr(self, 'sales_tree'):
                self.reset_sales_filter()
                
            # --- PARTNERS TAB ---
            if hasattr(self, 'partners_frame') and tab_widget == self.partners_frame and hasattr(self, 'partners_tree'):
                self.refresh_partners_list(self.partners_search_var.get() if hasattr(self, 'partners_search_var') else '')

        self.notebook.bind('<<NotebookTabChanged>>', on_tab_changed)

        # === SETTINGS TAB (permission-gated) ===
        if self.has_permission('settings_visible'):
            self.settings_frame = ttk.Frame(self.notebook)
            self.notebook.add(self.settings_frame, text=f"  {get_text('settings_tab', self.lang)}  ")
            self.create_settings_tab()
            # Hide individual settings sections based on permissions
            self._apply_settings_permissions()

        # Status bar (outside notebook)
        status_frame = tk.Frame(self.master, bg=c['bg_tertiary'], height=28)
        status_frame.pack(side=tk.BOTTOM, fill=tk.X)
        status_frame.pack_propagate(False)
        
        self.status_bar = tk.Label(status_frame, text=f"  {get_text('ready', self.lang)}  |  {get_text('receipt_no', self.lang)} {self.receipts_manager.counter}", 
                                   bg=c['bg_tertiary'], fg=c['fg_muted'], anchor=tk.W, font=self.font_small_tuple)
        self.status_bar.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5, pady=4)
        
        # Clock label in status bar
        self.clock_label = tk.Label(status_frame, text="", bg=c['bg_tertiary'], fg=c['fg_muted'], font=self.font_small_tuple)
        self.clock_label.pack(side=tk.RIGHT, padx=10)
        self.update_clock()

    def update_clock(self):
        """Update clock in status bar."""
        if getattr(self, '_shutting_down', False):
            return
        now = datetime.now()
        lbl = self.clock_label
        if lbl is None or not lbl.winfo_exists():
            return
        lbl.config(text=now.strftime("%H:%M:%S  |  %d.%m.%Y"))
        if not getattr(self, '_shutting_down', False):
            self._schedule(1000, self.update_clock)

    def show_loading_overlay(self, message=None):
        """Show a standard modal loading overlay."""
        overlay = tk.Toplevel(self.master)
        overlay.overrideredirect(True)
        overlay.transient(self.master)
        overlay.configure(bg=self.colors['bg_secondary'], highlightthickness=2, highlightbackground=self.colors['accent'])
        
        # Center the overlay
        win_w = 350
        win_h = 100
        sw = self.master.winfo_screenwidth()
        sh = self.master.winfo_screenheight()
        x = int(sw/2 - win_w/2)
        y = int(sh/2 - win_h/2)
        overlay.geometry(f"{win_w}x{win_h}+{x}+{y}")
        
        if message is None:
            message = "Загрузка...\nПожалуйста подождите" if self.lang == 'ru' else "Loading...\nPlease wait"
            
        tk.Label(overlay, text=message, 
                 font=self.font_bold_tuple, bg=self.colors['bg_secondary'], fg=self.colors['fg']).pack(expand=True)
        overlay.update()
        return overlay

    def save_all_settings(self):
        """Save all settings to file and apply changes."""
        # Show loading overlay to minimize apparent freezing
        overlay = self.show_loading_overlay("Применение настроек...\nПожалуйста подождите" if self.lang == 'ru' else "Applying settings...\nPlease wait")

        try:
            if hasattr(self, 'current_permissions_hook') and self.current_permissions_hook:
                try:
                    self.current_permissions_hook()
                except Exception as pe:
                    import traceback
                    traceback.print_exc()
                    print(f"⚠ Permissions hook failed (settings continue): {pe}")
                
            if self.scheduler_enabled_var.get():
                watch_dir = self.watch_directory_var.get().strip()
                if not watch_dir or not os.path.exists(watch_dir):
                    self.show_toast("Неверная папка для отслеживания!", "error")
                    return
    
            saved_scheduled_time = self.settings.get('scheduled_time', '09:00')
            sched_val = self.scheduled_time_var.get()
            try:
                h_str, m_str = sched_val.split(':')
                h, m = int(h_str), int(m_str)
                if not (0 <= h <= 23 and 0 <= m <= 59):
                    raise ValueError
            except Exception:
                sched_val = saved_scheduled_time
                self.scheduled_time_var.set(saved_scheduled_time)
                self.show_toast("Неверное время планировщика — восстановлено предыдущее", "warning")
            new_settings = self.settings.copy()
            new_settings.update({
                'language': self.language_var.get(),
                'theme': self.theme_var.get(), 'interface_size': self.interface_size_var.get(),
                'font_size': self.font_size_var.get(), 'button_size': self.button_size_var.get(),
                'scale_preset': self.scale_preset_var.get(), 'scheduler_enabled': self.scheduler_enabled_var.get(),
                'scheduled_time': sched_val, 'watch_directory': self.watch_directory_var.get(),
                'auto_download_receipts': self.auto_download_receipts_var.get(),
                'partner_autoblock': self.partner_autoblock_var.get(),
                'shutdown_after_done': self.shutdown_after_var.get(), 'autorun_enabled': self.autorun_var.get(),
                'slow_network_mode': self.slow_network_var.get(), 'max_empty_pages': self.max_empty_pages_var.get(),
                'sync_name': self.sync_name_var.get() if hasattr(self, 'sync_name_var') else '',
                'live_bot_v2': self.live_bot_v2_var.get(),
                'live_bot_delay': self.live_bot_delay_var.get(),
            })
            
            # Appearance/notification settings live in 'appearance_settings'
            # (get_appearance_settings reads from there) — keep toast controls
            # and toast_colors in one place so they survive restarts.
            app_set = settings.get_appearance_settings()
            app_set.update({
                'toast_size': self.toast_size_var.get(),
                'toast_alpha': self.toast_alpha_var.get(),
                'toast_position': self.toast_position_var.get(),
                'show_success_toast': self.toast_show_success_var.get(),
                'show_error_toast': self.toast_show_error_var.get(),
                'show_warning_toast': self.toast_show_warning_var.get(),
                'show_info_toast': self.toast_show_info_var.get(),
                'show_print_success_toast': self.toast_show_print_success_var.get(),
                'show_print_error_toast': self.toast_show_print_error_var.get(),
                'show_sync_toast': self.toast_show_sync_var.get(),
                'show_bot_toast': self.toast_show_bot_var.get(),
                'show_inventory_toast': self.toast_show_inventory_var.get(),
                'show_sales_toast': self.toast_show_sales_var.get(),
                'skip_low_stock_warning': self.skip_low_stock_warning_var.get(),
            })
            new_settings['appearance_settings'] = app_set
            
            sizes_changed = (self.settings.get('interface_size', 44) != self.interface_size_var.get() or 
                            self.settings.get('font_size', 44) != self.font_size_var.get() or
                            self.settings.get('button_size', 44) != self.button_size_var.get() or
                            self.settings.get('scale_preset', 'Default') != self.scale_preset_var.get())
            theme_changed = self.settings.get('theme', 'light') != new_settings['theme']
            lang_changed = self.settings.get('language', 'ru') != self.language_var.get()
    
            if hasattr(self, '_get_current_receipt_config'):
                new_settings['receipt_config'] = self._get_current_receipt_config()

            settings.save_settings(new_settings)

            old_autorun = self.settings.get('autorun_enabled', False)
            new_autorun = self.autorun_var.get()
            actual_autorun = is_autorun_enabled()
            if new_autorun:
                # Enable: create the launch entry. If it already existed,
                # enabling is a no-op; if creation fails, surface the error
                # and revert the checkbox so we don't save a false positive.
                if not actual_autorun:
                    if enable_autorun():
                        new_settings['autorun_enabled'] = True
                    else:
                        self.autorun_var.set(False)
                        new_settings['autorun_enabled'] = False
                        self._ui_call(lambda: self.show_toast(
                            "Не удалось включить автозапуск", "error"))
                else:
                    new_settings['autorun_enabled'] = True
            else:
                # Disable: remove the launch entry even if our saved flag was
                # already False but the file somehow exists (desync cleanup).
                if actual_autorun:
                    disable_autorun()
                new_settings['autorun_enabled'] = False
            # Persist any corrections made above (e.g. failure-revert) since
            # save_settings() ran before this block.
            settings.save_settings(new_settings)

            self.settings = new_settings

            # Apply slow-network multiplier immediately (no restart needed)
            self.timeout_multiplier = 3 if self.slow_network_var.get() else 1
            self.delay_multiplier = 3 if self.slow_network_var.get() else 1

            # Ensure background scheduler thread updates its cache (time/directory)
            self.update_scheduler_cache()
            if saved_scheduled_time != self.scheduled_time_var.get():
                self.log_message(
                    f"📅 Настройки планировщика обновлены: {self.scheduled_time_var.get()}",
                    "info", source="pv_bot")
            
            # Save integration settings
            int_set = settings.get_integration_settings()
            int_set.update({
                'email_enabled': self.email_enabled_var.get(),
                'smtp_server': self.smtp_server_var.get(),
                'smtp_port': self.smtp_port_var.get(),
                'smtp_user': self.smtp_user_var.get(),
                'smtp_password': self.smtp_pwd_var.get(),
                'email_recipient': self.email_recipient_var.get(),
                'telegram_enabled': self.tg_enabled_var.get(),
                'tg_bot_token': self.tg_token_var.get(),
                'tg_chat_id': self.tg_chat_id_var.get(),
                'send_report_on_exit': self.send_report_on_exit_var.get(),
                'require_otp_on_failure': self.require_otp_var.get(),
            })
            settings.save_integration_settings(int_set)

            # (Re)Start/Stop Telegram background bot based on new settings
            try:
                if hasattr(self, 'integration_bot') and self.integration_bot:
                    self.integration_bot.stop()
                if int_set.get('telegram_enabled'):
                    from pvm_core import IntegrationBot
                    self.integration_bot = IntegrationBot(self)
                    self.integration_bot.start()
            except Exception as be:
                import traceback
                traceback.print_exc()
                print(f"⚠ Integration bot restart failed (settings continue): {be}")
            try:
                self._refresh_tg_status()
            except Exception:
                pass
            
            # Apply UI changes instantly without restart
            if sizes_changed or theme_changed or lang_changed:
                self.lang = self.language_var.get()
                try:
                    self.rebuild_ui()
                except Exception as re:
                    import traceback
                    traceback.print_exc()
                    print(f"⚠ UI rebuild failed (settings already saved): {re}")
                    self._ui_call(lambda: self.show_toast(
                        get_text('rebuild_ui_failed', self.lang), "warning"))

            # Save Receipt settings
            rcfg = self._get_current_receipt_config()
            settings.save_receipt_config(rcfg)
            self._receipt_config = rcfg

            self._mark_saved()
            try:
                self.refresh_quick_status()
            except Exception as qe:
                print(f"⚠ Quick status refresh failed (settings continue): {qe}")
            self.show_toast(get_text('settings_saved_toast', self.lang), "success")
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"Error saving all settings: {e}")
            self.show_toast(get_text('error_saving_settings', self.lang).format(error=e), "error")
        finally:
            # Safely destroy overlay
            try:
                if overlay.winfo_exists():
                    overlay.destroy()
            except:
                pass
