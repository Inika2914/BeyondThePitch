# Beyond The Pitch

An AI-powered agent that helps hackathon judges evaluate submissions
consistently — scoring code quality, README clarity, demo completeness,
business impact, and novelty against a fixed rubric, with full
justification for every score.

## Why this architecture

Judges lose consistency for two reasons: **fatigue** (submission #40 gets
less scrutiny than submission #3) and **presentation bias** (well-packaged
but shallow projects outscore rough-but-strong engineering). Beyond The Pitch
addresses both by:

1. Scoring every submission against the **same explicit rubric**
   (`scoring/rubrics.py`) — not a free-form "what do you think" prompt.
2. Running a **pool-level calibration pass** (`scoring/aggregator.py`)
   that normalizes scores across all submissions in a batch, so judging
   order doesn't silently shift the scale.
3. Staying **source-agnostic**: the scoring engine only ever consumes a
   `SubmissionBundle` (`models/submission.py`). It has no idea whether the
   code came from a zip upload, a GitHub clone, or a manual form — new
   ingestion sources are adapters, not rewrites.

```
[Adapter: Folder/Zip] ─┐
[Adapter: Manual/Form] ├─► SubmissionBundle ─► Scoring Engine ─► EvaluationResult
[Adapter: GitHub, etc]─┘   (source-agnostic)    (rubric-driven)     ─► Aggregator (pool calibration)
                                                                              │
                                                                              ▼
                                                              [CLI]  or  [Web dashboard]
```

The web dashboard (`web/`) is a new interface layer only — it calls the
same `adapters/`, `models/`, and `scoring/` code the CLI uses, unchanged.
Nothing about the engine had to move or be rewritten to add it.

## Project layout

```
beyond-the-pitch/
├── models/
│   └── submission.py       # SubmissionBundle — the normalized contract
├── adapters/
│   ├── base.py              # adapter interface
│   ├── folder_adapter.py    # ingest a folder/unzipped submission
│   └── manual_adapter.py    # ingest form text only, no code
├── scoring/
│   ├── rubrics.py            # explicit, editable per-dimension rubrics
│   ├── engine.py             # LLM scoring per dimension, structured JSON out
│   └── aggregator.py         # pool-level ranking + bias calibration
├── web/
│   ├── app.py                 # FastAPI backend — calls the engine above, unchanged
│   └── static/
│       ├── index.html          # judge dashboard shell
│       ├── styles.css          # scorecard/ledger styling
│       └── app.js              # dashboard logic (fetch calls to web/app.py)
├── tests/
│   ├── test_adapters.py        # offline, no API key needed
│   ├── test_aggregator.py      # offline, no API key needed
│   ├── test_web_shapes.py      # offline — validates API response shapes
│   └── test_rubric_weights.py  # offline — validates weight config logic
├── main.py                    # CLI: score one submission, or batch + rank
└── requirements.txt
```

## Setup

```bash
pip install -r requirements.txt
export OPENROUTER_API_KEY=your_key_here
```

## Judge dashboard (web)

The dashboard walks the same flow every submission goes through:
**Submission → Analyze → 5 rubric scores → Evidence → Overall score →
Strengths → Improvements → Judge flags** — but as something a judge can
click through in a browser instead of a PowerShell command.

```bash
uvicorn web.app:app --reload
```

Then open **http://localhost:8000**. From there:

1. Click **+ New submission**, fill in the team/project fields, and
   attach a `.zip` of the team's code folder (optional — leave it out for
   idea-only submissions).
2. The submission ingests immediately and shows up in the left rail as
   "Ready to score" along with the file count and whether a README was
   found — so you can sanity-check ingestion before spending an LLM call.
3. Click **Run evaluation** on the submission's page. This calls the
   exact same `scoring.engine.evaluate()` the CLI uses — same rubric,
   same JSON schema, same justification-per-score.
4. The result renders as a scorecard: each of the 5 rubric dimensions
   with its score, a progress bar, the model's justification, and
   bullet-level evidence (strengths marked `+`, concerns marked `–`),
   followed by top strengths, top improvements, and a judge-flags box
   for anything needing explicit human attention.
5. Once more than one submission is scored, click **View leaderboard**
   for the pool-calibrated ranking (same `scoring.aggregator.calibrate_pool()`
   math as `score-batch` in the CLI) — click any row to jump back to that
   submission's full scorecard.

The dashboard keeps submissions in memory for the running session — it's
built for judging-day use, not as a permanent record. If you need results
to survive a server restart, see the note below on swapping in a database.

## Reweighting dimensions

Not every event needs all 5 dimensions weighted the same. A track with
no live-demo requirement shouldn't have teams penalized as hard for
"missing" demo evidence they were never asked to provide; an event
focused purely on technical depth might want Code Quality weighted
heavier than Business Impact.

Click **Rubric weights ⚙** in the dashboard's left rail to adjust each
dimension with a slider, save, and reset to defaults. Under the hood
this calls:

```
GET   /api/config/weights          # current weights + defaults
PUT   /api/config/weights          # body: {"weights": {"demo_completeness": 0.05}}
POST  /api/config/weights/reset
```

which reads and writes `scoring.rubrics.DIMENSIONS` directly — the same
list object `scoring/engine.py` and `scoring/aggregator.py` both iterate
over — so a change takes effect on the very next evaluation, no restart
needed.

**Important caveat:** reweighting does not retroactively change scores
already computed. An `EvaluationResult`'s `weighted_total` was calculated
using whatever weights were active the moment `evaluate()` ran. If you
adjust weights partway through judging, previously-scored submissions and
the current leaderboard become slightly apples-to-oranges — the practical
fix is re-running **Run evaluation** on already-scored submissions after
changing weights, not just applying the new weights going forward. For
this reason, it's best to settle on weights *before* judging starts
rather than mid-event.

Weights don't need to sum to 1 — `total_weight()` normalizes the final
score regardless of what the individual weights add up to.

## CLI usage (still available)

The original command-line flow described below still works exactly as
before — the dashboard is additive, not a replacement.

### Score one submission

```bash
python main.py score \
  --folder ./submissions/team-alpha \
  --team "Team Alpha" \
  --title "SmartQueue" \
  --problem "Long hospital wait times with no visibility for patients" \
  --impact "Cuts average wait time by 30% in pilot clinics" \
  --stack "Python, FastAPI, React" \
  --demo-kind live_url \
  --demo-value "https://smartqueue.demo" \
  --output results/team-alpha.json
```

### Score and rank a whole batch

Create a config file listing every submission:

```json
[
  {
    "folder": "./submissions/team-alpha",
    "team_name": "Team Alpha",
    "project_title": "SmartQueue",
    "problem_statement": "...",
    "business_impact_pitch": "...",
    "tech_stack": "Python, FastAPI",
    "demo_kind": "live_url",
    "demo_value": "https://..."
  },
  {
    "folder": "./submissions/team-beta",
    "team_name": "Team Beta",
    "project_title": "EcoTrack",
    "demo_kind": "none"
  }
]
```

```bash
python main.py score-batch --config batch_config.json --output leaderboard.json
```

This prints a ranked leaderboard with both the **raw** weighted score and
a **calibrated** score (adjusted against the pool's score distribution
per dimension) — use the calibrated column as the primary ranking signal,
and the raw score + per-dimension justifications as the audit trail.

## Adding a new ingestion source

Implement `BaseAdapter.ingest(...) -> SubmissionBundle` (see
`adapters/folder_adapter.py` for reference). The scoring engine and CLI
`score-batch` command need no changes — only `main.py`'s argument parsing
needs a new subcommand or flag to route to your adapter.

## Design notes / things to extend before production use

- **Demo completeness** is the hardest dimension to fully automate — right
  now it relies on judge-supplied `demo-kind`/`demo-value`/`demo-notes`
  rather than actually watching a video or crawling a live URL. A next
  step would be frame-sampling a demo video or scripted-clicking a live
  URL before handing evidence to the model.
- **Cross-submission novelty detection** (flagging near-duplicate ideas
  across the pool) isn't implemented yet — would need embedding each
  submission's pitch/README and comparing pairwise similarity across the
  batch, run once all submissions are ingested.
- All scores are a **first-pass draft** for the judge, not a final verdict
  — the output always includes per-dimension justification so a human
  can override any score with a clear reason.
- **Dashboard storage is in-memory** (`web/app.py`'s `SUBMISSIONS` dict) —
  fine for a single judging session, but it resets on server restart and
  isn't shared across multiple judges' browsers. Swapping in SQLite
  (or Postgres for a multi-judge event) means replacing that one dict
  with real reads/writes — `adapters/`, `models/`, and `scoring/` don't
  need to change at all.
