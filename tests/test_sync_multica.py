import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
SYNC = REPO / "scripts" / "sync-multica.py"
ENTRYPOINT = REPO / "bin" / "agent-sync"


FAKE_MULTICA = r"""#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

state_path = Path(os.environ["FAKE_MULTICA_STATE"])
log_path = Path(os.environ["FAKE_MULTICA_LOG"])
args = sys.argv[1:]
with log_path.open("a", encoding="utf-8") as handle:
    handle.write(json.dumps(args) + "\n")
state = json.loads(state_path.read_text(encoding="utf-8"))

def emit(value):
    print(json.dumps(value))

def option(name):
    return args[args.index(name) + 1]

def save():
    state_path.write_text(json.dumps(state), encoding="utf-8")

def by_id(collection, identifier):
    return next(item for item in collection if item["id"] == identifier)

if args == ["--version"]:
    print(f"multica {state['version']} (commit: fake, built: now)")
elif args[:2] == ["skill", "list"]:
    emit(state["skills"])
elif args[:2] == ["skill", "get"]:
    emit(by_id(state["skills"], args[2]))
elif args[:2] == ["skill", "create"]:
    identifier = f"skill-{len(state['skills']) + 1}"
    item = {
        "id": identifier,
        "name": option("--name"),
        "description": option("--description") if "--description" in args else "",
        "content": Path(option("--content-file")).read_text(encoding="utf-8"),
        "files": [],
    }
    state["skills"].append(item)
    save()
    emit(item)
elif args[:2] == ["skill", "update"]:
    item = by_id(state["skills"], args[2])
    if "--name" in args:
        item["name"] = option("--name")
    if "--description" in args:
        item["description"] = option("--description")
    if "--content-file" in args:
        item["content"] = Path(option("--content-file")).read_text(encoding="utf-8")
    save()
    emit(item)
elif args[:3] == ["skill", "files", "upsert"]:
    item = by_id(state["skills"], args[3])
    path = option("--path")
    content = Path(option("--content-file")).read_text(encoding="utf-8")
    existing = next((entry for entry in item["files"] if entry["path"] == path), None)
    if existing:
        existing["content"] = content
    else:
        item["files"].append(
            {"id": f"file-{len(item['files']) + 1}", "path": path, "content": content}
        )
    save()
    emit(item)
elif args[:3] == ["skill", "files", "delete"]:
    item = by_id(state["skills"], args[3])
    file_id = args[4]
    item["files"] = [entry for entry in item["files"] if entry["id"] != file_id]
    save()
elif args[:2] == ["agent", "list"]:
    emit(state["agents"])
elif args[:2] == ["agent", "get"]:
    emit(by_id(state["agents"], args[2]))
elif args[:3] == ["agent", "skills", "set"]:
    item = by_id(state["agents"], args[3])
    skill_ids = option("--skill-ids").split(",") if option("--skill-ids") else []
    item["skills"] = [
        {"id": skill["id"], "name": skill["name"]}
        for skill in state["skills"]
        if skill["id"] in skill_ids
    ]
    save()
    emit(item)
elif args[:2] == ["squad", "list"]:
    emit(state["squads"])
elif args[:2] == ["squad", "get"]:
    emit(by_id(state["squads"], args[2]))
elif args[:2] == ["squad", "update"]:
    item = by_id(state["squads"], args[2])
    if "--name" in args:
        item["name"] = option("--name")
    if "--description" in args:
        item["description"] = option("--description")
    if "--instructions" in args:
        item["instructions"] = option("--instructions")
    if "--leader" in args:
        leader = option("--leader")
        matches = [
            agent for agent in state["agents"]
            if agent["id"] == leader or agent["name"] == leader
        ]
        item["leader_id"] = matches[0]["id"]
    save()
    emit(item)
else:
    print("unsupported fake command: " + " ".join(args), file=sys.stderr)
    sys.exit(64)
"""


class SyncMulticaTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        root = Path(self.tempdir.name)
        self.home = root / "home"
        self.hub = root / "hub"
        self.bin_dir = root / "bin"
        self.state_path = root / "state.json"
        self.log_path = root / "multica.log"
        self.bin_dir.mkdir()
        self.home.mkdir()
        self.hub.mkdir()
        fake = self.bin_dir / "multica"
        fake.write_text(FAKE_MULTICA, encoding="utf-8")
        fake.chmod(0o755)
        (self.hub / "manifest.yaml").write_text(
            "version: 1\nskills: []\n", encoding="utf-8"
        )
        skill_dir = self.hub / "skills" / "shared"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: shared\n---\nCanonical skill body.\n", encoding="utf-8"
        )
        (skill_dir / "reference.md").write_text(
            "Canonical reference.\n", encoding="utf-8"
        )
        squad_dir = self.hub / "multica" / "squads"
        squad_dir.mkdir(parents=True)
        (squad_dir / "research.md").write_text(
            "# Research routing\n\nUse one writer.\n", encoding="utf-8"
        )
        self.config = {
            "schema_version": 1,
            "minimum_multica_version": "0.4.9",
            "skills": [
                {
                    "name": "Shared Skill",
                    "source": "skills/shared",
                    "description": "Canonical description",
                }
            ],
            "agent_skill_assignments": [
                {"agent": "Planner", "skills": ["Shared Skill"]}
            ],
            "squads": [
                {
                    "name": "Research Squad",
                    "previous_name": "Old Research Squad",
                    "leader": "Planner",
                    "description": "Canonical squad description",
                    "instructions_file": "multica/squads/research.md",
                }
            ],
        }
        self.write_config()
        self.write_state()

    def tearDown(self):
        self.tempdir.cleanup()

    def write_config(self):
        path = self.hub / "multica" / "desired-state.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.config, indent=2) + "\n", encoding="utf-8")

    def write_state(self, *, version="0.4.9", duplicate_skill=False):
        skills = [
            {
                "id": "skill-1",
                "name": "Shared Skill",
                "description": "Stale description",
                "content": "Stale skill body.\n",
                "files": [
                    {"id": "file-1", "path": "reference.md", "content": "Stale.\n"},
                    {"id": "file-2", "path": "obsolete.md", "content": "Remove me.\n"},
                ],
            }
        ]
        if duplicate_skill:
            skills.append(
                {
                    "id": "skill-duplicate",
                    "name": "Shared Skill",
                    "description": "",
                    "content": "",
                    "files": [],
                }
            )
        state = {
            "version": version,
            "skills": skills,
            "agents": [
                {"id": "agent-1", "name": "Planner", "skills": []},
                {"id": "agent-2", "name": "Old Leader", "skills": []},
            ],
            "squads": [
                {
                    "id": "squad-1",
                    "name": "Old Research Squad",
                    "leader_id": "agent-2",
                    "description": "Stale squad description",
                    "instructions": "Stale instructions.\n",
                }
            ],
        }
        self.state_path.write_text(json.dumps(state), encoding="utf-8")
        self.log_path.write_text("", encoding="utf-8")

    def env(self):
        return os.environ | {
            "HOME": str(self.home),
            "AGENT_HUB_ROOT": str(self.hub),
            "AGENT_SYNC_HOME": str(REPO),
            "MULTICA_BIN": str(self.bin_dir / "multica"),
            "FAKE_MULTICA_STATE": str(self.state_path),
            "FAKE_MULTICA_LOG": str(self.log_path),
        }

    def run_script(self, *args):
        return subprocess.run(
            ["python3", str(SYNC), *args],
            cwd=REPO,
            env=self.env(),
            text=True,
            capture_output=True,
        )

    def calls(self):
        text = self.log_path.read_text(encoding="utf-8")
        return [json.loads(line) for line in text.splitlines() if line]

    def assert_no_mutating_calls(self):
        mutating = {
            ("skill", "create"),
            ("skill", "update"),
            ("skill", "files", "upsert"),
            ("skill", "files", "delete"),
            ("agent", "skills", "set"),
            ("squad", "update"),
        }
        calls = self.calls()
        self.assertFalse(
            any(tuple(call[: len(prefix)]) == prefix for call in calls for prefix in mutating),
            calls,
        )

    def test_default_is_read_only_and_returns_two_for_drift(self):
        before = self.state_path.read_bytes()

        result = self.run_script()

        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(self.state_path.read_bytes(), before)
        self.assertIn("[DRIFT]", result.stdout)
        self.assert_no_mutating_calls()

    def test_apply_converges_only_allowlisted_surfaces(self):
        result = self.run_script("--apply")

        self.assertEqual(result.returncode, 0, result.stderr)
        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        skill = state["skills"][0]
        self.assertEqual(skill["description"], "Canonical description")
        self.assertEqual(
            skill["content"],
            (self.hub / "skills" / "shared" / "SKILL.md").read_text(encoding="utf-8"),
        )
        self.assertEqual(
            [(item["path"], item["content"]) for item in skill["files"]],
            [("reference.md", "Canonical reference.\n")],
        )
        self.assertEqual(state["agents"][0]["skills"][0]["name"], "Shared Skill")
        squad = state["squads"][0]
        self.assertEqual(squad["name"], "Research Squad")
        self.assertEqual(squad["leader_id"], "agent-1")
        self.assertEqual(squad["description"], "Canonical squad description")
        self.assertEqual(
            squad["instructions"],
            (self.hub / "multica" / "squads" / "research.md").read_text(
                encoding="utf-8"
            ),
        )
        flattened = [" ".join(call) for call in self.calls()]
        self.assertFalse(
            any(
                forbidden in command
                for command in flattened
                for forbidden in (" runtime ", " mcp ", " issue ", " comment ", " git ")
            ),
            flattened,
        )
        combined = result.stdout + result.stderr
        for private_value in ("skill-1", "agent-1", "squad-1", "Canonical skill body"):
            self.assertNotIn(private_value, combined)

    def test_apply_creates_missing_skill_with_supporting_files(self):
        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        state["skills"] = []
        self.state_path.write_text(json.dumps(state), encoding="utf-8")

        result = self.run_script("--apply")

        self.assertEqual(result.returncode, 0, result.stderr)
        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        self.assertEqual(len(state["skills"]), 1)
        self.assertEqual(state["skills"][0]["name"], "Shared Skill")
        self.assertEqual(
            [(item["path"], item["content"]) for item in state["skills"][0]["files"]],
            [("reference.md", "Canonical reference.\n")],
        )

    def test_apply_is_idempotent_after_convergence(self):
        first = self.run_script("--apply")
        self.assertEqual(first.returncode, 0, first.stderr)
        self.log_path.write_text("", encoding="utf-8")

        second = self.run_script("--apply")

        self.assertEqual(second.returncode, 0, second.stderr)
        self.assert_no_mutating_calls()

    def test_duplicate_remote_name_fails_before_mutation(self):
        self.write_state(duplicate_skill=True)

        result = self.run_script("--apply")

        self.assertEqual(result.returncode, 1)
        self.assertIn("duplicate", result.stderr.lower())
        self.assert_no_mutating_calls()

    def test_source_must_stay_inside_private_hub(self):
        outside = Path(self.tempdir.name) / "outside"
        outside.mkdir()
        (outside / "SKILL.md").write_text("outside\n", encoding="utf-8")
        self.config["skills"][0]["source"] = "../outside"
        self.write_config()

        result = self.run_script("--apply")

        self.assertEqual(result.returncode, 1)
        self.assertIn("inside", result.stderr.lower())
        self.assertEqual(self.calls(), [])

    def test_old_multica_version_fails_before_remote_reads_or_writes(self):
        self.write_state(version="0.4.8")

        result = self.run_script("--apply")

        self.assertEqual(result.returncode, 1)
        self.assertIn("0.4.9", result.stderr)
        self.assertEqual(self.calls(), [["--version"]])

    def test_entrypoint_dispatches_multica_and_rejects_unknown_options(self):
        result = subprocess.run(
            [str(ENTRYPOINT), "multica"],
            cwd=REPO,
            env=self.env(),
            text=True,
            capture_output=True,
        )
        invalid = subprocess.run(
            [str(ENTRYPOINT), "multica", "--yes"],
            cwd=REPO,
            env=self.env(),
            text=True,
            capture_output=True,
        )

        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertNotEqual(invalid.returncode, 0)
        self.assertIn("unknown", (invalid.stdout + invalid.stderr).lower())

    def test_all_branch_does_not_reference_multica_adapter(self):
        entrypoint = ENTRYPOINT.read_text(encoding="utf-8")
        all_branch = entrypoint.split("\n    all)", 1)[1].split("\n        ;;", 1)[0]

        self.assertNotIn("sync-multica", all_branch)
        self.assertNotIn("multica", all_branch.lower())


if __name__ == "__main__":
    unittest.main()
