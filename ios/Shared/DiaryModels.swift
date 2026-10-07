import Foundation

private extension KeyedDecodingContainer {
    func num(_ k: Key) -> Double { (try? decodeIfPresent(Double.self, forKey: k)) ?? 0 }
    func str(_ k: Key) -> String { (try? decodeIfPresent(String.self, forKey: k)) ?? "" }
}

/// Several foods eaten together, kept as one thing to log (a "usual breakfast").
struct SavedMeal: Decodable, Identifiable, Hashable {
    var id: String
    var name: String
    var meal: String
    var items: [String]
    var values: Nutrients

    enum CodingKeys: String, CodingKey { case id, name, meal, items }
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = c.str(.id); name = c.str(.name); meal = c.str(.meal)
        items = (try? c.decodeIfPresent([String].self, forKey: .items)) ?? nil ?? []
        values = try Nutrients(from: decoder)
    }

    var summary: String { items.prefix(4).joined(separator: ", ") + (items.count > 4 ? "…" : "") }
}

/// What gets eaten again and again: recent foods, usuals per meal, saved meals.
struct Recent: Decodable {
    var foods: [RecentFood] = []
    var usuals: [String: [RecentFood]] = [:]
    var saved: [SavedMeal] = []

    enum CodingKeys: String, CodingKey { case foods, usuals, saved }
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        foods = (try? c.decodeIfPresent([RecentFood].self, forKey: .foods)) ?? nil ?? []
        usuals = (try? c.decodeIfPresent([String: [RecentFood]].self, forKey: .usuals)) ?? nil ?? [:]
        saved = (try? c.decodeIfPresent([SavedMeal].self, forKey: .saved)) ?? nil ?? []
    }
}

/// A week looking back: one headline (days on target) and only what stands out.
struct WeekReview: Decodable, Identifiable {
    var id: String { end }
    struct DayRow: Decodable, Identifiable {
        var date: String
        var kcal: Double
        var logged: Int
        var id: String { date }
    }
    struct Favourite: Decodable { var name: String; var times: Int }
    struct Best: Decodable { var date: String; var kcal: Double }

    var start: String
    var end: String
    var days: [DayRow]
    var goal_kcal: Double
    var days_logged: Int
    var avg_kcal: Double
    var on_target: Int
    var over: Int
    var protein_days: Int
    var last_week_avg_kcal: Double?
    var favourite: Favourite?
    var new_dishes: [String]?
    var best_day: Best?

    /// The one thing to say: how many of the logged days were on target.
    var headline: String { "\(on_target) of \(days_logged) days on target" }

    var average: String {
        var s = "Averaging \(Fmt.kcal(avg_kcal)) kcal a day"
        if let prev = last_week_avg_kcal, abs(prev - avg_kcal) >= 50 {
            s += ", \(Fmt.kcal(abs(prev - avg_kcal))) \(avg_kcal < prev ? "less" : "more") than the week before"
        }
        return s + "."
    }

    /// At most one more thing, and only if it stands out: a new recipe, a strong protein week, a clear favourite.
    var highlight: String? {
        if let n = new_dishes?.first { return "You tried \(n)." }
        if protein_days >= 4 { return "Protein goal on \(protein_days) days." }
        if let f = favourite, f.times >= 3 { return "\(f.name) \(f.times) times." }
        return nil
    }
}
