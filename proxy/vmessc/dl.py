from argparse import ArgumentParser
from collections.abc import Sequence
import re
import json

## Domain List Community
#
# origin: https://github.com/v2fly/domain-list-community
# fork: https://github.com/vhqr0/domain-list-community

line_re = re.compile(r"^(([^\s:]+):)?([^\s]+)\s*(@([^\s]+))?$")

# line_re.match("baidu.com").groups()
# => (None, None, 'baidu.com', None, None)
# line_re.match("domain:baidu.com @cn").groups()
# => ('domain:', 'domain', 'baidu.com', '@cn', 'cn')
# line_re.match("include:baidu").groups()
# => ('include:', 'include', 'baidu', None, None)

tags_dict = {"ads": "block", "cn": "direct", "!cn": "proxy"}

data_dir = ".vmessc/domain-list-community/data/"

entries = [
    ("cn", "cn"),
    ("geolocation-!cn", "!cn"),
]


class DomainList:
    def __init__(
        self,
        data_dir: str = data_dir,
        entries: Sequence[tuple[str, str]] = entries,
        tags_dict: dict[str, str] = tags_dict,
        config_path: str = ".vmessc/tags.json",
    ):
        self.tags: dict[str, str] = dict()
        self.data_dir = data_dir
        self.entries = entries
        self.tags_dict = tags_dict
        self.config_path = config_path

    def load_all(self):
        for name, default_tag in self.entries:
            self.load(name, default_tag)

    def load(self, name: str, default_tag: str):
        with open(self.data_dir + name, "r") as f:
            for line in f:
                line = line.split("#", 1)[0].strip()
                if len(line) > 0:
                    match = line_re.match(line)
                    if match is None:
                        raise Exception("Invalid line", line)
                    match match[2] or "domain":
                        case "domain" | "full":
                            host = match[3]
                            tag = match[5] or default_tag
                            self.tags[host] = self.tags_dict[tag]
                        case "include":
                            self.load(match[3], default_tag)
                        case _:
                            print("Skip unsupported command", line)

    def gen(self):
        with open(self.config_path, "w") as f:
            json.dump({"type": "data", "tags": self.tags}, f)


def main():
    parser = ArgumentParser()
    parser.add_argument("command")
    args = parser.parse_args()
    dl = DomainList()
    match args.command:
        case "gen":
            dl.load_all()
            dl.gen()
        case command:
            raise Exception("Invalid command", command)
