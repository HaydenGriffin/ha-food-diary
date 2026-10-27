"""Veg (1.11.0): every estimate with numbers gives the grams of vegetables and pulses in it (`veg_g`, or `veg_g` in
per_100), entries keep it and scale it with portions and grams, days add it up (`veg_g`, `veg_known`), batches and saved
meals carry it, and food without it is unknown, never 0."""

from __future__ import annotations

import copy
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
import pytest
import voluptuous as vol

from custom_components.food_diary.const import DOMAIN
from custom_components.food_diary.estimate import label_result, off_product

from .conftest import AI_ANSWERS
from .test_batches import ai_item
from .test_food_diary import B64, KCAL, call, item

PHOTO = "estimate a meal from a photo"
SOUP = {"name": "Lentil soup", "kcal": 250, "protein_g": 12, "carbs_g": 30, "fat_g": 6, "fibre_g": 8}
BEANS = {"kcal": 80, "protein_g": 5, "carbs_g": 13, "fat_g": 0.5, "fibre_g": 4, "veg_g": 60}


def veg_item(name: str, grams: float, kcal_100: float, veg_100: Any) -> dict[str, Any]:
    return {**item(name, grams, kcal_100), "veg_g_100": veg_100}


async def logged(hass: HomeAssistant, **f: Any) -> dict[str, Any]:
    return (await call(hass, "log_food", {"meal": "lunch", **f}))["entry"]


def stored(setup, entry_id: str) -> dict[str, Any]:
    diary = setup.runtime_data.diary
    return copy.deepcopy(next(e for days in diary.data["days"].values() for e in days if e["id"] == entry_id))


# ---------- estimates ----------


async def test_a_photo_gives_its_veg_and_each_items(hass: HomeAssistant, setup, ai_calls, monkeypatch):
    plate = [veg_item("Chicken", 140, 213, 0), veg_item("Broccoli", 80, 35, 100), veg_item("Chickpeas", 50, 160, 100)]
    monkeypatch.setitem(AI_ANSWERS, PHOTO, {**AI_ANSWERS[PHOTO], "veg_g": 999, "items": plate})
    out = await call(hass, "estimate", {"kind": "photo", "image": B64})
    chicken, broccoli, chickpeas = out["items"]
    assert (chicken["veg_g"], chicken["per_100"]["veg_g"]) == (0, 0)
    assert (broccoli["veg_g"], broccoli["per_100"]["veg_g"], chickpeas["veg_g"]) == (80, 100, 50)
    assert out["veg_g"] == 130  # what the items add up to, not the AI's own figure for the plate
    asked = ai_calls[-1]
    assert asked["structure"]["veg_g"]["required"] and "veg_g_100" in asked["structure"]["items"]["selector"]["object"]["fields"]
    assert "vegetables and pulses" in asked["instructions"] and "potatoes and fruit don't count" in asked["instructions"]


async def test_a_photo_whose_items_dont_all_know_veg_doesnt(hass: HomeAssistant, setup, monkeypatch):
    plate = [veg_item("Chicken", 140, 213, 0), item("Rice", 150, 130)]  # rice: no veg given
    monkeypatch.setitem(AI_ANSWERS, PHOTO, {**AI_ANSWERS[PHOTO], "veg_g": 40, "items": plate})
    out = await call(hass, "estimate", {"kind": "photo", "image": B64})
    assert "veg_g" not in out and "veg_g" not in out["items"][1] and out["items"][0]["veg_g"] == 0


async def test_a_photo_with_items_left_out_keeps_its_own_veg(hass: HomeAssistant, setup, monkeypatch):
    plate = [veg_item("Chicken", 140, 213, 0), veg_item("Peas", 80, 80, 100), item("", 50, 100)]
    monkeypatch.setitem(AI_ANSWERS, PHOTO, {**AI_ANSWERS[PHOTO], "veg_g": 120, "items": plate})
    out = await call(hass, "estimate", {"kind": "photo", "image": B64})
    assert out["items_partial"] is True and out["veg_g"] == 120


@pytest.mark.parametrize(("veg", "known"), [(150, 150), (0, 0), (None, None), (-5, None), ("lots", None), (99999, None)])
async def test_words_give_veg_when_it_can_be_true(hass: HomeAssistant, setup, monkeypatch, veg, known):
    monkeypatch.setitem(AI_ANSWERS, "estimate a meal from words", {**AI_ANSWERS["estimate a meal from words"], "veg_g": veg})
    out = await call(hass, "estimate", {"kind": "text", "text": "beans on toast"})
    assert out.get("veg_g") == known


@pytest.mark.parametrize(("veg", "known"), [(35, 35), (0, 0), (101, None), (None, None)])
async def test_per_100_gives_veg_per_100(hass: HomeAssistant, setup, monkeypatch, veg, known):
    answer = {"kcal_100": 120, "protein_g_100": 7, "carbs_g_100": 15, "fat_g_100": 3, "fibre_g_100": 5, "unit": "g"}
    monkeypatch.setitem(AI_ANSWERS, "estimate per 100 g", {**answer, "veg_g_100": veg})
    out = await call(hass, "estimate", {"kind": "per_100", "name": "Bean chilli"})
    assert out["per_100"].get("veg_g") == known


async def test_a_label_gives_veg_and_teaches_it_with_the_barcode(hass: HomeAssistant, setup, ai_calls, monkeypatch):
    monkeypatch.setitem(AI_ANSWERS, "read a nutrition label", {**AI_ANSWERS["read a nutrition label"], "veg_g_100": 10})
    out = await call(hass, "estimate", {"kind": "label", "image": B64, "amount": "50 g"})
    assert out["per_100"]["veg_g"] == 10 and out["veg_g"] == 4.4  # the label's AI read 44.1 g eaten
    assert "veg_g_100" in ai_calls[-1]["structure"]
    again = await call(hass, "estimate", {"kind": "barcode", "barcode": out["barcode"], "amount": "100 g"})
    assert again["per_100"]["veg_g"] == 10 and again["veg_g"] == 10 and len(ai_calls) == 1  # the product book's, no AI


def test_open_food_facts_has_no_veg():
    product = off_product({"product_name": "Baked beans", "nutriments": {"energy-kcal_100g": 78, "fiber_100g": 3.7}})
    out = label_result({**product, "eaten_g": 200})
    assert "veg_g" not in out and "veg_g" not in out["per_100"]


async def test_ingredients_give_raw_veg_per_100(hass: HomeAssistant, setup, ai_calls, monkeypatch):
    monkeypatch.setitem(
        AI_ANSWERS,
        "estimate ingredients",
        {
            "items": [
                {**ai_item("Lentils", 200, 350, 2.5), "veg_g_100": 100},
                {**ai_item("Rice", 300, 350, 2.5), "veg_g_100": 0},
                {**ai_item("My stock", 500, 10, 0.8), "veg_g_100": 5},
                {**ai_item("Tomatoes", 400, 20, 0.8), "veg_g_100": 100},
            ]
        },
    )
    own = {"kcal": 10, "protein_g": 1, "carbs_g": 1, "fat_g": 0, "fibre_g": 0}
    out = await call(
        hass,
        "estimate",
        {
            "kind": "ingredients",
            "items": [
                {"name": "Lentils", "grams": 200},
                {"name": "Rice", "grams": 300},
                {"name": "My stock", "grams": 500, "per_100": own},  # her numbers, no veg: the AI's veg
                {"name": "Tomatoes", "grams": 400, "per_100": {**own, "veg_g": 90}},  # her veg wins
            ],
        },
    )
    assert [i["per_100"]["veg_g"] for i in out["items"]] == [100, 0, 5, 90]
    assert "veg_g_100" in ai_calls[-1]["structure"]["items"]["selector"]["object"]["fields"]


async def test_a_dish_gives_veg_and_keeps_it_with_its_answer(hass: HomeAssistant, setup, ai_calls, monkeypatch):
    monkeypatch.setitem(AI_ANSWERS, "estimate a dish", {**AI_ANSWERS["estimate a dish"], "veg_g": 140})
    ask = {"kind": "dish", "dish_id": "chilli", "dish_name": "Chilli", "ingredients": ["500 g beans"], "servings": 4}
    assert (await call(hass, "estimate", ask))["veg_g"] == 140
    kept = await call(hass, "estimate", ask)
    assert kept["veg_g"] == 140 and kept["source"] == "dish" and len(ai_calls) == 1
    await call(hass, "set_dish_nutrition", {"dish_id": "chilli", "kcal": 400}, response=False)  # her numbers: no veg
    assert "veg_g" not in await call(hass, "estimate", ask)


# ---------- entries and days ----------


async def test_veg_follows_portions_and_days_add_up_what_is_known(hass: HomeAssistant, setup):
    e = await logged(hass, name="Beans", **BEANS, portions=1.5)
    assert (e["veg_g"], e["per_portion"]["veg_g"]) == (90, 60)
    e = (await call(hass, "update_food", {"entry_id": e["id"], "portions": 2}))["entry"]
    assert e["veg_g"] == 120 and "edited" not in e
    await logged(hass, name="Toast", kcal=150)  # no veg: unknown, not 0
    await logged(hass, name="Water", kcal=0.5, veg_g=0)  # an explicit 0 is known
    day = await call(hass, "get_day")
    assert (day["veg_g"], day["veg_known"]) == (120, 2)
    assert "veg_g" not in next(x for x in day["entries"] if x["name"] == "Toast")
    attrs = hass.states.get(KCAL).attributes
    assert (attrs["veg_g"], attrs["veg_known"]) == (120, 2)
    history = await call(hass, "get_history", {"days": 1})
    assert (history["days"][0]["veg_g"], history["days"][0]["veg_known"]) == (120, 2)


async def test_an_empty_day_knows_no_veg(hass: HomeAssistant, setup):
    day = await call(hass, "get_day")
    assert (day["veg_g"], day["veg_known"], day["fullness"]) == (0, 0, {})


@pytest.mark.parametrize("veg", [-1, "x", float("nan"), 20001])
async def test_veg_must_be_a_real_amount(hass: HomeAssistant, setup, veg):
    with pytest.raises(vol.Invalid):
        await logged(hass, name="Beans", kcal=80, veg_g=veg)
    with pytest.raises(vol.Invalid):
        await logged(hass, name="Beans", grams=100, per_100={"kcal": 80, "veg_g": veg})


async def test_veg_by_weight_follows_the_grams(hass: HomeAssistant, setup):
    e = await logged(hass, name="Peas", grams=80, per_100={"kcal": 80, "veg_g": 100})
    assert (e["veg_g"], e["per_100"]["veg_g"]) == (80, 100)
    e = (await call(hass, "update_food", {"entry_id": e["id"], "grams": 120}))["entry"]
    assert (e["veg_g"], e["per_portion"]["veg_g"]) == (120, 120)


async def test_veg_for_what_was_eaten_by_weight_is_kept_per_100(hass: HomeAssistant, setup):
    e = await logged(hass, name="Ratatouille", grams=200, veg_g=150, per_100={"kcal": 60})
    assert (e["veg_g"], e["per_100"]["veg_g"]) == (150, 75)
    e = (await call(hass, "update_food", {"entry_id": e["id"], "grams": 100}))["entry"]
    assert e["veg_g"] == 75


async def test_a_switch_to_by_weight_takes_veg_from_per_100_and_undo_puts_it_back(hass: HomeAssistant, setup):
    e = await logged(hass, name="Beans", **BEANS)
    sw = await call(
        hass, "update_food", {"entry_id": e["id"], "grams": 200, "per_100": {"kcal": 80, "veg_g": 50}, "expected_rev": 1}
    )
    assert (sw["entry"]["veg_g"], sw["entry"]["per_100"]["veg_g"], sw["before"]["veg_g"]) == (100, 50, 60)
    back = await call(hass, "restore_food", {"entry_id": e["id"], "entry": sw["before"], "expected_rev": 2})
    assert {k: v for k, v in back["entry"].items() if k != "rev"} == {k: v for k, v in sw["before"].items() if k != "rev"}
    unknown = await call(hass, "update_food", {"entry_id": e["id"], "grams": 200, "per_100": {"kcal": 80}})
    assert "veg_g" not in unknown["entry"] and "veg_g" not in unknown["entry"]["per_portion"]  # per_100 without veg


async def test_veg_doesnt_go_with_a_switch(hass: HomeAssistant, setup):
    e = await logged(hass, name="Beans", **BEANS)
    with pytest.raises(ServiceValidationError, match="veg_g"):
        await call(hass, "update_food", {"entry_id": e["id"], "grams": 200, "per_100": {"kcal": 80}, "veg_g": 10})


async def test_own_veg_is_her_number_and_other_numbers_keep_it(hass: HomeAssistant, setup):
    e = await logged(hass, name="Beans", **BEANS, portions=2)
    e = (await call(hass, "update_food", {"entry_id": e["id"], "kcal": 200}))["entry"]
    assert e["veg_g"] == 120 and e["edited"] is True
    e = (await call(hass, "update_food", {"entry_id": e["id"], "veg_g": 40}))["entry"]
    assert (e["veg_g"], e["per_portion"]["veg_g"], e["kcal"]) == (40, 20, 200)


async def test_own_veg_by_weight_scales_with_a_new_weight(hass: HomeAssistant, setup):
    e = await logged(hass, name="Peas", grams=100, per_100={"kcal": 80})
    e = (await call(hass, "update_food", {"entry_id": e["id"], "veg_g": 90}))["entry"]
    e = (await call(hass, "update_food", {"entry_id": e["id"], "grams": 50}))["entry"]
    assert e["veg_g"] == 45 and e["edited"] is True


async def test_too_much_veg_is_refused_like_the_other_numbers(hass: HomeAssistant, setup):
    e = await logged(hass, name="Peas", grams=0.1, per_100={"kcal": 80})
    await call(hass, "update_food", {"entry_id": e["id"], "veg_g": 20000, "kcal": 0})
    with pytest.raises(ServiceValidationError, match="too big"):  # 20000 g of veg in 0.1 g, made 20000 g
        await call(hass, "update_food", {"entry_id": e["id"], "grams": 20000})
    assert stored(setup, e["id"])["veg_g"] == 20000


async def test_copies_and_recent_foods_keep_veg(hass: HomeAssistant, setup):
    from .test_week_features import day

    await logged(hass, name="Beans", **BEANS, portions=2, date=day(-1))
    await logged(hass, name="Peas", grams=80, per_100={"kcal": 80, "veg_g": 100}, date=day(-1))
    await call(hass, "copy_day", {"from": day(-1), "to": [day(0)]}, response=False)
    today = await call(hass, "get_day")
    assert sorted(e["veg_g"] for e in today["entries"]) == [80, 120] and today["veg_known"] == 2
    foods = {f["name"]: f for f in (await call(hass, "get_recent"))["foods"]}
    assert foods["Beans"]["veg_g"] == 60 and foods["Peas"]["per_100"]["veg_g"] == 100


async def test_food_from_before_has_no_veg(hass: HomeAssistant, setup):
    e = await logged(hass, **SOUP)
    assert "veg_g" not in stored(setup, e["id"]) and (await call(hass, "get_day"))["veg_known"] == 0


async def test_a_saved_meal_knows_veg_when_every_food_does(hass: HomeAssistant, setup):
    foods = [{"name": "Beans", **BEANS}, {"name": "Toast", "kcal": 150, "veg_g": 0}]
    saved = await call(hass, "save_meal", {"name": "Beans on toast", "meal": "lunch", "foods": foods})
    assert saved["saved"]["veg_g"] == 60
    foods[1].pop("veg_g")
    saved = await call(hass, "save_meal", {"name": "Beans on toast", "meal": "lunch", "foods": foods})
    assert "veg_g" not in saved["saved"]


# ---------- batches ----------


def veg_pot(**extra: Any) -> dict[str, Any]:
    return {
        "name": "Dal",
        "ingredients": [
            {"name": "Red lentils", "grams": 300, "per_100": {"kcal": 350, "veg_g": 100}, "cooked_factor": 2.5},
            {"name": "Tomatoes", "grams": 400, "per_100": {"kcal": 20, "veg_g": 100}, "cooked_factor": 0.8},
            {"name": "Oil", "grams": 30, "per_100": {"kcal": 884, "veg_g": 0}, "cooked_factor": 1},
        ],
        **extra,
    }


async def test_a_batch_knows_its_veg_when_every_ingredient_does(hass: HomeAssistant, setup):
    batch = (await call(hass, "save_batch", veg_pot(cooked_g=1400)))["batch"]
    assert batch["totals"]["veg_g"] == 700 and batch["per_100"]["veg_g"] == 50
    bowl = await logged(hass, source="batch", ref=batch["id"], grams=300)
    assert bowl["veg_g"] == 150
    await call(hass, "update_batch", {"batch_id": batch["id"], "cooked_g": 1000}, response=False)
    assert (await call(hass, "get_day"))["veg_g"] == 210  # the bowl follows the weighed pot
    pot = veg_pot()
    del pot["ingredients"][2]["per_100"]["veg_g"]
    unknown = (await call(hass, "save_batch", {**pot, "name": "Dal 2"}))["batch"]
    assert "veg_g" not in unknown["per_100"] and "veg_g" not in unknown["totals"]


async def test_a_dish_by_plan_without_veg_stays_unknown(hass: HomeAssistant, setup):
    """A recipe's numbers from the book carry no veg: food logged from it doesn't know veg (no AI call for it)."""
    hass.data[DOMAIN]["dishes"].set("soup", {**SOUP, "source": "own"})
    out = await call(hass, "estimate", {"kind": "dish", "dish_id": "soup", "dish_name": "Soup"})
    assert "veg_g" not in out


# ---------- a new amount with veg of her own (review fix) ----------


async def test_a_new_weight_with_own_veg_keeps_the_numbers_for_that_weight(hass: HomeAssistant, setup):
    e = await logged(hass, name="Peas", grams=100, per_100={"kcal": 80, "protein_g": 5})
    e = (await call(hass, "update_food", {"entry_id": e["id"], "grams": 200, "veg_g": 180}))["entry"]
    assert (e["kcal"], e["protein_g"], e["veg_g"], e["grams"]) == (160, 10, 180, 200)
    assert (e["per_portion"]["kcal"], e["per_portion"]["veg_g"]) == (160, 180)


async def test_new_portions_with_own_veg_keep_the_numbers_for_those_portions(hass: HomeAssistant, setup):
    e = await logged(hass, name="Beans", **BEANS)
    e = (await call(hass, "update_food", {"entry_id": e["id"], "portions": 2, "veg_g": 100}))["entry"]
    assert (e["kcal"], e["protein_g"], e["veg_g"], e["per_portion"]["veg_g"]) == (160, 10, 100, 50)
    e = (await call(hass, "update_food", {"entry_id": e["id"], "portions": 4, "kcal": 300}))["entry"]
    assert (e["kcal"], e["protein_g"], e["veg_g"]) == (300, 20, 200)  # left out: scaled with the portions


# ---------- the meal plan carries veg (review fix) ----------


async def test_a_planned_meal_takes_veg_from_the_plans_numbers(hass: HomeAssistant, setup, ai_calls):
    from .test_planner import day, entries, planner

    with_veg = {"name": "Dal", "nutrition": {**{k: v for k, v in SOUP.items() if k != "name"}, "veg_g": 150}}
    without = {"name": "Soup", "nutrition": {k: v for k, v in SOUP.items() if k != "name"}}
    await planner(hass, setup, [{"date": day(1), "dinner": with_veg, "lunch": without}]).async_sync()
    names = await entries(hass, day(1))
    assert names["Dal"]["veg_g"] == 150 and names["Dal"]["per_portion"]["veg_g"] == 150
    assert "veg_g" not in names["Soup"]  # unknown stays unknown
    assert ai_calls == []


async def test_a_planned_recipe_takes_the_librarys_veg_and_follows_it(hass: HomeAssistant, setup, ai_calls):
    from .test_planner import day, dinner, planner

    numbers = {k: v for k, v in SOUP.items() if k != "name"}
    week = [{"date": day(1), "dinner": "Lentil soup"}]
    dish = {"id": "r5", "name": "Lentil soup", "nutrition": {**numbers, "veg_g": 120}}
    p = planner(hass, setup, week, [dish])
    await p.async_sync()
    assert (await dinner(hass, day(1)))["veg_g"] == 120 and hass.data[DOMAIN]["dishes"].get("r5")["veg_g"] == 120
    p = planner(hass, setup, week, [{**dish, "nutrition": {**numbers, "veg_g": 90}}])  # only the veg changed
    await p.async_sync()
    assert (await dinner(hass, day(1)))["veg_g"] == 90
    p = planner(hass, setup, week, [{**dish, "nutrition": numbers}])  # no veg any more: unknown again
    await p.async_sync()
    assert "veg_g" not in await dinner(hass, day(1))
    assert ai_calls == []


async def test_a_planned_meal_by_name_keeps_the_estimates_veg(hass: HomeAssistant, setup, ai_calls, monkeypatch):
    from .test_planner import day, dinner, planner

    monkeypatch.setitem(AI_ANSWERS, "estimate a meal from words", {**AI_ANSWERS["estimate a meal from words"], "veg_g": 70})
    p = planner(hass, setup, [{"date": day(1), "dinner": "Vegetable stir fry"}])
    await p.async_sync()
    assert (await dinner(hass, day(1)))["veg_g"] == 70
    assert hass.data[DOMAIN]["dishes"].get("name:vegetable stir fry")["veg_g"] == 70
    await p.async_sync()
    assert len(ai_calls) == 1  # kept: not asked again


async def test_veg_for_a_completed_recipe_needs_no_estimate_again(hass: HomeAssistant, setup, ai_calls):
    from .test_planner import day, dinner, planner

    week = [{"date": day(1), "dinner": "Stew"}]
    dish = {"id": "r6", "name": "Stew", "nutrition": {"kcal": 600, "protein_g": 20}}  # partial: completed once
    await planner(hass, setup, week, [dish]).async_sync()
    asked = len(ai_calls)
    assert asked >= 1 and "veg_g" not in await dinner(hass, day(1))
    await planner(hass, setup, week, [{**dish, "nutrition": {**dish["nutrition"], "veg_g": 200}}]).async_sync()
    assert (await dinner(hass, day(1)))["veg_g"] == 200 and len(ai_calls) == asked
