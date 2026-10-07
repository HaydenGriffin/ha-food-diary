import SwiftUI

/// What Home Assistant worked out, to check before it's added: the amount (grams for a label or barcode, portions otherwise),
/// which meal, and their own numbers if they know better. The button says exactly where it's going.
struct DraftView: View {
    @Environment(AppModel.self) private var model
    @State var draft: Draft
    var date: String
    var finished: () -> Void
    @State private var busy = false
    @State private var editing = false
    @FocusState private var focus: Bool

    init(draft: Draft, date: String, finished: @escaping () -> Void) {
        _draft = State(initialValue: draft)
        self.date = date
        self.finished = finished
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                HStack(alignment: .top, spacing: 14) {
                    if let img = draft.image {
                        Image(uiImage: img).resizable().scaledToFill().frame(width: 84, height: 84).clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
                            .accessibilityLabel("Your photo")
                    }
                    VStack(alignment: .leading, spacing: 6) {
                        HStack(alignment: .firstTextBaseline, spacing: 8) {
                            TextField("Name", text: $draft.name, axis: .vertical).font(.heading(.title2)).foregroundStyle(Theme.text)
                                .accessibilityHint("Edit the name")
                            Image(systemName: "pencil").font(.footnote.weight(.semibold)).foregroundStyle(Theme.muted).accessibilityHidden(true)
                        }
                        if let n = subline { Text(n).font(.body).foregroundStyle(Theme.muted) }
                    }
                }
                NumbersCard(values: draft.totals)
                if draft.isLabel {
                    GramsField(grams: Binding(get: { draft.grams ?? 0 }, set: { draft.grams = $0; draft.guessed = false }), unit: draft.unit, focus: $focus)
                    if draft.guessed {
                        Label(draft.servingG != nil ? "I couldn't tell how much, so this is one serving. Change it if not." : "I couldn't tell how much. Check the amount.",
                              systemImage: "exclamationmark.circle").font(.body).foregroundStyle(Theme.warn)
                    }
                } else {
                    Picked(title: "How much") { BigSegments(label: "Portions", options: Portions.options, selection: $draft.portions) }
                }
                Picked(title: "Meal") { BigSegments(label: "Meal", options: Meal.allCases.map { ($0, $0.single) }, selection: $draft.meal) }
                if editing {
                    OwnNumbers(start: draft.totals, focus: $focus) { v in
                        draft.perPortion = draft.isLabel ? v : v.scaled(1 / max(draft.portions, 0.05))
                        draft.edited = true; editing = false
                    }
                } else {
                    Button { editing = true } label: { Label("Change numbers", systemImage: "pencil").font(.body.weight(.semibold)).frame(minHeight: 48).contentShape(Rectangle()) }
                        .buttonStyle(.plain).foregroundStyle(Theme.accent)
                }
                Text(footnote).font(.footnote).foregroundStyle(Theme.muted)
            }
            .padding(16)
        }
        .scrollDismissesKeyboard(.interactively)
        .safeAreaInset(edge: .bottom) {
            StepButton(title: busy ? "Adding…" : "Add to \(DayName.meal(draft.meal, on: date))", icon: "checkmark", busy: busy) {
                Task { busy = true; if await model.log(draft, date: date) { finished() }; busy = false }
            }
        }
        .keyboardDone($focus)
        .background(PatternedPage())
        .navigationTitle("Check it").navigationBarTitleDisplayMode(.inline)
    }

    private var subline: String? {
        if let p = draft.per100, draft.isLabel { return "Label: \(Fmt.kcal(p.kcal)) kcal per 100 \(draft.unit)" }
        guard let n = draft.note, !n.isEmpty else { return nil }
        switch draft.portions {  // what the photo showed, scaled by the portion they picked
        case 1: return n
        case 0.5: return "Half of \(n)"
        default: return "\(Fmt.portions(draft.portions)) × \(n)"
        }
    }
    private var footnote: String {
        if draft.edited { return "Your numbers." }
        switch draft.source {
        case "barcode": return "From the food database (or a label you photographed before)."
        case "label": return "From the label."
        case "again": return "As you had it before."
        default: return "An estimate. Change the portions or the numbers if it's off."
        }
    }
}

/// How much, in grams (or ml): − and + either side of the number, or type it.
struct GramsField: View {
    @Binding var grams: Double
    var unit: String
    var focus: FocusState<Bool>.Binding
    @State private var text = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("How much").font(.subheadline.weight(.semibold)).foregroundStyle(Theme.muted).accessibilityHidden(true)
            HStack(spacing: 14) {
                Button { grams = max(1, grams - step) } label: { stepper("minus") }
                    .accessibilityLabel("Less, \(Int(step)) \(unit)")
                HStack(alignment: .firstTextBaseline, spacing: 4) {
                    TextField("0", text: $text).keyboardType(.decimalPad).multilineTextAlignment(.trailing).font(.number(.title2)).fixedSize()
                        .focused(focus)
                        .onChange(of: text) { _, t in if let v = Fmt.parse(t), v > 0 { grams = v } }
                        .accessibilityLabel("Amount in \(unit == "ml" ? "millilitres" : "grams")")
                    Text(unit).font(.title3).foregroundStyle(Theme.muted)
                }
                .frame(maxWidth: .infinity)
                Button { grams += step } label: { stepper("plus") }
                    .accessibilityLabel("More, \(Int(step)) \(unit)")
            }
            .padding(6).background(Capsule().fill(Theme.raised))
        }
        .buttonStyle(.plain).foregroundStyle(Theme.text)
        .onAppear { text = Self.show(grams) }
        .onChange(of: grams) { _, g in if Fmt.parse(text) != g { text = Self.show(g) } }
    }

    private func stepper(_ icon: String) -> some View {
        Image(systemName: icon).font(.title3.weight(.semibold)).foregroundStyle(Theme.fillInk)
            .frame(width: 48, height: 48).background(Circle().fill(Theme.fill))
    }
    private var step: Double { grams >= 100 ? 10 : 5 }
    private static func show(_ g: Double) -> String { g == g.rounded() ? "\(Int(g))" : g.formatted(.number.precision(.fractionLength(0...1))) }
}

/// Their own numbers for what was eaten.
struct OwnNumbers: View {
    var start: Nutrients
    var focus: FocusState<Bool>.Binding
    var save: (Nutrients) -> Void
    @State private var kcal = ""
    @State private var protein = ""
    @State private var carbs = ""
    @State private var fat = ""

    private var calories: Double? { Fmt.parse(kcal).flatMap { $0 > 0 ? $0 : nil } }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Your numbers").heading(.headline).foregroundStyle(Theme.text)
            Grid(horizontalSpacing: 12, verticalSpacing: 12) {
                GridRow { field("Calories", "kcal", $kcal); field("Protein", "g", $protein) }
                GridRow { field("Carbs", "g", $carbs); field("Fat", "g", $fat) }
            }
            if calories == nil { Text("Calories need a number above 0.").font(.footnote).foregroundStyle(Theme.alert) }
            BigButton(title: "Use these", icon: "checkmark") {
                guard let c = calories else { return }
                let n = { (s: String) in Fmt.parse(s) ?? 0 }
                save(Nutrients(kcal: c, protein_g: n(protein), carbs_g: n(carbs), fat_g: n(fat), fibre_g: 0))
            }
            .disabled(calories == nil).opacity(calories == nil ? 0.5 : 1)
        }
        .padding(16).background(RoundedRectangle(cornerRadius: Theme.radius, style: .continuous).fill(Theme.panel))
        .onAppear {
            let r = { (v: Double) in "\(Int(v.rounded()))" }
            kcal = r(start.kcal); protein = r(start.protein_g); carbs = r(start.carbs_g); fat = r(start.fat_g)
        }
    }

    private func field(_ name: String, _ unit: String, _ b: Binding<String>) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(name).font(.subheadline).foregroundStyle(Theme.muted).accessibilityHidden(true)
            HStack {
                TextField("0", text: b).keyboardType(.decimalPad).font(.number(.title2)).focused(focus).accessibilityLabel("\(name) in \(unit)")
                Text(unit).foregroundStyle(Theme.muted)
            }
            .padding(.horizontal, 14).frame(minHeight: 52).background(RoundedRectangle(cornerRadius: 14).fill(Theme.raised))
        }
    }
}
