# Adding support for a new account

Every account you can import CSVs into needs a **standardizer**: a small,
dedicated function that knows exactly how one institution's export, for one
kind of account, maps onto this module's canonical `Posting` schema. This
document describes exactly what to add, and where, when the Import page
flags an account with "no CSV parsing rule registered for this institution
and account kind."

Writing one isn't the only option, though — the Import page can always
fall back to the no-code **canonical CSV importer** (`importers/canonical/`,
see `canonical-csv-import.md`) instead, which guesses column names and
date/amount formats rather than requiring an exact, known shape. Reach for
a dedicated standardizer below when you want a bank permanently
auto-detected with no per-import guessing (and no risk of a format quirk
tripping up the fuzzy parser); reach for the canonical importer when you
just want a one-off or occasional bank's CSV in without writing any code.

## What already exists, and why the warning appears

`accounting/importers/ingest.py` holds a single dispatch table,
`_STANDARDIZERS`, keyed by `(institution, account_kind)`:

```python
_STANDARDIZERS: dict[tuple[str, str], Callable[[str, str], pl.DataFrame]] = {
    ("Chase", "checking"): standardize_chase_checking,
    ("Chase", "credit_card"): standardize_chase_credit_card,
    ("SoFi", "checking"): standardize_sofi_checking,
    ("SoFi", "savings"): standardize_sofi_savings,
    ("SoFi", "vault"): standardize_sofi_savings,
}
```

`GET /api/v1/accounting/supported-import-kinds` exposes exactly this table's
keys, and the Import page's account list cross-references every account
against it — an account whose `(institution, kind)` pair isn't a key here
gets the amber warning icon, because uploading a CSV for it would have
nowhere to go. Adding support means adding one new entry to this table
(plus the function it points at), never changing how the table itself is
read.

## Step by step

### 1. Write a row model for the CSV's own column names

Every institution names its columns differently, and sometimes
inconsistently even across its own account types. That native vocabulary
is only ever allowed to exist in one place: a pydantic model living next to
the importer that reads it, validating the raw CSV's own column names via
field aliases. For example, Chase's checking export becomes:

```python
class ChaseCheckingRow(BaseModel):
    details: str = Field(alias="Details")
    posting_date: str = Field(alias="Posting Date")
    description: str = Field(alias="Description")
    amount: float = Field(alias="Amount")
    type: str = Field(alias="Type")
```

This model is never imported anywhere outside its own institution's
importer package — it exists purely to parse one CSV shape and disappears
immediately after.

### 2. Write the standardizer function

A function named `standardize_{institution}_{kind}`, taking the raw CSV
text and the real account id it belongs to, returning canonical
`Posting`-shaped rows:

```python
def standardize_chase_checking(csv_text: str, account_id: str) -> pl.DataFrame:
    postings: list[Posting] = []
    for raw in csv.DictReader(io.StringIO(csv_text)):
        row = ChaseCheckingRow.model_validate(raw)
        posted_at = datetime.combine(parse_us_date(row.posting_date), datetime.min.time())
        counterparty = UNCATEGORIZED_INCOME_ACCOUNT_ID if row.amount >= 0 else UNCATEGORIZED_EXPENSE_ACCOUNT_ID
        transaction_row_id = row_hash(account_id, row.posting_date, str(row.amount), row.description)
        leg = RawLeg(
            posted_at=posted_at,
            amount=row.amount,
            currency="USD",
            description=row.description,
            meta={"source_type": row.type, "row_hash": transaction_row_id},
        )
        postings.extend(
            posting_pair(
                source="chase-checking",
                row_id=transaction_row_id,
                account_id=account_id,
                counterparty_account_id=counterparty,
                leg=leg,
            )
        )
    return postings_to_frame(postings)
```

The shared helpers in `accounting/importers/common.py` do the repetitive
part of this for every institution:

- `row_hash(...)` — a stable content hash used to detect a row that's
  already been imported, so re-uploading the same statement twice (or an
  overlapping date range) never double-counts it.
- `posting_pair(...)` — builds the two-posting pair for one raw row: the
  real leg against `account_id`, and a placeholder leg against whichever
  of the two virtual accounts matches the amount's sign (positive →
  `uncategorized:income`, negative → `uncategorized:expense`) — no rule
  matching or transfer detection happens at this layer; that's a separate,
  later step over the whole ledger, not something any one importer needs
  to know about.
- `postings_to_frame(...)` — turns the finished list of validated
  `Posting` models into the flat, polars-friendly shape everything else in
  this module reads.

If the source represents a fact that doesn't fit any existing canonical
field — a new kind of account, a new per-posting attribute nothing else
has needed yet — extend `accounting/models.py` once, at the source: add a
new `Literal` arm to `AccountKind`, or a new key inside a posting's
`meta` dict for anything rare or source-specific. Never invent a
parallel, source-specific schema next to the canonical one.

### 3. Register the standardizer

Add one entry to `_STANDARDIZERS` in `ingest.py`:

```python
_STANDARDIZERS: dict[tuple[str, str], Callable[[str, str], pl.DataFrame]] = {
    ...("Ally", "savings"): standardize_ally_savings,
}
```

This one line is what actually makes the Import page's warning disappear
for that institution/kind — `supported_import_kinds()` just returns this
table's keys.

### 4. Teach the Import page to recognize the file automatically (recommended)

This step is optional — a standardizer registered in step 3 can already
be selected by hand from the Import page's institution/account-kind
dropdowns. But every existing source also gets auto-detected, so the
dropdowns are pre-filled with a guess the moment a file is dropped, rather
than left for the user to fill in every time.

Detection lives in `accounting/importers/detect.py`, and is a header (and
sometimes filename) fingerprint: each known CSV shape's column header is a
distinct, literal tuple. Add the new header tuple, a small `_detect_*`
function that checks for it, and wire it into `detect_bank_account`'s
dispatch. If the account number embeds in the filename (most exports do),
a small regex extracts it the same way `_CHASE_ACCOUNT_NUMBER` /
`_SOFI_ACCOUNT_NUMBER` already do; if the file instead names its own
account in a data column rather than the filename (SoFi's newer export
does this — see `importers/sofi/csv.py`), detection needs to peek at
the first parsed data row instead, which is why `detect_bank_account`
accepts one optional row alongside the header.

### 5. Nothing else changes

No other file needs to know a new institution or account kind exists.
The Import page's account-creation form, the accounts table, the ledger
replay, categorization, budgets, and goals are all already generic over
`Account`/`Posting` — they were never written against a fixed list of
institutions to begin with.

## Testing

Write the test before the implementation: drop a real (or realistically
shaped, anonymized) sample of the new CSV into a test fixture, assert the
resulting postings' amounts, dates, and placeholder counterparties are
correct, watch it fail against a standardizer that doesn't exist yet, then
write the standardizer to make it pass.
`tests/accounting/importers/test_bank_importers.py` has one such test per
existing institution/kind pair to copy the shape of.
