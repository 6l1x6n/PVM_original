# -*- coding: utf-8 -*-
"""
PVM.core Smart Bridge Entry Point
=================================
AI ASSISTANT INSTRUCTIONS:
1. This file handles module loading and version checking.
2. Updates are now MANUAL: if `version.json` on Supabase has a newer version, 
   this script flags it via `UPDATE_AVAILABLE = True`.
3. The UI in `ui_settings.py` handles the physical download of new modules.
4. DO NOT change the automatic loading logic back to 'force download' unless 
   explicitly requested (to save user bandwidth).
"""

import os
import sys
import types
import tempfile
import threading
import hashlib
import json
import tkinter as tk
from tkinter import messagebox
from datetime import datetime

# =============================================================================
# _u8 - ASCII-safe UTF-8 string helper (survives latin-1 launcher decode)
# =============================================================================
def _u8(hex_str):
    """Decode hex-encoded UTF-8 text. Source stays pure ASCII so it works
    even when an old launcher decodes this file as ISO-8859-1."""
    return bytes.fromhex(hex_str).decode('utf-8')

# =============================================================================
# CLOG — step logger (placeholder, no file output)
# =============================================================================
def _cl(msg):
    pass

# =============================================================================
# CONFIGURATION (Inherited from launcher)
# =============================================================================
BASE_DIR = globals().get('BASE_DIR')
LOGS_DIR = globals().get('LOGS_DIR')
SUPABASE_URL = globals().get('SUPABASE_URL')
SUPABASE_KEY = globals().get('SUPABASE_KEY')

_ext_load_settings = globals().get('_load_settings')
_ext_save_settings = globals().get('_save_settings')
_ext_get_device_key = globals().get('_get_device_key')

# New: Fernet key loader
_get_fernet_key = globals().get('_get_fernet_key')
_OFFLINE_MODE = globals().get('_OFFLINE_MODE', False)

if not all([BASE_DIR, LOGS_DIR, SUPABASE_URL, SUPABASE_KEY]):
    messagebox.showerror("Error", "Configuration not provided by launcher")
    sys.exit(1)

# =============================================================================
# MODULE CONFIGURATION
# =============================================================================
BUCKET_NAME = "backend"
MODULE_VERSION = globals().get("MODULE_VERSION", "3.9.50")

# Global state for UI
UPDATE_AVAILABLE = False
LATEST_VERSION_INFO = None

MODULES_TO_LOAD = [
    'settings.py',
    'db_sqlite.py',
    'db.py',
    'market.py',
    'receipt_printer.py',
    'pvm_core.py',
    'ui_lang.py',
    'ui_dialogs.py',
    'ui_sales.py',
    'ui_pos.py',
    'ui_arrival.py',
    'ui_partners.py',
    'ui_main_tab.py',
    'ui_analytics.py',
    'ui_bizanalytics.py',
    'ui_autoreview.py',
    'ui_settings.py',
    'ui_bot.py',
    'ui.py',
    'sync_transport.py',
    'transport_local.py',
    'sync_queue.py',
    'sync_registry.py',
    'sync_engine.py',
    'sync_setup_wizard.py',
]

# Version metadata cache path
VERSION_CACHE_PATH = os.path.join(BASE_DIR, 'cache', '_vrs.bin') if BASE_DIR else None


# =============================================================================
# FERNET ENCRYPTION HELPERS
# =============================================================================
_fernet_instance = None

def _get_fernet():
    """Get or create Fernet instance."""
    global _fernet_instance
    if _fernet_instance is None:
        if _get_fernet_key:
            key = _get_fernet_key()
            if key:
                from cryptography.fernet import Fernet
                _fernet_instance = Fernet(key)
    return _fernet_instance


def _cache_filename(module_name):
    """Generate obfuscated cache filename from module name."""
    h = hashlib.sha256(module_name.encode()).hexdigest()[:12]
    return f"{h}.dat"


def _encrypt_to_cache_ex(cache_path, source_code):
    """Encrypt module source and write to specific cache path.

    Staged activation: content is written to a temp file and atomically
    renamed, so a crash mid-write can never leave a truncated cache file
    (which would fail hash verification on the next boot)."""
    fernet = _get_fernet()
    if not fernet:
        return False
    try:
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        encrypted = fernet.encrypt(source_code.encode('utf-8'))
        payload = os.urandom(4) + len(encrypted).to_bytes(4, 'little') + encrypted
        fd, tmp_path = tempfile.mkstemp(
            prefix='.pvm_tmp_', dir=os.path.dirname(cache_path))
        try:
            with os.fdopen(fd, 'wb') as f:
                f.write(payload)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, cache_path)
        except Exception:
            try:
                os.remove(tmp_path)
            except Exception:
                pass
            raise
        return True
    except Exception as e:
        print(f"  Cache write error: {e}")
        return False


def _decrypt_from_cache_ex(cache_path):
    """Read and decrypt module source from specific cache path."""
    fernet = _get_fernet()
    if not fernet or not os.path.exists(cache_path):
        return None
    try:
        with open(cache_path, 'rb') as f:
            f.read(4)  # skip fake header
            data_len = int.from_bytes(f.read(4), 'little')
            encrypted = f.read(data_len)
        return fernet.decrypt(encrypted).decode('utf-8')
    except Exception as e:
        print(f"  Cache read error: {e}")
        return None


# =============================================================================
# VERSION.JSON MANAGEMENT
# =============================================================================
def download_version_json(local_version=None):
    """Download version.json from Supabase Storage with ETag support."""
    import requests
    url = f"{SUPABASE_URL}/storage/v1/object/public/{BUCKET_NAME}/version.json"
    print(f"  Downloading version.json...", end=" ", flush=True)
    try:
        headers = {}
        if local_version and 'etag' in local_version:
            headers['If-None-Match'] = local_version['etag']
            
        response = requests.get(url, headers=headers, timeout=30)
        
        if response.status_code == 304:
            print(f"OK (Not Modified 304, version {local_version.get('version', 'unknown')})")
            return local_version
            
        if response.status_code == 200:
            data = response.json()
            if 'ETag' in response.headers:
                data['etag'] = response.headers['ETag'].strip('"')
            v = data.get('version', 'unknown')
            print(f"OK (version {v})")
            # Proactively update local MODULE_VERSION if we got a valid one
            if v != 'unknown':
                global MODULE_VERSION
                MODULE_VERSION = v
            return data
        else:
            print(f"FAIL HTTP {response.status_code}")
            return None
    except Exception as e:
        print(f"FAIL {e}")
        return None


def load_local_version_json():
    """Load local version.json from encrypted cache."""
    if not VERSION_CACHE_PATH or not os.path.exists(VERSION_CACHE_PATH):
        return None
    
    fernet = _get_fernet()
    if not fernet:
        return None
    
    try:
        with open(VERSION_CACHE_PATH, 'rb') as f:
            f.read(4)  # skip fake header
            data_len = int.from_bytes(f.read(4), 'little')
            encrypted = f.read(data_len)
        decrypted = fernet.decrypt(encrypted).decode('utf-8')
        return json.loads(decrypted)
    except Exception as e:
        print(f"  Warning: Could not load local version cache: {e}")
        return None


def save_local_version_json(version_data):
    """Save version.json to encrypted local cache."""
    if not VERSION_CACHE_PATH:
        return False
    
    fernet = _get_fernet()
    if not fernet:
        return False
    
    try:
        os.makedirs(os.path.dirname(VERSION_CACHE_PATH), exist_ok=True)
        json_str = json.dumps(version_data, ensure_ascii=False)
        encrypted = fernet.encrypt(json_str.encode('utf-8'))
        
        with open(VERSION_CACHE_PATH, 'wb') as f:
            f.write(os.urandom(4))  # fake header
            f.write(len(encrypted).to_bytes(4, 'little'))
            f.write(encrypted)
        return True
    except Exception as e:
        print(f"  Warning: Could not save version cache: {e}")
        return False


def expand_env_path(path_template):
    """Expand environment variables in path template. Handles Windows and macOS/Linux."""
    expanded = path_template
    
    if sys.platform == 'darwin':
        # macOS specific expansion
        home = os.path.expanduser('~')
        app_support = os.path.join(home, 'Library', 'Application Support')
        expanded = expanded.replace('%LOCALAPPDATA%', app_support)
        expanded = expanded.replace('%APPDATA%', app_support)
        expanded = expanded.replace('%PROGRAMDATA%', '/Library/Application Support')
        expanded = expanded.replace('%USERPROFILE%', home)
        # Normalize backslashes from Windows-style templates
        expanded = expanded.replace('\\', '/')
    else:
        # Standard Windows replacement
        expanded = expanded.replace('%LOCALAPPDATA%', os.environ.get('LOCALAPPDATA', ''))
        expanded = expanded.replace('%PROGRAMDATA%', os.environ.get('PROGRAMDATA', ''))
        expanded = expanded.replace('%APPDATA%', os.environ.get('APPDATA', ''))
        expanded = expanded.replace('%USERPROFILE%', os.environ.get('USERPROFILE', ''))
    
    return expanded


# =============================================================================
# IN-MEMORY MODULE CREATION
# =============================================================================
def create_module_in_memory(module_name, source_code, shared_globals=None):
    """
    Execute source code and register as a proper Python module in sys.modules.
    No files are written to disk.
    """
    clean_name = module_name.replace('.py', '')
    mod = types.ModuleType(clean_name)
    mod.__file__ = f'<memory:{module_name}>'
    mod.__loader__ = None
    mod.__spec__ = None

    # Pre-populate module namespace with shared globals if needed
    if shared_globals:
        for k, v in shared_globals.items():
            setattr(mod, k, v)

    # Execute source in module's namespace
    exec(compile(source_code, f'<memory:{module_name}>', 'exec'), mod.__dict__)

    # Register in sys.modules so `import X` works
    sys.modules[clean_name] = mod
    return mod


# =============================================================================
# DOWNLOAD MODULE FROM SUPABASE
# =============================================================================
def download_module(module_name):
    """Download a single module from Supabase storage."""
    import requests
    url = f"{SUPABASE_URL}/storage/v1/object/public/{BUCKET_NAME}/{module_name}"
    print(f"  Downloading {module_name}...", end=" ", flush=True)
    try:
        response = requests.get(url, timeout=30)
        if response.status_code == 200:
            content = response.content.decode('utf-8')
            print(f"OK ({len(content)} bytes)")
            return content
        else:
            print(f"FAIL HTTP {response.status_code}")
            return None
    except Exception as e:
        print(f"FAIL {e}")
        return None


# =============================================================================
# UPDATE APP ICON FROM CLOUD
# =============================================================================
def _update_app_icon(version_data):
    """Check if app.ico was updated in the cloud and download if needed."""
    remote_icon_info = version_data.get('icon')
    if not remote_icon_info:
        return

    remote_hash = remote_icon_info.get('hash')
    if not remote_hash:
        return

    icon_path = os.path.join(BASE_DIR, 'app.ico')

    local_hash = None
    if os.path.exists(icon_path):
        import hashlib
        with open(icon_path, 'rb') as f:
            local_hash = hashlib.sha256(f.read()).hexdigest()

    if local_hash == remote_hash:
        return

    print(f"Updating app.ico (hash changed)...")
    import requests
    url = f"{SUPABASE_URL}/storage/v1/object/public/{BUCKET_NAME}/app.ico"
    try:
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            with open(icon_path, 'wb') as f:
                f.write(resp.content)
            print(f"  Updated app.ico ({len(resp.content)} bytes)")
            if sys.platform == 'win32':
                try:
                    import subprocess
                    subprocess.run(['ie4uinit.exe', '-show'],
                                 capture_output=True, timeout=5)
                except:
                    pass
        else:
            print(f"  Icon download failed: HTTP {resp.status_code}")
    except Exception as e:
        print(f"  Icon update failed: {e}")


# =============================================================================
# CONFIGURE SETTINGS (called between settings.py and other modules exec)
# =============================================================================
def _configure_settings():
    """Configure settings module with launcher-provided values."""
    import settings
    settings.BASE_DIR = BASE_DIR
    settings.LOGS_DIR = LOGS_DIR
    settings.SUPABASE_URL = SUPABASE_URL
    settings.SUPABASE_KEY = SUPABASE_KEY
    settings._ext_load_settings = _ext_load_settings
    settings._ext_save_settings = _ext_save_settings
    settings._ext_get_device_key = _ext_get_device_key
    settings.SETTINGS_PATH = os.path.join(BASE_DIR, "cache", "_cfg.bin")
    settings.DATA_DIR = os.path.join(BASE_DIR, "data")
    settings.GOODS_DIR = os.path.join(settings.DATA_DIR, "goods")
    settings.PARTNERS_DIR = os.path.join(settings.DATA_DIR, "partners")
    settings.RECEIPTS_DIR = os.path.join(settings.DATA_DIR, "receipts")
    settings.PURCHASES_DIR = os.path.join(settings.DATA_DIR, "purchases")
    settings.CONFIG_DIR = os.path.join(settings.DATA_DIR, "config")
    settings.init_directories()


# =============================================================================
# LOAD ALL MODULES WITH VERSION CHECKING
# =============================================================================
def load_modules():
    """Load all modules into memory with smart version checking."""
    global MODULE_VERSION, UPDATE_AVAILABLE, LATEST_VERSION_INFO
    
    print("=" * 70)
    print(f"PVM.core v{MODULE_VERSION} - Initializing...")
    print("=" * 70)
    
    # Step 1: Load local version cache
    local_version = load_local_version_json()
    if local_version and local_version.get('version', 'unknown') != 'unknown':
        MODULE_VERSION = local_version['version']

    # Step 2: Download fresh version.json
    print("Checking for updates in cloud...")
    remote_version = download_version_json(local_version)
    
    if remote_version:
        LATEST_VERSION_INFO = remote_version
        current_version = remote_version # Metadata for hash comparison
    else:
        print(_u8("20e29aa0efb88f20436f756c64206e6f7420636865636b20666f72207570646174657320284f66666c696e65206d6f6465292e"))
        if not local_version:
             raise Exception("Internet connection required for first run")
        current_version = local_version
        
    print(f"[{datetime.now().strftime('%H:%M:%S')}] PVM.core v{current_version.get('version', '?.?.?')}")

    # Step 3: Load modules from cache ONLY
    # If a module is missing or corrupted, we download it, but we don't 
    # force updates here anymore to allow the user to trigger it manually.
    
    cache_paths = current_version.get('cache_paths', {})
    modules_info = current_version.get('modules', {})
    
    print(f"\nLoading {len(MODULES_TO_LOAD)} modules from local cache:")
    print("-" * 70)
    
    modules_source = {}
    
    for module_name in MODULES_TO_LOAD:
        cache_dir_template = cache_paths.get(module_name)
        cache_dir = expand_env_path(cache_dir_template) if cache_dir_template else os.path.join(BASE_DIR, 'cache')
        cache_path = os.path.join(cache_dir, _cache_filename(module_name))
        
        module_info = modules_info.get(module_name, {})
        target_hash = module_info.get('hash')
        
        # Try to load from cache
        source = _decrypt_from_cache_ex(cache_path)
        
        # Selective Update Check
        remote_hash = module_info.get('hash')
        current_local_hash = hashlib.sha256(source.encode('utf-8')).hexdigest() if source else None
        
        should_update = False
        if _OFFLINE_MODE:
             should_update = False
        elif not source:
             should_update = True
        elif remote_hash and current_local_hash != remote_hash:
             should_update = True

        if should_update:
            msg = "Missing from cache" if not source else "New version found"
            print(f"{_u8('2020f09f93a520')}{module_name}: {msg}, downloading...")
            source = download_module(module_name)
            if not source:
                 raise Exception(f"Failed to load {module_name}")
            _encrypt_to_cache_ex(cache_path, source)
        
        modules_source[module_name] = source
        print(f"{_u8('2020e29c8520')}{module_name}")
    
    print("-" * 70)
    print(f"All {len(modules_source)} modules loaded")
    print()
    
    # Step 6: Execute modules in memory
    print("Initializing modules in memory...")
    
    # Shared globals injected into every module's namespace BEFORE exec().
    # This ensures MODULE_VERSION from version.json propagates correctly
    # instead of each module falling back to its hardcoded default.
    shared = {
        "MODULE_VERSION": MODULE_VERSION,
        "UPDATE_AVAILABLE": UPDATE_AVAILABLE,
        "LATEST_VERSION_INFO": LATEST_VERSION_INFO,
        "download_module": download_module,
        "_cache_filename": _cache_filename,
        "_encrypt_to_cache_ex": _encrypt_to_cache_ex,
        "expand_env_path": expand_env_path,
        "save_local_version_json": save_local_version_json,
        "MODULES_TO_LOAD": MODULES_TO_LOAD,
        "_cl": _cl
    }
    
    # Load settings first (other modules depend on it)
    try:
        create_module_in_memory('settings.py', modules_source['settings.py'], shared_globals=shared)
        _configure_settings()
        print(_u8("2020e29c852073657474696e67732e707920636f6e66696775726564"))
    except Exception as e:
        print(f"{_u8('2020e29d8c')} settings.py: FAILED - {e}")
        messagebox.showerror("Critical Error", f"Failed to initialize settings:\n{e}")
        return None
    
    # Load remaining modules
    for module_name in MODULES_TO_LOAD:
        if module_name == 'settings.py':
            continue  # Already loaded
        
        try:
            create_module_in_memory(module_name, modules_source[module_name], shared_globals=shared)
            print(f"{_u8('2020e29c85')} {module_name}")
        except Exception as e:
            print(f"{_u8('2020e29d8c')} {module_name}: FAILED - {e}")
            friendly = _friendly_net_error(e)
            if friendly:
                messagebox.showerror(_u8("d09dd0b5d18220d0bfd0bed0b4d0bad0bbd18ed187d0b5d0bdd0b8d18f20d0ba20d0b8d0bdd182d0b5d180d0bdd0b5d182d183"),
                                     f"{friendly}\n\n{_u8('d09ad0bed0bcd0bfd0bed0bdd0b5d0bdd1823a20')}{module_name}")
            else:
                messagebox.showerror("Module Error", f"Failed to initialize {module_name}:\n{e}")
            return None
    
    print("=" * 70)
    print(_u8("e29c8520416c6c206d6f64756c657320696e697469616c697a6564207375636365737366756c6c79"))
    print("=" * 70)
    print()
    
    return True


# =============================================================================
# DEPENDENCY CHECK
# =============================================================================
REQUIRED_PACKAGES = {
    'pandas': 'pandas',
    'supabase': 'supabase',
    'playwright': 'playwright',
    'requests': 'requests',
    'tkcalendar': 'tkcalendar',
    'cryptography': 'cryptography',
    'pydantic': 'pydantic',
    'PIL': 'Pillow',
    'qrcode': 'qrcode',
}

# pywin32 is Windows-only (provides win32print for receipt printing via USB on Windows)
if sys.platform == 'win32':
    REQUIRED_PACKAGES['win32print'] = 'pywin32'


def check_and_install_dependencies():
    """Check for required packages and install missing ones."""
    missing = []
    for module_name, pip_name in REQUIRED_PACKAGES.items():
        try:
            __import__(module_name)
        except ImportError:
            missing.append(pip_name)

    if missing:
        print(f"Missing packages: {', '.join(missing)}")
        import subprocess
        for package in missing:
            try:
                print(f"  Installing {package}...")
                # Hide console window on Windows when installing dependencies
                kwargs = {}
                if sys.platform == "win32":
                    startupinfo = subprocess.STARTUPINFO()
                    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                    kwargs["startupinfo"] = startupinfo
                    kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)

                subprocess.check_call(
                    [sys.executable, "-m", "pip", "install", package, "-q"],
                    **kwargs,
                )
                print(f"  {package} installed")
            except subprocess.CalledProcessError as e:
                print(f"  Failed to install {package}: {e}")
                print(f"\nPlease manually install: pip install {' '.join(missing)}")
                input("Press Enter to exit...")
                sys.exit(1)


check_and_install_dependencies()

# =============================================================================
# LOAD MODULES INTO MEMORY (settings configured inside load_modules)
# =============================================================================
if not load_modules():
    sys.exit(1)

print(f"Configuration loaded:")
print(f"   BASE_DIR: {BASE_DIR}")
print(f"   LOGS_DIR: {LOGS_DIR}")
print(f"   SUPABASE_URL: {SUPABASE_URL[:30]}...")
print(f"   Mode: {'OFFLINE' if _OFFLINE_MODE else 'ONLINE'}")
print()

# =============================================================================
# REFERENCE MODULES (already in sys.modules from load_modules)
# =============================================================================
import settings
import db_sqlite
import db
import pvm_core
import ui

print("All modules configured successfully\n")

# =============================================================================
# ENSURE SQLITE DATABASE EXISTS
# =============================================================================
def _show_already_running():
    """Notify that the program is already running (Unicode-safe, no mojibake)."""
    title = "PVM.core"
    message = _u8("d09fd180d0bed0b3d180d0b0d0bcd0bcd0b020d183d0b6d0b520d0b7d0b0d0bfd183d189d0b5d0bdd0b021")
    if sys.platform == 'win32':
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, message, title, 0x40 | 0x1000)
            return
        except Exception:
            pass
    try:
        messagebox.showinfo(title, message)
    except Exception:
        pass


_lock_file = None


def _acquire_single_instance_lock():
    """Acquire the single-instance lock. Returns True on success.

    MUST run before any database/UI work: a second instance or a
    concurrent migration must never touch the same SQLite file
    (parallel writers can corrupt or lose data)."""
    global _lock_file
    lock_file_path = os.path.join(tempfile.gettempdir(), 'pvmcore.lock')
    _cl("lock path: " + lock_file_path)
    if sys.platform == 'win32':
        try:
            import msvcrt
            _lock_file = open(lock_file_path, 'w')
            msvcrt.locking(_lock_file.fileno(), msvcrt.LK_NBLCK, 1)
        except (IOError, OSError, ImportError):
            _show_already_running()
            return False
    else:
        try:
            import fcntl
            _lock_file = open(lock_file_path, 'w')
            fcntl.flock(_lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (IOError, OSError, ImportError):
            _show_already_running()
            return False
    _cl("lock acquired")
    return True


def ensure_database():
    """Create SQLite database if it doesn't exist.

    DatabaseManager never deletes a corrupt DB anymore: on failure it
    creates a backup copy and raises RuntimeError, and we exit with a
    clear message instead of silently destroying the data."""
    db_path = os.path.join(BASE_DIR, 'cache', 'pvmcore.db')
    try:
        db_sqlite.DatabaseManager(db_path)
    except RuntimeError as e:
        print(f"CRITICAL DB ERROR: {e}")
        messagebox.showerror("Ошибка базы данных", str(e))
        sys.exit(1)
    print("SQLite database ready")


# Single-instance lock FIRST (before DB init, UI, or any worker thread)
if not _acquire_single_instance_lock():
    sys.exit(0)

ensure_database()


def _friendly_net_error(e):
    """Return a friendly offline message for network errors, else None."""
    es = str(e).lower()
    if any(k in es for k in [
        'connection', 'resolve', 'getaddrinfo', 'nodename',
        'max retries', 'unreachable', 'timed out', 'failed to load',
    ]):
        return (_u8("d09dd0b520d183d0b4d0b0d0bbd0bed181d18c20d183d181d182d0b0d0bdd0bed0b2d0b8d182d18c20d181d0bed0b5d0b4d0b8d0bdd0b5d0bdd0b8d0b520d18120d181d0b5d180d0b2d0b5d180d0bed0bc2e0a") +
                _u8("d09fd180d0bed0b2d0b5d180d18cd182d0b520d0b8d0bdd182d0b5d180d0bdd0b5d1822dd181d0bed0b5d0b4d0b8d0bdd0b5d0bdd0b8d0b520d0b820d0b7d0b0d0bfd183d181d182d0b8d182d0b520d0bfd180d0b8d0bbd0bed0b6d0b5d0bdd0b8d0b520d0b5d189d19120d180d0b0d0b72e"))
    return None


# =============================================================================
# ENTRY POINT
# =============================================================================
def run_application():
    """Main entry point called by the static launcher."""
    _cl("run_application entered")
    # Single-instance lock was acquired before ensure_database() at module
    # level — see _acquire_single_instance_lock().

    # Update app icon if cloud version changed
    _cl("before update icon")
    _update_app_icon(LATEST_VERSION_INFO or {})
    _cl("icon done")

    print("=" * 50)
    print(f"PVM.core v{MODULE_VERSION} - Initializing...")
    print("=" * 50)

    _cl("before device key")
    device_key = db.get_or_create_device_key()
    if not device_key:
        messagebox.showerror("Error", "Could not initialize device key.")
        return
    _cl("device key: " + str(device_key))

    print(f"Device Key: {device_key}")
    print("Checking license status...")

    # New License Check Logic (Daily + Sync support)
    _cl("before license check")
    is_active, status_message, _max_devices, activation_end, subscription_level = db.check_license_in_supabase(device_key)
    _cl("license check done: " + str(is_active))

    if is_active is False:
        print(f"License Status: {status_message}")
        # If not active, show waiting screen or error
        waiting = ui.WaitingScreen(device_key, status_message)
        activated, subscription_level = waiting.show()
        if not activated:
            print("Application closed without activation.")
            return
        # If activated via waiting screen, update local settings with HMAC signature
        now_iso = datetime.now().isoformat()
        sig = settings.generate_license_signature(device_key, now_iso, subscription_level)
        settings.update_sync_settings(
            last_license_check=now_iso,
            license_active=True,
            subscription_level=subscription_level,
            license_signature=sig
        )
        settings.save_protected_license_vault(device_key, now_iso, sig, subscription_level)
    
    elif is_active is None:
        # Offline mode with HMAC grace period & hardware binding
        sync_meta = settings.get_sync_settings()
        last_check = sync_meta.get('last_license_check')
        signature = sync_meta.get('license_signature')
        sub_level = sync_meta.get('subscription_level', 4)

        vault = settings.load_protected_license_vault()
        
        # If settings file tampered or missing signature, check vault fallback
        if not last_check or not signature:
            if vault and vault.get('last_check') and vault.get('signature'):
                last_check = vault.get('last_check')
                signature = vault.get('signature')
                sub_level = vault.get('subscription_level', 4)

        if last_check and signature:
            # 1. HARDWARE & HMAC SIGNATURE VALIDATION
            if not settings.verify_license_signature(device_key, last_check, signature, sub_level):
                messagebox.showerror(_u8("d09ed188d0b8d0b1d0bad0b020d0b1d0b5d0b7d0bed0bfd0b0d181d0bdd0bed181d182d0b8"), 
                                   _u8("d09ed0b1d0bdd0b0d180d183d0b6d0b5d0bdd0b020d0bfd0bed0b4d0b4d0b5d0bbd0bad0b020d184d0b0d0b9d0bbd0b020d0bbd0b8d186d0b5d0bdd0b7d0b8d0b820d0b8d0bbd0b820d0b8d0b7d0bcd0b5d0bdd0b5d0bdd0b8d0b520d0bed0b1d0bed180d183d0b4d0bed0b2d0b0d0bdd0b8d18f2e0a") +
                                   _u8("d094d0bbd18f20d0bfd0bed0b4d0bbd0b8d0bdd0bdd0bed0b920d0bfd180d0bed0b2d0b5d180d0bad0b820d182d180d0b5d0b1d183d0b5d182d181d18f20d0b8d0bdd182d0b5d180d0bdd0b5d1822e"))
                return

            # 2. VAULT SYNC INTEGRITY CHECK
            if vault and vault.get('signature'):
                if vault.get('signature') != signature or vault.get('device_key') != device_key:
                    messagebox.showerror(_u8("d09ed188d0b8d0b1d0bad0b020d186d0b5d0bbd0bed181d182d0bdd0bed181d182d0b8"), 
                                       _u8("d09bd0bed0bad0b0d0bbd18cd0bdd18bd0b520d0b4d0b0d0bdd0bdd18bd0b520d0bbd0b8d186d0b5d0bdd0b7d0b8d0b820d180d0b0d181d185d0bed0b4d18fd182d181d18f20d18120d181d0b8d181d182d0b5d0bcd0bdd18bd0bc20d185d180d0b0d0bdd0b8d0bbd0b8d189d0b5d0bc2e0a") +
                                       _u8("d0a2d180d0b5d0b1d183d0b5d182d181d18f20d0bfd0bed0b4d0bad0bbd18ed187d0b5d0bdd0b8d0b520d0ba20d0b8d0bdd182d0b5d180d0bdd0b5d182d1832e"))
                    return

            # 3. TIME INTERVAL & ANTI-TAMPER CHECK
            try:
                check_dt = datetime.fromisoformat(last_check)
                hours_since = (datetime.now() - check_dt).total_seconds() / 3600
                if hours_since > 72:  # 3 days grace period
                    messagebox.showerror(_u8("d0a2d180d0b5d0b1d183d0b5d182d181d18f20d0b8d0bdd182d0b5d180d0bdd0b5d182"), 
                                       f"{_u8('d09bd0b8d186d0b5d0bdd0b7d0b8d18f20d0bdd0b520d0bfd180d0bed0b2d0b5d180d18fd0bbd0b0d181d18c20d0b1d0bed0bbd0b5d0b520373220d187d0b0d181d0bed0b22028')}{int(hours_since)}{_u8('d187292e0a')}{_u8('d09fd0bed0b6d0b0d0bbd183d0b9d181d182d0b02c20d0bfd0bed0b4d0bad0bbd18ed187d0b8d182d0b5d181d18c20d0ba20d0b8d0bdd182d0b5d180d0bdd0b5d182d1832e')}")
                    return
                # ANTI-TAMPER: Clock moved BACKWARD relative to last check
                if hours_since < -0.1:
                    messagebox.showerror(_u8("d09ed188d0b8d0b1d0bad0b020d0b2d180d0b5d0bcd0b5d0bdd0b8"), 
                                       _u8("d09ed0b1d0bdd0b0d180d183d0b6d0b5d0bdd0be20d0b8d0b7d0bcd0b5d0bdd0b5d0bdd0b8d0b520d181d0b8d181d182d0b5d0bcd0bdd0bed0b3d0be20d0b2d180d0b5d0bcd0b5d0bdd0b820d0bdd0b0d0b7d0b0d0b42e0a") +
                                       _u8("d094d0bbd18f20d0bfd180d0bed0b4d0bed0bbd0b6d0b5d0bdd0b8d18f20d180d0b0d0b1d0bed182d18b20d182d180d0b5d0b1d183d0b5d182d181d18f20d0bfd0bed0b4d0bad0bbd18ed187d0b5d0bdd0b8d0b520d0ba20d0b8d0bdd182d0b5d180d0bdd0b5d182d1832e"))
                    return
                print(f"Offline mode: Verified HMAC license ({int(hours_since)}h since last check)")
            except Exception as e:
                messagebox.showerror(_u8("d09ed188d0b8d0b1d0bad0b0"), _u8("d09dd0b5d0bad0bed180d180d0b5d0bad182d0bdd18bd0b520d0b4d0b0d0bdd0bdd18bd0b520d0bfd0bed181d0bbd0b5d0b4d0bdd0b5d0b920d0bfd180d0bed0b2d0b5d180d0bad0b820d0bbd0b8d186d0b5d0bdd0b7d0b8d0b82e20d0a2d180d0b5d0b1d183d0b5d182d181d18f20d0b8d0bdd182d0b5d180d0bdd0b5d1822e"))
                return
        else:
            messagebox.showerror(_u8("d0a2d180d0b5d0b1d183d0b5d182d181d18f20d0b8d0bdd182d0b5d180d0bdd0b5d182"), _u8("d0a2d180d0b5d0b1d183d0b5d182d181d18f20d0bfd0b5d180d0b2d0b8d187d0bdd0b0d18f20d0bfd180d0bed0b2d0b5d180d0bad0b020d0bbd0b8d186d0b5d0bdd0b7d0b8d0b820d187d0b5d180d0b5d0b720d0b8d0bdd182d0b5d180d0bdd0b5d1822e"))
            return
    
    else:
        # License active ✅
        print(f"License Status: {status_message}")
        now_iso = datetime.now().isoformat()
        sig = settings.generate_license_signature(device_key, now_iso, subscription_level)
        settings.update_sync_settings(
            last_license_check=now_iso,
            license_active=True,
            subscription_level=subscription_level,
            license_signature=sig
        )
        settings.save_protected_license_vault(device_key, now_iso, sig, subscription_level)

    _cl("before credentials")
    login, password = db.get_credentials_from_supabase(device_key)
    _cl("credentials: " + str(login is not None))

    if not login or not password:
        messagebox.showerror("Error", "Credentials not found. Please contact administrator.")
        return

    print(f"Credentials loaded for: {login}")

    # === USER AUTHENTICATION SYSTEM ===
    _cl("before db init")
    db_path = os.path.join(BASE_DIR, 'cache', 'pvmcore.db')
    try:
        users_mgr = db_sqlite.UsersManagerSQL(db_sqlite.DatabaseManager(db_path))
    except RuntimeError as e:
        messagebox.showerror("Ошибка базы данных", str(e))
        return
    _cl("db init done")
    
    # Ensure exactly one superadmin exists (the first-created user).
    # Migrates pre-superadmin databases by promoting the oldest admin.
    try:
        users_mgr.ensure_superadmin()
    except Exception as _e:
        print(f"ensure_superadmin warning: {_e}")

    def process_queue_background():
        try:
            queue = db.load_upload_queue()
            if queue:
                print(f"Background: Processing {len(queue)} queued sessions...")
                uploaded, remaining = db.process_upload_queue()
                if uploaded > 0:
                    print(f"Background: Uploaded {uploaded} previously queued sessions")
        except Exception as e:
            print(f"Note: Background queue processing failed: {e}")

    # Session loop: login screen → main app. When the user requests to switch
    # user, the app closes and the login screen is shown again.
    switch_requested = True
    while switch_requested:
        switch_requested = False

        # First run after activation: create superadmin user
        if not users_mgr.has_any_users():
            print("First run: Admin setup wizard...")
            _cl("admin setup wizard")
            wizard = ui.AdminSetupWizard(db_path)
            admin_user = wizard.show()
            if not admin_user:
                print("Admin setup cancelled.")
                return
            print(f"Admin user created: {admin_user['display_name']}")
            current_user = admin_user
        else:
            # Show login screen
            print("Showing user login screen...")
            _cl("user login screen")
            login_screen = ui.UserLoginScreen(db_path)
            current_user = login_screen.show()
            if not current_user:
                print("Login cancelled.")
                return
            print(f"Logged in as: {current_user['display_name']} ({current_user['role']})")
        _cl("user auth done")

        print("Launching main application...")
        print("-" * 50)

        # Create root Tk window after authentication
        root = tk.Tk()
        _cl("tk.Tk() ok")

        _cl("before GreenLeafApp")
        try:
            app = ui.GreenLeafApp(root, login, password, current_user=current_user, subscription_level=subscription_level)
        except RuntimeError as e:
            try:
                root.destroy()
            except Exception:
                pass
            if "базы данных" in str(e) or "База данных" in str(e):
                messagebox.showerror("Ошибка базы данных", str(e))
                return
            raise
        _cl("GreenLeafApp created")

        # Run background tasks AFTER the window is created and visible
        def _post_launch_tasks():
            # Ensure Playwright browsers are ready (may take time on first install)
            # Gated for Level 3 and 4 (Bot subscription)
            if subscription_level in [3, 4]:
                try:
                    pvm_core.ensure_playwright_browsers()
                except Exception as e:
                    print(f"Note: Playwright browser check failed: {e}")
            
        # Start synchronization services
        # LAN/Cloud sync is retired in favor of Mega Sync: the SyncEngine is
        # started inside GreenLeafApp._init_sync_engine() when a sync folder is
        # configured (see settings.get_sync_settings).

        # Process queued uploads
        process_queue_background()

        bg_thread = threading.Thread(target=_post_launch_tasks, daemon=True)
        bg_thread.start()

        _cl("before mainloop")
        root.mainloop()

        print("Application closed by user.")
        # C3: stop every worker of the closed session (scheduler, bots,
        # autoreview, sync, live bot) before showing the next login screen —
        # otherwise the old user's processes keep running.
        try:
            app._stop_all_workers()
        except Exception:
            pass
        switch_requested = getattr(app, 'switch_user_requested', False)
        if switch_requested:
            print("Switching user...")


# =============================================================================
# AUTO-EXECUTE
# =============================================================================
if __name__ == '__main__':
    try:
        run_application()
    except Exception as e:
        friendly = _friendly_net_error(e)
        if friendly:
            messagebox.showerror(_u8("d09dd0b5d18220d0bfd0bed0b4d0bad0bbd18ed187d0b5d0bdd0b8d18f20d0ba20d0b8d0bdd182d0b5d180d0bdd0b5d182d183"), friendly)
        else:
            raise
