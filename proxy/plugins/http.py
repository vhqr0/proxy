from collections.abc import Sequence
import re

from proxy.struct import (
    StructError,
    WrapStruct,
    DelimitedFrame,
)

from proxy import (
    ProxyRequest,
    Stream,
    ProxyServerCallback,
    ProxyClientCallback,
    ProxyServer,
    ProxyClient,
    ProxyServerConfig,
    ProxyClientConfig,
)


def http_headers_pack(headers: Sequence[str]) -> bytes:
    return "\r\n".join(headers).encode()


def http_headers_unpack(b: bytes) -> Sequence[str]:
    return b.decode().split("\r\n")


st_http_headers = WrapStruct(
    DelimitedFrame(b"\r\n\r\n"), http_headers_pack, http_headers_unpack
)


class HostPortStructError(StructError):
    pass


re_hostport = re.compile(r"^([^:]*)(:(.*))?$")
re_bracketed_hostport = re.compile(r"^\[([^\[\]]*)\](:(.*))?$")


def split_hostport(hostport: str, default_port=80) -> tuple[str, int]:
    if hostport[0] == "[":
        match = re_bracketed_hostport.match(hostport)
    else:
        match = re_hostport.match(hostport)
    if match is None:
        raise HostPortStructError("Invalid hostport", hostport)
    host = match[1]
    port = default_port if match[3] is None else int(match[3])
    return host, port


# split_hostport("google.com:443") # => ("google.com", 443)
# split_hostport("google.com") # => ("google.com", 80)
# split_hostport("[2000::1]:443") # => ("2000::1", 443)
# split_hostport("[2000::1]") # => ("2000::1", 80)


def join_hostport(host: str, port: int) -> str:
    if ":" in host:
        return f"[{host}]:{port}"
    else:
        return f"{host}:{port}"


class HTTPError(StructError):
    pass


class HTTPServer(ProxyServer):
    connect_re = re.compile(r"^[Cc][Oo][Nn][Nn][Ee][Cc][Tt] .* [Hh][Tt][Tt][Pp]/1\.1$")
    host_re = re.compile(r"^[Hh][Oo][Ss][Tt]:(.*)$")

    async def handshake(self, stream: Stream, callback: ProxyServerCallback):
        headers = await st_http_headers.read_async(stream.reader)
        connect = headers[0]
        match = self.connect_re.match(connect)
        if match is None:
            raise HTTPError("Invalid connect", connect)
        for header in headers[1:]:
            match = self.host_re.match(header)
            if match is not None:
                hostport = match[1].strip()
                host, port = split_hostport(hostport)
                break
        else:
            raise HTTPError("No host headers found")
        await st_http_headers.pack_one_then_write_async(
            stream.writer,
            ["HTTP/1.1 200 OK", "Connection: close"],
        )
        await callback(stream, ProxyRequest(host, port))


class HTTPClient(ProxyClient):
    response_re = re.compile(r"^[Hh][Tt][Tt][Pp]/1\.1 200 [Oo][Kk]$")

    async def handshake(
        self, stream: Stream, request: ProxyRequest, callback: ProxyClientCallback
    ):
        hostport = join_hostport(request.host, request.port)
        await st_http_headers.pack_one_then_write_async(
            stream.writer,
            [f"CONNECT {hostport} HTTP/1.1", f"Host: {hostport}"],
        )
        headers = await st_http_headers.read_async(stream.reader)
        response = headers[0]
        if self.response_re.match(response) is None:
            raise HTTPError("Invlaid response", response)
        await callback(stream)


class HTTPServerConfig(ProxyServerConfig):
    type = "http"

    @classmethod
    def from_kwargs(cls) -> HTTPServer:
        return HTTPServer()

    @classmethod
    def from_data(cls, data: dict) -> HTTPServer:
        return cls.from_kwargs(**data)


class HTTPClientConfig(ProxyClientConfig):
    type = "http"

    @classmethod
    def from_kwargs(cls) -> HTTPClient:
        return HTTPClient()

    @classmethod
    def from_data(cls, data: dict) -> HTTPClient:
        return cls.from_kwargs(**data)
