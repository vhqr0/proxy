import sys
from argparse import ArgumentParser
from importlib import import_module
import json
import logging.config
import asyncio as aio
from proxy import ServerConfig

parser = ArgumentParser(
    prog="proxy",
    description="A simple proxy tool",
)

parser.add_argument("-c", "--config", default="config.json")


def main(args):
    args = parser.parse_args(args)
    with open(args.config, "r") as f:
        config = json.load(f)
    for plugin in config["plugins"]:
        import_module(plugin)
    logging.config.dictConfig(config["logger"])
    server = ServerConfig.from_data(config["server"])
    try:
        aio.run(server.start_server())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main(sys.argv[1:])
