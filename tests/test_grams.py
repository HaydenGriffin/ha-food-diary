"""Grams for anything (1.10.0): `estimate` kind `per_100` gives one food's numbers per 100 g (or ml) by its name,
`update_food` with `per_100` and `grams` switches an entry to by weight (keeping what it is), a new weight for numbers
of the person's own scales them and keeps them hers, and `restore_food` puts back exactly what an update_food changed."""

from __future__ import annotations

import copy
import math
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.util import dt as dt_util
import pytest
import voluptuous as vol

from .conftest import AI_ANSWERS
from .test_food_diary import B64, call
from .test_planner import KATSU, plan, planner

TASK = "estimate per 100 g"
BREAD = {"kcal_100": 250, "protein_g_100": 9, "carbs_g_100": 41, "fat_g_100": 4, "fibre_g_100": 7, "unit": "g"}
PER_100 = {"kcal": 250, "protein_g": 9, "carbs_g": 41, "fat_g": 4, "fibre_g": 7}
SLICE = {"name": "Multigrain bread", "kcal": 110, "protein_g": 4, "carbs_g": 18, "fat_g": 2, "fibre_g": 3}


def stored(setup, entry_id: str) -> dict[str, Any]:
    """The entry exactly as the diary keeps it."""
    diary = setup.runtime_data.diary
    return copy.deepcopy(next(e for days in diary.data["days"].values() for e in days if e["id"] == entry_id))


async def logged(hass: HomeAssistant, **f: Any) -> dict[str, Any]:
    return (await call(hass, "log_food", {**SLICE, "meal": "breakfast", **f}))["entry"]


async def per_100(hass: HomeAssistant, monkeypatch, answer: dict[str, Any], **f: Any) -> dict[str, Any]:
    monkeypatch.setitem(AI_ANSWERS, TASK, answer)
    return await call(hass, "estimate", {"kind": "per_100", "name": "Multigrain bread", **f})


# ---------- estimate kind per_100 ----------


async def test_per_100_is_one_call_for_the_food_as_named(hass: HomeAssistant, setup, ai_calls, monkeypatch):
    out = await per_100(hass, monkeypatch, BREAD, hint="toasted")
    assert out == {"name": "Multigrain bread", "per_100": PER_100, "unit": "g", "source": "estimate"}
    assert [c["task"] for c in ai_calls] == [TASK]
    assert '"Multigrain bread"' in ai_calls[0]["instructions"] and "They say: toasted" in ai_calls[0]["instructions"]
    assert set(ai_calls[0]["structure"]) == {
        *("kcal_100", "protein_g_100", "carbs_g_100", "fat_g_100", "fibre_g_100", "veg_g_100", "unit")
    }


async def test_per_100_without_a_hint_says_nothing_more(hass: HomeAssistant, setup, ai_calls, monkeypatch):
    await per_100(hass, monkeypatch, BREAD)
    assert "They say" not in ai_calls[0]["instructions"]


@pytest.mark.parametrize(
    "missing",
    [
        {"fibre_g_100": None},
        {"fibre_g_100": ""},
        {"fibre_g_100": "lots"},
        {"fibre_g_100": -1},
        {"fibre_g_100": math.nan},
        {"fibre_g_100": math.inf},
        {"fibre_g_100": 101},
        {"fibre_g_100": True},
        {"fibre_g_100": 10**400},
    ],
)
async def test_a_macro_missing_or_impossible_is_unknown(hass: HomeAssistant, setup, monkeypatch, missing):
    out = await per_100(hass, monkeypatch, {**BREAD, **missing})
    assert out["per_100"] == {**PER_100, "fibre_g": 0.0}
    assert out["unknown"] == ["fibre_g"]


async def test_a_macro_left_out_is_unknown(hass: HomeAssistant, setup, monkeypatch):
    answer = {k: v for k, v in BREAD.items() if k not in ("protein_g_100", "fat_g_100")}
    out = await per_100(hass, monkeypatch, answer)
    assert out["unknown"] == ["protein_g", "fat_g"]
    assert (out["per_100"]["protein_g"], out["per_100"]["fat_g"], out["per_100"]["carbs_g"]) == (0, 0, 41)


@pytest.mark.parametrize("kcal", [None, "", "x", -5, 901, math.nan, math.inf])
async def test_no_real_calories_fails(hass: HomeAssistant, setup, monkeypatch, kcal):
    with pytest.raises(HomeAssistantError, match="couldn't work out Multigrain bread per 100 g"):
        await per_100(hass, monkeypatch, {**BREAD, "kcal_100": kcal})


async def test_water_has_no_calories(hass: HomeAssistant, setup, monkeypatch):
    out = await call_water(hass, monkeypatch)
    assert out["per_100"] == {"kcal": 0, "protein_g": 0, "carbs_g": 0, "fat_g": 0, "fibre_g": 0}
    assert out["unit"] == "ml" and "unknown" not in out


async def call_water(hass: HomeAssistant, monkeypatch) -> dict[str, Any]:
    zero = {k: 0 for k in BREAD if k != "unit"}
    monkeypatch.setitem(AI_ANSWERS, TASK, {**zero, "unit": "ml"})
    return await call(hass, "estimate", {"kind": "per_100", "name": "Water"})


@pytest.mark.parametrize(
    ("said", "unit"), [("ml", "ml"), (" ML ", "ml"), ("millilitres", "ml"), ("g", "g"), ("", "g"), (None, "g")]
)
async def test_a_drink_is_per_100_ml(hass: HomeAssistant, setup, monkeypatch, said, unit):
    out = await per_100(hass, monkeypatch, {**BREAD, "kcal_100": 42, "unit": said}, name="Orange juice")
    assert (out["name"], out["unit"], out["per_100"]["kcal"]) == ("Orange juice", unit, 42)


async def test_per_100_needs_a_name(hass: HomeAssistant, setup, ai_calls):
    with pytest.raises(ServiceValidationError, match="Give the food's name"):
        await call(hass, "estimate", {"kind": "per_100", "name": "  "})
    with pytest.raises(vol.Invalid):
        await call(hass, "estimate", {"kind": "per_100", "name": "x" * 121})
    assert ai_calls == []


# ---------- update_food: switching an entry to by weight ----------


async def test_a_portion_entry_switches_to_grams_keeping_what_it_is(hass: HomeAssistant, setup):
    e = await logged(hass, portions=2, source="text", ref="r-1", note="1 slice, about 40 g", client_id="switch-0001")
    e = (await call(hass, "set_photo", {"entry_id": e["id"], "image": B64}))["entry"]
    was = stored(setup, e["id"])
    out = await call(hass, "update_food", {"entry_id": e["id"], "per_100": PER_100, "grams": 60, "expected_rev": was["rev"]})
    now = out["entry"]
    assert out["before"] == was
    assert (now["per_100"], now["unit"], now["grams"], now["portions"]) == (PER_100, "g", 60, 1.0)
    assert now["per_portion"] == {"kcal": 150, "protein_g": 5.4, "carbs_g": 24.6, "fat_g": 2.4, "fibre_g": 4.2}
    assert (now["kcal"], now["protein_g"], now["fibre_g"]) == (150, 5.4, 4.2)
    kept = ("id", "at", "name", "meal", "source", "ref", "note", "photo", "client_id")
    assert {k: now[k] for k in kept} == {k: was[k] for k in kept}
    assert now["rev"] == was["rev"] + 1 and "edited" not in now and "unknown" not in now
    assert (await call(hass, "get_day"))["totals"]["kcal"] == 150


async def test_switching_keeps_ml_and_passes_edited_through(hass: HomeAssistant, setup):
    e = await logged(hass, name="Smoothie")
    now = (
        await call(hass, "update_food", {"entry_id": e["id"], "per_100": PER_100, "grams": 250, "unit": "ml", "edited": True})
    )["entry"]
    assert (now["unit"], now["grams"], now["kcal"], now["edited"]) == ("ml", 250, 625, True)
    again = (await call(hass, "update_food", {"entry_id": e["id"], "per_100": PER_100, "grams": 100}))["entry"]
    assert (again["unit"], again["kcal"], "edited" in again) == ("g", 250, False)


async def test_an_edited_entry_switched_is_not_edited_unless_said(hass: HomeAssistant, setup):
    e = await logged(hass)
    await call(hass, "update_food", {"entry_id": e["id"], "kcal": 130})
    now = (await call(hass, "update_food", {"entry_id": e["id"], "per_100": PER_100, "grams": 40}))["entry"]
    assert now["kcal"] == 100 and "edited" not in now


async def test_unknown_macros_come_from_the_per_100(hass: HomeAssistant, setup):
    e = await logged(hass)
    half = {"kcal": 250, "protein_g": 9, "carbs_g": 41, "unknown": ["carbs_g"]}  # fat and fibre left out, carbs said
    now = (await call(hass, "update_food", {"entry_id": e["id"], "per_100": half, "grams": 50}))["entry"]
    assert now["unknown"] == ["carbs_g", "fat_g", "fibre_g"]
    assert now["per_100"] == {"kcal": 250, "protein_g": 9, "carbs_g": 0, "fat_g": 0, "fibre_g": 0}  # unknown: 0
    assert (await call(hass, "get_day"))["incomplete"] == {"carbs_g": 1, "fat_g": 1, "fibre_g": 1}
    later = (await call(hass, "update_food", {"entry_id": e["id"], "grams": 100}))["entry"]
    assert later["unknown"] == ["carbs_g", "fat_g", "fibre_g"] and later["kcal"] == 250


async def test_an_entry_with_unknowns_switched_to_full_numbers_knows_them(hass: HomeAssistant, setup):
    e = (await call(hass, "log_food", {"name": "Toast", "kcal": 100}))["entry"]
    assert e["unknown"] == ["protein_g", "carbs_g", "fat_g", "fibre_g"]
    now = (await call(hass, "update_food", {"entry_id": e["id"], "per_100": PER_100, "grams": 40}))["entry"]
    assert "unknown" not in now and now["protein_g"] == 3.6


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"per_100": PER_100}, "per_100 needs grams"),
        ({"per_100": PER_100, "unit": "ml"}, "per_100 needs grams"),
        ({"unit": "ml", "grams": 50}, "unit and edited go with per_100"),
        ({"edited": True}, "unit and edited go with per_100"),
        ({"per_100": PER_100, "grams": 50, "kcal": 100}, "don't send kcal"),
        ({"per_100": PER_100, "grams": 50, "portions": 2}, "don't send portions"),
        ({"per_100": PER_100, "grams": 50, "unknown": ["fat_g"]}, "don't send unknown"),
    ],
)
async def test_a_switch_that_doesnt_add_up_is_refused(hass: HomeAssistant, setup, change, message):
    e = await logged(hass)
    with pytest.raises(ServiceValidationError, match=message):
        await call(hass, "update_food", {"entry_id": e["id"], **change})
    assert stored(setup, e["id"])["rev"] == 1 and "per_100" not in stored(setup, e["id"])


@pytest.mark.parametrize(
    "change",
    [
        {"per_100": {**PER_100, "kcal": math.nan}, "grams": 50},
        {"per_100": {**PER_100, "fat_g": -1}, "grams": 50},
        {"per_100": {**PER_100, "kcal": 20001}, "grams": 50},
        {"per_100": {"protein_g": 9}, "grams": 50},  # no calories
        {"per_100": PER_100, "grams": 0},
        {"per_100": PER_100, "grams": math.inf},
        {"per_100": PER_100, "grams": 50, "unit": "oz"},
        {"per_100": {**PER_100, "unknown": ["kcal"]}, "grams": 50},
    ],
)
async def test_a_switch_takes_real_numbers_only(hass: HomeAssistant, setup, change):
    e = await logged(hass)
    with pytest.raises(vol.Invalid):
        await call(hass, "update_food", {"entry_id": e["id"], **change})


async def test_a_stale_switch_is_a_conflict(hass: HomeAssistant, setup):
    e = await logged(hass)
    await call(hass, "update_food", {"entry_id": e["id"], "name": "Rye bread"})
    with pytest.raises(ServiceValidationError) as err:
        await call(hass, "update_food", {"entry_id": e["id"], "per_100": PER_100, "grams": 50, "expected_rev": 1})
    assert err.value.translation_key == "conflict"
    assert "per_100" not in stored(setup, e["id"])


# ---------- update_food: a new weight ----------


async def test_a_new_weight_scales_her_own_numbers_and_keeps_them_hers(hass: HomeAssistant, setup):
    e = await logged(hass, per_100=PER_100, grams=100)
    fixed = (await call(hass, "update_food", {"entry_id": e["id"], "kcal": 300, "protein_g": 12}))["entry"]
    assert fixed["edited"] is True
    now = (await call(hass, "update_food", {"entry_id": e["id"], "grams": 150}))["entry"]
    assert (now["kcal"], now["protein_g"], now["carbs_g"], now["fibre_g"]) == (450, 18, 61.5, 10.5)
    assert now["edited"] is True and now["grams"] == 150 and now["per_100"] == PER_100
    back = (await call(hass, "update_food", {"entry_id": e["id"], "grams": 100}))["entry"]
    assert (back["kcal"], back["protein_g"]) == (300, 12)


async def test_a_new_weight_on_numbers_not_hers_follows_the_per_100(hass: HomeAssistant, setup):
    e = await logged(hass, per_100=PER_100, grams=100)
    now = (await call(hass, "update_food", {"entry_id": e["id"], "grams": 40}))["entry"]
    assert now["kcal"] == 100 and "edited" not in now


async def test_a_new_weight_counts_the_portions_her_numbers_were_for(hass: HomeAssistant, setup):
    e = await logged(hass, per_100=PER_100, grams=100)
    await call(hass, "update_food", {"entry_id": e["id"], "portions": 2})  # 200 g in all: 500 kcal
    await call(hass, "update_food", {"entry_id": e["id"], "kcal": 600})  # hers, for those 200 g
    now = (await call(hass, "update_food", {"entry_id": e["id"], "grams": 100}))["entry"]
    assert (now["kcal"], now["portions"], now["edited"]) == (300, 1.0, True)


async def test_unknown_macros_stay_unknown_when_her_numbers_scale(hass: HomeAssistant, setup):
    e = await logged(hass, per_100={"kcal": 250, "protein_g": 9}, grams=100)
    await call(hass, "update_food", {"entry_id": e["id"], "kcal": 280})
    now = (await call(hass, "update_food", {"entry_id": e["id"], "grams": 50}))["entry"]
    assert now["unknown"] == ["carbs_g", "fat_g", "fibre_g"]
    assert (now["kcal"], now["protein_g"], now["fat_g"], now["edited"]) == (140, 4.5, 0, True)


# ---------- restore_food: Undo of an update_food ----------


async def undo(hass: HomeAssistant, out: dict[str, Any], **extra: Any) -> dict[str, Any]:
    e = out["entry"]
    return (await call(hass, "restore_food", {"entry_id": e["id"], "entry": out["before"], "expected_rev": e["rev"], **extra}))[
        "entry"
    ]


async def test_undo_of_a_switch_puts_the_portion_entry_back_exactly(hass: HomeAssistant, setup):
    e = await logged(hass, portions=1.5, source="text", ref="r-1", client_id="switch-0002")
    e = (await call(hass, "set_photo", {"entry_id": e["id"], "image": B64}))["entry"]
    was = stored(setup, e["id"])
    assert "unknown" not in was  # all five known: the key isn't there, and isn't after Undo either
    out = await call(hass, "update_food", {"entry_id": e["id"], "per_100": {"kcal": 250}, "grams": 60, "edited": True})
    back = await undo(hass, out)
    assert back == {**was, "rev": was["rev"] + 2}
    assert stored(setup, e["id"]) == back
    assert (await call(hass, "get_day"))["totals"]["kcal"] == 165


async def test_undo_of_a_new_weight_puts_her_numbers_back(hass: HomeAssistant, setup):
    e = await logged(hass, per_100={"kcal": 250, "protein_g": 9}, grams=100, unit="ml")
    await call(hass, "update_food", {"entry_id": e["id"], "kcal": 280})
    was = stored(setup, e["id"])
    out = await call(hass, "update_food", {"entry_id": e["id"], "grams": 50})
    assert await undo(hass, out) == {**was, "rev": was["rev"] + 2}


async def test_undo_of_any_change_is_exact(hass: HomeAssistant, setup):
    e = (await call(hass, "log_food", {"name": "Toast", "kcal": 100}))["entry"]  # calories only: all four unknown
    was = stored(setup, e["id"])
    change = {"name": "Rye", "meal": "lunch", "checked": True, "portions": 2, "protein_g": 9, "unknown": ["fat_g"]}
    out = await call(hass, "update_food", {"entry_id": e["id"], **change})
    assert out["entry"]["checked"] is True and out["entry"]["edited"] is True
    assert await undo(hass, out) == {**was, "rev": was["rev"] + 2}


async def test_undo_after_a_later_change_is_a_conflict(hass: HomeAssistant, setup):
    e = await logged(hass)
    out = await call(hass, "update_food", {"entry_id": e["id"], "per_100": PER_100, "grams": 60})
    made = out["entry"]["rev"]  # the revision the switch made (the answer's entry is read now, as an app would)
    await call(hass, "update_food", {"entry_id": e["id"], "grams": 80})
    later = stored(setup, e["id"])
    with pytest.raises(ServiceValidationError) as err:
        await call(hass, "restore_food", {"entry_id": e["id"], "entry": out["before"], "expected_rev": made})
    assert err.value.translation_key == "conflict"
    assert stored(setup, e["id"]) == later and later["grams"] == 80
    with pytest.raises(vol.Invalid):  # expected_rev is needed
        await call(hass, "restore_food", {"entry_id": e["id"], "entry": out["before"]})


async def test_undo_refuses_another_entry_or_one_gone(hass: HomeAssistant, setup):
    e = await logged(hass)
    out = await call(hass, "update_food", {"entry_id": e["id"], "grams": 1, "per_100": PER_100})
    other = await logged(hass, name="Tea")
    with pytest.raises(ServiceValidationError, match="another one"):
        await call(hass, "restore_food", {"entry_id": other["id"], "entry": out["before"], "expected_rev": 1})
    with pytest.raises(ServiceValidationError, match="per_100 and grams together"):
        await call(
            hass,
            "restore_food",
            {"entry_id": e["id"], "entry": {**out["before"], "per_100": PER_100}, "expected_rev": out["entry"]["rev"]},
        )
    await call(hass, "delete_food", {"entry_id": e["id"]})
    with pytest.raises(ServiceValidationError, match="isn't in the diary"):
        await undo(hass, out)


@pytest.mark.parametrize(
    "bad",
    [
        {"kcal": math.nan},
        {"kcal": -1},
        {"per_portion": {"kcal": 1}},
        {"meal": "brunch"},
        {"name": ""},
        {"portions": 0},
        {"unknown": ["kcal"]},
        {"unit": "oz"},
    ],
)
async def test_undo_takes_a_real_entry_only(hass: HomeAssistant, setup, bad):
    e = await logged(hass)
    out = await call(hass, "update_food", {"entry_id": e["id"], "name": "Rye"})
    with pytest.raises(vol.Invalid):
        await call(hass, "restore_food", {"entry_id": e["id"], "entry": {**out["before"], **bad}, "expected_rev": 2})
    assert stored(setup, e["id"])["name"] == "Rye"


async def test_undo_keeps_what_update_food_cant_change(hass: HomeAssistant, setup):
    """Only what an update changes is taken from `entry`: a photo, source or time sent along is not."""
    e = await logged(hass, source="text")
    out = await call(hass, "update_food", {"entry_id": e["id"], "name": "Rye"})
    sent = {**out["before"], "photo": "../../secrets.yaml", "source": "label", "at": "2000-01-01T00:00", "client_id": "x"}
    back = (await call(hass, "restore_food", {"entry_id": e["id"], "entry": sent, "expected_rev": 2}))["entry"]
    assert back == {**out["before"], "rev": 3}


# ---------- review fixes (1.10.0) ----------


async def test_a_recipes_new_numbers_leave_an_entry_by_weight_alone(hass: HomeAssistant, setup):
    """Diary.follow sets numbers per portion: an entry by weight keeps per_100 × grams."""
    e = await logged(hass, source="dish", ref="ig-1")
    await call(hass, "update_food", {"entry_id": e["id"], "per_100": {"kcal": 200}, "grams": 150})
    out = await call(hass, "set_dish_nutrition", {"dish_id": "ig-1", "kcal": 500, "protein_g": 30})
    now = stored(setup, e["id"])
    assert out["refreshed"] == 0
    assert (now["kcal"], now["grams"], now["per_portion"]["kcal"]) == (300, 150, 300)


async def test_the_planner_leaves_a_planned_entry_by_weight_alone(hass: HomeAssistant, setup, ai_calls):
    """Planner._refresh renumbers per portion: a planned entry switched to grams keeps per_100 × grams."""
    d = dt_util.now().date().isoformat()
    p = planner(hass, setup, [{"date": d, "dinner": "Chicken katsu curry"}], [KATSU])
    await p.async_sync()
    (e,) = setup.runtime_data.diary.entries(d)
    await call(hass, "update_food", {"entry_id": e["id"], "per_100": {"kcal": 200}, "grams": 150})
    plan(hass, [{"date": d, "dinner": "Chicken katsu curry"}], [{**KATSU, "nutrition": {**KATSU["nutrition"], "kcal": 500}}])
    await p.async_sync()
    now = stored(setup, e["id"])
    assert (now["kcal"], now["grams"], now["per_portion"]["kcal"]) == (300, 150, 300)
    assert setup.runtime_data.diary.renumber(d, e["id"], {"kcal": 500}, "r1", "") is None


async def test_her_numbers_without_a_weight_cant_follow_a_new_one(hass: HomeAssistant, setup):
    """An entry by weight from before 1.10.0 with 0 g: her corrected numbers are refused a new weight, never dropped."""
    e = setup.runtime_data.diary.add(dt_util.now().date().isoformat(), {**SLICE, "per_100": PER_100, "grams": 0})
    await call(hass, "update_food", {"entry_id": e["id"], "kcal": 130})
    with pytest.raises(ServiceValidationError, match="aren't for a weight"):
        await call(hass, "update_food", {"entry_id": e["id"], "grams": 50})
    now = stored(setup, e["id"])
    assert (now["kcal"], now["edited"], now["grams"]) == (130, True, 0)


async def test_log_food_needs_a_weight_with_per_100(hass: HomeAssistant, setup):
    with pytest.raises(ServiceValidationError, match="per_100 needs grams"):
        await call(hass, "log_food", {**SLICE, "per_100": PER_100, "grams": 0})
    with pytest.raises(ServiceValidationError, match="per_100 needs grams"):
        await call(hass, "log_food", {**SLICE, "per_100": PER_100})
    assert (await call(hass, "get_day"))["entries"] == []


async def test_an_estimate_per_100_logs_as_it_came(hass: HomeAssistant, setup, monkeypatch):
    """The estimate's source is one log_food takes, and its unknowns come along nested or at the top."""
    est = await per_100(hass, monkeypatch, {**BREAD, "fibre_g_100": None})
    for food in (
        {"per_100": {**est["per_100"], "unknown": est["unknown"]}},
        {"per_100": est["per_100"], "unknown": est["unknown"]},
    ):
        e = (
            await call(hass, "log_food", {"name": est["name"], "source": est["source"], "grams": 60, "unit": est["unit"], **food})
        )["entry"]
        assert (e["source"], e["kcal"], e["unknown"], e["grams"]) == ("estimate", 150, ["fibre_g"], 60)


async def test_water_is_logged_by_its_volume(hass: HomeAssistant, setup, monkeypatch):
    water = await call_water(hass, monkeypatch)
    e = (await call(hass, "log_food", {"name": "Water", "per_100": water["per_100"], "grams": 250, "unit": "ml"}))["entry"]
    assert (e["kcal"], e["grams"], e["unit"], e["per_100"]["kcal"], "unknown" in e) == (0, 250, "ml", 0, False)


async def test_log_food_per_100_needs_calories(hass: HomeAssistant, setup):
    with pytest.raises(vol.Invalid):
        await call(hass, "log_food", {"name": "Bread", "per_100": {"protein_g": 9}, "grams": 50})


async def test_numbers_too_big_for_one_entry_are_refused_and_every_entry_restores(hass: HomeAssistant, setup):
    e = await logged(hass, per_100=PER_100, grams=0.1)
    await call(hass, "update_food", {"entry_id": e["id"], "kcal": 20000})  # hers, for 0.1 g
    with pytest.raises(ServiceValidationError, match="too big"):
        await call(hass, "update_food", {"entry_id": e["id"], "grams": 20000})  # 4,000,000,000 kcal
    assert stored(setup, e["id"])["kcal"] == 20000 and stored(setup, e["id"])["grams"] == 0.1
    out = await call(hass, "update_food", {"entry_id": e["id"], "grams": 4000})  # 800,000,000 kcal: the most is 10^9
    assert out["entry"]["kcal"] == 800_000_000
    rev = out["entry"]["rev"]
    big = await call(hass, "update_food", {"entry_id": e["id"], "name": "Rye"})
    assert big["before"]["kcal"] == 800_000_000
    back = (await call(hass, "restore_food", {"entry_id": e["id"], "entry": big["before"], "expected_rev": rev + 1}))["entry"]
    assert back == {**big["before"], "rev": rev + 2}
