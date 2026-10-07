"""Usual foods, saved meals, the week's look back, the recipes with calories, and syncing the meal plan on demand."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.util import dt as dt_util
import pytest

from custom_components.food_diary.const import CONF_DISHES_SENSOR, CONF_PLAN_SENSOR

from .test_food_diary import call


def day(n: int) -> str:
    return (dt_util.now().date() + timedelta(days=n)).isoformat()


async def log(hass, name, kcal, d, meal="breakfast", **extra):
    await call(hass, "log_food", {"name": name, "kcal": kcal, "date": d, "meal": meal, **extra}, response=False)


@pytest.fixture
async def planned(hass: HomeAssistant, setup):
    """Alex's diary linked to a meal plan and a dish library through the options."""
    hass.states.async_set("sensor.meal_plan", "x", {"week": []})
    hass.states.async_set(
        "sensor.dish_library",
        "x",
        {
            "dishes": [
                {
                    "id": "ls-porridge",
                    "name": "Owsianka",
                    "name_en": "Porridge",
                    "lang": "pl",
                    "source": "web",
                    "status": "ours",
                    "favourite": True,
                    "times": 3,
                    "meal_types": ["breakfast"],
                    "image": "/local/recipes/ls-porridge.jpg",
                },
                {
                    "id": "ig-1",
                    "name_en": "Chilli con carne",
                    "source": "web",
                    "status": "to_try",
                    "meal_types": ["dinner"],
                    "ingredients": [{"name": "beef", "amount": "500 g"}],
                    "servings": 4,
                },
            ]
        },
    )
    hass.config_entries.async_update_entry(
        setup, options={**setup.options, CONF_PLAN_SENSOR: "sensor.meal_plan", CONF_DISHES_SENSOR: "sensor.dish_library"}
    )
    await hass.async_block_till_done()
    return setup


async def test_usuals_are_what_is_had_most_days(hass: HomeAssistant, setup):
    for n in (-1, -2, -4):
        await log(hass, "Hazelnut milk cereal", 154, day(n))
    for n in (-1, -3):
        await log(hass, "Toast", 200, day(n))
    await log(hass, "Hazelnut milk cereal", 154, day(-30))  # too long ago to count
    diary = setup.runtime_data.diary
    for n in (-1, -2, -3):  # the plan's entries aren't habits
        diary.add(day(n), {"name": "Planned porridge", "kcal": 300, "meal": "breakfast", "source": "plan"})
    r = await call(hass, "get_recent")
    usual = r["usuals"]["breakfast"]
    assert [u["name"] for u in usual] == ["Hazelnut milk cereal"]
    assert usual[0]["times"] == 3 and usual[0]["kcal"] == 154 and usual[0]["meal"] == "breakfast"
    assert r["usuals"]["lunch"] == [] and r["saved"] == []


async def test_saved_meals(hass: HomeAssistant, setup):
    await log(hass, "Porridge", 300, day(0), protein_g=10)
    await log(hass, "Banana", 105, day(0), protein_g=1.3)
    await log(hass, "Soup", 250, day(0), meal="lunch")
    saved = (await call(hass, "save_meal", {"name": "My breakfast", "meal": "breakfast"}))["saved"]
    assert (
        saved["items"] == ["Porridge", "Banana"]
        and saved["kcal"] == 405
        and saved["protein_g"] == 11.3
        and saved["meal"] == "breakfast"
    )
    again = (await call(hass, "save_meal", {"name": "my breakfast", "meal": "lunch"}))["saved"]  # the same name replaces it
    r = await call(hass, "get_recent")
    assert [s["name"] for s in r["saved"]] == ["my breakfast"] and r["saved"][0]["kcal"] == 250
    await call(
        hass,
        "log_food",
        {"name": "my breakfast", "kcal": 250, "source": "saved", "ref": again["id"], "meal": "breakfast"},
        response=False,
    )
    await call(hass, "delete_saved_meal", {"saved_id": again["id"]}, response=False)
    assert (await call(hass, "get_recent"))["saved"] == []
    with pytest.raises(ServiceValidationError):
        await call(hass, "save_meal", {"name": "Nothing", "meal": "snack"})
    with pytest.raises(ServiceValidationError):
        await call(hass, "delete_saved_meal", {"saved_id": "gone"})


async def test_week_review(hass: HomeAssistant, setup):
    await call(hass, "set_goals", {"kcal": 1400, "protein_g": 95}, response=False)
    await log(hass, "Porridge", 300, day(-1), ref="ls-porridge", protein_g=12)
    await log(hass, "Chicken curry", 1000, day(-1), meal="dinner", protein_g=80)  # 1300: on target, protein day
    await log(hass, "Porridge", 300, day(-2), ref="ls-porridge")
    await log(hass, "Lasagne", 1300, day(-2), meal="dinner", source="dish", ref="ig-9")  # 1600: over; a new recipe
    await log(hass, "Toast", 700, day(-3))  # too little to be on target
    await log(hass, "Porridge", 300, day(-12), ref="ls-porridge")  # tried before: not new
    await log(hass, "Stew", 1000, day(-9), meal="dinner")  # the week before
    r = await call(hass, "get_week_review", {"date": day(-1)})
    assert r["start"] == day(-7) and r["end"] == day(-1) and len(r["days"]) == 7
    assert r["days_logged"] == 3 and r["on_target"] == 1 and r["over"] == 1 and r["protein_days"] == 1
    assert r["avg_kcal"] == round((1300 + 1600 + 700) / 3) and r["last_week_avg_kcal"] == 650  # stew 1000 and porridge 300
    assert r["favourite"] == {"name": "Porridge", "times": 2}
    assert r["new_dishes"] == ["Lasagne"] and r["best_day"] == {"date": day(-1), "kcal": 1300}
    assert (await call(hass, "get_week_review"))["end"] == day(-1)  # yesterday by default


async def test_dishes_with_calories(hass: HomeAssistant, planned):
    await hass.services.async_call(
        "food_diary",
        "set_dish_nutrition",
        {"dish_id": "ls-porridge", "kcal": 348, "protein_g": 12, "source": "own"},
        blocking=True,
        return_response=True,
    )
    dishes = {d["id"]: d for d in (await call(hass, "get_dishes"))["dishes"]}
    porridge = dishes["ls-porridge"]
    assert porridge["name"] == "Owsianka" and porridge["title"] == "Owsianka (Porridge)"  # named in another language
    assert porridge["kcal"] == 348 and porridge["kcal_source"] == "own"
    assert dishes["ig-1"]["name"] == dishes["ig-1"]["title"] == "Chilli con carne"
    assert dishes["ls-porridge"]["favourite"] and dishes["ls-porridge"]["meal_types"] == ["breakfast"]
    assert "kcal" not in dishes["ig-1"] and dishes["ig-1"]["status"] == "to_try"


async def test_sync_plan_now(hass: HomeAssistant, planned, ai_calls):
    hass.states.async_set(
        "sensor.meal_plan", "y", {"week": [{"date": day(1), "dinner": "Chilli con carne", "breakfast": "Porridge"}]}
    )
    assert (await call(hass, "sync_plan"))["changed"] == 2
    names = {e["name"]: e for e in (await call(hass, "get_day", {"date": day(1)}))["entries"]}
    assert names["Chilli con carne"]["source"] == "plan" and names["Chilli con carne"]["ref"] == "ig-1"
    assert (await call(hass, "sync_plan"))["changed"] == 0


async def test_dish_library_by_itself(hass: HomeAssistant, setup):
    """A dish library without a meal plan: recipes and checks work, syncing the plan does nothing."""
    hass.states.async_set("sensor.dish_library", "x", {"dishes": [{"id": "d1", "name": "Soup"}]})
    hass.config_entries.async_update_entry(setup, options={**setup.options, CONF_DISHES_SENSOR: "sensor.dish_library"})
    await hass.async_block_till_done()
    assert [d["id"] for d in (await call(hass, "get_dishes"))["dishes"]] == ["d1"]
    assert await call(hass, "sync_plan") == {"changed": 0, "completed": []}


async def test_update_saved_meal_and_recent_limit(hass: HomeAssistant, setup):
    await log(hass, "Porridge", 300, day(0))
    await log(hass, "Banana", 105, day(0))
    await log(hass, "Soup", 250, day(0), meal="lunch")
    first = (await call(hass, "save_meal", {"name": "My breakfast", "meal": "breakfast"}))["saved"]
    other = (await call(hass, "save_meal", {"name": "Lunch", "meal": "lunch"}))["saved"]
    s = (await call(hass, "update_saved_meal", {"saved_id": first["id"], "name": "Weekday breakfast", "meal": "snack"}))["saved"]
    assert s["name"] == "Weekday breakfast" and s["meal"] == "snack" and s["items"] == ["Porridge", "Banana"]
    s = (await call(hass, "update_saved_meal", {"saved_id": first["id"], "name": "lunch"}))[
        "saved"
    ]  # takes the other's name: it replaces it
    saved = (await call(hass, "get_recent"))["saved"]
    assert (
        [x["id"] for x in saved] == [first["id"]] and saved[0]["name"] == "lunch" and other["id"] not in [x["id"] for x in saved]
    )
    with pytest.raises(ServiceValidationError):
        await call(hass, "update_saved_meal", {"saved_id": "gone", "name": "x"})
    assert len((await call(hass, "get_recent", {"limit": 2}))["foods"]) == 2
    assert len((await call(hass, "get_recent"))["foods"]) == 3
