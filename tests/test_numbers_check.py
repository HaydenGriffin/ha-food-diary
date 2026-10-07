"""Recipe numbers that look wrong are flagged with a suggestion, and fixed in one go (a pasta recipe printing 470 kcal and
55 g protein a portion, when its ingredients make ~790 kcal and it serves two)."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.util import dt as dt_util
import pytest

from custom_components.food_diary.checks import doubt, fitting_portions, numbers_key, off, reason
from custom_components.food_diary.const import DOMAIN
from custom_components.food_diary.planner import PLAN_NOTE

from .conftest import ALEX

PASTA = {
    "id": "ig-pasta",
    "name": "hot honey chicken & halloumi pasta",
    "name_en": "Hot honey chicken and halloumi pasta",
    "amounts_per": "portion",
    "servings": 1,
    "ingredients": [
        {"name": "Chicken breast", "amount": "120 g"},
        {"name": "Rigatoni", "amount": "120 g"},
        {"name": "Low fat milk", "amount": "60 ml"},
        {"name": "Honey", "amount": "1 tsp"},
    ],
}
STEW = {
    "id": "ig-stew",
    "name": "Stew",
    "name_en": "Stew",
    "amounts_per": "recipe",
    "servings": 4,
    "ingredients": [{"name": "Beef", "amount": "800 g"}, {"name": "Stock", "amount": "1000 g"}],
}
PRINTED = {"kcal": 470, "protein_g": 55, "carbs_g": 46.1, "fat_g": 7.3, "fibre_g": 4.5, "source": "recipe"}
WHOLE = {"kcal": 790, "protein_g": 56, "carbs_g": 112, "fat_g": 14, "fibre_g": 6}  # the fake AI's "estimate a whole recipe"
HALF = {"kcal": 395, "protein_g": 28, "carbs_g": 56, "fat_g": 7}


def day(n: int) -> str:
    return (dt_util.now().date() + timedelta(days=n)).isoformat()


async def call(hass, service, data=None, response=True):
    return await hass.services.async_call(
        DOMAIN, service, data or {}, blocking=True, return_response=response, context=Context(user_id=ALEX)
    )


def book(hass):
    return hass.data[DOMAIN]["dishes"]


def checker(hass, setup, dishes=(PASTA, STEW)):
    hass.states.async_set("sensor.dish_library", "x", {"dishes": list(dishes)})
    data = setup.runtime_data
    data.dishes_sensor = data.checker.dishes_sensor = "sensor.dish_library"
    data.checker.gap = 0
    return data.checker


def whole_asks(ai_calls):
    return [c for c in ai_calls if c["task"] == "estimate a whole recipe"]


# ---------- when numbers are in doubt ----------


@pytest.mark.parametrize(
    ("yours", "expected"),
    [
        ({"kcal": 470, "protein_g": 30}, "kcal"),  # 40% under 790
        ({"kcal": 1100, "protein_g": 56}, "kcal"),  # 39% over
        ({"kcal": 600, "protein_g": 56}, None),  # 24% under: close enough
        ({"kcal": 790, "protein_g": 85}, "protein_g"),  # protein 52% and 29 g over
        ({"kcal": 790, "protein_g": 30}, "protein_g"),  # 46% and 26 g under
        ({"kcal": 790, "protein_g": 70}, None),  # 25% over
    ],
)
def test_off_thresholds(yours, expected):
    assert off(yours, WHOLE) == expected


def test_small_protein_gaps_never_count():
    assert off({"kcal": 200, "protein_g": 15}, {"kcal": 200, "protein_g": 8}) is None  # 88% but only 7 g


def test_the_pasta_is_in_doubt_and_may_serve_two():
    check = doubt(PASTA, PRINTED, WHOLE)
    assert check == {**HALF, "portions": 2, "reason": "Low for 120 g chicken breast and 120 g rigatoni — may serve 2"}
    assert len(check["reason"]) <= 80


def test_never_doubted():
    assert doubt(PASTA, {**PRINTED, "source": "own"}, WHOLE) is None
    assert doubt(PASTA, {**PRINTED, "dismissed": numbers_key(PRINTED)}, WHOLE) is None
    assert doubt(PASTA, {**PRINTED, "dismissed": "1|2|3|4"}, WHOLE) is not None  # other numbers were confirmed
    assert doubt(PASTA, {**HALF, "portions": 2, "source": "recipe"}, WHOLE) is None  # right for the 2 it makes


def test_right_numbers_for_another_number_of_portions_are_not_in_doubt():
    # printed 200 kcal and 14 g protein a portion: the stew makes 4 (per the recipe) — the ingredients say 790/4 ≈ 198
    assert doubt(STEW, {"kcal": 200, "protein_g": 14, "source": "recipe"}, WHOLE) is None
    # the recipe says 4, but 395 a portion fits 2 exactly: just the servings, not the numbers
    assert doubt(STEW, {**HALF, "source": "recipe"}, WHOLE) is None
    assert fitting_portions({"kcal": 395}, WHOLE, 4) == 2 and fitting_portions({"kcal": 3000}, WHOLE, 4) == 4


def test_the_recipes_own_servings_come_first():
    # amounts marked per portion, but 4 salmon fillets: the recipe's 4 servings fit (456 vs 400), before the closer 5 (365)
    salmon = {
        "amounts_per": "portion",
        "servings": 4,
        "ingredients": [{"name": "Salmon fillets", "amount": "4"}, {"name": "Red onion", "amount": "30 g"}],
    }
    whole = {"kcal": 1825, "protein_g": 94}
    assert fitting_portions({"kcal": 370}, whole, 1, 4) == 4
    check = doubt(salmon, {"kcal": 370, "protein_g": 40, "source": "recipe"}, whole)
    assert check["portions"] == 4 and check["reason"] == "Low for its ingredients — may serve 4"  # no onion in the reason


def test_high_numbers_and_short_reasons():
    check = doubt(STEW, {"kcal": 450, "protein_g": 14, "source": "ai"}, WHOLE)  # 4 portions → 198; nearest fit 2 → 395 (12%)
    assert check["portions"] == 2 and check["reason"] == "High for 800 g beef — may serve 2"  # no stock in the reason
    long = {**PASTA, "ingredients": [{"name": "x" * 70, "amount": "300 g"}, {"name": "y" * 70, "amount": "200 g"}]}
    assert reason("protein_g", {"kcal": 1, "protein_g": 50}, {"protein_g": 20}, 1, 1, long) == "Protein high for its ingredients"


# ---------- the book: scanning and remembering ----------


async def test_scan_flags_doubtful_dishes_and_asks_the_ai_once(hass: HomeAssistant, setup, ai_calls):
    c = checker(hass, setup)
    book(hass).set("ig-pasta", PRINTED)
    book(hass).set("ig-stew", {"kcal": 200, "protein_g": 14, "source": "recipe"})
    book(hass).set("ls-porridge", {"kcal": 300, "source": "recipe"})  # not a library recipe: skipped
    assert await c.async_scan() == ["ig-pasta"]
    kept = book(hass).get("ig-pasta")
    assert kept["check"]["portions"] == 2 and kept["check"]["kcal"] == 395 and kept["estimate"]["kcal"] == 790
    assert len(whole_asks(ai_calls)) == 2
    assert await c.async_scan() == ["ig-pasta"] and len(whole_asks(ai_calls)) == 2  # nothing changed: nothing asked
    book(hass).set("ig-pasta", {**PRINTED, "kcal": 800, "protein_g": 56})  # new numbers: checked again from the kept sum
    assert book(hass).get("ig-pasta").get("check") is None and book(hass).get("ig-pasta")["estimate"]["kcal"] == 790
    assert await c.async_scan() == [] and len(whole_asks(ai_calls)) == 2


async def test_own_numbers_lose_their_doubt(hass: HomeAssistant, setup, ai_calls):
    c = checker(hass, setup)
    book(hass).set("ig-pasta", PRINTED)
    await c.async_scan()
    book(hass).patch("ig-pasta", {"source": "own"})
    assert await c.async_scan() == []


async def test_new_ingredients_are_added_up_again(hass: HomeAssistant, setup, ai_calls):
    c = checker(hass, setup)
    book(hass).set("ig-pasta", PRINTED)
    await c.async_scan()
    checker(hass, setup, [{**PASTA, "ingredients": PASTA["ingredients"][:2]}, STEW])
    await c.async_scan()
    assert len(whole_asks(ai_calls)) == 2


# ---------- what the app sees ----------


async def test_day_dishes_and_nutrition_carry_the_check(hass: HomeAssistant, setup, ai_calls):
    c = checker(hass, setup)
    diary = setup.runtime_data.diary
    book(hass).set("ig-pasta", PRINTED)
    await c.async_scan()
    planned = diary.add(
        day(0),
        {
            "name": "Hot honey chicken and halloumi pasta",
            "meal": "dinner",
            "source": "plan",
            "ref": "ig-pasta",
            "plan_key": f"{day(0)}|dinner",
            "note": PLAN_NOTE,
            **PRINTED,
        },
    )
    mine = diary.add(day(0), {"name": "Pasta", "meal": "lunch", "ref": "ig-pasta", "edited": True, **PRINTED})
    entries = {e["id"]: e for e in (await call(hass, "get_day"))["entries"]}
    want = {**HALF, "portions": 2, "reason": "Low for 120 g chicken breast and 120 g rigatoni — may serve 2"}
    assert entries[planned["id"]]["check"] == want and "check" not in entries[mine["id"]]
    assert "check" not in diary.entries(day(0))[0]  # shown, not stored on the entry
    dishes = {d["id"]: d for d in (await call(hass, "get_dishes"))["dishes"]}
    assert dishes["ig-pasta"]["check"] == want and "check" not in dishes["ig-stew"]
    n = await call(hass, "get_dish_nutrition", {"dish_id": "ig-pasta"})
    assert n["check"] == want and "estimate" not in n


# ---------- check_numbers ----------


async def test_check_numbers_for_a_dish(hass: HomeAssistant, setup, ai_calls):
    checker(hass, setup)
    book(hass).set("ig-pasta", PRINTED)
    r = await call(hass, "check_numbers", {"dish_id": "ig-pasta"})
    assert r == {
        "yours": {"kcal": 470, "protein_g": 55, "carbs_g": 46.1, "fat_g": 7.3},
        "house": HALF,
        "portions": 2,
        "reason": "Low for 120 g chicken breast and 120 g rigatoni — may serve 2",
        "differs": True,
    }
    r = await call(hass, "check_numbers", {"dish_id": "ig-pasta", "portions": 1})
    assert r["house"]["kcal"] == 790 and r["portions"] == 1 and r["differs"] and r["reason"].startswith("Low for 120 g")
    assert len(whole_asks(ai_calls)) == 1  # kept from the first
    book(hass).set("ig-pasta", {**HALF, "fibre_g": 3, "source": "own"})  # the person's own fix
    r = await call(hass, "check_numbers", {"dish_id": "ig-pasta"})
    assert (
        r["differs"] is False
        and r["portions"] == 2
        and r["house"] == HALF
        and r["reason"] == "Matches its ingredients for 2 portions"
    )


async def test_check_numbers_for_entries(hass: HomeAssistant, setup, ai_calls):
    checker(hass, setup)
    diary = setup.runtime_data.diary
    book(hass).set("ig-pasta", PRINTED)
    pasta = diary.add(day(-1), {"name": "Pasta", "meal": "dinner", "ref": "ig-pasta", "portions": 2, **PRINTED})
    r = await call(hass, "check_numbers", {"entry_id": pasta["id"], "date": day(-1)})
    assert r["yours"]["kcal"] == 470 and r["house"] == HALF and r["differs"]  # per portion, whatever was eaten
    soup = diary.add(day(0), {"name": "Soup", "meal": "lunch", "kcal": 150, "protein_g": 4})
    r = await call(hass, "check_numbers", {"entry_id": soup["id"]})
    # no recipe: one normal portion by its name (the fake "estimate a meal from words": 315 kcal)
    assert r["house"]["kcal"] == 315 and r["portions"] == 1 and r["differs"] and r["reason"] == "Low for a normal portion"
    await call(hass, "check_numbers", {"entry_id": soup["id"]})
    assert [c["task"] for c in ai_calls].count("estimate a meal from words") == 1
    with pytest.raises(ServiceValidationError):
        await call(hass, "check_numbers", {"entry_id": "nope"})
    with pytest.raises(ServiceValidationError):
        await call(hass, "check_numbers", {})


# ---------- fixing and dismissing ----------


async def test_set_numbers_with_portions_fixes_the_plan(hass: HomeAssistant, setup, ai_calls):
    c = checker(hass, setup)
    diary = setup.runtime_data.diary
    book(hass).set("ig-pasta", PRINTED)
    await c.async_scan()
    old = {
        "name": "Hot honey chicken and halloumi pasta",
        "meal": "dinner",
        "source": "plan",
        "ref": "ig-pasta",
        "note": PLAN_NOTE,
        **PRINTED,
    }
    past = diary.add(day(-1), {**old, "plan_key": f"{day(-1)}|dinner"})
    tonight = diary.add(day(0), {**old, "plan_key": f"{day(0)}|dinner", "portions": 1.5})
    edited = diary.add(day(1), {**old, "plan_key": f"{day(1)}|dinner", "edited": True})
    later = diary.add(day(3), {**old, "plan_key": f"{day(3)}|dinner"})
    r = await call(hass, "set_dish_nutrition", {"dish_id": "ig-pasta", **HALF, "fibre_g": 3, "source": "own", "portions": 2})
    assert r["refreshed"] == 2 and r["portions"] == 2 and "check" not in r
    kept = book(hass).get("ig-pasta")
    assert kept["portions"] == 2 and "check" not in kept and kept["estimate"]["kcal"] == 790
    got = {e["id"]: e for d in (day(-1), day(0), day(1), day(3)) for e in diary.entries(d)}
    assert got[tonight["id"]]["kcal"] == round(395 * 1.5, 1) and got[tonight["id"]]["portions"] == 1.5
    assert got[later["id"]]["kcal"] == 395 and got[later["id"]]["protein_g"] == 28
    assert got[past["id"]]["kcal"] == 470 and got[edited["id"]]["kcal"] == 470
    assert await c.async_scan() == []
    assert "check" not in next(e for e in (await call(hass, "get_day"))["entries"] if e["id"] == tonight["id"])


async def test_portions_alone_set_what_the_check_assumes(hass: HomeAssistant, setup, ai_calls):
    c = checker(hass, setup)
    await call(hass, "set_dish_nutrition", {"dish_id": "ig-pasta", **HALF, "source": "recipe", "portions": 2})
    assert await c.async_scan() == []  # 395 is right for 2 portions
    r = await call(hass, "check_numbers", {"dish_id": "ig-pasta"})
    assert r["portions"] == 2 and not r["differs"]


async def test_dismiss_keeps_these_numbers_quiet(hass: HomeAssistant, setup, ai_calls):
    c = checker(hass, setup)
    book(hass).set("ig-pasta", PRINTED)
    assert await c.async_scan() == ["ig-pasta"]
    await call(hass, "dismiss_check", {"dish_id": "ig-pasta"}, response=False)
    assert "check" not in book(hass).get("ig-pasta")
    assert await c.async_scan() == []
    book(hass).set("ig-pasta", {**PRINTED, "kcal": 450})  # other numbers: open to doubt again
    assert await c.async_scan() == ["ig-pasta"]
    with pytest.raises(ServiceValidationError):
        await call(hass, "dismiss_check", {"dish_id": "nope"}, response=False)
