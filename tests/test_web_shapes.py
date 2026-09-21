"""
Offline smoke test for the FastAPI backend's data-shaping logic.

Doesn't start a server (fastapi isn't guaranteed installed in every dev
environment yet) — instead exercises the same SUBMISSIONS-dict shape and
serialization the endpoints use, against the real dataclasses, to catch
shape mismatches before anyone opens a browser.
"""

import sys
from pathlib import Path
from dataclasses import asdict
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

from adapters.folder_adapter import FolderAdapter
from scoring.engine import EvaluationResult, DimensionScore
from scoring.rubrics import DIMENSIONS
from scoring.aggregator import calibrate_pool
import tempfile


def make_fake_result(team, title, scores):
    keys = [d.key for d in DIMENSIONS]
    dimension_scores = [
        DimensionScore(key=k, label=k, score=scores[k], justification="test",
                        strengths=["s1"], concerns=["c1"])
        for k in keys
    ]
    weighted = sum(ds.score * d.weight for ds, d in zip(dimension_scores, DIMENSIONS))
    total_w = sum(d.weight for d in DIMENSIONS)
    return EvaluationResult(
        team_name=team, project_title=title,
        dimension_scores=dimension_scores,
        weighted_total=round((weighted / (10 * total_w)) * 100, 1),
        top_strengths=["strong readme"], top_improvements=["add tests"],
        judge_flags=["no demo provided"],
    )


def test_submission_detail_shape():
    """Mirrors what submission_detail() in web/app.py builds and returns."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "README.md").write_text("# Test\nA test project.")
        (root / "app.py").write_text("print('hi')")

        bundle = FolderAdapter().ingest(
            folder_path=tmp, team_name="Team X", project_title="Widget",
            demo_kind="live_url", demo_value="https://example.com",
        )
        result = make_fake_result("Team X", "Widget", {d.key: 7 for d in DIMENSIONS})

        # This is exactly the shape submission_detail() returns.
        payload = {
            "id": "abc123",
            "team_name": "Team X",
            "project_title": "Widget",
            "status": "scored",
            "error": None,
            "submission": {
                "problem_statement": bundle.problem_statement,
                "business_impact_pitch": bundle.business_impact_pitch,
                "tech_stack": bundle.tech_stack,
                "readme_present": bool(bundle.readme_text),
                "code_file_count": len(bundle.code_files),
                "demo_kind": bundle.demo.kind,
                "demo_value": bundle.demo.value,
            },
            "result": asdict(result),
        }

        assert payload["submission"]["readme_present"] is True
        assert payload["submission"]["code_file_count"] == 1
        assert payload["submission"]["demo_kind"] == "live_url"
        assert payload["result"]["weighted_total"] == result.weighted_total
        assert len(payload["result"]["dimension_scores"]) == len(DIMENSIONS)
        print("test_submission_detail_shape: PASS")


def test_leaderboard_shape():
    """Mirrors what leaderboard() in web/app.py builds and returns."""
    r1 = make_fake_result("Team A", "Proj A", {d.key: 9 for d in DIMENSIONS})
    r2 = make_fake_result("Team B", "Proj B", {d.key: 4 for d in DIMENSIONS})
    ranked = calibrate_pool([r1, r2])

    payload = [
        {
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

    assert payload[0]["team_name"] == "Team A"
    assert payload[0]["rank"] == 1
    assert payload[1]["rank"] == 2
    assert all("judge_flags" in row for row in payload)
    print("test_leaderboard_shape: PASS")


if __name__ == "__main__":
    test_submission_detail_shape()
    test_leaderboard_shape()
    print("\nAll web-layer shape tests passed.")
