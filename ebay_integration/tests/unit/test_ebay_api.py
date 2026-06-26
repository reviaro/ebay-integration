"""
Unit tests for ebay_integration.utils.ebay_api.eBayWrapper.

All HTTP calls are intercepted via ``unittest.mock.patch("requests.request")``
(or ``requests.post`` where the SUT calls it directly).  Frappe is provided by
the shared ``frappe_mock`` fixture from conftest.py; the ``ebay_wrapper``
helper fixture builds a fully-configured eBayWrapper on top of it.

Coverage:
- get_my_selling()   – pagination (1 / 2 / 3 pages), non-200, exception
- get_orders()       – pagination (1 / 2 pages), non-200
- _refresh_token()   – success, non-200, missing refresh_token
- _make_request()    – 401 → refresh+retry, 401 + refresh fails, 200 passthrough
"""

import pytest
from unittest.mock import MagicMock, patch, call

from ebay_integration.utils.ebay_api import eBayWrapper


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _mock_response(status_code: int, json_data: dict | None = None, text: str = "") -> MagicMock:
    """Build a lightweight mock HTTP response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    resp.text = text
    return resp


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def ebay_wrapper(frappe_mock):
    """
    Provides a fully-configured eBayWrapper instance backed by ``frappe_mock``.

    frappe.get_single("eBay Settings") returns a mock settings doc with:
    - client_id  = "test_client"
    - sandbox    = False
    - get_password("access_token")   → "test_token"
    - get_password("refresh_token")  → "test_refresh"
    - get_password("client_secret")  → "test_secret"
    """
    settings = MagicMock()
    settings.client_id = "test_client"
    settings.sandbox = False

    def _get_password(field):
        return {
            "access_token": "test_token",
            "refresh_token": "test_refresh",
            "client_secret": "test_secret",
        }.get(field, "")

    settings.get_password.side_effect = _get_password
    frappe_mock.get_single.return_value = settings

    wrapper = eBayWrapper()
    return wrapper


# ---------------------------------------------------------------------------
# get_my_selling() tests
# ---------------------------------------------------------------------------

class TestGetMySelling:
    """Tests for eBayWrapper.get_my_selling() pagination and error handling."""

    def test_single_page_returns_all_items(self, ebay_wrapper):
        """When total ≤ limit the loop exits after one request and returns every item."""
        items = [{"sku": f"ITEM-{i}"} for i in range(50)]
        response = _mock_response(200, {"inventoryItems": items, "total": 50})

        with patch("requests.request", return_value=response) as mock_req:
            result = ebay_wrapper.get_my_selling()

        assert len(result) == 50
        assert result == items
        mock_req.assert_called_once()

    def test_two_pages_returns_all_items(self, ebay_wrapper):
        """When total > limit a second request is made; both pages are combined."""
        page1 = [{"sku": f"ITEM-{i}"} for i in range(100)]
        page2 = [{"sku": f"ITEM-{i}"} for i in range(100, 150)]

        responses = [
            _mock_response(200, {"inventoryItems": page1, "total": 150}),
            _mock_response(200, {"inventoryItems": page2, "total": 150}),
        ]

        with patch("requests.request", side_effect=responses) as mock_req:
            result = ebay_wrapper.get_my_selling()

        assert len(result) == 150
        assert result == page1 + page2
        assert mock_req.call_count == 2

        # Verify offset progression
        first_params = mock_req.call_args_list[0].kwargs["params"]
        second_params = mock_req.call_args_list[1].kwargs["params"]
        assert first_params["offset"] == 0
        assert second_params["offset"] == 100

    def test_three_pages_returns_all_items(self, ebay_wrapper):
        """Three-page scenario: 250 total items across three 100-item pages."""
        page1 = [{"sku": f"ITEM-{i}"} for i in range(100)]
        page2 = [{"sku": f"ITEM-{i}"} for i in range(100, 200)]
        page3 = [{"sku": f"ITEM-{i}"} for i in range(200, 250)]

        responses = [
            _mock_response(200, {"inventoryItems": page1, "total": 250}),
            _mock_response(200, {"inventoryItems": page2, "total": 250}),
            _mock_response(200, {"inventoryItems": page3, "total": 250}),
        ]

        with patch("requests.request", side_effect=responses) as mock_req:
            result = ebay_wrapper.get_my_selling()

        assert len(result) == 250
        assert result == page1 + page2 + page3
        assert mock_req.call_count == 3

        offsets = [c.kwargs["params"]["offset"] for c in mock_req.call_args_list]
        assert offsets == [0, 100, 200]

    def test_non_200_response_returns_empty_list(self, ebay_wrapper):
        """A non-200 status on the first page logs an error and returns an empty list."""
        response = _mock_response(403, text="Forbidden")

        with patch("requests.request", return_value=response):
            result = ebay_wrapper.get_my_selling()

        assert result == []

    def test_non_200_after_first_page_returns_partial_items(self, ebay_wrapper):
        """
        If the first page succeeds but the second returns non-200,
        the method breaks and returns only items fetched so far.
        """
        page1 = [{"sku": f"ITEM-{i}"} for i in range(100)]

        responses = [
            _mock_response(200, {"inventoryItems": page1, "total": 250}),
            _mock_response(500, text="Internal Server Error"),
        ]

        with patch("requests.request", side_effect=responses):
            result = ebay_wrapper.get_my_selling()

        # Only the first page should be present
        assert result == page1

    def test_exception_during_request_returns_empty_list(self, ebay_wrapper):
        """An unexpected exception is caught; the method logs and returns []."""
        with patch("requests.request", side_effect=RuntimeError("network failure")):
            result = ebay_wrapper.get_my_selling()

        assert result == []


# ---------------------------------------------------------------------------
# get_orders() tests
# ---------------------------------------------------------------------------

class TestGetOrders:
    """Tests for eBayWrapper.get_orders() pagination and error handling."""

    def test_single_page_returns_all_orders(self, ebay_wrapper):
        """When total ≤ limit only one HTTP call is made."""
        orders = [{"orderId": f"ORD-{i}"} for i in range(100)]
        response = _mock_response(200, {"orders": orders, "total": 100})

        with patch("requests.request", return_value=response) as mock_req:
            result = ebay_wrapper.get_orders()

        assert len(result) == 100
        assert result == orders
        mock_req.assert_called_once()

    def test_two_pages_returns_all_orders(self, ebay_wrapper):
        """350 total orders across two pages (limit=200) produces 2 requests."""
        page1 = [{"orderId": f"ORD-{i}"} for i in range(200)]
        page2 = [{"orderId": f"ORD-{i}"} for i in range(200, 350)]

        responses = [
            _mock_response(200, {"orders": page1, "total": 350}),
            _mock_response(200, {"orders": page2, "total": 350}),
        ]

        with patch("requests.request", side_effect=responses) as mock_req:
            result = ebay_wrapper.get_orders()

        assert len(result) == 350
        assert result == page1 + page2
        assert mock_req.call_count == 2

        first_params = mock_req.call_args_list[0].kwargs["params"]
        second_params = mock_req.call_args_list[1].kwargs["params"]
        assert first_params["offset"] == 0
        assert second_params["offset"] == 200

    def test_non_200_response_returns_empty_list(self, ebay_wrapper):
        """A non-200 status logs an error and returns an empty list."""
        response = _mock_response(401, text="Unauthorized")

        with patch("requests.request", return_value=response):
            result = ebay_wrapper.get_orders()

        assert result == []

    def test_date_filter_is_included_in_params(self, ebay_wrapper):
        """The ``filter`` param is always present and references a date range."""
        orders = [{"orderId": "ORD-1"}]
        response = _mock_response(200, {"orders": orders, "total": 1})

        with patch("requests.request", return_value=response) as mock_req:
            ebay_wrapper.get_orders(days_back=7)

        params = mock_req.call_args.kwargs["params"]
        assert "filter" in params
        assert "creationdate:[" in params["filter"]


# ---------------------------------------------------------------------------
# _refresh_token() tests
# ---------------------------------------------------------------------------

class TestRefreshToken:
    """Tests for eBayWrapper._refresh_token()."""

    def test_successful_refresh_updates_token_and_db(self, ebay_wrapper, frappe_mock):
        """A 200 response with access_token updates self.access_token and calls db.set_value."""
        new_token = "new_access_token_xyz"
        resp = _mock_response(200, {"access_token": new_token})

        with patch("requests.post", return_value=resp):
            result = ebay_wrapper._refresh_token()

        assert result is True
        assert ebay_wrapper.access_token == new_token
        frappe_mock.db.set_value.assert_called_once_with(
            "eBay Settings", "eBay Settings", "access_token", new_token
        )
        frappe_mock.db.commit.assert_called()

    def test_non_200_response_logs_error_and_returns_false(self, ebay_wrapper, frappe_mock):
        """A non-200 token endpoint response causes _refresh_token to return False."""
        resp = _mock_response(400, text="Bad Request")

        with patch("requests.post", return_value=resp):
            result = ebay_wrapper._refresh_token()

        assert result is False
        # db.set_value must NOT have been called
        frappe_mock.db.set_value.assert_not_called()

    def test_missing_refresh_token_returns_false(self, ebay_wrapper, frappe_mock):
        """If get_password('refresh_token') returns falsy, _refresh_token returns False immediately."""
        # Override get_password so refresh_token is empty
        ebay_wrapper.settings.get_password.side_effect = lambda field: (
            "test_token" if field == "access_token" else ""
        )

        with patch("requests.post") as mock_post:
            result = ebay_wrapper._refresh_token()

        assert result is False
        mock_post.assert_not_called()

    def test_successful_refresh_uses_correct_endpoint_for_production(self, ebay_wrapper):
        """Production wrapper posts to api.ebay.com (not sandbox)."""
        resp = _mock_response(200, {"access_token": "new_token"})

        with patch("requests.post", return_value=resp) as mock_post:
            ebay_wrapper._refresh_token()

        posted_url = mock_post.call_args.args[0]
        assert "api.ebay.com" in posted_url
        assert "sandbox" not in posted_url

    def test_successful_refresh_uses_sandbox_endpoint_when_sandbox(self, frappe_mock):
        """Sandbox wrapper posts to api.sandbox.ebay.com."""
        settings = MagicMock()
        settings.client_id = "test_client"
        settings.sandbox = True
        settings.get_password.side_effect = lambda f: {
            "access_token": "test_token",
            "refresh_token": "test_refresh",
            "client_secret": "test_secret",
        }.get(f, "")
        frappe_mock.get_single.return_value = settings

        wrapper = eBayWrapper()
        resp = _mock_response(200, {"access_token": "new_sandbox_token"})

        with patch("requests.post", return_value=resp) as mock_post:
            wrapper._refresh_token()

        posted_url = mock_post.call_args.args[0]
        assert "sandbox.ebay.com" in posted_url


# ---------------------------------------------------------------------------
# _make_request() tests
# ---------------------------------------------------------------------------

class TestMakeRequest:
    """Tests for eBayWrapper._make_request() retry-on-401 logic."""

    def test_200_response_returned_directly(self, ebay_wrapper):
        """A 200 response is passed through with no retry."""
        resp = _mock_response(200, {"result": "ok"})

        with patch("requests.request", return_value=resp) as mock_req:
            result = ebay_wrapper._make_request("GET", "https://api.ebay.com/test")

        assert result is resp
        mock_req.assert_called_once()

    def test_401_triggers_refresh_and_retry(self, ebay_wrapper, frappe_mock):
        """On a 401 the method calls _refresh_token then retries the request once."""
        resp_401 = _mock_response(401, text="Unauthorized")
        resp_200 = _mock_response(200, {"result": "retried"})

        refresh_resp = _mock_response(200, {"access_token": "refreshed_token"})

        with patch("requests.request", side_effect=[resp_401, resp_200]) as mock_req, \
             patch("requests.post", return_value=refresh_resp):
            result = ebay_wrapper._make_request("GET", "https://api.ebay.com/test")

        assert result is resp_200
        assert mock_req.call_count == 2

    def test_401_when_refresh_fails_no_further_retry(self, ebay_wrapper):
        """
        When _refresh_token returns False (bad token endpoint response),
        _make_request does NOT issue a second HTTP call.
        """
        resp_401 = _mock_response(401, text="Unauthorized")
        refresh_resp = _mock_response(400, text="Bad Request")

        with patch("requests.request", return_value=resp_401) as mock_req, \
             patch("requests.post", return_value=refresh_resp):
            result = ebay_wrapper._make_request("GET", "https://api.ebay.com/test")

        assert result is resp_401
        # Only the initial request; no retry after failed refresh
        mock_req.assert_called_once()

    def test_retry_uses_updated_authorization_header(self, ebay_wrapper, frappe_mock):
        """After a successful refresh the retry uses the new Bearer token."""
        resp_401 = _mock_response(401)
        resp_200 = _mock_response(200)
        refresh_resp = _mock_response(200, {"access_token": "brand_new_token"})

        with patch("requests.request", side_effect=[resp_401, resp_200]) as mock_req, \
             patch("requests.post", return_value=refresh_resp):
            ebay_wrapper._make_request("GET", "https://api.ebay.com/test")

        # The second call must carry the updated token
        second_headers = mock_req.call_args_list[1].kwargs["headers"]
        assert second_headers["Authorization"] == "Bearer brand_new_token"

    def test_retry_on_401_disabled_returns_401_directly(self, ebay_wrapper):
        """When retry_on_401=False a 401 response is returned without any refresh attempt."""
        resp_401 = _mock_response(401)

        with patch("requests.request", return_value=resp_401) as mock_req, \
             patch("requests.post") as mock_post:
            result = ebay_wrapper._make_request(
                "GET", "https://api.ebay.com/test", retry_on_401=False
            )

        assert result is resp_401
        mock_req.assert_called_once()
        mock_post.assert_not_called()


# ---------------------------------------------------------------------------
# eBayWrapper.__init__ edge-cases
# ---------------------------------------------------------------------------

class TesteBayWrapperInit:
    """Tests for constructor validation via frappe.throw."""

    def test_throws_when_no_client_id(self, frappe_mock):
        """Missing client_id causes frappe.throw (RuntimeError in tests)."""
        settings = MagicMock()
        settings.client_id = None
        frappe_mock.get_single.return_value = settings

        with pytest.raises(RuntimeError, match="eBay Settings"):
            eBayWrapper()

    def test_throws_when_no_access_token(self, frappe_mock):
        """Empty access_token causes frappe.throw (RuntimeError in tests)."""
        settings = MagicMock()
        settings.client_id = "test_client"
        settings.get_password.return_value = ""  # all fields return ""
        frappe_mock.get_single.return_value = settings

        with pytest.raises(RuntimeError, match="No access token"):
            eBayWrapper()

    def test_production_base_url(self, frappe_mock):
        """sandbox=False sets base_url to production endpoint."""
        settings = MagicMock()
        settings.client_id = "test_client"
        settings.sandbox = False
        settings.get_password.side_effect = lambda f: "test_token" if f == "access_token" else ""
        frappe_mock.get_single.return_value = settings

        wrapper = eBayWrapper()
        assert wrapper.base_url == "https://api.ebay.com"

    def test_sandbox_base_url(self, frappe_mock):
        """sandbox=True sets base_url to sandbox endpoint."""
        settings = MagicMock()
        settings.client_id = "test_client"
        settings.sandbox = True
        settings.get_password.side_effect = lambda f: "test_token" if f == "access_token" else ""
        frappe_mock.get_single.return_value = settings

        wrapper = eBayWrapper()
        assert wrapper.base_url == "https://api.sandbox.ebay.com"
