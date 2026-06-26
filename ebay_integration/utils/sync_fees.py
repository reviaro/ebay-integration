"""
eBay Selling-Fee Sync Module

eBay's final-value / selling fees reduce the seller's payout — they are a selling
expense, not something the buyer pays. This module fetches SALE transactions from
the eBay Finances API and books each order's fee as an expense Journal Entry:

    Dr  eBay Selling Fees (expense)
        Cr  eBay payout / clearing account (the same account the Payment Entry
            debits, so the net cash reconciles to the real eBay payout)

SAFETY: This writes accounting documents. It is gated behind the
``enable_fee_sync`` toggle in eBay Settings, which is OFF by default. Enable it
only after confirming the captured fee on one real order matches the eBay payout
report.

Key functions:
- sync_seller_fees(): scheduler entry point (gated by enable_fee_sync)
- process_fee_transaction(): per-order lookup, dedup, Journal Entry creation
- create_fee_journal_entry(): builds and submits the expense Journal Entry
- extract_fee_from_transaction(): the ONLY function encoding the eBay Finances
  response shape (isolated so a field-path change is a one-line fix)
"""

import frappe
from frappe.utils import nowdate, flt


def extract_fee_from_transaction(transaction):
	"""Return the seller fee for a SALE transaction as a positive float.

	NOTE: This is the single point that depends on the eBay Finances response
	shape (``totalFeeAmount.value``). If the live field differs, fix it here.
	"""
	fee_info = transaction.get("totalFeeAmount") or {}
	return abs(flt(fee_info.get("value", 0)))


def _order_id_from_transaction(transaction):
	"""Resolve the eBay order ID from a transaction (direct field or references)."""
	order_id = transaction.get("orderId")
	if order_id:
		return order_id
	for ref in transaction.get("references", []) or []:
		if ref.get("referenceType") == "ORDER_ID":
			return ref.get("referenceId")
	return None


def get_selling_fee_account(company):
	"""Expense account for eBay selling fees."""
	configured = frappe.db.get_single_value("eBay Settings", "fee_expense_account")
	if configured:
		return configured

	account = frappe.db.get_value("Account", {
		"account_name": "eBay Selling Fees",
		"company": company,
		"is_group": 0
	}, "name")
	if account:
		return account

	account = frappe.db.get_value("Account", {
		"account_name": ["like", "%Fee%"],
		"company": company,
		"root_type": "Expense",
		"is_group": 0
	}, "name")
	if account:
		return account

	return frappe.db.get_value("Company", company, "default_expense_account")


def get_fee_clearing_account(company):
	"""Account the fee is credited to — the same account the Payment Entry debits
	(eBay payout account), so the net cash reconciles to the real payout."""
	configured = frappe.db.get_single_value("eBay Settings", "payment_account")
	if configured:
		return configured

	account = frappe.db.get_value("Account", {
		"account_type": "Cash", "company": company, "is_group": 0
	}, "name")
	if account:
		return account

	return frappe.db.get_value("Account", {
		"account_type": "Bank", "company": company, "is_group": 0
	}, "name")


def create_fee_journal_entry(order_id, fee_amount, company):
	"""Build and submit an expense Journal Entry for an eBay selling fee.

	Returns:
		str: the Journal Entry name.
	"""
	posting_date = nowdate()
	expense_account = get_selling_fee_account(company)
	clearing_account = get_fee_clearing_account(company)
	cost_center = frappe.db.get_value("Company", company, "cost_center")

	je = frappe.get_doc({
		"doctype": "Journal Entry",
		"voucher_type": "Journal Entry",
		"company": company,
		"posting_date": posting_date,
		"cheque_no": f"EBAY-FEE-{order_id}",
		"cheque_date": posting_date,
		"user_remark": f"eBay selling fee for order {order_id}",
		"accounts": [
			{
				"account": expense_account,
				"debit_in_account_currency": fee_amount,
				"cost_center": cost_center,
			},
			{
				"account": clearing_account,
				"credit_in_account_currency": fee_amount,
			},
		],
	})
	je.insert(ignore_permissions=True)
	je.submit()
	return je.name


def process_fee_transaction(transaction):
	"""Book a single SALE transaction's eBay fee as a Journal Entry.

	Returns:
		bool: True if a Journal Entry was created, False otherwise.
	"""
	order_id = _order_id_from_transaction(transaction)
	if not order_id:
		return False

	fee_amount = extract_fee_from_transaction(transaction)
	if fee_amount <= 0:
		return False

	so_name = frappe.db.get_value("Sales Order", {"po_no": order_id}, "name")
	if not so_name:
		log_sync_result("process_fee_transaction", "Warning",
						f"No Sales Order found for eBay order {order_id}")
		return False

	company = frappe.db.get_value("Sales Order", so_name, "company")

	# Only handle same-currency fees; cross-currency needs an exchange rate.
	fee_currency = (transaction.get("totalFeeAmount") or {}).get("currency")
	company_currency = frappe.db.get_value("Company", company, "default_currency")
	if fee_currency and company_currency and fee_currency != company_currency:
		log_sync_result("process_fee_transaction", "Warning",
						f"Skipped fee for {order_id}: currency {fee_currency} "
						f"!= company currency {company_currency}")
		return False

	# Dedup: one fee Journal Entry per order
	if frappe.db.exists("Journal Entry", {
		"cheque_no": f"EBAY-FEE-{order_id}", "docstatus": 1
	}):
		return False

	create_fee_journal_entry(order_id, fee_amount, company)

	# Store the fee on the Sales Order for visibility
	frappe.db.set_value("Sales Order", so_name,
						{"ebay_selling_fee": fee_amount}, update_modified=False)
	frappe.db.commit()
	return True


def sync_seller_fees(days_back=None):
	"""Scheduler entry point — fetch SALE transactions and book fee Journal Entries.

	Gated by the ``enable_fee_sync`` toggle (OFF by default).
	"""
	settings = frappe.get_single("eBay Settings")
	if not settings.enable_fee_sync:
		return {"message": "Fee sync is disabled in eBay Settings", "fees_processed": 0}

	if days_back is None:
		days_back = settings.refund_sync_days or 30

	results = {"fees_processed": 0, "errors": 0}

	try:
		from ebay_integration.utils.ebay_api import eBayWrapper
		ebay = eBayWrapper()

		for transaction in ebay.get_sale_transactions(days_back=days_back):
			try:
				if process_fee_transaction(transaction):
					results["fees_processed"] += 1
			except Exception as e:
				results["errors"] += 1
				frappe.log_error(
					message=f"Error processing fee for {transaction.get('orderId')}: {e}",
					title="eBay Fee Sync Error"
				)

		frappe.db.commit()
		log_sync_result("sync_seller_fees", "Success",
						f"Posted {results['fees_processed']} fee Journal Entries, "
						f"{results['errors']} errors")
		return results

	except Exception as e:
		log_sync_result("sync_seller_fees", "Error", str(e))
		frappe.log_error(message=str(e), title="eBay Fee Sync Error")
		return results


@frappe.whitelist()
def manual_sync_fees(days_back=30):
	"""Manual trigger from the eBay Settings page."""
	days_back = int(days_back)
	result = sync_seller_fees(days_back=days_back)
	if isinstance(result, dict) and "message" in result:
		return result["message"]
	return (f"Posted {result.get('fees_processed', 0)} fee Journal Entries, "
			f"{result.get('errors', 0)} errors")


def log_sync_result(method, status, message, details=None):
	"""Helper to log sync results to the eBay Log doctype."""
	try:
		frappe.get_doc({
			"doctype": "eBay Log",
			"method": method,
			"status": status,
			"message": message or "",
			"details": details or ""
		}).insert(ignore_permissions=True)
		frappe.db.commit()
	except Exception:
		print(f"eBay Fee Sync [{status}] - {method}: {message}")
