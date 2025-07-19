from argparse import ArgumentParser
from typing import Self
from collections.abc import Sequence
from dataclasses import dataclass
import json
import binascii
import base64
import datetime
import requests

## V2rayN Subscribe
#
# https://github.com/2dust/v2rayN/wiki/分享链接格式说明(ver-2)
#
# Tree of dot dir:
#
#   .vmessc/
#     \_ sub.url                 # subscribe url
#     \_ sub.txt                 # subscribe content
#     \_ sub.bak/                # subscribe backup dir
#          \_ #timestamp#.txt    # subscribe backup
#     \_ outbound.json           # outbound config generated from subscribe


@dataclass
class Node:
    v: str
    ps: str
    add: str
    port: str
    id: str
    aid: str = ""
    scy: str = "auto"
    net: str = "tcp"
    type: str = "none"
    host: str = ""
    path: str = "/"
    tls: str = ""
    sni: str = ""
    alpn: str = ""
    fp: str = ""

    @property
    def resolved_port(self) -> int:
        return int(self.port)

    @property
    def resolved_host(self) -> str:
        return self.host or self.add

    @property
    def resolved_sni(self) -> str:
        return self.sni or self.resolved_host

    @property
    def resolved_tls(self) -> bool:
        return self.tls == "tls"

    @property
    def ws_schema(self) -> str:
        return "wss" if self.resolved_tls else "ws"

    @property
    def ws_uri(self) -> str:
        return (
            f"{self.ws_schema}://{self.resolved_host}:{self.resolved_port}{self.path}"
        )

    @property
    def client_provider(self) -> dict:
        match self.net:
            case "tcp":
                client_provider = {
                    "type": "tcp",
                    "host": self.add,
                    "port": self.resolved_port,
                }
            case "ws":
                client_provider = {
                    "type": "ws",
                    "host": self.add,
                    "port": self.resolved_port,
                    "uri": self.ws_uri,
                }
            case _:
                raise Exception("Invalid net", self.net)
        if self.resolved_tls:
            client_provider["ssl"] = True
            client_provider["server_hostname"] = self.resolved_sni
        return client_provider

    @property
    def proxy_client(self) -> dict:
        return {"type": "vmess", "id": self.id}

    @property
    def outbound(self) -> dict:
        return {
            "type": "proxy",
            "client_provider": self.client_provider,
            "proxy_client": self.proxy_client,
        }

    @classmethod
    def parse(cls, content: str) -> Sequence[Self]:
        nodes: list[Self] = list()
        for url in content.splitlines():
            url = url.strip()
            if url.startswith("vmess://"):
                content = url.removeprefix("vmess://")
                content = base64.b64decode(content).decode()
                nodes.append(cls(**json.loads(content)))
        return nodes


class Subscribe:
    def __init__(
        self,
        url_path=".vmessc/sub.url",
        content_path=".vmessc/sub.txt",
        content_backup_dir=".vmessc/sub.bak/",
        outbound_path=".vmessc/outbound.json",
    ):
        self.url_path = url_path
        self.content_path = content_path
        self.content_backup_dir = content_backup_dir
        self.outbound_path = outbound_path

    @property
    def content_backup_path(self) -> str:
        return f"{self.content_backup_dir}{self.timestamp}.txt"

    @property
    def timestamp(self) -> str:
        return datetime.datetime.now().isoformat()

    @property
    def url(self) -> str:
        with open(self.url_path, "r") as f:
            return f.read().strip()

    def fetch(self):
        resp = requests.get(self.url, allow_redirects=True)
        resp.raise_for_status()
        content = resp.text
        try:
            content = base64.b64decode(content).decode()
        except binascii.Error:
            pass
        for path in self.content_backup_path, self.content_path:
            with open(path, "w") as f:
                f.write(content)

    def load(self) -> Sequence[Node]:
        with open(self.content_path, "r") as f:
            return Node.parse(f.read())

    @staticmethod
    def list_nodes(nodes: Sequence[Node]):
        for i, node in enumerate(nodes):
            print(i, node)

    def list(self):
        nodes = self.load()
        self.list_nodes(nodes)

    def gen(self):
        nodes = self.load()
        self.list_nodes(nodes)
        select = input("select: ")
        outbounds = [nodes[int(i)].outbound for i in select.split()]
        if len(outbounds) == 0:
            raise Exception("No nodes are selected")
        elif len(outbounds) == 1:
            outbound = outbounds[0]
        else:
            outbound = {"type": "rand_dispatch", "outbounds": outbounds}
        with open(self.outbound_path, "w") as f:
            json.dump(outbound, f)


def main():
    parser = ArgumentParser()
    parser.add_argument("command")
    args = parser.parse_args()
    sub = Subscribe()
    match args.command:
        case "fetch":
            sub.fetch()
        case "list":
            sub.list()
        case "gen":
            sub.gen()
        case command:
            raise Exception("Invalid command", command)


if __name__ == "__main__":
    main()
