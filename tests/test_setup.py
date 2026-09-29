import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

from delphi_code.errors import ExitCode, Failure
from delphi_code.paths import data_directory
from delphi_code.setup import download, provision, verify_assets


class Setup(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "source"
        self.source.mkdir()
        (self.source / "model.safetensors").write_bytes(b"weights")
        self.manifest = {"sha256": {"model.safetensors": hashlib.sha256(b"weights").hexdigest()}}
        self.args = argparse.Namespace(model=str(self.root / "models/model"), source=str(self.source))
        self.reader = patch("delphi_code.setup.json.loads", return_value=self.manifest)
        self.reader.start()
        self.addCleanup(self.reader.stop)
        self.environment = patch.dict(os.environ, {"DELPHI_CODE_INDEX_ROOT": str(self.root / "indexes")})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def test_import_and_reuse_without_download(self):
        with patch("delphi_code.setup.diagnose", return_value={}) as doctor, patch("delphi_code.setup.download") as download:
            first = provision(self.args)
            self.assertFalse(first["reused"])
            self.assertEqual(Path(first["model"]).joinpath("model.safetensors").read_bytes(), b"weights")
            self.args.source = None
            self.assertTrue(provision(self.args)["reused"])
            self.assertEqual(doctor.call_count, 2)
            download.assert_not_called()

    def test_corruption_preserves_destination(self):
        destination = Path(self.args.model)
        destination.mkdir(parents=True)
        (destination / "model.safetensors").write_bytes(b"corrupt")
        with self.assertRaisesRegex(Failure, "Existing assets were preserved"):
            provision(self.args)
        self.assertEqual((destination / "model.safetensors").read_bytes(), b"corrupt")

    def test_failed_diagnostics_does_not_publish(self):
        with patch("delphi_code.setup.diagnose", side_effect=Failure("broken", "diagnostic failed", ExitCode.OPERATION)):
            with self.assertRaises(Failure):
                provision(self.args)
        self.assertFalse(Path(self.args.model).exists())
        self.assertEqual(list(Path(self.args.model).parent.glob(".model-*")), [])

    def test_download_is_verified_before_publication(self):
        self.args.source = None
        def fake_download(destination):
            (destination / "model.safetensors").write_bytes(b"bad download")
        with patch("delphi_code.setup.download", side_effect=fake_download), patch("delphi_code.setup.diagnose") as doctor:
            with self.assertRaisesRegex(Failure, "checksum"):
                provision(self.args)
            doctor.assert_not_called()
        self.assertFalse(Path(self.args.model).exists())

    def test_concurrent_setup_fails_clearly(self):
        parent = Path(self.args.model).parent
        parent.mkdir()
        with (parent / ".model.setup.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(Failure, "Another setup"):
                provision(self.args)

    def test_download_process_has_online_environment(self):
        with patch("delphi_code.setup.subprocess.run") as run:
            run.return_value.returncode = 0
            download(self.root / "download")
            command = run.call_args.args[0]
            self.assertTrue(command[1].endswith("download.py"))
            self.assertNotIn("HF_HUB_OFFLINE", run.call_args.kwargs["env"])
            self.assertEqual(os.environ["HF_HUB_OFFLINE"], "1")

    def test_download_process_failure_has_recovery(self):
        with patch("delphi_code.setup.subprocess.run") as run:
            run.return_value.returncode = 1
            with self.assertRaisesRegex(Failure, "delphi-code setup --from"):
                download(self.root / "download")

    def test_linked_assets_rejected(self):
        asset = self.source / "model.safetensors"
        asset.unlink()
        asset.symlink_to(self.root / "external")
        with self.assertRaises(Failure):
            verify_assets(self.source)

    def test_os_metadata_accepted(self):
        (self.source / ".DS_Store").write_bytes(b"finder")
        verify_assets(self.source)
        (self.source / "stray.bin").write_bytes(b"extra")
        with self.assertRaisesRegex(Failure, "Unexpected model asset"):
            verify_assets(self.source)

    def test_import_keeps_network_guard(self):
        with self.assertRaisesRegex(RuntimeError, "Offline policy"):
            socket.getaddrinfo("example.com", 443)


class Storage(unittest.TestCase):
    def test_platform_defaults(self):
        with patch("delphi_code.paths.Path.home", return_value=Path("/home/test")), patch("delphi_code.paths.sys.platform", "darwin"):
            self.assertEqual(data_directory(), Path("/home/test/Library/Application Support/delphi-code"))
        with patch("delphi_code.paths.sys.platform", "linux"), patch.dict(os.environ, {"XDG_DATA_HOME": "/tmp/custom-data"}):
            self.assertEqual(data_directory(), Path("/tmp/custom-data/delphi-code").resolve())

