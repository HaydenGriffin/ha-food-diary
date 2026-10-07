"""Reading the optional meal plan and dish library sensors (their attribute shapes are in docs/integration.md)."""

from __future__ import annotations

import re
from typing import Any

from homeassistant.core import HomeAssistant

LEFTOVERS = re.compile(r"^\s*leftovers:\s*", re.IGNORECASE)


def plain_name(name: Any) -> str:
    """A dish name without a "Leftovers:" prefix, so leftovers match the dish they came from."""
    return LEFTOVERS.sub("", str(name or "")).strip()


def read_dishes(hass: HomeAssistant, entity_id: str | None) -> list[dict[str, Any]]:
    """The dish library's recipes (each with an `id`); empty when no sensor is set or it has none."""
    state = hass.states.get(entity_id) if entity_id else None
    dishes = state.attributes.get("dishes") if state else None
    if not isinstance(dishes, list):
        return []
    return [{**x, "id": str(x["id"])} for x in dishes if isinstance(x, dict) and x.get("id")]


def read_week(hass: HomeAssistant, entity_id: str | None) -> list[dict[str, Any]]:
    """The meal plan's days; empty when no sensor is set or it has none."""
    state = hass.states.get(entity_id) if entity_id else None
    week = state.attributes.get("week") if state else None
    return [d for d in week if isinstance(d, dict)] if isinstance(week, list) else []


def dish_names(dish: dict[str, Any]) -> set[str]:
    """The lowercase names a dish answers to (its name and its English name)."""
    return {n for n in (plain_name(dish.get("name")).lower(), plain_name(dish.get("name_en")).lower()) if n}


def ingredient_lines(dish: dict[str, Any] | None) -> list[str]:
    """A dish's ingredients as "amount name" lines."""
    return [
        f"{i.get('amount') or ''} {i.get('name') or ''}".strip()
        for i in (dish or {}).get("ingredients") or []
        if isinstance(i, dict)
    ]
