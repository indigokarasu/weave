# Interactive Menu

When invoked interactively (via `/` command), present a two-level menu using the `clarify` tool so the user can pick which function to run.

**Level 1 — Category selection** (max 4 choices):

```python
result = clarify(
    question="What would you like to do?",
    choices=[
        "People & Relationships — upsert persons, relationships, preferences",
        "Query & Export — query graph, import CSV, export data, generate vCard",
        "Connect & Init — sync Google Contacts, initialize database",
        "Status — show system status",
    ]
)
```

**Level 2 — Action selection** based on Level 1 choice:

- **People & Relationships** → clarify with choices: "upsert.person — Add/update a person", "upsert.relationship — Add/update a relationship", "upsert.preference — Store a preference"
- **Query & Export** → clarify with choices: "query — Query the social graph", "import.csv — Bulk import from CSV", "export — Export graph data", "project.vcard — Generate vCard"
- **Connect & Init** → clarify with choices: "sync.google-contacts — Sync with Google Contacts", "init — Initialize/repair database"
- **Status** → run "status — Show system status" directly (single action — no sub-menu needed)

After the user selects an action, execute it following the relevant procedure in this skill. Loop back to the menu after each action completes, until the user chooses to exit or sends `/stop`.

### Response parsing

Match the user's response against the full choice string. Extract the action key by splitting on `" — "` and taking the first segment. If the response doesn't match any known choice (user typed free-form via "Other"), match key prefixes case-insensitively. Re-present the current menu level on no match.

### Platform adaptation

On CLI, choices are navigable with arrow keys. On messaging platforms, choices render as a numbered list. The two-level hierarchy ensures no more than 4 options appear at any level on any platform.

