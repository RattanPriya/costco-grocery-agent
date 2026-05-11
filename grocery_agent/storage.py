from __future__ import annotations

import json
from dataclasses import fields, is_dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, TypeVar, get_args, get_origin, get_type_hints

from grocery_agent.models import (
    ApprovalRecord,
    Budget,
    Cart,
    CartItem,
    CartStatus,
    DecisionLogEntry,
    FulfillmentType,
    GroceryProfile,
    HouseholdPreference,
    OrderRecord,
    OutOfStockItem,
    PantryEstimate,
    Product,
    to_plain,
)

T = TypeVar("T")


class JsonStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load_profile(self) -> GroceryProfile:
        data = self._read()
        profile_data = data.get("profile")
        if profile_data is None:
            profile = GroceryProfile()
            self.save_profile(profile)
            return profile
        return _decode(GroceryProfile, profile_data)

    def save_profile(self, profile: GroceryProfile) -> None:
        data = self._read()
        data["profile"] = to_plain(profile)
        self._write(data)

    def load_carts(self) -> list[Cart]:
        return [_decode(Cart, item) for item in self._read().get("carts", [])]

    def save_cart(self, cart: Cart) -> None:
        data = self._read()
        carts = [item for item in data.get("carts", []) if item.get("id") != cart.id]
        carts.append(to_plain(cart))
        data["carts"] = carts
        self._write(data)

    def latest_cart(self) -> Cart | None:
        carts = self.load_carts()
        if not carts:
            return None
        return sorted(carts, key=lambda cart: cart.created_at)[-1]

    def load_orders(self) -> list[OrderRecord]:
        return [_decode(OrderRecord, item) for item in self._read().get("orders", [])]

    def save_order(self, order: OrderRecord) -> None:
        data = self._read()
        orders = [item for item in data.get("orders", []) if item.get("order_id") != order.order_id]
        orders.append(to_plain(order))
        data["orders"] = orders
        self._write(data)

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        with self.path.open("r", encoding="utf-8") as handle:
            return json.load(handle)

    def _write(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, sort_keys=True)


def _decode(expected_type: type[T], value: Any) -> T:
    origin = get_origin(expected_type)
    if origin is list:
        item_type = get_args(expected_type)[0]
        return [_decode(item_type, item) for item in value]  # type: ignore[return-value]
    if origin is dict:
        value_type = get_args(expected_type)[1]
        return {key: _decode(value_type, item) for key, item in value.items()}  # type: ignore[return-value]
    if hasattr(expected_type, "__args__") and type(None) in get_args(expected_type):
        if value is None:
            return None  # type: ignore[return-value]
        concrete = next(arg for arg in get_args(expected_type) if arg is not type(None))
        return _decode(concrete, value)
    if expected_type is datetime:
        return datetime.fromisoformat(value)  # type: ignore[return-value]
    if expected_type is date:
        return date.fromisoformat(value)  # type: ignore[return-value]
    if expected_type is CartStatus:
        return CartStatus(value)  # type: ignore[return-value]
    if expected_type is FulfillmentType:
        return FulfillmentType(value)  # type: ignore[return-value]
    if expected_type in {str, int, float, bool}:
        return expected_type(value)  # type: ignore[return-value]
    if is_dataclass(expected_type):
        type_hints = get_type_hints(expected_type)
        kwargs = {}
        for field_def in fields(expected_type):
            if field_def.name in value:
                kwargs[field_def.name] = _decode(type_hints[field_def.name], value[field_def.name])
        return expected_type(**kwargs)
    return value
