from collections import namedtuple

from .ml import BaseModel

STFTParams = namedtuple(
    "STFTParams",
    ["window_length", "hop_length", "window_type", "match_stride", "padding_type"],
)
STFTParams.__new__.__defaults__ = (None, None, None, None, None)


class AudioSignal:
    """
    Minimal placeholder to satisfy Fish Speech's import path.

    The current Fish runtime only inherits from BaseModel and does not
    instantiate AudioSignal during inference.
    """

    pass
