"""Food photos (the person's own, the product's, the recipe's) and copying days, with undo."""

from __future__ import annotations

import base64
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.util import dt as dt_util
import pytest

from custom_components.food_diary.const import CONF_DISHES_SENSOR, OFF_URL

from .conftest import JPEG
from .test_food_diary import B64, call


def day(n: int) -> str:
    return (dt_util.now().date() + timedelta(days=n)).isoformat()


async def test_meal_photo_is_kept_and_served(hass: HomeAssistant, setup, hass_client):
    est = await call(hass, "estimate", {"kind": "photo", "image": B64})
    assert est["photo"]
    await call(
        hass, "log_food", {"name": est["name"], "kcal": est["kcal"], "photo": est["photo"], "source": "photo"}, response=False
    )
    e = (await call(hass, "get_day"))["entries"][0]
    assert e["image"].startswith("/api/food_diary/photo/") and e["photo"] != est["photo"]
    client = await hass_client()
    r = await client.get(e["image"])
    assert r.status == 200 and await r.read() == JPEG
    assert (await client.get("/api/food_diary/photo/../../secrets.yaml")).status == 404
    again = next(f for f in (await call(hass, "get_recent"))["foods"] if f["name"] == est["name"])
    await call(hass, "log_food", {"name": again["name"], "kcal": 1, "photo": again["photo"]}, response=False)  # again: same photo
    assert {x["photo"] for x in (await call(hass, "get_day"))["entries"]} == {e["photo"]}


async def test_photo_needs_sign_in(hass: HomeAssistant, setup, hass_client_no_auth):
    r = await (await hass_client_no_auth()).get("/api/food_diary/photo/0123456789abcdef.jpg")
    assert r.status == 401


async def test_add_a_photo_later(hass: HomeAssistant, setup):
    await call(hass, "log_food", {"name": "Toast", "kcal": 200}, response=False)
    e = (await call(hass, "get_day"))["entries"][0]
    assert "image" not in e
    out = await call(hass, "set_photo", {"entry_id": e["id"], "image": base64.b64encode(JPEG).decode()})
    assert out["entry"]["image"].startswith("/api/food_diary/photo/")


async def test_product_and_recipe_photos(hass: HomeAssistant, setup, aioclient_mock):
    aioclient_mock.get(
        OFF_URL.format(code="5010029000023"),
        json={
            "status": 1,
            "product": {
                "product_name": "Weetabix",
                "nutriments": {"energy-kcal_100g": 362},
                "serving_quantity": 37.5,
                "image_front_url": "https://images.openfoodfacts.org/weetabix.jpg",
            },
        },
    )
    est = await call(hass, "estimate", {"kind": "barcode", "barcode": "5010029000023", "amount": "1 serving"})
    assert est["image_url"] == "https://images.openfoodfacts.org/weetabix.jpg"
    await call(hass, "log_food", {"name": "Weetabix", "kcal": 135, "barcode": "5010029000023"}, response=False)  # by its barcode
    hass.states.async_set(
        "sensor.dish_library",
        "x",
        {
            "dishes": [
                {"id": "ls-porridge", "name": "Owsianka", "name_en": "Porridge", "image": "/local/recipes/ls-porridge.jpg?v=2"}
            ]
        },
    )
    hass.config_entries.async_update_entry(setup, options={**setup.options, CONF_DISHES_SENSOR: "sensor.dish_library"})
    await hass.async_block_till_done()
    await call(hass, "log_food", {"name": "Porridge", "kcal": 350}, response=False)  # by name
    await call(
        hass, "log_food", {"name": "Their porridge", "kcal": 350, "source": "dish", "ref": "ls-porridge"}, response=False
    )  # by id
    await call(hass, "log_food", {"name": "Mystery", "kcal": 100, "image_url": "http://not-https.example/x.jpg"}, response=False)
    images = {e["name"]: e.get("image") for e in (await call(hass, "get_day"))["entries"]}
    assert images == {
        "Weetabix": "https://images.openfoodfacts.org/weetabix.jpg",
        "Porridge": "/local/recipes/ls-porridge.jpg?v=2",
        "Their porridge": "/local/recipes/ls-porridge.jpg?v=2",
        "Mystery": None,
    }
    again = {f["name"]: f.get("image") for f in (await call(hass, "get_recent"))["foods"]}
    assert again["Weetabix"].startswith("https://")


async def test_copy_a_day_and_undo(hass: HomeAssistant, setup):
    diary = setup.runtime_data.diary
    await call(hass, "log_food", {"name": "Porridge", "kcal": 300, "meal": "breakfast", "date": day(0)}, response=False)
    await call(hass, "log_food", {"name": "Soup", "kcal": 250, "meal": "lunch", "date": day(0)}, response=False)
    diary.add(
        day(0),
        {
            "name": "Chilli",
            "kcal": 600,
            "meal": "dinner",
            "source": "plan",
            "plan_key": f"{day(0)}|dinner",
            "note": "From the meal plan",
        },
    )
    await call(hass, "log_food", {"name": "Toast", "kcal": 200, "meal": "breakfast", "date": day(2)}, response=False)
    r = await call(hass, "copy_day", {"from": day(0), "to": [day(1), day(2)]})
    assert r["added"] == {day(1): 3, day(2): 3} and r["removed"] == {}
    d1 = (await call(hass, "get_day", {"date": day(1)}))["entries"]
    chilli = next(e for e in d1 if e["name"] == "Chilli")
    assert chilli["source"] == "again" and "plan_key" not in chilli and "note" not in chilli  # planned food comes as plain food
    assert len((await call(hass, "get_day", {"date": day(2)}))["entries"]) == 4  # added to what was there
    await call(hass, "undo_copy", {"token": r["token"]}, response=False)
    assert (await call(hass, "get_day", {"date": day(1)}))["entries"] == []
    assert [e["name"] for e in (await call(hass, "get_day", {"date": day(2)}))["entries"]] == ["Toast"]


async def test_copy_a_meal_replacing(hass: HomeAssistant, setup):
    await call(hass, "log_food", {"name": "Porridge", "kcal": 300, "meal": "breakfast", "date": day(0)}, response=False)
    await call(hass, "log_food", {"name": "Soup", "kcal": 250, "meal": "lunch", "date": day(0)}, response=False)
    await call(hass, "log_food", {"name": "Toast", "kcal": 200, "meal": "breakfast", "date": day(1)}, response=False)
    await call(hass, "log_food", {"name": "Salad", "kcal": 200, "meal": "lunch", "date": day(1)}, response=False)
    r = await call(hass, "copy_day", {"from": day(0), "to": [day(1)], "meal": "breakfast", "replace": True})
    assert r["added"] == {day(1): 1} and r["removed"] == {day(1): 1}
    assert sorted(e["name"] for e in (await call(hass, "get_day", {"date": day(1)}))["entries"]) == ["Porridge", "Salad"]
    await call(hass, "undo_copy", {"token": r["token"]}, response=False)
    assert sorted(e["name"] for e in (await call(hass, "get_day", {"date": day(1)}))["entries"]) == ["Salad", "Toast"]
    with pytest.raises(ServiceValidationError):
        await call(hass, "undo_copy", {"token": r["token"]})
