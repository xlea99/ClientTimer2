import sys
from ct.common import crash
from ct.common.logger import log
from ct.common.setup import assert_running_from_install_root, PATHS
from ct.ui.app import main

# Entry point for `python -m ct`
def run() -> None:
    # Before anything else, so a failure during setup is still reported.
    crash.init()
    try:
        assert_running_from_install_root(PATHS.root / "clienttimer2.exe")
        main()
    except SystemExit:
        raise
    except Exception:
        # Full stack trace, always
        log.exception("Uncaught exception in entrypoint, exiting")
        # The logging integration is off (the log carries client names), so
        # nothing reports this unless it is sent explicitly.
        crash.capture_current_exception()
        sys.exit(1)

if __name__ == "__main__":
    run()