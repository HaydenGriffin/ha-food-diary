import SwiftUI

/// The growing sprig: a leaf for each day in a row they landed within their goal (75–105%, more room on active days when they
/// count exercise). It never wilts: a day outside simply starts a new sprig.
struct Streak: Equatable {
    var days: Int                // in a row, to yesterday
    var todayOnTrack: Bool       // today, so far
    var helpedByExercise: Int    // days that only counted thanks to exercise
    var recent: [Bool]           // the last days before today, oldest first

    /// Monday-to-Sunday weeks (the UK way, whatever the phone's setting).
    static var weekCal: Calendar { var c = Calendar(identifier: .iso8601); c.timeZone = .current; return c }
    static func weekStart(_ d: Date) -> Date { weekCal.dateInterval(of: .weekOfYear, for: d)?.start ?? d }

    /// Within the goal: at least 75%, and at most 105% plus whatever room exercise gives it.
    static func onTarget(_ kcal: Double, goal: Double, room: Double = 0) -> Bool {
        goal > 0 && kcal >= goal * 0.75 && kcal <= goal * Theme.overBand + room
    }

    /// Days in a row on target, counting back from the last day given.
    static func count(_ days: [HistoryDay], goal: Double, room: [String: Double] = [:]) -> Int {
        var n = 0
        for d in days.reversed() { guard onTarget(d.kcal, goal: goal, room: room[d.date] ?? 0) else { break }; n += 1 }
        return n
    }
    var line: String {
        switch (days, todayOnTrack) {
        case (0, true): return "First leaf growing today"
        case (0, false): return "A leaf for each day on target"
        case (let n, _): return "\(n) day\(n == 1 ? "" : "s") growing"
        }
    }
    var spoken: String {
        line + (days > 0 && todayOnTrack ? ", and today's on track" : "")
    }
}

/// One olive leaf, pointed at both ends, drawn growing up from its base at the bottom centre.
struct LeafShape: Shape {
    func path(in r: CGRect) -> Path {
        var p = Path()
        p.move(to: CGPoint(x: r.midX, y: r.maxY))
        p.addQuadCurve(to: CGPoint(x: r.midX, y: r.minY), control: CGPoint(x: r.minX - r.width * 0.15, y: r.midY))
        p.addQuadCurve(to: CGPoint(x: r.midX, y: r.maxY), control: CGPoint(x: r.maxX + r.width * 0.15, y: r.midY))
        return p
    }
}

/// A gentle stem along the row with alternating leaves, a bud outline for today while it's still growing, and a tip bud.
/// `unit` scales the whole drawing (the sheet draws it bigger rather than stretching it).
struct StreakVine: View {
    var leaves: Int
    var bud: Bool
    var unit: CGFloat = 1
    var gap: CGFloat = 11
    var maxLeaves = 7

    var body: some View {
        let shown = min(leaves, maxLeaves)
        let total = shown + (bud ? 1 : 0)
        let step = gap * unit
        let width = CGFloat(max(total, 1)) * step + 14 * unit
        Canvas { ctx, size in
            let y = size.height * 0.58
            let end = CGPoint(x: width - 4 * unit, y: y - 2 * unit)
            var stem = Path()
            stem.move(to: CGPoint(x: 2 * unit, y: y))
            stem.addQuadCurve(to: end, control: CGPoint(x: end.x / 2, y: y - 3 * unit))
            ctx.stroke(stem, with: .color(Theme.ring), style: StrokeStyle(lineWidth: 2 * unit, lineCap: .round))
            ctx.fill(Path(ellipseIn: CGRect(x: end.x - 1.5 * unit, y: end.y - 1.5 * unit, width: 3 * unit, height: 3 * unit)), with: .color(Theme.ring))
            for i in 0..<total {
                let x = 9 * unit + CGFloat(i) * step
                let up = i % 2 == 0
                let leaf = LeafShape().path(in: CGRect(x: -2.75 * unit, y: -13 * unit, width: 5.5 * unit, height: 13 * unit))
                    .applying(CGAffineTransform(rotationAngle: up ? 0.87 : .pi - 0.87).concatenating(CGAffineTransform(translationX: x, y: y - 1 * unit)))
                if bud && i == total - 1 {
                    ctx.stroke(leaf, with: .color(Theme.ring), lineWidth: 1.25 * unit)
                } else {
                    ctx.fill(leaf, with: .color(Theme.ring))
                }
            }
        }
        .frame(width: width, height: 30 * unit)
        .accessibilityHidden(true)
    }
}

/// On Today, under the numbers: the sprig and one line. Tapping it explains, and holds the exercise switch. A tap, not a
/// Button: the row is the width of the day's card, which swipes between days, and a Button would also fire at the end of
/// a sideways swipe (the finger lifts still inside it). A tap gesture gives up as soon as the finger moves.
struct StreakRow: View {
    var streak: Streak
    @State private var open = false

    var body: some View {
        HStack(spacing: 12) {
            StreakVine(leaves: streak.days, bud: streak.todayOnTrack)
            Text(streak.line).font(.subheadline.weight(.semibold)).foregroundStyle(Theme.soft).lineLimit(1).minimumScaleFactor(0.9)
            Spacer(minLength: 0)
            Image(systemName: "chevron.right").font(.caption.weight(.semibold)).foregroundStyle(Theme.muted).accessibilityHidden(true)
        }
        .frame(minHeight: 44).contentShape(Rectangle())
        .onTapGesture { open = true }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(streak.spoken)
        .accessibilityHint("Shows how the sprig grows")
        .accessibilityAddTraits(.isButton)
        .accessibilityAction { open = true }
        .sheet(isPresented: $open) { StreakSheet() }
    }
}

struct StreakSheet: View {
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss

    /// The leaf rule in their own numbers, not percentages.
    private var leafRule: String {
        let range = model.day.map { "between \(Fmt.kcal($0.goals.kcal * 0.75)) and \(Fmt.kcal($0.goals.kcal * Theme.overBand)) kcal" } ?? "close to your goal"
        return "A leaf for each day you eat \(range). A day outside it simply starts a new sprig."
    }

    var body: some View {
        @Bindable var m = model
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 20) {
                    if let s = model.streak {
                        VStack(alignment: .leading, spacing: 10) {
                            StreakVine(leaves: s.days, bud: s.todayOnTrack, unit: 2, gap: 14, maxLeaves: 14)
                            Text(s.days == 0 ? "A new sprig" : "\(s.days) day\(s.days == 1 ? "" : "s") growing").heading(.title2).foregroundStyle(Theme.text)
                            Text(leafRule)
                                .font(.body).foregroundStyle(Theme.soft).fixedSize(horizontal: false, vertical: true)
                        }
                    }
                    if let n = model.leafNumbers(Date().ymd) { TodayLeaf(n: n, counts: model.exerciseCounts, health: HealthSync.shared.asked) }
                    Toggle(isOn: $m.exerciseCounts) {
                        VStack(alignment: .leading, spacing: 2) {
                            Text("Count exercise").font(.body.weight(.semibold)).foregroundStyle(Theme.text)
                            Text(HealthSync.shared.asked ? "Calories you burn raise the most a day can be and still grow a leaf. Your goal and your numbers don't change."
                                                         : "Connect Apple Health first.")
                                .font(.subheadline).foregroundStyle(Theme.muted).fixedSize(horizontal: false, vertical: true)
                        }
                    }
                    .tint(Theme.ring)
                    .disabled(!HealthSync.shared.asked)
                    .padding(16).background(RoundedRectangle(cornerRadius: Theme.radius, style: .continuous).fill(Theme.panel))
                    LastWeek()
                }
                .padding(16)
            }
            .background(PatternedPage())
            .navigationTitle("Your sprig").navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } } }
        }
        .presentationDetents([.large])
    }
}

/// Today's leaf in numbers: what they've eaten, and the range that grows a leaf, with what exercise adds (or would add).
private struct TodayLeaf: View {
    var n: (eaten: Double, goal: Double, burned: Double, low: Double, high: Double)
    var counts: Bool
    var health: Bool

    var body: some View {
        let plain = n.goal * Theme.overBand
        VStack(alignment: .leading, spacing: 12) {
            Text("Today").heading(.headline).foregroundStyle(Theme.text)
            HStack(spacing: 0) {
                figure(Fmt.kcal(n.eaten), "eaten")
                figure(Fmt.kcal(n.goal), "goal")
                if health { figure("\(Fmt.kcal(n.burned))", "burned") }
            }
            VStack(alignment: .leading, spacing: 4) {
                Text("A leaf for \(Fmt.kcal(n.low))–\(Fmt.kcal(counts ? n.high : plain)) kcal").font(.body.weight(.semibold)).foregroundStyle(Theme.text)
                if health && n.burned > 0 {
                    Text(counts ? "105% of your goal is \(Fmt.kcal(plain)), plus the \(Fmt.kcal(n.burned)) you've burned."
                                : "With Count exercise on, it would go up to \(Fmt.kcal(plain + n.burned)) (the \(Fmt.kcal(n.burned)) you've burned).")
                        .font(.subheadline).foregroundStyle(Theme.muted).fixedSize(horizontal: false, vertical: true)
                }
            }
        }
        .padding(16).frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: Theme.radius, style: .continuous).fill(Theme.panel))
        .accessibilityElement(children: .combine)
    }

    private func figure(_ value: String, _ label: String) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            Text(value).font(.number(.title3)).foregroundStyle(Theme.text)
            Text(label).font(.subheadline).foregroundStyle(Theme.muted)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

/// The last seven days, each with what was eaten, the most that counted, and whether it grew a leaf.
private struct LastWeek: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        let days = (1...7).compactMap { Calendar.current.date(byAdding: .day, value: -$0, to: Date())?.ymd }
        VStack(alignment: .leading, spacing: 10) {
            Text("The last 7 days").heading(.headline).foregroundStyle(Theme.text)
            VStack(spacing: 0) {
                ForEach(days, id: \.self) { d in
                    if let n = model.leafNumbers(d) {
                        let leaf = model.leafDays.contains(d)
                        HStack(spacing: 12) {
                            Text(Date.fromYMD(d)?.formatted(.dateTime.weekday(.wide)) ?? d).font(.body).foregroundStyle(Theme.text)
                            Spacer(minLength: 8)
                            VStack(alignment: .trailing, spacing: 0) {
                                Text("\(Fmt.kcal(n.eaten)) kcal").font(.number(.body, .medium)).foregroundStyle(Theme.text)
                                Text(model.exerciseCounts && n.burned > 0 ? "up to \(Fmt.kcal(n.high)) with \(Fmt.kcal(n.burned)) burned" : "up to \(Fmt.kcal(n.high))")
                                    .font(.footnote.monospacedDigit()).foregroundStyle(Theme.muted)
                            }
                            Group {
                                if leaf { LeafShape().fill(Theme.ring).frame(width: 9, height: 16).rotationEffect(.degrees(25)) }
                                else { Circle().fill(Theme.raised2).frame(width: 6, height: 6) }
                            }
                            .frame(width: 24)
                        }
                        .padding(.horizontal, 16).padding(.vertical, 10)
                        .accessibilityElement(children: .ignore)
                        .accessibilityLabel("\(Date.fromYMD(d)?.formatted(.dateTime.weekday(.wide)) ?? d): \(Fmt.kcal(n.eaten)) calories, up to \(Fmt.kcal(n.high)) counted\(leaf ? ", grew a leaf" : "")")
                        if d != days.last { Divider().padding(.leading, 16) }
                    }
                }
            }
            .background(RoundedRectangle(cornerRadius: Theme.radius, style: .continuous).fill(Theme.panel))
        }
    }
}

/// On a past day's summary: the leaf it earned (nothing is said about a day that didn't).
struct LeafCredit: View {
    var body: some View {
        HStack(spacing: 10) {
            LeafShape().fill(Theme.ring).frame(width: 10, height: 18).rotationEffect(.degrees(25)).frame(width: 24)
            Text("This day grew a leaf").font(.subheadline.weight(.semibold)).foregroundStyle(Theme.soft)
            Spacer(minLength: 0)
        }
        .frame(minHeight: 36)
        .accessibilityElement(children: .combine)
    }
}

/// The morning after a day on target: the sprig grows its new leaf, once, then gets out of the way.
struct Celebration: View {
    var streak: Streak
    var done: () -> Void
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var shown = 0
    @State private var start = Date()


    var body: some View {
        ZStack {
            Color.black.opacity(0.32).ignoresSafeArea().onTapGesture(perform: done)
            if !reduceMotion { FallingLeaves(start: start).allowsHitTesting(false) }
            VStack(spacing: 16) {
                StreakVine(leaves: shown, bud: false, unit: 2.2, gap: 14, maxLeaves: 14)
                    .animation(reduceMotion ? nil : .spring(duration: 0.6, bounce: 0.35), value: shown)
                Text("Yesterday grew a leaf")
                    .heading(.title).foregroundStyle(Theme.text).multilineTextAlignment(.center)
                Text("\(streak.days) day\(streak.days == 1 ? "" : "s") growing. Keep it going.")
                    .font(.body).foregroundStyle(Theme.soft).multilineTextAlignment(.center)
                BigButton(title: "Lovely", icon: "leaf", lead: true, action: done)
            }
            .padding(24)
            .background(RoundedRectangle(cornerRadius: 28, style: .continuous).fill(Theme.panel).shadow(color: .black.opacity(0.18), radius: 24, y: 10))
            .padding(24)
        }
        .task {
            shown = max(0, streak.days - 1)
            start = Date()
            try? await Task.sleep(for: .milliseconds(450))
            shown = streak.days
            UINotificationFeedbackGenerator().notificationOccurred(.success)
        }
        .accessibilityAddTraits(.isModal)
        .accessibilityAction(.escape, done)
        .task {
            // a moment, not a gate in front of breakfast: it goes by itself (unless VoiceOver is reading it)
            try? await Task.sleep(for: .seconds(6))
            if !UIAccessibility.isVoiceOverRunning { done() }
        }
    }
}

/// A few olive leaves drifting down for a moment.
private struct FallingLeaves: View {
    var start: Date
    private let leaves: [(x: CGFloat, delay: Double, sway: CGFloat, size: CGFloat, spin: Double)] = (0..<14).map { i in
        let r = { (k: Int) in CGFloat((i * 9301 + k * 49297) % 233280) / 233280 }
        return (r(1), Double(r(2)) * 0.9, 18 + r(3) * 26, 10 + r(4) * 8, Double(r(5)) * 2 - 1)
    }

    var body: some View {
        TimelineView(.animation) { tl in
            let t = tl.date.timeIntervalSince(start)
            Canvas { ctx, size in
                guard t < 3.4 else { return }
                for l in leaves {
                    let p = max(0, t - l.delay) / 2.4
                    guard p > 0, p < 1 else { continue }
                    let y = -20 + p * (size.height * 0.75)
                    let x = l.x * size.width + sin(p * 6 + l.spin * 3) * l.sway
                    let leaf = LeafShape().path(in: CGRect(x: -l.size / 4, y: -l.size / 2, width: l.size / 2, height: l.size))
                        .applying(CGAffineTransform(rotationAngle: CGFloat(l.spin + p * 3)).concatenating(CGAffineTransform(translationX: x, y: y)))
                    ctx.opacity = min(1, (1 - p) * 2)
                    ctx.fill(leaf, with: .color(Theme.ring))
                }
            }
        }
        .ignoresSafeArea()
    }
}

/// Asked once on Today: reminders when a meal isn't in yet, and a cheer the morning after a day on target.
struct RemindersOffer: View {
    @Environment(AppModel.self) private var model
    @State private var busy = false

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Label { Text("Meal reminders") } icon: { Image(systemName: "bell").foregroundStyle(Theme.accent) }.heading(.title3).foregroundStyle(Theme.text)
            Text("A nudge when a meal isn't in yet, with your usual one tap away, and a cheer after a good day.")
                .font(.body).foregroundStyle(Theme.soft).fixedSize(horizontal: false, vertical: true)
            HStack(spacing: 10) {
                Button { Reminders.shared.notNow(); model.remindersAnswered() } label: {
                    Text("Not now").font(.body.weight(.semibold)).foregroundStyle(Theme.text)
                        .frame(maxWidth: .infinity, minHeight: 52).background(Capsule().fill(Theme.raised)).contentShape(Capsule())
                }
                Button {
                    Task {
                        busy = true
                        let on = await Reminders.shared.turnOnAll()
                        busy = false
                        model.remindersAnswered()
                        model.toast = on ? Toast(text: "Reminders on. Change them in Settings.")
                                         : Toast(text: "Notifications are off for Food. Turn them on in iPhone Settings → Food.", error: true)
                    }
                } label: {
                    HStack(spacing: 8) { if busy { ProgressView().tint(Theme.fillInk) }; Text("Turn on") }
                        .font(.body.weight(.semibold)).foregroundStyle(Theme.fillInk)
                        .frame(maxWidth: .infinity, minHeight: 52).background(Capsule().fill(Theme.fill)).contentShape(Capsule())
                }
                .disabled(busy)
            }
            .buttonStyle(.plain)
        }
        .padding(18).background(RoundedRectangle(cornerRadius: Theme.radius, style: .continuous).fill(Theme.panel))
    }
}
