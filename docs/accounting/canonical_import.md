# Canonical CSV/Excel Importer

The canonical importer is a **fallback parser** that imports bank transactions from any CSV or Excel file, even when no dedicated standardizer exists for that bank. It's designed to be forgiving about column names and date/amount formats while maintaining strict validation where it matters.

When a bank-specific importer (Chase, SoFi, etc.) fails, the system automatically tries the canonical importer as a fallback.

---

## Overview

**What it does:**
- Detects column names by fuzzy matching (not exact bank export formats)
- Auto-detects CSV separator (`,`, `;`, `\t`, `|`)
- Parses flexible date/amount formats
- Auto-creates categories and subcategories
- Tolerates up to 20% of rows failing to parse
- Reports exactly which rows couldn't be parsed and why

**What it accepts:**
- `.csv` files (any delimiter)
- `.xlsx` files (all sheets checked)
- Generic transaction export formats

---

## Required Columns

At minimum, your file must have:

### **Option A: Single Amount Column** (most common)
- **Date** column
- **Description** column  
- **Amount** column (negative = money out, positive = money in)

### **Option B: Separate Debit/Credit Columns**
- **Date** column
- **Description** column
- **Debit** column (money out)
- **Credit** column (money in)

### **Optional Columns**
- **Category** column — auto-creates categories by name
- **Subcategory** column — auto-creates subcategories under their parent category

---

## Column Name Matching

### **Case Sensitivity: ❌ NOT Case-Sensitive**

These are all recognized as the same column:

✓ `Date`, `date`, `DATE`, `dAte`  
✓ `Description`, `DESCRIPTION`, `description`  
✓ `Amount`, `AMOUNT`, `amount`

### **Whitespace: ✅ Whitespace-Forgiving**

These are all recognized as the same column:

✓ `Transaction Date`, `transaction date`, `TRANSACTION DATE`  
✓ `Transaction Description`, `transaction description`  

Leading/trailing spaces are ignored.

### **Exact Wording: ⚠️ MUST Match One of These Lists**

The importer looks for these exact words (case/whitespace don't matter, but the word itself must match):

#### **Date Column Aliases:**
`date`, `transaction date`, `posting date`, `posted date`, `trans date`

Example: ✓ `Posting Date` matches  
Example: ❌ `Txn Date` does **not** match (use `Trans Date` or just `Date`)

#### **Description Column Aliases:**
`description`, `memo`, `narrative`, `payee`, `details`, `transaction description`

Example: ✓ `Payee` matches  
Example: ❌ `Reference` does **not** match (use `Details` or `Description`)

#### **Amount Column Aliases:**
`amount`, `transaction amount`, `value`

Example: ✓ `Transaction Amount` matches  
Example: ❌ `Total` does **not** match (use `Amount`)

#### **Debit Column Aliases:**
`debit`, `withdrawal`, `money out`, `debit amount`

#### **Credit Column Aliases:**
`credit`, `deposit`, `money in`, `credit amount`

#### **Category Column Aliases:**
`category`

Only `category` is recognized (case-insensitive). `Type` or `Classification` do **not** match.

#### **Subcategory Column Aliases:**
`subcategory`, `sub category`, `sub-category`

All three forms are recognized (spaces and hyphens are equivalent).

---

## Date Format Flexibility

The importer auto-detects date formats. These all work:

### **Unambiguous Formats** (always work)
✓ `2026-06-30` (ISO, YYYY-MM-DD)  
✓ `2026-7-4` (ISO with single digits)  
✓ `June 30, 2026` (named month)  
✓ `Jun 30 2026` (abbreviated month)  
✓ `30-Jun-2026` (day-month-year with month name)

### **Ambiguous Numeric Formats** (depends on `date_order` setting)

When both parts could be a month (1–12), the `date_order` parameter breaks the tie:

**`date_order: "MDY"` (US default: Month-Day-Year)**
- `06/30/2026` → June 30, 2026
- `06/15` → June 15 (current year)
- `06/31` → **Rejected** (June doesn't have 31 days)

**`date_order: "DMY"` (European: Day-Month-Year)**
- `30/06/2026` → 30 June 2026
- `15/06` → 15 June (current year)
- `31/06` → **Rejected** (June doesn't have 31 days)

### **If in Doubt:**
- Use named months: `Jun 30 2026`, `30 Jun 2026` (always works)
- Use ISO format: `2026-06-30` (always works)
- Use a day > 12: `2026-06-30` (unambiguous)

---

## Amount Format Flexibility

The importer auto-detects amount formats. These all work:

### **Currency Symbols** (stripped and ignored)
✓ `1,234.56` (with currency symbol: `$1,234.56`)  
✓ `-1234.56` (negative = money out)  
✓ `(1234.56)` (parentheses = negative)

### **Thousands Separators** (auto-detected)

**US Format:**
✓ `1,234.56` (comma for thousands, period for decimal)  
✓ `1234.56` (no thousands separator)

**European Format:**
✓ `1.234,56` (period for thousands, comma for decimal)

**Plain Numbers:**
✓ `1234.56` (detected as US)  
✓ `1234,56` (detected as European)

### **If in Doubt:**
- Use plain decimals: `1234.56` (always works)
- Avoid mixing formats in the same file

---

## Row Parsing Tolerance

### **Acceptance Threshold: 80% Minimum**

If more than **20% of rows fail to parse**, the whole file is rejected with details about what went wrong.

**Examples:**

✓ **Accepts:**
- 100 rows: 2 fail (2% < 20%)
- 50 rows: 9 fail (18% < 20%)
- 100 rows: 20 fail (20% exactly, at boundary)

❌ **Rejects:**
- 100 rows: 21 fail (21% > 20%)
- 50 rows: 11 fail (22% > 20%)

### **Why Rows Fail**

Rows are skipped (not rejected) if:
- Date column has an unrecognized format
- Amount column has an unparseable number

**Error Response Example:**
```
Couldn't parse most of this file's rows — 2 row(s) had an unrecognized 
date and 1 row(s) had an unrecognized amount, out of 50 data row(s).
```

---

## Skip Tracking & Error Reporting

When rows can't be parsed (and stay under the 20% threshold), they're tracked and reported.

### **API Response Includes:**

```json
{
  "account_id": "Chase-Checking-1234",
  "new_posting_count": 48,
  "total_posting_count": 150,
  "skipped_rows": {
    "total_rows": 50,
    "skipped_count": 2,
    "bad_dates": 1,
    "bad_amounts": 1,
    "skipped_row_numbers": [15, 42]
  }
}
```

### **UI Display Example:**

```
✅ Import successful (48 new transactions)

⚠️ 2 rows skipped:
  • Row 15: Unrecognized date format
  • Row 42: Invalid amount format

Imported to Chase Checking (1234)
```

Users can then fix those specific rows and re-import.

---

## Excel Multi-Sheet Handling

For `.xlsx` files:

1. **All sheets are checked** in order (as they appear in the workbook)
2. **First sheet with valid columns is used** — remaining sheets are skipped silently
3. **If no sheet matches** → Error lists all sheet names found

### **Valid Columns Check:**

Each sheet's header row (row 1) is checked for the required columns. The header can be anywhere in the row (leftmost or middle), but must be in **row 1**.

**Example:** ✓ Works
```
Sheet 1: [Empty, Empty, Date, Description, Amount]  ← Uses columns C, D, E
Sheet 2: Not checked (Sheet 1 matched)
```

**Example:** ✓ Works (a non-matching sheet is skipped, not fatal)
```
Sheet 1: [Empty, Empty, Empty]  ← No columns found, skipped
Sheet 2: [Date, Description, Amount]  ← Used instead
```

**Example:** ❌ Fails
```
Sheet 1: [Empty, Empty, Empty]  ← No columns found
Sheet 2: [Foo, Bar, Baz]        ← No columns found either
```

---

## Category & Subcategory Auto-Creation

### **Matching: Case & Whitespace Insensitive**

If your file has a **Category** or **Subcategory** column, values are auto-matched to existing categories:

- `grocery`, `GROCERY`, `Grocery` all match category `Grocery`
- Trailing whitespace is ignored: `Grocery ` matches `Grocery`

### **New Categories: Created Automatically**

If a category name in your file doesn't match any existing category, it's created automatically:

✓ File has `Dining Out` → Creates new category (if not already there)  
✓ File has `Grocery` → Uses existing category if found, creates if not

### **Subcategories: Grouped by Parent**

Subcategories are created under their parent category. Two rows with the same subcategory name under **different** parent categories create **two separate** subcategories:

Example:
- Row 1: Category=`Dining`, Subcategory=`Lunch` → Creates `Dining > Lunch`
- Row 2: Category=`Travel`, Subcategory=`Lunch` → Creates **separate** `Travel > Lunch` (not merged)

### **Category Overrides** (Preview Step)

Before importing, you can preview what categories would be created and optionally **rename or merge** them:

```json
{
  "categories": {
    "Old Name": "New Name",
    "Red Lobster": "Restaurants"
  },
  "subcategories": {
    "Dining": {
      "Fast Casual": "Quick Service"
    }
  }
}
```

---

## CSV Separator Auto-Detection

The importer tries to guess your file's delimiter:

**Candidates checked (in order):**
1. Comma: `,`
2. Semicolon: `;`
3. Tab: `\t`
4. Pipe: `|`

### **How it Works:**

A sample of the first 8KB is checked for which delimiter appears most frequently and consistently in the header row.

### **If Auto-Detection Fails:**

Error response:
```
Could not auto-detect the file's column separator. 
Please specify one: comma, semicolon, tab, or pipe.
```

User can then retry with `separator` parameter set explicitly.

---

## Encoding Handling

The importer tries multiple encodings in order (most common first):

1. `utf-8-sig` (UTF-8 with BOM, common from Excel)
2. `utf-8` (plain UTF-8)
3. `cp1252` (Windows/Excel on some systems)
4. `iso-8859-1` (Mac/European systems)

If all fail, the file is rejected with an encoding error.

---

## CSV vs Excel Files

Both are supported, dispatched automatically by file extension:

### **.csv files:**
- Separator auto-detected (or use `separator` parameter)
- All content treated as text (no numeric/date inference)
- Any encoding (fallback chain used)

### **.xlsx files:**
- All sheets checked for valid columns
- First matching sheet is used
- All cells read as text (no Excel numeric/date formatting applied)
- UTF-8 (Excel standard)

---

## Common Issues & Fixes

### **❌ Column Not Found**

Error:
```
Expected a Date column, a Description column, and either an Amount 
column or separate Debit and Credit columns.
```

**Fix:**
- Rename your column to match the aliases list above
- Ensure the column name is spelled exactly (case doesn't matter)
- Check for extra spaces in the column name

**Example:**
- ❌ `Txn Date` → ✓ Change to `Date` or `Trans Date`
- ❌ `Reference` → ✓ Change to `Description` or `Details`
- ❌ `Total ` (trailing space) → ✓ Remove the space

### **❌ Date Format Not Recognized**

Error:
```
Couldn't parse most of this file's rows — 15 row(s) had an 
unrecognized date...
```

**Fix:**
- Use ISO format: `2026-06-30`
- Use named months: `Jun 30 2026`
- Use a day > 12 to disambiguate: `30/06/2026` (clearly day/month)
- Ensure consistency across the file
- Set `date_order` parameter if needed (MDY vs DMY)

**Example:**
- ❌ `6/7/26` (ambiguous) → ✓ `2026-06-07` (ISO)
- ❌ Mixed `6/7` and `June 7` → ✓ Use consistent format

### **❌ Amount Format Not Recognized**

Error:
```
Couldn't parse most of this file's rows — ... row(s) had an 
unrecognized amount...
```

**Fix:**
- Use simple decimals: `1234.56`
- Remove extra characters (ensure amounts are numeric)
- Avoid mixing US and European formats in the same file

**Example:**
- ❌ `1.234,56 EUR` → ✓ Just `1234.56` or `1234,56`
- ❌ Column mix of `$1,234.56` and `1.234,56` → ✓ Standardize

### **❌ More Than 20% of Rows Skipped**

Error:
```
Couldn't parse most of this file's rows...
```

**Fix:**
- Check which row numbers failed (listed in skip info)
- Fix date/amount formats in those rows
- Re-upload the corrected file

---

## Fallback Behavior

When a **bank-specific importer fails** (e.g., Chase checking CSV with wrong column names), the system automatically:

1. ❌ Tries the bank-specific standardizer
2. ✅ Falls back to canonical importer automatically
3. Reports any rows that couldn't be parsed
4. Returns success if >80% of rows parse

**No user action required** — the fallback is automatic.

---

## API Endpoints

### **Auto-Detect Bank + Import**
`POST /api/accounting/import`

Auto-detects bank type from filename/headers, tries bank-specific importer first, falls back to canonical if needed.

**Parameters:**
- `file` (multipart)
- `institution` (bank name)
- `account_kind` (checking, savings, credit_card)
- `account_id`

**Response:**
```json
{
  "account_id": "...",
  "new_posting_count": 42,
  "total_posting_count": 100,
  "skipped_rows": { ... }  // Optional, only if rows were skipped
}
```

### **Canonical Import (Explicit)**
`POST /api/accounting/import/canonical`

Explicitly use the canonical importer (skip bank detection).

**Parameters:**
- `file` (multipart)
- `separator` (optional: `,`, `;`, `\t`, `|`)
- `date_order` (optional: `MDY` or `DMY`, default `MDY`)
- `category_overrides` (optional: JSON `CanonicalCategoryOverridesRequest`)

**Response:** Same as auto-detect endpoint.

### **Canonical Preview**
`POST /api/accounting/import/canonical/preview`

Preview what categories would be created without committing to import.

**Parameters:** Same as canonical import (minus `category_overrides`)

**Response:**
```json
{
  "new_categories": [
    {
      "category_id": "expense:groceries",
      "name": "Groceries",
      "classification": "expense",
      "color": "#FF6B6B"
    },
    ...
  ]
}
```

---

## Summary Table

| Feature | Behavior |
|---------|----------|
| **Column Name Matching** | Case-insensitive, whitespace-forgiving, exact word-match required |
| **Date Formats** | Auto-detected, multiple formats supported, ambiguous dates use `date_order` |
| **Amount Formats** | Auto-detected, US & European separators, currency symbols stripped |
| **Row Tolerance** | 80% minimum parse success (up to 20% can fail) |
| **Skip Reporting** | Yes, lists exact row numbers and reasons |
| **CSV Separator** | Auto-detected or explicit |
| **Excel Sheets** | All checked, first match used |
| **Excel Header Location** | Must be in row 1 (first row of sheet) |
| **Categories** | Auto-created, case-insensitive matching |
| **Subcategories** | Auto-created, grouped by parent category |
| **Encoding** | Fallback chain: utf-8-sig, utf-8, cp1252, iso-8859-1 |
| **Fallback Trigger** | When bank-specific importer fails (automatic) |
