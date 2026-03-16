"""Data recovery guidance based on RAID and enclosure status."""

from dataclasses import dataclass, field

from .enclosure_detector import EnclosureInfo
from .raid_detector import RAIDArray


@dataclass
class RecoveryStep:
    order: int
    title: str
    description: str
    command: str | None = None
    warning: str | None = None
    tools: list[str] = field(default_factory=list)


@dataclass
class RecoveryPlan:
    scenario: str
    severity: str  # "info", "warning", "critical"
    steps: list[RecoveryStep] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    recommended_tools: list[str] = field(default_factory=list)


def generate_recovery_plans(
    raid_arrays: list[RAIDArray],
    enclosures: list[EnclosureInfo],
    drives: list | None = None,
) -> list[RecoveryPlan]:
    """Generate recovery plans based on detected arrays and enclosures."""
    plans: list[RecoveryPlan] = []

    for array in raid_arrays:
        plan = _plan_for_raid_array(array)
        if plan:
            plans.append(plan)

    for enclosure in enclosures:
        plan = _plan_for_enclosure(enclosure)
        if plan:
            plans.append(plan)

    # Always include general warnings
    plans.append(_general_recovery_warnings())

    return plans


def _plan_for_raid_array(array: RAIDArray) -> RecoveryPlan | None:
    """Generate recovery plan based on RAID level and status."""
    level = array.raid_level.lower()
    status = array.status.lower()

    if status == "healthy":
        return RecoveryPlan(
            scenario=f"{array.name} ({array.raid_level}) - Healthy",
            severity="info",
            steps=[
                RecoveryStep(
                    order=1,
                    title="Verify backups",
                    description="Even healthy RAID is not a backup. Verify your backup strategy covers this array.",
                ),
                RecoveryStep(
                    order=2,
                    title="Monitor SMART data",
                    description="Regularly check SMART attributes for early warning signs of disk failure.",
                    command="smartctl -a /dev/<member_device>",
                    tools=["smartmontools"],
                ),
                RecoveryStep(
                    order=3,
                    title="Check array consistency",
                    description="Periodically verify array integrity.",
                    command=f"mdadm --detail /dev/{array.name}" if array.source == "mdadm" else None,
                ),
            ],
            warnings=["RAID is not a substitute for backups"],
            recommended_tools=["smartmontools"],
        )

    if "raid1" in level or "mirror" in level:
        if status == "degraded":
            return RecoveryPlan(
                scenario=f"{array.name} (RAID 1 Mirror) - Degraded",
                severity="warning",
                steps=[
                    RecoveryStep(
                        order=1,
                        title="Identify failed member",
                        description="Determine which disk has failed or been removed.",
                        command=f"mdadm --detail /dev/{array.name}" if array.source == "mdadm" else None,
                    ),
                    RecoveryStep(
                        order=2,
                        title="Replace failed disk",
                        description="Physically replace the failed disk with one of equal or greater capacity.",
                        warning="Ensure the array is not under I/O load during replacement if possible.",
                    ),
                    RecoveryStep(
                        order=3,
                        title="Add new disk to array",
                        description="Add the replacement disk and initiate rebuild.",
                        command=f"mdadm /dev/{array.name} --add /dev/<new_device>" if array.source == "mdadm" else None,
                    ),
                    RecoveryStep(
                        order=4,
                        title="Monitor rebuild",
                        description="Watch rebuild progress. The array remains accessible during rebuild.",
                        command="watch cat /proc/mdstat",
                    ),
                ],
                warnings=[
                    "Data is NOT protected during degraded operation",
                    "A second disk failure will result in data loss",
                    "Back up critical data immediately if possible",
                ],
                recommended_tools=["mdadm", "smartmontools"],
            )

    if "raid5" in level:
        if status == "degraded":
            faulty_count = sum(1 for m in array.members if m.status == "offline")
            if faulty_count <= 1:
                return RecoveryPlan(
                    scenario=f"{array.name} (RAID 5) - Degraded (single failure)",
                    severity="warning",
                    steps=[
                        RecoveryStep(
                            order=1,
                            title="Identify failed disk",
                            description="Determine which member has failed.",
                            command=f"mdadm --detail /dev/{array.name}" if array.source == "mdadm" else None,
                        ),
                        RecoveryStep(
                            order=2,
                            title="Back up critical data NOW",
                            description="Copy critical data off the array immediately. A second failure means total loss.",
                            warning="URE (Unrecoverable Read Error) risk increases significantly during rebuild.",
                        ),
                        RecoveryStep(
                            order=3,
                            title="Replace and rebuild",
                            description="Hot-swap the failed disk and initiate rebuild.",
                            command=f"mdadm /dev/{array.name} --add /dev/<new_device>" if array.source == "mdadm" else None,
                            warning="Rebuild can take hours/days for large arrays. URE risk is real.",
                        ),
                        RecoveryStep(
                            order=4,
                            title="Monitor rebuild closely",
                            description="Watch for errors during rebuild. Any additional failure is catastrophic.",
                            command="watch cat /proc/mdstat",
                        ),
                    ],
                    warnings=[
                        "RAID 5 with one failure has ZERO redundancy",
                        "URE during rebuild can cause complete array failure",
                        "For arrays > 2TB, consider professional recovery",
                        "Do NOT run fsck on degraded RAID 5",
                    ],
                    recommended_tools=["mdadm", "smartmontools", "ddrescue"],
                )
            else:
                return _plan_multi_failure(array, "RAID 5")

        if status == "failed":
            return _plan_multi_failure(array, "RAID 5")

    if "raid6" in level:
        if status == "degraded":
            faulty_count = sum(1 for m in array.members if m.status == "offline")
            if faulty_count <= 2:
                return RecoveryPlan(
                    scenario=f"{array.name} (RAID 6) - Degraded ({faulty_count} failure(s))",
                    severity="warning" if faulty_count == 1 else "critical",
                    steps=[
                        RecoveryStep(
                            order=1,
                            title="Identify failed disk(s)",
                            description=f"{faulty_count} disk(s) have failed.",
                        ),
                        RecoveryStep(
                            order=2,
                            title="Replace and rebuild",
                            description="Replace failed disks one at a time and rebuild.",
                        ),
                    ],
                    warnings=[
                        f"RAID 6 can tolerate 2 failures; currently at {faulty_count}",
                        "Back up data immediately if at 2 failures",
                    ],
                    recommended_tools=["mdadm", "smartmontools"],
                )

    if "raid0" in level or "stripe" in level:
        if status in ("degraded", "failed"):
            return RecoveryPlan(
                scenario=f"{array.name} (RAID 0) - Failed",
                severity="critical",
                steps=[
                    RecoveryStep(
                        order=1,
                        title="Stop all I/O immediately",
                        description="Do not attempt to write to or repair the array.",
                        warning="Any write operation may overwrite recoverable data.",
                    ),
                    RecoveryStep(
                        order=2,
                        title="Image individual disks",
                        description="Create bit-for-bit copies of each surviving member disk.",
                        command="ddrescue /dev/<member> /path/to/image.img /path/to/logfile",
                        tools=["ddrescue"],
                    ),
                    RecoveryStep(
                        order=3,
                        title="Attempt file carving",
                        description="Use data recovery tools to scan disk images for recoverable files.",
                        tools=["testdisk", "photorec"],
                    ),
                    RecoveryStep(
                        order=4,
                        title="Consider professional recovery",
                        description="RAID 0 failure often requires professional data recovery services.",
                    ),
                ],
                warnings=[
                    "RAID 0 has NO redundancy - any disk failure means data loss",
                    "Partial recovery may be possible via file carving",
                    "Do NOT attempt to rebuild - it will fail",
                ],
                recommended_tools=["ddrescue", "testdisk", "photorec"],
            )

    return None


def _plan_multi_failure(array: RAIDArray, raid_type: str) -> RecoveryPlan:
    """Plan for multi-disk failure in redundant RAID."""
    return RecoveryPlan(
        scenario=f"{array.name} ({raid_type}) - Multiple Failures",
        severity="critical",
        steps=[
            RecoveryStep(
                order=1,
                title="STOP - Do not modify the array",
                description="Power down if possible. Do not attempt rebuild or fsck.",
                warning="Any modification risks permanent data loss.",
            ),
            RecoveryStep(
                order=2,
                title="Image each member disk",
                description="Create bit-for-bit images of every member disk before any recovery attempt.",
                command="ddrescue /dev/<member> /path/to/image.img /path/to/logfile",
                tools=["ddrescue"],
            ),
            RecoveryStep(
                order=3,
                title="Contact professional recovery service",
                description="Multi-disk RAID failure typically requires professional recovery. "
                            "Services like DriveSavers, Ontrack, or Gillware specialize in RAID recovery.",
            ),
            RecoveryStep(
                order=4,
                title="Attempt software reassembly (advanced)",
                description="If professional recovery is not an option, attempt manual reassembly from images.",
                command="mdadm --assemble --force --run /dev/md0 /dev/image1 /dev/image2 ...",
                warning="Only attempt on COPIES of disk images, never on original disks.",
                tools=["mdadm", "testdisk", "photorec"],
            ),
        ],
        warnings=[
            "Multi-disk failure in RAID is a critical data loss event",
            "Professional recovery has the highest chance of success",
            "Always work from disk images, never from original media",
            "Do NOT run fsck on a degraded/failed array",
        ],
        recommended_tools=["ddrescue", "testdisk", "photorec"],
    )


def _plan_for_enclosure(enclosure: EnclosureInfo) -> RecoveryPlan | None:
    """Generate recovery guidance for detected enclosures."""
    if not enclosure.inferred_raid:
        return None

    vendor_lower = enclosure.vendor.lower()

    if "drobo" in vendor_lower:
        return RecoveryPlan(
            scenario=f"Drobo Enclosure ({enclosure.model})",
            severity="info",
            steps=[
                RecoveryStep(
                    order=1,
                    title="Use Drobo Dashboard",
                    description="Drobo's BeyondRAID is proprietary. Use Drobo Dashboard for all management.",
                ),
                RecoveryStep(
                    order=2,
                    title="Replace failed disk via hot-swap",
                    description="Drobo supports hot-swap. Replace the failed disk (indicated by red LED) with equal or larger capacity.",
                ),
                RecoveryStep(
                    order=3,
                    title="For complete failure: contact Drobo support",
                    description="BeyondRAID cannot be reconstructed with standard tools. "
                               "Professional recovery may be needed.",
                    warning="Do not remove multiple disks simultaneously.",
                ),
            ],
            warnings=[
                "BeyondRAID is proprietary - standard RAID tools will not work",
                "Never remove more than one disk at a time",
                "Drobo recovery requires specialized knowledge",
            ],
            recommended_tools=["Drobo Dashboard"],
        )

    if "synology" in vendor_lower:
        return RecoveryPlan(
            scenario=f"Synology NAS ({enclosure.model})",
            severity="info",
            steps=[
                RecoveryStep(
                    order=1,
                    title="Use DSM Storage Manager",
                    description="Synology SHR (Synology Hybrid RAID) is managed through DSM web interface.",
                ),
                RecoveryStep(
                    order=2,
                    title="Replace failed disk",
                    description="Replace the failed disk and initiate repair through Storage Manager.",
                ),
                RecoveryStep(
                    order=3,
                    title="For advanced recovery",
                    description="SHR uses standard Linux mdadm/LVM underneath. In emergencies, "
                               "disks can be read in a standard Linux system.",
                    tools=["mdadm", "lvm2"],
                ),
            ],
            warnings=["SHR is based on mdadm but with Synology-specific partition layout"],
            recommended_tools=["Synology DSM", "mdadm"],
        )

    # Generic enclosure
    return RecoveryPlan(
        scenario=f"External Enclosure: {enclosure.vendor} {enclosure.model}",
        severity="info",
        steps=[
            RecoveryStep(
                order=1,
                title="Identify RAID configuration",
                description=f"Inferred RAID type: {enclosure.inferred_raid} (confidence: {enclosure.confidence}). "
                            "Consult enclosure documentation for exact configuration.",
            ),
            RecoveryStep(
                order=2,
                title="Use manufacturer tools",
                description="Most hardware RAID enclosures require manufacturer-specific tools for management.",
            ),
        ],
        warnings=[
            "Hardware RAID enclosures may use proprietary disk formats",
            "Do not mix disks between different enclosure models",
        ],
    )


def _general_recovery_warnings() -> RecoveryPlan:
    """Universal recovery warnings applicable to all scenarios."""
    return RecoveryPlan(
        scenario="General Recovery Guidelines",
        severity="info",
        steps=[
            RecoveryStep(
                order=1,
                title="Image before repair",
                description="Always create bit-for-bit disk images before attempting any repair or recovery.",
                command="ddrescue /dev/sdX /path/to/image.img /path/to/logfile",
                tools=["ddrescue"],
            ),
            RecoveryStep(
                order=2,
                title="Work on copies",
                description="Never perform recovery operations on original media. Always work from images.",
            ),
            RecoveryStep(
                order=3,
                title="Document everything",
                description="Record disk serial numbers, positions, array configuration before making changes.",
            ),
            RecoveryStep(
                order=4,
                title="Avoid destructive operations",
                description="Do not run fsck, chkdsk, or format on degraded/failed arrays.",
                warning="These operations can overwrite data needed for recovery.",
            ),
        ],
        warnings=[
            "Do NOT write to a degraded or failed array",
            "Do NOT run fsck/chkdsk on degraded RAID",
            "Do NOT initialize/format disks that may contain recoverable data",
            "Image first, repair second -- always",
            "When in doubt, consult a professional data recovery service",
        ],
        recommended_tools=["ddrescue", "testdisk", "photorec", "smartmontools"],
    )
