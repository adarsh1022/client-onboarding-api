"""The mapping eval: the case file is sound, the scoring is right, and the heuristic
suggester has not got worse than its recorded baseline."""

import json
from collections import Counter
from datetime import date

import pytest

from app.ingest.heuristic import HeuristicSuggester
from app.ingest.suggester import ColumnSuggestion, SuggestionError
from evals import run_mapping_eval as mapping_eval
from evals.run_mapping_eval import BASELINE_PATH, VALID_EXPECTED, Case, Report, Result


@pytest.fixture(scope="module")
def cases():
    return mapping_eval.load_cases()


# --- the case file ------------------------------------------------------------------------


def test_cases_are_well_formed(cases):
    assert 55 <= len(cases) <= 80
    assert len({c.id for c in cases}) == len(cases)
    assert {c.expected for c in cases} <= VALID_EXPECTED
    assert {c.split for c in cases} == {"dev", "holdout"}
    assert all(c.header.strip() and c.samples for c in cases)


def test_cases_cover_every_field_and_a_good_share_of_none(cases):
    counts = Counter(c.expected for c in cases)
    assert all(counts[field] >= 3 for field in VALID_EXPECTED), counts
    assert counts["none"] >= len(cases) * 0.25


def test_cases_include_the_tricky_headers(cases):
    headers = {c.header for c in cases}
    assert {"Mob", "Cntry", "Date of Joining", "Ref Code", "Remarks"} <= headers
    scripts = {
        "hindi": any("ऀ" <= ch <= "ॿ" for h in headers for ch in h),
        "german": {"Anmeldedatum", "Telefonnummer", "Land"} <= headers,
        "spanish": {"Nombre", "Teléfono", "País"} <= headers,
    }
    assert all(scripts.values()), scripts


# --- the scoring --------------------------------------------------------------------------


class Scripted:
    """Answers each header from a dict; a missing header gets no suggestion at all."""

    def __init__(self, answers, error=None):
        self.answers, self.error = answers, error

    def suggest(self, columns, fields):
        if self.error:
            raise self.error
        return [
            ColumnSuggestion(c.header, self.answers[c.header], 0.9, "scripted")
            for c in columns
            if c.header in self.answers
        ]


def tiny_cases():
    return [
        Case("a", "dev", "Mob", ["9876543210"], "phone"),
        Case("b", "dev", "Notes", ["hi"], "none"),
        Case("c", "holdout", "Mail", ["a@x.com"], "email"),
        Case("d", "holdout", "Ref", ["12"], "none"),
    ]


def test_scoring_counts_accuracy_none_accuracy_splits_and_mistakes():
    suggester = Scripted({"Mob": "phone", "Notes": "name", "Mail": None, "Ref": None})
    report = mapping_eval.run(suggester, tiny_cases())

    assert report.accuracy() == (2, 4)  # Mob and Ref right; Notes and Mail wrong
    assert report.none_accuracy() == (1, 2)
    assert report.field_accuracy() == (1, 2)
    assert report.split_accuracy("dev") == (1, 2)
    assert report.split_accuracy("holdout") == (1, 2)
    assert [(m.case.id, m.got) for m in report.mistakes] == [("b", "name"), ("c", "none")]


def test_a_suggester_that_says_nothing_is_scored_as_none():
    report = mapping_eval.run(Scripted({}), tiny_cases())
    assert report.none_accuracy() == (2, 2)
    assert report.field_accuracy() == (0, 2)


def test_suggester_errors_are_wrong_even_for_none_cases():
    report = mapping_eval.run(Scripted({}, error=SuggestionError("down")), tiny_cases())
    assert report.accuracy() == (0, 4)
    assert report.errors == 4


def test_samples_are_masked_before_the_suggester_sees_them():
    seen = []

    class Spy:
        def suggest(self, columns, fields):
            seen.extend(columns)
            return []

    mapping_eval.run_case(Spy(), Case("x", "dev", "Mail", ["asha@x.com", "98765 43210"], "email"))

    assert seen[0].samples == ["<email>", "99999 99999"]


def test_report_formats_mention_the_numbers():
    report = Report([Result(c, c.expected) for c in tiny_cases()])
    assert "4/4 = 100.0%" in mapping_eval.format_text(report, "x")
    assert "| Accuracy (all cases) | 4/4 = 100.0% |" in mapping_eval.format_markdown(
        report, "x", "m", date(2026, 1, 2)
    )


# --- the regression gate ------------------------------------------------------------------


def test_heuristic_is_not_worse_than_its_baseline(cases):
    baseline = json.loads(BASELINE_PATH.read_text())["heuristic"]
    correct, total = mapping_eval.run(HeuristicSuggester(), cases).accuracy()

    assert total == baseline["total"], "cases changed: rerun the eval with --update-baseline"
    assert correct >= baseline["correct"], f"{correct}/{total}, baseline {baseline['correct']}"


def test_cli_passes_against_the_baseline_and_fails_on_a_regression(tmp_path, capsys):
    ok = mapping_eval.main(["--suggester", "heuristic", "--baseline", str(BASELINE_PATH)])
    assert ok == 0 and "OK:" in capsys.readouterr().out

    baseline = json.loads(BASELINE_PATH.read_text())["heuristic"]
    demanding = tmp_path / "baseline.json"
    demanding.write_text(
        json.dumps({"heuristic": {"correct": baseline["total"] + 1, "total": baseline["total"]}})
    )
    assert mapping_eval.main(["--suggester", "heuristic", "--baseline", str(demanding)]) == 1
    assert "REGRESSION" in capsys.readouterr().err


def test_cli_min_accuracy_and_update_baseline(tmp_path, capsys):
    assert mapping_eval.main(["--suggester", "heuristic", "--min-accuracy", "0.5"]) == 0
    assert mapping_eval.main(["--suggester", "heuristic", "--min-accuracy", "1.01"]) == 1

    target = tmp_path / "fresh.json"
    code = mapping_eval.main(
        ["--suggester", "heuristic", "--baseline", str(target), "--update-baseline"]
    )
    written = json.loads(target.read_text())["heuristic"]
    assert code == 0 and written["total"] == len(mapping_eval.load_cases())
    capsys.readouterr()


def test_cli_baseline_needs_the_same_number_of_cases(tmp_path, capsys):
    stale = tmp_path / "stale.json"
    stale.write_text(json.dumps({"heuristic": {"correct": 1, "total": 3}}))
    assert mapping_eval.main(["--suggester", "heuristic", "--baseline", str(stale)]) == 1
    assert "cases changed" in capsys.readouterr().err


def test_claude_without_a_key_is_reported_not_called(capsys):
    assert mapping_eval.main(["--suggester", "claude"]) == 2
    assert "ANTHROPIC_API_KEY" in capsys.readouterr().err
