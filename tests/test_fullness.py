"""Fullness (1.11.0): how full the person was a couple of hours after breakfast, lunch or dinner. One answer per day and
meal, for today and the 7 days before; None takes it back; read back with that meal's food as it is now."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
import voluptuous as vol

from custom_components.food_diary.const import CONF_PERSON, CONF_WEBHOOK_ID, DOMAIN
from custom_components.food_diary.fullness import KEEP_DAYS, Fullness

from .conftest import SAM
from .test_food_diary import call


def day(n: int) -> str:
    return (dt_util.now().date() + timedelta(days=n)).isoformat()


async def answer(hass: HomeAssistant, meal: str, fullness: str | None, d: str | None = None, **extra) -> dict:
    return await call(hass, "set_fullness", {"meal": meal, "fullness": fullness, **({"date": d} if d else {}), **extra})


async def log(hass: HomeAssistant, name: str, kcal: float, meal: str, d: str | None = None) -> dict:
    data = {"name": name, "kcal": kcal, "meal": meal, **({"date": d} if d else {})}
    return (await call(hass, "log_food", data))["entry"]


async def test_an_answer_is_kept_on_the_day_and_taken_back_with_none(hass: HomeAssistant, setup):
    assert await answer(hass, "lunch", "full") == {"date": day(0), "meal": "lunch", "fullness": "full"}
    await answer(hass, "breakfast", "hungry")
    assert (await call(hass, "get_day"))["fullness"] == {"breakfast": "hungry", "lunch": "full"}
    assert (await answer(hass, "lunch", None))["fullness"] is None
    assert (await call(hass, "get_day"))["fullness"] == {"breakfast": "hungry"}
    await answer(hass, "breakfast", None)
    assert (await call(hass, "get_day"))["fullness"] == {} and setup.runtime_data.diary.data["fullness"] == {}


async def test_the_same_answer_again_changes_nothing(hass: HomeAssistant, setup):
    await answer(hass, "dinner", "satisfied")
    kept = dict(setup.runtime_data.diary.data["fullness"])
    assert (await answer(hass, "dinner", "satisfied"))["fullness"] == "satisfied"
    assert setup.runtime_data.diary.data["fullness"] == kept == {day(0): {"dinner": "satisfied"}}
    assert (await answer(hass, "dinner", "full"))["fullness"] == "full"  # a new answer replaces it


@pytest.mark.parametrize("back", [0, -1, -7])
async def test_today_and_the_seven_days_before(hass: HomeAssistant, setup, back):
    assert (await answer(hass, "lunch", "full", day(back)))["date"] == day(back)


async def test_not_older_than_seven_days(hass: HomeAssistant, setup):
    with pytest.raises(ServiceValidationError, match="7 days before"):
        await answer(hass, "lunch", "full", day(-8))


async def test_not_a_day_still_to_come(hass: HomeAssistant, setup):
    with pytest.raises(ServiceValidationError, match="hasn't come yet"):
        await answer(hass, "lunch", "full", day(1))
    assert setup.runtime_data.diary.data.get("fullness", {}) == {}


@pytest.mark.parametrize(("meal", "fullness"), [("snack", "full"), ("brunch", "full"), ("lunch", "stuffed"), ("lunch", "")])
async def test_only_the_three_meals_and_the_three_answers(hass: HomeAssistant, setup, meal, fullness):
    with pytest.raises(vol.Invalid):
        await answer(hass, meal, fullness)


async def test_answers_come_back_newest_first_with_the_meals_food_now(hass: HomeAssistant, setup):
    await log(hass, "Porridge", 320, "breakfast", day(-2))
    await answer(hass, "breakfast", "full", day(-2))
    await answer(hass, "lunch", "hungry", day(-1))  # nothing logged for it: no foods
    await answer(hass, "lunch", "satisfied")
    await answer(hass, "dinner", "full")
    await log(hass, "Salad", 180, "lunch")  # logged after the answer: read back with it
    curry = await log(hass, "Curry", 650, "lunch")
    await log(hass, "Crisps", 150, "snack")  # another meal: not lunch's
    out = (await call(hass, "get_fullness"))["answers"]
    assert [(a["date"], a["meal"], a["fullness"]) for a in out] == [
        (day(0), "dinner", "full"),
        (day(0), "lunch", "satisfied"),
        (day(-1), "lunch", "hungry"),
        (day(-2), "breakfast", "full"),
    ]
    assert out[1]["kcal"] == 830 and out[1]["foods"] == [{"name": "Curry", "kcal": 650}, {"name": "Salad", "kcal": 180}]
    assert (out[0]["kcal"], out[0]["foods"], out[2]["foods"]) == (0, [], [])
    assert out[3]["foods"] == [{"name": "Porridge", "kcal": 320}]
    await call(hass, "update_food", {"entry_id": curry["id"], "portions": 0.5}, response=False)  # read at read time
    assert (await call(hass, "get_fullness"))["answers"][1]["kcal"] == 505


async def test_get_fullness_reads_the_days_asked(hass: HomeAssistant, setup):
    await answer(hass, "lunch", "full", day(-7))
    await answer(hass, "lunch", "full")
    assert len((await call(hass, "get_fullness", {"days": 7}))["answers"]) == 1
    assert len((await call(hass, "get_fullness", {"days": 8}))["answers"]) == 2
    with pytest.raises(vol.Invalid):
        await call(hass, "get_fullness", {"days": 401})


async def test_a_client_id_sets_it_once(hass: HomeAssistant, setup):
    first = await answer(hass, "lunch", "full", client_id="answer-0001")
    await answer(hass, "lunch", "hungry")  # a newer answer
    late = await answer(hass, "lunch", "full", client_id="answer-0001")  # the first sent again, late
    assert first == {"date": day(0), "meal": "lunch", "fullness": "full"}
    assert late == {"date": day(0), "meal": "lunch", "fullness": "hungry", "duplicate": True}
    with pytest.raises(ServiceValidationError) as err:
        await answer(hass, "lunch", "satisfied", client_id="answer-0001")
    assert err.value.translation_key == "client_id_reused"


async def test_a_refused_answer_doesnt_use_up_its_client_id(hass: HomeAssistant, setup):
    with pytest.raises(ServiceValidationError):
        await answer(hass, "lunch", "full", day(1), client_id="answer-0002")
    assert "answer-0002" not in setup.runtime_data.diary.client_ids


async def test_answers_are_each_persons_own(hass: HomeAssistant, setup):
    sam = MockConfigEntry(
        domain=DOMAIN,
        title="Sam's food diary",
        unique_id="person.sam",
        data={CONF_PERSON: "person.sam", CONF_WEBHOOK_ID: "food_diary_test_hook_sam"},
    )
    sam.add_to_hass(hass)
    assert await hass.config_entries.async_setup(sam.entry_id)
    await hass.async_block_till_done()
    await answer(hass, "lunch", "full")
    await call(hass, "set_fullness", {"meal": "lunch", "fullness": "hungry"}, user=SAM)
    assert (await call(hass, "get_day"))["fullness"] == {"lunch": "full"}
    assert (await call(hass, "get_day", user=SAM))["fullness"] == {"lunch": "hungry"}
    assert [a["fullness"] for a in (await call(hass, "get_fullness", {"person": "person.sam"}))["answers"]] == ["hungry"]
    await hass.config_entries.async_unload(sam.entry_id)


async def test_answers_are_kept_over_a_restart_and_forgotten_after_400_days(hass: HomeAssistant, setup):
    await answer(hass, "lunch", "full")
    old = (dt_util.now().date() - timedelta(days=KEEP_DAYS + 1)).isoformat()
    setup.runtime_data.diary.data["fullness"][old] = {"dinner": "hungry"}
    await hass.config_entries.async_reload(setup.entry_id)
    await hass.async_block_till_done()
    assert setup.runtime_data.diary.data["fullness"] == {day(0): {"lunch": "full"}}
    assert (await call(hass, "get_day"))["fullness"] == {"lunch": "full"}


def test_prune_keeps_the_last_400_days():
    class FakeDiary:
        def __init__(self) -> None:
            self.data = {"fullness": {day(-KEEP_DAYS): {"lunch": "full"}, day(-KEEP_DAYS - 1): {"lunch": "full"}, day(0): {}}}
            self.changed = 0

        def async_changed(self) -> None:
            self.changed += 1

    diary = FakeDiary()
    Fullness(diary).prune()  # type: ignore[arg-type]
    assert diary.data["fullness"] == {day(-KEEP_DAYS): {"lunch": "full"}} and diary.changed == 1
