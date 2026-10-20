# Profile Picture Discovery & Sync

## Modes

- **additive** (`WEAVE_PIC_MODE=additive`): Only add an image when the contact has no `avatar_url` yet.
- **improve** (`WEAVE_PIC_MODE=improve`): Add an image when the current avatar is NOT a photograph (no face detected, or clearly a monogram/non-person graphic). Never replaces a picture that already contains a detected human face.
- **auto** (default): additive for the first run, improve on subsequent runs.

## Environment Variables

- `WEAVE_PIC_MODE`: Set to `additive`, `improve`, or `auto`. If unset, profile picture enrichment is disabled (preserves nightly run).
- `WEAVE_BATCH_SIZE`: Number of contacts to process per run (default 200).
- `WEAVE_IGNORE_COOLDOWN`: Set to `1` to ignore the cooldown period and process all contacts.

## How It Works

- **Candidate discovery**: Uses only known sources already associated with the contact (GitHub, Twitter/X, Instagram, Facebook, and Gravatar from email). The image is known to be the person because it comes from a verified source. **LinkedIn is NOT scrapeable** — automated fetches return redirects/favicons, not the profile picture; only use a direct `media.licdn.com` URL if one was already captured. NO SearXNG image search, free-text search, or random web images are used.

2. **Image Analysis**:
   - **Face Detection**: Uses YuNet ONNX model (cv2.FaceDetectorYN_create) to detect human faces. Requires confidence >= 0.5 and face area >= 5% of image area.
   - **Image Quality**: Computes sharpness (Laplacian variance), color variance, and a photo-likelihood guess.
   - **Placeholder Filtering**: Rejects known placeholder/generic images (Gravatar defaults, Pinterest open graph, etc.).

3. **Candidate Scoring**:
   ```
   score = (photo + face * conf + sharp * 0.4 + color * 0.2) / 1.6
   ```

4. **Mode Logic**:
   - **additive**: Skip if contact already has an avatar_url.
   - **improve**: Skip if current avatar already has a face detected. Also skip if current image quality is noticeably better than candidate (score + 0.1).

5. **Persistence**:
   - Updates `persons.avatar_url` in the Weave SQLite database.
   - Creates a fact record for provenance.

6. **Google Contacts Sync**:
   - Uses Google People API `updateContactPhoto` endpoint (POST with photoBytes).
   - Base64-encodes the image and sends it to Google Contacts.
   - Only syncs if the contact has a `google_resource_name`.

## Files

- `profile_pic.py`: Main module with candidate discovery, image analysis, scoring, and DB persistence.
- `face_detection_yunet.onnx`: YuNet face detection model (~340KB).
- `overnight_enrichment.py`: Integrated profile picture enrichment step (gated by WEAVE_PIC_MODE).
- `google_sync.py`: Added `update_contact_photo` function.
- `test_profile_pic.py`: Unit tests for the module.

## Usage

```bash
export HERMES_HOME=/root/.hermes/profiles/indigo
export AGENT_ROOT=/root/.hermes/profiles/indigo
export WEAVE_PIC_MODE=improve
export WEAVE_BATCH_SIZE=20
/root/hermes-agent/.venv/bin/python /root/.hermes/profiles/indigo/skills/ocas-weave/scripts/overnight_enrichment.py
```

## Pitfalls

- **Face detection must be a hard gate**: skip candidates without a detected face. The scoring formula alone does not enforce this — an image with high photo/sharpness/color but no face can still win if not gated.
- **Revert avatars without faces**: After enrichment, scan all avatars with face detection; revert any that lack a detected face (set avatar_url=NULL, remove profile_pic fact). The scoring formula alone does not enforce face presence — an image with high photo/sharpness/color but no face can still win if not gated.

## Safety

- The feature is disabled by default (WEAVE_PIC_MODE unset).
- It uses only free sources (SearXNG, direct image fetch, Google cache, Wayback Machine).
- It does not break the nightly run when disabled.
- All changes are atomic: DB update and fact creation happen in a single transaction.