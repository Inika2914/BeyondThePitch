"""
Jury-scoring logic: combining independent per-jury scores, blind-reveal
rules, and the jury leaderboard.

Each jury submits their own independent score for a submission (not
shared/averaged live) — see web/db.py's jury_scores table. This module
answers three questions on top of that raw data:

  1. Who is *expected* to score a given submission (depends on judging
     mode — 'split' uses the assignments table, 'all' means every jury
     account)?
  2. Is a submission's jury scoring *revealed* yet — i.e. can juries see
     each other's scores/comments for it? Standard blind-judging rule:
     hidden until everyone expected has submitted, or admin force-reveals
     (web/db.py's reveals table, an explicit override).
  3. What does the *combined* jury view look like for ranking — reusing
     scoring.aggregator.calibrate_pool() (unchanged) by building a
     synthetic EvaluationResult per submission from the average of that
     submission's jury scores.
"""

from typing import Optional

from scoring.aggregator import calibrate_pool
from scoring.engine import DimensionScore, EvaluationResult
from scoring.rubrics import DIMENSIONS
from web import db


def expected_juries(submission_id: str) -> list[str]:
    """Jury user ids expected to score this submission, given the
    current judging mode."""
    mode = db.get_judging_mode()
    if mode == "split":
        return db.get_assigned_juries(submission_id)
    return [u["id"] for u in db.list_users(role="jury")]


def submitted_juries(submission_id: str) -> list[str]:
    return list(db.get_jury_scores_for_submission(submission_id).keys())


def is_revealed(submission_id: str) -> bool:
    """Whether jury scores for this submission are visible cross-jury."""
    if db.is_force_revealed(submission_id):
        return True
    expected = expected_juries(submission_id)
    if not expected:
        return False
    return set(expected).issubset(set(submitted_juries(submission_id)))


def reveal_status(submission_id: str) -> dict:
    expected = expected_juries(submission_id)
    submitted = submitted_juries(submission_id)
    return {
        "expected_juries": expected,
        "submitted_juries": submitted,
        "revealed": is_revealed(submission_id),
        "force_revealed": db.is_force_revealed(submission_id),
    }


def visible_scores_for(submission_id: str, viewer: dict) -> dict[str, dict]:
    """Jury-score dicts this viewer is allowed to see for a submission.

    Admins always see everything. A jury always sees their own score.
    Other juries' scores are included only once the submission is
    revealed."""
    all_scores = db.get_jury_scores_for_submission(submission_id)
    if viewer["role"] == "admin":
        return all_scores
    if is_revealed(submission_id):
        return all_scores
    own = all_scores.get(viewer["id"])
    return {viewer["id"]: own} if own else {}


def can_jury_access(submission_id: str, jury_id: str) -> bool:
    """Split mode: a jury can only see/score submissions assigned to
    them. All mode: every jury can see/score every submission."""
    mode = db.get_judging_mode()
    if mode == "all":
        return True
    return jury_id in db.get_assigned_juries(submission_id)


def auto_split_assignments() -> dict[str, list[str]]:
    """Round-robin every submission across all jury accounts, replacing
    any existing assignments. Returns {submission_id: [jury_id, ...]}."""
    juries = [u["id"] for u in db.list_users(role="jury")]
    submissions = [sid for sid, _ in db.list_submissions()]
    if not juries:
        raise ValueError("Create at least one jury account before auto-splitting.")

    db.clear_all_assignments()
    result: dict[str, list[str]] = {}
    for i, sid in enumerate(submissions):
        jury_id = juries[i % len(juries)]
        db.set_assignment(sid, [jury_id])
        result[sid] = [jury_id]
    return result


def combined_result_for(submission_id: str, record: dict) -> Optional[EvaluationResult]:
    """Average every jury's score for this submission into one synthetic
    EvaluationResult, so it can be fed through the same calibrate_pool()
    used for the AI-engine leaderboard. Returns None if no jury has
    scored it yet."""
    scores = db.get_jury_scores_for_submission(submission_id)
    if not scores:
        return None

    by_dim: dict[str, list[dict]] = {}
    for js in scores.values():
        for ds in js["dimension_scores"]:
            by_dim.setdefault(ds["key"], []).append(ds)

    dimension_scores = []
    for d in DIMENSIONS:
        entries = by_dim.get(d.key)
        if not entries:
            continue
        avg_score = round(sum(e["score"] for e in entries) / len(entries))
        strengths = [s for e in entries for s in e.get("strengths", [])][:5]
        concerns = [c for e in entries for c in e.get("concerns", [])][:5]
        dimension_scores.append(DimensionScore(
            key=d.key,
            label=d.label,
            score=max(1, min(10, avg_score)),
            justification=f"Averaged across {len(entries)} jury score(s).",
            strengths=strengths,
            concerns=concerns,
        ))

    if not dimension_scores:
        return None

    weighted_total = round(sum(js["weighted_total"] for js in scores.values()) / len(scores), 1)
    all_flags = sorted({f for js in scores.values() for f in js["judge_flags"]})

    return EvaluationResult(
        team_name=record["team_name"],
        project_title=record["project_title"],
        dimension_scores=dimension_scores,
        weighted_total=weighted_total,
        top_strengths=[],
        top_improvements=[],
        judge_flags=all_flags,
    )


def jury_leaderboard() -> list[dict]:
    results = []
    id_by_team = {}
    for sid, record in db.list_submissions():
        combined = combined_result_for(sid, record)
        if combined is None:
            continue
        results.append(combined)
        id_by_team[combined.team_name] = sid

    if not results:
        return []

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
            "juries_submitted": len(db.get_jury_scores_for_submission(id_by_team[r.result.team_name])),
        }
        for r in ranked
    ]
