"""Filter base class and auto-discovery mechanism."""

import importlib
import inspect
import os
import pkgutil
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional


class BaseFilter(ABC):
    """Base class for all file filters."""

    name: str = ""
    description: str = ""

    def __init__(self):
        self._match_count = 0
        self._matches: list[dict] = []

    @abstractmethod
    def load_config(self, config_path: Path) -> None:
        """Load configuration from a YAML file."""
        ...

    @abstractmethod
    def match(self, file_path: Path, file_stat: os.stat_result) -> Optional[dict]:
        """Check if a file matches this filter.

        Returns a dict with match details if matched, None otherwise.
        """
        ...

    @abstractmethod
    def summary(self) -> str:
        """Return a summary string of matches found."""
        ...

    def record_match(self, details: dict) -> dict:
        """Record a match and return the details."""
        self._match_count += 1
        self._matches.append(details)
        return details

    @property
    def match_count(self) -> int:
        return self._match_count

    @property
    def matches(self) -> list[dict]:
        return list(self._matches)

    @property
    def enabled(self) -> bool:
        return getattr(self, "_enabled", True)


def discover_filters() -> dict[str, type[BaseFilter]]:
    """Auto-discover all filter classes in this package."""
    filters: dict[str, type[BaseFilter]] = {}
    package_dir = Path(__file__).parent

    for module_info in pkgutil.iter_modules([str(package_dir)]):
        if module_info.name.startswith("_"):
            continue
        module = importlib.import_module(f".{module_info.name}", package=__package__)
        for _name, obj in inspect.getmembers(module, inspect.isclass):
            if issubclass(obj, BaseFilter) and obj is not BaseFilter and obj.name:
                filters[obj.name] = obj

    return filters
