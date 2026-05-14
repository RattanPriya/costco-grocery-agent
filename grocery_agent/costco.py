from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import UTC, datetime
from uuid import uuid4

from grocery_agent.models import Cart, CartStatus, Product


class PurchaseSafetyError(RuntimeError):
    pass


class CostcoClient(ABC):
    @abstractmethod
    def search_products(self, query: str) -> list[Product]:
        raise NotImplementedError

    @abstractmethod
    def fulfillment_options(self) -> list[str]:
        raise NotImplementedError

    @abstractmethod
    def place_order(self, cart: Cart) -> str:
        raise NotImplementedError


class MockCostcoClient(CostcoClient):
    def __init__(self, catalog: list[Product] | None = None) -> None:
        self.catalog = catalog or default_catalog()

    def search_products(self, query: str) -> list[Product]:
        normalized = query.lower().strip()
        words = set(normalized.replace(",", " ").split())
        scored: list[tuple[int, Product]] = []
        for product in self.catalog:
            haystack = " ".join([product.name, product.category, product.brand, *product.tags]).lower()
            score = sum(1 for word in words if word in haystack)
            if normalized in haystack:
                score += 3
            if score:
                scored.append((score, product))
        return [product for _, product in sorted(scored, key=lambda item: (-item[0], item[1].price))]

    def fulfillment_options(self) -> list[str]:
        return [
            "Delivery: Sunday 9am-12pm",
            "Delivery: Sunday 2pm-5pm",
            "Pickup: Sunday 11am-1pm",
        ]

    def place_order(self, cart: Cart) -> str:
        if cart.status is not CartStatus.APPROVED or cart.approval is None:
            raise PurchaseSafetyError("Cart must be explicitly approved before checkout.")
        if not cart.items:
            raise PurchaseSafetyError("Cannot place an empty cart.")
        return f"MOCK-COSTCO-{datetime.now(UTC).strftime('%Y%m%d')}-{uuid4().hex[:8].upper()}"


def default_catalog() -> list[Product]:
    return [
        Product("1001", "Kirkland Signature Organic Whole Milk, 3 x 64 fl oz", "milk", "Kirkland", "3 half gallons", 13.49, tags=["dairy", "organic", "staple"]),
        Product("1002", "Horizon Organic Whole Milk, 3 x 64 fl oz", "milk", "Horizon", "3 half gallons", 17.99, tags=["dairy", "organic"]),
        Product("1010", "Kirkland Signature Cage Free Eggs, 24 count", "eggs", "Kirkland", "24 count", 6.99, tags=["dairy", "breakfast", "staple"]),
        Product("1020", "Organic Bananas, 3 lbs", "produce", "Dole", "3 lb", 2.49, tags=["fruit", "banana", "organic"]),
        Product("1021", "Strawberries, 2 lbs", "produce", "Driscoll's", "2 lb", 7.99, in_stock=False, tags=["fruit", "berries"]),
        Product("1022", "Organic Blueberries, 18 oz", "produce", "Naturipe", "18 oz", 8.99, tags=["fruit", "berries", "organic"]),
        Product("1023", "Organic Raspberries, 12 oz", "produce", "Driscoll's", "12 oz", 6.99, tags=["fruit", "berries", "raspberries"]),
        Product("1024", "Navel Oranges, 8 lbs", "produce", "Sunkist", "8 lb", 11.99, tags=["fruit", "citrus", "oranges", "naval"]),
        Product("1025", "Mini Seedless Watermelon, 2 count", "produce", "Dulcinea", "2 count", 9.99, tags=["fruit", "watermelon", "melon"]),
        Product("1026", "Yellow Onions, 10 lbs", "produce", "Peri & Sons", "10 lb", 7.49, tags=["vegetable", "onions", "onion"]),
        Product("1033", "Red Onions, 5 lbs", "produce", "Peri & Sons", "5 lb", 6.99, tags=["vegetable", "onions", "onion", "red"]),
        Product("1027", "Campari Tomatoes, 2 lbs", "produce", "Sunset", "2 lb", 6.49, tags=["vegetable", "tomatoes", "tomato"]),
        Product("1028", "Broccoli Florets, 3 lbs", "produce", "Taylor Farms", "3 lb", 6.99, tags=["vegetable", "broccoli", "brocoli"]),
        Product("1029", "Organic Baby Spinach, 1 lb", "produce", "Earthbound Farm", "1 lb", 5.99, tags=["vegetable", "spinach", "organic"]),
        Product("1032", "Organic Baby Carrots, 5 lbs", "produce", "Grimmway Farms", "5 lb", 6.49, tags=["vegetable", "carrots", "carrot", "organic"]),
        Product("1030", "Kirkland Signature Baby Wipes, 900 count", "baby", "Kirkland", "900 count", 22.99, tags=["baby", "wipes", "household"]),
        Product("1031", "Huggies Plus Diapers Size 4, 174 count", "baby", "Huggies", "174 count", 52.99, tags=["baby", "diapers"]),
        Product("1040", "Kirkland Signature Paper Towels, 12 rolls", "household", "Kirkland", "12 rolls", 23.99, tags=["paper", "cleaning", "staple"]),
        Product("1041", "Charmin Ultra Soft Toilet Paper, 30 rolls", "household", "Charmin", "30 rolls", 31.99, tags=["paper", "bathroom", "staple"]),
        Product("1050", "Kirkland Signature Organic Chicken Breast, 6 lb avg", "meat", "Kirkland", "6 lb", 29.99, tags=["protein", "organic"]),
        Product("1051", "Australian Lamb Loin Chops, 3 lb avg", "meat", "Kirkland", "3 lb", 39.99, tags=["protein", "lamb", "chops"]),
        Product("1060", "Dave's Killer Bread Organic 21 Whole Grains, 2 loaves", "bakery", "Dave's Killer Bread", "2 loaves", 9.99, tags=["bread", "organic"]),
        Product("1070", "Kirkland Signature Greek Yogurt, 48 oz", "yogurt", "Kirkland", "48 oz", 6.49, tags=["dairy", "protein", "breakfast"]),
        Product("1080", "Goldfish Crackers Variety Pack, 45 count", "snacks", "Pepperidge Farm", "45 count", 15.99, tags=["snack", "kids"]),
        Product("1090", "Kirkland Signature Olive Oil, 2 L", "pantry", "Kirkland", "2 L", 19.99, tags=["cooking", "staple"]),
        Product("1091", "Royal Basmati Rice, 20 lbs", "pantry", "Royal", "20 lb", 18.99, tags=["rice", "basmati", "grain", "dinner"]),
        Product("1092", "Garbanzo Beans, 8 x 15.5 oz", "pantry", "Bush's", "8 cans", 10.49, tags=["chickpeas", "garbanzo", "beans", "vegetarian", "protein"]),
        Product("1093", "Stonefire Mini Naan, 18 count", "bakery", "Stonefire", "18 count", 8.99, tags=["naan", "bread", "dinner"]),
        Product("1094", "Sach Foods Paneer, 2 x 14 oz", "dairy", "Sach", "28 oz", 14.99, tags=["paneer", "cheese", "vegetarian", "protein"]),
        Product("1095", "Kirkland Signature Sparkling Water, 35 count", "beverages", "Kirkland", "35 cans", 12.99, tags=["sparkling", "water", "drinks", "party"]),
        Product("1096", "Kirkland Signature Tuxedo Chocolate Mousse Cake", "bakery", "Kirkland", "42 oz", 16.99, tags=["dessert", "cake", "party"]),
        Product("1097", "Barilla Pasta Variety Pack, 8 count", "pantry", "Barilla", "8 boxes", 11.99, tags=["pasta", "dinner", "kids"]),
        Product("1098", "Kirkland Signature Basil Pesto, 22 oz", "pantry", "Kirkland", "22 oz", 9.49, tags=["pesto", "sauce", "pasta"]),
        Product("1100", "Kirkland Signature Laundry Detergent, 194 fl oz", "household", "Kirkland", "194 fl oz", 21.99, tags=["cleaning", "laundry"]),
    ]
