from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4


class CartStatus(StrEnum):
    DRAFT = "draft"
    REVIEW_READY = "review_ready"
    APPROVED = "approved"
    REJECTED = "rejected"
    ORDER_PLACED = "order_placed"


class FulfillmentType(StrEnum):
    DELIVERY = "delivery"
    PICKUP = "pickup"


@dataclass(slots=True)
class Budget:
    target_total: float = 250.0
    hard_limit: float = 400.0
    expensive_item_threshold: float = 40.0


@dataclass(slots=True)
class HouseholdPreference:
    preferred_brands: dict[str, list[str]] = field(default_factory=dict)
    rejected_brands: dict[str, list[str]] = field(default_factory=dict)
    dietary_restrictions: list[str] = field(default_factory=list)
    preferred_pack_sizes: dict[str, str] = field(default_factory=dict)
    always_buy: list[str] = field(default_factory=list)
    never_buy: list[str] = field(default_factory=list)
    substitution_preferences: dict[str, list[str]] = field(default_factory=dict)
    product_rules: dict[str, "ProductRule"] = field(default_factory=dict)
    preferred_fulfillment: FulfillmentType = FulfillmentType.DELIVERY
    preferred_delivery_windows: list[str] = field(default_factory=lambda: ["Sunday 9am-12pm"])
    budget: Budget = field(default_factory=Budget)
    checkout_policy: "CheckoutPolicy" = field(default_factory=lambda: CheckoutPolicy())


@dataclass(slots=True)
class ProductRule:
    canonical_item: str
    search_query: str
    preferred_product_name: str
    quantity: int = 1
    notes: str = ""
    max_price: float | None = None


@dataclass(slots=True)
class CheckoutPolicy:
    delivery_address: str | None = None
    delivery_zip: str | None = None
    preferred_tip: float = 0.0
    preferred_fulfillment: str = "standard"
    max_total_without_reapproval: float = 250.0
    allow_autonomous_checkout: bool = False
    require_final_place_order_approval: bool = True


@dataclass(slots=True)
class PantryEstimate:
    item: str
    category: str
    cadence_days: int
    last_purchased: date | None = None
    usual_quantity: int = 1

    def due_by(self, today: date) -> bool:
        if self.last_purchased is None:
            return True
        return (today - self.last_purchased).days >= self.cadence_days


@dataclass(slots=True)
class GroceryProfile:
    household_name: str = "My Household"
    preferences: HouseholdPreference = field(default_factory=HouseholdPreference)
    pantry: list[PantryEstimate] = field(default_factory=list)


@dataclass(slots=True)
class Product:
    sku: str
    name: str
    category: str
    brand: str
    unit_size: str
    price: float
    in_stock: bool = True
    tags: list[str] = field(default_factory=list)


@dataclass(slots=True)
class CartItem:
    product: Product
    quantity: int
    requested_item: str
    reason: str
    substitution_for: str | None = None
    flags: list[str] = field(default_factory=list)

    @property
    def line_total(self) -> float:
        return round(self.product.price * self.quantity, 2)


@dataclass(slots=True)
class OutOfStockItem:
    requested_item: str
    attempted_product: Product | None
    suggested_substitutions: list[Product] = field(default_factory=list)
    reason: str = "No in-stock match found."


@dataclass(slots=True)
class ApprovalRecord:
    approved_by: str
    approved_at: datetime
    statement: str


@dataclass(slots=True)
class DecisionLogEntry:
    timestamp: datetime
    action: str
    detail: str


@dataclass(slots=True)
class Cart:
    id: str = field(default_factory=lambda: str(uuid4()))
    status: CartStatus = CartStatus.DRAFT
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    requested_items: list[str] = field(default_factory=list)
    items: list[CartItem] = field(default_factory=list)
    out_of_stock: list[OutOfStockItem] = field(default_factory=list)
    fulfillment_options: list[str] = field(default_factory=list)
    selected_fulfillment: str | None = None
    decision_log: list[DecisionLogEntry] = field(default_factory=list)
    approval: ApprovalRecord | None = None
    placed_order_id: str | None = None

    @property
    def subtotal(self) -> float:
        return round(sum(item.line_total for item in self.items), 2)

    @property
    def total_cost(self) -> float:
        # Mocked estimate: Costco item subtotal plus delivery/service estimate.
        delivery_fee = 8.99 if self.items and self.selected_fulfillment and "Delivery" in self.selected_fulfillment else 0.0
        return round(self.subtotal + delivery_fee, 2)


@dataclass(slots=True)
class OrderRecord:
    order_id: str
    cart_id: str
    placed_at: datetime
    items: list[CartItem]
    total_cost: float


@dataclass(slots=True)
class BrowserPageState:
    url: str
    title: str
    body_text: str
    buttons: list[str] = field(default_factory=list)
    inputs: list[str] = field(default_factory=list)
    dialogs: list[str] = field(default_factory=list)


@dataclass(slots=True)
class PreflightReport:
    ok: bool
    url: str
    title: str
    signed_in: bool
    delivery_address: str | None
    cart_count: int | None
    issues: list[str] = field(default_factory=list)


@dataclass(slots=True)
class CheckoutReview:
    items: list[str]
    item_count: int | None
    subtotal: float | None
    estimated_tax: float | None
    delivery_tip: float | None
    total: float | None
    delivery_window: str | None
    delivery_address: str | None
    payment_summary: str | None
    place_order_visible: bool
    raw_text: str = ""


def to_plain(value: Any) -> Any:
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if hasattr(value, "__dataclass_fields__"):
        return {field_name: to_plain(getattr(value, field_name)) for field_name in value.__dataclass_fields__}
    if isinstance(value, list):
        return [to_plain(item) for item in value]
    if isinstance(value, dict):
        return {key: to_plain(item) for key, item in value.items()}
    return value
