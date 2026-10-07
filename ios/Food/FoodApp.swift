import BackgroundTasks
import SwiftUI
import UserNotifications
#if canImport(WidgetKit)
import WidgetKit
#endif

final class AppDelegate: NSObject, UIApplicationDelegate, UNUserNotificationCenterDelegate {
    /// The lunch reminder's "Add …" button runs here, in the background.
    func userNotificationCenter(_ center: UNUserNotificationCenter, didReceive response: UNNotificationResponse) async {
        let info = response.notification.request.content.userInfo
        if response.actionIdentifier == ProteinNudge.add {
            await ProteinNudge.addPick(on: info["date"] as? String ?? Date().ymd)
            return
        }
        guard response.actionIdentifier == Reminders.addUsual else { return }
        let date = info["date"] as? String ?? Date().ymd, meal = Meal(rawValue: info["meal"] as? String ?? "") ?? .lunch
        await Reminders.shared.addUsual(meal: meal, on: date)
    }

    func application(_ application: UIApplication, didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]? = nil) -> Bool {
        UNUserNotificationCenter.current().delegate = self
        // Health → Home Assistant keeps working while the app is closed: iOS relaunches it for new samples
        HealthSync.shared.startBackgroundDelivery()
        BGTaskScheduler.shared.register(forTaskWithIdentifier: FoodApp.syncTask, using: nil) { task in
            let work = Task {
                await HealthSync.shared.syncFood()
                await HealthSync.shared.sendActivityThrottled()
                await Reminders.shared.checkDiary()
                if let d = try? await FoodAPI.day(Date().ymd) { Snapshot.save(from: d) }
                WidgetCenter.shared.reloadAllTimelines()
                task.setTaskCompleted(success: true)
            }
            task.expirationHandler = { work.cancel() }
            FoodApp.scheduleSync()
        }
        return true
    }
}

@main
struct FoodApp: App {
    @UIApplicationDelegateAdaptor(AppDelegate.self) var delegate
    @State private var model = AppModel()
    @Environment(\.scenePhase) private var phase

    init() {
        let serif = UIFontDescriptor.preferredFontDescriptor(withTextStyle: .headline).withDesign(.serif) ?? UIFontDescriptor.preferredFontDescriptor(withTextStyle: .headline)
        // the serif title grows with the text size, like everything else
        let title = UIFontMetrics(forTextStyle: .headline).scaledFont(for: UIFont(descriptor: serif.withSymbolicTraits(.traitBold) ?? serif, size: 20), maximumPointSize: 30)
        UINavigationBar.appearance().titleTextAttributes = [.font: title]
    }

    static let syncTask = (Bundle.main.bundleIdentifier ?? "food") + ".sync"

    static func scheduleSync() {
        let r = BGAppRefreshTaskRequest(identifier: syncTask)
        r.earliestBeginDate = Date().addingTimeInterval(30 * 60)
        try? BGTaskScheduler.shared.submit(r)
    }

    var body: some Scene {
        WindowGroup {
            RootView()
                .environment(model)
                .tint(Theme.accent)
                .task {
                    await model.start()
                    #if DEBUG
                    presentTestShareSheet()
                    #endif
                }
                .onOpenURL { url in if let r = Route.from(url) { model.open(r) } }
                .onReceive(NotificationCenter.default.publisher(for: .foodRoute)) { n in
                    if let s = n.object as? String, let r = Route(rawValue: s) { _ = Route.takePending(); model.open(r) }
                }
                .onChange(of: phase) { _, p in
                    guard p == .active else { if p == .background { FoodApp.scheduleSync() }; return }
                    if let r = Route.takePending() { model.open(r) }
                    Task { await model.resumed() }
                }
                .onReceive(NotificationCenter.default.publisher(for: UIApplication.significantTimeChangeNotification)) { _ in model.dayChanged() }
        }
    }
}

struct RootView: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        Group {
            if !model.checked { Theme.page.ignoresSafeArea() }
            else if model.signedIn { TodayView() }
            else { SignInView() }
        }
        .toastHost()  // above the Add bar, never over the toolbar (a stray tap would Undo)
    }
}

#if DEBUG
/// Simulator tests: -shareImage <path> opens the share sheet with that photo, to drive the FoodShare extension.
@MainActor private func presentTestShareSheet() {
    let args = ProcessInfo.processInfo.arguments
    guard let i = args.firstIndex(of: "-shareImage"), i + 1 < args.count, let image = UIImage(contentsOfFile: args[i + 1]),
          let root = (UIApplication.shared.connectedScenes.first as? UIWindowScene)?.keyWindow?.rootViewController else { return }
    root.present(UIActivityViewController(activityItems: [image], applicationActivities: nil), animated: true)
}
#endif
