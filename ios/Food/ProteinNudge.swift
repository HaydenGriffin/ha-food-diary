import UserNotifications

/// The evening protein nudge, if they turn it on: at 7:30 pm, when they're still 15 g or more short of their protein and has
/// room in their calories, one quiet notification with the most protein-for-calories food they have often ("Skyr would do
/// it"), and that food one tap away. It's an offer: nothing is added unless they tap it.
@MainActor
enum ProteinNudge {
    static let add = "ADD_PROTEIN"
    static let category = "PROTEIN"
    static let hour = 19, minute = 30
    static let minGap = 15.0      // grams short before it's worth a nudge
    static let minRoom = 100.0    // kcal left: never suggest more food to someone already at their calories

    private static var defaults: UserDefaults { AppConfig.shared }
    private static var center: UNUserNotificationCenter { UNUserNotificationCenter.current() }
    static var isOn: Bool { defaults.bool(forKey: "proteinNudge") }
    static func set(_ on: Bool) { defaults.set(on, forKey: "proteinNudge"); if !on { cancel() } }

    /// Today's nudge, (re)worked out from the day as it stands; taken back once they're close enough.
    static func update(today: Day, recent: [RecentFood]) -> UNNotificationCategory? {
        cancel()
        guard isOn, today.date == Date().ymd, let when = time(), when > Date() else { return nil }
        let gap = today.goals.protein_g - today.totals.protein_g, room = today.goals.kcal - today.totals.kcal
        guard today.goals.protein_g > 0, gap >= minGap, room >= minRoom else { return nil }
        let pick = best(recent, room: room)
        let c = UNMutableNotificationContent()
        c.title = "\(Int(gap.rounded())) g protein to go"
        c.body = pick.map { "\($0.name) (\(Int($0.proteinEach.rounded())) g, \(Fmt.kcal($0.kcalEach)) kcal) would help." }
            ?? "Something with protein would close it today."
        c.interruptionLevel = .passive
        c.userInfo = ["date": today.date]
        var category: UNNotificationCategory?
        if let pick {
            defaults.set(pick.name, forKey: "proteinPick")
            c.categoryIdentifier = Self.category
            category = UNNotificationCategory(identifier: Self.category, actions: [
                UNNotificationAction(identifier: add, title: "Add \(pick.name)", options: []),
                UNNotificationAction(identifier: "OPEN", title: "Something else", options: [.foreground]),
            ], intentIdentifiers: [])
        }
        let comps = Calendar.current.dateComponents([.year, .month, .day, .hour, .minute], from: when)
        center.add(UNNotificationRequest(identifier: id, content: c, trigger: UNCalendarNotificationTrigger(dateMatching: comps, repeats: false)))
        return category
    }

    /// From a background wake: they've caught up on protein since, so the nudge isn't needed.
    static func check(today: Day) {
        if today.goals.protein_g - today.totals.protein_g < minGap { cancel() }
    }

    /// The notification's "Add …": the picked food as a snack today, without opening the app.
    static func addPick(on date: String) async {
        guard let name = defaults.string(forKey: "proteinPick"), let r = try? await FoodAPI.recentAll(),
              let f = r.foods.first(where: { $0.name.lowercased() == name.lowercased() }) else { return }
        _ = try? await FoodAPI.logAgain(f, meal: .snack, date: date)
    }

    /// Something they've had at least twice, with real protein in it, that fits in what's left: the most protein per kcal.
    static func best(_ foods: [RecentFood], room: Double) -> RecentFood? {
        foods.filter { $0.times >= 2 && $0.proteinEach >= 8 && $0.kcalEach > 0 && $0.kcalEach <= room }
            .max { $0.proteinEach / $0.kcalEach < $1.proteinEach / $1.kcalEach }
    }

    private static var id: String { "protein-\(Date().ymd)" }
    private static func cancel() { center.removePendingNotificationRequests(withIdentifiers: [id]) }
    private static func time() -> Date? { Calendar.current.date(bySettingHour: hour, minute: minute, second: 0, of: Date()) }
}
