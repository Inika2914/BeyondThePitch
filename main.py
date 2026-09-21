"""
Beyond The Pitch CLI.

Usage:
    # Evaluate a single folder-based submission
    python main.py score --folder ./submissions/team-alpha \\
        --team "Team Alpha" --title "SmartQueue" \\
        --problem "Long hospital wait times" \\
        --impact "Cuts average wait by 30% in pilot clinics" \\
        --stack "Python, FastAPI, React" \\
        --demo-kind live_url --demo-value "https://smartqueue.demo"

    # Evaluate every subfolder under a submissions directory and rank them
    python main.py score-batch --submissions-dir ./submissions --config batch_config.json
"""

import argparse
import json
import sys
from pathlib import Path

from adapters.folder_adapter import FolderAdapter
from adapters.manual_adapter import ManualAdapter
from scoring.engine import evaluate, result_to_dict
from scoring.aggregator import calibrate_pool


def cmd_score(args):
    adapter = FolderAdapter()
    bundle = adapter.ingest(
        folder_path=args.folder,
        team_name=args.team,
        project_title=args.title,
        problem_statement=args.problem or "",
        business_impact_pitch=args.impact or "",
        tech_stack=args.stack or "",
        demo_kind=args.demo_kind,
        demo_value=args.demo_value,
        demo_notes=args.demo_notes,
    )
    print(f"Ingested {len(bundle.code_files)} code files, "
          f"README: {'yes' if bundle.readme_text else 'no'}. Scoring...",
          file=sys.stderr)

    result = evaluate(bundle)
    _print_result(result)

    if args.output:
        Path(args.output).write_text(json.dumps(result_to_dict(result), indent=2))
        print(f"\nWritten to {args.output}", file=sys.stderr)


def cmd_score_batch(args):
    """
    Config file format (JSON list):
    [
      {
        "folder": "./submissions/team-alpha",
        "team_name": "Team Alpha",
        "project_title": "SmartQueue",
        "problem_statement": "...",
        "business_impact_pitch": "...",
        "tech_stack": "...",
        "demo_kind": "live_url",
        "demo_value": "https://...",
        "demo_notes": ""
      },
      ...
    ]
    """
    config = json.loads(Path(args.config).read_text())
    adapter = FolderAdapter()
    results = []

    for entry in config:
        print(f"Scoring {entry['team_name']}...", file=sys.stderr)
        bundle = adapter.ingest(
            folder_path=entry["folder"],
            team_name=entry["team_name"],
            project_title=entry["project_title"],
            problem_statement=entry.get("problem_statement", ""),
            business_impact_pitch=entry.get("business_impact_pitch", ""),
            tech_stack=entry.get("tech_stack", ""),
            demo_kind=entry.get("demo_kind", "none"),
            demo_value=entry.get("demo_value"),
            demo_notes=entry.get("demo_notes"),
        )
        results.append(evaluate(bundle))

    ranked = calibrate_pool(results)

    print("\n" + "=" * 60)
    print("LEADERBOARD (calibrated against pool distribution)")
    print("=" * 60)
    for r in ranked:
        print(f"#{r.rank:<3} {r.result.team_name:<25} "
              f"raw={r.result.weighted_total:>5.1f}  "
              f"calibrated={r.calibrated_total:>5.1f}  "
              f"pctile={r.percentile:>5.1f}")

    if args.output:
        payload = [
            {
                "rank": r.rank,
                "percentile": r.percentile,
                "calibrated_total": r.calibrated_total,
                "result": result_to_dict(r.result),
            }
            for r in ranked
        ]
        Path(args.output).write_text(json.dumps(payload, indent=2))
        print(f"\nWritten to {args.output}", file=sys.stderr)


def _print_result(result):
    print(f"\n{'=' * 60}")
    print(f"{result.team_name} — {result.project_title}")
    print(f"{'=' * 60}")
    print(f"Weighted total: {result.weighted_total}/100\n")

    for ds in result.dimension_scores:
        print(f"  [{ds.score}/10] {ds.label}")
        print(f"      {ds.justification}")
        for s in ds.strengths:
            print(f"      + {s}")
        for c in ds.concerns:
            print(f"      - {c}")
        print()

    print("Top strengths:")
    for s in result.top_strengths:
        print(f"  + {s}")
    print("Top improvements:")
    for s in result.top_improvements:
        print(f"  - {s}")
    if result.judge_flags:
        print("Judge attention flags:")
        for f in result.judge_flags:
            print(f"  ! {f}")


def build_parser():
    parser = argparse.ArgumentParser(description="Beyond The Pitch — hackathon submission evaluator")
    sub = parser.add_subparsers(dest="command", required=True)

    p_score = sub.add_parser("score", help="Score a single submission")
    p_score.add_argument("--folder", required=True)
    p_score.add_argument("--team", required=True)
    p_score.add_argument("--title", required=True)
    p_score.add_argument("--problem")
    p_score.add_argument("--impact")
    p_score.add_argument("--stack")
    p_score.add_argument("--demo-kind", default="none", choices=["video_url", "live_url", "screenshots", "none"])
    p_score.add_argument("--demo-value")
    p_score.add_argument("--demo-notes")
    p_score.add_argument("--output", help="Write JSON result to this path")
    p_score.set_defaults(func=cmd_score)

    p_batch = sub.add_parser("score-batch", help="Score and rank all submissions in a config file")
    p_batch.add_argument("--config", required=True, help="Path to JSON config listing submissions")
    p_batch.add_argument("--output", help="Write JSON leaderboard to this path")
    p_batch.set_defaults(func=cmd_score_batch)

    return parser


if __name__ == "__main__":
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)
