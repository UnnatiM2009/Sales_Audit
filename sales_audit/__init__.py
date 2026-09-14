"""Sales Process Audit — a config-driven dealership sales audit engine."""

__version__ = "1.0.0"

from .audit import run_audit
from .config import Config
from .loaders import load_all

__all__ = ["run_audit", "Config", "load_all", "__version__"]
