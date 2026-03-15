"""Document file filter."""

import os
from datetime import datetime
from pathlib import Path
from typing import Optional

import yaml

from . import BaseFilter


# Magic byte signatures for verification
MAGIC_BYTES = {
    ".pdf": b"%PDF-",
    ".docx": b"PK\x03\x04",
    ".xlsx": b"PK\x03\x04",
    ".pptx": b"PK\x03\x04",
    ".odt": b"PK\x03\x04",
    ".ods": b"PK\x03\x04",
    ".odp": b"PK\x03\x04",
    ".doc": b"\xd0\xcf\x11\xe0",  # OLE2 compound document
    ".xls": b"\xd0\xcf\x11\xe0",
    ".ppt": b"\xd0\xcf\x11\xe0",
    ".rtf": b"{\\rtf",
}


class DocumentFilter(BaseFilter):
    name = "documents"
    description = "Find document files (PDF, Office, OpenDocument, etc.)"

    def __init__(self):
        super().__init__()
        self._enabled = True
        self._extensions: set[str] = set()
        self._min_size: int = 100
        self._max_size: int | None = None
        self._verify_magic: bool = True

    def load_config(self, config_path: Path) -> None:
        config_file = config_path / "documents.yaml"
        if not config_file.exists():
            # Defaults
            self._extensions = {
                ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
                ".odt", ".ods", ".odp", ".rtf", ".csv", ".txt",
                ".pages", ".numbers", ".key",
            }
            return

        with open(config_file) as f:
            cfg = yaml.safe_load(f) or {}

        self._enabled = cfg.get("enabled", True)
        self._extensions = set(cfg.get("extensions", []))
        self._min_size = cfg.get("min_size_bytes", 100)
        self._max_size = cfg.get("max_size_bytes")
        self._verify_magic = cfg.get("verify_magic_bytes", True)

    def match(self, file_path: Path, file_stat: os.stat_result) -> Optional[dict]:
        ext = file_path.suffix.lower()
        if ext not in self._extensions:
            return None

        size = file_stat.st_size
        if size < self._min_size:
            return None
        if self._max_size is not None and size > self._max_size:
            return None

        # Optional magic byte verification
        verified = None
        if self._verify_magic and ext in MAGIC_BYTES:
            try:
                with open(file_path, "rb") as f:
                    header = f.read(8)
                verified = header.startswith(MAGIC_BYTES[ext])
            except (OSError, PermissionError):
                verified = None

        mtime = datetime.fromtimestamp(file_stat.st_mtime).isoformat()

        return self.record_match({
            "path": str(file_path),
            "type": ext.lstrip(".").upper(),
            "size": size,
            "modified": mtime,
            "verified": verified,
        })

    def summary(self) -> str:
        if not self._matches:
            return "No documents found."
        by_type: dict[str, int] = {}
        for m in self._matches:
            t = m["type"]
            by_type[t] = by_type.get(t, 0) + 1
        breakdown = ", ".join(f"{v} {k}" for k, v in sorted(by_type.items()))
        return f"Found {self._match_count} documents: {breakdown}"
