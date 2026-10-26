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
| `sensor.<person>_food_diary_protein_today`, `…_carbs_today`, `…_fat_today`, `…_fibre_today` | g | Attributes: `goal`, `left`, and `incomplete` (how many of today's entries don't know this macro, when any: the state is then at least that much; see [Unknown numbers](#unknown-numbers)). |
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

### Unknown numbers

A macro that wasn't given is not a 0 that was (1.6.0, additive in `api` 2). Calories are always known; protein, carbs,
fat and fibre can be unknown:

- **Entries** carry `unknown`: a list of the macros (`protein_g`, `carbs_g`, `fat_g`, `fibre_g`) their writer didn't give
  (left out, or `null`). Their numbers stay `0`, so every sum is still a lower bound. An explicit `0` is known. The key is
  left out when nothing is unknown, except that an entry with calories and an explicit 0 for protein, carbs and fat (a
  spirit) stores `"unknown": []`, so it doesn't read as an old entry (below).
- **Entries from before 1.6.0** have no `unknown`. They are read by one rule: calories and no protein, carbs or fat at all
  means all four are unknown; anything else is fully known. Stored data isn't changed; an entry gets `unknown` written
  when something next changes it.
- **Every writer** sets it: `log_food`, the webhook, voice, the planner, `copy_day`, Undo. A `log_food` call can also
  send `unknown` itself, as `estimate`, saved meals and `get_recent` foods list it. Copied, saved and planned food brings
  its source's unknowns along (a saved meal lists a macro any of its foods misses). `update_food` giving a macro takes it
  off the list; changing portions or grams doesn't. `update_food` with `unknown` (1.6.1) puts macros back on it, stored
  as `0`: an app's Undo after a fix sends the numbers as they were and lists the ones that were unknown. `restore_food`
  (1.10.0), `snapshot` / `restore_snapshot` and `undo_copy` put it back exactly.
- **Recipe numbers** (`set_dish_nutrition`, a dish library's or a plan's `nutrition`) may leave macros out: those are
  unknown, kept in the recipe book item's `unknown`, and passed on to the planned entries.
- **Days** returned by `get_day`, `get_history`, `get_week_review` (each of its `days`) and `log_food`'s answer carry
  `incomplete`: `{<macro>: <count of entries with calories that don't know it>}`, only the macros some entry misses, and
  left out when there are none. `totals` are unchanged. Show a total with an `incomplete` count as "at least N g".
- The [numbers check](#recipe-numbers-check) never calls an unknown protein low.

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
| `per_100`, `grams`, `unit` | The entry is by weight (a label, a barcode, a photo item, a batch bowl, or switched with `update_food`), so its numbers are `per_100 × grams` (unless `edited`). `unit` is `g` or `ml`. |
| `note`, `barcode`, `photo`, `image_url` | Extra details about the food. |
| `plan_key` | The planned slot it came from (`"<date>|<meal>"`). |
| `edited: true` | The numbers were typed by hand, so the planner and recipe updates leave them alone. |
| `checked: true` | The entry was confirmed as right. |
| `client_id` | The `client_id` it was logged with. |
| `unknown` | The macros not known for it (see [Unknown numbers](#unknown-numbers)). |

`rev` is always there (see [Doing things once, and revisions](#doing-things-once-and-revisions)).

Entries returned by `get_day`, `get_recent` and `set_photo` also carry `image`. That is the first that exists of:

1. the entry's own photo (`/api/food_diary/photo/<name>`, which needs a signed-in user);
2. its product photo;
3. its recipe's photo from the dish library.

`get_day` entries also carry `check` when their recipe's numbers [look wrong](#recipe-numbers-check).

Each entry's `source` is one of `photo`, `label`, `barcode`, `text`, `dish`, `manual`, `again`, `saved`, `voice`,
`import` (food brought over from another app) or `batch` (a bowl of a [batch](#batches), `ref` its id).
`plan` is set only by the planner.

### Logging and changing

#### `food_diary.log_food` (response: optional)

Adds an entry. Give `kcal` (plus any macros) for one portion, or give `per_100` with `grams`.

| Field | Type | Notes |
| --- | --- | --- |
| `person` | entity id | See [Choosing a diary](#choosing-a-diary). |
| `name` | string | **Required**, except for a batch (then the batch's name). |
| `kcal`, `protein_g`, `carbs_g`, `fat_g`, `fibre_g` | number | For one portion, 0 to 20000 (not NaN or infinite; the same for every number below). |
| `portions` | number | 0.05 to 50. Default 1. |
| `meal` | meal | Defaults to the meal for the time of day (see below). |
| `date` | date | |
| `source` | string | One of the sources above. Default `manual`. |
| `ref` | string | A recipe id (`dish_id`), a saved meal id, or a batch id (source `batch`). |
| `note`, `barcode` | string | |
| `per_100` | object | The five numbers per 100 g or 100 ml, each 0 to 20000. |
| `grams` | number | The amount eaten, used with `per_100`. |
| `unit` | `g` or `ml` | |
| `edited` | boolean | Marks the numbers as typed by hand. |
| `photo` | string | The `photo` value an `estimate` returned. The picture is kept with the entry. |
| `image_url` | string | An `https://` product picture. |
| `client_id` | string | See [Doing things once](#doing-things-once-and-revisions). |
| `unknown` | list | Macros not known (`protein_g`, `carbs_g`, `fat_g`, `fibre_g`), as `estimate`, a saved meal or a recent food lists them. A macro left out is unknown too; see [Unknown numbers](#unknown-numbers). |
| `plan_key` | string | Puts a deleted planned entry back (Undo): its `plan_key` as it was (`<date>|<meal>`, or a side's `<date>|<meal>|side:<n>`). Kept only when it matches `date` and `meal`; with it, `source` may be `plan`. A plan that [follows the diary](#a-plan-that-follows-the-diary) puts the meal back on the plan. |

With `source: "batch"` and `ref` a batch id, `grams` is all a bowl needs: a missing `name` is the batch's, and a missing
`per_100` is the batch's cooked `per_100` (with its `unknown`). An unknown batch id then fails with "That batch isn't
there.", except for a retry with the same `client_id`, which still returns the first result.

The time-of-day meal is breakfast before 10:30, lunch before 14:30, snack before 17:30, dinner before 21:30, and snack
after that.

**Response:** `{"entry": <entry>, "entry_id": "…", "date": "<date>", "totals": {<five numbers>}, "incomplete"?: {…}}`. Also fires
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
| `grams` | number | 0.1 to 20000. For entries that have `per_100` (or with `per_100`, below): the new amount, one portion. The numbers are `per_100 × grams`; numbers of your own (`edited`) are for the amount the entry had, so they scale with it (× new grams / old grams × portions) and stay `edited` (1.10.0; before, they were replaced by `per_100 × grams` and `edited` cleared). Unknown macros stay unknown. On an entry without `per_100`, `grams` alone does nothing. |
| `per_100` | object | With `grams` only (1.10.0): the entry is by weight from now on. `{kcal, protein_g?, carbs_g?, fat_g?, fibre_g?, unknown?}`, each 0 to 20000, `kcal` needed (0 is fine); a macro left out or listed in its `unknown` is unknown (stored as 0). The entry gets this `per_100`, `unit` and `grams`, `portions: 1` and `per_portion = per_100 × grams / 100`; its name, meal, source, ref, note, photo, `client_id` and plan stay. Sent without `grams`: "per_100 needs grams: how much was eaten." Sent with `portions`, any of the five numbers or `unknown`: refused, as `per_100` and `grams` set them. |
| `unit` | `g` or `ml` | With `per_100`: what it is per. Default `g`. |
| `edited` | boolean | With `per_100`: `true` when it was worked out from the person's own corrected numbers, so the entry stays `edited` (recipe updates leave it alone). Left out or `false`: not `edited`. `unit` or `edited` without `per_100` is refused. |
| `meal` | meal | |
| `name` | string | |
| `kcal`, `protein_g`, `carbs_g`, `fat_g`, `fibre_g` | number | Your own numbers for the **whole** entry. Sets `edited`. |
| `checked` | boolean | `true` marks the entry as confirmed right. `false` clears it. |
| `unknown` | list | Macros (`protein_g`, `carbs_g`, `fat_g`, `fibre_g`) that aren't known after all: stored as `0` and listed in the entry's [`unknown`](#unknown-numbers). Wins over a number sent for the same macro. Sets `edited`. Since 1.6.1. |
| `expected_rev` | integer | Fail with `conflict` if the entry's `rev` isn't this. |

**Response:** `{"entry": <entry>, "before": <entry>}`. `before` (1.10.0) is the entry exactly as it was before this change:
keep it with the answer's `entry.rev` to undo the change with `restore_food`.

Switching a portion entry to by weight, then undoing it:

```json
{"entry_id": "a1b2c3d4e5", "per_100": {"kcal": 250, "protein_g": 9, "carbs_g": 41, "fat_g": 4, "fibre_g": 7},
 "grams": 60, "unit": "g", "expected_rev": 2}
→ {"entry": {…, "portions": 1.0, "per_100": {…}, "unit": "g", "grams": 60.0, "per_portion": {"kcal": 150.0, …},
   "kcal": 150.0, …, "rev": 3}, "before": {…, "portions": 1.5, "per_portion": {…}, "kcal": 165.0, …, "rev": 2}}

restore_food {"entry_id": "a1b2c3d4e5", "entry": <before>, "expected_rev": 3}
→ {"entry": <before, with "rev": 4>}
```

#### `food_diary.restore_food` (response: optional)

Undo of an `update_food` (1.10.0), exactly, including a switch to by weight: puts back what `update_food` can change
(name, meal, portions, `per_portion`, the five numbers, `per_100`, `grams`, `unit`, `unknown`, `edited`, `checked`) as
`entry` has it; one `entry` doesn't have is removed (so a switched entry is a portion entry again, and an entry that had
no `unknown` key has none). Everything else (id, time, source, ref, note, photo, `client_id`) is the entry's own, which
`update_food` never changes, and is not taken from `entry`. With `expected_rev` the `rev` that update made, the entry is
then exactly `before` with a new `rev` (one more). A plan that [follows the diary](#a-plan-that-follows-the-diary) is
told as for an update: a meal moved back (`moved` or `restored`), a name put back (`renamed`).

| Field | Type | Notes |
| --- | --- | --- |
| `person` | entity id | |
| `entry_id` | string | **Required.** |
| `date` | date | The day the entry is on. |
| `entry` | object | **Required.** `before` from `update_food`'s answer, as it was. Its numbers must be real (finite, 0 or more), `meal` one of the four, `unit` `g` or `ml`, and `per_100` and `grams` together; its `id`, when given, must be `entry_id`. |
| `expected_rev` | integer | **Required.** The `rev` of `update_food`'s answer. If the entry has changed since (another change, the planner, a recipe's new numbers, a photo), nothing changes and the call fails with `conflict`. |

**Response:** `{"entry": <entry>}`. Fails with "That entry isn't in the diary on that day." when it's gone (Undo doesn't
bring a deleted entry back; `log_food` does).

#### `food_diary.delete_food` (response: optional)

Removes an entry. Fields: `person`, `entry_id` (**required**), `date`, `expected_rev`. If you remove a planned entry, the
planner remembers it and won't add it back; with a plan that [follows the diary](#a-plan-that-follows-the-diary), the meal
comes off the plan instead.

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
{"date": "…", "entries": [<entry>, …], "totals": {…}, "incomplete": {"protein_g": 1, "fibre_g": 2}, "goals": {…},
 "left": {…}, "meals": {"breakfast": 0, "lunch": 0, "dinner": 0, "snack": 0}}
```

Entries are sorted by meal, then by time. `left` only includes numbers that have a goal. `incomplete` is only there when
an entry misses a macro ([Unknown numbers](#unknown-numbers)); `totals` and `left` count those as 0.

#### `food_diary.get_history` (response: only)

Fields: `person`, `date` (the last day), `days` (1 to 366, default 7).

**Response:**

```json
{"days": [{"date": "…", "kcal": 0, "protein_g": 0, "carbs_g": 0, "fat_g": 0, "fibre_g": 0, "incomplete"?: {…},
            "logged": 0}, …],
 "goals": {…}}
```

Days run from oldest to newest. `incomplete` as in `get_day`.

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
- `unknown` when some macros aren't known (send it back with `log_food`);
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
- **`protein_days`:** days at 90% or more of the protein goal. A day with an unknown protein counts only when what is
  known already reaches it (its total is a lower bound).
- **`favourite`**, **`best_day`** and **`last_week_avg_kcal`** can be `null`.
- **`new_dishes`:** recipes (entries with a `ref`) not eaten in the previous 8 weeks, up to 5.

### Saved meals and copying

#### `food_diary.save_meal` (response: optional)

Keeps everything logged in one meal on one day as one thing to log again, or, with `foods`, exactly those foods. Saving
with an existing name replaces that saved meal.

| Field | Type | Notes |
| --- | --- | --- |
| `person` | entity id | |
| `name` | string | **Required.** |
| `date` | date | Ignored with `foods`. |
| `meal` | meal | **Required.** |
| `foods` | list | At most 50, each `{name, <five numbers>, unknown?}`, as `log_food` takes them (numbers 0 to 20000): saved as they are, whatever else that meal holds. Foods without calories are left out; other fields are ignored. |

**Response:**

```json
{"saved": {"id": "…", "name": "…", "meal": "…", "items": ["Porridge", "Banana"], <five numbers>, "unknown"?: […],
           "at": "…"}}
```

`unknown` lists a macro any of its foods doesn't know (its number is then a lower bound).

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

### Batches

A batch is a pot cooked from weighed raw ingredients, eaten by the bowl in grams. Its totals are each raw ingredient's
`per_100` × its grams. Cooking changes the weight but not the totals: each ingredient's `cooked_factor` (cooked weight /
raw weight, 0.2 to 4; dry pasta about 2.2, mince about 0.7) gives the pot's weight when it wasn't weighed. A bowl is then
the batch's cooked `per_100` × grams (`log_food` with `source: "batch"`). Batches are kept in each person's diary for 400
days: older ones go when the diary loads, on `get_batches` and on `save_batch`.

#### `food_diary.save_batch` (response: optional)

| Field | Type | Notes |
| --- | --- | --- |
| `person` | entity id | |
| `name` | string | **Required.** The same name (any case) on the same day replaces that batch, keeping its id. |
| `date` | date | The day it was cooked. |
| `ingredients` | list | **Required**, 1 to 50: each `{name, grams, per_100, cooked_factor?, ref?, unknown?}`, raw. `per_100` needs `kcal` (0 is fine, for water); its numbers and `grams` are 0 to 20000. `cooked_factor` defaults to 1. A macro `per_100` leaves out (or `unknown` lists) is unknown. Together they weigh at least 1 g ("Give each ingredient's raw weight (grams)." otherwise). |
| `cooked_g` | number | The whole pot weighed after cooking. Leave out to estimate it. |
| `portions` | number | How many portions it made (0.5 to 100). |
| `recipe_id` | string | The recipe it was cooked from. |

**Response:**

```json
{"batch": {"id": "…", "name": "Chilli", "date": "<date>", "at": "…", "recipe_id"?: "…",
           "ingredients": [{"name": "…", "grams": 500, "per_100": {<five numbers>}, "cooked_factor": 0.7, "ref"?: "…",
                            "unknown"?: […]}],
           "raw_g": 1400, "cooked_g": 1750, "cooked_estimated": true, "portions"?: 5,
           "totals": {<five numbers>}, "per_100": {<five numbers>}, "unknown"?: […]},
 "refreshed": 0}
```

`cooked_g` is the weighed weight, or Σ grams × `cooked_factor` (`cooked_estimated: true`). `per_100` is the cooked
batch's: `totals` / `cooked_g` × 100. `unknown` lists a macro any ingredient lacks (its numbers are then a lower bound).
`refreshed` counts the entries that took a replaced batch's new numbers (see `update_batch`).

#### `food_diary.update_batch` (response: optional)

The pot's real weight, weighed after all, or how many portions it made. Fields: `person`, `batch_id` (**required**),
`cooked_g`, `portions`. Entries logged from the batch (source `batch`, `ref` its id) whose numbers weren't edited take
its new `per_100`; their grams stay.

**Response:** `{"batch": <batch>, "refreshed": <entries changed>}`.

#### `food_diary.delete_batch` (response: optional)

Forgets a batch; what was logged from it stays. Fields: `person`, `batch_id` (**required**). **Response:** `{"ok": true}`.

#### `food_diary.get_batches` (response: only)

Fields: `person`, `days` (1 to 400, default 5).

**Response:** `{"recent": [<batch>], "regulars": [{"name": "…", "last": <batch>}]}`. `recent` is the batches cooked today
and on the days before, `days` days in all, newest first. `regulars` is every dish batched before, one per name (any
case), newest first, with its last batch: start a new one from its ingredients.

### Undoing a change made with other services

To undo one `update_food`, use its answer's `before` with [`restore_food`](#food_diaryrestore_food-response-optional):
it survives a restart and is refused when the entry changed since. A change that takes several services (say, new numbers for a recipe, which its entries then follow, plus an edit of
one entry) can be undone exactly: take a snapshot first, then restore it.

#### `food_diary.snapshot` (response: optional)

| Field | Type | Notes |
| --- | --- | --- |
| `token` | string | Required. 4–40 of `A-Z a-z 0-9 _ -`, made by the caller. The same token again replaces its snapshot. |
| `entry_id`, `date` | string, date | Optional. An entry the change may touch (`date` defaults to today). |
| `dish_id` | string | Optional. A recipe whose numbers the change may set: its book item and its entries from today on are kept. |

Response: `{"token", "entries": <count kept>, "entry": {id, rev, name, meal, portions, ref, source, edited, kcal…,
date, per_portion, unknown?} | null, "dish": <book item> | null}`. The last ten snapshots are kept, until a restart.

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
| `kind` | `photo`, `label`, `barcode`, `text`, `dish`, `ingredients` or `per_100` | **Required.** |
| `image` | string | Base64. Required for `photo` and `label`. |
| `hint` | string | `photo` and `per_100`. |
| `name` | string | `per_100`: the food's name, up to 120 characters ("Multigrain bread"). Required for it. |
| `amount` | string | For `label` and `barcode`, e.g. "3 biscuits", "50 g" or "half the pack". |
| `barcode` | string | EAN or UPC digits. Required for `barcode`, optional for `label`. |
| `text` | string | For `text`, e.g. "2 eggs on toast". |
| `dish_id` | string | Required for `dish`. |
| `dish_name` | string | `dish`, or the batch's name for `ingredients`. |
| `ingredients` | list of strings | `dish` only. |
| `servings` | number | `dish` only. |
| `amounts_per` | `recipe` or `portion` | `dish` only. |
| `fresh` | boolean | `dish` only. Ignores the kept answer and asks again. |
| `items` | list | `ingredients` only, 1 to 30: each `{name, grams?, amount?, barcode?, per_100?, unknown?}`. |

The AI only reads values: what is on a label, and how much was eaten. The integration does all the arithmetic. Common
amounts like "50 g", "3 biscuits" or "half the pack" are worked out without the AI.

**Response by kind:**

| Kind | Response |
| --- | --- |
| `photo` | `{name, <five numbers>, note, foods, meals?, source: "photo", photo}`. Pass `photo` to `log_food` to keep the picture with the entry. |
| `text` | `{name, <five numbers>, note, foods, meals?, source: "text"}`. |
| `label` | `{name, per_100, grams, unit, serving_g, pack_g, guessed, note, <five numbers>, source: "label", barcode?}`. |
| `barcode` | Like `label`, plus `source: "barcode"`, `barcode`, `product_source` and `image_url?`. |
| `dish` | `{name, <five numbers>, source: "dish", ref: <dish_id>, nutrition_source}`. Freshly estimated answers also have `note` and `foods`. |
| `ingredients` | `{items: [{name, grams, per_100, cooked_factor, unknown?}]}`, one per item asked, in order. |
| `per_100` | `{name, per_100: {<five numbers>}, unit: "g" or "ml", unknown?, source: "estimate"}`. Since 1.10.0. |

`meals` (photo and text): the meals the food is usually eaten as, most usual first (`breakfast`, `lunch`, `dinner`, `snack`),
when the AI said; an app can use it to suggest where food goes when nobody picked a meal. `log_food` ignores it.

`items` (photo, when it shows two or more separate things): each `{name, grams, unit, per_100, <five numbers>, unknown?}`,
about what that thing weighs and its numbers per 100 g (or ml); `unknown` lists a macro the AI didn't give for it (0 in
its numbers). The meal's own numbers are then what the items add up to, and its `unknown` is every macro an item lacks.
When the AI listed something that couldn't be used (no name, calories or weight) or more than 12 things, the items that
could are still returned with `items_partial: true`, and the meal keeps the AI's estimate for the whole plate, so it can
be more than the items add up to. To log them one by one, send each item's `per_100` and `grams` to `log_food`; an app
can let the person correct each weight first.

`ingredients` works out a batch's raw ingredients in one AI call: `grams` is the weight given, else the AI's reading of
`amount` ("1 onion", "2 tins"); `per_100` is raw, and an item's own `per_100` (one of the person's foods) or a `barcode`
in the product book keeps those numbers (a 0 among them, like water's calories, is kept); `cooked_factor` is cooked
weight / raw weight, 0.2 to 4. The AI's answer is matched to the ingredients by their number in the list (by name only
where one ingredient has it). When the answer leaves any ingredient out, or without a weight or calories, nothing is
returned: it fails with "I couldn't work out <names>. Try again, or give their weight and numbers." Send the items to
`save_batch` as its `ingredients`.

`per_100` works out one food by its name in one AI call: its numbers per 100 g as it is eaten (cooked, prepared or
served as the name says), or per 100 ml for a drink (`unit: "ml"`; g and ml are never converted). `name` is the name
asked (its first 80 characters). Calories must be real (0 to 900, 0 for water): otherwise it fails with "I couldn't work out <name> per 100 g. Try
again." A macro the AI leaves out, or gives as something that can't be (not a number, negative, over 100 per 100), is
listed in `unknown` and is 0. Nothing is kept. To log it by weight, send `per_100`, `unit` and `grams` to `log_food`;
to switch a logged entry, to `update_food`. Example: `{"kind": "per_100", "name": "Multigrain bread", "hint": "toasted"}`
→ `{"name": "Multigrain bread", "per_100": {"kcal": 250.0, "protein_g": 9.0, "carbs_g": 41.0, "fat_g": 4.0,
"fibre_g": 7.0}, "unit": "g", "source": "estimate"}`.

`note` (photo and text): what one portion is, with about what it weighs ("1 slice, about 40 g"), so an app can show the
weight and scale it with the portions.

Every kind adds `unknown` when the answer lacks a macro (the AI left it out, or Open Food Facts doesn't list it); its
number is then 0. Send it on with `log_food`.

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

- **The five numbers and `kcal_source`** appear only when the recipe book has numbers for the dish. `unknown` lists the macros
  those numbers don't have, when any.
- **`check`** appears only when the dish's numbers look wrong.
- **`name` and `title`:** if a dish has a non-English `lang` and a different `name_en`, then `name` is the original name
  (what goes on the plan) and `title` is `"<name> (<name_en>)"`. Otherwise both are `name_en`, falling back to `name`.

#### `food_diary.get_dish_nutrition` (response: only)

Returns the recipe book's numbers for one portion of a dish. Field: `dish_id` (**required**).

**Response:** `{<five numbers>, "unknown"?, "source", "at", "portions"?, "check"?, "completed"?, "from"?}`, or `{}` if the book
doesn't have the dish.

#### `food_diary.set_dish_nutrition` (response: optional)

Sets a dish's numbers for one portion.

| Field | Type | Notes |
| --- | --- | --- |
| `dish_id` | string | **Required.** |
| `kcal` | number | **Required.** |
| `protein_g`, `carbs_g`, `fat_g`, `fibre_g` | number | One left out is unknown (kept in the book item's `unknown`), not 0. |
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
                                  # (a macro left out or null is unknown, not 0)
      ref: "tray-2"               # optional: the entry's ref for those numbers (default: dish_id)
      sides:                      # optional: what's planned with it, in order (at most 6)
        - "Green salad"           # a name, or an object like a slot's (name, dish_id, nutrition, ref)
        - {name: "Garlic bread", dish_id: "bread-1"}
```

A sensor that doesn't exist, or whose state is `unavailable`, means the plan isn't available: nothing is added or removed
until it is back.

How the plan is applied:

- **Past days** are ignored.
- **Planned meals:** each non-empty `breakfast`, `lunch` and `dinner` becomes one portion in the diary, with
  `source: plan`, `plan_key: "<date>|<meal>"` and `note: "From the meal plan"`.
- **Sides:** each of a meal's `sides` becomes its own entry too, once the meal's own planned entry is there, with
  `plan_key: "<date>|<meal>|side:<n>"` (n = its position) and `note: "With <meal>"`. Its numbers come the same way as a
  meal's. When something was logged in that meal before the plan (so the plan added nothing), no sides are added either;
  a side planned later, once the meal's entry is there, is added then. A side taken off the plan goes (unless its numbers
  were edited); one that moves up or down the list keeps its entry.
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
- **Only its own:** the planner removes only entries it made. One you logged that the plan took (below) is just let go.
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
      {kcal: 520, protein_g: 38, carbs_g: 40, fat_g: 21, fibre_g: 9}   # a macro left out or null: unknown, not 0
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

### A plan that follows the diary

A plan provider may also have `follow(change)` (a callback, in the event loop; it must not block). Then the diary and the
plan stay in step both ways. Each of the person's own changes to a planned entry is passed to it as it happens:

| `action` | When | Also |
| --- | --- | --- |
| `deleted` | A planned entry is deleted, or moved to a snack. | |
| `renamed` | A planned entry is renamed. | `previous`: the old name. |
| `moved` | A planned entry is moved to another planned meal. Its `plan_key` follows. | `from`: the meal it left; `previous`: the name before. |
| `restored` | A planned entry is back: `log_food` with its `plan_key`, `restore_snapshot`, `undo_copy`, or moved back from a snack to the meal it was planned for. | |

Every change has `date`, `meal` (the plan meal it's in now) and `name` (its name now). Past days are passed on too. The
planner's own adds and removals never are.

A change to a planned **side** has `side` too (its position in the slot's `sides`, with `meal` the meal it's a side of).
A side is never `moved`: moved to another meal it is `deleted` (and `restored` when moved back). A plan that offers
`sides` must read `side`, or it would take a side's delete for the meal's.

- **Change `days()` at once**, then write the change to your plan. The planner reads the plan straight after; until it says
  the new name, a renamed entry would look like a plan change.
- **Deletes aren't remembered** as dismissed: the plan no longer has the meal, so there is nothing to keep out.
- **Taking her food:** when the plan is set to the name of something already logged in that meal (an app asking "Replace
  the planned dish with this?"), that entry becomes the planned one: it gets the `plan_key`, keeps its numbers, and the
  old planned entry goes. If the plan later moves on, it stays as hers, without the `plan_key`.

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

Dismissed numbers are never doubted. Your own numbers (`own`, or `import`) aren't either, until the recipe's ingredients
change after you gave them: then they are checked like any other (kept as they are; the check only asks). New numbers
are trusted again for the ingredients of the time. The check appears on `get_dishes`, on `get_dish_nutrition`, and on
`get_day` entries for that recipe whose numbers weren't edited.

## Storage and privacy

- **Diary data** lives in Home Assistant's `.storage` folder:
  - `food_diary.<person>`: each person's diary, with its `client_id` ledger, saved meals and batches;
  - `food_diary.dishes`: the shared recipe book;
  - `food_diary.products`: barcodes learned from Open Food Facts or label photos.
- **Photos sent for estimates** are kept for 30 days in local media, under `food_diary/`, so AI Task can attach them.
- **Photos kept with entries** are stored in `<config>/food_diary/photos`. They are only served to signed-in users.
- **Data sent off the server:**
  - photos and descriptions go to your AI Task provider;
  - barcodes are looked up on Open Food Facts.
