"""The meal a food lands in when none was said (voice "log a biscuit"), by the time of day."""

from datetime import datetime

from homeassistant.util import dt as dt_util
import pytest

from custom_components.food_diary.diary import meal_now


@pytest.mark.parametrize(
    "at, meal",
    [
        ("09:00", "breakfast"),
        ("10:29", "breakfast"),
        ("13:00", "lunch"),
        ("14:29", "lunch"),
        ("14:30", "snack"),
        ("16:00", "snack"),
        ("17:30", "dinner"),
        ("21:00", "dinner"),
        ("23:00", "snack"),
    ],
)
def test_meal_now(freezer, at, meal):
    h, m = map(int, at.split(":"))
    freezer.move_to(datetime(2026, 10, 7, h, m, tzinfo=dt_util.get_default_time_zone()))  # local time, whatever the test's zone
    assert meal_now() == meal
