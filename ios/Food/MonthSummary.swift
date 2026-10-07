import SwiftUI

/// The month in one glance: what an average day came to (the one big number), the month's rings beside it, protein, carbs
/// and fat on average against their goals, and the leaves it grew. A month that's over offers its recap.
struct MonthSummary: View {
    var data: MonthData
    var loaded: Bool
    var failed: Bool
    var recap: () -> Void

    private var name: String { data.month.formatted(.dateTime.month(.wide)) }
    private var current: Bool { Calendar.current.isDate(data.month, equalTo: Date(), toGranularity: .month) }
    private var started: Bool { data.month <= Date() }

    var body: some View {
        let a = data.average(data.days)
        VStack(alignment: .leading, spacing: 14) {
            Group {
                if a.days > 0 { numbers(a) } else { empty }
            }
            .accessibilityElement(children: .ignore)
            .accessibilityLabel(spoken(a))
            .accessibilityIdentifier("month-summary")
            if !current && started && a.days > 0 {
                Button(action: recap) {
                    Label("See the recap", systemImage: "sparkles").font(.body.weight(.semibold)).foregroundStyle(Theme.accent).frame(minHeight: 44)
                }
                .buttonStyle(.plain)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(18)
        .background(RoundedRectangle(cornerRadius: Theme.radius, style: .continuous).fill(Theme.panel))
    }

    private func numbers(_ a: MonthAverage) -> some View {
        let goals = data.goals
        let over = goals.kcal > 0 && a.kcal > goals.kcal * Theme.overBand
        return VStack(alignment: .leading, spacing: 12) {
            Text(current ? "An average day so far" : "An average day").font(.subheadline.weight(.semibold)).foregroundStyle(Theme.muted)
            HStack(alignment: .center, spacing: 16) {
                FoodRings(kcal: a.kcal, protein: a.protein, carbs: a.carbs, fat: a.fat, goals: goals, style: .detail).frame(width: 76, height: 76)
                VStack(alignment: .leading, spacing: 0) {
                    Text(Fmt.kcal(a.kcal)).heroNumber().foregroundStyle(over ? Theme.over : Theme.text).contentTransition(.numericText())
                    Text("of \(Fmt.kcal(goals.kcal)) kcal a day").font(.subheadline).foregroundStyle(Theme.soft)
                }
            }
            VStack(alignment: .leading, spacing: 3) {
                RingLine(name: "Protein", value: RingLine.of(a.protein, goals.protein_g, "g"), colors: FoodRingColor.protein)
                RingLine(name: "Carbs", value: RingLine.of(a.carbs, goals.carbs_g, "g"), colors: FoodRings.colors(RingSpec.of(a.carbs, goals.carbs_g), FoodRingColor.carbs))
                RingLine(name: "Fat", value: RingLine.of(a.fat, goals.fat_g, "g"), colors: FoodRings.colors(RingSpec.of(a.fat, goals.fat_g), FoodRingColor.fat))
            }
            LeafTally(leaves: a.leaves, logged: a.days)
        }
    }

    @ViewBuilder private var empty: some View {
        if failed {
            Text("Couldn't reach Home Assistant. Pull down to try again.").font(.body).foregroundStyle(Theme.muted)
        } else if !loaded {
            ProgressView().frame(maxWidth: .infinity, minHeight: 60)
        } else {
            Text(emptyLine).font(.body).foregroundStyle(Theme.soft)
        }
    }

    private var emptyLine: String {
        if !started { return "\(name) hasn't started yet. Planned days show below." }
        return current ? "Your month's numbers start once a day's logged." : "Nothing was logged in \(name)."
    }

    /// "October so far: on average 1,372 of 1,400 calories a day, protein …, 12 days logged, 4 leaves grown".
    private func spoken(_ a: MonthAverage) -> String {
        if a.days == 0 { return failed ? "Couldn't reach Home Assistant" : loaded ? emptyLine : "Loading \(name)" }
        return "\(name)\(current ? " so far" : ""): " + a.spoken(data.goals)
    }
}

/// "4 leaves grown · 12 days logged", with the sprig's leaf.
struct LeafTally: View {
    var leaves: Int
    var logged: Int

    var body: some View {
        HStack(spacing: 7) {
            LeafShape().fill(leaves > 0 ? Theme.ring : Theme.raised2).frame(width: 7, height: 12).rotationEffect(.degrees(25)).frame(width: 9)
            Text("\(MonthAverage.count(leaves, "leaf", "leaves")) grown · \(MonthAverage.count(logged, "day")) logged")
                .font(.subheadline.weight(.semibold)).foregroundStyle(Theme.soft)
        }
    }
}

/// One week on average: its rings and lines, the leaves it grew, and Apple's rings on average once Health has them.
struct WeekDetail: View {
    var days: [String]
    var average: MonthAverage
    var goals: Goals

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            VStack(alignment: .leading, spacing: 2) {
                Text(title).heading(.title3).foregroundStyle(Theme.text)
                Text("A day on average").font(.subheadline).foregroundStyle(Theme.muted)
            }
            HStack(alignment: .center, spacing: 16) {
                FoodRings(kcal: average.kcal, protein: average.protein, carbs: average.carbs, fat: average.fat, goals: goals, style: .detail)
                    .frame(width: 76, height: 76)
                VStack(alignment: .leading, spacing: 3) {
                    if average.days > 0 {
                        FoodRingLines(kcal: average.kcal, protein: average.protein, carbs: average.carbs, fat: average.fat, goals: goals)
                    } else {
                        Text("Nothing logged yet").font(.body).foregroundStyle(Theme.muted)
                    }
                }
                Spacer(minLength: 0)
            }
            .accessibilityElement(children: .ignore)
            .accessibilityLabel(average.spoken(goals))
            if average.days > 0 { LeafTally(leaves: average.leaves, logged: average.days) }
            if let h = average.health {
                Divider().overlay(Theme.raised2)
                HStack(alignment: .center, spacing: 16) {
                    HealthRings(h, style: .detail).frame(width: 76, height: 76)
                    HealthRingLines(move: h.move, moveGoal: h.moveGoal, exercise: h.exercise, exerciseGoal: h.exerciseGoal, stand: h.stand, standGoal: h.standGoal)
                    Spacer(minLength: 0)
                }
                .accessibilityElement(children: .combine)
            }
        }
        .padding(18)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: Theme.radius, style: .continuous).fill(Theme.panel))
    }

    /// "6 – 12 October" (a week's days in this month).
    private var title: String {
        guard let first = days.first.flatMap(Date.fromYMD), let last = days.last.flatMap(Date.fromYMD) else { return "This week" }
        let end = last.formatted(.dateTime.day().month(.wide))
        return days.count == 1 ? end : "\(first.formatted(.dateTime.day())) – \(end)"
    }
}
