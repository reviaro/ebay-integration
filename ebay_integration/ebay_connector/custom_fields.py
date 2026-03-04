"""
Custom Fields for eBay Integration

Run this after installation to create custom fields on standard doctypes.
Usage: bench --site [site] execute ebay_integration.ebay_connector.custom_fields.create_custom_fields
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def get_custom_fields():
	"""Define custom fields to be created on standard doctypes"""
	return {
		"Sales Order Item": [
			{
				"fieldname": "ebay_item_title",
				"fieldtype": "Small Text",
				"label": "eBay Item Title",
				"insert_after": "description",
				"read_only": 1,
				"description": "Original eBay listing title for this item"
			},
			{
				"fieldname": "ebay_item_number",
				"fieldtype": "Data",
				"label": "eBay Item Number",
				"insert_after": "ebay_item_title",
				"read_only": 1
			},
			{
				"fieldname": "ebay_variation_details",
				"fieldtype": "Data",
				"label": "Variation Details",
				"insert_after": "ebay_item_number",
				"read_only": 1
			}
		],
		"Sales Order": [
			{
				"fieldname": "ebay_section",
				"fieldtype": "Section Break",
				"label": "eBay Information",
				"insert_after": "terms",
				"collapsible": 1
			},
			{
				"fieldname": "ebay_order_link",
				"fieldtype": "Link",
				"label": "eBay Order Details",
				"options": "eBay Order",
				"insert_after": "ebay_section",
				"read_only": 1,
				"description": "Link to full eBay order data (tracking, fees, programs, etc.)"
			},
			{
				"fieldname": "ebay_column_break",
				"fieldtype": "Column Break",
				"insert_after": "ebay_order_link"
			},
			{
				"fieldname": "ebay_tracking_number",
				"fieldtype": "Data",
				"label": "eBay Tracking Number",
				"insert_after": "ebay_column_break",
				"read_only": 1
			},
			{
				"fieldname": "ebay_shipping_service",
				"fieldtype": "Data",
				"label": "Shipping Service",
				"insert_after": "ebay_tracking_number",
				"read_only": 1
			},
			# Cancellation and Refund Tracking Fields
			{
				"fieldname": "ebay_refund_section",
				"fieldtype": "Section Break",
				"label": "eBay Cancellation/Refund Status",
				"insert_after": "ebay_shipping_service",
				"collapsible": 1
			},
			{
				"fieldname": "ebay_cancellation_status",
				"fieldtype": "Select",
				"label": "Cancellation Status",
				"options": "\nNone Requested\nIn Progress\nCanceled",
				"insert_after": "ebay_refund_section",
				"read_only": 1,
				"description": "Current cancellation status from eBay"
			},
			{
				"fieldname": "ebay_refund_status",
				"fieldtype": "Select",
				"label": "Refund Status",
				"options": "\nNo Refund\nPartial Refund\nFull Refund",
				"insert_after": "ebay_cancellation_status",
				"read_only": 1,
				"description": "Current refund status for this order"
			},
			{
				"fieldname": "ebay_refund_column_break",
				"fieldtype": "Column Break",
				"insert_after": "ebay_refund_status"
			},
			{
				"fieldname": "ebay_refund_amount",
				"fieldtype": "Currency",
				"label": "Total Refunded",
				"insert_after": "ebay_refund_column_break",
				"read_only": 1,
				"description": "Total amount refunded to the buyer"
			},
			{
				"fieldname": "ebay_last_sync",
				"fieldtype": "Datetime",
				"label": "Last Sync",
				"insert_after": "ebay_refund_amount",
				"read_only": 1,
				"description": "Last time this order was synced with eBay"
			}
		],
		"Item": [
			{
				"fieldname": "ebay_section",
				"fieldtype": "Section Break",
				"label": "eBay Information",
				"insert_after": "description",
				"collapsible": 1
			},
			{
				"fieldname": "ebay_item_number",
				"fieldtype": "Data",
				"label": "eBay Item Number",
				"insert_after": "ebay_section",
				"read_only": 1
			},
			{
				"fieldname": "ebay_listing_format",
				"fieldtype": "Data",
				"label": "Listing Format",
				"insert_after": "ebay_item_number",
				"read_only": 1
			},
			{
				"fieldname": "ebay_column_break",
				"fieldtype": "Column Break",
				"insert_after": "ebay_listing_format"
			},
			{
				"fieldname": "ebay_sold_quantity",
				"fieldtype": "Int",
				"label": "eBay Sold Quantity",
				"insert_after": "ebay_column_break",
				"read_only": 1
			},
			{
				"fieldname": "ebay_category",
				"fieldtype": "Data",
				"label": "eBay Category",
				"insert_after": "ebay_sold_quantity",
				"read_only": 1
			}
		],
		"Customer": [
			{
				"fieldname": "ebay_section",
				"fieldtype": "Section Break",
				"label": "eBay Information",
				"insert_after": "language",
				"collapsible": 1
			},
			{
				"fieldname": "ebay_username",
				"fieldtype": "Data",
				"label": "eBay Username",
				"insert_after": "ebay_section"
			},
			{
				"fieldname": "tax_id_type",
				"fieldtype": "Data",
				"label": "Tax ID Type",
				"insert_after": "ebay_username",
				"description": "e.g., CURP, VAT, etc."
			},
			{
				"fieldname": "tax_id",
				"fieldtype": "Data",
				"label": "Tax ID Value",
				"insert_after": "tax_id_type"
			}
		]
	}


def create_all_custom_fields():
	"""Create all custom fields for eBay Integration"""
	custom_fields = get_custom_fields()

	for doctype, fields in custom_fields.items():
		for field in fields:
			# Check if field already exists
			existing = frappe.db.exists("Custom Field", {
				"dt": doctype,
				"fieldname": field["fieldname"]
			})

			if not existing:
				try:
					custom_field = frappe.get_doc({
						"doctype": "Custom Field",
						"dt": doctype,
						**field
					})
					custom_field.insert(ignore_permissions=True)
					print(f"Created custom field: {doctype}.{field['fieldname']}")
				except Exception as e:
					print(f"Error creating {doctype}.{field['fieldname']}: {e}")
			else:
				print(f"Field already exists: {doctype}.{field['fieldname']}")

	frappe.db.commit()
	print("Custom fields creation complete!")


# Alias for bench execute command
create_custom_fields = create_all_custom_fields


@frappe.whitelist()
def setup_custom_fields():
	"""Whitelist method to create custom fields from UI"""
	create_all_custom_fields()
	return "Custom fields created successfully!"
