# Weave vCard Projection

## Principles
- Projection is read-only: it generates a draft, not a writeback
- Strict mode (default): omit any field where data is absent or confidence is low
- Conservative: prefer omission over invention
- Standards-conscious: follow vCard 4.0 field semantics

## Field Mapping
- FN → Person.name
- NICKNAME → Person.aliases (if present)
- EMAIL → identifiers where type=email
- TEL → identifiers where type=phone (only if explicitly stored)
- ORG → facts where predicate=works_at
- NOTE → person notes (truncated)

## Omission Rules
- If a field has no backing data, omit it entirely
- If confidence is low, omit unless the user explicitly requests inclusion
- Never generate synthetic data to fill gaps
