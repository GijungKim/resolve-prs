# resolve-prs

An agent skill for safely assessing and resolving GitHub dependency-update PRs from Dependabot, Renovate, pyup, and conventional human-authored dependency bumps.

It works with agents that support the open `SKILL.md` format, including Claude Code and Codex. The workflow combines agent judgment with deterministic helpers for PR qualification, repository inspection, policy validation, and head-and-base evidence gating.

## What it does

1. Identifies dependency-update PRs and repository policy.
2. Assesses semver, peer/framework constraints, release notes, and affected surface.
3. Validates risky updates in isolated Git worktrees.
4. Binds validation evidence to the exact PR head and base SHAs.
5. Merges safe updates sequentially, fixes scoped migrations, or explains why an update should close/defer.
6. Emits novel migration knowledge as reviewable candidates instead of rewriting the installed skill.

## Install

### Portable installer

```bash
npx skills add GijungKim/resolve-prs
```

### Claude Code plugin

```text
/plugin marketplace add GijungKim/resolve-prs
/plugin install resolve-prs@resolve-prs
```

### Codex

Ask `$skill-installer` to install the `resolve-prs` skill from `GijungKim/resolve-prs`, or copy `skills/resolve-prs/` to `~/.agents/skills/resolve-prs/`.

## Usage

Claude Code:

```text
/resolve-prs
/resolve-prs --dry-run
/resolve-prs owner/repo
/resolve-prs --all --dry-run
```

Codex and other skill-aware agents:

```text
$resolve-prs resolve the dependency PRs in this repository
$resolve-prs assess owner/repo without changing anything
$resolve-prs assess every repository below this directory
```

The agent still follows its host's approval and sandbox rules. Resolve mode does not grant blanket permission outside the requested repositories.

## Optional policy

Add `.resolve-prs.json` to a target repository:

```json
{
  "$schema": "https://raw.githubusercontent.com/GijungKim/resolve-prs/main/skills/resolve-prs/references/policy.schema.json",
  "minimumReleaseAgeHours": 72,
  "mergeMethodPriority": ["squash", "rebase"],
  "allowAutoMerge": false,
  "allowedActions": ["merge", "fix", "comment", "request-rebase"],
  "ignore": ["expo-*", "react-native"]
}
```

The legacy `.resolve-prs-ignore` format remains supported.

## Architecture

```text
skills/resolve-prs/
├── SKILL.md                    portable operating contract
├── agents/openai.yaml          optional Codex/ChatGPT metadata
├── scripts/resolve_prs.py      deterministic read-only mechanics
├── references/                 GitHub, validation, policy, and knowledge guidance
└── knowledge/patterns.json     reviewed, versioned migration knowledge
```

The agent handles research and migration judgment. The helper handles repeatable classification and evidence checks. Repository policy is data, and ecosystem knowledge is maintained independently of the core prompt.

## Safety properties

- Dry-run/assessment performs no remote mutation.
- Medium/high-risk validation runs in disposable worktrees.
- Semver alone never authorizes a merge.
- Every merge requires evidence gated against the latest observed PR head and base; direct GitHub merges use a head-SHA compare-and-swap.
- Fixed PRs require new evidence after their head changes.
- PRs are merged sequentially to expose lockfile and peer conflicts.
- Auto-merge is disabled by default and is permitted only with policy approval.

## Prerequisites

- An agent that supports `SKILL.md`
- Authenticated [`gh`](https://cli.github.com/) or equivalent GitHub access
- Git with worktree support
- The toolchains required by the repositories being validated

## Development

```bash
python3 -m unittest discover -s tests -v
python3 skills/resolve-prs/scripts/resolve_prs.py validate-bundle
```

## License

MIT
