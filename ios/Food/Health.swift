import Foundation
import HealthKit

/// Apple Health, both ways.
/// - Food → Health: every diary entry as one food correlation (calories, protein, carbs, fat, fibre), tagged with its entry id,
///   kept in step with the diary for the last week: written when new, rewritten when changed, removed when deleted (wherever
///   it was logged: this app, the phone dashboard, an automation).
/// - Health → the app: activity rings, steps and sleep, shown beside the food (the month calendar, a day's detail, the sprig's
///   "count exercise" room, the sleep insight).
/// - Health → Home Assistant (optional): steps, distance, active energy, exercise, stand hours, ring goals and sleep, sent to a
///   webhook the user chose in Settings whenever iOS has new samples (background delivery). Off until a webhook id is set.
final class HealthSync: @unchecked Sendable {
    static let shared = HealthSync()
    let store = HKHealthStore()
    private let entryKey = "FoodDiaryEntryID", versionKey = "FoodDiaryVersion"

    private var foodTypes: [HKQuantityType] {
        [.init(.dietaryEnergyConsumed), .init(.dietaryProtein), .init(.dietaryCarbohydrates), .init(.dietaryFatTotal), .init(.dietaryFiber)]
    }
    private var activityTypes: [HKQuantityType] {
        [.init(.stepCount), .init(.distanceWalkingRunning), .init(.activeEnergyBurned), .init(.appleExerciseTime)]
    }

    var available: Bool { HKHealthStore.isHealthDataAvailable() }

    func requestAccess() async {
        guard available else { return }
        var read: Set<HKObjectType> = Set(foodTypes).union(activityTypes)
        read.insert(HKCategoryType(.sleepAnalysis))
        read.insert(HKCategoryType(.appleStandHour))
        read.insert(HKObjectType.activitySummaryType())  // food correlations are read through the dietary types above
        try? await store.requestAuthorization(toShare: Set(foodTypes), read: read)
        AppConfig.shared.set(true, forKey: "healthAsked")
    }

    var asked: Bool { AppConfig.shared.bool(forKey: "healthAsked") }

    /// Whether the user let the app write food to Health (iOS tells apps that much; what they may read stays private).
    var foodAllowed: Bool { available && store.authorizationStatus(for: HKQuantityType(.dietaryEnergyConsumed)) == .sharingAuthorized }

    // ---------- Food → Health ----------

    private let queue = SyncQueue()

    /// Make Health's food for the last `days` days match the diary. One sync at a time: asked again while one runs, it runs
    /// once more afterwards (so the same food is never written twice by two syncs racing).
    func syncFood(days: Int = 7) async {
        guard await queue.start() else { return }
        repeat { await syncFoodNow(days: days) } while await queue.finish()
    }

    private func syncFoodNow(days: Int) async {
        guard available, asked, foodAllowed else { return }
        var entries: [String: (Entry, String)] = [:]
        for i in 0..<days {
            let date = Calendar.current.date(byAdding: .day, value: -i, to: Date())!.ymd
            guard let d = try? await FoodAPI.day(date) else { return }  // offline: try again later rather than delete anything
            for e in d.entries { entries[e.id] = (e, date) }
        }
        let start = Calendar.current.startOfDay(for: Calendar.current.date(byAdding: .day, value: -(days - 1), to: Date())!)
        let existing = await foodCorrelations(from: start)
        var keep = Set<String>()
        for c in existing {
            guard let id = c.metadata?[entryKey] as? String else { continue }
            if let (e, _) = entries[id], (c.metadata?[versionKey] as? String) == version(e), !keep.contains(id) { keep.insert(id); continue }
            try? await store.delete(Array(c.objects) + [c])
        }
        for (id, (e, date)) in entries where !keep.contains(id) {
            guard eatenAt(e, date: date) <= Date() else { continue }  // planned for later: it goes in when its mealtime comes
            try? await store.save(correlation(e, date: date))
        }
    }

    private func version(_ e: Entry) -> String {
        [e.name, e.meal, String(format: "%.1f|%.1f|%.1f|%.1f|%.1f", e.totals.kcal, e.totals.protein_g, e.totals.carbs_g, e.totals.fat_g, e.totals.fibre_g)].joined(separator: "|")
    }

    /// When it was (or will be) eaten: the time it was logged if that was on its own day, else the meal's usual time that day
    /// (food planned the night before belongs to the next day's breakfast, not to the evening it was typed in).
    func eatenAt(_ e: Entry, date: String) -> Date {
        if let t = e.time, t.ymd == date { return t }
        return e.mealValue.usualTime(on: date)
    }

    private func correlation(_ e: Entry, date: String) -> HKCorrelation {
        let when = eatenAt(e, date: date)
        let meta: [String: Any] = [HKMetadataKeyFoodType: e.name, entryKey: e.id, versionKey: version(e)]
        func q(_ id: HKQuantityTypeIdentifier, _ unit: HKUnit, _ v: Double) -> HKQuantitySample? {
            v > 0 ? HKQuantitySample(type: .init(id), quantity: .init(unit: unit, doubleValue: v), start: when, end: when, metadata: meta) : nil
        }
        let samples = [q(.dietaryEnergyConsumed, .kilocalorie(), e.totals.kcal), q(.dietaryProtein, .gram(), e.totals.protein_g),
                       q(.dietaryCarbohydrates, .gram(), e.totals.carbs_g), q(.dietaryFatTotal, .gram(), e.totals.fat_g),
                       q(.dietaryFiber, .gram(), e.totals.fibre_g)].compactMap { $0 }
        return HKCorrelation(type: .init(.food), start: when, end: when, objects: Set(samples), metadata: meta)
    }

    private func foodCorrelations(from start: Date) async -> [HKCorrelation] {
        await withCheckedContinuation { cont in
            let pred = NSCompoundPredicate(andPredicateWithSubpredicates: [
                HKQuery.predicateForSamples(withStart: start, end: nil), HKQuery.predicateForObjects(from: .default()),
            ])
            let q = HKCorrelationQuery(type: .init(.food), predicate: pred, samplePredicates: nil) { _, r, _ in cont.resume(returning: r ?? []) }
            store.execute(q)
        }
    }

    // ---------- Health → Home Assistant (optional) ----------

    /// Send the last two days of activity and the last three nights of sleep to the user's webhook; true once Home Assistant
    /// has them. The body is `{"days": {"2026-10-05": {"steps": 8412, "distance_km": 6.1, "active_kcal": 412, "exercise_min": 34,
    /// "stand_h": 11, "move_goal": 500, "exercise_goal": 30, "stand_goal": 12}}, "sleeps": [{"date", "asleep_min", "in_bed",
    /// "woke", "deep_min", "rem_min", "core_min", "source"}]}`.
    @discardableResult
    func sendActivity() async -> Bool {
        guard available, asked, let hook = AppConfig.activityWebhook else { return false }
        let cal = Calendar.current
        let start = cal.startOfDay(for: cal.date(byAdding: .day, value: -1, to: Date())!)
        var days: [String: [String: Any]] = [:]
        for (type, unit, key, scale) in [(HKQuantityType(.stepCount), HKUnit.count(), "steps", 1.0), (.init(.distanceWalkingRunning), .meterUnit(with: .kilo), "distance_km", 1),
                                         (.init(.activeEnergyBurned), .kilocalorie(), "active_kcal", 1), (.init(.appleExerciseTime), .minute(), "exercise_min", 1)] {
            for (date, v) in await dailySums(type, unit: unit, from: start) { days[date, default: [:]][key] = key == "distance_km" ? (v * scale * 100).rounded() / 100 : Int((v * scale).rounded()) }
        }
        for (date, h) in await standHours(from: start) { days[date, default: [:]]["stand_h"] = h }
        for (date, g) in await ringGoals(from: start) { days[date, default: [:]].merge(g) { _, b in b } }
        let sleeps = await nights(from: cal.date(byAdding: .day, value: -3, to: Date())!)
        var body: [String: Any] = [:]
        if !days.isEmpty { body["days"] = days }
        if !sleeps.isEmpty { body["sleeps"] = sleeps }
        guard !body.isEmpty else { return true }
        do {
            try await HAClient.shared.webhook(hook, body)
            AppConfig.shared.set(Date(), forKey: "healthSent")  // only once it's there, so a failed send is tried again soon
            return true
        } catch { return false }
    }

    /// A day's activity from Apple Health: the three rings against their goals, steps, and the night's sleep before it.
    struct Activity: Equatable {
        var move = 0.0, moveGoal = 0.0, exercise = 0.0, exerciseGoal = 0.0, stand = 0.0, standGoal = 0.0
        var steps = 0.0
        var sleepMin: Int?
        var rings: Bool { moveGoal > 0 }
        var moveClosed: Bool { moveGoal > 0 && move >= moveGoal }
        var exerciseClosed: Bool { exerciseGoal > 0 && exercise >= exerciseGoal }
        var standClosed: Bool { standGoal > 0 && stand >= standGoal }
    }

    /// Each day's activity between two dates (the calendar's month).
    func activity(from start: Date, to end: Date) async -> [String: Activity] {
        #if DEBUG
        if ProcessInfo.processInfo.arguments.contains("-fakeActivity") { return Self.fakeActivity(from: start, to: end) }
        #endif
        guard available, asked else { return [:] }
        let cal = Calendar.current
        var out: [String: Activity] = [:]
        let summaries: [HKActivitySummary] = await withCheckedContinuation { cont in
            var a = cal.dateComponents([.era, .year, .month, .day], from: start); a.calendar = cal
            var b = cal.dateComponents([.era, .year, .month, .day], from: end); b.calendar = cal
            store.execute(HKActivitySummaryQuery(predicate: HKQuery.predicate(forActivitySummariesBetweenStart: a, end: b)) { _, r, _ in
                cont.resume(returning: r ?? [])
            })
        }
        for s in summaries {
            guard let d = s.dateComponents(for: cal).date?.ymd else { continue }
            out[d] = Activity(move: s.activeEnergyBurned.doubleValue(for: .kilocalorie()), moveGoal: s.activeEnergyBurnedGoal.doubleValue(for: .kilocalorie()),
                         exercise: s.appleExerciseTime.doubleValue(for: .minute()), exerciseGoal: s.appleExerciseTimeGoal.doubleValue(for: .minute()),
                         stand: s.appleStandHours.doubleValue(for: .count()), standGoal: s.appleStandHoursGoal.doubleValue(for: .count()))
        }
        for (d, v) in await dailySums(HKQuantityType(.stepCount), unit: .count(), from: cal.startOfDay(for: start)) where d <= end.ymd {
            out[d, default: Activity()].steps = v
        }
        for n in await nights(from: cal.date(byAdding: .day, value: -1, to: start) ?? start) {
            if let d = n["date"] as? String, let m = n["asleep_min"] as? Int { out[d, default: Activity()].sleepMin = m }
        }
        return out
    }

    #if DEBUG
    /// The simulator has no Watch: a believable month of rings for UI tests and screenshots.
    private static func fakeActivity(from start: Date, to end: Date) -> [String: Activity] {
        var out: [String: Activity] = [:], d = start, i = 0.0
        while d.ymd <= end.ymd {
            out[d.ymd] = Activity(move: 180 + 260 * abs(sin(i * 1.7)), moveGoal: 350, exercise: 40 * abs(sin(i * 0.9)), exerciseGoal: 30,
                                  stand: 6 + (i * 5).truncatingRemainder(dividingBy: 7), standGoal: 12, steps: 4000 + 6000 * abs(sin(i)), sleepMin: 400 + Int(i) % 90)
            d = Calendar.current.date(byAdding: .day, value: 1, to: d) ?? end.addingTimeInterval(1); i += 1
        }
        return out
    }
    #endif

    /// Active calories burned each day (Apple Watch, or the phone's estimate), for the last `days` days.
    func activeKcal(days: Int) async -> [String: Double] {
        guard available, asked else { return [:] }
        let cal = Calendar.current
        let start = cal.startOfDay(for: cal.date(byAdding: .day, value: -(days - 1), to: Date())!)
        return await dailySums(HKQuantityType(.activeEnergyBurned), unit: .kilocalorie(), from: start)
    }

    private func dailySums(_ type: HKQuantityType, unit: HKUnit, from start: Date) async -> [String: Double] {
        await withCheckedContinuation { cont in
            let q = HKStatisticsCollectionQuery(quantityType: type, quantitySamplePredicate: HKQuery.predicateForSamples(withStart: start, end: nil),
                                                options: .cumulativeSum, anchorDate: start, intervalComponents: DateComponents(day: 1))
            q.initialResultsHandler = { _, r, _ in
                var out: [String: Double] = [:]
                r?.enumerateStatistics(from: start, to: Date()) { s, _ in
                    if let v = s.sumQuantity()?.doubleValue(for: unit) { out[s.startDate.ymd] = v }
                }
                cont.resume(returning: out)
            }
            store.execute(q)
        }
    }

    private func standHours(from start: Date) async -> [String: Int] {
        await withCheckedContinuation { cont in
            let q = HKSampleQuery(sampleType: HKCategoryType(.appleStandHour), predicate: HKQuery.predicateForSamples(withStart: start, end: nil),
                                  limit: HKObjectQueryNoLimit, sortDescriptors: nil) { _, r, _ in
                var out: [String: Int] = [:]
                for s in (r as? [HKCategorySample]) ?? [] where s.value == HKCategoryValueAppleStandHour.stood.rawValue { out[s.startDate.ymd, default: 0] += 1 }
                cont.resume(returning: out)
            }
            store.execute(q)
        }
    }

    private func ringGoals(from start: Date) async -> [String: [String: Any]] {
        await withCheckedContinuation { cont in
            let cal = Calendar.current
            var a = cal.dateComponents([.era, .year, .month, .day], from: start); a.calendar = cal
            var b = cal.dateComponents([.era, .year, .month, .day], from: Date()); b.calendar = cal
            let q = HKActivitySummaryQuery(predicate: HKQuery.predicate(forActivitySummariesBetweenStart: a, end: b)) { _, r, _ in
                var out: [String: [String: Any]] = [:]
                for s in r ?? [] {
                    guard let d = s.dateComponents(for: cal).date else { continue }
                    out[d.ymd] = ["move_goal": Int(s.activeEnergyBurnedGoal.doubleValue(for: .kilocalorie()).rounded()),
                                  "exercise_goal": Int(s.appleExerciseTimeGoal.doubleValue(for: .minute()).rounded()),
                                  "stand_goal": Int(s.appleStandHoursGoal.doubleValue(for: .count()).rounded())]
                }
                cont.resume(returning: out)
            }
            store.execute(q)
        }
    }

    /// Nights keyed by the waking date, minutes asleep, in bed and woke times, stages.
    private func nights(from start: Date) async -> [[String: Any]] {
        let samples: [HKCategorySample] = await withCheckedContinuation { cont in
            let q = HKSampleQuery(sampleType: HKCategoryType(.sleepAnalysis), predicate: HKQuery.predicateForSamples(withStart: start, end: nil),
                                  limit: HKObjectQueryNoLimit, sortDescriptors: [NSSortDescriptor(key: HKSampleSortIdentifierStartDate, ascending: true)]) { _, r, _ in
                cont.resume(returning: (r as? [HKCategorySample]) ?? [])
            }
            store.execute(q)
        }
        // a night = samples with gaps under 3 hours; prefer the watch's stages when both phone and watch recorded
        var groups: [[HKCategorySample]] = []
        for s in samples {
            if let last = groups.last?.map(\.endDate).max(), s.startDate.timeIntervalSince(last) < 3 * 3600 { groups[groups.count - 1].append(s) } else { groups.append([s]) }
        }
        let iso = ISO8601DateFormatter()
        iso.timeZone = .current
        iso.formatOptions = [.withInternetDateTime]
        return groups.compactMap { g in
            let asleepValues: Set<Int> = [HKCategoryValueSleepAnalysis.asleepCore.rawValue, HKCategoryValueSleepAnalysis.asleepDeep.rawValue,
                                          HKCategoryValueSleepAnalysis.asleepREM.rawValue, HKCategoryValueSleepAnalysis.asleepUnspecified.rawValue]
            let staged = g.filter { [HKCategoryValueSleepAnalysis.asleepCore.rawValue, HKCategoryValueSleepAnalysis.asleepDeep.rawValue, HKCategoryValueSleepAnalysis.asleepREM.rawValue].contains($0.value) }
            let asleep = (staged.isEmpty ? g.filter { asleepValues.contains($0.value) } : staged)
            guard let woke = asleep.map(\.endDate).max(), let first = g.map(\.startDate).min(), woke < Date() else { return nil }
            func mins(_ v: HKCategoryValueSleepAnalysis) -> Int { Int(g.filter { $0.value == v.rawValue }.reduce(0) { $0 + $1.endDate.timeIntervalSince($1.startDate) } / 60) }
            let total = Int(asleep.reduce(0) { $0 + $1.endDate.timeIntervalSince($1.startDate) } / 60)
            guard total >= 60 else { return nil }  // naps aren't nights
            return ["date": woke.ymd, "asleep_min": total, "in_bed": iso.string(from: first), "woke": iso.string(from: woke),
                    "deep_min": mins(.asleepDeep), "rem_min": mins(.asleepREM), "core_min": mins(.asleepCore), "source": "Food app"]
        }
    }

    // ---------- background ----------

    /// iOS wakes the app when new steps, activity or sleep arrive; each wake sends to the webhook, if one is set, and takes back
    /// reminders for meals already in (Health keeps working while the app is closed).
    func startBackgroundDelivery() {
        guard available, asked else { return }
        let types: [(HKSampleType, HKUpdateFrequency)] = [(HKQuantityType(.stepCount), .hourly), (HKQuantityType(.activeEnergyBurned), .hourly),
                                                           (HKQuantityType(.appleExerciseTime), .hourly), (HKCategoryType(.sleepAnalysis), .immediate)]
        for (type, freq) in types {
            store.enableBackgroundDelivery(for: type, frequency: freq) { _, _ in }
            let q = HKObserverQuery(sampleType: type, predicate: nil) { [weak self] _, done, _ in
                Task { await self?.sendActivityThrottled(); await Reminders.shared.checkDiary(); done() }
            }
            store.execute(q)
        }
    }

    /// At most every 10 minutes (several types often fire together).
    func sendActivityThrottled() async {
        guard AppConfig.activityWebhook != nil else { return }
        if let last = AppConfig.shared.object(forKey: "healthSent") as? Date, Date().timeIntervalSince(last) < 600 { return }
        await sendActivity()
    }
}

/// One food sync at a time; a request while one runs is remembered and run once after it.
private actor SyncQueue {
    private var running = false, again = false
    func start() -> Bool { if running { again = true; return false }; running = true; return true }
    func finish() -> Bool { if again { again = false; return true }; running = false; return false }
}
