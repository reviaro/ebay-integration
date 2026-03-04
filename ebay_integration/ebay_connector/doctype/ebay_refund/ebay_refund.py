# Copyright (c) 2024, Antigravity and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class eBayRefund(Document):
	def validate(self):
		self.validate_refund_amount()

	def validate_refund_amount(self):
		if self.refund_amount and self.refund_amount < 0:
			frappe.throw("Refund amount cannot be negative")

	def before_save(self):
		# Auto-link Sales Order if we have eBay Order ID but no SO linked
		if self.ebay_order_id and not self.sales_order:
			so = frappe.db.get_value("Sales Order", {"po_no": self.ebay_order_id}, "name")
			if so:
				self.sales_order = so

		# Auto-link Sales Invoice if we have Sales Order
		if self.sales_order and not self.sales_invoice:
			si = frappe.db.get_value("Sales Invoice Item",
				{"sales_order": self.sales_order, "docstatus": 1},
				"parent")
			if si:
				self.sales_invoice = si

	def on_update(self):
		# Update Sales Order refund tracking fields
		if self.sales_order:
			self.update_sales_order_refund_status()

	def update_sales_order_refund_status(self):
		"""Update the refund tracking fields on the linked Sales Order"""
		if not self.sales_order:
			return

		# Calculate total refunded for this Sales Order
		total_refunded = frappe.db.sql("""
			SELECT COALESCE(SUM(refund_amount), 0) as total
			FROM `tabeBay Refund`
			WHERE sales_order = %s AND refund_status = 'Processed'
		""", self.sales_order, as_dict=True)[0].total

		# Get Sales Order total
		so_total = frappe.db.get_value("Sales Order", self.sales_order, "grand_total") or 0

		# Determine refund status
		if total_refunded <= 0:
			refund_status = "No Refund"
		elif total_refunded >= so_total * 0.95:  # 95% threshold for full refund
			refund_status = "Full Refund"
		else:
			refund_status = "Partial Refund"

		# Update Sales Order custom fields
		frappe.db.set_value("Sales Order", self.sales_order, {
			"ebay_refund_status": refund_status,
			"ebay_refund_amount": total_refunded
		}, update_modified=False)
