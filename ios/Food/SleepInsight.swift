import Foundation

/// "After nights under 6 hours you eat about 250 kcal more": said only when their own last two months show it clearly (at
/// least 4 short nights and 6 rested ones, both logged, and a difference of 150 kcal or more), and at most once a month.
enum SleepInsight {
    /// The difference in kcal eaten after short nights (under 6 h) compared with rested ones (7 h or more), or nil.
    static func compute(food: [HistoryDay], sleep: [String: Int]) -> Int? {
        var short: [Double] = [], rested: [Double] = []
        for d in food where d.kcal > 0 {
            guard let m = sleep[d.date] else { continue }  // the night before this day (sleep is kept by the waking date)
            if m < 360 { short.append(d.kcal) } else if m >= 420 { rested.append(d.kcal) }
        }
        guard short.count >= 4, rested.count >= 6 else { return nil }
        let diff = short.reduce(0, +) / Double(short.count) - rested.reduce(0, +) / Double(rested.count)
        guard abs(diff) >= 150 else { return nil }
        return Int((diff / 50).rounded() * 50)
    }

    static func text(_ kcal: Int) -> String {
        "After nights under 6 hours you eat about \(abs(kcal)) kcal \(kcal > 0 ? "more" : "less") than after a good night."
    }

    /// Once a month: shown for this week's look back if it hasn't been shown this month (or was shown for this same week).
    static func due(for reviewEnd: String) -> Bool {
        let month = String(Date().ymd.prefix(7))
        let shown = AppConfig.shared.string(forKey: "sleepInsight") ?? ""   // "<month>|<review end>"
        return !shown.hasPrefix(month) || shown == "\(month)|\(reviewEnd)"
    }
    static func shown(for reviewEnd: String) { AppConfig.shared.set("\(Date().ymd.prefix(7))|\(reviewEnd)", forKey: "sleepInsight") }
}
