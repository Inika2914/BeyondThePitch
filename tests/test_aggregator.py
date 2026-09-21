"""
Offline test for the pool calibration logic — constructs synthetic
EvaluationResults directly (bypassing the API) to verify ranking and
calibration math is correct.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from scoring.engine import EvaluationResult, DimensionScore
from scoring.aggregator import calibrate_pool
from scoring.rubrics import DIMENSIONS


def make_result(team_name: str, scores: dict[str, int]) -> EvaluationResult:
    dimension_scores = [
        DimensionScore(
            key=d.key, label=d.label, score=scores[d.key],
            justification="synthetic", strengths=[], concerns=[],
        )
        for d in DIMENSIONS
    ]
    weighted = sum(ds.score * d.weight for ds, d in zip(dimension_scores, DIMENSIONS))
    total_w = sum(d.weight for d in DIMENSIONS)
    return EvaluationResult(
        team_name=team_name,
        project_title=f"{team_name} Project",
        dimension_scores=dimension_scores,
        weighted_total=round((weighted / (10 * total_w)) * 100, 1),
        top_strengths=[], top_improvements=[], judge_flags=[],
    )


def test_calibrate_pool_ranks_correctly():
    keys = [d.key for d in DIMENSIONS]
    results = [
        make_result("Strong Team", {k: 9 for k in keys}),
        make_result("Middling Team", {k: 5 for k in keys}),
        make_result("Weak Team", {k: 2 for k in keys}),
    ]
    ranked = calibrate_pool(results)

    assert ranked[0].result.team_name == "Strong Team"
    assert ranked[1].result.team_name == "Middling Team"
    assert ranked[2].result.team_name == "Weak Team"
    assert ranked[0].rank == 1 and ranked[2].rank == 3
    assert ranked[0].calibrated_total > ranked[1].calibrated_total > ranked[2].calibrated_total
    print("test_calibrate_pool_ranks_correctly: PASS")


def test_calibrate_pool_handles_single_submission():
    keys = [d.key for d in DIMENSIONS]
    results = [make_result("Solo Team", {k: 7 for k in keys})]
    ranked = calibrate_pool(results)
    assert len(ranked) == 1
    assert ranked[0].rank == 1
    assert ranked[0].percentile == 100.0
    print("test_calibrate_pool_handles_single_submission: PASS")


def test_calibrate_pool_empty():
    assert calibrate_pool([]) == []
    print("test_calibrate_pool_empty: PASS")


if __name__ == "__main__":
    test_calibrate_pool_ranks_correctly()
    test_calibrate_pool_handles_single_submission()
    test_calibrate_pool_empty()
    print("\nAll aggregator tests passed.")
