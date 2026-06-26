app_name = "ebay_integration"
app_title = "eBay Integration"
app_publisher = "reviaro"
app_description = "eBay Connector for ERPNext"
app_license = "mit"

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/ebay_integration/css/ebay_integration.css"
# app_include_js = "/assets/ebay_integration/js/ebay_integration.js"

# Fixtures
# --------
# Export custom fields created for eBay integration
fixtures = [
	{
		"dt": "Custom Field",
		"filters": [
			["name", "like", "%ebay%"]
		]
	}
]

# Scheduled Tasks
# ---------------

scheduler_events = {
	"cron": {
		# Sync orders daily at midnight
		"0 0 * * *": [
			"ebay_integration.utils.sync_orders.sync_orders"
		],
		# Sync inventory daily at 12:30 AM
		"30 0 * * *": [
			"ebay_integration.utils.sync_inventory.sync_inventory"
		],
		# Run price comparison every 4 days at 2 AM
		"0 2 */4 * *": [
			"ebay_integration.utils.price_comparison.run_price_comparison"
		],
		# Sync cancellations and refunds every 6 hours
		"0 */6 * * *": [
			"ebay_integration.utils.sync_cancellations.sync_cancellations_and_refunds"
		],
		# Sync eBay selling fees daily at 1:00 AM (gated by enable_fee_sync, OFF by default)
		"0 1 * * *": [
			"ebay_integration.utils.sync_fees.sync_seller_fees"
		]
	}
}

# Whitelisted Methods
# -------------------
# These are the API endpoints that can be called from JavaScript

# Note: Methods decorated with @frappe.whitelist() in the following files
# are automatically exposed:
# - ebay_integration.ebay_connector.doctype.ebay_settings.ebay_settings
#   - get_authorize_url
#   - generate_token
#   - manual_sync_orders
#   - manual_sync_inventory
#   - manual_import_historical
#   - manual_update_orders
#   - manual_sync_cancellations
#
# - ebay_integration.utils.import_ebay_csv
#   - upload_csv_file
#
# - ebay_integration.utils.import_inventory_csv
#   - upload_inventory_csv
#   - get_inventory_import_preview
#
# - ebay_integration.utils.price_comparison
#   - manual_price_comparison
#
# - ebay_integration.utils.sync_cancellations
#   - manual_sync_cancellations

# DocType Class Overrides
# -----------------------
# override_doctype_class = {
# 	"ToDo": "custom_app.overrides.CustomToDo"
# }

# Document Events
# ---------------
# Hook on document methods and events

# doc_events = {
# 	"Sales Order": {
# 		"on_submit": "ebay_integration.utils.sync_orders.on_so_submit"
# 	}
# }
