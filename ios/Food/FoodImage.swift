import PhotosUI
import SwiftUI

/// A food's picture: the user's own photo or the product's (whichever Home Assistant has), else a drawn tile.
struct FoodImage: View {
    var path: String?
    var name: String
    var corner: CGFloat = 14
    var full = false   // a big picture (the entry sheet): sharper than the list's thumbnail
    @State private var image: UIImage?

    var body: some View {
        Color.clear
            .overlay {
                if let image { Image(uiImage: image).resizable().scaledToFill() } else { FoodTile(name: name) }
            }
            .clipShape(RoundedRectangle(cornerRadius: corner, style: .continuous))
            .task(id: path) {
                guard path != nil else { image = nil; return }
                if let hit = await ImageStore.shared.cached(path, full: full) { image = hit; return }  // already here: no fade
                // a big picture starts as the list's copy, so a slow or failed sharper load never leaves a drawn tile
                if full, let thumb = await ImageStore.shared.cached(path) { image = thumb }
                var loaded = await ImageStore.shared.image(path, full: full)
                if loaded == nil, !Task.isCancelled {  // one more go: the sheet often opens just as the phone wakes its connection
                    try? await Task.sleep(for: .seconds(1))
                    loaded = await ImageStore.shared.image(path, full: full)
                }
                if loaded == nil, full { loaded = await ImageStore.shared.image(path) }
                if let loaded { withAnimation(.easeOut(duration: 0.2)) { image = loaded } }
            }
            .accessibilityHidden(true)
    }
}

/// Pictures by address, kept in memory once shrunk (the URL cache keeps the downloads); the same picture asked for by many
/// rows at once is fetched once.
actor ImageStore {
    static let shared = ImageStore()
    private let cache: NSCache<NSString, UIImage> = { let c = NSCache<NSString, UIImage>(); c.totalCostLimit = 60_000_000; return c }()
    private var loading: [String: Task<UIImage?, Never>] = [:]

    private func key(_ path: String, _ full: Bool) -> NSString { (full ? path + "#full" : path) as NSString }

    func cached(_ path: String?, full: Bool = false) -> UIImage? {
        guard let path, !path.isEmpty else { return nil }
        return cache.object(forKey: key(path, full))
    }

    func image(_ path: String?, full: Bool = false) async -> UIImage? {
        guard let path, !path.isEmpty else { return nil }
        let k = key(path, full)
        if let hit = cache.object(forKey: k) { return hit }
        if let running = loading[k as String] { return await running.value }
        let task = Task<UIImage?, Never> {
            guard let data = try? await HAClient.shared.imageData(path), let original = UIImage(data: data) else { return nil }
            let side: CGFloat = full ? 1200 : 480
            let s = side / max(1, min(original.size.width, original.size.height))
            return s < 1 ? (await original.byPreparingThumbnail(ofSize: CGSize(width: original.size.width * s, height: original.size.height * s)) ?? original) : original
        }
        loading[k as String] = task
        let img = await task.value
        loading[k as String] = nil
        if let img { cache.setObject(img, forKey: k, cost: Int(img.size.width * img.size.height * img.scale * img.scale * 4)) }
        return img
    }

    func forget(_ path: String) { cache.removeObject(forKey: path as NSString); cache.removeObject(forKey: (path + "#full") as NSString) }
}

/// No picture: a soft tile with a drawn hint of what it is (a cup for drinks, a leaf for salads…).
struct FoodTile: View {
    var name: String

    var body: some View {
        let tones = [Theme.accentSoft, Theme.raised, Theme.select.opacity(0.55)]
        let pick = name.lowercased().unicodeScalars.reduce(0) { $0 + Int($1.value) } % tones.count  // the same tone every time
        ZStack {
            tones[pick]
            Image(systemName: Self.symbol(for: name)).font(.system(size: 22, weight: .semibold)).foregroundStyle(Theme.accent.opacity(0.75))
        }
    }

    /// The kind of food from its words: a word that starts with a hint ("eggs" for "egg"); short hints that are the start of
    /// other words ("tea" in "steak", "gin" in "ginger", "egg" in "veggie") must be the whole word.
    static func symbol(for name: String) -> String {
        let words = name.lowercased().split { !$0.isLetter }.map(String.init)
        let whole: Set<String> = ["tea", "gin", "egg", "cod"]
        func has(_ hint: String) -> Bool {
            if hint.contains(" ") { return name.lowercased().contains(hint) }
            return words.contains { whole.contains(hint) ? ($0 == hint || $0 == hint + "s") : $0.hasPrefix(hint) }
        }
        let kinds: [(String, [String])] = [
            ("cup.and.saucer.fill", ["coffee", "tea", "latte", "cappuccino", "espresso", "flat white", "americano"]),
            ("wineglass.fill", ["wine", "beer", "prosecco", "gin", "cocktail"]),
            ("waterbottle.fill", ["juice", "smoothie", "milk", "shake", "water"]),
            ("birthday.cake.fill", ["cake", "biscuit", "cookie", "digestive", "chocolate", "haribo", "sweet", "pastry", "danish", "dessert",
                                    "brownie", "ice cream", "tiramisu", "muffin"]),
            ("popcorn.fill", ["popcorn", "crisps", "chips", "nuts", "pretzel"]),
            ("fish.fill", ["fish", "salmon", "tuna", "cod", "prawn", "shrimp"]),
            ("frying.pan.fill", ["egg", "omelette", "pancake", "fried", "scrambled"]),
            ("leaf.fill", ["salad", "greens", "spinach", "broccoli", "veg", "kale", "avocado", "quinoa", "poke"]),
            ("takeoutbag.and.cup.and.straw.fill", ["burger", "wrap", "sandwich", "kebab", "pizza", "takeaway", "fries", "toast", "bagel"]),
            ("carrot.fill", ["carrot", "soup", "stew", "potato"]),
        ]
        return kinds.first { $0.1.contains(where: has) }?.0 ?? "fork.knife"
    }
}

/// "Take a photo" or "Choose from your photos", for a food or a recipe; hands back a JPEG small enough to send.
struct ChoosePhoto: ViewModifier {
    @Binding var isPresented: Bool
    var maxBytes: Int
    var picked: (Data) -> Void
    @State private var camera = false
    @State private var library = false
    @State private var item: PhotosPickerItem?

    func body(content: Content) -> some View {
        content
            .confirmationDialog("Photo", isPresented: $isPresented, titleVisibility: .hidden) {
                Button("Take a photo") {
                    #if DEBUG
                    // simulator tests: -testImage <path> stands in for the camera
                    let args = ProcessInfo.processInfo.arguments
                    if let i = args.firstIndex(of: "-testImage"), i + 1 < args.count, let img = UIImage(contentsOfFile: args[i + 1]), let d = img.jpeg(maxBytes: maxBytes) {
                        picked(d); return
                    }
                    #endif
                    camera = true
                }
                Button("Choose from your photos") { library = true }
                Button("Cancel", role: .cancel) {}
            }
            .photosPicker(isPresented: $library, selection: $item, matching: .images)
            .fullScreenCover(isPresented: $camera) {
                CameraPicker { img in camera = false; if let img, let d = img.jpeg(maxBytes: maxBytes) { picked(d) } }.ignoresSafeArea()
            }
            .onChange(of: item) { _, it in
                guard let it else { return }
                Task {
                    if let data = try? await it.loadTransferable(type: Data.self), let img = UIImage(data: data), let d = img.jpeg(maxBytes: maxBytes) { picked(d) }
                    item = nil
                }
            }
    }
}

extension View {
    func choosePhoto(isPresented: Binding<Bool>, maxBytes: Int = 300_000, picked: @escaping (Data) -> Void) -> some View {
        modifier(ChoosePhoto(isPresented: isPresented, maxBytes: maxBytes, picked: picked))
    }
}

extension UIImage {
    /// A JPEG at most `maxBytes`, shrinking and lowering quality as needed (food photos stay sharp at 1200 px).
    func jpeg(maxBytes: Int) -> Data? {
        var side: CGFloat = 1200, quality: CGFloat = 0.8
        while side >= 320 {
            let k = min(1, side / max(size.width, size.height))
            let target = CGSize(width: size.width * k, height: size.height * k)
            let f = UIGraphicsImageRendererFormat(); f.scale = 1
            let img = UIGraphicsImageRenderer(size: target, format: f).image { _ in draw(in: CGRect(origin: .zero, size: target)) }
            for q in stride(from: quality, through: 0.45, by: -0.1) {
                if let d = img.jpegData(compressionQuality: q), d.count <= maxBytes { return d }
            }
            side *= 0.8
        }
        return nil
    }
}
