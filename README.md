# eBay Integration for ERPNext

A comprehensive eBay connector for ERPNext that syncs orders and inventory from eBay to your ERPNext instance.

## Features

- **API Sync**: Sync orders and inventory directly from eBay API
- **CSV Import**: Import orders and inventory from eBay Seller Hub CSV exports
- **Full Data Capture**: Captures 80+ fields from eBay orders including:
  - All buyer/shipping information
  - Tax identifiers (VAT, CURP, etc.)
  - All fee types (e-waste, mattress, tire, etc.)
  - Tracking and shipping details
  - eBay program flags (Plus, Global Shipping, etc.)
- **Automatic Document Creation**: Creates Sales Orders, Invoices, and Payments
- **Stock Reconciliation**: Keeps inventory in sync with eBay

## Installation

### First Time Setup

1. **Inside the container**, run:
```bash
cd /home/frappe/frappe-bench
bench get-app /workspace/custom_apps/ebay_integration
bench --site [your-site] install-app ebay_integration
bench --site [your-site] migrate
```

2. Configure eBay Settings in ERPNext:
   - Go to: **eBay Settings**
   - Enter your eBay API credentials
   - Set default company, warehouse, and customer group
   - Click **Authorize eBay** and follow the OAuth flow

### After Container Restart

If the app disappears after container restart, you have two options:

#### Option 1: Use Persistent Docker Compose (Recommended)

Use the persistent docker-compose file that includes a volume for the bench:

```bash
docker-compose -f docker-compose.persistent.yml up -d
```

#### Option 2: Reinstall the App

Run the install script from inside the container:

```bash
# From inside the frappe container
cd /workspace/custom_apps/ebay_integration
bash scripts/install.sh
```

Or manually:
```bash
cd /home/frappe/frappe-bench
bench get-app /workspace/custom_apps/ebay_integration
bench --site [your-site] install-app ebay_integration
bench --site [your-site] migrate
bench --site [your-site] clear-cache
```

## Usage

### Syncing from eBay API

1. Go to **eBay Settings**
2. Click **Sync Orders Now** or **Sync Inventory Now**

### Importing from CSV

1. Export orders/inventory from eBay Seller Hub
2. Go to **eBay Settings**
3. Click **Import Orders CSV** or **Import Inventory CSV**
4. Upload your CSV file

### Viewing Data

- **eBay Orders**: Stores all eBay-specific data linked to Sales Orders
- **eBay Log**: View sync history and errors

## CSV Field Support

### Orders CSV (All fields captured)
- Sales Record Number, Order Number, Transaction ID
- Buyer Username, Name, Email, Note
- Buyer Tax Identifier (Name & Value)
- Buyer Address & Ship To Address
- Item Number, Title, Custom Label (SKU)
- Variation Details
- Quantity, Sold For, Shipping And Handling
- All tax types: eBay Collected, Seller Collected
- All fee types: E-Waste, Mattress, Battery, Tire, Lumber, etc.
- Total Price, Payment Method
- All dates: Sale, Paid, Ship By, Shipped, Delivery estimates
- Tracking Number, Shipping Service
- PayPal Transaction ID
- All eBay programs: Global Shipping, eBay Plus, Click & Collect, etc.
- Feedback status

### Inventory CSV
- Item number (handles scientific notation like 4.05331E+11)
- Title
- Quantity on hand
- Format (FIXED_PRICE, AUCTION)
- Currency
- Sales price
- Sold quantity
- eBay category

## Troubleshooting

### Import buttons not working
1. Clear browser cache (Ctrl+Shift+R)
2. Run: `bench --site [site] clear-cache`
3. Run: `bench --site [site] build`

### App disappears after restart
Use `docker-compose.persistent.yml` which includes a volume for bench data.

### OAuth token expired
Go to eBay Settings and click **Authorize eBay** to get a new token.

## License

MIT License
