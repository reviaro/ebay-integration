# eBay Integration Update Instructions

This document provides step-by-step instructions to update and install the eBay Integration app on your ERPNext server.

## Latest Changes (v0.0.2)

### Enhanced CSV Import
- Now handles ALL 80+ columns from eBay order export
- Proper scientific notation handling for item numbers (4.0626E+11 → 406260000000)
- Flexible date parsing (Oct-29-25, 10-29-2025, etc.)
- Full buyer/shipping address support with country validation
- All fee types captured (e-waste, mattress, battery, tire, lumber, etc.)
- eBay program flags (Global Shipping, eBay Plus, etc.)

### New DocType: eBay Order
- Stores all eBay-specific data linked to Sales Orders
- View at: **eBay Order** list

### Improved UI
- Better organized buttons (API Sync, CSV Import, View)
- Dialog-based CSV upload with options
- Detailed import results display

### Docker Persistence Fix
- New `docker-compose.persistent.yml` with bench volume
- Install scripts for easy reinstallation

---

## Prerequisites

- SSH access to the server where ERPNext is installed
- The updated code files (this repository)

---

## Quick Start (After Container Restart)

If the app disappeared after restart:

```bash
# Enter the container
docker exec -it [container_name] bash

# Navigate to bench
cd /home/frappe/frappe-bench

# Reinstall
bench get-app /workspace/custom_apps/ebay_integration
bench --site [your-site] install-app ebay_integration
bench --site [your-site] migrate
bench --site [your-site] clear-cache
bench restart
```

---

## Permanent Fix: Use Persistent Docker Compose

```bash
# Stop containers
docker-compose down

# Start with persistent compose (keeps bench data)
docker-compose -f docker-compose.persistent.yml up -d
```

---

## Docker Installation (Detailed)

### Step 1: Find your container

```bash
docker ps | grep frappe
```

### Step 2: Enter the container

```bash
docker exec -it [container_name] bash
```

### Step 3: Install/Update the app

```bash
cd /home/frappe/frappe-bench

# Get the app (if not already installed)
bench get-app /workspace/custom_apps/ebay_integration

# Install on site
bench --site [your-site] install-app ebay_integration

# Run migrations (creates new eBay Order doctype)
bench --site [your-site] migrate

# Clear cache
bench --site [your-site] clear-cache

# Rebuild assets (for JS changes)
bench build

# Restart
bench restart
```

### Step 4: Verify

1. Go to ERPNext
2. Navigate to **eBay Settings**
3. You should see:
   - **API Sync** menu: Sync Orders, Sync Inventory, Import Historical, Update Taxes
   - **CSV Import** menu: Import Orders CSV, Import Inventory CSV
   - **View** menu: View Sync Logs, View eBay Orders

---

## Manual File Update (Alternative)

If you need to update files manually:

### Copy files to container

```bash
# From your host machine
docker cp ./custom_apps/ebay_integration frappe_container:/workspace/custom_apps/
```

### Then inside container

```bash
bench get-app /workspace/custom_apps/ebay_integration --overwrite
bench --site [site] migrate
bench --site [site] clear-cache
bench build
bench restart
```

---

## Files Changed in This Update

```
ebay_integration/
├── ebay_integration/
│   ├── hooks.py                    (updated - scheduler paths)
│   ├── ebay_connector/
│   │   ├── __init__.py             (updated)
│   │   └── doctype/
│   │       ├── ebay_order/         (NEW - entire folder)
│   │       │   ├── ebay_order.json
│   │       │   ├── ebay_order.py
│   │       │   └── __init__.py
│   │       └── ebay_settings/
│   │           ├── ebay_settings.js (updated - improved UI)
│   │           └── ebay_settings.py (updated - fixed paths)
│   └── utils/
│       ├── __init__.py             (updated)
│       ├── import_ebay_csv.py      (complete rewrite - 80+ fields)
│       ├── import_inventory_csv.py (updated - better parsing)
│       ├── sync_orders.py          (updated - fixed imports)
│       └── sync_inventory.py       (updated - fixed imports)
├── scripts/
│   └── install.sh                  (NEW)
├── install.py                      (NEW)
├── README.md                       (NEW)
└── UPDATE_INSTRUCTIONS.md          (this file)

Root directory:
├── docker-compose.persistent.yml   (NEW - fixes persistence)
```

---

## Troubleshooting

### Buttons not appearing in eBay Settings

```bash
bench --site [site] clear-cache
bench build
# Then refresh browser with Ctrl+Shift+R
```

### Import fails with "Method not found"

```bash
bench --site [site] migrate
bench restart
```

### eBay Order doctype not found

```bash
bench --site [site] migrate
```

### App disappears after container restart

Use `docker-compose.persistent.yml`:
```bash
docker-compose -f docker-compose.persistent.yml up -d
```

### Error: "Column doesn't exist"

```bash
bench --site [site] migrate
bench --site [site] clear-cache
```

### Scheduled jobs not running

```bash
bench doctor
bench enable-scheduler
```

---

## CSV Import Field Support

### Orders CSV (All fields captured)
- Sales Record Number, Order Number, Transaction ID
- Buyer Username, Name, Email, Note
- Buyer Tax Identifier (Name & Value) - CURP, VAT, etc.
- Buyer Address & Ship To Address (full details)
- Item Number, Title, Custom Label (SKU)
- Variation Details
- Quantity, Sold For, Shipping And Handling
- All tax types: eBay Collected, Seller Collected
- All fee types: E-Waste, Mattress, Battery, Tire, Lumber, Wireless, Road Improvement, etc.
- Total Price, Payment Method
- All dates: Sale, Paid, Ship By, Shipped, Min/Max Delivery estimates
- Tracking Number, Shipping Service
- PayPal Transaction ID
- All eBay programs: Global Shipping, eBay Plus, Click & Collect, Authenticity Verification, PSA Vault, eBay Fulfillment
- Feedback status (left/received)
- My Item Note

### Inventory CSV
- Item number (handles scientific notation like 4.05331E+11)
- Title
- Quantity on hand
- Format (FIXED_PRICE, AUCTION)
- Currency
- Sales price
- Sold quantity
- eBay category

---

## Support

If you encounter issues:
1. Check the **Error Log** in ERPNext
2. Check the **eBay Log** for sync-specific errors
3. Review scheduler logs: `bench --site [site] show-scheduler-log`
