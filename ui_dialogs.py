# -*- coding: utf-8 -*-
"""
PVM.core - Dialog Classes
============================
WaitingScreen, AdminSetupWizard,
UserLoginScreen, AutoScrollbar, AutocompleteEntry.
"""

import os
import threading
import tkinter as tk
from tkinter import ttk, messagebox
from typing import Any, Optional

import settings
from db_sqlite import DatabaseManager, UsersManagerSQL
from ui_lang import get_text, MODULE_VERSION
from pvm_core import OTPManager

class WaitingScreen:
    """Display waiting screen while license is inactive."""
    
    def __init__(self, device_key, status_message, parent=None):
        self.device_key = device_key
        self.status_message = status_message
        self.parent = parent
        self.should_continue = True
        self.is_activated = False
        self.root: Optional[tk.Tk] = None
        self.status_label: Optional[tk.Label] = None
        self.progress_label: Optional[tk.Label] = None
        self.tk_logo: Optional[Any] = None
        self.subscription_level = 4 # Default to FullPackage
        
    def show(self):
        """Show waiting screen and poll for activation."""
        if self.parent and str(self.parent.state()) != 'withdrawn':
            root = tk.Toplevel(self.parent)
            root.transient(self.parent)
        else:
            root = tk.Toplevel() if self.parent else tk.Tk()
        self.root = root
        root.title("PVM.core - Ожидание активации")
        root.geometry("500x350")
        root.resizable(False, False)
        
        # Center window
        root.update_idletasks()
        x = (root.winfo_screenwidth() // 2) - (250)
        y = (root.winfo_screenheight() // 2) - (175)
        root.geometry(f"500x350+{x}+{y}")
        
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        
        # Title
        tk.Label(root, text="🔒 Ожидание активации", 
                font=("Arial", 16, "bold")).pack(pady=20)
        
        # Device key
        frame = tk.LabelFrame(root, text="Информация об устройстве", padx=10, pady=10)
        frame.pack(pady=10, padx=20, fill="x")
        
        tk.Label(frame, text="Ключ устройства:", font=("Arial", 10)).pack(anchor="w")
        
        key_frame = tk.Frame(frame)
        key_frame.pack(fill="x", pady=5)
        
        key_entry = tk.Entry(key_frame, width=40, font=("Courier", 10))
        key_entry.insert(0, self.device_key)
        key_entry.config(state="readonly")
        key_entry.pack(side="left", padx=(0, 5))
        
        tk.Button(key_frame, text="Копировать", 
                 command=lambda: self.copy_to_clipboard(self.device_key)).pack(side="left")
        
        # Status
        status_label = tk.Label(root, text=f"Статус: {self.status_message}", 
                                     font=("Arial", 10), fg="red")
        status_label.pack(pady=10)
        self.status_label = status_label
        
        # Instructions
        tk.Label(root, text="Предоставьте ключ устройства администратору\nдля активации лицензии.",
                font=("Arial", 9), fg="gray").pack(pady=10)
        
        # Progress indicator
        progress_label = tk.Label(root, text="Проверка статуса...", font=("Arial", 9))
        progress_label.pack(pady=5)
        self.progress_label = progress_label
        
        # Start polling
        self.poll_activation()
        
        if self.parent:
            self.parent.wait_window(root)
        else:
            root.mainloop()
        
        return self.is_activated, self.subscription_level

    def copy_to_clipboard(self, text):
        """Copy text to clipboard."""
        if self.root:
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
            messagebox.showinfo("Скопировано", "Ключ скопирован в буфер обмена!")
    
    def poll_activation(self):
        """Poll Supabase for activation status."""
        if not self.should_continue:
            return
        
        from db import check_license_status_only
        is_active, status_message, _, _, subscription_level = check_license_status_only(self.device_key)
        self.subscription_level = subscription_level
        
        if is_active:
            self.is_activated = True
            if self.status_label:
                self.status_label.config(text=f"✅ {status_message}", fg="green")
            if self.progress_label:
                self.progress_label.config(text="Активация подтверждена! Запуск приложения...")
            if self.root:
                self.root.after(1500, self.root.destroy)
        else:
            if self.status_label:
                self.status_label.config(text=f"❌ {status_message}", fg="red")
            from datetime import datetime
            timestamp = datetime.now().strftime("%H:%M:%S")
            if self.progress_label:
                self.progress_label.config(text=f"Последняя проверка: {timestamp}")
            if self.root:
                self.root.after(60000, self.poll_activation)  # Check every minute
    
    def on_close(self):
        """Handle window close."""
        self.should_continue = False
        if self.root:
            self.root.destroy()


# =============================================================================
# ADMIN SETUP WIZARD (First run after activation)
# =============================================================================
class AdminSetupWizard:
    """First-run wizard to create the superadmin user with PIN."""
    
    def __init__(self, db_path, parent=None):
        self.db_path = db_path
        self.parent = parent
        self.db_manager = DatabaseManager(db_path)
        self.users_manager = UsersManagerSQL(self.db_manager)
        self.result = None  # Will be set to user dict on success
    
    def show(self):
        """Show setup wizard. Returns superadmin user dict or None."""
        if self.parent and str(self.parent.state()) != 'withdrawn':
            self.root = tk.Toplevel(self.parent)
            self.root.transient(self.parent)
        else:
            self.root = tk.Toplevel() if self.parent else tk.Tk()
        self.root.title("PVM.core - Настройка суперадминистратора")
        self.root.geometry("480x420")
        self.root.resizable(False, False)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        
        # Center window
        self.root.update_idletasks()
        x = (self.root.winfo_screenwidth() // 2) - 240
        y = (self.root.winfo_screenheight() // 2) - 210
        self.root.geometry(f"480x420+{x}+{y}")
        
        tk.Label(self.root, text="🔐 Создание суперадминистратора", 
                font=("Arial", 16, "bold")).pack(pady=15)
        tk.Label(self.root, text="Суперадмин — единственный, создается первым и имеет полный доступ.\nЗадайте PIN-код для входа в приложение",
                font=("Arial", 10), fg="gray", justify="center").pack(pady=5)
        
        frame = tk.Frame(self.root)
        frame.pack(pady=15, padx=40, fill="x")
        
        # Display name
        tk.Label(frame, text="Имя:", font=("Arial", 11), anchor="e", width=18).grid(row=0, column=0, pady=8, sticky="e")
        self.name_entry = tk.Entry(frame, font=("Arial", 11), width=22)
        self.name_entry.grid(row=0, column=1, pady=8, padx=5)
        self.name_entry.insert(0, "admin")
        
        vcmd = (self.root.register(self._validate_pin), '%P')
        
        # PIN entry
        tk.Label(frame, text="PIN (4 цифры):", font=("Arial", 11), anchor="e", width=18).grid(row=1, column=0, pady=8, sticky="e")
        self.pin_entry = tk.Entry(frame, font=("Arial", 14), width=10, show="●", justify="center", validate="key", validatecommand=vcmd)
        self.pin_entry.grid(row=1, column=1, pady=8, padx=5, sticky="w")
        
        # Confirm PIN
        tk.Label(frame, text="Повторите PIN:", font=("Arial", 11), anchor="e", width=18).grid(row=2, column=0, pady=8, sticky="e")
        self.pin_confirm = tk.Entry(frame, font=("Arial", 14), width=10, show="●", justify="center", validate="key", validatecommand=vcmd)
        self.pin_confirm.grid(row=2, column=1, pady=8, padx=5, sticky="w")
        
        # PIN hint
        tk.Label(frame, text="Подсказка (если забыли):", font=("Arial", 11), anchor="e", width=18).grid(row=3, column=0, pady=8, sticky="e")
        self.hint_entry = tk.Entry(frame, font=("Arial", 11), width=22)
        self.hint_entry.grid(row=3, column=1, pady=8, padx=5)
        
        self.error_label = tk.Label(self.root, text="", font=("Arial", 10), fg="red")
        self.error_label.pack(pady=5)
        
        btn = tk.Button(self.root, text="Создать суперадминистратора", command=self._on_submit,
                       bg="#4CAF50", fg="white", font=("Arial", 12, "bold"),
                       width=25, height=2, cursor="hand2")
        btn.pack(pady=10)
        
        # Keybindings
        self.root.bind('<Return>', lambda e: self._on_submit())
        self.root.bind('<KP_Enter>', lambda e: self._on_submit())
        
        self.name_entry.focus_set()
        if self.parent:
            self.parent.wait_window(self.root)
        else:
            self.root.mainloop()
        return self.result
    
    def _validate_pin(self, P):
        """Validation for PIN entries: digits only, max 4."""
        if P == "": return True
        return P.isdigit() and len(P) <= 4
    
    def _on_submit(self):
        name = self.name_entry.get().strip()
        pin = self.pin_entry.get().strip()
        pin2 = self.pin_confirm.get().strip()
        hint = self.hint_entry.get().strip()
        
        if not name:
            self.error_label.config(text="Введите имя пользователя")
            return
        if len(pin) != 4 or not pin.isdigit():
            self.error_label.config(text="PIN должен быть 4 цифры")
            return
        if pin != pin2:
            self.error_label.config(text="PIN-коды не совпадают")
            return
        
        user = self.users_manager.create_user(
            username=name.lower(), display_name=name,
            role='superadmin', pin=pin, pin_hint=hint
        )
        if user:
            self.result = user
            self.root.destroy()
        else:
            self.error_label.config(text="Ошибка: пользователь уже существует")
    
    def _on_close(self):
        self.result = None
        self.root.destroy()


# =============================================================================
# USER LOGIN SCREEN (On every app launch)
# =============================================================================
class UserLoginScreen:
    """Login screen with dropdown user select + 4-digit PIN."""
    
    def __init__(self, db_path, parent=None):
        self.db_path = db_path
        self.parent = parent
        self.db_manager = DatabaseManager(db_path)
        self.users_manager = UsersManagerSQL(self.db_manager)
        self.result = None  # Will be user dict on success
        self.attempts = 0
        self.otp_mode = False
    
    def show(self):
        """Show login screen. Returns user dict or None."""
        users = self.users_manager.get_all_users()
        if not users:
            return None
        
        # Load theme from settings for login screen styling
        import settings as _st
        _s = _st.load_settings()
        _is_dark = _s.get('theme', 'light') == 'dark'
        _bg = '#2d2d2d' if _is_dark else '#fafafa'
        _fg = '#e0e0e0' if _is_dark else '#333333'
        _fg_muted = '#808080' if _is_dark else '#888888'
        _fg_sec = '#b0b0b0' if _is_dark else '#555555'
        _frame_bg = '#3d3d3d' if _is_dark else '#ffffff'
        _accent = '#90caf9' if _is_dark else '#5c6bc0'
        _btn_color = '#4caf50' if _is_dark else '#2e7d32'
        
        if self.parent and str(self.parent.state()) != 'withdrawn':
            self.root = tk.Toplevel(self.parent)
            self.root.transient(self.parent)
        else:
            self.root = tk.Toplevel() if self.parent else tk.Tk()
        self.root.withdraw() # Hide while positioning
        self.root.title("PVM.core - Вход")
        self.root.geometry("400x380")
        self.root.resizable(False, False)
        self.root.configure(bg=_bg)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        
        # Center
        self.root.update_idletasks()
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        x = (sw // 2) - 200
        y = (sh // 2) - 190
        self.root.geometry(f"400x380+{x}+{y}")
        self.root.deiconify() # Show centered
        
        tk.Label(self.root, text="PVM.core", font=("Arial", 20, "bold"), bg=_bg, fg=_btn_color).pack(pady=(20, 0))
        tk.Label(self.root, text=f"v{MODULE_VERSION}", font=("Arial", 9), bg=_bg, fg=_fg_muted).pack()
        tk.Label(self.root, text="Выберите пользователя", font=("Arial", 11), bg=_bg, fg=_fg_muted).pack(pady=5)
        
        frame = tk.Frame(self.root, bg=_bg)
        frame.pack(pady=20, padx=40, fill="x")
        
        # User dropdown
        tk.Label(frame, text="Пользователь:", font=("Arial", 11), bg=_bg, fg=_fg_sec).grid(row=0, column=0, pady=10, sticky="e", padx=5)
        self.user_var = tk.StringVar()
        self.user_map = {}  # display -> username
        display_values = []
        for u in users:
            role_label = settings.ROLE_LABELS.get(u['role'], u['role'])
            display = f"{u['display_name']} ({role_label})"
            display_values.append(display)
            self.user_map[display] = u['username']
        
        # Load last user if exists
        last_user_path = os.path.join(os.path.dirname(self.db_path) or ".", "last_user.txt")
        last_user = None
        if os.path.exists(last_user_path):
            try:
                with open(last_user_path, 'r', encoding='utf-8') as f:
                    last_user = f.read().strip()
            except Exception: pass
            
        self.user_combo = ttk.Combobox(frame, textvariable=self.user_var, values=display_values,
                                        state="readonly", width=18, font=("Arial", 11))
        self.user_combo.grid(row=0, column=1, pady=10)
        if display_values:
            found_idx = 0
            if last_user:
                for i, (disp, uname) in enumerate(self.user_map.items()):
                    if uname == last_user:
                        found_idx = i
                        break
            self.user_combo.current(found_idx)
        
        # After choosing a user — clear PIN and move focus straight to it.
        # Switching users cancels OTP mode: the code is bound to the previous
        # user and would never verify for the new one.
        def _on_user_selected(e):
            if getattr(self, 'otp_mode', False):
                self.otp_mode = False
                self.attempts = 0
                for w in getattr(self, 'otp_widgets', []):
                    try:
                        w.destroy()
                    except Exception:
                        pass
                self.otp_widgets = []
                self.pin_entry.config(width=8, show="●")
                vcmd = (self.root.register(self._validate_pin), '%P')
                self.pin_entry.config(validatecommand=vcmd)
                self.error_label.config(text="")
            self.pin_entry.delete(0, tk.END)
            self.pin_entry.focus_set()
        self.user_combo.bind('<<ComboboxSelected>>', _on_user_selected)
        self.user_combo.bind('<Return>', lambda e: self.pin_entry.focus_set())
        
        # PIN
        tk.Label(frame, text="PIN:", font=("Arial", 11), bg=_bg, fg=_fg_sec).grid(row=1, column=0, pady=10, sticky="e", padx=5)
        vcmd = (self.root.register(self._validate_pin), '%P')
        self.pin_entry = tk.Entry(frame, font=("Arial", 18), width=8, show="●", justify="center",
                                   validate="key", validatecommand=vcmd,
                                   bg=_frame_bg, fg=_fg, insertbackground=_fg)
        self.pin_entry.grid(row=1, column=1, pady=10, sticky="w")
        
        # Error / hint label
        self.error_label = tk.Label(self.root, text="", font=("Arial", 10), bg=_bg, fg="#ef5350" if _is_dark else "red")
        self.error_label.pack(pady=5)
        
        # Hint button
        self.hint_btn = tk.Button(self.root, text="Забыли PIN?", font=("Arial", 9), fg=_accent,
                                  bg=_bg, activebackground=_bg, relief="flat", cursor="hand2", command=self._show_hint)
        self.hint_btn.pack()
        
        # Login button
        btn = tk.Button(self.root, text="Войти", command=self._on_login,
                       bg=_btn_color, fg="white", font=("Arial", 13, "bold"),
                       activebackground=_btn_color, activeforeground="white",
                       width=18, height=2, cursor="hand2")
        btn.pack(pady=15)
        
        # Keybindings
        self.root.bind('<Return>', lambda e: self._on_login())
        self.root.bind('<KP_Enter>', lambda e: self._on_login())
        self.root.bind('<Escape>', lambda e: self._on_close())
        
        # Ensure window is on top and focused
        self.root.lift()
        self.root.transient(self.root.master if self.root.master else None)
        self.pin_entry.focus_force()
        
        if self.parent:
            self.parent.wait_window(self.root)
        else:
            self.root.mainloop()
        return self.result
    
    def _validate_pin(self, P):
        """Validation for PIN entries: digits only, max 4."""
        if P == "": return True
        return P.isdigit() and len(P) <= 4

    def _on_login(self):
        display = self.user_var.get()
        username = self.user_map.get(display)
        pin = self.pin_entry.get().strip()
        
        if not username:
            self.error_label.config(text="Выберите пользователя")
            return
            
        # If in OTP mode
        if hasattr(self, 'otp_mode') and self.otp_mode:
            otp = pin # Reuse pin entry for OTP
            if OTPManager.is_blocked():
                self.error_label.config(
                    text="Слишком много неверных попыток. Запросите код заново.")
                return
            if OTPManager.verify(otp, username):
                # OTP proves ownership of the registered email/Telegram —
                # this is the recovery path after a wrong PIN, so the user
                # is logged in without re-checking the failed PIN.
                user = self.users_manager.get_user_by_username(username)
                if user:
                    self.result = user
                    self.root.destroy()
                    return
            else:
                self.error_label.config(text="Неверный код подтверждения")
                self.pin_entry.delete(0, tk.END)
                return

        if len(pin) != 4:
            self.error_label.config(text="Введите 4-значный PIN")
            return
        
        user = self.users_manager.verify_pin(username, pin)
        if user:
            self.result = user
            # Save last user choice
            try:
                last_user_path = os.path.join(os.path.dirname(self.db_path) or ".", "last_user.txt")
                with open(last_user_path, 'w', encoding='utf-8') as f:
                    f.write(username)
            except Exception: pass
            
            self.root.destroy()
        else:
            # Disabled account must get an honest message, not "wrong PIN"/OTP
            disabled = self.users_manager.get_user_by_username(username)
            if disabled and not disabled.get('is_active', 1):
                self.error_label.config(text="Учётная запись отключена. Обратитесь к администратору")
                self.pin_entry.delete(0, tk.END)
                self.pin_entry.focus_set()
                return
            self.attempts += 1
            config = settings.get_integration_settings()
            if self.attempts >= 3 and config.get('require_otp_on_failure'):
                self._trigger_otp(username)
            else:
                self.error_label.config(text=f"Неверный PIN (попытка {self.attempts})")
                self.pin_entry.delete(0, tk.END)
                self.pin_entry.focus_set()

    def _trigger_otp(self, username):
        """Switch to OTP verification mode."""
        self.otp_mode = True
        self.otp_widgets = []
        
        _is_dark = self.root.cget('bg') == '#2d2d2d'
        _orange = '#ff9800' if _is_dark else 'orange'
        _gray = '#808080' if _is_dark else 'gray'
        _bg = self.root.cget('bg')
        self.log_label = tk.Label(self.root, text="🛡️ Требуется подтверждение", font=("Arial", 11, "bold"), fg=_orange,
                                   bg=_bg)
        self.log_label.pack(pady=5)
        self.otp_widgets.append(self.log_label)
        
        self.error_label.config(text="Код подтверждения отправлен на почту", fg=_orange)
        otp_hint = tk.Label(self.root, text="Введите 6-значный код из письма/Telegram", font=("Arial", 9), fg=_gray,
                            bg=_bg)
        otp_hint.pack()
        self.otp_widgets.append(otp_hint)
        
        # Resend button: a fresh code also resets the 5-attempts block
        def resend_otp():
            self.resend_btn.config(state="disabled")
            self.error_label.config(text="Отправляем код заново...", fg=_orange)
            threading.Thread(target=OTPManager.send_to_owner, args=(username,), daemon=True).start()
            self.root.after(3000, lambda: (
                self.resend_btn.config(state="normal") if self.resend_btn.winfo_exists() else None,
                self.error_label.config(text="Код отправлен заново", fg=_orange)
                if self.error_label.winfo_exists() else None))
        
        self.resend_btn = tk.Button(self.root, text="📨 Отправить код заново", font=("Arial", 9), fg=_orange,
                                    bg=_bg, activebackground=_bg, relief="flat", cursor="hand2",
                                    command=resend_otp)
        self.resend_btn.pack(pady=3)
        self.otp_widgets.append(self.resend_btn)
        
        # Adjust UI for 6 digits
        self.pin_entry.config(width=10, show="")
        self.pin_entry.delete(0, tk.END)
        # Re-register validation for 6 digits
        vcmd = (self.root.register(lambda P: P == "" or (P.isdigit() and len(P) <= 6)), '%P')
        self.pin_entry.config(validatecommand=vcmd)
        
        # Send OTP
        threading.Thread(target=OTPManager.send_to_owner, args=(username,), daemon=True).start()
    
    def _show_hint(self):
        display = self.user_var.get()
        username = self.user_map.get(display)
        if username:
            hint = self.users_manager.get_pin_hint(username)
            if hint:
                messagebox.showinfo("Подсказка", f"Подсказка: {hint}")
            else:
                messagebox.showinfo("Подсказка", "Подсказка не задана. Обратитесь к администратору.")
    
    def _on_close(self):
        self.result = None
        self.root.destroy()


# =============================================================================
# DEVICE-TYPE PICKER — first-run choice between "Cashier PC" and "Warehouse PC"
# =============================================================================
class DeviceTypePickerDialog:
    """One-time first-run dialog that classifies this physical PC as either
    a Cashier PC (server/master, owns sales and goods.quantity) or a Warehouse
    PC (client/secondary, arrivals + catalog edits + writeoffs + audits, no
    new-sale creation). Stored in sync_settings.json's `device_type` field.
    Requires an app restart to take full effect (sync topology changes).
    """

    def __init__(self, parent, lang='ru', colors=None):
        self.parent = parent
        self.lang = lang
        self.result = None  # 'cashier' | 'warehouse' | None (dismissed)

    def show(self):
        dialog = tk.Toplevel(self.parent)
        dialog.title(get_text('device_type_title', self.lang))
        dialog.transient(self.parent)
        dialog.grab_set()
        dialog.configure(bg="#ffffff")
        dialog.resizable(False, False)

        # Center
        dialog.update_idletasks()
        w, h = 640, 320
        sw = dialog.winfo_screenwidth()
        sh = dialog.winfo_screenheight()
        dialog.geometry(f"{w}x{h}+{(sw - w) // 2}+{(sh - h) // 2}")

        # Header
        tk.Label(
            dialog,
            text=get_text('choose_device_type', self.lang),
            font=("Segoe UI", 14, "bold"),
            bg="#ffffff", fg="#1f2937",
        ).pack(pady=(20, 8))

        tk.Label(
            dialog,
            text=get_text('device_type_desc', self.lang),
            font=("Segoe UI", 10),
            bg="#ffffff", fg="#4b5563", wraplength=580, justify="center",
        ).pack(pady=(0, 14))

        # Buttons
        btn_frame = tk.Frame(dialog, bg="#ffffff")
        btn_frame.pack(pady=10)

        def _choose(dt):
            self.result = dt
            dialog.destroy()

        tk.Button(
            btn_frame, text=f"💻  {get_text('cashier_server', self.lang)}",
            font=("Segoe UI", 11, "bold"),
            bg="#059669", fg="white", relief="flat", cursor="hand2",
            width=24, pady=10, padx=8,
            command=lambda: _choose('cashier'),
        ).grid(row=0, column=0, padx=8)

        tk.Button(
            btn_frame, text=f"🏭  {get_text('warehouse_receive', self.lang)}",
            font=("Segoe UI", 11, "bold"),
            bg="#2563eb", fg="white", relief="flat", cursor="hand2",
            width=40, pady=10, padx=8,
            command=lambda: _choose('warehouse'),
        ).grid(row=0, column=1, padx=8)

        tk.Label(
            dialog,
            text=get_text('device_type_change_later', self.lang),
            font=("Segoe UI", 9), bg="#ffffff", fg="#6b7280",
        ).pack(pady=(14, 6))

        dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)
        self.parent.wait_window(dialog)
        return self.result


# =============================================================================
# AUTO-SCROLLBAR
# =============================================================================
class AutoScrollbar(tk.Scrollbar):
    """A scrollbar that matches the parent background and hides when not needed."""
    def __init__(self, master, auto_hide=True, **kwargs):
        self.auto_hide = auto_hide
        try:
            parent_bg = master.cget('bg')
        except:
            parent_bg = '#f0f0f0'
        kwargs.setdefault('bg', parent_bg)
        kwargs.setdefault('troughcolor', parent_bg)
        kwargs.setdefault('width', 14)
        super().__init__(master, **kwargs)

    def set(self, lo, hi):
        try:
            l, h = float(lo), float(hi)
        except:
            super().set(lo, hi)
            return
        if self.auto_hide and l <= 0.0 and h >= 0.9999:
            if getattr(self, "base_pack", False):
                self.pack_forget()
            else:
                self.grid_remove()
        else:
            if getattr(self, "base_pack", False):
                if hasattr(self, '_pack_info'):
                    self.pack(**self._pack_info)
                else:
                    self.pack(side="right", fill="y")
            else:
                mgr = self.winfo_manager()
                if mgr == 'grid':
                    self.grid()
                elif mgr == 'pack':
                    self.pack(side="right", fill="y")
        super().set(lo, hi)
        
    def pack(self, **kwargs):
        self._pack_info = kwargs
        self.base_pack = True
        super().pack(**kwargs)
        
    def grid(self, **kwargs):
        self.base_pack = False
        super().grid(**kwargs)

# =============================================================================
# AUTOCOMPLETE ENTRY
# =============================================================================
class AutocompleteEntry(tk.Entry):
    """An Entry widget with a dropdown listbox for autocomplete suggestions."""
    def __init__(self, master, get_results_callback, on_select_callback, list_font, target_tree=None, *args, **kwargs):
        super().__init__(master, *args, **kwargs)
        self.get_results_callback = get_results_callback
        self.on_select_callback = on_select_callback
        self.target_tree = target_tree
        
        self.listbox_window = None
        self.listbox = None
        self.list_font = list_font
        
        self.bind('<KeyRelease>', self._on_keyrelease)
        self.bind('<Down>', self._on_down)
        self.bind('<Up>', self._on_up)
        self.bind('<Return>', self._on_return)
        self.bind('<FocusOut>', self._on_focus_out)
        self.bind('<Escape>', lambda e: self.hide_listbox())

    def _on_keyrelease(self, event):
        if event.keysym in ('Up', 'Down', 'Return', 'Escape', 'Tab'): return
        query = self.get().strip().lower()
        if len(query) < 2:
            self.hide_listbox()
            return
            
        results = self.get_results_callback(query)
        if results:
            self.show_listbox(results)
        else:
            self.hide_listbox()

    def show_listbox(self, results):
        if self.listbox_window is None:
            self.listbox_window = tk.Toplevel(self)
            self.listbox_window.wm_overrideredirect(True)
            self.listbox_window.transient(self.winfo_toplevel())
            
            # Position relative to entry
            x = self.winfo_rootx()
            y = self.winfo_rooty() + self.winfo_height()
            self.listbox_window.wm_geometry(f"+{x}+{y}")
            
            holder = tk.Frame(self.listbox_window)
            holder.pack(fill="both", expand=True)
            self.listbox = tk.Listbox(holder, font=self.list_font, height=min(10, len(results)))
            self.listbox_sb = AutoScrollbar(holder, orient="vertical", command=self.listbox.yview)
            self.listbox.configure(yscrollcommand=self.listbox_sb.set)
            self.listbox.pack(side="left", fill="both", expand=True)
            self.listbox_sb.pack(side="right", fill="y")
            self.listbox.bind('<ButtonRelease-1>', self._on_listbox_select)
            
        self.listbox.delete(0, tk.END)
        for r in results:
            self.listbox.insert(tk.END, r)
            
        self.listbox.config(height=min(10, len(results)))
        
        # Adjust geometry dynamically
        x = self.winfo_rootx()
        y = self.winfo_rooty() + self.winfo_height()
        w = self.winfo_width()
        if w < 200: w = 300  # Minimum width
        self.listbox_window.wm_geometry(f"{w}x{self.listbox.winfo_reqheight()+22}+{x}+{y}")
        self.listbox_sb.set(0.0, 1.0)

    def hide_listbox(self, event=None):
        if self.listbox_window:
            self.listbox_window.destroy()
            self.listbox_window = None
        if event and event.keysym == "Escape":
            self.delete(0, tk.END)
            self.listbox = None

    def _on_down(self, event):
        if self.listbox_window and self.listbox.size() > 0:
            sel = self.listbox.curselection()
            if not sel:
                self.listbox.selection_set(0)
            else:
                idx = sel[0]
                self.listbox.selection_clear(idx)
                if idx < self.listbox.size() - 1:
                    idx += 1
                self.listbox.selection_set(idx)
                self.listbox.see(idx)
            return "break"
        elif self.target_tree:
            self.target_tree.focus_set()
            if self.target_tree.get_children() and not self.target_tree.selection():
                first = self.target_tree.get_children()[0]
                self.target_tree.selection_set(first)
                self.target_tree.see(first)
            return "break"

    def _on_up(self, event):
        if self.listbox_window and self.listbox.size() > 0:
            sel = self.listbox.curselection()
            if sel:
                idx = sel[0]
                self.listbox.selection_clear(idx)
                if idx > 0:
                    idx -= 1
                self.listbox.selection_set(idx)
                self.listbox.see(idx)
            return "break"

    def _on_return(self, event):
        if self.listbox_window and self.listbox.winfo_viewable() and self.listbox.size() > 0:
            if not self.listbox.curselection():
                self.listbox.selection_set(0)
            self._on_listbox_select()
            return "break"
        elif self.on_select_callback:
            self.on_select_callback(self.get())
            return "break"

    def _on_listbox_select(self, event=None):
        if not self.listbox: return
        sel = self.listbox.curselection()
        if not sel: return
        val = self.listbox.get(sel[0])
        self.delete(0, tk.END)
        self.insert(0, val)
        self.hide_listbox()
        if self.on_select_callback:
            self.on_select_callback(val)

    def _on_focus_out(self, event):
        # Increased delay heavily for reliability when single clicking listbox items
        self.after(500, self.hide_listbox)


class ToolTip:
    """Compact popover tooltip with fade-in animation."""
    def __init__(self, widget, text, title='', bg='#2d2d2d', fg='#ffffff', delay_ms=300):
        self.widget = widget
        self.text = text
        self.title = title
        self.bg = bg
        self.fg = fg
        self.delay_ms = delay_ms
        self.tw = None
        self._after_id = None
        widget.bind('<Enter>', self._schedule, add="+")
        widget.bind('<Leave>', self._hide, add="+")

    def _schedule(self, event=None):
        self._hide()
        self._after_id = self.widget.after(self.delay_ms, self._show)

    def _show(self):
        if self.tw:
            return
        tw_width = 304
        x = self.widget.winfo_rootx() - tw_width - 6
        y = self.widget.winfo_rooty()
        if x < 10:
            x = self.widget.winfo_rootx() + self.widget.winfo_width() + 6
        sw = self.widget.winfo_screenwidth()
        if x + tw_width > sw - 10:
            x = sw - tw_width - 10
        self.tw = tk.Toplevel(self.widget)
        self.tw.wm_overrideredirect(True)
        self.tw.wm_geometry(f'+{x}+{y}')
        self.tw.transient(self.widget.winfo_toplevel())

        inner = tk.Frame(self.tw, bg=self.bg, padx=12, pady=10)
        inner.pack()

        if self.title:
            tk.Label(inner, text=self.title, font=('Segoe UI', 10, 'bold'),
                     bg=self.bg, fg=self.fg, anchor='w', wraplength=280).pack(fill='x', pady=(0, 4))
        tk.Label(inner, text=self.text, font=('Segoe UI', 9),
                 bg=self.bg, fg=self.fg, anchor='w', wraplength=280, justify='left').pack(fill='x')

        self.tw.bind('<Enter>', lambda e: self._cancel_hide(), add="+")
        self.tw.bind('<Leave>', self._hide, add="+")
        self.tw.bind('<Button-1>', self._hide, add="+")
        self.widget.bind('<Button-1>', self._hide, add="+")

    def _cancel_hide(self):
        if self._after_id:
            self.widget.after_cancel(self._after_id)
            self._after_id = None

    def _hide(self, event=None):
        if self._after_id:
            self.widget.after_cancel(self._after_id)
            self._after_id = None
        if self.tw:
            try:
                self.tw.destroy()
            except tk.TclError:
                pass
            self.tw = None

    def destroy(self):
        self._hide()
        try:
            self.widget.unbind('<Enter>')
            self.widget.unbind('<Leave>')
        except:
            pass

