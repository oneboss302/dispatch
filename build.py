#!/usr/bin/env python3
"""Build dispatch.castafero.com data.

Chester County: live Fire / EMS / Traffic calls from the county's official public WebCAD page,
kept as a rolling 48-hour history (the county page only lists calls that are currently active).
Delaware County publishes no live 911 feed, so it gets what is public: NWS alerts, scanner audio
links, and local news. Chester County road closures and NWS alerts are included too.

Standard library only. Usage: python3 build.py <state_dir> <out_dir>
  state_dir: history.json + geocache.json carried between runs (Actions cache)
  out_dir:   data.json for the page
"""
import html, json, math, os, re, sys, time
import urllib.parse, urllib.request, xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo

ET_TZ = ZoneInfo("America/New_York")
NOW = datetime.now(timezone.utc)
UA = "castafero-dispatch/1.0 (+https://dispatch.castafero.com)"
KEEP = timedelta(hours=48)
WEBCAD = "https://webcad.chesco.org/WebCad/webcad.asp"
CLOSURES = "https://webcad.chesco.org/WebCad/road_closures.asp"
GEOCODE_BUDGET = 40          # Nominatim lookups per run (1 per second, per its usage policy)

DELCO_MUNIS = """Aldan, Aston, Bethel, Brookhaven, Chadds Ford, Chester, Chester Heights, Chester Township, Clifton Heights,
Collingdale, Colwyn, Concord, Darby, Darby Township, East Lansdowne, Eddystone, Edgmont, Folcroft, Glenolden, Haverford,
Lansdowne, Lower Chichester, Marcus Hook, Marple, Media, Middletown, Millbourne, Morton, Nether Providence, Newtown, Norwood,
Parkside, Prospect Park, Radnor, Ridley, Ridley Park, Rose Valley, Rutledge, Sharon Hill, Springfield, Swarthmore, Thornbury,
Tinicum, Trainer, Upland, Upper Chichester, Upper Darby, Upper Providence, Yeadon""".replace("\n", " ").split(", ")
# Names distinctive enough to identify a Delco story on their own
DELCO_KEYWORDS = ["Delaware County", "Delco", "Upper Darby", "Ridley", "Haverford", "Radnor", "Marple", "Swarthmore",
                  "Collingdale", "Folcroft", "Glenolden", "Lansdowne", "Sharon Hill", "Yeadon", "Clifton Heights", "Aston",
                  "Brookhaven", "Prospect Park", "Marcus Hook", "Upper Chichester", "Lower Chichester", "Eddystone",
                  "Darby", "Nether Providence", "Tinicum", "Essington", "Drexel Hill", "Havertown", "Broomall", "Wallingford",
                  "Chester City", "City of Chester", "Media, Pa", "Media, PA", "Springfield, Delaware County"]
CHESCO_KEYWORDS = ["Chester County", "West Chester", "Coatesville", "Downingtown", "Phoenixville", "Exton", "Kennett",
                   "Oxford, Pa", "Malvern", "Tredyffrin", "Berwyn", "Paoli", "West Goshen", "East Goshen", "Uwchlan",
                   "Caln", "Spring City", "Honey Brook", "Avondale", "West Grove", "Chesco"]

INCIDENT_RE = re.compile(r"\b(fires?|blaze|burn(s|ed|ing)?|crash(es|ed)?|collisions?|accidents?|shoot(ing|ings|s)?|shot|stabb(ed|ing)|"
                         r"police|killed|kill(s|ing)?|dies|died|dead|death|struck|injur(ed|ies|y)|rescue[ds]?|evacuat\w*|hazmat|"
                         r"arrest(ed|s)?|charged|missing|investigat\w*|emergency|explosion|gas leak|flood\w*|outages?|closed|"
                         r"closure|drown\w*|overdose|robbery|assault|homicide|pursuit|lockdown)\b", re.I)

# Never show satire or fabricated-news outlets
SATIRE = ["the onion", "theonion", "babylon bee", "babylonbee", "clickhole", "the beaverton", "thebeaverton", "waterford whispers",
          "daily mash", "thedailymash", "newsthump", "duffel blog", "duffelblog", "reductress", "the hard times", "thehardtimes",
          "points in case", "national report", "nationalreport", "world news daily report", "worldnewsdailyreport", "empire news",
          "empirenews", "the shovel", "theshovel", "the betoota advocate", "betoota", "der postillon", "the rochdale herald",
          "the daily discord", "the borowitz report", "borowitz", "huzlers", "react365", "newsbiscuit", "the spoof", "thespoof",
          "satire", "parody"]


def is_satire(publisher, url):
    blob = f"{publisher} {url}".lower()
    return any(s in blob for s in SATIRE)

SCANNERS = [  # Broadcastify feeds for Delaware County, PA
    ("Delaware County Fire/EMS: Countywide Ops", 46438), ("Delaware County Fire: East", 46435),
    ("Delaware County Fire: Central", 46446), ("Delaware County Fire: West", 46445),
    ("Delaware County EMS: Countywide Ops", 46439), ("Delaware County Police Dispatch", 36323),
    ("Delaware County Police: Countywide Ops", 46530),
]
CHESCO_SCANNERS = [("Chester County Fire/EMS", 29810)]

ORDINALS = {"FIRST": "1st", "SECOND": "2nd", "THIRD": "3rd", "FOURTH": "4th", "FIFTH": "5th", "SIXTH": "6th",
            "SEVENTH": "7th", "EIGHTH": "8th", "NINTH": "9th", "TENTH": "10th", "ELEVENTH": "11th", "TWELFTH": "12th",
            "THIRTEENTH": "13th", "FOURTEENTH": "14th", "FIFTEENTH": "15th", "SIXTEENTH": "16th", "SEVENTEENTH": "17th",
            "EIGHTEENTH": "18th", "NINETEENTH": "19th", "TWENTIETH": "20th"}


def get(url, timeout=45, tries=3):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except Exception as err:  # the county server is flaky; retry a couple of times
            last = err
            time.sleep(3 * (i + 1))
    raise last


def iso(d):
    return d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if d else None


def from_iso(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None


def text(s):
    s = re.sub(r"<[^>]+>", " ", s or "")
    return re.sub(r"\s+", " ", html.unescape(s)).strip()


def titlecase(s):
    small = {"and", "of", "the", "at", "on"}
    out = []
    for i, w in enumerate(s.lower().split()):
        if w in ORDINALS.values() or re.fullmatch(r"\d+(st|nd|rd|th)", w):
            out.append(w)
        elif i and w in small:
            out.append(w)
        elif w in ("ems", "als", "bls", "co", "mva", "us", "pa", "sr", "i-76", "i-176", "rt", "n", "s", "e", "w") and len(w) <= 3:
            out.append(w.upper())
        else:
            out.append(w[:1].upper() + w[1:])
    return " ".join(out)


# ---------------------------------------------------------------- Chester County CAD
def chesco_calls():
    s = get(WEBCAD).decode("utf-8", "replace")
    m = re.search(r"Last Updated\s*([\d/]+\s+[\d:]+\s*[AP]M)", text(s))
    upstream = datetime.strptime(m.group(1).strip(), "%m/%d/%Y %I:%M:%S %p").replace(tzinfo=ET_TZ) if m else None
    calls = []
    for row in re.findall(r"<tr>.*?</tr>", s, re.S | re.I):
        cat = re.search(r"livecadcomments-(\w+)\.asp", row)
        if not cat:
            continue
        cells = [text(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S | re.I)]
        if len(cells) < 6:
            continue
        inc, typ, loc, muni, when, station = cells[:6]
        try:
            dispatched = datetime.strptime(when, "%m-%d-%Y %H:%M:%S").replace(tzinfo=ET_TZ)
        except ValueError:
            dispatched = None
        calls.append({"id": inc, "cat": cat.group(1).lower(), "type": titlecase(typ), "loc": loc, "muni": muni,
                      "station": station.strip(), "dispatched": iso(dispatched), "county": "Chester"})
    return calls, iso(upstream)


def chesco_closures():
    s = get(CLOSURES).decode("utf-8", "replace")
    out = []
    for row in re.findall(r"<tr[^>]*>.*?</tr>", s, re.S | re.I):
        cells = [text(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S | re.I)]
        if len(cells) >= 5 and cells[0] and cells[0].lower() != "primary road":
            out.append({"road": titlecase(cells[0]), "muni": cells[1], "remarks": cells[2], "since": cells[3], "kind": cells[4]})
    return out


# ---------------------------------------------------------------- geocoding (OpenStreetMap Nominatim, cached)
class Geocoder:
    def __init__(self, cache):
        self.cache, self.used, self.last = cache, 0, 0.0

    def _q(self, q):
        if q in self.cache:
            return self.cache[q]
        if self.used >= GEOCODE_BUDGET:
            return "defer"
        wait = 1.1 - (time.time() - self.last)
        if wait > 0:
            time.sleep(wait)
        self.used += 1
        self.last = time.time()
        url = "https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode(
            {"format": "jsonv2", "limit": 1, "countrycodes": "us", "q": q})
        try:
            d = json.loads(get(url, timeout=20, tries=1))
            val = [round(float(d[0]["lat"]), 5), round(float(d[0]["lon"]), 5)] if d else None
        except Exception:
            return "defer"
        self.cache[q] = val
        return val

    @staticmethod
    def street(s):
        s = re.sub(r"\b(BLK|BLOCK)\b.*", "", s.upper()).strip(" ,&/")
        return " ".join(ORDINALS.get(w, w) for w in s.split())

    def locate(self, loc, muni, county):
        # OSM names boroughs and cities without the suffix ("West Chester", not "West Chester Borough")
        area = f"{re.sub(r'\s+(Borough|City)$', '', muni.strip())}, {county} County, Pennsylvania"
        parts = [self.street(p) for p in re.split(r"\s*[&/]\s*", loc) if p.strip()]
        pts = []
        for p in parts[:2]:
            if len(p) < 3:
                continue
            r = self._q(f"{p}, {area}")
            if r == "defer":
                return None, "pending"
            if r:
                pts.append(r)
        if len(pts) == 2 and abs(pts[0][0] - pts[1][0]) < 0.03 and abs(pts[0][1] - pts[1][1]) < 0.03:
            return [round((pts[0][0] + pts[1][0]) / 2, 5), round((pts[0][1] + pts[1][1]) / 2, 5)], "intersection"
        if pts:
            return pts[0], "street"
        r = self._q(area)
        if r == "defer":
            return None, "pending"
        return (r, "town") if r else (None, "none")


# ---------------------------------------------------------------- other sources
def nws(zone, label):
    d = json.loads(get(f"https://api.weather.gov/alerts/active?zone={zone}", timeout=30))
    out = []
    for f in d.get("features", []):
        p = f["properties"]
        out.append({"county": label, "event": p.get("event"), "severity": p.get("severity"), "headline": p.get("headline"),
                    "ends": p.get("ends") or p.get("expires"), "url": f"https://forecast.weather.gov/showsigwx.php?warnzone={zone}"})
    return out


def news():
    queries = [
        '"Delaware County" (Pa OR Pennsylvania) (fire OR crash OR shooting OR police OR rescue OR evacuated)',
        '("Upper Darby" OR Ridley OR Haverford OR Radnor OR Marple OR "Drexel Hill" OR Havertown) (fire OR crash OR shooting OR police)',
        '"Chester County" (Pa OR Pennsylvania) (fire OR crash OR shooting OR police OR rescue)',
    ]
    feeds = [("Google News", "https://news.google.com/rss/search?" + urllib.parse.urlencode(
        {"q": q + " when:2d", "hl": "en-US", "gl": "US", "ceid": "US:en"}), True) for q in queries]
    feeds.append(("6abc", "https://6abc.com/feed/", False))
    seen, out = set(), []
    for src, url, gn in feeds:
        try:
            root = ET.fromstring(get(url, timeout=30, tries=2))
        except Exception as err:
            print("news failed:", src, err, file=sys.stderr)
            continue
        for it in root.iter("item"):
            title = text(it.findtext("title"))
            pub = text(it.findtext("source")) if gn else src
            if gn:
                title = re.sub(r"\s+-\s+[^-]{2,60}$", "", title)
            key = re.sub(r"\W+", " ", title.lower()).strip()
            if not title or key in seen:
                continue
            blob = title + " " + text(it.findtext("description"))
            county = ("Delaware" if any(k.lower() in blob.lower() for k in DELCO_KEYWORDS) else
                      "Chester" if any(k.lower() in blob.lower() for k in CHESCO_KEYWORDS) else None)
            if not county:
                continue
            if re.search(r"\b(Ohio|Indiana|Iowa|Oklahoma|New York|N\.Y\.|Ridley Scott)\b", blob):
                continue
            if not INCIDENT_RE.search(title) or is_satire(pub, it.findtext("link") or ""):
                continue
            try:
                d = parsedate_to_datetime(it.findtext("pubDate"))
            except Exception:
                d = None
            if d and NOW - d > timedelta(days=3):
                continue
            seen.add(key)
            out.append({"title": title, "src": pub, "url": it.findtext("link"), "date": iso(d), "county": county})
    out.sort(key=lambda x: x["date"] or "", reverse=True)
    return out[:40]


# ---------------------------------------------------------------- main
def load(path, default):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def main(state_dir, out_dir):
    os.makedirs(state_dir, exist_ok=True)
    os.makedirs(out_dir, exist_ok=True)
    history = {c["id"]: c for c in load(os.path.join(state_dir, "history.json"), [])}
    geocache = load(os.path.join(state_dir, "geocache.json"), {})
    errors = []

    try:
        live, upstream = chesco_calls()
    except Exception as err:
        live, upstream = None, None
        errors.append(f"Chester County CAD unreachable: {err}")

    if live is not None:
        live_ids = {c["id"] for c in live}
        for c in live:
            h = history.get(c["id"])
            if h:
                h.update({k: c[k] for k in ("type", "cat", "loc", "muni", "station", "dispatched")})
                h["last_seen"] = iso(NOW)
                h["active"] = True
                h.pop("cleared", None)
            else:
                c.update(first_seen=iso(NOW), last_seen=iso(NOW), active=True)
                history[c["id"]] = c
        for h in history.values():
            if h.get("active") and h["id"] not in live_ids:
                h["active"] = False
                h["cleared"] = iso(NOW)   # cleared at or before this run

    # drop old calls
    for k in [k for k, h in history.items() if NOW - from_iso(h.get("dispatched") or h["first_seen"]) > KEEP]:
        del history[k]

    # geocode, newest first
    geo = Geocoder(geocache)
    for h in sorted(history.values(), key=lambda h: h.get("dispatched") or "", reverse=True):
        if h.get("ll") and h.get("prec") != "pending":
            continue
        ll, prec = geo.locate(h["loc"], h["muni"], h["county"])
        h["ll"], h["prec"] = ll, prec

    extras = {}
    for key, fn, args in [("closures", chesco_closures, ()), ("alerts_delco", nws, ("PAC045", "Delaware")),
                          ("alerts_chesco", nws, ("PAC029", "Chester")), ("news", news, ())]:
        try:
            extras[key] = fn(*args)
        except Exception as err:
            extras[key] = []
            errors.append(f"{key}: {err}")

    calls = sorted(history.values(), key=lambda h: h.get("dispatched") or h["first_seen"], reverse=True)
    data = {
        "updated": iso(NOW), "upstream_updated": upstream, "errors": errors,
        "calls": calls, "closures": extras["closures"],
        "alerts": extras["alerts_delco"] + extras["alerts_chesco"], "news": extras["news"],
        "scanners": [{"name": n, "url": f"https://www.broadcastify.com/listen/feed/{i}"} for n, i in SCANNERS],
        "chesco_scanners": [{"name": n, "url": f"https://www.broadcastify.com/listen/feed/{i}"} for n, i in CHESCO_SCANNERS],
        "delco_munis": DELCO_MUNIS,
    }
    with open(os.path.join(out_dir, "data.json"), "w", encoding="utf-8") as fh:
        json.dump(data, fh, separators=(",", ":"))
    with open(os.path.join(state_dir, "history.json"), "w", encoding="utf-8") as fh:
        json.dump(calls, fh, separators=(",", ":"))
    with open(os.path.join(state_dir, "geocache.json"), "w", encoding="utf-8") as fh:
        json.dump(geocache, fh, indent=0, sort_keys=True)
    active = sum(1 for c in calls if c.get("active"))
    print(f"calls: {len(calls)} in 48h, {active} active; geocoded {geo.used} new; "
          f"pending {sum(1 for c in calls if c.get('prec') == 'pending')}; news {len(extras['news'])}; errors {errors}")
    if live is None and not calls:
        sys.exit("no data at all; keeping the previous site")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "state", sys.argv[2] if len(sys.argv) > 2 else "_site")
