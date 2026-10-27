"""The look back's days (1.11.0): each day's calories per meal, fibre and veg, the week before's veg and fibre
(`last_week`), and up to 35 days at once (four weeks for weekdays against weekends)."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
import pytest
import voluptuous as vol

from .test_food_diary import call
from .test_week_features import day, log


async def review(hass: HomeAssistant, days: int = 7) -> dict:
    return await call(hass, "get_week_review", {"date": day(0), "days": days})


async def test_each_day_has_its_meals_fibre_and_veg(hass: HomeAssistant, setup):
    await log(hass, "Porridge", 320, day(0), "breakfast", fibre_g=5, veg_g=0)
    await log(hass, "Bean chilli", 600, day(0), "dinner", fibre_g=14, veg_g=200)
    await log(hass, "Rice", 200, day(0), "dinner", fibre_g=1)  # veg not known
    await log(hass, "Biscuits", 150, day(0), "snack")
    row = (await review(hass))["days"][-1]
    assert row["meals"] == {"breakfast": 320, "lunch": 0, "dinner": 800, "snack": 150}
    assert (row["fibre_g"], row["veg_g"], row["veg_known"], row["logged"], row["kcal"]) == (20, 200, 2, 4, 1270)
    empty = (await review(hass))["days"][0]
    assert empty["meals"] == {"breakfast": 0, "lunch": 0, "dinner": 0, "snack": 0}
    assert (empty["veg_g"], empty["veg_known"]) == (0, 0)


async def test_last_week_averages_the_days_that_know_veg(hass: HomeAssistant, setup):
    await log(hass, "Lentil soup", 300, day(-7), "lunch", fibre_g=10, veg_g=240)  # the day before the week: counted
    await log(hass, "Salad", 150, day(-9), "lunch", fibre_g=6, veg_g=160)
    await log(hass, "Toast", 200, day(-9), "breakfast", fibre_g=2)  # same day, no veg: its fibre still counts
    await log(hass, "Pizza", 800, day(-10), "dinner", fibre_g=4)  # no veg known that day: left out
    await log(hass, "Peas", 70, day(-14), "dinner", fibre_g=5, veg_g=80)  # 8 days before the week: not last week
    out = await review(hass)
    assert out["start"] == day(-6)
    assert out["last_week"] == {"veg_g_avg": 200, "fibre_g_avg": 9, "veg_days": 2}


async def test_last_week_without_veg_has_no_averages(hass: HomeAssistant, setup):
    await log(hass, "Toast", 200, day(-8), "breakfast", fibre_g=2)
    assert (await review(hass))["last_week"] == {"veg_g_avg": None, "fibre_g_avg": None, "veg_days": 0}


@pytest.mark.parametrize("days", [28, 35])
async def test_up_to_35_days(hass: HomeAssistant, setup, days):
    await log(hass, "Porridge", 320, day(-(days - 1)), "breakfast")
    out = await review(hass, days)
    assert len(out["days"]) == days and out["start"] == day(-(days - 1)) and out["days"][0]["meals"]["breakfast"] == 320


async def test_not_more_than_35_days(hass: HomeAssistant, setup):
    with pytest.raises(vol.Invalid):
        await review(hass, 36)
