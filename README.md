# bsdata-importer

Importer danych o jednostkach do apek wargamingowych. Trzy źródła: repozytoria BSData (staty, koszty, keywordy), scrapowany oficjalny MFM 40k (punkty szybciej niż aktualizacje .cat) oraz PDF-y (MFM / Balance Dataslate / rozmiary baz). Wynik ląduje w SQLite (`data.db`), z eksportem do JSON.

## Instalacja

Jako paczka w projekcie (warscore, unitle, pilezero, …):

```bash
pip install "bsdata-importer @ git+https://github.com/ogdowski/bsdata-importer.git"
# z parserem PDF (pdfplumber):
pip install "bsdata-importer[pdf] @ git+https://github.com/ogdowski/bsdata-importer.git"
```

albo wpis w `requirements.txt` / `pyproject.toml` projektu:

```
bsdata-importer @ git+https://github.com/ogdowski/bsdata-importer.git
```

Po instalacji dostępna jest komenda `bsdata-importer` (zamiennie z `python bsdata_importer.py` z checkoutu). Pliki `games.json` i `import_config.json` skrypt czyta z bieżącego katalogu projektu, a dopiero potem spod własnej lokalizacji — każda apka może więc mieć własną konfigurację importu.

Moduł da się też importować bezpośrednio: `from bsdata_importer import load_games, parse_catalogue, ...`.

## Obsługiwane gry (wbudowane)

| klucz | gra | repo |
|---|---|---|
| `40k` | Warhammer 40,000 (11th) | BSData/wh40k-11e (format JSON!) |
| `40k-10e` | Warhammer 40,000 (10th) | BSData/wh40k-10e |
| `aos` | Age of Sigmar (4th) | BSData/age-of-sigmar-4th |
| `heresy` | Horus Heresy (3rd) | BSData/horus-heresy-3rd-edition |
| `heresy-2e` | Horus Heresy (2nd) | BSData/horus-heresy-2nd-edition |
| `killteam` | Kill Team | BSData/wh40k-killteam (branch `master`) |
| `oldworld` | The Old World | Birddie721/TOW (BSData nie hostuje TOW; to community repo) |

## Typowy workflow

```bash
python bsdata_importer.py fetch 40k          # staty + koszty z BSData
python bsdata_importer.py mfm-live           # aktualne punkty 40k (scrap oficjalnego MFM)
python bsdata_importer.py pdf dataslate.pdf --game 40k --kind points --source ds-2026-06
python bsdata_importer.py pdf bazy.pdf --game 40k --kind bases
python bsdata_importer.py apply-points 40k   # fuzzy merge punktów/baz do jednostek
python bsdata_importer.py export --game 40k -o 40k.json
```

### Filtry eksportu

```bash
python bsdata_importer.py export --game aos --dedupe --no-legends -o aos.json
python bsdata_importer.py export --game 40k --no-legends --exclude "path to glory" --exclude "spearhead"
```

`--no-legends` wycina jednostki oznaczone jako Legends: keyword `Legends` (AoS), katalogi `[LEGENDS]`/`(Legends)` oraz flagę `legends: true` z MFM (propagowaną do jednostek przy `apply-points`). `--exclude WZORZEC` (wielokrotny, case-insensitive) filtruje po fragmencie nazwy frakcji lub keywordu — BSData nie ma uniwersalnego znacznika "narrative", więc treści narracyjne wycinasz wzorcem pasującym do danej gry. Każdy filtr wypisuje ile usunął, więc od razu widać czy wzorzec trafił.

`apply-points` nadpisuje `points_current` najświeższym źródłem (fuzzy matching nazw, próg 0.87, normalizacja diakrytyków). Oryginalny koszt z bsdata zostaje w `costs.pts`, więc zawsze widać różnicę bsdata vs MFM/dataslate.

## Co importować — import_config.json

Zamiast flag CLI: plik JSON z parametrami true/false per kategoria (`keywords`, `weapons`, `abilities`, `stats`, `costs`). Sekcja `default` plus nadpisania per gra; brak klucza = importuj. Skrypt automatycznie czyta `import_config.json` obok siebie, albo wskaż inny plik przez `--config` (działa na `fetch` i `export`).

```json
{
  "default": { "abilities": false },
  "killteam": { "abilities": true, "costs": false }
}
```

Dopuszczalny też płaski format: `{ "weapons": false }`.

## Dodawanie nowej gry

Plik `games.json` obok skryptu — zero zmian w kodzie:

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

## API programistyczne (rich parser)

Dla aplikacji, które budują własny model jednostki (np. Unitle), moduł
wystawia bogatszą warstwę parsowania niż CLI:

```python
from bsdata_importer import (
    BUILTIN_GAMES, parse_catalogue_rich, resolve_head, fetch_repo_dir,
    index_by_id, resolve_subtree,
)

sha = resolve_head("BSData/wh40k-killteam", "master")   # przypięcie importu
repo_dir = fetch_repo_dir("BSData/wh40k-killteam", sha, workdir)  # cache po sha

game = BUILTIN_GAMES["killteam"]
xml = (repo_dir / "2024 - Death Korps.cat").read_bytes()
team, units = parse_catalogue_rich(xml, game, qualify_profile_type="Operative")
```

`parse_catalogue_rich` różni się od `parse_catalogue` tym, że:

- profile, kategorie i **rules** (`unit.rules`, np. `<rule name="Base Size">`
  w AoS) zbiera z poddrzewa **rozwiązanego** przez `entryLink`/`infoLink`
  w obrębie pliku — bez tego profile podpinane linkami (pliki Library) są
  niewidoczne;
- parametr `qualify_profile_type` kwalifikuje wpis jako jednostkę tylko wtedy,
  gdy rozwiązane poddrzewo ma profil o danym `typeName` (np. `"Unit"` w 40k/AoS,
  `"Operative"` w Kill Team);
- wpisy zagnieżdżone w innym kandydacie odpadają (sub-modele jednostek);
- kategorie mogą zawierać duplikaty (efekt linków) — konsument robi z nich zbiór.

Selekcja jednostek (whitelisty, scalanie duplikatów, mapowanie frakcji,
rozmiary baz z zewnętrznych tabel) celowo NIE wchodzi do paczki — to decyzje
produktowe aplikacji.

## Uwagi techniczne

- Pobieranie idzie przez `codeload.github.com` (tarball całego repo) — jeden request, brak limitów GitHub API.
- Parser XML jest namespace-agnostyczny i czyta `.cat`, `.gst` oraz spakowane `.catz`/`.gstz`. Dla 11e BSData przeszło na JSON — parser wykrywa format po rozszerzeniu.
- Punkty 40k: repo `BSData/wh40k-11e-mfm` codziennie scrapuje `mfm.warhammer-community.com` do YAML (jednostki + enhancementy per detachment), więc dla 40k zwykle nie musisz parsować PDF-a. Parser PDF przydaje się dla Heresy, TOW i lokalnych dataslate'ów.
- Kill Team (edycja 2024) nie używa punktów — tabela `units` i tak dostaje profile operatywów.
- Parser CLI (`parse_catalogue`) nie rozwiązuje `entryLinks`/`infoLinks` (jednostki współdzielone przez Library i tak są w plikach Library, więc pokrycie jest pełne — ale bez dziedziczenia modyfikatorów). `parse_catalogue_rich` rozwiązuje linki w obrębie pliku.
- XML parsowany przez `defusedxml` (pliki przychodzą z zewnętrznych repo — ochrona przed XXE/billion-laughs).
