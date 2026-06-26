import frappe
from frappe.utils import nowdate, nowtime


def sync_inventory(force=False):
	if not force and not frappe.db.get_single_value("eBay Settings", "sync_enabled"):
		return {"message": "Sync not enabled", "synced": 0}

	try:
		from ebay_integration.utils.ebay_api import eBayWrapper
		ebay = eBayWrapper()
		items = ebay.get_my_selling()

		log_sync_result("sync_inventory", "Success", f"Fetched {len(items)} items from eBay Inventory API")

		default_warehouse = frappe.db.get_single_value("eBay Settings", "default_warehouse")
		if not default_warehouse:
			log_sync_result("sync_inventory", "Error", "No Default Warehouse in eBay Settings")
			return {"message": "No Default Warehouse configured", "synced": 0}

		company = frappe.db.get_single_value("eBay Settings", "default_company")

		# Build a map of eBay SKU -> {qty, title, description} for fast lookup
		ebay_qty_map = {}
		ebay_product_map = {}
		for item_data in items:
			sku = item_data.get('sku')
			if not sku:
				continue
			availability = item_data.get('availability', {})
			ship_avail = availability.get('shipToLocationAvailability', {})
			ebay_qty_map[sku] = float(ship_avail.get('quantity', 0))

			product = item_data.get('product', {}) or {}
			ebay_product_map[sku] = {
				"title": product.get('title'),
				"description": product.get('description'),
			}

		reconciliation_items = []

		# --- Pass 1: all eBay items (create if missing, reconcile quantity) ---
		for sku, qty_ebay in ebay_qty_map.items():
			product = ebay_product_map.get(sku, {})
			if not frappe.db.exists("Item", sku):
				_create_item_from_sku(sku, product.get("title"), product.get("description"))
			else:
				# Keep name/description in sync with eBay's current listing data
				_update_item_details(sku, product.get("title"), product.get("description"))

			valuation_rate = _get_valuation_rate(sku, default_warehouse)
			reconciliation_items.append({
				"item_code": sku,
				"warehouse": default_warehouse,
				"qty": qty_ebay,
				"valuation_rate": valuation_rate
			})

		# --- Pass 2: items in warehouse with qty > 0 that are no longer on eBay ---
		# Because the warehouse is dedicated to eBay stock, anything not in the
		# eBay response should be zeroed out.
		warehouse_items = frappe.db.sql("""
			SELECT b.item_code, b.actual_qty
			FROM `tabBin` b
			WHERE b.warehouse = %s
			AND b.actual_qty > 0
		""", (default_warehouse,), as_dict=True)

		for row in warehouse_items:
			if row.item_code not in ebay_qty_map:
				# Item no longer listed on eBay — zero it out
				valuation_rate = _get_valuation_rate(row.item_code, default_warehouse)
				reconciliation_items.append({
					"item_code": row.item_code,
					"warehouse": default_warehouse,
					"qty": 0,
					"valuation_rate": valuation_rate
				})

		if reconciliation_items:
			sr = frappe.get_doc({
				"doctype": "Stock Reconciliation",
				"purpose": "Stock Reconciliation",
				"company": company,
				"items": reconciliation_items,
				"posting_date": nowdate(),
				"posting_time": nowtime()
			})
			sr.insert(ignore_permissions=True)
			sr.submit()
			frappe.db.commit()

			zeroed = sum(1 for i in reconciliation_items if i["qty"] == 0)
			updated = len(reconciliation_items) - zeroed
			log_sync_result("sync_inventory", "Success",
				f"Synced {updated} eBay items, zeroed {zeroed} removed items")
			return {"message": f"Synced {updated} items, zeroed {zeroed} removed", "synced": len(reconciliation_items)}
		else:
			log_sync_result("sync_inventory", "Success",
				f"No inventory changes detected (checked {len(items)} eBay items)")
			return {"message": f"No changes detected (checked {len(items)} items)", "synced": 0}

	except Exception as e:
		log_sync_result("sync_inventory", "Error", str(e))
		frappe.log_error(message=str(e), title="eBay Sync Inventory Error")
		return {"message": f"Error: {str(e)}", "synced": 0}


def _get_valuation_rate(item_code, warehouse):
	"""Return the best available valuation rate for a Stock Reconciliation row.

	Priority: existing bin valuation rate → item valuation_rate → standard_rate → 0.01.
	Logs a warning when falling back to 0.01 so the user can fix the item master.
	"""
	# Try the current bin valuation rate first (most accurate moving average)
	bin_rate = frappe.db.get_value(
		"Bin", {"item_code": item_code, "warehouse": warehouse}, "valuation_rate"
	)
	if bin_rate and float(bin_rate) > 0:
		return float(bin_rate)

	# Fall back to item master valuation_rate
	item_rate = frappe.db.get_value("Item", item_code, "valuation_rate")
	if item_rate and float(item_rate) > 0:
		return float(item_rate)

	# Fall back to standard_rate (selling price)
	std_rate = frappe.db.get_value("Item", item_code, "standard_rate")
	if std_rate and float(std_rate) > 0:
		return float(std_rate)

	# Last resort — log so the user can set a proper cost
	frappe.log_error(
		message=f"Item {item_code} has no valuation rate; defaulting to 0.01. Please set a cost on this item.",
		title="eBay Sync: Missing Valuation Rate"
	)
	return 0.01


def _create_item_from_sku(sku, title=None, description=None):
	"""Create a stock item from an eBay SKU found during inventory sync.

	Uses the eBay product title/description when available so the item is named
	meaningfully instead of just by its SKU.
	"""
	try:
		item = frappe.get_doc({
			"doctype": "Item",
			"item_code": sku,
			"item_name": title or sku,
			"item_group": "All Item Groups",
			"stock_uom": "Nos",
			"is_stock_item": 1,
			"description": description or f"eBay item (SKU: {sku})"
		})
		item.insert(ignore_permissions=True)
		frappe.db.commit()
	except Exception as e:
		frappe.log_error(message=f"Could not create item for SKU {sku}: {e}",
						 title="eBay Sync: Item Creation Error")


def _update_item_details(sku, title=None, description=None):
	"""Refresh an existing item's name/description from eBay product data.

	Only writes the fields eBay actually provides, so items keep their current
	values when eBay returns no product info. Best-effort: never raises.
	"""
	updates = {}
	if title:
		updates["item_name"] = title
	if description:
		updates["description"] = description

	if not updates:
		return

	try:
		frappe.db.set_value("Item", sku, updates, update_modified=False)
	except Exception as e:
		frappe.log_error(message=f"Could not update item details for SKU {sku}: {e}",
						 title="eBay Sync: Item Update Error")


def log_sync_result(method, status, message, details=None):
	"""Helper to log sync results to eBay Log doctype"""
	frappe.get_doc({
		"doctype": "eBay Log",
		"method": method,
		"status": status,
		"message": message or "",
		"details": details or ""
	}).insert(ignore_permissions=True)
	frappe.db.commit()
