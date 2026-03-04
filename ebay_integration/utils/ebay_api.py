import frappe
import requests
import base64
from datetime import datetime, timedelta

class eBayWrapper:
	"""
	Wrapper for eBay API using REST APIs (Fulfillment, Inventory).
	SAFETY NOTICE: This wrapper is designed for ONE-WAY SYNC (eBay -> ERPNext).
	Do NOT implement methods that write data back to eBay.
	"""
	def __init__(self):
		self.settings = frappe.get_single("eBay Settings")
		if not self.settings.client_id:
			frappe.throw("Please configure eBay Settings first.")

		self.access_token = self.settings.get_password("access_token")
		if not self.access_token:
			frappe.throw("No access token found. Please authorize eBay first.")

		self.sandbox = self.settings.sandbox
		self.base_url = "https://api.sandbox.ebay.com" if self.sandbox else "https://api.ebay.com"

	def _get_headers(self):
		return {
			"Authorization": f"Bearer {self.access_token}",
			"Content-Type": "application/json",
			"Accept": "application/json"
		}

	def _refresh_token(self):
		"""Refresh the access token using refresh token"""
		try:
			refresh_token = self.settings.get_password("refresh_token")
			if not refresh_token:
				return False

			client_id = self.settings.client_id
			client_secret = self.settings.get_password("client_secret")
			auth_header = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()

			token_url = "https://api.sandbox.ebay.com/identity/v1/oauth2/token" if self.sandbox else "https://api.ebay.com/identity/v1/oauth2/token"

			resp = requests.post(
				token_url,
				headers={
					"Authorization": f"Basic {auth_header}",
					"Content-Type": "application/x-www-form-urlencoded"
				},
				data={
					"grant_type": "refresh_token",
					"refresh_token": refresh_token
				}
			)

			if resp.status_code == 200:
				tokens = resp.json()
				new_access_token = tokens.get("access_token")
				if new_access_token:
					# Update in database
					frappe.db.set_value("eBay Settings", "eBay Settings", "access_token", new_access_token)
					frappe.db.commit()
					# Update in this instance
					self.access_token = new_access_token
					self.log_info("token_refresh", "Access token refreshed successfully")
					return True

			self.log_error("token_refresh", f"Failed to refresh: {resp.status_code} - {resp.text[:200]}")
			return False

		except Exception as e:
			self.log_error("token_refresh", str(e))
			return False

	def _make_request(self, method, url, params=None, retry_on_401=True):
		"""Make API request with automatic token refresh on 401"""
		response = requests.request(method, url, headers=self._get_headers(), params=params)

		if response.status_code == 401 and retry_on_401:
			# Token expired, try to refresh
			if self._refresh_token():
				# Retry with new token
				response = requests.request(method, url, headers=self._get_headers(), params=params)

		return response

	def log_info(self, method, message):
		"""Log info message"""
		try:
			frappe.get_doc({
				"doctype": "eBay Log",
				"method": method,
				"status": "Success",
				"message": str(message)[:140]
			}).insert(ignore_permissions=True)
			frappe.db.commit()
		except Exception:
			pass

	def get_orders(self, days_back=1):
		"""Fetch orders from eBay using Fulfillment API (with auto token refresh)"""
		try:
			# Calculate time range
			create_time_from = datetime.utcnow() - timedelta(days=days_back)
			date_filter = create_time_from.strftime("%Y-%m-%dT%H:%M:%S.000Z")

			# Use Fulfillment API
			url = f"{self.base_url}/sell/fulfillment/v1/order"
			params = {
				"filter": f"creationdate:[{date_filter}..]",
				"limit": 200
			}

			response = self._make_request("GET", url, params=params)

			if response.status_code == 200:
				data = response.json()
				orders = data.get("orders", [])
				return orders
			else:
				self.log_error("get_orders", f"Status {response.status_code}: {response.text}")
				return []

		except Exception as e:
			self.log_error("get_orders", str(e))
			return []

	def get_order(self, order_id):
		"""Fetch a single order by ID with full cancellation/refund details.

		Args:
			order_id: The eBay order ID (e.g., '04-12345-67890')

		Returns:
			dict: Full order data including cancellation status, or None if not found.
			The response includes:
			- orderFulfillmentStatus: FULFILLED, NOT_STARTED, IN_PROGRESS
			- orderPaymentStatus: PAID, PENDING, FAILED
			- cancelStatus: Contains cancellation request details if any
			- refunds: List of refund transactions (if fieldGroups=TAX_BREAKDOWN used)
		"""
		try:
			url = f"{self.base_url}/sell/fulfillment/v1/order/{order_id}"

			# Request additional field groups for complete data
			params = {
				"fieldGroups": "TAX_BREAKDOWN"
			}

			response = self._make_request("GET", url, params=params)

			if response.status_code == 200:
				return response.json()
			elif response.status_code == 404:
				self.log_info("get_order", f"Order {order_id} not found")
				return None
			else:
				self.log_error("get_order", f"Status {response.status_code}: {response.text[:200]}")
				return None

		except Exception as e:
			self.log_error("get_order", str(e))
			return None

	def get_refund_transactions(self, days_back=30):
		"""Fetch refund transactions from eBay Finances API.

		This method retrieves all REFUND type transactions from the Finances API,
		which provides detailed information about refunds issued to buyers.

		Args:
			days_back: Number of days to look back for refund transactions (default: 30)

		Returns:
			list: List of refund transaction dictionaries containing:
			- transactionId: Unique ID for the refund transaction
			- orderId: Associated eBay order ID
			- transactionType: Always 'REFUND' for these results
			- transactionStatus: FUNDS_AVAILABLE_FOR_PAYOUT, PAYOUT, etc.
			- amount: Refund amount with value and currency
			- transactionDate: When the refund was processed
			- buyer: Buyer information
			- references: Related order/transaction references

		Note:
			Requires the sell.finances scope in OAuth authorization.
			Rate limit: 20 calls per second per user.
		"""
		try:
			# Calculate date range
			transaction_date_from = datetime.utcnow() - timedelta(days=days_back)
			date_filter = transaction_date_from.strftime("%Y-%m-%dT%H:%M:%S.000Z")

			url = f"{self.base_url}/sell/finances/v1/transaction"

			all_transactions = []
			offset = 0
			limit = 100

			while True:
				params = {
					"filter": f"transactionType:{{REFUND}},transactionDate:[{date_filter}..]",
					"limit": limit,
					"offset": offset
				}

				response = self._make_request("GET", url, params=params)

				if response.status_code == 200:
					data = response.json()
					transactions = data.get("transactions", [])
					all_transactions.extend(transactions)

					# Check if there are more pages
					total = data.get("total", 0)
					if offset + limit >= total:
						break
					offset += limit
				else:
					self.log_error("get_refund_transactions",
								   f"Status {response.status_code}: {response.text[:200]}")
					break

			self.log_info("get_refund_transactions",
						  f"Retrieved {len(all_transactions)} refund transactions")
			return all_transactions

		except Exception as e:
			self.log_error("get_refund_transactions", str(e))
			return []

	def get_cancellation_requests(self, days_back=30):
		"""Fetch orders with active cancellation requests.

		Queries the Fulfillment API for orders that have cancellation requests
		in various states (REQUESTED, APPROVED, REJECTED).

		Args:
			days_back: Number of days to look back (default: 30)

		Returns:
			list: Orders with cancelStatus containing cancellation details
		"""
		try:
			# Calculate time range
			create_time_from = datetime.utcnow() - timedelta(days=days_back)
			date_filter = create_time_from.strftime("%Y-%m-%dT%H:%M:%S.000Z")

			url = f"{self.base_url}/sell/fulfillment/v1/order"

			all_cancelled_orders = []
			offset = 0
			limit = 200

			while True:
				# Filter for orders with any cancellation activity
				params = {
					"filter": f"creationdate:[{date_filter}..]",
					"limit": limit,
					"offset": offset
				}

				response = self._make_request("GET", url, params=params)

				if response.status_code == 200:
					data = response.json()
					orders = data.get("orders", [])

					# Filter to orders that have cancellation info
					for order in orders:
						cancel_status = order.get("cancelStatus", {})
						# Check if there's any cancellation activity
						if cancel_status.get("cancelState") or cancel_status.get("cancelRequests"):
							all_cancelled_orders.append(order)

					# Check for pagination
					total = data.get("total", 0)
					if offset + limit >= total:
						break
					offset += limit
				else:
					self.log_error("get_cancellation_requests",
								   f"Status {response.status_code}: {response.text[:200]}")
					break

			self.log_info("get_cancellation_requests",
						  f"Found {len(all_cancelled_orders)} orders with cancellation activity")
			return all_cancelled_orders

		except Exception as e:
			self.log_error("get_cancellation_requests", str(e))
			return []

	def get_my_selling(self):
		"""Fetch active inventory items using Inventory API (with auto token refresh)"""
		try:
			url = f"{self.base_url}/sell/inventory/v1/inventory_item"
			params = {
				"limit": 100
			}

			response = self._make_request("GET", url, params=params)

			if response.status_code == 200:
				data = response.json()
				items = data.get("inventoryItems", [])
				return items
			else:
				self.log_error("get_my_selling", f"Status {response.status_code}: {response.text}")
				return []

		except Exception as e:
			self.log_error("get_my_selling", str(e))
			return []

	def log_error(self, method, result):
		try:
			frappe.get_doc({
				"doctype": "eBay Log",
				"method": method,
				"status": "Error",
				"message": str(result)[:140],
				"details": str(result)
			}).insert(ignore_permissions=True)
			frappe.db.commit()
		except Exception:
			# If logging fails, just print
			print(f"eBay API Error - {method}: {result}")

	def search_similar_items(self, keywords, condition="USED", limit=50,
						   category_ids=None, min_price=None, max_price=None):
		"""Search eBay for similar items using Browse API

		Args:
			keywords: Search keywords
			condition: Item condition filter (USED, NEW, etc.)
			limit: Maximum number of results to return
			category_ids: eBay category ID(s) to filter results (e.g., "6028" for Auto Parts)
			min_price: Minimum price filter (helps exclude cheap accessories)
			max_price: Maximum price filter

		Returns:
			dict with 'items' list and 'total' count
		"""
		try:
			url = f"{self.base_url}/buy/browse/v1/item_summary/search"

			# Build filter string
			filters = [f"conditions:{{{condition}}}"]

			# Add price range filter if specified
			# Format: price:[min..max] or price:[min..] or price:[..max]
			if min_price is not None or max_price is not None:
				min_val = str(int(min_price)) if min_price else ""
				max_val = str(int(max_price)) if max_price else ""
				filters.append(f"price:[{min_val}..{max_val}]")

			params = {
				"q": keywords,
				"filter": ",".join(filters),
				"limit": limit,
				"sort": "price"
			}

			# Add category filter if specified
			# Category 6028 = "Auto Parts & Accessories" (excludes tools, manuals, etc.)
			if category_ids:
				params["category_ids"] = category_ids

			response = self._make_request("GET", url, params=params)

			if response.status_code == 200:
				data = response.json()
				items = data.get("itemSummaries", [])
				return {
					"items": items,
					"total": data.get("total", 0)
				}
			else:
				self.log_error("search_similar_items", f"Status {response.status_code}: {response.text[:200]}")
				return {"items": [], "total": 0}

		except Exception as e:
			self.log_error("search_similar_items", str(e))
			return {"items": [], "total": 0}
