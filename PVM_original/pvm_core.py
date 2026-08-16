# -*- coding: utf-8 -*-
"""
PVM.core v2.7.0 - PVM Automation Core
======================================
Playwright automation logic for GreenLeaf system.
All methods here are designed to be monkey-patched onto ui.GreenLeafApp at import time.
"""

import sys
import subprocess
import os
import smtplib
import threading
import time
import requests
import json
import base64
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import Header
from datetime import date, timedelta

import settings

# =============================================================================
# PROGRESS TRACKING (Resume interrupted sessions)
# =============================================================================
PROGRESS_PATH = os.path.join(settings.BASE_DIR, 'cache', '_prg.bin') if settings.BASE_DIR else None

def load_progress():
    """Load interrupted progress from hidden file."""
    try:
        if PROGRESS_PATH and os.path.exists(PROGRESS_PATH):
            with open(PROGRESS_PATH, 'r', encoding='utf-8') as f:
                enc = f.read()
                return json.loads(base64.b64decode(enc).decode())
    except Exception as e:
        print(f"Error loading progress: {e}")
    return None

def save_progress(progress):
    """Save current progress to hidden file."""
    try:
        if not PROGRESS_PATH:
            return False
        os.makedirs(os.path.dirname(PROGRESS_PATH), exist_ok=True)
        with open(PROGRESS_PATH, 'w', encoding='utf-8') as f:
            enc = base64.b64encode(json.dumps(progress).encode()).decode()
            f.write(enc)
        return True
    except Exception as e:
        print(f"Error saving progress: {e}")
        return False

def clear_progress():
    """Clear progress file after successful completion."""
    try:
        if PROGRESS_PATH and os.path.exists(PROGRESS_PATH):
            os.remove(PROGRESS_PATH)
    except Exception:
        pass


# =============================================================================
# PLAYWRIGHT SETUP
# =============================================================================
def ensure_playwright_browsers(timeout=180):
    """Ensure Playwright browsers are installed."""
    try:
        print("Checking Playwright browser installation...")
        # On Windows, run Playwright installer without showing a console window
        kwargs = {"check": True, "capture_output": True}
        if sys.platform == "win32":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            kwargs["startupinfo"] = startupinfo
            # CREATE_NO_WINDOW is not available on all platforms, use getattr
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)

        subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            timeout=timeout,
            **kwargs,
        )
        print("✅ Playwright browsers ready.")
        return True
    except Exception as e:
        print(f"⚠️ Warning: Could not verify Playwright browsers: {e}")
        return False


# =============================================================================
# AUTOMATION CORE FUNCTIONS
# =============================================================================
# NOTE: All automation methods (_login, _run_step1, _run_step2, etc.)
# are defined as methods of GreenLeafApp in ui.py.
# This module only provides utility functions used by code.py and ui.py.


# =============================================================================
# INTEGRATIONS (Email & Telegram)
# =============================================================================

class EmailService:
    @staticmethod
    def send_email(subject, body, config=None):
        """Send email using SMTP."""
        if config is None:
            config = settings.get_integration_settings()
        
        if not config.get('email_enabled'):
            return False, "Email-уведомления отключены"
        
        user = config.get('smtp_user')
        pwd = config.get('smtp_password')
        server_host = config.get('smtp_server')
        try:
            port = int(config.get('smtp_port', 465))
        except (ValueError, TypeError):
            port = 465
        recipient = config.get('email_recipient')
        
        if not all([user, pwd, server_host, recipient]):
            return False, "Заполнены не все настройки SMTP (сервер, логин, пароль, получатель)"
        
        try:
            msg = MIMEMultipart()
            msg['From'] = user
            msg['To'] = recipient
            msg['Subject'] = str(Header(subject, 'utf-8'))
            msg.attach(MIMEText(body, 'plain', 'utf-8'))
            
            # Use SSL for port 465, STARTTLS for others
            if port == 465:
                server = smtplib.SMTP_SSL(server_host, port, timeout=10)
            else:
                server = smtplib.SMTP(server_host, port, timeout=10)
                server.starttls()
            
            server.login(user, pwd)
            server.send_message(msg)
            server.quit()
            return True, "Success"
        except Exception as e:
            return False, str(e)

class TelegramService:
    @staticmethod
    def send_message(text, config=None):
        """Send message via Telegram Bot API."""
        if config is None:
            config = settings.get_integration_settings()
            
        if not config.get('telegram_enabled'):
            return False, "Telegram-бот отключён"
            
        token = config.get('tg_bot_token')
        chat_id = config.get('tg_chat_id')
        
        if not token or not chat_id:
            return False, "Указаны не все данные Telegram (токен и Chat ID)"
            
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        try:
            resp = requests.post(url, json={'chat_id': chat_id, 'text': text,
                                            'parse_mode': 'HTML',
                                            'disable_web_page_preview': True}, timeout=10)
            if resp.status_code == 200:
                return True, "Success"
            err = resp.text
            if len(err) > 300:
                err = err[:300] + '...'
            return False, err
        except Exception as e:
            return False, str(e)


def build_daily_report(mgr, target_date):
    """Build a human-readable daily sales report (integer amounts)."""
    data = mgr.get_daily_summary(target_date)
    
    def fmt(v):
        try:
            return f"{int(round(float(v))):,}".replace(',', ' ')
        except Exception:
            return "0"
    
    lines = [
        f"<b>📊 Отчёт за {data['date']}</b>",
        "──────────────",
        f"Чеков: {data['receipts_count']}",
        f"Итого: {fmt(data['total_sales'])} тг",
        f"  • Наличные: {fmt(data['payment_cash'])} тг",
        f"  • Карта: {fmt(data['payment_card'])} тг",
        f"  • Внутренние: {fmt(data['payment_internal'])} тг",
    ]
    if data.get('top_items'):
        lines.append("")
        lines.append("<b>🔥 Топ товаров:</b>")
        for item in data['top_items'][:5]:
            lines.append(f"• {item['name']}: {item['qty']} шт")
    return "\n".join(lines)


class IntegrationBot:
    """Background bot for handling Telegram commands."""
    _shared_last_update_id = 0  # survives bot restarts (no duplicate processing)

    def __init__(self, app_instance):
        self.app = app_instance
        self.running = False
        self.thread = None
        self.stop_event = threading.Event()
        self.last_update_id = IntegrationBot._shared_last_update_id
        
    def start(self):
        if self.running: return
        config = settings.get_integration_settings()
        if not config.get('telegram_enabled') or not config.get('tg_bot_token'):
            return
            
        self.running = True
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._poll_loop, daemon=True)
        self.thread.start()
        
    def stop(self):
        self.running = False
        try:
            self.stop_event.set()
        except Exception:
            pass

    def _poll_loop(self):
        while self.running and not self.stop_event.is_set():
            try:
                config = settings.get_integration_settings()
                token = config.get('tg_bot_token')
                if not token: 
                    time.sleep(10)
                    continue
                
                url = f"https://api.telegram.org/bot{token}/getUpdates"
                params = {'offset': self.last_update_id + 1, 'timeout': 30}
                resp = requests.get(url, params=params, timeout=35)
                
                if resp.status_code == 200:
                    data = resp.json()
                    for update in data.get('result', []):
                        self.last_update_id = update['update_id']
                        IntegrationBot._shared_last_update_id = self.last_update_id
                        if 'message' in update:
                            self._handle_message(update['message'])
                else:
                    time.sleep(10)
            except Exception:
                time.sleep(10)

    def _handle_message(self, msg):
        text = msg.get('text', '').strip().lower()
        chat_id = msg['chat'].get('id')
        
        # Security: Only respond to the configured Chat ID
        config = settings.get_integration_settings()
        if str(chat_id) != str(config.get('tg_chat_id')):
            if text == "/start":
                TelegramService.send_message(
                    f"Ваш Chat ID: <code>{chat_id}</code>\n"
                    "Скопируйте его в POS: Настройки → Интеграции.",
                    config={'telegram_enabled': True,
                            'tg_bot_token': config.get('tg_bot_token'),
                            'tg_chat_id': chat_id})
            return

        if text in ("/today", "/сегодня"):
            self._send_report(chat_id, date.today())
        elif text in ("/yesterday", "/вчера"):
            self._send_report(chat_id, date.today() - timedelta(days=1))
        elif text in ("/stats", "/статистика"):
            self._send_quick_stats(chat_id)
        elif text in ("/help", "/помощь", "/start"):
            help_text = ("<b>Доступные команды:</b>\n"
                         "/today — отчёт за сегодня\n"
                         "/yesterday — отчёт за вчера\n"
                         "/stats — сводка за 7 дней\n"
                         "/help — список команд")
            TelegramService.send_message(help_text, config)

    def _send_report(self, chat_id, target_date):
        mgr = self.app.receipts_manager
        report = build_daily_report(mgr, target_date)
        config = settings.get_integration_settings()
        TelegramService.send_message(report, config)

    def _send_quick_stats(self, chat_id):
        mgr = self.app.receipts_manager
        total_week = 0
        for i in range(7):
            d = date.today() - timedelta(days=i)
            data = mgr.get_daily_summary(d)
            total_week += data['total_sales']
        try:
            total_str = f"{int(round(total_week)):,}".replace(',', ' ')
        except Exception:
            total_str = "0"
        config = settings.get_integration_settings()
        TelegramService.send_message(f"<b>📈 Продажи за 7 дней:</b>\nИтого: {total_str} тг", config)


def send_exit_report(app, config=None):
    """Send the daily report on app exit (send_report_on_exit)."""
    if config is None:
        config = settings.get_integration_settings()
    if not (config.get('send_report_on_exit') and
            (config.get('email_enabled') or config.get('telegram_enabled'))):
        return False
    try:
        mgr = app.receipts_manager
        text = build_daily_report(mgr, date.today())
        sent = False
        if config.get('email_enabled'):
            subject = f"Отчёт за {date.today().strftime('%d.%m.%Y')} — PVM.core"
            ok, _ = EmailService.send_email(subject, text, config)
            sent = sent or ok
        if config.get('telegram_enabled'):
            ok, _ = TelegramService.send_message(text, config)
            sent = sent or ok
        return sent
    except Exception as e:
        print(f"[EXIT REPORT] {e}")
        return False

class OTPManager:
    """Manages 6-digit verification codes."""
    _current_otp = None
    _otp_user = None
    _expiry = 0
    _attempts = 0
    MAX_ATTEMPTS = 5
    OTP_TTL = 600

    @classmethod
    def generate_otp(cls, username=None):
        import secrets
        cls._current_otp = "".join([str(secrets.randbelow(10)) for _ in range(6)])
        cls._otp_user = username
        cls._expiry = time.time() + cls.OTP_TTL
        cls._attempts = 0
        return cls._current_otp

    @classmethod
    def verify(cls, code, username=None):
        if not cls._current_otp or time.time() > cls._expiry:
            return False
        if username and cls._otp_user and str(username) != str(cls._otp_user):
            return False
        if cls._attempts >= cls.MAX_ATTEMPTS:
            cls.clear_otp()
            return False
        if str(code) == str(cls._current_otp):
            cls.clear_otp()
            return True
        cls._attempts += 1
        return False

    @classmethod
    def is_blocked(cls):
        return (cls._current_otp is not None
                and cls._attempts >= cls.MAX_ATTEMPTS)

    @classmethod
    def clear_otp(cls):
        cls._current_otp = None
        cls._otp_user = None
        cls._expiry = 0
        cls._attempts = 0

    @classmethod
    def send_to_owner(cls, username=None):
        otp = cls.generate_otp(username)
        config = settings.get_integration_settings()
        subject = "Код подтверждения — PVM.core"
        body = f"Ваш код подтверждения: {otp}\nДействителен {cls.OTP_TTL // 60} минут."
        
        EmailService.send_email(subject, body, config)
        if config.get('telegram_enabled'):
            TelegramService.send_message(
                f"🔐 Внимание: попытка входа в POS.\nКод подтверждения: <code>{otp}</code>\nДействителен {cls.OTP_TTL // 60} минут.",
                config)
        return True
