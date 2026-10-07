import SwiftUI

/// Daily goals (saved on Done), Apple Health and the optional activity webhook, reminders, and the Home Assistant signed in to.
struct SettingsView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    @State private var kcal = ""
    @State private var protein = ""
    @State private var carbs = ""
    @State private var fat = ""
    @State private var start: Goals?
    @State private var server = ""
    @State private var healthSent: Date?
    @State private var sending = false
    @State private var saving = false
    @State private var confirmSignOut = false
    @State private var remind: [Meal: Bool] = Dictionary(uniqueKeysWithValues: [Meal.breakfast, .lunch, .dinner].map { ($0, Reminders.shared.isOn($0)) })
    @State private var cheers = Reminders.shared.celebrate
    @State private var proteinNudge = ProteinNudge.isOn
    @State private var webhook = AppConfig.activityWebhook ?? ""
    @FocusState private var focus: Bool

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    goal("Calories", "kcal", $kcal); goal("Protein", "g", $protein); goal("Carbs", "g", $carbs); goal("Fat", "g", $fat)
                } header: { header("Daily goals") } footer: {
                    if let bad = invalid { Text(bad).foregroundStyle(Theme.alert) } else { Text("Saved when you tap Done.").foregroundStyle(Theme.muted) }
                }
                .listRowBackground(Theme.panel)
                Section {
                    if HealthSync.shared.asked && HealthSync.shared.foodAllowed {
                        Label("Food goes to Apple Health; your rings, steps and sleep show beside it.", systemImage: "heart.fill").foregroundStyle(Theme.text)
                    } else if HealthSync.shared.asked {
                        Label("Food isn't going to Apple Health.", systemImage: "heart.slash").foregroundStyle(Theme.text)
                        Button("Open the Health app") { if let u = URL(string: "x-apple-health://") { UIApplication.shared.open(u) } }
                    } else {
                        Button("Connect Apple Health") { Task { await model.connectHealth() } }
                    }
                } header: { header("Apple Health") } footer: {
                    Text("Change what's shared in the Health app: Sharing → Apps → Food.").foregroundStyle(Theme.muted)
                }
                .listRowBackground(Theme.panel)
                Section {
                    ForEach([Meal.breakfast, .lunch, .dinner]) { meal in
                        Toggle(isOn: Binding(get: { remind[meal] ?? false }, set: { on in remind[meal] = on; Task { await setReminder(meal, on) } })) {
                            helper("\(meal.single) reminder", "At \(hourText(Reminders.times[meal] ?? 12)), only if \(meal.single.lowercased()) isn't in yet. Your usual is one tap away.")
                        }
                    }
                    Toggle(isOn: Binding(get: { proteinNudge }, set: { on in proteinNudge = on; Task { await setProtein(on) } })) {
                        helper("Protein nudge", "At 7:30 pm, only if you're 15 g or more short and have room. Something you often have is one tap away.")
                    }
                    Toggle(isOn: Binding(get: { cheers }, set: { on in cheers = on; Task { await setCheers(on) } })) {
                        helper("Celebrate good days", "A cheer the morning after a day on target.")
                    }
                } header: { header("Reminders") }
                .tint(Theme.ring)
                .listRowBackground(Theme.panel)
                Section {
                    @Bindable var m = model
                    Toggle(isOn: $m.exerciseCounts) { helper("Count exercise", HealthSync.shared.asked ? "A long walk gives the day more room on your sprig. Your goal and numbers don't change." : "Connect Apple Health first.") }
                        .disabled(!HealthSync.shared.asked)
                } header: { header("Your sprig") }
                .tint(Theme.ring)
                .listRowBackground(Theme.panel)
                Section {
                    TextField("Webhook id", text: $webhook).textInputAutocapitalization(.never).autocorrectionDisabled()
                        .onSubmit { saveWebhook() }
                        .accessibilityLabel("Activity webhook id")
                    if AppConfig.activityWebhook != nil {
                        if let healthSent { Text("Last sent \(healthSent.formatted(.relative(presentation: .named)))").foregroundStyle(Theme.muted) }
                        Button { Task { await sendNow() } } label: {
                            HStack { Text(sending ? "Sending…" : "Send now"); if sending { Spacer(); ProgressView() } }
                        }
                        .disabled(sending || !HealthSync.shared.asked)
                    }
                } header: { header("Send activity to Home Assistant") } footer: {
                    Text("Optional. Steps, activity, rings and sleep from Apple Health are posted to /api/webhook/<id> on your Home Assistant whenever Health has new data. Leave empty to keep them on the phone.")
                        .foregroundStyle(Theme.muted)
                }
                .listRowBackground(Theme.panel)
                Section {
                    LabeledContent("Server") { Text(server).foregroundStyle(Theme.muted) }
                    Button("Sign out…") { confirmSignOut = true }.foregroundStyle(Theme.alert)
                } header: { header("Home Assistant") }
                .listRowBackground(Theme.panel)
                Section {
                    Text("Food \(Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "") (\(Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String ?? ""))").foregroundStyle(Theme.muted)
                }
                .listRowBackground(Theme.panel)
            }
            .tint(Theme.accent)
            .scrollContentBackground(.hidden).background(PatternedPage())
            .scrollDismissesKeyboard(.interactively)
            .keyboardDone($focus)
            .navigationTitle("Settings").navigationBarTitleDisplayMode(.inline)
            .toolbar {
                if changed { ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } } }
                ToolbarItem(placement: .confirmationAction) {
                    Button { Task { await done() } } label: { if saving { ProgressView() } else { Text("Done").fontWeight(.semibold) } }
                        .disabled(saving || start == nil || invalid != nil)
                }
            }
            .confirmationDialog("Sign out of Home Assistant?", isPresented: $confirmSignOut, titleVisibility: .visible) {
                Button("Sign out", role: .destructive) { Task { await model.signOut() } }
                Button("Cancel", role: .cancel) {}
            } message: { Text("Your diary stays in Home Assistant. To sign back in you'll need your Home Assistant password.") }
            .task { await load() }
        }
        .toastHost(bottom: 16)
        .interactiveDismissDisabled(changed)
    }

    private func header(_ s: String) -> some View { Text(s).font(.subheadline.weight(.semibold)).foregroundStyle(Theme.muted).textCase(nil) }

    private func helper(_ title: String, _ detail: String) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(title).foregroundStyle(Theme.text)
            Text(detail).font(.subheadline).foregroundStyle(Theme.muted).fixedSize(horizontal: false, vertical: true)
        }
        .padding(.vertical, 2)
    }

    private func setReminder(_ meal: Meal, _ on: Bool) async {
        if await Reminders.shared.set(meal, on) {
            model.remindersAnswered()
        } else {
            remind[meal] = false
            model.toast = Toast(text: "Notifications are off for Food. Turn them on in iPhone Settings → Food.", error: true)
        }
    }

    private func setProtein(_ on: Bool) async {
        if await Reminders.shared.setProtein(on) { model.remindersAnswered() } else {
            proteinNudge = false
            model.toast = Toast(text: "Notifications are off for Food. Turn them on in iPhone Settings → Food.", error: true)
        }
    }

    private func setCheers(_ on: Bool) async {
        if await Reminders.shared.setCelebrate(on) { model.remindersAnswered() } else {
            cheers = false
            model.toast = Toast(text: "Notifications are off for Food. Turn them on in iPhone Settings → Food.", error: true)
        }
    }

    private func hourText(_ h: Int) -> String { h == 12 ? "noon" : "\(h > 12 ? h - 12 : h) \(h < 12 ? "am" : "pm")" }

    private func goal(_ name: String, _ unit: String, _ b: Binding<String>) -> some View {
        HStack {
            Text(name).foregroundStyle(Theme.text)
            Spacer()
            TextField("0", text: b).keyboardType(.decimalPad).multilineTextAlignment(.trailing).font(.number(.body)).frame(minWidth: 80)
                .focused($focus).disabled(start == nil).accessibilityLabel("\(name) goal in \(unit)")
            Text(unit).foregroundStyle(Theme.muted).accessibilityHidden(true)
        }
        .frame(minHeight: 44)
    }

    private var values: [String: Double?] {
        ["kcal": Fmt.parse(kcal), "protein_g": Fmt.parse(protein), "carbs_g": Fmt.parse(carbs), "fat_g": Fmt.parse(fat)]
    }

    /// A blank or zero goal is never saved (it would read as "no goal at all").
    private var invalid: String? {
        guard start != nil else { return nil }
        let names = ["kcal": "Calories", "protein_g": "Protein", "carbs_g": "Carbs", "fat_g": "Fat"]
        let bad = ["kcal", "protein_g", "carbs_g", "fat_g"].filter { (values[$0] ?? nil).map { $0 <= 0 } ?? true }
        return bad.isEmpty ? nil : "\(bad.map { names[$0]! }.joined(separator: ", ")) need\(bad.count == 1 ? "s" : "") a number above 0."
    }

    /// Only what they changed goes to Home Assistant.
    private var edits: [String: Double] {
        guard let g = start else { return [:] }
        let was = ["kcal": g.kcal, "protein_g": g.protein_g, "carbs_g": g.carbs_g, "fat_g": g.fat_g]
        var out: [String: Double] = [:]
        for (k, v) in values { if let v, v > 0, Int(v.rounded()) != Int((was[k] ?? 0).rounded()) { out[k] = v } }
        return out
    }
    private var changed: Bool { !edits.isEmpty }

    private func load() async {
        server = await HAClient.shared.server?.host() ?? ""
        healthSent = AppConfig.shared.object(forKey: "healthSent") as? Date
        let g: Goals? = if let d = model.day { d.goals } else { try? await FoodAPI.day(Date().ymd).goals }
        guard let g else { model.toast = Toast(text: "Couldn't load your goals. Try again in a moment.", error: true); return }
        let r = { (v: Double) in "\(Int(v.rounded()))" }
        kcal = r(g.kcal); protein = r(g.protein_g); carbs = r(g.carbs_g); fat = r(g.fat_g)
        start = g
    }

    private func saveWebhook() {
        let new = webhook.trimmingCharacters(in: .whitespacesAndNewlines)
        guard new != (AppConfig.activityWebhook ?? "") else { return }
        AppConfig.activityWebhook = new.isEmpty ? nil : new
        if !new.isEmpty && HealthSync.shared.asked { Task { await sendNow() } }
    }

    private func done() async {
        saveWebhook()
        guard changed else { dismiss(); return }
        saving = true; defer { saving = false }
        if await model.setGoals(edits) { dismiss() }
    }

    private func sendNow() async {
        sending = true; defer { sending = false }
        if await HealthSync.shared.sendActivity() {
            healthSent = AppConfig.shared.object(forKey: "healthSent") as? Date
            model.toast = Toast(text: "Sent to Home Assistant")
        } else {
            model.toast = Toast(text: "Couldn't send it. It'll try again by itself.", error: true)
        }
    }
}
