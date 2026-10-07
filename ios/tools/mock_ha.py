#!/usr/bin/env python3
"""A stand-in for Home Assistant's food_diary integration, for the simulator: UI tests, demos and screenshots.

    python3 tools/mock_ha.py [port]          # default port 8811

Then launch the app (Debug build) with the arguments `-mockSignIn -server http://localhost:8811`. The diary is a
believable one: today's four meals, two months of history with a growing streak, usuals, saved meals and a week to look
back on. Nothing is real and nothing leaves your Mac.

Photos: drop JPEGs named after a food's slug (e.g. `tools/demo-photos/miso-salmon-with-sesame-greens.jpg`) into
tools/demo-photos/ and the mock serves them as that food's picture. Without them the app draws its own tiles.

Every request is appended to /tmp/mock_ha.log (/tmp/mock_ha-<port>.log on other ports) as one JSON line, so tests can
check what reached "Home Assistant". Test hooks (POST): /reset, /outage {"on": bool}, /ahead_today.
"""
import json
import os
import random
import re
import sys
import uuid
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

NUM = ("kcal", "protein_g", "carbs_g", "fat_g", "fibre_g")
PHOTOS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "demo-photos")
TODAY = date.today().isoformat()
GOALS0 = {"kcal": 1800.0, "protein_g": 110.0, "carbs_g": 200.0, "fat_g": 60.0, "fibre_g": 30.0}
GOALS = dict(GOALS0)
PER_MEAL = {"breakfast": [350, 500], "lunch": [450, 600], "dinner": [500, 700], "snack": [100, 250]}


def slug(name):
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def picture(name):
    """A demo photo for this food, if one is in tools/demo-photos/."""
    return f"/local/demo/{slug(name)}.jpg" if os.path.exists(os.path.join(PHOTOS_DIR, f"{slug(name)}.jpg")) else None


# name, kcal, protein, carbs, fat, fibre
BREAKFASTS = [
    ("Greek yogurt with berries and honey", 290, 20, 36, 7, 4),
    ("Overnight oats with blueberries", 340, 14, 52, 9, 7),
    ("Smashed avocado and poached eggs on sourdough", 430, 19, 34, 24, 8),
    ("Banana and peanut butter porridge", 410, 15, 58, 13, 7),
    ("Spinach and feta omelette", 320, 24, 6, 22, 2),
    ("Buttermilk pancakes with maple and berries", 470, 12, 72, 14, 4),
]
LUNCHES = [
    ("Chicken, avocado and quinoa bowl", 520, 38, 44, 19, 9),
    ("Hummus and falafel wrap", 480, 16, 58, 20, 10),
    ("Tomato and red lentil soup with crusty bread", 410, 18, 62, 9, 12),
    ("Poke bowl with salmon and edamame", 560, 34, 60, 18, 6),
    ("Caprese ciabatta with basil pesto", 520, 22, 52, 24, 4),
    ("Thai green curry with jasmine rice", 610, 28, 70, 22, 5),
]
DINNERS = [
    ("Miso salmon with sesame greens", 590, 40, 52, 22, 6),
    ("Chicken stir-fry with egg noodles", 560, 39, 64, 14, 6),
    ("Mushroom and spinach risotto", 540, 15, 78, 17, 4),
    ("Harissa chickpea and squash traybake", 510, 18, 66, 18, 15),
    ("Turkey meatballs with tomato orzo", 620, 42, 66, 18, 6),
    ("Prawn tacos with mango salsa", 530, 32, 58, 16, 7),
    ("Beef chilli with brown rice", 640, 40, 70, 18, 11),
]
SNACKS = [
    ("Apple with almond butter", 190, 5, 22, 10, 5),
    ("Skyr with granola", 260, 22, 30, 5, 2),
    ("Rice cakes with peanut butter", 160, 6, 14, 9, 2),
    ("Flat white", 110, 6, 9, 6, 0),
    ("Clementine", 35, 1, 8, 0, 1),
]
DRINKS = [("Flat white", 110, 6, 9, 6, 0)]
COMMON = [  # everyday foods the mock "knows" when typed (matched inside what was typed, longest first)
    ("porridge with blueberries", 310, 11, 52, 7, 6), ("porridge", 260, 9, 44, 6, 5), ("banana", 105, 1, 27, 0, 3),
    ("latte", 150, 9, 13, 6, 0), ("flat white", 110, 6, 9, 6, 0), ("apple", 80, 0, 21, 0, 4), ("toast", 90, 3, 16, 1, 2),
    ("boiled egg", 78, 6, 1, 5, 0), ("orange juice", 110, 2, 26, 0, 1), ("croissant", 230, 5, 26, 12, 1),
]
CHOCOLATE = {"name": "Dark chocolate 70%", "per_100": {"kcal": 580, "protein_g": 8, "carbs_g": 36, "fat_g": 42, "fibre_g": 11}}


def vals(t):
    return dict(zip(NUM, t[1:]))


def entry(day, name, meal, values, at, portions=1.0, source="text", **extra):
    per = {k: float(values.get(k, 0)) for k in NUM}
    e = {"id": uuid.uuid4().hex[:10], "at": f"{day}T{at}+01:00", "meal": meal, "name": name, "portions": portions,
         "source": source, "per_portion": per, **extra}
    if picture(name):
        e["image"] = picture(name)
    recount(e)
    return e


def label_entry(day, food, grams, meal, at):
    per = {k: round(food["per_100"][k] * grams / 100, 1) for k in NUM}
    return entry(day, food["name"], meal, per, at, source="barcode", per_100=food["per_100"], grams=grams, unit="g")


def recount(e):
    p = e.get("portions") or 1
    for k in NUM:
        e[k] = round(e["per_portion"].get(k, 0) * p, 1)


def today_entries():
    return [
        entry(TODAY, BREAKFASTS[0][0], "breakfast", vals(BREAKFASTS[0]), "07:45", source="photo"),
        entry(TODAY, "Flat white", "breakfast", vals(DRINKS[0]), "07:50", source="again"),
        entry(TODAY, LUNCHES[0][0], "lunch", vals(LUNCHES[0]), "12:40", source="photo"),
        label_entry(TODAY, CHOCOLATE, 20, "snack", "15:10"),
        entry(TODAY, DINNERS[0][0], "dinner", vals(DINNERS[0]), "18:45", source="photo"),
    ]


def history_day(d, i, rng):
    """One past day: a breakfast, a lunch, a dinner and maybe a snack, scaled so most days land near the goal. The last
    nine days all do (a growing streak); a few earlier days are empty or over."""
    if i > 9 and i % 11 == 0:
        return []
    b, l, n = BREAKFASTS[(i * 5) % len(BREAKFASTS)], LUNCHES[(i * 3) % len(LUNCHES)], DINNERS[(i * 2) % len(DINNERS)]
    es = [entry(d, b[0], "breakfast", vals(b), "07:50"), entry(d, l[0], "lunch", vals(l), "12:45"),
          entry(d, n[0], "dinner", vals(n), "19:00")]
    if i % 2:
        s = SNACKS[i % len(SNACKS)]
        es.append(entry(d, s[0], "snack", vals(s), "16:00", source="again"))
    if i % 3 == 0:
        es.append(entry(d, "Flat white", "breakfast", vals(DRINKS[0]), "08:00", source="again"))
    # a bigger dinner (in half portions) brings the day up towards its aim
    aim = GOALS0["kcal"] * (rng.uniform(0.88, 1.02) if i <= 9 or i % 7 else rng.uniform(1.1, 1.2))
    rest = sum(e["kcal"] for e in es) - es[2]["kcal"]
    es[2]["portions"] = max(1.0, min(2.0, round((aim - rest) / es[2]["kcal"] * 2) / 2))
    recount(es[2])
    return es


def fresh_days():
    rng = random.Random(7)
    days = {TODAY: today_entries()}
    for i in range(1, 75):
        d = (date.today() - timedelta(days=i)).isoformat()
        days[d] = history_day(d, i, rng)
    return days


def recent_food(t, times, meal=None):
    f = {"name": t[0], **vals(t), "times": times, "source": "text", "ref": ""}
    if meal:
        f["meal"] = meal
    if picture(t[0]):
        f["image"] = picture(t[0])
    return f


def fresh_saved():
    def saved(name, meal, items):
        tot = {k: round(sum(x[i + 1] for x in items), 1) for i, k in enumerate(NUM)}
        return {"id": uuid.uuid4().hex[:10], "name": name, "meal": meal, "items": [x[0] for x in items], **tot, "at": f"{TODAY}T09:00+01:00"}
    return [saved("Sunday brunch", "breakfast", [BREAKFASTS[2], DRINKS[0]]),
            saved("Gym day lunch", "lunch", [LUNCHES[0], SNACKS[1]])]


DAYS, SAVED, UNDO, PHOTOS = {}, [], {}, {}
LOG = {"path": "/tmp/mock_ha.log"}
OUTAGE = {"on": False}  # tests: food_diary answers 599, which a Debug build reads as "offline"


def reset():
    DAYS.clear(); DAYS.update(fresh_days())
    SAVED[:] = fresh_saved()
    UNDO.clear(); PHOTOS.clear()
    GOALS.clear(); GOALS.update(GOALS0)
    OUTAGE["on"] = False


def totals(es):
    return {k: round(sum(e.get(k, 0) for e in es), 1) for k in NUM}


def recent():
    foods = [recent_food(SNACKS[1], 14), recent_food(BREAKFASTS[0], 12), recent_food(DRINKS[0], 30), recent_food(LUNCHES[0], 9),
             recent_food(DINNERS[0], 6), recent_food(SNACKS[0], 8), recent_food(SNACKS[2], 5), recent_food(SNACKS[4], 7),
             recent_food(LUNCHES[1], 5), recent_food(DINNERS[1], 5), recent_food(BREAKFASTS[3], 4),
             {"name": CHOCOLATE["name"], "per_100": CHOCOLATE["per_100"], "grams": 20, "unit": "g",
              **{k: round(CHOCOLATE["per_100"][k] * 0.2, 1) for k in NUM}, "times": 6}]
    usuals = {"breakfast": [recent_food(BREAKFASTS[1], 9, "breakfast")], "lunch": [recent_food(LUNCHES[1], 6, "lunch")],
              "dinner": [recent_food(DINNERS[1], 5, "dinner")], "snack": [recent_food(SNACKS[0], 8, "snack")]}
    return {"foods": foods, "usuals": usuals, "saved": SAVED}


def review(end, n=7):
    last = date.fromisoformat(end)
    span = [(last - timedelta(days=i)).isoformat() for i in range(n - 1, -1, -1)]
    rows = [{"date": d, **totals(DAYS.get(d, [])), "logged": len(DAYS.get(d, []))} for d in span]
    logged = [r for r in rows if r["logged"]]
    goal = GOALS["kcal"]
    on = [r for r in logged if goal * 0.75 <= r["kcal"] <= goal * 1.05]
    names = [e["name"] for d in span for e in DAYS.get(d, [])]
    top = max(set(names), key=names.count) if names else None
    before = (last - timedelta(days=n)).isoformat()
    prev = [sum(e["kcal"] for e in DAYS.get((date.fromisoformat(before) - timedelta(days=i)).isoformat(), [])) for i in range(7)]
    prev = [k for k in prev if k]
    return {"start": span[0], "end": span[-1], "days": rows, "goal_kcal": goal, "goal_protein_g": GOALS["protein_g"],
            "days_logged": len(logged), "avg_kcal": round(sum(r["kcal"] for r in logged) / len(logged)) if logged else 0,
            "on_target": len(on), "over": len([r for r in logged if r["kcal"] > goal * 1.05]),
            "protein_days": len([r for r in logged if r["protein_g"] >= GOALS["protein_g"] * 0.9]),
            "last_week_avg_kcal": round(sum(prev) / len(prev)) if prev else None,
            "favourite": {"name": top, "times": names.count(top)} if top else None,
            "new_dishes": ["Harissa chickpea and squash traybake"],
            "best_day": (lambda b: {"date": b["date"], "kcal": b["kcal"]})(min(on, key=lambda r: abs(r["kcal"] - goal))) if on else None}


def estimate(d):
    kind = d.get("kind", "text")
    if kind == "barcode":
        if str(d.get("barcode", "")).startswith("000"):
            raise ValueError("That product isn't in Open Food Facts yet. Take a photo of its label instead.")
        p = {"kcal": 362, "protein_g": 12, "carbs_g": 69, "fat_g": 2, "fibre_g": 10}
        return {"name": "Wholegrain wheat biscuits", "per_100": p, "grams": 75, "unit": "g", "serving_g": 37.5, "pack_g": 430, "guessed": False,
                "note": "75 g · 362 kcal per 100 g", "source": "barcode", "barcode": str(d.get("barcode", "")), "product_source": "openfoodfacts",
                **{k: round(v * 0.75, 1) for k, v in p.items()}}
    if kind == "label":
        p = {"kcal": 488, "protein_g": 7, "carbs_g": 64, "fat_g": 21, "fibre_g": 3.6}
        return {"name": "Oat biscuits", "per_100": p, "grams": 44.1, "unit": "g", "serving_g": 14.7, "pack_g": 300, "guessed": False,
                "note": "44 g · 488 kcal per 100 g", "source": "label", **{k: round(v * 0.441, 1) for k, v in p.items()}}
    if kind == "photo":  # the picture is kept, as the integration does, for log_food's `photo`
        name = uuid.uuid4().hex[:12]
        PHOTOS[name] = __import__("base64").b64decode(d.get("image", ""))
        return {"name": "Beef chilli with rice", "kcal": 520, "protein_g": 32, "carbs_g": 48, "fat_g": 20, "fibre_g": 9,
                "note": "1 bowl, about 400 g", "foods": ["beef chilli", "rice"], "source": "photo", "photo": name}
    t = str(d.get("text", "")).strip()
    if t.lower() == "2 eggs on toast":
        return {"name": "Fried eggs on toast", "kcal": 315, "protein_g": 16, "carbs_g": 18, "fat_g": 20, "fibre_g": 1.5,
                "note": "2 eggs and 1 slice of toast", "foods": ["fried egg", "toast"], "source": "text"}
    bare = re.sub(r"^(a|an|some|one)\s+", "", t.lower())
    known = next((f for f in BREAKFASTS + LUNCHES + DINNERS + SNACKS + COMMON if f[0].lower() == bare or f[0].lower() in bare), None)
    if known:
        name = t[:1].upper() + t[1:] if known in COMMON else known[0]
        return {"name": name, **vals(known), "note": "one portion", "foods": [known[0].lower()], "source": "text"}
    k = 80 + (sum(map(ord, t)) % 9) * 40  # anything else: believable, and the same every time
    return {"name": t[:1].upper() + t[1:], "kcal": k, "protein_g": round(k * 0.05, 1), "carbs_g": round(k * 0.1, 1),
            "fat_g": round(k * 0.035, 1), "fibre_g": 1.0, "note": "one portion", "foods": [t.lower()], "source": "text"}


def find(day, entry_id):
    e = next((e for e in DAYS.get(day, []) if e["id"] == entry_id), None)
    if e is None:
        raise ValueError("That entry isn't in the diary on that day.")
    return e


def service(name, d):
    day = d.get("date") or TODAY
    if name == "get_day":
        es = DAYS.get(day, [])
        t = totals(es)
        return {"date": day, "entries": es, "totals": t, "goals": {**GOALS, "per_meal": PER_MEAL},
                "left": {k: round(GOALS[k] - t[k], 1) for k in GOALS},
                "meals": {m: round(sum(e["kcal"] for e in es if e["meal"] == m), 1) for m in ("breakfast", "lunch", "dinner", "snack")}}
    if name == "get_history":
        n = int(d.get("days", 7))
        end = date.fromisoformat(d.get("date") or TODAY)
        out = []
        for i in range(n - 1, -1, -1):
            dd = (end - timedelta(days=i)).isoformat()
            out.append({"date": dd, **totals(DAYS.get(dd, [])), "logged": len(DAYS.get(dd, []))})
        return {"days": out, "goals": GOALS}
    if name == "get_recent":
        return recent()
    if name == "estimate":
        return estimate(d)
    if name == "log_food":
        per100, grams = d.get("per_100"), d.get("grams")
        e = {"id": uuid.uuid4().hex[:10], "at": datetime.now().astimezone().isoformat(timespec="minutes"), "meal": d.get("meal", "snack"),
             "name": d["name"], "portions": 1.0 if per100 and grams else float(d.get("portions", 1)), "source": d.get("source", "manual")}
        e["per_portion"] = ({k: round(per100.get(k, 0) * grams / 100, 2) for k in NUM} if per100 and grams else {k: float(d.get(k, 0)) for k in NUM})
        if per100 and grams:
            e.update(per_100=per100, grams=grams, unit=d.get("unit", "g"))
        for k in ("edited", "note", "ref", "barcode", "photo", "image_url"):
            if d.get(k):
                e[k] = d[k]
        if d.get("photo") in PHOTOS:
            e["image"] = f"/api/food_diary/photo/{d['photo']}"
        elif d.get("image_url") or picture(d["name"]):
            e["image"] = d.get("image_url") or picture(d["name"])
        recount(e)
        DAYS.setdefault(day, []).append(e)
        return {"entry": e, "date": day, "totals": totals(DAYS[day])}
    if name == "update_food":
        e = find(day, d["entry_id"])
        if d.get("grams") and e.get("per_100"):
            e["grams"] = d["grams"]; e["per_portion"] = {k: round(e["per_100"].get(k, 0) * d["grams"] / 100, 2) for k in NUM}; e["portions"] = 1
        if d.get("portions"):
            e["portions"] = d["portions"]
        if any(k in d for k in NUM):
            e["per_portion"] = {k: (d.get(k, e[k])) / (e["portions"] or 1) for k in NUM}; e["edited"] = True
        if d.get("meal"):
            e["meal"] = d["meal"]
        recount(e)
        return {"entry": e}
    if name == "delete_food":
        find(day, d["entry_id"])
        DAYS[day] = [e for e in DAYS.get(day, []) if e["id"] != d["entry_id"]]
        return {"ok": True}
    if name == "set_goals":
        GOALS.update({k: float(v) for k, v in d.items() if k in NUM})
        return {"goals": GOALS}
    if name == "save_meal":
        items = [e for e in DAYS.get(day, []) if e["meal"] == d["meal"]]
        if not items:
            raise ValueError("Nothing is logged in that meal on that day.")
        sv = {"id": uuid.uuid4().hex[:10], "name": d["name"], "meal": d["meal"], "items": [e["name"] for e in items], **totals(items),
              "at": datetime.now().astimezone().isoformat(timespec="minutes")}
        SAVED[:] = [x for x in SAVED if x["name"].lower() != d["name"].lower()] + [sv]
        return {"saved": sv}
    if name == "update_saved_meal":
        s = next((x for x in SAVED if x["id"] == d["saved_id"]), None)
        if s is None:
            raise ValueError("That saved meal isn't there.")
        if d.get("name"):
            SAVED[:] = [x for x in SAVED if x["id"] == s["id"] or x["name"].lower() != d["name"].lower()]
            s["name"] = d["name"]
        if d.get("meal"):
            s["meal"] = d["meal"]
        return {"saved": s}
    if name == "delete_saved_meal":
        SAVED[:] = [x for x in SAVED if x["id"] != d["saved_id"]]
        return {"ok": True}
    if name == "get_week_review":
        return review(d.get("date") or (date.today() - timedelta(days=1)).isoformat(), int(d.get("days", 7)))
    if name == "copy_day":
        token = uuid.uuid4().hex[:8]
        src = [e for e in DAYS.get(d["from"], []) if not d.get("meal") or e["meal"] == d["meal"]]
        UNDO[token] = {t: list(DAYS.get(t, [])) for t in d["to"]}
        added, removed = {}, {}
        for t in d["to"]:
            if t == d["from"]:
                continue
            es = DAYS.setdefault(t, [])
            if d.get("replace"):
                gone = [e for e in es if not d.get("meal") or e["meal"] == d["meal"]]
                DAYS[t] = es = [e for e in es if e not in gone]
                if gone:
                    removed[t] = len(gone)
            for e in src:
                es.append({**e, "id": uuid.uuid4().hex[:10], "source": "again"})
            added[t] = len(src)
        return {"token": token, "added": added, "removed": removed}
    if name == "undo_copy":
        DAYS.update(UNDO.pop(d["token"]))
        return {"ok": True}
    if name == "set_photo":
        e = find(day, d["entry_id"])
        PHOTOS[e["id"]] = __import__("base64").b64decode(d.get("image", ""))
        e["photo"] = e["id"]
        e["image"] = f"/api/food_diary/photo/{e['id']}"
        return {"entry": e}
    raise ValueError(f"Unknown action {name}")


class H(BaseHTTPRequestHandler):
    def _json(self, code, body):
        raw = json.dumps(body).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(raw))); self.end_headers()
        self.wfile.write(raw)

    def _jpeg(self, raw):
        self.send_response(200); self.send_header("Content-Type", "image/jpeg"); self.send_header("Content-Length", str(len(raw))); self.end_headers()
        self.wfile.write(raw)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    def _log(self, body):
        with open(LOG["path"], "a") as f:
            f.write(json.dumps({"path": self.path, "body": body}) + "\n")

    def do_GET(self):
        p = urlparse(self.path).path
        self._log("")
        if p.startswith("/local/demo/"):
            f = os.path.join(PHOTOS_DIR, os.path.basename(p))
            if os.path.exists(f):
                return self._jpeg(open(f, "rb").read())
        if p.startswith("/api/food_diary/photo/"):
            raw = PHOTOS.get(os.path.basename(p))
            if raw:
                return self._jpeg(raw)
        self._json(404, {"message": "Not found"})

    def do_POST(self):
        p = urlparse(self.path).path
        raw = self._body()
        try:
            data = json.loads(raw or b"{}")
        except ValueError:
            data = {}
        if p == "/reset":  # tests: every case starts from the same diary
            reset()
            open(LOG["path"], "w").close()
            return self._json(200, {"ok": True})
        if p == "/outage":  # tests: {"on": true} cuts Home Assistant off for adding food, {"on": false} brings it back
            OUTAGE["on"] = bool(data.get("on"))
            return self._json(200, {"ok": True})
        if p == "/ahead_today":  # tests: tonight's dinner was put in yesterday (leftovers), so it counts but isn't eaten yet
            y = (date.today() - timedelta(days=1)).isoformat()
            DAYS[TODAY] = [e for e in DAYS[TODAY] if e["meal"] != "dinner"]
            DAYS[TODAY].append({**entry(TODAY, "Lasagne with garlic bread", "dinner", {"kcal": 900, "protein_g": 44, "carbs_g": 92, "fat_g": 38, "fibre_g": 6}, "21:30", source="again"),
                                "at": f"{y}T21:30+01:00"})
            return self._json(200, {"ok": True})
        if p == "/auth/token":
            self._log(raw.decode())
            return self._json(200, {"access_token": "mock-access", "refresh_token": "mock-refresh", "expires_in": 1800, "token_type": "Bearer"})
        self._log({k: (v[:40] + "…" if isinstance(v, str) and len(v) > 60 else v) for k, v in data.items()} if isinstance(data, dict) else data)
        if p.startswith("/api/webhook/"):  # Apple Health activity, when a webhook id is set in Settings
            return self._json(200, {})
        if p.startswith("/api/services/food_diary/"):
            action = p.rsplit("/", 1)[1]
            if OUTAGE["on"] and action in ("log_food", "get_day", "get_recent", "get_history"):
                return self._json(599, {})
            try:
                return self._json(200, {"changed_states": [], "service_response": service(action, data)})
            except ValueError as e:
                return self._json(400, {"message": str(e)})
        self._json(404, {"message": "Not found"})

    def log_message(self, *a):
        pass


reset()

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8811
    if port != 8811:
        LOG["path"] = f"/tmp/mock_ha-{port}.log"
    print(f"Mock Home Assistant (food_diary) on http://localhost:{port}")
    ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
