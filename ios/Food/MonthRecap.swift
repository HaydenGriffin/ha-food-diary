import SwiftUI

/// A month in food, on the phone only: a few cards to swipe through, each with one big thing.
/// Offered on Today for the first three days of a month, and from the Month page for any month that's over.
struct MonthStats {
    var month: Date
    var photos: [String] = []
    var foods: [String] = []   // the month's most-eaten foods, drawn as tiles when there are no photos
    var logged = 0
    var leaves = 0
    var average = 0.0
    var goal = 0.0
    var favourite: (name: String, times: Int)?
    var longest = 0

    var title: String { month.formatted(.dateTime.month(.wide)) }

    static func load(_ month: Date) async -> MonthStats? {
        let cal = Calendar.current
        guard let range = cal.range(of: .day, in: .month, for: month) else { return nil }
        let days = range.compactMap { cal.date(byAdding: .day, value: $0 - 1, to: month)?.ymd }.filter { $0 < Date().ymd }
        guard let last = days.last, let h = try? await FoodAPI.history(days: days.count, through: last),
              let full = try? await FoodAPI.days(days) else { return nil }
        var s = MonthStats(month: month)
        s.goal = h.goals.kcal
        let ate = h.days.filter { $0.kcal > 0 }
        s.logged = ate.count
        guard s.logged > 0 else { return s }
        s.average = ate.reduce(0) { $0 + $1.kcal } / Double(ate.count)
        s.leaves = ate.filter { Streak.onTarget($0.kcal, goal: s.goal) }.count
        var run = 0
        for d in h.days { if Streak.onTarget(d.kcal, goal: s.goal) { run += 1; s.longest = max(s.longest, run) } else { run = 0 } }
        let entries = full.flatMap(\.entries)
        let images = entries.compactMap(\.image)
        s.photos = Array((images.filter { $0.hasPrefix("/api/") } + images.filter { !$0.hasPrefix("/api/") }).prefix(9))
        let counts = Dictionary(grouping: entries, by: { $0.name }).mapValues(\.count)
        s.foods = counts.sorted { $0.value != $1.value ? $0.value > $1.value : $0.key < $1.key }.prefix(9).map(\.key)
        if let top = counts.max(by: { $0.value < $1.value }), top.value >= 3 { s.favourite = (top.key, top.value) }
        return s
    }

    /// The first three days of a month: last month's recap, once.
    static var due: Date? {
        let cal = Calendar.current
        #if DEBUG
        if ProcessInfo.processInfo.arguments.contains("-showRecap") { return cal.dateInterval(of: .month, for: Date())?.start }
        #endif
        guard cal.component(.day, from: Date()) <= 3, let thisMonth = cal.dateInterval(of: .month, for: Date())?.start,
              let last = cal.date(byAdding: .month, value: -1, to: thisMonth),
              !AppConfig.shared.bool(forKey: "recapSeen-\(last.ymd.prefix(7))") else { return nil }
        return last
    }
    static func seen(_ month: Date) { AppConfig.shared.set(true, forKey: "recapSeen-\(month.ymd.prefix(7))") }
}

/// On Today: "Your September in food", one tap to open it.
struct RecapOffer: View {
    @Environment(AppModel.self) private var model
    var month: Date
    @State private var open = false

    var body: some View {
        Button { open = true } label: {
            HStack(spacing: 14) {
                Image(systemName: "sparkles").font(.title2).foregroundStyle(Theme.accent).frame(width: 52, height: 52)
                    .background(Circle().fill(Theme.accentSoft))
                VStack(alignment: .leading, spacing: 2) {
                    Text("Your \(month.formatted(.dateTime.month(.wide))) in food").heading(.title3).foregroundStyle(Theme.text)
                    Text("A look back, just for you").font(.subheadline).foregroundStyle(Theme.muted)
                }
                Spacer(minLength: 0)
                Image(systemName: "chevron.right").font(.footnote.weight(.semibold)).foregroundStyle(Theme.muted)
            }
            .padding(18).background(RoundedRectangle(cornerRadius: Theme.radius, style: .continuous).fill(Theme.panel))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .sheet(isPresented: $open, onDismiss: { MonthStats.seen(month); model.recapOffer = nil }) { MonthRecapView(month: month) }
    }
}

struct MonthRecapView: View {
    @Environment(\.dismiss) private var dismiss
    var month: Date
    @State private var stats: MonthStats?
    @State private var page = 0

    var body: some View {
        NavigationStack {
            Group {
                if let s = stats {
                    if s.logged == 0 {
                        Text("Nothing was logged in \(s.title).").font(.body).foregroundStyle(Theme.muted).frame(maxWidth: .infinity, maxHeight: .infinity)
                    } else {
                        TabView(selection: $page) {
                            ForEach(Array(cards(s).enumerated()), id: \.offset) { i, c in c.padding(24).tag(i) }
                        }
                        .tabViewStyle(.page(indexDisplayMode: .always))
                        .indexViewStyle(.page(backgroundDisplayMode: .always))
                    }
                } else {
                    ProgressView().frame(maxWidth: .infinity, maxHeight: .infinity)
                }
            }
            .background(PatternedPage())
            .navigationTitle("\(month.formatted(.dateTime.month(.wide).year())) in food").navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } } }
            .task { stats = await MonthStats.load(month) }
        }
    }

    /// One idea per card, and only cards with something worth saying.
    private func cards(_ s: MonthStats) -> [AnyView] {
        var out: [AnyView] = []
        if s.photos.isEmpty && s.foods.count >= 4 {
            out.append(AnyView(card(top: "Your month on a plate", hero: "\(s.logged)", line: "days of food") {
                LazyVGrid(columns: Array(repeating: GridItem(.flexible(), spacing: 6), count: 3), spacing: 6) {
                    ForEach(s.foods, id: \.self) { n in FoodImage(path: nil, name: n, corner: 14).aspectRatio(1, contentMode: .fit) }
                }
            }))
        }
        if !s.photos.isEmpty {
            let cols = s.photos.count == 1 ? 1 : s.photos.count <= 4 ? 2 : 3
            out.append(AnyView(card(top: "Your month on a plate", hero: "\(s.logged)", line: "days of food") {
                if let only = s.photos.first, cols == 1 {
                    FoodImage(path: only, name: "food", corner: 18).frame(width: 220, height: 220).frame(maxWidth: .infinity)
                } else {
                    LazyVGrid(columns: Array(repeating: GridItem(.flexible(), spacing: 6), count: cols), spacing: 6) {
                        ForEach(s.photos, id: \.self) { p in FoodImage(path: p, name: "food", corner: 14).aspectRatio(1, contentMode: .fit) }
                    }
                }
            }))
        }
        if s.leaves > 0 {
            out.append(AnyView(card(top: "Leaves grown", hero: "\(s.leaves)", line: s.longest >= 3 ? "Longest sprig: \(s.longest) days in a row" : "of \(s.logged) days logged") {
                StreakVine(leaves: min(s.leaves, 14), bud: false, unit: 2.2, gap: 14, maxLeaves: 14)
            }))
        }
        out.append(AnyView(card(top: "On an average day", hero: Fmt.kcal(s.average), line: "kcal, with a goal of \(Fmt.kcal(s.goal))") { EmptyView() }))
        if let f = s.favourite {
            out.append(AnyView(card(top: "Your favourite", hero: f.name, line: "\(f.times) times this month", heroSize: .title) { EmptyView() }))
        }
        return out
    }

    private func card<C: View>(top: String, hero: String?, line: String, heroSize: Font.TextStyle? = nil, @ViewBuilder art: () -> C) -> some View {
        VStack(spacing: 18) {
            Spacer(minLength: 0)
            Text(top).font(.headline).foregroundStyle(Theme.muted)
            if let hero {
                if let heroSize { Text(hero).heading(heroSize).multilineTextAlignment(.center).foregroundStyle(Theme.text) }
                else { Text(hero).heroNumber().foregroundStyle(Theme.text) }
            }
            art()
            Text(line).font(.title3).foregroundStyle(Theme.soft).multilineTextAlignment(.center)
            Spacer(minLength: 0)
        }
        .frame(maxWidth: .infinity)
        .padding(24)
        .background(RoundedRectangle(cornerRadius: 28, style: .continuous).fill(Theme.panel))
        .padding(.bottom, 30)
        .accessibilityElement(children: .combine)
    }
}
