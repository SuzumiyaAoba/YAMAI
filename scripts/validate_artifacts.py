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
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_ROOT = ROOT / "schemas"
PROTOCOL = "1.0-draft.5"
PROFILE = "riichi-4p"
PROFILE_REVISION = "1.0-draft.3"
YRC0003_SCHEMA_DIR = SCHEMA_ROOT / "yrc-0003" / PROTOCOL
RELEASE_MANIFEST_PATH = ROOT / "release-manifest.json"
MAX_INT = 9007199254740991
ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")
EXTENSION_FIELD_RE = re.compile(r"^x_[A-Za-z0-9][A-Za-z0-9_]{0,63}$")
ERROR_CODE_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
PROFILE_HASH_INPUTS = [
    "schemas/yrc-0003/1.0-draft.5/profile/riichi-4p.schema.json",
    "schemas/yrc-0005/1.0-draft.3/riichi-4p-rules.schema.json",
    "schemas/yrc-0005/1.0-draft.3/scoring-vectors.schema.json",
    "registry/yrc-0003/1.0-draft.5/registry.json",
    "registry/yrc-0005/1.0-draft.3/registry.json",
    "test-vectors/yrc-0003/1.0-draft.5/vectors.json",
    "test-vectors/yrc-0005/1.0-draft.3/scoring.json",
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


def _walk_json(value: Any, depth: int = 0, path: str = "$") -> None:
    if depth > 64:
        raise ArtifactError("resource_limit", "JSON depth exceeds 64 at " + path)
    if isinstance(value, bool) or value is None:
        return
    if isinstance(value, int):
        if value < -MAX_INT or value > MAX_INT:
            raise ArtifactError("invalid_json", "integer outside IEEE-754 safe range at " + path)
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ArtifactError("invalid_json", "non-finite number at " + path)
        return
    if isinstance(value, str):
        if any(0xD800 <= ord(ch) <= 0xDFFF for ch in value):
            raise ArtifactError("invalid_json", "lone surrogate at " + path)
        return
    if isinstance(value, list):
        for i, item in enumerate(value):
            _walk_json(item, depth + 1, f"{path}[{i}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            _walk_json(key, depth + 1, f"{path}.{key}")
            _walk_json(item, depth + 1, f"{path}.{key}")
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
    try:
        value = json.loads(
            text,
            object_pairs_hook=_pairs,
            parse_constant=_reject_constant,
        )
    except ArtifactError:
        raise
    except (json.JSONDecodeError, RecursionError, ValueError) as exc:
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
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if name == "boolean":
        return isinstance(value, bool)
    if name == "null":
        return value is None
    return True


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
        if isinstance(value, dict):
            for key, item in value.items():
                if key in UNSUPPORTED_DRAFT202012_KEYWORDS:
                    raise ArtifactError(
                        "schema_error",
                        f"unsupported Draft 2020-12 keyword {key} in {owner}{path}",
                    )
                self._check_keyword_support(item, owner, f"{path}.{key}")
        elif isinstance(value, list):
            for index, item in enumerate(value):
                self._check_keyword_support(item, owner, f"{path}[{index}]")

    def _check_refs(self, value: Any, owner: str) -> None:
        if isinstance(value, dict):
            if "$ref" in value:
                ref = value["$ref"]
                if not isinstance(ref, str):
                    raise ArtifactError("schema_error", f"non-string $ref in {owner}")
                self.resolve(ref, self.schemas[owner])
            for item in value.values():
                self._check_refs(item, owner)
        elif isinstance(value, list):
            for item in value:
                self._check_refs(item, owner)

    def validate(self, value: Any, schema: Any, path: str = "$") -> None:
        self._validate(value, schema, path, schema)

    def _validate(self, value: Any, schema: Any, path: str, base: Mapping[str, Any]) -> None:
        if not isinstance(schema, dict):
            raise ArtifactError("schema_error", "schema is not an object")
        if "$ref" in schema:
            target, target_base = self.resolve(schema["$ref"], base)
            self._validate(value, target, path, target_base)
        if "allOf" in schema:
            for sub in schema["allOf"]:
                self._validate(value, sub, path, base)
        if "anyOf" in schema:
            errors = []
            for sub in schema["anyOf"]:
                try:
                    self._validate(value, sub, path, base)
                    return
                except ArtifactError as exc:
                    errors.append(exc)
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
        if "const" in schema and value != schema["const"]:
            raise ArtifactError("invalid_message", f"const mismatch at {path}")
        if "enum" in schema and value not in schema["enum"]:
            raise ArtifactError("invalid_message", f"enum mismatch at {path}")
        typ = schema.get("type")
        if typ is not None:
            types = typ if isinstance(typ, list) else [typ]
            if not any(_json_type(value, item) for item in types):
                raise ArtifactError("invalid_message", f"type mismatch at {path}")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
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
                if len({json.dumps(x, sort_keys=True, ensure_ascii=False) for x in value}) != len(value):
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
                if key in props:
                    self._validate(item, props[key], f"{path}.{key}", base)
                else:
                    matched = False
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
    p = strict_load(ROOT / "registry/yrc-0003/1.0-draft.5/registry.json")
    r = strict_load(ROOT / "registry/yrc-0005/1.0-draft.3/registry.json")
    if p["protocol_version"] != PROTOCOL or r["protocol_version"] != PROTOCOL:
        raise ArtifactError("registry_error", "registry protocol version mismatch")
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

    event_schema = schema_by_id(schemas, "urn:yamai:schema:yrc-0003:1.0-draft.5:event")
    event_union = event_schema.get("properties", {}).get("event", {}).get("oneOf", [])
    event_values = set()
    for branch in event_union:
        event_values.update(_schema_property_values(branch, "type"))
    if set(p["event_types"]) != event_values:
        raise ArtifactError("registry_error", "event type registry does not match event schema")

    action_schema = schema_by_id(schemas, "urn:yamai:schema:yrc-0003:1.0-draft.5:action")
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

    ack_schema = schema_by_id(schemas, "urn:yamai:schema:yrc-0003:1.0-draft.5:ack")
    ack_statuses = set(_schema_property_values(ack_schema, "status"))
    if set(p["ack_statuses"]) != ack_statuses:
        raise ArtifactError("registry_error", "ack status registry does not match ack schema")

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
        for path in (SCHEMA_ROOT / "yrc-0005" / "1.0-draft.3").glob("*.json")
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

    The release artifacts intentionally contain only JSON strings, integers,
    booleans, nulls, arrays, and objects.  Python's compact sorted encoding is
    therefore byte-identical to JCS for this subset; rejecting non-finite
    values keeps the invariant explicit if a future artifact adds numbers.
    """
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def profile_hash(protocol_registry: Dict[str, Any], rules_registry: Dict[str, Any]) -> str:
    manifest = strict_load(ROOT / "test-vectors/yrc-0003/1.0-draft.5/manifest.json")
    vectors = strict_load(ROOT / manifest["vectors"])
    scoring = strict_load(ROOT / manifest["scoring_vectors"])
    profile_schema = strict_load(ROOT / "schemas/yrc-0003/1.0-draft.5/profile/riichi-4p.schema.json")
    rules_schema = strict_load(ROOT / "schemas/yrc-0005/1.0-draft.3/riichi-4p-rules.schema.json")
    scoring_vectors_schema = strict_load(ROOT / "schemas/yrc-0005/1.0-draft.3/scoring-vectors.schema.json")
    # Normalize mutable hash fields before constructing the canonical artifact
    # projection.  The registry hash itself is omitted from that projection,
    # so writing the resulting digest back cannot create a hash cycle.
    zero_hash = "sha256:" + "0" * 64

    def normalize_profile_hashes(value: Any) -> Any:
        if isinstance(value, dict):
            return {key: (zero_hash if key == "profile_hash" else normalize_profile_hashes(item)) for key, item in value.items()}
        if isinstance(value, list):
            return [normalize_profile_hashes(item) for item in value]
        return value

    pcopy = json.loads(json.dumps(protocol_registry))
    pcopy["profiles"][0]["hash"] = zero_hash
    pcopy["profiles"][0].pop("hash", None)
    vectors = normalize_profile_hashes(vectors)
    payload = {
        "profile_schema": profile_schema,
        "rules_schema": rules_schema,
        "scoring_vectors_schema": scoring_vectors_schema,
        "yrc0003_registry": pcopy,
        "yrc0005_registry": rules_registry,
        "official_vectors": vectors,
        "scoring_vectors": scoring,
    }
    canonical_bytes = canonical(payload)
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
    path = ROOT / "test-vectors/yrc-0003/1.0-draft.5/manifest.json"
    manifest = strict_load(path)
    schema = schema_by_id(schemas, "urn:yamai:schema:yrc-0003:1.0-draft.5:vector-manifest")
    schemas.validate(manifest, schema)
    expected_registries = [
        "registry/yrc-0003/1.0-draft.5/registry.json",
        "registry/yrc-0005/1.0-draft.3/registry.json",
    ]
    if manifest["registry"] != expected_registries:
        raise ArtifactError("manifest_error", "vector manifest registry set mismatch")
    if manifest["schema_root"] != "schemas/yrc-0003/1.0-draft.5/message.schema.json":
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
        "registry/yrc-0003/1.0-draft.5/registry.json",
        "registry/yrc-0005/1.0-draft.3/registry.json",
    ]
    if release.get("registries") != expected_registries or manifest["registry"] != expected_registries:
        raise ArtifactError("release_error", "release registry set mismatch")
    _repo_file_list(release.get("registries"), "registries")

    expected_vectors = [
        "test-vectors/yrc-0003/1.0-draft.5/manifest.json",
        manifest["vectors"],
        manifest["scoring_vectors"],
    ]
    if release.get("test_vectors") != expected_vectors:
        raise ArtifactError("release_error", "release test vector set mismatch")
    _repo_file_list(release.get("test_vectors"), "test_vectors")

    expected_schemas = {
        *p["schema_files"],
        "schemas/yrc-0003/1.0-draft.5/vector-manifest.schema.json",
        *r["schema_files"],
    }
    release_schemas = release.get("schemas")
    if not isinstance(release_schemas, list) or len(release_schemas) != len(set(release_schemas)) or set(release_schemas) != expected_schemas:
        raise ArtifactError("release_error", "release schema file set mismatch")
    _repo_file_list(release_schemas, "schemas")

    scope = release.get("profile_hash_scope")
    if not isinstance(scope, Mapping) or scope.get("canonicalization") != manifest["profile_hash_canonicalization"] or scope.get("inputs") != manifest["profile_hash_inputs"]:
        raise ArtifactError("release_error", "release profile hash scope mismatch")
    if scope.get("protocol_version_pins") != [manifest["schema_root"]]:
        raise ArtifactError("release_error", "release protocol version pin mismatch")

    validator = release.get("validator")
    if not isinstance(validator, Mapping) or validator.get("path") != "scripts/validate_artifacts.py" or validator.get("command") != "rtk python3 scripts/validate_artifacts.py":
        raise ArtifactError("release_error", "release validator metadata mismatch")
    _repo_file(validator.get("path"), "validator.path")

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
        valid_view = view in {"public", "full"}
        if isinstance(view, dict):
            valid_view = (
                "seat" in view
                and all(key == "seat" or EXTENSION_FIELD_RE.fullmatch(key) for key in view)
                and isinstance(view.get("seat"), int)
                and 0 <= view["seat"] <= 3
            )
        _require(valid_view and seat is None, "invalid_message", f"{context}: invalid replay view/seat")


def _check_target(target: Any, *, allowed_types: set[str] = TARGET_TYPES) -> None:
    _require(isinstance(target, dict), "invalid_message", "target must be a tagged object")
    _require(target.get("type") in allowed_types, "invalid_message", "target.type is not allowed for this mode")
    _require(isinstance(target.get("id"), str) and ID_RE.fullmatch(target["id"]), "invalid_message", "target.id is invalid")


def _check_capabilities(capabilities: Any) -> None:
    _require(isinstance(capabilities, dict), "invalid_message", "capabilities must be an object")
    required, optional = capabilities.get("required"), capabilities.get("optional")
    _require(isinstance(required, list) and isinstance(optional, list), "invalid_message", "capability arrays are missing")
    _require(len(set(required)) == len(required) and len(set(optional)) == len(optional), "invalid_message", "capabilities contain duplicates")
    _require(not set(required) & set(optional), "invalid_message", "capability appears in both arrays")
    for value in required + optional:
        _require(isinstance(value, str) and (re.fullmatch(r"[a-z][a-z0-9_]{0,63}", value) or re.fullmatch(r"x-[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", value)), "invalid_message", "capability name is invalid")


def _check_rules(rules: Any) -> None:
    _require(isinstance(rules, dict), "invalid_message", "rules must be an object")
    for key in rules:
        _require(key in KNOWN_RULE_KEYS or EXTENSION_FIELD_RE.fullmatch(key), "invalid_message", f"unknown standard rule key: {key}")


def _check_players(players: Any) -> None:
    _require(isinstance(players, list) and len(players) == 4, "invalid_message", "players must contain four seats")
    seats = [player.get("seat") for player in players if isinstance(player, dict)]
    _require(len(seats) == 4 and set(seats) == {0, 1, 2, 3}, "invalid_message", "players must contain each seat exactly once")


def _check_action_object(action: Any, *, expected_actor: int | None = None) -> None:
    _require(isinstance(action, dict), "invalid_message", "action candidate must be an object")
    kind = action.get("type")
    if kind == "none":
        actor = action.get("actor")
        _require(actor is None or (isinstance(actor, int) and 0 <= actor <= 3), "invalid_message", "none actor is invalid")
        return
    _require(isinstance(action.get("actor"), int) and 0 <= action["actor"] <= 3, "invalid_message", "action actor is invalid")
    if expected_actor is not None:
        _require(action["actor"] == expected_actor, "invalid_message", "action actor does not match request seat")
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


def _check_request(message: Mapping[str, Any], *, grace_ms: int | None = None) -> None:
    request_id = message.get("request_id")
    seat = message.get("seat")
    _require(isinstance(request_id, str) and ID_RE.fullmatch(request_id), "invalid_message", "request_id is invalid")
    _require(isinstance(seat, int) and 0 <= seat <= 3, "invalid_message", "request seat is invalid")
    actions = message.get("legal_actions")
    _require(isinstance(actions, list) and 1 <= len(actions) <= 512, "invalid_message", "legal_actions is not complete")
    action_ids: set[str] = set()
    for candidate in actions:
        _require(isinstance(candidate, dict), "invalid_message", "action candidate is not an object")
        action_id = candidate.get("action_id")
        _require(isinstance(action_id, str) and ID_RE.fullmatch(action_id), "invalid_message", "action_id is invalid")
        _require(action_id not in action_ids, "invalid_message", "action_id is not unique")
        action_ids.add(action_id)
        _check_action_object(candidate.get("action"), expected_actor=seat)
    _require(message.get("default_action_id") in action_ids, "invalid_message", "default_action_id is not a legal action")
    group = message.get("decision_group_id")
    grouped = any(x in message for x in GROUP_FIELDS)
    _require(not grouped or isinstance(group, str), "invalid_message", "decision group fields require an id")
    if group is not None:
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
        _require(isinstance(members, list) and 2 <= len(members) <= 4, "invalid_message", "decision group members are invalid")
        member_ids = []
        member_seats = []
        for member in members:
            _require(isinstance(member, dict) and set(member) == {"request_id", "seat"}, "invalid_message", "decision group member shape is invalid")
            _require(isinstance(member["request_id"], str) and ID_RE.fullmatch(member["request_id"]), "invalid_message", "group request_id is invalid")
            _require(isinstance(member["seat"], int) and 0 <= member["seat"] <= 3, "invalid_message", "group seat is invalid")
            member_ids.append(member["request_id"])
            member_seats.append(member["seat"])
        _require(len(set(member_ids)) == len(member_ids) and len(set(member_seats)) == len(member_seats), "invalid_message", "group members are not unique")
        _require(request_id in member_ids and seat in member_seats, "invalid_message", "request is absent from its group")
        _require(message.get("decision_group_close") == "all_resolved_or_deadline", "invalid_message", "unsupported group close")


def _check_snapshot(message: Mapping[str, Any]) -> None:
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
    if isinstance(state.get("kyoku"), dict):
        phase = state["kyoku"].get("turn", {}).get("phase")
        if phase in {"awaiting_action", "awaiting_responses"}:
            _require(pending_present and len(state.get("pending_requests", [])) > 0, "invalid_message", "active turn requires pending_requests")
        if phase in {"awaiting_draw", "resolving"}:
            _require(not pending_present or len(state.get("pending_requests", [])) == 0, "invalid_message", "draw/resolving phase cannot have pending requests")
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
                    _require(isinstance(tenpai, list) and len(tenpai) == 4, "invalid_message", "fanpai requires a four-seat tenpai array")
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
        if event.get("type") == "end_kyoku":
            result = event.get("result", {})
            if result.get("type") == "hora":
                wins = result.get("wins")
                _require(isinstance(wins, list) and 1 <= len(wins) <= 3, "invalid_message", "hora wins count is invalid")
                actors = []
                for win in wins:
                    actors.append(win.get("actor"))
                    regular_han = sum(item.get("value", 0) for item in win.get("yakus", []) if item.get("unit") == "han")
                    bonus_han = sum(item.get("han", 0) for item in win.get("bonuses", []))
                    yakuman = sum(item.get("value", 0) for item in win.get("yakus", []) if item.get("unit") == "yakuman")
                    if yakuman:
                        _require(win.get("han") == 0 and win.get("bonuses") == [], "invalid_message", "yakuman win must not carry han/bonuses")
                    else:
                        _require(win.get("han") == regular_han + bonus_han, "invalid_message", "win han does not match yaku/bonus sum")
                _require(actors == sorted(actors) and len(set(actors)) == len(actors), "invalid_message", "wins must be sorted by unique actor")
                win_deltas = [sum(win.get("deltas", [0, 0, 0, 0])[i] for win in wins) for i in range(4)]
                _require(result.get("wins") and event.get("deltas") == win_deltas, "invalid_message", "hora deltas do not match win deltas")
            if result.get("type") == "penalty":
                payments = result.get("penalty", {}).get("payments", [])
                delta = [0, 0, 0, 0]
                for payment in payments:
                    _require(payment.get("from") == result.get("offender") and payment.get("to") != payment.get("from"), "invalid_message", "penalty payment endpoints are invalid")
                    points = payment.get("points", 0)
                    delta[payment["from"]] -= points
                    delta[payment["to"]] += points
                _require(event.get("deltas") == delta, "invalid_message", "penalty deltas do not match payments")
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
            _require(view in {"public", "full"} or (isinstance(view, dict) and "seat" in view and all(key == "seat" or EXTENSION_FIELD_RE.fullmatch(key) for key in view) and isinstance(view.get("seat"), int) and 0 <= view["seat"] <= 3), "invalid_message", "replay view is invalid")
            _check_target(message.get("target"), allowed_types={"recording"})
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
        if resumed:
            _require("replay_from_seq" in message and "resume" in message, "invalid_message", "resumed welcome is incomplete")
        else:
            _require("replay_from_seq" not in message, "invalid_message", "new welcome must not contain replay_from_seq")
    elif kind == "hello":
        _check_capabilities(message.get("capabilities"))
        for profile in message.get("profiles", []):
            _require(isinstance(profile, dict), "invalid_message", "profile advertisement is invalid")
            revisions = profile.get("revisions", [])
            hashes = profile.get("hashes", {})
            _require(set(revisions) == set(hashes), "invalid_message", "profile revisions and hashes must match")
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


def semantic_transport_trace(trace: Mapping[str, Any]) -> None:
    _require(trace.get("trace_type") == "transport", "invalid_message", "transport trace type is invalid")
    transport = trace.get("transport")
    if transport == "jsonl":
        for line in trace.get("lines", []):
            _require(isinstance(line, str) and line.endswith("\n") and line.strip(" \t\r\n") != "", "invalid_frame", "invalid JSONL line")
    elif transport == "websocket":
        _require(trace.get("message_type") in {"text", "binary"}, "invalid_frame", "invalid websocket message type")
        if trace.get("message_type") == "text":
            _require(isinstance(trace.get("message"), str) and trace["message"] != "", "invalid_frame", "empty websocket text message")
            if "fragments" in trace:
                _require(isinstance(trace["fragments"], list) and "".join(trace["fragments"]) == trace["message"], "invalid_frame", "websocket fragments do not reconstruct the message")
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


def _check_scoring_hand(hand: Any, fixture_id: str) -> None:
    _require(isinstance(hand, dict), "scoring_error", f"{fixture_id}: hand is not an object")
    concealed = hand.get("concealed_tiles", [])
    melds = hand.get("melds", [])
    _require(isinstance(concealed, list) and isinstance(melds, list), "scoring_error", f"{fixture_id}: hand arrays are invalid")
    kan_kinds = {"kantsu", "ankan", "daiminkan", "kakan"}
    called_open = {"chi": True, "pon": True, "daiminkan": True, "kakan": True, "ankan": False}
    logical_count = len(concealed)
    kan_count = 0
    for meld in melds:
        _require(isinstance(meld, dict) and isinstance(meld.get("tiles"), list), "scoring_error", f"{fixture_id}: meld tiles are invalid")
        kind = meld.get("kind")
        if kind in called_open:
            _require(meld.get("open") is called_open[kind], "scoring_error", f"{fixture_id}: {kind} open flag is inconsistent")
        logical_count += len(meld["tiles"])
        if kind in kan_kinds:
            kan_count += 1
    # The fixture format represents a 13-tile hand plus one extra physical
    # tile per kan (the winning tile is part of the 13-tile input shape).
    _require(logical_count == 13 + kan_count, "scoring_error", f"{fixture_id}: hand/meld logical tile count is inconsistent")


def _scoring_state_values(state: Any, fixture_id: str) -> Tuple[int, int]:
    _require(isinstance(state, Mapping), "scoring_error", f"{fixture_id}: state is not an object")
    values = []
    for field in ("honba", "kyotaku"):
        value = state.get(field, 0)
        _require(isinstance(value, int) and not isinstance(value, bool) and value >= 0, "scoring_error", f"{fixture_id}: {field} is invalid")
        values.append(value)
    return values[0], values[1]


def _payment_deltas(payments: Any, fixture_id: str, context: str) -> List[int]:
    _require(isinstance(payments, list), "scoring_error", f"{fixture_id}.{context}: payments are not an array")
    deltas = [0, 0, 0, 0]
    for payment in payments:
        _require(isinstance(payment, Mapping), "scoring_error", f"{fixture_id}.{context}: payment is not an object")
        source = payment.get("from")
        target = payment.get("to")
        points = payment.get("points")
        _require(isinstance(source, int) and not isinstance(source, bool) and 0 <= source <= 3, "scoring_error", f"{fixture_id}.{context}: payment source is invalid")
        _require(isinstance(target, int) and not isinstance(target, bool) and 0 <= target <= 3, "scoring_error", f"{fixture_id}.{context}: payment target is invalid")
        _require(source != target, "scoring_error", f"{fixture_id}.{context}: payment endpoints are identical")
        _require(isinstance(points, int) and not isinstance(points, bool) and points >= 0, "scoring_error", f"{fixture_id}.{context}: payment points are invalid")
        deltas[source] -= points
        deltas[target] += points
    return deltas


def _check_delta_conservation(deltas: Any, fixture_id: str, context: str) -> None:
    _require(isinstance(deltas, list) and len(deltas) == 4, "scoring_error", f"{fixture_id}.{context}: deltas are invalid")
    _require(all(isinstance(value, int) and not isinstance(value, bool) for value in deltas), "scoring_error", f"{fixture_id}.{context}: deltas contain a non-integer")
    _require(sum(deltas) == 0, "scoring_error", f"{fixture_id}.{context}: deltas do not conserve points")


def _check_expected_win(input_data: Mapping[str, Any], win: Mapping[str, Any], fixture_id: str) -> None:
    method = input_data.get("win_method")
    _require(input_data.get("actor") == win.get("actor"), "scoring_error", f"{fixture_id}: expected actor does not match input")
    _require(input_data.get("target") == win.get("target"), "scoring_error", f"{fixture_id}: expected target does not match input")
    _require(input_data.get("winning_tile") == win.get("winning_tile"), "scoring_error", f"{fixture_id}: winning_tile does not match expected win")
    if method == "tsumo":
        _require(win.get("target") == win.get("actor"), "scoring_error", f"{fixture_id}: tsumo target must equal actor")
    elif method == "ron":
        _require(win.get("target") != win.get("actor"), "scoring_error", f"{fixture_id}: ron target must differ from actor")
    else:
        raise ArtifactError("scoring_error", f"{fixture_id}: unknown win method")
    for key, id_key in (("yakus", "id"), ("bonuses", "id"), ("pao", "yaku_id")):
        values = [entry.get(id_key) for entry in win.get(key, []) if isinstance(entry, dict)]
        _require(len(values) == len(set(values)), "scoring_error", f"{fixture_id}: duplicate {key} id")
    payments = win.get("payments", [])
    derived_deltas = _payment_deltas(payments, fixture_id, "win")
    _require(derived_deltas == win.get("deltas"), "scoring_error", f"{fixture_id}: win deltas do not match payments")
    _check_delta_conservation(win.get("deltas"), fixture_id, "win")


def _effective_scoring_rules(base_rules: Mapping[str, Any], fixture: Mapping[str, Any]) -> Dict[str, Any]:
    effective = dict(base_rules)
    overrides = fixture.get("rule_overrides", {})
    if isinstance(overrides, Mapping):
        for key, value in overrides.items():
            if isinstance(value, Mapping) and isinstance(effective.get(key), Mapping):
                nested = dict(effective[key])
                nested.update(value)
                effective[key] = nested
            else:
                effective[key] = value
    return effective


def _expected_settlement_extras(
    fixture_id: str,
    input_data: Mapping[str, Any],
    wins: Sequence[Mapping[str, Any]],
    state: Mapping[str, Any],
    rules: Mapping[str, Any],
) -> List[int]:
    """Return per-win additions to hand_points from honba and kyotaku."""
    honba, kyotaku = _scoring_state_values(state, fixture_id)
    stick_value = rules.get("riichi_stick_value", 0)
    ron_honba = rules.get("honba_ron_value", 0)
    tsumo_honba = rules.get("honba_tsumo_value_per_payer", 0)
    _require(isinstance(stick_value, int) and not isinstance(stick_value, bool) and stick_value >= 0 and stick_value % 100 == 0, "scoring_error", f"{fixture_id}: riichi_stick_value is invalid")
    _require(isinstance(ron_honba, int) and not isinstance(ron_honba, bool) and ron_honba >= 0, "scoring_error", f"{fixture_id}: honba_ron_value is invalid")
    _require(isinstance(tsumo_honba, int) and not isinstance(tsumo_honba, bool) and tsumo_honba >= 0, "scoring_error", f"{fixture_id}: honba_tsumo_value_per_payer is invalid")

    count = len(wins)
    _require(count > 0, "scoring_error", f"{fixture_id}: no wins to settle")
    extras = [0] * count
    methods = [entry.get("win_method") for entry in [input_data] + list(input_data.get("other_winners", []))]
    if count == 1:
        method = methods[0]
        if method == "ron":
            extras[0] += honba * ron_honba
        elif method == "tsumo":
            # A tsumo has three payer shares. Pao may collapse those shares
            # into one payment entry, but the honba amount remains per payer.
            extras[0] += honba * tsumo_honba * 3
        else:
            raise ArtifactError("scoring_error", f"{fixture_id}: unknown settlement method")
        extras[0] += kyotaku * stick_value
        return extras

    _require(all(method == "ron" for method in methods), "scoring_error", f"{fixture_id}: multiple wins must all be ron")
    settlement = rules.get("multiple_ron_settlement", {})
    _require(isinstance(settlement, Mapping), "scoring_error", f"{fixture_id}: multiple_ron_settlement is invalid")
    honba_policy = settlement.get("honba")
    kyotaku_policy = settlement.get("kyotaku")
    _require(honba_policy in {"each_winner", "first_winner"}, "scoring_error", f"{fixture_id}: multiple-ron honba policy is invalid")
    _require(kyotaku_policy in {"first_winner", "equal_split"}, "scoring_error", f"{fixture_id}: multiple-ron kyotaku policy is invalid")
    first_index = min(
        range(count),
        key=lambda index: (wins[index].get("actor", 0) - wins[index].get("target", 0) + 4) % 4,
    )
    if honba_policy == "each_winner":
        extras = [honba * ron_honba] * count
    else:
        extras[first_index] += honba * ron_honba

    total_kyotaku = kyotaku * stick_value
    if kyotaku_policy == "first_winner":
        extras[first_index] += total_kyotaku
    else:
        # Equal split is in 100-point units; any remainder goes to the first
        # winner, as required by the profile settlement rule.
        equal_share = (total_kyotaku // count // 100) * 100
        extras = [extra + equal_share for extra in extras]
        extras[first_index] += total_kyotaku - equal_share * count
    return extras


def _check_scoring_fixture_semantics(fixture: Mapping[str, Any], base_rules: Mapping[str, Any]) -> None:
    fixture_id = fixture.get("id", "<unknown>")
    input_data = fixture.get("input", {})
    _check_scoring_hand(input_data.get("hand"), fixture_id)
    state = fixture.get("state", {})
    state_honba, state_kyotaku = _scoring_state_values(state, fixture_id)
    expected = fixture.get("expected", {})
    result_type = expected.get("result_type")
    if result_type == "penalty":
        payments = expected.get("payments", [])
        derived_deltas = _payment_deltas(payments, fixture_id, "penalty")
        _require(derived_deltas == expected.get("deltas"), "scoring_error", f"{fixture_id}: penalty deltas do not match payments")
        _check_delta_conservation(expected.get("deltas"), fixture_id, "penalty")
        offender = expected.get("offender")
        _require(isinstance(offender, int) and not isinstance(offender, bool) and 0 <= offender <= 3, "scoring_error", f"{fixture_id}: penalty offender is invalid")
        for payment in payments:
            _require(payment.get("from") == offender and payment.get("to") != offender, "scoring_error", f"{fixture_id}: penalty payment does not originate at offender")
        return
    if result_type == "ryukyoku":
        _check_delta_conservation(expected.get("deltas"), fixture_id, "ryukyoku")
        return
    if result_type != "hora":
        raise ArtifactError("scoring_error", f"{fixture_id}: unknown result type")
    wins = expected.get("wins", [])
    winner_inputs = [input_data] + list(input_data.get("other_winners", []))
    _require(len(wins) == len(winner_inputs), "scoring_error", f"{fixture_id}: winner/input count mismatch")
    _require(len({win.get("actor") for win in wins}) == len(wins), "scoring_error", f"{fixture_id}: winner actors are not unique")
    for index, winner_input in enumerate(winner_inputs):
        winner_state = state if index == 0 else winner_input.get("state")
        winner_honba, winner_kyotaku = _scoring_state_values(winner_state, f"{fixture_id}.winner{index}")
        _require((winner_honba, winner_kyotaku) == (state_honba, state_kyotaku), "scoring_error", f"{fixture_id}: winner state settlement counters differ")
    for index, (winner_input, win) in enumerate(zip(winner_inputs, wins)):
        _check_scoring_hand(winner_input.get("hand"), f"{fixture_id}.winner{index}")
        _check_expected_win(winner_input, win, f"{fixture_id}.winner{index}")
        if index > 0:
            nested_expected = winner_input.get("expected")
            _require(isinstance(nested_expected, dict), "scoring_error", f"{fixture_id}.winner{index}: nested expected win is missing")
            _check_expected_win(winner_input, nested_expected, f"{fixture_id}.winner{index}.nested")
    expected_deltas = expected.get("deltas")
    combined = [sum(win.get("deltas", [0, 0, 0, 0])[i] for win in wins) for i in range(4)]
    _require(expected_deltas == combined, "scoring_error", f"{fixture_id}: combined deltas do not match wins")
    _check_delta_conservation(expected_deltas, fixture_id, "combined")

    if len(wins) > 1:
        targets = {win.get("target") for win in wins}
        _require(len(targets) == 1, "scoring_error", f"{fixture_id}: multiple wins do not share a target")
        _require(all(winner_input.get("win_method") == "ron" for winner_input in winner_inputs), "scoring_error", f"{fixture_id}: multiple wins contain a non-ron input")
    extras = _expected_settlement_extras(fixture_id, input_data, wins, state, _effective_scoring_rules(base_rules, fixture))
    for index, win in enumerate(wins):
        incoming = sum(payment.get("points", 0) for payment in win.get("payments", []) if payment.get("to") == win.get("actor"))
        _require(all(payment.get("from") != win.get("actor") for payment in win.get("payments", [])), "scoring_error", f"{fixture_id}.winner{index}: winner cannot be a payer")
        expected_incoming = win.get("hand_points", 0) + extras[index]
        _require(incoming == expected_incoming, "scoring_error", f"{fixture_id}.winner{index}: settlement allocation does not match hand_points/state")


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
        wall = state.get("wall_remaining", 136)
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
                phase, pending_kan, wall = "awaiting_draw", False, 136
                first_turn, kan_counts = [True] * 4, [0] * 4
            elif event_type == "tsumo":
                _require(phase == "awaiting_draw" and wall > 0, "invalid_message", "tsumo outside draw phase")
                wall -= 1
                phase = "awaiting_action"
            elif event_type == "dahai":
                _require(phase == "awaiting_action", "invalid_message", "dahai outside action phase")
                actor = event.get("actor")
                _require(isinstance(actor, int) and 0 <= actor <= 3, "invalid_message", "dahai actor is invalid")
                first_turn[actor] = False
                phase = "awaiting_responses"
            elif event_type == "kakan_declared" or event_type == "ankan_declared":
                _require(phase == "awaiting_action" and not pending_kan, "invalid_message", "kan declaration outside action phase")
                pending_kan = True
            elif event_type in {"kakan", "ankan"}:
                _require(pending_kan, "invalid_message", "kan commit without declaration")
                actor = event.get("actor")
                _require(isinstance(actor, int) and 0 <= actor <= 3 and kan_counts[actor] < 4, "invalid_message", "kan count is invalid")
                kan_counts[actor] += 1
                pending_kan, phase = False, "awaiting_draw"
            elif event_type == "end_kyoku":
                _require(not pending_kan, "invalid_message", "end_kyoku with pending kan")
            elif event_type == "end_game":
                _require("end_kyoku" in types[: event_index + 1], "invalid_message", "end_game before end_kyoku")


def check_vectors(schemas: SchemaSet, manifest: Dict[str, Any]) -> int:
    vectors = strict_load(ROOT / manifest["vectors"])
    root_schema = schema_by_id(schemas, "urn:yamai:schema:yrc-0003:1.0-draft.5:message")
    checked = 0
    for entry in manifest["cases"]:
        case_id = entry["id"]
        case = vectors[case_id]
        if case.get("negative_expect") != entry["expect_negative"]:
            raise ArtifactError("vector_error", f"{case_id}: manifest negative expectation mismatch")
        if case.get("negative_expect") not in {
            "invalid_frame",
            "invalid_json",
            "invalid_message",
            "profile_mismatch",
            "request_conflict",
            "resource_limit",
            "resume_unavailable",
        }:
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


def check_scoring(schemas: SchemaSet, rules_registry: Mapping[str, Any]) -> Tuple[int, int]:
    data = strict_load(ROOT / "test-vectors/yrc-0005/1.0-draft.3/scoring.json")
    scoring_schema_id = "urn:yamai:schema:yrc-0005:1.0-draft.3:scoring-vectors"
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
        _check_scoring_fixture_semantics(fixture, base_rules)
    expected_fixture_count = rules_registry.get("scoring_fixture_count")
    if isinstance(expected_fixture_count, int) and len(fixtures) != expected_fixture_count:
        raise ArtifactError("scoring_error", "scoring fixture count does not match registry")

    coverage = rules_registry.get("fixture_coverage", {})
    observed_yakus = set()
    fixtures_by_id = {fixture["id"]: fixture for fixture in fixtures}
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
    return len(vectors), len(fixtures)


def main() -> int:
    try:
        load_all_json()
        schemas = SchemaSet()
        schemas.check_refs()
        schemas.check_keyword_support()
        root = schema_by_id(schemas, "urn:yamai:schema:yrc-0003:1.0-draft.5:message")
        union_refs = root.get("oneOf", [])
        expected_kinds = {"hello", "join", "welcome", "event", "request", "action", "ack", "error", "snapshot"}
        if len(union_refs) != 9:
            raise ArtifactError("schema_error", "message union must contain nine branches")
        expected_ids = {"urn:yamai:schema:yrc-0003:1.0-draft.5:" + x for x in expected_kinds}
        actual_ids = {item.get("$ref", "").split("#", 1)[0] for item in union_refs}
        if actual_ids != expected_ids:
            raise ArtifactError("schema_error", "message union branches mismatch")
        p, r = check_registry(schemas)
        manifest = check_manifest(schemas, p, r)
        check_release_manifest(manifest, p, r)
        vector_count = check_vectors(schemas, manifest)
        scoring_count, scoring_fixture_count = check_scoring(schemas, r)
        print(f"OK: schemas={len(schemas.schemas)} vectors={vector_count} scoring_vectors={scoring_count} scoring_fixtures={scoring_fixture_count} profile_hash={manifest['profile_hash']}")
        return 0
    except ArtifactError as exc:
        print(f"FAIL [{exc.code}]: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # make CI failures actionable instead of a traceback
        print(f"FAIL [internal]: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
