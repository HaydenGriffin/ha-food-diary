"""Food diary: setup, whose diary a call is, logging and changing, sensors, labels, barcodes, the webhook, Undo, midnight."""

from __future__ import annotations

import base64
from datetime import timedelta

from homeassistant import config_entries
from homeassistant.core import Context, HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import ServiceValidationError
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.food_diary.const import DOMAIN, OFF_URL
from custom_components.food_diary.estimate import amount_grams, label_result, off_product

from .conftest import ALEX, JPEG, NOTIFY, OPEN_PATH, PLAN, SAM, WEBHOOK_ID

B64 = base64.b64encode(JPEG).decode()
KCAL = "sensor.alex_food_diary_calories_today"
HOOK = f"/api/webhook/{WEBHOOK_ID}"


async def call(hass, service, data=None, user=ALEX, response=True):
    return await hass.services.async_call(
        DOMAIN, service, data or {}, blocking=True, return_response=response, context=Context(user_id=user)
    )


# ---------- pure ----------


@pytest.mark.parametrize(
    ("said", "grams"),
    [
        ("50 g", 50),
        ("50", 50),
        ("about 30g of it", 30),
        ("3 biscuits", 44.1),
        ("1 serving", 14.7),
        ("a biscuit", 14.7),
        ("half the pack", 200),
        ("the whole pack", 400),
        ("half", None),
        ("2 packs", 800),
        ("some", None),
        ("", None),
        ("a big bowl", None),
        ("2 small slices", 29.4),
        ("one pot", 14.7),
    ],
)
def test_amount_grams(said, grams):
    got = amount_grams(said, 14.7, 400)
    assert (got is None and grams is None) or round(got, 2) == round(grams, 2)


def test_label_result_sums_and_guess():
    out = label_result({"name": "Digestives", "kcal_100": 488, "protein_g_100": 7, "eaten_g": 50, "serving_g": 14.7})
    assert out["grams"] == 50 and out["kcal"] == 244 and out["protein_g"] == 3.5 and not out["guessed"]
    guess = label_result({"name": "Yogurt", "kcal_100": 60, "unit": "ml", "serving_g": 150})
    assert guess["grams"] == 150 and guess["guessed"] and guess["unit"] == "ml" and "check it" in guess["note"]


def test_off_product_kj_fallback_and_brand():
    p = off_product(
        {
            "product_name": "Digestives",
            "brands": "McVitie's",
            "nutriments": {"energy_100g": 2050, "proteins_100g": 7},
            "serving_quantity": 14.7,
            "product_quantity": 400,
        }
    )
    assert p["name"] == "McVitie's Digestives" and p["kcal_100"] == pytest.approx(489.96, 0.01) and p["serving_g"] == 14.7
    assert off_product({"product_name": "Water", "nutriments": {}}) is None


# ---------- setup ----------


async def test_config_flow_one_diary_per_person(hass: HomeAssistant):
    hass.states.async_set("person.alex", "home", {"user_id": ALEX, "friendly_name": "Alex"})
    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert r["type"] is FlowResultType.FORM
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"person": "person.alex", "open_path": OPEN_PATH})
    assert r["type"] is FlowResultType.CREATE_ENTRY and r["title"] == "Alex's food diary"
    assert r["data"]["webhook_id"] and r["options"] == {"open_path": OPEN_PATH}
    again = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    again = await hass.config_entries.flow.async_configure(again["flow_id"], {"person": "person.alex"})
    assert again["type"] is FlowResultType.ABORT and again["reason"] == "already_configured"


async def test_options_flow_shows_the_webhook_and_saves(hass: HomeAssistant, setup):
    r = await hass.config_entries.options.async_init(setup.entry_id)
    assert r["type"] is FlowResultType.FORM and r["description_placeholders"]["webhook_url"] == HOOK  # no URL configured
    r = await hass.config_entries.options.async_configure(
        r["flow_id"], {"notify_service": NOTIFY, "meal_plan_sensor": PLAN, "open_path": ""}
    )
    assert r["type"] is FlowResultType.CREATE_ENTRY
    assert setup.options == {"notify_service": NOTIFY, "meal_plan_sensor": PLAN}  # empty options are dropped
    await hass.async_block_till_done()
    assert setup.runtime_data.planner is not None and setup.runtime_data.open_path is None  # reloaded with them


async def test_prompts_mention_the_country_when_set(hass: HomeAssistant, setup, ai_calls):
    await call(hass, "estimate", {"kind": "text", "text": "toast"})
    assert "Alex (one adult) ate this" in ai_calls[-1]["instructions"]
    hass.config.country = "GB"
    await call(hass, "estimate", {"kind": "text", "text": "toast"})
    assert "Alex (one adult in GB) ate this" in ai_calls[-1]["instructions"]


async def test_entities(hass: HomeAssistant, setup):
    s = hass.states.get(KCAL)
    assert float(s.state) == 0 and s.attributes["unit_of_measurement"] == "kcal" and s.attributes["state_class"] == "total"
    assert s.attributes["goal"] == 2000 and s.attributes["last_reset"]
    assert float(hass.states.get("sensor.alex_food_diary_calories_left").state) == 2000
    assert float(hass.states.get("number.alex_food_diary_calorie_goal").state) == 2000
    await hass.services.async_call(
        "number", "set_value", {"entity_id": "number.alex_food_diary_calorie_goal", "value": 1400}, blocking=True
    )
    assert float(hass.states.get("sensor.alex_food_diary_calories_left").state) == 1400


# ---------- whose diary ----------


async def test_whose_diary(hass: HomeAssistant, setup):
    await call(hass, "log_food", {"name": "Apple", "kcal": 80}, response=False)
    assert float(hass.states.get(KCAL).state) == 80
    # Sam has no diary; with only Alex's set up, Sam's call (or an automation's) lands in Alex's
    await call(hass, "log_food", {"name": "Pear", "kcal": 60}, user=SAM, response=False)
    assert float(hass.states.get(KCAL).state) == 140
    with pytest.raises(ServiceValidationError):
        await call(hass, "get_day", {"person": "person.sam"}, user=SAM)


# ---------- logging and changing ----------


async def test_log_change_delete(hass: HomeAssistant, setup):
    r = await call(hass, "log_food", {"name": "Porridge", "kcal": 320, "protein_g": 12, "portions": 1.5, "meal": "breakfast"})
    e = r["entry"]
    assert e["kcal"] == 480 and e["protein_g"] == 18 and e["per_portion"]["kcal"] == 320
    s = hass.states.get(KCAL)
    assert float(s.state) == 480 and s.attributes["breakfast"] == 480 and s.attributes["left"] == 1520
    await call(hass, "update_food", {"entry_id": e["id"], "portions": 1}, response=False)
    assert float(hass.states.get(KCAL).state) == 320
    await call(hass, "update_food", {"entry_id": e["id"], "kcal": 300, "protein_g": 10}, response=False)
    day = await call(hass, "get_day")
    assert day["entries"][0]["kcal"] == 300 and day["entries"][0]["edited"] and day["totals"]["protein_g"] == 10
    await call(hass, "delete_food", {"entry_id": e["id"]}, response=False)
    assert float(hass.states.get(KCAL).state) == 0
    with pytest.raises(ServiceValidationError):
        await call(hass, "delete_food", {"entry_id": e["id"]}, response=False)


async def test_label_entry_keeps_grams(hass: HomeAssistant, setup):
    r = await call(
        hass, "log_food", {"name": "Digestives", "per_100": {"kcal": 488, "protein_g": 7}, "grams": 50, "source": "label"}
    )
    e = r["entry"]
    assert e["kcal"] == 244 and e["grams"] == 50 and e["per_100"]["kcal"] == 488
    r = await call(hass, "update_food", {"entry_id": e["id"], "grams": 100})
    assert r["entry"]["kcal"] == 488 and r["entry"]["protein_g"] == 7
    recent = await call(hass, "get_recent")
    assert recent["foods"][0]["per_100"]["kcal"] == 488


async def test_history_and_other_days(hass: HomeAssistant, setup):
    yesterday = (dt_util.now().date() - timedelta(days=1)).isoformat()
    await call(hass, "log_food", {"name": "Pasta", "kcal": 600, "date": yesterday}, response=False)
    await call(hass, "log_food", {"name": "Salad", "kcal": 300}, response=False)
    h = await call(hass, "get_history", {"days": 3})
    assert [d["kcal"] for d in h["days"]] == [0, 600, 300]
    assert float(hass.states.get(KCAL).state) == 300


async def test_midnight_starts_again(hass: HomeAssistant, setup, freezer):
    freezer.move_to(dt_util.start_of_local_day() + timedelta(hours=20))
    await call(hass, "log_food", {"name": "Dinner", "kcal": 700}, response=False)
    assert float(hass.states.get(KCAL).state) == 700
    freezer.move_to(dt_util.start_of_local_day() + timedelta(days=1, seconds=2))
    async_fire_time_changed(hass, dt_util.now())
    await hass.async_block_till_done()
    s = hass.states.get(KCAL)
    assert float(s.state) == 0 and dt_util.parse_datetime(s.attributes["last_reset"]) == dt_util.start_of_local_day()


async def test_kept_after_restart(hass: HomeAssistant, setup):
    await call(hass, "log_food", {"name": "Toast", "kcal": 200}, response=False)
    await hass.config_entries.async_reload(setup.entry_id)
    await hass.async_block_till_done()
    assert float(hass.states.get(KCAL).state) == 200


# ---------- working things out ----------


async def test_estimate_label_teaches_the_barcode(hass: HomeAssistant, setup, ai_calls, aioclient_mock):
    r = await call(hass, "estimate", {"kind": "label", "image": B64, "amount": "3 biscuits"})
    assert r["kcal"] == 215.2 and r["grams"] == 44.1 and r["barcode"] == "5000168001142"
    assert ai_calls[-1]["attachments"][0][0].startswith("media-source://media_source/local/food_diary/")
    # the same product by barcode: known from the label, so no Open Food Facts call
    r = await call(hass, "estimate", {"kind": "barcode", "barcode": "5000168001142", "amount": "2 biscuits"})
    assert r["grams"] == 29.4 and r["kcal"] == 143.5 and aioclient_mock.call_count == 0


async def test_estimate_barcode_open_food_facts(hass: HomeAssistant, setup, ai_calls, aioclient_mock):
    aioclient_mock.get(
        OFF_URL.format(code="5010029000023"),
        json={
            "status": 1,
            "product": {
                "product_name": "Weetabix",
                "nutriments": {"energy-kcal_100g": 362, "proteins_100g": 12, "carbohydrates_100g": 69, "fat_100g": 2},
                "serving_quantity": 37.5,
                "product_quantity": 430,
            },
        },
    )
    r = await call(hass, "estimate", {"kind": "barcode", "barcode": "5010029000023", "amount": "2 biscuits"})
    assert r["grams"] == 75 and r["kcal"] == 271.5 and r["source"] == "barcode"
    r = await call(hass, "estimate", {"kind": "barcode", "barcode": "5010029000023", "amount": "a big bowl"})
    assert r["grams"] == 60 and ai_calls[-1]["task"] == "work out an amount"
    assert aioclient_mock.call_count == 1  # kept after the first lookup
    aioclient_mock.get(OFF_URL.format(code="1234567890123"), status=404)
    with pytest.raises(Exception, match="isn't in Open Food Facts"):
        await call(hass, "estimate", {"kind": "barcode", "barcode": "1234567890123"})


async def test_estimate_dish_is_kept(hass: HomeAssistant, setup, ai_calls):
    r = await call(
        hass, "estimate", {"kind": "dish", "dish_id": "ig-1", "dish_name": "Chili", "ingredients": ["beef 500 g"], "servings": 4}
    )
    assert r["kcal"] == 425 and "makes 4 portions" in ai_calls[-1]["instructions"]
    n = len(ai_calls)
    r = await call(hass, "estimate", {"kind": "dish", "dish_id": "ig-1", "dish_name": "Chili"})
    assert r["kcal"] == 425 and len(ai_calls) == n
    await call(hass, "set_dish_nutrition", {"dish_id": "ig-1", "kcal": 290, "protein_g": 24, "source": "recipe"}, response=False)
    assert (await call(hass, "get_dish_nutrition", {"dish_id": "ig-1"}))["source"] == "recipe"
    r = await call(hass, "estimate", {"kind": "dish", "dish_id": "ig-1", "dish_name": "Chili", "fresh": True})
    assert len(ai_calls) == n + 1


async def test_bad_photo(hass: HomeAssistant, setup):
    with pytest.raises(ServiceValidationError):
        await call(hass, "estimate", {"kind": "photo", "image": base64.b64encode(b"not a picture" * 100).decode()})


# ---------- the shortcut ----------


async def test_webhook_logs_answers_and_notifies(hass: HomeAssistant, setup, hass_client_no_auth, notes):
    client = await hass_client_no_auth()
    r = await client.post(HOOK, json={"kind": "label", "image": B64, "amount": "3 biscuits"})
    body = await r.json()
    assert r.status == 200 and body["status"] == "logged" and body["kcal"] == 215.2 and body["grams"] == 44.1
    assert body["message"].startswith("44 g · 215 kcal · 1785 left today")
    assert float(hass.states.get(KCAL).state) == 215.2
    await hass.async_block_till_done()
    note = notes[-1].data
    assert note["title"] == "Logged: Digestives" and note["data"]["url"] == f"{OPEN_PATH}#food-{body['entry_id']}"
    undo = note["data"]["actions"][0]["action"]
    # Undo on the notification takes it out again
    hass.bus.async_fire("mobile_app_notification_action", {"action": undo})
    await hass.async_block_till_done()
    assert float(hass.states.get(KCAL).state) == 0 and notes[-1].data["title"] == "Removed"


async def test_webhook_unknown_barcode_asks_for_label(hass: HomeAssistant, setup, hass_client_no_auth, aioclient_mock, notes):
    aioclient_mock.get(OFF_URL.format(code="1234567890123"), status=404)
    client = await hass_client_no_auth()
    body = await (await client.post(HOOK, json={"kind": "barcode", "barcode": "1234567890123"})).json()
    assert body == {
        "ok": False,
        "status": "need_label",
        "message": "That product isn't in Open Food Facts yet. Take a photo of its label instead.",
    }
    bad = await client.post(HOOK, data=b"nope")
    assert bad.status == 400
    # a caller that shows the result itself asks for no notification
    n = len(notes)
    ok = await (await client.post(HOOK, json={"kind": "text", "text": "2 eggs on toast", "notify": False})).json()
    await hass.async_block_till_done()
    assert ok["status"] == "logged" and ok["kcal"] == 315 and len(notes) == n


# ---------- other days ----------


async def test_future_days_are_fine(hass: HomeAssistant, setup):
    nextweek = (dt_util.now().date() + timedelta(days=6)).isoformat()
    await call(hass, "log_food", {"name": "Planned porridge", "kcal": 300, "date": nextweek, "meal": "breakfast"}, response=False)
    h = await call(hass, "get_history", {"days": 8, "date": nextweek})
    assert h["days"][-1]["kcal"] == 300
    assert float(hass.states.get(KCAL).state) == 0  # today's sensors only count today
