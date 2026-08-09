#!/usr/bin/env python3
"""Erzeugt die animierten Profil-Assets aus echten GitHub-Daten.

Schreibt vier SVGs nach assets/:
    activity-dark.svg / activity-light.svg     Contribution-Heatmap + Kennzahlen
    languages-dark.svg / languages-light.svg   Sprachverteilung nach Codegröße

Nur Standardbibliothek, damit der Workflow ohne pip-Schritt läuft.

Datenschutz: Es werden ausschließlich Aggregate veröffentlicht — Beiträge pro
Tag und Bytes pro Sprache. Repository-Namen, Commit-Nachrichten und Inhalte
privater Repos verlassen den Workflow nicht.

Gestaltungsregel für alle Animationen: GitHub skaliert README-Bilder auf ~846px
(0.66x), und viele Kontexte zeigen nur den ersten Frame. Deshalb ist jeder
inhaltstragende Teil ohne Animation vollständig sichtbar; Bewegung läuft
ausschließlich als Endlosschleife, deren 0%-Zustand der Ruhezustand ist.
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

MONTHS_DE = ["Jan", "Feb", "Mär", "Apr", "Mai", "Jun",
             "Jul", "Aug", "Sep", "Okt", "Nov", "Dez"]

LEVEL_INDEX = {
    "NONE": 0,
    "FIRST_QUARTILE": 1,
    "SECOND_QUARTILE": 2,
    "THIRD_QUARTILE": 3,
    "FOURTH_QUARTILE": 4,
}

DARK = {
    "name": "dark",
    "bg": "#0D1117", "panel": "#161B22", "line": "#30363D",
    "text": "#E6EDF3", "muted": "#8B949E",
    "accent": "#39D353", "accent2": "#2F81F7",
    "levels": ["#161B22", "#0E4429", "#006D32", "#26A641", "#39D353"],
    "cell_stroke": "#1B2028",
    "shimmer": "#FFFFFF", "shimmer_alpha": "0.13",
}

LIGHT = {
    "name": "light",
    "bg": "#FFFFFF", "panel": "#F6F8FA", "line": "#D0D7DE",
    "text": "#1F2328", "muted": "#59636E",
    "accent": "#1A7F37", "accent2": "#0969DA",
    "levels": ["#EBEDF0", "#9BE9A8", "#40C463", "#30A14E", "#216E39"],
    "cell_stroke": "#E4E8EC",
    "shimmer": "#1A7F37", "shimmer_alpha": "0.16",
}

FONT_MONO = ('ui-monospace,SFMono-Regular,&quot;SF Mono&quot;,Menlo,Consolas,'
             '&quot;Liberation Mono&quot;,monospace')
FONT_SANS = ('ui-sans-serif,-apple-system,BlinkMacSystemFont,&quot;Segoe UI&quot;,'
             'Roboto,Helvetica,Arial,sans-serif')


# --------------------------------------------------------------------------- #
# Datenbeschaffung
# --------------------------------------------------------------------------- #

def gql(query, variables):
    body = json.dumps({"query": query, "variables": variables}).encode("utf-8")
    req = urllib.request.Request(
        API, data=body,
        headers={
            "Authorization": "Bearer " + TOKEN,
            "Content-Type": "application/json",
            "User-Agent": "profile-asset-generator",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            payload = json.load(resp)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:400]
        sys.exit("HTTP %s von der GitHub-API: %s" % (exc.code, detail))
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
        weeks {
          contributionDays { date contributionCount contributionLevel weekday }
        }
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
          edges { size node { name color } }
        }
      }
    }
  }
}
"""

# Klone fremder Projekte verfälschen die Sprachverteilung, weil sie fremden
# Code mitzählen. open-webui ist ein Klon eines OSS-Projekts (Svelte) und wird
# deshalb übersprungen. Weitere Namen per PROFILE_EXCLUDE, kommagetrennt.
EXCLUDE = {n.strip() for n in (os.environ.get("PROFILE_EXCLUDE") or "open-webui").split(",")
           if n.strip()}


def fetch_calendar():
    data = gql(CAL_QUERY, {"login": LOGIN})
    coll = data["user"]["contributionsCollection"]
    cal = coll["contributionCalendar"]
    weeks = []
    for week in cal["weeks"]:
        days = []
        for day in week["contributionDays"]:
            days.append({
                "date": day["date"],
                "count": day["contributionCount"],
                "level": LEVEL_INDEX.get(day["contributionLevel"], 0),
                "weekday": day["weekday"],
            })
        weeks.append(days)
    return {
        "total": cal["totalContributions"],
        "restricted": coll["restrictedContributionsCount"],
        "weeks": weeks,
    }


def fetch_languages():
    """Gewichtet jedes Repository gleich statt nach Bytes.

    Rohe Bytes lassen ein einzelnes Repo mit gebündeltem oder generiertem Code
    die gesamte Verteilung bestimmen — bei 43 Repos kam so HTML auf 57%, obwohl
    nur zwei Projekte überwiegend HTML sind. Deshalb wird pro Repository der
    Anteil je Sprache berechnet und über alle Repositories gemittelt.
    """
    shares = defaultdict(float)
    colors = {}
    counted = 0
    skipped = []
    cursor = None
    while True:
        data = gql(REPO_QUERY, {"login": LOGIN, "cursor": cursor})
        conn = data["user"]["repositories"]
        for repo in conn["nodes"]:
            if repo["isArchived"]:
                continue
            if repo["name"] in EXCLUDE:
                skipped.append(repo["name"])
                continue
            edges = repo["languages"]["edges"]
            repo_bytes = sum(edge["size"] for edge in edges)
            if not repo_bytes:
                continue
            counted += 1
            for edge in edges:
                name = edge["node"]["name"]
                shares[name] += edge["size"] / repo_bytes
                if edge["node"]["color"]:
                    colors[name] = edge["node"]["color"]
        if not conn["pageInfo"]["hasNextPage"]:
            break
        cursor = conn["pageInfo"]["endCursor"]

    ranked = sorted(shares.items(), key=lambda kv: -kv[1])
    return {"ranked": ranked, "colors": colors, "repos": counted,
            "weight_total": float(counted) or 1.0, "skipped": skipped}


# --------------------------------------------------------------------------- #
# Auswertung
# --------------------------------------------------------------------------- #

def flat_days(weeks):
    days = [day for week in weeks for day in week]
    days.sort(key=lambda d: d["date"])
    today = datetime.date.today().isoformat()
    return [d for d in days if d["date"] <= today]


def streaks(days):
    """Längste Serie und aktuelle Serie. Ein heute noch leerer Tag beendet die
    laufende Serie nicht — sonst wäre sie jeden Morgen auf 0."""
    longest = run = 0
    for day in days:
        run = run + 1 if day["count"] > 0 else 0
        longest = max(longest, run)

    tail = days[:]
    if tail and tail[-1]["count"] == 0:
        tail = tail[:-1]
    current = 0
    for day in reversed(tail):
        if day["count"] == 0:
            break
        current += 1
    return current, longest


def analyse(cal):
    days = flat_days(cal["weeks"])
    active = sum(1 for d in days if d["count"] > 0)
    best = max(days, key=lambda d: d["count"]) if days else {"count": 0, "date": ""}
    current, longest = streaks(days)
    return {
        "total": cal["total"],
        "restricted": cal["restricted"],
        "active_days": active,
        "best": best,
        "current_streak": current,
        "longest_streak": longest,
    }


# --------------------------------------------------------------------------- #
# SVG-Bau
# --------------------------------------------------------------------------- #

def esc(text):
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


CELL = 18          # Zellkante
GAP = 4            # Abstand zwischen Zellen
STEP = CELL + GAP  # 22 pro Woche/Zeile
GRID_X = 58        # Beginn des Rasters
GRID_Y = 74        # Beginn des Rasters
WIDTH = 1280


def build_activity(cal, stats, pal):
    weeks = cal["weeks"]
    grid_w = len(weeks) * STEP - GAP
    grid_h = 7 * STEP - GAP
    stats_y = GRID_Y + grid_h + 62
    height = stats_y + 74

    out = []
    add = out.append
    add('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" '
        'width="%d" height="%d" role="img" aria-label="%s Beiträge in den letzten '
        '12 Monaten">' % (WIDTH, height, WIDTH, height, stats["total"]))
    add('  <title>Contribution-Heatmap</title>')

    add('  <style>')
    add('    svg{--bg:%s;--panel:%s;--line:%s;--text:%s;--muted:%s;--accent:%s;'
        '--mono:%s;--sans:%s}' % (pal["bg"], pal["panel"], pal["line"], pal["text"],
                                  pal["muted"], pal["accent"], FONT_MONO, FONT_SANS))
    add('    .bg{fill:var(--bg)}')
    add('    .cap{font-family:var(--mono);font-size:17px;fill:var(--muted);letter-spacing:1.6px}')
    add('    .mon{font-family:var(--sans);font-size:17px;fill:var(--muted)}')
    add('    .wd{font-family:var(--sans);font-size:15px;fill:var(--muted)}')
    add('    .cell{stroke:%s;stroke-width:1;rx:4}' % pal["cell_stroke"])
    add('    .num{font-family:var(--sans);font-weight:700;font-size:34px;fill:var(--text)}')
    add('    .num-a{fill:var(--accent)}')
    add('    .lbl{font-family:var(--mono);font-size:15px;fill:var(--muted);letter-spacing:.6px}')
    add('    .lgd{font-family:var(--sans);font-size:15px;fill:var(--muted)}')
    add('    .sep{stroke:var(--line);stroke-width:1.5}')
    # Ambience: Schimmer wandert endlos über das Raster, 0% = außerhalb links
    add('    .shimmer{fill:url(#shimmer);animation:sweep 7.5s cubic-bezier(.45,0,.55,1) infinite}')
    add('    @keyframes sweep{0%%{transform:translateX(-260px)}62%%,100%%'
        '{transform:translateX(%dpx)}}' % (grid_w + 260))
    add('    .today{fill:none;stroke:var(--accent);stroke-width:2;rx:5;'
        'animation:beat 2.2s ease-in-out infinite}')
    add('    @keyframes beat{0%,100%{opacity:1}50%{opacity:.25}}')
    add('    @media (prefers-reduced-motion:reduce){.shimmer{display:none}.today{animation:none}}')
    add('  </style>')

    add('  <defs>')
    add('    <linearGradient id="shimmer" x1="0" y1="0" x2="1" y2="0">')
    add('      <stop offset="0" stop-color="%s" stop-opacity="0"/>' % pal["shimmer"])
    add('      <stop offset=".5" stop-color="%s" stop-opacity="%s"/>'
        % (pal["shimmer"], pal["shimmer_alpha"]))
    add('      <stop offset="1" stop-color="%s" stop-opacity="0"/>' % pal["shimmer"])
    add('    </linearGradient>')
    add('    <clipPath id="gridclip"><rect x="%d" y="%d" width="%d" height="%d"/></clipPath>'
        % (GRID_X, GRID_Y, grid_w, grid_h))
    add('  </defs>')

    add('  <rect class="bg" width="%d" height="%d"/>' % (WIDTH, height))
    add('  <text class="cap" x="%d" y="34">BEITRÄGE · LETZTE 12 MONATE</text>' % GRID_X)

    # Monatsbeschriftung: Label bei der ersten Woche, in der ein neuer Monat beginnt
    add('  <g>')
    last_month = None
    for i, week in enumerate(weeks):
        first = week[0]["date"]
        month = int(first[5:7])
        if month != last_month:
            x = GRID_X + i * STEP
            if x <= GRID_X + grid_w - 40:
                add('    <text class="mon" x="%d" y="%d">%s</text>'
                    % (x, GRID_Y - 12, MONTHS_DE[month - 1]))
            last_month = month
    add('  </g>')

    # Wochentage: nur Mo, Mi, Fr wie bei GitHub
    add('  <g>')
    for idx, label in ((1, "Mo"), (3, "Mi"), (5, "Fr")):
        y = GRID_Y + idx * STEP + CELL - 4
        add('    <text class="wd" x="%d" y="%d" text-anchor="end">%s</text>'
            % (GRID_X - 12, y, label))
    add('  </g>')

    # Raster
    today = datetime.date.today().isoformat()
    today_rect = None
    add('  <g>')
    for i, week in enumerate(weeks):
        for day in week:
            if day["date"] > today:
                continue
            x = GRID_X + i * STEP
            y = GRID_Y + day["weekday"] * STEP
            add('    <rect class="cell" x="%d" y="%d" width="%d" height="%d" fill="%s"/>'
                % (x, y, CELL, CELL, pal["levels"][day["level"]]))
            if day["date"] == today:
                today_rect = (x, y)
    add('  </g>')

    add('  <g clip-path="url(#gridclip)">')
    add('    <rect class="shimmer" x="%d" y="%d" width="260" height="%d"/>'
        % (GRID_X, GRID_Y, grid_h))
    add('  </g>')

    if today_rect:
        add('  <rect class="today" x="%d" y="%d" width="%d" height="%d"/>'
            % (today_rect[0] - 3, today_rect[1] - 3, CELL + 6, CELL + 6))

    # Legende rechts unter dem Raster
    legend_x = GRID_X + grid_w - 210
    legend_y = GRID_Y + grid_h + 26
    add('  <text class="lgd" x="%d" y="%d">Weniger</text>' % (legend_x, legend_y))
    for i, colour in enumerate(pal["levels"]):
        add('  <rect x="%d" y="%d" width="13" height="13" rx="3" fill="%s" stroke="%s"/>'
            % (legend_x + 62 + i * 17, legend_y - 11, colour, pal["cell_stroke"]))
    add('  <text class="lgd" x="%d" y="%d">Mehr</text>' % (legend_x + 156, legend_y))

    add('  <path class="sep" d="M%d %dH%d"/>'
        % (GRID_X, stats_y - 34, GRID_X + grid_w))

    # Kennzahlen
    best_date = stats["best"]["date"]
    if best_date:
        best_label = "%s. %s" % (int(best_date[8:10]), MONTHS_DE[int(best_date[5:7]) - 1])
    else:
        best_label = "—"
    items = [
        (str(stats["total"]), "BEITRÄGE", True),
        (str(stats["current_streak"]), "TAGE SERIE AKTUELL", False),
        (str(stats["longest_streak"]), "TAGE SERIE LÄNGSTE", False),
        (str(stats["active_days"]), "AKTIVE TAGE", False),
        ("%d" % stats["best"]["count"], "BESTER TAG · " + best_label.upper(), False),
    ]
    col = grid_w // len(items)
    for i, (value, label, highlight) in enumerate(items):
        x = GRID_X + i * col
        cls = "num num-a" if highlight else "num"
        add('  <text class="%s" x="%d" y="%d">%s</text>' % (cls, x, stats_y, esc(value)))
        add('  <text class="lbl" x="%d" y="%d">%s</text>' % (x, stats_y + 24, esc(label)))

    add('</svg>')
    return "\n".join(out) + "\n"


def build_languages(langs, pal, top_n=7):
    ranked = langs["ranked"][:top_n]
    total = langs["weight_total"]
    row_h = 40
    head_y = 34
    start_y = 74
    height = start_y + len(ranked) * row_h + 26

    name_x = GRID_X
    bar_x = GRID_X + 190
    bar_w = 830
    pct_x = bar_x + bar_w + 84

    out = []
    add = out.append
    add('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" width="%d" '
        'height="%d" role="img" aria-label="Sprachverteilung nach Codegröße">'
        % (WIDTH, height, WIDTH, height))
    add('  <title>Sprachverteilung</title>')

    add('  <style>')
    add('    svg{--bg:%s;--panel:%s;--line:%s;--text:%s;--muted:%s;--mono:%s;--sans:%s}'
        % (pal["bg"], pal["panel"], pal["line"], pal["text"], pal["muted"],
           FONT_MONO, FONT_SANS))
    add('    .bg{fill:var(--bg)}')
    add('    .cap{font-family:var(--mono);font-size:17px;fill:var(--muted);letter-spacing:1.6px}')
    add('    .lang{font-family:var(--mono);font-size:19px;fill:var(--text)}')
    add('    .pct{font-family:var(--mono);font-size:18px;fill:var(--muted)}')
    add('    .track{fill:var(--panel);stroke:var(--line);stroke-width:1}')
    # Ambience: ein Glanz läuft versetzt durch die Balken, Balken selbst statisch
    add('    .glow{fill:url(#gl);animation:run 6.5s cubic-bezier(.45,0,.55,1) infinite}')
    add('    @keyframes run{0%%{transform:translateX(-200px)}70%%,100%%'
        '{transform:translateX(%dpx)}}' % (bar_w + 200))
    for i in range(len(ranked)):
        add('    .g%d{animation-delay:%.2fs}' % (i, i * 0.16))
    add('    @media (prefers-reduced-motion:reduce){.glow{display:none}}')
    add('  </style>')

    add('  <defs>')
    add('    <linearGradient id="gl" x1="0" y1="0" x2="1" y2="0">')
    add('      <stop offset="0" stop-color="%s" stop-opacity="0"/>' % pal["shimmer"])
    add('      <stop offset=".5" stop-color="%s" stop-opacity="%s"/>'
        % (pal["shimmer"], pal["shimmer_alpha"]))
    add('      <stop offset="1" stop-color="%s" stop-opacity="0"/>' % pal["shimmer"])
    add('    </linearGradient>')
    add('  </defs>')

    add('  <rect class="bg" width="%d" height="%d"/>' % (WIDTH, height))
    add('  <text class="cap" x="%d" y="%d">SPRACHEN · %d REPOSITORIES · JE REPOSITORY '
        'GLEICH GEWICHTET</text>' % (name_x, head_y, langs["repos"]))

    for i, (name, size) in enumerate(ranked):
        share = size / total
        y = start_y + i * row_h
        fill_w = max(4, int(round(bar_w * share)))
        colour = langs["colors"].get(name, pal["accent"])
        add('  <text class="lang" x="%d" y="%d">%s</text>' % (name_x, y + 15, esc(name)))
        add('  <rect class="track" x="%d" y="%d" width="%d" height="20" rx="10"/>'
            % (bar_x, y, bar_w))
        add('  <rect x="%d" y="%d" width="%d" height="20" rx="10" fill="%s"/>'
            % (bar_x, y, fill_w, colour))
        add('  <clipPath id="c%d"><rect x="%d" y="%d" width="%d" height="20" rx="10"/></clipPath>'
            % (i, bar_x, y, fill_w))
        add('  <g clip-path="url(#c%d)"><rect class="glow g%d" x="%d" y="%d" width="200" '
            'height="20"/></g>' % (i, i, bar_x, y))
        add('  <text class="pct" x="%d" y="%d" text-anchor="end">%.1f&#37;</text>'
            % (pct_x, y + 15, share * 100))

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
    langs = fetch_languages()

    print("Beiträge: %d (davon %d aus privaten Repos) · Serie aktuell %d · längste %d"
          % (stats["total"], stats["restricted"], stats["current_streak"],
             stats["longest_streak"]))
    print("Sprachen: %d über %d Repositories (übersprungen: %s)"
          % (len(langs["ranked"]), langs["repos"],
             ", ".join(langs["skipped"]) or "keine"))

    for pal in (DARK, LIGHT):
        write(os.path.join(OUT_DIR, "activity-%s.svg" % pal["name"]),
              build_activity(cal, stats, pal))
        write(os.path.join(OUT_DIR, "languages-%s.svg" % pal["name"]),
              build_languages(langs, pal))


if __name__ == "__main__":
    main()
