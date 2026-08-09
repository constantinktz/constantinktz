#!/usr/bin/env python3
"""Erzeugt die Profil-Assets aus echten GitHub-Daten.

Schreibt vier SVGs nach assets/:
    activity-dark.svg   / activity-light.svg     Heatmap + Kennzahlen
    languages-dark.svg  / languages-light.svg    Sprachverteilung

Nur Standardbibliothek, damit der Workflow ohne pip-Schritt läuft.

Datenschutz: Es werden ausschließlich Aggregate veröffentlicht: Beiträge pro
Tag und Anteile pro Sprache. Repository-Namen, Commit-Nachrichten und Inhalte
privater Repos verlassen den Workflow nicht.

Gestaltung: Swiss-technisch. Strenges Raster, scharfe Kanten (kein Radius),
Haarlinien nur als Struktur, ein Akzent, Rang über Deckkraft statt über Farbe.

Motion: Die Heatmap wird von links nach rechts durch ein Portal aufgezogen. Eine
helle Kante mit geblühtem Kern und nachlaufendem Schein wandert über das Raster,
dahinter erscheinen die Beiträge. Der Zyklus dauert 14s: 4.2s Aufzug, rund 8s
Standzeit, dann ein weiches Ausblenden und von vorn. Die Schleife ist nötig, weil
GitHub intern über Turbo navigiert und das Bild dabei nicht neu lädt, eine
einmalige Animation also nie wieder zu sehen wäre.

Zwei Ebenen machen das möglich: das leere Kalendergitter liegt dauerhaft da, die
Beitragsdaten stecken in einer zweiten Ebene hinter einem wachsenden clipPath.
Vor der Kante ist deshalb nichts von den Daten zu sehen, und wo nur der erste
Frame gerendert wird (Vorschauen, Social-Cards), steht trotzdem ein intakter
Kalender statt einer leeren Fläche.
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
    """Sprachanteile, je Repository gleich gewichtet statt nach Bytes.

    Rohe Bytes lassen ein einzelnes Repo mit gebündeltem oder generiertem Code
    die gesamte Verteilung bestimmen; so kam HTML auf 57 Prozent, obwohl nur
    zwei Projekte überwiegend HTML sind.
    """
    shares = defaultdict(float)
    counted = 0
    skipped = []
    cursor = None
    while True:
        conn = gql(REPO_QUERY, {"login": LOGIN, "cursor": cursor})["user"]["repositories"]
        for repo in conn["nodes"]:
            if repo["name"] in EXCLUDE:
                skipped.append(repo["name"])
                continue
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
            "skipped": skipped}


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
    return {
        "total": cal["total"],
        "restricted": cal["restricted"],
        "active_days": sum(1 for d in days if d["count"] > 0),
        "best": max(days, key=lambda d: d["count"]) if days else {"count": 0, "date": ""},
        "current_streak": current,
        "longest_streak": longest,
    }


# --------------------------------------------------------------------------- #
# SVG
# --------------------------------------------------------------------------- #

def esc(text):
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


CELL = 18
GAP = 3
STEP = CELL + GAP
GRID_X = 92
GRID_Y = 96


def build_activity(cal, stats, pal):
    weeks = cal["weeks"]
    grid_w = len(weeks) * STEP - GAP
    grid_h = 7 * STEP - GAP
    portal_y = GRID_Y - 30
    portal_h = grid_h + 30
    rule2 = GRID_Y + grid_h + 44
    height = rule2 + 100
    span = RIGHT - LEFT

    out = ['<svg xmlns="http://www.w3.org/2000/svg" '
           'xmlns:xlink="http://www.w3.org/1999/xlink" '
           'viewBox="0 0 %d %d" width="%d" height="%d" role="img" '
           'aria-label="%d Beiträge in den letzten zwölf Monaten">'
           % (W, height, W, height, stats["total"]),
           '  <title>Beiträge</title>',
           '  <style>',
           '    svg{--bg:%s;--text:%s;--muted:%s;--rule:%s;--accent:%s;--mono:%s;--sans:%s}'
           % (pal["bg"], pal["text"], pal["muted"], pal["rule"], pal["accent"], MONO, SANS),
           '    .bg{fill:var(--bg)}',
           '    .cap{font-family:var(--mono);font-size:17px;fill:var(--muted)}',
           '    .mon{font-family:var(--mono);font-size:15px;fill:var(--muted)}',
           '    .lbl{font-family:var(--mono);font-size:15px;fill:var(--muted)}',
           '    .big{font-family:var(--sans);font-weight:700;font-size:30px;fill:var(--text)}',
           '    .rule{stroke:var(--rule);stroke-width:1}',
           '    .mark{fill:var(--accent)}',
           '',
           '    /* Portal: der Aufzug wächst von links nach rechts und legt die',
           '       Beitragsdaten frei. Vorher steht nur das leere Gitter da, die',
           '       Daten selbst sind noch nicht sichtbar.',
           '       Das width-Attribut trägt die Endbreite, deshalb zeigt',
           '       prefers-reduced-motion sofort alles. */',
           '    /* Der Reveal läuft in Schleife über 14s: 4.2s Aufzug, danach',
           '       rund 8s Standzeit, dann blendet die Datenebene weich aus und',
           '       der Aufzug beginnt neu. Die Heatmap ist damit den Großteil der',
           '       Zeit vollständig lesbar, und die Bewegung kommt auch dann',
           '       zurück, wenn GitHub intern über Turbo navigiert und das Bild',
           '       nicht neu lädt. */',
           '    @keyframes portal{',
           '      0%{width:0;animation-timing-function:cubic-bezier(.32,.02,.28,1)}',
           '      30%%{width:%dpx}' % grid_w,
           '      94%%{width:%dpx}' % grid_w,
           '      94.5%{width:0}',
           '      100%{width:0}}',
           '    #pr{animation:portal 14s linear infinite}',
           '    @keyframes veil{0%,88%{opacity:1}94%{opacity:0}'
           '94.5%,99.9%{opacity:0}100%{opacity:1}}',
           '    .data{animation:veil 14s linear infinite}',
           '    /* Kante und Aufzugsgrenze teilen Zyklus und Kurve, laufen also',
           '       exakt synchron. */',
           '    @keyframes edge{',
           '      0%{transform:translateX(0);opacity:0;'
           'animation-timing-function:cubic-bezier(.32,.02,.28,1)}',
           '      1.2%{opacity:1}',
           '      27%{opacity:1}',
           '      30%%{transform:translateX(%dpx);opacity:0}' % grid_w,
           '      100%%{transform:translateX(%dpx);opacity:0}}' % grid_w,
           '    .edge{animation:edge 14s linear infinite}',
           '    @keyframes fade{from{opacity:.25}to{opacity:1}}',
           '    @keyframes draw{from{stroke-dashoffset:%d}to{stroke-dashoffset:0}}' % span,
           '    .f{animation:fade .7s ease-out both}',
           '    .d{stroke-dasharray:%d;animation:draw .9s cubic-bezier(.2,.8,.25,1) both}' % span,
           '    .s1{animation-delay:3.9s}.s2{animation-delay:4.02s}.s3{animation-delay:4.14s}',
           '    .s4{animation-delay:4.26s}.s5{animation-delay:4.38s}',
           '',
           '    @media (prefers-reduced-motion:reduce){',
           '      #pr{animation:none}\n      .data{animation:none;opacity:1}',
           '      .edge{display:none}',
           '      .f{animation:none;opacity:1}',
           '      .d{animation:none;stroke-dashoffset:0}',
           '    }',
           '  </style>']
    add = out.append

    # Zwei Ebenen: das leere Gitter liegt dauerhaft da, die eigentlichen
    # Beiträge kommen durch das Portal. Dadurch ist vorher nichts von den Daten
    # zu sehen, und ein erster Frame zeigt trotzdem einen intakten Kalender
    # statt einer leeren Fläche.
    add('  <defs>')
    today = datetime.date.today().isoformat()

    add('    <g id="skel">')
    last_month = None
    for i, week in enumerate(weeks):
        month = int(week[0]["date"][5:7])
        if month != last_month:
            x = GRID_X + i * STEP
            if x <= GRID_X + grid_w - 44:
                add('      <text class="mon" x="%d" y="%d">%s</text>'
                    % (x, GRID_Y - 14, MONTHS_DE[month - 1]))
            last_month = month
    for i, week in enumerate(weeks):
        for day in week:
            if day["date"] > today:
                continue
            row = WEEKDAY_ORDER.index(day["weekday"])
            add('      <rect x="%d" y="%d" width="%d" height="%d" fill="%s"/>'
                % (GRID_X + i * STEP, GRID_Y + row * STEP, CELL, CELL,
                   pal["levels"][0]))
    add('    </g>')

    today_col = None
    add('    <g id="data">')
    for i, week in enumerate(weeks):
        for day in week:
            if day["date"] > today:
                continue
            if day["date"] == today:
                today_col = i
            if day["level"] == 0:
                continue
            row = WEEKDAY_ORDER.index(day["weekday"])
            add('      <rect x="%d" y="%d" width="%d" height="%d" fill="%s"/>'
                % (GRID_X + i * STEP, GRID_Y + row * STEP, CELL, CELL,
                   pal["levels"][day["level"]]))
    add('    </g>')

    add('    <clipPath id="portal">')
    add('      <rect id="pr" x="%d" y="%d" width="%d" height="%d"/>'
        % (GRID_X, portal_y, grid_w, portal_h))
    add('    </clipPath>')
    add('    <linearGradient id="glow" x1="0" y1="0" x2="1" y2="0">')
    add('      <stop offset="0" stop-color="%s" stop-opacity="0"/>' % pal["accent"])
    add('      <stop offset=".72" stop-color="%s" stop-opacity=".30"/>' % pal["accent"])
    add('      <stop offset="1" stop-color="%s" stop-opacity=".62"/>' % pal["accent"])
    add('    </linearGradient>')
    add('    <filter id="bloom" x="-400%" y="-25%" width="900%" height="150%">')
    add('      <feGaussianBlur stdDeviation="7"/>')
    add('    </filter>')
    add('  </defs>')

    add('  <rect class="bg" width="%d" height="%d"/>' % (W, height))
    add('  <text class="cap f" x="%d" y="38">Beiträge im letzten Jahr</text>' % LEFT)
    add('  <path class="rule d" d="M%d 56H%d"/>' % (LEFT, RIGHT))

    for idx, name in ((0, "Mo"), (2, "Mi"), (4, "Fr")):
        add('  <text class="lbl f" x="%d" y="%d" text-anchor="end">%s</text>'
            % (GRID_X - 12, GRID_Y + idx * STEP + CELL - 4, name))

    add('  <use href="#skel" xlink:href="#skel"/>')
    add('  <g class="data" clip-path="url(#portal)">'
        '<use href="#data" xlink:href="#data"/></g>')

    # Portalkante: breiter nachlaufender Schein, geblühter Kern und harte Linie
    # genau auf der Aufzugsgrenze.
    add('  <g class="edge">')
    add('    <rect x="%d" y="%d" width="170" height="%d" fill="url(#glow)"/>'
        % (GRID_X - 170, portal_y, portal_h))
    add('    <rect class="mark" x="%d" y="%d" width="4" height="%d" filter="url(#bloom)"/>'
        % (GRID_X - 1, portal_y - 6, portal_h + 12))
    add('    <rect class="mark" x="%d" y="%d" width="2" height="%d"/>'
        % (GRID_X, portal_y - 6, portal_h + 12))
    add('  </g>')

    if today_col is not None:
        add('  <rect class="mark f s5" x="%d" y="%d" width="%d" height="3"/>'
            % (GRID_X + today_col * STEP, GRID_Y + grid_h + 8, CELL))

    legend_x = RIGHT - 5 * 17 - 96
    legend_y = GRID_Y + grid_h + 34
    add('  <text class="lbl f s5" x="%d" y="%d">weniger</text>' % (legend_x, legend_y))
    for i, colour in enumerate(pal["levels"]):
        add('  <rect class="f s5" x="%d" y="%d" width="12" height="12" fill="%s"/>'
            % (legend_x + 68 + i * 17, legend_y - 10, colour))
    add('  <text class="lbl f s5" x="%d" y="%d">mehr</text>'
        % (legend_x + 68 + 5 * 17 + 6, legend_y))

    add('  <path class="rule d" d="M%d %dH%d"/>' % (LEFT, rule2, RIGHT))

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
        add('  <text class="big f s%d" x="%d" y="%d">%s</text>'
            % (i + 1, x, rule2 + 42, esc(value)))
        add('  <text class="lbl f s%d" x="%d" y="%d">%s</text>'
            % (i + 1, x, rule2 + 66, esc(label)))

    add('</svg>')
    return "\n".join(out) + "\n"


def build_languages(repos, pal, top_n=7):
    ranked = repos["ranked"][:top_n]
    total = repos["weight_total"]
    row_h = 38
    start = 84
    height = start + len(ranked) * row_h + 26
    span = RIGHT - LEFT

    bar_x = LEFT + 214
    bar_w = 790
    bar_h = 14

    out = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" width="%d" '
           'height="%d" role="img" aria-label="Sprachverteilung über %d Repositories">'
           % (W, height, W, height, repos["repos"]),
           '  <title>Sprachen</title>',
           '  <style>',
           '    svg{--bg:%s;--text:%s;--muted:%s;--rule:%s;--accent:%s;--mono:%s}'
           % (pal["bg"], pal["text"], pal["muted"], pal["rule"], pal["accent"], MONO),
           '    .bg{fill:var(--bg)}',
           '    .cap{font-family:var(--mono);font-size:17px;fill:var(--muted)}',
           '    .val{font-family:var(--mono);font-size:18px;fill:var(--text)}',
           '    .rule{stroke:var(--rule);stroke-width:1}',
           '    .bar{fill:var(--accent)}',
           '    @keyframes grow{from{opacity:.2;transform:scaleX(.86)}'
           'to{opacity:1;transform:scaleX(1)}}',
           '    @keyframes fade{from{opacity:.25}to{opacity:1}}',
           '    @keyframes draw{from{stroke-dashoffset:%d}to{stroke-dashoffset:0}}' % span,
           '    .grow{transform-box:fill-box;transform-origin:left center;'
           'animation:grow .7s cubic-bezier(.2,.8,.25,1) both}',
           '    .f{animation:fade .6s ease-out both}',
           '    .d{stroke-dasharray:%d;animation:draw .9s cubic-bezier(.2,.8,.25,1) both}' % span]
    for i in range(len(ranked)):
        out.append('    .r%d{animation-delay:%.2fs}' % (i, 0.06 + i * 0.07))
    out += ['    @media (prefers-reduced-motion:reduce){',
            '      .grow,.f{animation:none;opacity:1;transform:none}',
            '      .d{animation:none;stroke-dashoffset:0}',
            '    }',
            '  </style>',
            '  <rect class="bg" width="%d" height="%d"/>' % (W, height)]
    add = out.append

    add('  <text class="cap f" x="%d" y="38">Sprachen über %d Repositories, '
        'je Repository gleich gewichtet</text>' % (LEFT, repos["repos"]))
    add('  <path class="rule d" d="M%d 56H%d"/>' % (LEFT, RIGHT))

    for i, (name, weight) in enumerate(ranked):
        share = weight / total
        y = start + i * row_h
        # Rang über Deckkraft statt über Farbe: ein Akzent für die ganze Seite.
        opacity = max(0.34, 1.0 - i * 0.11)
        add('  <text class="val f r%d" x="%d" y="%d">%s</text>'
            % (i, LEFT, y + bar_h - 2, esc(name)))
        add('  <rect x="%d" y="%d" width="%d" height="1" fill="var(--rule)"/>'
            % (bar_x, y + bar_h // 2, bar_w))
        add('  <rect class="bar grow r%d" x="%d" y="%d" width="%d" height="%d" '
            'opacity="%.2f"/>' % (i, bar_x, y, max(3, int(round(bar_w * share))),
                                  bar_h, opacity))
        add('  <text class="val f r%d" x="%d" y="%d" text-anchor="end">%.1f&#37;</text>'
            % (i, RIGHT, y + bar_h - 2, share * 100))

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

    for pal in (DARK, LIGHT):
        write(os.path.join(OUT_DIR, "activity-%s.svg" % pal["name"]),
              build_activity(cal, stats, pal))
        write(os.path.join(OUT_DIR, "languages-%s.svg" % pal["name"]),
              build_languages(repos, pal))


if __name__ == "__main__":
    main()
