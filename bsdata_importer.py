#!/usr/bin/env python3
"""
bsdata-importer — importer danych do apek wargamingowych.

Źródła:
  1. Repozytoria BSData (XML .cat/.gst) — statystyki, koszty, keywordy
  2. BSData/wh40k-11e-mfm — punkty 40k scrapowane z oficjalnego MFM (YAML)
  3. PDF-y (MFM / Balance Dataslate / dokumenty z rozmiarami baz) — pdfplumber

Wyjście: SQLite (data.db) + eksport JSON.

Zależności: pip install requests pyyaml pdfplumber

Użycie:
  python bsdata_importer.py games                     # lista gier
  python bsdata_importer.py fetch 40k                 # pobierz + sparsuj BSData
  python bsdata_importer.py mfm-live                  # punkty 40k z repo MFM
  python bsdata_importer.py pdf plik.pdf --game heresy --kind points
  python bsdata_importer.py pdf bazy.pdf --game 40k --kind bases
  python bsdata_importer.py apply-points 40k          # nadpisz punkty z MFM/PDF
  python bsdata_importer.py export --game 40k -o 40k.json
"""

from __future__ import annotations

import argparse
import io
import json
import re
import sqlite3
import sys
import tarfile
import unicodedata
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from xml.etree import ElementTree as ET

import requests

__version__ = "0.1.0"

# ---------------------------------------------------------------------------
# 1. REJESTR GIER — dodanie nowej gry = jeden wpis tutaj albo w games.json
# ---------------------------------------------------------------------------


@dataclass
class Game:
    key: str
    repo: str                      # owner/repo na GitHubie
    branch: str = "main"
    label: str = ""
    # typy selectionEntry traktowane jako "jednostki"
    unit_types: tuple = ("unit", "model")
    # nazwy kosztów, które są punktami (pierwszy znaleziony wygrywa)
    points_cost_names: tuple = ("pts", "Pts", "points", "Points", " pts")
    # kategorie pomijane przy imporcie: keywords, weapons, abilities, stats, costs
    skip: tuple = ()
    # typeName profili traktowane jako statystyki jednostki (reszta -> abilities)
    stat_profile_types: tuple = (
        "unit", "model", "operative", "profile", "vehicle", "monster",
        "manifestation", "battlefield fortification", "fortification", "knight",
    )


BUILTIN_GAMES: dict[str, Game] = {
    "40k": Game("40k", "BSData/wh40k-11e", label="Warhammer 40,000 (11th)"),
    "40k-10e": Game("40k-10e", "BSData/wh40k-10e", label="Warhammer 40,000 (10th)"),
    "aos": Game("aos", "BSData/age-of-sigmar-4th", label="Age of Sigmar (4th)"),
    "heresy": Game("heresy", "BSData/horus-heresy-3rd-edition", label="Horus Heresy (3rd)"),
    "heresy-2e": Game("heresy-2e", "BSData/horus-heresy-2nd-edition", label="Horus Heresy (2nd)"),
    "killteam": Game("killteam", "BSData/wh40k-killteam", branch="master", label="Kill Team"),
    "oldworld": Game("oldworld", "Birddie721/TOW", label="WH: The Old World (community)"),
}

MFM_REPO = ("BSData/wh40k-11e-mfm", "main")   # scrapowany oficjalny MFM 40k


def _default_cfg(name: str) -> Path:
    """Plik konfiguracyjny: najpierw katalog roboczy (projekt używający paczki),
    potem obok modułu (uruchomienie z checkoutu repo)."""
    local = Path.cwd() / name
    return local if local.exists() else Path(__file__).with_name(name)


def load_games(config_path: Path | None = None) -> dict[str, Game]:
    """Wbudowane gry + opcjonalny games.json (cwd lub obok skryptu)."""
    games = dict(BUILTIN_GAMES)
    cfg = config_path or _default_cfg("games.json")
    if cfg.exists():
        for key, spec in json.loads(cfg.read_text(encoding="utf-8")).items():
            games[key] = Game(
                key=key,
                repo=spec["repo"],
                branch=spec.get("branch", "main"),
                label=spec.get("label", key),
                unit_types=tuple(spec.get("unit_types", ("unit", "model"))),
                points_cost_names=tuple(
                    spec.get("points_cost_names", Game.points_cost_names)
                ),
                skip=tuple(spec.get("skip", ())),
                stat_profile_types=tuple(
                    spec.get("stat_profile_types", Game.stat_profile_types)
                ),
            )
    return games


# ---------------------------------------------------------------------------
# 2. POBIERANIE — tarball przez codeload (1 request, zero limitów API)
# ---------------------------------------------------------------------------


def download_repo_tarball(repo: str, branch: str) -> tarfile.TarFile:
    url = f"https://codeload.github.com/{repo}/tar.gz/refs/heads/{branch}"
    print(f"  ↓ {url}")
    r = requests.get(url, timeout=120)
    r.raise_for_status()
    return tarfile.open(fileobj=io.BytesIO(r.content), mode="r:gz")


def iter_catalogue_files(tar: tarfile.TarFile):
    """Zwraca (nazwa_pliku, format, bytes) dla .cat/.gst/.json (+ spakowane .catz/.gstz)."""
    for member in tar.getmembers():
        if not member.isfile():
            continue
        name = member.name
        low = name.lower()
        parts = Path(name).parts
        if low.endswith((".cat", ".gst")):
            yield Path(name).name, "xml", tar.extractfile(member).read()
        elif low.endswith(".json") and len(parts) == 2 and not parts[-1].startswith("."):
            # katalogi JSON (nowy format BSData, np. wh40k-11e) — tylko główny katalog repo
            yield Path(name).name, "json", tar.extractfile(member).read()
        elif low.endswith((".catz", ".gstz")):
            raw = tar.extractfile(member).read()
            with zipfile.ZipFile(io.BytesIO(raw)) as zf:
                inner = zf.namelist()[0]
                yield Path(inner).name, "xml", zf.read(inner)


# ---------------------------------------------------------------------------
# 3. PARSER XML BATTLESCRIBE (namespace-agnostyczny)
# ---------------------------------------------------------------------------


def _tag(el) -> str:
    return el.tag.split("}")[-1]


def _children(el, name):
    return [c for c in el if _tag(c) == name]


def _find(el, name):
    for c in el:
        if _tag(c) == name:
            return c
    return None


def classify_profile(type_name: str, game: Game) -> str:
    """Zwraca kategorię profilu: 'stats' | 'weapons' | 'abilities'."""
    tn = (type_name or "").lower().strip()
    if "weapon" in tn:
        return "weapons"
    if tn in game.stat_profile_types:
        return "stats"
    return "abilities"


@dataclass
class ParsedUnit:
    name: str
    bs_id: str
    entry_type: str
    faction: str
    costs: dict = field(default_factory=dict)
    profiles: list = field(default_factory=list)   # [{type, name, chars{}}]
    categories: list = field(default_factory=list)


def parse_catalogue(xml_bytes: bytes, game: Game) -> tuple[str, list[ParsedUnit]]:
    root = ET.fromstring(xml_bytes)
    faction = root.get("name", "?")
    units: list[ParsedUnit] = []

    for se in root.iter():
        if _tag(se) != "selectionEntry" or se.get("type") not in game.unit_types:
            continue
        unit = ParsedUnit(
            name=se.get("name", "?"),
            bs_id=se.get("id", ""),
            entry_type=se.get("type", ""),
            faction=faction,
        )
        # koszty bezpośrednio pod wpisem (nie z zagnieżdżonych upgrade'ów)
        costs_el = _find(se, "costs")
        if costs_el is not None:
            for c in _children(costs_el, "cost"):
                try:
                    val = float(c.get("value", "0"))
                except ValueError:
                    continue
                cname = (c.get("name") or "").strip()
                if cname and (val or cname in game.points_cost_names):
                    unit.costs[cname] = val
        # profile z całego poddrzewa wpisu: staty, bronie, abilities
        for p in se.iter():
            if _tag(p) != "profile":
                continue
            chars = {}
            chs = _find(p, "characteristics")
            if chs is not None:
                for ch in _children(chs, "characteristic"):
                    chars[ch.get("name", "?")] = (ch.text or "").strip()
            unit.profiles.append({
                "type": p.get("typeName", ""), "name": p.get("name", ""),
                "kind": classify_profile(p.get("typeName", ""), game), "chars": chars,
            })
        # kategorie / keywordy
        cats_el = _find(se, "categoryLinks")
        if cats_el is not None:
            unit.categories = [c.get("name", "") for c in _children(cats_el, "categoryLink")]
        units.append(unit)

    return faction, units


def parse_catalogue_json(json_bytes: bytes, game: Game) -> tuple[str, list[ParsedUnit]]:
    """Nowy format BSData (np. wh40k-11e): ta sama struktura co XML, ale w JSON."""
    doc = json.loads(json_bytes)
    root = doc.get("catalogue") or doc.get("gameSystem") or {}
    faction = root.get("name", "?")
    units: list[ParsedUnit] = []

    def walk(obj):
        if isinstance(obj, dict):
            if obj.get("type") in game.unit_types and "name" in obj and (
                "costs" in obj or "profiles" in obj or "categoryLinks" in obj
            ):
                unit = ParsedUnit(
                    name=obj["name"], bs_id=obj.get("id", ""),
                    entry_type=obj["type"], faction=faction,
                )
                for c in obj.get("costs", []):
                    cname = (c.get("name") or "").strip()
                    val = float(c.get("value", 0) or 0)
                    if cname and (val or cname in game.points_cost_names):
                        unit.costs[cname] = val
                def collect_profiles(o):
                    if isinstance(o, dict):
                        for p in o.get("profiles", []) or []:
                            chars = {
                                ch.get("name", "?"): (ch.get("$text") or "").strip()
                                for ch in p.get("characteristics", [])
                            }
                            unit.profiles.append({
                                "type": p.get("typeName", ""), "name": p.get("name", ""),
                                "kind": classify_profile(p.get("typeName", ""), game),
                                "chars": chars,
                            })
                        for v in o.values():
                            if v is not o.get("profiles"):
                                collect_profiles(v)
                    elif isinstance(o, list):
                        for v in o:
                            collect_profiles(v)
                collect_profiles(obj)
                unit.categories = [
                    c.get("name", "") for c in obj.get("categoryLinks", [])
                ]
                units.append(unit)
            for v in obj.values():
                walk(v)
        elif isinstance(obj, list):
            for v in obj:
                walk(v)

    walk(root)
    return faction, units


# ---------------------------------------------------------------------------
# 4. BAZA DANYCH
# ---------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS units (
    game TEXT, faction TEXT, bs_id TEXT, name TEXT, entry_type TEXT,
    costs_json TEXT, profiles_json TEXT, categories_json TEXT,
    base_size TEXT, points_current REAL, points_source TEXT, legends INTEGER DEFAULT 0,
    PRIMARY KEY (game, bs_id, name)
);
CREATE TABLE IF NOT EXISTS points (
    game TEXT, faction TEXT, unit_name TEXT, models INTEGER,
    points REAL, source TEXT, fetched_at TEXT, legends INTEGER DEFAULT 0,
    PRIMARY KEY (game, faction, unit_name, models, source)
);
CREATE TABLE IF NOT EXISTS enhancements (
    game TEXT, faction TEXT, detachment TEXT, name TEXT, points REAL, source TEXT,
    PRIMARY KEY (game, faction, detachment, name, source)
);
CREATE TABLE IF NOT EXISTS base_sizes (
    game TEXT, unit_name TEXT, base TEXT, source TEXT,
    PRIMARY KEY (game, unit_name, source)
);
CREATE INDEX IF NOT EXISTS idx_units_name ON units(game, name);
"""


def open_db(path: str) -> sqlite3.Connection:
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    for table in ("units", "points"):
        try:
            con.execute(f"ALTER TABLE {table} ADD COLUMN legends INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass  # kolumna już istnieje
    return con


def is_legends(faction: str, categories: list[str]) -> bool:
    return "legend" in faction.lower() or any("legend" in c.lower() for c in categories)


# ---------------------------------------------------------------------------
# 5. KOMENDY: fetch / mfm-live
# ---------------------------------------------------------------------------


IMPORT_CATEGORIES = ("keywords", "weapons", "abilities", "stats", "costs")


def load_import_config(path: str | None, game_key: str) -> set:
    """Czyta JSON z parametrami true/false per kategoria. Zwraca zbiór pomijanych.

    Format (sekcja "default" + opcjonalne nadpisania per gra):
      { "default": {"weapons": true, "abilities": false},
        "killteam": {"abilities": true} }
    Dopuszczalny też płaski słownik: {"weapons": true, "abilities": false}.
    Brak klucza = true (importuj). Domyślny plik: import_config.json w cwd
    lub obok skryptu.
    """
    cfg_path = Path(path) if path else _default_cfg("import_config.json")
    if not cfg_path.exists():
        if path:
            sys.exit(f"Nie znaleziono pliku konfiguracji: {cfg_path}")
        return set()
    doc = json.loads(cfg_path.read_text(encoding="utf-8"))
    if any(isinstance(v, dict) for v in doc.values()):
        merged = dict(doc.get("default", {}))
        merged.update(doc.get(game_key, {}))
    else:
        merged = doc
    unknown = set(merged) - set(IMPORT_CATEGORIES)
    if unknown:
        print(f"  ! nieznane kategorie w konfiguracji (ignoruję): {', '.join(sorted(unknown))}")
    return {c for c in IMPORT_CATEGORIES if merged.get(c, True) is False}


def apply_skip(unit: ParsedUnit, skip: set, game: Game):
    if "keywords" in skip:
        unit.categories = []
    if "costs" in skip:
        unit.costs = {
            k: v for k, v in unit.costs.items() if k in game.points_cost_names
        }
    drop = {c for c in ("weapons", "abilities", "stats") if c in skip}
    if drop:
        unit.profiles = [p for p in unit.profiles if p["kind"] not in drop]


def cmd_fetch(args, games):
    game = games[args.game]
    skip = set(game.skip) | load_import_config(args.config, game.key)
    if skip:
        print(f"  pomijam kategorie: {', '.join(sorted(skip))}")
    con = open_db(args.db)
    tar = download_repo_tarball(game.repo, game.branch)
    n_files = n_units = 0
    for fname, fmt, blob in iter_catalogue_files(tar):
        try:
            if fmt == "json":
                faction, units = parse_catalogue_json(blob, game)
            else:
                faction, units = parse_catalogue(blob, game)
        except (ET.ParseError, json.JSONDecodeError) as e:
            print(f"  ! pomijam {fname}: {e}")
            continue
        n_files += 1
        for u in units:
            apply_skip(u, skip, game)
            pts = next(
                (u.costs[c] for c in game.points_cost_names if c in u.costs), None
            )
            con.execute(
                "INSERT OR REPLACE INTO units VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    game.key, u.faction, u.bs_id, u.name, u.entry_type,
                    json.dumps(u.costs), json.dumps(u.profiles, ensure_ascii=False),
                    json.dumps(u.categories, ensure_ascii=False),
                    None, pts, "bsdata", int(is_legends(u.faction, u.categories)),
                ),
            )
            n_units += 1
    con.commit()
    print(f"✓ {game.key}: {n_files} katalogów, {n_units} jednostek → {args.db}")


def cmd_mfm_live(args, games):
    """Punkty 40k z BSData/wh40k-11e-mfm (scrapowany oficjalny MFM)."""
    import yaml

    con = open_db(args.db)
    repo, branch = MFM_REPO
    tar = download_repo_tarball(repo, branch)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    n_units = n_enh = 0
    for member in tar.getmembers():
        p = Path(member.name)
        if not (member.isfile() and p.suffix == ".yaml" and p.parent.name == "data"):
            continue
        doc = yaml.safe_load(tar.extractfile(member).read())
        faction = doc.get("name", p.stem)
        for unit in doc.get("units", []):
            for pricing in unit.get("pricing", []):
                for cost in pricing.get("costs", []):
                    con.execute(
                        "INSERT OR REPLACE INTO points VALUES (?,?,?,?,?,?,?,?)",
                        (args.game, faction, unit["name"],
                         cost.get("models", 1), cost.get("points"), "mfm-live", now,
                         int(bool(unit.get("legends")))),
                    )
                    n_units += 1
        for det in doc.get("detachments", []):
            for enh in det.get("enhancements", []):
                con.execute(
                    "INSERT OR REPLACE INTO enhancements VALUES (?,?,?,?,?,?)",
                    (args.game, faction, det.get("name", ""),
                     enh["name"], enh.get("points"), "mfm-live"),
                )
                n_enh += 1
    con.commit()
    print(f"✓ MFM live: {n_units} wpisów punktowych, {n_enh} enhancementów → {args.db}")


# ---------------------------------------------------------------------------
# 6. PARSER PDF (MFM / dataslate / rozmiary baz)
# ---------------------------------------------------------------------------

# "Archon ......... 1 model ........ 85 pts" | "Kabalite Warriors 10 models 110 pts"
RE_MODELS_PTS = re.compile(
    r"(?P<models>\d+)\s*models?\s*[.\s…]*\+?\s*(?P<pts>\d+)\s*pts?\b", re.I
)
# "Nazwa ..... 120 pts" (bez liczby modeli) / enhancementy "+15 pts"
RE_NAME_PTS = re.compile(
    r"^(?P<name>[^\d.…][^.…]*?)\s*[.\s…]*\+?\s*(?P<pts>\d+)\s*pts?\.?\s*$", re.I
)
# Rozmiary baz: "32mm", "28.5mm", "60x35.5mm Oval Base", "105mm x 70mm",
# oraz nazwane: "Hull", "Large/Small Flying Base", "Unique" (styl GW Event Companion)
RE_BASE = re.compile(
    r"(?P<base>"
    r"\d{2,3}(?:\.\d)?\s*(?:mm\s*)?[x×]\s*\d{2,3}(?:\.\d)?\s*mm"
    r"(?:\s*(?:oval|round|owal\w*)(?:\s*base)?)?"
    r"|\d{2,3}(?:\.\d)?\s*mm(?:\s*(?:oval|round|owal\w*)(?:\s*base)?)?"
    r"|(?:large|small)\s+flying\s+base"
    r"|hull"
    r"|unique"
    r")\s*$",
    re.I,
)
RE_BRACKET_TAG = re.compile(r"\s*\[[^\]]*\]")   # np. "Immolator [Adepta_Sororitas]"
BASE_SKIP_LINES = {"unit base size", "unit", "base size"}
RE_HEADER = re.compile(r"^[A-ZĄĆĘŁŃÓŚŹŻ0-9 '\-&:,]{4,}$")  # nagłówki frakcji CAPS-em


def parse_pdf_points(pdf_path: str) -> list[dict]:
    """Parser layoutu MFM/dataslate: nazwa jednostki + wiersze 'N models … pts'."""
    import pdfplumber

    out, current_unit, current_faction = [], None, None
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            for raw in (page.extract_text() or "").splitlines():
                line = raw.strip()
                if not line:
                    continue
                m = RE_MODELS_PTS.search(line)
                if m:
                    # nazwa może być w tej samej linii przed 'N models'
                    prefix = line[: m.start()].strip(" .…")
                    name = prefix or current_unit
                    if prefix:
                        current_unit = prefix
                    if name:
                        out.append(dict(
                            faction=current_faction, unit=name,
                            models=int(m["models"]), points=int(m["pts"]),
                        ))
                    continue
                m = RE_NAME_PTS.match(line)
                if m:
                    out.append(dict(
                        faction=current_faction, unit=m["name"].strip(" .…"),
                        models=None, points=int(m["pts"]),
                    ))
                    continue
                if RE_HEADER.match(line):
                    current_faction, current_unit = line.title(), None
                else:
                    current_unit = line.strip(" .…")
    return out


def parse_base_lines(lines) -> list[dict]:
    """Wyciąga pary (nazwa jednostki, rozmiar bazy) z linii tekstu.
    Obsługuje styl GW Event Companion (Base Size Guide)."""
    out = []
    for raw in lines:
        line = re.sub(r"\s+", " ", raw).strip()
        if not line or line.lower() in BASE_SKIP_LINES or line.isdigit():
            continue
        m = RE_BASE.search(line)
        if not m:
            continue
        name = RE_BRACKET_TAG.sub("", line[: m.start()]).strip(" .…-–\t")
        if not name or name.isdigit():
            continue
        base = re.sub(r"\s+", " ", m["base"]).lower()
        out.append(dict(unit=name, base=base))
    return out


def parse_pdf_bases(pdf_path: str) -> list[dict]:
    """Wyciąga pary (nazwa jednostki, rozmiar bazy) z PDF-a lub pliku .txt."""
    if pdf_path.lower().endswith(".txt"):
        return parse_base_lines(Path(pdf_path).read_text(encoding="utf-8").splitlines())
    import pdfplumber

    out = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            out.extend(parse_base_lines((page.extract_text() or "").splitlines()))
    return out


def cmd_pdf(args, games):
    con = open_db(args.db)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    source = args.source or Path(args.pdf).stem
    if args.kind in ("points", "auto"):
        rows = parse_pdf_points(args.pdf)
        for r in rows:
            con.execute(
                "INSERT OR REPLACE INTO points VALUES (?,?,?,?,?,?,?,?)",
                (args.game, r["faction"] or "?", r["unit"],
                 r["models"] or 1, r["points"], source, now, 0),
            )
        print(f"✓ punkty z PDF: {len(rows)} wpisów (źródło '{source}')")
    if args.kind in ("bases", "auto"):
        rows = parse_pdf_bases(args.pdf)
        for r in rows:
            con.execute(
                "INSERT OR REPLACE INTO base_sizes VALUES (?,?,?,?)",
                (args.game, r["unit"], r["base"], source),
            )
        print(f"✓ rozmiary baz z PDF: {len(rows)} wpisów")
    con.commit()


# ---------------------------------------------------------------------------
# 7. FUZZY MERGE — punkty z PDF/MFM + bazy → tabela units
# ---------------------------------------------------------------------------


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9 ]", "", s.lower()).strip()


def fuzzy_lookup(name: str, candidates: dict[str, str], cutoff=0.87) -> str | None:
    """candidates: {norm_name: oryginalna_nazwa}. Zwraca oryginalną nazwę lub None."""
    n = norm(name)
    if n in candidates:
        return candidates[n]
    best, best_r = None, cutoff
    for cn, orig in candidates.items():
        r = SequenceMatcher(None, n, cn).ratio()
        if r > best_r:
            best, best_r = orig, r
    return best


def cmd_apply_points(args, games):
    con = open_db(args.db)
    units = {norm(r[0]): r[0] for r in con.execute(
        "SELECT DISTINCT name FROM units WHERE game=?", (args.game,))}
    # najnowsze punkty per jednostka (preferuj min. liczbę modeli jako bazową)
    rows = con.execute(
        """SELECT unit_name, points, source, legends FROM points
           WHERE game=? ORDER BY fetched_at DESC, models ASC""",
        (args.game,),
    ).fetchall()
    applied, missed, seen = 0, [], set()
    for unit_name, pts, source, legends in rows:
        if unit_name in seen:
            continue
        seen.add(unit_name)
        match = fuzzy_lookup(unit_name, units)
        if match:
            con.execute(
                "UPDATE units SET points_current=?, points_source=?, "
                "legends=MAX(legends,?) WHERE game=? AND name=?",
                (pts, source, int(legends or 0), args.game, match),
            )
            applied += 1
        else:
            missed.append(unit_name)
    # bazy
    bases = con.execute(
        "SELECT unit_name, base FROM base_sizes WHERE game=?", (args.game,)
    ).fetchall()
    for unit_name, base in bases:
        match = fuzzy_lookup(unit_name, units)
        if not match and ":" in unit_name:
            match = fuzzy_lookup(unit_name.split(":")[0], units)
        if match:
            con.execute(
                "UPDATE units SET base_size=? WHERE game=? AND name=?",
                (base, args.game, match),
            )
    con.commit()
    print(f"✓ zaktualizowano punkty {applied} jednostek, bazy: {len(bases)} wpisów")
    if missed and args.verbose:
        print("  niedopasowane:", ", ".join(missed[:20]))


# ---------------------------------------------------------------------------
# 8. EKSPORT
# ---------------------------------------------------------------------------


def _unit_score(rec: dict) -> tuple:
    """Ranking duplikatów: punkty > profil Unit > liczba profili > typ 'unit' > keywordy."""
    return (
        rec["points"] is not None,
        any(p.get("type") == "Unit" for p in rec["profiles"]),
        len(rec["profiles"]),
        rec["type"] == "unit",
        len(rec["keywords"]),
    )


def dedupe_units(data: list[dict]) -> list[dict]:
    """Jedna pozycja per (game, faction, name) — wygrywa najbogatszy wpis,
    keywordy z przegranych duplikatów są scalane."""
    best: dict[tuple, dict] = {}
    for rec in data:
        key = (rec["game"], rec["faction"], norm(rec["name"]))
        cur = best.get(key)
        if cur is None:
            best[key] = rec
        else:
            winner, loser = (rec, cur) if _unit_score(rec) > _unit_score(cur) else (cur, rec)
            merged_kw = list(dict.fromkeys(winner["keywords"] + loser["keywords"]))
            winner["keywords"] = merged_kw
            best[key] = winner
    return list(best.values())


def cmd_export(args, games):
    con = open_db(args.db)
    con.row_factory = sqlite3.Row
    q = "SELECT * FROM units" + (" WHERE game=?" if args.game else "")
    rows = con.execute(q, (args.game,) if args.game else ()).fetchall()
    data = []
    for r in rows:
        data.append({
            "game": r["game"], "faction": r["faction"], "name": r["name"],
            "type": r["entry_type"], "points": r["points_current"],
            "points_source": r["points_source"], "base": r["base_size"],
            "legends": bool(r["legends"]),
            "costs": json.loads(r["costs_json"] or "{}"),
            "profiles": json.loads(r["profiles_json"] or "[]"),
            "keywords": json.loads(r["categories_json"] or "[]"),
        })
    skip_cats = load_import_config(args.config, args.game or "default")
    for cat in skip_cats:
        if cat == "keywords":
            for u in data:
                u["keywords"] = []
        elif cat == "costs":
            for u in data:
                u["costs"] = {}
        elif cat in ("weapons", "abilities", "stats"):
            for u in data:
                u["profiles"] = [p for p in u["profiles"] if p.get("kind") != cat]
    if args.no_legends:
        before = len(data)
        data = [u for u in data if not (u["legends"] or is_legends(u["faction"], u["keywords"]))]
        print(f"  no-legends: {before} → {len(data)}")
    for pattern in (args.exclude or []):
        p = pattern.lower()
        before = len(data)
        data = [
            u for u in data
            if p not in u["faction"].lower()
            and not any(p in k.lower() for k in u["keywords"])
        ]
        print(f"  exclude '{pattern}': {before} → {len(data)}")
    if args.dedupe:
        before = len(data)
        data = dedupe_units(data)
        print(f"  dedupe: {before} → {len(data)} jednostek")
    Path(args.output).write_text(
        json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(f"✓ wyeksportowano {len(data)} jednostek → {args.output}")


def cmd_games(args, games):
    for g in games.values():
        print(f"  {g.key:12} {g.label:35} https://github.com/{g.repo} ({g.branch})")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    ap = argparse.ArgumentParser(description="Importer BSData + MFM/PDF")
    ap.add_argument("--db", default="data.db")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("games", help="lista obsługiwanych gier")

    p = sub.add_parser("fetch", help="pobierz i sparsuj repo BSData")
    p.add_argument("game")
    p.add_argument("--config", metavar="PLIK.json",
                   help="konfiguracja importu true/false per kategoria "
                        "(domyślnie: import_config.json obok skryptu, jeśli istnieje)")

    p = sub.add_parser("mfm-live", help="punkty 40k z BSData/wh40k-11e-mfm")
    p.add_argument("--game", default="40k")

    p = sub.add_parser("pdf", help="parsuj PDF (MFM/dataslate/bazy)")
    p.add_argument("pdf")
    p.add_argument("--game", required=True)
    p.add_argument("--kind", choices=["points", "bases", "auto"], default="auto")
    p.add_argument("--source", help="etykieta źródła, np. 'dataslate-2026-06'")

    p = sub.add_parser("apply-points", help="fuzzy merge punktów/baz do units")
    p.add_argument("game")
    p.add_argument("-v", "--verbose", action="store_true")

    p = sub.add_parser("export", help="eksport do JSON")
    p.add_argument("--game")
    p.add_argument("--config", metavar="PLIK.json",
                   help="konfiguracja true/false per kategoria stosowana do eksportu")
    p.add_argument("--dedupe", action="store_true",
                   help="jedna pozycja per (gra, frakcja, nazwa) — scala duplikaty")
    p.add_argument("--no-legends", action="store_true",
                   help="odfiltruj jednostki Legends (keyword/katalog/flaga MFM)")
    p.add_argument("--exclude", action="append", metavar="WZORZEC",
                   help="odfiltruj po fragmencie nazwy frakcji lub keywordu; "
                        "można podać wielokrotnie, np. --exclude 'path to glory'")
    p.add_argument("-o", "--output", default="export.json")

    args = ap.parse_args()
    games = load_games()
    if getattr(args, "game", None) and args.cmd in ("fetch", "apply-points") \
            and args.game not in games:
        sys.exit(f"Nieznana gra '{args.game}'. Dostępne: {', '.join(games)}")

    {"games": cmd_games, "fetch": cmd_fetch, "mfm-live": cmd_mfm_live,
     "pdf": cmd_pdf, "apply-points": cmd_apply_points, "export": cmd_export}[args.cmd](args, games)


if __name__ == "__main__":
    main()
