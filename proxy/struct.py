from typing import Optional, Any
from collections.abc import Callable, Sequence
from abc import ABC, abstractmethod
from struct import Struct as _Struct
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


class CommonBufferedReader:
    def __init__(self, buffer: bytes = b"", buffer_limit: int = 64 * 1024):
        self.buffer = buffer
        self.buffer_limit = buffer_limit

    def check_buffer_limit(self):
        if len(self.buffer) >= self.buffer_limit:
            raise BufferLimitOverrunError()

    def extend_buffer(self, b: bytes):
        if len(b) == 0:
            raise BufferIncompleteReadError()
        self.buffer += b

    def maybe_pop_all(self) -> Optional[bytes]:
        if len(self.buffer) > 0:
            b, self.buffer = self.buffer, b""
            return b

    def maybe_pop_exactly(self, n: int) -> Optional[bytes]:
        if len(self.buffer) >= n:
            b = self.buffer[:n]
            self.buffer = self.buffer[n:]
            return b

    def maybe_pop_until(self, sep: bytes) -> Optional[bytes]:
        sp = self.buffer.split(sep, 1)
        if len(sp) == 2:
            b, self.buffer = sp
            return b


class BufferedReader(Reader, CommonBufferedReader):
    @abstractmethod
    def read1(self) -> bytes:
        pass

    def peek_more(self):
        self.check_buffer_limit()
        self.extend_buffer(self.read1())

    def read(self) -> bytes:
        b = self.maybe_pop_all()
        if b is not None:
            return b
        else:
            return self.read1()

    def readexactly(self, n: int) -> bytes:
        while True:
            b = self.maybe_pop_exactly(n)
            if b is not None:
                return b
            else:
                self.peek_more()

    def readuntil(self, sep: bytes) -> bytes:
        while True:
            b = self.maybe_pop_until(sep)
            if b is not None:
                return b
            else:
                self.peek_more()


class AsyncBufferedReader(AsyncReader, CommonBufferedReader):
    @abstractmethod
    async def read1_async(self) -> bytes:
        pass

    async def peek_more_async(self):
        self.check_buffer_limit()
        self.extend_buffer(await self.read1_async())

    async def read_async(self) -> bytes:
        b = self.maybe_pop_all()
        if b is not None:
            return b
        else:
            return await self.read1_async()

    async def readexactly_async(self, n: int) -> bytes:
        while True:
            b = self.maybe_pop_exactly(n)
            if b is not None:
                return b
            else:
                await self.peek_more_async()

    async def readuntil_async(self, sep: bytes) -> bytes:
        while True:
            b = self.maybe_pop_until(sep)
            if b is not None:
                return b
            else:
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

    def check_data_length(self, data):
        if len(data) != self.length:
            raise InvalidFixedFrameLengthError()

    def write(self, writer: Writer, data: bytes):
        self.check_data_length(data)
        writer.write(data)

    async def write_async(self, writer: AsyncWriter, data: bytes):
        self.check_data_length(data)
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
    def __init__(self, format: _Struct | str):
        if isinstance(format, str):
            format = _Struct(format)
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
    def __init__(self, format: _Struct | str):
        if isinstance(format, str):
            format = _Struct(format)
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
