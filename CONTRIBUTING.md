# Contributing

Thanks for improving `resolve-prs`. Keep the portable core small and put conditional detail in the narrowest relevant reference, helper, policy, or knowledge entry.

## Breaking-change knowledge

Patterns live in `skills/resolve-prs/knowledge/patterns.json`; they no longer belong in `SKILL.md`.

A useful entry must include:

- a stable unique `id`;
- ecosystem and package patterns;
- an explicit version range or `appliesWhen` condition;
- a concise description of the break and tested guidance;
- `trust: "curated"` for maintained heuristics or `trust: "observed"` with `learned` and `lastVerified` dates;
- primary release, migration, or issue sources when available.

Observed patterns should generalize beyond one repository. Do not include secrets, local paths, or an incident narrative that cannot guide another upgrade.

Generate a candidate after a successful migration:

```bash
python3 skills/resolve-prs/scripts/resolve_prs.py candidate \
  --id package-1-2 \
  --ecosystem javascript \
  --package package \
  --from-version 1.x \
  --to-version 2.x \
  --summary "What changed" \
  --guidance "How to migrate" \
  --source "https://example.com/migration"
```

Review and add the output through a normal pull request. Resolution runs must never modify an installed skill automatically.

## Workflow changes

- Keep host-specific packaging out of `SKILL.md`.
- Put GitHub behavior in `references/github.md` and validation behavior in `references/validation.md`.
- Put stable, repeated mechanics in `scripts/resolve_prs.py` with unit tests.
- Preserve head-and-base evidence gating, base-bound merge authorization, worktree isolation, sequential actions, and dry-run non-mutation.
- Add configuration only when it represents repository policy rather than universal behavior.

## Validation

```bash
python3 -m unittest discover -s tests -v
python3 skills/resolve-prs/scripts/resolve_prs.py validate-bundle
```
