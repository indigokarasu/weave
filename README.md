# 🕸️ Weave

Private provenance-backed social graph using LadybugDB.

**Skill name:** `ocas-weave`
**Version:** 2.2.0
**Type:** system
**Layer:** Memory
**Author:** Indigo Karasu

---

## Files

| File | Purpose |
|---|---|
| `skill.json` | Package metadata and routing description |
| `SKILL.md` | Operational instructions for the agent |
| `references/` | Support files referenced by SKILL.md |

---

## Changelog

### 2.2.0 (2026-03-22)

- Added short-name routing aliases to skill.json description and SKILL.md frontmatter for natural invocation ('Scout', 'Sift', etc.)
- Added trigger phrases to descriptions for improved routing accuracy
- Cross-skill references in descriptions now use 'use X' format for routing clarity

### 2.1.0 (2026-03-22)

- Added Run completion section with explicit journal write for every command
- Added Initialization section documenting auto-init behavior
- Removed non-conformant OCAS_ROOT environment variable reference from prose and Python code
- Fixed Python code to use literal ~/openclaw/ path

### 2.0.0 (2026-03-18)

- Initial build of all OCAS skills as a unified suite
