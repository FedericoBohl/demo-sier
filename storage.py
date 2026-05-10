from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from game_engine import default_settings, compute_next_period

DB_PATH = Path(__file__).resolve().parent / "sier_demo.db"
UTC = timezone.utc


# ---------- utilidades de tiempo ----------
def utcnow() -> datetime:
    return datetime.now(tz=UTC)


def dt_to_str(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def str_to_dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _duration_for_period(settings: Dict[str, Any], period_no: int, fallback_minutes: int = 15) -> int:
    durations = settings.get("period_durations")
    if isinstance(durations, list) and durations:
        idx = max(0, int(period_no) - 1)
        if idx < len(durations):
            try:
                return max(1, int(durations[idx]))
            except Exception:
                return max(1, int(fallback_minutes))
        try:
            return max(1, int(durations[-1]))
        except Exception:
            return max(1, int(fallback_minutes))
    return max(1, int(fallback_minutes))


# ---------- conexión sqlite ----------
def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


@contextmanager
def transaction(immediate: bool = False):
    conn = get_connection()
    try:
        if immediate:
            conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------- hashing simple ----------
def hash_password(password: str, *, salt: str | None = None) -> str:
    actual_salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), actual_salt.encode("utf-8"), 150_000
    ).hex()
    return f"{actual_salt}${digest}"



def verify_password(password: str, stored: str) -> bool:
    try:
        salt, digest = stored.split("$", 1)
    except ValueError:
        return False
    attempted = hash_password(password, salt=salt).split("$", 1)[1]
    return hmac.compare_digest(attempted, digest)


# ---------- inicialización ----------
def initialize_db() -> None:
    with transaction() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL,
                country_id INTEGER,
                is_active INTEGER NOT NULL DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS worlds (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                status TEXT NOT NULL,
                current_period INTEGER NOT NULL,
                total_periods INTEGER NOT NULL,
                period_duration_minutes INTEGER NOT NULL,
                auto_advance_when_all_submitted INTEGER NOT NULL DEFAULT 0,
                started_at TEXT,
                deadline_at TEXT,
                settings_json TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS countries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                world_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                sort_order INTEGER NOT NULL,
                FOREIGN KEY(world_id) REFERENCES worlds(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS country_states (
                country_id INTEGER PRIMARY KEY,
                world_id INTEGER NOT NULL,
                fx_index REAL NOT NULL,
                gov_spending REAL NOT NULL,
                vat_rate REAL NOT NULL,
                public_employment REAL NOT NULL,
                price_index REAL NOT NULL,
                inflation REAL NOT NULL,
                unemployment REAL NOT NULL,
                consumption REAL NOT NULL,
                exports REAL NOT NULL,
                imports REAL NOT NULL,
                political_support REAL NOT NULL,
                deficit_ratio REAL NOT NULL,
                card_status TEXT NOT NULL,
                required_gov_delta_next REAL,
                FOREIGN KEY(country_id) REFERENCES countries(id) ON DELETE CASCADE,
                FOREIGN KEY(world_id) REFERENCES worlds(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS tariffs (
                world_id INTEGER NOT NULL,
                from_country_id INTEGER NOT NULL,
                to_country_id INTEGER NOT NULL,
                tariff_rate REAL NOT NULL,
                PRIMARY KEY(world_id, from_country_id, to_country_id),
                FOREIGN KEY(world_id) REFERENCES worlds(id) ON DELETE CASCADE,
                FOREIGN KEY(from_country_id) REFERENCES countries(id) ON DELETE CASCADE,
                FOREIGN KEY(to_country_id) REFERENCES countries(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS submissions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                world_id INTEGER NOT NULL,
                period_no INTEGER NOT NULL,
                country_id INTEGER NOT NULL,
                submitted_by_user_id INTEGER NOT NULL,
                submitted_at TEXT NOT NULL,
                fx_delta REAL NOT NULL,
                gov_delta REAL NOT NULL,
                vat_delta REAL NOT NULL,
                public_emp_delta REAL NOT NULL,
                UNIQUE(world_id, period_no, country_id),
                FOREIGN KEY(world_id) REFERENCES worlds(id) ON DELETE CASCADE,
                FOREIGN KEY(country_id) REFERENCES countries(id) ON DELETE CASCADE,
                FOREIGN KEY(submitted_by_user_id) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS submission_tariffs (
                submission_id INTEGER NOT NULL,
                to_country_id INTEGER NOT NULL,
                tariff_delta REAL NOT NULL,
                PRIMARY KEY(submission_id, to_country_id),
                FOREIGN KEY(submission_id) REFERENCES submissions(id) ON DELETE CASCADE,
                FOREIGN KEY(to_country_id) REFERENCES countries(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS period_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                world_id INTEGER NOT NULL,
                period_no INTEGER NOT NULL,
                country_id INTEGER NOT NULL,
                applied_policy_json TEXT NOT NULL,
                state_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(world_id, period_no, country_id),
                FOREIGN KEY(world_id) REFERENCES worlds(id) ON DELETE CASCADE,
                FOREIGN KEY(country_id) REFERENCES countries(id) ON DELETE CASCADE
            );
            """
        )

        admin = conn.execute("SELECT id FROM users WHERE username = 'admin'").fetchone()
        if admin is None:
            conn.execute(
                "INSERT INTO users (username, password_hash, role, country_id, is_active) VALUES (?, ?, ?, NULL, 1)",
                ("admin", hash_password("admin123"), "admin"),
            )


# ---------- lectura / utilidades ----------
def row_to_dict(row: sqlite3.Row | None) -> Dict[str, Any] | None:
    return dict(row) if row else None



def get_active_world(conn: sqlite3.Connection | None = None) -> Dict[str, Any] | None:
    own_conn = False
    if conn is None:
        conn = get_connection()
        own_conn = True
    try:
        row = conn.execute(
            "SELECT * FROM worlds WHERE active = 1 ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        data = dict(row)
        data["settings"] = json.loads(data["settings_json"])
        return data
    finally:
        if own_conn:
            conn.close()



def get_user_by_username(username: str) -> Dict[str, Any] | None:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM users WHERE username = ? AND is_active = 1", (username.strip(),)
        ).fetchone()
        return row_to_dict(row)
    finally:
        conn.close()



def authenticate_user(username: str, password: str) -> Dict[str, Any] | None:
    user = get_user_by_username(username)
    if not user:
        return None
    if not verify_password(password, user["password_hash"]):
        return None
    return user



def get_countries(world_id: int, conn: sqlite3.Connection | None = None) -> List[Dict[str, Any]]:
    own_conn = False
    if conn is None:
        conn = get_connection()
        own_conn = True
    try:
        rows = conn.execute(
            "SELECT * FROM countries WHERE world_id = ? ORDER BY sort_order", (world_id,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        if own_conn:
            conn.close()



def get_country_name_map(world_id: int, conn: sqlite3.Connection | None = None) -> Dict[int, str]:
    return {c["id"]: c["name"] for c in get_countries(world_id, conn=conn)}



def get_current_states(world_id: int, conn: sqlite3.Connection | None = None) -> Dict[int, Dict[str, Any]]:
    own_conn = False
    if conn is None:
        conn = get_connection()
        own_conn = True
    try:
        rows = conn.execute(
            "SELECT cs.*, c.name FROM country_states cs JOIN countries c ON c.id = cs.country_id WHERE cs.world_id = ?",
            (world_id,),
        ).fetchall()
        return {row["country_id"]: dict(row) for row in rows}
    finally:
        if own_conn:
            conn.close()



def get_tariffs(world_id: int, conn: sqlite3.Connection | None = None) -> Dict[int, Dict[int, float]]:
    own_conn = False
    if conn is None:
        conn = get_connection()
        own_conn = True
    try:
        rows = conn.execute(
            "SELECT * FROM tariffs WHERE world_id = ?", (world_id,)
        ).fetchall()
        matrix: Dict[int, Dict[int, float]] = {}
        for row in rows:
            matrix.setdefault(row["from_country_id"], {})[row["to_country_id"]] = float(row["tariff_rate"])
        return matrix
    finally:
        if own_conn:
            conn.close()



def get_submissions_for_period(
    world_id: int,
    period_no: int,
    conn: sqlite3.Connection | None = None,
) -> Dict[int, Dict[str, Any]]:
    own_conn = False
    if conn is None:
        conn = get_connection()
        own_conn = True
    try:
        submissions = {}
        rows = conn.execute(
            "SELECT * FROM submissions WHERE world_id = ? AND period_no = ?",
            (world_id, period_no),
        ).fetchall()
        for row in rows:
            data = dict(row)
            trows = conn.execute(
                "SELECT to_country_id, tariff_delta FROM submission_tariffs WHERE submission_id = ?",
                (row["id"],),
            ).fetchall()
            data["tariff_changes"] = {
                int(tr["to_country_id"]): float(tr["tariff_delta"]) for tr in trows
            }
            submissions[int(row["country_id"])] = data
        return submissions
    finally:
        if own_conn:
            conn.close()



def period_submission_status(world_id: int, period_no: int) -> List[Dict[str, Any]]:
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT c.id AS country_id, c.name,
                   CASE WHEN s.id IS NULL THEN 0 ELSE 1 END AS submitted,
                   s.submitted_at,
                   u.username AS submitted_by
            FROM countries c
            LEFT JOIN submissions s
              ON s.country_id = c.id AND s.world_id = c.world_id AND s.period_no = ?
            LEFT JOIN users u ON u.id = s.submitted_by_user_id
            WHERE c.world_id = ?
            ORDER BY c.sort_order
            """,
            (period_no, world_id),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ---------- configuración y creación de mundo ----------
def reset_world_and_users_except_admin(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM submission_tariffs")
    conn.execute("DELETE FROM submissions")
    conn.execute("DELETE FROM period_results")
    conn.execute("DELETE FROM tariffs")
    conn.execute("DELETE FROM country_states")
    conn.execute("DELETE FROM countries")
    conn.execute("DELETE FROM worlds")
    conn.execute("DELETE FROM users WHERE role != 'admin'")



def create_world(
    *,
    world_name: str,
    total_periods: int,
    period_duration_minutes: int,
    auto_advance_when_all_submitted: bool,
    settings: Dict[str, Any],
    country_specs: List[Dict[str, str]],
) -> int:
    with transaction() as conn:
        reset_world_and_users_except_admin(conn)

        durations = settings.get("period_durations")
        if not isinstance(durations, list) or len(durations) != int(total_periods):
            settings["period_durations"] = [int(period_duration_minutes)] * int(total_periods)

        current_duration = _duration_for_period(settings, 1, int(period_duration_minutes))
        now = utcnow()
        deadline = now + timedelta(minutes=current_duration)
        cur = conn.execute(
            """
            INSERT INTO worlds (
                name, status, current_period, total_periods, period_duration_minutes,
                auto_advance_when_all_submitted, started_at, deadline_at, settings_json, active
            ) VALUES (?, 'running', 1, ?, ?, ?, ?, ?, ?, 1)
            """,
            (
                world_name.strip() or "Mundo demo",
                int(total_periods),
                int(current_duration),
                1 if auto_advance_when_all_submitted else 0,
                dt_to_str(now),
                dt_to_str(deadline),
                json.dumps(settings, ensure_ascii=False),
            ),
        )
        world_id = int(cur.lastrowid)

        initial = settings["initial"]
        # Crear países, estado inicial y cuentas.
        for order, spec in enumerate(country_specs, start=1):
            ccur = conn.execute(
                "INSERT INTO countries (world_id, name, sort_order) VALUES (?, ?, ?)",
                (world_id, spec["name"], order),
            )
            country_id = int(ccur.lastrowid)

            conn.execute(
                """
                INSERT INTO country_states (
                    country_id, world_id, fx_index, gov_spending, vat_rate, public_employment,
                    price_index, inflation, unemployment, consumption, exports, imports,
                    political_support, deficit_ratio, card_status, required_gov_delta_next
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
                """,
                (
                    country_id,
                    world_id,
                    initial["fx_index"],
                    initial["gov_spending"],
                    initial["vat_rate"],
                    initial["public_employment"],
                    initial["price_index"],
                    initial["inflation"],
                    initial["unemployment"],
                    initial["consumption"],
                    initial["exports"],
                    initial["imports"],
                    100.0,
                    1.8,
                    "ninguna",
                ),
            )

            conn.execute(
                "INSERT INTO users (username, password_hash, role, country_id, is_active) VALUES (?, ?, 'country_viewer', ?, 1)",
                (
                    spec["viewer_username"],
                    hash_password(spec["viewer_password"]),
                    country_id,
                ),
            )
            conn.execute(
                "INSERT INTO users (username, password_hash, role, country_id, is_active) VALUES (?, ?, 'country_leader', ?, 1)",
                (
                    spec["leader_username"],
                    hash_password(spec["leader_password"]),
                    country_id,
                ),
            )

        countries = get_countries(world_id, conn=conn)
        for c_from in countries:
            for c_to in countries:
                if c_from["id"] == c_to["id"]:
                    continue
                conn.execute(
                    "INSERT INTO tariffs (world_id, from_country_id, to_country_id, tariff_rate) VALUES (?, ?, ?, 0.0)",
                    (world_id, c_from["id"], c_to["id"]),
                )

        snapshot_initial_period(conn, world_id, period_no=0)
        return world_id



def snapshot_initial_period(conn: sqlite3.Connection, world_id: int, period_no: int) -> None:
    countries = get_countries(world_id, conn=conn)
    states = get_current_states(world_id, conn=conn)
    tariffs = get_tariffs(world_id, conn=conn)
    created_at = dt_to_str(utcnow())
    for country in countries:
        cid = country["id"]
        state = states[cid]
        payload_state = {
            k: state[k]
            for k in [
                "fx_index",
                "gov_spending",
                "vat_rate",
                "public_employment",
                "price_index",
                "inflation",
                "unemployment",
                "consumption",
                "exports",
                "imports",
                "political_support",
                "deficit_ratio",
                "card_status",
                "required_gov_delta_next",
            ]
        }
        payload_state["avg_tariff_imposed"] = 0.0
        payload_state["avg_tariff_against"] = 0.0
        payload_state["q_gap_pct"] = 0.0
        payload_state["tariffs_out"] = tariffs.get(cid, {})
        applied_policy = {
            "fx_delta": 0.0,
            "gov_delta": 0.0,
            "vat_delta": 0.0,
            "public_emp_delta": 0.0,
            "tariff_changes": {},
        }
        conn.execute(
            "INSERT OR REPLACE INTO period_results (world_id, period_no, country_id, applied_policy_json, state_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (world_id, period_no, cid, json.dumps(applied_policy, ensure_ascii=False), json.dumps(payload_state, ensure_ascii=False), created_at),
        )


# ---------- envíos ----------
def upsert_submission(
    *,
    world_id: int,
    period_no: int,
    country_id: int,
    submitted_by_user_id: int,
    fx_delta: float,
    gov_delta: float,
    vat_delta: float,
    public_emp_delta: float,
    tariff_changes: Dict[int, float],
) -> None:
    with transaction() as conn:
        existing = conn.execute(
            "SELECT id FROM submissions WHERE world_id = ? AND period_no = ? AND country_id = ?",
            (world_id, period_no, country_id),
        ).fetchone()
        now_str = dt_to_str(utcnow())
        if existing is None:
            cur = conn.execute(
                """
                INSERT INTO submissions (
                    world_id, period_no, country_id, submitted_by_user_id, submitted_at,
                    fx_delta, gov_delta, vat_delta, public_emp_delta
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    world_id,
                    period_no,
                    country_id,
                    submitted_by_user_id,
                    now_str,
                    fx_delta,
                    gov_delta,
                    vat_delta,
                    public_emp_delta,
                ),
            )
            submission_id = int(cur.lastrowid)
        else:
            submission_id = int(existing["id"])
            conn.execute(
                """
                UPDATE submissions
                   SET submitted_by_user_id = ?, submitted_at = ?, fx_delta = ?, gov_delta = ?,
                       vat_delta = ?, public_emp_delta = ?
                 WHERE id = ?
                """,
                (
                    submitted_by_user_id,
                    now_str,
                    fx_delta,
                    gov_delta,
                    vat_delta,
                    public_emp_delta,
                    submission_id,
                ),
            )
            conn.execute("DELETE FROM submission_tariffs WHERE submission_id = ?", (submission_id,))

        for to_country_id, delta in tariff_changes.items():
            conn.execute(
                "INSERT INTO submission_tariffs (submission_id, to_country_id, tariff_delta) VALUES (?, ?, ?)",
                (submission_id, int(to_country_id), float(delta)),
            )



def clear_submission(world_id: int, period_no: int, country_id: int) -> None:
    with transaction() as conn:
        row = conn.execute(
            "SELECT id FROM submissions WHERE world_id = ? AND period_no = ? AND country_id = ?",
            (world_id, period_no, country_id),
        ).fetchone()
        if row:
            conn.execute("DELETE FROM submission_tariffs WHERE submission_id = ?", (row["id"],))
            conn.execute("DELETE FROM submissions WHERE id = ?", (row["id"],))


# ---------- finalización de período ----------
def _all_countries_submitted(conn: sqlite3.Connection, world_id: int, period_no: int) -> bool:
    total = conn.execute("SELECT COUNT(*) FROM countries WHERE world_id = ?", (world_id,)).fetchone()[0]
    sent = conn.execute(
        "SELECT COUNT(*) FROM submissions WHERE world_id = ? AND period_no = ?",
        (world_id, period_no),
    ).fetchone()[0]
    return int(sent) >= int(total)



def maybe_auto_finalize(world_id: int) -> bool:
    with transaction(immediate=True) as conn:
        world = get_active_world(conn)
        if world is None or int(world["id"]) != int(world_id):
            return False
        if world["status"] != "running":
            return False
        deadline = str_to_dt(world["deadline_at"])
        expired = deadline is not None and utcnow() >= deadline
        everyone_ready = bool(world["auto_advance_when_all_submitted"]) and _all_countries_submitted(
            conn, world_id, int(world["current_period"])
        )
        if not expired and not everyone_ready:
            return False
        _finalize_period_locked(conn, world)
        return True



def force_finalize(world_id: int) -> bool:
    with transaction(immediate=True) as conn:
        world = get_active_world(conn)
        if world is None or int(world["id"]) != int(world_id):
            return False
        if world["status"] != "running":
            return False
        _finalize_period_locked(conn, world)
        return True



def _finalize_period_locked(conn: sqlite3.Connection, world: Dict[str, Any]) -> None:
    world_id = int(world["id"])
    period_no = int(world["current_period"])
    settings = world["settings"]
    countries = get_countries(world_id, conn=conn)
    states = get_current_states(world_id, conn=conn)
    tariffs = get_tariffs(world_id, conn=conn)
    submissions = get_submissions_for_period(world_id, period_no, conn=conn)

    default_policies: Dict[int, Dict[str, Any]] = {}
    for country in countries:
        cid = int(country["id"])
        submission = submissions.get(cid)
        if submission is None:
            default_policies[cid] = {
                "fx_delta": 0.0,
                "gov_delta": 0.0,
                "vat_delta": 0.0,
                "public_emp_delta": 0.0,
                "tariff_changes": {},
            }
        else:
            default_policies[cid] = {
                "fx_delta": float(submission["fx_delta"]),
                "gov_delta": float(submission["gov_delta"]),
                "vat_delta": float(submission["vat_delta"]),
                "public_emp_delta": float(submission["public_emp_delta"]),
                "tariff_changes": submission.get("tariff_changes", {}),
            }

        required_cut = states[cid].get("required_gov_delta_next")
        if required_cut is not None and float(default_policies[cid]["gov_delta"]) > float(required_cut):
            default_policies[cid]["gov_delta"] = float(required_cut)

    next_states = compute_next_period(
        countries=countries,
        current_states=states,
        tariffs=tariffs,
        policies=default_policies,
        settings=settings,
    )

    created_at = dt_to_str(utcnow())
    # Persistir estados y snapshots.
    for item in next_states:
        cid = int(item["country_id"])
        conn.execute(
            """
            UPDATE country_states
               SET fx_index = ?, gov_spending = ?, vat_rate = ?, public_employment = ?,
                   price_index = ?, inflation = ?, unemployment = ?, consumption = ?,
                   exports = ?, imports = ?, political_support = ?, deficit_ratio = ?,
                   card_status = ?, required_gov_delta_next = ?
             WHERE country_id = ?
            """,
            (
                item["fx_index"],
                item["gov_spending"],
                item["vat_rate"],
                item["public_employment"],
                item["price_index"],
                item["inflation"],
                item["unemployment"],
                item["consumption"],
                item["exports"],
                item["imports"],
                item["political_support"],
                item["deficit_ratio"],
                item["card_status"],
                item["required_gov_delta_next"],
                cid,
            ),
        )

        for to_country_id, tariff_value in item["tariffs_out"].items():
            conn.execute(
                "UPDATE tariffs SET tariff_rate = ? WHERE world_id = ? AND from_country_id = ? AND to_country_id = ?",
                (tariff_value, world_id, cid, int(to_country_id)),
            )

        state_payload = {
            key: item[key]
            for key in [
                "fx_index",
                "gov_spending",
                "vat_rate",
                "public_employment",
                "price_index",
                "inflation",
                "unemployment",
                "consumption",
                "exports",
                "imports",
                "political_support",
                "deficit_ratio",
                "card_status",
                "required_gov_delta_next",
                "avg_tariff_imposed",
                "avg_tariff_against",
                "q_gap_pct",
            ]
        }
        state_payload["tariffs_out"] = item["tariffs_out"]
        conn.execute(
            "INSERT OR REPLACE INTO period_results (world_id, period_no, country_id, applied_policy_json, state_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (
                world_id,
                period_no,
                cid,
                json.dumps(item["applied_policy"], ensure_ascii=False),
                json.dumps(state_payload, ensure_ascii=False),
                created_at,
            ),
        )

    new_period = period_no + 1
    if new_period > int(world["total_periods"]):
        conn.execute(
            "UPDATE worlds SET status = 'finished', current_period = ?, deadline_at = NULL WHERE id = ?",
            (new_period, world_id),
        )
    else:
        next_duration = _duration_for_period(settings, new_period, int(world["period_duration_minutes"]))
        new_deadline = utcnow() + timedelta(minutes=int(next_duration))
        conn.execute(
            "UPDATE worlds SET current_period = ?, period_duration_minutes = ?, deadline_at = ? WHERE id = ?",
            (new_period, int(next_duration), dt_to_str(new_deadline), world_id),
        )


# ---------- historial y paneles ----------
def get_period_results(world_id: int) -> List[Dict[str, Any]]:
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT pr.period_no, c.name, pr.applied_policy_json, pr.state_json, pr.created_at
            FROM period_results pr
            JOIN countries c ON c.id = pr.country_id
            WHERE pr.world_id = ?
            ORDER BY pr.period_no, c.sort_order
            """,
            (world_id,),
        ).fetchall()
        out = []
        for row in rows:
            out.append(
                {
                    "period_no": row["period_no"],
                    "country": row["name"],
                    "applied_policy": json.loads(row["applied_policy_json"]),
                    "state": json.loads(row["state_json"]),
                    "created_at": row["created_at"],
                }
            )
        return out
    finally:
        conn.close()



def get_past_policy_table(world_id: int, max_period: int | None = None) -> List[Dict[str, Any]]:
    conn = get_connection()
    try:
        country_names = get_country_name_map(world_id, conn=conn)
        if max_period is None:
            rows = conn.execute(
                """
                SELECT s.id, s.period_no, c.name AS country, s.fx_delta, s.gov_delta, s.vat_delta, s.public_emp_delta
                FROM submissions s
                JOIN countries c ON c.id = s.country_id
                WHERE s.world_id = ?
                ORDER BY s.period_no, c.sort_order
                """,
                (world_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT s.id, s.period_no, c.name AS country, s.fx_delta, s.gov_delta, s.vat_delta, s.public_emp_delta
                FROM submissions s
                JOIN countries c ON c.id = s.country_id
                WHERE s.world_id = ? AND s.period_no <= ?
                ORDER BY s.period_no, c.sort_order
                """,
                (world_id, int(max_period)),
            ).fetchall()
        table = []
        for row in rows:
            trows = conn.execute(
                "SELECT to_country_id, tariff_delta FROM submission_tariffs WHERE submission_id = ? ORDER BY to_country_id",
                (row["id"],),
            ).fetchall()
            tariffs_txt = ", ".join(
                f"{country_names[int(tr['to_country_id'])]}: {float(tr['tariff_delta']):+.1f} pp"
                for tr in trows
                if abs(float(tr["tariff_delta"])) > 1e-9
            )
            table.append(
                {
                    "Período": int(row["period_no"]),
                    "País": row["country"],
                    "Δ tipo de cambio (%)": float(row["fx_delta"]),
                    "Δ gasto (%)": float(row["gov_delta"]),
                    "Δ IVA (pp)": float(row["vat_delta"]),
                    "Δ empleo público (%)": float(row["public_emp_delta"]),
                    "Aranceles": tariffs_txt or "Sin cambios",
                }
            )
        return table
    finally:
        conn.close()


def update_world_settings(
    world_id: int,
    *,
    settings: Dict[str, Any] | None = None,
    total_periods: int | None = None,
    period_duration_minutes: int | None = None,
    auto_advance_when_all_submitted: bool | None = None,
) -> None:
    with transaction() as conn:
        world = get_active_world(conn)
        if world is None or int(world["id"]) != int(world_id):
            return
        new_settings = settings or world["settings"]
        effective_duration = period_duration_minutes
        if settings is not None:
            current_period = int(world["current_period"])
            effective_duration = _duration_for_period(
                new_settings,
                current_period,
                period_duration_minutes or int(world["period_duration_minutes"]),
            )
        conn.execute(
            """
            UPDATE worlds
               SET settings_json = ?,
                   total_periods = COALESCE(?, total_periods),
                   period_duration_minutes = COALESCE(?, period_duration_minutes),
                   auto_advance_when_all_submitted = COALESCE(?, auto_advance_when_all_submitted)
             WHERE id = ?
            """,
            (
                json.dumps(new_settings, ensure_ascii=False),
                total_periods,
                effective_duration,
                (1 if auto_advance_when_all_submitted else 0) if auto_advance_when_all_submitted is not None else None,
                world_id,
            ),
        )


def impose_required_cut(country_id: int, required_gov_delta_next: float | None) -> None:
    with transaction() as conn:
        conn.execute(
            "UPDATE country_states SET required_gov_delta_next = ? WHERE country_id = ?",
            (required_gov_delta_next, country_id),
        )
