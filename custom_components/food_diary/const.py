"""Constants for the Food Diary integration."""

from __future__ import annotations

DOMAIN = "food_diary"

# Every entry, portion and goal carries these five numbers.
NUM = ("kcal", "protein_g", "carbs_g", "fat_g", "fibre_g")
MEALS = ("breakfast", "lunch", "dinner", "snack")
# Where an entry's numbers came from. "plan" is only ever written by the meal planner.
SOURCES = ("photo", "label", "barcode", "text", "dish", "manual", "again", "saved", "voice")
# Where a recipe's per-portion numbers came from: typed by the person, printed on the recipe, or estimated.
DISH_SOURCES = ("own", "recipe", "ai")

CONF_PERSON = "person"
CONF_AI_TASK = "ai_task_entity"
CONF_NOTIFY = "notify_service"
CONF_OPEN_PATH = "open_path"
CONF_WEBHOOK_ID = "webhook_id"
CONF_PLAN_SENSOR = "meal_plan_sensor"  # see docs/integration.md, "Meal plan sensor contract"
CONF_DISHES_SENSOR = "dishes_sensor"  # see docs/integration.md, "Dish library sensor contract"

DEFAULT_GOALS = {"kcal": 2000.0, "protein_g": 100.0, "carbs_g": 230.0, "fat_g": 70.0, "fibre_g": 30.0}

PHOTO_DIR = "food_diary"  # under the local media folder, so ai_task can attach the pictures
PHOTO_DAYS = 30
MAX_IMAGE_BYTES = 4 * 1024 * 1024

EVENT_LOGGED = "food_diary_logged"
UNDO_PREFIX = "FOOD_DIARY_UNDO|"

OFF_URL = "https://world.openfoodfacts.org/api/v2/product/{code}"
OFF_FIELDS = (
    "image_front_small_url,image_front_url,product_name,product_name_en,generic_name,brands,nutriments,"
    "serving_quantity,serving_size,product_quantity,product_quantity_unit,quantity"
)
OFF_USER_AGENT = "ha-food-diary/1.0 (+https://github.com/HaydenGriffin/ha-food-diary)"
