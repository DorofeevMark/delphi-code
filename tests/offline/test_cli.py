import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
MODEL = os.environ.get("DELPHI_CODE_TEST_MODEL")


@unittest.skipUnless(MODEL, "Set DELPHI_CODE_TEST_MODEL to a provisioned local model")
class OfflineCLI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workspace = tempfile.TemporaryDirectory(prefix="delphi-code-test-")
        cls.base = Path(cls.workspace.name).resolve()
        cls.project = cls.base / "project"
        cls.project.mkdir()
        cls.index_root = cls.base / "indexes"
        cls.audit = cls.base / "audit"
        cls.audit.mkdir()
        (cls.audit / "sitecustomize.py").write_text(
            "import os, sys\n"
            "def audit(event, args):\n"
            "    if event in {'socket.connect', 'socket.getaddrinfo', 'socket.gethostbyname', 'socket.gethostbyaddr', 'socket.sendto'}:\n"
            "        if event != 'socket.connect' or getattr(args[0], 'family', None) in (2, 10, 30):\n"
            "            with open(os.environ['DELPHI_CODE_NETWORK_LOG'], 'a') as stream:\n"
            "                stream.write(event + '\\n')\n"
            "            raise RuntimeError('Network attempted during offline test')\n"
            "sys.addaudithook(audit)\n"
        )
        cls.network_log = cls.base / "network.log"
        cls.env = dict(
            os.environ,
            PYTHONPATH=os.pathsep.join([str(cls.audit), str(ROOT)]),
            DELPHI_CODE_INDEX_ROOT=str(cls.index_root),
            DELPHI_CODE_REGISTRY=str(cls.base / "repos.toml"),
            DELPHI_CODE_LOG_FILE=str(cls.base / "delphi-code.log"),
            HF_HOME=str(cls.base / "empty-hf-cache"),
            DELPHI_CODE_NETWORK_LOG=str(cls.network_log),
            COCOINDEX_DISABLE_USAGE_TRACKING="0",
            HF_HUB_OFFLINE="0",
            HF_HUB_DISABLE_TELEMETRY="0",
        )

    @classmethod
    def tearDownClass(cls):
        cls.workspace.cleanup()

    def invoke(self, *args, code=0, model=MODEL, project=True, env=None):
        command = [sys.executable, "-m", "delphi_code", *args]
        if project:
            command.extend(["--project", str(self.project)])
        if args[0] not in {"status", "list", "remove"}:
            command.extend(["--model", str(model)])
        result = subprocess.run(command, env=env or self.env, text=True, capture_output=True, timeout=120)
        self.assertEqual(result.returncode, code, result.stderr + result.stdout)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["ok"], code == 0)
        self.assertFalse(self.network_log.exists(), self.network_log.read_text() if self.network_log.exists() else "")
        return payload["data"] if code == 0 else payload["error"]

    def test_setup_import_and_reuse(self):
        destination = self.base / "provisioned-model"
        command = [sys.executable, "-m", "delphi_code", "setup", "--from", MODEL, "--model", str(destination)]
        for reused in (False, True):
            result = subprocess.run(command, env=self.env, text=True, capture_output=True, timeout=120)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["data"]["reused"], reused)
            self.assertEqual(payload["data"]["diagnostics"]["dimensions"], 384)
            self.assertEqual(payload["data"]["diagnostics"]["model"], str(destination))
            self.assertFalse(self.network_log.exists())

    def test_workflow(self):
        missing = self.invoke("index", model=self.base / "missing", code=3)
        self.assertEqual(missing["code"], "model_missing")
        self.assertFalse((self.project / ".delphi-code").exists())
        self.assertEqual(self.invoke("search", "password", code=4)["code"], "index_missing")
        doctor = self.invoke("doctor")
        self.assertEqual(doctor["dimensions"], 384)
        self.assertEqual(doctor["self_distance"], 0)
        (self.project / "auth.py").write_text(
            "def authenticate_user(password, expected_password):\n    return password == expected_password\n"
        )
        (self.project / "math.py").write_text("def add_numbers(left, right):\n    return left + right\n")
        (self.project / ".gitignore").write_text("ignored.py\n*.private\n")
        (self.project / "ignored.py").write_text("TOP_SECRET = 'password'\n")
        (self.project / "binary.py").write_bytes(b"\x00\xff")
        (self.project / "nested").mkdir()
        (self.project / "nested/.gitignore").write_text("!keep.private\nhidden.py\n")
        (self.project / "nested/keep.private").write_text("retained text")
        (self.project / "nested/hide.private").write_text("excluded text")
        (self.project / "nested/hidden.py").write_text("excluded code")
        (self.project / "linked.py").symlink_to(self.project / "auth.py")
        initial = self.invoke("index")
        self.assertGreaterEqual(initial["files"], 3)
        self.state = Path(initial["index_directory"])
        self.assertEqual(self.state.parent, self.index_root)
        self.assertEqual(initial["key"], f"local:{self.project}")
        self.assertFalse((self.project / ".delphi-code").exists())
        results = self.invoke("search", "verify user password", "--language", "python", "--limit", "1")["results"]
        self.assertEqual(results[0]["path"], "auth.py")
        across = self.invoke("search", "verify user password", "--limit", "1", project=False)
        self.assertEqual(across["projects"], [str(self.project)])
        self.assertEqual(across["results"][0]["project"], str(self.project))
        self.assertEqual(across["results"][0]["path"], "auth.py")
        self.assertEqual(results[0]["start_line"], 1)
        self.assertEqual(results[0]["end_line"], 2)
        repeated = self.invoke("index")
        self.assertEqual(repeated["incremental"].get("num_adds", 0), 0)
        self.assertEqual(repeated["incremental"].get("num_reprocesses", 0), 0)
        self.assertGreater(repeated["incremental"].get("num_unchanged", 0), 0)
        auth = self.project / "auth.py"
        timestamp = auth.stat()
        auth.write_text("def verify_token(token, expected):\n    return token == expected\n")
        os.utime(auth, ns=(timestamp.st_atime_ns, timestamp.st_mtime_ns))
        (self.project / "math.py").unlink()
        changed = self.invoke("index")
        self.assertGreater(changed["incremental"].get("num_reprocesses", 0), 0)
        self.assertGreater(changed["incremental"].get("num_deletes", 0), 0)
        self.assertEqual(self.invoke("search", "add numbers", "--path", "math.py")["results"], [])
        updated = self.invoke("search", "token", "--path", "auth.py")["results"]
        self.assertIn("verify_token", updated[0]["text"])
        self.assertEqual(self.invoke("search", "excluded", "--path", "nested/hidden.py")["results"], [])
        self.assertEqual(self.invoke("search", "excluded", "--path", "nested/hide.private")["results"], [])
        self.assertEqual(len(self.invoke("search", "retained", "--path", "nested/keep.private")["results"]), 1)
        self.assertTrue(self.invoke("status")["ready"])
        with (self.state / "lock").open("r") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertEqual(self.invoke("status", code=5)["code"], "index_busy")
            self.assertEqual(self.invoke("search", "token", code=5)["code"], "index_busy")
        manifest = self.state / "manifest.json"
        incomplete = json.loads(manifest.read_text())
        incomplete["ready"] = False
        manifest.write_text(json.dumps(incomplete))
        self.assertEqual(self.invoke("search", "token", code=4)["code"], "index_incomplete")
        self.invoke("index")
        alternative = self.base / "alternative-model"
        alternative.mkdir()
        (alternative / "modules.json").write_text('[{"type":"sentence_transformers.models.Transformer","path":""}]')
        self.assertEqual(self.invoke("index", model=alternative, code=3)["code"], "model_missing")
        (alternative / "model.safetensors").write_bytes(b"invalid")
        self.assertEqual(self.invoke("search", "token", model=alternative, code=4)["code"], "model_mismatch")
        self.assertEqual(self.invoke("doctor", model=alternative, code=3)["code"], "model_invalid")
        self.invoke("search", "token", "--limit", "0", code=2)
        self.invoke("index", "--language", "python", "--ignore", "auth.py")
        self.assertEqual(self.invoke("search", "token")["results"], [])

    def test_registry_workflow(self):
        env = dict(self.env, DELPHI_CODE_INDEX_ROOT=str(self.base / "registry-indexes"))
        invoke = lambda *args, code=0: self.invoke(*args, code=code, project=False, env=env)
        project = self.base / "tracked"
        project.mkdir()
        (project / "parse.py").write_text(
            "def parse_configuration(text):\n    return dict(line.split('=') for line in text.splitlines())\n"
        )
        subprocess.run(["git", "init", "-q", str(project)], check=True)
        subprocess.run(
            ["git", "-C", str(project), "remote", "add", "origin", "git@bitbucket.org:acme/tracked.git"], check=True
        )
        added = invoke("add", str(project))["repos"]
        self.assertEqual([(repo["ok"], repo["key"]) for repo in added], [(True, "bitbucket.org/acme/tracked")])
        results = invoke("search", "read config file", "-p", "acme/tracked")["results"]
        self.assertEqual(results[0]["path"], "parse.py")
        moved = self.base / "moved"
        project.rename(moved)
        again = invoke("index", "-p", str(moved))
        self.assertEqual(again["index_directory"], added[0]["index_directory"])
        self.assertEqual(again["incremental"].get("num_adds", 0), 0)
        self.assertEqual(invoke("sync", code=5)["code"], "sync_failed")
        rows = {row["key"]: row for row in invoke("list")["repos"]}
        self.assertTrue(rows["bitbucket.org/acme/tracked"]["tracked"])
        removed = invoke("remove", "tracked")
        self.assertEqual(removed["deleted_index"], added[0]["index_directory"])
        self.assertFalse(Path(added[0]["index_directory"]).exists())

    def test_remote_workflow(self):
        remote = self.base / "remote"
        env = dict(
            self.env,
            DELPHI_CODE_INDEX_ROOT=str(self.base / "remote-indexes"),
            DELPHI_CODE_REGISTRY=str(self.base / "remote.toml"),
            DELPHI_CODE_BITBUCKET_GIT_BASE=remote.as_uri(),
            DELPHI_CODE_GITHUB_GIT_BASE=remote.as_uri(),
        )
        invoke = lambda *args, code=0: self.invoke(*args, code=code, project=False, env=env)
        git = lambda *args: subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout.strip()
        work = self.base / "remote-work"
        git("init", "-q", "--bare", "-b", "main", str(remote / "acme/api.git"))
        git("clone", "-q", str(remote / "acme/api.git"), str(work))
        git("-C", str(work), "symbolic-ref", "HEAD", "refs/heads/main")
        (work / "auth.py").write_text(
            "def authenticate_user(password, expected_password):\n    return password == expected_password\n"
        )
        (work / "math.py").write_text("def add_numbers(left, right):\n    return left + right\n")
        git("-C", str(work), "add", ".")
        git("-C", str(work), "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-q", "-m", "first")
        git("-C", str(work), "push", "-q", "origin", "main")
        added = invoke("add", "bitbucket.org/acme/api")["repos"][0]
        self.assertEqual(
            (added["key"], added["project"], added["ref"], added["files"]), ("bitbucket.org/acme/api", None, "main", 2)
        )
        commit = added["commit"]
        result = invoke("search", "verify user password", "--limit", "1")["results"][0]
        self.assertEqual(
            (result["key"], result["path"], result["project"]), ("bitbucket.org/acme/api", "auth.py", None)
        )
        self.assertEqual(result["url"], f"https://bitbucket.org/acme/api/src/{commit}/auth.py#lines-1:2")
        self.assertTrue(invoke("sync")["repos"][0]["unchanged"])
        (work / "math.py").write_text("def multiply_numbers(left, right):\n    return left * right\n")
        git(
            "-C",
            str(work),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "-q",
            "-am",
            "second",
        )
        git("-C", str(work), "push", "-q", "origin", "main")
        synced = invoke("sync")["repos"][0]
        self.assertFalse(synced["unchanged"])
        self.assertNotEqual(synced["commit"], commit)
        self.assertEqual((synced["incremental"].get("num_unchanged"), synced["incremental"].get("num_adds", 0)), (1, 0))
        self.assertEqual(invoke("index", "-p", "acme/api", code=2)["code"], "usage")
        self.assertIn(
            "multiply",
            invoke("search", "multiply", "-p", "bitbucket.org/acme/api", "--limit", "1")["results"][0]["text"],
        )
        mirrored = invoke("add", "github.com/acme/api")["repos"][0]
        self.assertEqual((mirrored["key"], mirrored["incremental"].get("num_adds")), ("github.com/acme/api", 2))
        result = invoke("search", "multiply", "-p", "github.com/acme/api", "--limit", "1")["results"][0]
        self.assertEqual(result["url"], f"https://github.com/acme/api/blob/{mirrored['commit']}/math.py#L1-L2")
        self.assertEqual(invoke("search", "-p", "api", "multiply", code=2)["code"], "project_ambiguous")


class NetworkSandbox(unittest.TestCase):
    @unittest.skipUnless(os.environ.get("DELPHI_CODE_OS_SANDBOX") == "1", "Run via scripts/test_offline.sh")
    def test_os_denies_network(self):
        probe = subprocess.run(
            [
                sys.executable,
                "-c",
                "import socket, sys\n"
                "with socket.socket() as sock:\n"
                "    try:\n"
                "        sock.connect(('127.0.0.1', 9))\n"
                "    except PermissionError:\n"
                "        sys.exit(0)\n"
                "    raise RuntimeError('OS network denial is not active')\n",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(probe.returncode, 0, probe.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
