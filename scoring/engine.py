"""
Scoring engine for Beyond The Pitch.

The scoring logic is provider-independent. It uses OpenRouter's
OpenAI-compatible API so the project can use a free OpenRouter model.
"""

import json
import os
from dataclasses import dataclass, asdict

from models.submission import SubmissionBundle
from scoring.rubrics import DIMENSIONS, Dimension, total_weight


# OpenRouter free model.
MODEL = "nex-agi/nex-n2.5-pro:free"

@dataclass
class DimensionScore:
    key: str
    label: str
    score: int                 # 1-10
    justification: str
    strengths: list[str]
    concerns: list[str]


@dataclass
class EvaluationResult:
    team_name: str
    project_title: str
    dimension_scores: list[DimensionScore]
    weighted_total: float       # normalized to 0-100
    top_strengths: list[str]
    top_improvements: list[str]
    judge_flags: list[str]


SYSTEM_PROMPT = """You are Beyond The Pitch, an assistant that helps hackathon
judges score submissions consistently against a fixed rubric.

You are NOT the final decision-maker. Your job is to produce a structured,
honest, defensible first-pass assessment that a human judge can review
and override.

Rules:
- Score strictly against the rubric given.
- Do not reward polish, length, or marketing language that isn't backed
  by evidence in the submission.
- Be specific in justifications. Reference actual content from the
  submission instead of generic praise or criticism.
- If material for a dimension is missing, say so plainly and score low
  rather than guessing or being generous.
- Output ONLY valid JSON matching the requested schema.
- Do not include markdown fences.
- Do not include any commentary outside the JSON.
"""


def _client():
    """Create an OpenRouter client using the environment variable.

    Imported lazily so the dataclasses/rubrics/aggregator in this module
    stay testable without the openai package installed.
    """
    from openai import OpenAI

    api_key = os.environ.get("OPENROUTER_API_KEY")

    if not api_key:
        raise RuntimeError(
            "OPENROUTER_API_KEY not set. "
            "Set it in PowerShell before running the scorer."
        )

    return OpenAI(
        api_key=api_key,
        base_url="https://openrouter.ai/api/v1",
        timeout=45.0,   # fail loudly instead of hanging forever on a
                        # slow/overloaded free-tier model
        max_retries=1,
    )


def _extract_response_text(response) -> str:
    """Extract text safely from an OpenAI-compatible response."""

    if not response.choices:
        raise RuntimeError("OpenRouter returned no choices.")

    message = response.choices[0].message

    if message is None:
        raise RuntimeError("OpenRouter returned an empty message.")

    content = message.content

    if content is None:
        # Some reasoning models may put useful information in the
        # reasoning field instead of content. However, reasoning is
        # not the JSON answer we want, so report the response clearly.
        reasoning = getattr(message, "reasoning", None)

        if reasoning:
            raise RuntimeError(
                "OpenRouter returned reasoning but no final answer. "
                "The selected model did not provide the requested JSON."
            )

        raise RuntimeError("OpenRouter returned an empty response.")

    if isinstance(content, list):
        text_parts = []

        for item in content:
            if isinstance(item, dict):
                text = item.get("text")
                if text:
                    text_parts.append(text)
            else:
                text_parts.append(str(item))

        content = "".join(text_parts)

    return str(content).strip()


def _clean_json_text(raw_text: str) -> str:
    """Remove accidental markdown code fences from model output."""

    raw_text = raw_text.strip()

    if raw_text.startswith("```json"):
        raw_text = raw_text[len("```json"):]

    elif raw_text.startswith("```"):
        raw_text = raw_text[len("```"):]

    if raw_text.endswith("```"):
        raw_text = raw_text[:-3]

    return raw_text.strip()


def _score_dimension(
    client,
    bundle: SubmissionBundle,
    dim: Dimension
) -> DimensionScore:

    if dim.requires_code and not bundle.has_code():
        return DimensionScore(
            key=dim.key,
            label=dim.label,
            score=1,
            justification="No code files were provided in this submission.",
            strengths=[],
            concerns=["No code artifact available to evaluate."],
        )

    context = bundle.to_prompt_context()

    user_prompt = f"""
DIMENSION TO SCORE: {dim.label}

RUBRIC:
{dim.criteria}

SUBMISSION MATERIAL:
{context}

Respond with ONLY this JSON object. Keep the justification to 2-3 sentences
and each strength/concern to one short phrase — the response must fit
comfortably within the token limit, so do not pad it:

{{
  "score": <integer 1-10>,
  "justification": "<2-3 sentences citing specific evidence>",
  "strengths": ["<short phrase>", "..."],
  "concerns": ["<short phrase>", "..."]
}}
"""

    def _call_and_parse():
        response = client.chat.completions.create(
            model=MODEL,
            messages=[
                {
                    "role": "system",
                    "content": SYSTEM_PROMPT,
                },
                {
                    "role": "user",
                    "content": user_prompt,
                },
            ],
            max_tokens=2000,
            response_format={"type": "json_object"},
        )
        raw_text = _extract_response_text(response)
        raw_text = _clean_json_text(raw_text)
        return json.loads(raw_text), raw_text

    try:
        parsed, raw_text = _call_and_parse()
    except json.JSONDecodeError:
        # Most common cause: the model's output got cut off mid-object
        # before hitting a natural stop. One retry, same prompt, is
        # cheap and usually succeeds — free-tier models are more prone
        # to this than paid ones.
        try:
            parsed, raw_text = _call_and_parse()
        except json.JSONDecodeError as e:
            raise RuntimeError(
                f"Model did not return valid JSON for {dim.key} after retry: "
                f"{raw_text[:500] if 'raw_text' in dir() else '(no text captured)'}"
            ) from e

    if "score" not in parsed:
        raise RuntimeError(
            f"Model JSON for {dim.key} is missing 'score': "
            f"{raw_text[:500]}"
        )

    score = int(parsed["score"])

    # Safety check: scores must stay between 1 and 10.
    score = max(1, min(10, score))

    return DimensionScore(
        key=dim.key,
        label=dim.label,
        score=score,
        justification=parsed.get(
            "justification",
            "No justification provided by the model."
        ),
        strengths=parsed.get("strengths", []),
        concerns=parsed.get("concerns", []),
    )


def _synthesize_summary(
    client,
    bundle: SubmissionBundle,
    scores: list[DimensionScore]
) -> tuple[list[str], list[str], list[str]]:
    """
    Second pass: roll up per-dimension detail into a judge-facing summary.
    """

    scores_blob = "\n".join(
        f"- {s.label}: {s.score}/10 — {s.justification}"
        for s in scores
    )

    user_prompt = f"""
Here are the per-dimension scores for this submission:

{scores_blob}

Summarize this into the following JSON object:

{{
  "top_strengths": [
    "<at most 3, most important strengths across all dimensions>"
  ],
  "top_improvements": [
    "<at most 3, most important areas to improve>"
  ],
  "judge_flags": [
    "<anything requiring explicit human judge attention, "
    "e.g. missing demo evidence, suspiciously thin code, "
    "contradictions between pitch and code>"
  ]
}}

Respond with ONLY valid JSON. Keep each item to a short phrase, not a
full sentence — the response must fit comfortably within the token
limit.
"""

    def _call_and_parse():
        response = client.chat.completions.create(
            model=MODEL,
            messages=[
                {
                    "role": "system",
                    "content": SYSTEM_PROMPT,
                },
                {
                    "role": "user",
                    "content": user_prompt,
                },
            ],
            max_tokens=1000,
            response_format={"type": "json_object"},
        )
        raw_text = _extract_response_text(response)
        raw_text = _clean_json_text(raw_text)
        return json.loads(raw_text), raw_text

    try:
        parsed, raw_text = _call_and_parse()
    except json.JSONDecodeError:
        try:
            parsed, raw_text = _call_and_parse()
        except json.JSONDecodeError as e:
            raise RuntimeError(
                f"Model did not return valid JSON for summary after retry: "
                f"{raw_text[:500] if 'raw_text' in dir() else '(no text captured)'}"
            ) from e

    return (
        parsed.get("top_strengths", []),
        parsed.get("top_improvements", []),
        parsed.get("judge_flags", []),
    )


def evaluate(bundle: SubmissionBundle) -> EvaluationResult:

    client = _client()

    dimension_scores = [
        _score_dimension(client, bundle, dim)
        for dim in DIMENSIONS
    ]

    weighted_sum = sum(
        ds.score * dim.weight
        for ds, dim in zip(dimension_scores, DIMENSIONS)
    )

    weighted_total = round(
        (weighted_sum / (10 * total_weight())) * 100,
        1
    )

    top_strengths, top_improvements, judge_flags = _synthesize_summary(
        client,
        bundle,
        dimension_scores
    )

    return EvaluationResult(
        team_name=bundle.team_name,
        project_title=bundle.project_title,
        dimension_scores=dimension_scores,
        weighted_total=weighted_total,
        top_strengths=top_strengths,
        top_improvements=top_improvements,
        judge_flags=judge_flags,
    )


def evaluate_stream(bundle: SubmissionBundle):
    """Generator version of evaluate() that yields progress events as it
    goes, so a caller (e.g. the web layer) can stream live status to a
    client instead of blocking silently until the whole pass finishes.

    Yields dicts of the form:
      {"stage": "dimension", "status": "start",  "key": ..., "label": ...}
      {"stage": "dimension", "status": "done",   "key": ..., "label": ..., "score": DimensionScore}
      {"stage": "dimension", "status": "error",  "key": ..., "label": ..., "error": str}
      {"stage": "summary",   "status": "start"}
      {"stage": "summary",   "status": "done"}
      {"stage": "final",     "result": EvaluationResult}

    Raises the underlying exception after yielding an "error" event for
    a dimension, so callers can decide how to surface it (the generator
    stops there — no "final" event follows).
    """

    client = _client()

    dimension_scores: list[DimensionScore] = []
    for dim in DIMENSIONS:
        yield {"stage": "dimension", "status": "start", "key": dim.key, "label": dim.label}
        try:
            ds = _score_dimension(client, bundle, dim)
        except Exception as e:
            yield {"stage": "dimension", "status": "error", "key": dim.key, "label": dim.label, "error": str(e)}
            raise
        dimension_scores.append(ds)
        yield {"stage": "dimension", "status": "done", "key": dim.key, "label": dim.label, "score": ds}

    weighted_sum = sum(
        ds.score * dim.weight
        for ds, dim in zip(dimension_scores, DIMENSIONS)
    )

    weighted_total = round(
        (weighted_sum / (10 * total_weight())) * 100,
        1
    )

    yield {"stage": "summary", "status": "start"}
    try:
        top_strengths, top_improvements, judge_flags = _synthesize_summary(
            client,
            bundle,
            dimension_scores
        )
    except Exception as e:
        yield {"stage": "summary", "status": "error", "error": str(e)}
        raise
    yield {"stage": "summary", "status": "done"}

    result = EvaluationResult(
        team_name=bundle.team_name,
        project_title=bundle.project_title,
        dimension_scores=dimension_scores,
        weighted_total=weighted_total,
        top_strengths=top_strengths,
        top_improvements=top_improvements,
        judge_flags=judge_flags,
    )

    yield {"stage": "final", "result": result}


def result_to_dict(result: EvaluationResult) -> dict:
    return asdict(result)