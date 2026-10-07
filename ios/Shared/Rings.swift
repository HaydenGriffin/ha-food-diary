import SwiftUI

/// Rings, a play on Apple's activity rings. Food has four in the Olive Garden palette: Calories (sage) outside, then
/// Protein (honey), Carbs (slate teal) and Fat (plum). Apple Health's three (Move, Exercise, Stand) are drawn by the same
/// `RingStack` in Apple's colours, so the two always sit together at the same weight. Protein goes round again past its
/// goal, as Apple's do. Calories, carbs and fat never lap (more isn't better there): they turn clay once clearly over, and
/// the calories flame turns into a leaf when the day is in range. Planned food still to come is the faint part of a ring:
/// it counts already (the numbers include it), but they can see what's eaten and what's still the plan.
enum FoodRingColor {
    static let kcal = (Color(UIColor(hex: 0x56653A)), Color(UIColor(hex: 0x8CA24A)))
    static let protein = (Color(UIColor(hex: 0x9C5A1C)), Color(UIColor(hex: 0xD7962C)))
    static let carbs = (Color(UIColor(hex: 0x2C5F69)), Color(UIColor(hex: 0x529B9B)))
    static let fat = (Color(UIColor(hex: 0x6B3F5E)), Color(UIColor(hex: 0xA6708F)))
    static let over = (Color(UIColor(hex: 0x8A3B26)), Color(UIColor(hex: 0xC9694A)))
}

/// Apple's ring colours, softened a touch to sit on parchment.
enum HealthRingColor {
    static let move = (Color(UIColor(hex: 0xC92F55)), Color(UIColor(hex: 0xEE5C82)))
    static let exercise = (Color(UIColor(hex: 0x4F8F1C)), Color(UIColor(hex: 0x8BC93F)))
    static let stand = (Color(UIColor(hex: 0x1D84A6)), Color(UIColor(hex: 0x4CC0DF)))
}

/// One ring weight per size, so food and Health rings beside each other always match.
struct RingStyle {
    var lineWidth: CGFloat
    var gap: CGFloat
    /// Today's card.
    static let hero = RingStyle(lineWidth: 10.5, gap: 2.5)
    /// A calendar day's detail.
    static let detail = RingStyle(lineWidth: 6.5, gap: 1.5)
    /// A calendar month cell.
    static let mini = RingStyle(lineWidth: 2.6, gap: 0.7)
}

/// One ring. `progress` 1 is the goal; past 1 it laps (unless `lap` is false, when it stops full). `planned`, when more
/// than `progress`, is where the ring will reach once the planned food is eaten: drawn faint ahead of the solid part.
struct GradientRing: View, Animatable {
    var progress: Double
    var colors: (Color, Color)
    var lineWidth: CGFloat
    var glyph: String? = nil
    var lap = true
    var planned = 0.0

    var animatableData: AnimatablePair<Double, Double> {
        get { AnimatablePair(progress, planned) }
        set { progress = newValue.first; planned = newValue.second }
    }

    var body: some View {
        let p = max(0, lap ? min(progress, 2) : min(progress, 1))
        let ahead = min(max(planned, 0), 1)
        ZStack {
            Circle().stroke(colors.0.opacity(0.13), lineWidth: lineWidth)
            if ahead > min(p, 1) {
                Circle().trim(from: 0, to: ahead)
                    .stroke(colors.1.opacity(0.32), style: StrokeStyle(lineWidth: lineWidth, lineCap: .round))
                    .rotationEffect(.degrees(-90))
            }
            arc(min(p, 1), from: colors.0, to: colors.1)
            if p > 1 {
                arc(p - 1, from: colors.1, to: colors.1)
                    .shadow(color: .black.opacity(0.3), radius: lineWidth * 0.16)  // the second lap rides over the first
            }
            tip(p)
        }
        .padding(lineWidth / 2)
    }

    private func arc(_ amount: Double, from: Color, to: Color) -> some View {
        Circle().trim(from: 0, to: amount)
            .stroke(AngularGradient(colors: [from, to], center: .center, startAngle: .zero, endAngle: .degrees(360 * max(amount, 0.01))),
                    style: StrokeStyle(lineWidth: lineWidth, lineCap: .round))
            .rotationEffect(.degrees(-90))
    }

    /// The leading end: a round cap that casts a little shadow back onto the ring as it closes, with the ring's glyph on it.
    private func tip(_ p: Double) -> some View {
        GeometryReader { g in
            let r = min(g.size.width, g.size.height) / 2
            let turn = p >= 1 && p.truncatingRemainder(dividingBy: 1) == 0 ? 1 : p.truncatingRemainder(dividingBy: 1)
            let a = turn * 2 * .pi - .pi / 2
            let started = p > 0.01
            ZStack {
                if started {
                    Circle().fill(colors.1)
                        .shadow(color: .black.opacity(p > 0.9 ? 0.32 : 0), radius: lineWidth * 0.2,
                                x: -sin(a) * lineWidth * 0.18, y: cos(a) * lineWidth * 0.18)
                }
                if let glyph, lineWidth >= 10 {
                    Image(systemName: glyph).font(.system(size: lineWidth * 0.6, weight: .heavy))
                        .foregroundStyle(started ? Color.white : colors.0.opacity(0.7))
                        .accessibilityHidden(true)
                }
            }
            .frame(width: lineWidth, height: lineWidth)
            .position(x: g.size.width / 2 + cos(a) * r, y: g.size.height / 2 + sin(a) * r)
        }
        .allowsHitTesting(false)
    }
}

/// What one ring in a stack shows.
struct RingSpec {
    var progress: Double
    var colors: (Color, Color)
    var glyph: String?
    var lap = true
    var planned = 0.0   // eaten plus planned, against the goal (0: nothing planned)

    static func of(_ value: Double, _ goal: Double) -> Double { goal > 0 ? value / goal : 0 }
}

/// Rings nested outside in, spun up from empty the first time they appear.
struct RingStack: View {
    var rings: [RingSpec]
    var style: RingStyle
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var shown = 0.0

    var body: some View {
        let step = style.lineWidth + style.gap
        ZStack {
            ForEach(rings.indices, id: \.self) { i in
                GradientRing(progress: rings[i].progress * shown, colors: rings[i].colors, lineWidth: style.lineWidth,
                             glyph: rings[i].glyph, lap: rings[i].lap, planned: rings[i].planned * shown)
                    .padding(step * CGFloat(i))
            }
        }
        .animation(reduceMotion ? nil : .easeOut(duration: 0.7), value: rings.map(\.progress) + rings.map(\.planned))
        .onAppear {
            guard shown == 0 else { return }
            if reduceMotion { shown = 1 } else { withAnimation(.spring(duration: 1.2, bounce: 0.12).delay(0.1)) { shown = 1 } }
        }
        .accessibilityHidden(true)
    }
}

/// The four food rings: calories, protein, carbs and fat against their goals. The values include `planned` (food on the plan
/// still to come), which is drawn as the faint part of each ring; colours follow the whole day, plan included.
struct FoodRings: View {
    var kcal: Double, protein: Double, carbs: Double, fat: Double
    var goals: Goals
    var planned = Nutrients()
    var style: RingStyle = .hero

    var body: some View {
        let k = RingSpec.of(kcal, goals.kcal)
        let inRange = goals.kcal > 0 && k >= 0.75 && k <= Theme.overBand
        RingStack(rings: [
            ring(kcal, planned.kcal, goals.kcal, Self.colors(k, FoodRingColor.kcal), glyph: inRange ? "leaf.fill" : "flame.fill", lap: false),
            ring(protein, planned.protein_g, goals.protein_g, FoodRingColor.protein, glyph: "dumbbell.fill", lap: true),
            ring(carbs, planned.carbs_g, goals.carbs_g, Self.colors(RingSpec.of(carbs, goals.carbs_g), FoodRingColor.carbs), glyph: "bolt.fill", lap: false),
            ring(fat, planned.fat_g, goals.fat_g, Self.colors(RingSpec.of(fat, goals.fat_g), FoodRingColor.fat), glyph: "drop.fill", lap: false),
        ], style: style)
    }

    /// Solid up to what's eaten, faint on to the whole day.
    private func ring(_ value: Double, _ plan: Double, _ goal: Double, _ colors: (Color, Color), glyph: String, lap: Bool) -> RingSpec {
        RingSpec(progress: RingSpec.of(value - plan, goal), colors: colors, glyph: glyph, lap: lap, planned: plan > 0 ? RingSpec.of(value, goal) : 0)
    }

    /// A ring that shouldn't go past its goal turns clay once it's clearly over.
    static func colors(_ progress: Double, _ normal: (Color, Color)) -> (Color, Color) {
        progress > Theme.overBand ? FoodRingColor.over : normal
    }
}

/// Apple Health's three rings (Move, Exercise, Stand), at the same weight as the food rings beside them.
struct HealthRings: View {
    var move: Double, moveGoal: Double
    var exercise: Double, exerciseGoal: Double
    var stand: Double, standGoal: Double
    var style: RingStyle = .hero

    var body: some View {
        RingStack(rings: [
            RingSpec(progress: RingSpec.of(move, moveGoal), colors: HealthRingColor.move, glyph: "arrow.right"),
            RingSpec(progress: RingSpec.of(exercise, exerciseGoal), colors: HealthRingColor.exercise, glyph: "chevron.right.2"),
            RingSpec(progress: RingSpec.of(stand, standGoal), colors: HealthRingColor.stand, glyph: "arrow.up"),
        ], style: style)
    }
}

/// One ring's line beside its rings, Apple-Fitness style: a dot in the ring's colours, its name, then the numbers. Name and
/// numbers are one piece of text, so a tight line shrinks evenly instead of cutting off the numbers.
struct RingLine: View {
    var name: String
    var value: String
    var colors: (Color, Color)

    var body: some View {
        HStack(spacing: 7) {
            Circle().fill(LinearGradient(colors: [colors.0, colors.1], startPoint: .bottomLeading, endPoint: .topTrailing)).frame(width: 9, height: 9)
            Text("\(Text(name).font(.subheadline.weight(.semibold)).foregroundStyle(Theme.text))  \(Text(value).font(.number(.subheadline, .semibold)).foregroundStyle(colors.0))")
                .contentTransition(.numericText())
        }
        .lineLimit(1).minimumScaleFactor(0.7)
    }

    /// "820 / 1,500" (calories: the name says the unit), "388 / 350 kcal", "41 / 95 g".
    static func of(_ value: Double, _ goal: Double, _ unit: String) -> String {
        let n = unit.isEmpty || unit == "kcal" ? "\(Fmt.kcal(value)) / \(Fmt.kcal(goal))" : "\(Int(value.rounded())) / \(Int(goal.rounded()))"
        return unit.isEmpty ? n : "\(n) \(unit)"
    }
}

/// The food rings' four lines, the same on Today and in the calendar.
struct FoodRingLines: View {
    var kcal: Double, protein: Double, carbs: Double, fat: Double
    var goals: Goals

    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            RingLine(name: "Calories", value: RingLine.of(kcal, goals.kcal, ""), colors: FoodRings.colors(RingSpec.of(kcal, goals.kcal), FoodRingColor.kcal))
            RingLine(name: "Protein", value: RingLine.of(protein, goals.protein_g, "g"), colors: FoodRingColor.protein)
            RingLine(name: "Carbs", value: RingLine.of(carbs, goals.carbs_g, "g"), colors: FoodRings.colors(RingSpec.of(carbs, goals.carbs_g), FoodRingColor.carbs))
            RingLine(name: "Fat", value: RingLine.of(fat, goals.fat_g, "g"), colors: FoodRings.colors(RingSpec.of(fat, goals.fat_g), FoodRingColor.fat))
        }
    }

    /// "Protein 41 of 95 grams, carbs 120 of 180 grams, fat 30 of 55 grams" for VoiceOver.
    static func spoken(protein: Double, carbs: Double, fat: Double, goals: Goals) -> String {
        func g(_ v: Double, _ goal: Double) -> String { "\(Int(v.rounded())) of \(Int(goal.rounded())) grams" }
        return "Protein \(g(protein, goals.protein_g)), carbs \(g(carbs, goals.carbs_g)), fat \(g(fat, goals.fat_g))"
    }
}

/// Apple Health's three lines, matching the food lines.
struct HealthRingLines: View {
    var move: Double, moveGoal: Double
    var exercise: Double, exerciseGoal: Double
    var stand: Double, standGoal: Double

    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            RingLine(name: "Move", value: RingLine.of(move, moveGoal, "kcal"), colors: HealthRingColor.move)
            RingLine(name: "Exercise", value: RingLine.of(exercise, exerciseGoal, "min"), colors: HealthRingColor.exercise)
            RingLine(name: "Stand", value: RingLine.of(stand, standGoal, "h"), colors: HealthRingColor.stand)
        }
    }
}
