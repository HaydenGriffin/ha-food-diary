import XCTest

/// The mock Home Assistant and the screenshot folder. Parallel runs set TEST_RUNNER_MOCK_PORT / TEST_RUNNER_SHOTS_DIR on
/// xcodebuild (each its own mock_ha.py port and simulator); otherwise :8811 and /tmp/food-shots.
let mockBase = "http://localhost:\(ProcessInfo.processInfo.environment["MOCK_PORT"] ?? "8811")"
private let shotsDir = ProcessInfo.processInfo.environment["SHOTS_DIR"] ?? "/tmp/food-shots"
/// Every request the mock saw (its own file per port, so parallel runs don't read each other's).
private let mockLogPath = ProcessInfo.processInfo.environment["MOCK_PORT"].flatMap { $0 == "8811" ? nil : "/tmp/mock_ha-\($0).log" } ?? "/tmp/mock_ha.log"

/// Pictures for the camera steps (the simulator has no camera): drawn once, as the app reads them by path.
enum TestImages {
    static let meal = "/tmp/food-oss-meal.jpg"
    static let label = "/tmp/food-oss-label.jpg"

    static func make() {
        for (path, colors) in [(meal, [UIColor(red: 0.62, green: 0.30, blue: 0.16, alpha: 1), UIColor(red: 0.95, green: 0.86, blue: 0.62, alpha: 1)]),
                               (label, [UIColor.white, UIColor.darkGray])] where !FileManager.default.fileExists(atPath: path) {
            let img = UIGraphicsImageRenderer(size: CGSize(width: 800, height: 600)).image { ctx in
                colors[1].setFill(); ctx.fill(CGRect(x: 0, y: 0, width: 800, height: 600))
                colors[0].setFill(); ctx.cgContext.fillEllipse(in: CGRect(x: 200, y: 100, width: 400, height: 400))
            }
            try? img.jpegData(compressionQuality: 0.8)?.write(to: URL(fileURLWithPath: path))
        }
    }
}

/// POSTs to the mock directly (to reset it, set things up, or cut it off).
func mockPost(_ test: XCTestCase, _ path: String, _ body: [String: Any] = [:]) {
    var r = URLRequest(url: URL(string: "\(mockBase)\(path)")!)
    r.httpMethod = "POST"
    r.setValue("application/json", forHTTPHeaderField: "Content-Type")
    r.httpBody = try? JSONSerialization.data(withJSONObject: body)
    let done = test.expectation(description: path)
    URLSession.shared.dataTask(with: r) { _, _, _ in done.fulfill() }.resume()
    test.wait(for: [done], timeout: 5)
}

/// Drives the app against tools/mock_ha.py and saves a screenshot of each step to /tmp/food-shots.
final class FoodUITests: XCTestCase {
    var app: XCUIApplication!

    override func setUp() {
        continueAfterFailure = false
        try? FileManager.default.createDirectory(atPath: shotsDir, withIntermediateDirectories: true)
        TestImages.make()
        app = XCUIApplication()
        app.launchArguments = ["-mockSignIn", "-server", mockBase, "-resetLocal"]
        mockPost(self, "/reset")
    }

    private func launch(image: String? = nil) {
        if let image { app.launchArguments += ["-testImage", image] }
        app.launch()
        XCTAssertTrue(app.navigationBars["Today"].waitForExistence(timeout: 15), "Today screen")
    }

    private func shot(_ name: String) {
        try? XCUIScreen.main.screenshot().pngRepresentation.write(to: URL(fileURLWithPath: "\(shotsDir)/\(name).png"))
    }

    private func tap(_ e: XCUIElement, _ what: String, timeout: TimeInterval = 10) {
        XCTAssertTrue(e.waitForExistence(timeout: timeout), what)
        e.tap()
    }

    /// Any element whose label starts with this (cards and rows read as one element to VoiceOver).
    private func starting(_ prefix: String) -> XCUIElement {
        app.descendants(matching: .any).matching(NSPredicate(format: "label BEGINSWITH %@", prefix)).firstMatch
    }

    private func button(startingWith prefix: String) -> XCUIElement {
        app.buttons.matching(NSPredicate(format: "label BEGINSWITH %@", prefix)).firstMatch
    }

    private func addButton() -> XCUIElement { button(startingWith: "Add to") }

    /// The day's card swiped: left for the day after, right for the day before.
    private func swipeDay(next: Bool) {
        let card = app.otherElements.matching(NSPredicate(format: "label CONTAINS 'calories left' OR label CONTAINS 'calories over'")).firstMatch
        XCTAssertTrue(card.waitForExistence(timeout: 10), "the day's card")
        if next { card.swipeLeft() } else { card.swipeRight() }
    }

    /// A day in the week strip, by how far it is from today.
    private func stripDay(_ offset: Int) -> XCUIElement {
        let d = Calendar.current.date(byAdding: .day, value: offset, to: Date())!
        return app.buttons.matching(NSPredicate(format: "label CONTAINS %@", d.formatted(.dateTime.weekday(.wide).day().month()))).firstMatch
    }

    /// Scroll down Today until it's on screen (offers and Fits come after the meals).
    private func scrollDown(to e: XCUIElement, tries: Int = 6) {
        for _ in 0..<tries where !(e.exists && e.isHittable) { app.swipeUp() }
    }

    /// Short drags until `e` sits in the middle of the screen, clear of the toolbar and the bottom buttons.
    private func scrollToMiddle(_ e: XCUIElement, tries: Int = 10) {
        let w = app.windows.firstMatch, h = w.frame.height
        func at(_ y: Double) -> XCUICoordinate { w.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: y)) }
        for _ in 0..<tries {
            let up = !e.exists || e.frame.minY < h * 0.15, down = e.exists && e.frame.maxY > h * 0.75
            guard up || down else { return }
            at(up ? 0.4 : 0.7).press(forDuration: 0.05, thenDragTo: at(up ? 0.65 : 0.45), withVelocity: .slow, thenHoldForDuration: 0.2)
        }
    }

    /// Drags the list until the element sits clear of the Add bar at the bottom (a swipe there would hit the bar).
    private func clearOfBar(_ e: XCUIElement) {
        let bottom = app.windows.firstMatch.frame.maxY - 190
        for _ in 0..<4 where e.exists && e.frame.maxY > bottom {
            app.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.72)).press(forDuration: 0.05, thenDragTo: app.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)))
        }
    }

    /// The menu under the day's title (another day, copying).
    private func openDayMenu(_ title: String) {
        tap(app.navigationBars.buttons.matching(NSPredicate(format: "label BEGINSWITH %@", title)).firstMatch, "the day's menu")
    }

    /// The search-or-type box at the bottom of the Add sheet.
    private func composer() -> XCUIElement { app.descendants(matching: .any).matching(identifier: "composer").firstMatch }

    /// Add food on Today, then types `text` in the box (a "\n" at the end is Return).
    private func type(_ text: String) {
        tap(app.buttons["Add food"].firstMatch, "Add food on Today")
        let box = composer()
        XCTAssertTrue(box.waitForExistence(timeout: 5), "the box")
        sleep(1)  // the sheet is up
        box.tap()
        if !text.isEmpty { box.typeText(text) }
    }

    private func toast(_ text: String) -> Bool {
        app.staticTexts.containing(NSPredicate(format: "label CONTAINS %@", text)).firstMatch.waitForExistence(timeout: 10)
    }

    private func mockLog() -> String { (try? String(contentsOfFile: mockLogPath, encoding: .utf8)) ?? "" }

    private static let ymd: DateFormatter = { let f = DateFormatter(); f.dateFormat = "yyyy-MM-dd"; return f }()

    // ---------- adding ----------

    func test01_TodayAndTyping() {
        launch()
        shot("01-today")
        type("")
        XCTAssertTrue(app.staticTexts["You often have"].waitForExistence(timeout: 5), "usuals on the Add sheet")
        shot("02-add")
        composer().typeText("2 eggs on toast")
        XCTAssertTrue(app.buttons["Work out \u{201C}2 eggs on toast\u{201D}"].waitForExistence(timeout: 5), "work it out")
        shot("03-type")
        composer().typeText("\n")  // Return works it out
        XCTAssertTrue(starting("315 calories").waitForExistence(timeout: 10), "the draft: 315 kcal")
        shot("04-draft")
        tap(addButton(), "Add to …")
        XCTAssertTrue(toast("Added"), "toast")
        shot("05-logged")
        XCTAssertTrue(mockLog().contains("Fried eggs on toast"), "logged on Home Assistant")
    }

    func test02_MealPhotoWithNote() {
        launch(image: TestImages.meal)
        tap(app.buttons["Photo of a meal"].firstMatch, "the camera on Today")
        XCTAssertTrue(app.staticTexts["Anything to add?"].waitForExistence(timeout: 5), "note step")
        app.textFields.firstMatch.tap()
        app.textFields.firstMatch.typeText("only ate half")
        shot("06-meal-note")
        tap(app.buttons["Work it out"], "Work it out")
        XCTAssertTrue(starting("520 calories").waitForExistence(timeout: 10))
        tap(app.buttons["½"], "half portion")
        XCTAssertTrue(starting("260 calories").waitForExistence(timeout: 5), "half of 520")
        shot("07-meal-draft")
        tap(addButton(), "Add to …")
        XCTAssertTrue(toast("Added"))
    }

    func test03_LabelWithAmountAndGrams() {
        launch(image: TestImages.label)
        tap(app.buttons["Add food"].firstMatch, "Add food")
        tap(app.buttons["Photo of a label"], "photo of a label")
        XCTAssertTrue(app.staticTexts["How much did you eat?"].waitForExistence(timeout: 5))
        app.textFields.firstMatch.typeText("3 biscuits")
        tap(app.buttons["Work it out"], "Work it out")
        XCTAssertTrue(starting("215 calories").waitForExistence(timeout: 10), "label estimate")
        tap(button(startingWith: "More"), "more grams")
        shot("08-label-draft")
        tap(addButton(), "Add to …")
        XCTAssertTrue(toast("Added"))
    }

    func test04_BarcodeKnownAndUnknown() {
        launch(image: TestImages.label)
        tap(app.buttons["Scan a barcode"], "Scan button")
        let digits = app.textFields.firstMatch
        XCTAssertTrue(digits.waitForExistence(timeout: 5), "manual barcode in the simulator")  // no camera scanner in the simulator
        digits.tap(); digits.typeText("5010029000023")
        tap(app.buttons["Look it up"], "Look it up")
        tap(app.buttons["2 servings"], "2 servings chip")
        XCTAssertTrue(starting("272 calories").waitForExistence(timeout: 10), "75 g of cereal")
        shot("09-barcode-draft")
        tap(addButton(), "Add to …")
        XCTAssertTrue(toast("Added"))
        tap(app.buttons["Scan a barcode"], "Scan again")
        let d2 = app.textFields.firstMatch
        XCTAssertTrue(d2.waitForExistence(timeout: 5)); d2.tap(); d2.typeText("0001234567890")
        tap(app.buttons["Look it up"], "Look it up")
        tap(app.buttons["Work it out"], "Work it out")
        XCTAssertTrue(app.staticTexts["Not known yet"].waitForExistence(timeout: 10), "unknown barcode asks for the label")
        shot("10-barcode-unknown")
        tap(app.buttons["Photo of the label"], "label instead")
        XCTAssertTrue(app.staticTexts["How much did you eat?"].waitForExistence(timeout: 5))
    }

    func test05_SeveralFoodsAndYesterdaysMeal() {
        launch()
        XCTAssertTrue(app.buttons.matching(NSPredicate(format: "label CONTAINS 'growing'")).firstMatch.waitForExistence(timeout: 10), "the sprig on Today")
        type("porridge, a banana, a latte\n")
        let add = button(startingWith: "Add 3 to")
        XCTAssertTrue(add.waitForExistence(timeout: 10), "three foods to check")
        shot("11-several")
        tap(add, "add all three")
        XCTAssertTrue(toast("Added 3 things"), "added")
        for _ in 0..<3 { app.swipeDown() }
        swipeDay(next: true)  // tomorrow: its breakfast is empty, today's isn't
        XCTAssertTrue(app.navigationBars["Tomorrow"].waitForExistence(timeout: 5))
        let same = button(startingWith: "Add the same breakfast as today")
        for _ in 0..<4 where !same.isHittable { app.swipeUp() }
        clearOfBar(same)
        shot("12-same-as-yesterday")
        tap(same, "same as today")
        XCTAssertTrue(toast("Added today's breakfast"), "copied the meal")
    }

    // ---------- changing ----------

    func test06_AgainEditAndRemove() {
        launch()
        type("skyr")
        XCTAssertTrue(app.staticTexts["You've had"].waitForExistence(timeout: 5)); sleep(1)
        tap(button(startingWith: "Add Skyr with granola"), "again row")
        XCTAssertTrue(toast("Added Skyr"))
        XCTAssertTrue(starting("Added Skyr").waitForNonExistence(timeout: 12), "the message goes by itself")
        let row = button(startingWith: "Greek yogurt with berries")
        scrollToMiddle(row)
        tap(row, "entry row")
        XCTAssertTrue(app.buttons["Change numbers"].waitForExistence(timeout: 5), "the entry sheet")
        app.swipeUp()  // below the fold, under the sheet's own Done
        tap(app.buttons["Change numbers"], "change numbers")
        shot("13-entry-numbers")
        tap(app.buttons["Close"], "close")
        let yogurt = button(startingWith: "Greek yogurt with berries")
        XCTAssertTrue(yogurt.waitForExistence(timeout: 5))
        clearOfBar(yogurt)
        yogurt.swipeLeft()
        tap(app.buttons["Remove"], "swipe remove")
        XCTAssertTrue(toast("Removed"))
        shot("14-removed")
        tap(app.buttons["Undo"], "undo")
        sleep(2)
        XCTAssertTrue(button(startingWith: "Greek yogurt with berries").waitForExistence(timeout: 5), "back again")
    }

    func test07_SprigAndPortions() {
        launch()
        let sprig = app.buttons.matching(NSPredicate(format: "label CONTAINS 'growing' OR label BEGINSWITH 'A leaf'")).firstMatch
        XCTAssertTrue(sprig.waitForExistence(timeout: 10), "the growing sprig")
        sprig.tap()
        XCTAssertTrue(app.switches.matching(NSPredicate(format: "label BEGINSWITH 'Count exercise'")).firstMatch.waitForExistence(timeout: 5), "exercise switch")
        shot("15-sprig-sheet")
        tap(app.buttons["Done"], "done")
        let row = button(startingWith: "Greek yogurt with berries")
        for _ in 0..<3 where !row.isHittable { app.swipeUp() }
        clearOfBar(row)
        row.swipeRight()
        tap(app.buttons["2 portions"], "two portions")  // the four places are always ½ · 1 · 1½ · 2
        XCTAssertTrue(toast("Greek yogurt with berries and honey: 2 portions"), "changed with a swipe")
        shot("16-portions")
    }

    func test08_PhotoOnAnEntry() {
        launch()
        let row = button(startingWith: "Miso salmon")
        for _ in 0..<4 where !row.isHittable { app.swipeUp() }
        tap(row, "dinner")
        XCTAssertTrue(app.buttons["Add a photo"].waitForExistence(timeout: 5), "a photo can go on any food")
        shot("17-entry")
    }

    // ---------- days ----------

    func test09_SettingsAndWeek() {
        launch()
        tap(app.buttons["Settings"], "settings")
        XCTAssertTrue(app.staticTexts["Daily goals"].waitForExistence(timeout: 5))
        shot("18-settings")
        let hook = app.textFields["Activity webhook id"]
        for _ in 0..<5 where !hook.isHittable { app.swipeUp() }
        tap(hook, "the webhook field")
        hook.typeText("demo_activity")
        tap(app.navigationBars.buttons["Done"], "done")
        XCTAssertTrue(app.navigationBars["Today"].waitForExistence(timeout: 5))
        swipeDay(next: false)
        XCTAssertTrue(app.navigationBars["Yesterday"].waitForExistence(timeout: 5), "a swipe across the card goes back a day")
        // a swipe across the week strip: the same weekday a week on (or today, when that's this week)
        let mondayToday = Calendar.current.component(.weekday, from: Date()) == 2
        let expected = mondayToday ? "Today" : Calendar.current.date(byAdding: .day, value: 6, to: Date())!.formatted(.dateTime.weekday(.wide).day().month(.abbreviated))
        let strip = stripDay(-1)
        XCTAssertTrue(strip.waitForExistence(timeout: 5), "yesterday in the strip")
        strip.swipeLeft()
        XCTAssertTrue(app.navigationBars[expected].waitForExistence(timeout: 8), "next week opens on \(expected)")
    }

    func test10_PlanAheadAndCopy() {
        launch()
        let fits = app.staticTexts.containing(NSPredicate(format: "label BEGINSWITH 'Fits in your'")).firstMatch
        for _ in 0..<3 where !fits.waitForExistence(timeout: 2) { app.swipeUp() }
        XCTAssertTrue(fits.exists, "what fits")
        for _ in 0..<3 { app.swipeDown() }
        swipeDay(next: true)
        XCTAssertTrue(app.navigationBars["Tomorrow"].waitForExistence(timeout: 5), "tomorrow opens")
        let addBreakfast = app.buttons["Add breakfast"]
        for _ in 0..<4 where !addBreakfast.exists { app.swipeUp() }
        tap(addBreakfast, "add to tomorrow's breakfast")
        tap(composer(), "the box")
        composer().typeText("porridge with banana\n")
        XCTAssertTrue(app.buttons["Add to tomorrow's breakfast"].waitForExistence(timeout: 10), "the button says where it goes")
        tap(app.buttons["Add to tomorrow's breakfast"], "add it")
        XCTAssertTrue(toast("Added"))
        let tomorrow = Self.ymd.string(from: Calendar.current.date(byAdding: .day, value: 1, to: Date())!)
        XCTAssertTrue(mockLog().contains("\"date\": \"\(tomorrow)\"") && mockLog().contains("\"meal\": \"breakfast\""), "logged on tomorrow's breakfast")
        sleep(1)
        openDayMenu("Tomorrow")
        tap(app.descendants(matching: .any).matching(NSPredicate(format: "label == 'Copy from today…'")).firstMatch, "copy from today")
        tap(app.buttons["Copy"], "confirm the copy")
        XCTAssertTrue(toast("Copied"), "copied today onto tomorrow")
    }

    func test11_CopyToOtherDays() {
        launch()
        openDayMenu("Today")
        tap(app.buttons["Copy this day to…"], "copy this day")
        XCTAssertTrue(app.staticTexts["To"].waitForExistence(timeout: 5), "the copy sheet")
        for n in [2, 3] {
            let d = Calendar.current.date(byAdding: .day, value: n, to: Date())!
            tap(button(startingWith: d.formatted(.dateTime.weekday(.wide).day().month(.wide))), "day +\(n)")
        }
        sleep(1)
        shot("19-copy-sheet")
        tap(app.buttons["Copy to 2 days"], "copy")
        XCTAssertTrue(toast("Copied to 2 days"), "copied")
        tap(app.buttons["Undo"], "undo")
        sleep(1)
        XCTAssertTrue(mockLog().contains("copy_day") && mockLog().contains("undo_copy"), "copied, then undone")
    }

    func test12_FoodPutInAheadAndProteinNudge() {
        mockPost(self, "/ahead_today")
        launch()
        if Date() < Calendar.current.date(bySettingHour: 18, minute: 0, second: 0, of: Date())! {  // dinner's still to come
            XCTAssertTrue(app.staticTexts["Includes 900 kcal planned"].waitForExistence(timeout: 10), "food put in ahead is shown apart")
            XCTAssertTrue(app.staticTexts["over with the plan"].exists, "over only because of what's still to come")
        }
        shot("20-ahead")
        tap(app.buttons["Settings"], "settings")
        let nudge = app.switches.matching(NSPredicate(format: "label BEGINSWITH 'Protein nudge'")).firstMatch
        for _ in 0..<6 where !nudge.isHittable { app.swipeUp() }
        XCTAssertTrue(nudge.exists, "the protein nudge can be turned on")
    }

    // ---------- saved meals and usuals ----------

    func test13_UsualsAndSavedMeal() {
        launch()
        swipeDay(next: true)  // tomorrow: breakfast is empty
        XCTAssertTrue(app.navigationBars["Tomorrow"].waitForExistence(timeout: 5))
        let usual = button(startingWith: "Add Overnight oats")
        for _ in 0..<4 where !usual.isHittable { app.swipeUp() }
        XCTAssertTrue(usual.waitForExistence(timeout: 5), "the usual in the empty breakfast")
        shot("21-usual")
        usual.tap()
        XCTAssertTrue(toast("Added Overnight oats"), "one tap adds it")
        let more = app.buttons["Add more to breakfast"].firstMatch
        for _ in 0..<4 where !more.exists { app.swipeUp() }
        tap(more, "add more to breakfast")
        tap(composer(), "the box")
        composer().typeText("skyr")
        tap(button(startingWith: "Add Skyr with granola again"), "again")
        let save = app.buttons["Save these together"]
        for _ in 0..<4 where !save.exists { app.swipeUp() }
        XCTAssertTrue(save.waitForExistence(timeout: 5), "two foods: save them as one")
        save.tap()
        tap(app.alerts.buttons["Save"], "save in the alert")
        XCTAssertTrue(toast("Saved Overnight oats"))
        tap(app.buttons["Add to tomorrow"], "add to tomorrow")
        XCTAssertTrue(app.staticTexts["Your meals"].waitForExistence(timeout: 5), "saved meals in Add")
    }

    func test14_SearchAndSavedMeals() {
        launch()
        tap(app.buttons["Add food"].firstMatch, "Add food")
        XCTAssertTrue(app.staticTexts["Your meals"].waitForExistence(timeout: 5))
        tap(app.buttons["More for Gym day lunch"], "the saved meal's menu")
        tap(app.buttons["Rename…"], "rename")
        let name = app.alerts.textFields.firstMatch
        XCTAssertTrue(name.waitForExistence(timeout: 5))
        name.tap()
        name.typeText(String(repeating: XCUIKeyboardKey.delete.rawValue, count: 30) + "Training day lunch")
        tap(app.alerts.buttons["Save"], "save the name")
        XCTAssertTrue(toast("Renamed to Training day lunch"), "renamed")
        let search = composer()
        tap(search, "the box")
        search.typeText("sky")
        XCTAssertTrue(app.staticTexts["You've had"].waitForExistence(timeout: 5), "foods in the results")
        XCTAssertTrue(app.buttons["Work out \u{201C}sky\u{201D}"].exists, "something new is always there")
        shot("22-search")
        tap(button(startingWith: "Add Skyr with granola again"), "add from search")
        XCTAssertTrue(toast("Added Skyr"), "added from search")
    }

    func test15_OfflineAddsWait() {
        launch()
        mockPost(self, "/outage", ["on": true])
        type("skyr")
        tap(button(startingWith: "Add Skyr with granola again"), "again row")
        XCTAssertTrue(toast("waiting for signal"), "kept on the phone")
        let waiting = starting("Skyr with granola, 260 calories, waiting")
        for _ in 0..<5 where !waiting.exists { app.swipeUp() }
        XCTAssertTrue(waiting.waitForExistence(timeout: 5), "shown as waiting")
        shot("23-waiting")
        mockPost(self, "/outage", ["on": false])
        XCUIDevice.shared.press(.home)
        sleep(1)
        app.activate()
        XCTAssertTrue(toast("Back online"), "sent once Home Assistant is back")
        XCTAssertTrue(mockLog().contains("Skyr with granola"), "it reached Home Assistant")
    }

    // ---------- looking back ----------

    func test16_WeekLookBackAndSleep() {
        app.launchArguments += ["-showReview", "-fakeSleep"]
        launch()
        let card = app.staticTexts.matching(NSPredicate(format: "label ENDSWITH 'days on target'")).firstMatch
        sleep(2)
        scrollDown(to: card)
        XCTAssertTrue(card.waitForExistence(timeout: 10), "last week on Today")
        shot("24-review-card")
        tap(app.buttons["See last week"], "see last week")
        XCTAssertTrue(starting("Closest to your goal").waitForExistence(timeout: 5), "a good moment from the week")
        let sleepLine = starting("Sleep and food")
        for _ in 0..<3 where !sleepLine.exists { app.swipeUp() }
        XCTAssertTrue(sleepLine.waitForExistence(timeout: 5), "the sleep insight")
        shot("25-review")
        tap(app.buttons["Close"], "close")
        tap(app.buttons["Hide last week"], "hide")
        XCTAssertTrue(card.waitForNonExistence(timeout: 5), "hidden")
    }

    func test17_MonthRemindersAndCheer() {
        app.launchArguments += ["-celebrate", "-fakeActivity"]
        launch()
        XCTAssertTrue(app.staticTexts["Yesterday grew a leaf"].waitForExistence(timeout: 10), "the morning-after moment")
        shot("26-celebration")
        tap(app.buttons["Lovely"], "lovely")
        let notNow = app.buttons["Not now"].firstMatch
        for _ in 0..<6 where !notNow.isHittable { app.swipeUp() }
        if notNow.exists { notNow.tap() }  // Apple Health
        let offer = app.staticTexts["Meal reminders"]
        for _ in 0..<4 where !offer.exists { app.swipeUp() }
        XCTAssertTrue(offer.waitForExistence(timeout: 5), "reminders offered once")
        let turnOn = app.buttons["Turn on"]
        for _ in 0..<3 where !turnOn.isHittable { app.swipeUp() }
        tap(turnOn, "turn reminders on")
        allowSystemAlert()
        XCTAssertTrue(app.staticTexts["Meal reminders"].waitForNonExistence(timeout: 8), "answered: the offer goes")
        openDayMenu("Today")
        tap(app.collectionViews.buttons["This month"], "this month, from the day's menu")
        let month = Date().formatted(.dateTime.month(.wide))
        XCTAssertTrue(app.navigationBars[month].waitForExistence(timeout: 10), "titled with the month")
        let summary = monthSummary(month)
        XCTAssertTrue(summary.waitForExistence(timeout: 10), "the month in one glance")
        XCTAssertTrue(summary.label.hasPrefix("\(month) so far: on average ") && summary.label.contains("days logged"), summary.label)
        XCTAssertTrue(app.buttons["Open this day"].waitForExistence(timeout: 10), "today's detail")
        shot("27-month")
        let week = app.buttons.matching(NSPredicate(format: "label BEGINSWITH 'Week of' AND label CONTAINS %@", month)).firstMatch
        XCTAssertTrue(week.exists, "a week's average")
        week.tap()
        XCTAssertTrue(app.staticTexts["A day on average"].waitForExistence(timeout: 5), "the week's detail")
        // last month, a page to the left: its own numbers and its recap
        app.swipeRight()
        let last = Calendar.current.date(byAdding: .month, value: -1, to: Date())!
        let lastName = last.formatted(.dateTime.month(.wide))
        let lastTitle = Calendar.current.isDate(last, equalTo: Date(), toGranularity: .year) ? lastName : last.formatted(.dateTime.month(.wide).year())
        XCTAssertTrue(app.navigationBars[lastTitle].waitForExistence(timeout: 5), "turned to last month")
        XCTAssertTrue(monthSummary(lastName).waitForExistence(timeout: 10), "last month in one glance")
        XCTAssertTrue(app.buttons["See the recap"].exists, "a month that's over has its recap")
        shot("28-month-past")
        tap(app.buttons["Next month"], "back with the arrow")
        XCTAssertTrue(app.navigationBars[month].waitForExistence(timeout: 5), "this month again")
        tap(app.buttons["Photos"], "photos")
        sleep(1)
        shot("29-month-photos")
    }

    func test18_MonthRecap() {
        app.launchArguments += ["-showRecap"]
        launch()
        let offer = app.buttons.matching(NSPredicate(format: "label BEGINSWITH 'Your ' AND label CONTAINS 'in food'")).firstMatch
        sleep(2)
        scrollDown(to: offer)
        XCTAssertTrue(offer.waitForExistence(timeout: 10), "the recap offered on Today")
        offer.tap()
        let first = app.descendants(matching: .any).matching(NSPredicate(format: "label CONTAINS 'days of food' OR label CONTAINS 'On an average day'")).firstMatch
        XCTAssertTrue(first.waitForExistence(timeout: 15), "the cards")
        sleep(1)
        shot("30-recap")
        app.swipeLeft(); sleep(1)
        shot("31-recap-2")
    }

    // ---------- the share extension ----------

    func test19_SharePhoto() {
        app.launchArguments += ["-shareImage", TestImages.meal]
        app.launch()
        let food = app.descendants(matching: .any).matching(NSPredicate(format: "label == 'Food' AND elementType != %d AND elementType != %d",
                                                                          XCUIElement.ElementType.application.rawValue, XCUIElement.ElementType.image.rawValue)).firstMatch
        XCTAssertTrue(food.waitForExistence(timeout: 20), "Food in the share sheet")
        sleep(1)
        food.tap()
        let work = app.descendants(matching: .any).matching(NSPredicate(format: "label == 'Work it out'")).firstMatch
        if app.descendants(matching: .any).matching(NSPredicate(format: "label == 'Sign in to Food first'")).firstMatch.waitForExistence(timeout: 5) {
            XCTFail("the share extension is signed out: it reads the app's sign-in from the shared keychain group, which needs a signed build")
            return
        }
        tap(work, "work it out")
        let add = app.descendants(matching: .any).matching(NSPredicate(format: "label BEGINSWITH 'Add to'")).firstMatch
        tap(add, "add it", timeout: 10)
        XCTAssertTrue(starting("Added Beef chilli").waitForExistence(timeout: 10), "added from the share sheet")
        shot("32-share-added")
    }

    // ---------- helpers ----------

    /// The Month page's summary for one month (the pages either side are loaded too).
    private func monthSummary(_ month: String) -> XCUIElement {
        app.descendants(matching: .any).matching(NSPredicate(format: "identifier == 'month-summary' AND label BEGINSWITH %@", month)).firstMatch
    }

    /// iOS's own permission question (notifications): allow it.
    private func allowSystemAlert() {
        let springboard = XCUIApplication(bundleIdentifier: "com.apple.springboard")
        for label in ["Allow", "Allow Full Access"] where springboard.buttons[label].waitForExistence(timeout: 4) {
            springboard.buttons[label].tap(); return
        }
    }
}
