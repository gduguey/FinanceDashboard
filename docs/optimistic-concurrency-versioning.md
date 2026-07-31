# Optimistic concurrency & versioning: when to check a version, and when not to

## The problem this whole topic exists to solve

Two requests read the same row, both compute a change based on what they
read, and both write back. Whichever write lands second silently
overwrites the first one's effect — the first user's change is just
gone, with no error, no signal, nothing. This is the **lost update**
problem, and it's the entire reason "versioning" exists as a topic at
all. If two writes can never disagree about what the final state should
be (more on this below), there's no lost-update risk and no need for any
of this.

## Two families of solution

- **Pessimistic locking** — a client locks the row before editing it;
  everyone else is blocked from touching it until the lock releases.
  Requires the server to hold state about who has what locked, and to
  handle a client that locked something and then vanished (crashed,
  closed the tab). [Generally considered a poor fit for stateless HTTP
  APIs](https://scriptin.github.io/2014-08-30/restful-http-concurrency-optimistic-locking.html)
  for exactly that reason — REST doesn't want the server remembering
  client state between requests.
- **Optimistic locking** (a.k.a. optimistic concurrency control, OCC) —
  nobody locks anything. Every write carries the version it was based
  on; the database rejects the write if that version is no longer
  current. [Wikipedia's overview](https://en.wikipedia.org/wiki/Optimistic_concurrency_control)
  frames this as the right default whenever conflicts are expected to be
  *rare* — you pay almost nothing on the common case (no lock
  acquisition, no blocking) and only pay when a real conflict happens.

[Martin Fowler's **Optimistic Offline Lock**](https://martinfowler.com/eaaCatalog/optimisticOfflineLock.html)
pattern (PoEAA) is the canonical name for the optimistic version, scoped
specifically to "offline" in his sense — a business transaction that
spans multiple system transactions/requests, the way a user editing a
form across several seconds-to-minutes is "offline" relative to the
database transaction that finally saves it. His guidance: use it when
you expect low contention and want every user able to work concurrently
without waiting on each other; reach for **Pessimistic Offline Lock**
instead only when conflicts are actually likely and you'd rather block
than ever reject a write.

## Granularity: how much of the data does one version number cover?

Three levels, in increasing granularity:

1. **Whole-document / whole-record-set version** — one counter covers
   *every* row of every table for a user or tenant. Any write to
   anything bumps it, so any two concurrent writes to *completely
   unrelated* rows still conflict with each other. Cheap to implement
   (one counter), but the false-conflict rate scales with how much
   unrelated data shares that one counter — two users, or even one user
   in two browser tabs, editing entirely different records can end up
   spuriously blocking each other.
2. **Row-level version** — one counter per row (Rails' `lock_version`,
   JPA's `@Version` column, a plain `version` integer column). [Baeldung's
   JPA guide](https://www.baeldung.com/jpa-optimistic-locking) describes
   the standard mechanism: the `UPDATE`'s `WHERE` clause includes both the
   row's `id` *and* the expected version; if another write already
   landed, zero rows match and the caller can tell a lost race happened.
   This is the [standard, most commonly implemented
   granularity](https://rite2rohit88.medium.com/concurrency-control-with-versioning-5ed6feacfecf)
   in practice — fine enough to stop cross-record false conflicts, coarse
   enough to stay simple.
3. **Field-level / property-level** — no single version for the row at
   all; each *field* independently takes whichever value arrived last.
   This is what Figma and Linear actually run in production (next
   section).

## What Figma and Linear actually do in production

[Figma's multiplayer engineering post](https://www.figma.com/blog/how-figmas-multiplayer-technology-works/)
describes a **server-ordered, property-level last-writer-wins** design,
explicitly compared to (but simpler than) a CRDT last-writer-wins
register: "Figma's multiplayer servers keep track of the latest value
that any client has sent for a given property on a given object... we
don't need a timestamp because the server can define the order of
events." Two clients editing *different* properties of the same object
both just succeed — there's no row-level conflict at all, because there's
no row-level version. Two clients editing the *same* property: whichever
one the server received last simply wins, no rejection, no conflict
error, nothing for either user to resolve. They explicitly rejected both
classic Operational Transformation (too combinatorially complex) and
pure CRDTs (too much decentralization overhead) in favor of this simpler
server-authoritative scheme.

[Linear's sync engine](https://liveblocks.io/blog/understanding-sync-engines-how-figma-linear-and-google-docs-work)
does the same thing under a plainer name: "last update wins" per field —
whatever value arrives last for a field is that field's value, full stop.

The reason this works for them and isn't reckless: a property like an
object's fill color, or a boolean's on/off state, is **idempotent and
commutative** — the *only* thing that matters about the end state is
which value was actually wanted, and every client agrees on what
"wanted" means (whatever was set last). There is no way to "merge" two
conflicting fill-color intents that's better than "the most recent one
wins" — unlike two people's edits to the same paragraph of prose, where
whichever one didn't win is real, lost work.

## So: when should a field actually be version-checked?

The dividing line these sources converge on is **whether losing the
race is actually a loss**:

- **Version-check it** when a silently-discarded write would destroy
  real, unrecoverable intent — free text someone typed, a monetary
  amount, anything where "the last write wins" could mean *the wrong*
  value wins and nobody notices. Two different edits to the same prose
  field, or the same numeric field, landing out of order is a genuine,
  silent data-loss bug, not a UX nit.
- **Skip the version check (last-write-wins)** when every possible write
  converges on the same outcome regardless of arrival order, or when an
  older write silently losing to a newer one is exactly what was wanted
  anyway. A boolean toggle is the textbook case: clicking on→off→on
  should end up **on**, and it doesn't matter that the middle "off"
  write's effect never actually landed — the middle click wasn't the
  final intent. Version-checking a toggle just turns "the system
  correctly figured out what was wanted" into a false-positive conflict
  error.

[Enterprise Craftsmanship's piece on optimistic
locking](https://enterprisecraftsmanship.com/posts/optimistic-locking-automatic-retry/)
makes the same point from the opposite direction, and adds a sharp
corollary: **"if the user is fine with overriding other users' changes,
just don't implement the locking [...] the default 'last transaction
wins' mode will suffice."** Locking isn't a default to add everywhere "to
be safe" — it's a deliberate choice for the specific fields where an
overwrite is a real loss.

### The corollary this same article makes about conflicts that *do* get flagged

When a version check *does* reject a write, don't auto-retry it. "An
optimistic locking assumes a manual intervention in order to decide
whether to proceed with the update" — the whole point of rejecting the
write is that a person needs to look at what changed and decide, not
have the client silently retry-and-clobber (which just moves the
lost-update bug one layer down) or silently retry-and-discard (which
loses the user's own edit instead). Auto-retry is only appropriate for
genuinely transient failures (a dropped connection, a 503), never for a
real version conflict.

## A related but different problem: duplicate creates

Everything above is about **losing an update to existing data**.
Duplicate `POST`s — a double-click, a client retrying after a timeout
that actually succeeded server-side — are a different failure mode with
a different fix: an **idempotency key**. [Stripe's approach](https://docs.stripe.com/api/idempotent_requests)
is the reference design: the client generates a random key (a v4 UUID)
once per logical operation and sends it on every retry of that same
operation; the server remembers the first response for that key (Stripe
keeps it 24h) and returns the exact same result for any repeat, even a
failure, instead of executing it twice. This has nothing to do with
version numbers — it's a separate mechanism for a separate problem
(preventing an operation from *happening* twice, vs. preventing a write
from *overwriting* another write).

## Implementation mechanics: three ways to carry "the version"

- **A plain version column + conditional `UPDATE`** — what Rails'
  `lock_version` and JPA's `@Version` do under the hood. The client gets
  the version back on every read and sends it back on every write; the
  `UPDATE ... WHERE id = ? AND version = ?` either matches and bumps, or
  matches zero rows and signals a conflict. [Vlad Mihalcea's JPA/Hibernate
  writeup](https://vladmihalcea.com/optimistic-locking-version-property-jpa-hibernate/)
  is a good deep dive on this exact mechanic. One explicit convention
  worth keeping: **never let the client set the version directly** — only
  ever echo back what the server last issued.
- **HTTP-native: `ETag` + `If-Match`** — the same idea expressed as
  transport-level headers instead of a request-body field. The server
  returns an `ETag` on `GET`; the client sends it back as `If-Match` on
  the next write; a mismatch gets a `412 Precondition Failed`.
  [Kevin Sookocheff's writeup](https://sookocheff.com/post/api/optimistic-locking-in-a-rest-api/)
  and the [Ed-Fi API guidelines](https://docs.ed-fi.org/reference/data-exchange/api-guidelines/design-and-implementation-guidelines/api-implementation-guidelines/handling-optimistic-concurrency-with-etags/)
  both cover this well. This buys standard HTTP conditional-request
  semantics that any client already knows how to speak, at the cost of
  correctly implementing weak/strong ETag comparison and the
  cache-validation semantics `ETag`/`If-Match` were originally designed
  for — worth it mainly when independent, third-party clients need to
  interoperate over plain HTTP; a plain version field in the request/
  response body is simpler when the client and server are developed
  together.
- **Timestamp-based versioning** — using `updated_at` as the "version"
  instead of an integer counter. Works, but is [generally considered
  weaker](https://blog.appsignal.com/2021/10/20/optimistic-locking-in-rails-rest-apis.html)
  than a monotonic integer: clock skew across app servers, and multiple
  writes that happen to land within the same clock tick, can both
  produce a timestamp collision that a plain incrementing counter can't.

### 409 vs 412 — which status code

There isn't full consensus, but the most common framing: **412** is for
requests that used an explicit conditional header (`If-Match`,
`If-Unmodified-Since`) and that precondition failed — it's really an HTTP
caching/conditional-request concept that concurrency control happens to
reuse. **409** is the broader "this request conflicts with the resource's
current state" code, appropriate whether or not a conditional header was
even involved, and is what most APIs use for a plain version-field
mismatch.

## Decision checklist

1. **Does a lost update actually destroy something?** If yes → give the
   field(s) a row-level version check. If every possible outcome
   converges to the same thing regardless of write order → skip the
   version check for that field, last-write-wins.
2. **Is the risk "two edits landing out of order" or "the same logical
   create firing twice"?** The first is versioning; the second is an
   idempotency key (Stripe section above) — don't reach for a version
   column to solve a duplicate-create problem.
3. **Does the operation already have no state to conflict over?** A
   `DELETE` on an already-deleted row has nothing left to check against —
   RFC 7231 already calls `DELETE` idempotent, and in practice services
   return `404`/`410` on a repeat delete rather than a conflict of any
   kind. A delete generally needs no version check at all.
4. **When a real conflict does get flagged, surface it — don't
   auto-retry it.** A human needs to see what changed and decide, per
   Enterprise Craftsmanship's point above.
5. **Prevent a user's own rapid-fire clicks from racing themselves**, as
   a UI concern separate from all of the above: debounce a
   rapid-click-shaped control (collapse a burst of clicks into one
   request after a short quiet period — the [standard debounce
   pattern](https://kannanravi.medium.com/implementing-efficient-autosave-with-javascript-debounce-techniques-463704595a7a)
   used for autosave/search-as-you-type), or disable the control while a
   request is in flight. This is orthogonal to whether the field is
   version-checked at all — it just reduces how often redundant writes
   get generated in the first place.

## What this repo actually does

One mechanism, at exactly one granularity: **row-level** (level 2 above).

`db.base.check_and_bump_row_version` is the whole of it. It runs a single
atomic statement — `UPDATE ... SET version = version + 1 WHERE id = ? AND
user_id = ? AND (? IS NULL OR version = ?)` — so the check and the bump
can't be split by a concurrent write. It is used against the `version`
column on two tables, the ones that hold genuinely conflicting user
intent:

- `accounting.goals` — a goal's name, target amount, target date;
- `accounting.categorization_rules` — the match text and what to do with
  a match, for both of its effects: a `transfer` row's accounts,
  priority and exclusion list, a `categorize` row's category. (These
  were two tables, `transfer_rules` and `category_patterns`, before they
  merged; the `version` semantics did not change with them.)

The client sends the version it last read back as `expected_version` in
the **request body** (not a header, not an `ETag`) on `PATCH
/goals/{id}`, `PATCH /transfer-rules/{id}`, `PATCH
/category-patterns/{id}`. A mismatch raises
`db.base.VersionConflictError`, translated to an HTTP **409** by one
global handler in `trades.api.api`; the frontend turns that into a
`RowVersionConflictError` and shows a toast with a Reload action
(`web/src/App.tsx`), never an auto-retry — per the "don't auto-retry"
corollary above.

`expected_version: null` deliberately opts a write out of the check,
last-write-wins. That's the toggle case from the checklist: flipping a
rule's `active` switch on→off→on should settle on the last click, not
409 against its own earlier in-flight one. See
`web/src/lib/transferRules.ts`, and the `active` toggles in
`TransferRulesTab.tsx` / `CategoriesTab.tsx`.

**Everything else has no version check at all**, on purpose:

- Deletes — checklist item 3: nothing left to conflict over, so a repeat
  delete is a plain 404.
- Creates — a duplicate `POST` is an idempotency problem, not a
  versioning one (checklist item 2). Nothing here uses an idempotency key
  yet either.
- Pure ordering writes like `PUT /goal-automations/withdrawals/order` —
  the whole ordering arrives at once, so the last one submitted is by
  definition the wanted order.
- `trades.dashboard_settings` — one row per user, edited by the one
  person who owns it, every field an idempotent preference. See
  `trades.dashboard.settings.save_settings`.

### What used to be here, and why it isn't

Two identical **whole-record-set** counters (level 1 above) used to sit
alongside the row-level mechanism: `accounting.store_versions` guarding
every accounting table for a user at once, and
`trades.dashboard_settings_versions` guarding the settings row. Each was
carried in an HTTP header (`X-Expected-Store-Version`,
`X-Expected-Dashboard-Settings-Version`), stashed on the request's
SQLAlchemy session by a router-level dependency, and read back out deep
in the persistence layer.

They were removed because they were the false-conflict failure mode this
document's own level-1 description warns about, and they cost real
complexity to keep: one counter over ~25 tables meant editing a budget
conflicted with renaming an account. Worse, the client-side cache of "the
last version I saw" was global, so the frontend had to serialize batches
of genuinely independent writes (accepting several duplicate-merge
suggestions, adding two transfer rules) into sequential loops purely to
stop them racing their own shared header — sequencing that has since been
replaced with plain parallel requests. Two mechanisms also meant two ways
to be wrong; there is now one.

## Sources

- [Wikipedia — Optimistic concurrency control](https://en.wikipedia.org/wiki/Optimistic_concurrency_control)
- [Martin Fowler — Optimistic Offline Lock](https://martinfowler.com/eaaCatalog/optimisticOfflineLock.html)
- [Martin Fowler — Pessimistic Offline Lock](https://martinfowler.com/eaaCatalog/pessimisticOfflineLock.html)
- [Dmitry Shpika — RESTful HTTP: concurrency control with optimistic locking](https://scriptin.github.io/2014-08-30/restful-http-concurrency-optimistic-locking.html)
- [Enterprise Craftsmanship — Optimistic locking and automatic retry](https://enterprisecraftsmanship.com/posts/optimistic-locking-automatic-retry/)
- [AppSignal — Optimistic Locking in Rails REST APIs](https://blog.appsignal.com/2021/10/20/optimistic-locking-in-rails-rest-apis.html)
- [Baeldung — Optimistic Locking in JPA](https://www.baeldung.com/jpa-optimistic-locking)
- [Vlad Mihalcea — Optimistic locking with JPA and Hibernate](https://vladmihalcea.com/optimistic-locking-version-property-jpa-hibernate/)
- [Kevin Sookocheff — Optimistic Locking in a REST API](https://sookocheff.com/post/api/optimistic-locking-in-a-rest-api/)
- [Ed-Fi Alliance — Handling Optimistic Concurrency with ETags](https://docs.ed-fi.org/reference/data-exchange/api-guidelines/design-and-implementation-guidelines/api-implementation-guidelines/handling-optimistic-concurrency-with-etags/)
- [Figma — How Figma's multiplayer technology works](https://www.figma.com/blog/how-figmas-multiplayer-technology-works/)
- [Liveblocks — Understanding sync engines: how Figma, Linear, and Google Docs work](https://liveblocks.io/blog/understanding-sync-engines-how-figma-linear-and-google-docs-work)
- [Stripe — Designing robust and predictable APIs with idempotency](https://stripe.com/blog/idempotency)
- [Stripe API Reference — Idempotent requests](https://docs.stripe.com/api/idempotent_requests)
- [Kannan Ravindran — Implementing Efficient AutoSave with JavaScript Debounce Techniques](https://kannanravi.medium.com/implementing-efficient-autosave-with-javascript-debounce-techniques-463704595a7a)
- [Rohit Kumar — Concurrency control with versioning](https://rite2rohit88.medium.com/concurrency-control-with-versioning-5ed6feacfecf)
- [DevToolbox — HTTP 409 vs 412 comparison](https://www.dev-toolbox.tech/tools/http-status-codes/examples/http-409-vs-412-conflict-vs-precondition-failed)
