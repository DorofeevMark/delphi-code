from enum import StrEnum
from pathlib import Path

from .errors import Failure


class Stage(StrEnum):
    CHECKING_FOR_CHANGES = "checking for changes"
    CLONING = "cloning"
    READING_FILES = "reading files"
    EMBEDDING = "embedding"
    SAVING = "saving"
    COPYING_MODEL = "copying model"
    DOWNLOADING_MODEL = "downloading model"
    VERIFYING_MODEL = "verifying model"
    CHECKING_INSTALLATION = "checking installation"


class Progress:
    def owners_listing_started(self, host_name: str, owner_noun: str):
        pass

    def repositories_listing_started(self, host_name: str, owner: str):
        pass

    def listing_finished(self):
        pass

    def repository_started(self, key: str, position: int, count: int):
        pass

    def model_setup_started(self, destination: Path):
        pass

    def stage_started(self, stage: Stage):
        pass

    def files_embedded(self, done: int, total: int):
        pass

    def repository_finished(self, result: dict):
        pass

    def repository_failed(self, failure: Failure):
        pass

    def model_setup_finished(self, reused: bool):
        pass


SILENT = Progress()
