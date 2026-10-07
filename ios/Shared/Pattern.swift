import SwiftUI

/// One olive sprig: a curved stem, slim paired leaves, a couple of olives. Drawn, not an image, so it stays crisp and tints.
struct OliveSprig: Shape {
    var olives = 2

    func path(in r: CGRect) -> Path {
        var p = Path()
        let w = r.width, h = r.height
        let start = CGPoint(x: r.minX + w * 0.5, y: r.maxY)
        let end = CGPoint(x: r.minX + w * 0.56, y: r.minY + h * 0.04)
        let ctrl = CGPoint(x: r.minX + w * 0.30, y: r.minY + h * 0.5)
        // the stem, with a little width
        var stem = Path()
        stem.move(to: start)
        stem.addQuadCurve(to: end, control: ctrl)
        p.addPath(stem.strokedPath(StrokeStyle(lineWidth: max(1, w * 0.035), lineCap: .round)))
        // leaves in pairs along the stem, alternating sides, smaller towards the tip
        func point(_ t: CGFloat) -> CGPoint {
            let a = (1 - t) * (1 - t), b = 2 * (1 - t) * t, c = t * t
            return CGPoint(x: a * start.x + b * ctrl.x + c * end.x, y: a * start.y + b * ctrl.y + c * end.y)
        }
        for (i, t) in [0.2, 0.36, 0.52, 0.68, 0.84].enumerated() {
            let at = point(t)
            let len = h * (0.30 - 0.03 * CGFloat(i)), wid = len * 0.30
            let side: CGFloat = i % 2 == 0 ? -1 : 1
            let leaf = Path(ellipseIn: CGRect(x: -wid / 2, y: -len, width: wid, height: len))
            let angle = Angle.degrees(Double(side) * (52 - Double(i) * 4) - 8)
            p.addPath(leaf.applying(CGAffineTransform(rotationAngle: CGFloat(angle.radians)).concatenating(CGAffineTransform(translationX: at.x, y: at.y))))
        }
        p.addPath(Path(ellipseIn: CGRect(x: end.x - w * 0.07, y: end.y - h * 0.02, width: w * 0.14, height: h * 0.12)))  // the tip bud
        for k in 0..<olives {
            let at = point(k == 0 ? 0.44 : 0.62)
            let o = CGRect(x: at.x + (k == 0 ? w * 0.06 : -w * 0.22), y: at.y + h * 0.02, width: w * 0.16, height: w * 0.21)
            p.addPath(Path(ellipseIn: o))
        }
        return p
    }
}

/// Parchment with a small olive sprig repeated in a half-drop, like printed wallpaper: evenly spaced, alternately leaning,
/// tone on tone and very faint, so it reads as texture behind the cards and never competes with the words.
struct PatternedPage: View {
    @Environment(\.colorScheme) private var scheme
    @Environment(\.colorSchemeContrast) private var contrast

    var body: some View {
        ZStack {
            Theme.page
            Canvas { ctx, size in
                // light sprigs on a dark page read stronger, so they're fainter; with Increase Contrast they nearly go
                ctx.opacity = contrast == .increased ? 0.04 : scheme == .dark ? 0.05 : 0.08
                ctx.fill(SprigRepeat.cached(in: size), with: .color(Theme.pattern))
            }
            .accessibilityHidden(true)
        }
        .ignoresSafeArea()
    }
}

/// The wallpaper: one sprig per cell of a half-drop grid (every other column dropped half a cell), leaning left and right in turn.
enum SprigRepeat {
    static let cell: CGFloat = 92
    static let sprig = CGSize(width: 22, height: 36)
    static let lean = 28.0

    static func path(in size: CGSize) -> Path {
        var p = Path()
        let shape = OliveSprig().path(in: CGRect(x: -sprig.width / 2, y: -sprig.height / 2, width: sprig.width, height: sprig.height))
        let step = cell * 0.866
        var col = 0
        var x = -step / 2
        while x < size.width + step {
            var y = (col % 2 == 0 ? 0 : cell / 2) - cell / 2
            var row = 0
            while y < size.height + cell {
                let angle = ((col + row) % 2 == 0 ? lean : -lean) * .pi / 180
                p.addPath(shape.applying(CGAffineTransform(rotationAngle: angle).concatenating(CGAffineTransform(translationX: x, y: y))))
                y += cell; row += 1
            }
            x += step; col += 1
        }
        return p
    }

    private static let lock = NSLock()
    nonisolated(unsafe) private static var paths: [String: Path] = [:]

    /// Each page size is drawn once and reused by every screen and sheet.
    static func cached(in size: CGSize) -> Path {
        let key = "\(Int(size.width.rounded()))x\(Int(size.height.rounded()))"
        lock.lock(); defer { lock.unlock() }
        if let p = paths[key] { return p }
        let p = path(in: size)
        paths[key] = p
        return p
    }
}
