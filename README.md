# eBay Integration for ERPNext

A custom Frappe/ERPNext application that provides automated one-way synchronization from eBay to ERPNext. Syncs orders, inventory, pricing intelligence, and refund/cancellation data through eBay's REST APIs.

## Features

### Order Synchronization
- **API Sync** — Automated daily sync via eBay Fulfillment API with full financial breakdown (subtotal, shipping, tax, fees)
- **CSV Import** — Bulk import from eBay order exports with 80+ fields including all fee types, program flags, and feedback data
- **Historical Import** — Backfill up to 400 days of order history
- **Full Document Flow** — Sales Order → Delivery Note → Sales Invoice → Payment Entry, created automatically
- **Duplicate Prevention** — Checks `po_no` field to avoid re-importing existing orders

### Inventory Sync
- Fetches active eBay inventory via the Inventory API
- Compares quantities against ERPNext warehouse stock
- Creates Stock Reconciliation documents for discrepancies

### Price Comparison (Market Analysis)
- Automated competitor price analysis using the Browse API
- Multi-pass search strategy with category and price-range filters
- IQR-based outlier filtering for accurate market positioning
- Tracks lowest, highest, average, and median competitor prices
- Classifies your price position: Below / At / Above Market

### Cancellation & Refund Tracking
- Monitors order status changes and cancellation requests
- Fetches refund transactions from the Finances API
- Creates **Credit Notes** (with partial refund support via pro-rata adjustment)
- Creates **Return Delivery Notes** to adjust warehouse stock
- Refund type detection: Return, Shipping, Goodwill, Cancellation, Partial, Full

## Architecture

```
ebay_integration/
├── hooks.py                         # App config, scheduled tasks
├── ebay_connector/
│   ├── custom_fields.py             # Extends Sales Order, Item, Customer
│   └── doctype/
│       ├── ebay_settings/           # OAuth config, manual sync controls
│       ├── ebay_order/              # Full eBay order data (54 fields)
│       ├── ebay_price_comparison/   # Price analysis records
│       ├── ebay_refund/             # Refund & cancellation records
│       └── ebay_log/                # Sync history & error tracking
└── utils/
    ├── ebay_api.py                  # eBayWrapper — REST API client
    ├── sync_orders.py               # Order sync (API-based)
    ├── sync_inventory.py            # Inventory reconciliation
    ├── sync_cancellations.py        # Refund & cancellation processing
    ├── price_comparison.py          # Competitor price analysis
    ├── import_ebay_csv.py           # CSV order import (80+ fields)
    └── import_inventory_csv.py      # CSV inventory import
```

## Data Flow

```
┌──────────────┐         ┌──────────────────┐         ┌─────────────────┐
│   eBay APIs  │────────▶│  eBayWrapper     │────────▶│    ERPNext      │
│              │         │  (ebay_api.py)   │         │                 │
│ Fulfillment  │         └──────────────────┘         │ Sales Order     │
│ Inventory    │                                      │ Delivery Note   │
│ Browse       │         ┌──────────────────┐         │ Sales Invoice   │
│ Finances     │         │  CSV Import      │────────▶│ Payment Entry   │
│              │         │  (import_*.py)   │         │ Stock Recon     │
└──────────────┘         └──────────────────┘         │ Credit Note     │
                                                      │ Return DN       │
                                                      └─────────────────┘
```

## Tech Stack

| Layer | Technology |
|-------|------------|
| Backend | Python 3, Frappe Framework |
| ERP | ERPNext v14 / v15 |
| eBay APIs | Fulfillment, Inventory, Browse, Finances (all REST) |
| Authentication | OAuth 2.0 with automatic token refresh |
| Deployment | Docker (Frappe Docker) |

## DocTypes

| DocType | Purpose |
|---------|---------|
| **eBay Settings** | OAuth credentials, API config, manual sync buttons |
| **eBay Order** | Stores full eBay order data (54 fields across 11 sections) |
| **eBay Price Comparison** | Market price analysis results per item |
| **eBay Refund** | Refund and cancellation tracking records |
| **eBay Log** | Audit trail for all sync operations |

## Custom Fields

The app extends standard ERPNext DocTypes:

- **Sales Order** — eBay order link, tracking number, shipping service, cancellation/refund status
- **Sales Order Item** — eBay item title, item number, variation details
- **Item** — eBay item number, listing format, sold quantity, category
- **Customer** — eBay username, tax ID

## Scheduled Tasks

| Schedule | Task | Description |
|----------|------|-------------|
| Daily at midnight | `sync_orders` | Fetch new orders via Fulfillment API |
| Daily at 12:30 AM | `sync_inventory` | Reconcile inventory quantities |
| Every 4 days at 2 AM | `price_comparison` | Run competitor price analysis |
| Every 6 hours | `sync_cancellations` | Check for refunds & cancellations |

## Installation

### Prerequisites
- ERPNext v14 or v15 running on Frappe Framework
- eBay Developer account with API credentials

### Setup

```bash
# Install the app
bench get-app /path/to/ebay_integration
bench --site your-site install-app ebay_integration
bench --site your-site migrate

# Create custom fields on standard DocTypes
bench --site your-site execute ebay_integration.ebay_connector.custom_fields.create_custom_fields
```

### Docker Deployment

```bash
# Install inside container
docker compose exec backend ./env/bin/pip install -e /path/to/ebay_integration
docker compose exec backend bench --site your-site migrate
docker compose exec backend bench --site your-site clear-cache
docker compose restart backend
```

## Configuration

1. Navigate to **eBay Settings** in ERPNext
2. Enter your eBay Developer credentials:
   - Client ID (App ID)
   - Client Secret (Cert ID) — stored encrypted via Frappe Password field
   - RuName (Redirect URL Name)
3. Click **Authorize** to initiate OAuth 2.0 flow
4. Paste the authorization code and click **Generate Token**
5. Configure sync preferences and use the manual sync buttons to verify

### Required OAuth Scopes

| Scope | Used By |
|-------|---------|
| `api_scope` | Basic API access |
| `buy.browse` | Price comparison (Browse API) |
| `sell.fulfillment.readonly` | Order sync |
| `sell.inventory.readonly` | Inventory sync |
| `sell.finances` | Refund transaction retrieval |
| `sell.marketing.readonly` | Marketing data |
| `sell.account.readonly` | Account settings |

## Security Design

- **Read-only integration** — No write operations to eBay; all API scopes are read-only
- **Encrypted credentials** — Client secret, access token, and refresh token stored via Frappe's encrypted Password fields, accessed only through `doc.get_password()`
- **Automatic token refresh** — Handles expired tokens transparently on 401 responses
- **No hardcoded secrets** — All credentials configured through the eBay Settings DocType

## License

MIT
