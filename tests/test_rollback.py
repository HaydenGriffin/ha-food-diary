"""Rolling back is safe: a diary written by this release (entries with `rev` and `client_id`, the client_id ledger) loads
in release 1.0.0's storage layer (tests/v1, a frozen copy) without losing an entry, and everything 1.0.0 does with it
still works. The store version is unchanged, so 1.0.0 doesn't refuse or migrate the file.

What a rollback gives up: 1.0.0 doesn't know the ledger, so its first save drops it (the entries keep their client_id), and
its edits don't bump `rev`. Rolling forward again loads that file as usual."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from custom_components.food_diary.diary import STORE_VERSION, Diary

from .test_food_diary import call
from .v1.diary import STORE_VERSION as V1_STORE_VERSION, Diary as V1Diary

KEY = "food_diary.alex"
CID = "0d7c6a52-1b44-4bb5-8a6e-6c2f0f3a9e10"


def day(n: int) -> str:
    return (dt_util.now().date() + timedelta(days=n)).isoformat()


async def written_by_this_release(hass: HomeAssistant, setup) -> dict:
    """A diary with everything this release adds: client_ids, revs past 1, the ledger (with a deleted entry's line), copies."""
    await call(hass, "log_food", {"name": "Granola", "per_100": {"kcal": 100}, "grams": 200, "meal": "breakfast"})
    e = (await call(hass, "log_food", {"name": "Tea", "kcal": 30, "meal": "snack", "client_id": CID}))["entry"]
    await call(hass, "update_food", {"entry_id": e["id"], "kcal": 35, "expected_rev": 1})
    gone = (await call(hass, "log_food", {"name": "Cake", "kcal": 400, "client_id": "cake-00000001"}))["entry"]
    await call(hass, "delete_food", {"entry_id": gone["id"]})
    await call(hass, "copy_day", {"from": day(0), "to": [day(1)], "client_id": "copy-00000001"})
    await setup.runtime_data.diary.async_flush()
    return setup.runtime_data.diary.data


async def test_v1_loads_and_uses_a_new_diary(hass: HomeAssistant, setup, hass_storage):
    new = await written_by_this_release(hass, setup)
    stored = hass_storage[KEY]
    assert stored["version"] == STORE_VERSION == V1_STORE_VERSION
    assert set(stored["data"]["client_ids"]) == {CID, "cake-00000001", "copy-00000001"}
    tea = next(e for e in stored["data"]["days"][day(0)] if e["name"] == "Tea")
    assert (tea["rev"], tea["client_id"]) == (2, CID)

    old = V1Diary(hass, "alex")
    await old.async_load()
    for d in (day(0), day(1)):  # every entry, as it was
        assert old.entries(d) == new["days"][d]
        assert old.day(d)["totals"] == {"kcal": 235.0, "protein_g": 0.0, "carbs_g": 0.0, "fat_g": 0.0, "fibre_g": 0.0}
    assert old.goals == new["goals"]

    # everything 1.0.0 does still works on these entries
    old.recent()
    old.usuals()
    old.review(day(1))
    old.history(day(1), 7)
    assert old.update(day(0), tea["id"], {"kcal": 40})["kcal"] == 40
    copy = old.copy(day(0), [day(2)])
    assert len(copy["added"][day(2)]) == 2 and old.day(day(2))["totals"]["kcal"] == 240
    assert old.uncopy(copy["token"])
    assert old.delete(day(1), old.entries(day(1))[0]["id"])
    await old.async_flush()

    # 1.0.0 saved it: nothing lost but the ledger, and this release loads it again
    saved = hass_storage[KEY]["data"]
    assert "client_ids" not in saved and len(saved["days"][day(0)]) == 2 and len(saved["days"][day(1)]) == 1
    again = Diary(hass, "alex")
    await again.async_load()
    assert {e["name"]: e["rev"] for e in again.entries(day(0))} == {"Granola": 1, "Tea": 2}
    assert again.client_ids == {}


async def test_v1_books_are_untouched(hass: HomeAssistant, setup, hass_storage, ai_calls):
    """The recipe and product books gain no new fields (their 'version' is worked out, not stored)."""
    await call(hass, "estimate", {"kind": "dish", "dish_id": "ig-9", "dish_name": "Stew", "ingredients": ["beef"]})
    await call(hass, "set_dish_nutrition", {"dish_id": "ig-8", "kcal": 450})
    await hass.async_block_till_done()
    book = hass.data["food_diary"]["dishes"]
    await book.store.async_save(book.items)
    assert {k for item in hass_storage["food_diary.dishes"]["data"].values() for k in item} <= {
        "kcal",
        "protein_g",
        "carbs_g",
        "fat_g",
        "fibre_g",
        "source",
        "at",
    }
