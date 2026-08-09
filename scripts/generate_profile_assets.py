#!/usr/bin/env python3
"""Erzeugt die Profil-Assets aus echten GitHub-Daten.

Schreibt sechs SVGs nach assets/:
    activity-dark.svg   / activity-light.svg     Heatmap + Kennzahlen
    languages-dark.svg  / languages-light.svg    Sprachverteilung
    rhythm-dark.svg     / rhythm-light.svg       Wochentage + Projekte pro Jahr

Nur Standardbibliothek, damit der Workflow ohne pip-Schritt läuft.

Datenschutz: Es werden ausschließlich Aggregate veröffentlicht: Beiträge pro
Tag, Anteile pro Sprache, Projekte pro Jahr. Repository-Namen, Commit-Nachrichten
und Inhalte privater Repos verlassen den Workflow nicht.

Gestaltung: Swiss-technisch. Strenges Raster, scharfe Kanten (kein Radius),
Haarlinien nur als Struktur, ein Akzent, monochrome Datenbalken mit Rang über
Deckkraft. Keine Glows, kein Deko-Raster, keine Dauerschleifen.

Motion: Aufbau beim Laden, einmalig. Jede Animation startet gedimmt und
verkleinert, nicht bei Null. Dadurch bleibt jede Grafik auch dort lesbar, wo
nur der erste Frame gerendert wird (Vorschauen, Social-Cards, inaktive Tabs).
"""

import datetime
import json
import os
import sys
import urllib.error
import urllib.request
from collections import defaultdict

API = "https://api.github.com/graphql"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, "assets")

LOGIN = os.environ.get("PROFILE_LOGIN") or "constantinktz"
TOKEN = os.environ.get("GH_TOKEN") or ""

# Klone fremder Projekte verfälschen die Sprachverteilung, weil ihr Code
# mitzählt. Kommagetrennt über PROFILE_EXCLUDE überschreibbar.
EXCLUDE = {n.strip() for n in (os.environ.get("PROFILE_EXCLUDE") or "open-webui").split(",")
           if n.strip()}

MONTHS_DE = ["Jan", "Feb", "Mär", "Apr", "Mai", "Jun",
             "Jul", "Aug", "Sep", "Okt", "Nov", "Dez"]
WEEKDAYS_DE = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]
# GitHub liefert weekday 0 = Sonntag; wir zeigen die Woche ab Montag.
WEEKDAY_ORDER = [1, 2, 3, 4, 5, 6, 0]

LEVEL_INDEX = {"NONE": 0, "FIRST_QUARTILE": 1, "SECOND_QUARTILE": 2,
               "THIRD_QUARTILE": 3, "FOURTH_QUARTILE": 4}

DARK = {
    "name": "dark",
    "bg": "#0D1117", "text": "#E6EDF3", "muted": "#7D8590",
    "rule": "#21262D", "accent": "#39D353",
    "levels": ["#161B22", "#0E4429", "#006D32", "#26A641", "#39D353"],
}

LIGHT = {
    "name": "light",
    "bg": "#FFFFFF", "text": "#1F2328", "muted": "#59636E",
    "rule": "#D8DEE4", "accent": "#1A7F37",
    "levels": ["#EBEDF0", "#9BE9A8", "#40C463", "#30A14E", "#216E39"],
}

MONO = ('ui-monospace,SFMono-Regular,&quot;SF Mono&quot;,Menlo,Consolas,'
        '&quot;Liberation Mono&quot;,monospace')
SANS = ('ui-sans-serif,-apple-system,BlinkMacSystemFont,&quot;Segoe UI&quot;,'
        'Roboto,Helvetica,Arial,sans-serif')

# Rasterkanten. GitHub skaliert README-Bilder auf ~846px (0.66x); alle
# Schriftgrade sind darauf ausgelegt.
W = 1280
LEFT = 56
RIGHT = 1224


# --------------------------------------------------------------------------- #
# Daten
# --------------------------------------------------------------------------- #

def gql(query, variables):
    body = json.dumps({"query": query, "variables": variables}).encode("utf-8")
    req = urllib.request.Request(API, data=body, headers={
        "Authorization": "Bearer " + TOKEN,
        "Content-Type": "application/json",
        "User-Agent": "profile-asset-generator",
    })
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            payload = json.load(resp)
    except urllib.error.HTTPError as exc:
        sys.exit("HTTP %s von der GitHub-API: %s"
                 % (exc.code, exc.read().decode("utf-8", "replace")[:400]))
    if payload.get("errors"):
        sys.exit("GraphQL-Fehler: " + json.dumps(payload["errors"], ensure_ascii=False))
    return payload["data"]


CAL_QUERY = """
query($login: String!) {
  user(login: $login) {
    contributionsCollection {
      restrictedContributionsCount
      contributionCalendar {
        totalContributions
        weeks { contributionDays { date contributionCount contributionLevel weekday } }
      }
    }
  }
}
"""

REPO_QUERY = """
query($login: String!, $cursor: String) {
  user(login: $login) {
    repositories(first: 100, after: $cursor, ownerAffiliations: OWNER,
                 isFork: false, orderBy: {field: PUSHED_AT, direction: DESC}) {
      pageInfo { hasNextPage endCursor }
      nodes {
        name
        createdAt
        isArchived
        languages(first: 12, orderBy: {field: SIZE, direction: DESC}) {
          edges { size node { name } }
        }
      }
    }
  }
}
"""


def fetch_calendar():
    coll = gql(CAL_QUERY, {"login": LOGIN})["user"]["contributionsCollection"]
    cal = coll["contributionCalendar"]
    weeks = [[{
        "date": d["date"],
        "count": d["contributionCount"],
        "level": LEVEL_INDEX.get(d["contributionLevel"], 0),
        "weekday": d["weekday"],
    } for d in w["contributionDays"]] for w in cal["weeks"]]
    return {"total": cal["totalContributions"],
            "restricted": coll["restrictedContributionsCount"],
            "weeks": weeks}


def fetch_repos():
    """Sprachanteile und Projekte pro Jahr.

    Sprachen werden je Repository gleich gewichtet statt nach Bytes. Rohe Bytes
    lassen ein einzelnes Repo mit gebündeltem oder generiertem Code die gesamte
    Verteilung bestimmen; so kam HTML auf 57 Prozent, obwohl nur zwei Projekte
    überwiegend HTML sind.
    """
    shares = defaultdict(float)
    per_year = defaultdict(int)
    counted = 0
    skipped = []
    cursor = None
    while True:
        conn = gql(REPO_QUERY, {"login": LOGIN, "cursor": cursor})["user"]["repositories"]
        for repo in conn["nodes"]:
            if repo["name"] in EXCLUDE:
                skipped.append(repo["name"])
                continue
            per_year[int(repo["createdAt"][:4])] += 1
            if repo["isArchived"]:
                continue
            edges = repo["languages"]["edges"]
            repo_bytes = sum(e["size"] for e in edges)
            if not repo_bytes:
                continue
            counted += 1
            for edge in edges:
                shares[edge["node"]["name"]] += edge["size"] / repo_bytes
        if not conn["pageInfo"]["hasNextPage"]:
            break
        cursor = conn["pageInfo"]["endCursor"]

    return {"ranked": sorted(shares.items(), key=lambda kv: -kv[1]),
            "repos": counted, "weight_total": float(counted) or 1.0,
            "per_year": dict(sorted(per_year.items())), "skipped": skipped}


def flat_days(weeks):
    days = sorted((d for w in weeks for d in w), key=lambda d: d["date"])
    today = datetime.date.today().isoformat()
    return [d for d in days if d["date"] <= today]


def streaks(days):
    """Längste und aktuelle Serie. Ein heute noch leerer Tag beendet die
    laufende Serie nicht, sonst stünde sie jeden Morgen auf null."""
    longest = run = 0
    for day in days:
        run = run + 1 if day["count"] > 0 else 0
        longest = max(longest, run)
    tail = days[:-1] if days and days[-1]["count"] == 0 else days[:]
    current = 0
    for day in reversed(tail):
        if day["count"] == 0:
            break
        current += 1
    return current, longest


def analyse(cal):
    days = flat_days(cal["weeks"])
    current, longest = streaks(days)
    by_weekday = defaultdict(int)
    for day in days:
        by_weekday[day["weekday"]] += day["count"]
    return {
        "total": cal["total"],
        "restricted": cal["restricted"],
        "active_days": sum(1 for d in days if d["count"] > 0),
        "best": max(days, key=lambda d: d["count"]) if days else {"count": 0, "date": ""},
        "current_streak": current,
        "longest_streak": longest,
        "by_weekday": dict(by_weekday),
    }


# --------------------------------------------------------------------------- #
# SVG
# --------------------------------------------------------------------------- #

def esc(text):
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def tokens(pal):
    return ('    svg{--bg:%s;--text:%s;--muted:%s;--rule:%s;--accent:%s;'
            '--mono:%s;--sans:%s}' % (pal["bg"], pal["text"], pal["muted"],
                                      pal["rule"], pal["accent"], MONO, SANS))


BASE_CSS = [
    '    .bg{fill:var(--bg)}',
    '    .cap{font-family:var(--mono);font-size:17px;fill:var(--muted)}',
    '    .rule{stroke:var(--rule);stroke-width:1}',
    '    .lbl{font-family:var(--mono);font-size:15px;fill:var(--muted)}',
    '    .val{font-family:var(--mono);font-size:18px;fill:var(--text)}',
    '    .big{font-family:var(--sans);font-weight:700;font-size:30px;fill:var(--text)}',
    '    .bar{fill:var(--accent)}',
]

# Aufbau beim Laden. Startwerte sind gedimmt und verkleinert, nicht null:
# so bleibt jede Grafik im ersten Frame lesbar.
LOAD_CSS = [
    '    @keyframes cell{from{opacity:.18;transform:scale(.5)}to{opacity:1;transform:scale(1)}}',
    '    @keyframes grow{from{opacity:.2;transform:scaleX(.86)}to{opacity:1;transform:scaleX(1)}}',
    '    @keyframes fade{from{opacity:.25}to{opacity:1}}',
    '    @keyframes draw{from{stroke-dashoffset:1168}to{stroke-dashoffset:0}}',
    '    .cell{transform-box:fill-box;transform-origin:center;'
    'animation:cell .5s cubic-bezier(.2,.8,.25,1) both}',
    '    .grow{transform-box:fill-box;transform-origin:left center;'
    'animation:grow .7s cubic-bezier(.2,.8,.25,1) both}',
    '    .fade{animation:fade .6s ease-out both}',
    '    .draw{stroke-dasharray:1168;animation:draw .8s cubic-bezier(.2,.8,.25,1) both}',
]

REDUCED = [
    '    @media (prefers-reduced-motion:reduce){',
    '      .cell,.grow,.fade{animation:none;opacity:1;transform:none}',
    '      .draw{animation:none;stroke-dashoffset:0}',
    '    }',
]


def head(width, height, label, title, pal, extra_css=()):
    out = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" width="%d" '
           'height="%d" role="img" aria-label="%s">' % (width, height, width, height, label),
           '  <title>%s</title>' % title, '  <style>', tokens(pal)]
    out += BASE_CSS + LOAD_CSS + list(extra_css) + REDUCED
    out += ['  </style>', '  <rect class="bg" width="%d" height="%d"/>' % (width, height)]
    return out


CELL = 18
GAP = 3
STEP = CELL + GAP
GRID_X = 92
GRID_Y = 96


def build_activity(cal, stats, pal):
    weeks = cal["weeks"]
    grid_w = len(weeks) * STEP - GAP
    grid_h = 7 * STEP - GAP
    rule2 = GRID_Y + grid_h + 44
    height = rule2 + 100

    extra = ['    .lvl{stroke:none}']
    # Spaltenweise Verzögerung: der Aufbau läuft chronologisch von links nach
    # rechts, die Bewegung erzählt also den Zeitverlauf.
    for i in range(len(weeks)):
        extra.append('    .w%d{animation-delay:%.3fs}' % (i, i * 0.013))

    out = head(W, height,
               "%s Beiträge in den letzten zwölf Monaten" % stats["total"],
               "Beiträge", pal, extra)
    add = out.append

    add('  <text class="cap fade" x="%d" y="38">Beiträge im letzten Jahr</text>' % LEFT)
    add('  <path class="rule draw" d="M%d 56H%d"/>' % (LEFT, RIGHT))

    last_month = None
    for i, week in enumerate(weeks):
        month = int(week[0]["date"][5:7])
        if month != last_month:
            x = GRID_X + i * STEP
            if x <= GRID_X + grid_w - 44:
                add('    <text class="lbl fade" x="%d" y="%d">%s</text>'
                    % (x, GRID_Y - 14, MONTHS_DE[month - 1]))
            last_month = month

    for idx, name in ((0, "Mo"), (2, "Mi"), (4, "Fr")):
        add('  <text class="lbl fade" x="%d" y="%d" text-anchor="end">%s</text>'
            % (GRID_X - 12, GRID_Y + idx * STEP + CELL - 4, name))

    today = datetime.date.today().isoformat()
    today_col = None
    for i, week in enumerate(weeks):
        for day in week:
            if day["date"] > today:
                continue
            row = WEEKDAY_ORDER.index(day["weekday"])
            add('  <rect class="cell lvl w%d" x="%d" y="%d" width="%d" height="%d" '
                'fill="%s"/>' % (i, GRID_X + i * STEP, GRID_Y + row * STEP,
                                 CELL, CELL, pal["levels"][day["level"]]))
            if day["date"] == today:
                today_col = i

    # Heute wird als Akzentmarke unter der Spalte gesetzt, nicht als
    # pulsierender Punkt: sie zeigt eine echte Position, ohne Dauerschleife.
    if today_col is not None:
        add('  <rect class="bar fade" x="%d" y="%d" width="%d" height="3"/>'
            % (GRID_X + today_col * STEP, GRID_Y + grid_h + 8, CELL))

    legend_x = RIGHT - 5 * 17 - 96
    legend_y = GRID_Y + grid_h + 34
    add('  <text class="lbl fade" x="%d" y="%d">weniger</text>' % (legend_x, legend_y))
    for i, colour in enumerate(pal["levels"]):
        add('  <rect class="fade" x="%d" y="%d" width="12" height="12" fill="%s"/>'
            % (legend_x + 68 + i * 17, legend_y - 10, colour))
    add('  <text class="lbl fade" x="%d" y="%d">mehr</text>' % (legend_x + 68 + 5 * 17 + 6, legend_y))

    add('  <path class="rule draw" d="M%d %dH%d"/>' % (LEFT, rule2, RIGHT))

    best = stats["best"]["date"]
    best_label = ("Bester Tag (%d. %s)" % (int(best[8:10]), MONTHS_DE[int(best[5:7]) - 1])
                  if best else "Bester Tag")
    items = [(stats["total"], "Beiträge"),
             (stats["current_streak"], "Serie aktuell"),
             (stats["longest_streak"], "Serie längste"),
             (stats["active_days"], "Aktive Tage"),
             (stats["best"]["count"], best_label)]
    col = (RIGHT - LEFT) // len(items)
    for i, (value, label) in enumerate(items):
        x = LEFT + i * col
        add('  <text class="big fade" x="%d" y="%d">%s</text>' % (x, rule2 + 42, esc(value)))
        add('  <text class="lbl fade" x="%d" y="%d">%s</text>' % (x, rule2 + 66, esc(label)))

    add('</svg>')
    return "\n".join(out) + "\n"


def build_languages(repos, pal, top_n=7):
    ranked = repos["ranked"][:top_n]
    total = repos["weight_total"]
    row_h = 38
    start = 84
    height = start + len(ranked) * row_h + 26

    name_x = LEFT
    bar_x = LEFT + 214
    bar_w = 790
    bar_h = 14

    extra = []
    for i in range(len(ranked)):
        extra.append('    .r%d{animation-delay:%.2fs}' % (i, 0.06 + i * 0.07))

    out = head(W, height, "Sprachverteilung über %d Repositories" % repos["repos"],
               "Sprachen", pal, extra)
    add = out.append

    add('  <text class="cap fade" x="%d" y="38">Sprachen über %d Repositories, '
        'je Repository gleich gewichtet</text>' % (LEFT, repos["repos"]))
    add('  <path class="rule draw" d="M%d 56H%d"/>' % (LEFT, RIGHT))

    for i, (name, weight) in enumerate(ranked):
        share = weight / total
        y = start + i * row_h
        # Rang über Deckkraft statt über Farbe: ein Akzent für die ganze Seite.
        opacity = max(0.34, 1.0 - i * 0.11)
        add('  <text class="val fade r%d" x="%d" y="%d">%s</text>'
            % (i, name_x, y + bar_h - 2, esc(name)))
        add('  <rect class="rule" x="%d" y="%d" width="%d" height="1" stroke="none" '
            'fill="var(--rule)"/>' % (bar_x, y + bar_h // 2, bar_w))
        add('  <rect class="bar grow r%d" x="%d" y="%d" width="%d" height="%d" '
            'opacity="%.2f"/>' % (i, bar_x, y, max(3, int(round(bar_w * share))),
                                  bar_h, opacity))
        add('  <text class="val fade r%d" x="%d" y="%d" text-anchor="end">%.1f&#37;</text>'
            % (i, RIGHT, y + bar_h - 2, share * 100))

    add('</svg>')
    return "\n".join(out) + "\n"


def build_rhythm(stats, repos, pal):
    """Zwei Panels: Verteilung über die Woche, Projekte pro Jahr."""
    row_h = 30
    start = 84
    height = start + 7 * row_h + 40

    lw_name = LEFT
    lw_bar = LEFT + 74
    lw_w = 380
    bar_h = 12

    mid = 700
    years = list(repos["per_year"].items())
    year_max = max((n for _, n in years), default=1) or 1

    extra = []
    for i in range(7):
        extra.append('    .d%d{animation-delay:%.2fs}' % (i, 0.06 + i * 0.06))
    for i in range(len(years)):
        extra.append('    .y%d{animation-delay:%.2fs}' % (i, 0.10 + i * 0.07))

    out = head(W, height, "Verteilung über die Woche und Projekte pro Jahr",
               "Rhythmus", pal, extra)
    add = out.append

    add('  <text class="cap fade" x="%d" y="38">Verteilung über die Woche</text>' % LEFT)
    add('  <text class="cap fade" x="%d" y="38">Projekte pro Jahr</text>' % mid)
    add('  <path class="rule draw" d="M%d 56H%d"/>' % (LEFT, RIGHT))

    week_max = max((stats["by_weekday"].get(wd, 0) for wd in WEEKDAY_ORDER), default=1) or 1
    for i, wd in enumerate(WEEKDAY_ORDER):
        value = stats["by_weekday"].get(wd, 0)
        y = start + i * row_h
        add('  <text class="lbl fade d%d" x="%d" y="%d">%s</text>'
            % (i, lw_name, y + bar_h, WEEKDAYS_DE[i]))
        add('  <rect class="bar grow d%d" x="%d" y="%d" width="%d" height="%d" '
            'opacity="%.2f"/>' % (i, lw_bar, y, max(3, int(round(lw_w * value / week_max))),
                                  bar_h, 0.42 + 0.58 * value / week_max))
        add('  <text class="lbl fade d%d" x="%d" y="%d" text-anchor="end">%d</text>'
            % (i, lw_bar + lw_w + 56, y + bar_h, value))

    # Jahre als Säulen: die Zeitachse liegt waagerecht, das liest sich als Verlauf.
    col_w = 46
    gap = 26
    base_y = start + 7 * row_h - 14
    max_h = 150
    for i, (year, count) in enumerate(years):
        x = mid + i * (col_w + gap)
        h = max(4, int(round(max_h * count / year_max)))
        add('  <rect class="bar fade y%d" x="%d" y="%d" width="%d" height="%d" '
            'opacity="%.2f"/>' % (i, x, base_y - h, col_w, h,
                                  0.42 + 0.58 * count / year_max))
        add('  <text class="val fade y%d" x="%d" y="%d" text-anchor="middle">%d</text>'
            % (i, x + col_w // 2, base_y - h - 12, count))
        add('  <text class="lbl fade y%d" x="%d" y="%d" text-anchor="middle">%d</text>'
            % (i, x + col_w // 2, base_y + 22, year))

    add('</svg>')
    return "\n".join(out) + "\n"


# --------------------------------------------------------------------------- #

def write(path, content):
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(content)
    print("geschrieben: %s (%d Bytes)" % (os.path.relpath(path, ROOT), len(content)))


def main():
    if not TOKEN:
        sys.exit("GH_TOKEN fehlt. Im Workflow als secrets.PROFILE_TOKEN setzen "
                 "(Scopes: read:user für die Heatmap, repo für private Sprachanteile).")
    os.makedirs(OUT_DIR, exist_ok=True)

    cal = fetch_calendar()
    stats = analyse(cal)
    repos = fetch_repos()

    print("Beiträge: %d · Serie aktuell %d · längste %d · aktive Tage %d"
          % (stats["total"], stats["current_streak"], stats["longest_streak"],
             stats["active_days"]))
    print("Sprachen: %d über %d Repositories (übersprungen: %s)"
          % (len(repos["ranked"]), repos["repos"], ", ".join(repos["skipped"]) or "keine"))
    print("Projekte pro Jahr: %s" % repos["per_year"])

    for pal in (DARK, LIGHT):
        write(os.path.join(OUT_DIR, "activity-%s.svg" % pal["name"]),
              build_activity(cal, stats, pal))
        write(os.path.join(OUT_DIR, "languages-%s.svg" % pal["name"]),
              build_languages(repos, pal))
        write(os.path.join(OUT_DIR, "rhythm-%s.svg" % pal["name"]),
              build_rhythm(stats, repos, pal))


if __name__ == "__main__":
    main()
