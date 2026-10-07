import SwiftUI

extension HealthRings {
    init(_ a: HealthSync.Activity, style: RingStyle) {
        self.init(move: a.move, moveGoal: a.moveGoal, exercise: a.exercise, exerciseGoal: a.exerciseGoal, stand: a.stand, standGoal: a.standGoal, style: style)
    }
}

/// One day, both halves: food (its four rings and lines, the leaf) and Apple Health (its rings, steps, sleep), with the way in.
struct DayDetail: View {
    var date: Date
    var food: HistoryDay?
    var goals: Goals
    var leaf: Bool
    var activity: HealthSync.Activity?
    var open: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text(date.formatted(.dateTime.weekday(.wide).day().month(.wide))).heading(.title3).foregroundStyle(Theme.text)
            HStack(alignment: .center, spacing: 16) {
                FoodRings(kcal: food?.kcal ?? 0, protein: food?.protein ?? 0, carbs: food?.carbs ?? 0, fat: food?.fat ?? 0, goals: goals, style: .detail)
                    .frame(width: 76, height: 76)
                VStack(alignment: .leading, spacing: 3) {
                    if let f = food, f.kcal > 0 {
                        FoodRingLines(kcal: f.kcal, protein: f.protein, carbs: f.carbs, fat: f.fat, goals: goals)
                        if leaf {
                            HStack(spacing: 6) {
                                LeafShape().fill(Theme.ring).frame(width: 7, height: 12).rotationEffect(.degrees(25))
                                Text("Grew a leaf").font(.subheadline.weight(.semibold)).foregroundStyle(Theme.soft)
                            }
                        }
                    } else {
                        Text(date.ymd > Date().ymd ? "Nothing planned yet" : "Nothing logged").font(.body).foregroundStyle(Theme.muted)
                    }
                }
                Spacer(minLength: 0)
            }
            if let a = activity, a.rings || a.steps > 0 || a.sleepMin != nil {
                Divider().overlay(Theme.raised2)
                HStack(alignment: .center, spacing: 16) {
                    if a.rings {
                        HealthRings(a, style: .detail).frame(width: 76, height: 76)
                    } else {
                        Image(systemName: "figure.walk").font(.title).foregroundStyle(Theme.accent).frame(width: 76)
                    }
                    VStack(alignment: .leading, spacing: 3) {
                        if a.rings {
                            HealthRingLines(move: a.move, moveGoal: a.moveGoal, exercise: a.exercise, exerciseGoal: a.exerciseGoal, stand: a.stand, standGoal: a.standGoal)
                        }
                        let extra = [a.steps > 0 ? "\(Fmt.kcal(a.steps)) steps" : nil, a.sleepMin.map { "slept \($0 / 60) h \($0 % 60) min" }].compactMap { $0 }
                        if !extra.isEmpty { Text(extra.joined(separator: " · ")).font(.subheadline).foregroundStyle(Theme.muted) }
                    }
                    Spacer(minLength: 0)
                }
                .accessibilityElement(children: .combine)
            }
            BigButton(title: "Open this day", icon: "arrow.right", lead: true, action: open)
        }
        .padding(18)
        .background(RoundedRectangle(cornerRadius: Theme.radius, style: .continuous).fill(Theme.panel))
    }
}
