# Changelog

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
