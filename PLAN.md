# Buy or Wait? — Build Plan

Two deliverables, built in sequence. **MVP** is a complete, submittable solution: a
deterministic decision engine plus a minimal LLM layer that reads message text. **Final** keeps
that engine untouched and deepens the evidence layer — richer extraction, vision for document
images, explanation polish, and self-verification. Nothing in Final replaces the MVP; it wraps
it.

All model calls go through **OpenRouter**, using **OpenAI** models, with the key read from
`OPENROUTER_API_KEY`.

---

## 1. Problem Shape (what the data actually demands)

Profiling the dataset establishes the following facts, which drive every design decision below:

| Fact | Consequence |
|---|---|
| 250 eval requests, 275 profiles, **one request per user** | Per-request work is independent and embarrassingly parallel; no cross-request state. |
| 25,342 financial events; 20.5k `expense`, 2.5k `subscription`, 1.7k `income` | Financial state must be reconstructed by simulation, not lookup. |
| Statuses: `settled` 25,148, `pending` 71, `scheduled` 70, `cancelled` 22, `failed` 21, `unrealized` 10 | A small but decisive set of non-settled rows carries most of the "trap" signal. |
| 790 payment options across 275 request IDs (the 250 eval requests plus the 25 samples); every eval request has 2–4 options and exactly one of them is `full_payment` | Installment plans are selected from a supplied menu, never synthesised, and a full-payment option always exists to price against. |
| `payment_frequency_days` ∈ {28, 30, 31} only — never weekly; `number_of_payments` ranges 1–24 while `max_installment_months` never exceeds 12 | One payment ≈ one month, so comparing `number_of_payments` against `max_installment_months` is sound. **381 of the 469 installment options on eval requests are rejected outright** by that cap or by a blank `max_installment_months` (119 users). The eligible candidate set is far smaller than the option count suggests, and `wait` / `not_recommended` will be common outcomes. |
| 140 events in a currency other than the user's `home_currency` | Dated FX conversion is mandatory, not optional. |
| 16 events with a **blank** `amount`, each linked to a PNG document | Vision extraction is required for exact correctness on ~11 eval requests — and it is decisive, not cosmetic: for `request_16` the blank-amount scheduled rent is the *only* future event the user has, and that sample's drawdown is INR 117,470. Without the image there is nothing to forecast. |
| 215 messages, 116 attached directly to an eval request; ~45 (21%) written in Indonesian | Natural-language amendment handling is required across two languages; regex alone is brittle, so an LLM extractor is warranted from the MVP onward. |
| 25 sample outputs follow rigid explanation templates | `decision_explanation` is template-shaped; an LLM is a polish layer, not the author. |

**The core of this problem is a deterministic cash-flow simulator.** The LLM's job is narrow
and well-bounded: turn unstructured evidence (messages, images) into structured amendments
that the simulator consumes. Getting that split right is the central architectural bet.

---

## 2. Scope

### In scope

- Reading all nine `dataset/` inputs and writing a root-level `output.csv` with one row per
  `request_id` in `dataset/requests.csv`, in the required column order.
- Per-user financial-state reconstruction: opening balance, recurring commitments, pending
  reservations, confirmed income, one-off noise separation, duplicate/reversal collapse.
- A 90-day daily-resolution balance forecast from `request_date`.
- Dated FX normalisation of every non-home-currency cash event.
- Candidate plan enumeration (full / partial / installments / wait / not recommended) and
  ranking under the published six-level preference order.
- `amount_safe_to_pay` solved as the largest safe same-day payment, capped at
  `requested_amount`; `earliest_date_for_full_payment` solved as the first safe full-payment
  date — both computed **before** optional spending changes.
- Spending-change search over **recurring**, non-protected, flexible events in permitted
  categories, capped at three actions, `stop` and `reduce_to` mutually exclusive per event.
- LLM evidence extraction over `messages.csv` (English and Indonesian) via OpenRouter, emitting
  typed amendment records — present from the MVP onward.
- A deterministic output validator that rejects any row violating the contract.
- **Two distinct scorers** against `dataset/sample_requests.csv`: the drawdown harness of §5.4,
  which grades the forecast curve, and a **full-output scorer** comparing all six predicted
  fields against the 25 known rows. These measure different things — the forecast can be exact
  while the emitted row is wrong, and vice versa — so neither substitutes for the other.
- The three submission artifacts: `code.zip`, the generated root-level `output.csv`, and
  `log.txt` as the `chat_transcript`. `code.zip` is the `code/` directory zipped at its own
  root, so the required report lands at `evaluation/usage_report.md` inside the archive — the
  starter repo already carries `code/evaluation/usage_report.md` as an empty placeholder, which
  fixes the path. The archive also carries the prompts, configuration, and a README with setup
  and run instructions.

### Out of scope

- Asset-price prediction, security recommendations, or any live market/FX/banking call.
- Any use of organizer-only files or hardcoded per-request labels.
- Hardcoding answers derived from `sample_requests.csv` — samples are a format and calibration
  reference, never labels.
- A UI. The deliverable is a terminal-runnable batch job.

### Non-negotiable contract invariants

Enforced by the validator; these are the definition of "done" for both versions.

- `0 <= amount_safe_to_pay <= requested_amount`.
- `affordable_now` ⟺ `earliest_date_for_full_payment == request_date`.
- `earliest_date_for_full_payment` empty ⟺ no safe full payment within the forecast.
- `payment_plan` is chronological `YYYY-MM-DD:amount` joined by `|`, or `none`.
- An `installments` plan matches a supplied `payment_option_id` exactly — dates from
  `first_payment_date` + `payment_frequency_days`, amounts from `payment_amount`, count from
  `number_of_payments`.
- `partial_payment` ⟹ status is `affordable_with_plan`, the request allows partial, the user
  accepts partial, `0 < amount_safe_to_pay < requested_amount`, exactly two payments summing to
  `requested_amount`, second payment on or before `desired_completion_date`.
- Recommended method ∈ `payment_methods_user_will_consider`, or is `wait` / `not_recommended`.
- Installment options rejected when `number_of_payments` exceeds `max_installment_months`, and
  all installment options rejected when `max_installment_months` is blank.
**Status and method are tightly coupled.** Verified across all 25 samples: only six combinations
occur, and three of the four statuses admit exactly one method.

| `affordability_status` | permitted `recommended_payment_method` |
|---|---|
| `affordable_now` | `full_payment` only |
| `affordable_later` | `wait` only |
| `not_affordable` | `not_recommended` only |
| `affordable_with_plan` | `installments`, `partial_payment`, or `full_payment` |

**Each combination fixes the shape of the rest of the row.** These are derived from the samples
and are not spelled out in the brief, but every sample obeys them:

- `affordable_now` + `full_payment` — `amount_safe_to_pay` equals `requested_amount`;
  `payment_plan` is one payment of `requested_amount` on `request_date`;
  `earliest_date_for_full_payment` equals `request_date`; no spending changes.
- `affordable_later` + `wait` — `payment_plan` is **exactly one** payment of the full
  `requested_amount` on `earliest_date_for_full_payment`, which is later than `request_date`.
  Not `none`. Six of the 25 samples take this branch.
- `not_affordable` + `not_recommended` — `payment_plan` is `none` **and**
  `earliest_date_for_full_payment` is empty. Both, always.
- `affordable_with_plan` + `full_payment` — the spending-changes branch:
  `spending_changes_needed` is non-empty, `amount_safe_to_pay` is strictly less than
  `requested_amount` (it is measured before the changes), `payment_plan` is a single payment of
  the full `requested_amount` on `request_date`, and `earliest_date_for_full_payment` is the
  later date at which no changes would be needed.
- `affordable_with_plan` + `installments` / `partial_payment` — as specified in the brief.

**Amount formatting differs between fields, and the difference is real.** Every fractional
amount inside `payment_plan` is rendered to exactly two decimal places — `620.40`, `3246.10`,
`996.60`, `15952906.67` — while whole amounts carry none: `38016`, `13110000`. But
`amount_safe_to_pay` is *not* two-decimal formatted: the samples contain `433.4`, `603.3`,
`17229139.2`. The two fields follow different rules in the same row, and matching ground truth
on exact comparison depends on reproducing both.

- Spending changes touch only events that are **recurring**, flexible (`reducible`, `stoppable`,
  or `reducible_or_stoppable`), outside `expense_categories_to_protect`, and inside the user's
  willing-to-reduce or willing-to-stop lists; `reduce_to` respects `minimum_allowed_amount`; at
  most three changes. The README's checklist states the recurring qualifier explicitly, and it
  matters: 2,505 `expense` rows carry `reducible` without all of them being recurring.

---

## 3. Assumptions

Recorded explicitly because several are inferences from the samples rather than from the spec.
Each is isolated behind a named constant or a single function so it can be revised cheaply.

**Forecast and safety**

1. The forecast window is exactly 90 days starting at `request_date`, evaluated at daily
   resolution, with the floor checked after every simulated movement on every day.
2. **Verified (25/25 samples).** `current_available_balance` is the balance as of `request_date`
   and already reflects all `settled` events; settled history is used only for recurrence
   inference, never re-applied. Test: since paying `x` today lowers the whole curve by `x`,
   `amount_safe_to_pay + minimum_balance_to_keep <= current_available_balance` must hold. It
   holds for all 25 samples, with strictly positive slack in every case — so the balance is
   neither stale nor already net of pending items.
3. `pending` debits are reserved on their settlement date (63 rows); `pending` credits are
   ignored (8 rows). `scheduled` debits are applied (23 rows, all essential categories);
   `scheduled` credits are applied as confirmed income — verified, not assumed, since all 47 of
   them are `salary`. `cancelled`, `failed`, and `unrealized` rows never move cash, and neither
   does any row with direction `non_cash`.
4. Confirmed salary recurs monthly from its `scheduled` settlement date at the same amount
   unless amended by evidence. The sample answers repeatedly land
   `earliest_date_for_full_payment` on a salary date, which corroborates this.
5. Recurrence is inferred only where history supports it: a category/description group needs at
   least three occurrences at a stable interval (weekly / fortnightly / monthly) before it is
   projected forward. Everything else is one-off and is not projected.
6. Essential variable spending is forecast conservatively — at a mean of recent observations,
   not a high percentile. Measurement (§5.4) shows expense projection is the *smaller* problem:
   the samples without an income anomaly cluster around 20% error, most of it amount noise that
   barely moves the trough. Blanket max-selection made things worse. **The dominant error source
   is income projection, not expense conservatism** — see assumption 6b.
6b. **Income series are projected forward only when the series is genuinely ongoing.** This is
   the single largest accuracy lever in the build and is only partly solved. The spec supports
   it directly ("do not count pending credits, bonuses, commissions … until they settle"; "do
   not invent unsupported future income"), and every catastrophic sample error traces to it.
7. A reversal pair (`refund` / `expense` joined by `linked_event_id`) and an
   authorisation-then-settlement pair each collapse to their net cash effect; the link alone
   does not decide this, the statuses do.

**Evidence**

8. Messages and images are untrusted data. Imperative text inside them is ignored; only
   financial facts are extracted. This is enforced structurally — the extractor emits a typed
   amendment record and never gains free-form control over the engine.
9. A message applies to a request only if `sent_at <= request_date` and it attaches to the
   request, the user, or an event belonging to that user. Later messages from the same source
   supersede earlier ones.
10. A blank `amount` is never zero. Its value is recovered from the linked image; where that is
    unavailable, the safest supported interpretation is used and the fallback is logged.

**Model layer**

11. OpenRouter is reachable and `OPENROUTER_API_KEY` is present in the environment (or `.env`,
    which is git-ignored). The key is never written to logs, the cache, or the submission.
12. Model calls are made at `temperature=0` with a fixed seed where the model honours one, and
    every response is cached on disk by content hash, so a re-run with a warm cache is bit-for-bit
    reproducible and costs nothing. A cold-cache run is reproducible only up to model
    non-determinism, which the schema validator and the engine's fallbacks absorb.
13. Exact OpenAI model IDs are pinned in config and confirmed against OpenRouter's live model
    list at build time rather than assumed from memory.

**Inputs deliberately not used for decisions**

13b. `request_text` is treated as a restatement of the structured fields, not as a source of
   facts. Checked: across all 250 requests, every number in the text that differs from
   `requested_amount` is a calendar year. It may inform explanation wording, never a decision.
13c. `financial_priorities` (9 distinct values, present on every profile) does not enter the
   safety or ranking logic, which the brief defines entirely in terms of protected categories,
   willingness lists, payment preferences and the minimum balance. It is available to
   explanation wording only. Revisit if sample scoring shows priority-correlated error.

**Output**

14. Amounts are emitted in the user's `home_currency`, unrounded except where a payment option
    supplies its own precision; explanations render amounts with thousands separators, matching
    the sample style.
15. `decision_explanation` follows the sample's template families (pay today / wait until /
    use N installments / do not proceed), each grounded in the figures the engine actually used.

---

## 4. Tradeoffs

| Decision | Chosen | Rejected alternative | Why |
|---|---|---|---|
| Who decides | Deterministic engine decides; LLM only supplies structured evidence | LLM decides affordability end-to-end | Arithmetic and constraint satisfaction are where an LLM is least reliable and where scoring is strictest. Determinism also satisfies the "deterministic where possible" constraint and makes runs reproducible. |
| Forecast granularity | Daily simulation over 90 days | Monthly aggregate / closed form | `earliest_date_for_full_payment` is a date, and the floor must hold on *every* day. Aggregation loses exactly the days that break plans. 250 × 90 steps is trivially cheap. |
| `amount_safe_to_pay` and `earliest_date_for_full_payment` | Closed form from one forecast curve and its suffix minima (§5.2) | Binary search on a safety predicate | A payment shifts the curve uniformly, so both figures fall out of the curve's trough and suffix minima in one pass — exact, no tolerance error, no repeated simulation. Verified against all 25 samples via the drawdown identity in §5.4. Multi-payment plans still go through the full simulator, where no such shortcut exists. |
| Recurrence detection | Conservative, evidence-gated threshold | Aggressive pattern mining | False positives invent expenses and suppress affordability; false negatives inflate it. The spec's "financially safer interpretation" rule favours caution, but over-caution costs accuracy on `affordable_now` rows — so the threshold is tuned against the 25 samples, not guessed. |
| Variable-spend forecast | High percentile of recent history | Mean | "Forecast essential variable spending conservatively" is explicit in the spec. |
| Explanations | Deterministic templates in MVP; LLM-polished in Final with the template as fallback | LLM-authored from scratch | Templates guarantee grounding and cost nothing. The LLM adds fluency where the rubric rewards usefulness, but can never introduce a figure the engine did not compute. |
| Image handling | Vision extraction in Final; explicit safe fallback in MVP | Block the MVP on vision | 16 blank amounts across ~11 eval requests. The MVP must produce a valid submission without them; the Final closes the gap. |
| Evidence cost | One LLM call per request that has evidence (~116 of 250), cached on disk by content hash | One call per message, or one per request unconditionally | Roughly halves calls versus unconditional, and caching makes re-runs free — which matters because the engine will be re-run many times while tuning. |
| Cost vs accuracy | Small OpenAI model for message extraction; vision-capable model only for the 16 images | One large model everywhere | Extraction is a narrow structured task, and the usage report rewards defensible economy. |
| Provider access | OpenRouter, single OpenAI-compatible client for text and vision | Direct OpenAI API | One key, one base URL, one accounting path for both modalities, and model swaps become a config change. It also adds a network hop and a dependency on OpenRouter's availability — mitigated by the disk cache, which means a mid-run outage never costs completed work. |
| LLM in the MVP | Included, scoped to message extraction only | Fully offline MVP with regex parsing | ~45 of 215 messages are Indonesian, and the templates vary enough that a regex layer would silently mis-parse. The engine still runs and produces valid output if every extraction fails, so the LLM raises accuracy without becoming a single point of failure. |
| Token accounting | OpenRouter's per-response `usage` block, plus its generation endpoint for authoritative cost | Local tokeniser estimate | The usage report asks for actual calls, tokens, and cost. Provider-reported figures are the real numbers rather than a reconstruction. |

---

## 5. Architecture

### 5.1 Shared pipeline (both versions)

```text
dataset/*.csv ──► [1] Loaders ──► typed records, indexed by user_id / request_id
                      │
                      ▼
                  [2] FX Normaliser ── exchange_rates.csv, matched on settlement date + pair
                      │
                      ▼
                  [3] Evidence Resolver ──► amendment records (typed, untrusted-safe)
                      │                     MVP: rule-based    Final: + LLM + vision
                      ▼
                  [4] State Reconstructor ──► opening balance, recurring schedule,
                      │                        reserves, confirmed income, one-offs
                      ▼
                  [5] Forecaster ──► 90-day daily balance curve (pure function)
                      │
                      ▼
                  [6] Plan Engine ──► safe-amount solver, earliest-full-date solver,
                      │                spending-change search, candidate enumeration,
                      │                six-level ranking
                      ▼
                  [7] Validator ──► contract invariants; hard-fail on violation
                      │
                      ▼
                  [8] Writer ──► output.csv   +   [9] Scorer vs sample_requests.csv
```

Components 4–7 are pure functions over typed inputs. That is what lets the Final version change
only component 3 while leaving the decision logic — and its test results — intact.

### 5.2 The safety predicate

One function underpins everything:

```text
is_safe(user_state, payments[], spending_changes[]) -> bool
```

It replays the 90-day forecast with the proposed payments and changes applied and returns
whether the balance stays at or above `minimum_balance_to_keep` on every day. Multi-payment
plans (installments, partial payment) and spending-change candidates go through it directly.

**The two headline figures, however, are closed-form — no search required.** Because a payment
on day `d` lowers the curve uniformly from `d` onward, one forecast curve `C(t)` yields both:

- `amount_safe_to_pay = clamp(min(C) − minimum_balance_to_keep, 0, requested_amount)`, since a
  payment today shifts the entire curve down and the binding constraint is its global trough.
- `earliest_date_for_full_payment` = the first day `d` where
  `suffix_min(C, d) ≥ minimum_balance_to_keep + requested_amount` — a single right-to-left pass
  of suffix minima over the curve. Empty when no such `d` exists in the window.

This replaces the binary search in the first draft: one curve plus its suffix-minimum array
answers both questions exactly, in one pass, with no tolerance error. It also makes the whole
problem reduce to a single question — **how accurately can the curve be built?** — which is
where all remaining risk sits.

Every reported number comes from that one curve. There is no second code path that can disagree.

### 5.3 Plan selection

Enumerate every eligible candidate, drop the unsafe ones, then rank the survivors by: completes
by `desired_completion_date` → no spending changes → lowest `total_payable_amount` → earliest
start → fewest payments → lowest `payment_option_id`. Every installment option carries a nonzero
`financing_fee` (all 515 do), so full payment always wins the cost criterion when it is eligible
and safe. Eligibility is gated on
`payment_methods_user_will_consider` and `max_installment_months` before safety is tested.
`wait` applies when full payment is safe later and the user accepts full payment;
`not_recommended` when nothing eligible is safe.

Note the deliberate independence: `earliest_date_for_full_payment` measures financial capacity
and is computed without reference to the user's method preferences, so it may equal
`request_date` even when the recommendation is `installments`.

### 5.4 Calibration target

The identity in §5.2 makes the forecast directly measurable against the 25 samples without
waiting for the rest of the engine. For each sample, the drawdown the ground truth implies is:

```text
target_drawdown = current_available_balance − (amount_safe_to_pay + minimum_balance_to_keep)
```

That is the exact depth of the curve's trough below the opening balance. It is a single scalar
per sample, comparable to the forecaster's output, so recurrence detection and the variable-spend
estimator can be tuned against 25 known values before any plan logic exists. Crucially, this target is **exact only when `amount_safe_to_pay < requested_amount`**. When the
two are equal the figure was capped by the request size, so the derived drawdown is merely an
upper bound and scoring against it as an equality is wrong. That splits the samples into 21
exact-trough cases and 4 bounded ones (`request_01`, `request_09`, `request_12`, `request_16`),
which are checked for bound violations instead. A first-cut model sits at ~22% median relative
error on the 21 exact cases — the starting point, not the target.

Two structural facts surfaced while measuring it, both of which shape the forecaster:

- **Phase beats amount.** The trough is set by where the monthly expense cluster (rent ~day 3,
  utilities ~day 5–7, subscriptions ~day 10–13) falls relative to salary on day 15. Anchoring
  monthly series to their modal day-of-month rather than to "last occurrence + median gap"
  measurably improves accuracy (median 25.6% → 22.6%), because mis-phasing salary by one cycle
  moves the trough by a full month of net flow while a 10% amount error barely moves it. This is
  also why blanket max-selection degraded accuracy: it inflated amounts without fixing phase.
- **The error distribution is bimodal, not a smooth spread.** Fifteen samples sit within ~36%
  while six are off by −100% to +695%. That rules out "tune the estimator" as the fix: the tail
  is six structural failures with a common cause, diagnosed in §5.5.
- **Projected salary is not optional.** Testing the reading that only the single explicitly
  `scheduled` salary counts — a defensible reading of "do not invent unsupported future income" —
  raised median error from 22% to over 400%. Recurring salary *is* projected; the question is
  which series qualify, which is what §5.5 settles.
- **Some users have no future events at all.** For `request_11` and `request_15` every event
  predates `request_date`, so the entire curve is projected recurrence with nothing explicit to
  anchor it. These are the cases that punish weak recurrence detection hardest, and they are not
  rare.

### 5.5 Income modelling — the dominant error source

Diagnosing the six catastrophic samples (§5.4) showed all of them are income-projection failures,
not expense-forecast failures. Grouping income by `category` is the specific bug: `salary` lumps
together series with completely different forward semantics.

| Sample | What history shows | Why naive projection fails |
|---|---|---|
| `request_05` | Five identical ZAR 14,740 payrolls; the last is described *"**Final** employer payroll"* | Employment ended. The signal is in the description text, not in `status`. |
| `request_10` | Weekly gig payouts, INR 41k–83k, described "Delivery/Driver/Task platform payout" | Variable, unconfirmed platform income. A message confirms the next payout is still pending and not withdrawable. |
| `request_11` | `salary` = **Base salary** IDR 23,256,000 fixed on the 15th **plus** commission IDR 8.5M–20M on the 24th | Two series in one category. A message confirms base pay and states the commission is not approved. |
| `request_13` | "Primary household salary" EUR 1,343.54 monthly **and** "Second household income" that stops after January | A series that silently ends. |
| `request_14` | Reduced/absent recent salary | A message says regular EUR 2,717 *resumes* on 2025-08-15 — the future differs from history. |
| `request_15` | Almost no salary history (new job) | A message confirms a first salary of EUR 1,661 on 2026-01-15. Nothing to project from; the series must be created from evidence. |

**The fix has four parts:**

1. **Split series identity by direction.** Income series key on `(category, normalised description)`
   so base pay, commission, second-household income, and gig payouts stay separate. Expenses stay
   keyed on `category` — measurement shows description-keying *fragments* expense series and makes
   things worse (median 21.6% → 32.6%), because a grocery series legitimately varies its
   description week to week.
2. **Project an income series only when it is ongoing.** Suppression triggers on termination
   wording in the description (`final`, `last`), a series that has gone stale (no occurrence
   within ~1.6× its own interval), or message evidence that the income is unapproved, pending, or
   not withdrawable. A `scheduled` row is affirmative confirmation.
3. **Let evidence set the forward amount, overriding history.** The confirmed base
   (`request_11`), the resumed amount (`request_14`), the first salary of a new job
   (`request_15`). This includes *creating* a series that has no history at all, from a message
   alone — which is why the LLM evidence layer is load-bearing for accuracy and not just for
   edge cases.
4. **Do not gate on amount variance.** A first cut used a coefficient-of-variation test as a
   proxy for "unconfirmed income". It fixed every diagnosed case (`request_05` −85%→+18%,
   `request_10` −95%→+36%, `request_11` −100%→−22%, `request_25` +23%→+6%) but regressed others
   badly (`request_08` +19%→+895%) because some salaries legitimately vary — user_08's runs
   EUR 1,422.85 then 782.57 and still continues. Variance does not distinguish "irregular but
   ongoing" from "not coming back"; only description semantics and message evidence do. This is
   precisely the judgement the LLM layer is well suited to and a statistical rule is not.

The calibration harness in §5.4 gates every one of these changes, so each is accepted or rejected
on measured error against the 21 exact samples rather than on plausibility.

### 5.6 Model layer

A single OpenAI-compatible client points at OpenRouter's base URL and serves both modalities:

| Role | Model | Used by | Volume |
|---|---|---|---|
| Message extraction | small OpenAI text model | MVP and Final | ~116 calls, one per request carrying messages |
| Document extraction | vision-capable OpenAI model | Final only | 16 calls, one per blank-amount image |
| Explanation polish | small OpenAI text model | Final only | one batched pass over the 250 rows |

Exact model IDs live in config and are confirmed against OpenRouter's model list before the
final run rather than assumed. Around the client sit four fixed guarantees:

- **Structured output.** Every call requests a JSON schema and the response is validated against
  it. Off-schema output is a dropped extraction, never a partial parse.
- **Cache.** Keyed by a hash of the prompt, model ID, and schema version. Re-runs are free and
  deterministic, and the cache is what makes tuning the engine against 250 requests practical.
- **Failure isolation.** Any call that errors, times out, or fails validation degrades to "no
  amendment". The engine always has a complete, valid answer without the model.
- **Accounting.** Each response's `usage` block is recorded per call and per model, with
  OpenRouter's generation endpoint supplying authoritative cost, feeding
  `evaluation/usage_report.md` directly.

### 5.7 Observability

Observability is built in, not bought. **No LangChain and no LangSmith** — recorded here because
both were considered and rejected on grounds that would otherwise be re-litigated mid-build.

**Why not LangChain.** The model layer is one `POST /chat/completions` per call with a JSON
schema. There are no chains, agents, retrieval, memory, or tool calls to orchestrate — the
architecture deliberately keeps the model out of control flow (§5.5). An orchestration framework
would add a dependency and a version surface while inserting indirection between the code and the
raw response, including the `usage` block the required cost report is built from.

**Why not LangSmith.** Every need it would serve is already met by something this project builds
anyway, in two cases because the brief requires it:

| Need | Met by |
|---|---|
| Inspect what an extraction returned and what it changed | the decision bundle (§9.1), which shows the amendment beside the curve and the resulting decision — the LLM call in isolation is the less useful view |
| Token and cost accounting | `evaluation/usage_report.md`, a required deliverable built from OpenRouter's per-response `usage` block |
| Debug a specific call | the content-hash cache, which already persists every prompt and response to disk — greppable, offline, no account |
| Evaluate quality | the drawdown harness (§5.4) and the full-output scorer (§6.1), both grounded in real ground truth |

Adding it would introduce a second account and API key to keep out of a public repository, a
network dependency in the hot path, and outbound transmission of financial records and message
text to a third party — none of which is required, and the last of which is worth avoiding on
principle for data of this kind.

**What is built instead.** A structured run log, written as JSONL, one record per model call:
request hash, model ID, cache hit or miss, input and output tokens, reported cost, latency,
schema-validation outcome, and for every dropped amendment the reason it was dropped. Cost is
near-zero because `usage_report.md` is generated directly from it — the observability is a
by-product of a required artifact rather than an addition to it. Alongside it, the per-request
decision bundle records the inputs, the curve, the candidates considered, the ranking criterion
that decided the outcome, and the applied amendments.

Together these answer the questions that will actually come up during the build — *why did this
request get this answer*, and *what did the model contribute to it* — which per-call tracing on
its own does not.

**When the calculus would change.** Multi-step agent loops, non-deterministic control flow,
sustained production traffic, or several people needing shared visibility into live runs. A
fixed 250-request batch job with single-turn structured calls has none of these.

---

## 6. Testing

Test scope, not a task list. The pipeline in §5 is a chain of pure functions over typed records,
so most of it is testable without a network, without the LLM, and without the full dataset.

### 6.1 Shared — required for both versions

**Loader and normalisation.** Every column of all nine inputs parses into its typed record.
Covers blank `amount`, blank `max_installment_months`, blank `minimum_allowed_amount`, blank
`linked_event_id`, empty pipe-delimited preference lists, and the single-element case that looks
like a scalar. A malformed row fails loudly rather than silently defaulting.

**FX normalisation.** Same-currency passthrough with no lookup; each of the five currencies
converting to the others; rate selected by settlement date and stated direction, never by event
date and never by inverting a rate that exists only in the other direction; a missing rate
surfaces as an explicit failure rather than an implicit 1.0.

**Cash-state semantics.** One case per status and direction: settled debit and credit, pending
debit reserved on its settlement date, pending credit ignored, scheduled debit applied, scheduled
credit applied, cancelled / failed / unrealized never moving cash, and `non_cash` never moving
cash. Reversal pairs and authorisation-then-settlement pairs each collapse to their net effect.

**Recurrence and income gating (§5.5).** The fixture set mirrors the six diagnosed failures: a
terminated series flagged only by its description, variable platform payouts, one category
holding two distinct income series, a series that silently stops, a series resuming at a new
amount, and a series with no history that exists only in evidence. Each asserts whether the
series projects forward and at what amount. A companion case pins the finding that legitimately
variable-but-ongoing salary must still project, guarding against a variance-based gate being
reintroduced later.

**Curve, trough, and suffix minima.** The identities in §5.2 are property-tested: paying `x`
today lowers every point of the curve by exactly `x`; `amount_safe_to_pay` never exceeds
`min(C) − minimum_balance_to_keep`; `suffix_min` is non-decreasing in `d`; and the closed-form
`earliest_date_for_full_payment` agrees with a brute-force day-by-day search over randomised
curves. That cross-check is what licenses replacing the search with the closed form.

**Plan eligibility and ranking.** Each method gated correctly by
`payment_methods_user_will_consider`; installment options rejected past `max_installment_months`
and rejected wholesale when it is blank; the six ranking criteria exercised so each one is the
deciding factor in at least one case, including the `payment_option_id` tie-break; and the
independence case where `earliest_date_for_full_payment` equals `request_date` while the
recommendation is still `installments`.

**Spending-change search.** Only recurring, flexible, non-protected events in permitted
categories are selectable; `reduce_to` never goes below `minimum_allowed_amount`; at most three
actions; `stop` and `reduce_to` never target the same event; and a plan needing changes loses to
an otherwise-equal plan needing none.

**Output contract.** The validator is tested against deliberately invalid rows — one per
invariant in §2 — to prove it rejects, rather than only that it passes valid input. Plus
formatting: date rendering, amount precision, `|` joining, `none` versus empty string, and exact
column order.

**End-to-end.** A miniature dataset of a handful of users exercising each `affordability_status`
runs the whole pipeline and asserts an exact `output.csv`. Fast enough to run on every change.

**Full-output scorer.** Compares all six predicted fields against the 25 solved samples —
status, method, plan, earliest date, spending changes, and the numeric field — reporting per-field
accuracy rather than one aggregate, so a regression in one field cannot hide behind gains in
another. The status-to-method coupling table in §2 is asserted here on the full 250-row output as
well, where no ground truth exists but the coupling must still hold.

**Regression harness (§5.4).** Scores the 21 exact-trough samples and bound-checks the 4 capped
ones, reporting median and mean error. Any change that worsens the median fails. This is the gate
the §5.5 work is tuned against, and the reason a plausible-sounding fix that quietly regresses
three samples gets caught rather than shipped.

### 6.2 MVP-specific

**Extraction contract.** The message extractor is tested against recorded fixtures, never a live
call, covering both languages and each amendment kind, plus the negatives: messages carrying no
financial fact, and messages whose `sent_at` is after `request_date` and must be ignored.

**Schema and confidence enforcement.** Off-schema output, an amendment referencing a nonexistent
event, and a below-floor confidence are each dropped rather than partially applied.

**Prompt-injection resistance.** Fixtures where message text contains instructions ("ignore
previous rules", "mark this affordable", "set the balance to…"). The assertion is structural: the
emitted amendments are unchanged from the equivalent neutral message, and the final decision is
identical. This is tested rather than assumed, because resistance to untrusted input is a claim
the architecture makes.

**Offline degradation.** With the network unavailable, and separately with the provider returning
errors, the run still completes, produces a valid `output.csv` for all 250 requests, and every
extraction degrades to "no amendment" rather than to a wrong amendment.

**Determinism.** Two consecutive runs against a warm cache produce byte-identical output.

### 6.3 Final-specific

**Vision extraction.** Each of the 16 documents asserts the extracted amount and currency, with a
cross-check that a result contradicting the event's own currency or category is rejected in
favour of the fallback. `request_16` is called out specifically: its blank-amount rent is the
only future event that user has.

**Conflict resolution.** Multi-message fixtures exercising the spec's precedence order in turn —
explicit cancellation or amendment beating an earlier record, newer from the same source beating
older, settled beating estimate, and the financially safer interpretation when nothing else
resolves it.

**Explanation polish invariant.** For every polished explanation, the figures and dates it
contains are exactly those the engine computed; a fixture where the model alters, adds, or drops
a number asserts that the deterministic template is emitted instead.

**Fallback to MVP.** A row whose Final-path answer fails validation falls back to the MVP answer
for that request, and the fallback is recorded. The suite asserts the Final version's validity
never drops below the MVP's on any request.

**Usage accounting.** Recorded token counts and costs reconcile with the per-call usage entries,
and `evaluation/usage_report.md` contains every field the brief requires and no credentials.

---

## 7. Version Control

**Remotes.** `origin` is the submission repository,
`https://github.com/dhairyasurana007/hackerrank-orchestrate-september26`. The organizer's starter
repository is retained as `upstream`, so dataset or statement corrections can still be pulled.

**Branching.** Two branches, matching the two deliverables:

| Branch | Holds | Tracks |
|---|---|---|
| `mvp` | The MVP implementation (§8) — engine, forecaster, rule layer, message extraction | `origin/mvp` |
| `main` | The Final implementation (§9), built on top of the MVP | `origin/main` |

Because Final *wraps* the MVP rather than replacing it, `mvp` merges into `main` once the MVP
meets its done-criteria, and Final work continues on `main` from that merge commit. The `mvp`
branch is not deleted afterwards: §9 requires a per-row fallback to the MVP answer, so the
baseline has to stay independently runnable and comparable for the life of the project. If a
correctness fix is found in shared engine code during Final work, it lands on `mvp` first and is
merged forward, so the fallback path never drifts behind the code it backstops.

Shared foundations — `PLAN.md`, `.gitignore`, the dataset, and the test scaffolding both versions
use — belong on `main` before `mvp` branches from it, so neither branch owns material the other
needs.

**Commits.** Small and scoped, each leaving the pipeline runnable. Sequenced so the regression
harness (§5.4) exists before the forecaster it grades, since accuracy changes are only meaningful
once they can be measured.

**Never committed.** `log.txt` (gitignored; submitted separately as the `chat_transcript`), `.env`
and any credential, the LLM response cache, and `code.zip`. Secrets are read from the environment
only. The `dataset/` inputs are committed as received and never modified — generated predictions
go to the root-level `output.csv`.

**Tagging.** `mvp-baseline` at the MVP's done-criteria commit and `final-submission` at the
submitted state, so the exact commit behind the submitted `output.csv` and `usage_report.md`
remains identifiable afterwards. The tag is deliberately *not* named `mvp`: a tag sharing a name
with a branch makes the refname ambiguous, and git will warn and resolve it unpredictably.

---

## 8. MVP

**Goal: a complete, valid, competitive submission — the full engine plus one narrow LLM layer.**

Everything in §5 implemented deterministically, with component 3 covering:

- Structured signals: status semantics, `linked_event_id` lifecycles, dated FX,
  `flexibility` / `minimum_allowed_amount`, payment-option schedules, profile preferences.
- **LLM message extraction.** One call per request that carries messages, over both English and
  Indonesian text, returning a schema-validated list of typed amendments —
  `{kind, target, amount, currency, effective_date, confidence}` across kinds such as salary
  amendment, cancellation, delay, confirmation, credit-not-yet-settled, and recurring-amount
  change. Message text enters the prompt as quoted untrusted data; any amendment that is
  off-schema, references a record that does not exist, or falls below the confidence floor is
  dropped. The engine consumes amendments through one typed interface, so the model never
  reaches the arithmetic.
- Blank-amount events fall back to the conservative supported estimate for their recurring
  group, with the fallback recorded — no vision yet.
- `decision_explanation` from deterministic templates.

MVP is done when it runs end-to-end from the terminal, writes a fully valid `output.csv` for all
250 requests, passes the validator with zero violations, emits a first `usage_report.md`,
self-scores against the 25 samples, and passes the §6.1 and §6.2 suites. Because extraction failures degrade to "no amendment", it
also produces a valid submission with the network unavailable — that offline run is the floor,
and the scored run with amendments is the regression baseline every Final change must beat.

---

## 9. Final

**Goal: close the accuracy gap on evidence-heavy requests without touching the engine.**

Built on the MVP. The decision logic in components 4–7 does not change; the evidence layer
deepens and three checks are added around the edges.

- **Vision extraction.** The 16 blank-amount events resolved from their PNGs (payslips,
  invoices, receipts) into an exact amount and currency — `image_01.png`, for instance, is an
  Indonesian payslip whose net pay is the missing figure for `event_253`. Each result is
  cross-checked against the event's own category and currency before acceptance, with the MVP's
  conservative estimate as fallback.
- **Deeper message extraction.** The MVP's single-pass extractor gains the cases it handles
  weakly: multi-message conflict resolution under the spec's precedence order (explicit
  cancellation or amendment → newer record from the same source → settled over estimate →
  financially safer reading), percentage-based changes, and messages attached to the user rather
  than the request that still bear on the forecast window.
- **Explanation polish.** Template output rewritten for fluency under a hard constraint: the
  polished text must carry the same figures and dates as the template, verified
  programmatically, or the template is emitted instead.
- **Verification pass.** Every row re-simulated against the contract; any failure falls back to
  the MVP answer for that request. The Final version therefore cannot score below the MVP on a
  validity basis.
- **Cost controls and reporting.** Warm disk cache across runs, batched extraction, small model
  for text, vision model only for the 16 images, and `evaluation/usage_report.md` generated from
  recorded provider usage — per-model and overall calls, input/output tokens, totals and averages
  per request, and estimated total and per-request cost.

### 9.1 Deployment and repository hosting

**There is no deployed application, and GitHub Pages is not used to host the solution.** This is
a deliberate decision, recorded because the option was considered and rejected on three
independent grounds:

- Pages serves **static files only**, with no server-side execution. The deliverable is a Python
  batch job that reads `dataset/` and writes `output.csv`; Pages cannot run it.
- The pipeline authenticates to OpenRouter with `OPENROUTER_API_KEY`. Anything served from or
  reachable by a Pages site is public, so there is no safe place for the key. Shipping a
  browser-side call would expose it outright.
- Nothing in the brief asks for a deployment. The submission is `code.zip`, `output.csv`, and the
  `chat_transcript`; the only runtime constraint is that the solution be runnable from the
  terminal. A UI is explicitly out of scope (§2).

GitHub still earns its place in the workflow, in three ways that do fit a terminal-run batch job:

**Continuous integration (GitHub Actions).** The natural home for hosted execution. On every push
to `mvp` and `main`, run the §6 suites and an end-to-end pipeline run with the model layer
disabled, asserting that a valid `output.csv` is produced for all 250 requests with the network
unavailable. `OPENROUTER_API_KEY` is stored as an encrypted repository secret — the correct home
for it — so a scored run can be triggered manually without the key ever entering the repository.
CI also runs the §5.4 regression harness and fails on a worsened median, which makes the accuracy
gate automatic rather than a matter of remembering to check.

**Releases.** At the `final-submission` tag, attach `code.zip` and the generated `output.csv` as
release assets. This gives the exact submitted artifacts an immutable URL tied to a specific
commit, so what was submitted stays identifiable after the fact.

**Pages — an optional bonus, not a deliverable.** A web app is not required and has no bearing on
scoring, but one form of it is both buildable on Pages and genuinely useful: a **static explorer
over precomputed results**. Because the engine is deterministic and the dataset is fixed, the run
that produces `output.csv` can also emit a JSON bundle — per request, the 90-day forecast curve,
the minimum-balance floor, the trough, the evaluated candidate plans, the applied evidence
amendments, and the final decision. The page loads that committed JSON and renders it. The
browser computes nothing, calls nothing, and holds no secret.

What it would show, in rough order of value:

- The forecast curve for any request, with the floor line, the trough marked, and the recommended
  plan's payments overlaid — a direct visualisation of §5.2, which is the heart of the system and
  otherwise invisible.
- Why a decision came out as it did: the events driving the trough, the candidates that were
  rejected and at which ranking criterion, and any evidence amendment that changed the curve.
- Predicted versus target drawdown across the 21 exact-trough samples (§5.4), which makes the
  accuracy story legible at a glance.
- The usage and cost summary from `evaluation/usage_report.md`.

The boundary that keeps it static: **no live recomputation and no model calls from the browser.**
Accepting a new request, re-running the engine, or calling OpenRouter client-side would each
require a server or would expose the key, and would turn a bonus into a liability.

**Host choice is near-irrelevant while it stays static** — GitHub Pages and a Render static site
both simply serve files, and Pages wins only on already being in the repository ecosystem. (A
Render static site deploying from a private repository would resolve the visibility collision
below, where Pages needs a paid plan; Render docs did not confirm that for the free tier, so it
should be verified before being relied on.)

**If live what-if recomputation is ever wanted, Render is the right host and the reason is
architectural.** The realistic live feature is not evaluating a stranger finances — the engine
needs a full profile and event history — but selecting one of the 250 users and changing the
requested amount or date. Serving that from a static host would mean reimplementing the decision
logic in JavaScript, creating a second implementation that can disagree with the first and
breaking the invariant in §5.2 that every reported number comes from one curve. A server-side
endpoint running the same Python engine preserves it. Practical cost on Render free tier: web
services spin down after 15 minutes idle with a 30-60 second cold start, and 750 instance hours
per workspace per month, so a cold demo link is slow. This is strictly post-deadline work.

**Sequencing.** This is built only after both versions meet their done-criteria — it consumes
their output and cannot be finished before them, and no part of the graded submission depends on
it. Note also that it collides with the visibility recommendation below: Pages on a private
repository requires a paid plan, so the practical order is to build the JSON emitter whenever
convenient, keep the repository private through the deadline, and publish the site afterwards.
The emitter is worth having regardless — the same bundle is the fastest way to debug a wrong
decision during the build, whether or not a page is ever served from it.

**Repository visibility — act on this before pushing any solution code.** The submission
repository is currently **public**. This is a solo challenge in which the participant must be the
author of the submission, and the deadline is 2026-09-13 18:00 IST. A public repository publishes
a working solution to other entrants while the contest is still running, and enabling Pages would
make it more discoverable still. The repository should be made **private until after the
deadline**, then opened up if desired. Nothing about the workflow above depends on the repository
being public: Actions, encrypted secrets, and Releases all work on private repositories, and
Pages on a private repository is available on paid plans and is in any case the optional piece.

---

### 9.2 Done

Final is done when it passes the §6.1 and §6.3 suites and its regression score beats the MVP
baseline on the §5.4 harness.

Both versions remain runnable from the terminal, read only from `dataset/`, take secrets from
environment variables only, and are reproducible given a warm evidence cache.
