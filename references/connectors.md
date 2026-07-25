# Connectors

Bidirectional sync for Google Contacts and Clay. Both connectors run within the same process as Weave, sharing the same database connection.

Sync state is stored in `{agent_root}/commons/db/ocas-weave/config.json`.

## Rules (both connectors)

Inbound always runs before outbound in a sync session.
Conflict resolution: Weave provenance wins. External data fills gaps; it does not overwrite higher-confidence Weave records.
Outbound requires the relevant writeback flag `true` in config AND explicit per-sync user approval. Neither alone is sufficient.
Report counts after every sync: N upserted, N skipped, N pushed, N failed.

## Google Contacts

API: Google People API v1. Scope: `https://www.googleapis.com/auth/contacts`.

Field map (Google → Weave):

| Google Field | Weave Field |
|---|---|
| resourceName | google_resource_name |
| names[0].displayName | name |
| names[0].givenName | name_given |
| names[0].familyName | name_family |
| emailAddresses[0].value | email |
| phoneNumbers[0].value | phone |
| organizations[0].name | org |
| organizations[0].title | occupation |
| addresses[0].city | location_city |
| addresses[0].countryCode | location_country |

Inbound sync uses `google_sync.py` which handles pagination, matching (by resource name → email → phone), and gap-fill. Outbound sync pushes modified contacts via `BatchUpdateContacts` API.

<<<<<<< Updated upstream
Run: `AGENT_ROOT=<hermes-home>/profiles/indigo HOME=/root python3 {skill_root}/scripts/google_sync.py`
=======
Run: `AGENT_ROOT=~/.hermes/profiles/indigo HOME=/root python3 {skill_root}/scripts/google_sync.py`
>>>>>>> Stashed changes

## Google People API — Operational Learnings (Apr 2026, updated Jun 2026)

**Quota limits:**
- `Critical read requests` quota: 90/min per user per project
- Both GET (etag fetch) AND PATCH (update) count against this same 90/min bucket
- Use `BatchUpdateContacts` for outbound — up to 200 contacts per batch
- Use `people:batchGet` for etag fetching — 50 contacts per request
- Sleep 1.5s between batches of 200

**BatchUpdateContacts empty response:**
- The API may return HTTP 200 with empty body `{}` instead of `updateResult`
- Empty response means ALL contacts in the batch were updated successfully

**Rate limit backoff:**
- Start backoff at 5s minimum, not 1s
- On 429: retry with exponential backoff doubling from 5s (5s → 10s → 20s → 40s), max 4 attempts

**Etag requirement:**
- `updateContact` and `batchUpdateContacts` require current etag or returns 412/FAILED_PRECONDITION
- Etags are URL-encoded strings

**Stale references (404):**
- A Weave record may have `google_resource_name` but the contact was deleted from Google
- On 404: clear `google_resource_name` in Weave so future syncs don't retry

**Data integrity before sync:**
- Run validation before outbound sync to catch bad data from web enrichment
- Common issues: fragment phones (< 7 digits), full bios in occupation field, job titles in city field, overlong org fields
- Clear invalid fields rather than pushing bad data to Google

**Token path:**
- All auth goes through `scripts/google_api.py` which reads from `<user-google-email>.json`
- The shared module handles token refresh automatically

## Contact Snapshot Safeguard (required before outbound sync)

**CRITICAL**: Before ANY outbound sync that modifies Google Contacts, a snapshot MUST be taken.

Script: `scripts/contact_snapshots.py` (imported by `google_sync.py` automatically)

### How it works
1. `create_snapshot(resource_names)` fetches current Google state via `people:batchGet` (50 per request)
2. Stores each contact's full data in timestamped JSONL: `~/.hermes/commons/db/ocas-weave/snapshots/{sync_id}.jsonl`
3. Integrated into `google_sync.py` — runs automatically before every outbound push

### Recovery procedure
If bad data is pushed to Google:
1. List snapshots: `ls ~/.hermes/commons/db/ocas-weave/snapshots/`
2. Find the snapshot taken before the bad sync
3. Contact Google Contacts support or manually restore from the snapshot data

### Snapshot Script Pitfalls
- Token path must match the main sync script — both use `google_api.py`
- Always verify snapshot file has content after a run: `wc -l <snapshot_file>`

## Data Validation Before Sync

Before outbound sync, validate Weave data to catch enrichment bugs. Key checks:
- Phone: must have 7+ digits
- Org: not a fragment (< 3 chars), not too long (< 80 chars)
- Occupation: not truncated (starts lowercase), not a full bio (< 80 chars, no @)
- City: not a job title

Clear invalid fields rather than pushing bad data. An empty field is better than a wrong field.

## Clay

API: Clay REST API v1. Auth: Bearer token. Base: `https://api.clay.earth/v1`.

Field map (Clay → Weave):

| Clay Field | Weave Field |
|---|---|
| id | clay_id |
| name | name |
| first_name | name_given |
| last_name | name_family |
| email | email |
| phone | phone |
| company | org |
| title | occupation |
| city | location_city |
| country_code | location_country |

Inbound/outbound sync for Clay uses the same `google_api.py` auth pattern and WeaveDB SQLite backend.