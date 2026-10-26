"""A meal plan that follows the diary (sources.py `follow`): the person's deletes, renames, moves and Undos of planned entries
are told to the plan at once, the planner never undoes them, and setting the plan to what was logged makes that entry the
planned one."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant, callback
import pytest

from custom_components.food_diary.sources import async_register_source

from .test_food_diary import call
from .test_sources import CHILLI, Fake, day, use_source


class Following(Fake):
    """A plan that follows the diary the way a provider should: it changes what days() says at once, and keeps a log."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.told: list[dict[str, Any]] = []

    @callback
    def follow(self, change: dict[str, Any]) -> None:
        self.told.append(change)
        days = [dict(x) for x in self._days or []]
        row = next((x for x in days if x["date"] == change["date"]), None)
        if row is None:
            row = {"date": change["date"]}
            days.append(row)
        if change["action"] == "moved":
            row[change["from"]] = None
        row[change["meal"]] = None if change["action"] == "deleted" else change["name"]
        self.change(days=days)

    def slot(self, d: str, meal: str) -> Any:
        return next((x.get(meal) for x in self._days or [] if x["date"] == d), None)


@pytest.fixture
async def kitchen(hass: HomeAssistant, setup, ai_calls):
    plan = Following([CHILLI], [{"date": day(1), "dinner": "Chilli con carne", "lunch": "Soup"}])
    stop = async_register_source(hass, "kitchen", library=plan, plan=plan)
    await use_source(hass, setup)
    await call(hass, "sync_plan")
    plan.told.clear()
    yield plan
    stop()
    await hass.async_block_till_done()


async def entries(hass: HomeAssistant, d: str) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for e in (await call(hass, "get_day", {"date": d}))["entries"]:
        out.setdefault(e["meal"], []).append(e)
    return out


async def planned(hass: HomeAssistant, meal: str, d: str | None = None) -> dict[str, Any]:
    (e,) = (await entries(hass, d or day(1)))[meal]
    return e


async def test_deleting_a_planned_entry_takes_it_off_the_plan(hass: HomeAssistant, setup, kitchen):
    e = await planned(hass, "dinner")
    await call(hass, "delete_food", {"entry_id": e["id"], "date": day(1)})
    assert kitchen.told == [{"action": "deleted", "date": day(1), "meal": "dinner", "name": "Chilli con carne"}]
    assert kitchen.slot(day(1), "dinner") is None
    assert setup.runtime_data.diary.data.get("dismissed", {}) == {}  # the plan took it: nothing to remember
    await call(hass, "sync_plan")
    assert "dinner" not in await entries(hass, day(1))
    kitchen.change(days=[{"date": day(1), "dinner": "Chilli con carne", "lunch": "Soup"}])  # planned again later
    await call(hass, "sync_plan")
    assert (await planned(hass, "dinner"))["name"] == "Chilli con carne"  # not blocked by an old dismissal


async def test_the_planners_own_removals_are_not_told(hass: HomeAssistant, setup, kitchen):
    kitchen.change(days=[{"date": day(1), "dinner": None, "lunch": "Salad"}])
    await call(hass, "sync_plan")
    assert kitchen.told == []
    assert [e["name"] for e in (await entries(hass, day(1)))["lunch"]] == ["Salad"]


async def test_undo_relogs_with_its_plan_key_and_the_plan_follows(hass: HomeAssistant, setup, kitchen):
    e = await planned(hass, "dinner")
    await call(hass, "delete_food", {"entry_id": e["id"], "date": day(1)})
    back = {k: e[k] for k in ("name", "meal", "source", "ref", "note", "kcal", "protein_g", "plan_key")}
    await call(hass, "log_food", {**back, "date": day(1)})
    assert kitchen.told[-1] == {"action": "restored", "date": day(1), "meal": "dinner", "name": "Chilli con carne"}
    assert kitchen.slot(day(1), "dinner") == "Chilli con carne"
    await call(hass, "sync_plan")
    again = await planned(hass, "dinner")
    assert again["plan_key"] == f"{day(1)}|dinner" and again["kcal"] == e["kcal"]  # one entry, linked again


async def test_a_plan_key_for_another_slot_is_dropped(hass: HomeAssistant, setup, kitchen):
    await call(
        hass, "log_food", {"name": "Toast", "kcal": 200, "meal": "breakfast", "date": day(1), "plan_key": f"{day(1)}|dinner"}
    )
    (toast,) = (await entries(hass, day(1)))["breakfast"]
    assert "plan_key" not in toast and kitchen.told == []


async def test_restore_snapshot_puts_the_plan_back(hass: HomeAssistant, setup, kitchen):
    e = await planned(hass, "dinner")
    await call(hass, "snapshot", {"token": "undo-1234", "entry_id": e["id"], "date": day(1)})
    await call(hass, "delete_food", {"entry_id": e["id"], "date": day(1)})
    await call(hass, "restore_snapshot", {"token": "undo-1234"})
    assert [c["action"] for c in kitchen.told] == ["deleted", "restored"]
    assert kitchen.slot(day(1), "dinner") == "Chilli con carne"


async def test_a_rename_is_kept_and_the_plan_takes_the_new_name(hass: HomeAssistant, setup, kitchen):
    e = await planned(hass, "dinner")
    await call(hass, "update_food", {"entry_id": e["id"], "date": day(1), "portions": 2})
    await call(hass, "update_food", {"entry_id": e["id"], "date": day(1), "name": "Chilli with rice"})
    assert kitchen.told == [
        {"action": "renamed", "date": day(1), "meal": "dinner", "name": "Chilli with rice", "previous": "Chilli con carne"}
    ]
    await call(hass, "sync_plan")
    now = await planned(hass, "dinner")
    assert now["id"] == e["id"] and now["name"] == "Chilli with rice" and now["portions"] == 2  # never undone by the planner


async def test_edited_numbers_and_portions_survive_syncs(hass: HomeAssistant, setup, kitchen):
    e = await planned(hass, "dinner")
    await call(hass, "update_food", {"entry_id": e["id"], "date": day(1), "portions": 1.5})
    await call(hass, "sync_plan")
    assert (await planned(hass, "dinner"))["portions"] == 1.5
    await call(hass, "update_food", {"entry_id": e["id"], "date": day(1), "kcal": 777})
    await call(hass, "sync_plan")
    now = await planned(hass, "dinner")
    assert now["kcal"] == 777 and now["portions"] == 1.5 and kitchen.told == []


async def test_moving_a_planned_entry_moves_it_on_the_plan(hass: HomeAssistant, setup, kitchen):
    """Onto a meal that had another plan: that plan is replaced, and its planned entry goes."""
    e = await planned(hass, "dinner")
    await call(hass, "update_food", {"entry_id": e["id"], "date": day(1), "meal": "lunch"})
    assert kitchen.told == [
        {
            "action": "moved",
            "date": day(1),
            "meal": "lunch",
            "name": "Chilli con carne",
            "from": "dinner",
            "previous": "Chilli con carne",
        }
    ]
    assert kitchen.slot(day(1), "dinner") is None and kitchen.slot(day(1), "lunch") == "Chilli con carne"
    await call(hass, "sync_plan")
    got = await entries(hass, day(1))
    assert "dinner" not in got
    (lunch,) = got["lunch"]
    assert lunch["id"] == e["id"] and lunch["plan_key"] == f"{day(1)}|lunch"  # the soup's planned entry went


async def test_moving_to_a_snack_takes_it_off_the_plan(hass: HomeAssistant, setup, kitchen):
    e = await planned(hass, "dinner")
    await call(hass, "update_food", {"entry_id": e["id"], "date": day(1), "meal": "snack"})
    assert kitchen.told == [{"action": "deleted", "date": day(1), "meal": "dinner", "name": "Chilli con carne"}]
    await call(hass, "sync_plan")
    (snack,) = (await entries(hass, day(1)))["snack"]
    assert snack["id"] == e["id"] and "plan_key" not in snack
    await call(hass, "update_food", {"entry_id": e["id"], "date": day(1), "meal": "dinner"})  # Undo
    assert kitchen.told[-1] == {"action": "restored", "date": day(1), "meal": "dinner", "name": "Chilli con carne"}
    await call(hass, "sync_plan")
    back = await planned(hass, "dinner")
    assert back["id"] == e["id"] and back["plan_key"] == f"{day(1)}|dinner"


async def test_past_days_follow_too(hass: HomeAssistant, setup, kitchen):
    """The plan's history matches what was eaten; the planner still never touches past days."""
    await call(
        hass, "log_food", {"name": "Stew", "kcal": 500, "meal": "dinner", "date": day(-2), "plan_key": f"{day(-2)}|dinner"}
    )
    e = await planned(hass, "dinner", day(-2))
    await call(hass, "delete_food", {"entry_id": e["id"], "date": day(-2)})
    assert [(c["action"], c["date"]) for c in kitchen.told] == [("restored", day(-2)), ("deleted", day(-2))]


async def test_setting_the_plan_to_what_she_logged_takes_her_entry(hass: HomeAssistant, setup, kitchen):
    """'Replace the planned dish with this?' Yes: the plan says what she logged; the planned entry goes and hers becomes the
    planned one, numbers kept. When the plan moves on again, hers stays (just hers) and nothing is added on top."""
    await call(hass, "log_food", {"name": "Pizza", "kcal": 850, "meal": "dinner", "date": day(1)})
    kitchen.change(days=[{"date": day(1), "dinner": "Pizza", "lunch": "Soup"}])
    await call(hass, "sync_plan")
    (pizza,) = (await entries(hass, day(1)))["dinner"]
    assert pizza["name"] == "Pizza" and pizza["kcal"] == 850 and pizza["plan_key"] == f"{day(1)}|dinner"
    await call(hass, "sync_plan")
    assert (await planned(hass, "dinner"))["kcal"] == 850  # never renumbered: her own numbers
    kitchen.change(days=[{"date": day(1), "dinner": "Chilli con carne", "lunch": "Soup"}])
    await call(hass, "sync_plan")
    (still,) = (await entries(hass, day(1)))["dinner"]
    assert still["id"] == pizza["id"] and "plan_key" not in still
    assert kitchen.told == []


async def test_a_plan_that_doesnt_follow_still_remembers_deletes(hass: HomeAssistant, setup, ai_calls):
    plan = Fake([CHILLI], [{"date": day(1), "dinner": "Chilli con carne"}])
    stop = async_register_source(hass, "kitchen", library=plan, plan=plan)
    await use_source(hass, setup)
    await call(hass, "sync_plan")
    e = await planned(hass, "dinner")
    await call(hass, "delete_food", {"entry_id": e["id"], "date": day(1)})
    assert setup.runtime_data.diary.plan_follower is None
    assert setup.runtime_data.diary.data["dismissed"] == {f"{day(1)}|dinner": "chilli con carne"}
    await call(hass, "sync_plan")
    assert "dinner" not in await entries(hass, day(1))
    stop()
    await hass.async_block_till_done()


async def test_planned_food_logged_without_its_plan_key_is_just_food(hass: HomeAssistant, setup, kitchen):
    await call(hass, "log_food", {"name": "Soup", "kcal": 300, "meal": "lunch", "date": day(2), "source": "plan"})
    (soup,) = (await entries(hass, day(2)))["lunch"]
    assert soup["source"] == "again" and "plan_key" not in soup and kitchen.told == []


async def test_restore_food_puts_a_move_or_a_rename_back_on_the_plan(hass: HomeAssistant, setup, kitchen):
    """Undo of an update_food (1.10.0) is told to the plan like an update: off to a snack and back, a name and back."""
    e = await planned(hass, "dinner")
    for change, told in (
        ({"meal": "snack"}, {"action": "restored", "date": day(1), "meal": "dinner", "name": "Chilli con carne"}),
        (
            {"name": "Chilli with rice"},
            {"action": "renamed", "date": day(1), "meal": "dinner", "name": "Chilli con carne", "previous": "Chilli with rice"},
        ),
    ):
        out = await call(hass, "update_food", {"entry_id": e["id"], "date": day(1), **change})
        rev = out["entry"]["rev"]
        back = await call(
            hass, "restore_food", {"entry_id": e["id"], "date": day(1), "entry": out["before"], "expected_rev": rev}
        )
        assert kitchen.told[-1] == told
        assert back["entry"] == {**out["before"], "rev": rev + 1}
    assert kitchen.slot(day(1), "dinner") == "Chilli con carne"
