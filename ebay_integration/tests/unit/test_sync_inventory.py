"""
Unit tests for ebay_integration.utils.sync_inventory.

Covers:
  - _get_valuation_rate: priority chain (bin → item → standard → 0.01 fallback)
  - _create_item_from_sku: doc creation, insert/commit, graceful error handling
  - sync_inventory: early-exit guards, reconciliation building, Stock Reconciliation
    creation/submission, "no changes" branch, and logging counts

frappe is injected as a mock via sys.modules in conftest.py.
The ``frappe_mock`` fixture resets DB state between tests.

NOTE on patch target: eBayWrapper is imported *inside* sync_inventory() with
    ``from ebay_integration.utils.ebay_api import eBayWrapper``
Patching ``ebay_integration.utils.sync_inventory.eBayWrapper`` has no effect
because the attribute does not exist on the sync_inventory module at patch time.
The correct target is ``ebay_integration.utils.ebay_api.eBayWrapper`` — that is
the actual module attribute the deferred ``from … import`` reads.
"""

import types
import pytest
from unittest.mock import MagicMock, patch, call

from ebay_integration.utils.sync_inventory import (
    _get_valuation_rate,
    _create_item_from_sku,
    _update_item_details,
    sync_inventory,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_bin_row(item_code, actual_qty):
    """Return a SimpleNamespace that supports attribute-style access (as_dict=True rows)."""
    return types.SimpleNamespace(item_code=item_code, actual_qty=actual_qty)


def _make_ebay_item(sku, quantity):
    """Return a minimal eBay inventory item dict as returned by get_my_selling()."""
    return {
        "sku": sku,
        "availability": {
            "shipToLocationAvailability": {"quantity": quantity}
        }
    }


def _make_ebay_wrapper_patch(items):
    """
    Context-manager factory: patches eBayWrapper at its source module so the
    deferred ``from ebay_integration.utils.ebay_api import eBayWrapper`` in
    sync_inventory() resolves to our mock.
    """
    mock_instance = MagicMock()
    mock_instance.get_my_selling.return_value = items
    return patch("ebay_integration.utils.ebay_api.eBayWrapper", return_value=mock_instance)


def _configure_settings(frappe_mock, sync_enabled=1, warehouse="eBay Warehouse",
                        company="Test Co"):
    """Wire frappe.db.get_single_value to return sensible eBay Settings values."""
    def _gsv(doctype, field):
        if field == "sync_enabled":
            return sync_enabled
        if field == "default_warehouse":
            return warehouse
        if field == "default_company":
            return company
        return None

    frappe_mock.db.get_single_value = MagicMock(side_effect=_gsv)


# ---------------------------------------------------------------------------
# _get_valuation_rate
# ---------------------------------------------------------------------------

class TestGetValuationRate:

    def test_returns_bin_rate_when_positive(self, frappe_mock):
        """Bin valuation_rate > 0 is the highest-priority source; item/standard rates
        must be ignored when the bin already has a meaningful rate."""
        def _get_value(doctype, filters_or_field, field=None):
            if doctype == "Bin":
                return 25.50
            # Should never be reached
            return 99.0

        frappe_mock.db.get_value = MagicMock(side_effect=_get_value)
        result = _get_valuation_rate("PART-001", "eBay Warehouse")

        assert result == 25.50
        # Only the Bin lookup should have fired
        frappe_mock.db.get_value.assert_called_once()
        first_call_args = frappe_mock.db.get_value.call_args[0]
        assert first_call_args[0] == "Bin"

    def test_skips_bin_zero_returns_item_valuation_rate(self, frappe_mock):
        """When the bin rate is 0, fall through to the item master valuation_rate."""
        call_log = []

        def _get_value(doctype, filters_or_field, field=None):
            call_log.append((doctype, field or filters_or_field))
            if doctype == "Bin":
                return 0
            if doctype == "Item" and (field == "valuation_rate"):
                return 12.00
            return 0

        frappe_mock.db.get_value = MagicMock(side_effect=_get_value)
        result = _get_valuation_rate("PART-001", "eBay Warehouse")

        assert result == 12.00
        assert frappe_mock.log_error.call_count == 0

    def test_skips_bin_none_returns_item_valuation_rate(self, frappe_mock):
        """None bin rate is treated the same as 0 — skip to item master."""
        def _get_value(doctype, filters_or_field, field=None):
            if doctype == "Bin":
                return None
            if doctype == "Item" and field == "valuation_rate":
                return 8.75
            return None

        frappe_mock.db.get_value = MagicMock(side_effect=_get_value)
        result = _get_valuation_rate("PART-001", "eBay Warehouse")

        assert result == 8.75

    def test_skips_bin_and_item_rate_returns_standard_rate(self, frappe_mock):
        """When bin and item valuation_rate are both 0/None, use standard_rate."""
        def _get_value(doctype, filters_or_field, field=None):
            if doctype == "Bin":
                return None
            if doctype == "Item" and field == "valuation_rate":
                return 0
            if doctype == "Item" and field == "standard_rate":
                return 50.00
            return None

        frappe_mock.db.get_value = MagicMock(side_effect=_get_value)
        result = _get_valuation_rate("PART-001", "eBay Warehouse")

        assert result == 50.00
        assert frappe_mock.log_error.call_count == 0

    def test_falls_back_to_0_01_and_logs_error_when_all_zero(self, frappe_mock):
        """Last resort: all rates are 0/None — return 0.01 and call frappe.log_error."""
        frappe_mock.db.get_value = MagicMock(return_value=None)

        result = _get_valuation_rate("PART-NOVALUE", "eBay Warehouse")

        assert result == 0.01
        frappe_mock.log_error.assert_called_once()
        # Verify the error title identifies the problem
        call_kwargs = frappe_mock.log_error.call_args[1]
        assert "Valuation" in call_kwargs.get("title", "")

    def test_falls_back_to_0_01_when_standard_rate_is_zero(self, frappe_mock):
        """Explicit 0 standard_rate also triggers the last-resort fallback."""
        frappe_mock.log_error.reset_mock()

        def _get_value(doctype, filters_or_field, field=None):
            if doctype == "Item" and field == "standard_rate":
                return 0
            return None

        frappe_mock.db.get_value = MagicMock(side_effect=_get_value)
        result = _get_valuation_rate("PART-NOVALUE", "eBay Warehouse")

        assert result == 0.01
        assert frappe_mock.log_error.call_count == 1

    def test_bin_rate_used_even_when_item_rate_also_set(self, frappe_mock):
        """Ensure bin rate wins when both bin and item rates are positive."""
        def _get_value(doctype, filters_or_field, field=None):
            if doctype == "Bin":
                return 30.0
            return 10.0  # would be item or standard rate

        frappe_mock.db.get_value = MagicMock(side_effect=_get_value)
        result = _get_valuation_rate("PART-001", "eBay Warehouse")

        assert result == 30.0


# ---------------------------------------------------------------------------
# _create_item_from_sku
# ---------------------------------------------------------------------------

class TestCreateItemFromSku:

    def test_calls_get_doc_with_correct_fields(self, frappe_mock):
        """frappe.get_doc must be called with the expected item fields."""
        mock_doc = MagicMock()
        frappe_mock.get_doc = MagicMock(return_value=mock_doc)

        _create_item_from_sku("NEW-SKU-42")

        frappe_mock.get_doc.assert_called_once()
        doc_data = frappe_mock.get_doc.call_args[0][0]
        assert doc_data["doctype"] == "Item"
        assert doc_data["item_code"] == "NEW-SKU-42"
        assert doc_data["item_name"] == "NEW-SKU-42"
        assert doc_data["item_group"] == "All Item Groups"
        assert doc_data["stock_uom"] == "Nos"
        assert doc_data["is_stock_item"] == 1
        assert "NEW-SKU-42" in doc_data["description"]

    def test_calls_insert_and_db_commit(self, frappe_mock):
        """The new doc must be inserted and the transaction committed."""
        mock_doc = MagicMock()
        frappe_mock.get_doc = MagicMock(return_value=mock_doc)

        _create_item_from_sku("NEW-SKU-42")

        mock_doc.insert.assert_called_once()
        frappe_mock.db.commit.assert_called()

    def test_does_not_raise_when_get_doc_raises(self, frappe_mock):
        """An exception inside get_doc must be swallowed and logged, not propagated."""
        frappe_mock.log_error.reset_mock()
        frappe_mock.get_doc = MagicMock(side_effect=Exception("DB error"))

        # Should not raise
        _create_item_from_sku("BAD-SKU")

        frappe_mock.log_error.assert_called_once()
        error_msg = frappe_mock.log_error.call_args[1].get("message", "")
        assert "BAD-SKU" in error_msg

    def test_does_not_raise_when_insert_raises(self, frappe_mock):
        """An exception during insert must also be caught and logged."""
        frappe_mock.log_error.reset_mock()
        mock_doc = MagicMock()
        mock_doc.insert.side_effect = Exception("Duplicate entry")
        frappe_mock.get_doc = MagicMock(return_value=mock_doc)

        _create_item_from_sku("DUP-SKU")

        frappe_mock.log_error.assert_called_once()
        error_msg = frappe_mock.log_error.call_args[1].get("message", "")
        assert "DUP-SKU" in error_msg

    def test_uses_ebay_title_as_item_name(self, frappe_mock):
        """When eBay provides a product title, it becomes the item_name."""
        mock_doc = MagicMock()
        frappe_mock.get_doc = MagicMock(return_value=mock_doc)

        _create_item_from_sku("SKU-9", title="Genuine OEM Brake Pad", description="Front set")

        doc_data = frappe_mock.get_doc.call_args[0][0]
        assert doc_data["item_name"] == "Genuine OEM Brake Pad"
        assert doc_data["description"] == "Front set"

    def test_falls_back_to_sku_when_no_title(self, frappe_mock):
        """Without an eBay title the item_name stays the SKU (backwards compatible)."""
        mock_doc = MagicMock()
        frappe_mock.get_doc = MagicMock(return_value=mock_doc)

        _create_item_from_sku("SKU-10")

        doc_data = frappe_mock.get_doc.call_args[0][0]
        assert doc_data["item_name"] == "SKU-10"


# ---------------------------------------------------------------------------
# _update_item_details
# ---------------------------------------------------------------------------

class TestUpdateItemDetails:

    def test_updates_name_and_description_on_existing_item(self, frappe_mock):
        frappe_mock.db.set_value = MagicMock()

        _update_item_details("SKU-1", "New Title", "New Description")

        frappe_mock.db.set_value.assert_called_once()
        args = frappe_mock.db.set_value.call_args[0]
        assert args[0] == "Item"
        assert args[1] == "SKU-1"
        assert args[2]["item_name"] == "New Title"
        assert args[2]["description"] == "New Description"

    def test_updates_only_title_when_description_missing(self, frappe_mock):
        frappe_mock.db.set_value = MagicMock()

        _update_item_details("SKU-2", "Only Title", None)

        values = frappe_mock.db.set_value.call_args[0][2]
        assert values["item_name"] == "Only Title"
        assert "description" not in values

    def test_no_update_when_neither_provided(self, frappe_mock):
        frappe_mock.db.set_value = MagicMock()

        _update_item_details("SKU-3", None, None)

        frappe_mock.db.set_value.assert_not_called()


# ---------------------------------------------------------------------------
# sync_inventory
# ---------------------------------------------------------------------------

class TestSyncInventory:

    # ---- guard clauses -------------------------------------------------------

    def test_returns_early_when_sync_not_enabled(self, frappe_mock):
        """sync_enabled = 0 (or None) must return immediately without calling eBay."""
        frappe_mock.db.get_single_value = MagicMock(return_value=None)

        with _make_ebay_wrapper_patch([]) as mock_wrapper_cls:
            result = sync_inventory()

        assert result["message"] == "Sync not enabled"
        assert result["synced"] == 0
        mock_wrapper_cls.assert_not_called()

    def test_returns_early_when_sync_disabled_explicitly(self, frappe_mock):
        """sync_enabled = 0 explicitly also triggers the early exit."""
        frappe_mock.db.get_single_value = MagicMock(return_value=0)

        with _make_ebay_wrapper_patch([]) as mock_wrapper_cls:
            result = sync_inventory()

        assert result["message"] == "Sync not enabled"
        mock_wrapper_cls.assert_not_called()

    def test_returns_early_when_no_default_warehouse(self, frappe_mock):
        """Missing default_warehouse must return before building reconciliation."""
        def _gsv(doctype, field):
            if field == "sync_enabled":
                return 1
            return None  # warehouse, company all None

        frappe_mock.db.get_single_value = MagicMock(side_effect=_gsv)
        frappe_mock.db.sql = MagicMock(return_value=[])

        with _make_ebay_wrapper_patch([]) as mock_wrapper_cls:
            result = sync_inventory()

        assert "No Default Warehouse" in result["message"]
        assert result["synced"] == 0

    # ---- reconciliation item building ----------------------------------------

    def test_ebay_items_added_with_correct_qty(self, frappe_mock, sample_inventory_items):
        """Each eBay item must appear in reconciliation_items with its eBay quantity."""
        _configure_settings(frappe_mock)
        frappe_mock.db.exists = MagicMock(return_value=True)  # all items exist
        frappe_mock.db.get_value = MagicMock(return_value=10.0)  # any valuation rate
        frappe_mock.db.sql = MagicMock(return_value=[])  # no extra warehouse items

        submitted_items = []

        def _capture_get_doc(data):
            doc = MagicMock()
            doc.insert = MagicMock()
            doc.submit = MagicMock()
            if isinstance(data, dict) and data.get("doctype") == "Stock Reconciliation":
                submitted_items.extend(data.get("items", []))
            return doc

        frappe_mock.get_doc = MagicMock(side_effect=_capture_get_doc)

        with _make_ebay_wrapper_patch(sample_inventory_items):
            result = sync_inventory()

        assert "Error" not in result.get("message", ""), result
        # PART-001 qty=3, PART-002 qty=0
        qty_map = {i["item_code"]: i["qty"] for i in submitted_items}
        assert qty_map.get("PART-001") == 3.0
        assert qty_map.get("PART-002") == 0.0

    def test_warehouse_items_not_in_ebay_zeroed_out(self, frappe_mock):
        """Items that have stock in the warehouse but are absent from eBay get qty=0."""
        _configure_settings(frappe_mock)
        frappe_mock.db.exists = MagicMock(return_value=True)
        frappe_mock.db.get_value = MagicMock(return_value=5.0)

        # Only PART-OLD is in the warehouse; eBay has none
        frappe_mock.db.sql = MagicMock(return_value=[
            _make_bin_row("PART-OLD", 7)
        ])

        submitted_items = []

        def _capture_get_doc(data):
            doc = MagicMock()
            doc.insert = MagicMock()
            doc.submit = MagicMock()
            if isinstance(data, dict) and data.get("doctype") == "Stock Reconciliation":
                submitted_items.extend(data.get("items", []))
            return doc

        frappe_mock.get_doc = MagicMock(side_effect=_capture_get_doc)

        ebay_items = [_make_ebay_item("PART-NEW", 2)]
        with _make_ebay_wrapper_patch(ebay_items):
            result = sync_inventory()

        assert "Error" not in result.get("message", ""), result
        qty_map = {i["item_code"]: i["qty"] for i in submitted_items}
        assert qty_map["PART-OLD"] == 0
        assert qty_map["PART-NEW"] == 2.0

    def test_warehouse_item_already_in_ebay_not_double_added(self, frappe_mock):
        """An item present in both eBay and the warehouse bin must appear only once
        (from the eBay pass), not be zeroed out by pass 2."""
        _configure_settings(frappe_mock)
        frappe_mock.db.exists = MagicMock(return_value=True)
        frappe_mock.db.get_value = MagicMock(return_value=5.0)

        # PART-001 is in both the eBay list and the warehouse
        frappe_mock.db.sql = MagicMock(return_value=[
            _make_bin_row("PART-001", 5)
        ])

        submitted_items = []

        def _capture_get_doc(data):
            doc = MagicMock()
            doc.insert = MagicMock()
            doc.submit = MagicMock()
            if isinstance(data, dict) and data.get("doctype") == "Stock Reconciliation":
                submitted_items.extend(data.get("items", []))
            return doc

        frappe_mock.get_doc = MagicMock(side_effect=_capture_get_doc)

        ebay_items = [_make_ebay_item("PART-001", 3)]
        with _make_ebay_wrapper_patch(ebay_items):
            result = sync_inventory()

        assert "Error" not in result.get("message", ""), result
        codes = [i["item_code"] for i in submitted_items]
        assert codes.count("PART-001") == 1
        # Must use the eBay qty, not zeroed
        assert submitted_items[0]["qty"] == 3.0

    def test_missing_sku_triggers_create_item(self, frappe_mock, sample_inventory_items):
        """SKUs absent from ERPNext (db.exists returns falsy) must trigger item creation."""
        _configure_settings(frappe_mock)
        frappe_mock.db.exists = MagicMock(return_value=None)  # all items missing
        frappe_mock.db.get_value = MagicMock(return_value=1.0)
        frappe_mock.db.sql = MagicMock(return_value=[])

        created_skus = []

        def _capture_get_doc(data):
            doc = MagicMock()
            doc.insert = MagicMock()
            doc.submit = MagicMock()
            if isinstance(data, dict) and data.get("doctype") == "Item":
                created_skus.append(data.get("item_code"))
            return doc

        frappe_mock.get_doc = MagicMock(side_effect=_capture_get_doc)

        with _make_ebay_wrapper_patch(sample_inventory_items):
            sync_inventory()

        # Both sample items are missing → both should be created
        assert "PART-001" in created_skus
        assert "PART-002" in created_skus

    def test_existing_sku_does_not_trigger_create_item(self, frappe_mock,
                                                        sample_inventory_items):
        """SKUs that already exist in ERPNext must NOT trigger item creation."""
        _configure_settings(frappe_mock)
        frappe_mock.db.exists = MagicMock(return_value=True)  # all items exist
        frappe_mock.db.get_value = MagicMock(return_value=5.0)
        frappe_mock.db.sql = MagicMock(return_value=[])

        created_skus = []

        def _capture_get_doc(data):
            doc = MagicMock()
            doc.insert = MagicMock()
            doc.submit = MagicMock()
            if isinstance(data, dict) and data.get("doctype") == "Item":
                created_skus.append(data.get("item_code"))
            return doc

        frappe_mock.get_doc = MagicMock(side_effect=_capture_get_doc)

        with _make_ebay_wrapper_patch(sample_inventory_items):
            sync_inventory()

        assert created_skus == []

    # ---- Stock Reconciliation creation ---------------------------------------

    def test_creates_and_submits_stock_reconciliation(self, frappe_mock,
                                                       sample_inventory_items):
        """A Stock Reconciliation must be inserted and submitted when items exist."""
        _configure_settings(frappe_mock)
        frappe_mock.db.exists = MagicMock(return_value=True)
        frappe_mock.db.get_value = MagicMock(return_value=10.0)
        frappe_mock.db.sql = MagicMock(return_value=[])

        sr_doc = MagicMock()
        sr_doc.insert = MagicMock()
        sr_doc.submit = MagicMock()

        def _capture_get_doc(data):
            if isinstance(data, dict) and data.get("doctype") == "Stock Reconciliation":
                return sr_doc
            return MagicMock(insert=MagicMock(), submit=MagicMock())

        frappe_mock.get_doc = MagicMock(side_effect=_capture_get_doc)

        with _make_ebay_wrapper_patch(sample_inventory_items):
            result = sync_inventory()

        sr_doc.insert.assert_called_once()
        sr_doc.submit.assert_called_once()
        frappe_mock.db.commit.assert_called()
        assert "Error" not in result.get("message", ""), result

    def test_stock_reconciliation_has_correct_doctype_fields(self, frappe_mock,
                                                              sample_inventory_items):
        """The Stock Reconciliation doc must carry the expected metadata fields."""
        _configure_settings(frappe_mock)
        frappe_mock.db.exists = MagicMock(return_value=True)
        frappe_mock.db.get_value = MagicMock(return_value=5.0)
        frappe_mock.db.sql = MagicMock(return_value=[])

        captured_data = {}

        def _capture_get_doc(data):
            doc = MagicMock()
            doc.insert = MagicMock()
            doc.submit = MagicMock()
            if isinstance(data, dict) and data.get("doctype") == "Stock Reconciliation":
                captured_data.update(data)
            return doc

        frappe_mock.get_doc = MagicMock(side_effect=_capture_get_doc)

        with _make_ebay_wrapper_patch(sample_inventory_items):
            sync_inventory()

        assert captured_data.get("purpose") == "Stock Reconciliation"
        assert captured_data.get("company") == "Test Co"
        assert "posting_date" in captured_data
        assert "posting_time" in captured_data

    def test_no_changes_message_when_reconciliation_empty(self, frappe_mock):
        """When eBay returns no items and the warehouse is empty, return 'No changes'."""
        _configure_settings(frappe_mock)
        frappe_mock.db.exists = MagicMock(return_value=True)
        frappe_mock.db.get_value = MagicMock(return_value=5.0)
        frappe_mock.db.sql = MagicMock(return_value=[])  # empty warehouse

        sr_doc = MagicMock()

        def _capture_get_doc(data):
            if isinstance(data, dict) and data.get("doctype") == "Stock Reconciliation":
                return sr_doc
            return MagicMock(insert=MagicMock(), submit=MagicMock())

        frappe_mock.get_doc = MagicMock(side_effect=_capture_get_doc)

        with _make_ebay_wrapper_patch([]):  # empty eBay response
            result = sync_inventory()

        assert "No changes" in result["message"]
        assert result["synced"] == 0
        # Stock Reconciliation must NOT have been created
        sr_doc.insert.assert_not_called()

    # ---- logging counts -------------------------------------------------------

    def test_logs_correct_updated_and_zeroed_counts(self, frappe_mock):
        """Return dict must reflect the correct split of updated vs zeroed items."""
        _configure_settings(frappe_mock)
        frappe_mock.db.exists = MagicMock(return_value=True)
        frappe_mock.db.get_value = MagicMock(return_value=5.0)

        # 1 warehouse-only item to be zeroed; 2 eBay items to be updated
        frappe_mock.db.sql = MagicMock(return_value=[
            _make_bin_row("PART-OLD", 3)  # not in eBay → zeroed
        ])

        ebay_items = [
            _make_ebay_item("PART-001", 5),
            _make_ebay_item("PART-002", 2),
        ]

        def _capture_get_doc(data):
            doc = MagicMock()
            doc.insert = MagicMock()
            doc.submit = MagicMock()
            return doc

        frappe_mock.get_doc = MagicMock(side_effect=_capture_get_doc)

        with _make_ebay_wrapper_patch(ebay_items):
            result = sync_inventory()

        assert "Error" not in result.get("message", ""), result
        # 2 eBay items synced + 1 zeroed = 3 total
        assert result["synced"] == 3
        assert "2" in result["message"]   # updated count
        assert "1" in result["message"]   # zeroed count

    def test_valuation_rate_included_in_reconciliation_rows(self, frappe_mock):
        """Each reconciliation row must carry the valuation_rate returned by
        _get_valuation_rate so ERPNext can post accurate stock values."""
        _configure_settings(frappe_mock)
        frappe_mock.db.exists = MagicMock(return_value=True)
        frappe_mock.db.sql = MagicMock(return_value=[])

        def _get_value(doctype, filters_or_field, field=None):
            # Return a known bin rate so the test is deterministic
            if doctype == "Bin":
                return 42.0
            return None

        frappe_mock.db.get_value = MagicMock(side_effect=_get_value)

        submitted_items = []

        def _capture_get_doc(data):
            doc = MagicMock()
            doc.insert = MagicMock()
            doc.submit = MagicMock()
            if isinstance(data, dict) and data.get("doctype") == "Stock Reconciliation":
                submitted_items.extend(data.get("items", []))
            return doc

        frappe_mock.get_doc = MagicMock(side_effect=_capture_get_doc)

        ebay_items = [_make_ebay_item("PART-001", 5)]
        with _make_ebay_wrapper_patch(ebay_items):
            sync_inventory()

        assert len(submitted_items) == 1
        assert submitted_items[0]["valuation_rate"] == 42.0

    def test_items_without_sku_are_skipped(self, frappe_mock):
        """eBay items missing the 'sku' key must be silently ignored."""
        _configure_settings(frappe_mock)
        frappe_mock.db.exists = MagicMock(return_value=True)
        frappe_mock.db.get_value = MagicMock(return_value=5.0)
        frappe_mock.db.sql = MagicMock(return_value=[])

        submitted_items = []

        def _capture_get_doc(data):
            doc = MagicMock()
            doc.insert = MagicMock()
            doc.submit = MagicMock()
            if isinstance(data, dict) and data.get("doctype") == "Stock Reconciliation":
                submitted_items.extend(data.get("items", []))
            return doc

        frappe_mock.get_doc = MagicMock(side_effect=_capture_get_doc)

        # One item with sku, one without
        ebay_items = [
            _make_ebay_item("PART-001", 3),
            {"availability": {"shipToLocationAvailability": {"quantity": 1}}},  # no sku
        ]
        with _make_ebay_wrapper_patch(ebay_items):
            result = sync_inventory()

        assert "Error" not in result.get("message", ""), result
        codes = [i["item_code"] for i in submitted_items]
        assert "PART-001" in codes
        assert len(codes) == 1
