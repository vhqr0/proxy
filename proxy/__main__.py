import sys
from argparse import ArgumentParser
from importlib import import_module
import json
import asyncio as aio
from proxy import ServerConfig

parser = ArgumentParser(
    prog="proxy",
    description="A simple proxy tool",
)

parser.add_argument("-c", "--config", default="config.json")


def main(args):
    args = parser.parse_args(args)
    config_path = args.config
    with open(config_path, "r") as f:
        config = json.load(f)
    plugins = config["plugins"]
    for plugin in plugins:
        import_module(plugin)
    server = ServerConfig.from_data(config["server"])
    aio.run(server.start_server())


if __name__ == "__main__":
    main(sys.argv[1:])
