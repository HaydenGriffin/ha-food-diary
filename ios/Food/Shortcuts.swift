import AppIntents

/// Siri and Shortcuts phrases ("Log food in Food", "Calories left in Food", "Scan food in Food").
struct FoodShortcuts: AppShortcutsProvider {
    static var appShortcuts: [AppShortcut] {
        AppShortcut(intent: LogFoodIntent(), phrases: ["Log food in \(.applicationName)", "Log a meal in \(.applicationName)"],
                    shortTitle: "Log food", systemImageName: "fork.knife")
        AppShortcut(intent: CaloriesLeftIntent(), phrases: ["How many calories have I got left in \(.applicationName)", "Calories left in \(.applicationName)"],
                    shortTitle: "Calories left", systemImageName: "flame")
        AppShortcut(intent: OpenFoodIntent(screen: .scan), phrases: ["Scan food in \(.applicationName)", "Scan a barcode in \(.applicationName)"],
                    shortTitle: "Scan a barcode", systemImageName: "barcode.viewfinder")
        AppShortcut(intent: OpenFoodIntent(screen: .photo), phrases: ["Take a meal photo in \(.applicationName)", "Photograph my meal in \(.applicationName)"],
                    shortTitle: "Photo of a meal", systemImageName: "camera")
    }
}
