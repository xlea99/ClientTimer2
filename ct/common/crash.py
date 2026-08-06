"""Crash and problem reporting, via Sentry.

Everything here is built around one fact: THIS APP'S DATA IS CLIENT NAMES.
Row names are client names, they appear in the log, and they appear in local
variables all over the UI code. Sentry's defaults would ship all of it, so
most of this file is turning defaults off rather than turning features on.

What is deliberately disabled:

  send_default_pii        would attach IP address and username
  include_local_variables would attach every stack frame's locals, and a
                          crash in a rename or a row build has the client
                          name sitting right there in scope
  logging breadcrumbs     the app log contains row names — verified, not
                          assumed — so the whole logging integration is off
  server_name             defaults to the machine hostname, which on a
                          corporate laptop usually identifies the person

`before_send` is the backstop: it re-strips frame variables in case an SDK
default changes, drops the hostname, and redacts any term the app has
registered via `set_scrub_terms`. Belt and braces, because a leak here is
not the kind of thing you get to take back.
"""

import platform
import sys

from ct.common.logger import log
from ct.common.version import __version__

DSN = ("https://d2a048ad20bd43624c0b0a0e478b917c"
       "@o4511860892368896.ingest.us.sentry.io/4511860904296448")

# Safe to publish: a DSN is write-only and can only submit events to this one
# project. Sentry documents them in public samples for exactly this reason.

_enabled = True          # flipped by set_enabled() once settings have loaded
_scrub_terms = []        # strings to redact from any outgoing payload
_started = False


def set_enabled(value):
    """Turn reporting off at runtime.

    Not done by skipping init(): init has to happen before anything else can
    crash, which is long before settings are readable. So reporting starts on
    and this drops events at send time instead.
    """
    global _enabled
    _enabled = bool(value)


def set_scrub_terms(terms):
    """Register strings to redact from every outgoing event.

    The app calls this with its current row names, so even if a client name
    reaches a payload by a route nobody anticipated, it is replaced on the
    way out. Short terms are ignored — redacting "6" would shred the payload
    and tell an attacker nothing anyway.
    """
    global _scrub_terms
    _scrub_terms = sorted(
        {t for t in terms if isinstance(t, str) and len(t.strip()) >= 4},
        key=len, reverse=True)          # longest first, so subsets don't win


def _redact(obj):
    """Walk a payload replacing any registered term. Structure preserved."""
    if not _scrub_terms:
        return obj
    if isinstance(obj, str):
        for term in _scrub_terms:
            if term in obj:
                obj = obj.replace(term, "[redacted]")
        return obj
    if isinstance(obj, dict):
        return {k: _redact(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return type(obj)(_redact(v) for v in obj)
    return obj


def _before_send(event, hint):
    if not _enabled:
        return None
    # A user report's own sentence is exempt from redaction — they chose to
    # write it. Everything wrapped around it is not, so pull the sentence
    # out, scrub the rest, and put it back. Without this the blanket redact
    # below silently rewrites the user's words, which was the whole thing
    # the "sent as written" promise in the dialog was about.
    # capture_message puts the text in event["message"]; some SDK paths use
    # event["logentry"]["message"] instead, so preserve whichever is there.
    written = None
    written_logentry = None
    if (event.get("tags") or {}).get("user_report") == "true":
        written = event.get("message")
        written_logentry = (event.get("logentry") or {}).get("message")

    # The hostname identifies the user on a corporate machine.
    event.pop("server_name", None)
    # Re-strip frame locals even though include_local_variables is off, so a
    # future SDK default can't quietly turn this back on.
    for entry in event.get("exception", {}).get("values", []):
        for frame in entry.get("stacktrace", {}).get("frames", []):
            frame.pop("vars", None)

    event = _redact(event)
    if written is not None:
        event["message"] = written
    if written_logentry is not None:
        event.setdefault("logentry", {})["message"] = written_logentry
    return event


def init():
    """Start reporting. Call once, as early as possible."""
    global _started
    if _started:
        return
    try:
        import sentry_sdk
        from sentry_sdk.integrations.logging import LoggingIntegration
    except ImportError:
        log.warning("sentry_sdk not available — crash reporting disabled.")
        return

    frozen = getattr(sys, "frozen", False)
    sentry_sdk.init(
        dsn=DSN,
        # Groups crashes by build, and makes "is this still happening after
        # the fix?" answerable. This is what version.py was for.
        release=f"clienttimer2@{__version__}",
        environment="production" if frozen else "development",
        send_default_pii=False,
        include_local_variables=False,
        # Passing a configured LoggingIntegration replaces the default one.
        # level=None: no log record becomes a breadcrumb.
        # event_level=None: no log record becomes an event on its own.
        integrations=[LoggingIntegration(level=None, event_level=None)],
        max_breadcrumbs=25,
        before_send=_before_send,
        # Errors only. No performance tracing — it would sample UI events
        # and there is nothing here worth the payload.
        traces_sample_rate=0.0,
    )
    sentry_sdk.set_tag("os", platform.platform(terse=True))
    sentry_sdk.set_tag("frozen", str(frozen))
    _started = True
    log.info(f"Crash reporting started (clienttimer2@{__version__}, "
             f"{'production' if frozen else 'development'}).")


def log_tail(lines=250):
    """The end of the app log, redacted, as bytes ready to attach.

    Tail rather than whole: the log runs to hundreds of thousands of lines,
    and only the run that broke is worth anything.
    """
    from ct.common.setup import PATHS
    path = PATHS.logs / "clienttimer2.log"
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return b""
    tail = "\n".join(text.splitlines()[-lines:])
    return _redact(tail).encode("utf-8")


def state_snapshot():
    """state.json with client names replaced, as bytes ready to attach.

    Nearly everything in that file is diagnostic gold and carries no
    identity: settings (theme and size drive most layout bugs), row count
    and order, group nesting, collapsed state, window height, elapsed
    times, schema version. Only `name` identifies anyone, and it is the
    least useful field for reproducing a bug.

    Replacement names keep the ORIGINAL LENGTH. Row width is derived from
    the longest name in the list, so a layout bug that only happens with a
    40-character client would vanish if every name became "Client 1".
    """
    import json
    from ct.common.setup import PATHS
    try:
        data = json.loads((PATHS.current / "state.json")
                          .read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return b""
    for i, row in enumerate(data.get("layout", {}).get("rows", [])):
        original = row.get("name", "")
        stand_in = f"Client {i}" if row.get("type") == "timer" else f"Group {i}"
        if len(original) > len(stand_in):
            stand_in += "x" * (len(original) - len(stand_in))
        row["name"] = stand_in[:len(original)] or stand_in
    return json.dumps(data, indent=2).encode("utf-8")


def capture_current_exception():
    """Report the exception currently being handled.

    The entrypoint catches everything so it can log a full trace and exit
    cleanly, which means Sentry's own excepthook never fires — the exception
    is handled. Without this call, the one place guaranteed to see a fatal
    error would be the one place that never reports it.
    """
    if not (_started and _enabled):
        return
    try:
        import sentry_sdk
        with sentry_sdk.new_scope() as scope:
            # Attach it here too: with a crash there is nobody to ask what
            # their theme, size or row layout was.
            snap = state_snapshot()
            if snap:
                scope.add_attachment(bytes=snap, filename="state.json")
            sentry_sdk.capture_exception()
    except Exception:
        log.exception("Failed to report the fatal exception")


def report(message, attachments=None, context=None):
    """Send a user-written problem report. Returns True if it was handed off.

    attachments: iterable of (filename, bytes)
    context:     dict of extra structured fields
    """
    if not (_started and _enabled):
        return False
    try:
        import sentry_sdk
        with sentry_sdk.new_scope() as scope:
            # Read by _before_send to exempt the description from redaction.
            scope.set_tag("user_report", "true")
            if context:
                scope.set_context("report", _redact(context))
            for filename, data in (attachments or ()):
                scope.add_attachment(bytes=data, filename=filename)
            # The message is sent AS WRITTEN. Everything else here is
            # redacted because the user never chose to send it; their own
            # sentence is the one thing they did choose, and scrubbing it
            # would turn "Acme's timer broke" into gibberish. The dialog
            # says so, so the choice is informed.
            sentry_sdk.capture_message(message, level="info")
        return True
    except Exception:
        log.exception("Failed to submit problem report")
        return False
