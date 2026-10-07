import SwiftUI

/// A short olivewood pill near the bottom: what just happened, with Undo when it can be undone. Every screen and sheet hosts
/// one (`toastHost`) and only the top one draws it, so a message is never hidden under a sheet. It sits above the bottom
/// buttons and clear of the toolbar, where a stray tap would Undo. The model times it, announces it and gives the haptic.
struct ToastView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.dynamicTypeSize) private var typeSize
    var host: UUID
    var bottom: CGFloat
    var edge: VerticalEdge = .bottom

    var body: some View {
        ZStack {
            if let t = model.toast, model.toastHosts.last == host {
                (typeSize.isAccessibilitySize ? AnyLayout(VStackLayout(alignment: .leading, spacing: 8)) : AnyLayout(HStackLayout(spacing: 12))) {
                    Image(systemName: t.error ? "exclamationmark.circle.fill" : "checkmark.circle.fill")
                        .foregroundStyle(t.error ? Theme.fillInk : Theme.select).accessibilityHidden(true)
                    Text(t.text).font(.body.weight(.semibold)).foregroundStyle(Theme.fillInk).fixedSize(horizontal: false, vertical: true)
                    if t.undo != nil {
                        Button { model.undoToast() } label: {
                            Text("Undo").font(.body.weight(.semibold)).foregroundStyle(Theme.fillInk)
                                .padding(.horizontal, 14).frame(minWidth: 44, minHeight: 44)
                                .background(Capsule().fill(Theme.fillInk.opacity(0.16)))
                                .contentShape(Capsule())
                        }
                        .buttonStyle(.plain)
                    }
                }
                .padding(.leading, 18).padding(.trailing, t.undo == nil ? 18 : 6).padding(.vertical, 8)
                .background(RoundedRectangle(cornerRadius: 30, style: .continuous).fill(t.error ? Theme.over : Theme.fill))
                .shadow(color: .black.opacity(0.22), radius: 14, y: 6)
                .padding(edge == .bottom ? .bottom : .top, edge == .bottom ? bottom : 8).padding(.horizontal, 16)
                .transition(reduceMotion ? .opacity : .move(edge: edge == .bottom ? .bottom : .top).combined(with: .opacity))
                .accessibilityElement(children: .contain)
            }
        }
        .animation(reduceMotion ? nil : .snappy(duration: 0.25), value: model.toast)
    }
}

/// Registers a screen as able to show messages while it's on top.
private struct ToastHost: ViewModifier {
    @Environment(AppModel.self) private var model
    @State private var id = UUID()
    var bottom: CGFloat
    var edge: VerticalEdge

    func body(content: Content) -> some View {
        content
            .overlay(alignment: edge == .bottom ? .bottom : .top) { ToastView(host: id, bottom: bottom, edge: edge) }
            .onAppear { model.toastHosts.removeAll { $0 == id }; model.toastHosts.append(id) }
            .onDisappear { model.toastHosts.removeAll { $0 == id } }
    }
}

extension View {
    /// Shows the app's messages over this screen, above its bottom buttons.
    /// `edge: .top` for screens whose own buttons sit low (Ask's cards), so the message never lands on one of them.
    func toastHost(bottom: CGFloat = 84, edge: VerticalEdge = .bottom) -> some View { modifier(ToastHost(bottom: bottom, edge: edge)) }
}
