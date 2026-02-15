import sys
from ct.common.logger import log
from ct.common.setup import assert_running_from_install_root, PATHS
from ct.ui.app import main

# Entry point for `python -m ct`
def run() -> None:
    try:
        assert_running_from_install_root(PATHS.root / "clienttimer2.exe")
        main()
    except SystemExit:
        raise
    except Exception:
        # Full stack trace, always
        log.exception("Uncaught exception in entrypoint, exiting")
        sys.exit(1)

if __name__ == "__main__":
    run()