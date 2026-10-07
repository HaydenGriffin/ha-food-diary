"""The webhook for phone shortcuts and other HTTP clients: POST JSON to /api/webhook/<the diary's webhook id>.

  {"kind": "barcode", "barcode": "5000168001142", "amount": "3 biscuits"}
  {"kind": "label", "image": "<base64 JPEG>", "amount": "half the pack", "barcode": "<optional, from a scan>"}
  {"kind": "photo", "image": "<base64 JPEG>", "hint": "<optional>"}
  {"kind": "text", "text": "2 eggs on toast"}
  optional on all: "meal" (breakfast|lunch|dinner|snack, else from the time of day), "quiet": "yes" (or "notify": false):
  no phone notification (when the caller shows the result itself)

It works the food out, logs it at once and answers with the numbers (so a shortcut can, say, write them to a health app):
  {"ok": true, "status": "logged", "name", "kcal", "protein_g", "carbs_g", "fat_g", "fibre_g", "grams", "unit", "meal",
   "entry_id", "title", "message"}
  {"ok": false, "status": "need_label", "message"}   barcode not known: photograph the label (send it back with the barcode)
  {"ok": false, "status": "error", "message"}        anything else that went wrong, said plainly
The notify service from the options also gets a notification with Undo (and Change, when a page is set).
The webhook id is the secret: anyone who has it can log food to this diary.
"""

from __future__ import annotations

import logging
from typing import Any

from aiohttp import web
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .const import CONF_WEBHOOK_ID, DOMAIN, MEALS, NUM
from .diary import today
from .estimate import NeedLabel
from .notifications import fire_logged, notify_logged

_LOGGER = logging.getLogger(__name__)


def _reply(body: dict[str, Any], status: int = 200) -> web.Response:
    return web.json_response(body, status=status)


async def async_handle_webhook(hass: HomeAssistant, webhook_id: str, request: web.Request) -> web.Response:
    entry = next((e for e in hass.config_entries.async_entries(DOMAIN) if e.data.get(CONF_WEBHOOK_ID) == webhook_id), None)
    if entry is None or not hasattr(entry, "runtime_data"):
        return _reply({"ok": False, "status": "error", "message": "The food diary isn't running."}, 503)
    try:
        body = await request.json()
        if not isinstance(body, dict):
            raise ValueError
    except ValueError:
        return _reply({"ok": False, "status": "error", "message": "That didn't come through. Try again."}, 400)

    data = entry.runtime_data
    est = data.estimator
    kind = str(body.get("kind") or ("photo" if body.get("image") else "text")).lower()
    amount = str(body.get("amount") or "").strip()
    try:
        if kind == "barcode":
            found = await est.barcode(str(body.get("barcode") or ""), amount)
        elif kind == "label":
            found = await est.label(str(body.get("image") or ""), amount, str(body.get("barcode") or ""))
        elif kind == "photo":
            found = await est.photo(str(body.get("image") or ""), str(body.get("hint") or "").strip())
        else:
            found = await est.text(str(body.get("text") or ""))
    except NeedLabel as err:
        return _reply({"ok": False, "status": "need_label", "message": str(err)})
    except HomeAssistantError as err:
        return _reply({"ok": False, "status": "error", "message": str(err)})

    meal = str(body.get("meal") or "").lower()
    day = today()
    e = data.diary.add(day, {**found, "meal": meal if meal in MEALS else None, "source": found.get("source", kind)})
    fire_logged(hass, data, day, e)
    quiet = body.get("notify", True) in (False, "false", "no", 0) or str(body.get("quiet", "")).lower() in ("yes", "true", "1")
    if not quiet:
        await notify_logged(hass, data, entry.entry_id, day, e)
    left = data.diary.day(day)["left"].get("kcal")
    title = f"Logged: {e['name']}"
    message = (f"{round(e['grams'])} {e.get('unit', 'g')} · " if e.get("grams") else "") + f"{round(e['kcal'])} kcal"
    if found.get("guessed"):
        message += " (one serving, check it)" if found.get("serving_g") else " (check the amount)"
    if left is not None:
        message += f" · {round(abs(left))} {'left' if left >= 0 else 'over'} today"
    _LOGGER.debug("Logged %s for %s from the webhook", e["name"], data.person)
    return _reply(
        {
            "ok": True,
            "status": "logged",
            "name": e["name"],
            "meal": e["meal"],
            "entry_id": e["id"],
            "date": day,
            **{k: e[k] for k in NUM},
            "grams": e.get("grams") or 0,
            "unit": e.get("unit", "g"),
            "guessed": bool(found.get("guessed")),
            "title": title,
            "message": message,
        }
    )
