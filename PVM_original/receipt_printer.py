# -*- coding: utf-8 -*-
"""
PVM.core v2.8.0 - Receipt Printer Module
=========================================
ESC/POS thermal printer support for Kazakhstan receipt standards.
Supports USB-connected thermal printers (58mm and 80mm).

Required fields per KZ law:
- Наименование налогоплательщика
- Дата и время покупки
- ИИН/БИН
- Заводской номер ККМ
- Регистрационный номер ККМ в ОГД
- Порядковый номер чека
- Цена товара и сумма покупки
"""

import os
import sys
import struct
from datetime import datetime

import settings


def _cashier_name(cashier_user):
    """Extract the username from a 'Device/User' audit label for the receipt."""
    if not cashier_user:
        return ''
    return str(cashier_user).split('/')[-1].strip()


# =============================================================================
# ESC/POS COMMAND CONSTANTS
# =============================================================================
ESC = b'\x1b'
GS = b'\x1d'
LF = b'\x0a'

# Initialize
INIT = ESC + b'\x40'

# Text formatting
ALIGN_LEFT = ESC + b'\x61\x00'
ALIGN_CENTER = ESC + b'\x61\x01'
ALIGN_RIGHT = ESC + b'\x61\x02'

FONT_A = ESC + b'\x4d\x00'  # Normal (12x24)
FONT_B = ESC + b'\x4d\x01'  # Small (9x17)

# Text size: width(high nibble) x height(low nibble), 0=1x, 1=2x etc.
SIZE_NORMAL = GS + b'\x21\x00'    # 1x1
SIZE_DOUBLE_H = GS + b'\x21\x01'  # 1x2 height

BOLD_ON = ESC + b'\x45\x01'
BOLD_OFF = ESC + b'\x45\x00'

# Cut paper
CUT_PARTIAL = GS + b'\x56\x01'

# Feed
FEED_LINES = lambda n: ESC + b'\x64' + bytes([n])

# Codepage CP866 for Cyrillic
SET_CP866 = ESC + b'\x74\x11'  # Code page 17 = CP866

# Characters absent from CP866 are transliterated before encoding so that
# they never print as '?' (e.g. the '…' appended to cut product names).
CP866_TEXT_MAP = str.maketrans({
    '…': '...',
    '₸': 'тг',
    '—': '-',
    '–': '-',
    '•': '*',
    '«': '"',
    '»': '"',
    '„': '"',
    '“': '"',
    '”': '"',
    '‘': "'",
    '’': "'",
})


def _fit_name(name, width):
    """Truncate a product name so its CP866-encoded length fits `width` exactly.

    Characters expanded by CP866_TEXT_MAP ('…' -> '...', '₸' -> 'тг') would
    silently overflow a padded row and make the printer wrap the last column;
    the truncation marker is therefore plain ASCII '...', and names that
    expand during encoding are returned pre-translated so padding stays exact.
    """
    name = str(name or '')
    enc = name.translate(CP866_TEXT_MAP).encode('cp866', errors='ignore')
    if len(enc) > width:
        if width <= 3:
            return '...'[:width]
        return enc[:width - 3].decode('cp866', errors='ignore') + '...'
    if len(enc) > len(name):
        return enc.decode('cp866', errors='ignore')
    return name


# =============================================================================
# RECEIPT BUILDER
# =============================================================================
class ReceiptBuilder:
    """Build ESC/POS byte stream for receipt printing."""

    def __init__(self, config=None):
        self.config = config or settings.get_receipt_config()
        self.paper_width = self.config.get('paper_width', 58)
        self.char_width = self.config.get('char_width', 32)
        if self.paper_width >= 80:
            self.char_width = 48
            self.char_width_small = 64
        else:
            self.char_width = 32
            self.char_width_small = 42
        self.buffer = bytearray()
        self._init()

    def _init(self):
        """Initialize printer."""
        self.buffer.extend(INIT)
        # Disable Chinese/Kanji mode (FS .) to prevent CP866 bytes being rendered as Chinese characters
        self.buffer.extend(b'\x1c\x2e')
        self.buffer.extend(SET_CP866)

    def _encode(self, text):
        """Encode text to CP866 for Cyrillic support.

        Characters that do not exist in CP866 (e.g. '…', '₸', '—', '•',
        curly quotes, emoji) are transliterated or dropped instead of
        becoming '?' — otherwise long names cut with '…' would print as '?'.
        """
        try:
            text = str(text).translate(CP866_TEXT_MAP)
            return text.encode('cp866')
        except (UnicodeEncodeError, LookupError):
            # Drop remaining unsupported characters instead of '?'
            return text.encode('cp866', errors='ignore')

    def _align(self, align='left'):
        """Set text alignment."""
        if align == 'center':
            self.buffer.extend(ALIGN_CENTER)
        elif align == 'right':
            self.buffer.extend(ALIGN_RIGHT)
        else:
            self.buffer.extend(ALIGN_LEFT)

    def _size(self, level=1):
        """Set font size: 1=small, 2=normal, 3=large (with text_scale).

        Scale only increases glyph HEIGHT (double-height), never width, so
        every line always fits the paper width regardless of text_scale.
        """
        scale = self.config.get('text_scale', 1.0)
        
        if level <= 1:
            self.buffer.extend(FONT_B)
            self.buffer.extend(SIZE_DOUBLE_H if scale >= 1.2 else SIZE_NORMAL)
        elif level == 2:
            self.buffer.extend(FONT_A)
            self.buffer.extend(SIZE_DOUBLE_H if scale >= 1.3 else SIZE_NORMAL)
        else:
            self.buffer.extend(FONT_A)
            self.buffer.extend(SIZE_DOUBLE_H)

    def _bold(self, on=True):
        self.buffer.extend(BOLD_ON if on else BOLD_OFF)

    def _line(self, text='', align='left', size=2, bold=False):
        """Print a single line."""
        self._align(align)
        self._size(size)
        if bold:
            self._bold(True)
        self.buffer.extend(self._encode(text))
        self.buffer.extend(LF)
        if bold:
            self._bold(False)

    def _separator(self, char='-', align='left'):
        """Print separator line."""
        if align == 'center':
            self.buffer.extend(ALIGN_CENTER)
        elif align == 'right':
            self.buffer.extend(ALIGN_RIGHT)
        else:
            self.buffer.extend(ALIGN_LEFT)
        self._size(1)
        self.buffer.extend(self._encode(char * self.char_width_small))
        self.buffer.extend(LF)

    def _two_columns(self, left, right, size=1):
        """Print two-column text (left-aligned and right-aligned on same line)."""
        self._align('left')
        self._size(size)
        # Calculate available width based on font
        if size <= 1:
            w = self.char_width_small
        elif size == 2:
            w = self.char_width
        else:
            w = self.char_width // 2
        right_len = len(right)
        left_max = w - right_len - 1
        if len(left) > left_max:
            left = left[:left_max]
        spaces = w - len(left) - right_len
        if spaces < 1:
            spaces = 1
        line = left + ' ' * spaces + right
        self.buffer.extend(self._encode(line))
        self.buffer.extend(LF)

    def _logo(self, align='center'):
        """Print logo image if configured."""
        logo_path = self.config.get('logo_path', '')
        if not logo_path or not os.path.exists(logo_path):
            return
        try:
            from PIL import Image
            img = Image.open(logo_path).convert('1')
            max_dots = 384 if self.paper_width < 80 else 576
            if img.width > max_dots:
                ratio = max_dots / img.width
                img = img.resize((max_dots, int(img.height * ratio)))
            w_bytes = (img.width + 7) // 8
            if align == 'center':
                self.buffer.extend(ALIGN_CENTER)
            elif align == 'right':
                self.buffer.extend(ALIGN_RIGHT)
            else:
                self.buffer.extend(ALIGN_LEFT)
            self.buffer.extend(GS + b'\x76\x30\x00')
            self.buffer.extend(struct.pack('<HH', w_bytes, img.height))
            pixels = img.load()
            for y in range(img.height):
                row = bytearray(w_bytes)
                for x in range(img.width):
                    if pixels[x, y] == 0:
                        row[x // 8] |= (0x80 >> (x % 8))
                self.buffer.extend(row)
        except Exception as e:
            print(f"Logo print error: {e}")

    def build_receipt(self, receipt_data):
        """
        Build complete receipt from receipt data dict.
        
        receipt_data = {
            'number': int,
            'datetime': str (ISO),
            'items': [{'name', 'quantity', 'price', 'sum'}],
            'subtotal': float,
            'discount': float,
            'total': float,
            'payment': {'cash': float, 'card': float, 'internal': float, 'change': float},
            'partner_id': str or None,
            'partner_name': str or None,
        }
        """
        cfg = self.config
        block_order = cfg.get('block_order', settings.DEFAULT_RECEIPT_CONFIG['block_order'])
        font_sizes = cfg.get('block_font_sizes', settings.DEFAULT_RECEIPT_CONFIG['block_font_sizes'])
        aligns = cfg.get('block_align', settings.DEFAULT_RECEIPT_CONFIG['block_align'])

        dt = datetime.fromisoformat(receipt_data['datetime'])
        
        block_builders = {
            'logo': lambda: self._logo(aligns.get('logo', 'center')),
            'taxpayer': lambda: self._line(
                cfg.get('taxpayer_name', ''), 
                aligns.get('taxpayer', 'center'), 
                font_sizes.get('taxpayer', 2), bold=True),
            'address': lambda: self._line(
                cfg.get('address', ''),
                aligns.get('address', 'center'),
                font_sizes.get('address', 1)) if cfg.get('address') else None,
            'separator1': lambda: self._separator(align=aligns.get('separator1', 'left')),
            'separator2': lambda: self._separator(align=aligns.get('separator2', 'left')),
            'separator3': lambda: self._separator(align=aligns.get('separator3', 'left')),
            'separator4': lambda: self._separator(align=aligns.get('separator4', 'left')),
            'datetime': lambda: self._line(
                dt.strftime("%d.%m.%Y  %H:%M:%S"),
                aligns.get('datetime', 'left'),
                font_sizes.get('datetime', 1)),
            'receipt_number': lambda: self._line(
                f"Чек №{receipt_data['number']}",
                aligns.get('receipt_number', 'left'),
                font_sizes.get('receipt_number', 2), bold=True),
            'cashier_info': lambda: self._line(
                f"Кассир: {_cashier_name(receipt_data.get('cashier_user', ''))}",
                aligns.get('receipt_number', 'left'),
                font_sizes.get('receipt_number', 1)) if receipt_data.get('cashier_user') else None,
            'kkm_info': lambda: self._build_kkm_info(cfg, font_sizes, aligns),
            'partner_info': lambda: self._build_partner_info(receipt_data, font_sizes, aligns),
            'items_table': lambda: self._build_items_table(
                receipt_data['items'], font_sizes.get('items_table', 1), aligns.get('items_table', 'left')),
            'totals': lambda: self._build_totals(receipt_data, font_sizes, aligns),
            'payment_info': lambda: self._build_payment_info(
                receipt_data.get('payment', {}), font_sizes, aligns),
            'footer': lambda: self._line(
                cfg.get('footer_text', ''),
                aligns.get('footer', 'center'),
                font_sizes.get('footer', 1)),
        }

        for block_id in block_order:
            # Handle standard blocks
            builder = block_builders.get(block_id)
            if builder:
                builder()
                continue
            
            # Handle dynamic blocks: line separators
            if str(block_id).startswith('separator'):
                self._separator(align=aligns.get(str(block_id), 'left'))
                continue
                
            # Handle dynamic blocks: empty spaces (indents)
            if str(block_id).startswith('space_sep'):
                self.buffer.extend(LF)
                continue

        # Feed and cut
        self.buffer.extend(FEED_LINES(4))
        if cfg.get('auto_cut', True):
            self.buffer.extend(CUT_PARTIAL)

        return bytes(self.buffer)

    def _build_kkm_info(self, cfg, font_sizes, aligns):
        """Print taxpayer identification info."""
        size = font_sizes.get('kkm_info', 1)
        align = aligns.get('kkm_info', 'left')
        
        iin = cfg.get('iin_bin', '')
        if iin:
            self._line(f"ИИН/БИН: {iin}", align, size)

    def _build_items_table(self, items, size=1, align='left'):
        """Print items table with fixed column alignment."""
        self._size(size)
        if align == 'center':
            self.buffer.extend(ALIGN_CENTER)
        elif align == 'right':
            self.buffer.extend(ALIGN_RIGHT)
        else:
            self.buffer.extend(ALIGN_LEFT)
        show_pv = self.config.get('show_pv', True)
        item_layout = self.config.get('item_layout', 'compact')
        
        if item_layout == 'wide':
            if size <= 1:
                cw = self.char_width_small
            elif size == 2:
                cw = self.char_width
            else:
                cw = self.char_width // 2
            
            if cw >= 42:
                if show_pv:
                    name_w = cw - 4 - 7 - 3 - 7 - 4
                    col = {'name': name_w, 'pv': 4, 'price': 7, 'qty': 3, 'total': 7}
                    hdr = f'{"Наименование":<{col["name"]}} {"PV":>{col["pv"]}} {"Цена":>{col["price"]}} {"Кол":>{col["qty"]}} {"Сумма":>{col["total"]}}'
                else:
                    name_w = cw - 7 - 3 - 7 - 3
                    col = {'name': name_w, 'price': 7, 'qty': 3, 'total': 7}
                    hdr = f'{"Наименование":<{col["name"]}} {"Цена":>{col["price"]}} {"Кол":>{col["qty"]}} {"Сумма":>{col["total"]}}'
                self._line(hdr, 'left', size, bold=True)
                self._separator()
                
                for item in items:
                    name = item['name']
                    qty = item['quantity']
                    price = item['price']
                    total = item['sum']
                    pv = item.get('pv', 0) or 0
                    
                    name_col = _fit_name(name, col['name'])
                    if show_pv:
                        pv_total = (pv * qty) if pv > 0 else 0
                        row = f'{name_col:<{col["name"]}} {pv_total:>{col["pv"]}} {fmt_amount(price, col["price"])} {fmt_qty(qty):>{col["qty"]}} {fmt_amount(total, col["total"])}'
                    else:
                        row = f'{name_col:<{col["name"]}} {fmt_amount(price, col["price"])} {fmt_qty(qty):>{col["qty"]}} {fmt_amount(total, col["total"])}'
                    self._line(row, 'left', size)
        else:
            # Compact: name on line 1, details on line 2 (32 chars)
            for item in items:
                name = item['name']
                qty = item['quantity']
                price = item['price']
                total = item['sum']
                pv = item.get('pv', 0) or 0
                
                self._line(name, 'left', size)
                if show_pv:
                    pv_total = (pv * qty) if pv > 0 else 0
                    row = f'  {pv_total:>4} {fmt_amount(price, 7)} {fmt_qty(qty):>3} {fmt_amount(total, 7)}'
                else:
                    row = f'  {fmt_amount(price, 7)} {fmt_qty(qty):>3} {fmt_amount(total, 7)}'
                self._line(row, 'left', size)

    def _build_totals(self, receipt_data, font_sizes, aligns):
        """Print totals section respecting block alignment."""
        size = font_sizes.get('totals', 2)
        align = aligns.get('totals', 'left')
        
        if receipt_data.get('discount', 0) > 0:
            if align == 'right':
                self._line(f"Подытог: {fmt_amount(receipt_data['subtotal'])}", 'right', 1)
                self._line(f"Скидка: -{fmt_amount(receipt_data['discount'])}", 'right', 1)
            elif align == 'center':
                self._line(f"Подытог: {fmt_amount(receipt_data['subtotal'])}", 'center', 1)
                self._line(f"Скидка: -{fmt_amount(receipt_data['discount'])}", 'center', 1)
            else:
                self._two_columns("Подытог:", fmt_amount(receipt_data['subtotal']), 1)
                self._two_columns("Скидка:", f"-{fmt_amount(receipt_data['discount'])}", 1)
        
        self._bold(True)
        total_val = fmt_amount(receipt_data['total'])
        show_pv = self.config.get('show_pv', True)
        pv_str = ''
        if show_pv:
            items = receipt_data.get('items', [])
            total_pv = sum((item.get('pv', 0) or 0) * item.get('quantity', 1) for item in items)
            if total_pv > 0:
                pv_str = f"{total_pv:g}" if total_pv == int(total_pv) else f"{total_pv:.2f}"
        
        if pv_str:
            if self.char_width >= 42:
                itogo_line = f"PV: {pv_str}  |  Итоговая сумма: {total_val}"
                self._line(itogo_line, align, size, bold=True)
            else:
                self._line(f"PV: {pv_str}", align, 1)
                self._bold(False)
                self._line(f"Итоговая сумма: {total_val}", align, size, bold=True)
                return
        else:
            if align == 'right':
                self._line(f"Итоговая сумма: {total_val}", 'right', size, bold=True)
            elif align == 'center':
                self._line(f"Итоговая сумма: {total_val}", 'center', size, bold=True)
            else:
                self._two_columns("Итоговая сумма:", total_val, size)
        self._bold(False)

    def _build_partner_info(self, receipt_data, font_sizes, aligns):
        """Print partner info if configured and available."""
        if not self.config.get('show_partner', True):
            return
            
        p_name = receipt_data.get('partner_name')
        p_id = receipt_data.get('partner_id')
        p_phone = receipt_data.get('partner_phone')
        
        if not p_name and not p_id:
            return
            
        size = font_sizes.get('partner_info', 1)
        align = aligns.get('partner_info', 'left')
        
        display_name = p_name or "Клиент"
        display_id = str(p_id) if p_id else ""
        
        if self.config.get('partial_id', False) and display_id:
            # Mask ID: initials + ******last2
            masked_id = "*" * (len(display_id) - 2) + display_id[-2:] if len(display_id) > 2 else display_id
            parts = display_name.split()
            initials = "".join([p[0].upper() + "." for p in parts if p])
            self._line(f"Id/ФИО: {initials} {masked_id}", align, size)
        else:
            if display_id:
                self._line(f"Id/ФИО: {display_id} {display_name}", align, size)
            else:
                self._line(f"Партнер: {display_name}", align, size)
        
        # Phone — only if enabled in config and present in data
        if self.config.get('show_partner_phone', False) and p_phone:
            self._line(f"Тел: {p_phone}", align, size)

    def _build_payment_info(self, payment, font_sizes, aligns):
        """Print payment details respecting block alignment."""
        size = font_sizes.get('payment_info', 1)
        align = aligns.get('payment_info', 'left')
        
        cash = payment.get('cash', 0)
        card = payment.get('card', 0)
        internal = payment.get('internal', 0)
        change = payment.get('change', 0)
        
        if align == 'right':
            if cash > 0:
                self._line(f"Наличные: {fmt_amount(cash)}", 'right', size)
            if card > 0:
                self._line(f"Карта: {fmt_amount(card)}", 'right', size)
            if internal > 0:
                self._line(f"Баланс: {fmt_amount(internal)}", 'right', size)
            if change > 0:
                self._line(f"Сдача: {fmt_amount(change)}", 'right', size)
        elif align == 'center':
            if cash > 0:
                self._line(f"Наличные: {fmt_amount(cash)}", 'center', size)
            if card > 0:
                self._line(f"Карта: {fmt_amount(card)}", 'center', size)
            if internal > 0:
                self._line(f"Баланс: {fmt_amount(internal)}", 'center', size)
            if change > 0:
                self._line(f"Сдача: {fmt_amount(change)}", 'center', size)
        else:
            if cash > 0:
                self._two_columns("Наличные:", fmt_amount(cash), size)
            if card > 0:
                self._two_columns("Карта:", fmt_amount(card), size)
            if internal > 0:
                self._two_columns("Баланс:", fmt_amount(internal), size)
            if change > 0:
                self._two_columns("Сдача:", fmt_amount(change), size)


# =============================================================================
# PRINTER DISCOVERY & PRINTING
# =============================================================================
def find_usb_printers():
    """Find available printers on Windows using PowerShell (primary) or WMIC (fallback)."""
    printers = []
    
    if sys.platform != 'win32':
        return printers

    # Keywords indicating thermal/POS printers
    THERMAL_KEYWORDS = (
        'pos', 'thermal', 'receipt', 'esc', 'xprinter',
        'epson', 'star', 'citizen', 'bixolon', 'sewoo', 'cashino'
    )

    def _classify(name, driver):
        combined = (name + driver).lower()
        return 'usb_pos' if any(k in combined for k in THERMAL_KEYWORDS) else 'generic'

    import subprocess

    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    CREATE_NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0x08000000)

    # --- Primary: PowerShell Get-Printer ---
    try:
        result = subprocess.run(
            ['powershell', '-NoProfile', '-NonInteractive', '-Command',
             'Get-Printer | Select-Object Name, PortName, DriverName | ConvertTo-Csv -NoTypeInformation'],
            capture_output=True, text=True, timeout=15,
            startupinfo=si, creationflags=CREATE_NO_WINDOW,
        )
        if result.returncode == 0 and result.stdout.strip():
            lines = result.stdout.strip().splitlines()
            for line in lines[1:]:  # skip CSV header
                parts = line.replace('"', '').split(',')
                if len(parts) >= 1:
                    name = parts[0].strip()
                    port = parts[1].strip() if len(parts) > 1 else ''
                    driver = parts[2].strip() if len(parts) > 2 else ''
                    if name:
                        printers.append({
                            'name': name, 'port': port, 'driver': driver,
                            'type': _classify(name, driver)
                        })
            if printers:
                return printers
    except Exception:
        pass  # fall through to WMIC

    # --- Fallback: WMIC ---
    try:
        result = subprocess.run(
            ['wmic', 'printer', 'get', 'Name,PortName,DriverName', '/FORMAT:CSV'],
            capture_output=True, text=True, timeout=10,
            startupinfo=si, creationflags=CREATE_NO_WINDOW,
        )
        if result.returncode == 0:
            for line in result.stdout.strip().splitlines()[2:]:
                parts = line.strip().split(',')
                if len(parts) >= 4:
                    driver = parts[1].strip()
                    name = parts[2].strip()
                    port = parts[3].strip()
                    if name:
                        printers.append({
                            'name': name, 'port': port, 'driver': driver,
                            'type': _classify(name, driver)
                        })
    except Exception:
        pass

    return printers


def print_receipt_win32(printer_name, data_bytes):
    """Print raw bytes to a Windows printer (ESC/POS)."""
    if sys.platform != 'win32':
        raise OSError("Win32 printing only supported on Windows")
    
    # Try win32print first (preferred, most reliable)
    try:
        import win32print
    except ImportError:
        # Fallback to COPY command if pywin32 is definitely not installed
        # This will only work if the printer is shared in Windows settings
        return _print_raw_fallback(printer_name, data_bytes)
        
    try:
        hprinter = win32print.OpenPrinter(printer_name)
    except Exception as e:
        raise Exception(f"Не удалось открыть принтер '{printer_name}' (win32print winerror: {e})")
        
    try:
        hjob = win32print.StartDocPrinter(hprinter, 1, ("PVM Receipt", None, "RAW"))
        win32print.StartPagePrinter(hprinter)
        win32print.WritePrinter(hprinter, data_bytes)
        win32print.EndPagePrinter(hprinter)
        win32print.EndDocPrinter(hprinter)
        return True
    except Exception as e:
        raise Exception(f"Ошибка отправки данных (win32print winerror: {e})")
    finally:
        win32print.ClosePrinter(hprinter)


def _print_raw_fallback(printer_name, data_bytes):
    """Fallback: write raw bytes via copy command (Windows)."""
    import tempfile

    try:
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.bin')
        tmp.write(data_bytes)
        tmp.close()
        
        import subprocess
        # Use COPY command without showing a console window on Windows
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        result = subprocess.run(
            f'copy /b "{tmp.name}" "\\\\localhost\\{printer_name}"',
            shell=True,
            capture_output=True,
            timeout=15,
            startupinfo=startupinfo,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        os.unlink(tmp.name)
        return result.returncode == 0
    except Exception as e:
        print(f"Fallback print error: {e}")
        return False


def print_receipt(receipt_data, config=None):
    """
    Main entry: build and print receipt.
    Returns (success: bool, error_message: str or None)
    """
    if config is None:
        config = settings.get_receipt_config()
    
    printer_name = config.get('printer_name', '')
    if not printer_name:
        return False, "Принтер не выбран. Настройте принтер в разделе Настройки → Чек."
    
    try:
        builder = ReceiptBuilder(config)
        data = builder.build_receipt(receipt_data)
        
        if sys.platform == 'win32':
            success = print_receipt_win32(printer_name, data)
        else:
            success = False
        
        if success:
            return True, None
        else:
            return False, "Не удалось отправить данные на принтер."
    except Exception as e:
        return False, f"Ошибка печати: {e}"


def fmt_amount(val, w=None):
    """Format a money value as a whole number (no decimals)."""
    _gs = settings.load_settings()
    lang = _gs.get('language', 'ru')
    try:
        v = float(val)
    except (ValueError, TypeError):
        t = str(val)
        return t.rjust(w) if w else t
    s = f"{int(round(v)):,}"
    if lang == 'ru':
        s = s.replace(',', ' ')
    if w and len(s) > w:
        s = s[-w:]  # never let a value stretch the row beyond its column
    return s.rjust(w) if w else s


def fmt_qty(val):
    """Format a quantity — drop .0 if the value is a whole number."""
    try:
        v = round(float(val), 3)
        return str(int(v)) if v == int(v) else f"{v:g}"
    except (ValueError, TypeError):
        return str(val)


def generate_preview_text(receipt_data, config=None):
    """
    Generate plain-text preview of receipt (for UI preview).
    Returns list of (text, is_bold) tuples. Every line is laid out in
    NORMAL-font character units (48 for 80mm / 32 for 58mm): lines the
    printer renders with the narrow font (FONT_B) are rebuilt with columns
    scaled proportionally, so all lines end exactly at the paper edge.
    """
    if config is None:
        config = settings.get_receipt_config()
    
    w = config.get('char_width', 32)
    if config.get('paper_width', 58) >= 80:
        w = 48
        w_small = 64
    else:
        w = 32
        w_small = 42
    ratio = w_small / w  # FONT_B chars per FONT_A char
    
    item_layout = config.get('item_layout', 'compact')
    block_font_sizes = config.get('block_font_sizes', settings.DEFAULT_RECEIPT_CONFIG.get('block_font_sizes', {}))
    item_size = block_font_sizes.get('items_table', 1)
    block_order = config.get('block_order', settings.DEFAULT_RECEIPT_CONFIG['block_order'])
    lang = settings.load_settings().get('language', 'ru')
    lines: list[tuple[str, bool]] = []
    
    def _add(text, bold=False):
        lines.append((text, bold))

    def center(text):
        return text.center(w)
    
    def right(text):
        return text.rjust(w)
    
    def two_col(left, right_text, width=None):
        cw = width or w
        left = left if len(left) <= cw else left[:cw]
        max_r = max(1, cw - len(left) - 1)
        if len(right_text) > max_r:
            right_text = right_text[-max_r:]
        spaces = cw - len(left) - len(right_text)
        if spaces < 1:
            spaces = 1
        return left + ' ' * spaces + right_text
    
    def sep(char='-'):
        return char * w

    def _cell(val, width):
        """Right-aligned cell that never exceeds the column width."""
        s = str(val)
        if len(s) > width:
            s = s[-width:]
        return s.rjust(width)
    
    def _scaled_cols(small_cols):
        """Scale FONT_B column widths to normal-font units (keeps physical proportions)."""
        return tuple(max(2, int(c * ratio)) for c in small_cols)
    
    dt = datetime.fromisoformat(receipt_data.get('datetime', datetime.now().isoformat()))
    
    block_align = config.get('block_align', settings.DEFAULT_RECEIPT_CONFIG['block_align'])
    
    for block_id in block_order:
        align = block_align.get(block_id, 'left')
        
        def apply_align(text):
            if align == 'center': return center(text)
            if align == 'right':
                if ':' in text:
                    label, val = text.split(':', 1)
                    label = label.strip() + ":"
                    val = val.strip()
                    return two_col(label, val)
                return right(text)
            return text
            
        if block_id == 'logo':
            if config.get('logo_path'):
                _add(apply_align('[ЛОГОТИП]'))
        elif block_id == 'taxpayer':
            name = config.get('taxpayer_name', '')
            if name:
                _add(apply_align(name), bold=True)
        elif block_id == 'address':
            addr = config.get('address', '')
            if addr:
                _add(apply_align(addr))
        elif block_id.startswith('separator'):
            _add(sep())
        elif block_id.startswith('space_sep'):
            _add("")
        elif block_id == 'datetime':
            _add(apply_align(dt.strftime("%d.%m.%Y  %H:%M:%S")))
        elif block_id == 'receipt_number':
            num = receipt_data.get('number', '1')
            _add(apply_align(f"Чек №{num}"), bold=True)
        elif block_id == 'cashier_info':
            cashier = _cashier_name(receipt_data.get('cashier_user', ''))
            if cashier:
                _add(apply_align(f"Кассир: {cashier}"))
        elif block_id == 'kkm_info':
            iin = config.get('iin_bin', '')
            if iin:
                _add(apply_align(f"ИИН/БИН: {iin}"))
        elif block_id == 'items_table':
            show_pv = config.get('show_pv', True)
            items_small = item_size <= 1
            if item_layout == 'wide':
                # Rebuild FONT_B rows in normal-font units, scaling columns by
                # ratio so proportions match the physical paper
                if show_pv:
                    pv_w, price_w, qty_w, total_w = _scaled_cols((4, 7, 3, 7))
                    name_w = w - pv_w - price_w - qty_w - total_w - 4
                    col = {'name': name_w, 'pv': pv_w, 'price': price_w, 'qty': qty_w, 'total': total_w}
                    hdr = f'{"Наименование":<{col["name"]}} {"PV":>{col["pv"]}} {"Цена":>{col["price"]}} {"Кол":>{col["qty"]}} {"Сумма":>{col["total"]}}'
                else:
                    price_w, qty_w, total_w = _scaled_cols((7, 3, 7))
                    name_w = w - price_w - qty_w - total_w - 3
                    col = {'name': name_w, 'price': price_w, 'qty': qty_w, 'total': total_w}
                    hdr = f'{"Наименование":<{col["name"]}} {"Цена":>{col["price"]}} {"Кол":>{col["qty"]}} {"Сумма":>{col["total"]}}'
                _add(apply_align(hdr) if align != 'left' else hdr, bold=True)
                _add('-' * w)
                for item in receipt_data.get('items', []):
                    name = item['name']
                    qty = item['quantity']
                    price = item['price']
                    total = item['sum']
                    pv = item.get('pv', 0) or 0
                    name_col = name if len(name) <= col['name'] else name[:col['name']-3] + '...'
                    if show_pv:
                        pv_total = (pv * qty) if pv > 0 else 0
                        row = f'{name_col:<{col["name"]}} {_cell(pv_total, col["pv"])} {fmt_amount(price, col["price"])} {_cell(fmt_qty(qty), col["qty"])} {fmt_amount(total, col["total"])}'
                    else:
                        row = f'{name_col:<{col["name"]}} {fmt_amount(price, col["price"])} {_cell(fmt_qty(qty), col["qty"])} {fmt_amount(total, col["total"])}'
                    _add(apply_align(row) if align != 'left' else row)
            else:
                for item in receipt_data.get('items', []):
                    name = item['name']
                    qty = item['quantity']
                    price = item['price']
                    total = item['sum']
                    pv = item.get('pv', 0) or 0
                    _add(apply_align(name) if align != 'left' else name)
                    if show_pv:
                        pv_total = (pv * qty) if pv > 0 else 0
                        row = f'  {_cell(pv_total, 4)} {fmt_amount(price, 7)} {_cell(fmt_qty(qty), 3)} {fmt_amount(total, 7)}'
                    else:
                        row = f'  {fmt_amount(price, 7)} {_cell(fmt_qty(qty), 3)} {fmt_amount(total, 7)}'
                    _add(apply_align(row) if align != 'left' else row)
        elif block_id == 'totals':
            disc = receipt_data.get('discount', 0)
            if disc > 0:
                sub_amt = fmt_amount(receipt_data.get('subtotal', 0))
                disc_amt = fmt_amount(disc)
                if align != 'left':
                    _add(two_col("Подытог:", sub_amt))
                    _add(two_col("Скидка:", f"-{disc_amt}"))
                else:
                    _add(two_col("Подытог:", sub_amt))
                    _add(two_col("Скидка:", f"-{disc_amt}"))
            total_val = fmt_amount(receipt_data['total'])
            show_pv = config.get('show_pv', True)
            pv_str = ''
            if show_pv:
                total_pv = sum((it.get('pv', 0) or 0) * it.get('quantity', 1) for it in receipt_data.get('items', []))
                if total_pv > 0:
                    pv_str = f"{total_pv:g}" if total_pv == int(total_pv) else f"{total_pv:.2f}"
            if pv_str:
                if w >= 42:
                    itogo_line = f"PV: {pv_str}  |  Итоговая сумма: {total_val}"
                    _add(apply_align(itogo_line) if align != 'left' else itogo_line, bold=True)
                else:
                    if align != 'left':
                        _add(apply_align(f"PV: {pv_str}"))
                        _add(apply_align(f"Итоговая сумма: {total_val}"), bold=True)
                    else:
                        _add(f"PV: {pv_str}")
                        _add(two_col("Итоговая сумма:", total_val), bold=True)
            else:
                if align != 'left':
                    _add(apply_align(f"Итоговая сумма: {total_val}"), bold=True)
                else:
                    _add(two_col("Итоговая сумма:", total_val), bold=True)
        elif block_id == 'partner_info':
            if config.get('show_partner', True):
                p_name = receipt_data.get('partner_name')
                p_id = receipt_data.get('partner_id')
                p_phone = receipt_data.get('partner_phone')
                if p_name or p_id:
                    display_name = p_name or "Клиент"
                    display_id = str(p_id) if p_id else ""
                    if config.get('partial_id', False) and display_id:
                        masked_id = "*" * (len(display_id)-2) + display_id[-2:] if len(display_id)>2 else display_id
                        parts = display_name.split()
                        initials = "".join([p[0].upper() + "." for p in parts if p])
                        _add(apply_align(f"Id/ФИО: {initials} {masked_id}"))
                    else:
                        if display_id:
                            _add(apply_align(f"Id/ФИО: {display_id} {display_name}"))
                        else:
                            _add(apply_align(f"Партнер: {display_name}"))
                    if config.get('show_partner_phone', False) and p_phone:
                        _add(apply_align(f"Тел: {p_phone}"))
        elif block_id == 'payment_info':
            payment = receipt_data.get('payment', {})
            for p_key, p_label in [('cash', 'Наличные:'), ('card', 'Карта:'), ('internal', 'Баланс:'), ('change', 'Сдача:')]:
                val = payment.get(p_key, 0)
                if val > 0:
                    p_val = fmt_amount(val)
                    if align != 'left':
                        _add(two_col(p_label, p_val))
                    else:
                        _add(two_col(p_label, p_val))
        elif block_id == 'footer':
            footer = config.get('footer_text', '')
            if footer:
                _add(apply_align(footer))
    
    return lines
