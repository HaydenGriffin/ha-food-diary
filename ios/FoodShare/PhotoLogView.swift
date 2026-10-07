import SwiftUI

/// A shared photo: worked out, checked (name, calories, which meal), then added today, with Undo on the result.
@Observable @MainActor
final class PhotoLog {
    enum Stage { case asking, checking(Estimate), added(entry: String, date: String, text: String), removed }

    let image: UIImage
    var kind = "photo"
    var text = ""
    var meal = Meal.now
    var busy = false
    var stage = Stage.asking
    var error: String?

    init(image: UIImage) { self.image = image }

    /// Ask Home Assistant what it is (nothing is added yet).
    func workOut() async {
        busy = true; error = nil
        defer { busy = false }
        guard let jpeg = await Task.detached(operation: { [image] in image.jpegForUpload() }).value else { error = "Couldn't read that photo."; return }
        do {
            let said = text.trimmingCharacters(in: .whitespacesAndNewlines)
            let data: [String: Any] = kind == "photo" ? ["kind": "photo", "image": jpeg.base64EncodedString(), "hint": said]
                                                       : ["kind": "label", "image": jpeg.base64EncodedString(), "amount": said]
            stage = .checking(try await FoodAPI.estimate(data))
        } catch { self.error = error.localizedDescription }
    }

    /// Add it, as checked.
    func add(_ e: Estimate) async -> Bool {
        busy = true; error = nil
        defer { busy = false }
        var d: [String: Any] = ["name": e.name, "source": e.source ?? kind, "meal": meal.rawValue, "date": Date().ymd]
        if let p = e.per100, let g = e.grams { d["per_100"] = p.dict; d["grams"] = g; d["unit"] = e.unit ?? "g" } else { d.merge(e.values.dict) { _, b in b } }
        if let n = e.note, !n.isEmpty { d["note"] = n }
        if let p = e.photo { d["photo"] = p }
        do {
            let r = try await FoodAPI.log(d)
            stage = .added(entry: r.entry.id, date: r.date, text: "Added \(e.name) to \(meal.single.lowercased())")
            AccessibilityNotification.Announcement("Added \(e.name)").post()
            return true
        } catch { self.error = error.localizedDescription; return false }
    }

    func undo(entry: String, date: String) async {
        busy = true; defer { busy = false }
        do { try await FoodAPI.delete(entry, date: date); stage = .removed } catch { self.error = "Couldn't undo that. \(error.localizedDescription)" }
    }
}

struct PhotoLogView: View {
    let share: ShareModel
    @Bindable var photo: PhotoLog
    @FocusState private var focus: Bool

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 18) {
                    switch photo.stage {
                    case .asking: asking
                    case .checking(let e): checking(e)
                    case .added(_, _, let text): done(text, icon: "checkmark.circle.fill")
                    case .removed: done("Taken off again", icon: "arrow.uturn.backward.circle.fill")
                    }
                    if let e = photo.error { Text(e).font(.body).foregroundStyle(Theme.alert) }
                }
                .padding(16)
            }
            .scrollDismissesKeyboard(.interactively)
            .safeAreaInset(edge: .bottom) { bottom.padding(.horizontal, 16).padding(.bottom, 8) }
            .background(PatternedPage())
            .navigationTitle("Add food").navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .cancellationAction) { Button(isDone ? "Close" : "Cancel") { isDone ? share.finish() : share.cancel() } } }
        }
        .interactiveDismissDisabled(photo.busy)
    }

    private var isDone: Bool { if case .asking = photo.stage { return false }; if case .checking = photo.stage { return false }; return true }

    @ViewBuilder private var asking: some View {
        Image(uiImage: photo.image).resizable().scaledToFill().frame(maxWidth: .infinity).frame(height: 220).clipped()
            .clipShape(RoundedRectangle(cornerRadius: Theme.radius, style: .continuous)).accessibilityLabel("The shared photo")
        BigSegments(label: "What is it", options: [("photo", "A meal"), ("label", "A label")], selection: $photo.kind)
        TextField(photo.kind == "photo" ? "Anything to add? e.g. only ate half" : "How much did you eat? e.g. 3 biscuits", text: $photo.text, axis: .vertical)
            .font(.title3).lineLimit(1...3).padding(16).background(RoundedRectangle(cornerRadius: 16).fill(Theme.panel)).focused($focus)
    }

    @ViewBuilder private func checking(_ e: Estimate) -> some View {
        HStack(alignment: .top, spacing: 14) {
            Image(uiImage: photo.image).resizable().scaledToFill().frame(width: 84, height: 84).clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 4) {
                Text(e.name).heading(.title2).foregroundStyle(Theme.text)
                if let n = e.note, !n.isEmpty { Text(n).font(.body).foregroundStyle(Theme.muted) }
            }
        }
        NumbersCard(values: e.per100.flatMap { p in e.grams.map { p.scaled($0 / 100) } } ?? e.values)
        Picked(title: "Meal") { BigSegments(label: "Meal", options: Meal.allCases.map { ($0, $0.single) }, selection: $photo.meal) }
        Text("An estimate. Change it in the Food app if it's off.").font(.footnote).foregroundStyle(Theme.muted)
    }

    private func done(_ text: String, icon: String) -> some View {
        Label(text, systemImage: icon).font(.title2.weight(.semibold)).foregroundStyle(Theme.text).padding(.top, 40)
    }

    @ViewBuilder private var bottom: some View {
        switch photo.stage {
        case .asking:
            ScenePill(title: photo.busy ? "Working it out…" : "Work it out", main: true) { focus = false; Task { await photo.workOut() } }
                .disabled(photo.busy)
        case .checking(let e):
            ScenePill(title: photo.busy ? "Adding…" : "Add to \(photo.meal.single.lowercased())", main: true) { Task { await photo.add(e) } }
                .disabled(photo.busy)
        case .added(let entry, let date, _):
            HStack(spacing: 10) {
                ScenePill(title: "Undo") { Task { await photo.undo(entry: entry, date: date) } }.disabled(photo.busy)
                ScenePill(title: "Done", main: true) { share.finish() }
            }
        case .removed:
            ScenePill(title: "Done", main: true) { share.finish() }
        }
    }
}

extension UIImage {
    /// About 1280 px on the long side, JPEG: plenty for the AI, quick to send from anywhere.
    func jpegForUpload(maxSide: CGFloat = 1280) -> Data? {
        let k = min(1, maxSide / max(size.width, size.height))
        let target = CGSize(width: (size.width * k).rounded(), height: (size.height * k).rounded())
        let f = UIGraphicsImageRendererFormat(); f.scale = 1
        return UIGraphicsImageRenderer(size: target, format: f).image { _ in draw(in: CGRect(origin: .zero, size: target)) }.jpegData(compressionQuality: 0.72)
    }
}
