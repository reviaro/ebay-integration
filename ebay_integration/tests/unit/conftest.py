"""
Unit test configuration.

frappe is NOT installed on the host — every unit test patches it via sys.modules
at collection time (module-level code here, before any app imports).

Integration tests (marked @pytest.mark.integration) live in tests/integration/
and use a real frappe instance; this file does NOT apply to them.
"""

import sys
import datetime
from unittest.mock import MagicMock

# ---------------------------------------------------------------------------
# Build a realistic frappe mock and inject it before any app module imports.
# ---------------------------------------------------------------------------

def _make_frappe_mock():
    frappe = MagicMock(name="frappe")

    utils = MagicMock(name="frappe.utils")
    utils.nowdate.return_value = "2026-04-25"
    utils.nowtime.return_value = "00:00:00"
    utils.now_datetime.return_value = datetime.datetime(2026, 4, 25, 0, 0, 0)
    utils.getdate.side_effect = lambda s: datetime.date.fromisoformat(str(s)[:10]) if s else datetime.date.today()
    utils.flt.side_effect = lambda x, *_: float(x) if x not in (None, "") else 0.0
    frappe.utils = utils

    model = MagicMock(name="frappe.model")
    mapper = MagicMock(name="frappe.model.mapper")
    model.mapper = mapper
    frappe.model = model

    custom = MagicMock(name="frappe.custom")
    frappe.custom = custom

    db = MagicMock(name="frappe.db")
    db.commit.return_value = None
    frappe.db = db

    def _default_get_doc(data=None, *args, **kwargs):
        doc = MagicMock()
        doc.insert.return_value = None
        doc.submit.return_value = None
        doc.save.return_value = None
        if isinstance(data, dict):
            for k, v in data.items():
                setattr(doc, k, v)
        return doc

    frappe.get_doc.side_effect = _default_get_doc
    frappe.new_doc.side_effect = _default_get_doc

    frappe.throw.side_effect = lambda msg, *a, **kw: (_ for _ in ()).throw(RuntimeError(msg))

    return frappe


_FRAPPE_MOCK = _make_frappe_mock()

sys.modules["frappe"] = _FRAPPE_MOCK
sys.modules["frappe.utils"] = _FRAPPE_MOCK.utils
sys.modules["frappe.model"] = _FRAPPE_MOCK.model
sys.modules["frappe.model.mapper"] = _FRAPPE_MOCK.model.mapper
sys.modules["frappe.custom"] = _FRAPPE_MOCK.custom
sys.modules["frappe.custom.doctype"] = MagicMock()
sys.modules["frappe.custom.doctype.custom_field"] = MagicMock()
sys.modules["frappe.custom.doctype.custom_field.custom_field"] = MagicMock()

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

import pytest


@pytest.fixture
def frappe_mock(monkeypatch):
    """
    Per-test fixture that resets the shared frappe mock's db calls so tests
    don't bleed state into each other.
    """
    import frappe as _f

    _f.db.get_single_value = MagicMock(return_value=None)
    _f.db.get_value = MagicMock(return_value=None)
    _f.db.exists = MagicMock(return_value=None)
    _f.db.sql = MagicMock(return_value=[])
    _f.db.commit = MagicMock(return_value=None)
    _f.db.set_value = MagicMock(return_value=None)

    _f.get_doc.side_effect = None
    _f.get_doc.return_value = MagicMock(
        insert=MagicMock(), submit=MagicMock(), save=MagicMock(),
        items=[], taxes=[]
    )
    _f.get_all.return_value = []
    _f.get_single.return_value = MagicMock()
    _f.log_error.return_value = None

    return _f


@pytest.fixture
def sample_ebay_order():
    """Minimal eBay Fulfillment API order payload."""
    return {
        "orderId": "12-34567-89012",
        "orderPaymentStatus": "PAID",
        "creationDate": "2026-04-20T10:00:00.000Z",
        "buyer": {
            "username": "test_buyer",
            "buyerRegistrationAddress": {"email": "buyer@example.com"}
        },
        "pricingSummary": {
            "priceSubtotal": {"value": "50.00", "currency": "USD"},
            "deliveryCost": {"value": "5.00"},
            "tax": {"value": "3.50"},
            "total": {"value": "58.50"}
        },
        "lineItems": [
            {
                "sku": "PART-001",
                "title": "Used Engine Part",
                "quantity": 1,
                "legacyItemId": "111222333444",
                "lineItemCost": {"value": "50.00"}
            }
        ]
    }


@pytest.fixture
def sample_inventory_items():
    """Two eBay inventory items as returned by get_my_selling()."""
    return [
        {
            "sku": "PART-001",
            "availability": {
                "shipToLocationAvailability": {"quantity": 3}
            }
        },
        {
            "sku": "PART-002",
            "availability": {
                "shipToLocationAvailability": {"quantity": 0}
            }
        }
    ]
