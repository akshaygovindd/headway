"""Alert classifier pilot on a small synthetic labelled set (no network)."""

import csv
from pathlib import Path

import numpy as np
import pytest
from headway_ml import alert_classifier as ac

TEMPLATES = {
    "shuttle_diversion": ["Shuttle buses replace {l} trains between {a} and {b}",
                          "{l} Line trains diverted, take the replacement bus from {a}"],
    "delay": ["{l} Line trains are delayed about {n} minutes due to a signal problem",
              "Delays of {n} minutes on the {l} Line near {a}"],
    "station_or_stop_closure": ["{a} station is closed, trains bypass {a}",
                                "The {a} entrance is closed until further notice"],
    "accessibility": ["The elevator at {a} is out of service",
                      "Escalator at {a} is unavailable for maintenance"],
    "schedule_change": ["{l} Line runs every {n} minutes on weekends",
                        "Holiday schedule in effect on the {l} Line"],
    "other": ["Please be careful of the platform edge at {a}",
              "Fare information: see the website for details about {a}"],
}  # fmt: skip
EFFECT = {"shuttle_diversion": "DETOUR", "delay": "SIGNIFICANT_DELAYS",
          "accessibility": "ACCESSIBILITY_ISSUE"}  # fmt: skip
STOPS = ["Alewife", "Davis", "Porter", "Harvard", "Kendall", "Park", "Wollaston", "Quincy"]


def write(path: Path, per_class: int = 10, blank: int = 0, bad: str | None = None) -> Path:
    rows = []
    for ci, cls in enumerate(ac.CLASSES):
        for i in range(per_class):
            t = TEMPLATES[cls][i % 2].format(
                l=("Red", "Orange", "Blue")[i % 3], a=STOPS[(i + ci) % 8],
                b=STOPS[(i + ci + 3) % 8], n=5 + i,
            )  # fmt: skip
            rows.append(
                {
                    "alert_id": f"{cls}-{i}",
                    "effect": EFFECT.get(cls, "OTHER_EFFECT"),
                    "cause": "",
                    "route_ids": "Red",
                    "header_text": t,
                    "label": cls,
                }
            )
    for i in range(blank):
        rows[i]["label"] = ""
    if bad:
        rows[-1]["label"] = bad
    with path.open("w", newline="") as f:
        w = csv.DictWriter(
            f, fieldnames=["alert_id", "effect", "cause", "route_ids", "header_text", "label"]
        )
        w.writeheader()
        w.writerows(rows)
    return path


def test_refuses_until_every_row_has_a_valid_label(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(ac.LabelError, match="2 of 60 rows"):
        ac.load_labels(write(tmp_path / "a.csv", blank=2))
    with pytest.raises(ac.LabelError, match="not_a_class"):
        ac.load_labels(write(tmp_path / "b.csv", bad="not_a_class"))
    out = tmp_path / "report.md"
    assert ac.main(["--labels", str(write(tmp_path / "c.csv", blank=1)), "--out", str(out)]) == 1
    assert not out.exists()
    assert "refusing to report" in capsys.readouterr().err


def test_folds_are_stratified_seeded_and_cover_every_row(tmp_path: Path) -> None:
    y = [e.label for e in ac.load_labels(write(tmp_path / "a.csv"))]
    folds = ac.make_folds(y)
    assert len(folds) == 5
    assert sorted(np.concatenate([te for _, te in folds])) == list(range(len(y)))
    for _, te in folds:
        assert {y[i] for i in te} == set(ac.CLASSES)  # every class in every test fold
    again = ac.make_folds(y)
    assert all(np.array_equal(a[1], b[1]) for a, b in zip(folds, again, strict=True))
    other = ac.make_folds(y, seed=99)
    assert not all(np.array_equal(a[1], b[1]) for a, b in zip(folds, other, strict=True))


def test_rule_baseline() -> None:
    assert ac.rule_predict("DETOUR", "anything") == "shuttle_diversion"
    assert ac.rule_predict("OTHER_EFFECT", "The elevator at Park is out") == "accessibility"
    assert ac.rule_predict("UNKNOWN_EFFECT", "Delays of 10 minutes") == "delay"
    assert ac.rule_predict("OTHER_EFFECT", "Mind the gap") == "other"


def test_tfidf_lr_learns_a_separable_set(tmp_path: Path) -> None:
    ex = ac.load_labels(write(tmp_path / "a.csv"))
    results, proba = ac.evaluate(ex, client=None)
    lr = next(r for r in results if r.name.startswith("TF-IDF"))
    assert lr.predictions is not None
    s = ac.scores([e.label for e in ex], lr.predictions)
    assert s["macro"][2] > 0.8
    assert np.allclose(proba.sum(axis=1), 1.0)


def test_metrics_on_a_hand_checked_case() -> None:
    y_true = ["delay", "delay", "other", "other", "other"]
    y_pred = ["delay", "other", "other", "other", "delay"]
    s = ac.scores(y_true, y_pred)
    p, r, f, support = s["per_class"]["delay"]
    assert (p, r, support) == (0.5, 0.5, 2)
    assert f == 0.5
    o = s["per_class"]["other"]
    assert o[0] == pytest.approx(2 / 3) and o[1] == pytest.approx(2 / 3)
    i_d, i_o = ac.CLASSES.index("delay"), ac.CLASSES.index("other")
    assert s["confusion"][i_d, i_d] == 1 and s["confusion"][i_d, i_o] == 1
    assert s["confusion"][i_o, i_d] == 1 and s["confusion"][i_o, i_o] == 2


class FakeClient:
    model = "fake-llm"

    def __init__(self, truth: dict[str, str]) -> None:
        self.truth = truth
        self.calls = 0

    def complete(self, instruction: str, text: str) -> str:
        self.calls += 1
        assert "Answer with the class name only" in instruction
        return "gibberish" if self.calls == 1 else self.truth[text]


def test_llm_parsing_and_invalid_answers(tmp_path: Path) -> None:
    assert ac.parse_llm_label(" `Shuttle_Diversion`. ") == "shuttle_diversion"
    assert ac.parse_llm_label("station or stop closure") == "station_or_stop_closure"
    assert ac.parse_llm_label("it is a delay") is None
    ex = ac.load_labels(write(tmp_path / "a.csv"))
    client = FakeClient({e.header_text: e.label for e in ex})
    results, _ = ac.evaluate(ex, client=client)
    llm = results[2]
    assert llm.predictions is not None and llm.invalid == 1 and client.calls == len(ex)


def test_report_is_labelled_pilot_and_reports_llm_not_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    out = tmp_path / "report.md"
    assert ac.main(["--labels", str(write(tmp_path / "a.csv")), "--out", str(out)]) == 0
    text = out.read_text()
    assert "(pilot)" in text and "Nothing in production depends on" in text
    assert "not run" in text and "no OPENAI_API_KEY" in text
    assert "Confusion matrix" in text and "Five hardest examples" in text
    assert text.count("\n- Alert ") == 5
    for name in ("Rule baseline", "TF-IDF + logistic regression"):
        assert name in text
