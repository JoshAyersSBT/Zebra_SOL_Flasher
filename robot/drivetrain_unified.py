"""
Backward-compatible drivetrain module.

Import this from main.py instead of directly binding to a single drive model.
"""

from .drive_models import (
    BaseDriveModel,
    DifferentialDriveModel,
    AckermannDriveModel,
    DriveSystem,
    create_drive_system,
)

# Legacy alias preserved for older code that still imports DifferentialDrive.
DifferentialDrive = DifferentialDriveModel
AckermannDrive = AckermannDriveModel
