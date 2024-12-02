from io import StringIO
from typing import Any, Union

from ruamel.yaml import CommentedMap, CommentedSeq, YAML

CommentedData = Union[CommentedMap, CommentedSeq, Any]
PlainData = Union[dict, list, Any]


def yaml_load(text: str) -> CommentedData:
    return make_yaml_parser().load(text)


def yaml_dump(data: PlainData) -> str:
    stream = StringIO()
    make_yaml_parser().dump(data, stream)
    return stream.getvalue()


def make_yaml_parser() -> YAML:
    yaml_parser = YAML()
    yaml_parser.indent(mapping=2, sequence=4, offset=2)
    yaml_parser.preserve_quotes = True
    return yaml_parser


def merge_data(base: CommentedData, data: PlainData) -> CommentedData:
    if isinstance(base, CommentedSeq) and isinstance(data, list):
        merge_seq(base, data)
        return base
    elif isinstance(base, CommentedMap) and isinstance(data, dict):
        merge_map(base, data)
        return base
    else:
        return data


def merge_map(base: CommentedMap, data: dict) -> None:
    to_delete = [k for k in base if k not in data]
    for k in to_delete:
        del base[k]
    for i, (k, v) in enumerate(data.items()):
        if k in base:
            base[k] = merge_data(base[k], v)
        else:
            base.insert(i, k, v)


def merge_seq(base: CommentedSeq, data: list) -> None:
    to_delete = [v for v in base if v not in data]
    for v in to_delete:
        base.remove(v)
    idx = 0
    for v in data:
        if v in base:
            idx = base.index(v)
            base[idx] = merge_data(base[idx], v)
        else:
            base.insert(idx+1, v)
