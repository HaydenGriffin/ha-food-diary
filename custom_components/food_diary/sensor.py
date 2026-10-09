"""Today's numbers per person: calories, protein, carbs, fat and fibre eaten (with long-term statistics, so history graphs
and averages work), calories left against the goal, and when something was last logged. Each carries `api` (see
const.API_VERSION), so apps can tell what the diary's services support."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorEntityDescription, SensorStateClass
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import FoodDiaryConfigEntry, FoodDiaryData
from .const import API_VERSION, DOMAIN, MEALS
from .diary import today


@dataclass(frozen=True, kw_only=True)
class DiarySensorDescription(SensorEntityDescription):
    value: Callable[[dict[str, Any]], Any]
    eaten: bool = True  # a total for today that starts again at midnight


SENSORS = (
    DiarySensorDescription(
        key="calories_today",
        translation_key="calories_today",
        native_unit_of_measurement="kcal",
        suggested_display_precision=0,
        icon="mdi:fire",
        value=lambda d: d["totals"]["kcal"],
    ),
    DiarySensorDescription(
        key="protein_today",
        translation_key="protein_today",
        native_unit_of_measurement="g",
        suggested_display_precision=0,
        icon="mdi:food-steak",
        value=lambda d: d["totals"]["protein_g"],
    ),
    DiarySensorDescription(
        key="carbs_today",
        translation_key="carbs_today",
        native_unit_of_measurement="g",
        suggested_display_precision=0,
        icon="mdi:bread-slice-outline",
        value=lambda d: d["totals"]["carbs_g"],
    ),
    DiarySensorDescription(
        key="fat_today",
        translation_key="fat_today",
        native_unit_of_measurement="g",
        suggested_display_precision=0,
        icon="mdi:water-outline",
        value=lambda d: d["totals"]["fat_g"],
    ),
    DiarySensorDescription(
        key="fibre_today",
        translation_key="fibre_today",
        native_unit_of_measurement="g",
        suggested_display_precision=0,
        icon="mdi:leaf",
        value=lambda d: d["totals"]["fibre_g"],
    ),
    DiarySensorDescription(
        key="calories_left",
        translation_key="calories_left",
        native_unit_of_measurement="kcal",
        suggested_display_precision=0,
        icon="mdi:target",
        eaten=False,
        value=lambda d: d["left"].get("kcal"),
    ),
)


def device_info(entry: FoodDiaryConfigEntry) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name=f"{entry.runtime_data.name} food diary",
        model="Food diary",
        entry_type=DeviceEntryType.SERVICE,
    )


async def async_setup_entry(hass: HomeAssistant, entry: FoodDiaryConfigEntry, add: AddConfigEntryEntitiesCallback) -> None:
    add([*(DiarySensor(entry, d) for d in SENSORS), LastLoggedSensor(entry)])


class _DiaryEntity(SensorEntity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, entry: FoodDiaryConfigEntry, key: str) -> None:
        self.data: FoodDiaryData = entry.runtime_data
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = device_info(entry)

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self.data.diary.async_add_listener(self._changed))

    @callback
    def _changed(self) -> None:
        self.async_write_ha_state()


class DiarySensor(_DiaryEntity):
    entity_description: DiarySensorDescription

    def __init__(self, entry: FoodDiaryConfigEntry, description: DiarySensorDescription) -> None:
        super().__init__(entry, description.key)
        self.entity_description = description
        if description.eaten:
            self._attr_state_class = SensorStateClass.TOTAL

    @property
    def _today(self) -> dict[str, Any]:
        return self.data.diary.day(today())

    @property
    def native_value(self) -> float | None:
        return self.entity_description.value(self._today)

    @property
    def last_reset(self) -> datetime | None:
        if not self.entity_description.eaten:
            return None
        return dt_util.start_of_local_day()

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        return {**self._attributes(), "api": API_VERSION}

    def _attributes(self) -> dict[str, Any]:
        d = self._today
        key = self.entity_description.key
        if key == "calories_today":
            return {
                "goal": d["goals"].get("kcal"),
                "left": d["left"].get("kcal"),
                "logged": len(d["entries"]),
                **{m: d["meals"][m] for m in MEALS},
            }
        if key == "calories_left":
            return {"goal": d["goals"].get("kcal")}
        nutrient = {"protein_today": "protein_g", "carbs_today": "carbs_g", "fat_today": "fat_g", "fibre_today": "fibre_g"}[key]
        return {"goal": d["goals"].get(nutrient), "left": d["left"].get(nutrient)}


class LastLoggedSensor(_DiaryEntity):
    """When something was last logged (for reminders like "nothing logged since breakfast")."""

    _attr_translation_key = "last_logged"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:history"

    def __init__(self, entry: FoodDiaryConfigEntry) -> None:
        super().__init__(entry, "last_logged")

    def _last(self) -> dict[str, Any] | None:
        days = self.data.diary.data["days"]
        for d in sorted(days, reverse=True)[:3]:
            if days[d]:
                return max(days[d], key=lambda e: e.get("at", ""))
        return None

    @property
    def native_value(self) -> datetime | None:
        e = self._last()
        at = dt_util.parse_datetime(e["at"]) if e and e.get("at") else None
        if at is not None and at.tzinfo is None:  # stored without an offset (older or imported data): local time
            at = at.replace(tzinfo=dt_util.get_default_time_zone())
        return at

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        e = self._last()
        last = {"name": e["name"], "kcal": e["kcal"], "meal": e["meal"], "entry_id": e["id"], "rev": e.get("rev", 1)} if e else {}
        return {**last, "api": API_VERSION}
