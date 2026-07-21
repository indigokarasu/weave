# weave

<p align="center">
<img src="./assets/readme/hero.jpg" width="100%" alt="Weave: private social graph — provenance-backed contacts, relationships, preferences, and shared experiences.">
</p>

weave — Weave: private social graph — provenance-backed contacts, relationships, preferences, and shared experiences.


> Tell it what you need. It does the work.

## What it does

Weave maintains a private social graph where every stored fact carries provenance — source type, reference, timestamp, and confidence score. It supports meeting prep, gift ideas, hosting context, city connections, and serendipity discovery. The underlying database (LadybugDB) initializes automatically.

## Dependencies

- [Elephas](https://github.com/indigokarasu/elephas) — Chronicle enrichment
- [Scout](https://github.com/indigokarasu/scout) — OSINT findings as upsert candidates
- LadybugDB (embedded graph database)
- Google Contacts, Clay (optional sync)

---

*weave is part of the [OCAS Agent Suite](https://github.com/indigokarasu).*