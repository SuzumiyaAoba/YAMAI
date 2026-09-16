"""Independent Draft 2020-12 meta/schema check, run by the pinned Nix gate.

All $refs are resolved from the release's local schema registry. This check
does not fetch schemas and does not replace the strict parser/semantic gates.
"""
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

import validate_artifacts as v


def main():
    schemas = v.SchemaSet()
    registry = Registry().with_resources((sid, Resource.from_contents(schema)) for sid, schema in schemas.schemas.items())
    for schema in schemas.schemas.values():
        Draft202012Validator.check_schema(schema)
    def check(data, sid):
        Draft202012Validator({"$ref":sid}, registry=registry).validate(data)
    protocol = f"urn:yamai:schema:yrc-0003:{v.PROTOCOL}:"
    manifest = v.strict_load(v.ROOT / f"test-vectors/yrc-0003/{v.PROTOCOL}/manifest.json")
    check(manifest, protocol + "vector-manifest")
    vectors = v.strict_load(v.ROOT / manifest["vectors"])
    count = 1
    for case in vectors.values():
        positive = case["positive"]
        if "kind" in positive:
            check(positive, protocol + "message")
            count += 1
        for message in case.get("positive_messages", []):
            check(message, protocol + "message")
            count += 1
        if case.get("schema_negative", False):
            negatives = case.get("negative_variants", []) + case.get("negative_messages", [])
            if "negative" in case:
                negatives = [case["negative"], *negatives]
            for message in negatives:
                validator = Draft202012Validator({"$ref":protocol + "message"}, registry=registry)
                if validator.is_valid(message):
                    raise AssertionError("Schema accepted a designated negative message")
                count += 1
        trace = positive.get("trace", {})
        if trace.get("trace_type") == "session":
            check(trace, protocol + "stateful-trace")
            count += 1
        if trace.get("operation") == "legal_actions":
            for action in trace["expected"]:
                check(action, protocol + "action#/$defs/actionObject")
                count += 1
        elif trace.get("operation") == "event_state":
            for event in trace["input"]["events"]:
                check(event, protocol + "event#/properties/event")
                count += 1
    scoring = v.strict_load(v.ROOT / manifest["scoring_vectors"])
    check(scoring, f"urn:yamai:schema:yrc-0005:{v.PROFILE_REVISION}:scoring-vectors")
    print(f"Draft 2020-12: {len(schemas.schemas)} schemas; {count + 1} independent instance checks")


if __name__ == "__main__":
    main()
