"""
OpenLabel — AI Output Schemas (Member 3 re-export)

We keep `ai_logic/` untouched. These local exports exist so older imports (if any)
still resolve to Member 3's structured output.
"""

from ai_logic.llm_service import (  # noqa: F401
    FlagItem as AIFlag,
    ProductAnalysisResult as OpenLabelReport,
)

# Backward-compatible name kept for any legacy imports. The new adapters in
# `services/ai_engine.py` use Member 3's prompt/schema directly.
TECH_JUSTICE_SYSTEM_PROMPT = ""
