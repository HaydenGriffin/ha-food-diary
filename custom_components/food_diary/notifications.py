"""What happens after something is logged outside the app: the `food_diary_logged` event and the phone notification."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .const import EVENT_LOGGED, UNDO_PREFIX

if TYPE_CHECKING:
    from . import FoodDiaryData


def fire_logged(hass: HomeAssistant, data: FoodDiaryData, day: str, e: dict[str, Any], context: Context | None = None) -> None:
    hass.bus.async_fire(
        EVENT_LOGGED,
        {
            "person": data.person,
            "date": day,
            "entry_id": e["id"],
            "name": e["name"],
            "meal": e["meal"],
            "kcal": e["kcal"],
            "source": e["source"],
        },
        context=context,
    )


async def notify_logged(hass: HomeAssistant, data: FoodDiaryData, entry_key: str, day: str, e: dict[str, Any]) -> None:
    """The phone note after a shortcut or voice log: what, how much, what's left; with Undo (and Change when a page is set)."""
    if not data.notify:
        return
    left = data.diary.day(day)["left"].get("kcal")
    amount = f"{round(e['grams'])} {e.get('unit', 'g')} · " if e.get("grams") else ""
    message = f"{amount}{round(e['kcal'])} kcal"
    if left is not None:
        message += f" · {round(left)} left today" if left >= 0 else f" · {round(-left)} over today"
    extra: dict[str, Any] = {
        "tag": f"food-{e['id']}",
        "group": "food-diary",
        "actions": [{"action": f"{UNDO_PREFIX}{entry_key}|{day}|{e['id']}", "title": "Undo", "destructive": True}],
    }
    if data.open_path:
        url = f"{data.open_path}#food-{e['id']}"
        extra.update(url=url, clickAction=url)
        extra["actions"].append({"action": "URI", "title": "Change", "uri": url})
    await _notify(hass, data.notify, f"Logged: {e['name']}", message, extra)


async def notify_removed(hass: HomeAssistant, data: FoodDiaryData, e: dict[str, Any]) -> None:
    if data.notify:
        await _notify(
            hass,
            data.notify,
            "Removed",
            f"{e['name']} is out of today's diary.",
            {"tag": f"food-{e['id']}", "group": "food-diary"},
        )


async def _notify(hass: HomeAssistant, service: str, title: str, message: str, data: dict[str, Any]) -> None:
    domain, _, name = service.partition(".")
    try:
        await hass.services.async_call(
            domain or "notify", name or service, {"title": title, "message": message, "data": data}, blocking=False
        )
    except HomeAssistantError:  # a notification that can't be sent never undoes the logging
        return
