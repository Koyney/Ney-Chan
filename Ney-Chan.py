# pylint: disable=line-too-long,too-many-lines
"""Ney-Chan — Téléchargeur d'animes depuis Anime-Sama.
Console uniquement. Compatible Windows, Linux, Termux/Android.
"""

import os
import sys
import re
import json
import time
import signal
import shutil
import tempfile
import traceback
import subprocess

VERSION    = "1.0"
_BASE_DIR  = os.path.dirname(os.path.abspath(__file__))

# ── Détections plateforme ─────────────────────────────────────────────────────
def _is_termux():
    return os.name != "nt" and (
        "ANDROID_STORAGE" in os.environ
        or "com.termux" in os.environ.get("PREFIX", "")
        or os.path.isdir("/storage/emulated/0")
    )

IS_TERMUX  = _is_termux()
IS_WINDOWS = os.name == "nt"
IS_PC      = not IS_TERMUX   # PC = tout sauf Termux/Android

# Callback de progression pour l'interface Qt (None = mode console)
_qt_progress_callback = None  # pylint: disable=invalid-name

try:
    import msvcrt
except ImportError:
    msvcrt = None

try:
    import tty, termios
    import select as _select
except ImportError:
    tty = termios = _select = None


# ── Chargement .env manuel ────────────────────────────────────────────────────
def _load_env():
    env_path = os.path.join(_BASE_DIR, ".env")
    if not os.path.exists(env_path):
        return
    with open(env_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = val

_load_env()

# ── Constantes ────────────────────────────────────────────────────────────────
GITHUB_REPO    = "Koyney/Anime-Sama"
GITHUB_BRANCH  = "main"
GITHUB_TOKEN   = os.environ.get("GITHUB_TOKEN", "")

LOCAL_DB_DIR   = os.path.join(_BASE_DIR, "anime-sama")
LOCAL_IDX_FILE = os.path.join(LOCAL_DB_DIR, "index.json")
LOCAL_ANI_DIR  = os.path.join(LOCAL_DB_DIR, "animes")

def _default_db_dir():
    return os.path.join(_BASE_DIR, "anime-sama")

def _set_db_dir(path):
    """Met à jour les chemins de la base de données locale."""
    global LOCAL_DB_DIR, LOCAL_IDX_FILE, LOCAL_ANI_DIR  # pylint: disable=global-statement
    LOCAL_DB_DIR   = path
    LOCAL_IDX_FILE = os.path.join(path, "index.json")
    LOCAL_ANI_DIR  = os.path.join(path, "animes")

def init_db_dir(cfg):
    """Initialise le chemin de la base de données depuis la config."""
    saved = cfg.get("db_dir", "")
    if saved:
        _set_db_dir(saved)
    else:
        _set_db_dir(_default_db_dir())

ALL_LANGUAGES  = ["vf", "vostfr", "va", "vkr", "vcn", "vqc",
                  "vf1", "vf2", "vf3", "vf4", "vf5"]

LANG_LABELS = {
    "vf":     "VF — Version Française",
    "vostfr": "VOSTFR — Sous-titres Français",
    "va":     "VA — Version Anglaise (dub)",
    "vkr":    "VKR — Version Coréenne",
    "vcn":    "VCN — Version Chinoise",
    "vqc":    "VQC — Version Québécoise",
    "vf1": "VF1", "vf2": "VF2", "vf3": "VF3", "vf4": "VF4", "vf5": "VF5",
}


# ══════════════════════════════════════════════════════════════════════════════
#  ConsoleUI
# ══════════════════════════════════════════════════════════════════════════════
class ConsoleUI:
    RESET   = "\033[0m"
    BOLD    = "\033[1m"
    DIM     = "\033[2m"
    RED     = "\033[31m"
    GREEN   = "\033[32m"
    YELLOW  = "\033[33m"
    CYAN    = "\033[36m"
    MAGENTA = "\033[35m"
    WHITE   = "\033[97m"
    BG_HDR  = "\033[48;5;17m"   # fond bleu nuit pour en-têtes

    BANNER = "\033[36m" + r"""
╔═════════════════════════════════════════════════════════════════════════╗
║                                                                         ║
║  ███╗   ██╗███████╗██╗   ██╗        ██████╗██╗  ██╗ █████╗ ███╗   ██╗   ║
║  ████╗  ██║██╔════╝╚██╗ ██╔╝       ██╔════╝██║  ██║██╔══██╗████╗  ██║   ║
║  ██╔██╗ ██║█████╗   ╚████╔╝ █████╗ ██║     ███████║███████║██╔██╗ ██║   ║
║  ██║╚██╗██║██╔══╝    ╚██╔╝  ╚════╝ ██║     ██╔══██║██╔══██║██║╚██╗██║   ║
║  ██║ ╚████║███████╗   ██║          ╚██████╗██║  ██║██║  ██║██║ ╚████║   ║
║  ╚═╝  ╚═══╝╚══════╝   ╚═╝           ╚═════╝╚═╝  ╚═╝╚═╝  ╚═╝╚═╝  ╚═══╝   ║
║                                                                         ║
║              🎌  NEY-CHAN  ANIME DOWNLOADER  v1.0  🎌                   ║
║                                                                         ║
╚═════════════════════════════════════════════════════════════════════════╝""" + "\033[0m"

    # Bannière légère pour Termux (sans ASCII art)
    TERMUX_BANNER = (
        "\033[36m\n  " + "─" * 54 + "\033[0m\n"
        "  \033[1m\033[36m  🎌  NEY-CHAN  ·  ANIME DOWNLOADER  🎌\033[0m\n"
        "\033[36m  " + "─" * 54 + "\033[0m"
    )

    MAX_VISIBLE = 8

    @staticmethod
    def enable_ansi():
        if IS_WINDOWS:
            try:
                import ctypes
                ctypes.windll.kernel32.SetConsoleMode(
                    ctypes.windll.kernel32.GetStdHandle(-11), 7)
            except Exception:
                pass

    @staticmethod
    def clear():
        os.system("cls" if IS_WINDOWS else "clear")

    @staticmethod
    def display_len(s):
        count = 0
        for ch in s:
            cp = ord(ch)
            if cp in (0xFE0E, 0xFE0F, 0x200D, 0x20E3): continue
            if 0x0300 <= cp <= 0x036F: continue
            wide = (0x1F000 <= cp <= 0x1FFFF or 0x2600 <= cp <= 0x27BF
                    or 0x2B00 <= cp <= 0x2BFF or 0xFE30 <= cp <= 0xFE4F
                    or 0x2E80 <= cp <= 0x2EFF or 0x3000 <= cp <= 0x9FFF
                    or 0xF900 <= cp <= 0xFAFF or 0xAC00 <= cp <= 0xD7AF)
            count += 2 if wide else 1
        return count

    @staticmethod
    def show_menu(options, title="MENU", selected_index=0, subtitle=""):
        box_w   = 62
        ConsoleUI.clear()
        print(ConsoleUI.BANNER)
        if subtitle:
            print(f"\n  {ConsoleUI.DIM}{subtitle}{ConsoleUI.RESET}")
        else:
            print()
        visible = min(len(options), ConsoleUI.MAX_VISIBLE)
        half    = visible // 2
        top     = max(0, min(selected_index - half, len(options) - visible))
        h_line  = "=" * box_w
        tl      = ConsoleUI.display_len(title)
        tpl     = max(0, (box_w - tl) // 2)
        tpr     = max(0, box_w - tl - tpl)
        print(f"  +{h_line}+")
        print(f"  |{' '*tpl}{ConsoleUI.BOLD}{ConsoleUI.CYAN}{title}{ConsoleUI.RESET}{' '*tpr}|")
        print(f"  +{h_line}+")
        if top > 0:
            aw = f"^  {top} element(s) plus haut"
            print(f"  |  {ConsoleUI.CYAN}{aw}{ConsoleUI.RESET}{' '*(box_w-2-ConsoleUI.display_len(aw))}|")
        else:
            print(f"  |{' '*box_w}|")
        inner  = box_w - 4
        max_t  = inner - 3
        for i in range(top, top + visible):
            raw = options[i]
            if ConsoleUI.display_len(raw) > max_t:
                acc, w = [], 0
                for ch in raw:
                    cw = 2 if ConsoleUI.display_len(ch) == 2 else 1
                    if w + cw > max_t - 1: break
                    acc.append(ch); w += cw
                raw = "".join(acc) + "..."
            prefix = ">  " if i == selected_index else "   "
            vt     = prefix + raw
            pr     = " " * max(0, inner - ConsoleUI.display_len(vt))
            if i == selected_index:
                print(f"  |  {ConsoleUI.CYAN}{ConsoleUI.BOLD}{vt}{ConsoleUI.RESET}{pr}  |")
            else:
                print(f"  |  {vt}{pr}  |")
        remaining = len(options) - top - visible
        if remaining > 0:
            aw = f"v  {remaining} element(s) plus bas"
            print(f"  |  {ConsoleUI.CYAN}{aw}{ConsoleUI.RESET}{' '*(box_w-2-ConsoleUI.display_len(aw))}|")
        else:
            print(f"  |{' '*box_w}|")
        print(f"  +{h_line}+")
        nav = "haut/bas  Naviguer   Entree  Valider   Echap  Retour"
        print(f"  |  {ConsoleUI.YELLOW}{nav}{ConsoleUI.RESET}{' '*(box_w-2-ConsoleUI.display_len(nav))}|")
        print(f"  +{h_line}+")

    @staticmethod
    def show_menu_termux(options, title="MENU", subtitle=""):
        ConsoleUI.clear()
        print(ConsoleUI.TERMUX_BANNER)
        print(f"\n  {ConsoleUI.BOLD}{ConsoleUI.CYAN}  {title}{ConsoleUI.RESET}")
        if subtitle:
            print(f"  {ConsoleUI.DIM}{subtitle}{ConsoleUI.RESET}")
        print(f"{ConsoleUI.CYAN}  {'─'*54}{ConsoleUI.RESET}\n")
        for i, opt in enumerate(options, 1):
            print(f"  {ConsoleUI.CYAN}{ConsoleUI.BOLD}[{i}]{ConsoleUI.RESET}  {opt}")
        print(f"  {ConsoleUI.CYAN}{ConsoleUI.BOLD}[0]{ConsoleUI.RESET}  {ConsoleUI.DIM}Retour{ConsoleUI.RESET}")
        print(f"\n{ConsoleUI.CYAN}  {'─'*54}{ConsoleUI.RESET}")

    @staticmethod
    def get_key():
        if IS_WINDOWS:
            if msvcrt and msvcrt.kbhit():
                key = msvcrt.getch()
                if key == b"\xe0":
                    key = msvcrt.getch()
                    if key == b"H": return "UP"
                    if key == b"P": return "DOWN"
                elif key == b"\r":    return "ENTER"
                elif key == b"\x1b":  return "ESC"
        else:
            if tty and termios and _select:
                fd = sys.stdin.fileno()
                try:
                    old = termios.tcgetattr(fd)
                except Exception:
                    return None
                try:
                    tty.setraw(fd)
                    if _select.select([sys.stdin], [], [], 0.05)[0]:
                        ch = sys.stdin.read(1)
                        if ch == "\x1b":
                            if _select.select([sys.stdin], [], [], 0.05)[0]:
                                more = sys.stdin.read(2)
                                if more == "[A": return "UP"
                                if more == "[B": return "DOWN"
                            return "ESC"
                        if ch in ("\r", "\n"): return "ENTER"
                finally:
                    termios.tcsetattr(fd, termios.TCSADRAIN, old)
        return None

    @staticmethod
    def navigate(options, title="MENU", subtitle=""):
        if not options:
            return -1
        if IS_TERMUX:
            while True:
                ConsoleUI.show_menu_termux(options, title, subtitle)
                try:
                    raw = input(f"  {ConsoleUI.YELLOW}>  {ConsoleUI.RESET}Choix : ").strip()
                except (EOFError, OSError):
                    return -1
                if raw in ("0", ""):
                    return -1
                if raw.isdigit():
                    idx = int(raw) - 1
                    if 0 <= idx < len(options):
                        return idx
                ConsoleUI.warn(f"Choix invalide -- entrez un nombre entre 1 et {len(options)}")
                time.sleep(0.8)
        else:
            selected = 0
            while True:
                ConsoleUI.show_menu(options, title, selected, subtitle)
                while True:
                    key = ConsoleUI.get_key()
                    if key: break
                    time.sleep(0.03)
                if key == "UP":    selected = (selected - 1) % len(options)
                elif key == "DOWN": selected = (selected + 1) % len(options)
                elif key == "ENTER": return selected
                elif key == "ESC":   return -1

    @staticmethod
    def input_screen(title, prompt_text, subtitle="", allow_esc=False):
        ConsoleUI.clear()
        banner = ConsoleUI.TERMUX_BANNER if IS_TERMUX else ConsoleUI.BANNER
        print(banner)
        print(f"\n  {ConsoleUI.CYAN}{ConsoleUI.BOLD}{'─'*58}{ConsoleUI.RESET}")
        print(f"  {ConsoleUI.BOLD}{ConsoleUI.WHITE}{title}{ConsoleUI.RESET}")
        if subtitle:
            print(f"  {ConsoleUI.DIM}{subtitle}{ConsoleUI.RESET}")
        if allow_esc:
            print(f"  {ConsoleUI.DIM}(Laissez vide + Entree pour annuler){ConsoleUI.RESET}")
        print(f"  {ConsoleUI.CYAN}{'─'*58}{ConsoleUI.RESET}\n")
        try:
            val = input(f"  {ConsoleUI.YELLOW}>  {ConsoleUI.RESET}{prompt_text} : ").strip()
            if allow_esc and not val:
                return None
            return val
        except (EOFError, OSError):
            return None if allow_esc else ""

    @staticmethod
    def result_screen(lines, pause=True):
        ConsoleUI.clear()
        banner = ConsoleUI.TERMUX_BANNER if IS_TERMUX else ConsoleUI.BANNER
        print(banner)
        print(ConsoleUI.CYAN + "\n  " + "─" * 58 + ConsoleUI.RESET)
        for line in lines:
            print(line)
        print(ConsoleUI.CYAN + "\n  " + "─" * 58 + ConsoleUI.RESET)
        if pause:
            try:
                input(f"\n  {ConsoleUI.DIM}Appuyez sur Entree pour continuer...{ConsoleUI.RESET}")
            except (EOFError, OSError):
                pass

    @staticmethod
    def info(m):    print(f"  {ConsoleUI.CYAN}ℹ  {ConsoleUI.RESET}{m}")
    @staticmethod
    def success(m): print(f"  {ConsoleUI.GREEN}✔  {ConsoleUI.RESET}{m}")
    @staticmethod
    def warn(m):    print(f"  {ConsoleUI.YELLOW}⚠  {ConsoleUI.RESET}{m}")
    @staticmethod
    def err(m):     print(f"  {ConsoleUI.RED}✖  {ConsoleUI.RESET}{m}")
    @staticmethod
    def sep():      print(f"\n  {ConsoleUI.CYAN}{'─'*54}{ConsoleUI.RESET}\n")


# ══════════════════════════════════════════════════════════════════════════════
#  Interface PyQt6 — PC uniquement (Windows / Linux desktop)
#  Termux/Android conserve l'interface console existante (ConsoleUI).
# ══════════════════════════════════════════════════════════════════════════════

# ── Import PyQt6 (optionnel) ──────────────────────────────────────────────────
PYQT_AVAILABLE = False  # pylint: disable=invalid-name

if IS_PC:
    try:
        from PyQt6.QtWidgets import (  # pylint: disable=import-error
            QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
            QLayout, QPushButton, QLabel, QLineEdit, QListWidget, QProgressBar,
            QTextEdit, QStackedWidget, QFileDialog, QMessageBox, QSpinBox,
        )
        from PyQt6.QtCore import (  # pylint: disable=import-error
            Qt, QThread, pyqtSignal, QRect, QPoint, QSize,
        )
        from PyQt6.QtGui import QFont  # pylint: disable=import-error
        PYQT_AVAILABLE = True
    except ImportError:
        pass

# Stubs minimalistes pour que les définitions de classes ne lèvent pas NameError
# quand PyQt6 est absent — elles ne seront jamais instanciées dans ce cas.
if not PYQT_AVAILABLE:
    class QThread:          # pylint: disable=too-few-public-methods
        """Stub QThread."""
    class QMainWindow:      # pylint: disable=too-few-public-methods
        """Stub QMainWindow."""
    def pyqtSignal(*_a, **_k):  # pylint: disable=invalid-name
        """Stub pyqtSignal."""
        return None
    class FlowLayout:       # pylint: disable=too-few-public-methods
        """Stub FlowLayout."""
else:
    class FlowLayout(QLayout):  # pylint: disable=too-many-public-methods
        """Disposition en flux : les boutons s'enchaînent sur la ligne courante
        et passent automatiquement à la suivante selon la largeur disponible.
        Équivalent du 'flex-wrap' CSS pour les widgets Qt."""

        def __init__(self, parent=None, h_spacing=8, v_spacing=6):
            super().__init__(parent)
            self._items     = []
            self._h_spacing = h_spacing
            self._v_spacing = v_spacing

        # ── API QLayout ───────────────────────────────────────────────────────
        def addItem(self, item):
            self._items.append(item)

        def count(self):
            return len(self._items)

        def itemAt(self, index):
            if 0 <= index < len(self._items):
                return self._items[index]
            return None

        def takeAt(self, index):
            if 0 <= index < len(self._items):
                return self._items.pop(index)
            return None

        def hasHeightForWidth(self):
            return True

        def heightForWidth(self, width):
            return self._do_layout(QRect(0, 0, width, 0), test_only=True)

        def setGeometry(self, rect):
            super().setGeometry(rect)
            self._do_layout(rect, test_only=False)

        def sizeHint(self):
            return self.minimumSize()

        def minimumSize(self):
            size = QSize()
            for item in self._items:
                size = size.expandedTo(item.minimumSize())
            m = self.contentsMargins()
            return size + QSize(m.left() + m.right(), m.top() + m.bottom())

        # ── Calcul de disposition ─────────────────────────────────────────────
        def _do_layout(self, rect, test_only):
            m         = self.contentsMargins()
            eff_x     = rect.x() + m.left()
            eff_right = rect.right() - m.right()
            x         = eff_x
            y         = rect.y() + m.top()
            row_h     = 0

            for item in self._items:
                hint   = item.sizeHint()
                iw, ih = hint.width(), hint.height()

                if x + iw > eff_right and row_h > 0:
                    # Retour à la ligne
                    x  = eff_x
                    y += row_h + self._v_spacing
                    row_h = 0

                if not test_only:
                    item.setGeometry(QRect(QPoint(x, y), hint))

                x    += iw + self._h_spacing
                row_h = max(row_h, ih)

            return y + row_h - rect.y() + m.bottom()


# ── Feuille de style (thème sombre anime) ────────────────────────────────────
_QSS = """
QMainWindow, QWidget {
    background-color: #0d0d1a;
    color: #e0e8ff;
    font-family: Consolas, "Courier New", monospace;
}
QLabel#title {
    color: #00d4ff;
    font-size: 14px;
    font-weight: bold;
    letter-spacing: 1px;
}
QLabel#subtitle { color: #7788aa; font-size: 11px; }
QLabel#section {
    color: #cc88ff;
    font-size: 11px;
    font-weight: bold;
    letter-spacing: 2px;
    text-transform: uppercase;
}
QPushButton {
    background-color: #12122a;
    color: #00d4ff;
    border: 1px solid #00d4ff55;
    border-radius: 6px;
    padding: 7px 16px;
    font-family: Consolas, "Courier New", monospace;
}
QPushButton:hover  {
    background-color: #00d4ff;
    color: #0d0d1a;
    border-color: #00d4ff;
}
QPushButton:disabled { color: #2a2a4a; border-color: #1e1e3a; background-color: #0f0f1e; }
QPushButton#danger { color: #ff6b6b; border-color: #ff6b6b55; }
QPushButton#danger:hover  { background-color: #ff6b6b; color: #0d0d1a; }
QPushButton#success { color: #00ff99; border-color: #00ff9955; }
QPushButton#success:hover { background-color: #00ff99; color: #0d0d1a; }
QPushButton#accent {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #1a0a30, stop:1 #0a1a30);
    color: #cc88ff;
    border-color: #cc88ff55;
}
QPushButton#accent:hover { background-color: #cc88ff; color: #0d0d1a; }
QListWidget {
    background-color: #0f0f22;
    border: 1px solid #1e1e3a;
    border-radius: 6px;
    color: #e0e8ff;
    font-family: Consolas, "Courier New", monospace;
    font-size: 12px;
    outline: none;
}
QListWidget::item { padding: 8px 10px; border-bottom: 1px solid #1a1a30; }
QListWidget::item:selected {
    background-color: #00d4ff1a;
    color: #00d4ff;
    border-left: 3px solid #00d4ff;
}
QListWidget::item:hover { background-color: #181832; }
QLineEdit {
    background-color: #0f0f22;
    border: 1px solid #1e1e3a;
    border-radius: 6px;
    color: #e0e8ff;
    padding: 6px 10px;
    font-family: Consolas, "Courier New", monospace;
    font-size: 12px;
}
QLineEdit:focus { border-color: #00d4ff; background-color: #11112a; }
QSpinBox {
    background-color: #0f0f22;
    border: 1px solid #1e1e3a;
    border-radius: 6px;
    color: #e0e8ff;
    padding: 4px 8px;
    font-size: 14px;
}
QSpinBox:focus { border-color: #00d4ff; }
QSpinBox::up-button, QSpinBox::down-button { background: #1a1a2e; border: none; }
QProgressBar {
    background-color: #0f0f22;
    border: 1px solid #1e1e3a;
    border-radius: 5px;
    text-align: center;
    color: #e0e8ff;
    font-size: 10px;
}
QProgressBar::chunk {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #0099bb, stop:1 #00d4ff);
    border-radius: 4px;
}
QTextEdit {
    background-color: #080814;
    border: 1px solid #1e1e3a;
    border-radius: 6px;
    color: #7788aa;
    font-family: Consolas, "Courier New", monospace;
    font-size: 10px;
}
QScrollBar:vertical { background: #0f0f22; width: 8px; border-radius: 4px; }
QScrollBar::handle:vertical { background: #2a2a4a; border-radius: 4px; min-height: 20px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QMessageBox { background-color: #12122a; }
QFrame#divider { background-color: #1e1e3a; max-height: 1px; }
"""


# ══════════════════════════════════════════════════════════════════════════════
#  Threads
# ══════════════════════════════════════════════════════════════════════════════

class InitThread(QThread):
    """Thread d'initialisation : installe les dépendances et cherche FFmpeg."""

    progress = pyqtSignal(str)   # message de statut
    finished = pyqtSignal(str)   # chemin ffmpeg_exe (ou "" si introuvable)

    def run(self):
        """Exécution dans le thread."""
        self.progress.emit("Installation des dépendances...")
        setup_dependencies()
        self.progress.emit("Recherche de FFmpeg...")
        ffmpeg = setup_ffmpeg()
        self.finished.emit(ffmpeg or "")


class LoadAnimeThread(QThread):
    """Thread de chargement des données d'un anime (local → GitHub → scraping)."""

    finished = pyqtSignal(object)   # dict anime_data ou None

    def __init__(self, slug, cfg, parent=None):
        super().__init__(parent)
        self.slug = slug
        self.cfg  = cfg

    def run(self):
        """Exécution dans le thread."""
        data = load_anime_local(self.slug)
        if data is None and self.cfg.get("github_fallback") and GITHUB_TOKEN:
            data = load_anime_github(self.slug)
        if data is None:
            data = scrape_anime_data(self.slug)
        self.finished.emit(data)


class DownloadThread(QThread):
    """Thread de téléchargement des épisodes."""

    ep_start    = pyqtSignal(int, int, str)    # numéro courant, total, nom
    ep_progress = pyqtSignal(float, str, str)  # pct, vitesse, eta
    ep_done     = pyqtSignal(bool, str)        # succès, nom de fichier
    all_done    = pyqtSignal(int, int)         # succès total, échecs total

    def __init__(  # pylint: disable=too-many-arguments,too-many-positional-arguments
            self, slug, anime_data, lang, saison_key, ep_range,
            dest_dir, anime_name, ffmpeg_exe, parent=None):
        super().__init__(parent)
        self.slug        = slug
        self.anime_data  = anime_data
        self.lang        = lang
        self.saison_key  = saison_key
        self.ep_range    = ep_range
        self.dest_dir    = dest_dir
        self.anime_name  = anime_name
        self.ffmpeg_exe  = ffmpeg_exe
        self._cancelled  = False

    def cancel(self):
        """Demande l'annulation du téléchargement."""
        self._cancelled = True

    def run(self):  # pylint: disable=too-many-locals
        """Exécution dans le thread."""
        global _qt_progress_callback  # pylint: disable=global-statement

        def _hook(d):
            if d["status"] == "downloading":
                pct_str = d.get("_percent_str", "0%").strip()
                speed   = d.get("_speed_str",   "").strip()
                eta     = d.get("_eta_str",     "").strip()
                try:
                    pct = float(pct_str.replace("%", "").strip())
                except ValueError:
                    pct = 0.0
                self.ep_progress.emit(pct, speed, eta)

        _qt_progress_callback = _hook
        blocs      = self.anime_data.get(self.lang, {}).get(self.saison_key, [])
        total_eps  = count_episodes(blocs)
        ep_start_i = max(0, self.ep_range[0])
        ep_end_i   = min(self.ep_range[1], total_eps - 1)
        total      = ep_end_i - ep_start_i + 1
        success    = fail = 0
        try:
            for i, ep_idx in enumerate(range(ep_start_i, ep_end_i + 1)):
                if self._cancelled:
                    break
                base_name = ep_filename(self.saison_key, ep_idx)
                self.ep_start.emit(i + 1, total, base_name)
                ok = download_episode(
                    self.slug, self.saison_key, ep_idx, blocs,
                    self.dest_dir, self.anime_name, self.lang, self.ffmpeg_exe,
                )
                self.ep_done.emit(ok, base_name)
                if ok:
                    success += 1
                else:
                    fail += 1
            self.all_done.emit(success, fail)
        finally:
            _qt_progress_callback = None


# ══════════════════════════════════════════════════════════════════════════════
#  NeyChanWindow — fenêtre principale PyQt6
# ══════════════════════════════════════════════════════════════════════════════

class NeyChanWindow(QMainWindow):  # pylint: disable=too-many-instance-attributes,too-many-public-methods
    """Fenêtre principale de Ney-Chan (interface PyQt6, PC uniquement)."""

    # Indices des pages dans le QStackedWidget
    PAGE_INIT     = 0
    PAGE_MAIN     = 1
    PAGE_SEARCH   = 2
    PAGE_LIST     = 3
    PAGE_EP_INPUT = 4
    PAGE_SETTINGS = 5
    PAGE_DOWNLOAD = 6
    PAGE_WHAT_DL  = 7

    def __init__(self, cfg, dest_dir_ref, ffmpeg_exe):
        super().__init__()
        self.cfg           = cfg
        self.dest_dir_ref  = dest_dir_ref   # list[str] — référence mutable
        self.ffmpeg_exe    = ffmpeg_exe

        # État de navigation
        self._slug         = ""
        self._anime_data   = None
        self._anime_name   = ""
        self._lang         = ""
        self._saison_key   = ""
        self._list_cb      = None
        self._list_back    = self.PAGE_MAIN
        self._ep_cb        = None
        self._ep_back      = self.PAGE_LIST
        self._dl_thread    = None
        self._dl_queue     = []
        self._dl_queue_ctx = None
        self._dl_queue_res = (0, 0)

        # État de la page QUE TÉLÉCHARGER ?
        self._whatdl_saisons      = []
        self._whatdl_lang_data    = {}
        self._whatdl_sel_idx      = 0
        self._whatdl_back         = None
        self._whatdl_saison_btns  = []
        self._whatdl_resume       = None   # point de reprise détecté

        # État étendu de la page saisie d'épisode
        self._ep_cancel_cb        = None   # Annuler (haut-gauche) → QUE TELECHARGER ?
        self._ep_back_cb          = None   # Retour (bas-gauche) → étape précédente
        self._ep_prompt_fn        = None   # callable(n) → prompt texte
        self._ep_saisons_ep       = []     # saisons pour le sélecteur de la page épisode
        self._ep_lang_data_ep     = {}
        self._ep_sel_idx_ep       = 0
        self._ep_saison_btns_ep   = []

        self._setup_window()
        self._build_ui()

    # ── Fenêtre ────────────────────────────────────────────────────────────────

    def _setup_window(self):
        self.setWindowTitle(f"Ney-Chan v{VERSION} — Anime Downloader")
        self.setMinimumSize(820, 580)
        self.resize(920, 660)
        self.setStyleSheet(_QSS)

    def _build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        vlay = QVBoxLayout(root)
        vlay.setContentsMargins(0, 0, 0, 0)
        vlay.setSpacing(0)

        vlay.addWidget(self._make_header())

        self._stack = QStackedWidget()
        vlay.addWidget(self._stack, 1)

        self._status_lbl = QLabel()
        self._status_lbl.setObjectName("subtitle")
        self._status_lbl.setContentsMargins(14, 4, 14, 4)
        vlay.addWidget(self._status_lbl)

        for builder in (
            self._build_page_init,
            self._build_page_main,
            self._build_page_search,
            self._build_page_list,
            self._build_page_ep_input,
            self._build_page_settings,
            self._build_page_download,
            self._build_page_what_dl,
        ):
            self._stack.addWidget(builder())

        self._refresh_status()

    def _make_header(self):
        frame = QWidget()
        frame.setFixedHeight(72)
        frame.setStyleSheet(
            "background: qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            "stop:0 #080814, stop:0.5 #0d0d22, stop:1 #080814);"
            "border-bottom: 1px solid #00d4ff33;"
        )
        hlay = QHBoxLayout(frame)
        hlay.setContentsMargins(20, 8, 20, 8)

        vlay = QVBoxLayout()
        t1 = QLabel("🎌  NEY-CHAN")
        t1.setStyleSheet("color:#00d4ff; font-size:22px; font-weight:bold; letter-spacing:2px;")
        t2 = QLabel("A N I M E   D O W N L O A D E R")
        t2.setStyleSheet("color:#cc88ff; font-size:9px; letter-spacing:4px;")
        vlay.addWidget(t1)
        vlay.addWidget(t2)
        hlay.addLayout(vlay)
        hlay.addStretch()

        ver = QLabel(f"v{VERSION}")
        ver.setStyleSheet("color:#2a3a5a; font-size:10px;")
        hlay.addWidget(ver)
        return frame

    def _refresh_status(self):
        dest   = self.dest_dir_ref[0] if self.dest_dir_ref else "—"
        github = "GitHub: ON" if self.cfg.get("github_fallback") else "GitHub: OFF"
        self._status_lbl.setText(f"  {dest}   |   {github}")

    # ── Constructeurs de pages ─────────────────────────────────────────────────

    def _build_page_init(self):
        page = QWidget()
        lay  = QVBoxLayout(page)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._init_lbl  = QLabel("Initialisation...")
        self._init_lbl.setObjectName("title")
        self._init_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._init_bar  = QProgressBar()
        self._init_bar.setRange(0, 0)
        self._init_bar.setFixedWidth(320)
        lay.addStretch()
        lay.addWidget(self._init_lbl)
        lay.addSpacing(16)
        lay.addWidget(self._init_bar, 0, Qt.AlignmentFlag.AlignCenter)
        lay.addStretch()
        return page

    def _build_page_main(self):
        page = QWidget()
        lay  = QVBoxLayout(page)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        header = QLabel("— MENU PRINCIPAL —")
        header.setObjectName("title")
        header.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addStretch()
        lay.addWidget(header)
        lay.addSpacing(28)
        for text, slot, obj in [
            ("🔍   Rechercher un anime", self._go_search, ""),
            ("⚙    Paramètres",          self._go_settings, ""),
            ("✖   Quitter",              self.close, "danger"),
        ]:
            btn = QPushButton(text)
            btn.setFixedSize(290, 50)
            btn.setFont(QFont("Consolas", 11))
            if obj:
                btn.setObjectName(obj)
            btn.clicked.connect(slot)
            lay.addWidget(btn, 0, Qt.AlignmentFlag.AlignCenter)
            lay.addSpacing(8)
        lay.addStretch()
        return page

    def _build_page_search(self):
        page = QWidget()
        lay  = QVBoxLayout(page)
        lay.setContentsMargins(48, 40, 48, 40)
        lbl  = QLabel("RECHERCHER UN ANIME")
        lbl.setObjectName("title")
        lay.addWidget(lbl)
        sub = QLabel("Base de données locale (dossier anime-sama/)  —  fallback scraping anime-sama")
        sub.setObjectName("subtitle")
        lay.addWidget(sub)
        lay.addSpacing(18)
        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText("Nom de l'anime…")
        self._search_edit.setFixedHeight(38)
        self._search_edit.returnPressed.connect(self._on_search_submit)
        lay.addWidget(self._search_edit)
        lay.addSpacing(14)
        row = QHBoxLayout()
        btn_back = QPushButton("← Retour")
        btn_back.clicked.connect(lambda: self._go(self.PAGE_MAIN))
        btn_go   = QPushButton("Rechercher →")
        btn_go.clicked.connect(self._on_search_submit)
        row.addWidget(btn_back)
        row.addStretch()
        row.addWidget(btn_go)
        lay.addLayout(row)
        lay.addStretch()
        return page

    def _build_page_list(self):
        page = QWidget()
        lay  = QVBoxLayout(page)
        lay.setContentsMargins(48, 24, 48, 24)
        self._list_title_lbl = QLabel("LISTE")
        self._list_title_lbl.setObjectName("title")
        lay.addWidget(self._list_title_lbl)
        self._list_sub_lbl = QLabel("")
        self._list_sub_lbl.setObjectName("subtitle")
        lay.addWidget(self._list_sub_lbl)
        lay.addSpacing(10)
        self._list_w = QListWidget()
        self._list_w.itemDoubleClicked.connect(lambda _: self._list_select())
        lay.addWidget(self._list_w, 1)
        lay.addSpacing(10)
        row = QHBoxLayout()
        self._list_back_btn   = QPushButton("← Retour")
        self._list_back_btn.clicked.connect(self._list_back_action)
        self._list_select_btn = QPushButton("Sélectionner →")
        self._list_select_btn.clicked.connect(self._list_select)
        row.addWidget(self._list_back_btn)
        row.addStretch()
        row.addWidget(self._list_select_btn)
        lay.addLayout(row)
        return page

    def _build_page_ep_input(self):
        page = QWidget()
        lay  = QVBoxLayout(page)
        lay.setContentsMargins(48, 20, 48, 32)

        # ── Barre supérieure : bouton Annuler (gauche) ────────────────────────
        top_row = QHBoxLayout()
        self._ep_cancel_top_btn = QPushButton("✖  Annuler")
        self._ep_cancel_top_btn.setObjectName("danger")
        self._ep_cancel_top_btn.setFixedWidth(115)
        self._ep_cancel_top_btn.clicked.connect(self._ep_cancel_top)
        self._ep_cancel_top_btn.setVisible(False)
        top_row.addWidget(self._ep_cancel_top_btn)
        top_row.addStretch()
        lay.addLayout(top_row)
        lay.addSpacing(10)

        # ── Mode de téléchargement ────────────────────────────────────────────
        self._ep_mode_lbl = QLabel("")
        self._ep_mode_lbl.setObjectName("title")
        lay.addWidget(self._ep_mode_lbl)
        self._ep_sub_lbl = QLabel("")
        self._ep_sub_lbl.setObjectName("subtitle")
        lay.addWidget(self._ep_sub_lbl)

        lay.addStretch()

        # ── SpinBox ───────────────────────────────────────────────────────────
        self._ep_spin = QSpinBox()
        self._ep_spin.setRange(1, 9999)
        self._ep_spin.setFixedSize(130, 42)
        self._ep_spin.setFont(QFont("Consolas", 14))
        lay.addWidget(self._ep_spin, 0, Qt.AlignmentFlag.AlignCenter)

        lay.addStretch()

        # ── Sélecteur de saison (optionnel) ───────────────────────────────────
        self._ep_saison_lbl = QLabel("SAISON")
        self._ep_saison_lbl.setObjectName("section")
        self._ep_saison_lbl.setVisible(False)
        lay.addWidget(self._ep_saison_lbl)
        lay.addSpacing(6)

        self._ep_saison_container = QWidget()
        FlowLayout(self._ep_saison_container, h_spacing=8, v_spacing=6)
        self._ep_saison_container.setVisible(False)
        lay.addWidget(self._ep_saison_container)
        lay.addSpacing(14)

        # ── Boutons de navigation ─────────────────────────────────────────────
        nav_row = QHBoxLayout()
        self._ep_back_btn = QPushButton("← Retour")
        self._ep_back_btn.setVisible(False)
        self._ep_back_btn.clicked.connect(self._ep_back_action)
        self._ep_ok_btn = QPushButton("Confirmer →")
        self._ep_ok_btn.clicked.connect(self._ep_confirm)
        nav_row.addWidget(self._ep_back_btn)
        nav_row.addStretch()
        nav_row.addWidget(self._ep_ok_btn)
        lay.addLayout(nav_row)

        return page

    def _build_page_settings(self):  # pylint: disable=too-many-locals
        page = QWidget()
        lay  = QVBoxLayout(page)
        lay.setContentsMargins(48, 28, 48, 28)

        lbl = QLabel("PARAMÈTRES")
        lbl.setObjectName("title")
        lay.addWidget(lbl)
        lay.addSpacing(6)

        # ── Dossier de téléchargement ─────────────────────────────────────────
        lbl_dir = QLabel("DOSSIER DE TÉLÉCHARGEMENT")
        lbl_dir.setObjectName("section")
        lay.addWidget(lbl_dir)
        lay.addSpacing(6)

        self._set_dir_edit = QLineEdit(self.dest_dir_ref[0] if self.dest_dir_ref else "")
        self._set_dir_edit.setPlaceholderText("Chemin du dossier de téléchargement…")
        self._set_dir_edit.setFixedHeight(34)
        self._set_dir_edit.returnPressed.connect(self._settings_apply_dir)
        lay.addWidget(self._set_dir_edit)
        lay.addSpacing(6)

        row_dir = QHBoxLayout()
        btn_apply_dir = QPushButton("✔  Appliquer")
        btn_apply_dir.setFixedWidth(130)
        btn_apply_dir.setObjectName("success")
        btn_apply_dir.clicked.connect(self._settings_apply_dir)

        btn_choose_dir = QPushButton("📂  Choisir")
        btn_choose_dir.setFixedWidth(120)
        btn_choose_dir.clicked.connect(self._settings_choose_dir)

        row_dir.addWidget(btn_apply_dir)
        row_dir.addSpacing(6)
        row_dir.addWidget(btn_choose_dir)
        row_dir.addStretch()
        lay.addLayout(row_dir)
        lay.addSpacing(20)

        # ── Base de données locale ────────────────────────────────────────────
        lbl_db = QLabel("BASE DE DONNÉES LOCALE")
        lbl_db.setObjectName("section")
        lay.addWidget(lbl_db)
        lay.addSpacing(4)
        hint_db = QLabel("Dossier contenant le sous-dossier anime-sama/ (index.json + animes/)")
        hint_db.setStyleSheet("color:#556677; font-size:10px;")
        lay.addWidget(hint_db)
        lay.addSpacing(6)

        self._set_db_edit = QLineEdit(LOCAL_DB_DIR)
        self._set_db_edit.setPlaceholderText("Chemin du dossier de la base de données…")
        self._set_db_edit.setFixedHeight(34)
        self._set_db_edit.returnPressed.connect(self._settings_apply_db)
        lay.addWidget(self._set_db_edit)
        lay.addSpacing(6)

        row_db = QHBoxLayout()
        btn_db_apply = QPushButton("✔  Appliquer")
        btn_db_apply.setFixedWidth(130)
        btn_db_apply.setObjectName("success")
        btn_db_apply.clicked.connect(self._settings_apply_db)

        btn_db_choose = QPushButton("📂  Choisir")
        btn_db_choose.setFixedWidth(120)
        btn_db_choose.clicked.connect(self._settings_choose_db)

        row_db.addWidget(btn_db_apply)
        row_db.addSpacing(6)
        row_db.addWidget(btn_db_choose)
        row_db.addStretch()
        lay.addLayout(row_db)
        lay.addSpacing(20)

        # ── GitHub fallback ───────────────────────────────────────────────────
        lbl_gh = QLabel("GITHUB FALLBACK")
        lbl_gh.setObjectName("section")
        lay.addWidget(lbl_gh)
        lay.addSpacing(6)

        row_gh = QHBoxLayout()
        self._set_gh_btn = QPushButton()
        self._set_gh_btn.setFixedWidth(130)
        self._set_gh_btn.clicked.connect(self._settings_toggle_gh)

        # Si pas de token : forcer désactivé et bloquer le bouton
        if not GITHUB_TOKEN:
            self.cfg["github_fallback"] = False
            _save_config({"github_fallback": False})

        self._refresh_gh_btn()
        row_gh.addWidget(self._set_gh_btn)
        row_gh.addStretch()
        lay.addLayout(row_gh)

        if not GITHUB_TOKEN:
            warn_lbl = QLabel("⚠  GITHUB_TOKEN absent du fichier .env — fallback GitHub désactivé")
            warn_lbl.setStyleSheet("color:#ffaa44; font-size:10px; padding-top:4px;")
            lay.addWidget(warn_lbl)
            env_hint = QLabel("   Ajoutez  GITHUB_TOKEN=<votre_token>  dans un fichier .env")
            env_hint.setStyleSheet("color:#556677; font-size:10px;")
            lay.addWidget(env_hint)

        lay.addStretch()

        btn_back = QPushButton("← Retour")
        btn_back.setFixedWidth(120)
        btn_back.clicked.connect(lambda: self._go(self.PAGE_MAIN))
        lay.addWidget(btn_back)
        return page

    def _build_page_download(self):  # pylint: disable=too-many-statements
        page = QWidget()
        lay  = QVBoxLayout(page)
        lay.setContentsMargins(48, 24, 48, 24)

        self._dl_title_lbl = QLabel("TÉLÉCHARGEMENT EN COURS")
        self._dl_title_lbl.setObjectName("title")
        lay.addWidget(self._dl_title_lbl)
        self._dl_anime_lbl = QLabel("")
        self._dl_anime_lbl.setObjectName("subtitle")
        lay.addWidget(self._dl_anime_lbl)
        lay.addSpacing(14)

        self._dl_ep_lbl = QLabel("Préparation…")
        self._dl_ep_lbl.setStyleSheet("color:#00d4ff; font-size:12px;")
        lay.addWidget(self._dl_ep_lbl)
        lay.addSpacing(4)

        lbl_o = QLabel("Progression globale :")
        lbl_o.setObjectName("subtitle")
        lay.addWidget(lbl_o)
        self._dl_overall = QProgressBar()
        self._dl_overall.setFixedHeight(14)
        lay.addWidget(self._dl_overall)
        lay.addSpacing(10)

        lbl_e = QLabel("Progression de l'épisode :")
        lbl_e.setObjectName("subtitle")
        lay.addWidget(lbl_e)
        self._dl_ep_bar = QProgressBar()
        self._dl_ep_bar.setRange(0, 100)
        self._dl_ep_bar.setFixedHeight(22)
        lay.addWidget(self._dl_ep_bar)
        lay.addSpacing(4)
        self._dl_speed_lbl = QLabel("")
        self._dl_speed_lbl.setObjectName("subtitle")
        lay.addWidget(self._dl_speed_lbl)
        lay.addSpacing(12)

        lbl_log = QLabel("Journal :")
        lbl_log.setObjectName("subtitle")
        lay.addWidget(lbl_log)
        self._dl_log = QTextEdit()
        self._dl_log.setReadOnly(True)
        self._dl_log.setFixedHeight(150)
        lay.addWidget(self._dl_log)
        lay.addSpacing(12)

        row = QHBoxLayout()
        row.setSpacing(16)

        self._dl_cancel_btn = QPushButton("✖  Annuler")
        self._dl_cancel_btn.setObjectName("danger")
        self._dl_cancel_btn.setFixedHeight(42)
        self._dl_cancel_btn.setMinimumWidth(130)
        self._dl_cancel_btn.clicked.connect(self._dl_cancel)

        self._dl_done_btn = QPushButton()
        self._dl_done_btn.setFixedHeight(42)
        self._dl_done_btn.setMinimumWidth(260)
        self._dl_done_btn.setFont(QFont("Consolas", 11, QFont.Weight.Bold))
        self._dl_done_btn.clicked.connect(lambda: self._go(self.PAGE_MAIN))
        self._dl_set_done(False)   # état initial : pas terminé

        row.addWidget(self._dl_cancel_btn)
        row.addStretch()
        row.addWidget(self._dl_done_btn)
        lay.addLayout(row)
        return page

    def _build_page_what_dl(self):
        """Page 'QUE TÉLÉCHARGER ?' avec sélecteur de saison intégré en bas."""
        page = QWidget()
        lay  = QVBoxLayout(page)
        lay.setContentsMargins(48, 24, 48, 24)

        self._whatdl_title_lbl = QLabel("QUE TÉLÉCHARGER ?")
        self._whatdl_title_lbl.setObjectName("title")
        lay.addWidget(self._whatdl_title_lbl)
        self._whatdl_sub_lbl = QLabel("")
        self._whatdl_sub_lbl.setObjectName("subtitle")
        lay.addWidget(self._whatdl_sub_lbl)
        lay.addSpacing(10)

        self._whatdl_list = QListWidget()
        self._whatdl_list.itemDoubleClicked.connect(lambda _: self._whatdl_select())
        lay.addWidget(self._whatdl_list, 1)
        lay.addSpacing(12)

        # ── Sélecteur de saison ───────────────────────────────────────────────
        self._whatdl_saison_lbl = QLabel("SAISON")
        self._whatdl_saison_lbl.setObjectName("section")
        lay.addWidget(self._whatdl_saison_lbl)
        lay.addSpacing(6)

        self._whatdl_saison_container = QWidget()
        FlowLayout(self._whatdl_saison_container, h_spacing=8, v_spacing=6)
        lay.addWidget(self._whatdl_saison_container)
        lay.addSpacing(14)

        # ── Boutons de navigation ─────────────────────────────────────────────
        nav_row = QHBoxLayout()
        self._whatdl_back_btn = QPushButton("← Retour")
        self._whatdl_back_btn.clicked.connect(self._whatdl_back_action)
        self._whatdl_sel_btn  = QPushButton("Sélectionner →")
        self._whatdl_sel_btn.clicked.connect(self._whatdl_select)
        nav_row.addWidget(self._whatdl_back_btn)
        nav_row.addStretch()
        nav_row.addWidget(self._whatdl_sel_btn)
        lay.addLayout(nav_row)

        return page

    # ── Navigation ─────────────────────────────────────────────────────────────

    def _go(self, page_idx):
        self._stack.setCurrentIndex(page_idx)

    def _go_search(self):
        self._search_edit.clear()
        self._go(self.PAGE_SEARCH)
        self._search_edit.setFocus()

    def _go_settings(self):
        self._set_dir_edit.setText(self.dest_dir_ref[0] if self.dest_dir_ref else "")
        self._set_db_edit.setText(LOCAL_DB_DIR)
        self._refresh_gh_btn()
        self._go(self.PAGE_SETTINGS)

    def _show_list(self, opts, title, subtitle, callback, back=None):
        """Remplit et affiche la page liste générique."""
        self._list_title_lbl.setText(title)
        self._list_sub_lbl.setText(subtitle)
        self._list_w.clear()
        for o in opts:
            self._list_w.addItem(o)
        if self._list_w.count():
            self._list_w.setCurrentRow(0)
        self._list_cb   = callback
        self._list_back = back if back is not None else self.PAGE_MAIN
        self._go(self.PAGE_LIST)

    def _list_select(self):
        row = self._list_w.currentRow()
        if row >= 0 and self._list_cb:
            self._list_cb(row)

    def _list_back_action(self):
        if callable(self._list_back):
            self._list_back()
        else:
            self._go(self._list_back)

    def _ask_ep(self, prompt_fn_or_str, max_val, min_val, callback,
                mode_label="NUMÉRO D'ÉPISODE",
                cancel_cb=None,
                back_action=None,
                show_saison=False, saisons=None, lang_data=None, sel_idx=0):
        """Affiche la page saisie d'épisode (version enrichie).

        prompt_fn_or_str : str fixe OU callable(n) → str
        cancel_cb        : callable → Annuler (haut-gauche, retour QUE TELECHARGER)
        back_action      : callable → Retour  (bas-gauche, étape précédente)
        show_saison      : afficher le sélecteur de saison
        """
        # Prompt
        if callable(prompt_fn_or_str):
            self._ep_prompt_fn = prompt_fn_or_str
        else:
            self._ep_prompt_fn = lambda _n, _s=prompt_fn_or_str: _s

        self._ep_mode_lbl.setText(mode_label)
        self._ep_sub_lbl.setText(self._ep_prompt_fn(max_val))
        self._ep_spin.setRange(min_val, max_val)
        self._ep_spin.setValue(min_val)
        self._ep_cb   = callback
        self._ep_back = self._stack.currentIndex()

        # Annuler haut-gauche
        self._ep_cancel_cb = cancel_cb
        self._ep_cancel_top_btn.setVisible(cancel_cb is not None)

        # Retour bas-gauche
        self._ep_back_cb = back_action
        self._ep_back_btn.setVisible(back_action is not None)

        # Sélecteur de saison
        if show_saison and saisons and lang_data:
            self._ep_saisons_ep     = list(saisons)
            self._ep_lang_data_ep   = lang_data
            self._ep_sel_idx_ep     = sel_idx
            self._ep_saison_lbl.setVisible(True)
            self._ep_saison_container.setVisible(True)
            self._ep_rebuild_saison_btns()
        else:
            self._ep_saison_lbl.setVisible(False)
            self._ep_saison_container.setVisible(False)

        self._go(self.PAGE_EP_INPUT)

    def _ep_rebuild_saison_btns(self):
        lay_s = self._ep_saison_container.layout()
        while lay_s.count():
            item = lay_s.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._ep_saison_btns_ep = []
        for i, k in enumerate(self._ep_saisons_ep):
            disp, _, _, _ = saison_key_info(k)
            btn = QPushButton(disp)
            btn.setFixedHeight(32)
            btn.clicked.connect(lambda _c, idx=i: self._ep_pick_saison(idx))
            lay_s.addWidget(btn)
            self._ep_saison_btns_ep.append(btn)
        self._ep_refresh_saison_btns()

    def _ep_pick_saison(self, idx):
        self._ep_sel_idx_ep = idx
        skey = self._ep_saisons_ep[idx]
        n    = count_episodes(self._ep_lang_data_ep[skey])
        self._ep_spin.setRange(1, n)
        self._ep_spin.setValue(1)
        if self._ep_prompt_fn:
            self._ep_sub_lbl.setText(self._ep_prompt_fn(n))
        self._ep_refresh_saison_btns()

    def _ep_refresh_saison_btns(self):
        _ACTIVE = (
            "background-color:#00d4ff; color:#0d0d1a; border:1px solid #00d4ff;"
            "border-radius:6px; padding:4px 14px;"
            "font-family:Consolas,'Courier New',monospace;"
        )
        for i, btn in enumerate(self._ep_saison_btns_ep):
            btn.setStyleSheet(_ACTIVE if i == self._ep_sel_idx_ep else "")

    def _ep_cancel_top(self):
        if callable(self._ep_cancel_cb):
            self._ep_cancel_cb()
        else:
            self._go(self.PAGE_WHAT_DL)

    def _ep_back_action(self):
        if callable(self._ep_back_cb):
            self._ep_back_cb()
        else:
            self._go(self._ep_back)

    def _ep_confirm(self):
        val = self._ep_spin.value()
        # Mise à jour de la saison si le sélecteur est actif
        if self._ep_saison_lbl.isVisible() and self._ep_saisons_ep:
            self._saison_key = self._ep_saisons_ep[self._ep_sel_idx_ep]
        if self._ep_cb:
            self._ep_cb(val)

    def _ep_cancel(self):
        self._go(self._ep_back)

    # ── Recherche ──────────────────────────────────────────────────────────────

    def _on_search_submit(self):
        query_raw = self._search_edit.text().strip()
        if not query_raw:
            return
        query   = normalize_query(query_raw)
        results = search_local(query)

        # Si pas de résultat local : on tente directement (GitHub + scraping auto)
        if not results:
            self._load_anime(query)
            return
        if len(results) == 1:
            self._load_anime(results[0])
            return
        self._show_list(
            [slug_to_display(s) for s in results],
            "RÉSULTATS",
            f"{len(results)} résultat(s) pour « {query_raw} »",
            lambda idx, r=results: self._load_anime(r[idx]),
            back=self.PAGE_SEARCH,
        )

    def _load_anime(self, slug):
        self._slug       = slug
        self._anime_name = slug_to_display(slug)
        self._list_title_lbl.setText("CHARGEMENT…")
        self._list_sub_lbl.setText(f"Récupération des données pour {self._anime_name}")
        self._list_w.clear()
        self._go(self.PAGE_LIST)
        t = LoadAnimeThread(slug, self.cfg, self)
        t.finished.connect(self._on_anime_loaded)
        t.start()

    def _on_anime_loaded(self, data):
        if data is None:
            self._msg("Anime introuvable",
                      f"Impossible de trouver « {self._anime_name} ».\n"
                      "Vérifiez le nom ou sa disponibilité sur anime-sama.")
            self._go(self.PAGE_SEARCH)
            return
        if not data:
            self._msg("Aucune vidéo",
                      f"« {self._anime_name} » existe mais aucune vidéo n'est disponible.")
            self._go(self.PAGE_SEARCH)
            return
        self._anime_data = data
        langs = [l for l in ALL_LANGUAGES if l in data]
        if not langs:
            self._msg("Aucune langue", "Aucune langue disponible pour cet anime.")
            self._go(self.PAGE_SEARCH)
            return
        self._show_list(
            [LANG_LABELS.get(l, l.upper()) for l in langs],
            "CHOISIR UNE LANGUE",
            self._anime_name,
            lambda idx, ls=langs: self._on_lang(ls[idx]),
            back=self.PAGE_SEARCH,
        )

    def _on_lang(self, lang):
        self._lang   = lang
        lang_data    = self._anime_data.get(lang, {})
        saisons      = list(lang_data.keys())
        if not saisons:
            self._msg("Aucune saison", f"Aucune saison disponible en {lang.upper()}.")
            return

        def _back_to_langs():
            langs = [l for l in ALL_LANGUAGES if l in self._anime_data]
            self._show_list(
                [LANG_LABELS.get(l, l.upper()) for l in langs],
                "CHOISIR UNE LANGUE",
                self._anime_name,
                lambda idx, ls=langs: self._on_lang(ls[idx]),
                back=self.PAGE_SEARCH,
            )

        self._show_what_download(lang, lang_data, saisons, back_fn=_back_to_langs)

    # ── Page QUE TÉLÉCHARGER ? ─────────────────────────────────────────────────

    def _show_what_download(self, lang, lang_data, saisons, back_fn=None):
        """Peuple et affiche la page QUE TÉLÉCHARGER ? avec sélecteur de saison."""
        self._whatdl_saisons    = saisons
        self._whatdl_lang_data  = lang_data
        self._whatdl_sel_idx    = 0
        self._whatdl_back       = back_fn

        total_all  = sum(count_episodes(lang_data[k]) for k in lang_data)
        nb_saisons = len(saisons)

        self._whatdl_sub_lbl.setText(f"{self._anime_name}  —  {lang.upper()}")

        # ── Détection du point de reprise ─────────────────────────────────────
        resume = _find_resume_point(
            self.dest_dir_ref[0], self._anime_name, lang, self._anime_data
        )
        self._whatdl_resume = resume
        if resume is None:
            resume_label = "▶  Reprendre  (aucun téléchargement détecté)"
        elif resume == "done":
            resume_label = "▶  Reprendre  (tout est déjà téléchargé ✔)"
        else:
            r_si, r_ep = resume
            r_d, _, _, _ = saison_key_info(saisons[r_si])
            resume_label = f"▶  Reprendre depuis {r_d} — Épisode {r_ep + 1}"

        self._whatdl_list.clear()
        self._whatdl_list.addItem(resume_label)                                            # row 0
        self._whatdl_list.addItem(f"Toute la série  ({total_all} ep. — {nb_saisons} saison(s))")  # row 1
        self._whatdl_list.addItem("")          # "Toute la saison", mis à jour par _whatdl_refresh  # row 2
        self._whatdl_list.addItem("Un épisode spécifique")                                 # row 3
        self._whatdl_list.addItem("D'un épisode X à Y")                                   # row 4
        self._whatdl_list.addItem("Depuis un épisode")                                    # row 5
        self._whatdl_list.setCurrentRow(0)

        # Reconstruction des boutons de saison (FlowLayout adaptatif)
        lay_s = self._whatdl_saison_container.layout()
        while lay_s.count():
            item = lay_s.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._whatdl_saison_btns = []

        for i, k in enumerate(saisons):
            disp, _, _, _ = saison_key_info(k)
            btn = QPushButton(disp)
            btn.setFixedHeight(32)
            btn.clicked.connect(lambda _checked, idx=i: self._whatdl_pick_saison(idx))
            lay_s.addWidget(btn)
            self._whatdl_saison_btns.append(btn)

        self._whatdl_saison_lbl.setVisible(nb_saisons > 1)
        self._whatdl_saison_container.setVisible(nb_saisons > 1)

        self._whatdl_refresh()
        self._go(self.PAGE_WHAT_DL)

    def _whatdl_refresh(self):
        saisons   = self._whatdl_saisons
        lang_data = self._whatdl_lang_data
        sel       = self._whatdl_sel_idx
        skey      = saisons[sel]
        n         = count_episodes(lang_data[skey])

        # "Toute la saison" est désormais à l'index 2 (index 0 = Reprendre)
        item = self._whatdl_list.item(2)
        if item:
            item.setText(f"Toute la saison  ({n} épisode(s))")

        _ACTIVE = (
            "background-color:#00d4ff; color:#0d0d1a; border:1px solid #00d4ff;"
            "border-radius:6px; padding:4px 14px;"
            "font-family:Consolas,'Courier New',monospace;"
        )
        for i, btn in enumerate(self._whatdl_saison_btns):
            btn.setStyleSheet(_ACTIVE if i == sel else "")

    def _whatdl_pick_saison(self, idx):
        self._whatdl_sel_idx = idx
        self._whatdl_refresh()

    def _whatdl_back_action(self):
        if callable(self._whatdl_back):
            self._whatdl_back()
        else:
            self._go(self._whatdl_back if self._whatdl_back is not None else self.PAGE_MAIN)

    def _whatdl_select(self):  # pylint: disable=too-many-locals
        row       = self._whatdl_list.currentRow()
        if row < 0:
            return
        slug      = self._slug
        data      = self._anime_data
        lang      = self._lang
        saisons   = self._whatdl_saisons
        lang_data = self._whatdl_lang_data
        sel       = self._whatdl_sel_idx
        skey      = saisons[sel]
        n         = count_episodes(lang_data[skey])
        self._saison_key = skey

        _cancel = lambda: self._go(self.PAGE_WHAT_DL)  # pylint: disable=unnecessary-lambda-assignment

        if row == 0:
            # ── Reprendre depuis le dernier épisode téléchargé ────────────────
            resume = self._whatdl_resume
            if resume is None:
                self._msg("Aucun téléchargement détecté",
                          "Aucun épisode téléchargé trouvé dans le dossier de destination.\n"
                          "Lancez d'abord un téléchargement.")
                return
            if resume == "done":
                self._msg("Déjà téléchargé",
                          "Tous les épisodes sont déjà téléchargés !")
                return
            r_si, r_ep = resume
            self._start_resume(slug, data, lang, saisons, lang_data, r_si, r_ep)

        elif row == 1:
            # Toute la série
            self._start_series(slug, data, lang, saisons, lang_data)

        elif row == 2:
            # Toute la saison sélectionnée
            self._start_dl(slug, data, lang, skey, (0, n - 1))

        elif row == 3:
            # Un épisode spécifique — avec sélecteur de saison
            def _do_ep_specific(ep):
                actual_skey = self._ep_saisons_ep[self._ep_sel_idx_ep] if self._ep_saison_lbl.isVisible() and self._ep_saisons_ep else skey
                self._saison_key = actual_skey
                self._start_dl(slug, data, lang, actual_skey, (ep - 1, ep - 1))

            self._ask_ep(
                lambda n_: f"Épisode à télécharger (1-{n_})", n, 1, _do_ep_specific,
                mode_label="UN ÉPISODE SPÉCIFIQUE",
                cancel_cb=_cancel,
                show_saison=True, saisons=saisons, lang_data=lang_data, sel_idx=sel,
            )

        elif row == 4:
            # D'un épisode X à Y — sélecteur de saison sur étape X, Retour sur étape Y
            def _ask_x():
                skey_x = saisons[sel]
                n_x    = count_episodes(lang_data[skey_x])

                def _ask_y(x):
                    # Saison choisie à l'étape X
                    skey_from_x = self._ep_saisons_ep[self._ep_sel_idx_ep] if self._ep_saison_lbl.isVisible() and self._ep_saisons_ep else skey_x
                    _, _, _, typ_x = saison_key_info(skey_from_x)

                    # Filtrer les saisons disponibles pour Y :
                    #  - X = film  → Y = film uniquement (cohérence de type)
                    #  - X = saison → Y = toutes sauf film
                    if typ_x == "film":
                        saisons_y = [k for k in saisons if saison_key_info(k)[3] == "film"]
                    else:
                        saisons_y = [k for k in saisons if saison_key_info(k)[3] != "film"]

                    if not saisons_y:
                        saisons_y = [skey_from_x]

                    # Saison de départ pour Y : même que X si disponible, sinon première
                    if skey_from_x in saisons_y:
                        sel_y = saisons_y.index(skey_from_x)
                    else:
                        sel_y = 0

                    skey_y0 = saisons_y[sel_y]
                    n_y     = count_episodes(lang_data[skey_y0])

                    def _do_dl(y):
                        actual_skey_y = self._ep_saisons_ep[self._ep_sel_idx_ep] if self._ep_saison_lbl.isVisible() and self._ep_saisons_ep else skey_y0
                        self._saison_key = actual_skey_y
                        self._start_dl(slug, data, lang, actual_skey_y, (x - 1, y - 1))

                    # Afficher le sélecteur si plusieurs choix OU si X est un film
                    # (dans ce cas on affiche "Film" pour que l'utilisateur comprenne le contexte)
                    show_s_y = len(saisons_y) > 1 or typ_x == "film"
                    self._ask_ep(
                        lambda n_: f"Épisode de fin (1-{n_})", n_y, 1, _do_dl,
                        mode_label="D'UN ÉPISODE X À Y  —  Choix de l'épisode de fin (Y)",
                        cancel_cb=_cancel,
                        back_action=_ask_x,
                        show_saison=show_s_y, saisons=saisons_y, lang_data=lang_data, sel_idx=sel_y,
                    )

                self._ask_ep(
                    lambda n_: f"Épisode de départ (1-{n_})", n_x, 1, _ask_y,
                    mode_label="D'UN ÉPISODE X À Y  —  Choix de l'épisode de départ (X)",
                    cancel_cb=_cancel,
                    show_saison=True, saisons=saisons, lang_data=lang_data, sel_idx=sel,
                )

            _ask_x()

        elif row == 5:
            # Depuis un épisode
            self._ask_ep(
                lambda n_: f"Depuis l'épisode (1-{n_})", n, 1,
                lambda ep: self._start_dl(slug, data, lang, skey, (ep - 1, n - 1)),
                mode_label="DEPUIS UN ÉPISODE",
                cancel_cb=_cancel,
            )

    # ── Téléchargement ─────────────────────────────────────────────────────────

    def _start_dl(self, slug, anime_data, lang, saison_key, ep_range):
        """Lance un téléchargement sur une saison / plage d'épisodes."""
        disp, _, _, _ = saison_key_info(saison_key)
        total = ep_range[1] - ep_range[0] + 1
        self._dl_title_lbl.setText("TÉLÉCHARGEMENT EN COURS")
        self._dl_anime_lbl.setText(f"{self._anime_name}  —  {lang.upper()}  —  {disp}")
        self._dl_ep_lbl.setText("Préparation…")
        self._dl_overall.setRange(0, total)
        self._dl_overall.setValue(0)
        self._dl_ep_bar.setValue(0)
        self._dl_speed_lbl.setText("")
        self._dl_log.clear()
        self._dl_cancel_btn.setEnabled(True)
        self._dl_set_done(False)
        self._go(self.PAGE_DOWNLOAD)

        self._dl_thread = DownloadThread(
            slug, anime_data, lang, saison_key, ep_range,
            self.dest_dir_ref[0], self._anime_name, self.ffmpeg_exe, self,
        )
        self._dl_thread.ep_start.connect(self._dl_ep_start)
        self._dl_thread.ep_progress.connect(self._dl_ep_prog)
        self._dl_thread.ep_done.connect(self._dl_ep_done)
        self._dl_thread.all_done.connect(self._dl_all_done)
        self._dl_thread.start()

    def _start_series(self, slug, anime_data, lang, saisons, lang_data):
        """Télécharge toute la série saison par saison."""
        self._dl_queue     = list(saisons)
        self._dl_queue_ctx = (slug, anime_data, lang, lang_data)
        self._dl_queue_res = (0, 0)
        self._next_in_queue()

    def _start_resume(self, slug, anime_data, lang, saisons, lang_data, start_si, start_ep):
        """Reprend le téléchargement depuis start_ep dans saisons[start_si], puis
        enchaîne toutes les saisons suivantes depuis leur début."""
        r_sk = saisons[start_si]
        n_r  = count_episodes(lang_data[r_sk])
        disp, _, _, _ = saison_key_info(r_sk)

        # Les saisons après la première seront traitées via la file habituelle
        self._dl_queue     = list(saisons[start_si + 1:])
        self._dl_queue_ctx = (slug, anime_data, lang, lang_data)
        self._dl_queue_res = (0, 0)

        self._dl_title_lbl.setText("TÉLÉCHARGEMENT EN COURS  —  REPRISE")
        self._dl_anime_lbl.setText(f"{self._anime_name}  —  {lang.upper()}  —  {disp}")
        self._dl_ep_lbl.setText("Préparation…")
        total = n_r - start_ep
        self._dl_overall.setRange(0, total)
        self._dl_overall.setValue(0)
        self._dl_ep_bar.setValue(0)
        self._dl_speed_lbl.setText("")
        self._dl_log.clear()
        self._dl_log.append(f"▶  Reprise depuis {disp} — Épisode {start_ep + 1}")
        self._dl_cancel_btn.setEnabled(True)
        self._dl_set_done(False)
        self._go(self.PAGE_DOWNLOAD)

        self._dl_thread = DownloadThread(
            slug, anime_data, lang, r_sk, (start_ep, n_r - 1),
            self.dest_dir_ref[0], self._anime_name, self.ffmpeg_exe, self,
        )
        self._dl_thread.ep_start.connect(self._dl_ep_start)
        self._dl_thread.ep_progress.connect(self._dl_ep_prog)
        self._dl_thread.ep_done.connect(self._dl_ep_done)
        # Quand la première saison est terminée, continuer la file
        self._dl_thread.all_done.connect(self._dl_queue_season_done)
        self._dl_thread.start()

    def _next_in_queue(self):
        slug, anime_data, lang, lang_data = self._dl_queue_ctx
        while self._dl_queue:
            skey = self._dl_queue.pop(0)
            n    = count_episodes(lang_data.get(skey, []))
            if n:
                disp, _, _, _ = saison_key_info(skey)
                self._dl_anime_lbl.setText(
                    f"{self._anime_name}  —  {lang.upper()}  —  {disp}"
                )
                self._dl_ep_bar.setValue(0)
                self._dl_overall.setRange(0, n)
                self._dl_overall.setValue(0)
                self._dl_cancel_btn.setEnabled(True)
                self._dl_set_done(False)
                self._go(self.PAGE_DOWNLOAD)

                self._dl_thread = DownloadThread(
                    slug, anime_data, lang, skey, (0, n - 1),
                    self.dest_dir_ref[0], self._anime_name, self.ffmpeg_exe, self,
                )
                self._dl_thread.ep_start.connect(self._dl_ep_start)
                self._dl_thread.ep_progress.connect(self._dl_ep_prog)
                self._dl_thread.ep_done.connect(self._dl_ep_done)
                self._dl_thread.all_done.connect(self._dl_queue_season_done)
                self._dl_thread.start()
                return
        # File vide → fin de série
        ok, fail = self._dl_queue_res
        self._dl_log.append(f"\n✔  Série complète terminée !  {ok} succès  /  {fail} échec(s)")
        self._dl_cancel_btn.setEnabled(False)
        self._dl_set_done(True)

    def _dl_queue_season_done(self, ok, fail):
        prev_ok, prev_fail = self._dl_queue_res
        self._dl_queue_res = (prev_ok + ok, prev_fail + fail)
        self._next_in_queue()

    def _dl_ep_start(self, current, total, name):
        self._dl_ep_lbl.setText(f"Épisode {current} / {total}  —  {name}")
        self._dl_overall.setValue(current - 1)
        self._dl_ep_bar.setValue(0)
        self._dl_log.append(f"[{current}/{total}]  {name}")

    def _dl_ep_prog(self, pct, speed, eta):
        self._dl_ep_bar.setValue(int(pct))
        parts = [p for p in (speed, f"ETA {eta}" if eta else "") if p]
        self._dl_speed_lbl.setText("   ".join(parts))

    def _dl_ep_done(self, ok, name):
        self._dl_overall.setValue(self._dl_overall.value() + 1)
        if ok:
            self._dl_log.append(f"  ✔  {name}.mp4")
        else:
            self._dl_log.append(f"  ✖  {name}  — ÉCHEC")

    def _dl_set_done(self, done: bool):
        """Met à jour le bouton Terminé selon l'état du téléchargement."""
        if done:
            self._dl_done_btn.setText("✔   Terminé  —  Retour menu")
            self._dl_done_btn.setEnabled(True)
            self._dl_done_btn.setStyleSheet(
                "QPushButton {"  # pylint: disable=implicit-str-concat
                "  background-color: transparent; color: #00ff99;"
                "  border: 1px solid #00ff99; border-radius: 8px;"
                "  padding: 6px 24px;"
                "  font-family: Consolas, 'Courier New', monospace;"
                "}"
                "QPushButton:hover {"
                "  background-color: #00ff99; color: #0d0d1a;"
                "  border-color: #00ff99;"
                "}"
            )
        else:
            self._dl_done_btn.setText("✖   Pas Terminé")
            self._dl_done_btn.setEnabled(False)
            self._dl_done_btn.setStyleSheet(
                "QPushButton {"  # pylint: disable=implicit-str-concat
                "  background-color: #1a0808; color: #ff4444;"
                "  border: none; border-radius: 8px;"
                "  padding: 6px 24px;"
                "  font-family: Consolas, 'Courier New', monospace;"
                "}"
            )

    def _dl_all_done(self, ok, fail):
        self._dl_ep_bar.setValue(100)
        self._dl_overall.setValue(self._dl_overall.maximum())
        self._dl_ep_lbl.setText(f"Terminé !   {ok} succès  /  {fail} échec(s)")
        self._dl_speed_lbl.setText("")
        self._dl_log.append(f"\n✔  Terminé !  {ok} succès  /  {fail} échec(s)")
        self._dl_cancel_btn.setEnabled(False)
        self._dl_set_done(True)

    def _dl_cancel(self):
        if self._dl_thread and self._dl_thread.isRunning():
            self._dl_thread.cancel()
            self._dl_log.append("  ⚠  Annulation demandée…")
            self._dl_cancel_btn.setEnabled(False)

    # ── Paramètres ─────────────────────────────────────────────────────────────

    def _refresh_gh_btn(self):
        if not GITHUB_TOKEN:
            self._set_gh_btn.setText("✖  Désactivé")
            self._set_gh_btn.setEnabled(False)
            self._set_gh_btn.setStyleSheet("")
            return
        on = self.cfg.get("github_fallback", False)
        self._set_gh_btn.setText("✔  Activé" if on else "✖  Désactivé")
        self._set_gh_btn.setEnabled(True)
        self._set_gh_btn.setStyleSheet(
            "color:#00ff99; border-color:#00ff9955;" if on else ""
        )

    def _settings_choose_dir(self):
        path = QFileDialog.getExistingDirectory(
            self, "Choisir le dossier de téléchargement",
            self.dest_dir_ref[0] if self.dest_dir_ref else ""
        )
        if path:
            self._set_dir_edit.setText(path)
            self._settings_apply_dir()

    def _settings_apply_dir(self):
        path = self._set_dir_edit.text().strip()
        if not path:
            return
        try:
            os.makedirs(path, exist_ok=True)
            self.dest_dir_ref[0] = os.path.abspath(path)
            _save_config({"dest_dir": self.dest_dir_ref[0]})
            self._set_dir_edit.setText(self.dest_dir_ref[0])
            self._refresh_status()
        except Exception as e:
            self._msg("Erreur dossier", str(e))

    def _settings_choose_db(self):
        path = QFileDialog.getExistingDirectory(
            self, "Choisir le dossier de la base de données locale",
            LOCAL_DB_DIR,
        )
        if path:
            self._set_db_edit.setText(path)
            self._settings_apply_db()

    def _settings_apply_db(self):
        path = self._set_db_edit.text().strip()
        if not path:
            return
        try:
            os.makedirs(path, exist_ok=True)
            abs_path = os.path.abspath(path)
            _set_db_dir(abs_path)
            _save_config({"db_dir": abs_path})
            self.cfg["db_dir"] = abs_path
            self._set_db_edit.setText(abs_path)
        except Exception as e:
            self._msg("Erreur base de données", str(e))

    def _settings_toggle_gh(self):
        on = not self.cfg.get("github_fallback", False)
        self.cfg["github_fallback"] = on
        _save_config({"github_fallback": on})
        self._refresh_gh_btn()
        self._refresh_status()

    # ── Utilitaires ────────────────────────────────────────────────────────────

    def _msg(self, title, text):
        dlg = QMessageBox(self)
        dlg.setWindowTitle(title)
        dlg.setText(text)
        dlg.setStyleSheet(_QSS)
        dlg.exec()

    def closeEvent(self, event):  # pylint: disable=invalid-name
        """Arrête proprement le thread de téléchargement à la fermeture."""
        if self._dl_thread and self._dl_thread.isRunning():
            self._dl_thread.cancel()
            self._dl_thread.wait(3000)
        event.accept()


# ══════════════════════════════════════════════════════════════════════════════
#  Point d'entrée GUI
# ══════════════════════════════════════════════════════════════════════════════

def _ask_dest_dir_gui(app_ref):
    """
    Dialogue obligatoire de premier lancement (PC, config absente).
    - "Valider et démarrer" → retourne le chemin choisi (et SEULEMENT là le JSON sera créé).
    - Bouton Quitter (en haut à droite) ou croix de fermeture → sys.exit(0), rien n'est créé.
    """
    from PyQt6.QtWidgets import (  # pylint: disable=import-error
        QDialog, QVBoxLayout, QHBoxLayout, QLabel,
        QLineEdit, QPushButton, QFileDialog,
    )
    from PyQt6.QtCore import Qt  # pylint: disable=import-error

    dlg = QDialog()
    dlg.setWindowTitle("Ney-Chan — Configuration initiale")
    dlg.setFixedSize(560, 280)
    dlg.setStyleSheet(_QSS)

    # Fermer la fenêtre (X de la barre titre ou barre des tâches) = quitter
    def _on_close(event):
        event.accept()
        sys.exit(0)
    dlg.closeEvent = _on_close

    vlay = QVBoxLayout(dlg)
    vlay.setContentsMargins(32, 22, 32, 24)
    vlay.setSpacing(8)

    # ── Ligne du haut : titre + bouton Quitter ────────────────────────────────
    top_row = QHBoxLayout()
    title = QLabel("🎌  Bienvenue dans Ney-Chan !")
    title.setStyleSheet("color:#00d4ff; font-size:15px; font-weight:bold;")
    top_row.addWidget(title, 1)

    btn_quit = QPushButton("✖  Quitter")
    btn_quit.setObjectName("danger")
    btn_quit.setFixedSize(95, 28)
    btn_quit.setStyleSheet(
        "QPushButton { color:#ff6b6b; border:1px solid #ff6b6b55;"
        " border-radius:5px; font-size:11px; padding:2px 8px; }"
        "QPushButton:hover { background:#ff6b6b; color:#0d0d1a; }"
    )
    btn_quit.clicked.connect(lambda: sys.exit(0))
    top_row.addWidget(btn_quit)
    vlay.addLayout(top_row)

    sub = QLabel("Aucune configuration trouvée. Choisissez un dossier de téléchargement.")
    sub.setObjectName("subtitle")
    vlay.addWidget(sub)

    vlay.addSpacing(12)

    # ── Champ + bouton Choisir ────────────────────────────────────────────────
    row = QHBoxLayout()
    edit = QLineEdit()
    edit.setPlaceholderText("Chemin du dossier…")
    edit.setFixedHeight(34)

    btn_browse = QPushButton("📂 Choisir")
    btn_browse.setFixedWidth(110)

    def _browse():
        p = QFileDialog.getExistingDirectory(dlg, "Choisir le dossier de téléchargement", "")
        if p:
            edit.setText(p)

    btn_browse.clicked.connect(_browse)
    row.addWidget(edit, 1)
    row.addSpacing(6)
    row.addWidget(btn_browse)
    vlay.addLayout(row)

    hint = QLabel("⚠  Ce dossier est obligatoire. Il recevra tous vos téléchargements.")
    hint.setStyleSheet("color:#ffaa44; font-size:10px;")
    vlay.addWidget(hint)

    vlay.addStretch()

    # ── Bouton Valider ────────────────────────────────────────────────────────
    btn_ok = QPushButton("✔  Valider et démarrer")
    btn_ok.setObjectName("success")
    btn_ok.setFixedHeight(38)

    result_holder = [None]

    def _validate():
        p = edit.text().strip()
        if not p:
            hint.setText("⚠  Veuillez saisir ou choisir un dossier avant de valider.")
            hint.setStyleSheet("color:#ff6b6b; font-size:10px;")
            return
        try:
            os.makedirs(p, exist_ok=True)
            result_holder[0] = os.path.abspath(p)
            dlg.accept()
        except Exception as e:
            hint.setText(f"⚠  Impossible de créer le dossier : {e}")
            hint.setStyleSheet("color:#ff6b6b; font-size:10px;")

    btn_ok.clicked.connect(_validate)
    edit.returnPressed.connect(_validate)
    vlay.addWidget(btn_ok)

    dlg.exec()
    return result_holder[0]


def main_gui():
    """Lance l'interface PyQt6 (PC uniquement)."""
    # Désactive le clear terminal pour ne pas polluer l'éventuelle console
    ConsoleUI.clear = staticmethod(lambda: None)

    app = QApplication(sys.argv)
    app.setApplicationName("Ney-Chan")
    app.setApplicationVersion(VERSION)
    app.setStyleSheet(_QSS)

    cfg      = _load_config()
    dest_ref = [cfg.get("dest_dir", "")]

    # ── Écran de démarrage ───────────────────────────────────────────────────
    splash = QWidget()
    splash.setWindowTitle("Ney-Chan — Initialisation")
    splash.setFixedSize(420, 200)
    splash.setStyleSheet(_QSS)
    slay = QVBoxLayout(splash)
    slay.setAlignment(Qt.AlignmentFlag.AlignCenter)
    sl1 = QLabel("🎌  NEY-CHAN")
    sl1.setStyleSheet("color:#00d4ff; font-size:26px; font-weight:bold;")
    sl1.setAlignment(Qt.AlignmentFlag.AlignCenter)
    sl2 = QLabel("Initialisation…")
    sl2.setStyleSheet("color:#7788aa; font-size:11px;")
    sl2.setAlignment(Qt.AlignmentFlag.AlignCenter)
    spb = QProgressBar()
    spb.setRange(0, 0)
    spb.setFixedWidth(300)
    slay.addStretch()
    slay.addWidget(sl1)
    slay.addSpacing(8)
    slay.addWidget(sl2)
    slay.addSpacing(14)
    slay.addWidget(spb, 0, Qt.AlignmentFlag.AlignCenter)
    slay.addStretch()
    splash.show()

    win_holder = [None]   # garde une référence à la fenêtre principale

    def _on_init_progress(msg):
        sl2.setText(msg)

    def _on_init_done(ffmpeg_exe):
        splash.hide()
        splash.deleteLater()

        # ── Résolution du dossier de destination ────────────────────────────
        config_exists = os.path.isfile(_config_path())
        saved = cfg.get("dest_dir", "")

        if not config_exists:
            # Premier lancement : demande obligatoire.
            # Le JSON n'est créé QUE si l'utilisateur valide.
            # Si l'utilisateur quitte, _ask_dest_dir_gui appelle sys.exit(0).
            chosen = _ask_dest_dir_gui(app)
            if chosen:
                dest_ref[0] = chosen
                _save_config({"dest_dir": chosen})
            # Si chosen est None ici c'est impossible (sys.exit déjà appelé)
        elif not saved or not os.path.isdir(saved):
            # Config existe mais dossier invalide : fallback silencieux
            if IS_WINDOWS:
                la = os.environ.get("LOCALAPPDATA", os.path.expanduser("~"))
                fallback = os.path.join(la, "Koyney", "Ney-Chan", "Downloads")
            else:
                fallback = os.path.join(os.path.expanduser("~"), "Animes")
            try:
                os.makedirs(fallback, exist_ok=True)
                dest_ref[0] = fallback
            except Exception:
                dest_ref[0] = os.path.abspath(_BASE_DIR)
            _save_config({"dest_dir": dest_ref[0]})
        else:
            dest_ref[0] = saved

        win = NeyChanWindow(cfg, dest_ref, ffmpeg_exe or None)
        init_db_dir(cfg)   # applique db_dir depuis la config
        win._go(NeyChanWindow.PAGE_MAIN)  # pylint: disable=protected-access
        win.show()
        win_holder[0] = win

    init_t = InitThread()
    init_t.progress.connect(_on_init_progress)
    init_t.finished.connect(_on_init_done)
    init_t.start()

    sys.exit(app.exec())


# ══════════════════════════════════════════════════════════════════════════════
#  Config
# ══════════════════════════════════════════════════════════════════════════════
def _config_path():
    if IS_TERMUX:
        return os.path.join(_BASE_DIR, "ney-chan_config.json")
    if IS_WINDOWS:
        la = os.environ.get("LOCALAPPDATA", os.path.expanduser("~"))
        return os.path.join(la, "Koyney", "Ney-Chan", "ney-chan_config.json")
    return os.path.join(_BASE_DIR, "ney-chan_config.json")

def _load_config():
    try:
        with open(_config_path(), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def _save_config(data):
    path = _config_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        existing = _load_config()
        existing.update(data)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(existing, f, indent=2, ensure_ascii=False)
    except Exception:
        pass

def _is_in_appdata():
    if not IS_WINDOWS:
        return False
    la = os.environ.get("LOCALAPPDATA", "")
    if not la:
        return False
    appdata = os.path.join(la, "Koyney", "Ney-Chan")
    try:
        return os.path.normcase(os.path.abspath(_BASE_DIR)).startswith(
            os.path.normcase(os.path.abspath(appdata))
        )
    except Exception:
        return False


# ══════════════════════════════════════════════════════════════════════════════
#  Utilitaires slugs / saisons
# ══════════════════════════════════════════════════════════════════════════════
def normalize_query(text):
    text = text.lower().strip()
    for ch in ("'", "\u2019", "\u2018", "`"):
        text = text.replace(ch, "-")
    text = text.replace(" ", "-")
    text = re.sub(r"[^a-z0-9\-]", "-", text)
    text = re.sub(r"-+", "-", text)
    return text.strip("-")

def slug_to_display(slug):
    return " ".join(w.capitalize() for w in slug.split("-"))

def saison_key_info(key):
    if key == "film":
        return "Film", "Film", "film", "film"
    if key == "oav":
        return "OAV", "OAV", "oav", "oav"
    m = re.match(r"^s(\d+)(hs)?(?:-(\d+))?$", key, re.IGNORECASE)
    if m:
        num   = m.group(1)
        hs    = bool(m.group(2))
        part  = m.group(3)
        disp  = f"Saison {num}"
        fold  = f"Saison {num}"
        pref  = f"s{num}"
        if hs:
            disp += " HS"; fold += " HS"; pref += "hs"
        if part:
            disp += f" - Partie {part}"
            fold += f" - Partie {part}"
            pref += f"p{part}"
        return disp, fold, pref, "saison"
    return key.upper(), key, key, "autre"

def ep_filename(saison_key, ep_idx):
    _, _, prefix, typ = saison_key_info(saison_key)
    if typ in ("film", "oav"):
        return f"{prefix}{ep_idx + 1}"
    return f"ep{ep_idx + 1:02d}_{prefix}"

def count_episodes(blocs):
    return max((len(b) for b in blocs), default=0)

def get_lecteur(url):
    if "vidmoly" in url:  return "vidmoly"
    if "sibnet"  in url:  return "sibnet"
    if "sendvid" in url:  return "sendvid"
    return "inconnu"

def sort_blocs_vidmoly_last(blocs):
    def priority(i):
        if not blocs[i]: return 2
        return 1 if get_lecteur(blocs[i][0]) == "vidmoly" else 0
    return sorted(range(len(blocs)), key=priority)


# ══════════════════════════════════════════════════════════════════════════════
#  Installation des dependances
# ══════════════════════════════════════════════════════════════════════════════
def setup_dependencies():
    for pkg, pip_name in [("yt_dlp", "yt-dlp"), ("requests", "requests"),
                          ("imageio_ffmpeg", "imageio-ffmpeg")]:
        try:
            __import__(pkg)
        except ImportError:
            ConsoleUI.info(f"Installation de {pip_name}...")
            subprocess.check_call(
                [sys.executable, "-m", "pip", "install", pip_name],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            ConsoleUI.success(f"{pip_name} installe.")

def setup_ffmpeg():
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg
        src     = imageio_ffmpeg.get_ffmpeg_exe()
        exe     = "ffmpeg.exe" if IS_WINDOWS else "ffmpeg"
        tmp_dir = os.path.join(tempfile.gettempdir(), "neychan_ffmpeg")
        os.makedirs(tmp_dir, exist_ok=True)
        dst = os.path.join(tmp_dir, exe)
        if not os.path.exists(dst) or os.path.getsize(dst) != os.path.getsize(src):
            shutil.copy2(src, dst)
            if not IS_WINDOWS:
                os.chmod(dst, 0o755)
        return dst
    except Exception:
        return None


# ══════════════════════════════════════════════════════════════════════════════
#  Sources de donnees
# ══════════════════════════════════════════════════════════════════════════════

# -- 1. Local -----------------------------------------------------------------
def search_local(query):
    if not os.path.exists(LOCAL_IDX_FILE):
        return []
    try:
        with open(LOCAL_IDX_FILE, encoding="utf-8") as f:
            index = json.load(f)
        return [s for s in index if query in s]
    except Exception:
        return []

def load_anime_local(slug):
    path = os.path.join(LOCAL_ANI_DIR, f"{slug}.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None

# -- 2. GitHub (repo prive Koyney/Anime-Sama) ---------------------------------
def load_anime_github(slug):
    if not GITHUB_TOKEN:
        return None
    import requests as _req
    url = (f"https://raw.githubusercontent.com/{GITHUB_REPO}"
           f"/{GITHUB_BRANCH}/animes/{slug}.json")
    try:
        r = _req.get(
            url,
            headers={"Authorization": f"token {GITHUB_TOKEN}"},
            timeout=10,
        )
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return None

# -- 3. Scraping anime-sama (methodes issues de get_data.py) ------------------
import requests as _req_mod
from requests.adapters import HTTPAdapter as _HTTPAdapter

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}
_session = _req_mod.Session()
_session.headers.update(_HEADERS)
_adapter = _HTTPAdapter(pool_connections=5, pool_maxsize=5)
_session.mount("https://", _adapter)
_session.mount("http://",  _adapter)

def _get_active_domain():
    try:
        r = _session.get("https://anime-sama.pw/", timeout=10)
        for pattern in [
            r'class="btn-primary"\s+href="(https?://anime-sama\.(?!pw)[a-z]+)"',
            r'href="(https?://anime-sama\.(?!pw)[a-z]+)"',
        ]:
            m = re.search(pattern, r.text)
            if m:
                domain = m.group(1).rstrip("/")
                try:
                    check = _session.head(domain, timeout=8, allow_redirects=True)
                    final = check.url.rstrip("/")
                    if "anime-sama" in final and "anime-sama.pw" not in final:
                        base = final.split("/catalogue")[0] if "/catalogue" in final else final
                        return f"{base}/catalogue/"
                except Exception:
                    pass
                return f"{domain}/catalogue/"
    except Exception:
        pass
    return None

def _get_saisons_from_page(base_url, anime_id):
    url = f"{base_url}{anime_id}/"
    try:
        r = _session.get(url, timeout=6)
        if r.status_code != 200:
            return None
        html = re.sub(r"<!--.*?-->", "", r.text, flags=re.DOTALL)
        h2_pat = (
            r'class="text-white text-xl font-bold uppercase '
            r'border-b-2 mt-5 border-slate-500"[^>]*>\s*Anime\s*<'
        )
        m = re.search(h2_pat, html)
        if not m:
            return []
        html_after   = html[m.end():]
        script_match = re.search(r"<script[^>]*>(.*?)</script>", html_after, re.DOTALL)
        if not script_match:
            return []
        raw_urls = re.findall(
            r'panneauAnime\(\s*"[^"]+"\s*,\s*"([^"]+)"\s*\)',
            script_match.group(1),
        )
        return list(dict.fromkeys(u.split("/")[0] for u in raw_urls))
    except Exception:
        return None

def _classify_link(url):
    if "sibnet" in url and re.search(r"videoid=\d+", url):
        return "sibnet", url
    if "sendvid" in url and re.search(r"/embed/[a-zA-Z0-9]+", url):
        return "sendvid", url
    if "vidmoly" in url and re.search(r"/embed-[a-zA-Z0-9]+\.html", url):
        return "vidmoly", url
    return None, None

def _parse_episodes_js(js_text):
    eps_blocks = re.findall(r"var\s+eps\d+\s*=\s*\[(.*?)\]\s*;", js_text, re.DOTALL)
    result = []
    for block in eps_blocks:
        urls  = re.findall(r"'(https?://[^']+)'", block)
        valid = [v for u in urls for t, v in [_classify_link(u)] if t]
        if valid:
            result.append(valid)
    return result

def _saison_to_key(s):
    return ("s" + s[len("saison"):]) if s.startswith("saison") else s

def _fetch_episodes(base_url, anime_id, season_str, lang):
    url = f"{base_url}{anime_id}/{season_str}/{lang}/episodes.js"
    try:
        r = _session.get(url, timeout=5)
        if r.status_code == 200 and r.text.strip():
            return r.text
    except Exception:
        pass
    return None

def _scan_variantes(base_url, anime_id, season_str, lang, lang_data):
    vi = 1; miss = 0
    while miss < 2:
        v  = f"{season_str}-{vi}"
        js = _fetch_episodes(base_url, anime_id, v, lang)
        if js:
            eps = _parse_episodes_js(js)
            if eps:
                lang_data[_saison_to_key(v)] = eps
            miss = 0
        else:
            miss += 1
        vi += 1

def scrape_anime_data(slug):
    base_url = _get_active_domain()
    if not base_url:
        return None
    saisons = _get_saisons_from_page(base_url, slug)
    if saisons is None:
        return None
    if not saisons:
        return {}
    result = {}
    for lang in ALL_LANGUAGES:
        lang_data = {}
        for season_str in saisons:
            js = _fetch_episodes(base_url, slug, season_str, lang)
            if js:
                eps = _parse_episodes_js(js)
                if eps:
                    lang_data[_saison_to_key(season_str)] = eps
            _scan_variantes(base_url, slug, season_str, lang, lang_data)
        if lang_data:
            result[lang] = lang_data
    return result

def scrape_saison_data(slug, lang, saison_key):
    """Re-scrape une seule saison pour obtenir des URLs fraiches."""
    base_url = _get_active_domain()
    if not base_url:
        return []
    if re.match(r"^s\d", saison_key):
        season_str = "saison" + saison_key[1:]
    else:
        season_str = saison_key
    js = _fetch_episodes(base_url, slug, season_str, lang)
    if js:
        return _parse_episodes_js(js)
    return []


# ══════════════════════════════════════════════════════════════════════════════
#  Telechargement
# ══════════════════════════════════════════════════════════════════════════════
def _download_url(url, out_path, ffmpeg_exe=None):
    """
    Telechargement via yt-dlp :
    - concurrent_fragment_downloads=4 : maximise la vitesse sur un seul fichier
    - continuedl=True                 : reprend les fichiers .part interrompus
    - Affiche une barre de progression en temps reel
    Retourne True si succes.
    """
    import yt_dlp

    bar_w = 28

    def _progress_hook(d):
        # En mode Qt, déléguer au callback graphique
        if _qt_progress_callback is not None:
            _qt_progress_callback(d)
            return
        if d["status"] == "downloading":
            pct_str = d.get("_percent_str", "  ?%").strip()
            speed   = d.get("_speed_str",   "").strip()
            eta     = d.get("_eta_str",     "").strip()
            try:
                pct = float(pct_str.replace("%", "").strip())
            except ValueError:
                pct = 0.0
            filled = int(bar_w * pct / 100)
            bar    = "#" * filled + "." * (bar_w - filled)
            parts  = [f"\r  {ConsoleUI.CYAN}[{bar}]{ConsoleUI.RESET} {pct_str:>6}"]
            if speed: parts.append(f"  {ConsoleUI.DIM}{speed}{ConsoleUI.RESET}")
            if eta:   parts.append(f"  {ConsoleUI.DIM}ETA {eta}{ConsoleUI.RESET}")
            print("".join(parts), end="", flush=True)
        elif d["status"] == "finished":
            bar = "#" * bar_w
            print(f"\r  {ConsoleUI.GREEN}[{bar}]{ConsoleUI.RESET} 100%  ", flush=True)

    base = out_path if not out_path.endswith(".mp4") else out_path[:-4]
    opts = {
        "outtmpl":                       base + ".%(ext)s",
        "format":                        "bestvideo+bestaudio/best",
        "merge_output_format":           "mp4",
        "progress_hooks":                [_progress_hook],
        "concurrent_fragment_downloads": 4,
        "quiet":                         True,
        "no_warnings":                   True,
        "noprogress":                    False,
        "continuedl":                    True,
    }
    if ffmpeg_exe:
        opts["ffmpeg_location"] = os.path.dirname(ffmpeg_exe)

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])
        for ext in (".mp4", ".mkv", ".webm", ".ts", ".avi"):
            if os.path.exists(base + ext):
                return True
        return False
    except Exception:
        return False


def download_episode(slug, saison_key, ep_idx, blocs, dest_dir,
                     anime_name, lang, ffmpeg_exe=None):
    """
    Tente le telechargement d'un episode :
    1. Essaie les blocs dans l'ordre (vidmoly en dernier)
    2. Si tous echouent, re-scrape la saison depuis anime-sama
    """
    _, folder_name, _, _ = saison_key_info(saison_key)
    ep_dir    = os.path.join(dest_dir, f"{anime_name} {lang.upper()}", folder_name)
    os.makedirs(ep_dir, exist_ok=True)
    base_name = ep_filename(saison_key, ep_idx)
    out_path  = os.path.join(ep_dir, base_name + ".mp4")

    if os.path.exists(out_path):
        ConsoleUI.success(f"{base_name}.mp4 deja present -- ignore.")
        return True

    def _try_url(url):
        lecteur = get_lecteur(url)
        print(f"  {ConsoleUI.DIM}  Lecteur : {lecteur}{ConsoleUI.RESET}")
        return _download_url(url, out_path, ffmpeg_exe)

    order = sort_blocs_vidmoly_last(blocs)
    for bi in order:
        bloc = blocs[bi]
        if ep_idx < len(bloc):
            if _try_url(bloc[ep_idx]):
                ConsoleUI.success(f"{base_name}.mp4 telecharge.")
                return True

    ConsoleUI.warn("Tous les lecteurs ont echoue -- re-scrape anime-sama...")
    fresh_blocs = scrape_saison_data(slug, lang, saison_key)
    if fresh_blocs:
        order2 = sort_blocs_vidmoly_last(fresh_blocs)
        for bi in order2:
            bloc = fresh_blocs[bi]
            if ep_idx < len(bloc):
                if _try_url(bloc[ep_idx]):
                    ConsoleUI.success(f"{base_name}.mp4 telecharge (re-scrape).")
                    return True

    ConsoleUI.err(f"Impossible de telecharger l'episode {ep_idx + 1}.")
    return False


def run_download(slug, anime_data, lang, saison_key, ep_range,
                 dest_dir, anime_name, ffmpeg_exe=None):
    blocs     = anime_data.get(lang, {}).get(saison_key, [])
    total_eps = count_episodes(blocs)
    if not blocs or not total_eps:
        ConsoleUI.err("Aucune donnee disponible pour cette saison/langue.")
        return 0, 0

    ep_start = max(0, ep_range[0])
    ep_end   = min(ep_range[1], total_eps - 1)
    total    = ep_end - ep_start + 1
    disp, _, _, _ = saison_key_info(saison_key)

    ConsoleUI.clear()
    print(ConsoleUI.BANNER)
    print(f"  {ConsoleUI.CYAN}{'='*58}{ConsoleUI.RESET}")
    print(f"  {ConsoleUI.BOLD}  Telechargement en cours{ConsoleUI.RESET}")
    print(f"  {ConsoleUI.CYAN}{'='*58}{ConsoleUI.RESET}")
    print(f"\n  {ConsoleUI.BOLD}{anime_name}{ConsoleUI.RESET}  "
          f"  {ConsoleUI.CYAN}{lang.upper()}{ConsoleUI.RESET}  --  {disp}")
    print(f"  {ConsoleUI.DIM}Episodes {ep_start+1} -> {ep_end+1}  "
          f"({total} episode(s) / {total_eps} total){ConsoleUI.RESET}\n")
    ConsoleUI.sep()

    success = fail = 0
    for i, ep_idx in enumerate(range(ep_start, ep_end + 1)):
        print(f"  {ConsoleUI.CYAN}[{i+1}/{total}]{ConsoleUI.RESET}"
              f"  Episode {ep_idx+1}")
        ok = download_episode(slug, saison_key, ep_idx, blocs,
                              dest_dir, anime_name, lang, ffmpeg_exe)
        if ok: success += 1
        else:  fail    += 1
        print()

    return success, fail


# ══════════════════════════════════════════════════════════════════════════════
#  Menus de telechargement
# ══════════════════════════════════════════════════════════════════════════════
def _ask_ep_number(prompt, max_val, min_val=1):
    while True:
        val = ConsoleUI.input_screen(
            "NUMERO D'EPISODE",
            f"{prompt} (1-{max_val})",
            allow_esc=True,
        )
        if val is None:
            return None
        try:
            n = int(val)
            if min_val <= n <= max_val:
                return n
            ConsoleUI.warn(f"Entrez un nombre entre {min_val} et {max_val}.")
        except ValueError:
            ConsoleUI.warn("Entrez un nombre entier.")
        time.sleep(0.6)

def _pick_saison(saisons, lang_data, anime_name, lang):
    if len(saisons) == 1:
        return saisons[0]
    opts = []
    for k in saisons:
        d, _, _, _ = saison_key_info(k)
        n = count_episodes(lang_data[k])
        opts.append(f"{d}  ({n} episode(s))")
    opts.append("<  Retour")
    idx = ConsoleUI.navigate(opts, "CHOISIR UNE SAISON",
                             f"{anime_name}  --  {lang.upper()}")
    if idx < 0 or idx >= len(saisons):
        return None
    return saisons[idx]

def _find_resume_point(dest_dir, anime_name, lang, anime_data):
    """
    Parcourt les dossiers locaux pour trouver le dernier episode
    telecharge ou en cours (.part).
    Retourne :
      None         -> aucun fichier trouve (rien a reprendre)
      "done"       -> tous les episodes sont deja presents
      (si, ep_idx) -> (index saison dans la liste, index du prochain ep a telecharger)
    """
    lang_data = anime_data.get(lang, {})
    saisons   = list(lang_data.keys())

    last_si = -1
    last_ep = -1

    for si, sk in enumerate(saisons):
        _, folder_name, _, _ = saison_key_info(sk)
        ep_dir = os.path.join(dest_dir, f"{anime_name} {lang.upper()}", folder_name)
        if not os.path.isdir(ep_dir):
            continue
        n = count_episodes(lang_data[sk])
        for ep_idx in range(n - 1, -1, -1):
            base_name = ep_filename(sk, ep_idx)
            found = False
            # Fichier complet (plusieurs extensions possibles)
            for ext in (".mp4", ".mkv", ".webm", ".ts", ".avi"):
                if os.path.exists(os.path.join(ep_dir, base_name + ext)):
                    found = True
                    break
            # Fichier partiel en cours de telechargement
            if not found:
                for ext in (".mp4.part", ".mkv.part", ".webm.part", ".ts.part"):
                    if os.path.exists(os.path.join(ep_dir, base_name + ext)):
                        found = True
                        break
            if found:
                last_si = si
                last_ep = ep_idx
                break  # on a le dernier ep de cette saison, on continue les suivantes

    if last_si == -1:
        return None  # Aucun fichier detecte

    sk     = saisons[last_si]
    n      = count_episodes(lang_data[sk])
    next_ep = last_ep + 1

    if next_ep < n:
        return last_si, next_ep          # Il reste des episodes dans cette saison
    if last_si + 1 < len(saisons):
        return last_si + 1, 0            # Passer a la saison suivante
    return "done"                        # Tout est deja telecharge


def menu_what_to_download(slug, anime_data, lang, dest_dir, anime_name, ffmpeg_exe):
    lang_data = anime_data.get(lang, {})
    saisons   = list(lang_data.keys())
    if not saisons:
        ConsoleUI.result_screen([
            f"  {ConsoleUI.RED}X  Aucune saison disponible en {lang.upper()}.{ConsoleUI.RESET}",
        ])
        return

    total_all    = sum(count_episodes(lang_data[k]) for k in saisons)
    saison_count = len(saisons)

    # ── Detection du point de reprise ─────────────────────────────────────────
    resume = _find_resume_point(dest_dir, anime_name, lang, anime_data)
    if resume is None:
        resume_label = "▶  Reprendre  (aucun telechargement detecte)"
    elif resume == "done":
        resume_label = "▶  Reprendre  (tout est deja telecharge)"
    else:
        r_si, r_ep = resume
        r_sk = saisons[r_si]
        r_d, _, _, _ = saison_key_info(r_sk)
        resume_label = f"▶  Reprendre depuis {r_d} -- Episode {r_ep + 1}"

    choices = [
        resume_label,
        f"Toute la serie  ({total_all} ep. -- {saison_count} saison(s))",
        "Une saison specifique",
        "Un episode specifique",
        "D'un episode X a Y",
        "Commencer depuis un episode",
        "<  Retour",
    ]
    choice = ConsoleUI.navigate(
        choices, "QUE TELECHARGER ?",
        f"{anime_name}  --  {lang.upper()}  --  {total_all} episode(s)",
    )

    # ── Reprendre depuis le dernier episode ───────────────────────────────────
    if choice == 0:
        if resume is None:
            ConsoleUI.result_screen([
                f"  {ConsoleUI.YELLOW}⚠  Aucun episode telecharge trouve.{ConsoleUI.RESET}",
                f"  {ConsoleUI.DIM}Lancez d'abord un telechargement.{ConsoleUI.RESET}",
            ])
            return
        if resume == "done":
            ConsoleUI.result_screen([
                f"  {ConsoleUI.GREEN}✔  Tous les episodes sont deja telecharges !{ConsoleUI.RESET}",
            ])
            return
        r_si, r_ep = resume
        r_sk = saisons[r_si]
        r_d, _, _, _ = saison_key_info(r_sk)
        total_ok = total_fail = 0
        # Finir la saison en cours depuis le prochain episode
        n_r = count_episodes(lang_data[r_sk])
        ok, fail = run_download(slug, anime_data, lang, r_sk,
                                (r_ep, n_r - 1), dest_dir, anime_name, ffmpeg_exe)
        total_ok += ok; total_fail += fail
        # Telecharger les saisons suivantes en entier
        for sk in saisons[r_si + 1:]:
            n = count_episodes(lang_data[sk])
            if n:
                ok, fail = run_download(slug, anime_data, lang, sk,
                                        (0, n - 1), dest_dir, anime_name, ffmpeg_exe)
                total_ok += ok; total_fail += fail
        ConsoleUI.result_screen([
            f"  {ConsoleUI.GREEN}OK Reprise depuis {r_d} ep.{r_ep + 1} terminee !{ConsoleUI.RESET}",
            f"  {ConsoleUI.DIM}{total_ok} succes  --  {total_fail} echec(s){ConsoleUI.RESET}",
            f"  {ConsoleUI.DIM}Dossier : {dest_dir}{os.sep}{anime_name} {lang.upper()}{ConsoleUI.RESET}",
        ])

    elif choice == 1:
        total_ok = total_fail = 0
        for sk in saisons:
            n = count_episodes(lang_data[sk])
            if n:
                ok, fail = run_download(slug, anime_data, lang, sk,
                                        (0, n - 1), dest_dir, anime_name, ffmpeg_exe)
                total_ok += ok; total_fail += fail
        ConsoleUI.result_screen([
            f"  {ConsoleUI.GREEN}OK Serie complete telechargee !{ConsoleUI.RESET}",
            f"  {ConsoleUI.DIM}{total_ok} succes  --  {total_fail} echec(s){ConsoleUI.RESET}",
            f"  {ConsoleUI.DIM}Dossier : {dest_dir}{os.sep}{anime_name} {lang.upper()}{ConsoleUI.RESET}",
        ])

    elif choice == 2:
        sk = _pick_saison(saisons, lang_data, anime_name, lang)
        if sk is None: return
        n = count_episodes(lang_data[sk])
        ok, fail = run_download(slug, anime_data, lang, sk,
                                (0, n - 1), dest_dir, anime_name, ffmpeg_exe)
        d, _, _, _ = saison_key_info(sk)
        ConsoleUI.result_screen([
            f"  {ConsoleUI.GREEN}OK {d} telechargee !{ConsoleUI.RESET}",
            f"  {ConsoleUI.DIM}{ok} succes  --  {fail} echec(s){ConsoleUI.RESET}",
        ])

    elif choice == 3:
        sk = _pick_saison(saisons, lang_data, anime_name, lang)
        if sk is None: return
        n  = count_episodes(lang_data[sk])
        ep = _ask_ep_number("Numero d'episode", n)
        if ep is None: return
        run_download(slug, anime_data, lang, sk,
                     (ep - 1, ep - 1), dest_dir, anime_name, ffmpeg_exe)

    elif choice == 4:
        sk = _pick_saison(saisons, lang_data, anime_name, lang)
        if sk is None: return
        n = count_episodes(lang_data[sk])
        x = _ask_ep_number("De l'episode", n)
        if x is None: return
        y = _ask_ep_number(f"A l'episode (min {x})", n, min_val=x)
        if y is None: return
        ok, fail = run_download(slug, anime_data, lang, sk,
                                (x - 1, y - 1), dest_dir, anime_name, ffmpeg_exe)
        ConsoleUI.result_screen([
            f"  {ConsoleUI.GREEN}OK Episodes {x}->{y} telecharges !{ConsoleUI.RESET}",
            f"  {ConsoleUI.DIM}{ok} succes  --  {fail} echec(s){ConsoleUI.RESET}",
        ])

    elif choice == 5:
        sk = _pick_saison(saisons, lang_data, anime_name, lang)
        if sk is None: return
        n  = count_episodes(lang_data[sk])
        ep = _ask_ep_number("Depuis l'episode", n)
        if ep is None: return
        ok, fail = run_download(slug, anime_data, lang, sk,
                                (ep - 1, n - 1), dest_dir, anime_name, ffmpeg_exe)
        ConsoleUI.result_screen([
            f"  {ConsoleUI.GREEN}OK Episodes {ep}->{n} telecharges !{ConsoleUI.RESET}",
            f"  {ConsoleUI.DIM}{ok} succes  --  {fail} echec(s){ConsoleUI.RESET}",
        ])


# ══════════════════════════════════════════════════════════════════════════════
#  Menu Recherche
# ══════════════════════════════════════════════════════════════════════════════
def menu_search(dest_dir, ffmpeg_exe, cfg):
    query_raw = ConsoleUI.input_screen(
        "RECHERCHER UN ANIME",
        "Nom de l'anime",
        subtitle="Recherche locale, puis GitHub (si actif), puis anime-sama.",
        allow_esc=True,
    )
    if not query_raw:
        return

    query   = normalize_query(query_raw)
    results = search_local(query)

    # Si pas de résultat local : on tente directement avec le slug du query
    if not results:
        results = [query]

    if len(results) == 1:
        slug = results[0]
    else:
        opts = [slug_to_display(s) for s in results] + ["<  Retour"]
        idx  = ConsoleUI.navigate(opts, "RESULTATS",
                                  f"{len(results)} resultat(s) pour '{query_raw}'")
        if idx < 0 or idx >= len(results):
            return
        slug = results[idx]

    anime_name = slug_to_display(slug)

    ConsoleUI.clear()
    _sbanner = ConsoleUI.TERMUX_BANNER if IS_TERMUX else ConsoleUI.BANNER
    print(_sbanner)
    print()
    ConsoleUI.info(f"Chargement des donnees pour {ConsoleUI.BOLD}{anime_name}{ConsoleUI.RESET}...")

    anime_data = load_anime_local(slug)

    if anime_data is None and cfg.get("github_fallback") and GITHUB_TOKEN:
        ConsoleUI.info("Non trouve en local -> essai GitHub (repo prive)...")
        anime_data = load_anime_github(slug)

    if anime_data is None:
        ConsoleUI.info("Non trouve en local/GitHub -> scraping anime-sama en direct...")
        anime_data = scrape_anime_data(slug)

    if anime_data is None:
        ConsoleUI.result_screen([
            f"  {ConsoleUI.RED}✖  Anime introuvable : {anime_name}{ConsoleUI.RESET}",
            f"  {ConsoleUI.DIM}Verifiez le nom ou sa disponibilite sur anime-sama.{ConsoleUI.RESET}",
        ])
        return

    if not anime_data:
        ConsoleUI.result_screen([
            f"  {ConsoleUI.YELLOW}⚠  {anime_name} existe mais "
            f"aucune video n'est disponible (manga uniquement ?).{ConsoleUI.RESET}",
        ])
        return

    langs_dispo = [l for l in ALL_LANGUAGES if l in anime_data]
    if not langs_dispo:
        ConsoleUI.result_screen([
            f"  {ConsoleUI.RED}✖  Aucune langue disponible pour {anime_name}.{ConsoleUI.RESET}",
        ])
        return

    lang_opts = [LANG_LABELS.get(l, l.upper()) for l in langs_dispo] + ["<  Retour"]
    lang_idx  = ConsoleUI.navigate(lang_opts, "CHOISIR UNE LANGUE", anime_name)
    if lang_idx < 0 or lang_idx >= len(langs_dispo):
        return
    lang = langs_dispo[lang_idx]

    menu_what_to_download(slug, anime_data, lang, dest_dir, anime_name, ffmpeg_exe)


# ══════════════════════════════════════════════════════════════════════════════
#  Menu Parametres
# ══════════════════════════════════════════════════════════════════════════════
def menu_settings(dest_dir_ref, cfg, ffmpeg_exe):
    # Sur Termux/PC sans token : forcer github_fallback à False si token absent
    if not GITHUB_TOKEN:
        cfg["github_fallback"] = False
        if not IS_TERMUX:
            _save_config({"github_fallback": False})

    while True:
        github_on   = cfg.get("github_fallback", False)
        github_etat = (f"{ConsoleUI.GREEN}actif{ConsoleUI.RESET}"
                       if github_on else f"{ConsoleUI.RED}desactive{ConsoleUI.RESET}")
        token_ok    = (f"{ConsoleUI.GREEN}present{ConsoleUI.RESET}"
                       if GITHUB_TOKEN else f"{ConsoleUI.YELLOW}manquant (.env){ConsoleUI.RESET}")
        ffmpeg_info = os.path.basename(ffmpeg_exe) if ffmpeg_exe else "introuvable"

        opts = [
            f"Dossier de telechargement   ({dest_dir_ref[0]})",
            f"Base de donnees locale      ({LOCAL_DB_DIR})",
            f"GitHub fallback : {github_etat}  [token : {token_ok}]",
            "<  Retour",
        ]
        choice = ConsoleUI.navigate(opts, "PARAMETRES")

        if choice == 0:
            new = ConsoleUI.input_screen(
                "DOSSIER DE TELECHARGEMENT",
                "Chemin complet du dossier",
                subtitle=f"Actuel : {dest_dir_ref[0]}",
                allow_esc=True,
            )
            if new:
                try:
                    os.makedirs(new, exist_ok=True)
                    dest_dir_ref[0] = os.path.abspath(new)
                    cfg["dest_dir"] = dest_dir_ref[0]
                    if not IS_TERMUX:
                        _save_config({"dest_dir": dest_dir_ref[0]})
                    ConsoleUI.result_screen([
                        f"  {ConsoleUI.GREEN}✔  Dossier mis a jour.{ConsoleUI.RESET}",
                        f"  {ConsoleUI.CYAN}   {dest_dir_ref[0]}{ConsoleUI.RESET}",
                        *([] if not IS_TERMUX else [
                            f"  {ConsoleUI.YELLOW}⚠  Changement temporaire (non sauvegarde).{ConsoleUI.RESET}",
                        ]),
                    ])
                except Exception as e:
                    ConsoleUI.result_screen([f"  {ConsoleUI.RED}✖  {e}{ConsoleUI.RESET}"])

        elif choice == 1:
            new_db = ConsoleUI.input_screen(
                "BASE DE DONNÉES LOCALE",
                "Chemin complet du dossier",
                subtitle=f"Actuel : {LOCAL_DB_DIR}",
                allow_esc=True,
            )
            if new_db:
                try:
                    os.makedirs(new_db, exist_ok=True)
                    abs_db = os.path.abspath(new_db)
                    _set_db_dir(abs_db)
                    cfg["db_dir"] = abs_db
                    _save_config({"db_dir": abs_db})
                    ConsoleUI.result_screen([
                        f"  {ConsoleUI.GREEN}✔  Base de donnees mise a jour.{ConsoleUI.RESET}",
                        f"  {ConsoleUI.CYAN}   {abs_db}{ConsoleUI.RESET}",
                    ])
                except Exception as e:
                    ConsoleUI.result_screen([f"  {ConsoleUI.RED}✖  {e}{ConsoleUI.RESET}"])

        elif choice == 2:
            if not GITHUB_TOKEN:
                ConsoleUI.result_screen([
                    f"  {ConsoleUI.YELLOW}⚠  GitHub fallback desactive automatiquement.{ConsoleUI.RESET}",
                    f"  {ConsoleUI.DIM}Aucun GITHUB_TOKEN trouve dans le fichier .env.{ConsoleUI.RESET}",
                    f"  {ConsoleUI.DIM}Ajoutez GITHUB_TOKEN=<votre_token> dans .env pour activer.{ConsoleUI.RESET}",
                ])
            else:
                cfg["github_fallback"] = not github_on
                _save_config({"github_fallback": cfg["github_fallback"]})
                etat = "active" if cfg["github_fallback"] else "desactive"
                ConsoleUI.result_screen([
                    f"  {ConsoleUI.GREEN}✔  GitHub fallback {etat}.{ConsoleUI.RESET}",
                ])

        else:
            break


# ══════════════════════════════════════════════════════════════════════════════
#  Initialisation du dossier de telechargement
# ══════════════════════════════════════════════════════════════════════════════
def init_dest_dir(cfg):
    saved      = cfg.get("dest_dir", "")
    in_appdata = _is_in_appdata()
    config_ok  = os.path.isfile(_config_path())

    if IS_TERMUX:
        # Sur Termux : dossier par défaut fixe, jamais sauvegardé.
        if saved and os.path.isdir(saved):
            return saved
        fallback = "/storage/emulated/0/Download/Animes"
        try:
            os.makedirs(fallback, exist_ok=True)
        except Exception:
            fallback = os.path.join(os.path.expanduser("~"), "Animes")
            try:
                os.makedirs(fallback, exist_ok=True)
            except Exception:
                pass
        # NE PAS appeler _save_config sur Termux
        return fallback

    def _ask_new(allow_cancel=False):
        while True:
            new = ConsoleUI.input_screen(
                "DOSSIER DE TELECHARGEMENT",
                "Chemin complet du dossier",
                subtitle="Ce dossier sera utilise pour tous les telechargements.",
                allow_esc=allow_cancel,
            )
            if new is None:
                return None
            if not new:
                if not in_appdata:
                    chosen = os.path.abspath(_BASE_DIR)
                    _save_config({"dest_dir": chosen})
                    return chosen
                ConsoleUI.warn("Un chemin est requis.")
                time.sleep(0.6)
                continue
            try:
                os.makedirs(new, exist_ok=True)
                chosen = os.path.abspath(new)
                _save_config({"dest_dir": chosen})
                ConsoleUI.result_screen([
                    f"  {ConsoleUI.GREEN}OK Dossier configure !{ConsoleUI.RESET}",
                    f"  {ConsoleUI.CYAN}  {chosen}{ConsoleUI.RESET}",
                ])
                return chosen
            except Exception as e:
                ConsoleUI.result_screen([f"  {ConsoleUI.RED}X  {e}{ConsoleUI.RESET}"])

    if not config_ok:
        if in_appdata:
            ConsoleUI.result_screen([
                f"  {ConsoleUI.CYAN}{ConsoleUI.BOLD}Bienvenue dans Ney-Chan !{ConsoleUI.RESET}",
                "",
                f"  {ConsoleUI.DIM}Aucune configuration trouvee.{ConsoleUI.RESET}",
                f"  {ConsoleUI.DIM}Choisissez un dossier de telechargement.{ConsoleUI.RESET}",
            ], pause=False)
            return _ask_new(allow_cancel=False)
        else:
            chosen = os.path.abspath(_BASE_DIR)
            _save_config({"dest_dir": chosen})
            return chosen

    if saved and os.path.isdir(saved):
        if in_appdata:
            keep = ConsoleUI.navigate(
                [f"Garder : {saved}", "Changer"],
                "DOSSIER DE TELECHARGEMENT",
            )
            if keep in (0, -1):
                return saved
            result = _ask_new(allow_cancel=True)
            return result if result else saved
        return saved

    ConsoleUI.result_screen([
        f"  {ConsoleUI.YELLOW}!  Dossier introuvable : {saved or '(non defini)'}{ConsoleUI.RESET}",
        f"  {ConsoleUI.DIM}Veuillez en choisir un nouveau.{ConsoleUI.RESET}",
    ], pause=False)
    result = _ask_new(allow_cancel=not in_appdata)
    if result:
        return result
    chosen = os.path.abspath(_BASE_DIR)
    _save_config({"dest_dir": chosen})
    return chosen


# ══════════════════════════════════════════════════════════════════════════════
#  Main
# ══════════════════════════════════════════════════════════════════════════════
def main():
    ConsoleUI.enable_ansi()
    # ── Mode GUI (PC) ──────────────────────────────────────────────────────────
    if IS_PC and PYQT_AVAILABLE:
        main_gui()
        return
    # ── Mode console (Termux / PC sans PyQt6) ─────────────────────────────────
    if IS_WINDOWS:
        os.system("title Ney-Chan -- Anime Downloader")

    ConsoleUI.clear()
    _startup_banner = ConsoleUI.TERMUX_BANNER if IS_TERMUX else ConsoleUI.BANNER
    print(_startup_banner)
    print(f"\n  {ConsoleUI.DIM}Initialisation, veuillez patienter...{ConsoleUI.RESET}\n")

    setup_dependencies()

    steps   = [("FFmpeg", setup_ffmpeg)]
    results = {}
    bar_w   = 40
    for idx, (label, fn) in enumerate(steps):
        pct    = int((idx / len(steps)) * 100)
        filled = pct * bar_w // 100
        pbar   = "#" * filled + "." * (bar_w - filled)
        print(f"\r  {ConsoleUI.CYAN}[{pbar}]{ConsoleUI.RESET}  {label}...", end="", flush=True)
        results[label] = fn()
    bar_full = "#" * bar_w
    print(f"\r  {ConsoleUI.CYAN}[{bar_full}]{ConsoleUI.RESET}  Pret !           ", flush=True)
    print()

    ffmpeg_exe = results["FFmpeg"]
    if not ffmpeg_exe:
        ConsoleUI.warn("FFmpeg introuvable -- certains telechargements peuvent echouer.")

    cfg      = _load_config()
    dest_dir = [init_dest_dir(cfg)]
    cfg      = _load_config()
    init_db_dir(cfg)

    while True:
        github_etat = "ON" if cfg.get("github_fallback") else "OFF"
        choice = ConsoleUI.navigate(
            ["Rechercher un anime", "Parametres", "Quitter"],
            "MENU PRINCIPAL",
            f"v{VERSION}  --  {dest_dir[0]}  |  GitHub : {github_etat}",
        )
        if choice == 0:
            menu_search(dest_dir[0], ffmpeg_exe, cfg)
        elif choice == 1:
            menu_settings(dest_dir, cfg, ffmpeg_exe)
        elif choice in (2, -1):
            _goodbye()


def _goodbye():
    try:
        ConsoleUI.clear()
        _bye_banner = ConsoleUI.TERMUX_BANNER if IS_TERMUX else ConsoleUI.BANNER
        print(_bye_banner)
        print(f"\n  {ConsoleUI.CYAN}A bientot !{ConsoleUI.RESET}\n")
        time.sleep(0.8)
    except Exception:
        pass
    sys.exit(0)

def _signal_handler(_sig, _frame):
    _goodbye()


if __name__ == "__main__":
    signal.signal(signal.SIGINT,  _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)
    if hasattr(signal, "SIGHUP"):
        signal.signal(signal.SIGHUP, _signal_handler)
    try:
        main()
    except KeyboardInterrupt:
        _goodbye()
    except Exception as _e:
        ConsoleUI.clear()
        print(ConsoleUI.RED + "\n\n  ERREUR CRITIQUE\n" + ConsoleUI.RESET)
        print(f"  {_e}\n")
        traceback.print_exc()
        try:
            input("\n  Appuyez sur Entree pour quitter...")
        except (EOFError, OSError):
            pass
        _goodbye()