# Finance platform — refactor orchestration plan

Drive the whole VISION refactor as **5 sequential PRs**, each one green on its own,
each merged to `main` with a real merge commit. This doc is the source of truth
for how the agent works and what each PR contains. Two things live here:

1. **The standing brief** — the rules the agent follows on every PR. Drop this into
   the repo as `CLAUDE.md` (or `AGENTS.md`) so it's always in context, *or* paste it
   once at the start of a session.
2. **The 5-PR plan** — scope, ordering rationale, and acceptance criteria, each tied
   back to specific audit finding IDs (T#, D#, S#, A#, F#) so the agent stays grounded
   in the audits instead of improvising.

Then at the bottom: the **copy-paste prompts** (kickoff, resume-after-review, and the
concrete "start PR1" message).

---

## Part 1 — Standing brief  (→ save as `CLAUDE.md`, or paste once)

> **Role.** You are refactoring this repo to meet the four standards in `VISION.md`:
> correct-by-construction, money-grade integrity, feels-instant, exemplary
> engineering, effortless-to-understand. The five `VISION*.md` files at the repo root
> are audits that already located every gap. Read all five in full before touching
> code. Treat their finding IDs (T1–T6, D1–D16, S1–S4, A1–A4, F1–F12) as the backlog.
>
> **Mindset — greenfield, not incremental.** This is pre-launch. There are no real
> users and no real data; staging is throwaway and the whole VM can be wiped and
> rebuilt from an empty database. So:
> - **Do not preserve backward compatibility.** The React SPA and the generated TS
>   client move in lockstep; break contracts freely and regenerate.
> - **Do not write data-preservation migrations.** Collapse the schema history —
>   drop-and-recreate from empty, or a single fresh baseline migration. Never write a
>   migration whose job is to keep old rows alive.
> - **Do not spend effort on "how do I change this without breaking existing state."**
>   Design as if starting from a blank repo, built by the best engineer in the world.
>   "The right design" always beats "the compatible one."
> - **Do not keep dead code, retired importers, or params "for contract stability."**
>   Delete them (VISION-AUDIT flags several: the SoFi PDF importer, dead params behind
>   `# noqa: ARG001`, the retired PDF path).
>
> **Working style.**
> - **One PR at a time, in the plan's order.** Finish, merge, and get my explicit go
>   before starting the next. Never open the next branch early.
> - **Iterate in green commits.** Slice each PR into commits that each build, type-check,
>   lint, and test. `main` is green after every merge; the branch is green at every
>   commit you push. "Always a working new addition."
> - **A handful of PRs, not a dozen.** The plan is 5. Do not fragment a PR into many
>   small PRs to main — land each numbered PR whole.
>
> **Git & PR discipline.**
> - Branch off latest `main` as **`gduguey/<short-name>`** (see plan for each name).
> - Open a PR to `main`. When merging, **use a merge commit** (`gh pr merge --merge`),
>   never squash or rebase — I want the "Merge pull request #N" commit preserved on `main`.
> - **Commit messages: match this repo's existing convention** — inspect
>   `git log --oneline -40` and look into memory for commit format and follow the exact format already in use (the one we
>   established earlier). Do not invent a new style.
>
> **Gates before every merge — non-negotiable.**
> - The full CI suite is green (backend tests, mypy, ruff, frontend build, Biome, the
>   OpenAPI→TS drift check). If a gate is currently decorative (VISION-AUDIT: Biome
>   `preset:"none"`, RLS untested), the PR that touches that area also makes the gate
>   real — a green check must actually mean something.
> - **Read every CodeRabbit comment on the PR.** Implement the ones that are correct and
>   in-scope; push the fixes. For anything you deliberately don't do, leave a one-line
>   reply saying why (out of scope / superseded by a later PR / disagree because …).
>   Do not merge with unread review comments.
>
> **Keep the audits honest.** As findings are resolved, update the relevant `VISION*.md`
> — check off or annotate the finding IDs this PR closed — so the audit docs track
> reality. This is part of "exemplary engineering," not an afterthought.
>
> **Explicit non-goals (the audits' own "lines not to cross").**
> - No EAV / generic `annotation(type, target, json_payload)` table — keep typed
>   CHECK/FK constraints (DB-audit "line not to cross").
> - No HATEOAS / hypermedia — single first-party SPA, Level 2 REST is correct (F11).
> - Don't merge the two ledgers into one — keep `trades` and `accounting` independent
>   and fix the seam with a real FK (DB-audit consolidation move #1).

---

## Part 2 — The 5-PR plan

Ordering rationale in one line: **the schema is the root.** VISION-AUDIT T2 (the
whole-store God-aggregate) and the DB-audit "target shape" show that redrawing the
data model *dissolves* most speed, concurrency, and correctness findings by
construction. So the keystone schema PR goes first; everything else builds on the
shape it establishes. Within each PR, the listed finding IDs are the checklist.

---

### PR 1 — `gduguey/schema-rewrite`  · the keystone data-model + persistence rewrite

**Why first:** T2 + the DB-audit target shape are upstream of nearly everything.
Because there's no data to preserve, this is a clean cutover — one fresh baseline,
not a migration chain. Splitting it would leave `main` half-migrated and non-green,
so it lands as one PR sliced into many green commits.

**Scope (redraw to the DB-audit "target shape", ~28 tables):**
- Kill the God-aggregate: no more `AccountingStore` load-mutate-`save_store` that
  DELETEs+re-INSERTs ~15 tables per call. Each aggregate root (identity, dimensions,
  accounts, accounting ledger, trades ledger, interpretation, planning, settings)
  loads and writes independently. (T2, D5)
- Replace the three coexisting concurrency models with **one**: per-row optimistic
  versioning; retire the whole-store `store_versions` counter and the
  `session.info["expected_store_version"]` side-channel. (T2, F9)
- **Money is Decimal end-to-end at the DB/domain layer:** `asdecimal=True` NUMERIC
  (or integer minor units); rate/pct columns become NUMERIC not `double precision`;
  a single rounding/quantize policy; delete the tolerance-masking constants. (T1, D6)
- **Structural RLS:** a helper every tenant table routes through, so "has `user_id`"
  structurally implies "has FORCED policy"; add the three missing tables' coverage;
  add a test that asserts *every* tenant table has a forced policy (fails if one is
  ever added without one). (T3, D7)
- **Index the FKs + `(user_id, hot_col)` composites** everywhere; PKs to sequential
  (UUIDv7 or bigint) to fix insert locality and stop orphaning-on-hash-drift. (D1, D2, D3)
- **Ledger seam:** replace `Account.external_ref = "trades"` string glue with a real
  typed FK; normalize the sign convention at one boundary. Keep two ledgers. (DB-audit move #1)
- **Structural invariants that are Python-only today:** 2-level category/account depth,
  `category_id`/`subcategory_id` pairing, per-transaction zero-sum, "one remainder"
  partial-unique, money positivity CHECKs, `transactions` owns its own date/description/
  balance invariant. (Standard 1 findings, D8, D10)
- **Immutable postings + overlay interpretation:** stop the in-place category write;
  all interpretation lives in the ~5 overlay tables with precedence declared in data.
  Merge genuine synonyms only (rules≈patterns→`categorization_rules`, pending≈dismissed→
  `suggestions`, manual transfer→real two-posting transaction). (T6, D14, DB-audit move #2)
- **Dimension tables** (`currencies`, `institutions`, `securities`) retiring the repeated
  `currency`/`symbol`/`institution` strings + 15 duplicated CHECKs. (D-audit move #3)
- **Merge duplicated planning tables** (`budgets`+`general_budgets`; `recurring_additions`+
  `withdrawal_priority_entries`→`goal_automations`). (DB-audit move #4)
- `created_at`/`updated_at` on every table; centralize + size the connection pool; fix
  the double-commit. (D13, D15, D16)

**Acceptance:** app boots and all existing feature flows work against the new schema
on a freshly recreated DB; the RLS-coverage test exists and passes; a money-exactness
smoke test passes; no `AccountingStore` God-object and no `session.info` version
side-channel remain; `git grep` shows no `float` on money model fields at the DB/domain
layer; CI green; CodeRabbit addressed; VISION-AUDIT T2/T3 + DB-audit D1–D14 annotated.

---

### PR 2 — `gduguey/read-paths`  · bounded, non-replaying reads (the server half of "instant")

**Why here:** the target shape makes per-aggregate loading possible; now make reads
stop replaying all of history and stop binding a param per transaction.

**Scope:**
- **Kill the 65,535-param hard 500 ceiling** — replace giant `IN`/param lists with a
  join against a driving table or `= ANY(array)`. This is a correctness/availability
  cliff, not a perf nit. (S2, D4)
- **Pagination + hard max caps** on `/postings`, `/store`, `/ledger/export`
  (`limit`/`offset`, defaults, DoS cap; optional `filter`/`sort`/`fields`). (S1, T5, F3)
- Reads are **O(what's shown), not O(total history):** stop full-ledger replay on every
  `/store`/`/postings`; remove the double `load_ledger` in `/postings`; interpretation
  is now overlay-table lookups with declared precedence, not a 6-step replay. (S1, T5)
- Add per-request time bounds where the audit flagged their absence. (T5)

**Acceptance:** a seeded 100k-transaction user does **not** 500 and `/store`/`/postings`
stay well under the "instant" budget on server compute; reproduce with the speed-audit
harness (`scratchpad/speed_probe.py`) and record the new curve in VISION-SPEED-AUDIT;
CI green; CodeRabbit addressed.

---

### PR 3 — `gduguey/api-contract`  · REST contract to Azure-guide standard

**Why here:** the resources are settled after PR1/PR2, so lock the HTTP contract once
and regenerate the client.

**Scope:**
- `201 Created` + `Location` on every create; `204 No Content` on every delete (drop the
  id-echo bodies). (A1, A4, F10)
- `PATCH /{id}` with `application/merge-patch+json` for **every** editable resource;
  retire whole-collection PUT as the default update path (it was the God-aggregate
  leaking into HTTP). (A2)
- `POST /api/sync` → `202 Accepted` + `Location` to the progress endpoint; `303` on
  completion. (F2)
- Introduce the **`/v1/`** namespace now, while it's free. (F1)
- Unify the `/api` vs `/api/accounting` prefix split and the split "settings" concept;
  split the 1502-line god-router into per-resource routers. (F5, VISION-AUDIT backend)
- Route responses through **API-layer schemas** (`api_models`), never ORM/DB types, so
  the wire contract and persistence evolve independently. (F12)
- Tidy the bare-verb endpoints (`/rebuild`, `/detect`, preview/apply) into resource
  actions where it reads better. (A3, F6)
- Regenerate the OpenAPI schema + TS client; the drift check passes.

**Acceptance:** every mutation returns the right status + `Location`; no whole-collection
PUT remains as a primary update path; `/v1/` in place; generated TS client rebuilt and
compiling; CI green; CodeRabbit addressed; API-audit A1–A4 + F1/F2/F5/F10/F12 annotated.

---

### PR 4 — `gduguey/frontend-instant`  · the client half of "instant" + god-component teardown

**Why here:** depends on the settled API shape + regenerated client from PR3.

**Scope:**
- **Compression:** gzip/brotli at the Caddy edge **and** compression middleware in the
  app — the built 1.58 MB bundle is currently served uncompressed; the "453 kB gzip"
  is never realized. (You can front-load this as commit 1 for an immediate win.) (S3)
- **Code-splitting:** `React.lazy`/`Suspense`, drop `import.meta.glob({eager:true})`;
  heavy pages no longer paid for on landing; add a bundle-size budget. (Standard 3 FE)
- **Optimistic core interaction:** categorizing a posting paints locally and reconciles;
  it currently invalidate-waits on a full double `/postings` replay. Extend optimistic
  coverage to the reversible edits the audit lists (budgets, accounts, opening balances,
  contributions, reorders, tag rename). Creates stay non-optimistic per VISION. (Standard 3)
- **Kill the invalidate-all storm:** replace the bare `['accounting']` prefix invalidation
  with scoped invalidation; remove the module-global mutable `lastKnownStoreVersion`. (Standard 3, T2 FE)
- **Decompose god-components:** `TransactionsTab.tsx` (1662 lines), `useAccountingData.ts`
  (1147 lines / 91 exports); de-duplicate the `update()/remove()/add()` editor triples.
- **Money-safe JS:** stop re-summing/converting money in JS floats across the ~15
  chart/table components; consume the API's exact decimal strings. (T1 FE)

**Acceptance:** measured first-load transfer is the compressed size, not 1.58 MB;
categorize + the listed edits are optimistic (no spinner round-trip on the happy path);
one edit no longer triggers a refetch storm; no component over ~400 lines in the touched
areas; CI + bundle budget green; CodeRabbit addressed.

---

### PR 5 — `gduguey/gates-and-clarity`  · make the guarantees real + finance clarity

**Why last:** it hardens and verifies everything the earlier PRs built, and closes the
"nothing tests/measures the guarantees" theme plus the clarity findings.

**Scope:**
- **Turn the gates on:** Biome real preset (a11y + dead-code rules), not `preset:"none"`;
  a green lint must mean something. (T4)
- **Tests that exercise the guarantees:** RLS isolation tested through *migrations* +
  the `app_runtime` role (not `Base.metadata` as owner); money-exactness **property
  tests** with `hypothesis`; frontend component/integration tests (the ~4% coverage is
  the gap); import-idempotency tests **beyond byte-identical files** — description drift,
  pending→posted drift, bank-vs-canonical id mismatch. (T4, T6)
- **Perf in CI:** bundle-size budget; INP/web-vitals instrumentation
  (`PerformanceObserver`); a load/latency check so S1/S2 can't silently regress. (T4)
- **Finance clarity (Standard 5):** fix "unallocated money" conflating a cumulative flow
  with available cash; stop converting historical FX income/expense at *today's* rate;
  fix the sign conventions (card refund reading as income; green "+" on a shortfall);
  add the missing Overview hero-card tooltips; correct the "alpha vs HYSA" label misuse. (Standard 5)

**Acceptance:** Biome preset is real and passing; RLS test runs through migrations as
`app_runtime` and proves isolation; hypothesis money tests pass; frontend coverage
materially up with component/integration tests; CI has a bundle budget + web-vitals
check; the Standard-5 items are fixed with tooltips/labels corrected; VISION-AUDIT T4/T6
+ Standard 5 annotated as closed.

---

## Part 3 — Copy-paste prompts

### 3a. Kickoff (paste when starting any PR)

```text
We're executing REFACTOR-PLAN.md, PR <N> (`gduguey/<short-name>`). Follow CLAUDE.md /
the standing brief exactly: greenfield mindset (no backward compat, no data-preserving
migrations, staging is wipeable — design as if starting blank, built by the best
engineer alive).

Before writing code:
1. Read all five VISION*.md files in full if you haven't this session.
2. Restate PR <N>'s scope and acceptance criteria from the plan in your own words, list
   the exact audit finding IDs it closes, and give me your commit-by-commit slicing.
3. Flag anything in the scope you think is wrong or mis-ordered. Then wait for my "go".

Once I say go: branch `gduguey/<short-name>` off latest main, work in green commits
(each builds+types+lints+tests), match the repo's existing commit-message convention
(`git log --oneline -40`), and keep going until the acceptance criteria are met and CI
is green. Then open the PR to main and stop — do not merge yet.
```

### 3b. Resume after CI + CodeRabbit (paste once the PR is open and review has run)

```text
The PR is open and CI + CodeRabbit have run. Now:
1. Go through every CodeRabbit comment. Implement the ones that are correct and in-scope;
   push the fixes. For each one you don't act on, reply on the thread with a one-line
   reason (out of scope / superseded by PR <M> / disagree because …).
2. Get all CI checks green.
3. Update the relevant VISION*.md to annotate the finding IDs this PR closed.
4. When everything is green and every comment is addressed, merge with a MERGE COMMIT
   (`gh pr merge <#> --merge`) — not squash, not rebase. Confirm main is green after merge.
Then summarize what landed and stop. Do not start the next PR until I tell you to.
```

### 3c. Concrete start message for PR 1

```text
Start with REFACTOR-PLAN.md PR 1 (`gduguey/schema-rewrite`) — the keystone data-model
+ persistence rewrite. This is the greenfield cutover: redraw to the DB-audit "target
shape" (~28 tables), Decimal money end-to-end, structural RLS with a coverage test,
FK + (user_id, hot_col) indexes, sequential PKs, the real-FK ledger seam, immutable
postings + overlay interpretation, and delete the AccountingStore God-aggregate and the
session.info version side-channel. No data to preserve — one fresh baseline, no migration
chain. Follow the kickoff protocol: read the audits, restate scope + finding IDs (T2, T3,
T6, T1-partial, D1–D14), give me your commit slicing, flag concerns, then wait for my go.
```

---

## One-glance summary

| PR | Branch | Closes | Leaves main… |
|----|--------|--------|--------------|
| 1 | `gduguey/schema-rewrite` | T2, T3, T6, D1–D14, half of T1 | on the new schema, God-aggregate gone |
| 2 | `gduguey/read-paths` | S1, S2, T5, D4 | reads bounded, no 65k 500 ceiling |
| 3 | `gduguey/api-contract` | A1–A4, F1, F2, F5, F10, F12 | REST contract to Azure guide, `/v1/` |
| 4 | `gduguey/frontend-instant` | S3, Standard 3, rest of T1 | compressed, code-split, optimistic |
| 5 | `gduguey/gates-and-clarity` | T4, rest of T6, Standard 5 | gates real, guarantees tested |

Sequential. Each green. Five merge commits on `main`, not a dozen.
