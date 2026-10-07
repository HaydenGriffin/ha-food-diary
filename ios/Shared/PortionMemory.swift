import Foundation

/// How much they usually have of a food: the last portions they picked for it (when not one), so having it again starts there.
/// Label and barcode foods remember their grams on Home Assistant already.
enum PortionMemory {
    private static let key = "portionMemory"

    static func portions(for name: String) -> Double {
        (AppConfig.shared.dictionary(forKey: key)?[name.lowercased()] as? Double) ?? 1
    }

    static func remember(_ portions: Double, for name: String) {
        var all = AppConfig.shared.dictionary(forKey: key) ?? [:]
        all[name.lowercased()] = portions == 1 ? nil : portions
        if all.count > 300 { all = Dictionary(uniqueKeysWithValues: all.prefix(300).map { ($0.key, $0.value) }) }
        AppConfig.shared.set(all, forKey: key)
    }
}
