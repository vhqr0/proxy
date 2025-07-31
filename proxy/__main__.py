from argparse import ArgumentParser
from collections.abc import Sequence
from importlib import import_module
import logging.config
import json

from proxy import ServerConfig


class Proxy:
    def __init__(self, plugins: Sequence[str], logger: dict, server: dict):
        self.plugins = plugins
        self.logger = logger
        self.server = server

    def run(self):
        self.load_plugins()
        self.config_logger()
        self.start_server()

    def load_plugins(self):
        for plugin in self.plugins:
            import_module(plugin)

    def config_logger(self):
        logging.config.dictConfig(self.logger)

    @property
    def _server(self):
        return ServerConfig.from_data(self.server)

    def start_server(self):
        server = self._server
        try:
            server.run()
        except KeyboardInterrupt:
            pass

    @classmethod
    def from_json(cls, path: str):
        with open(path, "r") as f:
            data = json.load(f)
        return cls(**data)

    @classmethod
    def from_args(cls):
        parser = ArgumentParser()
        parser.add_argument("-c", "--config", default="config.json")
        args = parser.parse_args()
        return cls.from_json(args.config)

    @classmethod
    def main(cls):
        proxy = cls.from_args()
        proxy.run()


if __name__ == "__main__":
    Proxy.main()
