from __future__ import annotations

import re
import time
from dataclasses import dataclass

from grocery_agent.browser import BrowserAutomationError, BrowserSession
from grocery_agent.models import (
    BrowserPageState,
    CheckoutPolicy,
    CheckoutReview,
    GroceryProfile,
    PreflightReport,
    ProductRule,
)


class SafetyGateError(RuntimeError):
    pass


@dataclass(slots=True)
class CartBuildResult:
    added: list[str]
    missing_rules: list[str]
    preflight: PreflightReport


class CostcoSameDayBrowserAgent:
    def __init__(self, browser: BrowserSession, profile: GroceryProfile) -> None:
        self.browser = browser
        self.profile = profile

    def preflight(self) -> PreflightReport:
        state = self.browser.current_state()
        issues: list[str] = []
        if "sameday.costco.com" not in state.url:
            issues.append(f"Active tab is not Costco Same Day: {state.url}")
        signed_in = "Account Menu" in _visible_text(state) and "Sign In / Register" not in _visible_text(state)
        if not signed_in:
            issues.append("Costco Same Day does not appear signed in.")
        delivery_address = _extract_delivery_address(state.body_text)
        policy = self.profile.preferences.checkout_policy
        if policy.delivery_address and delivery_address and policy.delivery_address.lower() not in delivery_address.lower():
            issues.append(f"Delivery address differs from policy: {delivery_address}")
        cart_count = _extract_cart_count(state.body_text)
        return PreflightReport(
            ok=not issues,
            url=state.url,
            title=state.title,
            signed_in=signed_in,
            delivery_address=delivery_address,
            cart_count=cart_count,
            issues=issues,
        )

    def build_cart(self, requested_items: list[str]) -> CartBuildResult:
        preflight = self.preflight()
        if not preflight.ok:
            raise SafetyGateError("; ".join(preflight.issues))

        added: list[str] = []
        missing_rules: list[str] = []
        for item in requested_items:
            rule = resolve_product_rule(self.profile, item)
            if rule is None:
                missing_rules.append(item)
                continue
            self.browser.navigate(f"https://sameday.costco.com/store/costco/s?k={_quote_query(rule.search_query)}")
            state = _wait_for_product_text(self.browser, rule.preferred_product_name)
            if rule.preferred_product_name not in state.body_text:
                missing_rules.append(item)
                continue
            try:
                self.browser.click_button(aria=f"Add 1 ct {rule.preferred_product_name}")
            except BrowserAutomationError:
                try:
                    self.browser.click_button_near_text(rule.preferred_product_name, "Add")
                except BrowserAutomationError:
                    missing_rules.append(item)
                    continue
            added.append(rule.preferred_product_name)
        return CartBuildResult(added=added, missing_rules=missing_rules, preflight=preflight)

    def open_checkout_review(self) -> CheckoutReview:
        preflight = self.preflight()
        if not preflight.ok:
            raise SafetyGateError("; ".join(preflight.issues))
        self.browser.click_button_containing(text="Go to checkout")
        self.browser.click_button(text="Continue to checkout")
        return self.read_checkout_review()

    def read_checkout_review(self) -> CheckoutReview:
        state = self.browser.current_state()
        return parse_checkout_review(state)

    def apply_checkout_policy(self) -> CheckoutReview:
        policy = self.profile.preferences.checkout_policy
        review = self.read_checkout_review()
        if policy.preferred_tip == 0 and review.delivery_tip not in {None, 0.0}:
            self.browser.click_button(text="Other")
            self.browser.set_text_input('input[placeholder="Other amount"]', "0")
            self.browser.click_button(text="Save Tip")
            state = self.browser.current_state()
            if "Continue with $0 tip" in _visible_text(state):
                self.browser.click_button(text="Continue with $0 tip")
            review = self.read_checkout_review()
        if policy.max_total_without_reapproval and review.total and review.total > policy.max_total_without_reapproval:
            raise SafetyGateError(f"Checkout total ${review.total:.2f} exceeds policy limit ${policy.max_total_without_reapproval:.2f}.")
        return review

    def place_order(self, approval_statement: str) -> CheckoutReview:
        if "approve" not in approval_statement.lower() or "place" not in approval_statement.lower():
            raise SafetyGateError("Explicit approval statement must include approve and place.")
        review = self.read_checkout_review()
        if not review.place_order_visible:
            raise SafetyGateError("Place order button is not visible.")
        self.browser.click_button(text="Place order")
        return review


def resolve_product_rule(profile: GroceryProfile, requested_item: str) -> ProductRule | None:
    key = _normalize(requested_item)
    rules = profile.preferences.product_rules
    if key in rules:
        return rules[key]
    for rule_key, rule in rules.items():
        names = {_normalize(rule_key), _normalize(rule.canonical_item), _normalize(rule.preferred_product_name)}
        if key in names:
            return rule
    return None


def remember_product_rule(profile: GroceryProfile, rule: ProductRule) -> None:
    profile.preferences.product_rules[_normalize(rule.canonical_item)] = rule


def _wait_for_product_text(browser: BrowserSession, product_name: str, timeout_seconds: float = 20.0) -> BrowserPageState:
    deadline = time.monotonic() + timeout_seconds
    state = browser.current_state()
    while product_name not in state.body_text and time.monotonic() < deadline:
        time.sleep(2)
        state = browser.current_state()
    return state


def parse_checkout_review(state: BrowserPageState) -> CheckoutReview:
    text = state.body_text
    return CheckoutReview(
        items=_extract_review_items(text),
        item_count=_extract_int_before(text, "items (loyalty applied)") or _extract_int_before(text, "items"),
        subtotal=_extract_money_after(text, "items (loyalty applied)"),
        estimated_tax=_extract_money_after(text, "Est. tax"),
        delivery_tip=_extract_money_after(text, "Delivery Tip"),
        total=_extract_money_after(text, "Total"),
        delivery_window=_extract_delivery_window(text),
        delivery_address=_extract_checkout_address(text),
        payment_summary=_extract_payment(text),
        place_order_visible="Place order" in _visible_text(state),
        raw_text=text,
    )


def _visible_text(state: BrowserPageState) -> str:
    return "\n".join([state.body_text, *state.buttons, *state.inputs, *state.dialogs])


def _normalize(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def _quote_query(value: str) -> str:
    return re.sub(r"\s+", "%20", value.strip())


def _extract_cart_count(text: str) -> int | None:
    matches = re.findall(r"\n(\d+)\n(?:Add \$|Departments|View cart)", text)
    if matches:
        return int(matches[-1])
    return None


def _extract_delivery_address(text: str) -> str | None:
    match = re.search(r"Delivery ·[^\n]+\n([^\n]+)", text)
    return match.group(1).strip() if match else None


def _extract_checkout_address(text: str) -> str | None:
    match = re.search(r"Deliver to\n(.+?)\n", text)
    return match.group(1).strip() if match else _extract_delivery_address(text)


def _extract_delivery_window(text: str) -> str | None:
    match = re.search(r"(?:Priority|Standard)\n([0-9:apm–\- ]+)", text)
    return match.group(1).strip() if match else None


def _extract_payment(text: str) -> str | None:
    match = re.search(r"Pay with\n+.*?\n(.*?Visa \*\d+|Visa \d+)", text, re.S)
    return match.group(1).strip() if match else None


def _extract_money_after(text: str, label: str) -> float | None:
    match = re.search(re.escape(label) + r"(?:[^\n]*\n)+?\$([0-9,]+\.\d{2})", text)
    if not match:
        match = re.search(re.escape(label) + r"\n\$([0-9,]+\.\d{2})", text)
    return float(match.group(1).replace(",", "")) if match else None


def _extract_int_before(text: str, label: str) -> int | None:
    match = re.search(r"(\d+)\s+" + re.escape(label), text)
    return int(match.group(1)) if match else None


def _extract_review_items(text: str) -> list[str]:
    items: list[str] = []
    for line in text.splitlines():
        if line.endswith("(each)") or re.search(r"\(\d+ lb\)$", line):
            items.append(line)
    return items
