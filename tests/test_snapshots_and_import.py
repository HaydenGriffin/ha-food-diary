"""An exact Undo for changes made with other services (snapshot / restore_snapshot), the "import" source and renaming a
source in stored data, and last-logged times stored without an offset."""

from __future__ import annotations

import copy
from datetime import timedelta

from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import ServiceValidationError, Unauthorized
from homeassistant.util import dt as dt_util
import pytest
import voluptuous as vol

from custom_components.food_diary.checks import TRUSTED
from custom_components.food_diary.const import DOMAIN

from .conftest import ALEX
from .test_food_diary import KCAL, call

OLD = {"kcal": 395, "protein_g": 28, "carbs_g": 56, "fat_g": 7, "fibre_g": 4}
NEW = {"kcal": 310, "protein_g": 19, "carbs_g": 50, "fat_g": 4, "fibre_g": 4}


def stored(setup):
    return copy.deepcopy(setup.runtime_data.diary.data["days"])


def without_rev(days):
    """The entries without `rev`: an exact Undo puts everything else back, and is a change itself (rev goes on up)."""
    return {d: [{k: v for k, v in e.items() if k != "rev"} for e in es] for d, es in days.items()}


def revs(days):
    return [e["rev"] for es in days.values() for e in es]


# ---------- snapshot / restore_snapshot ----------


async def test_an_edit_is_undone_exactly(hass: HomeAssistant, setup):
    e = (
        await call(
            hass, "log_food", {"name": "Pasta bake", "meal": "dinner", **OLD, "source": "dish", "ref": "r-pasta", "portions": 1.5}
        )
    )["entry"]
    before = stored(setup)
    snap = await call(hass, "snapshot", {"token": "t-1234", "entry_id": e["id"]})
    assert snap["entry"]["portions"] == 1.5 and snap["entry"]["per_portion"]["kcal"] == 395 and not snap["entry"]["edited"]
    await call(hass, "update_food", {"entry_id": e["id"], **{k: v * 1.5 for k, v in NEW.items()}}, response=False)
    assert setup.runtime_data.diary.entries(snap["entry"]["date"])[0]["edited"]
    assert await call(hass, "restore_snapshot", {"token": "t-1234"}) == {"ok": True, "entries": 1, "book": False}
    after = stored(setup)
    assert without_rev(after) == without_rev(before)  # numbers back, edited flag gone, same place
    assert revs(before) == [1] and revs(after) == [3]  # 1 logged, 2 the edit, 3 the Undo
    assert float(hass.states.get(KCAL).state) == 592.5
    with pytest.raises(ServiceValidationError, match="can't be undone any more"):  # once per token; apps show this text
        await call(hass, "restore_snapshot", {"token": "t-1234"})


async def test_a_recipe_change_is_undone_with_its_book_item_and_followers(hass: HomeAssistant, setup):
    book = hass.data[DOMAIN]["dishes"]
    await call(hass, "set_dish_nutrition", {"dish_id": "r-pasta", **OLD, "source": "recipe", "portions": 2}, response=False)
    book.patch("r-pasta", {"estimate": {"kcal": 785, "of": "abc"}, "check": {"kcal": 400}})
    item = copy.deepcopy(book.get("r-pasta"))
    e = (await call(hass, "log_food", {"name": "Pasta bake", "meal": "dinner", **OLD, "source": "dish", "ref": "r-pasta"}))[
        "entry"
    ]
    before = stored(setup)
    snap = await call(hass, "snapshot", {"token": "t-5678", "dish_id": "r-pasta", "entry_id": e["id"]})
    assert snap["entries"] == 1 and snap["dish"]["portions"] == 2 and "estimate" not in snap["dish"]  # internal notes hidden
    r = await call(hass, "set_dish_nutrition", {"dish_id": "r-pasta", **NEW, "source": "own", "portions": 3})
    assert r["refreshed"] == 1 and "check" not in book.get("r-pasta")
    await call(hass, "restore_snapshot", {"token": "t-5678"}, response=False)
    assert book.get("r-pasta") == item and without_rev(stored(setup)) == without_rev(before)
    assert revs(stored(setup)) == [3]  # 1 logged, 2 followed the new numbers, 3 the Undo


async def test_a_deleted_entry_comes_back_and_a_new_recipe_goes(hass: HomeAssistant, setup):
    book = hass.data[DOMAIN]["dishes"]
    e = (await call(hass, "log_food", {"name": "Soup", "meal": "lunch", "kcal": 200}))["entry"]
    await call(hass, "snapshot", {"token": "t-gone", "entry_id": e["id"], "dish_id": "r-new"})
    await call(hass, "delete_food", {"entry_id": e["id"]}, response=False)
    await call(hass, "set_dish_nutrition", {"dish_id": "r-new", **NEW, "source": "own"}, response=False)
    await call(hass, "restore_snapshot", {"token": "t-gone"}, response=False)
    assert [x["name"] for x in (await call(hass, "get_day"))["entries"]] == ["Soup"]
    assert book.get("r-new") is None  # it had no numbers: none again


async def test_snapshot_refuses_bad_input(hass: HomeAssistant, setup):
    with pytest.raises(ServiceValidationError):
        await call(hass, "snapshot", {"token": "t-xxxx", "entry_id": "nope"})
    with pytest.raises(vol.Invalid):  # tokens are short plain words
        await call(hass, "snapshot", {"token": "a b"})
    for n in range(12):  # the last ten are kept
        await call(hass, "snapshot", {"token": f"t-{n:04}"})
    with pytest.raises(ServiceValidationError):
        await call(hass, "restore_snapshot", {"token": "t-0000"})
    assert (await call(hass, "restore_snapshot", {"token": "t-0011"}))["ok"]


# ---------- the import source ----------


async def test_import_is_a_source_and_is_trusted(hass: HomeAssistant, setup):
    e = (await call(hass, "log_food", {"name": "Oat bar", "meal": "snack", "kcal": 180, "source": "import"}))["entry"]
    assert e["source"] == "import"
    r = await call(hass, "set_dish_nutrition", {"dish_id": "r-1", "kcal": 500, "source": "import"})
    assert r["source"] == "import" and "import" in TRUSTED


async def test_renaming_a_source_in_stored_data(hass: HomeAssistant, setup, hass_admin_user, hass_read_only_user):
    diary, book = setup.runtime_data.diary, hass.data[DOMAIN]["dishes"]
    d = dt_util.now().date().isoformat()
    diary.data["days"][d] = [
        {"id": "a", "name": "Old app dinner", "meal": "dinner", "kcal": 500, "source": "oldapp", "rev": 1},
        {"id": "b", "name": "Toast", "meal": "breakfast", "kcal": 200, "source": "manual", "rev": 1},
    ]
    book.items.update({"r-1": {"kcal": 500, "source": "oldapp"}, "r-2": {"kcal": 300, "source": "own"}})
    data = {"from": "oldapp", "to": "import"}
    with pytest.raises(Unauthorized):
        await hass.services.async_call(
            DOMAIN, "rename_source", data, blocking=True, context=Context(user_id=hass_read_only_user.id)
        )
    r = await hass.services.async_call(
        DOMAIN, "rename_source", data, blocking=True, return_response=True, context=Context(user_id=hass_admin_user.id)
    )
    assert r == {"entries": {"person.alex": 1}, "recipes": 1}
    assert [e["source"] for e in diary.data["days"][d]] == ["import", "manual"]
    assert book.get("r-1")["source"] == "import" and book.get("r-2")["source"] == "own"
    again = await hass.services.async_call(
        DOMAIN, "rename_source", data, blocking=True, return_response=True, context=Context(user_id=hass_admin_user.id)
    )
    assert again == {"entries": {"person.alex": 0}, "recipes": 0}  # done once; repeating is harmless


# ---------- last logged ----------


async def test_last_logged_reads_times_without_an_offset_as_local(hass: HomeAssistant, setup):
    await hass.config.async_set_time_zone("Europe/Paris")
    diary = setup.runtime_data.diary
    d = dt_util.now().date().isoformat()
    diary.data["days"][d] = [{"id": "x", "name": "Tea", "meal": "snack", "kcal": 20, "at": f"{d}T09:30:00", "rev": 1}]
    diary.async_refresh()
    await hass.async_block_till_done()
    state = hass.states.get("sensor.alex_food_diary_last_logged")
    at = dt_util.parse_datetime(state.state)
    assert at is not None and dt_util.as_local(at).strftime("%H:%M") == "09:30"


async def test_last_logged_ignores_the_meal_plan_ahead(hass: HomeAssistant, setup):
    """Seen live: planned meals for the days ahead (added by the planner yesterday) kept the sensor on yesterday's time,
    while food logged today, by words, again and from a photo, didn't move it."""
    diary = setup.runtime_data.diary
    today = dt_util.now().date()
    yesterday, ahead = (today - timedelta(days=1)).isoformat(), (today + timedelta(days=2)).isoformat()
    planned = {"name": "Chilli", "meal": "dinner", "kcal": 600, "source": "plan", "plan_key": f"{ahead}|dinner"}
    diary.data["days"][ahead] = [{**planned, "id": "p1", "at": f"{yesterday}T08:15+01:00", "rev": 1}]
    diary.data["days"][today.isoformat()] = [
        {**planned, "id": "p0", "at": f"{yesterday}T08:15+01:00", "plan_key": f"{today}|dinner"},
        *(
            {"id": f"e{i}", "name": n, "meal": "lunch", "kcal": 100, "source": src, "at": f"{today}T10:5{i}+01:00", "rev": 1}
            for i, (n, src) in enumerate((("Soup", "text"), ("Bread", "text"), ("Tea", "again"), ("Salad", "photo")))
        ),
    ]
    diary.async_refresh()
    await hass.async_block_till_done()
    state = hass.states.get("sensor.alex_food_diary_last_logged")
    assert dt_util.parse_datetime(state.state) == dt_util.parse_datetime(f"{today}T10:53+01:00")
    assert state.attributes["name"] == "Salad"


async def test_a_user_context(hass: HomeAssistant, setup):
    """The fixtures' person is linked to a user, so the diary services above resolved Alex's diary."""
    assert hass.states.get("person.alex").attributes["user_id"] == ALEX
