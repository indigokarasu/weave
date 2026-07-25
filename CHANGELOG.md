## [3.3.1] - 2026-04-26

### Removed
- Ad-hoc migration scratch scripts: `scripts/weave_enrich.py` (hardcoded UUIDs with literal `enrichments={}`), `scripts/weave_upsert.py` (mock with `try: import ladybugdb`), `scripts/weave_upsert_batch.py` (hardcoded single-contact record). Git history is the archive.
- References to nonexistent `scripts/weave_sync_inbound.py` and `scripts/weave_sync_outbound.py` from SKILL.md. The actual implementation is `scripts/google_sync.py`, which runs inbound + outbound passes in a single invocation; SKILL.md cron table and Google Contacts sync section updated accordingly.

## [2.6.0] - 2026-04-12

### Added
- Google Contacts sync: inbound sync procedure, OAuth scope requirements, known pitfalls (false duplicate prevention, scope expansion, phone hygiene, bulk import)
- Write-back rule: requires config flag AND explicit per-sync user approval
- Clay/Mesh MCP integration note: old Clay REST API deprecated, current path is Mesh MCP via Smithery

### Changed
- Optional skill cooperation updated with Clay MCP entry

## [2026-04-04] Spec Compliance Update

### Changes
- Added missing SKILL.md sections per ocas-skill-authoring-rules.md
- Updated skill.json with required metadata fields
- Ensured all storage layouts and journal paths are properly declared
- Aligned ontology and background task declarations with spec-ocas-ontology.md

### Validation
- ✓ All required SKILL.md sections present
- ✓ All skill.json fields complete
- ✓ Storage layout properly declared
- ✓ Journal output paths configured
- ✓ Version: 2.4.0 → 2.4.1

# Changelog

## [2.5.1] - 2026-04-08

### Storage Architecture Update

- Replaced $OCAS_DATA_ROOT variable with platform-native {agent_root}/commons/ convention
- Replaced intake directory pattern with journal payload convention
- Added errors/ as universal storage root alongside journals/
- Inter-skill communication now flows through typed journal payload fields
- No invented environment variables — skills ask the agent for its root directory


## [2.5.0] - 2026-04-08

### Multi-Platform Compatibility Migration

- Adopted agentskills.io open standard for skill packaging
- Replaced skill.json with YAML frontmatter in SKILL.md
- Replaced hardcoded ~/openclaw/ paths with {agent_root}/commons/ for platform portability
- Abstracted cron/heartbeat registration to declarative metadata pattern
- Added metadata.hermes and metadata.openclaw extension points
- Compatible with both OpenClaw and Hermes Agent


## [2.4.0] - 2026-04-02

### Added
- Structured entity observations in journal payloads (`entities_observed`, `relationships_observed`, `preferences_observed`)
- `user_relevance` tagging on journal observations and optional signal emission (default `user` for social graph entities)
- Elephas journal cooperation in skill cooperation section

## 2.3.2

- Add skill_type field to skill.json for spec compliance
- Complete required SKILL.md sections per ocas-skill-authoring-rules.md
