import websockets
import websockets.asyncio.connection as ws_conn
import websockets.asyncio.server as ws_server
import websockets.asyncio.client as ws_client

from proxy import (
    AsyncWriter,
    AsyncBufferedReader,
    ServerCallback,
    ClientCallback,
    Stream,
    ServerProvider,
    ClientProvider,
    ServerProviderConfig,
    ClientProviderConfig,
)


class WSReader(AsyncBufferedReader):
    def __init__(self, ws: ws_conn.Connection, **kwargs):
        self.ws = ws
        super().__init__(**kwargs)

    async def read1_async(self) -> bytes:
        try:
            while True:
                msg = await self.ws.recv()
                if isinstance(msg, str):
                    msg = msg.encode()
                if len(msg) > 0:
                    return msg
        except websockets.exceptions.ConnectionClosedOK:
            return b""


class WSWriter(AsyncWriter):
    def __init__(self, ws: ws_conn.Connection):
        self.ws = ws

    async def write_async(self, data: bytes):
        if len(data) > 0:
            await self.ws.send(data)


class WSServerProvider(ServerProvider):
    def __init__(self, **kwargs):
        self.kwargs = kwargs

    async def start_server(self, callback: ServerCallback):
        async def ws_callback(ws: ws_conn.Connection):
            await callback(Stream(WSReader(ws), WSWriter(ws)))

        server = await ws_server.serve(ws_callback, **self.kwargs)
        async with server:
            await server.serve_forever()


class WSClientProvider(ClientProvider):
    def __init__(self, **kwargs):
        self.kwargs = kwargs

    async def open_connection(self, callback: ClientCallback):
        async with ws_client.connect(**self.kwargs) as ws:
            await callback(Stream(WSReader(ws), WSWriter(ws)))


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
