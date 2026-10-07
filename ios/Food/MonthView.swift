import SwiftUI

/// What the card under the month's grid shows: one day, or one week on average (a week by its first day in the month).
enum MonthPick: Equatable {
    case day(String)
    case week(String)

    var ymd: String { switch self { case .day(let d), .week(let d): d } }
}

/// The Month page: the month in one glance (an average day against their goals, the leaves it grew), every day's rings in
/// Monday-to-Sunday rows with the week's average at the end of each, and the chosen day or week under them. Months turn
/// sideways like pages, and each is loaded once per visit so turning back doesn't flicker.
struct MonthView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    @State private var page = 0                          // months from this one: -1 is last month
    @State private var months: [Int: MonthData] = [:]
    @State private var failed: Set<Int> = []
    @State private var pick: MonthPick?
    @AppStorage("calendarLook") private var look = "days"   // days | photos

    /// Two years back is plenty to look through; forward only as far as they can plan.
    private static let back = 24
    private let cal = Calendar.current
    private var thisMonth: Date { cal.dateInterval(of: .month, for: Date())?.start ?? Date() }
    private var ahead: Int { offset(of: cal.date(byAdding: .day, value: AppModel.maxAhead, to: Date()) ?? Date()) }

    var body: some View {
        NavigationStack {
            TabView(selection: $page) {
                ForEach(-Self.back...ahead, id: \.self) { i in
                    MonthPage(data: months[i] ?? MonthData(month: month(i)), loaded: months[i] != nil, failed: failed.contains(i),
                              look: $look, pick: $pick, open: open, reload: { await load(i) })
                        .tag(i)
                        .task { if months[i] == nil { await load(i) } }
                }
            }
            .tabViewStyle(.page(indexDisplayMode: .never))
            .background(PatternedPage())
            .navigationTitle(title(page)).navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Close") { dismiss() } }
                ToolbarItem(placement: .principal) { switcher }
                ToolbarItem(placement: .confirmationAction) { Button("Today") { turn(to: 0); pick = .day(Date().ymd) } }
            }
            .onChange(of: page) { if pick.map({ !$0.ymd.hasPrefix(month(page).ymd.prefix(7)) }) ?? true { pick = firstPick(page) } }
            .onAppear {
                page = min(max(offset(of: Date.fromYMD(model.date) ?? Date()), -Self.back), ahead)
                pick = .day(model.date)
            }
        }
    }

    /// The month's name between its arrows (swiping turns the page too).
    private var switcher: some View {
        HStack(spacing: 0) {
            arrow("chevron.left", "Previous month", to: page - 1).disabled(page <= -Self.back)
            Text(title(page)).font(.heading(.headline)).foregroundStyle(Theme.text).lineLimit(1).fixedSize()
                .contentTransition(.numericText()).accessibilityAddTraits(.isHeader)
            arrow("chevron.right", "Next month", to: page + 1).disabled(page >= ahead)
        }
    }

    private func arrow(_ icon: String, _ label: String, to p: Int) -> some View {
        Button { turn(to: p) } label: {
            Image(systemName: icon).font(.body.weight(.semibold)).frame(width: 40, height: 44).contentShape(Rectangle())
        }
        .foregroundStyle(Theme.accent)
        .accessibilityLabel(label)
    }

    // ---------- months ----------

    private func month(_ i: Int) -> Date { cal.date(byAdding: .month, value: i, to: thisMonth) ?? thisMonth }

    private func offset(of d: Date) -> Int {
        let start = cal.dateInterval(of: .month, for: d)?.start ?? d
        return cal.dateComponents([.month], from: thisMonth, to: start).month ?? 0
    }

    /// "October" this year, "October 2025" otherwise.
    private func title(_ i: Int) -> String {
        let m = month(i)
        return cal.isDate(m, equalTo: Date(), toGranularity: .year) ? m.formatted(.dateTime.month(.wide)) : m.formatted(.dateTime.month(.wide).year())
    }

    /// A month opens on today when it's in it, otherwise with nothing chosen (the month's own numbers lead).
    private func firstPick(_ i: Int) -> MonthPick? { i == 0 ? .day(Date().ymd) : nil }

    private func turn(to p: Int) {
        guard p >= -Self.back, p <= ahead else { return }
        withAnimation(.snappy) { page = p }
    }

    private func load(_ i: Int) async {
        if let m = await MonthData.load(month(i), exercise: model.exerciseCounts ? model.exercise : nil) {
            months[i] = m; failed.remove(i)
        } else if months[i] == nil {
            failed.insert(i)
        }
    }

    private func open(_ d: String) {
        Task { await model.go(day: d) }
        dismiss()
    }
}

/// One month, top to bottom: days or photos, the month in one glance, the grid with its week column, and the chosen day or
/// week. Pull down to load it again.
private struct MonthPage: View {
    var data: MonthData
    var loaded: Bool
    var failed: Bool
    @Binding var look: String
    @Binding var pick: MonthPick?
    var open: (String) -> Void
    var reload: () async -> Void
    @State private var recap = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                BigSegments(label: "Show the month as", options: [("days", "Days"), ("photos", "Photos")], selection: $look)
                MonthSummary(data: data, loaded: loaded, failed: failed) { recap = true }
                MonthGrid(data: data, look: look, pick: $pick)
                detail
                if !HealthSync.shared.asked {
                    Text("Connect Apple Health in Settings to see your rings and sleep here too.").font(.footnote).foregroundStyle(Theme.muted)
                }
            }
            .padding(16)
        }
        .refreshable { await reload() }
        .sheet(isPresented: $recap) { MonthRecapView(month: data.month) }
    }

    @ViewBuilder private var detail: some View {
        if case .day(let d) = pick, data.days.contains(d), let date = Date.fromYMD(d) {
            DayDetail(date: date, food: data.food[d], goals: data.goals, leaf: data.leaf(d), activity: data.activity[d]) { open(d) }
        } else if case .week(let first) = pick, let week = data.weeks.map({ $0.compactMap { $0 } }).first(where: { $0.first == first }) {
            WeekDetail(days: week, average: data.average(week), goals: data.goals)
        } else if loaded {
            Text("Tap a day to see it, or a week for its average.").font(.footnote).foregroundStyle(Theme.muted)
        }
    }
}
