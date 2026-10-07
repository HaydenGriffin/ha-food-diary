"""Daily goals per person, editable in Home Assistant (and from the phone)."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.number import NumberEntity, NumberEntityDescription, NumberMode
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import FoodDiaryConfigEntry
from .sensor import device_info


@dataclass(frozen=True, kw_only=True)
class GoalDescription(NumberEntityDescription):
    goal: str


GOALS = (
    GoalDescription(
        key="calorie_goal",
        translation_key="calorie_goal",
        goal="kcal",
        native_unit_of_measurement="kcal",
        native_min_value=800,
        native_max_value=5000,
        native_step=10,
        icon="mdi:target",
    ),
    GoalDescription(
        key="protein_goal",
        translation_key="protein_goal",
        goal="protein_g",
        native_unit_of_measurement="g",
        native_min_value=0,
        native_max_value=400,
        native_step=1,
        icon="mdi:food-steak",
    ),
    GoalDescription(
        key="carbs_goal",
        translation_key="carbs_goal",
        goal="carbs_g",
        native_unit_of_measurement="g",
        native_min_value=0,
        native_max_value=600,
        native_step=1,
        icon="mdi:bread-slice-outline",
    ),
    GoalDescription(
        key="fat_goal",
        translation_key="fat_goal",
        goal="fat_g",
        native_unit_of_measurement="g",
        native_min_value=0,
        native_max_value=300,
        native_step=1,
        icon="mdi:water-outline",
    ),
    GoalDescription(
        key="fibre_goal",
        translation_key="fibre_goal",
        goal="fibre_g",
        native_unit_of_measurement="g",
        native_min_value=0,
        native_max_value=100,
        native_step=1,
        icon="mdi:leaf",
    ),
)


async def async_setup_entry(hass: HomeAssistant, entry: FoodDiaryConfigEntry, add: AddConfigEntryEntitiesCallback) -> None:
    add(GoalNumber(entry, d) for d in GOALS)


class GoalNumber(NumberEntity):
    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.BOX
    entity_description: GoalDescription

    def __init__(self, entry: FoodDiaryConfigEntry, description: GoalDescription) -> None:
        self.entity_description = description
        self.diary = entry.runtime_data.diary
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_device_info = device_info(entry)

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self.diary.async_add_listener(self._changed))

    @callback
    def _changed(self) -> None:
        self.async_write_ha_state()

    @property
    def native_value(self) -> float | None:
        return self.diary.goals.get(self.entity_description.goal)

    async def async_set_native_value(self, value: float) -> None:
        self.diary.set_goals({self.entity_description.goal: value})
