"""
Top-level test configuration.

Unit tests: ebay_integration/tests/unit/conftest.py injects a mock frappe.
Integration tests: ebay_integration/tests/integration/ uses real frappe from
  the bench environment — no mock injection here.
"""
