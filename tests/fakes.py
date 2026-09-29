from pathlib import Path
from unittest.mock import patch

import numpy as np

from delphi_code.model import LocalModel


class FakeModel:
    def __init__(self, directory: Path, sha256: str = "same-model", vector=(1, 0)):
        self.directory = directory
        self.sha256 = sha256
        self.vector = vector
        self.embedded: list[list[str]] = []

    @property
    def dimensions(self):
        return len(self.vector)

    def embed(self, texts):
        self.embedded.append(texts)
        return np.array([self.vector] * len(texts), dtype=np.float32)


def use_fake_model(test_case, directory: Path, **options) -> FakeModel:
    model = FakeModel(directory, **options)
    test_case.enterContext(patch.object(LocalModel, "inspect", return_value=model))
    return model
