"""One person's food diary, and the books every diary shares (recipe nutrition, products by barcode).

Everything lives in Home Assistant's own storage (.storage/food_diary.<person>, .storage/food_diary.dishes and
.storage/food_diary.products). A day is a list of entries; an entry keeps its numbers per portion and for what was eaten, so
portions or grams can change later without asking the AI again.

Every entry has a revision (`rev`, 1 when made, one more on every change), so an app can say which version it is changing
(`expected_rev`) and background work can tell that an entry changed while it waited. A create that comes with a `client_id`
(an id the app made for that one action) happens once: the diary keeps a ledger of them, with the entry each one made, that
outlives the entry, so a retried or doubled request returns the first result instead of logging the food again.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from collections.abc import Awaitable, Callable
import copy
from datetime import date, timedelta
import hashlib
import json
import logging
from typing import Any
import uuid

from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import DEFAULT_GOALS, DOMAIN, MEALS, NUM

STORE_VERSION = 1  # rev, client_id and the client_id ledger are additions older versions load (and ignore) as they are
KEPT = ("estimate", "portions", "dismissed")  # a dish's book entry: kept through new numbers (see checks.py)
NOT_COPIED = ("id", "at", "rev", "client_id", "plan_key", "edited")  # a copied entry is a new entry
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


def canonical_hash(value: Any) -> str:
    """sha256 of the canonical JSON of `value` (sorted keys, no whitespace; dates as ISO text)."""
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(text.encode()).hexdigest()


def payload_hash(request: dict[str, Any]) -> str:
    """What a create request asked for, without its client_id: the same id with another payload is a mistake."""
    return canonical_hash({k: v for k, v in request.items() if k != "client_id"})


def rev(e: dict[str, Any]) -> int:
    """An entry's revision (entries from before revisions count as 1)."""
    return int(e.get("rev") or 1)


def conflict() -> ServiceValidationError:
    return ServiceValidationError(translation_domain=DOMAIN, translation_key="conflict")


def client_id_reused() -> ServiceValidationError:
    return ServiceValidationError(translation_domain=DOMAIN, translation_key="client_id_reused")


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
        self._snapshots: list[dict[str, Any]] = []  # the last ten snapshots, for restore_snapshot (not kept either)
        self._creating: dict[str, tuple[asyncio.Lock, list[int]]] = {}  # client_id -> (its lock, how many wait on it)

    async def async_load(self) -> None:
        stored = await self.store.async_load()
        if stored:
            self.data["days"] = stored.get("days") or {}
            self.data["goals"] = {**DEFAULT_GOALS, **(stored.get("goals") or {})}
            self.data["dismissed"] = stored.get("dismissed") or {}
            self.data["saved"] = stored.get("saved") or []
            self.data["client_ids"] = stored.get("client_ids") or {}
        for entries in self.data["days"].values():
            for e in entries:
                e.setdefault("rev", 1)

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

    def find(self, d: str, entry_id: str) -> dict[str, Any] | None:
        return next((e for e in self.entries(d) if e["id"] == entry_id), None)

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

    # ---------- doing a create once ----------

    @property
    def client_ids(self) -> dict[str, dict[str, Any]]:
        """The ledger: client_id -> {entry_id, date, payload_hash, at} (a copy keeps `copy` instead of one entry). It is
        never trimmed, and deleting an entry leaves its line, so a late retry can't bring a deleted entry back."""
        return self.data.setdefault("client_ids", {})

    async def create_once(
        self, client_id: str | None, request: dict[str, Any], create: Callable[[], Awaitable[dict[str, Any]]]
    ) -> tuple[dict[str, Any], bool]:
        """Run `create` once per client_id. `create` makes the entries and returns what the ledger keeps about them
        ({"entry_id", "date"}, or {"copy": …}). Returns that record and whether it is a repeat (nothing made this time).

        Calls with the same id wait for each other, so two at once (a double tap, a retry while the first is still being
        worked out) make one entry. The same id with a different request is refused (`client_id_reused`). Without a
        client_id, `create` just runs."""
        if not client_id:
            return await create(), False
        wanted = payload_hash(request)
        lock, users = self._creating.setdefault(client_id, (asyncio.Lock(), [0]))
        users[0] += 1
        try:
            async with lock:
                if (done := self.client_ids.get(client_id)) is not None:
                    if done.get("payload_hash") != wanted:
                        raise client_id_reused()
                    return done, True
                record = {
                    **await create(),
                    "payload_hash": wanted,
                    "at": dt_util.now().isoformat(timespec="seconds"),
                }
                self.client_ids[client_id] = record
                self.async_changed()
                return record, False
        finally:
            users[0] -= 1
            if not users[0]:
                self._creating.pop(client_id, None)

    # ---------- writing ----------

    def add(self, d: str, f: dict[str, Any]) -> dict[str, Any]:
        entry = self._new_entry(f)
        self._recount(entry)
        return self._insert(d, entry)

    def _insert(self, d: str, entry: dict[str, Any]) -> dict[str, Any]:
        self.data["days"].setdefault(d, []).append(entry)
        self.async_changed()
        return entry

    @staticmethod
    def _new_entry(f: dict[str, Any]) -> dict[str, Any]:
        """A new entry (rev 1) from a log request: label and barcode food is per_100 × grams, anything else its numbers for
        one portion times the portions."""
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
            "rev": 1,
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
        if f.get("client_id"):
            entry["client_id"] = str(f["client_id"])
        return entry

    @staticmethod
    def _check_rev(e: dict[str, Any], expected_rev: int | None) -> None:
        """An app that says which revision it is changing gets `conflict` when the entry has changed since."""
        if expected_rev is not None and rev(e) != expected_rev:
            raise conflict()

    @staticmethod
    def _changed_entry(e: dict[str, Any]) -> None:
        e["rev"] = rev(e) + 1

    def update(self, d: str, entry_id: str, f: dict[str, Any], expected_rev: int | None = None) -> dict[str, Any] | None:
        for e in self.entries(d):
            if e["id"] != entry_id:
                continue
            self._check_rev(e, expected_rev)
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
            self._changed_entry(e)
            self.async_changed()
            return e
        return None

    def renumber(
        self, d: str, entry_id: str, values: dict[str, Any], ref: str, note: str, expected_rev: int | None = None
    ) -> dict[str, Any] | None:
        """New numbers for one portion of a planned entry (from its recipe); its portions stay. With `expected_rev` (work
        that waited on the AI), nothing changes when the entry changed meanwhile or its numbers are now the person's own:
        a newer edit always wins."""
        e = self.find(d, entry_id)
        if e is None or (expected_rev is not None and (rev(e) != expected_rev or e.get("edited"))):
            return None
        e.update(per_portion=nums(values), ref=ref, note=note)
        self._recount(e)
        self._changed_entry(e)
        self.async_changed()
        return e

    def follow(self, ref: str, values: dict[str, Any], start: str) -> int:
        """A recipe's numbers changed: its entries from `start` on whose numbers weren't edited take them (portions stay)."""
        changed = 0
        for d in sorted(x for x in self.data["days"] if x >= start):
            for e in self.entries(d):
                if e.get("ref") == ref and not e.get("edited") and nums(e.get("per_portion")) != nums(values):
                    e["per_portion"] = nums(values)
                    self._recount(e)
                    self._changed_entry(e)
                    changed += 1
        if changed:
            self.async_changed()
        return changed

    def delete(self, d: str, entry_id: str, dismiss: bool = True, expected_rev: int | None = None) -> dict[str, Any] | None:
        """Remove an entry. Removing one that came from the meal plan remembers it, so the plan doesn't put it back."""
        entries = self.entries(d)
        for e in entries:
            if e["id"] == entry_id:
                self._check_rev(e, expected_rev)
                if dismiss and e.get("plan_key"):
                    self.data.setdefault("dismissed", {})[e["plan_key"]] = e["name"].lower()
                entries.remove(e)
                if not entries:
                    self.data["days"].pop(d, None)
                self.async_changed()
                return e
        return None

    def set_photo(self, d: str, entry_id: str, photo: str, expected_rev: int | None = None) -> dict[str, Any] | None:
        if (e := self.find(d, entry_id)) is None:
            return None
        self._check_rev(e, expected_rev)
        e["photo"] = photo
        self._changed_entry(e)
        self.async_changed()
        return e

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
            record["added"][d] = [self._insert(d, self._copied(e))["id"] for e in food]
        self._undo = [*self._undo[-9:], record]
        return record

    def _copied(self, e: dict[str, Any]) -> dict[str, Any]:
        """A new entry eaten exactly like `e`: the same portions, grams and numbers (typed ones too), kept as they are rather
        than worked out again from a label or barcode's per_100. Planned food comes along as plain food."""
        c = {k: v for k, v in e.items() if k not in NOT_COPIED}
        if c.get("source") == "plan":
            c["source"] = "again"
            c.pop("note", None)
        entry = self._new_entry({**c, "edited": e.get("edited", False)})
        entry.update(
            portions=num(e.get("portions")) or 1.0,
            per_portion=nums(e.get("per_portion") or e),
            **{k: round(num(e.get(k)), 1) for k in NUM},
        )
        if isinstance(e.get("per_100"), dict):
            entry.update({k: copy.deepcopy(e[k]) for k in ("per_100", "grams", "unit") if k in e})
        return entry

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

    # ---------- an exact Undo for a change made with other services ----------

    def snapshot(
        self, token: str, entry: tuple[str, str] | None, ref: str | None, start: str, book: Book | None
    ) -> dict[str, Any]:
        """Exact copies of what a change may touch, so `restore` can put it back as it was: one entry (date, id), every
        entry from `start` on with ref `ref` (a recipe's new numbers follow into them), and the recipe's book item. The
        last ten, until a restart; the same token again replaces its snapshot. Returns the record (deep copies)."""
        picked: dict[tuple[str, str], dict[str, Any]] = {}
        if entry:
            e = next((x for x in self.entries(entry[0]) if x["id"] == entry[1]), None)
            if e is not None:
                picked[(entry[0], e["id"])] = copy.deepcopy(e)
        if ref:
            for d in sorted(x for x in self.data["days"] if x >= start):
                for e in self.entries(d):
                    if e.get("ref") == ref:
                        picked[(d, e["id"])] = copy.deepcopy(e)
        record: dict[str, Any] = {"token": token, "entries": [(d, e) for (d, _), e in picked.items()]}
        if ref and book is not None:
            record["book"] = (ref, copy.deepcopy(book.get(ref)))
        self._snapshots = [*[r for r in self._snapshots if r["token"] != token][-9:], record]
        return record

    def restore(self, token: str, book: Book | None) -> dict[str, Any] | None:
        """Puts back what `snapshot` kept, exactly: each entry in its place (at the end of its day if it was removed
        since) with a new revision (putting it back is a change too), and the book item (or no item, when there was
        none). One use per token; None when the token is unknown or used."""
        record = next((r for r in self._snapshots if r["token"] == token), None)
        if record is None:
            return None
        for d, kept in record["entries"]:
            entries = self.data["days"].setdefault(d, [])
            i = next((n for n, x in enumerate(entries) if x["id"] == kept["id"]), None)
            back = copy.deepcopy(kept)
            back["rev"] = max(rev(kept), rev(entries[i]) if i is not None else 0) + 1
            if i is None:
                entries.append(back)
            else:
                entries[i] = back
        if "book" in record and book is not None:
            book.put(record["book"][0], copy.deepcopy(record["book"][1]))
        self._snapshots.remove(record)
        self.async_changed()
        return {"entries": len(record["entries"]), "book": "book" in record}

    def rename_source(self, old: str, new: str) -> int:
        """Every entry whose source is `old` gets `new` (data brought over from another app or an older setup)."""
        n = 0
        for entries in self.data["days"].values():
            for e in entries:
                if e.get("source") == old:
                    e["source"] = new
                    n += 1
        if n:
            self.async_changed()
        return n

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

    def version(self, key: str) -> str:
        """`key`'s numbers as they are now (and where they came from): work that asks the AI first notes this, and keeps
        its answer only when it is still the same afterwards, so numbers typed meanwhile are never overwritten."""
        item = self.items.get(key) or {}
        return canonical_hash([nums(item), item.get("source"), item.get("completed")])

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
    def put(self, key: str, item: dict[str, Any] | None) -> None:
        """`key` exactly as `item` (None: no item), for an exact Undo."""
        if item is None:
            self.items.pop(key, None)
        else:
            self.items[key] = item
        self.store.async_delay_save(lambda: self.items, 1)

    @callback
    def rename_source(self, old: str, new: str) -> int:
        """Every item whose source is `old` gets `new`."""
        hits = [x for x in self.items.values() if x.get("source") == old]
        for x in hits:
            x["source"] = new
        if hits:
            self.store.async_delay_save(lambda: self.items, 1)
        return len(hits)

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
