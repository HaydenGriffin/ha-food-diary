import SwiftUI

/// A logged entry, on the day it's on: how much (grams or portions), which meal, the user's own numbers and photo, or remove
/// it. Unsaved changes are never lost to a swipe or Close without asking.
struct EntrySheet: View {
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    let entry: Entry
    let date: String
    @State private var grams = 0.0
    @State private var portions = 1.0
    @State private var meal = Meal.snack
    @State private var own: Nutrients?
    @State private var editing = false
    @State private var busy = false
    @State private var choosing = false
    @State private var photoBusy = false
    @State private var image: String?
    @State private var loaded = false
    @State private var confirmClose = false
    @FocusState private var focus: Bool

    private var changes: [String: Any] {
        var c: [String: Any] = [:]
        if meal != entry.mealValue { c["meal"] = meal.rawValue }
        if let own { c.merge(own.dict) { _, b in b } }
        else if entry.per100 != nil, grams != (entry.grams ?? 0) { c["grams"] = grams }
        else if entry.per100 == nil, portions != entry.portions { c["portions"] = portions }
        return c
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 20) {
                    photo
                    Text(entry.name).heading(.title2).foregroundStyle(Theme.text)
                    NumbersCard(values: preview)
                    if entry.per100 != nil && own == nil {
                        GramsField(grams: $grams, unit: entry.unit ?? "g", focus: $focus)
                    } else if own == nil {
                        Picked(title: "How much") { BigSegments(label: "Portions", options: Portions.options, selection: $portions) }
                    }
                    Picked(title: "Meal") { BigSegments(label: "Meal", options: Meal.allCases.map { ($0, $0.single) }, selection: $meal) }
                    if editing {
                        OwnNumbers(start: preview, focus: $focus) { v in own = v; editing = false }
                    } else {
                        changeNumbers
                    }
                    if let n = entry.note { Text(n).font(.footnote).foregroundStyle(Theme.muted) }
                    BigButton(title: "Remove", icon: "trash", role: .destructive) {
                        dismiss()
                        Task { await model.delete(entry, date: date) }  // Undo puts it back as it was
                    }
                }
                .padding(16)
            }
            .scrollDismissesKeyboard(.interactively)
            .safeAreaInset(edge: .bottom) {
                StepButton(title: busy ? "Saving…" : changes.isEmpty ? "Done" : "Save", icon: "checkmark", busy: busy) { Task { await save() } }
            }
            .keyboardDone($focus)
            .background(PatternedPage())
            .navigationTitle("\(entry.mealValue.single) · \(DayName.of(date).prefix(1).uppercased() + DayName.of(date).dropFirst())")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Close") { if changes.isEmpty { dismiss() } else { confirmClose = true } } }
            }
            .confirmationDialog("Discard your changes?", isPresented: $confirmClose, titleVisibility: .visible) {
                Button("Discard changes", role: .destructive) { dismiss() }
                Button("Keep editing", role: .cancel) {}
            }
        }
        .toastHost()
        .interactiveDismissDisabled(!changes.isEmpty)
        .presentationDetents([.large])
        .onAppear {
            guard !loaded else { return }  // the camera closing must not reset what they've changed
            loaded = true
            grams = entry.grams ?? 0; portions = entry.portions; meal = entry.mealValue; image = entry.image
        }
        .choosePhoto(isPresented: $choosing) { jpeg in Task { await setPhoto(jpeg) } }
    }

    private var changeNumbers: some View {
        Button { editing = true } label: { Label("Change numbers", systemImage: "pencil").font(.body.weight(.semibold)).frame(minHeight: 48).contentShape(Rectangle()) }
            .buttonStyle(.plain).foregroundStyle(Theme.accent)
    }

    /// The user's own photo already, or a stand-in (the product's, or a drawn tile) that can be replaced.
    private var ownPhoto: Bool { (image ?? "").hasPrefix("/api/") }

    /// The food's picture, and a way to put their own photo on it.
    private var photo: some View {
        FoodImage(path: image, name: entry.name, corner: Theme.radius, full: true)
            .frame(maxWidth: .infinity).frame(height: 200)
            .overlay(alignment: .bottomTrailing) {
                Button { choosing = true } label: {
                    HStack(spacing: 6) {
                        if photoBusy { ProgressView().tint(Theme.fillInk) } else { Image(systemName: "camera") }
                        Text(ownPhoto ? "Change photo" : "Add a photo")
                    }
                    .font(.body.weight(.semibold)).foregroundStyle(Theme.fillInk)
                    .padding(.horizontal, 16).frame(minHeight: 48).background(Capsule().fill(Theme.fill.opacity(0.92)))
                    .contentShape(Capsule())
                }
                .buttonStyle(.plain).disabled(photoBusy).padding(12)
            }
    }

    private func setPhoto(_ jpeg: Data) async {
        photoBusy = true; defer { photoBusy = false }
        do {
            try await FoodAPI.setPhoto(entry.id, date: date, jpeg: jpeg)
            if let old = image { await ImageStore.shared.forget(old) }
            let fresh = try? await FoodAPI.day(date)
            image = fresh?.entries.first { $0.id == entry.id }?.image ?? image
            model.toast = Toast(text: "Photo saved")
            model.changed()
        } catch { model.show(error) }
    }

    private var preview: Nutrients {
        if let own { return own }
        if let p = entry.per100, entry.grams != nil { return p.scaled(grams / 100) }
        return entry.perPortion.scaled(portions)
    }

    private func save() async {
        let c = changes
        guard !c.isEmpty else { dismiss(); return }
        busy = true; defer { busy = false }
        if await model.update(entry, date: date, c) { model.toast = Toast(text: "Saved \(entry.name)"); dismiss() }
    }
}
