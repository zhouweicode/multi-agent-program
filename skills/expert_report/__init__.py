"""专家报告 Skill。"""

from skills.expert_report.composer import compose_expert_report, render_expert_report
from skills.expert_report.spec import EXPERT_REPORT_SPEC, normalize_expert_report_input

__all__ = [
    "EXPERT_REPORT_SPEC",
    "compose_expert_report",
    "normalize_expert_report_input",
    "render_expert_report",
]
