"""YAML 1.2 load and dump for frontmatter, with ruamel.yaml.

Loading uses the core schema: values are ``str``, ``int``, ``float``, ``bool``,
``None``, ``list``, and ``dict``. Timestamps stay strings, because the YAML 1.2 core
schema has no timestamp type; the models parse the timestamp keys that OKF defines.

Frontmatter is untrusted input, so loading also rejects anchors and aliases (which
OKF does not need, and which let a few hundred bytes expand to gigabytes) and limits
how deep collections nest.

Dumping is canonical, so the same mapping always gives the same text. Strings that a
YAML 1.1 or YAML 1.2 reader could take as another type (``"0.3"``, ``"yes"``, a date)
are double-quoted, as OKF §4 recommends.
"""

from __future__ import annotations

import io
from collections.abc import Mapping
from datetime import date, datetime
from pathlib import PurePath
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.composer import Composer, MaxDepthExceededError
from ruamel.yaml.constructor import SafeConstructor
from ruamel.yaml.emitter import Emitter
from ruamel.yaml.error import MarkedYAMLError, YAMLError
from ruamel.yaml.events import AliasEvent
from ruamel.yaml.nodes import ScalarNode
from ruamel.yaml.representer import RoundTripRepresenter
from ruamel.yaml.resolver import VersionedResolver
from ruamel.yaml.scalarstring import DoubleQuotedScalarString

from second_brain_builder.okf.models import OKFError

_STR_TAG = "tag:yaml.org,2002:str"
_RESOLVERS = (VersionedResolver(version=(1, 1)), VersionedResolver(version=(1, 2)))

MAX_DEPTH = 32
"""How deep nodes can nest in frontmatter. The keys that OKF defines need four levels."""


class _AnchorError(MarkedYAMLError):
    """Frontmatter uses an anchor (``&name``) or an alias (``*name``)."""


class _Composer(Composer):
    """The composer, which rejects anchors and aliases.

    An alias makes a shared reference, so a few hundred bytes of nested aliases can
    stand for billions of nodes, which every later walk (type checks, rendering)
    visits one by one. OKF frontmatter does not need anchors.
    """

    def compose_node(self, parent: Any, index: Any) -> Any:
        event = self.parser.peek_event()
        if isinstance(event, AliasEvent) or event.anchor is not None:
            raise _AnchorError(None, None, "anchors and aliases are not allowed", event.start_mark)
        return super().compose_node(parent, index)


class _CoreConstructor(SafeConstructor):
    """The safe constructor, with timestamps kept as the strings that they are in YAML 1.2."""


_CoreConstructor.add_constructor("tag:yaml.org,2002:timestamp", _CoreConstructor.construct_yaml_str)


class _Representer(RoundTripRepresenter):
    """Writes ``null`` for ``None``, not an empty value."""

    def represent_none(self, data: Any) -> ScalarNode:
        return self.represent_scalar("tag:yaml.org,2002:null", "null")


_Representer.add_representer(type(None), _Representer.represent_none)


class _Emitter(Emitter):
    """Uses double quotes where the default emitter would use single quotes."""

    def choose_scalar_style(self) -> Any:
        style = super().choose_scalar_style()
        return '"' if style == "'" else style


def load_mapping(text: str, *, first_line: int = 1) -> dict[str, Any]:
    """Parse a frontmatter block as a YAML 1.2 mapping. An empty block gives ``{}``.

    ``first_line`` is the file line of the first YAML line, for error messages.

    Raises:
        OKFError: The text is not YAML, is not a mapping, has duplicate keys, has
            non-string keys at the top level, has keys or values outside the core
            schema, uses anchors or aliases, or nests deeper than ``MAX_DEPTH``.
    """
    yaml = YAML(typ="safe", pure=True)
    yaml.Composer = _Composer
    yaml.Constructor = _CoreConstructor
    yaml.max_depth = MAX_DEPTH
    try:
        data = yaml.load(text)
    except _AnchorError as exc:
        raise OKFError(
            f"frontmatter uses YAML anchors or aliases{_where(exc, first_line)}"
        ) from exc
    except MaxDepthExceededError as exc:
        raise OKFError(
            f"frontmatter nests deeper than {MAX_DEPTH} levels{_where(exc, first_line)}"
        ) from exc
    except MarkedYAMLError as exc:
        raise OKFError(
            f"frontmatter is not valid YAML: {exc.problem}{_where(exc, first_line)}"
        ) from exc
    except YAMLError as exc:
        raise OKFError(f"frontmatter is not valid YAML: {exc}") from exc
    except (ValueError, LookupError, TypeError, ArithmeticError, RecursionError) as exc:
        # The constructors of ruamel.yaml raise built-in errors for some values that
        # do not fit their tag, for example "!!int x" (ValueError), "!!bool x"
        # (KeyError), "!!int ''" (IndexError), or an int with more digits than Python
        # converts (ValueError). RecursionError is a safety net: MAX_DEPTH stops deep
        # nesting before the recursion limit.
        raise OKFError(f"frontmatter has a value that cannot be read: {exc}") from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise OKFError("frontmatter is not a YAML mapping")
    for key in data:
        if not isinstance(key, str):
            raise OKFError(f"frontmatter key {key!r} is not a string")
    _check_core_types(data, "frontmatter")
    return data


def _where(exc: MarkedYAMLError, first_line: int) -> str:
    mark = exc.problem_mark
    return f" (line {mark.line + first_line})" if mark is not None else ""


def _check_core_types(value: Any, where: str) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key is not None and not isinstance(key, (str, int, float)):
                raise OKFError(f"{where} has a key outside the core schema: {key!r}")
            _check_core_types(item, f"{where}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _check_core_types(item, f"{where}[{index}]")
    elif value is not None and not isinstance(value, (str, int, float)):
        raise OKFError(f"{where} has a YAML type outside the core schema: {type(value).__name__}")


def dump_mapping(mapping: CommentedMap) -> str:
    """Dump a mapping in block style. An empty mapping gives an empty string."""
    if not mapping:
        return ""
    yaml = YAML(typ="rt", pure=True)
    yaml.Representer = _Representer
    yaml.Emitter = _Emitter
    yaml.default_flow_style = False
    yaml.allow_unicode = True
    yaml.width = 1_000_000  # never fold long lines
    yaml.indent(mapping=2, sequence=4, offset=2)
    buffer = io.StringIO()
    yaml.dump(mapping, buffer)
    return buffer.getvalue()


def scalar(text: str, *, flow: bool = False) -> str:
    """A string value or key, double-quoted when a YAML reader could take it as another type.

    In a flow collection, strings with ``:`` are quoted too, because some YAML 1.1
    readers do not accept them plain there, and so are strings that start with ``?``,
    because readers (ruamel.yaml too) take it as the start of a key there:
    ``[?draft]`` reads as ``[{draft: null}]``.
    """
    if (flow and (":" in text or text.startswith("?"))) or any(
        resolver.resolve(ScalarNode, text, (True, False)) != _STR_TAG for resolver in _RESOLVERS
    ):
        return _quoted(text)
    return text


def _quoted(text: str) -> str:
    quoted: str = DoubleQuotedScalarString(text)
    return quoted


def format_timestamp(value: datetime) -> str:
    """RFC 3339 text for a timestamp, double-quoted in the output."""
    return _quoted(value.isoformat())


def flow_map(items: Mapping[str, Any]) -> CommentedMap:
    """A mapping that dumps on one line: ``{by: ..., at: ...}``."""
    mapping = CommentedMap()
    for key, value in items.items():
        mapping[scalar(key, flow=True)] = to_node(value, flow=True)
    mapping.fa.set_flow_style()
    return mapping


def flow_seq(items: list[str]) -> CommentedSeq:
    """A list of strings that dumps on one line: ``[a, b]``."""
    seq = CommentedSeq(scalar(item, flow=True) for item in items)
    seq.fa.set_flow_style()
    return seq


def key_node(key: Any, *, flow: bool = False) -> Any:
    """A mapping key for ``dump_mapping``: a string key is quoted as ``scalar`` quotes it."""
    return scalar(key, flow=flow) if type(key) is str else key


def to_node(value: Any, *, flow: bool = False) -> Any:
    """Convert a Python value to a node for ``dump_mapping``.

    Accepts the core types, ``datetime`` and ``date`` (written as quoted RFC 3339
    strings), ``str`` subclasses such as string enums (written as their value), and
    ``PurePath`` (written as a POSIX path).

    Raises:
        TypeError: The value has another type.
    """
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return scalar(value if type(value) is str else str.__str__(value), flow=flow)
    if isinstance(value, (datetime, date)):
        return _quoted(value.isoformat())
    if isinstance(value, PurePath):
        return scalar(value.as_posix(), flow=flow)
    if isinstance(value, Mapping):
        mapping = CommentedMap()
        for key, item in value.items():
            mapping[key_node(key, flow=flow)] = to_node(item, flow=flow)
        if flow:
            mapping.fa.set_flow_style()
        return mapping
    if isinstance(value, (list, tuple)):
        seq = CommentedSeq(to_node(item, flow=flow) for item in value)
        if flow:
            seq.fa.set_flow_style()
        return seq
    raise TypeError(f"cannot write a {type(value).__name__} value in frontmatter")
