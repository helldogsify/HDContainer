# -*- coding: utf-8 -*-
"""Command line for the HDContainer LAN link (for scripts and Claude sessions on this PC).

Acts as THIS device with the pairing keys of the HDContainer installed for the current Windows
user (%APPDATA%\\HDContainer\\HDContainer_lan.json, read only, never written). Works whether or not
the tray app runs; the other device must run HDContainer with the link switched on.

  python lan_cli.py peers                         paired devices: online, route (lan / far), address
  python lan_cli.py drives DEVICE                 drives and known folders of DEVICE
  python lan_cli.py ls DEVICE PATH                list a folder (dirs end with \\)
  python lan_cli.py get DEVICE REMOTE LOCAL       file or folder -> LOCAL (an existing folder: put inside it)
  python lan_cli.py put DEVICE LOCAL REMOTE       file or folder -> REMOTE (an existing folder: put inside it)
  python lan_cli.py mkdir DEVICE PATH
  python lan_cli.py mv DEVICE SRC DST             rename / move on DEVICE (same drive)
  python lan_cli.py rm DEVICE PATH [PATH...]      to the Recycle Bin of DEVICE
DEVICE = name as shown in Settings -> Devices (case-insensitive) or its id. Exit code 0 = done,
1 = failed (the reason on stderr), 2 = bad arguments. --json prints machine-readable output.
"""
import json
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lan          # noqa: E402


def _data_dir():
    return os.path.join(os.environ.get("APPDATA") or "", "HDContainer")


def _node():
    return lan.Node(_data_dir(), lambda m: None, lambda: "", "cli", readonly=True)


def _find(node, name):
    with node.store.lock:
        peers = dict(node.store.peers)
    for pid, p in peers.items():
        if name.lower() in (pid, (p.get("name") or "").lower()):
            return pid, p
    raise SystemExit(_fail("no paired device %r (see: peers)" % name))


def _fail(msg):
    sys.stderr.write("error: %s\n" % msg)
    return 1


def _remote_isdir(ch, path):
    try:
        return bool(ch.call({"op": "stat", "path": path}).get("dir"))
    except lan.LinkError as ex:
        if ex.code == "missing":
            return None
        raise


def cmd_peers(node, args, as_json):
    out = []
    with node.store.lock:
        peers = dict(node.store.peers)

    def probe(pid, p, row):
        t = time.time()
        try:
            ch = node.open(pid)
            ch.call({"op": "ping"})
            row.update(online=True, route="lan" if lan.is_private(ch.addr[0]) else "far", addr=ch.addr[0],
                       ms=int((time.time() - t) * 1000))
            ch.close()
        except lan.LinkError as ex:
            row.update(online=False, error=ex.code)
        except Exception as ex:
            row.update(online=False, error=str(ex))
    threads = []
    for pid, p in peers.items():
        row = {"name": p.get("name", ""), "id": pid}
        out.append(row)
        th = threading.Thread(target=probe, args=(pid, p, row), daemon=True)
        th.start()
        threads.append(th)
    for th in threads:
        th.join(15)
    if as_json:
        print(json.dumps(out, ensure_ascii=False, indent=1))
    else:
        if not out:
            print("no paired devices")
        for r in out:
            st = ("online %s %s %d ms" % (r["route"], r["addr"], r["ms"])) if r.get("online") \
                else "offline (%s)" % r.get("error", "")
            print("%-20s %s" % (r["name"], st))
    return 0


def run(argv):
    as_json = "--json" in argv
    argv = [a for a in argv if a != "--json"]
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__)
        return 2 if not argv else 0
    cmd, rest = argv[0], argv[1:]
    node = _node()
    if cmd == "peers":
        return cmd_peers(node, rest, as_json)
    need = {"drives": 1, "ls": 2, "get": 3, "put": 3, "mkdir": 2, "mv": 3, "rm": 2}
    if cmd not in need or len(rest) < need[cmd]:
        print(__doc__)
        return 2
    pid, _p = _find(node, rest[0])
    try:
        ch = node.open(pid)
    except lan.LinkError as ex:
        return _fail("%s: %s" % (rest[0], ex.code))
    try:
        if cmd == "drives":
            info = ch.call({"op": "info"})
            if as_json:
                print(json.dumps({"drives": info["drives"], "places": info["places"]}, ensure_ascii=False, indent=1))
            else:
                for d in info["drives"]:
                    print("%-4s %-10s %-24s free %.1f GB of %.1f GB" % (d["path"], d["type"], d["label"],
                                                                     d["free"] / 2 ** 30, d["total"] / 2 ** 30))
                for p in info["places"]:
                    print("%-10s %s" % (p["key"], p["path"]))
        elif cmd == "ls":
            items, _ = ch.call_paged({"op": "list", "path": rest[1]})
            items.sort(key=lambda i: (not i[1], i[0].lower()))
            if as_json:
                print(json.dumps([{"name": i[0], "dir": bool(i[1]), "size": i[2], "mtime": i[3], "hidden": bool(i[4])}
                                  for i in items], ensure_ascii=False, indent=1))
            else:
                for name, isdir, size, mtime, hidden in items:
                    print("%s  %12s  %s%s" % (time.strftime("%Y-%m-%d %H:%M", time.localtime(mtime)) if mtime else " " * 16,
                                             "" if isdir else size, name, "\\" if isdir else ""))
        elif cmd == "get":
            remote, local = rest[1], rest[2]
            isdir = _remote_isdir(ch, remote)
            if isdir is None:
                return _fail("not found on %s: %s" % (rest[0], remote))
            if os.path.isdir(local):
                local = os.path.join(local, os.path.basename(remote.rstrip("\\")))
            total = 0
            if isdir:
                os.makedirs(local, exist_ok=True)
                items, _ = ch.call_paged({"op": "walk", "path": remote})
                for rel, d, size, _m in items:
                    lp = os.path.join(local, rel)
                    if d:
                        os.makedirs(lp, exist_ok=True)
                    else:
                        os.makedirs(os.path.dirname(lp), exist_ok=True)
                        total += lan.download_file(ch, os.path.join(remote, rel), lp)
            else:
                total = lan.download_file(ch, remote, local)
            print("%s (%d bytes)" % (local, total))
        elif cmd == "put":
            local, remote = rest[1], rest[2]
            if not os.path.exists(local):
                return _fail("no local %s" % local)
            if _remote_isdir(ch, remote):
                remote = os.path.join(remote, os.path.basename(local.rstrip("\\/")))
            total = 0
            if os.path.isdir(local):
                ch.call({"op": "mkdir", "path": remote, "ok_exists": True})
                for dirpath, dirnames, filenames in os.walk(local):
                    rel = os.path.relpath(dirpath, local)
                    rel = "" if rel == "." else rel
                    for d in dirnames:
                        ch.call({"op": "mkdir", "path": os.path.join(remote, rel, d), "ok_exists": True})
                    for f in filenames:
                        lp = os.path.join(dirpath, f)
                        lan.upload_file(ch, lp, os.path.join(remote, rel, f), mkdirs=True)
                        total += os.path.getsize(lp)
            else:
                lan.upload_file(ch, local, remote)
                total = os.path.getsize(local)
            print("%s (%d bytes)" % (remote, total))
        elif cmd == "mkdir":
            ch.call({"op": "mkdir", "path": rest[1], "ok_exists": True})
        elif cmd == "mv":
            ch.call({"op": "rename", "src": rest[1], "dst": rest[2]})
        elif cmd == "rm":
            ch.call({"op": "delete", "paths": rest[1:]})
        return 0
    except lan.LinkError as ex:
        return _fail("%s (%s)" % (ex, ex.code))
    except OSError as ex:
        return _fail(str(ex))
    finally:
        ch.close()


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    sys.exit(run(sys.argv[1:]))
