 eBay Price Comparison Feature - Implementation Plan                                                                                                                                                                                                                         

 Overview

 Create a price comparison system that compares your inventory prices against similar active listings on eBay, using title keywords and condition (used items only). Runs automatically every 4 days and displays results in an ERPNext list view with price statistics.     

 ---
 Prerequisites (User Action Required)

 1. Add Browse API Scope to eBay Application

 Before implementing, you must update your eBay Developer application:

 1. Go to https://developer.ebay.com/my/keys
 2. Edit your application
 3. Add OAuth scope: https://api.ebay.com/oauth/api_scope/buy.browse (or the broader buy.item.readonly)
 4. Save changes

 2. Re-authorize After Implementation

 After code changes, click "Authorize eBay" in eBay Settings to get a new token with the Browse API scope.

 ---
 Architecture

 ebay_integration/
 ├── ebay_connector/doctype/
 │   └── ebay_price_comparison/           # NEW DocType
 │       ├── ebay_price_comparison.json
 │       ├── ebay_price_comparison.py
 │       └── __init__.py
 ├── utils/
 │   ├── ebay_api.py                      # ADD: search_similar_items() method
 │   └── price_comparison.py              # NEW: comparison logic
 └── hooks.py                             # ADD: scheduler for every 4 days

 ---
 Implementation Steps

 Step 1: Update OAuth Scopes (ebay_settings.py)

 File: ebay_integration/ebay_connector/doctype/ebay_settings/ebay_settings.py

 Add Browse API scope to the authorization URL:
 scopes = "https://api.ebay.com/oauth/api_scope https://api.ebay.com/oauth/api_scope/buy.browse ..."

 ---
 Step 2: Add Search Method to eBay API Wrapper

 File: ebay_integration/utils/ebay_api.py

 Add new method to eBayWrapper class:

 def search_similar_items(self, keywords, condition="USED", limit=50):
     """Search eBay for similar items using Browse API"""
     url = f"{self.base_url}/buy/browse/v1/item_summary/search"
     params = {
         "q": keywords,
         "filter": f"conditions:{{{condition}}}",
         "limit": limit,
         "sort": "price"
     }

     response = self._make_request("GET", url, params=params)

     if response.status_code == 200:
         data = response.json()
         items = data.get("itemSummaries", [])
         return {
             "items": items,
             "total": data.get("total", 0)
         }
     else:
         self.log_error("search_similar_items", f"Status {response.status_code}")
         return {"items": [], "total": 0}

 ---
 Step 3: Create Price Comparison DocType

 File: ebay_integration/ebay_connector/doctype/ebay_price_comparison/ebay_price_comparison.json

 Fields:
 ┌──────────────────────┬─────────────┬───────────────────────────────────────────────┐
 │        Field         │    Type     │                  Description                  │
 ├──────────────────────┼─────────────┼───────────────────────────────────────────────┤
 │ item_code            │ Link (Item) │ Your inventory item                           │
 ├──────────────────────┼─────────────┼───────────────────────────────────────────────┤
 │ item_name            │ Data        │ Item name (read-only)                         │
 ├──────────────────────┼─────────────┼───────────────────────────────────────────────┤
 │ your_price           │ Currency    │ Your current price                            │
 ├──────────────────────┼─────────────┼───────────────────────────────────────────────┤
 │ search_keywords      │ Data        │ Keywords used for search                      │
 ├──────────────────────┼─────────────┼───────────────────────────────────────────────┤
 │ comparison_date      │ Datetime    │ When comparison was run                       │
 ├──────────────────────┼─────────────┼───────────────────────────────────────────────┤
 │ Price Statistics     │             │                                               │
 ├──────────────────────┼─────────────┼───────────────────────────────────────────────┤
 │ lowest_price         │ Currency    │ Lowest competitor price                       │
 ├──────────────────────┼─────────────┼───────────────────────────────────────────────┤
 │ highest_price        │ Currency    │ Highest competitor price                      │
 ├──────────────────────┼─────────────┼───────────────────────────────────────────────┤
 │ average_price        │ Currency    │ Average competitor price                      │
 ├──────────────────────┼─────────────┼───────────────────────────────────────────────┤
 │ median_price         │ Currency    │ Median price                                  │
 ├──────────────────────┼─────────────┼───────────────────────────────────────────────┤
 │ listings_found       │ Int         │ Number of similar listings                    │
 ├──────────────────────┼─────────────┼───────────────────────────────────────────────┤
 │ Analysis             │             │                                               │
 ├──────────────────────┼─────────────┼───────────────────────────────────────────────┤
 │ price_position       │ Select      │ "Below Market" / "At Market" / "Above Market" │
 ├──────────────────────┼─────────────┼───────────────────────────────────────────────┤
 │ price_difference     │ Currency    │ Your price - Average price                    │
 ├──────────────────────┼─────────────┼───────────────────────────────────────────────┤
 │ price_difference_pct │ Percent     │ Percentage difference                         │
 └──────────────────────┴─────────────┴───────────────────────────────────────────────┘
 Naming: Auto-increment PRICE-CMP-.#####

 ---
 Step 4: Create Price Comparison Logic

 File: ebay_integration/utils/price_comparison.py

 def run_price_comparison():
     """Main function - called by scheduler every 4 days"""
     if not frappe.db.get_single_value("eBay Settings", "sync_enabled"):
         return

     try:
         from ebay_integration.utils.ebay_api import eBayWrapper
         ebay = eBayWrapper()

         # Get all stock items with eBay item numbers
         items = get_items_to_compare()

         compared = 0
         for item in items:
             if compare_item_price(ebay, item):
                 compared += 1

         log_sync_result("price_comparison", "Success",
                         f"Compared {compared} items")
         frappe.db.commit()

     except Exception as e:
         log_sync_result("price_comparison", "Error", str(e))
         frappe.log_error(str(e), "Price Comparison Error")

 def get_items_to_compare():
     """Get items from inventory that have prices set"""
     return frappe.get_all("Item",
         filters={
             "disabled": 0,
             "is_stock_item": 1,
             "standard_rate": [">", 0]
         },
         fields=["name", "item_name", "standard_rate"]
     )

 def compare_item_price(ebay, item):
     """Compare single item against eBay listings"""
     # Extract keywords from item name
     keywords = extract_search_keywords(item.item_name)

     # Search eBay
     results = ebay.search_similar_items(keywords, condition="USED", limit=50)

     if not results["items"]:
         return False

     # Calculate statistics
     prices = [float(i.get("price", {}).get("value", 0))
               for i in results["items"] if i.get("price")]

     if not prices:
         return False

     stats = calculate_price_stats(prices)

     # Determine price position
     your_price = float(item.standard_rate)
     if your_price < stats["average"] * 0.9:
         position = "Below Market"
     elif your_price > stats["average"] * 1.1:
         position = "Above Market"
     else:
         position = "At Market"

     # Create/Update comparison record
     create_comparison_record(item, keywords, stats, position, results["total"])

     return True

 def extract_search_keywords(item_name):
     """Extract meaningful keywords from item name"""
     # Remove common words, keep important terms
     stop_words = ["the", "a", "an", "for", "and", "or", "with", "oem", "new"]
     words = item_name.lower().split()
     keywords = [w for w in words if w not in stop_words and len(w) > 2]
     return " ".join(keywords[:8])  # Limit to 8 keywords

 def calculate_price_stats(prices):
     """Calculate min, max, avg, median from price list"""
     sorted_prices = sorted(prices)
     return {
         "lowest": min(prices),
         "highest": max(prices),
         "average": sum(prices) / len(prices),
         "median": sorted_prices[len(sorted_prices) // 2],
         "count": len(prices)
     }

 def create_comparison_record(item, keywords, stats, position, total_found):
     """Create or update Price Comparison record"""
     your_price = float(item.standard_rate)

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
     doc.comparison_date = frappe.utils.now_datetime()
     doc.lowest_price = stats["lowest"]
     doc.highest_price = stats["highest"]
     doc.average_price = stats["average"]
     doc.median_price = stats["median"]
     doc.listings_found = stats["count"]
     doc.price_position = position
     doc.price_difference = your_price - stats["average"]
     doc.price_difference_pct = ((your_price - stats["average"]) / stats["average"]) * 100

     doc.save(ignore_permissions=True)

 ---
 Step 5: Add Scheduler Event

 File: ebay_integration/hooks.py

 Add to scheduler_events:
 scheduler_events = {
     "cron": {
         "0 0 * * *": [...],  # existing
         "30 0 * * *": [...], # existing
         # Run price comparison every 4 days at 2 AM
         "0 2 */4 * *": [
             "ebay_integration.utils.price_comparison.run_price_comparison"
         ]
     }
 }

 ---
 Step 6: Add Manual Button to eBay Settings

 File: ebay_integration/ebay_connector/doctype/ebay_settings/ebay_settings.js

 Add button in refresh function:
 frm.add_custom_button('Run Price Comparison', function () {
     frappe.call({
         method: "ebay_integration.utils.price_comparison.manual_price_comparison",
         freeze: true,
         freeze_message: "Comparing prices with eBay market...",
         callback: function (r) {
             if (r.message) {
                 frappe.msgprint(r.message);
             }
         }
     });
 }, 'API Sync');

 frm.add_custom_button('View Price Comparisons', function () {
     frappe.set_route('List', 'eBay Price Comparison');
 }, 'View');

 ---
 Step 7: Add Whitelist Method

 File: ebay_integration/utils/price_comparison.py

 @frappe.whitelist()
 def manual_price_comparison():
     """Manually trigger price comparison from UI"""
     try:
         run_price_comparison()
         count = frappe.db.count("eBay Price Comparison")
         return f"Price comparison complete. {count} items compared."
     except Exception as e:
         frappe.log_error(str(e), "Manual Price Comparison Error")
         return f"Error: {str(e)}"

 ---
 Files to Create/Modify
 ┌───────────────────────────────────┬────────┬───────────────────────────────────┐
 │               File                │ Action │              Purpose              │
 ├───────────────────────────────────┼────────┼───────────────────────────────────┤
 │ ebay_settings.py                  │ Modify │ Add Browse API scope              │
 ├───────────────────────────────────┼────────┼───────────────────────────────────┤
 │ ebay_api.py                       │ Modify │ Add search_similar_items() method │
 ├───────────────────────────────────┼────────┼───────────────────────────────────┤
 │ price_comparison.py               │ Create │ Main comparison logic             │
 ├───────────────────────────────────┼────────┼───────────────────────────────────┤
 │ ebay_price_comparison.json        │ Create │ DocType definition                │
 ├───────────────────────────────────┼────────┼───────────────────────────────────┤
 │ ebay_price_comparison.py          │ Create │ DocType class                     │
 ├───────────────────────────────────┼────────┼───────────────────────────────────┤
 │ ebay_price_comparison/__init__.py │ Create │ Empty init file                   │
 ├───────────────────────────────────┼────────┼───────────────────────────────────┤
 │ hooks.py                          │ Modify │ Add scheduler event               │
 ├───────────────────────────────────┼────────┼───────────────────────────────────┤
 │ ebay_settings.js                  │ Modify │ Add UI buttons                    │
 └───────────────────────────────────┴────────┴───────────────────────────────────┘
 ---
 Rate Limit Considerations

 - eBay Browse API: 5,000 calls/day
 - Each item comparison = 1 API call
 - Running every 4 days: Can compare up to 5,000 items per run
 - Recommendation: If you have >500 items, consider batching or reducing frequency

 ---
 Verification Steps

 After implementation:

 1. Re-authorize eBay (required for new scope)
   - Go to eBay Settings
   - Click "Authorize eBay"
   - Complete OAuth flow
 2. Test API access
 from ebay_integration.utils.ebay_api import eBayWrapper
 ebay = eBayWrapper()
 results = ebay.search_similar_items("auto part used", condition="USED", limit=5)
 print(results)
 3. Run manual comparison
   - Go to eBay Settings
   - Click "Run Price Comparison"
   - Check results in "View Price Comparisons"
 4. Verify scheduler
 bench --site frontend show-scheduler-log

 ---
 Expected Result

 After running, you'll see a list view like:
 ┌─────────────────────┬────────────┬───────────┬──────────────┬───────────────┐
 │        Item         │ Your Price │ Avg Price │   Position   │  Difference   │
 ├─────────────────────┼────────────┼───────────┼──────────────┼───────────────┤
 │ A/C Panel 2008-2012 │ $149.98    │ $125.00   │ Above Market │ +$24.98 (20%) │
 ├─────────────────────┼────────────┼───────────┼──────────────┼───────────────┤
 │ Brake Rotor Set     │ $89.00     │ $95.50    │ Below Market │ -$6.50 (-7%)  │
 ├─────────────────────┼────────────┼───────────┼──────────────┼───────────────┤
 │ ...                 │ ...        │ ...       │ ...          │ ...           │
 └─────────────────────┴────────────┴───────────┴──────────────┴───────────────┘
 Filter by "Above Market" to find items you might want to reprice.