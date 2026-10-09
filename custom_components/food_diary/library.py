"""Matching names to the dish library's recipes (the library itself comes from sources.py)."""

from __future__ import annotations

import re
from typing import Any

LEFTOVERS = re.compile(r"^\s*leftovers:\s*", re.IGNORECASE)


def plain_name(name: Any) -> str:
    """A dish name without a "Leftovers:" prefix, so leftovers match the dish they came from."""
    return LEFTOVERS.sub("", str(name or "")).strip()


def dish_names(dish: dict[str, Any]) -> set[str]:
    """The lowercase names a dish answers to: its name, its English name and its `aliases`."""
    names = (dish.get("name"), dish.get("name_en"), *(dish.get("aliases") or []))
    return {n for n in (plain_name(x).lower() for x in names if isinstance(x, str)) if n}


def find_dish(slot: dict[str, Any] | None, dishes: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The recipe a planned meal is: by the plan's `dish_id` when the library has it, else by name."""
    if not slot:
        return None
    if (did := slot.get("dish_id")) and (dish := next((x for x in dishes if x["id"] == did), None)):
        return dish
    low = plain_name(slot.get("name")).lower()
    return next((x for x in dishes if low and low in dish_names(x)), None)


def ingredient_lines(dish: dict[str, Any] | None) -> list[str]:
    """A dish's ingredients as "amount name" lines."""
    return [
        f"{i.get('amount') or ''} {i.get('name') or ''}".strip()
        for i in (dish or {}).get("ingredients") or []
        if isinstance(i, dict)
    ]
