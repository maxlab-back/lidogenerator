#!/usr/bin/env python3
"""Веб-интерфейс универсального лидогенератора.

    python app.py                        # откроет http://127.0.0.1:8765 в браузере
    python app.py --port 9000            # другой порт
    python app.py --no-browser           # не открывать браузер
    python app.py --host 0.0.0.0         # доступ по сети для команды (нужны пользователи)

Пользователи (для командного режима):
    python app.py --add-user ivan        # завести / сменить пароль
    python app.py --del-user ivan
    python app.py --users
"""
from __future__ import annotations

import argparse
import getpass
import sys

from parser.config import load_config
from parser.webapp import open_db, serve


def main() -> None:
    ap = argparse.ArgumentParser(description="Универсальный лидогенератор (веб-интерфейс)")
    ap.add_argument("--config", default=None, help="путь к config.yaml")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true", help="не открывать браузер")
    ap.add_argument("--add-user", metavar="ИМЯ", help="завести пользователя или сменить пароль")
    ap.add_argument("--del-user", metavar="ИМЯ", help="удалить пользователя")
    ap.add_argument("--users", action="store_true", help="список пользователей")
    ap.add_argument("--check", action="store_true",
                    help="проверить окружение, ключи и остаток кредитов и выйти")
    args = ap.parse_args()
    cfg = load_config(args.config)

    if args.check:
        from parser.doctor import report
        sys.exit(report(cfg, port=args.port))

    if args.add_user or args.del_user or args.users:
        db = open_db(cfg)
        if args.users:
            print("\n".join(db.users()) or "Пользователей нет — вход не требуется (только localhost).")
        elif args.del_user:
            db.delete_user(args.del_user)
            print(f"Удалён: {args.del_user}")
        else:
            from parser.auth import hash_password
            pw = getpass.getpass("Пароль (от 8 символов): ")
            if len(pw) < 8 or pw != getpass.getpass("Повтори пароль: "):
                sys.exit("Пароль короче 8 символов или не совпал.")
            db.set_user(args.add_user.strip(), hash_password(pw))
            print(f"Готово: {args.add_user}. Теперь вход по паролю включён.")
        return

    serve(cfg, args.host, args.port, open_browser=not args.no_browser)


if __name__ == "__main__":
    main()
