# The HTTP contract: which method, which status, which body

What every route in `src/accounting/api/routers/` and `src/trades/api/routers/`
promises, and the rule that decided each case. Written down because the rules
are the kind that get re-litigated per route otherwise, and because several of
them are decisions *against* an obvious-looking alternative.

The contract is enforced, not just described: `tests/api/test_status_codes.py`
asserts these rules against the application's own OpenAPI schema, so a route
added later either follows them or fails there.

## Versioning and namespaces

Every route is under `/api/v1/{accounting,trades}`, declared once as a router
prefix per module (`accounting.api.api`, `trades.api.api`), never repeated on a
decorator. `/api` comes before `/v1` because `trades.api.api` serves the SPA
from a `/{full_path:path}` catch-all, and a bare `/v1/...` would look like a
frontend route.

The namespace is per-module rather than resource-first because `/settings`,
`/ledger/export` and `/statements/export` exist in *both* modules — three real
collisions a flat resource namespace could not hold.

Unversioned by design: `/health`, `/docs`, `/redoc`, `/openapi.json`, and
`/api/webhooks/clerk` (renaming that last one would break configuration held
in the Clerk Dashboard, out of band from this repo).

## Creates

**A create that mints its own id answers `201 Created` with a `Location`.**
The id is a fresh `uuid4` or a name slug the route refuses to collide with, so
creation is the only outcome the route has and the status is the whole truth.

**A create that genuinely upserts answers `201` with a `Location` when the row
came into existence and `200 OK` when it replaced one.** A blanket `201` on
these would report a replace as a creation, and would attach a `Location` for a
resource the request did not create. The distinction must come from the write
itself:

- `upsert_budget` reads it out of its own `INSERT ... ON CONFLICT` via
  `RETURNING xmax = 0`.
- `upsert_posting_merge` reads it from the parent-row delete count a
  replace-in-full already computes.
- `post_transfer_rule` reuses the `existing` lookup it needs anyway to carry
  `active` and the exclusion set forward.
- `post_transfer_link` reuses its `already_this_link` lookup.
- `post_category_pattern` and `dismiss_suggestion` do an existence check inside
  the transaction that writes.

What is *not* acceptable is inferring it from a second round trip whose answer
another request could invalidate in between.

### Why most content-derived-id upserts stayed `POST`

An upsert at a caller-known address is really a `PUT`, and one of them became
one: `PUT /dismissed-suggestions/{suggestion_id}`. That entry's id **is** the
suggestion's own id, which the client is already reading off
`GET /transfer-suggestions` — nothing is derived server-side, so the caller can
name the address, and an idempotent replace at a known URL is exactly what
`PUT` means.

Every other upsert kept `POST` on the collection, on one test: **can a client
name the address without reimplementing server-side key derivation?**

| Route | Id derived by | Caller can name it? |
| --- | --- | --- |
| `POST /category-patterns` | `importers.common.row_hash` | No — a hash |
| `POST /transfer-rules` | `importers.common.row_hash` | No — a hash |
| `POST /budgets` | `repositories.planning.budget_row_key` | Only by copying the key format |
| `POST /posting-merges` | `_merge_id`, `merge:{kept_transaction_id}` | Only by copying the prefix |
| `POST /transfer-links` | `make_transfer_link`, sorted pair | Only by copying the format |

Moving those to `PUT /{id}` would export a private key format as part of the
public contract, and break every client silently the day it changed. A `POST`
that reports honestly which of two things it did is the better contract.

`Location` is always built with `request.url_for` through
`accounting.api.locations.location_of`, never formatted by hand: a header
naming an unregistered route then raises at the create instead of shipping an
address that 404s when followed. Every 201 also *declares* its `Location` in
the schema (`CREATED_WITH_LOCATION`), because FastAPI derives bodies from
return annotations and knows nothing about headers — an undeclared header is
invisible to `web/src/types/schema.ts` and indistinguishable from an absent one.

## Updates

**Whole-collection `PUT` is retired.** Twelve routes replaced an entire
collection from a client-held snapshot, which meant a stale client could
resurrect a deleted row or revert a concurrent edit, and every write carried
the whole collection over the wire. Nine of the twelve had no SPA call site at
all and were deleted outright — each resource already had the per-row writes
its editing needed, so adding a `PATCH` for them would have been dead code.

`PUT` remains correct for a **single addressed resource**, and those were kept:
`/accounts/{id}`, `/goal-contributions/{id}`, `/postings/{id}/override`,
`/postings/{id}/split`, `/accounts/{id}/opening-balance`, the `/settings/*`
singletons, and `/dismissed-suggestions/{id}`. The retired thing is replacing a
*collection*, not addressing one resource.

Two exceptions are worth their own note:

- **`PATCH /settings/target-allocation`** takes
  `application/merge-patch+json`. The resource is a `symbol -> percentage` map,
  which is precisely what RFC 7386 is defined for: a number sets, `null`
  removes, an absent key is untouched. Note the client-side consequence —
  `AllocationView` used to rely on a symbol simply being absent from a
  whole-map replace in order to delete it, and now has to send an explicit
  `null` per removed symbol.
- **`PUT /goal-automations/{contributions,withdrawals}/order`** replaced two
  whole-list PUTs. An atomic reorder is not expressible as a series of per-item
  `PATCH`es: it would break the "a `remainder` automation is lowest-priority"
  invariant midway, and a failure halfway would leave the order scrambled. So
  the ordering is treated as what it is — a single addressed sub-resource —
  and `PUT` is right for it. The body is *only* the ordered ids, and a body
  whose id set is not exactly the direction's persisted set is a 400, so field
  edits, insertions and deletions can no longer travel disguised as a reorder.
  Per-automation field edits go to `PATCH /goal-automations/{automation_id}`.

## Deletes

**Every delete answers `204 No Content`.** Fifteen used to echo the id the
client had just sent in the path, through fifteen one-field `*IdResponse`
models; none told a caller anything it did not already know, and no frontend
call site read one.

Three deletes keep a `200` and a body, because each clears or cascades rather
than removing the one row named — so the response holds something the caller
cannot derive from its own request: `DELETE /accounting/categories/{id}`
(cascades, returns the surviving tree), `DELETE /accounting/settings/llm` and
`DELETE /trades/settings/ibkr` (clear fields on a row that still exists). The
exception list is pinned in `tests/api/test_status_codes.py`, so a delete that
starts returning a body without being added there fails, and one that stops
needing the exception fails too.

Note the client-side hazard this created: `response.json()` on a 204 throws, so
both `request<T>` wrappers in `web/src/lib` route through `parseBody`, which
returns `undefined` for a 204. Without it every delete in the app would fail
*after* the server had carried it out.

## Reads

Every created resource has an item `GET` at the address its `Location` names.
Before PR 3 there were **zero** item-GET routes in the whole API: a client
could only re-read the entire collection or `GET /store`. Fourteen were added,
one per resource with a create.

Two of them sit in an address space that also contains literal segments, and
Starlette matches routes in registration order, so the ordering is
load-bearing and commented at each site:

- `GET /goals/{goal_id}` is registered **after** `GET /goals/summary`.
- `GET /budgets/{budget_id}` is registered **after** `GET /budgets/comparison`
  and `GET /budgets/suggested-amount` — which is why those two moved out of
  `routers.dashboard` into `routers.budgets`, so the constraint lives in one
  file instead of depending on `api.py`'s router include order.

A future `GET /<collection>/<literal>` has to go above the item route in the
same module.

## Which models the wire is made of

Every route answers with a model declared in `api.api_models` or
`api.entities` — never with one from `accounting.models`/`trades.models`.
The return annotation *is* the response contract (neither package uses
`response_model=` anywhere), so an annotation naming a domain model makes the
wire format a projection of internal state: a field added there for internal
reasons ships to every client and into `web/src/types/schema.ts` with nobody
deciding it should. Before PR 3, 46 routes named a domain model directly and
14 more reached one through a wrapper field.

`api.entities` holds one mirror per entity a client reads, each restating the
fields it puts on the wire, with `from_domain` as the single copying seam. The
fields a mirror does not declare are dropped there, which is the whole point.
Four mirrors also carry `to_domain`, for the four entities a client *sends* as
a whole (`OpeningBalance`, `ManualTransfer`, `ManualOverride`, `PostingSplit`);
every other write takes a purpose-built body from `api_models`, because a
request body is rarely the shape of the entity it creates.

Two things are deliberately **not** mirrored:

- **The `Literal` vocabularies** — `AccountKind`, `CurrencyCode`,
  `CategoryClassification`, `GoalAutomationMode` and the rest. They are value
  sets, not shapes: they have no fields to leak, they appear in the schema
  inlined rather than as components, and duplicating them would mean a new
  currency had to be added in two places before it could be said. Adding a
  member is a deliberate vocabulary change under either arrangement.
- **Validators and class-level machinery.** A mirror restates fields and
  constraints, not `model_validator`s that police what may be *written*
  (`GoalAutomation`'s schedule check, `LedgerEvent`'s event-type invariants)
  nor `polars_schema`, which is a fact about the analytics frame.
  `tests/api/test_response_models.py` pins the rule against the real routing
  table, walking into type arguments and model fields alike, so a route added
  later either follows it or fails.

## Deliberate non-goals

**No HATEOAS.** (API-audit F11.) A single first-party SPA that is generated
from the OpenAPI schema and deploys in lockstep with the server gets nothing
from runtime link discovery; it would pay for it in payload size and in every
handler needing to know the route table. `Location` on a create is not a step
toward it — it is a create telling the caller where the thing it just made
lives, which is worth having on its own.

**No bare-verb route renames.** (API-audit A3 and F6.) Routes like
`POST /accounts/{id}/close`, `/reopen`, `/categories/{id}/rename`,
`/goals/run-recurring-additions` and `/postings/validate-pending` name
operations that are not the creation, replacement or deletion of a resource.
Recasting each as a noun sub-resource would add a resource that exists only to
justify a method, which is a worse description of what the endpoint does. They
stay as they are.

**`GET /store` is not decomposed.** (API-audit F4.) Its stated defect names a
class PR 1 deleted. What it warranted was the twelve collection GETs — adding
those, not removing the composite read the SPA boots from.
