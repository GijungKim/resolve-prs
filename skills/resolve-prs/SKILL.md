---
name: resolve-prs
description: Resolve open dependency-update PRs on GitHub repos (Dependabot, Renovate, pyup, plus human-authored "chore(deps)" / "build(deps)" / "bump" PRs). Assesses each PR, merges safe ones, fixes and merges fixable ones, and closes broken ones with explanations. Use --all to process every git repo in the current directory in parallel. Use --dry-run to assess without taking action.
argument-hint: "[--all] [--dry-run] [owner/repo]"
disable-model-invocation: false
allowed-tools: Bash, Read, Edit, Write, Glob, Grep, Task, Agent, SendMessage, TaskCreate, TaskUpdate, TaskList
---

# Resolve Dependency-Update PRs

You are resolving open dependency-update PRs on GitHub repositories. The primary case is Dependabot, but the same workflow applies to Renovate, pyup, and human-authored dep bumps that follow conventional-commit naming. Follow this methodology precisely.

## Prerequisites

- `gh` CLI authenticated with access to the target repo(s)
- The project's package manager installed (bun, npm, yarn, or pnpm)
- Write access to push fixes and merge PRs

## Arguments

- No arguments: process the current git repo (uses `origin` remote to determine owner/repo)
- `--all`: find all git repos in the current directory and process each one in parallel using a Claude team (one agent per repo)
- `--dry-run`: assess and report on all PRs without merging, fixing, or closing anything
- `owner/repo`: process a specific GitHub repo

Flags can be combined, e.g. `--all --dry-run`.

Raw arguments: $ARGUMENTS

## Step 1: Determine Target Repos

If `--all` flag is present:
1. Run `find . -maxdepth 2 -name .git -type d` to discover repos
2. Spawn one subagent per repo (Agent/Task tool), **capped at 8 concurrent agents**. If more repos than the cap, queue the rest and process in waves as agents finish. The cap protects against I/O contention from parallel package installs and against accidentally hammering GitHub's API limits across many repos.
3. Each agent runs the PR resolution workflow below independently and reports a one-line status when it finishes (e.g. `repo X: 3 merged, 1 fixed & merged, 1 closed, 0 failed`)
4. Coordinate results from those one-liners and present a unified summary at the end

Otherwise, determine the single target repo:
- If a `owner/repo` argument is given, use that
- Otherwise, parse the current repo from `git remote get-url origin`

## Step 2: List Open PRs

```bash
gh pr list --repo OWNER/REPO --state open --json number,title,author,mergeable,mergeStateStatus,headRefName,labels,statusCheckRollup
```

Filter to dependency-update PRs. A PR qualifies if EITHER:
- Its author login matches a known dep-bump bot: `dependabot[bot]`, `dependabot-preview[bot]`, `renovate[bot]`, `renovate-bot`, `pyup-bot`, `pre-commit-ci[bot]`. Substring matches on `dependabot`, `renovate`, or `pyup` are also fine.
- OR its title starts with a conventional dep-bump prefix: `chore(deps):`, `chore(deps-dev):`, `build(deps):`, `build(deps-dev):`, `bump `, or `Bump ` (case-insensitive). This catches human-authored PRs that were opened before the bot was configured, or one-off manual upgrades.

Renovate "lock file maintenance" PRs (title `chore(deps): lock file maintenance`) also qualify. They have no single dependency name — skip the `.resolve-prs-ignore` matching and the changelog step, treat them as Low Risk, and validate with install + typecheck.

If no PRs match, report that and stop.

PRs that are already `mergeable: CONFLICTING` at this point still get assessed; handle the conflict via the "Merge order matters" rules in Step 7.

### Apply `.resolve-prs-ignore` (if present)

If a `.resolve-prs-ignore` file exists at the repo root, exclude any PR whose updated dependency matches a pattern in the file. Use this for pinned-on-purpose deps (Expo SDK pins, packages on a fork, deps awaiting a coordinated bump, etc.) so the skill stops trying to merge them every run.

**File format:** one pattern per line; blank lines and `#` comments ignored. Patterns are matched against the dependency name from the PR's title/diff. Glob support: `*` matches anything within a name segment.

```
# .resolve-prs-ignore example
expo-*              # pinned by Expo SDK; do not bump
react-native        # pinned by Expo SDK
react-native-mmkv   # we're on a fork
@auth0/auth0-react  # waiting for v3
```

**Matching:** identify each PR's dependency name from the PR title (Dependabot/Renovate convention is `bump <package> from X to Y` / `chore(deps): update <package> to Y`) or, if ambiguous, from the changed `package.json` diff lines. A PR is ignored if the dependency name matches any pattern. Skipped PRs are reported with `Action = Skipped` and `Reason = "matches .resolve-prs-ignore: <pattern>"`.

The ignore file is per-repo. With `--all`, each repo reads its own.

## Step 3: Fetch Changelogs

For each PR, before assessing risk, try to fetch release notes or changelogs:

1. From the PR body itself (Dependabot and Renovate both inline a changelog summary; manual PRs may not)
2. From GitHub releases of the dependency, covering the **from -> to range** — the PR may skip several versions, and `releases/latest` alone misses the intermediate ones where the breaking change usually lives:
   ```bash
   repo_url=$(npm view "PACKAGE@NEW_VERSION" repository.url 2>/dev/null)   # pin the version; bare `npm view` reads `latest`
   if [[ "$repo_url" == *github.com* ]]; then
     dep_repo=$(printf '%s' "$repo_url" | sed -E 's,[?#].*$,,; s,.*github\.com[:/],,; s,^([^/]+/[^/]+).*,\1,; s,\.git$,,')
     gh api "repos/$dep_repo/releases?per_page=100" --jq '.[] | "## " + .tag_name + "\n" + .body' 2>/dev/null
   fi
   ```
   The `sed` reduces any GitHub URL form (`git+https://`, `git://`, `git@github.com:`, monorepo `/tree/...` subpaths) to `owner/repo`; skip this source for non-GitHub packages. Keep the releases whose tags fall in `(from, to]`. If 100 releases don't reach back to `from` (release-heavy repos), paginate or settle for what you have — and say so in the risk assessment rather than implying full coverage. Some projects tag without creating GitHub releases; an empty result here isn't evidence of "no breaking changes".
3. CHANGELOG.md or MIGRATION.md inside the installed package — only readable **in the Step 6 worktree after install** (the main checkout's `node_modules` still has the OLD version). Sources 1–2 are what feed the Step 4 risk assessment; this one is extra context when validating or fixing medium/high-risk PRs. Low-risk PRs merged on green CI never create a worktree — don't create one just for this.

Use this information to anticipate breaking changes before testing. If the changelog explicitly mentions breaking changes or migration steps, factor that into the risk assessment and keep the info handy for Step 7 (fixing).

## Step 4: Assess Each PR

For each PR, get the diff:
```bash
gh pr diff NUMBER --repo OWNER/REPO
```

Categorize each PR by risk level:

### Low Risk (merge if signal is green)
- Patch version bumps (e.g., 1.2.3 -> 1.2.5)
- Minor version bumps of dev dependencies (e.g., prettier 3.1 -> 3.2)
- Dependencies with no peer dependency constraints

Even low-risk PRs require a green signal before merge. Never merge purely on the version-number heuristic. Semver is aspirational (libraries break in patch versions by accident), and supply-chain attacks ride patch/minor bumps (e.g. `eslint-config-prettier 8.10.1`, `xz-utils`). Acceptable signals, in order of preference:
1. `statusCheckRollup` from Step 2 is `SUCCESS` (CI passed on the PR)
2. Local smoke test passes: at minimum a typecheck (`tsc --noEmit` or the `compile` script) plus lint
If neither signal is available (no CI configured, no compile/lint script), treat the PR as Medium Risk and follow Step 6.

As a cheap extra supply-chain guard, check how fresh the new version is:
```bash
npm view PACKAGE time --json | jq -r '."NEW_VERSION"'
```
If a patch/minor release is less than ~48 hours old, prefer deferring it to the next run (mark it **Deferred** in the report). Compromised releases are usually caught and yanked within days; waiting costs nothing.

### Medium Risk (test first, then merge)
- Minor version bumps of runtime dependencies
- Major bumps of dev-only tools (linters, formatters) - test lint/compile
- Any bump of dependencies used in the project's core functionality

### High Risk (likely breaking)
- Major version bumps of core dependencies (react, react-native, etc.)
- Version bumps that create mismatches with pinned peer dependencies (e.g., react-dom bumped but react stays pinned)
- Bumps incompatible with framework SDK constraints (e.g., Expo SDK pins)

Always cross-reference with the "Common Breaking Change Patterns" section below and any changelog info from Step 3.

## Step 5: Detect Package Manager

Before testing, detect the project's package manager from the **repo root**:
- `bun.lock` or `bun.lockb` -> bun
- `yarn.lock` -> yarn
- `pnpm-lock.yaml` -> pnpm
- `package-lock.json` -> npm

Use the detected package manager for all install/run commands throughout. The package manager is determined once per repo, even in monorepos; the lockfile lives at the root.

**Bundler/Ruby PRs need a different validator.** A PR that changes `Gemfile`/`Gemfile.lock` (common in Expo/React Native repos with fastlane, or in Ruby projects) is NOT exercised by the JS package manager. Detect it from the PR's changed files (a `Gemfile` or `Gemfile.lock` path) and validate in the worktree with `bundle install` instead — which also refreshes the machine's installed gems (see the Ruby/Bundler pattern below for why that matters). A repo can carry both a JS lockfile and a Gemfile; handle each changed manager.
## Step 5.5: Locate the Changed package.json (monorepos)

Many repos contain more than one `package.json`: workspaces (npm/yarn/pnpm/bun workspaces, Turborepo, Nx) and "two unrelated apps in one repo" cases (e.g. `app/` + `worker/`). The wrong directory means the wrong typecheck context, so the validation in Step 6 runs from the directory the PR actually touches.

1. From the PR diff, find which `package.json` files were modified:
   ```bash
   gh pr diff NUMBER --repo OWNER/REPO --name-only | grep '/package.json$\|^package.json$'
   ```
2. Determine each affected directory (the relative path of each modified `package.json`, dropping the filename). Call this list `pkg_dirs`. For most PRs `pkg_dirs` has exactly one entry; coordinated workspace bumps may have several.
3. Detect whether the repo is a workspace monorepo:
   - Root `package.json` contains a `"workspaces"` field, OR
   - Root contains `pnpm-workspace.yaml`, `turbo.json`, or `nx.json`
4. Set `install_dir` and `validate_dirs`:
   - **Workspace monorepo:** `install_dir = "."` (root); `validate_dirs = pkg_dirs`. The workspace tool resolves all packages from the root install.
   - **Non-workspace multi-package (e.g. `app/` + `worker/` with separate lockfiles or no root lockfile):** `install_dir = pkg_dir`; `validate_dirs = [pkg_dir]`. Run install AND validation inside each affected directory.
   - **Single package.json:** both equal `"."`.

Step 6 uses `install_dir` for the install and iterates `validate_dirs` for typecheck/lint/test.

## Step 6: Validate Locally

Use a git worktree to isolate the test environment. This guarantees the install resolves against the PR's actual lockfile (not the default branch's), never leaks state into the user's working tree if validation crashes, and avoids package-manager-specific `--no-save` flags.

For low-risk PRs without a green CI signal, run a smoke test (typecheck + lint). For medium/high risk PRs, run the full validation suite (typecheck + lint + tests).

1. Resolve the default branch name once at the start of the run (don't assume `main`):
   ```bash
   default_branch=$(gh repo view --json defaultBranchRef -q .defaultBranchRef.name)
   ```
   Use `$default_branch` anywhere the workflow refers to "main".

2. Fetch remote branches: `git fetch origin`

3. Create a temporary worktree on the PR branch and run validation inside it. Wrap setup, validation, and teardown in a single bash invocation so the `trap` cleans up reliably on failure. Use `install_dir` and `validate_dirs` from Step 5.5:
   ```bash
   worktree_dir=$(mktemp -d -t resolve-prs-XXXXXX)
   trap 'git worktree remove "$worktree_dir" --force 2>/dev/null' EXIT
   git worktree add "$worktree_dir" "origin/BRANCH"

   # Install once, in the right place
   cd "$worktree_dir/$install_dir"
   <pkg-manager> install            # uses PR's lockfile - real resolution

   # Validate in each affected package
   for d in "${validate_dirs[@]}"; do
     cd "$worktree_dir/$d"
     <pkg-manager> run typecheck    # see script detection note below
     <pkg-manager> run lint
     <pkg-manager> test             # medium/high risk only
   done
   ```

   Don't guess script names — read the affected `package.json`'s `scripts` first and run what actually exists:
   - **Typecheck:** the script under whatever name it has (`typecheck`, `compile`, `check-types`, ...). Fallback if none but a `tsconfig.json` exists: run the project-local compiler through the package manager (`npx tsc --noEmit`, `yarn tsc --noEmit`, `pnpm exec tsc --noEmit`, `bunx tsc --noEmit`) — a bare `tsc` may not resolve `node_modules/.bin` and can fail or pick up a globally installed, wrong-version TypeScript.
   - **Lint:** prefer a check-style script (`lint:check`) over one that writes fixes. If the only lint script auto-fixes, run it and then check `git status --porcelain` in the worktree — a dirty tree means the PR's code doesn't pass lint as-is; treat that as a lint failure (or fold the fixes into the FIX-then-MERGE flow), never as a pass.
   - **Test:** the test script, skipping it if it's the npm placeholder (`"echo \"Error: no test specified\""`).

   The worktree carries the PR's `package.json` AND lockfile, so the install matches what would actually land on `$default_branch`. No flags needed; the lockfile pins versions exactly.

4. The user's main checkout is never modified. There is no per-PR restore step. Each PR gets its own fresh worktree; the `trap` removes it whether validation passes, fails, or crashes.

## Step 6.5: Expo Projects — SDK Compatibility

If any affected `package.json` lists `expo` in its dependencies, add one check inside the Step 6 worktree, after install:

```bash
CI=1 npx expo install --check
```

The `CI=1` matters: run interactively, the command prompts to install the "expected" versions, which would hang the run — and accepting would rewrite the worktree. In CI mode it's check-only and exits non-zero listing every package whose version doesn't match what the installed SDK expects.

- **If the PR's bumped package is flagged**, the bump fights the SDK pin. Don't merge it, and don't "fix" it by re-pinning in the PR — that just re-creates the bot's diff in reverse. Close it with the reason `pinned by Expo SDK <version>; npx expo install --check flags <package>@<new-version>` plus the bot-ignore snippet from Step 7, and suggest adding the package to `.resolve-prs-ignore` so future runs skip it outright.
- **If only unrelated packages are flagged**, that's pre-existing drift in the repo — note it once in the final report, but don't hold it against the PR.

Never auto-merge a bump of the `expo` package itself across SDK versions (e.g. 52 -> 53). That's an SDK upgrade, not a dep bump: it needs `npx expo install --fix`, `npx expo-doctor`, and usually config-plugin/native changes. Close it pointing at the Expo upgrade guide (https://docs.expo.dev/workflow/upgrading-expo-sdk-walkthrough/), or leave it open with a comment if the user does SDK upgrades manually.

## Step 7: Take Action

**If `--dry-run` is set, skip this step entirely. Just report the assessment from Step 9.**

### Merge order matters

Every merged PR rewrites the lockfile on `$default_branch`, which turns the remaining PRs stale or conflicted. Process PRs **one at a time, lowest risk first**, and re-check each PR's state right before acting on it:

```bash
gh pr view NUMBER --repo OWNER/REPO --json mergeable,mergeStateStatus,headRefOid
```

- **If `headRefOid` differs from the head you validated** (the bot force-pushed since Step 2), the green signal belongs to a commit that no longer exists. Re-check the diff: if the target version changed, redo the Step 3–4 assessment (including the publish-age check) — a rebase can pull in a materially different release; if only the base moved, re-run Step 6 validation on the new head.
- `mergeStateStatus: BEHIND` — fine when the merges that moved `$default_branch` are unrelated to this PR (different packages, no shared peer constraints). If an earlier merge this run bumped the same package or a peer of it, treat it like a conflict below (rebase + re-validate) — two individually-green dep PRs can still break in combination.
- `mergeable: CONFLICTING` (typically a lockfile conflict with a PR you just merged): trigger a rebase from the bot. For Dependabot: `gh pr comment NUMBER --repo OWNER/REPO --body "@dependabot rebase"`. For Renovate: flip the rebase checkbox in the PR body from `- [ ]` to `- [x]` (`gh pr view NUMBER --repo OWNER/REPO --json body`, edit, `gh pr edit NUMBER --repo OWNER/REPO --body "..."`). Poll for the bot's force-push for ~2 minutes; if it lands, apply the `headRefOid` rule above (re-assess if the version changed, re-validate regardless), then merge. If it doesn't land in time, mark the PR **Deferred** and move on — the bot rebases on its own schedule and the next run picks it up.

The same handling applies to PRs that were already conflicted at the start of the run. Under `--dry-run`, don't trigger rebases; report conflicted PRs as **Would defer (needs rebase)**.

### For safe/passing PRs: MERGE

A PR is mergeable only if it has a green signal: `statusCheckRollup` is `SUCCESS`, or local validation from Step 6 passed. Never merge purely on the Step 4 risk classification.

Detect the repo's allowed merge methods once per run — a hardcoded method fails outright on repos that only allow squash:

```bash
gh repo view OWNER/REPO --json squashMergeAllowed,mergeCommitAllowed,rebaseMergeAllowed
```

Dep bumps are single-commit, so prefer `--squash` if allowed, then `--merge`, then `--rebase`. Pin the merge to the exact commit you validated so a force-push in the window between recheck and merge fails loudly instead of landing an untested head:

```bash
gh pr merge NUMBER --repo OWNER/REPO --squash --match-head-commit HEAD_SHA   # headRefOid from the recheck
```

If branch protection requires checks that are still running (merge is blocked but nothing failed), enable auto-merge with the full form — `gh pr merge NUMBER --repo OWNER/REPO --squash --auto --match-head-commit HEAD_SHA` — and report the PR as **Auto-merge enabled**, not merged: it only lands later, when checks pass. If the repository has auto-merge disabled the command errors; mark the PR **Deferred** instead.

### For PRs that need fixes: FIX then MERGE

Use a worktree for the same reasons as Step 6: the user's main checkout stays clean.

1. Create a worktree with the PR branch checked out (not detached, so you can push):
   ```bash
   worktree_dir=$(mktemp -d -t resolve-prs-fix-XXXXXX)
   git worktree add -B BRANCH "$worktree_dir" "origin/BRANCH"
   cd "$worktree_dir/$install_dir"   # install_dir from Step 5.5 (root for workspaces, subpackage for multi-package repos)
   ```
2. Install dependencies: `<pkg-manager> install`
3. Make the necessary code changes (API migrations, config updates, mock updates, etc.)
4. Use changelog/migration guide info from Step 3 to inform the fix
5. Commit with a clear message explaining the fix
6. Push to the PR branch from the worktree: `git push origin BRANCH`
7. Remove the worktree: `cd - && git worktree remove "$worktree_dir" --force`
8. Merge the PR using the merge method detected above: `gh pr merge NUMBER --repo OWNER/REPO --squash` (or `--merge`/`--rebase`)

### For broken/incompatible PRs: CLOSE with explanation
```bash
gh pr close NUMBER --repo OWNER/REPO --comment "Reason for closing..."
```

Always include a clear, specific reason:
- "Incompatible with X which requires Y"
- "Major version change requires migration of Z, not automated"
- "Version mismatch: A stays at X while B would be Y"

**Stop the bot from reopening it next week.** `gh pr close` does not stop Dependabot or Renovate from re-creating the same PR on the next scheduled run. When closing, append a config snippet to the close comment so the user can paste it into their bot config:

For Dependabot (append to `.github/dependabot.yml` under the matching `updates:` entry):
```yaml
ignore:
  - dependency-name: "PACKAGE"
    versions: ["X.Y.Z"]   # or use update-types: ["version-update:semver-major"]
```

For Renovate (append to `renovate.json`):
```json
"packageRules": [
  { "matchPackageNames": ["PACKAGE"], "matchUpdateTypes": ["major"], "enabled": false }
]
```

If the package is one the user already pins on purpose, also suggest they add it to `.resolve-prs-ignore` so this skill skips it on every future run too.

## Step 8: Auto-Learn (after fixing novel breaking changes)

When you successfully fix a breaking change that is NOT already listed in the "Common Breaking Change Patterns" section below, add it. Auto-learned patterns can over-fit to one project or grow stale across versions, so the format below tracks provenance and freshness. Verify a pattern before trusting it blindly.

### Rules for auto-learn

1. **Only record generalizable patterns.** The entry should help with ANY project that hits this upgrade, not just the current one. Example of good: "react-native-mmkv 3 -> 4: `new MMKV()` -> `createMMKV()`". Example of bad: "fixed import in src/utils/storage.ts".

2. **One line per pattern, with dates.** Format: `**package X -> Y** (learned YYYY-MM, last verified YYYY-MM): brief description of what changed and how to fix it.` Both dates equal the current month when first written. Hand-curated patterns (the ones already in this file without dates) are trusted as-is and don't need backfilling.

3. **Refresh the verified date on reuse.** When an existing pattern correctly applies to a new PR (its suggested fix worked), update its `last verified` date to the current month before merging. This keeps useful patterns fresh and lets unused ones age out visually.

4. **Treat patterns older than 6 months as hints, not rules.** Before applying any auto-learned pattern whose `last verified` date is >6 months old, re-verify by testing. Don't trust it blindly. If it still applies, refresh the date per rule 3. If it doesn't, fix or remove the pattern.

5. **Cap at 30 entries per subsection.** If a subsection hits 30, evict the entry with the oldest `last verified` date (least-recently-useful), not the oldest `learned` date. Patterns that keep proving useful stay; patterns nobody hits get culled.

6. **Don't duplicate.** If a similar pattern already exists, update it (refresh the dates and merge the description) rather than adding a new one.

7. **Where to write.** Edit the SKILL.md file directly in the skill's directory. The file location depends on where the skill is installed:
   - Personal: `~/.claude/skills/resolve-prs/SKILL.md`
   - Project: `.claude/skills/resolve-prs/SKILL.md`
   - Plugin: find via `ls ~/.claude/plugins/*/skills/resolve-prs/SKILL.md`

   Use the Edit tool to append to the appropriate subsection under "Common Breaking Change Patterns".

## Step 9: Cleanup

The worktree-based approach in Steps 6 and 7 means the user's working tree was never touched, so there's no stash to restore and no branch to switch back from.

1. Verify no leftover worktrees from crashed invocations:
   ```bash
   git worktree list | grep -E 'resolve-prs-(fix-)?[A-Za-z0-9]+' && \
     git worktree list | awk '/resolve-prs-/ {print $1}' | xargs -I {} git worktree remove --force {}
   ```
2. Pull merged changes on the user's current default branch checkout: `git pull origin "$default_branch"` (only if the user is on `$default_branch`; otherwise skip, and don't switch branches on their behalf).

## Step 10: Report

Present a summary table:

| PR | Title | Update | Risk | Action | Reason |
|---|---|---|---|---|---|
| #N | ... | X -> Y | Low/Medium/High | Merged / Auto-merge enabled / Fixed & Merged / Closed / Skipped / Deferred / Would merge (dry-run) / Would close (dry-run) | ... |

If `--dry-run`, use "Would merge", "Would close", "Would fix & merge" in the Action column. PRs filtered out by `.resolve-prs-ignore` use `Skipped`, and releases <48h old use `Deferred` — the same in dry-run, since no action would have been taken either way. Conflicted PRs in dry-run use `Would defer (needs rebase)`: for real runs a rebase is triggered, but dry-run doesn't trigger one.

If any new patterns were learned, mention them at the bottom:
> Learned N new breaking change pattern(s) - the skill will handle these automatically next time.

## Common Breaking Change Patterns

Reference these when assessing PRs. This is not exhaustive - always verify by testing. New patterns are added automatically via auto-learn (Step 8).

### JavaScript / TypeScript Ecosystem
- **ESLint 8 -> 9+**: Requires flat config migration (`.eslintrc.*` -> `eslint.config.js`). Check if `eslint-config-*` packages support flat config before merging.
- **ESLint 9 -> 10**: Removes `FlatESLint`/`LegacyESLint` exports, breaking `typescript-eslint` <8.56.0 and `@eslint/js` <10. Bump `@eslint/js` to ^10.0.0 and `typescript-eslint` to ^8.56.0+. Note: `eslint-plugin-react-hooks` may lack ESLint 10 peer dep but works anyway.
- **eslint-plugin-react-refresh 0.4 -> 0.5**: Ships ESM-only, changes default export to named `{ reactRefresh }`, and `configs.vite` becomes `configs.vite()` (function call). Also `customHOCs` renamed to `extraHOCs`.
- **Jest major bumps**: Often incompatible with framework-specific jest presets (`jest-expo`, `react-scripts`). Check the preset's peer dependencies.
- **TypeScript major bumps**: Check if all `@types/*` packages and build tools ship compatible definitions.
- **TypeScript 5.x -> 6.x**: `baseUrl` and `moduleResolution: "node"` are deprecated (error by default). Fix: remove `baseUrl` (default is `.`), change `moduleResolution` to `"bundler"`. Also, TS 6 no longer auto-includes all `@types/*` from `typeRoots`. Add an explicit `"types"` array listing needed type packages (e.g., `["react", "jest", "node"]`).
- **Prettier major bumps**: Usually safe but may reformat code - check if CI enforces formatting.
- **kysely 0.28 -> 0.29** (learned 2026-06, last verified 2026-07): kysely 0.29 removed the `DEFAULT_MIGRATION_TABLE` and `DEFAULT_MIGRATION_LOCK_TABLE` named exports. Older `@better-auth/kysely-adapter` (pulled in transitively by `better-auth`) still imported them, so an esbuild/bundler build failed with `No matching export in ".../kysely/dist/index.js" for import "DEFAULT_MIGRATION_TABLE"` — **even though `tsc --noEmit` passes**. **RESOLVED as of better-auth >= 1.6.20**: its kysely-adapter now declares `kysely: "^0.28.17 || ^0.29.0"` and no longer imports the removed symbols. Verified 2026-06-30 by bumping better-auth 1.6.9 -> 1.6.23 (kysely re-resolved to 0.29.2); `wrangler deploy --dry-run` bundled clean. So DON'T close a better-auth bump over kysely fears anymore — instead run the bundle build to confirm. If you're on an older better-auth that still breaks, keep kysely pinned to `^0.28` via a `package.json` `overrides`/`resolutions` entry until you can bump better-auth to >= 1.6.20. Lesson still generalizes: for Cloudflare Workers / esbuild-bundled projects whose CI is install+typecheck only, always run a `--dry-run` bundle build (not just `tsc`) before merging any runtime-dep bump — typecheck-only CI won't catch a bad bundle import. **Scope note (verified 2026-07 on an Expo RN app):** the kysely concern is N/A when there is NO esbuild/Worker bundle and NO `kysely` in the tree — e.g. a pure Expo/Metro mobile app that only imports `@better-auth/expo` (client). Metro bundles the RN app, better-auth's server-side kysely code is never bundled, and `grep -rl kysely --include=package.json` + no `wrangler.*` confirms it. There, a green full-gate CI (tsc + eslint + unit) is sufficient to merge a better-auth bump; don't over-investigate. The `--dry-run` bundle rule applies ONLY to esbuild/Worker-bundled projects. **Pin-removal gotcha (bun, verified 2026-07):** when testing whether an `overrides` pin can be dropped, deleting the override and running `bun install` does NOT re-resolve the package — the lockfile silently keeps the pinned version, so the test proves nothing. And `bun update <pkg>` is worse: it adds the package as a direct dependency and leaves a nested old copy under the original consumer (`node_modules/<consumer>/node_modules/<pkg>`), so the bundle still uses the old version while the test looks green. To genuinely test, regenerate the lockfile fresh (rm lockfile + install) or hand-edit the lock entry, then confirm a single hoisted copy at the new version before running the bundle build.

- **better-auth / @better-auth/expo 1.6.24 -> 1.6.27** (learned 2026-08, last verified 2026-08): a declaration-emit regression makes bundled client plugins (`expoClient()`, `oneTapClient()`, `jwtClient()`) stop satisfying `BetterAuthClientPlugin` — `tsc` reports TS2322 on `getActions` at the `createAuthClient({ plugins: [...] })` call, and inference collapses so plugin-added members vanish (TS2339 e.g. `authClient.emailOtp`, `.oneTap`, `.token`). Not a duplicate `@better-fetch/fetch` problem (single copy in the lockfile); it's upstream better-auth/better-auth#10767 / #10583 / #10515, fixed in **1.6.28** (#10794). Any bump landing in 1.6.24–1.6.27 fails the client app's typecheck while a server-only worker stays green. Fix = bump to >= 1.6.28 — but honor the repo's Renovate `minimumReleaseAge` before hand-pushing a newer version onto the PR; if the fixed release is still inside that window, defer and let Renovate roll the PR forward.

### React / React Native
- **react-dom without react**: Must always match the `react` version exactly.
- **react-native-mmkv 3 -> 4**: Constructor changed from `new MMKV()` to `createMMKV()`, `.delete()` renamed to `.remove()`, requires Nitro Modules jest mock.
- **Expo SDK pins**: Many dependencies are pinned by Expo SDK version. Verify with `npx expo install --check` (read-only; `--fix` mutates). Don't bump packages that Expo constrains. See Step 6.5. (last verified 2026-08) The pin set includes `typescript` and `react` (SDK 57 expects `typescript@~6.0.3`, `react@19.2.3` exactly) — close such bumps even on green CI. But the pinned `react-native` PATCH can advance across expo point releases (expo 57.0.1 pins RN 0.86.0; 57.0.9 pins RN 0.86.2), so check the latest satisfiable expo version's `bundledNativeModules.json` before assuming an RN patch bump fights the pin.
- **expo (SDK) major bumps (e.g. 52 -> 53)**: Never auto-merge — this is a full SDK upgrade (`npx expo install --fix`, `expo-doctor`, config-plugin and native changes), not a dep bump. Close or defer to a manual upgrade.
- **jest-expo / babel-preset-expo / expo-* packages**: Versioned in lockstep with the Expo SDK; a solo bump of one of them almost always fails `expo install --check`.
- **React Navigation major bumps**: Often requires simultaneous updates of all `@react-navigation/*` packages.
- **@tiptap/* 2 -> 3** (learned 2026-05, last verified 2026-05): Most extensions still ship default exports, but `@tiptap/extension-table` and `@tiptap/extension-text-style` dropped theirs. Switch to named: `import { Table } from "@tiptap/extension-table"` and `import { TextStyle } from "@tiptap/extension-text-style"`. The `extension-text-style` package now also re-exports Color/FontFamily/FontSize/BackgroundColor/LineHeight, so separate `@tiptap/extension-color` etc. still resolve but are redundant. Editor command API (toggleBold, setTextAlign, insertTable, toggleHighlight, etc.) is unchanged.
- **@vitejs/plugin-react 4 -> 6**: Requires `vite@^8.0.0` (peer). v6 also drops the Babel dependency since Vite 8 handles React Refresh via Oxc/Rolldown. Must bump together with Vite; close standalone vite-major PRs that leave plugin-react at v4 (peer mismatch).
- **Renovate "expo monorepo" major bump (Expo SDK N -> N+1)** (learned 2026-07, last verified 2026-07): Renovate's expo-monorepo group bumps `expo`/`expo-*` packages but NOT `react-native`/`react`, which each SDK pins to specific versions (e.g. SDK 57 -> RN 0.86.0, react 19.2.3). CI can stay green while versions are misaligned. Before merging, run `CI=1 npx expo-doctor` in the app dir and fix with `npx expo install --fix`, then push the alignment commit to the PR branch. In expo-module repos, `expo-doctor`'s "duplicate packages" failure is a benign artifact of the `file:..` example link — ignore it; only the version-check matters.
- **Expo SDK major bump in an expo-module repo (jest-expo preset)** (learned 2026-07, last verified 2026-07): `expo-module-scripts` releases lag one SDK behind and pin `jest-expo@~<oldSDK>`, whose peer deps want the old `react-native`. After aligning RN to the new SDK's version, npm (used by CI) nests/skips the transitive jest-expo and tests die with `Preset jest-expo/node not found` — while bun still hoists it, so local bun validation passes. Fix: add `jest-expo@~<newSDK>` as an explicit root devDependency. Always replicate CI's actual package manager (`npm install` + test) before merging, not just the repo's lockfile manager.

### Python
- **Django major bumps**: Check middleware, URL config, and deprecated feature removals.
- **SQLAlchemy 1.x -> 2.x**: Query API completely changed.

### General
- **Peer dependency mismatches**: If package A requires `B@^2.0` but the PR bumps B to 3.0, close it.
- **Monorepo grouped updates**: If one package in the group is breaking, the whole PR fails. Consider asking Dependabot to split it.

### Ruby / Bundler
- **Any bundler PR (Gemfile.lock bump)** (learned 2026-08, last verified 2026-08): merging a Gemfile.lock change updates the lock but does NOT install the new gem on the machine, so downstream `bundle exec` tooling (fastlane, cocoapods, danger, rake) breaks with `Bundler::GemNotFound: Could not find <gem>-<version> in locally installed gems` until someone runs `bundle install`. Install-and-typecheck CI never runs `bundle exec`, so it stays green while a release/ship script is silently broken. Two implications: (1) validate a bundler PR with `bundle install` in the worktree (not the JS package manager) plus, if present, a `bundle exec fastlane lanes` / `bundle exec rake -T` smoke test — that install also refreshes the local gem set, pre-empting the breakage; (2) if the repo's release scripts call `bundle exec` with no preceding `bundle install`/`bundle check`, flag it — the fix is a `bundle check || bundle install` guard before the `bundle exec` line. Seen 2026-08: a merged `json 2.19.7 -> 2.19.9` bump broke a fastlane `ship:ios` exactly this way.
