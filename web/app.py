"""
Beyond The Pitch web backend.

This is purely a new interface layer on top of the existing engine —
adapters/, models/, and scoring/ are untouched. The web app:
  1. accepts a submission (zip upload + form metadata),
  2. runs it through FolderAdapter -> SubmissionBundle (unchanged),
  3. runs scoring.engine.evaluate() (unchanged),
  4. stores the result in SQLite (web/data.db, via web/db.py), and
     serves it to the dashboard as JSON, and recomputes the calibrated
     leaderboard via scoring.aggregator.calibrate_pool() (unchanged)
     whenever more than one submission has been scored.

Storage lives in web/db.py — a thin SQLite repository — so submissions
and scores survive a server restart. Delete web/data.db to reset.
"""

import io
import json
import sys
import uuid
import zipfile
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, UploadFile, Form, File, HTTPException, Request, Response
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).parent.parent))  # repo root on path

from adapters.folder_adapter import FolderAdapter
from scoring.engine import evaluate, evaluate_stream, EvaluationResult
from scoring.aggregator import calibrate_pool
from scoring import rubrics
from web import auth, db, jury_scoring

app = FastAPI(title="Beyond The Pitch")

UPLOAD_ROOT = Path(__file__).parent / "_uploads"
UPLOAD_ROOT.mkdir(exist_ok=True)

db.init_db()
auth.seed_admin_if_needed()

# Persistence now lives in web/db.py (SQLite, web/data.db) instead of an
# in-memory dict — submissions and scores survive a server restart.
# Every place that used to do SUBMISSIONS[sid] now goes through db.*.


def _submission_summary(sid: str, record: dict) -> dict:
    result: Optional[EvaluationResult] = record.get("result")
    return {
        "id": sid,
        "team_name": record["team_name"],
        "project_title": record["project_title"],
        "status": record["status"],
        "error": record.get("error"),
        "weighted_total": result.weighted_total if result else None,
    }


@app.post("/api/submissions")
async def create_submission(
    team_name: str = Form(...),
    project_title: str = Form(...),
    problem_statement: str = Form(""),
    business_impact_pitch: str = Form(""),
    tech_stack: str = Form(""),
    demo_kind: str = Form("none"),
    demo_value: str = Form(""),
    demo_notes: str = Form(""),
    code_zip: Optional[UploadFile] = File(None),
    admin=Depends(auth.require_admin),
):
    """Create a submission from form fields + an optional zip of the
    team's code folder. Ingests immediately; scoring is a separate call
    so the UI can show 'ingested' before the (slower) LLM pass runs."""
    sid = str(uuid.uuid4())[:8]
    submission_dir = UPLOAD_ROOT / sid

    if code_zip is not None and code_zip.filename:
        submission_dir.mkdir(parents=True, exist_ok=True)
        raw = await code_zip.read()
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as zf:
                zf.extractall(submission_dir)
        except zipfile.BadZipFile:
            raise HTTPException(400, "Uploaded file is not a valid zip archive.")
        # If the zip contains a single top-level folder, ingest from there
        # so README/code are found at the expected relative paths.
        entries = [p for p in submission_dir.iterdir() if not p.name.startswith("__MACOSX")]
        folder_path = str(entries[0]) if len(entries) == 1 and entries[0].is_dir() else str(submission_dir)
    else:
        submission_dir.mkdir(parents=True, exist_ok=True)
        folder_path = str(submission_dir)

    try:
        bundle = FolderAdapter().ingest(
            folder_path=folder_path,
            team_name=team_name,
            project_title=project_title,
            problem_statement=problem_statement,
            business_impact_pitch=business_impact_pitch,
            tech_stack=tech_stack,
            demo_kind=demo_kind,
            demo_value=demo_value or None,
            demo_notes=demo_notes or None,
        )
    except Exception as e:
        raise HTTPException(400, f"Ingestion failed: {e}")

    db.create_submission(
        sid, team_name, project_title, bundle,
        status="ingested",
        code_file_count=len(bundle.code_files),
        has_readme=bool(bundle.readme_text),
    )
    record = db.get_submission(sid)
    return _submission_summary(sid, record) | {
        "code_file_count": len(bundle.code_files),
        "has_readme": bool(bundle.readme_text),
    }


@app.post("/api/submissions/{sid}/evaluate")
def evaluate_submission(sid: str, admin=Depends(auth.require_admin)):
    """Run the (unchanged) scoring engine against this submission's
    bundle. Synchronous — fine for hackathon-day submission volumes."""
    record = db.get_submission(sid)
    if not record:
        raise HTTPException(404, "Submission not found.")

    db.set_status(sid, "scoring")
    try:
        result = evaluate(record["bundle"])
        db.save_result(sid, result)
    except Exception as e:
        db.set_status(sid, "error", str(e))
        raise HTTPException(500, f"Scoring failed: {e}")

    return submission_detail(sid)


@app.post("/api/submissions/{sid}/evaluate/stream")
def evaluate_submission_stream(sid: str, admin=Depends(auth.require_admin)):
    """Same scoring pass as /evaluate, but streamed as Server-Sent Events
    so the UI can show live progress ('Scoring Code Quality…', etc.)
    instead of a single blocking spinner. Each dimension call to the LLM
    takes a few seconds; this surfaces that instead of hiding it."""
    record = db.get_submission(sid)
    if not record:
        raise HTTPException(404, "Submission not found.")

    def _sse(event: dict) -> str:
        return f"data: {json.dumps(event)}\n\n"

    def _gen():
        db.set_status(sid, "scoring")
        try:
            for event in evaluate_stream(record["bundle"]):
                if event["stage"] == "dimension":
                    payload = {
                        "stage": "dimension",
                        "status": event["status"],
                        "key": event["key"],
                        "label": event["label"],
                    }
                    if event["status"] == "done":
                        payload["score"] = asdict(event["score"])
                    if event["status"] == "error":
                        payload["error"] = event["error"]
                    yield _sse(payload)
                elif event["stage"] == "summary":
                    payload = {"stage": "summary", "status": event["status"]}
                    if event.get("error"):
                        payload["error"] = event["error"]
                    yield _sse(payload)
                elif event["stage"] == "final":
                    db.save_result(sid, event["result"])
                    yield _sse({
                        "stage": "final",
                        "status": "done",
                        "detail": submission_detail(sid),
                    })
        except Exception as e:
            db.set_status(sid, "error", str(e))
            yield _sse({"stage": "fatal", "status": "error", "error": str(e)})

    return StreamingResponse(
        _gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # disable proxy buffering (e.g. nginx)
        },
    )


@app.get("/api/submissions")
def list_submissions(user=Depends(auth.current_user)):
    items = db.list_submissions()
    if user["role"] == "jury":
        items = [
            (sid, r) for sid, r in items
            if jury_scoring.can_jury_access(sid, user["id"])
        ]
    return [_submission_summary(sid, r) for sid, r in items]


@app.get("/api/submissions/{sid}")
def get_submission_detail(sid: str, user=Depends(auth.current_user)):
    if user["role"] == "jury" and not jury_scoring.can_jury_access(sid, user["id"]):
        raise HTTPException(404, "Submission not found.")
    return submission_detail(sid)


def submission_detail(sid: str) -> dict:
    """Internal helper (also called from the evaluate endpoints, which
    already did their own admin-auth check) — not itself a route."""
    record = db.get_submission(sid)
    if not record:
        raise HTTPException(404, "Submission not found.")

    bundle = record["bundle"]
    result: Optional[EvaluationResult] = record.get("result")

    return {
        "id": sid,
        "team_name": record["team_name"],
        "project_title": record["project_title"],
        "status": record["status"],
        "error": record.get("error"),
        "submission": {
            "problem_statement": bundle.problem_statement,
            "business_impact_pitch": bundle.business_impact_pitch,
            "tech_stack": bundle.tech_stack,
            "readme_present": bool(bundle.readme_text),
            "code_file_count": len(bundle.code_files),
            "demo_kind": bundle.demo.kind,
            "demo_value": bundle.demo.value,
        },
        "result": asdict(result) if result else None,
    }


@app.get("/api/leaderboard")
def leaderboard(user=Depends(auth.current_user)):
    scored = [
        (sid, r["result"]) for sid, r in db.list_submissions()
        if r.get("result") is not None
    ]
    if not scored:
        return []

    results = [r for _, r in scored]
    id_by_team = {r.team_name: sid for sid, r in scored}  # assumes unique team names
    ranked = calibrate_pool(results)

    return [
        {
            "id": id_by_team.get(r.result.team_name),
            "rank": r.rank,
            "percentile": r.percentile,
            "calibrated_total": r.calibrated_total,
            "team_name": r.result.team_name,
            "project_title": r.result.project_title,
            "weighted_total": r.result.weighted_total,
            "judge_flags": r.result.judge_flags,
        }
        for r in ranked
    ]


@app.delete("/api/submissions/{sid}")
def delete_submission(sid: str, admin=Depends(auth.require_admin)):
    if not db.delete_submission(sid):
        raise HTTPException(404, "Submission not found.")
    return {"deleted": sid}


# --- Rubric weight configuration --------------------------------------
#
# Lets judges/organizers reweight dimensions for events where a criterion
# doesn't apply the same way — e.g. an idea-only track with no live demo
# requirement might want Demo Completeness weighted near zero rather than
# penalizing every team equally for something they weren't asked to do.
#
# This reads/writes scoring.rubrics.DIMENSIONS directly (the same list
# scoring/engine.py and scoring/aggregator.py iterate over), so a change
# here takes effect on the next evaluation immediately — no restart.
# Already-scored submissions keep the weighted_total they were scored
# with; re-run "Run evaluation" on them if you want scores reflecting
# the new weights (see the note in scoring/rubrics.py:set_weights).

class WeightsUpdate(BaseModel):
    weights: dict[str, float]


@app.get("/api/config/weights")
def get_rubric_weights(user=Depends(auth.current_user)):
    return {
        "weights": rubrics.get_weights(),
        "defaults": rubrics.get_default_weights(),
        "dimensions": [
            {"key": d.key, "label": d.label} for d in rubrics.DIMENSIONS
        ],
    }


@app.put("/api/config/weights")
def update_rubric_weights(payload: WeightsUpdate, admin=Depends(auth.require_admin)):
    try:
        updated = rubrics.set_weights(payload.weights)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"weights": updated}


@app.post("/api/config/weights/reset")
def reset_rubric_weights(admin=Depends(auth.require_admin)):
    return {"weights": rubrics.reset_weights()}


# --- Auth --------------------------------------------------------------

class LoginPayload(BaseModel):
    username: str
    password: str


@app.post("/api/auth/login")
def login(payload: LoginPayload, response: Response):
    token, user = auth.login(payload.username, payload.password)
    response.set_cookie(
        auth.SESSION_COOKIE, token,
        httponly=True, samesite="lax", max_age=60 * 60 * 24 * 7,
    )
    return user


@app.post("/api/auth/logout")
def logout(request: Request, response: Response):
    auth.logout(request.cookies.get(auth.SESSION_COOKIE))
    response.delete_cookie(auth.SESSION_COOKIE)
    return {"ok": True}


@app.get("/api/auth/me")
def me(user=Depends(auth.current_user)):
    return user


# --- Admin: jury accounts, judging mode, assignment, reveal ------------
#
# Everything here requires an admin session. Admin creates jury accounts
# (there's no public signup), picks the judging mode for the event, and
# controls assignment + blind-reveal overrides.

class JuryAccountCreate(BaseModel):
    username: str
    password: str
    display_name: str = ""


@app.get("/api/admin/juries")
def list_juries(admin=Depends(auth.require_admin)):
    return [
        {"id": u["id"], "username": u["username"], "display_name": u["display_name"]}
        for u in db.list_users(role="jury")
    ]


@app.post("/api/admin/juries")
def create_jury(payload: JuryAccountCreate, admin=Depends(auth.require_admin)):
    user = auth.create_user(payload.username, payload.password, role="jury",
                             display_name=payload.display_name)
    return {"id": user["id"], "username": user["username"], "display_name": user["display_name"]}


@app.delete("/api/admin/juries/{jury_id}")
def delete_jury(jury_id: str, admin=Depends(auth.require_admin)):
    if not db.delete_user(jury_id):
        raise HTTPException(404, "Jury account not found.")
    db.delete_sessions_for_user(jury_id)
    return {"deleted": jury_id}


class ConfigUpdate(BaseModel):
    judging_mode: str


@app.get("/api/admin/config")
def get_config(admin=Depends(auth.require_admin)):
    return {"judging_mode": db.get_judging_mode()}


@app.put("/api/admin/config")
def update_config(payload: ConfigUpdate, admin=Depends(auth.require_admin)):
    if payload.judging_mode not in ("split", "all"):
        raise HTTPException(400, "judging_mode must be 'split' or 'all'.")
    db.set_judging_mode(payload.judging_mode)
    return {"judging_mode": payload.judging_mode}


@app.post("/api/admin/assign/auto-split")
def auto_split_assignments(admin=Depends(auth.require_admin)):
    try:
        assignments = jury_scoring.auto_split_assignments()
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"assignments": assignments}


class AssignmentUpdate(BaseModel):
    jury_ids: list[str]


@app.get("/api/admin/assign/{sid}")
def get_assignment(sid: str, admin=Depends(auth.require_admin)):
    if not db.get_submission(sid):
        raise HTTPException(404, "Submission not found.")
    return {
        "submission_id": sid,
        "assigned_juries": db.get_assigned_juries(sid),
        **jury_scoring.reveal_status(sid),
    }


@app.put("/api/admin/assign/{sid}")
def set_assignment(sid: str, payload: AssignmentUpdate, admin=Depends(auth.require_admin)):
    if not db.get_submission(sid):
        raise HTTPException(404, "Submission not found.")
    db.set_assignment(sid, payload.jury_ids)
    return {"submission_id": sid, "jury_ids": payload.jury_ids}


@app.post("/api/admin/reveal/{sid}")
def admin_force_reveal(sid: str, admin=Depends(auth.require_admin)):
    if not db.get_submission(sid):
        raise HTTPException(404, "Submission not found.")
    db.force_reveal(sid, admin["id"])
    return jury_scoring.reveal_status(sid)


@app.post("/api/admin/unreveal/{sid}")
def admin_undo_force_reveal(sid: str, admin=Depends(auth.require_admin)):
    if not db.get_submission(sid):
        raise HTTPException(404, "Submission not found.")
    db.clear_force_reveal(sid)
    return jury_scoring.reveal_status(sid)


# --- Jury scoring --------------------------------------------------------

class JuryDimensionScore(BaseModel):
    key: str
    score: int
    justification: str = ""
    strengths: list[str] = []
    concerns: list[str] = []


class JuryScoreSubmit(BaseModel):
    dimension_scores: list[JuryDimensionScore]
    comment: str = ""
    judge_flags: list[str] = []


@app.get("/api/submissions/{sid}/jury-score")
def get_jury_score_view(sid: str, user=Depends(auth.current_user)):
    if not db.get_submission(sid):
        raise HTTPException(404, "Submission not found.")
    if user["role"] == "jury" and not jury_scoring.can_jury_access(sid, user["id"]):
        raise HTTPException(404, "Submission not found.")

    visible = jury_scoring.visible_scores_for(sid, user)
    name_by_id = {u["id"]: u["display_name"] for u in db.list_users(role="jury")}

    return {
        "own_score": visible.get(user["id"]) if user["role"] == "jury" else None,
        "scores": [
            {"jury_id": jid, "jury_name": name_by_id.get(jid, jid), **s}
            for jid, s in visible.items()
        ],
        **jury_scoring.reveal_status(sid),
    }


@app.post("/api/submissions/{sid}/jury-score")
def submit_jury_score(sid: str, payload: JuryScoreSubmit, user=Depends(auth.current_user)):
    if user["role"] != "jury":
        raise HTTPException(403, "Only jury accounts submit jury scores.")
    if not db.get_submission(sid):
        raise HTTPException(404, "Submission not found.")
    if not jury_scoring.can_jury_access(sid, user["id"]):
        raise HTTPException(404, "Submission not found.")

    weights = rubrics.get_weights()
    submitted_keys = {d.key for d in payload.dimension_scores}
    missing = set(weights) - submitted_keys
    if missing:
        raise HTTPException(400, f"Missing scores for dimension(s): {sorted(missing)}")
    for d in payload.dimension_scores:
        if not (1 <= d.score <= 10):
            raise HTTPException(400, f"Score for '{d.key}' must be between 1 and 10.")

    total_weight = sum(weights.values())
    weighted_total = round(
        sum(d.score * weights.get(d.key, 0) for d in payload.dimension_scores)
        / (10 * total_weight) * 100,
        1,
    )

    db.upsert_jury_score(
        sid, user["id"],
        dimension_scores=[d.dict() for d in payload.dimension_scores],
        weighted_total=weighted_total,
        comment=payload.comment,
        judge_flags=payload.judge_flags,
    )
    return get_jury_score_view(sid, user)


@app.get("/api/leaderboard/jury")
def jury_leaderboard_route(user=Depends(auth.current_user)):
    return jury_scoring.jury_leaderboard()


# --- Static frontend -------------------------------------------------

static_dir = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.get("/")
def index():
    return FileResponse(static_dir / "index.html")
