from argparse import ArgumentParser
import re
import json

## Domain List Community
#
# origin: https://github.com/v2fly/domain-list-community
# fork: https://github.com/vhqr0/domain-list-community
#
# Tree of .vmessc:
#
#   .vmessc/
#     \_ domain-list-community/     # repo of domain-list-community
#          \_ data/                 # data dir of domain list
#     \_ tags.json                  # tags config generated from domain list


# supported commands: domain, full, include
# unsupported commands: regexp, keyword
# attrs are partially supported: only support one optional attr.

line_re = re.compile(r"^(([^\s:]+):)?([^\s]+)\s*(@([^\s]+))?$")

# line_re.match("baidu.com").groups()
#   => (None, None, 'baidu.com', None, None)
# line_re.match("domain:baidu.com @cn").groups()
#   => ('domain:', 'domain', 'baidu.com', '@cn', 'cn')
# line_re.match("include:baidu").groups()
#   => ('include:', 'include', 'baidu', None, None)

data_dir = ".vmessc/domain-list-community/data/"

entries = {"cn": "cn", "geolocation-!cn": "!cn"}

tags_dict = {"ads": "block", "cn": "direct", "!cn": "proxy"}

tags_path = ".vmessc/tags.json"


class DomainList:
    def __init__(
        self,
        data_dir: str = data_dir,
        entries: dict[str, str] = entries,
        tags_dict: dict[str, str] = tags_dict,
        tags_path: str = tags_path,
    ):
        self.data_dir = data_dir
        self.entries = entries
        self.tags_dict = tags_dict
        self.tags_path = tags_path
        self.tags: dict[str, str] = dict()

    @property
    def tags_provider(self):
        return {"type": "data", "tags": self.tags}

    def load(self):
        for name, default_tag in self.entries.items():
            self.load_entry(name, default_tag)

    def load_entry(self, name: str, default_tag: str):
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
                            self.load_entry(match[3], default_tag)
                        case _:
                            print("Skip unsupported command", line)

    def gen(self):
        with open(self.tags_path, "w") as f:
            json.dump(self.tags_provider, f)

    @classmethod
    def from_args(cls):
        parser = ArgumentParser()
        parser.add_argument("--data-dir", default=data_dir)
        parser.add_argument("--entries", type=json.loads, default=entries)
        parser.add_argument("--tags-dict", type=json.loads, default=tags_dict)
        parser.add_argument("--tags-path", default=tags_path)
        args = parser.parse_args()
        return cls(
            data_dir=args.data_dir,
            entries=args.entries,
            tags_dict=args.tags_dict,
            tags_path=args.tags_path,
        )

    @classmethod
    def main(cls):
        dl = cls.from_args()
        dl.load()
        dl.gen()


if __name__ == "__main__":
    DomainList.main()
