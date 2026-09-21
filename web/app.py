"""
Beyond The Pitch web backend.

This is purely a new interface layer on top of the existing engine —
adapters/, models/, and scoring/ are untouched. The web app:
  1. accepts a submission (zip upload + form metadata),
  2. runs it through FolderAdapter -> SubmissionBundle (unchanged),
  3. runs scoring.engine.evaluate() (unchanged),
  4. stores the result in memory,
  5. serves it to the dashboard as JSON, and recomputes the calibrated
     leaderboard via scoring.aggregator.calibrate_pool() (unchanged)
     whenever more than one submission has been scored.

In-memory storage is intentional for a hackathon-day tool — see README
for the swap-in-a-database note if this needs to persist across restarts.
"""

import io
import sys
import uuid
import zipfile
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, UploadFile, Form, File, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).parent.parent))  # repo root on path

from adapters.folder_adapter import FolderAdapter
from scoring.engine import evaluate, EvaluationResult
from scoring.aggregator import calibrate_pool
from scoring import rubrics

app = FastAPI(title="Beyond The Pitch")

UPLOAD_ROOT = Path(__file__).parent / "_uploads"
UPLOAD_ROOT.mkdir(exist_ok=True)

# In-memory store: submission_id -> record dict
# record = {meta..., "status": "pending"|"scoring"|"scored"|"error",
#           "result": EvaluationResult | None, "error": str | None}
SUBMISSIONS: dict[str, dict] = {}


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

    SUBMISSIONS[sid] = {
        "team_name": team_name,
        "project_title": project_title,
        "bundle": bundle,
        "status": "ingested",
        "result": None,
        "error": None,
        "code_file_count": len(bundle.code_files),
        "has_readme": bool(bundle.readme_text),
    }
    return _submission_summary(sid, SUBMISSIONS[sid]) | {
        "code_file_count": len(bundle.code_files),
        "has_readme": bool(bundle.readme_text),
    }


@app.post("/api/submissions/{sid}/evaluate")
def evaluate_submission(sid: str):
    """Run the (unchanged) scoring engine against this submission's
    bundle. Synchronous — fine for hackathon-day submission volumes."""
    record = SUBMISSIONS.get(sid)
    if not record:
        raise HTTPException(404, "Submission not found.")

    record["status"] = "scoring"
    try:
        result = evaluate(record["bundle"])
        record["result"] = result
        record["status"] = "scored"
        record["error"] = None
    except Exception as e:
        record["status"] = "error"
        record["error"] = str(e)
        raise HTTPException(500, f"Scoring failed: {e}")

    return submission_detail(sid)


@app.get("/api/submissions")
def list_submissions():
    return [_submission_summary(sid, r) for sid, r in SUBMISSIONS.items()]


@app.get("/api/submissions/{sid}")
def submission_detail(sid: str):
    record = SUBMISSIONS.get(sid)
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
def leaderboard():
    scored = [
        (sid, r["result"]) for sid, r in SUBMISSIONS.items()
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
def delete_submission(sid: str):
    if sid not in SUBMISSIONS:
        raise HTTPException(404, "Submission not found.")
    del SUBMISSIONS[sid]
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
def get_rubric_weights():
    return {
        "weights": rubrics.get_weights(),
        "defaults": rubrics.get_default_weights(),
        "dimensions": [
            {"key": d.key, "label": d.label} for d in rubrics.DIMENSIONS
        ],
    }


@app.put("/api/config/weights")
def update_rubric_weights(payload: WeightsUpdate):
    try:
        updated = rubrics.set_weights(payload.weights)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"weights": updated}


@app.post("/api/config/weights/reset")
def reset_rubric_weights():
    return {"weights": rubrics.reset_weights()}


# --- Static frontend -------------------------------------------------

static_dir = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.get("/")
def index():
    return FileResponse(static_dir / "index.html")
