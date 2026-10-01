from enum import IntEnum


class ExitCode(IntEnum):
    USAGE = 2
    RUNTIME_ASSETS = 3
    INDEX_STATE = 4
    OPERATION = 5


class Failure(Exception):
    def __init__(self, code: str, message: str, exit_code: ExitCode, data: dict | None = None):
        super().__init__(message)
        self.code = code
        self.exit_code = exit_code
        self.data = data

    @classmethod
    def from_exception(cls, exc: BaseException) -> "Failure":
        if isinstance(exc, Failure):
            return exc
        if isinstance(exc, ModuleNotFoundError):
            return cls("dependency_missing", str(exc), ExitCode.RUNTIME_ASSETS)
        return cls("runtime_error", str(exc), ExitCode.OPERATION)

    def to_json(self) -> dict:
        return {"code": self.code, "message": str(self)}
