ATTCharacterData = {
  ["Player-1111-00000001"] = {
    guid = "Player-1111-00000001", name = "Ada", realm = "Test Realm One", class = "MAGE",
    Lockouts = { raid = { name = "Nested boss" } },
  },
  ["Player-2222-00000002"] = {
    guid = "Player-2222-00000002", name = "Ada", realm = "Test Realm Two", class = "PRIEST",
  },
  ["Player-9999-00000009"] = {
    guid = "Player-9999-00000009", name = "Historical Extra", realm = "Another Realm",
  },
}
-- The same GUID in a different table must not overwrite a named ATT record.
OtherAddonTable = { ["Player-1111-00000001"] = { name = "Wrong table" } }
