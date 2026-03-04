frappe.listview_settings['eBay Price Comparison'] = {
	onload: function(listview) {
		// Add custom CSS for list view
		const style = document.createElement('style');
		style.id = 'ebay-price-comparison-list-style';

		// Remove existing style if present
		const existing = document.getElementById('ebay-price-comparison-list-style');
		if (existing) existing.remove();

		style.textContent = `
			/* List View: item_name is 4th column (checkbox, name, item_code, item_name) */
			[data-doctype="eBay Price Comparison"] .list-row-col:nth-child(4) {
				white-space: normal !important;
				word-wrap: break-word !important;
				overflow-wrap: break-word !important;
				min-width: 280px !important;
				flex: 2 !important;
			}

			[data-doctype="eBay Price Comparison"] .list-row-col:nth-child(4) * {
				white-space: normal !important;
				overflow: visible !important;
				text-overflow: unset !important;
			}

			/* Ensure row height adjusts */
			[data-doctype="eBay Price Comparison"] .list-row {
				height: auto !important;
				min-height: 40px !important;
			}

			[data-doctype="eBay Price Comparison"] .list-row-container {
				align-items: flex-start !important;
			}

			/* Report View: Color badges for price_position */
			.price-position-below {
				background-color: #e4f5e9 !important;
				color: #38a169 !important;
				padding: 2px 8px;
				border-radius: 4px;
				font-weight: 500;
			}
			.price-position-at {
				background-color: #e3f2fd !important;
				color: #1976d2 !important;
				padding: 2px 8px;
				border-radius: 4px;
				font-weight: 500;
			}
			.price-position-above {
				background-color: #fff3e0 !important;
				color: #f57c00 !important;
				padding: 2px 8px;
				border-radius: 4px;
				font-weight: 500;
			}
			.price-position-nomatch {
				background-color: #f5f5f5 !important;
				color: #757575 !important;
				padding: 2px 8px;
				border-radius: 4px;
				font-weight: 500;
			}
		`;
		document.head.appendChild(style);
	},

	// Format price_position field with colors in Report View
	formatters: {
		price_position: function(value) {
			if (value === "Below Market") {
				return `<span class="price-position-below">${value}</span>`;
			} else if (value === "At Market") {
				return `<span class="price-position-at">${value}</span>`;
			} else if (value === "Above Market") {
				return `<span class="price-position-above">${value}</span>`;
			} else if (value === "No Match") {
				return `<span class="price-position-nomatch">${value}</span>`;
			}
			return value || '';
		}
	},

	get_indicator: function(doc) {
		// Color indicator for List View
		if (doc.price_position === "Below Market") {
			return [__("Below Market"), "green", "price_position,=,Below Market"];
		} else if (doc.price_position === "At Market") {
			return [__("At Market"), "blue", "price_position,=,At Market"];
		} else if (doc.price_position === "Above Market") {
			return [__("Above Market"), "orange", "price_position,=,Above Market"];
		} else if (doc.price_position === "No Match") {
			return [__("No Match"), "gray", "price_position,=,No Match"];
		}
		return [__("Unknown"), "gray"];
	}
};
