"""Рендер JS-сайтов через headless Chromium (`--dump-dom`) — без Playwright и новых зависимостей.

Обычный requests не видит контент, который рисуется скриптами (SPA, конструкторы сайтов).
Если главная страница почти пустая, прогоняем её через Chromium и берём готовый DOM.
Бинарник ищем сам: Chromium из кеша Playwright, системный chromium/google-chrome или путь из конфига.
"""
from __future__ import annotations

import glob
import os
import shutil
import subprocess
import tempfile
import threading

_CANDIDATES = [
    "~/.cache/ms-playwright/chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell",
    "~/.cache/ms-playwright/chromium-*/chrome-linux64/chrome",
    "~/.cache/ms-playwright/chromium-*/chrome-linux/chrome",
]
_SYSTEM = ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable")

# признаки страницы, которой нужен браузер
_JS_MARKERS = ("enable javascript", "включите javascript", "javascript is disabled", "you need to enable",
               "id=\"root\"></div>", "id=\"app\"></div>", "id=\"__nuxt\"></div>")


def find_chrome(explicit: str = "") -> str:
    if explicit and os.access(os.path.expanduser(explicit), os.X_OK):
        return os.path.expanduser(explicit)
    for pattern in _CANDIDATES:
        found = sorted(glob.glob(os.path.expanduser(pattern)), reverse=True)
        for f in found:
            if os.access(f, os.X_OK):
                return f
    for name in _SYSTEM:
        p = shutil.which(name)
        if p:
            return p
    return ""


def needs_render(html: str, visible_text_len: int) -> bool:
    if visible_text_len < 400:
        return True
    low = html[:30000].lower()
    return any(m in low for m in _JS_MARKERS)


class Renderer:
    def __init__(self, path: str, user_agent: str = "", max_parallel: int = 3, timeout: int = 30):
        self.path = path
        self.user_agent = user_agent
        self.timeout = timeout
        self.sem = threading.Semaphore(max_parallel)
        self.count = 0

    def render(self, url: str, proxy: str = "") -> str:
        if not url.startswith(("http://", "https://")):
            return ""
        with self.sem, tempfile.TemporaryDirectory(prefix="leadgen-chrome-") as profile:
            cmd = [self.path, "--headless", "--disable-gpu", "--no-first-run", "--mute-audio",
                   f"--user-data-dir={profile}", "--blink-settings=imagesEnabled=false",
                   "--virtual-time-budget=8000", "--dump-dom"]
            if self.user_agent:
                cmd.append(f"--user-agent={self.user_agent}")
            if proxy:
                cmd.append(f"--proxy-server={proxy}")
            cmd.append(url)
            try:
                r = subprocess.run(cmd, capture_output=True, timeout=self.timeout)
            except (subprocess.TimeoutExpired, OSError):
                return ""
            self.count += 1
            return r.stdout.decode("utf-8", "ignore")
