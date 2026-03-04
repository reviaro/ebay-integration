#!/usr/bin/env python
"""
eBay Integration Installation Helper

This script helps ensure the ebay_integration app is properly installed
after container restarts. Run this from inside the frappe container.

Usage (from inside container):
    cd /workspace/custom_apps/ebay_integration
    python install.py

Or use bench commands:
    bench get-app /workspace/custom_apps/ebay_integration
    bench --site [site-name] install-app ebay_integration
    bench --site [site-name] migrate
"""

import subprocess
import sys
import os


def run_command(cmd, check=True):
    """Run a shell command and return output"""
    print(f"Running: {cmd}")
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if result.stdout:
        print(result.stdout)
    if result.stderr:
        print(result.stderr, file=sys.stderr)
    if check and result.returncode != 0:
        print(f"Command failed with return code {result.returncode}")
        return False
    return True


def get_site_name():
    """Get the site name from sites directory"""
    sites_path = os.path.expanduser("~/frappe-bench/sites")
    if not os.path.exists(sites_path):
        # Try alternative paths
        for alt_path in ["/home/frappe/frappe-bench/sites", "/workspace/frappe-bench/sites"]:
            if os.path.exists(alt_path):
                sites_path = alt_path
                break

    if not os.path.exists(sites_path):
        print("Could not find sites directory")
        return None

    # Find site directories (exclude common non-site directories)
    exclude = {'assets', 'common_site_config.json', 'apps.txt', '.git'}
    for item in os.listdir(sites_path):
        item_path = os.path.join(sites_path, item)
        if os.path.isdir(item_path) and item not in exclude and not item.startswith('.'):
            return item

    return None


def main():
    print("=" * 50)
    print("eBay Integration Installation Helper")
    print("=" * 50)

    # Check if we're in a bench environment
    bench_path = os.path.expanduser("~/frappe-bench")
    if not os.path.exists(bench_path):
        for alt_path in ["/home/frappe/frappe-bench", "/workspace/frappe-bench"]:
            if os.path.exists(alt_path):
                bench_path = alt_path
                break

    if not os.path.exists(bench_path):
        print("ERROR: Could not find frappe-bench directory")
        print("Please run this script from inside the frappe container")
        sys.exit(1)

    print(f"Found bench at: {bench_path}")
    os.chdir(bench_path)

    # Get site name
    site_name = get_site_name()
    if not site_name:
        print("ERROR: Could not determine site name")
        print("Please specify site name as argument: python install.py [site-name]")
        if len(sys.argv) > 1:
            site_name = sys.argv[1]
        else:
            sys.exit(1)

    print(f"Using site: {site_name}")

    # Check if app is already installed
    apps_txt_path = os.path.join(bench_path, "sites", "apps.txt")
    app_installed = False
    if os.path.exists(apps_txt_path):
        with open(apps_txt_path) as f:
            if "ebay_integration" in f.read():
                app_installed = True
                print("App 'ebay_integration' is already in apps.txt")

    # Get the app if not in apps folder
    app_path = os.path.join(bench_path, "apps", "ebay_integration")
    if not os.path.exists(app_path):
        print("Installing app from /workspace/custom_apps/ebay_integration...")
        if not run_command("bench get-app /workspace/custom_apps/ebay_integration"):
            print("Failed to get app. Trying alternative method...")
            # Create symlink instead
            os.makedirs(os.path.join(bench_path, "apps"), exist_ok=True)
            run_command(f"ln -sf /workspace/custom_apps/ebay_integration {app_path}", check=False)

    # Install on site if not already installed
    if not app_installed:
        print(f"Installing app on site {site_name}...")
        run_command(f"bench --site {site_name} install-app ebay_integration")

    # Run migrations
    print("Running migrations...")
    run_command(f"bench --site {site_name} migrate")

    # Clear cache
    print("Clearing cache...")
    run_command(f"bench --site {site_name} clear-cache")

    print("")
    print("=" * 50)
    print("Installation complete!")
    print("=" * 50)
    print("")
    print("If you still have issues, try:")
    print("  bench restart")
    print("  bench --site [site-name] clear-cache")


if __name__ == "__main__":
    main()
