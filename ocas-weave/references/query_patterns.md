# Weave Query Patterns

## Who knows who
Ask: "How does Sam know Jordan?"
Mode: lookup. Query relationships between the two people. Return relationship type, provenance, shared experiences.

## What does this person like
Ask: "What are Alex's preferences?"
Mode: lookup. Query facts with subject=Alex and predicate matching preference categories. Return with provenance.

## Who do we know in a city
Ask: "Who do we know in Tokyo?"
Mode: lookup. Query people with known_locations containing Tokyo or experiences with place in Tokyo.

## Pre-meeting summary
Ask: "Summarize what matters about Morgan before dinner."
Mode: summarize. Return: name, relationship, key preferences, recent experiences, notable facts. Keep concise.

## Gift ideas from evidence
Ask: "What gift ideas fit Priya?"
Mode: serendipity. Query preferences, interests, and experiences. Generate suggestions grounded in specific stored facts. Every suggestion cites the supporting evidence.

## Serendipity connections
Ask: "Any interesting connections between these people?"
Mode: serendipity. Find shared preferences, mutual contacts, overlapping experiences. Only surface connections backed by stored evidence.
