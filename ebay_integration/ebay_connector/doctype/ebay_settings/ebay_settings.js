frappe.ui.form.on('eBay Settings', {
	authorize_button: function (frm) {
		if (frm.is_dirty()) {
			frappe.msgprint("Please Save first.");
			return;
		}

		frappe.call({
			method: "ebay_integration.ebay_connector.doctype.ebay_settings.ebay_settings.get_authorize_url",
			args: {
				"doc_name": frm.doc.name
			},
			callback: function (r) {
				if (r.message) {
					window.open(r.message, '_blank');
					frappe.msgprint("Please login to eBay, approve the app, and then copy the 'code' parameter from the redirect URL into the 'Auth Code' field here. Then click 'Generate Token'.");
				}
			}
		});
	},

	refresh: function (frm) {
		// Generate Token button - show when auth_code exists but no valid token
		if (frm.doc.auth_code) {
			frm.add_custom_button('Generate Token', function () {
				frappe.call({
					method: "ebay_integration.ebay_connector.doctype.ebay_settings.ebay_settings.generate_token",
					args: {
						"doc_name": frm.doc.name
					},
					callback: function (r) {
						frm.reload_doc();
						frappe.msgprint("Token Generated Successfully!");
					}
				});
			}, 'Actions');
		}

		// ========================
		// API SYNC BUTTONS
		// ========================

		// Manual Sync Orders button
		frm.add_custom_button('Sync Orders Now', function () {
			frappe.call({
				method: "ebay_integration.ebay_connector.doctype.ebay_settings.ebay_settings.manual_sync_orders",
				freeze: true,
				freeze_message: "Syncing orders from eBay API...",
				callback: function (r) {
					if (r.message) {
						frappe.msgprint(r.message);
					}
				}
			});
		}, 'API Sync');

		// Manual Sync Inventory button
		frm.add_custom_button('Sync Inventory Now', function () {
			frappe.call({
				method: "ebay_integration.ebay_connector.doctype.ebay_settings.ebay_settings.manual_sync_inventory",
				freeze: true,
				freeze_message: "Syncing inventory from eBay API...",
				callback: function (r) {
					if (r.message) {
						frappe.msgprint(r.message);
					}
				}
			});
		}, 'API Sync');

		// Import Historical Orders button
		frm.add_custom_button('Import Historical Orders', function () {
			frappe.prompt({
				label: 'Days Back',
				fieldname: 'days_back',
				fieldtype: 'Int',
				default: 90,
				description: 'Number of days to look back (eBay API typically limits to 90-120 days)'
			}, function (values) {
				frappe.call({
					method: "ebay_integration.ebay_connector.doctype.ebay_settings.ebay_settings.manual_import_historical",
					args: {
						days_back: values.days_back
					},
					freeze: true,
					freeze_message: "Importing historical orders from eBay API...",
					callback: function (r) {
						if (r.message) {
							frappe.msgprint(r.message);
						}
					}
				});
			}, 'Import Historical Orders', 'Import');
		}, 'API Sync');

		// Update Existing Orders (add taxes/shipping to old orders)
		frm.add_custom_button('Update Orders with Taxes', function () {
			frappe.prompt({
				label: 'Days Back',
				fieldname: 'days_back',
				fieldtype: 'Int',
				default: 120,
				description: 'Look back X days and update existing draft orders with tax/shipping data'
			}, function (values) {
				frappe.call({
					method: "ebay_integration.ebay_connector.doctype.ebay_settings.ebay_settings.manual_update_orders",
					args: {
						days_back: values.days_back
					},
					freeze: true,
					freeze_message: "Updating existing orders with tax data...",
					callback: function (r) {
						if (r.message) {
							frappe.msgprint(r.message);
						}
					}
				});
			}, 'Update Orders', 'Update');
		}, 'API Sync');

		// Sync Cancellations and Refunds
		frm.add_custom_button('Sync Cancellations/Refunds', function () {
			frappe.prompt({
				label: 'Days Back',
				fieldname: 'days_back',
				fieldtype: 'Int',
				default: 30,
				description: 'Number of days to look back for cancellations and refunds'
			}, function (values) {
				frappe.call({
					method: "ebay_integration.ebay_connector.doctype.ebay_settings.ebay_settings.manual_sync_cancellations",
					args: {
						days_back: values.days_back
					},
					freeze: true,
					freeze_message: "Syncing cancellations and refunds from eBay...",
					callback: function (r) {
						if (r.message) {
							frappe.msgprint({
								title: 'Cancellation/Refund Sync Complete',
								message: r.message,
								indicator: 'green'
							});
						}
					}
				});
			}, 'Sync Cancellations/Refunds', 'Sync');
		}, 'API Sync');


		// ========================
		// CSV IMPORT BUTTONS
		// ========================

		// Import Orders CSV button
		frm.add_custom_button('Import Orders CSV', function () {
			let d = new frappe.ui.Dialog({
				title: 'Import eBay Orders from CSV',
				fields: [
					{
						fieldtype: 'HTML',
						options: `
							<div class="alert alert-info">
								<strong>Supported CSV Format:</strong><br>
								Upload a CSV file exported from eBay Seller Hub.<br><br>
								<strong>All fields are captured including:</strong>
								<ul style="margin-bottom: 0; padding-left: 20px;">
									<li>Order & Transaction IDs</li>
									<li>Buyer information & addresses</li>
									<li>Item details & variations</li>
									<li>All taxes, fees & charges</li>
									<li>Shipping & tracking info</li>
									<li>eBay program flags</li>
								</ul>
							</div>
						`
					},
					{
						fieldtype: 'Attach',
						fieldname: 'csv_file',
						label: 'CSV File',
						reqd: 1,
						options: {
							restrictions: {
								allowed_file_types: ['.csv']
							}
						}
					},
					{
						fieldtype: 'Check',
						fieldname: 'update_existing',
						label: 'Update existing orders (if found)',
						default: 0
					}
				],
				primary_action_label: 'Import',
				primary_action: function (values) {
					d.hide();
					frappe.call({
						method: "ebay_integration.utils.import_ebay_csv.upload_csv_file",
						args: {
							file_url: values.csv_file,
							update_existing: values.update_existing
						},
						freeze: true,
						freeze_message: "Importing orders from CSV... This may take a while for large files.",
						callback: function (r) {
							if (r.message) {
								show_import_results('Orders CSV Import', r.message);
							}
						}
					});
				}
			});
			d.show();
		}, 'CSV Import');

		// Import Inventory CSV button
		frm.add_custom_button('Import Inventory CSV', function () {
			let d = new frappe.ui.Dialog({
				title: 'Import eBay Inventory from CSV',
				fields: [
					{
						fieldtype: 'HTML',
						options: `
							<div class="alert alert-info">
								<strong>Supported CSV Format:</strong><br>
								Upload a CSV file exported from eBay Seller Hub inventory.<br><br>
								<strong>Fields captured:</strong>
								<ul style="margin-bottom: 0; padding-left: 20px;">
									<li>Item number (handles scientific notation)</li>
									<li>Title & pricing</li>
									<li>Quantity on hand</li>
									<li>Sold quantity</li>
									<li>eBay category</li>
								</ul>
							</div>
						`
					},
					{
						fieldtype: 'Attach',
						fieldname: 'csv_file',
						label: 'CSV File',
						reqd: 1,
						options: {
							restrictions: {
								allowed_file_types: ['.csv']
							}
						}
					},
					{
						fieldtype: 'Check',
						fieldname: 'create_items',
						label: 'Create new items if not found',
						default: 1
					},
					{
						fieldtype: 'Check',
						fieldname: 'update_stock',
						label: 'Update stock quantities (creates Stock Reconciliation)',
						default: 1
					}
				],
				primary_action_label: 'Import',
				primary_action: function (values) {
					d.hide();
					frappe.call({
						method: "ebay_integration.utils.import_inventory_csv.upload_inventory_csv",
						args: {
							file_url: values.csv_file,
							create_items: values.create_items,
							update_stock: values.update_stock
						},
						freeze: true,
						freeze_message: "Importing inventory from CSV... This may take a while for large files.",
						callback: function (r) {
							if (r.message) {
								show_inventory_import_results(r.message);
							}
						}
					});
				}
			});
			d.show();
		}, 'CSV Import');

		// ========================
		// PRICE COMPARISON BUTTONS
		// ========================

		frm.add_custom_button('Retry Failed Orders', function () {
			frappe.call({
				method: "ebay_integration.ebay_connector.doctype.ebay_settings.ebay_settings.retry_failed_orders",
				freeze: true,
				freeze_message: "Retrying stuck orders...",
				callback: function (r) {
					if (r.message) {
						frappe.msgprint({
							title: 'Retry Failed Orders',
							message: r.message,
							indicator: r.message.includes('failed') ? 'orange' : 'green'
						});
					}
				}
			});
		}, 'API Sync');

		frm.add_custom_button('Run Price Comparison', function () {
			frappe.call({
				method: "ebay_integration.utils.price_comparison.manual_price_comparison",
				callback: function (r) {
					if (r.message) {
						frappe.msgprint({
							title: 'Price Comparison',
							indicator: 'blue',
							message: r.message + '<br><br>You will be notified when complete.'
						});
					}
				}
			});
		}, 'API Sync');

		// ========================
		// VIEW BUTTONS
		// ========================

		frm.add_custom_button('View Sync Logs', function () {
			frappe.set_route('List', 'eBay Log');
		}, 'View');

		frm.add_custom_button('View eBay Orders', function () {
			frappe.set_route('List', 'eBay Order');
		}, 'View');

		frm.add_custom_button('View eBay Refunds', function () {
			frappe.set_route('List', 'eBay Refund');
		}, 'View');

		frm.add_custom_button('View Price Comparisons', function () {
			frappe.set_route('List', 'eBay Price Comparison');
		}, 'View');

		// Setup button to create custom fields
		frm.add_custom_button('Create Custom Fields', function () {
			frappe.confirm(
				'This will create custom fields on Sales Order, Item, and Customer doctypes to show eBay data. Continue?',
				function () {
					frappe.call({
						method: "ebay_integration.ebay_connector.custom_fields.setup_custom_fields",
						freeze: true,
						freeze_message: "Creating custom fields...",
						callback: function (r) {
							if (r.message) {
								frappe.msgprint({
									title: 'Success',
									message: r.message + '<br><br>Please reload the page to see the new fields.',
									indicator: 'green'
								});
							}
						}
					});
				}
			);
		}, 'Setup');
	}
});


// Helper function to show import results
function show_import_results(title, msg) {
	let html = `
		<table class="table table-bordered">
			<tr><td><strong>Imported</strong></td><td class="text-right">${msg.imported || 0}</td></tr>
			<tr><td><strong>Updated</strong></td><td class="text-right">${msg.updated || 0}</td></tr>
			<tr><td><strong>Skipped</strong></td><td class="text-right">${msg.skipped || 0}</td></tr>
			<tr><td><strong>Errors</strong></td><td class="text-right">${msg.errors ? msg.errors.length : 0}</td></tr>
		</table>
	`;

	if (msg.messages && msg.messages.length > 0) {
		html += `<div class="alert alert-info"><strong>Messages:</strong><br>${msg.messages.join('<br>')}</div>`;
	}

	if (msg.errors && msg.errors.length > 0) {
		html += `<div class="alert alert-warning"><strong>Errors:</strong><br>`;
		html += msg.errors.slice(0, 15).map(e => `• ${e}`).join('<br>');
		if (msg.errors.length > 15) {
			html += `<br><br><em>... and ${msg.errors.length - 15} more errors. Check Error Log for details.</em>`;
		}
		html += '</div>';
	}

	frappe.msgprint({
		title: title,
		message: html,
		indicator: (msg.errors && msg.errors.length > 0) ? 'orange' : 'green',
		wide: true
	});
}


// Helper function for inventory import results
function show_inventory_import_results(msg) {
	let html = `
		<table class="table table-bordered">
			<tr><td><strong>Items Created</strong></td><td class="text-right">${msg.created || 0}</td></tr>
			<tr><td><strong>Items Updated</strong></td><td class="text-right">${msg.updated || 0}</td></tr>
			<tr><td><strong>Stock Reconciled</strong></td><td class="text-right">${msg.stock_updated || 0}</td></tr>
			<tr><td><strong>Skipped</strong></td><td class="text-right">${msg.skipped || 0}</td></tr>
			<tr><td><strong>Errors</strong></td><td class="text-right">${msg.errors ? msg.errors.length : 0}</td></tr>
		</table>
	`;

	if (msg.messages && msg.messages.length > 0) {
		html += `<div class="alert alert-info"><strong>Messages:</strong><br>${msg.messages.join('<br>')}</div>`;
	}

	if (msg.errors && msg.errors.length > 0) {
		html += `<div class="alert alert-warning"><strong>Errors:</strong><br>`;
		html += msg.errors.slice(0, 15).map(e => `• ${e}`).join('<br>');
		if (msg.errors.length > 15) {
			html += `<br><br><em>... and ${msg.errors.length - 15} more errors. Check Error Log for details.</em>`;
		}
		html += '</div>';
	}

	frappe.msgprint({
		title: 'Inventory CSV Import Complete',
		message: html,
		indicator: (msg.errors && msg.errors.length > 0) ? 'orange' : 'green',
		wide: true
	});
}
