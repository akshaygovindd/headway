"""Alert classifier pilot (Release 3, C12): three models on hand-labelled alert headers.

Nothing in production depends on this. It reads ``evals/alert_labels.csv`` (written by
scripts/export_alerts_for_labelling.py and labelled by hand, see evals/ALERT_LABELLING.md) and
compares, on the same stratified 5-fold split (fixed seed):

1. a rule baseline (GTFS effect plus keywords),
2. TF-IDF + logistic regression (scikit-learn), trained on the four training folds,
3. an LLM zero-shot classifier behind a small ``complete(instruction, text)`` interface, run only
   when OPENAI_API_KEY is set (otherwise reported as not run).

Precision, recall and F1 come from the out-of-fold predictions. The run refuses to report until
every row has one of the six labels.
"""

import argparse
import csv
import json
import os
import re
import sys
import urllib.error
import urllib.request
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import confusion_matrix, precision_recall_fscore_support
from sklearn.model_selection import StratifiedKFold

ROOT = Path(__file__).resolve().parents[2]
CLASSES = (
    "shuttle_diversion",
    "delay",
    "station_or_stop_closure",
    "accessibility",
    "schedule_change",
    "other",
)
N_FOLDS = 5
SEED = 7
LLM_DEFAULT_MODEL = "gpt-4o-mini"
LLM_INSTRUCTION = (
    "Classify one public-transit service alert header into exactly one class: "
    + ", ".join(CLASSES)
    + ". shuttle_diversion: buses replace trains or trains are diverted. delay: trains late or "
    "irregular. station_or_stop_closure: a station, stop or entrance is closed or bypassed. "
    "accessibility: an elevator or escalator is out or back. schedule_change: planned timetable or "
    "frequency changes. other: anything else. Answer with the class name only."
)


class LabelError(ValueError):
    """The label file is incomplete or holds an unknown label."""


@dataclass(frozen=True)
class Example:
    alert_id: str
    effect: str
    header_text: str
    label: str


def load_labels(path: Path) -> list[Example]:
    """The labelled rows; raises LabelError naming every empty or unknown label."""
    with path.open(newline="") as f:
        rows = list(csv.DictReader(f))
    problems = []
    for i, r in enumerate(rows, start=2):  # line 1 is the header
        label = (r.get("label") or "").strip()
        if label not in CLASSES:
            shown = label or "empty"
            problems.append(f"line {i} (alert {r.get('alert_id', '?')}): {shown}")
    if not rows:
        raise LabelError(f"{path} has no rows")
    if problems:
        raise LabelError(
            f"{len(problems)} of {len(rows)} rows have no valid label "
            f"(one of {', '.join(CLASSES)}):\n  " + "\n  ".join(problems[:20])
        )
    return [
        Example(r["alert_id"], r.get("effect", ""), r["header_text"], r["label"].strip())
        for r in rows
    ]


def make_folds(labels: list[str], seed: int = SEED) -> list[tuple[np.ndarray, np.ndarray]]:
    """Stratified 5-fold (train, test) index pairs, shared by every model."""
    skf = StratifiedKFold(n_splits=N_FOLDS, shuffle=True, random_state=seed)
    y = np.array(labels)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # a class with fewer than 5 examples only warns
        return [(tr, te) for tr, te in skf.split(np.zeros(len(y)), y)]


# ---- model 1: rules ------------------------------------------------------------------------

_EFFECT_CLASS = {
    "DETOUR": "shuttle_diversion",
    "SIGNIFICANT_DELAYS": "delay",
    "ACCESSIBILITY_ISSUE": "accessibility",
    "STOP_MOVED": "station_or_stop_closure",
    "REDUCED_SERVICE": "schedule_change",
    "MODIFIED_SERVICE": "schedule_change",
    "ADDITIONAL_SERVICE": "schedule_change",
}
_KEYWORDS = (
    ("shuttle_diversion", r"shuttle|bus(es)? (replac|substitut)|replacement bus|detour|diverted"),
    ("station_or_stop_closure", r"clos(ed|ure)|bypass|skip(ping|s)?\b|not stopping"),
    ("accessibility", r"elevator|escalator|accessib|wheelchair"),
    ("delay", r"delay|late\b|slow|disabled train|signal problem"),
    ("schedule_change", r"schedule|every \d+ min|holiday|weekend|frequency|first train|last train"),
)


def rule_predict(effect: str, text: str) -> str:
    """Strong GTFS effects decide; for the vague ones (other, unknown, no service) keywords do."""
    if effect in _EFFECT_CLASS:
        return _EFFECT_CLASS[effect]
    lowered = text.lower()
    for label, pattern in _KEYWORDS:
        if re.search(pattern, lowered):
            return label
    return "other"


# ---- model 2: TF-IDF + logistic regression --------------------------------------------------


def tfidf_lr_fold(
    train_text: list[str], train_y: list[str], test_text: list[str], seed: int = SEED
) -> np.ndarray:
    """Class probabilities (columns in CLASSES order; 0 for a class absent from training)."""
    vec = TfidfVectorizer(lowercase=True, ngram_range=(1, 2), sublinear_tf=True, min_df=1)
    x = vec.fit_transform(train_text)
    clf = LogisticRegression(C=10.0, max_iter=1000, random_state=seed)
    clf.fit(x, train_y)
    proba = clf.predict_proba(vec.transform(test_text))
    out = np.zeros((len(test_text), len(CLASSES)))
    for col, cls in enumerate(clf.classes_):
        out[:, CLASSES.index(str(cls))] = proba[:, col]
    return out


# ---- model 3: LLM zero-shot -----------------------------------------------------------------


class LlmClient(Protocol):
    model: str

    def complete(self, instruction: str, text: str) -> str: ...


class OpenAIClient:
    """Chat completions over HTTPS with the standard library, like the incident summaries."""

    def __init__(self, api_key: str, model: str | None = None, timeout: float = 30.0) -> None:
        self._key = api_key
        self.model = model or os.environ.get("HEADWAY_ALERT_LLM_MODEL", LLM_DEFAULT_MODEL)
        self._timeout = timeout

    def complete(self, instruction: str, text: str) -> str:
        body = json.dumps(
            {
                "model": self.model,
                "temperature": 0,
                "max_completion_tokens": 20,
                "messages": [
                    {"role": "system", "content": instruction},
                    {"role": "user", "content": text},
                ],
            }
        ).encode()
        req = urllib.request.Request(  # noqa: S310 - fixed https endpoint
            "https://api.openai.com/v1/chat/completions",
            data=body,
            headers={"Authorization": f"Bearer {self._key}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:  # noqa: S310
                doc = json.loads(resp.read())
            return str(doc["choices"][0]["message"]["content"]).strip()
        except (urllib.error.URLError, TimeoutError, KeyError, IndexError, ValueError) as exc:
            raise RuntimeError(type(exc).__name__) from exc


def parse_llm_label(answer: str) -> str | None:
    """The class named by the answer, or None when it is not exactly one class."""
    token = answer.strip().strip("`'\".").lower().replace(" ", "_").replace("-", "_")
    return token if token in CLASSES else None


def llm_predict(client: LlmClient, text: str) -> tuple[str, bool]:
    """(label, valid): an unparseable answer or a failed call counts as ``other``, invalid."""
    try:
        parsed = parse_llm_label(client.complete(LLM_INSTRUCTION, text))
    except RuntimeError:
        parsed = None
    return (parsed or "other", parsed is not None)


# ---- evaluation -----------------------------------------------------------------------------


@dataclass
class ModelResult:
    name: str
    predictions: list[str] | None  # None: not run
    note: str = ""
    invalid: int = 0


def evaluate(
    examples: list[Example], *, client: LlmClient | None = None, seed: int = SEED
) -> tuple[list[ModelResult], np.ndarray]:
    """Out-of-fold predictions of every model on the same folds, and the LR probabilities."""
    y = [e.label for e in examples]
    text = [e.header_text for e in examples]
    folds = make_folds(y, seed)
    n = len(examples)

    rules = [rule_predict(e.effect, e.header_text) for e in examples]

    proba = np.zeros((n, len(CLASSES)))
    for tr, te in folds:
        proba[te] = tfidf_lr_fold(
            [text[i] for i in tr], [y[i] for i in tr], [text[i] for i in te], seed
        )
    lr = [CLASSES[int(k)] for k in proba.argmax(axis=1)]

    results = [
        ModelResult("Rule baseline (GTFS effect + keywords)", rules),
        ModelResult("TF-IDF + logistic regression", lr),
    ]
    if client is None:
        results.append(
            ModelResult("LLM zero-shot", None, "not run: no OPENAI_API_KEY (no key, no calls)")
        )
    else:
        out = [llm_predict(client, t) for t in text]  # zero-shot: every row, folds do not matter
        results.append(
            ModelResult(
                f"LLM zero-shot ({client.model})",
                [label for label, _ in out],
                invalid=sum(1 for _, ok in out if not ok),
            )
        )
    return results, proba


def scores(y_true: list[str], y_pred: list[str]) -> dict[str, Any]:
    labels = list(CLASSES)
    p, r, f, support = precision_recall_fscore_support(
        y_true, y_pred, labels=labels, zero_division=0
    )
    mp, mr, mf, _ = precision_recall_fscore_support(
        y_true, y_pred, labels=labels, average="macro", zero_division=0
    )
    return {
        "per_class": {
            c: (float(p[i]), float(r[i]), float(f[i]), int(support[i]))
            for i, c in enumerate(labels)
        },
        "macro": (float(mp), float(mr), float(mf)),
        "accuracy": float(np.mean(np.array(y_true) == np.array(y_pred))),
        "confusion": confusion_matrix(y_true, y_pred, labels=labels),
    }


def hardest(
    examples: list[Example], results: list[ModelResult], proba: np.ndarray, k: int = 5
) -> list[tuple[Example, list[str]]]:
    """Examples most models get wrong, then the lowest LR probability for the hand label."""
    ran = [r for r in results if r.predictions is not None]
    keyed = []
    for i, e in enumerate(examples):
        wrong = [r.name for r in ran if r.predictions and r.predictions[i] != e.label]
        gold_p = float(proba[i, CLASSES.index(e.label)])
        keyed.append((-len(wrong), gold_p, i, wrong))
    keyed.sort()
    return [(examples[i], wrong) for _, _, i, wrong in keyed[:k]]


def _pct(x: float) -> str:
    return f"{100 * x:.0f}%"


def render_report(
    examples: list[Example], results: list[ModelResult], proba: np.ndarray, seed: int
) -> str:
    y = [e.label for e in examples]
    counts = {c: y.count(c) for c in CLASSES}
    lines = [
        "# Alert classifier (pilot)",
        "",
        "**Pilot.** Nothing in production depends on this classifier; it exists to learn whether "
        "alert text can be sorted into six classes better than by rules. Generated by "
        "`make alert-classifier` from the hand labels in `evals/alert_labels.csv`; do not edit.",
        "",
        f"- Rows: {len(examples)} distinct subway alerts, labelled by hand "
        "(guide: `evals/ALERT_LABELLING.md`).",
        "- Classes: " + ", ".join(f"{c} ({counts[c]})" for c in CLASSES) + ".",
        f"- Protocol: stratified {N_FOLDS}-fold cross-validation, seed {seed}, the same folds for "
        "every model; scores are from the out-of-fold predictions. The LLM is zero-shot, so it "
        "sees no training folds. Classes with fewer than 5 examples cannot be stratified across "
        "all folds; per-class numbers on small classes are noisy.",
        "- Small sample: differences of a few points are not evidence of a better model.",
        "",
        "## Summary",
        "",
        "| Model | Precision (macro) | Recall (macro) | F1 (macro) | Accuracy |",
        "|---|---|---|---|---|",
    ]
    for r in results:
        if r.predictions is None:
            lines.append(f"| {r.name} | not run | not run | not run | not run |")
            continue
        s = scores(y, r.predictions)
        p, rc, f = s["macro"]
        extra = f" ({r.invalid} unparseable answers counted as `other`)" if r.invalid else ""
        lines.append(
            f"| {r.name}{extra} | {_pct(p)} | {_pct(rc)} | {_pct(f)} | {_pct(s['accuracy'])} |"
        )
    for r in results:
        if r.predictions is None:
            lines += ["", f"{r.name}: {r.note}."]
    for r in results:
        if r.predictions is None:
            continue
        s = scores(y, r.predictions)
        lines += [
            "",
            f"## {r.name}",
            "",
            "| Class | Precision | Recall | F1 | Support |",
            "|---|---|---|---|---|",
        ]
        for c, (p, rc, f, sup) in s["per_class"].items():
            lines.append(f"| {c} | {_pct(p)} | {_pct(rc)} | {_pct(f)} | {sup} |")
        lines += [
            "",
            "Confusion matrix (rows: hand label, columns: predicted):",
            "",
            "| | " + " | ".join(CLASSES) + " |",
            "|---|" + "---|" * len(CLASSES),
        ]
        for i, c in enumerate(CLASSES):
            lines.append(
                f"| **{c}** | " + " | ".join(str(int(v)) for v in s["confusion"][i]) + " |"
            )
    lines += [
        "",
        "## Five hardest examples",
        "",
        "Most models wrong first, then the lowest "
        "logistic-regression probability for the hand label.",
        "",
    ]
    for e, wrong in hardest(examples, results, proba):
        header = e.header_text.replace("\n", " ").replace("|", "\\|")
        lines.append(
            f"- Alert {e.alert_id} (effect {e.effect or 'none'}), hand label "
            f'`{e.label}`: "{header}" Wrong in: {", ".join(wrong) or "no model"}.'
        )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--labels", type=Path, default=ROOT / "evals" / "alert_labels.csv")
    p.add_argument("--out", type=Path, default=ROOT / "docs" / "ALERT_CLASSIFIER.md")
    p.add_argument("--seed", type=int, default=SEED)
    args = p.parse_args(argv)
    try:
        examples = load_labels(args.labels)
    except LabelError as exc:
        print(f"refusing to report: {exc}", file=sys.stderr)
        return 1
    key = os.environ.get("OPENAI_API_KEY")
    client = OpenAIClient(key) if key else None
    results, proba = evaluate(examples, client=client, seed=args.seed)
    args.out.write_text(render_report(examples, results, proba, args.seed))
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
