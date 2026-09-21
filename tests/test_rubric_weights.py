"""
Offline tests for runtime rubric-weight configuration — no API/server
needed. Validates set_weights/reset_weights/get_weights behavior and
that scoring picks up the new weights immediately.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from scoring import rubrics
from scoring.engine import EvaluationResult, DimensionScore
from scoring.rubrics import DIMENSIONS


def test_set_weights_updates_in_place():
    original = rubrics.get_weights()
    try:
        rubrics.set_weights({"demo_completeness": 0.05})
        assert rubrics.get_weights()["demo_completeness"] == 0.05
        # Other weights untouched
        assert rubrics.get_weights()["code_quality"] == original["code_quality"]
    finally:
        rubrics.reset_weights()


def test_reset_restores_defaults():
    original = rubrics.get_weights()
    rubrics.set_weights({"novelty": 0.4, "code_quality": 0.1})
    assert rubrics.get_weights() != original
    rubrics.reset_weights()
    assert rubrics.get_weights() == original


def test_unknown_key_rejected():
    try:
        rubrics.set_weights({"not_real": 0.3})
        assert False, "should have raised ValueError"
    except ValueError:
        pass
    finally:
        rubrics.reset_weights()


def test_non_positive_weight_rejected():
    try:
        rubrics.set_weights({"code_quality": 0})
        assert False, "should have raised ValueError"
    except ValueError:
        pass
    try:
        rubrics.set_weights({"code_quality": -0.1})
        assert False, "should have raised ValueError"
    except ValueError:
        pass
    finally:
        rubrics.reset_weights()


def test_partial_update_leaves_others_unchanged():
    rubrics.reset_weights()
    before = rubrics.get_weights()
    rubrics.set_weights({"readme_clarity": 0.3})
    after = rubrics.get_weights()
    assert after["readme_clarity"] == 0.3
    for key in before:
        if key != "readme_clarity":
            assert after[key] == before[key]
    rubrics.reset_weights()


def test_reweighting_changes_weighted_total():
    """Confirms a weight change actually shifts the computed total for
    the exact same per-dimension scores — this is the behavior the web
    layer's config endpoint exists to expose."""
    rubrics.reset_weights()
    keys = [d.key for d in DIMENSIONS]
    dimension_scores = [
        DimensionScore(key=k, label=k, score=1 if k == "demo_completeness" else 8,
                        justification="t", strengths=[], concerns=[])
        for k in keys
    ]

    def weighted_total():
        total_w = sum(d.weight for d in DIMENSIONS)
        s = sum(ds.score * d.weight for ds, d in zip(dimension_scores, DIMENSIONS))
        return round((s / (10 * total_w)) * 100, 1)

    before = weighted_total()

    # Demo scored 1/10 and drags the total down under default weight.
    # Lowering its weight should raise the total for these same scores.
    rubrics.set_weights({"demo_completeness": 0.02})
    after = weighted_total()

    assert after > before, f"expected total to rise after lowering demo weight, got {before} -> {after}"
    rubrics.reset_weights()


if __name__ == "__main__":
    test_set_weights_updates_in_place()
    test_reset_restores_defaults()
    test_unknown_key_rejected()
    test_non_positive_weight_rejected()
    test_partial_update_leaves_others_unchanged()
    test_reweighting_changes_weighted_total()
    print("All rubric weight tests passed.")
