"""Food diary by voice: logging, what's left, taking the last one off, whose diary, and the sentences end to end."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
import shutil
from unittest.mock import patch

from homeassistant.components import conversation
from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import intent
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_capture_events

from custom_components.food_diary.const import CONF_PERSON, CONF_WEBHOOK_ID, DOMAIN, EVENT_LOGGED
from custom_components.food_diary.intent import (
    INTENT_LEFT,
    INTENT_LOG,
    INTENT_UNDO,
    NO_DIARY,
    NO_ESTIMATE,
    NOTHING_TO_UNDO,
    WHOSE,
)

from .conftest import ALEX, SAM

SENTENCES = Path(__file__).parent.parent / "custom_sentences" / "en" / "food_diary.yaml"
KCAL = "sensor.alex_food_diary_calories_today"


@pytest.fixture
async def voice(hass: HomeAssistant, setup):
    """Alex's diary with a 1,400 calorie goal; Home Assistant's intent integration registers the voice intents by itself."""
    assert await async_setup_component(hass, "intent", {})
    await hass.async_block_till_done()
    assert {INTENT_LOG, INTENT_LEFT, INTENT_UNDO} <= {h.intent_type for h in intent.async_get(hass)}
    setup.runtime_data.diary.set_goals({"kcal": 1400})
    return setup


async def say(hass: HomeAssistant, intent_type: str, user: str | None = None, **slots: str) -> str:
    """What Assist says back (a voice satellite: no user)."""
    r = await intent.async_handle(
        hass, "conversation", intent_type, {k: {"value": v} for k, v in slots.items()}, context=Context(user_id=user)
    )
    return r.speech["plain"]["speech"]


async def log(hass: HomeAssistant, name: str, kcal: float, **extra) -> None:
    await hass.services.async_call(
        DOMAIN, "log_food", {"name": name, "kcal": kcal, **extra}, blocking=True, context=Context(user_id=ALEX)
    )


def today_entries(entry) -> list[dict]:
    return entry.runtime_data.diary.entries(dt_util.now().date().isoformat())


# ---------- logging ----------


async def test_log_by_voice(hass: HomeAssistant, voice, ai_calls, notes):
    events = async_capture_events(hass, EVENT_LOGGED)
    spoken = await say(hass, INTENT_LOG, food="porridge with banana.", meal="breakfast")
    assert spoken == "Logged eggs on toast for breakfast, 315 calories. 1,085 left today."
    assert ai_calls[-1]["task"] == "estimate a meal from words" and '"porridge with banana"' in ai_calls[-1]["instructions"]
    [e] = today_entries(voice)
    assert e["source"] == "voice" and e["meal"] == "breakfast" and e["kcal"] == 315 and e["protein_g"] == 16
    assert float(hass.states.get(KCAL).state) == 315
    assert events[0].data == {
        "person": "person.alex",
        "date": dt_util.now().date().isoformat(),
        "entry_id": e["id"],
        "name": "Eggs on toast",
        "meal": "breakfast",
        "kcal": 315,
        "source": "voice",
    }
    # the phone gets the usual notification, and its Undo works
    await hass.async_block_till_done()
    note = notes[-1].data
    assert note["title"] == "Logged: Eggs on toast" and note["message"] == "315 kcal · 1085 left today"
    hass.bus.async_fire("mobile_app_notification_action", {"action": note["data"]["actions"][0]["action"]})
    await hass.async_block_till_done()
    assert today_entries(voice) == []


async def test_log_meal_by_time_snack_and_over(hass: HomeAssistant, voice, freezer):
    freezer.move_to(dt_util.start_of_local_day() + timedelta(hours=13))
    assert await say(hass, INTENT_LOG, food="soup") == "Logged eggs on toast for lunch, 315 calories. 1,085 left today."
    voice.runtime_data.diary.set_goals({"kcal": 600})
    assert (
        await say(hass, INTENT_LOG, food="a kitkat", meal="snack")
        == "Logged eggs on toast as a snack, 315 calories. 30 over today."
    )
    voice.runtime_data.diary.set_goals({"kcal": 0})  # no goal: just what was logged
    assert await say(hass, INTENT_LOG, food="toast", meal="dinner") == "Logged eggs on toast for dinner, 315 calories."


@pytest.mark.parametrize("ai", [{"name": "A bad day", "kcal": 0}, HomeAssistantError("The AI didn't answer")])
async def test_log_when_the_estimate_fails(hass: HomeAssistant, voice, notes, ai):
    async def fake_ai(self, *args, **kwargs):
        if isinstance(ai, Exception):
            raise ai
        return ai

    with patch("custom_components.food_diary.estimate.Estimator._ai", fake_ai):
        assert await say(hass, INTENT_LOG, food="a bad day") == NO_ESTIMATE
    await hass.async_block_till_done()
    assert today_entries(voice) == [] and notes == []


# ---------- what's left ----------


async def test_calories_left(hass: HomeAssistant, voice):
    assert await say(hass, INTENT_LEFT) == "1,400 calories left today, of 1,400."
    await log(hass, "Porridge", 580)
    assert await say(hass, INTENT_LEFT) == "820 calories left today, of 1,400."
    await log(hass, "Pizza", 700)
    await log(hass, "Ice cream", 220)
    assert await say(hass, INTENT_LEFT) == "100 calories over today, of 1,400."
    voice.runtime_data.diary.set_goals({"kcal": 0})
    assert await say(hass, INTENT_LEFT) == "1,500 calories so far today."


# ---------- taking the last one off ----------


async def test_undo_last(hass: HomeAssistant, voice, notes):
    assert await say(hass, INTENT_UNDO) == NOTHING_TO_UNDO
    diary = voice.runtime_data.diary
    today = dt_util.now().date().isoformat()
    yesterday = (dt_util.now().date() - timedelta(days=1)).isoformat()
    await log(hass, "Wine", 200, date=yesterday)
    await say(hass, INTENT_LOG, food="eggs on toast", meal="breakfast")
    await log(hass, "Apple", 80)
    diary.add(today, {"name": "Fish pie", "kcal": 500, "meal": "dinner", "source": "plan", "plan_key": f"{today}|dinner"})
    assert await say(hass, INTENT_UNDO) == "Took apple off."
    await hass.async_block_till_done()
    assert notes[-1].data["title"] == "Removed" and notes[-1].data["message"] == "Apple is out of today's diary."
    assert await say(hass, INTENT_UNDO) == "Took eggs on toast off."
    assert await say(hass, INTENT_UNDO) == NOTHING_TO_UNDO  # the meal plan's dinner stays, and so does yesterday
    assert [e["name"] for e in diary.entries(today)] == ["Fish pie"] and len(diary.entries(yesterday)) == 1


# ---------- whose diary ----------


async def test_whose_diary(hass: HomeAssistant, voice):
    sam = MockConfigEntry(
        domain=DOMAIN,
        title="Sam's food diary",
        unique_id="person.sam",
        data={CONF_PERSON: "person.sam", CONF_WEBHOOK_ID: "food_diary_test_hook_sam"},
    )
    sam.add_to_hass(hass)
    assert await hass.config_entries.async_setup(sam.entry_id)
    await hass.async_block_till_done()
    # two diaries and a satellite with no user: ask, don't guess
    assert await say(hass, INTENT_LOG, food="toast") == WHOSE
    assert await say(hass, INTENT_LEFT) == WHOSE
    assert today_entries(voice) == [] and today_entries(sam) == []
    # from someone's own Assist (their user): their diary
    assert (
        await say(hass, INTENT_LOG, user=SAM, food="toast", meal="lunch")
        == "Logged eggs on toast for lunch, 315 calories. 1,685 left today."
    )
    assert len(today_entries(sam)) == 1 and today_entries(voice) == []
    assert await say(hass, INTENT_LEFT, user=ALEX) == "1,400 calories left today, of 1,400."
    for e in (voice, sam):
        assert await hass.config_entries.async_unload(e.entry_id)
    assert await say(hass, INTENT_LEFT) == NO_DIARY


# ---------- the sentences, through the default conversation agent ----------


@pytest.fixture
async def assist(hass: HomeAssistant, voice, tmp_path):
    """The default agent with food_diary.yaml installed in the config folder's custom_sentences/en."""
    hass.config.config_dir = str(tmp_path)
    (tmp_path / "custom_sentences" / "en").mkdir(parents=True)
    shutil.copy(SENTENCES, tmp_path / "custom_sentences" / "en" / "food_diary.yaml")
    assert await async_setup_component(hass, "homeassistant", {})
    assert await async_setup_component(hass, "conversation", {})
    return voice


async def converse(hass: HomeAssistant, text: str) -> str:
    r = await conversation.async_converse(hass, text, None, Context(), language="en")
    return r.response.speech["plain"]["speech"]


async def test_sentences_end_to_end(hass: HomeAssistant, assist, ai_calls):
    assert (
        await converse(hass, "I had porridge for breakfast")
        == "Logged eggs on toast for breakfast, 315 calories. 1,085 left today."
    )
    assert '"porridge"' in ai_calls[-1]["instructions"]
    assert await converse(hass, "How many calories have I got left?") == "1,085 calories left today, of 1,400."
    assert await converse(hass, "Take the last food off") == "Took eggs on toast off."
    assert today_entries(assist) == []


HEARD = {
    # logging: the meal word never ends up in the food
    "I had porridge for breakfast": ("FoodDiaryLog", {"food": "porridge", "meal": "breakfast"}),
    "I had porridge with banana for breakfast.": ("FoodDiaryLog", {"food": "porridge with banana", "meal": "breakfast"}),
    "I've just had an apple as a snack": ("FoodDiaryLog", {"food": "an apple", "meal": "snack"}),
    "I had fish and chips for my tea": ("FoodDiaryLog", {"food": "fish and chips", "meal": "dinner"}),
    "I had cereal for brekkie today": ("FoodDiaryLog", {"food": "cereal", "meal": "breakfast"}),
    "I ate a banana": ("FoodDiaryLog", {"food": "a banana"}),
    "I had a cup of tea": ("FoodDiaryLog", {"food": "a cup of tea"}),
    "For lunch I had a ham sandwich": ("FoodDiaryLog", {"meal": "lunch", "food": "a ham sandwich"}),
    "Log a KitKat as a snack": ("FoodDiaryLog", {"food": "a KitKat", "meal": "snack"}),
    "log two crumpets": ("FoodDiaryLog", {"food": "two crumpets"}),
    "Add a yoghurt to my food diary": ("FoodDiaryLog", {"food": "a yoghurt"}),
    "add porridge for breakfast to my food diary": ("FoodDiaryLog", {"food": "porridge", "meal": "breakfast"}),
    "log a banana in my food diary for lunch": ("FoodDiaryLog", {"food": "a banana", "meal": "lunch"}),
    # what's left
    "how many calories have I got left": ("FoodDiaryLeft", {}),
    "how many calories do I have left today": ("FoodDiaryLeft", {}),
    "calories left today": ("FoodDiaryLeft", {}),
    "how am I doing on calories": ("FoodDiaryLeft", {}),
    # taking it off
    "take the last food off": ("FoodDiaryUndo", {}),
    "remove the last thing I logged": ("FoodDiaryUndo", {}),
    "undo the last food": ("FoodDiaryUndo", {}),
    "take that off my food diary": ("FoodDiaryUndo", {}),
}
NOT_OURS = [
    "add milk to the shopping list",
    "add porridge for breakfast",
    "add tacos to Friday",
    "we're having curry for dinner",
    "remove milk from the shopping list",
    "undo that",
    "undo",
    "what's for dinner",
    "what's for breakfast tomorrow",
    "how many lights are on",
    "take the bins out",
    "I'm having a lie in tomorrow",
]


async def test_sentences_heard(hass: HomeAssistant, assist, hass_ws_client):
    client = await hass_ws_client(hass)
    sentences = list(HEARD) + NOT_OURS
    await client.send_json_auto_id({"type": "conversation/agent/homeassistant/debug", "sentences": sentences, "language": "en"})
    msg = await client.receive_json()
    assert msg["success"]
    got = dict(zip(sentences, msg["result"]["results"], strict=True))
    for text, (name, slots) in HEARD.items():
        r = got[text]
        assert r and r["match"] and r["source"] == "custom", text
        values = {k: d["value"] for k, d in r["details"].items()}  # what the handler gets ("a snack" -> snack, "tea" -> dinner)
        assert (r["intent"]["name"], values) == (name, slots), text
    for text in NOT_OURS:
        r = got[text]
        assert not (r and r["intent"]["name"].startswith("FoodDiary")), f"{text} -> {r}"
