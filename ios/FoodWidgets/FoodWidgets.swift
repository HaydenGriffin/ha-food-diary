import AppIntents
import SwiftUI
import WidgetKit

struct FoodEntry: TimelineEntry {
    var date: Date
    var snap: Snapshot
    var recent: [RecentFood]
    var signedIn: Bool
}

struct Provider: TimelineProvider {
    func placeholder(in context: Context) -> FoodEntry { FoodEntry(date: Date(), snap: .placeholder, recent: [], signedIn: true) }
    func getSnapshot(in context: Context, completion: @escaping (FoodEntry) -> Void) {
        completion(FoodEntry(date: Date(), snap: Snapshot.load() ?? .placeholder, recent: [], signedIn: true))
    }
    func getTimeline(in context: Context, completion: @escaping (Timeline<FoodEntry>) -> Void) {
        Task {
            let signedIn = await HAClient.shared.signedIn
            var snap = Snapshot.load() ?? Snapshot(date: Date().ymd, kcal: 0, goal: 2000, protein: 0, carbs: 0, fat: 0, proteinGoal: 100, carbsGoal: 230, fatGoal: 70, updated: Date())
            var recent: [RecentFood] = []
            if signedIn {
                if let d = try? await FoodAPI.day(Date().ymd) { Snapshot.save(from: d); snap = Snapshot.load() ?? snap }
                if context.family == .systemMedium || context.family == .systemLarge { recent = (try? await FoodAPI.recent()) ?? [] }
            }
            let next = min(Date().addingTimeInterval(30 * 60), Calendar.current.startOfDay(for: Date().addingTimeInterval(86400)).addingTimeInterval(60))
            completion(Timeline(entries: [FoodEntry(date: Date(), snap: snap, recent: Array(recent.prefix(2)), signedIn: signedIn)], policy: .after(next)))
        }
    }
}

struct FoodWidgetView: View {
    @Environment(\.widgetFamily) private var family
    var entry: FoodEntry

    /// Numbers more than an hour old (Home Assistant couldn't be reached): say when they're from.
    private var stale: String? {
        Date().timeIntervalSince(entry.snap.updated) > 3600 ? "as of \(entry.snap.updated.formatted(date: .omitted, time: .shortened))" : nil
    }
    private var leftText: String { entry.snap.left >= 0 ? "\(Fmt.kcal(entry.snap.left)) kcal left" : "\(Fmt.kcal(-entry.snap.left)) kcal over" }

    var body: some View {
        if !entry.signedIn {
            signedOut
        } else {
            today
        }
    }

    @ViewBuilder private var signedOut: some View {
        switch family {
        case .accessoryCircular: Image(systemName: "fork.knife").widgetURL(URL(string: "fooddiary://today"))
        case .accessoryRectangular, .accessoryInline: Text("Sign in to Food").widgetURL(URL(string: "fooddiary://today"))
        default:
            VStack(spacing: 6) {
                Image(systemName: "fork.knife").font(.title2).foregroundStyle(Theme.accent)
                Text("Sign in to Food").font(.headline).foregroundStyle(Theme.text)
            }
            .widgetURL(URL(string: "fooddiary://today"))
        }
    }

    @ViewBuilder private var today: some View {
        let s = entry.snap
        switch family {
        case .accessoryCircular:
            Gauge(value: min(s.kcal, max(s.goal, 1)), in: 0...max(s.goal, 1)) {
                Image(systemName: "fork.knife")
            } currentValueLabel: { Text(short(s.left)).font(.system(.body, design: .rounded).monospacedDigit()).privacySensitive() }
            .gaugeStyle(.accessoryCircular)
            .accessibilityLabel("Calories")
            .accessibilityValue(leftText)
            .widgetURL(URL(string: "fooddiary://add"))
        case .accessoryRectangular:
            VStack(alignment: .leading, spacing: 2) {
                Text(leftText).font(.headline).privacySensitive()
                Text(stale ?? "P \(Int(s.protein)) · C \(Int(s.carbs)) · F \(Int(s.fat))").font(.subheadline).privacySensitive()
                Gauge(value: min(s.kcal, max(s.goal, 1)), in: 0...max(s.goal, 1)) { EmptyView() }.gaugeStyle(.accessoryLinearCapacity)
                    .accessibilityHidden(true)
            }
            .accessibilityElement(children: .combine)
            .widgetURL(URL(string: "fooddiary://add"))
        case .accessoryInline:
            Text(leftText).privacySensitive()
        case .systemSmall:
            VStack(spacing: 8) {
                ZStack {
                    CalorieRing(eaten: s.kcal, goal: s.goal, lineWidth: 11)
                    VStack(spacing: -2) {
                        Text(Fmt.kcal(abs(s.left))).font(.number(.title2, .bold)).foregroundStyle(Theme.text).minimumScaleFactor(0.6).widgetAccentable()
                        Text(s.left >= 0 ? "left" : "over").font(.caption.weight(.semibold)).foregroundStyle(Theme.muted)
                    }
                }
                Text(stale ?? "\(Fmt.kcal(s.kcal)) of \(Fmt.kcal(s.goal))").font(.caption.weight(.semibold)).foregroundStyle(Theme.muted)
            }
            .privacySensitive()
            .accessibilityElement(children: .ignore)
            .accessibilityLabel("\(leftText). \(Fmt.kcal(s.kcal)) of \(Fmt.kcal(s.goal)) eaten\(stale.map { ", \($0)" } ?? "")")
            .widgetURL(URL(string: "fooddiary://add"))
        default:
            HStack(spacing: 16) {
                VStack(alignment: .leading, spacing: 10) {
                    HStack(spacing: 10) {
                        ZStack {
                            CalorieRing(eaten: s.kcal, goal: s.goal, lineWidth: 8)
                            Text(Fmt.kcal(abs(s.left))).font(.number(.subheadline, .bold)).foregroundStyle(Theme.text).minimumScaleFactor(0.5).widgetAccentable()
                        }
                        .frame(width: 58, height: 58)
                        VStack(alignment: .leading, spacing: 0) {
                            Text(s.left >= 0 ? "left" : "over").font(.caption.weight(.semibold)).foregroundStyle(Theme.muted)
                            Text(stale ?? "\(Fmt.kcal(s.kcal)) of \(Fmt.kcal(s.goal))").font(.caption).foregroundStyle(Theme.muted)
                        }
                    }
                    .privacySensitive()
                    .accessibilityElement(children: .ignore)
                    .accessibilityLabel("\(leftText). \(Fmt.kcal(s.kcal)) of \(Fmt.kcal(s.goal)) eaten")
                    HStack(spacing: 8) {
                        Link(destination: URL(string: "fooddiary://scan")!) { pill("barcode.viewfinder", "Scan") }
                        Link(destination: URL(string: "fooddiary://photo")!) { pill("camera", "Photo") }
                    }
                }
                if !entry.recent.isEmpty {
                    VStack(alignment: .leading, spacing: 8) {
                        Text("Again?").font(.caption.weight(.semibold)).foregroundStyle(Theme.muted)
                        ForEach(entry.recent, id: \.id) { f in
                            Button(intent: LogAgainIntent(name: f.name)) {
                                HStack(spacing: 6) {
                                    Image(systemName: "plus.circle.fill").font(.title3).foregroundStyle(Theme.accent)
                                    Text(f.name).font(.caption.weight(.semibold)).foregroundStyle(Theme.text).lineLimit(2)
                                }
                                .frame(maxWidth: .infinity, minHeight: 36, alignment: .leading)
                                .contentShape(Rectangle())
                            }
                            .buttonStyle(.plain)
                            .accessibilityLabel("Add \(f.name) again")
                        }
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                }
            }
            .widgetURL(URL(string: "fooddiary://add"))
        }
    }

    private func short(_ v: Double) -> String { abs(v) >= 1000 ? (v / 1000).formatted(.number.precision(.fractionLength(1))) + "k" : "\(Int(v.rounded()))" }

    /// Icon and word when there's room, else just the icon (never a word broken over two lines).
    private func pill(_ icon: String, _ text: String) -> some View {
        ViewThatFits(in: .horizontal) {
            Label(text, systemImage: icon).lineLimit(1).fixedSize()
            Image(systemName: icon)
        }
        .font(.caption.weight(.semibold)).foregroundStyle(Theme.fillInk)
        .padding(.horizontal, 10).padding(.vertical, 8).frame(maxWidth: .infinity).background(Capsule().fill(Theme.fill))
        .accessibilityLabel(text)
    }
}

struct FoodWidget: Widget {
    var body: some WidgetConfiguration {
        StaticConfiguration(kind: "food.today", provider: Provider()) { entry in
            FoodWidgetView(entry: entry).containerBackground(Theme.panel, for: .widget)
        }
        .configurationDisplayName("Calories today")
        .description("What's left today, with quick ways to log.")
        .supportedFamilies([.systemSmall, .systemMedium, .accessoryCircular, .accessoryRectangular, .accessoryInline])
    }
}

/// Control Center, the Lock Screen and the Action button: straight to logging.
struct LogFoodControl: ControlWidget {
    var body: some ControlWidgetConfiguration {
        StaticControlConfiguration(kind: "food.control.add") {
            ControlWidgetButton(action: OpenFoodIntent(screen: .add)) { Label("Log food", systemImage: "fork.knife") }
        }
        .displayName("Log food")
    }
}

struct ScanFoodControl: ControlWidget {
    var body: some ControlWidgetConfiguration {
        StaticControlConfiguration(kind: "food.control.scan") {
            ControlWidgetButton(action: OpenFoodIntent(screen: .scan)) { Label("Scan food", systemImage: "barcode.viewfinder") }
        }
        .displayName("Scan food")
    }
}

@main
struct FoodWidgetBundle: WidgetBundle {
    var body: some Widget {
        FoodWidget()
        LogFoodControl()
        ScanFoodControl()
    }
}
