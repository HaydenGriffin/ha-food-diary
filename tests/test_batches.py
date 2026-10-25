"""Batches (1.9.0): raw ingredients worked out in one AI call, a pot saved with its cooked numbers, bowls of it logged by
weight, the pot weighed later, and the batches of lately and the regulars."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.util import dt as dt_util
import pytest
import voluptuous as vol

from custom_components.food_diary.batches import KEEP_DAYS, cooked_factor
from custom_components.food_diary.const import DOMAIN

from .conftest import AI_ANSWERS, ALEX

PASTA = {"kcal": 359, "protein_g": 12.5, "carbs_g": 71, "fat_g": 1.5, "fibre_g": 3}
MINCE = {"kcal": 250, "protein_g": 17, "carbs_g": 0, "fat_g": 20, "fibre_g": 0}
SAUCE = {"kcal": 40, "protein_g": 1.2, "carbs_g": 7}  # fat and fibre not given: unknown


async def call(hass, service, data=None, user=ALEX, response=True):
    return await hass.services.async_call(
        DOMAIN, service, data or {}, blocking=True, return_response=response, context=Context(user_id=user)
    )


def ai_item(name, grams, kcal_100, factor, **per):
    return {
        "name": name,
        "grams": grams,
        "kcal_100": kcal_100,
        **{f"{k}_100": per.get(k, 0) for k in ("protein_g", "carbs_g", "fat_g", "fibre_g")},
        "cooked_factor": factor,
    }


def bolognese(**extra):
    return {
        "name": "Pasta bake",
        "ingredients": [
            {"name": "Penne", "grams": 500, "per_100": PASTA, "cooked_factor": 2.2},
            {"name": "Beef mince", "grams": 500, "per_100": MINCE, "cooked_factor": 0.7},
            {"name": "Tomato sauce", "grams": 400, "per_100": SAUCE, "cooked_factor": 0.75, "unknown": ["fat_g", "fibre_g"]},
        ],
        **extra,
    }


# ---------- pure ----------


@pytest.mark.parametrize(("given", "factor"), [(2.2, 2.2), (9, 4.0), (0.05, 0.2), (None, 1.0), (0, 1.0), ("x", 1.0)])
def test_cooked_factor_is_kept_within_what_cooking_does(given, factor):
    assert cooked_factor(given) == factor


# ---------- estimate ----------


async def test_ingredients_are_worked_out_in_one_call(hass: HomeAssistant, setup, ai_calls, monkeypatch):
    monkeypatch.setitem(
        AI_ANSWERS,
        "estimate ingredients",
        {
            "items": [
                ai_item("Spaghetti", 500, 359, 2.3, protein_g=12.5, carbs_g=71),
                ai_item("Onion", 150, 40, 0.8, carbs_g=9),
                {**ai_item("Chopped tomatoes", 800, 21, 9, carbs_g=3.5), "fat_g_100": None},  # factor too big, fat unknown
            ]
        },
    )
    out = await call(
        hass,
        "estimate",
        {
            "kind": "ingredients",
            "dish_name": "Spaghetti with sauce",
            "items": [
                {"name": "Spaghetti", "grams": 450},  # her weight wins over the AI's
                {"name": "Onion", "amount": "1 onion"},
                {"name": "Chopped tomatoes", "amount": "2 tins"},
            ],
        },
    )
    assert len(ai_calls) == 1 and ai_calls[0]["task"] == "estimate ingredients"
    assert "1. Spaghetti: 450 g" in ai_calls[0]["instructions"] and "2. Onion: 1 onion" in ai_calls[0]["instructions"]
    assert ai_calls[0]["structure"]["items"]["selector"]["object"]["multiple"] is True
    spaghetti, onion, tomatoes = out["items"]
    assert spaghetti == {
        "name": "Spaghetti",
        "grams": 450,
        "per_100": {"kcal": 359, "protein_g": 12.5, "carbs_g": 71, "fat_g": 0, "fibre_g": 0},
        "cooked_factor": 2.3,
    }
    assert onion["grams"] == 150 and onion["cooked_factor"] == 0.8
    assert tomatoes["cooked_factor"] == 4.0 and tomatoes["unknown"] == ["fat_g"]


async def test_known_products_keep_their_label_numbers(hass: HomeAssistant, setup, ai_calls, monkeypatch):
    hass.data[DOMAIN]["products"].set(
        "5000000000017", {"name": "Passata", "kcal_100": 32, "protein_g_100": 1.4, "carbs_g_100": 5.5, "fat_g_100": None}
    )
    monkeypatch.setitem(
        AI_ANSWERS,
        "estimate ingredients",
        {"items": [ai_item("Rice", 300, 350, 2.5, carbs_g=78), ai_item("Passata", 500, 50, 0.8), ai_item("Lentils", 200, 1, 2)]},
    )
    out = await call(
        hass,
        "estimate",
        {
            "kind": "ingredients",
            "items": [
                {"name": "Rice", "grams": 300, "per_100": {"kcal": 361, "protein_g": 7, "carbs_g": 79, "fat_g": 1, "fibre_g": 1}},
                {"name": "Passata", "grams": 500, "barcode": "5000000000017"},
                {"name": "Lentils", "grams": 200},
            ],
        },
    )
    rice, passata, lentils = out["items"]
    assert rice["per_100"]["kcal"] == 361 and rice["cooked_factor"] == 2.5  # her food's numbers, the AI's factor
    assert passata["per_100"] == {"kcal": 32, "protein_g": 1.4, "carbs_g": 5.5, "fat_g": 0, "fibre_g": 0}
    assert passata["unknown"] == ["fat_g", "fibre_g"] and passata["cooked_factor"] == 0.8
    assert lentils["per_100"]["kcal"] == 1


async def test_ingredients_need_items_and_at_most_30(hass: HomeAssistant, setup):
    with pytest.raises(ServiceValidationError):
        await call(hass, "estimate", {"kind": "ingredients"})
    with pytest.raises(vol.Invalid):
        await call(hass, "estimate", {"kind": "ingredients", "items": [{"name": f"Thing {n}"} for n in range(31)]})


# ---------- batches ----------


async def test_a_batch_is_its_raw_numbers_over_its_cooked_weight(hass: HomeAssistant, setup):
    b = (await call(hass, "save_batch", bolognese(portions=5)))["batch"]
    assert b["raw_g"] == 1400 and b["cooked_g"] == round(500 * 2.2 + 500 * 0.7 + 400 * 0.75) == 1750
    assert b["cooked_estimated"] is True and b["portions"] == 5 and b["date"] == dt_util.now().date().isoformat()
    assert b["totals"] == {"kcal": 3205.0, "protein_g": 152.3, "carbs_g": 383.0, "fat_g": 107.5, "fibre_g": 15.0}
    assert b["per_100"]["kcal"] == round(3205 / 1750 * 100, 2) and b["unknown"] == ["fat_g", "fibre_g"]
    assert [i["name"] for i in b["ingredients"]] == ["Penne", "Beef mince", "Tomato sauce"]
    weighed = (await call(hass, "save_batch", bolognese(name="Pasta bake ", cooked_g=1600)))["batch"]
    assert weighed["id"] == b["id"] and weighed["cooked_g"] == 1600 and weighed["cooked_estimated"] is False
    assert weighed["per_100"]["kcal"] == round(3205 / 1600 * 100, 2)  # same name, same day: replaced
    assert len((await call(hass, "get_batches"))["recent"]) == 1


async def test_a_bowl_is_logged_by_weight_and_follows_the_weighed_pot(hass: HomeAssistant, setup):
    b = (await call(hass, "save_batch", bolognese()))["batch"]
    out = await call(hass, "log_food", {"source": "batch", "ref": b["id"], "grams": 350, "meal": "dinner"})
    e = out["entry"]
    assert e["name"] == "Pasta bake" and e["source"] == "batch" and e["ref"] == b["id"] and e["grams"] == 350
    assert e["kcal"] == round(b["per_100"]["kcal"] * 3.5, 1) and e["unknown"] == ["fat_g", "fibre_g"]
    own = await call(
        hass,
        "log_food",
        {"name": "Seconds", "source": "batch", "ref": b["id"], "per_100": b["per_100"], "grams": 100, "edited": True},
    )
    assert own["entry"]["name"] == "Seconds"
    r = await call(hass, "update_batch", {"batch_id": b["id"], "cooked_g": 1400, "portions": 4})
    assert r["batch"]["per_100"]["kcal"] == round(3205 / 1400 * 100, 2) and r["batch"]["portions"] == 4
    assert r["refreshed"] == 1  # the bowl; the edited one stays
    day = await call(hass, "get_day")
    bowl = next(x for x in day["entries"] if x["id"] == e["id"])
    assert bowl["kcal"] == round(3205 / 1400 * 350, 1) and bowl["grams"] == 350 and bowl["rev"] == 2


async def test_log_food_from_a_batch_needs_the_batch_or_a_name(hass: HomeAssistant, setup):
    with pytest.raises(ServiceValidationError, match="batch"):
        await call(hass, "log_food", {"source": "batch", "ref": "nope", "grams": 300})
    with pytest.raises(ServiceValidationError, match="name"):
        await call(hass, "log_food", {"kcal": 100})


async def test_recent_batches_and_regulars(hass: HomeAssistant, setup):
    d = dt_util.now().date()
    days = {n: (d - timedelta(days=n)).isoformat() for n in (0, 2, 4, 5, 30, KEEP_DAYS + 1)}
    await call(hass, "save_batch", bolognese(name="Lentil soup", date=days[KEEP_DAYS + 1]))
    await call(hass, "save_batch", bolognese(name="Chilli", date=days[30]))
    await call(hass, "save_batch", bolognese(name="Curry", date=days[5]))
    await call(hass, "save_batch", bolognese(name="chilli", date=days[4]))
    await call(hass, "save_batch", bolognese(name="Pasta bake", date=days[2]))
    await call(hass, "save_batch", bolognese(name="Stew", date=days[0]))
    out = await call(hass, "get_batches")  # 5 days: today and the four before
    assert [b["name"] for b in out["recent"]] == ["Stew", "Pasta bake", "chilli"]
    assert [(r["name"], r["last"]["date"]) for r in out["regulars"]] == [
        ("Stew", days[0]),
        ("Pasta bake", days[2]),
        ("chilli", days[4]),
        ("Curry", days[5]),
    ]  # one per name, newest first; the soup is older than KEEP_DAYS and gone
    assert [b["name"] for b in (await call(hass, "get_batches", {"days": 1}))["recent"]] == ["Stew"]


async def test_batches_survive_a_restart_and_delete(hass: HomeAssistant, setup):
    b = (await call(hass, "save_batch", bolognese()))["batch"]
    await hass.config_entries.async_reload(setup.entry_id)
    await hass.async_block_till_done()
    assert (await call(hass, "get_batches"))["recent"][0]["id"] == b["id"]
    assert await call(hass, "delete_batch", {"batch_id": b["id"]}) == {"ok": True}
    assert (await call(hass, "get_batches"))["recent"] == []
    for service, data in (("delete_batch", {}), ("update_batch", {"cooked_g": 900})):
        with pytest.raises(ServiceValidationError):
            await call(hass, service, {"batch_id": b["id"], **data})


async def test_a_batch_needs_weighed_ingredients(hass: HomeAssistant, setup):
    with pytest.raises(ServiceValidationError):
        await call(hass, "save_batch", {"name": "Air", "ingredients": [{"name": "Water", "grams": 0, "per_100": {"kcal": 0}}]})
