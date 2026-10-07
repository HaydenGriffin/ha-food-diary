import Foundation

/// Saved meals, the week looking back, photos and copying days: the rest of the food_diary actions the app uses.
extension FoodAPI {
    static func recentAll() async throws -> Recent { try await client.call("get_recent", as: Recent.self) }

    /// More of what they've had, for searching (an integration without the `limit` option gives its usual 30).
    static func recentForSearch() async throws -> [RecentFood] {
        do { return try await client.call("get_recent", ["limit": 150], as: Recent.self).foods }
        catch HAClient.Failure.server { return try await recentAll().foods }
    }

    static func days(_ dates: [String]) async throws -> [Day] {
        try await withThrowingTaskGroup(of: (Int, Day).self) { group in
            for (i, d) in dates.enumerated() { group.addTask { (i, try await day(d)) } }
            var out: [(Int, Day)] = []
            for try await x in group { out.append(x) }
            return out.sorted { $0.0 < $1.0 }.map(\.1)
        }
    }

    // ---------- saved meals ----------

    static func saveMeal(_ name: String, meal: Meal, date: String) async throws -> SavedMeal {
        struct R: Decodable { var saved: SavedMeal }
        return try await client.call("save_meal", ["name": name, "meal": meal.rawValue, "date": date], as: R.self).saved
    }

    static func deleteSaved(_ id: String) async throws { _ = try await client.call("delete_saved_meal", ["saved_id": id]) }

    /// Rename a saved meal, or move it to another meal.
    static func updateSaved(_ id: String, name: String? = nil, meal: Meal? = nil) async throws -> SavedMeal {
        struct R: Decodable { var saved: SavedMeal }
        var d: [String: Any] = ["saved_id": id]
        if let name { d["name"] = name }
        if let meal { d["meal"] = meal.rawValue }
        return try await client.call("update_saved_meal", d, as: R.self).saved
    }

    static func logSaved(_ s: SavedMeal, meal: Meal, date: String) async throws -> Logged {
        try await log(savedPayload(s, meal: meal, date: date))
    }

    static func savedPayload(_ s: SavedMeal, meal: Meal, date: String) -> [String: Any] {
        var d: [String: Any] = ["name": s.name, "meal": meal.rawValue, "source": "saved", "ref": s.id, "portions": 1, "date": date]
        if !s.items.isEmpty { d["note"] = String(s.items.joined(separator: ", ").prefix(120)) }
        d.merge(s.values.dict) { _, b in b }
        return d
    }

    // ---------- the week looking back ----------

    static func review(through end: String, days: Int = 7) async throws -> WeekReview {
        try await client.call("get_week_review", ["date": end, "days": days], as: WeekReview.self)
    }

    // ---------- photos ----------

    /// The user's own photo for something already logged.
    static func setPhoto(_ entryID: String, date: String, jpeg: Data) async throws {
        _ = try await client.call("set_photo", ["entry_id": entryID, "date": date, "image": jpeg.base64EncodedString()])
        await afterChange()
    }

    // ---------- copying days ----------

    struct Copied: Decodable { var token: String; var added: [String: Int]; var removed: [String: Int] }

    static func copyDay(from: String, to: [String], meal: Meal? = nil, replace: Bool = false) async throws -> Copied {
        var d: [String: Any] = ["from": from, "to": to, "replace": replace]
        if let meal { d["meal"] = meal.rawValue }
        let r = try await client.call("copy_day", d, as: Copied.self)
        await afterChange()
        return r
    }

    static func undoCopy(_ token: String) async throws {
        _ = try await client.call("undo_copy", ["token": token])
        await afterChange()
    }
}
