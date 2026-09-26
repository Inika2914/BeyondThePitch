"""
SQLite persistence for Beyond The Pitch's web layer.

This replaces the in-memory SUBMISSIONS dict that used to live in
web/app.py. It's intentionally a thin repository: it stores/loads the
same shapes app.py already worked with (SubmissionBundle, EvaluationResult),
it just backs them with a file instead of a process-lifetime dict.

adapters/, models/, and scoring/ are untouched — same as the README's
"swap in a database" note describes. Only this file and web/app.py change.

The DB file lives at web/data.db by default; delete it to reset all
stored submissions (or call reset_db() from a Python shell).
"""

import json
import sqlite3
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from models.submission import SubmissionBundle, CodeFile, DemoAsset
from scoring.engine import EvaluationResult, DimensionScore

DB_PATH = Path(__file__).parent / "data.db"


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    """Create tables if they don't exist yet. Safe to call on every
    app startup."""
    with _connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS submissions (
                id TEXT PRIMARY KEY,
                team_name TEXT NOT NULL,
                project_title TEXT NOT NULL,
                status TEXT NOT NULL,
                error TEXT,
                bundle_json TEXT NOT NULL,
                result_json TEXT,
                code_file_count INTEGER NOT NULL DEFAULT 0,
                has_readme INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)

        # --- jury logins & synchronized dashboards ---------------------

        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                salt TEXT NOT NULL,
                role TEXT NOT NULL CHECK (role IN ('admin', 'jury')),
                display_name TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)

        # Singleton row (id always 1) holding event-wide judging config.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS event_config (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                judging_mode TEXT NOT NULL DEFAULT 'all' CHECK (judging_mode IN ('split', 'all'))
            )
        """)
        conn.execute("""
            INSERT OR IGNORE INTO event_config (id, judging_mode) VALUES (1, 'all')
        """)

        # Which jury is responsible for which submission. Only consulted
        # in 'split' mode — in 'all' mode every jury is implicitly
        # assigned to every submission.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS assignments (
                submission_id TEXT NOT NULL REFERENCES submissions(id) ON DELETE CASCADE,
                jury_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                PRIMARY KEY (submission_id, jury_id)
            )
        """)

        # Each jury's own independent score for a submission.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS jury_scores (
                submission_id TEXT NOT NULL REFERENCES submissions(id) ON DELETE CASCADE,
                jury_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                dimension_scores_json TEXT NOT NULL,
                weighted_total REAL NOT NULL,
                comment TEXT NOT NULL DEFAULT '',
                judge_flags_json TEXT NOT NULL DEFAULT '[]',
                submitted_at TEXT NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY (submission_id, jury_id)
            )
        """)

        # Admin override of the normal reveal-when-everyone's-in logic.
        # Presence of a row = forced open; absence = auto (computed from
        # assignments/jury_scores completeness).
        conn.execute("""
            CREATE TABLE IF NOT EXISTS reveals (
                submission_id TEXT PRIMARY KEY REFERENCES submissions(id) ON DELETE CASCADE,
                revealed_by TEXT,
                revealed_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)


def reset_db() -> None:
    """Drop and recreate the table — wipes every stored submission."""
    with _connect() as conn:
        conn.execute("DROP TABLE IF EXISTS submissions")
    init_db()


# --- dataclass <-> JSON helpers -----------------------------------
#
# dataclasses.asdict() handles the serialize direction for free, but
# reconstructing nested dataclasses (CodeFile, DemoAsset, DimensionScore)
# back from plain dicts needs a little help — dataclasses don't do this
# automatically.

def _bundle_to_json(bundle: SubmissionBundle) -> str:
    return json.dumps(asdict(bundle))


def _bundle_from_json(raw: str) -> SubmissionBundle:
    d = json.loads(raw)
    d["code_files"] = [CodeFile(**f) for f in d.get("code_files", [])]
    d["demo"] = DemoAsset(**d["demo"]) if d.get("demo") else DemoAsset(kind="none")
    return SubmissionBundle(**d)


def _result_to_json(result: EvaluationResult) -> str:
    return json.dumps(asdict(result))


def _result_from_json(raw: str) -> EvaluationResult:
    d = json.loads(raw)
    d["dimension_scores"] = [DimensionScore(**s) for s in d.get("dimension_scores", [])]
    return EvaluationResult(**d)


def _row_to_record(row: sqlite3.Row) -> dict:
    return {
        "team_name": row["team_name"],
        "project_title": row["project_title"],
        "status": row["status"],
        "error": row["error"],
        "bundle": _bundle_from_json(row["bundle_json"]),
        "result": _result_from_json(row["result_json"]) if row["result_json"] else None,
        "code_file_count": row["code_file_count"],
        "has_readme": bool(row["has_readme"]),
    }


# --- public repository API ----------------------------------------

def create_submission(
    sid: str,
    team_name: str,
    project_title: str,
    bundle: SubmissionBundle,
    status: str,
    code_file_count: int,
    has_readme: bool,
) -> None:
    with _connect() as conn:
        conn.execute(
            """INSERT INTO submissions
               (id, team_name, project_title, status, error, bundle_json,
                result_json, code_file_count, has_readme)
               VALUES (?, ?, ?, ?, NULL, ?, NULL, ?, ?)""",
            (sid, team_name, project_title, status, _bundle_to_json(bundle),
             code_file_count, int(has_readme)),
        )


def get_submission(sid: str) -> Optional[dict]:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM submissions WHERE id = ?", (sid,)).fetchone()
    return _row_to_record(row) if row else None


def list_submissions() -> list[tuple[str, dict]]:
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM submissions ORDER BY created_at ASC").fetchall()
    return [(row["id"], _row_to_record(row)) for row in rows]


def set_status(sid: str, status: str, error: Optional[str] = None) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE submissions SET status = ?, error = ? WHERE id = ?",
            (status, error, sid),
        )


def save_result(sid: str, result: EvaluationResult) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE submissions SET result_json = ?, status = 'scored', error = NULL WHERE id = ?",
            (_result_to_json(result), sid),
        )


def delete_submission(sid: str) -> bool:
    with _connect() as conn:
        cur = conn.execute("DELETE FROM submissions WHERE id = ?", (sid,))
        return cur.rowcount > 0


# --- users & sessions -----------------------------------------------

def create_user(uid: str, username: str, password_hash: str, salt: str,
                 role: str, display_name: str) -> None:
    with _connect() as conn:
        conn.execute(
            """INSERT INTO users (id, username, password_hash, salt, role, display_name)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (uid, username, password_hash, salt, role, display_name),
        )


def get_user(uid: str) -> Optional[dict]:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
    return dict(row) if row else None


def get_user_by_username(username: str) -> Optional[dict]:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    return dict(row) if row else None


def list_users(role: Optional[str] = None) -> list[dict]:
    with _connect() as conn:
        if role:
            rows = conn.execute(
                "SELECT * FROM users WHERE role = ? ORDER BY created_at ASC", (role,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM users ORDER BY created_at ASC").fetchall()
    return [dict(r) for r in rows]


def count_users() -> int:
    with _connect() as conn:
        return conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]


def delete_user(uid: str) -> bool:
    with _connect() as conn:
        cur = conn.execute("DELETE FROM users WHERE id = ?", (uid,))
        return cur.rowcount > 0


def create_session(token: str, user_id: str) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT INTO sessions (token, user_id) VALUES (?, ?)", (token, user_id)
        )


def get_user_by_session(token: str) -> Optional[dict]:
    with _connect() as conn:
        row = conn.execute(
            """SELECT users.* FROM sessions
               JOIN users ON users.id = sessions.user_id
               WHERE sessions.token = ?""",
            (token,),
        ).fetchone()
    return dict(row) if row else None


def delete_session(token: str) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM sessions WHERE token = ?", (token,))


def delete_sessions_for_user(uid: str) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (uid,))


# --- event config -----------------------------------------------------

def get_judging_mode() -> str:
    with _connect() as conn:
        row = conn.execute("SELECT judging_mode FROM event_config WHERE id = 1").fetchone()
    return row["judging_mode"] if row else "all"


def set_judging_mode(mode: str) -> None:
    with _connect() as conn:
        conn.execute("UPDATE event_config SET judging_mode = ? WHERE id = 1", (mode,))


# --- assignments -------------------------------------------------------

def set_assignment(submission_id: str, jury_ids: list[str]) -> None:
    """Replace the full assignment list for one submission."""
    with _connect() as conn:
        conn.execute("DELETE FROM assignments WHERE submission_id = ?", (submission_id,))
        conn.executemany(
            "INSERT INTO assignments (submission_id, jury_id) VALUES (?, ?)",
            [(submission_id, jid) for jid in jury_ids],
        )


def add_assignment(submission_id: str, jury_id: str) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO assignments (submission_id, jury_id) VALUES (?, ?)",
            (submission_id, jury_id),
        )


def get_assigned_juries(submission_id: str) -> list[str]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT jury_id FROM assignments WHERE submission_id = ?", (submission_id,)
        ).fetchall()
    return [r["jury_id"] for r in rows]


def get_assignments_for_jury(jury_id: str) -> list[str]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT submission_id FROM assignments WHERE jury_id = ?", (jury_id,)
        ).fetchall()
    return [r["submission_id"] for r in rows]


def clear_all_assignments() -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM assignments")


# --- jury scores --------------------------------------------------------

def upsert_jury_score(submission_id: str, jury_id: str, dimension_scores: list[dict],
                       weighted_total: float, comment: str, judge_flags: list[str]) -> None:
    with _connect() as conn:
        conn.execute(
            """INSERT INTO jury_scores
                   (submission_id, jury_id, dimension_scores_json, weighted_total,
                    comment, judge_flags_json, submitted_at)
               VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
               ON CONFLICT(submission_id, jury_id) DO UPDATE SET
                   dimension_scores_json = excluded.dimension_scores_json,
                   weighted_total = excluded.weighted_total,
                   comment = excluded.comment,
                   judge_flags_json = excluded.judge_flags_json,
                   submitted_at = datetime('now')""",
            (submission_id, jury_id, json.dumps(dimension_scores), weighted_total,
             comment, json.dumps(judge_flags)),
        )


def get_jury_score(submission_id: str, jury_id: str) -> Optional[dict]:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM jury_scores WHERE submission_id = ? AND jury_id = ?",
            (submission_id, jury_id),
        ).fetchone()
    return _jury_score_row_to_dict(row) if row else None


def get_jury_scores_for_submission(submission_id: str) -> dict[str, dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM jury_scores WHERE submission_id = ?", (submission_id,)
        ).fetchall()
    return {r["jury_id"]: _jury_score_row_to_dict(r) for r in rows}


def get_all_jury_scores() -> list[dict]:
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM jury_scores").fetchall()
    return [_jury_score_row_to_dict(r) for r in rows]


def _jury_score_row_to_dict(row: sqlite3.Row) -> dict:
    return {
        "submission_id": row["submission_id"],
        "jury_id": row["jury_id"],
        "dimension_scores": json.loads(row["dimension_scores_json"]),
        "weighted_total": row["weighted_total"],
        "comment": row["comment"],
        "judge_flags": json.loads(row["judge_flags_json"]),
        "submitted_at": row["submitted_at"],
    }


# --- reveals -------------------------------------------------------------

def force_reveal(submission_id: str, admin_id: str) -> None:
    with _connect() as conn:
        conn.execute(
            """INSERT INTO reveals (submission_id, revealed_by, revealed_at)
               VALUES (?, ?, datetime('now'))
               ON CONFLICT(submission_id) DO UPDATE SET
                   revealed_by = excluded.revealed_by, revealed_at = datetime('now')""",
            (submission_id, admin_id),
        )


def clear_force_reveal(submission_id: str) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM reveals WHERE submission_id = ?", (submission_id,))


def is_force_revealed(submission_id: str) -> bool:
    with _connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM reveals WHERE submission_id = ?", (submission_id,)
        ).fetchone()
    return row is not None
