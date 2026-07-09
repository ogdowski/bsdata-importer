# Bazy + superfrakcje/frakcje w unitach (v0.2.0)

Data: 2026-07-09 · Status: zaakceptowany przez użytkownika (podejście A, dane surowe)

## Cel

Po `fetch <gra>` + `export` każdy unit ma jawne pola `superfaction`, `faction`,
`faction_tags`, `catalogue` i `base` — bez dodatkowych kroków i bez dostarczania
PDF-ów przez użytkownika. Konsumenci: pilezero (encje GameSystem/Faction/
ModelFaction), warscore; unitle zostaje przy własnym pipeline.

Paczka dostarcza dane **surowe, ustrukturyzowane** — żadnych decyzji
produktowych (bez aliasów Asuryani→Aeldari, bez „primary faction", bez
specjalnych reguł cult troops). Scalanie i aliasowanie to robota konsumentów.

## Zakres

- Pełne wzbogacenie: `40k` (wh40k-11e, JSON), `40k-10e` (XML), `aos`.
- Pozostałe gry: generyczny split nazwy katalogu (superfaction/faction),
  `base` = null. Bez nowych źródeł danych.
- Poza zakresem: Kill Team/Heresy bazy, rozwiązywanie entryLinków,
  eksport manifestu frakcji (konsument deriwuje z unitów).

## Struktura paczki

Moduł `bsdata_importer.py` staje się pakietem (setuptools `py-modules` nie
pakuje plików danych):

```
bsdata_importer/
  __init__.py        # dotychczasowy kod; re-eksport publicznego API + main
  bases.py           # slug, warianty primary/derived, match_base_size (port z unitle)
  data/
    wh40k_bases.json          # seed: kopia zweryfikowanego pliku unitle (997 wpisów, Event Companion)
    wh40k_bases_manual.json   # seed: kopia manuala unitle (21 prawdziwych braków PDF)
    aos_grand_alliances.json  # mapa frakcja -> Grand Alliance (kopia FACTION_TO_GA z unitle)
```

Dane czytane przez `importlib.resources`. Entry point `bsdata-importer` bez
zmian. `pyproject.toml`: `packages` + `package-data` zamiast `py-modules`.
Wersja 0.2.0, tag po weryfikacji.

## Model danych

Nowe kolumny `units` (migracja: `ALTER TABLE ... ADD COLUMN` w `open_db`,
wzorzec jak `legends`): `superfaction TEXT`, `catalogue TEXT`,
`faction_tags_json TEXT`. Kolumna `faction` zmienia znaczenie (breaking,
pre-1.0): dziś surowa nazwa katalogu → będzie czysta nazwa frakcji.
`is_legends()` przechodzi z `faction` na `catalogue`.

Eksport per unit — nowe/zmienione pola:

| pole | 40k / 40k-10e | aos | pozostałe |
|---|---|---|---|
| `superfaction` | prefiks katalogu: literalne `Imperium`/`Chaos`, wszystko inne → `Xenos` | z mapy `aos_grand_alliances.json`; brak w mapie → `null` + warning | człon przed " - " lub `null` |
| `faction` | człon katalogu po " - ", bez sufiksu `[Legends]` | nazwa katalogu bez sufiksu " - Library" | człon po " - " lub cała nazwa |
| `faction_tags` | wszystkie `X` z keywordów `Faction: X` (surowa lista) | jw. | jw. |
| `catalogue` | surowa nazwa katalogu | jw. | jw. |
| `base` | bundel + heurystyki (niżej) | reguła `Base Size` z BSData | `null` |

## Bazy 40k — bundel + heurystyki

Priorytet przy `fetch`: (1) `wh40k_bases_manual.json` po exact slugu,
(2) `match_base_size` na `wh40k_bases.json` (warianty primary z prefix-union
sub-modeli, derived tylko exact, fallback po sufiksie klucza — dokładnie
logika wdrożona i przetestowana w unitle 2026-07-09). `apply-points` z PDF-a
użytkownika może później nadpisać `base_size` (jak punkty dziś).

Regeneracja bundla przy nowym Event Companion: nowa flaga
`pdf <plik> --kind bases --json out.json` — zapis mapy `{slug: [rozmiary]}`
do pliku zamiast do tabeli `base_sizes`. `--json` dozwolone wyłącznie
z `--kind bases` (inaczej błąd CLI).

## Bazy AoS

Z reguł `<rule name="Base Size">` w poddrzewie selectionEntry (wartości
normalizowane jak w unitle: wymiar `NNxMMmm`/`NNmm` lowercase, etykiety bez
wymiaru bez zmian). Paczka nie rozwiązuje entryLinków — jeśli pomiar pokrycia
na realnych danych pokaże >5% jednostek nie-Legends bez bazy, dopiero wtedy
dodajemy minimalne rozwiązywanie linków w obrębie katalogu.

## Testy (pierwsze w paczce)

pytest + fixtures (małe wycinki `.cat`/`.gst`/`.json` w `tests/fixtures/`):

- superfaction/faction/faction_tags: po jednym przypadku na grę + generic,
- heurystyki baz: port testów z unitle (`test_wh40k_base_size.py`),
- ekstrakcja baz AoS z fixture,
- kształt eksportu (nowe pola, `catalogue` zamiast surowego `faction`),
- migracja: `open_db` na bazie ze starym schematem nie traci danych.

## Weryfikacja E2E (kryteria sukcesu)

1. `fetch 40k` + `fetch 40k-10e` + `fetch aos` + `export` na żywych danych
   przechodzi (obie ścieżki parsera: JSON 11e i XML 10e).
2. Pokrycie baz: ≥95% rekordów nie-Legends typu unit/model w 40k-10e ma
   `base`; lista braków wypisana w raporcie weryfikacji (nie ukrywamy).
3. AoS: pokrycie baz raportowane; decyzja o linkach na podstawie liczby braków.
4. Dla próbki jednostek wspólnych z unitle wartości `base` są zgodne.
