# Self-update

`weave.update` is **retired**. Skill updates are centralized: the fleet-wide
`skills:update-fleet` cron runs `update_skill.sh` at the agent root, which syncs
every skill repo from its `source:` URL and never discards uncommitted or
unpushed local work.

Do NOT `git pull` this skill in place, and do not re-add a per-skill updater.
The in-skill wrapper was removed 2026-09-24 because it duplicated — and in some
cases contradicted — the safe centralized updater.

## What to do when the skill is stale

- [ ] Check the fleet updater ran: look for `update_skill.sh` output in the
      cron log for the `skills:update-fleet` job
- [ ] If the job errored, the `jobs.json` registry keeps a stale
      `last_status=error` until the next scheduled execution — force it with
      `hermes cron run <job_id>` and verify the last status flipped
- [ ] If the fleet updater cannot be run, say so and stop. Do not improvise a
      manual pull: a discard-capable pull can lose unpushed local work.

## What to verify after any update

A pull can break two things silently:

- **Frontmatter.** Validate after every update:
  `python3 -c "import yaml; yaml.safe_load(open('<path>/SKILL.md').read().split('---')[1])"`
- **Phantom references.** An upstream drop of a `references/*.md` file while
  SKILL.md still points at it leaves a link to a subsystem that no longer
  exists. `tests/test_weave_invariants.py` asserts this; run
  `python3 -m unittest discover -s tests`.

## Scope

Updates refresh documentation and helper scripts. They never migrate the
database and never re-run a sync. Schema changes require the explicit migration
path in `database_maintenance.md`.
