#!/bin/sh
# Запуск лидогенератора на любой машине с Linux.
#
# Скрипт сам проверяет Python, окружение, зависимости и .env, доустанавливает недостающее
# и запускает веб-интерфейс. Написан на POSIX sh: работает и там, где нет bash (Alpine,
# минимальные контейнеры) — нужен только /bin/sh.
#
#   ./start.sh                  проверить всё и запустить (http://127.0.0.1:8765)
#   ./start.sh --check          только диагностика, ничего не ставить и не запускать
#   ./start.sh --setup          только установка окружения, без запуска
#   ./start.sh --yes            не задавать вопросов (для серверов и скриптов)
#   ./start.sh --recreate       пересобрать окружение с нуля
#   ./start.sh --install        ярлык в меню приложений + автозапуск при входе в систему
#   ./start.sh --uninstall      убрать ярлык и автозапуск
#   ./start.sh --cli [...]      запустить нишевый режим (run.py) вместо веб-интерфейса
#   ./start.sh --host 0.0.0.0 --no-browser      — любые флаги app.py идут дальше как есть
#
# Если ./start.sh не запускается (нет прав на файл) — выполни:  sh start.sh
set -eu

MIN_MINOR=10          # минимальная версия Python: 3.10
VENV=".venv"
DO_CHECK=0
DO_SETUP_ONLY=0
DO_INSTALL=0
DO_UNINSTALL=0
PORT=8765
ASSUME_YES=0
RECREATE=0
RUN_CLI=0

# каталог скрипта: работает и при запуске по относительному пути, и через симлинк
SELF="$0"
if [ -L "$SELF" ] && command -v readlink >/dev/null 2>&1; then
  SELF=$(readlink -f "$SELF" 2>/dev/null || echo "$0")
fi
cd "$(CDPATH= cd -- "$(dirname -- "$SELF")" && pwd)"

# ---------------------------------------------------------------- разбор аргументов
# свои флаги забираем себе, остальные оставляем в "$@" и передаём питону
ARGC=$#
IDX=0
while [ "$IDX" -lt "$ARGC" ]; do
  ARG="$1"
  shift
  case "$ARG" in
    --check)    DO_CHECK=1 ;;
    --setup)    DO_SETUP_ONLY=1 ;;
    --yes|-y)   ASSUME_YES=1 ;;
    --recreate) RECREATE=1 ;;
    --cli)       RUN_CLI=1 ;;
    --install)   DO_INSTALL=1 ;;
    --uninstall) DO_UNINSTALL=1 ;;
    --port)      # запоминаем, но передаём дальше: нужен, чтобы узнать уже запущенный сервер
                 PORT="${1:-8765}"; set -- "$@" "$ARG" ;;
    -h|--help)
      awk 'NR>1 && /^#/ {sub(/^# ?/, ""); print; next} NR>1 {exit}' "$0"
      exit 0 ;;
    --port=*)    PORT="${ARG#--port=}"; set -- "$@" "$ARG" ;;
    *) set -- "$@" "$ARG" ;;
  esac
  IDX=$((IDX + 1))
done

# ---------------------------------------------------------------- оформление
if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
  ESC=$(printf '\033')
  C_R="${ESC}[31m"; C_G="${ESC}[32m"; C_Y="${ESC}[33m"; C_B="${ESC}[1m"; C_0="${ESC}[0m"
else
  C_R=""; C_G=""; C_Y=""; C_B=""; C_0=""
fi
ok()   { printf '  %s✓%s %s\n' "$C_G" "$C_0" "$1"; }
warn() { printf '  %s!%s %s\n' "$C_Y" "$C_0" "$1"; }
bad()  { printf '  %s✗%s %s\n' "$C_R" "$C_0" "$1"; }
step() { printf '\n%s%s%s\n' "$C_B" "$1" "$C_0"; }
die()  { printf '\n%sНе удалось запустить:%s %s\n' "$C_R" "$C_0" "$1" >&2; exit 1; }

ask() {   # ask "вопрос" -> 0 если согласие
  [ "$ASSUME_YES" = 1 ] && return 0
  [ -t 0 ] || return 1                      # не интерактивно и без --yes — не зависаем
  printf '  %s [Y/n] ' "$1"
  read -r ANSWER || return 1
  case "$ANSWER" in [Nn]*|[Нн]*) return 1 ;; *) return 0 ;; esac
}

# ---------------------------------------------------------------- ярлык и автозапуск
APP_DIR=$(pwd)
DESKTOP_FILE="$HOME/.local/share/applications/lidogenerator.desktop"
USER_UNIT="$HOME/.config/systemd/user/lidogenerator.service"

uninstall_shortcut() {
  if command -v systemctl >/dev/null 2>&1; then
    systemctl --user disable --now lidogenerator.service >/dev/null 2>&1 || true
  fi
  rm -f "$USER_UNIT" "$DESKTOP_FILE"
  if command -v systemctl >/dev/null 2>&1; then
    systemctl --user daemon-reload >/dev/null 2>&1 || true
  fi
  ok "ярлык и автозапуск убраны. Сам лидогенератор и база лидов остались на месте"
}

if [ "$DO_UNINSTALL" = 1 ]; then
  step "Убираю ярлык и автозапуск"
  uninstall_shortcut
  exit 0
fi

# ---------------------------------------------------------------- пакеты системы
SUDO=""
if [ "$(id -u)" -ne 0 ] && command -v sudo >/dev/null 2>&1; then
  SUDO="sudo"
fi

PM=""; PM_INSTALL=""; PKG_PY=""; PKG_VENV=""; PKG_CHROME=""; PKG_BUILD=""
if command -v apt-get >/dev/null 2>&1; then
  PM="apt"; PM_INSTALL="$SUDO apt-get install -y"
  PKG_PY="python3"; PKG_VENV="python3-venv python3-pip"; PKG_CHROME="chromium"
  PKG_BUILD="build-essential python3-dev libxml2-dev libxslt1-dev zlib1g-dev"
elif command -v dnf >/dev/null 2>&1; then
  PM="dnf"; PM_INSTALL="$SUDO dnf install -y"
  PKG_PY="python3"; PKG_VENV="python3-pip"; PKG_CHROME="chromium"
  PKG_BUILD="gcc python3-devel libxml2-devel libxslt-devel zlib-devel"
elif command -v zypper >/dev/null 2>&1; then
  PM="zypper"; PM_INSTALL="$SUDO zypper --non-interactive install"
  PKG_PY="python3"; PKG_VENV="python3-pip python3-virtualenv"; PKG_CHROME="chromium"
  PKG_BUILD="gcc python3-devel libxml2-devel libxslt-devel zlib-devel"
elif command -v pacman >/dev/null 2>&1; then
  PM="pacman"; PM_INSTALL="$SUDO pacman -S --needed --noconfirm"
  PKG_PY="python"; PKG_VENV="python-pip"; PKG_CHROME="chromium"
  PKG_BUILD="base-devel libxml2 libxslt zlib"
elif command -v apk >/dev/null 2>&1; then
  PM="apk"; PM_INSTALL="$SUDO apk add --no-cache"
  PKG_PY="python3"; PKG_VENV="py3-pip"; PKG_CHROME="chromium"
  PKG_BUILD="gcc musl-dev python3-dev libxml2-dev libxslt-dev zlib-dev"
elif command -v yum >/dev/null 2>&1; then
  PM="yum"; PM_INSTALL="$SUDO yum install -y"
  PKG_PY="python3"; PKG_VENV="python3-pip"; PKG_CHROME="chromium"
  PKG_BUILD="gcc python3-devel libxml2-devel libxslt-devel zlib-devel"
fi

distro() {
  if [ -r /etc/os-release ]; then
    . /etc/os-release 2>/dev/null || true
    printf '%s' "${PRETTY_NAME:-${NAME:-Linux}}"
  else
    printf 'Linux'
  fi
}

install_pkgs() {   # install_pkgs "пакеты"
  if [ -z "$PM_INSTALL" ]; then
    bad "не понял пакетный менеджер ($(distro)) — поставь вручную: $1"
    return 1
  fi
  if [ -z "$SUDO" ] && [ "$(id -u)" -ne 0 ]; then
    bad "нет прав и нет sudo. Поставь вручную:  $PM_INSTALL $1"
    return 1
  fi
  warn "нужно доустановить: $1"
  if ! ask "Выполнить «$PM_INSTALL $1»?"; then
    bad "пропущено. Команда для ручной установки:  $PM_INSTALL $1"
    return 1
  fi
  $PM_INSTALL $1 || { bad "установка не прошла — выполни вручную: $PM_INSTALL $1"; return 1; }
  return 0
}

# ---------------------------------------------------------------- 1. Python
py_ok() {
  "$1" -c "import sys; sys.exit(0 if sys.version_info >= (3, $MIN_MINOR) else 1)" >/dev/null 2>&1
}

find_python() {
  for CAND in python3.14 python3.13 python3.12 python3.11 python3.10 python3 python; do
    command -v "$CAND" >/dev/null 2>&1 || continue
    if py_ok "$CAND"; then command -v "$CAND"; return 0; fi
  done
  return 1
}

py_pkg() {   # имена пакетов для версии $1 (3.12) в текущем дистрибутиве
  case "$PM" in
    apt)      printf 'python%s python%s-venv' "$1" "$1" ;;
    dnf|yum)  printf 'python%s python%s-pip' "$1" "$1" ;;
    zypper)   PYV=$(echo "$1" | tr -d .); printf 'python%s python%s-pip' "$PYV" "$PYV" ;;
    *)        printf '' ;;
  esac
}

install_python() {
  # В Debian 11, Ubuntu 20.04 и RHEL 9 пакет python3 — это 3.9 и ниже, поэтому
  # сначала просим версионные пакеты и только потом обычный python3.
  [ -n "$PM_INSTALL" ] || return 1
  if [ -z "$SUDO" ] && [ "$(id -u)" -ne 0 ]; then
    bad "нет прав и нет sudo — поставить Python из репозитория не смогу"
    return 1
  fi
  ask "Поставить Python 3.$MIN_MINOR+ из репозитория системы?" || return 1
  if [ "$PM" = "apt" ]; then
    printf '  обновляю список пакетов…\n'
    $SUDO apt-get update >/dev/null 2>&1 || true
  fi
  for PYVER in 3.13 3.12 3.11 3.10; do
    NAMES=$(py_pkg "$PYVER")
    [ -n "$NAMES" ] || continue
    printf '  пробую python%s…\n' "$PYVER"
    if $PM_INSTALL $NAMES >/dev/null 2>&1 && PY=$(find_python); then return 0; fi
  done
  printf '  пробую %s…\n' "$PKG_PY"
  if $PM_INSTALL $PKG_PY $PKG_VENV >/dev/null 2>&1 && PY=$(find_python); then return 0; fi
  bad "в репозитории этой системы нет Python 3.$MIN_MINOR+"
  return 1
}

step "1. Python"
if PY=$(find_python); then
  ok "$("$PY" -V 2>&1) — $PY"
else
  HAVE=$(python3 -V 2>&1 || true)
  [ -n "$HAVE" ] || HAVE="питона нет вовсе"
  bad "нужен Python 3.$MIN_MINOR или новее, а в системе: $HAVE"
  [ "$DO_CHECK" = 1 ] && exit 1
  if install_python; then
    ok "поставлен: $("$PY" -V 2>&1)"
  else
    die "поставь Python 3.$MIN_MINOR+ сам и запусти снова. Система: $(distro)"
  fi
fi

# ---------------------------------------------------------------- 2. окружение
# Обычный путь — виртуальное окружение. Если собрать его нечем и доставить venv нельзя,
# работаем прямо системным Python (pip --user): лишь бы лидогенератор запустился.
step "2. Окружение"
SYSTEM_MODE=0
PIP_EXTRA=""
STAMP="$VENV/.requirements.sha256"

if [ "$RECREATE" = 1 ] && [ -d "$VENV" ]; then
  warn "пересобираю окружение с нуля"
  rm -rf "$VENV"
fi

if ! "$PY" -c "import venv, ensurepip" >/dev/null 2>&1 && [ ! -x "$VENV/bin/python" ]; then
  if [ "$DO_CHECK" = 1 ]; then
    bad "у Python нет модулей venv/ensurepip"
  else
    warn "у Python нет модулей venv/ensurepip"
    install_pkgs "$PKG_VENV" || true
  fi
fi

if [ -x "$VENV/bin/python" ]; then
  ok "виртуальное окружение на месте"
elif [ "$DO_CHECK" = 1 ]; then
  bad "окружение не создано — запусти ./start.sh --setup"
  SYSTEM_MODE=1
elif "$PY" -m venv "$VENV" >/dev/null 2>&1; then
  ok "виртуальное окружение создано"
else
  rm -rf "$VENV"
  SYSTEM_MODE=1
  warn "виртуальное окружение создать нечем — работаю системным Python, пакеты в домашний каталог"
fi

if [ -x "$VENV/bin/python" ]; then
  VPY="$VENV/bin/python"
else
  SYSTEM_MODE=1
  VPY="$PY"
  PIP_EXTRA="--user"
  STAMP=".deps.sha256"
fi
"$VPY" -c "import sys" >/dev/null 2>&1 || die "Python сломан: $VPY (попробуй ./start.sh --recreate)"

# ---------------------------------------------------------------- 3. зависимости
step "3. Зависимости"
REQ_HASH=$("$VPY" -c "import hashlib; print(hashlib.sha256(open('requirements.txt','rb').read()).hexdigest())" 2>/dev/null || true)
[ -n "$REQ_HASH" ] || die "Python не смог прочитать requirements.txt — окружение сломано ($VPY)"

deps_ok() {
  "$VPY" -c "import requests, yaml, bs4, openpyxl, dotenv, anthropic, pydantic, dns.resolver" >/dev/null 2>&1
}

pip_install() {
  "$VPY" -m pip install -r requirements.txt --disable-pip-version-check $PIP_EXTRA "$@"
}

if deps_ok && [ -f "$STAMP" ] && [ "$(cat "$STAMP")" = "$REQ_HASH" ]; then
  ok "всё на месте"
elif [ "$DO_CHECK" = 1 ]; then
  bad "зависимости не установлены или requirements.txt менялся — запусти ./start.sh --setup"
else
  printf '  ставлю пакеты из requirements.txt (первый раз это минута-две)…\n'
  "$VPY" -m pip install --upgrade pip --quiet --disable-pip-version-check $PIP_EXTRA >/dev/null 2>&1 || true
  if pip_install; then
    printf '%s' "$REQ_HASH" > "$STAMP"
    ok "установлены"
  elif [ "$SYSTEM_MODE" = 1 ] && pip_install --break-system-packages; then
    # свежие Debian/Fedora без этого флага не дают ставить пакеты в системный Python (PEP 668)
    PIP_EXTRA="$PIP_EXTRA --break-system-packages"
    printf '%s' "$REQ_HASH" > "$STAMP"
    ok "установлены (в обход защиты системных пакетов)"
  else
    warn "часть пакетов не собралась"
    if [ -n "$PKG_BUILD" ] && ask "Доставить инструменты сборки и попробовать снова?"; then
      $PM_INSTALL $PKG_BUILD || true
      pip_install && printf '%s' "$REQ_HASH" > "$STAMP" && ok "установлены со второй попытки"
    fi
  fi
  if ! deps_ok; then
    "$VPY" -c "import requests, yaml, bs4, openpyxl, dotenv, anthropic, pydantic, dns.resolver" || true
    die "не хватает обязательных пакетов (ошибка выше). Если нет интернета — собери окружение на машине с сетью и скопируй папку целиком."
  fi
fi
if "$VPY" -c "import lxml" >/dev/null 2>&1; then
  ok "lxml есть (быстрый разбор HTML)"
else
  warn "lxml нет — HTML разбирает html.parser из стандартной библиотеки (медленнее, но работает)"
fi

# ---------------------------------------------------------------- 4. ключи
step "4. Ключи (.env)"
if [ -f .env ]; then
  ok ".env на месте"
elif [ -f .env.example ]; then
  cp .env.example .env
  warn "файла .env не было — создал из .env.example. Впиши ключи, иначе поиск пойдёт"
  warn "через бесплатный DuckDuckGo и без Google Карт."
else
  warn ".env нет — поиск пойдёт через бесплатный DuckDuckGo, без карт и без ИИ"
fi

# ---------------------------------------------------------------- 5. Chromium
step "5. Chromium (для сайтов на JavaScript)"
CHROME=$("$VPY" -c "
import sys; sys.path.insert(0, '.')
from parser.render import find_chrome
print(find_chrome() or '')" 2>/dev/null || true)
if [ -n "$CHROME" ]; then
  ok "найден: $CHROME"
else
  warn "не найден — сайты на JavaScript отдадут меньше контактов (остальное работает)"
  if [ "$DO_CHECK" = 0 ] && [ -n "$PM_INSTALL" ] && ask "Поставить $PKG_CHROME?"; then
    $PM_INSTALL $PKG_CHROME || warn "не поставился — пропускаю, это не критично"
  fi
fi

running_url() {   # печатает адрес, если лидогенератор уже работает на этом порту
  "$VPY" - "$PORT" <<'PYEOF' 2>/dev/null
import sys, urllib.request
port = sys.argv[1]
try:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/me", timeout=2) as r:
        if b'"auth"' in r.read(200):
            print(f"http://127.0.0.1:{port}/")
except Exception:
    pass
PYEOF
}

open_url() {
  if command -v xdg-open >/dev/null 2>&1; then
    (xdg-open "$1" >/dev/null 2>&1 &)
  else
    "$VPY" -c "import sys, webbrowser; webbrowser.open(sys.argv[1])" "$1" >/dev/null 2>&1 || true
  fi
}

install_shortcut() {
  mkdir -p "$(dirname "$DESKTOP_FILE")"
  cat > "$DESKTOP_FILE" <<EOF
[Desktop Entry]
Type=Application
Name=Лидогенератор
GenericName=Поиск компаний-лидов
Comment=Поиск компаний для отдела продаж по России и Беларуси
Exec=$APP_DIR/start.sh --yes
Path=$APP_DIR
Icon=$APP_DIR/web/icon.svg
Terminal=true
Categories=Office;
StartupNotify=true
EOF
  chmod +x "$DESKTOP_FILE"
  if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$(dirname "$DESKTOP_FILE")" >/dev/null 2>&1 || true
  fi
  ok "ярлык в меню приложений: «Лидогенератор»"

  if ! command -v systemctl >/dev/null 2>&1; then
    warn "systemd не найден — автозапуск после перезагрузки не настроить"
    return 0
  fi
  if ! ask "Запускать лидогенератор автоматически при входе в систему?"; then
    printf '  Автозапуск пропущен. Включить позже: ./start.sh --install\n'
    return 0
  fi
  mkdir -p "$(dirname "$USER_UNIT")"
  cat > "$USER_UNIT" <<EOF
[Unit]
Description=Лидогенератор
After=network-online.target

[Service]
Type=simple
WorkingDirectory=$APP_DIR
ExecStart=$APP_DIR/start.sh --yes --no-browser --port $PORT
Restart=on-failure
RestartSec=10

[Install]
WantedBy=default.target
EOF
  systemctl --user daemon-reload >/dev/null 2>&1 || true
  if systemctl --user enable --now lidogenerator.service >/dev/null 2>&1; then
    ok "автозапуск включён — лидогенератор поднимется сам при входе в систему"
    printf '  Состояние:  systemctl --user status lidogenerator\n'
    printf '  Остановить: systemctl --user stop lidogenerator\n'
  else
    warn "не удалось включить службу (нет сессии systemd?) — ярлык всё равно работает"
  fi
}

# ---------------------------------------------------------------- запуск
if [ "$DO_CHECK" = 1 ]; then
  step "6. Состояние лидогенератора"
  exec "$VPY" app.py --check
fi
if [ "$DO_INSTALL" = 1 ]; then
  step "6. Ярлык и автозапуск"
  install_shortcut
  step "Готово"
  printf '  Запускать: значок «Лидогенератор» в меню приложений или ./start.sh\n'
  printf '  Убрать:    ./start.sh --uninstall\n'
  exit 0
fi
if [ "$DO_SETUP_ONLY" = 1 ]; then
  step "Готово"
  printf '  Окружение собрано. Запуск:  ./start.sh\n'
  exit 0
fi

# уже запущен (например, службой автозапуска) — просто открываем окно, а не падаем «порт занят»
ALREADY=$(running_url)
if [ -n "$ALREADY" ] && [ "$RUN_CLI" = 0 ]; then
  step "Лидогенератор уже работает"
  ok "$ALREADY"
  case " $* " in *" --no-browser "*) : ;; *) open_url "$ALREADY" ;; esac
  exit 0
fi

step "Запускаю"
if [ "$RUN_CLI" = 1 ]; then
  exec "$VPY" run.py "$@"
fi
exec "$VPY" app.py "$@"
