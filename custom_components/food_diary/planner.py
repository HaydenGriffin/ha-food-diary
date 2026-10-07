"""The meal plan → the food diary, so planned meals count in the day's calories ahead of time.

For today and the days ahead, each planned breakfast, lunch and dinner becomes a diary entry (source "plan", one portion,
plan_key "<date>|<meal>") with one portion's numbers, taken from (first that has them):
- the recipe book: numbers typed by the person ("own"), printed on the recipe ("recipe") or estimated before ("ai");
- the dish library's own per-portion `nutrition` for that recipe (kept in the book as "recipe" numbers);
- an estimate from the library dish's ingredients (kept);
- an estimate from the meal's name (kept under "name:<meal>").
Partial book numbers (a recipe that prints kcal and protein only) are completed once from an estimate of the dish (see
macros.py), and every sync also completes any other partial dish in the book.

Then:
- the plan changes → the entry is replaced; the slot empties → it's removed (unless its numbers were edited);
- the book's numbers for a planned meal change → its entry takes them (its portions stay), unless its numbers were edited;
- a planned entry that's deleted is remembered and not put back (until that slot's meal changes);
- something already logged in that meal on that day → the plan adds nothing (no double counting).
Past days are never touched.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime
import logging
from typing import TYPE_CHECKING, Any

from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_call_later, async_track_state_change_event, async_track_time_change

from .const import DOMAIN
from .diary import Book, nums, today
from .library import dish_names, ingredient_lines, plain_name, read_dishes, read_week
from .macros import complete, partial

if TYPE_CHECKING:
    from . import FoodDiaryData

_LOGGER = logging.getLogger(__name__)
PLANNED_MEALS = ("breakfast", "lunch", "dinner")
PLAN_NOTE = "From the meal plan"
TAKES_LIBRARY_NUMBERS = (None, "ai", "recipe")  # book sources the library's own numbers may replace

type Nutrition = tuple[dict[str, float], str]  # one portion's numbers, and the recipe id ("" when by name)


class Planner:
    """Keeps one diary in line with the meal plan sensor."""

    def __init__(self, hass: HomeAssistant, data: FoodDiaryData, plan_sensor: str, dishes_sensor: str | None) -> None:
        self.hass, self.data, self.plan_sensor, self.dishes_sensor = hass, data, plan_sensor, dishes_sensor
        self._pending: CALLBACK_TYPE | None = None
        self._running = False
        self._lock = asyncio.Lock()  # one sync at a time: sync_plan, or a plan change after it
        self.completed: list[str] = []  # the book keys the last sync completed

    @property
    def book(self) -> Book:
        return self.hass.data[DOMAIN]["dishes"]

    # ---------- running by itself ----------

    @callback
    def async_start(self) -> list[CALLBACK_TYPE]:
        """Listeners to stop on unload: plan or library changes (debounced), a new day, and a first run after start."""
        ids = [x for x in (self.plan_sensor, self.dishes_sensor) if x]
        return [
            async_track_state_change_event(self.hass, ids, self._changed),
            async_track_time_change(self.hass, self._new_day, hour=0, minute=1, second=0),
            self.schedule(20),
        ]

    @callback
    def _changed(self, _event: Event) -> None:
        self.schedule(10)

    @callback
    def _new_day(self, _now: datetime) -> None:
        self.schedule(5)

    @callback
    def schedule(self, delay: float) -> CALLBACK_TYPE:
        if self._pending:
            self._pending()
        self._pending = async_call_later(self.hass, delay, self._run)
        return self._cancel

    @callback
    def _cancel(self) -> None:
        if self._pending:
            self._pending()
            self._pending = None

    async def _run(self, _now: datetime | None = None) -> None:
        self._pending = None
        if self._running:
            self.schedule(30)
            return
        self._running = True
        try:
            await self.async_sync()
        except Exception:  # a plan that can't be read never breaks the diary
            _LOGGER.exception("Couldn't sync the meal plan into the food diary")
        finally:
            self._running = False

    # ---------- syncing ----------

    async def async_sync(self) -> int:
        """Bring the diary in line with the plan; returns how many entries changed."""
        async with self._lock:
            return await self._sync()

    async def _sync(self) -> int:
        week = read_week(self.hass, self.plan_sensor)
        dishes = read_dishes(self.hass, self.dishes_sensor)
        now = today()
        self.data.diary.forget_dismissed_before(now)
        self._take_library_numbers(dishes)
        self.completed = await self._complete_book(dishes)
        changed = 0
        for day in week:
            d = str(day.get("date") or "")
            if not d or d < now:
                continue
            for meal in PLANNED_MEALS:
                changed += await self._slot(d, meal, str(day.get(meal) or "").strip(), dishes)
        return changed

    async def _slot(self, d: str, meal: str, name: str, dishes: list[dict[str, Any]]) -> int:
        diary = self.data.diary
        pkey = f"{d}|{meal}"
        mine = [e for e in diary.entries(d) if e.get("plan_key") == pkey]
        if not name:
            gone = [e for e in mine if not e.get("edited")]
            for e in gone:
                diary.delete(d, e["id"], dismiss=False)
            return len(gone)
        if same := [e for e in mine if e["name"].lower() == name.lower()]:
            return sum([await self._refresh(d, e, name, dishes) for e in same])
        for e in mine:  # the plan changed to another meal
            diary.delete(d, e["id"], dismiss=False)
        if diary.dismissed(pkey, name):
            return len(mine)
        if any(e.get("meal") == meal and not e.get("plan_key") for e in diary.entries(d)):
            return len(mine)  # this meal is already logged by hand
        found = await self._nutrition(name, dishes)
        if not found:
            return len(mine)
        values, ref = found
        diary.add(
            d,
            {
                "name": name,
                "meal": meal,
                "source": "plan",
                "ref": ref,
                "plan_key": pkey,
                "portions": 1,
                "note": PLAN_NOTE,
                **values,
            },
        )
        return len(mine) + 1

    async def _refresh(self, d: str, e: dict[str, Any], name: str, dishes: list[dict[str, Any]]) -> int:
        """A planned entry takes the book's current numbers (completed, or newly arrived); never one whose numbers were edited."""
        if e.get("edited"):
            return 0
        found = await self._nutrition(name, dishes)
        if not found:
            return 0
        values, ref = found
        if e.get("ref") == ref and nums(values) == nums(e.get("per_portion")):
            return 0
        return int(self.data.diary.renumber(d, e["id"], values, ref, PLAN_NOTE) is not None)

    async def _nutrition(self, name: str, dishes: list[dict[str, Any]]) -> Nutrition | None:
        """One portion: the book's numbers, else worked out from the dish's ingredients (kept), else from its name (kept)."""
        est = self.data.estimator
        base = plain_name(name)
        low = base.lower()
        dish = next((x for x in dishes if low in dish_names(x)), None)
        try:
            if dish:
                did = dish["id"]
                if (known := self.book.get(did)) and known.get("kcal"):
                    if partial(known):
                        known = await self._completed(did, known, self._dish_estimate(dish))
                    return nums(known), did
                out = await est.dish(
                    did, base, ingredient_lines(dish), float(dish.get("servings") or 0), str(dish.get("amounts_per") or "recipe")
                )
                return nums(out), did
            key = f"name:{low}"
            if (known := self.book.get(key)) and known.get("kcal"):
                if partial(known):
                    known = await self._completed(key, known, lambda: est.text(base))
                return nums(known), ""
            out = await est.text(base)
            self.book.set(key, {**nums(out), "source": "ai"})
            return nums(out), ""
        except HomeAssistantError as err:
            _LOGGER.debug("No numbers for planned %s: %s", name, err)
            return None

    # ---------- the recipe book ----------

    def _take_library_numbers(self, dishes: list[dict[str, Any]]) -> None:
        """A library dish's own per-portion `nutrition` goes in the book as "recipe" numbers, unless the book has the
        person's own numbers, or has already completed these same (partial) numbers."""
        for dish in dishes:
            given = dish.get("nutrition")
            if not isinstance(given, dict) or not nums(given)["kcal"]:
                continue
            values, known = nums(given), self.book.get(dish["id"]) or {}
            if known.get("source") not in TAKES_LIBRARY_NUMBERS:
                continue
            if known.get("completed") and nums(known.get("from")) == values:
                continue
            if known.get("source") == "recipe" and not known.get("completed") and nums(known) == values:
                continue
            self.book.set(dish["id"], {**values, "source": "recipe"})

    async def _completed(
        self, key: str, known: dict[str, Any], estimate: Callable[[], Awaitable[dict[str, Any]]]
    ) -> dict[str, Any]:
        """The book's partial numbers for `key` made whole and kept (marked, so it's done once). An AI that doesn't
        answer leaves them as they are, for the next try."""
        try:
            est = await estimate()
        except HomeAssistantError as err:
            _LOGGER.debug("Couldn't complete the numbers for %s: %s", key, err)
            return known
        values = complete(known, est)
        return self.book.set(
            key,
            {
                **(values or nums(known)),
                "source": known.get("source"),
                "completed": "estimate" if values else "none",
                "from": nums(known),
            },
        )

    async def _complete_book(self, dishes: list[dict[str, Any]]) -> list[str]:
        """Every partial dish in the book completed: library recipes from their ingredients, meals from their name."""
        library = {x["id"]: x for x in dishes}
        est = self.data.estimator
        done = []
        for key, known in list(self.book.items.items()):
            if not partial(known):
                continue
            if dish := library.get(key):
                estimate = self._dish_estimate(dish)
            elif key.startswith("name:"):
                estimate = lambda text=key[5:]: est.text(text)  # noqa: E731
            else:
                continue
            if (await self._completed(key, known, estimate)).get("completed") == "estimate":
                done.append(key)
        if done:
            _LOGGER.info("Completed partial numbers in the recipe book: %s", ", ".join(done))
        return done

    def _dish_estimate(self, dish: dict[str, Any]) -> Callable[[], Awaitable[dict[str, Any]]]:
        name = plain_name(dish.get("name_en") or dish.get("name"))
        return lambda: self.data.estimator.dish_ai(
            name, ingredient_lines(dish), float(dish.get("servings") or 0), str(dish.get("amounts_per") or "recipe")
        )
