from hashlib import sha224
import ipaddress as ia

from proxy import (
    AsyncReader,
    AsyncWriter,
    ProxyClientStream,
    Stream,
    Struct,
    WrapStruct,
    DictStruct,
    FixedFrame,
    DelimitedFrame,
    VarFrame,
    st_uint8,
    st_uint16_be,
    ProxyServer,
    ProxyClient,
    ProxyServerConfig,
    ProxyClientConfig,
)

st_ipv4 = WrapStruct(
    FixedFrame(4),
    lambda s: ia.IPv4Address(s).packed,
    lambda b: str(ia.IPv4Address(b)),
)

st_ipv6 = WrapStruct(
    FixedFrame(16),
    lambda s: ia.IPv6Address(s).packed,
    lambda b: str(ia.IPv6Address(b)),
)

st_socks5_str = WrapStruct(VarFrame(st_uint8), str.encode, bytes.decode)

socks5_atype_dict: dict[int, Struct] = {
    3: st_socks5_str,
    1: st_ipv4,
    4: st_ipv6,
}

st_socks5_addr = DictStruct(
    [
        ("atype", st_uint8),
        ("host", lambda data: socks5_atype_dict[data["atype"]]),
        ("port", st_uint16_be),
    ]
)

st_socks5_auth_req = DictStruct(
    [
        ("ver", st_uint8),
        ("meths", VarFrame(st_uint8)),
    ]
)

st_socks5_auth_resp = DictStruct(
    [
        ("ver", st_uint8),
        ("meth", st_uint8),
    ]
)

st_socks5_req = DictStruct(
    [
        ("ver", st_uint8),
        ("cmd", st_uint8),
        ("rsv", st_uint8),
        ("addr", st_socks5_addr),
    ]
)

st_socks5_resp = DictStruct(
    [
        ("ver", st_uint8),
        ("status", st_uint8),
        ("rsv", st_uint8),
        ("addr", st_socks5_addr),
    ]
)


class Socks5Server(ProxyServer):
    async def wrap(self, reader: AsyncReader, writer: AsyncWriter) -> ProxyClientStream:
        auth_req = await st_socks5_auth_req.read_async(reader)
        if auth_req["ver"] != 5 or 0 not in auth_req["meths"]:
            raise Exception("Invalid socks5 auth req", auth_req)
        await st_socks5_auth_resp.pack_one_then_write_async(
            writer, {"ver": 5, "meth": 0}
        )
        req = await st_socks5_req.read_async(reader)
        if req["ver"] != 5 or req["cmd"] != 1:
            raise Exception("Invalid socks5 req", req)
        await st_socks5_resp.pack_one_then_write_async(
            writer,
            {
                "ver": 5,
                "status": 0,
                "rsv": 0,
                "addr": {"atype": 1, "host": "0.0.0.0", "port": 0},
            },
        )
        return reader, writer, req["addr"]["host"], req["addr"]["port"]


class Socks5Client(ProxyClient):
    async def wrap(
        self, reader: AsyncReader, writer: AsyncWriter, host: str, port: int
    ) -> Stream:
        await st_socks5_auth_req.pack_one_then_write_async(
            writer, {"ver": 5, "meths": "\x00"}
        )
        auth_resp = await st_socks5_auth_resp.read_async(reader)
        if auth_resp["ver"] != 5 or auth_resp["meth"] != 0:
            raise Exception("Invalid socks5 auth resp", auth_resp)
        await st_socks5_req.pack_one_then_write_async(
            writer,
            {
                "ver": 5,
                "cmd": 1,
                "rsv": 0,
                "addr": {"atype": 3, "host": host, "port": port},
            },
        )
        resp = await st_socks5_resp.read_async(reader)
        if resp["ver"] != 5 or resp["status"] != 0:
            raise Exception("Invalid socks5 resp", resp)
        return reader, writer


st_trojan_req = DictStruct(
    [
        ("auth", DelimitedFrame(b"\r\n")),
        ("cmd", st_uint8),
        ("addr", st_socks5_addr),
        ("rsv", DelimitedFrame(b"\r\n")),
    ]
)


class TrojanServer(ProxyServer):
    def __init__(self, auth: bytes):
        self.auth = auth

    async def wrap(self, reader: AsyncReader, writer: AsyncWriter) -> ProxyClientStream:
        req = await st_trojan_req.read_async(reader)
        if req["auth"] != self.auth or req["cmd"] != 1 or len(req["rsv"]) != 0:
            raise Exception("Invlaid trojan req", req)
        return reader, writer, req["addr"]["host"], req["addr"]["port"]


class TrojanClient(ProxyClient):
    def __init__(self, auth: bytes):
        self.auth = auth

    async def wrap(
        self, reader: AsyncReader, writer: AsyncWriter, host: str, port: int
    ) -> Stream:
        await st_trojan_req.pack_one_then_write_async(
            writer,
            {
                "auth": self.auth,
                "cmd": 1,
                "addr": {"atype": 3, "host": host, "port": port},
                "rsv": b"",
            },
        )
        return reader, writer


class Socks5ServerConfig(ProxyServerConfig):
    type = "socks5"

    @classmethod
    def from_data(cls, data: dict) -> Socks5Server:
        _ = data
        return Socks5Server()


class Socks5ClientConfig(ProxyClientConfig):
    type = "socks5"

    @classmethod
    def from_data(cls, data: dict) -> Socks5Client:
        _ = data
        return Socks5Client()


class TrojanServerConfig(ProxyServerConfig):
    type = "trojan"

    @classmethod
    def from_data(cls, data: dict) -> TrojanServer:
        return TrojanServer(sha224(data["auth"]).digest())


class TrojanClientConfig(ProxyClientConfig):
    type = "trojan"

    @classmethod
    def from_data(cls, data: dict) -> TrojanClient:
        return TrojanClient(sha224(data["auth"]).digest())
