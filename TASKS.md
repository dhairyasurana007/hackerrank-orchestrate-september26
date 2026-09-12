# Buy or Wait? — Task Breakdown

Execution plan derived from [PLAN.md](PLAN.md). PLAN.md is the authority on *what* and *why*; this
file is the authority on *order* and *done*. Where they disagree, PLAN.md wins on decisions and
this file wins on sequencing.

Two sections: **MVP** (§3, on the `mvp` branch) and **FINAL** (§4, on `main`). MVP ships first and
is a complete, submittable solution on its own. FINAL is built on top of it and never replaces it.

**The repository.** All work happens in, and is pushed to:

> **https://github.com/dhairyasurana007/hackerrank-orchestrate-september26**

This is the submission repository and the only push target. It is `origin`. The organizer's
starter repository — `upstream`, at
`https://github.com/interviewstreet/hackerrank-orchestrate-september26.git` — is **never** pushed
to; it exists solely to pull dataset or problem-statement corrections. Confirm with
`git remote -v` before the first push of a session, because `gh` resolves to `upstream` by default
here (§1.3).

**§1 governs every commit in both sections and is not optional.** Read it before starting any task.

---

## 1. The Per-Commit Cycle

**This section governs what happens for every single commit. No task is complete until this cycle
has run to completion for it.**

Work proceeds one task at a time. A task is implemented, verified locally, pushed, and verified
again in CI. Only when **both** verifications are green does the next task begin.

### 1.1 The cycle

```
  ┌──► 1. IMPLEMENT the current task
  │         │
  │         ▼
  │    2. TEST LOCALLY  — the task's local gate + the entire existing suite
  │         │
  │         ├── red ──► 3. DEBUG → FIX → back to step 2
  │         │
  │         └── green
  │              │
  │              ▼
  │    4. COMMIT and PUSH to the task's branch
  │              │
  │              ▼
  │    5. WAIT for GitHub Actions to finish. Do not start anything else.
  │              │
  │              ▼
  │    6. CHECK the result explicitly — never assume it passed
  │              │
  │              ├── any job red ──► 7. DEBUG → FIX ──┐
  │              │                                     │
  │              └── all jobs green                    │
  │                       │                            │
  │                       ▼                            │
  │              8. TASK COMPLETE → next task          │
  └─────────────────────────────────────────────────────┘
                  (a CI failure returns to step 2, not step 4 —
                   every fix is re-verified locally before it is pushed)
```

### 1.2 The steps in words

1. **Implement** the current task, and only that task.
2. **Test locally.** Run the current task's local gate — the verification of *this* task, on this
   machine (§1.4) — and then the full existing suite. The gate proves the new thing works; the
   suite proves it did not break anything that already did.
3. **If local tests fail: debug, fix, and re-run step 2.** Do not push a red tree.
4. **Commit and push** to the task's branch (`mvp` during §3, `main` during §4).
5. **Wait for GitHub Actions, using `gh run watch`.** The push triggers the §2.3 workflow. **Use
   `gh` to monitor it** — `gh run watch -R "$R"` blocks until the run finishes, which is what makes
   the wait enforced rather than remembered. Do not begin the next task while a run is pending: a
   task started on top of an unverified commit turns one failure into two entangled ones. See §1.3
   for the exact invocation and its two caveats; the REST API there is a fallback for when `gh` is
   unavailable, not the default.
6. **Check the result explicitly, using `gh run list` or `gh run view`.** Read the actual run
   conclusion — it must say `success`. A push completing successfully says nothing about whether CI
   passed, and neither does a `gh run watch` that returns.
7. **If any CI job fails: use `gh run view -R "$R" --log-failed` to read the failing job's log**,
   then debug, fix, and return to step 2 — re-verify locally, then push again. Repeat this cycle as
   many times as it takes. Do not guess at the cause from the job name; read the log.
8. **Only when every job is green is the task complete.** Move to the next task.

### 1.3 Checking CI status

**Preferred: `gh`.** Installed at `C:\Program Files\GitHub CLI\gh.exe` (v2.100.0), authenticated
as `dhairyasurana007` with scopes including `workflow` — which M0 needs, since pushing
`.github/workflows/ci.yml` requires it.

Two environment caveats, both verified:

- **Not on the shell PATH** for sessions started before it was installed. Invoke by full path, or
  from a fresh shell.
- **This repository has two remotes**, and `gh` resolved to `upstream`
  (`interviewstreet/hackerrank-orchestrate-september26`) rather than `origin`
  (`dhairyasurana007/hackerrank-orchestrate-september26`). Left uncorrected, every CI check in this cycle
  would poll the organizer's repository and report no runs. `gh repo set-default` has been set to
  the submission repo, which fixes `gh run`, but not every subcommand honours it — `gh secret`
  still fails on the ambiguity. **Pass `-R` explicitly**; it is the only form verified to work
  across subcommands.

`$R` below is the repository in `owner/name` form. It is the variable used by the commands in
§1.2 steps 5 to 7, and must be set in the shell before they will work:

```bash
GH="/c/Program Files/GitHub CLI/gh.exe"
R="dhairyasurana007/hackerrank-orchestrate-september26"
"$GH" run watch -R "$R"             # step 5 - blocks until the run finishes, no polling loop
"$GH" run list -R "$R" --limit 5     # step 6 - recent runs with their conclusions
"$GH" run view -R "$R" --log-failed  # step 7 - the failing job log, straight to the cause
```

`gh run watch` is the better fit for step 5 precisely because it blocks: the wait is enforced by
the tool rather than left to discipline.

**Fallback: the REST API.** No auth needed while the repository is public, so this keeps working if
`gh` is unavailable or unauthenticated. It needs a token if the repository is made private:

```bash
BRANCH=$(git rev-parse --abbrev-ref HEAD)   # not hardcoded: mvp during §3, main during §4
URL="https://api.github.com/repos/dhairyasurana007/hackerrank-orchestrate-september26/actions/runs?branch=$BRANCH&per_page=1"
curl -s "$URL" | python -c "import json,sys; w=json.load(sys.stdin)['workflow_runs']; print(w[0]['status'], w[0]['conclusion'], w[0]['html_url']) if w else print('no runs for this branch')"
```

`status` is `queued`, `in_progress`, or `completed`; only once it is `completed` does `conclusion`
mean anything, and it must read `success`. On `failure`, open `html_url` or fetch the failing job's
log to find the cause.

### 1.4 What a local gate is

**A task's local gate is the verification of *that task*, run on this machine.** It is what proves
the thing just built actually works, before anything leaves the developer's machine. "Local" is in
contrast to CI (step 5); "gate" means the task does not advance until it passes.

Three properties:

- **Scoped to the current task.** M2's gate verifies FX conversion. It does not verify the plan
  engine, and it is not a synonym for "the whole suite".
- **Automated, not eyeballed.** A gate worded as an assertion ("the rate is selected by settlement
  date, never by event date") means a test asserting exactly that. Once written, that test stays in
  the suite permanently and protects the behaviour for every later task. If something genuinely
  cannot be automated it is marked *(manual)* in the task; nothing is marked manual today.
- **Written as part of the task, not after it.** A task is not implemented and then gated; the gate
  is part of implementing it.

**Step 2 runs the gate, then the whole suite.** Two separate things, in order:

```bash
# 1. the current task's gate — fast, focused, what you iterate against
python -m unittest code.tests.test_fx -v

# 2. the full suite — the regression check, run before every push
python -m unittest discover -s code/tests -t .
```

The first is the local gate proper. The second exists because the most common failure is not the
new code failing but the new code breaking something already working, and it is the same command
CI's `tests` job runs, which is what keeps the two levels from drifting.

A gate that says "record that number" means committing the figure to the relevant threshold file,
not noting it in a message.

### 1.5 Standing rules

- **Never weaken a test to make it pass.** Do not comment out an assertion, loosen a threshold, or
  skip a case to get to green. If a test is genuinely wrong, change it deliberately and say so in
  the commit message so the change is visible in review rather than buried.
- **Never stack a change on a red commit.** Fix the red one first. Two entangled changes take
  longer to untangle than one takes to fix.
- **A green local run with a red CI run is a real finding, not flakiness.** It means the code
  depends on something the clean CI machine lacks — a stray file, a warm cache, an installed
  package, a working-directory assumption. That is precisely the class of bug that otherwise
  surfaces while packaging the submission, at the worst possible time.
- **One task per commit where practical.** It keeps a CI failure attributable to a single change.

### 1.6 How gates appear per task

Every task in §3 and §4 carries a **Local gate** — the task's own verification, run locally, as
defined in §1.4. It is the first half of step 2; the full-suite run is the second half and is the
same for every task.

**Step 5's CI verification is the §2.3 workflow and applies to every task without exception**, so
it is not repeated task by task; a task states a **CI gate** only when it adds something beyond
the standard workflow. The absence of a CI gate line never means a task skips CI.

**Two exceptions, both narrow.** Documentation-only commits skip steps 2 and 3 but still respect
steps 4 through 8. Tasks marked *(optional / post-deadline)* sit outside the cycle entirely, since
nothing depends on them.

---

## 2. Version Control and CI

### 2.1 Branches

| Branch | Holds | Tracks | Created from |
|---|---|---|---|
| `main` | Shared foundations, then the FINAL implementation | `origin/main` | — |
| `mvp` | The MVP implementation | `origin/mvp` (see below) | `main` at `866d972` |

- **`mvp` is pushed and tracking `origin/mvp`.** It was created from `main` and fast-forwarded to
  `main`'s tip before its first push, so the two branches start identical and `mvp` carries no work
  of its own yet. Every push from here is a plain `git push`.
- `origin` → `https://github.com/dhairyasurana007/hackerrank-orchestrate-september26.git`
  — the submission repository, and the only branch target for every push in §3 and §4.
- `upstream` → `https://github.com/interviewstreet/hackerrank-orchestrate-september26.git`
  — the organizer's starter repository, retained for dataset or statement corrections.
  **Never pushed to.**

Both strings match `git remote -v` exactly, including the `.git` suffix.
- MVP work commits to `mvp`. When the MVP meets its done-criteria it is tagged `mvp-baseline` and
  merged into `main`; FINAL work continues on `main` from that merge commit.
- `mvp` is **not deleted** after the merge. FINAL requires a per-row fallback to the MVP answer, so
  the baseline must stay independently runnable. A shared-engine fix found during FINAL work lands
  on `mvp` first and is merged forward.
- Tags: `mvp-baseline`, `final-submission`. Deliberately not named `mvp`/`final` — a tag sharing a
  name with a branch makes the refname ambiguous.

### 2.2 Never committed

`log.txt`, `.env` or any credential, the model response cache, `code.zip`. All are in `.gitignore`.

`output.csv` is the exception: it **is** committed, but only at submission time (F7). It is *not*
gitignored, so every pipeline run leaves it modified in the working tree and it will show in
`git status` constantly. Do not sweep it into a task commit — `git add` specific paths rather than
`git add -A`, so a stale prediction file is never committed alongside unrelated work.

### 2.3 GitHub Actions workflow

One workflow at `.github/workflows/ci.yml`, triggered on push to `mvp` and `main` and on pull
requests targeting either. **It must run green without any API key** — that is what makes it a
usable gate on every push.

| Job | Runs | Asserts |
|---|---|---|
| `tests` | The full test suite — §3 **and** §4 tasks' tests | All pass. This is the same command as step 2's full-suite run (§1.4), not the focused task gate, so the two levels cannot drift. |
| `pipeline-offline` | `python code/main.py --no-llm` | Exits 0; a CSV parse of `output.csv` yields exactly 250 data rows with the required header; the validator reports zero contract violations. |
| `scorers` | `python code/main.py --samples --no-llm` | Each recorded threshold is met. Thresholds ratchet down as accuracy improves, never up to accommodate a regression. |

**Assertions are staged, because a check cannot precede the thing it checks.** Each is added to the
workflow by the task that makes it satisfiable, in the same commit:

| Assertion | Added by | Why not earlier |
|---|---|---|
| 250 parsed rows, valid header | M0 | The stub satisfies it from commit one. |
| Zero contract violations | M11 | No validator exists before then. |
| **Zero rows from the failure-isolation fallback** | M13 | M0's stub emits placeholder rows for every request; the fallback path itself does not exist until M13. Asserting this at M0 would fail by construction. |
| Drawdown median threshold (`code/evaluation/thresholds/drawdown.json`) | M5b | No harness before then. |
| Full-output per-field threshold (`code/evaluation/thresholds/full_output.json`) | M12 | The scorer exists at M4, but nothing can produce real rows to score until the writer lands at M12. |

Note the row count is asserted by **parsing the CSV**, not by counting lines:
`decision_explanation` is quoted free text, and a newline inside a quoted field would break a line
count while leaving the file perfectly valid.

A fourth job, `scored-run`, is `workflow_dispatch` only (manual) and uses `OPENROUTER_API_KEY` from
repository secrets to run the pipeline with the model layer live. It is never on the push path, so
a missing or exhausted key can never block development.

Python is pinned to the same minor version used locally. No dependency installation step — the
solution is standard-library only (PLAN.md §5.8); if that ever changes, the workflow gains a
`pip install -r requirements.txt` step in the same commit that adds the dependency.

---

## 3. MVP — branch `mvp`

Goal: a complete, valid, submittable solution. Deterministic engine plus one narrow LLM layer for
message extraction.

**Execution order is the numeric order of the task IDs** (M0, M1, M2, …), which already satisfies
every dependency. The **Depends on** lines state the *real* constraints, which are looser: they
permit reordering when convenient — M5a may be pulled forward to just after M1, for instance — but
reordering is a deliberate choice, and numeric order is the default when there is no reason to
deviate.

**Ordering constraint that drives everything below:** no forecaster *tuning* happens before the
harness exists. Splitting both tasks makes the real dependency explicit rather than implied:

| Task | Depends on | Note |
|---|---|---|
| M5a — target derivation | M1 | Pure. No forecaster involved, so it is never blocked. |
| M5b — harness runner | M5a | Grades any forecaster callable, including a stub. |
| M7a — explicit-event curve | M1–M3 | Buildable before M5 exists; yields a real baseline number. |
| M7b — recurring projection | M5b, M6, M7a | First point where measurement is needed. |
| M7c — estimator calibration | M7b | The tuning loop the constraint exists to protect. |

Only **M7b and M7c** are genuinely gated. M7a can be written first — it is deterministic, exactly
testable on fixtures, and produces the explicit-only baseline that shows how much of each
drawdown must come from projection at all. Three reasons the gate on M7b/M7c holds:

1. **Without the harness, M7b/M7c have no acceptance criterion.** The gate would read "the
   forecaster runs", which is not a gate. With M5b in place it is a recorded median error that CI
   then defends, ratcheted down on every accepted improvement.
2. **Measurement changes what gets built, not just when the error is noticed.** Measured against
   this harness, three plausible design choices turned out to be wrong: interval-stepping lost to
   day-of-month anchoring, max-selection made accuracy worse rather than safer, and the dominant
   error source proved to be income projection rather than the variable-expense conservatism
   originally blamed. Built blind, those are three things to unwind rather than avoid.
3. **CI cannot guard a threshold that does not exist yet**, and the unguarded window would fall
   exactly when the forecaster is changing fastest.

### M0 — Scaffolding and CI

Project skeleton under `code/`, the `.github/workflows/ci.yml` from §2.3, and a test runner
entry point. Stub `main.py` that writes a valid all-fallback `output.csv` for all 250 requests.

- **Local gate:** `python -m unittest discover -s code/tests -t .` runs and reports zero tests;
  `python code/main.py --no-llm` produces an `output.csv` that parses to exactly 250 data rows with
  the required header.
- **CI gate:** all three push-path jobs run and pass, with only the M0-stage assertions enabled
  (see §2.3) — row count and header. The validator, fallback and threshold assertions are added
  later by the tasks that make them satisfiable.
- **Note on the stub:** its rows are *placeholders*, which is not the same thing as the
  failure-isolation fallback introduced in M13. Keeping the two distinct is what lets M13 later
  assert that zero rows came from the fallback path.
- **Why a stub first:** it makes the CI contract real from commit one, so every later task is
  verified by a pipeline that already works rather than one assembled at the end.

### M1 — Typed records and loaders

Load all nine `dataset/` inputs into typed records indexed by `user_id` / `request_id`.

- **Local gate:** every column of every file parses; blank `amount`, blank
  `max_installment_months`, blank `minimum_allowed_amount`, blank `linked_event_id`, empty
  pipe-delimited lists and single-element lists all handled; a deliberately malformed row fails
  loudly rather than defaulting silently.

### M2 — FX normalisation

Dated conversion using `exchange_rates.csv`.

- **Local gate:** same-currency passthrough performs no lookup; all five currencies convert to each
  other; the rate is selected by **settlement date and stated direction**, never by event date and
  never by inverting a rate that exists only in the other direction; a missing rate raises rather
  than silently becoming 1.0.

### M3 — Cash-state semantics

Status and direction rules, reversal and authorisation-settlement collapse.

- **Local gate:** one case per status × direction — settled debit/credit, pending debit reserved on
  its settlement date, pending credit ignored, scheduled debit applied, scheduled credit applied,
  `cancelled`/`failed`/`unrealized` never moving cash, `non_cash` never moving cash. Reversal pairs
  and authorisation-then-settlement pairs collapse to net effect. Investment purchases and sales
  move cash; valuations do not.

### M4 — Full-output scorer skeleton

Scores predicted rows against the 25 solved samples, per field, not as one aggregate.

- **Local gate:** runs against whatever `output.csv` currently contains and produces a per-field
  accuracy report. Against the M0 stub it scores near zero, which is the correct starting point.
- **Owns** `code/evaluation/thresholds/full_output.json`. This is a **different file** from the drawdown
  threshold owned by M5b — the two measure different things (emitted-row correctness versus forecast
  curve accuracy) and must not be merged into one number. CI enforces this one from M12, the first
  point at which real rows exist to score.

### M5a — Drawdown target derivation *(no forecaster dependency)*

The target side of PLAN.md §5.4, as a pure function of `sample_requests.csv` and
`financial_profiles.csv`: `target_drawdown = balance − (amount_safe_to_pay + min_keep)`, plus the
classification of each sample as exact-trough or capped.

- **Local gate:** produces the known split — 21 exact-trough and 4 capped (`request_01`,
  `request_09`, `request_12`, `request_16`); targets match hand-computed values for at least three
  samples spanning different currencies; and the module imports and runs with no forecaster
  present at all, which is the assertion that keeps this piece genuinely independent.
- **Depends on:** M1 only. Can be built at any point after the loaders, including before M3.

### M5b — Harness runner and thresholds

Takes any forecaster callable and grades it: median and mean relative error over the exact set,
bound violations over the capped set, per-sample detail on request.

- **Owns** `code/evaluation/thresholds/drawdown.json` — distinct from M4's full-output threshold.
- **Local gate:** runs against a stub forecaster returning zero drawdown and produces a complete
  report without crashing; `drawdown.json` round-trips; the `scorers` CI job is wired and green
  with the drawdown assertion enabled.
- **Depends on:** M5a.

### M6 — Recurrence detection and income gating

The single largest accuracy lever (PLAN.md §5.5). Expense series keyed on `category`; income series
keyed on `(category, normalised description)`.

- **Local gate:** fixtures mirroring all six diagnosed failures — terminated-by-description
  (`user_05`), variable platform payouts (`user_10`), two income series in one category
  (`user_11`), a silently stopping series (`user_13`), a series resuming at a new amount
  (`user_14`), a series existing only in evidence (`user_15`). Each asserts whether the series
  projects and at what amount. Plus the guard case: legitimately variable but ongoing salary
  (`user_08`, EUR 1422.85 → 782.57) **must still project**, so a variance-based gate cannot be
  reintroduced.

### M7a — Explicit-event curve

The 90-day daily curve built from explicit future events only — pending debits reserved on their
settlement date, scheduled debits and credits applied — with no recurrence projection whatsoever.

- **Local gate:** the curve is exact on hand-built fixtures; M5b runs against it end to end and
  reports a number. **Record that number as the explicit-only baseline.** It is informative in its
  own right: it separates the drawdown that comes from known future events from the drawdown that
  must be projected. Assert specifically that users with no future events at all (`user_11`,
  `user_15`) produce a flat curve and therefore zero drawdown here — that is correct at this stage,
  and it makes the size of M7b's job visible.
- **Depends on:** M1, M2, M3. Not on M6.

### M7b — Recurring projection

Adds M6's detected series to the curve: monthly series anchored to their modal day-of-month,
sub-monthly series stepped by their interval, income projected only where M6's gate permits.

- **Local gate:** phase correctness asserted on fixtures (a monthly series lands on its modal day,
  not on last-occurrence-plus-median-gap); M5b's median error improves against the M7a baseline;
  the improved figure becomes the new committed threshold.
- **Depends on:** M5b, M6, M7a.

### M7c — Estimator calibration *(the gated task)*

Tune the per-series amount estimator and any remaining projection parameters against M5b. This is
the tuning loop the ordering constraint exists to protect.

- **Local gate:** every candidate change is accepted only if M5b's median error improves, and the
  threshold is ratcheted down on acceptance. **Rejected variants are recorded with their measured
  numbers** — starting with the three already falsified in planning: interval-stepping instead of
  day-of-month anchoring, max-selection instead of mean, and suppressing all projected salary in
  favour of only the explicitly scheduled row. Recording them costs a line each and stops them
  being retried on intuition later.
- **Depends on:** M7b.

### M8 — Safety predicate and closed-form solvers

`is_safe()` plus the closed-form identities from PLAN.md §5.2.

- **Local gate:** property tests — paying `x` today lowers every curve point by exactly `x`;
  `amount_safe_to_pay` never exceeds `min(C) − min_keep`; `suffix_min` is non-decreasing; and the
  closed-form `earliest_date_for_full_payment` agrees with a brute-force day-by-day search over
  randomised curves. That last one is what licenses using the closed form at all.

### M9 — Plan engine

Candidate enumeration, eligibility gating, six-level ranking.

- **Local gate:** each method gated by `payment_methods_user_will_consider`; installment options
  rejected past `max_installment_months` and wholesale when it is blank; each of the six ranking
  criteria is the deciding factor in at least one test, including the `payment_option_id`
  tie-break; and the independence case where `earliest_date_for_full_payment` equals `request_date`
  while the recommendation is `installments`.

### M10 — Spending-change search

Search over permitted flexible events for the smallest set of changes that makes a plan safe.

- **Local gate:** only recurring, flexible, non-protected events in permitted categories are
  selectable; `reduce_to` respects `minimum_allowed_amount`; at most three actions; `stop` and
  `reduce_to` never target the same event; a plan needing changes loses to an equal plan needing
  none.

### M11 — Validator

Every invariant in PLAN.md §2, including the status↔method coupling table and the per-combination
row shapes.

- **Local gate:** tested against deliberately invalid rows, one per invariant, proving it
  *rejects* — not merely that it accepts valid input.

### M12 — Writer and formatting

Emit `output.csv` with the exact column order and the per-field formatting rules from PLAN.md §2.

- **Local gate:** exact column order; `payment_plan` fractional amounts at exactly 2dp
  (`620.40`, `3246.10`) while whole amounts carry none (`38016`); `amount_safe_to_pay` **not**
  2dp-formatted (`433.4`, `603.3`, `17229139.2`); `|` joining; `none` versus empty string;
  `wait` emitting one full payment rather than `none`.

### M13 — CLI and failure isolation

Flags per PLAN.md §5.8; per-request error boundary with a contract-valid fallback row.

- **Local gate:** a request rigged to raise still yields a valid fallback row, the run completes
  with all 250 rows, and the failure is recorded with its traceback. Path resolution asserted from
  the repo root, from inside `code/`, and from an extracted-archive layout.

### M14 — Model layer

OpenRouter client, content-hash cache, JSON-schema validation, JSONL run log.

- **Local gate:** off-schema output, an amendment referencing a nonexistent event, and a
  below-confidence-floor amendment are each dropped rather than partially applied; a provider error
  degrades to "no amendment" rather than failing the run; two consecutive warm-cache runs produce
  byte-identical output.

### M15 — Message extraction

One call per request carrying messages; typed amendments only.

- **Local gate:** recorded fixtures, never live calls — both languages, each amendment kind, plus
  the negatives (messages carrying no financial fact). **Prompt-injection fixtures**: message text
  containing instructions produces amendments identical to the equivalent neutral message, and an
  identical final decision.
- **CI note:** this runs on the `--no-llm` path in CI, so the fixtures are what prove it works;
  the live path is exercised only by the manual `scored-run` job.

### M16 — Explanation templates

Deterministic templates in the sample's four families, grounded in engine-computed figures.

- **Local gate:** each of the four families renders for a fixture and contains only figures the
  engine actually computed; amounts render with thousands separators as the samples do; no template
  can emit a number that is not passed into it.

### M17 — Usage report

`code/evaluation/usage_report.md` generated from the M14 run log: per-model and overall calls,
input/output tokens, totals and averages per request, estimated total and per-request cost.

- **Local gate:** figures reconcile with the run log; no credentials present in the output.

### M18 — MVP done

- **Local gate:** full suite green; `--no-llm` run produces a valid 250-row `output.csv` with zero
  validator violations and zero fallback rows; sample scoring recorded.
- **CI gate:** all three push-path jobs green on `mvp`.
- **Then:** tag `mvp-baseline`, then merge `mvp` into `main` with `git merge --no-ff mvp` — a true
  merge commit. **Not squashed and not rebased**: §2.1 requires `mvp` to stay independently runnable
  and directly comparable for FINAL's per-row fallback, and squashing would sever the shared history
  that makes a later `mvp`-first fix mergeable forward. Confirm CI is green on `main` after the
  merge; that green merge commit is where FINAL starts.

---

## 4. FINAL — branch `main`

Goal: close the accuracy gap on evidence-heavy requests. The decision logic from M7–M12 does not
change; the evidence layer deepens and guards are added around it.

### F0 — Baseline confirmation

After the merge, reproduce the MVP's recorded scores on `main`.

- **Local gate:** `--no-llm` output on `main` is byte-identical to `--no-llm` output at the
  `mvp-baseline` tag, and both scorers report the same figures. The comparison is deliberately on
  the offline path, so it tests the merge rather than the state of the response cache. If anything
  differs, the merge introduced a change and that is fixed before any FINAL work starts.

### F1 — Vision extraction

The 16 blank-amount events resolved from their PNGs.

- **Local gate:** each of the 16 asserts the extracted amount and currency; a result contradicting
  the event's own currency or category is rejected in favour of the MVP fallback. `request_16` is
  called out specifically — its blank-amount rent is the only future event that user has.

### F2 — Deeper message extraction

Multi-message conflict resolution, percentage-based changes, user-level messages bearing on the
window.

- **Local gate:** fixtures exercising the spec's precedence order in turn — explicit cancellation
  or amendment beats an earlier record, newer from the same source beats older, settled beats
  estimate, safer interpretation when nothing else resolves.

### F3 — Explanation polish

Rewrite the M16 templates for fluency, under a hard figure-matching constraint.

- **Local gate:** the polished text contains exactly the figures and dates the engine computed; a
  fixture where the model alters, adds or drops a number emits the deterministic template instead.

### F4 — Verification pass and MVP fallback

Every row re-simulated against the contract; failures fall back to the MVP answer for that request.

- **Local gate:** a row rigged to fail validation falls back and the fallback is recorded; FINAL's
  validity never drops below the MVP's on any request.

### F5 — Final usage report

Regenerated for the full scored run, per-model and overall.

- **Local gate:** every required field is present — providers, model names, call counts, input and
  output tokens, totals and averages per request, estimated total and per-request cost; figures
  reconcile with the M14 run log; no credential appears anywhere in the file.

### F6 — Full scored run

Run the complete pipeline with the model layer live.

- **Local gate:** valid 250-row `output.csv`; zero validator violations; sample scores beat the
  `mvp-baseline` figures.
- **CI gate:** push-path jobs green; the manual `scored-run` job green.

### F7 — Packaging and submission

- `code/README.md` with setup and run instructions — a separate file from the repository README.
- `code.zip` = the `code/` directory zipped at its own root, so the report lands at
  `evaluation/usage_report.md` inside the archive. **Re-confirm this layout against the submission
  form before uploading** — it is inferred from the starter structure, not stated.
- Commit `output.csv`, tag `final-submission`, attach `code.zip` and `output.csv` to a release.
- Submit at
  <https://www.hackerrank.com/contests/hackerrank-orchestrate-september26/challenges/buy-or-wait/submission>
  with `code.zip`, `output.csv`, and `log.txt` as the `chat_transcript`.

- **Local gate:** `code.zip` extracted into a clean directory alongside a `dataset/` copy runs
  end to end and reproduces the committed `output.csv` byte for byte; `evaluation/usage_report.md`
  is present at the archive root; no `.env`, credential, cache directory or `log.txt` is inside
  the archive. This is the last point at which a packaging error is still cheap to fix.

### F8 — Decision bundle emitter

JSON per request: curve, floor, trough, candidates, applied amendments, decision. Worth building
before F6 if debugging gets hard — it is the fastest way to see *why* a request came out wrong.

- **Local gate:** a bundle is emitted for every request; the decision it records matches the row
  written to `output.csv` for that request, so the two can never tell different stories.

### F9 — Static results explorer *(optional / post-deadline)*

Outside the verification loop. See PLAN.md §9.1 — no live recomputation, no model calls from the
browser, and gated on the repository visibility decision.
