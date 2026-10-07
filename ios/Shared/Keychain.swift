import Foundation
import Security

/// The Home Assistant session, shared by the app, its widgets and its share extension (one keychain group).
struct HASession: Codable, Equatable {
    var server: URL
    var accessToken: String
    var refreshToken: String
    var expires: Date
}

enum Keychain {
    private static let account = "home-assistant-session"

    private static func query(group: Bool) -> [String: Any] {
        var q: [String: Any] = [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: AppConfig.keychainService,
                                kSecAttrAccount as String: account]
        if group { q[kSecAttrAccessGroup as String] = AppConfig.keychainGroup }
        return q
    }

    static func save(_ session: HASession) {
        guard let data = try? JSONEncoder().encode(session) else { return }
        for group in [true, false] {  // without the group only where entitlements aren't signed in (simulator builds)
            SecItemDelete(query(group: group) as CFDictionary)
            var add = query(group: group)
            add[kSecValueData as String] = data
            add[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlock
            if SecItemAdd(add as CFDictionary, nil) == errSecSuccess { return }
        }
    }

    static func load() -> HASession? {
        for group in [true, false] {
            var q = query(group: group)
            q[kSecReturnData as String] = true
            q[kSecMatchLimit as String] = kSecMatchLimitOne
            var out: AnyObject?
            if SecItemCopyMatching(q as CFDictionary, &out) == errSecSuccess, let data = out as? Data,
               let s = try? JSONDecoder().decode(HASession.self, from: data) { return s }
        }
        return nil
    }

    static func clear() {
        for group in [true, false] { SecItemDelete(query(group: group) as CFDictionary) }
    }
}
