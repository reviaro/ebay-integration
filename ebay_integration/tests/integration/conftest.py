"""
Integration test configuration.

Initializes frappe against the live 'frontend' site before any tests run,
then tears it down afterward. All tests in this directory get a real DB
connection — no mock frappe.
"""

import pytest


@pytest.fixture(scope="session", autouse=True)
def frappe_site():
    import os
    import frappe

    frappe.init(site="frontend", sites_path="/home/frappe/frappe-bench/sites")

    # Frappe's logger creates two log files — one bench-level and one site-level.
    # Both parent dirs must exist. site_path may be relative (to CWD) depending
    # on how frappe resolves it, so we use frappe.local.site_path directly.
    # Frappe's logger uses two paths:
    #   1. bench-level:  <bench_root>/logs/<module>.log  → resolves to apps/logs/ from CWD
    #   2. site-level:   <site_name>/logs/<module>.log   → relative to CWD
    # Neither directory exists by default when running pytest directly.
    os.makedirs("/home/frappe/frappe-bench/apps/logs", exist_ok=True)
    os.makedirs(os.path.join(frappe.local.site, "logs"), exist_ok=True)

    frappe.connect()
    yield
    frappe.destroy()
