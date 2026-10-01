import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from delphi_code.cli import arguments


class ModelDefaults(unittest.TestCase):
    def test_override_precedence(self):
        with patch.dict(os.environ, {"DELPHI_CODE_MODEL": "/configured/model"}):
            with patch.object(sys, "argv", ["delphi-code", "index"]):
                self.assertEqual(arguments().model, "/configured/model")
            with patch.object(sys, "argv", ["delphi-code", "index", "--model", "/explicit/model"]):
                self.assertEqual(arguments().model, "/explicit/model")

    def test_cli_uses_default_without_environment(self):
        with (
            patch.dict(os.environ, {}, clear=True),
            patch("delphi_code.cli.model_directory", return_value=Path("/local/default")),
            patch.object(sys, "argv", ["delphi-code", "search", "query"]),
        ):
            self.assertEqual(arguments().model, Path("/local/default"))


class ModelIdentity(unittest.TestCase):
    def test_incidental_files_do_not_change_identity(self):
        import json
        import tempfile

        from delphi_code.infrastructure.model import LocalModel

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "modules.json").write_text(
                json.dumps([{"type": "sentence_transformers.models.Normalize", "path": ""}])
            )
            (root / "model.safetensors").write_bytes(b"weights")
            before = LocalModel.inspect(root).sha256
            (root / ".DS_Store").write_bytes(b"finder")
            (root / "._model.safetensors").write_bytes(b"resource fork")
            (root / "README.md").write_text("model card")
            (root / "LICENSE").write_text("license")
            self.assertEqual(LocalModel.inspect(root).sha256, before)
            (root / "model.safetensors").write_bytes(b"changed")
            self.assertNotEqual(LocalModel.inspect(root).sha256, before)
