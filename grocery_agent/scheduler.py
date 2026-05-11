from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from grocery_agent.agent import GroceryAgent
from grocery_agent.models import Cart


@dataclass(slots=True)
class BiweeklySundayScheduler:
    anchor_sunday: date

    def should_run(self, today: date) -> bool:
        if today.weekday() != 6:
            return False
        return ((today - self.anchor_sunday).days // 7) % 2 == 0

    def prepare_if_due(self, agent: GroceryAgent, today: date) -> Cart | None:
        if not self.should_run(today):
            return None
        return agent.generate_cart([], today=today, proactive=True)

