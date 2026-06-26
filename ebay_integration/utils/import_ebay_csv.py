import frappe
import csv
from frappe.utils import getdate, nowdate, flt
from io import StringIO


def import_ebay_csv(file_content, update_existing=False):
	"""
	Import eBay orders from CSV export file.

	Handles all standard eBay order export columns including:
	- Order info: Sales Record Number, Order Number, Transaction ID
	- Buyer info: Username, Name, Email, Note, Address, Tax Identifier
	- Ship To info: Name, Phone, Address
	- Item info: Number, Title, Custom Label, Variation Details
	- Financial: Sold For, Shipping, all tax/fee types, Total Price
	- Dates: Sale Date, Paid On Date, Ship By Date, Shipped On Date, Delivery estimates
	- Tracking: Shipping Service, Tracking Number, PayPal Transaction ID
	- Programs: Global Shipping, eBay Plus, Click and Collect, etc.
	"""

	results = {
		'imported': 0,
		'updated': 0,
		'skipped': 0,
		'errors': [],
		'messages': []
	}

	try:
		# Remove BOM (Byte Order Mark) if present - common in Excel exports
		if file_content.startswith('\ufeff'):
			file_content = file_content[1:]

		# Parse CSV
		reader = csv.DictReader(StringIO(file_content))
		rows = list(reader)
		results['messages'].append(f"Found {len(rows)} rows in CSV")

		# Log column names for debugging
		if rows:
			results['messages'].append(f"CSV columns: {list(rows[0].keys())[:10]}...")

		company = frappe.db.get_single_value("eBay Settings", "default_company")
		if not company:
			results['errors'].append("No default company set in eBay Settings")
			return results

		for i, row in enumerate(rows):
			try:
				result = process_csv_row(row, company, update_existing)
				if result == 'imported':
					results['imported'] += 1
				elif result == 'updated':
					results['updated'] += 1
				elif result == 'skipped':
					results['skipped'] += 1
			except Exception as e:
				error_msg = f"Row {i+1}: {str(e)}"
				results['errors'].append(error_msg)
				frappe.log_error(message=error_msg, title="eBay CSV Import Row Error")

		frappe.db.commit()
		results['messages'].append(
			f"Import complete: {results['imported']} imported, {results['updated']} updated, "
			f"{results['skipped']} skipped, {len(results['errors'])} errors"
		)

	except Exception as e:
		results['errors'].append(f"CSV parsing error: {str(e)}")
		frappe.log_error(message=str(e), title="eBay CSV Import Error")

	return results


def process_csv_row(row, company, update_existing=False):
	"""Process a single CSV row into a Sales Order with full eBay data"""

	# Extract order ID - try different column name variations
	order_number = get_csv_value(row, ['Order Number', 'order number', 'OrderNumber'])
	sales_record = get_csv_value(row, ['Sales Record Number', 'sales record number'])
	transaction_id = get_csv_value(row, ['Transaction ID', 'transaction id', 'TransactionID'])

	if not order_number:
		raise ValueError("Missing Order Number")

	# Use order number as the unique identifier
	existing = frappe.db.get_value("Sales Order", {"po_no": order_number}, "name")
	if existing:
		if not update_existing:
			return 'skipped'
		# Update existing order with additional data
		return update_existing_order(existing, row, company)

	# ============================================
	# EXTRACT ALL FINANCIAL DATA
	# ============================================

	# Base prices
	sold_for = parse_currency(get_csv_value(row, ['Sold For', 'sold for']))
	shipping = parse_currency(get_csv_value(row, ['Shipping And Handling', 'shipping and handling']))
	qty = flt(get_csv_value(row, ['Quantity', 'quantity']) or '1') or 1

	# Tax data
	tax_rate = get_csv_value(row, ['eBay Collect And Remit Tax Rate', 'ebay collect and remit tax rate'])
	tax_type = get_csv_value(row, ['eBay Collect And Remit Tax Type', 'ebay collect and remit tax type'])
	ebay_tax = parse_currency(get_csv_value(row, ['eBay Collected Tax', 'ebay collected tax']))
	seller_tax = parse_currency(get_csv_value(row, ['Seller Collected Tax', 'seller collected tax']))
	tax_status = get_csv_value(row, ['Tax Status', 'tax status'])

	# All fee types
	ewaste_fee = parse_currency(get_csv_value(row, ['Electronic Waste Recycling Fee', 'electronic waste recycling fee']))
	mattress_fee = parse_currency(get_csv_value(row, ['Mattress Recycling Fee', 'mattress recycling fee']))
	battery_fee = parse_currency(get_csv_value(row, ['Battery Recycling Fee', 'battery recycling fee']))
	white_goods_fee = parse_currency(get_csv_value(row, ['White Goods Disposal Tax', 'white goods disposal tax']))
	tire_fee = parse_currency(get_csv_value(row, ['Tire Recycling Fee', 'tire recycling fee']))
	additional_fee = parse_currency(get_csv_value(row, ['Additional Fee', 'additional fee']))
	lumber_fee = parse_currency(get_csv_value(row, ['Lumber Fee', 'lumber fee']))
	wireless_fee = parse_currency(get_csv_value(row, ['Prepaid Wireless Fee', 'prepaid wireless fee']))
	road_fee = parse_currency(get_csv_value(row, ['Road Improvement And Food Delivery Fee', 'road improvement and food delivery fee']))
	ebay_charges = parse_currency(get_csv_value(row, ['eBay Collected Charges', 'ebay collected charges']))

	total = parse_currency(get_csv_value(row, ['Total Price', 'total price']))
	tax_included = get_csv_value(row, ['eBay Collected Tax and Fees Included in Total', 'ebay collected tax and fees included in total'])
	payment_method = get_csv_value(row, ['Payment Method', 'payment method'])

	# ============================================
	# PARSE DATES
	# ============================================

	sale_date = parse_ebay_date(get_csv_value(row, ['Sale Date', 'sale date']))
	paid_date = parse_ebay_date(get_csv_value(row, ['Paid On Date', 'paid on date']))
	ship_by_date = parse_ebay_date(get_csv_value(row, ['Ship By Date', 'ship by date']))
	shipped_date = parse_ebay_date(get_csv_value(row, ['Shipped On Date', 'shipped on date']))
	min_delivery_date = parse_ebay_date(get_csv_value(row, ['Minimum Estimated Delivery Date', 'minimum estimated delivery date']))
	max_delivery_date = parse_ebay_date(get_csv_value(row, ['Maximum Estimated Delivery Date', 'maximum estimated delivery date']))

	# ============================================
	# TRACKING & SHIPPING DATA
	# ============================================

	tracking_number = parse_scientific_notation(get_csv_value(row, ['Tracking Number', 'tracking number']))
	shipping_service = get_csv_value(row, ['Shipping Service', 'shipping service'])
	paypal_txn_id = get_csv_value(row, ['PayPal Transaction ID', 'paypal transaction id'])

	# ============================================
	# ITEM DATA
	# ============================================

	item_number = parse_scientific_notation(get_csv_value(row, ['Item Number', 'item number']))
	item_title = get_csv_value(row, ['Item Title', 'item title']) or 'eBay Item'
	custom_label = get_csv_value(row, ['Custom Label', 'custom label'])  # SKU
	variation_details = get_csv_value(row, ['Variation Details', 'variation details'])
	promoted_listing = get_csv_value(row, ['Sold Via Promoted Listings', 'sold via promoted listings'])

	# Item location
	item_location = get_csv_value(row, ['Item Location', 'item location'])
	item_zip = get_csv_value(row, ['Item Zip Code', 'item zip code'])
	item_country = get_csv_value(row, ['Item Country', 'item country'])

	# ============================================
	# BUYER DATA
	# ============================================

	buyer_username = get_csv_value(row, ['Buyer Username', 'buyer username'])
	buyer_name = get_csv_value(row, ['Buyer Name', 'buyer name'])
	buyer_email = get_csv_value(row, ['Buyer Email', 'buyer email'])
	buyer_note = get_csv_value(row, ['Buyer Note', 'buyer note'])

	# Buyer tax identifier
	buyer_tax_id_name = get_csv_value(row, ['Buyer Tax Identifier Name', 'buyer tax identifier name'])
	buyer_tax_id_value = get_csv_value(row, ['Buyer Tax Identifier Value', 'buyer tax identifier value'])

	# Buyer address (billing)
	buyer_address1 = get_csv_value(row, ['Buyer Address 1', 'buyer address 1'])
	buyer_address2 = get_csv_value(row, ['Buyer Address 2', 'buyer address 2'])
	buyer_city = get_csv_value(row, ['Buyer City', 'buyer city'])
	buyer_state = get_csv_value(row, ['Buyer State', 'buyer state'])
	buyer_zip = get_csv_value(row, ['Buyer Zip', 'buyer zip'])
	buyer_country = get_csv_value(row, ['Buyer Country', 'buyer country'])

	# ============================================
	# SHIP TO DATA
	# ============================================

	ship_to_name = get_csv_value(row, ['Ship To Name', 'ship to name'])
	ship_to_phone = get_csv_value(row, ['Ship To Phone', 'ship to phone'])
	ship_to_address1 = get_csv_value(row, ['Ship To Address 1', 'ship to address 1'])
	ship_to_address2 = get_csv_value(row, ['Ship To Address 2', 'ship to address 2'])
	ship_to_city = get_csv_value(row, ['Ship To City', 'ship to city'])
	ship_to_state = get_csv_value(row, ['Ship To State', 'ship to state'])
	ship_to_zip = get_csv_value(row, ['Ship To Zip', 'ship to zip'])
	ship_to_country = get_csv_value(row, ['Ship To Country', 'ship to country'])

	# Tax location (for international)
	tax_city = get_csv_value(row, ['Tax City', 'tax city'])
	tax_state = get_csv_value(row, ['Tax State', 'tax state'])
	tax_zip = get_csv_value(row, ['Tax Zip', 'tax zip'])
	tax_country = get_csv_value(row, ['Tax Country', 'tax country'])

	# ============================================
	# PROGRAM FLAGS
	# ============================================

	global_shipping = get_csv_value(row, ['Global Shipping Program', 'global shipping program'])
	global_shipping_ref = get_csv_value(row, ['Global Shipping Reference ID', 'global shipping reference id'])
	click_collect = get_csv_value(row, ['Click And Collect', 'click and collect'])
	click_collect_ref = get_csv_value(row, ['Click And Collect Reference Number', 'click and collect reference number'])
	ebay_plus = get_csv_value(row, ['eBay Plus', 'ebay plus'])
	auth_verification = get_csv_value(row, ['Authenticity Verification Program', 'authenticity verification program'])
	auth_status = get_csv_value(row, ['Authenticity Verification Status', 'authenticity verification status'])
	auth_reason = get_csv_value(row, ['Authenticity Verification Outcome Reason', 'authenticity verification outcome reason'])
	psa_vault = get_csv_value(row, ['PSA Vault Program', 'psa vault program'])
	vault_type = get_csv_value(row, ['Vault Fulfillment Type', 'vault fulfillment type'])
	ebay_fulfillment = get_csv_value(row, ['eBay Fulfillment Program', 'ebay fulfillment program'])
	ebay_intl_shipping = get_csv_value(row, ['eBay International Shipping', 'ebay international shipping'])

	# Feedback
	feedback_left = get_csv_value(row, ['Feedback Left', 'feedback left'])
	feedback_received = get_csv_value(row, ['Feedback Received', 'feedback received'])
	my_item_note = get_csv_value(row, ['My Item Note', 'my item note'])

	# eBay Reference
	ebay_ref_name = get_csv_value(row, ['eBay Reference Name', 'ebay reference name'])
	ebay_ref_value = get_csv_value(row, ['eBay Reference Value', 'ebay reference value'])

	# ============================================
	# CREATE CUSTOMER
	# ============================================

	customer = get_or_create_csv_customer(
		buyer_name=buyer_name,
		buyer_username=buyer_username,
		buyer_email=buyer_email,
		buyer_tax_id_name=buyer_tax_id_name,
		buyer_tax_id_value=buyer_tax_id_value,
		# Shipping address (primary)
		ship_to_name=ship_to_name,
		ship_to_phone=ship_to_phone,
		ship_to_address1=ship_to_address1,
		ship_to_address2=ship_to_address2,
		ship_to_city=ship_to_city,
		ship_to_state=ship_to_state,
		ship_to_zip=ship_to_zip,
		ship_to_country=ship_to_country,
		# Buyer address (billing)
		buyer_address1=buyer_address1,
		buyer_address2=buyer_address2,
		buyer_city=buyer_city,
		buyer_state=buyer_state,
		buyer_zip=buyer_zip,
		buyer_country=buyer_country
	)

	# ============================================
	# CREATE/GET ITEM
	# ============================================

	# Use Custom Label (SKU) if available, otherwise use Item Number
	sku = custom_label or item_number
	item_code = get_or_create_csv_item(
		sku=sku,
		title=item_title,
		price=sold_for,
		ebay_item_number=item_number,
		variation_details=variation_details
	)

	# ============================================
	# BUILD ORDER ITEMS
	# ============================================

	items = [{
		"item_code": item_code,
		"qty": qty,
		"rate": sold_for,
		"delivery_date": ship_by_date or nowdate(),
		"description": build_item_description(item_title, variation_details, custom_label),
		"ebay_item_title": item_title,
		"ebay_item_number": item_number,
		"ebay_variation_details": variation_details
	}]

	# ============================================
	# BUILD TAXES AND CHARGES
	# ============================================

	taxes = []

	# Shipping charge
	if shipping > 0:
		shipping_account = get_shipping_account(company)
		if shipping_account:
			taxes.append({
				"charge_type": "Actual",
				"account_head": shipping_account,
				"description": f"eBay Shipping - {shipping_service}" if shipping_service else "eBay Shipping",
				"tax_amount": shipping
			})

	# eBay Collected Tax
	if ebay_tax > 0:
		tax_account = get_tax_account(company)
		if tax_account:
			desc_parts = ["eBay Collected Tax"]
			if tax_type:
				desc_parts.append(f"({tax_type})")
			if tax_rate:
				desc_parts.append(f"@ {tax_rate}%")
			taxes.append({
				"charge_type": "Actual",
				"account_head": tax_account,
				"description": " ".join(desc_parts),
				"tax_amount": ebay_tax
			})

	# Seller Collected Tax (separate from eBay collected)
	if seller_tax > 0:
		tax_account = get_tax_account(company)
		if tax_account:
			taxes.append({
				"charge_type": "Actual",
				"account_head": tax_account,
				"description": "Seller Collected Tax",
				"tax_amount": seller_tax
			})

	# Environmental/Recycling Fees
	total_env_fees = ewaste_fee + mattress_fee + battery_fee + white_goods_fee + tire_fee
	if total_env_fees > 0:
		fee_account = get_fee_account(company)
		if fee_account:
			fee_details = []
			if ewaste_fee > 0:
				fee_details.append(f"E-Waste: ${ewaste_fee:.2f}")
			if mattress_fee > 0:
				fee_details.append(f"Mattress: ${mattress_fee:.2f}")
			if battery_fee > 0:
				fee_details.append(f"Battery: ${battery_fee:.2f}")
			if white_goods_fee > 0:
				fee_details.append(f"White Goods: ${white_goods_fee:.2f}")
			if tire_fee > 0:
				fee_details.append(f"Tire: ${tire_fee:.2f}")
			taxes.append({
				"charge_type": "Actual",
				"account_head": fee_account,
				"description": f"Environmental Fees ({', '.join(fee_details)})",
				"tax_amount": total_env_fees
			})

	# Other Fees
	total_other_fees = additional_fee + lumber_fee + wireless_fee + road_fee
	if total_other_fees > 0:
		fee_account = get_fee_account(company)
		if fee_account:
			fee_details = []
			if additional_fee > 0:
				fee_details.append(f"Additional: ${additional_fee:.2f}")
			if lumber_fee > 0:
				fee_details.append(f"Lumber: ${lumber_fee:.2f}")
			if wireless_fee > 0:
				fee_details.append(f"Wireless: ${wireless_fee:.2f}")
			if road_fee > 0:
				fee_details.append(f"Road/Delivery: ${road_fee:.2f}")
			taxes.append({
				"charge_type": "Actual",
				"account_head": fee_account,
				"description": f"Additional Fees ({', '.join(fee_details)})",
				"tax_amount": total_other_fees
			})

	# ============================================
	# CREATE SALES ORDER
	# ============================================

	so_data = {
		"doctype": "Sales Order",
		"customer": customer,
		"po_no": order_number,
		"transaction_date": sale_date or nowdate(),
		"delivery_date": ship_by_date or nowdate(),
		"company": company,
		"items": items,
		# Custom fields for eBay data (if they exist)
		"order_type": "Sales"
	}

	# Add customer note if present
	if buyer_note:
		so_data["instructions"] = buyer_note

	if taxes:
		so_data["taxes"] = taxes

	so = frappe.get_doc(so_data)

	# Set custom fields if they exist on the doctype
	set_custom_field_if_exists(so, "ebay_order_number", order_number)
	set_custom_field_if_exists(so, "ebay_sales_record", sales_record)
	set_custom_field_if_exists(so, "ebay_transaction_id", transaction_id)
	set_custom_field_if_exists(so, "ebay_tracking_number", tracking_number)
	set_custom_field_if_exists(so, "ebay_shipping_service", shipping_service)
	set_custom_field_if_exists(so, "ebay_payment_method", payment_method)
	set_custom_field_if_exists(so, "ebay_buyer_note", buyer_note)
	set_custom_field_if_exists(so, "is_global_shipping", is_yes(global_shipping))
	set_custom_field_if_exists(so, "is_ebay_plus", is_yes(ebay_plus))
	set_custom_field_if_exists(so, "is_ebay_intl_shipping", is_yes(ebay_intl_shipping))

	so.insert(ignore_permissions=True)
	so.submit()

	# ============================================
	# CREATE EBAY ORDER RECORD (if doctype exists)
	# ============================================

	create_ebay_order_record(
		sales_order=so.name,
		row_data={
			'order_number': order_number,
			'sales_record': sales_record,
			'transaction_id': transaction_id,
			'buyer_username': buyer_username,
			'buyer_name': buyer_name,
			'buyer_email': buyer_email,
			'buyer_note': buyer_note,
			'buyer_tax_id_name': buyer_tax_id_name,
			'buyer_tax_id_value': buyer_tax_id_value,
			'item_number': item_number,
			'item_title': item_title,
			'custom_label': custom_label,
			'variation_details': variation_details,
			'promoted_listing': promoted_listing,
			'item_location': item_location,
			'item_zip': item_zip,
			'item_country': item_country,
			'tracking_number': tracking_number,
			'shipping_service': shipping_service,
			'paypal_txn_id': paypal_txn_id,
			'sale_date': sale_date,
			'paid_date': paid_date,
			'ship_by_date': ship_by_date,
			'shipped_date': shipped_date,
			'min_delivery_date': min_delivery_date,
			'max_delivery_date': max_delivery_date,
			'payment_method': payment_method,
			'tax_status': tax_status,
			'tax_city': tax_city,
			'tax_state': tax_state,
			'tax_zip': tax_zip,
			'tax_country': tax_country,
			'global_shipping': global_shipping,
			'global_shipping_ref': global_shipping_ref,
			'click_collect': click_collect,
			'click_collect_ref': click_collect_ref,
			'ebay_plus': ebay_plus,
			'auth_verification': auth_verification,
			'auth_status': auth_status,
			'auth_reason': auth_reason,
			'psa_vault': psa_vault,
			'vault_type': vault_type,
			'ebay_fulfillment': ebay_fulfillment,
			'ebay_intl_shipping': ebay_intl_shipping,
			'feedback_left': feedback_left,
			'feedback_received': feedback_received,
			'my_item_note': my_item_note,
			'ebay_ref_name': ebay_ref_name,
			'ebay_ref_value': ebay_ref_value,
			'ewaste_fee': ewaste_fee,
			'mattress_fee': mattress_fee,
			'battery_fee': battery_fee,
			'white_goods_fee': white_goods_fee,
			'tire_fee': tire_fee,
			'additional_fee': additional_fee,
			'lumber_fee': lumber_fee,
			'wireless_fee': wireless_fee,
			'road_fee': road_fee,
			'ebay_charges': ebay_charges,
			'ebay_tax': ebay_tax,
			'seller_tax': seller_tax,
			'tax_rate': tax_rate,
			'tax_type': tax_type,
			'tax_included': tax_included
		}
	)

	# ============================================
	# CREATE INVOICE AND PAYMENT
	# ============================================

	# CSV orders are typically already paid
	try:
		create_csv_invoice_and_payment(so, order_number, paid_date)
	except Exception as e:
		frappe.log_error(message=f"Invoice error for {order_number}: {e}", title="eBay CSV Invoice Error")

	return 'imported'


def update_existing_order(so_name, row, company):
	"""Update an existing Sales Order with additional data from CSV"""
	try:
		so = frappe.get_doc("Sales Order", so_name)

		# Only update if in draft state
		if so.docstatus != 0:
			return 'skipped'

		# Update tracking info if available
		tracking = parse_scientific_notation(get_csv_value(row, ['Tracking Number', 'tracking number']))
		if tracking:
			set_custom_field_if_exists(so, "ebay_tracking_number", tracking)

		shipping_service = get_csv_value(row, ['Shipping Service', 'shipping service'])
		if shipping_service:
			set_custom_field_if_exists(so, "ebay_shipping_service", shipping_service)

		so.save(ignore_permissions=True)
		return 'updated'

	except Exception as e:
		frappe.log_error(message=f"Error updating {so_name}: {e}", title="eBay CSV Update")
		return 'skipped'


# ============================================
# HELPER FUNCTIONS
# ============================================

def get_csv_value(row, column_names):
	"""Get value from row trying multiple column name variations"""
	for name in column_names:
		value = row.get(name, '').strip() if row.get(name) else ''
		if value:
			return value
	return ''


def parse_currency(value):
	"""Parse currency string like '$68.00' to float"""
	if not value:
		return 0.0
	# Remove currency symbols and commas
	cleaned = str(value).replace('$', '').replace(',', '').replace('€', '').replace('£', '').replace('¥', '').strip()
	try:
		return flt(cleaned)
	except Exception:
		return 0.0


def parse_scientific_notation(value):
	"""Parse values that may be in scientific notation (e.g., 4.0626E+11)"""
	if not value:
		return ''
	value = str(value).strip()
	if 'E+' in value.upper() or 'E-' in value.upper():
		try:
			# Convert scientific notation to full number string
			return str(int(float(value)))
		except Exception:
			return value
	return value


def parse_ebay_date(date_str):
	"""Parse eBay date format like 'Oct-29-25' or 'Oct-29-2025'"""
	if not date_str:
		return None

	date_str = str(date_str).strip()

	import datetime

	# Try different formats
	formats = [
		'%b-%d-%y',      # Oct-29-25
		'%b-%d-%Y',      # Oct-29-2025
		'%m-%d-%y',      # 10-29-25
		'%m-%d-%Y',      # 10-29-2025
		'%Y-%m-%d',      # 2025-10-29
		'%d-%b-%y',      # 29-Oct-25
		'%d-%b-%Y',      # 29-Oct-2025
		'%m/%d/%y',      # 10/29/25
		'%m/%d/%Y',      # 10/29/2025
	]

	for fmt in formats:
		try:
			return datetime.datetime.strptime(date_str, fmt).date()
		except ValueError:
			continue

	# If all formats fail, try getdate
	try:
		return getdate(date_str)
	except Exception:
		return None


def is_yes(value):
	"""Check if value represents 'Yes' or truthy"""
	if not value:
		return 0
	return 1 if str(value).lower() in ('yes', 'true', '1', 'y') else 0


def build_item_description(title, variation_details, custom_label):
	"""Build item description from available data"""
	parts = [title or 'eBay Item']
	if variation_details:
		parts.append(f"Variation: {variation_details}")
	if custom_label:
		parts.append(f"SKU: {custom_label}")
	return " | ".join(parts)


def set_custom_field_if_exists(doc, fieldname, value):
	"""Set a custom field value only if the field exists on the doctype"""
	if value and hasattr(doc, fieldname):
		try:
			setattr(doc, fieldname, value)
		except Exception:
			pass


def get_or_create_csv_customer(buyer_name, buyer_username, buyer_email,
							   buyer_tax_id_name=None, buyer_tax_id_value=None,
							   ship_to_name=None, ship_to_phone=None,
							   ship_to_address1=None, ship_to_address2=None,
							   ship_to_city=None, ship_to_state=None,
							   ship_to_zip=None, ship_to_country=None,
							   buyer_address1=None, buyer_address2=None,
							   buyer_city=None, buyer_state=None,
							   buyer_zip=None, buyer_country=None):
	"""Get or create customer from CSV data with full address info"""

	# Determine customer name
	customer_name = buyer_name or buyer_username or 'eBay Buyer'

	# Determine email
	if not buyer_email:
		email = f"{buyer_username or 'unknown'}@ebay.placeholder.com"
	else:
		email = buyer_email

	# Check if customer exists by email
	existing = frappe.db.get_value("Customer", {"email_id": email}, "name")
	if existing:
		# Update addresses if they don't exist
		ensure_customer_addresses(
			existing,
			ship_to_name or customer_name, ship_to_phone,
			ship_to_address1, ship_to_address2, ship_to_city,
			ship_to_state, ship_to_zip, ship_to_country,
			buyer_address1, buyer_address2, buyer_city,
			buyer_state, buyer_zip, buyer_country
		)
		return existing

	# Create customer
	customer_group = frappe.db.get_single_value("eBay Settings", "default_customer_group") or "All Customer Groups"

	customer = frappe.get_doc({
		"doctype": "Customer",
		"customer_name": customer_name[:140],
		"customer_type": "Individual",
		"customer_group": customer_group,
		"email_id": email
	})

	# Set custom tax ID fields if they exist
	set_custom_field_if_exists(customer, "tax_id_type", buyer_tax_id_name)
	set_custom_field_if_exists(customer, "tax_id", buyer_tax_id_value)

	customer.insert(ignore_permissions=True)

	# Create shipping address
	if ship_to_address1:
		create_customer_address(
			customer.name,
			ship_to_name or customer_name,
			ship_to_phone,
			ship_to_address1, ship_to_address2,
			ship_to_city, ship_to_state, ship_to_zip, ship_to_country,
			address_type="Shipping"
		)

	# Create billing address if different from shipping
	if buyer_address1 and buyer_address1 != ship_to_address1:
		create_customer_address(
			customer.name,
			buyer_name or customer_name,
			None,  # No phone for billing
			buyer_address1, buyer_address2,
			buyer_city, buyer_state, buyer_zip, buyer_country,
			address_type="Billing"
		)

	return customer.name


def ensure_customer_addresses(customer_name,
							  ship_to_name, ship_to_phone,
							  ship_to_address1, ship_to_address2,
							  ship_to_city, ship_to_state, ship_to_zip, ship_to_country,
							  buyer_address1, buyer_address2,
							  buyer_city, buyer_state, buyer_zip, buyer_country):
	"""Ensure customer has shipping and billing addresses"""

	# Check if shipping address exists
	if ship_to_address1:
		existing_ship = frappe.db.exists("Address", {
			"address_line1": ship_to_address1,
			"city": ship_to_city,
			"pincode": ship_to_zip
		})
		if not existing_ship:
			create_customer_address(
				customer_name,
				ship_to_name,
				ship_to_phone,
				ship_to_address1, ship_to_address2,
				ship_to_city, ship_to_state, ship_to_zip, ship_to_country,
				address_type="Shipping"
			)

	# Check if billing address exists
	if buyer_address1 and buyer_address1 != ship_to_address1:
		existing_bill = frappe.db.exists("Address", {
			"address_line1": buyer_address1,
			"city": buyer_city,
			"pincode": buyer_zip
		})
		if not existing_bill:
			create_customer_address(
				customer_name,
				ship_to_name,
				None,
				buyer_address1, buyer_address2,
				buyer_city, buyer_state, buyer_zip, buyer_country,
				address_type="Billing"
			)


def create_customer_address(customer_name, address_title, phone,
							address1, address2, city, state, pincode, country,
							address_type="Shipping"):
	"""Create an address linked to customer"""
	try:
		# Clean and validate country
		country = validate_country(country)

		address = frappe.get_doc({
			"doctype": "Address",
			"address_title": (address_title or customer_name)[:100],
			"address_type": address_type,
			"address_line1": address1[:140] if address1 else "",
			"address_line2": address2[:140] if address2 else "",
			"city": city[:100] if city else "",
			"state": state[:100] if state else "",
			"pincode": pincode[:20] if pincode else "",
			"country": country,
			"phone": phone[:20] if phone else "",
			"links": [{
				"link_doctype": "Customer",
				"link_name": customer_name
			}]
		})
		address.insert(ignore_permissions=True)
		return address.name
	except Exception as e:
		frappe.log_error(message=f"Address creation error: {e}", title="eBay CSV Address Error")
		return None


def validate_country(country_name):
	"""Validate and return proper country name for ERPNext"""
	if not country_name:
		return "United States"

	country_name = country_name.strip()

	# Check if country exists
	if frappe.db.exists("Country", country_name):
		return country_name

	# Common mappings
	country_map = {
		'US': 'United States',
		'USA': 'United States',
		'UK': 'United Kingdom',
		'GB': 'United Kingdom',
		'CA': 'Canada',
		'AU': 'Australia',
		'DE': 'Germany',
		'FR': 'France',
		'MX': 'Mexico',
		'JP': 'Japan',
		'CN': 'China',
		'IN': 'India',
		'BR': 'Brazil',
		'IT': 'Italy',
		'ES': 'Spain',
		'NL': 'Netherlands',
	}

	if country_name.upper() in country_map:
		return country_map[country_name.upper()]

	# Try to find by partial match
	found = frappe.db.get_value("Country", {"name": ["like", f"%{country_name}%"]}, "name")
	if found:
		return found

	return "United States"  # Default fallback


def get_or_create_csv_item(sku, title, price, ebay_item_number=None, variation_details=None):
	"""Get or create item from CSV data.

	When no SKU is provided, uses eBay item number as the item code.
	The item_name is always set to the actual eBay listing title for clarity.
	"""
	# Determine item code: prefer SKU, then eBay item number, then generate from title
	if sku:
		item_code = str(sku)[:140].strip()
	elif ebay_item_number:
		# Use eBay item number as item code when no SKU
		item_code = f"EBAY-{str(ebay_item_number)}"[:140].strip()
	elif title:
		# Generate a unique code from title + timestamp for truly SKU-less items
		import hashlib
		title_hash = hashlib.md5(title.encode()).hexdigest()[:8]
		item_code = f"EBAY-{title[:50].replace(' ', '-').upper()}-{title_hash}"[:140]
	else:
		# Last resort fallback
		import time
		item_code = f"EBAY-ITEM-{int(time.time())}"

	if frappe.db.exists("Item", item_code):
		# Update item if needed
		item = frappe.get_doc("Item", item_code)
		if price and item.standard_rate != price:
			item.standard_rate = price
			item.save(ignore_permissions=True)
		return item_code

	# Create item with the actual eBay title as the item_name
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
		"description": title  # Store full title in description as well
	})

	# Set custom fields if they exist
	set_custom_field_if_exists(item, "ebay_item_number", ebay_item_number)
	set_custom_field_if_exists(item, "variation_details", variation_details)

	item.insert(ignore_permissions=True)
	return item.name


def get_shipping_account(company):
	"""Get shipping income account"""
	from ebay_integration.utils.sync_orders import get_shipping_account as get_shipping
	return get_shipping(company)


def get_tax_account(company):
	"""Get eBay tax account"""
	from ebay_integration.utils.sync_orders import get_tax_account as get_tax
	return get_tax(company)


def get_fee_account(company):
	"""Get account for environmental/regulatory fees"""
	# Try to find existing fee account
	account = frappe.db.get_value("Account", {
		"account_name": "eBay Fees",
		"company": company,
		"is_group": 0
	}, "name")

	if account:
		return account

	# Try generic fees account
	account = frappe.db.get_value("Account", {
		"account_name": ["like", "%Fee%"],
		"company": company,
		"root_type": "Expense",
		"is_group": 0
	}, "name")

	if account:
		return account

	# Fall back to tax account
	return get_tax_account(company)


def create_ebay_order_record(sales_order, row_data):
	"""Create eBay Order record to store all eBay-specific data"""
	# Check if eBay Order doctype exists
	if not frappe.db.exists("DocType", "eBay Order"):
		return None

	try:
		ebay_order = frappe.get_doc({
			"doctype": "eBay Order",
			"sales_order": sales_order,
			"ebay_order_number": row_data.get('order_number'),
			"sales_record_number": row_data.get('sales_record'),
			"transaction_id": row_data.get('transaction_id'),
			"buyer_username": row_data.get('buyer_username'),
			"buyer_name": row_data.get('buyer_name'),
			"buyer_email": row_data.get('buyer_email'),
			"buyer_note": row_data.get('buyer_note'),
			"buyer_tax_id_name": row_data.get('buyer_tax_id_name'),
			"buyer_tax_id_value": row_data.get('buyer_tax_id_value'),
			"item_number": row_data.get('item_number'),
			"item_title": row_data.get('item_title'),
			"custom_label": row_data.get('custom_label'),
			"variation_details": row_data.get('variation_details'),
			"sold_via_promoted": is_yes(row_data.get('promoted_listing')),
			"item_location": row_data.get('item_location'),
			"item_zip": row_data.get('item_zip'),
			"item_country": row_data.get('item_country'),
			"tracking_number": row_data.get('tracking_number'),
			"shipping_service": row_data.get('shipping_service'),
			"paypal_transaction_id": row_data.get('paypal_txn_id'),
			"sale_date": row_data.get('sale_date'),
			"paid_on_date": row_data.get('paid_date'),
			"ship_by_date": row_data.get('ship_by_date'),
			"shipped_on_date": row_data.get('shipped_date'),
			"min_delivery_date": row_data.get('min_delivery_date'),
			"max_delivery_date": row_data.get('max_delivery_date'),
			"payment_method": row_data.get('payment_method'),
			"tax_status": row_data.get('tax_status'),
			"tax_city": row_data.get('tax_city'),
			"tax_state": row_data.get('tax_state'),
			"tax_zip": row_data.get('tax_zip'),
			"tax_country": row_data.get('tax_country'),
			"global_shipping_program": is_yes(row_data.get('global_shipping')),
			"global_shipping_ref_id": row_data.get('global_shipping_ref'),
			"click_and_collect": is_yes(row_data.get('click_collect')),
			"click_collect_ref": row_data.get('click_collect_ref'),
			"ebay_plus": is_yes(row_data.get('ebay_plus')),
			"authenticity_verification": is_yes(row_data.get('auth_verification')),
			"auth_verification_status": row_data.get('auth_status'),
			"auth_verification_reason": row_data.get('auth_reason'),
			"psa_vault_program": is_yes(row_data.get('psa_vault')),
			"vault_fulfillment_type": row_data.get('vault_type'),
			"ebay_fulfillment_program": is_yes(row_data.get('ebay_fulfillment')),
			"ebay_international_shipping": is_yes(row_data.get('ebay_intl_shipping')),
			"feedback_left": is_yes(row_data.get('feedback_left')),
			"feedback_received": row_data.get('feedback_received'),
			"my_item_note": row_data.get('my_item_note'),
			"ebay_reference_name": row_data.get('ebay_ref_name'),
			"ebay_reference_value": row_data.get('ebay_ref_value'),
			# Fees
			"ewaste_fee": row_data.get('ewaste_fee') or 0,
			"mattress_fee": row_data.get('mattress_fee') or 0,
			"battery_fee": row_data.get('battery_fee') or 0,
			"white_goods_fee": row_data.get('white_goods_fee') or 0,
			"tire_fee": row_data.get('tire_fee') or 0,
			"additional_fee": row_data.get('additional_fee') or 0,
			"lumber_fee": row_data.get('lumber_fee') or 0,
			"wireless_fee": row_data.get('wireless_fee') or 0,
			"road_fee": row_data.get('road_fee') or 0,
			"ebay_charges": row_data.get('ebay_charges') or 0,
			"ebay_collected_tax": row_data.get('ebay_tax') or 0,
			"seller_collected_tax": row_data.get('seller_tax') or 0,
			"tax_rate": row_data.get('tax_rate'),
			"tax_type": row_data.get('tax_type'),
			"tax_included_in_total": is_yes(row_data.get('tax_included'))
		})
		ebay_order.insert(ignore_permissions=True)

		# Link eBay Order back to Sales Order (if custom field exists)
		try:
			so_doc = frappe.get_doc("Sales Order", sales_order)
			if hasattr(so_doc, 'ebay_order_link'):
				so_doc.db_set('ebay_order_link', ebay_order.name, update_modified=False)
			if hasattr(so_doc, 'ebay_tracking_number') and row_data.get('tracking_number'):
				so_doc.db_set('ebay_tracking_number', row_data.get('tracking_number'), update_modified=False)
			if hasattr(so_doc, 'ebay_shipping_service') and row_data.get('shipping_service'):
				so_doc.db_set('ebay_shipping_service', row_data.get('shipping_service'), update_modified=False)
		except Exception:
			pass  # Custom fields may not exist yet

		return ebay_order.name
	except Exception as e:
		# Log error but don't fail the import
		frappe.log_error(message=f"eBay Order record error: {e}", title="eBay CSV Order Record Error")
		return None


def create_csv_invoice_and_payment(so, order_number, paid_date=None):
	"""
	Create Delivery Note, Sales Invoice, and Payment Entry for CSV imported order.

	Document flow: Sales Order → Delivery Note → Sales Invoice → Payment Entry
	- Delivery Note: Reduces stock from warehouse
	- Sales Invoice: Creates accounting entries (fees/taxes appear in accounting)
	- Payment Entry: Records payment received
	"""
	from frappe.model.mapper import get_mapped_doc

	company = so.company

	# ============================================
	# STEP 1: CREATE DELIVERY NOTE (reduces stock)
	# ============================================

	# Get default warehouse from eBay Settings or company
	default_warehouse = frappe.db.get_single_value("eBay Settings", "default_warehouse")
	if not default_warehouse:
		default_warehouse = frappe.db.get_value("Stock Settings", None, "default_warehouse")
	if not default_warehouse:
		# Try to find any warehouse for this company
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

	# Set posting date if shipped date is available
	if paid_date:
		dn.posting_date = paid_date

	try:
		dn.insert(ignore_permissions=True)
		dn.submit()
	except Exception as e:
		# If stock not available, log but continue with invoice
		frappe.log_error(message=f"Delivery Note error for {order_number}: {e}", title="eBay CSV DN Error")
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

	# Set invoice date
	if paid_date:
		si.posting_date = paid_date

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
		frappe.log_error(message=f"Missing payment accounts for {order_number}", title="eBay CSV Payment Error")
		return

	pe = frappe.get_doc({
		"doctype": "Payment Entry",
		"payment_type": "Receive",
		"party_type": "Customer",
		"party": so.customer,
		"company": company,
		"paid_amount": si.grand_total,
		"received_amount": si.grand_total,
		"reference_no": order_number,
		"reference_date": paid_date or nowdate(),
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


@frappe.whitelist()
def upload_csv_file(file_url, update_existing=False):
	"""Whitelist method to import CSV from uploaded file"""
	from frappe.utils.file_manager import get_file

	try:
		file_name, file_content = get_file(file_url)
		if isinstance(file_content, bytes):
			file_content = file_content.decode('utf-8')

		# Handle boolean parameter from frontend
		if isinstance(update_existing, str):
			update_existing = update_existing.lower() in ('true', '1', 'yes')

		results = import_ebay_csv(file_content, update_existing)
		return results
	except Exception as e:
		frappe.log_error(message=str(e), title="eBay CSV Upload Error")
		return {'errors': [str(e)], 'imported': 0, 'updated': 0, 'skipped': 0, 'messages': []}
