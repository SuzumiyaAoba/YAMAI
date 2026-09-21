#!/usr/bin/env python3
"""Dependency-free smoke/conformance checker for the YAMAI draft artifacts.

The protocol deliberately has requirements that JSON Schema cannot express
(duplicate object names, raw UTF-8/framing, seq/state transitions and scoring
conservation).  This file therefore contains a small strict JSON reader, a
small Draft-2020-12 subset validator, and the minimum semantic checks needed
by the official vectors.  It is intentionally self-contained so release CI
does not depend on an installed package.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import sys
from collections import Counter
from copy import deepcopy
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

from request_contract import evaluate as evaluate_request_contract
from scoring_reference import ScoringError, basic_points, normal_payments, validate_score_bounds, MAX_GAME_EVENTS, MAX_HAND_POINTS, calculate_fixture as calculate_scoring_fixture, tile_index
from session_contract import SessionError, Receiver, negotiate, check_token_trace, replay_plan, resource_trace, classify_player_input, check_clock
from game_contract import GameError, EventState, next_kyoku, legal_actions, canonical_action, furiten, furiten_step, abortive_reason, kan_sequence, public_pao, round_coordinates_reachable, check_snapshot_rinshan, check_snapshot_public_history, known_round_tiles


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_ROOT = ROOT / "schemas"
PROTOCOL = "1.0-draft.9"
PROFILE = "riichi-4p"
PROFILE_REVISION = "1.0-draft.7"
YRC0003_SCHEMA_DIR = SCHEMA_ROOT / "yrc-0003" / PROTOCOL
RELEASE_MANIFEST_PATH = ROOT / "release-manifest.json"
MAX_INT = 9007199254740991
ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")
EXTENSION_FIELD_RE = re.compile(r"^x_(?=[A-Za-z0-9_]{3,62}$)[A-Za-z0-9]+_[A-Za-z0-9][A-Za-z0-9_]*$")
CAPABILITY_RE = re.compile(r"^(?:[a-z][a-z0-9_]{0,63}|x-(?=[A-Za-z0-9_.-]{3,62}$)[A-Za-z0-9]+-[A-Za-z0-9][A-Za-z0-9_.-]*)$")
ERROR_CODE_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
PROFILE_HASH_INPUTS = [
    "schemas/yrc-0003/1.0-draft.9/profile/riichi-4p.schema.json",
    "schemas/yrc-0005/1.0-draft.7/riichi-4p-rules.schema.json",
    "schemas/yrc-0005/1.0-draft.7/scoring-vectors.schema.json",
    "registry/yrc-0003/1.0-draft.9/registry.json",
    "registry/yrc-0005/1.0-draft.7/registry.json",
    "test-vectors/yrc-0003/1.0-draft.9/vectors.json",
    "test-vectors/yrc-0005/1.0-draft.7/scoring.json",
]

# The release checker intentionally implements the assertion keywords used by
# the YAMAI artifact set, rather than claiming to be a general-purpose
# Draft-2020-12 implementation.  Keep this list explicit: if a future schema
# introduces one of these unsupported keywords, CI must fail instead of
# silently accepting an instance that was never checked.
UNSUPPORTED_DRAFT202012_KEYWORDS = {
    "$anchor",
    "$dynamicAnchor",
    "$dynamicRef",
    "dependentRequired",
    "dependentSchemas",
    "exclusiveMaximum",
    "exclusiveMinimum",
    "prefixItems",
    "propertyNames",
    "unevaluatedItems",
    "unevaluatedProperties",
}


class ArtifactError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _pairs(pairs: List[Tuple[str, Any]]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ArtifactError("invalid_json", "duplicate object member: " + key)
        result[key] = value
    return result


def _reject_constant(value: str) -> Any:
    raise ArtifactError("invalid_json", "non-JSON number: " + value)


def _parse_real(raw: str) -> Any:
    value = Decimal(raw)
    if value == value.to_integral_value():
        if not -MAX_INT <= value <= MAX_INT:
            raise ArtifactError("invalid_json", "integer outside IEEE-754 safe range")
        return int(value)
    return value


def _walk_json(value: Any, depth: int = 0, path: str = "$") -> None:
    if isinstance(value, (dict, list)):
        depth += 1
        if depth > 64:
            raise ArtifactError("resource_limit", "JSON depth exceeds 64 at " + path)
    if isinstance(value, bool) or value is None:
        return
    if isinstance(value, int):
        if value < -MAX_INT or value > MAX_INT:
            raise ArtifactError("invalid_json", "integer outside IEEE-754 safe range at " + path)
        return
    if isinstance(value, (float, Decimal)):
        if not math.isfinite(value):
            raise ArtifactError("invalid_json", "non-finite number at " + path)
        return
    if isinstance(value, str):
        if any(0xD800 <= ord(ch) <= 0xDFFF for ch in value):
            raise ArtifactError("invalid_json", "lone surrogate at " + path)
        return
    if isinstance(value, list):
        for i, item in enumerate(value):
            _walk_json(item, depth, f"{path}[{i}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            _walk_json(key, depth, f"{path}.{key}")
            _walk_json(item, depth, f"{path}.{key}")
        return
    raise ArtifactError("invalid_json", "unsupported JSON value at " + path)


def strict_load_bytes(data: bytes, *, source: str = "<bytes>", max_bytes: int | None = None) -> Any:
    if max_bytes is not None and len(data) > max_bytes:
        raise ArtifactError("resource_limit", f"{source} exceeds {max_bytes} bytes")
    if data.startswith(b"\xef\xbb\xbf"):
        raise ArtifactError("invalid_json", f"BOM is forbidden in {source}")
    try:
        text = data.decode("utf-8", "strict")
    except UnicodeDecodeError as exc:
        raise ArtifactError("invalid_json", f"invalid UTF-8 in {source}: {exc}") from exc
    depth, quoted, escaped = 0, False, False
    for char in text:
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in "[{":
            depth += 1
            if depth > 64:
                raise ArtifactError("resource_limit", f"JSON depth exceeds 64 in {source}")
        elif char in "]}":
            depth -= 1
    try:
        value = json.loads(
            text,
            object_pairs_hook=_pairs,
            parse_constant=_reject_constant,
            parse_float=_parse_real,
        )
    except ArtifactError:
        raise
    except (json.JSONDecodeError, RecursionError, ValueError, InvalidOperation, OverflowError) as exc:
        raise ArtifactError("invalid_json", f"invalid JSON in {source}: {exc}") from exc
    _walk_json(value, path=source)
    return value


def strict_load(path: Path) -> Any:
    return strict_load_bytes(path.read_bytes(), source=str(path.relative_to(ROOT)))


def _json_type(value: Any, name: str) -> bool:
    if name == "object":
        return isinstance(value, dict)
    if name == "array":
        return isinstance(value, list)
    if name == "string":
        return isinstance(value, str)
    if name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if name == "number":
        return isinstance(value, (int, float, Decimal)) and not isinstance(value, bool)
    if name == "boolean":
        return isinstance(value, bool)
    if name == "null":
        return value is None
    raise ArtifactError("schema_error", "unknown JSON Schema type: " + str(name))


def _json_equal(left: Any, right: Any) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if isinstance(left, (int, float, Decimal)) and isinstance(right, (int, float, Decimal)):
        return left == right
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(_json_equal(left[k], right[k]) for k in left)
    if isinstance(left, list):
        return len(left) == len(right) and all(_json_equal(a, b) for a, b in zip(left, right))
    return left == right


class SchemaSet:
    def __init__(self) -> None:
        self.schemas: Dict[str, Any] = {}
        self.paths: Dict[str, Path] = {}
        for path in sorted(SCHEMA_ROOT.rglob("*.json")):
            schema = strict_load(path)
            sid = schema.get("$id") if isinstance(schema, dict) else None
            if not isinstance(sid, str):
                raise ArtifactError("schema_error", f"missing $id in {path}")
            if sid in self.schemas:
                raise ArtifactError("schema_error", f"duplicate schema $id: {sid}")
            self.schemas[sid] = schema
            self.paths[sid] = path

    def resolve(self, ref: str, current: Mapping[str, Any]) -> Tuple[Any, Mapping[str, Any]]:
        base, sep, fragment = ref.partition("#")
        if not base:
            root: Mapping[str, Any] = current
            target: Any = current
        else:
            if base not in self.schemas:
                raise ArtifactError("schema_error", f"unresolved schema ref: {base}")
            root = self.schemas[base]
            target = root
        if sep and fragment:
            if not fragment.startswith("/"):
                raise ArtifactError("schema_error", f"unsupported schema fragment: {ref}")
            for part in fragment[1:].split("/"):
                part = part.replace("~1", "/").replace("~0", "~")
                if not isinstance(target, dict) or part not in target:
                    raise ArtifactError("schema_error", f"missing schema fragment: {ref}")
                target = target[part]
        if not isinstance(target, dict):
            raise ArtifactError("schema_error", f"schema ref is not an object: {ref}")
        return target, root

    def check_refs(self) -> None:
        for sid, schema in self.schemas.items():
            self._check_refs(schema, sid)

    def check_keyword_support(self) -> None:
        """Reject core keywords that this dependency-free checker cannot enforce.

        Unknown annotation keywords are permitted by JSON Schema, but known
        Draft 2020-12 applicators/assertions must not be silently ignored.
        This guard keeps the declared ``full_conformance: false`` boundary
        explicit when the artifact schemas evolve.
        """
        for sid, schema in self.schemas.items():
            self._check_keyword_support(schema, sid, "$")

    def _check_keyword_support(self, value: Any, owner: str, path: str) -> None:
        if type(value) is bool:
            return
        if not isinstance(value, dict):
            raise ArtifactError("schema_error", f"schema is not an object or boolean in {owner}{path}")
        if path != "$" and "$id" in value:
            raise ArtifactError("schema_error", f"nested schema resources require a full Draft 2020-12 validator in {owner}{path}")
        for key in value:
            if key in UNSUPPORTED_DRAFT202012_KEYWORDS:
                raise ArtifactError("schema_error", f"unsupported Draft 2020-12 keyword {key} in {owner}{path}")
        if "type" in value:
            types = value["type"] if isinstance(value["type"], list) else [value["type"]]
            if not types or any(not isinstance(t, str) or t not in {"null", "boolean", "object", "array", "number", "integer", "string"} for t in types) or len(types) != len(set(types)):
                raise ArtifactError("schema_error", f"invalid type keyword in {owner}{path}")
        for key in ("minLength", "maxLength", "minItems", "maxItems", "minProperties", "maxProperties", "minContains", "maxContains"):
            if key in value and (type(value[key]) is not int or value[key] < 0):
                raise ArtifactError("schema_error", f"invalid {key} in {owner}{path}")
        for suffix, child in self._schema_children(value):
            self._check_keyword_support(child, owner, path + suffix)

    @staticmethod
    def _schema_children(value: Mapping[str, Any]) -> Iterable[Tuple[str, Any]]:
        for key in ("$defs", "definitions", "properties", "patternProperties"):
            if key in value:
                if not isinstance(value[key], dict):
                    raise ArtifactError("schema_error", f"{key} must be a schema map")
                for name, child in value[key].items():
                    yield f".{key}.{name}", child
        for key in ("allOf", "anyOf", "oneOf"):
            if key in value:
                if not isinstance(value[key], list) or not value[key]:
                    raise ArtifactError("schema_error", f"{key} must be a nonempty schema array")
                for index, child in enumerate(value[key]):
                    yield f".{key}[{index}]", child
        for key in ("items", "contains", "not", "if", "then", "else", "additionalProperties"):
            if key in value:
                yield "." + key, value[key]

    def _check_refs(self, value: Any, owner: str) -> None:
        if isinstance(value, dict):
            if "$ref" in value:
                ref = value["$ref"]
                if not isinstance(ref, str):
                    raise ArtifactError("schema_error", f"non-string $ref in {owner}")
                self.resolve(ref, self.schemas[owner])
            for _, item in self._schema_children(value):
                self._check_refs(item, owner)

    def validate(self, value: Any, schema: Any, path: str = "$") -> None:
        self._validate(value, schema, path, schema)

    def _validate(self, value: Any, schema: Any, path: str, base: Mapping[str, Any]) -> None:
        if schema is True:
            return
        if schema is False:
            raise ArtifactError("invalid_message", "false schema at " + path)
        if not isinstance(schema, dict):
            raise ArtifactError("schema_error", "schema is not an object")
        if "$ref" in schema:
            target, target_base = self.resolve(schema["$ref"], base)
            self._validate(value, target, path, target_base)
        if "allOf" in schema:
            for sub in schema["allOf"]:
                self._validate(value, sub, path, base)
        if "anyOf" in schema:
            matched = False
            for sub in schema["anyOf"]:
                try:
                    self._validate(value, sub, path, base)
                    matched = True
                    break
                except ArtifactError as exc:
                    if exc.code != "invalid_message":
                        raise
            if not matched:
                raise ArtifactError("invalid_message", f"no anyOf branch matched at {path}")
        if "oneOf" in schema:
            matches = 0
            for sub in schema["oneOf"]:
                try:
                    self._validate(value, sub, path, base)
                    matches += 1
                except ArtifactError:
                    pass
            if matches != 1:
                raise ArtifactError("invalid_message", f"oneOf matched {matches} branches at {path}")
        if "not" in schema:
            try:
                self._validate(value, schema["not"], path, base)
            except ArtifactError:
                pass
            else:
                raise ArtifactError("invalid_message", f"not constraint matched at {path}")
        if "if" in schema:
            condition = True
            try:
                self._validate(value, schema["if"], path, base)
            except ArtifactError:
                condition = False
            branch = schema.get("then") if condition else schema.get("else")
            if branch is not None:
                self._validate(value, branch, path, base)
        if "const" in schema and not _json_equal(value, schema["const"]):
            raise ArtifactError("invalid_message", f"const mismatch at {path}")
        if "enum" in schema and not any(_json_equal(value, item) for item in schema["enum"]):
            raise ArtifactError("invalid_message", f"enum mismatch at {path}")
        typ = schema.get("type")
        if typ is not None:
            types = typ if isinstance(typ, list) else [typ]
            if not any(_json_type(value, item) for item in types):
                raise ArtifactError("invalid_message", f"type mismatch at {path}")
        if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
            if "minimum" in schema and value < schema["minimum"]:
                raise ArtifactError("invalid_message", f"minimum mismatch at {path}")
            if "maximum" in schema and value > schema["maximum"]:
                raise ArtifactError("invalid_message", f"maximum mismatch at {path}")
            if "multipleOf" in schema and value % schema["multipleOf"] != 0:
                raise ArtifactError("invalid_message", f"multipleOf mismatch at {path}")
        if isinstance(value, str):
            if "minLength" in schema and len(value) < schema["minLength"]:
                raise ArtifactError("invalid_message", f"minLength mismatch at {path}")
            if "maxLength" in schema and len(value) > schema["maxLength"]:
                raise ArtifactError("invalid_message", f"maxLength mismatch at {path}")
            if "pattern" in schema and re.search(schema["pattern"], value) is None:
                raise ArtifactError("invalid_message", f"pattern mismatch at {path}")
        if isinstance(value, list):
            if "minItems" in schema and len(value) < schema["minItems"]:
                raise ArtifactError("invalid_message", f"minItems mismatch at {path}")
            if "maxItems" in schema and len(value) > schema["maxItems"]:
                raise ArtifactError("invalid_message", f"maxItems mismatch at {path}")
            if schema.get("uniqueItems"):
                if any(_json_equal(item, earlier) for i, item in enumerate(value) for earlier in value[:i]):
                    raise ArtifactError("invalid_message", f"uniqueItems mismatch at {path}")
            if "contains" in schema:
                matches = 0
                for index, item in enumerate(value):
                    try:
                        self._validate(item, schema["contains"], f"{path}[{index}]", base)
                    except ArtifactError:
                        continue
                    matches += 1
                minimum = schema.get("minContains", 1)
                maximum = schema.get("maxContains", len(value))
                if matches < minimum or matches > maximum:
                    raise ArtifactError("invalid_message", f"contains mismatch at {path}")
            if "items" in schema:
                for i, item in enumerate(value):
                    self._validate(item, schema["items"], f"{path}[{i}]", base)
        if isinstance(value, dict):
            if "minProperties" in schema and len(value) < schema["minProperties"]:
                raise ArtifactError("invalid_message", f"minProperties mismatch at {path}")
            if "maxProperties" in schema and len(value) > schema["maxProperties"]:
                raise ArtifactError("invalid_message", f"maxProperties mismatch at {path}")
            for req in schema.get("required", []):
                if req not in value:
                    raise ArtifactError("invalid_message", f"missing {req} at {path}")
            props = schema.get("properties", {})
            patterns = schema.get("patternProperties", {})
            for key, item in value.items():
                matched = key in props
                if key in props:
                    self._validate(item, props[key], f"{path}.{key}", base)
                for pattern, subschema in patterns.items():
                    if re.search(pattern, key):
                        self._validate(item, subschema, f"{path}.{key}", base)
                        matched = True
                if not matched and schema.get("additionalProperties") is False:
                    raise ArtifactError("invalid_message", f"unknown member {key} at {path}")
                if not matched and isinstance(schema.get("additionalProperties"), dict):
                    self._validate(item, schema["additionalProperties"], f"{path}.{key}", base)


def schema_by_id(schemas: SchemaSet, sid: str) -> Any:
    if sid not in schemas.schemas:
        raise ArtifactError("schema_error", f"missing schema: {sid}")
    return schemas.schemas[sid]


def _schema_property_values(schema: Mapping[str, Any], property_name: str) -> List[str]:
    properties = schema.get("properties", {})
    if not isinstance(properties, Mapping):
        return []
    definition = properties.get(property_name)
    if not isinstance(definition, Mapping):
        return []
    values: List[str] = []
    if isinstance(definition.get("const"), str):
        values.append(definition["const"])
    if isinstance(definition.get("enum"), list):
        values.extend(value for value in definition["enum"] if isinstance(value, str))
    return values


def _collect_union_property_values(
    schemas: SchemaSet,
    node: Any,
    root: Mapping[str, Any],
    property_name: str,
    seen: set[int] | None = None,
) -> List[str]:
    """Collect discriminator values from a schema union, following local refs."""
    if seen is None:
        seen = set()
    if isinstance(node, Mapping):
        identity = id(node)
        if identity in seen:
            return []
        seen.add(identity)
        values = _schema_property_values(node, property_name)
        if "$ref" in node and isinstance(node["$ref"], str):
            target, target_root = schemas.resolve(node["$ref"], root)
            values.extend(_collect_union_property_values(schemas, target, target_root, property_name, seen))
        for key in ("oneOf", "anyOf", "allOf"):
            branches = node.get(key, [])
            if isinstance(branches, list):
                for branch in branches:
                    values.extend(_collect_union_property_values(schemas, branch, root, property_name, seen))
        return values
    if isinstance(node, list):
        values: List[str] = []
        for branch in node:
            values.extend(_collect_union_property_values(schemas, branch, root, property_name, seen))
        return values
    return []


def load_all_json() -> None:
    for base in (ROOT / "registry", ROOT / "test-vectors"):
        for path in sorted(base.rglob("*.json")):
            strict_load(path)
    strict_load(RELEASE_MANIFEST_PATH)


def check_registry(schemas: SchemaSet) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    p = strict_load(ROOT / "registry/yrc-0003/1.0-draft.9/registry.json")
    r = strict_load(ROOT / "registry/yrc-0005/1.0-draft.7/registry.json")
    if p["protocol_version"] != PROTOCOL or r["protocol_version"] != PROTOCOL:
        raise ArtifactError("registry_error", "registry protocol version mismatch")
    bounds = r["arithmetic"]["score_range"]
    _require(p["limits"]["max_event_count"] == bounds["max_game_events"] == MAX_GAME_EVENTS
             and bounds["max_hand_points"] == MAX_HAND_POINTS and bounds["max_integer"] == MAX_INT,
             "registry_error", "score range constants differ from the protocol event/integer limits")
    if not isinstance(p.get("profiles"), list) or len(p["profiles"]) != 1:
        raise ArtifactError("registry_error", "protocol registry must contain exactly one profile")
    if p["profiles"][0]["id"] != PROFILE or p["profiles"][0]["revision"] != PROFILE_REVISION:
        raise ArtifactError("registry_error", "profile registry mismatch")
    for field in ("message_kinds", "event_types", "action_types", "ack_statuses", "error_codes", "rule_keys", "result_types", "result_reasons"):
        values = p[field]
        ids = [x if isinstance(x, str) else x["id"] for x in values]
        if len(ids) != len(set(ids)):
            raise ArtifactError("registry_error", f"duplicate registry id in {field}")
        if any(not isinstance(item, str) or ERROR_CODE_RE.fullmatch(item) is None for item in ids):
            raise ArtifactError("registry_error", f"invalid registry id in {field}")

    expected_message_kinds = {"hello", "join", "welcome", "event", "request", "action", "ack", "error", "snapshot"}
    message_kinds = {entry.get("id") for entry in p.get("message_kinds", []) if isinstance(entry, Mapping)}
    if message_kinds != expected_message_kinds:
        raise ArtifactError("registry_error", "message kind registry does not match the protocol union")
    for entry in p["message_kinds"]:
        sid = entry.get("schema")
        if not isinstance(sid, str):
            raise ArtifactError("registry_error", "message kind schema reference is missing")
        schema = schema_by_id(schemas, sid)
        if set(_collect_union_property_values(schemas, schema, schema, "kind")) != {entry["id"]}:
            raise ArtifactError("registry_error", f"message kind/schema discriminator mismatch: {entry['id']}")

    event_schema = schema_by_id(schemas, "urn:yamai:schema:yrc-0003:1.0-draft.9:event")
    event_union = event_schema.get("properties", {}).get("event", {}).get("oneOf", [])
    event_values = set()
    for branch in event_union:
        event_values.update(_schema_property_values(branch, "type"))
    if set(p["event_types"]) != event_values:
        raise ArtifactError("registry_error", "event type registry does not match event schema")

    action_schema = schema_by_id(schemas, "urn:yamai:schema:yrc-0003:1.0-draft.9:action")
    action_root = action_schema
    action_values = set(
        _collect_union_property_values(
            schemas,
            action_schema.get("$defs", {}).get("actionObject", {}),
            action_root,
            "type",
        )
    )
    if set(p["action_types"]) != action_values:
        raise ArtifactError("registry_error", "action type registry does not match action schema")

    ack_schema = schema_by_id(schemas, "urn:yamai:schema:yrc-0003:1.0-draft.9:ack")
    ack_statuses = set(_schema_property_values(ack_schema, "status"))
    if set(p["ack_statuses"]) != ack_statuses:
        raise ArtifactError("registry_error", "ack status registry does not match ack schema")
    error_id = f"urn:yamai:schema:yrc-0003:{PROTOCOL}:error"
    error_schema = schema_by_id(schemas, error_id)
    _require(set(error_schema["$defs"]["base"]["properties"]["code"]["enum"]) == {item["id"] for item in p["error_codes"]}, "registry_error", "error code schema and registry differ")
    for entry in p["error_codes"]:
        contexts = entry.get("contexts")
        _require(entry["severity"] in {"fatal", "recoverable", "contextual"}, "registry_error", "unknown error severity policy")
        _require(isinstance(contexts, list) and bool(contexts) and all(isinstance(c, str) for c in contexts) and len(contexts) == len(set(contexts)) and set(contexts) <= {"host-negotiation", "player-negotiation", "host-application", "player-application"}, "registry_error", "invalid error context registry")
        for context in ("host-negotiation", "player-negotiation", "host-application", "player-application"):
            for severity in ("fatal", "recoverable"):
                message = {"kind":"error","code":entry["id"],"severity":severity,"message":"diagnostic"}
                if context.endswith("application"):
                    message.update(yamai=PROTOCOL, session_id="s1", game_id="g1")
                    if context.startswith("host"):
                        message["seq"] = 1
                if entry["id"] == "sequence_gap":
                    message.update(expected_seq=1, received_seq=2)
                if entry["id"] == "request_conflict":
                    message.update(request_id="r1", action_id="a1")
                allowed_severity = entry["severity"] == severity or (entry["severity"] == "contextual" and (severity == "fatal" or context == "host-application"))
                expected = context in entry.get("contexts", []) and allowed_severity
                actual = True
                try:
                    side = context.split("-", 1)[0]
                    schemas.validate(message, {"$ref": error_id + "#/$defs/" + side + "Message"})
                except ArtifactError as error:
                    if error.code != "invalid_message":
                        raise
                    actual = False
                _require(actual == expected, "registry_error", f"{entry['id']}: error context/severity differs for {context}/{severity}")

    expected_schema_files = {
        str(path.relative_to(ROOT))
        for path in YRC0003_SCHEMA_DIR.rglob("*.json")
        if path.name != "vector-manifest.schema.json"
    }
    schema_files = p.get("schema_files")
    if not isinstance(schema_files, list) or len(schema_files) != len(set(schema_files)) or set(schema_files) != expected_schema_files:
        raise ArtifactError("registry_error", "protocol registry schema file set mismatch")
    for relative in schema_files:
        path = ROOT / relative
        if not path.is_file():
            raise ArtifactError("registry_error", f"registry schema file is missing: {relative}")
        schema_by_id(schemas, strict_load(path)["$id"])

    expected_scoring_schema_files = {
        str(path.relative_to(ROOT))
        for path in (SCHEMA_ROOT / "yrc-0005" / "1.0-draft.7").glob("*.json")
    }
    if set(r.get("schema_files", [])) != expected_scoring_schema_files:
        raise ArtifactError("registry_error", "scoring registry schema file set mismatch")
    yaku_ids = [x["id"] for x in r["yaku_ids"]]
    if len(yaku_ids) != len(set(yaku_ids)):
        raise ArtifactError("registry_error", "duplicate yaku id")
    if set(r["bonus_ids"]) != {"dora", "uradora", "akadora"}:
        raise ArtifactError("registry_error", "bonus registry mismatch")
    if "scoring_vectors_schema" in r:
        schema_by_id(schemas, r["scoring_vectors_schema"])
    for field in ("schema_files",):
        if field not in p:
            raise ArtifactError("registry_error", "missing registry metadata")
    return p, r


def canonical(value: Any) -> bytes:
    """Return the artifact subset's RFC 8785-compatible canonical bytes.

    Artifacts use only safe integers, not floating-point numbers. JCS sorts
    object names by UTF-16 code units, including names outside the BMP.
    Fail if the artifact subset changes instead of silently hashing with
    Python's different float formatting or Unicode code-point sort order.
    """
    def prepare(item: Any) -> Any:
        if item is None or isinstance(item, bool):
            return item
        if isinstance(item, str):
            if any(0xD800 <= ord(ch) <= 0xDFFF for ch in item):
                raise ArtifactError("hash_error", "JCS input contains a lone surrogate")
            return item
        if type(item) is int and -MAX_INT <= item <= MAX_INT:
            return item
        if isinstance(item, list):
            return [prepare(child) for child in item]
        if isinstance(item, dict):
            if any(not isinstance(key, str) for key in item):
                raise ArtifactError("hash_error", "JCS object names must be strings")
            keys = [prepare(key) for key in item]
            return {key: prepare(item[key]) for key in sorted(keys, key=lambda key: key.encode("utf-16-be"))}
        raise ArtifactError("hash_error", "JCS artifact input requires safe integers; floating-point values are unsupported")

    return json.dumps(prepare(value), ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def normalize_wire_profile_hashes(wire: str) -> str:
    """Replace only identity-value tokens; retain every unrelated wire byte."""
    try:
        value = strict_load_bytes(wire.encode("utf-8"))
    except ArtifactError:
        return wire
    if not isinstance(value, dict):
        return wire
    paths = set()
    valid_hash = lambda item: isinstance(item, str) and re.fullmatch(r"sha256:[0-9a-f]{64}", item) is not None
    if value.get("kind") in ("join", "welcome") and valid_hash(value.get("profile_hash")):
        paths.add(("profile_hash",))
    elif value.get("kind") == "hello" and isinstance(value.get("profiles"), list):
        for i, profile in enumerate(value["profiles"]):
            if isinstance(profile, dict) and isinstance(profile.get("hashes"), dict):
                paths.update(("profiles", i, "hashes", revision) for revision, digest in profile["hashes"].items() if valid_hash(digest))
    if not paths:
        return wire
    decoder = json.JSONDecoder()
    replacements = []
    cursor = 0

    def whitespace() -> None:
        nonlocal cursor
        while cursor < len(wire) and wire[cursor] in " \t\r\n":
            cursor += 1

    def visit(path: tuple) -> None:
        nonlocal cursor
        whitespace()
        if wire[cursor] in "{[":
            is_object = wire[cursor] == "{"
            closing = "}" if is_object else "]"
            cursor += 1
            whitespace()
            index = 0
            while wire[cursor] != closing:
                if is_object:
                    key, cursor = decoder.raw_decode(wire, cursor)
                    whitespace()
                    cursor += 1  # colon; the complete JSON was validated above
                else:
                    key = index
                    index += 1
                visit((*path, key))
                whitespace()
                if wire[cursor] == ",":
                    cursor += 1
                    whitespace()
                else:
                    break
            cursor += 1
        else:
            start = cursor
            _, cursor = decoder.raw_decode(wire, cursor)
            if path in paths:
                replacements.append((start, cursor))

    visit(())
    for start, end in reversed(replacements):
        wire = wire[:start] + json.dumps("sha256:" + "0" * 64) + wire[end:]
    return wire


def profile_hash(protocol_registry: Dict[str, Any], rules_registry: Dict[str, Any]) -> str:
    manifest = strict_load(ROOT / "test-vectors/yrc-0003/1.0-draft.9/manifest.json")
    vectors = strict_load(ROOT / manifest["vectors"])
    scoring = strict_load(ROOT / manifest["scoring_vectors"])
    profile_schema = strict_load(ROOT / "schemas/yrc-0003/1.0-draft.9/profile/riichi-4p.schema.json")
    rules_schema = strict_load(ROOT / "schemas/yrc-0005/1.0-draft.7/riichi-4p-rules.schema.json")
    scoring_vectors_schema = strict_load(ROOT / "schemas/yrc-0005/1.0-draft.7/scoring-vectors.schema.json")
    # Normalize mutable hash fields before constructing the canonical artifact
    # projection.  The registry hash itself is omitted from that projection,
    # so writing the resulting digest back cannot create a hash cycle.
    zero_hash = "sha256:" + "0" * 64

    def normalize_profile_hashes(value: Any) -> Any:
        if isinstance(value, dict):
            result = {key: normalize_profile_hashes(item) for key, item in value.items()}
            if isinstance(value.get("wire"), str):
                result["wire"] = normalize_wire_profile_hashes(value["wire"])
            if isinstance(value.get("kind"), str) and value["kind"] in {"join", "welcome"} and isinstance(value.get("profile_hash"), str) and re.fullmatch(r"sha256:[0-9a-f]{64}", value["profile_hash"]):
                result["profile_hash"] = zero_hash
            if value.get("kind") == "hello" and isinstance(result.get("profiles"), list):
                for profile in result.get("profiles", []):
                    if isinstance(profile, dict) and isinstance(profile.get("hashes"), dict):
                        profile["hashes"] = {revision: zero_hash if isinstance(digest, str) and re.fullmatch(r"sha256:[0-9a-f]{64}", digest) else digest for revision, digest in profile["hashes"].items()}
            return result
        if isinstance(value, list):
            return [normalize_profile_hashes(item) for item in value]
        return value

    pcopy = json.loads(json.dumps(protocol_registry))
    for profile in pcopy["profiles"]:
        profile.pop("hash", None)
    payload = {
        "profile_schema": profile_schema,
        "rules_schema": rules_schema,
        "scoring_vectors_schema": scoring_vectors_schema,
        "yrc0003_registry": pcopy,
        "yrc0005_registry": rules_registry,
        "official_vectors": vectors,
        "scoring_vectors": scoring,
    }
    canonical_bytes = canonical(normalize_profile_hashes(payload))
    return "sha256:" + hashlib.sha256(canonical_bytes).hexdigest()


def _repo_file(value: Any, field: str) -> Path:
    if not isinstance(value, str) or not value or Path(value).is_absolute() or ".." in Path(value).parts:
        raise ArtifactError("manifest_error", f"{field} is not a safe repository-relative path")
    path = ROOT / value
    if not path.is_file():
        raise ArtifactError("manifest_error", f"{field} points to a missing file: {value}")
    return path


def _repo_file_list(values: Any, field: str) -> None:
    if not isinstance(values, list):
        raise ArtifactError("manifest_error", f"{field} must be an array")
    for index, value in enumerate(values):
        _repo_file(value, f"{field}[{index}]")


def check_manifest(schemas: SchemaSet, p: Dict[str, Any], r: Dict[str, Any]) -> Dict[str, Any]:
    path = ROOT / "test-vectors/yrc-0003/1.0-draft.9/manifest.json"
    manifest = strict_load(path)
    schema = schema_by_id(schemas, "urn:yamai:schema:yrc-0003:1.0-draft.9:vector-manifest")
    schemas.validate(manifest, schema)
    expected_registries = [
        "registry/yrc-0003/1.0-draft.9/registry.json",
        "registry/yrc-0005/1.0-draft.7/registry.json",
    ]
    if manifest["registry"] != expected_registries:
        raise ArtifactError("manifest_error", "vector manifest registry set mismatch")
    if manifest["schema_root"] != "schemas/yrc-0003/1.0-draft.9/message.schema.json":
        raise ArtifactError("manifest_error", "vector manifest schema root mismatch")
    _repo_file_list(manifest["registry"], "registry")
    _repo_file(manifest["schema_root"], "schema_root")
    _repo_file(manifest["vectors"], "vectors")
    _repo_file(manifest["scoring_vectors"], "scoring_vectors")
    if manifest.get("profile_hash_canonicalization") != "RFC8785-JCS":
        raise ArtifactError("manifest_error", "profile hash canonicalization must be RFC8785-JCS")
    if manifest.get("profile_hash_inputs") != PROFILE_HASH_INPUTS:
        raise ArtifactError("manifest_error", "profile hash input manifest is not the release set")
    _repo_file_list(manifest["profile_hash_inputs"], "profile_hash_inputs")
    actual = profile_hash(p, r)
    if manifest["profile_hash"] != actual:
        raise ArtifactError("manifest_error", f"profile_hash mismatch: expected {manifest['profile_hash']}, actual {actual}")
    vectors = strict_load(ROOT / manifest["vectors"])
    expected = {item["id"] for item in manifest["cases"]}
    case_ids = [item["id"] for item in manifest["cases"]]
    if len(case_ids) != len(set(case_ids)):
        raise ArtifactError("manifest_error", "duplicate vector case id")
    if set(vectors) != expected:
        raise ArtifactError("manifest_error", "manifest/vector case set mismatch")
    return manifest


def check_release_manifest(manifest: Dict[str, Any], p: Dict[str, Any], r: Dict[str, Any]) -> None:
    release = strict_load(RELEASE_MANIFEST_PATH)
    if release.get("release_id") != f"yamai-{PROTOCOL}":
        raise ArtifactError("release_error", "release id does not match protocol version")
    if release.get("required_git_tag") != release.get("release_id"):
        raise ArtifactError("release_error", "required git tag does not match release id")

    protocol = release.get("protocol")
    if not isinstance(protocol, Mapping) or protocol.get("name") != "yamai" or protocol.get("version") != PROTOCOL:
        raise ArtifactError("release_error", "release protocol metadata mismatch")
    if protocol.get("message_schema_root") != manifest["schema_root"]:
        raise ArtifactError("release_error", "release message schema root mismatch")
    _repo_file(protocol.get("document"), "protocol.document")
    _repo_file(protocol.get("message_schema_root"), "protocol.message_schema_root")

    profiles = release.get("profiles")
    if not isinstance(profiles, list) or len(profiles) != 1:
        raise ArtifactError("release_error", "release must contain exactly one profile")
    profile = profiles[0]
    if not isinstance(profile, Mapping) or profile.get("name") != PROFILE:
        raise ArtifactError("release_error", "release profile name mismatch")
    if profile.get("revision") != manifest["profile_revision"] or profile.get("profile_hash") != manifest["profile_hash"]:
        raise ArtifactError("release_error", "release profile revision/hash mismatch")
    _repo_file(profile.get("document"), "profiles[0].document")

    expected_registries = [
        "registry/yrc-0003/1.0-draft.9/registry.json",
        "registry/yrc-0005/1.0-draft.7/registry.json",
    ]
    if release.get("registries") != expected_registries or manifest["registry"] != expected_registries:
        raise ArtifactError("release_error", "release registry set mismatch")
    _repo_file_list(release.get("registries"), "registries")

    expected_vectors = [
        "test-vectors/yrc-0003/1.0-draft.9/manifest.json",
        manifest["vectors"],
        manifest["scoring_vectors"],
    ]
    if release.get("test_vectors") != expected_vectors:
        raise ArtifactError("release_error", "release test vector set mismatch")
    _repo_file_list(release.get("test_vectors"), "test_vectors")

    expected_schemas = {
        *p["schema_files"],
        "schemas/yrc-0003/1.0-draft.9/vector-manifest.schema.json",
        *r["schema_files"],
    }
    release_schemas = release.get("schemas")
    if not isinstance(release_schemas, list) or len(release_schemas) != len(set(release_schemas)) or set(release_schemas) != expected_schemas:
        raise ArtifactError("release_error", "release schema file set mismatch")
    _repo_file_list(release_schemas, "schemas")

    scope = release.get("profile_hash_scope")
    if not isinstance(scope, Mapping) or scope.get("canonicalization") != manifest["profile_hash_canonicalization"] or scope.get("inputs") != manifest["profile_hash_inputs"]:
        raise ArtifactError("release_error", "release profile hash scope mismatch")
    proposal_path = f"schemas/yrc-0003/{PROTOCOL}/negotiation/join-proposal.schema.json"
    if protocol.get("proposal_schema") != proposal_path:
        raise ArtifactError("release_error", "release join proposal schema mismatch")
    if scope.get("protocol_version_pins") != [manifest["schema_root"], proposal_path]:
        raise ArtifactError("release_error", "release protocol version pin mismatch")

    validator = release.get("validator")
    if not isinstance(validator, Mapping) or validator.get("path") != "scripts/validate_artifacts.py" or validator.get("command") != "python3 scripts/validate_artifacts.py":
        raise ArtifactError("release_error", "release validator metadata mismatch")
    _repo_file(validator.get("path"), "validator.path")
    if validator.get("support_files") != ["scripts/request_contract.py", "scripts/scoring_reference.py", "scripts/session_contract.py", "scripts/game_contract.py", "scripts/test_scoring_reference.py", "scripts/test_session_contract.py", "scripts/test_game_contract.py", "scripts/test_validator.py", "scripts/check_jsonschema.py", "scripts/score_oracle.py", "tests/test_regressions.py"]:
        raise ArtifactError("release_error", "release validator support files mismatch")
    _repo_file_list(validator.get("support_files"), "validator.support_files")

    change_control = release.get("change_control")
    if not isinstance(change_control, Mapping):
        raise ArtifactError("release_error", "release change control metadata is missing")
    _repo_file(change_control.get("changelog"), "change_control.changelog")
    _repo_file(change_control.get("process"), "change_control.process")
    _repo_file_list(release.get("normative_documents"), "normative_documents")
    _repo_file_list(release.get("informational_documents"), "informational_documents")


TERMINAL_ACK_STATUSES = {"accepted", "passed", "superseded", "defaulted", "stale"}
GROUP_FIELDS = ("decision_group_members", "decision_group_deadline_ms", "decision_group_close")
TARGET_TYPES = {"game", "recording"}
KNOWN_RULE_KEYS = {
    "game_length", "starting_points", "extension", "ranking_policy", "red_fives", "kuitan", "ron_policy",
    "reaction_priority", "multiple_ron_settlement", "bankruptcy", "bankruptcy_threshold", "dealer_continuation",
    "abortive_draw_continuation", "agariyame", "noten_payment", "riichi_stick_value", "honba_ron_value",
    "honba_tsumo_value_per_payer", "kiriage_mangan", "kazoe_yakuman", "double_yakuman", "pao", "chombo",
    "ankan_chankan", "kan_dora_timing", "invalid_action_policy", "time_control", "abortive_draws", "local_yaku",
}


def _require(condition: bool, code: str, message: str) -> None:
    if not condition:
        raise ArtifactError(code, message)


def _check_mode_view(mode: Any, view: Any, seat: Any, *, context: str) -> None:
    if mode == "play":
        _require(view == "seat", "invalid_message", f"{context}: play view must be seat")
        _require(isinstance(seat, int) and not isinstance(seat, bool) and 0 <= seat <= 3, "invalid_message", f"{context}: play seat is required")
    elif mode == "spectate":
        _require(view == "public" and seat is None, "invalid_message", f"{context}: spectate requires public/null")
    elif mode == "replay":
        valid_view = isinstance(view, str) and view in {"public", "full"}
        if isinstance(view, dict):
            valid_view = (
                "seat" in view
                and all(key == "seat" or EXTENSION_FIELD_RE.fullmatch(key) for key in view)
                and type(view.get("seat")) is int
                and 0 <= view["seat"] <= 3
            )
        _require(valid_view and seat is None, "invalid_message", f"{context}: invalid replay view/seat")


def _check_target(target: Any, *, allowed_types: set[str] = TARGET_TYPES) -> None:
    _require(isinstance(target, dict), "invalid_message", "target must be a tagged object")
    _require(target.get("type") in allowed_types, "invalid_message", "target.type is not allowed for this mode")
    _require(isinstance(target.get("id"), str) and ID_RE.fullmatch(target["id"]), "invalid_message", "target.id is invalid")


def _check_event_visibility(event: Mapping[str, Any], mode: str, view: Any, seat: Any) -> None:
    visible_seat = seat if mode == "play" else view.get("seat") if isinstance(view, dict) else None
    full = view == "full"
    if event.get("type") == "start_kyoku":
        for actor, hand in enumerate(event["hands"]):
            _require(("tiles" in hand) == (full or actor == visible_seat), "invalid_message", "event hand visibility differs")
    if event.get("type") == "tsumo":
        _require((event.get("pai") is not None) == (full or event["actor"] == visible_seat), "invalid_message", "draw visibility differs")


def _check_capabilities(capabilities: Any) -> None:
    _require(isinstance(capabilities, dict), "invalid_message", "capabilities must be an object")
    required, optional = capabilities.get("required"), capabilities.get("optional")
    _require(isinstance(required, list) and isinstance(optional, list), "invalid_message", "capability arrays are missing")
    _require(all(isinstance(value, str) for value in required + optional), "invalid_message", "capability names must be strings")
    _require(len(set(required)) == len(required) and len(set(optional)) == len(optional), "invalid_message", "capabilities contain duplicates")
    _require(not set(required) & set(optional), "invalid_message", "capability appears in both arrays")
    for value in required + optional:
        _require(CAPABILITY_RE.fullmatch(value) is not None, "invalid_message", "capability name is invalid")


def _check_rules(rules: Any) -> None:
    _require(isinstance(rules, dict), "invalid_message", "rules must be an object")
    for key in rules:
        _require(key in KNOWN_RULE_KEYS or EXTENSION_FIELD_RE.fullmatch(key), "invalid_message", f"unknown standard rule key: {key}")
    try:
        validate_score_bounds(rules)
    except ScoringError as exc:
        raise ArtifactError(exc.code, str(exc)) from exc


def _check_players(players: Any) -> None:
    _require(isinstance(players, list) and len(players) == 4, "invalid_message", "players must contain four seats")
    seats = [player.get("seat") for player in players if isinstance(player, dict)]
    _require(all(type(seat) is int for seat in seats) and seats == [0, 1, 2, 3], "invalid_message", "players must be in absolute seat order")


def _check_action_object(action: Any, *, expected_actor: int | None = None, extension_types: Iterable[str] = ()) -> None:
    _require(isinstance(action, dict), "invalid_message", "action candidate must be an object")
    kind = action.get("type")
    if kind == "none":
        actor = action.get("actor")
        _require("actor" not in action or (type(actor) is int and 0 <= actor <= 3), "invalid_message", "none actor is invalid")
        _require(actor is None or expected_actor is None or actor == expected_actor, "invalid_message", "none actor does not match request seat")
        return
    _require(isinstance(action.get("actor"), int) and 0 <= action["actor"] <= 3, "invalid_message", "action actor is invalid")
    if expected_actor is not None:
        _require(action["actor"] == expected_actor, "invalid_message", "action actor does not match request seat")
    if kind in extension_types:
        return
    if kind == "dahai":
        _require(isinstance(action.get("pai"), str) and action["pai"] != "?", "invalid_message", "dahai pai is invalid")
        _require(isinstance(action.get("tsumogiri"), bool), "invalid_message", "dahai tsumogiri is invalid")
        return
    if kind in {"chi", "pon", "daiminkan"}:
        expected = 3 if kind == "daiminkan" else 2
        _require(isinstance(action.get("target"), int) and 0 <= action["target"] <= 3, "invalid_message", "meld target is invalid")
        _require(action["target"] != action["actor"], "invalid_message", "meld target equals actor")
        _require(isinstance(action.get("consumed"), list) and len(action["consumed"]) == expected, "invalid_message", "meld consumed count is invalid")
        if kind in {"chi", "pon"}:
            _require(isinstance(action.get("dahai"), dict), "invalid_message", "chi/pon requires nested dahai")
            _require(action["dahai"].get("type") == "dahai" and action["dahai"].get("actor") == action["actor"], "invalid_message", "nested dahai type/actor mismatch")
            _check_action_object(action["dahai"], expected_actor=action["actor"])
        return
    if kind == "ankan":
        _require(isinstance(action.get("consumed"), list) and len(action["consumed"]) == 4, "invalid_message", "ankan consumed count is invalid")
        return
    if kind == "kakan":
        _require(isinstance(action.get("pai"), str) and isinstance(action.get("consumed"), list) and len(action["consumed"]) == 3, "invalid_message", "kakan shape is invalid")
        return
    if kind == "reach":
        _require(isinstance(action.get("dahai"), dict), "invalid_message", "reach requires nested dahai")
        _require(action["dahai"].get("type") == "dahai" and action["dahai"].get("actor") == action["actor"], "invalid_message", "nested reach dahai mismatch")
        _check_action_object(action["dahai"], expected_actor=action["actor"])
        return
    _require(kind in {"hora", "ryukyoku"}, "invalid_message", "unknown action type")


def _check_request(message: Mapping[str, Any], *, grace_ms: int | None = None, extension_contexts: Mapping[str, Sequence[str]] | None = None) -> None:
    extension_contexts = extension_contexts or {}
    request_id = message.get("request_id")
    seat = message.get("seat")
    _require(isinstance(request_id, str) and ID_RE.fullmatch(request_id), "invalid_message", "request_id is invalid")
    _require(type(seat) is int and 0 <= seat <= 3, "invalid_message", "request seat is invalid")
    actions = message.get("legal_actions")
    _require(isinstance(actions, list) and 1 <= len(actions) <= 512, "invalid_message", "legal_actions is not complete")
    action_ids: set[str] = set()
    choices: set[str] = set()
    for candidate in actions:
        _require(isinstance(candidate, dict), "invalid_message", "action candidate is not an object")
        action_id = candidate.get("action_id")
        _require(isinstance(action_id, str) and ID_RE.fullmatch(action_id), "invalid_message", "action_id is invalid")
        _require(action_id not in action_ids, "invalid_message", "action_id is not unique")
        action_ids.add(action_id)
        _check_action_object(candidate.get("action"), expected_actor=seat, extension_types=extension_contexts)
        choice = canonical_action(candidate["action"])
        _require(choice not in choices, "invalid_message", "same choice is listed under more than one action ID")
        choices.add(choice)
    _require(message.get("default_action_id") in action_ids, "invalid_message", "default_action_id is not a legal action")
    default = next(c["action"] for c in actions if c["action_id"] == message["default_action_id"])
    if any(c["action"]["type"] == "none" for c in actions):
        _require(default["type"] == "none", "invalid_message", "reaction default must be none")
    else:
        _require(default["type"] == "dahai" and default.get("tsumogiri") is True, "invalid_message", "draw default must discard the drawn tile")
    group = message.get("decision_group_id")
    _require((group is not None) == any(c["action"]["type"] == "none" for c in actions), "invalid_message", "reaction requests require a group and a none candidate")
    context = "reaction" if group is not None or any(c["action"]["type"] == "none" for c in actions) else "turn"
    for candidate in actions:
        kind = candidate["action"]["type"]
        if kind in extension_contexts:
            _require(context in extension_contexts[kind], "invalid_message", "extension action is not allowed in this decision context")
    grouped = any(x in message for x in GROUP_FIELDS)
    _require(not grouped or isinstance(group, str), "invalid_message", "decision group fields require an id")
    if group is not None:
        _require(all(c["action"]["type"] in {"none", "hora", "chi", "pon", "daiminkan"} or c["action"]["type"] in extension_contexts for c in actions), "invalid_message", "group contains a non-reaction action")
        _require(all(x in message for x in GROUP_FIELDS), "invalid_message", "decision group is incomplete")
        timeout_ms = message.get("timeout_ms")
        time_bank_ms = message.get("time_bank_ms")
        group_deadline_ms = message.get("decision_group_deadline_ms")
        _require(isinstance(timeout_ms, int) and isinstance(time_bank_ms, int) and isinstance(group_deadline_ms, int), "invalid_message", "decision group deadline fields are invalid")
        deadline_floor = timeout_ms + time_bank_ms
        if grace_ms is not None:
            _require(isinstance(grace_ms, int) and not isinstance(grace_ms, bool) and grace_ms >= 0, "invalid_message", "trace grace_ms is invalid")
            deadline_floor += grace_ms
        _require(group_deadline_ms >= deadline_floor, "invalid_message", "decision group deadline is shorter than the available request time")
        members = message["decision_group_members"]
        _require(isinstance(members, list) and len(members) == 3, "invalid_message", "reaction group must contain the three other seats")
        member_ids = []
        member_seats = []
        for member in members:
            _require(isinstance(member, dict) and {"request_id", "seat"}.issubset(member) and all(key in {"request_id", "seat"} or EXTENSION_FIELD_RE.fullmatch(key) for key in member), "invalid_message", "decision group member shape is invalid")
            _require(isinstance(member["request_id"], str) and ID_RE.fullmatch(member["request_id"]), "invalid_message", "group request_id is invalid")
            _require(isinstance(member["seat"], int) and 0 <= member["seat"] <= 3, "invalid_message", "group seat is invalid")
            member_ids.append(member["request_id"])
            member_seats.append(member["seat"])
        _require(len(set(member_ids)) == len(member_ids) and len(set(member_seats)) == len(member_seats), "invalid_message", "group members are not unique")
        _require((request_id, seat) in set(zip(member_ids, member_seats)), "invalid_message", "request/seat pair is absent from its group")
        _require(message.get("decision_group_close") == "all_selected_or_deadline", "invalid_message", "unsupported group close")


def _check_decision_cause(request: Mapping[str, Any], cause: Mapping[str, Any]) -> None:
    seat, kind = request["seat"], cause["type"]
    _require(kind in {"tsumo", "dahai", "ankan_declared", "kakan_declared"}, "invalid_message", "event cannot cause a decision")
    turn = kind == "tsumo"
    _require((cause["actor"] == seat) == turn, "invalid_message", "cause actor and request owner differ")
    _require(("decision_group_id" not in request) == turn, "invalid_message", "decision context differs from cause")
    if not turn:
        _require({m["seat"] for m in request["decision_group_members"]} == set(range(4)) - {cause["actor"]}, "invalid_message", "group does not contain exactly the other seats")
    for candidate in request["legal_actions"]:
        action = candidate["action"]
        t = action["type"]
        if t in {"chi", "pon", "daiminkan"}:
            _require(kind == "dahai" and action["target"] == cause["actor"] and action["pai"] == cause["pai"], "invalid_message", "call differs from its cause discard")
            _require(t != "chi" or action["target"] == (seat + 3) % 4, "invalid_message", "chi is not from the preceding seat")
        elif t in {"dahai", "reach", "ankan", "kakan", "ryukyoku"}:
            _require(turn, "invalid_message", "draw action proposed in a reaction")
            discard = action.get("dahai", action)
            if discard["type"] == "dahai" and discard["tsumogiri"]:
                _require(discard["pai"] == cause["pai"], "invalid_message", "tsumogiri is not the drawn tile")


def _check_snapshot(message: Mapping[str, Any], extension_contexts: Mapping[str, Sequence[str]] | None = None,
                    rules: Mapping[str, Any] | None = None) -> None:
    state = message.get("state")
    _require(isinstance(state, dict), "invalid_message", "snapshot state is missing")
    _check_players(state.get("players"))
    mode, view, seat = state.get("mode"), state.get("view"), state.get("seat")
    _check_mode_view(mode, view, seat, context="snapshot")
    pending_present = "pending_requests" in state
    if mode == "play":
        _require(pending_present, "invalid_message", "play snapshot requires pending_requests")
        if isinstance(state.get("kyoku"), dict):
            _require("self_state" in state["kyoku"], "invalid_message", "play snapshot requires self_state")
    else:
        _require(not pending_present, "invalid_message", "public snapshot must omit pending_requests")
        if isinstance(state.get("kyoku"), dict):
            _require("self_state" not in state["kyoku"], "invalid_message", "public snapshot must omit self_state")
    pending = state.get("pending_requests", [])
    _require(len(pending) <= 1, "invalid_message", "snapshot contains another seat's request")
    _require(isinstance(state.get("kyoku"), dict) == (state.get("game_phase") == "in_kyoku"),
             "invalid_message", "snapshot kyoku presence differs from phase")
    if isinstance(state.get("kyoku"), dict):
        kyoku = state["kyoku"]
        turn = kyoku["turn"]
        _check_event_visibility(turn["last_event"], mode, view, seat)
        phase = turn["phase"]
        initial_observer = mode == "spectate" and message["seq"] == 1 and message["replaces_through_seq"] == 0
        if initial_observer:
            _require(turn["last_event_seq"] is None, "invalid_message", "initial observer has no earlier session event")
        else:
            _require(type(turn["last_event_seq"]) is int and 0 < turn["last_event_seq"] <= message["replaces_through_seq"], "invalid_message", "snapshot cause event lies outside replacement range")
        # A snapshot is fixed only at a transaction boundary: its last
        # committed event is a decision cause or the round start, never a
        # transaction-interior event (call, acceptance, marker, pao).
        reactions = {"dahai", "ankan_declared", "kakan_declared"}
        _require(phase == "awaiting_draw" and turn["last_event"]["type"] == "start_kyoku"
                 or phase == "awaiting_action" and turn["last_event"]["type"] == "tsumo"
                 or phase == "awaiting_responses" and turn["last_event"]["type"] in reactions
                 or phase == "resolving" and turn["last_event"]["type"] in reactions | {"tsumo"},
                 "invalid_message", "snapshot phase does not follow its last committed event")
        needs_request = mode == "play" and ((phase == "awaiting_action" and turn["actor"] == seat) or (phase == "awaiting_responses" and turn["actor"] != seat))
        _require(len(pending) == int(needs_request), "invalid_message", "pending requests do not match this seat's turn")
        declared = turn["last_event"]["type"] in {"ankan_declared", "kakan_declared"}
        _require(kyoku["pending_kan"] == (turn["last_event"] if declared else None),
                 "invalid_message", "snapshot pending kan differs from its cause")
        # A declared-but-unaccepted riichi exists only inside the reach
        # discard's reaction window, and a deferred dora marker survives a
        # boundary only while the rinshan decision is open (§10.1–§10.3).
        reach_declared = [a for a, s in enumerate(kyoku["reach_status"]) if s["state"] == "declared"]
        _require(not reach_declared or (reach_declared == [turn["actor"]] and turn["last_event"]["type"] == "dahai"
                                        and phase in {"awaiting_responses", "resolving"}),
                 "invalid_message", "unaccepted reach declaration survives outside its discard window")
        for a, s in enumerate(kyoku["reach_status"]):
            river = kyoku["rivers"][a]
            if s["state"] == "none":
                _require(not s["double"] and not s["ippatsu"] and not any(t["reach"] for t in river),
                         "invalid_message", "reach flags or river mark precede any declaration")
            elif s["state"] == "declared":
                _require(river and river[-1]["reach"] and not s["ippatsu"], "invalid_message",
                         "declaration discard is unmarked or carries ippatsu")
                _require(s["double"] == (len(river) == 1 and not any(kyoku["melds"])), "invalid_message",
                         "double flag differs from declaration-time eligibility")
            else:
                _require(any(t["reach"] for t in river), "invalid_message", "accepted reach has no marked discard")
                _require(not s["double"] or river[0]["reach"], "invalid_message",
                         "double flag differs from declaration-time eligibility")
                _require(s["double"] or not (river[0]["reach"] and not any(kyoku["melds"])), "invalid_message",
                         "double flag differs from declaration-time eligibility")
                _require(not s["ippatsu"] or river[-1]["reach"], "invalid_message", "ippatsu survives a post-reach discard")
                _require(s["ippatsu"] or not (river[-1]["reach"] and not any(kyoku["melds"])), "invalid_message",
                         "ippatsu flag differs from the open reach window")
        cause = turn["last_event"]
        # The turn actor and projected state follow the committed cause: a
        # round start fixes the dealer and a fresh board, while a decision
        # cause belongs to the acting seat and its physical effects are
        # already visible in rivers, hands and the pending declaration.
        if cause["type"] == "start_kyoku":
            _require(turn["actor"] == cause["oya"]
                     and all(kyoku[k] == cause[k] for k in ("bakaze", "kyoku", "oya", "honba", "kyotaku", "extension_round"))
                     and all(("tiles" in hand) == ("tiles" in dealt)
                             and (Counter(hand["tiles"]) == Counter(dealt["tiles"]) if "tiles" in hand
                                  else hand["count"] == dealt["count"])
                             for hand, dealt in zip(kyoku["hands"], cause["hands"]))
                     and kyoku["dora_markers"] == [cause["dora_marker"]]
                     and state["scores"] == cause["scores"],
                     "invalid_message", "snapshot round start differs from its start event")
            _require(not any(kyoku["rivers"]) and not any(kyoku["melds"]) and not kyoku["pao"]
                     and kyoku["kan_counts"] == [0] * 4 and not kyoku["rinshan"] and not kyoku["haitei"]
                     and all(s["state"] == "none" for s in kyoku["reach_status"])
                     and kyoku["wall_remaining"] == 70
                     and (kyoku.get("self_state") is None
                          or not kyoku["self_state"]["temporary_furiten"] and not kyoku["self_state"]["riichi_furiten"]),
                     "invalid_message", "snapshot round start is not a fresh deal")
        else:
            _require(turn["actor"] == cause["actor"], "invalid_message", "snapshot turn actor differs from its cause")
            actor = turn["actor"]
            if cause["type"] == "dahai":
                tail = kyoku["rivers"][actor][-1] if kyoku["rivers"][actor] else None
                _require(tail is not None and tail["pai"] == cause["pai"]
                         and tail["tsumogiri"] == cause["tsumogiri"] and tail["called_by"] is None
                         and tail["reach"] == (kyoku["reach_status"][actor]["state"] == "declared"),
                         "invalid_message", "snapshot river tail differs from its cause discard")
            elif cause["type"] == "tsumo":
                hand = kyoku["hands"][actor]
                _require(cause["pai"] is None or "tiles" not in hand or cause["pai"] in hand["tiles"],
                         "invalid_message", "snapshot draw is absent from the visible hand")
            else:
                _require(len({tile_index(t) for t in cause["consumed"]}) == 1,
                         "invalid_message", "kan declaration mixes tile kinds")
                _require(kyoku["wall_remaining"] > 0 and sum(kyoku["kan_counts"]) < 4,
                         "invalid_message", "kan declaration lacks commit capacity")
                if cause["type"] == "kakan_declared":
                    _require(kyoku["reach_status"][actor]["state"] == "none"
                             and tile_index(cause["pai"]) == tile_index(cause["consumed"][0])
                             and any(m["type"] == "pon" and Counter([m["pai"], *m["consumed"]]) == Counter(cause["consumed"])
                                     for m in kyoku["melds"][actor]),
                             "invalid_message", "kakan declaration has no matching pon")
                hand = kyoku["hands"][actor]
                if "tiles" in hand:
                    needed = Counter(cause["consumed"] if cause["type"] == "ankan_declared" else [cause["pai"]])
                    _require(not needed - Counter(hand["tiles"]), "invalid_message", "kan declaration uses absent visible tiles")
        # Self furiten flags follow public state: riichi furiten exists only
        # under an accepted declaration, and a seat's own draw clears its
        # temporary furiten (§10.4, §13.3).
        self_state = kyoku.get("self_state")
        if self_state is not None:
            _require(isinstance(seat, int) and 0 <= seat <= 3, "invalid_message", "self state without a play seat")
            _require(not self_state["riichi_furiten"]
                     or kyoku["reach_status"][seat]["state"] == "accepted",
                     "invalid_message", "riichi furiten without an accepted declaration")
            _require(not self_state["temporary_furiten"]
                     or cause["type"] != "tsumo" or cause["actor"] != seat,
                     "invalid_message", "temporary furiten survives its own draw")
        try:
            check_snapshot_rinshan(kyoku, rules)
        except GameError as error:
            raise ArtifactError("invalid_message", str(error)) from error
        _require(sum(s["state"] == "accepted" for s in kyoku["reach_status"]) <= kyoku["kyotaku"],
                 "invalid_message", "accepted riichi deposits exceed the round's deposit count")
        for a in range(4):
            _require(kyoku["first_turn_eligible"][a] == (not kyoku["rivers"][a] and not any(kyoku["melds"])),
                     "invalid_message", "first-turn eligibility differs from public discard/call history")
        _require(kyoku["kyotaku"] == state["kyotaku"], "invalid_message", "snapshot kyotaku differs")
        _require(kyoku["oya"] == kyoku["kyoku"] - 1, "invalid_message", "snapshot round coordinates are unreachable")
        if rules is not None:
            _require(round_coordinates_reachable(kyoku, rules),
                     "invalid_message", "snapshot round coordinates are unreachable")
        if mode == "play":
            _require(kyoku["self_state"]["time_bank_ms"] == state["time_bank_ms"], "invalid_message", "snapshot time bank differs")
            _require(not kyoku["self_state"]["kuikae_forbidden"], "invalid_message", "snapshot pauses a compound discard")
        visible_seat = seat if mode == "play" else view.get("seat") if isinstance(view, dict) else None
        for actor, hand in enumerate(kyoku["hands"]):
            visible = view == "full" or actor == visible_seat
            _require(("tiles" in hand) == visible, "invalid_message", "snapshot hand visibility differs")
            physical_count = len(hand["tiles"]) if visible else hand["count"]
            holds_draw = (phase == "awaiting_action"
                          or phase == "resolving" and turn["last_event"]["type"] == "tsumo"
                          or kyoku["pending_kan"] is not None and phase in {"awaiting_responses", "resolving"})
            extra = int(actor == turn["actor"] and holds_draw)
            _require(physical_count == 13 - 3 * len(kyoku["melds"][actor]) + extra, "invalid_message", "snapshot concealed tile count differs")
            for meld in kyoku["melds"][actor]:
                _require(meld["actor"] == actor, "invalid_message", "snapshot meld seat differs")
                _require(len(meld["consumed"]) == {"chi":2,"pon":2,"daiminkan":3,"ankan":4,"kakan":3}[meld["type"]],
                         "invalid_message", "snapshot meld consumed count differs")
                target = meld.get("target")
                if meld["type"] == "ankan":
                    _require(target is None and len({tile_index(t) for t in meld["consumed"]}) == 1,
                             "invalid_message", "invalid ankan meld")
                    continue
                _require(isinstance(target, int) and 0 <= target <= 3 and target != actor,
                         "invalid_message", "snapshot meld target seat differs")
                indices = sorted(tile_index(t) for t in [meld.get("pai"), *meld["consumed"]])
                if meld["type"] == "chi":
                    _require(target == (actor + 3) % 4 and indices[0] < 27
                             and indices[0] // 9 == indices[-1] // 9
                             and indices == list(range(indices[0], indices[0] + 3)),
                             "invalid_message", "invalid chi meld geometry")
                else:
                    _require(len(set(indices)) == 1, "invalid_message", "invalid meld geometry")
                _require(any(t["pai"] == meld.get("pai") and t["called_by"] == actor
                             for t in kyoku["rivers"][target]),
                         "invalid_message", "called meld lacks its river discard")
            for tile in kyoku["rivers"][actor]:
                caller = tile["called_by"]
                _require(caller is None
                         or (isinstance(caller, int) and 0 <= caller <= 3 and caller != actor
                             and any(m["type"] != "ankan" and m["target"] == actor and m.get("pai") == tile["pai"]
                                     for m in kyoku["melds"][caller])),
                         "invalid_message", "called river tile has no matching meld")
        try:
            check_snapshot_public_history(kyoku)
        except GameError as error:
            raise ArtifactError("invalid_message", str(error)) from error
        _require(sum(kyoku["kan_counts"]) <= 4, "invalid_message", "too many kans")
        for actor in range(4):
            count = sum(m["type"] in {"ankan", "daiminkan", "kakan"} for m in kyoku["melds"][actor])
            _require(kyoku["kan_counts"][actor] == count, "invalid_message", "kan count differs from committed melds")
        concealed_count = sum(len(h["tiles"]) if "tiles" in h else h["count"] for h in kyoku["hands"])
        meld_count = sum(len(m["consumed"]) + int(m["type"] != "ankan") for row in kyoku["melds"] for m in row)
        river_count = sum(t["called_by"] is None for river in kyoku["rivers"] for t in river)
        dead_count = 14 + int(kyoku["rinshan"] and phase == "awaiting_draw")
        _require(concealed_count + meld_count + river_count + kyoku["wall_remaining"] + dead_count == 136, "invalid_message", "snapshot loses or creates physical tiles")
        # Every publicly visible tile — own hand, uncalled river discards,
        # committed melds, declared kan tiles and dora markers — is bounded by the
        # physical wall: at most four copies of a tile kind, and the red/ordinary
        # five split negotiated in the rules when they are known.
        known = known_round_tiles(kyoku)
        _require(all(n <= 4 for n in Counter(tile_index(t) for t in known).values()),
                 "invalid_message", "more than four copies of a tile")
        if rules is not None:
            physical = Counter(known)
            for suit in "mps":
                red = rules["red_fives"][suit]
                _require(physical[f"5{suit}r"] <= red and physical[f"5{suit}"] <= 4 - red,
                         "invalid_message", "red/ordinary five inventory exceeded")
        _require(len(kyoku["dora_markers"]) == 1 + sum(kyoku["kan_counts"]) - int(kyoku["pending_dora"] is not None), "invalid_message", "dora count differs from kan state")
        pao_keys = [(item["actor"], item["yaku_id"]) for item in kyoku["pao"]]
        _require(len(pao_keys) == len(set(pao_keys)) and all(item["actor"] != item["liable_seat"] for item in kyoku["pao"]), "invalid_message", "pao assignments are invalid")
        pao_yakus = rules["pao"]["yakus"] if rules is not None else ["daisangen", "daisuushii"]
        known_pao = public_pao(kyoku["melds"], {"pao":{"yakus":pao_yakus}})
        _require(sorted(kyoku["pao"], key=lambda p:(p["actor"],p["yaku_id"])) == known_pao, "invalid_message", "snapshot pao differs from public meld history")
        for request in pending:
            _check_request(request, extension_contexts=extension_contexts)
            _check_decision_cause(request, turn["last_event"])
            _require(request["seat"] == seat and request["caused_by_seq"] == turn["last_event_seq"] < message["replaces_through_seq"], "invalid_message", "snapshot request owner/cause differs")
            selection = request["selection"]
            if selection is not None:
                _require(selection["action_id"] in {c["action_id"] for c in request["legal_actions"]}, "invalid_message", "snapshot selection is not legal")
                _require(selection["source"] != "default" or selection["action_id"] == request["default_action_id"], "invalid_message", "snapshot default selection differs")
                _require(selection["time_bank_ms"] <= request["time_bank_ms"], "invalid_message", "snapshot selection invents bank time")
                _require(selection["time_bank_ms"] == state["time_bank_ms"], "invalid_message", "selected time bank differs from the shared balance")
                if rules is not None:
                    try:
                        check_clock(request, selection, rules["time_control"]["grace_ms"],
                                    user=selection["source"] == "user",
                                    timeout=selection["source"] == "default" and rules["invalid_action_policy"] != "default")
                    except SessionError as error:
                        raise ArtifactError(error.code, str(error)) from error
            else:
                _require(request["time_bank_ms"] == state["time_bank_ms"], "invalid_message", "open request changed the shared balance")
    else:
        _require(not pending, "invalid_message", "out-of-round snapshot contains pending requests")
    _require((state.get("next_kyoku") is not None) == (state.get("game_phase") == "between_kyoku"),
             "invalid_message", "snapshot next kyoku differs from phase")
    if state.get("next_kyoku") is not None:
        nxt = state["next_kyoku"]
        _require(nxt["kyotaku"] == state["kyotaku"] and nxt["oya"] == nxt["kyoku"] - 1,
                 "invalid_message", "snapshot next kyotaku differs")
        if rules is not None:
            _require(round_coordinates_reachable(nxt, rules),
                     "invalid_message", "snapshot next kyotaku differs")
    if state.get("game_phase") == "ended":
        order = sorted(range(4), key=lambda i: (-state["scores"][i], i))
        _require(state["final_rankings"] == [order.index(i) + 1 for i in range(4)], "invalid_message", "snapshot rankings differ")
    if rules is not None:
        _require(sum(state["scores"]) + state["kyotaku"] * rules["riichi_stick_value"]
                 == 4 * rules["starting_points"],
                 "invalid_message", "snapshot does not conserve scores and deposits")
    _require(("original_seq" in state) == (mode == "replay"), "invalid_message", "snapshot recording cursor differs from mode")
    _require(message.get("seq") == message.get("replaces_through_seq", -1) + 1, "invalid_message", "snapshot seq does not follow replacement range")


def semantic_message(message: Mapping[str, Any], case_id: str, expected_profile_hash: str | None = None) -> None:
    kind = message.get("kind")
    if kind == "event":
        event = message.get("event", {})
        if event.get("type") == "start_game":
            _check_rules(event.get("rules"))
            _check_players(event.get("players"))
        if event.get("type") == "end_kyoku":
            result = event.get("result", {})
            if result.get("type") == "ryukyoku":
                tenpai = result.get("tenpai")
                if result.get("reason") == "fanpai":
                    _require(isinstance(tenpai, list) and len(tenpai) == 4 and all(type(value) is bool for value in tenpai), "invalid_message", "fanpai requires a four-seat tenpai array")
                    deltas = event.get("deltas")
                    if isinstance(deltas, list) and len(deltas) == 4:
                        count = sum(tenpai)
                        if count in (0, 4):
                            _require(deltas == [0] * 4, "invalid_message", "unanimous or absent tenpai must not move points")
                        else:
                            total = count * deltas[next(i for i in range(4) if tenpai[i])]
                            _require(total >= 0 and total % 600 == 0
                                     and all(deltas[i] * count == total for i in range(4) if tenpai[i])
                                     and all(deltas[i] * (count - 4) == total for i in range(4) if not tenpai[i]),
                                     "invalid_message", "noten deltas do not follow the tenpai count split")
                else:
                    _require(result.get("reason") in {"kyushukyuhai", "suufon_renda", "suucha_riichi", "suukan_sanra", "sanchaho"}, "invalid_message", "unknown abortive draw reason")
                    _require(tenpai is None and event.get("deltas") == [0] * 4, "invalid_message", "abortive draw must preserve scores without tenpai settlement")
            deltas = event.get("deltas")
            if result.get("type") != "hora" and isinstance(deltas, list):
                _require(sum(deltas) == 0, "invalid_message", "score deltas must conserve points")
        if event.get("type") == "end_game":
            rankings = event.get("rankings")
            _require(isinstance(rankings, list) and set(rankings) == {1, 2, 3, 4}, "invalid_message", "rankings must contain each rank exactly once")
            scores = event.get("scores")
            _require(isinstance(scores, list) and len(scores) == 4 and all(isinstance(score, int) and not isinstance(score, bool) for score in scores), "invalid_message", "end_game scores are invalid")
            for left in range(4):
                for right in range(4):
                    if scores[left] > scores[right]:
                        _require(rankings[left] < rankings[right], "invalid_message", "rankings do not follow descending scores")
            order = sorted(range(4), key=lambda i: (-scores[i], i))
            _require(rankings == [order.index(i) + 1 for i in range(4)], "invalid_message", "ranking tie does not follow initial seat order")
        if event.get("type") == "end_kyoku":
            result = event.get("result", {})
            if result.get("type") == "hora":
                wins = result.get("wins")
                _require(isinstance(wins, list) and 1 <= len(wins) <= 3, "invalid_message", "hora wins count is invalid")
                actors = []
                for win in wins:
                    actors.append(win.get("actor"))
                    yaku_ids = [item.get("id") for item in win.get("yakus", [])]
                    _require(yaku_ids == sorted(yaku_ids), "invalid_message", "win yaku ids are not in ASCII order")
                    bonus_ids = [item.get("id") for item in win.get("bonuses", [])]
                    _require(bonus_ids == sorted(bonus_ids), "invalid_message", "win bonus ids are not in ASCII order")
                    regular_han = sum(item.get("value", 0) for item in win.get("yakus", []) if item.get("unit") == "han")
                    bonus_han = sum(item.get("han", 0) for item in win.get("bonuses", []))
                    yakuman = sum(item.get("value", 0) for item in win.get("yakus", []) if item.get("unit") == "yakuman")
                    if yakuman:
                        _require(win.get("han") == 0 and win.get("bonuses") == [], "invalid_message", "yakuman win must not carry han/bonuses")
                        _require(win.get("fu") == 0 and all(item.get("unit") == "yakuman" for item in win["yakus"]), "invalid_message", "yakuman must not mix ordinary roles or fu")
                    else:
                        _require(win.get("han") == regular_han + bonus_han, "invalid_message", "win han does not match yaku/bonus sum")
                _require(actors == sorted(actors) and len(set(actors)) == len(actors), "invalid_message", "wins must be sorted by unique actor")
                win_deltas = [sum(win.get("deltas", [0, 0, 0, 0])[i] for win in wins) for i in range(4)]
                _require(result.get("wins") and event.get("deltas") == win_deltas, "invalid_message", "hora deltas do not match win deltas")
            if result.get("type") == "penalty":
                offender = result.get("offender")
                payments = result.get("penalty", {}).get("payments", [])
                delta = [0, 0, 0, 0]
                shares = {}
                for payment in payments:
                    _require(payment.get("from") == offender and payment.get("to") != payment.get("from"), "invalid_message", "penalty payment endpoints are invalid")
                    _require(payment.get("to") not in shares, "invalid_message", "penalty payment recipient is duplicated")
                    points = payment.get("points", 0)
                    delta[payment["from"]] -= points
                    delta[payment["to"]] += points
                    shares[payment["to"]] = points
                _require(event.get("deltas") == delta, "invalid_message", "penalty deltas do not match payments")
                if type(offender) is int and 0 <= offender <= 3:
                    amount = sum(shares.values())
                    others = [seat for seat in range(4) if seat != offender]
                    base, remainder = amount // 300 * 100, amount % 300
                    expected = {seat: base + (remainder if seat == others[0] else 0) for seat in others}
                    expected = {seat: points for seat, points in expected.items() if points}
                    _require(shares == expected, "invalid_message", "penalty split does not follow the chombo distribution")
    elif kind == "join":
        if message.get("version") != PROTOCOL:
            raise ArtifactError("unsupported_version", "unsupported protocol version")
        if expected_profile_hash is not None and message.get("profile_hash") != expected_profile_hash:
            raise ArtifactError("profile_mismatch", "profile hash does not match the selected release")
        _check_capabilities(message.get("capabilities"))
        mode, view = message.get("mode"), message.get("view")
        _require(mode in {"play", "spectate", "replay"}, "invalid_message", "unknown join mode")
        if mode == "play":
            _require(view == "seat" and "target" not in message, "invalid_message", "play join requires seat and forbids target")
        elif mode == "spectate":
            _require(view == "public" and "target" in message and "resume" not in message, "invalid_message", "spectate join shape is invalid")
            _check_target(message.get("target"), allowed_types={"game"})
        else:
            _require("target" in message and "resume" not in message, "invalid_message", "replay join shape is invalid")
            _require((isinstance(view, str) and view in {"public", "full"}) or (isinstance(view, dict) and "seat" in view and all(key == "seat" or EXTENSION_FIELD_RE.fullmatch(key) for key in view) and type(view.get("seat")) is int and 0 <= view["seat"] <= 3), "invalid_message", "replay view is invalid")
            _check_target(message.get("target"), allowed_types={"game", "recording"})
        limits = message.get("receive_limits", {})
        if message.get("profile") == PROFILE and (
            limits.get("max_message_bytes") != 1048576
            or limits.get("max_json_depth") != 64
            or limits.get("max_unresolved_requests") != 4
        ):
            raise ArtifactError("unsupported_limit", "riichi-4p minimum receive limits not met")
        resume = message.get("resume")
        if resume is not None:
            _require(mode == "play", "resume_unavailable", "resume is only valid for play")
            _require(isinstance(resume, dict) and isinstance(resume.get("last_seq"), int) and resume["last_seq"] >= 0, "resume_unavailable", "resume last_seq is outside the retained range")
    elif kind == "welcome":
        _check_rules(message.get("rules"))
        _check_players(message.get("players"))
        _check_mode_view(message.get("mode"), message.get("view"), message.get("seat"), context="welcome")
        resumed = message.get("resumed")
        _require(isinstance(resumed, bool), "invalid_message", "welcome resumed is invalid")
        capabilities = message.get("capabilities")
        _require(isinstance(capabilities, list) and all(isinstance(c, str) for c in capabilities) and capabilities == sorted(set(capabilities)), "invalid_message", "welcome capabilities must be sorted and unique")
        resume_enabled = message["mode"] == "play" and "resume" in capabilities
        _require(("resume" in message) == resume_enabled, "invalid_message", "welcome resume capability/member differs")
        _require(message["mode"] == "play" or "resume" not in capabilities, "invalid_message", "resume enabled in a non-play mode")
        if resumed:
            _require("replay_from_seq" in message and "replay_through_seq" in message and "resume" in message, "invalid_message", "resumed welcome is incomplete")
            _require(message["replay_from_seq"] <= message["replay_through_seq"] + 1, "invalid_message", "resume range is reversed")
        else:
            _require("replay_from_seq" not in message and "replay_through_seq" not in message, "invalid_message", "new welcome must not contain replay bounds")
    elif kind == "hello":
        _check_capabilities(message.get("capabilities"))
        names = [profile.get("name") for profile in message.get("profiles", [])]
        _require(len(names) == len(set(names)), "invalid_message", "profile advertisements repeat a name")
        for profile in message.get("profiles", []):
            _require(isinstance(profile, dict), "invalid_message", "profile advertisement is invalid")
            revisions = profile.get("revisions", [])
            hashes = profile.get("hashes", {})
            _require(set(revisions) == set(hashes), "invalid_message", "profile revisions and hashes must match")
            protocols = profile.get("protocol_versions", {})
            _require(set(revisions) == set(protocols), "invalid_message", "profile protocol matrix must cover all revisions")
            _require(all(set(versions) <= set(message["versions"]) for versions in protocols.values()), "invalid_message", "profile matrix contains an unadvertised protocol")
    elif kind == "request":
        _check_request(message)
    elif kind == "ack":
        _require(message.get("status") in TERMINAL_ACK_STATUSES | {"rejected"}, "invalid_message", "unknown ack status")
        _require(isinstance(message.get("elapsed_ms"), int) and 0 <= message["elapsed_ms"] <= 1800000, "invalid_message", "ack elapsed_ms is invalid")
        _require(isinstance(message.get("time_bank_ms"), int) and 0 <= message["time_bank_ms"] <= 600000, "invalid_message", "ack time_bank_ms is invalid")
        if message.get("status") == "rejected":
            _require(message.get("status") not in TERMINAL_ACK_STATUSES, "invalid_message", "rejected cannot be terminal")
    elif kind == "snapshot":
        _check_snapshot(message)
    elif kind == "action":
        if "resolved_status" in message and message.get("resolved_status") in TERMINAL_ACK_STATUSES:
            raise ArtifactError("request_conflict", "different action for a resolved request")
    elif kind == "error":
        if message.get("code") == "sequence_gap":
            _require(message.get("received_seq", 0) > message.get("expected_seq", 0), "invalid_message", "sequence_gap range is not increasing")
    elif kind == "future_kind":
        raise ArtifactError("invalid_message", "unknown message kind")


def semantic_score_trace(trace: Mapping[str, Any]) -> None:
    scores = trace.get("initial_scores")
    kyotaku = trace.get("initial_kyotaku")
    stick = trace.get("riichi_stick_value")
    _require(isinstance(scores, list) and len(scores) == 4, "invalid_message", "trace initial scores are invalid")
    _require(isinstance(kyotaku, int) and kyotaku >= 0, "invalid_message", "trace initial kyotaku is invalid")
    _require(isinstance(stick, int) and stick >= 0, "invalid_message", "trace stick value is invalid")
    for index, event in enumerate(trace.get("events", [])):
        _require(isinstance(event, dict), "invalid_message", f"score trace event {index} is invalid")
        delta = event.get("deltas")
        new_scores = event.get("scores")
        new_kyotaku = event.get("kyotaku", kyotaku)
        _require(isinstance(delta, list) and len(delta) == 4, "invalid_message", f"score trace event {index} deltas are invalid")
        _require(isinstance(new_scores, list) and len(new_scores) == 4, "invalid_message", f"score trace event {index} scores are invalid")
        _require(isinstance(new_kyotaku, int) and new_kyotaku >= 0, "invalid_message", f"score trace event {index} kyotaku is invalid")
        _require(all(new_scores[i] == scores[i] + delta[i] for i in range(4)), "invalid_message", f"score trace event {index} score update is invalid")
        _require(sum(new_scores) + new_kyotaku * stick == sum(scores) + kyotaku * stick, "invalid_message", f"score trace event {index} violates conservation")
        scores, kyotaku = new_scores, new_kyotaku


def semantic_event_trace(trace: Mapping[str, Any]) -> None:
    events = trace.get("events")
    _require(isinstance(events, list) and events, "invalid_message", "event trace is empty")
    types = [event.get("type") if isinstance(event, dict) else None for event in events]
    _require(all(isinstance(item, str) for item in types), "invalid_message", "event trace item is not an object")
    if "kakan_declared" in types:
        declared = types.index("kakan_declared")
        _require(declared + 1 < len(types) and types[declared + 1] in {"kakan", "end_kyoku"}, "invalid_message", "kakan declaration was not resolved before the next event")
    if "reach" in types and "reach_accepted" in types:
        reach_index = types.index("reach")
        accepted_index = types.index("reach_accepted")
        between = set(types[reach_index + 1 : accepted_index])
        _require(not (between & {"chi", "pon", "daiminkan", "hora"}), "invalid_message", "reach accepted after a competing reaction")


def semantic_resource_trace(trace: Mapping[str, Any]) -> None:
    _require(trace.get("trace_type") == "resource", "invalid_message", "resource trace type is invalid")
    backlog_bytes = trace.get("send_backlog_bytes", 0)
    backlog_messages = trace.get("send_backlog_messages", 0)
    _require(isinstance(backlog_bytes, int) and isinstance(backlog_messages, int), "invalid_message", "backlog values are invalid")
    _require(backlog_bytes >= 0 and backlog_messages >= 0, "invalid_message", "backlog values are negative")
    if backlog_bytes > 8388608 or backlog_messages > 1024:
        raise ArtifactError("resource_limit", "send backlog exceeds the protocol limit")
    if trace.get("peer_reads") is False and trace.get("write_deadline_ms") == 60000 and backlog_bytes >= 8388608:
        raise ArtifactError("resource_limit", "peer did not drain the send backlog")


def parse_jsonl_chunks(chunks: Iterable[bytes], max_bytes: int = 1048576) -> List[Dict[str, Any]]:
    """Frame and decode JSONL, including split UTF-8 and CRLF boundaries."""
    pending = bytearray()
    messages = []
    for chunk in chunks:
        for byte in chunk:
            if byte == 10:
                payload = bytes(pending[:-1] if pending.endswith(b"\r") else pending)
                pending.clear()
                _require(payload.startswith(b"{") and b"\r" not in payload, "invalid_frame", "JSONL frame must start with an object and contain no raw CR")
                message = strict_load_bytes(payload, max_bytes=max_bytes)
                _require(isinstance(message, dict), "invalid_message", "JSONL payload must be an object")
                messages.append(message)
            else:
                pending.append(byte)
                if len(pending) > max_bytes + 1 or (len(pending) == max_bytes + 1 and byte != 13):
                    raise ArtifactError("resource_limit", "JSONL payload exceeds byte limit")
    _require(not pending, "invalid_frame", "EOF during an incomplete JSONL frame")
    return messages


def semantic_transport_trace(trace: Mapping[str, Any]) -> None:
    _require(trace.get("trace_type") == "transport", "invalid_message", "transport trace type is invalid")
    transport = trace.get("transport")
    if transport == "jsonl":
        if "chunks_hex" in trace:
            try:
                chunks = [bytes.fromhex(value) for value in trace["chunks_hex"]]
            except (ValueError, TypeError) as exc:
                raise ArtifactError("invalid_frame", "invalid byte chunks") from exc
        else:
            lines = trace.get("lines")
            _require(isinstance(lines, list) and all(isinstance(line, str) for line in lines), "invalid_frame", "invalid JSONL input")
            stream = "".join(lines).encode("utf-8")
            splits = trace.get("split_at", [])
            _require(all(type(i) is int and 0 < i < len(stream) for i in splits) and splits == sorted(set(splits)), "invalid_frame", "invalid chunk boundaries")
            cuts = [0, *splits, len(stream)]
            chunks = [stream[a:b] for a,b in zip(cuts,cuts[1:])]
        messages = parse_jsonl_chunks(chunks)
        if "expected_messages" in trace:
            _require(_json_equal(messages, trace["expected_messages"]), "invalid_message", "decoded JSONL messages differ")
    elif transport == "websocket":
        _require(trace.get("message_type") in {"text", "binary"}, "invalid_frame", "invalid websocket message type")
        if trace.get("message_type") == "text":
            _require(isinstance(trace.get("message"), str) and trace["message"] != "", "invalid_frame", "empty websocket text message")
            if "fragments" in trace:
                _require(isinstance(trace["fragments"], list) and "".join(trace["fragments"]) == trace["message"], "invalid_frame", "websocket fragments do not reconstruct the message")
            message = strict_load_bytes(trace["message"].encode("utf-8"), max_bytes=1048576)
            _require(isinstance(message, dict), "invalid_message", "WebSocket payload must be an object")
        if trace.get("message_type") == "binary":
            raise ArtifactError("unsupported_frame", "binary websocket message")
    else:
        raise ArtifactError("invalid_frame", "unknown transport")


def semantic_replay_trace(trace: Mapping[str, Any]) -> None:
    previous_seq = 0
    previous_original = 0
    for event in trace.get("events", []):
        _require(isinstance(event, dict), "invalid_message", "replay trace item is invalid")
        _require(isinstance(event.get("seq"), int) and event["seq"] == previous_seq + 1, "invalid_message", "replay seq is not contiguous")
        previous_seq = event["seq"]
        if event.get("kind") == "event":
            _require(isinstance(event.get("original_seq"), int) and event["original_seq"] > 0, "invalid_message", "replay event requires original_seq")
            _require(event["original_seq"] > previous_original, "invalid_message", "replay original_seq is not ordered")
            previous_original = event["original_seq"]
            _require(isinstance(event.get("event"), dict) and isinstance(event["event"].get("type"), str), "invalid_message", "replay event payload is missing")
        else:
            _require("original_seq" not in event, "invalid_message", "original_seq is only for replay events")


def semantic_scoring_trace(trace: Mapping[str, Any]) -> None:
    required = {"child_30fu_3han_ron", "dealer_40fu_3han_ron", "child_30fu_2han_tsumo", "multiple_ron_settlement", "pao_split_rounding", "noten_by_tenpai_count", "double_yakuman", "red_dora_and_ura_dora"}
    actual = set(trace.get("scoring_vector_ids", []))
    _require(required.issubset(actual), "invalid_message", "scoring vector trace is incomplete")


def semantic_noten_trace(trace: Mapping[str, Any]) -> None:
    total = trace.get("total_points")
    _require(isinstance(total, int) and total >= 0 and total % 600 == 0, "invalid_message", "noten total_points is invalid")
    for case in trace.get("cases", []):
        tenpai = case.get("tenpai")
        deltas = case.get("deltas")
        _require(isinstance(tenpai, list) and len(tenpai) == 4 and isinstance(deltas, list) and len(deltas) == 4, "invalid_message", "noten case is invalid")
        count = sum(1 for value in tenpai if value is True)
        if count == 0 or count == 4:
            expected = [0, 0, 0, 0]
        elif count == 1:
            expected = [total if value else -total // 3 for value in tenpai]
        elif count == 2:
            expected = [total // 2 if value else -total // 2 for value in tenpai]
        else:
            expected = [total // 3 if value else -total for value in tenpai]
        _require(deltas == expected, "invalid_message", "noten payment does not match tenpai count")


def semantic_ack_trace(trace: Mapping[str, Any]) -> None:
    statuses = trace.get("statuses")
    _require(isinstance(statuses, list) and all(status in TERMINAL_ACK_STATUSES | {"rejected"} for status in statuses), "invalid_message", "ack status trace is invalid")
    if statuses and statuses[-1] == "rejected":
        _require(trace.get("request_open", True) is True, "invalid_message", "rejected ack incorrectly terminalized request")


def semantic_composite_trace(trace: Mapping[str, Any]) -> None:
    if "requests" in trace:
        semantic_request_trace({"requests": trace["requests"]})
    if "ack" in trace:
        semantic_ack_trace(trace["ack"])


def _check_scoring_fixture_semantics(fixture: Mapping[str, Any], base_rules: Mapping[str, Any], schemas: SchemaSet) -> None:
    fixture_id = fixture["id"]
    effective_rules = {**base_rules, **fixture["rule_overrides"]}
    schemas.validate(effective_rules, {"$ref": "urn:yamai:schema:yrc-0005:1.0-draft.7:riichi-4p-rules"})
    try:
        actual = calculate_scoring_fixture(dict(fixture), dict(base_rules))
    except ScoringError as exc:
        raise ArtifactError("scoring_error", f"{fixture_id}: {exc.code}: {exc}") from exc
    _require(_json_equal(actual, fixture["expected"]), "scoring_error", f"{fixture_id}: calculated roles, fu, payments, scores or deposits differ from the golden result")


def _trace_grace_ms(trace: Mapping[str, Any]) -> int | None:
    """Read the profile's actual grace_ms from a trace, if it is provided.

    A standalone request has no negotiated profile context, so callers leave
    this unset and only check timeout + bank.  State/request traces may carry
    a welcome, profile, rules object, or a welcome event; all of those are
    accepted as context rather than assuming the current profile default.
    """

    def read(source: Any, depth: int = 0) -> int | None:
        if source is None:
            return None
        _require(isinstance(source, Mapping), "invalid_message", "trace profile/rules context is invalid")
        if depth > 4:
            raise ArtifactError("invalid_message", "trace profile/rules context is too deep")
        if "grace_ms" in source:
            grace = source["grace_ms"]
            _require(isinstance(grace, int) and not isinstance(grace, bool) and 0 <= grace <= 600000, "invalid_message", "trace grace_ms is invalid")
            return grace
        for key in ("time_control", "rules", "profile"):
            if key in source:
                value = read(source[key], depth + 1)
                if value is not None:
                    return value
        return None

    values: List[int] = []
    for key in ("welcome", "profile", "rules"):
        if key in trace:
            value = read(trace[key])
            if value is not None:
                values.append(value)
    for event in trace.get("events", []):
        if isinstance(event, Mapping) and (event.get("type") == "welcome" or any(key in event for key in ("time_control", "rules", "profile"))):
            value = read(event)
            if value is not None:
                values.append(value)
    _require(len(set(values)) <= 1, "invalid_message", "trace grace_ms context differs")
    return values[0] if values else None


def semantic_request_trace(trace: Mapping[str, Any]) -> None:
    requests = trace.get("requests")
    _require(isinstance(requests, list), "invalid_message", "request trace is invalid")
    seats = []
    groups: Dict[str, Mapping[str, Any]] = {}
    grace_ms = _trace_grace_ms(trace)
    for request in requests:
        _check_request(request, grace_ms=grace_ms)
        seats.append(request["seat"])
        group_id = request.get("decision_group_id")
        if group_id is not None:
            _require(isinstance(group_id, str), "invalid_message", "decision group id is invalid")
            reference = groups.setdefault(group_id, request)
            _require(request.get("decision_group_members") == reference.get("decision_group_members"), "invalid_message", "group members differ across requests")
            _require(request.get("decision_group_deadline_ms") == reference.get("decision_group_deadline_ms"), "invalid_message", "group deadline differs across requests")
            _require(request.get("decision_group_close") == reference.get("decision_group_close"), "invalid_message", "group close policy differs across requests")
    _require(len(seats) == len(set(seats)), "invalid_message", "a seat has duplicate pending requests")
    for group_id, reference in groups.items():
        declared = {(member["request_id"], member["seat"]) for member in reference["decision_group_members"]}
        observed = {(request["request_id"], request["seat"]) for request in requests if request.get("decision_group_id") == group_id}
        _require(declared == observed, "invalid_message", f"group {group_id} does not contain all member requests")
        if grace_ms is not None:
            for request in requests:
                if request.get("decision_group_id") == group_id:
                    _require(
                        reference["decision_group_deadline_ms"] >= grace_ms + request["timeout_ms"] + request["time_bank_ms"],
                        "invalid_message",
                        f"group {group_id} deadline does not cover every member",
                    )
    if "snapshot_remaining_ms" in trace:
        for request in requests:
            _require(trace["snapshot_remaining_ms"] <= request.get("timeout_ms", 0), "invalid_message", "snapshot restarted a request deadline")


def semantic_welcome_trace(trace: Mapping[str, Any]) -> None:
    _require(trace.get("resumed") is True, "invalid_message", "welcome trace is not resumed")
    _require(isinstance(trace.get("replay_from_seq"), int) and trace["replay_from_seq"] > 0, "invalid_message", "welcome replay_from_seq is invalid")
    _require(type(trace.get("replay_through_seq")) is int and trace["replay_through_seq"] >= trace["replay_from_seq"] - 1, "invalid_message", "welcome replay frontier is invalid")
    _require(isinstance(trace.get("resume"), dict) and isinstance(trace["resume"].get("token"), str), "invalid_message", "welcome resume token is missing")


def semantic_state_trace(trace: Mapping[str, Any]) -> None:
    _trace_grace_ms(trace)
    if "requests" in trace:
        semantic_request_trace(trace)
    events = trace.get("events", [])
    _require(isinstance(events, list) and events, "invalid_message", "state trace is empty")
    for event in events:
        _require(isinstance(event, dict) and isinstance(event.get("type"), str), "invalid_message", "state trace event is invalid")
    types = [event["type"] for event in events]
    if "end_kyoku" in types and "end_game" in types:
        _require(types.index("end_kyoku") < types.index("end_game"), "invalid_message", "end_game precedes end_kyoku")
    if "start_kyoku" in types and "start_game" in types:
        _require(types.index("start_game") < types.index("start_kyoku"), "invalid_message", "start_kyoku precedes start_game")
    state = trace.get("initial_state")
    if state is not None:
        _require(isinstance(state, dict), "invalid_message", "initial state is invalid")
        game_started = bool(state.get("game_started", False))
        phase = state.get("phase", "idle")
        pending_kan = bool(state.get("pending_kan", False))
        wall = state.get("wall_remaining", 70)
        rinshan = bool(state.get("rinshan", False))
        first_turn = list(state.get("first_turn_eligible", [True, True, True, True]))
        kan_counts = list(state.get("kan_counts", [0, 0, 0, 0]))
        for event_index, event in enumerate(events):
            event_type = event["type"]
            if not game_started and event_type in {
                "start_kyoku", "tsumo", "dahai", "chi", "pon", "daiminkan",
                "ankan_declared", "ankan", "kakan_declared", "kakan", "dora",
                "reach", "reach_accepted", "pao", "end_kyoku", "end_game",
            }:
                raise ArtifactError("invalid_message", "game event precedes start_game")
            if event_type == "start_game":
                _require(not game_started, "invalid_message", "duplicate start_game")
                game_started = True
            elif event_type == "start_kyoku":
                _require(game_started, "invalid_message", "start_kyoku before start_game")
                phase, pending_kan, wall = "awaiting_draw", False, 70
                rinshan = False
                first_turn, kan_counts = [True] * 4, [0] * 4
            elif event_type == "tsumo":
                _require(phase == "awaiting_draw" and (wall > 0 or rinshan), "invalid_message", "tsumo outside draw phase")
                if not rinshan:
                    wall -= 1
                phase = "awaiting_action"
            elif event_type == "dahai":
                _require(phase == "awaiting_action", "invalid_message", "dahai outside action phase")
                actor = event.get("actor")
                _require(isinstance(actor, int) and 0 <= actor <= 3, "invalid_message", "dahai actor is invalid")
                first_turn[actor] = False
                rinshan = False
                phase = "awaiting_responses"
            elif event_type == "kakan_declared" or event_type == "ankan_declared":
                _require(phase == "awaiting_action" and not pending_kan, "invalid_message", "kan declaration outside action phase")
                pending_kan = True
            elif event_type in {"kakan", "ankan"}:
                _require(pending_kan, "invalid_message", "kan commit without declaration")
                actor = event.get("actor")
                _require(isinstance(actor, int) and 0 <= actor <= 3 and kan_counts[actor] < 4, "invalid_message", "kan count is invalid")
                kan_counts[actor] += 1
                _require(wall > 0 and sum(kan_counts) <= 4, "invalid_message", "kan has no replacement tile")
                wall -= 1
                rinshan = True
                first_turn = [False] * 4
                pending_kan, phase = False, "awaiting_draw"
            elif event_type == "end_kyoku":
                pending_kan = False
                phase = "between_kyoku"
            elif event_type == "end_game":
                _require("end_kyoku" in types[: event_index + 1], "invalid_message", "end_game before end_kyoku")


def semantic_lifecycle_trace(trace: Mapping[str, Any]) -> None:
    """Validate a complete final observation and earlier checkpoints."""
    grace = trace.get("grace_ms")
    _require(type(grace) is int and 0 <= grace <= 600000, "invalid_message", "invalid lifecycle grace")
    _require(trace.get("invalid_action_policy") in {"reject", "default", "chombo"}, "invalid_message", "invalid lifecycle policy")
    _require(trace.get("ron_policy") in {"multiple", "head_bump", "double_only"}, "invalid_message", "invalid ron policy")
    requests = trace.get("requests")
    _require(isinstance(requests, list) and 1 <= len(requests) <= 4, "invalid_message", "invalid lifecycle requests")
    semantic_request_trace({"requests": requests, "rules": {"time_control": {"grace_ms": grace}}})
    _require(len({r["request_id"] for r in requests}) == len(requests), "invalid_message", "duplicate lifecycle request id")
    if len(requests) > 1:
        _require(len({r.get("decision_group_id") for r in requests}) == 1 and requests[0].get("decision_group_id") is not None, "invalid_message", "lifecycle trace needs one complete group")
        _require(type(trace.get("target")) is int and 0 <= trace["target"] <= 3, "invalid_message", "lifecycle target is invalid")
        _require({r["seat"] for r in requests} == set(range(4)) - {trace["target"]}, "invalid_message", "reaction group must exclude its cause actor and include every other seat")
    steps = trace.get("steps")
    _require(isinstance(steps, list) and steps, "invalid_message", "empty lifecycle trace")
    for step in steps:
        _require(isinstance(step, dict) and step.get("op") in {"submit", "advance", "resolve"}, "invalid_message", "invalid lifecycle step")
        _require(type(step.get("at_us")) is int and step["at_us"] >= 0, "invalid_message", "invalid lifecycle timestamp")
        if step["op"] == "submit":
            _require(all(isinstance(step.get(k), str) and ID_RE.fullmatch(step[k]) for k in ("request_id", "action_id")), "invalid_message", "invalid lifecycle action ids")
    try:
        observed = evaluate_request_contract(dict(trace))
    except (KeyError, ValueError, StopIteration, TypeError) as exc:
        raise ArtifactError("invalid_message", f"invalid lifecycle trace: {exc}") from exc
    _require(observed[-1] == trace.get("expected"), "invalid_message", "request lifecycle final observation differs")
    for checkpoint in trace.get("checkpoints", []):
        step_index = checkpoint.get("step")
        _require(type(step_index) is int and 0 <= step_index < len(observed), "invalid_message", "invalid checkpoint index")
        _require(observed[step_index] == checkpoint.get("expected"), "invalid_message", "request lifecycle checkpoint differs")


def semantic_game_trace(trace: Mapping[str, Any]) -> None:
    schemas = SchemaSet()
    rules = strict_load(ROOT / f"test-vectors/yrc-0005/{PROFILE_REVISION}/scoring.json")["rules"]
    rules.update(deepcopy(trace.get("rule_overrides", {})))
    schemas.validate(rules, {"$ref": f"urn:yamai:schema:yrc-0005:{PROFILE_REVISION}:riichi-4p-rules"})
    op, data = trace["operation"], trace["input"]
    try:
        if op == "next_kyoku":
            actual = next_kyoku(data["current"], data["result"], data["scores"], data["kyotaku"], rules)
        elif op == "legal_actions":
            actual = legal_actions(data, rules)
            # Full candidate sets, compared independently of ID/ordering.
            for action in [*actual, *trace["expected"]]:
                schemas.validate(action, {"$ref": f"urn:yamai:schema:yrc-0003:{PROTOCOL}:action#/$defs/actionObject"})
            actual = sorted(canonical_action(a) for a in actual)
            expected = sorted(canonical_action(a) for a in trace["expected"])
            _require(len(expected) == len(set(expected)), "invalid_message", "expected choices are duplicated")
            _require(actual == expected, "invalid_message", "complete legal candidate set differs")
            return
        elif op == "furiten":
            actual = furiten(data, rules)
        elif op == "furiten_step":
            actual = furiten_step(data["position"], data["operation"], rules)
        elif op == "abortive_reason":
            actual = abortive_reason(data, rules)
        elif op == "kan_sequence":
            actual = kan_sequence(**data)
        elif op == "event_state":
            state = EventState(rules)
            if "snapshot" in data:
                schemas.validate(data["snapshot"], {"$ref": f"urn:yamai:schema:yrc-0003:{PROTOCOL}:snapshot"})
                _check_snapshot(data["snapshot"], rules=rules)
                state.restore(data["snapshot"]["state"])
            for event in data["events"]:
                schemas.validate(event, {"$ref": f"urn:yamai:schema:yrc-0003:{PROTOCOL}:event#/properties/event"})
                state.apply(event)
            observed = {"game_phase":state.game_phase,"scores":state.scores,"kyotaku":state.kyotaku,
                        "next":state.next,"round":state.round,"last_cause":state.last_cause}
            actual = {}
            for path in trace["observe"]:
                value = observed
                for key in path.split("."):
                    value = value[int(key)] if isinstance(value,list) else value[key]
                actual[path] = value
        else:
            raise ArtifactError("vector_error", "unknown game contract operation")
    except (GameError, ScoringError, KeyError, TypeError, ValueError) as error:
        raise ArtifactError("invalid_message", str(error)) from error
    _require(_json_equal(actual, trace["expected"]), "invalid_message", "game contract observation differs")


def _session_schema_validator(schemas: SchemaSet, expected_hash: str, definitions: Sequence[Mapping[str, Any]] = (), enabled: Sequence[str] = (), rules: Mapping[str, Any] | None = None):
    urn = f"urn:yamai:schema:yrc-0003:{PROTOCOL}:"
    extension_contexts: dict[str, Sequence[str]] = {}
    if definitions or any(cap not in {"resume", "snapshot"} for cap in enabled):
        schemas = deepcopy(schemas)
        registered = {definition["capability"]: definition for definition in definitions}
        _require(len(registered) == len(definitions), "unsupported_capability", "duplicate extension capability definition")
        private_types: set[tuple[str, str]] = set()
        extension_schema_ids: set[str] = set()
        def register(schema: Mapping[str, Any]) -> dict:
            sid = schema.get("$id")
            _require(isinstance(sid, str) and re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", sid) is not None and "#" not in sid, "unsupported_capability", "extension needs an absolute schema id without a fragment")
            if sid in schemas.schemas:
                _require(sid in extension_schema_ids and _json_equal(schemas.schemas[sid], schema), "unsupported_capability", "extension schema id is already bound to another definition")
                return {"$ref": sid}
            schemas.schemas[sid] = dict(schema)
            extension_schema_ids.add(sid)
            return {"$ref": sid}
        for capability in enabled:
            if capability in {"resume", "snapshot"}:
                continue
            _require(capability in registered and re.fullmatch(r"x-(?=[A-Za-z0-9_.-]{3,62}$)[A-Za-z0-9]+-[A-Za-z0-9][A-Za-z0-9_.-]*", capability), "unsupported_capability", "extension schema is not installed")
            definition = registered[capability]
            owner = capability.split("-", 2)[1]
            for category, schema_name, pointer in (("event_types", "event", "event"), ("action_types", "action", "actionObject")):
                for tag, descriptor in definition.get(category, {}).items():
                    _require(tag.startswith("x-" + owner + "-") and CAPABILITY_RE.fullmatch(tag) is not None and (category, tag) not in private_types, "unsupported_capability", "extension type collides or changes a standard identifier")
                    private_types.add((category, tag))
                    body = descriptor["schema"]
                    _require("type" in body.get("required", []) and body.get("properties", {}).get("type", {}).get("const") == tag, "unsupported_capability", "extension schema must fix its discriminator")
                    if category == "event_types":
                        schemas.schemas[urn + schema_name]["properties"][pointer]["oneOf"].append(register(body))
                    else:
                        _require("actor" in body.get("required", []), "unsupported_capability", "extension action requires its actor")
                        contexts = descriptor.get("contexts", [])
                        _require(bool(contexts) and set(contexts) <= {"turn", "reaction"}, "unsupported_capability", "extension action context is not declared")
                        extension_contexts[tag] = contexts
                        schemas.schemas[urn + schema_name]["$defs"][pointer]["oneOf"].append(register(body))
            for kind, constraint in definition.get("message_schemas", {}).items():
                _require(kind in {"event", "request", "action", "ack", "error", "snapshot"}, "unsupported_capability", "extension cannot add a message kind")
                schemas.schemas[urn + kind].setdefault("allOf", []).append(register(constraint))
        try:
            schemas.check_refs()
            schemas.check_keyword_support()
        except ArtifactError as error:
            raise ArtifactError("unsupported_capability", "extension schema cannot be loaded: " + str(error)) from error
    proposal = {"$ref": urn + "join-proposal"}

    def validate(kind: str, message: dict) -> None:
        try:
            if kind == "decision-cause":
                _check_decision_cause(message["request"], message["cause"])
                return
            if kind == "pending-request":
                _check_request(message, extension_contexts=extension_contexts)
                return
            if kind == "visible-event":
                _check_event_visibility(message["event"], message["mode"], message["view"], message["seat"])
                return
            if kind == "join-proposal":
                schemas.validate(message, proposal)
                _check_capabilities(message["capabilities"])
                return
            if kind == "host-application":
                _require(message.get("kind") in {"event", "request", "ack", "error", "snapshot"}, "invalid_message", "unexpected host application kind")
                schemas.validate(message, {"$ref": urn + "host-message"})
            elif kind == "player-application":
                _require(message.get("kind") in {"action", "error"}, "invalid_message", "unexpected player application kind")
                schemas.validate(message, {"$ref": urn + "player-message"})
            else:
                schemas.validate(message, {"$ref": urn + kind})
            if message.get("kind") == "request":
                _check_request(message, extension_contexts=extension_contexts)
            elif message.get("kind") == "snapshot":
                _check_snapshot(message, extension_contexts, rules=rules)
            else:
                semantic_message(message, "session-contract", expected_hash)
        except ArtifactError as error:
            raise SessionError(error.code, str(error)) from error
    return validate


def semantic_session_trace(trace: Mapping[str, Any], expected_hash: str) -> None:
    schemas = SchemaSet()
    validate = _session_schema_validator(schemas, expected_hash)
    kind = trace["trace_type"]
    try:
        if kind == "negotiation":
            actual = negotiate(trace["hello"], trace["join"], trace["welcome"], trace.get("context", {}), validate, PROTOCOL, PROFILE_REVISION, expected_hash)
        elif kind == "resume_tokens":
            actual = check_token_trace(dict(trace), validate, PROTOCOL, PROFILE_REVISION, expected_hash)
        elif kind == "resource_clock":
            actual = resource_trace(dict(trace))
        elif kind == "wire_direction":
            validate(trace["direction"] + "-application", trace["message"])
            actual = "valid"
        elif kind == "input_error":
            actual = classify_player_input(trace["message"], trace["context"], validate)
        elif kind == "extension_message":
            extended = _session_schema_validator(schemas, expected_hash, trace["definitions"], trace["enabled_capabilities"])
            extended(trace["direction"] + "-application", trace["message"])
            actual = "valid"
        elif kind == "replay_plan":
            history = [None if raw is None else raw.encode("utf-8") for raw in trace["history"]]
            for seq, raw in enumerate(history, 1):
                if raw is not None:
                    message = strict_load_bytes(raw, max_bytes=1048576)
                    validate("host-application", message)
                    _require(message["seq"] == seq, "invalid_message", "retained history sequence differs")
            plan = replay_plan(history, trace["expected_seq"], trace["received_seq"], snapshot=trace.get("snapshot", False))
            for raw in trace.get("append_after_plan", []):
                history.append(raw.encode("utf-8"))
            actual = {key: [p.decode("utf-8") for p in value] if key == "payloads" else value for key, value in plan.items()}
        elif kind == "receiver":
            validate("welcome", trace["welcome"])
            receiver_validate = _session_schema_validator(schemas, expected_hash, trace.get("definitions", []), trace["welcome"]["capabilities"], rules=trace["welcome"]["rules"])
            receiver = Receiver(trace["welcome"], lambda raw: strict_load_bytes(raw, max_bytes=1048576), receiver_validate, initial_snapshot=trace.get("initial_snapshot", False))
            actual = []
            for step in trace["steps"]:
                outcome = None
                try:
                    if step["op"] == "resume":
                        receiver.begin_resume(step["welcome"])
                        outcome = "resumed"
                    else:
                        _require(step["op"] == "receive", "invalid_message", "unknown receiver operation")
                        raw = step["raw"] if "raw" in step else json.dumps(step["message"], ensure_ascii=False, separators=(",", ":"))
                        outcome = receiver.receive(raw.encode("utf-8"))
                except (SessionError, ArtifactError) as error:
                    outcome = error.code
                    if getattr(error, "severity", "fatal") == "fatal":
                        receiver.closed = True
                actual.append({"outcome":outcome,"applied_seq":receiver.applied,"active_requests":sorted(receiver.active_requests),
                               "ended":receiver.ended,"closed":receiver.closed,"original_seq":receiver.original_seq,
                               "recovering":receiver.recovery is not None})
        elif kind == "multi_session":
            actual = []
            seen_sessions, seen_games, retired = [], [], []
            previous = None
            for session in trace["sessions"]:
                _require(previous is None or (previous.ended and not previous.closed), "invalid_message", "same transport started a new session before end_game")
                context = {"used_session_ids": seen_sessions, "finished_game_ids": seen_games, **session.get("context", {})}
                negotiate(session["hello"], session["join"], session["welcome"], context, validate, PROTOCOL, PROFILE_REVISION, expected_hash)
                w = session["welcome"]
                identity = {key:w[key] for key in ("yamai", "session_id", "game_id")}
                for action in session.get("old_actions", []):
                    result = classify_player_input(action, {"identity":identity,"known_request_ids":[],"retired_sessions":retired}, validate)
                    _require(result["code"] == "ignored", "invalid_message", "retired session affected new negotiation")
                receiver = Receiver(w, lambda raw: strict_load_bytes(raw, max_bytes=1048576), _session_schema_validator(schemas, expected_hash, rules=w["rules"]))
                for message in session["messages"]:
                    receiver.receive(json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
                actual.append({"session_id":w["session_id"],"game_id":w["game_id"],"last_seq":receiver.applied,"ended":receiver.ended})
                seen_sessions.append(w["session_id"])
                seen_games.append(w["game_id"])
                retired.append(identity)
                previous = receiver
        else:
            raise ArtifactError("vector_error", "unknown session trace")
    except SessionError as error:
        raise ArtifactError(error.code, str(error)) from error
    _require(_json_equal(actual, trace.get("expected")), "invalid_message", "session contract result differs")


def semantic_ledger_trace(trace: Mapping[str, Any], expected_hash: str) -> None:
    """Audit one peer's timestamped payloads and immutable ledger records.

    Shared group choices are verified by request_lifecycle traces. This peer
    capture cannot observe another session's private requests or ACKs.
    """
    schemas = SchemaSet()
    schemas.validate(trace, {"$ref": f"urn:yamai:schema:yrc-0003:{PROTOCOL}:stateful-trace"})
    validate = _session_schema_validator(schemas, expected_hash)
    clients = trace.get("clients", [])
    _require(len(clients) == 1, "invalid_message", "ledger capture describes one peer session")
    client = clients[0]
    ledger, previous, finished_transactions = {}, trace.get("ledger_start_seq", 1) - 1, set()
    current_transaction = None
    for entry in trace["ledger"]:
        msg, seq = entry["message"], entry["seq"]
        raw = entry["wire"].encode("utf-8")
        _require(_json_equal(strict_load_bytes(raw, max_bytes=1048576), msg), "sequence_conflict", "ledger bytes differ from decoded message")
        _require(msg.get("session_id") == trace["session_id"] and msg.get("game_id") == trace["game_id"] and msg.get("seq") == seq, "invalid_message", "ledger identity differs")
        _require(seq == previous + 1 or (msg.get("kind") == "snapshot" and msg["replaces_through_seq"] >= previous and seq == msg["replaces_through_seq"] + 1), "invalid_message", "ledger has an unexplained gap")
        tx = entry["transaction_id"]
        if tx != current_transaction:
            _require(tx not in finished_transactions, "invalid_message", "transaction is interleaved")
            if current_transaction is not None:
                finished_transactions.add(current_transaction)
            current_transaction = tx
        ledger[seq], previous = entry, seq
    hello = join = receiver = None
    now, seen = -1, set()
    starts, first_actions = {}, {}
    pending_transaction = None
    try:
        for step in trace["messages"]:
            _require(step["at_ms"] >= now, "invalid_message", "capture clock moved backwards")
            now = step["at_ms"]
            _require(step["client_id"] == client["client_id"], "invalid_message", "message belongs to another capture")
            msg, raw = step["message"], step["wire"].encode("utf-8")
            _require(_json_equal(strict_load_bytes(raw, max_bytes=1048576), msg), "sequence_conflict", "captured bytes differ from decoded message")
            kind = msg["kind"]
            if kind == "hello":
                _require(step["direction"] == "out" and receiver is None, "invalid_message", "unexpected hello")
                validate("hello", msg)
                hello = msg
            elif kind == "join":
                _require(step["direction"] == "in" and hello is not None and receiver is None, "invalid_message", "unexpected join")
                join = msg
            elif kind == "welcome":
                _require(step["direction"] == "out" and join is not None and receiver is None, "invalid_message", "unexpected welcome")
                negotiate(hello, join, msg, trace.get("context", {}), validate, PROTOCOL, PROFILE_REVISION, expected_hash)
                _require(all(msg[k] == client[k] for k in ("mode", "view", "seat")), "invalid_message", "capture descriptor differs from welcome")
                _require(msg["session_id"] == trace["session_id"] and msg["game_id"] == trace["game_id"], "invalid_message", "welcome differs from capture identity")
                receiver = Receiver(msg, strict_load_bytes, _session_schema_validator(schemas, expected_hash, rules=msg["rules"]), initial_snapshot=msg["mode"] == "spectate" and trace.get("context", {}).get("game_started", False))
            elif step["direction"] == "in":
                _require(receiver is not None, "invalid_message", "input before welcome")
                validate("player-application", msg)
                _require(msg["session_id"] == trace["session_id"] and msg["game_id"] == trace["game_id"], "invalid_message", "input session differs")
                if kind == "action":
                    rid = msg["request_id"]
                    _require(rid in receiver.requests, "invalid_action", "action references an unknown request")
                    first_actions.setdefault(rid, (msg["action_id"], now))
            else:
                _require(receiver is not None, "invalid_message", "application message before welcome")
                seq = msg["seq"]
                _require(seq in ledger and raw.decode("utf-8") == ledger[seq]["wire"], "sequence_conflict", "wire retransmission differs from immutable ledger")
                _require(all(step.get(k) == ledger[seq][k] for k in ("transaction_id", "operation_id")), "invalid_message", "message changed its transaction or operation")
                duplicate = seq in seen
                terminalizing = kind == "ack" and msg["request_id"] in receiver.active_requests and msg["status"] != "rejected"
                if not duplicate and pending_transaction is not None:
                    _require(kind == "event" and (step["transaction_id"], step["operation_id"]) == pending_transaction, "invalid_message", "terminal ACK and result events belong to different transactions")
                if kind == "request" and not duplicate:
                    starts[msg["request_id"]] = step.get("group_start", now)
                    _require(starts[msg["request_id"]] >= now, "invalid_message", "group clock starts before request recording")
                if kind == "ack" and not duplicate and msg["request_id"] in starts:
                    request = receiver.requests[msg["request_id"]]
                    if "decision_group_id" not in request:
                        grace = receiver.welcome["rules"]["time_control"]["grace_ms"]
                        deadline = grace + request["timeout_ms"] + request["time_bank_ms"]
                        action = first_actions.get(msg["request_id"])
                        if msg["status"] == "accepted":
                            _require(action is not None and action[0] == msg["action_id"], "invalid_message", "ACK differs from captured choice")
                            elapsed = action[1] - starts[msg["request_id"]]
                            _require(0 <= elapsed < deadline and msg["elapsed_ms"] == elapsed, "invalid_message", "captured action deadline or elapsed time differs")
                        elif msg["status"] == "defaulted" and action is None:
                            _require(now >= starts[msg["request_id"]] + deadline and msg["elapsed_ms"] == deadline, "invalid_message", "timeout precedes its original deadline")
                outcome = receiver.receive(raw)
                _require(outcome in {"applied", "duplicate"}, "invalid_message", "capture contains an unapplied host message")
                if not duplicate:
                    if terminalizing:
                        pending_transaction = (step["transaction_id"], step["operation_id"])
                    elif kind == "event" and msg["event"]["type"] in {"dahai", "tsumo", "end_kyoku", "end_game"}:
                        pending_transaction = None
                seen.add(seq)
        _require(receiver is not None and set(ledger) == seen, "invalid_message", "capture omits ledger messages")
        _require(trace.get("allow_open_requests", False) or (not receiver.active_requests and not receiver.awaiting_request
                 and not receiver.expected_effects and not receiver.unadopted_reaction),
                 "invalid_message", "capture ends with an unresolved decision or missing action effects")
        for lifecycle in trace.get("request_lifecycles", []):
            semantic_lifecycle_trace(lifecycle)
            _require(lifecycle["grace_ms"] == receiver.welcome["rules"]["time_control"]["grace_ms"] and all(lifecycle[k] == receiver.welcome["rules"][k] for k in ("ron_policy", "invalid_action_policy")), "invalid_message", "lifecycle rules differ from negotiation")
            bound = [r for r in lifecycle["requests"] if r["request_id"] in receiver.requests]
            _require(bool(bound), "invalid_message", "lifecycle has no request in this capture")
            for request in bound:
                actual_request = receiver.requests[request["request_id"]]
                _require(all(_json_equal(actual_request.get(k), value) for k, value in request.items()), "invalid_message", "lifecycle request differs from captured wire")
                rid = request["request_id"]
                submitted = [(s["action_id"], s["at_us"] // 1000) for s in lifecycle["steps"] if s["op"] == "submit" and s["request_id"] == rid]
                captured = [(s["message"]["action_id"], max(0, s["at_ms"] - starts[rid])) for s in trace["messages"] if s["direction"] == "in" and s["message"].get("kind") == "action" and s["message"]["request_id"] == rid]
                _require(submitted == captured, "invalid_message", "lifecycle submissions differ from captured actions")
                captured_acks = [{k:step["message"][k] for k in ("kind", "action_id", "status", "elapsed_ms", "time_bank_ms")} for step in trace["messages"] if step["message"].get("kind") == "ack" and step["message"].get("request_id") == request["request_id"]]
                _require(_json_equal(captured_acks, lifecycle["expected"]["messages"][request["request_id"]]), "invalid_message", "lifecycle ACK differs from captured wire")
                cause = ledger[request["caused_by_seq"]]["message"]["event"]
                for member in lifecycle["requests"]:
                    _check_decision_cause(member, cause)
        for record in trace.get("visibility", []):
            for target in record["projections"]:
                projection = deepcopy(record["source"])
                if projection["kind"] == "event":
                    event = projection["event"]
                    full = target["mode"] == "replay" and target["view"] == "full"
                    seat = target["seat"] if target["mode"] == "play" else target["view"].get("seat") if isinstance(target["view"], dict) else None
                    if event["type"] == "tsumo" and not full and event["actor"] != seat:
                        event["pai"] = None
                    if event["type"] == "start_kyoku":
                        event["hands"] = [h if full or actor == seat else {"count": len(h["tiles"])} for actor, h in enumerate(event["hands"])]
                    _check_event_visibility(target["message"]["event"], target["mode"], target["view"], target["seat"])
                elif projection["kind"] == "snapshot":
                    state = projection["state"]
                    state.update(mode=target["mode"], view=target["view"], seat=target["seat"])
                    full = target["mode"] == "replay" and target["view"] == "full"
                    seat = target["seat"] if target["mode"] == "play" else target["view"].get("seat") if isinstance(target["view"], dict) else None
                    if target["mode"] != "play":
                        state.pop("pending_requests", None)
                        state.pop("time_bank_ms", None)
                    if target["mode"] != "replay":
                        state.pop("original_seq", None)
                    kyoku = state["kyoku"]
                    if kyoku is not None:
                        kyoku["hands"] = [h if full or actor == seat else {"count": len(h["tiles"]) if "tiles" in h else h["count"]} for actor,h in enumerate(kyoku["hands"])]
                        event = kyoku["turn"]["last_event"]
                        if event["type"] == "tsumo" and not full and event["actor"] != seat:
                            event["pai"] = None
                        if target["mode"] != "play":
                            kyoku.pop("self_state", None)
                    _check_snapshot(target["message"], rules=receiver.welcome["rules"] if receiver is not None else None)
                _require(_json_equal(projection, target["message"]), "invalid_message", "visibility projection differs from captured source")
    except SessionError as error:
        raise ArtifactError(error.code, str(error)) from error


def check_vectors(schemas: SchemaSet, manifest: Dict[str, Any]) -> int:
    vectors = strict_load(ROOT / manifest["vectors"])
    root_schema = schema_by_id(schemas, "urn:yamai:schema:yrc-0003:1.0-draft.9:message")
    checked = 0
    session_types = {"negotiation", "resume_tokens", "resource_clock", "wire_direction", "input_error", "replay_plan", "receiver", "multi_session", "extension_message"}
    error_codes = {item["id"] for item in strict_load(ROOT / f"registry/yrc-0003/{PROTOCOL}/registry.json")["error_codes"]}
    for entry in manifest["cases"]:
        case_id = entry["id"]
        case = vectors[case_id]
        if case.get("negative_expect") != entry["expect_negative"]:
            raise ArtifactError("vector_error", f"{case_id}: manifest negative expectation mismatch")
        if case.get("negative_expect") not in error_codes:
            raise ArtifactError("vector_error", f"{case_id}: unsupported negative expectation")
        if "negative_profile_hash" in case and case.get("negative", {}).get("profile_hash") != case["negative_profile_hash"]:
            raise ArtifactError("vector_error", f"{case_id}: negative profile hash metadata mismatch")
        if "negative_capability" in case and case.get("negative", {}).get("capabilities") != case["negative_capability"]:
            raise ArtifactError("vector_error", f"{case_id}: negative capability metadata mismatch")

        positive_checked = False
        positive = case.get("positive")
        if isinstance(positive, dict) and "kind" in positive:
            positive_checked = True
            schemas.validate(positive, root_schema, case_id + ".positive")
            if positive.get("kind") == "join" and positive.get("profile_hash") != manifest["profile_hash"]:
                raise ArtifactError("vector_error", f"{case_id}: positive join profile_hash mismatch")
            semantic_message(positive, case_id, manifest["profile_hash"])
        elif isinstance(positive, dict) and "trace" in positive:
            positive_checked = True
            trace = positive["trace"]
            trace_type = trace.get("trace_type")
            if trace_type == "event_order":
                semantic_event_trace(trace)
            elif trace_type == "resource":
                semantic_resource_trace(trace)
            elif trace_type == "transport":
                semantic_transport_trace(trace)
            elif trace_type == "replay":
                semantic_replay_trace(trace)
            elif trace_type == "request_state":
                semantic_request_trace(trace)
            elif trace_type == "scoring":
                semantic_scoring_trace(trace)
            elif trace_type == "welcome":
                semantic_welcome_trace(trace)
            elif trace_type == "state_machine":
                semantic_state_trace(trace)
            elif trace_type == "noten":
                semantic_noten_trace(trace)
            elif trace_type == "ack":
                semantic_ack_trace(trace)
            elif trace_type == "composite":
                semantic_composite_trace(trace)
            elif trace_type == "request_lifecycle":
                semantic_lifecycle_trace(trace)
            elif trace_type == "game_contract":
                semantic_game_trace(trace)
            elif trace_type == "session":
                semantic_ledger_trace(trace, manifest["profile_hash"])
            elif trace_type in session_types:
                semantic_session_trace(trace, manifest["profile_hash"])
            else:
                semantic_score_trace(trace)
        if not positive_checked:
            raise ArtifactError("vector_error", f"{case_id}: missing positive payload")
        for index, message in enumerate(case.get("positive_messages", [])):
            schemas.validate(message, root_schema, f"{case_id}.positive_messages[{index}]")
            semantic_message(message, case_id, manifest["profile_hash"])
        for index, message in enumerate(case.get("negative_messages", [])):
            caught = None
            try:
                schemas.validate(message, root_schema, f"{case_id}.negative_messages[{index}]")
                semantic_message(message, case_id, manifest["profile_hash"])
            except ArtifactError as exc:
                caught = exc.code
            if caught != case["negative_expect"]:
                raise ArtifactError("vector_error", f"{case_id}.negative_messages[{index}]: expected {case['negative_expect']}, got {caught}")
        for index, message in enumerate(case.get("negative_variants", [])):
            caught = None
            try:
                schemas.validate(message, root_schema, f"{case_id}.negative_variants[{index}]")
                semantic_message(message, case_id, manifest["profile_hash"])
            except ArtifactError as exc:
                caught = exc.code
            if caught != case["negative_expect"]:
                raise ArtifactError("vector_error", f"{case_id}.negative_variants[{index}]: expected {case['negative_expect']}, got {caught}")
        if "websocket_positive" in case:
            trace = {"trace_type": "transport", "transport": "websocket", **case["websocket_positive"]}
            semantic_transport_trace(trace)
        if "websocket_negative" in case:
            caught = None
            try:
                trace = {"trace_type": "transport", "transport": "websocket", **case["websocket_negative"]}
                semantic_transport_trace(trace)
            except ArtifactError as exc:
                caught = exc.code
            expected_websocket = case.get("websocket_negative_expect", "unsupported_frame")
            if caught != expected_websocket:
                raise ArtifactError("vector_error", f"{case_id}.websocket_negative: expected {expected_websocket}, got {caught}")
        if "unicode_negative" in case:
            raw = ('{"value":"' + case["unicode_negative"] + '"}').encode("utf-8")
            try:
                strict_load_bytes(raw, source=case_id + ".unicode_negative")
            except ArtifactError as exc:
                if exc.code != "invalid_json":
                    raise ArtifactError("vector_error", f"{case_id}: expected invalid_json for unicode negative, got {exc.code}")
            else:
                raise ArtifactError("vector_error", f"{case_id}: unicode negative unexpectedly accepted")
        if "number_negative" in case:
            raw = ('{"value":' + case["number_negative"] + '}').encode("ascii")
            try:
                strict_load_bytes(raw, source=case_id + ".number_negative")
            except ArtifactError as exc:
                if exc.code != "invalid_json":
                    raise ArtifactError("vector_error", f"{case_id}: expected invalid_json for number negative, got {exc.code}")
            else:
                raise ArtifactError("vector_error", f"{case_id}: number negative unexpectedly accepted")
        if "negative_raw" in case:
            try:
                strict_load_bytes(case["negative_raw"].encode("utf-8"), source=case_id + ".negative_raw")
            except ArtifactError as exc:
                if exc.code != case["negative_expect"]:
                    raise ArtifactError("vector_error", f"{case_id}: expected {case['negative_expect']}, got {exc.code}")
            else:
                raise ArtifactError("vector_error", f"{case_id}: negative raw unexpectedly accepted")
        elif isinstance(case.get("negative"), dict) and "kind" in case["negative"]:
            negative = case["negative"]
            caught = None
            try:
                schemas.validate(negative, root_schema, case_id + ".negative")
                semantic_message(negative, case_id, manifest["profile_hash"])
                context = case.get("negative_context")
                if isinstance(context, dict) and context.get("resolved_status") in TERMINAL_ACK_STATUSES:
                    raise ArtifactError("request_conflict", "different action for a resolved request")
                if isinstance(context, dict) and context.get("resume_available") is False:
                    raise ArtifactError("resume_unavailable", "requested resume state is not retained")
            except ArtifactError as exc:
                caught = exc.code
            if caught is None:
                raise ArtifactError("vector_error", f"{case_id}: negative unexpectedly accepted")
            if caught != case["negative_expect"]:
                raise ArtifactError("vector_error", f"{case_id}: expected {case['negative_expect']}, got {caught}")
        elif isinstance(case.get("negative"), dict) and "trace" in case["negative"]:
            caught = None
            try:
                trace = case["negative"]["trace"]
                if trace.get("trace_type") == "event_order":
                    semantic_event_trace(trace)
                elif trace.get("trace_type") == "resource":
                    semantic_resource_trace(trace)
                elif trace.get("trace_type") == "transport":
                    semantic_transport_trace(trace)
                elif trace.get("trace_type") == "replay":
                    semantic_replay_trace(trace)
                elif trace.get("trace_type") == "request_state":
                    semantic_request_trace(trace)
                elif trace.get("trace_type") == "scoring":
                    semantic_scoring_trace(trace)
                elif trace.get("trace_type") == "welcome":
                    semantic_welcome_trace(trace)
                elif trace.get("trace_type") == "state_machine":
                    semantic_state_trace(trace)
                elif trace.get("trace_type") == "noten":
                    semantic_noten_trace(trace)
                elif trace.get("trace_type") == "ack":
                    semantic_ack_trace(trace)
                elif trace.get("trace_type") == "composite":
                    semantic_composite_trace(trace)
                elif trace.get("trace_type") == "request_lifecycle":
                    semantic_lifecycle_trace(trace)
                elif trace.get("trace_type") == "game_contract":
                    semantic_game_trace(trace)
                elif trace.get("trace_type") == "session":
                    semantic_ledger_trace(trace, manifest["profile_hash"])
                elif trace.get("trace_type") in session_types:
                    semantic_session_trace(trace, manifest["profile_hash"])
                else:
                    semantic_score_trace(trace)
            except ArtifactError as exc:
                caught = exc.code
            if caught != case["negative_expect"]:
                raise ArtifactError("vector_error", f"{case_id}: expected {case['negative_expect']}, got {caught}")
        elif "negative_variants" in case or "negative_messages" in case:
            # The extra negative payloads above are the complete negative set
            # for this case; no legacy single negative member is required.
            pass
        else:
            negative = case.get("negative", {})
            if case_id == "V06_transport_frame":
                if negative.get("raw") != "\n":
                    raise ArtifactError("vector_error", "V06 blank-frame fixture changed")
            elif case_id == "V07_backpressure":
                if negative.get("peer_reads") is not False:
                    raise ArtifactError("vector_error", "V07 slow peer fixture changed")
            else:
                raise ArtifactError("vector_error", f"{case_id}: missing negative payload")
        checked += 1
    if checked != len(manifest["cases"]):
        raise ArtifactError("vector_error", f"checked {checked} vectors, expected {len(manifest['cases'])}")
    return checked


def check_scoring(schemas: SchemaSet, rules_registry: Mapping[str, Any]) -> Tuple[int, int, int]:
    data = strict_load(ROOT / "test-vectors/yrc-0005/1.0-draft.7/scoring.json")
    scoring_schema_id = "urn:yamai:schema:yrc-0005:1.0-draft.7:scoring-vectors"
    if scoring_schema_id in schemas.schemas:
        schemas.validate(data, schema_by_id(schemas, scoring_schema_id))
    vector_items = data.get("vectors", [])
    vector_ids = [item.get("id") for item in vector_items if isinstance(item, dict)]
    if len(vector_ids) != len(set(vector_ids)):
        raise ArtifactError("scoring_error", "duplicate scoring vector id")
    vectors = {item["id"]: item for item in vector_items}
    expected = {
        "child_30fu_3han_ron": (30 * (2 ** 5), 3900),
        "dealer_40fu_3han_ron": (40 * (2 ** 5), 7700),
        "child_30fu_2han_tsumo": (30 * (2 ** 4), 2000),
    }
    for key, (basic, points) in expected.items():
        if key not in vectors or vectors[key]["basic_points"] != basic or vectors[key]["hand_points"] != points:
            raise ArtifactError("scoring_error", f"arithmetic vector mismatch: {key}")
    required_vectors = set(rules_registry.get("required_test_vectors", []))
    if not required_vectors.issubset(vectors):
        raise ArtifactError("scoring_error", "incomplete scoring vector set")
    fixtures = data.get("fixtures")
    if not isinstance(fixtures, list) or not fixtures:
        raise ArtifactError("scoring_error", "scoring fixture set is empty")
    fixture_ids = [fixture.get("id") for fixture in fixtures if isinstance(fixture, dict)]
    if len(fixture_ids) != len(fixtures) or any(not isinstance(item, str) or not item for item in fixture_ids):
        raise ArtifactError("scoring_error", "scoring fixture id is missing")
    if len(fixture_ids) != len(set(fixture_ids)):
        raise ArtifactError("scoring_error", "duplicate scoring fixture id")
    base_rules = data.get("rules", {})
    _require(isinstance(base_rules, Mapping), "scoring_error", "scoring rules are not an object")
    for fixture in fixtures:
        _check_scoring_fixture_semantics(fixture, base_rules, schemas)
    expected_fixture_count = rules_registry.get("scoring_fixture_count")
    if isinstance(expected_fixture_count, int) and len(fixtures) != expected_fixture_count:
        raise ArtifactError("scoring_error", "scoring fixture count does not match registry")
    negatives = data["negative_fixtures"]
    negative_ids = [fixture["id"] for fixture in negatives]
    _require(len(negative_ids) == len(set(negative_ids)) and not set(negative_ids).intersection(fixture_ids), "scoring_error", "duplicate negative scoring fixture id")
    _require(len(negatives) == rules_registry.get("scoring_negative_fixture_count"), "scoring_error", "negative scoring fixture count differs from registry")
    for fixture in negatives:
        caught = None
        try:
            schemas.validate(fixture["input"], {"$ref": scoring_schema_id + "#/$defs/input"})
            schemas.validate(fixture["state"], {"$ref": scoring_schema_id + "#/$defs/state"})
            schemas.validate(fixture["rule_overrides"], {"$ref": scoring_schema_id + "#/$defs/rule_overrides"})
            effective = {**base_rules, **fixture["rule_overrides"]}
            schemas.validate(effective, {"$ref": "urn:yamai:schema:yrc-0005:1.0-draft.7:riichi-4p-rules"})
            calculate_scoring_fixture(fixture, base_rules)
        except (ScoringError, ArtifactError) as exc:
            caught = exc.code
        _require(caught == fixture["expected_error"], "scoring_error", f"{fixture['id']}: expected {fixture['expected_error']}, got {caught}")

    coverage = rules_registry.get("fixture_coverage", {})
    observed_yakus = set()
    fixtures_by_id = {fixture["id"]: fixture for fixture in fixtures}
    for summary in vector_items:
        sid = summary["id"]
        rules = {**base_rules, **summary.get("rule_overrides", {})}
        schemas.validate(rules, {"$ref": "urn:yamai:schema:yrc-0005:1.0-draft.7:riichi-4p-rules"})
        if all(key in summary for key in ("fu", "han", "dealer", "tsumo")):
            basic = basic_points(summary["fu"], summary["han"], 0, rules)
            actor = 0 if summary["dealer"] else 1
            target = actor if summary["tsumo"] else 1 - actor
            points = sum(normal_payments(basic, actor, target, 0).values())
            _require((basic, points) == (summary["basic_points"], summary["hand_points"]), "scoring_error", f"{sid}: arithmetic summary differs")
            continue
        if "yakuman_value" in summary:
            _require(basic_points(0, 0, summary["yakuman_value"], rules) == summary["basic_points"], "scoring_error", f"{sid}: yakuman summary differs")
            continue
        links = summary.get("fixture_ids", [])
        _require(bool(links) and all(link in fixtures_by_id for link in links), "scoring_error", f"{sid}: summary has no executable fixture")
        linked = [fixtures_by_id[link] for link in links]
        for fixture in linked:
            effective = {**base_rules, **fixture["rule_overrides"]}
            expected = fixture["expected"]
            for field in ("honba", "kyotaku"):
                if field in summary:
                    _require(summary[field] == effective["multiple_ron_settlement"][field], "scoring_error", f"{sid}: linked settlement policy differs")
            if "min_winners" in summary:
                _require(len(expected.get("wins", [])) >= summary["min_winners"], "scoring_error", f"{sid}: linked fixture lacks multiple winners")
            if "pao_ron" in summary:
                _require(effective["pao"]["ron"] == summary["pao_ron"] and any(win["pao"] for win in expected.get("wins", [])), "scoring_error", f"{sid}: linked pao policy differs")
            if "total_points" in summary:
                value = effective["chombo"]["penalty_points"] if fixture["input"]["type"] == "penalty" else effective["noten_payment"]["total_points"]
                _require(summary["total_points"] == value, "scoring_error", f"{sid}: linked point amount differs")
            if "conserves_points" in summary:
                _require(summary["conserves_points"] is True and sum(expected["deltas"]) + (expected["kyotaku"] - fixture["state"]["kyotaku"]) * effective["riichi_stick_value"] == 0, "scoring_error", f"{sid}: conservation summary differs")
        if "cases" in summary:
            _require(all(isinstance(f["expected"].get("tenpai"), list) for f in linked), "scoring_error", f"{sid}: linked draw does not classify tenpai")
            _require(summary["cases"] == sorted(sum(f["expected"]["tenpai"]) for f in linked), "scoring_error", f"{sid}: tenpai coverage summary differs")
        if "bonus_ids" in summary:
            actual = {b["id"] for f in linked for w in f["expected"].get("wins", []) for b in w["bonuses"]}
            _require(len(summary["bonus_ids"]) == len(set(summary["bonus_ids"])) and set(summary["bonus_ids"]) == actual, "scoring_error", f"{sid}: bonus coverage summary differs")
    for fixture in fixtures:
        expected = fixture.get("expected", {})
        for win in expected.get("wins", []) if isinstance(expected, dict) else []:
            if isinstance(win, dict):
                for yaku in win.get("yakus", []):
                    if isinstance(yaku, dict) and isinstance(yaku.get("id"), str):
                        observed_yakus.add(yaku["id"])
    for key in ("normal_yaku_ids", "yakuman_ids"):
        required_ids = coverage.get(key, [])
        if not isinstance(required_ids, list) or not set(required_ids).issubset(observed_yakus):
            raise ArtifactError("scoring_error", f"fixture coverage is incomplete for {key}")

    category_expectations = {
        "boundaries": {"yaku_pinfu": "fu", "yaku_chiitoitsu": "fu", "boundary_open_ron_20_to_30": "fu", "boundary_kiriage_4han_30fu": "fu"},
        "settlement": {"settlement_multiple_ron": "settlement", "settlement_pao_split": "settlement", "noten_0": "noten", "noten_1": "noten", "noten_2": "noten", "noten_3": "noten", "noten_4": "noten"},
        "bonus": {"bonus_red_ura_dora": "bonus"},
    }
    for coverage_key, expected_categories in category_expectations.items():
        for fixture_id in coverage.get(coverage_key, []):
            fixture = fixtures_by_id.get(fixture_id)
            expected_category = expected_categories.get(fixture_id)
            if fixture is None or (expected_category is not None and fixture.get("category") != expected_category):
                raise ArtifactError("scoring_error", f"fixture coverage is incomplete for {coverage_key}: {fixture_id}")
    return len(vectors), len(fixtures), len(negatives)


def check_document_examples(schemas: SchemaSet, expected_hash: str) -> int:
    count = 0
    urn = f"urn:yamai:schema:yrc-0003:{PROTOCOL}:"
    for path in (ROOT / "docs/yamai-protocol.md",):
        for match in re.finditer(r"```json\n(.*?)\n```", path.read_text(), re.S):
            line = path.read_text()[:match.start()].count("\n") + 1
            context = f"{path.name}:{line}"
            obj = strict_load_bytes(match[1].encode("utf-8"), source=context)
            _require(isinstance(obj, dict), "vector_error", f"{context}: example is not an object")
            if "kind" in obj:
                schemas.validate(obj, {"$ref": urn + "message"}, context)
                semantic_message(obj, context, expected_hash)
            elif "type" in obj:
                schemas.validate(obj, {"$ref": urn + "event#/properties/event"}, context)
                semantic_message({"kind": "event", "event": obj}, context)
            elif "action_id" in obj:
                schemas.validate(obj, {"$ref": urn + "action#/$defs/actionCandidate"}, context)
                _check_action_object(obj["action"])
            elif set(obj) == {"hands"}:
                schemas.validate(obj["hands"], {"$ref": urn + "common#/$defs/hands"}, context)
            else:
                raise ArtifactError("vector_error", f"{context}: unclassified JSON example")
            count += 1
    _require(count > 0, "vector_error", "normative JSON examples are missing")
    return count


def main() -> int:
    try:
        load_all_json()
        schemas = SchemaSet()
        schemas.check_refs()
        schemas.check_keyword_support()
        root = schema_by_id(schemas, "urn:yamai:schema:yrc-0003:1.0-draft.9:message")
        union_refs = root.get("oneOf", [])
        expected_kinds = {"hello", "join", "welcome", "event", "request", "action", "ack", "error", "snapshot"}
        if len(union_refs) != 9:
            raise ArtifactError("schema_error", "message union must contain nine branches")
        expected_ids = {"urn:yamai:schema:yrc-0003:1.0-draft.9:" + x for x in expected_kinds}
        actual_ids = {item.get("$ref", "").split("#", 1)[0] for item in union_refs}
        if actual_ids != expected_ids:
            raise ArtifactError("schema_error", "message union branches mismatch")
        p, r = check_registry(schemas)
        manifest = check_manifest(schemas, p, r)
        check_release_manifest(manifest, p, r)
        vector_count = check_vectors(schemas, manifest)
        scoring_count, scoring_fixture_count, scoring_negative_count = check_scoring(schemas, r)
        example_count = check_document_examples(schemas, manifest["profile_hash"])
        print(f"OK: schemas={len(schemas.schemas)} vectors={vector_count} scoring_vectors={scoring_count} scoring_fixtures={scoring_fixture_count} scoring_negative_fixtures={scoring_negative_count} json_examples={example_count} profile_hash={manifest['profile_hash']}")
        return 0
    except ArtifactError as exc:
        print(f"FAIL [{exc.code}]: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # make CI failures actionable instead of a traceback
        print(f"FAIL [internal]: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
