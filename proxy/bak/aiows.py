from collections.abc import Callable, Awaitable
import aiohttp
import aiohttp.web as aioweb
import aiohttp.client_ws as aiowsc

from proxy import (
    AsyncReader,
    BufferedAsyncReader,
    AsyncWriter,
    ServerProvider,
    ClientProvider,
)


class AIOWSReader(BufferedAsyncReader):
    def __init__(self, ws: aiowsc.ClientWebSocketResponse | aioweb.WebSocketResponse):
        self.ws = ws

    async def read1_async(self) -> bytes:
        msg = await self.ws.receive()
        if msg.type == aiohttp.WSMsgType.BINARY:
            return msg.data
        if msg.type == aiohttp.WSMsgType.TEXT:
            return msg.data.decode()
        if msg.type == aiohttp.WSMsgType.CLOSE:
            return b""
        raise Exception("Unknown ws msg type")


class AIOWSWriter(AsyncWriter):
    def __init__(self, ws: aiowsc.ClientWebSocketResponse | aioweb.WebSocketResponse):
        self.ws = ws

    async def write_async(self, data: bytes):
        await self.ws.send_bytes(data)


class AIOWSServerProvider(ServerProvider):
    def __init__(self, path="/", **kwargs):
        self.path = path
        self.kwargs = kwargs

    async def start_server(
        self, callback: Callable[[AsyncReader, AsyncWriter], Awaitable]
    ):
        async def aiows_callback(request: aioweb.Request) -> aioweb.WebSocketResponse:
            ws = aioweb.WebSocketResponse()
            await ws.prepare(request)
            await callback(AIOWSReader(ws), AIOWSWriter(ws))
            return ws

        app = aioweb.Application()
        app.add_routes([aioweb.get(self.path, aiows_callback)])
        await aioweb._run_app(app, **self.kwargs)


class AIOWSClientProvider(ClientProvider):
    def __init__(self, url):
        self.url = url

    async def open_connection(
        self, callback: Callable[[AsyncReader, AsyncWriter], Awaitable]
    ):
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(self.url) as ws:
                await callback(AIOWSReader(ws), AIOWSWriter(ws))
