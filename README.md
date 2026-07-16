# bsdata-importer

Unit data importer for wargaming apps. Three sources: BSData repositories (stats, costs, keywords), the scraped official 40k MFM (points land faster than `.cat` updates) and PDFs (MFM / Balance Dataslate / base size guides). Results go into SQLite (`data.db`), with JSON export.

## Installation

As a package in a project (warscore, unitle, pilezero, …):

```bash
pip install "bsdata-importer @ git+https://github.com/ogdowski/bsdata-importer.git"
# with the PDF parser (pdfplumber):
pip install "bsdata-importer[pdf] @ git+https://github.com/ogdowski/bsdata-importer.git"
```

or an entry in the project's `requirements.txt` / `pyproject.toml`:

```
bsdata-importer @ git+https://github.com/ogdowski/bsdata-importer.git
```

After installation the `bsdata-importer` command is available (interchangeable with `python bsdata_importer.py` from a checkout). The script reads `games.json` and `import_config.json` from the current project directory first, then from its own location — so every app can carry its own import configuration.

The module can also be imported directly: `from bsdata_importer import load_games, parse_catalogue, ...`.

## Supported games (built-in)

| key | game | repo |
|---|---|---|
| `40k` | Warhammer 40,000 (11th) | BSData/wh40k-11e (JSON format!) |
| `40k-10e` | Warhammer 40,000 (10th) | BSData/wh40k-10e |
| `aos` | Age of Sigmar (4th) | BSData/age-of-sigmar-4th |
| `heresy` | Horus Heresy (3rd) | BSData/horus-heresy-3rd-edition |
| `heresy-2e` | Horus Heresy (2nd) | BSData/horus-heresy-2nd-edition |
| `killteam` | Kill Team | BSData/wh40k-killteam (branch `master`) |
| `oldworld` | The Old World | Birddie721/TOW (BSData does not host TOW; community repo) |

## Typical workflow

```bash
python bsdata_importer.py fetch 40k          # stats + costs from BSData
python bsdata_importer.py mfm-live           # current 40k points (scraped official MFM)
python bsdata_importer.py pdf dataslate.pdf --game 40k --kind points --source ds-2026-06
python bsdata_importer.py pdf bases.pdf --game 40k --kind bases
python bsdata_importer.py apply-points 40k   # fuzzy-merge points/bases into units
python bsdata_importer.py export --game 40k -o 40k.json
```

### Export filters

```bash
python bsdata_importer.py export --game aos --dedupe --no-legends -o aos.json
python bsdata_importer.py export --game 40k --no-legends --exclude "path to glory" --exclude "spearhead"
```

`--no-legends` drops units marked as Legends: the `Legends` keyword (AoS), `[LEGENDS]`/`(Legends)` catalogues and the `legends: true` flag from the MFM (propagated onto units by `apply-points`). `--exclude PATTERN` (repeatable, case-insensitive) filters by a fragment of the faction name or a keyword — BSData has no universal "narrative" marker, so you cut narrative content with a pattern that fits the given game. Every filter prints how much it removed, so you immediately see whether the pattern hit.

`apply-points` overwrites `points_current` with the freshest source (fuzzy name matching, 0.87 cutoff, diacritics normalized). The original bsdata cost stays in `costs.pts`, so the bsdata vs MFM/dataslate difference is always visible.

## What to import — import_config.json

Instead of CLI flags: a JSON file with true/false switches per category (`keywords`, `weapons`, `abilities`, `stats`, `costs`). A `default` section plus per-game overrides; a missing key means import. The script automatically reads `import_config.json` next to itself, or point to another file with `--config` (works for `fetch` and `export`).

```json
{
  "default": { "abilities": false },
  "killteam": { "abilities": true, "costs": false }
}
```

A flat format is also accepted: `{ "weapons": false }`.

## Adding a new game

A `games.json` file next to the script — zero code changes:

```json
{
  "necromunda": {
    "repo": "BSData/necromunda",
    "branch": "master",
    "label": "Necromunda",
    "unit_types": ["unit", "model"],
    "points_cost_names": ["Credits", "pts"]
  }
}
```

## Programmatic API (rich parser)

For apps that build their own unit model (e.g. Unitle), the module exposes a richer parsing layer than the CLI:

```python
from bsdata_importer import (
    BUILTIN_GAMES, parse_catalogue_rich, resolve_head, fetch_repo_dir,
    index_by_id, resolve_subtree,
)

sha = resolve_head("BSData/wh40k-killteam", "master")   # pin the import
repo_dir = fetch_repo_dir("BSData/wh40k-killteam", sha, workdir)  # cached by sha

game = BUILTIN_GAMES["killteam"]
xml = (repo_dir / "2024 - Death Korps.cat").read_bytes()
team, units = parse_catalogue_rich(xml, game, qualify_profile_type="Operative")
```

`parse_catalogue_rich` differs from `parse_catalogue` in that:

- profiles, categories and **rules** (`unit.rules`, e.g. `<rule name="Base Size">`
  in AoS) are collected from the subtree **resolved** through `entryLink`/`infoLink`
  within the file — without this, profiles attached via links (Library files)
  are invisible;
- the `qualify_profile_type` parameter qualifies an entry as a unit only when
  the resolved subtree contains a profile with the given `typeName` (e.g.
  `"Unit"` in 40k/AoS, `"Operative"` in Kill Team);
- entries nested inside another candidate are dropped (unit sub-models);
- categories may contain duplicates (an artifact of links) — the consumer
  turns them into a set.

Unit selection (whitelists, duplicate merging, faction mapping, base sizes
from external tables) deliberately does NOT go into the package — those are
product decisions of the app.

## Technical notes

- Downloads go through `codeload.github.com` (tarball of the whole repo) — one request, no GitHub API limits.
- The XML parser is namespace-agnostic and reads `.cat`, `.gst` and zipped `.catz`/`.gstz`. For 11e BSData switched to JSON — the parser detects the format by extension.
- 40k points: the `BSData/wh40k-11e-mfm` repo scrapes `mfm.warhammer-community.com` into YAML daily (units + enhancements per detachment), so for 40k you usually don't need to parse a PDF. The PDF parser is useful for Heresy, TOW and local dataslates.
- Kill Team (2024 edition) doesn't use points — the `units` table still gets operative profiles.
- The CLI parser (`parse_catalogue`) does not resolve `entryLinks`/`infoLinks` (units shared via Library are present in the Library files anyway, so coverage is complete — but without modifier inheritance). `parse_catalogue_rich` resolves links within a file.
- XML is parsed with `defusedxml` (files come from external repos — protection against XXE/billion-laughs).
