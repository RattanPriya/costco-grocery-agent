from __future__ import annotations

from collections import Counter, defaultdict
from datetime import UTC, date, datetime

from grocery_agent.costco import CostcoClient
from grocery_agent.gemini_hosting import CalendarEvent, HostingCartRecommendation, build_hosting_plan, detect_hosting_events
from grocery_agent.models import (
    ApprovalRecord,
    Cart,
    CartItem,
    CartStatus,
    DecisionLogEntry,
    GroceryProfile,
    OrderRecord,
    OutOfStockItem,
    PantryEstimate,
    Product,
)
from grocery_agent.storage import JsonStore


class ApprovalRequiredError(RuntimeError):
    pass


class GroceryAgent:
    def __init__(self, store: JsonStore, costco: CostcoClient) -> None:
        self.store = store
        self.costco = costco

    def generate_cart(self, requested_items: list[str], today: date | None = None, proactive: bool = False) -> Cart:
        profile = self.store.load_profile()
        request = [item.strip() for item in requested_items if item.strip()]
        if proactive:
            request = self._proactive_request(profile, today or date.today(), request)

        cart = Cart(requested_items=request)
        cart.fulfillment_options = self.costco.fulfillment_options()
        cart.selected_fulfillment = self._choose_fulfillment(profile, cart.fulfillment_options)
        cart.decision_log.append(_log("cart_created", f"Started cart with {len(request)} requested item(s)."))

        for item in request:
            self._add_best_match(cart, profile, item)

        self._flag_budget_risks(cart, profile)
        cart.status = CartStatus.REVIEW_READY
        self.store.save_cart(cart)
        return cart

    def generate_gemini_hosting_cart(
        self,
        calendar_events: list[CalendarEvent],
        today: date | None = None,
        horizon_days: int = 14,
    ) -> HostingCartRecommendation | None:
        profile = self.store.load_profile()
        detected = detect_hosting_events(calendar_events, today or date.today(), horizon_days=horizon_days)
        if not detected:
            return None

        plan = build_hosting_plan(detected[0], profile)
        cart = self.generate_cart(plan.grocery_items, today=today)
        cart.decision_log.append(
            _log(
                "gemini_hosting_event_detected",
                f"{plan.gemini_surface}: {plan.event.title} on {plan.event.start.isoformat()} for about {plan.guest_count} guest(s).",
            )
        )
        cart.decision_log.append(_log("gemini_meal_plan_suggested", "Menu: " + "; ".join(plan.menu)))
        self.store.save_cart(cart)
        return HostingCartRecommendation(plan=plan, cart=cart)

    def review_summary(self, cart: Cart | None = None) -> str:
        cart = cart or self._latest_cart_or_raise()
        lines = [f"Cart {cart.id} [{cart.status}]", ""]
        for item in cart.items:
            sub = f" (substitution for {item.substitution_for})" if item.substitution_for else ""
            flags = f" Flags: {', '.join(item.flags)}." if item.flags else ""
            lines.append(f"- {item.product.name} x{item.quantity}: ${item.line_total:.2f}{sub}")
            lines.append(f"  Why: {item.reason}.{flags}")
        if cart.out_of_stock:
            lines.append("")
            lines.append("Out of stock:")
            for missing in cart.out_of_stock:
                names = ", ".join(product.name for product in missing.suggested_substitutions) or "No substitution found"
                lines.append(f"- {missing.requested_item}: {missing.reason} Substitutions: {names}")
        lines.extend(
            [
                "",
                f"Subtotal: ${cart.subtotal:.2f}",
                f"Estimated total: ${cart.total_cost:.2f}",
                f"Fulfillment: {cart.selected_fulfillment or 'Not selected'}",
                "Approval required before purchase.",
            ]
        )
        return "\n".join(lines)

    def approve_cart(self, cart_id: str | None = None, approver: str = "user", statement: str = "I approve this Costco cart for checkout.") -> Cart:
        cart = self._cart_by_id(cart_id) if cart_id else self._latest_cart_or_raise()
        if cart.status is not CartStatus.REVIEW_READY:
            raise ApprovalRequiredError(f"Only review-ready carts can be approved; current status is {cart.status}.")
        cart.status = CartStatus.APPROVED
        cart.approval = ApprovalRecord(approved_by=approver, approved_at=datetime.now(UTC), statement=statement)
        cart.decision_log.append(_log("cart_approved", f"Approved by {approver}: {statement}"))
        self.store.save_cart(cart)
        return cart

    def reject_cart(self, cart_id: str | None = None, reason: str = "Rejected by user.") -> Cart:
        cart = self._cart_by_id(cart_id) if cart_id else self._latest_cart_or_raise()
        cart.status = CartStatus.REJECTED
        cart.decision_log.append(_log("cart_rejected", reason))
        self.store.save_cart(cart)
        self.learn_from_rejections(cart)
        return cart

    def place_order(self, cart_id: str | None = None) -> OrderRecord:
        cart = self._cart_by_id(cart_id) if cart_id else self._latest_cart_or_raise()
        if cart.status is not CartStatus.APPROVED or cart.approval is None:
            raise ApprovalRequiredError("Human approval is required before placing a Costco order.")
        order_id = self.costco.place_order(cart)
        order = OrderRecord(order_id=order_id, cart_id=cart.id, placed_at=datetime.now(UTC), items=cart.items, total_cost=cart.total_cost)
        cart.status = CartStatus.ORDER_PLACED
        cart.placed_order_id = order_id
        cart.decision_log.append(_log("order_placed", f"Mock Costco order id {order_id}."))
        self.store.save_order(order)
        self.store.save_cart(cart)
        self.learn_from_order(order)
        return order

    def edit_quantity(self, cart_id: str | None, sku: str, quantity: int) -> Cart:
        cart = self._cart_by_id(cart_id) if cart_id else self._latest_cart_or_raise()
        for item in cart.items:
            if item.product.sku == sku:
                item.quantity = quantity
                item.reason += f" User edited quantity to {quantity}"
                cart.decision_log.append(_log("quantity_edited", f"{item.product.name} quantity set to {quantity}."))
                self.store.save_cart(cart)
                return cart
        raise ValueError(f"SKU {sku} not found in cart.")

    def learn_from_order(self, order: OrderRecord) -> None:
        profile = self.store.load_profile()
        by_category = defaultdict(Counter)
        for historical in self.store.load_orders() + [order]:
            for item in historical.items:
                by_category[item.product.category][item.product.brand] += item.quantity

        for category, counts in by_category.items():
            profile.preferences.preferred_brands[category] = [brand for brand, _ in counts.most_common(3)]

        pantry_by_item = {entry.item.lower(): entry for entry in profile.pantry}
        for item in order.items:
            key = item.product.category.lower()
            estimate = pantry_by_item.get(key)
            if estimate is None:
                profile.pantry.append(
                    PantryEstimate(
                        item=key,
                        category=item.product.category,
                        cadence_days=_default_cadence(item.product.category),
                        last_purchased=order.placed_at.date(),
                        usual_quantity=item.quantity,
                    )
                )
            else:
                estimate.last_purchased = order.placed_at.date()
                estimate.usual_quantity = item.quantity
        self.store.save_profile(profile)

    def learn_from_rejections(self, cart: Cart) -> None:
        profile = self.store.load_profile()
        for item in cart.items:
            rejected = profile.preferences.rejected_brands.setdefault(item.product.category, [])
            if item.product.brand not in rejected:
                rejected.append(item.product.brand)
        self.store.save_profile(profile)

    def _add_best_match(self, cart: Cart, profile: GroceryProfile, requested_item: str) -> None:
        if requested_item.lower() in {item.lower() for item in profile.preferences.never_buy}:
            cart.decision_log.append(_log("never_buy_skipped", f"Skipped {requested_item} due to never-buy rule."))
            return

        matches = self.costco.search_products(requested_item)
        if not matches:
            cart.out_of_stock.append(OutOfStockItem(requested_item=requested_item, attempted_product=None, reason="No Costco catalog match found."))
            return

        ranked = sorted(matches, key=lambda product: self._score_product(profile, requested_item, product), reverse=True)
        chosen = next((product for product in ranked if product.in_stock), None)
        attempted = ranked[0]
        if chosen is None:
            substitutions = self._substitutions_for(attempted, ranked)
            cart.out_of_stock.append(
                OutOfStockItem(
                    requested_item=requested_item,
                    attempted_product=attempted,
                    suggested_substitutions=substitutions,
                    reason=f"Best match {attempted.name} is out of stock.",
                )
            )
            return

        substitution_for = None if chosen is attempted else attempted.name
        quantity = self._usual_quantity(profile, chosen.category)
        reason = self._reason_for_choice(profile, requested_item, chosen, substitution_for)
        cart.items.append(CartItem(product=chosen, quantity=quantity, requested_item=requested_item, reason=reason, substitution_for=substitution_for))
        cart.decision_log.append(_log("item_added", f"Added {chosen.name} for {requested_item}: {reason}."))

    def _score_product(self, profile: GroceryProfile, requested_item: str, product: Product) -> int:
        score = 0
        normalized = requested_item.lower()
        if normalized in product.name.lower() or normalized in product.category.lower() or normalized in product.tags:
            score += 20
        product_text = " ".join([product.name, product.category, product.brand, *product.tags]).lower()
        if any(never.lower() in product_text for never in profile.preferences.never_buy):
            score -= 100
        if product.in_stock:
            score += 10
        if product.brand in profile.preferences.preferred_brands.get(product.category, []):
            score += 8
        if product.brand in profile.preferences.rejected_brands.get(product.category, []):
            score -= 15
        if product.brand == "Kirkland":
            score += 3
        for restriction in profile.preferences.dietary_restrictions:
            if restriction.lower() in {"organic", "gluten-free"} and restriction.lower() in product.tags:
                score += 2
        return score

    def _reason_for_choice(self, profile: GroceryProfile, requested_item: str, product: Product, substitution_for: str | None) -> str:
        parts = [f"matched '{requested_item}' to Costco item"]
        if product.brand in profile.preferences.preferred_brands.get(product.category, []):
            parts.append(f"preferred {product.brand} brand")
        elif product.brand == "Kirkland":
            parts.append("Kirkland default value preference")
        if substitution_for:
            parts.append(f"available substitute for {substitution_for}")
        return "; ".join(parts)

    def _flag_budget_risks(self, cart: Cart, profile: GroceryProfile) -> None:
        budget = profile.preferences.budget
        for item in cart.items:
            if item.line_total >= budget.expensive_item_threshold:
                item.flags.append(f"expensive item over ${budget.expensive_item_threshold:.0f}")
        if cart.total_cost > budget.hard_limit:
            cart.decision_log.append(_log("budget_limit_exceeded", f"Estimated total ${cart.total_cost:.2f} exceeds hard limit ${budget.hard_limit:.2f}."))
        elif cart.total_cost > budget.target_total:
            cart.decision_log.append(_log("budget_target_exceeded", f"Estimated total ${cart.total_cost:.2f} exceeds target ${budget.target_total:.2f}."))

    def _substitutions_for(self, attempted: Product, ranked_matches: list[Product]) -> list[Product]:
        seen = {attempted.sku}
        substitutions = [product for product in ranked_matches[1:] if product.in_stock and product.sku not in seen]
        seen.update(product.sku for product in substitutions)
        for query in [attempted.category, *attempted.tags]:
            for product in self.costco.search_products(query):
                if product.in_stock and product.sku not in seen and product.category == attempted.category:
                    substitutions.append(product)
                    seen.add(product.sku)
        attempted_tags = set(attempted.tags)
        return sorted(
            substitutions,
            key=lambda product: (len(attempted_tags.intersection(product.tags)), -product.price),
            reverse=True,
        )[:3]

    def _choose_fulfillment(self, profile: GroceryProfile, options: list[str]) -> str | None:
        preferred = [window.lower() for window in profile.preferences.preferred_delivery_windows]
        for option in options:
            if any(window.lower() in option.lower() for window in preferred):
                return option
        return options[0] if options else None

    def _proactive_request(self, profile: GroceryProfile, today: date, seed_items: list[str]) -> list[str]:
        items = list(dict.fromkeys([*seed_items, *profile.preferences.always_buy]))
        for estimate in profile.pantry:
            if estimate.due_by(today):
                items.append(estimate.item)
        return list(dict.fromkeys(items))

    def _usual_quantity(self, profile: GroceryProfile, category: str) -> int:
        for estimate in profile.pantry:
            if estimate.category == category or estimate.item == category:
                return max(1, estimate.usual_quantity)
        return 1

    def _latest_cart_or_raise(self) -> Cart:
        cart = self.store.latest_cart()
        if cart is None:
            raise ValueError("No carts found.")
        return cart

    def _cart_by_id(self, cart_id: str | None) -> Cart:
        for cart in self.store.load_carts():
            if cart.id == cart_id:
                return cart
        raise ValueError(f"Cart {cart_id} not found.")


def _log(action: str, detail: str) -> DecisionLogEntry:
    return DecisionLogEntry(timestamp=datetime.now(UTC), action=action, detail=detail)


def _default_cadence(category: str) -> int:
    return {
        "milk": 14,
        "eggs": 21,
        "produce": 7,
        "baby": 21,
        "household": 45,
        "snacks": 21,
    }.get(category, 30)
