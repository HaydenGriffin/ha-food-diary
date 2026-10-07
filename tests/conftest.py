"""Fixtures: two people, a fake AI (answers by task name), a notify service that records, Open Food Facts mocked."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Generator
from typing import Any
from unittest.mock import patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.setup import async_setup_component
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.food_diary.const import CONF_NOTIFY, CONF_OPEN_PATH, CONF_PERSON, CONF_WEBHOOK_ID, DOMAIN

pytest_plugins = "pytest_homeassistant_custom_component"

ALEX, SAM = "u-alex", "u-sam"
NOTIFY = "notify.mobile_app_alex_phone"
OPEN_PATH = "/lovelace/food"
WEBHOOK_ID = "food_diary_test_hook"
PLAN, LIBRARY = "sensor.meal_plan", "sensor.dish_library"
AI_ANSWERS: dict[str, dict[str, Any]] = {
    "read a nutrition label": {
        "name": "Digestives",
        "kcal_100": 488,
        "protein_g_100": 7,
        "carbs_g_100": 64,
        "fat_g_100": 21,
        "fibre_g_100": 3.6,
        "unit": "g",
        "serving_g": 14.7,
        "pack_g": 400,
        "eaten_g": 44.1,
        "barcode": "5000168001142",
    },
    "estimate a meal from a photo": {
        "name": "Chilli con carne",
        "kcal": 520,
        "protein_g": 32,
        "carbs_g": 48,
        "fat_g": 20,
        "fibre_g": 9,
        "note": "1 bowl",
    },
    "estimate a meal from words": {
        "name": "Eggs on toast",
        "kcal": 315,
        "protein_g": 16,
        "carbs_g": 18,
        "fat_g": 20,
        "fibre_g": 1.5,
    },
    "estimate a dish": {"name": "Chili", "kcal": 425, "protein_g": 24, "carbs_g": 45, "fat_g": 16, "fibre_g": 9},
    "estimate a dish by name": {"name": "Soup", "kcal": 300, "protein_g": 10, "carbs_g": 30, "fat_g": 12, "fibre_g": 4},
    "estimate a whole recipe": {"kcal": 790, "protein_g": 56, "carbs_g": 112, "fat_g": 14, "fibre_g": 6},
    "work out an amount": {"eaten_g": 60},
}
JPEG = b"\xff\xd8\xff\xe0" + b"0" * 2000


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> Generator[None]:
    yield


@pytest.fixture
def ai_calls() -> Generator[list[dict[str, Any]]]:
    """Every AI Task request, answered from AI_ANSWERS by its task name."""
    calls: list[dict[str, Any]] = []

    async def fake_ai(self, task, instructions, structure, attachments=None):
        calls.append({"task": task, "instructions": instructions, "attachments": attachments})
        return dict(AI_ANSWERS[task])

    with patch("custom_components.food_diary.estimate.Estimator._ai", fake_ai):
        yield calls


@pytest.fixture
async def notes(hass: HomeAssistant) -> list[ServiceCall]:
    sent: list[ServiceCall] = []

    async def record(call: ServiceCall) -> None:
        sent.append(call)

    hass.services.async_register("notify", NOTIFY.split(".", 1)[1], record)
    return sent


@pytest.fixture
async def setup(hass: HomeAssistant, tmp_path, ai_calls, notes) -> AsyncGenerator[MockConfigEntry]:
    """Alex's diary (with Sam also a person), set up and running with notifications and no meal plan."""
    hass.config.media_dirs = {"local": str(tmp_path)}
    hass.states.async_set("person.alex", "home", {"user_id": ALEX, "friendly_name": "Alex"})
    hass.states.async_set("person.sam", "home", {"user_id": SAM, "friendly_name": "Sam"})
    assert await async_setup_component(hass, "http", {})
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Alex's food diary",
        unique_id="person.alex",
        data={CONF_PERSON: "person.alex", CONF_WEBHOOK_ID: WEBHOOK_ID},
        options={CONF_NOTIFY: NOTIFY, CONF_OPEN_PATH: OPEN_PATH},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    yield entry
    if entry.state is ConfigEntryState.LOADED:  # stop the planner's and checker's timers
        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
