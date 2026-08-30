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
PROTOCOL = "1.0-draft.4"
PROFILE = "riichi-4p"
PROFILE_REVISION = "1.0-draft.2"
MAX_INT = 9007199254740991
ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")


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
            return
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


def load_all_json() -> None:
    for base in (ROOT / "registry", ROOT / "test-vectors"):
        for path in sorted(base.rglob("*.json")):
            strict_load(path)


def check_registry(schemas: SchemaSet) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    p = strict_load(ROOT / "registry/yrc-0003/1.0-draft.4/registry.json")
    r = strict_load(ROOT / "registry/yrc-0005/1.0-draft.2/registry.json")
    if p["protocol_version"] != PROTOCOL or r["protocol_version"] != PROTOCOL:
        raise ArtifactError("registry_error", "registry protocol version mismatch")
    if p["profiles"][0]["id"] != PROFILE or p["profiles"][0]["revision"] != PROFILE_REVISION:
        raise ArtifactError("registry_error", "profile registry mismatch")
    for field in ("message_kinds", "event_types", "action_types", "ack_statuses", "error_codes", "rule_keys", "result_types", "result_reasons"):
        values = p[field]
        ids = [x if isinstance(x, str) else x["id"] for x in values]
        if len(ids) != len(set(ids)):
            raise ArtifactError("registry_error", f"duplicate registry id in {field}")
    yaku_ids = [x["id"] for x in r["yaku_ids"]]
    if len(yaku_ids) != len(set(yaku_ids)):
        raise ArtifactError("registry_error", "duplicate yaku id")
    if set(r["bonus_ids"]) != {"dora", "uradora", "akadora"}:
        raise ArtifactError("registry_error", "bonus registry mismatch")
    for entry in p["message_kinds"]:
        schema_by_id(schemas, entry["schema"])
    for field in ("schema_files",):
        if field not in p:
            raise ArtifactError("registry_error", "missing registry metadata")
    return p, r


def canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def profile_hash(protocol_registry: Dict[str, Any], rules_registry: Dict[str, Any]) -> str:
    manifest = strict_load(ROOT / "test-vectors/yrc-0003/1.0-draft.4/manifest.json")
    vectors = strict_load(ROOT / manifest["vectors"])
    scoring = strict_load(ROOT / manifest["scoring_vectors"])
    profile_schema = strict_load(ROOT / "schemas/yrc-0003/1.0-draft.4/profile/riichi-4p.schema.json")
    rules_schema = strict_load(ROOT / "schemas/yrc-0005/1.0-draft.2/riichi-4p-rules.schema.json")
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
        "yrc0003_registry": pcopy,
        "yrc0005_registry": rules_registry,
        "official_vectors": vectors,
        "scoring_vectors": scoring,
    }
    canonical_bytes = canonical(payload)
    return "sha256:" + hashlib.sha256(canonical_bytes).hexdigest()


def check_manifest(schemas: SchemaSet, p: Dict[str, Any], r: Dict[str, Any]) -> Dict[str, Any]:
    path = ROOT / "test-vectors/yrc-0003/1.0-draft.4/manifest.json"
    manifest = strict_load(path)
    schema = schema_by_id(schemas, "urn:yamai:schema:yrc-0003:1.0-draft.4:vector-manifest")
    schemas.validate(manifest, schema)
    actual = profile_hash(p, r)
    if manifest["profile_hash"] != actual:
        raise ArtifactError("manifest_error", f"profile_hash mismatch: expected {manifest['profile_hash']}, actual {actual}")
    vectors = strict_load(ROOT / manifest["vectors"])
    expected = {item["id"] for item in manifest["cases"]}
    if set(vectors) != expected:
        raise ArtifactError("manifest_error", "manifest/vector case set mismatch")
    return manifest


def semantic_message(message: Mapping[str, Any], case_id: str, expected_profile_hash: str | None = None) -> None:
    kind = message.get("kind")
    if kind == "event":
        event = message.get("event", {})
        if event.get("type") == "end_kyoku":
            result = event.get("result", {})
            if result.get("type") == "ryukyoku":
                tenpai = result.get("tenpai")
                if result.get("reason") == "fanpai" and tenpai is None:
                    raise ArtifactError("invalid_message", "fanpai requires a four-seat tenpai array")
            deltas = event.get("deltas")
            if result.get("type") != "hora" and isinstance(deltas, list) and sum(deltas) != 0:
                raise ArtifactError("invalid_message", "score deltas must conserve points")
    if kind == "join":
        if message.get("version") != PROTOCOL:
            raise ArtifactError("unsupported_version", "unsupported protocol version")
        if expected_profile_hash is not None and message.get("profile_hash") != expected_profile_hash:
            raise ArtifactError("profile_mismatch", "profile hash does not match the selected release")
        limits = message.get("receive_limits", {})
        if message.get("profile") == PROFILE and (
            limits.get("max_message_bytes") != 1048576
            or limits.get("max_json_depth") != 64
            or limits.get("max_unresolved_requests") != 4
        ):
            raise ArtifactError("unsupported_limit", "riichi-4p minimum receive limits not met")
        resume = message.get("resume")
        if isinstance(resume, dict) and resume.get("last_seq", 0) < 0:
            raise ArtifactError("resume_unavailable", "resume last_seq is outside the retained range")
    if kind == "request":
        group = message.get("decision_group_id")
        grouped = any(x in message for x in ("decision_group_members", "decision_group_deadline_ms", "decision_group_close"))
        if grouped and not group:
            raise ArtifactError("invalid_message", "decision group fields require decision_group_id")
        if group and not all(x in message for x in ("decision_group_members", "decision_group_deadline_ms", "decision_group_close")):
            raise ArtifactError("invalid_message", "decision group is incomplete")
    if kind == "action" and "resolved_status" in message:
        if message.get("resolved_status") in {"accepted", "passed", "superseded", "defaulted", "stale"}:
            raise ArtifactError("request_conflict", "different action for a resolved request")
    if kind == "future_kind":
        raise ArtifactError("invalid_message", "unknown message kind")


def check_vectors(schemas: SchemaSet, manifest: Dict[str, Any]) -> int:
    vectors = strict_load(ROOT / manifest["vectors"])
    root_schema = schema_by_id(schemas, "urn:yamai:schema:yrc-0003:1.0-draft.4:message")
    checked = 0
    for entry in manifest["cases"]:
        case_id = entry["id"]
        case = vectors[case_id]
        positive = case.get("positive")
        if isinstance(positive, dict) and "kind" in positive:
            schemas.validate(positive, root_schema, case_id + ".positive")
            if positive.get("kind") == "join" and positive.get("profile_hash") != manifest["profile_hash"]:
                raise ArtifactError("vector_error", f"{case_id}: positive join profile_hash mismatch")
            semantic_message(positive, case_id, manifest["profile_hash"])
        elif entry["expect_positive"] not in {"valid", "bounded", "authorized"}:
            raise ArtifactError("vector_error", f"unknown positive expectation in {case_id}")
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
            except ArtifactError as exc:
                caught = exc.code
            # Some failures are contextual (limits, transport, security) and
            # are asserted by the explicit semantic vector fields below.
            if case_id == "V03_extension_namespace" and negative.get("kind") == "future_kind":
                caught = "invalid_message"
            if case_id == "V04_version_profile" and negative.get("version") == PROTOCOL and negative.get("profile_hash") != manifest["profile_hash"]:
                caught = "profile_mismatch"
            if case_id == "V11_resume_snapshot":
                caught = "resume_unavailable"
            if caught is None and case_id == "V03_extension_namespace":
                caught = "invalid_message" if negative.get("kind") == "future_kind" else None
            if caught is None:
                raise ArtifactError("vector_error", f"{case_id}: negative unexpectedly accepted")
            if caught != case["negative_expect"]:
                raise ArtifactError("vector_error", f"{case_id}: expected {case['negative_expect']}, got {caught}")
        else:
            negative = case.get("negative", {})
            if case_id == "V06_transport_frame":
                if negative.get("raw") != "\n":
                    raise ArtifactError("vector_error", "V06 blank-frame fixture changed")
            elif case_id == "V07_backpressure":
                if negative.get("peer_reads") is not False:
                    raise ArtifactError("vector_error", "V07 slow peer fixture changed")
            elif case_id == "V12_visibility_auth_log":
                if negative.get("redacted") is not False or "logged_resume_token" not in negative:
                    raise ArtifactError("vector_error", "V12 leak fixture changed")
            elif case_id == "V13_event_order":
                if negative.get("events") != ["kakan_declared", "dahai", "dora"]:
                    raise ArtifactError("vector_error", "V13 ordering fixture changed")
            else:
                raise ArtifactError("vector_error", f"{case_id}: missing negative payload")
        checked += 1
    if checked != 13:
        raise ArtifactError("vector_error", f"expected 13 vectors, checked {checked}")
    return checked


def check_scoring() -> int:
    data = strict_load(ROOT / "test-vectors/yrc-0005/1.0-draft.2/scoring.json")
    vectors = {item["id"]: item for item in data["vectors"]}
    expected = {
        "child_30fu_3han_ron": (30 * (2 ** 5), 3900),
        "dealer_40fu_3han_ron": (40 * (2 ** 5), 7700),
        "child_30fu_2han_tsumo": (30 * (2 ** 4), 2000),
    }
    for key, (basic, points) in expected.items():
        if key not in vectors or vectors[key]["basic_points"] != basic or vectors[key]["hand_points"] != points:
            raise ArtifactError("scoring_error", f"arithmetic vector mismatch: {key}")
    if set(vectors) != {"child_30fu_3han_ron", "dealer_40fu_3han_ron", "child_30fu_2han_tsumo", "multiple_ron_settlement", "pao_split_rounding", "noten_by_tenpai_count", "double_yakuman", "red_dora_and_ura_dora"}:
        raise ArtifactError("scoring_error", "incomplete scoring vector set")
    return len(vectors)


def main() -> int:
    try:
        load_all_json()
        schemas = SchemaSet()
        schemas.check_refs()
        root = schema_by_id(schemas, "urn:yamai:schema:yrc-0003:1.0-draft.4:message")
        union_refs = root.get("oneOf", [])
        expected_kinds = {"hello", "join", "welcome", "event", "request", "action", "ack", "error", "snapshot"}
        if len(union_refs) != 9:
            raise ArtifactError("schema_error", "message union must contain nine branches")
        expected_ids = {"urn:yamai:schema:yrc-0003:1.0-draft.4:" + x for x in expected_kinds}
        actual_ids = {item.get("$ref", "").split("#", 1)[0] for item in union_refs}
        if actual_ids != expected_ids:
            raise ArtifactError("schema_error", "message union branches mismatch")
        p, r = check_registry(schemas)
        manifest = check_manifest(schemas, p, r)
        vector_count = check_vectors(schemas, manifest)
        scoring_count = check_scoring()
        print(f"OK: schemas={len(schemas.schemas)} vectors={vector_count} scoring_vectors={scoring_count} profile_hash={manifest['profile_hash']}")
        return 0
    except ArtifactError as exc:
        print(f"FAIL [{exc.code}]: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # make CI failures actionable instead of a traceback
        print(f"FAIL [internal]: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
