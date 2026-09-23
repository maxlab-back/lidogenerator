"""База лидов на SQLite: компании копятся между поисками.

Одна компания = одна строка companies. Ключи склейки (телефоны, домен, карточка) лежат в
company_keys, поэтому повторный поиск узнаёт уже найденных, а два ключа одной компании,
найденные в разных поисках, сливают записи. Статус, менеджер и комментарий — работа отдела
продаж, поиск их не трогает. Здесь же: история поисков, кеш ответов платных API,
расписания автопоиска, пользователи и сессии.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path

STATUSES = {
    "new": "Новый", "work": "В работе", "call": "Созвон", "offer": "КП отправлено",
    "client": "Клиент", "refused": "Отказ", "bad": "Не наш",
}

LIST_FIELDS = ("phones", "emails", "rubrics", "ai_services")
JSON_FIELDS = LIST_FIELDS + ("socials",)
# из свежего поиска берём, только если в базе пусто
FILL_FIELDS = ("name", "country", "region", "city", "address", "website", "rating", "description",
               "inn", "ogrn", "legal_name", "okved", "manager", "legal_status", "employees",
               "revenue", "source_url")
# всегда обновляются свежими данными (последняя проверка важнее старой)
FRESH_FIELDS = ("ai_verdict", "ai_type", "ai_reason", "ai_confidence", "score")
SALES_FIELDS = ("status", "owner", "comment")

SCHEMA = """
CREATE TABLE IF NOT EXISTS companies (
  id INTEGER PRIMARY KEY,
  name TEXT DEFAULT '', country TEXT DEFAULT '', region TEXT DEFAULT '', city TEXT DEFAULT '',
  address TEXT DEFAULT '', website TEXT DEFAULT '',
  phones TEXT DEFAULT '[]', emails TEXT DEFAULT '[]', socials TEXT DEFAULT '{}',
  rubrics TEXT DEFAULT '[]', rating TEXT DEFAULT '', description TEXT DEFAULT '',
  inn TEXT DEFAULT '', ogrn TEXT DEFAULT '', legal_name TEXT DEFAULT '', okved TEXT DEFAULT '',
  manager TEXT DEFAULT '', legal_status TEXT DEFAULT '', employees TEXT DEFAULT '', revenue TEXT DEFAULT '',
  ai_verdict TEXT DEFAULT '', ai_type TEXT DEFAULT '', ai_reason TEXT DEFAULT '',
  ai_confidence INTEGER, ai_services TEXT DEFAULT '[]',
  score REAL DEFAULT 0, source TEXT DEFAULT '', source_url TEXT DEFAULT '',
  status TEXT DEFAULT 'new', owner TEXT DEFAULT '', comment TEXT DEFAULT '',
  crm TEXT DEFAULT '', crm_id TEXT DEFAULT '', crm_at TEXT DEFAULT '',
  first_seen TEXT, last_seen TEXT, first_run TEXT DEFAULT '', updated_at TEXT
);
CREATE TABLE IF NOT EXISTS company_keys (key TEXT PRIMARY KEY, company_id INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS ix_keys_company ON company_keys(company_id);
CREATE TABLE IF NOT EXISTS runs (
  id TEXT PRIMARY KEY, created TEXT, title TEXT, spec TEXT, user TEXT DEFAULT '',
  status TEXT DEFAULT 'running', credits INTEGER DEFAULT 0, ai_cost REAL DEFAULT 0,
  total INTEGER DEFAULT 0, new INTEGER DEFAULT 0, schedule_id INTEGER
);
CREATE TABLE IF NOT EXISTS run_leads (
  run_id TEXT, company_id INTEGER, score REAL, query TEXT DEFAULT '', matched TEXT DEFAULT '[]',
  is_new INTEGER DEFAULT 0, PRIMARY KEY (run_id, company_id)
);
CREATE INDEX IF NOT EXISTS ix_run_leads_company ON run_leads(company_id);
CREATE TABLE IF NOT EXISTS history (
  id INTEGER PRIMARY KEY, company_id INTEGER, ts TEXT, user TEXT, action TEXT, detail TEXT
);
CREATE INDEX IF NOT EXISTS ix_history_company ON history(company_id);
CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, value TEXT, ts REAL);
CREATE TABLE IF NOT EXISTS schedules (
  id INTEGER PRIMARY KEY, title TEXT, spec TEXT, every_hours INTEGER, next_run REAL,
  last_run REAL, last_run_id TEXT DEFAULT '', enabled INTEGER DEFAULT 1, owner TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS users (name TEXT PRIMARY KEY, pw_hash TEXT, created TEXT);
CREATE TABLE IF NOT EXISTS sessions (token TEXT PRIMARY KEY, user TEXT, expires REAL);
"""


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _norm_domain(url: str) -> str:
    u = (url or "").lower().strip()
    for p in ("https://", "http://", "www."):
        if u.startswith(p):
            u = u[len(p):]
    return u.split("/")[0]


def lead_keys(lead: dict) -> list[str]:
    """Ключи склейки: любой телефон, домен; без них — карточка карт или имя+город."""
    keys = ["tel:" + "".join(c for c in p if c.isdigit()) for p in lead.get("phones") or []]
    if lead.get("website"):
        keys.append("site:" + _norm_domain(lead["website"]))
    from .contacts import tg_username
    for url in (lead.get("socials") or {}).get("telegram") or []:
        if tg_username(url):
            keys.append("tg:" + tg_username(url))   # канал из Telegram = та же фирма из карт
    if not keys:
        if lead.get("source_url"):
            keys.append("card:" + lead["source_url"])
        elif lead.get("name"):
            keys.append("name:" + (lead["name"] + "|" + (lead.get("city") or "")).lower().strip())
    return [k for k in dict.fromkeys(keys) if len(k) > 5]


def _union(a, b) -> list:
    out = list(a or [])
    for x in b or []:
        if x and x not in out:
            out.append(x)
    return out


def merge_company(base: dict, new: dict) -> dict:
    out = dict(base)
    for f in LIST_FIELDS:
        out[f] = _union(base.get(f), new.get(f))
    from .contacts import merge_socials
    out["socials"] = merge_socials(base.get("socials"), new.get("socials"))
    for f in FILL_FIELDS:
        if not out.get(f) and new.get(f):
            out[f] = new[f]
    for f in FRESH_FIELDS:
        if new.get(f) not in (None, ""):
            out[f] = new[f]
    srcs = [s for s in (base.get("source") or "").split("+") if s]
    for s in (new.get("source") or "").split("+"):
        if s and s not in srcs:
            srcs.append(s)
    out["source"] = "+".join(srcs)
    return out


class DB:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path), check_same_thread=False, timeout=30)
        self.conn.row_factory = sqlite3.Row
        # lower() в SQLite не знает кириллицу — регистрируем питоновский
        self.conn.create_function("pylower", 1, lambda s: (s or "").lower().replace("ё", "е"))
        self.lock = threading.RLock()
        with self.lock:
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.executescript(SCHEMA)
            self.conn.commit()
        self._columns = [r[1] for r in self.conn.execute("PRAGMA table_info(companies)")]

    # ------------------------------------------------------------------ компании
    def _decode(self, r: sqlite3.Row | None) -> dict | None:
        if r is None:
            return None
        d = dict(r)
        for f in JSON_FIELDS:
            if f in d:
                try:
                    d[f] = json.loads(d[f] or ("{}" if f == "socials" else "[]"))
                except ValueError:
                    d[f] = {} if f == "socials" else []
        if "matched" in d and isinstance(d["matched"], str):
            try:
                d["matched"] = json.loads(d["matched"])
            except ValueError:
                d["matched"] = []
        return d

    def _encode(self, d: dict) -> dict:
        row = {k: v for k, v in d.items() if k in self._columns and k != "id"}
        for f in JSON_FIELDS:
            if f in row:
                row[f] = json.dumps(row[f] or ({} if f == "socials" else []), ensure_ascii=False)
        return row

    def get(self, cid: int) -> dict | None:
        with self.lock:
            d = self._decode(self.conn.execute("SELECT * FROM companies WHERE id=?", (cid,)).fetchone())
            if d:
                d["history"] = [dict(h) for h in self.conn.execute(
                    "SELECT ts, user, action, detail FROM history WHERE company_id=? ORDER BY id DESC LIMIT 50",
                    (cid,))]
                d["runs"] = [dict(r) for r in self.conn.execute(
                    "SELECT r.id, r.title, r.created, rl.score FROM run_leads rl JOIN runs r ON r.id=rl.run_id "
                    "WHERE rl.company_id=? ORDER BY r.created DESC LIMIT 20", (cid,))]
            return d

    def upsert(self, lead: dict, run_id: str = "") -> tuple[int, bool]:
        """Сохранить лид из поиска. -> (id компании, впервые ли видим)."""
        keys = lead_keys(lead)
        now = _now()
        with self.lock:
            c = self.conn
            ids: list[int] = []
            if keys:
                q = f"SELECT DISTINCT company_id FROM company_keys WHERE key IN ({','.join('?' * len(keys))})"
                ids = sorted(r[0] for r in c.execute(q, keys))
            if not ids:
                row = self._encode({**lead, "first_seen": now, "last_seen": now, "updated_at": now,
                                    "first_run": run_id, "status": "new"})
                cols = ", ".join(row)
                cur = c.execute(f"INSERT INTO companies ({cols}) VALUES ({','.join('?' * len(row))})",
                                list(row.values()))
                cid, is_new = cur.lastrowid, True
            else:
                cid, is_new = ids[0], False
                for other in ids[1:]:
                    self._merge_into(cid, other)
                base = self._decode(c.execute("SELECT * FROM companies WHERE id=?", (cid,)).fetchone())
                merged = merge_company(base, lead)
                merged["last_seen"] = now
                row = self._encode({k: v for k, v in merged.items() if k not in SALES_FIELDS})
                c.execute(f"UPDATE companies SET {', '.join(f'{k}=?' for k in row)} WHERE id=?",
                          [*row.values(), cid])
            for k in keys:
                c.execute("INSERT OR REPLACE INTO company_keys(key, company_id) VALUES (?, ?)", (k, cid))
            c.commit()
            return cid, is_new

    def _merge_into(self, keep: int, drop: int) -> None:
        """Одна компания нашлась под двумя записями — сливаем drop в keep."""
        c = self.conn
        a = self._decode(c.execute("SELECT * FROM companies WHERE id=?", (keep,)).fetchone())
        b = self._decode(c.execute("SELECT * FROM companies WHERE id=?", (drop,)).fetchone())
        if not a or not b:
            return
        m = merge_company(a, b)
        # работа продажников важнее: если в keep ещё «новый», а в drop уже что-то сделали — берём drop
        if a.get("status") == "new" and b.get("status") != "new":
            for f in SALES_FIELDS:
                m[f] = b.get(f, "")
        if a.get("comment") and b.get("comment") and a["comment"] != b["comment"]:
            m["comment"] = a["comment"] + "\n" + b["comment"]
        m["first_seen"] = min(filter(None, [a.get("first_seen"), b.get("first_seen")]), default=_now())
        row = self._encode(m)
        c.execute(f"UPDATE companies SET {', '.join(f'{k}=?' for k in row)} WHERE id=?", [*row.values(), keep])
        c.execute("UPDATE company_keys SET company_id=? WHERE company_id=?", (keep, drop))
        c.execute("UPDATE OR IGNORE run_leads SET company_id=? WHERE company_id=?", (keep, drop))
        c.execute("DELETE FROM run_leads WHERE company_id=?", (drop,))
        c.execute("UPDATE history SET company_id=? WHERE company_id=?", (keep, drop))
        c.execute("DELETE FROM companies WHERE id=?", (drop,))

    def update_fields(self, cid: int, fields: dict) -> None:
        """Техническое обновление (ИИ, юр.данные, CRM) — без истории."""
        row = self._encode(fields)
        if not row:
            return
        with self.lock:
            self.conn.execute(f"UPDATE companies SET {', '.join(f'{k}=?' for k in row)} WHERE id=?",
                              [*row.values(), cid])
            self.conn.commit()

    def update_sales(self, ids: list[int], fields: dict, user: str) -> int:
        """Статус / менеджер / комментарий от пользователя — с записью в историю."""
        fields = {k: str(v) for k, v in fields.items() if k in SALES_FIELDS}
        if "status" in fields and fields["status"] not in STATUSES:
            raise ValueError("неизвестный статус")
        if not fields or not ids:
            return 0
        now = _now()
        n = 0
        with self.lock:
            for cid in ids:
                old = self.conn.execute("SELECT status, owner, comment FROM companies WHERE id=?", (cid,)).fetchone()
                if not old:
                    continue
                changes = {k: v for k, v in fields.items() if (old[k] or "") != v}
                if not changes:
                    continue
                self.conn.execute(
                    f"UPDATE companies SET {', '.join(f'{k}=?' for k in changes)}, updated_at=? WHERE id=?",
                    [*changes.values(), now, cid])
                for k, v in changes.items():
                    detail = STATUSES.get(v, v) if k == "status" else v
                    self.conn.execute("INSERT INTO history(company_id, ts, user, action, detail) VALUES (?,?,?,?,?)",
                                      (cid, now, user, k, detail[:500]))
                n += 1
            self.conn.commit()
        return n

    def log(self, cid: int, user: str, action: str, detail: str) -> None:
        with self.lock:
            self.conn.execute("INSERT INTO history(company_id, ts, user, action, detail) VALUES (?,?,?,?,?)",
                              (cid, _now(), user, action, detail[:500]))
            self.conn.commit()

    _SORTS = {"score": "c.score", "name": "pylower(c.name)", "city": "pylower(c.city)",
              "first_seen": "c.first_seen", "updated_at": "c.updated_at", "status": "c.status",
              "source": "c.source"}

    def companies(self, f: dict) -> dict:
        """Поиск по базе с фильтрами. f: status[], owner, q, country, region, min_score,
        has_phone, has_email, run_id, new_only, sort, dir, limit, offset."""
        where: list[str] = []
        args: list = []
        if f.get("q"):
            where.append("(pylower(c.name) LIKE ? OR pylower(c.website) LIKE ? OR pylower(c.city) LIKE ? "
                         "OR pylower(c.region) LIKE ? OR pylower(c.address) LIKE ? OR c.phones LIKE ? "
                         "OR pylower(c.emails) LIKE ? OR c.inn LIKE ? OR pylower(c.comment) LIKE ? "
                         "OR pylower(c.rubrics) LIKE ?)")
            q = "%" + str(f["q"]).lower().replace("ё", "е").strip() + "%"
            args += [q] * 10
        for key in ("country", "region"):
            if f.get(key):
                where.append(f"c.{key}=?")
                args.append(f[key])
        if f.get("owner") == "__none":
            where.append("c.owner=''")
        elif f.get("owner"):
            where.append("c.owner=?")
            args.append(f["owner"])
        if f.get("min_score"):
            where.append("c.score>=?")
            args.append(float(f["min_score"]))
        if f.get("has_phone"):
            where.append("c.phones!='[]'")
        if f.get("has_email"):
            where.append("c.emails!='[]'")
        if f.get("run_id"):
            where.append("c.id IN (SELECT company_id FROM run_leads WHERE run_id=?)")
            args.append(f["run_id"])
        if f.get("new_only"):
            where.append("c.status='new'")
        if f.get("crm") == "no":
            where.append("c.crm_id=''")

        base_where = list(where)
        base_args = list(args)
        statuses = [s for s in (f.get("status") or []) if s in STATUSES]
        if statuses:
            where.append(f"c.status IN ({','.join('?' * len(statuses))})")
            args += statuses

        sql_where = ("WHERE " + " AND ".join(where)) if where else ""
        sort = self._SORTS.get(f.get("sort") or "score", "c.score")
        direction = "ASC" if f.get("dir") == "asc" else "DESC"
        limit = max(1, min(int(f.get("limit") or 200), 5000))
        offset = max(0, int(f.get("offset") or 0))
        with self.lock:
            total = self.conn.execute(f"SELECT COUNT(*) FROM companies c {sql_where}", args).fetchone()[0]
            rows = [self._decode(r) for r in self.conn.execute(
                f"SELECT c.* FROM companies c {sql_where} ORDER BY {sort} {direction}, c.id DESC LIMIT ? OFFSET ?",
                [*args, limit, offset])]
            bw = ("WHERE " + " AND ".join(base_where)) if base_where else ""
            counts = {r[0]: r[1] for r in self.conn.execute(
                f"SELECT c.status, COUNT(*) FROM companies c {bw} GROUP BY c.status", base_args)}
            regions = [r[0] for r in self.conn.execute(
                "SELECT DISTINCT region FROM companies WHERE region!='' ORDER BY region")]
            owners = [r[0] for r in self.conn.execute(
                "SELECT DISTINCT owner FROM companies WHERE owner!='' ORDER BY owner")]
        return {"rows": rows, "total": total, "status_counts": counts, "regions": regions, "owners": owners}

    def mark_crm(self, cid: int, crm: str, crm_id: str, user: str) -> None:
        with self.lock:
            self.conn.execute("UPDATE companies SET crm=?, crm_id=?, crm_at=? WHERE id=?",
                              (crm, str(crm_id), _now(), cid))
            self.conn.execute("INSERT INTO history(company_id, ts, user, action, detail) VALUES (?,?,?,?,?)",
                              (cid, _now(), user, "crm", f"{crm} #{crm_id}"))
            self.conn.commit()

    # ------------------------------------------------------------------ поиски
    def start_run(self, run_id: str, title: str, spec: dict, user: str = "", schedule_id: int | None = None):
        with self.lock:
            self.conn.execute("INSERT OR REPLACE INTO runs(id, created, title, spec, user, schedule_id) "
                              "VALUES (?,?,?,?,?,?)",
                              (run_id, _now(), title, json.dumps(spec, ensure_ascii=False), user, schedule_id))
            self.conn.commit()

    def finish_run(self, run_id: str, **fields) -> None:
        allowed = {k: v for k, v in fields.items() if k in ("status", "credits", "ai_cost", "total", "new")}
        if not allowed:
            return
        with self.lock:
            self.conn.execute(f"UPDATE runs SET {', '.join(f'{k}=?' for k in allowed)} WHERE id=?",
                              [*allowed.values(), run_id])
            self.conn.commit()

    def add_run_lead(self, run_id: str, cid: int, score: float, query: str, matched: list, is_new: bool):
        with self.lock:
            prev = self.conn.execute("SELECT is_new, score FROM run_leads WHERE run_id=? AND company_id=?",
                                     (run_id, cid)).fetchone()
            if prev:
                self.conn.execute("UPDATE run_leads SET score=?, is_new=? WHERE run_id=? AND company_id=?",
                                  (max(prev["score"] or 0, score), int(bool(prev["is_new"]) or is_new), run_id, cid))
            else:
                self.conn.execute("INSERT INTO run_leads(run_id, company_id, score, query, matched, is_new) "
                                  "VALUES (?,?,?,?,?,?)",
                                  (run_id, cid, score, query, json.dumps(matched, ensure_ascii=False), int(is_new)))
            self.conn.commit()

    def run_rows(self, run_id: str) -> list[dict]:
        with self.lock:
            rows = self.conn.execute(
                "SELECT c.*, rl.score AS run_score, rl.query AS query, rl.matched AS matched, rl.is_new AS is_new "
                "FROM run_leads rl JOIN companies c ON c.id=rl.company_id WHERE rl.run_id=? "
                "ORDER BY rl.score DESC, c.id", (run_id,)).fetchall()
        out = []
        for r in rows:
            d = self._decode(r)
            d["score"] = d.pop("run_score")      # релевантность именно в этом поиске
            out.append(d)
        return out

    def runs(self, limit: int = 100) -> list[dict]:
        with self.lock:
            return [dict(r) for r in self.conn.execute(
                "SELECT id, created, title, user, status, credits, ai_cost, total, new, schedule_id "
                "FROM runs ORDER BY created DESC LIMIT ?", (limit,))]

    def run(self, run_id: str) -> dict | None:
        with self.lock:
            r = self.conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if not r:
            return None
        d = dict(r)
        d["spec"] = json.loads(d.get("spec") or "{}")
        return d

    def stats(self) -> dict:
        with self.lock:
            counts = {r[0]: r[1] for r in self.conn.execute("SELECT status, COUNT(*) FROM companies GROUP BY status")}
        return {"total": sum(counts.values()), "by_status": counts}

    # ------------------------------------------------------------------ кеш API
    def cache_get(self, key: str, max_age_s: float):
        with self.lock:
            r = self.conn.execute("SELECT value, ts FROM cache WHERE key=?", (key,)).fetchone()
        if not r or time.time() - r["ts"] > max_age_s:
            return None
        return json.loads(r["value"])

    def cache_set(self, key: str, value) -> None:
        with self.lock:
            self.conn.execute("INSERT OR REPLACE INTO cache(key, value, ts) VALUES (?,?,?)",
                              (key, json.dumps(value, ensure_ascii=False), time.time()))
            self.conn.commit()

    # ------------------------------------------------------------------ автопоиск
    def schedules(self) -> list[dict]:
        with self.lock:
            out = [dict(r) for r in self.conn.execute("SELECT * FROM schedules ORDER BY id")]
        for s in out:
            s["spec"] = json.loads(s.get("spec") or "{}")
        return out

    def add_schedule(self, title: str, spec: dict, every_hours: int, owner: str) -> int:
        with self.lock:
            cur = self.conn.execute(
                "INSERT INTO schedules(title, spec, every_hours, next_run, enabled, owner) VALUES (?,?,?,?,1,?)",
                (title, json.dumps(spec, ensure_ascii=False), every_hours, time.time() + every_hours * 3600, owner))
            self.conn.commit()
            return cur.lastrowid

    def update_schedule(self, sid: int, **fields) -> None:
        allowed = {k: v for k, v in fields.items()
                   if k in ("title", "every_hours", "next_run", "last_run", "last_run_id", "enabled")}
        if not allowed:
            return
        with self.lock:
            self.conn.execute(f"UPDATE schedules SET {', '.join(f'{k}=?' for k in allowed)} WHERE id=?",
                              [*allowed.values(), sid])
            self.conn.commit()

    def delete_schedule(self, sid: int) -> None:
        with self.lock:
            self.conn.execute("DELETE FROM schedules WHERE id=?", (sid,))
            self.conn.commit()

    # ------------------------------------------------------------------ пользователи
    def set_user(self, name: str, pw_hash: str) -> None:
        with self.lock:
            self.conn.execute("INSERT OR REPLACE INTO users(name, pw_hash, created) VALUES (?,?,?)",
                              (name, pw_hash, _now()))
            self.conn.commit()

    def user_hash(self, name: str) -> str | None:
        with self.lock:
            r = self.conn.execute("SELECT pw_hash FROM users WHERE name=?", (name,)).fetchone()
        return r[0] if r else None

    def users(self) -> list[str]:
        with self.lock:
            return [r[0] for r in self.conn.execute("SELECT name FROM users ORDER BY name")]

    def delete_user(self, name: str) -> None:
        with self.lock:
            self.conn.execute("DELETE FROM users WHERE name=?", (name,))
            self.conn.execute("DELETE FROM sessions WHERE user=?", (name,))
            self.conn.commit()

    def create_session(self, token: str, user: str, days: int = 30) -> None:
        with self.lock:
            self.conn.execute("DELETE FROM sessions WHERE expires<?", (time.time(),))
            self.conn.execute("INSERT INTO sessions(token, user, expires) VALUES (?,?,?)",
                              (token, user, time.time() + days * 86400))
            self.conn.commit()

    def session_user(self, token: str) -> str | None:
        if not token:
            return None
        with self.lock:
            r = self.conn.execute("SELECT user, expires FROM sessions WHERE token=?", (token,)).fetchone()
        if not r or r["expires"] < time.time():
            return None
        return r["user"]

    def delete_session(self, token: str) -> None:
        with self.lock:
            self.conn.execute("DELETE FROM sessions WHERE token=?", (token,))
            self.conn.commit()

    # ------------------------------------------------------------------ миграция
    def import_run_files(self, runs_dir: Path) -> int:
        """Разово переносим старые output/runs/*.json (до появления базы) в базу."""
        n = 0
        if not runs_dir.exists():
            return 0
        for p in sorted(runs_dir.glob("*.json")):
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            rid = d.get("id") or p.stem
            with self.lock:
                if self.conn.execute("SELECT 1 FROM runs WHERE id=?", (rid,)).fetchone():
                    continue
            self.start_run(rid, d.get("title", ""), d.get("spec") or {})
            with self.lock:
                self.conn.execute("UPDATE runs SET created=? WHERE id=?", (d.get("created") or _now(), rid))
            new = 0
            leads = d.get("leads") or []
            for l in leads:
                lead = {k: v for k, v in l.items() if k not in ("id", "matched", "query")}
                cid, is_new = self.upsert(lead, rid)
                new += is_new
                self.add_run_lead(rid, cid, l.get("score") or 0, l.get("query", ""), l.get("matched") or [], is_new)
            self.finish_run(rid, status="done", credits=d.get("credits", 0), total=len(leads), new=new)
            n += 1
        return n
