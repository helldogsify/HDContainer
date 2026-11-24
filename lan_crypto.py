# -*- coding: utf-8 -*-
"""Crypto for the LAN link between the user's own devices, no third-party packages.

- key agreement: finite-field Diffie-Hellman over the 2048-bit MODP group of RFC 3526
  (Python's pow does it in a few ms);
- key derivation: HKDF-SHA256 (RFC 5869) from hmac/hashlib;
- traffic: AES-256-GCM from Windows CNG (bcrypt.dll) through ctypes: native speed,
  a frame of 1 MiB costs well under a millisecond.
A pairing code (6 digits) is derived from the DH transcript; the user compares it on both
screens, which rules out a man in the middle.
"""
import ctypes
import hashlib
import hmac
import secrets
import struct
from ctypes import wintypes

# RFC 3526, group 14 (2048-bit MODP), generator 2
_P = int(
    "FFFFFFFFFFFFFFFFC90FDAA22168C234C4C6628B80DC1CD129024E088A67CC74020BBEA63B139B22514A08798E3404DD"
    "EF9519B3CD3A431B302B0A6DF25F14374FE1356D6D51C245E485B576625E7EC6F44C42E9A637ED6B0BFF5CB6F406B7ED"
    "EE386BFB5A899FA5AE9F24117C4B1FE649286651ECE45B3DC2007CB8A163BF0598DA48361C55D39A69163FA8FD24CF5F"
    "83655D23DCA3AD961C62F356208552BB9ED529077096966D670C354E4ABC9804F1746C08CA18217C32905E462E36CE3B"
    "E39E772C180E86039B2783A2EC07A28FB5C55DF06F4C52C9DE2BCBF6955817183995497CEA956AE515D2261898FA0510"
    "15728E5A8AACAA68FFFFFFFFFFFFFFFF", 16)
_G = 2
PUB_LEN = 256


def dh_keypair():
    priv = secrets.randbits(512) | (1 << 511)
    return priv, pow(_G, priv, _P).to_bytes(PUB_LEN, "big")


def dh_shared(priv, peer_pub):
    y = int.from_bytes(peer_pub, "big")
    if len(peer_pub) != PUB_LEN or not 1 < y < _P - 1:
        raise ValueError("bad public key")
    z = pow(y, priv, _P)
    if z in (1, _P - 1):
        raise ValueError("weak shared secret")
    return z.to_bytes(PUB_LEN, "big")


def hkdf(ikm, salt, info, length):
    prk = hmac.new(salt or b"\0" * 32, ikm, hashlib.sha256).digest()
    out, t, i = b"", b"", 1
    while len(out) < length:
        t = hmac.new(prk, t + info + bytes([i]), hashlib.sha256).digest()
        out += t
        i += 1
    return out[:length]


def pair_code(shared, pub_a, pub_b):
    """6-digit code both screens show; pub_a/pub_b in a fixed order (initiator first)."""
    h = hashlib.sha256(b"hdc-lan-code" + shared + pub_a + pub_b).digest()
    return "%06d" % (int.from_bytes(h[:8], "big") % 1000000)


# ---------------------------------------------------------------- AES-GCM (CNG)
_bcrypt = ctypes.WinDLL("bcrypt")
_H = ctypes.c_void_p
_NTSTATUS = ctypes.c_long
STATUS_AUTH_TAG_MISMATCH = -1073700862     # 0xC000A002


class _AuthInfo(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.ULONG), ("dwInfoVersion", wintypes.ULONG),
                ("pbNonce", ctypes.c_void_p), ("cbNonce", wintypes.ULONG),
                ("pbAuthData", ctypes.c_void_p), ("cbAuthData", wintypes.ULONG),
                ("pbTag", ctypes.c_void_p), ("cbTag", wintypes.ULONG),
                ("pbMacContext", ctypes.c_void_p), ("cbMacContext", wintypes.ULONG),
                ("cbAAD", wintypes.ULONG), ("cbData", ctypes.c_ulonglong),
                ("dwFlags", wintypes.ULONG)]


def _d(fn, args):
    fn.restype = _NTSTATUS
    fn.argtypes = args
    return fn


_open = _d(_bcrypt.BCryptOpenAlgorithmProvider, [ctypes.POINTER(_H), wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.ULONG])
_setprop = _d(_bcrypt.BCryptSetProperty, [_H, wintypes.LPCWSTR, ctypes.c_void_p, wintypes.ULONG, wintypes.ULONG])
_genkey = _d(_bcrypt.BCryptGenerateSymmetricKey, [_H, ctypes.POINTER(_H), ctypes.c_void_p, wintypes.ULONG,
                                                  ctypes.c_char_p, wintypes.ULONG, wintypes.ULONG])
_destroy = _d(_bcrypt.BCryptDestroyKey, [_H])
_crypt_args = [_H, ctypes.c_void_p, wintypes.ULONG, ctypes.c_void_p, ctypes.c_void_p, wintypes.ULONG,
               ctypes.c_void_p, wintypes.ULONG, ctypes.POINTER(wintypes.ULONG), wintypes.ULONG]
_encrypt = _d(_bcrypt.BCryptEncrypt, _crypt_args)
_decrypt = _d(_bcrypt.BCryptDecrypt, _crypt_args)

_alg = None


def _algorithm():
    global _alg
    if _alg is None:
        h = _H()
        st = _open(ctypes.byref(h), "AES", None, 0)
        if st:
            raise OSError("BCryptOpenAlgorithmProvider 0x%08x" % (st & 0xFFFFFFFF))
        mode = ctypes.create_unicode_buffer("ChainingModeGCM")
        st = _setprop(h, "ChainingMode", mode, ctypes.sizeof(mode), 0)
        if st:
            raise OSError("BCryptSetProperty 0x%08x" % (st & 0xFFFFFFFF))
        _alg = h
    return _alg


class AuthError(Exception):
    pass


class Gcm:
    """AES-256-GCM with a 96-bit nonce = 4-byte prefix + 8-byte counter (one per direction)."""
    TAG = 16

    def __init__(self, key, prefix):
        if len(key) != 32 or len(prefix) != 4:
            raise ValueError("key/prefix size")
        self._key = _H()
        st = _genkey(_algorithm(), ctypes.byref(self._key), None, 0, key, len(key), 0)
        if st:
            raise OSError("BCryptGenerateSymmetricKey 0x%08x" % (st & 0xFFFFFFFF))
        self._prefix = prefix
        self._n = 0

    def _info(self, nonce, tag):
        info = _AuthInfo()
        info.cbSize = ctypes.sizeof(_AuthInfo)
        info.dwInfoVersion = 1
        info.pbNonce = ctypes.cast(nonce, ctypes.c_void_p)
        info.cbNonce = 12
        info.pbTag = ctypes.cast(tag, ctypes.c_void_p)
        info.cbTag = self.TAG
        return info

    def _next_nonce(self):
        n = self._n
        self._n += 1
        return ctypes.create_string_buffer(self._prefix + struct.pack(">Q", n), 12)

    def seal(self, data):
        nonce = self._next_nonce()
        tag = ctypes.create_string_buffer(self.TAG)
        info = self._info(nonce, tag)
        out = ctypes.create_string_buffer(len(data) or 1)
        got = wintypes.ULONG()
        st = _encrypt(self._key, data, len(data), ctypes.byref(info), None, 0,
                      out, len(data), ctypes.byref(got), 0)
        if st:
            raise OSError("BCryptEncrypt 0x%08x" % (st & 0xFFFFFFFF))
        return out.raw[:len(data)] + tag.raw

    def open(self, blob):
        if len(blob) < self.TAG:
            raise AuthError("short frame")
        data, tagb = blob[:-self.TAG], blob[-self.TAG:]
        nonce = self._next_nonce()
        tag = ctypes.create_string_buffer(tagb, self.TAG)
        info = self._info(nonce, tag)
        out = ctypes.create_string_buffer(len(data) or 1)
        got = wintypes.ULONG()
        st = _decrypt(self._key, data, len(data), ctypes.byref(info), None, 0,
                      out, len(data), ctypes.byref(got), 0)
        if st == STATUS_AUTH_TAG_MISMATCH:
            raise AuthError("tag mismatch")
        if st:
            raise OSError("BCryptDecrypt 0x%08x" % (st & 0xFFFFFFFF))
        return out.raw[:len(data)]

    def close(self):
        if self._key:
            _destroy(self._key)
            self._key = _H()

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass
