from datetime import datetime


# Simply returns the current local time as an ISO8601 string with timezone offset.
def now_iso():
    return datetime.now().astimezone().isoformat()

# Given seconds as a number, this method returns a pretty HH:MM:SS formatted string. Negative values
# get clamped to zero.
def format_time(seconds : int | float):
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


# What the clipboard gets. The ROW always shows HH:MM:SS — this is only about
# the copy, because a time is read on screen and pasted into something else,
# and those two want different things.
COPY_FORMATS = ("HH:MM", "HH:MM:SS", "Decimal", "Raw Minutes")

DEFAULT_COPY_FORMAT = "HH:MM"


# Formats seconds for the clipboard. Unknown format names fall back to the
# default rather than raising: a hand-edited state.json must not be able to
# break copying, which is the app's whole reason for existing.
#
# Two deliberate choices worth keeping:
#
#   * HH:MM and Raw Minutes both FLOOR to the whole minute. They therefore
#     agree with each other and with the HH:MM:SS on screen — a row reading
#     05:15:45 copies as 05:15 and 315, never 05:16 and 316. Rounding up
#     would also mean the app silently overstates billable time, which is
#     the one direction an error must not go.
#   * Decimal keeps two places, the timesheet convention, so it carries the
#     sub-minute precision the other two drop. 05:15:45 is 5.26 there. That
#     is not a contradiction — it is the finer-grained format doing its job.
def format_copy_time(seconds : int | float, fmt : str = DEFAULT_COPY_FORMAT):
    seconds = max(0, int(seconds))
    if fmt == "HH:MM:SS":
        return format_time(seconds)
    if fmt == "Decimal":
        return f"{seconds / 3600:.2f}"
    if fmt == "Raw Minutes":
        # Bare number, no unit. These are pasted into numeric fields, and
        # "315 minutes" is not a number. Same reasoning as Decimal's "5.25".
        return str(seconds // 60)
    h, rem = divmod(seconds, 3600)
    return f"{h:02d}:{rem // 60:02d}"
