# Weave Schemas

## Person
```json
{"id":"string","name":"string","aliases":["string"],"identifiers":[{"type":"string","value":"string"}],"notes":"string|null","tags":["string"],"provenance":[{"source":"string","captured_at":"string","evidence_ref":"string"}]}
```

## Fact
```json
{"fact_id":"string","subject_id":"string","predicate":"string","object":"string","confidence":"string — high|med|low","provenance":[{"source":"string","captured_at":"string","evidence_ref":"string"}]}
```

## Relationship
```json
{"relationship_id":"string","person_a_id":"string","person_b_id":"string","type":"string — friend|spouse|colleague|introduced_by|knows_from|neighbor|family","directional":"boolean","confidence":"string","provenance":["object"],"notes":"string|null"}
```

## Experience
```json
{"experience_id":"string","person_ids":["string"],"type":"string — dinner|trip|meeting|gift|event","date":"string","place":"string|null","description":"string","provenance":["object"]}
```

## QueryRequest
```json
{"query_id":"string","type":"string — lookup|serendipity|summarize","params":"object","constraints":"object|null"}
```

## VCardProjection
Maps graph-safe contact fields to vCard 4.0 draft output. Omit fields where data is absent or uncertain. Never invent data.

## DecisionRecord
Extends shared DecisionRecord. Weave-specific types: person_created, fact_stored, relationship_updated, writeback_approved, writeback_rejected.
