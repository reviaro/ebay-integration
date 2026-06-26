"""
Unit tests for ebay_integration.utils.sync_cancellations.

Covers the four pure/near-pure functions:
  - determine_refund_type
  - map_cancellation_state
  - should_create_return_dn
  - process_cancellation

frappe is injected as a mock via sys.modules in conftest.py.
The ``frappe_mock`` fixture resets DB state between tests.
"""

import pytest
from unittest.mock import MagicMock, call

from ebay_integration.utils.sync_cancellations import (
    determine_refund_type,
    map_cancellation_state,
    should_create_return_dn,
    process_cancellation,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_order_data(order_id="12-34567-89012", cancel_state="CANCELED",
                     cancel_reason="", total_value="58.50",
                     cancel_requests=None):
    """Build a minimal eBay order payload for process_cancellation tests."""
    requests = cancel_requests if cancel_requests is not None else [
        {
            "cancelReason": cancel_reason,
            "cancelCompletedDate": "2026-04-20T10:00:00.000Z",
        }
    ]
    return {
        "orderId": order_id,
        "cancelStatus": {
            "cancelState": cancel_state,
            "cancelRequests": requests,
        },
        "pricingSummary": {
            "total": {"value": total_value},
        },
    }


# ---------------------------------------------------------------------------
# 1. determine_refund_type
# ---------------------------------------------------------------------------

class TestDetermineRefundType:
    """Pure function — no frappe interaction required."""

    # --- keyword routing ---

    def test_return_keyword(self):
        assert determine_refund_type("Item return requested", {}) == "Return Refund"

    def test_shipping_keyword(self):
        assert determine_refund_type("shipping cost adjustment", {}) == "Shipping Refund"

    def test_goodwill_keyword(self):
        assert determine_refund_type("goodwill gesture", {}) == "Goodwill Refund"

    def test_courtesy_keyword(self):
        assert determine_refund_type("courtesy adjustment", {}) == "Goodwill Refund"

    def test_cancel_keyword(self):
        assert determine_refund_type("order cancel requested", {}) == "Cancellation Refund"

    def test_partial_keyword(self):
        assert determine_refund_type("partial refund approved", {}) == "Partial Refund"

    def test_unknown_memo_returns_full_refund(self):
        assert determine_refund_type("something unrecognised", {}) == "Full Refund"

    def test_empty_string_returns_full_refund(self):
        assert determine_refund_type("", {}) == "Full Refund"

    def test_none_memo_returns_full_refund(self):
        assert determine_refund_type(None, {}) == "Full Refund"

    # --- case-insensitivity ---

    def test_return_uppercase(self):
        assert determine_refund_type("RETURN", {}) == "Return Refund"

    def test_shipping_mixed_case(self):
        assert determine_refund_type("Shipping Overcharge", {}) == "Shipping Refund"

    def test_goodwill_mixed_case(self):
        assert determine_refund_type("GOODWILL", {}) == "Goodwill Refund"

    def test_courtesy_uppercase(self):
        assert determine_refund_type("COURTESY", {}) == "Goodwill Refund"

    def test_cancel_uppercase(self):
        assert determine_refund_type("CANCEL", {}) == "Cancellation Refund"

    def test_partial_uppercase(self):
        assert determine_refund_type("PARTIAL", {}) == "Partial Refund"

    # --- keyword priority: "return" wins over later keywords ---

    def test_return_takes_priority_over_shipping(self):
        # "return shipping" contains both keywords; "return" appears first in the if-chain
        assert determine_refund_type("return shipping label", {}) == "Return Refund"

    def test_transaction_arg_is_ignored_for_routing(self):
        # transaction dict is currently unused but must not raise
        result = determine_refund_type("", {"transactionType": "REFUND"})
        assert result == "Full Refund"


# ---------------------------------------------------------------------------
# 2. map_cancellation_state
# ---------------------------------------------------------------------------

class TestMapCancellationState:
    """Pure mapping function — no frappe interaction required."""

    def test_empty_string(self):
        assert map_cancellation_state("") == "None Requested"

    def test_none_requested(self):
        assert map_cancellation_state("NONE_REQUESTED") == "None Requested"

    def test_in_progress(self):
        assert map_cancellation_state("IN_PROGRESS") == "In Progress"

    def test_canceled(self):
        assert map_cancellation_state("CANCELED") == "Canceled"

    def test_closed(self):
        assert map_cancellation_state("CLOSED") == "Canceled"

    def test_unknown_state_returns_none_requested(self):
        assert map_cancellation_state("SOME_FUTURE_STATE") == "None Requested"

    def test_lowercase_unknown_returns_none_requested(self):
        # The mapping keys are uppercase; lowercase should fall through to default
        assert map_cancellation_state("canceled") == "None Requested"

    def test_none_input_returns_none_requested(self):
        # dict.get(None) hits the default branch
        assert map_cancellation_state(None) == "None Requested"


# ---------------------------------------------------------------------------
# 3. should_create_return_dn
# ---------------------------------------------------------------------------

class TestShouldCreateReturnDn:
    """Needs frappe.db.exists to be mocked."""

    def test_return_refund_with_dn_exists(self, frappe_mock):
        frappe_mock.db.exists.return_value = "DN-0001"
        assert should_create_return_dn("SO-001", "Return Refund") is True
        frappe_mock.db.exists.assert_called_once_with(
            "Delivery Note Item",
            {"against_sales_order": "SO-001", "docstatus": 1},
        )

    def test_full_refund_with_dn_exists(self, frappe_mock):
        frappe_mock.db.exists.return_value = "DN-0002"
        assert should_create_return_dn("SO-002", "Full Refund") is True

    def test_cancellation_refund_with_dn_exists(self, frappe_mock):
        frappe_mock.db.exists.return_value = "DN-0003"
        assert should_create_return_dn("SO-003", "Cancellation Refund") is True

    def test_return_refund_no_dn(self, frappe_mock):
        frappe_mock.db.exists.return_value = None
        assert should_create_return_dn("SO-001", "Return Refund") is False

    def test_full_refund_no_dn(self, frappe_mock):
        frappe_mock.db.exists.return_value = None
        assert should_create_return_dn("SO-001", "Full Refund") is False

    def test_shipping_refund_returns_false_without_db_call(self, frappe_mock):
        # Short-circuits before touching frappe.db.exists
        result = should_create_return_dn("SO-001", "Shipping Refund")
        assert result is False
        frappe_mock.db.exists.assert_not_called()

    def test_goodwill_refund_returns_false_without_db_call(self, frappe_mock):
        result = should_create_return_dn("SO-001", "Goodwill Refund")
        assert result is False
        frappe_mock.db.exists.assert_not_called()

    def test_partial_refund_returns_false_without_db_call(self, frappe_mock):
        result = should_create_return_dn("SO-001", "Partial Refund")
        assert result is False
        frappe_mock.db.exists.assert_not_called()


# ---------------------------------------------------------------------------
# 4. process_cancellation
# ---------------------------------------------------------------------------

class TestProcessCancellation:
    """Integration of process_cancellation with the frappe mock."""

    # --- guard: missing orderId ---

    def test_returns_false_when_order_id_missing(self, frappe_mock):
        result = process_cancellation({}, auto_process=False)
        assert result is False
        frappe_mock.db.get_value.assert_not_called()

    def test_returns_false_when_order_id_is_none(self, frappe_mock):
        result = process_cancellation({"orderId": None}, auto_process=False)
        assert result is False

    # --- guard: non-terminal cancelState ---

    def test_returns_false_and_updates_so_for_in_progress_state(self, frappe_mock):
        """cancelState == IN_PROGRESS: update SO status but return False."""
        frappe_mock.db.get_value.return_value = "SO-0001"
        order_data = _make_order_data(cancel_state="IN_PROGRESS")

        result = process_cancellation(order_data, auto_process=False)

        assert result is False
        frappe_mock.db.set_value.assert_called_once()
        # Confirm the mapped status "In Progress" is written
        set_value_kwargs = frappe_mock.db.set_value.call_args
        status_dict = set_value_kwargs[0][2]  # positional arg index 2 is the value dict
        assert status_dict.get("ebay_cancellation_status") == "In Progress"

    def test_returns_false_and_does_not_set_value_when_no_so_for_in_progress(self, frappe_mock):
        """Non-terminal state with no matching SO: nothing written, returns False."""
        frappe_mock.db.get_value.return_value = None
        order_data = _make_order_data(cancel_state="IN_PROGRESS")

        result = process_cancellation(order_data, auto_process=False)

        assert result is False
        frappe_mock.db.set_value.assert_not_called()

    def test_returns_false_for_none_requested_state(self, frappe_mock):
        frappe_mock.db.get_value.return_value = None
        order_data = _make_order_data(cancel_state="NONE_REQUESTED")
        assert process_cancellation(order_data) is False

    # --- guard: no matching Sales Order for terminal state ---

    def test_returns_false_when_no_sales_order_found(self, frappe_mock):
        """cancelState == CANCELED but no SO exists: log warning, return False.

        log_sync_result internally calls frappe.get_doc for eBay Log entries, so
        we cannot assert get_doc was never called.  What matters is that no eBay
        Refund document is created.
        """
        frappe_mock.db.get_value.return_value = None
        frappe_mock.get_doc.reset_mock()
        order_data = _make_order_data(cancel_state="CANCELED")

        result = process_cancellation(order_data, auto_process=False)

        assert result is False
        # No frappe.get_doc call should carry an "eBay Refund" dict
        refund_doc_calls = [
            c for c in frappe_mock.get_doc.call_args_list
            if c.args and isinstance(c.args[0], dict)
            and c.args[0].get("doctype") == "eBay Refund"
        ]
        assert refund_doc_calls == [], "process_cancellation must not create an eBay Refund when no SO exists"

    def test_returns_false_when_no_sales_order_for_closed_state(self, frappe_mock):
        frappe_mock.db.get_value.return_value = None
        order_data = _make_order_data(cancel_state="CLOSED")
        assert process_cancellation(order_data, auto_process=False) is False

    # --- happy path: creates a new eBay Refund record ---

    def test_creates_new_refund_record_when_none_exists(self, frappe_mock):
        """Full happy path: SO found, no existing refund → new eBay Refund inserted."""
        frappe_mock.db.get_value.return_value = "SO-9999"
        frappe_mock.db.exists.return_value = None  # no existing refund

        order_data = _make_order_data(
            order_id="12-99999-00001",
            cancel_state="CANCELED",
            cancel_reason="Buyer requested",
            total_value="45.00",
        )

        # Capture the doc that gets passed to frappe.get_doc({...})
        inserted_doc = MagicMock(
            insert=MagicMock(),
            save=MagicMock(),
            refund_status="Pending",
            name="eBay-Refund-0001",
        )
        frappe_mock.get_doc.return_value = inserted_doc

        result = process_cancellation(order_data, auto_process=False)

        assert result is True

        # frappe.get_doc must have been called with a dict for the new refund
        doc_calls = [
            c for c in frappe_mock.get_doc.call_args_list
            if c.args and isinstance(c.args[0], dict)
            and c.args[0].get("doctype") == "eBay Refund"
        ]
        assert len(doc_calls) == 1, "Expected exactly one frappe.get_doc call for eBay Refund dict"

        refund_dict = doc_calls[0].args[0]
        assert refund_dict["ebay_order_id"] == "12-99999-00001"
        assert refund_dict["sales_order"] == "SO-9999"
        assert refund_dict["refund_type"] == "Cancellation Refund"
        assert refund_dict["refund_amount"] == 45.0
        assert refund_dict["refund_reason"] == "Buyer requested"
        assert refund_dict["refund_status"] == "Pending"

        inserted_doc.insert.assert_called_once_with(ignore_permissions=True)

    def test_creates_new_refund_for_closed_state(self, frappe_mock):
        """CLOSED is also a terminal state and should create a refund."""
        frappe_mock.db.get_value.return_value = "SO-8888"
        frappe_mock.db.exists.return_value = None

        order_data = _make_order_data(cancel_state="CLOSED", total_value="0.00")

        inserted_doc = MagicMock(
            insert=MagicMock(),
            save=MagicMock(),
            refund_status="Pending",
        )
        frappe_mock.get_doc.return_value = inserted_doc

        result = process_cancellation(order_data, auto_process=False)

        assert result is True
        inserted_doc.insert.assert_called_once_with(ignore_permissions=True)

    def test_updates_so_cancellation_status_on_success(self, frappe_mock):
        """After creating the refund the SO status must be set to 'Canceled'."""
        frappe_mock.db.get_value.return_value = "SO-7777"
        frappe_mock.db.exists.return_value = None

        order_data = _make_order_data(cancel_state="CANCELED")
        inserted_doc = MagicMock(
            insert=MagicMock(),
            save=MagicMock(),
            refund_status="Pending",
        )
        frappe_mock.get_doc.return_value = inserted_doc

        process_cancellation(order_data, auto_process=False)

        # At least one set_value call should target the SO with "Canceled"
        set_value_calls = frappe_mock.db.set_value.call_args_list
        so_status_calls = [
            c for c in set_value_calls
            if c.args[0] == "Sales Order"
            and c.args[1] == "SO-7777"
            and c.args[2].get("ebay_cancellation_status") == "Canceled"
        ]
        assert len(so_status_calls) == 1

    def test_updates_existing_refund_instead_of_creating(self, frappe_mock):
        """When an eBay Refund for the same order already exists, save (not insert)."""
        frappe_mock.db.get_value.return_value = "SO-6666"
        frappe_mock.db.exists.return_value = "eBay-Refund-0002"  # existing

        existing_doc = MagicMock(
            insert=MagicMock(),
            save=MagicMock(),
            refund_status="Pending",
        )
        frappe_mock.get_doc.return_value = existing_doc

        order_data = _make_order_data(cancel_state="CANCELED")
        result = process_cancellation(order_data, auto_process=False)

        assert result is True
        existing_doc.save.assert_called_once_with(ignore_permissions=True)
        existing_doc.insert.assert_not_called()

    def test_zero_amount_when_pricing_summary_missing(self, frappe_mock):
        """No pricingSummary → refund_amount defaults to 0."""
        frappe_mock.db.get_value.return_value = "SO-5555"
        frappe_mock.db.exists.return_value = None
        # Reset call history so stale calls from previous tests don't pollute the search
        frappe_mock.get_doc.reset_mock()

        order_data = {
            "orderId": "12-55555-00001",
            "cancelStatus": {
                "cancelState": "CANCELED",
                "cancelRequests": [],
            },
            # pricingSummary intentionally absent
        }

        inserted_doc = MagicMock(
            insert=MagicMock(),
            save=MagicMock(),
            refund_status="Pending",
        )
        frappe_mock.get_doc.return_value = inserted_doc

        result = process_cancellation(order_data, auto_process=False)

        assert result is True
        doc_call = next(
            c for c in frappe_mock.get_doc.call_args_list
            if c.args and isinstance(c.args[0], dict)
            and c.args[0].get("doctype") == "eBay Refund"
        )
        assert doc_call.args[0]["refund_amount"] == 0.0

    def test_db_commit_called_on_success(self, frappe_mock):
        """frappe.db.commit must be called after a successful cancellation."""
        frappe_mock.db.get_value.return_value = "SO-4444"
        frappe_mock.db.exists.return_value = None

        order_data = _make_order_data(cancel_state="CANCELED")
        inserted_doc = MagicMock(
            insert=MagicMock(),
            save=MagicMock(),
            refund_status="Pending",
        )
        frappe_mock.get_doc.return_value = inserted_doc

        process_cancellation(order_data, auto_process=False)

        frappe_mock.db.commit.assert_called()
