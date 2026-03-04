# Copyright (c) 2024, Antigravity and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class eBayOrder(Document):
	def validate(self):
		# Validate that sales order exists
		if self.sales_order and not frappe.db.exists("Sales Order", self.sales_order):
			frappe.throw(f"Sales Order {self.sales_order} does not exist")

	def before_insert(self):
		# Check for duplicate eBay order number
		if self.ebay_order_number:
			existing = frappe.db.exists("eBay Order", {
				"ebay_order_number": self.ebay_order_number,
				"name": ["!=", self.name or ""]
			})
			if existing:
				frappe.throw(f"eBay Order {self.ebay_order_number} already exists: {existing}")


@frappe.whitelist()
def get_ebay_order_by_sales_order(sales_order):
	"""Get eBay Order linked to a Sales Order"""
	ebay_order = frappe.db.get_value("eBay Order", {"sales_order": sales_order}, "name")
	if ebay_order:
		return frappe.get_doc("eBay Order", ebay_order)
	return None


@frappe.whitelist()
def get_ebay_order_by_order_number(ebay_order_number):
	"""Get eBay Order by eBay order number"""
	ebay_order = frappe.db.get_value("eBay Order", {"ebay_order_number": ebay_order_number}, "name")
	if ebay_order:
		return frappe.get_doc("eBay Order", ebay_order)
	return None
