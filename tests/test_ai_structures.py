"""What the AI is asked for (1.11.1): every structure is one ai_task takes, and a request it refuses is the AI not
answering (logged), never "the house can't do that"."""

from __future__ import annotations

from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
import pytest
import voluptuous as vol

from custom_components.food_diary import estimate
from custom_components.food_diary.estimate import NUMBER, PHOTO_STRUCTURE, Estimator

from .conftest import AI_STRUCTURE

STRUCTURES = sorted(n for n in dir(estimate) if n.endswith("_STRUCTURE"))


@pytest.mark.parametrize("name", STRUCTURES)
def test_ai_task_takes_every_structure(name):
    AI_STRUCTURE(getattr(estimate, name))


@pytest.fixture
def ai_task(hass: HomeAssistant) -> None:
    """ai_task.generate_data with its own check of `structure`, answering a name."""

    async def answer(call: ServiceCall) -> dict:
        return {"data": {"name": "Pasta"}}

    hass.services.async_register(
        "ai_task",
        "generate_data",
        answer,
        schema=vol.Schema({vol.Required("structure"): AI_STRUCTURE}, extra=vol.ALLOW_EXTRA),
        supports_response=SupportsResponse.ONLY,
    )


async def test_a_photo_request_goes_through(hass: HomeAssistant, ai_task):
    est = Estimator(hass, None, "Alex", None, None)
    assert await est._ai("estimate a meal from a photo", "A photo", PHOTO_STRUCTURE) == {"name": "Pasta"}


async def test_a_refused_request_is_the_ai_not_answering(hass: HomeAssistant, ai_task, caplog):
    nested = {"fields": {"veg_g_100": {"description": "Grams of veg", "required": True, "selector": NUMBER}}, "multiple": True}
    est = Estimator(hass, None, "Alex", None, None)
    with pytest.raises(HomeAssistantError, match="The AI didn't answer"):
        await est._ai("estimate a meal from a photo", "A photo", {"items": {"selector": {"object": nested}}})
    assert "ai_task refused the request to estimate a meal from a photo" in caplog.text
