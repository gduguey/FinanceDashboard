# The canonical CSV importer (fallback)

Every other importer in `accounting/importers/` (`chase/`, `sofi/`) knows
its bank's exact export shape ahead of time — an exact header, a fixed date
format, a fixed number format. `importers/canonical/` is the opposite: a
no-code fallback for any bank with no dedicated standardizer (see
`adding-accounts.md`), used from the Import page whenever the selected
institution/account-kind pair isn't in `ingest.supported_import_kinds()`.
It guesses column names and formats the same way a general-purpose
spreadsheet import would, rather than requiring an exact, known shape.

## What it looks for

Column names are matched case- and whitespace-insensitively
(`canonical.parsing.find_column`) against a small set of known aliases per
concept:

| Concept | Required? | Recognized column names |
|---|---|---|
| Date | yes | Date, Transaction Date, Posting Date, Posted Date, Trans Date |
| Description | yes | Description, Memo, Narrative, Payee, Details, Transaction Description |
| Amount | one of Amount or Debit+Credit | Amount, Transaction Amount, Value |
| Debit / Credit | | Debit/Withdrawal/Money Out/Debit Amount, Credit/Deposit/Money In/Credit Amount |
| Category | no | Category |
| Subcategory | no | Subcategory, Sub Category, Sub-Category |

If neither Amount nor a Debit/Credit pair is found — or Date or
Description is missing — the import fails with a `CanonicalCsvError`
naming exactly what's expected, shown to the user as-is.

## Date and amount parsing

- **Dates** (`canonical.parsing.parse_date_flexible`) are parsed with
  `dateutil`'s general parser, which already covers ISO (`2026-06-30`), US
  (`06/30/2026`), and named-month (`Jan 5, 2026`, `05-Jan-2026`) forms.
- **Amounts** (`canonical.parsing.parse_amount_flexible`) strip any
  currency symbol, recognize a negative amount written with a leading or
  trailing minus sign or wrapped in parentheses (the standard accounting
  convention), and disambiguate a thousands separator from a decimal one:
  both US (`1,234.56`) and European (`1.234,56`) conventions are
  supported. A single separator followed by exactly three digits with
  nothing else after it reads as a thousands grouping rather than a
  decimal, since real currency amounts essentially never carry three
  decimal digits.
- If **Debit**/**Credit** columns are used instead of a single **Amount**
  column, the resulting posting's amount is `credit − |debit|`.

## Column separator

The separator (comma, semicolon, tab, or pipe) is auto-detected
(`csv.Sniffer`, falling back to counting each candidate in the header
line). If it can't be guessed at all, `CanonicalCsvSeparatorUnknownError`
(a `CanonicalCsvError` subclass) is raised, and the Import page's retry
control lets the user pick one explicitly and resubmit with `separator`
set — no need to reformat the file.

## Category and subcategory auto-creation

A `Category`/`Subcategory` column value is matched against the store's
existing categories by name, case- and trailing-whitespace-insensitively
(so `"Groceries "` and `"Groceries"` are the same category) — a match
reuses that category's id; anything unmatched is created automatically.
A brand-new top-level category's classification (`income` vs. `expense`)
is inferred from the majority sign of the amounts filed under it in that
one import; a new subcategory always inherits its parent's classification
and color, matching `Category`'s own two-level model. The import's
response includes every category/subcategory it created, which the Import
page renders as a small taxonomy summary (the same static table
`CategoriesTab` uses for reference), linking to the Categories tab in case
anything needs renaming or merging afterward.

## Wiring

`importers.canonical.csv.standardize_canonical_csv` does the parsing and
returns a `CanonicalImportResult` (postings + newly-encountered
categories); `importers.ingest.ingest_canonical_csv` is the layer that
archives the raw file, calls it, persists the new categories into the
store, and merges the postings into the ledger — mirroring `ingest_csv`'s
archive-then-merge shape, but also touching the category store, which
`ingest_csv` never needs to. Two API endpoints cover this path:
`POST /api/accounting/import/canonical/preview` parses without persisting
anything (for previewing which categories a file would create before
committing), and `POST /api/accounting/import/canonical` actually imports.
