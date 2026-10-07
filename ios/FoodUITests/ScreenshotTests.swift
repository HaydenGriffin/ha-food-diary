import XCTest

/// Marketing screenshots against the mock's demo diary. Skipped unless a folder is given:
///
///     TEST_RUNNER_MARKETING_DIR=/path/to/folder xcodebuild … -only-testing:FoodUITests/ScreenshotTests test
///
/// Set the simulator's status bar first (`xcrun simctl status_bar <device> override --time 9:41 …`).
final class ScreenshotTests: XCTestCase {
    private var app: XCUIApplication!
    private var dir = ""

    override func setUpWithError() throws {
        guard let d = ProcessInfo.processInfo.environment["MARKETING_DIR"], !d.isEmpty else { throw XCTSkip("Set TEST_RUNNER_MARKETING_DIR to take marketing screenshots") }
        dir = d
        try? FileManager.default.createDirectory(atPath: dir, withIntermediateDirectories: true)
        continueAfterFailure = true
        TestImages.make()
        mockPost(self, "/reset")
        app = XCUIApplication()
        app.launchArguments = ["-mockSignIn", "-server", mockBase, "-resetLocal", "-fakeActivity", "-fakeSleep"]
    }

    private func shot(_ name: String) {
        sleep(1)  // animations settle
        try? XCUIScreen.main.screenshot().pngRepresentation.write(to: URL(fileURLWithPath: "\(dir)/\(name).png"))
    }

    private func element(_ predicate: String, _ args: CVarArg...) -> XCUIElement {
        app.descendants(matching: .any).matching(NSPredicate(format: predicate, argumentArray: args)).firstMatch
    }

    /// A fresh simulator explains slide-to-type the first time the keyboard shows: put it away.
    private func tapBox(_ box: XCUIElement) {
        box.tap()
        let tip = app.buttons["Continue"]
        if tip.waitForExistence(timeout: 2) { tip.tap(); box.tap() }
    }

    private func launchToday(_ extra: [String] = []) {
        app.launchArguments += extra
        app.launch()
        XCTAssertTrue(app.navigationBars["Today"].waitForExistence(timeout: 15))
        sleep(2)  // pictures and the week strip
    }

    private func drag(from: Double, to: Double) {
        let w = app.windows.firstMatch
        w.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: from))
            .press(forDuration: 0.05, thenDragTo: w.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: to)), withVelocity: .slow, thenHoldForDuration: 0.3)
    }

    func test1_TodayAndAdding() {
        launchToday()
        shot("01-today")
        drag(from: 0.75, to: 0.3)
        shot("02-today-meals")
        drag(from: 0.3, to: 0.85)

        // the entry sheet
        let salmon = app.buttons.matching(NSPredicate(format: "label BEGINSWITH 'Miso salmon'")).firstMatch
        for _ in 0..<4 where !salmon.isHittable { app.swipeUp() }
        if salmon.waitForExistence(timeout: 5) { salmon.tap(); sleep(1); shot("06-entry-detail"); app.buttons["Close"].tap() }
        for _ in 0..<4 { app.swipeDown() }

        // the Add sheet
        app.buttons["Add food"].firstMatch.tap()
        XCTAssertTrue(app.staticTexts["You often have"].waitForExistence(timeout: 5))
        shot("03-add-food")
        let box = app.descendants(matching: .any).matching(identifier: "composer").firstMatch
        tapBox(box); box.typeText("sal")
        XCTAssertTrue(app.staticTexts["You've had"].waitForExistence(timeout: 5))
        shot("04-add-search")
        box.typeText(String(repeating: XCUIKeyboardKey.delete.rawValue, count: 3) + "2 eggs on toast\n")
        XCTAssertTrue(element("label BEGINSWITH %@", "315 calories").waitForExistence(timeout: 10))
        shot("05-add-check-it")
    }

    func test2_SeveralFoods() {
        launchToday()
        app.buttons["Add food"].firstMatch.tap()
        let box = app.descendants(matching: .any).matching(identifier: "composer").firstMatch
        XCTAssertTrue(box.waitForExistence(timeout: 5)); sleep(1)
        tapBox(box); box.typeText("porridge with blueberries, a banana, a flat white\n")
        XCTAssertTrue(element("label BEGINSWITH %@", "Add 3 to").waitForExistence(timeout: 10))
        shot("07-add-several-foods")
    }

    func test3_WeekReviewAndStreak() {
        launchToday(["-showReview"])
        let sprig = app.buttons.matching(NSPredicate(format: "label CONTAINS 'growing'")).firstMatch
        if sprig.waitForExistence(timeout: 5) {
            sprig.tap(); sleep(1); shot("08-streak")
            app.buttons["Done"].tap()
        }
        let card = app.staticTexts.matching(NSPredicate(format: "label ENDSWITH 'days on target'")).firstMatch
        for _ in 0..<6 where !(card.exists && card.isHittable) { app.swipeUp() }
        shot("09-week-review-card")
        app.buttons["See last week"].tap()
        XCTAssertTrue(element("label BEGINSWITH %@", "Closest to your goal").waitForExistence(timeout: 5))
        shot("10-week-review")
    }

    func test4_MonthAndRecap() {
        launchToday(["-showRecap"])
        app.buttons["This month"].firstMatch.tap()
        XCTAssertTrue(app.buttons["Open this day"].waitForExistence(timeout: 10))
        shot("11-month")
        let yesterday = Calendar.current.date(byAdding: .day, value: -1, to: Date())!
        let cell = app.buttons.matching(NSPredicate(format: "label BEGINSWITH %@", yesterday.formatted(.dateTime.weekday(.wide).day().month(.wide)))).firstMatch
        if cell.exists && cell.isHittable { cell.tap(); app.swipeUp(); shot("12-month-day") }
        app.buttons["Close"].tap()
        let offer = app.buttons.matching(NSPredicate(format: "label BEGINSWITH 'Your ' AND label CONTAINS 'in food'")).firstMatch
        for _ in 0..<6 where !(offer.exists && offer.isHittable) { app.swipeUp() }
        offer.tap()
        XCTAssertTrue(element("label CONTAINS 'days of food' OR label CONTAINS 'On an average day'").waitForExistence(timeout: 15))
        shot("13-month-recap")
        app.swipeLeft()
        shot("14-month-recap-leaves")
    }

    func test5_Settings() {
        launchToday()
        app.buttons["Settings"].tap()
        XCTAssertTrue(app.staticTexts["Daily goals"].waitForExistence(timeout: 5))
        shot("15-settings")
    }

    /// The Home Screen widget, added through Springboard (best effort: the flow differs between iOS versions).
    func test6_Widget() throws {
        launchToday()  // the widget's snapshot comes from the app
        XCUIDevice.shared.press(.home)
        let board = XCUIApplication(bundleIdentifier: "com.apple.springboard")
        sleep(1)
        board.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.62)).press(forDuration: 1.5)
        let edit = board.buttons["Edit"]
        if edit.waitForExistence(timeout: 3) {
            edit.tap()
            let add = board.buttons.matching(NSPredicate(format: "label CONTAINS[c] 'Add Widget'")).firstMatch
            if add.waitForExistence(timeout: 3) { add.tap() }
        } else {
            let plus = board.buttons.matching(NSPredicate(format: "label CONTAINS[c] 'Add Widget' OR label == 'Add'")).firstMatch
            if plus.waitForExistence(timeout: 3) { plus.tap() }
        }
        let search = board.searchFields.firstMatch
        guard search.waitForExistence(timeout: 5) else { throw XCTSkip("No widget gallery found on this iOS version") }
        search.tap(); search.typeText("Food")
        let food = board.descendants(matching: .any).matching(NSPredicate(format: "label == 'Food'")).element(boundBy: 1)
        let anyFood = food.exists ? food : board.cells.matching(NSPredicate(format: "label CONTAINS 'Food'")).firstMatch
        guard anyFood.waitForExistence(timeout: 5) else { throw XCTSkip("Food isn't in the widget gallery") }
        anyFood.tap()
        sleep(1)
        board.swipeLeft()  // the medium size, with Scan and Photo
        let addWidget = board.buttons.matching(NSPredicate(format: "label CONTAINS[c] 'Add Widget'")).firstMatch
        guard addWidget.waitForExistence(timeout: 5) else { throw XCTSkip("No Add Widget button") }
        addWidget.tap()
        sleep(2)
        let done = board.buttons["Done"]
        if done.waitForExistence(timeout: 3) { done.tap() }
        sleep(3)
        shot("16-widget-home-screen")
    }
}
