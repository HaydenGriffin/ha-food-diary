import SwiftUI

/// The month's days in Monday-to-Sunday rows. Each day shows its food rings with the leaf it grew, and Apple Health's rings
/// under them once Health has some; the column at the end of each row is that week on average. In the photo look each day
/// is its picture instead, and the week column steps aside so the photos keep their size.
struct MonthGrid: View {
    var data: MonthData
    var look: String
    @Binding var pick: MonthPick?

    private var photos: Bool { look == "photos" }
    private let weekWidth: CGFloat = 44

    var body: some View {
        VStack(spacing: 6) {
            HStack(spacing: 2) {
                ForEach(Self.weekdays, id: \.self) { w in
                    Text(w).font(.footnote.weight(.semibold)).foregroundStyle(Theme.muted).frame(maxWidth: .infinity)
                }
                if !photos { Text("Week").font(.caption.weight(.semibold)).foregroundStyle(Theme.muted).frame(width: weekWidth) }
            }
            .accessibilityHidden(true)
            ForEach(data.weeks.indices, id: \.self) { i in row(data.weeks[i]) }
        }
        .padding(12)
        .background(RoundedRectangle(cornerRadius: Theme.radius, style: .continuous).fill(Theme.panel))
    }

    /// Monday first, whatever the phone's setting (like the week strip).
    private static var weekdays: [String] {
        let s = Calendar.current.veryShortStandaloneWeekdaySymbols
        return Array(s[1...] + s[..<1])
    }

    private func row(_ week: [String?]) -> some View {
        HStack(alignment: .top, spacing: 2) {
            ForEach(0..<7, id: \.self) { j in
                if let d = week[j] { day(d) } else { Color.clear.frame(maxWidth: .infinity, minHeight: 40) }
            }
            if !photos { weekCell(week.compactMap { $0 }) }
        }
    }

    // ---------- a day ----------

    private func day(_ d: String) -> some View {
        let date = Date.fromYMD(d) ?? Date()
        let f = data.food[d]
        let isToday = d == Date().ymd, sel = pick == .day(d)
        let future = d > Date().ymd
        return Button { withAnimation(.snappy) { pick = .day(d) } } label: {
            VStack(spacing: 4) {
                if photos {
                    photoCell(d, isToday: isToday, future: future)
                } else {
                    Text(date.formatted(.dateTime.day())).font(.number(.footnote, isToday || sel ? .bold : .medium)).lineLimit(1).fixedSize()
                        .foregroundStyle(isToday ? Theme.accent : Theme.text)
                    FoodRings(kcal: f?.kcal ?? 0, protein: f?.protein ?? 0, carbs: f?.carbs ?? 0, fat: f?.fat ?? 0, goals: data.goals, style: .mini)
                        .frame(width: 32, height: 32)
                        .opacity(future && (f?.kcal ?? 0) == 0 ? 0.4 : 1)
                        .overlay(alignment: .topTrailing) {
                            if data.leaf(d) { LeafShape().fill(Theme.ring).frame(width: 6, height: 11).rotationEffect(.degrees(35)).offset(x: 5, y: -4) }
                        }
                    if data.showHealth && !future {  // Apple's rings under the food ones, at the same weight
                        HealthRings(data.activity[d] ?? HealthSync.Activity(), style: .mini).frame(width: 32, height: 32)
                    }
                }
            }
            .frame(maxWidth: .infinity, minHeight: 40)
            .padding(.vertical, photos ? 0 : 4)
            .background(RoundedRectangle(cornerRadius: 12, style: .continuous).fill(sel ? Theme.raised : .clear))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel(dayLabel(date, f, leaf: data.leaf(d)))
        .accessibilityAddTraits(sel ? .isSelected : [])
    }

    private func dayLabel(_ date: Date, _ f: HistoryDay?, leaf: Bool) -> String {
        let goals = data.goals
        var parts = [date.formatted(.dateTime.weekday(.wide).day().month(.wide))]
        if let f, f.kcal > 0 {
            parts.append("\(Fmt.kcal(f.kcal)) of \(Fmt.kcal(goals.kcal)) calories, " + MonthAverage.lines(protein: f.protein, carbs: f.carbs, fat: f.fat, goals: goals))
        }
        if let a = data.activity[date.ymd], a.rings {
            parts.append("Move \(Fmt.kcal(a.move)) of \(Fmt.kcal(a.moveGoal)) calories, exercise \(Int(a.exercise)) of \(Int(a.exerciseGoal)) minutes, stand \(Int(a.stand)) of \(Int(a.standGoal)) hours")
        }
        if leaf { parts.append("grew a leaf") }
        return parts.joined(separator: ", ")
    }

    /// A day as its food: the photo, or a drawn tile when it has food but no picture, or an outline when it's empty.
    private func photoCell(_ d: String, isToday: Bool, future: Bool) -> some View {
        let date = Date.fromYMD(d) ?? Date()
        let photo = data.photos[d]
        return ZStack(alignment: .bottomTrailing) {
            if let photo {
                FoodImage(path: photo, name: "food", corner: 10)
            } else if (data.logged[d] ?? 0) > 0 {
                RoundedRectangle(cornerRadius: 10, style: .continuous).fill(Theme.accentSoft)
                    .overlay { Image(systemName: "fork.knife").font(.footnote).foregroundStyle(Theme.accent) }
            } else {
                RoundedRectangle(cornerRadius: 10, style: .continuous).strokeBorder(Theme.raised2, style: StrokeStyle(lineWidth: 1, dash: future ? [3, 3] : []))
            }
            Text(date.formatted(.dateTime.day())).font(.number(.caption2, .bold)).foregroundStyle(photo != nil ? .white : (isToday ? Theme.accent : Theme.muted))
                .padding(3).shadow(color: photo != nil ? .black.opacity(0.6) : .clear, radius: 2)
        }
        .frame(width: 44, height: 44)
        .overlay(alignment: .topTrailing) {
            if data.leaf(d) {  // the same small leaf as on a day's ring, in the corner of its photo
                LeafShape().fill(Theme.ring).frame(width: 7, height: 12).rotationEffect(.degrees(35))
                    .frame(width: 18, height: 18).background(Circle().fill(Theme.panel)).offset(x: 6, y: -6)
            }
        }
        .padding(.bottom, 6)
    }

    // ---------- a week ----------

    /// The week on average, in the same shape as a day: the average calories where a day has its date, the rings under it.
    /// A week that hasn't started has nothing to average, so it stays an empty slot.
    @ViewBuilder private func weekCell(_ days: [String]) -> some View {
        let a = data.average(days)
        let first = days.first ?? ""
        let sel = pick == .week(first)
        if first > Date().ymd {
            Color.clear.frame(width: weekWidth, height: 40)
        } else {
            Button { withAnimation(.snappy) { pick = .week(first) } } label: {
                VStack(spacing: 4) {
                    Text(a.days > 0 ? Fmt.kcal(a.kcal) : "–").font(.number(.caption2, sel ? .bold : .semibold)).lineLimit(1).minimumScaleFactor(0.8)
                        .foregroundStyle(Theme.soft).frame(height: 16)
                    FoodRings(kcal: a.kcal, protein: a.protein, carbs: a.carbs, fat: a.fat, goals: data.goals, style: .mini)
                        .frame(width: 32, height: 32)
                        .opacity(a.days == 0 ? 0.4 : 1)
                    if data.showHealth {
                        let h = a.health ?? HealthSync.Activity()
                        HealthRings(h, style: .mini).frame(width: 32, height: 32)
                    }
                }
                .frame(width: weekWidth)
                .padding(.vertical, 4)
                .background(RoundedRectangle(cornerRadius: 12, style: .continuous).fill(sel ? Theme.raised : Theme.page.opacity(0.8)))
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Week of \(Date.fromYMD(first)?.formatted(.dateTime.day().month(.wide)) ?? first): \(a.spoken(data.goals))")
            .accessibilityAddTraits(sel ? .isSelected : [])
        }
    }
}
