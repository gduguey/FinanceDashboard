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
    assert statement.gross_pay == pytest.approx(4000.0)
    assert statement.taxes_withheld == pytest.approx(800.0)
    assert statement.net_pay == pytest.approx(3200.0)
    assert statement.pay_date.date().isoformat() == "2026-06-30"


def test_parse_earnings_statement_text_reads_every_deposit_line() -> None:
    statement = parse_earnings_statement_text(SAMPLE_STATEMENT_TEXT)
    assert len(statement.deposits) == 2
    amounts = sorted(deposit.amount for deposit in statement.deposits)
    assert amounts == pytest.approx([200.0, 3000.0])
    assert all(deposit.account_last4 == "9579" for deposit in statement.deposits)


def test_parse_earnings_statement_text_defaults_missing_taxes_to_zero() -> None:
    text = "Pay Date: 06/30/2026\nGross Pay: $100.00\nNet Pay: $100.00\nChecking ending in 1234 $100.00\n"
    statement = parse_earnings_statement_text(text)
    assert statement.taxes_withheld == pytest.approx(0.0)


def test_parse_earnings_statement_text_raises_naming_whats_missing() -> None:
    with pytest.raises(ValueError, match="gross pay"):
        parse_earnings_statement_text("Net Pay: $100.00\nPay Date: 06/30/2026\n")


def test_parse_earnings_statement_text_raises_when_no_deposit_line_found() -> None:
    with pytest.raises(ValueError, match="deposit"):
        parse_earnings_statement_text("Pay Date: 06/30/2026\nGross Pay: $100.00\nNet Pay: $100.00\n")
