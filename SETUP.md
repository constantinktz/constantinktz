# Einrichtung

Diese Datei gehört nicht ins Profil. `README.md` ist die öffentliche Profilseite.

## Secret anlegen

Der Workflow braucht ein Personal Access Token als Secret `PROFILE_TOKEN`.

```bash
gh secret set PROFILE_TOKEN --repo constantinktz/constantinktz
```

`gh` fragt den Wert interaktiv ab, er landet also nicht in der Shell-History.
Alternativ im Browser unter
[Settings → Secrets and variables → Actions](https://github.com/constantinktz/constantinktz/settings/secrets/actions).

**Benötigte Scopes**

| Scope      | Wofür |
| :--        | :-- |
| `read:user` | Contribution-Kalender inklusive privater Beiträge |
| `repo`      | Sprachanteile privater Repositories |

Ohne das Secret bricht der Workflow im Schritt „Secret prüfen" mit klarer
Meldung ab, statt eine leere Heatmap zu committen.

## Warum ein PAT und nicht `GITHUB_TOKEN`

Das automatisch bereitgestellte `GITHUB_TOKEN` sieht nur öffentliche Beiträge.
Bei diesem Konto ist das ein Bruchteil der tatsächlichen Beiträge, die Heatmap
wäre praktisch leer.

Das bedeutet umgekehrt: mit dem PAT wird das zeitliche Muster der Arbeit in
privaten Repositories öffentlich sichtbar, Tage und Intensität. Repository-Namen,
Commit-Nachrichten und Inhalte nicht. Wer das nicht will, setzt im Workflow
`secrets.PROFILE_TOKEN` auf `secrets.GITHUB_TOKEN` zurück.

## Lokal testen

```bash
GH_TOKEN="$(gh auth token)" PROFILE_LOGIN=constantinktz \
  python3 scripts/generate_profile_assets.py
```

Schreibt die vier SVGs nach `assets/`. Nur Standardbibliothek, kein `pip`-Schritt.

## Fremdcode aus der Sprachstatistik halten

Geklonte OSS-Projekte verfälschen die Verteilung, weil ihr Code mitzählt.
Sie werden über `PROFILE_EXCLUDE` ausgeschlossen, kommagetrennt, Standard
`open-webui`:

```yaml
env:
  PROFILE_EXCLUDE: open-webui,noch-ein-klon
```

Zusätzlich wird jedes Repository gleich gewichtet statt nach Bytes, sonst
bestimmt ein einzelnes Repo mit gebündeltem Code die gesamte Statistik.

## Zeitplan

Täglich 04:17 UTC, zusätzlich manuell über „Run workflow". Ein Commit entsteht
nur, wenn sich die SVGs geändert haben. Da sich das Kalenderfenster täglich um
einen Tag verschiebt, ist etwa ein Commit pro Tag normal.
