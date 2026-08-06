"""Update checking: is there a newer build than this one?

Reads a small manifest from the repo and compares it against the version
baked into this build. Deliberately knows nothing about downloading or
installing — that is a separate step, and keeping them apart means the check
can run on every launch without any risk of touching the user's machine.

Design rules this file follows:

  * NEVER blocks startup. The check runs on a worker thread with a short
    timeout, and every failure path is silent. An always-on-top scratchpad
    that hangs three seconds because a CDN is slow is worse than one that
    never mentions updates at all.
  * raw.githubusercontent, NOT the GitHub API. The API allows 60 requests
    per hour per IP, and a few dozen users behind one corporate NAT share a
    single IP — a Monday morning would exhaust it and every user past the
    60th would silently get nothing. The raw host is CDN-served with no such
    limit.
  * Version comparison is numeric, never string. "2.10.0" < "2.9.0" is True
    as text and False as a version, and that bug stays invisible until the
    tenth patch release.
"""

import json
import urllib.error
import urllib.request
from pathlib import Path

from ct.common.logger import log
from ct.common.version import __version__, version_tuple

MANIFEST_URL = ("https://raw.githubusercontent.com/xlea99/ClientTimer2/"
                "master/latest.json")

# Short on purpose. This runs at startup; a slow answer is worth less than a
# fast "never mind".
TIMEOUT_SECONDS = 6


def _parse_version(text):
    """'2.3.0' -> (2, 3, 0), or None if it isn't a plain numeric version."""
    try:
        parts = tuple(int(p) for p in str(text).strip().split("."))
    except (ValueError, AttributeError):
        return None
    return parts if len(parts) == 3 else None


def fetch_manifest(url=MANIFEST_URL, timeout=TIMEOUT_SECONDS):
    """Download and parse the manifest. Returns a dict, or None on any failure.

    Every failure is None rather than an exception: no network, corporate
    proxy, DNS, a 404 because a release has not been published yet, malformed
    JSON. None of those are worth telling the user about — they cannot act on
    any of them, and the app works fine without an update check.
    """
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": f"ClientTimer2/{__version__}"})
        with urllib.request.urlopen(req, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, OSError,
            ValueError, TimeoutError) as exc:
        log.info(f"Update check did not complete ({type(exc).__name__}). "
                 f"This is not an error.")
        return None
    if not isinstance(data, dict):
        log.warning("Update manifest was not a JSON object; ignoring.")
        return None
    return data


def is_newer(remote_version, local_version=None):
    """True only if remote is a valid version strictly newer than local.

    Unparseable input is False, not a crash and not an update prompt. A
    typo'd manifest should be a non-event, never a push toward a download.
    """
    remote = _parse_version(remote_version)
    if remote is None:
        return False
    local = _parse_version(local_version) if local_version else version_tuple()
    if local is None:
        return False
    return remote > local


# check() outcomes.
UPDATE = "update"      # a newer, installable release exists
CURRENT = "current"    # reachable, and we are already up to date
FAILED = "failed"      # could not reach or parse the manifest


def check(url=MANIFEST_URL, timeout=TIMEOUT_SECONDS):
    """Look for a newer release. Returns (status, manifest).

    Three outcomes, not two, because an AUTOMATIC check and a check the user
    explicitly asked for need different things. The automatic one stays
    silent whether we are current or the network is down. A manual one has
    to answer either way — a button that does nothing visible reads as
    broken, and "you're up to date" is a different answer from "I couldn't
    tell". manifest is None only when status is FAILED.
    """
    manifest = fetch_manifest(url, timeout)
    if manifest is None:
        return FAILED, None
    remote = manifest.get("version")
    if not is_newer(remote):
        log.info(f"Update check: running {__version__}, "
                 f"latest is {remote} — up to date.")
        return CURRENT, manifest
    if not str(manifest.get("url", "")).startswith("https://"):
        # A manifest that advertises a version but no usable download is a
        # publishing mistake. Telling the user about an update they cannot
        # install is worse than staying quiet, so this counts as current.
        log.warning(f"Manifest advertises {remote} with no valid https url; "
                    f"ignoring.")
        return CURRENT, manifest
    log.info(f"Update available: {remote} (running {__version__}).")
    return UPDATE, manifest


def release_page_url():
    """Where the human-readable changelog lives.

    The manifest's `notes` is one line sized for a toast; the GitHub release
    body is the actual changelog, with markdown and history. Linking beats
    trying to squeeze it into the UI.
    """
    return "https://github.com/xlea99/ClientTimer2/releases"


def file_sha256(path, chunk=1024 * 1024):
    """Hex digest of a file, read in chunks so a 40 MB installer stays cheap."""
    import hashlib
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(chunk), b""):
            digest.update(block)
    return digest.hexdigest()


def download(url, dest_dir=None, timeout=120, sha256=None):
    """Fetch the installer to a temp file. Returns the path, or None.

    urllib rather than a browser ON PURPOSE. Mark-of-the-Web — the zone
    marker that makes Windows show "Windows protected your PC" — is attached
    by the DOWNLOADING program, not the host. Browsers attach it; urllib does
    not. Handing the URL to a browser would introduce a SmartScreen prompt
    that the current Dropbox-sync workflow does not have, which is exactly
    the sort of new friction that makes people stop updating.

    `sha256` is the digest the manifest claims for this file. Two rules, and
    the difference between them matters:

      * ABSENT -> skip the check. No claim was made, and refusing would mean
        a hand-edited manifest breaks updates for everyone at once.
      * PRESENT AND MISMATCHED -> refuse. A claim was made and violated.

    Note WHERE it is verified: after the write, read back off disk. Hashing
    the in-memory bytes would only re-check what TLS already guarantees —
    the record layer is authenticated, so a corrupted response fails the
    connection rather than arriving quietly. The step nothing is watching is
    the write itself (full disk, AV touching the file mid-write), and only a
    read-back catches that.
    """
    import tempfile
    dest_dir = dest_dir or tempfile.gettempdir()
    name = url.rsplit("/", 1)[-1] or "ClientTimer2_Setup.exe"
    path = Path(dest_dir) / name
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": f"ClientTimer2/{__version__}"})
        with urllib.request.urlopen(req, timeout=timeout) as response:
            expected = response.headers.get("Content-Length")
            data = response.read()
        # A truncated download is indistinguishable from a good one once it
        # is on disk, and we are about to EXECUTE it. Check what the server
        # said it was sending.
        if expected is not None and len(data) != int(expected):
            log.warning(f"Update download was {len(data)} bytes, expected "
                        f"{expected}; discarding.")
            return None
        if len(data) < 1_000_000:
            log.warning(f"Update download was only {len(data)} bytes — that "
                        f"is not an installer; discarding.")
            return None
        path.write_bytes(data)
        if sha256:
            actual = file_sha256(path)
            if actual.lower() != str(sha256).strip().lower():
                # Leaving it on disk would mean a file we have declined to
                # trust sitting in temp with an installer's name on it.
                log.warning(f"Update checksum did not match: manifest claims "
                            f"{sha256}, file is {actual}; discarding.")
                path.unlink(missing_ok=True)
                return None
            log.info("Update checksum verified.")
        else:
            log.info("Manifest carries no sha256; skipping checksum check.")
    except (urllib.error.URLError, urllib.error.HTTPError, OSError,
            ValueError, TimeoutError) as exc:
        log.warning(f"Update download failed: {type(exc).__name__}: {exc}")
        return None
    log.info(f"Update downloaded to '{path}' ({len(data):,} bytes).")
    return path


def launch_installer(path):
    """Start Setup and return True. The app does NOT need to exit first.

    Restart Manager closes the running app itself — verified against a real
    build, Setup's own log reporting "RestartManager found an application
    using one of our files: clienttimer2.exe". That is why there is no
    AppMutex, no helper process, no temp batch file and no guessed delay
    here: all of that existed to work around a blocker that was removed.

    Not quitting first is deliberate. If Setup fails to start — quarantined
    by AV, corrupt file, disk full — the user still has a working app. Quit
    first and a failed launch leaves them with nothing.
    """
    import subprocess
    try:
        subprocess.Popen(
            [str(path), "/SILENT", "/NORESTART", "/FORCECLOSEAPPLICATIONS"],
            close_fds=True,
            creationflags=getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        )
    except OSError as exc:
        log.warning(f"Could not launch the installer: {exc}")
        return False
    log.info("Installer launched; Restart Manager will close the app.")
    return True
