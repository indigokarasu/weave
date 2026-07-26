# ⚙️ Weave

  <img src="./assets/readme/hero.jpg" width="100%" alt="Weave">

Private provenance-backed social graph. Maintains queryable records of people, relationships, preferences, and shared experiences for recall, gifting, hosting, introductions, and serendipity. Use for storing or retrieving facts about a person, recording a relationship, or discovering connections between people. Not for sending messages (use Dispatch), calendar management (use Sands), OSINT research (use Scout), or web research without a social graph need (use Sift).

**Skill name:** `ocas-weave`
**Version:** 4.3.0
**Type:** 
**Layer:** data-science
**Author:** <agent-name>

---

## 📖 Overview

Private provenance-backed social graph. Maintains queryable records of people, relationships, preferences, and shared experiences for recall, gifting, hosting, introductions, and serendipity. Use for storing or retrieving facts about a person, recording a relationship, or discovering connections between people. Not for sending messages (use Dispatch), calendar management (use Sands), OSINT research (use Scout), or web research without a social graph need (use Sift).

---

## 🔧 Commands

- **`enrichment_data.py` does NOT exist on disk.** Do not attempt to run it. Instead:
- `persons`: id, name, email, phone, location_city, location_country, occupation, org, google_resource_name, clay_id, source_type, source_ref, confidence, record_time, valid_from, valid_until
- `facts`: id, predicate, value, confidence, source_type, source_ref, record_time (NO person_id)
- `edges`: id, source_id, target_id, rel_type, strength, since, context, source_ref, confidence, record_time
- `web_extract` fails with SearXNG backend ("search-only backend cannot extract URL content"). Use `curl -s "https://r.jina.ai/URL"` for page content fetching instead.
- **`enrichment_data.py` does not exist**: There is no `enrichment_data.py` on disk. Use direct WeaveDB queries and `curl` for SearXNG health. See the "Agent-Driven Overnight Enrichment" section above.
- **`web_extract` cannot fetch URLs with SearXNG backend**: Use `curl -s "https://r.jina.ai/URL"` instead. This is the reliable page-fetching method in cron/agent context.

---

## 📊 Outputs

See `SKILL.md` for outputs, journals, and persistence rules.

---

## 📄 Files

| File | Purpose |
|---|---|
| `SKILL.md` | Skill definition |
| `references/` | Supporting documentation |
| `scripts/` | Helper scripts |


## Changelog

- [3.3.1] - 2026-04-26
- Removed
- [2.6.0] - 2026-04-12
- Added
- Changed
- [2026-04-04] Spec Compliance Update
- Changes
- Validation

---

## 📚 Documentation

Read `SKILL.md` for operational details, schemas, and validation rules.

Read `references/` for detailed specifications and examples.


---

## 📄 License

MIT License — see `LICENSE` for details.