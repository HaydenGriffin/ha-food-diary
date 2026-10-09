# Food Diary integration reference

`food_diary` is a Home Assistant custom integration that keeps a calorie and macro diary for each person. It can work out
food from a meal photo, a nutrition label, a barcode (via Open Food Facts), a short description or a recipe. It provides
sensors, goal numbers, services, a webhook for phone shortcuts and voice intents. Optionally, it can read a meal plan so
planned meals count ahead of time.

This page covers setup, the entities, every service with its fields and response, the webhook, voice, the two optional
sensor contracts, and how another integration can provide the recipes and the plan instead.

- [Installation](#installation)
- [Configuration](#configuration)
- [Entities](#entities)
- [Choosing a diary](#choosing-a-diary)
- [Services](#services)
- [Webhook](#webhook)
- [Event](#event)
- [Voice (optional)](#voice-optional)
- [Meal plan sensor contract](#meal-plan-sensor-contract)
- [Dish library sensor contract](#dish-library-sensor-contract)
- [Recipes and plan from another integration](#recipes-and-plan-from-another-integration)
- [Recipe numbers check](#recipe-numbers-check)
- [Storage and privacy](#storage-and-privacy)

## Installation

**HACS:** add this repository as a custom repository (category *Integration*), install **Food Diary**, then restart
Home Assistant.

**Manual:** copy `custom_components/food_diary` into your configuration folder's `custom_components/`, then restart.

Then go to **Settings → Devices & services → Add integration → Food Diary**. Add the integration once per person.

Requirements:

- Home Assistant 2026.2 or newer. The tests run against 2026.2.3.
- An [AI Task](https://www.home-assistant.io/integrations/ai_task/) entity for photos, labels, text and recipe estimates.
  It must accept image attachments. Manual logging and barcode lookups work without one.

## Configuration

**Person** (setup only) is the `person` entity whose diary this is. Each person can have one diary.

Every other field is optional. You can set them during setup and change them later under **Configure**:

| Option | Key | What it does |
| --- | --- | --- |
| AI Task entity | `ai_task_entity` | The entity that reads photos, labels and descriptions. If empty, the default AI Task entity is used. |
| Notify service | `notify_service` | Gets a notification with **Undo** after food is logged by voice or webhook (e.g. `notify.mobile_app_<device>`). |
| Page to open from notifications | `open_path` | A dashboard path, such as `/lovelace/food`. Adds a **Change** button that opens `<path>#food-<entry id>`. |
| Meal plan sensor | `meal_plan_sensor` | Turns on the [meal planner](#meal-plan-sensor-contract). |
| Dish library sensor | `dishes_sensor` | Gives planned meals their recipes' numbers, fills `get_dishes`, and turns on the [recipe numbers check](#recipe-numbers-check). |
| Recipes and meal plan from | `source` | Shown once another integration [offers its own](#recipes-and-plan-from-another-integration). When set, it replaces both sensors. |

The **Configure** dialog also shows the diary's webhook address. Keep it private.

If the home's country is set (**Settings → System → General**), the AI prompts mention it, so portion sizes follow local
habits.

When neither sensor (nor a source) is set, the planner and the background check don't run. In that state `sync_plan` returns
`{"changed": 0, "completed": []}` and `get_dishes` returns `{"dishes": []}`. `check_numbers` still works for diary entries,
by comparing them with a normal portion of the same name.

## Entities

Each diary adds one device, `<Person> food diary`, with these entities:

| Entity | Unit | Notes |
| --- | --- | --- |
| `sensor.<person>_food_diary_calories_today` | kcal | Attributes: `goal`, `left`, `logged` (entry count), and `breakfast`, `lunch`, `dinner`, `snack` (kcal per meal). |
| `sensor.<person>_food_diary_protein_today`, `…_carbs_today`, `…_fat_today`, `…_fibre_today` | g | Attributes: `goal`, `left`. |
| `sensor.<person>_food_diary_calories_left` | kcal | Calories left against the goal (negative when over). Attribute: `goal`. |
| `sensor.<person>_food_diary_last_logged` | timestamp | Attributes: `name`, `kcal`, `meal`, `entry_id`, `rev`. |
| `number.<person>_food_diary_calorie_goal`, `…_protein_goal`, `…_carbs_goal`, `…_fat_goal`, `…_fibre_goal` | kcal / g | Daily goals. Default 2000 kcal, 100 g protein, 230 g carbs, 70 g fat and 30 g fibre. |

The five "today" sensors use `state_class: total` and reset at local midnight. That keeps long-term statistics, history
graphs and averages working.

Every diary sensor also has the attribute `api`: the version of the service contract this diary supports. It is `2` from
release 1.1.0 (creates take [`client_id`](#doing-things-once-and-revisions), entries carry `rev` and changes take
`expected_rev`). Apps should only send `client_id` and `expected_rev` to a diary whose `api` is 2 or more.

## Choosing a diary

Every service that takes `person` (a `person.*` entity id) picks the diary in this order:

1. the diary named by `person`;
2. otherwise, the diary of the person linked to the calling Home Assistant user;
3. otherwise, the only diary, if just one is set up.

If more than one diary is set up and none of these applies, the call fails with a validation error.

## Services

The service names and response shapes below are the public contract used by the companion iOS app. Unless stated
otherwise:

- dates are `YYYY-MM-DD`, and a missing `date` means today;
- `meal` is one of `breakfast`, `lunch`, `dinner` or `snack`;
- the five numbers are `kcal`, `protein_g`, `carbs_g`, `fat_g` and `fibre_g`.

"Response: optional" means the service can be called with or without `return_response`. "Response: only" means it must be
called with a response.

### Doing things once, and revisions

**`client_id`.** `log_food`, `copy_day` and the [webhook](#webhook) accept an optional `client_id`: an id the app makes
once for one action (8 to 64 letters, digits, `-` or `_`; a UUID is fine), kept with the request so a retry sends the
same one. The diary remembers every `client_id` it has seen, with the entry it made, in a ledger that is never trimmed and
outlives the entry. So:

- the same `client_id` with the same request returns the first result with `duplicate: true`, and nothing new is made;
- if that entry has been deleted since, the response says `deleted: true` (and gives the original `entry_id`), and the
  food is **not** logged again;
- the same `client_id` with a different request fails with a validation error (translation key `client_id_reused`);
- two calls with the same `client_id` at the same time make one entry: the second waits for the first.

Without `client_id`, every call makes a new entry, as before. The diary never makes up ids itself. "The same request"
means the same service data apart from `client_id` (compared as SHA-256 of its canonical JSON).

**`rev`.** Every entry has an integer `rev`: 1 when it's made, and one more on every change (by you, the planner or a
recipe's new numbers). `update_food`, `delete_food` and `set_photo` accept an optional `expected_rev`: the `rev` you last
saw. If the entry has changed since, nothing happens and the call fails with a validation error (translation key
`conflict`); get the day again and decide.

Background work that waits for the AI (the meal planner, recipe estimates, completing partial recipe numbers) notes what
it started from and only keeps its answer if that is still the same afterwards. Food logged by hand, an edit, a deletion or
a new plan in the meantime always wins.

### Entry shape

The responses below refer to an **entry**. It has this shape:

```json
{
  "id": "a1b2c3d4e5", "at": "2026-10-07T08:15+01:00", "meal": "breakfast", "name": "Porridge",
  "portions": 1.5, "source": "manual", "ref": "",
  "per_portion": {"kcal": 320, "protein_g": 12, "carbs_g": 54, "fat_g": 6, "fibre_g": 5},
  "kcal": 480, "protein_g": 18, "carbs_g": 81, "fat_g": 9, "fibre_g": 7.5, "rev": 1
}
```

Some keys appear only when they apply:

| Key | Meaning |
| --- | --- |
| `per_100`, `grams`, `unit` | The entry came from a label or barcode, so its numbers are `per_100 × grams`. |
| `note`, `barcode`, `photo`, `image_url` | Extra details about the food. |
| `plan_key` | The planned slot it came from (`"<date>|<meal>"`). |
| `edited: true` | The numbers were typed by hand, so the planner and recipe updates leave them alone. |
| `checked: true` | The entry was confirmed as right. |
| `client_id` | The `client_id` it was logged with. |

`rev` is always there (see [Doing things once, and revisions](#doing-things-once-and-revisions)).

Entries returned by `get_day`, `get_recent` and `set_photo` also carry `image`. That is the first that exists of:

1. the entry's own photo (`/api/food_diary/photo/<name>`, which needs a signed-in user);
2. its product photo;
3. its recipe's photo from the dish library.

`get_day` entries also carry `check` when their recipe's numbers [look wrong](#recipe-numbers-check).

Each entry's `source` is one of `photo`, `label`, `barcode`, `text`, `dish`, `manual`, `again`, `saved`, `voice` or
`import` (food brought over from another app).
`plan` is set only by the planner.

### Logging and changing

#### `food_diary.log_food` (response: optional)

Adds an entry. Give `kcal` (plus any macros) for one portion, or give `per_100` with `grams`.

| Field | Type | Notes |
| --- | --- | --- |
| `person` | entity id | See [Choosing a diary](#choosing-a-diary). |
| `name` | string | **Required.** |
| `kcal`, `protein_g`, `carbs_g`, `fat_g`, `fibre_g` | number | For one portion. |
| `portions` | number | 0.05 to 50. Default 1. |
| `meal` | meal | Defaults to the meal for the time of day (see below). |
| `date` | date | |
| `source` | string | One of the sources above. Default `manual`. |
| `ref` | string | A recipe id (`dish_id`) or a saved meal id. |
| `note`, `barcode` | string | |
| `per_100` | object | The five numbers per 100 g or 100 ml. |
| `grams` | number | The amount eaten, used with `per_100`. |
| `unit` | `g` or `ml` | |
| `edited` | boolean | Marks the numbers as typed by hand. |
| `photo` | string | The `photo` value an `estimate` returned. The picture is kept with the entry. |
| `image_url` | string | An `https://` product picture. |
| `client_id` | string | See [Doing things once](#doing-things-once-and-revisions). |

The time-of-day meal is breakfast before 10:30, lunch before 14:30, snack before 17:30, dinner before 21:30, and snack
after that.

**Response:** `{"entry": <entry>, "entry_id": "…", "date": "<date>", "totals": {<five numbers>}}`. Also fires
[`food_diary_logged`](#event). A repeated `client_id` adds `"duplicate": true` and returns the entry as it is now; once
that entry is deleted, `"entry"` is `null` and `"deleted": true` is added. A repeat fires no event.

#### `food_diary.update_food` (response: optional)

Changes an existing entry.

| Field | Type | Notes |
| --- | --- | --- |
| `person` | entity id | |
| `entry_id` | string | **Required.** |
| `date` | date | The day the entry is on. |
| `portions` | number | |
| `grams` | number | Only for entries that have `per_100`. Recalculates the numbers and clears `edited`. |
| `meal` | meal | |
| `name` | string | |
| `kcal`, `protein_g`, `carbs_g`, `fat_g`, `fibre_g` | number | Your own numbers for the **whole** entry. Sets `edited`. |
| `checked` | boolean | `true` marks the entry as confirmed right. `false` clears it. |
| `expected_rev` | integer | Fail with `conflict` if the entry's `rev` isn't this. |

**Response:** `{"entry": <entry>}`.

#### `food_diary.delete_food` (response: optional)

Removes an entry. Fields: `person`, `entry_id` (**required**), `date`, `expected_rev`. If you remove a planned entry, the
planner remembers it and won't add it back.

**Response:** `{"ok": true}`.

#### `food_diary.set_photo` (response: optional)

Adds a photo to an existing entry. Fields: `person`, `entry_id` (**required**), `date`, `expected_rev`, and `image`
(**required**, a base64 JPEG, PNG or WebP of up to 4 MB).

**Response:** `{"entry": <entry with image>}`.

#### `food_diary.set_goals` (response: optional)

Sets daily goals. Fields: `person` and any of the five numbers. Goals can also be changed through the number entities.

**Response:** `{"goals": {<five numbers>}}`.

### Reading

#### `food_diary.get_day` (response: only)

Fields: `person`, `date`.

**Response:**

```json
{"date": "…", "entries": [<entry>, …], "totals": {…}, "goals": {…}, "left": {…},
 "meals": {"breakfast": 0, "lunch": 0, "dinner": 0, "snack": 0}}
```

Entries are sorted by meal, then by time. `left` only includes numbers that have a goal.

#### `food_diary.get_history` (response: only)

Fields: `person`, `date` (the last day), `days` (1 to 366, default 7).

**Response:**

```json
{"days": [{"date": "…", "kcal": 0, "protein_g": 0, "carbs_g": 0, "fat_g": 0, "fibre_g": 0, "logged": 0}, …],
 "goals": {…}}
```

Days run from oldest to newest.

#### `food_diary.get_recent` (response: only)

Fields: `person`, `limit` (1 to 200, default 30).

**Response:**

```json
{"foods": [<food>, …], "usuals": {"breakfast": [<food>, …], "lunch": […], "dinner": […], "snack": […]},
 "saved": [<saved meal>, …]}
```

- **`foods`:** what was logged in the last 60 days, most often first.
- **`usuals`:** up to two foods per meal that were logged on at least 3 of the last 28 days. Planned entries don't count.
- **`saved`:** the diary's saved meals.

A `<food>` is what you need to log it again:

- `name`, `meal`, `source`, `ref`, and the five numbers for one portion;
- `per_100`, `grams` and `unit` when the food has them;
- `photo`, `image_url`, `barcode` and `image` when they exist;
- `times`.

#### `food_diary.get_week_review` (response: only)

Fields: `person`, `date` (the last day, default yesterday), `days` (1 to 31, default 7).

**Response:**

```json
{"start": "…", "end": "…", "days": [<history day>, …], "goal_kcal": 2000, "goal_protein_g": 100,
 "days_logged": 5, "avg_kcal": 1840, "on_target": 3, "over": 1, "protein_days": 2, "last_week_avg_kcal": 1910,
 "favourite": {"name": "Porridge", "times": 4}, "new_dishes": ["Lasagne"], "best_day": {"date": "…", "kcal": 1990}}
```

- **`on_target`:** days at 75 to 105% of the calorie goal.
- **`protein_days`:** days at 90% or more of the protein goal.
- **`favourite`**, **`best_day`** and **`last_week_avg_kcal`** can be `null`.
- **`new_dishes`:** recipes (entries with a `ref`) not eaten in the previous 8 weeks, up to 5.

### Saved meals and copying

#### `food_diary.save_meal` (response: optional)

Keeps everything logged in one meal on one day as one thing to log again. Saving with an existing name replaces that
saved meal.

| Field | Type | Notes |
| --- | --- | --- |
| `person` | entity id | |
| `name` | string | **Required.** |
| `date` | date | |
| `meal` | meal | **Required.** |

**Response:**

```json
{"saved": {"id": "…", "name": "…", "meal": "…", "items": ["Porridge", "Banana"], <five numbers>, "at": "…"}}
```

#### `food_diary.update_saved_meal` (response: optional)

Renames a saved meal or moves it to another meal. Fields: `person`, `saved_id` (**required**), `name`, `meal`. Taking
another saved meal's name replaces that one.

**Response:** `{"saved": <saved meal>}`.

#### `food_diary.delete_saved_meal` (response: optional)

Removes a saved meal. Fields: `person`, `saved_id` (**required**).

**Response:** `{"ok": true}`.

#### `food_diary.copy_day` (response: optional)

Copies a day, or one meal of it, onto other days. Each copy is eaten exactly like the original: the same portions, grams
and numbers (numbers typed by hand too). Planned food arrives as ordinary food (`source: again`).

| Field | Type | Notes |
| --- | --- | --- |
| `person` | entity id | |
| `from` | date | **Required.** |
| `to` | list of dates | **Required.** |
| `meal` | meal | Copy just this meal. |
| `replace` | boolean | Remove what the target days already have in that meal (or the whole day) first. |
| `client_id` | string | See [Doing things once](#doing-things-once-and-revisions). |

**Response:** `{"token": "…", "added": {"<date>": <count>}, "removed": {"<date>": <count>}}`. A repeated `client_id`
returns the first copy's response with `"duplicate": true` (and `"deleted": true` once every copied entry is gone, after
an `undo_copy`, say).

#### `food_diary.undo_copy` (response: optional)

Undoes one of the last ten copies. Undo history is lost on restart. Fields: `person`, `token` (**required**).

**Response:** `{"ok": true}`.

### Undoing a change made with other services

A change that takes several services (say, new numbers for a recipe, which its entries then follow, plus an edit of
one entry) can be undone exactly: take a snapshot first, then restore it.

#### `food_diary.snapshot` (response: optional)

| Field | Type | Notes |
| --- | --- | --- |
| `token` | string | Required. 4–40 of `A-Z a-z 0-9 _ -`, made by the caller. The same token again replaces its snapshot. |
| `entry_id`, `date` | string, date | Optional. An entry the change may touch (`date` defaults to today). |
| `dish_id` | string | Optional. A recipe whose numbers the change may set: its book item and its entries from today on are kept. |

Response: `{"token", "entries": <count kept>, "entry": {id, rev, name, meal, portions, ref, source, edited, kcal…,
date, per_portion} | null, "dish": <book item> | null}`. The last ten snapshots are kept, until a restart.

#### `food_diary.restore_snapshot` (response: optional)

`{token}` puts back exactly what was kept: each entry in its place (or at the end of its day if it was deleted since),
with a new `rev`, and the recipe's book item (or no item, if it had none). Response: `{"ok": true, "entries": n,
"book": bool}`. Once per token; an unknown or used token raises "That change can't be undone any more."

### Data brought over from elsewhere

#### `food_diary.rename_source` (admin only, response: optional)

`{from, to}` gives every entry (in every diary) and every recipe-book item whose `source` is `from` the source `to`, for
example after importing another app's history under its own name: `{"from": "otherapp", "to": "import"}`. Response:
`{"entries": {<person>: n}, "recipes": n}`. Running it again changes nothing.

### Estimating

#### `food_diary.estimate` (response: only)

Works out what something is and its numbers. It doesn't log anything.

| Field | Type | Notes |
| --- | --- | --- |
| `person` | entity id | Chooses whose AI setting is used. |
| `kind` | `photo`, `label`, `barcode`, `text` or `dish` | **Required.** |
| `image` | string | Base64. Required for `photo` and `label`. |
| `hint` | string | `photo` only. |
| `amount` | string | For `label` and `barcode`, e.g. "3 biscuits", "50 g" or "half the pack". |
| `barcode` | string | EAN or UPC digits. Required for `barcode`, optional for `label`. |
| `text` | string | For `text`, e.g. "2 eggs on toast". |
| `dish_id` | string | Required for `dish`. |
| `dish_name` | string | `dish` only. |
| `ingredients` | list of strings | `dish` only. |
| `servings` | number | `dish` only. |
| `amounts_per` | `recipe` or `portion` | `dish` only. |
| `fresh` | boolean | `dish` only. Ignores the kept answer and asks again. |

The AI only reads values: what is on a label, and how much was eaten. The integration does all the arithmetic. Common
amounts like "50 g", "3 biscuits" or "half the pack" are worked out without the AI.

**Response by kind:**

| Kind | Response |
| --- | --- |
| `photo` | `{name, <five numbers>, note, foods, source: "photo", photo}`. Pass `photo` to `log_food` to keep the picture with the entry. |
| `text` | `{name, <five numbers>, note, foods, source: "text"}`. |
| `label` | `{name, per_100, grams, unit, serving_g, pack_g, guessed, note, <five numbers>, source: "label", barcode?}`. |
| `barcode` | Like `label`, plus `source: "barcode"`, `barcode`, `product_source` and `image_url?`. |
| `dish` | `{name, <five numbers>, source: "dish", ref: <dish_id>, nutrition_source}`. Freshly estimated answers also have `note` and `foods`. |

For `label` and `barcode`, the five numbers are for the amount eaten. `guessed` is `true` when no amount could be worked
out, in which case one serving (or 100 g) was assumed.

A label photo that shows a barcode teaches the product book, so the next scan of that barcode needs no photo.

If a barcode is in neither the product book nor Open Food Facts, the call fails with "That product isn't in Open Food
Facts yet…". The webhook reports this case as `need_label`.

`dish` answers are kept per `dish_id`. `nutrition_source` is `own`, `recipe`, `ai` or `import`.

### Recipes

#### `food_diary.get_dishes` (response: only)

Lists the dish library's recipes. Field: `person`. Returns an empty list when no dish library sensor is set.

**Response:**

```json
{"dishes": [{"id": "…", "name": "…", "title": "…", "image": "", "source": "", "status": "", "favourite": false,
             "times": 0, "meal_types": [], "added": "",
             "kcal": 0, "protein_g": 0, "carbs_g": 0, "fat_g": 0, "fibre_g": 0, "kcal_source": "own|recipe|ai",
             "check": {…}}]}
```

- **The five numbers and `kcal_source`** appear only when the recipe book has numbers for the dish.
- **`check`** appears only when the dish's numbers look wrong.
- **`name` and `title`:** if a dish has a non-English `lang` and a different `name_en`, then `name` is the original name
  (what goes on the plan) and `title` is `"<name> (<name_en>)"`. Otherwise both are `name_en`, falling back to `name`.

#### `food_diary.get_dish_nutrition` (response: only)

Returns the recipe book's numbers for one portion of a dish. Field: `dish_id` (**required**).

**Response:** `{<five numbers>, "source", "at", "portions"?, "check"?, "completed"?, "from"?}`, or `{}` if the book
doesn't have the dish.

#### `food_diary.set_dish_nutrition` (response: optional)

Sets a dish's numbers for one portion.

| Field | Type | Notes |
| --- | --- | --- |
| `dish_id` | string | **Required.** |
| `kcal` | number | **Required.** |
| `protein_g`, `carbs_g`, `fat_g`, `fibre_g` | number | |
| `source` | `own`, `recipe`, `ai` or `import` | Default `own`. `own` and `import` numbers are never doubted. |
| `portions` | integer | 1 to 24. How many portions the recipe makes. |

Setting new numbers clears any doubt about the dish. Entries for this dish from today onwards take the new numbers, in
every diary, unless their numbers were edited.

**Response:** `{<the stored numbers>, "source", "at", "portions"?, "refreshed": <entries updated>}`.

#### `food_diary.sync_plan` (response: optional)

Brings the diary in line with the meal plan now. Without this call, a sync happens about 10 seconds after the plan or
dish library changes, and shortly after midnight. Field: `person`.

**Response:** `{"changed": <entries added, changed or removed>, "completed": [<book keys whose partial numbers were completed>]}`.
Without a meal plan sensor: `{"changed": 0, "completed": []}`.

#### `food_diary.check_numbers` (response: only)

Compares numbers with what the ingredients add up to, per portion. Give `dish_id`, or `entry_id` with `date`.

| Field | Type | Notes |
| --- | --- | --- |
| `person` | entity id | |
| `dish_id` | string | A dish in the dish library. |
| `entry_id`, `date` | string, date | A diary entry. |
| `portions` | integer | 1 to 24. How many portions to assume the recipe makes. |

An entry without a library recipe is compared with one normal portion of the same name.

**Response:**

```json
{"yours": {"kcal", "protein_g", "carbs_g", "fat_g"}, "house": {"kcal", "protein_g", "carbs_g", "fat_g"},
 "portions": 2, "reason": "Low for 120 g chicken breast and 120 g rigatoni — may serve 2", "differs": true}
```

`house` holds the suggested numbers. It keeps that name for compatibility with the iOS app.

#### `food_diary.dismiss_check` (response: optional)

Confirms that a dish's numbers are right. The doubt goes and isn't raised again until the numbers change. Fields:
`person`, `dish_id` (**required**).

**Response:** `{"ok": true}`.

## Webhook

Each diary has a webhook at `/api/webhook/<webhook id>`. The id is shown under **Configure**. It accepts `POST` with a JSON
body, works out the food, logs it immediately and answers with the numbers. A phone shortcut can then pass those numbers on,
for example to a health app.

Anyone who has the webhook id can log food to that diary, so treat it like a password.

Request bodies:

```json
{"kind": "barcode", "barcode": "5000168001142", "amount": "3 biscuits"}
{"kind": "label", "image": "<base64 JPEG>", "amount": "half the pack", "barcode": "<optional>"}
{"kind": "photo", "image": "<base64 JPEG>", "hint": "<optional>"}
{"kind": "text", "text": "2 eggs on toast"}
```

Every request can also include:

- `"meal"`: if left out, the meal is chosen by the time of day;
- `"quiet": "yes"` or `"notify": false`: skips the phone notification;
- `"client_id"`: see [Doing things once](#doing-things-once-and-revisions). A shortcut that retries should send the same
  one. A repeat answers like the first time, with `"duplicate": true`, without asking the AI or notifying again.

Responses:

```json
{"ok": true, "status": "logged", "name": "…", "meal": "…", "entry_id": "…", "date": "…",
 "kcal": 0, "protein_g": 0, "carbs_g": 0, "fat_g": 0, "fibre_g": 0, "grams": 0, "unit": "g", "guessed": false,
 "title": "Logged: …", "message": "44 g · 215 kcal · 1785 left today"}
{"ok": true, "status": "deleted", "entry_id": "…", "date": "…", "duplicate": true, "deleted": true, "message": "…"}
{"ok": false, "status": "need_label", "message": "…"}
{"ok": false, "status": "error", "message": "…"}
{"ok": false, "status": "error", "error": "client_id_reused", "message": "…"}
```

`logged` responses also carry the entry's `rev`. `deleted` answers a repeated `client_id` whose entry has been removed
since: it is not logged again. `client_id_reused` (HTTP 409) means the id was already used with a different body.

A `need_label` response means the barcode is unknown. Photograph the label and send it back with the barcode.

## Event

Logging through `log_food`, the webhook or voice fires `food_diary_logged` with this data:

```json
{"person": "person.alex", "date": "…", "entry_id": "…", "name": "…", "meal": "…", "kcal": 0, "source": "…"}
```

## Voice (optional)

The integration registers three Assist intents:

| Intent | Example sentence | What it does |
| --- | --- | --- |
| `FoodDiaryLog` | "I had porridge for breakfast" | Estimates the food, logs it and says what's left. |
| `FoodDiaryLeft` | "How many calories have I got left?" | Says today's calories left (or over). |
| `FoodDiaryUndo` | "Take the last food off" | Removes the latest entry logged today. Planned meals are never removed this way. |

To turn on the English sentences:

1. Copy [`custom_sentences/en/food_diary.yaml`](../custom_sentences/en/food_diary.yaml) to
   `<config>/custom_sentences/en/food_diary.yaml`.
2. Restart Home Assistant.

The sentences are deliberately narrow. "add" and "put" must name the food diary, so commands like "add milk to the shopping
list" stay with their own handlers.

A voice satellite has no user, so the diary is the speaker's if Home Assistant knows who is speaking, otherwise the only
diary. With several diaries and no way to tell, Assist asks rather than guessing.

## Meal plan sensor contract

Any sensor can be a meal plan, such as a template sensor or one from another integration. Its **state** is ignored. Its
`week` **attribute** is a list of days:

```yaml
week:
  - date: "2026-10-08"            # YYYY-MM-DD, required
    breakfast: "Porridge"         # optional
    lunch: "Leftovers: Chilli con carne"
    dinner: "Chicken katsu curry"
  - date: "2026-10-09"
    dinner: ""                    # empty or missing: nothing planned
  - date: "2026-10-10"
    dinner:                       # or an object, when the plan knows more
      name: "Sweet potato traybake"   # required
      dish_id: "tray-2"           # optional: the dish library recipe it is (beats matching by name)
      note: "Batch cook"          # optional
      nutrition: {kcal: 593, protein_g: 12.8, carbs_g: 65.9, fat_g: 33.4, fibre_g: 14.5}  # optional: ONE portion
      ref: "tray-2"               # optional: the entry's ref for those numbers (default: dish_id)
```

A sensor that doesn't exist, or whose state is `unavailable`, means the plan isn't available: nothing is added or removed
until it is back.

How the plan is applied:

- **Past days** are ignored.
- **Planned meals:** each non-empty `breakfast`, `lunch` and `dinner` becomes one portion in the diary, with
  `source: plan`, `plan_key: "<date>|<meal>"` and `note: "From the meal plan"`.
- **Leftovers:** a `Leftovers:` prefix is ignored when matching a meal to a dish, so leftovers count as the same dish.
- **Where the numbers come from**, first match wins:
  1. the plan's own `nutrition` for that meal (its `ref`, else its `dish_id`, becomes the entry's ref);
  2. the recipe book (`set_dish_nutrition`, or earlier estimates);
  3. the dish's own `nutrition` from the dish library;
  4. an AI estimate from the library dish's ingredients;
  5. an AI estimate from the meal's name.

  The dish is the one with the slot's `dish_id` when the library has it, else a match by name, case-insensitively,
  against a dish's `name`, `name_en` or `aliases`. A slot's `nutrition` with a `dish_id` (and no other `ref`) is also kept
  in the recipe book as that recipe's `recipe` numbers, unless the library gives numbers for it or the book has `own` ones. Estimates are kept, so each one is only
  asked once.
- **Plan changes:** if a slot's meal changes, its entry is replaced. If the slot empties, the entry is removed, unless its
  numbers were edited.
- **Deleted entries:** if you delete a planned entry, it stays deleted until that slot's meal changes.
- **No double counting:** if a meal was already logged by hand that day, the plan adds nothing.
- **Recipe changes:** if the recipe book's numbers change (for example, the library's `nutrition` arrives later), planned
  entries take them. Their portions stay, and entries whose numbers were edited are left alone.

## Dish library sensor contract

Its **state** is ignored. Its `dishes` **attribute** is a list of recipes:

```yaml
dishes:
  - id: "chilli-1"                 # required, unique, stable; used as the entry's ref and the recipe book key
    name: "Chilli con carne"       # the name used on the meal plan
    name_en: "Chilli con carne"    # optional English name (also matched against the plan)
    aliases: ["Chili"]             # optional other names the plan may use
    lang: "en"                     # optional language of `name`; non-English + name_en → title "name (name_en)"
    servings: 4                    # optional: how many portions the ingredients make
    amounts_per: "recipe"          # optional: "recipe" (default) or "portion"
    ingredients:                   # optional: used to estimate numbers and to check printed ones
      - {amount: "500 g", name: "beef mince"}
      - {amount: "1 tin", name: "kidney beans"}
    nutrition:                     # optional: the recipe's own numbers for ONE portion
      {kcal: 520, protein_g: 38, carbs_g: 40, fat_g: 21, fibre_g: 9}
    image: "/local/recipes/chilli.jpg"   # optional, shown on entries from this recipe
    # optional, passed through by get_dishes for display:
    source: "web"
    status: "to_try"
    favourite: false
    times: 3
    meal_types: [dinner]
    added: "2026-09-30"
```

A sensor that doesn't exist, or whose state is `unavailable`, means the library isn't available: the planner waits for it
rather than estimating meals by name.

**Recipe numbers.** A dish's `nutrition` is stored in the recipe book as `recipe` numbers, unless the book already has
`own` numbers for that dish. Partial numbers are completed once: for example, a recipe that prints only kcal and protein.
The missing macros are filled from an estimate of the same dish, scaled so `4·protein + 4·carbs + 9·fat` matches the
printed calories.

## Recipes and plan from another integration

The planner, the numbers check, `get_dishes` and entry pictures read the recipes and the plan through two small providers
(`custom_components/food_diary/sources.py`). The sensors above are the default ones. Another integration (a recipe manager,
a household integration) can offer its own:

```python
from custom_components.food_diary.sources import async_register_source


class Library:
    def dishes(self) -> list[dict] | None:  # the dish library contract's items; None while it isn't available
        ...

    def async_subscribe(self, on_change) -> Callable[[], None]:  # call on_change() after a change; return "stop"
        ...


class Plan:
    def days(self) -> list[dict] | None:  # [{date, breakfast?, lunch?, dinner?}], slots as in the meal plan contract
        ...

    def async_subscribe(self, on_change) -> Callable[[], None]:
        ...


unregister = async_register_source(hass, "my_recipes", library=Library(), plan=Plan())  # either one may be left out
```

- **Choosing it:** a diary reads from the source when its option **Recipes and meal plan from** (`source`) is that name.
  The option appears once a source is registered. The sensor options are then ignored.
- **Start order doesn't matter:** a diary set to a source that isn't registered yet runs without a plan or library, and
  reloads when the source registers (and again if it's withdrawn).
- **Reads are synchronous** and should be cheap: keep the last data you fetched and call `on_change()` when it changes.
- **Unavailable is not empty:** return `None` while your data can't be read. The planner then changes nothing, instead of
  removing planned entries or estimating meals by name.
- **Unknown meals:** a meal left out of a day is "not known" (a list that couldn't be read, say), and that slot is left as
  it is. `None` or `""` means nothing is planned.
- The food diary doesn't depend on your integration; yours depends on `food_diary` (add it to `dependencies` in your
  manifest).

## Recipe numbers check

When a dish library is set (a sensor or a source), each recipe with both numbers and ingredients is checked in the background:

- **When it runs:** about 90 seconds after start, then a minute after the plan or the library changes.
- **What it asks:** the AI adds up the whole recipe once. The result is cached until the ingredients change.
- **Per portion:** the total is divided by the recipe's portions. That is `portions` if set, otherwise `servings`. If
  neither is set, it is 1 when `amounts_per: portion` and 2 otherwise.

The numbers are in doubt when:

- the calories are more than 30% off; or
- the protein is more than 40% **and** more than 10 g off.

A dish in doubt gets `check: {kcal, protein_g, carbs_g, fat_g, portions, reason}`. If a different number of portions
explains the calories, the suggestion says so (for example "may serve 2"). If that portion count explains all the
numbers, the dish isn't in doubt at all.

`own` numbers and dismissed numbers are never doubted. The check appears on `get_dishes`, on `get_dish_nutrition`, and on
`get_day` entries for that recipe whose numbers weren't edited.

## Storage and privacy

- **Diary data** lives in Home Assistant's `.storage` folder:
  - `food_diary.<person>`: each person's diary, with its `client_id` ledger;
  - `food_diary.dishes`: the shared recipe book;
  - `food_diary.products`: barcodes learned from Open Food Facts or label photos.
- **Photos sent for estimates** are kept for 30 days in local media, under `food_diary/`, so AI Task can attach them.
- **Photos kept with entries** are stored in `<config>/food_diary/photos`. They are only served to signed-in users.
- **Data sent off the server:**
  - photos and descriptions go to your AI Task provider;
  - barcodes are looked up on Open Food Facts.
