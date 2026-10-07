"""The meal plan → the diary: planned meals count ahead of time, follow the plan and the recipe book, and never double up."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.food_diary.const import DOMAIN
from custom_components.food_diary.planner import PLAN_NOTE, Planner

from .conftest import LIBRARY, PLAN
from .test_food_diary import call

CHILLI = {"id": "ig-1", "name_en": "Chilli con carne", "ingredients": [{"name": "beef", "amount": "500 g"}], "servings": 4}
KATSU = {
    "id": "r1",
    "name": "Chicken katsu curry",
    "nutrition": {"kcal": 930.2, "protein_g": 58.8, "carbs_g": 127.3, "fat_g": 18.9, "fibre_g": 6.4},
}


def day(n: int) -> str:
    return (dt_util.now().date() + timedelta(days=n)).isoformat()


def plan(hass: HomeAssistant, week: list[dict[str, Any]], dishes: list[dict[str, Any]] | None = None) -> None:
    hass.states.async_set(PLAN, "x", {"week": week})
    if dishes is not None:
        hass.states.async_set(LIBRARY, "x", {"dishes": dishes})


def planner(
    hass: HomeAssistant, setup: MockConfigEntry, week: list[dict[str, Any]], dishes: list[dict[str, Any]] | None = None
) -> Planner:
    plan(hass, week, dishes or [])
    return Planner(hass, setup.runtime_data, PLAN, LIBRARY)


async def entries(hass: HomeAssistant, d: str) -> dict[str, dict[str, Any]]:
    return {e["name"]: e for e in (await call(hass, "get_day", {"date": d}))["entries"]}


async def dinner(hass: HomeAssistant, d: str) -> dict[str, Any]:
    return next(e for e in (await call(hass, "get_day", {"date": d}))["entries"] if e["meal"] == "dinner")


# ---------- counting ahead ----------


async def test_plan_counts_ahead(hass: HomeAssistant, setup, ai_calls):
    p = planner(
        hass,
        setup,
        [{"date": day(-1), "dinner": "Old dinner"}, {"date": day(1), "dinner": "Chilli con carne", "lunch": "Soup"}],
        [CHILLI],
    )
    assert await p.async_sync() == 2
    names = await entries(hass, day(1))
    chilli = names["Chilli con carne"]
    assert (chilli["kcal"], chilli["meal"], chilli["source"], chilli["ref"]) == (425, "dinner", "plan", "ig-1")
    assert chilli["note"] == PLAN_NOTE and chilli["plan_key"] == f"{day(1)}|dinner"
    dish_ask = next(c for c in ai_calls if c["task"] == "estimate a dish")
    assert "makes 4 portions" in dish_ask["instructions"]  # from the library dish's ingredients
    assert names["Soup"]["kcal"] == 315 and names["Soup"]["meal"] == "lunch"  # no library dish: from its name
    assert await entries(hass, day(-1)) == {}  # the past is never touched
    n = len(ai_calls)
    assert await p.async_sync() == 0 and len(ai_calls) == n  # nothing new, nothing asked again


async def test_leftovers_match_their_dish(hass: HomeAssistant, setup, ai_calls):
    p = planner(hass, setup, [{"date": day(1), "dinner": "Chilli con carne", "lunch": "Leftovers: Chilli con carne"}], [CHILLI])
    await p.async_sync()
    names = await entries(hass, day(1))
    assert names["Leftovers: Chilli con carne"]["ref"] == "ig-1" and names["Leftovers: Chilli con carne"]["kcal"] == 425
    assert [c["task"] for c in ai_calls] == ["estimate a dish"]  # worked out once, for both


async def test_plan_changes_and_deletions_stick(hass: HomeAssistant, setup, ai_calls):
    p = planner(hass, setup, [{"date": day(1), "dinner": "Chilli con carne"}])
    await p.async_sync()
    plan(hass, [{"date": day(1), "dinner": "Fish pie"}])  # the plan swaps dinner: the entry follows
    await p.async_sync()
    assert list(await entries(hass, day(1))) == ["Fish pie"]
    e = (await entries(hass, day(1)))["Fish pie"]  # deleted from the diary: it stays deleted
    await call(hass, "delete_food", {"entry_id": e["id"], "date": day(1)}, response=False)
    await p.async_sync()
    assert await entries(hass, day(1)) == {}
    plan(hass, [{"date": day(1), "dinner": ""}])  # the slot empties: nothing comes back either
    await p.async_sync()
    assert await entries(hass, day(1)) == {}


async def test_plan_never_doubles_what_was_logged(hass: HomeAssistant, setup, ai_calls):
    await call(hass, "log_food", {"name": "Beef stew", "kcal": 600, "meal": "dinner"}, response=False)
    p = planner(hass, setup, [{"date": day(0), "dinner": "Roasted sweet potatoes", "breakfast": "Porridge"}])
    await p.async_sync()
    assert sorted(await entries(hass, day(0))) == ["Beef stew", "Porridge"]  # dinner is logged; breakfast comes from the plan
    porridge = (await entries(hass, day(0)))["Porridge"]
    await call(hass, "update_food", {"entry_id": porridge["id"], "kcal": 350}, response=False)
    plan(hass, [{"date": day(0), "dinner": "Roasted sweet potatoes"}])  # edited numbers survive the slot emptying
    await p.async_sync()
    assert (await entries(hass, day(0)))["Porridge"]["kcal"] == 350


async def test_moving_a_meal_on_the_plan_moves_the_diary(hass: HomeAssistant, setup, ai_calls):
    p = planner(hass, setup, [{"date": day(2), "dinner": "Chilli con carne"}])
    await p.async_sync()
    plan(hass, [{"date": day(2), "dinner": ""}, {"date": day(3), "dinner": "Chilli con carne"}])
    await p.async_sync()
    assert await entries(hass, day(2)) == {}
    assert (await dinner(hass, day(3)))["source"] == "plan"


# ---------- the dish library's own numbers ----------


async def test_library_numbers_are_used_without_the_ai(hass: HomeAssistant, setup, ai_calls):
    p = planner(hass, setup, [{"date": day(1), "dinner": "Chicken katsu curry"}], [KATSU])
    assert await p.async_sync() == 1
    e = await dinner(hass, day(1))
    assert (e["kcal"], e["protein_g"], e["fibre_g"], e["ref"]) == (930.2, 58.8, 6.4, "r1") and ai_calls == []
    kept = await call(hass, "get_dish_nutrition", {"dish_id": "r1"})
    assert kept["kcal"] == 930.2 and kept["source"] == "recipe"  # the recipe page says the same


async def test_library_numbers_arriving_later_update_planned_entries(hass: HomeAssistant, setup, ai_calls):
    week = [{"date": day(1), "dinner": "Chicken katsu curry"}, {"date": day(2), "dinner": "Chicken katsu curry"}]
    p = planner(hass, setup, week, [{**KATSU, "nutrition": None}])
    await p.async_sync()
    first, second = await dinner(hass, day(1)), await dinner(hass, day(2))
    assert first["kcal"] == second["kcal"] == 300  # estimated (by name) before the dish's own numbers arrived
    await call(hass, "update_food", {"entry_id": first["id"], "date": day(1), "portions": 0.5}, response=False)
    await call(hass, "update_food", {"entry_id": second["id"], "date": day(2), "kcal": 500}, response=False)
    plan(hass, week, [KATSU])
    assert await p.async_sync() == 1
    first, second = await dinner(hass, day(1)), await dinner(hass, day(2))
    assert first["portions"] == 0.5 and first["kcal"] == 465.1 and first["per_portion"]["kcal"] == 930.2  # portions stay
    assert second["kcal"] == 500 and second["edited"]  # edited numbers stay
    assert await p.async_sync() == 0


async def test_own_numbers_beat_the_librarys(hass: HomeAssistant, setup, ai_calls):
    await call(hass, "set_dish_nutrition", {"dish_id": "r1", "kcal": 700, "protein_g": 40, "source": "own"}, response=False)
    p = planner(hass, setup, [{"date": day(1), "dinner": "Chicken katsu curry"}], [KATSU])
    await p.async_sync()
    assert (await dinner(hass, day(1)))["kcal"] == 700
    assert (await call(hass, "get_dish_nutrition", {"dish_id": "r1"}))["source"] == "own"


# ---------- nothing configured ----------


async def test_without_plan_or_library_everything_is_off(hass: HomeAssistant, setup):
    assert setup.runtime_data.planner is None
    assert await call(hass, "sync_plan") == {"changed": 0, "completed": []}
    assert await call(hass, "get_dishes") == {"dishes": []}
    assert hass.data[DOMAIN]["dishes"].items == {}


async def test_a_broken_plan_sensor_is_ignored(hass: HomeAssistant, setup, ai_calls):
    hass.states.async_set(PLAN, "x", {"week": "not a list"})
    hass.states.async_set(LIBRARY, "x", {"dishes": [{"name": "no id"}, "junk"]})
    assert await Planner(hass, setup.runtime_data, PLAN, LIBRARY).async_sync() == 0
    assert await Planner(hass, setup.runtime_data, "sensor.missing", None).async_sync() == 0
