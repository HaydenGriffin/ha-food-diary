"""Copying keeps what was eaten (A-4): grams, portions and numbers typed by hand come along as they are, instead of being
worked out again from a label's per_100 (which reset the portions to one). The first test is the external audit's
reproduction: a copied 400 kcal barcode entry became 200 kcal."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .test_food_diary import call

NUMS = ("kcal", "protein_g", "carbs_g", "fat_g", "fibre_g")


def day(n: int) -> str:
    return (dt_util.now().date() + timedelta(days=n)).isoformat()


async def copied(hass: HomeAssistant, meal: str | None = None) -> dict:
    await call(hass, "copy_day", {"from": day(0), "to": [day(1)], **({"meal": meal} if meal else {})})
    return {e["name"]: e for e in (await call(hass, "get_day", {"date": day(1)}))["entries"]}


async def test_copied_barcode_entry_keeps_its_portions(hass: HomeAssistant, setup):
    """Audit: a 400 kcal barcode entry (200 g, then two portions of it) became 200 kcal when copied."""
    e = (
        await call(
            hass,
            "log_food",
            {
                "name": "Granola",
                "per_100": {"kcal": 100, "protein_g": 10, "carbs_g": 60, "fat_g": 2, "fibre_g": 5},
                "grams": 200,
                "barcode": "5000000000017",
                "source": "barcode",
                "meal": "breakfast",
            },
        )
    )["entry"]
    assert e["kcal"] == 200
    two = (await call(hass, "update_food", {"entry_id": e["id"], "portions": 2}))["entry"]
    assert two["kcal"] == 400
    c = (await copied(hass))["Granola"]
    assert c["kcal"] == 400 and c["portions"] == 2 and c["grams"] == 200 and c["per_100"] == two["per_100"]
    assert {k: c[k] for k in NUMS} == {k: two[k] for k in NUMS}
    assert c["id"] != e["id"] and c["rev"] == 1 and c["barcode"] == "5000000000017"
    # the copy is a normal label entry: new grams still work it out from per_100
    regrammed = (await call(hass, "update_food", {"entry_id": c["id"], "date": day(1), "grams": 50}))["entry"]
    assert (regrammed["kcal"], regrammed["portions"]) == (50, 1)


async def test_copied_entry_keeps_numbers_typed_by_hand(hass: HomeAssistant, setup):
    e = (await call(hass, "log_food", {"name": "Pasta", "kcal": 300, "protein_g": 12, "portions": 1.5, "meal": "dinner"}))[
        "entry"
    ]
    typed = (await call(hass, "update_food", {"entry_id": e["id"], "kcal": 520, "fat_g": 21}))["entry"]
    c = (await copied(hass, "dinner"))["Pasta"]
    assert {k: c[k] for k in NUMS} == {k: typed[k] for k in NUMS} and c["kcal"] == 520 and c["fat_g"] == 21
    assert c["portions"] == 1.5 and c["edited"] is True and c["per_portion"] == typed["per_portion"]


async def test_copied_label_entry_with_its_own_grams(hass: HomeAssistant, setup):
    """Grams changed after logging (and then numbers typed over them) copy as they ended up."""
    e = (
        await call(
            hass,
            "log_food",
            {"name": "Crisps", "per_100": {"kcal": 520}, "grams": 25, "source": "label", "meal": "snack", "unit": "g"},
        )
    )["entry"]
    await call(hass, "update_food", {"entry_id": e["id"], "grams": 40})
    typed = (await call(hass, "update_food", {"entry_id": e["id"], "kcal": 190}))["entry"]
    c = (await copied(hass))["Crisps"]
    assert (c["kcal"], c["grams"], c["portions"], c["edited"]) == (190, 40, 1, True)
    assert c["kcal"] == typed["kcal"]


async def test_copy_drops_what_belongs_to_the_original(hass: HomeAssistant, setup):
    e = (await call(hass, "log_food", {"name": "Tea", "kcal": 30, "client_id": "tea-0000000001"}))["entry"]
    await call(hass, "update_food", {"entry_id": e["id"], "kcal": 35})
    c = (await copied(hass))["Tea"]
    assert "client_id" not in c and c["rev"] == 1 and c["at"] and c["id"] != e["id"]
