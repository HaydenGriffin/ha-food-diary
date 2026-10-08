"""Revisions, and AI work that never overwrites something newer (A-3).

Every entry has `rev` (1 when made, one more per change), changes can say which rev they expect, and work that waits on the
AI (the planner, recipe estimates, completing partial numbers) keeps its answer only when what it started from is unchanged.
The first two tests are the external audit's reproductions: a planned estimate landing on a meal logged meanwhile (300 kcal
became 600), and an older estimate replacing a newer correction (450 kcal became 300).
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.util import dt as dt_util
import pytest

from custom_components.food_diary.planner import PLAN_NOTE

from .test_food_diary import B64, call
from .test_planner import CHILLI, planner


def day(n: int) -> str:
    return (dt_util.now().date() + timedelta(days=n)).isoformat()


async def kcal_today(hass: HomeAssistant) -> float:
    return (await call(hass, "get_day"))["totals"]["kcal"]


# ---------- the audit's reproductions ----------


async def test_meal_logged_while_the_planner_estimates_is_not_doubled(hass: HomeAssistant, setup, held_ai):
    """Audit: a manually logged 300 kcal meal became 600 kcal when an in-flight planner estimate completed."""
    held_ai.answers["estimate a meal from words"] = {**held_ai.answers["estimate a meal from words"], "kcal": 300}
    p = planner(hass, setup, [{"date": day(0), "dinner": "Soup"}])
    sync = hass.async_create_task(p.async_sync())
    await held_ai.asked.wait()  # the planner is waiting on the AI for tonight's soup
    await call(hass, "log_food", {"name": "Soup", "kcal": 300, "meal": "dinner"})
    held_ai.gate.set()
    changed = await sync
    assert await kcal_today(hass) == 300
    assert changed == 0
    assert [e["source"] for e in (await call(hass, "get_day"))["entries"]] == ["manual"]


async def test_newer_correction_beats_an_older_estimate(hass: HomeAssistant, setup, held_ai):
    """Audit: a newer correction of 450 kcal was replaced by an older 300 kcal AI estimate."""
    held_ai.answers["estimate a dish"] = {**held_ai.answers["estimate a dish"], "kcal": 300}
    p = planner(hass, setup, [{"date": day(0), "dinner": "Chilli con carne"}], [CHILLI])
    planned = setup.runtime_data.diary.add(
        day(0),
        {
            "name": "Chilli con carne",
            "meal": "dinner",
            "source": "plan",
            "ref": "ig-1",
            "plan_key": f"{day(0)}|dinner",
            "note": PLAN_NOTE,
            "kcal": 500,
        },
    )
    sync = hass.async_create_task(p.async_sync())  # no book numbers for ig-1 yet: it asks the AI
    await held_ai.asked.wait()
    await call(hass, "update_food", {"entry_id": planned["id"], "kcal": 450})
    held_ai.gate.set()
    await sync
    e = (await call(hass, "get_day"))["entries"][0]
    assert e["kcal"] == 450
    assert (e["edited"], e["rev"]) == (True, 2)


# ---------- the recipe book ----------


async def test_numbers_typed_while_a_dish_is_estimated_win(hass: HomeAssistant, setup, held_ai):
    estimate = hass.async_create_task(
        call(hass, "estimate", {"kind": "dish", "dish_id": "ig-9", "dish_name": "Stew", "ingredients": ["500 g beef"]})
    )
    await held_ai.asked.wait()
    await call(hass, "set_dish_nutrition", {"dish_id": "ig-9", "kcal": 450, "protein_g": 30})
    held_ai.gate.set()
    out = await estimate
    assert (out["kcal"], out["nutrition_source"]) == (450, "own")
    book = await call(hass, "get_dish_nutrition", {"dish_id": "ig-9"})
    assert (book["kcal"], book["source"]) == (450, "own")


async def test_a_fresh_estimate_is_kept_when_nothing_changed(hass: HomeAssistant, setup, ai_calls):
    await call(hass, "set_dish_nutrition", {"dish_id": "ig-9", "kcal": 450, "source": "ai"})
    out = await call(
        hass, "estimate", {"kind": "dish", "dish_id": "ig-9", "dish_name": "Stew", "ingredients": ["beef"], "fresh": True}
    )
    assert out["kcal"] == 425
    assert (await call(hass, "get_dish_nutrition", {"dish_id": "ig-9"}))["kcal"] == 425


async def test_completing_partial_numbers_never_overwrites_typed_ones(hass: HomeAssistant, setup, held_ai):
    partial_dish = {**CHILLI, "nutrition": {"kcal": 470, "protein_g": 55}}  # carbs and fat missing
    p = planner(hass, setup, [], [partial_dish])
    sync = hass.async_create_task(p.async_sync())
    await held_ai.asked.wait()  # completing ig-1's numbers from an estimate of the dish
    await call(hass, "set_dish_nutrition", {"dish_id": "ig-1", "kcal": 520, "protein_g": 40, "carbs_g": 50, "fat_g": 18})
    held_ai.gate.set()
    await sync
    book = await call(hass, "get_dish_nutrition", {"dish_id": "ig-1"})
    assert (book["kcal"], book["carbs_g"], book["source"]) == (520, 50, "own") and "completed" not in book
    assert p.completed == []


# ---------- revisions ----------


async def test_every_change_bumps_rev(hass: HomeAssistant, setup):
    e = (await call(hass, "log_food", {"name": "Chilli", "kcal": 400, "ref": "ig-1", "source": "dish"}))["entry"]
    assert e["rev"] == 1
    assert (await call(hass, "update_food", {"entry_id": e["id"], "portions": 2}))["entry"]["rev"] == 2
    assert (await call(hass, "set_photo", {"entry_id": e["id"], "image": B64}))["entry"]["rev"] == 3
    await call(hass, "set_dish_nutrition", {"dish_id": "ig-1", "kcal": 380})  # the recipe's new numbers follow
    now = (await call(hass, "get_day"))["entries"][0]
    assert (now["kcal"], now["rev"]) == (760, 4)


async def test_stale_expected_rev_is_a_conflict(hass: HomeAssistant, setup):
    e = (await call(hass, "log_food", {"name": "Toast", "kcal": 200}))["entry"]
    await call(hass, "update_food", {"entry_id": e["id"], "kcal": 250, "expected_rev": 1})
    for service, extra in (("update_food", {"kcal": 100}), ("delete_food", {}), ("set_photo", {"image": B64})):
        with pytest.raises(ServiceValidationError) as err:
            await call(hass, service, {"entry_id": e["id"], "expected_rev": 1, **extra})
        assert err.value.translation_key == "conflict"
    now = (await call(hass, "get_day"))["entries"]
    assert [(x["kcal"], x["rev"], "photo" in x) for x in now] == [(250, 2, False)]
    await call(hass, "delete_food", {"entry_id": e["id"], "expected_rev": 2})
    assert (await call(hass, "get_day"))["entries"] == []


async def test_entries_from_before_revisions_count_as_rev_1(hass: HomeAssistant, hass_storage, setup):
    diary = setup.runtime_data.diary
    diary.data["days"][day(0)] = [{"id": "old1", "name": "Tea", "meal": "snack", "portions": 1, "per_portion": {"kcal": 30}}]
    diary._recount(diary.data["days"][day(0)][0])
    await diary.async_flush()
    await hass.config_entries.async_reload(setup.entry_id)
    await hass.async_block_till_done()
    assert (await call(hass, "get_day"))["entries"][0]["rev"] == 1
    out = await call(hass, "update_food", {"entry_id": "old1", "kcal": 40, "expected_rev": 1})
    assert out["entry"]["rev"] == 2


async def test_planner_still_renumbers_untouched_entries(hass: HomeAssistant, setup, ai_calls):
    """The guard only drops work whose slot changed: an unchanged planned entry still takes the book's new numbers."""
    p = planner(hass, setup, [{"date": day(1), "dinner": "Chilli con carne"}], [CHILLI])
    await p.async_sync()
    e = next(x for x in (await call(hass, "get_day", {"date": day(1)}))["entries"])
    assert (e["kcal"], e["rev"]) == (425, 1)
    hass.data["food_diary"]["dishes"].set("ig-1", {"kcal": 500, "source": "recipe"})
    assert await p.async_sync() == 1
    e = next(x for x in (await call(hass, "get_day", {"date": day(1)}))["entries"])
    assert (e["kcal"], e["rev"]) == (500, 2)


async def test_two_syncs_and_a_log_settle(hass: HomeAssistant, setup, held_ai):
    """Logging while a sync waits, then syncing again: the hand-logged meal stays the only one."""
    p = planner(hass, setup, [{"date": day(0), "dinner": "Soup"}])
    sync = hass.async_create_task(p.async_sync())
    await held_ai.asked.wait()
    await call(hass, "log_food", {"name": "Soup", "kcal": 280, "meal": "dinner"})
    held_ai.gate.set()
    await sync
    await asyncio.wait_for(p.async_sync(), 5)
    assert await kcal_today(hass) == 280
