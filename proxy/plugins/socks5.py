from hashlib import sha224
from ipaddress import IPv4Address, IPv6Address

from proxy import *

st_ipv4 = WrapStruct(
    FixedFrame(4),
    lambda s: IPv4Address(s).packed,
    lambda b: str(IPv4Address(b)),
)

st_ipv6 = WrapStruct(
    FixedFrame(16),
    lambda s: IPv6Address(s).packed,
    lambda b: str(IPv6Address(b)),
)

socks5_atype_dict: dict[int, Struct] = {
    3: st_uint8_var_str,
    1: st_ipv4,
    4: st_ipv6,
}


def st_socks5_host(data: DictContext) -> Struct:
    return socks5_atype_dict[data["atype"]]


st_socks5_addr = DictStruct(
    [
        ("atype", st_uint8),
        ("host", st_socks5_host),
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


class Socks5StructError(StructError):
    pass


class Socks5Server(ProxyServer):
    async def wrap(self, reader: AsyncReader, writer: AsyncWriter) -> ProxyClientStream:
        auth_req = await st_socks5_auth_req.read_async(reader)
        if auth_req["ver"] != 5 or 0 not in auth_req["meths"]:
            raise Socks5StructError("Invalid socks5 auth req", auth_req)
        await st_socks5_auth_resp.pack_one_then_write_async(
            writer, {"ver": 5, "meth": 0}
        )
        req = await st_socks5_req.read_async(reader)
        if req["ver"] != 5 or req["cmd"] != 1:
            raise Socks5StructError("Invalid socks5 req", req)
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
            raise Socks5StructError("Invalid socks5 auth resp", auth_resp)
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
            raise Socks5StructError("Invalid socks5 resp", resp)
        return reader, writer


st_trojan_req = DictStruct(
    [
        ("auth", st_http_line),
        ("cmd", st_uint8),
        ("addr", st_socks5_addr),
        ("rsv", st_http_line),
    ]
)


class TrojanStructError(StructError):
    pass


class TrojanAuthError(StructError):
    pass


class TrojanServer(ProxyServer):
    def __init__(self, auth: str):
        self.auth = auth

    async def wrap(self, reader: AsyncReader, writer: AsyncWriter) -> ProxyClientStream:
        req = await st_trojan_req.read_async(reader)
        if req["cmd"] != 1 or req["rsv"] != "":
            raise TrojanStructError("Invlaid trojan req", req)
        if req["auth"] != self.auth:
            raise TrojanAuthError("Invalid trojan req", req)
        return reader, writer, req["addr"]["host"], req["addr"]["port"]


class TrojanClient(ProxyClient):
    def __init__(self, auth: str):
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
    def from_kwargs(cls) -> Socks5Server:
        return Socks5Server()

    @classmethod
    def from_data(cls, data: dict) -> Socks5Server:
        return cls.from_kwargs(**data)


class Socks5ClientConfig(ProxyClientConfig):
    type = "socks5"

    @classmethod
    def from_kwargs(cls) -> Socks5Client:
        return Socks5Client()

    @classmethod
    def from_data(cls, data: dict) -> Socks5Client:
        return cls.from_kwargs(**data)


def trojan_auth(auth: str) -> str:
    return sha224(auth.encode()).digest().hex()


class TrojanServerConfig(ProxyServerConfig):
    type = "trojan"

    @classmethod
    def from_kwargs(cls, auth: str) -> TrojanServer:
        return TrojanServer(trojan_auth(auth))

    @classmethod
    def from_data(cls, data: dict) -> TrojanServer:
        return cls.from_kwargs(**data)


class TrojanClientConfig(ProxyClientConfig):
    type = "trojan"

    @classmethod
    def from_kwargs(cls, auth: str) -> TrojanClient:
        return TrojanClient(trojan_auth(auth))

    @classmethod
    def from_data(cls, data: dict) -> TrojanClient:
        return cls.from_kwargs(**data)
