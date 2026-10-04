# "Intelligence": what it is, how it is measured, why there is no ML

The prototype displayed invented numbers (pattern strengths, a precision percentage, a "said it helped" rate, forecasts, "agents", a digital twin, counterfactual replays,
simulated live signals). **All of that was removed.** What remains is a small set of rules that read real rows, state their parameters, and show their evidence.

| # | Rule | Parameters (env / admin) | Output | Evidence shown |
|---|---|---|---|---|
| 1 | **Triage routing** | keywords per service with weight (Admin → Services → Keywords); word-start matching; urgent keywords | service, department, priority, location, asset | the matched words. No "confidence" number |
| 2 | **Urgent language** | keywords flagged *urgent* | priority `critical`; request never blocked on consent; crisis helplines shown | matched urgent words |
| 3 | **Assignment** | — | assigned to the department member with the fewest open requests (heads last on ties) | the assignment event ("automatic") |
| 4 | **SLA** | SLA policies per priority / service; at-risk %; severe multiplier (Admin → SLA) | at risk → breached → severe; escalation notifications; priority raise | timestamps and targets |
| 5 | **Incident cluster** | `CLUSTER_MIN_REQUESTS`=3, `CLUSTER_WINDOW_DAYS`=14 | "open requests piling up at location X" — a *suggestion* | request numbers, services, departments |
| 6 | **Problem suggestion** | `PROBLEM_MIN_INCIDENTS`=2, `PROBLEM_WINDOW_DAYS`=60 | repeat incidents on one asset/location without a problem record | incident numbers |
| 7 | **Support-case suggestion** | `CASE_MIN_REQUESTS`=3, `CASE_MIN_DEPARTMENTS`=3, `CASE_WINDOW_DAYS`=21 | a `suggested` case, visible **only** to people the student consented to; a dismissal suppresses re-suggestion for one window | counts, departments, request numbers — no text |
| 8 | **Answer suggestions** | PostgreSQL full-text search over published public articles | up to 3 articles while the student types | the articles |
| 9 | **Aggregates** | `AGGREGATE_MIN_CELL`=5 | heatmap/care outcomes with small sensitive cells hidden | the note on screen |

## How quality is measured (from rows, always with n)
* **Case rule:** confirmation rate = confirmed ÷ (confirmed + dismissed) by reviewers, plus dismissal reasons. "Not enough decisions yet" is shown until there are some.
* **Support actions:** outcomes recorded by staff (helped / partly / no change), hidden below `AGGREGATE_MIN_CELL`; plus the student's own "did it help?".
* **Requests:** first-response time, resolution time, SLA compliance, and the share of students who said it helped — each with its sample size.

## Why there is no machine learning
ML is justified only when (a) there is enough labelled data, (b) a rule cannot do the job, and (c) the cost of error is acceptable. None holds today:
* A single campus produces hundreds to low thousands of requests a term; reviewer decisions on case suggestions will take terms to reach even a few hundred. That is too little to train, validate and monitor a model fairly.
* The decisions affect students' welfare. A false positive is an unwanted outreach; a false negative a missed one. Auditable thresholds that a counsellor can read, challenge and tune are safer than a model whose reasons are opaque.
* Rules are explainable to the student ("3 requests across 3 areas in 21 days") — a requirement for meaningful consent.
**Criteria to revisit:** ≥ ~1,000 reviewed suggestions with outcomes, a rule baseline to beat on a held-out term, a bias/fairness review across cohorts, a data-protection impact assessment, drift monitoring, and a human always in the loop. Use the confirm/dismiss data PULSE already collects as the labels.

## Changing a rule
Rule thresholds are environment variables (restart the api/worker); routing keywords and SLAs are data, edited live in the Admin screens; every edit is audited.
