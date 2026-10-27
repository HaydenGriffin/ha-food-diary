"""Batches: a pot of food cooked from weighed raw ingredients, then eaten by the bowl in grams.

Each ingredient is weighed raw and keeps its raw numbers per 100 g, so the pot's totals are Σ per_100 × grams. Cooking
changes the weight, not the numbers: pasta and rice take up water, meat loses fat and water, sauces simmer down. Each
ingredient's `cooked_factor` (cooked weight / raw weight) gives the pot's weight when it wasn't weighed (Σ grams ×
cooked_factor); a weighed `cooked_g` wins. A bowl of it is the batch's cooked per_100 × grams, logged with source "batch"
and ref the batch id.

Batches are kept in each person's diary for KEEP_DAYS (older ones go when the diary loads, on get_batches and on a
save); the same name on the same day is the same batch (it replaces). A pot needs at least MIN_RAW_G of ingredients.

A batch knows its veg (`veg_g` in its totals and per_100) when every ingredient's per_100 does.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any
import uuid

from homeassistant.exceptions import ServiceValidationError
from homeassistant.util import dt as dt_util

from .const import MACROS, NUM, VEG
from .diary import Diary, given, num, nums, today, unknown_in, veg_of, with_veg

KEEP_DAYS = 400
MIN_RAW_G = 1
NO_WEIGHT = "Give each ingredient's raw weight (grams)."
FACTOR_MIN, FACTOR_MAX = 0.2, 4.0


def cooked_factor(v: Any) -> float:
    """Cooked weight / raw weight, kept within what cooking can do (1: unchanged when it isn't said)."""
    return round(min(FACTOR_MAX, max(FACTOR_MIN, num(v))), 2) if given(v) and num(v) > 0 else 1.0


def ingredient(f: dict[str, Any]) -> dict[str, Any]:
    """One raw ingredient: name, grams, per_100 (raw), cooked_factor, ref?, unknown? (a macro its per_100 lacks)."""
    raw = f.get("per_100") if isinstance(f.get("per_100"), dict) else {}
    u = unknown_in(raw, f.get("unknown"))
    return {
        "name": str(f.get("name") or "Something").strip()[:80],
        "grams": round(num(f.get("grams")), 1),
        "per_100": with_veg(nums(raw), veg_of(raw)),
        "cooked_factor": cooked_factor(f.get("cooked_factor")),
        **({"ref": str(f["ref"])[:80]} if f.get("ref") else {}),
        **({"unknown": u} if u else {}),
    }


def counted(b: dict[str, Any]) -> dict[str, Any]:
    """The batch's weights and numbers worked out again from its ingredients (and its weighed cooked_g, if any)."""
    items = b["ingredients"]
    raw = sum(i["grams"] for i in items)
    if raw < MIN_RAW_G:
        raise ServiceValidationError(NO_WEIGHT)
    b["raw_g"] = round(raw, 1)
    if b.get("cooked_estimated", True):
        cooked = sum(i["grams"] * i["cooked_factor"] for i in items)  # unrounded: cooked_g is only for showing
        b["cooked_g"] = round(cooked)
        b["cooked_estimated"] = True
    else:
        cooked = num(b["cooked_g"])
    totals = {k: sum(i["per_100"][k] * i["grams"] / 100 for i in items) for k in NUM}
    b["totals"] = {k: round(v, 1) for k, v in totals.items()}
    b["per_100"] = {k: round(totals[k] / cooked * 100, 2) for k in NUM}
    vegs = [veg_of(i["per_100"]) for i in items]
    if all(v is not None for v in vegs):  # every ingredient knows its veg
        veg = sum((v or 0.0) * i["grams"] / 100 for v, i in zip(vegs, items, strict=True))
        b["totals"][VEG], b["per_100"][VEG] = round(veg, 1), round(veg / cooked * 100, 2)
    u = [k for k in MACROS if any(k in i.get("unknown", ()) for i in items)]  # a macro any ingredient lacks
    if u:
        b["unknown"] = u
    else:
        b.pop("unknown", None)
    return b


def new_batch(f: dict[str, Any], d: str, same: dict[str, Any] | None) -> dict[str, Any]:
    """A batch from save_batch's fields; `same` (that name on that day) gives its id, so food logged from it follows."""
    b: dict[str, Any] = {
        "id": same["id"] if same else uuid.uuid4().hex[:10],
        "name": str(f["name"]).strip()[:80],
        "date": d,
        "at": dt_util.now().isoformat(timespec="minutes"),
        "ingredients": [ingredient(i) for i in f["ingredients"]],
    }
    if num(f.get("cooked_g")) > 0:
        b.update(cooked_g=round(num(f["cooked_g"])), cooked_estimated=False)
    if num(f.get("portions")) > 0:
        b["portions"] = num(f["portions"])
    if f.get("recipe_id"):
        b["recipe_id"] = str(f["recipe_id"])
    return counted(b)


def newest_first(batches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(batches, key=lambda b: (b["date"], b.get("at", "")), reverse=True)


def recent(batches: list[dict[str, Any]], end: str, days: int) -> list[dict[str, Any]]:
    """Batches cooked today or on the days-1 days before, newest first."""
    start = (date.fromisoformat(end) - timedelta(days=days - 1)).isoformat()
    return [b for b in newest_first(batches) if start <= b["date"] <= end]


def regulars(batches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One per name (any case), newest first, with its last batch: what she cooks again from memory."""
    out: dict[str, dict[str, Any]] = {}
    for b in newest_first(batches):
        out.setdefault(b["name"].lower(), {"name": b["name"], "last": b})
    return list(out.values())


def kept(batches: list[dict[str, Any]], end: str) -> list[dict[str, Any]]:
    """The batches cooked within KEEP_DAYS of `end`."""
    cut = (date.fromisoformat(end) - timedelta(days=KEEP_DAYS)).isoformat()
    return [b for b in batches if b.get("date", "") >= cut]


class Batches:
    """One person's batches, kept in their diary's store (`batches`)."""

    def __init__(self, diary: Diary) -> None:
        self.diary = diary

    @property
    def all(self) -> list[dict[str, Any]]:
        return self.diary.data.setdefault("batches", [])

    def prune(self) -> list[dict[str, Any]]:
        """Forget the batches older than KEEP_DAYS (on load and on get_batches); returns the ones kept."""
        if len(left := kept(self.all, today())) != len(self.all):
            self.diary.data["batches"] = left
            self.diary.async_changed()
        return left

    def get(self, batch_id: str) -> dict[str, Any] | None:
        return next((b for b in self.all if b["id"] == batch_id), None)

    def save(self, f: dict[str, Any], d: str) -> tuple[dict[str, Any], int]:
        """A new batch (that name on that day again replaces it, keeping its id). Returns it and how many entries took its
        new numbers."""
        name = str(f["name"]).strip().lower()
        same = next((b for b in self.all if b["date"] == d and b["name"].lower() == name), None)
        b = new_batch(f, d, same)
        rest = [x for x in self.all if x is not same]
        self.diary.data["batches"] = kept([*rest, b], today())
        return b, self._follow(b)

    def update(self, batch_id: str, cooked_g: float | None, portions: float | None) -> tuple[dict[str, Any], int] | None:
        """The pot's real weight (weighed after all) or how many portions it made; entries from it follow."""
        if (b := self.get(batch_id)) is None:
            return None
        if cooked_g:
            b.update(cooked_g=round(num(cooked_g)), cooked_estimated=False)
        if portions:
            b["portions"] = num(portions)
        counted(b)
        return b, self._follow(b)

    def delete(self, batch_id: str) -> dict[str, Any] | None:
        """Forget a batch; what was logged from it stays as it is."""
        if (b := self.get(batch_id)) is None:
            return None
        self.all.remove(b)
        self.diary.async_changed()
        return b

    def _follow(self, b: dict[str, Any]) -> int:
        n = self.diary.follow_per_100(b["id"], "batch", b["per_100"], b.get("unknown", []), b["date"])
        self.diary.async_changed()
        return n
