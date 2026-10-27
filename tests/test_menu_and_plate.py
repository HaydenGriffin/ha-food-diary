"""Eating out and plates (1.11.0): a meal photo says what it's on (`plate`) so its portions are sized against it, a menu's
photos become its dishes in order, and a dish that isn't on the menu is worked out by its name and where it's eaten."""

from __future__ import annotations

import base64
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
import pytest
import voluptuous as vol

from custom_components.food_diary.const import DOMAIN
from custom_components.food_diary.estimate import MAX_DISHES, plate_line

from .conftest import AI_ANSWERS
from .test_food_diary import B64, call

MENU = "read a menu"
OUT = "estimate a dish eaten out"
PNG = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"0" * 2000).decode()


def dish(name: str, kcal: Any, **extra: Any) -> dict[str, Any]:
    return {"name": name, "kcal": kcal, "protein_g": 30, "carbs_g": 50, "fat_g": 20, "fibre_g": 6, "veg_g": 80, **extra}


# ---------- plate ----------


@pytest.mark.parametrize(
    ("plate", "line"),
    [
        ({"kind": "plate", "cm": 26}, "The plate is about 26 cm across."),
        ({"kind": "plate", "cm": 22.5}, "The plate is about 22.5 cm across."),
        ({"kind": "bowl", "cm": 18}, "It is in a bowl about 18 cm across."),
        ({"kind": "bowl"}, "It is in a bowl."),
        ({"kind": "plate"}, "It is on a plate."),
    ],
)
async def test_the_plate_is_in_the_photo_prompt(hass: HomeAssistant, setup, ai_calls, plate, line):
    await call(hass, "estimate", {"kind": "photo", "image": B64, "plate": plate, "hint": "a big one"})
    asked = ai_calls[-1]["instructions"]
    assert line in asked and asked.index(line) < asked.index("They say: a big one")


async def test_no_plate_says_nothing_about_one(hass: HomeAssistant, setup, ai_calls):
    await call(hass, "estimate", {"kind": "photo", "image": B64})
    assert "plate is about" not in ai_calls[-1]["instructions"] and "in a bowl" not in ai_calls[-1]["instructions"]
    assert plate_line(None) == ""


@pytest.mark.parametrize(
    "plate", [{"kind": "cup"}, {"kind": "plate", "cm": 9}, {"kind": "plate", "cm": 41}, {"cm": 26}, {"kind": "bowl", "cm": "x"}]
)
async def test_a_plate_must_make_sense(hass: HomeAssistant, setup, ai_calls, plate):
    with pytest.raises(vol.Invalid):
        await call(hass, "estimate", {"kind": "photo", "image": B64, "plate": plate})
    assert ai_calls == []


# ---------- menu ----------


async def test_a_menu_is_its_dishes_in_order(hass: HomeAssistant, setup, ai_calls, monkeypatch):
    monkeypatch.setitem(
        AI_ANSWERS,
        MENU,
        {
            "place": "  The Olive Tree ",
            "dishes": [
                dish("Chicken souvlaki", 610, protein_g=42, description="with chips and salad", grams=450),
                dish("Greek salad", 320, veg_g=250),
                {**dish("Baklava", 420, veg_g=0), "fibre_g": None, "grams": 0},  # fibre unknown; no weight
                dish("Fresh orange juice", 110, veg_g=0, grams=250),
            ],
        },
    )
    out = await call(hass, "estimate", {"kind": "menu", "media": [B64, PNG], "hint": "lunch menu"})
    assert out["place"] == "The Olive Tree" and out["source"] == "estimate"
    assert [d["name"] for d in out["dishes"]] == ["Chicken souvlaki", "Greek salad", "Baklava", "Fresh orange juice"]
    souvlaki, salad, baklava, _ = out["dishes"]
    assert souvlaki == {
        "name": "Chicken souvlaki",
        "description": "with chips and salad",
        "kcal": 610,
        "protein_g": 42,
        "carbs_g": 50,
        "fat_g": 20,
        "fibre_g": 6,
        "veg_g": 80,
        "grams": 450,
    }
    assert salad["veg_g"] == 250 and "description" not in salad and "grams" not in salad
    assert baklava["unknown"] == ["fibre_g"] and baklava["fibre_g"] == 0 and baklava["veg_g"] == 0 and "grams" not in baklava
    asked = ai_calls[-1]
    assert asked["task"] == MENU and len(asked["attachments"]) == 2 and asked["attachments"][1][1] == "image/png"
    assert "They say: lunch menu" in asked["instructions"] and "in the order the menu lists them" in asked["instructions"]
    assert "drinks and desserts too" in asked["instructions"] and "sides the menu lists" in asked["instructions"]
    assert set(asked["structure"]["dishes"]["selector"]["object"]["fields"]) == {
        *("name", "description", "kcal", "protein_g", "carbs_g", "fat_g", "fibre_g", "veg_g", "grams")
    }


async def test_a_menu_without_its_name_has_no_place(hass: HomeAssistant, setup, monkeypatch):
    monkeypatch.setitem(AI_ANSWERS, MENU, {"place": "", "dishes": [dish("Burger", 800)]})
    out = await call(hass, "estimate", {"kind": "menu", "image": B64})  # one photo as image works too
    assert "place" not in out and out["dishes"][0]["name"] == "Burger"


async def test_dishes_that_cant_be_used_are_left_out_and_forty_at_most(hass: HomeAssistant, setup, monkeypatch):
    bad = [dish("", 500), dish("Water", 0), dish("Mystery", None), dish("Huge", 9000), "not a dish", {"kcal": 300}]
    many = [dish(f"Dish {n}", 400 + n) for n in range(MAX_DISHES + 5)]
    monkeypatch.setitem(AI_ANSWERS, MENU, {"dishes": [*bad, *many]})
    out = await call(hass, "estimate", {"kind": "menu", "media": [B64]})
    assert len(out["dishes"]) == MAX_DISHES and out["dishes"][0]["name"] == "Dish 0"


@pytest.mark.parametrize("answer", [{"dishes": []}, {}, {"dishes": "none"}, {"dishes": [dish("", 500), dish("Tea", 0)]}])
async def test_no_readable_menu_says_so(hass: HomeAssistant, setup, monkeypatch, answer):
    monkeypatch.setitem(AI_ANSWERS, MENU, answer)
    with pytest.raises(ServiceValidationError, match=r"Couldn't read a menu in that photo\."):
        await call(hass, "estimate", {"kind": "menu", "media": [B64]})


async def test_a_menu_needs_one_to_four_photos(hass: HomeAssistant, setup, ai_calls):
    with pytest.raises(ServiceValidationError, match="photo of the menu"):
        await call(hass, "estimate", {"kind": "menu"})
    with pytest.raises(vol.Invalid):
        await call(hass, "estimate", {"kind": "menu", "media": [B64] * 5})
    with pytest.raises(vol.Invalid):
        await call(hass, "estimate", {"kind": "menu", "media": []})
    with pytest.raises(ServiceValidationError, match="didn't come through"):
        await call(hass, "estimate", {"kind": "menu", "media": [B64, "not a photo"]})
    assert ai_calls == []


async def test_a_dish_from_a_menu_logs_as_an_estimate_with_its_veg(hass: HomeAssistant, setup, monkeypatch):
    monkeypatch.setitem(AI_ANSWERS, MENU, {"dishes": [dish("Bean burrito", 700, veg_g=150)]})
    d = (await call(hass, "estimate", {"kind": "menu", "media": [B64]}))["dishes"][0]
    e = (await call(hass, "log_food", {**d, "meal": "lunch", "source": "estimate", "note": "The Olive Tree"}))["entry"]
    assert (e["kcal"], e["veg_g"], e["source"]) == (700, 150, "estimate")


# ---------- a dish that isn't on the menu ----------


async def test_a_dish_by_its_name_where_it_is_eaten(hass: HomeAssistant, setup, ai_calls, monkeypatch):
    monkeypatch.setitem(
        AI_ANSWERS,
        OUT,
        {"name": "Peri-peri chicken", "kcal": 720, "protein_g": 55, "carbs_g": 40, "fat_g": 35, "fibre_g": 5, "veg_g": 60},
    )
    out = await call(hass, "estimate", {"kind": "dish", "name": "Half chicken with rice", "hint": "at The Grill"})
    assert out["name"] == "Half chicken with rice" and out["source"] == "estimate" and out["kcal"] == 720
    assert out["veg_g"] == 60 and "ref" not in out
    asked = ai_calls[-1]
    assert asked["task"] == OUT and '"Half chicken with rice"' in asked["instructions"]
    assert "They say: at The Grill" in asked["instructions"] and "restaurant" in asked["instructions"]
    assert hass.data[DOMAIN]["dishes"].items == {}  # nothing kept


async def test_a_dish_by_dish_name_works_too(hass: HomeAssistant, setup, ai_calls, monkeypatch):
    monkeypatch.setitem(AI_ANSWERS, OUT, {"name": "x", "kcal": 500})
    out = await call(hass, "estimate", {"kind": "dish", "dish_name": "Fish and chips"})
    assert out["name"] == "Fish and chips" and out["unknown"] == ["protein_g", "carbs_g", "fat_g", "fibre_g"]
    assert "They say" not in ai_calls[-1]["instructions"]


async def test_a_dish_needs_an_id_or_a_name(hass: HomeAssistant, setup, ai_calls):
    with pytest.raises(ServiceValidationError, match=r"Add dish_id, or the dish's name\."):
        await call(hass, "estimate", {"kind": "dish", "hint": "at The Grill"})
    assert ai_calls == []
