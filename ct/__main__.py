import sys
from ct.common import crash
from ct.common.logger import log
from ct.common.setup import assert_running_from_install_root, PATHS
from ct.ui.app import main

def _install_slot_excepthook():
    """Log exceptions that escape a Qt slot.

    The try/except in run() only sees exceptions raised BEFORE app.exec()
    starts. Anything raised from a slot — the one-second tick, every button,
    the update-check signal — is handed to sys.excepthook by PySide6 and
    then the event loop carries on. The default hook prints to stderr,
    which the frozen build does not have, so those errors vanished without
    a trace: a broken tick silently stopped autosave for the whole session.

    Chains to whatever hook was already installed rather than replacing
    it: crash.init() runs first, and Sentry's own excepthook integration
    is what reports the exception, so calling capture here as well would
    send every one twice.
    """
    previous = sys.excepthook

    def hook(exc_type, exc, tb):
        if not issubclass(exc_type, KeyboardInterrupt):
            log.error("Uncaught exception in a Qt slot",
                      exc_info=(exc_type, exc, tb))
        previous(exc_type, exc, tb)

    sys.excepthook = hook


# Entry point for `python -m ct`
def run() -> None:
    # Before anything else, so a failure during setup is still reported.
    crash.init()
    _install_slot_excepthook()
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