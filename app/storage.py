"""Saving small JSON files on a boat, where the power goes off without warning.

Every setting, waypoint, route, track, trip and calibration value this dashboard keeps lives in
a small JSON file on the Pi's SD card, and the boat's battery switch cuts that power with no
shutdown. Two rules follow, and this module is where both live.

**Writes are atomic.** `Path.write_text` truncates the file and then writes it, so power lost in
between leaves it empty or half-written. Here the data goes to a temporary file in the same
directory, is fsync'd, and only then renamed over the original, so the name only ever points at
a complete old file or a complete new one. The fsync is not optional: without it ext4 may make
the rename durable before the data, and a power cut then leaves an empty file with the right
name -- the very failure the rename was meant to prevent.

**An unreadable file is never silently replaced.** The loaders here all used to treat a damaged
file as "nothing saved yet", which is right for not crashing at boot -- but the next save then
wrote the defaults straight over it, and whatever was still recoverable in it was gone, with no
message anywhere. Now a damaged file is moved aside as `<name>.corrupt-<time>` and logged, and a
file with some bad records keeps the good ones and a copy of the original.

Failures to write are logged and reported, never raised: a full or failing card must not take
the helm display down with it. What was being saved is still in memory, and the next successful
save persists it.
"""
import json
import os
import shutil
import tempfile
import time
from pathlib import Path


def _log(message):
    print(f"[storage] {message}")


def _fsync_dir(directory):
    """Make a rename in `directory` durable. POSIX only -- Windows cannot open a directory."""
    if os.name != "posix":
        return
    try:
        fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def write_text_atomic(path, text):
    """Replace `path` with `text` so that it is always either the old file or the new one.
    Returns True on success; on failure logs why, leaves the old file untouched, returns False."""
    path = Path(path)
    tmp = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
        tmp = None
        _fsync_dir(path.parent)
        return True
    except OSError as exc:
        _log(f"could not save {path.name} ({exc}); keeping the previous copy")
        return False
    finally:
        if tmp is not None:
            try:
                os.unlink(tmp)
            except OSError:
                pass


def write_json(path, data, indent=None):
    return write_text_atomic(path, json.dumps(data, indent=indent))


def quarantine(path, reason, keep_original=False):
    """Set a damaged file aside where the next save cannot overwrite it.

    Moves it by default; with keep_original, copies it instead, for when the file is still partly
    usable and stays in place. Returns the new path, or None if even that failed."""
    path = Path(path)
    aside = path.with_name(f"{path.name}.corrupt-{time.strftime('%Y%m%d-%H%M%S')}")
    try:
        if keep_original:
            shutil.copy2(path, aside)
        else:
            os.replace(path, aside)
    except OSError as exc:
        _log(f"{path.name}: {reason}; could not set it aside either ({exc})")
        return None
    _log(f"{path.name}: {reason}; the original is kept as {aside.name}")
    return aside


def read_json(path, default):
    """The file's parsed contents, or `default` if it does not exist or cannot be read. A file
    that exists but cannot be read is quarantined first, so nothing recoverable is lost."""
    path = Path(path)
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:   # ValueError covers both bad JSON and bad UTF-8
        quarantine(path, f"unreadable ({exc.__class__.__name__}: {exc})")
        return default


def read_dict(path):
    """read_json for settings files: always returns a dict. Anything else is quarantined."""
    data = read_json(path, {})
    if isinstance(data, dict):
        return data
    quarantine(path, f"expected an object, found {type(data).__name__}")
    return {}


def read_records(path, factory):
    """A list of records, each built with factory(**record). A record that no longer fits the
    current shape (a field removed in an update, a hand-edit gone wrong) is skipped rather than
    taking the rest down with it, and a copy of the original file is kept so it can be recovered."""
    raw = read_json(path, [])
    if not isinstance(raw, list):
        quarantine(path, f"expected a list, found {type(raw).__name__}")
        return []
    good, bad = [], 0
    for item in raw:
        try:
            good.append(factory(**item))
        except (TypeError, ValueError, KeyError):
            bad += 1
    if bad:
        quarantine(path, f"{bad} of {len(raw)} records could not be read and were skipped", keep_original=True)
    return good
