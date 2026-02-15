"""Entry point for `python -m ct`."""

from ct.common.setup import assert_running_from_install_root, PATHS
from ct.app import main


assert_running_from_install_root(PATHS.root / "clienttimer2.exe")
main()
