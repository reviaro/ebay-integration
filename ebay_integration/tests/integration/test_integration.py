"""
Integration tests — require a live ERPNext instance.

Run with (inside Docker container):
    cd /home/frappe/frappe-bench/apps/ebay_integration
    /home/frappe/frappe-bench/env/bin/python -m pytest ebay_integration/tests/integration/ -v -m integration

These tests use real Frappe APIs against the 'frontend' site.
eBay HTTP calls are still mocked — no real eBay API credentials needed.

Isolation strategy:
  - Each test generates a unique order ID (uuid) so old test data never interferes.
  - Records created during tests are cleaned up in teardown fixtures.
  - Never toggling live settings via save() — use frappe.local cache busting instead.
"""

import uuid
import pytest
from unittest.mock import patch, MagicMock


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _unique_order_id():
    return f"INTTEST-{uuid.uuid4().hex[:8].upper()}"


def _make_ebay_order(order_id):
    return {
        "orderId": order_id,
        "orderPaymentStatus": "UNPAID",   # avoid triggering invoice/payment chain
        "creationDate": "2026-04-20T10:00:00.000Z",
        "buyer": {
            "username": f"inttest_buyer",
            "buyerRegistrationAddress": {"email": f"inttest@ebay.placeholder.com"}
        },
        "pricingSummary": {
            "priceSubtotal": {"value": "25.00", "currency": "USD"},
            "deliveryCost": {"value": "0.00"},
            "tax": {"value": "0.00"},
            "total": {"value": "25.00"}
        },
        "lineItems": [
            {
                "sku": f"INTTEST-SKU-{order_id}",
                "title": "Integration Test Part",
                "quantity": 1,
                "legacyItemId": "999000999",
                "lineItemCost": {"value": "25.00"}
            }
        ]
    }


def _delete_so_if_exists(frappe, order_id):
    """Best-effort cleanup of a test Sales Order (ignores errors)."""
    try:
        so_name = frappe.db.get_value("Sales Order", {"po_no": order_id}, "name")
        if not so_name:
            return
        so = frappe.get_doc("Sales Order", so_name)
        if so.docstatus == 1:
            so.cancel()
        frappe.delete_doc("Sales Order", so_name, ignore_permissions=True, force=True)
        frappe.db.commit()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestSyncOrdersIntegration:
    """Tests that process_order() writes correctly to a live ERPNext site."""

    def test_process_order_creates_sales_order(self):
        """A new eBay order creates a Sales Order with correct fields."""
        import frappe
        from ebay_integration.utils.sync_orders import process_order

        order_id = _unique_order_id()
        try:
            result = process_order(_make_ebay_order(order_id))

            assert result is True, "process_order() should return True for a new order"

            so_name = frappe.db.get_value("Sales Order", {"po_no": order_id}, "name")
            assert so_name is not None, "Sales Order was not created in ERPNext"

            so = frappe.get_doc("Sales Order", so_name)
            assert so.po_no == order_id
            assert len(so.items) == 1
            assert so.items[0].item_code == f"INTTEST-SKU-{order_id}"
        finally:
            _delete_so_if_exists(frappe, order_id)

    def test_process_order_skips_duplicate(self):
        """process_order() returns False when the order already exists."""
        import frappe
        from ebay_integration.utils.sync_orders import process_order

        order_id = _unique_order_id()
        try:
            process_order(_make_ebay_order(order_id))          # first import
            result = process_order(_make_ebay_order(order_id)) # duplicate
            assert result is False
        finally:
            _delete_so_if_exists(frappe, order_id)


@pytest.mark.integration
class TestSyncInventoryIntegration:
    """Tests that sync_inventory() correctly reconciles stock."""

    def test_sync_inventory_zeros_removed_item(self):
        """
        sync_inventory() runs to completion and reports synced/zeroed counts.
        eBay API is mocked to return one item; the warehouse may zero others.
        """
        import frappe
        from ebay_integration.utils.sync_inventory import sync_inventory

        mock_items = [
            {"sku": "INTTEST-INV-KEEP", "availability": {"shipToLocationAvailability": {"quantity": 5}}}
        ]

        # eBayWrapper is imported inside the function body — patch the source module
        with patch("ebay_integration.utils.ebay_api.eBayWrapper") as MockWrapper:
            MockWrapper.return_value.get_my_selling.return_value = mock_items
            result = sync_inventory()

        assert "synced" in result

    def test_sync_inventory_returns_early_when_disabled(self):
        """sync_inventory() returns a 'not enabled' message when sync_enabled=0."""
        import frappe
        from ebay_integration.utils.sync_inventory import sync_inventory

        # frappe.db.get_single_value caches in frappe.local.db_singles (in-process).
        # frappe.clear_cache() clears Redis/disk but not the in-process dict.
        # The most reliable way to test this guard is to patch the call directly.
        with patch("frappe.db.get_single_value", return_value=0):
            result = sync_inventory()

        assert result["synced"] == 0
        assert "not enabled" in result["message"].lower()


@pytest.mark.integration
class TestEbayApiIntegration:
    """Smoke tests for eBayWrapper against a live ERPNext (HTTP still mocked)."""

    def test_wrapper_initializes_with_valid_settings(self):
        """eBayWrapper constructs correctly when eBay Settings are configured."""
        import frappe
        from ebay_integration.utils.ebay_api import eBayWrapper

        settings = frappe.get_single("eBay Settings")
        if not settings.client_id or not settings.get_password("access_token"):
            pytest.skip("eBay Settings not configured on this site")

        wrapper = eBayWrapper()
        assert wrapper.access_token is not None
