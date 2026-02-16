import os
import sys
from pathlib import Path
from dataclasses import dataclass

# Lil helper function to create missing directories if missing, and optionally error out when a path
# doesn't exist.
def ensure_directory(path: Path,must_exist=False):
    if must_exist:
        if not path.exists():
            raise FileNotFoundError(f"Required directory is missing: {path}")
        if not path.is_dir():
            raise NotADirectoryError(f"Expected a directory, got a file: {path}")
    else:
        path.mkdir(parents=True,exist_ok=True)
    return path

# Ensures that, while running in a frozen (exe) build, the path of the running exe is EXACTLY paths.root / exe_name.
def assert_running_from_install_root(expected_path: Path):
    # Running from source, so we just ignore rn
    if not getattr(sys, "frozen", False):
        return

    actual_exe = Path(sys.executable).resolve()
    expected_exe = expected_path.resolve()

    if actual_exe != expected_exe:
        raise RuntimeError(
            "Application is being run from an unexpected location.\n"
            f"Expected: {expected_exe}\n"
            f"Actual:   {actual_exe}"
        )

# Dataclass for accessing paths across program.
@dataclass(frozen=False)
class ProjectPaths:

    root: Path
    data: Path
    assets: Path
    old: Path

    logs: Path
    current: Path
    snapshots: Path
    sessions: Path

    @staticmethod
    def build():
        appdata = os.getenv("APPDATA")
        if not appdata:
            raise RuntimeError("Missing APPDATA environment variable, cannot determine data directories.")
        localappdata = os.getenv("LOCALAPPDATA")
        if not localappdata:
            raise RuntimeError("Missing LOCALAPPDATA environment variable, cannot determine data directories.")

        # Folder for the install itself, no user-specific files, just runtime
        if getattr(sys, "frozen", False):
            root = ensure_directory(Path(localappdata) / "Programs" / "ClientTimer2", must_exist=True)
        # For when we're running this nonfrozen in intellichad
        else:
            root = Path(__file__).resolve().parents[2]

        # Folder for runtime assets, should be root/assets
        assets = ensure_directory(root / "assets",must_exist=True)

        # Folder for all clienttimer user-specific and session related stuff
        data = ensure_directory(Path(appdata) / "ClientTimer2")

        # The path to the Old ClientTimer1 folder may or may not exist, so we don't enforce it.
        old = Path(Path(appdata) / "ICOMM Client Timer")

        # Folders within the data folder
        logs = ensure_directory(data / "logs")
        current = ensure_directory(data / "current")
        snapshots = ensure_directory(data / "snapshots")
        sessions = ensure_directory(data / "completed_sessions")

        return ProjectPaths(
            root = root,
            data = data,
            assets = assets,
            old = old,
            logs = logs,
            current = current,
            snapshots = snapshots,
            sessions = sessions
        )
PATHS = ProjectPaths.build()