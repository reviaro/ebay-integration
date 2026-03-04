#!/bin/bash
# eBay Integration Installation Script
# Run this after container starts to ensure the app is installed

set -e

echo "=================================================="
echo "eBay Integration - Installation Script"
echo "=================================================="

# Find bench directory
BENCH_PATH=""
for path in ~/frappe-bench /home/frappe/frappe-bench /workspace/frappe-bench; do
    if [ -d "$path" ]; then
        BENCH_PATH="$path"
        break
    fi
done

if [ -z "$BENCH_PATH" ]; then
    echo "ERROR: Could not find frappe-bench directory"
    exit 1
fi

echo "Found bench at: $BENCH_PATH"
cd "$BENCH_PATH"

# Find site name
SITE_NAME=""
for dir in sites/*/; do
    dirname=$(basename "$dir")
    if [ "$dirname" != "assets" ] && [ -d "sites/$dirname" ]; then
        if [ -f "sites/$dirname/site_config.json" ]; then
            SITE_NAME="$dirname"
            break
        fi
    fi
done

if [ -z "$SITE_NAME" ]; then
    echo "ERROR: Could not determine site name"
    echo "Usage: ./install.sh [site-name]"
    if [ -n "$1" ]; then
        SITE_NAME="$1"
    else
        exit 1
    fi
fi

echo "Using site: $SITE_NAME"

# Check if app exists in apps folder
APP_PATH="$BENCH_PATH/apps/ebay_integration"
CUSTOM_APP_PATH="/workspace/custom_apps/ebay_integration"

if [ ! -d "$APP_PATH" ]; then
    echo "App not found in apps folder. Installing..."

    if [ -d "$CUSTOM_APP_PATH" ]; then
        echo "Getting app from $CUSTOM_APP_PATH..."
        bench get-app "$CUSTOM_APP_PATH" || {
            echo "bench get-app failed, creating symlink..."
            ln -sf "$CUSTOM_APP_PATH" "$APP_PATH"
        }
    else
        echo "ERROR: Custom app not found at $CUSTOM_APP_PATH"
        exit 1
    fi
fi

# Check if installed on site
if ! grep -q "ebay_integration" "sites/apps.txt" 2>/dev/null; then
    echo "Installing app on site $SITE_NAME..."
    bench --site "$SITE_NAME" install-app ebay_integration
fi

# Run migrations
echo "Running migrations..."
bench --site "$SITE_NAME" migrate

# Clear cache
echo "Clearing cache..."
bench --site "$SITE_NAME" clear-cache

echo ""
echo "=================================================="
echo "Installation complete!"
echo "=================================================="
echo ""
echo "To start the development server, run:"
echo "  bench start"
