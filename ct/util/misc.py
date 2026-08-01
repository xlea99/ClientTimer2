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

# Given a path to an old ClientTimer1 config.txt, this extracts a dict of settings.
def read_old_config(old_config_path):
    # Dict for roughly translating one theme to another, although since they've changed a lot its approximation.
    _APPROXIMATE_THEME = {
        "Classic Light": "E-Ink (Default)",
        "Classic Dark": "Galaxy Dark",
        "Single Pane of Glass": "Single Pane of Glass",
        "Twilight Carrier": "Telecomm Blues",
        "Black Herizons": "Black Herizons",
        # CT1's "Pretty In Pink-Mobile" was magenta-on-white — T-Magentle IS
        # the faithful port. CT2's pastel theme of the same name is new.
        "Pretty In Pink-Mobile": "T-Magentle",
        "Nothing-Else-In-Stock Green": "Park In The Forest",
        "50 Shades Of Teams Popups": "Dialpad At Dusk",
        "Unavailable: Orange Getup": "Telecomm Blues",
    }

    return_dict = {}
    with open(old_config_path,"r") as f:
        for line in f.readlines():
            parts = line.split("=", 1)
            if len(parts) < 2:
                continue
            value = parts[1].strip()
            if line.startswith("> clientList"):
                # CT1's UI stripped names at display/save time but its config
                # parser didn't, so hand-edited "[Alpha, Beta]" entries carry
                # leading spaces here while recent_save.txt has them stripped.
                # Strip, drop empties, and dedupe to match CT1's effective
                # behavior (its loadedClients dict was keyed by stripped name).
                client_list = value.lstrip("[").rstrip("]")
                names = (c.strip() for c in client_list.split(","))
                return_dict["Timers"] = list(dict.fromkeys(n for n in names if n))
            elif line.startswith("> programColorTheme"):
                return_dict["Theme"] = _APPROXIMATE_THEME.get(value, "E-Ink (Default)")
            elif line.startswith("> programSize"):
                return_dict["Size"] = value

    # Try to read elapsed times from CT1's recent_save.txt
    save_path = old_config_path.parent / "recent_save.txt"
    times = {}
    try:
        with open(save_path, "r") as f:
            lines = f.readlines()
        # First line is the date (MM/DD/YY), rest are "ClientName | HH:MM:SS"
        for line in lines[1:]:
            parts = line.strip().split("|")
            if len(parts) != 2:
                continue
            name = parts[0].strip()
            time_str = parts[1].strip()
            try:
                h, m, s = map(int, time_str.split(":"))
            except ValueError:
                continue
            secs = h * 3600 + m * 60 + s
            # A fresh CT1 day saves all-zero times — don't offer to migrate those.
            if secs > 0:
                times[name] = secs
        if times:
            return_dict["Times"] = times
    except (OSError, IndexError):
        pass

    return return_dict
