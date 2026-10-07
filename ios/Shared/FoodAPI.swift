import Foundation
#if canImport(WidgetKit)
import WidgetKit
#endif

/// The food_diary actions, as the app uses them. Everything is stored in Home Assistant; the app keeps no diary of its own.
enum FoodAPI {
    static let client = HAClient.shared

    static func day(_ date: String) async throws -> Day {
        let data = try await client.call("get_day", ["date": date])
        let d = try JSONDecoder().decode(Day.self, from: data)
        if date == Date().ymd { AppConfig.shared.set(data, forKey: "lastDay") }  // so the app opens on today's numbers at once
        return d
    }

    /// Today as last seen (shown at launch while Home Assistant is asked again); nil once the day has changed.
    static func lastDay() -> Day? {
        guard let data = AppConfig.shared.data(forKey: "lastDay"), let d = try? JSONDecoder().decode(Day.self, from: data), d.date == Date().ymd else { return nil }
        return d
    }
    static func history(days: Int = 7, through end: String = Date().ymd) async throws -> History {
        try await client.call("get_history", ["days": days, "date": end], as: History.self)
    }

    static func recent() async throws -> [RecentFood] {
        struct R: Decodable { var foods: [RecentFood] }
        return try await client.call("get_recent", as: R.self).foods
    }

    static func estimate(_ data: [String: Any]) async throws -> Estimate { try await client.call("estimate", data, as: Estimate.self) }

    struct Logged: Decodable {
        struct E: Decodable { var id: String }
        var entry: E
        var date: String
    }

    @discardableResult
    static func log(_ data: [String: Any]) async throws -> Logged {
        let r = try await client.call("log_food", data, as: Logged.self)
        await afterChange()
        return r
    }

    static func update(_ entryID: String, date: String, _ changes: [String: Any]) async throws {
        _ = try await client.call("update_food", changes.merging(["entry_id": entryID, "date": date]) { a, _ in a })
        await afterChange()
    }

    static func delete(_ entryID: String, date: String) async throws {
        _ = try await client.call("delete_food", ["entry_id": entryID, "date": date])
        await afterChange()
    }

    static func setGoals(_ goals: [String: Double]) async throws {
        _ = try await client.call("set_goals", goals)
        await afterChange()
    }

    /// Log something eaten before, as it was (grams for label foods), in the portions they usually have.
    static func logAgain(_ f: RecentFood, meal: Meal = .now, date: String = Date().ymd) async throws -> Logged {
        try await log(againPayload(f, meal: meal, date: date))
    }

    static func againPayload(_ f: RecentFood, meal: Meal, date: String) -> [String: Any] {
        var d: [String: Any] = ["name": f.name, "meal": meal.rawValue, "source": "again", "portions": 1, "date": date]
        if let p = f.per100, let g = f.grams, g > 0 { d["per_100"] = p.dict; d["grams"] = g; d["unit"] = f.unit ?? "g" }
        else { d.merge(f.values.dict) { _, b in b }; d["portions"] = PortionMemory.portions(for: f.name) }
        if let r = f.ref, !r.isEmpty { d["ref"] = r }
        if let p = f.photo { d["photo"] = p }
        if let u = f.imageURL { d["image_url"] = u }
        return d
    }

    /// After any change: tell the app's screens at once; the widgets' snapshot and timelines follow in the background (the
    /// widgets fetch the day themselves too), so nothing waits on an extra round trip.
    static func afterChange() async {
        NotificationCenter.default.post(name: .foodDiaryChanged, object: nil)
        Task.detached {
            if let d = try? await day(Date().ymd) { Snapshot.save(from: d) }
            #if canImport(WidgetKit)
            WidgetCenter.shared.reloadAllTimelines()
            #endif
        }
    }
}

extension Notification.Name {
    static let foodDiaryChanged = Notification.Name("foodDiaryChanged")
}

/// What the widgets show when they can't reach Home Assistant: today's totals as last seen.
struct Snapshot: Codable {
    var date: String
    var kcal: Double
    var goal: Double
    var protein: Double, carbs: Double, fat: Double
    var proteinGoal: Double, carbsGoal: Double, fatGoal: Double
    var updated: Date

    var left: Double { goal - kcal }

    static func save(from d: Day) {
        let s = Snapshot(date: d.date, kcal: d.totals.kcal, goal: d.goals.kcal, protein: d.totals.protein_g, carbs: d.totals.carbs_g, fat: d.totals.fat_g,
                         proteinGoal: d.goals.protein_g, carbsGoal: d.goals.carbs_g, fatGoal: d.goals.fat_g, updated: Date())
        if let data = try? JSONEncoder().encode(s) { AppConfig.shared.set(data, forKey: "snapshot") }
    }

    static func load() -> Snapshot? {
        guard let data = AppConfig.shared.data(forKey: "snapshot"), let s = try? JSONDecoder().decode(Snapshot.self, from: data) else { return nil }
        return s.date == Date().ymd ? s : Snapshot(date: Date().ymd, kcal: 0, goal: s.goal, protein: 0, carbs: 0, fat: 0,
                                                   proteinGoal: s.proteinGoal, carbsGoal: s.carbsGoal, fatGoal: s.fatGoal, updated: s.updated)
    }

    static let placeholder = Snapshot(date: Date().ymd, kcal: 870, goal: 1400, protein: 52, carbs: 80, fat: 31, proteinGoal: 95, carbsGoal: 151, fatGoal: 47, updated: Date())
}

/// Screens the widgets, Siri and links open the app on (fooddiary://scan, …).
enum Route: String {
    case today, add, scan, photo, label, type
    static func from(_ url: URL) -> Route? { url.scheme == AppConfig.redirectScheme ? Route(rawValue: url.host ?? "") : nil }

    static func setPending(_ r: Route) { AppConfig.shared.set(r.rawValue, forKey: "pendingRoute") }
    static func takePending() -> Route? {
        defer { AppConfig.shared.removeObject(forKey: "pendingRoute") }
        return AppConfig.shared.string(forKey: "pendingRoute").flatMap(Route.init(rawValue:))
    }
}
