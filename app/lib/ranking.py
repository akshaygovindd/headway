"""Ranking lines by a metric, only where their coverage is comparable."""

from dataclasses import dataclass

from lib.coverage import coverage_badge

MAX_COVERAGE_SPREAD = 15.0


@dataclass(frozen=True)
class RankedLine:
    line: str
    value: float | None
    coverage_pct: float | None
    group: int | None  # comparable group (0 = best covered); None when not ranked
    rank: int | None  # rank within the group, 1 = smallest value


@dataclass(frozen=True)
class Ranking:
    lines: list[RankedLine]
    notes: list[str]


def rank_lines(rows: list[tuple[str, float | None, float | None]]) -> Ranking:
    """Rank (line, value, coverage_pct) rows; a smaller value (less deviation) ranks first.

    Lines below 50% coverage are excluded. The rest are split into groups: a group starts at
    the best covered line left and holds every line within 15 coverage points of it. Lines
    are ranked only inside their group; when there are several groups the gap is stated.
    """
    usable = [
        r for r in rows if r[1] is not None and r[2] is not None and coverage_badge(r[2]).rankable
    ]
    excluded = [r for r in rows if r not in usable]
    by_cov = sorted(usable, key=lambda r: (-(r[2] or 0.0), r[0]))
    groups: list[list[tuple[str, float | None, float | None]]] = []
    for row in by_cov:
        if groups and (groups[-1][0][2] or 0.0) - (row[2] or 0.0) <= MAX_COVERAGE_SPREAD:
            groups[-1].append(row)
        else:
            groups.append([row])

    out: list[RankedLine] = []
    notes: list[str] = []
    for g, members in enumerate(groups):
        ordered = sorted(members, key=lambda r: (r[1] if r[1] is not None else 0.0, r[0]))
        out += [RankedLine(r[0], r[1], r[2], g, i + 1) for i, r in enumerate(ordered)]
        if g > 0:
            prev = groups[g - 1]
            gap = (prev[-1][2] or 0.0) - (members[0][2] or 0.0)
            notes.append(
                f"Coverage gap of {gap:.0f} points: {', '.join(m[0] for m in members)} "
                f"is not ranked against {', '.join(m[0] for m in prev)}."
            )
    out += [RankedLine(r[0], r[1], r[2], None, None) for r in sorted(excluded)]
    if excluded:
        names = ", ".join(r[0] for r in sorted(excluded))
        notes.append(f"Not ranked (insufficient data, coverage below 50%): {names}.")
    return Ranking(out, notes)
