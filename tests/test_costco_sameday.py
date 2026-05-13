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

    def test_product_rule_memory_resolves_common_singular_plural_variants(self) -> None:
        profile = GroceryProfile()
        remember_product_rule(
            profile,
            ProductRule(
                canonical_item="strawberries",
                search_query="strawberries fresh",
                preferred_product_name="Kirkland Signature Organic Strawberries, 4 lbs",
            ),
        )
        remember_product_rule(
            profile,
            ProductRule(
                canonical_item="onions",
                search_query="red onions",
                preferred_product_name="Red Onions, 5 lbs",
            ),
        )
        self.assertEqual(resolve_product_rule(profile, "strawberry").preferred_product_name, "Kirkland Signature Organic Strawberries, 4 lbs")
        self.assertEqual(resolve_product_rule(profile, "onion").preferred_product_name, "Red Onions, 5 lbs")

    def test_product_rule_memory_resolves_recent_household_items(self) -> None:
        profile = GroceryProfile()
        rules = [
            ProductRule("blueberries", "blueberries", "Blueberries, 18 oz"),
            ProductRule("bananas", "bananas", "Bananas, 3 lbs"),
            ProductRule("apples", "apple", "Organic Fuji Apples, 4 lbs"),
            ProductRule("lamb chops", "lamb chops", "Kirkland Signature Lamb Loin Chops, Australian"),
            ProductRule("chicken thighs", "chicken thighs", "Kirkland Signature Fresh Boneless Skinless Chicken Thighs"),
        ]
        for rule in rules:
            remember_product_rule(profile, rule)
        self.assertEqual(resolve_product_rule(profile, "blueberries").preferred_product_name, "Blueberries, 18 oz")
        self.assertEqual(resolve_product_rule(profile, "banana").preferred_product_name, "Bananas, 3 lbs")
        self.assertEqual(resolve_product_rule(profile, "apple").preferred_product_name, "Organic Fuji Apples, 4 lbs")
        self.assertEqual(resolve_product_rule(profile, "lamb chop").preferred_product_name, "Kirkland Signature Lamb Loin Chops, Australian")
        self.assertEqual(resolve_product_rule(profile, "chicken thighs.").preferred_product_name, "Kirkland Signature Fresh Boneless Skinless Chicken Thighs")

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

    def test_open_checkout_review_uses_view_cart_path(self) -> None:
        profile = GroceryProfile()
        state = BrowserPageState(
            url="https://sameday.costco.com/store/costco/s?k=strawberries",
            title="Costco Strawberries",
            body_text=(
                "Account Menu\nDelivery · 6:00-7:00pm\n1439 Tarrytown Street\n"
                "View cart\n1\nCheckout\nDeliver to\n1439 Tarrytown Street San Mateo, CA 94402\n"
                "Summary\nDelivery Tip\n$0.00\n1 items (loyalty applied)\n$13.61\n"
                "Est. tax\n$0.00\nTotal\n$13.61\nPlace order"
            ),
            buttons=["Account Menu", "View cart\n\n1", "Go to checkout", "Continue to checkout", "Place order"],
        )
        browser = FakeBrowserSession([state])
        review = CostcoSameDayBrowserAgent(browser, profile).open_checkout_review()
        self.assertEqual(review.total, 13.61)
        self.assertIn("click_contains:View cart", browser.actions)
        self.assertIn("click_contains:Go to checkout", browser.actions)

    def test_open_checkout_review_reports_minimum_order_instead_of_missing_button(self) -> None:
        profile = GroceryProfile()
        state = BrowserPageState(
            url="https://sameday.costco.com/store/costco/s?k=strawberries",
            title="Costco Strawberries",
            body_text="Account Menu\nDelivery · 6:00-7:00pm\n1439 Tarrytown Street\nView cart\n1",
            buttons=["Account Menu", "View cart\n\n1"],
            dialogs=["Cart\nKirkland Signature Organic Strawberries, 4 lbs\n$35 Min. to checkout\n$13.61"],
        )
        browser = FakeBrowserSession([state])
        with self.assertRaisesRegex(SafetyGateError, "below the \\$35.00 minimum"):
            CostcoSameDayBrowserAgent(browser, profile).open_checkout_review()

    def test_open_cart_review_text_reads_cart_dialog(self) -> None:
        profile = GroceryProfile()
        state = BrowserPageState(
            url="https://sameday.costco.com/store/costco/s?k=strawberries",
            title="Costco Strawberries",
            body_text="Account Menu\nDelivery · 6:00-7:00pm\n1439 Tarrytown Street\nView cart\n1",
            buttons=["Account Menu", "View cart\n\n1"],
            dialogs=["Cart\nKirkland Signature Organic Strawberries, 4 lbs (each)\n$13.61\n$35 Min. to checkout"],
        )
        browser = FakeBrowserSession([state])
        text = CostcoSameDayBrowserAgent(browser, profile).open_cart_review_text()
        self.assertIn("Kirkland Signature Organic Strawberries", text)
        self.assertIn("$35 Min. to checkout", text)

    def test_open_cart_review_text_uses_stable_cart_selector_when_button_text_changes(self) -> None:
        profile = GroceryProfile()
        state = BrowserPageState(
            url="https://sameday.costco.com/store/costco/s?k=olive%20oil",
            title="Costco Olive Oil",
            body_text="Account Menu\nDelivery · 6:00-7:00pm\n1439 Tarrytown Street\nSaving $1.50\n3",
            buttons=["Account Menu", "Saving $1.50\n\n3"],
            dialogs=["Cart\nKirkland Signature, Organic Extra Virgin Olive Oil, 2 L\n$19.17"],
        )
        browser = FakeBrowserSession([state])
        text = CostcoSameDayBrowserAgent(browser, profile).open_cart_review_text()
        self.assertIn("Organic Extra Virgin Olive Oil", text)
        self.assertNotIn("click_selector:#floating-cart-button", browser.actions)

    def test_open_cart_review_text_clicks_stable_selector_when_dialog_is_closed(self) -> None:
        profile = GroceryProfile()
        state = BrowserPageState(
            url="https://sameday.costco.com/store/costco/s?k=olive%20oil",
            title="Costco Olive Oil",
            body_text="Account Menu\nDelivery · 6:00-7:00pm\n1439 Tarrytown Street\nSaving $1.50\n3",
            buttons=["Account Menu", "Saving $1.50\n\n3"],
        )
        browser = FakeBrowserSession([state])
        CostcoSameDayBrowserAgent(browser, profile).open_cart_review_text()
        self.assertIn("click_selector:#floating-cart-button", browser.actions)


if __name__ == "__main__":
    unittest.main()
