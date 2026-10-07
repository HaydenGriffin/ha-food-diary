import Foundation

/// Talks to Home Assistant as the signed-in user (OAuth), so the food diary's actions answer their own diary.
actor HAClient {
    static let shared = HAClient()

    enum Failure: LocalizedError {
        case signedOut, server(String), network(String)
        /// The request never left the phone (no signal, or Home Assistant couldn't be reached at all): safe to send again later.
        case offline(String)
        var errorDescription: String? {
            switch self {
            case .signedOut: return "Sign in to Home Assistant first."
            case .server(let m), .network(let m), .offline(let m): return m
            }
        }
    }

    private var session: HASession? = Keychain.load()
    private var refreshing: Task<HASession, Error>?

    var signedIn: Bool { session != nil }
    var server: URL? { session?.server }

    // ---------- signing in ----------

    func authorizeURL(server: URL, state: String) -> URL {
        var c = URLComponents(url: server.appendingPathComponent("auth/authorize"), resolvingAgainstBaseURL: false)!
        c.queryItems = [.init(name: "response_type", value: "code"), .init(name: "client_id", value: AppConfig.clientID),
                        .init(name: "redirect_uri", value: AppConfig.redirect), .init(name: "state", value: state)]
        return c.url!
    }

    func finishSignIn(server: URL, code: String) async throws {
        let s = try await tokenRequest(server: server, form: ["grant_type": "authorization_code", "code": code, "client_id": AppConfig.clientID], keepRefresh: nil)
        session = s
        Keychain.save(s)
    }

    func signOut() {
        session = nil
        Keychain.clear()
    }

    private func tokenRequest(server: URL, form: [String: String], keepRefresh: String?) async throws -> HASession {
        var req = URLRequest(url: server.appendingPathComponent("auth/token"))
        req.httpMethod = "POST"
        req.setValue("application/x-www-form-urlencoded", forHTTPHeaderField: "Content-Type")
        req.httpBody = form.map { "\($0.key)=\($0.value.addingPercentEncoding(withAllowedCharacters: .alphanumerics) ?? $0.value)" }
            .joined(separator: "&").data(using: .utf8)
        let (data, resp) = try await fetch(req)
        let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
        let j = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
        guard code == 200, let j, let access = j["access_token"] as? String else {
            // Only Home Assistant saying the login itself is no longer good signs them out; a proxy, a captive portal or a
            // restarting Home Assistant is just "try again".
            if (j?["error"] as? String) == "invalid_grant" { throw Failure.signedOut }
            throw Failure.server(code == 0 ? "Can't reach Home Assistant right now. Try again in a moment." : "Home Assistant is having trouble (\(code)). Try again in a moment.")
        }
        let refresh = (j["refresh_token"] as? String) ?? keepRefresh ?? ""
        let expires = Date().addingTimeInterval((j["expires_in"] as? Double) ?? 1800)
        return HASession(server: server, accessToken: access, refreshToken: refresh, expires: expires)
    }

    /// A valid access token, refreshed when it has under a minute left (once, however many calls ask at the same time), or
    /// straight away when Home Assistant has just turned the current one down.
    private func validSession(force: Bool = false) async throws -> HASession {
        if session == nil { session = Keychain.load() }  // the app may have signed in after this widget or extension started
        if let saved = Keychain.load(), let s = session, saved.refreshToken != s.refreshToken, saved.expires > s.expires { session = saved }  // another process refreshed
        guard let s = session else { throw Failure.signedOut }
        if !force && s.expires.timeIntervalSinceNow > 60 { return s }
        if let r = refreshing { return try await r.value }
        let task = Task { try await tokenRequest(server: s.server, form: ["grant_type": "refresh_token", "refresh_token": s.refreshToken,
                                                                          "client_id": AppConfig.clientID], keepRefresh: s.refreshToken) }
        refreshing = task
        defer { refreshing = nil }
        do {
            let fresh = try await task.value
            session = fresh
            Keychain.save(fresh)
            return fresh
        } catch Failure.signedOut {
            // only clear the keychain if no other process has signed in again or refreshed in the meantime
            if Keychain.load()?.refreshToken == s.refreshToken { signOut() } else { session = Keychain.load() }
            throw Failure.signedOut
        }
    }

    // ---------- calling ----------

    /// A food_diary action; returns its response. Working something out (the AI) may take a while; everything else should
    /// answer quickly, so a Home Assistant that has gone quiet is reported in seconds rather than after a minute and a half.
    func call(_ service: String, _ data: [String: Any] = [:]) async throws -> Data {
        try await self.service("food_diary", service, data, response: true, timeout: service == "estimate" ? 90 : 25)
    }

    /// Any action; with `response`, what it answered. The app only calls food_diary.* through `call`.
    private func service(_ domain: String, _ name: String, _ data: [String: Any] = [:], response: Bool = false, timeout: TimeInterval = 30) async throws -> Data {
        var s = try await validSession()
        var c = URLComponents(url: s.server.appendingPathComponent("api/services/\(domain)/\(name)"), resolvingAgainstBaseURL: false)!
        if response { c.queryItems = [.init(name: "return_response", value: nil)] }
        var req = URLRequest(url: c.url!)
        req.httpMethod = "POST"
        req.timeoutInterval = timeout
        req.setValue("Bearer \(s.accessToken)", forHTTPHeaderField: "Authorization")
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try JSONSerialization.data(withJSONObject: data)
        var (body, resp) = try await fetch(req)
        var code = (resp as? HTTPURLResponse)?.statusCode ?? 0
        #if DEBUG
        if code == 599 { throw Failure.offline("You're offline. Try again when you have signal.") }  // tools/mock_ha.py's /outage
        #endif
        if code == 401 {  // turned down: get a fresh token once and try again before giving up on the login
            s = try await validSession(force: true)
            req.setValue("Bearer \(s.accessToken)", forHTTPHeaderField: "Authorization")
            (body, resp) = try await fetch(req)
            code = (resp as? HTTPURLResponse)?.statusCode ?? 0
            if code == 401 { signOut(); throw Failure.signedOut }
        }
        guard code == 200 else { throw Failure.server(Self.message(body) ?? "Home Assistant had a problem (\(code)). Try again.") }
        guard response else { return Data() }
        guard let j = try JSONSerialization.jsonObject(with: body) as? [String: Any] else { throw Failure.server("Home Assistant answered oddly.") }
        return try JSONSerialization.data(withJSONObject: j["service_response"] ?? [:])
    }

    func call<T: Decodable>(_ service: String, _ data: [String: Any] = [:], as type: T.Type) async throws -> T {
        try JSONDecoder().decode(T.self, from: try await call(service, data))
    }

    /// A picture: Home Assistant's own (their photos need their login; /local ones don't) or a product photo from the web.
    func imageData(_ path: String) async throws -> Data {
        if path.hasPrefix("https://") {
            return try await fetch(URLRequest(url: URL(string: path)!)).0
        }
        let s = try await validSession()
        guard let url = URL(string: path, relativeTo: s.server) else { throw Failure.server("Bad picture address.") }
        var req = URLRequest(url: url)
        if path.hasPrefix("/api/") { req.setValue("Bearer \(s.accessToken)", forHTTPHeaderField: "Authorization") }
        let (data, resp) = try await fetch(req)
        guard (resp as? HTTPURLResponse)?.statusCode == 200 else { throw Failure.server("No picture.") }
        return data
    }

    /// A webhook (Apple Health activity → Home Assistant). Webhooks need no login; their id is the secret.
    func webhook(_ id: String, _ data: [String: Any]) async throws {
        guard let server = session?.server ?? Keychain.load()?.server else { throw Failure.signedOut }
        var req = URLRequest(url: server.appendingPathComponent("api/webhook/\(id)"))
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try JSONSerialization.data(withJSONObject: data)
        let (_, resp) = try await fetch(req)
        guard (200..<300).contains((resp as? HTTPURLResponse)?.statusCode ?? 0) else { throw Failure.server("Home Assistant didn't answer. Try again in a moment.") }
    }

    private func fetch(_ req: URLRequest) async throws -> (Data, URLResponse) {
        do { return try await URLSession.shared.data(for: req) } catch let e as URLError {
            switch e.code {
            case .cancelled: throw CancellationError()
            case .timedOut: throw Failure.network("Home Assistant is taking too long to answer. Try again in a moment.")
            case .notConnectedToInternet, .dataNotAllowed, .internationalRoamingOff, .callIsActive:
                throw Failure.offline("You're offline. Try again when you have signal.")
            case .cannotConnectToHost, .cannotFindHost, .dnsLookupFailed:
                throw Failure.offline("Can't reach Home Assistant right now. Try again in a moment.")
            default: throw Failure.network("Can't reach Home Assistant right now. Try again in a moment.")
            }
        } catch is CancellationError {
            throw CancellationError()
        } catch {
            throw Failure.network("Can't reach Home Assistant right now. Try again in a moment.")
        }
    }


    private static func message(_ body: Data) -> String? {
        if let j = try? JSONSerialization.jsonObject(with: body) as? [String: Any], let m = j["message"] as? String, !m.isEmpty {
            return m.replacingOccurrences(of: "Validation error: ", with: "")
        }
        let s = String(data: body, encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        return s.isEmpty || s.hasPrefix("<") || s.count > 200 ? nil : s.replacingOccurrences(of: "400: ", with: "")
    }
}
