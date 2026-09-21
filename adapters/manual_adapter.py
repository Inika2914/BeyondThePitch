"""
ManualAdapter: for submissions that are just form text + links, with no
code folder available (e.g. idea-only tracks, or a judge manually pasting
in README/pitch text). Produces a SubmissionBundle with empty code_files.
"""

from adapters.base import BaseAdapter
from models.submission import SubmissionBundle, DemoAsset


class ManualAdapter(BaseAdapter):
    name = "manual"

    def ingest(
        self,
        team_name: str,
        project_title: str,
        problem_statement: str = "",
        business_impact_pitch: str = "",
        tech_stack: str = "",
        readme_text: str = "",
        demo_kind: str = "none",
        demo_value: str | None = None,
        demo_notes: str | None = None,
    ) -> SubmissionBundle:
        return SubmissionBundle(
            team_name=team_name,
            project_title=project_title,
            problem_statement=problem_statement,
            business_impact_pitch=business_impact_pitch,
            tech_stack=tech_stack,
            readme_text=readme_text,
            code_files=[],
            demo=DemoAsset(kind=demo_kind, value=demo_value, notes=demo_notes),
            source_adapter=self.name,
            source_reference=None,
        )
