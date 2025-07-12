from collections.abc import Callable, Awaitable
import asyncio as aio
import websockets
import websockets.asyncio.connection as ws_conn
import websockets.asyncio.server as ws_server
import websockets.asyncio.client as ws_client

from proxy import (
    AsyncReader,
    BufferedAsyncReader,
    AsyncWriter,
    ServerProvider,
    ClientProvider,
    ServerProviderConfig,
    ClientProviderConfig,
)


class WSReader(BufferedAsyncReader):
    def __init__(self, ws: ws_conn.Connection, **kwargs):
        self.ws = ws
        super().__init__(**kwargs)

    async def read1_async(self) -> bytes:
        try:
            msg = await self.ws.recv()
            if isinstance(msg, str):
                msg = msg.encode()
            return msg
        except websockets.exceptions.ConnectionClosedOK:
            return b""


class WSWriter(AsyncWriter):
    def __init__(self, ws: ws_conn.Connection):
        self.ws = ws

    async def write_async(self, data: bytes):
        await self.ws.send(data)


class WSServerProvider(ServerProvider):
    def __init__(self, **kwargs):
        self.kwargs = kwargs

    async def start_server(
        self, callback: Callable[[AsyncReader, AsyncWriter], Awaitable]
    ):
        async def ws_callback(ws: ws_conn.Connection):
            await callback(WSReader(ws), WSWriter(ws))

        async with ws_server.serve(ws_callback, **self.kwargs):
            await aio.get_running_loop().create_future()


class WSClientProvider(ClientProvider):
    def __init__(self, **kwargs):
        self.kwargs = kwargs

    async def open_connection(
        self, callback: Callable[[AsyncReader, AsyncWriter], Awaitable]
    ):
        async with ws_client.connect(**self.kwargs) as ws:
            await callback(WSReader(ws), WSWriter(ws))


class WSServerProviderConfig(ServerProviderConfig):
    type = "ws"

    @classmethod
    def from_data(cls, data: dict) -> WSServerProvider:
        return WSServerProvider(**data)


class WSClientProviderConfig(ClientProviderConfig):
    type = "ws"

    @classmethod
    def from_data(cls, data: dict) -> WSClientProvider:
        return WSClientProvider(**data)
