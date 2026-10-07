"""Partial recipe numbers (kcal and protein printed, no carbs or fat) are completed once from the dish's estimate."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.core import Context, HomeAssistant
from homeassistant.util import dt as dt_util
import pytest

from custom_components.food_diary.const import DOMAIN
from custom_components.food_diary.macros import complete, macro_kcal, partial
from custom_components.food_diary.planner import PLAN_NOTE, Planner

from .conftest import AI_ANSWERS, ALEX

PASTA = {
    "id": "ig-pasta",
    "name": "hot honey chicken & halloumi pasta",
    "name_en": "Hot honey chicken and halloumi pasta",
    "amounts_per": "portion",
    "servings": 1,
    "ingredients": [{"name": "Chicken breast", "amount": "120 g"}, {"name": "Rigatoni", "amount": "120 g"}],
}
PRINTED = {"kcal": 470, "protein_g": 55, "source": "recipe"}  # what an imported recipe printed
# the fake AI's "estimate a dish" (conftest): 425 kcal, protein 24, carbs 45, fat 16, fibre 9. The 250 kcal left after
# protein go to carbs and fat in its proportions: 4*45 + 9*16 = 324 kcal of them.
DONE = {
    "kcal": 470.0,
    "protein_g": 55.0,
    "carbs_g": round(45 * 250 / 324, 1),
    "fat_g": round(16 * 250 / 324, 1),
    "fibre_g": round(9 * 470 / 425, 1),
}


def day(n: int) -> str:
    return (dt_util.now().date() + timedelta(days=n)).isoformat()


async def call(hass, service, data=None, response=True):
    return await hass.services.async_call(
        DOMAIN, service, data or {}, blocking=True, return_response=response, context=Context(user_id=ALEX)
    )


def planner(hass, setup, week, dishes=()):
    hass.states.async_set("sensor.meal_plan", "x", {"week": week})
    hass.states.async_set("sensor.dish_library", "x", {"dishes": list(dishes)})
    return Planner(hass, setup.runtime_data, "sensor.meal_plan", "sensor.dish_library")


def book(hass):
    return hass.data[DOMAIN]["dishes"]


async def dinner(hass, d):
    return next(e for e in (await call(hass, "get_day", {"date": d}))["entries"] if e["meal"] == "dinner")


# ---------- which numbers are partial, and completing them ----------


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        ({"kcal": 470, "protein_g": 55}, True),  # kcal and protein only
        ({"kcal": 600, "protein_g": 30, "carbs_g": 50}, True),  # no fat, 280 kcal unexplained
        ({"kcal": 146, "protein_g": 5, "carbs_g": 30}, False),  # a sorbet: no fat, and none needed
        ({"kcal": 562, "protein_g": 26.5, "carbs_g": 47, "fat_g": 33.2}, False),  # whole
        ({"kcal": 500, "protein_g": 10, "carbs_g": 10, "fat_g": 2}, False),  # low but nothing missing: left alone
        ({"kcal": 470, "protein_g": 55, "completed": "none"}, False),  # tried once already
        ({"kcal": 0, "protein_g": 0}, False),
        (None, False),
    ],
)
def test_partial(values, expected):
    assert partial(values) is expected


def test_complete_keeps_kcal_and_protein_and_fills_the_rest():
    est = {"kcal": 655, "protein_g": 48.5, "carbs_g": 92, "fat_g": 11.5, "fibre_g": 6.5}  # the live AI's answer for the pasta
    out = complete({"kcal": 470, "protein_g": 55}, est)
    assert out["kcal"] == 470 and out["protein_g"] == 55
    assert 40 <= out["carbs_g"] <= 90 and 5 <= out["fat_g"] <= 20 and out["fibre_g"] == round(6.5 * 470 / 655, 1)
    assert abs(macro_kcal(out) - 470) < 2


def test_complete_keeps_known_fibre_and_needs_something_to_fill():
    assert complete({"kcal": 600, "protein_g": 30, "carbs_g": 50, "fibre_g": 3}, {"kcal": 500, "fat_g": 20, "fibre_g": 8}) == {
        "kcal": 600,
        "protein_g": 30,
        "carbs_g": 50,
        "fat_g": 31.1,
        "fibre_g": 3,
    }
    assert complete({"kcal": 470, "protein_g": 55}, {"kcal": 400, "protein_g": 30}) is None  # no carbs or fat to go on
    assert complete({"kcal": 100, "protein_g": 30}, {"kcal": 400, "carbs_g": 30}) is None  # nothing left to fill


# ---------- the planner ----------


async def test_planned_dish_with_partial_book_numbers_is_completed_once(hass: HomeAssistant, setup, ai_calls):
    d = day(0)
    book(hass).set("ig-pasta", PRINTED)
    p = planner(hass, setup, [{"date": d, "dinner": "Hot honey chicken and halloumi pasta"}], [PASTA])
    assert await p.async_sync() == 1
    e = await dinner(hass, d)
    assert {k: e[k] for k in DONE} == DONE and e["ref"] == "ig-pasta" and e["source"] == "plan"
    kept = book(hass).get("ig-pasta")
    assert {k: kept[k] for k in DONE} == DONE and kept["source"] == "recipe" and kept["completed"] == "estimate"
    assert kept["from"]["kcal"] == 470 and kept["from"]["carbs_g"] == 0
    assert p.completed == ["ig-pasta"] and [c["task"] for c in ai_calls] == ["estimate a dish"]
    assert "120 g Rigatoni" in ai_calls[0]["instructions"] and "per portion" in ai_calls[0]["instructions"]
    assert await p.async_sync() == 0 and len(ai_calls) == 1 and p.completed == []  # done once
    # the recipe page and "Log a portion" get the whole numbers too
    assert (await call(hass, "estimate", {"kind": "dish", "dish_id": "ig-pasta"}))["carbs_g"] == DONE["carbs_g"]


async def test_planned_entries_from_partial_numbers_are_refreshed_unless_edited(hass: HomeAssistant, setup, ai_calls):
    d0, d1 = day(0), day(1)
    diary = setup.runtime_data.diary
    name = "Hot honey chicken and halloumi pasta"
    old = {
        "name": name,
        "meal": "dinner",
        "source": "plan",
        "ref": "ig-pasta",
        "portions": 1,
        "note": PLAN_NOTE,
        "kcal": 470,
        "protein_g": 55,
    }
    today_e = diary.add(d0, {**old, "plan_key": f"{d0}|dinner"})
    mine = diary.add(d1, {**old, "plan_key": f"{d1}|dinner", "edited": True})  # edited numbers: never touched
    book(hass).set("ig-pasta", PRINTED)
    p = planner(hass, setup, [{"date": d0, "dinner": name}, {"date": d1, "dinner": name}], [PASTA])
    assert await p.async_sync() == 1
    e = await dinner(hass, d0)
    assert e["id"] == today_e["id"] and {k: e[k] for k in DONE} == DONE and e["portions"] == 1
    kept = await dinner(hass, d1)
    assert kept["id"] == mine["id"] and kept["carbs_g"] == 0 and kept["fat_g"] == 0
    assert await p.async_sync() == 0


async def test_meal_by_name_with_partial_numbers_is_completed(hass: HomeAssistant, setup, ai_calls):
    d = day(1)
    book(hass).set("name:chicken wrap", {"kcal": 480, "protein_g": 30, "source": "ai"})
    p = planner(hass, setup, [{"date": d, "dinner": "Chicken wrap"}])
    await p.async_sync()
    e = await dinner(hass, d)
    # the fake "estimate a meal from words": carbs 18, fat 20 → 360 kcal left after protein, 4*18 + 9*20 = 252 of them
    assert (
        e["kcal"] == 480
        and e["protein_g"] == 30
        and e["carbs_g"] == round(18 * 360 / 252, 1)
        and e["fat_g"] == round(20 * 360 / 252, 1)
    )
    assert book(hass).get("name:chicken wrap")["completed"] == "estimate"


async def test_every_partial_dish_in_the_book_is_completed(hass: HomeAssistant, setup, ai_calls):
    other = {**PASTA, "id": "ig-other", "name": "Other", "name_en": "Other"}
    book(hass).set("ig-pasta", PRINTED)
    book(hass).set("ig-other", {"kcal": 400, "protein_g": 30, "carbs_g": 40, "fat_g": 13, "source": "recipe"})  # whole
    book(hass).set("name:soup", {"kcal": 300, "protein_g": 10, "source": "ai"})
    book(hass).set("r123", {"kcal": 500, "protein_g": 20, "source": "recipe"})  # not a library recipe: nothing to go on
    p = planner(hass, setup, [], [PASTA, other])
    assert await p.async_sync() == 0
    assert sorted(p.completed) == ["ig-pasta", "name:soup"]
    assert not book(hass).get("ig-other").get("completed") and not book(hass).get("r123").get("completed")


async def test_an_ai_that_cant_fill_it_is_asked_once(hass: HomeAssistant, setup, ai_calls):
    book(hass).set("ig-pasta", PRINTED)
    p = planner(hass, setup, [], [PASTA])
    saved = AI_ANSWERS["estimate a dish"]
    AI_ANSWERS["estimate a dish"] = {"name": "x", "kcal": 400, "protein_g": 30, "carbs_g": 0, "fat_g": 0, "fibre_g": 0}
    try:
        await p.async_sync()
        await p.async_sync()
    finally:
        AI_ANSWERS["estimate a dish"] = saved
    kept = book(hass).get("ig-pasta")
    assert kept["completed"] == "none" and kept["kcal"] == 470 and kept["carbs_g"] == 0 and len(ai_calls) == 1
