from typing import Any, Optional
from collections.abc import Callable, Awaitable, Sequence
from abc import ABC, abstractmethod
from functools import reduce
from dataclasses import dataclass
import struct as format_struct
from random import choice
from logging import getLogger
import json
import io
import asyncio as aio


class Reader(ABC):
    @abstractmethod
    def read(self) -> bytes:
        pass

    @abstractmethod
    def readexactly(self, n: int) -> bytes:
        pass

    @abstractmethod
    def readuntil(self, sep: bytes) -> bytes:
        pass


class AsyncReader(ABC):
    @abstractmethod
    async def read_async(self) -> bytes:
        pass

    @abstractmethod
    async def readexactly_async(self, n: int) -> bytes:
        pass

    @abstractmethod
    async def readuntil_async(self, sep: bytes) -> bytes:
        pass


class AsyncedReader(AsyncReader):
    def __init__(self, reader: Reader):
        self.reader = reader

    async def read_async(self) -> bytes:
        return self.reader.read()

    async def readexactly_async(self, n: int) -> bytes:
        return self.reader.readexactly(n)

    async def readuntil_async(self, sep: bytes) -> bytes:
        return self.reader.readuntil(sep)


class BufferLimitOverrunError(Exception):
    pass


class BufferIncompleteReadError(EOFError):
    pass


class BufferedReader(Reader):
    def __init__(self, buffer: bytes = b"", buffer_limit: int = 64 * 1024):
        self.buffer = buffer
        self.buffer_limit = buffer_limit

    @abstractmethod
    def read1(self) -> bytes:
        pass

    def peek_more(self):
        if len(self.buffer) >= self.buffer_limit:
            raise BufferLimitOverrunError()
        b = self.read1()
        if len(b) == 0:
            raise BufferIncompleteReadError()
        self.buffer += b

    def read(self) -> bytes:
        if len(self.buffer) == 0:
            return self.read1()
        else:
            b = self.buffer
            self.buffer = b""
            return b

    def readexactly(self, n: int) -> bytes:
        while True:
            if len(self.buffer) >= n:
                b = self.buffer[:n]
                self.buffer = self.buffer[n:]
                return b
            self.peek_more()

    def readuntil(self, sep: bytes) -> bytes:
        while True:
            sp = self.buffer.split(sep, 1)
            if len(sp) == 2:
                b, self.buffer = sp
                return b
            self.peek_more()


class AsyncBufferedReader(AsyncReader):
    def __init__(self, buffer: bytes = b"", buffer_limit: int = 64 * 1024):
        self.buffer = buffer
        self.buffer_limit = buffer_limit

    @abstractmethod
    async def read1_async(self) -> bytes:
        pass

    async def peek_more_async(self):
        if len(self.buffer) >= self.buffer_limit:
            raise BufferLimitOverrunError()
        b = await self.read1_async()
        if len(b) == 0:
            raise BufferIncompleteReadError()
        self.buffer += b

    async def read_async(self) -> bytes:
        if len(self.buffer) == 0:
            return await self.read1_async()
        else:
            b = self.buffer
            self.buffer = b""
            return b

    async def readexactly_async(self, n: int) -> bytes:
        while True:
            if len(self.buffer) >= n:
                b = self.buffer[:n]
                self.buffer = self.buffer[n:]
                return b
            await self.peek_more_async()

    async def readuntil_async(self, sep: bytes) -> bytes:
        while True:
            sp = self.buffer.split(sep, 1)
            if len(sp) == 2:
                b, self.buffer = sp
                return b
            await self.peek_more_async()


class Writer(ABC):
    @abstractmethod
    def write(self, data: bytes):
        pass


class AsyncWriter(ABC):
    @abstractmethod
    async def write_async(self, data: bytes):
        pass


class AsyncedWriter(AsyncWriter):
    def __init__(self, writer: Writer):
        self.writer = writer

    async def write_async(self, data: bytes):
        self.writer.write(data)


class IOReader(BufferedReader):
    def __init__(self, file: io.BufferedIOBase, **kwargs):
        self.file = file
        super().__init__(**kwargs)

    def read1(self) -> bytes:
        return self.file.read(4096)


class IOWriter(Writer):
    def __init__(self, file: io.BufferedIOBase):
        self.file = file

    def write(self, data: bytes):
        if len(data) > 0:
            self.file.write(data)


class AIOReader(AsyncReader):
    def __init__(self, reader: aio.StreamReader):
        self.reader = reader

    async def read_async(self) -> bytes:
        return await self.reader.read(4096)

    async def readexactly_async(self, n: int) -> bytes:
        return await self.reader.readexactly(n)

    async def readuntil_async(self, sep: bytes) -> bytes:
        return await self.reader.readuntil(sep)


class AIOWriter(AsyncWriter):
    def __init__(self, writer: aio.StreamWriter):
        self.writer = writer

    async def write_async(self, data: bytes):
        if len(data) > 0:
            self.writer.write(data)
            await self.writer.drain()


def pipe(reader: Reader, writer: Writer):
    while True:
        b = reader.read()
        if len(b) == 0:
            return
        else:
            writer.write(b)


async def pipe_async(reader: AsyncReader, writer: AsyncWriter):
    while True:
        b = await reader.read_async()
        if len(b) == 0:
            return
        else:
            await writer.write_async(b)


class StructError(Exception):
    pass


class InvalidUnpackError(Exception):
    pass


class Struct(ABC):
    @abstractmethod
    def read(self, reader: Reader) -> Any:
        pass

    @abstractmethod
    async def read_async(self, reader: AsyncReader) -> Any:
        pass

    @abstractmethod
    def write(self, writer: Writer, data: Any):
        pass

    @abstractmethod
    async def write_async(self, writer: AsyncWriter, data: Any):
        pass

    def unpack_one(self, b: bytes) -> Any:
        bio = io.BytesIO(b)
        bio_reader = IOReader(bio)
        data = self.read(bio_reader)
        if bio.tell() != len(b):
            raise InvalidUnpackError()
        return data

    def unpack_many(self, b: bytes) -> Sequence[Any]:
        bio = io.BytesIO(b)
        bio_reader = IOReader(bio)
        data = list()
        while bio.tell() != len(b):
            data.append(self.read(bio_reader))
        return data

    def pack_one(self, data: Any) -> bytes:
        bio = io.BytesIO()
        bio_writer = IOWriter(bio)
        self.write(bio_writer, data)
        return bio.getvalue()

    def pack_many(self, data: Sequence[Any]) -> bytes:
        bio = io.BytesIO()
        bio_writer = IOWriter(bio)
        for _data in data:
            self.write(bio_writer, _data)
        return bio.getvalue()

    async def pack_one_then_write_async(self, writer: AsyncWriter, data: Any):
        await writer.write_async(self.pack_one(data))

    async def pack_many_then_write_async(
        self, writer: AsyncWriter, data: Sequence[Any]
    ):
        await writer.write_async(self.pack_many(data))


class WrapStruct(Struct):
    def __init__(
        self,
        struct: Struct,
        pack_fn: Callable[[Any], Any],
        unpack_fn: Callable[[Any], Any],
    ):
        self.struct = struct
        self.pack_fn = pack_fn
        self.unpack_fn = unpack_fn

    def read(self, reader: Reader) -> Any:
        return self.unpack_fn(self.struct.read(reader))

    async def read_async(self, reader: AsyncReader) -> Any:
        return self.unpack_fn(await self.struct.read_async(reader))

    def write(self, writer: Writer, data: Any):
        self.struct.write(writer, self.pack_fn(data))

    async def write_async(self, writer: AsyncWriter, data: Any):
        await self.struct.write_async(writer, self.pack_fn(data))


type TupleContext = Sequence[Any]


class TupleStruct(Struct):
    def __init__(self, structs: Sequence[Struct]):
        self.structs = structs

    def read(self, reader: Reader) -> TupleContext:
        data: TupleContext = list()
        for struct in self.structs:
            data.append(struct.read(reader))
        return data

    async def read_async(self, reader: AsyncReader) -> TupleContext:
        data: TupleContext = list()
        for struct in self.structs:
            data.append(await struct.read_async(reader))
        return data

    def write(self, writer: Writer, data: TupleContext):
        for i in range(len(self.structs)):
            self.structs[i].write(writer, data[i])

    async def write_async(self, writer: AsyncWriter, data: TupleContext):
        for i in range(len(self.structs)):
            await self.structs[i].write_async(writer, data[i])


type DictContext = dict[str, Any]
type DictContextStruct = Struct | Callable[[DictContext], Struct]


class DictStruct(Struct):
    def __init__(self, key_structs: Sequence[tuple[str, DictContextStruct]]):
        self.key_structs = key_structs

    def read(self, reader: Reader) -> DictContext:
        data: DictContext = dict()
        for key, struct in self.key_structs:
            if not isinstance(struct, Struct):
                struct = struct(data)
            data[key] = struct.read(reader)
        return data

    async def read_async(self, reader: AsyncReader) -> DictContext:
        data: DictContext = dict()
        for key, struct in self.key_structs:
            if not isinstance(struct, Struct):
                struct = struct(data)
            data[key] = await struct.read_async(reader)
        return data

    def write(self, writer: Writer, data: DictContext):
        for key, struct in self.key_structs:
            if not isinstance(struct, Struct):
                struct = struct(data)
            struct.write(writer, data[key])

    async def write_async(self, writer: AsyncWriter, data: DictContext):
        for key, struct in self.key_structs:
            if not isinstance(struct, Struct):
                struct = struct(data)
            await struct.write_async(writer, data[key])


class Frame(Struct):
    @abstractmethod
    def read(self, reader: Reader) -> bytes:
        pass

    @abstractmethod
    async def read_async(self, reader: AsyncReader) -> bytes:
        pass

    @abstractmethod
    def write(self, writer: Writer, data: bytes):
        pass

    @abstractmethod
    async def write_async(self, writer: AsyncWriter, data: bytes):
        pass


class InvalidFixedFrameLengthError(StructError):
    pass


class FixedFrame(Frame):
    def __init__(self, length: int):
        self.length = length

    def read(self, reader: Reader) -> bytes:
        return reader.readexactly(self.length)

    async def read_async(self, reader: AsyncReader) -> bytes:
        return await reader.readexactly_async(self.length)

    def write(self, writer: Writer, data: bytes):
        if len(data) != self.length:
            raise InvalidFixedFrameLengthError()
        writer.write(data)

    async def write_async(self, writer: AsyncWriter, data: bytes):
        if len(data) != self.length:
            raise InvalidFixedFrameLengthError()
        await writer.write_async(data)


class DelimitedFrame(Frame):
    def __init__(self, delim: bytes):
        self.delim = delim

    def read(self, reader: Reader) -> bytes:
        return reader.readuntil(self.delim)

    async def read_async(self, reader: AsyncReader) -> bytes:
        return await reader.readuntil_async(self.delim)

    def write(self, writer: Writer, data: bytes):
        writer.write(data)
        writer.write(self.delim)

    async def write_async(self, writer: AsyncWriter, data: bytes):
        await writer.write_async(data)
        await writer.write_async(self.delim)


class VarFrame(Frame):
    def __init__(self, length_struct: Struct):
        self.length_struct = length_struct

    def read(self, reader: Reader) -> Any:
        length: int = self.length_struct.read(reader)
        return reader.readexactly(length)

    async def read_async(self, reader: AsyncReader) -> Any:
        length: int = await self.length_struct.read_async(reader)
        return await reader.readexactly_async(length)

    def write(self, writer: Writer, data: bytes):
        self.length_struct.write(writer, len(data))
        writer.write(data)

    async def write_async(self, writer: AsyncWriter, data: bytes):
        await self.length_struct.write_async(writer, len(data))
        await writer.write_async(data)


class FormatStruct(Struct):
    def __init__(self, format: format_struct.Struct | str):
        if isinstance(format, str):
            format = format_struct.Struct(format)
        self.format = format

    def read(self, reader: Reader) -> Sequence[int]:
        b = reader.readexactly(self.format.size)
        return self.format.unpack(b)

    async def read_async(self, reader: AsyncReader) -> Sequence[int]:
        b = await reader.readexactly_async(self.format.size)
        return self.format.unpack(b)

    def write(self, writer: Writer, data: Sequence[int]):
        writer.write(self.format.pack(*data))

    async def writer(self, writer: AsyncWriter, data: Sequence[int]):
        await writer.write_async(self.format.pack(*data))


class IntStruct(Struct):
    def __init__(self, format: format_struct.Struct | str):
        if isinstance(format, str):
            format = format_struct.Struct(format)
        self.format = format

    def read(self, reader: Reader) -> int:
        b = reader.readexactly(self.format.size)
        return self.format.unpack(b)[0]

    async def read_async(self, reader: AsyncReader) -> int:
        b = await reader.readexactly_async(self.format.size)
        return self.format.unpack(b)[0]

    def write(self, writer: Writer, data: int):
        writer.write(self.format.pack(data))

    async def write_async(self, writer: AsyncWriter, data: int):
        await writer.write_async(self.format.pack(data))


st_int8 = IntStruct("b")
st_uint8 = IntStruct("B")
st_int16_be = IntStruct(">h")
st_int32_be = IntStruct(">l")
st_int64_be = IntStruct(">q")
st_uint16_be = IntStruct(">H")
st_uint32_be = IntStruct(">L")
st_uint64_be = IntStruct(">Q")
st_int16_le = IntStruct("<h")
st_int32_le = IntStruct("<l")
st_int64_le = IntStruct("<q")
st_uint16_le = IntStruct("<H")
st_uint32_le = IntStruct("<L")
st_uint64_le = IntStruct("<Q")

st_unix_line = WrapStruct(DelimitedFrame(b"\n"), str.encode, bytes.decode)
st_http_line = WrapStruct(DelimitedFrame(b"\r\n"), str.encode, bytes.decode)
st_uint8_var_str = WrapStruct(VarFrame(st_uint8), str.encode, bytes.decode)


@dataclass
class Stream:
    reader: AsyncReader
    writer: AsyncWriter


type StreamCallback = Callable[[Stream], Awaitable]

type ServerCallback = StreamCallback
type ClientCallback = StreamCallback


class ServerProvider(ABC):
    @abstractmethod
    async def start_server(self, callback: ServerCallback):
        pass


class ClientProvider(ABC):
    @abstractmethod
    async def open_connection(self, callback: ClientCallback):
        pass


class TCPServerProvider(ServerProvider):
    def __init__(self, **kwargs):
        self.kwargs = kwargs

    async def start_server(self, callback: ServerCallback):
        async def tcp_callback(reader: aio.StreamReader, writer: aio.StreamWriter):
            try:
                await callback(Stream(AIOReader(reader), AIOWriter(writer)))
            finally:
                writer.close()
                await writer.wait_closed()

        server = await aio.start_server(tcp_callback, **self.kwargs)
        async with server:
            await server.serve_forever()


class TCPClientProvider(ClientProvider):
    def __init__(self, **kwargs):
        self.kwargs = kwargs

    async def open_connection(self, callback: ClientCallback):
        reader, writer = await aio.open_connection(**self.kwargs)
        try:
            await callback(Stream(AIOReader(reader), AIOWriter(writer)))
        finally:
            writer.close()
            await writer.wait_closed()


@dataclass
class ProxyRequest:
    host: str
    port: int


type ProxyServerCallback = Callable[[Stream, ProxyRequest], Awaitable]
type ProxyClientCallback = StreamCallback


class ProxyServer(ABC):
    @abstractmethod
    async def handshake(
        self,
        stream: Stream,
        callback: ProxyServerCallback,
    ):
        pass


class ProxyClient(ABC):
    @abstractmethod
    async def handshake(
        self,
        stream: Stream,
        request: ProxyRequest,
        callback: ProxyClientCallback,
    ):
        pass


type InBoundCallback = ProxyServerCallback
type OutBoundCallback = StreamCallback


logger = getLogger("proxy")


@dataclass
class Request:
    proxy: ProxyRequest
    context: dict


class InBound(ABC):
    @abstractmethod
    async def start_server(self, callback: InBoundCallback):
        pass


class OutBound(ABC):
    @abstractmethod
    async def open_connection(
        self,
        request: Request,
        callback: OutBoundCallback,
    ):
        pass


class ProxyInBound(InBound):
    def __init__(self, server_provider: ServerProvider, proxy_server: ProxyServer):
        self.server_provider = server_provider
        self.proxy_server = proxy_server

    async def start_server(self, callback: InBoundCallback):
        async def server_provider_callback(stream: Stream):
            await self.proxy_server.handshake(stream, callback)

        await self.server_provider.start_server(server_provider_callback)


class ProxyOutBound(OutBound):
    def __init__(self, client_provider: ClientProvider, proxy_client: ProxyClient):
        self.client_provider = client_provider
        self.proxy_client = proxy_client

    async def open_connection(self, request: Request, callback: OutBoundCallback):
        async def client_provider_callback(stream: Stream):
            await self.proxy_client.handshake(stream, request.proxy, callback)

        await self.client_provider.open_connection(client_provider_callback)


class BlockOutBound(OutBound):
    async def open_connection(self, request: Request, callback: OutBoundCallback):
        _ = request, callback


class DirectOutBound(OutBound):
    async def open_connection(self, request: Request, callback: OutBoundCallback):
        reader, writer = await aio.open_connection(
            request.proxy.host, request.proxy.port
        )
        try:
            await callback(Stream(AIOReader(reader), AIOWriter(writer)))
        finally:
            writer.close()
            await writer.wait_closed()


class MultiInBound(InBound):
    def __init__(self, inbounds: Sequence[InBound]):
        self.inbounds = inbounds

    async def start_server(self, callback: InBoundCallback):
        await aio.gather(
            *map(lambda inbound: inbound.start_server(callback), self.inbounds)
        )


class RandDispatchOutBound(OutBound):
    def __init__(self, outbounds: Sequence[OutBound]):
        self.outbounds = list(outbounds)

    async def open_connection(self, request: Request, callback: OutBoundCallback):
        outbound = choice(self.outbounds)
        await outbound.open_connection(request, callback)


type MiddleWareCallback = Callable[[Request], Awaitable]


class MiddleWare(ABC):
    @abstractmethod
    async def open_connection(self, request: Request, callback: MiddleWareCallback):
        pass


class IdentityMiddleWare(MiddleWare):
    async def open_connection(self, request: Request, callback: MiddleWareCallback):
        await callback(request)


class ComposeMiddleWare(MiddleWare):
    def __init__(self, middleware1: MiddleWare, middleware2: MiddleWare):
        self.middleware1 = middleware1
        self.middleware2 = middleware2

    @classmethod
    def compose(cls, middlewares: Sequence[MiddleWare]) -> MiddleWare:
        if len(middlewares) == 0:
            return IdentityMiddleWare()
        else:
            return reduce(cls, middlewares)

    async def open_connection(self, request: Request, callback: MiddleWareCallback):
        async def middleware1_callback(request: Request):
            await self.middleware2.open_connection(request, callback)

        await self.middleware1.open_connection(request, middleware1_callback)


class MiddleWareOutBound(OutBound):
    def __init__(self, middleware: MiddleWare, outbound: OutBound):
        self.middleware = middleware
        self.outbound = outbound

    async def open_connection(self, request: Request, callback: OutBoundCallback):
        async def middleware_callback(request: Request):
            await self.outbound.open_connection(request, callback)

        await self.middleware.open_connection(request, middleware_callback)


class LogMiddleWare(MiddleWare):
    async def open_connection(self, request: Request, callback: MiddleWareCallback):
        host = request.proxy.host
        port = request.proxy.port
        tag = request.context.get("tag", "default")
        logger.info("connect to [%s] %s %d", tag, host, port)
        try:
            await callback(request)
        except Exception as e:
            logger.debug(
                "except while connect to [%s] %s %d: %s %s", tag, host, port, type(e), e
            )
            raise


type Tag = str
type Tags = dict[str, Tag]


def match_tags(host: str, tags: Tags) -> Optional[Tag]:
    tag = tags.get(host)
    if tag is not None:
        return tag
    sp = host.split(".", 1)
    if len(sp) == 2:
        return match_tags(sp[1], tags)


class TagMiddleWare(MiddleWare):
    def __init__(self, tags: Tags, default_tag: Tag):
        self.tags = tags
        self.default_tag = default_tag

    def match_tags(self, host: str) -> Tag:
        return match_tags(host, self.tags) or self.default_tag

    async def open_connection(self, request: Request, callback: MiddleWareCallback):
        tag = self.match_tags(request.proxy.host)
        request.context["tag"] = tag
        await callback(request)


class TagDispatchOutBound(OutBound):
    def __init__(self, outbounds: dict[Tag, OutBound]):
        self.outbounds = outbounds

    async def open_connection(self, request: Request, callback: OutBoundCallback):
        outbound = self.outbounds[request.context["tag"]]
        await outbound.open_connection(request, callback)


class Server:
    def __init__(self, inbound: InBound, outbound: OutBound):
        self.inbound = inbound
        self.outbound = outbound
        self.tasks: set[aio.Task] = set()

    async def start_server(self):
        async def inbound_callback(in_stream: Stream, request: ProxyRequest):
            async def outbound_callback(out_stream: Stream):
                task1 = aio.create_task(pipe_async(in_stream.reader, out_stream.writer))
                task2 = aio.create_task(pipe_async(out_stream.reader, in_stream.writer))
                for task in task1, task2:
                    self.tasks.add(task)
                    task.add_done_callback(self.tasks.discard)
                try:
                    await aio.gather(task1, task2)
                except Exception as e:
                    logger.debug("except while piping: %s %s", type(e), e)
                finally:
                    for task in task1, task2:
                        if not task.cancelled():
                            task.cancel()

            await self.outbound.open_connection(
                Request(request, dict()), outbound_callback
            )

        await self.inbound.start_server(inbound_callback)

    def run(self):
        aio.run(self.start_server())


class Config(ABC):
    @classmethod
    @abstractmethod
    def from_data(cls, data: dict) -> Any:
        pass


class RegistrableConfig(Config):
    registry: dict[str, type]
    type: str

    def __init_subclass__(cls):
        if hasattr(cls, "registry") and hasattr(cls, "type"):
            cls.registry[cls.type] = cls
        return super().__init_subclass__()

    @classmethod
    def from_kwargs_by_type(cls, type, **data) -> Any:
        return cls.registry[type].from_data(data)

    @classmethod
    def from_data_by_type(cls, data: dict) -> Any:
        return cls.from_kwargs_by_type(**data)


class ProxyServerConfig(RegistrableConfig):
    registry = dict()

    @classmethod
    @abstractmethod
    def from_data(cls, data: dict) -> ProxyServer:
        pass


class ProxyClientConfig(RegistrableConfig):
    registry = dict()

    @classmethod
    @abstractmethod
    def from_data(cls, data: dict) -> ProxyClient:
        pass


class ServerProviderConfig(RegistrableConfig):
    registry = dict()

    @classmethod
    @abstractmethod
    def from_data(cls, data: dict) -> ServerProvider:
        pass


class ClientProviderConfig(RegistrableConfig):
    registry = dict()

    @classmethod
    @abstractmethod
    def from_data(cls, data: dict) -> ClientProvider:
        pass


class TCPServerProviderConfig(ServerProviderConfig):
    type = "tcp"

    @classmethod
    def from_data(cls, data: dict) -> TCPServerProvider:
        return TCPServerProvider(**data)


class TCPClientProviderConfig(ClientProviderConfig):
    type = "tcp"

    @classmethod
    def from_data(cls, data: dict) -> TCPClientProvider:
        return TCPClientProvider(**data)


class InBoundConfig(RegistrableConfig):
    registry = dict()

    @classmethod
    @abstractmethod
    def from_data(cls, data: dict) -> InBound:
        pass


class OutBoundConfig(RegistrableConfig):
    registry = dict()

    @classmethod
    @abstractmethod
    def from_data(cls, data: dict) -> OutBound:
        pass


class ProxyInBoundConfig(InBoundConfig):
    type = "proxy"

    @classmethod
    def from_kwargs(cls, server_provider: dict, proxy_server: dict) -> ProxyInBound:
        return ProxyInBound(
            server_provider=ServerProviderConfig.from_data_by_type(server_provider),
            proxy_server=ProxyServerConfig.from_data_by_type(proxy_server),
        )

    @classmethod
    def from_data(cls, data: dict) -> ProxyInBound:
        return cls.from_kwargs(**data)


class ProxyOutBoundConfig(OutBoundConfig):
    type = "proxy"

    @classmethod
    def from_kwargs(cls, client_provider: dict, proxy_client: dict) -> ProxyOutBound:
        return ProxyOutBound(
            client_provider=ClientProviderConfig.from_data_by_type(client_provider),
            proxy_client=ProxyClientConfig.from_data_by_type(proxy_client),
        )

    @classmethod
    def from_data(cls, data: dict) -> ProxyOutBound:
        return cls.from_kwargs(**data)


class BlockOutBoundConfig(OutBoundConfig):
    type = "block"

    @classmethod
    def from_kwargs(cls) -> BlockOutBound:
        return BlockOutBound()

    @classmethod
    def from_data(cls, data: dict) -> BlockOutBound:
        return cls.from_kwargs(**data)


class DirectOutBoundConfig(OutBoundConfig):
    type = "direct"

    @classmethod
    def from_kwargs(cls) -> DirectOutBound:
        return DirectOutBound()

    @classmethod
    def from_data(cls, data: dict) -> DirectOutBound:
        return cls.from_kwargs(**data)


class MultiInBoundConfig(InBoundConfig):
    type = "multi"

    @classmethod
    def from_kwargs(cls, inbounds: Sequence[dict]) -> MultiInBound:
        return MultiInBound(
            inbounds=[InBoundConfig.from_data_by_type(inbound) for inbound in inbounds]
        )

    @classmethod
    def from_data(cls, data: dict) -> MultiInBound:
        return cls.from_kwargs(**data)


class RandDispatchOutBoundConfig(OutBoundConfig):
    type = "rand_dispatch"

    @classmethod
    def from_kwargs(cls, outbounds: Sequence[dict]) -> RandDispatchOutBound:
        return RandDispatchOutBound(
            outbounds=[
                OutBoundConfig.from_data_by_type(outbound) for outbound in outbounds
            ]
        )

    @classmethod
    def from_data(cls, data: dict) -> RandDispatchOutBound:
        return cls.from_kwargs(**data)


class MiddleWareConfig(RegistrableConfig):
    registry = dict()

    @classmethod
    @abstractmethod
    def from_data(cls, data: dict) -> MiddleWare:
        pass


class LogMiddleWareConfig(MiddleWareConfig):
    type = "log"

    @classmethod
    def from_kwargs(cls) -> LogMiddleWare:
        return LogMiddleWare()

    @classmethod
    def from_data(cls, data: dict) -> LogMiddleWare:
        return cls.from_kwargs(**data)


class MiddleWareOutBoundConfig(OutBoundConfig):
    type = "middleware"

    @classmethod
    def from_kwargs(
        cls, middlewares: Sequence[dict], outbound: dict
    ) -> MiddleWareOutBound:
        return MiddleWareOutBound(
            middleware=ComposeMiddleWare.compose(
                [
                    MiddleWareConfig.from_data_by_type(middleware)
                    for middleware in middlewares
                ]
            ),
            outbound=OutBoundConfig.from_data_by_type(outbound),
        )

    @classmethod
    def from_data(cls, data: dict) -> MiddleWareOutBound:
        return cls.from_kwargs(**data)


class TagsProviderConfig(RegistrableConfig):
    registry = dict()

    @classmethod
    @abstractmethod
    def from_data(cls, data: dict) -> Tags:
        pass


class MultiTagsProviderConfig(TagsProviderConfig):
    type = "multi"

    @classmethod
    def from_kwargs(cls, providers: Sequence[dict]) -> Tags:
        tags: Tags = dict()
        for provider in providers:
            provider_tags: Tags = TagsProviderConfig.from_data_by_type(provider)
            for host, tag in provider_tags:
                tags[host] = tag
        return tags

    @classmethod
    def from_data(cls, data: dict) -> Tags:
        return cls.from_kwargs(**data)


class DataTagsProviderConfig(TagsProviderConfig):
    type = "data"

    @classmethod
    def from_kwargs(cls, tags: Tags) -> Tags:
        return tags

    @classmethod
    def from_data(cls, data: dict) -> Tags:
        return cls.from_kwargs(**data)


class TagMiddleWareConfig(MiddleWareConfig):
    type = "tag"

    @classmethod
    def from_kwargs(cls, tags: dict, default_tag: Tag) -> TagMiddleWare:
        return TagMiddleWare(
            tags=TagsProviderConfig.from_data_by_type(tags),
            default_tag=default_tag,
        )

    @classmethod
    def from_data(cls, data: dict) -> TagMiddleWare:
        return cls.from_kwargs(**data)


class TagDispatchOutBoundConfig(OutBoundConfig):
    type = "tag_dispatch"

    @classmethod
    def from_kwargs(cls, outbounds: dict[Tag, dict]) -> TagDispatchOutBound:
        return TagDispatchOutBound(
            outbounds={
                tag: OutBoundConfig.from_data_by_type(outbound)
                for tag, outbound in outbounds.items()
            },
        )

    @classmethod
    def from_data(cls, data: dict) -> TagDispatchOutBound:
        return cls.from_kwargs(**data)


class ServerConfig(Config):
    @classmethod
    def from_kwargs(cls, inbound: dict, outbound: dict) -> Server:
        return Server(
            inbound=InBoundConfig.from_data_by_type(inbound),
            outbound=OutBoundConfig.from_data_by_type(outbound),
        )

    @classmethod
    def from_data(cls, data: dict) -> Server:
        return cls.from_kwargs(**data)


class JsonConfig(RegistrableConfig):
    type = "json"

    @classmethod
    def from_kwargs(cls, path: str) -> Any:
        with open(path, "r") as f:
            return cls.from_data_by_type(json.load(f))

    @classmethod
    def from_data(cls, data: dict) -> Any:
        return cls.from_kwargs(**data)


class JsonInBoundConfig(JsonConfig, InBoundConfig):
    pass


class JsonOutBoundConfig(JsonConfig, OutBoundConfig):
    pass


class JsonTagsProviderConfig(JsonConfig, TagsProviderConfig):
    pass
