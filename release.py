"""Cut a release: source in, installer + manifest out.

Two ways to run it, doing exactly the same thing:

    * Fill in VERSION and NOTES in the config block below and hit Run in the
      IDE. This is the intended everyday path — a release is a handful of
      steps you take a few times a year, and retyping a command line each
      time is friction with no upside.
    * python release.py 2.4.0 --notes "What changed, in one line."

Passing any command-line argument ignores the config block entirely, so
neither path is a special case of the other.

WHY THIS EXISTS. The version number used to be typed by hand in five places
across three files — version.py (twice), the .iss, and latest.json (in
`version`, and again inside `url` as both the tag and the filename). They
fail differently when they drift, and the nastiest one is silent: a `url`
that does not match the uploaded asset 404s the download for every user,
with nothing visible on this end. Now the number is typed once, here, and
everything else is derived.

The checksum comes along for free. release.py hashes the installer it just
built, so the digest in latest.json cannot be stale or mistyped — which was
the entire objection to adding one by hand.

THIS SCRIPT NEVER TOUCHES THE NETWORK. It builds, and it writes local files.
Publishing — creating the GitHub release and pushing latest.json — stays
manual and in that order, because those are the two irreversible steps and
they are worth doing deliberately. If the script fails partway, the only
damage is a modified working tree: `git checkout ct/common/version.py
installer/version.iss latest.json` undoes it.

ORDER MATTERS WHEN YOU PUBLISH. The release asset must exist BEFORE
latest.json is pushed. Push the manifest first and every running app is told
to download something that is not there yet. The script prints the steps in
the correct order at the end; follow them top to bottom.
"""

import argparse
import hashlib
import json
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

# Two fixes for running under an IDE or a redirected log rather than a console:
#   encoding    - Windows falls back to the locale codepage (cp1252) when
#                 stdout is a pipe, turning every em dash into a replacement
#                 glyph.
#   line_buffer - without it, progress lines sit in a buffer and land AFTER
#                 the subprocess output and the final error, so the run reads
#                 out of order.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", line_buffering=True)
    except (AttributeError, ValueError):
        pass

# ===========================================================================
#  RUN-FROM-IDE CONFIG — fill these in and hit Run. That's the whole ritual.
# ===========================================================================
# These are used ONLY when the script is started with no command-line
# arguments, which is what IntelliJ/PyCharm's green arrow does. Passing any
# argument on the command line ignores this block entirely, so both ways of
# running stay honest — same steps, same guards, same order.
#
# Leaving a stale VERSION here after a release is harmless: the next run
# fails on "not newer than the current X" before it writes anything.

VERSION = "2.3.0"        # e.g. "2.4.0"
NOTES = "UI and usability update"          # one line, shown in the update toast

REBUILD = False     # re-cut a version that is ALREADY the current one
SKIP_TESTS = True  # don't fucking do it
ALLOW_DIRTY = False # build with uncommitted changes present (DON'T DO IT)
ISCC_PATH = None    # path to ISCC.exe, if it isn't in the usual place

# ===========================================================================

ROOT = Path(__file__).resolve().parent
VERSION_PY = ROOT / "ct" / "common" / "version.py"
VERSION_ISS = ROOT / "installer" / "version.iss"
SETUP_ISS = ROOT / "installer" / "clienttimer2_setup.iss"
SPEC = ROOT / "clienttimer2.spec"
OUTPUT_DIR = ROOT / "installer" / "output"
MANIFEST = ROOT / "latest.json"

# Must match the repo that ct/core/update.py reads its manifest from.
REPO = "xlea99/ClientTimer2"

VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")

# Inno's command-line compiler. The GUI is not needed and cannot be scripted.
ISCC_CANDIDATES = [
    Path(r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"),
    Path(r"C:\Program Files\Inno Setup 6\ISCC.exe"),
]


class ReleaseError(Exception):
    """Anything that should stop the release with a readable message."""


def step(n, total, message):
    print(f"\n[{n}/{total}] {message}", flush=True)


def run(cmd, **kwargs):
    """Run a command, streaming its output. Raises on a non-zero exit."""
    print(f"    $ {' '.join(str(c) for c in cmd)}", flush=True)
    result = subprocess.run(cmd, cwd=ROOT, **kwargs)
    if result.returncode != 0:
        raise ReleaseError(f"command failed with exit code "
                           f"{result.returncode}: {cmd[0]}")
    return result


def parse_version(text):
    return tuple(int(p) for p in text.split("."))


def current_version():
    """Read __version__ without importing — the file may be mid-rewrite."""
    match = re.search(r'^__version__\s*=\s*"([^"]+)"',
                      VERSION_PY.read_text(encoding="utf-8"), re.M)
    if not match:
        raise ReleaseError(f"could not find __version__ in {VERSION_PY}")
    return match.group(1)


def find_iscc(override=None):
    if override:
        path = Path(override)
        if not path.exists():
            raise ReleaseError(f"ISCC not found at {path}")
        return path
    for candidate in ISCC_CANDIDATES:
        if candidate.exists():
            return candidate
    raise ReleaseError(
        "Inno Setup's compiler (ISCC.exe) was not found. Install Inno Setup 6 "
        "from https://jrsoftware.org/isinfo.php, or pass --iscc <path>.")


def _porcelain_path(line):
    """The path out of a `git status --porcelain` line.

    Format is two status characters, a space, then the path. Renames appear
    as `old -> new`; the new name is the one that matters here.
    """
    path = line[3:].strip()
    if " -> " in path:
        path = path.split(" -> ", 1)[1]
    return path.strip('"')


def check_clean_tree():
    """Refuse to build on a dirty tree — except for this file itself.

    release.py is EXPECTED to be modified before every run: the config block
    at the top is edited in place, which is the whole point of running it
    from the IDE. Counting that as "uncommitted work you should deal with"
    would mean the script blocks itself every single time, and the only way
    out would be --allow-dirty — which would then also be silencing the
    check for every OTHER file. A guard people are trained to bypass is
    worse than no guard.

    The trade: real logic changes to release.py are ignored here too. That
    is acceptable, because the point of this check is protecting the files
    the script REWRITES (version.py, version.iss, latest.json) so a failed
    build leaves an obvious diff. It was never about the script's own source.
    """
    result = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT,
                            capture_output=True, text=True)
    if result.returncode != 0:
        raise ReleaseError("git status failed — is this a git repo?")
    self_path = Path(__file__).resolve().relative_to(ROOT).as_posix()
    dirty, ignored_self = [], False
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        if _porcelain_path(line) == self_path:
            ignored_self = True
            continue
        dirty.append(line)
    if ignored_self:
        print(f"    ({self_path} has uncommitted changes — expected, ignoring)")
    if dirty:
        raise ReleaseError(
            "the working tree has uncommitted changes:\n    "
            + "\n    ".join(dirty)
            + "\n\nCommit or stash them first, so that if the build goes wrong "
              "you can tell the script's edits from your own. "
              "Use --allow-dirty to override.")


def sha256_of(path, chunk=1024 * 1024):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(chunk), b""):
            digest.update(block)
    return digest.hexdigest()


def write_version_py(version, released):
    """Rewrite the two constants in place, leaving the docstring intact."""
    text = VERSION_PY.read_text(encoding="utf-8")
    text, n1 = re.subn(r'^__version__\s*=\s*"[^"]*"',
                       f'__version__ = "{version}"', text, count=1, flags=re.M)
    text, n2 = re.subn(r'^RELEASE_DATE\s*=\s*"[^"]*"',
                       f'RELEASE_DATE = "{released}"', text, count=1, flags=re.M)
    if not (n1 and n2):
        raise ReleaseError(f"could not rewrite the constants in {VERSION_PY}")
    VERSION_PY.write_text(text, encoding="utf-8")


def write_version_iss(version):
    VERSION_ISS.write_text(
        "; GENERATED FILE — do not edit by hand.\n"
        "; Written by release.py from ct/common/version.py, which is the "
        "single\n; source of truth for the version number.\n"
        ";\n"
        "; It is committed rather than gitignored so that "
        "clienttimer2_setup.iss\n; still compiles straight from a fresh "
        "clone, with or without release.py.\n"
        f'#define MyAppVersion "{version}"\n',
        encoding="utf-8")


def resolve_config(argv):
    """Settings from the command line, or from the block at the top of the file.

    argparse is only consulted when arguments were actually passed. It cannot
    run first and fall back afterwards: `--notes` is required, so a bare
    `python release.py` would die inside argparse before the IDE block ever
    got a look in.
    """
    if argv:
        parser = argparse.ArgumentParser(
            description="Build a Client Timer 2 release and its manifest.")
        parser.add_argument("version", help="the new version, e.g. 2.4.0")
        parser.add_argument("--notes", required=True,
                            help="one-line summary shown in the update toast")
        parser.add_argument("--rebuild", action="store_true",
                            help="re-cut the CURRENT version instead of "
                                 "bumping (see the notes in check_version)")
        parser.add_argument("--skip-tests", action="store_true",
                            help="skip the test suite (don't)")
        parser.add_argument("--allow-dirty", action="store_true",
                            help="build with uncommitted changes present")
        parser.add_argument("--iscc",
                            help="path to ISCC.exe if not in the usual place")
        return parser.parse_args(argv)

    if not VERSION.strip() or not NOTES.strip():
        raise ReleaseError(
            "nothing to release.\n\n"
            "    Running with no arguments uses the config block at the top of\n"
            f"    {Path(__file__).name}. Set both:\n\n"
            '        VERSION = "2.4.0"\n'
            '        NOTES   = "What changed, in one line."\n\n'
            "    Or pass them on the command line instead:\n"
            '        python release.py 2.4.0 --notes "..."')

    return argparse.Namespace(
        version=VERSION, notes=NOTES, skip_tests=SKIP_TESTS,
        allow_dirty=ALLOW_DIRTY, iscc=ISCC_PATH, rebuild=REBUILD)


def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]
    args = resolve_config(argv)
    if not argv:
        print(f"Reading the config block in {Path(__file__).name} "
              f"(no arguments passed).", flush=True)

    version = args.version.strip().lstrip("v")
    total = 8

    if not VERSION_RE.match(version):
        raise ReleaseError(f"'{version}' is not a three-part version like 2.4.0")

    # --- 1. Checks that must happen before anything is written -------------
    step(1, total, "Checking the version and the working tree")
    previous = current_version()
    if args.rebuild:
        # Re-cutting a version that is already current. Legitimate when the
        # build went out wrong, or was never distributed and has since been
        # superseded by more work under the same number.
        #
        # It relaxes ONLY the bump check, and only for the exact current
        # version — a typo'd rebuild target is still an error, and the
        # already-published check below is still enforced against a version
        # OLDER than this one.
        #
        # THE COST, which is why it is opt-in: anyone already running this
        # version will never be offered the new build. Same number means the
        # updater has nothing to compare, so they sit on the old bytes
        # forever. Only rebuild a version you know nobody is running.
        if parse_version(version) != parse_version(previous):
            raise ReleaseError(
                f"--rebuild re-cuts the CURRENT version, but {version} is not "
                f"{previous}.\n\n"
                f"    Set the version to {previous} to rebuild it, or drop "
                f"--rebuild to release {version} as a bump.")
        print(f"    REBUILD: re-cutting {version} (not a bump)")
        print(f"    Anyone already on {version} will NOT be offered this "
              f"build — same version, nothing to compare.")
    elif parse_version(version) <= parse_version(previous):
        raise ReleaseError(
            f"{version} is not newer than the current {previous}.\n\n"
            f"    That {previous} is read from ct/common/version.py, which is\n"
            f"    THE source of truth — not latest.json. A previous run of this\n"
            f"    script rewrote it, so resetting the manifest will not clear\n"
            f"    this. To undo an unwanted release, restore all three files\n"
            f"    the script writes:\n\n"
            f"        git checkout HEAD -- ct/common/version.py "
            f"installer/version.iss latest.json\n\n"
            f"    Releasing a version that is not a bump means nobody is ever\n"
            f"    prompted for it.\n\n"
            f"    Re-cutting {previous} on purpose? Use --rebuild, or set "
            f"REBUILD = True in the config block.")
    print(f"    {previous} -> {version}   (from ct/common/version.py)")

    if MANIFEST.exists():
        published = json.loads(MANIFEST.read_text(encoding="utf-8")).get("version")
        # Under --rebuild the manifest is EXPECTED to already name this
        # version; that is the whole point. Going BACKWARDS is still wrong
        # either way, so the check relaxes from <= to < rather than off.
        too_old = (parse_version(version) < parse_version(published)
                   if args.rebuild
                   else parse_version(version) <= parse_version(published))
        if published and too_old:
            raise ReleaseError(
                f"latest.json already advertises {published}, which is not "
                f"older than {version}.\n\n"
                f"    This is the secondary guard — the primary one reads "
                f"ct/common/version.py.")

    if not args.allow_dirty:
        check_clean_tree()
    iscc = find_iscc(args.iscc)
    print(f"    ISCC: {iscc}")

    # --- 2. Tests, BEFORE the version is bumped ---------------------------
    # Deliberately first: a failure here leaves the tree untouched, so there
    # is nothing to undo.
    step(2, total, "Running the test suite")
    if args.skip_tests:
        print("    SKIPPED (--skip-tests)")
    else:
        run([sys.executable, "-m", "unittest", "discover", "-s", "tests"])

    released = date.today().isoformat()

    step(3, total, f"Writing the version into {VERSION_PY.name} and "
                   f"{VERSION_ISS.name}")
    write_version_py(version, released)
    write_version_iss(version)
    print(f"    version {version}, released {released}")

    step(4, total, "Building the app with PyInstaller")
    run([sys.executable, "-m", "PyInstaller", str(SPEC), "--noconfirm"])

    step(5, total, "Compiling the installer with Inno Setup")
    run([str(iscc), str(SETUP_ISS)])

    # --- 6. Hash the artifact we actually produced ------------------------
    step(6, total, "Hashing the installer")
    installer = OUTPUT_DIR / f"ClientTimer2_Setup_{version}.exe"
    if not installer.exists():
        raise ReleaseError(
            f"the installer is missing: {installer}\nISCC reported success, so "
            f"check OutputBaseFilename in the .iss still matches this name.")
    size = installer.stat().st_size
    if size < 1_000_000:
        raise ReleaseError(f"{installer.name} is only {size:,} bytes — that is "
                           f"not a complete installer.")
    digest = sha256_of(installer)
    print(f"    {installer.name}  ({size:,} bytes)")
    print(f"    sha256 {digest}")

    # --- 7. Derive the manifest from the artifact -------------------------
    # Every field here comes from the file that was just built, which is the
    # whole point: the URL cannot name a version the exe does not have, and
    # the digest cannot be stale.
    step(7, total, "Writing latest.json")
    url = (f"https://github.com/{REPO}/releases/download/"
           f"{version}/{installer.name}")
    MANIFEST.write_text(
        json.dumps({
            "version": version,
            "released": released,
            "url": url,
            "sha256": digest,
            "notes": args.notes.strip(),
        }, indent=2) + "\n", encoding="utf-8")
    print(f"    {url}")

    step(8, total, "Done — the rest is manual, in this order")
    if args.rebuild:
        # A rebuild has an extra step a normal release does not, and it is
        # the easy one to skip: the release ALREADY EXISTS with an asset on
        # it. Uploading beside the old file leaves two, and the sha256 just
        # written matches only one of them.
        step_two = f"""2. The `{version}` release already exists. Open it, DELETE the old
       asset, then upload the new one:
           {installer}
       https://github.com/{REPO}/releases/tag/{version}

       Do not leave both. latest.json now carries a sha256 that matches
       ONLY the file above, so the wrong asset fails every download."""
    else:
        step_two = f"""2. Create the GitHub release, tag *exactly* `{version}`, and upload:
           {installer}
       https://github.com/{REPO}/releases/new"""
    print(f"""
    The build is finished and nothing has left this machine yet.

    1. Commit:
           git add -A && git commit -m "Release {version}"

    {step_two}

    3. Only once that asset is live, push:
           git push

    Step 3 is what arms the update for everyone, so it goes last. Pushing
    latest.json before the asset exists tells every running app to download
    a file that is not there.
""", flush=True)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ReleaseError as exc:
        print(f"\nrelease failed: {exc}\n", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\ninterrupted\n", file=sys.stderr)
        sys.exit(130)
