from decimal import Decimal
import pytest

from accounting.importers.paystub import parse_earnings_statement_text

# A hand-constructed sample using the generic label text this module's
# regexes look for — not a real paystub. If your actual paystub uses
# different wording, adjust the patterns in `importers/paystub.py`
# to match.
SAMPLE_STATEMENT_TEXT = """
Acme Corp Earnings Statement
Pay Date: 06/30/2026
Gross Pay: $4,000.00
Total Taxes: $800.00
Net Pay: $3,200.00

Direct Deposit
Checking ending in 9579 $3,000.00
Reimbursement ending in 9579 $200.00
"""


def test_parse_earnings_statement_text_reads_the_totals() -> None:
    statement = parse_earnings_statement_text(SAMPLE_STATEMENT_TEXT)
    assert statement.gross_pay == Decimal("4000.0")
    assert statement.taxes_withheld == Decimal("800.0")
    assert statement.net_pay == Decimal("3200.0")
    assert statement.pay_date.date().isoformat() == "2026-06-30"


def test_parse_earnings_statement_text_reads_every_deposit_line() -> None:
    statement = parse_earnings_statement_text(SAMPLE_STATEMENT_TEXT)
    assert len(statement.deposits) == 2
    amounts = sorted(deposit.amount for deposit in statement.deposits)
    assert amounts == [Decimal("200.0"), Decimal("3000.0")]
    assert all(deposit.account_last4 == "9579" for deposit in statement.deposits)


def test_parse_earnings_statement_text_defaults_missing_taxes_to_zero() -> None:
    text = "Pay Date: 06/30/2026\nGross Pay: $100.00\nNet Pay: $100.00\nChecking ending in 1234 $100.00\n"
    statement = parse_earnings_statement_text(text)
    assert statement.taxes_withheld == Decimal("0.0")


def test_parse_earnings_statement_text_raises_naming_whats_missing() -> None:
    with pytest.raises(ValueError, match="gross pay"):
        parse_earnings_statement_text("Net Pay: $100.00\nPay Date: 06/30/2026\n")


def test_parse_earnings_statement_text_raises_when_no_deposit_line_found() -> None:
    with pytest.raises(ValueError, match="deposit"):
        parse_earnings_statement_text("Pay Date: 06/30/2026\nGross Pay: $100.00\nNet Pay: $100.00\n")


# The real extracted text of a Gusto-style "Earnings Statement" paystub —
# textual pay date, "( . . . XXXX)"-style deposit lines split across two
# accounts, and a reimbursements section with both real and $0.00
# (YTD-only) line items.
REAL_PAYSTUB_TEXT = (
    "Company Employee\nEarnings Statement\nEQORE Inc. Gabriel Duguey\n444 Somerville Ave XXX-XX-5036\n"
    "Somerville, MA 02143 1 Union St\nPay period: Jun 1, 2026 - Jun 15, 2026 Pay Day: Jun 15, 2026 857-320-5134 Apt 2\n"
    "SoFi HYSA ( . . . 3680): $2,000.00 Trace ID: 021000023445046 Cambridge, MA 02139\n"
    "Chase Checking Gabriel ( . . . 9579): $2,715.82 Trace ID:\n021000026065794\n"
    "Employee Gross Earnings\nDescription Rate Hours Current Year To Date\n"
    "Regular Hours | Salaried $52.88 86.666667 $4,583.33 $47,402.21\nPaid Holidays $2,115.39\nTime Off $846.15\n"
    "Sick $52.88\nTotals 86.666667 $4,583.33 $50,416.63\nEmployee Taxes Withheld Employer Tax\n"
    "Employee Tax Current Year To Date Company Tax Current Year To Date\n"
    "Federal Income Tax $815.19 $8,621.38 MA Unemployment Insurance Tax $0.00 $363.01\n"
    "MA Withholding Tax $220.00 $2,420.00 MA Workforce Training Tax $0.00 $8.41\n"
    "Massachusetts Paid Family Leave - Employee $8.25 $90.75\nMassachusetts Paid Medical Leave - Employee $12.83 $141.13\n"
    "Employee Deductions\nDescription Type Current Year To Date\nNone - $0.00 $0.00\n"
    "Employer Contributions\nDescription Type Current Year To Date\nNone - $0.00 $0.00\n"
    "Summary\nDescription Current Year To Date\nGross Earnings $4,583.33 $50,416.63\n"
    "Pre-Tax Deductions/Contributions $0.00 $0.00\nTaxes $1,056.27 $11,273.26\n"
    "Post-Tax Deductions/Contributions $0.00 $0.00\nNet Pay $3,527.06 $39,143.37\n"
    "Total Reimbursements $1,188.76 $3,369.96\nQSEHRA $400.00 $1,600.00\n"
    "Various travel expenses 2/13-3/6 $0.00 $67.20\nStanding desk purchase $0.00 $114.00\n"
    "Late-night meal reimbursement - 6/3/26 $18.76 $18.76\n"
    "STEM OPT application fees (Reimbursement #19) $770.00 $770.00\nOne time reimbursement $0.00 $800.00\n"
    "Check Amount $4,715.82 $42,513.33\nTotal Hours Worked 86.666667 936.33\n"
)


def test_parse_earnings_statement_text_reads_a_real_gusto_style_paystub() -> None:
    statement = parse_earnings_statement_text(REAL_PAYSTUB_TEXT)
    assert statement.gross_pay == Decimal("4583.33")
    assert statement.taxes_withheld == Decimal("1056.27")
    assert statement.net_pay == Decimal("3527.06")
    assert statement.pay_date.date().isoformat() == "2026-06-15"


def test_parse_earnings_statement_text_reads_deposits_split_across_two_accounts() -> None:
    statement = parse_earnings_statement_text(REAL_PAYSTUB_TEXT)
    assert len(statement.deposits) == 2
    by_last4 = {deposit.account_last4: deposit.amount for deposit in statement.deposits}
    assert by_last4["3680"] == Decimal("2000.0")
    assert by_last4["9579"] == Decimal("2715.82")
    assert sum(deposit.amount for deposit in statement.deposits) == Decimal("4715.82")


def test_parse_earnings_statement_text_excludes_zero_current_period_reimbursements() -> None:
    statement = parse_earnings_statement_text(REAL_PAYSTUB_TEXT)
    labels = {line.label for line in statement.reimbursement_lines}
    assert labels == {
        "QSEHRA",
        "Late-night meal reimbursement - 6/3/26",
        "STEM OPT application fees (Reimbursement #19)",
    }
    assert sum(line.amount for line in statement.reimbursement_lines) == Decimal("1188.76")


def test_parse_earnings_statement_text_deposits_reconcile_against_net_pay_plus_reimbursements() -> None:
    statement = parse_earnings_statement_text(REAL_PAYSTUB_TEXT)
    total_deposited = sum(deposit.amount for deposit in statement.deposits)
    total_owed = statement.net_pay + sum(line.amount for line in statement.reimbursement_lines)
    # Exact, not approximate: both sides are parsed straight off the paystub
    # as `Decimal`, so a real statement reconciles to the cent or it is wrong.
    assert total_deposited == total_owed
