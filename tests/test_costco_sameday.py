from __future__ import annotations

import unittest

from grocery_agent.browser import FakeBrowserSession
from grocery_agent.costco_sameday import (
    CostcoSameDayBrowserAgent,
    SafetyGateError,
    parse_checkout_review,
    remember_product_rule,
    resolve_product_rule,
)
from grocery_agent.models import BrowserPageState, GroceryProfile, ProductRule


class CostcoSameDayBrowserAgentTest(unittest.TestCase):
    def test_preflight_requires_sameday_and_signed_in(self) -> None:
        profile = GroceryProfile()
        browser = FakeBrowserSession(
            [
                BrowserPageState(
                    url="https://sameday.costco.com/store/costco/storefront",
                    title="Costco Delivery or Pickup",
                    body_text="Sign In / Register\nDelivery · 6:00-7:00pm\n94402\nView cart\n0",
                    buttons=["Sign In / Register"],
                )
            ]
        )
        report = CostcoSameDayBrowserAgent(browser, profile).preflight()
        self.assertFalse(report.ok)
        self.assertFalse(report.signed_in)
        self.assertIn("does not appear signed in", report.issues[0])

    def test_preflight_accepts_signed_in_sameday_with_account_menu(self) -> None:
        profile = GroceryProfile()
        profile.preferences.checkout_policy.delivery_address = "1439 Tarrytown Street"
        browser = FakeBrowserSession(
            [
                BrowserPageState(
                    url="https://sameday.costco.com/store/costco/storefront",
                    title="Costco Delivery or Pickup",
                    body_text="Delivery · 6:00-7:00pm\n1439 Tarrytown Street\nAdd $24 more\n12\nDepartments",
                    buttons=["Account Menu", "Add $24 more\n\n12"],
                )
            ]
        )
        report = CostcoSameDayBrowserAgent(browser, profile).preflight()
        self.assertTrue(report.ok)
        self.assertTrue(report.signed_in)
        self.assertEqual(report.delivery_address, "1439 Tarrytown Street")
        self.assertEqual(report.cart_count, 12)

    def test_product_rule_memory_resolves_exact_household_preference(self) -> None:
        profile = GroceryProfile()
        remember_product_rule(
            profile,
            ProductRule(
                canonical_item="onions",
                search_query="red onions",
                preferred_product_name="Red Onions, 5 lbs",
            ),
        )
        rule = resolve_product_rule(profile, "onions")
        self.assertIsNotNone(rule)
        self.assertEqual(rule.preferred_product_name, "Red Onions, 5 lbs")

    def test_build_cart_uses_product_rules_and_blocks_missing_rules(self) -> None:
        profile = GroceryProfile()
        remember_product_rule(
            profile,
            ProductRule(
                canonical_item="olive oil",
                search_query="olive oil",
                preferred_product_name="Kirkland Signature, Organic Extra Virgin Olive Oil, 2 L",
            ),
        )
        browser = FakeBrowserSession(
            [
                BrowserPageState(
                    url="https://sameday.costco.com/store/costco/storefront",
                    title="Costco Delivery or Pickup",
                    body_text="Delivery · 6:00-7:00pm\n1439 Tarrytown Street\nAdd $24 more\n12\nKirkland Signature, Organic Extra Virgin Olive Oil, 2 L",
                    buttons=["Account Menu", "Add 1 ct Kirkland Signature, Organic Extra Virgin Olive Oil, 2 L"],
                )
            ]
        )
        result = CostcoSameDayBrowserAgent(browser, profile).build_cart(["olive oil", "mystery item"])
        self.assertEqual(result.added, ["Kirkland Signature, Organic Extra Virgin Olive Oil, 2 L"])
        self.assertEqual(result.missing_rules, ["mystery item"])
        self.assertIn("navigate:https://sameday.costco.com/store/costco/s?k=olive%20oil", browser.actions)
        self.assertIn("click:Add 1 ct Kirkland Signature, Organic Extra Virgin Olive Oil, 2 L", browser.actions)

    def test_checkout_review_parser_and_place_order_gate(self) -> None:
        profile = GroceryProfile()
        state = BrowserPageState(
            url="https://sameday.costco.com/store/checkout_v4",
            title="Costco Same-Day - Checkout",
            body_text=(
                "Checkout\nDeliver to\n1439 Tarrytown Street San Mateo, CA 94402\n"
                "Standard\n7:46pm-9:04pm\nPay with\n\nEdit\n\nVisa *1418\n"
                "Summary\nDelivery Tip\n$0.00\n12 items (loyalty applied)\n$125.26\n"
                "Est. tax\n$1.59\nTotal\noriginal price $126.85, your price $126.85\n$126.85\nPlace order"
            ),
            buttons=["Place order"],
        )
        review = parse_checkout_review(state)
        self.assertEqual(review.delivery_tip, 0.0)
        self.assertEqual(review.total, 126.85)
        self.assertTrue(review.place_order_visible)

        browser = FakeBrowserSession([state])
        agent = CostcoSameDayBrowserAgent(browser, profile)
        with self.assertRaises(SafetyGateError):
            agent.place_order("go ahead")
        agent.place_order("I approve, place the order")
        self.assertIn("click:Place order", browser.actions)


if __name__ == "__main__":
    unittest.main()
