import SwiftUI

/// The day: which day (the week strip), what's left, what fits, then each meal with its own Add. Planning ahead is the same
/// screen on a later day.
struct TodayView: View {
    @Environment(AppModel.self) private var model
    @State private var showingMonth = false
    @State private var copyFrom: String?

    var body: some View {
        @Bindable var m = model
        NavigationStack {
            ScrollViewReader { proxy in
            List {
                Section { WeekStrip().dynamicTypeSize(...DynamicTypeSize.xxLarge) }  // seven days must fit across
                    .listRowBackground(Color.clear).listRowInsets(EdgeInsets(top: 0, leading: 4, bottom: 0, trailing: 4))
                if let err = model.loadError, model.day != nil {
                    Section { ErrorRow(text: err) }.listRowBackground(Theme.overSoft)
                }
                if let d = model.day {
                    Section { SummaryCard(day: d).opacity(model.switching ? 0.45 : 1).modifier(DaySwipe()) }
                        .listRowBackground(Color.clear).listRowInsets(EdgeInsets(top: 4, leading: 0, bottom: 4, trailing: 0))
                    // their food first; what could be added, and the offers, come after it
                    ForEach(Meal.allCases) { meal in MealSection(day: d, meal: meal).opacity(model.switching ? 0.45 : 1) }
                    if d.entries.isEmpty && model.isPast {
                        Section { Text("Nothing was logged that day.").font(.body).foregroundStyle(Theme.muted).padding(.vertical, 6) }
                            .listRowBackground(Color.clear)
                    }
                    if !model.fits.isEmpty && model.isToday {
                        Section { FitsRow() }.listRowBackground(Color.clear).listRowInsets(EdgeInsets(top: 2, leading: 0, bottom: 2, trailing: 0))
                    }
                    if let o = offer {
                        Section {
                            switch o {
                            case .review(let r): ReviewCard(review: r)
                            case .health: HealthCard()
                            case .reminders: RemindersOffer()
                            case .recap(let m): RecapOffer(month: m)
                            }
                        }
                        .listRowBackground(Color.clear).listRowInsets(EdgeInsets(top: 4, leading: 0, bottom: 4, trailing: 0))
                    }
                } else if let err = model.loadError {
                    Section { ErrorRow(text: err) }.listRowBackground(Theme.overSoft)
                } else {
                    Section { HStack { Spacer(); ProgressView(); Spacer() }.padding(40) }.listRowBackground(Color.clear)
                }
                Section { Color.clear.frame(height: 72) }.listRowBackground(Color.clear)
            }
            .listStyle(.insetGrouped)
            .listSectionSpacing(14)
            .contentMargins(.top, 0, for: .scrollContent)
            .scrollContentBackground(.hidden)
            .background(PatternedPage())
            .refreshable { await model.refresh() }
            .onChange(of: model.focus) { _, f in
                // something just added: bring its meal into view once its day is open, let it glow, then let go
                guard let f, let meal = f.meal else { return }
                Task {
                    try? await Task.sleep(for: .milliseconds(350))
                    withAnimation(.snappy) { proxy.scrollTo("meal-\(meal.rawValue)", anchor: .center) }
                    try? await Task.sleep(for: .seconds(1.8))
                    if model.focus == f { withAnimation(.easeOut(duration: 0.6)) { model.focus = nil } }
                }
            }
            }
            .navigationTitle(model.title)
            .navigationBarTitleDisplayMode(.inline)
            .toolbarTitleMenu { dayMenu }
            .toolbar {
                ToolbarItemGroup(placement: .topBarTrailing) {
                    Button { showingMonth = true } label: { Image(systemName: "calendar") }.accessibilityLabel("This month")
                    Button { model.settings = true } label: { Image(systemName: "gearshape") }.accessibilityLabel("Settings")
                }
            }
            .safeAreaInset(edge: .bottom) { AddBar() }
            .overlay {
                if let s = model.celebrating {
                    Celebration(streak: s) { withAnimation { model.celebrating = nil } }.transition(.opacity)
                }
            }
            .confirmationDialog(copyFrom.map { "Copy \(DayName.of($0))'s food to \(DayName.of(model.date))?" } ?? "",
                                isPresented: Binding(get: { copyFrom != nil }, set: { if !$0 { copyFrom = nil } }), titleVisibility: .visible) {
                Button("Copy") { if let f = copyFrom { Task { await model.copy(from: f) } }; copyFrom = nil }
                Button("Cancel", role: .cancel) { copyFrom = nil }
            } message: {
                Text(copyFrom.map(copySummary) ?? "")
            }
            .sheet(item: $m.addFlow) { r in AddFlowView(request: r) }
            .sheet(item: $m.editing) { e in EntrySheet(entry: e, date: m.date) }
            .sheet(isPresented: $m.settings) { SettingsView() }
            .sheet(isPresented: $showingMonth) { MonthView() }
            .sheet(item: $m.lookBack) { r in ReviewSheet(review: r) }
            .sheet(item: $m.copying) { c in CopySheet(request: c) }
        }
    }

    /// Today has one place for an offer, and they take turns (most useful first): last week, then Apple Health. Only the
    /// summary and the meals are there every day.
    enum Offer { case recap(Date), review(WeekReview), health, reminders }

    private var offer: Offer? {
        guard model.isToday else { return nil }
        if let m = model.recapOffer { return .recap(m) }
        if let r = model.review { return .review(r) }
        if !model.healthAsked && !model.healthLater && HealthSync.shared.available { return .health }
        if !model.remindersAsked { return .reminders }
        return nil
    }

    /// The day's own menu, under its title: back to today, the month, and copying this day out or another day's food in.
    @ViewBuilder private var dayMenu: some View {
        let empty = model.day?.entries.isEmpty ?? true
        Section {
            if !model.isToday { Button { Task { await model.go(day: Date().ymd) } } label: { Label("Back to today", systemImage: "sun.max") } }
            Button { showingMonth = true } label: { Label("This month", systemImage: "calendar") }
        }
        Section {
                Button { model.copying = CopyRequest(from: model.date) } label: { Label("Copy this day to…", systemImage: "arrow.up.doc.on.clipboard") }
                    .disabled(empty)
                Menu {
                    ForEach(Meal.allCases.filter { !(model.day?.entries($0).isEmpty ?? true) }) { m in
                        Button(m.single) { model.copying = CopyRequest(from: model.date, meal: m) }
                    }
                } label: { Label("Copy a meal to…", systemImage: "square.on.square") }
                .disabled(empty)
            } header: { Text(empty ? "Nothing to copy from this day yet" : "From this day") }
            Section {
                if let y = model.dayOffset(-1) { Button { copyFrom = y } label: { Label("Copy from \(DayName.of(y))…", systemImage: "arrow.down.doc") } }
                if let w = model.dayOffset(-7), let d = Date.fromYMD(w) {
                    Button { copyFrom = w } label: { Label("Copy from last \(d.formatted(.dateTime.weekday(.wide)))…", systemImage: "arrow.uturn.backward") }
                }
        } header: { Text("Into this day") }
    }

    private func copySummary(_ from: String) -> String {
        guard let d = model.known(from) else { return "It's added to what's already there. You can undo it." }
        if d.entries.isEmpty { return "Nothing was logged that day." }
        return "\(d.entries.count) item\(d.entries.count == 1 ? "" : "s"), \(Fmt.kcal(d.totals.kcal)) kcal, added to what's already there. You can undo it."
    }
}

extension AppModel {
    var isPast: Bool { date < Date().ymd }
    var title: String {
        guard let d = Date.fromYMD(date) else { return date }
        let cal = Calendar.current
        if cal.isDateInToday(d) { return "Today" }
        if cal.isDateInTomorrow(d) { return "Tomorrow" }
        if cal.isDateInYesterday(d) { return "Yesterday" }
        return d.formatted(.dateTime.weekday(.wide).day().month(.abbreviated))
    }
    func dayOffset(_ n: Int) -> String? {
        Date.fromYMD(date).flatMap { Calendar.current.date(byAdding: .day, value: n, to: $0) }?.ymd
    }
    /// The meal one-tap food goes in on the day on screen: the time of day today, else breakfast.
    var defaultMeal: Meal { isToday ? .now : .breakfast }
}

struct SectionTitle: View {
    var text: String
    var detail: String?
    var trailing: String?
    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Text(text).heading(.title3).foregroundStyle(Theme.text)
            if let detail { Text(detail).font(.subheadline).foregroundStyle(Theme.muted) }
            Spacer()
            if let trailing { Text(trailing).font(.number(.callout, .medium)).foregroundStyle(Theme.muted) }
        }
        .textCase(nil)
    }
}

/// Home Assistant couldn't be reached: what went wrong, and a way to try again (what's on screen may be from earlier).
struct ErrorRow: View {
    @Environment(AppModel.self) private var model
    var text: String
    @State private var busy = false
    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 12) {
            Image(systemName: "wifi.exclamationmark").foregroundStyle(Theme.over).accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 8) {
                Text(model.day == nil ? text : "Couldn't update. \(text)").font(.body).foregroundStyle(Theme.text)
                Button { Task { busy = true; await model.refresh(); busy = false } } label: {
                    HStack(spacing: 8) { if busy { ProgressView() }; Text(busy ? "Trying…" : "Try again") }
                        .font(.body.weight(.semibold)).foregroundStyle(Theme.accent).frame(minHeight: 44)
                }
                .buttonStyle(.plain).disabled(busy)
            }
        }
        .padding(.vertical, 4)
    }
}

/// Monday-to-Sunday weeks of small rings (filled to each day's goal), swiped like pages; the chosen day is lifted. Tap a day
/// to open it; swipe to another week and its same weekday opens (today, on this week).
struct WeekStrip: View {
    @Environment(AppModel.self) private var model
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @ScaledMetric(relativeTo: .callout) private var ring: CGFloat = 38
    @State private var page: String?

    var body: some View {
        ScrollView(.horizontal) {
            LazyHStack(spacing: 0) {
                ForEach(model.stripWeeks, id: \.self) { mon in week(mon).containerRelativeFrame(.horizontal) }
            }
            .scrollTargetLayout()
        }
        .scrollTargetBehavior(.paging)
        .scrollIndicators(.hidden)
        .scrollPosition(id: $page)
        .onAppear { page = AppModel.monday(of: model.date) }
        .onChange(of: model.date) { _, d in
            let mon = AppModel.monday(of: d)
            if page != mon { withAnimation(reduceMotion ? nil : .snappy) { page = mon } }
        }
        .onChange(of: page) { _, mon in
            guard let mon, mon != AppModel.monday(of: model.date) else { return }
            Task { await model.go(week: mon) }
        }
        .task(id: page) { if let page { await model.loadWeek(page) } }
        .sensoryFeedback(.selection, trigger: page)
        .animation(reduceMotion ? nil : .snappy(duration: 0.22), value: model.date)
    }

    /// Long-press on a day: copy or move its food, or bring it into the day on screen.
    @ViewBuilder private func dayActions(_ d: String) -> some View {
        let name = DayName.of(d), empty = model.known(d)?.entries.isEmpty ?? false
        Section {
            Button { model.copying = CopyRequest(from: d) } label: { Label("Copy \(name) to…", systemImage: "doc.on.doc") }.disabled(empty)
            Button { model.copying = CopyRequest(from: d, move: true) } label: { Label("Move \(name) to…", systemImage: "arrow.right.doc.on.clipboard") }
                .disabled(empty)
            if d != model.date {
                Button { Task { await model.copy(from: d) } } label: { Label("Copy \(name) to \(DayName.of(model.date))", systemImage: "arrow.down.doc") }
                    .disabled(empty)
            }
        }
    }

    private func week(_ mon: String) -> some View {
        let goal = model.day?.goals.kcal ?? 2000
        let start = Date.fromYMD(mon) ?? Date()
        let days = (0..<7).compactMap { Streak.weekCal.date(byAdding: .day, value: $0, to: start)?.ymd }
        return HStack(spacing: 2) {
            ForEach(days, id: \.self) { d in dayButton(d, goal: goal) }
        }
    }

    /// One day: a tap opens it; a long-press its own menu (a context menu in a list row would be the whole row's).
    private func dayButton(_ d: String, goal: Double) -> some View {
        let kcal = d == model.day?.date ? (model.day?.totals.kcal ?? 0) : (model.stripKcal[d] ?? 0)
        let sel = d == model.date
        return Menu { dayActions(d) } label: { dayFace(d, kcal: kcal, goal: goal, sel: sel) } primaryAction: { Task { await model.go(day: d) } }
            .menuIndicator(.hidden)
            .buttonStyle(.plain)
            .disabled(d > (Calendar.current.date(byAdding: .day, value: AppModel.maxAhead, to: Date())?.ymd ?? d))
            .accessibilityLabel(dayLabel(d, kcal: kcal, goal: goal))
            .accessibilityHint("Opens the day. Touch and hold to copy or move it.")
            .accessibilityAction(named: "Copy to other days") { model.copying = CopyRequest(from: d) }
            .accessibilityAction(named: "Move to another day") { model.copying = CopyRequest(from: d, move: true) }
            .accessibilityAddTraits(sel ? .isSelected : [])
            .accessibilityShowsLargeContentViewer()
    }

    private func dayFace(_ d: String, kcal: Double, goal: Double, sel: Bool) -> some View {
        let isToday = d == Date().ymd, date = Date.fromYMD(d), size = min(ring, 46)
        return VStack(spacing: 6) {
            Text(date?.formatted(.dateTime.weekday(.abbreviated)) ?? "").font(.footnote.weight(isToday ? .bold : .medium))
                .foregroundStyle(isToday ? Theme.accent : Theme.muted).lineLimit(1).fixedSize()
            ZStack {
                if model.leafDays.contains(d) {
                    LeafShape().fill(Theme.ring).frame(width: 7, height: 12).rotationEffect(.degrees(35)).offset(x: size / 2, y: -size / 2 + 2)
                } else if isToday && model.streak?.todayOnTrack == true {  // growing: the leaf is earned when the day ends on target
                    LeafShape().stroke(Theme.ring, lineWidth: 1.3).frame(width: 7, height: 12).rotationEffect(.degrees(35)).offset(x: size / 2, y: -size / 2 + 2)
                }
                CalorieRing(eaten: kcal, goal: goal, lineWidth: 4)
                Text(date?.formatted(.dateTime.day()) ?? "").font(.number(.callout, sel ? .bold : .medium)).lineLimit(1).fixedSize()
                    .foregroundStyle(sel ? Theme.text : Theme.soft)
            }
            .frame(width: size, height: size)
        }
        .frame(maxWidth: .infinity).padding(.vertical, 8)
        .background(RoundedRectangle(cornerRadius: 16, style: .continuous).fill(sel ? Theme.panel : .clear))
        .overlay(RoundedRectangle(cornerRadius: 16, style: .continuous).strokeBorder(sel ? Theme.raised2 : .clear, lineWidth: 1.5))
        .shadow(color: .black.opacity(sel ? 0.06 : 0), radius: 6, y: 2)
        .contentShape(Rectangle())
    }

    private func dayLabel(_ d: String, kcal: Double, goal: Double) -> String {
        let when = Date.fromYMD(d)?.formatted(.dateTime.weekday(.wide).day().month()) ?? d
        let over = kcal > goal * Theme.overBand ? ", over" : "", leaf = model.leafDays.contains(d) ? ", grew a leaf" : ""
        return "\(d == Date().ymd ? "Today, " : "")\(when): \(Fmt.kcal(kcal)) calories\(over)\(leaf)"
    }
}

/// A swipe across the day's card goes to the day after (left) or before (right); the card leans with the finger.
/// The food rows keep their own swipes (portions, remove), so the day swipe lives here and not on the whole page.
struct DaySwipe: ViewModifier {
    @Environment(AppModel.self) private var model
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var lean: CGFloat = 0

    func body(content: Content) -> some View {
        content
            .offset(x: lean)
            .simultaneousGesture(
                DragGesture(minimumDistance: 24)
                    .onChanged { v in
                        guard abs(v.translation.width) > abs(v.translation.height) * 1.5 else { return }
                        lean = reduceMotion ? 0 : v.translation.width * 0.3
                    }
                    .onEnded { v in
                        withAnimation(.snappy) { lean = 0 }
                        let dx = v.predictedEndTranslation.width
                        guard abs(v.translation.width) > abs(v.translation.height) * 1.5, abs(dx) > 90 else { return }
                        Task { await model.step(days: dx < 0 ? 1 : -1) }
                    }
            )
            .sensoryFeedback(.selection, trigger: model.date)
    }
}

/// The day at a glance: the food rings (calories, protein, carbs, fat) beside what's left and their lines, then the sprig.
struct SummaryCard: View {
    @Environment(AppModel.self) private var model
    @Environment(\.dynamicTypeSize) private var typeSize
    @ScaledMetric(relativeTo: .title) private var ringSize: CGFloat = 128
    var day: Day

    var body: some View {
        let waiting = model.pending(on: day.date)
        let eaten = day.totals.kcal + waiting.reduce(0) { $0 + $1.kcal }, left = day.goals.kcal - eaten
        let over = left < 0
        let clearlyOver = eaten > day.goals.kcal * Theme.overBand  // a little over still grows a leaf, so it isn't shown in clay
        let t = day.totals
        let planned = day.entries.filter { $0.stillPlanned(on: day.date) }.reduce(Nutrients()) { $0.adding($1.totals) }
        let word = heroWord(over: over, eaten: eaten, planned: planned.kcal)
        let stacked = typeSize.isAccessibilitySize
        VStack(alignment: .leading, spacing: 18) {
            (stacked ? AnyLayout(VStackLayout(alignment: .leading, spacing: 16)) : AnyLayout(HStackLayout(spacing: 16))) {
                FoodRings(kcal: eaten, protein: t.protein_g, carbs: t.carbs_g, fat: t.fat_g, goals: day.goals, planned: planned)
                    .frame(width: min(ringSize, 190), height: min(ringSize, 190))
                VStack(alignment: .leading, spacing: 0) {
                    Text(model.isPast && eaten == 0 ? "–" : Fmt.kcal(abs(left))).heroNumber().foregroundStyle(clearlyOver ? Theme.over : Theme.text).contentTransition(.numericText())
                    Text(word).font(.title3.weight(.semibold)).lineLimit(1).minimumScaleFactor(0.8).foregroundStyle(clearlyOver ? Theme.over : Theme.soft)
                    FoodRingLines(kcal: eaten, protein: t.protein_g, carbs: t.carbs_g, fat: t.fat_g, goals: day.goals)
                        .padding(.top, 10)
                    if planned.kcal > 0 {  // the faint part of the rings, in words
                        Text("Includes \(Fmt.kcal(planned.kcal)) kcal planned").font(.footnote).foregroundStyle(Theme.muted).padding(.top, 4)
                    }
                }
                Spacer(minLength: 0)
            }
            .animation(.snappy, value: eaten)
            .accessibilityElement(children: .ignore)
            .accessibilityLabel("\(Fmt.kcal(abs(left))) calories \(word.replacingOccurrences(of: "kcal ", with: "")). \(Fmt.kcal(eaten)) \(eatenWord) of \(Fmt.kcal(day.goals.kcal)). "
                                + FoodRingLines.spoken(protein: t.protein_g, carbs: t.carbs_g, fat: t.fat_g, goals: day.goals) + ".")
            .accessibilityAction(named: "Next day") { Task { await model.step(days: 1) } }
            .accessibilityAction(named: "Previous day") { Task { await model.step(days: -1) } }
            if model.isToday, let s = model.streak {
                Divider().overlay(Theme.raised2)
                StreakRow(streak: s).padding(.vertical, -6)
            } else if model.isPast && model.leafDays.contains(day.date) {
                Divider().overlay(Theme.raised2)
                LeafCredit().padding(.vertical, -4)
            }
        }
        .padding(20)
        .background(RoundedRectangle(cornerRadius: Theme.radius, style: .continuous).fill(Theme.panel))
    }

    private var eatenWord: String { model.isFuture ? "planned" : model.isToday ? "so far" : "eaten" }

    /// Under the big number: what's left today and ahead; how the day came out once it's over.
    /// Over only because of food still to come (planned, or put in ahead): said so, so they don't think they're over already.
    private func heroWord(over: Bool, eaten: Double, planned: Double) -> String {
        if model.isPast { return eaten == 0 ? "nothing logged" : over ? "kcal over" : "kcal under" }
        if over && planned > 0 && eaten - planned <= day.goals.kcal { return "over with the plan" }
        return over ? "kcal over" : "kcal left"
    }
}

/// "What can I still have?": things they've had before that fit in what's left, drawn as suggestions (dashed, not added yet).
struct FitsRow: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Text("Fits in your \(Fmt.kcal(model.day?.left ?? 0)) kcal").heading(.title3).foregroundStyle(Theme.text)
                if model.proteinFirst { Text("protein first").font(.subheadline).foregroundStyle(Theme.muted) }
            }
            .padding(.horizontal, 20)
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 8) {
                    ForEach(model.fits) { f in
                        let busy = model.adding.contains(f.id)
                        Button { Task { await model.again(f, meal: model.defaultMeal) } } label: {
                            HStack(spacing: 8) {
                                if busy { ProgressView().frame(width: 24) } else {
                                    Image(systemName: "plus.circle.fill").font(.title3).foregroundStyle(Theme.accent)
                                }
                                VStack(alignment: .leading, spacing: 0) {
                                    Text(f.name).font(.callout.weight(.semibold)).foregroundStyle(Theme.soft)
                                    Text("\(Fmt.kcal(f.kcalEach)) kcal · to \(model.defaultMeal.single.lowercased())").font(.number(.footnote, .regular)).foregroundStyle(Theme.muted)
                                }
                            }
                            .padding(.leading, 12).padding(.trailing, 16).frame(minHeight: 56)
                            .background(Capsule().fill(Theme.panel.opacity(0.6)))
                            .overlay(Capsule().strokeBorder(Theme.muted.opacity(0.55), style: StrokeStyle(lineWidth: 1.5, dash: [5, 4])))
                            .contentShape(Capsule())
                        }
                        .buttonStyle(.plain).disabled(busy)
                        .accessibilityLabel("Add \(f.name), \(Fmt.kcal(f.kcalEach)) calories, to \(DayName.meal(model.defaultMeal, on: model.date))")
                    }
                }
                .padding(.horizontal, 20)
            }
        }
    }
}

/// One meal: its total and their target range, its entries, and its own Add (always there when planning or today).
struct MealSection: View {
    @Environment(AppModel.self) private var model
    var day: Day
    var meal: Meal

    private var waiting: [Pending] { model.pending(on: day.date).filter { $0.mealValue == meal } }

    /// Swipe right: how much, in a tap (portions; half or double for grams), with Undo.
    @ViewBuilder private func amounts(_ e: Entry) -> some View {
        // the same four places every time, like the portion picker; the current amount is ticked
        let dark = Color(UIColor(hex: 0x2D2F22)), sage = Color(UIColor(hex: 0x55603F)), bark = Color(UIColor(hex: 0x68604D))
        if e.per100 != nil, let g = e.grams {
            Button { Task { await model.setAmount(e, date: day.date, grams: g / 2) } } label: { Text("Half") }.tint(dark)
            Button { Task { await model.setAmount(e, date: day.date, grams: g * 2) } } label: { Text("Double") }.tint(sage)
        } else {
            ForEach(Array([0.5, 1, 1.5, 2].enumerated()), id: \.offset) { i, p in
                let now = p == e.portions
                Button { if !now { Task { await model.setAmount(e, date: day.date, portions: p) } } } label: {
                    if now { Label(Fmt.portions(p), systemImage: "checkmark") } else { Text(Fmt.portions(p)) }
                }
                .tint(now ? bark : i % 2 == 0 ? dark : sage)
                .accessibilityLabel("\(Fmt.portions(p)) \(p <= 1 ? "portion" : "portions")\(now ? ", now" : "")")
            }
        }
    }

    var body: some View {
        let es = day.entries(meal)
        let usuals = es.isEmpty && waiting.isEmpty ? model.usual(for: meal) : []
        let prev = Date.fromYMD(day.date).flatMap { Calendar.current.date(byAdding: .day, value: -1, to: $0) }?.ymd ?? ""
        let before = es.isEmpty && waiting.isEmpty && !model.isPast ? (model.known(prev)?.entries(meal) ?? []) : []
        let again: (from: String, to: String, entries: [Entry])? = before.isEmpty ? nil : (prev, day.date, before)
        if !es.isEmpty || !waiting.isEmpty || !model.isPast {
            Section {
                ForEach(es) { e in
                    Button { model.editing = e } label: { EntryRow(entry: e, date: day.date) }
                        .buttonStyle(.plain)
                        .swipeActions { Button(role: .destructive) { Task { await model.delete(e, date: day.date) } } label: { Label("Remove", systemImage: "trash") } }
                        .swipeActions(edge: .leading, allowsFullSwipe: false) { amounts(e) }
                }
                ForEach(waiting) { p in PendingRow(item: p) }
                if !usuals.isEmpty || again != nil {
                    UsualSuggestions(usuals: usuals, meal: meal, again: again)
                }
                Button { model.add(meal: meal) } label: {
                    Label(es.isEmpty ? "Add \(meal.single.lowercased())" : "Add more", systemImage: "plus")
                        .font(.body.weight(.semibold)).foregroundStyle(Theme.accent).frame(maxWidth: .infinity, minHeight: 44, alignment: .leading)
                        .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .accessibilityLabel(es.isEmpty ? "Add \(meal.single.lowercased())" : "Add more to \(meal.single.lowercased())")
                .id("meal-\(meal.rawValue)")
                if es.count >= 2 && !model.saved.contains(where: { $0.meal == meal.rawValue && $0.items == es.map(\.name) }) {
                    SaveMealButton(meal: meal)
                }
            } header: {
                let total = es.reduce(0) { $0 + $1.totals.kcal } + waiting.reduce(0) { $0 + $1.kcal }
                SectionTitle(text: meal.title, detail: day.goals.aim(meal), trailing: total == 0 ? nil : "\(Fmt.kcal(total)) kcal")
            }
            .listRowBackground(glowing ? Theme.accentSoft : Theme.panel)
        }
    }

    /// Brought into view after an add: lit for a moment.
    private var glowing: Bool { model.focus?.date == day.date && model.focus?.meal == meal }
}

struct EntryRow: View {
    var entry: Entry
    var date: String

    /// Put in ahead and its mealtime still to come: it counts already, marked so it reads as planned.
    private var planned: Bool { entry.stillPlanned(on: date) }

    var body: some View {
        HStack(spacing: 14) {
            FoodImage(path: entry.image, name: entry.name).frame(width: 60, height: 60)
            VStack(alignment: .leading, spacing: 3) {
                Text(entry.name).font(.body.weight(.semibold)).foregroundStyle(Theme.text).multilineTextAlignment(.leading)
                if planned {
                    Label("Planned", systemImage: "calendar").font(.subheadline.weight(.semibold)).foregroundStyle(Theme.accent).labelStyle(.titleAndIcon)
                }
                if !detail.isEmpty {
                    ViewThatFits(in: .horizontal) {
                        Text(detail)
                        VStack(alignment: .leading, spacing: 1) {
                            if let a = entry.amountText { Text(a) }
                            if let m = macros { Text(m) }
                        }
                    }
                    .font(.subheadline.monospacedDigit()).foregroundStyle(Theme.muted)
                }
            }
            Spacer(minLength: 8)
            Text("\(Fmt.kcal(entry.totals.kcal))").font(.number(.title3)).foregroundStyle(Theme.text)
        }
        .padding(.vertical, 4)
        .contentShape(Rectangle())
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("\(entry.name)\(planned ? ", planned" : ""), \(Fmt.kcal(entry.totals.kcal)) calories, \(Int(entry.totals.protein_g.rounded())) grams protein, \(Int(entry.totals.carbs_g.rounded())) carbs, \(Int(entry.totals.fat_g.rounded())) fat")
        .accessibilityHint("Opens it to change the amount or meal")
        .accessibilityAddTraits(.isButton)
    }
    /// Protein, carbs and fat, when it has them (foods imported from elsewhere often have only calories).
    private var macros: String? {
        let t = entry.totals
        guard t.protein_g + t.carbs_g + t.fat_g >= 0.5 else { return entry.edited ? "your numbers" : nil }
        return "P \(Int(t.protein_g.rounded())) · C \(Int(t.carbs_g.rounded())) · F \(Int(t.fat_g.rounded()))" + (entry.edited ? " · your numbers" : "")
    }
    private var detail: String { [entry.amountText, macros].compactMap { $0 }.joined(separator: " · ") }
}

/// Added with no signal: in its meal straight away, marked as waiting until Home Assistant has it.
struct PendingRow: View {
    @Environment(AppModel.self) private var model
    var item: Pending
    @State private var busy = false

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 14) {
                FoodImage(path: nil, name: item.name).frame(width: 60, height: 60).opacity(0.7)
                VStack(alignment: .leading, spacing: 3) {
                    Text(item.name).font(.body.weight(.semibold)).foregroundStyle(Theme.soft).multilineTextAlignment(.leading)
                    if item.failed == true {
                        Label("Home Assistant couldn't add it", systemImage: "exclamationmark.circle").font(.subheadline.weight(.semibold)).foregroundStyle(Theme.over)
                    } else {
                        Label("Waiting for signal", systemImage: "hourglass").font(.subheadline.weight(.semibold)).foregroundStyle(Theme.muted)
                    }
                }
                Spacer(minLength: 8)
                Text(Fmt.kcal(item.kcal)).font(.number(.title3)).foregroundStyle(Theme.soft)
            }
            .accessibilityElement(children: .ignore)
            .accessibilityLabel("\(item.name), \(Fmt.kcal(item.kcal)) calories, \(item.failed == true ? "Home Assistant couldn't add it" : "waiting for signal")")
            if item.failed == true {
                HStack(spacing: 10) {
                    Button { Task { busy = true; await model.retry(item); busy = false } } label: {
                        HStack(spacing: 6) { if busy { ProgressView().controlSize(.small).tint(Theme.fillInk) }; Text("Try again") }
                            .font(.subheadline.weight(.semibold)).foregroundStyle(Theme.fillInk)
                            .padding(.horizontal, 16).frame(minHeight: 44).background(Capsule().fill(Theme.fill))
                    }
                    Button { model.drop(item) } label: {
                        Text("Remove").font(.subheadline.weight(.semibold)).foregroundStyle(Theme.accent).padding(.horizontal, 12).frame(minHeight: 44)
                    }
                }
                .buttonStyle(.plain).disabled(busy)
            }
        }
        .padding(.vertical, 4)
    }
}

/// Asked from a tap, not at sign-in: what Health is for, then iOS's own Health sheet. "Not now" puts it away; Settings
/// still has it.
struct HealthCard: View {
    @Environment(AppModel.self) private var model
    @State private var busy = false
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Label { Text("Connect Apple Health") } icon: { Image(systemName: "heart.fill").foregroundStyle(Theme.accent) }
                .heading(.title3).foregroundStyle(Theme.text)
            Text("Your food goes into Health, and your activity, rings and sleep show beside it.").font(.body).foregroundStyle(Theme.soft)
            HStack(spacing: 10) {
                Button { model.healthNotNow() } label: {
                    Text("Not now").font(.body.weight(.semibold)).foregroundStyle(Theme.text)
                        .frame(maxWidth: .infinity, minHeight: 52).background(Capsule().fill(Theme.raised)).contentShape(Capsule())
                }
                .buttonStyle(.plain)
                Button { Task { busy = true; await model.connectHealth(); busy = false } } label: {
                    HStack(spacing: 8) { if busy { ProgressView().tint(Theme.fillInk) }; Text(busy ? "Connecting…" : "Connect") }
                        .font(.body.weight(.semibold)).foregroundStyle(Theme.fillInk)
                        .frame(maxWidth: .infinity, minHeight: 52).background(Capsule().fill(Theme.fill)).contentShape(Capsule())
                }
                .buttonStyle(.plain).disabled(busy)
            }
        }
        .padding(18).background(RoundedRectangle(cornerRadius: Theme.radius, style: .continuous).fill(Theme.panel))
    }
}

/// Always at hand, over a fade of the page so nothing shows through: Scan, Add food (the bar's main thing) and a meal
/// photo, each one tap. All three go to the day on screen.
struct AddBar: View {
    @Environment(AppModel.self) private var model

    /// "tomorrow", "Thursday", or "Thu 1 Dec" further off, so the button never cuts its own words.
    static func short(_ ymd: String) -> String {
        let name = DayName.of(ymd)
        guard name.contains(" "), let d = Date.fromYMD(ymd) else { return name }
        return d.formatted(.dateTime.weekday(.abbreviated).day().month(.abbreviated))
    }

    private var addWords: String { model.isToday ? "Add food" : "Add to \(Self.short(model.date))" }

    var body: some View {
        HStack(spacing: 10) {
            Button { model.add(.scan) } label: {
                Image(systemName: "barcode.viewfinder").font(.title3.weight(.semibold)).foregroundStyle(Theme.accent)
                    .frame(width: 60, height: 60).background(Circle().fill(Theme.panel)).shadow(color: .black.opacity(0.08), radius: 10, y: 4)
            }
            .accessibilityLabel("Scan a barcode")
            Button { model.add() } label: {
                Label { ViewThatFits(in: .horizontal) { Text(addWords); Text("Add") } } icon: { Image(systemName: "plus") }
                    .font(.title3.weight(.semibold)).foregroundStyle(Theme.fillInk).lineLimit(1)
                    .frame(maxWidth: .infinity, minHeight: 60).background(Capsule().fill(Theme.fill))
                    .shadow(color: .black.opacity(0.14), radius: 10, y: 4).contentShape(Capsule())
            }
            .accessibilityLabel(addWords)
            Button { model.add(.photo) } label: {
                Image(systemName: "camera").font(.title3.weight(.semibold)).foregroundStyle(Theme.accent)
                    .frame(width: 60, height: 60).background(Circle().fill(Theme.panel)).shadow(color: .black.opacity(0.08), radius: 10, y: 4)
            }
            .accessibilityLabel("Photo of a meal")
        }
        .buttonStyle(.plain)
        .dynamicTypeSize(...DynamicTypeSize.accessibility1)
        .padding(.horizontal, 16).padding(.top, 18).padding(.bottom, 4)
        .background {
            LinearGradient(stops: [.init(color: Theme.page.opacity(0), location: 0), .init(color: Theme.page.opacity(0.94), location: 0.45)],
                           startPoint: .top, endPoint: .bottom)
                .ignoresSafeArea(edges: .bottom)
                .allowsHitTesting(false)  // only the buttons take taps; a row showing through the fade still opens
        }
    }
}
