# Build Spec: `ocas-weave` v1.1.0

## Purpose
Build a complete, ready-to-install Agent Skill package named `ocas-weave`.

`ocas-weave` is a provenance-backed personal social graph skill for OpenClaw. It maintains a private, queryable graph of people, relationships, interests, preferences, places, experiences, and interaction history so the agent can support recall, gifting, hosting, introductions, follow-ups, and serendipity without relying on speculative memory.

## Skill identity
- Name: `ocas-weave`
- Version: `1.1.0`
- Author: `Indigo Karasu`
- Email: `mx.indigo.karasu@gmail.com`
- Skill type: `system`

## One-sentence build objective
Produce a real Agent Skill package that lets an OpenClaw agent capture and query a private provenance-backed social context graph, generate deterministic socially useful answers and suggestions from that graph, optionally project records to standards-compliant vCard output, and never write back to external contact systems without explicit approval.

## Responsibility Boundary

Weave specializes in relationship networks between people.

Chronicle (Elephas) stores general world knowledge across Entities, Places, Concepts, and Things.

Weave may reference Chronicle records but does not replace Chronicle.

---

## Build rules
The coder must follow these rules:
- build a real Agent Skill package, not a planning memo
- keep `SKILL.md` lean, operational, and routing-aware
- preserve the full functional surface described in this file
- define every required term locally
- prefer exact schemas, examples, and commands over long explanation
- include support files only where they materially improve reliability or maintainability
- treat the package as private and local-first
- do not invent cloud services, external databases, or hidden background systems unless explicitly specified here
- optimize for deterministic behavior, provenance, and safe write controls

## Required output
Produce this package:

```text
ocas-weave/
  skill.json
  SKILL.md
  references/
    schemas.md
    query_patterns.md
    vcard_projection.md
```

Do not add `scripts/` or `assets/` unless absolutely necessary to satisfy a requirement in this spec.

## Package specification

### `skill.json`
Create a valid `skill.json` with at minimum:
- `name`: `ocas-weave`
- `version`: `1.0.1`
- `description`
- `author`: `Indigo Karasu`
- `email`: `mx.indigo.karasu@gmail.com`

The description must make routing obvious. It should clearly state that the skill is for maintaining and querying a private social graph with provenance, useful for requests like:
- “remember that Alex likes natural wine and modernist design”
- “who do we know in Tokyo”
- “what gift ideas fit Shannon’s taste”
- “summarize what matters about this person before dinner”
- “turn this contact into a strict vCard draft”

The description should also make clear that the skill should not trigger for generic contact editing, CRM automation, or speculative people analysis without evidence.

### `SKILL.md`
`SKILL.md` must be the main operational file and must stay lean. It must contain these exact sections, in this order:
1. Title
2. When to use
3. When not to use
4. Purpose and boundaries
5. Core entities
6. Commands
7. Operating rules
8. Query and response rules
9. vCard projection rules
10. Writeback gating
11. Storage model
12. Support file map
13. Validation rules

`SKILL.md` must tell the agent:
- what requests should trigger the skill
- which commands exist and when to use them
- how evidence and provenance must be handled
- when to read each reference file
- that queries must be deterministic against the same graph state
- that external writeback is always gated

Target `SKILL.md` size:
- 150 to 260 lines
- concise but operational

## Functional surface that must be preserved
The final package must preserve all of the following capabilities.

### 1. Private provenance-backed graph
The skill maintains a local graph with explicit provenance for every material fact. Facts may originate from:
- direct user statements
- explicitly imported records
- message-derived summaries
- manually entered updates
- interaction/event records

Every persisted fact must retain:
- source type
- capture timestamp
- evidence reference or source note
- confidence level

The graph is private by default. The skill must not assume remote sync, external storage, or background enrichment.

### 2. Graph primitives
The graph must support at least these primitives:
- `Person`
- `Relationship` as a typed edge between people
- `Preference` or `Interest` as provenance-backed facts
- `Experience` as a dated interaction, event, meeting, trip, dinner, gift, or shared moment

These primitives must be sufficient to answer:
- who a person is
- how they relate to others
- what they like or avoid
- where they are associated
- what happened with them and when

### 3. Deterministic query layer
The skill must support deterministic, reproducible queries against the current graph snapshot.

Required query modes:
- `lookup` for direct retrieval
- `serendipity` for useful suggestion generation grounded only in stored evidence
- `summarize` for person/context digests

Deterministic means:
- the same query against the same graph state returns the same material result set
- the skill may rank or group results, but the logic must be consistent
- the skill must not fabricate facts to improve narrative flow

### 4. Upsert commands
The skill must support these commands exactly:
- `weave.upsert.person`
- `weave.upsert.relationship`
- `weave.upsert.preference`
- `weave.query`
- `weave.project.vcard`
- `weave.writeback.contacts`
- `weave.status`

Each command must be explained in `SKILL.md`.

Required command behaviors:

#### `weave.upsert.person`
Creates or updates a person record.
Must support:
- canonical name
- aliases
- identifiers
- optional notes
- provenance

Should merge carefully rather than duplicating obvious same-person records when strong identifiers are present.
If identity is ambiguous, the skill should preserve separation or surface the ambiguity instead of silently collapsing records.

#### `weave.upsert.relationship`
Creates or updates a typed relationship between two people.
Must support:
- relationship type
- directionality if relevant
- provenance
- confidence
- optional notes

Examples:
- friend
- spouse
- colleague
- introduced_by
- knows_from
- neighbor
- family

#### `weave.upsert.preference`
Stores a provenance-backed preference, dislike, interest, constraint, habit, or affinity.
Must support examples like:
- dietary preference
- gift preference
- design taste
- travel preference
- favorite cuisines
- dislikes
- hobbies
- aversions

The skill must distinguish between stable preferences and one-off experiences when possible.

#### `weave.query`
Runs a graph query in one of the supported query modes.
Must support:
- person lookup
- relation lookup
- affinity lookup
- place-based lookup
- event or interaction recap
- serendipity suggestions grounded in evidence
- summary generation

#### `weave.project.vcard`
Generates a strict vCard 4.0 projection draft from a person record.
This is a projection, not a writeback.
The output must be standards-conscious and conservative.
If the graph lacks sufficient fields, the draft should remain partial rather than inventing data.

#### `weave.writeback.contacts`
Optional and gated.
This command may only be available when writeback is enabled and explicit user approval is present for the specific action.
The skill must never silently write to contacts.

#### `weave.status`
Returns current health/status of the skill state, including at minimum:
- whether the graph store exists
- count of people
- count of facts
- count of relationships
- whether writeback is enabled
- whether strict vCard mode is enabled

### 5. Config
The package must define the intended local config file at:
- `.weave/config.json`

The config namespace is `weave`.

Required config areas:
- `confidence.thresholds`
- `writeback.enabled` with default `false`
- `vcard.strict` with default `true`
- `retention.policy`

Do not overdesign the config. It only needs to define the intended configuration surface and defaults.

### 6. Storage layout
The package must specify this local storage layout:

```text
.weave/
  config.json
  graph.jsonl
  facts.jsonl
  decisions.jsonl
  exports/
  reports/
```

Storage rules:
- writes stay inside `.weave/`
- append-only JSONL should be preferred for graph/fact/decision records where feasible
- exports go under `.weave/exports/`
- human-readable reports go under `.weave/reports/`

### 7. Decision and explainability behavior
When the skill produces a materially interpretive output, especially in `serendipity` mode, it must ground the result in stored evidence.

Important behavior rules:
- suggestions must be explainable in plain language
- evidence references should point back to stored facts or experiences
- the skill should distinguish between strong evidence and weak hints
- if evidence is weak, the response should say so
- no speculative personality profiling

A lightweight decision log may be stored in `decisions.jsonl` for consequential interpretations or write actions.

### 8. vCard projection
The skill must support optional vCard 4.0 projection.
This is not a full contact-sync engine.

Projection rules:
- create draft-safe output only from known fields
- honor strict mode by omitting unsupported or uncertain values
- preserve original graph data separately from projection output
- never claim a vCard field exists unless it can be backed by stored data

### 9. Writeback gating
The skill must enforce all of the following:
- contact writeback is disabled by default
- enabling writeback in config is not by itself sufficient for execution
- every writeback action also requires explicit user approval at action time
- the skill must reject silent or implicit writeback
- if approval is missing, it must produce a safe refusal or draft instead

### 10. Minimization and sensitivity
The skill must minimize what it stores.
Rules:
- store useful, durable, socially actionable facts
- avoid sensitive attributes unless explicitly provided and clearly useful
- avoid speculative inference about private identity categories
- avoid unnecessary duplication
- prefer provenance-backed facts over broad narrative summaries

### 11. Social utility use cases
The package must explicitly support using the graph for:
- recall before seeing someone
- introductions and who-knows-who context
- hosting and guest fit
- gifting grounded in known preferences
- travel context such as “who is in this city”
- serendipity suggestions grounded in previous experiences and affinities
- compact person summaries for real-world interaction preparation

### 12. Non-goals and boundaries
The package must explicitly reject these interpretations:
- speculative OSINT or web enrichment by default
- a CRM or sales pipeline system
- autonomous outreach
- silent syncing to contacts
- unsupported claims about relationships or preferences
- personality analysis without evidence

## Reference files
Create these files and ensure `SKILL.md` points to them explicitly.

### `references/schemas.md`
Purpose:
Define the data contracts and lightweight schemas used by the skill.

Must include at minimum:
- `Person`
- `Relationship`
- `Fact`
- `Experience`
- `QueryRequest`
- `DecisionRecord`
- `VCardProjection`

The schemas do not need to be formal JSON Schema, but they must be precise and implementation-oriented.

Required minimum shape guidance:

#### `Person`
Must include fields equivalent to:
- `id`
- `name`
- `aliases`
- `identifiers`
- optional notes or tags

#### `Fact`
Must include fields equivalent to:
- `fact_id`
- `subject_id`
- `predicate`
- `object`
- `confidence`
- `provenance[]`

Each provenance item must include fields equivalent to:
- `source`
- `captured_at`
- `evidence_ref`

#### `QueryRequest`
Must support fields equivalent to:
- `query_id`
- `type` with values `lookup|serendipity|summarize`
- `params`
- `constraints`

#### `DecisionRecord`
Must be lightweight but sufficient to explain consequential interpretation or write decisions.

#### `VCardProjection`
Must map graph-safe contact fields to draft output fields while allowing omissions.

### `references/query_patterns.md`
Purpose:
Provide deterministic query patterns and response shapes for the most important use cases.

Must include examples for:
- who knows who
- what does this person like
- who do we know in a given city
- what matters before meeting this person
- what gift ideas are defensible from evidence
- what serendipity suggestions can be made from existing facts

Each pattern must show:
- the user-style ask
- the intended query mode
- the grounding logic
- the expected answer shape

### `references/vcard_projection.md`
Purpose:
Define conservative vCard projection rules.

Must include:
- projection principles
- strict-mode behavior
- field mapping examples
- omission rules for uncertain data
- examples of acceptable partial output

## Specificity map

### Be exact about
- skill name and metadata
- the seven command names
- config path and required config areas
- storage layout
- mandatory provenance for persisted facts
- deterministic query behavior
- writeback gating
- vCard projection boundaries
- the required `SKILL.md` section order
- the required reference filenames and purposes

### Allow flexibility in
- wording of examples
- relationship type taxonomy beyond the required examples
- confidence threshold values
- formatting of human-readable responses
- exact internal file field names so long as the required semantics are preserved

## Validation requirements
A build is complete only if all of the following are satisfied.

### Routing validation
Prompts that should trigger the skill:
- “Remember that Alex hates truffle and loves Japanese stationery.”
- “Who do we know in Honolulu?”
- “Summarize what matters about Morgan before dinner tomorrow.”
- “What gift ideas fit Priya based on what’s already known?”
- “Create a strict vCard draft for this contact from what’s in memory.”
- “How does Sam know Jordan?”

Prompts that should not trigger the skill:
- “Search the web for everything about this person.”
- “Send this person a message.”
- “Automatically sync my contacts in the background.”
- “Invent a personality profile for this executive.”
- “Do generic note-taking for this meeting.”

### Structural validation
Confirm:
- all required files exist
- `skill.json` metadata is correct
- `SKILL.md` uses the required section order
- all three reference files exist
- `SKILL.md` explicitly tells the agent when to consult each reference file
- no support directory exists without justification
- duplication is controlled and secondary detail is kept out of `SKILL.md`

### Functional validation
Confirm:
- every material fact requires provenance
- the seven commands are implemented in the package guidance
- repeated identical queries against the same graph state are defined as deterministic
- writeback is gated by config and action-time approval
- vCard projection is draft-safe and conservative
- the skill supports recall, introductions, hosting, gifting, travel context, and serendipity use cases
- the skill rejects speculative or ungated behaviors outside scope

### Usefulness validation
Confirm:
- the skill has one sharp promise: maintain and query a private social graph with provenance
- first useful action is obvious from `SKILL.md`
- the package is specific where failure is costly
- the package remains concise enough to maintain

## Optional Skill Cooperation

This skill may cooperate with other skills when present but must never depend on them.
If a cooperating skill is absent, this skill must still function normally.

- Elephas — reference Chronicle records for general entity context.
- Scout — provide social graph context during people investigations.
- Dispatch — provide participant context for communication threads.

---

## Journal Outputs

This skill emits the following journal types as defined in the OCAS Journal Specification (spec-ocas-Journals.md):

- Observation Journal

Weave emits Observation Journal entries recording relationship discoveries, preference captures, and identity resolution decisions.

---

## Visibility

visibility: public

---

## Universal OKRs

This skill must implement the universal OKRs defined in the OCAS Journal Specification (spec-ocas-Journals.md).

Required universal OKRs:

- Reliability: success_rate >= 0.95, retry_rate <= 0.10
- Validation Integrity: validation_failure_rate <= 0.05
- Efficiency: latency trending downward, repair_events <= 0.05
- Context Stability: context_utilization <= 0.70
- Observability: journal_completeness = 1.0

Skill-specific OKRs should be defined in the built SKILL.md to measure domain-relevant outcomes.

---

## Final response format for the coder
Return:
1. package tree
2. full contents of every file
3. brief validation summary

Do not return planning commentary or process narration.
