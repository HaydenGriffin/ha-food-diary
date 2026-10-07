import AuthenticationServices
import SwiftUI

/// Sign in with a Home Assistant login (Home Assistant's own sign-in page; nothing to copy or paste).
struct SignInView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.webAuthenticationSession) private var webAuth
    @State private var server = (AppConfig.shared.string(forKey: "server") ?? AppConfig.defaultServer.absoluteString)
    @State private var busy = false
    @State private var error: String?
    @State private var showServer = false
    @ScaledMetric(relativeTo: .largeTitle) private var titleSize: CGFloat = 48

    var body: some View {
        VStack(spacing: 28) {
            Spacer()
            OliveSprig().fill(Theme.ring).frame(width: 74, height: 120).rotationEffect(.degrees(-12)).accessibilityHidden(true)
            VStack(spacing: 10) {
                Text("Food").font(.system(size: titleSize, weight: .regular, design: .serif)).foregroundStyle(Theme.text).accessibilityAddTraits(.isHeader)
                Text("Your food diary, kept in Home Assistant.").font(.title3).foregroundStyle(Theme.muted).multilineTextAlignment(.center)
            }
            Spacer()
            if let error { Text(error).font(.body).foregroundStyle(Theme.alert).multilineTextAlignment(.center) }
            BigButton(title: busy ? "Signing in…" : "Sign in with Home Assistant", icon: "house", lead: true, busy: busy) { Task { await signIn() } }
            DisclosureGroup("Home Assistant address", isExpanded: $showServer) {
                TextField("https://…", text: $server).textFieldStyle(.roundedBorder).keyboardType(.URL).textInputAutocapitalization(.never)
                    .autocorrectionDisabled().padding(.top, 8)
            }
            .tint(Theme.accent)
            .font(.body).foregroundStyle(Theme.muted)
        }
        .padding(28)
        .background(PatternedPage())
    }

    private func signIn() async {
        guard let url = URL(string: server.trimmingCharacters(in: .whitespaces)), url.scheme?.hasPrefix("http") == true else {
            error = "That address doesn't look right. It should start with http:// or https://."; return
        }
        busy = true; error = nil
        defer { busy = false }
        AppConfig.shared.set(url.absoluteString, forKey: "server")
        do {
            #if DEBUG
            if ProcessInfo.processInfo.arguments.contains("-mockSignIn") {  // simulator tests against tools/mock_ha.py
                try await HAClient.shared.finishSignIn(server: url, code: "mock")
                await model.start(); return
            }
            #endif
            let state = UUID().uuidString
            let callback = try await webAuth.authenticate(using: await HAClient.shared.authorizeURL(server: url, state: state),
                                                          callbackURLScheme: AppConfig.redirectScheme, preferredBrowserSession: .ephemeral)
            let items = URLComponents(url: callback, resolvingAgainstBaseURL: false)?.queryItems ?? []
            guard items.first(where: { $0.name == "state" })?.value == state, let code = items.first(where: { $0.name == "code" })?.value else {
                error = "Signing in didn't finish. Try again."; return
            }
            try await HAClient.shared.finishSignIn(server: url, code: code)
            // straight to Today; Apple Health is asked from there, when they tap Connect (asking while the sign-in sheet
            // is still closing can leave iOS's Health sheet waiting forever)
            await model.start()
        } catch let e as ASWebAuthenticationSessionError where e.code == .canceledLogin {
            return
        } catch {
            self.error = error.localizedDescription
        }
    }
}
