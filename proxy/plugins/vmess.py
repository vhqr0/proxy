from typing import Callable, Iterable
from time import time
from random import randbytes, getrandbits
from functools import reduce, cached_property
from uuid import UUID
from zlib import crc32
from hashlib import sha256, md5
from Crypto.Hash.SHAKE128 import SHAKE128_XOF
from cryptography.hazmat.primitives.ciphers import Cipher
from cryptography.hazmat.primitives.ciphers.algorithms import AES
from cryptography.hazmat.primitives.ciphers.modes import ECB
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from proxy import (
    AsyncReader,
    BufferedAsyncReader,
    AsyncWriter,
    DictStruct,
    FixedFrame,
    st_uint8,
    st_uint16_be,
    st_uint32_be,
    st_uint64_be,
    ProxyClient,
    ProxyClientConfig,
)
from proxy.plugins.socks5 import st_socks5_str


VMESS_MAGIC = b"c48619fe-8f02-49e0-b9e9-edf763e17e21"
VMESS_KDF = b"VMess AEAD KDF"
VMESS_AID = b"AES Auth ID Encryption"
VMESS_REQ_LEN_KEY = b"VMess Header AEAD Key_Length"
VMESS_REQ_LEN_IV = b"VMess Header AEAD Nonce_Length"
VMESS_REQ_KEY = b"VMess Header AEAD Key"
VMESS_REQ_IV = b"VMess Header AEAD Nonce"
VMESS_RESP_LEN_KEY = b"AEAD Resp Header Len Key"
VMESS_RESP_LEN_IV = b"AEAD Resp Header Len IV"
VMESS_RESP_KEY = b"AEAD Resp Header Key"
VMESS_RESP_IV = b"AEAD Resp Header IV"


def derive_hash_fn(
    hash_fn: Callable[[bytes], bytes], key: bytes
) -> Callable[[bytes], bytes]:
    """Given a hash fn and a key, derive a new hash fn based on hmac."""
    if len(key) > 64:
        key = hash_fn(key)
    _ikey = bytearray(64)
    _okey = bytearray(64)
    for i in range(64):
        _ikey[i] = 0x36
        _okey[i] = 0x5C
    for i, c in enumerate(key):
        _ikey[i] ^= c
        _okey[i] ^= c
    ikey = bytes(_ikey)
    okey = bytes(_okey)

    def new_hasn_fn(data: bytes) -> bytes:
        return hash_fn(ikey + hash_fn(okey + data))

    return new_hasn_fn


def sha256_hash(data: bytes) -> bytes:
    return sha256(data).digest()


def vmess_hash(keys: Iterable[bytes], data: bytes) -> bytes:
    hash_fn = reduce(derive_hash_fn, keys, sha256_hash)
    return hash_fn(data)


def fnv1a(data: bytes) -> int:
    r = 0x811C9DC5
    p = 0x01000193
    m = 0xFFFFFFFF
    for c in data:
        r = ((c ^ r) * p) & m
    return r


def aesecb_encrypt(key: bytes, data: bytes) -> bytes:
    return Cipher(AES(key), ECB()).encryptor().update(data)


def aesgcm_encrypt(key: bytes, iv: bytes, data: bytes, aad: bytes) -> bytes:
    return AESGCM(key).encrypt(iv, data, aad)


def aesgcm_decrypt(key: bytes, iv: bytes, data: bytes, aad: bytes) -> bytes:
    return AESGCM(key).decrypt(iv, data, aad)


class VMessID(UUID):
    @cached_property
    def cmd_key(self):
        return md5(self.bytes + VMESS_MAGIC).digest()

    @cached_property
    def auth_key(self):
        return vmess_hash([VMESS_KDF, VMESS_AID], self.cmd_key)[:16]

    def encrypt_req(self, req: bytes):
        req += st_uint32_be.pack_one(fnv1a(req))
        aid = st_uint64_be.pack_one(time()) + randbytes(4)
        aid += st_uint32_be.pack_one(crc32(aid))
        eaid = aesecb_encrypt(self.auth_key, aid)
        nonce = randbytes(8)
        req_len = st_uint16_be.pack_one(len(req))
        elen_key = vmess_hash([VMESS_KDF, VMESS_REQ_LEN_KEY, eaid, nonce], self.cmd_key)
        elen_iv = vmess_hash([VMESS_KDF, VMESS_REQ_LEN_IV, eaid, nonce], self.cmd_key)
        elen = aesgcm_encrypt(elen_key[:16], elen_iv[:12], req_len, eaid)
        ereq_key = vmess_hash([VMESS_KDF, VMESS_REQ_KEY, eaid, nonce], self.cmd_key)
        ereq_iv = vmess_hash([VMESS_KDF, VMESS_REQ_IV, eaid, nonce], self.cmd_key)
        ereq = aesgcm_encrypt(ereq_key[:16], ereq_iv[:12], req, eaid)
        return eaid + elen + nonce + ereq


class VMessCryptor:
    def __init__(self, key: bytes, iv: bytes, count=0):
        self.shake = SHAKE128_XOF(iv)
        self.aead = AESGCM(key)
        self.iv = iv[2:12]
        self.count = count

    def next_iv(self) -> bytes:
        iv = st_uint16_be.pack_one(self.count) + self.iv
        self.count += 1
        return iv

    def mask_len(self, _len: int) -> int:
        return _len ^ st_uint16_be.unpack_one(self.shake.read(2))

    def encrypt_len(self, _len: int) -> bytes:
        return st_uint16_be.pack_one(self.mask_len(_len))

    def decrypt_len(self, elen: bytes) -> int:
        return self.mask_len(st_uint16_be.unpack_one(elen))

    def encrypt(self, data: bytes) -> bytes:
        return self.aead.encrypt(self.next_iv(), data, b"")

    def decrypt(self, data: bytes) -> bytes:
        return self.aead.decrypt(self.next_iv(), data, b"")

    def encrypt_with_len(self, data: bytes) -> bytes:
        edata = self.encrypt(data)
        elen = self.encrypt_len(len(edata))
        return elen + edata


st_vmess_req = DictStruct(
    [
        ("ver", st_uint8),
        ("iv", FixedFrame(16)),
        ("key", FixedFrame(16)),
        ("v", st_uint8),
        ("opt", st_uint8),
        ("plen_sec", st_uint8),
        ("keep", st_uint8),
        ("port", st_uint16_be),
        ("atype", st_uint8),
        ("host", st_socks5_str),
    ]
)


class VMessReader(BufferedAsyncReader):
    def __init__(self, key: bytes, iv: bytes, verify: int, reader: AsyncReader):
        self.key = key
        self.iv = iv
        self.verify = verify
        self.reader = reader
        self.cryptor = VMessCryptor(key, iv)
        self.wait_resp = True

    async def read_decrypt_resp_async(self):
        elen = await self.reader.readexactly_async(18)
        elen_key = vmess_hash([VMESS_KDF, VMESS_RESP_LEN_KEY], self.key)
        elen_iv = vmess_hash([VMESS_KDF, VMESS_RESP_LEN_IV], self.iv)
        _len = st_uint16_be.unpack_one(
            aesgcm_decrypt(elen_key[:16], elen_iv[:12], elen, b"")
        )
        eresp = await self.reader.readexactly_async(_len + 16)
        eresp_key = vmess_hash([VMESS_KDF, VMESS_RESP_KEY], self.key)
        eresp_iv = vmess_hash([VMESS_KDF, VMESS_RESP_IV], self.iv)
        resp = aesgcm_decrypt(eresp_key[:16], eresp_iv[:12], eresp, b"")
        if resp[0] != self.verify or resp[1:] != b"\x00\x00\x00":
            raise Exception("Invalid vmess resp")

    async def read1_async(self) -> bytes:
        if self.wait_resp:
            self.wait_resp = False
            await self.read_decrypt_resp_async()
        elen = await self.reader.readexactly_async(2)
        edata = await self.reader.readexactly_async(self.cryptor.decrypt_len(elen))
        return self.cryptor.decrypt(edata)


class VMessWriter(AsyncWriter):
    def __init__(
        self,
        id: VMessID,
        key: bytes,
        iv: bytes,
        verify: int,
        host: str,
        port: int,
        writer: AsyncWriter,
    ):
        self.id = id
        self.key = key
        self.iv = iv
        self.verify = verify
        self.host = host
        self.port = port
        self.writer = writer
        self.cryptor = VMessCryptor(key, iv)
        self.wait_req = True

    def gen_req(self):
        plen = getrandbits(4)
        pad = randbytes(plen)
        req = st_vmess_req.pack_one(
            {
                "ver": 1,
                "iv": self.iv,
                "key": self.key,
                "v": self.verify,
                "opts": 5,  # M | S
                "plen_sec": (plen << 4) + 3,  # AESGCM
                "keep": 0,
                "cmd": 1,  # TCP
                "port": self.port,
                "atype": 2,  # DOMAIN
                "host": self.host,
            }
        )
        return req + pad

    async def write_async(self, data: bytes):
        if self.wait_req:
            self.wait_req = False
            req = self.gen_req()
            ereq = self.id.encrypt_req(req)
            edata = self.cryptor.encrypt_with_len(data)
            await self.writer.write_async(ereq + edata)
        else:
            await self.writer.write_async(self.cryptor.encrypt_with_len(data))


class VMessClient(ProxyClient):
    def __init__(self, id: VMessID):
        self.id = id

    async def wrap(
        self, reader: AsyncReader, writer: AsyncWriter, host: str, port: int
    ) -> tuple[AsyncReader, AsyncWriter]:
        key, iv = randbytes(16), randbytes(16)
        rkey, riv = sha256_hash(key)[:16], sha256_hash(key)[:16]
        verify = getrandbits(8)
        return (
            VMessReader(rkey, riv, verify, reader),
            VMessWriter(self.id, key, iv, verify, host, port, writer),
        )


class VMessClientConfig(ProxyClientConfig):
    type = "vmess"

    @classmethod
    def from_data(cls, data: dict) -> VMessClient:
        return VMessClient(id=VMessID(data["id"]))
