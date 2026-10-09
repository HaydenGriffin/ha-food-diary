"""Recipes and the meal plan through the provider interface (sources.py): another integration's source, the plan's own
links and numbers, meals that aren't known, and providers that aren't available."""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.food_diary.const import CONF_SOURCE, DOMAIN
from custom_components.food_diary.planner import PLAN_NOTE, Planner
from custom_components.food_diary.sources import (
    SensorLibrary,
    SensorPlan,
    async_register_source,
    read_plan,
    registered,
)

from .conftest import LIBRARY, PLAN
from .test_food_diary import call

CHILLI = {"id": "ig-1", "name_en": "Chilli con carne", "ingredients": [{"name": "beef", "amount": "500 g"}], "servings": 4}
KATSU = {"id": "r1", "name": "Chicken katsu curry", "nutrition": {"kcal": 930, "protein_g": 58, "carbs_g": 127, "fat_g": 19}}
GIVEN = {"kcal": 593, "protein_g": 12.8, "carbs_g": 65.9, "fat_g": 33.4, "fibre_g": 14.5}


def day(n: int) -> str:
    return (dt_util.now().date() + timedelta(days=n)).isoformat()


class Fake:
    """An in-memory library and plan, the way another integration would offer them."""

    def __init__(self, dishes: list[dict[str, Any]] | None = None, days: list[dict[str, Any]] | None = None) -> None:
        self._dishes, self._days = dishes, days
        self.listeners: list[Callable[[], None]] = []

    def dishes(self) -> list[dict[str, Any]] | None:
        return self._dishes

    def days(self) -> list[dict[str, Any]] | None:
        return self._days

    @callback
    def async_subscribe(self, on_change: Callable[[], None]) -> Callable[[], None]:
        self.listeners.append(on_change)
        return lambda: self.listeners.remove(on_change)

    def change(self, *, dishes: Any = ..., days: Any = ...) -> None:
        if dishes is not ...:
            self._dishes = dishes
        if days is not ...:
            self._days = days
        for listener in list(self.listeners):
            listener()


async def dinners(hass: HomeAssistant, d: str) -> list[dict[str, Any]]:
    return [e for e in (await call(hass, "get_day", {"date": d}))["entries"] if e["meal"] == "dinner"]


async def use_source(hass: HomeAssistant, setup: MockConfigEntry, name: str = "kitchen") -> None:
    hass.config_entries.async_update_entry(setup, options={**setup.options, CONF_SOURCE: name})
    await hass.async_block_till_done()


# ---------- another integration's source ----------


async def test_a_registered_source_drives_the_diary(hass: HomeAssistant, setup, ai_calls):
    fake = Fake([CHILLI], [{"date": day(1), "dinner": {"name": "Chilli con carne", "dish_id": "ig-1"}}])
    stop = async_register_source(hass, "kitchen", library=fake, plan=fake)
    await use_source(hass, setup)
    data = setup.runtime_data
    assert data.planner is not None and data.recipes is fake and data.plan is fake
    assert (await call(hass, "sync_plan"))["changed"] == 1
    (e,) = await dinners(hass, day(1))
    assert e["source"] == "plan" and e["ref"] == "ig-1" and e["note"] == PLAN_NOTE
    assert [d["id"] for d in (await call(hass, "get_dishes"))["dishes"]] == ["ig-1"]
    stop()
    await hass.async_block_till_done()
    assert setup.runtime_data.planner is None and registered(hass) == []  # withdrawn: the diary reloaded without it


async def test_the_diary_can_start_before_its_source(hass: HomeAssistant, setup):
    await use_source(hass, setup)
    assert setup.runtime_data.planner is None and (await call(hass, "get_dishes")) == {"dishes": []}
    fake = Fake([CHILLI], [])
    stop = async_register_source(hass, "kitchen", library=fake, plan=fake)
    await hass.async_block_till_done()
    assert setup.runtime_data.planner is not None and setup.runtime_data.recipes is fake
    stop()
    await hass.async_block_till_done()


async def test_the_sensors_are_not_read_while_a_source_is_chosen(hass: HomeAssistant, setup, ai_calls):
    hass.states.async_set(PLAN, "x", {"week": [{"date": day(1), "dinner": "Soup"}]})
    hass.config_entries.async_update_entry(setup, options={**setup.options, "meal_plan_sensor": PLAN, CONF_SOURCE: "kitchen"})
    await hass.async_block_till_done()
    assert setup.runtime_data.planner is None and await dinners(hass, day(1)) == []


async def test_a_name_is_registered_once(hass: HomeAssistant):
    fake = Fake([], [])
    stop = async_register_source(hass, "kitchen", plan=fake)
    with pytest.raises(ValueError):
        async_register_source(hass, "kitchen", library=fake)
    with pytest.raises(ValueError):
        async_register_source(hass, "empty")
    stop()
    stop()  # a second call does nothing
    assert registered(hass) == []


async def test_a_change_from_the_source_syncs_by_itself(hass: HomeAssistant, setup, ai_calls):
    fake = Fake([CHILLI], [])
    stop = async_register_source(hass, "kitchen", library=fake, plan=fake)
    await use_source(hass, setup)
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=30))  # the first run after start
    await hass.async_block_till_done()
    fake.change(days=[{"date": day(1), "dinner": "Chilli con carne"}])
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=45))
    await hass.async_block_till_done()
    assert [e["name"] for e in await dinners(hass, day(1))] == ["Chilli con carne"]
    stop()
    await hass.async_block_till_done()


async def test_the_options_offer_registered_sources(hass: HomeAssistant, setup):
    r = await hass.config_entries.options.async_init(setup.entry_id)
    assert CONF_SOURCE not in {str(k) for k in r["data_schema"].schema}
    hass.config_entries.options.async_abort(r["flow_id"])
    stop = async_register_source(hass, "kitchen", plan=Fake([], []))
    r = await hass.config_entries.options.async_init(setup.entry_id)
    assert CONF_SOURCE in {str(k) for k in r["data_schema"].schema}
    r = await hass.config_entries.options.async_configure(r["flow_id"], {CONF_SOURCE: "kitchen"})
    assert r["type"] is FlowResultType.CREATE_ENTRY and setup.options[CONF_SOURCE] == "kitchen"
    await hass.async_block_till_done()
    assert setup.runtime_data.planner is not None
    stop()
    await hass.async_block_till_done()


# ---------- what the plan says ----------


async def test_the_plans_dish_id_beats_the_name(hass: HomeAssistant, setup, ai_calls):
    """A recipe renamed after it was planned is still the same recipe (the id), not an estimate from the old name."""
    fake = Fake([CHILLI], [{"date": day(1), "dinner": {"name": "Chili (old name)", "dish_id": "ig-1"}}])
    await Planner(hass, setup.runtime_data, fake, fake).async_sync()
    (e,) = await dinners(hass, day(1))
    assert e["ref"] == "ig-1" and [c["task"] for c in ai_calls] == ["estimate a dish"]


async def test_aliases_match_the_plan(hass: HomeAssistant, setup, ai_calls):
    fake = Fake([{**CHILLI, "aliases": ["Chilli (Texas style)"]}], [{"date": day(1), "dinner": "chilli (texas style)"}])
    await Planner(hass, setup.runtime_data, fake, fake).async_sync()
    assert (await dinners(hass, day(1)))[0]["ref"] == "ig-1"


async def test_the_plans_own_numbers_come_first(hass: HomeAssistant, setup, ai_calls):
    """A slot with `nutrition` (say a cooking service's own numbers for the planned recipe) counts those, and one recipe's
    numbers are kept in the recipe book; several recipes' combined numbers aren't."""
    await call(hass, "set_dish_nutrition", {"dish_id": "r9", "kcal": 400, "source": "own"}, response=False)
    slot = {"name": "Roasted sweet potatoes", "dish_id": "r2", "nutrition": GIVEN}
    both = {"name": "Soup + Bread", "ref": "r3+r4", "nutrition": {"kcal": 700, "protein_g": 20}}
    mine = {"name": "Stew", "dish_id": "r9", "nutrition": {"kcal": 800}}
    fake = Fake([], [{"date": day(1), "dinner": slot, "lunch": both, "breakfast": mine}])
    p = Planner(hass, setup.runtime_data, fake, fake)
    assert await p.async_sync() == 3 and ai_calls == []
    got = {e["meal"]: e for e in (await call(hass, "get_day", {"date": day(1)}))["entries"]}
    assert got["dinner"]["kcal"] == 593 and got["dinner"]["ref"] == "r2"
    assert got["lunch"]["kcal"] == 700 and got["lunch"]["ref"] == "r3+r4"
    assert got["breakfast"]["kcal"] == 800  # the plan's numbers for this cook…
    book = hass.data[DOMAIN]["dishes"]
    assert book.get("r2")["kcal"] == 593 and book.get("r2")["source"] == "recipe"
    assert book.get("r3+r4") is None and book.get("r9")["source"] == "own"  # …but the person's own stay in the book
    fake.change(
        days=[{"date": day(1), "dinner": {**slot, "nutrition": {**GIVEN, "kcal": 610}}, "lunch": both, "breakfast": mine}]
    )
    assert await p.async_sync() == 1
    assert (await dinners(hass, day(1)))[0]["kcal"] == 610  # new numbers from the plan: the entry follows


async def test_a_meal_the_plan_doesnt_know_is_left_alone(hass: HomeAssistant, setup, ai_calls):
    fake = Fake([CHILLI], [{"date": day(1), "dinner": "Chilli con carne", "lunch": "Soup"}])
    p = Planner(hass, setup.runtime_data, fake, fake)
    await p.async_sync()
    fake.change(days=[{"date": day(1), "lunch": None}])  # dinner isn't known now (its list is down, say)
    assert await p.async_sync() == 1
    names = {e["meal"]: e["name"] for e in (await call(hass, "get_day", {"date": day(1)}))["entries"]}
    assert names == {"dinner": "Chilli con carne"}  # lunch emptied, dinner kept


async def test_nothing_happens_while_a_provider_is_unavailable(hass: HomeAssistant, setup, ai_calls):
    fake = Fake([CHILLI], [{"date": day(1), "dinner": "Chilli con carne"}])
    p = Planner(hass, setup.runtime_data, fake, fake)
    await p.async_sync()
    fake.change(days=None)  # unavailable is not "nothing planned"
    assert await p.async_sync() == 0 and len(await dinners(hass, day(1))) == 1
    fake.change(days=[{"date": day(2), "dinner": "Chilli con carne"}], dishes=None)
    asked = len(ai_calls)
    assert await p.async_sync() == 0 and await dinners(hass, day(2)) == []
    assert len(ai_calls) == asked  # no guess from the name while the library is away


# ---------- the sensor providers ----------


async def test_sensor_providers(hass: HomeAssistant):
    plan, library = SensorPlan(hass, PLAN), SensorLibrary(hass, LIBRARY)
    assert plan.days() is None and library.dishes() is None  # not there yet
    hass.states.async_set(PLAN, "unavailable", {})
    assert plan.days() is None
    hass.states.async_set(PLAN, "x", {"week": [{"date": "2026-10-09", "dinner": {"name": "Stew", "dish_id": "d1", "note": "x"}}]})
    assert read_plan(plan) == [
        {"date": "2026-10-09", "breakfast": None, "lunch": None, "dinner": {"name": "Stew", "dish_id": "d1", "note": "x"}}
    ]  # a meal missing from a sensor day is nothing planned (its contract)
    hass.states.async_set(LIBRARY, "x", {"other": 1})
    assert library.dishes() == []
    seen = []
    stop = library.async_subscribe(lambda: seen.append(1))
    hass.states.async_set(LIBRARY, "y", {"dishes": []})
    await hass.async_block_till_done()
    stop()
    assert seen == [1]
