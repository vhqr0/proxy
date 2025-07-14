from typing import Optional
from collections.abc import Callable, Awaitable
from abc import abstractmethod
import json
from proxy import AsyncReader, AsyncWriter, OutBound, RegistrableConfig, OutBoundConfig


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


class TagsProviderConfig(RegistrableConfig):
    registry = dict()

    @classmethod
    @abstractmethod
    def from_data(cls, data: dict) -> dict[str, str]:
        pass


class MultiTagsProviderConfig(TagsProviderConfig):
    type = "multi"

    @classmethod
    def from_data(cls, data: dict) -> dict[str, str]:
        tags: dict[str, str] = dict()
        for provider in data["providers"]:
            for host, tag in TagsProviderConfig.from_data_by_type(provider):
                tags[host] = tag
        return tags


class DataTagsProviderConfig(TagsProviderConfig):
    type = "data"

    @classmethod
    def from_data(cls, data: dict) -> dict[str, str]:
        return data["tags"]


class JsonTagsProviderConfig(TagsProviderConfig):
    type = "json"

    @classmethod
    def from_data(cls, data: dict) -> dict[str, str]:
        with open(data["path"], "r") as f:
            return json.load(f)


class TextTagsProviderConfig(TagsProviderConfig):
    type = "text"

    @classmethod
    def from_data(cls, data: dict) -> dict[str, str]:
        tags: dict[str, str] = dict()
        with open(data["path"], "r") as f:
            for line in f:
                # remove comment regions that start with "#", then strip
                line = line.split("#", 1)[0].strip()
                if len(line) > 0:
                    tag, host = line.split()
                    tags[host] = tag
        return tags


class TagDispatchOutBoundConfig(OutBoundConfig):
    type = "tag_dispatch"

    @classmethod
    def from_data(cls, data: dict) -> TagDispatchOutBound:
        return TagDispatchOutBound(
            tags=TagsProviderConfig.from_data_by_type(data["tags"]),
            default_tag=data["default_tag"],
            outbounds={
                tag: OutBoundConfig.from_data_by_type(outbound)
                for tag, outbound in data["outbounds"].items()
            },
        )
