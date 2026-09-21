"""
Explicit, editable rubrics for each evaluation dimension.

These are deliberately kept as plain data (not buried in a prompt string)
so organizers/judges can review, tune, or reweight criteria without
touching scoring logic. Every dimension is scored 1-10.
"""

from dataclasses import dataclass


@dataclass
class Dimension:
    key: str
    label: str
    weight: float          # relative weight in the final aggregate score
    criteria: str           # the rubric text handed to the model
    requires_code: bool = False


DIMENSIONS: list[Dimension] = [
    Dimension(
        key="code_quality",
        label="Code Quality",
        weight=0.25,
        requires_code=True,
        criteria="""
Score 1-10 based on:
- Structure & modularity (clear separation of concerns vs. one giant file)
- Error handling (are failure paths handled, or does anything obviously break)
- Testing (any tests present, even minimal)
- Dependency hygiene (reasonable, declared dependencies vs. unmanaged mess)
- Readability (naming, comments where needed, no dead/commented-out code dumped in)

Do NOT reward verbosity or file count. A small, clean, well-structured
project should score as well as a large one. Penalize copy-pasted
boilerplate with no original logic. If no code was provided, score 1 and
say so explicitly — do not infer quality from the README alone.
""".strip(),
    ),
    Dimension(
        key="readme_clarity",
        label="README Clarity",
        weight=0.15,
        criteria="""
Score 1-10 based on whether the README:
- Clearly explains the problem being solved
- Explains the solution/approach
- Has setup/run instructions a stranger could follow
- Documents the tech stack / architecture at a high level
- Is honest about limitations, rather than only marketing copy

A short, clear README should outscore a long README full of buzzwords
with no actionable setup instructions. If no README was provided, score 1.
""".strip(),
    ),
    Dimension(
        key="demo_completeness",
        label="Demo Completeness",
        weight=0.15,
        criteria="""
Score 1-10 based on the demo evidence provided (video, live URL, or
screenshots):
- Does it show an actually working end-to-end flow, not just a UI mockup?
- Does it cover the core claimed functionality, or only a fragment?
- Is there enough evidence to trust the project works as described?

If demo evidence is "none", score 1 and flag it explicitly — this is a
significant gap regardless of how strong the code or pitch is. Do not
guess at functionality that wasn't demonstrated.
""".strip(),
    ),
    Dimension(
        key="business_impact",
        label="Business Impact",
        weight=0.20,
        criteria="""
Score 1-10 based on the problem statement and business impact pitch:
- Is the problem real and clearly articulated, with a plausible audience?
- Is the proposed impact specific (who benefits, how, how much) rather
  than vague ("this will help everyone")?
- Is there any evidence of validation (even informal), or is it pure
  speculation?
- Is the solution proportionate to the problem (not wildly overbuilt or
  underbuilt relative to the stated impact)?

Reward specificity and realism over grand but unsubstantiated claims.
""".strip(),
    ),
    Dimension(
        key="novelty",
        label="Novelty",
        weight=0.25,
        criteria="""
Score 1-10 based on:
- Is the core idea genuinely novel, or a fairly standard implementation
  of a well-known pattern (e.g. "yet another CRUD to-do app")?
- Is there a novel technical approach even if the problem itself is
  common (e.g. a creative use of a technique to solve an old problem)?
- Does it combine existing ideas in a new, non-obvious way?

Do not penalize a common problem domain if the approach or execution is
genuinely inventive. Do not reward novelty-for-its-own-sake if the
project doesn't actually work (that's captured by other dimensions).
""".strip(),
    ),
]


def get_dimension(key: str) -> Dimension:
    for d in DIMENSIONS:
        if d.key == key:
            return d
    raise KeyError(f"Unknown dimension: {key}")


def total_weight() -> float:
    return sum(d.weight for d in DIMENSIONS)


# Captured once at import time, before any runtime overrides — this is
# what "reset to defaults" restores. Do not mutate this dict.
_DEFAULT_WEIGHTS: dict[str, float] = {d.key: d.weight for d in DIMENSIONS}


def get_weights() -> dict[str, float]:
    """Current weight for every dimension, keyed by dimension key."""
    return {d.key: d.weight for d in DIMENSIONS}


def get_default_weights() -> dict[str, float]:
    """The weights this module started with, regardless of any runtime
    overrides applied since. Useful for a 'reset' control in the UI."""
    return dict(_DEFAULT_WEIGHTS)


def set_weights(overrides: dict[str, float]) -> dict[str, float]:
    """
    Apply new weights to one or more dimensions in place. Because
    DIMENSIONS is the same list object scoring/engine.py and
    scoring/aggregator.py both import and iterate over, this change is
    visible to any evaluation or calibration run that happens after this
    call returns — no restart needed.

    Unknown keys or non-positive weights raise ValueError rather than
    being silently ignored, since a typo'd dimension key silently not
    taking effect would be a confusing, hard-to-notice bug for a judge
    mid-event.

    Note: this does not retroactively change scores already computed —
    an EvaluationResult's weighted_total was calculated with whatever
    weights were active at the time evaluate() ran. If you reweight
    mid-event, prior scores and the current leaderboard become slightly
    apples-to-oranges; the practical fix is re-running evaluation for
    already-scored submissions after changing weights, not just for new
    ones.
    """
    valid_keys = {d.key for d in DIMENSIONS}
    unknown = set(overrides) - valid_keys
    if unknown:
        raise ValueError(f"Unknown dimension key(s): {sorted(unknown)}")

    for key, weight in overrides.items():
        if weight <= 0:
            raise ValueError(f"Weight for '{key}' must be positive, got {weight}")

    for dim in DIMENSIONS:
        if dim.key in overrides:
            dim.weight = float(overrides[dim.key])

    return get_weights()


def reset_weights() -> dict[str, float]:
    """Restore every dimension's weight to its original default."""
    return set_weights(_DEFAULT_WEIGHTS)
