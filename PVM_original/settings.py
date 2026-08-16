# -*- coding: utf-8 -*-
"""
PVM.core v3.11.83 - Settings & Configuration
==========================================
All configuration, settings loading/saving, and device key management.
"""

import os
import sys
import json
import uuid
import hashlib
import hmac
import platform
from datetime import datetime
from typing import Any, Dict
import subprocess
import sys

# =============================================================================
# CONFIGURATION (Will be set by code.py after import)
# =============================================================================
BASE_DIR: str = ""
LOGS_DIR: str = ""
SUPABASE_URL: str = ""
SUPABASE_KEY: str = ""

# Sync configuration
SYNC_NAME: str = ""  # Custom name for this device, will be initialized as needed
DEBUG_SYNC: bool = False # Set to True to enable detailed synchronization logs in terminal

# Functions passed from launcher
_ext_load_settings: object = None
_ext_save_settings: object = None
_ext_get_device_key: object = None

# Hidden paths (will be updated by code.py)
SETTINGS_PATH: str = ""

# GreenLeaf URLs
PURCHASES_URL = "https://greenleaf-global.com/do.vshow#admin/shop/purchase"
PAYMENTS_URL = "https://greenleaf-global.com/do.vshow#admin/shop/payment"

# Notification Settings Defaults
DEFAULT_APPEARANCE_SETTINGS = {
    'toast_size': 1.0,               # Multiplier for toast size
    'toast_alpha': 0.95,             # Transparency (0.0 to 1.0)
    'toast_duration': 2500,          # ms
    'toast_position': 'top_center',  # top_center, top_right, etc.
    'toast_colors': {
        'success': {'bg': '#E8F5E9', 'fg': '#2E7D32', 'border': '#81C784'},
        'error': {'bg': '#FFEBEE', 'fg': '#C62828', 'border': '#EF9A9A'},
        'warning': {'bg': '#FFF3E0', 'fg': '#E65100', 'border': '#FFCC80'},
        'info': {'bg': '#E3F2FD', 'fg': '#1565C0', 'border': '#90CAF9'},
        'print_success': {'bg': '#E8F5E9', 'fg': '#2E7D32', 'border': '#81C784'},
        'print_error': {'bg': '#FFEBEE', 'fg': '#C62828', 'border': '#EF9A9A'},
        'sync_info': {'bg': '#E0F7FA', 'fg': '#00695C', 'border': '#80DEEA'},
        'bot_status': {'bg': '#F3E5F5', 'fg': '#6A1B9A', 'border': '#CE93D8'},
        'inventory': {'bg': '#FFF8E1', 'fg': '#F57F17', 'border': '#FFE082'},
        'sales': {'bg': '#E8EAF6', 'fg': '#283593', 'border': '#9FA8DA'}
    },
    'filtered_tabs': [],             # Tab IDs where notifications are HIDDEN
    'show_success_toast': True,
    'show_error_toast': True,
    'show_warning_toast': True,
    'show_info_toast': True,
    'show_print_success_toast': True,
    'show_print_error_toast': True,
    'show_sync_toast': True,
    'show_bot_toast': True,
    'show_inventory_toast': True,
    'show_sales_toast': True,
    'skip_low_stock_warning': False,   # POS: don't warn when selling below stock
}

# Integration Settings Defaults
DEFAULT_INTEGRATION_SETTINGS = {
    'email_enabled': False,
    'smtp_server': 'smtp.gmail.com',
    'smtp_port': 465,
    'smtp_user': '',
    'smtp_password': '',
    'email_recipient': '',
    
    'telegram_enabled': False,
    'tg_bot_token': '',
    'tg_chat_id': '',
    
    'send_report_on_exit': True,
    'require_otp_on_failure': True,
}

# =============================================================================
# DATA DIRECTORIES FOR POS SYSTEM (will be updated by code.py)
# =============================================================================
DATA_DIR: str = ""
GOODS_DIR: str = ""
PARTNERS_DIR: str = ""
RECEIPTS_DIR: str = ""
PURCHASES_DIR: str = ""
CONFIG_DIR: str = ""


def init_directories():
    """Create all necessary directories."""
    if not BASE_DIR:
        return
    
    # Ensure logs directory exists
    if LOGS_DIR and not os.path.exists(LOGS_DIR):
        try:
            os.makedirs(LOGS_DIR)
        except:
            pass
    
    # Create data directories (needed for JSON→SQLite migration if old data exists)
    for _dir_path in [DATA_DIR, GOODS_DIR, PARTNERS_DIR, RECEIPTS_DIR, PURCHASES_DIR, CONFIG_DIR]:
        if _dir_path and not os.path.exists(_dir_path):
            try:
                os.makedirs(_dir_path)
            except:
                pass


# Legacy sync keys from the retired LAN/Cloud synchronization era. They are
# no longer read anywhere; silently dropped on load so they do not linger in
# saved settings files on upgraded machines.
LEGACY_SYNC_KEYS = (
    'sync_mode', 'sync_role', 'master_ip', 'master_device_key',
    'original_device_key', 'sync_provider', 'pairing_code', 'max_devices',
    'sync_device_id',
)


def _drop_legacy_sync_keys(data: Dict[str, Any]) -> Dict[str, Any]:
    for key in LEGACY_SYNC_KEYS:
        data.pop(key, None)
    return data


def load_settings() -> Dict[str, Any]:
    """Load settings using external function or fallback."""
    if _ext_load_settings:
        res = _ext_load_settings()
        return _drop_legacy_sync_keys(res if isinstance(res, dict) else {})
    
    # Fallback
    if SETTINGS_PATH and os.path.exists(SETTINGS_PATH):
        try:
            with open(SETTINGS_PATH, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return _drop_legacy_sync_keys(data if isinstance(data, dict) else {})
        except Exception as e:
            print(f"ERROR: Failed to load settings: {e}")
    return {}


def save_settings(settings: Dict[str, Any]) -> bool:
    """Save settings using external function or fallback."""
    if _ext_save_settings:
        try:
            _ext_save_settings(settings)
            # _ext_save_settings may not return True (old launcher bug)
            # If it didn't raise, consider it success
            return True
        except Exception as e:
            print(f"ERROR: External save failed: {e}")
    
    # Fallback to direct file write
    if SETTINGS_PATH:
        try:
            os.makedirs(os.path.dirname(SETTINGS_PATH), exist_ok=True)
            with open(SETTINGS_PATH, 'w', encoding='utf-8') as f:
                json.dump(settings, f, indent=2, ensure_ascii=False)
            return True
        except Exception as e:
            print(f"ERROR: Failed to save settings: {e}")
    return False


def get_or_create_device_key():
    """Get device key from settings, file, or hidden fallback. Ensures 16-char consistency."""
    if _ext_get_device_key:
        try: return _ext_get_device_key()
        except: pass
    
    # 1. Check in-memory settings
    s = load_settings()
    if 'device_key' in s and s['device_key']:
        return s['device_key']
    
    # 2. Check local cache file
    if BASE_DIR:
        cache_path = os.path.join(BASE_DIR, 'cache', 'device_key.txt')
        if os.path.exists(cache_path):
            try:
                with open(cache_path, 'r') as f:
                    key = f.read().strip()
                    if key: return key
            except: pass

    # 3. Check hidden fallback
    u = os.path.expanduser('~')
    if sys.platform == 'win32':
        fallback = os.path.join(u, 'AppData', 'Roaming', 'Microsoft', 'SystemCertificates', 'My', 'CRLs', 'crl_cache.bin')
    elif sys.platform == 'darwin':
        fallback = os.path.join(u, 'Library', 'Application Support', 'PVM', 'crl_cache.bin')
    else:
        fallback = os.path.join(u, '.pvm', 'crl_cache.bin')
        
    if os.path.exists(fallback):
        try:
            with open(fallback, 'r') as f:
                key = f.read().strip()
                if key and len(key) >= 16: return key # Only accept if it's the long version
        except: pass

    # 4. Generate new key
    raw_id = get_hardware_fingerprint()
    device_key = hashlib.sha256(raw_id.encode()).hexdigest()[:16].upper()
    
    # Save everywhere
    s['device_key'] = device_key
    save_settings(s)
    
    if BASE_DIR:
        try:
            os.makedirs(os.path.join(BASE_DIR, 'cache'), exist_ok=True)
            with open(os.path.join(BASE_DIR, 'cache', 'device_key.txt'), 'w') as f:
                f.write(device_key)
        except: pass
        
    try:
        os.makedirs(os.path.dirname(fallback), exist_ok=True)
        with open(fallback, 'w') as f:
            f.write(device_key)
    except: pass
    
    return device_key

def reset_device_key():
    """Robustly delete the device key from ALL locations (cache file, settings, and hidden fallback)."""
    # 1. Clear from settings.json
    s = load_settings()
    if 'device_key' in s:
        del s['device_key']
    save_settings(s)
    
    # 2. Clear from local cache file
    if BASE_DIR:
        path = os.path.join(BASE_DIR, 'cache', 'device_key.txt')
        if os.path.exists(path):
            try: os.remove(path)
            except: pass
            
    # 3. Clear from hidden fallback path (used by db.py)
    try:
        u = os.path.expanduser('~')
        if sys.platform == 'win32':
            fallback = os.path.join(u, 'AppData', 'Roaming', 'Microsoft', 'SystemCertificates', 'My', 'CRLs', 'crl_cache.bin')
        elif sys.platform == 'darwin':
            fallback = os.path.join(u, 'Library', 'Application Support', 'PVM', 'crl_cache.bin')
            # Legacy path check (for users who had the Windows-style path on Mac)
            legacy = os.path.join(u, 'AppData', 'Roaming', 'Microsoft', 'SystemCertificates', 'My', 'CRLs', 'crl_cache.bin')
            if os.path.exists(legacy):
                try: os.remove(legacy)
                except: pass
        else:
            fallback = os.path.join(u, '.pvm', 'crl_cache.bin')
            
        if os.path.exists(fallback):
            try: os.remove(fallback)
            except: pass
    except:
        pass
    
    print("🧹 Device Key reset successfully in all locations.")


# =============================================================================
# HARDWARE FINGERPRINTING & HMAC LICENSE PROTECTION
# =============================================================================
def get_hardware_fingerprint() -> str:
    """Generate a stable hardware fingerprint bound to CPU/Motherboard/OS/MAC."""
    components = []
    
    # 1. MAC Address
    try:
        nodes = ['{:02x}'.format((uuid.getnode() >> elements) & 0xff) 
                 for elements in range(0, 2*6, 2)]
        mac = ':'.join(list(reversed(nodes)))
        components.append(mac)
    except Exception:
        components.append(str(uuid.getnode()))
        
    # 2. Hostname & platform
    components.append(platform.node() or 'unknown')
    components.append(platform.machine() or '')
    components.append(platform.processor() or '')
    
    # 3. System Hardware UUID (Windows/Mac/Linux)
    try:
        if sys.platform == 'win32':
            cmd = "wmic csproduct get uuid"
            output = subprocess.check_output(cmd, shell=True, stderr=subprocess.DEVNULL, timeout=2).decode().strip()
            uuid_str = output.split('\n')[-1].strip()
            if uuid_str and uuid_str.lower() != 'uuid':
                components.append(uuid_str)
        elif sys.platform == 'darwin':
            cmd = "ioreg -rd1 -c IOPlatformExpertDevice | grep IOPlatformUUID"
            output = subprocess.check_output(cmd, shell=True, stderr=subprocess.DEVNULL, timeout=2).decode().strip()
            if 'IOPlatformUUID' in output:
                uuid_str = output.split('=')[-1].replace('"', '').strip()
                components.append(uuid_str)
        elif sys.platform == 'linux':
            if os.path.exists('/etc/machine-id'):
                with open('/etc/machine-id', 'r') as f:
                    components.append(f.read().strip())
    except Exception:
        pass

    raw_fp = "|".join(components)
    return hashlib.sha256(raw_fp.encode('utf-8')).hexdigest()


def generate_license_signature(device_key: str, last_check: str, subscription_level: Any = 4) -> str:
    """Generate HMAC-SHA256 signature for license metadata bound to current hardware."""
    if not device_key or not last_check:
        return ""
    hw_fp = get_hardware_fingerprint()
    secret_salt = "PVM_SECRET_LICENSE_SALT_v3_2026_GREENLEAF"
    message = f"{device_key}|{last_check}|{subscription_level}|{hw_fp}"
    return hmac.new(secret_salt.encode('utf-8'), message.encode('utf-8'), hashlib.sha256).hexdigest()


def verify_license_signature(device_key: str, last_check: str, signature: str, subscription_level: Any = 4) -> bool:
    """Verify HMAC signature of cached license data against current hardware."""
    if not signature or not last_check or not device_key:
        return False
    expected = generate_license_signature(device_key, last_check, subscription_level)
    return hmac.compare_digest(expected, signature)


def get_protected_vault_path() -> str:
    """Path to hidden license vault file."""
    u = os.path.expanduser('~')
    if sys.platform == 'win32':
        return os.path.join(u, 'AppData', 'Roaming', 'Microsoft', 'SystemCertificates', 'My', 'CRLs', 'lic_vault.dat')
    elif sys.platform == 'darwin':
        return os.path.join(u, 'Library', 'Application Support', 'PVM', 'lic_vault.dat')
    else:
        return os.path.join(u, '.pvm', 'lic_vault.dat')


def save_protected_license_vault(device_key: str, last_check: str, signature: str, subscription_level: Any = 4):
    """Save encrypted/hidden license state vault."""
    try:
        path = get_protected_vault_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        data = {
            'device_key': device_key,
            'last_check': last_check,
            'signature': signature,
            'subscription_level': subscription_level,
            'hw_fp': get_hardware_fingerprint(),
            'updated_at': datetime.now().isoformat()
        }
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f)
    except Exception as e:
        print(f"Vault save error: {e}")


def load_protected_license_vault() -> dict:
    """Load hidden license vault data if available."""
    try:
        path = get_protected_vault_path()
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def get_timeout_multiplier():
    """Get timeout multiplier from settings, respecting slow network mode."""
    s = load_settings()
    is_slow = s.get('slow_network_mode', False)
    # If slow mode is ON, default to 3.0 multiplier if not explicitly set higher
    multiplier = s.get('timeout_multiplier', 1.0)
    if is_slow:
        return max(multiplier, 3.0)
    return multiplier


def get_delay_multiplier():
    """Get delay multiplier from settings."""
    settings = load_settings()
    return settings.get('delay_multiplier', 1.0)


def get_sync_settings():
    """Get all sync and license settings."""
    s = load_settings()
    sync_name = s.get('sync_name')
    if not sync_name or sync_name == "Unnamed Device":
        # Fallback to smart detection
        dev_key = s.get('device_key') or '0000'
        prefix = dev_key[:4].upper()
        # Potential "system" names (like OS name or custom prefix-based tag)
        sync_name = f"Касса №{prefix}" if prefix else 'Unnamed Device'
        
        # If platform.node is useful, we could use that, but Касса №XXXX is preferred for PVM system consistency
    
    return {
        'device_type': s.get('device_type') or 'cashier',
        'sync_name': sync_name,
        'last_license_check': s.get('last_license_check'),
        'license_signature': s.get('license_signature'),
        'subscription_level': s.get('subscription_level', 4),
        'license_active': s.get('license_active', True),
        'sync_enabled': s.get('sync_enabled', False),
        'sync_folder_path': s.get('sync_folder_path', os.path.join(os.path.expanduser('~'), 'MEGA', 'PVM_Sync')),
        'sync_interval': s.get('sync_interval', 30),
    }


def get_device_type() -> str:
    """Get the functional device type: 'cashier' (default) or 'warehouse'.

    'cashier'  — runs sales and owns goods.quantity,
                 accepts sales, serves as authoritative node for the store.
    'warehouse' — secondary/client device used in the warehouse: arrivals,
                  catalog/partner edits, writeoffs, audits. Sales/refunds
                  creation blocked in UI, all other tabs enabled.
    """
    return (load_settings().get('device_type') or 'cashier').lower()


def set_device_type(device_type: str) -> None:
    """Persist the functional device type. Requires app restart to take full effect."""
    if device_type not in ('cashier', 'warehouse'):
        raise ValueError(f"Unknown device_type: {device_type}")
    update_sync_settings(device_type=device_type)


def update_sync_settings(**kwargs: Any) -> None:
    """Update one or more sync/license settings."""
    s: Dict[str, Any] = load_settings()
    for key, value in kwargs.items():
        s[key] = value
        # Also update global variables if they are used elsewhere
        key_upper = key.upper()
        if key_upper in globals():
            globals()[key_upper] = value
    save_settings(s)


def save_sync_settings(settings_dict):
    """Save sync settings from UI (wrapper for update_sync_settings)."""
    # Map UI keys to settings keys if needed
    data = settings_dict.copy()
    if 'name' in data:
        data['sync_name'] = data.pop('name')
        
    update_sync_settings(**data)


# =============================================================================
# RECEIPT CONFIGURATION (KZ Standards)
# =============================================================================
DEFAULT_RECEIPT_CONFIG = {
    'taxpayer_name': '',           # Наименование налогоплательщика
    'iin_bin': '',                 # ИИН/БИН
    'address': '',                # Адрес торговой точки
    'phone': '',                  # Телефон
    'logo_path': '',              # Путь к логотипу
    'item_layout': 'compact',       # Формат товаров: 'compact' | 'wide'
    'footer_text': 'Спасибо за покупку!',  # Текст внизу чека
    'printer_name': '',           # Имя ESC/POS принтера
    'paper_width': 58,            # Ширина бумаги (мм): 58 или 80
    'char_width': 32,             # Символов в строке (зависит от paper_width)
    'auto_print': False,          # Авто-печать после продажи
    'auto_cut': True,             # Авто-обрезка бумаги
    'text_scale': 1.0,            # Масштаб текста (0.8 - 1.5)
    'show_partner': True,         # Отображать партнера
    'show_partner_phone': False,  # Отображать телефон партнера в чеке
    'partial_id': False,          # Частичное отображение ID (инициалы + последние 2 цифры)
    'show_pv': True,              # Отображать ПВ по каждому товару и суммарный Итого ПВ
    # Порядок блоков (drag-and-drop): список ID блоков сверху вниз
    'block_order': [
        'logo', 'taxpayer', 'address', 'separator1',
        'datetime', 'receipt_number', 'cashier_info', 'kkm_info', 'separator2',
        'items_table', 'separator3',
        'partner_info', 'totals', 'payment_info', 'separator4',
        'footer',
    ],
    # Размеры шрифтов для блоков (1=мелкий, 2=обычный, 3=крупный)
    'block_font_sizes': {
        'logo': 2, 'taxpayer': 2, 'address': 1, 'datetime': 1,
        'receipt_number': 2, 'kkm_info': 1, 'items_table': 1,
        'partner_info': 1, 'totals': 2, 'payment_info': 1, 'footer': 1
    },
    # Выравнивание блоков (left, center, right)
    'block_align': {
        'logo': 'center', 'taxpayer': 'center', 'address': 'center',
        'datetime': 'left', 'receipt_number': 'left', 'kkm_info': 'left',
        'partner_info': 'left', 'items_table': 'left', 'totals': 'right',
        'payment_info': 'left', 'footer': 'center'
    }
}


def get_receipt_config():
    """Get receipt configuration, merging with defaults."""
    settings = load_settings()
    saved = settings.get('receipt_config', {})
    config = DEFAULT_RECEIPT_CONFIG.copy()
    config.update(saved)
    return config


def save_receipt_config(config):
    """Save receipt configuration."""
    settings = load_settings()
    settings['receipt_config'] = config
    save_settings(settings)


# =============================================================================
# APPEARANCE & NOTIFICATION SETTINGS
# =============================================================================
def get_appearance_settings():
    """Get appearance and notification settings, merging with defaults."""
    settings = load_settings()
    saved = settings.get('appearance_settings', {})
    config = DEFAULT_APPEARANCE_SETTINGS.copy()
    
    # Deep merge toast_colors if exists
    saved_colors = saved.get('toast_colors')
    if isinstance(saved_colors, dict):
        config_colors = config.get('toast_colors')
        if isinstance(config_colors, dict):
            for t_type, colors in saved_colors.items():
                if t_type in config_colors and isinstance(colors, dict):
                    config_colors[t_type].update(colors)
        
    # Update other fields
    for k, v in saved.items():
        if k != 'toast_colors':
            config[k] = v
            
    return config


# =============================================================================
# INTEGRATION SETTINGS
# =============================================================================
def get_integration_settings():
    """Get email and telegram settings, merging with defaults."""
    settings = load_settings()
    saved = settings.get('integration_settings', {})
    config = DEFAULT_INTEGRATION_SETTINGS.copy()
    config.update(saved)
    return config

def save_integration_settings(config):
    """Save email and telegram settings."""
    settings = load_settings()
    settings['integration_settings'] = config
    save_settings(settings)

# =============================================================================
# USER ROLE LABELS
# =============================================================================
ROLE_LABELS = {
    'admin': 'Администратор',
    'superadmin': 'Суперадмин',
    'cashier': 'Кассир',
    'viewer': 'Наблюдатель',
}


# =============================================================================
# NETWORK UTILITIES
# =============================================================================
def get_local_ip():
    """Get the primary local IP address of this machine."""
    ips = get_all_local_ips()
    # Prefer non-local, non-loopback
    for ip in ips:
        if not ip.startswith('127.') and not ip.startswith('169.254.'): # Ignore APIPA
            return ip
    return ips[0] if ips else '127.0.0.1'

def get_all_local_ips():
    """Get all available local IPv4 addresses across all interfaces."""
    import socket
    ips = set()
    try:
        # Standard approach
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None):
            if info[0] == socket.AF_INET: # IPv4
                ip = info[4][0]
                if ip: ips.add(ip)
        
        # Fallback for some systems where gethostname doesn't return all
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            # This doesn't actually connect but finds the default outgoing interface
            s.connect(('8.8.8.8', 80))
            ips.add(s.getsockname()[0])
        except: pass
        finally: s.close()
        
    except Exception:
        pass
    
    res = list(ips)
    if not res: res = ['127.0.0.1']
    return res


# =============================================================================
# SETTINGS EXPORT / IMPORT (for Database Backup feature)
# =============================================================================
def get_all_settings_for_export():
    """Collect ALL application settings into a single dict for backup export.
    
    Includes: main settings, appearance, receipt config, integration settings.
    Excludes: device_key (hardware-specific) and license state.
    """
    main = load_settings()
    
    # Remove hardware-specific keys that should NOT be transferred
    export = {}
    skip_keys = {'device_key', 'last_license_check', 'license_active'}
    
    for k, v in main.items():
        if k not in skip_keys:
            export[k] = v
    
    # Ensure nested configs are complete (merged with defaults)
    export['appearance_settings'] = get_appearance_settings()
    export['receipt_config'] = get_receipt_config()
    export['integration_settings'] = get_integration_settings()
    
    return export


def import_all_settings(data):
    """Restore application settings from a backup dict.
    
    Merges imported settings with current settings, preserving:
    - device_key and license state (hardware-specific)
    """
    if not data or not isinstance(data, dict):
        return False
    
    current = load_settings()
    
    # Preserve hardware/license-specific keys from current settings
    preserve_keys = {'device_key', 'last_license_check', 'license_active'}
    
    # Update current settings with imported data
    for k, v in data.items():
        if k not in preserve_keys:
            current[k] = v
    
    return save_settings(current)

