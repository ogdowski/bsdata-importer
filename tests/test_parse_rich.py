import textwrap

from bsdata_importer import BUILTIN_GAMES, parse_catalogue_rich

NS = "http://www.battlescribe.net/schema/catalogueSchema"

CATALOGUE = textwrap.dedent(f"""\
    <catalogue xmlns="{NS}" name="Test Faction">
      <sharedProfiles>
        <profile id="p-unit" name="Grunt" typeName="Unit">
          <characteristics>
            <characteristic name="W">3</characteristic>
          </characteristics>
        </profile>
      </sharedProfiles>
      <selectionEntries>
        <selectionEntry id="e-grunt" name="Grunt" type="unit">
          <infoLinks>
            <infoLink targetId="p-unit" type="profile"/>
          </infoLinks>
          <categoryLinks>
            <categoryLink name="Infantry"/>
          </categoryLinks>
          <rules>
            <rule name="Base Size">
              <description>32mm</description>
            </rule>
          </rules>
          <selectionEntries>
            <selectionEntry id="e-sub" name="Grunt Leader" type="model">
              <profiles>
                <profile id="p-sub" name="Grunt Leader" typeName="Unit">
                  <characteristics>
                    <characteristic name="W">4</characteristic>
                  </characteristics>
                </profile>
              </profiles>
            </selectionEntry>
          </selectionEntries>
        </selectionEntry>
        <selectionEntry id="e-upgrade" name="Banner" type="upgrade"/>
        <selectionEntry id="e-noprofile" name="Empty" type="unit"/>
      </selectionEntries>
    </catalogue>
""").encode()


def test_rich_resolves_links_rules_and_nesting():
    game = BUILTIN_GAMES["40k-10e"]
    faction, units = parse_catalogue_rich(
        CATALOGUE, game, qualify_profile_type="Unit")

    assert faction == "Test Faction"
    # sub-model zagnieżdżony w kandydacie odpada; wpis bez profilu Unit też
    assert [u.name for u in units] == ["Grunt"]

    unit = units[0]
    # profil podpięty infoLinkiem jest widoczny + profil sub-modelu w poddrzewie
    ws = sorted(p["chars"]["W"] for p in unit.profiles if p["type"] == "Unit")
    assert ws == ["3", "4"]
    assert unit.categories == ["Infantry"]
    assert unit.rules == [{"name": "Base Size", "description": "32mm"}]


def test_rich_without_qualifier_keeps_profileless_units():
    game = BUILTIN_GAMES["40k-10e"]
    _, units = parse_catalogue_rich(CATALOGUE, game)
    assert {u.name for u in units} == {"Grunt", "Empty"}
