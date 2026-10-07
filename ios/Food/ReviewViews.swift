import Charts
import SwiftUI

/// Sunday afternoon and Monday on Today: last week in one line, and one thing that stood out.
struct ReviewCard: View {
    @Environment(AppModel.self) private var model
    var review: WeekReview

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(alignment: .top, spacing: 8) {
                Text(review.headline).heading(.title2).foregroundStyle(Theme.text).fixedSize(horizontal: false, vertical: true)
                Spacer(minLength: 0)
                Button { model.dismissReview() } label: {
                    Image(systemName: "xmark").font(.body.weight(.semibold)).foregroundStyle(Theme.muted).frame(width: 44, height: 44)
                }
                .buttonStyle(.plain).accessibilityLabel("Hide last week")
                .padding(.top, -10).padding(.trailing, -12)
            }
            Text(review.highlight ?? review.average).font(.body).foregroundStyle(Theme.soft).fixedSize(horizontal: false, vertical: true)
            Button { model.lookBack = review } label: {
                Text("See last week").font(.body.weight(.semibold)).foregroundStyle(Theme.fillInk)
                    .frame(maxWidth: .infinity, minHeight: 56).background(Capsule().fill(Theme.fill)).contentShape(Capsule())
            }
            .buttonStyle(.plain)
        }
        .padding(20)
        .background(RoundedRectangle(cornerRadius: Theme.radius, style: .continuous).fill(Theme.panel))
    }
}

/// The week in full: one headline, the days against the goal, and at most two things worth smiling about.
struct ReviewSheet: View {
    @Environment(\.dismiss) private var dismiss
    @Environment(AppModel.self) private var model
    var review: WeekReview

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 20) {
                    VStack(alignment: .leading, spacing: 6) {
                        Text(review.headline).heading(.title).foregroundStyle(Theme.text)
                        Text(review.average).font(.body).foregroundStyle(Theme.soft)
                    }
                    .fixedSize(horizontal: false, vertical: true)
                    chart
                    if !moments.isEmpty {
                        VStack(alignment: .leading, spacing: 12) {
                            ForEach(moments, id: \.0) { m in moment(m.0, m.1, m.2) }
                        }
                    }
                    if let k = model.sleepInsight, SleepInsight.due(for: review.end) {
                        moment("moon.zzz", "Sleep and food", SleepInsight.text(k))
                            .onAppear { SleepInsight.shown(for: review.end) }
                    }
                }
                .padding(16)
            }
            .background(PatternedPage())
            .navigationTitle(title).navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Close") { dismiss() } } }
            .task { await model.loadSleepInsight() }
        }
    }

    /// Only what stands out, and only good news: the day closest to the goal, then something new, a strong protein week, or a
    /// favourite kept coming back to. (The chart already shows the days over.)
    private var moments: [(String, String, String)] {
        var out: [(String, String, String)] = []
        if let b = review.best_day, let d = Date.fromYMD(b.date) {
            out.append(("target", "Closest to your goal", "\(d.formatted(.dateTime.weekday(.wide))), \(Fmt.kcal(b.kcal)) kcal"))
        }
        if let n = review.new_dishes?.first { out.append(("sparkles", "Something new", n)) }
        else if review.protein_days >= 4 { out.append(("bolt.heart", "Protein goal", "\(review.protein_days) of \(review.days_logged) days")) }
        else if let f = review.favourite, f.times >= 3 { out.append(("heart", "Your favourite", "\(f.name), \(f.times) times")) }
        return Array(out.prefix(2))
    }

    private var title: String {
        guard let s = Date.fromYMD(review.start), let e = Date.fromYMD(review.end) else { return "Your week" }
        return "\(s.formatted(.dateTime.day().month(.abbreviated))) – \(e.formatted(.dateTime.day().month(.abbreviated)))"
    }

    private var chart: some View {
        Chart {
            ForEach(review.days) { d in
                BarMark(x: .value("Day", Date.fromYMD(d.date)?.formatted(.dateTime.weekday(.abbreviated)) ?? d.date),
                        y: .value("kcal", d.logged > 0 ? d.kcal : review.goal_kcal * 0.03))  // a stub, so a day with nothing logged isn't a gap
                    .foregroundStyle(d.logged == 0 ? Theme.raised2 : d.kcal > review.goal_kcal * Theme.overBand ? Theme.over : Theme.ring)
                    .cornerRadius(6)
            }
            RuleMark(y: .value("Goal", review.goal_kcal))
                .foregroundStyle(Theme.muted).lineStyle(StrokeStyle(lineWidth: 1.5, dash: [5, 4]))
                .annotation(position: .top, alignment: .leading, spacing: 4) {
                    Text("Goal \(Fmt.kcal(review.goal_kcal))").font(.footnote.weight(.semibold)).foregroundStyle(Theme.muted)
                        .padding(.horizontal, 8).padding(.vertical, 2).background(Capsule().fill(Theme.panel))
                }
        }
        .chartYAxis(.hidden)
        .chartXAxis { AxisMarks { _ in AxisValueLabel().font(.footnote.weight(.semibold)).foregroundStyle(Theme.muted) } }
        .frame(height: 200)
        .padding(18)
        .background(RoundedRectangle(cornerRadius: Theme.radius, style: .continuous).fill(Theme.panel))
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(review.days.map { d in
            let day = Date.fromYMD(d.date)?.formatted(.dateTime.weekday(.wide)) ?? d.date
            guard d.logged > 0 else { return "\(day): nothing logged" }
            let state = d.kcal > review.goal_kcal * Theme.overBand ? ", over" : d.kcal >= review.goal_kcal * 0.75 ? ", on target" : ", under"
            return "\(day): \(Fmt.kcal(d.kcal)) calories\(state)"
        }.joined(separator: ". "))
    }

    private func moment(_ icon: String, _ name: String, _ value: String) -> some View {
        HStack(alignment: .center, spacing: 14) {
            Image(systemName: icon).font(.title3.weight(.semibold)).foregroundStyle(Theme.accent).frame(width: 44, height: 44)
                .background(Circle().fill(Theme.accentSoft)).accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 2) {
                Text(name).font(.subheadline.weight(.semibold)).foregroundStyle(Theme.muted)
                Text(value).font(.body.weight(.semibold)).foregroundStyle(Theme.text).fixedSize(horizontal: false, vertical: true)
            }
            Spacer(minLength: 0)
        }
        .padding(16)
        .background(RoundedRectangle(cornerRadius: Theme.radius, style: .continuous).fill(Theme.panel))
        .accessibilityElement(children: .combine)
    }
}
