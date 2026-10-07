import UserNotifications

/// Gentle nudges, if turned on: a reminder when a meal still isn't in by its time (breakfast 11, lunch 3, dinner 9),
/// with the usual for that meal as a button on the notification; and a cheer the morning after a day on target. Each meal's
/// reminder is taken back as soon as the app (or a background wake) sees that meal is in.
@MainActor
final class Reminders {
    static let shared = Reminders()
    static let addUsual = "ADD_USUAL"
    static let times: [Meal: Int] = [.breakfast: 11, .lunch: 15, .dinner: 20]
    private let center = UNUserNotificationCenter.current()
    private let defaults = AppConfig.shared

    /// The one-time question on Today has been answered.
    var asked: Bool { defaults.bool(forKey: "remindersAsked") }
    func isOn(_ m: Meal) -> Bool { (defaults.stringArray(forKey: "reminders") ?? []).contains(m.rawValue) }
    var celebrate: Bool { defaults.bool(forKey: "celebrate") }

    /// "Turn on" from Today: all three meals and the cheers, after iOS says yes.
    func turnOnAll() async -> Bool {
        defaults.set(true, forKey: "remindersAsked")
        guard await permission() else { return false }
        defaults.set(Meal.allCases.filter { Self.times[$0] != nil }.map(\.rawValue), forKey: "reminders")
        defaults.set(true, forKey: "celebrate")
        ProteinNudge.set(true)
        return true
    }

    func notNow() { defaults.set(true, forKey: "remindersAsked") }

    /// One meal's reminder on or off. False when iOS has notifications off for Food.
    func set(_ m: Meal, _ on: Bool) async -> Bool {
        defaults.set(true, forKey: "remindersAsked")
        if on, !(await permission()) { return false }
        var all = Set(defaults.stringArray(forKey: "reminders") ?? [])
        if on { all.insert(m.rawValue) } else { all.remove(m.rawValue); cancel(m, days: 0..<8) }
        defaults.set(Array(all), forKey: "reminders")
        return true
    }

    func setProtein(_ on: Bool) async -> Bool {
        defaults.set(true, forKey: "remindersAsked")
        if on, !(await permission()) { return false }
        ProteinNudge.set(on)
        return true
    }

    func setCelebrate(_ on: Bool) async -> Bool {
        defaults.set(true, forKey: "remindersAsked")
        if on, !(await permission()) { return false }
        defaults.set(on, forKey: "celebrate")
        return true
    }

    private func permission() async -> Bool {
        (try? await center.requestAuthorization(options: [.alert, .sound])) ?? false
    }

    // ---------- meal reminders ----------

    /// The next week of reminders for each meal they have on; today's left out for meals already in.
    func update(today: Day, usuals: [String: [RecentFood]], recent: [RecentFood]) {
        var categories = Set<UNNotificationCategory>()
        if let protein = ProteinNudge.update(today: today, recent: recent) { categories.insert(protein) }
        for (meal, hour) in Self.times {
            guard isOn(meal) else { continue }
            let usual = usuals[meal.rawValue]?.first
            if let usual {
                defaults.set(usual.name, forKey: "reminderUsual-\(meal.rawValue)")
                let add = UNNotificationAction(identifier: Self.addUsual, title: "Add \(usual.name)", options: [])
                let open = UNNotificationAction(identifier: "OPEN", title: "Something else", options: [.foreground])
                categories.insert(UNNotificationCategory(identifier: "REMIND-\(meal.rawValue)", actions: [add, open], intentIdentifiers: []))
            }
            cancel(meal, days: 0..<8)
            for offset in 0..<7 {
                guard let when = Calendar.current.date(bySettingHour: hour, minute: 0, second: 0, of: date(offset)), when > Date() else { continue }
                if offset == 0 && !today.entries(meal).isEmpty { continue }
                let c = UNMutableNotificationContent()
                c.title = "\(meal.single) isn't in your diary yet"
                c.body = usual.map { "Had your usual? \($0.name), \(Fmt.kcal($0.kcalEach)) kcal." } ?? "Add it when you have a moment."
                c.categoryIdentifier = usual == nil ? "" : "REMIND-\(meal.rawValue)"
                c.userInfo = ["date": day(offset), "meal": meal.rawValue]
                c.interruptionLevel = .passive  // it waits in Notification Centre; no buzz
                let comps = Calendar.current.dateComponents([.year, .month, .day, .hour, .minute], from: when)
                center.add(UNNotificationRequest(identifier: id(meal, day(offset)), content: c, trigger: UNCalendarNotificationTrigger(dateMatching: comps, repeats: false)))
            }
        }
        center.setNotificationCategories(categories)
    }

    private func cancel(_ meal: Meal, days: Range<Int>) {
        center.removePendingNotificationRequests(withIdentifiers: days.map { id(meal, day($0)) })
    }

    /// The notification's "Add …" button: their usual for that meal, on that day, without opening the app.
    func addUsual(meal: Meal, on date: String) async {
        guard let name = defaults.string(forKey: "reminderUsual-\(meal.rawValue)"), let r = try? await FoodAPI.recentAll(),
              let f = ((r.usuals[meal.rawValue] ?? []) + r.foods).first(where: { $0.name.lowercased() == name.lowercased() }) else { return }
        _ = try? await FoodAPI.logAgain(f, meal: meal, date: date)
    }

    // ---------- background ----------

    /// From a background wake: take back today's reminders for meals that are in, and in the morning cheer yesterday if it
    /// grew a leaf.
    func checkDiary() async {
        guard (Self.times.keys.contains { isOn($0) }) || celebrate || ProteinNudge.isOn, let today = try? await FoodAPI.day(Date().ymd) else { return }
        for meal in Self.times.keys where isOn(meal) && !today.entries(meal).isEmpty { cancel(meal, days: 0..<1) }
        ProteinNudge.check(today: today)
        await cheerYesterday()
    }

    /// Yesterday's moment was seen in the app: no notification for it.
    func markCheered() {
        if let y = Calendar.current.date(byAdding: .day, value: -1, to: Date())?.ymd { defaults.set(y, forKey: "cheered") }
    }

    /// Once, between 6 am and noon, the morning after a day on target: "Yesterday grew a leaf".
    func cheerYesterday() async {
        let hour = Calendar.current.component(.hour, from: Date())
        guard celebrate, (6..<12).contains(hour), let yesterday = Calendar.current.date(byAdding: .day, value: -1, to: Date())?.ymd,
              defaults.string(forKey: "cheered") != yesterday, let h = try? await FoodAPI.history(days: 60, through: yesterday) else { return }
        let room = defaults.bool(forKey: "exerciseCounts") ? await HealthSync.shared.activeKcal(days: 61) : [:]
        let n = Streak.count(h.days, goal: h.goals.kcal, room: room)
        guard n > 0 else { return }
        defaults.set(yesterday, forKey: "cheered")
        let c = UNMutableNotificationContent()
        c.title = "Yesterday grew a leaf"
        c.body = "\(n) day\(n == 1 ? "" : "s") growing."
        try? await center.add(UNNotificationRequest(identifier: "cheer-\(yesterday)", content: c, trigger: nil))
    }

    private func id(_ meal: Meal, _ day: String) -> String { "remind-\(meal.rawValue)-\(day)" }
    private func date(_ offset: Int) -> Date { Calendar.current.date(byAdding: .day, value: offset, to: Date()) ?? Date() }
    private func day(_ offset: Int) -> String { date(offset).ymd }
}
