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
    RESET  = "\033[0m"
    BOLD   = "\033[1m"
    DIM    = "\033[2m"
    RED    = "\033[31m"
    GREEN  = "\033[32m"
    YELLOW = "\033[33m"
    CYAN   = "\033[36m"

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
        print(f"{ConsoleUI.CYAN}\n  {'='*54}{ConsoleUI.RESET}")
        print(f"  {ConsoleUI.BOLD}{ConsoleUI.CYAN}  Ney-Chan  --  {title}{ConsoleUI.RESET}")
        if subtitle:
            print(f"  {ConsoleUI.DIM}{subtitle}{ConsoleUI.RESET}")
        print(f"{ConsoleUI.CYAN}  {'='*54}{ConsoleUI.RESET}\n")
        for i, opt in enumerate(options, 1):
            print(f"  {ConsoleUI.CYAN}{ConsoleUI.BOLD}[{i}]{ConsoleUI.RESET}  {opt}")
        print(f"  {ConsoleUI.CYAN}{ConsoleUI.BOLD}[0]{ConsoleUI.RESET}  {ConsoleUI.DIM}Retour{ConsoleUI.RESET}")
        print(f"\n{ConsoleUI.CYAN}  {'-'*54}{ConsoleUI.RESET}")

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
        print(ConsoleUI.BANNER)
        print(f"\n  {ConsoleUI.CYAN}{ConsoleUI.BOLD}{'-'*58}{ConsoleUI.RESET}")
        print(f"  {ConsoleUI.BOLD}{title}{ConsoleUI.RESET}")
        if subtitle:
            print(f"  {ConsoleUI.DIM}{subtitle}{ConsoleUI.RESET}")
        if allow_esc:
            print(f"  {ConsoleUI.DIM}(Laissez vide + Entree pour annuler){ConsoleUI.RESET}")
        print(f"  {ConsoleUI.CYAN}{'-'*58}{ConsoleUI.RESET}\n")
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
        print(ConsoleUI.BANNER)
        print(ConsoleUI.CYAN + "\n  " + "=" * 58 + ConsoleUI.RESET)
        for line in lines:
            print(line)
        print(ConsoleUI.CYAN + "\n  " + "=" * 58 + ConsoleUI.RESET)
        if pause:
            try:
                input(f"\n  {ConsoleUI.DIM}Appuyez sur Entree pour continuer...{ConsoleUI.RESET}")
            except (EOFError, OSError):
                pass

    @staticmethod
    def info(m):    print(f"  {ConsoleUI.CYAN}i  {ConsoleUI.RESET}{m}")
    @staticmethod
    def success(m): print(f"  {ConsoleUI.GREEN}OK {ConsoleUI.RESET}{m}")
    @staticmethod
    def warn(m):    print(f"  {ConsoleUI.YELLOW}!  {ConsoleUI.RESET}{m}")
    @staticmethod
    def err(m):     print(f"  {ConsoleUI.RED}X  {ConsoleUI.RESET}{m}")
    @staticmethod
    def sep():      print(f"\n  {ConsoleUI.DIM}{'-'*54}{ConsoleUI.RESET}\n")


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

    choices = [
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

    if choice == 0:
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

    elif choice == 1:
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

    elif choice == 2:
        sk = _pick_saison(saisons, lang_data, anime_name, lang)
        if sk is None: return
        n  = count_episodes(lang_data[sk])
        ep = _ask_ep_number("Numero d'episode", n)
        if ep is None: return
        run_download(slug, anime_data, lang, sk,
                     (ep - 1, ep - 1), dest_dir, anime_name, ffmpeg_exe)

    elif choice == 3:
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

    elif choice == 4:
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
        subtitle="La recherche utilise la base de donnees locale (dossier anime-sama/).",
        allow_esc=True,
    )
    if not query_raw:
        return

    query   = normalize_query(query_raw)
    results = search_local(query)

    if not results:
        choice = ConsoleUI.navigate(
            [f"Chercher '{slug_to_display(query)}' sur anime-sama",
             "Modifier la recherche",
             "<  Retour"],
            "AUCUN RESULTAT LOCAL",
            f"Aucun anime trouve pour '{query_raw}' dans l'index local.",
        )
        if choice == 0:
            results = [query]
        elif choice == 1:
            menu_search(dest_dir, ffmpeg_exe, cfg)
            return
        else:
            return

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
    print(ConsoleUI.BANNER)
    print()
    ConsoleUI.info(f"Chargement des donnees pour {ConsoleUI.BOLD}{anime_name}{ConsoleUI.RESET}...")

    anime_data = load_anime_local(slug)

    if anime_data is None and cfg.get("github_fallback") and GITHUB_TOKEN:
        ConsoleUI.info("Non trouve en local -> essai GitHub (repo prive)...")
        anime_data = load_anime_github(slug)

    if anime_data is None:
        ConsoleUI.info("Non trouve -> scraping anime-sama en direct...")
        anime_data = scrape_anime_data(slug)

    if anime_data is None:
        ConsoleUI.result_screen([
            f"  {ConsoleUI.RED}X  Anime introuvable : {anime_name}{ConsoleUI.RESET}",
            f"  {ConsoleUI.DIM}Verifiez le nom ou sa disponibilite sur anime-sama.{ConsoleUI.RESET}",
        ])
        return

    if not anime_data:
        ConsoleUI.result_screen([
            f"  {ConsoleUI.YELLOW}!  {anime_name} existe mais "
            f"aucune video n'est disponible (manga uniquement ?).{ConsoleUI.RESET}",
        ])
        return

    langs_dispo = [l for l in ALL_LANGUAGES if l in anime_data]
    if not langs_dispo:
        ConsoleUI.result_screen([
            f"  {ConsoleUI.RED}X  Aucune langue disponible pour {anime_name}.{ConsoleUI.RESET}",
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
    while True:
        github_on   = cfg.get("github_fallback", False)
        github_etat = (f"{ConsoleUI.GREEN}actif{ConsoleUI.RESET}"
                       if github_on else f"{ConsoleUI.RED}desactive{ConsoleUI.RESET}")
        token_ok    = (f"{ConsoleUI.GREEN}present{ConsoleUI.RESET}"
                       if GITHUB_TOKEN else f"{ConsoleUI.YELLOW}manquant (.env){ConsoleUI.RESET}")
        ffmpeg_info = os.path.basename(ffmpeg_exe) if ffmpeg_exe else "introuvable"

        opts = [
            f"Dossier de telechargement   ({dest_dir_ref[0]})",
            f"GitHub fallback : {github_etat}  [token : {token_ok}]",
            f"FFmpeg : {ffmpeg_info}",
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
                    _save_config({"dest_dir": dest_dir_ref[0]})
                    ConsoleUI.result_screen([
                        f"  {ConsoleUI.GREEN}OK Dossier mis a jour.{ConsoleUI.RESET}",
                        f"  {ConsoleUI.CYAN}  {dest_dir_ref[0]}{ConsoleUI.RESET}",
                    ])
                except Exception as e:
                    ConsoleUI.result_screen([f"  {ConsoleUI.RED}X  {e}{ConsoleUI.RESET}"])

        elif choice == 1:
            cfg["github_fallback"] = not github_on
            _save_config({"github_fallback": cfg["github_fallback"]})
            etat = "active" if cfg["github_fallback"] else "desactive"
            ConsoleUI.result_screen([
                f"  {ConsoleUI.GREEN}OK GitHub fallback {etat}.{ConsoleUI.RESET}",
                *([] if GITHUB_TOKEN else [
                    f"  {ConsoleUI.YELLOW}!  GITHUB_TOKEN manquant dans le fichier .env.{ConsoleUI.RESET}",
                ]),
            ])

        elif choice == 2:
            ConsoleUI.result_screen([
                f"  {ConsoleUI.CYAN}FFmpeg{ConsoleUI.RESET}",
                f"  {ConsoleUI.DIM}{ffmpeg_exe or 'Non trouve'}{ConsoleUI.RESET}",
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
        if saved and os.path.isdir(saved):
            return saved
        fallback = "/storage/emulated/0/Download/Animes"
        try:
            os.makedirs(fallback, exist_ok=True)
        except Exception:
            fallback = os.path.join(os.path.expanduser("~"), "Animes")
            os.makedirs(fallback, exist_ok=True)
        _save_config({"dest_dir": fallback})
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
    if IS_WINDOWS:
        os.system("title Ney-Chan -- Anime Downloader")

    ConsoleUI.clear()
    print(ConsoleUI.BANNER)
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
        print(ConsoleUI.BANNER)
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
