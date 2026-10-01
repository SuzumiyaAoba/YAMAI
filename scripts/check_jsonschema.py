"""Independent Draft 2020-12 meta/schema check, run by the pinned Nix gate.

All $refs are resolved from the release's local schema registry. This check
does not fetch schemas and does not replace the strict parser/semantic gates.
"""
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

import validate_artifacts as v


def request_payloads(trace):
    """Yield real fixture payloads, including lifecycles bound to a ledger."""
    yield from trace.get("requests", [])
    for lifecycle in trace.get("request_lifecycles", []):
        yield from request_payloads(lifecycle)


def main():
    schemas = v.SchemaSet()
    registry = Registry().with_resources((sid, Resource.from_contents(schema)) for sid, schema in schemas.schemas.items())
    for schema in schemas.schemas.values():
        Draft202012Validator.check_schema(schema)
    request_validator = Draft202012Validator(v.request_payload_schema(schemas), registry=registry)
    def check(data, sid):
        Draft202012Validator({"$ref":sid}, registry=registry).validate(data)
    protocol = f"urn:yamai:schema:protocol:{v.PROTOCOL}:"
    manifest = v.strict_load(v.ROOT / f"test-vectors/protocol/{v.PROTOCOL}/manifest.json")
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
        for request in request_payloads(trace):
            request_validator.validate(request)
            count += 1
        negatives = [case.get("negative", {}), *case.get("negative_variants", []), *case.get("negative_messages", [])]
        for negative in negatives:
            negative_trace = negative.get("trace", {})
            request_errors = []
            for request in request_payloads(negative_trace):
                request_errors.extend(request_validator.iter_errors(request))
                count += 1
            # A negative trace can fail a semantic assertion after its
            # payloads pass schema validation. A payload schema failure is
            # evidence only for the designated invalid_message outcome.
            if request_errors and case["negative_expect"] != "invalid_message":
                raise AssertionError("Negative trace request schema failed with a different expected outcome")
            if negative_trace and case.get("schema_negative", False) and not request_errors:
                raise AssertionError("Schema accepted designated negative trace request payloads")
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
    check(scoring, f"urn:yamai:schema:riichi-4p:{v.PROFILE_REVISION}:scoring-vectors")
    print(f"Draft 2020-12: {len(schemas.schemas)} schemas; {count + 1} independent instance checks")


if __name__ == "__main__":
    main()
