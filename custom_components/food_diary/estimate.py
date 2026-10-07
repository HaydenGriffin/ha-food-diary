"""Working out what something is and its nutrition, before anything is logged.

- photo: a picture of a meal; the AI estimates one portion as served.
- label: a picture of a nutrition label plus what was eaten ("3 biscuits", "50 g", "half the pack"); the AI reads the per-100
  values and sizes, and the grams are worked out here. A barcode it can see teaches the product book for next time.
- barcode: Open Food Facts (or the product book) gives the per-100 values; no AI unless the amount needs reading.
- text: "2 eggs on toast".
- dish: a recipe's ingredients and servings; the answer is kept per dish so it is only worked out once.
- a whole recipe: everything its ingredients add up to, for checking the numbers a recipe came with (checks.py).

Every answer is per what was eaten, with per_100 and grams when they are known, and nothing is saved to the diary here.
Prompts mention the home's country (Settings → System → General) when it is set, so portion sizes match local habits.
"""

from __future__ import annotations

import base64
import binascii
from pathlib import Path
import re
import time
from typing import Any
import uuid

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import MAX_IMAGE_BYTES, NUM, OFF_FIELDS, OFF_URL, OFF_USER_AGENT, PHOTO_DAYS, PHOTO_DIR
from .diary import Book, num, nums

LABELS = {
    "kcal": "Calories (kcal)",
    "protein_g": "Protein in grams",
    "carbs_g": "Carbohydrates in grams",
    "fat_g": "Fat in grams",
    "fibre_g": "Fibre in grams",
}
NUMBER = {"number": {"min": 0, "max": 10000, "step": 0.1, "mode": "box"}}

MEAL_STRUCTURE = {
    "name": {
        "description": "A short name for what this is, in English, sentence case",
        "required": True,
        "selector": {"text": {}},
    },
    "foods": {"description": "The foods you can see or that it contains", "selector": {"text": {"multiple": True}}},
    **{k: {"description": f"{v} for ONE portion", "required": True, "selector": NUMBER} for k, v in LABELS.items()},
    "note": {"description": 'One short line on what the portion is (like "1 bowl, about 350 g")', "selector": {"text": {}}},
}
LABEL_STRUCTURE = {
    "name": {
        "description": "The product name in short English, sentence case (brand only if it helps)",
        "required": True,
        "selector": {"text": {}},
    },
    **{
        f"{k}_100": {
            "description": f"{v} per 100 g (or per 100 ml for a drink), as printed; 0 if not on the label",
            "required": True,
            "selector": NUMBER,
        }
        for k, v in LABELS.items()
    },
    "unit": {"description": '"g" or "ml": what the per-100 column is per', "required": True, "selector": {"text": {}}},
    "serving_g": {
        "description": "One serving or one piece in g or ml, as printed (0 if not given)",
        "required": True,
        "selector": NUMBER,
    },
    "pack_g": {"description": "The whole pack in g or ml, as printed (0 if not given)", "required": True, "selector": NUMBER},
    "eaten_g": {
        "description": "How many g or ml were eaten, worked out from what they say (0 if they said nothing)",
        "required": True,
        "selector": NUMBER,
    },
    "barcode": {
        "description": "The digits printed under the barcode, if the photo shows one; empty if not",
        "selector": {"text": {}},
    },
}
AMOUNT_STRUCTURE = {
    "eaten_g": {"description": "How many g or ml were eaten (0 if it can't be worked out)", "required": True, "selector": NUMBER},
}

PHOTO_PROMPT = (
    "This is a photo of food {who} is about to eat (one adult{where}). Say what it is and estimate the nutrition for ONE "
    "portion as it is on the plate or in the bowl: calories, protein, carbohydrates, fat and fibre in grams. Be realistic for "
    "typical portions{where}; when something is unclear, assume the usual home-cooked version.{hint}"
)
LABEL_PROMPT = (
    "This is a photo of the nutrition label on a food or drink pack. Read the values PER 100 g (or per 100 ml): calories (kcal, "
    "not kJ), protein, carbohydrate, fat and fibre. If the label only gives values per serving, convert them to per 100 using "
    "the serving size. Also read the serving or piece size and the pack size if printed, the product name if you can see it, "
    "and the barcode digits if a barcode is in the photo.\n"
    "Then work out eaten_g, how much {who} ate in g or ml, from what they say: a weight is that weight; 'half the pack' is "
    "half the pack size; '2 biscuits' or '1 serving' uses the printed serving or piece size. "
    "If they said nothing, eaten_g is 0.\n"
    "{who} says they ate: {amount}"
)
AMOUNT_PROMPT = (
    '{who} ate some of {name}. One serving or piece is {serving} and the whole pack is {pack}. They say they ate: "{amount}". '
    "How many g (or ml) is that?"
)
TEXT_PROMPT = (
    '{who} (one adult{where}) ate this: "{text}". Name it in short English and estimate the nutrition for exactly what '
    "they wrote (if no amount is given, one normal portion): calories, protein, carbohydrates, fat and fibre in grams. Food "
    "names in other languages are fine; answer in English."
)
DISH_PROMPT = (
    "Estimate the nutrition for ONE portion of this home-cooked dish. The INGREDIENTS are {per}. Give calories, protein, "
    "carbohydrates, fat and fibre in grams for one portion.\n\nDISH: {name}\nINGREDIENTS: {ingredients}"
)
DISH_NAME_PROMPT = "Estimate the nutrition for ONE normal home-cooked portion{where} of: {name}."
WHOLE_PROMPT = (
    "Add up the nutrition of ALL these ingredients together, in the amounts listed: the whole lot, NOT one portion. Count "
    "what ends up eaten (leave out water, and oil for deep frying); a vague amount (a pinch, a handful, ½ an onion) is the "
    "usual amount{where}. Give calories, protein, carbohydrates, fat and fibre in grams for everything together.\n\n"
    "DISH: {name}\nINGREDIENTS: {ingredients}"
)
WHOLE_STRUCTURE = {
    k: {"description": f"{v} for ALL the ingredients together", "required": True, "selector": NUMBER} for k, v in LABELS.items()
}

# a count of these is that many servings ("3 biscuits"); anything else ("a big bowl") is read by the AI
PIECES = (
    r"(?:servings?|portions?|pieces?|biscuits?|cookies?|crackers?|slices?|bars?|squares?|cubes?|pots?|sachets?|scoops?|"
    r"sticks?|fingers?|chunks?|rolls?|wraps?|eggs?|units?)"
)
WORD_NUMBERS = {
    "a": 1,
    "an": 1,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "half": 0.5,
    "½": 0.5,
    "quarter": 0.25,
    "¼": 0.25,
    "third": 1 / 3,
}


class NeedLabel(HomeAssistantError):
    """The barcode isn't known anywhere: a photo of the label is needed."""


# ---------- pure helpers ----------


def decode_image(b64: str) -> tuple[bytes, str]:
    """The picture's bytes and MIME type; an error when it's missing, too big or not a picture."""
    raw_text = re.sub(r"\s+", "", str(b64 or ""))
    if raw_text.startswith("data:"):
        raw_text = raw_text.split(",", 1)[-1]
    try:
        raw = base64.b64decode(raw_text, validate=True)
    except (binascii.Error, ValueError) as err:
        raise ServiceValidationError("That picture didn't come through. Try again.") from err
    if len(raw) < 500:
        raise ServiceValidationError("That picture didn't come through. Try again.")
    if len(raw) > MAX_IMAGE_BYTES:
        raise ServiceValidationError("That picture is too big. Try a smaller one.")
    if raw[:3] == b"\xff\xd8\xff":
        return raw, "image/jpeg"
    if raw[:8] == b"\x89PNG\r\n\x1a\n":
        return raw, "image/png"
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return raw, "image/webp"
    raise ServiceValidationError("That isn't a photo I can read (use JPEG or PNG).")


def amount_grams(amount: str, serving: float, pack: float) -> float | None:
    """What was eaten in g or ml from the usual ways of saying it, or None when it needs reading properly."""
    a = str(amount or "").lower().strip()
    if not a:
        return None
    if m := re.fullmatch(r"(\d+(?:[.,]\d+)?)\s*(g|gr|grams?|ml|millilit(?:er|re)s?)?", a):
        return float(m.group(1).replace(",", "."))
    if m := re.search(r"(\d+(?:[.,]\d+)?)\s*(g|gr|grams?|ml|millilit(?:er|re)s?)\b", a):
        return float(m.group(1).replace(",", "."))
    count = None
    if m := re.match(r"(\d+(?:[.,]\d+)?|\d+/\d+|a|an|one|two|three|four|five|six|half|½|quarter|¼|third)\b", a):
        t = m.group(1)
        count = float(t.split("/")[0]) / float(t.split("/")[1]) if "/" in t else WORD_NUMBERS.get(t) or float(t.replace(",", "."))
    whole = re.search(r"\b(packs?|packets?|bags?|tubs?|bottles?|cans?|tins?|box(?:es)?|jars?|cartons?)\b", a)
    if pack and (whole and ("whole" in a or "all" in a or "the " in a or count is not None)):
        return pack * (count if count is not None else 1)
    if serving and count is not None and not whole and re.match(rf"\S+\s+(?:x\s+)?(?:\w+\s+)?{PIECES}\b", a):
        return serving * count
    return None


def label_result(x: dict[str, Any]) -> dict[str, Any]:
    """The label AI's reading (or a product) times what was eaten."""
    per_100 = {k: round(num(x.get(f"{k}_100")), 2) for k in NUM}
    if not per_100["kcal"]:
        raise HomeAssistantError("I couldn't read the calories on that label. Try a closer, straighter photo.")
    unit = "ml" if str(x.get("unit", "")).lower().startswith("ml") else "g"
    eaten, serving, pack = num(x.get("eaten_g")), num(x.get("serving_g")), num(x.get("pack_g"))
    grams = eaten or serving or 100.0
    guessed = not eaten
    note = (
        f"{round(grams)} {unit}"
        + (" (one serving: check it)" if guessed and serving else " (check it)" if guessed else "")
        + f" · {round(per_100['kcal'])} kcal per 100 {unit}"
    )
    return {
        "name": str(x.get("name") or "Food from a label").strip()[:80],
        "per_100": per_100,
        "grams": round(grams, 1),
        "unit": unit,
        "serving_g": serving,
        "pack_g": pack,
        "guessed": guessed,
        "note": note,
        **{k: round(per_100[k] * grams / 100, 1) for k in NUM},
    }


def off_product(p: dict[str, Any]) -> dict[str, Any] | None:
    """An Open Food Facts product as {name, kcal_100, …, unit, serving_g, pack_g}, or None without calories."""
    n = p.get("nutriments") or {}
    kcal = num(n.get("energy-kcal_100g")) or num(n.get("energy_100g")) / 4.184
    if not kcal:
        return None
    unit = (
        "ml"
        if str(p.get("product_quantity_unit") or "").lower() == "ml"
        or re.search(r"\d\s*ml\b", str(p.get("quantity") or "").lower())
        else "g"
    )
    name = (p.get("product_name_en") or p.get("product_name") or p.get("generic_name") or "").strip()
    brand = str(p.get("brands") or "").split(",")[0].strip()
    if brand and brand.lower() not in name.lower():
        name = f"{brand} {name}".strip()
    return {
        "name": name or "Unnamed product",
        "kcal_100": round(kcal, 1),
        "protein_g_100": num(n.get("proteins_100g")),
        "carbs_g_100": num(n.get("carbohydrates_100g")),
        "fat_g_100": num(n.get("fat_100g")),
        "fibre_g_100": num(n.get("fiber_100g")),
        "unit": unit,
        "serving_g": num(p.get("serving_quantity")),
        "pack_g": num(p.get("product_quantity")),
        "source": "openfoodfacts",
        **({"image": img} if (img := p.get("image_front_url") or p.get("image_front_small_url")) else {}),
    }


# ---------- Home Assistant side ----------


class Estimator:
    """Asks the AI Task entity, Open Food Facts and the shared books."""

    def __init__(self, hass: HomeAssistant, ai_entity: str | None, who: str, dishes: Book, products: Book) -> None:
        self.hass, self.ai_entity, self.who, self.dishes, self.products = hass, ai_entity, who, dishes, products

    @property
    def where(self) -> str:
        """The home's country for the prompts (like ' in GB'), or nothing when it isn't set."""
        return f" in {self.hass.config.country}" if self.hass.config.country else ""

    async def _ai(
        self, task: str, instructions: str, structure: dict[str, Any], attachments: list[tuple[str, str]] | None = None
    ) -> dict[str, Any]:
        data: dict[str, Any] = {"task_name": task, "instructions": instructions, "structure": structure}
        if self.ai_entity:
            data["entity_id"] = self.ai_entity
        if attachments:
            data["attachments"] = [{"media_content_id": m, "media_content_type": t} for m, t in attachments]
        try:
            res = await self.hass.services.async_call("ai_task", "generate_data", data, blocking=True, return_response=True)
        except HomeAssistantError as err:
            raise HomeAssistantError(f"The AI didn't answer: {err}") from err
        return (res or {}).get("data") or {}

    def _photo_path(self) -> Path:
        base = self.hass.config.media_dirs.get("local") or self.hass.config.path("media")
        return Path(base) / PHOTO_DIR

    async def save_photo(self, b64: str) -> tuple[str, str]:
        """Keep the picture for PHOTO_DAYS under local media (so ai_task can attach it); returns its media-source id and
        MIME type."""
        raw, mime = decode_image(b64)
        folder = self._photo_path()
        name = f"{int(time.time())}-{uuid.uuid4().hex[:6]}.{mime.split('/')[1].replace('jpeg', 'jpg')}"

        def write() -> None:
            folder.mkdir(parents=True, exist_ok=True)
            cut = time.time() - PHOTO_DAYS * 86400
            for f in folder.iterdir():
                if f.is_file() and f.stat().st_mtime < cut:
                    f.unlink(missing_ok=True)
            (folder / name).write_bytes(raw)

        await self.hass.async_add_executor_job(write)
        return f"media-source://media_source/local/{PHOTO_DIR}/{name}", mime

    async def photo(self, image: str, hint: str = "") -> dict[str, Any]:
        media = await self.save_photo(image)
        x = await self._ai(
            "estimate a meal from a photo",
            PHOTO_PROMPT.format(who=self.who, where=self.where, hint=f" They say: {hint}" if hint else ""),
            MEAL_STRUCTURE,
            [media],
        )
        return {**self._meal(x, "photo"), "photo": media[0].rsplit("/", 1)[-1]}

    async def text(self, text: str) -> dict[str, Any]:
        if not str(text or "").strip():
            raise ServiceValidationError("Say what you ate, like “2 eggs on toast”.")
        x = await self._ai(
            "estimate a meal from words",
            TEXT_PROMPT.format(who=self.who, where=self.where, text=str(text).strip()[:300]),
            MEAL_STRUCTURE,
        )
        return self._meal(x, "text")

    async def label(self, image: str, amount: str = "", barcode: str = "") -> dict[str, Any]:
        media = await self.save_photo(image)
        x = await self._ai(
            "read a nutrition label",
            LABEL_PROMPT.format(who=self.who, amount=amount or "(nothing said)"),
            LABEL_STRUCTURE,
            [media],
        )
        code = re.sub(r"\D", "", str(barcode or x.get("barcode") or ""))
        if num(x.get("kcal_100")) and len(code) >= 8:  # the next scan of this barcode needs no photo
            self.products.set(
                code,
                {k: x.get(k) for k in ("name", *[f"{n}_100" for n in NUM], "unit", "serving_g", "pack_g")} | {"source": "label"},
            )
        if not num(x.get("kcal_100")) and len(code) >= 8 and (product := await self._product(code)):
            x = {**product, "eaten_g": x.get("eaten_g")}
        out = label_result(x)
        return {**out, "source": "label", **({"barcode": code} if code else {})}

    async def barcode(self, code: str, amount: str = "") -> dict[str, Any]:
        code = re.sub(r"\D", "", str(code or ""))
        if len(code) < 8:
            raise ServiceValidationError("That barcode didn't scan properly. Try again.")
        product = await self._product(code)
        if not product:
            raise NeedLabel("That product isn't in Open Food Facts yet. Take a photo of its label instead.")
        serving, pack = num(product.get("serving_g")), num(product.get("pack_g"))
        grams = amount_grams(amount, serving, pack)
        if grams is None and str(amount or "").strip():
            x = await self._ai(
                "work out an amount",
                AMOUNT_PROMPT.format(
                    who=self.who,
                    name=product["name"],
                    amount=str(amount).strip()[:80],
                    serving=f"{serving:g} {product['unit']}" if serving else "not printed",
                    pack=f"{pack:g} {product['unit']}" if pack else "not printed",
                ),
                AMOUNT_STRUCTURE,
            )
            grams = num(x.get("eaten_g")) or None
        out = label_result({**product, "eaten_g": grams or 0})
        return {
            **out,
            "source": "barcode",
            "barcode": code,
            "product_source": product.get("source"),
            **({"image_url": product["image"]} if product.get("image") else {}),
        }

    async def _product(self, code: str) -> dict[str, Any] | None:
        if known := self.products.get(code):
            return known
        session = async_get_clientsession(self.hass)
        try:
            async with session.get(
                OFF_URL.format(code=code),
                params={"fields": OFF_FIELDS},
                headers={"User-Agent": OFF_USER_AGENT},
                timeout=aiohttp.ClientTimeout(total=12),
            ) as resp:
                if resp.status == 404:
                    return None
                resp.raise_for_status()
                body = await resp.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError) as err:
            raise HomeAssistantError("Open Food Facts didn't answer. Try again, or photograph the label.") from err
        product = off_product(body.get("product") or {}) if body.get("status") == 1 else None
        if product:
            self.products.set(code, product)
        return product

    async def dish(
        self,
        dish_id: str,
        name: str,
        ingredients: list[str] | None,
        servings: float = 0,
        amounts_per: str = "recipe",
        fresh: bool = False,
    ) -> dict[str, Any]:
        if not fresh and (known := self.dishes.get(dish_id)) and num(known.get("kcal")):
            return {**nums(known), "name": name, "source": "dish", "ref": dish_id, "nutrition_source": known.get("source", "ai")}
        out = await self.dish_ai(name, ingredients, servings, amounts_per)
        self.dishes.set(dish_id, {**nums(out), "source": "ai"})
        return {**out, "name": name or out["name"], "ref": dish_id, "nutrition_source": "ai"}

    async def dish_ai(
        self, name: str, ingredients: list[str] | None, servings: float = 0, amounts_per: str = "recipe"
    ) -> dict[str, Any]:
        """The AI's numbers for one portion of a dish from its ingredients (else its name); nothing is kept."""
        lines = [str(i).strip() for i in ingredients or [] if str(i).strip()]
        if lines:
            per = (
                "per portion"
                if amounts_per == "portion"
                else (
                    f"for the whole recipe, which makes {servings:g} portions"
                    if servings
                    else "for the whole recipe (assume it makes 2 portions)"
                )
            )
            x = await self._ai(
                "estimate a dish", DISH_PROMPT.format(per=per, name=name, ingredients="; ".join(lines)[:4000]), MEAL_STRUCTURE
            )
        else:
            x = await self._ai("estimate a dish by name", DISH_NAME_PROMPT.format(where=self.where, name=name), MEAL_STRUCTURE)
        return self._meal(x, "dish")

    async def dish_whole(self, name: str, ingredients: list[str]) -> dict[str, float]:
        """The AI's numbers for everything a recipe's ingredients add up to (all of them, as listed); nothing is kept."""
        lines = [str(i).strip() for i in ingredients if str(i).strip()]
        x = await self._ai(
            "estimate a whole recipe",
            WHOLE_PROMPT.format(where=self.where, name=name, ingredients="; ".join(lines)[:4000]),
            WHOLE_STRUCTURE,
        )
        if not num(x.get("kcal")):
            raise HomeAssistantError("I couldn't work that one out.")
        return {k: round(num(x.get(k)), 1) for k in NUM}

    @staticmethod
    def _meal(x: dict[str, Any], source: str) -> dict[str, Any]:
        if not num(x.get("kcal")):
            raise HomeAssistantError("I couldn't work that one out. Try again, or type what it is.")
        return {
            "name": str(x.get("name") or "Something").strip()[:80],
            **{k: round(num(x.get(k)), 1) for k in NUM},
            "note": str(x.get("note") or "")[:120],
            "foods": [str(f) for f in x.get("foods") or []][:12],
            "source": source,
        }
