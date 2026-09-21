"""
Offline tests for the ingestion layer — no API key or network required.
Validates that adapters correctly normalize different input shapes into
SubmissionBundle, and that the prompt-context builder behaves sensibly.
Run with: python -m pytest tests/ -v   (or just: python tests/test_adapters.py)
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from adapters.folder_adapter import FolderAdapter
from adapters.manual_adapter import ManualAdapter


def test_folder_adapter_reads_readme_and_code():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "README.md").write_text("# SmartQueue\nA queueing app.")
        (root / "app.py").write_text("def main():\n    print('hello')\n")
        (root / "node_modules").mkdir()
        (root / "node_modules" / "junk.js").write_text("should be skipped")

        bundle = FolderAdapter().ingest(
            folder_path=tmp,
            team_name="Team Alpha",
            project_title="SmartQueue",
            problem_statement="Long wait times",
            business_impact_pitch="Cuts wait time 30%",
        )

        assert bundle.readme_text.startswith("# SmartQueue")
        assert bundle.has_code()
        assert any(f.path == "app.py" for f in bundle.code_files)
        assert not any("node_modules" in f.path for f in bundle.code_files)
        print("test_folder_adapter_reads_readme_and_code: PASS")


def test_folder_adapter_handles_missing_readme():
    with tempfile.TemporaryDirectory() as tmp:
        bundle = FolderAdapter().ingest(
            folder_path=tmp,
            team_name="Team Beta",
            project_title="Empty",
        )
        assert bundle.readme_text == ""
        assert not bundle.has_code()
        print("test_folder_adapter_handles_missing_readme: PASS")


def test_manual_adapter_no_code():
    bundle = ManualAdapter().ingest(
        team_name="Team Gamma",
        project_title="Idea Only",
        problem_statement="A problem",
        readme_text="Some pasted readme text",
    )
    assert bundle.source_adapter == "manual"
    assert not bundle.has_code()
    assert bundle.readme_text == "Some pasted readme text"
    print("test_manual_adapter_no_code: PASS")


def test_prompt_context_caps_code_length():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for i in range(5):
            (root / f"file{i}.py").write_text("x = 1\n" * 5000)  # large file

        bundle = FolderAdapter().ingest(
            folder_path=tmp, team_name="Team Delta", project_title="Big Repo"
        )
        context = bundle.to_prompt_context(max_code_chars=2000)
        # code portion should be capped even though 5 large files exist
        code_section = context.split("--- CODE SAMPLE")[1]
        assert len(code_section) < 3000
        print("test_prompt_context_caps_code_length: PASS")


if __name__ == "__main__":
    test_folder_adapter_reads_readme_and_code()
    test_folder_adapter_handles_missing_readme()
    test_manual_adapter_no_code()
    test_prompt_context_caps_code_length()
    print("\nAll adapter tests passed.")
