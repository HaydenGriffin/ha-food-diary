"""Fixing an entry in place: macros added to a kcal-only food keep its calories, and `checked` marks an entry as confirmed."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant

from .test_food_diary import KCAL, call


async def log(hass: HomeAssistant, **f: Any) -> dict[str, Any]:
    return (await call(hass, "log_food", {"meal": "lunch", **f}))["entry"]


async def test_adding_macros_keeps_kcal(hass: HomeAssistant, setup):
    e = await log(hass, name="Jacket potato with salmon", kcal=481, portions=2)  # 962 kcal in all
    r = await call(hass, "update_food", {"entry_id": e["id"], "protein_g": 25, "carbs_g": 45, "fat_g": 22})
    x = r["entry"]
    assert (x["kcal"], x["protein_g"], x["carbs_g"], x["fat_g"], x["portions"]) == (962, 25, 45, 22, 2)
    assert x["per_portion"]["protein_g"] == 12.5 and x["edited"]
    assert float(hass.states.get(KCAL).state) == 962


async def test_checked_marks_and_clears(hass: HomeAssistant, setup):
    a = await log(hass, name="Jacket potato", kcal=481)
    b = await log(hass, name="Jacket potato", kcal=481, meal="snack")
    for e in (a, b):  # a likely double entry confirmed as two real ones
        r = await call(hass, "update_food", {"entry_id": e["id"], "checked": True})
        assert r["entry"]["checked"] is True
    day = await call(hass, "get_day")
    assert [e.get("checked") for e in day["entries"]] == [True, True]
    assert day["totals"]["kcal"] == 962 and not any(e.get("edited") for e in day["entries"])  # nothing else changes
    r = await call(hass, "update_food", {"entry_id": a["id"], "checked": False})
    assert "checked" not in r["entry"]


async def test_checked_is_kept(hass: HomeAssistant, setup):
    e = await log(hass, name="Skyr", kcal=120, protein_g=20)
    await call(hass, "update_food", {"entry_id": e["id"], "checked": True}, response=False)
    await call(hass, "update_food", {"entry_id": e["id"], "portions": 2}, response=False)  # a later change keeps it
    diary = setup.runtime_data.diary
    await diary.async_flush()
    stored = await diary.store.async_load()
    kept = next(x for d in stored["days"].values() for x in d if x["id"] == e["id"])
    assert kept["checked"] is True and kept["kcal"] == 240
