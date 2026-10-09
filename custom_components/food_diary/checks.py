"""Numbers that look wrong: a recipe's per-portion numbers checked against what its ingredients add up to.

Imported recipes often come with wrong totals, or the wrong number of portions (a pasta recipe printing 470 kcal a portion
when its 120 g of dry pasta and 120 g of chicken make ~790 kcal, and it serves two). So, for each dish library recipe with
numbers and ingredients:
- the AI adds up the whole recipe once (kept on its book entry as `estimate`, until the ingredients change);
- one portion is that divided by the portions the recipe makes: what was set (`portions`), else the recipe's servings (one
  when its amounts are per portion, two when it doesn't say);
- the numbers are in doubt when the calories are more than 30% off that, or the protein more than 40% and 10 g off. The
  doubt (`check` on the book entry) is the suggested numbers for one portion, the portions they assume and a short reason.
  When another number of portions explains the calories, that's the suggestion ("may serve 2"); when it explains all of
  them, the numbers are right for it and there's no doubt.
The person's own numbers ("own") are never doubted, nor numbers confirmed as right (`dismissed`, until they change). Every
check is remembered (`checked_for`), so each dish is only looked at again when its numbers, ingredients or portions change.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
import hashlib
import json
import logging
import re
from typing import TYPE_CHECKING, Any

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_call_later

from .const import DOMAIN
from .diary import Book, num, nums
from .library import ingredient_lines as lines, plain_name
from .sources import MealPlan, RecipeLibrary, read_dishes

if TYPE_CHECKING:
    from .estimate import Estimator

_LOGGER = logging.getLogger(__name__)
KEYS = ("kcal", "protein_g", "carbs_g", "fat_g")
KCAL_OFF = 0.30  # calories this far off the suggestion: in doubt
PROTEIN_OFF, PROTEIN_G = 0.40, 10  # protein this far off, and by more than this many grams
FITS = 0.25  # another number of portions "explains" the calories within this
MAX_PORTIONS = 8
TRUSTED = ("own",)
RULES = 2  # bump when the rules change: every dish is checked again (from its kept sum)
AI_GAP = 3.0  # seconds between AI asks in a scan
AI_TIMEOUT = 20
GRAMS = re.compile(r"(\d+(?:[.,]\d+)?)\s*(kg|g|gr|grams?)\b", re.IGNORECASE)
# left out of the reason: they hardly move the calories
LIGHT = re.compile(
    r"\b(water|stock|broth|ice|tomato(es)?|passata|onions?|peppers?|spinach|lettuce|cucumber|courgettes?|zucchini|"
    r"mushrooms?|carrots?|broccoli|cabbage|celery|garlic|juice|basil|coriander|parsley|herbs?|salt)\b",
    re.IGNORECASE,
)


# ---------- pure helpers ----------


def dish_name(dish: dict[str, Any]) -> str:
    return plain_name(dish.get("name_en") or dish.get("name"))


def ingredients_key(dish: dict[str, Any]) -> str:
    return hashlib.sha1(json.dumps([dish_name(dish), lines(dish)]).encode()).hexdigest()[:12]


def numbers_key(v: dict[str, Any] | None) -> str:
    return "|".join(f"{num((v or {}).get(k)):g}" for k in KEYS)


def default_portions(dish: dict[str, Any]) -> int:
    """How many portions the listed ingredients make, as the recipe says: one when its amounts are per portion."""
    if dish.get("amounts_per") == "portion":
        return 1
    return max(1, round(num(dish.get("servings")))) if num(dish.get("servings")) >= 1 else 2


def per_portion(whole: dict[str, Any], portions: int) -> dict[str, float]:
    p = max(1, portions)
    return {k: round(num(whole.get(k)) / p, 0 if k == "kcal" else 1) for k in KEYS}


def off(yours: dict[str, Any], suggested: dict[str, Any]) -> str | None:
    """Which number is off the suggestion ("kcal" or "protein_g"), or None when they agree."""
    yk, hk = num(yours.get("kcal")), num(suggested.get("kcal"))
    if hk > 0 and abs(yk - hk) > KCAL_OFF * hk:
        return "kcal"
    yp, hp = num(yours.get("protein_g")), num(suggested.get("protein_g"))
    if abs(yp - hp) > PROTEIN_G and abs(yp - hp) > PROTEIN_OFF * max(hp, 1):
        return "protein_g"
    return None


def fitting_portions(yours: dict[str, Any], whole: dict[str, Any], assumed: int, stated: int = 0) -> int:
    """How many portions the given calories fit: `assumed` or the recipe's `stated` servings when they do, else the number whose
    calories come closest when that's close, else `assumed`."""
    yk, wk = num(yours.get("kcal")), num(whole.get("kcal"))
    if yk <= 0 or wk <= 0:
        return assumed

    def fits(n: int) -> bool:
        return abs(wk / n - yk) <= FITS * (wk / n)

    for n in (assumed, stated):
        if n >= 1 and fits(n):
            return n
    k = min(range(1, MAX_PORTIONS + 1), key=lambda n: abs(wk / n - yk))
    return k if fits(k) else assumed


def stated_servings(dish: dict[str, Any] | None) -> int:
    return round(num((dish or {}).get("servings"))) if num((dish or {}).get("servings")) >= 1 else 0


def main_ingredients(dish: dict[str, Any] | None) -> list[str]:
    """The two biggest weighed ingredients, like "120 g rigatoni" (not water, stock, vegetables or herbs)."""
    found = []
    for i in (dish or {}).get("ingredients") or []:
        if not isinstance(i, dict) or LIGHT.search(str(i.get("name") or "")):
            continue
        if m := GRAMS.search(str(i.get("amount") or "")):
            g = float(m.group(1).replace(",", ".")) * (1000 if m.group(2).lower() == "kg" else 1)
            found.append((g, f"{g:g} g {str(i.get('name') or '').strip().lower()}"))
    return [t for _, t in sorted(found, key=lambda x: -x[0])[:2]]


def reason(
    what: str | None, yours: dict[str, Any], suggested: dict[str, Any], portions: int, assumed: int, dish: dict[str, Any] | None
) -> str:
    """One short plain line (≤80 characters) on why the numbers look wrong, or that they look right."""
    serve = f" — may serve {portions}" if portions != assumed else ""
    if what is None:
        return (
            f"Matches its ingredients for {portions} portions"
            if portions > 1
            else "Matches its ingredients"
            if lines(dish)
            else "Close to a normal portion"
        )
    side = "Low" if num(yours.get(what)) < num(suggested.get(what)) else "High"
    head = side if what == "kcal" else f"Protein {side.lower()}"
    if not num(yours.get("kcal")):
        return "No numbers yet"
    if not lines(dish):
        return f"{head} for a normal portion"
    main = main_ingredients(dish)
    for text in (" and ".join(main), main[0] if main else ""):
        if text and len(t := f"{head} for {text}{serve}") <= 80:
            return t
    return f"{head} for its ingredients{serve}"[:80]


def doubt(dish: dict[str, Any], entry: dict[str, Any], whole: dict[str, Any]) -> dict[str, Any] | None:
    """The suggested numbers when a recipe's numbers look wrong for its ingredients, else None."""
    if entry.get("source") in TRUSTED or not num(entry.get("kcal")) or not num(whole.get("kcal")):
        return None
    if entry.get("dismissed") and entry["dismissed"] == numbers_key(entry):
        return None
    yours = nums(entry)
    assumed = int(num(entry.get("portions"))) or default_portions(dish)
    suggested = per_portion(whole, assumed)
    what = off(yours, suggested)
    if what is None:
        return None
    k = fitting_portions(yours, whole, assumed, stated_servings(dish))
    if k != assumed:
        if off(yours, per_portion(whole, k)) is None:
            return None  # right for k portions: just the servings were wrong
        suggested = per_portion(whole, k)
    return {**suggested, "portions": k, "reason": reason(what, yours, per_portion(whole, assumed), k, assumed, dish)}


# ---------- Home Assistant side ----------


class Checker:
    """Keeps the recipe book's doubts up to date, and answers "are these numbers right?"."""

    def __init__(
        self, hass: HomeAssistant, estimator: Estimator, recipes: RecipeLibrary | None, plan: MealPlan | None = None
    ) -> None:
        self.hass, self.estimator, self.recipes, self.plan = hass, estimator, recipes, plan
        self._lock = asyncio.Lock()
        self._pending: CALLBACK_TYPE | None = None
        self._live = False
        self.gap = AI_GAP

    @property
    def book(self) -> Book:
        return self.hass.data[DOMAIN]["dishes"]

    def library(self) -> dict[str, dict[str, Any]]:
        return {x["id"]: x for x in read_dishes(self.recipes) or []}

    # ---------- running by itself ----------

    @callback
    def async_start(self) -> list[CALLBACK_TYPE]:
        """Look over the book shortly after start, and again a minute after the plan or the dish library change."""
        self._live = True
        subscribed = [x.async_subscribe(self._changed) for x in (self.recipes, self.plan) if x]
        return [*subscribed, self.schedule(90), self._stop]

    @callback
    def _stop(self) -> None:
        self._live = False
        self._cancel_pending()

    @callback
    def _cancel_pending(self) -> None:
        if self._pending:
            self._pending()
            self._pending = None

    @callback
    def _changed(self) -> None:
        self.schedule(60)

    @callback
    def schedule(self, delay: float) -> CALLBACK_TYPE:
        if not self._live:
            return lambda: None
        if self._pending:
            self._pending()
        self._pending = async_call_later(self.hass, delay, self._run)
        return self._cancel_pending

    async def _run(self, _now: datetime | None = None) -> None:
        self._pending = None
        try:
            await self.async_scan()
        except Exception:  # a check that fails never breaks the diary
            _LOGGER.exception("Couldn't check the recipe book's numbers")

    # ---------- the book ----------

    async def whole(self, key: str, dish: dict[str, Any]) -> dict[str, float]:
        """What the dish's ingredients add up to: kept on its book entry, asked of the AI only when they change."""
        of = ingredients_key(dish)
        cached = (self.book.get(key) or {}).get("estimate")
        if isinstance(cached, dict) and cached.get("of") == of and num(cached.get("kcal")):
            return nums(cached)
        try:
            async with asyncio.timeout(AI_TIMEOUT):
                out = await self.estimator.dish_whole(dish_name(dish), lines(dish))
        except TimeoutError as err:
            raise HomeAssistantError("Couldn't work that out just now. Try again.") from err
        self.book.patch(key, {"estimate": {**out, "of": of, "whole": True}})
        return out

    async def by_name(self, name: str) -> dict[str, float]:
        """One normal portion of a dish by its name (no ingredients to go on): kept under the name."""
        key = f"name:{name.strip().lower()}"
        cached = (self.book.get(key) or {}).get("estimate")
        if isinstance(cached, dict) and cached.get("of") == "name" and num(cached.get("kcal")):
            return nums(cached)
        try:
            async with asyncio.timeout(AI_TIMEOUT):
                out = nums(await self.estimator.text(name))
        except TimeoutError as err:
            raise HomeAssistantError("Couldn't work that out just now. Try again.") from err
        self.book.patch(key, {"estimate": {**out, "of": "name"}})
        return out

    def _checked_for(self, entry: dict[str, Any], dish: dict[str, Any]) -> str:
        parts = (
            RULES,
            numbers_key(entry),
            entry.get("source"),
            int(num(entry.get("portions"))),
            ingredients_key(dish),
            entry.get("dismissed", ""),
        )
        return "|".join(str(p) for p in parts)

    async def async_scan(self) -> list[str]:
        """Check every library recipe whose numbers, ingredients or portions changed since it was last checked; the AI is
        asked once per recipe (a few seconds apart), and not at all for what it has already added up. Returns the ids now
        in doubt."""
        async with self._lock:
            asked = fails = 0
            for key, dish in self.library().items():
                entry = self.book.get(key)
                if not entry or not num(entry.get("kcal")) or not lines(dish):
                    continue
                if entry.get("source") in TRUSTED:
                    if entry.get("check"):
                        self.book.patch(key, {"check": None})
                    continue
                stamp = self._checked_for(entry, dish)
                if entry.get("checked_for") == stamp:
                    continue
                cached = isinstance(entry.get("estimate"), dict) and entry["estimate"].get("of") == ingredients_key(dish)
                if not cached and asked and self.gap:
                    await asyncio.sleep(self.gap)
                try:
                    whole = await self.whole(key, dish)
                except HomeAssistantError as err:
                    _LOGGER.debug("Couldn't check the numbers of %s: %s", key, err)
                    fails += 1
                    if fails >= 3:
                        break
                    continue
                asked += not cached
                entry = self.book.get(key) or {}
                if self._checked_for(entry, dish) != stamp:
                    continue  # its numbers changed meanwhile: next time
                self.book.patch(key, {"check": doubt(dish, entry, whole), "checked_for": stamp})
            doubts = [k for k, v in self.book.items.items() if v.get("check")]
            if asked:
                _LOGGER.info("Checked recipe numbers (%s added up); %s in doubt", asked, len(doubts))
            return doubts

    # ---------- one question ----------

    async def compare(
        self, yours: dict[str, Any], key: str | None, dish: dict[str, Any] | None, name: str, portions: int | None = None
    ) -> dict[str, Any]:
        """Given numbers for one portion against the suggested ones: from the recipe's ingredients for `portions` (else
        what was set for the recipe, else the number of portions the given numbers fit, else what the recipe says), else
        from the name. The suggestion is returned under `house` (a stable response key)."""
        yours = {k: round(num(yours.get(k)), 1) for k in KEYS}
        entry = (self.book.get(key) if key else None) or {}
        if dish and lines(dish) and key:
            whole = await self.whole(key, dish)
            set_for = portions or int(num(entry.get("portions")))
            assumed = set_for or default_portions(dish)
            n = assumed if set_for else fitting_portions(yours, whole, assumed, stated_servings(dish))
        else:
            whole, assumed, n = await self.by_name(name or dish_name(dish or {})), 1, 1
        suggested, base = per_portion(whole, n), per_portion(whole, assumed)
        if not num(yours["kcal"]):
            what = why = "kcal"
        else:  # why, said against the portions the recipe says it makes: "Low for … — may serve 2"
            what = off(yours, suggested)
            why = (off(yours, base) or what) if what else None
        return {
            "yours": yours,
            "house": suggested,
            "portions": n,
            "differs": what is not None,
            "reason": reason(why, yours, base, n, assumed, dish),
        }

    def dismiss(self, key: str) -> dict[str, Any] | None:
        """The numbers are confirmed as right: no doubt, now or later, for these numbers."""
        entry = self.book.get(key)
        if entry is None:
            return None
        return self.book.patch(key, {"check": None, "dismissed": numbers_key(entry)})
