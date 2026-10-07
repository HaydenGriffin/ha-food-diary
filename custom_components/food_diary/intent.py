"""Voice: the food diary's Assist intents.

  FoodDiaryLog   "I had porridge for breakfast", "log a banana": the food is worked out from the words (the diary's AI), logged
                 on today's diary (source "voice") with the usual phone notification and Undo, and Assist says what it logged.
  FoodDiaryLeft  "how many calories have I got left": today's calories left (or over) against the goal.
  FoodDiaryUndo  "take the last food off": the latest entry logged today comes off (never a planned one).

The sentences ship in custom_sentences/en/food_diary.yaml; copying it into the config folder's custom_sentences/en/ turns them
on (see docs/integration.md). A voice satellite has no user, so the diary is the speaker's own when Home Assistant knows who
they are, else the only one; with several diaries and no way to tell, Assist asks rather than guesses.
"""

from __future__ import annotations

from typing import Any, ClassVar

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv, intent
import voluptuous as vol

from .const import MEALS
from .diary import Diary, today
from .notifications import fire_logged, notify_logged, notify_removed
from .services import loaded

INTENT_LOG = "FoodDiaryLog"
INTENT_LEFT = "FoodDiaryLeft"
INTENT_UNDO = "FoodDiaryUndo"

NO_DIARY = "The food diary isn't set up yet."
WHOSE = "I can't tell whose food diary to use. Try again from your own device."
NO_ESTIMATE = "I couldn't work out the calories for that."
NOTHING_TO_UNDO = "There's nothing logged today to take off."
MEAL_WORDS = {"breakfast": "for breakfast", "lunch": "for lunch", "dinner": "for dinner", "snack": "as a snack"}


async def async_setup_intents(hass: HomeAssistant) -> None:
    """Register the voice intents. Home Assistant's intent integration calls this itself (this module is food_diary's intent
    platform), once food_diary is loaded; nothing else needs to."""
    for handler in (LogFoodHandler(), CaloriesLeftHandler(), UndoLastHandler()):
        intent.async_register(hass, handler)


def whose(intent_obj: intent.Intent, entries: list[ConfigEntry]) -> ConfigEntry | None:
    """The speaker's own diary when Home Assistant knows them, else the only one; None when it can't tell."""
    if user := intent_obj.context.user_id:
        for entry in entries:
            state = intent_obj.hass.states.get(entry.runtime_data.person)
            if state and state.attributes.get("user_id") == user:
                return entry
    return entries[0] if len(entries) == 1 else None


def spoken(name: str) -> str:
    """'Porridge with banana' -> 'porridge with banana' (names like 'BLT' stay as they are)."""
    return name[:1].lower() + name[1:] if name[1:2].islower() else name


def calories(n: float) -> str:
    return f"{round(n):,}"


def left_today(diary: Diary, day: str) -> str:
    """' 1,050 left today.' / ' 120 over today.' / '' without a calorie goal."""
    left = diary.day(day)["left"].get("kcal")
    if left is None:
        return ""
    return f" {calories(left)} left today." if round(left) >= 0 else f" {calories(-left)} over today."


class DiaryIntentHandler(intent.IntentHandler):
    """Works out whose diary it is, then says what `answer` returns."""

    async def async_handle(self, intent_obj: intent.Intent) -> intent.IntentResponse:
        entries = loaded(intent_obj.hass)
        entry = whose(intent_obj, entries)
        text = await self.answer(intent_obj, entry) if entry else (WHOSE if entries else NO_DIARY)
        response = intent_obj.create_response()
        response.async_set_speech(text)
        return response

    async def answer(self, intent_obj: intent.Intent, entry: ConfigEntry) -> str:
        raise NotImplementedError


class LogFoodHandler(DiaryIntentHandler):
    """'I had porridge for breakfast': estimate it from the words, log it today, say what went in and what's left."""

    intent_type = INTENT_LOG
    slot_schema: ClassVar[dict[vol.Marker, Any]] = {vol.Required("food"): cv.string, vol.Optional("meal"): vol.In(MEALS)}

    async def answer(self, intent_obj: intent.Intent, entry: ConfigEntry) -> str:
        slots: dict[str, Any] = self.async_validate_slots(intent_obj.slots)
        food = slots["food"]["value"].strip(" .,!?")
        meal = slots.get("meal", {}).get("value")  # none said: the diary picks it by the time of day
        data = entry.runtime_data
        try:
            found = await data.estimator.text(food)
        except HomeAssistantError:
            return NO_ESTIMATE
        day = today()
        e = data.diary.add(day, {**found, "meal": meal, "source": "voice"})
        fire_logged(intent_obj.hass, data, day, e, intent_obj.context)
        await notify_logged(intent_obj.hass, data, entry.entry_id, day, e)
        return f"Logged {spoken(e['name'])} {MEAL_WORDS[e['meal']]}, {calories(e['kcal'])} calories.{left_today(data.diary, day)}"


class CaloriesLeftHandler(DiaryIntentHandler):
    """'How many calories have I got left': '820 calories left today, of 2,000.'"""

    intent_type = INTENT_LEFT

    async def answer(self, intent_obj: intent.Intent, entry: ConfigEntry) -> str:
        day = entry.runtime_data.diary.day(today())
        left = day["left"].get("kcal")
        if left is None:  # no calorie goal set
            return f"{calories(day['totals']['kcal'])} calories so far today."
        goal = calories(day["goals"]["kcal"])
        if round(left) >= 0:
            return f"{calories(left)} calories left today, of {goal}."
        return f"{calories(-left)} calories over today, of {goal}."


class UndoLastHandler(DiaryIntentHandler):
    """'Take the last food off': removes the latest entry logged today, whatever logged it, except the meal plan."""

    intent_type = INTENT_UNDO

    async def answer(self, intent_obj: intent.Intent, entry: ConfigEntry) -> str:
        data = entry.runtime_data
        day = today()
        last = next((e for e in reversed(data.diary.entries(day)) if e.get("source") != "plan"), None)
        if last is None:
            return NOTHING_TO_UNDO
        data.diary.delete(day, last["id"])
        await notify_removed(intent_obj.hass, data, last)
        return f"Took {spoken(last['name'])} off."
