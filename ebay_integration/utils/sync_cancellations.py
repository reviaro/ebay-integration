"""
eBay Cancellation and Refund Sync Module

This module handles synchronization of cancellations and refunds from eBay to ERPNext.
It creates eBay Refund records and optionally generates Credit Notes and Return Delivery Notes.

Key Functions:
- sync_cancellations_and_refunds(): Main scheduler function (runs every 6 hours)
- check_order_status_changes(): Checks existing orders for status changes
- process_cancellation(): Handles cancelled orders from eBay
- process_refund(): Handles refund transactions from eBay Finances API
- create_credit_note(): Creates Credit Note for refunds
- create_return_delivery_note(): Creates Return Delivery Note for shipped items

Usage:
    # Scheduled (via hooks.py):
    scheduler_events = {
        "cron": {
            "0 */6 * * *": ["ebay_integration.utils.sync_cancellations.sync_cancellations_and_refunds"]
        }
    }

    # Manual trigger:
    bench --site [site] execute ebay_integration.utils.sync_cancellations.sync_cancellations_and_refunds
"""

import frappe
from frappe.utils import nowdate, now_datetime, flt, getdate
from frappe.model.mapper import get_mapped_doc


def sync_cancellations_and_refunds(days_back=None):
    """Main sync function for cancellations and refunds - called by scheduler.

    This function:
    1. Checks if refund sync is enabled in eBay Settings
    2. Fetches cancellation requests from eBay Fulfillment API
    3. Fetches refund transactions from eBay Finances API
    4. Creates/updates eBay Refund records
    5. Optionally creates Credit Notes and Return Delivery Notes

    Args:
        days_back: Override the configured refund_sync_days setting

    Returns:
        dict: Summary of processing results with keys:
            - cancellations_processed
            - refunds_processed
            - credit_notes_created
            - return_dns_created
            - errors
    """
    # Check if sync is enabled
    settings = frappe.get_single("eBay Settings")
    if not settings.enable_refund_sync:
        return {"message": "Refund sync is disabled in eBay Settings"}

    # Use configured days or override
    if days_back is None:
        days_back = settings.refund_sync_days or 30

    results = {
        "cancellations_processed": 0,
        "refunds_processed": 0,
        "credit_notes_created": 0,
        "return_dns_created": 0,
        "errors": 0
    }

    try:
        from ebay_integration.utils.ebay_api import eBayWrapper
        ebay = eBayWrapper()

        # Step 1: Process cancellation requests
        log_sync_result("sync_cancellations", "Info", f"Fetching cancellations for last {days_back} days...")

        cancelled_orders = ebay.get_cancellation_requests(days_back=days_back)
        for order_data in cancelled_orders:
            try:
                if process_cancellation(order_data, settings.auto_process_cancellations):
                    results["cancellations_processed"] += 1
            except Exception as e:
                results["errors"] += 1
                frappe.log_error(
                    message=f"Error processing cancellation for order {order_data.get('orderId')}: {str(e)}",
                    title="eBay Cancellation Sync Error"
                )

        # Step 2: Process refund transactions from Finances API
        log_sync_result("sync_cancellations", "Info", f"Fetching refund transactions for last {days_back} days...")

        refund_transactions = ebay.get_refund_transactions(days_back=days_back)
        for transaction in refund_transactions:
            try:
                processed, cn_created, rdn_created = process_refund(
                    transaction,
                    settings.auto_process_cancellations
                )
                if processed:
                    results["refunds_processed"] += 1
                if cn_created:
                    results["credit_notes_created"] += 1
                if rdn_created:
                    results["return_dns_created"] += 1
            except Exception as e:
                results["errors"] += 1
                frappe.log_error(
                    message=f"Error processing refund {transaction.get('transactionId')}: {str(e)}",
                    title="eBay Refund Sync Error"
                )

        # Step 3: Check for status changes on recent orders
        check_order_status_changes(days_back, ebay)

        frappe.db.commit()

        # Log summary
        log_sync_result(
            "sync_cancellations",
            "Success",
            f"Processed {results['cancellations_processed']} cancellations, "
            f"{results['refunds_processed']} refunds, "
            f"{results['errors']} errors"
        )

        return results

    except Exception as e:
        log_sync_result("sync_cancellations", "Error", str(e))
        frappe.log_error(message=str(e), title="eBay Cancellation/Refund Sync Error")
        return results


def check_order_status_changes(days_back, ebay):
    """Check existing Sales Orders for cancellation/refund status changes.

    This function queries Sales Orders that have eBay order IDs and checks
    their current status on eBay to detect any changes.

    Args:
        days_back: Number of days to look back for orders
        ebay: eBayWrapper instance for API calls
    """
    try:
        # Get recent Sales Orders with eBay order IDs
        orders = frappe.db.sql("""
            SELECT name, po_no, ebay_cancellation_status, ebay_refund_status
            FROM `tabSales Order`
            WHERE po_no IS NOT NULL
            AND po_no != ''
            AND creation >= DATE_SUB(NOW(), INTERVAL %s DAY)
            AND docstatus = 1
        """, (days_back,), as_dict=True)

        for so in orders:
            try:
                # Fetch current order status from eBay
                order_data = ebay.get_order(so.po_no)
                if not order_data:
                    continue

                # Check for cancellation status changes
                cancel_status = order_data.get("cancelStatus", {})
                cancel_state = cancel_status.get("cancelState", "")

                new_cancellation_status = map_cancellation_state(cancel_state)

                # Update if changed
                if new_cancellation_status != so.ebay_cancellation_status:
                    frappe.db.set_value("Sales Order", so.name, {
                        "ebay_cancellation_status": new_cancellation_status,
                        "ebay_last_sync": now_datetime()
                    }, update_modified=False)

            except Exception as e:
                frappe.log_error(
                    message=f"Error checking order status for {so.name}: {str(e)}",
                    title="eBay Order Status Check Error"
                )

    except Exception as e:
        frappe.log_error(message=str(e), title="eBay Order Status Check Error")


def map_cancellation_state(cancel_state):
    """Map eBay cancellation state to ERPNext status.

    Args:
        cancel_state: eBay cancelState value

    Returns:
        str: Mapped status for ebay_cancellation_status field
    """
    state_mapping = {
        "": "None Requested",
        "NONE_REQUESTED": "None Requested",
        "IN_PROGRESS": "In Progress",
        "CANCELED": "Canceled",
        "CLOSED": "Canceled"
    }
    return state_mapping.get(cancel_state, "None Requested")


def process_cancellation(order_data, auto_process=False):
    """Process a cancelled order from eBay.

    Creates an eBay Refund record for the cancellation and optionally
    creates Credit Note and Return Delivery Note.

    Args:
        order_data: Order data from eBay Fulfillment API
        auto_process: If True, automatically create Credit Note and Return DN

    Returns:
        bool: True if successfully processed, False otherwise
    """
    ebay_order_id = order_data.get("orderId")
    if not ebay_order_id:
        return False

    cancel_status = order_data.get("cancelStatus", {})
    cancel_state = cancel_status.get("cancelState", "")

    # Only process if actually cancelled
    if cancel_state not in ["CANCELED", "CLOSED"]:
        # Update status on Sales Order but don't create refund record
        so_name = frappe.db.get_value("Sales Order", {"po_no": ebay_order_id}, "name")
        if so_name:
            frappe.db.set_value("Sales Order", so_name, {
                "ebay_cancellation_status": map_cancellation_state(cancel_state),
                "ebay_last_sync": now_datetime()
            }, update_modified=False)
        return False

    # Find linked Sales Order
    so_name = frappe.db.get_value("Sales Order", {"po_no": ebay_order_id}, "name")
    if not so_name:
        log_sync_result("process_cancellation", "Warning",
                        f"No Sales Order found for eBay order {ebay_order_id}")
        return False

    # Get cancellation details
    cancel_requests = cancel_status.get("cancelRequests", [])
    cancel_reason = ""
    cancel_date = now_datetime()

    if cancel_requests:
        latest_request = cancel_requests[-1]
        cancel_reason = latest_request.get("cancelReason", "")
        cancel_date_str = latest_request.get("cancelCompletedDate") or latest_request.get("cancelRequestDate")
        if cancel_date_str:
            try:
                cancel_date = getdate(cancel_date_str[:10])
            except Exception:
                cancel_date = now_datetime()

    # Get refund amount from pricing summary
    pricing_summary = order_data.get("pricingSummary", {})
    total_info = pricing_summary.get("total", {})
    refund_amount = flt(total_info.get("value", 0))

    # Check if eBay Refund record already exists
    existing_refund = frappe.db.exists("eBay Refund", {
        "ebay_order_id": ebay_order_id,
        "refund_type": "Cancellation Refund"
    })

    if existing_refund:
        # Update existing record
        refund_doc = frappe.get_doc("eBay Refund", existing_refund)
        refund_doc.refund_status = "Processed" if auto_process else "Pending"
        refund_doc.save(ignore_permissions=True)
    else:
        # Create new eBay Refund record
        refund_doc = frappe.get_doc({
            "doctype": "eBay Refund",
            "ebay_order_id": ebay_order_id,
            "sales_order": so_name,
            "refund_type": "Cancellation Refund",
            "refund_amount": refund_amount,
            "refund_reason": cancel_reason,
            "refund_date": cancel_date,
            "refund_status": "Pending"
        })
        refund_doc.insert(ignore_permissions=True)

    # Update Sales Order cancellation status
    frappe.db.set_value("Sales Order", so_name, {
        "ebay_cancellation_status": "Canceled",
        "ebay_last_sync": now_datetime()
    }, update_modified=False)

    # Auto-process if enabled
    if auto_process and refund_doc.refund_status == "Pending":
        try:
            # Create Credit Note
            credit_note = create_credit_note(refund_doc)
            if credit_note:
                refund_doc.credit_note = credit_note
                refund_doc.refund_status = "Processed"
                refund_doc.processed_date = now_datetime()
                refund_doc.save(ignore_permissions=True)
        except Exception as e:
            frappe.log_error(
                message=f"Error auto-processing cancellation for {ebay_order_id}: {str(e)}",
                title="eBay Auto-Process Cancellation Error"
            )

    _safe_update_so_refund_status(so_name, ebay_order_id)

    frappe.db.commit()
    return True


def process_refund(transaction, auto_process=False):
    """Process a refund transaction from eBay Finances API.

    Creates an eBay Refund record and optionally creates Credit Note
    and Return Delivery Note.

    Args:
        transaction: Refund transaction data from eBay Finances API
        auto_process: If True, automatically create Credit Note and Return DN

    Returns:
        tuple: (processed: bool, credit_note_created: bool, return_dn_created: bool)
    """
    transaction_id = transaction.get("transactionId")
    if not transaction_id:
        return False, False, False

    # Check if already processed
    existing = frappe.db.exists("eBay Refund", {"ebay_refund_id": transaction_id})
    if existing:
        return False, False, False

    # Extract order ID from references
    order_id = None
    references = transaction.get("references", [])
    for ref in references:
        if ref.get("referenceType") == "ORDER_ID":
            order_id = ref.get("referenceId")
            break

    if not order_id:
        # Try to get from orderId field directly
        order_id = transaction.get("orderId")

    if not order_id:
        log_sync_result("process_refund", "Warning",
                        f"No order ID found for refund transaction {transaction_id}")
        return False, False, False

    # Find linked Sales Order
    so_name = frappe.db.get_value("Sales Order", {"po_no": order_id}, "name")
    if not so_name:
        log_sync_result("process_refund", "Warning",
                        f"No Sales Order found for eBay order {order_id}")
        return False, False, False

    # --- Deduplicate against a cancellation that already produced this refund ---
    # process_cancellation creates an eBay Refund (refund_type "Cancellation Refund")
    # with no ebay_refund_id. The Finances API later reports the same money as a
    # REFUND transaction. Link the transaction to the existing record instead of
    # creating a duplicate eBay Refund (and a second Credit Note / stock return).
    existing_cancellation = frappe.db.get_value("eBay Refund", {
        "ebay_order_id": order_id,
        "refund_type": "Cancellation Refund",
        "ebay_refund_id": ["in", ["", None]],
    }, "name")
    if existing_cancellation:
        refund_doc = frappe.get_doc("eBay Refund", existing_cancellation)
        refund_doc.ebay_refund_id = transaction_id
        refund_doc.save(ignore_permissions=True)
        frappe.db.commit()
        log_sync_result(
            "process_refund", "Info",
            f"Linked Finances refund {transaction_id} to existing cancellation "
            f"refund for order {order_id} (no duplicate created)"
        )
        _safe_update_so_refund_status(so_name, order_id)
        frappe.db.commit()
        return False, False, False

    # Extract refund details
    amount_info = transaction.get("amount", {})
    refund_amount = abs(flt(amount_info.get("value", 0)))  # Refunds are negative in API

    # Determine refund type based on transaction memo or reason
    transaction_memo = transaction.get("transactionMemo", "")
    refund_type = determine_refund_type(transaction_memo, transaction)

    # Parse refund date
    refund_date = now_datetime()
    transaction_date = transaction.get("transactionDate")
    if transaction_date:
        try:
            refund_date = getdate(transaction_date[:10])
        except Exception:
            refund_date = now_datetime()

    # Create eBay Refund record
    refund_doc = frappe.get_doc({
        "doctype": "eBay Refund",
        "ebay_order_id": order_id,
        "ebay_refund_id": transaction_id,
        "sales_order": so_name,
        "refund_type": refund_type,
        "refund_amount": refund_amount,
        "refund_reason": transaction_memo,
        "refund_date": refund_date,
        "refund_status": "Pending"
    })
    refund_doc.insert(ignore_permissions=True)

    credit_note_created = False
    return_dn_created = False

    # Auto-process if enabled
    if auto_process:
        try:
            # Check if we need a Return Delivery Note (for shipped items)
            if should_create_return_dn(so_name, refund_type):
                return_dn = create_return_delivery_note(refund_doc)
                if return_dn:
                    refund_doc.return_delivery_note = return_dn
                    return_dn_created = True

            # Create Credit Note
            credit_note = create_credit_note(refund_doc)
            if credit_note:
                refund_doc.credit_note = credit_note
                credit_note_created = True

            refund_doc.refund_status = "Processed"
            refund_doc.processed_date = now_datetime()
            refund_doc.save(ignore_permissions=True)

        except Exception as e:
            refund_doc.notes = f"Auto-processing failed: {str(e)}"
            refund_doc.save(ignore_permissions=True)
            frappe.log_error(
                message=f"Error auto-processing refund {transaction_id}: {str(e)}",
                title="eBay Auto-Process Refund Error"
            )

    _safe_update_so_refund_status(so_name, order_id)

    frappe.db.commit()
    return True, credit_note_created, return_dn_created


def determine_refund_type(memo, transaction):
    """Determine the refund type based on transaction memo and details.

    Args:
        memo: Transaction memo from eBay
        transaction: Full transaction data

    Returns:
        str: Refund type matching eBay Refund DocType options
    """
    memo_lower = memo.lower() if memo else ""

    if "return" in memo_lower:
        return "Return Refund"
    elif "shipping" in memo_lower:
        return "Shipping Refund"
    elif "goodwill" in memo_lower or "courtesy" in memo_lower:
        return "Goodwill Refund"
    elif "cancel" in memo_lower:
        return "Cancellation Refund"
    elif "partial" in memo_lower:
        return "Partial Refund"
    else:
        return "Full Refund"


def should_create_return_dn(so_name, refund_type):
    """Determine if a Return Delivery Note should be created.

    A Return DN is needed when:
    1. The order has been shipped (Delivery Note exists and is submitted)
    2. The refund type indicates a return

    Args:
        so_name: Sales Order name
        refund_type: Type of refund

    Returns:
        bool: True if Return DN should be created
    """
    # Only create Return DN for return-type refunds
    if refund_type not in ["Return Refund", "Full Refund", "Cancellation Refund"]:
        return False

    # Check if there's a submitted Delivery Note
    dn_exists = frappe.db.exists("Delivery Note Item", {
        "against_sales_order": so_name,
        "docstatus": 1
    })

    return bool(dn_exists)


def create_credit_note(refund_doc):
    """Create a Credit Note (Sales Invoice with is_return=1) for a refund.

    The Credit Note is created against the original Sales Invoice if one exists,
    otherwise it's created as a standalone return invoice.

    Args:
        refund_doc: eBay Refund document

    Returns:
        str: Name of created Credit Note, or None if creation failed
    """
    try:
        # Find the original Sales Invoice
        si_name = refund_doc.sales_invoice
        if not si_name:
            # Try to find from Sales Order
            si_name = frappe.db.get_value(
                "Sales Invoice Item",
                {"sales_order": refund_doc.sales_order, "docstatus": 1},
                "parent"
            )

        if not si_name:
            log_sync_result("create_credit_note", "Warning",
                            f"No Sales Invoice found for {refund_doc.sales_order}")
            return None

        # Get original invoice
        original_si = frappe.get_doc("Sales Invoice", si_name)

        # Calculate what percentage of the original invoice this refund represents
        refund_percentage = flt(refund_doc.refund_amount) / flt(original_si.grand_total) if original_si.grand_total else 1

        # Create Credit Note using mapping
        credit_note = get_mapped_doc("Sales Invoice", si_name, {
            "Sales Invoice": {
                "doctype": "Sales Invoice",
                "validation": {"docstatus": ["=", 1]}
            },
            "Sales Invoice Item": {
                "doctype": "Sales Invoice Item"
            },
            "Sales Taxes and Charges": {
                "doctype": "Sales Taxes and Charges"
            }
        }, ignore_permissions=True)

        # Set as return/credit note
        credit_note.is_return = 1
        credit_note.return_against = si_name
        credit_note.update_outstanding_for_self = 1
        credit_note.update_billed_amount_in_sales_order = 1

        # Adjust quantities/amounts if partial refund
        if refund_percentage < 0.95:  # Not a full refund
            for item in credit_note.items:
                item.qty = -abs(flt(item.qty) * refund_percentage)
        else:
            # Full refund - negate all quantities
            for item in credit_note.items:
                item.qty = -abs(item.qty)

        # Add reference to eBay refund
        credit_note.po_no = f"Refund: {refund_doc.ebay_order_id}"

        credit_note.insert(ignore_permissions=True)
        credit_note.submit()

        log_sync_result("create_credit_note", "Success",
                        f"Created Credit Note {credit_note.name} for {refund_doc.name}")

        return credit_note.name

    except Exception as e:
        frappe.log_error(
            message=f"Error creating Credit Note for {refund_doc.name}: {str(e)}",
            title="eBay Credit Note Creation Error"
        )
        return None


def create_return_delivery_note(refund_doc):
    """Create a Return Delivery Note (is_return=1) for returned items.

    This handles stock adjustment when items are returned to inventory.

    Args:
        refund_doc: eBay Refund document

    Returns:
        str: Name of created Return Delivery Note, or None if creation failed
    """
    try:
        # Find the original Delivery Note
        dn_item = frappe.db.get_value(
            "Delivery Note Item",
            {"against_sales_order": refund_doc.sales_order, "docstatus": 1},
            ["parent", "warehouse"],
            as_dict=True
        )

        if not dn_item:
            log_sync_result("create_return_delivery_note", "Warning",
                            f"No Delivery Note found for {refund_doc.sales_order}")
            return None

        dn_name = dn_item.parent

        # Get original Delivery Note
        original_dn = frappe.get_doc("Delivery Note", dn_name)

        # Calculate refund percentage
        so_total = frappe.db.get_value("Sales Order", refund_doc.sales_order, "grand_total") or 0
        refund_percentage = flt(refund_doc.refund_amount) / flt(so_total) if so_total else 1

        # Create Return Delivery Note using mapping
        return_dn = get_mapped_doc("Delivery Note", dn_name, {
            "Delivery Note": {
                "doctype": "Delivery Note",
                "validation": {"docstatus": ["=", 1]}
            },
            "Delivery Note Item": {
                "doctype": "Delivery Note Item",
                "field_map": {
                    "name": "dn_detail",
                    "parent": "against_sales_order"
                }
            }
        }, ignore_permissions=True)

        # Set as return
        return_dn.is_return = 1
        return_dn.return_against = dn_name

        # Adjust quantities if partial refund
        if refund_percentage < 0.95:
            for item in return_dn.items:
                item.qty = -abs(flt(item.qty) * refund_percentage)
        else:
            for item in return_dn.items:
                item.qty = -abs(item.qty)

        return_dn.insert(ignore_permissions=True)
        return_dn.submit()

        log_sync_result("create_return_delivery_note", "Success",
                        f"Created Return DN {return_dn.name} for {refund_doc.name}")

        return return_dn.name

    except Exception as e:
        frappe.log_error(
            message=f"Error creating Return Delivery Note for {refund_doc.name}: {str(e)}",
            title="eBay Return DN Creation Error"
        )
        return None


def _safe_update_so_refund_status(so_name, ebay_order_id):
    """Best-effort wrapper: never let a refund-summary update abort the caller.

    The Sales Order summary fields are informational; if computing them fails we
    log it and move on rather than rolling back the refund record we just made.
    """
    try:
        update_sales_order_refund_status(so_name, ebay_order_id)
    except Exception as e:
        frappe.log_error(
            message=f"Could not update SO refund status for {so_name}: {e}",
            title="eBay Refund Status Update Error",
        )


def update_sales_order_refund_status(so_name, ebay_order_id):
    """Aggregate all eBay Refund records for an order onto the Sales Order.

    Writes the read-only custom fields ``ebay_refund_amount`` and
    ``ebay_refund_status`` (None Refund / Partial Refund / Full Refund) so the
    refund state is visible directly on the Sales Order, not only on the
    separate eBay Refund records.

    Args:
        so_name: Sales Order name
        ebay_order_id: The eBay order ID used to find related refunds
    """
    if not so_name:
        return

    refunds = frappe.get_all(
        "eBay Refund",
        filters={"ebay_order_id": ebay_order_id},
        fields=["refund_amount"],
    )
    total_refunded = sum(flt(r.get("refund_amount")) for r in refunds)

    grand_total = flt(frappe.db.get_value("Sales Order", so_name, "grand_total"))

    if total_refunded <= 0:
        status = "No Refund"
    elif grand_total and total_refunded >= grand_total - 0.01:
        status = "Full Refund"
    else:
        # Either a genuine partial refund, or grand_total is unknown so we
        # cannot prove it is a full refund — treat as partial to stay safe.
        status = "Partial Refund"

    frappe.db.set_value("Sales Order", so_name, {
        "ebay_refund_amount": total_refunded,
        "ebay_refund_status": status,
        "ebay_last_sync": now_datetime(),
    }, update_modified=False)


def log_sync_result(method, status, message, details=None):
    """Helper to log sync results to eBay Log doctype.

    Args:
        method: The method/operation name
        status: Success, Error, Warning, Info
        message: Short message (truncated to 140 chars)
        details: Full details (optional)
    """
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
        # If logging fails, print to console
        print(f"eBay Sync [{status}] - {method}: {message}")


@frappe.whitelist()
def manual_sync_cancellations(days_back=30):
    """Whitelist function for manual trigger from UI.

    This is called from the eBay Settings page when user clicks
    the "Sync Cancellations" button.

    Args:
        days_back: Number of days to look back (default: 30)

    Returns:
        str: Summary message of sync results
    """
    days_back = int(days_back)
    result = sync_cancellations_and_refunds(days_back=days_back)

    if isinstance(result, dict) and "message" in result:
        return result["message"]

    return (
        f"Processed {result.get('cancellations_processed', 0)} cancellations, "
        f"{result.get('refunds_processed', 0)} refunds. "
        f"Created {result.get('credit_notes_created', 0)} Credit Notes, "
        f"{result.get('return_dns_created', 0)} Return Delivery Notes. "
        f"Errors: {result.get('errors', 0)}"
    )


def get_refund_summary_for_order(ebay_order_id):
    """Get refund summary for a specific eBay order.

    Useful for displaying refund information on Sales Order forms.

    Args:
        ebay_order_id: The eBay order ID

    Returns:
        dict: Summary with total_refunded, refund_count, refund_records
    """
    refunds = frappe.get_all(
        "eBay Refund",
        filters={"ebay_order_id": ebay_order_id},
        fields=["name", "refund_type", "refund_amount", "refund_status", "refund_date"]
    )

    total_refunded = sum(flt(r.refund_amount) for r in refunds if r.refund_status == "Processed")

    return {
        "total_refunded": total_refunded,
        "refund_count": len(refunds),
        "refund_records": refunds
    }
