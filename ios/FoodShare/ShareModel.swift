import ImageIO
import SwiftUI
import UniformTypeIdentifiers

/// What was shared, and closing the share sheet when it's done.
@Observable @MainActor
final class ShareModel {
    enum Content {
        case loading, photo(PhotoLog), signedOut, nothing
    }

    weak var context: NSExtensionContext?
    var content = Content.loading

    init(context: NSExtensionContext?) { self.context = context }

    func load() async {
        guard await HAClient.shared.signedIn else { content = .signedOut; return }
        let attachments = ((context?.inputItems as? [NSExtensionItem]) ?? []).flatMap { $0.attachments ?? [] }
        if let p = attachments.first(where: { $0.hasItemConformingToTypeIdentifier(UTType.image.identifier) }) {
            content = await Self.image(p).map { .photo(PhotoLog(image: $0)) } ?? .nothing
        } else {
            content = .nothing
        }
    }

    func finish() { context?.completeRequest(returningItems: nil) }

    func cancel() { context?.cancelRequest(withError: NSError(domain: "food", code: 0)) }

    /// The shared photo, read straight to a ~1600 px picture: a 48 MP original decoded in full would take more memory than
    /// a share extension is allowed.
    private static func image(_ p: NSItemProvider) async -> UIImage? {
        let data = try? await p.loadItem(forTypeIdentifier: UTType.image.identifier)
        let source: CGImageSource? = if let url = data as? URL { CGImageSourceCreateWithURL(url as CFURL, nil) }
                                     else if let d = data as? Data { CGImageSourceCreateWithData(d as CFData, nil) } else { nil }
        if let source {
            let opts: [CFString: Any] = [kCGImageSourceCreateThumbnailFromImageAlways: true, kCGImageSourceCreateThumbnailWithTransform: true,
                                         kCGImageSourceThumbnailMaxPixelSize: 1600]
            if let cg = CGImageSourceCreateThumbnailAtIndex(source, 0, opts as CFDictionary) { return UIImage(cgImage: cg) }
        }
        return data as? UIImage
    }
}
