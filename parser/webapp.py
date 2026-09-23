"""Веб-сервер лидогенератора: страница (web/) + JSON-API. Локально или для команды.

Ключи API остаются на сервере (в .env) и в браузер не уходят. Если в базе заведены
пользователи (python app.py --add-user ИМЯ) или сервер слушает не localhost — нужен вход.

API (все ответы JSON):
  POST /api/login {user,password} · POST /api/logout · GET /api/me
  GET  /api/meta · GET /api/balance
  POST /api/estimate {spec} · POST /api/ai/suggest {description,keywords,sphere,countries}
  POST /api/jobs {spec} · GET /api/jobs/current · GET /api/jobs/<id>?log_from=N
  GET  /api/jobs/<id>/leads · POST /api/jobs/<id>/cancel
  GET  /api/runs · GET /api/runs/<id>
  POST /api/companies/query {фильтры} · GET /api/companies/<id>
  POST /api/companies/update {ids,status?,owner?,comment?}
  POST /api/crm/push {ids,crm}
  GET  /api/schedules · POST /api/schedules {title,spec,every_hours}
  POST /api/schedules/<id> {enabled?,every_hours?} · POST /api/schedules/<id>/delete · …/run
  POST /api/telegram/test · POST /api/xlsx {rows,threshold,name}
  GET  /api/keys · POST /api/keys {values} · POST /api/keys/test {id}
"""
from __future__ import annotations

import io
import json
import re
import sys
import threading
import time
import webbrowser
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

from . import geo
from .auth import check_password, new_token
from .config import ROOT, refresh_keys
from .crm import build_crms
from .db import DB, STATUSES
from .export import rows_to_xlsx
from .keys import ENV_NAMES, check as check_key, state as keys_state, write_env
from .notify import send_telegram
from .presets import COMMON_MINUS, SPHERES
from .render import Renderer, find_chrome
from .universal import SOURCE_TITLES, Job, Spec, estimate, run_job

WEB_DIR = ROOT / "web"
STATIC = {"/": ("index.html", "text/html; charset=utf-8"),
          "/index.html": ("index.html", "text/html; charset=utf-8"),
          "/app.js": ("app.js", "application/javascript; charset=utf-8"),
          "/app.css": ("app.css", "text/css; charset=utf-8")}
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
COOKIE = "lg_session"
_NEEDS = {"serper": ["serper"], "google_cse": ["google_cse_key", "google_cse_cx"],
          "yandex_xml": ["yandex_xml_user", "yandex_xml_key"]}


def open_db(cfg: dict) -> DB:
    p = Path((cfg.get("universal") or {}).get("db") or "output/leads.db")
    return DB(p if p.is_absolute() else ROOT / p)


class App:
    def __init__(self, cfg: dict, host: str = "127.0.0.1"):
        self.cfg = cfg
        self.keys = cfg.get("_keys", {})
        self.ucfg = cfg.get("universal") or {}
        self.db = open_db(cfg)
        out_dir = Path(cfg.get("output", {}).get("dir", "output"))
        imported = self.db.import_run_files(out_dir / "runs")
        if imported:
            print(f"В базу перенесено прошлых поисков: {imported}")
        self.jobs: dict[str, Job] = {}
        self.lock = threading.Lock()
        self.crms = build_crms(self.keys)
        rcfg = self.ucfg.get("render") or {}
        chrome = find_chrome(rcfg.get("chrome_path", "")) if rcfg.get("enabled", True) else ""
        ua = cfg.get("network", {}).get("user_agent", "")
        self.renderer = Renderer(chrome, ua, int(rcfg.get("max_parallel", 3))) if chrome else None
        self.auth = bool(self.db.users()) or host not in LOCAL_HOSTS
        self._fails: dict[str, list[float]] = {}
        self._balance: tuple[float, int | None] = (0.0, None)
        self.env_path = ROOT / ".env"

    # --- возможности (по наличию ключей) ---
    def backend_name(self) -> str:
        name = (self.cfg.get("sources", {}).get("search", {}).get("backend") or "duckduckgo").lower()
        if name in _NEEDS and not all(self.keys.get(k) for k in _NEEDS[name]):
            return "duckduckgo"
        return name

    def caps(self) -> dict:
        k = self.keys
        return {
            "backend": self.backend_name(),
            "serper": bool(k.get("serper")),
            "google_cse": bool(k.get("google_cse_key") and k.get("google_cse_cx")),
            "yandex": bool(k.get("yandex_search_key") and k.get("yandex_folder")),
            "apify": bool(k.get("apify")),
            "ai": bool(k.get("anthropic")),
            "dadata": bool(k.get("dadata")),
            "render": bool(self.renderer),
            "crm": list(self.crms),
            "telegram": bool(k.get("telegram_token") and k.get("telegram_chat")),
        }

    def meta(self, user: str) -> dict:
        return {
            "countries": geo.meta(), "spheres": SPHERES, "common_minus": COMMON_MINUS,
            "caps": self.caps(), "statuses": STATUSES, "sources": SOURCE_TITLES,
            "user": user, "auth": self.auth, "users": self.db.users(),
            "ai_model": (self.ucfg.get("ai") or {}).get("model", "claude-opus-5"),
        }

    def balance(self, max_age: float = 60.0) -> dict:
        """Остаток кредитов Serper. Держим минуту в кеше: смету форма пересчитывает на каждый чих."""
        if not self.keys.get("serper"):
            return {"balance": None}
        ts, value = self._balance
        if time.time() - ts > max_age:
            from .sources.search import serper_balance
            value = serper_balance(self.keys["serper"])
            self._balance = (time.time(), value)
        return {"balance": value}

    # --- ключи API ---
    def keys_view(self) -> dict:
        return {"services": keys_state(self.env_path), "caps": self.caps()}

    def save_keys(self, values: dict) -> dict:
        """Пишем в .env и подхватываем на лету: перезапуск сервера не нужен."""
        clean = {name: str(values[name]).strip() for name in ENV_NAMES if name in values}
        if not clean:
            raise ValueError("Нечего сохранять.")
        write_env(self.env_path, clean)
        refresh_keys(self.cfg)
        self._balance = (0.0, None)          # остаток кредитов пересчитаем с новым ключом
        self.crms = build_crms(self.keys)
        return self.keys_view()

    # --- задачи ---
    def running(self) -> Job | None:
        return next((j for j in self.jobs.values() if j.status == "running"), None)

    def start(self, spec: Spec, user: str = "", schedule_id: int | None = None) -> Job:
        with self.lock:
            if self.running():
                raise RuntimeError("Уже идёт поиск — дождись окончания или останови его.")
            job = Job(spec, self.cfg, self.db, user, schedule_id, self.renderer)
            self.jobs[job.id] = job
        threading.Thread(target=run_job, args=(job,), daemon=True).start()
        return job

    def scheduler_loop(self) -> None:
        """Автопоиск: раз в 30 с проверяем расписания; одна задача за раз."""
        while True:
            time.sleep(30)
            try:
                if self.running():
                    continue
                now = time.time()
                for s in self.db.schedules():
                    if s["enabled"] and (s["next_run"] or 0) <= now:
                        spec = Spec.from_json({**s["spec"], "title": s["title"], "notify": True})
                        job = self.start(spec, s["owner"], s["id"])
                        self.db.update_schedule(s["id"], last_run=now, last_run_id=job.id,
                                                next_run=now + s["every_hours"] * 3600)
                        break
            except Exception as e:  # noqa: BLE001 — планировщик не должен падать
                print(f"[автопоиск] ошибка: {e}")

    def ai_suggest(self, body: dict) -> dict:
        if not self.keys.get("anthropic"):
            raise RuntimeError("Нет ANTHROPIC_API_KEY в .env")
        from .ai import AI
        ai = AI(self.keys["anthropic"], self.ucfg.get("ai") or {})
        plan = ai.suggest(str(body.get("description") or ""), [str(k) for k in body.get("keywords") or []],
                          str(body.get("sphere") or ""), [str(c) for c in body.get("countries") or []])
        if not plan:
            raise RuntimeError(ai.disabled or ai.last_error or "ИИ не ответил")
        return {**plan.model_dump(), "cost": round(ai.cost, 4)}

    def crm_push(self, ids: list[int], crm: str, user: str) -> dict:
        if crm not in self.crms:
            raise RuntimeError("CRM не настроена в .env")
        companies = [c for c in (self.db.get(i) for i in ids) if c]
        todo = [c for c in companies if not c.get("crm_id")]
        results = self.crms[crm].push(todo) if todo else []
        ok = 0
        errors = []
        for cid, crm_id, err in results:
            if crm_id:
                self.db.mark_crm(cid, crm, crm_id, user)
                ok += 1
            else:
                errors.append(err)
        return {"ok": ok, "skipped": len(companies) - len(todo), "errors": errors[:5], "failed": len(errors)}

    # --- вход ---
    def login_allowed(self, ip: str) -> bool:
        now = time.time()
        fails = [t for t in self._fails.get(ip, []) if now - t < 600]
        self._fails[ip] = fails
        return len(fails) < 8

    def login_failed(self, ip: str) -> None:
        self._fails.setdefault(ip, []).append(time.time())


def make_handler(app: App):
    class Handler(BaseHTTPRequestHandler):
        server_version = "LeadGen/2.0"

        def log_message(self, *args):  # тихо: прогресс виден в интерфейсе
            pass

        # --- ответы ---
        def _send(self, code: int, body: bytes, ctype: str, extra: dict | None = None):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, code: int = 200, extra: dict | None = None):
            self._send(code, json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8"),
                       "application/json; charset=utf-8", extra)

        def _err(self, code: int, msg: str):
            self._json({"error": msg}, code)

        def _body(self) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            if not n or n > 20_000_000:
                return {}
            try:
                d = json.loads(self.rfile.read(n).decode("utf-8"))
                return d if isinstance(d, dict) else {}
            except ValueError:
                return {}

        def _token(self) -> str:
            c = SimpleCookie(self.headers.get("Cookie") or "")
            return c[COOKIE].value if COOKIE in c else ""

        def _user(self):
            """Имя пользователя, '' без авторизации, None — не вошёл (ответ 401 уже отправлен)."""
            if not app.auth:
                return ""
            u = app.db.session_user(self._token())
            if u is None:
                self._err(401, "Нужно войти")
            return u

        # --- маршруты ---
        def do_GET(self):
            url = urlparse(self.path)
            if url.path in STATIC:
                name, ctype = STATIC[url.path]
                return self._send(200, (WEB_DIR / name).read_bytes(), ctype)
            parts = [p for p in url.path.split("/") if p]
            if parts[:1] != ["api"]:
                return self._err(404, "not found")
            if parts == ["api", "me"]:
                u = app.db.session_user(self._token()) if app.auth else ""
                return self._json({"user": u, "auth": app.auth})
            user = self._user()
            if user is None:
                return
            q = parse_qs(url.query)
            if parts == ["api", "meta"]:
                return self._json(app.meta(user))
            if parts == ["api", "balance"]:
                return self._json(app.balance())
            if parts == ["api", "runs"]:
                return self._json({"runs": app.db.runs()})
            if len(parts) == 3 and parts[1] == "runs":
                run = app.db.run(parts[2])
                if not run:
                    return self._err(404, "нет такого поиска")
                return self._json({"run": run, "rows": app.db.run_rows(parts[2])})
            if parts == ["api", "jobs", "current"]:
                j = app.running()
                return self._json({"id": j.id if j else None})
            if len(parts) >= 3 and parts[1] == "jobs":
                job = app.jobs.get(parts[2])
                if not job:
                    return self._err(404, "задача не найдена (сервер перезапускали?)")
                if len(parts) == 3:
                    try:
                        log_from = int(q.get("log_from", ["0"])[0])
                    except ValueError:
                        log_from = 0
                    return self._json(job.state(log_from))
                if parts[3] == "leads":
                    return self._json({"leads": job.rows(), "version": job.version, "final": job.final})
            if len(parts) == 3 and parts[1] == "companies" and parts[2].isdigit():
                c = app.db.get(int(parts[2]))
                return self._json(c) if c else self._err(404, "нет такой компании")
            if parts == ["api", "schedules"]:
                return self._json({"schedules": app.db.schedules()})
            if parts == ["api", "keys"]:
                return self._json(app.keys_view())
            return self._err(404, "not found")

        def do_POST(self):
            parts = [p for p in urlparse(self.path).path.split("/") if p]
            # защита от CSRF: только JSON (кросс-сайтовая форма его не отправит без preflight)
            if "application/json" not in (self.headers.get("Content-Type") or ""):
                return self._err(415, "нужен Content-Type: application/json")
            body = self._body()
            if parts == ["api", "login"]:
                return self._login(body)
            if parts == ["api", "logout"]:
                app.db.delete_session(self._token())
                return self._json({"ok": True}, extra={"Set-Cookie": f"{COOKIE}=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict"})
            user = self._user()
            if user is None:
                return
            try:
                return self._post(parts, body, user)
            except (RuntimeError, ValueError) as e:
                return self._err(400, str(e))

        def _login(self, body: dict):
            ip = self.client_address[0]
            if not app.login_allowed(ip):
                return self._err(429, "Слишком много попыток — подожди 10 минут")
            name = str(body.get("user") or "").strip()
            if not check_password(str(body.get("password") or ""), app.db.user_hash(name)):
                app.login_failed(ip)
                time.sleep(1)
                return self._err(401, "Неверное имя или пароль")
            token = new_token()
            app.db.create_session(token, name)
            return self._json({"ok": True, "user": name}, extra={
                "Set-Cookie": f"{COOKIE}={token}; Path=/; Max-Age=2592000; HttpOnly; SameSite=Strict"})

        def _post(self, parts: list[str], body: dict, user: str):
            if parts == ["api", "estimate"]:
                return self._json(estimate(Spec.from_json(body), app.caps(), app.ucfg,
                                           app.balance()["balance"]))
            if parts == ["api", "ai", "suggest"]:
                return self._json(app.ai_suggest(body))
            if parts == ["api", "jobs"]:
                spec = Spec.from_json(body)
                if not spec.keywords:
                    raise ValueError("Впиши хотя бы одну ключевую фразу.")
                if not spec.any_source:
                    raise ValueError("Включи хотя бы один источник.")
                try:
                    job = app.start(spec, user)
                except RuntimeError as e:
                    return self._err(409, str(e))
                return self._json({"id": job.id})
            if len(parts) == 4 and parts[1] == "jobs" and parts[3] == "cancel":
                job = app.jobs.get(parts[2])
                if not job:
                    return self._err(404, "задача не найдена")
                job.cancel.set()
                job.say(f"Остановка по запросу{' (' + user + ')' if user else ''}…")
                return self._json({"ok": True})
            if parts == ["api", "companies", "query"]:
                f = dict(body)
                if f.get("owner") == "__me":
                    f["owner"] = user or "__none"
                return self._json(app.db.companies(f))
            if parts == ["api", "companies", "update"]:
                ids = [int(i) for i in body.get("ids") or [] if str(i).isdigit()]
                fields = {k: body[k] for k in ("status", "owner", "comment") if k in body}
                if fields.get("owner") == "__me":
                    fields["owner"] = user
                n = app.db.update_sales(ids, fields, user or "локально")
                return self._json({"updated": n})
            if parts == ["api", "crm", "push"]:
                ids = [int(i) for i in body.get("ids") or [] if str(i).isdigit()]
                return self._json(app.crm_push(ids, str(body.get("crm") or ""), user or "локально"))
            if parts == ["api", "schedules"]:
                spec = Spec.from_json(body.get("spec") or {})
                if not spec.keywords:
                    raise ValueError("В автопоиске нет ключевых слов.")
                hours = int(body.get("every_hours") or 24)
                if hours < 1:
                    raise ValueError("Интервал — от 1 часа.")
                title = str(body.get("title") or spec.keywords[0])[:80]
                from dataclasses import asdict
                sid = app.db.add_schedule(title, asdict(spec), hours, user)
                return self._json({"id": sid})
            if len(parts) >= 3 and parts[1] == "schedules" and parts[2].isdigit():
                sid = int(parts[2])
                if len(parts) == 4 and parts[3] == "delete":
                    app.db.delete_schedule(sid)
                    return self._json({"ok": True})
                if len(parts) == 4 and parts[3] == "run":
                    s = next((x for x in app.db.schedules() if x["id"] == sid), None)
                    if not s:
                        return self._err(404, "нет такого автопоиска")
                    try:
                        job = app.start(Spec.from_json({**s["spec"], "title": s["title"], "notify": True}),
                                        user, sid)
                    except RuntimeError as e:
                        return self._err(409, str(e))
                    app.db.update_schedule(sid, last_run=time.time(), last_run_id=job.id)
                    return self._json({"id": job.id})
                fields = {}
                if "enabled" in body:
                    fields["enabled"] = 1 if body["enabled"] else 0
                if "every_hours" in body:
                    fields["every_hours"] = max(1, int(body["every_hours"]))
                    fields["next_run"] = time.time() + fields["every_hours"] * 3600
                app.db.update_schedule(sid, **fields)
                return self._json({"ok": True})
            if parts == ["api", "keys"]:
                return self._json(app.save_keys(body.get("values") or {}))
            if parts == ["api", "keys", "test"]:
                result = check_key(str(body.get("id") or ""), app.env_path)
                return self._json({**result, "caps": app.caps()})
            if parts == ["api", "telegram", "test"]:
                err = send_telegram(app.keys, "🎯 Лидогенератор: тестовое уведомление")
                if err:
                    raise RuntimeError(err)
                return self._json({"ok": True})
            if parts == ["api", "xlsx"]:
                rows = body.get("rows") or []
                buf = io.BytesIO()
                rows_to_xlsx(rows, buf, float(body.get("threshold") or 0.5))
                name = re.sub(r"[^\w\-. ]", "_", str(body.get("name") or "leads"))[:80] + ".xlsx"
                return self._send(200, buf.getvalue(),
                                  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                  {"Content-Disposition": f"attachment; filename=\"leads.xlsx\"; "
                                                          f"filename*=UTF-8''{quote(name)}"})
            return self._err(404, "not found")

    return Handler


def serve(cfg: dict, host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True) -> None:
    app = App(cfg, host)
    if host not in LOCAL_HOSTS and not app.db.users():
        print("Для доступа по сети сначала заведи пользователя: python app.py --add-user ИМЯ")
        sys.exit(1)
    threading.Thread(target=app.scheduler_loop, daemon=True).start()
    httpd = ThreadingHTTPServer((host, port), make_handler(app))
    url = f"http://{'127.0.0.1' if host in ('0.0.0.0', '::') else host}:{port}/"
    caps = app.caps()
    print(f"Лидогенератор запущен: {url}" + ("  (вход по паролю)" if app.auth else ""))
    on = lambda b: "да" if b else "нет"  # noqa: E731
    print(f"Поиск: {caps['backend']} · Google Карты: {on(caps['serper'])} · Яндекс: {on(caps['yandex'])} · "
          f"Apify: {on(caps['apify'])} · ИИ: {on(caps['ai'])} · DaData: {on(caps['dadata'])} · "
          f"Chromium: {on(caps['render'])} · CRM: {', '.join(caps['crm']) or 'нет'} · "
          f"Telegram: {on(caps['telegram'])}")
    print("Остановить: Ctrl+C")
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nОстановлено.")
    finally:
        httpd.server_close()
