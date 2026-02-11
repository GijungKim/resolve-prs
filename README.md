# resolve-prs

A [Claude Code](https://docs.anthropic.com/en/docs/claude-code) skill that automatically resolves Dependabot PRs on your GitHub repos.

Instead of manually reviewing each Dependabot PR, this skill:

1. **Assesses risk** — categorizes each PR as low/medium/high risk based on semver, peer deps, and framework constraints
2. **Tests locally** — checks out risky PRs and runs your project's compile, lint, and test commands
3. **Merges safe ones** — auto-merges patch bumps and verified-safe updates
4. **Fixes breaking ones** — migrates code for known breaking changes (API renames, config format changes, mock updates) and merges
5. **Closes incompatible ones** — with a clear comment explaining why

## Install

```bash
/plugin marketplace add GijungKim/resolve-prs
/plugin install resolve-prs@resolve-prs
```

Or manually copy `skills/resolve-prs/SKILL.md` to `~/.claude/skills/resolve-prs/SKILL.md`.

## Usage

```bash
# Resolve PRs on the current repo
/resolve-prs

# Target a specific repo
/resolve-prs owner/repo

# Process all git repos in the current directory (parallel, one agent per repo)
/resolve-prs --all
```

## Prerequisites

- [Claude Code](https://docs.anthropic.com/en/docs/claude-code) installed
- [`gh` CLI](https://cli.github.com/) authenticated with repo access
- Your project's package manager (bun, npm, yarn, or pnpm)

## What it knows about

The skill includes a knowledge base of common breaking changes it can detect and handle:

| Pattern | What it does |
|---------|-------------|
| ESLint 8 -> 9+ | Detects flat config requirement, closes if not migrated |
| Jest major bumps | Checks framework preset compatibility (jest-expo, etc.) |
| react / react-dom mismatch | Closes if versions would diverge |
| react-native-mmkv 3 -> 4 | Migrates `new MMKV()` to `createMMKV()`, fixes `.delete()` -> `.remove()`, adds jest mock |
| Expo SDK pins | Respects Expo version constraints |
| Peer dependency conflicts | Detects and closes incompatible bumps |

The patterns section in the skill is designed to grow — add your own as you encounter new breaking changes.

## How `--all` works

When you pass `--all`, the skill:

1. Finds all git repos within the current directory (up to 2 levels deep)
2. Spins up a Claude team with one agent per repo
3. Each agent independently runs the full PR resolution workflow
4. Results are collected into a unified summary table

This is useful when you have a directory of projects (e.g., `~/projects/`) and want to batch-resolve Dependabot PRs across all of them.

## License

MIT
