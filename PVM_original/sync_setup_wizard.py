# -*- coding: utf-8 -*-
"""
PVM.core - Sync Setup Wizard
==============================
Dialog that lets the operator pick the MEGA-synced folder, run transport
diagnostics and save the configuration (sync_folder_path + sync_enabled).
Opened from Settings → «⚙ Настроить».
"""

import os
import tkinter as tk
from tkinter import filedialog, messagebox

import settings
from sync_engine import run_transport_diagnostics


def _default_folder() -> str:
    try:
        cfg = settings.get_sync_settings()
        path = cfg.get("sync_folder_path", "")
        if path and os.path.isdir(path):
            return path
        mega_default = os.path.join(os.path.expanduser("~"), "MEGA", "PVM_Sync")
        if os.path.isdir(mega_default):
            return mega_default
        return path
    except Exception:
        return ""


def run_sync_wizard(master=None):
    """Show the wizard. Returns True if the folder was configured."""
    result = {"ok": False}
    parent = master if master is not None else None

    dialog = tk.Toplevel(parent) if parent is not None else tk.Toplevel()
    dialog.title("Настройка синхронизации (MEGA)")
    dialog.geometry("640x420")
    dialog.resizable(False, False)
    if parent is not None:
        try:
            dialog.transient(parent)
            dialog.grab_set()
        except Exception:
            pass
    try:
        c = parent.colors if parent is not None else {}
    except Exception:
        c = {}
    bg = c.get("bg", "#f5f5f5")
    fg = c.get("fg", "#222222")
    sub = c.get("fg_secondary", "#666666")
    btn_bg = c.get("bg_tertiary", "#e0e0e0")
    dialog.configure(bg=bg)

    def _lbl(parent_w, text, **kw):
        return tk.Label(parent_w, text=text, bg=bg, fg=kw.pop("color", fg),
                        font=kw.pop("font", ("Arial", 10)), **kw)

    _lbl(dialog, "Папка синхронизации MEGA", font=("Arial", 14, "bold")).pack(pady=(15, 4))
    _lbl(dialog, "1. Установите приложение MEGA на этот компьютер и войдите в аккаунт магазина.",
         color=sub, font=("Arial", 9)).pack(anchor="w", padx=25, pady=(0, 2))
    _lbl(dialog, "2. В настройках MEGA синхронизируйте папку PVM_Sync (или любую свою).",
         color=sub, font=("Arial", 9)).pack(anchor="w", padx=25, pady=(0, 2))
    _lbl(dialog, "3. Укажите её ниже. Все кассы магазина используют одну и ту же папку.",
         color=sub, font=("Arial", 9)).pack(anchor="w", padx=25, pady=(0, 8))

    row = tk.Frame(dialog, bg=bg)
    row.pack(fill="x", padx=25, pady=6)
    folder_var = tk.StringVar(value=_default_folder())
    entry = tk.Entry(row, textvariable=folder_var, font=("Arial", 10))
    entry.pack(side="left", fill="x", expand=True)

    def browse():
        path = filedialog.askdirectory(
            title="Выберите папку синхронизации",
            initialdir=folder_var.get() or os.path.expanduser("~"))
        if path:
            folder_var.set(path)

    tk.Button(row, text="...", command=browse, bg=btn_bg, relief="flat",
              width=3).pack(side="left", padx=(4, 0))

    result_text = tk.Text(dialog, height=7, font=("Menlo", 9) if os.name != "nt"
                          else ("Consolas", 9), bg=bg, fg=fg, state="disabled",
                          relief="flat")
    result_text.pack(fill="x", padx=25, pady=(4, 8))

    def _show_result(lines):
        result_text.config(state="normal")
        result_text.delete("1.0", tk.END)
        result_text.insert(tk.END, "\n".join(lines))
        result_text.config(state="disabled")

    def run_diag():
        folder = folder_var.get().strip()
        if not folder:
            messagebox.showwarning("Внимание", "Сначала укажите папку", parent=dialog)
            return
        checks = run_transport_diagnostics(folder)
        lines = []
        labels = {"write_test": "Запись", "read_test": "Чтение",
                  "delete_test": "Удаление", "cloud_sync": "MEGA-синхронизация"}
        for name, ok, detail in checks:
            lines.append(f"{'✓' if ok else '✗'} {labels.get(name, name)}: {detail}")
        all_ok = all(ok for _, ok, _ in checks[:3])
        lines.append("")
        lines.append("✅ Папка готова к использованию" if all_ok
                     else "⚠ Проверьте указанные ошибки выше")
        _show_result(lines)

    def save():
        folder = folder_var.get().strip()
        if not folder:
            messagebox.showwarning("Внимание", "Укажите папку синхронизации",
                                   parent=dialog)
            return
        if not os.path.isdir(folder):
            messagebox.showerror("Ошибка", "Папка не существует. Создайте её "
                                 "(например, внутри папки MEGA).", parent=dialog)
            return
        settings.update_sync_settings(sync_folder_path=folder, sync_enabled=True)
        result["ok"] = True
        dialog.destroy()

    btn_row = tk.Frame(dialog, bg=bg)
    btn_row.pack(side="bottom", fill="x", padx=25, pady=12)
    tk.Button(btn_row, text="Проверить", command=run_diag, bg=btn_bg,
              relief="flat", padx=14).pack(side="left")
    tk.Button(btn_row, text="Отмена", command=dialog.destroy, bg=btn_bg,
              relief="flat", padx=14).pack(side="right")
    tk.Button(btn_row, text="Сохранить", command=save, bg=c.get("success", "#2e7d32")
              if c else "#2e7d32", fg="white", relief="flat",
              padx=20).pack(side="right", padx=(0, 8))

    if parent is not None:
        try:
            dialog.wait_window()
        except Exception:
            pass
    else:
        dialog.mainloop()
    return result["ok"]
