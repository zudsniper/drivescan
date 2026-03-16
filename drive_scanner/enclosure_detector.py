"""Detect external enclosures and infer their RAID configurations."""

import json
import platform
import subprocess
from dataclasses import dataclass, field


@dataclass
class EnclosureInfo:
    vendor: str
    model: str
    connection: str  # "USB", "Thunderbolt", "eSATA", etc.
    inferred_raid: str | None = None  # e.g. "RAID5", "BeyondRAID", "SHR"
    confidence: str = "low"  # "low", "medium", "high"
    logical_volumes: list[str] = field(default_factory=list)
    reasoning: str = ""


# Known enclosure vendors and their default RAID types
_VENDOR_DEFAULTS: dict[str, tuple[str, str]] = {
    "drobo": ("BeyondRAID", "high"),
    "synology": ("SHR", "medium"),
    "terramaster": ("RAID5", "medium"),
    "owc": ("SoftRAID", "medium"),
    "qnap": ("RAID5", "medium"),
    "buffalo": ("RAID5", "medium"),
    "lacie": ("RAID5", "medium"),
    "g-technology": ("RAID5", "medium"),
    "promise": ("RAID5", "medium"),
    "mediasonic": ("JBOD", "low"),
    "sabrent": ("JBOD", "low"),
    "orico": ("JBOD", "low"),
    "icy dock": ("JBOD", "low"),
    "startech": ("JBOD", "low"),
}


def detect_enclosures() -> list[EnclosureInfo]:
    """Detect external enclosures on the current platform."""
    system = platform.system()
    if system == "Darwin":
        return _detect_macos_enclosures()
    elif system == "Linux":
        return _detect_linux_enclosures()
    return []


def _detect_macos_enclosures() -> list[EnclosureInfo]:
    """Detect enclosures via macOS system_profiler."""
    enclosures: list[EnclosureInfo] = []

    # Check USB devices
    try:
        result = subprocess.run(
            ["system_profiler", "SPUSBDataType", "-json"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            _walk_macos_devices(data, "USB", enclosures)
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError, json.JSONDecodeError):
        pass

    # Check Thunderbolt devices
    try:
        result = subprocess.run(
            ["system_profiler", "SPThunderboltDataType", "-json"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            _walk_macos_devices(data, "Thunderbolt", enclosures)
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError, json.JSONDecodeError):
        pass

    # Cross-reference with diskutil for logical volumes
    for enc in enclosures:
        _enrich_macos_enclosure(enc)

    return enclosures


def _walk_macos_devices(data: dict | list, connection: str, enclosures: list[EnclosureInfo]) -> None:
    """Recursively walk system_profiler JSON to find storage enclosures."""
    if isinstance(data, list):
        for item in data:
            _walk_macos_devices(item, connection, enclosures)
        return

    if not isinstance(data, dict):
        return

    for key, value in data.items():
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    vendor = item.get("manufacturer", item.get("_name", ""))
                    model = item.get("_name", "")
                    # Check if this looks like a storage enclosure (not bare disks)
                    combined_lower = (vendor + " " + model).lower()
                    is_storage = any(
                        kw in combined_lower
                        for kw in ("storage", "raid", "nas", "enclosure",
                                   "drobo", "synology", "terramaster", "owc",
                                   "qnap", "buffalo", "lacie", "g-tech",
                                   "promise", "mediasonic", "sabrent", "orico",
                                   "icy dock", "startech")
                    )
                    if is_storage:
                        enc = EnclosureInfo(
                            vendor=vendor,
                            model=model,
                            connection=connection,
                        )
                        _identify_vendor(enc)
                        enclosures.append(enc)
                    # Recurse into nested items
                    _walk_macos_devices(item, connection, enclosures)


def _enrich_macos_enclosure(enclosure: EnclosureInfo) -> None:
    """Add logical volume info from diskutil."""
    try:
        result = subprocess.run(
            ["diskutil", "list"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            # Look for external volumes
            for line in result.stdout.splitlines():
                if "external" in line.lower() or "physical" in line.lower():
                    enclosure.logical_volumes.append(line.strip())
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass


def _detect_linux_enclosures() -> list[EnclosureInfo]:
    """Detect enclosures via lsusb and lsblk."""
    enclosures: list[EnclosureInfo] = []

    # Check USB storage devices
    try:
        result = subprocess.run(
            ["lsusb"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                lower = line.lower()
                if any(kw in lower for kw in ("storage", "mass storage", "uas")):
                    # Extract vendor/product from lsusb line
                    # Format: Bus 001 Device 002: ID 1234:5678 Vendor Product
                    parts = line.split(":", 2)
                    if len(parts) >= 3:
                        desc = parts[2].strip()
                        # Remove ID prefix
                        id_parts = desc.split(" ", 1)
                        name = id_parts[1] if len(id_parts) > 1 else desc
                        enc = EnclosureInfo(
                            vendor=name.split()[0] if name else "Unknown",
                            model=name,
                            connection="USB",
                        )
                        _identify_vendor(enc)
                        enclosures.append(enc)
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass

    # Check for block devices with USB transport
    try:
        result = subprocess.run(
            ["lsblk", "-J", "-o", "NAME,TRAN,VENDOR,MODEL,SIZE"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            for dev in data.get("blockdevices", []):
                tran = (dev.get("tran") or "").lower()
                if tran in ("usb", "thunderbolt", "sata"):
                    vendor = (dev.get("vendor") or "").strip()
                    model = (dev.get("model") or "").strip()
                    combined = f"{vendor} {model}".strip()
                    if combined and not any(e.model == combined for e in enclosures):
                        enc = EnclosureInfo(
                            vendor=vendor or "Unknown",
                            model=combined,
                            connection=tran.upper(),
                        )
                        _identify_vendor(enc)
                        enclosures.append(enc)
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError, json.JSONDecodeError):
        pass

    return enclosures


def _identify_vendor(enclosure: EnclosureInfo) -> None:
    """Look up vendor defaults and infer RAID type."""
    combined = f"{enclosure.vendor} {enclosure.model}".lower()
    for vendor_key, (raid_type, confidence) in _VENDOR_DEFAULTS.items():
        if vendor_key in combined:
            enclosure.inferred_raid = raid_type
            enclosure.confidence = confidence
            enclosure.reasoning = f"Identified as {vendor_key} product; default configuration is {raid_type}"
            return

    # If multi-bay keywords found but no known vendor
    multi_bay_keywords = ("4-bay", "5-bay", "2-bay", "8-bay", "multi", "enclosure", "raid")
    if any(kw in combined for kw in multi_bay_keywords):
        enclosure.inferred_raid = "Unknown RAID"
        enclosure.confidence = "low"
        enclosure.reasoning = "Multi-bay enclosure detected but vendor RAID defaults unknown"


def _infer_enclosure_raid(enclosure: EnclosureInfo, physical_count: int, logical_count: int) -> None:
    """Refine RAID inference based on physical vs logical disk counts."""
    if enclosure.inferred_raid:
        return  # Already identified by vendor

    if physical_count > 1 and logical_count == 1:
        enclosure.inferred_raid = "RAID (type unknown)"
        enclosure.confidence = "medium"
        enclosure.reasoning = (
            f"{physical_count} physical disks presenting as {logical_count} logical volume; "
            "likely hardware RAID"
        )
    elif physical_count > 1 and logical_count == physical_count:
        enclosure.inferred_raid = "JBOD"
        enclosure.confidence = "medium"
        enclosure.reasoning = (
            f"{physical_count} physical disks presenting as {logical_count} logical volumes; "
            "likely JBOD/passthrough"
        )
