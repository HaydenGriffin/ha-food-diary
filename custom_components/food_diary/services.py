"""Food diary services. Each one works on the caller's own diary (the person linked to their Home Assistant user); add
`person` to choose another, and with a single diary set up, calls from automations and scripts use that one.

Creating services (log_food, copy_day) take an optional `client_id`: the same id again returns the first result with
`duplicate: true` (and `deleted: true` once its entry is gone) instead of creating again. Changing services take an optional
`expected_rev`: the entry's `rev` as the caller last saw it; when it has changed since, the call fails with `conflict`."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import date, timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
import voluptuous as vol

from .const import DISH_SOURCES, DOMAIN, MEALS, NUM, SOURCES
from .diary import Book, Diary, num, nums, today
from .library import read_dishes
from .notifications import fire_logged
from .photos import with_images

if TYPE_CHECKING:
    from . import FoodDiaryData

ATTR_PERSON = "person"
NUMBERS = {vol.Optional(k): vol.All(vol.Coerce(float), vol.Range(min=0, max=20000)) for k in NUM}
BASE = {vol.Optional(ATTR_PERSON): cv.entity_id}
DATE = vol.Optional("date")
PORTIONS = vol.All(vol.Coerce(float), vol.Range(min=0.05, max=50))
RECIPE_PORTIONS = vol.All(vol.Coerce(int), vol.Range(min=1, max=24))
CLIENT_ID = vol.All(cv.string, vol.Match(r"^[A-Za-z0-9_-]{8,64}$"))  # made by the app, once per action (a UUID is fine)
EXPECTED_REV = vol.All(vol.Coerce(int), vol.Range(min=1))

LOG_SCHEMA = vol.Schema(
    {
        **BASE,
        vol.Required("name"): cv.string,
        **NUMBERS,
        vol.Optional("portions", default=1): PORTIONS,
        vol.Optional("meal"): vol.In(MEALS),
        DATE: cv.date,
        vol.Optional("source", default="manual"): vol.In(SOURCES),
        vol.Optional("ref"): cv.string,
        vol.Optional("note"): cv.string,
        vol.Optional("barcode"): cv.string,
        vol.Optional("per_100"): vol.Schema({vol.Optional(k): vol.Coerce(float) for k in NUM}, extra=vol.REMOVE_EXTRA),
        vol.Optional("grams"): vol.All(vol.Coerce(float), vol.Range(min=0, max=20000)),
        vol.Optional("unit"): vol.In(("g", "ml")),
        vol.Optional("edited"): cv.boolean,
        vol.Optional("photo"): cv.string,
        vol.Optional("image_url"): cv.string,
        vol.Optional("client_id"): CLIENT_ID,
    }
)
UPDATE_SCHEMA = vol.Schema(
    {
        **BASE,
        vol.Required("entry_id"): cv.string,
        DATE: cv.date,
        **NUMBERS,
        vol.Optional("portions"): PORTIONS,
        vol.Optional("grams"): vol.All(vol.Coerce(float), vol.Range(min=0.1, max=20000)),
        vol.Optional("meal"): vol.In(MEALS),
        vol.Optional("name"): cv.string,
        vol.Optional("checked"): cv.boolean,
        vol.Optional("expected_rev"): EXPECTED_REV,
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
ESTIMATE_SCHEMA = vol.Schema(
    {
        **BASE,
        vol.Required("kind"): vol.In(("photo", "label", "barcode", "text", "dish")),
        vol.Optional("image"): cv.string,
        vol.Optional("amount", default=""): cv.string,
        vol.Optional("hint", default=""): cv.string,
        vol.Optional("text", default=""): cv.string,
        vol.Optional("barcode", default=""): cv.string,
        vol.Optional("dish_id"): cv.string,
        vol.Optional("dish_name", default=""): cv.string,
        vol.Optional("ingredients", default=[]): vol.All(cv.ensure_list, [cv.string]),
        vol.Optional("servings", default=0): vol.Coerce(float),
        vol.Optional("amounts_per", default="recipe"): vol.In(("recipe", "portion")),
        vol.Optional("fresh", default=False): cv.boolean,
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
SAVE_MEAL_SCHEMA = vol.Schema({**BASE, vol.Required("name"): cv.string, DATE: cv.date, vol.Required("meal"): vol.In(MEALS)})
SAVED_SCHEMA = vol.Schema({**BASE, vol.Required("saved_id"): cv.string})
UPDATE_SAVED_SCHEMA = vol.Schema(
    {**BASE, vol.Required("saved_id"): cv.string, vol.Optional("name"): cv.string, vol.Optional("meal"): vol.In(MEALS)}
)
RECENT_SCHEMA = vol.Schema({**BASE, vol.Optional("limit", default=30): vol.All(vol.Coerce(int), vol.Range(min=1, max=200))})
REVIEW_SCHEMA = vol.Schema(
    {**BASE, DATE: cv.date, vol.Optional("days", default=7): vol.All(vol.Coerce(int), vol.Range(min=1, max=31))}
)
DISH_SCHEMA = vol.Schema({vol.Required("dish_id"): cv.string})
DISH_SET_SCHEMA = vol.Schema(
    {
        vol.Required("dish_id"): cv.string,
        vol.Required("kcal"): vol.Coerce(float),
        **{vol.Optional(k): vol.Coerce(float) for k in NUM if k != "kcal"},
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
INTERNAL = ("estimate", "checked_for", "dismissed")  # the numbers check's own notes on a book entry

NOT_IN_DIARY = "That entry isn't in the diary on that day."


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


def logged(diary: Diary, record: dict[str, Any], repeat: bool) -> dict[str, Any]:
    """log_food's answer, for a new entry or a repeated client_id: the entry as it is now (None once it's deleted)."""
    d = record["date"]
    e = diary.find(d, record["entry_id"])
    return {
        "entry": e,
        "entry_id": record["entry_id"],
        "date": d,
        "totals": diary.day(d)["totals"],
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
        if not num(f.get("kcal")) and not (f.get("per_100") and num(f.get("grams"))):
            raise ServiceValidationError("Give kcal, or per_100 with grams.")
        day = f.pop("date").isoformat() if f.get("date") else today()

        async def create() -> dict[str, Any]:
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
        e = data.diary.update(day_of(call), call.data["entry_id"], changes, call.data.get("expected_rev"))
        if e is None:
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
        if (saved := resolve(hass, call).diary.save_meal(call.data["name"], day_of(call), call.data["meal"])) is None:
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

    async def get_week_review(call: ServiceCall) -> ServiceResponse:
        d = call.data.get("date")
        end = d.isoformat() if d else (date.fromisoformat(today()) - timedelta(days=1)).isoformat()
        return resolve(hass, call).diary.review(end, call.data["days"])

    async def get_dishes(call: ServiceCall) -> ServiceResponse:
        """The dish library with one portion's numbers where the recipe book knows them (empty without a library)."""
        data = resolve(hass, call)
        book = dishes_book(hass)
        out = []
        for x in read_dishes(hass, data.dishes_sensor):
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
                    **({**nums(n), "kcal_source": n.get("source")} if num(n.get("kcal")) else {}),
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
            return await est.photo(c["image"], c["hint"])
        if kind == "label":
            return await est.label(c["image"], c["amount"], c["barcode"])
        if kind == "barcode":
            return await est.barcode(c["barcode"], c["amount"])
        if kind == "text":
            return await est.text(c["text"])
        if not c.get("dish_id"):
            raise ServiceValidationError("Add dish_id.")
        return await est.dish(c["dish_id"], c["dish_name"], c["ingredients"], c["servings"], c["amounts_per"], c["fresh"])

    async def get_dish_nutrition(call: ServiceCall) -> ServiceResponse:
        return public(dishes_book(hass).get(call.data["dish_id"]) or {})

    async def set_dish_nutrition(call: ServiceCall) -> ServiceResponse:
        """A recipe's numbers for one portion (and how many portions it makes): any doubt about them goes, and diary
        entries for it from today on whose numbers weren't edited follow the new numbers."""
        dish_id = call.data["dish_id"]
        values = {**nums(call.data), "source": call.data["source"]}
        if call.data.get("portions"):
            values["portions"] = call.data["portions"]
        kept = dishes_book(hass).set(dish_id, values)
        refreshed = sum(e.runtime_data.diary.follow(dish_id, nums(kept), today()) for e in loaded(hass))
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
            yours, key = nums(dishes_book(hass).get(dish_id)), dish_id
        elif entry_id := call.data.get("entry_id"):
            e = next((x for x in data.diary.entries(day_of(call)) if x["id"] == entry_id), None)
            if e is None:
                raise ServiceValidationError(NOT_IN_DIARY)
            ref = str(e.get("ref") or "")
            dish = library.get(ref) if ref else None
            yours, key, name = nums(e.get("per_portion") or e), (ref if dish else None), e["name"]
        else:
            raise ServiceValidationError("Give dish_id, or entry_id and date.")
        return await data.checker.compare(yours, key, dish, name, call.data.get("portions"))

    async def dismiss_check(call: ServiceCall) -> ServiceResponse:
        if resolve(hass, call).checker.dismiss(call.data["dish_id"]) is None:
            raise ServiceValidationError("That recipe isn't in the recipe book.")
        return {"ok": True}

    handlers: list[tuple[str, Callable[[ServiceCall], Awaitable[ServiceResponse]], vol.Schema, SupportsResponse]] = [
        ("log_food", log_food, LOG_SCHEMA, SupportsResponse.OPTIONAL),
        ("update_food", update_food, UPDATE_SCHEMA, SupportsResponse.OPTIONAL),
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
        ("get_week_review", get_week_review, REVIEW_SCHEMA, SupportsResponse.ONLY),
        ("get_dishes", get_dishes, vol.Schema(BASE), SupportsResponse.ONLY),
        ("sync_plan", sync_plan, vol.Schema(BASE), SupportsResponse.OPTIONAL),
        ("estimate", estimate, ESTIMATE_SCHEMA, SupportsResponse.ONLY),
        ("get_dish_nutrition", get_dish_nutrition, DISH_SCHEMA, SupportsResponse.ONLY),
        ("set_dish_nutrition", set_dish_nutrition, DISH_SET_SCHEMA, SupportsResponse.OPTIONAL),
        ("check_numbers", check_numbers, CHECK_SCHEMA, SupportsResponse.ONLY),
        ("dismiss_check", dismiss_check, DISMISS_SCHEMA, SupportsResponse.OPTIONAL),
    ]
    for name, handler, schema, response in handlers:
        hass.services.async_register(DOMAIN, name, handler, schema=schema, supports_response=response)
