"""Blocking check of an incident summary against its evidence (ADR-008).

A summary may state only what the evidence JSON of its incident carries. Two rules, both blocking:

* every number in the text must match an evidence value, within display rounding or after a
  listed unit conversion (seconds <-> minutes, seconds -> hours);
* a causal phrase (because, due to, caused by, as a result of, ...) is allowed only inside a quoted
  alert header that is in the evidence: the summary may repeat what an alert says, never infer.

Numbers are integers, decimals, percentages, clock times (14:05, 2 PM), ISO dates, durations
(``3 min``, ``210 s``, ``2 hours``), ordinals and spelled-out numbers from two upward (``one`` is
too common in prose to read as a number). A summary that fails either rule is rejected and the
deterministic template is used in its place.

A third rule covers direction words (improved, worsened, rose, fell, higher, slower, ...): each
must agree with the sign of a related change in the evidence (deviation against its baseline, a
current value against the previous one). Evidence without any change rejects every direction word.

Signs. A figure written with a sign ("-86 s", "−86 s", "minus 86 s") must equal the signed
evidence value within rounding. An unsigned figure is read as positive, unless its clause carries a
sign word: early, ahead, faster, under schedule (a negative value) or late, behind, slower, over
schedule (a positive one); then it matches the absolute value of an evidence value of that sign
("86 s early" for -86, never "86 s late"). Those words themselves need a nonzero related value
(seconds or a change) of the matching sign in the evidence.
"""

import json
import re
from dataclasses import dataclass, field
from typing import Any

CAUSAL = re.compile(
    r"\b(because|due\s+to|caused\s+by|as\s+a\s+result\s+of|owing\s+to|on\s+account\s+of)\b",
    re.IGNORECASE,
)
# Direction words: +1 = the value went up, -1 = it went down; "bad" words carry the judgement
# (worse/slower/worsened = a delay-like value went up; better/faster/improved = it went down).
_UP = {"increased", "rose", "risen", "higher", "grew", "climbed", "up"}
_DOWN = {"decreased", "fell", "fallen", "dropped", "lower", "declined", "down"}
_WORSE = {"worsened", "worse", "slower", "deteriorated"}
_BETTER = {"improved", "better", "faster", "recovered"}
DIRECTION = re.compile(
    r"\b(" + "|".join(sorted(_UP | _DOWN | _WORSE | _BETTER)) + r")\b",
    re.IGNORECASE,
)
_NEG_WORDS = re.compile(r"\b(early|ahead|faster|under\s+schedule)\b", re.IGNORECASE)
_POS_WORDS = re.compile(r"\b(late|behind|slower|over\s+schedule)\b", re.IGNORECASE)
_CLAUSE_END = re.compile(r"[;!?]|\.(?!\d)|,(?!\d)|\b(?:and|but|while)\b", re.IGNORECASE)
_HIGHER_IS_BETTER = ("on_time", "within_band", "coverage")
_WORDS = {
    w: n
    for n, w in enumerate(
        [
            "zero",
            "one",
            "two",
            "three",
            "four",
            "five",
            "six",
            "seven",
            "eight",
            "nine",
            "ten",
            "eleven",
            "twelve",
            "thirteen",
            "fourteen",
            "fifteen",
            "sixteen",
            "seventeen",
            "eighteen",
            "nineteen",
            "twenty",
        ]  # fmt: skip
    )
    if n != 1
}
_WORDS.update({"thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80,
               "ninety": 90, "hundred": 100})  # fmt: skip
_UNITS = {
    "%": "pct", "percent": "pct", "per cent": "pct",
    "s": "s", "sec": "s", "secs": "s", "second": "s", "seconds": "s",
    "min": "min", "mins": "min", "minute": "min", "minutes": "min",
    "h": "h", "hr": "h", "hrs": "h", "hour": "h", "hours": "h",
    "day": "days", "days": "days",
}  # fmt: skip
_UNIT_ALT = "|".join(sorted((re.escape(u) for u in _UNITS), key=len, reverse=True))
_UNIT = rf"(?:{_UNIT_ALT})(?![A-Za-z])"
_WORD_ALT = "|".join(_WORDS)
_TOKEN = re.compile(
    r"(?P<date>\d{4}-\d{2}-\d{2})"
    r"|(?P<clock>\b(?P<ch>\d{1,2}):(?P<cm>\d{2})(?:\s?(?P<cap>[ap])\.?m\.?)?(?![\d]))"
    r"|(?P<clock12>\b(?P<c12h>\d{1,2})\s?(?P<c12ap>[ap])\.?m\b\.?)"
    r"|(?P<ordinal>\b\d+(?:st|nd|rd|th)\b)"
    rf"|(?P<num>(?<![\w.])(?P<sign>[-\u2212\u2013+]|minus\s+)?(?P<value>\d+(?:,\d{{3}})*(?:\.\d+)?)\s?(?P<unit>{_UNIT})?)"
    rf"|(?P<word>\b(?P<w>{_WORD_ALT})\b(?:\s+(?P<wunit>{_UNIT}))?)",
    re.IGNORECASE,
)
_CLOCK = re.compile(r"^\d{2}:\d{2}$")
_QUOTE = re.compile(r"\"([^\"]*)\"|“([^”]*)”")


@dataclass(frozen=True)
class Token:
    text: str
    kind: str  # number | clock | date
    value: float | int | str  # number: the value; clock: minutes of day; date: ISO text
    unit: str | None = None  # pct, s, min, h, days or None
    decimals: int = 0
    sign: int = 0  # -1 written with a minus, +1 with a plus, 0 unsigned
    start: int = 0  # offset in the text


def extract(text: str) -> list[Token]:
    """Every number-like token of the text, in order."""
    out: list[Token] = []
    for m in _TOKEN.finditer(text):
        if m.group("date"):
            out.append(Token(m.group(0), "date", m.group("date")))
        elif m.group("clock"):
            hour, minute = int(m.group("ch")), int(m.group("cm"))
            ap = (m.group("cap") or "").lower()
            if ap:
                hour = hour % 12 + (12 if ap == "p" else 0)
            if hour < 24 and minute < 60:
                out.append(Token(m.group(0), "clock", hour * 60 + minute))
            else:  # 25:99 is not a time: its parts are numbers
                out.append(Token(m.group(0), "number", float(hour)))
                out.append(Token(m.group(0), "number", float(minute)))
        elif m.group("clock12"):
            hour = int(m.group("c12h")) % 12 + (12 if m.group("c12ap").lower() == "p" else 0)
            out.append(Token(m.group(0), "clock", hour * 60))
        elif m.group("ordinal"):
            out.append(Token(m.group(0), "number", float(re.sub(r"\D", "", m.group(0)))))
        elif m.group("num"):
            raw = m.group("value").replace(",", "")
            decimals = len(raw.split(".")[1]) if "." in raw else 0
            unit = m.group("unit")
            mark = (m.group("sign") or "").strip().lower()
            sign = 0 if not mark else (1 if mark == "+" else -1)
            out.append(
                Token(
                    m.group(0),
                    "number",
                    float(raw),
                    _UNITS[unit.lower()] if unit else None,
                    decimals,
                    sign,
                    m.start(),
                )  # fmt: skip
            )
        else:
            unit = m.group("wunit")
            out.append(
                Token(
                    m.group(0),
                    "number",
                    float(_WORDS[m.group("w").lower()]),
                    _UNITS[unit.lower()] if unit else None,
                )  # fmt: skip
            )
    return out


def _unit_of(key: str) -> str | None:
    """The unit an evidence value carries, from its key's suffix."""
    if key.endswith("_pct"):
        return "pct"
    if key.endswith("_s"):
        return "s"
    if key in {"n_hours", "data_outage_hours_inside"}:
        return "h"
    if key == "baseline_days":
        return "days"
    return None  # a plain count (direction_id, ...)


@dataclass
class Allowed:
    """What a summary may say: numbers with their units, clock times, dates and alert headers."""

    numbers: list[tuple[float, str | None, bool]] = field(default_factory=list)  # value, unit, any
    clocks: set[int] = field(default_factory=set)
    dates: set[str] = field(default_factory=set)
    headers: list[str] = field(default_factory=list)
    # related changes: (signed change, True when a higher value is worse)
    changes: list[tuple[float, bool]] = field(default_factory=list)
    # nonzero seconds values and changes: what "early" and "late" must rest on
    signed: list[float] = field(default_factory=list)


def _change_kind(key: str) -> bool | None:
    """None when the key is not a change; else True when a higher value is worse."""
    if key == "deviation_over_baseline_s" or "change" in key or "delta" in key:
        return not any(m in key for m in _HIGHER_IS_BETTER)
    return None


def allowed_from(evidence: dict[str, Any]) -> Allowed:
    """Collect the values of an evidence document (the JSON of fct_incidents.evidence_json)."""
    allowed = Allowed()

    def add_text(text: str, *, header: bool) -> None:
        for tok in extract(text):
            if tok.kind == "clock":
                allowed.clocks.add(int(tok.value))
            elif tok.kind == "date":
                allowed.dates.add(str(tok.value))
            else:
                allowed.numbers.append((float(tok.value), None, True))
        if header:
            allowed.headers.append(text)

    def walk(node: Any, key: str, in_alert: bool) -> None:
        if isinstance(node, bool) or node is None:
            return
        if isinstance(node, int | float):
            allowed.numbers.append((float(node), _unit_of(key), False))
            worse_when_higher = _change_kind(key)
            if worse_when_higher is not None and node != 0:
                allowed.changes.append((float(node), worse_when_higher))
            if node != 0 and (_unit_of(key) == "s" or worse_when_higher is not None):
                allowed.signed.append(float(node))
        elif isinstance(node, str):
            if key == "header":
                add_text(node, header=True)
            elif _CLOCK.match(node):
                h, m = node.split(":")
                allowed.clocks.add(int(h) * 60 + int(m))
            elif key == "date" and re.fullmatch(r"\d{4}-\d{2}-\d{2}", node):
                allowed.dates.add(node)
            # names (line, effect, alert ids, rule names) are not numbers a summary may cite
        elif isinstance(node, dict):
            for k, v in node.items():
                walk(v, k, in_alert or k == "alerts")
        elif isinstance(node, list):
            for item in node:
                walk(item, key, in_alert)

    walk(evidence, "", False)
    return allowed


def _close(x: float, y: float, decimals: int) -> bool:
    """x is y shown with `decimals` decimals (rounding either way)."""
    return bool(abs(x - y) <= 0.5 * 10 ** (-decimals) + 1e-9)


def _clause(text: str, pos: int) -> str:
    """The clause of the text that holds offset `pos`."""
    start = 0
    for m in _CLAUSE_END.finditer(text):
        if m.end() <= pos:
            start = m.end()
        elif m.start() >= pos:
            return text[start : m.start()]
    return text[start:]


def _value_ok(tok: Token, x: float, v: float, unit: str | None, clause: str) -> bool:
    """x (the figure as written) is the evidence value v, read with the sign rules."""
    if tok.sign:
        return _close(tok.sign * x, v, tok.decimals)
    if not _close(x, abs(v), tok.decimals):
        return False
    neg_word = bool(_NEG_WORDS.search(clause))
    pos_word = bool(_POS_WORDS.search(clause))
    if v < 0:
        return neg_word  # unsigned reads positive: a negative value needs its sign word
    return not (v > 0 and unit == "s" and neg_word and not pos_word)  # "86 s early" is not +86


def _number_matches(tok: Token, allowed: Allowed, text: str = "") -> bool:
    x = float(tok.value)
    clause = _clause(text, tok.start)
    for value, unit, any_unit in allowed.numbers:
        if any_unit or tok.unit is None:
            if any_unit:  # a number quoted in an alert header: no sign to read
                if _close(x, value, tok.decimals):
                    return True
            elif _value_ok(tok, x, value, unit, clause):
                return True
            continue
        if tok.unit == unit and _value_ok(tok, x, value, unit, clause):
            return True
        # listed conversions: evidence in seconds, shown in minutes or hours
        if unit == "s" and tok.unit == "min" and _value_ok(tok, x, value / 60, unit, clause):
            return True
        if unit == "s" and tok.unit == "h" and _value_ok(tok, x, value / 3600, unit, clause):
            return True
        # evidence in hours shown in minutes or seconds
        if unit == "h" and tok.unit == "min" and _value_ok(tok, x, value * 60, unit, clause):
            return True
    return False


def _direction_ok(word: str, allowed: Allowed) -> bool:
    """The word agrees with the sign of at least one related change of the evidence."""
    w = word.lower()
    for delta, worse_when_higher in allowed.changes:
        up = delta > 0
        if w in _UP:
            good = up
        elif w in _DOWN:
            good = not up
        elif w in _WORSE:
            good = up == worse_when_higher
        else:  # better
            good = up != worse_when_higher
        if good:
            return True
    return False


def _sign_word_ok(word: str, allowed: Allowed) -> bool:
    """early, ahead, faster, under schedule need a negative related value; the others a positive."""
    negative = bool(_NEG_WORDS.fullmatch(word))
    return any((v < 0) == negative for v in allowed.signed)


@dataclass(frozen=True)
class CheckResult:
    ok: bool
    problems: tuple[str, ...] = ()

    @property
    def reason(self) -> str:
        return "; ".join(self.problems)


def _quoted_headers(text: str, headers: list[str]) -> list[tuple[int, int]]:
    """Spans of the double-quoted segments that are verbatim parts of an alert header."""
    spans = []
    norm = [" ".join(h.lower().split()) for h in headers]
    for m in _QUOTE.finditer(text):
        body = " ".join((m.group(1) or m.group(2) or "").lower().split())
        if body and any(body in h for h in norm):
            spans.append((m.start(), m.end()))
    return spans


def check(text: str, evidence: dict[str, Any] | str) -> CheckResult:
    """Number and causal check of a summary against its evidence. Blocking: not ok = reject."""
    doc = json.loads(evidence) if isinstance(evidence, str) else evidence
    allowed = allowed_from(doc)
    problems: list[str] = []
    for tok in extract(text):
        if tok.kind == "clock":
            good = int(tok.value) in allowed.clocks
        elif tok.kind == "date":
            good = str(tok.value) in allowed.dates
        else:
            good = _number_matches(tok, allowed, text)
        if not good:
            problems.append(f"number not in evidence: {tok.text.strip()!r}")
    spans = _quoted_headers(text, allowed.headers)
    for m in CAUSAL.finditer(text):
        if not any(a <= m.start() and m.end() <= b for a, b in spans):
            problems.append(f"causal wording outside a quoted alert header: {m.group(0)!r}")
    for m in DIRECTION.finditer(text):
        if not _direction_ok(m.group(0), allowed):
            problems.append(f"direction not supported by a change in the evidence: {m.group(0)!r}")
    for m in list(_NEG_WORDS.finditer(text)) + list(_POS_WORDS.finditer(text)):
        if not _sign_word_ok(m.group(0), allowed):
            problems.append(
                f"direction not supported by the sign of a related value in the evidence: "
                f"{m.group(0)!r}"
            )
    return CheckResult(not problems, tuple(problems))
