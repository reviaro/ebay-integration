"""
Unit tests for ebay_integration.utils.sync_orders.

Coverage targets:
  - get_or_create_item: item code derivation paths, truncation, existence check
  - get_or_create_customer: email resolution, existing vs new customer
  - process_order: guard clauses, Sales Order creation, tax/shipping rows,
    invoice+payment dispatch on PAID status
"""

import hashlib
import importlib
import sys
from unittest.mock import MagicMock, call, patch

import pytest

# ---------------------------------------------------------------------------
# Ensure the module is imported *after* the frappe mock is in sys.modules.
# conftest.py installs the mock at collection time, so a plain import here is
# safe.  We re-import via importlib so individual tests can rely on a fresh
# module-level view without carrying over side-effects between sessions.
# ---------------------------------------------------------------------------
import ebay_integration.utils.sync_orders as sync_orders_mod


# ===========================================================================
# Helpers
# ===========================================================================

def _md5_prefix(text: str) -> str:
    """Return the 8-char MD5 hex prefix used by get_or_create_item."""
    return hashlib.md5(text.encode()).hexdigest()[:8]


# ===========================================================================
# get_or_create_item
# ===========================================================================

class TestGetOrCreateItem:
    """Tests for sync_orders.get_or_create_item."""

    # -- Existing item -------------------------------------------------------

    def test_returns_existing_item_code_when_found(self, frappe_mock):
        """When frappe.db.exists returns a truthy value the function must
        return that item_code without calling frappe.get_doc."""
        frappe_mock.db.exists.return_value = "PART-001"
        frappe_mock.get_doc.reset_mock()  # clear calls from prior tests in session

        result = sync_orders_mod.get_or_create_item("PART-001", "Widget", 9.99, "111")

        assert result == "PART-001"
        # filter to Item-creation calls only (not eBay Log or Stock Reconciliation)
        item_calls = [
            c for c in frappe_mock.get_doc.call_args_list
            if isinstance(c.args[0], dict) and c.args[0].get("doctype") == "Item"
        ]
        assert item_calls == []

    # -- SKU path ------------------------------------------------------------

    def test_creates_item_with_sku_as_item_code(self, frappe_mock):
        """When sku is supplied the item_code must equal the sku string."""
        frappe_mock.db.exists.return_value = None
        mock_item = MagicMock()
        mock_item.name = "PART-002"
        frappe_mock.get_doc.return_value = mock_item

        result = sync_orders_mod.get_or_create_item("PART-002", "Widget", 9.99, "222")

        assert result == "PART-002"
        call_kwargs = frappe_mock.get_doc.call_args[0][0]
        assert call_kwargs["item_code"] == "PART-002"

    # -- eBay item ID fallback -----------------------------------------------

    def test_uses_ebay_item_id_when_sku_is_none(self, frappe_mock):
        """When sku is None but ebay_item_id is provided, item_code must be
        'EBAY-{ebay_item_id}'."""
        frappe_mock.db.exists.return_value = None
        mock_item = MagicMock()
        mock_item.name = "EBAY-999888"
        frappe_mock.get_doc.return_value = mock_item

        result = sync_orders_mod.get_or_create_item(None, "Widget", 9.99, "999888")

        assert result == "EBAY-999888"
        call_kwargs = frappe_mock.get_doc.call_args[0][0]
        assert call_kwargs["item_code"] == "EBAY-999888"

    # -- Title hash fallback -------------------------------------------------

    def test_uses_title_hash_when_sku_and_ebay_id_are_none(self, frappe_mock):
        """When both sku and ebay_item_id are None, item_code must be
        'EBAY-{TITLE}-{hash}'."""
        title = "Some Unique Title"
        expected_hash = _md5_prefix(title)
        expected_code = f"EBAY-{title[:50].replace(' ', '-').upper()}-{expected_hash}"

        frappe_mock.db.exists.return_value = None
        mock_item = MagicMock()
        mock_item.name = expected_code
        frappe_mock.get_doc.return_value = mock_item

        result = sync_orders_mod.get_or_create_item(None, title, 9.99, None)

        assert result == expected_code
        call_kwargs = frappe_mock.get_doc.call_args[0][0]
        assert call_kwargs["item_code"] == expected_code

    # -- Timestamp fallback --------------------------------------------------

    def test_uses_timestamp_fallback_when_title_also_none(self, frappe_mock):
        """When sku, ebay_item_id, and title are all None, item_code must
        start with 'EBAY-ITEM-'."""
        frappe_mock.db.exists.return_value = None
        mock_item = MagicMock()
        # The code is set on the doc dict, not on mock_item.name directly when
        # name is not returned; we read it from get_doc call args instead.
        frappe_mock.get_doc.return_value = mock_item

        sync_orders_mod.get_or_create_item(None, None, 9.99, None)

        call_kwargs = frappe_mock.get_doc.call_args[0][0]
        assert call_kwargs["item_code"].startswith("EBAY-ITEM-")

    # -- Truncation ----------------------------------------------------------

    @pytest.mark.parametrize("sku,ebay_id", [
        ("X" * 200, "111"),     # long sku
        (None, "Y" * 200),      # long ebay_id produces "EBAY-YYY…"
    ])
    def test_item_code_truncated_to_140_chars(self, frappe_mock, sku, ebay_id):
        """item_code must never exceed 140 characters."""
        frappe_mock.db.exists.return_value = None
        mock_item = MagicMock()
        frappe_mock.get_doc.return_value = mock_item

        sync_orders_mod.get_or_create_item(sku, "Title", 1.0, ebay_id)

        call_kwargs = frappe_mock.get_doc.call_args[0][0]
        assert len(call_kwargs["item_code"]) <= 140

    def test_title_hash_item_code_truncated_to_140_chars(self, frappe_mock):
        """Title-hash derived item_code must also be capped at 140 chars."""
        long_title = "A Very Long Title " * 20  # >140 chars
        frappe_mock.db.exists.return_value = None
        mock_item = MagicMock()
        frappe_mock.get_doc.return_value = mock_item

        sync_orders_mod.get_or_create_item(None, long_title, 1.0, None)

        call_kwargs = frappe_mock.get_doc.call_args[0][0]
        assert len(call_kwargs["item_code"]) <= 140

    # -- insert called -------------------------------------------------------

    def test_insert_called_on_new_item(self, frappe_mock):
        """frappe.get_doc().insert() must be called when creating a new item."""
        frappe_mock.db.exists.return_value = None
        mock_item = MagicMock()
        mock_item.name = "SKU-NEW"
        frappe_mock.get_doc.return_value = mock_item

        sync_orders_mod.get_or_create_item("SKU-NEW", "New Widget", 5.0, None)

        mock_item.insert.assert_called_once_with(ignore_permissions=True)

    # -- item_name set to title ----------------------------------------------

    def test_item_name_set_to_title(self, frappe_mock):
        """The 'item_name' key in the doc dict must equal the provided title."""
        frappe_mock.db.exists.return_value = None
        mock_item = MagicMock()
        mock_item.name = "SKU-123"
        frappe_mock.get_doc.return_value = mock_item

        sync_orders_mod.get_or_create_item("SKU-123", "Real Title", 10.0, None)

        call_kwargs = frappe_mock.get_doc.call_args[0][0]
        assert call_kwargs["item_name"] == "Real Title"


# ===========================================================================
# get_or_create_customer
# ===========================================================================

class TestGetOrCreateCustomer:
    """Tests for sync_orders.get_or_create_customer."""

    def _make_order(self, username="test_buyer", email="buyer@example.com"):
        order = {
            "buyer": {
                "username": username,
                "buyerRegistrationAddress": {"email": email}
            }
        }
        return order

    # -- Existing customer ---------------------------------------------------

    def test_returns_existing_customer_when_found_by_email(self, frappe_mock):
        """If frappe.db.get_value returns a customer name, return it
        immediately without creating a new Customer doc."""
        frappe_mock.db.get_value.return_value = "CUST-001"
        # Reset call history so prior tests in the session don't interfere
        frappe_mock.get_doc.reset_mock()

        result = sync_orders_mod.get_or_create_customer(self._make_order())

        assert result == "CUST-001"
        # No Customer document should have been created
        customer_create_calls = [
            c for c in frappe_mock.get_doc.call_args_list
            if isinstance(c.args[0], dict) and c.args[0].get("doctype") == "Customer"
        ]
        assert customer_create_calls == []

    # -- New customer --------------------------------------------------------

    def test_creates_new_customer_when_not_found(self, frappe_mock):
        """When no existing customer is found, a Customer doc must be
        created with the correct fields."""
        frappe_mock.db.get_value.return_value = None
        frappe_mock.db.get_single_value.return_value = "Retail Customers"

        mock_customer = MagicMock()
        mock_customer.name = "test_buyer"
        frappe_mock.get_doc.return_value = mock_customer

        result = sync_orders_mod.get_or_create_customer(self._make_order())

        assert result == "test_buyer"
        doc_data = frappe_mock.get_doc.call_args[0][0]
        assert doc_data["doctype"] == "Customer"
        assert doc_data["customer_name"] == "test_buyer"
        assert doc_data["customer_type"] == "Individual"
        assert doc_data["email_id"] == "buyer@example.com"
        mock_customer.insert.assert_called_once_with(ignore_permissions=True)

    # -- Placeholder email ---------------------------------------------------

    def test_uses_placeholder_email_when_email_missing(self, frappe_mock):
        """When buyerRegistrationAddress has no email, the placeholder
        '{username}@ebay.placeholder.com' must be used."""
        order = {
            "buyer": {
                "username": "nomail_user",
                "buyerRegistrationAddress": {}
            }
        }
        frappe_mock.db.get_value.return_value = None
        mock_customer = MagicMock()
        mock_customer.name = "nomail_user"
        frappe_mock.get_doc.return_value = mock_customer

        sync_orders_mod.get_or_create_customer(order)

        doc_data = frappe_mock.get_doc.call_args[0][0]
        assert doc_data["email_id"] == "nomail_user@ebay.placeholder.com"

    def test_uses_placeholder_email_when_buyer_reg_address_absent(self, frappe_mock):
        """When buyerRegistrationAddress key is entirely absent the placeholder
        email must still be generated."""
        order = {"buyer": {"username": "ghost_user"}}
        frappe_mock.db.get_value.return_value = None
        mock_customer = MagicMock()
        mock_customer.name = "ghost_user"
        frappe_mock.get_doc.return_value = mock_customer

        sync_orders_mod.get_or_create_customer(order)

        doc_data = frappe_mock.get_doc.call_args[0][0]
        assert doc_data["email_id"] == "ghost_user@ebay.placeholder.com"

    # -- Default username ----------------------------------------------------

    def test_uses_ebay_buyer_as_default_username(self, frappe_mock):
        """When buyer.username is absent, 'eBay Buyer' must be used."""
        order = {"buyer": {}}
        frappe_mock.db.get_value.return_value = None
        mock_customer = MagicMock()
        mock_customer.name = "eBay Buyer"
        frappe_mock.get_doc.return_value = mock_customer

        sync_orders_mod.get_or_create_customer(order)

        doc_data = frappe_mock.get_doc.call_args[0][0]
        assert doc_data["customer_name"] == "eBay Buyer"
        assert doc_data["email_id"] == "eBay Buyer@ebay.placeholder.com"

    # -- Customer group fallback ---------------------------------------------

    def test_customer_group_falls_back_to_all_customer_groups(self, frappe_mock):
        """When eBay Settings returns None for default_customer_group the
        value 'All Customer Groups' must be used."""
        frappe_mock.db.get_value.return_value = None
        frappe_mock.db.get_single_value.return_value = None  # no group configured
        mock_customer = MagicMock()
        mock_customer.name = "test_buyer"
        frappe_mock.get_doc.return_value = mock_customer

        sync_orders_mod.get_or_create_customer(self._make_order())

        doc_data = frappe_mock.get_doc.call_args[0][0]
        assert doc_data["customer_group"] == "All Customer Groups"


# ===========================================================================
# process_order
# ===========================================================================

MODULE = "ebay_integration.utils.sync_orders"


class TestProcessOrder:
    """Tests for sync_orders.process_order."""

    # -- Guard: missing orderId ---------------------------------------------

    def test_returns_false_when_order_id_missing(self, frappe_mock, sample_ebay_order):
        """An order without an orderId must be skipped (return False)."""
        del sample_ebay_order["orderId"]
        result = sync_orders_mod.process_order(sample_ebay_order)
        assert result is False

    def test_returns_false_when_order_id_is_none(self, frappe_mock, sample_ebay_order):
        """An order whose orderId is explicitly None must also return False."""
        sample_ebay_order["orderId"] = None
        result = sync_orders_mod.process_order(sample_ebay_order)
        assert result is False

    # -- Guard: already exists ---------------------------------------------

    def test_returns_false_when_order_already_exists(self, frappe_mock, sample_ebay_order):
        """If the Sales Order already exists in ERPNext, skip it."""
        frappe_mock.db.exists.return_value = "SO-0001"
        result = sync_orders_mod.process_order(sample_ebay_order)
        assert result is False

    # -- Guard: empty lineItems --------------------------------------------

    def test_returns_false_when_line_items_empty(self, frappe_mock, sample_ebay_order):
        """An order with no line items must return False."""
        sample_ebay_order["lineItems"] = []
        frappe_mock.db.exists.return_value = None

        with patch(f"{MODULE}.get_or_create_customer", return_value="CUST-1"), \
             patch(f"{MODULE}.get_or_create_item", return_value="ITEM-1"):
            result = sync_orders_mod.process_order(sample_ebay_order)

        assert result is False

    # -- Happy path: Sales Order creation ----------------------------------

    def test_calls_get_doc_to_create_sales_order(self, frappe_mock, sample_ebay_order):
        """process_order must call frappe.get_doc with doctype 'Sales Order'."""
        frappe_mock.db.exists.return_value = None
        frappe_mock.db.get_single_value.return_value = "Test Company"

        mock_so = MagicMock()
        frappe_mock.get_doc.return_value = mock_so

        with patch(f"{MODULE}.get_or_create_customer", return_value="CUST-1"), \
             patch(f"{MODULE}.get_or_create_item", return_value="ITEM-1"), \
             patch(f"{MODULE}.get_shipping_account", return_value=None), \
             patch(f"{MODULE}.get_tax_account", return_value=None), \
             patch(f"{MODULE}.create_invoice_and_payment"):
            result = sync_orders_mod.process_order(sample_ebay_order)

        assert result is True
        doc_data = frappe_mock.get_doc.call_args[0][0]
        assert doc_data["doctype"] == "Sales Order"
        assert doc_data["po_no"] == "12-34567-89012"

    def test_calls_insert_and_submit_on_sales_order(self, frappe_mock, sample_ebay_order):
        """process_order must call so.insert() and so.submit()."""
        frappe_mock.db.exists.return_value = None
        frappe_mock.db.get_single_value.return_value = "Test Company"

        mock_so = MagicMock()
        frappe_mock.get_doc.return_value = mock_so

        with patch(f"{MODULE}.get_or_create_customer", return_value="CUST-1"), \
             patch(f"{MODULE}.get_or_create_item", return_value="ITEM-1"), \
             patch(f"{MODULE}.get_shipping_account", return_value=None), \
             patch(f"{MODULE}.get_tax_account", return_value=None), \
             patch(f"{MODULE}.create_invoice_and_payment"):
            sync_orders_mod.process_order(sample_ebay_order)

        mock_so.insert.assert_called_once_with(ignore_permissions=True)
        mock_so.submit.assert_called_once()

    # -- Payment: PAID status ----------------------------------------------

    def test_calls_create_invoice_and_payment_when_paid(
        self, frappe_mock, sample_ebay_order
    ):
        """create_invoice_and_payment must be called when orderPaymentStatus is PAID."""
        assert sample_ebay_order["orderPaymentStatus"] == "PAID"
        frappe_mock.db.exists.return_value = None
        frappe_mock.db.get_single_value.return_value = "Test Company"

        mock_so = MagicMock()
        frappe_mock.get_doc.return_value = mock_so

        with patch(f"{MODULE}.get_or_create_customer", return_value="CUST-1"), \
             patch(f"{MODULE}.get_or_create_item", return_value="ITEM-1"), \
             patch(f"{MODULE}.get_shipping_account", return_value=None), \
             patch(f"{MODULE}.get_tax_account", return_value=None), \
             patch(f"{MODULE}.create_invoice_and_payment") as mock_invoice:
            sync_orders_mod.process_order(sample_ebay_order)

        mock_invoice.assert_called_once_with(mock_so, sample_ebay_order)

    @pytest.mark.parametrize("payment_status", ["UNPAID", "FAILED", "", None])
    def test_does_not_call_create_invoice_when_not_paid(
        self, frappe_mock, sample_ebay_order, payment_status
    ):
        """create_invoice_and_payment must NOT be called for non-PAID statuses."""
        sample_ebay_order["orderPaymentStatus"] = payment_status
        frappe_mock.db.exists.return_value = None
        frappe_mock.db.get_single_value.return_value = "Test Company"

        mock_so = MagicMock()
        frappe_mock.get_doc.return_value = mock_so

        with patch(f"{MODULE}.get_or_create_customer", return_value="CUST-1"), \
             patch(f"{MODULE}.get_or_create_item", return_value="ITEM-1"), \
             patch(f"{MODULE}.get_shipping_account", return_value=None), \
             patch(f"{MODULE}.get_tax_account", return_value=None), \
             patch(f"{MODULE}.create_invoice_and_payment") as mock_invoice:
            sync_orders_mod.process_order(sample_ebay_order)

        mock_invoice.assert_not_called()

    # -- Taxes: shipping ---------------------------------------------------

    def test_adds_shipping_tax_row_when_shipping_cost_positive(
        self, frappe_mock, sample_ebay_order
    ):
        """When shipping_cost > 0 and get_shipping_account returns an account,
        a tax row with 'eBay Shipping' description must be added to so_data."""
        # sample_ebay_order has deliveryCost of "5.00"
        frappe_mock.db.exists.return_value = None
        frappe_mock.db.get_single_value.return_value = "Test Company"

        captured = {}

        def capture_get_doc(data):
            captured["data"] = data
            doc = MagicMock()
            return doc

        frappe_mock.get_doc.side_effect = capture_get_doc

        with patch(f"{MODULE}.get_or_create_customer", return_value="CUST-1"), \
             patch(f"{MODULE}.get_or_create_item", return_value="ITEM-1"), \
             patch(f"{MODULE}.get_shipping_account", return_value="Shipping Income - TC"), \
             patch(f"{MODULE}.get_tax_account", return_value=None), \
             patch(f"{MODULE}.create_invoice_and_payment"):
            sync_orders_mod.process_order(sample_ebay_order)

        so_data = captured["data"]
        assert "taxes" in so_data
        shipping_rows = [
            t for t in so_data["taxes"] if t["description"] == "eBay Shipping"
        ]
        assert len(shipping_rows) == 1
        assert shipping_rows[0]["tax_amount"] == 5.0
        assert shipping_rows[0]["account_head"] == "Shipping Income - TC"
        assert shipping_rows[0]["charge_type"] == "Actual"

    def test_no_shipping_tax_row_when_shipping_account_none(
        self, frappe_mock, sample_ebay_order
    ):
        """If get_shipping_account returns None, no shipping row must be added
        even though shipping_cost > 0."""
        frappe_mock.db.exists.return_value = None
        frappe_mock.db.get_single_value.return_value = "Test Company"

        captured = {}

        def capture_get_doc(data):
            captured["data"] = data
            return MagicMock()

        frappe_mock.get_doc.side_effect = capture_get_doc

        with patch(f"{MODULE}.get_or_create_customer", return_value="CUST-1"), \
             patch(f"{MODULE}.get_or_create_item", return_value="ITEM-1"), \
             patch(f"{MODULE}.get_shipping_account", return_value=None), \
             patch(f"{MODULE}.get_tax_account", return_value=None), \
             patch(f"{MODULE}.create_invoice_and_payment"):
            sync_orders_mod.process_order(sample_ebay_order)

        so_data = captured["data"]
        assert "taxes" not in so_data or not any(
            t["description"] == "eBay Shipping" for t in so_data.get("taxes", [])
        )

    def test_no_shipping_tax_row_when_shipping_cost_zero(
        self, frappe_mock, sample_ebay_order
    ):
        """When deliveryCost is 0.00, no shipping tax row must be added."""
        sample_ebay_order["pricingSummary"]["deliveryCost"]["value"] = "0.00"
        frappe_mock.db.exists.return_value = None
        frappe_mock.db.get_single_value.return_value = "Test Company"

        captured = {}

        def capture_get_doc(data):
            captured["data"] = data
            return MagicMock()

        frappe_mock.get_doc.side_effect = capture_get_doc

        with patch(f"{MODULE}.get_or_create_customer", return_value="CUST-1"), \
             patch(f"{MODULE}.get_or_create_item", return_value="ITEM-1"), \
             patch(f"{MODULE}.get_shipping_account", return_value="Shipping Income - TC"), \
             patch(f"{MODULE}.get_tax_account", return_value=None), \
             patch(f"{MODULE}.create_invoice_and_payment"):
            sync_orders_mod.process_order(sample_ebay_order)

        so_data = captured["data"]
        shipping_rows = [
            t for t in so_data.get("taxes", []) if t["description"] == "eBay Shipping"
        ]
        assert len(shipping_rows) == 0

    # -- Taxes: eBay tax ---------------------------------------------------

    def test_ebay_collected_tax_excluded_from_taxes_and_stored_as_info(
            self, frappe_mock, sample_ebay_order):
        """eBay marketplace-collected tax is remitted by eBay, so it must NOT be
        booked as a Sales Order charge. It is stored on the informational
        ebay_collected_tax field instead. sample_ebay_order has tax "3.50"."""
        frappe_mock.db.exists.return_value = None
        frappe_mock.db.get_single_value.return_value = "Test Company"

        captured = {}

        def capture_get_doc(data):
            captured["data"] = data
            return MagicMock()

        frappe_mock.get_doc.side_effect = capture_get_doc

        with patch(f"{MODULE}.get_or_create_customer", return_value="CUST-1"), \
             patch(f"{MODULE}.get_or_create_item", return_value="ITEM-1"), \
             patch(f"{MODULE}.get_shipping_account", return_value=None), \
             patch(f"{MODULE}.get_tax_account", return_value="Tax Payable - TC"), \
             patch(f"{MODULE}.create_invoice_and_payment"):
            sync_orders_mod.process_order(sample_ebay_order)

        so_data = captured["data"]
        # No eBay-collected tax row in the taxes table
        tax_rows = [
            t for t in so_data.get("taxes", [])
            if "eBay Collected Tax" in t["description"]
        ]
        assert tax_rows == []
        # Tax amount retained as informational field only
        assert so_data.get("ebay_collected_tax") == 3.5

    def test_no_ebay_tax_row_when_tax_account_none(self, frappe_mock, sample_ebay_order):
        """If get_tax_account returns None, no tax row is added even if ebay_tax > 0."""
        frappe_mock.db.exists.return_value = None
        frappe_mock.db.get_single_value.return_value = "Test Company"

        captured = {}

        def capture_get_doc(data):
            captured["data"] = data
            return MagicMock()

        frappe_mock.get_doc.side_effect = capture_get_doc

        with patch(f"{MODULE}.get_or_create_customer", return_value="CUST-1"), \
             patch(f"{MODULE}.get_or_create_item", return_value="ITEM-1"), \
             patch(f"{MODULE}.get_shipping_account", return_value=None), \
             patch(f"{MODULE}.get_tax_account", return_value=None), \
             patch(f"{MODULE}.create_invoice_and_payment"):
            sync_orders_mod.process_order(sample_ebay_order)

        so_data = captured["data"]
        tax_rows = [
            t for t in so_data.get("taxes", [])
            if "eBay Collected Tax" in t["description"]
        ]
        assert len(tax_rows) == 0

    def test_no_ebay_tax_row_when_tax_is_zero(self, frappe_mock, sample_ebay_order):
        """When tax value is 0.00 no tax row must appear."""
        sample_ebay_order["pricingSummary"]["tax"]["value"] = "0.00"
        frappe_mock.db.exists.return_value = None
        frappe_mock.db.get_single_value.return_value = "Test Company"

        captured = {}

        def capture_get_doc(data):
            captured["data"] = data
            return MagicMock()

        frappe_mock.get_doc.side_effect = capture_get_doc

        with patch(f"{MODULE}.get_or_create_customer", return_value="CUST-1"), \
             patch(f"{MODULE}.get_or_create_item", return_value="ITEM-1"), \
             patch(f"{MODULE}.get_shipping_account", return_value=None), \
             patch(f"{MODULE}.get_tax_account", return_value="Tax Payable - TC"), \
             patch(f"{MODULE}.create_invoice_and_payment"):
            sync_orders_mod.process_order(sample_ebay_order)

        so_data = captured["data"]
        tax_rows = [
            t for t in so_data.get("taxes", [])
            if "eBay Collected Tax" in t["description"]
        ]
        assert len(tax_rows) == 0

    # -- Both shipping + tax together --------------------------------------

    def test_shipping_billed_but_ebay_tax_excluded(self, frappe_mock, sample_ebay_order):
        """Shipping is real revenue (billed). eBay-collected tax is excluded from
        the taxes table and kept only as the informational field."""
        frappe_mock.db.exists.return_value = None
        frappe_mock.db.get_single_value.return_value = "Test Company"

        captured = {}

        def capture_get_doc(data):
            captured["data"] = data
            return MagicMock()

        frappe_mock.get_doc.side_effect = capture_get_doc

        with patch(f"{MODULE}.get_or_create_customer", return_value="CUST-1"), \
             patch(f"{MODULE}.get_or_create_item", return_value="ITEM-1"), \
             patch(f"{MODULE}.get_shipping_account", return_value="Shipping Income - TC"), \
             patch(f"{MODULE}.get_tax_account", return_value="Tax Payable - TC"), \
             patch(f"{MODULE}.create_invoice_and_payment"):
            sync_orders_mod.process_order(sample_ebay_order)

        so_data = captured["data"]
        assert "taxes" in so_data
        descriptions = {t["description"] for t in so_data["taxes"]}
        assert "eBay Shipping" in descriptions
        assert "eBay Collected Tax (Sales Tax/VAT)" not in descriptions
        assert so_data.get("ebay_collected_tax") == 3.5

    # -- SO fields ---------------------------------------------------------

    def test_so_fields_populated_correctly(self, frappe_mock, sample_ebay_order):
        """Core Sales Order fields (customer, po_no, currency, company) must
        be populated from the order data and eBay Settings."""
        frappe_mock.db.exists.return_value = None
        frappe_mock.db.get_single_value.return_value = "Acme Corp"

        captured = {}

        def capture_get_doc(data):
            captured["data"] = data
            return MagicMock()

        frappe_mock.get_doc.side_effect = capture_get_doc

        with patch(f"{MODULE}.get_or_create_customer", return_value="CUST-007"), \
             patch(f"{MODULE}.get_or_create_item", return_value="ITEM-X"), \
             patch(f"{MODULE}.get_shipping_account", return_value=None), \
             patch(f"{MODULE}.get_tax_account", return_value=None), \
             patch(f"{MODULE}.create_invoice_and_payment"):
            sync_orders_mod.process_order(sample_ebay_order)

        so_data = captured["data"]
        assert so_data["customer"] == "CUST-007"
        assert so_data["po_no"] == "12-34567-89012"
        assert so_data["currency"] == "USD"
        assert so_data["company"] == "Acme Corp"

    # -- Line item fields --------------------------------------------------

    def test_line_item_fields_populated_from_order(self, frappe_mock, sample_ebay_order):
        """Each line item's item_code, qty, and rate must be taken from the
        eBay lineItems payload."""
        frappe_mock.db.exists.return_value = None
        frappe_mock.db.get_single_value.return_value = "Test Company"

        captured = {}

        def capture_get_doc(data):
            captured["data"] = data
            return MagicMock()

        frappe_mock.get_doc.side_effect = capture_get_doc

        with patch(f"{MODULE}.get_or_create_customer", return_value="CUST-1"), \
             patch(f"{MODULE}.get_or_create_item", return_value="PART-001"), \
             patch(f"{MODULE}.get_shipping_account", return_value=None), \
             patch(f"{MODULE}.get_tax_account", return_value=None), \
             patch(f"{MODULE}.create_invoice_and_payment"):
            sync_orders_mod.process_order(sample_ebay_order)

        so_data = captured["data"]
        assert len(so_data["items"]) == 1
        item_row = so_data["items"][0]
        assert item_row["item_code"] == "PART-001"
        assert item_row["qty"] == 1.0
        assert item_row["rate"] == 50.0

    # -- Variation aspects -------------------------------------------------

    def test_variation_details_included_in_description(
        self, frappe_mock, sample_ebay_order
    ):
        """When variationAspects are present in a line item, the description
        must include them after a pipe character."""
        sample_ebay_order["lineItems"][0]["variationAspects"] = [
            {"name": "Color", "value": "Red"},
            {"name": "Size", "value": "L"},
        ]
        frappe_mock.db.exists.return_value = None
        frappe_mock.db.get_single_value.return_value = "Test Company"

        captured = {}

        def capture_get_doc(data):
            captured["data"] = data
            return MagicMock()

        frappe_mock.get_doc.side_effect = capture_get_doc

        with patch(f"{MODULE}.get_or_create_customer", return_value="CUST-1"), \
             patch(f"{MODULE}.get_or_create_item", return_value="PART-001"), \
             patch(f"{MODULE}.get_shipping_account", return_value=None), \
             patch(f"{MODULE}.get_tax_account", return_value=None), \
             patch(f"{MODULE}.create_invoice_and_payment"):
            sync_orders_mod.process_order(sample_ebay_order)

        item_row = captured["data"]["items"][0]
        assert "|" in item_row["description"]
        assert "Color: Red" in item_row["description"]
        assert "Size: L" in item_row["description"]

    # -- Creation date parsing ---------------------------------------------

    @pytest.mark.parametrize("creation_date,expected", [
        ("2026-04-20T10:00:00.000Z", "2026-04-20"),
        ("", "2026-04-25"),   # empty → nowdate() from mock
    ])
    def test_creation_date_parsed_from_order(
        self, frappe_mock, sample_ebay_order, creation_date, expected
    ):
        """transaction_date on the SO must be the parsed date from creationDate
        (or today's date when absent)."""
        import datetime

        sample_ebay_order["creationDate"] = creation_date
        frappe_mock.db.exists.return_value = None
        frappe_mock.db.get_single_value.return_value = "Test Company"

        captured = {}

        def capture_get_doc(data):
            captured["data"] = data
            return MagicMock()

        frappe_mock.get_doc.side_effect = capture_get_doc

        with patch(f"{MODULE}.get_or_create_customer", return_value="CUST-1"), \
             patch(f"{MODULE}.get_or_create_item", return_value="ITEM-1"), \
             patch(f"{MODULE}.get_shipping_account", return_value=None), \
             patch(f"{MODULE}.get_tax_account", return_value=None), \
             patch(f"{MODULE}.create_invoice_and_payment"):
            sync_orders_mod.process_order(sample_ebay_order)

        so_data = captured["data"]
        transaction_date = so_data["transaction_date"]
        # May be a datetime.date or a string "YYYY-MM-DD"
        if hasattr(transaction_date, "isoformat"):
            assert transaction_date.isoformat() == expected
        else:
            assert str(transaction_date)[:10] == expected

    # -- db.commit called --------------------------------------------------

    def test_db_commit_called_after_insert(self, frappe_mock, sample_ebay_order):
        """frappe.db.commit() must be called after the SO is inserted and
        submitted."""
        frappe_mock.db.exists.return_value = None
        frappe_mock.db.get_single_value.return_value = "Test Company"

        mock_so = MagicMock()
        frappe_mock.get_doc.return_value = mock_so

        with patch(f"{MODULE}.get_or_create_customer", return_value="CUST-1"), \
             patch(f"{MODULE}.get_or_create_item", return_value="ITEM-1"), \
             patch(f"{MODULE}.get_shipping_account", return_value=None), \
             patch(f"{MODULE}.get_tax_account", return_value=None), \
             patch(f"{MODULE}.create_invoice_and_payment"):
            sync_orders_mod.process_order(sample_ebay_order)

        frappe_mock.db.commit.assert_called()


# ===========================================================================
# extract_shipping_info
# ===========================================================================

class TestExtractShippingInfo:
    """Pulls shipping service + tracking number from a Fulfillment order payload."""

    def test_returns_shipping_service_code(self):
        order = {
            "fulfillmentStartInstructions": [
                {"shippingStep": {"shippingServiceCode": "USPSPriority"}}
            ]
        }
        info = sync_orders_mod.extract_shipping_info(order)
        assert info["shipping_service"] == "USPSPriority"

    def test_returns_tracking_number_when_present(self):
        order = {
            "fulfillmentStartInstructions": [
                {"shippingStep": {
                    "shippingServiceCode": "USPSPriority",
                    "shipmentTrackingNumber": "9400111899223817200000",
                }}
            ]
        }
        info = sync_orders_mod.extract_shipping_info(order)
        assert info["tracking_number"] == "9400111899223817200000"

    def test_missing_instructions_returns_none_values(self):
        info = sync_orders_mod.extract_shipping_info({})
        assert info == {"shipping_service": None, "tracking_number": None}

    def test_empty_shipping_step_returns_none_values(self):
        order = {"fulfillmentStartInstructions": [{"shippingStep": {}}]}
        info = sync_orders_mod.extract_shipping_info(order)
        assert info["shipping_service"] is None
        assert info["tracking_number"] is None


class TestProcessOrderSetsShippingFields:
    """process_order must write shipping service / tracking onto the Sales Order."""

    def test_sets_shipping_service_on_sales_order(self, frappe_mock, sample_ebay_order):
        frappe_mock.db.exists.return_value = None
        frappe_mock.db.get_single_value.return_value = "Test Company"
        sample_ebay_order["fulfillmentStartInstructions"] = [
            {"shippingStep": {"shippingServiceCode": "FedExHomeDelivery"}}
        ]
        mock_so = MagicMock(name="SO-TRACK")
        mock_so.name = "SO-TRACK"
        frappe_mock.get_doc.return_value = mock_so

        with patch(f"{MODULE}.get_or_create_customer", return_value="CUST-1"), \
             patch(f"{MODULE}.get_or_create_item", return_value="ITEM-1"), \
             patch(f"{MODULE}.get_shipping_account", return_value=None), \
             patch(f"{MODULE}.get_tax_account", return_value=None), \
             patch(f"{MODULE}.create_invoice_and_payment"):
            sync_orders_mod.process_order(sample_ebay_order)

        shipping_calls = [
            c for c in frappe_mock.db.set_value.call_args_list
            if c.args[0] == "Sales Order"
            and isinstance(c.args[2], dict)
            and c.args[2].get("ebay_shipping_service") == "FedExHomeDelivery"
        ]
        assert len(shipping_calls) == 1

    def test_fetches_tracking_for_fulfilled_order(self, frappe_mock, sample_ebay_order):
        """A FULFILLED order with no tracking in the payload triggers an API fetch."""
        frappe_mock.db.exists.return_value = None
        frappe_mock.db.get_single_value.return_value = "Test Company"
        sample_ebay_order["orderFulfillmentStatus"] = "FULFILLED"
        sample_ebay_order["fulfillmentStartInstructions"] = [
            {"shippingStep": {"shippingServiceCode": "USPSPriority"}}
        ]
        mock_so = MagicMock()
        mock_so.name = "SO-FUL"
        frappe_mock.get_doc.return_value = mock_so

        with patch(f"{MODULE}.get_or_create_customer", return_value="CUST-1"), \
             patch(f"{MODULE}.get_or_create_item", return_value="ITEM-1"), \
             patch(f"{MODULE}.get_shipping_account", return_value=None), \
             patch(f"{MODULE}.get_tax_account", return_value=None), \
             patch(f"{MODULE}.create_invoice_and_payment"), \
             patch(f"{MODULE}.fetch_tracking_for_order",
                   return_value={"tracking_number": "1Z999", "shipping_service": "UPS"}) as mock_fetch:
            sync_orders_mod.process_order(sample_ebay_order)

        mock_fetch.assert_called_once_with("12-34567-89012")
        tracking_calls = [
            c for c in frappe_mock.db.set_value.call_args_list
            if c.args[0] == "Sales Order"
            and isinstance(c.args[2], dict)
            and c.args[2].get("ebay_tracking_number") == "1Z999"
        ]
        assert len(tracking_calls) == 1

    def test_no_tracking_fetch_when_not_fulfilled(self, frappe_mock, sample_ebay_order):
        """An unshipped order must not make the extra tracking API call."""
        frappe_mock.db.exists.return_value = None
        frappe_mock.db.get_single_value.return_value = "Test Company"
        sample_ebay_order["orderFulfillmentStatus"] = "IN_PROGRESS"
        mock_so = MagicMock()
        mock_so.name = "SO-INP"
        frappe_mock.get_doc.return_value = mock_so

        with patch(f"{MODULE}.get_or_create_customer", return_value="CUST-1"), \
             patch(f"{MODULE}.get_or_create_item", return_value="ITEM-1"), \
             patch(f"{MODULE}.get_shipping_account", return_value=None), \
             patch(f"{MODULE}.get_tax_account", return_value=None), \
             patch(f"{MODULE}.create_invoice_and_payment"), \
             patch(f"{MODULE}.fetch_tracking_for_order") as mock_fetch:
            sync_orders_mod.process_order(sample_ebay_order)

        mock_fetch.assert_not_called()

    def test_stores_fulfillment_status_on_sales_order(self, frappe_mock, sample_ebay_order):
        """orderFulfillmentStatus is written to the SO even without shipping info."""
        frappe_mock.db.exists.return_value = None
        frappe_mock.db.get_single_value.return_value = "Test Company"
        sample_ebay_order["orderFulfillmentStatus"] = "IN_PROGRESS"
        sample_ebay_order.pop("fulfillmentStartInstructions", None)
        mock_so = MagicMock()
        mock_so.name = "SO-FS"
        frappe_mock.get_doc.return_value = mock_so

        with patch(f"{MODULE}.get_or_create_customer", return_value="CUST-1"), \
             patch(f"{MODULE}.get_or_create_item", return_value="ITEM-1"), \
             patch(f"{MODULE}.get_shipping_account", return_value=None), \
             patch(f"{MODULE}.get_tax_account", return_value=None), \
             patch(f"{MODULE}.create_invoice_and_payment"):
            sync_orders_mod.process_order(sample_ebay_order)

        status_calls = [
            c for c in frappe_mock.db.set_value.call_args_list
            if c.args[0] == "Sales Order"
            and isinstance(c.args[2], dict)
            and c.args[2].get("ebay_fulfillment_status") == "IN_PROGRESS"
        ]
        assert len(status_calls) == 1

    def test_no_set_value_when_no_shipping_info(self, frappe_mock, sample_ebay_order):
        frappe_mock.db.exists.return_value = None
        frappe_mock.db.get_single_value.return_value = "Test Company"
        sample_ebay_order.pop("fulfillmentStartInstructions", None)
        mock_so = MagicMock()
        mock_so.name = "SO-NONE"
        frappe_mock.get_doc.return_value = mock_so

        with patch(f"{MODULE}.get_or_create_customer", return_value="CUST-1"), \
             patch(f"{MODULE}.get_or_create_item", return_value="ITEM-1"), \
             patch(f"{MODULE}.get_shipping_account", return_value=None), \
             patch(f"{MODULE}.get_tax_account", return_value=None), \
             patch(f"{MODULE}.create_invoice_and_payment"):
            sync_orders_mod.process_order(sample_ebay_order)

        shipping_calls = [
            c for c in frappe_mock.db.set_value.call_args_list
            if c.args[0] == "Sales Order"
            and isinstance(c.args[2], dict)
            and ("ebay_shipping_service" in c.args[2] or "ebay_tracking_number" in c.args[2])
        ]
        assert shipping_calls == []
