import frappe
from frappe.utils import getdate, nowdate, flt


def sync_orders():
	"""Main sync function - called by scheduler daily"""
	# Check if sync is enabled
	if not frappe.db.get_single_value("eBay Settings", "sync_enabled"):
		return

	try:
		from ebay_integration.utils.ebay_api import eBayWrapper
		ebay = eBayWrapper()
		orders = ebay.get_orders(days_back=1)

		processed = 0
		for order_data in orders:
			if process_order(order_data):
				processed += 1

		# Log success
		log_sync_result("sync_orders", "Success", f"Processed {processed} new orders out of {len(orders)} total")

	except Exception as e:
		log_sync_result("sync_orders", "Error", str(e))
		frappe.log_error(message=str(e), title="eBay Sync Orders Error")


def import_historical_orders(days_back=400):
	"""Import historical orders from eBay - call manually to backfill orders"""
	if not frappe.db.get_single_value("eBay Settings", "sync_enabled"):
		print("Sync not enabled in eBay Settings")
		return

	try:
		print(f"Fetching orders from last {days_back} days...")
		from ebay_integration.utils.ebay_api import eBayWrapper
		ebay = eBayWrapper()
		orders = ebay.get_orders(days_back=days_back)
		print(f"Found {len(orders)} orders from eBay")

		processed = 0
		skipped = 0
		errors = 0

		for i, order_data in enumerate(orders):
			try:
				if process_order(order_data):
					processed += 1
					print(f"[{i+1}/{len(orders)}] Imported order {order_data.get('orderId', 'unknown')}")
				else:
					skipped += 1
			except Exception as e:
				errors += 1
				print(f"[{i+1}/{len(orders)}] Error: {e}")

		result = f"Imported {processed} new orders, skipped {skipped} (already exist), {errors} errors"
		print(result)
		log_sync_result("import_historical_orders", "Success", result)
		frappe.db.commit()
		return {"processed": processed, "skipped": skipped, "errors": errors}

	except Exception as e:
		print(f"Error: {e}")
		log_sync_result("import_historical_orders", "Error", str(e))
		frappe.log_error(message=str(e), title="eBay Import Historical Orders Error")


def update_existing_orders(days_back=90):
	"""Update existing Sales Orders with tax/shipping data from eBay API.
	Use this to add financial data to orders that were imported before the tax feature was added.
	"""
	if not frappe.db.get_single_value("eBay Settings", "sync_enabled"):
		return {"updated": 0, "skipped": 0, "errors": 0, "message": "Sync not enabled"}

	try:
		from ebay_integration.utils.ebay_api import eBayWrapper
		ebay = eBayWrapper()
		orders = ebay.get_orders(days_back=days_back)

		company = frappe.db.get_single_value("eBay Settings", "default_company")
		updated = 0
		skipped = 0
		errors = 0

		for order_data in orders:
			ebay_order_id = order_data.get('orderId')
			if not ebay_order_id:
				continue

			# Find existing Sales Order
			so_name = frappe.db.get_value("Sales Order", {"po_no": ebay_order_id}, "name")
			if not so_name:
				skipped += 1
				continue

			try:
				so = frappe.get_doc("Sales Order", so_name)

				# Skip if already has taxes
				if so.taxes and len(so.taxes) > 0:
					skipped += 1
					continue

				# Skip if submitted (can't modify)
				if so.docstatus == 1:
					# Need to amend - or just skip for now
					skipped += 1
					continue

				# Extract financial data
				pricing_summary = order_data.get('pricingSummary', {})

				shipping_info = pricing_summary.get('deliveryCost', {})
				shipping_cost = flt(shipping_info.get('value', 0))

				tax_info = pricing_summary.get('tax', {})
				ebay_tax = flt(tax_info.get('value', 0))

				if shipping_cost == 0 and ebay_tax == 0:
					skipped += 1
					continue

				# Add taxes
				if shipping_cost > 0:
					shipping_account = get_shipping_account(company)
					if shipping_account:
						so.append("taxes", {
							"charge_type": "Actual",
							"account_head": shipping_account,
							"description": "eBay Shipping",
							"tax_amount": shipping_cost
						})

				if ebay_tax > 0:
					tax_account = get_tax_account(company)
					if tax_account:
						so.append("taxes", {
							"charge_type": "Actual",
							"account_head": tax_account,
							"description": "eBay Collected Tax (Sales Tax/VAT)",
							"tax_amount": ebay_tax
						})

				so.save(ignore_permissions=True)
				updated += 1

			except Exception as e:
				errors += 1
				frappe.log_error(f"Error updating {ebay_order_id}: {e}", "eBay Update Orders")

		frappe.db.commit()
		result = f"Updated {updated} orders, skipped {skipped}, errors {errors}"
		log_sync_result("update_existing_orders", "Success", result)
		return {"updated": updated, "skipped": skipped, "errors": errors, "message": result}

	except Exception as e:
		log_sync_result("update_existing_orders", "Error", str(e))
		frappe.log_error(message=str(e), title="eBay Update Orders Error")
		return {"updated": 0, "skipped": 0, "errors": 1, "message": str(e)}


def process_order(order_data):
	"""Process a single order from eBay Fulfillment API with full financial data"""
	# Fulfillment API uses 'orderId' (not 'OrderID')
	ebay_order_id = order_data.get('orderId')

	if not ebay_order_id:
		return False

	# Check if order exists
	if frappe.db.exists("Sales Order", {"po_no": ebay_order_id}):
		return False

	# Customer
	customer = get_or_create_customer(order_data)

	# Extract financial summary from Fulfillment API
	pricing_summary = order_data.get('pricingSummary', {})

	# Item subtotal (before shipping/tax)
	subtotal_info = pricing_summary.get('priceSubtotal', {})
	subtotal = flt(subtotal_info.get('value', 0))
	currency = subtotal_info.get('currency', 'USD')

	# Shipping cost
	shipping_info = pricing_summary.get('deliveryCost', {})
	shipping_cost = flt(shipping_info.get('value', 0))

	# eBay collected tax (sales tax / VAT that eBay collects and remits)
	tax_info = pricing_summary.get('tax', {})
	ebay_tax = flt(tax_info.get('value', 0))

	# Total amount
	total_info = pricing_summary.get('total', {})
	total_amount = flt(total_info.get('value', 0))

	# Items - Fulfillment API uses 'lineItems'
	items = []
	line_items = order_data.get('lineItems', [])

	for line_item in line_items:
		sku = line_item.get('sku')
		title = line_item.get('title', 'eBay Item')
		qty = float(line_item.get('quantity', 1))

		# Use eBay item ID as fallback if no SKU
		ebay_item_id = line_item.get('legacyItemId') or line_item.get('lineItemId')

		# Get variation details if present
		variation_aspects = line_item.get('variationAspects', [])
		variation_details = None
		if variation_aspects:
			variation_parts = [f"{v.get('name')}: {v.get('value')}" for v in variation_aspects if v.get('name') and v.get('value')]
			if variation_parts:
				variation_details = ", ".join(variation_parts)

		# Price from lineItemCost
		price_info = line_item.get('lineItemCost', {})
		price = float(price_info.get('value', 0))

		item_code = get_or_create_item(sku, title, price, ebay_item_id)

		# Build item description showing eBay title and variation
		item_description = title
		if variation_details:
			item_description = f"{title} | {variation_details}"

		items.append({
			"item_code": item_code,
			"qty": qty,
			"rate": price,
			"delivery_date": nowdate(),
			"description": item_description,
			"ebay_item_title": title,
			"ebay_item_number": str(ebay_item_id) if ebay_item_id else None,
			"ebay_variation_details": variation_details
		})

	if not items:
		return False

	# Parse creation date
	creation_date = order_data.get('creationDate', '')
	if creation_date:
		# Format: 2024-01-15T10:30:00.000Z
		try:
			creation_date = getdate(creation_date[:10])
		except Exception:
			creation_date = nowdate()
	else:
		creation_date = nowdate()

	# Get company
	company = frappe.db.get_single_value("eBay Settings", "default_company")

	# Build taxes and charges list
	taxes = []

	# Add shipping if present
	if shipping_cost > 0:
		shipping_account = get_shipping_account(company)
		if shipping_account:
			taxes.append({
				"charge_type": "Actual",
				"account_head": shipping_account,
				"description": "eBay Shipping",
				"tax_amount": shipping_cost
			})

	# Add eBay-collected tax if present
	if ebay_tax > 0:
		tax_account = get_tax_account(company)
		if tax_account:
			taxes.append({
				"charge_type": "Actual",
				"account_head": tax_account,
				"description": "eBay Collected Tax (Sales Tax/VAT)",
				"tax_amount": ebay_tax
			})

	# Create Sales Order
	so_data = {
		"doctype": "Sales Order",
		"customer": customer,
		"po_no": ebay_order_id,
		"transaction_date": creation_date,
		"delivery_date": nowdate(),
		"company": company,
		"currency": currency,
		"items": items
	}

	if taxes:
		so_data["taxes"] = taxes

	so = frappe.get_doc(so_data)
	so.insert(ignore_permissions=True)
	so.submit()

	frappe.db.commit()

	# Handle Payment if Paid
	payment_status = order_data.get('orderPaymentStatus', '')
	if payment_status == 'PAID':
		create_invoice_and_payment(so, order_data)

	return True


def get_shipping_account(company):
	"""Get or create shipping income account"""
	# Try configured account first
	configured_account = frappe.db.get_single_value("eBay Settings", "shipping_account")
	if configured_account:
		return configured_account

	# Try to find existing shipping account
	account = frappe.db.get_value("Account", {
		"account_name": "eBay Shipping Income",
		"company": company,
		"is_group": 0
	}, "name")

	if account:
		return account

	# Try generic shipping account
	account = frappe.db.get_value("Account", {
		"account_name": ["like", "%Shipping%"],
		"company": company,
		"root_type": "Income",
		"is_group": 0
	}, "name")

	if account:
		return account

	# Fall back to default income account
	return frappe.db.get_value("Company", company, "default_income_account")


def get_tax_account(company):
	"""Get or create eBay tax liability account"""
	# Try configured account first
	configured_account = frappe.db.get_single_value("eBay Settings", "tax_account_head")
	if configured_account:
		return configured_account

	# Try to find existing eBay tax account
	account = frappe.db.get_value("Account", {
		"account_name": "eBay Collected Tax",
		"company": company,
		"is_group": 0
	}, "name")

	if account:
		return account

	# Try generic tax payable account
	account = frappe.db.get_value("Account", {
		"account_type": "Tax",
		"company": company,
		"is_group": 0
	}, "name")

	if account:
		return account

	# Fall back to default expense account (not ideal but prevents errors)
	return frappe.db.get_value("Company", company, "default_expense_account")


def get_or_create_customer(order_data):
	"""Get or create customer from Fulfillment API order data"""
	buyer_info = order_data.get('buyer', {})
	buyer_username = buyer_info.get('username', 'eBay Buyer')

	# Try to get email from buyer registration address
	email = None
	reg_address = buyer_info.get('buyerRegistrationAddress', {})
	if reg_address:
		email = reg_address.get('email')

	if not email:
		email = f"{buyer_username}@ebay.placeholder.com"

	existing = frappe.db.get_value("Customer", {"email_id": email}, "name")
	if existing:
		return existing

	customer = frappe.get_doc({
		"doctype": "Customer",
		"customer_name": buyer_username,
		"customer_type": "Individual",
		"customer_group": frappe.db.get_single_value("eBay Settings", "default_customer_group") or "All Customer Groups",
		"email_id": email
	})
	customer.insert(ignore_permissions=True)
	return customer.name


def get_or_create_item(sku, title, price, ebay_item_id=None):
	"""Get or create item from SKU or eBay Item ID.

	When no SKU is provided, uses eBay item ID as the item code.
	The item_name is always set to the actual eBay listing title for clarity.
	"""
	# Determine item code: prefer SKU, then eBay item ID, then generate from title
	if sku:
		item_code = str(sku)[:140]
	elif ebay_item_id:
		item_code = f"EBAY-{ebay_item_id}"[:140]
	elif title:
		# Generate a unique code from title + hash for truly SKU-less items
		import hashlib
		title_hash = hashlib.md5(title.encode()).hexdigest()[:8]
		item_code = f"EBAY-{title[:50].replace(' ', '-').upper()}-{title_hash}"[:140]
	else:
		# Last resort fallback with timestamp
		import time
		item_code = f"EBAY-ITEM-{int(time.time())}"

	# Check if item exists
	if frappe.db.exists("Item", item_code):
		return item_code

	# Create new item with the actual eBay title as the item_name
	item_name = title[:140] if title else item_code

	item = frappe.get_doc({
		"doctype": "Item",
		"item_code": item_code,
		"item_name": item_name,
		"item_group": "All Item Groups",
		"stock_uom": "Nos",
		"is_stock_item": 1,
		"valuation_rate": price or 0.01,
		"standard_rate": price or 0,
		"description": title
	})

	# Set eBay item number custom field if it exists
	if ebay_item_id and hasattr(item, 'ebay_item_number'):
		item.ebay_item_number = str(ebay_item_id)

	item.insert(ignore_permissions=True)
	return item.name


def create_invoice_and_payment(so, order_data):
	"""
	Create Delivery Note, Sales Invoice, and Payment Entry for a paid order.

	Document flow: Sales Order → Delivery Note → Sales Invoice → Payment Entry
	- Delivery Note: Reduces stock from warehouse
	- Sales Invoice: Creates accounting entries (fees/taxes appear in accounting)
	- Payment Entry: Records payment received
	"""
	from frappe.model.mapper import get_mapped_doc

	try:
		company = so.company
		ebay_order_id = order_data.get('orderId')

		# ============================================
		# STEP 1: CREATE DELIVERY NOTE (reduces stock)
		# ============================================

		# Get default warehouse from eBay Settings or company
		default_warehouse = frappe.db.get_single_value("eBay Settings", "default_warehouse")
		if not default_warehouse:
			default_warehouse = frappe.db.get_value("Stock Settings", None, "default_warehouse")
		if not default_warehouse:
			default_warehouse = frappe.db.get_value("Warehouse", {"company": company, "is_group": 0}, "name")

		dn = get_mapped_doc("Sales Order", so.name, {
			"Sales Order": {
				"doctype": "Delivery Note",
				"validation": {"docstatus": ["=", 1]}
			},
			"Sales Order Item": {
				"doctype": "Delivery Note Item",
				"field_map": {
					"name": "so_detail",
					"parent": "against_sales_order",
					"rate": "rate",
					"qty": "qty"
				}
			},
			"Sales Taxes and Charges": {
				"doctype": "Sales Taxes and Charges"
			}
		}, ignore_permissions=True)

		# Set warehouse for stock reduction
		if default_warehouse:
			for item in dn.items:
				if not item.warehouse:
					item.warehouse = default_warehouse

		try:
			dn.insert(ignore_permissions=True)
			dn.submit()
		except Exception as e:
			# If stock not available, log but continue with invoice
			frappe.log_error(
				message=f"Delivery Note error for {ebay_order_id}: {e}",
				title="eBay Sync - DN Error"
			)
			dn = None

		# ============================================
		# STEP 2: CREATE SALES INVOICE (accounting)
		# ============================================

		if dn:
			# Create Sales Invoice from Delivery Note (proper linking)
			si = get_mapped_doc("Delivery Note", dn.name, {
				"Delivery Note": {
					"doctype": "Sales Invoice",
					"validation": {"docstatus": ["=", 1]}
				},
				"Delivery Note Item": {
					"doctype": "Sales Invoice Item",
					"field_map": {
						"name": "dn_detail",
						"parent": "delivery_note",
						"so_detail": "so_detail",
						"against_sales_order": "sales_order"
					}
				},
				"Sales Taxes and Charges": {
					"doctype": "Sales Taxes and Charges"
				}
			}, ignore_permissions=True)
		else:
			# Fallback: Create Sales Invoice directly from Sales Order
			si = get_mapped_doc("Sales Order", so.name, {
				"Sales Order": {
					"doctype": "Sales Invoice",
					"validation": {"docstatus": ["=", 1]}
				},
				"Sales Order Item": {
					"doctype": "Sales Invoice Item",
					"field_map": {
						"name": "so_detail",
						"parent": "sales_order"
					}
				},
				"Sales Taxes and Charges": {
					"doctype": "Sales Taxes and Charges"
				}
			}, ignore_permissions=True)

		si.insert(ignore_permissions=True)
		si.submit()

		# ============================================
		# STEP 3: CREATE PAYMENT ENTRY
		# ============================================

		default_receivable = frappe.db.get_value("Company", company, "default_receivable_account")

		# Try to get eBay payment account first, then fall back to cash/bank
		default_cash = frappe.db.get_single_value("eBay Settings", "payment_account")
		if not default_cash:
			default_cash = frappe.db.get_value("Account", {"account_type": "Cash", "company": company, "is_group": 0}, "name")
		if not default_cash:
			default_cash = frappe.db.get_value("Account", {"account_type": "Bank", "company": company, "is_group": 0}, "name")

		if not default_cash or not default_receivable:
			frappe.log_error(f"Missing payment accounts for company {company}", "eBay Payment Error")
			return

		pe = frappe.get_doc({
			"doctype": "Payment Entry",
			"payment_type": "Receive",
			"party_type": "Customer",
			"party": so.customer,
			"company": company,
			"paid_amount": si.grand_total,
			"received_amount": si.grand_total,
			"reference_no": ebay_order_id,
			"reference_date": nowdate(),
			"paid_from": default_receivable,
			"paid_to": default_cash,
			"paid_from_account_currency": frappe.db.get_value("Account", default_receivable, "account_currency"),
			"paid_to_account_currency": frappe.db.get_value("Account", default_cash, "account_currency")
		})
		pe.append("references", {
			"reference_doctype": "Sales Invoice",
			"reference_name": si.name,
			"total_amount": si.grand_total,
			"allocated_amount": si.grand_total
		})
		pe.insert(ignore_permissions=True)
		pe.submit()

	except Exception as e:
		frappe.log_error(message=str(e), title="eBay Invoice/Payment Error")


def log_sync_result(method, status, message, details=None):
	"""Helper to log sync results to eBay Log doctype"""
	frappe.get_doc({
		"doctype": "eBay Log",
		"method": method,
		"status": status,
		"message": message[:140] if message else "",
		"details": details or ""
	}).insert(ignore_permissions=True)
	frappe.db.commit()
