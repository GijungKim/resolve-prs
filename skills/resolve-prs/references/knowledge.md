# Knowledge lifecycle

Read this reference when a PR matches `knowledge/patterns.json`, when a recorded pattern is stale, or after a novel migration succeeds.

## Using entries

- A pattern is a lead for investigation, never merge evidence.
- Verify `appliesWhen` against the actual dependency graph and build path.
- Treat an observed entry older than the configured `knowledgeMaxAgeMonths` as stale. Re-test before using its guidance.
- Curated entries without observation dates remain general heuristics and still require validation.
- If the installed version, target version, framework, package manager, or bundler differs materially, do not assume the pattern applies.

List matching entries with:

```bash
python3 "$RESOLVE_PRS_SKILL_DIR/scripts/resolve_prs.py" patterns --package PACKAGE
```

## Proposing entries

Do not edit an installed skill or its knowledge file during a resolution run. Produce a candidate:

```bash
python3 "$RESOLVE_PRS_SKILL_DIR/scripts/resolve_prs.py" candidate \
  --id PACKAGE-FROM-TO \
  --ecosystem javascript \
  --package PACKAGE \
  --from-version FROM \
  --to-version TO \
  --summary "What changed" \
  --guidance "How it was fixed" \
  --source "release or migration URL"
```

Include the JSON in the final report. A maintainer can contribute it through a normal reviewed change.

Candidate acceptance requires:

- applicability beyond one repository;
- an explicit version range or applicability condition;
- reproducible validation evidence;
- a primary release, migration, or issue source when available;
- no secrets or repository-specific paths;
- no duplicate or contradictory active entry.

Refresh `lastVerified` only through a reviewed knowledge change after the guidance succeeds again.
