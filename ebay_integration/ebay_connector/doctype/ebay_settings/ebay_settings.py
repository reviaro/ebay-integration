import frappe
from frappe.model.document import Document


class eBaySettings(Document):
	pass


@frappe.whitelist()
def get_authorize_url(doc_name):
	from urllib.parse import urlencode

	doc = frappe.get_doc("eBay Settings", doc_name)

	# OAuth scopes required for the integration:
	# - api_scope: Basic API access
	# - buy.browse: Browse API for price comparison feature
	# - sell.marketing.readonly: Read marketing/promotion data
	# - sell.inventory.readonly: Read inventory items
	# - sell.account.readonly: Read account settings
	# - sell.fulfillment.readonly: Read orders and fulfillment data
	# - sell.finances: Read financial transactions (refunds, payouts)
	scopes = " ".join([
		"https://api.ebay.com/oauth/api_scope",
		"https://api.ebay.com/oauth/api_scope/buy.browse",
		"https://api.ebay.com/oauth/api_scope/sell.marketing.readonly",
		"https://api.ebay.com/oauth/api_scope/sell.inventory.readonly",
		"https://api.ebay.com/oauth/api_scope/sell.account.readonly",
		"https://api.ebay.com/oauth/api_scope/sell.fulfillment.readonly",
		"https://api.ebay.com/oauth/api_scope/sell.finances"
	])

	base = "https://auth.sandbox.ebay.com/oauth2/authorize" if doc.sandbox else "https://auth.ebay.com/oauth2/authorize"

	params = {
		"client_id": doc.client_id,
		"redirect_uri": doc.ru_name,
		"response_type": "code",
		"scope": scopes,
		"prompt": "login"
	}

	url = f"{base}?{urlencode(params)}"
	return url


@frappe.whitelist()
def generate_token(doc_name):
	doc = frappe.get_doc("eBay Settings", doc_name)
	if not doc.auth_code:
		frappe.throw("Please enter Auth Code first")

	# Exchange code for token
	import requests
	import base64
	from urllib.parse import unquote

	base_url = "https://api.sandbox.ebay.com" if doc.sandbox else "https://api.ebay.com"
	endpoint = f"{base_url}/identity/v1/oauth2/token"

	# Use get_password() to retrieve actual password value from Password field
	client_secret = doc.get_password("client_secret")
	auth_header = base64.b64encode(f"{doc.client_id}:{client_secret}".encode()).decode()

	# URL-decode the auth code in case it was copied with encoding
	auth_code = unquote(doc.auth_code.strip())

	headers = {
		"Authorization": f"Basic {auth_header}",
		"Content-Type": "application/x-www-form-urlencoded"
	}

	data = {
		"grant_type": "authorization_code",
		"code": auth_code,
		"redirect_uri": doc.ru_name
	}

	try:
		resp = requests.post(endpoint, headers=headers, data=data)
		resp.raise_for_status()
		tokens = resp.json()

		doc.access_token = tokens.get("access_token")
		doc.refresh_token = tokens.get("refresh_token")
		doc.auth_code = ""  # Clear used auth code
		doc.save()

		frappe.msgprint("Token generated successfully!")

	except requests.exceptions.HTTPError as e:
		error_details = f"""
Failed to generate token.

Error: {str(e)}
Response: {resp.text if 'resp' in locals() else 'No response'}

Debug Info:
- Client ID: {doc.client_id[:10]}...
- RuName: {doc.ru_name}
- Auth Code (first 20 chars): {auth_code[:20]}...
- Endpoint: {endpoint}

Make sure you're using the Authorize button in ERPNext (not eBay's test sign in).
"""
		frappe.throw(error_details)
	except Exception as e:
		frappe.throw(f"Failed to generate token: {str(e)}")


@frappe.whitelist()
def manual_sync_orders():
	"""Manually trigger order sync from eBay Settings page"""
	try:
		from ebay_integration.utils.ebay_api import eBayWrapper
		from ebay_integration.utils.sync_orders import process_order, log_sync_result

		ebay = eBayWrapper()
		orders = ebay.get_orders(days_back=7)

		processed = 0
		for order_data in orders:
			if process_order(order_data):
				processed += 1

		log_sync_result("manual_sync_orders", "Success", f"Processed {processed} new orders out of {len(orders)} total")
		frappe.db.commit()
		return f"Synced {processed} new orders from {len(orders)} found in last 7 days"
	except Exception as e:
		frappe.log_error(message=str(e), title="Manual Sync Orders Error")
		return f"Error: {str(e)}"


@frappe.whitelist()
def manual_sync_inventory():
	"""Manually trigger inventory sync from eBay Settings page"""
	try:
		from ebay_integration.utils.sync_inventory import sync_inventory
		sync_inventory()
		frappe.db.commit()
		return "Inventory sync completed. Check eBay Log for details."
	except Exception as e:
		frappe.log_error(message=str(e), title="Manual Sync Inventory Error")
		return f"Error: {str(e)}"


@frappe.whitelist()
def manual_import_historical(days_back=90):
	"""Manually import historical orders from eBay Settings page"""
	try:
		from ebay_integration.utils.sync_orders import import_historical_orders
		days_back = int(days_back)
		result = import_historical_orders(days_back=days_back)
		if result:
			return f"Imported {result['processed']} new orders, skipped {result['skipped']} existing, {result['errors']} errors"
		return "Import completed. Check eBay Log for details."
	except Exception as e:
		frappe.log_error(message=str(e), title="Manual Import Historical Error")
		return f"Error: {str(e)}"


@frappe.whitelist()
def manual_update_orders(days_back=90):
	"""Update existing orders with tax/shipping data from eBay API"""
	try:
		from ebay_integration.utils.sync_orders import update_existing_orders
		days_back = int(days_back)
		result = update_existing_orders(days_back=days_back)
		if result:
			return result.get('message', 'Update completed')
		return "Update completed. Check eBay Log for details."
	except Exception as e:
		frappe.log_error(message=str(e), title="Manual Update Orders Error")
		return f"Error: {str(e)}"


@frappe.whitelist()
def manual_sync_cancellations(days_back=30):
	"""Manually trigger cancellation and refund sync from eBay Settings page.

	This function fetches cancellation requests and refund transactions from eBay
	and creates corresponding eBay Refund records in ERPNext. If auto-processing
	is enabled, it will also create Credit Notes and Return Delivery Notes.

	Args:
		days_back: Number of days to look back for cancellations/refunds (default: 30)

	Returns:
		str: Summary message of sync results
	"""
	try:
		from ebay_integration.utils.sync_cancellations import sync_cancellations_and_refunds
		days_back = int(days_back)
		result = sync_cancellations_and_refunds(days_back=days_back)
		if result:
			return (
				f"Processed {result.get('cancellations_processed', 0)} cancellations, "
				f"{result.get('refunds_processed', 0)} refunds. "
				f"Created {result.get('credit_notes_created', 0)} Credit Notes, "
				f"{result.get('return_dns_created', 0)} Return Delivery Notes. "
				f"Errors: {result.get('errors', 0)}"
			)
		return "Sync completed. Check eBay Log for details."
	except Exception as e:
		frappe.log_error(message=str(e), title="Manual Sync Cancellations Error")
		return f"Error: {str(e)}"
