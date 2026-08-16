# -*- coding: utf-8 -*-
"""
PVM.core v2.7.0 - Database & License Management
================================================
Supabase integration: license checking, device registration, session tracking.
Local data (goods, partners, receipts) is handled by db_sqlite.py.
"""

import os
import sys
import json
import time
import subprocess
import tkinter as tk
from tkinter import messagebox
from datetime import datetime, date
import calendar

import settings

from typing import Optional, Dict, List, Any, Tuple

# =============================================================================
# SUPABASE CLIENT
# =============================================================================
_supabase_client: Any = None

# Session connectivity state
_supabase_unreachable = False
_notifications_fetched_once = False

def get_supabase_client() -> Any:
    """Get or create Supabase client (lazy initialization)."""
    global _supabase_client
    if _supabase_client is None:
        try:
            from supabase import create_client # type: ignore
            try:
                from supabase.lib.client_options import ClientOptions
                options = ClientOptions(postgrest_client_timeout=15.0)
                _supabase_client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY, options=options)
            except (ImportError, AttributeError, TypeError):
                _supabase_client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
        except Exception as e:
            print(f"❌ Error initializing Supabase client: {e}")
            return None
    return _supabase_client


# =============================================================================
# DEVICE KEY MANAGEMENT
# =============================================================================
def get_or_create_device_key() -> str:
    """Get device key from settings module to ensure consistency across the app."""
    import settings
    return settings.get_or_create_device_key()

def get_device_key():
    """Alias for compatibility."""
    return get_or_create_device_key()


# =============================================================================
# CREDENTIAL MANAGEMENT
# =============================================================================
def get_credentials_from_supabase(device_key: str) -> Tuple[Optional[str], Optional[str]]:
    """Get credentials from Supabase by device key, with local caching."""
    global _supabase_unreachable
    
    # Skip network if we already know Supabase is unreachable
    if not _supabase_unreachable:
        try:
            supabase = get_supabase_client()
            if not supabase: return None, None
            result = supabase.table("users").select("login, password").eq("device_key", device_key).execute()
            if result.data and len(result.data) > 0:
                user = result.data[0]
                login = str(user.get("login", "")).strip()
                password = str(user.get("password", "")).strip()
                if login and password:
                    # Cache successful credentials locally
                    import settings
                    settings.update_sync_settings(_gl_l=login, _gl_p=password)
                    return login, password
            return None, None
        except Exception as e:
            _supabase_unreachable = True
            # Quietly log the error if it looks like a network issue
            err_msg = str(e).lower()
            if any(kw in err_msg for kw in ['timeout', '522', 'connection', 'network']):
                print(f"📡 Supabase unreachable for credentials. Checking local cache...")
            else:
                print(f"📡 Supabase error (credentials): {type(e).__name__}")
    
    # Fallback to local cache (immediately if server is unreachable)
    import settings
    s = settings.load_settings()
    cached_l = s.get('_gl_l')
    cached_p = s.get('_gl_p')
    if cached_l and cached_p:
        print("✅ Using locally cached credentials for offline mode.")
        return str(cached_l), str(cached_p)
    
    return None, None


def prompt_for_credentials(parent=None) -> Tuple[Optional[str], Optional[str]]:
    """Prompt user for credentials via GUI (first-time registration)."""
    credentials: Dict[str, Optional[str]] = {"login": None, "password": None}

    def on_submit():
        login = login_entry.get().strip()
        password = password_entry.get().strip()
        if not login or not password:
            messagebox.showerror("Ошибка", "Логин и пароль не могут быть пустыми!")
            return
        credentials["login"] = login
        credentials["password"] = password
        root.destroy()

    if parent and str(parent.state()) != 'withdrawn':
        root = tk.Toplevel(parent)
        root.transient(parent)
    else:
        root = tk.Toplevel() if parent else tk.Tk()
    root.title("PVM.core - Регистрация")
    root.geometry("500x300")
    root.resizable(False, False)
    root.protocol("WM_DELETE_WINDOW", root.destroy)

    root.update_idletasks()
    x = (root.winfo_screenwidth() // 2) - (250)
    y = (root.winfo_screenheight() // 2) - (150)
    root.geometry(f"500x300+{x}+{y}")

    tk.Label(root, text="Добро пожаловать!", font=("Arial", 14, "bold")).pack(pady=10)
    tk.Label(root, text="Введите учетные данные:", font=("Arial", 10)).pack(pady=5)

    frame = tk.Frame(root)
    frame.pack(pady=10)
    tk.Label(frame, text="Логин:", width=10, anchor="e").grid(row=0, column=0, pady=5, padx=5)
    login_entry = tk.Entry(frame, width=30)
    login_entry.grid(row=0, column=1, pady=5)
    tk.Label(frame, text="Пароль:", width=10, anchor="e").grid(row=1, column=0, pady=5, padx=5)
    password_entry = tk.Entry(frame, width=30, show="*")
    password_entry.grid(row=1, column=1, pady=5)

    tk.Label(root, text="Эти данные хранятся анонимно и используются ботом для работы с ПВ.",
             font=("Arial", 8), fg="gray").pack(pady=10)
    tk.Button(root, text="Зарегистрировать", command=on_submit,
              bg="lightblue", width=20, height=2).pack(pady=10)
    if parent:
        parent.wait_window(root)
    else:
        root.mainloop()
    return credentials["login"], credentials["password"]


def register_device_in_supabase(device_key, login=None, password=None):
    """Register new device in Supabase."""
    try:
        supabase = get_supabase_client()
        data = {
            "device_key": device_key,
            "registration_time": datetime.now().isoformat(),
            "status": "Inactive",
            "login": login or "",
            "password": password or "",
            "activation_start": "",
            "activation_end": ""
        }
        supabase.table("users").insert(data).execute()
        print(f"✅ Device registered in Supabase with login: {login}")
        return True
    except Exception as e:
        print(f"❌ ERROR: Failed to register device: {e}")
        import traceback
        traceback.print_exc()
        return False


# =============================================================================
# LICENSE CHECKING
# =============================================================================
def _format_store_date(value):
    """Normalize Supabase dates for the existing UI format."""
    if not value:
        return ""
    raw = str(value).strip()
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(raw, fmt).strftime("%d.%m.%Y")
        except ValueError:
            continue
    return raw


def _fetch_effective_license(device_key):
    """Read the store subscription and apply the device-level override."""
    supabase = get_supabase_client()
    if supabase is None:
        raise RuntimeError("Supabase client unavailable")

    result = supabase.table("users").select("*").eq("device_key", device_key).limit(1).execute()
    if not result.data:
        return None

    user = result.data[0]
    login = (user.get("login") or "").strip()
    max_devices = user.get("max_devices", 1)
    subscription_level = user.get("subscription_level", 4)

    store_result = supabase.table("store_activation").select(
        "status,activation_start,activation_end"
    ).eq("login", login).limit(1).execute()
    if not store_result.data:
        return {
            "active": False,
            "message": "СЦ не активирован",
            "max_devices": max_devices,
            "activation_start": "",
            "activation_end": "",
            "subscription_level": subscription_level,
        }

    store = store_result.data[0]
    activation_start = _format_store_date(store.get("activation_start"))
    activation_end = _format_store_date(store.get("activation_end"))
    store_status = (store.get("status") or "inactive").strip().lower()

    def parse_date(value):
        for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%d/%m/%y"):
            try:
                return datetime.strptime(value, fmt).date()
            except ValueError:
                continue
        return None

    start_date = parse_date(activation_start) if activation_start else None
    end_date = parse_date(activation_end) if activation_end else None
    today = date.today()
    # Subscription status belongs to the store. The device key identifies the
    # device; replacing that key is the administrator's device-disable action.
    active = store_status == "active"
    if active and start_date and today < start_date:
        active = False
    if active and end_date and today > end_date:
        active = False

    if store_status != "active":
        message = f"Статус СЦ: {store.get('status', 'inactive')}"
    elif end_date and today > end_date:
        message = f"Срок подписки истёк: {activation_end}"
    elif start_date and today < start_date:
        message = f"Подписка начнётся: {activation_start}"
    elif end_date:
        message = f"Активно (осталось {(end_date - today).days} дней)"
    else:
        message = "Активно"

    return {
        "active": active,
        "message": message,
        "max_devices": max_devices,
        "activation_start": activation_start,
        "activation_end": activation_end,
        "subscription_level": subscription_level,
    }


def check_license_in_supabase(device_key, parent=None):
    """Check activation status in Supabase and validate expiry."""
    try:
        if get_supabase_client() is None:
            global _supabase_unreachable
            _supabase_unreachable = True
            return None, "Нет связи с сервером", 1, "", 4

        effective = _fetch_effective_license(device_key)
        if effective is not None:
            return (
                effective["active"],
                effective["message"],
                effective["max_devices"],
                effective["activation_end"],
                effective["subscription_level"],
            )

        supabase = get_supabase_client()
        result = supabase.table("users").select("*").eq("device_key", device_key).execute()

        if result.data and len(result.data) > 0:
            user = result.data[0]
            status = user.get("status", "Inactive").strip()
            max_devices = user.get("max_devices", 1)
            subscription_level = user.get("subscription_level", 4) # Default to Full for existing users

            if status.lower() == 'active':
                activation_start = user.get("activation_start", "")
                activation_end = user.get("activation_end", "")
                
                # Auto-calculate activation_end if empty
                if not activation_end or not activation_end.strip():
                    if activation_start and activation_start.strip():
                        try:
                            start_date = datetime.strptime(activation_start.strip(), "%d.%m.%Y")
                        except ValueError:
                            try:
                                start_date = datetime.strptime(activation_start.strip(), "%d/%m/%y")
                            except ValueError:
                                start_date = datetime.now()
                    else:
                        start_date = datetime.now()
                        activation_start = start_date.strftime("%d.%m.%Y")
                    
                    end_date = _add_one_month(start_date)
                    activation_end = end_date.strftime("%d.%m.%Y")
                    
                    supabase.table("users").update({
                        "activation_start": activation_start,
                        "activation_end": activation_end
                    }).eq("device_key", device_key).execute()
                    print(f"✅ Auto-calculated activation dates: {activation_start} - {activation_end}")

                # Check expiry
                if activation_end and activation_end.strip():
                    try:
                        try:
                            end_date = datetime.strptime(activation_end.strip(), "%d.%m.%Y")
                        except ValueError:
                            end_date = datetime.strptime(activation_end.strip(), "%d/%m/%y")
                        
                        current_date = datetime.now().date()
                        end_date_only = end_date.date()

                        if current_date > end_date_only:
                            # TRIGGER: If manually set to 'Active' but end date is in the past, 
                            # we treat it as a renewal trigger and update the dates instead of deactivating.
                            # (This happens when admin changes status to 'active' in Supabase but forgets to clear dates)
                            print(f"🔄 License manually activated but expired ({activation_end}). Resetting for 1 month...")
                            
                            start_date = datetime.now()
                            activation_start = start_date.strftime("%d.%m.%Y")
                            
                            end_date = _add_one_month(start_date)
                            activation_end = end_date.strftime("%d.%m.%Y")
                            
                            supabase.table("users").update({
                                "activation_start": activation_start,
                                "activation_end": activation_end
                            }).eq("device_key", device_key).execute()
                            
                            return True, f"Активно (продлено до {activation_end})", max_devices, activation_end, subscription_level
                        else:
                            days_left = (end_date_only - current_date).days
                            return True, f"Активно (осталось {days_left} дней)", max_devices, activation_end, subscription_level
                    except ValueError:
                        return True, "Активно (некорректная дата окончания)", max_devices, activation_end, subscription_level
                else:
                    return True, "Активно (без ограничения срока)", max_devices, "", subscription_level
            else:
                return False, f"Статус: {status}", max_devices, "", subscription_level
        else:
            # First-time registration
            print(f"🔍 Device key '{device_key}' not found in Supabase.")
            login, password = prompt_for_credentials(parent=parent)
            if login and password:
                if register_device_in_supabase(device_key, login, password):
                    return False, "Устройство зарегистрировано. Ожидание активации администратором.", 1, "", 4
                else:
                    return False, "Сбой регистрации.", 1, "", 4
            else:
                return False, "Регистрация отменена пользователем.", 1, "", 4

    except Exception as e:
        # Detect network/connectivity errors — return None to trigger grace period
        error_str = str(e).lower()
        is_network_error = any(kw in error_str for kw in [
            'connection', 'timeout', 'timed out', 'network', 'unreachable',
            '522', '503', '502', '504', 'eof', 'ssl', 'refused'
        ])

        # postgrest APIError: surface the real reason instead of hiding every
        # error behind "Нет связи с сервером". 5xx / unknown status keeps the
        # offline grace path; 4xx is a real rejection (RLS, missing table,
        # schema mismatch) and must be visible. Also catches Pydantic
        # ValidationError (happens when the server returns HTML).
        try:
            from postgrest.exceptions import APIError  # type: ignore
            if isinstance(e, APIError):
                resp = getattr(e, 'response', None)
                status = getattr(e, 'status_code', None) or getattr(resp, 'status_code', None)
                info = {
                    'code': getattr(e, 'code', None),
                    'message': getattr(e, 'message', None),
                    'details': getattr(e, 'details', None),
                    'hint': getattr(e, 'hint', None),
                }
                print(f"⚠️ Supabase APIError status={status} {info}")
                if status is None or (isinstance(status, (int, float)) and status >= 500):
                    is_network_error = True
                else:
                    reason = info.get('message') or info.get('details') or str(e)
                    return False, f"Ошибка Supabase: {reason}", 1, "", 4
        except ImportError:
            pass

        try:
            from pydantic_core import ValidationError  # type: ignore
            if isinstance(e, ValidationError):
                is_network_error = True
        except ImportError:
            pass

        if is_network_error:
            _supabase_unreachable = True
            # We explicitly do NOT print the traceback here to avoid dumping HTML logs (522 error)
            print(f"⚠️ Supabase unreachable ({type(e).__name__}). Falling back to offline grace period.")
            return None, f"Нет связи с сервером", 1, "", 4

        # Only print traceback for unexpected, non-network errors
        import traceback
        traceback.print_exc()
        return False, f"Ошибка доступа к Supabase: {type(e).__name__}", 1, "", 4


def get_activation_status(device_key):
    """Get activation status for display."""
    try:
        effective = _fetch_effective_license(device_key)
        if effective is not None:
            return (
                "Active" if effective["active"] else "Inactive",
                effective["activation_start"],
                effective["activation_end"],
            )

        supabase = get_supabase_client()
        result = supabase.table("users").select("*").eq("device_key", device_key).execute()
        if result.data and len(result.data) > 0:
            user = result.data[0]
            return user.get("status", "Inactive"), user.get("activation_start", ""), user.get("activation_end", "")
        return "Unknown", "", ""
    except:
        return "Error", "", ""


def check_license_status_only(device_key):
    """Check license status WITHOUT triggering registration (for polling)."""
    try:
        if get_supabase_client() is None:
            return False, "Нет связи с сервером", "", "", 4

        effective = _fetch_effective_license(device_key)
        if effective is not None:
            return (
                effective["active"],
                effective["message"],
                effective["activation_start"],
                effective["activation_end"],
                effective["subscription_level"],
            )

        supabase = get_supabase_client()
        if supabase is None:
            return False, "Нет связи с сервером", "", "", 4
        result = supabase.table("users").select("*").eq("device_key", device_key).execute()
        
        if result.data and len(result.data) > 0:
            user = result.data[0]
            status = user.get("status", "").lower()
            activation_start = user.get("activation_start", "")
            activation_end = user.get("activation_end", "")
            subscription_level = user.get("subscription_level", 4) # Default to Full for existing users
            
            if status == "active":
                if activation_end and activation_end.strip():
                    try:
                        if '.' in activation_end:
                            end_date = datetime.strptime(activation_end.strip(), '%d.%m.%Y').date()
                        else:
                            end_date = datetime.strptime(activation_end.strip(), '%Y-%m-%d').date()
                        
                        if date.today() > end_date:
                            # Reactivation flow for polling too
                            try:
                                print(f"🔄 Polling: License activated but expired. Triggering auto-renewal...")
                                start_date = datetime.now()
                                act_start = start_date.strftime("%d.%m.%Y")
                                
                                end_d = _add_one_month(start_date)
                                act_end = end_d.strftime("%d.%m.%Y")
                                
                                supabase.table("users").update({
                                    "activation_start": act_start,
                                    "activation_end": act_end
                                }).eq("device_key", device_key).execute()
                                return True, "Активно (Обновлено)", act_start, act_end, subscription_level
                            except:
                                return False, "Лицензия истекла", activation_start, activation_end, subscription_level
                                
                        return True, "Активно", activation_start, activation_end, subscription_level
                    except:
                        return True, "Активно", activation_start, activation_end, subscription_level
                else:
                    return True, "Активно", activation_start, "", subscription_level
            else:
                return False, f"Статус: {status}", activation_start, activation_end, subscription_level
        else:
            return False, "Устройство не найдено", "", "", 4
    except Exception as e:
        return False, f"Ошибка: {e}", "", "", 4


# =============================================================================
# HELPER: Date Calculation (+1 month)
# =============================================================================
def _add_one_month(start_date):
    """Calculate a date one month after start_date, handling month overflow."""
    next_month = start_date.month + 1
    next_year = start_date.year
    if next_month > 12:
        next_month = 1
        next_year += 1
    last_day = calendar.monthrange(next_year, next_month)[1]
    end_day = min(start_date.day, last_day)
    return start_date.replace(year=next_year, month=next_month, day=end_day)


# =============================================================================
# HELPER: Build session record for Supabase
# =============================================================================
def _build_session_record(device_key, session_data):
    """Build a session history record dict from raw session data."""
    return {
        'device_key': device_key,
        'login': session_data.get('login', ''),
        'session_date': session_data.get('date', ''),
        'start_time': session_data.get('start_time', ''),
        'end_time': session_data.get('end_time', ''),
        'duration_seconds': session_data.get('duration_seconds', 0),
        'avg_seconds_per_order': session_data.get('avg_seconds_per_order', 0),
        'total_orders': session_data.get('total_orders', 0),
        'successful': session_data.get('successful', 0),
        'failed': session_data.get('failed', 0),
        'recovered': session_data.get('recovered', 0),
        'total_sales': session_data.get('total_sales', 0),
        'total_items': session_data.get('total_items', 0),
        'unique_items': session_data.get('unique_items', 0),
        'avg_items_per_order': session_data.get('avg_items_per_order', 0),
        'min_items_per_order': session_data.get('min_items_per_order', 0),
        'max_items_per_order': session_data.get('max_items_per_order', 0),
        'most_item': session_data.get('most_item', ''),
        'most_item_count': session_data.get('most_item_count', 0),
        'least_item': session_data.get('least_item', ''),
        'least_item_count': session_data.get('least_item_count', 0),
        'unique_users': session_data.get('unique_users', 0),
        'top_user': session_data.get('top_user', ''),
        'top_user_orders': session_data.get('top_user_orders', 0),
        'top_user_items': session_data.get('top_user_items', 0),
        'interrupted': session_data.get('interrupted', False),
        'resumed_from': session_data.get('resumed_from', ''),
    }


# =============================================================================
# SESSION HISTORY UPLOAD
# =============================================================================
def upload_session_to_supabase(device_key, session_data, attempts=1):
    """Upload session history to Supabase. Returns True on success, False otherwise.
    Does NOT auto-queue on failure: the caller decides (queue_failed_session),
    so the final status (uploaded/queued/failed) is explicit."""
    try:
        supabase = get_supabase_client()
        if supabase is None:
            print("Upload failed: Supabase client is None")
            return False
    except Exception as e:
        print(f"Upload failed: cannot init Supabase client: {e}")
        return False
    for attempt in range(max(1, attempts)):
        try:
            supabase.table('session_history').insert(
                _build_session_record(device_key, session_data)
            ).execute()
            return True
        except Exception as e:
            print(f"Error uploading session to Supabase (attempt {attempt + 1}/{attempts}): {e}")
            if attempt + 1 < attempts:
                time.sleep(2)
    return False


# =============================================================================
# SESSION UPLOAD QUEUE
# =============================================================================
UPLOAD_QUEUE_PATH = os.path.join(settings.BASE_DIR, 'cache', '_uq.bin') if settings.BASE_DIR else None


def queue_failed_session(device_key, session_data):
    """Save failed session to queue file for later retry. Returns True when persisted."""
    if not UPLOAD_QUEUE_PATH:
        return False
    try:
        import base64
        queue = load_upload_queue()
        queue.append({
            'device_key': device_key,
            'session_data': session_data,
            'queued_at': datetime.now().isoformat(),
            'retry_count': 0
        })
        os.makedirs(os.path.dirname(UPLOAD_QUEUE_PATH), exist_ok=True)
        enc = base64.b64encode(json.dumps(queue).encode())
        with open(UPLOAD_QUEUE_PATH, 'wb') as f:
            f.write(bytes([b ^ 0x2F for b in enc]))
        return True
    except Exception as e:
        print(f"Error queuing session: {e}")
        return False


def load_upload_queue():
    """Load the upload queue from file."""
    if not UPLOAD_QUEUE_PATH:
        return []
    try:
        import base64
        if os.path.exists(UPLOAD_QUEUE_PATH):
            with open(UPLOAD_QUEUE_PATH, 'rb') as f:
                dec = bytes([b ^ 0x2F for b in f.read()])
                return json.loads(base64.b64decode(dec).decode())
    except Exception as e:
        print(f"Error loading upload queue: {e}")
    return []


def save_upload_queue(queue):
    """Save the upload queue to file."""
    if not UPLOAD_QUEUE_PATH:
        return
    try:
        import base64
        os.makedirs(os.path.dirname(UPLOAD_QUEUE_PATH), exist_ok=True)
        enc = base64.b64encode(json.dumps(queue).encode())
        with open(UPLOAD_QUEUE_PATH, 'wb') as f:
            f.write(bytes([b ^ 0x2F for b in enc]))
    except Exception as e:
        print(f"Error saving upload queue: {e}")


def process_upload_queue():
    """Try to upload queued sessions."""
    queue = load_upload_queue()
    if not queue:
        return 0, 0
    
    print(f"📤 Processing upload queue: {len(queue)} sessions pending...")
    successful = 0
    failed = 0
    remaining_queue: List[Dict[str, Any]] = []
    
    try:
        supabase = get_supabase_client()
        if supabase is None:
            print(f"❌ Cannot connect to Supabase: client is None")
            remaining_queue = queue
            failed = len(queue)
        else:
            for entry in queue:
                if not isinstance(entry, dict):
                    continue
                device_key = entry.get('device_key')
                session_data = entry.get('session_data')
                if not isinstance(session_data, dict):
                    continue
                retry_count = int(entry.get('retry_count', 0))
                try:
                    supabase.table('session_history').insert(
                        _build_session_record(device_key, session_data)
                    ).execute()
                    successful += 1
                except Exception as e:
                    failed += 1
                    if retry_count < 14:
                        entry['retry_count'] = retry_count + 1
                        remaining_queue.append(entry)
    except Exception as e:
        print(f"❌ Cannot connect to Supabase: {e}")
        remaining_queue = queue
        failed = len(queue)
    
    save_upload_queue(remaining_queue)
    if successful > 0:
        print(f"✅ Queue processed: {successful} uploaded, {len(remaining_queue)} remaining")
    return successful, failed


# =============================================================================
# AUTORUN MANAGER
# =============================================================================
def get_autorun_path():
    import sys
    if sys.platform == 'win32':
        return os.path.join(os.environ.get('APPDATA', ''),
                           'Microsoft', 'Windows', 'Start Menu', 'Programs',
                           'Startup', 'PVM.core.lnk')
    elif sys.platform == 'darwin':
        return os.path.expanduser('~/Library/LaunchAgents/com.pvmcore.automation.plist')
    else:
        return os.path.expanduser('~/.config/autostart/pvmcore-automation.desktop')


# Registry-based autorun (HKCU\...\Run) as fallback for Windows
_AUTORUN_REG_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_AUTORUN_REG_VALUE = "PVM.core"


def _enable_autorun_registry():
    """Fallback: create HKCU Run key."""
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, _AUTORUN_REG_KEY, 0, winreg.KEY_SET_VALUE)
        launcher_path = os.path.join(settings.BASE_DIR, 'outlook_telemetry.pyw')
        python_dir = os.path.dirname(sys.executable)
        pythonw = os.path.join(python_dir, 'pythonw.exe')
        if not os.path.exists(pythonw):
            pythonw = sys.executable
        winreg.SetValueEx(key, _AUTORUN_REG_VALUE, 0, winreg.REG_SZ, f'"{pythonw}" "{launcher_path}"')
        winreg.CloseKey(key)
        print("✅ Autorun registry key created")
        return True
    except Exception as e:
        print(f"❌ Registry fallback failed: {e}")
        return False


def _disable_autorun_registry():
    """Remove HKCU Run key if present."""
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, _AUTORUN_REG_KEY, 0, winreg.KEY_SET_VALUE)
        winreg.DeleteValue(key, _AUTORUN_REG_VALUE)
        winreg.CloseKey(key)
        print("✅ Autorun registry key removed")
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"Registry cleanup error: {e}")


def _is_autorun_in_registry():
    """Check if HKCU Run key exists."""
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, _AUTORUN_REG_KEY, 0, winreg.KEY_READ)
        val, _ = winreg.QueryValueEx(key, _AUTORUN_REG_VALUE)
        winreg.CloseKey(key)
        return bool(val)
    except FileNotFoundError:
        return False
    except Exception:
        return False


def enable_autorun():
    try:
        if sys.platform == 'win32':
            launcher_path = os.path.join(settings.BASE_DIR, 'outlook_telemetry.pyw')
            if not os.path.exists(launcher_path):
                print(f"Autorun: launcher not found at {launcher_path}")
                return False
            
            # Find pythonw.exe (silent Python)
            python_dir = os.path.dirname(sys.executable)
            pythonw = os.path.join(python_dir, 'pythonw.exe')
            if not os.path.exists(pythonw):
                pythonw = sys.executable  # fallback to python.exe
            
            shortcut_path = get_autorun_path()
            os.makedirs(os.path.dirname(shortcut_path), exist_ok=True)
            
            # Icon path (next to launcher)
            icon_path = os.path.join(settings.BASE_DIR, 'app.ico')
            
            # Use PowerShell to create .lnk shortcut (no winshell dependency)
            ps_command = (
                f"$s=(New-Object -COM WScript.Shell).CreateShortcut('{shortcut_path}');"
                f"$s.TargetPath='{pythonw}';"
                f"$s.Arguments='\"{launcher_path}\"';"
                f"$s.WorkingDirectory='{settings.BASE_DIR}';"
                f"$s.Description='PVM.core Automation';"
            )
            if os.path.exists(icon_path):
                ps_command += f"$s.IconLocation='{icon_path}';"
            ps_command += "$s.Save()"
            
            # Run PowerShell silently (no visible console window)
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            result = subprocess.run(
                ['powershell', '-Command', ps_command],
                capture_output=True,
                text=True,
                timeout=15,
                startupinfo=startupinfo,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if result.returncode == 0:
                print(f"✅ Autorun shortcut created: {shortcut_path}")
                return True
            else:
                print(f"❌ PowerShell error: {result.stderr}")
                print("⚠️ Trying Registry fallback...")
                return _enable_autorun_registry()
        elif sys.platform == 'darwin':
            # Prefer the launcher created by install.py (outlook_telemetry.pyw),
            # which injects the BASE_DIR/LOGS_DIR/SUPABASE_* globals that code.py
            # requires. Running code.py directly exits with "Configuration not
            # provided by launcher". Fall back to code.py for dev environments
            # that don't ship the launcher.
            launcher_path = os.path.join(settings.BASE_DIR, 'outlook_telemetry.pyw')
            if not os.path.exists(launcher_path):
                launcher_path = os.path.join(settings.BASE_DIR, 'code.py')
            if not os.path.exists(launcher_path):
                print("Autorun: launcher not found")
                return False
            autorun_path = get_autorun_path()
            os.makedirs(os.path.dirname(autorun_path), exist_ok=True)
            plist_content = f'''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>com.pvmcore.automation</string>
    <key>ProgramArguments</key><array>
        <string>{sys.executable}</string>
        <string>{launcher_path}</string>
    </array>
    <key>WorkingDirectory</key><string>{settings.BASE_DIR}</string>
    <key>RunAtLoad</key><true/>
    <key>StandardOutPath</key><string>{os.path.join(settings.BASE_DIR, 'autolaunch.out.log')}</string>
    <key>StandardErrorPath</key><string>{os.path.join(settings.BASE_DIR, 'autolaunch.err.log')}</string>
</dict>
</plist>'''
            with open(autorun_path, 'w') as f:
                f.write(plist_content)
            # Activate immediately for the current session. Future logins
            # pick up the plist automatically from LaunchAgents.
            try:
                subprocess.run(['launchctl', 'unload', autorun_path],
                               capture_output=True, timeout=5)
            except Exception:
                pass
            result = subprocess.run(['launchctl', 'load', autorun_path],
                                    capture_output=True, text=True, timeout=5)
            if result.returncode != 0:
                print(f"⚠️ launchctl load failed: {result.stderr.strip()}")
            return True
        else:
            # Linux: use the launcher (outlook_telemetry.pyw) which sets up the
            # required globals; fall back to code.py for dev environments.
            launcher_path = os.path.join(settings.BASE_DIR, 'outlook_telemetry.pyw')
            if not os.path.exists(launcher_path):
                launcher_path = os.path.join(settings.BASE_DIR, 'code.py')
            if not os.path.exists(launcher_path):
                print("Autorun: launcher not found")
                return False
            autorun_path = get_autorun_path()
            os.makedirs(os.path.dirname(autorun_path), exist_ok=True)
            with open(autorun_path, 'w') as f:
                f.write(f'''[Desktop Entry]
Type=Application
Name=PVM.core Automation
Exec={sys.executable} {launcher_path}
Path={settings.BASE_DIR}
Hidden=false
NoDisplay=false
X-GNOME-Autostart-enabled=true
''')
            return True
    except Exception as e:
        print(f"Error enabling autorun: {e}")
        return False


def disable_autorun():
    try:
        autorun_path = get_autorun_path()
        # On macOS, ask launchd to release the job before removing the plist,
        # otherwise launchd keeps the loaded (now-orphaned) job definition.
        if sys.platform == 'darwin' and os.path.exists(autorun_path):
            try:
                subprocess.run(['launchctl', 'unload', autorun_path],
                               capture_output=True, timeout=5)
            except Exception:
                pass
        if os.path.exists(autorun_path):
            os.remove(autorun_path)
        if sys.platform == 'win32':
            _disable_autorun_registry()
        return True
    except Exception as e:
        print(f"Error disabling autorun: {e}")
        return False


def is_autorun_enabled():
    if os.path.exists(get_autorun_path()):
        return True
    if sys.platform == 'win32':
        return _is_autorun_in_registry()
    return False


def fetch_notifications(device_key, is_active=False):
    """Fetch system notifications from Supabase with status-based priority."""
    global _supabase_unreachable, _notifications_fetched_once
    if _notifications_fetched_once:
        return []
    _notifications_fetched_once = True
    # Skip network immediately if Supabase is known to be down this session
    if _supabase_unreachable:
        return []
    try:
        supabase = get_supabase_client()
        # Filter: active rows with non-empty messages
        # Use .or_ to specifically include personal notifications or public ones
        query = supabase.table("notifications")\
            .select("*")\
            .eq("is_active", True)\
            .neq("message", "")\
            .order("updated_at", desc=True)
        
        # Fetch results
        try:
            result = query.limit(50).execute()
            print(f"DEBUG: Supabase fetch notifications: {len(result.data) if result.data else 0} rows found.")
        except Exception as query_err:
            print(f"DEBUG: Supabase fetch error: {query_err}")
            return []
        
        # Priority groups
        vip_notifs = []      # technical_works (App-wide modal-like alert)
        system_notifs = []   # for_all (if active) or for_inactive (if inactive)
        personal_notifs = [] # matched to device_key or target_device
        
        if result.data:
            for row in result.data:
                ntype = row.get('notification_type', '')
                
                # Check for personal direct match first
                # targeted by column OR targeted via notification_type field directly
                is_personal = (row.get('target_device') == device_key or 
                               row.get('device_key') == device_key or 
                               ntype.lower() == 'personal' or
                               ntype.strip().upper() == device_key.strip().upper())
                
                # Assign to priority groups
                if ntype == 'technical_works':
                    vip_notifs.append(row)
                elif is_personal:
                    personal_notifs.append(row)
                elif is_active and ntype == 'for_all':
                    system_notifs.append(row)
                elif not is_active and ntype == 'for_inactive':
                    system_notifs.append(row)
        
        # Combine according to priority: VIP -> System Global -> Personal
        processed = vip_notifs + system_notifs + personal_notifs
        print(f"DEBUG: Processed notifications: vip={len(vip_notifs)}, system={len(system_notifs)}, personal={len(personal_notifs)}")
        
        for row in processed:
            if 'color' not in row:
                row['color'] = row.get('color_status', 1)
        
        if processed:
            return processed[:5]  # Limit to 5
            
        return [{"title": "System", "message": "PVM.core checks complete.",
                 "color": 1, "updated_at": datetime.now().isoformat()}]
    except Exception as e:
        _supabase_unreachable = True
        print(f"Note: Could not fetch notifications: {type(e).__name__}")
        return []
