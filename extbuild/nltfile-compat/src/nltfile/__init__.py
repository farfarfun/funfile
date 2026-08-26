"""Compatibility namespace for the renamed funfile package."""

import warnings

warnings.warn("nltfile was renamed to funfile", DeprecationWarning, stacklevel=2)

from funfile import *  # noqa: E402,F401,F403
from funfile import __all__, __path__  # noqa: E402,F401
