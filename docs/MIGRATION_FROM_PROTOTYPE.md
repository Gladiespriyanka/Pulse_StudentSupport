# From prototype to production: what was kept, rebuilt, and removed

The prototype was a front-end demo: nine layered JS files patched one global state object persisted in `localStorage`; the "password" was compared in the browser;
authorization was a client-side function; the clock was simulated; and the "intelligence" was keyword regexes plus invented numbers (its own README says
*"all scores, forecasts and rules are illustrative demo values"*). Nothing needed trust in the browser to be correct — which is the problem.

## Kept and rebuilt on real data
| Prototype feature | Production implementation |
|---|---|
| Role-based sign-in (`pulse123`, client-side) | Real accounts: argon2id, httpOnly JWT cookies, refresh rotation, lockout, CSRF; 7 server-enforced roles |
| Role-scoped views (client `see()` function) | Permission gate per endpoint + row-level rules in SQL (own / department / consent / break-glass) |
| Smart triage: routing preview as you type, urgent fast-path, phone/email masking | `POST /triage/preview` + request creation; keyword rules live in the database (Admin-editable); the matched words are shown; **the invented "confidence" is gone**; PII masked before storage |
| Service catalogue → owner, workflow, SLA, escalation | `services`, `sla_policies`, workflow service; approvals for services that need them |
| SLA timers and escalation engine (High 1d / Medium 3d / Low 7d, "+1 day" button) | Real clocks from timestamps; default tiers kept (resolution 1 d / 3 d / 7 d + a critical tier and explicit response targets); at-risk → breach → severe; worker; no fake time travel |
| SOS; anonymous "worried about a friend" | `POST /requests/sos` (critical, emergency service); peer concern with **no requester, actor or IP stored** |
| Break-glass: extra access only with the student's approval, 48 h, audited | `access_requests` with expiry, revocation, and per-use visibility to the student |
| Privacy toggles, access matrix, audit log, Transparency Receipt | Consent grants (mentor / each department), **access log for students**, append-only audit table |
| Root-cause detection → one joint work order | Cluster *suggestion* (≥3 open requests at a location within 14 days) → **incident** with tasks across departments → **problem** → **change** → knowledge draft |
| Multi-authority case room (thread, invite others, role-scoped) | `cases`, participants gated by the student's consent, care thread, one coordinator |
| Intervention tracking: action, outcome, student feedback | Tasks on a case with outcome (helped / partly / no change); student feedback reopens the action |
| Heatmap by category and time of day, small counts masked | `GET /reports/heatmap` computed from rows in campus time; sensitive cells below `AGGREGATE_MIN_CELL` hidden |
| Weekly briefing (generated) | `GET /reports/briefing`, composed from real aggregates |
| Approval gates (RECOMMENDED → APPROVAL → APPROVED/REJECTED → EXECUTING → COMPLETED), only Command decides | `approvals` for service requests (department head) and changes (head for low risk, Command otherwise); separation of duties; reason required to reject |
| FAQ / policies / knowledge graph | `knowledge_articles` with full-text search; answers suggested while a student types; drafts generated from resolved problems |
| Super Admin account | split into **Command** (aggregates, SLA, approvals — no text) and **Admin** (users, catalogue, SLA, permissions, imports, audit — no content) |
| Browser `localStorage` state | PostgreSQL; the UI stores nothing |
| Demo Mode (39-step scripted replay, auto-approvals, simulated time) | Demo Mode = isolated database + seed + login chips, development/testing only; no scripted replay, no simulated clock |

## Removed — no real data source behind it, or fabricated
These are not hidden or switched off: they do not exist in the code base.
* **Fabricated model outputs:** "pattern strength", confidence percentages, *"precision 81%"*, *"91% said it helped"*, model health & fairness panel, Verify / Predict / Coordinate / Prevent scores.
  → Replaced by **measured** figures (confirm/dismiss rate, feedback rate, SLA compliance) always shown with their sample size.
* **Predictions and simulations:** time-to-crisis forecast, what-if sliders, digital twin, ripple, next-action, zero-silence, dependencies, blind spots, care-fatigue, crisis drill, memory & replay,
  counterfactual "why flagged", predictive SLA health, change-impact simulation, Campus Pulse Index, capacity numbers, playbooks "from similar past cases".
* **"Agents" and Autopilot / Orchestrate:** automation that acted on invented signals. Real automation is limited to what is auditable: routing, assignment, SLA escalation, auto-close.
* **Live simulation and the demo clock**, command palette.
* **Silent signals** (mess swipes, gate exits, LMS logins) and **"what changed vs the student's own baseline":** there is no feed of that data, and surveilling students' behaviour needs a deliberate, consented design.
  If you want it, the honest path is: an attendance/engagement CSV import → a `student_metrics` table → a simple baseline deviation shown to the student first → consent gating. It is *not* included so that no dummy feature ships.
* **Pulse check (mood) and counsellor slot booking:** not implemented in the backend model. Counselling is a normal consent-aware service request today; scheduling and mood tracking are candidates for a later, properly designed release.
* **"Contest / correct my journey":** students can accept or decline outreach and end a case; a full "contest the record" workflow is not implemented.

## Security issues in the prototype that no longer exist
Client-side password check · authorization in the browser · unescaped strings written to `innerHTML` (XSS) · sensitive state in `localStorage` · no audit trail · no rate limiting or lockout · a shared password for every account.

## Data migration
There is none to migrate: the prototype held only demo data in browsers. The old keyword lists became the optional starter catalogue (`python -m app.cli load-starter-catalog`), now editable in Admin → Services → Keywords.
