from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path

from grocery_agent.agent import GroceryAgent
from grocery_agent.costco import MockCostcoClient
from grocery_agent.gemini_hosting import CalendarEvent, build_hosting_plan, detect_hosting_events
from grocery_agent.models import CartStatus, GroceryProfile
from grocery_agent.storage import JsonStore


class GeminiHostingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = JsonStore(Path(self.tmp.name) / "data.json")
        profile = GroceryProfile()
        profile.preferences.dietary_restrictions = ["vegetarian"]
        self.store.save_profile(profile)
        self.agent = GroceryAgent(self.store, MockCostcoClient())

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_detects_at_home_hosting_and_ignores_restaurant(self) -> None:
        events = [
            CalendarEvent("Dinner reservation at Saffron", date(2026, 5, 15), location="Saffron"),
            CalendarEvent("Hosting friends dinner for 6", date(2026, 5, 16), attendees=["a", "b", "c"]),
            CalendarEvent("Team sync", date(2026, 5, 17)),
        ]

        detected = detect_hosting_events(events, today=date(2026, 5, 13), horizon_days=7)

        self.assertEqual([event.title for event in detected], ["Hosting friends dinner for 6"])

    def test_builds_vegetarian_gemini_hosting_plan(self) -> None:
        profile = self.store.load_profile()
        event = CalendarEvent("Hosting Indian dinner for 8", date(2026, 5, 16), description="vegetarian")

        plan = build_hosting_plan(event, profile)

        self.assertEqual(plan.gemini_surface, "Gemini proactive hosting assist")
        self.assertEqual(plan.guest_count, 8)
        self.assertIn("Paneer and vegetable curry", plan.menu)
        self.assertIn("paneer", plan.grocery_items)
        self.assertIn("sparkling water", plan.grocery_items)

    def test_generates_review_ready_cart_from_calendar_signal(self) -> None:
        events = [
            CalendarEvent("Hosting Indian dinner for 6", date(2026, 5, 16), description="vegetarian friends coming over"),
        ]

        recommendation = self.agent.generate_gemini_hosting_cart(events, today=date(2026, 5, 13))

        self.assertIsNotNone(recommendation)
        assert recommendation is not None
        self.assertEqual(recommendation.cart.status, CartStatus.REVIEW_READY)
        self.assertIn("paneer", recommendation.cart.requested_items)
        self.assertTrue(any("Paneer" in item.product.name for item in recommendation.cart.items))
        self.assertTrue(any(entry.action == "gemini_hosting_event_detected" for entry in recommendation.cart.decision_log))


if __name__ == "__main__":
    unittest.main()
