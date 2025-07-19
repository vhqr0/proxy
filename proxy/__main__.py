from argparse import ArgumentParser
from importlib import import_module
import logging.config
import json

from proxy import ServerConfig


def main():
    parser = ArgumentParser()
    parser.add_argument("-c", "--config", default="config.json")
    args = parser.parse_args()
    with open(args.config, "r") as f:
        config = json.load(f)
    for plugin in config["plugins"]:
        import_module(plugin)
    logging.config.dictConfig(config["logger"])
    server = ServerConfig.from_data(config["server"])
    try:
        server.run()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
