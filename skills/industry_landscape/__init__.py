"""产业全景报告 Skill。"""

from skills.industry_landscape.composer import compose_industry_landscape, render_industry_landscape
from skills.industry_landscape.spec import (
    INDUSTRY_LANDSCAPE_SPEC,
    normalize_industry_landscape_input,
    selected_capabilities,
)

__all__ = [
    "INDUSTRY_LANDSCAPE_SPEC",
    "compose_industry_landscape",
    "normalize_industry_landscape_input",
    "render_industry_landscape",
    "selected_capabilities",
]
