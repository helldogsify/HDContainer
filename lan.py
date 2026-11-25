# -*- coding: utf-8 -*-
"""LAN link between the user's own HDContainer devices: the engine (no tk here).

- discovery: every device sends a UDP beacon to the local broadcast address every few
  seconds (and straight to the paired devices it knows); the beacon only says who is there;
- pairing, once per pair of devices: Diffie-Hellman, a 6-digit code shown on both screens,
  confirmed by the user on the device asked; both store a 256-bit pair key (DPAPI-protected);
- every connection: fresh nonces from both sides + the pair key -> two AES-256-GCM session
  keys; nothing but the plain hello travels unencrypted;
- a paired device gets full access to all drives (list, read, write, rename, delete to the
  Recycle Bin), as the user asked;
- a paired device not heard on the LAN is tried at its other addresses (Tailscale), so the
  same files open from away when Tailscale is up.
Threads only: callers on the tk side read snapshot() and the events queue from a timer.
"""
import ctypes
import json
import os
import queue
import secrets
import select
import shutil
import socket
import struct
import threading
import time
import uuid
from ctypes import wintypes

import jsonstore
import lan_crypto as lc

try:
    from voice_sys import protect, unprotect
except Exception:                                   # no DPAPI helper: keys stay plain hex
    protect = unprotect = None

PROTO = "hdc-lan1"
DISC_PORT = int(os.environ.get("HDC_LAN_DISC_PORT") or 47820)
SRV_PORT = int(os.environ.get("HDC_LAN_PORT") or 47821)
BEACON_S = 3.0          # broadcast period
ONLINE_S = 10.0         # a device unheard this long is offline on the LAN
PROBE_S = 30.0          # paired devices not on the LAN are tried at their other addresses this often
CHUNK = 1 << 20         # file data per frame
MAX_FRAME = CHUNK + 64
PLAIN_MAX = 8192
PAGE_BYTES = 256 * 1024
PAIR_WAIT_S = 120
CONNECT_S = 2.0
OP_TIMEOUT_S = 60.0
IDLE_S = 300.0
WALK_MAX = 300000
PART = ".hdcpart"


class LinkError(Exception):
    """code: offline / unpaired / auth / declined / busy / off / cancelled / closed / proto / exists / remote"""
    def __init__(self, code, msg=""):
        super().__init__(msg or code)
        self.code = code


# ---------------------------------------------------------------- framing
def _recv_exact(sock, n):
    buf = bytearray(n)
    view = memoryview(buf)
    got = 0
    while got < n:
        k = sock.recv_into(view[got:], n - got)
        if not k:
            raise LinkError("closed", "connection closed")
        got += k
    return bytes(buf)


def recv_frame(sock, limit):
    n = struct.unpack(">I", _recv_exact(sock, 4))[0]
    if n > limit:
        raise LinkError("proto", "frame too large")
    return _recv_exact(sock, n)


def send_frame(sock, b):
    sock.sendall(struct.pack(">I", len(b)) + b)


def send_plain(sock, obj):
    send_frame(sock, json.dumps(obj, ensure_ascii=False).encode("utf-8"))


def recv_plain(sock):
    try:
        return json.loads(recv_frame(sock, PLAIN_MAX).decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise LinkError("proto", "bad hello")


class Channel:
    """An authenticated, encrypted connection. Frames: b"J"+json, b"D"+data, b"E" (end of data)."""
    def __init__(self, sock, tx, rx, peer_id, addr):
        self.sock, self.tx, self.rx, self.peer_id, self.addr = sock, tx, rx, peer_id, addr
        self.peer_name = ""
        self.lock = threading.Lock()        # one request at a time on a shared channel

    def send_json(self, obj):
        send_frame(self.sock, self.tx.seal(b"J" + json.dumps(obj, ensure_ascii=False).encode("utf-8")))

    def send_data(self, b):
        send_frame(self.sock, self.tx.seal(b"D" + b))

    def send_end(self):
        send_frame(self.sock, self.tx.seal(b"E"))

    def recv(self):
        p = self.rx.open(recv_frame(self.sock, MAX_FRAME))
        return p[:1], p[1:]

    def recv_json(self):
        k, body = self.recv()
        if k != b"J":
            raise LinkError("proto", "json expected")
        return json.loads(body.decode("utf-8"))

    def reply(self):
        r = self.recv_json()
        if r.get("err"):
            raise LinkError(r.get("code") or "remote", r["err"])
        return r

    def call(self, req):
        self.send_json(req)
        return self.reply()

    def call_paged(self, req):
        """Requests whose answer comes in pages: {"items": [...], "more": 1} ... {"more": 0}."""
        self.send_json(req)
        items = []
        while True:
            r = self.reply()
            items.extend(r.get("items", []))
            if not r.get("more"):
                return items, r

    def close(self):
        for x in (self.sock, self.tx, self.rx):
            try:
                x.close()
            except Exception:
                pass


def send_paged(ch, items, extra=None):
    page, size = [], 0
    for it in items:
        n = len(json.dumps(it, ensure_ascii=False)) + 2
        if page and size + n > PAGE_BYTES:
            ch.send_json({"ok": 1, "items": page, "more": 1})
            page, size = [], 0
        page.append(it)
        size += n
    last = {"ok": 1, "items": page, "more": 0}
    last.update(extra or {})
    ch.send_json(last)


def session_keys(pair_key, n_c, n_s, id_c, id_s):
    k = lc.hkdf(pair_key, n_c + n_s, ("%s session %s %s" % (PROTO, id_c, id_s)).encode(), 64)
    return k[:32], k[32:]          # client->server, server->client


def pair_keys(shared, pub_c, pub_s, id_c, id_s):
    confirm = lc.hkdf(shared, pub_c + pub_s, ("%s pair-confirm" % PROTO).encode(), 64)
    key = lc.hkdf(shared, pub_c + pub_s, ("%s pair-key %s %s" % (PROTO, id_c, id_s)).encode(), 32)
    return confirm[:32], confirm[32:], key


# ---------------------------------------------------------------- addresses
def _ip_int(ip):
    try:
        return struct.unpack(">I", socket.inet_aton(ip))[0]
    except OSError:
        return None


def is_private(ip):
    v = _ip_int(ip)
    if v is None:
        return False
    return (v >> 24 == 10 or v >> 20 == 0xAC1 or v >> 16 == 0xC0A8)


def is_tailscale(ip):
    v = _ip_int(ip)
    return v is not None and v >> 22 == (100 << 2 | 1)        # 100.64.0.0/10


_IPS = [0.0, []]


def local_ipv4():
    """This PC's IPv4 addresses; cached for 15 s (the beacon asks twice every 3 s)."""
    now = time.monotonic()
    if now - _IPS[0] < 15.0:
        return list(_IPS[1])
    ips = set()
    try:
        for fam, _t, _p, _c, sa in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(sa[0])
    except OSError:
        pass
    out = sorted(ip for ip in ips if not ip.startswith(("127.", "169.254.")))
    _IPS[0], _IPS[1] = now, out
    return list(out)


# ---------------------------------------------------------------- server-side file system
_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_k32.GetDriveTypeW.argtypes = [wintypes.LPCWSTR]
_k32.GetVolumeInformationW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD, ctypes.c_void_p,
                                       ctypes.c_void_p, ctypes.c_void_p, wintypes.LPWSTR, wintypes.DWORD]
_k32.SetErrorMode.argtypes = [wintypes.UINT]
DRIVE_TYPES = {2: "removable", 3: "fixed", 4: "remote", 5: "cdrom", 6: "ram"}


def list_drives():
    out = []
    try:
        roots = os.listdrives()
    except Exception:
        roots = ["%s:\\" % c for c in "CDEFGHIJKLMNOPQRSTUVWXYZ" if os.path.exists("%s:\\" % c)]
    old = _k32.SetErrorMode(0x8001)               # no "insert a disk" boxes for empty readers
    try:
        for r in roots:
            t = DRIVE_TYPES.get(_k32.GetDriveTypeW(r), "other")
            d = {"path": r, "type": t, "label": "", "total": 0, "free": 0}
            if t in ("fixed", "removable", "ram"):
                buf = ctypes.create_unicode_buffer(261)
                if _k32.GetVolumeInformationW(r, buf, 261, None, None, None, None, 0):
                    d["label"] = buf.value
                try:
                    u = shutil.disk_usage(r)
                    d["total"], d["free"] = u.total, u.free
                except OSError:
                    pass
            out.append(d)
    finally:
        _k32.SetErrorMode(old)
    return out


class _GUID(ctypes.Structure):
    _fields_ = [("a", ctypes.c_ulong), ("b", ctypes.c_ushort), ("c", ctypes.c_ushort), ("d", ctypes.c_ubyte * 8)]


_FOLDERS = {"desktop": "B4BFCC3A-DB2C-424C-B029-7FE99A87C641",
            "downloads": "374DE290-123F-4565-9164-39C4925E467B",
            "documents": "FDD39AD0-238F-46AF-ADB4-6C85480369C7"}


def known_folder(name):
    try:
        g = _GUID.from_buffer_copy(uuid.UUID(_FOLDERS[name]).bytes_le)
        p = ctypes.c_wchar_p()
        if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(g), 0, None, ctypes.byref(p)) != 0:
            return ""
        try:
            return p.value or ""
        finally:
            ctypes.windll.ole32.CoTaskMemFree(p)
    except Exception:
        return ""


def places():
    out = [{"key": k, "path": known_folder(k)} for k in ("desktop", "downloads", "documents")]
    out.append({"key": "home", "path": os.path.expanduser("~")})
    return [p for p in out if p["path"] and os.path.isdir(p["path"])]


def _entry(e):
    try:
        st = e.stat(follow_symlinks=False)
        isdir = e.is_dir()
        hidden = 1 if getattr(st, "st_file_attributes", 0) & 6 else 0      # hidden | system
        return [e.name, 1 if isdir else 0, 0 if isdir else st.st_size, int(st.st_mtime), hidden]
    except OSError:
        return [e.name, 0, 0, 0, 1]


def list_dir(path):
    with os.scandir(path) as it:
        return [_entry(e) for e in it]


def walk(root):
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        rel = os.path.relpath(dirpath, root)
        rel = "" if rel == "." else rel
        for d in dirnames:
            out.append([os.path.join(rel, d), 1, 0, 0])
        for f in filenames:
            try:
                st = os.stat(os.path.join(dirpath, f))
                out.append([os.path.join(rel, f), 0, st.st_size, int(st.st_mtime)])
            except OSError:
                pass
        if len(out) > WALK_MAX:
            raise LinkError("too_many", "more than %d items" % WALK_MAX)
    return out


class _SHFILEOP(ctypes.Structure):
    _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT), ("pFrom", ctypes.c_void_p),
                ("pTo", ctypes.c_void_p), ("fFlags", wintypes.WORD), ("aborted", wintypes.BOOL),
                ("maps", ctypes.c_void_p), ("title", ctypes.c_void_p)]


def recycle(paths):
    """Into the Recycle Bin of this device (drives without one delete for good, as in Explorer)."""
    buf = ctypes.create_unicode_buffer("\0".join(paths) + "\0\0")
    op = _SHFILEOP()
    op.wFunc = 3                                             # FO_DELETE
    op.pFrom = ctypes.cast(buf, ctypes.c_void_p)
    op.fFlags = 0x40 | 0x10 | 0x400 | 0x4                   # ALLOWUNDO | NOCONFIRMATION | NOERRORUI | SILENT
    ole = ctypes.windll.ole32
    inited = ole.CoInitializeEx(None, 2) in (0, 1)
    try:
        rc = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    finally:
        if inited:
            ole.CoUninitialize()
    if rc != 0 or op.aborted:
        raise OSError("delete failed (code %d)" % rc)


def _check_path(p):
    if not isinstance(p, str) or not os.path.isabs(p):
        raise LinkError("proto", "absolute path expected")
    return p


# ---------------------------------------------------------------- paired devices store
class Store:
    """%APPDATA%\\HDContainer\\HDContainer_lan.json: this device's id and the paired devices
    (keys DPAPI-protected). In memory each peer also carries "_key" (bytes)."""
    def __init__(self, path, log, readonly=False):
        self.path, self.log, self.readonly = path, log, readonly
        self.lock = threading.RLock()
        data, st = jsonstore.load(path, lambda d: isinstance(d, dict) and isinstance(d.get("id"), str), log)
        if data is None:
            data = {"v": 1, "id": uuid.uuid4().hex, "peers": {}}
        self.id = data["id"]
        self.peers = {}
        for pid, p in (data.get("peers") or {}).items():
            p = dict(p)
            raw = p.get("key", "")
            try:
                hexkey = unprotect(raw) if (unprotect and raw.startswith("dpapi:")) else raw
                p["_key"] = bytes.fromhex(hexkey) if hexkey else b""
            except ValueError:
                p["_key"] = b""
            self.peers[pid] = p
        if st != jsonstore.OK and not readonly:
            self.save()

    def save(self):
        if self.readonly:
            return
        with self.lock:
            peers = {pid: {k: v for k, v in p.items() if not k.startswith("_")} for pid, p in self.peers.items()}
            try:
                jsonstore.save(self.path, {"v": 1, "id": self.id, "peers": peers})
            except Exception as ex:
                self.log("lan: saving %s failed: %r" % (self.path, ex))

    def add(self, pid, name, key, addrs, port):
        hexkey = key.hex()
        prot = protect(hexkey) if protect else ""
        with self.lock:
            self.peers[pid] = {"name": name, "key": prot or hexkey, "_key": key, "addrs": _uniq(addrs)[:6],
                               "port": int(port or SRV_PORT), "t": int(time.time())}
        self.save()

    def remove(self, pid):
        with self.lock:
            gone = self.peers.pop(pid, None)
        if gone is not None:
            self.save()
        return gone is not None

    def update(self, pid, **kw):
        changed = False
        with self.lock:
            p = self.peers.get(pid)
            if p is None:
                return
            for k, v in kw.items():
                if p.get(k) != v:
                    p[k] = v
                    changed = True
        if changed:
            self.save()


def _uniq(seq):
    out = []
    for x in seq:
        if x and x not in out:
            out.append(x)
    return out


# ---------------------------------------------------------------- pairing request (server side)
class PairRequest:
    """Created by the server thread, answered by the user on the tk side."""
    def __init__(self, pid, name, ip, code):
        self.pid, self.name, self.ip, self.code = pid, name, ip, code
        self.decided = threading.Event()
        self.accepted = False
        self.closed = False            # set when the request is over (answered, timed out, initiator gone)

    def answer(self, yes):
        self.accepted = bool(yes)
        self.decided.set()


# ---------------------------------------------------------------- the node
class Node:
    def __init__(self, data_dir, log, get_name, version, readonly=False):
        self.log = log
        self.get_name = get_name
        self.version = version
        self.store = Store(os.path.join(data_dir, "HDContainer_lan.json"), log, readonly)
        self.id = self.store.id
        self.events = queue.Queue()       # ("pair_ask", PairRequest) / ("changed", None)
        self.seen = {}                    # id -> {"name","ip","port","v","ips","t"}  (beacons)
        self.far = {}                     # paired id -> (addr, t) last good connection outside the LAN
        self._probe_t = {}
        self.lock = threading.RLock()
        self.running = False
        self.port = 0
        self._srv = None
        self._udp = None
        self._pair_req = None
        self._pair_block = {}             # ip -> time before which pairing requests are refused

    # ---- lifecycle
    def start(self):
        if self.running:
            return
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            srv.bind(("0.0.0.0", SRV_PORT))
        except OSError:
            srv.bind(("0.0.0.0", 0))
        srv.listen(16)
        self.port = srv.getsockname()[1]
        udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        udp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        udp.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        udp.bind(("0.0.0.0", DISC_PORT))
        udp.settimeout(5.0)                # stop() closes it, which ends a waiting recvfrom at once
        self._halt = threading.Event()     # set by stop(): the sleeping threads wake and exit
        self._srv, self._udp, self.running = srv, udp, True
        self._gen = getattr(self, "_gen", 0) + 1          # threads of an earlier start() see a new generation and exit
        for fn in (self._accept_loop, self._disc_rx, self._disc_tx, self._probe_loop):
            threading.Thread(target=fn, args=(self._gen,), daemon=True, name="lan-" + fn.__name__.strip("_")).start()
        self.log("lan: started, id %s, tcp %d, udp %d" % (self.id[:8], self.port, DISC_PORT))

    def stop(self):
        if not self.running:
            return
        self.running = False
        self._halt.set()
        for s in (self._srv, self._udp):
            try:
                s.close()
            except Exception:
                pass
        req = self._pair_req
        if req is not None:
            req.answer(False)
        self.log("lan: stopped")

    def name(self):
        try:
            return (self.get_name() or socket.gethostname())[:64]
        except Exception:
            return socket.gethostname()[:64]

    # ---- state for the UI
    def snapshot(self):
        """[{"id","name","paired","online","route","ip","revoked","v"}], paired first, by name."""
        now = time.time()
        out = []
        with self.lock:
            seen = dict(self.seen)
            far = dict(self.far)
        with self.store.lock:
            peers = {pid: dict(p) for pid, p in self.store.peers.items()}
        for pid, p in peers.items():
            s = seen.get(pid)
            lan = bool(s and now - s["t"] < ONLINE_S)
            f = far.get(pid)
            remote = bool(f and now - f[1] < PROBE_S * 1.5)
            out.append({"id": pid, "name": (s or {}).get("name") or p.get("name") or pid[:8], "paired": True,
                        "online": lan or remote, "route": "lan" if lan else ("far" if remote else ""),
                        "ip": s["ip"] if lan else (f[0][0] if remote else ""),
                        "revoked": bool(p.get("revoked")) or not p.get("_key"), "v": (s or {}).get("v", "")})
        for pid, s in seen.items():
            if pid in peers or now - s["t"] >= ONLINE_S:
                continue
            out.append({"id": pid, "name": s.get("name") or pid[:8], "paired": False, "online": True,
                        "route": "lan", "ip": s["ip"], "revoked": False, "v": s.get("v", "")})
        out.sort(key=lambda d: (not d["paired"], d["name"].lower()))
        return out

    # ---- discovery
    def _beacon(self, ask):
        return json.dumps({"p": PROTO, "id": self.id, "name": self.name(), "port": self.port,
                           "v": self.version, "ips": local_ipv4(), "q": 1 if ask else 0},
                          ensure_ascii=False).encode("utf-8")

    @staticmethod
    def _broadcast(data, src_ips, socks):
        """One broadcast socket per local address, kept between beacons in `socks` (was a new
        socket, bind and close per address every 3 s)."""
        for ip in list(socks):
            if ip not in src_ips:
                socks.pop(ip).close()
        for ip in src_ips:
            s = socks.get(ip)
            try:
                if s is None:
                    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                    s.bind((ip, 0))
                    socks[ip] = s
                s.sendto(data, ("255.255.255.255", DISC_PORT))
            except OSError:
                if s is not None:
                    s.close()
                socks.pop(ip, None)

    def _unicast(self, data, ip):
        """Through the bound discovery socket: no new socket per packet."""
        try:
            self._udp.sendto(data, (ip, DISC_PORT))
        except OSError:
            pass

    def _live(self, gen):
        return self.running and gen == self._gen

    def _disc_tx(self, gen):
        first, halt, socks = True, self._halt, {}
        while self._live(gen):
            data = self._beacon(first)
            self._broadcast(data, [ip for ip in local_ipv4() if is_private(ip)], socks)
            # unicast too: some routers drop broadcasts. A device heard lately gets it only at
            # the address its own beacon came from; the others at every known LAN address.
            now = time.time()
            with self.lock:
                seen = {pid: s["ip"] for pid, s in self.seen.items() if now - s["t"] < ONLINE_S}
            with self.store.lock:
                known = []
                for pid, p in self.store.peers.items():
                    known += [seen[pid]] if pid in seen else [a for a in p.get("addrs", []) if is_private(a)]
            for a in _uniq(known):
                self._unicast(data, a)
            first = False
            halt.wait(BEACON_S)
        for s in socks.values():
            s.close()

    def _disc_rx(self, gen):
        udp = self._udp
        while self._live(gen):
            try:
                data, (ip, _port) = udp.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                if not self._live(gen):
                    return
                time.sleep(0.5)
                continue
            try:
                d = json.loads(data.decode("utf-8"))
                if d.get("p") != PROTO or not isinstance(d.get("id"), str) or d["id"] == self.id:
                    continue
                pid = d["id"]
                with self.lock:
                    old = self.seen.get(pid)
                    new = old is None or time.time() - old["t"] >= ONLINE_S
                    self.seen[pid] = {"name": str(d.get("name", ""))[:64], "ip": ip, "port": int(d.get("port") or SRV_PORT),
                                      "v": str(d.get("v", ""))[:16], "ips": [x for x in d.get("ips", []) if isinstance(x, str)][:8],
                                      "t": time.time()}
                p = self.store.peers.get(pid)
                if p is not None:
                    addrs = _uniq([ip] + self.seen[pid]["ips"] + p.get("addrs", []))[:6]
                    self.store.update(pid, addrs=addrs, port=self.seen[pid]["port"], name=self.seen[pid]["name"] or p.get("name"))
                if d.get("q") or new:
                    self._unicast(self._beacon(False), ip)
                if new:
                    self.events.put(("changed", None))
            except Exception:
                continue

    def _probe_loop(self, gen):
        halt = self._halt
        while self._live(gen):
            halt.wait(5)
            if not self._live(gen):
                return
            now = time.time()
            with self.store.lock:
                pids = [pid for pid, p in self.store.peers.items() if p.get("_key") and not p.get("revoked")]
            for pid in pids:
                s = self.seen.get(pid)
                if s and now - s["t"] < ONLINE_S:
                    continue
                if now - self._probe_t.get(pid, 0) < PROBE_S:
                    continue
                self._probe_t[pid] = now
                try:
                    ch = self.open(pid, lan_only=False, far_only=True)
                    ch.call({"op": "ping"})
                    ch.close()
                except Exception:
                    pass

    # ---- client
    def open(self, pid, lan_only=False, far_only=False):
        """Authenticated channel to a paired device: the LAN address first, then the others."""
        with self.store.lock:
            p = dict(self.store.peers.get(pid) or {})
        if not p:
            raise LinkError("unpaired")
        if not p.get("_key"):
            raise LinkError("auth")
        now = time.time()
        cands = []
        s = self.seen.get(pid)
        if s and now - s["t"] < ONLINE_S and not far_only:
            cands.append((s["ip"], s["port"]))
        if not lan_only:
            for a in p.get("addrs", []):
                if far_only and is_private(a):
                    continue
                if (a, p.get("port", SRV_PORT)) not in cands:
                    cands.append((a, p.get("port", SRV_PORT)))
        err = None
        for addr in cands:
            try:
                sock = socket.create_connection(addr, CONNECT_S)
            except OSError as ex:
                err = ex
                continue
            try:
                ch = self._hello(sock, pid, p["_key"], addr)
            except LinkError as ex:
                sock.close()
                if ex.code in ("unpaired", "auth"):
                    self.store.update(pid, revoked=True)
                    self.events.put(("changed", None))
                    raise
                err = ex
                continue
            except (OSError, lc.AuthError) as ex:
                sock.close()
                err = ex
                continue
            if p.get("revoked"):
                self.store.update(pid, revoked=False)
            if not is_private(addr[0]):
                with self.lock:
                    self.far[pid] = (addr, time.time())
            return ch
        raise LinkError("offline", str(err or "no address"))

    def _tune(self, sock):
        try:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4 << 20)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 << 20)
        except OSError:
            pass

    def _hello(self, sock, pid, key, addr):
        self._tune(sock)
        sock.settimeout(15)
        n_c = secrets.token_bytes(16)
        send_plain(sock, {"p": PROTO, "op": "hello", "id": self.id, "n": n_c.hex()})
        r = recv_plain(sock)
        if r.get("err"):
            raise LinkError(r["err"])
        if r.get("id") != pid:
            raise LinkError("proto", "another device answered")
        n_s = bytes.fromhex(r.get("n", ""))
        if len(n_s) != 16:
            raise LinkError("proto", "bad nonce")
        c2s, s2c = session_keys(key, n_c, n_s, self.id, pid)
        ch = Channel(sock, lc.Gcm(c2s, b"c2s "), lc.Gcm(s2c, b"s2c "), pid, addr)
        ch.send_json({"auth": 1})
        try:
            r = ch.recv_json()
        except lc.AuthError:
            ch.close()
            raise LinkError("auth")
        ch.peer_name = str(r.get("name", ""))
        sock.settimeout(OP_TIMEOUT_S)
        return ch

    def pair(self, pid, on_code, cancel):
        """Initiator side; runs in a worker thread. on_code(code) is called from this thread."""
        s = self.seen.get(pid)
        if not s:
            raise LinkError("offline")
        sock = socket.create_connection((s["ip"], s["port"]), CONNECT_S)
        try:
            sock.settimeout(15)
            priv, pub_c = lc.dh_keypair()
            send_plain(sock, {"p": PROTO, "op": "pair", "id": self.id, "name": self.name(),
                              "pub": pub_c.hex(), "port": self.port, "ips": local_ipv4()})
            r = recv_plain(sock)
            if r.get("err"):
                raise LinkError(r["err"])
            if r.get("id") != pid:
                raise LinkError("proto", "another device answered")
            pub_s = bytes.fromhex(r.get("pub", ""))
            shared = lc.dh_shared(priv, pub_s)
            on_code(lc.pair_code(shared, pub_c, pub_s))
            k_c2s, k_s2c, key = pair_keys(shared, pub_c, pub_s, self.id, pid)
            tx, rx = lc.Gcm(k_c2s, b"c2s "), lc.Gcm(k_s2c, b"s2c ")
            deadline = time.time() + PAIR_WAIT_S + 15
            while True:
                if cancel.is_set():
                    raise LinkError("cancelled")
                if time.time() > deadline:
                    raise LinkError("timeout")
                if select.select([sock], [], [], 0.3)[0]:
                    break
            try:
                ans = json.loads(rx.open(recv_frame(sock, PLAIN_MAX)))
            except lc.AuthError:
                raise LinkError("auth")
            if not ans.get("ok"):
                raise LinkError("declined")
            send_frame(sock, tx.seal(json.dumps({"ok": 1}).encode()))
            self.store.add(pid, str(r.get("name") or s.get("name") or "")[:64], key,
                           [s["ip"]] + s.get("ips", []), r.get("port") or s["port"])
            self.events.put(("changed", None))
            self.log("lan: paired with %s (%s)" % (r.get("name"), pid[:8]))
        finally:
            sock.close()

    def unpair(self, pid):
        """Tell the other device (best effort, it may be away), then forget it here."""
        try:
            ch = self.open(pid)
            try:
                ch.call({"op": "unpair"})
            finally:
                ch.close()
        except Exception as ex:
            self.log("lan: unpair notice to %s failed: %r" % (pid[:8], ex))
        self.store.remove(pid)
        with self.lock:
            self.far.pop(pid, None)
        self.events.put(("changed", None))

    # ---- server
    def _accept_loop(self, gen):
        srv = self._srv
        while self._live(gen):
            try:
                sock, addr = srv.accept()
            except OSError:
                if not self._live(gen):
                    return
                time.sleep(0.2)
                continue
            threading.Thread(target=self._serve, args=(sock, addr), daemon=True, name="lan-conn").start()

    def _serve(self, sock, addr):
        try:
            self._tune(sock)
            sock.settimeout(15)
            hello = recv_plain(sock)
            if hello.get("p") != PROTO:
                return
            if hello.get("op") == "pair":
                self._serve_pair(sock, addr, hello)
            elif hello.get("op") == "hello":
                self._serve_link(sock, addr, hello)
        except (LinkError, OSError, lc.AuthError, ValueError):
            pass
        except Exception as ex:
            self.log("lan: connection from %s failed: %r" % (addr[0], ex))
        finally:
            try:
                sock.close()
            except Exception:
                pass

    def _serve_link(self, sock, addr, hello):
        pid = hello.get("id")
        with self.store.lock:
            p = self.store.peers.get(pid)
            key = p.get("_key") if p else b""
        if not key:
            send_plain(sock, {"err": "unpaired"})
            return
        n_c = bytes.fromhex(hello.get("n", ""))
        if len(n_c) != 16:
            return
        n_s = secrets.token_bytes(16)
        send_plain(sock, {"id": self.id, "n": n_s.hex()})
        c2s, s2c = session_keys(key, n_c, n_s, pid, self.id)
        ch = Channel(sock, lc.Gcm(s2c, b"s2c "), lc.Gcm(c2s, b"c2s "), pid, addr)
        try:
            ch.recv_json()
        except lc.AuthError:
            self.log("lan: wrong key from %s (%s)" % (addr[0], pid[:8]))
            send_plain(sock, {"err": "auth"})
            return
        ch.send_json({"ok": 1, "name": self.name(), "v": self.version})
        if p.get("revoked"):
            self.store.update(pid, revoked=False)
        if not is_private(addr[0]):
            with self.lock:
                self.far[pid] = ((addr[0], p.get("port", SRV_PORT)), time.time())
        sock.settimeout(IDLE_S)
        while self.running:
            try:
                req = ch.recv_json()
            except (LinkError, OSError, lc.AuthError):
                return
            sock.settimeout(OP_TIMEOUT_S)
            try:
                self._op(ch, req)
            except _Broken:
                return
            except LinkError as ex:
                ch.send_json({"err": str(ex), "code": ex.code})
            except FileExistsError as ex:
                ch.send_json({"err": str(ex), "code": "exists"})
            except PermissionError as ex:
                ch.send_json({"err": str(ex), "code": "denied"})
            except FileNotFoundError as ex:
                ch.send_json({"err": str(ex), "code": "missing"})
            except Exception as ex:
                ch.send_json({"err": str(ex) or type(ex).__name__, "code": "remote"})
            sock.settimeout(IDLE_S)

    def _op(self, ch, req):
        op = req.get("op")
        if op == "ping":
            ch.send_json({"ok": 1})
        elif op == "info":
            ch.send_json({"ok": 1, "name": self.name(), "v": self.version, "drives": list_drives(),
                          "places": places(), "sep": os.sep})
        elif op == "list":
            send_paged(ch, list_dir(_check_path(req.get("path"))))
        elif op == "walk":
            send_paged(ch, walk(_check_path(req.get("path"))))
        elif op == "stat":
            p = _check_path(req.get("path"))
            st = os.stat(p)
            ch.send_json({"ok": 1, "dir": 1 if os.path.isdir(p) else 0, "size": st.st_size, "mtime": int(st.st_mtime)})
        elif op == "get":
            self._op_get(ch, req)
        elif op == "put":
            self._op_put(ch, req)
        elif op == "mkdir":
            os.makedirs(_check_path(req.get("path")), exist_ok=bool(req.get("ok_exists")))
            ch.send_json({"ok": 1})
        elif op == "rename":
            src, dst = _check_path(req.get("src")), _check_path(req.get("dst"))
            if os.path.exists(dst) and os.path.normcase(src) != os.path.normcase(dst):
                raise FileExistsError("already exists: " + os.path.basename(dst))
            os.rename(src, dst)
            ch.send_json({"ok": 1})
        elif op == "delete":
            paths = [_check_path(p) for p in req.get("paths", [])]
            if paths:
                recycle(paths)
            ch.send_json({"ok": 1})
        elif op == "unpair":
            ch.send_json({"ok": 1})
            self.store.remove(ch.peer_id)
            self.events.put(("changed", None))
            self.log("lan: %s unpaired itself" % ch.peer_id[:8])
            raise _Broken()
        else:
            raise LinkError("proto", "unknown operation")

    def _op_get(self, ch, req):
        path = _check_path(req.get("path"))
        f = open(path, "rb")
        try:
            st = os.fstat(f.fileno())
            off = max(0, int(req.get("offset") or 0))
            ch.send_json({"ok": 1, "size": st.st_size, "mtime": int(st.st_mtime)})
            try:
                f.seek(off)
                while True:
                    b = f.read(CHUNK)
                    if not b:
                        break
                    ch.send_data(b)
                ch.send_end()
            except Exception:
                raise _Broken()            # mid-stream: the only clean signal left is closing
        finally:
            f.close()

    def _op_put(self, ch, req):
        path = _check_path(req.get("path"))
        if os.path.exists(path) and not req.get("overwrite"):
            raise FileExistsError("already exists: " + os.path.basename(path))
        if req.get("mkdirs"):
            os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + PART
        f = open(tmp, "wb")
        ch.send_json({"ok": 1})
        err = None
        try:
            while True:
                try:
                    k, b = ch.recv()
                except Exception:
                    raise _Broken()
                if k == b"D":
                    if err is None:
                        try:
                            f.write(b)
                        except OSError as ex:
                            err = ex
                elif k == b"E":
                    break
                else:
                    raise _Broken()
            f.close()
            if err is None:
                try:
                    os.replace(tmp, path)
                    if req.get("mtime"):
                        os.utime(path, (time.time(), float(req["mtime"])))
                except OSError as ex:
                    err = ex
        finally:
            if not f.closed:
                f.close()
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass
        if err is not None:
            raise err
        ch.send_json({"ok": 1})

    def _serve_pair(self, sock, addr, hello):
        ip = addr[0]
        with self.lock:
            if self._pair_req is not None or time.time() < self._pair_block.get(ip, 0):
                send_plain(sock, {"err": "busy"})
                return
            self._pair_req = True                      # reserve
        req = None
        try:
            pid = str(hello.get("id", ""))
            pub_c = bytes.fromhex(hello.get("pub", ""))
            if not pid or pid == self.id:
                return
            priv, pub_s = lc.dh_keypair()
            shared = lc.dh_shared(priv, pub_c)
            send_plain(sock, {"id": self.id, "name": self.name(), "pub": pub_s.hex(), "port": self.port})
            req = PairRequest(pid, str(hello.get("name", ""))[:64], ip, lc.pair_code(shared, pub_c, pub_s))
            with self.lock:
                self._pair_req = req
            self.events.put(("pair_ask", req))
            deadline = time.time() + PAIR_WAIT_S
            while not req.decided.is_set() and self.running:
                if time.time() > deadline:
                    break
                if select.select([sock], [], [], 0.3)[0]:          # the initiator sends nothing now: readable = gone
                    try:
                        gone = not sock.recv(1, socket.MSG_PEEK)
                    except OSError:
                        gone = True
                    if gone:
                        break
            k_c2s, k_s2c, key = pair_keys(shared, pub_c, pub_s, pid, self.id)
            tx, rx = lc.Gcm(k_s2c, b"s2c "), lc.Gcm(k_c2s, b"c2s ")
            ok = req.decided.is_set() and req.accepted
            try:
                send_frame(sock, tx.seal(json.dumps({"ok": 1 if ok else 0}).encode()))
            except OSError:
                return
            if not ok:
                with self.lock:
                    self._pair_block[ip] = time.time() + 10
                return
            sock.settimeout(15)
            ack = json.loads(rx.open(recv_frame(sock, PLAIN_MAX)))
            if ack.get("ok"):
                addrs = [ip] + [x for x in hello.get("ips", []) if isinstance(x, str)]
                self.store.add(pid, req.name, key, addrs, hello.get("port"))
                self.events.put(("changed", None))
                self.log("lan: paired with %s (%s), asked by it" % (req.name, pid[:8]))
        finally:
            if req is not None:
                req.closed = True
            with self.lock:
                self._pair_req = None


class _Broken(Exception):
    """The connection can't continue (mid-stream failure, unpaired): close it."""


# ---------------------------------------------------------------- client helpers (worker threads)
def download_file(ch, remote, local, progress=None, cancel=None, offset=0):
    """remote file -> local path (written to local + PART, swapped in at the end)."""
    ch.send_json({"op": "get", "path": remote, "offset": offset})
    head = ch.reply()
    tmp = local + PART
    done = 0
    try:
        with open(tmp, "wb") as f:
            while True:
                if cancel is not None and cancel.is_set():
                    raise LinkError("cancelled")
                k, b = ch.recv()
                if k == b"E":
                    break
                if k != b"D":
                    raise LinkError("proto", "data expected")
                f.write(b)
                done += len(b)
                if progress:
                    progress(len(b))
        os.replace(tmp, local)
        try:
            os.utime(local, (time.time(), head.get("mtime") or time.time()))
        except OSError:
            pass
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
    return head.get("size", done)


def upload_file(ch, local, remote, progress=None, cancel=None, overwrite=False, mkdirs=False):
    st = os.stat(local)
    ch.send_json({"op": "put", "path": remote, "size": st.st_size, "mtime": int(st.st_mtime),
                  "overwrite": overwrite, "mkdirs": mkdirs})
    ch.reply()
    with open(local, "rb") as f:
        while True:
            if cancel is not None and cancel.is_set():
                raise LinkError("cancelled")       # the caller closes the channel: the remote drops the .hdcpart
            b = f.read(CHUNK)
            if not b:
                break
            ch.send_data(b)
            if progress:
                progress(len(b))
    ch.send_end()
    ch.reply()


# ---------------------------------------------------------------- firewall
FW_RULE = "HDContainer LAN"


def firewall_rule_present():
    import subprocess
    try:
        r = subprocess.run(["netsh", "advfirewall", "firewall", "show", "rule", "name=" + FW_RULE],
                           capture_output=True, timeout=15, creationflags=0x08000000)
        return r.returncode == 0
    except Exception:
        return False


def firewall_allow(exe, log):
    """One elevated PowerShell (a UAC prompt where UAC asks): drop inbound BLOCK rules Windows made
    for this exe when its prompt was dismissed, add an allow rule limited to the local subnet and
    Tailscale. Returns True if the elevated process ran and the rule is there."""
    import base64
    import subprocess
    ps = ("$p = '%s'; "
          "Get-NetFirewallApplicationFilter | Where-Object { $_.Program -eq $p } | Get-NetFirewallRule | "
          "Where-Object { $_.Direction -eq 'Inbound' -and $_.Action -eq 'Block' } | Remove-NetFirewallRule; "
          "Get-NetFirewallRule -DisplayName '%s' -ErrorAction SilentlyContinue | Remove-NetFirewallRule; "
          "New-NetFirewallRule -DisplayName '%s' -Direction Inbound -Action Allow -Program $p "
          "-RemoteAddress LocalSubnet,100.64.0.0/10 -Profile Any | Out-Null"
          % (exe.replace("'", "''"), FW_RULE, FW_RULE))
    enc = base64.b64encode(ps.encode("utf-16-le")).decode()

    class SEI(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("fMask", ctypes.c_ulong), ("hwnd", wintypes.HWND),
                    ("lpVerb", wintypes.LPCWSTR), ("lpFile", wintypes.LPCWSTR), ("lpParameters", wintypes.LPCWSTR),
                    ("lpDirectory", wintypes.LPCWSTR), ("nShow", ctypes.c_int), ("hInstApp", wintypes.HINSTANCE),
                    ("lpIDList", ctypes.c_void_p), ("lpClass", wintypes.LPCWSTR), ("hkeyClass", wintypes.HKEY),
                    ("dwHotKey", wintypes.DWORD), ("hIcon", wintypes.HANDLE), ("hProcess", wintypes.HANDLE)]
    sei = SEI()
    sei.cbSize = ctypes.sizeof(SEI)
    sei.fMask = 0x40                                  # SEE_MASK_NOCLOSEPROCESS
    sei.lpVerb = "runas"
    sei.lpFile = "powershell.exe"
    sei.lpParameters = "-NoProfile -NonInteractive -WindowStyle Hidden -EncodedCommand " + enc
    sei.nShow = 0
    if not ctypes.windll.shell32.ShellExecuteExW(ctypes.byref(sei)):
        log("lan: firewall rule not added (elevation refused or failed: %d)" % ctypes.GetLastError())
        return False
    if sei.hProcess:
        ctypes.windll.kernel32.WaitForSingleObject(sei.hProcess, 60000)
        ctypes.windll.kernel32.CloseHandle(sei.hProcess)
    ok = firewall_rule_present()
    log("lan: firewall rule %s" % ("added" if ok else "still missing"))
    return ok
