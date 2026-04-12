# Google Contacts Field Mapping

Strict mapping for synchronizing data between Weave (LadybugDB) and Google People API.

## Weave (Person) → Google People API

| Weave Field | Google People API Field | JSON Path / Type | Notes |
| :--- | :--- | :--- | :--- |
| `name` | `names[0].displayName` | `names` (array of objects) | Primary display name |
| `name_given` | `names[0].givenName` | `names` (array of objects) | First name |
| `name_family` | `names[0].familyName` | `names` (array of objects) | Last name |
| `email` | `emailAddresses[0].value` | `emailAddresses` (array) | set `type: "home"` or `"work"` |
| `phone` | `phoneNumbers[0].value` | `phoneNumbers` (array) | set `type: "mobile"` |
| `org` | `organizations[0].name` | `organizations` (array) | Company name |
| `occupation` | `organizations[0].title` | `organizations` (array) | Professional title |
| `location_city` | `addresses[0].city` | `addresses` (array) | City name |
| `location_country` | `addresses[0].countryCode`| `addresses` (array) | ISO 3166-1 alpha-2 code |
| `birthday` (Fact) | `birthdays[0].date` | `birthdays` (array) | { "date": { "month": M, "day": D } } |
| `linkedin` (Fact) | `urls[x].value` | `urls` (array) | set `type: "LinkedIn"` |
| `website` (Fact) | `urls[x].value` | `urls` (array) | set `type: "Website"` |
| `instagram` (Fact) | `urls[x].value` | `urls` (array) | set `type: "Instagram"` |
| `spouse` (Knows) | `relations[x].person` | `relations` (array) | **Plain text name**, NOT resource ID. Set `type: "spouse"`. |

## CRITICAL RULES
1. **NO NARRATIVE DUMPS**: Never use `biographies` as a dumping ground for structured data. If a field is not in the map above, it stays in Weave only.
2. **SYNC DIRECTION**: Every write to Weave MUST trigger a corresponding update to Google Contacts using this map.
3. **SENSITIVE DATA**: Do not sync private internal notes or confidence scores to Google Contacts.
