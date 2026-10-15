# Mapping eval results

Each case in [`mapping_cases.jsonl`](mapping_cases.jsonl) is one column (a header plus a few
sample values) with the field it should map to, or `none`. There are 67 cases: 41 that map to
a field and 26 that should be `none`, including Hindi, German and Spanish headers and
look-alikes such as `Ref Code` (ten digits that aren't a phone) and `Date of Birth` (a date
that isn't the signup date). Cases are tagged `dev` or `holdout`.

Run it with `python evals/run_mapping_eval.py --suggester heuristic|ollama|claude`.

## How much to trust these numbers

- **Small and self-written.** The same person wrote the heuristic and the cases, so the cases
  lean towards things the heuristic was built to handle. 67 cases is enough to catch
  regressions, not to predict accuracy on a client's real files.
- **`dev` is not a fair test.** The heuristic was developed against the `dev` cases, so its
  100% there is by construction. Look at `holdout`.
- **`holdout` is no longer untouched.** Its first run found two general flaws in the heuristic
  (see below), which I fixed. The holdout number after the fixes is therefore optimistic. The
  first-run number is the cleaner estimate.

## Heuristic suggester (free, offline)

### Current result (this is the CI baseline)

Model: none (rules only). Date: 2026-10-15. Cases: 67.

| Metric | Result |
|---|---|
| Accuracy (all cases) | 66/67 = 98.5% |
| dev cases | 37/37 = 100.0% |
| holdout cases | 29/30 = 96.7% |
| Accuracy on "none" cases | 25/26 = 96.2% |
| Accuracy on field cases | 41/41 = 100.0% |

Mistakes (1):

| Case | Header | Expected | Got | Confidence |
|---|---|---|---|---|
| c61 (holdout) | Fax | none | phone | 0.85 |

`Fax` holds phone-shaped numbers, and nothing in the header says it isn't a phone. This is a
real limit of rules, so it is left as a known mistake rather than special-cased.

### First run, before any fixes (2026-10-15)

| Metric | Result |
|---|---|
| Accuracy (all cases) | 63/67 = 94.0% |
| dev cases | 37/37 = 100.0% |
| holdout cases | 26/30 = 86.7% |
| Accuracy on "none" cases | 23/26 = 88.5% |
| Accuracy on field cases | 40/41 = 97.6% |

| Case | Header | Expected | Got | Why |
|---|---|---|---|---|
| c38 (holdout) | Client Email Address | email | none | the negative word "address" also counted against the `email address` phrase it belongs to |
| c47 (holdout) | City | none | country | `difflib` rated "city" a typo of "ctry" |
| c51 (holdout) | Plan | none | country | `difflib` rated "plan" a typo of "land" |
| c61 (holdout) | Fax | none | phone | phone-shaped numbers (see above) |

Two general flaws explain three of those mistakes, and both were fixed in the heuristic:
typo tolerance is now stricter for short words, and negative words no longer count against
the field's own phrases (shared generic words like "address" or "name" alone are no longer
evidence either).

## Ollama (`qwen2.5:3b`): not run

Skipped on purpose: the machine doesn't have enough free memory. On 2026-10-15 it had 7.7 GB
of RAM in total and only **1.1 GB available** (including reclaimable cache), with Windows,
VS Code and other apps already using the rest. `qwen2.5:3b` needs roughly 2.5 to 3 GB once
loaded with its context, so it would swap to disk and crawl or fail. Nothing was installed or
downloaded.

`OllamaSuggester` itself is tested (request shape, parsing, retries, timeouts) against a
throwaway local server, but it has **not** been measured against a real model, so there is no
accuracy number for it. To get one on a machine with about 4 GB or more free:

```bash
ollama pull qwen2.5:3b
python evals/run_mapping_eval.py --suggester ollama --markdown
```

## Claude: not run

It is a paid API, so it was not run. `python evals/run_mapping_eval.py --suggester claude`
needs `ANTHROPIC_API_KEY` and makes one request per case (67 in total).
