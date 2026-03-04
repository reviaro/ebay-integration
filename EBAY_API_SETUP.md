# eBay API Setup Guide for ERPNext Integration

This guide explains how to get the required eBay API credentials for the ERPNext eBay Integration.

---

## Required eBay Credentials

| Credential | What it is | Where to get it |
|------------|------------|-----------------|
| **App ID (Client ID)** | Your application identifier | eBay Developer Portal |
| **Cert ID (Client Secret)** | Your application secret | eBay Developer Portal |
| **Dev ID** | Your developer account ID | eBay Developer Portal |
| **RuName** | Redirect URL Name for OAuth | eBay Developer Portal |

---

## Step-by-Step Setup

### Step 1: Create eBay Developer Account

1. Go to https://developer.ebay.com/
2. Sign up or log in with your eBay account
3. Go to **My Account** → **Application Keys**

### Step 2: Create Application Keys

1. Click **Create a keyset**
2. Select **Production** environment (not Sandbox)
3. You'll receive three credentials:

| Credential | Example |
|------------|---------|
| App ID (Client ID) | `YourApp-PRD-abc123-def456` |
| Cert ID (Client Secret) | `PRD-abc123def-1234-5678-9abc` |
| Dev ID | `12345678-abcd-1234-efgh-567890abcdef` |

**Save these credentials securely!**

### Step 3: Create RuName (Redirect URL Name)

1. On the Application Keys page, find the **OAuth** section
2. Click **User Tokens** → **Get a Token from eBay via Your Application**
3. Click **Add eBay Redirect URL**
4. Fill in:
   - **Label**: `ERPNext Integration` (or any name you prefer)
   - **Redirect URL**: Any URL you control, e.g.:
     - `https://yourdomain.com/ebay-callback`
     - `https://localhost/callback` (works for testing)
   - **Note**: This URL doesn't need to actually work - eBay just redirects there with a code in the URL
5. Save and note down the **RuName** - looks like: `YourName-YourApp-PRD-abcdef`

---

## Enter Credentials in ERPNext

1. Log in to ERPNext as Administrator
2. Search for **eBay Settings** in the awesome bar
3. Fill in the credentials:

| Field | Value |
|-------|-------|
| Sandbox Environment | ☐ Unchecked (use Production) |
| Enable Sync | ☐ Check this when ready to sync |
| App ID (Client ID) | Your App ID from Step 2 |
| Cert ID (Client Secret) | Your Cert ID from Step 2 |
| Dev ID | Your Dev ID from Step 2 |
| RuName | Your RuName from Step 3 |
| Default Company | Select your company |
| Default Warehouse | Select your warehouse |
| Default Customer Group | Select customer group for eBay buyers |

4. Click **Save**

---

## Authorize & Generate Token

### Authorization Flow

1. In eBay Settings, click the **Authorize eBay** button
2. A new browser window opens with eBay's login page
3. Sign in with your **eBay Seller account** (the one with your store)
4. Review and **Approve** the requested permissions:
   - Read your selling activity
   - Read your inventory
   - Read your account info
5. eBay redirects to your RuName URL

### Get the Auth Code

After approval, eBay redirects to a URL like:
```
https://yourdomain.com/ebay-callback?code=v%5E1.1%23i%5E1%23p%5E3...
```

1. Copy the `code` parameter value from the URL
2. The code is URL-encoded, so it may look like: `v%5E1.1%23i%5E1%23p%5E3...`
3. You can use it as-is (ERPNext will handle decoding)

### Generate Token

1. Back in ERPNext eBay Settings
2. Paste the code into the **Auth Code** field
3. Click the **Generate Token** button that appears
4. If successful, you'll see "Token Generated Successfully!"

The access token and refresh token are now stored (hidden fields).

---

## Verify Setup

### Check Token Generation

```bash
# In ERPNext console (bench --site yoursite console)
import frappe
settings = frappe.get_single("eBay Settings")
print("Access Token:", "Present" if settings.access_token else "Missing")
print("Refresh Token:", "Present" if settings.refresh_token else "Missing")
```

### Test API Connection

```bash
# In ERPNext console
from ebay_integration.utils.ebay_api import eBayWrapper
ebay = eBayWrapper()
orders = ebay.get_orders(days_back=7)
print(f"Found {len(orders)} orders from last 7 days")
```

---

## Troubleshooting

### "Invalid grant" error when generating token
- The auth code expires quickly (~5 minutes)
- Click Authorize again and complete the flow faster

### "Invalid client credentials" error
- Double-check App ID and Cert ID are correct
- Make sure you're using Production keys, not Sandbox

### No orders/items returned
- Verify you signed in with your seller account (not buyer account)
- Check that you have active listings on eBay
- Ensure "Enable Sync" is checked

### Token expired
- Refresh tokens last ~18 months
- If expired, click Authorize again to get new tokens

---

## API Scopes Used

The integration uses these OAuth scopes:

| Scope | Purpose |
|-------|---------|
| `api_scope` | Basic API access |
| `sell.marketing.readonly` | Read marketing data |
| `sell.inventory.readonly` | Read your inventory/listings |
| `sell.account.readonly` | Read account information |
| `sell.fulfillment.readonly` | Read order fulfillment data |

All scopes are **read-only** - the integration cannot modify your eBay listings.

---

## Quick Reference

```
eBay Developer Portal: https://developer.ebay.com/
Application Keys: My Account → Application Keys
OAuth Settings: Application Keys → User Tokens

Credentials needed:
├── App ID (Client ID)
├── Cert ID (Client Secret)
├── Dev ID
└── RuName (Redirect URL Name)

ERPNext Flow:
1. Save credentials in eBay Settings
2. Click Authorize eBay
3. Login & Approve on eBay
4. Copy code from redirect URL
5. Paste in Auth Code field
6. Click Generate Token
7. Enable Sync when ready
```

---

## Support Links

- eBay Developer Documentation: https://developer.ebay.com/docs
- eBay API Explorer: https://developer.ebay.com/devzone/api-explorer/
- OAuth Guide: https://developer.ebay.com/api-docs/static/oauth-tokens.html
