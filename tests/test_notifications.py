from __future__ import annotations

import unittest
from typing import Any

from grocery_agent.agent import GroceryAgent
from grocery_agent.browser import FakeBrowserSession
from grocery_agent.costco import MockCostcoClient
from grocery_agent.costco_sameday import CostcoSameDayBrowserAgent, remember_product_rule
from grocery_agent.models import BrowserPageState, CartStatus, GroceryProfile, ProductRule
from grocery_agent.notifications import NotificationError, ReviewLinkBuilder, TelegramApiClient, TelegramCartBot, TelegramCostcoBot, TelegramNotifier
from grocery_agent.storage import JsonStore

import tempfile
from pathlib import Path


class NotificationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        store = JsonStore(Path(self.tmp.name) / "data.json")
        store.save_profile(GroceryProfile())
        self.store = store
        self.agent = GroceryAgent(store, MockCostcoClient())
        self.cart = self.agent.generate_cart(["milk", "eggs"])

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_review_link_builder_creates_cart_url_and_message(self) -> None:
        notification = ReviewLinkBuilder("https://example.ngrok-free.app/review/").notification_for(self.cart)
        self.assertEqual(notification.review_url, "https://example.ngrok-free.app/review/cart")
        self.assertIn("Costco cart ready for review", notification.message)
        self.assertIn(f"Cart: {self.cart.id}", notification.message)
        self.assertIn("Final purchase still needs explicit approval", notification.message)

    def test_review_link_builder_requires_public_url_shape(self) -> None:
        with self.assertRaises(ValueError):
            ReviewLinkBuilder("")
        with self.assertRaises(ValueError):
            ReviewLinkBuilder("example.com")

    def test_telegram_notifier_sends_review_button(self) -> None:
        calls: list[tuple[str, dict[str, Any]]] = []

        def sender(url: str, payload: dict[str, Any]) -> dict[str, Any]:
            calls.append((url, payload))
            return {"ok": True}

        notification = ReviewLinkBuilder("https://review.example").notification_for(self.cart)
        TelegramNotifier("token-123", "chat-456", sender=sender).send_review(notification)

        self.assertEqual(calls[0][0], "https://api.telegram.org/bottoken-123/sendMessage")
        self.assertEqual(calls[0][1]["chat_id"], "chat-456")
        self.assertEqual(calls[0][1]["reply_markup"]["inline_keyboard"][0][0]["url"], "https://review.example/cart")

    def test_telegram_notifier_surfaces_rejections(self) -> None:
        def sender(url: str, payload: dict[str, Any]) -> dict[str, Any]:
            return {"ok": False, "description": "chat not found"}

        notification = ReviewLinkBuilder("https://review.example").notification_for(self.cart)
        with self.assertRaises(NotificationError):
            TelegramNotifier("token-123", "chat-456", sender=sender).send_review(notification)

    def test_telegram_cart_bot_sends_latest_cart_with_buttons(self) -> None:
        calls: list[tuple[str, dict[str, Any]]] = []

        def sender(url: str, payload: dict[str, Any]) -> dict[str, Any]:
            calls.append((url, payload))
            return {"ok": True, "result": []}

        bot = TelegramCartBot(TelegramApiClient("token-123", sender=sender), self.agent)
        bot.handle_update({"message": {"chat": {"id": 42}, "text": "/cart"}})

        self.assertEqual(calls[0][0], "https://api.telegram.org/bottoken-123/sendMessage")
        self.assertIn("Costco cart review", calls[0][1]["text"])
        buttons = calls[0][1]["reply_markup"]["inline_keyboard"][0]
        self.assertEqual(buttons[0]["callback_data"], f"approve:{self.cart.id}")
        self.assertEqual(buttons[1]["callback_data"], f"reject:{self.cart.id}")

    def test_telegram_cart_bot_approves_from_callback(self) -> None:
        calls: list[tuple[str, dict[str, Any]]] = []

        def sender(url: str, payload: dict[str, Any]) -> dict[str, Any]:
            calls.append((url, payload))
            return {"ok": True, "result": []}

        bot = TelegramCartBot(TelegramApiClient("token-123", sender=sender), self.agent)
        bot.handle_update(
            {
                "callback_query": {
                    "id": "callback-1",
                    "data": f"approve:{self.cart.id}",
                    "message": {"message_id": 99, "chat": {"id": 42}},
                }
            }
        )

        self.assertEqual(self.store.latest_cart().status, CartStatus.APPROVED)
        method_names = [url.rsplit("/", 1)[-1] for url, _ in calls]
        self.assertIn("answerCallbackQuery", method_names)
        self.assertIn("editMessageText", method_names)

    def test_telegram_cart_bot_ignores_unapproved_chat(self) -> None:
        calls: list[tuple[str, dict[str, Any]]] = []

        def sender(url: str, payload: dict[str, Any]) -> dict[str, Any]:
            calls.append((url, payload))
            return {"ok": True, "result": []}

        bot = TelegramCartBot(TelegramApiClient("token-123", sender=sender), self.agent, allowed_chat_id="42")
        bot.handle_update({"message": {"chat": {"id": 100}, "text": "/cart"}})

        self.assertEqual(calls, [])

    def test_telegram_costco_bot_builds_real_cart_and_sends_checkout_review(self) -> None:
        calls: list[tuple[str, dict[str, Any]]] = []

        def sender(url: str, payload: dict[str, Any]) -> dict[str, Any]:
            calls.append((url, payload))
            return {"ok": True, "result": []}

        profile = GroceryProfile()
        remember_product_rule(profile, ProductRule("olive oil", "olive oil", "Kirkland Signature, Organic Extra Virgin Olive Oil, 2 L"))
        browser = FakeBrowserSession([_checkout_state("Kirkland Signature, Organic Extra Virgin Olive Oil, 2 L")])
        bot = TelegramCostcoBot(TelegramApiClient("token-123", sender=sender), CostcoSameDayBrowserAgent(browser, profile))

        bot.handle_update({"message": {"chat": {"id": 42}, "text": "/costco olive oil"}})

        self.assertIn("navigate:https://sameday.costco.com/store/costco/s?k=olive%20oil", browser.actions)
        self.assertIn("click:Add 1 ct Kirkland Signature, Organic Extra Virgin Olive Oil, 2 L", browser.actions)
        self.assertTrue(any("Costco cart build complete" in payload["text"] for _, payload in calls))
        self.assertTrue(any("Current Costco cart" in payload["text"] for _, payload in calls))
        checkout_messages = [payload for _, payload in calls if "Real Costco checkout review" in payload["text"]]
        self.assertEqual(len(checkout_messages), 1)
        self.assertEqual(checkout_messages[0]["reply_markup"]["inline_keyboard"][0][1]["callback_data"], "real_place_order")

    def test_telegram_costco_bot_places_real_order_from_callback(self) -> None:
        calls: list[tuple[str, dict[str, Any]]] = []

        def sender(url: str, payload: dict[str, Any]) -> dict[str, Any]:
            calls.append((url, payload))
            return {"ok": True, "result": []}

        browser = FakeBrowserSession([_checkout_state()])
        bot = TelegramCostcoBot(TelegramApiClient("token-123", sender=sender), CostcoSameDayBrowserAgent(browser, GroceryProfile()))
        bot.handle_update(
            {
                "callback_query": {
                    "id": "callback-1",
                    "data": "real_place_order",
                    "message": {"message_id": 99, "chat": {"id": 42}},
                }
            }
        )

        self.assertIn("click:Place order", browser.actions)
        self.assertTrue(any("Order placed" in payload["text"] for _, payload in calls))

    def test_telegram_costco_bot_grocery_command_opens_chrome_before_building(self) -> None:
        calls: list[tuple[str, dict[str, Any]]] = []

        def sender(url: str, payload: dict[str, Any]) -> dict[str, Any]:
            calls.append((url, payload))
            return {"ok": True, "result": []}

        profile = GroceryProfile()
        remember_product_rule(profile, ProductRule("olive oil", "olive oil", "Kirkland Signature, Organic Extra Virgin Olive Oil, 2 L"))
        browser = FakeBrowserSession([_checkout_state("Kirkland Signature, Organic Extra Virgin Olive Oil, 2 L")])
        bot = TelegramCostcoBot(TelegramApiClient("token-123", sender=sender), CostcoSameDayBrowserAgent(browser, profile))

        bot.handle_update({"message": {"chat": {"id": 42}, "text": "/grocery olive oil"}})

        self.assertEqual(browser.actions[0], "open_url:https://sameday.costco.com/store/costco/storefront")
        self.assertIn("navigate:https://sameday.costco.com/store/costco/s?k=olive%20oil", browser.actions)


if __name__ == "__main__":
    unittest.main()


def _checkout_state(extra_text: str = "") -> BrowserPageState:
    return BrowserPageState(
        url="https://sameday.costco.com/store/costco/storefront",
        title="Costco Same-Day",
        body_text=(
            "Account Menu\nDelivery · 6:00-7:00pm\n1439 Tarrytown Street\n"
            f"{extra_text}\n"
            "Checkout\nDeliver to\n1439 Tarrytown Street San Mateo, CA 94402\n"
            "Standard\n7:46pm-9:04pm\nPay with\n\nEdit\n\nVisa *1418\n"
            "Summary\nDelivery Tip\n$0.00\n1 items (loyalty applied)\n$21.99\n"
            "Est. tax\n$1.59\nTotal\n$23.58\nPlace order"
        ),
        buttons=[
            "Account Menu",
            "View cart\n\n1",
            "Add 1 ct Kirkland Signature, Organic Extra Virgin Olive Oil, 2 L",
            "Go to checkout",
            "Continue to checkout",
            "Place order",
        ],
    )
