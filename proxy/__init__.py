from typing import Any, Optional
from collections.abc import Callable, Awaitable, Sequence
from abc import ABC, abstractmethod
import struct as format_struct
from random import choice
from logging import getLogger, Logger
import json
import io
import asyncio as aio
from pydantic import BaseModel


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


class BufferedReader(Reader):
    def __init__(self, buffer: bytes = b"", buffer_limit: int = 64 * 1024):
        self.buffer = buffer
        self.buffer_limit = buffer_limit

    @abstractmethod
    def read1(self) -> bytes:
        pass

    def peek_more(self):
        if len(self.buffer) >= self.buffer_limit:
            raise Exception("Buffer exceed limit")
        b = self.read1()
        if len(b) == 0:
            raise Exception("Read at EOF")
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


class BufferedAsyncReader(AsyncReader):
    def __init__(self, buffer: bytes = b"", buffer_limit: int = 64 * 1024):
        self.buffer = buffer
        self.buffer_limit = buffer_limit

    @abstractmethod
    async def read1_async(self) -> bytes:
        pass

    async def peek_more_async(self):
        if len(self.buffer) >= self.buffer_limit:
            raise Exception("Buffer exceed limit")
        b = await self.read1_async()
        if len(b) == 0:
            raise Exception("Read at EOF")
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
            raise Exception("BIO not at EOF")
        return data

    def unpack_many(self, b: bytes) -> Sequence[Any]:
        bio = io.BytesIO(b)
        bio_reader = IOReader(bio)
        data = []
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
        data: TupleContext = []
        for struct in self.structs:
            data.append(struct.read(reader))
        return data

    async def read_async(self, reader: AsyncReader) -> TupleContext:
        data: TupleContext = []
        for struct in self.structs:
            data.append(await struct.read_async(reader))
        return data


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


class FixedFrame(Frame):
    def __init__(self, length: int):
        self.length = length

    def read(self, reader: Reader) -> bytes:
        return reader.readexactly(self.length)

    async def read_async(self, reader: AsyncReader) -> bytes:
        return await reader.readexactly_async(self.length)

    def write(self, writer: Writer, data: bytes):
        if len(data) != self.length:
            raise Exception("Struct validation error")
        writer.write(data)

    async def write_async(self, writer: AsyncWriter, data: bytes):
        if len(data) != self.length:
            raise Exception("Struct validation error")
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

type Stream = tuple[AsyncReader, AsyncWriter]
type StreamCallback = Callable[[AsyncReader, AsyncWriter], Awaitable]

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
                await callback(AIOReader(reader), AIOWriter(writer))
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
            await callback(AIOReader(reader), AIOWriter(writer))
        finally:
            writer.close()
            await writer.wait_closed()


type ProxyClientStream = tuple[AsyncReader, AsyncWriter, str, int]
type ProxyClientStreamCallback = Callable[
    [AsyncReader, AsyncWriter, str, int], Awaitable
]


class ProxyServer(ABC):
    @abstractmethod
    async def wrap(self, reader: AsyncReader, writer: AsyncWriter) -> ProxyClientStream:
        """Wrap reader/writer to a new pair of reader/writer, and request host/port."""
        pass


class ProxyClient(ABC):
    @abstractmethod
    async def wrap(
        self, reader: AsyncReader, writer: AsyncWriter, host: str, port: int
    ) -> Stream:
        """Wrap reader/writer to a new pair of reader/writer, connect to host/pair via target proxy server."""
        pass


type InBoundCallback = ProxyClientStreamCallback
type OutBoundCallback = StreamCallback


class InBound(ABC):
    @abstractmethod
    async def start_server(self, callback: InBoundCallback):
        pass


class OutBound(ABC):
    @abstractmethod
    async def open_connection(self, host: str, port: int, callback: OutBoundCallback):
        pass


class ProxyInBound(InBound):
    def __init__(self, server_provider: ServerProvider, proxy_server: ProxyServer):
        self.server_provider = server_provider
        self.proxy_server = proxy_server

    async def start_server(self, callback: InBoundCallback):
        async def server_provider_callback(reader: AsyncReader, writer: AsyncWriter):
            reader, writer, host, port = await self.proxy_server.wrap(reader, writer)
            await callback(reader, writer, host, port)

        await self.server_provider.start_server(server_provider_callback)


class ProxyOutBound(OutBound):
    def __init__(self, client_provider: ClientProvider, proxy_client: ProxyClient):
        self.client_provider = client_provider
        self.proxy_client = proxy_client

    async def open_connection(self, host: str, port: int, callback: OutBoundCallback):
        async def client_provider_callback(reader: AsyncReader, writer: AsyncWriter):
            reader, writer = await self.proxy_client.wrap(reader, writer, host, port)
            await callback(reader, writer)

        await self.client_provider.open_connection(client_provider_callback)


class BlockOutBound(OutBound):
    async def open_connection(self, host: str, port: int, callback: OutBoundCallback):
        _ = host, port, callback


class DirectOutBound(OutBound):
    async def open_connection(self, host: str, port: int, callback: OutBoundCallback):
        reader, writer = await aio.open_connection(host, port)
        try:
            await callback(AIOReader(reader), AIOWriter(writer))
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

    async def open_connection(self, host: str, port: int, callback: OutBoundCallback):
        outbound = choice(self.outbounds)
        await outbound.open_connection(host, port, callback)


type Tag = str
type Tags = dict[str, Tag]


def match_tags(host: str, tags: Tags) -> Optional[Tag]:
    tag = tags.get(host)
    if tag is not None:
        return tag
    sp = host.split(".", 1)
    if len(sp) == 2:
        return match_tags(sp[1], tags)


class TagDispatchOutBound(OutBound):
    def __init__(
        self,
        tags: Tags,  # host -> tag
        default_tag: Tag,
        outbounds: dict[str, OutBound],  # tag -> outbound
    ):
        self.tags = tags
        self.default_tag = default_tag
        self.outbounds = outbounds

    def match_tags(self, host: str) -> Tag:
        return match_tags(host, self.tags) or self.default_tag

    async def open_connection(self, host: str, port: int, callback: OutBoundCallback):
        outbound = self.outbounds[self.match_tags(host)]
        await outbound.open_connection(host, port, callback)


class Server:
    def __init__(
        self,
        inbound: InBound,
        outbound: OutBound,
        logger: Optional[Logger] = None,
    ):
        self.inbound = inbound
        self.outbound = outbound
        self.logger = logger or getLogger("proxy")
        self.tasks: set[aio.Task] = set()

    async def start_server(self):
        async def inbound_callback(
            in_reader: AsyncReader, in_writer: AsyncWriter, host: str, port: int
        ):
            self.logger.info("connect to %s %d", host, port)

            async def outbound_callback(
                out_reader: AsyncReader, out_writer: AsyncWriter
            ):
                task1 = aio.create_task(pipe_async(in_reader, out_writer))
                task2 = aio.create_task(pipe_async(out_reader, in_writer))
                for task in task1, task2:
                    self.tasks.add(task)
                    task.add_done_callback(self.tasks.discard)
                try:
                    await aio.gather(task1, task2)
                except Exception as e:
                    self.logger.debug("except while piping: %s %s", type(e), e)
                finally:
                    for task in task1, task2:
                        if not task.cancelled():
                            task.cancel()

            await self.outbound.open_connection(host, port, outbound_callback)

        await self.inbound.start_server(inbound_callback)


class Config(ABC):
    @classmethod
    @abstractmethod
    def from_data(cls, data: dict) -> Any:
        pass


class RegistrableConfig(Config):
    registry: dict
    type: str

    def __init_subclass__(cls):
        if hasattr(cls, "registry") and hasattr(cls, "type"):
            cls.registry[cls.type] = cls
        return super().__init_subclass__()

    @classmethod
    def from_data_by_type(cls, data: dict) -> Any:
        def unpack_type(type, **data):
            return type, data

        type, data = unpack_type(**data)
        return cls.registry[type].from_data(data)


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

    class Data(BaseModel):
        host: Optional[str] = None
        port: Optional[int] = None

    @classmethod
    def from_data(cls, data: dict) -> TCPServerProvider:
        cls.Data.model_validate(data)
        return TCPServerProvider(**data)


class TCPClientProviderConfig(ClientProviderConfig):
    type = "tcp"

    class Data(BaseModel):
        host: Optional[str] = None
        port: Optional[int] = None
        ssl: Optional[bool] = None
        server_hostname: Optional[str] = None

    @classmethod
    def from_data(cls, data: dict) -> TCPClientProvider:
        cls.Data.model_validate(data)
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

    class Data(BaseModel):
        server_provider: dict
        proxy_server: dict

    @classmethod
    def from_data(cls, data: dict) -> ProxyInBound:
        cls.Data.model_validate(data)
        server_provider: ServerProvider = ServerProviderConfig.from_data_by_type(
            data["server_provider"]
        )
        proxy_server: ProxyServer = ProxyServerConfig.from_data_by_type(
            data["proxy_server"]
        )
        return ProxyInBound(server_provider=server_provider, proxy_server=proxy_server)


class ProxyOutBoundConfig(OutBoundConfig):
    type = "proxy"

    class Data(BaseModel):
        client_provider: dict
        proxy_client: dict

    @classmethod
    def from_data(cls, data: dict) -> ProxyOutBound:
        cls.Data.model_validate(data)
        client_provider: ClientProvider = ClientProviderConfig.from_data_by_type(
            data["client_provider"]
        )
        proxy_client: ProxyClient = ProxyClientConfig.from_data_by_type(
            data["proxy_client"]
        )
        return ProxyOutBound(client_provider=client_provider, proxy_client=proxy_client)


class BlockOutBoundConfig(OutBoundConfig):
    type = "block"

    class Data(BaseModel):
        pass

    @classmethod
    def from_data(cls, data: dict) -> BlockOutBound:
        cls.Data.model_validate(data)
        return BlockOutBound()


class DirectOutBoundConfig(OutBoundConfig):
    type = "direct"

    class Data(BaseModel):
        pass

    @classmethod
    def from_data(cls, data: dict) -> DirectOutBound:
        cls.Data.model_validate(data)
        return DirectOutBound()


class MultiInBoundConfig(InBoundConfig):
    type = "multi"

    class Data(BaseModel):
        inbounds: Sequence[dict]

    @classmethod
    def from_data(cls, data: dict) -> MultiInBound:
        cls.Data.model_validate(data)
        inbounds: Sequence[InBound] = list(
            map(
                InBoundConfig.from_data_by_type,
                data["inbounds"],
            )
        )
        return MultiInBound(inbounds)


class RandDispatchOutBoundConfig(OutBoundConfig):
    type = "rand_dispatch"

    class Data(BaseModel):
        outbounds: Sequence[dict]

    @classmethod
    def from_data(cls, data: dict) -> RandDispatchOutBound:
        cls.Data.model_validate(data)
        outbounds: Sequence[OutBound] = list(
            map(
                OutBoundConfig.from_data_by_type,
                data["outbounds"],
            )
        )
        return RandDispatchOutBound(outbounds)


class TagsProviderConfig(RegistrableConfig):
    registry = dict()

    @classmethod
    @abstractmethod
    def from_data(cls, data: dict) -> Tags:
        pass


class MultiTagsProviderConfig(TagsProviderConfig):
    type = "multi"

    class Data(BaseModel):
        providers: Sequence[dict]

    @classmethod
    def from_data(cls, data: dict) -> Tags:
        cls.Data.model_validate(data)
        tags: Tags = dict()
        for provider in data["providers"]:
            provider_tags: Tags = TagsProviderConfig.from_data_by_type(provider)
            for host, tag in provider_tags:
                tags[host] = tag
        return tags


class DataTagsProviderConfig(TagsProviderConfig):
    type = "data"

    class Data(BaseModel):
        tags: Tags

    @classmethod
    def from_data(cls, data: dict) -> dict[str, str]:
        cls.Data.model_validate(data)
        return data["tags"]


class TagDispatchOutBoundConfig(OutBoundConfig):
    type = "tag_dispatch"

    class Data(BaseModel):
        tags: dict
        default_tag: Tag
        outbounds: dict[str, dict]

    @classmethod
    def from_data(cls, data: dict) -> TagDispatchOutBound:
        cls.Data.model_validate(data)
        return TagDispatchOutBound(
            tags=TagsProviderConfig.from_data_by_type(data["tags"]),
            default_tag=data["default_tag"],
            outbounds={
                tag: OutBoundConfig.from_data_by_type(outbound)
                for tag, outbound in data["outbounds"].items()
            },
        )


class ServerConfig(Config):
    class Data(BaseModel):
        inbound: dict
        outbound: dict

    @classmethod
    def from_data(cls, data: dict) -> Server:
        cls.Data.model_validate(data)
        inbound = InBoundConfig.from_data_by_type(data["inbound"])
        outbound = OutBoundConfig.from_data_by_type(data["outbound"])
        return Server(inbound=inbound, outbound=outbound)


class JsonConfig(RegistrableConfig):
    type = "json"

    class Data(BaseModel):
        path: str

    @classmethod
    def from_data(cls, data: dict) -> Any:
        cls.Data.model_validate(data)
        with open(data["path"], "r") as f:
            return cls.from_data_by_type(json.load(f))


class JsonInBoundConfig(JsonConfig, InBoundConfig):
    pass


class JsonOutBoundConfig(JsonConfig, OutBoundConfig):
    pass


class JsonTagsProviderConfig(JsonConfig, TagsProviderConfig):
    pass
