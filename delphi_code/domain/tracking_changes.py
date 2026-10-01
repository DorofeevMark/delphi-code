from typing import NamedTuple


class TrackingChanges(NamedTuple):
    added: list[str]
    removed: list[str]

    @classmethod
    def from_selection(cls, repositories, chosen_keys, tracked_keys):
        return cls(
            added=[
                repository.key
                for repository in repositories
                if repository.key in chosen_keys and repository.key not in tracked_keys
            ],
            removed=[
                repository.key
                for repository in repositories
                if repository.key not in chosen_keys and repository.key in tracked_keys
            ],
        )

    def __bool__(self):
        return bool(self.added or self.removed)
