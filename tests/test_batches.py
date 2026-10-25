"""Batches (1.9.0): raw ingredients worked out in one AI call, a pot saved with its cooked numbers, bowls of it logged by
weight, the pot weighed later, and the batches of lately and the regulars. 1.9.1: incomplete answers, bad numbers, tiny
pots and old batches."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
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


# ---------- 1.9.1: review fixes ----------


def ingredients(*names):
    return {"kind": "ingredients", "items": [{"name": n, "amount": "some"} for n in names]}


@pytest.mark.parametrize(
    "answer",
    [
        {"items": [ai_item("Oil", 30, 884, 1, fat_g=100)]},  # asked Rice then Oil, got only Oil
        {"items": []},
        {},
        {"items": [{"name": "Rice", "grams": 300, "cooked_factor": 2.5}, ai_item("Oil", 30, 884, 1)]},  # no calories
        {"items": [ai_item("Rice", 0, 350, 2.5), ai_item("Oil", 30, 884, 1)]},  # no weight
    ],
)
async def test_an_incomplete_answer_is_refused(hass: HomeAssistant, setup, monkeypatch, answer):
    monkeypatch.setitem(AI_ANSWERS, "estimate ingredients", answer)
    with pytest.raises(HomeAssistantError, match="Rice"):
        await call(hass, "estimate", ingredients("Rice", "Oil"))


async def test_ingredients_are_matched_one_to_one_by_their_number(hass: HomeAssistant, setup, ai_calls, monkeypatch):
    monkeypatch.setitem(
        AI_ANSWERS,
        "estimate ingredients",
        {
            "items": [
                {**ai_item("Onion", 80, 40, 0.8), "n": 3},  # out of order, and two onions: the number says which
                {**ai_item("Red onion", 150, 42, 0.8), "n": 1},  # renamed: still the first
                {**ai_item("Rice", 300, 350, 2.5), "n": 2},
                {**ai_item("Salt", 5, 0, 1), "n": "nan"},  # a number that isn't one: left over
            ]
        },
    )
    out = await call(hass, "estimate", ingredients("Onion", "Rice", "Onion"))
    assert [(i["name"], i["grams"], i["per_100"]["kcal"]) for i in out["items"]] == [
        ("Onion", 150, 42),
        ("Rice", 300, 350),
        ("Onion", 80, 40),
    ]
    assert ai_calls[-1]["structure"]["items"]["selector"]["object"]["fields"]["n"]["required"] is True


async def test_two_ingredients_of_one_name_need_their_numbers(hass: HomeAssistant, setup, monkeypatch):
    monkeypatch.setitem(
        AI_ANSWERS, "estimate ingredients", {"items": [ai_item("Onion", 150, 40, 0.8), ai_item("Onion", 80, 40, 0.8)]}
    )
    with pytest.raises(HomeAssistantError, match="Onion, Onion"):
        await call(hass, "estimate", ingredients("Onion", "Onion"))


async def test_a_given_zero_is_kept(hass: HomeAssistant, setup, monkeypatch):
    """Water: its own per_100 (or the product book's) with 0 kcal is 0, not the AI's guess."""
    hass.data[DOMAIN]["products"].set("5000000000024", {"name": "Still water", "kcal_100": 0, "protein_g_100": 0})
    monkeypatch.setitem(
        AI_ANSWERS,
        "estimate ingredients",
        {"items": [{**ai_item("Water", 500, 15, 0.7), "n": 1}, {**ai_item("Still water", 500, 12, 0.7), "n": 2}]},
    )
    zero = {k: 0 for k in ("kcal", "protein_g", "carbs_g", "fat_g", "fibre_g")}
    out = await call(
        hass,
        "estimate",
        {
            "kind": "ingredients",
            "items": [
                {"name": "Water", "grams": 500, "per_100": zero},
                {"name": "Still water", "grams": 500, "barcode": "5000000000024"},
            ],
        },
    )
    water, bottled = out["items"]
    assert water["per_100"] == zero and "unknown" not in water
    assert bottled["per_100"]["kcal"] == 0 and bottled["unknown"] == ["carbs_g", "fat_g", "fibre_g"]


@pytest.mark.parametrize(
    "per_100",
    [{"kcal": -5}, {"kcal": "nan"}, {"kcal": "inf"}, {"kcal": 10**400}, {"kcal": 100, "fat_g": float("-inf")}, {"fat_g": 3}],
)
async def test_batch_numbers_are_real_amounts(hass: HomeAssistant, setup, per_100):
    with pytest.raises(vol.Invalid):
        await call(hass, "save_batch", {"name": "Odd", "ingredients": [{"name": "Thing", "grams": 100, "per_100": per_100}]})
    with pytest.raises(vol.Invalid):
        await call(hass, "log_food", {"name": "Thing", "per_100": {**per_100, "kcal": "nan"}, "grams": 100})


async def test_a_retried_bowl_answers_after_its_batch_is_deleted(hass: HomeAssistant, setup):
    b = (await call(hass, "save_batch", bolognese()))["batch"]
    bowl = {"source": "batch", "ref": b["id"], "grams": 350, "client_id": "bowl-of-pasta-1"}
    first = await call(hass, "log_food", bowl)
    await call(hass, "delete_batch", {"batch_id": b["id"]})
    again = await call(hass, "log_food", bowl)
    assert again["duplicate"] is True and again["entry_id"] == first["entry_id"]


@pytest.mark.parametrize("grams", [0.01, 0.5])
async def test_a_pot_needs_a_gram_of_ingredients(hass: HomeAssistant, setup, grams):
    with pytest.raises(ServiceValidationError, match="raw weight"):
        await call(hass, "save_batch", {"name": "Tiny", "ingredients": [{"name": "Saffron", "grams": grams, "per_100": MINCE}]})


async def test_a_small_pot_keeps_its_precision(hass: HomeAssistant, setup):
    b = (
        await call(
            hass,
            "save_batch",
            {"name": "Paste", "ingredients": [{"name": "Garlic", "grams": 1.2, "per_100": {"kcal": 100}, "cooked_factor": 0.3}]},
        )
    )["batch"]
    assert b["cooked_g"] == 0 and b["per_100"]["kcal"] == round(1.2 / 0.36 * 100, 2)  # 0.36 g, not the 1.2 g raw


async def test_old_batches_go_on_load_and_on_read(hass: HomeAssistant, setup):
    diary = setup.runtime_data.diary
    b = (await call(hass, "save_batch", bolognese()))["batch"]
    old = (dt_util.now().date() - timedelta(days=KEEP_DAYS + 1)).isoformat()
    diary.data["batches"].append({**b, "id": "old-soup", "name": "Lentil soup", "date": old})
    assert [r["name"] for r in (await call(hass, "get_batches"))["regulars"]] == ["Pasta bake"]
    assert [x["id"] for x in diary.data["batches"]] == [b["id"]]
    diary.data["batches"].append({**b, "id": "old-soup", "name": "Lentil soup", "date": old})
    await diary.async_flush()
    await hass.config_entries.async_reload(setup.entry_id)
    await hass.async_block_till_done()
    assert [x["id"] for x in setup.runtime_data.diary.data["batches"]] == [b["id"]]
