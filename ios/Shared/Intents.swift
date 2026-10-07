import AppIntents
import Foundation

/// Screens Siri, widgets and the Action button can open the app on.
enum FoodScreen: String, AppEnum {
    case add, scan, photo, label, type
    static var typeDisplayRepresentation: TypeDisplayRepresentation = "Screen"
    static var caseDisplayRepresentations: [FoodScreen: DisplayRepresentation] = [
        .add: "Add food", .scan: "Scan a barcode", .photo: "Photo of a meal", .label: "Photo of a label", .type: "Type what I ate",
    ]
}

struct OpenFoodIntent: AppIntent {
    static var title: LocalizedStringResource = "Open Food"
    static var description = IntentDescription("Open the Food app ready to log something.")
    static var openAppWhenRun = true

    @Parameter(title: "Screen", default: .add) var screen: FoodScreen

    init() {}
    init(screen: FoodScreen) { self.screen = screen }

    @MainActor
    func perform() async throws -> some IntentResult {
        let route = Route(rawValue: screen.rawValue) ?? .add
        Route.setPending(route)
        NotificationCenter.default.post(name: .foodRoute, object: route.rawValue)
        return .result()
    }
}

struct LogFoodIntent: AppIntent {
    static var title: LocalizedStringResource = "Log food"
    static var description = IntentDescription("Say what you ate. Home Assistant works out the calories and adds it to your food diary.")

    @Parameter(title: "Food", requestValueDialog: "What did you eat?") var food: String

    func perform() async throws -> some IntentResult & ProvidesDialog & ReturnsValue<Int> {
        let e = try await FoodAPI.estimate(["kind": "text", "text": food])
        var d = e.values.dict
        d.merge(["name": e.name, "source": "text", "meal": Meal.now.rawValue]) { _, b in b }
        if let n = e.note, !n.isEmpty { d["note"] = n }
        let r = try await FoodAPI.log(d)
        let left = (try? await FoodAPI.day(r.date))?.left
        let tail = left.map { $0 >= 0 ? " \(Fmt.kcal($0)) left today." : " That's \(Fmt.kcal(-$0)) over today." } ?? ""
        return .result(value: Int(e.values.kcal.rounded()), dialog: "Logged \(e.name), \(Fmt.kcal(e.values.kcal)) calories.\(tail)")
    }
}

struct CaloriesLeftIntent: AppIntent {
    static var title: LocalizedStringResource = "Calories left today"
    static var description = IntentDescription("How many calories you've had today and how many are left.")

    func perform() async throws -> some IntentResult & ProvidesDialog & ReturnsValue<Int> {
        let d = try await FoodAPI.day(Date().ymd)
        let line = d.left >= 0 ? "\(Fmt.kcal(d.left)) left" : "\(Fmt.kcal(-d.left)) over"
        return .result(value: Int(d.left.rounded()), dialog: "You've had \(Fmt.kcal(d.totals.kcal)) of \(Fmt.kcal(d.goals.kcal)) calories today: \(line).")
    }
}

/// A widget's "+" next to something eaten before: logs one more, as it was.
struct LogAgainIntent: AppIntent {
    static var title: LocalizedStringResource = "Log again"
    static var isDiscoverable = false

    @Parameter(title: "Food") var name: String

    init() {}
    init(name: String) { self.name = name }

    func perform() async throws -> some IntentResult {
        guard let f = try await FoodAPI.recent().first(where: { $0.name.lowercased() == name.lowercased() }) else { return .result() }
        _ = try await FoodAPI.logAgain(f)
        return .result()
    }
}

extension Notification.Name {
    static let foodRoute = Notification.Name("foodRoute")
}
