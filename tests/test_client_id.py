"""Creates happen once per client_id: a double tap, a retry or a replayed queue makes one entry (A-2).

Without client_id every call still makes a new entry. With it, the diary keeps a ledger (persisted with the entries, never
trimmed, kept when the entry is deleted): a repeat returns the first result with `duplicate: true`, a repeat after the entry
was deleted returns `deleted: true` and doesn't log it again, and the same id with a different request is refused.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.util import dt as dt_util
import pytest
import voluptuous as vol

from custom_components.food_diary.const import API_VERSION
from custom_components.food_diary.diary import payload_hash

from .test_food_diary import B64, HOOK, call

CID = "3f2b8c1e-5d7a-4e0b-9c41-0a6e2d9f7b13"
TODAY = "sensor.alex_food_diary_calories_today"


def entries(setup) -> list[dict]:
    return setup.runtime_data.diary.entries(dt_util.now().date().isoformat())


async def test_without_client_id_every_call_logs(hass: HomeAssistant, setup):
    for _ in range(2):
        out = await call(hass, "log_food", {"name": "Tea", "kcal": 30})
        assert "duplicate" not in out
    assert len(entries(setup)) == 2


async def test_same_client_id_logs_once(hass: HomeAssistant, setup):
    first = await call(hass, "log_food", {"name": "Tea", "kcal": 30, "client_id": CID})
    assert first["entry"]["client_id"] == CID and first["entry"]["rev"] == 1 and "duplicate" not in first
    again = await call(hass, "log_food", {"name": "Tea", "kcal": 30, "client_id": CID})
    assert again["duplicate"] is True and "deleted" not in again
    assert again["entry_id"] == first["entry_id"] == again["entry"]["id"]
    assert again["totals"]["kcal"] == 30 and len(entries(setup)) == 1


async def test_repeat_returns_the_entry_as_it_is_now(hass: HomeAssistant, setup):
    first = await call(hass, "log_food", {"name": "Tea", "kcal": 30, "client_id": CID})
    await call(hass, "update_food", {"entry_id": first["entry_id"], "kcal": 45})
    again = await call(hass, "log_food", {"name": "Tea", "kcal": 30, "client_id": CID})
    assert again["duplicate"] is True and again["entry"]["kcal"] == 45 and again["entry"]["rev"] == 2


async def test_repeat_after_delete_is_not_logged_again(hass: HomeAssistant, setup):
    first = await call(hass, "log_food", {"name": "Tea", "kcal": 30, "client_id": CID})
    await call(hass, "delete_food", {"entry_id": first["entry_id"]})
    again = await call(hass, "log_food", {"name": "Tea", "kcal": 30, "client_id": CID})
    assert again == {
        "entry": None,
        "entry_id": first["entry_id"],
        "date": first["date"],
        "totals": {"kcal": 0, "protein_g": 0, "carbs_g": 0, "fat_g": 0, "fibre_g": 0},
        "duplicate": True,
        "deleted": True,
    }
    assert entries(setup) == []


async def test_same_client_id_with_other_food_is_refused(hass: HomeAssistant, setup):
    await call(hass, "log_food", {"name": "Tea", "kcal": 30, "client_id": CID})
    with pytest.raises(ServiceValidationError) as err:
        await call(hass, "log_food", {"name": "Tea", "kcal": 60, "client_id": CID})
    assert err.value.translation_key == "client_id_reused"
    assert len(entries(setup)) == 1


async def test_client_id_must_look_like_one(hass: HomeAssistant, setup):
    for bad in ("short", "x" * 65, "has spaces in it", "semi;colon!"):
        with pytest.raises(vol.Invalid):
            await call(hass, "log_food", {"name": "Tea", "kcal": 30, "client_id": bad})
    assert entries(setup) == []


async def test_two_at_once_make_one_entry(hass: HomeAssistant, setup):
    """A double tap: both calls are in flight together (keeping the photo waits on the disk), and only one logs."""
    est = await call(hass, "estimate", {"kind": "photo", "image": B64})
    food = {"name": est["name"], "kcal": est["kcal"], "photo": est["photo"], "source": "photo", "client_id": CID}
    a, b = await asyncio.gather(call(hass, "log_food", food), call(hass, "log_food", food))
    assert a["entry_id"] == b["entry_id"]
    assert sorted(bool(x.get("duplicate")) for x in (a, b)) == [False, True]
    assert len(entries(setup)) == 1


async def test_ledger_is_kept_with_the_diary(hass: HomeAssistant, setup, hass_storage):
    """The ledger is saved with the entries and loaded back, so a retry after a restart is still a repeat."""
    first = await call(hass, "log_food", {"name": "Tea", "kcal": 30, "client_id": CID})
    await call(hass, "delete_food", {"entry_id": first["entry_id"]})
    await hass.config_entries.async_reload(setup.entry_id)
    await hass.async_block_till_done()
    line = hass_storage["food_diary.alex"]["data"]["client_ids"][CID]
    assert line["entry_id"] == first["entry_id"] and line["date"] == first["date"]
    assert line["payload_hash"] == payload_hash(
        {"name": "Tea", "kcal": 30.0, "portions": 1.0, "source": "manual"}
    )  # as validated
    again = await call(hass, "log_food", {"name": "Tea", "kcal": 30, "client_id": CID})
    assert again["deleted"] is True and entries(setup) == []


async def test_copy_day_once(hass: HomeAssistant, setup):
    today = dt_util.now().date()
    tomorrow = (today + timedelta(days=1)).isoformat()
    await call(hass, "log_food", {"name": "Porridge", "kcal": 300, "meal": "breakfast"})
    first = await call(hass, "copy_day", {"from": today.isoformat(), "to": [tomorrow], "client_id": CID})
    again = await call(hass, "copy_day", {"from": today.isoformat(), "to": [tomorrow], "client_id": CID})
    assert again == {**first, "duplicate": True}
    assert len(setup.runtime_data.diary.entries(tomorrow)) == 1
    await call(hass, "undo_copy", {"token": first["token"]})
    gone = await call(hass, "copy_day", {"from": today.isoformat(), "to": [tomorrow], "client_id": CID})
    assert gone["deleted"] is True and setup.runtime_data.diary.entries(tomorrow) == []


async def test_webhook_logs_once(hass: HomeAssistant, setup, hass_client_no_auth, notes, held_ai, ai_calls):
    """The shortcut retries while the AI is still working out the first request: one entry, one AI ask, one note."""
    client = await hass_client_no_auth()
    body = {"kind": "text", "text": "2 eggs on toast", "client_id": CID}
    first = hass.async_create_task(client.post(HOOK, json=body))
    await held_ai.asked.wait()
    second = hass.async_create_task(client.post(HOOK, json=body))
    await asyncio.sleep(0.05)  # the retry is in, waiting on the first
    held_ai.gate.set()
    a, b = await (await first).json(), await (await second).json()
    assert a["status"] == b["status"] == "logged" and a["entry_id"] == b["entry_id"]
    assert "duplicate" not in a and b["duplicate"] is True and b["kcal"] == a["kcal"] == 315
    assert len(entries(setup)) == 1 and len(ai_calls) == 1 and len(notes) == 1
    assert [c for c in setup.runtime_data.diary.client_ids] == [CID]


async def test_webhook_repeat_after_delete_and_reuse(hass: HomeAssistant, setup, hass_client_no_auth, ai_calls):
    client = await hass_client_no_auth()
    body = {"kind": "text", "text": "2 eggs on toast", "client_id": CID, "quiet": "yes"}
    first = await (await client.post(HOOK, json=body)).json()
    await call(hass, "delete_food", {"entry_id": first["entry_id"]})
    gone = await (await client.post(HOOK, json=body)).json()
    assert gone["ok"] is True and gone["status"] == "deleted" and gone["deleted"] is True and gone["duplicate"] is True
    assert gone["entry_id"] == first["entry_id"] and entries(setup) == []
    r = await client.post(HOOK, json={**body, "text": "3 eggs"})
    assert r.status == 409 and (await r.json())["error"] == "client_id_reused"
    bad = await client.post(HOOK, json={**body, "client_id": "no"})
    assert bad.status == 400
    assert len(ai_calls) == 1  # repeats ask nothing


async def test_sensors_say_api_2(hass: HomeAssistant, setup):
    assert API_VERSION == 2
    for entity in (TODAY, "sensor.alex_food_diary_calories_left", "sensor.alex_food_diary_protein_today"):
        assert hass.states.get(entity).attributes["api"] == 2
    assert hass.states.get("sensor.alex_food_diary_last_logged").attributes["api"] == 2
    e = (await call(hass, "log_food", {"name": "Tea", "kcal": 30}))["entry"]
    await hass.async_block_till_done()
    last = hass.states.get("sensor.alex_food_diary_last_logged").attributes
    assert (last["api"], last["entry_id"], last["rev"]) == (2, e["id"], 1)
