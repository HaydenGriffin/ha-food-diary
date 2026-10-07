import Foundation

/// One month for the Month page: each day's food against their goals, Apple Health's rings, a picture and a count per day,
/// and the averages the page is built on.
struct MonthData {
    var month: Date
    var food: [String: HistoryDay] = [:]
    var goals = Goals()
    var activity: [String: HealthSync.Activity] = [:]
    var photos: [String: String] = [:]     // a picture for each day (their own photo first)
    var logged: [String: Int] = [:]        // how many things each day has
    var room: [String: Double] = [:]       // calories a day can take on top of its goal (exercise, when they count it)

    /// Every day of the month, as "yyyy-MM-dd".
    var days: [String] {
        let cal = Calendar.current
        guard let range = cal.range(of: .day, in: .month, for: month) else { return [] }
        return range.compactMap { cal.date(byAdding: .day, value: $0 - 1, to: month)?.ymd }
    }

    /// The month as Monday-to-Sunday rows (the UK way, like the week strip); days outside the month are nil.
    var weeks: [[String?]] {
        let days = days
        guard let first = days.first.flatMap(Date.fromYMD) else { return [] }
        let lead = (Streak.weekCal.component(.weekday, from: first) + 5) % 7  // Monday 0 … Sunday 6
        let cells: [String?] = Array(repeating: nil, count: lead) + days
        return stride(from: 0, to: cells.count, by: 7).map { i in
            let row = Array(cells[i..<min(i + 7, cells.count)])
            return row + Array(repeating: nil, count: 7 - row.count)
        }
    }

    /// Apple's rings go in the month once Health has some for it (connected, and read access given).
    var showHealth: Bool { activity.values.contains(where: \.rings) }

    /// A finished day within their goal (a leaf on the sprig). Today only counts once it's over.
    func leaf(_ d: String) -> Bool {
        guard d < Date().ymd, let f = food[d] else { return false }
        return Streak.onTarget(f.kcal, goal: goals.kcal, room: room[d] ?? 0)
    }

    /// The averages over these days. Only finished days with food count: today is still going, and an empty day is
    /// a day they didn't log, not a day they didn't eat.
    func average(_ days: [String]) -> MonthAverage {
        let ate = days.filter { $0 < Date().ymd }.compactMap { food[$0] }.filter { $0.kcal > 0 }
        var a = MonthAverage(days: ate.count, leaves: days.filter { leaf($0) }.count)
        if !ate.isEmpty {
            let n = Double(ate.count)
            a.kcal = ate.map(\.kcal).reduce(0, +) / n
            a.protein = ate.map(\.protein).reduce(0, +) / n
            a.carbs = ate.map(\.carbs).reduce(0, +) / n
            a.fat = ate.map(\.fat).reduce(0, +) / n
        }
        a.health = Self.average(days.filter { $0 <= Date().ymd }.compactMap { activity[$0] }.filter(\.rings))
        return a
    }

    private static func average(_ rings: [HealthSync.Activity]) -> HealthSync.Activity? {
        guard !rings.isEmpty else { return nil }
        let n = Double(rings.count)
        func avg(_ k: KeyPath<HealthSync.Activity, Double>) -> Double { rings.map { $0[keyPath: k] }.reduce(0, +) / n }
        return HealthSync.Activity(move: avg(\.move), moveGoal: avg(\.moveGoal), exercise: avg(\.exercise), exerciseGoal: avg(\.exerciseGoal),
                                   stand: avg(\.stand), standGoal: avg(\.standGoal))
    }

    /// The month from Home Assistant and Apple Health. `exercise` is each day's burned calories when they count exercise
    /// (it gives a day more room before it's over), nil when they don't. Nil back when Home Assistant can't be reached.
    @MainActor static func load(_ month: Date, exercise: [String: Double]?) async -> MonthData? {
        var m = MonthData(month: month)
        let days = m.days
        guard let first = days.first.flatMap(Date.fromYMD), let last = days.last,
              let h = try? await FoodAPI.history(days: days.count, through: last) else { return nil }
        m.food = Dictionary(h.days.map { ($0.date, $0) }, uniquingKeysWith: { a, _ in a })
        m.goals = h.goals
        await m.loadPictures()
        if first <= Date(), let end = Date.fromYMD(last) { m.activity = await HealthSync.shared.activity(from: first, to: min(end, Date())) }
        if let exercise {
            for d in days { m.room[d] = m.activity[d]?.move ?? exercise[d] ?? 0 }
        }
        return m
    }

    /// Each day's picture for the photo month (their own photo first, then any other), and how many things it has.
    @MainActor private mutating func loadPictures() async {
        let limit = Calendar.current.date(byAdding: .day, value: AppModel.maxAhead, to: Date())?.ymd ?? Date().ymd
        let days = days.filter { $0 <= limit }
        guard !days.isEmpty, let ds = try? await FoodAPI.days(days) else { return }
        for d in ds {
            logged[d.date] = d.entries.count
            let images = d.entries.compactMap(\.image)
            if let p = images.first(where: { $0.hasPrefix("/api/") }) ?? images.first { photos[d.date] = p }
        }
    }
}

/// Food averages over some finished days (a month, a week), with the leaves they grew and Apple's rings on average.
struct MonthAverage {
    var days = 0
    var leaves = 0
    var kcal = 0.0, protein = 0.0, carbs = 0.0, fat = 0.0
    var health: HealthSync.Activity?

    /// "on average 1,372 of 1,400 calories a day, protein 82 of 95 grams, …" for VoiceOver.
    func spoken(_ goals: Goals) -> String {
        guard days > 0 else { return "nothing logged yet" }
        let food = "on average \(Fmt.kcal(kcal)) of \(Fmt.kcal(goals.kcal)) calories a day, "
            + Self.lines(protein: protein, carbs: carbs, fat: fat, goals: goals)
        return food + ", \(Self.count(days, "day")) logged" + (leaves > 0 ? ", \(Self.count(leaves, "leaf", "leaves")) grown" : "")
    }

    /// "protein 80 of 95 grams, carbs …" to follow on mid-sentence (FoodRingLines.spoken starts one).
    static func lines(protein: Double, carbs: Double, fat: Double, goals: Goals) -> String {
        let s = FoodRingLines.spoken(protein: protein, carbs: carbs, fat: fat, goals: goals)
        return s.prefix(1).lowercased() + s.dropFirst()
    }

    /// "1 day", "4 days", "1 leaf", "3 leaves".
    static func count(_ n: Int, _ one: String, _ many: String? = nil) -> String { "\(n) \(n == 1 ? one : many ?? one + "s")" }
}
