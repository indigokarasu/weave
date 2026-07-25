# Google People API Technical Specification

Technical requirements for interacting with the Google People API to avoid 400/403 errors and data loss.

## 1. Authentication & Scopes
- **Required Scope**: `https://www.googleapis.com/auth/contacts`
- **Method**: OAuth2. Ensure the token is refreshed via `google-auth-library` before calls.

## 2. Update Pattern (The "Safe Update")
The People API uses a strict `etag` based concurrency model. A `PATCH` request without the current `etag` will fail.

**Correct Request Flow:**
1. `GET https://people.googleapis.com/v1/people/{resourceName}?personFields=X,Y,Z`
2. Extract the `etag` from the response.
3. `PATCH https://people.googleapis.com/v1/people/{resourceName}:updateContact?updatePersonFields=X,Y,Z`
4. Payload MUST include the `etag` and only the fields being changed.

## 3. JSON Formatting Gotchas
- **Case Sensitivity**: The REST API uses `camelCase` for all fields. (`personId` $\to$ FAIL, `person` $\to$ PASS).
- **Relations**: To link two people, use the `relations` array. The `person` field in a relation must be the **Plain Text Name** of the related person, NOT the `people/xxx` resource ID.
- **Birthdays**: Must use the `date` object `{ "month": integer, "day": integer }`. Do not send as a string.
- **URLs**: Use the `type` field for labels (e.g., `"type": "LinkedIn"`). Do not use a `label` field.

## 4. Endpoint Table
| Action | Method | Endpoint |
| :--- | :--- | :--- |
| Get Person | `GET` | `/v1/people/{resourceName}` |
| Update Person | `PATCH` | `/v1/people/{resourceName}:updateContact` |
| Search Contacts | `POST` | `/v1/people:searchContacts` |
| Create Contact | `POST` | `/v1/people:createContact` |

## 5. Troubleshooting
- **400 Bad Request**: Usually means an unknown field name in the JSON payload. Check `camelCase`.
- **403 Forbidden**: Insufficient scopes. Check if `contacts` scope is actually granted in the current token.
- **404 Not Found**: Check if you are using the `:updateContact` suffix on the URL.