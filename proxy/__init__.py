from typing import Any
from collections.abc import Callable, Awaitable, Iterable
from abc import ABC, abstractmethod
import struct as format_struct
import random
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


class BufferedReader(Reader):
    def __init__(self, buffer=b"", buffer_limit=64 * 1024):
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
    def __init__(self, buffer=b"", buffer_limit=64 * 1024):
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
        self.writer.write(data)
        await self.writer.drain()


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

    def unpack_many(self, b: bytes) -> Iterable[Any]:
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

    def pack_many(self, data: Iterable[Any]) -> bytes:
        bio = io.BytesIO()
        bio_writer = IOWriter(bio)
        for _data in data:
            self.write(bio_writer, _data)
        return bio.getvalue()

    async def pack_one_then_write_async(self, writer: AsyncWriter, data: Any):
        await writer.write_async(self.pack_one(data))

    async def pack_many_then_write_async(
        self, writer: AsyncWriter, data: Iterable[Any]
    ):
        await writer.write_async(self.pack_many(data))


class WrapStruct(Struct):
    def __init__(self, struct: Struct, pack_fn: Callable, unpack_fn: Callable):
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


class TupleStruct(Struct):
    def __init__(self, structs: Iterable[Struct]):
        self.structs = structs

    def read(self, reader: Reader) -> Iterable[Any]:
        data = []
        for struct in self.structs:
            data.append(struct.read(reader))
        return data

    async def read_async(self, reader: AsyncReader) -> Iterable[Any]:
        data = []
        for struct in self.structs:
            data.append(await struct.read_async(reader))
        return data


class DictStruct(Struct):
    def __init__(
        self,
        key_structs: Iterable[tuple[str, Struct | Callable[[dict[str, Any]], Struct]]],
    ):
        self.key_structs = key_structs

    def read(self, reader: Reader) -> dict[str, Any]:
        data = dict()
        for key, struct in self.key_structs:
            if not isinstance(struct, Struct):
                struct = struct(data)
            data[key] = struct.read(reader)
        return data

    async def read_async(self, reader: AsyncReader) -> dict[str, Any]:
        data = dict()
        for key, struct in self.key_structs:
            if not isinstance(struct, Struct):
                struct = struct(data)
            data[key] = await struct.read_async(reader)
        return data

    def write(self, writer: Writer, data: dict[str, Any]):
        for key, struct in self.key_structs:
            if not isinstance(struct, Struct):
                struct = struct(data)
            struct.write(writer, data[key])

    async def write_async(self, writer: AsyncWriter, data: dict[str, Any]):
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

    def read(self, reader: Reader) -> Iterable[int]:
        b = reader.readexactly(self.format.size)
        return self.format.unpack(b)

    async def read_async(self, reader: AsyncReader) -> Iterable[int]:
        b = await reader.readexactly_async(self.format.size)
        return self.format.unpack(b)

    def write(self, writer: Writer, data: Iterable[int]):
        writer.write(self.format.pack(*data))

    async def writer(self, writer: AsyncWriter, data: Iterable[int]):
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


class ServerProvider(ABC):
    @abstractmethod
    async def start_server(
        self, callback: Callable[[AsyncReader, AsyncWriter], Awaitable]
    ):
        pass


class ClientProvider(ABC):
    @abstractmethod
    async def open_connection(
        self, callback: Callable[[AsyncReader, AsyncWriter], Awaitable]
    ):
        pass


class AIOServerProvider(ServerProvider):
    def __init__(self, **kwargs):
        self.kwargs = kwargs

    async def start_server(
        self, callback: Callable[[AsyncReader, AsyncWriter], Awaitable]
    ):
        async def aio_callback(reader: aio.StreamReader, writer: aio.StreamWriter):
            await callback(AIOReader(reader), AIOWriter(writer))

        server = await aio.start_server(aio_callback, **self.kwargs)
        async with server:
            await server.serve_forever()


class AIOClientProvider(ClientProvider):
    def __init__(self, **kwargs):
        self.kwargs = kwargs

    async def open_connection(
        self, callback: Callable[[AsyncReader, AsyncWriter], Awaitable]
    ):
        reader, writer = await aio.open_connection(**self.kwargs)
        try:
            await callback(AIOReader(reader), AIOWriter(writer))
        finally:
            writer.close()
            await writer.wait_closed()


class ProxyServer(ABC):
    @abstractmethod
    async def wrap(
        self, reader: AsyncReader, writer: AsyncWriter
    ) -> tuple[AsyncReader, AsyncWriter, str, int]:
        """Wrap reader/writer to a new pair of reader/writer, and request host/port."""
        pass


class ProxyClient(ABC):
    @abstractmethod
    async def wrap(
        self, reader: AsyncReader, writer: AsyncWriter, host: str, port: int
    ) -> tuple[AsyncReader, AsyncWriter]:
        """Wrap reader/writer to a new pair of reader/writer, connect to host/pair via target proxy server."""
        pass


class InBound(ABC):
    @abstractmethod
    async def start_server(
        self, callback: Callable[[AsyncReader, AsyncWriter, str, int], Awaitable]
    ):
        pass


class ProxyInBound(InBound):
    def __init__(self, server_provider: ServerProvider, proxy_server: ProxyServer):
        self.server_provider = server_provider
        self.proxy_server = proxy_server

    async def start_server(
        self, callback: Callable[[AsyncReader, AsyncWriter, str, int], Awaitable]
    ):
        async def server_provider_callback(reader: AsyncReader, writer: AsyncWriter):
            reader, writer, host, port = await self.proxy_server.wrap(reader, writer)
            await callback(reader, writer, host, port)

        await self.server_provider.start_server(server_provider_callback)


class IngressInBound(InBound):
    def __init__(self, inbounds: Iterable[InBound]):
        self.inbounds = inbounds

    async def start_server(
        self, callback: Callable[[AsyncReader, AsyncWriter, str, int], Awaitable]
    ):
        await aio.gather(
            *map(lambda inbound: inbound.start_server(callback), self.inbounds)
        )


class OutBound(ABC):
    @abstractmethod
    async def open_connection(
        self,
        host: str,
        port: int,
        callback: Callable[[AsyncReader, AsyncWriter], Awaitable],
    ):
        pass


class BlockOutBound(OutBound):
    async def open_connection(
        self,
        host: str,
        port: int,
        callback: Callable[[AsyncReader, AsyncWriter], Awaitable],
    ):
        _ = host, port, callback


class DirectOutBound(OutBound):
    async def open_connection(
        self,
        host: str,
        port: int,
        callback: Callable[[AsyncReader, AsyncWriter], Awaitable],
    ):
        reader, writer = await aio.open_connection(host, port)
        try:
            await callback(AIOReader(reader), AIOWriter(writer))
        finally:
            writer.close()
            await writer.wait_closed()


class ProxyOutBound(OutBound):
    def __init__(self, client_provider: ClientProvider, proxy_client: ProxyClient):
        self.client_provider = client_provider
        self.proxy_client = proxy_client

    async def open_connection(
        self,
        host: str,
        port: int,
        callback: Callable[[AsyncReader, AsyncWriter], Awaitable],
    ):
        async def client_provider_callback(reader: AsyncReader, writer: AsyncWriter):
            reader, writer = await self.proxy_client.wrap(reader, writer, host, port)
            await callback(reader, writer)

        await self.client_provider.open_connection(client_provider_callback)


class RandDispatchOutBound(OutBound):
    def __init__(self, outbounds: Iterable[OutBound]):
        self.outbounds = list(outbounds)

    async def open_connection(
        self,
        host: str,
        port: int,
        callback: Callable[[AsyncReader, AsyncWriter], Awaitable],
    ):
        outbound = random.choice(self.outbounds)
        await outbound.open_connection(host, port, callback)


class Server:
    def __init__(self, inbound: InBound, outbound: OutBound):
        self.inbound = inbound
        self.outbound = outbound

    async def start_server(self):
        async def inbound_callback(
            in_reader: AsyncReader, in_writer: AsyncWriter, host: str, port: int
        ):
            async def outbound_callback(
                out_reader: AsyncReader, out_writer: AsyncWriter
            ):
                await aio.gather(
                    self.pipe(in_reader, out_writer),
                    self.pipe(out_reader, in_writer),
                )

            await self.outbound.open_connection(host, port, outbound_callback)

        await self.inbound.start_server(inbound_callback)

    @staticmethod
    async def pipe(reader: AsyncReader, writer: AsyncWriter):
        while True:
            b = await reader.read_async()
            if len(b) == 0:
                return
            else:
                await writer.write_async(b)


class Config(ABC):
    @classmethod
    @abstractmethod
    def from_data(cls, data: dict) -> Any:
        pass


class RegistrableConfig(Config):
    registry: dict

    def __init_subclass__(cls):
        if hasattr(cls, "registry") and hasattr(cls, "type"):
            cls.registry[getattr(cls, "type")] = cls
        return super().__init_subclass__()

    @classmethod
    def from_data_by_type(cls, data: dict) -> Any:
        return cls.registry[data["type"]].from_data(data)


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


class AIOServerProviderConfig(ServerProviderConfig):
    type = "aio"

    @classmethod
    def from_data(cls, data: dict) -> AIOServerProvider:
        return AIOServerProvider(**data["kwargs"])


class AIOClientProviderConfig(ClientProviderConfig):
    type = "aio"

    @classmethod
    def from_data(cls, data: dict) -> AIOClientProvider:
        return AIOClientProvider(**data["kwargs"])


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
    def from_data(cls, data: dict) -> ProxyInBound:
        server_provider: ServerProvider = ServerProviderConfig.from_data_by_type(
            data["server_provider"]
        )
        proxy_server: ProxyServer = ProxyServerConfig.from_data_by_type(
            data["proxy_server"]
        )
        return ProxyInBound(server_provider=server_provider, proxy_server=proxy_server)


class IngressInBoundConfig(InBoundConfig):
    type = "ingress"

    @classmethod
    def from_data(cls, data: dict) -> IngressInBound:
        inbounds: Iterable[InBound] = map(
            InBoundConfig.from_data_by_type,
            data["inbounds"],
        )
        return IngressInBound(inbounds)


class BlockOutBoundConfig(OutBoundConfig):
    type = "block"

    @classmethod
    def from_data(cls, data: dict) -> BlockOutBound:
        _ = data
        return BlockOutBound()


class DirectOutBoundConfig(OutBoundConfig):
    type = "direct"

    @classmethod
    def from_data(cls, data: dict) -> DirectOutBound:
        _ = data
        return DirectOutBound()


class ProxyOutBoundConfig(OutBoundConfig):
    type = "proxy"

    @classmethod
    def from_data(cls, data: dict) -> ProxyOutBound:
        client_provider: ClientProvider = ClientProviderConfig.from_data_by_type(
            data["client_provider"]
        )
        proxy_client: ProxyClient = ProxyClientConfig.from_data_by_type(
            data["proxy_client"]
        )
        return ProxyOutBound(client_provider=client_provider, proxy_client=proxy_client)


class RandDispatchOutBoundConfig(OutBoundConfig):
    type = "rand_dispatch"

    @classmethod
    def from_data(cls, data: dict) -> RandDispatchOutBound:
        outbounds: Iterable[OutBound] = map(
            OutBoundConfig.from_data_by_type,
            data["outbounds"],
        )
        return RandDispatchOutBound(outbounds)


class ServerConfig(Config):
    @classmethod
    def from_data(cls, data: dict) -> Server:
        inbound = InBoundConfig.from_data_by_type(data["inbound"])
        outbound = OutBoundConfig.from_data_by_type(data["outbound"])
        return Server(inbound=inbound, outbound=outbound)
