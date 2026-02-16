from datetime import datetime



# Simply returns the current local time as an ISO8601 string with timezone offset.
def now_iso():
    return datetime.now().astimezone().isoformat()


# Given a path to an old ClientTimer1 config.txt, this extracts a dict of settings.
def read_old_config(old_config_path):
    # Dict for roughly translating one theme to another, although since they've changed a lot its approximation.
    _APPROXIMATE_THEME = {
        "Classic Light": "Cupertino Light",
        "Classic Dark": "Galaxy Dark",
        "Cimply Blue": "Cimply Premier",
        "Twilight TMA": "ICOMM Blues",
        "Black Herizons": "Black Herizons",
        "Pretty In Pink-Mobile": "T-Magentle",
        "Nothing-Else-In-Stock Green": "Park In The Forest",
        "50 Shades Of Teams Popups": "Dialpad At Dusk",
        "Unavailable: Orange Getup": "ICOMM Blues",
    }

    return_dict = {}
    with open(old_config_path,"r") as f:
        for line in f.readlines():
            if line.startswith("> clientList"):
                client_list = line.split("=")[1].strip().lstrip("[").rstrip("]")
                return_dict["Timers"] = client_list.split(",")
            if line.startswith("> programColorTheme"):
                return_dict["Theme"] = _APPROXIMATE_THEME.get(line.split("=")[1].strip(),"Cupertino Light")
            if line.startswith("> programSize"):
                return_dict["Size"] = line.split("=")[1].strip()
    return return_dict