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
| `location_country` | `addresses[0].countryCode`| `addresses` (array) | ISO 3166-1 alpha-2 code (2-char only, skip full names) |
| `birthday` (Fact) | `birthdays[0].date` | `birthdays` (array) | { "date": { "month": M, "day": D } } |
| `linkedin` (Fact) | `urls[x].value` | `urls` (array) | set `type: "LinkedIn"` |
| `website` (Fact) | `urls[x].value` | `urls` (array) | set `type: "Website"` |
| `instagram` (Fact) | `urls[x].value` | `urls` (array) | set `type: "Instagram"` |
| `notes.social_profiles` | `urls[x].value` | `urls` (array) | Platform name as `type`. Each `{platform, url}` entry becomes a separate `urls` object. |
| `spouse` (Knows) | `relations[x].person` | `relations` (array) | **Plain text name**, NOT resource ID. Set `type: "spouse"`. |

## Social Profile URL Types

The `notes` JSON field may contain `social_profiles` — arrays of `{platform, url}` objects. Each becomes a `urls` entry with `type` set to the platform name. Google People API accepts any string as `type`.

| Platform | `type` value |
| :--- | :--- |
| Twitter/X | `Twitter` |
| GitHub | `GitHub` |
| Medium | `Medium` |
| Dribbble | `Dribbble` |
| ArtStation | `ArtStation` |
| Threads | `Threads` |
| Pinterest | `Pinterest` |
| VSCO | `VSCO` |
| SoundCloud | `SoundCloud` |
| Behance | `Behance` |
| YouTube | `YouTube` |
| Spotify | `Spotify` |
| Other | Use platform name as-is |

## CRITICAL RULES
1. **NEVER use `biographies`/`notes` as a dumping ground.** All structured data goes in its proper Google People API field. Social profiles from `notes.social_profiles` → `urls` with platform labels.
2. **SYNC DIRECTION**: Every write to Weave MUST trigger a corresponding update to Google Contacts using this map.
3. **SENSITIVE DATA**: Do not sync private internal notes, confidence scores, or source metadata to Google Contacts.
4. **URL LABELS**: Google People API `urls.type` accepts any string. Use the platform name directly — do NOT dump multiple profiles into a single "Website" field.
