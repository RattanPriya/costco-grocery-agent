from __future__ import annotations

import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from grocery_agent.agent import ApprovalRequiredError, GroceryAgent
from grocery_agent.costco import MockCostcoClient, PurchaseSafetyError
from grocery_agent.models import CartStatus, GroceryProfile, PantryEstimate
from grocery_agent.scheduler import BiweeklySundayScheduler
from grocery_agent.storage import JsonStore


class GroceryAgentTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = JsonStore(Path(self.tmp.name) / "data.json")
        profile = GroceryProfile()
        profile.preferences.preferred_brands = {"milk": ["Kirkland"], "baby": ["Huggies"]}
        profile.preferences.always_buy = ["milk"]
        profile.pantry = [
            PantryEstimate("eggs", "eggs", cadence_days=14, last_purchased=date.today() - timedelta(days=20), usual_quantity=1),
            PantryEstimate("paper towels", "household", cadence_days=45, last_purchased=date.today(), usual_quantity=1),
        ]
        self.store.save_profile(profile)
        self.agent = GroceryAgent(self.store, MockCostcoClient())

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_cart_generation_prefers_profile_brand(self) -> None:
        cart = self.agent.generate_cart(["milk", "diapers"])
        names = [item.product.name for item in cart.items]
        self.assertIn("Kirkland Signature Organic Whole Milk, 3 x 64 fl oz", names)
        self.assertIn("Huggies Plus Diapers Size 4, 174 count", names)
        self.assertEqual(cart.status, CartStatus.REVIEW_READY)
        self.assertTrue(any("preferred Huggies brand" in item.reason for item in cart.items))

    def test_approval_gate_blocks_unapproved_order(self) -> None:
        self.agent.generate_cart(["eggs"])
        with self.assertRaises(ApprovalRequiredError):
            self.agent.place_order()
        with self.assertRaises(PurchaseSafetyError):
            MockCostcoClient().place_order(self.store.latest_cart())

    def test_approved_cart_can_be_placed_and_learns_preferences(self) -> None:
        cart = self.agent.generate_cart(["eggs"])
        self.agent.approve_cart(cart.id, approver="Priya")
        order = self.agent.place_order(cart.id)
        self.assertTrue(order.order_id.startswith("MOCK-COSTCO-"))
        profile = self.store.load_profile()
        self.assertIn("Kirkland", profile.preferences.preferred_brands["eggs"])

    def test_out_of_stock_substitution_is_recorded(self) -> None:
        cart = self.agent.generate_cart(["strawberries"])
        self.assertFalse(any(item.product.category == "produce" and "Strawberries" in item.product.name for item in cart.items))
        self.assertEqual(len(cart.out_of_stock), 1)
        self.assertIn("out of stock", cart.out_of_stock[0].reason)
        self.assertTrue(any("Blueberries" in product.name for product in cart.out_of_stock[0].suggested_substitutions))

    def test_scheduler_runs_every_other_sunday(self) -> None:
        scheduler = BiweeklySundayScheduler(anchor_sunday=date(2026, 5, 10))
        self.assertTrue(scheduler.should_run(date(2026, 5, 24)))
        self.assertFalse(scheduler.should_run(date(2026, 5, 17)))
        self.assertFalse(scheduler.should_run(date(2026, 5, 25)))
        cart = scheduler.prepare_if_due(self.agent, date(2026, 5, 24))
        self.assertIsNotNone(cart)
        self.assertIn("milk", cart.requested_items)
        self.assertIn("eggs", cart.requested_items)


if __name__ == "__main__":
    unittest.main()
