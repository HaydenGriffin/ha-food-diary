"""Food photos. The person's own (the meal photo an entry was logged from, or one added later) are kept in the config
folder under food_diary/photos and served only to signed-in users at /api/food_diary/photo/<name>; product photos come from
Open Food Facts; recipe food shows the recipe's photo from the dish library. Every entry the diary hands out gets an `image`
from the first of those."""

from __future__ import annotations

from http import HTTPStatus
from pathlib import Path
import re
import shutil
from typing import TYPE_CHECKING, Any
import uuid

from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant

from .const import DOMAIN, PHOTO_DIR
from .estimate import decode_image
from .library import dish_names, plain_name, read_dishes

if TYPE_CHECKING:
    from . import FoodDiaryData

URL = "/api/food_diary/photo"
NAME = re.compile(r"^[0-9a-f]{16}\.(jpg|png|webp)$")
MEDIA_NAME = re.compile(r"^[0-9]+-[0-9a-f]{6}\.(jpg|png|webp|heic)$")


class Photos:
    """Kept photos on disk, by a random name (the name is all an entry keeps)."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self.folder = Path(hass.config.path(DOMAIN, "photos"))

    def path(self, name: str) -> Path | None:
        return self.folder / name if NAME.match(name or "") else None

    async def keep(self, b64: str) -> str:
        raw, mime = decode_image(b64)
        name = f"{uuid.uuid4().hex[:16]}.{mime.split('/')[1].replace('jpeg', 'jpg')}"

        def write() -> None:
            self.folder.mkdir(parents=True, exist_ok=True)
            (self.folder / name).write_bytes(raw)

        await self.hass.async_add_executor_job(write)
        return name

    async def keep_estimated(self, media_name: str) -> str | None:
        """The meal photo an estimate was made from (kept PHOTO_DAYS in local media), kept for good with its entry. A photo
        already kept (logging the same food again) is shared as it is."""
        if NAME.match(media_name or ""):
            return media_name if await self.hass.async_add_executor_job((self.folder / media_name).is_file) else None
        if not MEDIA_NAME.match(media_name or ""):
            return None
        base = self.hass.config.media_dirs.get("local") or self.hass.config.path("media")
        src = Path(base) / PHOTO_DIR / media_name
        name = f"{uuid.uuid4().hex[:16]}{src.suffix.replace('.heic', '.jpg')}"

        def copy() -> bool:
            if not src.is_file():
                return False
            self.folder.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, self.folder / name)
            return True

        return name if await self.hass.async_add_executor_job(copy) else None


class PhotoView(HomeAssistantView):
    """A kept photo, for signed-in users only."""

    url = URL + "/{name}"
    name = "api:food_diary:photo"
    requires_auth = True

    def __init__(self, photos: Photos) -> None:
        self.photos = photos

    async def get(self, request: web.Request, name: str) -> web.StreamResponse:
        path = self.photos.path(name)
        if path is None or not await request.app["hass"].async_add_executor_job(path.is_file):
            return web.Response(status=HTTPStatus.NOT_FOUND)
        return web.FileResponse(path, headers={"Cache-Control": "private, max-age=31536000, immutable"})


def with_images(hass: HomeAssistant, data: FoodDiaryData, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Each entry (or food to log again) with an `image`: its own photo, the product's, else its recipe's (by id, then by
    name)."""
    dishes = [x for x in read_dishes(hass, data.dishes_sensor) if x.get("image")]
    by_id = {x["id"]: x["image"] for x in dishes}
    by_name: dict[str, str] = {}
    for x in dishes:
        for n in sorted(dish_names(x)):
            by_name.setdefault(n, x["image"])
    products = hass.data[DOMAIN]["products"]
    out = []
    for e in entries:
        image = None
        if e.get("photo"):
            image = f"{URL}/{e['photo']}"
        elif e.get("image_url"):
            image = e["image_url"]
        elif e.get("barcode") and (p := products.get(str(e["barcode"]))) and p.get("image"):
            image = p["image"]
        elif e.get("ref") and e["ref"] in by_id:
            image = by_id[e["ref"]]
        else:
            image = by_name.get(plain_name(e.get("name")).lower())
        out.append({**e, "image": image} if image else e)
    return out
