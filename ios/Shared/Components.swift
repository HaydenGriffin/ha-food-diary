import SwiftUI

/// The same portion choices wherever they pick one.
enum Portions {
    static let options: [(Double, String)] = [0.5, 1, 1.5, 2, 3].map { ($0, Fmt.portions($0)) }
}

/// A small label over a choice, so it's clear what the pills are choosing.
struct Picked<Content: View>: View {
    var title: String
    @ViewBuilder var content: Content
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(title).font(.subheadline.weight(.semibold)).foregroundStyle(Theme.muted).accessibilityHidden(true)
            content
        }
    }
}

/// The calories, big, with the three macros beside them (beneath them at the largest text sizes).
struct NumbersCard: View {
    var values: Nutrients
    @Environment(\.dynamicTypeSize) private var typeSize

    var body: some View {
        let stacked = typeSize.isAccessibilitySize
        (stacked ? AnyLayout(VStackLayout(alignment: .leading, spacing: 14)) : AnyLayout(HStackLayout(alignment: .firstTextBaseline, spacing: 18))) {
            VStack(alignment: .leading, spacing: 0) {
                Text(Fmt.kcal(values.kcal)).heroNumber().foregroundStyle(Theme.text).contentTransition(.numericText()).fixedSize()
                Text("kcal").font(.subheadline.weight(.semibold)).foregroundStyle(Theme.muted)
            }
            if !stacked { Spacer(minLength: 8) }
            HStack(alignment: .firstTextBaseline, spacing: 18) {
                ForEach([("Protein", values.protein_g), ("Carbs", values.carbs_g), ("Fat", values.fat_g)], id: \.0) { n, v in
                    VStack(alignment: stacked ? .leading : .center, spacing: 2) {
                        Text(Fmt.g(v)).font(.number(.title3)).foregroundStyle(Theme.text).fixedSize()
                        Text(n).font(.subheadline).foregroundStyle(Theme.muted).fixedSize()
                    }
                }
            }
        }
        .animation(.snappy, value: values.kcal)
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(18).background(RoundedRectangle(cornerRadius: Theme.radius, style: .continuous).fill(Theme.panel))
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("\(Fmt.kcal(values.kcal)) calories, \(Int(values.protein_g.rounded())) grams protein, \(Int(values.carbs_g.rounded())) carbs, \(Int(values.fat_g.rounded())) fat")
    }
}
