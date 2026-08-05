"""The installed build's identity — the single source of truth for it.

Kept deliberately dependency-free so anything can import it: the About page,
the update check, the installer build script. Nothing here may import Qt or
anything from ct.core, or it stops being safe to read from a build script.

Two separate ideas that are easy to conflate:

  * THIS FILE describes the build the user is running right now. It ships
    inside the exe and is only ever changed by cutting a release.
  * latest.json in the repo root describes the newest build that EXISTS. It
    lives on the server and is read over the network.

An update check is exactly the comparison of those two. Never derive one
from the other at runtime.
"""

__version__ = "2.3.0"

# ISO-8601, and a plain string on purpose: the About page displays it and the
# manifest carries the same value, so parsing it into a date object here would
# just mean formatting it back again at every use site.
RELEASE_DATE = "2026-08-05"


def version_tuple():
    """(major, minor, patch) for comparing against a manifest.

    String comparison gets this wrong the moment a number reaches double
    digits — "2.10.0" < "2.9.0" is True as text and False as a version.
    """
    return tuple(int(part) for part in __version__.split("."))
