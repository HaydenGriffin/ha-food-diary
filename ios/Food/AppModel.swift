import Network
import SwiftUI
import UIKit
#if canImport(WidgetKit)
import WidgetKit
#endif

/// One of the usuals: a saved meal, or a food eaten most days.
enum Usual: Identifiable, Hashable {
    case saved(SavedMeal), food(RecentFood)
    var id: String { switch self { case .saved(let s): "s-\(s.id)"; case .food(let f): "f-\(f.id)" } }
    var name: String { switch self { case .saved(let s): s.name; case .food(let f): f.name } }
    var kcal: Double { switch self { case .saved(let s): s.values.kcal; case .food(let f): f.kcalEach } }
    var detail: String { switch self { case .saved(let s): s.summary; case .food: "Your usual" } }
    var image: String? { switch self { case .saved: nil; case .food(let f): f.image } }
}

/// Where new food goes, fixed when Add is opened: the day on screen then, and the meal if Add was tapped on one. Nothing
/// that happens while adding (coming back to the app, another day on screen) can move it.
struct AddRequest: Identifiable, Hashable {
    var route: Route
    var date: String
    var meal: Meal?
    var id: String { "\(route.rawValue)|\(date)|\(meal?.rawValue ?? "")" }

    /// The meal it lands in when none was picked: the time of day today, else breakfast.
    var defaultMeal: Meal { meal ?? (date == Date().ymd ? .now : .breakfast) }

    /// The sheet's title: "Add food", "Add breakfast", "Add to tomorrow", "Breakfast · Thursday".
    var title: String {
        let day = DayName.of(date)
        switch (meal, day == "today") {
        case (let m?, true): return "Add \(m.single.lowercased())"
        case (let m?, false): return "\(m.single) · \(day.prefix(1).uppercased() + day.dropFirst())"
        case (nil, true): return "Add food"
        case (nil, false): return "Add to \(day)"
        }
    }
}

/// Something worked out and not yet logged: the photo, the numbers, how much (portions, or grams for a label or barcode).
struct Draft {
    var name: String
    var perPortion: Nutrients          // per portion; for a label food, for `grams`
    var portions = 1.0
    var per100: Nutrients?
    var grams: Double?
    var unit = "g"
    var meal = Meal.now
    var source: String
    var ref: String?
    var note: String?
    var image: UIImage?
    var guessed = false
    var servingG: Double?
    var barcode: String?
    var edited = false
    var photo: String?
    var imageURL: String?

    var isLabel: Bool { per100 != nil && grams != nil && !edited }
    var totals: Nutrients { isLabel ? per100!.scaled(grams! / 100) : perPortion.scaled(portions) }

    init(name: String, perPortion: Nutrients, source: String) { self.name = name; self.perPortion = perPortion; self.source = source }

    init(_ e: Estimate, image: UIImage? = nil, meal: Meal = .now) {
        name = e.name; perPortion = e.values; per100 = e.per100; grams = e.grams; unit = e.unit ?? "g"; source = e.source ?? "text"
        ref = e.ref; note = e.note ?? (e.foods.isEmpty ? nil : e.foods.prefix(5).joined(separator: ", ")); self.image = image
        guessed = e.guessed; servingG = e.servingG; barcode = e.barcode; self.meal = meal; photo = e.photo; imageURL = e.imageURL
        if per100 == nil || grams == nil { portions = PortionMemory.portions(for: e.name) }  // as they usually have it
    }

    var payload: [String: Any] {
        var d: [String: Any] = ["name": name, "meal": meal.rawValue, "source": source]
        if isLabel { d["per_100"] = per100!.dict; d["grams"] = grams!; d["unit"] = unit; d["portions"] = 1 }
        else { d.merge(perPortion.dict) { _, b in b }; d["portions"] = portions }
        if let ref, !ref.isEmpty { d["ref"] = ref }
        if let note, !note.isEmpty { d["note"] = String(note.prefix(120)) }
        if let barcode { d["barcode"] = barcode }
        if edited { d["edited"] = true }
        if let photo { d["photo"] = photo }
        if let imageURL { d["image_url"] = imageURL }
        return d
    }
}

struct Toast: Identifiable, Equatable {
    let id = UUID()
    var text: String
    var error = false
    var undo: (() async throws -> Void)?
    static func == (a: Toast, b: Toast) -> Bool { a.id == b.id }
}

@Observable @MainActor
final class AppModel {
    var signedIn = false
    var checked = false
    /// The day on screen. It follows the calendar only while it's today (so after midnight Today is the new day), and stays
    /// put when they're looking at another day, however often they leave and come back.
    var date = Date().ymd { didSet { followsToday = date == Date().ymd } }
    private(set) var followsToday = true
    var day: Day?
    /// Each day's calories for the week strip, by date; kept on the phone so the strip isn't blank without signal.
    var stripKcal: [String: Double] = (AppConfig.shared.dictionary(forKey: "stripKcal") as? [String: Double]) ?? [:]
    @ObservationIgnored private var loadedWeeks: Set<String> = []  // Mondays whose days are in stripKcal
    var recent: [RecentFood] = []
    var usuals: [String: [RecentFood]] = [:]
    var saved: [SavedMeal] = []
    var review: WeekReview?      // Sunday afternoon and Monday: last week, looking back
    var switching = false        // a day not loaded yet: the last one stays, dimmed, until it is
    private var cache: [String: Day] = [:]
    private var prefetching = false
    private var refreshes = 0    // only the newest refresh's answer is shown
    private var lastRefresh = Date.distantPast
    var loadError: String?
    var toast: Toast? { didSet { if let t = toast, t != oldValue { present(t) } } }
    var toastHosts: [UUID] = []  // screens that can show a message; the last is on top
    private var toastTimer: Task<Void, Never>?
    var addFlow: AddRequest?     // the Add sheet
    var editing: Entry?
    var settings = false
    var lookBack: WeekReview?    // the week looking back, in full
    var copying: CopyRequest?    // Copy to other days
    var healthAsked = HealthSync.shared.asked
    var healthLater = AppConfig.shared.bool(forKey: "healthLater")
    var adding: Set<String> = [] // one-tap adds on their way (a second tap does nothing)
    var pending: [Pending] = Outbox.load()  // added with no signal, waiting to be sent
    private var flushing = false
    @ObservationIgnored private var network: NWPathMonitor?
    /// A day and meal Today should bring into view and light up for a moment (where something was just added).
    struct DayFocus: Equatable { var date: String; var meal: Meal?; var id = UUID() }
    var focus: DayFocus?

    var recapOffer: Date? = MonthStats.due   // last month's recap, offered on Today in the first days of a month
    var allFoods: [RecentFood] = []  // more of what they've had, for searching
    var streak: Streak?          // the growing sprig
    private var streakDays: [HistoryDay] = []
    private var streakGoal = 0.0
    private(set) var exercise: [String: Double] = [:]  // active kcal burned per day (Apple Health), whether or not it counts
    var leafDays: Set<String> = []  // days that grew a leaf
    var celebrating: Streak?     // the morning-after moment, once a day
    var sleepInsight: Int?       // kcal more (or less) after short nights, when it stands out
    var remindersAsked = Reminders.shared.asked
    var exerciseCounts = AppConfig.shared.bool(forKey: "exerciseCounts") {
        didSet { AppConfig.shared.set(exerciseCounts, forKey: "exerciseCounts"); Task { await loadStreak() } }
    }
    @ObservationIgnored private var changeObserver: NSObjectProtocol?
    @ObservationIgnored private var refreshSoon: Task<Void, Never>?

    var isToday: Bool { date == Date().ymd }
    var isFuture: Bool { date > Date().ymd }
    static let maxAhead = 60

    /// The week strip's pages: Monday-to-Sunday weeks from twelve weeks back to as far ahead as they can plan.
    var stripWeeks: [String] {
        let cal = Streak.weekCal
        // twelve weeks back, or further when they've opened an older day from the calendar
        let first = min(Streak.weekStart(cal.date(byAdding: .weekOfYear, value: -12, to: Date()) ?? Date()), Streak.weekStart(Date.fromYMD(date) ?? Date()))
        let last = Streak.weekStart(cal.date(byAdding: .day, value: Self.maxAhead, to: Date()) ?? Date())
        return stride(from: 0, through: (cal.dateComponents([.weekOfYear], from: first, to: last).weekOfYear ?? 0), by: 1)
            .compactMap { cal.date(byAdding: .weekOfYear, value: $0, to: first)?.ymd }
    }

    /// The Monday of the week a day is in.
    static func monday(of d: String) -> String { Streak.weekStart(Date.fromYMD(d) ?? Date()).ymd }

    /// Things they have eaten before that fit in what's left (planning: "what can I still have?"), leaving out what's already
    /// suggested in an empty meal on screen.
    var fits: [RecentFood] {
        guard let d = day, d.left > 60, !d.entries.isEmpty else { return [] }
        let logged = Set(d.entries.map { $0.name.lowercased() })
        let suggested = Set(Meal.allCases.filter { d.entries($0).isEmpty }.flatMap { usual(for: $0) }.map { $0.name.lowercased() })
        let all = recent.filter { $0.kcalEach <= d.left && !logged.contains($0.name.lowercased()) && !suggested.contains($0.name.lowercased()) }
        // protein well behind by the afternoon: the most protein for the calories first
        let sorted = proteinFirst ? all.sorted { $0.proteinEach / max($0.kcalEach, 1) > $1.proteinEach / max($1.kcalEach, 1) } : all
        return Array(sorted.prefix(8))
    }

    /// Today, from 2 pm, with protein under half its goal (and a protein goal at all).
    var proteinFirst: Bool {
        guard isToday, let d = day, d.goals.protein_g > 0, Calendar.current.component(.hour, from: Date()) >= 14 else { return false }
        return d.totals.protein_g < d.goals.protein_g * 0.5
    }

    func start() async {
        #if DEBUG
        // simulator tests and demos: -mockSignIn -server http://localhost:8811 signs in to tools/mock_ha.py without the web sign-in
        let args = ProcessInfo.processInfo.arguments
        if args.contains("-mockSignIn"), let i = args.firstIndex(of: "-server"), i + 1 < args.count, let url = URL(string: args[i + 1]),
           await HAClient.shared.server != url {
            try? await HAClient.shared.finishSignIn(server: url, code: "mock")
        }
        if args.contains("-resetLocal") {  // tests: start from a phone that's never answered anything
            for k in ["healthLater", "outbox", "portionMemory", "reminders", "remindersAsked", "celebrate", "cheered", "celebratedInApp", "exerciseCounts", "lastDay", "sleepInsight", "proteinNudge", "proteinPick", "activityWebhook"] {
                AppConfig.shared.removeObject(forKey: k)
            }
            UserDefaults.standard.removeObject(forKey: "calendarLook")
            healthLater = false; pending = []; exerciseCounts = false; remindersAsked = false
            if !args.contains("-celebrate") {  // the morning-after moment would cover every test; only the one that wants it sees it
                AppConfig.shared.set(Calendar.current.date(byAdding: .day, value: -1, to: Date())?.ymd, forKey: "celebratedInApp")
            }
        }
        #endif
        signedIn = await HAClient.shared.signedIn
        if signedIn, day == nil, let last = FoodAPI.lastDay() { day = last; cache[last.date] = last }  // today as last seen, at once
        checked = true
        guard signedIn else { return }
        observeChanges()
        watchNetwork()
        await flushOutbox()
        await refresh()
        await loadReview()
        await loadStreak()
        Task.detached { await HealthSync.shared.syncFood(); await HealthSync.shared.sendActivityThrottled() }
    }

    /// Back online: send whatever waited.
    private func watchNetwork() {
        guard network == nil else { return }
        let m = NWPathMonitor()
        m.pathUpdateHandler = { [weak self] path in
            guard path.status == .satisfied else { return }
            Task { @MainActor in await self?.flushOutbox() }
        }
        m.start(queue: .global(qos: .utility))
        network = m
    }

    /// Back in the app: a new day if midnight has passed while they were on Today, and fresh numbers unless they're
    /// only a moment old (pulling down Control Center shouldn't ask Home Assistant again).
    func resumed() async {
        let newDay = followsToday && date != Date().ymd && addFlow == nil
        if newDay { date = Date().ymd }
        await flushOutbox()
        guard signedIn, newDay || Date().timeIntervalSince(lastRefresh) > 30 else { return }
        await refresh()
        await loadReview()
        if newDay || streak == nil { await loadStreak() }
        Task.detached { await HealthSync.shared.syncFood() }
    }

    /// The day changed while the app was open (midnight, or a time-zone change).
    func dayChanged() {
        if followsToday && date != Date().ymd { date = Date().ymd; Task { await refresh(); await loadStreak() } }
    }

    func refresh() async {
        guard signedIn else { return }
        refreshes += 1
        let mine = refreshes, asked = date
        do {
            async let d = FoodAPI.day(asked)
            let mon = Self.monday(of: asked)
            async let h = FoodAPI.history(days: 7, through: Streak.weekCal.date(byAdding: .day, value: 6, to: Date.fromYMD(mon) ?? Date())?.ymd ?? asked)
            async let r = FoodAPI.recentAll()
            let (dd, hh, rr) = try await (d, h, r)
            cache[dd.date] = dd
            guard mine == refreshes else { return }  // a newer refresh is on its way: its answer will show
            lastRefresh = Date()
            for x in hh.days { stripKcal[x.date] = x.kcal }
            loadedWeeks.insert(mon)
            AppConfig.shared.set(stripKcal.filter { $0.key >= (Calendar.current.date(byAdding: .day, value: -120, to: Date())?.ymd ?? "") }, forKey: "stripKcal")
            recent = rr.foods; usuals = rr.usuals; saved = rr.saved; loadError = nil
            if dd.date == Date().ymd { Snapshot.save(from: dd) }
            guard asked == date else { return }  // they've moved on to another day: that one's answer will show
            day = dd; switching = false
            prefetch()
            if dd.date == Date().ymd {
                recomputeStreak()
                Reminders.shared.update(today: dd, usuals: usuals, recent: recent)
            }
        } catch HAClient.Failure.signedOut {
            signedIn = false
        } catch is CancellationError {
        } catch {
            guard mine == refreshes else { return }
            switching = false
            loadError = error.localizedDescription
            // never leave another day's food under this day's title
            if day?.date != date { day = cache[date] }
        }
    }

    /// The day before or after the one on screen (a swipe across the day).
    func step(days n: Int) async {
        guard let d = Date.fromYMD(date), let to = Calendar.current.date(byAdding: .day, value: n, to: d) else { return }
        await go(day: to.ymd)
    }

    /// Another week (a swipe across the week strip): today if it's this week, else the same weekday, never past the last
    /// day they can plan.
    func go(week mon: String) async {
        guard mon != Self.monday(of: date), let m = Date.fromYMD(mon), let cur = Date.fromYMD(date) else { return }
        let cal = Streak.weekCal
        let weekday = cal.dateComponents([.day], from: Streak.weekStart(cur), to: cur).day ?? 0
        var to = mon == Self.monday(of: Date().ymd) ? Date() : (cal.date(byAdding: .day, value: weekday, to: m) ?? m)
        if let limit = cal.date(byAdding: .day, value: Self.maxAhead, to: Date()), to > limit { to = limit }
        await go(day: to.ymd)
    }

    /// A week's calories for the strip, once (a swipe shows it before its day is opened).
    func loadWeek(_ mon: String) async {
        guard signedIn, !loadedWeeks.contains(mon), let m = Date.fromYMD(mon),
              let sun = Streak.weekCal.date(byAdding: .day, value: 6, to: m), let h = try? await FoodAPI.history(days: 7, through: sun.ymd) else { return }
        for x in h.days { stripKcal[x.date] = x.kcal }
        loadedWeeks.insert(mon)
    }

    /// Another day: at once from what's already loaded (the week strip's days are fetched ahead), then fresh.
    func go(day d: String) async {
        guard let limit = Calendar.current.date(byAdding: .day, value: Self.maxAhead, to: Date()), d <= limit.ymd else { return }
        date = d
        if let known = cache[d] { day = known; switching = false } else { switching = day != nil }
        await refresh()
    }

    /// A day already loaded (for previews like "3 items, 1,320 kcal").
    func known(_ d: String) -> Day? { cache[d] ?? (day?.date == d ? day : nil) }

    /// The week strip's days, loaded once in the background so tapping between them never waits.
    private func prefetch() {
        guard let m = Date.fromYMD(Self.monday(of: date)) else { return }
        let missing = (0..<7).compactMap { Streak.weekCal.date(byAdding: .day, value: $0, to: m)?.ymd }.filter { cache[$0] == nil }
        guard !missing.isEmpty, !prefetching else { return }
        prefetching = true
        Task {
            if let ds = try? await FoodAPI.days(missing) { for d in ds where cache[d.date] == nil { cache[d.date] = d } }
            prefetching = false
        }
    }

    /// Changes made from another screen (a copy, the share sheet): forget what's cached and look again, once.
    private func observeChanges() {
        guard changeObserver == nil else { return }
        changeObserver = NotificationCenter.default.addObserver(forName: .foodDiaryChanged, object: nil, queue: .main) { [weak self] _ in
            MainActor.assumeIsolated { self?.changedElsewhere() }
        }
    }

    private func changedElsewhere() {
        cache = cache.filter { $0.key == date }
        loadedWeeks = []
        refreshSoon?.cancel()
        refreshSoon = Task {
            try? await Task.sleep(for: .milliseconds(400))
            guard !Task.isCancelled else { return }
            await refresh()
            await loadStreak()  // a change to an earlier day can grow (or stop) the sprig
        }
    }

    func open(_ route: Route) {
        editing = nil; settings = false
        if route == .today { addFlow = nil; return }
        if !isToday { date = Date().ymd; Task { await refresh() } }
        addFlow = AddRequest(route: route, date: Date().ymd, meal: nil)
    }

    /// Add on the day on screen (from the Add bar, a meal's Add, or Scan).
    func add(_ route: Route = .add, meal: Meal? = nil) {
        addFlow = AddRequest(route: route, date: date, meal: meal)
    }

    // ---------- changing the diary ----------

    /// "Added Skyr to breakfast", "Added Skyr (½) to tomorrow's breakfast".
    static func added(_ name: String, _ meal: Meal, _ date: String, portions: Double = 1) -> String {
        "Added \(name)\(portions == 1 ? "" : " (\(Fmt.portions(portions)))") to \(DayName.meal(meal, on: date))"
    }

    /// Add to the diary, or, with no signal, keep it on the phone and send it when Home Assistant can be reached.
    @discardableResult
    private func send(_ payload: [String: Any], name: String, meal: Meal, date: String, quiet: Bool = false) async -> Bool {
        let portions = (payload["per_100"] == nil ? payload["portions"] as? Double : nil) ?? 1
        do {
            let r = try await FoodAPI.log(payload)
            if !quiet { toast = Toast(text: Self.added(name, meal, date, portions: portions)) { try await FoodAPI.delete(r.entry.id, date: r.date) } }
            changed()
            if !quiet && date == self.date {  // bring the meal it went into into view, lit for a moment, once the sheet is down
                Task { try? await Task.sleep(for: .milliseconds(450)); focus = DayFocus(date: date, meal: meal) }
            }
            return true
        } catch HAClient.Failure.offline {
            guard let p = Pending(payload, name: name, meal: meal, date: date) else { return false }
            pending.append(p); Outbox.save(pending)
            toast = Toast(text: "\(name) is waiting for signal") { [weak self] in
                await self?.unqueue(p.id)
            }
            return true
        } catch {
            show(error)
            return false
        }
    }

    private func unqueue(_ id: UUID) {
        pending.removeAll { $0.id == id }
        Outbox.save(pending)
    }

    func pending(on date: String) -> [Pending] { pending.filter { $0.date == date } }

    /// A waiting food Home Assistant turned down: try it once more now.
    func retry(_ p: Pending) async {
        guard let payload = p.payload else { unqueue(p.id); return }
        do {
            _ = try await FoodAPI.log(payload)
            unqueue(p.id)
            toast = Toast(text: "Added \(p.name)")
            changed()
        } catch { show(error) }
    }

    /// Let a waiting food go, with Undo.
    func drop(_ p: Pending) {
        unqueue(p.id)
        toast = Toast(text: "Removed \(p.name)") { [weak self] in
            guard let self else { return }
            var back = p; back.failed = nil; back.tries = 0
            self.pending.append(back); Outbox.save(self.pending)
        }
    }

    /// Send what waited, oldest first; stop at the first sign of still being offline.
    func flushOutbox() async {
        guard signedIn, !flushing, !pending.isEmpty else { return }
        flushing = true; defer { flushing = false }
        var sent = 0
        for p in pending {
            guard let payload = p.payload else { unqueue(p.id); continue }
            do {
                _ = try await FoodAPI.log(payload)
                unqueue(p.id); sent += 1
            } catch HAClient.Failure.offline {
                break
            } catch HAClient.Failure.network {
                break
            } catch {
                if p.tries >= 2, let i = pending.firstIndex(where: { $0.id == p.id }) {
                    // kept, marked, with Try again and Remove on its row: food they meant to log is never just dropped
                    pending[i].failed = true; Outbox.save(pending)
                } else if let i = pending.firstIndex(where: { $0.id == p.id }) {
                    pending[i].tries += 1; Outbox.save(pending)
                }
            }
        }
        if sent > 0 {
            toast = Toast(text: sent == 1 ? "Back online: added what was waiting" : "Back online: added the \(sent) things that were waiting")
            changed()
        }
    }

    func log(_ draft: Draft, date: String, quiet: Bool = false) async -> Bool {
        var p = draft.payload
        p["date"] = date
        if !draft.isLabel { PortionMemory.remember(draft.portions, for: draft.name) }
        return await send(p, name: draft.name, meal: draft.meal, date: date, quiet: quiet)
    }

    /// Something eaten before, one tap: into `meal` on `date` (the day on screen unless they're adding elsewhere).
    func again(_ f: RecentFood, meal: Meal, date: String? = nil) async {
        let date = date ?? self.date
        guard !adding.contains(f.id) else { return }
        adding.insert(f.id); defer { adding.remove(f.id) }
        await send(FoodAPI.againPayload(f, meal: meal, date: date), name: f.name, meal: meal, date: date)
    }

    /// One of their saved meals, as one entry, in that meal of the day.
    func logSaved(_ s: SavedMeal, meal: Meal, date: String? = nil) async {
        let date = date ?? self.date
        guard !adding.contains("saved-\(s.id)") else { return }
        adding.insert("saved-\(s.id)"); defer { adding.remove("saved-\(s.id)") }
        await send(FoodAPI.savedPayload(s, meal: meal, date: date), name: s.name, meal: meal, date: date)
    }

    /// Rename a saved meal or move it to another meal, with Undo.
    func updateSaved(_ s: SavedMeal, name: String? = nil, meal: Meal? = nil) async {
        let clean = name?.trimmingCharacters(in: .whitespacesAndNewlines)
        guard clean?.isEmpty != true else { return }
        do {
            let new = try await FoodAPI.updateSaved(s.id, name: clean, meal: meal)
            let text = clean != nil ? "Renamed to \(new.name)" : "Moved \(new.name) to \(new.meal)"
            toast = Toast(text: text) { _ = try await FoodAPI.updateSaved(s.id, name: s.name, meal: Meal(rawValue: s.meal)) }
            await refresh()
        } catch { show(error) }
    }

    /// A quick change of how much (from a swipe on the row): portions, or grams for a label food, with Undo.
    func setAmount(_ e: Entry, date: String, portions: Double? = nil, grams: Double? = nil) async {
        var c: [String: Any] = [:]
        if let portions { c["portions"] = portions } else if let grams { c["grams"] = grams.rounded() }
        guard !c.isEmpty else { return }
        do {
            try await FoodAPI.update(e.id, date: date, c)
            if let portions { PortionMemory.remember(portions, for: e.name) }
            let what = portions.map { "\(Fmt.portions($0)) \($0 <= 1 ? "portion" : "portions")" } ?? "\(Int((grams ?? 0).rounded())) \(e.unit ?? "g")"
            let back: [String: Any] = portions != nil ? ["portions": e.portions] : ["grams": e.grams ?? 0]
            toast = Toast(text: "\(e.name): \(what)") { try await FoodAPI.update(e.id, date: date, back) }
            changed()
        } catch { show(error) }
    }

    func saveMeal(_ name: String, meal: Meal) async {
        do {
            let s = try await FoodAPI.saveMeal(name, meal: meal, date: date)
            toast = Toast(text: "Saved \(s.name)") { try await FoodAPI.deleteSaved(s.id) }
            await refresh()
        } catch { show(error) }
    }

    func deleteSaved(_ s: SavedMeal) async {
        do {
            try await FoodAPI.deleteSaved(s.id)
            toast = Toast(text: "Deleted \(s.name)")
            await refresh()
        } catch { show(error) }
    }

    /// What they usually have in a meal: their saved meals for it first, then foods they have most days. Two at most.
    func usual(for meal: Meal) -> [Usual] {
        let mine = saved.filter { $0.meal == meal.rawValue }.map(Usual.saved)
        let often = (usuals[meal.rawValue] ?? []).map(Usual.food)
        return Array((mine + often).prefix(2))
    }

    // ---------- the growing sprig ----------

    /// The days before today, for the sprig, and the exercise Health has for them.
    func loadStreak() async {
        guard signedIn, let yesterday = Calendar.current.date(byAdding: .day, value: -1, to: Date())?.ymd,
              let h = try? await FoodAPI.history(days: 60, through: yesterday) else { return }
        streakDays = h.days; streakGoal = h.goals.kcal
        exercise = HealthSync.shared.asked ? await HealthSync.shared.activeKcal(days: 61) : [:]
        recomputeStreak()
        celebrateIfDue()
    }

    /// The last two months of food against the nights before them (from Apple Health), for the look back.
    func loadSleepInsight() async {
        #if DEBUG
        if ProcessInfo.processInfo.arguments.contains("-fakeSleep") { sleepInsight = 250; return }
        #endif
        guard HealthSync.shared.asked, let yesterday = Calendar.current.date(byAdding: .day, value: -1, to: Date()),
              let start = Calendar.current.date(byAdding: .day, value: -60, to: Date()),
              let h = try? await FoodAPI.history(days: 60, through: yesterday.ymd) else { return }
        let sleep = await HealthSync.shared.activity(from: start, to: yesterday).compactMapValues(\.sleepMin)
        sleepInsight = SleepInsight.compute(food: h.days, sleep: sleep)
    }

    /// The extra room exercise gives a day (only when they count it).
    func room(_ d: String) -> Double { exerciseCounts ? (exercise[d] ?? 0) : 0 }
    var goal: Double { (day?.date == Date().ymd ? day?.goals.kcal : nil) ?? streakGoal }

    /// Leaves for the days in a row (to yesterday) they landed within their goal, and whether today's on track so far.
    func recomputeStreak() {
        guard streakGoal > 0 else { return }
        let g = goal
        let count = Streak.count(streakDays, goal: g, room: exerciseCounts ? exercise : [:])
        let helped = streakDays.suffix(count).filter { $0.kcal > g * Theme.overBand }.count
        leafDays = Set(streakDays.filter { Streak.onTarget($0.kcal, goal: g, room: room($0.date)) }.map(\.date))
        let today = known(Date().ymd)?.totals.kcal ?? 0
        let onTrack = today > 0 && today <= g * Theme.overBand + room(Date().ymd)
        let recent = streakDays.suffix(13).map { leafDays.contains($0.date) }
        streak = Streak(days: count, todayOnTrack: onTrack, helpedByExercise: helped, recent: recent)
    }

    /// The numbers behind a day's leaf: eaten, the goal, exercise burned, and the most that still counts.
    func leafNumbers(_ d: String) -> (eaten: Double, goal: Double, burned: Double, low: Double, high: Double)? {
        let eaten = d == Date().ymd ? (known(d)?.totals.kcal ?? 0) : (streakDays.first { $0.date == d }?.kcal ?? known(d)?.totals.kcal ?? 0)
        guard goal > 0 else { return nil }
        return (eaten, goal, exercise[d] ?? 0, goal * 0.75, goal * Theme.overBand + room(d))
    }

    /// The morning after a day on target, the first time the app opens: a moment on Today (and, in the background, the cheer).
    private func celebrateIfDue() {
        guard let s = streak, let yesterday = Calendar.current.date(byAdding: .day, value: -1, to: Date())?.ymd,
              s.days > 0 && leafDays.contains(yesterday),
              AppConfig.shared.string(forKey: "celebratedInApp") != yesterday else { return }
        #if DEBUG
        let forced = ProcessInfo.processInfo.arguments.contains("-celebrate")
        #else
        let forced = false
        #endif
        guard Reminders.shared.celebrate || forced else { return }  // "Celebrate good days" off: no moment at all
        AppConfig.shared.set(yesterday, forKey: "celebratedInApp")
        celebrating = s
        Reminders.shared.markCheered()  // seen here, so the morning notification isn't sent as well
    }

    // ---------- the week looking back ----------

    /// The week to look back on ends on a Sunday: shown from Sunday 3 pm (through Saturday, as Sunday is still being
    /// eaten) and all Monday (the whole week). Hidden for that week once they close it.
    private var reviewWeek: (end: String, through: String, days: Int)? {
        let now = Date(), cal = Calendar.current
        let yesterday = cal.date(byAdding: .day, value: -1, to: now)!.ymd
        #if DEBUG
        if ProcessInfo.processInfo.arguments.contains("-showReview") { return (yesterday, yesterday, 7) }
        #endif
        switch cal.component(.weekday, from: now) {
        case 1 where cal.component(.hour, from: now) >= 15: return (now.ymd, yesterday, 6)
        case 2: return (yesterday, yesterday, 7)
        default: return nil
        }
    }
    private var reviewKey: String?

    func loadReview() async {
        guard let w = reviewWeek else { review = nil; return }
        #if DEBUG
        if ProcessInfo.processInfo.arguments.contains("-showReview") { AppConfig.shared.removeObject(forKey: "reviewSeen-\(w.end)") }
        #endif
        guard !AppConfig.shared.bool(forKey: "reviewSeen-\(w.end)") else { review = nil; return }
        reviewKey = "reviewSeen-\(w.end)"
        if let r = try? await FoodAPI.review(through: w.through, days: w.days), r.days_logged >= 3 { review = r } else { review = nil }
    }

    func dismissReview() {
        if let k = reviewKey { AppConfig.shared.set(true, forKey: k) }
        withAnimation { review = nil }
    }

    /// Copy another day's food onto this one (their usual weekdays), with Undo.
    func copy(from other: String) async {
        let here = date
        do {
            let r = try await FoodAPI.copyDay(from: other, to: [here])
            let n = r.added[here] ?? 0
            guard n > 0 else { toast = Toast(text: "Nothing to copy from \(DayName.of(other))"); return }
            toast = Toast(text: "Copied \(n) item\(n == 1 ? "" : "s") from \(DayName.of(other))") { try await FoodAPI.undoCopy(r.token) }
            changed()
        } catch { show(error) }
    }

    /// One meal of another day copied onto this meal (yesterday's breakfast again), with Undo.
    func copyMeal(from other: String, meal: Meal, to here: String) async {
        do {
            let r = try await FoodAPI.copyDay(from: other, to: [here], meal: meal)
            let n = r.added[here] ?? 0
            guard n > 0 else { toast = Toast(text: "Nothing to copy from \(DayName.of(other))"); return }
            toast = Toast(text: "Added \(DayName.of(other))'s \(meal.single.lowercased()) to \(DayName.meal(meal, on: here))") { try await FoodAPI.undoCopy(r.token) }
            changed()
        } catch { show(error) }
    }

    /// Take it out of the diary, with Undo that puts it back exactly as it was (photo, note and all) on the day it was on.
    func delete(_ e: Entry, date: String? = nil) async {
        let date = date ?? self.date
        do {
            try await FoodAPI.delete(e.id, date: date)
            toast = Toast(text: "Removed \(e.name)") { try await FoodAPI.log(e.restorePayload(date: date)) }
            changed()
        } catch { show(error) }
    }

    func update(_ e: Entry, date: String, _ changes: [String: Any]) async -> Bool {
        do {
            try await FoodAPI.update(e.id, date: date, changes)
            changed()
            return true
        } catch { show(error); return false }
    }

    func setGoals(_ g: [String: Double]) async -> Bool {
        do { try await FoodAPI.setGoals(g); toast = Toast(text: "Goals saved"); changed(); return true } catch { show(error); return false }
    }

    /// After a change: Today and the week strip catch up, and Apple Health follows. Nothing waits for it, so sheets close
    /// at once and Undo is on screen straight away.
    func changed() {
        Task {
            await refresh()
            await HealthSync.shared.syncFood()
        }
    }

    /// For searching in Add food: more of what's been had (once a session; cheap after that).
    func loadSearch() async {
        if allFoods.isEmpty, let f = try? await FoodAPI.recentForSearch() { allFoods = f }
    }

    func connectHealth() async {
        await HealthSync.shared.requestAccess()
        healthAsked = HealthSync.shared.asked
        guard HealthSync.shared.foodAllowed else {
            toast = Toast(text: "Food isn't going to Apple Health. Turn it on in Health → Sharing → Apps → Food.", error: true)
            return
        }
        HealthSync.shared.startBackgroundDelivery()
        toast = Toast(text: "Apple Health connected")
        await HealthSync.shared.syncFood()
        await HealthSync.shared.sendActivity()
    }

    func remindersAnswered() {
        withAnimation { remindersAsked = true }
        if let d = known(Date().ymd) { Reminders.shared.update(today: d, usuals: usuals, recent: recent) }
    }

    func healthNotNow() {
        AppConfig.shared.set(true, forKey: "healthLater")
        withAnimation { healthLater = true }
    }

    func show(_ error: Error) {
        if error is CancellationError { return }
        if case HAClient.Failure.signedOut = error { signedIn = false }
        toast = Toast(text: error.localizedDescription, error: true)
    }

    // ---------- the message pill ----------

    private func present(_ t: Toast) {
        AccessibilityNotification.Announcement(t.undo == nil ? t.text : "\(t.text). Undo is available.").post()
        UINotificationFeedbackGenerator().notificationOccurred(t.error ? .error : .success)
        toastTimer?.cancel()
        // long enough to read it and reach Undo; with VoiceOver, long enough to get to it by swiping
        let seconds: Double = UIAccessibility.isVoiceOverRunning ? 20 : t.error ? 6 : t.undo == nil ? 3.5 : 7
        toastTimer = Task {
            try? await Task.sleep(for: .seconds(seconds))
            guard !Task.isCancelled, toast?.id == t.id else { return }
            toast = nil
        }
    }

    /// Undo what the message is about; if Home Assistant can't, say so (it would be worse to look undone and not be).
    func undoToast() {
        guard let undo = toast?.undo else { return }
        toast = nil
        Task {
            do {
                try await undo()
                UINotificationFeedbackGenerator().notificationOccurred(.success)
                await refresh()
            } catch {
                toast = Toast(text: "Couldn't undo that. \(error.localizedDescription)", error: true)
            }
        }
    }

    func signOut() async {
        await HAClient.shared.signOut()
        signedIn = false; day = nil; stripKcal = [:]; loadedWeeks = []; recent = []; settings = false; cache = [:]
        AppConfig.shared.removeObject(forKey: "lastDay"); AppConfig.shared.removeObject(forKey: "snapshot")
        #if canImport(WidgetKit)
        WidgetCenter.shared.reloadAllTimelines()
        #endif
    }
}
