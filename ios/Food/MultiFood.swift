import SwiftUI

/// "Two eggs, toast, a latte": several foods in one line become separate entries. The line is split where it's clearly a
/// list (commas, semicolons, new lines, " + "), each part is worked out on its own, and they're checked together before
/// adding. "And" is left alone, so "mac and cheese" stays one food.
enum MultiFood {
    /// The separate foods, as written; one part when it isn't a list.
    static func split(_ text: String) -> [String] {
        // a comma between digits is a decimal ("1,5 kg"), not a list
        text.replacingOccurrences(of: "(?<=\\D),|,(?=\\D)|;|\\n| \\+ ", with: "\u{1F}", options: .regularExpression)
            .split(separator: "\u{1F}")
            .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }.filter { !$0.isEmpty }.prefix(8).map { $0 }
    }

    /// Each part worked out on its own (at the same time).
    static func estimates(_ parts: [String]) async throws -> [Estimate] {
        try await withThrowingTaskGroup(of: (Int, Estimate).self) { g in
            for (i, p) in parts.enumerated() { g.addTask { (i, try await FoodAPI.estimate(["kind": "text", "text": p])) } }
            var out: [(Int, Estimate)] = []
            for try await x in g { out.append(x) }
            return out.sorted { $0.0 < $1.0 }.map(\.1)
        }
    }
}

final class DraftsBox: Hashable {
    var drafts: [Draft]
    init(_ d: [Draft]) { drafts = d }
    static func == (a: DraftsBox, b: DraftsBox) -> Bool { a === b }
    func hash(into h: inout Hasher) { h.combine(ObjectIdentifier(self)) }
}

/// Several foods to check at once: each can be left out or given another amount, then they go in together.
struct MultiDraftView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.closeAdd) private var close
    var request: AddRequest
    @State var drafts: [Draft]
    @State private var keep: [Bool]
    @State private var meal: Meal
    @State private var busy = false

    init(drafts: [Draft], request: AddRequest) {
        _drafts = State(initialValue: drafts)
        _keep = State(initialValue: drafts.map { _ in true })
        _meal = State(initialValue: drafts.first?.meal ?? request.defaultMeal)
        self.request = request
    }

    private var chosen: [Int] { keep.indices.filter { keep[$0] } }
    private var total: Double { chosen.reduce(0) { $0 + drafts[$1].totals.kcal } }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                Text("\(drafts.count) things").heading(.title2).foregroundStyle(Theme.text)
                VStack(spacing: 0) {
                    ForEach(drafts.indices, id: \.self) { i in
                        row(i)
                        if i < drafts.count - 1 { Divider().padding(.leading, 16) }
                    }
                }
                .background(RoundedRectangle(cornerRadius: Theme.radius, style: .continuous).fill(Theme.panel))
                Picked(title: "Meal") { BigSegments(label: "Meal", options: Meal.allCases.map { ($0, $0.single) }, selection: $meal) }
                Text("Estimates. Change any of them after, like any food.").font(.footnote).foregroundStyle(Theme.muted)
            }
            .padding(16)
        }
        .safeAreaInset(edge: .bottom) {
            StepButton(title: busy ? "Adding…" : "Add \(chosen.count) to \(DayName.meal(meal, on: request.date)) · \(Fmt.kcal(total)) kcal", icon: "checkmark", busy: busy) {
                Task { await add() }
            }
            .disabled(chosen.isEmpty)
        }
        .background(PatternedPage())
        .navigationTitle("Check them").navigationBarTitleDisplayMode(.inline)
    }

    private func row(_ i: Int) -> some View {
        let d = drafts[i]
        return HStack(alignment: .center, spacing: 12) {
            Button { keep[i].toggle() } label: {
                Image(systemName: keep[i] ? "checkmark.circle.fill" : "circle").font(.title2).foregroundStyle(keep[i] ? Theme.accent : Theme.muted)
                    .frame(width: 44, height: 44).contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityLabel(keep[i] ? "Leave out \(d.name)" : "Keep \(d.name)")
            VStack(alignment: .leading, spacing: 2) {
                Text(d.name).font(.body.weight(.semibold)).foregroundStyle(keep[i] ? Theme.text : Theme.muted).strikethrough(!keep[i])
                if let n = d.note, !n.isEmpty { Text(n).font(.subheadline).foregroundStyle(Theme.muted).lineLimit(2) }
                if keep[i] {
                    HStack(spacing: 6) {
                        ForEach([0.5, 1, 1.5, 2], id: \.self) { p in
                            Button { drafts[i].portions = p } label: {
                                Text(Fmt.portions(p)).font(.subheadline.weight(.semibold)).foregroundStyle(drafts[i].portions == p ? Theme.selectInk : Theme.soft)
                                    .frame(minWidth: 44, minHeight: 44).background(Capsule().fill(drafts[i].portions == p ? Theme.select : Theme.raised))
                                    .contentShape(Capsule())
                            }
                            .buttonStyle(.plain)
                            .accessibilityLabel("\(Fmt.portions(p)) of \(d.name)")
                            .accessibilityAddTraits(drafts[i].portions == p ? .isSelected : [])
                        }
                    }
                    .padding(.top, 4)
                }
            }
            Spacer(minLength: 8)
            Text(Fmt.kcal(d.totals.kcal)).font(.number(.body)).foregroundStyle(keep[i] ? Theme.text : Theme.muted).fixedSize()
        }
        .padding(.horizontal, 12).padding(.vertical, 10)
    }

    private func add() async {
        busy = true; defer { busy = false }
        var added = 0
        for i in chosen {
            var d = drafts[i]; d.meal = meal
            if await model.log(d, date: request.date, quiet: true) { added += 1 }
        }
        if added > 0 {
            model.toast = Toast(text: "Added \(added) thing\(added == 1 ? "" : "s") to \(DayName.meal(meal, on: request.date))")
            close()
        }
    }
}
