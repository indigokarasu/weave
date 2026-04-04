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

## [2.4.0] - 2026-04-02

### Added
- Structured entity observations in journal payloads (`entities_observed`, `relationships_observed`, `preferences_observed`)
- `user_relevance` tagging on journal observations and optional signal emission (default `user` for social graph entities)
- Elephas journal cooperation in skill cooperation section

## 2.3.2

- Add skill_type field to skill.json for spec compliance
- Complete required SKILL.md sections per ocas-skill-authoring-rules.md

