"""
FolderAdapter: ingests a submission from a local folder (or an already-
unzipped upload). This is the lowest-common-denominator adapter — every
hackathon platform can eventually be reduced to "here's a folder of files
plus some form text", so this is the one every other adapter can fall
back to.
"""

import os
from pathlib import Path
from adapters.base import BaseAdapter
from models.submission import SubmissionBundle, CodeFile, DemoAsset

# File types we bother reading as "code" for scoring purposes.
CODE_EXTENSIONS = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".go", ".rs", ".rb",
    ".php", ".c", ".cpp", ".h", ".cs", ".swift", ".kt", ".html", ".css",
    ".sql", ".sh", ".yml", ".yaml", ".json",
}

# Directories to skip entirely — noise, not signal.
SKIP_DIRS = {
    "node_modules", ".git", "__pycache__", "venv", ".venv", "dist",
    "build", ".next", "target", "vendor", ".idea", ".vscode",
}

MAX_FILE_CHARS = 4000       # per-file cap before we mark it truncated
MAX_FILES_SAMPLED = 40      # cap total files read, prioritizing breadth


class FolderAdapter(BaseAdapter):
    name = "folder"

    def ingest(
        self,
        folder_path: str,
        team_name: str,
        project_title: str,
        problem_statement: str = "",
        business_impact_pitch: str = "",
        tech_stack: str = "",
        demo_kind: str = "none",
        demo_value: str | None = None,
        demo_notes: str | None = None,
    ) -> SubmissionBundle:
        root = Path(folder_path)
        if not root.exists():
            raise FileNotFoundError(f"Submission folder not found: {folder_path}")

        readme_text = self._find_readme(root)
        code_files = self._collect_code_files(root)

        return SubmissionBundle(
            team_name=team_name,
            project_title=project_title,
            problem_statement=problem_statement,
            business_impact_pitch=business_impact_pitch,
            tech_stack=tech_stack,
            readme_text=readme_text,
            code_files=code_files,
            demo=DemoAsset(kind=demo_kind, value=demo_value, notes=demo_notes),
            source_adapter=self.name,
            source_reference=str(root.resolve()),
        )

    def _find_readme(self, root: Path) -> str:
        for candidate in ["README.md", "readme.md", "README.txt", "README", "Readme.md"]:
            p = root / candidate
            if p.exists():
                try:
                    return p.read_text(errors="ignore")
                except Exception:
                    return ""
        return ""

    def _collect_code_files(self, root: Path) -> list[CodeFile]:
        collected: list[CodeFile] = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
            for fname in filenames:
                if len(collected) >= MAX_FILES_SAMPLED:
                    return collected
                ext = Path(fname).suffix.lower()
                if ext not in CODE_EXTENSIONS:
                    continue
                fpath = Path(dirpath) / fname
                try:
                    content = fpath.read_text(errors="ignore")
                except Exception:
                    continue
                truncated = len(content) > MAX_FILE_CHARS
                if truncated:
                    content = content[:MAX_FILE_CHARS]
                rel_path = str(fpath.relative_to(root))
                collected.append(CodeFile(
                    path=rel_path,
                    content=content,
                    language=ext.lstrip("."),
                    truncated=truncated,
                ))
        return collected
