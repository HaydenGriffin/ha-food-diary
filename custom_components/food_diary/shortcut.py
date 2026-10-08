"""The webhook for phone shortcuts and other HTTP clients: POST JSON to /api/webhook/<the diary's webhook id>.

  {"kind": "barcode", "barcode": "5000168001142", "amount": "3 biscuits"}
  {"kind": "label", "image": "<base64 JPEG>", "amount": "half the pack", "barcode": "<optional, from a scan>"}
  {"kind": "photo", "image": "<base64 JPEG>", "hint": "<optional>"}
  {"kind": "text", "text": "2 eggs on toast"}
  optional on all: "meal" (breakfast|lunch|dinner|snack, else from the time of day), "quiet": "yes" (or "notify": false):
  no phone notification (when the caller shows the result itself), "client_id": an id the caller made for this one log
  (8–64 letters, digits, - or _): sending it again (a retry) answers as the first time, with "duplicate": true, and logs
  nothing new; the same id with a different body is refused

It works the food out, logs it at once and answers with the numbers (so a shortcut can, say, write them to a health app):
  {"ok": true, "status": "logged", "name", "kcal", "protein_g", "carbs_g", "fat_g", "fibre_g", "grams", "unit", "meal",
   "entry_id", "title", "message"}
  {"ok": true, "status": "deleted", "entry_id", "date", "duplicate": true, "deleted": true, "message"}
                                                     a repeated client_id whose entry has since been removed (not re-logged)
  {"ok": false, "status": "need_label", "message"}   barcode not known: photograph the label (send it back with the barcode)
  {"ok": false, "status": "error", "message"}        anything else that went wrong, said plainly ("error": "client_id_reused"
                                                     for a client_id already used with a different body)
The notify service from the options also gets a notification with Undo (and Change, when a page is set).
The webhook id is the secret: anyone who has it can log food to this diary.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from aiohttp import web
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .const import CONF_WEBHOOK_ID, DOMAIN, MEALS, NUM
from .diary import Diary, today
from .estimate import NeedLabel
from .notifications import fire_logged, notify_logged

_LOGGER = logging.getLogger(__name__)
CLIENT_ID = re.compile(r"[A-Za-z0-9_-]{8,64}")
REUSED = "That client_id was already used for different food."


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

    client_id = body.get("client_id")
    if client_id is not None and not (isinstance(client_id, str) and CLIENT_ID.fullmatch(client_id)):
        return _reply({"ok": False, "status": "error", "message": "client_id must be 8 to 64 letters, digits, - or _."}, 400)

    data = entry.runtime_data
    quiet = body.get("notify", True) in (False, "false", "no", 0) or str(body.get("quiet", "")).lower() in ("yes", "true", "1")

    async def create() -> dict[str, Any]:
        found = await _work_out(data.estimator, body)
        meal = str(body.get("meal") or "").lower()
        day = today()
        e = data.diary.add(
            day,
            {
                **found,
                "meal": meal if meal in MEALS else None,
                "source": found.get("source", _kind(body)),
                "client_id": client_id,
            },
        )
        fire_logged(hass, data, day, e)
        if not quiet:
            await notify_logged(hass, data, entry.entry_id, day, e)
        _LOGGER.debug("Logged %s for %s from the webhook", e["name"], data.person)
        guessed = ("serving" if found.get("serving_g") else "amount") if found.get("guessed") else None
        return {"entry_id": e["id"], "date": day, **({"guessed": guessed} if guessed else {})}

    try:
        record, repeat = await data.diary.create_once(client_id, body, create)
    except NeedLabel as err:
        return _reply({"ok": False, "status": "need_label", "message": str(err)})
    except HomeAssistantError as err:
        if err.translation_key == "client_id_reused":
            return _reply({"ok": False, "status": "error", "error": "client_id_reused", "message": REUSED}, 409)
        return _reply({"ok": False, "status": "error", "message": str(err)})
    return _reply(_logged(data.diary, record, repeat))


def _kind(body: dict[str, Any]) -> str:
    return str(body.get("kind") or ("photo" if body.get("image") else "text")).lower()


async def _work_out(est: Any, body: dict[str, Any]) -> dict[str, Any]:
    kind = _kind(body)
    amount = str(body.get("amount") or "").strip()
    if kind == "barcode":
        return await est.barcode(str(body.get("barcode") or ""), amount)
    if kind == "label":
        return await est.label(str(body.get("image") or ""), amount, str(body.get("barcode") or ""))
    if kind == "photo":
        return await est.photo(str(body.get("image") or ""), str(body.get("hint") or "").strip())
    return await est.text(str(body.get("text") or ""))


def _logged(diary: Diary, record: dict[str, Any], repeat: bool) -> dict[str, Any]:
    """The answer for a new log, or for a repeated client_id: the entry as it is now."""
    day, entry_id = record["date"], record["entry_id"]
    flags = {"duplicate": True} if repeat else {}
    e = diary.find(day, entry_id)
    if e is None:
        return {
            "ok": True,
            "status": "deleted",
            "entry_id": entry_id,
            "date": day,
            **flags,
            "deleted": True,
            "message": "That food was logged before and has since been removed.",
        }
    left = diary.day(day)["left"].get("kcal")
    message = (f"{round(e['grams'])} {e.get('unit', 'g')} · " if e.get("grams") else "") + f"{round(e['kcal'])} kcal"
    if record.get("guessed"):
        message += " (one serving, check it)" if record["guessed"] == "serving" else " (check the amount)"
    if left is not None:
        message += f" · {round(abs(left))} {'left' if left >= 0 else 'over'} today"
    return {
        "ok": True,
        "status": "logged",
        "name": e["name"],
        "meal": e["meal"],
        "entry_id": e["id"],
        "rev": e.get("rev", 1),
        "date": day,
        **{k: e[k] for k in NUM},
        "grams": e.get("grams") or 0,
        "unit": e.get("unit", "g"),
        "guessed": bool(record.get("guessed")),
        "title": f"Logged: {e['name']}",
        "message": message,
        **flags,
    }
