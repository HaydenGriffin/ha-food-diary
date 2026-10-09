"""Where recipes and the meal plan come from: a small interface, sensors by default, and a hook for other integrations.

The diary reads two optional things, each through a provider:

    class RecipeLibrary(Protocol):
        def dishes(self) -> list[dict] | None: ...           # None: not available right now (never "no recipes")
        def async_subscribe(self, on_change) -> CALLBACK_TYPE: ...

    class MealPlan(Protocol):
        def days(self) -> list[dict] | None: ...             # None: not available right now (never "nothing planned")
        def async_subscribe(self, on_change) -> CALLBACK_TYPE: ...

Reads are synchronous and cheap (a provider keeps what it last fetched); `async_subscribe` calls `on_change()` (no
arguments, in the event loop) whenever either may have changed, and returns the function that stops it.

A dish is a dict with a unique, stable `id` (the shapes are in docs/integration.md, "Dish library sensor contract"). A plan
day is `{"date": "YYYY-MM-DD", "breakfast": slot, "lunch": slot, "dinner": slot}`; a slot is the meal's name, or
`{"name", "note"?, "dish_id"?, "nutrition"?, "ref"?}`, or None / "" for nothing planned. A meal left out of a provider's day
is "not known": the planner leaves that slot as it is (the sensor provider treats a missing meal as nothing planned, as its
contract always has).

By default the providers read the sensors from the options ("Meal plan sensor", "Dish library sensor"). Another integration
can register its own under a name:

    from custom_components.food_diary.sources import async_register_source

    unregister = async_register_source(hass, "my_planner", library=MyLibrary(), plan=MyPlan())

and each diary whose option "Recipes and meal plan from" is that name reads from them instead of the sensors (it reloads
when the source comes or goes, so the order things start in doesn't matter).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant, callback
from homeassistant.helpers.event import async_track_state_change_event

from .const import CONF_DISHES_SENSOR, CONF_PLAN_SENSOR, CONF_SOURCE, DOMAIN

PLANNED_MEALS = ("breakfast", "lunch", "dinner")
SLOT_TEXT = ("note", "dish_id", "ref")
REGISTRY = f"{DOMAIN}_sources"


class RecipeLibrary(Protocol):
    def dishes(self) -> list[dict[str, Any]] | None: ...

    def async_subscribe(self, on_change: Callable[[], None]) -> CALLBACK_TYPE: ...


class MealPlan(Protocol):
    def days(self) -> list[dict[str, Any]] | None: ...

    def async_subscribe(self, on_change: Callable[[], None]) -> CALLBACK_TYPE: ...


# ---------- the default: sensors ----------


class _Sensor:
    attribute = ""

    def __init__(self, hass: HomeAssistant, entity_id: str) -> None:
        self.hass, self.entity_id = hass, entity_id

    def _read(self) -> list[Any] | None:
        """The attribute's list; None when the sensor isn't there or is unavailable; [] when it has no such list."""
        state = self.hass.states.get(self.entity_id)
        if state is None or state.state == STATE_UNAVAILABLE:
            return None
        value = state.attributes.get(self.attribute)
        return value if isinstance(value, list) else []

    @callback
    def async_subscribe(self, on_change: Callable[[], None]) -> CALLBACK_TYPE:
        @callback
        def changed(_event: Event) -> None:
            on_change()

        return async_track_state_change_event(self.hass, [self.entity_id], changed)


class SensorLibrary(_Sensor):
    """A sensor whose `dishes` attribute lists the recipes."""

    attribute = "dishes"

    def dishes(self) -> list[dict[str, Any]] | None:
        return self._read()


class SensorPlan(_Sensor):
    """A sensor whose `week` attribute lists the planned days (a missing meal: nothing planned)."""

    attribute = "week"

    def days(self) -> list[dict[str, Any]] | None:
        week = self._read()
        if week is None:
            return None
        return [{**dict.fromkeys(PLANNED_MEALS), **d} for d in week if isinstance(d, dict)]


# ---------- reading, the same for every provider ----------


def read_dishes(library: RecipeLibrary | None) -> list[dict[str, Any]] | None:
    """The library's recipes, each with a string `id`; [] with no library, None when it isn't available."""
    if library is None:
        return []
    raw = library.dishes()
    if raw is None:
        return None
    return [{**x, "id": str(x["id"])} for x in raw if isinstance(x, dict) and x.get("id")]


def plan_slot(raw: Any) -> dict[str, Any] | None:
    """One planned meal as `{"name", "note"?, "dish_id"?, "nutrition"?, "ref"?}`, or None for nothing planned."""
    if isinstance(raw, str):
        return {"name": raw.strip()} if raw.strip() else None
    if not isinstance(raw, dict) or not str(raw.get("name") or "").strip():
        return None
    slot: dict[str, Any] = {"name": str(raw["name"]).strip()}
    for key in SLOT_TEXT:
        if raw.get(key):
            slot[key] = str(raw[key]).strip()
    if isinstance(raw.get("nutrition"), dict):
        slot["nutrition"] = raw["nutrition"]
    return slot


def read_plan(plan: MealPlan | None) -> list[dict[str, Any]] | None:
    """The plan's days with their known meals as slots (see plan_slot); [] with no plan, None when it isn't available."""
    if plan is None:
        return []
    raw = plan.days()
    if raw is None:
        return None
    out = []
    for day in raw:
        if not isinstance(day, dict) or not isinstance(day.get("date"), str) or not day["date"]:
            continue
        out.append({"date": day["date"], **{m: plan_slot(day[m]) for m in PLANNED_MEALS if m in day}})
    return out


def find_slot(plan: MealPlan | None, d: str, meal: str) -> dict[str, Any] | None:
    """What the plan says now for one meal on one day (None: nothing, or not known)."""
    return next((day.get(meal) for day in read_plan(plan) or [] if day["date"] == d), None)


# ---------- other integrations' sources ----------


@dataclass(frozen=True, eq=False)
class Source:
    library: RecipeLibrary | None = None
    plan: MealPlan | None = None


@callback
def async_register_source(
    hass: HomeAssistant, name: str, *, library: RecipeLibrary | None = None, plan: MealPlan | None = None
) -> CALLBACK_TYPE:
    """Offer a recipe library and/or a meal plan under `name`; returns the function that withdraws them. Diaries set to
    read from `name` reload to pick them up (and again when they're withdrawn)."""
    if not name or (library is None and plan is None):
        raise ValueError("A source needs a name and a library or a plan")
    sources: dict[str, Source] = hass.data.setdefault(REGISTRY, {})
    if name in sources:
        raise ValueError(f"A food diary source called {name!r} is already registered")
    source = sources[name] = Source(library, plan)
    _reload_readers(hass, name)

    @callback
    def unregister() -> None:
        if sources.get(name) is source:
            del sources[name]
            _reload_readers(hass, name)

    return unregister


def registered(hass: HomeAssistant) -> list[str]:
    return sorted(hass.data.get(REGISTRY, {}))


@callback
def async_resolve(hass: HomeAssistant, options: dict[str, Any]) -> Source:
    """A diary's providers from its options: the named source (nothing until it's registered), else the sensors."""
    if name := options.get(CONF_SOURCE):
        return hass.data.get(REGISTRY, {}).get(name) or Source()
    dishes, plan = options.get(CONF_DISHES_SENSOR), options.get(CONF_PLAN_SENSOR)
    return Source(SensorLibrary(hass, dishes) if dishes else None, SensorPlan(hass, plan) if plan else None)


@callback
def _reload_readers(hass: HomeAssistant, name: str) -> None:
    current = hass.data.get(REGISTRY, {}).get(name) or Source()
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.options.get(CONF_SOURCE) != name or entry.state is not ConfigEntryState.LOADED:
            continue
        data = entry.runtime_data
        if (data.recipes, data.plan) != (current.library, current.plan):
            hass.config_entries.async_schedule_reload(entry.entry_id)
