"""
Pool-level aggregation across all submissions in a hackathon.

Raw per-submission scores are produced independently (order shouldn't
matter, but LLM scoring can still drift slightly run to run). This module
adds a second-pass calibration step: rank submissions per dimension and
compute a percentile-adjusted score, so a submission judged early or late
in a batch isn't penalized relative to ones judged when the "scale" in
context had drifted.
"""

from dataclasses import dataclass
from statistics import mean, pstdev

from scoring.engine import EvaluationResult
from scoring.rubrics import DIMENSIONS


@dataclass
class RankedResult:
    result: EvaluationResult
    rank: int
    percentile: float
    calibrated_total: float


def calibrate_pool(results: list[EvaluationResult]) -> list[RankedResult]:
    """
    Applies z-score normalization per dimension across the whole pool,
    then recomputes each submission's weighted total from the normalized
    per-dimension scores. This flattens out systematic drift (e.g. the
    model scoring everything a point lower after reading 30 mediocre
    READMEs in a row) without hiding genuine differences in quality.
    """
    if not results:
        return []

    dim_keys = [d.key for d in DIMENSIONS]
    dim_weights = {d.key: d.weight for d in DIMENSIONS}

    # raw_scores[dim_key] = [score for each submission, in order]
    raw_scores: dict[str, list[float]] = {
        key: [next(ds.score for ds in r.dimension_scores if ds.key == key) for r in results]
        for key in dim_keys
    }

    calibrated_totals = [0.0] * len(results)

    for key in dim_keys:
        values = raw_scores[key]
        mu = mean(values)
        sigma = pstdev(values) or 1.0  # avoid div-by-zero when all scores tie

        for i, raw in enumerate(values):
            z = (raw - mu) / sigma
            # Map z-score back onto a 1-10 scale centered on the pool mean,
            # clipped to stay in range. This is intentionally gentle —
            # it corrects drift, it doesn't override genuine outliers.
            calibrated = min(10.0, max(1.0, mu + z * 1.5))
            calibrated_totals[i] += calibrated * dim_weights[key]

    total_weight = sum(dim_weights.values())
    calibrated_pct = [
        round((t / (10 * total_weight)) * 100, 1) for t in calibrated_totals
    ]

    ranked = sorted(
        zip(results, calibrated_pct),
        key=lambda pair: pair[1],
        reverse=True,
    )

    n = len(ranked)
    output: list[RankedResult] = []
    for idx, (result, cal_total) in enumerate(ranked):
        rank = idx + 1
        percentile = round((1 - (idx / max(n - 1, 1))) * 100, 1) if n > 1 else 100.0
        output.append(RankedResult(
            result=result,
            rank=rank,
            percentile=percentile,
            calibrated_total=cal_total,
        ))
    return output
