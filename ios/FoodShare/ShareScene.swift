import SwiftUI

/// A full-page share-sheet notice: patterned parchment and one round mark in the middle (an olive sprig, a turning ring
/// while it works, a tick when done), with a line or two of words.
struct ShareScene<Actions: View>: View {
    let share: ShareModel
    var mark: ShareMark.Look
    var title: String
    var detail: String
    @ViewBuilder var actions: Actions

    var body: some View {
        VStack(spacing: 0) {
            HStack {
                if mark == .working {
                    Button("Cancel") { share.cancel() }.font(.body.weight(.semibold)).foregroundStyle(Theme.accent)
                        .frame(minWidth: 44, minHeight: 44)
                }
                Spacer()
            }
            .frame(height: 44)
            .padding(.horizontal, 20).padding(.top, 8)
            Spacer()
            VStack(spacing: 24) {
                ShareMark(look: mark)
                VStack(spacing: 8) {
                    Text(title).font(.heading(.title)).foregroundStyle(Theme.text)
                    Text(detail).font(.title3).foregroundStyle(Theme.muted)
                }
                .multilineTextAlignment(.center)
                .fixedSize(horizontal: false, vertical: true)
                .transaction { $0.animation = nil }  // the words change at once; only the mark animates
                .accessibilityElement(children: .combine)
            }
            .padding(.horizontal, 32).padding(.bottom, 56)  // sits a little above the middle, where the eye expects it
            Spacer()
            actions.padding(.horizontal, 20).padding(.bottom, 16)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(PatternedPage())
    }
}

/// The round mark: the sprig with a turning ring while it works, a tick once done, ! when it failed.
struct ShareMark: View {
    enum Look { case idle, working, done, problem }
    var look: Look
    @State private var turn = false
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        ZStack {
            Circle().fill(look == .done ? Theme.ring : look == .problem ? Theme.overSoft : Theme.accentSoft)
            switch look {
            case .idle, .working:
                OliveSprig(olives: 2).fill(Theme.accent).frame(width: 40, height: 60)
                if look == .working && !reduceMotion {
                    Circle().trim(from: 0, to: 0.24).stroke(Theme.ring, style: StrokeStyle(lineWidth: 4, lineCap: .round))
                        .padding(2)
                        .rotationEffect(.degrees(turn ? 360 : 0))
                        .animation(.linear(duration: 1).repeatForever(autoreverses: false), value: turn)
                        .onAppear { turn = true }
                }
            case .done:
                Image(systemName: "checkmark").font(.system(size: 46, weight: .bold)).foregroundStyle(Theme.fillInk)
                    .transition(.scale(scale: 0.3).combined(with: .opacity))
            case .problem:
                Image(systemName: "exclamationmark").font(.system(size: 46, weight: .bold)).foregroundStyle(Theme.over)
            }
        }
        .frame(width: 116, height: 116)
        .accessibilityHidden(true)
    }
}

/// A full-width pill: the main choice in olivewood, the other on sand.
struct ScenePill: View {
    var title: String
    var main = false
    var action: () -> Void

    var body: some View {
        Button(action: action) {
            Text(title).font(.title3.weight(.semibold)).foregroundStyle(main ? Theme.fillInk : Theme.text)
                .frame(maxWidth: .infinity, minHeight: 60)
                .background(Capsule().fill(main ? Theme.fill : Theme.raised))
                .contentShape(Capsule())
        }
        .buttonStyle(.plain)
    }
}

/// Signed out, or nothing usable: say so, with a way out.
struct NoticeScene: View {
    let share: ShareModel
    var title: String
    var detail: String

    var body: some View {
        ShareScene(share: share, mark: .idle, title: title, detail: detail) {
            ScenePill(title: "Close", main: true) { share.cancel() }
        }
    }
}
