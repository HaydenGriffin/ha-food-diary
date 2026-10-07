import SwiftUI
import UIKit

/// Share to Food from anywhere: a photo is logged (a meal with an optional note, or a label with how much).
final class ShareViewController: UIViewController {
    override func viewDidLoad() {
        super.viewDidLoad()
        let share = ShareModel(context: extensionContext)
        let host = UIHostingController(rootView: ShareView(share: share))
        addChild(host)
        host.view.frame = view.bounds
        host.view.autoresizingMask = [.flexibleWidth, .flexibleHeight]
        view.addSubview(host.view)
        host.didMove(toParent: self)
        Task { await share.load() }
    }
}

struct ShareView: View {
    let share: ShareModel

    var body: some View {
        switch share.content {
        case .loading:
            PatternedPage()
        case .photo(let photo):
            PhotoLogView(share: share, photo: photo)
        case .signedOut:
            NoticeScene(share: share, title: "Sign in to Food first", detail: "Open the Food app, sign in, then share it again.")
        case .nothing:
            NoticeScene(share: share, title: "Nothing to save here", detail: "Share a photo of a meal or a nutrition label.")
        }
    }
}
