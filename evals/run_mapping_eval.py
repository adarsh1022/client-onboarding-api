"""Score a MappingSuggester on evals/mapping_cases.jsonl.

Each case is one column: a header, a few sample values and the field it should map to
("name", "email", "phone", "country", "signup_date") or "none". The runner feeds every case
to the suggester the way the pipeline would (samples masked and de-duplicated, all five
fields open) and reports accuracy, accuracy on the "none" cases, and every mistake.

    python evals/run_mapping_eval.py --suggester heuristic
    python evals/run_mapping_eval.py --suggester heuristic --baseline evals/baseline.json
    python evals/run_mapping_eval.py --suggester ollama      # needs Ollama running (free)
    python evals/run_mapping_eval.py --suggester claude      # needs ANTHROPIC_API_KEY (PAID)

Exit status: 0 ok, 1 accuracy below the baseline or --min-accuracy, 2 can't run.
"""

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

# Make `app` importable when this file is run directly (python evals/run_mapping_eval.py).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import Settings  # noqa: E402
from app.ingest.factory import build_suggester  # noqa: E402
from app.ingest.mapping import HEADER_ALIASES  # noqa: E402
from app.ingest.suggester import MappingSuggester, SuggestionError, sample_columns  # noqa: E402

CASES_PATH = Path(__file__).parent / "mapping_cases.jsonl"
BASELINE_PATH = Path(__file__).parent / "baseline.json"
VALID_EXPECTED = {*HEADER_ALIASES, "none"}


@dataclass
class Case:
    id: str
    split: str
    header: str
    samples: list[str]
    expected: str
    note: str = ""


@dataclass
class Result:
    case: Case
    got: str  # a field, "none", or "error"
    confidence: float = 0.0
    reason: str = ""

    @property
    def correct(self) -> bool:
        return self.got == self.case.expected


@dataclass
class Report:
    results: list[Result] = field(default_factory=list)

    @staticmethod
    def _ratio(results: list[Result]) -> tuple[int, int]:
        return sum(r.correct for r in results), len(results)

    def accuracy(self) -> tuple[int, int]:
        return self._ratio(self.results)

    def none_accuracy(self) -> tuple[int, int]:
        return self._ratio([r for r in self.results if r.case.expected == "none"])

    def field_accuracy(self) -> tuple[int, int]:
        return self._ratio([r for r in self.results if r.case.expected != "none"])

    def split_accuracy(self, split: str) -> tuple[int, int]:
        return self._ratio([r for r in self.results if r.case.split == split])

    @property
    def mistakes(self) -> list[Result]:
        return [r for r in self.results if not r.correct]

    @property
    def errors(self) -> int:
        return sum(r.got == "error" for r in self.results)


def load_cases(path: Path = CASES_PATH) -> list[Case]:
    cases = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if line.strip():
            try:
                cases.append(Case(**json.loads(line)))
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{path.name}:{line_number}: {exc}") from exc
    return cases


def run_case(suggester: MappingSuggester, case: Case) -> Result:
    rows = [{case.header: value} for value in case.samples]
    columns = sample_columns([case.header], rows)  # masked, exactly as the pipeline sends them
    try:
        suggestions = suggester.suggest(columns, list(HEADER_ALIASES))
    except SuggestionError as exc:
        return Result(case, "error", reason=str(exc))
    for suggestion in suggestions:
        if suggestion.header == case.header:
            return Result(
                case, suggestion.field or "none", suggestion.confidence, suggestion.reason
            )
    return Result(case, "none", reason="the suggester returned nothing for this column")


def run(suggester: MappingSuggester, cases: list[Case]) -> Report:
    return Report([run_case(suggester, case) for case in cases])


def percent(counts: tuple[int, int]) -> str:
    correct, total = counts
    return f"{correct}/{total} = {100 * correct / total:.1f}%" if total else "n/a"


def format_text(report: Report, label: str) -> str:
    lines = [
        f"Suggester: {label}    Cases: {len(report.results)}",
        f"Accuracy:           {percent(report.accuracy())}",
        f"  dev cases:        {percent(report.split_accuracy('dev'))}",
        f"  holdout cases:    {percent(report.split_accuracy('holdout'))}",
        f'Accuracy on "none": {percent(report.none_accuracy())}',
        f"Accuracy on fields: {percent(report.field_accuracy())}",
    ]
    if report.errors:
        lines.append(f"Suggester errors:   {report.errors} (counted as wrong)")
    lines.append(f"Mistakes ({len(report.mistakes)}):")
    for r in report.mistakes:
        lines.append(
            f"  {r.case.id} [{r.case.split}] {r.case.header!r}: expected {r.case.expected}, "
            f"got {r.got} (confidence {r.confidence:.2f})"
        )
    return "\n".join(lines)


def format_markdown(report: Report, label: str, model: str, today: date) -> str:
    rows = [
        ("Accuracy (all cases)", report.accuracy()),
        ("dev cases", report.split_accuracy("dev")),
        ("holdout cases", report.split_accuracy("holdout")),
        ('Accuracy on "none" cases', report.none_accuracy()),
        ("Accuracy on field cases", report.field_accuracy()),
    ]
    lines = [
        f"### {label}, {today.isoformat()}",
        "",
        f"Model: {model}. Cases: {len(report.results)}.",
        "",
        "| Metric | Result |",
        "|---|---|",
        *(f"| {name} | {percent(counts)} |" for name, counts in rows),
        "",
        f"Mistakes ({len(report.mistakes)}):",
        "",
        "| Case | Header | Expected | Got | Confidence |",
        "|---|---|---|---|---|",
        *(
            f"| {r.case.id} ({r.case.split}) | {r.case.header} | {r.case.expected} | {r.got} "
            f"| {r.confidence:.2f} |"
            for r in report.mistakes
        ),
    ]
    return "\n".join(lines)


def describe(name: str, config: Settings) -> tuple[str, str]:
    """(label, model) shown in reports."""
    if name == "ollama":
        return "ollama", config.ollama_model
    if name == "claude":
        return "claude", config.llm_model
    return "heuristic", "none (rules only)"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--suggester", choices=["heuristic", "ollama", "claude"], required=True)
    parser.add_argument("--cases", type=Path, default=CASES_PATH)
    parser.add_argument("--baseline", type=Path, help="fail if accuracy falls below this file")
    parser.add_argument(
        "--update-baseline", action="store_true", help="write the result to --baseline"
    )
    parser.add_argument("--min-accuracy", type=float, help="fail below this accuracy (0-1)")
    parser.add_argument("--markdown", action="store_true", help="print a RESULTS.md section")
    args = parser.parse_args(argv)

    config = Settings(suggester=args.suggester)  # env and .env still supply URLs, models, keys
    suggester = build_suggester(config)
    if suggester is None:
        print(
            "The claude suggester needs ANTHROPIC_API_KEY (and it is a paid API).", file=sys.stderr
        )
        return 2

    label, model = describe(args.suggester, config)
    report = run(suggester, load_cases(args.cases))
    if args.markdown:
        print(format_markdown(report, label, model, date.today()))
    else:
        print(format_text(report, label))

    correct, total = report.accuracy()
    if report.errors == total:
        print("\nEvery case errored: is the suggester reachable?", file=sys.stderr)
        return 2

    if args.baseline and args.update_baseline:
        baseline = json.loads(args.baseline.read_text()) if args.baseline.exists() else {}
        baseline[args.suggester] = {"correct": correct, "total": total}
        args.baseline.write_text(json.dumps(baseline, indent=2) + "\n")
        print(f"\nBaseline for {args.suggester} set to {correct}/{total}.")
        return 0

    if args.baseline:
        baseline = json.loads(args.baseline.read_text()).get(args.suggester)
        if baseline is None:
            print(f"\nNo baseline for {args.suggester} in {args.baseline}.", file=sys.stderr)
            return 2
        if total != baseline["total"]:
            print(
                f"\nThe cases changed ({total} now, {baseline['total']} in the baseline): "
                "re-run with --update-baseline after checking the result.",
                file=sys.stderr,
            )
            return 1
        if correct < baseline["correct"]:
            print(
                f"\nREGRESSION: {correct}/{total} correct, "
                f"baseline is {baseline['correct']}/{total}.",
                file=sys.stderr,
            )
            return 1
        print(f"\nOK: {correct}/{total} correct, baseline is {baseline['correct']}/{total}.")

    if args.min_accuracy is not None and correct / total < args.min_accuracy:
        print(f"\nBelow --min-accuracy {args.min_accuracy}.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
