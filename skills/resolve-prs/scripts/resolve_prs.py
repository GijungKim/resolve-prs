#!/usr/bin/env python3
"""Deterministic, read-only mechanics for the resolve-prs agent skill."""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


SKILL_ROOT = Path(__file__).resolve().parents[1]
PATTERNS_PATH = SKILL_ROOT / "knowledge" / "patterns.json"
DEFAULT_POLICY_PATH = SKILL_ROOT / "references" / "default-policy.json"
CHECK_STATUSES = {"pass", "fail", "unavailable", "skipped"}
RISK_POLICY_KEYS = {"low": "lowRisk", "medium": "mediumRisk", "high": "highRisk"}
BOT_MARKERS = ("dependabot", "renovate", "pyup")
BOT_LOGINS = {"pre-commit-ci[bot]", "pre-commit-ci"}
DEPENDENCY_TITLE = re.compile(
    r"^(?:chore|build)\(deps(?:-dev)?\):|^bump\s+", re.IGNORECASE
)
LOCKFILES = {
    "bun.lock": "bun",
    "bun.lockb": "bun",
    "pnpm-lock.yaml": "pnpm",
    "yarn.lock": "yarn",
    "package-lock.json": "npm",
    "Gemfile.lock": "bundler",
}
IGNORED_DIRS = {
    ".git",
    ".hg",
    ".svn",
    "node_modules",
    "vendor",
    ".venv",
    "venv",
    "dist",
    "build",
}


class HarnessError(ValueError):
    """A user-facing validation failure."""


def dump(value: Any) -> None:
    json.dump(value, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text())
    except FileNotFoundError as exc:
        raise HarnessError(f"missing file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise HarnessError(f"invalid JSON in {path}: {exc}") from exc


def author_login(pr: dict[str, Any]) -> str:
    author = pr.get("author")
    if isinstance(author, dict):
        return str(author.get("login", ""))
    return str(author or "")


def qualification_reason(pr: dict[str, Any]) -> str | None:
    login = author_login(pr).lower()
    title = str(pr.get("title", "")).strip()
    if login in BOT_LOGINS or any(marker in login for marker in BOT_MARKERS):
        return "dependency-bot author"
    if DEPENDENCY_TITLE.search(title):
        return "conventional dependency title"
    return None


def filter_prs(prs: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    qualified = []
    for pr in prs:
        reason = qualification_reason(pr)
        if reason is None:
            continue
        item = dict(pr)
        item["qualification"] = reason
        item["lockFileMaintenance"] = (
            str(pr.get("title", "")).strip().lower()
            == "chore(deps): lock file maintenance"
        )
        qualified.append(item)
    return qualified


def parse_ignore_lines(text: str) -> list[str]:
    patterns = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        line = re.split(r"\s+#", line, maxsplit=1)[0].strip()
        if line:
            patterns.append(line)
    return patterns


def matches_ignore(package: str, pattern: str) -> bool:
    # Keep '*' inside a package-name segment; '@scope/*' is useful while a bare
    # '*' should not accidentally cross the slash in a scoped package.
    expression = "".join("[^/]*" if char == "*" else re.escape(char) for char in pattern)
    return re.fullmatch(expression, package) is not None


def find_repositories(root: Path, max_depth: int) -> list[str]:
    root = root.resolve()
    found: list[str] = []
    for current, dirs, files in os.walk(root):
        current_path = Path(current)
        depth = len(current_path.relative_to(root).parts)
        if ".git" in dirs:
            found.append(str(current_path))
            dirs.remove(".git")
        elif ".git" in files:
            found.append(str(current_path))
        if depth >= max_depth:
            dirs[:] = []
        else:
            dirs[:] = [name for name in dirs if name not in IGNORED_DIRS]
    return sorted(found)


def iter_project_files(root: Path) -> Iterable[Path]:
    for current, dirs, files in os.walk(root):
        dirs[:] = [name for name in dirs if name not in IGNORED_DIRS]
        current_path = Path(current)
        for name in files:
            if name == "package.json" or name in LOCKFILES or name in {
                "pnpm-workspace.yaml",
                "turbo.json",
                "nx.json",
                "Gemfile",
            }:
                yield current_path / name


def package_manifest(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {"path": str(path), "scripts": {}, "invalid": True}
    return {
        "path": str(path),
        "scripts": value.get("scripts", {}) if isinstance(value, dict) else {},
        "workspaces": value.get("workspaces") if isinstance(value, dict) else None,
    }


def nearest_manifest(directory: Path, root: Path, manifests: set[Path]) -> Path | None:
    current = directory.resolve()
    root = root.resolve()
    while current == root or root in current.parents:
        candidate = current / "package.json"
        if candidate in manifests:
            return candidate
        if current == root:
            break
        current = current.parent
    return None


def inspect_repository(root: Path, changed: list[str]) -> dict[str, Any]:
    root = root.resolve()
    files = list(iter_project_files(root))
    manifests = {path.resolve() for path in files if path.name == "package.json"}
    lockfiles = [path for path in files if path.name in LOCKFILES]
    root_manifest = root / "package.json"
    root_data = package_manifest(root_manifest) if root_manifest.exists() else None
    workspace = bool(
        (root_data and root_data.get("workspaces"))
        or any((root / marker).exists() for marker in ("pnpm-workspace.yaml", "turbo.json", "nx.json"))
    )

    affected: set[Path] = set()
    for raw_path in changed:
        path = (root / raw_path).resolve() if not Path(raw_path).is_absolute() else Path(raw_path).resolve()
        if root not in path.parents and path != root:
            raise HarnessError(f"changed path escapes repository root: {raw_path}")
        if path.name == "package.json" and path in manifests:
            affected.add(path)
        else:
            nearest = nearest_manifest(path.parent, root, manifests)
            if nearest:
                affected.add(nearest)
    if not affected and root_manifest.resolve() in manifests:
        affected.add(root_manifest.resolve())

    manifest_details = [package_manifest(path) for path in sorted(affected)]
    managers = [
        {
            "manager": LOCKFILES[path.name],
            "lockfile": str(path.relative_to(root)),
            "directory": str(path.parent.relative_to(root)) or ".",
        }
        for path in sorted(lockfiles)
    ]
    return {
        "root": str(root),
        "workspace": workspace,
        "packageManagers": managers,
        "affectedManifests": [
            {
                **detail,
                "path": str(Path(detail["path"]).relative_to(root)),
            }
            for detail in manifest_details
        ],
        "installDirectory": "." if workspace else None,
        "ambiguities": [
            "multiple package managers detected; select by affected lockfile"
        ]
        if len({item["manager"] for item in managers}) > 1
        else [],
    }


def parse_checks(values: list[str]) -> dict[str, str]:
    checks: dict[str, str] = {}
    for value in values:
        if "=" not in value:
            raise HarnessError(f"check must use NAME=STATUS: {value}")
        name, status = value.split("=", 1)
        name, status = name.strip(), status.strip().lower()
        if not name or status not in CHECK_STATUSES:
            raise HarnessError(f"invalid check: {value}")
        checks[name] = status
    return checks


def make_evidence(args: argparse.Namespace) -> dict[str, Any]:
    checks = parse_checks(args.check)
    override = read_json(Path(args.policy)) if args.policy else {}
    policy = merged_policy(override)
    policy_required = policy["validation"][RISK_POLICY_KEYS[args.risk]]
    required = list(dict.fromkeys([*policy_required, *args.required]))
    return {
        "schemaVersion": 1,
        "repository": args.repo,
        "pullRequest": args.pr,
        "headSha": args.head_sha,
        "baseSha": args.base_sha,
        "risk": args.risk,
        "requiredChecks": required,
        "checks": checks,
        "createdAt": datetime.now(timezone.utc).isoformat(),
    }


def gate_evidence(
    evidence: dict[str, Any],
    repo: str,
    pr: int,
    current_head: str,
    current_base: str,
    policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    failures = []
    if evidence.get("schemaVersion") != 1:
        failures.append("unsupported or missing evidence schema version")
    if evidence.get("repository") != repo:
        failures.append("repository does not match evidence")
    if evidence.get("pullRequest") != pr:
        failures.append("pull request does not match evidence")
    if evidence.get("headSha") != current_head:
        failures.append("current head does not match validated head")
    if evidence.get("baseSha") != current_base:
        failures.append("current base does not match validated base")
    checks = evidence.get("checks", {})
    if not isinstance(checks, dict):
        failures.append("evidence checks are invalid")
        checks = {}
    invalid_statuses = sorted(
        name for name, status in checks.items() if status not in CHECK_STATUSES
    )
    if invalid_statuses:
        failures.append(f"checks have invalid status: {', '.join(invalid_statuses)}")
    failed = sorted(name for name, status in checks.items() if status == "fail")
    if failed:
        failures.append(f"failed checks: {', '.join(failed)}")
    required = evidence.get("requiredChecks", [])
    if (
        not isinstance(required, list)
        or not required
        or any(not isinstance(name, str) or not name for name in required)
    ):
        failures.append("requiredChecks must be a non-empty string array")
        required = []
    risk = evidence.get("risk")
    if risk not in RISK_POLICY_KEYS:
        failures.append("evidence risk is missing or invalid")
    else:
        effective_policy = policy or merged_policy({})
        policy_required = effective_policy["validation"][RISK_POLICY_KEYS[risk]]
        omitted = sorted(name for name in policy_required if name not in required)
        if omitted:
            failures.append(f"evidence omits policy-required checks: {', '.join(omitted)}")
    missing = sorted(name for name in required if checks.get(name) != "pass")
    if missing:
        failures.append(f"required checks not passing: {', '.join(missing)}")
    return {
        "allowed": not failures,
        "failures": failures,
        "headSha": current_head,
        "baseSha": current_base,
    }


def choose_merge_method(priority: list[str], allowed: set[str]) -> str:
    for method in priority:
        if method in allowed:
            return method
    raise HarnessError("repository allows none of the configured merge methods")


def validate_policy(policy: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(policy, dict):
        return ["policy must be an object"]
    known = {
        "$schema",
        "minimumReleaseAgeHours",
        "maxParallelRepositories",
        "mergeMethodPriority",
        "allowAutoMerge",
        "allowedActions",
        "ignore",
        "knowledgeMaxAgeMonths",
        "validation",
    }
    unknown = sorted(set(policy) - known)
    if unknown:
        errors.append(f"unknown policy keys: {', '.join(unknown)}")
    def integer(value: Any) -> bool:
        return isinstance(value, int) and not isinstance(value, bool)

    if "$schema" in policy and not isinstance(policy["$schema"], str):
        errors.append("$schema must be a string")
    if "minimumReleaseAgeHours" in policy:
        value = policy["minimumReleaseAgeHours"]
        if not integer(value) or value < 0:
            errors.append("minimumReleaseAgeHours must be a non-negative integer")
    if "maxParallelRepositories" in policy:
        maximum = policy["maxParallelRepositories"]
        if not integer(maximum) or not 1 <= maximum <= 8:
            errors.append("maxParallelRepositories must be an integer from 1 to 8")
    if "mergeMethodPriority" in policy:
        priority = policy["mergeMethodPriority"]
        if (
            not isinstance(priority, list)
            or not priority
            or any(not isinstance(item, str) for item in priority)
            or len(priority) != len(set(priority))
        ):
            errors.append("mergeMethodPriority must be a non-empty unique string array")
        elif any(item not in {"squash", "merge", "rebase"} for item in priority):
            errors.append("mergeMethodPriority contains an unsupported method")
    if "allowAutoMerge" in policy and not isinstance(policy["allowAutoMerge"], bool):
        errors.append("allowAutoMerge must be boolean")
    if "allowedActions" in policy:
        actions = policy["allowedActions"]
        supported = {"merge", "fix", "close", "comment", "request-rebase"}
        if (
            not isinstance(actions, list)
            or any(not isinstance(item, str) for item in actions)
            or len(actions) != len(set(actions))
        ):
            errors.append("allowedActions must be a unique string array")
        elif any(item not in supported for item in actions):
            errors.append("allowedActions contains an unsupported action")
    if "ignore" in policy:
        ignored = policy["ignore"]
        if (
            not isinstance(ignored, list)
            or any(not isinstance(item, str) or not item for item in ignored)
            or len(ignored) != len(set(ignored))
        ):
            errors.append("ignore must be a unique non-empty string array")
    if "knowledgeMaxAgeMonths" in policy:
        value = policy["knowledgeMaxAgeMonths"]
        if not integer(value) or value < 0:
            errors.append("knowledgeMaxAgeMonths must be a non-negative integer")
    if "validation" in policy:
        validation = policy["validation"]
        if not isinstance(validation, dict):
            errors.append("validation must be an object")
        else:
            unknown_validation = sorted(
                set(validation) - {"lowRisk", "mediumRisk", "highRisk"}
            )
            if unknown_validation:
                errors.append(
                    f"unknown validation keys: {', '.join(unknown_validation)}"
                )
            for risk, checks in validation.items():
                if (
                    not isinstance(checks, list)
                    or not checks
                    or any(not isinstance(item, str) or not item for item in checks)
                    or len(checks) != len(set(checks))
                ):
                    errors.append(f"validation.{risk} must be a unique non-empty string array")
    return errors


def merged_policy(override: dict[str, Any]) -> dict[str, Any]:
    errors = validate_policy(override)
    if errors:
        raise HarnessError("invalid policy: " + "; ".join(errors))
    default = read_json(DEFAULT_POLICY_PATH)
    result = {**default, **override}
    if "validation" in override:
        result["validation"] = {**default.get("validation", {}), **override["validation"]}
    return result


def validate_patterns(value: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(value, dict) or value.get("schemaVersion") != 1:
        return ["patterns must be an object with schemaVersion 1"]
    patterns = value.get("patterns")
    if not isinstance(patterns, list):
        return ["patterns must be an array"]
    ids: set[str] = set()
    counts: dict[str, int] = {}
    required = {"id", "ecosystem", "packages", "summary", "guidance", "trust"}
    for index, pattern in enumerate(patterns):
        if not isinstance(pattern, dict):
            errors.append(f"pattern {index} must be an object")
            continue
        missing = sorted(required - set(pattern))
        if missing:
            errors.append(f"pattern {index} missing: {', '.join(missing)}")
        pattern_id = pattern.get("id")
        if pattern_id in ids:
            errors.append(f"duplicate pattern id: {pattern_id}")
        if isinstance(pattern_id, str):
            ids.add(pattern_id)
        ecosystem = str(pattern.get("ecosystem", ""))
        counts[ecosystem] = counts.get(ecosystem, 0) + 1
        if pattern.get("trust") not in {"curated", "observed"}:
            errors.append(f"pattern {pattern_id} has invalid trust")
        packages = pattern.get("packages")
        if (
            not isinstance(packages, list)
            or not packages
            or any(not isinstance(item, str) or not item for item in packages)
        ):
            errors.append(f"pattern {pattern_id} needs at least one package")
        has_range = all(
            isinstance(pattern.get(field), str) and pattern.get(field)
            for field in ("from", "to")
        )
        has_condition = isinstance(pattern.get("appliesWhen"), str) and bool(
            pattern.get("appliesWhen")
        )
        if not has_range and not has_condition:
            errors.append(f"pattern {pattern_id} needs a version range or appliesWhen")
        if pattern.get("trust") == "observed":
            for field in ("learned", "lastVerified"):
                value = pattern.get(field)
                if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}", value):
                    errors.append(f"observed pattern {pattern_id} needs {field} as YYYY-MM")
    for ecosystem, count in counts.items():
        if count > 30:
            errors.append(f"ecosystem {ecosystem} exceeds 30 patterns")
    return errors


def validate_bundle(skill_root: Path) -> list[str]:
    errors: list[str] = []
    required = [
        "SKILL.md",
        "agents/openai.yaml",
        "knowledge/patterns.json",
        "references/default-policy.json",
        "references/policy.schema.json",
        "references/github.md",
        "references/risk-policy.md",
        "references/validation.md",
        "references/knowledge.md",
        "scripts/resolve_prs.py",
    ]
    for relative in required:
        if not (skill_root / relative).is_file():
            errors.append(f"missing bundle file: {relative}")
    skill_path = skill_root / "SKILL.md"
    if skill_path.exists():
        skill = skill_path.read_text()
        if not re.match(r"^---\n(?s:.*?)\n---", skill):
            errors.append("SKILL.md has invalid frontmatter")
        for field in ("name:", "description:"):
            if field not in skill.split("---", 2)[1]:
                errors.append(f"SKILL.md missing {field[:-1]}")
        forbidden = ("$ARGUMENTS", "allowed-tools:", "Claude team", "~/.claude/skills")
        for marker in forbidden:
            if marker in skill:
                errors.append(f"SKILL.md contains host-specific marker: {marker}")
        for target in re.findall(r"\[[^]]+\]\(([^)#]+)(?:#[^)]+)?\)", skill):
            if "://" not in target and not (skill_root / target).exists():
                errors.append(f"SKILL.md link target missing: {target}")
    if (skill_root / "references/default-policy.json").exists():
        errors.extend(validate_policy(read_json(skill_root / "references/default-policy.json")))
    if (skill_root / "knowledge/patterns.json").exists():
        errors.extend(validate_patterns(read_json(skill_root / "knowledge/patterns.json")))
    return errors


def patterns_for_package(package: str, path: Path = PATTERNS_PATH) -> list[dict[str, Any]]:
    value = read_json(path)
    matches = []
    for pattern in value.get("patterns", []):
        if any(fnmatch.fnmatchcase(package, item) for item in pattern.get("packages", [])):
            matches.append(pattern)
    return matches


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    discover = sub.add_parser("discover-repos")
    discover.add_argument("--root", default=".")
    discover.add_argument("--max-depth", type=int, default=2)

    sub.add_parser("filter-prs")

    ignored = sub.add_parser("match-ignore")
    ignored.add_argument("--package", required=True)
    ignored.add_argument("--file", required=True)

    inspect = sub.add_parser("inspect-repo")
    inspect.add_argument("--root", default=".")
    inspect.add_argument("--changed", action="append", default=[])

    pattern = sub.add_parser("patterns")
    pattern.add_argument("--package", required=True)
    pattern.add_argument("--file", default=str(PATTERNS_PATH))

    evidence = sub.add_parser("evidence")
    evidence.add_argument("--repo", required=True)
    evidence.add_argument("--pr", required=True, type=int)
    evidence.add_argument("--head-sha", required=True)
    evidence.add_argument("--base-sha", required=True)
    evidence.add_argument("--risk", choices=("low", "medium", "high"), required=True)
    evidence.add_argument("--check", action="append", default=[])
    evidence.add_argument("--required", action="append", default=[])
    evidence.add_argument("--policy")

    gate = sub.add_parser("gate")
    gate.add_argument("--evidence", required=True)
    gate.add_argument("--repo", required=True)
    gate.add_argument("--pr", required=True, type=int)
    gate.add_argument("--current-head", required=True)
    gate.add_argument("--current-base", required=True)
    gate.add_argument("--policy")

    merge = sub.add_parser("choose-merge-method")
    merge.add_argument("--priority", nargs="+", default=["squash", "merge", "rebase"])
    merge.add_argument("--allowed", nargs="+", required=True)

    candidate = sub.add_parser("candidate")
    candidate.add_argument("--id", required=True)
    candidate.add_argument("--ecosystem", required=True)
    candidate.add_argument("--package", action="append", required=True)
    candidate.add_argument("--from-version", required=True)
    candidate.add_argument("--to-version", required=True)
    candidate.add_argument("--summary", required=True)
    candidate.add_argument("--guidance", required=True)
    candidate.add_argument("--source", action="append", default=[])

    policy = sub.add_parser("validate-policy")
    policy.add_argument("path")

    load_policy = sub.add_parser("load-policy")
    load_policy.add_argument("--path")

    bundle = sub.add_parser("validate-bundle")
    bundle.add_argument("--skill-root", default=str(SKILL_ROOT))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "discover-repos":
            dump(find_repositories(Path(args.root), args.max_depth))
        elif args.command == "filter-prs":
            value = json.load(sys.stdin)
            if not isinstance(value, list):
                raise HarnessError("filter-prs expects a JSON array on stdin")
            dump(filter_prs(value))
        elif args.command == "match-ignore":
            patterns = parse_ignore_lines(Path(args.file).read_text())
            match = next((item for item in patterns if matches_ignore(args.package, item)), None)
            dump({"ignored": match is not None, "pattern": match})
        elif args.command == "inspect-repo":
            dump(inspect_repository(Path(args.root), args.changed))
        elif args.command == "patterns":
            dump(patterns_for_package(args.package, Path(args.file)))
        elif args.command == "evidence":
            dump(make_evidence(args))
        elif args.command == "gate":
            override = read_json(Path(args.policy)) if args.policy else {}
            result = gate_evidence(
                read_json(Path(args.evidence)),
                args.repo,
                args.pr,
                args.current_head,
                args.current_base,
                merged_policy(override),
            )
            dump(result)
            return 0 if result["allowed"] else 2
        elif args.command == "choose-merge-method":
            dump({"method": choose_merge_method(args.priority, set(args.allowed))})
        elif args.command == "candidate":
            dump(
                {
                    "id": args.id,
                    "ecosystem": args.ecosystem,
                    "packages": args.package,
                    "from": args.from_version,
                    "to": args.to_version,
                    "summary": args.summary,
                    "guidance": args.guidance,
                    "trust": "observed",
                    "learned": datetime.now(timezone.utc).date().isoformat()[:7],
                    "lastVerified": datetime.now(timezone.utc).date().isoformat()[:7],
                    "sources": args.source,
                    "status": "candidate",
                }
            )
        elif args.command == "validate-policy":
            errors = validate_policy(read_json(Path(args.path)))
            dump({"valid": not errors, "errors": errors})
            return 0 if not errors else 2
        elif args.command == "load-policy":
            override = read_json(Path(args.path)) if args.path else {}
            dump(merged_policy(override))
        elif args.command == "validate-bundle":
            errors = validate_bundle(Path(args.skill_root))
            dump({"valid": not errors, "errors": errors})
            return 0 if not errors else 2
    except (HarnessError, OSError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
