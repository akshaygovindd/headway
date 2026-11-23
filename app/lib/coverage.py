"""Coverage badge rules."""

from dataclasses import dataclass

NORMAL_MIN = 80.0
LOW_MIN = 50.0


@dataclass(frozen=True)
class CoverageBadge:
    level: str  # "normal" | "low" | "insufficient"
    label: str
    rankable: bool


def coverage_badge(coverage_pct: float | None) -> CoverageBadge:
    """>= 80 normal; 50-80 amber "low coverage"; < 50 (or unknown) "insufficient data"."""
    if coverage_pct is None or coverage_pct != coverage_pct or coverage_pct < LOW_MIN:
        return CoverageBadge("insufficient", "insufficient data", False)
    if coverage_pct < NORMAL_MIN:
        return CoverageBadge("low", "low coverage", True)
    return CoverageBadge("normal", f"{coverage_pct:.0f}% coverage", True)


def is_withheld(coverage_pct: float | None) -> bool:
    """The one withholding rule: a value with coverage below 50% (or unknown) is not shown."""
    return coverage_pct is None or coverage_pct != coverage_pct or coverage_pct < LOW_MIN


WITHHELD_TEXT = "withheld (<50% coverage)"
