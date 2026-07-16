import json

from bsdata_importer import BUILTIN_GAMES, parse_catalogue_rich

CATALOGUE = json.dumps({
    "catalogue": {
        "id": "cat-1",
        "name": "Xenos - Testers",
        "sharedProfiles": [
            {"id": "p-unit", "name": "Grunt", "typeName": "Unit",
             "characteristics": [{"name": "W", "$text": "3"}]},
        ],
        "sharedSelectionEntries": [
            {"id": "e-grunt", "name": "Grunt", "type": "unit",
             "infoLinks": [
                 {"id": "l-1", "name": "Grunt", "type": "profile",
                  "targetId": "p-unit"},
             ],
             "categoryLinks": [
                 {"id": "c-1", "name": "Infantry", "targetId": "x"},
                 {"id": "c-2", "name": "Faction: Testers", "targetId": "y"},
             ],
             "rules": [
                 {"id": "r-1", "name": "Base Size", "description": "32mm"},
             ],
             "selectionEntries": [
                 {"id": "e-sub", "name": "Grunt Leader", "type": "model",
                  "profiles": [
                      {"id": "p-sub", "name": "Grunt Leader", "typeName": "Unit",
                       "characteristics": [{"name": "W", "$text": "4"}]},
                  ]},
             ]},
            {"id": "e-upgrade", "name": "Banner", "type": "upgrade"},
            {"id": "e-noprofile", "name": "Empty", "type": "unit"},
        ],
    },
}).encode()


def test_rich_json_resolves_links_rules_and_nesting():
    game = BUILTIN_GAMES["40k"]
    faction, units = parse_catalogue_rich(
        CATALOGUE, game, qualify_profile_type="Unit")

    assert faction == "Xenos - Testers"
    # sub-model zagnieżdżony w kandydacie odpada; wpis bez profilu Unit też
    assert [u.name for u in units] == ["Grunt"]

    unit = units[0]
    ws = sorted(p["chars"]["W"] for p in unit.profiles if p["type"] == "Unit")
    assert ws == ["3", "4"]
    assert set(unit.categories) == {"Infantry", "Faction: Testers"}
    assert unit.rules == [{"name": "Base Size", "description": "32mm"}]


def test_rich_json_without_qualifier_keeps_profileless_units():
    game = BUILTIN_GAMES["40k"]
    _, units = parse_catalogue_rich(CATALOGUE, game)
    assert {u.name for u in units} == {"Grunt", "Empty"}
