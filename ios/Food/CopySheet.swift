import SwiftUI

/// What to copy: a day, or one meal of it.
struct CopyRequest: Identifiable, Hashable {
    var from: String
    var meal: Meal?
    var move = false   // to one other day, taking it off this one
    var id: String { "\(from)|\(meal?.rawValue ?? "day")|\(move)" }
}

/// "Copy Monday to…": what's being copied, the next two weeks to tick, add to or replace what's there, Undo after. "Move
/// Monday to…" is the same with one day to pick, and the food comes off Monday once it's there.
struct CopySheet: View {
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    let request: CopyRequest
    @State private var source: Day?
    @State private var failed: String?
    @State private var picked: Set<String> = []
    @State private var replace = false
    @State private var busy = false
    @State private var confirmReplace = false
    @ScaledMetric(relativeTo: .title3) private var chip: CGFloat = 76

    private var food: [Entry] { (source?.entries ?? []).filter { request.meal == nil || $0.mealValue == request.meal } }
    /// Yesterday and the next two weeks; the day being copied stays in its place (shown as the one it's from).
    private var days: [String] { (-1..<14).compactMap { Calendar.current.date(byAdding: .day, value: $0, to: Date())?.ymd } }
    /// The rest of this week after the day being copied (to Sunday).
    private var restOfWeek: [String] {
        guard let from = Date.fromYMD(request.from) else { return [] }
        let left = (8 - Calendar.current.component(.weekday, from: from)) % 7
        return (0..<left).compactMap { Calendar.current.date(byAdding: .day, value: $0 + 1, to: from)?.ymd }.filter(days.contains)
    }
    private var what: String { request.meal.map { $0.single.lowercased() } ?? "food" }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 22) {
                    summary
                    VStack(alignment: .leading, spacing: 12) {
                        HStack {
                            Text("To").heading(.title3).foregroundStyle(Theme.text)
                            Spacer()
                            if !request.move {
                                Button(picked == Set(restOfWeek) && !restOfWeek.isEmpty ? "Clear" : "Rest of the week") {
                                    picked = picked == Set(restOfWeek) ? [] : Set(restOfWeek)
                                }
                                .font(.body.weight(.semibold)).foregroundStyle(Theme.accent).frame(minHeight: 44)
                                .disabled(restOfWeek.isEmpty)
                            }
                        }
                        LazyVGrid(columns: [GridItem(.adaptive(minimum: chip), spacing: 8)], spacing: 8) {
                            ForEach(days, id: \.self) { d in
                                DayChip(date: d, on: picked.contains(d), source: d == request.from) { toggle(d) }
                            }
                        }
                    }
                    if request.move {
                        Text(picked.first.map { "It comes off \(DayName.of(request.from)) once it's on \(DayName.of($0)). You can undo it straight after." }
                             ?? "Pick the day it should be on. It comes off \(DayName.of(request.from)) once it's there.")
                            .font(.subheadline).foregroundStyle(Theme.muted).fixedSize(horizontal: false, vertical: true)
                    } else { replaceToggle }
                }
                .padding(16)
            }
            .background(PatternedPage())
            .safeAreaInset(edge: .bottom) {
                StepButton(title: busy ? (request.move ? "Moving…" : "Copying…") : picked.isEmpty ? "Pick the \(request.move ? "day" : "days")" : goTitle,
                           icon: request.move ? "arrow.right" : "doc.on.doc", busy: busy) { if replace { confirmReplace = true } else { Task { await copy() } } }
                    .disabled(picked.isEmpty || food.isEmpty || busy)
                    .opacity(picked.isEmpty || food.isEmpty ? 0.5 : 1)
            }
            .navigationTitle(title).navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Close") { dismiss() } } }
            .confirmationDialog("Replace the \(what) on \(picked.count) day\(picked.count == 1 ? "" : "s")?", isPresented: $confirmReplace, titleVisibility: .visible) {
                Button("Replace and copy", role: .destructive) { Task { await copy() } }
                Button("Cancel", role: .cancel) {}
            } message: { Text("What's there now is removed first. You can undo it straight after.") }
            .task { await load() }
        }
        .toastHost(bottom: 96)
    }

    private var goTitle: String {
        if request.move, let d = picked.first { return "Move to \(DayName.of(d))" }
        return "Copy to \(picked.count) day\(picked.count == 1 ? "" : "s")"
    }

    private var replaceToggle: some View {
                    Toggle(isOn: $replace) {
                        VStack(alignment: .leading, spacing: 2) {
                            Text("Replace what's there").font(.body.weight(.semibold)).foregroundStyle(Theme.text)
                            Text(replace ? "Removes those days' \(what) first, then copies." : "Adds to what those days already have.")
                                .font(.subheadline).foregroundStyle(Theme.muted)
                        }
                    }
                    .tint(Theme.ring)
                    .padding(16).background(RoundedRectangle(cornerRadius: Theme.radius, style: .continuous).fill(Theme.panel))
    }

    private var title: String {
        let day = DayName.of(request.from), verb = request.move ? "Move" : "Copy"
        return request.meal.map { "\(verb) \(day)'s \($0.single.lowercased())" } ?? "\(verb) \(day)"
    }

    /// The food being copied, with photos, so they can see it's the right thing.
    @ViewBuilder private var summary: some View {
        if let failed {
            VStack(alignment: .leading, spacing: 8) {
                Text(failed).font(.body).foregroundStyle(Theme.text)
                Button("Try again") { Task { await load() } }.font(.body.weight(.semibold)).foregroundStyle(Theme.accent).frame(minHeight: 44)
            }
        } else if source == nil {
            HStack { Spacer(); ProgressView(); Spacer() }.padding(24)
        } else if food.isEmpty {
            Text("Nothing logged there yet.").font(.body).foregroundStyle(Theme.muted)
        } else {
            VStack(alignment: .leading, spacing: 12) {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(alignment: .top, spacing: 10) {
                        ForEach(food) { e in
                            VStack(alignment: .leading, spacing: 6) {
                                FoodImage(path: e.image, name: e.name).frame(width: 96, height: 96)
                                Text(e.name).font(.footnote.weight(.semibold)).foregroundStyle(Theme.text).lineLimit(3).frame(width: 96, alignment: .leading)
                            }
                            .accessibilityElement(children: .combine)
                        }
                    }
                }
                Text("\(food.count) item\(food.count == 1 ? "" : "s"), \(Fmt.kcal(food.reduce(0) { $0 + $1.totals.kcal })) kcal")
                    .font(.number(.body, .regular)).foregroundStyle(Theme.muted)
            }
        }
    }

    private func load() async {
        failed = nil
        do { source = try await FoodAPI.day(request.from) } catch { failed = error.localizedDescription }
    }

    private func toggle(_ d: String) {
        if picked.contains(d) { picked.remove(d) } else if request.move { picked = [d] } else { picked.insert(d) }
    }

    private func copy() async {
        busy = true; defer { busy = false }
        do {
            let r = try await FoodAPI.copyDay(from: request.from, to: picked.sorted(), meal: request.meal, replace: replace)
            if request.move, let to = picked.first {
                // there now: take the originals off the day they came from (Undo brings them back, photos and all)
                var removed: [Entry] = []
                for e in food { try await FoodAPI.delete(e.id, date: request.from); removed.append(e) }
                let from = request.from
                model.toast = Toast(text: "Moved \(removed.count) item\(removed.count == 1 ? "" : "s") to \(DayName.of(to))") {
                    try await FoodAPI.undoCopy(r.token)
                    for e in removed { _ = try await FoodAPI.log(e.restorePayload(date: from)) }
                }
                model.changed()
                dismiss()
                return
            }
            let n = r.added.count, gone = r.removed.values.reduce(0, +)
            let text = "Copied to \(n) day\(n == 1 ? "" : "s")" + (gone > 0 ? ", replacing \(gone) item\(gone == 1 ? "" : "s")" : "")
            model.toast = Toast(text: text) { try await FoodAPI.undoCopy(r.token) }
            model.changed()
            dismiss()
        } catch { model.show(error) }
    }
}

/// A day to tick: its weekday and date, filled and outlined when chosen. The day being copied sits in its place, marked.
struct DayChip: View {
    var date: String
    var on: Bool
    var source = false
    var tap: () -> Void

    var body: some View {
        let d = Date.fromYMD(date)
        Button(action: tap) {
            VStack(spacing: 2) {
                Text(source ? "From" : d?.formatted(.dateTime.weekday(.abbreviated)) ?? "").font(.footnote.weight(.semibold))
                Text(d?.formatted(.dateTime.day()) ?? "").font(.number(.title3))
            }
            .foregroundStyle(on ? Theme.selectInk : source ? Theme.muted : Theme.text)
            .frame(maxWidth: .infinity, minHeight: 64)
            .background(RoundedRectangle(cornerRadius: 16, style: .continuous).fill(on ? Theme.select : source ? Theme.raised : Theme.panel))
            .overlay(RoundedRectangle(cornerRadius: 16, style: .continuous).strokeBorder(on ? Theme.ring : .clear, lineWidth: 2))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .disabled(source)
        .accessibilityLabel((d?.formatted(.dateTime.weekday(.wide).day().month(.wide)) ?? date) + (source ? ", the day being copied" : ""))
        .accessibilityAddTraits(on ? .isSelected : [])
    }
}
