# Food: the iPhone app

A calorie and macro diary for iPhone that keeps everything in your own Home Assistant. The app stores no diary of its
own: every food, goal and saved meal lives in the `food_diary` custom integration (in `custom_components/` at the root
of this repo), and the app talks to it through Home Assistant's REST API as the signed-in user.

It's written in SwiftUI for iOS 18 and later, with widgets, Control Center controls, a share extension and two-way Apple
Health sync.

<!-- Screenshots: docs/screenshots/raw/ -->

## Features

- **Today.** A calorie ring with protein, carbs and fat rings, what's left, and each meal with its own Add. Swipe across
  the card to change day; swipe a food right for ½ · 1 · 1½ · 2 portions, or left to remove it (with Undo).
- **A week strip.** Monday-to-Sunday rings, swiped like pages. Long-press a day to copy or move its food.
- **Adding food, five ways.**
  - Photo of a meal, with an optional note ("only ate half").
  - Barcode scan (Open Food Facts, through the integration), or type the digits.
  - Photo of a nutrition label, plus how much you had.
  - Typing: "2 eggs on toast". A list ("porridge, a banana, a flat white") becomes separate foods to check together.
  - Search what you've had before, and your saved meals, one tap each.
- **Check before it's added.** Portions, grams, the meal, or your own numbers. The button says where it goes ("Add to
  tomorrow's breakfast").
- **Usuals and saved meals.** An empty meal suggests your usuals and "Same as yesterday". Two or more foods in a meal can
  be saved as one meal, then renamed, moved or deleted.
- **Plan ahead and copy.** Put food in on later days; copy a day or a meal to other days, with Undo.
- **A growing sprig.** A leaf for each day in a row within 75–105% of your goal. "Count exercise" gives a day room for
  the active calories Apple Health says you burned.
- **Looking back.**
  - The week in review on Sunday afternoon and Monday: days on target, your average, one highlight.
  - A month calendar with Apple's activity rings beside each day's food.
  - A swipeable monthly recap.
- **Reminders (opt-in).** A nudge when a meal isn't in by its time, with your usual as a notification button; an evening
  protein nudge; a cheer the morning after a good day.
- **No signal.** Foods with known numbers wait on the phone and go in when Home Assistant can be reached.
- **Apple Health.**
  - Food goes in as food correlations, kept in step with the diary, including edits and deletes.
  - Activity, rings and sleep are read to show beside your food.
  - Optionally, they can be sent to a Home Assistant webhook (see below).
- **Widgets and controls.** Calories left on the Home and Lock Screen; "Log food" and "Scan food" for Control Center
  and the Action button.
- **Siri.** "Log food in Food", "Calories left in Food", "Scan food in Food".
- **Share sheet.** Share a photo of a meal or a label from Photos or anywhere else; it's worked out, checked and added.

The app uses the light "Olive Garden" palette (parchment, olive, sage, olivewood), New York serif headings and a faint
olive-sprig pattern behind each page. It is light-only by design.

## Requirements

- macOS with **Xcode 26** or later (the iOS 26 SDK; the app runs on iOS 18+).
- [XcodeGen](https://github.com/yonaskolb/XcodeGen): `brew install xcodegen`.
- **Home Assistant** reachable from your phone, with the `food_diary` integration installed and set up (see the repo's
  root README). The integration also needs an AI Task entity for photos, labels and typed food.
- Python 3, only for the mock server used by the UI tests and demos.

## Build

The Xcode project is generated from `project.yml` and isn't committed.

```sh
cd ios
cp Config/Local.xcconfig.example Config/Local.xcconfig   # then edit it (below)
xcodegen
open Food.xcodeproj
```

Run `xcodegen` again whenever you add or remove files, or change `project.yml` or the xcconfig.

### Signing with your own team

`Config/Base.xcconfig` (committed) holds placeholders. `Config/Local.xcconfig` (gitignored) overrides them:

| Setting | What it's for |
| --- | --- |
| `BUNDLE_ID_PREFIX` | A reverse-DNS prefix you own, e.g. `com.yourname`. The app is `<prefix>.food`; the widgets and the share extension are `.food.widgets` and `.food.share`. The App Group (`group.<prefix>.food`) and the keychain group (`<prefix>.food.shared`) come from it too. |
| `DEVELOPMENT_TEAM` | Your 10-character team id (Xcode → Settings → Accounts). |
| `HA_CLIENT_ID` | Optional. Your own OAuth client page (see below). |
| `HA_DEFAULT_SERVER` | Optional. The address the sign-in screen suggests. |

With automatic signing, Xcode registers the App Group, the keychain group and the HealthKit capability for you on first
build to a device. HealthKit background delivery needs a paid developer account.

Simulator builds need no team. For a build with no signing at all (as CI does), pass `CODE_SIGNING_ALLOWED=NO`. The app
works like that, but the share extension can't see the app's sign-in without the shared keychain group, so it shows "Sign
in to Food first".

## Signing in: the OAuth client_id

The app signs in with Home Assistant's own login page (`ASWebAuthenticationSession`) and the redirect
`fooddiary://auth`. Home Assistant only allows a redirect to a custom scheme if the app's `client_id` is a URL whose
page carries a matching link:

```html
<link rel="redirect_uri" href="fooddiary://auth">
```

By default the app uses `https://haydengriffin.github.io/ha-food-diary/auth/`, which is `docs/auth/index.html` in this
repo published with GitHub Pages. That works for anyone: the page only names the redirect, and your login and tokens
never leave your Home Assistant.

If you fork the app, publish your own copy of `docs/auth/index.html` and set `HA_CLIENT_ID` in `Config/Local.xcconfig`.
If you change the URL scheme, change it in `project.yml`, `Shared/Config.swift` and the page's `redirect_uri` together.

Home Assistant must be able to fetch the client page when you sign in, so it needs internet access at that moment.

## Sending Apple Health activity to Home Assistant (optional)

Settings → "Send activity to Home Assistant" takes a webhook id. When one is set, the app posts steps, distance, active
energy, exercise minutes, stand hours, ring goals and the last three nights of sleep to `/api/webhook/<id>` whenever
Health has new data, at most every 10 minutes. Leave it empty to keep that data on the phone. Point the webhook at an
automation of your own. The body is documented in `Food/Health.swift` (`sendActivity`).

## Home Assistant services used

The app calls only `food_diary.*` services, through `POST /api/services/food_diary/<service>?return_response`:

| Area | Services |
| --- | --- |
| The diary | `get_day`, `get_history`, `get_recent`, `log_food`, `update_food`, `delete_food`, `set_goals`, `set_photo` |
| Working things out | `estimate` (kinds `photo`, `label`, `barcode`, `text`) |
| Saved meals | `save_meal`, `update_saved_meal`, `delete_saved_meal` |
| Copying | `copy_day`, `undo_copy` |
| Looking back | `get_week_review` |

It also uses `/auth/authorize` and `/auth/token` (sign-in and refresh), `GET` on `/api/food_diary/photo/…` for the
user's own photos, and, only if you set one, `POST /api/webhook/<id>`. The integration's recipe and meal-plan services
aren't used by the app.

## Architecture

```
ios/
├── project.yml            XcodeGen spec: four targets, one scheme
├── Config/                Base.xcconfig (committed) + Local.xcconfig (yours, gitignored)
├── Shared/                Compiled into the app and both extensions
│   ├── Config.swift       Bundle prefix, App Group, client_id, dates and meal names
│   ├── HAClient.swift     OAuth sign-in, token refresh, food_diary calls, errors in plain words
│   ├── Keychain.swift     The session, in a keychain group the extensions share
│   ├── FoodAPI.swift      The day, history, logging, goals; the widgets' snapshot; deep-link routes
│   ├── DiaryAPI.swift     Saved meals, week review, photos, copying days
│   ├── Models.swift       Entry, Day, Goals, Estimate, RecentFood, number formatting
│   ├── DiaryModels.swift  SavedMeal, Recent, WeekReview
│   ├── Intents.swift      App Intents for Siri, Shortcuts, widgets and controls
│   └── Theme.swift, Rings.swift, Pattern.swift, Components.swift   The design system
├── Food/                  The app
│   ├── AppModel.swift     One @Observable model: the day on screen, caching, the outbox, Undo toasts, the sprig
│   ├── TodayView.swift    Today: week strip, summary card, meals, the Add bar
│   ├── AddFlowView.swift, AddHome.swift, DraftView.swift, MultiFood.swift   The Add sheet and its steps
│   ├── EntrySheet.swift, CopySheet.swift, UsualRows.swift
│   ├── Month*.swift, DayDetail.swift, ReviewViews.swift, Streak.swift, SleepInsight.swift
│   ├── Health.swift       HealthKit both ways, background delivery
│   ├── Reminders.swift, ProteinNudge.swift, Outbox.swift
│   └── SettingsView.swift, SignInView.swift
├── FoodWidgets/           WidgetKit: calories-left widget, Control Center controls
├── FoodShare/             Share extension: log a shared photo
├── FoodUITests/           UI tests against the mock, and the screenshot run
└── tools/mock_ha.py       A stand-in Home Assistant with a demo diary
```

Some notes on how it fits together:

- **Home Assistant holds all the data.** The app caches a few things in the App Group's `UserDefaults` so it opens
  instantly and works without signal: today as last seen, the week strip's numbers, the outbox, portion memory and
  on-phone answers.
- **One session, three processes.** The app, the widgets and the share extension share the Home Assistant session
  through the keychain group. Any of them may refresh the token; the others pick up the newer one.
- **Undo everywhere.** Every change shows a toast with Undo, which reverses it on Home Assistant (a delete is undone by
  logging the entry back exactly as it was).
- **Offline.** A `log_food` that never left the phone (no connection) is kept and retried in order. Anything that
  reached Home Assistant and failed is shown with Try again and Remove; it is never silently dropped.

## Tests and the mock server

`tools/mock_ha.py` is a small Home Assistant stand-in that implements the `food_diary` services the app uses, with a
generic demo diary: today's four meals, two months of history with a growing streak, usuals, saved meals and a week in
review. Debug builds can sign straight into it with launch arguments, skipping the web sign-in.

```sh
cd ios
python3 tools/mock_ha.py 8811 &
xcodebuild -project Food.xcodeproj -scheme Food \
  -destination 'platform=iOS Simulator,name=iPhone 17 Pro' \
  -only-testing:FoodUITests/FoodUITests test
```

Screenshots from each step land in `/tmp/food-shots`. To use another port, pass `TEST_RUNNER_MOCK_PORT=<port>` (and
`TEST_RUNNER_SHOTS_DIR=<dir>`) to `xcodebuild`.

- **Run the app on the demo diary yourself.** Edit the scheme's launch arguments to
  `-mockSignIn -server http://localhost:8811`.
- **Other launch arguments (Debug only).**
  - `-resetLocal` forgets on-phone answers.
  - `-showReview` and `-showRecap` show the week and month cards on any day.
  - `-celebrate` shows the morning-after moment.
  - `-fakeActivity` and `-fakeSleep` stand in for Apple Health.
- **Mock hooks.**
  - `POST /outage {"on": true}` makes `food_diary` answer 599, which a Debug build treats as offline.
  - `POST /reset` starts again from the demo diary.
- **Demo photos.** Drop JPEGs named after a food's slug into `tools/demo-photos/`
  (e.g. `miso-salmon-with-sesame-greens.jpg`) and the mock serves them as that food's picture. Without them the app
  draws its own tiles.

### Marketing screenshots

`FoodUITests/ScreenshotTests.swift` walks the main screens on the demo diary. It is skipped unless given a folder:

```sh
xcrun simctl status_bar booted override --time 9:41 --batteryState charged --batteryLevel 100 \
  --cellularBars 4 --wifiBars 3 --dataNetwork wifi
TEST_RUNNER_MARKETING_DIR=$PWD/../docs/screenshots/raw xcodebuild -project Food.xcodeproj -scheme Food \
  -destination 'platform=iOS Simulator,name=iPhone 17 Pro' -only-testing:FoodUITests/ScreenshotTests test
```

## Privacy

The app talks only to your Home Assistant (and, for barcode product photos, the image URLs Open Food Facts gives). It
has no analytics, no third-party SDKs and no AI keys: photos and text are worked out by your Home Assistant's AI Task
entity through the integration.
