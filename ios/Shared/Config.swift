import Foundation

/// Where Home Assistant is and how the app, its widgets and its share extension find each other. The bundle prefix, the
/// OAuth client id and the suggested server come from the build settings (Config/Base.xcconfig, Config/Local.xcconfig)
/// through each target's Info.plist.
enum AppConfig {
    private static func info(_ key: String) -> String? {
        (Bundle.main.object(forInfoDictionaryKey: key) as? String).flatMap { $0.isEmpty || $0.hasPrefix("$(") ? nil : $0 }
    }

    static let bundlePrefix = info("FDBundlePrefix") ?? "com.example.fooddiary"
    static let appGroup = "group.\(bundlePrefix).food"
    static let defaultServer = URL(string: info("FDDefaultServer") ?? "http://homeassistant.local:8123")!
    /// HA checks a third-party app's redirect against <link rel="redirect_uri"> on its client_id page (docs/auth/index.html).
    static let clientID = info("FDClientID") ?? "https://haydengriffin.github.io/ha-food-diary/auth/"
    static let redirectScheme = "fooddiary"
    static let redirect = "fooddiary://auth"

    static var keychainGroup: String { (info("AppIdentifierPrefix") ?? "") + "\(bundlePrefix).food.shared" }
    static var keychainService: String { "\(bundlePrefix).food" }
    static let shared = UserDefaults(suiteName: appGroup) ?? .standard

    /// Apple Health activity → a Home Assistant webhook of the user's choosing (off when empty). Set in Settings.
    static var activityWebhook: String? {
        get { shared.string(forKey: "activityWebhook").flatMap { $0.trimmingCharacters(in: .whitespaces).isEmpty ? nil : $0 } }
        set { shared.set(newValue?.trimmingCharacters(in: .whitespaces), forKey: "activityWebhook") }
    }
}

enum Meal: String, CaseIterable, Codable, Identifiable {
    case breakfast, lunch, dinner, snack
    var id: String { rawValue }
    var title: String { rawValue == "snack" ? "Snacks" : rawValue.capitalized }
    var single: String { rawValue.capitalized }

    /// The meal it most likely is now: the afternoon gap between lunch and dinner is a snack, as is late evening.
    static var now: Meal {
        let c = Calendar.current.dateComponents([.hour, .minute], from: Date())
        let h = Double(c.hour ?? 12) + Double(c.minute ?? 0) / 60
        return h < 10.5 ? .breakfast : h < 14.5 ? .lunch : h < 17.5 ? .snack : h < 21.5 ? .dinner : .snack
    }
}

extension Meal {
    /// When this meal is usually eaten on a day: food put in ahead counts as eaten from then.
    func usualTime(on ymd: String) -> Date {
        let (h, m) = switch self { case .breakfast: (8, 0); case .lunch: (12, 30); case .dinner: (18, 0); case .snack: (15, 30) }
        let day = Date.fromYMD(ymd) ?? Date()
        return Calendar.current.date(bySettingHour: h, minute: m, second: 0, of: day) ?? day
    }
}

extension Date {
    private static let ymdFormatter: DateFormatter = {
        let f = DateFormatter()
        f.calendar = Calendar(identifier: .gregorian)
        f.locale = Locale(identifier: "en_GB_POSIX")
        f.dateFormat = "yyyy-MM-dd"
        return f
    }()
    private static let entryFormatters: [DateFormatter] = ["yyyy-MM-dd'T'HH:mmXXXXX", "yyyy-MM-dd'T'HH:mm:ssXXXXX", "yyyy-MM-dd'T'HH:mm", "yyyy-MM-dd'T'HH:mm:ss"].map {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_GB_POSIX")
        f.dateFormat = $0
        return f
    }

    var ymd: String { Self.ymdFormatter.string(from: self) }

    static func fromYMD(_ s: String) -> Date? {
        ymdFormatter.date(from: s).map { Calendar.current.date(bySettingHour: 12, minute: 0, second: 0, of: $0) ?? $0 }
    }

    /// Entry times come as "2026-10-05T19:02+01:00" (or, from older diaries, "2026-10-05T11:43" in local time).
    static func fromEntry(_ s: String?) -> Date? {
        guard let s, !s.isEmpty else { return nil }
        for f in entryFormatters { if let d = f.date(from: s) { return d } }
        return nil
    }
}

/// How a day is named in words: "today", "tomorrow", "yesterday", or its weekday ("Thursday"), for sentences like
/// "Added to tomorrow's breakfast".
enum DayName {
    static func of(_ ymd: String) -> String {
        guard let d = Date.fromYMD(ymd) else { return ymd }
        let cal = Calendar.current
        if cal.isDateInToday(d) { return "today" }
        if cal.isDateInTomorrow(d) { return "tomorrow" }
        if cal.isDateInYesterday(d) { return "yesterday" }
        let days = cal.dateComponents([.day], from: cal.startOfDay(for: Date()), to: cal.startOfDay(for: d)).day ?? 0
        return abs(days) < 7 ? d.formatted(.dateTime.weekday(.wide)) : d.formatted(.dateTime.weekday(.wide).day().month(.wide))
    }

    /// "breakfast", "tomorrow's breakfast", "Thursday's breakfast".
    static func meal(_ meal: Meal, on ymd: String) -> String {
        let day = of(ymd)
        return day == "today" ? meal.single.lowercased() : "\(day)'s \(meal.single.lowercased())"
    }
}
