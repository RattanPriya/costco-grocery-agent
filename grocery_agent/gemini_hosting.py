from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
import re
from typing import Any

from grocery_agent.models import Cart, GroceryProfile


HOSTING_KEYWORDS = (
    "dinner",
    "host",
    "hosting",
    "friends over",
    "family over",
    "party",
    "potluck",
    "game night",
)
AWAY_FROM_HOME_KEYWORDS = ("reservation", "restaurant", "dine out", "dinner at", "meet at")


@dataclass(slots=True)
class CalendarEvent:
    title: str
    start: date
    description: str = ""
    attendees: list[str] = field(default_factory=list)
    location: str = ""

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> "CalendarEvent":
        raw_start = str(value.get("start") or value.get("date") or "")
        if not raw_start:
            raise ValueError("Calendar event requires a start date.")
        return cls(
            title=str(value.get("title") or value.get("summary") or "Untitled event"),
            start=_parse_event_date(raw_start),
            description=str(value.get("description") or ""),
            attendees=[str(attendee) for attendee in value.get("attendees", [])],
            location=str(value.get("location") or ""),
        )

    @property
    def searchable_text(self) -> str:
        return " ".join([self.title, self.description, self.location]).lower()


@dataclass(slots=True)
class HostingPlan:
    event: CalendarEvent
    guest_count: int
    occasion: str
    dietary_notes: list[str]
    menu: list[str]
    grocery_items: list[str]
    gemini_surface: str
    rationale: list[str]


@dataclass(slots=True)
class HostingCartRecommendation:
    plan: HostingPlan
    cart: Cart


def detect_hosting_events(events: list[CalendarEvent], today: date, horizon_days: int = 14) -> list[CalendarEvent]:
    upcoming: list[CalendarEvent] = []
    for event in events:
        days_until = (event.start - today).days
        text = event.searchable_text
        if days_until < 0 or days_until > horizon_days:
            continue
        if any(keyword in text for keyword in AWAY_FROM_HOME_KEYWORDS):
            continue
        if any(keyword in text for keyword in HOSTING_KEYWORDS):
            upcoming.append(event)
    return sorted(upcoming, key=lambda event: event.start)


def build_hosting_plan(event: CalendarEvent, profile: GroceryProfile) -> HostingPlan:
    text = event.searchable_text
    guest_count = _guest_count(event)
    dietary_notes = _dietary_notes(text, profile)
    occasion = _occasion(text)
    menu, grocery_items = _menu_for(text, dietary_notes, guest_count)
    rationale = [
        f"Calendar signal: '{event.title}' on {event.start.isoformat()} looks like an at-home hosting event.",
        f"Planned for roughly {guest_count} guest(s), using attendee count and event text.",
        "Generated a complete dinner basket across entree, sides, snacks, dessert, and drinks.",
        "Kept checkout behind the existing human approval gate.",
    ]
    return HostingPlan(
        event=event,
        guest_count=guest_count,
        occasion=occasion,
        dietary_notes=dietary_notes,
        menu=menu,
        grocery_items=grocery_items,
        gemini_surface="Gemini proactive hosting assist",
        rationale=rationale,
    )


def _parse_event_date(raw_start: str) -> date:
    normalized = raw_start.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized).date()
    except ValueError:
        return date.fromisoformat(raw_start[:10])


def _guest_count(event: CalendarEvent) -> int:
    text = event.searchable_text
    patterns = (
        r"(\d+)\s*(?:people|guests|friends|adults|family)",
        r"party\s*(?:of|for)?\s*(\d+)",
        r"for\s*(\d+)",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return max(2, int(match.group(1)))
    return max(2, len(event.attendees) + 1)


def _dietary_notes(text: str, profile: GroceryProfile) -> list[str]:
    notes = [restriction.lower() for restriction in profile.preferences.dietary_restrictions]
    for note in ("vegetarian", "vegan", "gluten-free", "kids", "no alcohol"):
        if note in text and note not in notes:
            notes.append(note)
    return notes


def _occasion(text: str) -> str:
    if "game night" in text:
        return "casual game night"
    if "potluck" in text:
        return "potluck dinner"
    if "family" in text:
        return "family dinner"
    return "dinner hosting"


def _menu_for(text: str, dietary_notes: list[str], guest_count: int) -> tuple[list[str], list[str]]:
    vegetarian = "vegetarian" in dietary_notes or "vegan" in dietary_notes or "paneer" in text or "indian" in text
    kids = "kids" in dietary_notes or "family" in text

    if vegetarian:
        menu = [
            "Paneer and vegetable curry",
            "Chana masala",
            "Basmati rice and naan",
            "Spinach tomato salad",
            "Fruit and dessert board",
            "Sparkling water",
        ]
        items = [
            "paneer",
            "chickpeas",
            "tomatoes",
            "spinach",
            "onions",
            "basmati rice",
            "naan",
            "greek yogurt",
            "blueberries",
            "dessert",
            "sparkling water",
        ]
    elif kids:
        menu = [
            "Chicken pesto pasta",
            "Broccoli and carrot tray",
            "Spinach tomato salad",
            "Fruit and snack board",
            "Cookies and sparkling water",
        ]
        items = [
            "chicken",
            "pasta",
            "pesto",
            "broccoli",
            "carrots",
            "spinach",
            "tomatoes",
            "bananas",
            "goldfish",
            "dessert",
            "sparkling water",
        ]
    else:
        menu = [
            "Roasted chicken dinner",
            "Broccoli and carrots",
            "Spinach tomato salad",
            "Bread and olive oil",
            "Berry dessert board",
            "Sparkling water",
        ]
        items = [
            "chicken",
            "broccoli",
            "carrots",
            "spinach",
            "tomatoes",
            "bread",
            "olive oil",
            "blueberries",
            "dessert",
            "sparkling water",
        ]

    if guest_count >= 8:
        items.extend(["paper towels", "sparkling water"])
    return menu, list(dict.fromkeys(items))
