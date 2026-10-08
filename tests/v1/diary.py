"""One person's food diary, and the books every diary shares (recipe nutrition, products by barcode).

Everything lives in Home Assistant's own storage (.storage/food_diary.<person>, .storage/food_diary.dishes and
.storage/food_diary.products). A day is a list of entries; an entry keeps its numbers per portion and for what was eaten, so
portions or grams can change later without asking the AI again.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from datetime import date, timedelta
import logging
from typing import Any
import uuid

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import DEFAULT_GOALS, DOMAIN, MEALS, NUM

STORE_VERSION = 1
KEPT = ("estimate", "portions", "dismissed")  # a dish's book entry: kept through new numbers (see checks.py)
_LOGGER = logging.getLogger(__name__)


def num(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def nums(src: dict[str, Any] | None) -> dict[str, float]:
    src = src or {}
    return {k: round(num(src.get(k)), 2) for k in NUM}


def totals(entries: list[dict[str, Any]]) -> dict[str, float]:
    return {k: round(float(sum(num(e.get(k)) for e in entries)), 1) for k in NUM}


def meal_now() -> str:
    """The meal it most likely is right now (it can be changed after). The afternoon gap between lunch and dinner is a
    snack, as is late evening."""
    t = dt_util.now()
    h = t.hour + t.minute / 60
    return "breakfast" if h < 10.5 else "lunch" if h < 14.5 else "snack" if h < 17.5 else "dinner" if h < 21.5 else "snack"


def today() -> str:
    return dt_util.now().date().isoformat()


def again(e: dict[str, Any]) -> dict[str, Any]:
    """What's needed to log an entry again as it was: its numbers per portion, or per 100 g with the grams."""
    out = {
        "name": e["name"],
        "meal": e.get("meal"),
        "source": e.get("source"),
        "ref": e.get("ref", ""),
        **(e.get("per_portion") or nums(e)),
        **{k: e[k] for k in ("photo", "image_url", "barcode") if e.get(k)},
    }
    if e.get("per_100"):
        out.update(per_100=e["per_100"], grams=e.get("grams"), unit=e.get("unit", "g"))
    return out


class Diary:
    """A person's days, goals and listeners (the sensors)."""

    def __init__(self, hass: HomeAssistant, key: str) -> None:
        self.hass = hass
        self.store: Store[dict[str, Any]] = Store(hass, STORE_VERSION, f"{DOMAIN}.{key}")
        self.data: dict[str, Any] = {"goals": dict(DEFAULT_GOALS), "days": {}}
        self._listeners: list[Callable[[], None]] = []
        self._undo: list[dict[str, Any]] = []  # the last ten copies, for undo_copy (not kept over a restart)

    async def async_load(self) -> None:
        stored = await self.store.async_load()
        if stored:
            self.data["days"] = stored.get("days") or {}
            self.data["goals"] = {**DEFAULT_GOALS, **(stored.get("goals") or {})}
            self.data["dismissed"] = stored.get("dismissed") or {}
            self.data["saved"] = stored.get("saved") or []

    def dismissed(self, plan_key: str, name: str) -> bool:
        return self.data.get("dismissed", {}).get(plan_key) == name.lower()

    def forget_dismissed_before(self, d: str) -> None:
        self.data["dismissed"] = {k: v for k, v in self.data.get("dismissed", {}).items() if k.split("|")[0] >= d}

    @callback
    def async_add_listener(self, cb: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(cb)
        return lambda: self._listeners.remove(cb)

    @callback
    def async_changed(self) -> None:
        self.store.async_delay_save(lambda: self.data, 1)
        self.async_refresh()

    async def async_flush(self) -> None:
        """Write now (before a reload), not in a second."""
        await self.store.async_save(self.data)

    @callback
    def async_refresh(self) -> None:
        """Tell the sensors (a change, or a new day). One that fails never stops the others, or the change itself."""
        for cb in list(self._listeners):
            try:
                cb()
            except Exception:
                _LOGGER.exception("A food diary entity couldn't update")

    # ---------- reading ----------

    @property
    def goals(self) -> dict[str, Any]:
        return self.data["goals"]

    def entries(self, d: str) -> list[dict[str, Any]]:
        return self.data["days"].get(d, [])

    def day(self, d: str) -> dict[str, Any]:
        entries = sorted(
            self.entries(d), key=lambda e: (MEALS.index(e["meal"]) if e.get("meal") in MEALS else 9, e.get("at", ""))
        )
        t = totals(entries)
        goals = {k: v for k, v in self.goals.items() if k in NUM and num(v)}
        return {
            "date": d,
            "entries": entries,
            "totals": t,
            "goals": dict(self.goals),
            "left": {k: round(num(goals[k]) - t[k], 1) for k in goals},
            "meals": {m: round(sum(num(e.get("kcal")) for e in entries if e.get("meal") == m), 1) for m in MEALS},
        }

    def history(self, end: str, days: int) -> dict[str, Any]:
        last = date.fromisoformat(end)
        out = []
        for i in range(days - 1, -1, -1):
            d = (last - timedelta(days=i)).isoformat()
            e = self.entries(d)
            out.append({"date": d, **totals(e), "logged": len(e)})
        return {"days": out, "goals": dict(self.goals)}

    def recent(self, days: int = 60, limit: int = 30) -> list[dict[str, Any]]:
        """Foods eaten lately, most often first, with what's needed to log them again."""
        seen: dict[str, dict[str, Any]] = {}
        for d in self._back(days):
            for e in self.entries(d):
                k = str(e.get("name", "")).lower()
                if not k:
                    continue
                if k not in seen:
                    seen[k] = {**again(e), "times": 0}
                seen[k]["times"] += 1
        return sorted(seen.values(), key=lambda x: -x["times"])[:limit]

    def usuals(self, days: int = 28, min_days: int = 3, per_meal: int = 2) -> dict[str, list[dict[str, Any]]]:
        """Per meal, what the person has most days: foods logged in that meal on at least `min_days` recent days (not the
        meal plan's)."""
        days_seen: dict[tuple[str, str], set[str]] = {}
        latest: dict[tuple[str, str], dict[str, Any]] = {}
        for d in self._back(days):  # newest first, so the first seen is how it was had last
            for e in self.entries(d):
                if e.get("source") == "plan" or e.get("meal") not in MEALS or not e.get("name"):
                    continue
                k = (e["meal"], e["name"].lower())
                days_seen.setdefault(k, set()).add(d)
                latest.setdefault(k, e)
        out: dict[str, list[dict[str, Any]]] = {m: [] for m in MEALS}
        for k, ds in sorted(days_seen.items(), key=lambda kv: -len(kv[1])):
            if len(ds) >= min_days and len(out[k[0]]) < per_meal:
                out[k[0]].append({**again(latest[k]), "meal": k[0], "times": len(ds)})
        return out

    def review(self, end: str, days: int = 7) -> dict[str, Any]:
        """A look back over a week: one clear headline (days on target) and only what stands out."""
        last = date.fromisoformat(end)
        span = [(last - timedelta(days=i)).isoformat() for i in range(days - 1, -1, -1)]
        goal, pgoal = num(self.goals.get("kcal")), num(self.goals.get("protein_g"))
        rows = [{"date": d, **totals(self.entries(d)), "logged": len(self.entries(d))} for d in span]
        logged = [r for r in rows if r["logged"]]
        on_target = [r for r in logged if goal and goal * 0.75 <= r["kcal"] <= goal * 1.05]
        before = [(last - timedelta(days=days + i)).isoformat() for i in range(days)]
        prev = [t["kcal"] for t in (totals(self.entries(d)) for d in before) if t["kcal"]]
        eaten = Counter(e["name"] for d in span for e in self.entries(d))
        tried_before = {
            e.get("ref")
            for i in range(days, days + 56)
            for e in self.entries((last - timedelta(days=i)).isoformat())
            if e.get("ref")
        }
        new: list[str] = []
        for d in span:
            for e in self.entries(d):
                if e.get("ref") and e["ref"] not in tried_before and e["name"] not in new:
                    new.append(e["name"])
        best = min(on_target, key=lambda r: abs(r["kcal"] - goal)) if on_target else None
        top = eaten.most_common(1)
        return {
            "start": span[0],
            "end": span[-1],
            "days": rows,
            "goal_kcal": goal,
            "goal_protein_g": pgoal,
            "days_logged": len(logged),
            "avg_kcal": round(sum(r["kcal"] for r in logged) / len(logged)) if logged else 0,
            "on_target": len(on_target),
            "over": len([r for r in logged if goal and r["kcal"] > goal * 1.05]),
            "protein_days": len([r for r in logged if pgoal and r["protein_g"] >= pgoal * 0.9]),
            "last_week_avg_kcal": round(sum(prev) / len(prev)) if prev else None,
            "favourite": {"name": top[0][0], "times": top[0][1]} if top and top[0][1] >= 2 else None,
            "new_dishes": new[:5],
            "best_day": {"date": best["date"], "kcal": best["kcal"]} if best else None,
        }

    def _back(self, days: int) -> list[str]:
        start = date.fromisoformat(today())
        return [(start - timedelta(days=i)).isoformat() for i in range(days)]

    # ---------- saved meals ----------

    @property
    def saved(self) -> list[dict[str, Any]]:
        return self.data.setdefault("saved", [])

    def save_meal(self, name: str, d: str, meal: str) -> dict[str, Any] | None:
        """Keep one meal of a day (several foods) as one thing to log again; the same name replaces the old one."""
        items = [e for e in self.entries(d) if e.get("meal") == meal]
        if not items:
            return None
        clean = str(name).strip()[:60] or meal.title()
        s = {
            "id": uuid.uuid4().hex[:10],
            "name": clean,
            "meal": meal,
            "items": [e["name"] for e in items],
            **totals(items),
            "at": dt_util.now().isoformat(timespec="minutes"),
        }
        self.data["saved"] = [x for x in self.saved if x["name"].lower() != clean.lower()] + [s]
        self.async_changed()
        return s

    def update_saved(self, saved_id: str, name: str | None = None, meal: str | None = None) -> dict[str, Any] | None:
        """Rename a saved meal or move it to another meal; a new name that another saved meal has replaces that one."""
        s = next((x for x in self.saved if x["id"] == saved_id), None)
        if s is None:
            return None
        if name is not None and (clean := str(name).strip()[:60]):
            self.data["saved"] = [x for x in self.saved if x["id"] == saved_id or x["name"].lower() != clean.lower()]
            s["name"] = clean
        if meal in MEALS:
            s["meal"] = meal
        self.async_changed()
        return s

    def delete_saved(self, saved_id: str) -> dict[str, Any] | None:
        for x in self.saved:
            if x["id"] == saved_id:
                self.saved.remove(x)
                self.async_changed()
                return x
        return None

    # ---------- writing ----------

    def add(self, d: str, f: dict[str, Any]) -> dict[str, Any]:
        portions = num(f.get("portions")) or 1.0
        per_100 = nums(f["per_100"]) if isinstance(f.get("per_100"), dict) and num(f["per_100"].get("kcal")) else None
        grams = num(f.get("grams"))
        if per_100 and grams:  # from a label or a barcode: what was eaten is per_100 x grams
            per_portion = {k: round(per_100[k] * grams / 100, 2) for k in NUM}
            portions = 1.0
        else:
            per_portion = nums(f.get("per_portion") or f)
        entry: dict[str, Any] = {
            "id": uuid.uuid4().hex[:10],
            "at": dt_util.now().isoformat(timespec="minutes"),
            "meal": f["meal"] if f.get("meal") in MEALS else meal_now(),
            "name": str(f.get("name") or "Something").strip()[:80],
            "portions": portions,
            "source": f.get("source") or "manual",
            "ref": str(f.get("ref") or ""),
            "per_portion": per_portion,
        }
        if per_100:
            entry.update(per_100=per_100, grams=round(grams, 1), unit="ml" if f.get("unit") == "ml" else "g")
        for k in ("note", "barcode", "plan_key", "photo"):
            if f.get(k):
                entry[k] = str(f[k])[:120]
        if str(f.get("image_url") or "").startswith("https://"):
            entry["image_url"] = str(f["image_url"])[:300]
        if f.get("edited"):
            entry["edited"] = True
        self._recount(entry)
        self.data["days"].setdefault(d, []).append(entry)
        self.async_changed()
        return entry

    def update(self, d: str, entry_id: str, f: dict[str, Any]) -> dict[str, Any] | None:
        for e in self.entries(d):
            if e["id"] != entry_id:
                continue
            if num(f.get("grams")) and e.get("per_100"):
                e["grams"] = round(num(f["grams"]), 1)
                e["per_portion"] = {k: round(num(e["per_100"].get(k)) * e["grams"] / 100, 2) for k in NUM}
                e["portions"] = 1.0
                e.pop("edited", None)
            if num(f.get("portions")):
                e["portions"] = num(f["portions"])
            if any(k in f for k in NUM):  # the person's own numbers for the whole entry
                p = num(e.get("portions")) or 1.0
                e["per_portion"] = {k: round((num(f[k]) if k in f else num(e.get(k))) / p, 2) for k in NUM}
                e["edited"] = True
            if str(f.get("name") or "").strip():
                e["name"] = str(f["name"]).strip()[:80]
            if f.get("meal") in MEALS:
                e["meal"] = f["meal"]
            if "checked" in f:  # confirmed as right (a likely double entry that isn't one, say)
                if f["checked"]:
                    e["checked"] = True
                else:
                    e.pop("checked", None)
            self._recount(e)
            self.async_changed()
            return e
        return None

    def renumber(self, d: str, entry_id: str, values: dict[str, Any], ref: str, note: str) -> dict[str, Any] | None:
        """New numbers for one portion of a planned entry (from its recipe); its portions stay."""
        for e in self.entries(d):
            if e["id"] == entry_id:
                e.update(per_portion=nums(values), ref=ref, note=note)
                self._recount(e)
                self.async_changed()
                return e
        return None

    def follow(self, ref: str, values: dict[str, Any], start: str) -> int:
        """A recipe's numbers changed: its entries from `start` on whose numbers weren't edited take them (portions stay)."""
        changed = 0
        for d in sorted(x for x in self.data["days"] if x >= start):
            for e in self.entries(d):
                if e.get("ref") == ref and not e.get("edited") and nums(e.get("per_portion")) != nums(values):
                    e["per_portion"] = nums(values)
                    self._recount(e)
                    changed += 1
        if changed:
            self.async_changed()
        return changed

    def delete(self, d: str, entry_id: str, dismiss: bool = True) -> dict[str, Any] | None:
        """Remove an entry. Removing one that came from the meal plan remembers it, so the plan doesn't put it back."""
        entries = self.entries(d)
        for e in entries:
            if e["id"] == entry_id:
                if dismiss and e.get("plan_key"):
                    self.data.setdefault("dismissed", {})[e["plan_key"]] = e["name"].lower()
                entries.remove(e)
                if not entries:
                    self.data["days"].pop(d, None)
                self.async_changed()
                return e
        return None

    def set_photo(self, d: str, entry_id: str, photo: str) -> dict[str, Any] | None:
        for e in self.entries(d):
            if e["id"] == entry_id:
                e["photo"] = photo
                self.async_changed()
                return e
        return None

    # ---------- copying days ----------

    def copy(self, src: str, targets: list[str], meal: str | None = None, replace: bool = False) -> dict[str, Any]:
        """Copy a day (or one meal of it) onto other days. Planned food comes along as plain food. With `replace`, what those
        days had in that meal (or all day) goes first. Returns what Undo needs; Undo is `uncopy`."""
        food = [e for e in self.entries(src) if meal is None or e.get("meal") == meal]
        record: dict[str, Any] = {
            "token": uuid.uuid4().hex[:10],
            "added": {},
            "removed": {},
            "dismissed": dict(self.data.get("dismissed", {})),
        }
        for d in targets:
            if d == src:
                continue
            if replace:
                gone = [e for e in self.entries(d) if meal is None or e.get("meal") == meal]
                for e in gone:
                    self.delete(d, e["id"])
                if gone:
                    record["removed"][d] = gone
            added = []
            for e in food:
                c = {k: v for k, v in e.items() if k not in ("id", "at", "plan_key", "edited")}
                if c.get("source") == "plan":
                    c["source"] = "again"
                    c.pop("note", None)
                c["edited"] = e.get("edited", False)
                added.append(self.add(d, {**c, "portions": e.get("portions") or 1, "per_portion": e.get("per_portion")})["id"])
            record["added"][d] = added
        self._undo = [*self._undo[-9:], record]
        return record

    def uncopy(self, token: str) -> bool:
        record = next((r for r in self._undo if r["token"] == token), None)
        if record is None:
            return False
        for d, ids in record["added"].items():
            for i in ids:
                self.delete(d, i, dismiss=False)
        for d, gone in record["removed"].items():
            self.data["days"].setdefault(d, []).extend(gone)
        self.data["dismissed"] = record["dismissed"]
        self._undo.remove(record)
        self.async_changed()
        return True

    def set_goals(self, f: dict[str, Any]) -> dict[str, Any]:
        for k in NUM:
            if k in f and f[k] is not None:
                self.goals[k] = round(num(f[k]), 1)
        self.async_changed()
        return dict(self.goals)

    @staticmethod
    def _recount(e: dict[str, Any]) -> None:
        p = num(e.get("portions")) or 1.0
        e.update({k: round(num(e["per_portion"].get(k)) * p, 1) for k in NUM})


class Book:
    """A shared lookup by id: dish nutrition per portion, or products by barcode."""

    def __init__(self, hass: HomeAssistant, name: str) -> None:
        self.store: Store[dict[str, Any]] = Store(hass, STORE_VERSION, f"{DOMAIN}.{name}")
        self.items: dict[str, dict[str, Any]] = {}

    async def async_load(self) -> None:
        self.items = await self.store.async_load() or {}

    def get(self, key: str) -> dict[str, Any] | None:
        return self.items.get(key)

    @callback
    def set(self, key: str, value: dict[str, Any]) -> dict[str, Any]:
        """New numbers for `key`. What the numbers check keeps about the dish (its cached estimate, how many portions the
        recipe makes, numbers confirmed as right) stays; an open doubt goes, and is worked out again for the new numbers."""
        old = self.items.get(key) or {}
        kept = {k: old[k] for k in KEPT if k in old and k not in value}
        self.items[key] = {**kept, **value, "at": dt_util.now().isoformat(timespec="minutes")}
        self.store.async_delay_save(lambda: self.items, 1)
        return self.items[key]

    @callback
    def patch(self, key: str, changes: dict[str, Any]) -> dict[str, Any]:
        """Some fields of `key` changed (None removes one); its numbers and time stay."""
        item = self.items.setdefault(key, {})
        for k, v in changes.items():
            if v is None:
                item.pop(k, None)
            else:
                item[k] = v
        self.store.async_delay_save(lambda: self.items, 1)
        return item
