"""Detect software and hardware RAID arrays."""

import platform
import re
import subprocess
from dataclasses import dataclass, field


@dataclass
class RAIDMember:
    device: str
    role: str  # e.g. "active", "spare", "faulty"
    status: str  # e.g. "online", "offline", "rebuilding"


@dataclass
class RAIDArray:
    name: str
    raid_level: str  # e.g. "raid0", "raid1", "raid5", "raid6", "mirror", "stripe"
    members: list[RAIDMember] = field(default_factory=list)
    status: str = "unknown"  # "healthy", "degraded", "failed", "rebuilding"
    total_size: str | None = None
    usable_capacity: str | None = None
    source: str = "unknown"  # "mdadm", "lvm", "hardware", "appleraid", "apfs"
    mount_point: str | None = None


def detect_raid_arrays() -> list[RAIDArray]:
    """Detect RAID arrays on the current platform."""
    system = platform.system()
    arrays: list[RAIDArray] = []

    if system == "Linux":
        arrays.extend(_detect_linux_mdadm())
        arrays.extend(_detect_linux_lvm())
        arrays.extend(_detect_linux_hardware_raid())
    elif system == "Darwin":
        arrays.extend(_detect_macos_raid())

    return arrays


def _detect_linux_mdadm() -> list[RAIDArray]:
    """Parse /proc/mdstat for Linux software RAID arrays."""
    arrays: list[RAIDArray] = []
    try:
        with open("/proc/mdstat") as f:
            content = f.read()
    except (FileNotFoundError, PermissionError, OSError):
        return arrays

    # Parse mdstat entries like:
    # md0 : active raid1 sda1[0] sdb1[1]
    #       1953513472 blocks super 1.2 [2/2] [UU]
    current_name = None
    current_level = None
    current_members: list[RAIDMember] = []
    current_status = "healthy"

    for line in content.splitlines():
        # Match array definition line
        arr_match = re.match(r'^(md\d+)\s*:\s*(\w+)\s+(raid\d+|linear)\s+(.*)', line)
        if arr_match:
            # Save previous array if any
            if current_name:
                arrays.append(RAIDArray(
                    name=current_name,
                    raid_level=current_level or "unknown",
                    members=current_members,
                    status=current_status,
                    source="mdadm",
                ))

            current_name = arr_match.group(1)
            active_state = arr_match.group(2)
            current_level = arr_match.group(3)
            member_str = arr_match.group(4)
            current_members = []
            current_status = "healthy" if active_state == "active" else "inactive"

            # Parse members: sda1[0] sdb1[1](F) sdc1[2](S)
            for m in re.finditer(r'(\w+)\[(\d+)\](\([A-Z]*\))?', member_str):
                dev = m.group(1)
                flags = m.group(3) or ""
                if "(F)" in flags:
                    role, status = "faulty", "offline"
                    current_status = "degraded"
                elif "(S)" in flags:
                    role, status = "spare", "online"
                else:
                    role, status = "active", "online"
                current_members.append(RAIDMember(device=f"/dev/{dev}", role=role, status=status))

        # Match status line like [2/1] [U_]
        status_match = re.search(r'\[(\d+)/(\d+)\]\s*\[([U_]+)\]', line)
        if status_match and current_name:
            total = int(status_match.group(1))
            active = int(status_match.group(2))
            if active < total:
                current_status = "degraded"

        # Match rebuild line
        if current_name and "recovery" in line.lower():
            current_status = "rebuilding"

    # Save last array
    if current_name:
        arrays.append(RAIDArray(
            name=current_name,
            raid_level=current_level or "unknown",
            members=current_members,
            status=current_status,
            source="mdadm",
        ))

    # Try to get sizes via mdadm --detail
    for arr in arrays:
        try:
            result = subprocess.run(
                ["mdadm", "--detail", f"/dev/{arr.name}"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                for detail_line in result.stdout.splitlines():
                    if "Array Size" in detail_line:
                        size_match = re.search(r'(\d+)\s*KB', detail_line)
                        if size_match:
                            kb = int(size_match.group(1))
                            arr.usable_capacity = _format_size(kb * 1024)
                    elif "Used Dev Size" in detail_line:
                        size_match = re.search(r'(\d+)\s*KB', detail_line)
                        if size_match:
                            kb = int(size_match.group(1))
                            arr.total_size = _format_size(kb * 1024 * len(arr.members))
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            pass

    return arrays


def _detect_linux_lvm() -> list[RAIDArray]:
    """Detect LVM mirror/stripe layouts."""
    arrays: list[RAIDArray] = []
    try:
        result = subprocess.run(
            ["lvs", "--noheadings", "-o", "lv_name,vg_name,lv_attr,lv_size,seg_type,devices"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            return arrays
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return arrays

    for line in result.stdout.strip().splitlines():
        parts = line.split()
        if len(parts) < 5:
            continue
        lv_name, vg_name, _attr, lv_size, seg_type = parts[:5]
        devices_str = parts[5] if len(parts) > 5 else ""

        # Only interested in mirror/raid/stripe layouts
        if seg_type not in ("raid1", "raid5", "raid6", "raid10", "mirror", "striped"):
            continue

        level_map = {
            "mirror": "raid1", "striped": "raid0",
            "raid1": "raid1", "raid5": "raid5",
            "raid6": "raid6", "raid10": "raid10",
        }
        raid_level = level_map.get(seg_type, seg_type)

        members = []
        for dev in re.findall(r'/dev/\S+', devices_str):
            members.append(RAIDMember(device=dev, role="active", status="online"))

        arrays.append(RAIDArray(
            name=f"{vg_name}/{lv_name}",
            raid_level=raid_level,
            members=members,
            status="healthy",
            total_size=lv_size,
            source="lvm",
        ))

    return arrays


def _detect_linux_hardware_raid() -> list[RAIDArray]:
    """Check for hardware RAID controllers via /sys."""
    arrays: list[RAIDArray] = []
    raid_vendors = {"LSI", "Adaptec", "Dell", "HP", "MegaRAID", "3ware", "Areca"}

    try:
        result = subprocess.run(
            ["lsblk", "-J", "-o", "NAME,SIZE,TYPE,VENDOR,MODEL"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            return arrays

        import json
        data = json.loads(result.stdout)
        for device in data.get("blockdevices", []):
            vendor = (device.get("vendor") or "").strip()
            model = (device.get("model") or "").strip()
            if device.get("type") == "disk" and any(rv.lower() in (vendor + model).lower() for rv in raid_vendors):
                arrays.append(RAIDArray(
                    name=device.get("name", "unknown"),
                    raid_level="hardware",
                    status="healthy",
                    total_size=device.get("size"),
                    source="hardware",
                ))
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError, ValueError):
        pass

    return arrays


def _detect_macos_raid() -> list[RAIDArray]:
    """Detect macOS AppleRAID sets and APFS multi-disk volume groups."""
    arrays: list[RAIDArray] = []

    # Check AppleRAID
    try:
        result = subprocess.run(
            ["diskutil", "appleRAID", "list"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and "No RAID" not in result.stdout:
            arrays.extend(_parse_appleraid_output(result.stdout))
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass

    # Check APFS container/volume group spanning multiple disks
    try:
        result = subprocess.run(
            ["diskutil", "apfs", "list", "-plist"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            import plistlib
            try:
                data = plistlib.loads(result.stdout.encode())
                for container in data.get("Containers", []):
                    stores = container.get("PhysicalStores", [])
                    if len(stores) > 1:
                        members = []
                        for store in stores:
                            dev = store.get("DeviceIdentifier", "unknown")
                            members.append(RAIDMember(
                                device=f"/dev/{dev}",
                                role="active",
                                status="online",
                            ))
                        ref = container.get("ContainerReference", "unknown")
                        arrays.append(RAIDArray(
                            name=f"APFS Container {ref}",
                            raid_level="apfs-fusion",
                            members=members,
                            status="healthy",
                            source="apfs",
                        ))
            except (ValueError, KeyError):
                pass
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass

    return arrays


def _parse_appleraid_output(output: str) -> list[RAIDArray]:
    """Parse diskutil appleRAID list output."""
    arrays: list[RAIDArray] = []
    current: dict | None = None

    for line in output.splitlines():
        line = line.strip()
        if line.startswith("Name:"):
            if current:
                arrays.append(_build_appleraid_array(current))
            current = {"name": line.split(":", 1)[1].strip(), "members": [], "level": "unknown", "status": "unknown"}
        elif current:
            if line.startswith("RAID Level:"):
                level_str = line.split(":", 1)[1].strip().lower()
                if "mirror" in level_str:
                    current["level"] = "raid1"
                elif "stripe" in level_str:
                    current["level"] = "raid0"
                elif "concat" in level_str:
                    current["level"] = "jbod"
                else:
                    current["level"] = level_str
            elif line.startswith("Status:"):
                status_str = line.split(":", 1)[1].strip().lower()
                if "online" in status_str:
                    current["status"] = "healthy"
                elif "degraded" in status_str:
                    current["status"] = "degraded"
                elif "failed" in status_str:
                    current["status"] = "failed"
                else:
                    current["status"] = status_str
            elif re.match(r'^\d+\)', line):
                # Member line like: 0) ... disk2s2
                dev_match = re.search(r'(disk\d+s?\d*)', line)
                if dev_match:
                    status = "online"
                    if "Failed" in line:
                        status = "offline"
                    elif "Rebuilding" in line:
                        status = "rebuilding"
                    current["members"].append(RAIDMember(
                        device=f"/dev/{dev_match.group(1)}",
                        role="active",
                        status=status,
                    ))

    if current:
        arrays.append(_build_appleraid_array(current))

    return arrays


def _build_appleraid_array(data: dict) -> RAIDArray:
    return RAIDArray(
        name=data["name"],
        raid_level=data["level"],
        members=data["members"],
        status=data["status"],
        source="appleraid",
    )


def _format_size(bytes_val: int) -> str:
    """Format bytes to human-readable size."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(bytes_val) < 1024:
            return f"{bytes_val:.1f} {unit}"
        bytes_val /= 1024
    return f"{bytes_val:.1f} PB"
