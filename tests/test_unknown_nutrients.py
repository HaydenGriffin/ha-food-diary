"""A number that wasn't given is not a 0 that was (1.6.0). An entry lists the macros its writer didn't give in `unknown`
(stored as 0, so totals stay a lower bound), copies, saved meals and the plan bring them along, an edit that gives one takes
it off, and a day counts them in `incomplete`. Entries from before have no `unknown`: calories only reads as all four
unknown. Each test here failed before 1.6.0 (no `unknown`, no `incomplete`)."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.util import dt as dt_util
import pytest
import voluptuous as vol

from custom_components.food_diary.checks import off
from custom_components.food_diary.const import OFF_URL
from custom_components.food_diary.planner import Planner

from .conftest import AI_ANSWERS, LIBRARY, PLAN
from .test_food_diary import HOOK, call

ALL = ["protein_g", "carbs_g", "fat_g", "fibre_g"]
PROTEIN = "sensor.alex_food_diary_protein_today"


def day(n: int) -> str:
    return (dt_util.now().date() + timedelta(days=n)).isoformat()


async def log(hass: HomeAssistant, **f: Any) -> dict[str, Any]:
    return (await call(hass, "log_food", {"meal": "lunch", **f}))["entry"]


async def entries(hass: HomeAssistant, d: str) -> dict[str, dict[str, Any]]:
    return {e["name"]: e for e in (await call(hass, "get_day", {"date": d}))["entries"]}


def legacy(setup, d: str, name: str, **numbers: float) -> dict[str, Any]:
    """An entry as a release before 1.6.0 stored it: every number, no `unknown`."""
    e = {"id": f"old-{name.lower()}", "rev": 1, "at": f"{d}T08:00", "meal": "breakfast", "name": name, "portions": 1.0}
    values = {k: float(numbers.get(k, 0)) for k in ("kcal", *ALL)}
    e |= {"source": "manual", "ref": "", "per_portion": dict(values), **values}
    setup.runtime_data.diary.data["days"].setdefault(d, []).append(e)
    return e


# ---------- writing ----------


async def test_absent_is_unknown_and_an_explicit_zero_is_known(hass: HomeAssistant, setup):
    toast = await log(hass, name="Toast", kcal=200, protein_g=0)
    assert toast["unknown"] == ["carbs_g", "fat_g", "fibre_g"] and toast["protein_g"] == 0 and toast["carbs_g"] == 0
    full = await log(hass, name="Skyr", kcal=120, protein_g=20, carbs_g=8, fat_g=0, fibre_g=0)
    assert "unknown" not in full  # left out when there are none
    vodka = await log(hass, name="Vodka", kcal=100, protein_g=0, carbs_g=0, fat_g=0, fibre_g=0)
    assert vodka["unknown"] == []  # truly none: stored, so it doesn't read as an old calories-only entry
    label = await log(hass, name="Oat bar", per_100={"kcal": 400, "protein_g": 10, "carbs_g": 60, "fat_g": 12}, grams=50)
    assert label["unknown"] == ["fibre_g"] and label["kcal"] == 200
    day0 = await call(hass, "get_day")
    assert day0["incomplete"] == {"carbs_g": 1, "fat_g": 1, "fibre_g": 2}
    assert day0["totals"]["protein_g"] == 25  # totals unchanged: the unknowns count as 0
    with pytest.raises((vol.Invalid, ServiceValidationError)):  # only macros can be unknown
        await call(hass, "log_food", {"name": "Tea", "kcal": 30, "unknown": ["kcal"]})


async def test_log_food_takes_unknown_from_an_estimate(hass: HomeAssistant, setup, monkeypatch):
    monkeypatch.setitem(
        AI_ANSWERS, "estimate a meal from words", {"name": "Pierogi", "kcal": 450, "protein_g": 14, "fat_g": None}
    )
    est = await call(hass, "estimate", {"kind": "text", "text": "pierogi"})
    assert est["unknown"] == ["carbs_g", "fat_g", "fibre_g"] and est["fat_g"] == 0  # numbers stay numbers for old apps
    e = await log(hass, **{k: est[k] for k in ("name", "kcal", "protein_g", "carbs_g", "fat_g", "fibre_g", "unknown")})
    assert e["unknown"] == ["carbs_g", "fat_g", "fibre_g"]
    r = await call(hass, "log_food", {"name": "Pierogi", "kcal": 450, "protein_g": 14})
    assert r["incomplete"] == {"carbs_g": 2, "fat_g": 2, "fibre_g": 2}  # log_food's answer has the day's count too


async def test_webhook_and_open_food_facts_keep_unknowns(hass: HomeAssistant, setup, hass_client_no_auth, aioclient_mock):
    aioclient_mock.get(
        OFF_URL.format(code="5010029000023"),
        json={
            "status": 1,
            "product": {
                "product_name": "Wheat biscuits",
                "nutriments": {"energy-kcal_100g": 362, "proteins_100g": 12, "carbohydrates_100g": 69, "fat_100g": 2},
                "serving_quantity": 37.5,
            },
        },
    )
    client = await hass_client_no_auth()
    body = await (await client.post(HOOK, json={"kind": "barcode", "barcode": "5010029000023", "amount": "2 biscuits"})).json()
    assert body["status"] == "logged" and body["unknown"] == ["fibre_g"] and body["fibre_g"] == 0
    assert (await entries(hass, day(0)))["Wheat biscuits"]["unknown"] == ["fibre_g"]


async def test_update_that_gives_a_macro_clears_it(hass: HomeAssistant, setup):
    e = await log(hass, name="Soup", kcal=300, protein_g=12)
    assert e["unknown"] == ["carbs_g", "fat_g", "fibre_g"]
    e = (await call(hass, "update_food", {"entry_id": e["id"], "portions": 2}))["entry"]
    assert e["unknown"] == ["carbs_g", "fat_g", "fibre_g"] and e["kcal"] == 600  # scaling keeps them
    e = (await call(hass, "update_food", {"entry_id": e["id"], "fat_g": 0, "fibre_g": 8}))["entry"]
    assert e["unknown"] == ["carbs_g"] and e["fibre_g"] == 8  # an explicit 0 is known too
    e = (await call(hass, "update_food", {"entry_id": e["id"], "carbs_g": 40}))["entry"]
    assert "unknown" not in e and "incomplete" not in await call(hass, "get_day")


async def test_legacy_entries_read_by_one_rule(hass: HomeAssistant, setup):
    legacy(setup, day(0), "Cake", kcal=350)  # calories only: all four unknown
    legacy(setup, day(0), "Eggs", kcal=150, protein_g=12, fat_g=10)  # some macros: all known (the zeros are real)
    legacy(setup, day(0), "Water", kcal=0)  # no calories: not counted
    d = await call(hass, "get_day")
    assert d["incomplete"] == {k: 1 for k in ALL}
    names = {e["name"]: e for e in d["entries"]}
    assert all("unknown" not in e for e in names.values())  # read-time only: nothing is migrated
    cake = (await call(hass, "update_food", {"entry_id": "old-cake", "protein_g": 4}))["entry"]
    assert cake["unknown"] == ["carbs_g", "fat_g", "fibre_g"]  # an edit writes what the rule read, less what it gave
    eggs = (await call(hass, "update_food", {"entry_id": "old-eggs", "portions": 2}))["entry"]
    assert "unknown" not in eggs


async def test_incomplete_on_history_week_review_and_sensor(hass: HomeAssistant, setup):
    await log(hass, name="Toast", kcal=200, protein_g=6, date=day(-1))
    await log(hass, name="Skyr", kcal=120, protein_g=20, carbs_g=8, fat_g=0, fibre_g=0, date=day(-1))
    await log(hass, name="Bar", kcal=220, protein_g=10, carbs_g=20, fat_g=9, fibre_g=2, date=day(-2))
    await log(hass, name="Curry", kcal=600)
    rows = {r["date"]: r for r in (await call(hass, "get_history", {"days": 3}))["days"]}
    assert rows[day(-1)]["incomplete"] == {"carbs_g": 1, "fat_g": 1, "fibre_g": 1} and rows[day(-1)]["protein_g"] == 26
    assert "incomplete" not in rows[day(-2)] and rows[day(0)]["incomplete"] == {k: 1 for k in ALL}
    review = await call(hass, "get_week_review", {"date": day(-1)})
    by_day = {r["date"]: r for r in review["days"]}
    assert by_day[day(-1)]["incomplete"] == {"carbs_g": 1, "fat_g": 1, "fibre_g": 1} and "incomplete" not in by_day[day(-2)]
    await hass.async_block_till_done()
    assert hass.states.get(PROTEIN).attributes["incomplete"] == 1  # the state is a lower bound
    await call(hass, "update_food", {"entry_id": (await entries(hass, day(0)))["Curry"]["id"], "protein_g": 30})
    await hass.async_block_till_done()
    assert "incomplete" not in hass.states.get(PROTEIN).attributes


# ---------- inherited ----------


async def test_copies_bring_their_unknowns(hass: HomeAssistant, setup):
    await log(hass, name="Soup", kcal=300, protein_g=12)
    await log(hass, name="Vodka", kcal=100, protein_g=0, carbs_g=0, fat_g=0, fibre_g=0)
    legacy(setup, day(0), "Cake", kcal=350)
    await call(hass, "copy_day", {"from": day(0), "to": [day(1)]})
    copied = await entries(hass, day(1))
    assert copied["Soup"]["unknown"] == ["carbs_g", "fat_g", "fibre_g"]
    assert copied["Vodka"]["unknown"] == [] and copied["Cake"]["unknown"] == ALL  # an old entry's reading comes along
    assert (await call(hass, "get_day", {"date": day(1)}))["incomplete"] == {
        "protein_g": 1,
        "carbs_g": 2,
        "fat_g": 2,
        "fibre_g": 2,
    }


async def test_saved_meals_and_recent_foods_carry_unknowns(hass: HomeAssistant, setup):
    await log(hass, name="Soup", kcal=300, protein_g=12, meal="dinner")
    await log(hass, name="Bread", kcal=150, protein_g=5, carbs_g=28, fat_g=2, fibre_g=3, meal="dinner")
    saved = (await call(hass, "save_meal", {"name": "Soup and bread", "meal": "dinner"}))["saved"]
    assert saved["unknown"] == ["carbs_g", "fat_g", "fibre_g"] and saved["carbs_g"] == 28  # a lower bound, like a day
    recent = await call(hass, "get_recent")
    soup = next(f for f in recent["foods"] if f["name"] == "Soup")
    assert soup["unknown"] == ["carbs_g", "fat_g", "fibre_g"]
    assert "unknown" not in next(f for f in recent["foods"] if f["name"] == "Bread")
    again = await log(hass, **{k: soup[k] for k in ("name", "kcal", "protein_g", "carbs_g", "fat_g", "fibre_g", "unknown")})
    assert again["unknown"] == ["carbs_g", "fat_g", "fibre_g"]
    meal = await log(
        hass, name=saved["name"], source="saved", kcal=saved["kcal"], protein_g=saved["protein_g"], unknown=saved["unknown"]
    )
    assert meal["unknown"] == ["carbs_g", "fat_g", "fibre_g"]


async def test_planned_meals_bring_the_plans_unknowns(hass: HomeAssistant, setup, ai_calls):
    # (protein and carbs explain the calories, so the stew isn't "partial" and completed from an estimate: macros.py)
    stew = {"id": "r-stew", "name": "Stew", "nutrition": {"kcal": 500, "protein_g": 30, "carbs_g": 85, "fat_g": None}}
    dal = {"id": "r-dal", "name": "Dal", "nutrition": {"kcal": 400, "protein_g": 18, "carbs_g": 50, "fat_g": 12, "fibre_g": 0}}
    week = [{"date": day(1), "dinner": "Stew", "lunch": "Dal", "breakfast": {"name": "Porridge", "nutrition": {"kcal": 350}}}]
    hass.states.async_set(PLAN, "x", {"week": week})
    hass.states.async_set(LIBRARY, "x", {"dishes": [stew, dal]})
    p = Planner(hass, setup.runtime_data, PLAN, LIBRARY)
    await p.async_sync()
    book = hass.data["food_diary"]["dishes"]
    assert book.get("r-stew")["unknown"] == ["fat_g", "fibre_g"] and "unknown" not in book.get("r-dal")
    planned = await entries(hass, day(1))
    assert planned["Stew"]["unknown"] == ["fat_g", "fibre_g"] and "unknown" not in planned["Dal"]
    assert planned["Porridge"]["unknown"] == ALL  # the plan's own numbers, calories only
    assert (await call(hass, "get_dish_nutrition", {"dish_id": "r-stew"}))["unknown"] == ["fat_g", "fibre_g"]
    # the recipe's numbers given in full later: the planned entry follows, and knows them
    await call(
        hass, "set_dish_nutrition", {"dish_id": "r-stew", "kcal": 500, "protein_g": 30, "carbs_g": 40, "fat_g": 20, "fibre_g": 5}
    )
    assert "unknown" not in (await entries(hass, day(1)))["Stew"]
    await call(hass, "set_dish_nutrition", {"dish_id": "r-stew", "kcal": 520, "protein_g": 30})
    assert (await entries(hass, day(1)))["Stew"]["unknown"] == ["carbs_g", "fat_g", "fibre_g"]


# ---------- Undo ----------


async def test_snapshot_restore_and_undo_copy_round_trip_unknown(hass: HomeAssistant, setup):
    e = await log(hass, name="Soup", kcal=300, protein_g=12)
    snap = await call(hass, "snapshot", {"token": "t-soup", "entry_id": e["id"]})
    assert snap["entry"]["unknown"] == ["carbs_g", "fat_g", "fibre_g"]
    await call(hass, "update_food", {"entry_id": e["id"], "carbs_g": 30, "fat_g": 9, "fibre_g": 4})
    assert "unknown" not in (await entries(hass, day(0)))["Soup"]
    await call(hass, "restore_snapshot", {"token": "t-soup"})
    assert (await entries(hass, day(0)))["Soup"]["unknown"] == ["carbs_g", "fat_g", "fibre_g"]
    # a copy that replaced a day, undone: the replaced entries come back with their unknowns
    await log(hass, name="Cake", kcal=350, date=day(1))
    r = await call(hass, "copy_day", {"from": day(0), "to": [day(1)], "replace": True})
    assert "Cake" not in await entries(hass, day(1))
    await call(hass, "undo_copy", {"token": r["token"]})
    assert (await entries(hass, day(1)))["Cake"]["unknown"] == ALL


# ---------- judging ----------


def test_an_unknown_protein_is_never_judged_low():
    yours, suggested = {"kcal": 500, "protein_g": 0}, {"kcal": 520, "protein_g": 35}
    assert off(yours, suggested) == "protein_g"
    assert off(yours, suggested, ["protein_g"]) is None
    assert off({"kcal": 200, "protein_g": 0}, suggested, ["protein_g"]) == "kcal"  # calories are still judged
