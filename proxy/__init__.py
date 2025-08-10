from typing import Any, Optional
from collections.abc import Callable, Awaitable, Sequence
from abc import ABC, abstractmethod
from functools import reduce
from dataclasses import dataclass
from random import choice
from logging import getLogger
import json
import asyncio as aio

from proxy.struct import (
    AsyncReader,
    AsyncWriter,
    AIOReader,
    AIOWriter,
    pipe_async,
)


logger = getLogger("proxy")


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
    async def handshake(self, stream: Stream, callback: ProxyServerCallback):
        pass


class ProxyClient(ABC):
    @abstractmethod
    async def handshake(
        self, stream: Stream, request: ProxyRequest, callback: ProxyClientCallback
    ):
        pass


type InBoundCallback = ProxyServerCallback
type OutBoundCallback = StreamCallback


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
    async def open_connection(self, request: Request, callback: OutBoundCallback):
        pass


class ProxyInBound(InBound):
    def __init__(self, server_provider: ServerProvider, proxy_server: ProxyServer):
        self.server_provider = server_provider
        self.proxy_server = proxy_server

    async def start_server(self, callback: InBoundCallback):
        async def server_callback(stream: Stream):
            await self.proxy_server.handshake(stream, callback)

        await self.server_provider.start_server(server_callback)


class ProxyOutBound(OutBound):
    def __init__(self, client_provider: ClientProvider, proxy_client: ProxyClient):
        self.client_provider = client_provider
        self.proxy_client = proxy_client

    async def open_connection(self, request: Request, callback: OutBoundCallback):
        async def client_callback(stream: Stream):
            await self.proxy_client.handshake(stream, request.proxy, callback)

        await self.client_provider.open_connection(client_callback)


class BlockOutBound(OutBound):
    async def open_connection(self, request: Request, callback: OutBoundCallback):
        _ = request, callback


class DirectOutBound(OutBound):
    async def open_connection(self, request: Request, callback: OutBoundCallback):
        client_provider = TCPClientProvider(
            host=request.proxy.host, port=request.proxy.port
        )
        await client_provider.open_connection(callback)


class MultiInBound(InBound):
    def __init__(self, inbounds: Sequence[InBound]):
        self.inbounds = inbounds

    async def start_server(self, callback: InBoundCallback):
        coros = [inbound.start_server(callback) for inbound in self.inbounds]
        await aio.gather(*coros)


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
    connect_log = "connect to [%s] %s %d"
    connect_except_log = "except while connecting to [%s] %s %d: %s %s"

    async def open_connection(self, request: Request, callback: MiddleWareCallback):
        host = request.proxy.host
        port = request.proxy.port
        tag = request.context.get("tag", "default")
        logger.info(self.connect_log, tag, host, port)
        try:
            await callback(request)
        except Exception as e:
            logger.debug(self.connect_except_log, tag, host, port, type(e), e)
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

    pipe_except_log = "except while piping: %s %s"

    def __init__(self, inbound: InBound, outbound: OutBound):
        self.inbound = inbound
        self.outbound = outbound
        self.tasks: set[aio.Task] = set()

    async def pipe(self, in_stream: Stream, out_stream: Stream):
        tasks = (
            aio.create_task(pipe_async(in_stream.reader, out_stream.writer)),
            aio.create_task(pipe_async(out_stream.reader, in_stream.writer)),
        )
        for task in tasks:
            self.tasks.add(task)
            task.add_done_callback(self.tasks.discard)
        try:
            await aio.gather(*tasks)
        except Exception as e:
            logger.debug(self.pipe_except_log, type(e), e)
        finally:
            for task in tasks:
                if not task.cancelled():
                    task.cancel()

    async def start_server(self):
        async def inbound_callback(in_stream: Stream, proxy: ProxyRequest):
            async def outbound_callback(out_stream: Stream):
                await self.pipe(in_stream, out_stream)

            request = Request(proxy, dict())
            await self.outbound.open_connection(request, outbound_callback)

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
            for host, tag in provider_tags.items():
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
