import frappe
import csv
from frappe.utils import nowdate, nowtime, flt
from io import StringIO


def import_inventory_csv(file_content, create_items=True, update_stock=True):
	"""
	Import eBay inventory from CSV export file.

	Handles all standard eBay inventory export columns:
	- Item number (eBay listing ID)
	- Title
	- quantity on hand
	- Format (FIXED_PRICE, AUCTION, etc.)
	- Currency
	- sales price
	- Sold quantity
	- eBay category 1 name
	"""

	results = {
		'created': 0,
		'updated': 0,
		'stock_updated': 0,
		'skipped': 0,
		'errors': [],
		'messages': []
	}

	try:
		# Remove BOM (Byte Order Mark) if present - common in Excel exports
		if file_content.startswith('\ufeff'):
			file_content = file_content[1:]

		# Try to detect delimiter (tab or comma)
		first_line = file_content.split('\n')[0] if '\n' in file_content else file_content
		tab_char = '\t'
		delimiter = tab_char if tab_char in first_line else ','
		delimiter_name = 'TAB' if delimiter == tab_char else 'COMMA'
		results['messages'].append(f"Detected delimiter: {delimiter_name}")

		# Parse CSV
		reader = csv.DictReader(StringIO(file_content), delimiter=delimiter)
		rows = list(reader)
		results['messages'].append(f"Found {len(rows)} rows in CSV")

		# Log column names for debugging (clean them up)
		if rows:
			# Get and clean column names
			raw_columns = list(rows[0].keys())
			results['messages'].append(f"CSV columns found: {raw_columns}")

		company = frappe.db.get_single_value("eBay Settings", "default_company")
		default_warehouse = frappe.db.get_single_value("eBay Settings", "default_warehouse")

		if not company:
			results['errors'].append("No default company set in eBay Settings")
			return results

		if not default_warehouse and update_stock:
			results['errors'].append("No default warehouse set in eBay Settings (needed for stock updates)")
			return results

		reconciliation_items = []
		skipped_reasons = {}

		for i, row in enumerate(rows):
			try:
				# Clean the row keys (remove extra whitespace)
				cleaned_row = {k.strip(): v for k, v in row.items() if k}

				result, reason = process_inventory_row(cleaned_row, company, create_items)
				if result == 'created':
					results['created'] += 1
				elif result == 'updated':
					results['updated'] += 1
				elif result == 'skipped':
					results['skipped'] += 1
					# Track skip reasons
					skipped_reasons[reason] = skipped_reasons.get(reason, 0) + 1

				# Collect stock reconciliation data
				if update_stock and result in ('created', 'updated'):
					item_number = get_item_number(cleaned_row)
					qty = parse_number(get_csv_value(cleaned_row, [
						'Available quantity', 'available quantity', 'Available Quantity',
						'quantity on hand', 'Quantity on hand', 'Quantity On Hand',
						'Quantity', 'quantity', 'Stock', 'stock', 'Available', 'available'
					]))

					if item_number and frappe.db.exists("Item", item_number):
						current_qty = frappe.db.get_value("Bin", {
							"item_code": item_number,
							"warehouse": default_warehouse
						}, "actual_qty") or 0

						if float(current_qty) != qty:
							valuation = parse_currency(get_csv_value(cleaned_row, [
								'Current price', 'current price', 'Current Price',
								'Start price', 'start price', 'Start Price',
								'sales price', 'Sales price', 'Sales Price',
								'Price', 'price', 'Selling Price', 'selling price'
							]))
							reconciliation_items.append({
								"item_code": item_number,
								"warehouse": default_warehouse,
								"qty": qty,
								"valuation_rate": valuation or 0.01
							})

			except Exception as e:
				error_msg = f"Row {i+1}: {str(e)}"
				results['errors'].append(error_msg)
				frappe.log_error(error_msg, "eBay Inventory CSV Row Error")

		# Log skip reasons
		if skipped_reasons:
			skip_summary = ", ".join([f"{reason}: {count}" for reason, count in skipped_reasons.items()])
			results['messages'].append(f"Skip reasons: {skip_summary}")

		# Create stock reconciliation if needed
		if reconciliation_items and update_stock:
			try:
				# Get difference account (Stock Adjustment account)
				difference_account = get_stock_adjustment_account(company)

				sr = frappe.get_doc({
					"doctype": "Stock Reconciliation",
					"purpose": "Stock Reconciliation",
					"company": company,
					"expense_account": difference_account,
					"items": reconciliation_items,
					"posting_date": nowdate(),
					"posting_time": nowtime()
				})
				sr.insert(ignore_permissions=True)
				sr.submit()
				results['stock_updated'] = len(reconciliation_items)
				results['messages'].append(f"Created Stock Reconciliation for {len(reconciliation_items)} items")
			except Exception as e:
				results['errors'].append(f"Stock Reconciliation error: {str(e)}")
				frappe.log_error(str(e), "eBay Inventory Stock Reconciliation Error")

		frappe.db.commit()
		results['messages'].append(
			f"Import complete: {results['created']} created, {results['updated']} updated, "
			f"{results['stock_updated']} stock updated, {results['skipped']} skipped, {len(results['errors'])} errors"
		)

	except Exception as e:
		results['errors'].append(f"CSV parsing error: {str(e)}")
		frappe.log_error(str(e), "eBay Inventory CSV Error")

	return results


def get_csv_value(row, column_names):
	"""Get value from row trying multiple column name variations"""
	for name in column_names:
		# Try exact match
		if name in row:
			value = row[name]
			if value and str(value).strip():
				return str(value).strip()

		# Try case-insensitive match
		for key in row.keys():
			if key.lower().strip() == name.lower().strip():
				value = row[key]
				if value and str(value).strip():
					return str(value).strip()

	return ''


def get_item_number(row):
	"""Extract item number from CSV row, handling scientific notation"""
	# Try different possible column names - prefer SKU/Custom label over eBay item number
	item_number = get_csv_value(row, [
		'Custom label (SKU)', 'custom label (sku)', 'Custom Label (SKU)',
		'Custom label', 'custom label', 'Custom Label',
		'SKU', 'sku', 'Sku',
		'Item number', 'item number', 'ItemNumber', 'Item Number',
		'Listing ID', 'listing id', 'ListingID', 'Listing Id',
		'eBay Item Number', 'ebay item number', 'eBay item number',
		'Item #', 'item #', 'Item#'
	])

	return parse_scientific_notation(item_number)


def parse_scientific_notation(value):
	"""Parse values that may be in scientific notation (e.g., 4.05331E+11)"""
	if not value:
		return ''

	value = str(value).strip()

	# Check for scientific notation (e.g., 4.05331E+11)
	if 'E' in value.upper():
		try:
			# Convert to integer then to string to avoid decimal points
			return str(int(float(value)))
		except (ValueError, OverflowError):
			return value

	# Also handle values that look like floats but should be integers
	# (e.g., "405331000000.0")
	try:
		float_val = float(value)
		# If it's a large number and a whole number, convert to int
		if float_val > 1000 and float_val == int(float_val):
			return str(int(float_val))
	except (ValueError, OverflowError):
		pass

	return value


def process_inventory_row(row, company, create_items=True):
	"""Process a single inventory CSV row. Returns (result, reason)"""

	item_number = get_item_number(row)
	if not item_number:
		return ('skipped', 'no_item_number')

	# Get all available data
	title = get_csv_value(row, [
		'Title', 'title', 'Item Title', 'item title',
		'Name', 'name', 'Product Name', 'product name'
	])
	price = parse_currency(get_csv_value(row, [
		'Current price', 'current price', 'Current Price',
		'Start price', 'start price', 'Start Price',
		'sales price', 'Sales price', 'Sales Price',
		'Price', 'price', 'Selling Price', 'selling price'
	]))
	qty = parse_number(get_csv_value(row, [
		'Available quantity', 'available quantity', 'Available Quantity',
		'quantity on hand', 'Quantity on hand', 'Quantity On Hand',
		'Quantity', 'quantity', 'Stock', 'stock', 'Available', 'available'
	]))
	sold_qty = parse_number(get_csv_value(row, [
		'Sold quantity', 'sold quantity', 'Sold Quantity',
		'Sold', 'sold', 'Total Sold', 'total sold'
	]))
	category = get_csv_value(row, [
		'eBay category 1 name', 'ebay category 1 name', 'eBay Category 1 Name',
		'Category', 'category', 'Primary Category', 'primary category',
		'eBay Category', 'ebay category'
	])
	listing_format = get_csv_value(row, [
		'Format', 'format', 'Listing Format', 'listing format',
		'Listing Type', 'listing type'
	])
	currency = get_csv_value(row, ['Currency', 'currency']) or 'USD'

	# Check if item exists
	if frappe.db.exists("Item", item_number):
		# Update existing item
		item = frappe.get_doc("Item", item_number)
		updated = False

		# Update price if provided and different
		if price and price > 0 and abs((item.standard_rate or 0) - price) > 0.01:
			item.standard_rate = price
			updated = True

		# Update title if provided and different
		if title and item.item_name != title[:140]:
			item.item_name = title[:140]
			updated = True

		# Update item group if category provided and different
		if category:
			new_group = get_or_create_item_group(category)
			if item.item_group != new_group and new_group != "All Item Groups":
				item.item_group = new_group
				updated = True

		# Set custom fields if they exist
		if set_custom_field_if_exists(item, "ebay_listing_format", listing_format):
			updated = True
		if set_custom_field_if_exists(item, "ebay_sold_quantity", sold_qty):
			updated = True
		if set_custom_field_if_exists(item, "ebay_currency", currency):
			updated = True

		if updated:
			item.save(ignore_permissions=True)
			return ('updated', None)
		return ('skipped', 'no_changes')

	elif create_items:
		# Create new item
		item_group = get_or_create_item_group(category) if category else "All Item Groups"

		item = frappe.get_doc({
			"doctype": "Item",
			"item_code": item_number,
			"item_name": title[:140] if title else item_number,
			"item_group": item_group,
			"stock_uom": "Nos",
			"is_stock_item": 1,
			"valuation_rate": price or 0.01,
			"standard_rate": price or 0,
			"description": build_item_description(title, item_number, listing_format)
		})

		# Set custom fields if they exist
		set_custom_field_if_exists(item, "ebay_item_number", item_number)
		set_custom_field_if_exists(item, "ebay_listing_format", listing_format)
		set_custom_field_if_exists(item, "ebay_sold_quantity", sold_qty)
		set_custom_field_if_exists(item, "ebay_currency", currency)
		set_custom_field_if_exists(item, "ebay_category", category)

		item.insert(ignore_permissions=True)
		return ('created', None)

	return ('skipped', 'create_items_disabled')


def build_item_description(title, item_number, listing_format):
	"""Build item description from available data"""
	parts = []
	if title:
		parts.append(title)
	parts.append(f"eBay Item #: {item_number}")
	if listing_format:
		parts.append(f"Format: {listing_format}")
	return " | ".join(parts)


def set_custom_field_if_exists(doc, fieldname, value):
	"""Set a custom field value only if the field exists on the doctype"""
	if value is not None and hasattr(doc, fieldname):
		try:
			current_value = getattr(doc, fieldname, None)
			if current_value != value:
				setattr(doc, fieldname, value)
				return True
		except Exception:
			pass
	return False


def get_or_create_item_group(category_name):
	"""Get or create item group from eBay category name"""
	if not category_name:
		return "All Item Groups"

	# Clean up category name
	category_name = category_name.strip()[:140]

	if frappe.db.exists("Item Group", category_name):
		return category_name

	try:
		item_group = frappe.get_doc({
			"doctype": "Item Group",
			"item_group_name": category_name,
			"parent_item_group": "All Item Groups",
			"is_group": 0
		})
		item_group.insert(ignore_permissions=True)
		return item_group.name
	except frappe.exceptions.DuplicateEntryError:
		return category_name
	except Exception as e:
		frappe.log_error(f"Error creating item group '{category_name}': {e}", "eBay Inventory Import")
		return "All Item Groups"


def get_stock_adjustment_account(company):
	"""Get stock adjustment account for Stock Reconciliation"""
	# Try to get from Company settings
	account = frappe.db.get_value("Company", company, "stock_adjustment_account")
	if account:
		return account

	# Try to find Stock Adjustment account
	account = frappe.db.get_value("Account", {
		"account_name": ["like", "%Stock Adjustment%"],
		"company": company,
		"is_group": 0
	}, "name")
	if account:
		return account

	# Try Temporary Opening account
	account = frappe.db.get_value("Account", {
		"account_name": ["like", "%Temporary%Opening%"],
		"company": company,
		"is_group": 0
	}, "name")
	if account:
		return account

	# Fall back to any expense account
	account = frappe.db.get_value("Account", {
		"root_type": "Expense",
		"company": company,
		"is_group": 0
	}, "name")

	return account


def parse_currency(value):
	"""Parse currency string to float"""
	if not value:
		return 0.0

	if isinstance(value, (int, float)):
		return float(value)

	# Remove currency symbols and commas
	cleaned = str(value).replace('$', '').replace(',', '').replace('€', '').replace('£', '').replace('¥', '').strip()

	try:
		return flt(cleaned)
	except Exception:
		return 0.0


def parse_number(value):
	"""Parse number string to float"""
	if not value:
		return 0.0

	if isinstance(value, (int, float)):
		return float(value)

	try:
		return flt(str(value).strip())
	except Exception:
		return 0.0


@frappe.whitelist()
def upload_inventory_csv(file_url, create_items=True, update_stock=True):
	"""Whitelist method to import inventory CSV from uploaded file"""
	from frappe.utils.file_manager import get_file

	try:
		file_name, file_content = get_file(file_url)
		if isinstance(file_content, bytes):
			file_content = file_content.decode('utf-8')

		# Convert string booleans if needed
		if isinstance(create_items, str):
			create_items = create_items.lower() in ('true', '1', 'yes')
		if isinstance(update_stock, str):
			update_stock = update_stock.lower() in ('true', '1', 'yes')

		results = import_inventory_csv(file_content, create_items, update_stock)
		return results
	except Exception as e:
		frappe.log_error(str(e), "eBay Inventory CSV Upload Error")
		return {
			'errors': [str(e)],
			'created': 0,
			'updated': 0,
			'stock_updated': 0,
			'skipped': 0,
			'messages': []
		}


@frappe.whitelist()
def get_inventory_import_preview(file_url, limit=10):
	"""Preview inventory import - returns first N rows parsed"""
	from frappe.utils.file_manager import get_file

	try:
		file_name, file_content = get_file(file_url)
		if isinstance(file_content, bytes):
			file_content = file_content.decode('utf-8')

		# Detect delimiter
		first_line = file_content.split('\n')[0] if '\n' in file_content else file_content
		delimiter = '\t' if '\t' in first_line else ','

		reader = csv.DictReader(StringIO(file_content), delimiter=delimiter)
		rows = list(reader)

		preview_items = []
		for i, row in enumerate(rows[:int(limit)]):
			# Clean row keys
			cleaned_row = {k.strip(): v for k, v in row.items() if k}

			item_number = get_item_number(cleaned_row)
			title = get_csv_value(cleaned_row, ['Title', 'title', 'Item Title', 'item title'])
			price = parse_currency(get_csv_value(cleaned_row, ['sales price', 'Sales price', 'Price']))
			qty = parse_number(get_csv_value(cleaned_row, ['quantity on hand', 'Quantity on hand', 'Quantity']))
			category = get_csv_value(cleaned_row, ['eBay category 1 name', 'ebay category 1 name', 'Category'])
			exists = frappe.db.exists("Item", item_number) if item_number else False

			preview_items.append({
				'item_number': item_number,
				'title': title[:50] + '...' if title and len(title) > 50 else title,
				'price': price,
				'qty': qty,
				'category': category,
				'exists': exists,
				'action': 'Update' if exists else 'Create'
			})

		return {
			'total_rows': len(rows),
			'preview': preview_items,
			'columns': list(rows[0].keys()) if rows else [],
			'delimiter': 'TAB' if delimiter == '\t' else 'COMMA'
		}

	except Exception as e:
		return {'error': str(e), 'preview': [], 'total_rows': 0}
