import SwiftUI

/// In an empty meal: their usuals, drawn as suggestions (a heading that says so, a dashed outline, quieter text)
/// so they never read as food they have already had.
struct UsualSuggestions: View {
    var usuals: [Usual]
    var meal: Meal
    var again: (from: String, to: String, entries: [Entry])? = nil   // the day before's same meal, one tap

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Label("Suggestions · not added yet", systemImage: "lightbulb")
                .font(.footnote.weight(.semibold)).foregroundStyle(Theme.muted)
                .accessibilityAddTraits(.isHeader)
            if let a = again { AgainMealRow(from: a.from, to: a.to, meal: meal, entries: a.entries) }
            ForEach(usuals) { u in UsualRow(usual: u, meal: meal) }
        }
        .padding(.vertical, 6)
    }
}

/// "Same as yesterday": the whole of that meal from the day before, in one tap (Undo puts it back).
struct AgainMealRow: View {
    @Environment(AppModel.self) private var model
    var from: String
    var to: String
    var meal: Meal
    var entries: [Entry]
    @State private var busy = false

    var body: some View {
        let kcal = entries.reduce(0) { $0 + $1.totals.kcal }
        Button { Task { busy = true; await model.copyMeal(from: from, meal: meal, to: to); busy = false } } label: {
            HStack(spacing: 12) {
                Image(systemName: "arrow.uturn.backward.circle").font(.title2).foregroundStyle(Theme.accent).frame(width: 44, height: 44)
                VStack(alignment: .leading, spacing: 2) {
                    Text("Same as \(DayName.of(from))").font(.body.weight(.semibold)).foregroundStyle(Theme.soft)
                    Text("\(Fmt.kcal(kcal)) kcal · \(entries.map(\.name).joined(separator: ", "))").font(.subheadline.monospacedDigit()).foregroundStyle(Theme.muted).lineLimit(1)
                }
                Spacer(minLength: 8)
                if busy { ProgressView().frame(width: 30) } else { Image(systemName: "plus.circle.fill").font(.title2).foregroundStyle(Theme.accent) }
            }
            .padding(.horizontal, 12).padding(.vertical, 8).frame(minHeight: 56)
            .background(RoundedRectangle(cornerRadius: 16, style: .continuous)
                .strokeBorder(Theme.muted.opacity(0.55), style: StrokeStyle(lineWidth: 1.5, dash: [5, 4])))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain).disabled(busy)
        .accessibilityLabel("Add the same \(meal.single.lowercased()) as \(DayName.of(from)): \(entries.map(\.name).joined(separator: ", ")), \(Fmt.kcal(kcal)) calories")
        .accessibilityHint("Suggestion, not added yet")
    }
}

/// One suggestion: one tap adds it to this meal on the day on screen.
struct UsualRow: View {
    @Environment(AppModel.self) private var model
    var usual: Usual
    var meal: Meal
    @State private var busy = false

    var body: some View {
        Button { Task { await add() } } label: {
            HStack(spacing: 12) {
                FoodImage(path: usual.image, name: usual.name).frame(width: 44, height: 44).opacity(0.8)
                VStack(alignment: .leading, spacing: 2) {
                    Text(usual.name).font(.body.weight(.semibold)).foregroundStyle(Theme.soft).multilineTextAlignment(.leading).lineLimit(2)
                    Text("\(Fmt.kcal(usual.kcal)) kcal · \(usual.detail)").font(.subheadline.monospacedDigit()).foregroundStyle(Theme.muted).lineLimit(1)
                }
                Spacer(minLength: 8)
                if busy { ProgressView().frame(width: 30) } else {
                    Image(systemName: "plus.circle.fill").font(.title2).foregroundStyle(Theme.accent)
                }
            }
            .padding(.horizontal, 12).padding(.vertical, 8).frame(minHeight: 56)
            .background(RoundedRectangle(cornerRadius: 16, style: .continuous)
                .strokeBorder(Theme.muted.opacity(0.55), style: StrokeStyle(lineWidth: 1.5, dash: [5, 4])))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain).disabled(busy)
        .accessibilityLabel("Add \(usual.name), \(Fmt.kcal(usual.kcal)) calories, to \(meal.single.lowercased())")
        .accessibilityHint("Suggestion, not added yet")
    }

    private func add() async {
        busy = true; defer { busy = false }
        switch usual {
        case .saved(let s): await model.logSaved(s, meal: meal)
        case .food(let f): await model.again(f, meal: meal)
        }
    }
}

/// Under a meal of two or more foods: keep it as one thing to add next time.
struct SaveMealButton: View {
    @Environment(AppModel.self) private var model
    var meal: Meal
    @State private var naming = false
    @State private var name = ""

    /// Named after what's in it ("Porridge + Skyr"), so it's recognisable in Add food without their having to think of a name.
    private var suggestedName: String {
        let names = (model.day?.entries(meal) ?? []).map(\.name)
        let joined = names.joined(separator: " + ")
        return joined.isEmpty || joined.count > 40 ? (names.first.map { "\($0) and more" } ?? "My \(meal.single.lowercased())") : joined
    }

    var body: some View {
        Button { name = suggestedName; naming = true } label: {
            Label("Save these together", systemImage: "bookmark")
                .font(.body.weight(.semibold)).foregroundStyle(Theme.accent).frame(maxWidth: .infinity, minHeight: 44, alignment: .leading)
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .alert("Save these together", isPresented: $naming) {
            TextField("Name", text: $name)
            Button("Save") { Task { await model.saveMeal(name, meal: meal) } }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("Next time it's one tap, here and in Add food.")
        }
    }
}
