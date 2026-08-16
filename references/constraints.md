# Constraints

- Never report a write as successful before read-back confirms it.
- Never parse or modify `.lbug`, `.wal`, `.shadow`, or `.tmp` files directly.
- Never write to Chronicle or any other skill's database.
- Never silently collapse two Person nodes into one.
- Use ontology standard relationship types in `Knows.rel_type`.
- Store useful, durable, socially actionable facts only.
- **Never push enrichment-derived values outbound.** Only owner-sourced data reaches Google Contacts; pseudo-contacts, archived and deceased records are never created there.
- No notes field for structured data — Person.notes column was dropped. Store metadata as Fact nodes with typed predicates.
- Surface lock errors immediately.
- Write a journal at the end of every run. Runs missing journals are invalid.
- Before outbound Google sync, verify Person-level fields are populated. Fact node data is NOT auto-synced to Google — aggregation step required.