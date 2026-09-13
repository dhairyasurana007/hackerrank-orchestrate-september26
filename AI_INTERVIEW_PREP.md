# AI Interview Prep: Buy or Wait?

This document is a concise technical briefing for explaining the project in an AI interview. It
focuses on the design choices, tradeoffs, reasoning, and honest caveats behind the submitted
solution.

## One-minute Summary

Buy or Wait? is a financial decision agent for the HackerRank Orchestrate challenge. For each row
in `dataset/requests.csv`, it decides whether a user should pay now, wait, use installments, pay
partially, or avoid the purchase. The output is the required `output.csv`, with one prediction row
per request.

The core design choice was to keep financial decisions deterministic. The system reconstructs each
user's cash-flow state from CSV inputs, simulates the next 90 days, evaluates allowed payment
options, and writes a contract-valid recommendation. The model layer is intentionally narrow: it
extracts structured evidence from untrusted messages, and it can polish explanations, but it does
not directly decide affordability.

The submitted run processed 250 requests, made 198 model calls through OpenRouter to
`openai/gpt-4.1-mini`, used 185,991 input tokens and 9,278 output tokens, and cost about
$0.089241.

Static demo:

<https://dhairyasurana007.github.io/hackerrank-orchestrate-september26/>

## What The Submission Produces

The graded deliverable is not the website. The graded deliverable is:

- `code.zip`: terminal-runnable solution package.
- `output.csv`: predictions for all 250 evaluation requests.
- `log.txt`: chat transcript / development log.

The website is a static explorer over precomputed decisions. It is useful for demoing and
explaining the results, but it does not run the Python engine, does not call a model, and does not
change `output.csv`.

## Important Clarification About `output.csv`

`output.csv` is not a purchase-history export. It is a decision file.

Each row answers one purchase/payment request from `dataset/requests.csv`:

- How much is safe to pay immediately.
- Whether the request is affordable now, later, with a plan, or not affordable.
- Which payment method is recommended.
- What payment plan should be used, if any.
- When full payment first becomes safe.
- Which spending changes, if any, would be needed.
- A concise explanation grounded in the engine's calculations.

So if asked, say: "It is one decision per requested purchase, generated from the user's profile,
events, payment options, messages, and fixed exchange rates. It is not merely a list of historical
transactions."

## System Architecture

High-level pipeline:

```text
dataset/*.csv
  -> loaders and typed records
  -> FX normalization
  -> cash-state reconstruction
  -> recurrence and income projection
  -> optional model evidence extraction
  -> 90-day forecast curve
  -> payment-plan enumeration and ranking
  -> contract validation
  -> output.csv
```

Core modules:

- `code/buyorwait/loaders.py`: parses dataset files into typed records.
- `code/buyorwait/fx.py`: applies dated exchange rates.
- `code/buyorwait/cashstate.py` and `state.py`: convert raw events into user financial state.
- `code/buyorwait/recurrence.py`: detects recurring income and expense series.
- `code/buyorwait/forecast.py`: builds the daily balance curve.
- `code/buyorwait/safety.py`: computes safe payment amounts and safe dates.
- `code/buyorwait/planner.py`: enumerates and ranks full, partial, installment, wait, and
  not-recommended plans.
- `code/buyorwait/spending.py`: searches allowed spending changes.
- `code/buyorwait/evidence.py` and `model/extract.py`: validate and apply LLM-extracted message
  amendments.
- `code/buyorwait/validator.py`: enforces the output contract.
- `code/buyorwait/writer.py`: writes rows in the exact required format.
- `site/`: static GitHub Pages demo over precomputed data.

## Key Design Decision: Deterministic Engine, Narrow LLM

The most important architectural decision was not to let the LLM make the financial decision.

Why:

- The scoring depends on arithmetic, dates, bounds, and exact output shapes.
- LLMs are poor at strict constraint satisfaction when used end-to-end.
- The challenge explicitly rewards deterministic behavior where possible.
- Untrusted messages and images can contain misleading or instruction-like text.

What the LLM is allowed to do:

- Extract structured financial facts from messages.
- Interpret natural language in English and Indonesian.
- Optionally polish the explanation text under guardrails.

What the LLM is not allowed to do:

- Choose the recommendation.
- Override the user's financial profile.
- Invent future income or payment options.
- Directly write arbitrary `output.csv` fields.

This is the core interview point: "The LLM is an evidence parser, not the decision-maker."

## Why An LLM Was Still Useful

The dataset includes messages that can amend or clarify financial facts. Some are in Indonesian,
and many are phrased differently enough that regex-only extraction would be brittle.

The model prompt asks for a strict JSON shape with amendment records such as:

- income amount changed.
- income date changed.
- income stopped.
- income suppressed because it is pending or not settled.
- recurring expense changed by a percentage.

Every amendment is then validated deterministically before it can affect the forecast. If an
amendment is off-schema, low-confidence, points to a nonexistent message, or cannot be reconciled
with the user's data, it is dropped.

Tradeoff:

- Benefit: better handling of messy natural-language evidence.
- Cost: external API dependency and some nondeterminism on cold runs.
- Mitigation: strict schema, confidence floor, validation, fallback to no amendment, and provider
  usage logging.

## Model Choice And Usage

Provider path:

- OpenRouter-compatible API.
- API key read from `OPENROUTER_API_KEY`.
- No credentials written to logs, reports, cache, or submission artifacts.

Configured models:

- `openai/gpt-4.1-mini`: message extraction and explanation polish.
- `openai/gpt-4.1`: configured for document-image extraction.

Measured submitted run:

- Requests processed: 250.
- Model calls: 198.
- Input tokens: 185,991.
- Output tokens: 9,278.
- Total estimated cost: $0.089241.
- Recorded billed model in the final usage report: `openai/gpt-4.1-mini`.

Important caveat for interview answers:

The codebase includes a vision extraction path in `code/buyorwait/model/vision.py`, configured to
use `openai/gpt-4.1` for linked document images. However, the recovered final usage report for the
submitted run records only `openai/gpt-4.1-mini` calls. So the safest answer is:

"The architecture supports vision extraction through `openai/gpt-4.1`, but the measured final run
I am submitting records 198 `openai/gpt-4.1-mini` calls and no billed vision-model calls. I would
not claim vision was used in the measured submitted run unless I rerun and regenerate the usage
report with those calls present."

That answer is honest and defensible.

## Forecasting And Safety Logic

The engine builds a daily forecast curve for each request. The key safety rule is that after the
recommended payment plan and essential future obligations, the user's balance must not fall below
their minimum balance.

Important implementation ideas:

- `current_available_balance` is treated as the starting balance on the request date.
- Settled history is used for recurrence detection, not reapplied as cash movement.
- Pending debits are reserved.
- Pending credits are not counted until settled.
- Scheduled salary can be counted on its settlement date.
- Cancelled, failed, unrealized, and non-cash events do not move cash.
- Foreign-currency events are converted using the provided dated exchange rates.
- Recurrence is detected only when history supports it.

Why daily simulation:

- The output asks for specific dates, not just monthly affordability.
- A payment plan can be unsafe because of a temporary trough on one day.
- 250 requests times a 90-day curve is computationally cheap.

## Plan Selection

The planner evaluates candidate recommendations:

- Full payment.
- Partial payment.
- Installments.
- Wait.
- Not recommended.

Constraints:

- Installments must match a supplied payment option exactly.
- Installment length must respect the user's `max_installment_months`.
- Partial payment is allowed only when both request and user preferences permit it.
- Spending changes can only touch recurring, flexible, non-protected categories the user is
  willing to adjust.
- The selected plan must remain safe throughout the forecast.

Ranking intuition:

The engine prefers plans that complete the request safely, avoid unnecessary spending changes,
respect user preferences, minimize cost and complexity, and start earlier where possible.

## Validation And Failure Isolation

The output contract is strict, so the project includes deterministic validation.

Validator responsibilities:

- Exact column order.
- One row per request.
- Allowed values for status and method.
- `amount_safe_to_pay` bounded between 0 and requested amount.
- Valid payment-plan syntax.
- Installment plans matching supplied options.
- Partial-payment plans summing exactly to the requested amount.
- Spending changes limited to allowed flexible events.

The pipeline has a per-request error boundary. If one request fails unexpectedly, the run still
emits a contract-valid fallback row and records the failure. For the final checked output, fallback
rows were validated as zero.

Interview framing:

"I optimized for never losing the entire run because one request was malformed. Validity and
observability came before cleverness."

## Prompt-injection And Untrusted Evidence

Messages and images are treated as untrusted evidence.

Defenses:

- Prompt explicitly says message text is data, not instruction.
- The model can only emit structured amendment records.
- Amendments are revalidated against known messages, events, categories, dates, currencies, and
  confidence thresholds.
- The deterministic engine owns conflict resolution.
- Unsupported or suspicious facts degrade to no amendment instead of becoming a bad decision.

Good interview line:

"The model never receives authority over the decision. It can only propose typed facts, and the
engine decides whether those facts are admissible."

## Static Website Design

The website was intentionally built as a static explorer.

What it does:

- Shows precomputed purchase decisions.
- Lets a user explore rows as if chatting with an assistant.
- Displays chart data inside the chat when requested.
- Uses clearer labels based on purchase/request content instead of opaque `request_xx` labels.
- Links to the deployed GitHub Pages demo.

What it does not do:

- It does not call an actual LLM.
- It does not rerun the Python engine.
- It does not accept arbitrary new financial histories and compute fresh decisions.
- It does not expose an API key.

Why:

- GitHub Pages is static hosting only.
- Client-side model calls would expose secrets.
- The graded challenge is a terminal batch job, not a hosted app.
- A precomputed explorer is safe and demo-friendly.

If asked why the chat feels like an LLM but is not one:

"The web demo is a conversational interface over fixed, precomputed results. It is a demo surface,
not the scoring engine."

## Testing And CI

Testing strategy:

- Unit tests for loaders, FX conversion, cash-state semantics, recurrence, safety, planning,
  validation, writing, evidence extraction, model-client behavior, usage reporting, and static
  explorer bundle integrity.
- End-to-end checks that produce a valid 250-row output.
- Sample scorers against solved rows.
- GitHub Actions on push.

Current verified state:

- Full local test suite: 461 tests passed.
- `output.csv` contract check: passed for 250 rows.
- Latest CI after documentation update: passed.

Good interview line:

"Because the challenge is evaluated by a strict CSV contract, I treated formatting and validation
as first-class behavior, not as an afterthought."

## Tradeoffs To Be Ready To Explain

### Deterministic engine vs end-to-end LLM

Chosen: deterministic engine.

Reason: exact arithmetic, constraints, dates, and reproducibility matter more than open-ended
reasoning.

Rejected: ask an LLM to directly write all output fields.

Risk of rejected path: hallucinated facts, invalid plans, inconsistent CSV fields.

### Daily simulation vs monthly aggregation

Chosen: daily simulation.

Reason: affordability can fail on a single day because of a pending debit or scheduled expense.

Rejected: monthly summaries.

Risk of rejected path: missing temporary balance troughs.

### Conservative recurrence detection vs aggressive inference

Chosen: evidence-gated recurrence.

Reason: inventing future income or expenses can flip decisions incorrectly.

Rejected: aggressively projecting weak patterns.

Risk of chosen path: may miss some true future events.

### Provider-reported usage vs local token estimates

Chosen: provider-reported usage from OpenRouter responses.

Reason: the challenge asks for model calls, input/output tokens, and cost for the final run.

Rejected: local tokenizer estimates.

Risk of rejected path: numbers might not reconcile with billed usage.

### Static website vs live hosted backend

Chosen: GitHub Pages static explorer.

Reason: safe, free, simple, no exposed API key, and aligned with demo needs.

Rejected: live backend during hackathon.

Risk of chosen path: user cannot upload arbitrary new data and receive real fresh decisions in the
website.

## Known Limitations

- The website is not a real LLM and does not compute new decisions.
- The submitted `output.csv` depends on the final generated state; rerunning without the model key
  follows the deterministic offline path and may not reproduce the same rows.
- The architecture includes vision extraction, but the measured final usage report does not show
  billed vision-model calls.
- Recurrence detection is conservative by design, which avoids unsupported projections but can miss
  some true future patterns.
- Explanation quality is guarded, but still secondary to numeric decision correctness.

## Strong Answers To Likely Interview Questions

### Why did you not use an LLM to make the final decision?

Because the hard part is not prose, it is constrained financial simulation. The final decision must
respect exact payment options, dates, minimum balances, pending debits, settled income, and strict
CSV formatting. I used the LLM where it adds value, which is extracting structured facts from messy
messages, then let deterministic code own the arithmetic and contract.

### How do you prevent prompt injection from messages?

Messages are treated as untrusted data. The prompt says not to follow instructions in the message,
but the real protection is structural: the model can only emit a schema-validated amendment. The
engine then validates that amendment against known messages, events, dates, currencies, and
confidence thresholds. The model never gets to write a recommendation directly.

### What happens if the model fails?

Extraction failure degrades to no amendment. The engine still runs and produces a valid row. Also,
each request is isolated, so one bad request cannot stop the full 250-row output.

### What exactly is in the website?

The website is a static explorer over the precomputed results. It presents the decisions in a
chat-like interface and can show charts, but it does not run the engine or call an LLM. It is a demo
surface, not the production decision engine.

### What VLM are you using for images?

The code has a document-image extraction path configured for `openai/gpt-4.1` through OpenRouter.
But the submitted usage report records only `openai/gpt-4.1-mini` calls. So I would phrase it as:
"Vision support exists architecturally, but the measured final submitted run does not show billed
vision calls."

### Why OpenRouter?

It gives one OpenAI-compatible API surface for text and vision models, usage reporting, and model
swapping. The tradeoff is an extra provider dependency, which is mitigated by the offline path,
schema validation, and warm-cache reproducibility.

### How would you improve it after the hackathon?

I would make the website a real upload-and-query app with a backend that runs the same Python
engine server-side. I would not put model calls in the browser. I would also rerun and verify the
vision path so image extraction is reflected in the usage report, then expand evaluation around
image-linked blank amounts.

## Demo Script

1. Start with the problem:
   "The task is to decide whether a user can safely make a purchase, given their balance, future
   commitments, payment options, messages, and exchange rates."

2. Explain the architecture:
   "The core is a deterministic cash-flow simulator. The LLM only turns unstructured evidence into
   typed facts."

3. Show the output:
   "Each row in `output.csv` is one decision, not history."

4. Show the website:
   "This is a static explorer over the decisions. It helps humans inspect the result without
   exposing secrets or changing the scoring path."

5. Mention validation:
   "The validator checks the exact HackerRank contract. The latest output validates for all 250
   rows."

6. Mention measured usage:
   "The final recorded run used 198 model calls and cost under ten cents."

7. Close with the tradeoff:
   "The main design bet was to keep the LLM useful but contained. That makes the result more
   auditable and much less likely to violate the financial constraints."

## Short Closing Pitch

"I built Buy or Wait? as a deterministic financial decision engine with a narrow model-powered
evidence layer. The engine owns the cash-flow simulation, payment-plan constraints, validation, and
CSV output. The LLM helps parse messy messages, but it cannot directly decide affordability or
invent facts. The static website is a demo explorer over the resulting decisions, while the graded
artifact remains the terminal-runnable package and `output.csv`."
