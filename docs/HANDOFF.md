# SDD ledger — plan: docs/PLAN.md

## Överlämning – läs först om du tar över

**Vad pågår:** genomförande av `docs/PLAN.md` §9.10, Task 11–17 (transaktionsbrygga och gemensam
AI-gräns), enligt superpowers:subagent-driven-development: en implementerare per uppgift, en granskning
efter varje, en slutgranskning av hela grenen. Den här filen är loggboken. Den checkas in och pushas
efter varje steg, så att en annan modell kan ta över om användningen tar slut.

**Var:**
- Arbetskatalog: `/Users/gt/Documents/redovisning/RedovisningAI/.worktrees/transaction-bridge`, gren
  `claude/transaction-bridge`.
- Push som fast-forward till den gren användaren angett:
  `git push origin claude/transaction-bridge:claude/exciting-tesla-udl9xl`. Ingen pull request.
- Rör aldrig huvudkatalogen `/Users/gt/Documents/redovisning/RedovisningAI`. Där ligger en annan
  sessions oincheckade rapportarbete, och dess lokala gren ligger medvetet efter origin (Ruling R2).

**Miljö:**
- Python-miljön är `services/api/.venv` i arbetskatalogen (`pip install -e ".[dev,worker,ai,s3]"`).
- Testdatabasen är PostgreSQL på 127.0.0.1:54329; fixturen återskapar `rai_test`.
- Webben: `npm ci` är gjort i `apps/web`.
- Kommandon från `services/api`: `.venv/bin/pytest -q`, `.venv/bin/ruff check src tests`,
  `.venv/bin/ruff format --check src tests`, `.venv/bin/mypy` och `.venv/bin/redovisningai eval`.
- Kommandon från `apps/web`: `npm run typecheck` och `npm run build`.

**Arbetsyta (git-ignorerad, bara lokalt):** `.superpowers/sdd/PLAN/` i arbetskatalogen, med briefer
(`task-N-brief.md`), implementerarnas rapporter (`task-N-report.md`), granskningspaket och utdrag av
spec och Global Constraints. `.superpowers/sdd/PLAN/progress.md` är en symlänk till den här filen.
Skripten `task-brief`, `review-package` och `sdd-workspace` finns i skill-katalogen för
superpowers:subagent-driven-development.

**Så tar du över:**
1. Läs loggboken nedan. En uppgift med raden `Task N: complete` är klar – skicka inte ut den igen.
2. För uppgiften som pågår: kör `git status` och `git log` i arbetskatalogen och läs
   `task-N-report.md`. Oincheckade ändringar kommer från en implementerare som avbröts. Låt en ny
   implementerare (samma brief, samma rapportfil) fortsätta från dem i stället för att börja om.
3. Incheckningar som pushats utan granskning står som `unreviewed` i loggboken.
4. Arbetsgången: brief → implementerare → `review-package BASE HEAD` → granskare → rättningsrundor
   (högst fem) → `Task N: complete` → push. Beslut skrivs som `Ruling: vad — varför — kostnad om fel`.
5. Checka bara in den här filen, och med sökväg:
   `git commit -m "docs: update the handoff log" -- docs/HANDOFF.md`. Då följer ingen implementerares
   oincheckade filer med. Pusha sedan.

**Kräver användaren:**
- Task 17:s kalibrering på avslutade perioder som användaren väljer (lokalt, utan AI).
- Tokenmätning av A3 mot OpenAI i testläge på syntetiska data. Användaren fick frågan och invände
  inte; bekräfta kort innan anropet.
- Webbläsarkontroll (tangentbord, 375 px) av bryggtabellen och inställningarna.

**Kvar efter Task 17:**
- Slutgranskning av hela grenen (`c72fa71..HEAD`) på den mest kapabla modellen. Ge granskaren alla
  rader `minor (deferred)`, `deferred concern`, `deferred check` och `Ruling`.
- En samlad rättningsomgång.
- Sammanställ till sist alla `Ruling:`-rader till användaren, med kostnad om fel.

---

Scope: §9.10 "Transaktionsbrygga och gemensam AI-gräns Implementation Plan", Task 11–17 (Task 1–10 in the
same file belong to an earlier, completed plan). Spec: §9.10 (extracted to spec-9-10.md).
Worktree: /Users/gt/Documents/redovisning/RedovisningAI/.worktrees/transaction-bridge, branch
claude/transaction-bridge from c72fa71. Push target (user's standing instruction):
origin claude/exciting-tesla-udl9xl as fast-forward. No pull request.
Workspace files: spec-9-10.md, global-constraints.md, review-focus.md, plan-header.md.

## Setup (2026-09-27 15:55)

- Main checkout /Users/gt/Documents/redovisning/RedovisningAI holds another session's uncommitted report work
  (13 files incl. ai/tasks.py, review/commentary.py, api/routes_review.py, reports/builders.py, tests), last
  edited 12:01–12:48; ListAgents at 15:48: no other Claude session running.
- Worktree venv: services/api/.venv (python 3.13.7, pip install -e ".[dev,worker,ai,s3]"); web: npm ci done.
- Baseline at c72fa71 in the worktree: pytest 260 collected = 258 passed, 2 skipped (PgBouncer); ruff clean;
  mypy clean (25 files); web typecheck clean. Postgres for tests on 127.0.0.1:54329 is up.

## Pre-flight scan

| Pair / task | Produces → consumes | Finding |
|---|---|---|
| 11 ↔ 12–17 | green baseline → every later task | Other session is not running, work uncommitted and red → Ruling R1 |
| 12 ↔ 14 | egress.py (COUNTERPARTY_RE, CounterpartyPseudonyms, PACKAGE_FIELDS) → 14 verifier, a3_transactions, PACKAGE_FIELDS["A3"]; service.py run(egress) → 14 provider_names + pseudonyms to verify; commentary.py build_commentary guard → 14 reuses the same guard | Consistent: 14 refines 12's build_commentary wiring (same guard for a3_transactions and run). |
| 12 ↔ 13 | CounterpartyGuess.surface (12) → 13 guess_counterparty; both edit api/routes_company.py (A1 guard vs new route + /vouchers) | Compatible additive field; different functions. |
| 12 ↔ 15 | wrap_tool + test_egress.py fixtures/helpers (analysis, review, _tools, leaks, strings) → 15's test | Consistent; 15's test uses only names 12 imports. |
| 12 ↔ 16 | PACKAGE_FIELDS["A4"] (12, without company) → 16 adds transactions; 12 removes company from A4 package → 16 client_package | 12's Files list lacks review/analysis.py although client_package lives there → Ruling R3 |
| 13 ↔ 14 | transaction_bridge, BRIDGE_VERSION, target_accounts, TransactionBridge.identified_share_fact_id, BridgePart.*_effect_fact_id → a3_transactions | Consistent field names. |
| 13 ↔ 15 | transaction_bridge, target_accounts → explain_transactions | Consistent. |
| 13 ↔ 16 | part facts CLIENT_SAFE (13) → 16 test asserts CLIENT_SAFE | Consistent. |
| 13 ↔ 17 | LARGE_BOOKING_MIN (13) → calibration default | Consistent. |
| 14 ↔ 16 | select_changes (14) → a4_transaction_summary; approval check → client_package "när godkännandet finns" | client_package has no DB/provider access; signature not specified → Ruling R4 |
| 14 ↔ 15 | approved_ai_providers + provider_names (14) → extended for /ask and build_commentary | Consistent (build_commentary takes extended from callers). |
| 11 ↔ 16 | "Om Task 11 tog bort case_questions ur kundrapporten" | With R1 the base is HEAD, whose builder never renders case_questions → Ruling R5 |
| Task 11 self | wait for other session | See R1. |
| Task 12 self | tests use period_commentary_input(analysis.commentary_package(review)), FakeProvider scripted A3, AIService(max_attempts=2), out.trace.error; spy test posts commentary/meeting/ask | Consistent with code at c72fa71 (FakeProvider.calls, AIOutcome.trace, feedback header "underkändes av granskaren"). |
| Task 13 self | golden numbers (1200 = 1100+500−700+300; X=4000 → 2000/−900; 300/7000; 25000 with signals) | Re-derived by hand: consistent. _client(ledger=...) change is in the Files list (test_metric_explanation_api.py). |
| Task 14 self | migration 0004 own RLS; not in COMPANY_TABLES; tests use approved_ai_providers | Consistent. |
| Task 15 self | tool offered only with extended=True | Consistent. |
| Task 16 self | test relies on demo company having ≥1 case question (asserted with message) | Consistent; builder change needed per R5. |
| Task 17 self | expected report {3; 0.25→4/"0.8818"; 0.5→3/"0.7455"} | Re-derived: 97000/110000, 82000/110000, ROUND_HALF_UP → consistent. |
| Rubric check | tests that assert nothing / mandated duplication | None found. |

## Rulings

- Ruling R1 (Task 11): the other session is not running and its work is uncommitted and red, so it is left
  untouched in the main checkout and Tasks 12–17 run in this isolated worktree from c72fa71, whose suite is
  green — waiting would stall indefinitely and building on the uncommitted work would mix two sessions'
  hunks — cost if wrong: whoever finishes the report work merges it over these commits (conflicts expected
  in ai/tasks.py, review/commentary.py, api/routes_review.py, reports/builders.py, tests); nothing of theirs
  is lost or changed.
- Ruling R2 (push): each task's commits are pushed with `git push origin
  claude/transaction-bridge:claude/exciting-tesla-udl9xl` (fast-forward), per the user's instruction to push
  to that branch; the main checkout's local branch ref is not moved — moving it would rewrite the base
  under the other session's uncommitted files — cost if wrong: the main checkout sits behind origin and
  must pull/rebase before its next push.
- Ruling R3 (Task 12): `company` is removed from the A4 package inside `client_package`
  (review/analysis.py, added to Task 12's files) and from the `/ask` package, instead of at each A4 call
  site — one place serves the meeting route, the CLI and the evals — cost if wrong: a consumer that read
  `company` from the A4 package loses it (the suite would show it).
- Ruling R4 (Task 16): `client_package(review, *, compare_spec=None, extended=False)`; the meeting route
  computes `extended` exactly as Task 14 does for A3 (provider_names ⊆ approved_ai_providers) — the
  package builder has no DB access — cost if wrong: one keyword argument to rename.
- Ruling R5 (Task 16): the base builder (c72fa71) never prints `case_questions`, so Task 16 adds the
  approved ones to "Att diskutera på mötet" — follows from R1 and the plan's own fallback clause — cost if
  wrong: none beyond the plan.
- Ruling R6 (setup): worktree created with `git worktree add` (not EnterWorktree, whose description forbids
  use without an explicit user request and which branches from origin/main) and ignored through
  .git/info/exclude rather than a committed .gitignore change — avoids a commit on the shared branch —
  cost if wrong: the ignore rule is local to this clone.

## Tasks

Task 11: complete (no code; R1 — baseline 258 passed / 2 skipped at c72fa71 in the worktree)
Task 12: dispatched implementer (opus) at 15:57, BASE c72fa71, agent a55453796638cecdb, report task-12-report.md
Task 12: implementer stopped at 17:0x by API rate limit before final verification (changes uncommitted); resumed same agent at 17:30
Task 12: implementer DONE_WITH_CONCERNS, commit 19360eb (275 passed, 2 skipped; ruff/format/mypy clean). Provider files (anthropic/openai) changed so EgressViolation from a tool is re-raised instead of swallowed — needed.
- Ruling R7 (Task 12, concern 1): counterparty slugs in fact ids (tools.py counterparty_spend, variance.py drilldown) leak names to the provider → fixed before review in a separate commit (hashed subjects) — the global constraint forbids names at the boundary — cost if wrong: fact ids change for counterparty facts (stored drafts citing old ids become unrenderable; drafts are fingerprinted anyway).
- Ruling R8 (Task 12, concern 2): the brief's cross-period test excludes the "Okänd motpart" bucket, which is no counterparty — the brief assumed every top-10 row is a named counterparty — cost if wrong: none (test still checks every named row).
- Ruling R9 (Task 12, concern 3): masking also covers counterparty keys and skips number-only/one-character forms — keys leaked otherwise; numbers must not become codes — cost if wrong: a one-character counterparty name is not masked.
Task 12: deferred concern: bare code rendering can collide with a voucher series "M" (e.g. voucher M12 shown as a name in internal text) — for final review.
Task 12: deferred concern: tokens spent before a mid-run egress stop are not recorded in the budget — minor.
Task 12: deferred concern: A4 input is masked, so A4 output may contain bare codes until Task 14 (verifier) and Task 16 (case questions) reject them — carry into 14/16.
Task 12: fix commit 9447b61 (hashed counterparty subjects via counterparty_subject(); 276 passed, 2 skipped). Review dispatched (opus) at 17:37 on c72fa71..9447b61, agent a633afdfac6236714
Task 12: review (opus) on c72fa71..9447b61 — spec ✅ with ⚠️ items; quality Needs fixes (1 Important, plan-mandated: genitive name forms "Telias"/"Microsofts" pass the whole-word mask).
- Ruling R10 (Task 12): the plan-mandated whole-word rule is overridden for Swedish genitive (`s`/`:s` suffix, stem replaced) — the spec forbids names at the boundary, incl. the consultant's A5 question — cost if wrong: a word that is a name plus "s" is masked needlessly.
- Ruling R11 (Task 12, ⚠️ confirmed gap): A1 examples and A2 evidence still carried masked voucher text; the spec says text is removed or replaced with a coded type for all tasks → replaced with Pattern types + counterparty codes in fix round 1 — cost if wrong: A1 mapping suggestions get weaker without example texts.
- Ruling R12 (Task 12 ⚠️ → Task 16): A4 `ask_client` still carries case titles (now coded); spec says A4 gets "ingen ärendetext" → Task 16 replaces titles with the case template's generic title (CaseTemplate.title) and filters codes from case questions — load-bearing for Task 16 — cost if wrong: A4 case questions become more generic.
- Ruling R13 (Task 12 ⚠️): no CALC_VERSION bump for the hashed counterparty fact subjects — stored drafts keep rendered text and are fingerprinted; only live stores use the ids — cost if wrong: an old draft's raw {f:id} for a counterparty fact no longer re-renders.
Task 12: minor (deferred): tool exception text ("Fel: {exc}", "Ogiltiga verktygsargument: {exc}") is not masked by wrap_tool.
Task 12: minor (deferred): PACKAGE_FIELDS is a hand copy of the projections; no test pins that the real A4 client_package/question_draft package reaches the provider (a new key silently turns A4 AI off).
Task 12: minor (deferred): common words/account names become forms (e.g. "Avräkning för skatter och avgifter" → M9); feedback masks the fixed verifier reason too.
Task 12: minor (deferred): names_to_mask silently ignored when egress is given.
Task 12: minor (deferred): guard built at every call site even when AI is off/budget spent (full-ledger scan + big regex) — build lazily or cache per ledger.
Task 12: minor (deferred): EgressViolation lives in ai/egress.py so providers import egress→analytics→domain; move next to ProviderError.
Task 12: minor (deferred): wrap_tool rebuilds ToolSpec positionally; use dataclasses.replace.
Task 12: minor (deferred): _key_of next(...) without default (egress.py:147).
Task 12: fix round 1 dispatched (resume implementer) at 17:49 with R10 + R11
Task 12: fix round 1 commit 48210bc (genitive masking for counterparties; A1/A2 carry voucher_type + counterparty key instead of text; A1 package moved to CompanyAnalysis.mapping_package(); A2 findings no longer send description; 278 passed, 2 skipped).
- Ruling R14 (Task 12): A2 findings stop sending `description` — many rules quote voucher text there; the spec removes text for all tasks — cost if wrong: A2 case explanations lose the rule's prose detail.
- Ruling R15 (Task 12): the person-name genitive gap (Pseudonymizer "Eriks") is fixed in the same round before re-review — same leak class as R10, one line — cost if wrong: none.
Task 12: person-name genitive fix dispatched (same round) at 17:55
Task 12: fix round 1 commit 94313e9 (person-name genitive in Pseudonymizer; shared rule in pseudonymize.py; 279 passed, 2 skipped). Scoped re-review (sonnet) dispatched at 18:00 on 9447b61..94313e9
Task 12: fix round 1/5 (3 addressed, 0 open — genitive counterparty names; A1/A2 voucher text; genitive person names; commits 9447b61..94313e9)
Task 12: complete (commits c72fa71..94313e9, review clean after 1 fix round)
Task 12: pushed 94313e9 to origin claude/exciting-tesla-udl9xl
Task 13: dispatched implementer (sonnet) at 18:13, BASE 94313e9, agent aa12bae5691a4bc9a, report task-13-report.md
- Ruling R16 (Task 13): group facts use counterparty_subject(key) plus target/period in extra; unknown group gets counterparty:unknown — carries R7 forward (fact ids reach the provider in Tasks 14–15) — cost if wrong: none beyond longer ids.
- Ruling R17 (Task 13): web placement — metric-detail component rows open the bridge for their line/category/account target; analysis-finding cards use account:<first account>; loaded on demand — the plan named the places but not the targets — cost if wrong: UI placement to adjust.
- Ruling R18 (Task 13): identified_share_abs are fractions summing to 1 (all 0 when both periods have zero absolute amount) — the brief's test uses 300/7000 — cost if wrong: display formatting only.
Task 13: implementer DONE, commits 6e62dab (engine + route) and af75b94 (web); 284 passed, 2 skipped; ruff/format/mypy/typecheck/build clean.
- Ruling R19 (Task 13): the bridge button reached a 422 for balance components → fixed before review: the server returns bridge_target per component (validated with target_accounts) and the web shows the button only then — a UI control that always fails is a defect — cost if wrong: one extra field in the metric-explanation response.
Task 13: deferred check: live keyboard + 375 px browser check of the bridge table — controller does it at the end on the running app.
Task 13: pre-review fix dispatched (resume implementer) at 18:56
Task 13: pre-review fix commit 2ada0c0 (bridge_target per component/finding; 285 passed, 2 skipped)
Task 13: review (opus) dispatched at 19:15 on 94313e9..2ada0c0, agent ad6d1efc83fa0766b
Task 13: review (opus) on 94313e9..2ada0c0 — spec ❌ (finding-card targets) + ⚠️ large-booking basis; quality Needs fixes (1 Important). Reconciliation checked on 200 demo components: 0 mismatches.
- Ruling R20 (Task 13 ⚠️): stor enskild bokning is judged per account (voucher net and period absolute amount per account), as spec and brief say, not per target basket — cost if wrong: fewer large-booking signals on line/category bridges.
- Ruling R21 (Task 13 minor #8 → load-bearing): bridge facts get a distinct extra so they can never overwrite drilldown/metric facts in a shared store — Task 14 shares review.store — cost if wrong: none.
- Ruling R22 (Task 13 minor #10 → spec): unknown category / unused account targets answer 422 in the bridge route (target_accounts/explain unchanged) — the brief says unknown target gives 422 — cost if wrong: none.
- Ruling R23 (Task 13 minor #4, plan-mandated privacy): masked payroll bridges return only change/totals; part amounts, counts, effects, signals and identified share become null/empty — parts can reveal one person's pay — cost if wrong: payroll-less users see less detail on payroll lines.
Task 13: minor (deferred): signal totals add flagged rows from both periods with the same sign (badge amount is neither a period amount nor an effect).
Task 13: minor (deferred): part voucher counts sum group counts, so a voucher with two counterparties in the same part is counted twice.
Task 13: minor (deferred): count/amount split shown twice (sub-rows and paragraph); GroupRow voucher links don't say which period; "Största motparter" can include the unknown group.
Task 13: minor (deferred): result_effect computed identically in match_reversals and _reversal_candidates.
Task 13: minor (deferred): test gaps — rounding case for X, sign=-1 revenue case, all-zero identified share, /vouchers fallback without exact date.
Task 13: minor (deferred): routes_company.py is ~780 lines (soft ceiling 800).
Task 13: fix round 1 dispatched (resume implementer) at 19:23: finding targets, per-account large booking, fact-id collision, 422 for unknown targets, payroll aggregate
Task 13: fix round 1 implementer stopped by API rate limit before saving anything (worktree clean at 2ada0c0); resumed same agent at 22:32
Task 13: fix round 1 commit 6f90bc7 (289 passed, 2 skipped)
Task 13: scoped re-review (sonnet) dispatched at 22:52 on 2ada0c0..6f90bc7
Task 13: fix round 1/5 (5 addressed, 0 open — finding targets; per-account large booking; fact-id collision; 422 unknown targets; payroll aggregate; commits 2ada0c0..6f90bc7)
Task 13: minor (deferred): dead try/except around target_accounts in _finding_bridge_target's single-account branch (routes_company.py ~578) — account: never raises.
Task 13: complete (commits 94313e9..6f90bc7, review clean after 1 fix round)
Task 13: pushed 6f90bc7 to origin claude/exciting-tesla-udl9xl
Task 14: dispatched implementer (opus) at 23:05, BASE 6f90bc7, agent a5f4642d6cc12537f, report task-14-report.md
- Ruling R24 (Task 14): select_changes drops payroll accounts per target and skips targets left empty (line:personnel) — payroll never goes to AI — cost if wrong: personnel changes are never explained by A3.
- Ruling R25 (Task 14): approval POST validated by Pydantic (data_types Literal list, provider 1–40 chars, valid_to >= valid_from); create/revoke for ADMIN or can_approve_reports; extended recorded in draft metadata — the plan named the roles but not the model — cost if wrong: small API contract change.
- Ruling R26 (Task 14): the live token measurement (plan step) is left to the controller's final verification — subagents must not call a real provider — cost if wrong: the measurement happens later.
- Ruling R27 (handoff, user request "anteckna och puscha allt du gör så en annan modell kan ta över"): the ledger moved to the tracked file docs/HANDOFF.md (workspace progress.md is a symlink to it); after every step it is committed with a pathspec and pushed; commits pushed before their review are marked unreviewed — keeps the work recoverable if usage runs out — cost if wrong: a docs file in the repo that can be deleted after the plan is done.
Status 2026-09-27 23:24: Task 14 in progress — implementer running since 23:0x, no commits yet, uncommitted changes in the worktree (migration 0004, transaction_package.py, repo, routes, verifier, tasks, evals, web).
