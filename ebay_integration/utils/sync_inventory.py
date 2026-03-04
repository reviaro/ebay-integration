import frappe
from frappe.utils import nowdate, nowtime


def sync_inventory():
	if not frappe.db.get_single_value("eBay Settings", "sync_enabled"):
		return {"message": "Sync not enabled", "synced": 0}

	try:
		from ebay_integration.utils.ebay_api import eBayWrapper
		ebay = eBayWrapper()
		items = ebay.get_my_selling()

		# Log what we got from eBay
		log_sync_result("sync_inventory", "Success", f"Fetched {len(items)} items from eBay Inventory API")

		reconciliation_items = []

		default_warehouse = frappe.db.get_single_value("eBay Settings", "default_warehouse")
		if not default_warehouse:
			log_sync_result("sync_inventory", "Error", "No Default Warehouse in eBay Settings")
			return {"message": "No Default Warehouse configured", "synced": 0}

		for item_data in items:
			# eBay Inventory API uses lowercase 'sku'
			sku = item_data.get('sku')
			if not sku:
				continue

			# Quantity is in 'availability.shipToLocationAvailability.quantity'
			availability = item_data.get('availability', {})
			ship_avail = availability.get('shipToLocationAvailability', {})
			qty_ebay = float(ship_avail.get('quantity', 0))

			# Check ERPNext Stock
			if frappe.db.exists("Item", sku):
				current_qty = frappe.db.get_value("Bin", {"item_code": sku, "warehouse": default_warehouse}, "actual_qty") or 0

				if float(current_qty) != qty_ebay:
					reconciliation_items.append({
						"item_code": sku,
						"warehouse": default_warehouse,
						"qty": qty_ebay,
						"valuation_rate": frappe.db.get_value("Item", sku, "valuation_rate") or 0.01
					})

		if reconciliation_items:
			sr = frappe.get_doc({
				"doctype": "Stock Reconciliation",
				"purpose": "Stock Reconciliation",
				"company": frappe.db.get_single_value("eBay Settings", "default_company"),
				"items": reconciliation_items,
				"posting_date": nowdate(),
				"posting_time": nowtime()
			})
			sr.insert(ignore_permissions=True)
			sr.submit()

			log_sync_result("sync_inventory", "Success", f"Synced {len(reconciliation_items)} items from eBay")
			return {"message": f"Synced {len(reconciliation_items)} items", "synced": len(reconciliation_items)}
		else:
			log_sync_result("sync_inventory", "Success", f"No inventory changes detected (checked {len(items)} eBay items)")
			return {"message": f"No changes detected (checked {len(items)} items)", "synced": 0}

	except Exception as e:
		log_sync_result("sync_inventory", "Error", str(e))
		frappe.log_error(message=str(e), title="eBay Sync Inventory Error")
		return {"message": f"Error: {str(e)}", "synced": 0}


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
