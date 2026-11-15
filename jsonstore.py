"""Crash-safe JSON files for settings, containers and boxes.

load() never turns an unreadable file into "empty data" silently:
  - a good file is copied to <path>.bak once per load (the last known good state);
  - an unreadable file is kept as <path>.broken-<timestamp> and the .bak is tried;
  - the caller gets a status and must not overwrite the file with empty data
    when the status is "broken".
save() writes a temp file, fsyncs it and swaps it in, so a crash or power loss
never leaves a truncated file behind.
"""
import json
import os
import shutil
import time

OK, MISSING, RESTORED, BROKEN = "ok", "missing", "restored", "broken"


def save(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)
        f.flush()
        try:
            os.fsync(f.fileno())
        except Exception:
            pass
    os.replace(tmp, path)


def _read(path, valid):
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not valid(data):
        raise ValueError("unexpected content")
    return data


def load(path, valid, log=None):
    """Returns (data, status). data is None unless status is OK or RESTORED."""
    def _log(msg):
        if log:
            try:
                log(msg)
            except Exception:
                pass
    if not os.path.exists(path):
        return None, MISSING
    try:
        data = _read(path, valid)
    except Exception as ex:
        _log("jsonstore: %s unreadable: %r" % (os.path.basename(path), ex))
        try:
            shutil.copy2(path, "%s.broken-%s" % (path, time.strftime("%Y%m%d-%H%M%S")))
        except Exception as ex2:
            _log("jsonstore: keeping a copy failed: %r" % ex2)
        try:
            data = _read(path + ".bak", valid)
        except Exception:
            return None, BROKEN
        _log("jsonstore: %s restored from .bak" % os.path.basename(path))
        return data, RESTORED
    try:
        shutil.copy2(path, path + ".bak")
    except Exception:
        pass
    return data, OK
