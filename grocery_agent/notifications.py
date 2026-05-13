from __future__ import annotations

import json
from dataclasses import dataclass
import time
from typing import Any, Protocol
from urllib import request
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin

from grocery_agent.costco_sameday import CostcoSameDayBrowserAgent, SafetyGateError
from grocery_agent.models import Cart, CheckoutReview


class NotificationError(RuntimeError):
    pass


class HttpSender(Protocol):
    def __call__(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        pass


@dataclass(frozen=True, slots=True)
class ReviewNotification:
    cart_id: str
    review_url: str
    message: str


class ReviewLinkBuilder:
    def __init__(self, base_url: str) -> None:
        cleaned = base_url.strip()
        if not cleaned:
            raise ValueError("Review base URL is required.")
        if not cleaned.startswith(("http://", "https://")):
            raise ValueError("Review base URL must start with http:// or https://.")
        self.base_url = cleaned.rstrip("/") + "/"

    def cart_url(self) -> str:
        return urljoin(self.base_url, "cart")

    def notification_for(self, cart: Cart) -> ReviewNotification:
        review_url = self.cart_url()
        message = _review_message(cart, review_url)
        return ReviewNotification(cart_id=cart.id, review_url=review_url, message=message)


class TelegramNotifier:
    def __init__(self, bot_token: str, chat_id: str, sender: HttpSender | None = None) -> None:
        if not bot_token:
            raise ValueError("Telegram bot token is required.")
        if not chat_id:
            raise ValueError("Telegram chat ID is required.")
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.sender = sender or _post_json

    def send_review(self, notification: ReviewNotification) -> None:
        endpoint = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload: dict[str, Any] = {
            "chat_id": self.chat_id,
            "text": notification.message,
            "disable_web_page_preview": True,
            "reply_markup": {
                "inline_keyboard": [
                    [{"text": "Review cart", "url": notification.review_url}],
                ],
            },
        }
        response = self.sender(endpoint, payload)
        if response.get("ok") is not True:
            description = response.get("description", "Telegram rejected the notification.")
            raise NotificationError(str(description))


class TelegramApiClient:
    def __init__(self, bot_token: str, sender: HttpSender | None = None) -> None:
        if not bot_token:
            raise ValueError("Telegram bot token is required.")
        self.bot_token = bot_token
        self.sender = sender or _post_json

    def call(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        endpoint = f"https://api.telegram.org/bot{self.bot_token}/{method}"
        response = self.sender(endpoint, payload)
        if response.get("ok") is not True:
            description = response.get("description", f"Telegram {method} request failed.")
            raise NotificationError(str(description))
        return response


class TelegramCartBot:
    def __init__(self, client: TelegramApiClient, agent: Any, allowed_chat_id: str | None = None) -> None:
        self.client = client
        self.agent = agent
        self.allowed_chat_id = str(allowed_chat_id) if allowed_chat_id else None

    def send_latest_cart(self, chat_id: str) -> None:
        cart = self.agent.store.latest_cart()
        if cart is None:
            self._send_message(chat_id, "No cart is ready yet. Generate a cart first.")
            return
        self._send_cart(chat_id, cart)

    def handle_update(self, update: dict[str, Any]) -> None:
        if "message" in update:
            self._handle_message(update["message"])
        elif "callback_query" in update:
            self._handle_callback(update["callback_query"])

    def run_polling(self, poll_seconds: float = 2.0) -> None:
        offset = 0
        while True:
            response = self.client.call("getUpdates", {"offset": offset, "timeout": int(max(1, poll_seconds))})
            for update in response.get("result", []):
                offset = max(offset, int(update.get("update_id", 0)) + 1)
                self.handle_update(update)
            time.sleep(max(0.1, poll_seconds))

    def _handle_message(self, message: dict[str, Any]) -> None:
        chat = message.get("chat", {})
        chat_id = str(chat.get("id", ""))
        if not self._chat_allowed(chat_id):
            return
        text = str(message.get("text", "")).strip().lower()
        if text.startswith("/start"):
            self._send_message(
                chat_id,
                f"Costco grocery bot connected.\n\nChat ID: {chat_id}\n\nSend /cart to review the latest cart with Approve and Reject buttons.",
            )
            return
        if text.startswith(("/cart", "/review")):
            self.send_latest_cart(chat_id)
            return
        self._send_message(chat_id, "Send /cart to review the latest Costco cart.")

    def _handle_callback(self, callback: dict[str, Any]) -> None:
        message = callback.get("message", {})
        chat = message.get("chat", {})
        chat_id = str(chat.get("id", ""))
        callback_id = str(callback.get("id", ""))
        if not self._chat_allowed(chat_id):
            self._answer_callback(callback_id, "This chat is not authorized.")
            return
        action, _, cart_id = str(callback.get("data", "")).partition(":")
        try:
            if action == "approve":
                cart = self.agent.approve_cart(cart_id, approver="telegram", statement="I approve this Costco cart for checkout review from Telegram.")
                self._answer_callback(callback_id, "Cart approved.")
                self._edit_message(chat_id, message.get("message_id"), _telegram_cart_message(cart, final_line="Cart approved for checkout review."))
                return
            if action == "reject":
                cart = self.agent.reject_cart(cart_id, reason="Rejected from Telegram review.")
                self._answer_callback(callback_id, "Cart rejected.")
                self._edit_message(chat_id, message.get("message_id"), _telegram_cart_message(cart, final_line="Cart rejected."))
                return
            self._answer_callback(callback_id, "Unknown action.")
        except Exception as exc:
            self._answer_callback(callback_id, str(exc))

    def _send_cart(self, chat_id: str, cart: Cart) -> None:
        self.client.call(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": _telegram_cart_message(cart),
                "reply_markup": _cart_keyboard(cart),
            },
        )

    def _send_message(self, chat_id: str, text: str) -> None:
        self.client.call("sendMessage", {"chat_id": chat_id, "text": text})

    def _edit_message(self, chat_id: str, message_id: Any, text: str) -> None:
        if message_id is None:
            self._send_message(chat_id, text)
            return
        self.client.call("editMessageText", {"chat_id": chat_id, "message_id": message_id, "text": text})

    def _answer_callback(self, callback_id: str, text: str) -> None:
        if callback_id:
            self.client.call("answerCallbackQuery", {"callback_query_id": callback_id, "text": text[:200]})

    def _chat_allowed(self, chat_id: str) -> bool:
        return bool(chat_id) and (self.allowed_chat_id is None or chat_id == self.allowed_chat_id)


class TelegramCostcoBot:
    def __init__(
        self,
        client: TelegramApiClient,
        browser_agent: CostcoSameDayBrowserAgent,
        allowed_chat_id: str | None = None,
    ) -> None:
        self.client = client
        self.browser_agent = browser_agent
        self.allowed_chat_id = str(allowed_chat_id) if allowed_chat_id else None

    def handle_update(self, update: dict[str, Any]) -> None:
        if "message" in update:
            self._handle_message(update["message"])
        elif "callback_query" in update:
            self._handle_callback(update["callback_query"])

    def run_polling(self, poll_seconds: float = 2.0) -> None:
        offset = 0
        while True:
            response = self.client.call("getUpdates", {"offset": offset, "timeout": int(max(1, poll_seconds))})
            for update in response.get("result", []):
                offset = max(offset, int(update.get("update_id", 0)) + 1)
                self.handle_update(update)
            time.sleep(max(0.1, poll_seconds))

    def _handle_message(self, message: dict[str, Any]) -> None:
        chat = message.get("chat", {})
        chat_id = str(chat.get("id", ""))
        if not self._chat_allowed(chat_id):
            return
        text = str(message.get("text", "")).strip()
        lowered = text.lower()
        if lowered.startswith("/start"):
            self._send_message(
                chat_id,
                (
                    "Costco live grocery bot connected.\n\n"
                    f"Chat ID: {chat_id}\n\n"
                    "Commands:\n"
                    "/preflight - check the active Costco Same Day browser tab\n"
                    "/grocery item 1, item 2 - open Chrome, add exact mapped items to the real Costco cart, and open checkout review\n"
                    "/costco item 1, item 2 - add exact mapped items to the real Costco cart and open checkout review\n"
                    "/checkout - reread the current Costco checkout review"
                ),
            )
            return
        if lowered.startswith("/preflight"):
            self._send_preflight(chat_id)
            return
        if lowered.startswith("/checkout"):
            self._send_checkout_review(chat_id)
            return
        if lowered.startswith("/grocery"):
            item_text = text[len("/grocery") :].strip()
            if not item_text:
                self._send_message(chat_id, "Send a comma-separated list, for example:\n/grocery strawberries, onions, olive oil")
                return
            self._open_and_build_real_cart(chat_id, _parse_items(item_text))
            return
        if lowered.startswith("/costco"):
            item_text = text[len("/costco") :].strip()
            if not item_text:
                self._send_message(chat_id, "Send a comma-separated list, for example:\n/costco strawberries, onions, olive oil")
                return
            self._build_real_cart(chat_id, _parse_items(item_text))
            return
        self._send_message(chat_id, "Send /grocery followed by a comma-separated grocery list.")

    def _handle_callback(self, callback: dict[str, Any]) -> None:
        message = callback.get("message", {})
        chat_id = str(message.get("chat", {}).get("id", ""))
        callback_id = str(callback.get("id", ""))
        if not self._chat_allowed(chat_id):
            self._answer_callback(callback_id, "This chat is not authorized.")
            return
        data = str(callback.get("data", ""))
        if data == "real_checkout_refresh":
            self._answer_callback(callback_id, "Refreshing checkout review.")
            self._send_checkout_review(chat_id)
            return
        if data == "real_place_order":
            try:
                review = self.browser_agent.place_order("I approve, place the order from Telegram.")
            except Exception as exc:
                self._answer_callback(callback_id, "Order was not placed.")
                self._send_message(chat_id, f"Could not place order:\n{exc}")
                return
            self._answer_callback(callback_id, "Order placed.")
            self._send_message(chat_id, "Order placed.\n\n" + _checkout_review_message(review))
            return
        self._answer_callback(callback_id, "Unknown action.")

    def _send_preflight(self, chat_id: str) -> None:
        try:
            report = self.browser_agent.preflight()
        except Exception as exc:
            self._send_message(chat_id, f"Preflight failed:\n{exc}")
            return
        lines = [
            "Costco Same Day preflight",
            f"OK: {report.ok}",
            f"URL: {report.url}",
            f"Signed in: {report.signed_in}",
            f"Delivery address: {report.delivery_address or 'unknown'}",
            f"Cart count: {report.cart_count if report.cart_count is not None else 'unknown'}",
        ]
        if report.issues:
            lines.extend(["", "Issues:", *[f"- {issue}" for issue in report.issues]])
        self._send_message(chat_id, "\n".join(lines))

    def _build_real_cart(self, chat_id: str, items: list[str]) -> None:
        self._send_message(chat_id, f"Building real Costco cart for: {', '.join(items)}")
        try:
            result = self.browser_agent.build_cart(items)
        except (SafetyGateError, Exception) as exc:
            self._send_message(chat_id, f"Could not build Costco cart:\n{exc}")
            return
        lines = ["Costco cart build complete."]
        if result.added:
            lines.extend(["", "Added:", *[f"- {item}" for item in result.added]])
        if result.missing_rules:
            lines.extend(["", "Needs product mapping:", *[f"- {item}" for item in result.missing_rules]])
        self._send_message(chat_id, "\n".join(lines))
        if result.added:
            self._send_live_cart_text(chat_id)
            self._send_checkout_review(chat_id)

    def _open_and_build_real_cart(self, chat_id: str, items: list[str]) -> None:
        self._send_message(chat_id, "Opening Costco Same Day in Chrome.")
        try:
            report = self.browser_agent.open_storefront()
        except Exception as exc:
            self._send_message(chat_id, f"Could not open Costco Same Day in Chrome:\n{exc}")
            return
        if not report.ok:
            lines = ["Costco is open, but preflight needs attention.", *[f"- {issue}" for issue in report.issues]]
            self._send_message(chat_id, "\n".join(lines))
            return
        self._build_real_cart(chat_id, items)

    def _send_checkout_review(self, chat_id: str) -> None:
        try:
            review = self.browser_agent.open_checkout_review()
            review = self.browser_agent.apply_checkout_policy()
        except Exception as exc:
            self._send_message(chat_id, f"Checkout review is not ready:\n{exc}")
            self._send_live_cart_text(chat_id)
            return
        self.client.call(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": _checkout_review_message(review),
                "reply_markup": {
                    "inline_keyboard": [
                        [
                            {"text": "Refresh review", "callback_data": "real_checkout_refresh"},
                            {"text": "Approve and place order", "callback_data": "real_place_order"},
                        ],
                    ],
                },
            },
        )

    def _send_live_cart_text(self, chat_id: str) -> None:
        try:
            cart_text = self.browser_agent.open_cart_review_text()
        except Exception as exc:
            self._send_message(chat_id, f"Could not read live Costco cart text:\n{exc}")
            return
        self._send_message(chat_id, "Current Costco cart:\n\n" + _telegram_limit(cart_text))

    def _send_message(self, chat_id: str, text: str) -> None:
        self.client.call("sendMessage", {"chat_id": chat_id, "text": text})

    def _answer_callback(self, callback_id: str, text: str) -> None:
        if callback_id:
            self.client.call("answerCallbackQuery", {"callback_query_id": callback_id, "text": text[:200]})

    def _chat_allowed(self, chat_id: str) -> bool:
        return bool(chat_id) and (self.allowed_chat_id is None or chat_id == self.allowed_chat_id)


def _review_message(cart: Cart, review_url: str) -> str:
    item_count = sum(item.quantity for item in cart.items)
    missing_count = len(cart.out_of_stock)
    lines = [
        "Costco cart ready for review",
        f"Cart: {cart.id}",
        f"Items: {item_count}",
        f"Estimated total: ${cart.total_cost:.2f}",
        f"Status: {cart.status}",
    ]
    if missing_count:
        lines.append(f"Needs review: {missing_count} out-of-stock item(s)")
    lines.extend(
        [
            "",
            "Open this link on your phone to approve or reject:",
            review_url,
            "",
            "Approval authorizes checkout review only. Final purchase still needs explicit approval.",
        ]
    )
    return "\n".join(lines)


def _telegram_cart_message(cart: Cart, final_line: str | None = None) -> str:
    lines = [
        "Costco cart review",
        f"Cart: {cart.id}",
        f"Status: {cart.status}",
        f"Estimated total: ${cart.total_cost:.2f}",
        "",
        "Items:",
    ]
    for item in cart.items:
        flags = f" ({', '.join(item.flags)})" if item.flags else ""
        lines.append(f"- {item.product.name} x{item.quantity}: ${item.line_total:.2f}{flags}")
    if cart.out_of_stock:
        lines.extend(["", "Needs review:"])
        for item in cart.out_of_stock:
            lines.append(f"- {item.requested_item}: {item.reason}")
    lines.extend(["", final_line or "Approval authorizes checkout review only. Final order placement still requires explicit purchase approval."])
    return "\n".join(lines)


def _cart_keyboard(cart: Cart) -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [
                {"text": "Approve cart", "callback_data": f"approve:{cart.id}"},
                {"text": "Reject", "callback_data": f"reject:{cart.id}"},
            ],
        ],
    }


def _checkout_review_message(review: CheckoutReview) -> str:
    lines = [
        "Real Costco checkout review",
        f"Items: {review.item_count if review.item_count is not None else 'unknown'}",
        f"Subtotal: {_money(review.subtotal)}",
        f"Estimated tax: {_money(review.estimated_tax)}",
        f"Delivery tip: {_money(review.delivery_tip)}",
        f"Total: {_money(review.total)}",
        f"Delivery window: {review.delivery_window or 'unknown'}",
        f"Delivery address: {review.delivery_address or 'unknown'}",
        f"Payment: {review.payment_summary or 'unknown'}",
        f"Place order visible: {review.place_order_visible}",
    ]
    if review.items:
        lines.extend(["", "Checkout items:", *[f"- {item}" for item in review.items[:20]]])
    lines.extend(
        [
            "",
            "Tap Approve and place order only after checking total, address, payment, delivery window, and items.",
        ]
    )
    return "\n".join(lines)


def _parse_items(value: str) -> list[str]:
    normalized = value.replace("\n", ",")
    return [item.strip() for item in normalized.split(",") if item.strip()]


def _money(value: float | None) -> str:
    return "unknown" if value is None else f"${value:.2f}"


def _telegram_limit(value: str, limit: int = 3500) -> str:
    cleaned = value.strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 40].rstrip() + "\n\n...truncated for Telegram..."


def _post_json(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    req = request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with request.urlopen(req, timeout=20) as response:
            decoded = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise NotificationError(f"Telegram request failed with HTTP {exc.code}: {detail}") from exc
    except (URLError, TimeoutError) as exc:
        raise NotificationError(f"Telegram request failed: {exc}") from exc
    if not isinstance(decoded, dict):
        raise NotificationError("Telegram response was not a JSON object.")
    return decoded
