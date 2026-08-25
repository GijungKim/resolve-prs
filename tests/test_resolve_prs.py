import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "skills" / "resolve-prs" / "scripts" / "resolve_prs.py"
SPEC = importlib.util.spec_from_file_location("resolve_prs_harness", SCRIPT)
assert SPEC and SPEC.loader
HARNESS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(HARNESS)


class QualificationTests(unittest.TestCase):
    def test_accepts_known_bot(self):
        pr = {"title": "Update package", "author": {"login": "renovate[bot]"}}
        self.assertEqual(HARNESS.qualification_reason(pr), "dependency-bot author")

    def test_accepts_conventional_human_pr(self):
        pr = {"title": "chore(deps): update react", "author": {"login": "human"}}
        self.assertEqual(
            HARNESS.qualification_reason(pr), "conventional dependency title"
        )

    def test_rejects_unrelated_pr(self):
        pr = {"title": "Fix session handling", "author": {"login": "human"}}
        self.assertIsNone(HARNESS.qualification_reason(pr))

    def test_marks_lock_file_maintenance(self):
        result = HARNESS.filter_prs(
            [
                {
                    "number": 1,
                    "title": "chore(deps): lock file maintenance",
                    "author": {"login": "renovate[bot]"},
                }
            ]
        )
        self.assertTrue(result[0]["lockFileMaintenance"])


class IgnoreTests(unittest.TestCase):
    def test_parses_comments_and_matches_within_segment(self):
        patterns = HARNESS.parse_ignore_lines(
            "# pinned\nexpo-* # sdk pin\n@scope/*\n\n"
        )
        self.assertEqual(patterns, ["expo-*", "@scope/*"])
        self.assertTrue(HARNESS.matches_ignore("expo-router", patterns[0]))
        self.assertTrue(HARNESS.matches_ignore("@scope/pkg", patterns[1]))
        self.assertFalse(HARNESS.matches_ignore("@scope/nested/pkg", patterns[1]))
        self.assertFalse(HARNESS.matches_ignore("foo/bar", "*"))


class RepositoryInspectionTests(unittest.TestCase):
    def test_detects_workspace_and_affected_scripts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "packages" / "app").mkdir(parents=True)
            (root / "package.json").write_text(
                json.dumps({"workspaces": ["packages/*"], "scripts": {"lint": "eslint ."}})
            )
            (root / "pnpm-lock.yaml").write_text("lockfileVersion: 9\n")
            (root / "packages" / "app" / "package.json").write_text(
                json.dumps({"scripts": {"typecheck": "tsc --noEmit", "test": "vitest"}})
            )
            result = HARNESS.inspect_repository(
                root, ["packages/app/package.json"]
            )
            self.assertTrue(result["workspace"])
            self.assertEqual(result["installDirectory"], ".")
            self.assertEqual(result["packageManagers"][0]["manager"], "pnpm")
            self.assertEqual(
                result["affectedManifests"][0]["scripts"]["typecheck"],
                "tsc --noEmit",
            )

    def test_rejects_changed_path_outside_root(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(HARNESS.HarnessError):
                HARNESS.inspect_repository(root, ["../outside/package.json"])

    def test_discovers_worktree_with_git_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            worktree = root / "linked-worktree"
            worktree.mkdir()
            (worktree / ".git").write_text("gitdir: /tmp/example\n")
            self.assertEqual(
                HARNESS.find_repositories(root, 2), [str(worktree.resolve())]
            )


class EvidenceGateTests(unittest.TestCase):
    def evidence(self):
        return {
            "schemaVersion": 1,
            "repository": "owner/repo",
            "pullRequest": 12,
            "headSha": "abc123",
            "baseSha": "base123",
            "risk": "low",
            "requiredChecks": ["typecheck", "lint"],
            "checks": {"typecheck": "pass", "lint": "pass"},
        }

    def test_allows_matching_green_evidence(self):
        result = HARNESS.gate_evidence(
            self.evidence(), "owner/repo", 12, "abc123", "base123"
        )
        self.assertTrue(result["allowed"])

    def test_rejects_head_change(self):
        result = HARNESS.gate_evidence(
            self.evidence(), "owner/repo", 12, "different", "base123"
        )
        self.assertFalse(result["allowed"])
        self.assertIn("current head does not match validated head", result["failures"])

    def test_rejects_missing_or_failed_required_check(self):
        evidence = self.evidence()
        evidence["checks"]["lint"] = "fail"
        result = HARNESS.gate_evidence(
            evidence, "owner/repo", 12, "abc123", "base123"
        )
        self.assertFalse(result["allowed"])
        self.assertTrue(any("lint" in failure for failure in result["failures"]))

    def test_rejects_empty_required_checks(self):
        evidence = self.evidence()
        evidence["requiredChecks"] = []
        result = HARNESS.gate_evidence(
            evidence, "owner/repo", 12, "abc123", "base123"
        )
        self.assertFalse(result["allowed"])

    def test_rejects_base_change(self):
        result = HARNESS.gate_evidence(
            self.evidence(), "owner/repo", 12, "abc123", "newbase"
        )
        self.assertFalse(result["allowed"])
        self.assertIn("current base does not match validated base", result["failures"])

    def test_rejects_evidence_that_omits_current_policy_requirements(self):
        evidence = self.evidence()
        evidence["risk"] = "medium"
        result = HARNESS.gate_evidence(
            evidence, "owner/repo", 12, "abc123", "base123"
        )
        self.assertFalse(result["allowed"])
        self.assertTrue(any("policy-required" in failure for failure in result["failures"]))


class ConfigurationTests(unittest.TestCase):
    def test_default_policy_and_knowledge_are_valid(self):
        policy = HARNESS.read_json(HARNESS.DEFAULT_POLICY_PATH)
        patterns = HARNESS.read_json(HARNESS.PATTERNS_PATH)
        self.assertEqual(HARNESS.validate_policy(policy), [])
        self.assertEqual(HARNESS.validate_patterns(patterns), [])

    def test_partial_policy_merges_over_defaults(self):
        policy = HARNESS.merged_policy({"ignore": ["expo-*"]})
        self.assertEqual(policy["ignore"], ["expo-*"])
        self.assertEqual(policy["mergeMethodPriority"], ["squash", "merge", "rebase"])

    def test_policy_validation_checks_are_available_to_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            policy_path = Path(directory) / "policy.json"
            policy_path.write_text(
                json.dumps({"validation": {"mediumRisk": ["integration"]}})
            )
            args = type(
                "Args",
                (),
                {
                    "check": ["integration=pass"],
                    "required": [],
                    "risk": "medium",
                    "repo": "owner/repo",
                    "pr": 1,
                    "head_sha": "head",
                    "base_sha": "base",
                    "policy": str(policy_path),
                },
            )()
            evidence = HARNESS.make_evidence(args)
            self.assertEqual(evidence["requiredChecks"], ["integration"])

    def test_explicit_required_checks_cannot_weaken_policy(self):
        args = type(
            "Args",
            (),
            {
                "check": ["custom=pass"],
                "required": ["custom"],
                "risk": "medium",
                "repo": "owner/repo",
                "pr": 1,
                "head_sha": "head",
                "base_sha": "base",
                "policy": None,
            },
        )()
        evidence = HARNESS.make_evidence(args)
        self.assertEqual(
            evidence["requiredChecks"],
            ["install", "typecheck", "lint", "test", "custom"],
        )

    def test_rejects_schema_invalid_policy_values(self):
        invalid = {
            "minimumReleaseAgeHours": -1,
            "allowedActions": ["destroy"],
            "validation": {"lowRisk": "not-an-array"},
        }
        errors = HARNESS.validate_policy(invalid)
        self.assertGreaterEqual(len(errors), 3)

    def test_rejects_incomplete_observed_pattern(self):
        value = {
            "schemaVersion": 1,
            "patterns": [
                {
                    "id": "incomplete",
                    "ecosystem": "javascript",
                    "packages": ["package"],
                    "summary": "summary",
                    "guidance": "guidance",
                    "trust": "observed",
                }
            ],
        }
        errors = HARNESS.validate_patterns(value)
        self.assertTrue(any("version range" in error for error in errors))
        self.assertTrue(any("lastVerified" in error for error in errors))

    def test_merge_method_respects_priority(self):
        self.assertEqual(
            HARNESS.choose_merge_method(
                ["squash", "merge", "rebase"], {"merge", "rebase"}
            ),
            "merge",
        )

    def test_bundle_is_complete_and_portable(self):
        self.assertEqual(HARNESS.validate_bundle(HARNESS.SKILL_ROOT), [])


if __name__ == "__main__":
    unittest.main()
