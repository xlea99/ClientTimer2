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
