import Foundation

private extension KeyedDecodingContainer {
    func num(_ k: Key) -> Double { (try? decodeIfPresent(Double.self, forKey: k)) ?? 0 }
    func optNum(_ k: Key) -> Double? { try? decodeIfPresent(Double.self, forKey: k) }
    func str(_ k: Key) -> String? { try? decodeIfPresent(String.self, forKey: k) }
}

struct Nutrients: Codable, Hashable {
    var kcal = 0.0, protein_g = 0.0, carbs_g = 0.0, fat_g = 0.0, fibre_g = 0.0

    init(kcal: Double = 0, protein_g: Double = 0, carbs_g: Double = 0, fat_g: Double = 0, fibre_g: Double = 0) {
        self.kcal = kcal; self.protein_g = protein_g; self.carbs_g = carbs_g; self.fat_g = fat_g; self.fibre_g = fibre_g
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        kcal = c.num(.kcal); protein_g = c.num(.protein_g); carbs_g = c.num(.carbs_g); fat_g = c.num(.fat_g); fibre_g = c.num(.fibre_g)
    }

    func adding(_ o: Nutrients) -> Nutrients {
        Nutrients(kcal: kcal + o.kcal, protein_g: protein_g + o.protein_g, carbs_g: carbs_g + o.carbs_g, fat_g: fat_g + o.fat_g, fibre_g: fibre_g + o.fibre_g)
    }
    func scaled(_ k: Double) -> Nutrients { Nutrients(kcal: kcal * k, protein_g: protein_g * k, carbs_g: carbs_g * k, fat_g: fat_g * k, fibre_g: fibre_g * k) }
    var dict: [String: Any] { ["kcal": kcal, "protein_g": protein_g, "carbs_g": carbs_g, "fat_g": fat_g, "fibre_g": fibre_g] }
}

struct Entry: Decodable, Identifiable, Hashable {
    var id: String
    var at: String?
    var meal: String
    var name: String
    var portions: Double
    var source: String?
    var ref: String?
    var totals: Nutrients
    var perPortion: Nutrients
    var per100: Nutrients?
    var grams: Double?
    var unit: String?
    var note: String?
    var edited: Bool
    var image: String?
    var photo: String?       // the user's own photo, kept by Home Assistant
    var imageURL: String?    // the product's photo
    var barcode: String?

    enum CodingKeys: String, CodingKey { case id, at, meal, name, portions, source, ref, per_portion, per_100, grams, unit, note, edited, image, photo, image_url, barcode }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        at = c.str(.at); meal = c.str(.meal) ?? "snack"; name = c.str(.name) ?? "Something"
        portions = c.optNum(.portions) ?? 1; source = c.str(.source); ref = c.str(.ref)
        totals = try Nutrients(from: decoder)
        perPortion = (try? c.decodeIfPresent(Nutrients.self, forKey: .per_portion)) ?? nil ?? totals.scaled(1 / max(portions, 0.05))
        per100 = try? c.decodeIfPresent(Nutrients.self, forKey: .per_100)
        grams = c.optNum(.grams); unit = c.str(.unit); note = c.str(.note)
        edited = (try? c.decodeIfPresent(Bool.self, forKey: .edited)) ?? nil ?? false
        image = c.str(.image); photo = c.str(.photo); imageURL = c.str(.image_url); barcode = c.str(.barcode)
    }

    var mealValue: Meal { Meal(rawValue: meal) ?? .snack }
    /// Counted already but not eaten yet: put in ahead (logged before its day began, e.g. a second night of leftovers),
    /// with its mealtime still to come.
    func stillPlanned(on ymd: String) -> Bool {
        guard mealValue.usualTime(on: ymd) > Date() else { return false }
        return time.map { $0.ymd < ymd } ?? false
    }
    var time: Date? { Date.fromEntry(at) }
    var amountText: String? {
        if per100 != nil, let g = grams, g > 0 { return "\(Int(g.rounded())) \(unit ?? "g")" }
        return portions == 1 ? nil : "\(Fmt.portions(portions)) \(portions < 1 ? "portion" : "portions")"
    }

    /// Everything needed to put it back exactly as it was on `date` (Undo after Remove): photo, note and all.
    func restorePayload(date: String) -> [String: Any] {
        var d: [String: Any] = ["name": name, "meal": meal, "source": source ?? "manual", "date": date]
        if let p = per100, let g = grams { d["per_100"] = p.dict; d["grams"] = g; d["unit"] = unit ?? "g" }
        else { d.merge(perPortion.dict) { _, b in b }; d["portions"] = portions }
        if edited { d["edited"] = true }
        if let ref, !ref.isEmpty { d["ref"] = ref }
        if let note, !note.isEmpty { d["note"] = note }
        if let barcode, !barcode.isEmpty { d["barcode"] = barcode }
        if let photo, !photo.isEmpty { d["photo"] = photo }
        if let imageURL, !imageURL.isEmpty { d["image_url"] = imageURL }
        return d
    }
}

struct Goals: Decodable, Hashable {
    var kcal = 2000.0, protein_g = 100.0, carbs_g = 230.0, fat_g = 70.0, fibre_g = 30.0
    /// Each meal's optional target range, e.g. breakfast [280, 420].
    var perMeal: [String: [Double]] = [:]
    enum CodingKeys: String, CodingKey { case kcal, protein_g, carbs_g, fat_g, fibre_g, per_meal }
    init() {}
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        kcal = c.optNum(.kcal) ?? 2000; protein_g = c.optNum(.protein_g) ?? 100; carbs_g = c.optNum(.carbs_g) ?? 230
        fat_g = c.optNum(.fat_g) ?? 70; fibre_g = c.optNum(.fibre_g) ?? 30
        perMeal = (try? c.decodeIfPresent([String: [Double]].self, forKey: .per_meal)) ?? nil ?? [:]
    }
    func aim(_ meal: Meal) -> String? {
        guard let r = perMeal[meal.rawValue], r.count == 2 else { return nil }
        return "aim \(Int(r[0]))–\(Int(r[1]))"
    }
}

struct Day: Decodable {
    var date: String
    var entries: [Entry]
    var totals: Nutrients
    var goals: Goals
    var left: Double { goals.kcal - totals.kcal }
    func entries(_ meal: Meal) -> [Entry] { entries.filter { $0.mealValue == meal } }
}

struct HistoryDay: Decodable, Hashable, Identifiable {
    var date: String
    var kcal: Double
    var protein = 0.0, carbs = 0.0, fat = 0.0
    var logged = 0
    var id: String { date }
    enum CodingKeys: String, CodingKey { case date, kcal, protein_g, carbs_g, fat_g, logged }
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        date = try c.decode(String.self, forKey: .date); kcal = c.num(.kcal)
        protein = c.num(.protein_g); carbs = c.num(.carbs_g); fat = c.num(.fat_g)
        logged = Int(c.optNum(.logged) ?? (kcal > 0 ? 1 : 0))
    }
    init(date: String, kcal: Double) { self.date = date; self.kcal = kcal }
}

struct History: Decodable { var days: [HistoryDay]; var goals: Goals }

/// What Home Assistant worked out, before it's logged.
struct Estimate: Decodable {
    var name: String
    var values: Nutrients
    var note: String?
    var foods: [String]
    var source: String?
    var ref: String?
    var per100: Nutrients?
    var grams: Double?
    var unit: String?
    var guessed: Bool
    var servingG: Double?
    var barcode: String?
    var photo: String?       // the meal photo it was worked out from, kept with the entry when logged
    var imageURL: String?    // the product's photo (Open Food Facts)

    enum CodingKeys: String, CodingKey { case name, note, foods, source, ref, per_100, grams, unit, guessed, serving_g, barcode, photo, image_url }
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        name = c.str(.name) ?? "Something"; values = try Nutrients(from: decoder); note = c.str(.note)
        foods = (try? c.decodeIfPresent([String].self, forKey: .foods)) ?? nil ?? []
        source = c.str(.source); ref = c.str(.ref); per100 = try? c.decodeIfPresent(Nutrients.self, forKey: .per_100)
        grams = c.optNum(.grams); unit = c.str(.unit); guessed = (try? c.decodeIfPresent(Bool.self, forKey: .guessed)) ?? nil ?? false
        servingG = c.optNum(.serving_g); barcode = c.str(.barcode); photo = c.str(.photo); imageURL = c.str(.image_url)
    }
}

struct RecentFood: Codable, Hashable, Identifiable {
    var name: String
    var meal: String?
    var values: Nutrients
    var source: String?
    var ref: String?
    var times: Int
    var per100: Nutrients?
    var grams: Double?
    var unit: String?
    var image: String?
    var photo: String?        // the user's photo of it, so it comes along when it's had again
    var imageURL: String?
    var id: String { name.lowercased() }
    /// One more of it, as they had it (grams for label foods).
    var kcalEach: Double { per100 != nil && grams != nil ? per100!.kcal * grams! / 100 : values.kcal }
    var proteinEach: Double { per100 != nil && grams != nil ? per100!.protein_g * grams! / 100 : values.protein_g }

    enum CodingKeys: String, CodingKey { case name, meal, source, ref, times, per_100, grams, unit, image, photo, image_url }
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        name = c.str(.name) ?? "Something"; meal = c.str(.meal); values = try Nutrients(from: decoder); source = c.str(.source); ref = c.str(.ref)
        times = Int(c.optNum(.times) ?? 1); per100 = try? c.decodeIfPresent(Nutrients.self, forKey: .per_100)
        grams = c.optNum(.grams); unit = c.str(.unit); image = c.str(.image)
        photo = c.str(.photo); imageURL = c.str(.image_url)
    }
    func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(name, forKey: .name); try c.encodeIfPresent(meal, forKey: .meal); try c.encodeIfPresent(source, forKey: .source); try c.encodeIfPresent(ref, forKey: .ref)
        try c.encode(times, forKey: .times); try c.encodeIfPresent(per100, forKey: .per_100); try c.encodeIfPresent(grams, forKey: .grams)
        try c.encodeIfPresent(unit, forKey: .unit); try c.encodeIfPresent(image, forKey: .image)
        try c.encodeIfPresent(photo, forKey: .photo); try c.encodeIfPresent(imageURL, forKey: .image_url); try values.encode(to: encoder)
    }
}

enum Fmt {
    static func kcal(_ v: Double) -> String { Int(v.rounded()).formatted() }
    static func g(_ v: Double) -> String { "\(Int(v.rounded())) g" }
    static func portions(_ v: Double) -> String {
        switch v { case 0.5: return "½"; case 1.5: return "1½"; case 2.5: return "2½"
        default: return v == v.rounded() ? "\(Int(v))" : v.formatted(.number.precision(.fractionLength(1))) }
    }

    /// A typed number, however it was typed: "1400", "1 400", "1,400", "47,5" or "47.5". A comma followed by exactly
    /// three digits groups thousands; any other comma is a decimal point (as in much of Europe).
    static func parse(_ s: String) -> Double? {
        var t = s.filter { !$0.isWhitespace && $0 != "\u{00A0}" && $0 != "\u{202F}" }
        guard !t.isEmpty else { return nil }
        if t.contains(",") && t.contains(".") {
            t = t.lastIndex(of: ",")! > t.lastIndex(of: ".")! ? t.replacingOccurrences(of: ".", with: "").replacingOccurrences(of: ",", with: ".")
                                                              : t.replacingOccurrences(of: ",", with: "")
        } else if t.contains(",") {
            let parts = t.components(separatedBy: ",")
            let grouping = !parts[0].isEmpty && parts[0] != "0" && parts.dropFirst().allSatisfy { $0.count == 3 }
            t = grouping ? parts.joined() : t.replacingOccurrences(of: ",", with: ".")
        }
        return Double(t)
    }
}
