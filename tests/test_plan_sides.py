"""Sides on a planned meal (sources.py `sides`): each side is its own diary entry (plan_key "<date>|<meal>|side:<n>", note
"With <meal>") once the meal's planned entry is there; nothing when the meal was logged first; sides added later are
added then; they follow the plan (taken off, re-ordered, renamed by the plan) and a following plan is told the person's
deletes, renames and Undos of them with `side`."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant, callback
import pytest

from custom_components.food_diary.sources import async_register_source, plan_slot

from .test_food_diary import call
from .test_plan_follows import Following, entries
from .test_sources import CHILLI, Fake, day, use_source

RICE = {"id": "ig-2", "name_en": "Rice", "ingredients": [{"name": "rice", "amount": "300 g"}], "servings": 4}
SIDES = ["Rice", "Sour cream", "Tortilla chips"]


def dinner(*sides: Any) -> list[dict[str, Any]]:
    return [{"date": day(1), "dinner": {"name": "Chilli con carne", "dish_id": "ig-1", "sides": list(sides)}}]


class WithSides(Following):
    """A following plan that has sides: a side's change edits its sides list (by name), the meal's edits the meal."""

    @callback
    def follow(self, change: dict[str, Any]) -> None:
        if "side" not in change:
            super().follow(change)
            return
        self.told.append(change)
        days = [{k: (dict(v) if isinstance(v, dict) else v) for k, v in x.items()} for x in self._days or []]
        slot = next(x for x in days if x["date"] == change["date"])[change["meal"]]
        names = [s if isinstance(s, str) else s["name"] for s in slot.get("sides") or []]
        if change["action"] == "deleted":
            names = [n for n in names if n.lower() != change["name"].lower()]
        elif change["action"] == "renamed":
            names = [change["name"] if n.lower() == change["previous"].lower() else n for n in names]
        elif change["action"] == "restored" and change["name"].lower() not in {n.lower() for n in names}:
            names.insert(change["side"], change["name"])
        slot["sides"] = names
        self.change(days=days)


@pytest.fixture
async def kitchen(hass: HomeAssistant, setup, ai_calls):
    plan = WithSides([CHILLI, RICE], dinner(*SIDES))
    stop = async_register_source(hass, "kitchen", library=plan, plan=plan)
    await use_source(hass, setup)
    await call(hass, "sync_plan")
    plan.told.clear()
    yield plan
    stop()
    await hass.async_block_till_done()


async def dinners(hass: HomeAssistant, d: str | None = None) -> list[dict[str, Any]]:
    return (await entries(hass, d or day(1))).get("dinner", [])


def test_a_slot_reads_its_sides():
    slot = plan_slot(
        {"name": "Chilli", "sides": ["Rice", {"name": " rice "}, "", {"name": "Salsa", "dish_id": "d-9", "sides": ["x"]}]}
    )
    assert slot["sides"] == [{"name": "Rice"}, {"name": "Salsa", "dish_id": "d-9"}]
    assert "sides" not in plan_slot({"name": "Chilli", "sides": []})
    assert len(plan_slot({"name": "Chilli", "sides": [f"S{i}" for i in range(9)]})["sides"]) == 6


async def test_each_side_is_its_own_entry_after_the_meal(hass: HomeAssistant, setup, kitchen):
    got = await dinners(hass)
    assert [(e["name"], e["plan_key"], e.get("note")) for e in got] == [
        ("Chilli con carne", f"{day(1)}|dinner", "From the meal plan"),
        ("Rice", f"{day(1)}|dinner|side:0", "With Chilli con carne"),
        ("Sour cream", f"{day(1)}|dinner|side:1", "With Chilli con carne"),
        ("Tortilla chips", f"{day(1)}|dinner|side:2", "With Chilli con carne"),
    ]
    assert all(e["source"] == "plan" and e["portions"] == 1 and e["kcal"] for e in got)
    assert got[1]["ref"] == "ig-2" and got[2]["ref"] == ""  # a recipe side counts its recipe; a food side its name
    await call(hass, "sync_plan")
    assert len(await dinners(hass)) == 4 and kitchen.told == []


async def test_logged_first_means_nothing_from_the_plan(hass: HomeAssistant, setup, ai_calls):
    plan = WithSides([CHILLI, RICE], [])
    stop = async_register_source(hass, "kitchen", library=plan, plan=plan)
    await use_source(hass, setup)
    await call(hass, "log_food", {"name": "Toast", "kcal": 200, "meal": "dinner", "date": day(1)})
    plan.change(days=dinner(*SIDES))
    await call(hass, "sync_plan")
    assert [e["name"] for e in await dinners(hass)] == ["Toast"]
    stop()
    await hass.async_block_till_done()


async def test_a_side_planned_later_is_added_and_one_taken_off_goes(hass: HomeAssistant, setup, kitchen):
    kitchen.change(days=dinner("Rice", "Tortilla chips", "Guacamole"))
    await call(hass, "sync_plan")
    got = {e["name"]: e for e in await dinners(hass)}
    assert set(got) == {"Chilli con carne", "Rice", "Tortilla chips", "Guacamole"}
    assert got["Tortilla chips"]["plan_key"] == f"{day(1)}|dinner|side:1"  # kept (re-keyed), not made again
    assert got["Guacamole"]["plan_key"] == f"{day(1)}|dinner|side:2"
    kitchen.change(days=[{"date": day(1), "dinner": None}])
    await call(hass, "sync_plan")
    assert await dinners(hass) == [] and kitchen.told == []


async def test_an_edited_side_stays_when_taken_off(hass: HomeAssistant, setup, kitchen):
    rice = next(e for e in await dinners(hass) if e["name"] == "Rice")
    await call(hass, "update_food", {"entry_id": rice["id"], "date": day(1), "kcal": 321})
    kitchen.change(days=dinner("Sour cream", "Tortilla chips"))
    await call(hass, "sync_plan")
    assert "Rice" in [e["name"] for e in await dinners(hass)]


async def test_deleting_renaming_and_undo_of_a_side_are_told(hass: HomeAssistant, setup, kitchen):
    got = {e["name"]: e for e in await dinners(hass)}
    await call(hass, "delete_food", {"entry_id": got["Sour cream"]["id"], "date": day(1)})
    assert kitchen.told[-1] == {"action": "deleted", "date": day(1), "meal": "dinner", "name": "Sour cream", "side": 1}
    await call(hass, "sync_plan")
    assert [e["name"] for e in await dinners(hass)] == ["Chilli con carne", "Rice", "Tortilla chips"]  # not put back
    e = got["Sour cream"]
    back = {k: e[k] for k in ("name", "meal", "source", "ref", "note", "kcal", "plan_key")}
    await call(hass, "log_food", {**back, "date": day(1)})  # Undo
    assert kitchen.told[-1] == {"action": "restored", "date": day(1), "meal": "dinner", "name": "Sour cream", "side": 1}
    await call(hass, "sync_plan")
    names = [e["name"] for e in await dinners(hass)]
    assert names.count("Sour cream") == 1 and len(names) == 4
    chips = next(e for e in await dinners(hass) if e["name"] == "Tortilla chips")
    await call(hass, "update_food", {"entry_id": chips["id"], "date": day(1), "name": "Nachos"})
    assert kitchen.told[-1] == {
        "action": "renamed",
        "date": day(1),
        "meal": "dinner",
        "name": "Nachos",
        "previous": "Tortilla chips",
        "side": 2,
    }
    await call(hass, "sync_plan")
    assert next(e for e in await dinners(hass) if e["id"] == chips["id"])["name"] == "Nachos"


async def test_moving_a_side_takes_it_off_and_moving_it_back_puts_it_back(hass: HomeAssistant, setup, kitchen):
    rice = next(e for e in await dinners(hass) if e["name"] == "Rice")
    await call(hass, "update_food", {"entry_id": rice["id"], "date": day(1), "meal": "lunch"})
    assert kitchen.told[-1] == {"action": "deleted", "date": day(1), "meal": "dinner", "name": "Rice", "side": 0}
    moved = (await entries(hass, day(1)))["lunch"][0]
    assert "plan_key" not in moved and moved["plan_was"] == f"{day(1)}|dinner|side:0"
    await call(hass, "update_food", {"entry_id": rice["id"], "date": day(1), "meal": "dinner"})
    assert kitchen.told[-1] == {"action": "restored", "date": day(1), "meal": "dinner", "name": "Rice", "side": 0}


async def test_a_side_she_logged_by_hand_becomes_the_planned_one(hass: HomeAssistant, setup, kitchen):
    await call(hass, "log_food", {"name": "Guacamole", "kcal": 150, "meal": "dinner", "date": day(1)})
    kitchen.change(days=dinner(*SIDES, "Guacamole"))
    await call(hass, "sync_plan")
    (g,) = [e for e in await dinners(hass) if e["name"] == "Guacamole"]
    assert g["plan_key"] == f"{day(1)}|dinner|side:3" and g["kcal"] == 150


async def test_a_side_key_for_another_meal_is_dropped(hass: HomeAssistant, setup, kitchen):
    await call(
        hass, "log_food", {"name": "Rice", "kcal": 200, "meal": "lunch", "date": day(1), "plan_key": f"{day(1)}|dinner|side:0"}
    )
    (rice,) = (await entries(hass, day(1)))["lunch"]
    assert "plan_key" not in rice and kitchen.told == []


async def test_a_plan_that_doesnt_follow_remembers_a_deleted_side(hass: HomeAssistant, setup, ai_calls):
    plan = Fake([CHILLI, RICE], dinner(*SIDES))
    stop = async_register_source(hass, "kitchen", library=plan, plan=plan)
    await use_source(hass, setup)
    await call(hass, "sync_plan")
    rice = next(e for e in await dinners(hass) if e["name"] == "Rice")
    await call(hass, "delete_food", {"entry_id": rice["id"], "date": day(1)})
    await call(hass, "sync_plan")
    assert "Rice" not in [e["name"] for e in await dinners(hass)]
    stop()
    await hass.async_block_till_done()
