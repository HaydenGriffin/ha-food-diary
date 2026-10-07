import PhotosUI
import SwiftUI
import VisionKit

extension EnvironmentValues {
    /// Closes the whole Add sheet from any step (a pushed step's own dismiss only goes Back).
    @Entry var closeAdd: () -> Void = {}
}

/// Add food: the first page (AddHome) has the ways in, saved meals, usuals and search. The photo, barcode, label and typing
/// ways in open as steps on this stack, and Back always works. Where food goes (the day, and the meal if Add was tapped on
/// one) is fixed when the sheet opens; the meal can be changed at the top.
struct AddFlowView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    private let opened: AddRequest
    @State private var meal: Meal?          // they can change which meal it goes in, from the top of the sheet
    @State private var path: [Step] = []
    @State private var camera: PhotoKind?
    @State private var started = false
    @State private var picked: UIImage?   // from the photo library: a meal or a label?
    @State private var pending: (barcode: String?, amount: String?)
    @State private var text = ""          // the one box

    enum PhotoKind: String, Identifiable { case meal, label; var id: String { rawValue } }

    init(request: AddRequest) {
        opened = request
        _meal = State(initialValue: request.meal)
    }

    /// The day it was opened for, and the meal they've chosen (or the one it was opened on).
    private var request: AddRequest { var r = opened; r.meal = meal; return r }

    var body: some View {
        NavigationStack(path: $path) {
            AddHome(request: request, meal: Binding(get: { meal ?? opened.defaultMeal }, set: { meal = $0 }), text: $text,
                    open: { path.append($0) }, pick: pick, picked: $picked)
                .navigationDestination(for: Step.self) { step in
                    switch step {
                    case .scan: ScanStep(path: $path, photoLabel: { shoot(.label) })
                    case .manualBarcode: ManualBarcode { code in path.append(.amount(code)) }.navigationTitle("Scan a barcode").navigationBarTitleDisplayMode(.inline)
                    case .type: TypeStep(request: request, path: $path)
                    case .typed(let text): TypeStep(request: request, path: $path, initial: text)
                    case .photo(let p): PhotoStep(request: request, input: p, path: $path)
                    case .amount(let code): AmountStep(request: request, barcode: code, path: $path, photoLabel: { amount in shoot(.label, barcode: code, amount: amount) })
                    case .draft(let box): DraftView(draft: box.draft, date: request.date) { dismiss() }
                    case .many(let box): MultiDraftView(drafts: box.drafts, request: request)
                    }
                }
        }
        .environment(\.closeAdd, { dismiss() })
        .interactiveDismissDisabled(holdsWork)  // a photo or a worked-out draft isn't lost to a stray swipe
        .toastHost()
        .confirmationDialog("What is it?", isPresented: Binding(get: { picked != nil }, set: { if !$0 { picked = nil } }), titleVisibility: .visible) {
            Button("A meal") { if let i = picked { path.append(.photo(PhotoInput(kind: .meal, image: i))) }; picked = nil }
            Button("A nutrition label") { if let i = picked { path.append(.photo(PhotoInput(kind: .label, image: i))) }; picked = nil }
            Button("Cancel", role: .cancel) { picked = nil }
        }
        .fullScreenCover(item: $camera) { kind in
            CameraPicker { image in
                camera = nil
                let (barcode, amount) = pending
                pending = (nil, nil)
                guard let image else { return }
                Task {
                    let small = await image.downsized()
                    path.append(.photo(PhotoInput(kind: kind, image: small, barcode: barcode, amount: amount)))
                }
            }
            .ignoresSafeArea()
        }
        .onAppear {
            guard !started else { return }
            started = true
            switch request.route {
            case .scan: path = [.scan]
            case .photo: shoot(.meal)
            case .label: shoot(.label)
            case .type: path = [.type]
            default: break
            }
        }
    }

    private var holdsWork: Bool {
        !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            || path.contains { if case .photo = $0 { true } else if case .draft = $0 { true } else { false } }
    }

    /// The camera; in UI tests (-testImage <path>, debug builds only) that photo instead, as the simulator has no camera.
    private func shoot(_ kind: PhotoKind, barcode: String? = nil, amount: String? = nil) {
        #if DEBUG
        let args = ProcessInfo.processInfo.arguments
        if let i = args.firstIndex(of: "-testImage"), i + 1 < args.count, let img = UIImage(contentsOfFile: args[i + 1]) {
            path.append(.photo(PhotoInput(kind: kind, image: img, barcode: barcode, amount: amount))); return
        }
        #endif
        pending = (barcode, amount)
        camera = kind
    }

    private func pick(_ r: Route) {
        switch r {
        case .scan: path.append(.scan)
        case .photo: shoot(.meal)
        case .label: shoot(.label)
        default: break
        }
    }
}

final class PhotoInput: Hashable {
    let kind: AddFlowView.PhotoKind
    let image: UIImage
    let barcode: String?
    let amount: String?   // already said for this barcode, so the label step doesn't ask again
    init(kind: AddFlowView.PhotoKind, image: UIImage, barcode: String? = nil, amount: String? = nil) {
        self.kind = kind; self.image = image; self.barcode = barcode; self.amount = amount
    }
    static func == (a: PhotoInput, b: PhotoInput) -> Bool { a === b }
    func hash(into h: inout Hasher) { h.combine(ObjectIdentifier(self)) }
}

final class DraftBox: Hashable {
    var draft: Draft
    init(_ d: Draft) { draft = d }
    static func == (a: DraftBox, b: DraftBox) -> Bool { a === b }
    func hash(into h: inout Hasher) { h.combine(ObjectIdentifier(self)) }
}

enum Step: Hashable { case scan, manualBarcode, type, typed(String), photo(PhotoInput), amount(String), draft(DraftBox), many(DraftsBox) }

// ---------- search ----------

/// What the typed words might mean, from what's already known: saved meals and what's been had. (Adding it as something
/// new is the row just below.)
struct SearchResults: View {
    @Environment(AppModel.self) private var model
    var query: String
    var request: AddRequest
    var open: (Step) -> Void

    private var q: String { query.trimmingCharacters(in: .whitespaces) }
    private func rank(_ name: String) -> Int? {
        if name.lowercased().hasPrefix(q.lowercased()) { return 0 }
        if name.split(separator: " ").contains(where: { $0.lowercased().hasPrefix(q.lowercased()) }) { return 1 }
        return name.localizedStandardContains(q) ? 2 : nil
    }

    var body: some View {
        let saved = model.saved.filter { rank($0.name) != nil }
        let foods = Self.merge(model.allFoods, model.recent).compactMap { f in rank(f.name).map { (f, $0) } }
            .sorted { $0.1 != $1.1 ? $0.1 < $1.1 : $0.0.times > $1.0.times }.map(\.0).prefix(15)
        VStack(alignment: .leading, spacing: 22) {
            if !saved.isEmpty { SavedMealsList(request: request, meals: saved) }
            if !foods.isEmpty {
                group("You've had") {
                    ForEach(Array(foods)) { f in
                        AgainRow(food: f, meal: request.defaultMeal, date: request.date)
                        if f.id != foods.last?.id { Divider().padding(.leading, 82) }
                    }
                }
            }
            if saved.isEmpty && foods.isEmpty {
                Text("Nothing you've had matches. Work it out below and Home Assistant estimates the calories.")
                    .font(.body).foregroundStyle(Theme.muted).fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    private func group(_ title: String, @ViewBuilder rows: () -> some View) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(title).heading(.title3).foregroundStyle(Theme.text)
            VStack(spacing: 0) { rows() }
                .background(RoundedRectangle(cornerRadius: Theme.radius, style: .continuous).fill(Theme.panel))
        }
    }

    /// The longer search list and the usual recent list together, each food once.
    static func merge(_ a: [RecentFood], _ b: [RecentFood]) -> [RecentFood] {
        var seen = Set<String>()
        return (a + b).filter { seen.insert($0.id).inserted }
    }
}

/// Something eaten before: one tap adds it again, as it was, where this sheet is adding.
struct AgainRow: View {
    @Environment(AppModel.self) private var model
    @Environment(\.closeAdd) private var close
    var food: RecentFood
    var meal: Meal
    var date: String

    var body: some View {
        let busy = model.adding.contains(food.id)
        Button { Task { await model.again(food, meal: meal, date: date); close() } } label: {
            HStack(spacing: 14) {
                FoodImage(path: food.image, name: food.name).frame(width: 52, height: 52)
                VStack(alignment: .leading, spacing: 2) {
                    Text(food.name).font(.body.weight(.semibold)).foregroundStyle(Theme.text).multilineTextAlignment(.leading).lineLimit(2)
                    Text("\(amount) · \(Fmt.kcal(kcal)) kcal").font(.subheadline.monospacedDigit()).foregroundStyle(Theme.muted)
                }
                Spacer(minLength: 8)
                if busy { ProgressView().frame(width: 28) } else { Image(systemName: "plus.circle.fill").font(.title2).foregroundStyle(Theme.accent) }
            }
            .padding(.horizontal, 16).frame(minHeight: 64)
            .contentShape(Rectangle())  // the whole row, not just its words
        }
        .buttonStyle(.plain).disabled(busy)
        .accessibilityLabel("Add \(food.name) again, \(Fmt.kcal(kcal)) calories")
    }

    /// As they'd have it: their grams for a label food, else the portions they usually have.
    private var portions: Double { food.per100 != nil && food.grams != nil ? 1 : PortionMemory.portions(for: food.name) }
    private var amount: String {
        if let g = food.grams, food.per100 != nil { return "\(Int(g.rounded())) \(food.unit ?? "g")" }
        return "\(Fmt.portions(portions)) \(portions <= 1 ? "portion" : "portions")"
    }
    private var kcal: Double { food.kcalEach * portions }
}

/// Saved meals: one tap adds the whole meal. Each has a visible ⋯ to delete it (asked first, as it can't be undone).
struct SavedMealsList: View {
    @Environment(AppModel.self) private var model
    @Environment(\.closeAdd) private var close
    var request: AddRequest
    var meals: [SavedMeal]
    @State private var deleting: SavedMeal?
    @State private var renaming: SavedMeal?
    @State private var newName = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Your meals").heading(.title3).foregroundStyle(Theme.text)
            VStack(spacing: 0) {
                ForEach(meals) { s in
                    let busy = model.adding.contains("saved-\(s.id)")
                    HStack(spacing: 0) {
                        Button { Task { await model.logSaved(s, meal: request.meal ?? Meal(rawValue: s.meal) ?? request.defaultMeal, date: request.date); close() } } label: {
                            HStack(spacing: 14) {
                                FoodImage(path: nil, name: s.name).frame(width: 52, height: 52)
                                VStack(alignment: .leading, spacing: 2) {
                                    Text(s.name).font(.body.weight(.semibold)).foregroundStyle(Theme.text).multilineTextAlignment(.leading).lineLimit(2)
                                    Text("\(Fmt.kcal(s.values.kcal)) kcal · \(s.summary)").font(.subheadline.monospacedDigit()).foregroundStyle(Theme.muted)
                                        .lineLimit(1).multilineTextAlignment(.leading)
                                }
                                Spacer(minLength: 8)
                                if busy { ProgressView().frame(width: 28) } else { Image(systemName: "plus.circle.fill").font(.title2).foregroundStyle(Theme.accent) }
                            }
                            .padding(.leading, 16).frame(minHeight: 64)
                            .contentShape(Rectangle())
                        }
                        .buttonStyle(.plain).disabled(busy)
                        .accessibilityLabel("Add \(s.name), \(Fmt.kcal(s.values.kcal)) calories")
                        Menu {
                            Button { newName = s.name; renaming = s } label: { Label("Rename…", systemImage: "pencil") }
                            Picker(selection: Binding(get: { s.meal }, set: { m in Task { await model.updateSaved(s, meal: Meal(rawValue: m)) } })) {
                                ForEach(Meal.allCases) { Text($0.single).tag($0.rawValue) }
                            } label: { Label("Meal", systemImage: "fork.knife") }
                            .pickerStyle(.menu)
                            Divider()
                            Button(role: .destructive) { deleting = s } label: { Label("Delete \(s.name)…", systemImage: "trash") }
                        } label: {
                            Image(systemName: "ellipsis").font(.body.weight(.semibold)).foregroundStyle(Theme.muted).frame(width: 44, height: 64)
                        }
                        .accessibilityLabel("More for \(s.name)")
                    }
                    .padding(.trailing, 4)
                    if s.id != meals.last?.id { Divider().padding(.leading, 82) }
                }
            }
            .background(RoundedRectangle(cornerRadius: Theme.radius, style: .continuous).fill(Theme.panel))
        }
        .confirmationDialog(deleting.map { "Remove \($0.name)?" } ?? "", isPresented: Binding(get: { deleting != nil }, set: { if !$0 { deleting = nil } }),
                            titleVisibility: .visible) {
            Button("Remove", role: .destructive) { if let s = deleting { Task { await model.deleteSaved(s) } }; deleting = nil }
            Button("Cancel", role: .cancel) { deleting = nil }
        } message: { Text("The food you've logged stays; only the shortcut goes.") }
        .alert("Rename", isPresented: Binding(get: { renaming != nil }, set: { if !$0 { renaming = nil } })) {
            TextField("Name", text: $newName)
            Button("Save") { if let s = renaming { Task { await model.updateSaved(s, name: newName) } }; renaming = nil }
            Button("Cancel", role: .cancel) { renaming = nil }
        } message: { Text(renaming.map { "\($0.summary)" } ?? "") }
    }
}

/// A step's main button, kept just above the keyboard (or the bottom of the screen) so it's always in reach.
struct StepButton: View {
    var title: String
    var icon: String
    var busy = false
    var action: () -> Void
    var body: some View {
        BigButton(title: title, icon: icon, lead: true, busy: busy, action: action)
            .padding(.horizontal, 16).padding(.top, 10).padding(.bottom, 8)
            .background(Theme.page.opacity(0.96).ignoresSafeArea())
    }
}

/// Number pads have no Return key: a Done above the keyboard puts it away.
struct KeyboardDone: ViewModifier {
    var focus: FocusState<Bool>.Binding
    func body(content: Content) -> some View {
        content.toolbar {
            ToolbarItemGroup(placement: .keyboard) { Spacer(); Button("Done") { focus.wrappedValue = false }.font(.body.weight(.semibold)) }
        }
    }
}

extension View {
    func keyboardDone(_ focus: FocusState<Bool>.Binding) -> some View { modifier(KeyboardDone(focus: focus)) }
}

// ---------- barcode ----------

struct ScanStep: View {
    @Binding var path: [Step]
    var photoLabel: () -> Void
    @State private var visits = 0  // back from the amount step: the same scanner starts looking again

    var body: some View {
        Group {
            if DataScannerViewController.isSupported && DataScannerViewController.isAvailable {
                BarcodeScanner(visit: visits) { code in path.append(.amount(code)) }.ignoresSafeArea(edges: .bottom)
                    .overlay(alignment: .bottom) {
                        Text("Point at the barcode").font(.title3.weight(.semibold)).foregroundStyle(.white).padding(.horizontal, 20).padding(.vertical, 12)
                            .background(Capsule().fill(.black.opacity(0.6))).padding(.bottom, 40)
                    }
            } else {
                ManualBarcode { code in path.append(.amount(code)) }
            }
        }
        .onAppear { visits += 1 }
        .navigationTitle("Scan a barcode").navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Menu {
                    Button { path.append(.manualBarcode) } label: { Label("Type the number", systemImage: "number") }
                    Button { photoLabel() } label: { Label("Photo of the label instead", systemImage: "text.viewfinder") }
                } label: { Text("Other ways") }
            }
        }
    }
}

/// Without a camera that can scan (or in the simulator, or a worn barcode): type the barcode's digits.
struct ManualBarcode: View {
    var done: (String) -> Void
    @State private var code = ""
    @FocusState private var focus: Bool
    var body: some View {
        Form {
            Section("Barcode digits") {
                TextField("e.g. 5000168001142", text: $code).keyboardType(.numberPad).font(.number(.title2, .regular)).focused($focus)
                Button("Look it up") { focus = false; done(code.filter(\.isNumber)) }.disabled(code.filter(\.isNumber).count < 8)
            }
            .listRowBackground(Theme.panel)
        }
        .scrollContentBackground(.hidden).background(PatternedPage())
        .keyboardDone($focus)
        .onAppear { focus = true }
    }
}

struct BarcodeScanner: UIViewControllerRepresentable {
    var visit: Int
    var found: (String) -> Void

    func makeUIViewController(context: Context) -> DataScannerViewController {
        let vc = DataScannerViewController(recognizedDataTypes: [.barcode(symbologies: [.ean13, .ean8, .upce, .code128])],
                                           qualityLevel: .balanced, isHighFrameRateTrackingEnabled: false, isHighlightingEnabled: true)
        vc.delegate = context.coordinator
        context.coordinator.visit = visit
        try? vc.startScanning()
        return vc
    }
    /// Coming back to it after a scan: look again (the scanner stops itself once it has found a barcode).
    func updateUIViewController(_ vc: DataScannerViewController, context: Context) {
        guard visit != context.coordinator.visit else { return }
        context.coordinator.visit = visit
        context.coordinator.done = false
        if !vc.isScanning { try? vc.startScanning() }
    }
    func makeCoordinator() -> Coordinator { Coordinator(found: found) }

    final class Coordinator: NSObject, DataScannerViewControllerDelegate {
        let found: (String) -> Void
        var visit = 0
        var done = false
        init(found: @escaping (String) -> Void) { self.found = found }
        func dataScanner(_ s: DataScannerViewController, didAdd items: [RecognizedItem], allItems: [RecognizedItem]) {
            guard !done else { return }
            for case .barcode(let b) in items {
                if let v = b.payloadStringValue, v.filter(\.isNumber).count >= 8 {
                    done = true
                    UINotificationFeedbackGenerator().notificationOccurred(.success)
                    s.stopScanning()
                    found(v)
                    return
                }
            }
        }
    }
}

/// After a scan: how much did you eat? (quick picks for the usual answers)
struct AmountStep: View {
    @Environment(AppModel.self) private var model
    var request: AddRequest
    var barcode: String
    @Binding var path: [Step]
    var photoLabel: (String?) -> Void
    @State private var amount = ""
    @State private var busy = false
    @State private var unknown = false
    @State private var work: Task<Void, Never>?
    @FocusState private var focus: Bool

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                if unknown {
                    VStack(alignment: .leading, spacing: 14) {
                        Text("Not known yet").heading(.title2).foregroundStyle(Theme.text)
                        Text("This product isn't in the food database yet. Take a photo of its nutrition label and Home Assistant will remember it.")
                            .font(.body).foregroundStyle(Theme.soft)
                        BigButton(title: "Photo of the label", icon: "text.viewfinder", lead: true) { photoLabel(said) }
                        BigButton(title: "Type it instead", icon: "keyboard") { path.append(.type) }
                    }
                } else {
                    Text("How much did you eat?").heading(.title2).foregroundStyle(Theme.text)
                    TextField("e.g. 2 biscuits, 50 g, half the pack", text: $amount).font(.title3).padding(16)
                        .background(RoundedRectangle(cornerRadius: 16).fill(Theme.panel)).focused($focus).submitLabel(.go)
                        .onSubmit { go() }
                    FlowChips(items: ["1 serving", "2 servings", "half the pack", "the whole pack", "100 g"]) { amount = $0; go() }
                }
            }
            .padding(16)
        }
        .scrollDismissesKeyboard(.interactively)
        .safeAreaInset(edge: .bottom) {
            if !unknown { StepButton(title: busy ? "Looking it up…" : "Work it out", icon: "wand.and.stars", busy: busy) { go() } }
        }
        .background(PatternedPage())
        .navigationTitle("Scan a barcode").navigationBarTitleDisplayMode(.inline)
        .onDisappear { work?.cancel() }  // no keyboard at first: after a scan, a chip and Work it out are one tap each
    }

    private var said: String? { amount.trimmingCharacters(in: .whitespaces).isEmpty ? nil : amount }

    private func go() {
        guard !busy else { return }
        busy = true
        work = Task {
            defer { busy = false }
            do {
                let e = try await FoodAPI.estimate(["kind": "barcode", "barcode": barcode, "amount": said ?? "1 serving"])
                guard !Task.isCancelled else { return }
                path.append(.draft(DraftBox(Draft(e, meal: request.defaultMeal))))
            } catch {
                guard !Task.isCancelled else { return }
                if error.localizedDescription.contains("isn't in Open Food Facts") { focus = false; withAnimation { unknown = true } }
                else { model.show(error) }
            }
        }
    }
}

// ---------- photos ----------

/// The photo is in: a meal takes an optional note ("only ate half", "no rice"); a label asks how much.
struct PhotoStep: View {
    @Environment(AppModel.self) private var model
    var request: AddRequest
    var input: PhotoInput
    @Binding var path: [Step]
    @State private var text = ""
    @State private var busy = false
    @State private var work: Task<Void, Never>?
    @FocusState private var focus: Bool

    private var meal: Bool { input.kind == .meal }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                Image(uiImage: input.image).resizable().scaledToFill().frame(maxWidth: .infinity).frame(height: focus ? 120 : 240).clipped()
                    .animation(.snappy, value: focus)
                    .clipShape(RoundedRectangle(cornerRadius: Theme.radius, style: .continuous)).accessibilityLabel(meal ? "Your meal" : "The label")
                Text(meal ? "Anything to add?" : "How much did you eat?").heading(.title2).foregroundStyle(Theme.text)
                if meal { Text("Optional. It helps the estimate.").font(.body).foregroundStyle(Theme.muted) }
                TextField(meal ? "e.g. only ate half, no rice, a big portion" : "e.g. 3 biscuits, 50 g, half the pack", text: $text, axis: .vertical)
                    .font(.title3).lineLimit(1...3).padding(16).background(RoundedRectangle(cornerRadius: 16).fill(Theme.panel))
                    .focused($focus).submitLabel(.go)
                    .onChange(of: text) { _, t in if t.contains("\n") { text = t.replacingOccurrences(of: "\n", with: ""); go() } }  // Return works it out
                if !meal { FlowChips(items: ["1 serving", "2 servings", "half the pack", "100 g"]) { text = $0; go() } }
            }
            .padding(16)
        }
        .scrollDismissesKeyboard(.interactively)
        .safeAreaInset(edge: .bottom) {
            StepButton(title: busy ? (meal ? "Looking at the photo…" : "Reading the label…") : "Work it out", icon: "wand.and.stars", busy: busy) { go() }
        }
        .background(PatternedPage())
        .navigationTitle(meal ? "Photo of a meal" : "Photo of a label").navigationBarTitleDisplayMode(.inline)
        .onAppear {
            if let a = input.amount, text.isEmpty { text = a }
            if !meal && input.amount == nil { focus = true }
        }
        .onDisappear { work?.cancel() }
    }

    private func go() {
        guard !busy else { return }
        busy = true; focus = false
        let said = text.trimmingCharacters(in: .whitespacesAndNewlines), image = input.image, kind = meal, code = input.barcode
        work = Task {
            defer { busy = false }
            guard let b64 = await Task.detached(priority: .userInitiated, operation: { image.jpegForUpload()?.base64EncodedString() }).value else { return }
            do {
                var data: [String: Any] = kind ? ["kind": "photo", "image": b64, "hint": said] : ["kind": "label", "image": b64, "amount": said]
                if let code { data["barcode"] = code }
                let e = try await FoodAPI.estimate(data)
                guard !Task.isCancelled else { return }
                path.append(.draft(DraftBox(Draft(e, image: image, meal: request.defaultMeal))))
            } catch {
                if !Task.isCancelled { model.show(error) }
            }
        }
    }
}

extension UIImage {
    /// About 1280 px on the long side, JPEG: plenty for the AI, quick to send from anywhere.
    func jpegForUpload(maxSide: CGFloat = 1280) -> Data? {
        let k = min(1, maxSide / max(size.width, size.height))
        let target = CGSize(width: (size.width * k).rounded(), height: (size.height * k).rounded())
        let img = UIGraphicsImageRenderer(size: target, format: { let f = UIGraphicsImageRendererFormat(); f.scale = 1; return f }()).image { _ in draw(in: CGRect(origin: .zero, size: target)) }
        return img.jpegData(compressionQuality: 0.72)
    }

    /// A camera or library photo shrunk once, as it arrives, so a 48 MP original isn't held while they add a note.
    func downsized(maxSide: CGFloat = 1600) async -> UIImage {
        let k = maxSide / max(size.width, size.height)
        guard k < 1 else { return self }
        return await byPreparingThumbnail(ofSize: CGSize(width: size.width * k, height: size.height * k)) ?? self
    }
}

/// The camera (or, where there's none, the photo library).
struct CameraPicker: UIViewControllerRepresentable {
    var done: (UIImage?) -> Void
    func makeUIViewController(context: Context) -> UIImagePickerController {
        let p = UIImagePickerController()
        p.sourceType = UIImagePickerController.isSourceTypeAvailable(.camera) ? .camera : .photoLibrary
        p.delegate = context.coordinator
        return p
    }
    func updateUIViewController(_ vc: UIImagePickerController, context: Context) {}
    func makeCoordinator() -> Coordinator { Coordinator(done: done) }
    final class Coordinator: NSObject, UIImagePickerControllerDelegate, UINavigationControllerDelegate {
        let done: (UIImage?) -> Void
        init(done: @escaping (UIImage?) -> Void) { self.done = done }
        func imagePickerController(_ p: UIImagePickerController, didFinishPickingMediaWithInfo info: [UIImagePickerController.InfoKey: Any]) { done(info[.originalImage] as? UIImage) }
        func imagePickerControllerDidCancel(_ p: UIImagePickerController) { done(nil) }
    }
}

// ---------- typing ----------

struct TypeStep: View {
    @Environment(AppModel.self) private var model
    var request: AddRequest
    @Binding var path: [Step]
    var initial = ""
    @State private var text = ""
    @State private var started = false
    @State private var busy = false
    @State private var work: Task<Void, Never>?
    @FocusState private var focus: Bool

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                Text("What did you eat?").heading(.title2).foregroundStyle(Theme.text)
                TextField("e.g. 2 eggs on toast, or porridge, a banana, a latte", text: $text, axis: .vertical).font(.title3).lineLimit(1...4).padding(16)
                    .background(RoundedRectangle(cornerRadius: 16).fill(Theme.panel)).focused($focus).submitLabel(.go)
                    .onChange(of: text) { _, t in if t.contains("\n") { text = t.replacingOccurrences(of: "\n", with: ""); go() } }  // Go works it out
            }
            .padding(16)
        }
        .scrollDismissesKeyboard(.interactively)
        .safeAreaInset(edge: .bottom) {
            StepButton(title: busy ? "Working it out…" : "Work it out", icon: "wand.and.stars", busy: busy) { go() }
                .disabled(text.trimmingCharacters(in: .whitespaces).isEmpty)
        }
        .background(PatternedPage())
        .navigationTitle("Type it").navigationBarTitleDisplayMode(.inline)
        .onAppear {
            guard !started else { return }
            started = true
            if initial.isEmpty { focus = true } else { text = initial; go() }  // from search: straight to working it out
        }
        .onDisappear { work?.cancel() }
    }

    private func go() {
        let t = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !busy, !t.isEmpty else { return }
        busy = true
        work = Task {
            defer { busy = false }
            do {
                // "two eggs, toast, a latte": each food goes in on its own
                let parts = MultiFood.split(t)
                if parts.count >= 2 {
                    let es = try await MultiFood.estimates(parts)
                    guard !Task.isCancelled else { return }
                    path.append(.many(DraftsBox(es.map { Draft($0, meal: request.defaultMeal) })))
                    return
                }
                let e = try await FoodAPI.estimate(["kind": "text", "text": t])
                guard !Task.isCancelled else { return }
                path.append(.draft(DraftBox(Draft(e, meal: request.defaultMeal))))
            } catch {
                if !Task.isCancelled { model.show(error) }
            }
        }
    }
}

// ---------- shared bits ----------

struct BigButton: View {
    var title: String
    var icon: String
    var lead = false
    var busy = false
    var role: ButtonRole?
    var action: () -> Void
    var body: some View {
        Button(role: role, action: action) {
            HStack(spacing: 10) {
                if busy { ProgressView().tint(lead ? Theme.fillInk : Theme.text) } else { Image(systemName: icon) }
                Text(title).multilineTextAlignment(.center)
            }
            .font(.title3.weight(.semibold)).foregroundStyle(role == .destructive ? Theme.alert : lead ? Theme.fillInk : Theme.text)
            .padding(.horizontal, 16)
            .frame(maxWidth: .infinity, minHeight: 60)
            .background(Capsule().fill(lead ? Theme.fill : Theme.raised))
            .contentShape(Capsule())
        }
        .buttonStyle(.plain).disabled(busy)
    }
}

struct FlowChips: View {
    var items: [String]
    var tap: (String) -> Void
    var body: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 8) {
                ForEach(items, id: \.self) { s in
                    Button { tap(s) } label: {
                        Text(s).font(.body.weight(.semibold)).foregroundStyle(Theme.text).padding(.horizontal, 16).frame(minHeight: 48)
                            .background(Capsule().fill(Theme.raised)).contentShape(Capsule())
                    }
                    .buttonStyle(.plain)
                }
            }
        }
        .padding(.horizontal, -16).contentMargins(.horizontal, 16, for: .scrollContent)  // runs to the screen edges, so the last chips show they're there
    }
}
