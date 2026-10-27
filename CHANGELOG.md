# Changelog

## 1.11.0 (27 Oct 2026)

- Veg: every `estimate` with numbers gives `veg_g`, the grams of vegetables and pulses (beans, lentils, chickpeas) in it
  as eaten; potatoes and fruit don't count. For one portion on `photo`, `text`, `dish` and menu dishes; in `per_100` on
  `per_100`, `label`, `ingredients` and each photo item. A photo with items has what its items add up to. Left out when
  the AI gives none that can be true. Open Food Facts has no veg; a label photo teaches it to the product book.
- Entries keep veg: `log_food` takes `veg_g` (one portion) or `veg_g` in `per_100`, `update_food` takes `veg_g` (the
  person's own, for the whole entry) or `per_100` with it. Portions and grams scale it, copies and recent foods keep
  it, and `restore_food` puts it back. Food without it doesn't know its veg: never 0, and nothing is worked out for older food.
  A batch knows it when every ingredient does; a saved meal when every food does. Planned meals take it from the plan's
  or the library's numbers, or an estimate made anyway; nothing is asked for veg alone.
- `update_food` with a new `portions` or `grams` and own numbers (or `veg_g`) scales the entry to the new amount first;
  only the numbers sent replace what that comes to.
- Days (`get_day`, `get_history`, each `get_week_review` day, the calories today sensor) have `veg_g` and `veg_known`,
  how many entries know it.
- `get_week_review`: each day has `meals` (calories per meal), and `last_week` has the 7 days before's average veg and
  fibre a day over the days that know veg (`veg_days`). `days` goes up to 35.
- `estimate` kind `photo` takes `plate` (`{kind: "plate" or "bowl", cm?}`): "The plate is about 26 cm across" goes in
  the prompt, so portions are sized against it.
- `estimate` kind `menu`: 1 to 4 photos of a menu (`media`) become its dishes in order, at most 40, each a typical
  restaurant portion with its listed sides, and the place's name when it shows. Nothing readable: "Couldn't read a menu
  in that photo."
- `estimate` kind `dish` without `dish_id`: a dish eaten out, by its name, with the hint saying where.
- Fullness: `set_fullness` keeps how full the person was after breakfast, lunch or dinner (`hungry`, `satisfied`,
  `full`, or `null` to take it back), for today and the 7 days before, with an optional `client_id`; kept 400 days.
  `get_fullness` reads them back newest first, each with that meal's calories and foods as they are now. `get_day` has
  the day's answers as `fullness`.

## 1.10.0 (26 Oct 2026)

- `estimate` kind `per_100` with `name` (and `hint`): one food's numbers per 100 g as it is eaten, or per 100 ml for a
  drink, in one AI call: `{name, per_100, unit, unknown?, source: "estimate"}`. Calories must be real (0 to 900); a macro
  left out or impossible is unknown.
- `update_food` takes `per_100` (with its own `unknown`), `unit` and `edited` together with `grams`: the entry is by
  weight from then on (`per_100 × grams`, one portion), keeping its name, meal, source, ref, photo and `client_id`.
  `per_100` without `grams`, or with portions, numbers or `unknown`, is refused.
- A new `grams` on an entry with numbers of the person's own (`edited`) scales them with the weight and keeps them
  `edited`, instead of replacing them with `per_100 × grams`.
- `update_food` answers `before`, the entry as it was; `restore_food` with it and the answer's `rev` as `expected_rev`
  puts it back exactly (a switch to by weight too), refused with `conflict` when the entry changed since.
- An entry by weight keeps its numbers when its recipe's numbers change (`set_dish_nutrition`) or the planner refreshes
  a planned meal: those are per portion.
- `log_food`: `per_100` needs `kcal` (0 is fine: water is logged by its volume, not as a portion with unknown macros) and
  `grams` above 0; it may carry its own `unknown`, as with `update_food`. A macro listed unknown is stored as 0 in
  `per_100`. `source` may be `estimate`.
- An update that would make an entry's numbers bigger than 1,000,000,000 is refused, so every entry `restore_food` is
  given back restores; own numbers on an entry with 0 g are refused a new weight instead of being dropped.

## 1.9.1 (25 Oct 2026)

- `estimate` kind `ingredients` matches the AI's answer to each ingredient by its number (by name only when that name is
  asked once), so a short answer can't give one ingredient's numbers to another. An answer that leaves an ingredient
  out, or without a weight or calories, fails ("I couldn't work out Rice. …") instead of returning zeros. An ingredient's
  own (or the product book's) 0 kcal is kept, not replaced by the AI's guess.
- A photo's `items` each say which macros they don't know (`unknown`), and the meal's `unknown` is theirs. When some
  things the AI listed couldn't be used (or there were more than 12), the meal keeps its whole-plate estimate and the
  usable items come back with `items_partial: true`.
- Numbers are checked everywhere they're taken: `per_100` (log_food, save_batch, estimate items), `save_meal`'s `foods`
  (at most 50) and `set_dish_nutrition` refuse negative, NaN, infinite and too-big numbers. A batch ingredient's
  `per_100` needs `kcal`, and a batch needs at least 1 g of ingredients; a tiny pot keeps its precision.
- A retried `log_food` (same `client_id`) from a batch deleted since returns the first result instead of failing.
- Batches older than 400 days go when the diary loads and on `get_batches`, not only when another batch is saved.

## 1.9.0 (25 Oct 2026)

- Batches: a pot cooked from weighed raw ingredients, eaten by the bowl in grams. `save_batch` keeps it with its totals,
  its cooked weight (weighed, or estimated from how cooking changes each ingredient's weight) and its cooked per 100 g.
  `update_batch` takes the real weight later, and bowls already logged follow. `delete_batch`, and `get_batches` for the
  batches of the last 5 days and the dishes batched before (one per name, with its last batch). Kept 400 days.
- `estimate` kind `ingredients`: a batch's raw ingredients in one AI call, each with grams (from an amount like "1 onion"
  when no weight is given), raw numbers per 100 g and `cooked_factor`. Known products keep their own numbers.
- `log_food` takes source `batch` with `ref` the batch id; the batch's name and per 100 g are used when not given.

## 1.8.0 (25 Oct 2026)

- A photo `estimate` lists each separate thing on the plate as `items`, with about what it weighs and its numbers per
  100 g, so an app can show every item's weight and let the person correct it. A photo of one thing has no `items`.
- `save_meal` takes `foods`: save exactly these foods as a meal (what a photo turned into), not everything in that meal.

## 1.7.1 (22 Oct 2026)

- The `note` on a photo or text estimate says about what one portion weighs ("1 slice, about 40 g"), so an app can show
  the food's weight and not only its grams of protein.

## 1.7.0 (20 Oct 2026)

- `estimate` for a photo or text says which meals the food is usually eaten as (`meals`, most usual first), so an app
  can put pad thai under dinner rather than the time of day's breakfast when nobody picked a meal. `log_food` ignores it.

## 1.6.1 (13 Oct 2026)

- `update_food` takes `unknown`, so Undo after a fix can mark a macro unknown again instead of writing a measured 0.
- Your own recipe numbers are checked again once the recipe's ingredients change. The numbers are kept; only the
  check asks ("Numbers look high/low"). New numbers, or "They're right", are trusted again.

## 1.6.0 (12 Oct 2026)

- Unknown numbers are not zeros. Entries carry `unknown`: the macros their writer left out. Numbers stay 0, so totals
  are a lower bound, and days report `incomplete` per macro. Macro sensors gain an `incomplete` attribute.
- Every writer sets it: `log_food`, the webhook, voice, AI and Open Food Facts estimates, copies, saved meals, recent
  foods and the planner. Older entries are read with one rule; nothing is migrated.
- The numbers check never calls an unknown protein low.

## 1.5.0 (12 Oct 2026)

- Sides on a planned meal: a plan slot may list up to 6 sides, and each becomes its own diary entry ("With <meal>").
- A following plan is told when a side is deleted, renamed or put back.

## 1.4.0 (11 Oct 2026)

- A meal plan can follow the diary: deletes, renames, moves and put-backs of planned entries are passed to the plan
  provider as they happen, so deleting a planned meal takes it off the plan.
- `log_food` takes `plan_key`, so an app's Undo puts a deleted planned entry back, still linked.
- The planner no longer double-counts after a move, and only removes entries it made.

## 1.3.0 (9 Oct 2026)

- `food_diary.snapshot` and `restore_snapshot`: exact Undo for changes made with other services.
- An `import` source for food logged in another app, trusted like your own numbers.
- `food_diary.rename_source` (admin) renames a source across every diary and the recipe book.
- Last logged reads stored times without an offset as local time, and ignores planned meals ahead of today.

## 1.2.0 (9 Oct 2026)

- Recipes and the meal plan come through a small provider interface. The sensors are the default providers, and
  another integration can register its own with `async_register_source`.
- An unavailable provider makes the planner wait instead of removing entries. Plan slots may carry a `dish_id`, a
  note and their own nutrition, and dishes may carry aliases.

## 1.1.0 (8 Oct 2026)

- `client_id` on `log_food`, `copy_day` and the webhook: a repeat makes one entry, never two.
- Entries carry a revision (`rev`), and edits take `expected_rev`. Background AI work never overwrites a newer edit.
- `copy_day` copies what was eaten (portions, grams and totals) instead of recalculating.

## 1.0.0 (7 Oct 2026)

- Initial public release.
