import Foundation

/// Food added with no signal: kept on the phone and sent, in order, as soon as Home Assistant can be reached. Only adds with
/// known numbers wait here (working something out needs Home Assistant), and only when the request never left the phone, so
/// nothing is ever added twice.
struct Pending: Codable, Identifiable, Equatable {
    var id = UUID()
    var json: Data
    var name: String
    var meal: String
    var date: String
    var kcal: Double
    var tries = 0
    var failed: Bool?   // Home Assistant turned it down three times: shown with Try again and Remove, never dropped

    var mealValue: Meal { Meal(rawValue: meal) ?? .snack }

    init?(_ payload: [String: Any], name: String, meal: Meal, date: String) {
        guard let data = try? JSONSerialization.data(withJSONObject: payload) else { return nil }
        json = data; self.name = name; self.meal = meal.rawValue; self.date = date
        let n = { (k: String) in (payload[k] as? Double) ?? Double(payload[k] as? Int ?? 0) }
        if let p = payload["per_100"] as? [String: Any], let k = p["kcal"] as? Double { kcal = k * n("grams") / 100 }
        else { kcal = n("kcal") * max(n("portions"), 0.05) }
    }

    var payload: [String: Any]? { try? JSONSerialization.jsonObject(with: json) as? [String: Any] }
}

enum Outbox {
    private static let key = "outbox"
    static func load() -> [Pending] {
        AppConfig.shared.data(forKey: key).flatMap { try? JSONDecoder().decode([Pending].self, from: $0) } ?? []
    }
    static func save(_ items: [Pending]) {
        if let d = try? JSONEncoder().encode(items) { AppConfig.shared.set(d, forKey: key) }
    }
}
