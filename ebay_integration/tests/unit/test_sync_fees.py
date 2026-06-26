"""
Unit tests for ebay_integration.utils.sync_fees.

Covers:
  - extract_fee_from_transaction  (the ONLY spot encoding the eBay Finances
    response shape — isolated so a field-path change is a one-line fix)
  - create_fee_journal_entry      (the accounting: debit/credit sides, cost centre)
  - process_fee_transaction       (lookup, dedup, SO field write)

frappe is injected as a mock via sys.modules in conftest.py.
"""

from unittest.mock import MagicMock, patch

from ebay_integration.utils.sync_fees import (
    extract_fee_from_transaction,
    create_fee_journal_entry,
    process_fee_transaction,
)

MODULE = "ebay_integration.utils.sync_fees"


# ---------------------------------------------------------------------------
# extract_fee_from_transaction — isolated API-shape assumption
# ---------------------------------------------------------------------------

class TestExtractFeeFromTransaction:

    def test_reads_total_fee_amount(self):
        txn = {"totalFeeAmount": {"value": "4.50", "currency": "USD"}}
        assert extract_fee_from_transaction(txn) == 4.50

    def test_missing_fee_returns_zero(self):
        assert extract_fee_from_transaction({}) == 0.0

    def test_negative_value_returned_as_positive(self):
        txn = {"totalFeeAmount": {"value": "-4.50"}}
        assert extract_fee_from_transaction(txn) == 4.50


# ---------------------------------------------------------------------------
# create_fee_journal_entry — fully verifiable accounting
# ---------------------------------------------------------------------------

class TestCreateFeeJournalEntry:

    def test_builds_balanced_expense_journal_entry(self, frappe_mock):
        frappe_mock.db.get_value = MagicMock(return_value="Main - CC")  # cost center
        captured = {}

        def _get_doc(data):
            captured["data"] = data
            return MagicMock(name="JE", insert=MagicMock(), submit=MagicMock())

        frappe_mock.get_doc = MagicMock(side_effect=_get_doc)

        with patch(f"{MODULE}.get_selling_fee_account", return_value="eBay Selling Fees - X"), \
             patch(f"{MODULE}.get_fee_clearing_account", return_value="eBay Clearing - X"):
            create_fee_journal_entry("12-99", 4.50, "Acme")

        data = captured["data"]
        assert data["doctype"] == "Journal Entry"
        assert data["company"] == "Acme"
        assert data["cheque_no"] == "EBAY-FEE-12-99"
        accounts = data["accounts"]
        assert len(accounts) == 2

        debit_row = next(a for a in accounts if a.get("debit_in_account_currency"))
        credit_row = next(a for a in accounts if a.get("credit_in_account_currency"))

        # Expense is debited; the clearing/payout account is credited
        assert debit_row["account"] == "eBay Selling Fees - X"
        assert debit_row["debit_in_account_currency"] == 4.50
        assert debit_row["cost_center"] == "Main - CC"

        assert credit_row["account"] == "eBay Clearing - X"
        assert credit_row["credit_in_account_currency"] == 4.50

    def test_submits_the_journal_entry(self, frappe_mock):
        frappe_mock.db.get_value = MagicMock(return_value="Main - CC")
        je = MagicMock(insert=MagicMock(), submit=MagicMock())
        frappe_mock.get_doc = MagicMock(return_value=je)

        with patch(f"{MODULE}.get_selling_fee_account", return_value="Exp"), \
             patch(f"{MODULE}.get_fee_clearing_account", return_value="Clear"):
            create_fee_journal_entry("12-1", 1.0, "Acme")

        je.insert.assert_called_once_with(ignore_permissions=True)
        je.submit.assert_called_once()


# ---------------------------------------------------------------------------
# process_fee_transaction
# ---------------------------------------------------------------------------

def _make_sale_txn(order_id="12-77", value="6.00", currency="USD"):
    return {
        "transactionType": "SALE",
        "orderId": order_id,
        "totalFeeAmount": {"value": value, "currency": currency},
    }


class TestProcessFeeTransaction:

    def _wire(self, frappe_mock, so_name="SO-1", company="Acme",
              company_currency="USD", duplicate=False):
        def _get_value(doctype, filters, fieldname=None, *a, **k):
            if doctype == "Sales Order" and isinstance(filters, dict):
                return so_name
            if doctype == "Sales Order" and fieldname == "company":
                return company
            if doctype == "Company":
                return company_currency
            return None
        frappe_mock.db.get_value = MagicMock(side_effect=_get_value)
        frappe_mock.db.exists = MagicMock(return_value="JE-DUP" if duplicate else None)
        frappe_mock.db.set_value = MagicMock()

    def test_creates_je_and_writes_fee_on_sales_order(self, frappe_mock):
        self._wire(frappe_mock)
        with patch(f"{MODULE}.create_fee_journal_entry", return_value="JE-1") as mock_je:
            result = process_fee_transaction(_make_sale_txn())

        assert result is True
        mock_je.assert_called_once()
        # fee amount stored on the Sales Order
        fee_calls = [
            c for c in frappe_mock.db.set_value.call_args_list
            if c.args[0] == "Sales Order"
            and isinstance(c.args[2], dict)
            and c.args[2].get("ebay_selling_fee") == 6.00
        ]
        assert len(fee_calls) == 1

    def test_skips_when_no_sales_order(self, frappe_mock):
        self._wire(frappe_mock, so_name=None)
        with patch(f"{MODULE}.create_fee_journal_entry") as mock_je:
            result = process_fee_transaction(_make_sale_txn())
        assert result is False
        mock_je.assert_not_called()

    def test_skips_when_fee_is_zero(self, frappe_mock):
        self._wire(frappe_mock)
        with patch(f"{MODULE}.create_fee_journal_entry") as mock_je:
            result = process_fee_transaction(_make_sale_txn(value="0.00"))
        assert result is False
        mock_je.assert_not_called()

    def test_skips_when_duplicate_je_exists(self, frappe_mock):
        self._wire(frappe_mock, duplicate=True)
        with patch(f"{MODULE}.create_fee_journal_entry") as mock_je:
            result = process_fee_transaction(_make_sale_txn())
        assert result is False
        mock_je.assert_not_called()

    def test_skips_on_currency_mismatch(self, frappe_mock):
        self._wire(frappe_mock, company_currency="EUR")
        with patch(f"{MODULE}.create_fee_journal_entry") as mock_je:
            result = process_fee_transaction(_make_sale_txn(currency="USD"))
        assert result is False
        mock_je.assert_not_called()
