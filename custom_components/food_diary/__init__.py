"""Food Diary: a calorie and macro diary per person, kept in Home Assistant.

Food is logged from a meal photo, a nutrition label, a barcode (Open Food Facts), words or a recipe. Each person gets sensors
for today (with long-term statistics), goal numbers, services for apps, dashboards and automations, a webhook for phone
shortcuts, and voice intents. An optional meal plan counts planned meals ahead of time, and an optional dish library gives
recipes their numbers and flags recipe numbers that look wrong; both are read from sensors, or from another integration that
registers them (sources.py).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from homeassistant.components import webhook
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.typing import ConfigType

from .checks import Checker
from .const import (
    CONF_AI_TASK,
    CONF_NOTIFY,
    CONF_OPEN_PATH,
    CONF_PERSON,
    CONF_WEBHOOK_ID,
    DOMAIN,
    UNDO_PREFIX,
)
from .diary import Book, Diary
from .estimate import Estimator
from .notifications import notify_removed
from .photos import Photos, PhotoView
from .planner import Planner
from .services import async_register_services
from .shortcut import async_handle_webhook
from .sources import MealPlan, RecipeLibrary, async_resolve

PLATFORMS = [Platform.NUMBER, Platform.SENSOR]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


@dataclass
class FoodDiaryData:
    """What one config entry (one person's diary) runs on."""

    person: str
    name: str
    diary: Diary
    estimator: Estimator
    checker: Checker
    notify: str | None
    open_path: str | None
    recipes: RecipeLibrary | None
    plan: MealPlan | None
    planner: Planner | None = None


type FoodDiaryConfigEntry = ConfigEntry[FoodDiaryData]


def person_key(person: str) -> str:
    return person.split(".", 1)[-1]


def person_name(hass: HomeAssistant, person: str) -> str:
    state = hass.states.get(person)
    return state.name if state else person_key(person).replace("_", " ").title()


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """The books every diary shares, the photo view and the services (once, for every diary)."""
    dishes, products = Book(hass, "dishes"), Book(hass, "products")
    await dishes.async_load()
    await products.async_load()
    photos = Photos(hass)
    hass.data[DOMAIN] = {"dishes": dishes, "products": products, "photos": photos}
    hass.http.register_view(PhotoView(photos))
    async_register_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: FoodDiaryConfigEntry) -> bool:
    person = entry.data[CONF_PERSON]
    name = person_name(hass, person)
    diary = Diary(hass, person_key(person))
    await diary.async_load()
    books = hass.data[DOMAIN]
    options = entry.options
    source = async_resolve(hass, dict(options))
    estimator = Estimator(hass, options.get(CONF_AI_TASK) or None, name, books["dishes"], books["products"])
    data = FoodDiaryData(
        person=person,
        name=name,
        diary=diary,
        estimator=estimator,
        checker=Checker(hass, estimator, source.library, source.plan),
        notify=options.get(CONF_NOTIFY) or None,
        open_path=options.get(CONF_OPEN_PATH) or None,
        recipes=source.library,
        plan=source.plan,
    )
    entry.runtime_data = data

    webhook.async_register(
        hass,
        DOMAIN,
        f"Food diary ({name})",
        entry.data[CONF_WEBHOOK_ID],
        async_handle_webhook,
        local_only=False,
        allowed_methods=["POST"],
    )

    @callback
    def midnight(_now: datetime) -> None:
        diary.async_refresh()  # a new day: today's sensors start again

    entry.async_on_unload(async_track_time_change(hass, midnight, hour=0, minute=0, second=1))

    async def undo(event: Event) -> None:
        """Undo on the phone notification."""
        action = str(event.data.get("action") or "")
        if not action.startswith(UNDO_PREFIX):
            return
        _, key, day, entry_id = [*action.split("|"), "", "", ""][:4]
        if key == entry.entry_id and (removed := diary.delete(day, entry_id)):
            await notify_removed(hass, data, removed)

    entry.async_on_unload(hass.bus.async_listen("mobile_app_notification_action", undo))
    if source.plan:
        data.planner = Planner(hass, data, source.plan, source.library)
        for stop in data.planner.async_start():
            entry.async_on_unload(stop)
    if source.library:
        for stop in data.checker.async_start():
            entry.async_on_unload(stop)
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _async_reload(hass: HomeAssistant, entry: FoodDiaryConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: FoodDiaryConfigEntry) -> bool:
    webhook.async_unregister(hass, entry.data[CONF_WEBHOOK_ID])
    await entry.runtime_data.diary.async_flush()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
