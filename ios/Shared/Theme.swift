import SwiftUI

/// Olive Garden: parchment paper with a faint olive-sprig pattern; Olivewood for words and the main buttons, Olive for what
/// they've chosen, Sage for what they've eaten, Sand for quiet grounds, and an earthy clay only when they're over.
/// Palette: Parchment #F1EAD8 · Sand #D5C7AD · Olive #BEC5A4 · Sage #8A8E75 · Bark #68604D · Olivewood #2D2F22.
/// Contrast is kept high: words ≥4.5:1 on their ground, bars ≥3:1 on their track. The food rings' numbers are always in
/// words beside them too, so the rings can stay soft.
enum Theme {
    /// A colour for light and dark, with optional stronger versions when they have Increase Contrast on.
    static func dyn(_ light: UInt32, _ dark: UInt32, high: (UInt32, UInt32)? = nil) -> Color {
        Color(UIColor { t in
            let strong = t.accessibilityContrast == .high
            let pair = strong ? (high ?? (light, dark)) : (light, dark)
            return UIColor(hex: t.userInterfaceStyle == .dark ? pair.1 : pair.0)
        })
    }
    static let page = dyn(0xF1EAD8, 0x202219)       // parchment / deep olivewood
    static let panel = dyn(0xFBF8F0, 0x2D2F22)      // paper cards
    static let raised = dyn(0xEAE2CE, 0x393C2D)     // quiet controls (sand, lighter)
    static let raised2 = dyn(0xDDD2B9, 0x474A3A)    // tracks, the stronger step
    static let text = dyn(0x2D2F22, 0xF1EAD8)       // olivewood / parchment
    static let soft = dyn(0x4A4637, 0xDDD2B9, high: (0x2D2F22, 0xF1EAD8))
    static let muted = dyn(0x68604D, 0xB9B19C, high: (0x4A4637, 0xDDD2B9))      // bark
    static let accent = dyn(0x55603F, 0xC9D0AE, high: (0x3B4429, 0xE2E6CF))     // words and icons that act (deep sage / olive)
    static let fill = dyn(0x2D2F22, 0xBEC5A4)       // the main buttons: olivewood / olive
    static let fillInk = dyn(0xF6F0E1, 0x2D2F22)
    static let select = dyn(0xBEC5A4, 0x5D6350)     // their choice in a set (olive)
    static let selectInk = dyn(0x2D2F22, 0xF6F0E1)
    static let accentSoft = dyn(0xE2E5D2, 0x3A3F2E)
    static let ring = dyn(0x6A7152, 0xBEC5A4)       // eaten: sage, deep enough to read on its sand track
    static let over = dyn(0xA4553A, 0xE0A182)       // clay
    static let overSoft = dyn(0xF1DDD2, 0x4A2E22)
    static let pattern = dyn(0x8A8E75, 0xBEC5A4)    // the sprigs, drawn very faint
    static let ok = dyn(0x3F6A3A, 0xA8D19A)
    static let warn = dyn(0x93501C, 0xE9A86F)
    static let alert = dyn(0xA33A2C, 0xF09A86)

    static let radius: CGFloat = 22
    /// Within 5% of a goal is on target: the ring and bars only turn clay beyond it (the week's chart uses the same band).
    static let overBand = 1.05
}

extension UIColor {
    convenience init(hex: UInt32) {
        self.init(red: CGFloat((hex >> 16) & 0xFF) / 255, green: CGFloat((hex >> 8) & 0xFF) / 255, blue: CGFloat(hex & 0xFF) / 255, alpha: 1)
    }
}

extension Font {
    /// Numbers in the rounded face, tabular, at a text style's size, so they grow with their text-size setting.
    static func number(_ style: Font.TextStyle, _ weight: Font.Weight = .semibold) -> Font { .system(style).weight(weight).monospacedDigit() }
    /// Headings in New York: section titles, sheet titles, the day.
    static func heading(_ style: Font.TextStyle = .title3) -> Font { .system(style, design: .serif).weight(.semibold) }
}

/// The one hero number on a screen: larger than Large Title, in New York, and it follows their text size as they change it.
struct HeroNumber: ViewModifier {
    @ScaledMetric(relativeTo: .largeTitle) private var size: CGFloat = 48
    func body(content: Content) -> some View { content.font(.system(size: size, weight: .semibold, design: .serif).monospacedDigit()) }
}

extension View {
    func heroNumber() -> some View { modifier(HeroNumber()) }
    /// A heading: New York, and announced as a heading so VoiceOver can jump between them.
    func heading(_ style: Font.TextStyle = .title3) -> some View { font(.heading(style)).accessibilityAddTraits(.isHeader) }
}

/// The calories ring: sage for eaten, the track for what's left; it fills as they log, and turns clay only once they're clearly
/// past the goal (the words say "over" from the first calorie).
/// The calorie ring alone (week strip, calendar, widget): the outer food ring, without its glyph at small sizes.
struct CalorieRing: View {
    var eaten: Double
    var goal: Double
    var lineWidth: CGFloat = 14
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        let p = goal > 0 ? min(eaten / goal, 1) : 0
        let isOver = goal > 0 && eaten > goal * Theme.overBand
        GradientRing(progress: p, colors: isOver ? FoodRingColor.over : FoodRingColor.kcal, lineWidth: lineWidth, lap: false)
            .padding(-lineWidth / 2)
            .animation(reduceMotion ? nil : .easeOut(duration: 0.7), value: p)
            .accessibilityHidden(true)
    }
}

/// A choice of a few things in big, easy-to-hit pills (Apple's segmented control is too small to read comfortably). The
/// chosen one is filled olive and outlined, so it reads without relying on colour; with very large text the pills
/// stack instead of squeezing their words. `label` names the choice for VoiceOver ("Meal", "Portions").
struct BigSegments<T: Hashable>: View {
    var label: String
    var options: [(T, String)]
    @Binding var selection: T
    @Environment(\.dynamicTypeSize) private var typeSize

    var body: some View {
        let stacked = typeSize.isAccessibilitySize
        let layout = stacked ? AnyLayout(VStackLayout(spacing: 4)) : AnyLayout(HStackLayout(spacing: 4))
        layout {
            ForEach(options.indices, id: \.self) { i in
                let (value, title) = options[i]
                let sel = value == selection
                Button { selection = value } label: {
                    Text(title).font(.body.weight(.semibold)).lineLimit(stacked ? nil : 1).minimumScaleFactor(stacked ? 1 : 0.85)
                        .padding(.horizontal, 6)
                        .foregroundStyle(sel ? Theme.selectInk : Theme.soft).frame(maxWidth: .infinity, minHeight: 52)
                        .padding(.horizontal, stacked ? 16 : 0)
                        .background(Capsule().fill(sel ? Theme.select : .clear))
                        .overlay(Capsule().strokeBorder(sel ? Theme.ring : .clear, lineWidth: 2))
                        .contentShape(Capsule())
                }
                .buttonStyle(.plain)
                .accessibilityLabel(title)
                .accessibilityAddTraits(sel ? .isSelected : [])
            }
        }
        .padding(4).background(RoundedRectangle(cornerRadius: stacked ? 30 : 60, style: .continuous).fill(Theme.raised))
        .accessibilityElement(children: .contain)
        .accessibilityLabel(label)
        .sensoryFeedback(.selection, trigger: selection)
    }
}
