from typing import Optional
from collections.abc import Callable, Awaitable
from proxy import AsyncReader, AsyncWriter, OutBound, OutBoundConfig


def match_tags(host: str, tags: dict[str, str]) -> Optional[str]:
    tag = tags.get(host)
    if tag is not None:
        return tag
    sp = host.split(".", 1)
    if len(sp) == 2:
        return match_tags(sp[1], tags)


class TagDispatchOutBound(OutBound):
    def __init__(
        self,
        tags: dict[str, str],  # host -> tag
        default_tag: str,
        outbounds: dict[str, OutBound],  # tag -> outbound
    ):
        self.tags = tags
        self.default_tag = default_tag
        self.outbounds = outbounds

    async def open_connection(
        self,
        host: str,
        port: int,
        callback: Callable[[AsyncReader, AsyncWriter], Awaitable],
    ):
        tag = match_tags(host, self.tags) or self.default_tag
        outbound = self.outbounds[tag]
        await outbound.open_connection(host, port, callback)


def load_tags(tags_path: str) -> dict[str, str]:
    """Load tags file.
    The format of tags file looks like:
    ---
    proxy goggle.com
    block ads.google.com
    ...
    ---
    Each line contains a tag and a host name.
    Blank lines or lines that start with '#' are skipped."""
    tags: dict[str, str] = dict()
    with open(tags_path, "r") as f:
        for line in f:
            line = line.strip()
            if len(line) > 0 and not line.startswith("#"):
                tag, host = line.split()
                tags[host] = tag
    return tags


class TagDispatchOutBoundConfig(OutBoundConfig):
    type = "tag_dispatch"

    @classmethod
    def from_data(cls, data: dict) -> TagDispatchOutBound:
        return TagDispatchOutBound(
            tags=load_tags(data["tags_path"]),
            default_tag=data["default_tag"],
            outbounds={
                tag: OutBoundConfig.from_data_by_type(outbound)
                for tag, outbound in data["outbounds"].items()
            },
        )
