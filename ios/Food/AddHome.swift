import PhotosUI
import SwiftUI

/// The first page of the Add sheet. Before anything's typed: the ways in (meal photo, barcode, label, a photo already
/// taken, typing), saved meals, and what's eaten most, one tap each. While typing: matching foods from the diary, and
/// "Work it out" for anything new. Close shuts the whole sheet; the meal can be changed at the top.
struct AddHome: View {
    @Environment(AppModel.self) private var model
    @Environment(\.closeAdd) private var close
    var request: AddRequest
    @Binding var meal: Meal
    @Binding var text: String
    var open: (Step) -> Void
    var pick: (Route) -> Void
    @Binding var picked: UIImage?
    @State private var library: PhotosPickerItem?
    @State private var libraryOpen = false
    @FocusState private var focus: Bool

    private var query: String { text.trimmingCharacters(in: .whitespacesAndNewlines) }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                if query.isEmpty {
                    ways
                    if !model.saved.isEmpty { SavedMealsList(request: request, meals: model.saved) }
                    if !quick.isEmpty { often }
                } else {
                    SearchResults(query: query, request: request, open: open)
                    WorkItOutRow(text: query) { workOut() }
                }
            }
            .padding(16)
        }
        .scrollDismissesKeyboard(.interactively)
        .safeAreaInset(edge: .bottom) { box }
        .onChange(of: query.isEmpty) { _, empty in if !empty { Task { await model.loadSearch() } } }
        .task { await model.loadSearch() }  // opens without the keyboard, so the ways in are all in view
        .toastHost(edge: .top)
        .background(PatternedPage())
        .navigationTitle("").navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .cancellationAction) { Button("Close") { close() } }
            ToolbarItem(placement: .principal) { MealTarget(request: request, meal: $meal) }
        }
        .photosPicker(isPresented: $libraryOpen, selection: $library, matching: .images)
        .onChange(of: library) { _, it in
            guard let it else { return }
            Task {
                defer { library = nil }
                if let data = try? await it.loadTransferable(type: Data.self), let img = UIImage(data: data) { picked = await img.downsized() }
            }
        }
    }

    // ---------- before anything's typed ----------

    /// The ways in, as big tiles: two across.
    private var ways: some View {
        LazyVGrid(columns: [GridItem(.flexible(), spacing: 10), GridItem(.flexible(), spacing: 10)], spacing: 10) {
            WayTile(icon: "camera", title: "Photo of a meal") { pick(.photo) }
            WayTile(icon: "barcode.viewfinder", title: "Scan a barcode") { pick(.scan) }
            WayTile(icon: "text.viewfinder", title: "Photo of a label") { pick(.label) }
            WayTile(icon: "photo.on.rectangle", title: "From your photos") { libraryOpen = true }
        }
    }

    /// What's had most in this meal, then most often overall: eight at most.
    private var quick: [RecentFood] {
        var seen = Set<String>()
        return ((model.usuals[request.defaultMeal.rawValue] ?? []) + model.recent).filter { seen.insert($0.name.lowercased()).inserted }.prefix(8).map { $0 }
    }

    private var often: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("You often have").heading(.title3).foregroundStyle(Theme.text)
            FlowLayout(spacing: 8) {
                ForEach(quick) { f in
                    let busy = model.adding.contains(f.id)
                    Button { Task { await model.again(f, meal: request.defaultMeal, date: request.date); close() } } label: {
                        HStack(spacing: 8) {
                            if busy { ProgressView().controlSize(.small) } else { Image(systemName: "plus").font(.footnote.weight(.bold)).foregroundStyle(Theme.accent) }
                            Text(f.name).font(.subheadline.weight(.semibold)).foregroundStyle(Theme.text).lineLimit(1)
                            Text(Fmt.kcal(f.kcalEach * PortionMemory.portions(for: f.name))).font(.number(.subheadline, .regular)).foregroundStyle(Theme.muted)
                        }
                        .padding(.horizontal, 14).frame(minHeight: 44).background(Capsule().fill(Theme.panel)).contentShape(Capsule())
                    }
                    .buttonStyle(.plain).disabled(busy)
                    .accessibilityLabel("Add \(f.name), \(Fmt.kcal(f.kcalEach)) calories")
                }
            }
        }
    }

    // ---------- the box ----------

    private var box: some View {
        HStack(alignment: .bottom, spacing: 8) {
            TextField("Search, or type what you ate", text: $text, prompt: Text("Search, or type what you ate").foregroundStyle(Theme.muted), axis: .vertical)
                .lineLimit(1...4).font(.body)
                .padding(.horizontal, 16).padding(.vertical, 12)
                .background(RoundedRectangle(cornerRadius: 22, style: .continuous).fill(Theme.panel))
                .overlay(RoundedRectangle(cornerRadius: 22, style: .continuous).strokeBorder(Theme.raised2, lineWidth: 1))
                .focused($focus).submitLabel(.go)
                .onChange(of: text) { _, t in
                    // Return in a box that grows to four lines arrives as a newline: take it out, then work it out
                    guard t.contains("\n") else { return }
                    text = t.replacingOccurrences(of: "\n", with: " ").trimmingCharacters(in: .whitespaces)
                    Task { @MainActor in workOut() }
                }
                .accessibilityHint("Type what you ate, or search what you've had")
                .accessibilityIdentifier("composer")
            Button { workOut() } label: {
                Image(systemName: "arrow.up").font(.body.weight(.bold)).foregroundStyle(Theme.fillInk).frame(width: 44, height: 44)
                    .background(Circle().fill(Theme.fill))
            }
            .buttonStyle(.plain)
            .disabled(query.isEmpty).opacity(query.isEmpty ? 0.4 : 1)
            .accessibilityLabel("Work it out")
        }
        .dynamicTypeSize(...DynamicTypeSize.accessibility2)
        .padding(.horizontal, 16).padding(.vertical, 8)
        .background(Theme.page.opacity(0.96).ignoresSafeArea())
    }

    /// Anything new: Home Assistant works out the calories (several foods when it's a list).
    private func workOut() {
        guard !query.isEmpty else { return }
        let t = query
        focus = false
        text = ""
        open(.typed(t))
    }
}

/// One way in: an icon and its name on a panel.
private struct WayTile: View {
    var icon: String
    var title: String
    var action: () -> Void

    var body: some View {
        Button(action: action) {
            VStack(alignment: .leading, spacing: 10) {
                Image(systemName: icon).font(.title2.weight(.semibold)).foregroundStyle(Theme.accent)
                    .frame(width: 44, height: 44).background(Circle().fill(Theme.accentSoft))
                Text(title).font(.body.weight(.semibold)).foregroundStyle(Theme.text).multilineTextAlignment(.leading)
            }
            .padding(14).frame(maxWidth: .infinity, minHeight: 104, alignment: .topLeading)
            .background(RoundedRectangle(cornerRadius: Theme.radius, style: .continuous).fill(Theme.panel))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel(title)
    }
}

/// Under the search results: add what was typed as something new.
private struct WorkItOutRow: View {
    var text: String
    var action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: 12) {
                Image(systemName: "wand.and.stars").font(.title3).foregroundStyle(Theme.accent).frame(width: 28)
                Text("Work out \u{201C}\(text)\u{201D}").font(.body.weight(.semibold)).foregroundStyle(Theme.text)
                    .lineLimit(2).truncationMode(.middle).multilineTextAlignment(.leading)
                Spacer(minLength: 4)
                Image(systemName: "chevron.right").font(.footnote.weight(.semibold)).foregroundStyle(Theme.muted)
            }
            .padding(.horizontal, 16).frame(minHeight: 56)
            .background(RoundedRectangle(cornerRadius: Theme.radius, style: .continuous).fill(Theme.panel))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }
}

/// At the top of the sheet: where food goes ("Lunch", "Thursday's dinner"), changeable before anything's added.
struct MealTarget: View {
    var request: AddRequest
    @Binding var meal: Meal

    var body: some View {
        Menu {
            Picker("Meal", selection: $meal) { ForEach(Meal.allCases) { m in Text(m.single).tag(m) } }
        } label: {
            HStack(spacing: 4) {
                Text(Self.name(meal, on: request.date)).font(.body.weight(.semibold))
                Image(systemName: "chevron.up.chevron.down").font(.footnote.weight(.semibold))
            }
            .foregroundStyle(Theme.text).frame(minHeight: 44).contentShape(Rectangle())
        }
        .accessibilityLabel("Adding to \(Self.name(meal, on: request.date))")
        .accessibilityHint("Changes which meal food goes in")
    }

    /// "Lunch", "Tomorrow's breakfast", "Thursday's dinner".
    static func name(_ m: Meal, on date: String) -> String {
        let s = DayName.meal(m, on: date)
        return s.prefix(1).uppercased() + s.dropFirst()
    }
}

/// Chips that wrap onto as many lines as they need.
struct FlowLayout: Layout {
    var spacing: CGFloat = 8

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let rows = arrange(subviews, width: proposal.width ?? .infinity)
        return CGSize(width: proposal.width ?? rows.map(\.width).max() ?? 0, height: rows.last.map { $0.y + $0.height } ?? 0)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        for row in arrange(subviews, width: bounds.width) {
            var x = bounds.minX
            for i in row.items {
                let size = subviews[i].sizeThatFits(.unspecified)
                subviews[i].place(at: CGPoint(x: x, y: bounds.minY + row.y), proposal: ProposedViewSize(size))
                x += size.width + spacing
            }
        }
    }

    private struct Row { var items: [Int] = []; var y: CGFloat = 0; var width: CGFloat = 0; var height: CGFloat = 0 }

    private func arrange(_ subviews: Subviews, width: CGFloat) -> [Row] {
        var rows: [Row] = [Row()]
        for i in subviews.indices {
            let size = subviews[i].sizeThatFits(.unspecified)
            var row = rows[rows.count - 1]
            if !row.items.isEmpty && row.width + spacing + size.width > width {
                rows.append(Row(y: row.y + row.height + spacing))
                row = rows[rows.count - 1]
            }
            row.width += (row.items.isEmpty ? 0 : spacing) + size.width
            row.height = max(row.height, size.height)
            row.items.append(i)
            rows[rows.count - 1] = row
        }
        return rows
    }
}
