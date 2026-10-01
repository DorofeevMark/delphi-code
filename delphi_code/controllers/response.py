from typing import NamedTuple

from ..ui.text_layout import TextView


class ControllerResponse(NamedTuple):
    data: dict
    views: list[TextView]
