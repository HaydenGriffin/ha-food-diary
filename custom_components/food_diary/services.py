"""Food diary services. Each one works on the caller's own diary (the person linked to their Home Assistant user); add
`person` to choose another, and with a single diary set up, calls from automations and scripts use that one.

Creating services (log_food, copy_day) take an optional `client_id`: the same id again returns the first result with
`duplicate: true` (and `deleted: true` once its entry is gone) instead of creating again. Changing services take an optional
`expected_rev`: the entry's `rev` as the caller last saw it; when it has changed since, the call fails with `conflict`.

A macro left out of log_food (or listed in its `unknown`, as an estimate, a saved meal or a recent food lists it) is
unknown, not 0: the entry keeps it in `unknown`, and get_day / get_history / get_week_review count it in `incomplete`.
update_food giving a macro makes it known; listing it in update_food's `unknown` makes it unknown again (Undo).

update_food with `per_100` and `grams` switches an entry to by weight; its answer has the entry as it was (`before`), and
restore_food puts that back exactly (Undo).

log_food with source "batch" and ref a batch id (batches.py) takes the batch's name and cooked per_100 when they aren't
given: grams is then all a bowl of it needs.

Veg (`veg_g`, grams of vegetables and pulses) goes with the other numbers wherever they're taken: log_food (per portion,
or in per_100), update_food (for the whole entry), save_meal's foods, a batch ingredient's per_100. Left out, it's unknown.

set_fullness and get_fullness keep how full the person was after a meal (fullness.py)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
import copy
from datetime import date, timedelta
import math
from typing import TYPE_CHECKING, Any

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.service import async_register_admin_service
import voluptuous as vol

from .batches import Batches, recent, regulars
from .const import DISH_SOURCES, DOMAIN, MACROS, MEALS, NUM, SOURCES, VEG
from .diary import ENTRY_MAX, Book, Diary, entry_unknown, num, nums, today, unknown_in, values_of
from .fullness import ANSWERS, ASKED, Fullness
from .notifications import fire_logged
from .photos import with_images
from .sources import read_dishes

if TYPE_CHECKING:
    from . import FoodDiaryData

ATTR_PERSON = "person"


def finite(v: Any) -> float:
    """A number that is a real amount: not NaN, not infinite, not too big for a float."""
    try:
        f = float(v)
    except (TypeError, ValueError, OverflowError) as err:
        raise vol.Invalid("expected a number") from err
    if isinstance(v, bool) or not math.isfinite(f):
        raise vol.Invalid("expected a number")
    return f


AMOUNT = vol.All(finite, vol.Range(min=0, max=20000))  # a nutrition number or a weight
NUMBERS = {vol.Optional(k): AMOUNT for k in NUM}
WITH_VEG = {**NUMBERS, vol.Optional(VEG): AMOUNT}  # the five numbers and veg, which isn't a goal
BASE = {vol.Optional(ATTR_PERSON): cv.entity_id}
DATE = vol.Optional("date")
PORTIONS = vol.All(finite, vol.Range(min=0.05, max=50))
RECIPE_PORTIONS = vol.All(vol.Coerce(int), vol.Range(min=1, max=24))
CLIENT_ID = vol.All(cv.string, vol.Match(r"^[A-Za-z0-9_-]{8,64}$"))  # made by the app, once per action (a UUID is fine)
EXPECTED_REV = vol.All(vol.Coerce(int), vol.Range(min=1))
UNKNOWN = vol.All(cv.ensure_list, [vol.In(MACROS)])  # macros not known (sent back as an estimate or a saved meal gave them)

PER_100 = vol.Schema(WITH_VEG, extra=vol.REMOVE_EXTRA)
BATCH_PER_100 = vol.Schema(  # kcal is needed (0 is fine: water)
    {**{vol.Required(k) if k == "kcal" else vol.Optional(k): AMOUNT for k in NUM}, vol.Optional(VEG): AMOUNT},
    extra=vol.REMOVE_EXTRA,
)
BY_WEIGHT = vol.Schema(  # log_food's and update_food's per_100 (kcal needed, 0 is fine) with the macros it doesn't know
    {**BATCH_PER_100.schema, vol.Optional("unknown"): UNKNOWN}, extra=vol.REMOVE_EXTRA
)
GRAMS = AMOUNT
PLAN_KEY = vol.All(cv.string, vol.Match(r"^\d{4}-\d{2}-\d{2}\|(breakfast|lunch|dinner)(\|side:\d{1,2})?$"))
LOG_SCHEMA = vol.Schema(
    {
        **BASE,
        vol.Optional("name"): cv.string,  # required, except for a batch (its name)
        **WITH_VEG,
        vol.Optional("portions", default=1): PORTIONS,
        vol.Optional("meal"): vol.In(MEALS),
        DATE: cv.date,
        vol.Optional("source", default="manual"): vol.In((*SOURCES, "plan")),  # "plan": putting a planned meal back
        vol.Optional("ref"): cv.string,
        vol.Optional("note"): cv.string,
        vol.Optional("barcode"): cv.string,
        vol.Optional("per_100"): BY_WEIGHT,
        vol.Optional("grams"): GRAMS,
        vol.Optional("unit"): vol.In(("g", "ml")),
        vol.Optional("edited"): cv.boolean,
        vol.Optional("photo"): cv.string,
        vol.Optional("image_url"): cv.string,
        vol.Optional("client_id"): CLIENT_ID,
        vol.Optional("unknown"): UNKNOWN,
        vol.Optional("plan_key"): PLAN_KEY,
    }
)
UPDATE_SCHEMA = vol.Schema(
    {
        **BASE,
        vol.Required("entry_id"): cv.string,
        DATE: cv.date,
        **WITH_VEG,
        vol.Optional("portions"): PORTIONS,
        vol.Optional("grams"): vol.All(finite, vol.Range(min=0.1, max=20000)),
        vol.Optional("meal"): vol.In(MEALS),
        vol.Optional("name"): cv.string,
        vol.Optional("checked"): cv.boolean,
        vol.Optional("unknown"): UNKNOWN,
        vol.Optional("per_100"): BY_WEIGHT,  # with grams: the entry by weight from now on
        vol.Optional("unit"): vol.In(("g", "ml")),
        vol.Optional("edited"): cv.boolean,
        vol.Optional("expected_rev"): EXPECTED_REV,
    }
)
ENTRY_NUMBER = vol.All(finite, vol.Range(min=0, max=ENTRY_MAX))  # anything an entry can have (update_food stays under it)
ENTRY_NUMBERS = {**{vol.Required(k): ENTRY_NUMBER for k in NUM}, vol.Optional(VEG): ENTRY_NUMBER}
BEFORE = vol.Schema(  # an entry as update_food's `before` gave it: what restore_food puts back (the rest is dropped)
    {
        vol.Optional("id"): cv.string,
        vol.Required("name"): vol.All(cv.string, vol.Length(min=1, max=80)),
        vol.Required("meal"): vol.In(MEALS),
        vol.Required("portions"): vol.All(finite, vol.Range(min=0.01, max=1000)),
        vol.Required("per_portion"): vol.Schema(ENTRY_NUMBERS, extra=vol.REMOVE_EXTRA),
        **ENTRY_NUMBERS,
        vol.Optional("per_100"): vol.Schema(ENTRY_NUMBERS, extra=vol.REMOVE_EXTRA),
        vol.Optional("grams"): GRAMS,
        vol.Optional("unit"): vol.In(("g", "ml")),
        vol.Optional("unknown"): UNKNOWN,
        vol.Optional("edited"): cv.boolean,
        vol.Optional("checked"): cv.boolean,
    },
    extra=vol.REMOVE_EXTRA,
)
RESTORE_FOOD_SCHEMA = vol.Schema(
    {
        **BASE,
        vol.Required("entry_id"): cv.string,
        DATE: cv.date,
        vol.Required("entry"): BEFORE,
        vol.Required("expected_rev"): EXPECTED_REV,
    }
)
ENTRY_SCHEMA = vol.Schema(
    {**BASE, vol.Required("entry_id"): cv.string, DATE: cv.date, vol.Optional("expected_rev"): EXPECTED_REV}
)
DAY_SCHEMA = vol.Schema({**BASE, DATE: cv.date})
HISTORY_SCHEMA = vol.Schema(
    {**BASE, DATE: cv.date, vol.Optional("days", default=7): vol.All(vol.Coerce(int), vol.Range(min=1, max=366))}
)
GOALS_SCHEMA = vol.Schema({**BASE, **NUMBERS})
ESTIMATE_ITEM = vol.Schema(  # an ingredient to work out (kind "ingredients")
    {
        vol.Required("name"): vol.All(cv.string, vol.Length(min=1, max=120)),
        vol.Optional("grams"): GRAMS,
        vol.Optional("amount"): cv.string,
        vol.Optional("barcode"): cv.string,
        vol.Optional("per_100"): PER_100,
        vol.Optional("unknown"): UNKNOWN,
    },
    extra=vol.REMOVE_EXTRA,
)
BATCH_INGREDIENT = vol.Schema(
    {
        vol.Required("name"): vol.All(cv.string, vol.Length(min=1, max=120)),
        vol.Required("grams"): GRAMS,
        vol.Required("per_100"): BATCH_PER_100,
        vol.Optional("cooked_factor"): finite,  # kept within 0.2 to 4
        vol.Optional("ref"): cv.string,
        vol.Optional("unknown"): UNKNOWN,
    },
    extra=vol.REMOVE_EXTRA,
)
COOKED_G = vol.All(finite, vol.Range(min=1, max=50000))
BATCH_PORTIONS = vol.All(finite, vol.Range(min=0.5, max=100))
SAVE_BATCH_SCHEMA = vol.Schema(
    {
        **BASE,
        vol.Required("name"): vol.All(cv.string, vol.Length(min=1, max=80)),
        DATE: cv.date,
        vol.Required("ingredients"): vol.All(cv.ensure_list, vol.Length(min=1, max=50), [BATCH_INGREDIENT]),
        vol.Optional("cooked_g"): COOKED_G,
        vol.Optional("portions"): BATCH_PORTIONS,
        vol.Optional("recipe_id"): cv.string,
    }
)
UPDATE_BATCH_SCHEMA = vol.Schema(
    {**BASE, vol.Required("batch_id"): cv.string, vol.Optional("cooked_g"): COOKED_G, vol.Optional("portions"): BATCH_PORTIONS}
)
BATCH_SCHEMA = vol.Schema({**BASE, vol.Required("batch_id"): cv.string})
GET_BATCHES_SCHEMA = vol.Schema({**BASE, vol.Optional("days", default=5): vol.All(vol.Coerce(int), vol.Range(min=1, max=400))})
PLATE = vol.Schema(  # what a meal photo is on, so the AI sizes the portions against it
    {vol.Required("kind"): vol.In(("plate", "bowl")), vol.Optional("cm"): vol.All(finite, vol.Range(min=10, max=40))}
)
MEDIA = vol.All(cv.ensure_list, vol.Length(min=1, max=4), [cv.string])  # a menu's photos, base64
ESTIMATE_SCHEMA = vol.Schema(
    {
        **BASE,
        vol.Required("kind"): vol.In(("photo", "label", "barcode", "text", "dish", "ingredients", "per_100", "menu")),
        vol.Optional("name", default=""): vol.All(cv.string, vol.Length(max=120)),
        vol.Optional("image"): cv.string,
        vol.Optional("amount", default=""): cv.string,
        vol.Optional("hint", default=""): cv.string,
        vol.Optional("text", default=""): cv.string,
        vol.Optional("barcode", default=""): cv.string,
        vol.Optional("dish_id"): cv.string,
        vol.Optional("dish_name", default=""): cv.string,
        vol.Optional("ingredients", default=[]): vol.All(cv.ensure_list, [cv.string]),
        vol.Optional("servings", default=0): vol.All(finite, vol.Range(min=0, max=100)),
        vol.Optional("amounts_per", default="recipe"): vol.In(("recipe", "portion")),
        vol.Optional("fresh", default=False): cv.boolean,
        vol.Optional("items", default=[]): vol.All(cv.ensure_list, vol.Length(max=30), [ESTIMATE_ITEM]),
        vol.Optional("plate"): PLATE,
        vol.Optional("media"): MEDIA,
    }
)
PHOTO_SCHEMA = vol.Schema(
    {
        **BASE,
        vol.Required("entry_id"): cv.string,
        DATE: cv.date,
        vol.Required("image"): cv.string,
        vol.Optional("expected_rev"): EXPECTED_REV,
    }
)
COPY_SCHEMA = vol.Schema(
    {
        **BASE,
        vol.Required("from"): cv.date,
        vol.Required("to"): vol.All(cv.ensure_list, [cv.date]),
        vol.Optional("meal"): vol.In(MEALS),
        vol.Optional("replace", default=False): cv.boolean,
        vol.Optional("client_id"): CLIENT_ID,
    }
)
UNCOPY_SCHEMA = vol.Schema({**BASE, vol.Required("token"): cv.string})
MAX_FOODS = 50
SAVED_FOOD = vol.Schema(  # one food of save_meal's `foods`, as log_food takes it (only these are kept)
    {vol.Required("name"): cv.string, **WITH_VEG, vol.Optional("unknown"): UNKNOWN}, extra=vol.REMOVE_EXTRA
)
SAVE_MEAL_SCHEMA = vol.Schema(
    {
        **BASE,
        vol.Required("name"): cv.string,
        DATE: cv.date,
        vol.Required("meal"): vol.In(MEALS),
        vol.Optional("foods"): vol.All(cv.ensure_list, vol.Length(max=MAX_FOODS), [SAVED_FOOD]),
    }
)
SAVED_SCHEMA = vol.Schema({**BASE, vol.Required("saved_id"): cv.string})
UPDATE_SAVED_SCHEMA = vol.Schema(
    {**BASE, vol.Required("saved_id"): cv.string, vol.Optional("name"): cv.string, vol.Optional("meal"): vol.In(MEALS)}
)
RECENT_SCHEMA = vol.Schema({**BASE, vol.Optional("limit", default=30): vol.All(vol.Coerce(int), vol.Range(min=1, max=200))})
REVIEW_SCHEMA = vol.Schema(
    {**BASE, DATE: cv.date, vol.Optional("days", default=7): vol.All(vol.Coerce(int), vol.Range(min=1, max=35))}
)
SET_FULLNESS_SCHEMA = vol.Schema(
    {
        **BASE,
        DATE: cv.date,
        vol.Required("meal"): vol.In(ASKED),
        vol.Required("fullness"): vol.Any(None, vol.In(ANSWERS)),  # None takes the answer back
        vol.Optional("client_id"): CLIENT_ID,
    }
)
GET_FULLNESS_SCHEMA = vol.Schema({**BASE, vol.Optional("days", default=60): vol.All(vol.Coerce(int), vol.Range(min=1, max=400))})
DISH_SCHEMA = vol.Schema({vol.Required("dish_id"): cv.string})
DISH_SET_SCHEMA = vol.Schema(
    {
        vol.Required("dish_id"): cv.string,
        vol.Required("kcal"): AMOUNT,
        **{vol.Optional(k): AMOUNT for k in NUM if k != "kcal"},
        vol.Optional("source", default="own"): vol.In(DISH_SOURCES),
        vol.Optional("portions"): RECIPE_PORTIONS,
    }
)
CHECK_SCHEMA = vol.Schema(
    {
        **BASE,
        vol.Optional("dish_id"): cv.string,
        vol.Optional("entry_id"): cv.string,
        DATE: cv.date,
        vol.Optional("portions"): RECIPE_PORTIONS,
    }
)
DISMISS_SCHEMA = vol.Schema({**BASE, vol.Required("dish_id"): cv.string})
TOKEN = vol.All(cv.string, vol.Match(r"^[A-Za-z0-9_-]{4,40}$"))
SNAPSHOT_SCHEMA = vol.Schema(
    {**BASE, vol.Required("token"): TOKEN, vol.Optional("dish_id"): cv.string, vol.Optional("entry_id"): cv.string, DATE: cv.date}
)
RESTORE_SCHEMA = vol.Schema({**BASE, vol.Required("token"): TOKEN})
RENAME_SCHEMA = vol.Schema(
    {
        vol.Required("from"): vol.All(cv.string, vol.Length(min=1, max=40)),
        vol.Required("to"): vol.In(sorted({*SOURCES, *DISH_SOURCES})),
    }
)
INTERNAL = ("estimate", "checked_for", "dismissed", "typed_for")  # the numbers check's own notes on a book entry

NOT_IN_DIARY = "That entry isn't in the diary on that day."
NO_BATCH = "That batch isn't there."
NOT_UNDOABLE = "That change can't be undone any more."  # apps may match this text: keep it


def loaded(hass: HomeAssistant) -> list[ConfigEntry]:
    return [e for e in hass.config_entries.async_entries(DOMAIN) if e.state is ConfigEntryState.LOADED]


@callback
def resolve(hass: HomeAssistant, call: ServiceCall) -> FoodDiaryData:
    """The diary a call is about: `person`, else the caller's own, else the only one."""
    entries = loaded(hass)
    if not entries:
        raise ServiceValidationError("No food diary is set up yet.")
    if person := call.data.get(ATTR_PERSON):
        for e in entries:
            if e.runtime_data.person == person:
                return e.runtime_data
        raise ServiceValidationError(f"{person} has no food diary.")
    if user := call.context.user_id:
        for e in entries:
            state = hass.states.get(e.runtime_data.person)
            if state and state.attributes.get("user_id") == user:
                return e.runtime_data
    if len(entries) == 1:
        return entries[0].runtime_data
    raise ServiceValidationError("Whose diary? Add person: to the action.")


def day_of(call: ServiceCall) -> str:
    d = call.data.get("date")
    return d.isoformat() if d else today()


def dishes_book(hass: HomeAssistant) -> Book:
    return hass.data[DOMAIN]["dishes"]


def public(item: dict[str, Any]) -> dict[str, Any]:
    """A book entry without the numbers check's internal notes."""
    return {k: v for k, v in item.items() if k not in INTERNAL}


def repeat_flags(repeat: bool, deleted: bool) -> dict[str, bool]:
    return {**({"duplicate": True} if repeat else {}), **({"deleted": True} if deleted else {})}


def from_batch(diary: Diary, f: dict[str, Any]) -> None:
    """A bowl of a batch (source "batch", ref its id): the batch's name and cooked per_100, where log_food didn't give them."""
    if f.get("source") != "batch" or (f.get("name") and f.get("per_100")):
        return
    if (b := Batches(diary).get(str(f.get("ref") or ""))) is None:
        raise ServiceValidationError(NO_BATCH)
    f.setdefault("name", b["name"])
    if not f.get("per_100"):
        f["per_100"] = dict(b["per_100"])
        f["unknown"] = [k for k in MACROS if k in (*b.get("unknown", ()), *f.get("unknown", ()))]


def check_switch(f: dict[str, Any]) -> None:
    """update_food's switch to by weight: `per_100` (with `unit`, `edited`) only together with `grams`, and alone: it sets
    the whole amount and the numbers."""
    if "per_100" not in f:
        if "unit" in f or "edited" in f:
            raise ServiceValidationError("unit and edited go with per_100 and grams.")
        return
    if "grams" not in f:
        raise ServiceValidationError("per_100 needs grams: how much was eaten.")
    if both := [k for k in (*NUM, VEG, "portions", "unknown") if k in f]:
        raise ServiceValidationError(
            f"per_100 and grams set the amount and the numbers: don't send {', '.join(both)} with them "
            "(per_100 has its own unknown and veg_g)."
        )


def logged(diary: Diary, record: dict[str, Any], repeat: bool) -> dict[str, Any]:
    """log_food's answer, for a new entry or a repeated client_id: the entry as it is now (None once it's deleted)."""
    d = record["date"]
    e = diary.find(d, record["entry_id"])
    day = diary.day(d)
    return {
        "entry": e,
        "entry_id": record["entry_id"],
        "date": d,
        "totals": day["totals"],
        **({"incomplete": day["incomplete"]} if "incomplete" in day else {}),
        **repeat_flags(repeat, e is None),
    }


def copied(diary: Diary, record: dict[str, Any], repeat: bool) -> dict[str, Any]:
    """copy_day's answer, for a new copy or a repeated client_id (`deleted` once every copied entry is gone)."""
    c = record["copy"]
    ids = [(d, i) for d, added in c["added"].items() for i in added]
    gone = bool(ids) and all(diary.find(d, i) is None for d, i in ids)
    return {
        "token": c["token"],
        "added": {d: len(i) for d, i in c["added"].items()},
        "removed": dict(c["removed"]),
        **repeat_flags(repeat, gone),
    }


@callback
def async_register_services(hass: HomeAssistant) -> None:
    """Register every food_diary service (once, in async_setup)."""

    def with_checks(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Entries from a recipe whose numbers look wrong (and weren't edited) carry the numbers check's suggestion."""
        book = dishes_book(hass)
        out = []
        for e in entries:
            check = (book.get(str(e["ref"])) or {}).get("check") if e.get("ref") and not e.get("edited") else None
            out.append({**e, "check": check} if check else e)
        return out

    async def log_food(call: ServiceCall) -> ServiceResponse:
        data = resolve(hass, call)
        f = dict(call.data)
        day = f.pop("date").isoformat() if f.get("date") else today()
        if f.get("source") == "plan" and not f.get("plan_key"):
            f["source"] = "again"  # only the plan's own entries (and Undo putting one back) are "plan"

        async def create() -> dict[str, Any]:
            from_batch(data.diary, f)  # after the client_id check: a retry still answers once the batch is gone
            if not str(f.get("name") or "").strip():
                raise ServiceValidationError("Give the food's name.")
            if f.get("per_100") is not None and not num(f.get("grams")):
                raise ServiceValidationError("per_100 needs grams: how much was eaten.")
            if not num(f.get("kcal")) and not (f.get("per_100") and num(f.get("grams"))):
                raise ServiceValidationError("Give kcal, or per_100 with grams.")
            if f.get("photo"):  # the meal photo it was estimated from: kept with the entry
                f["photo"] = await hass.data[DOMAIN]["photos"].keep_estimated(f["photo"])
            e = data.diary.add(day, f)
            fire_logged(hass, data, day, e, call.context)
            return {"entry_id": e["id"], "date": day}

        record, repeat = await data.diary.create_once(call.data.get("client_id"), dict(call.data), create)
        return logged(data.diary, record, repeat)

    async def update_food(call: ServiceCall) -> ServiceResponse:
        data = resolve(hass, call)
        changes = {k: v for k, v in call.data.items() if k not in (ATTR_PERSON, "entry_id", "date", "expected_rev")}
        check_switch(changes)
        d, entry_id = day_of(call), call.data["entry_id"]
        before = copy.deepcopy(data.diary.find(d, entry_id))
        if (e := data.diary.update(d, entry_id, changes, call.data.get("expected_rev"))) is None:
            raise ServiceValidationError(NOT_IN_DIARY)
        return {"entry": e, "before": before}

    async def restore_food(call: ServiceCall) -> ServiceResponse:
        """Undo of an update_food: the entry as its `before` was, refused (conflict) when it changed since that update."""
        before, entry_id = call.data["entry"], call.data["entry_id"]
        if before.get("id", entry_id) != entry_id:
            raise ServiceValidationError("That entry is another one: give the entry update_food answered for it.")
        if ("per_100" in before) != ("grams" in before):
            raise ServiceValidationError("An entry by weight has per_100 and grams together.")
        diary = resolve(hass, call).diary
        if (e := diary.put_back(day_of(call), entry_id, before, call.data["expected_rev"])) is None:
            raise ServiceValidationError(NOT_IN_DIARY)
        return {"entry": e}

    async def delete_food(call: ServiceCall) -> ServiceResponse:
        diary = resolve(hass, call).diary
        if diary.delete(day_of(call), call.data["entry_id"], expected_rev=call.data.get("expected_rev")) is None:
            raise ServiceValidationError(NOT_IN_DIARY)
        return {"ok": True}

    async def get_day(call: ServiceCall) -> ServiceResponse:
        data = resolve(hass, call)
        d = data.diary.day(day_of(call))
        return {**d, "entries": with_checks(with_images(hass, data, d["entries"]))}

    async def get_history(call: ServiceCall) -> ServiceResponse:
        return resolve(hass, call).diary.history(day_of(call), call.data["days"])

    async def get_recent(call: ServiceCall) -> ServiceResponse:
        data = resolve(hass, call)
        diary = data.diary
        return {
            "foods": with_images(hass, data, diary.recent(limit=call.data["limit"])),
            "usuals": {m: with_images(hass, data, u) for m, u in diary.usuals().items()},
            "saved": list(diary.saved),
        }

    async def set_goals(call: ServiceCall) -> ServiceResponse:
        return {"goals": resolve(hass, call).diary.set_goals({k: call.data[k] for k in NUM if k in call.data})}

    async def set_photo(call: ServiceCall) -> ServiceResponse:
        data = resolve(hass, call)
        name = await hass.data[DOMAIN]["photos"].keep(call.data["image"])
        if (e := data.diary.set_photo(day_of(call), call.data["entry_id"], name, call.data.get("expected_rev"))) is None:
            raise ServiceValidationError(NOT_IN_DIARY)
        return {"entry": with_images(hass, data, [e])[0]}

    async def copy_day(call: ServiceCall) -> ServiceResponse:
        diary = resolve(hass, call).diary
        targets = sorted({d.isoformat() for d in call.data["to"]})

        async def create() -> dict[str, Any]:
            r = diary.copy(call.data["from"].isoformat(), targets, call.data.get("meal"), call.data["replace"])
            removed = {d: len(g) for d, g in r["removed"].items()}
            added = {d: list(ids) for d, ids in r["added"].items()}
            return {"copy": {"token": r["token"], "added": added, "removed": removed}}

        record, repeat = await diary.create_once(call.data.get("client_id"), dict(call.data), create)
        return copied(diary, record, repeat)

    async def undo_copy(call: ServiceCall) -> ServiceResponse:
        if not resolve(hass, call).diary.uncopy(call.data["token"]):
            raise ServiceValidationError("That copy can't be undone any more.")
        return {"ok": True}

    async def save_meal(call: ServiceCall) -> ServiceResponse:
        diary = resolve(hass, call).diary
        if "foods" in call.data:  # these foods, as they were added (not whatever else is in that meal)
            saved = diary.save_foods(call.data["name"], call.data["meal"], call.data["foods"])
        else:
            saved = diary.save_meal(call.data["name"], day_of(call), call.data["meal"])
        if saved is None:
            raise ServiceValidationError("Nothing is logged in that meal on that day.")
        return {"saved": saved}

    async def update_saved_meal(call: ServiceCall) -> ServiceResponse:
        saved = resolve(hass, call).diary.update_saved(call.data["saved_id"], call.data.get("name"), call.data.get("meal"))
        if saved is None:
            raise ServiceValidationError("That saved meal isn't there.")
        return {"saved": saved}

    async def delete_saved_meal(call: ServiceCall) -> ServiceResponse:
        if resolve(hass, call).diary.delete_saved(call.data["saved_id"]) is None:
            raise ServiceValidationError("That saved meal isn't there.")
        return {"ok": True}

    async def save_batch(call: ServiceCall) -> ServiceResponse:
        batch, refreshed = Batches(resolve(hass, call).diary).save(dict(call.data), day_of(call))
        return {"batch": batch, "refreshed": refreshed}

    async def update_batch(call: ServiceCall) -> ServiceResponse:
        c = call.data
        if (r := Batches(resolve(hass, call).diary).update(c["batch_id"], c.get("cooked_g"), c.get("portions"))) is None:
            raise ServiceValidationError(NO_BATCH)
        return {"batch": r[0], "refreshed": r[1]}

    async def delete_batch(call: ServiceCall) -> ServiceResponse:
        if Batches(resolve(hass, call).diary).delete(call.data["batch_id"]) is None:
            raise ServiceValidationError(NO_BATCH)
        return {"ok": True}

    async def get_batches(call: ServiceCall) -> ServiceResponse:
        batches = Batches(resolve(hass, call).diary).prune()
        return {"recent": recent(batches, today(), call.data["days"]), "regulars": regulars(batches)}

    async def get_week_review(call: ServiceCall) -> ServiceResponse:
        d = call.data.get("date")
        end = d.isoformat() if d else (date.fromisoformat(today()) - timedelta(days=1)).isoformat()
        return resolve(hass, call).diary.review(end, call.data["days"])

    async def set_fullness(call: ServiceCall) -> ServiceResponse:
        """How full the person was after a meal (None takes it back). With a client_id it is set once: a retry, or one
        sent again late, changes nothing and answers what is there now."""
        diary = resolve(hass, call).diary
        d, meal = day_of(call), call.data["meal"]

        async def create() -> dict[str, Any]:
            Fullness(diary).set(d, meal, call.data["fullness"])
            return {"fullness": {"date": d, "meal": meal}}

        _, repeat = await diary.create_once(call.data.get("client_id"), dict(call.data), create)
        return {"date": d, "meal": meal, "fullness": Fullness(diary).get(d, meal), **repeat_flags(repeat, False)}

    async def get_fullness(call: ServiceCall) -> ServiceResponse:
        return {"answers": Fullness(resolve(hass, call).diary).answers(call.data["days"])}

    async def get_dishes(call: ServiceCall) -> ServiceResponse:
        """The dish library with one portion's numbers where the recipe book knows them (empty without a library)."""
        data = resolve(hass, call)
        book = dishes_book(hass)
        out = []
        for x in read_dishes(data.recipes) or []:
            n = book.get(x["id"]) or {}
            name, en = str(x.get("name") or "").strip(), str(x.get("name_en") or "").strip()
            # a dish named in another language keeps that name on the plan and shows both: "Owsianka (Porridge)"
            translated = str(x.get("lang") or "en") != "en" and name and en and name.lower() != en.lower()
            out.append(
                {
                    "id": x["id"],
                    "name": name if translated else (en or name),
                    "title": f"{name} ({en})" if translated else (en or name),
                    "image": x.get("image") or "",
                    "source": x.get("source") or "",
                    "status": x.get("status") or "",
                    "favourite": bool(x.get("favourite")),
                    "times": int(num(x.get("times"))),
                    "meal_types": x.get("meal_types") or [],
                    "added": str(x.get("added") or ""),
                    **({**values_of(n), "kcal_source": n.get("source")} if num(n.get("kcal")) else {}),
                    **({"check": n["check"]} if n.get("check") else {}),
                }
            )
        return {"dishes": out}

    async def sync_plan(call: ServiceCall) -> ServiceResponse:
        """Sync the meal plan now; with no meal plan sensor set it does nothing."""
        planner = resolve(hass, call).planner
        if planner is None:
            return {"changed": 0, "completed": []}
        changed = await planner.async_sync()
        return {"changed": changed, "completed": planner.completed}

    async def estimate(call: ServiceCall) -> ServiceResponse:
        est = resolve(hass, call).estimator
        c = call.data
        kind = c["kind"]
        if kind in ("photo", "label") and not c.get("image"):
            raise ServiceValidationError("Add the photo (image, base64).")
        if kind == "photo":
            return await est.photo(c["image"], c["hint"], c.get("plate"))
        if kind == "menu":
            return await est.menu(c.get("media") or ([c["image"]] if c.get("image") else []), c["hint"])
        if kind == "label":
            return await est.label(c["image"], c["amount"], c["barcode"])
        if kind == "barcode":
            return await est.barcode(c["barcode"], c["amount"])
        if kind == "text":
            return await est.text(c["text"])
        if kind == "ingredients":
            return await est.ingredients(c["items"], c["dish_name"])
        if kind == "per_100":
            return await est.per_100(c["name"], c["hint"])
        if not c.get("dish_id"):  # a dish by its name, eaten out
            return await est.eating_out(c["name"] or c["dish_name"], c["hint"])
        return await est.dish(c["dish_id"], c["dish_name"], c["ingredients"], c["servings"], c["amounts_per"], c["fresh"])

    async def get_dish_nutrition(call: ServiceCall) -> ServiceResponse:
        return public(dishes_book(hass).get(call.data["dish_id"]) or {})

    async def set_dish_nutrition(call: ServiceCall) -> ServiceResponse:
        """A recipe's numbers for one portion (and how many portions it makes): any doubt about them goes, and diary
        entries for it from today on whose numbers weren't edited follow the new numbers."""
        dish_id = call.data["dish_id"]
        values = {**values_of(call.data), "source": call.data["source"]}  # a macro left out is unknown, not 0
        if call.data.get("portions"):
            values["portions"] = call.data["portions"]
        kept = dishes_book(hass).set(dish_id, values)
        refreshed = sum(e.runtime_data.diary.follow(dish_id, values_of(kept), today()) for e in loaded(hass))
        for e in loaded(hass):
            e.runtime_data.checker.schedule(5)
        return {**public(kept), "refreshed": refreshed}

    async def check_numbers(call: ServiceCall) -> ServiceResponse:
        """Numbers for a recipe (or a diary entry) against what its ingredients add up to, per portion."""
        data = resolve(hass, call)
        library = data.checker.library()
        if dish_id := call.data.get("dish_id"):
            if not (dish := library.get(dish_id)):
                raise ServiceValidationError("That isn't a recipe in the dish library.")
            name = str(dish.get("name_en") or dish.get("name") or "")
            known = dishes_book(hass).get(dish_id)
            yours, key, unknown = nums(known), dish_id, unknown_in(known) if known else []
        elif entry_id := call.data.get("entry_id"):
            e = next((x for x in data.diary.entries(day_of(call)) if x["id"] == entry_id), None)
            if e is None:
                raise ServiceValidationError(NOT_IN_DIARY)
            ref = str(e.get("ref") or "")
            dish = library.get(ref) if ref else None
            yours, key, name = nums(e.get("per_portion") or e), (ref if dish else None), e["name"]
            unknown = entry_unknown(e)
        else:
            raise ServiceValidationError("Give dish_id, or entry_id and date.")
        return await data.checker.compare(yours, key, dish, name, call.data.get("portions"), unknown)

    async def dismiss_check(call: ServiceCall) -> ServiceResponse:
        if resolve(hass, call).checker.dismiss(call.data["dish_id"]) is None:
            raise ServiceValidationError("That recipe isn't in the recipe book.")
        return {"ok": True}

    async def snapshot(call: ServiceCall) -> ServiceResponse:
        """Before a change made with other services (update_food, set_dish_nutrition): exact copies of the entry, the
        recipe's entries from today on (its new numbers follow into them) and its book item, for restore_snapshot."""
        data = resolve(hass, call)
        entry_id, dish_id = call.data.get("entry_id"), call.data.get("dish_id")
        entry = (day_of(call), entry_id) if entry_id else None
        if entry and not any(e["id"] == entry_id for e in data.diary.entries(entry[0])):
            raise ServiceValidationError(NOT_IN_DIARY)
        book = dishes_book(hass)
        r = data.diary.snapshot(call.data["token"], entry, dish_id, today(), book)
        e = next((x for d, x in r["entries"] if entry and (d, x["id"]) == entry), None)
        known = book.get(dish_id) if dish_id else None
        kept = None
        if e:
            kept = {k: e.get(k) for k in ("id", "rev", "name", "meal", "portions", "ref", "source", "edited", *NUM)}
            kept |= {"date": entry[0], "per_portion": nums(e.get("per_portion"))}
            if u := entry_unknown(e):
                kept["unknown"] = u
        return {"token": r["token"], "entries": len(r["entries"]), "entry": kept, "dish": public(known) if known else None}

    async def restore_snapshot(call: ServiceCall) -> ServiceResponse:
        if (r := resolve(hass, call).diary.restore(call.data["token"], dishes_book(hass))) is None:
            raise ServiceValidationError(NOT_UNDOABLE)
        return {"ok": True, **r}

    async def rename_source(call: ServiceCall) -> ServiceResponse:
        """Every entry and recipe-book item with source `from` gets `to` (data brought over from elsewhere). Admin only."""
        old, new = call.data["from"], call.data["to"]
        entries = {e.runtime_data.person: e.runtime_data.diary.rename_source(old, new) for e in loaded(hass)}
        recipes = dishes_book(hass).rename_source(old, new) if new in DISH_SOURCES else 0
        return {"entries": entries, "recipes": recipes}

    async_register_admin_service(
        hass, DOMAIN, "rename_source", rename_source, schema=RENAME_SCHEMA, supports_response=SupportsResponse.OPTIONAL
    )

    handlers: list[tuple[str, Callable[[ServiceCall], Awaitable[ServiceResponse]], vol.Schema, SupportsResponse]] = [
        ("log_food", log_food, LOG_SCHEMA, SupportsResponse.OPTIONAL),
        ("update_food", update_food, UPDATE_SCHEMA, SupportsResponse.OPTIONAL),
        ("restore_food", restore_food, RESTORE_FOOD_SCHEMA, SupportsResponse.OPTIONAL),
        ("delete_food", delete_food, ENTRY_SCHEMA, SupportsResponse.OPTIONAL),
        ("get_day", get_day, DAY_SCHEMA, SupportsResponse.ONLY),
        ("get_history", get_history, HISTORY_SCHEMA, SupportsResponse.ONLY),
        ("get_recent", get_recent, RECENT_SCHEMA, SupportsResponse.ONLY),
        ("set_goals", set_goals, GOALS_SCHEMA, SupportsResponse.OPTIONAL),
        ("set_photo", set_photo, PHOTO_SCHEMA, SupportsResponse.OPTIONAL),
        ("copy_day", copy_day, COPY_SCHEMA, SupportsResponse.OPTIONAL),
        ("undo_copy", undo_copy, UNCOPY_SCHEMA, SupportsResponse.OPTIONAL),
        ("save_meal", save_meal, SAVE_MEAL_SCHEMA, SupportsResponse.OPTIONAL),
        ("update_saved_meal", update_saved_meal, UPDATE_SAVED_SCHEMA, SupportsResponse.OPTIONAL),
        ("delete_saved_meal", delete_saved_meal, SAVED_SCHEMA, SupportsResponse.OPTIONAL),
        ("save_batch", save_batch, SAVE_BATCH_SCHEMA, SupportsResponse.OPTIONAL),
        ("update_batch", update_batch, UPDATE_BATCH_SCHEMA, SupportsResponse.OPTIONAL),
        ("delete_batch", delete_batch, BATCH_SCHEMA, SupportsResponse.OPTIONAL),
        ("get_batches", get_batches, GET_BATCHES_SCHEMA, SupportsResponse.ONLY),
        ("get_week_review", get_week_review, REVIEW_SCHEMA, SupportsResponse.ONLY),
        ("set_fullness", set_fullness, SET_FULLNESS_SCHEMA, SupportsResponse.OPTIONAL),
        ("get_fullness", get_fullness, GET_FULLNESS_SCHEMA, SupportsResponse.ONLY),
        ("get_dishes", get_dishes, vol.Schema(BASE), SupportsResponse.ONLY),
        ("sync_plan", sync_plan, vol.Schema(BASE), SupportsResponse.OPTIONAL),
        ("estimate", estimate, ESTIMATE_SCHEMA, SupportsResponse.ONLY),
        ("get_dish_nutrition", get_dish_nutrition, DISH_SCHEMA, SupportsResponse.ONLY),
        ("set_dish_nutrition", set_dish_nutrition, DISH_SET_SCHEMA, SupportsResponse.OPTIONAL),
        ("check_numbers", check_numbers, CHECK_SCHEMA, SupportsResponse.ONLY),
        ("dismiss_check", dismiss_check, DISMISS_SCHEMA, SupportsResponse.OPTIONAL),
        ("snapshot", snapshot, SNAPSHOT_SCHEMA, SupportsResponse.OPTIONAL),
        ("restore_snapshot", restore_snapshot, RESTORE_SCHEMA, SupportsResponse.OPTIONAL),
    ]
    for name, handler, schema, response in handlers:
        hass.services.async_register(DOMAIN, name, handler, schema=schema, supports_response=response)
