{
  "run": "weave-overnight-enrichment",
  "timestamp": "2026-07-22T09:35:00Z",
  "mode": "agent-driven (cron)",
  "searxng_health": "healthy",
  "sync_inbound": {"fetched": 993, "upserted": 969, "skipped": 24},
  "sync_outbound": {"pushed": 588, "failed": 0},
  "garbage_cleared": {"orgs": 1, "occupations": 4, "uncorroborated_google": 1},
  "gap_contacts_found": 50,
  "enriched_verified": [
    {"id": "d52b68cc-9064-5f62-9661-bc840de9dc02", "name": "Curtis Stanier", "occupation": "Senior Product Manager", "location_city": "St. Helier, Jersey", "confidence": 0.9},
    {"id": "792fe6c5-4fef-46d9-829f-108938c8a6db", "name": "Harsh Bhangalia", "occupation": "User Experience Lead", "org": "<employer>", "location_city": "San Francisco, CA", "confidence": 0.85},
    {"id": "883baecb-4b57-4e1d-8499-8c0cb3899763", "name": "Payali Majumdar", "occupation": "Insights Lead", "org": "<vendor>", "location_city": "London, United Kingdom", "confidence": 0.8},
    {"id": "9aec9f64-eac6-4350-bf9b-e6425928faa5", "name": "Anant Garg", "occupation": "Product Designer & Strategist", "confidence": 0.7},
    {"id": "75020f18-7fa7-402e-aa80-ac7b2c3fce75", "name": "Rajeev Kumar", "occupation": "UX/UI Designer", "location_city": "Chandigarh, India", "confidence": 0.8}
  ],
  "skipped": {"non_person": 12, "unresolvable": 23, "insufficient_data_already_filled": 10},
  "db_state": {"persons": 1082, "with_occupation": 1028, "with_org": 1032, "facts": 7939, "edges": 31728},
  "notes": "Stale runbook in cron message (enrichment_data.py, ladybug-bridge) ignored per SKILL.md CRON INVOCATION warning. execute_code not used (blocked in cron); terminal + temp scripts used. Decisions logged to decisions.jsonl."
}
