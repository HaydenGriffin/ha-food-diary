# Showcase images

Renders the README images in `docs/images/` from the simulator screenshots in `docs/screenshots/raw/`
(captured by `ios/FoodUITests/ScreenshotTests.swift` against the demo mock server).

```bash
cd tools/showcase
npm install playwright && npx playwright install chromium
node render.js ../..
```

Uses Apple's New York and SF fonts from `/System/Library/Fonts`, so run it on a Mac.
