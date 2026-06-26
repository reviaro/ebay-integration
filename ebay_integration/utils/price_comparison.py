import frappe
import time
import traceback
from frappe.utils import flt, now_datetime


def run_price_comparison():
	"""Main function - called by scheduler every 4 days"""
	if not frappe.db.get_single_value("eBay Settings", "sync_enabled"):
		return

	try:
		from ebay_integration.utils.ebay_api import eBayWrapper
		ebay = eBayWrapper()

		# Get all stock items with prices set
		items = get_items_to_compare()

		compared = 0
		total = len(items)
		for i, item in enumerate(items):
			if compare_item_price(ebay, item):
				compared += 1
			# Log progress every 25 items
			if (i + 1) % 25 == 0:
				log_sync_result("price_comparison", "Info", f"Progress: {i+1}/{total} items checked, {compared} compared")
				frappe.db.commit()

		log_sync_result("price_comparison", "Success", f"Compared {compared} of {total} items")
		frappe.db.commit()

	except Exception as e:
		log_sync_result("price_comparison", "Error", str(e))
		frappe.log_error(message=str(e), title="Price Comparison Error")


def get_items_to_compare():
	"""Get items from inventory that have prices set.
	Limited to 200 items per run to avoid API rate limits and long execution times.
	Prioritizes items that haven't been compared recently.
	"""
	# Get items that either haven't been compared or were compared longest ago
	items = frappe.get_all("Item",
		filters={
			"disabled": 0,
			"is_stock_item": 1,
			"standard_rate": [">", 0]
		},
		fields=["name", "item_name", "standard_rate"],
		order_by="modified asc",
		limit_page_length=200
	)
	return items


def compare_item_price(ebay, item):
	"""Compare single item against eBay listings

	Uses a two-pass search strategy:
	1. First search with price floor (25% of your price) to exclude accessories
	2. If too few results, fall back to broader search with post-filtering

	Args:
		ebay: eBayWrapper instance
		item: Item dict with name, item_name, standard_rate

	Returns:
		bool: True if comparison was successful
	"""
	# Extract keywords from item name (simple approach - first 8 words)
	keywords = extract_search_keywords(item.item_name)

	if not keywords:
		return False

	your_price = flt(item.standard_rate)

	# Calculate price floor - exclude items below 25% of your price
	# This filters out accessories, hardware, clips, etc.
	min_price = your_price * 0.25 if your_price > 20 else 5

	# PASS 1: Search with price floor and category filter
	# Category 6028 = "Auto Parts & Accessories"
	results = ebay.search_similar_items(
		keywords,
		condition="USED",
		limit=50,
		category_ids="6028",
		min_price=min_price,
		max_price=your_price * 4  # Cap at 400% to exclude unrelated expensive items
	)

	prices = extract_prices_from_results(results)

	# Rate limit: wait between API calls to avoid eBay throttling
	time.sleep(0.5)

	# PASS 2: If too few results, try without price filter but keep category
	if len(prices) < 5:
		time.sleep(0.5)
		results = ebay.search_similar_items(
			keywords,
			condition="USED",
			limit=50,
			category_ids="6028"
		)
		prices = extract_prices_from_results(results)

	# PASS 3: If still too few, try without category filter (last resort)
	if len(prices) < 3:
		time.sleep(0.5)
		results = ebay.search_similar_items(
			keywords,
			condition="USED",
			limit=50
		)
		prices = extract_prices_from_results(results)

	if not prices:
		# No results at all - record as no match
		create_comparison_record(
			item, keywords,
			{"lowest": 0, "highest": 0, "average": 0, "median": 0, "count": 0},
			"No Match", 0
		)
		return True

	# Calculate statistics with outlier filtering
	stats = calculate_price_stats(prices, your_price)

	# Sanity check: if average is less than 30% of your price after filtering,
	# the search likely matched wrong items (accessories instead of main part)
	if stats["average"] < your_price * 0.30:
		create_comparison_record(
			item, keywords,
			{"lowest": 0, "highest": 0, "average": 0, "median": 0, "count": 0},
			"No Match", 0
		)
		return True

	# Also check if average is more than 300% - likely matched wrong items too
	if stats["average"] > your_price * 3.0:
		create_comparison_record(
			item, keywords,
			{"lowest": 0, "highest": 0, "average": 0, "median": 0, "count": 0},
			"No Match", 0
		)
		return True

	# Determine price position
	if your_price < stats["average"] * 0.9:
		position = "Below Market"
	elif your_price > stats["average"] * 1.1:
		position = "Above Market"
	else:
		position = "At Market"

	# Create/Update comparison record
	create_comparison_record(item, keywords, stats, position, results["total"])

	return True


def extract_prices_from_results(results):
	"""Extract valid prices from eBay search results

	Args:
		results: dict with 'items' list from eBay API

	Returns:
		list of float prices
	"""
	prices = []
	for item in results.get("items", []):
		price_obj = item.get("price", {})
		if price_obj:
			try:
				price_val = flt(price_obj.get("value", 0))
				if price_val > 0:
					prices.append(price_val)
			except (ValueError, TypeError):
				continue
	return prices


def extract_search_keywords(item_name):
	"""Extract meaningful keywords from item name using SIMPLE approach.

	The original simple approach (first N words minus stop words) works better
	than complex extraction because it preserves the specificity of the title.

	For auto parts, eBay search returns accessories when queries are too generic.
	Keeping more of the original title helps match the SAME type of part.

	Args:
		item_name: The item name/title to extract keywords from

	Returns:
		str: Space-separated keywords for search
	"""
	import re

	if not item_name:
		return ""

	# Minimal stop words - only remove truly useless words
	stop_words = {
		"the", "a", "an", "for", "and", "or", "with",
		"in", "on", "at", "to", "of", "is", "it", "by"
	}

	# Clean the item name - minimal processing
	clean_name = item_name.lower()
	# Remove OEM part numbers (8+ alphanumeric chars at end or standalone)
	clean_name = re.sub(r'\b[a-z0-9]{8,}\b', '', clean_name)
	# Normalize punctuation to spaces
	clean_name = re.sub(r'[^\w\s-]', ' ', clean_name)
	# Keep hyphens in year ranges but normalize
	clean_name = re.sub(r'\s+', ' ', clean_name).strip()

	words = clean_name.split()

	# Filter out stop words and very short words
	keywords = []
	for w in words:
		if w not in stop_words and len(w) > 1:
			keywords.append(w)

	# Return first 8 meaningful words - this is the sweet spot
	# More specific = better matches for expensive assemblies
	return " ".join(keywords[:8])


def filter_price_outliers(prices, your_price=None):
	"""Remove outlier prices using IQR method and reference price

	Args:
		prices: List of float prices
		your_price: Your selling price as a reference point (optional)

	Returns:
		List of prices with outliers removed
	"""
	if len(prices) < 4:
		return prices

	sorted_prices = sorted(prices)
	n = len(sorted_prices)
	median = sorted_prices[n // 2]

	# Calculate Q1 (25th percentile) and Q3 (75th percentile)
	q1_idx = n // 4
	q3_idx = (3 * n) // 4
	q1 = sorted_prices[q1_idx]
	q3 = sorted_prices[q3_idx]
	iqr = q3 - q1

	# IQR-based bounds
	iqr_lower = q1 - (2.0 * iqr)
	iqr_upper = q3 + (2.0 * iqr)

	# Reference-based bounds (if your_price provided)
	# Competitors unlikely to sell same item for <25% or >400% of your price
	if your_price and your_price > 0:
		ref_lower = your_price * 0.25  # 25% of your price minimum
		ref_upper = your_price * 4.0   # 400% of your price maximum
	else:
		ref_lower = 10.0
		ref_upper = float('inf')

	# Absolute minimum (real auto parts rarely sell for < $5)
	abs_lower = 5.0

	# Use the most reasonable bounds
	final_lower = max(iqr_lower, ref_lower, abs_lower)
	final_upper = min(iqr_upper, ref_upper) if your_price else iqr_upper

	# Filter prices
	filtered = [p for p in prices if final_lower <= p <= final_upper]

	# If too few results, try with just IQR bounds
	if len(filtered) < 3:
		filtered = [p for p in prices if iqr_lower <= p <= iqr_upper and p >= abs_lower]

	# If still too few, return original
	if len(filtered) < 3:
		return prices

	return filtered


def calculate_price_stats(prices, your_price=None):
	"""Calculate min, max, avg, median from price list

	Args:
		prices: List of float prices
		your_price: Your selling price for reference-based filtering

	Returns:
		dict with lowest, highest, average, median, count
	"""
	# First filter outliers using your price as reference
	filtered_prices = filter_price_outliers(prices, your_price)

	sorted_prices = sorted(filtered_prices)
	n = len(sorted_prices)

	# Calculate median
	if n % 2 == 0:
		median = (sorted_prices[n // 2 - 1] + sorted_prices[n // 2]) / 2
	else:
		median = sorted_prices[n // 2]

	return {
		"lowest": min(filtered_prices),
		"highest": max(filtered_prices),
		"average": sum(filtered_prices) / n,
		"median": median,
		"count": n
	}


def create_comparison_record(item, keywords, stats, position, total_found):
	"""Create or update Price Comparison record

	Args:
		item: Item dict with name, item_name, standard_rate
		keywords: Search keywords used
		stats: Price statistics dict
		position: Price position string
		total_found: Total listings found on eBay
	"""
	your_price = flt(item.standard_rate)

	# Check for existing record for this item
	existing = frappe.db.get_value("eBay Price Comparison",
									{"item_code": item.name}, "name")

	if existing:
		doc = frappe.get_doc("eBay Price Comparison", existing)
	else:
		doc = frappe.new_doc("eBay Price Comparison")
		doc.item_code = item.name

	doc.item_name = item.item_name
	doc.your_price = your_price
	doc.search_keywords = keywords
	doc.comparison_date = now_datetime()
	doc.lowest_price = stats["lowest"]
	doc.highest_price = stats["highest"]
	doc.average_price = stats["average"]
	doc.median_price = stats["median"]
	doc.listings_found = stats["count"]
	doc.price_position = position
	doc.price_difference = your_price - stats["average"]

	# Calculate percentage difference (avoid division by zero)
	if stats["average"] > 0:
		doc.price_difference_pct = ((your_price - stats["average"]) / stats["average"]) * 100
	else:
		doc.price_difference_pct = 0

	doc.save(ignore_permissions=True)


@frappe.whitelist()
def manual_price_comparison():
	"""Manually trigger price comparison from UI - runs as background job"""
	# Capture the current user so the background worker can send notifications back
	current_user = frappe.session.user
	# Enqueue as background job to avoid timeout
	frappe.enqueue(
		"ebay_integration.utils.price_comparison.run_price_comparison_background",
		queue="long",
		timeout=3600,  # 1 hour timeout
		job_name="eBay Price Comparison",
		user=current_user
	)
	return "Price comparison started in background. Check eBay Logs for progress."


def run_price_comparison_background(user=None):
	"""Background job wrapper for price comparison with notifications

	Args:
		user: The user who triggered the job (for sending notifications)
	"""
	# Use the passed user, fall back to session user
	notify_user = user or frappe.session.user

	try:
		from ebay_integration.utils.ebay_api import eBayWrapper
		ebay = eBayWrapper()

		items = get_items_to_compare()
		total = len(items)

		log_sync_result("price_comparison", "Info",
			f"Starting: found {total} items to compare")
		frappe.db.commit()

		if total == 0:
			log_sync_result("price_comparison", "Warning",
				"No items found to compare. Items need: disabled=0, is_stock_item=1, standard_rate > 0")
			frappe.db.commit()
			try:
				frappe.publish_realtime(
					"msgprint",
					"Price comparison: No items found to compare. Items need is_stock_item=1 and standard_rate > 0.",
					user=notify_user
				)
			except Exception:
				pass  # Notification failure shouldn't stop the job
			return

		compared = 0

		for i, item in enumerate(items):
			if compare_item_price(ebay, item):
				compared += 1
			# Log progress every 25 items
			if (i + 1) % 25 == 0:
				log_sync_result("price_comparison", "Info", f"Background progress: {i+1}/{total} items, {compared} compared")
				frappe.db.commit()

		log_sync_result("price_comparison", "Success", f"Compared {compared} of {total} items")
		frappe.db.commit()

		# Send notification to user
		try:
			frappe.publish_realtime(
				"msgprint",
				f"Price comparison complete: {compared} of {total} items compared.",
				user=notify_user
			)
		except Exception:
			pass  # Notification failure shouldn't mask success

	except Exception as e:
		tb = traceback.format_exc()
		log_sync_result("price_comparison", "Error", str(e), details=tb)
		frappe.log_error(message=tb, title="Price Comparison Error")
		try:
			frappe.publish_realtime(
				"msgprint",
				f"Price comparison failed: {str(e)}",
				user=notify_user
			)
		except Exception:
			pass


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
