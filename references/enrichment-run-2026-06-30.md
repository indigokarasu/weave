# Enrichment Run — June 30, 2026

## Summary
- **Google OAuth**: Revoked (`invalid_grant`). Skipped all Google sync.
- **SearXNG**: Healthy.
- **Contacts enriched**: 20+ (across 3 direct batches + 3 parallel subagents).
- **Database coverage**: 95% (1000/1052 have at least occupation or org; 975 have both).

## What Worked

### web_search as primary tool
`web_search` (Exa AI) returned higher-quality LinkedIn data than SearXNG for every contact. The LinkedIn title + description format ("Job Title at Company | LinkedIn" + location in description) was reliably parseable. This is now the recommended first step for every contact.

### Parallel delegation
Splitting 30 contacts across 3 subagents (10 each) with `delegate_task` worked well. Subagents used `web_search` + `terminal` + `write_file` independently. They enriched contacts I hadn't gotten to (Seamus McDonald, Liv Keil, Pernilla Nilsson, Jessie Oppenheimer, Lauren Knochelmann, Jody Badiei).

### Explicit state-check write pattern
Reading current state first, then building a dynamic UPDATE with only NULL/empty fields, was cleaner than COALESCE. Avoids redundant fact writes and makes debugging easier.

### Batch script approach
Writing a Python script to `/tmp/weave_batch_enrich_vN.py` and running with `python3 /tmp/...` was reliable. Each batch handled 5-12 contacts. Read-back verification after each write confirmed data integrity.

## What Didn't Work

### Stale cron runbook
The invocation message contained a hardcoded runbook referencing `enrichment_data.py` and `systemctl stop ladybug-bridge-weave.service` — both removed. I caught this from the skill's own docs but wasted a tool call on the bridge stop before realizing. **Fix**: Added top-level "CRON INVOCATION" section to skill.

### google_sync.py requires AGENT_ROOT env var
<<<<<<< Updated upstream
Running `python3 google_sync.py` without `AGENT_ROOT=<hermes-home>/profiles/indigo HOME=/root` fails with `FileNotFoundError` for config.json. The skill's background tasks table shows the correct invocation, but the runbook didn't include it.
=======
Running `python3 google_sync.py` without `AGENT_ROOT=~/.hermes/profiles/indigo HOME=/root` fails with `FileNotFoundError` for config.json. The skill's background tasks table shows the correct invocation, but the runbook didn't include it.
>>>>>>> Stashed changes

### Subagent ID mismatch
Subagents sometimes used different person IDs than expected (e.g., Cameron Moberg had two records: `8d3db255` and `41753824`). Both needed enrichment. Always query by name to find all variants.

### Ambiguous names
Gwendolyn McGinn, Shelley Reed Stoltz, Stephanie Goodnight, Karen Simonsen — common names with no disambiguation signals. Correctly skipped per unresolvable contacts protocol.

## Contacts Enriched This Run

| Name | Occupation | Org | Source |
|------|-----------|-----|--------|
| Siska Marcus | Spirits & Cocktail Specialist / Founder | Chili Cali | muckrack.com |
| Jeremiah Lee | Concept Artist / Character Art Director | Freelance | linkedin.com/in/jeremiah-lee-80619660 |
| Jenny Shears | Founder | StarMaps Creative PR & Partnerships | linkedin.com/in/jennyshears |
| Thomas Wedell | Owner | Skolos-Wedell | linkedin.com/in/twedell |
| Michael McQueen | Futurist / Author / Change Strategist | ODE Management | au.linkedin.com/in/michaelmcqueen1 |
| Jerome Francis Reding | Owner | JLR Engineering, LLC | linkedin.com/in/jerry-reding-a7824922 |
| Yingzhao Liu | Design Leader | The Robert H. N. Ho Family Foundation | linkedin.com/in/yingzhao |
| Nik Seif | Software Engineer | CrowdStrike | linkedin.com/in/nik-seif/ |
| Moe Tanabian | CEO and Founder | IntuigenceAI | linkedin.com/in/mtanabian/ |
| <counterparty> Nguyen | Staff Interaction Designer | YouTube | linkedin.com/in/kimpletedesign |
| Rick Gross | Chief Financial Officer | Rubris Inc | linkedin.com/in/rick-gross-3765246 |
| Elisa Cheng | Web Designer / Project Manager | Tiny Dot Designs | linkedin.com/in/elisamcheng |
| Cameron Michael Moberg | Creator / Pastor / Artist | CAMER1 | linkedin.com/in/cameron-moberg-1281866 |
| Jesse Lefkowitz | Art Director + Design Manager | YouTube | linkedin.com/in/jelefkowitz |
| Monica Perrone | Landscape Architect | Monica Perrone Landscape Architecture | linkedin.com/in/monica-perrone-59339510 |
| Ryan Gross | Owner / Vintage Dealer | BottleSnake Vintage and Designer Goods | inferred |
| Abi Jones (×2) | UX Design Manager | Google | linkedin.com/in/jonesabi |
| Jessica Phoenix | Volcanologist, Podcast Host, Writer | Independent / Freelance | linkedin.com/in/volcanojess |
| Laurie A Greengrass <operator-last> | Healing Practitioner / Owner | LZ's Healing Hands | inferred |
| Seamus McDonald | Tattoo Artist | Traditional Ink | subagent |
| Liv Keil | Chefrådgiver | Operate A/S | subagent |
| Pernilla Nilsson | Professor | Högskolan i Halmstad | subagent |
| Jessie Oppenheimer | RN | Johns Hopkins Hospital | subagent |
| Lauren Knochelmann | Owner | Thunder Ridge Farm | subagent |
| Jody Badiei | Owner | Clocktower Coffee Roasting Co. | subagent |

## Action Items for Next Run
1. **<operator> must re-authorize Google OAuth** — token is revoked, sync is unavailable
2. Clear pre-existing garbage data before enriching (still some junk values from prior runs)
3. Consider merging duplicate person records (Abi Jones ×2, Cameron Moberg ×2)
4. The 51 remaining "neither filled" contacts are mostly businesses or unresolvable names