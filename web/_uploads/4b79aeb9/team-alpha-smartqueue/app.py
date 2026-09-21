"""SmartQueue: minimal clinic queue management backend."""
from flask import Flask, request, jsonify
from dataclasses import dataclass, field
from datetime import datetime
import sqlite3

app = Flask(__name__)
DB_PATH = "queue.db"


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                patient_phone TEXT NOT NULL,
                checked_in_at TEXT NOT NULL,
                seen INTEGER DEFAULT 0
            )
        """)


@app.route("/checkin", methods=["POST"])
def checkin():
    phone = request.form.get("phone")
    if not phone:
        return jsonify({"error": "phone number required"}), 400
    with get_db() as conn:
        conn.execute(
            "INSERT INTO queue (patient_phone, checked_in_at) VALUES (?, ?)",
            (phone, datetime.utcnow().isoformat()),
        )
    return jsonify({"status": "checked in"}), 201


@app.route("/position/<phone>", methods=["GET"])
def position(phone):
    with get_db() as conn:
        row = conn.execute(
            "SELECT id FROM queue WHERE patient_phone = ? AND seen = 0",
            (phone,),
        ).fetchone()
        if not row:
            return jsonify({"error": "not found"}), 404
        ahead = conn.execute(
            "SELECT COUNT(*) as c FROM queue WHERE id < ? AND seen = 0",
            (row["id"],),
        ).fetchone()["c"]
    est_minutes = ahead * 5
    return jsonify({"position": ahead + 1, "estimated_wait_minutes": est_minutes})


@app.route("/next", methods=["POST"])
def mark_next_seen():
    with get_db() as conn:
        row = conn.execute(
            "SELECT id FROM queue WHERE seen = 0 ORDER BY id LIMIT 1"
        ).fetchone()
        if not row:
            return jsonify({"error": "queue empty"}), 404
        conn.execute("UPDATE queue SET seen = 1 WHERE id = ?", (row["id"],))
    return jsonify({"status": "marked seen", "id": row["id"]})


if __name__ == "__main__":
    init_db()
    app.run(debug=True)
