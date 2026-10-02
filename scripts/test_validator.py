"""Regression checks for the artifact checker; run with Python's unittest."""

import unittest
import random
import json
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
from decimal import Decimal, InvalidOperation, localcontext
from fractions import Fraction
from io import StringIO
from itertools import permutations
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import validate_artifacts as v


class ValidatorBoundaries(unittest.TestCase):
    def assert_error(self, code, operation, *args, **kwargs):
        with self.assertRaises(v.ArtifactError) as caught:
            operation(*args, **kwargs)
        self.assertEqual(caught.exception.code, code)

    def test_turn_request_rejects_reaction_only_candidates_without_cause(self):
        request = {"request_id": "r", "seat": 0, "caused_by_seq": 1,
                   "timeout_ms": 100, "time_bank_ms": 0,
                   "legal_actions": [{"action_id": "d", "action": {
                       "type": "dahai", "actor": 0, "pai": "1m", "tsumogiri": True}}],
                   "default_action_id": "d"}
        schemas = v.SchemaSet()
        schema = v.request_payload_schema(schemas)
        v._check_request(request)
        for kind in ("chi", "pon", "daiminkan"):
            action = {"type": kind, "actor": 0, "target": 3, "pai": "1m",
                      "consumed": ["2m", "3m"] if kind == "chi" else ["1m"] * (3 if kind == "daiminkan" else 2)}
            if kind != "daiminkan":
                action["dahai"] = {"type": "dahai", "actor": 0, "pai": "2p", "tsumogiri": False}
            candidate = deepcopy(request)
            candidate["legal_actions"].append({"action_id": "call", "action": action})
            with self.subTest(kind=kind):
                # The shape is legal; decision context must still be checked
                # when the cause event is absent from a partial capture.
                schemas.validate(candidate, schema)
                self.assert_error("invalid_message", v._check_request, candidate)
        for kind in ("hora", "ryukyoku", "x_acme_choice"):
            candidate = deepcopy(request)
            candidate["legal_actions"].append({"action_id": "choice", "action": {"type": kind, "actor": 0}})
            with self.subTest(kind=kind):
                v._check_request(candidate, extension_contexts={"x_acme_choice": ["turn"]})
        self.assert_error("invalid_message", v._check_request, candidate,
                          extension_contexts={"x_acme_choice": ["reaction"]})

    def test_static_resource_deadline_covers_both_backlog_limits(self):
        for backlog_bytes, backlog_messages in ((8388608, 1), (1000, 1024), (8388608, 1024)):
            trace = {'trace_type': 'resource', 'peer_reads': False,
                     'send_backlog_bytes': backlog_bytes, 'send_backlog_messages': backlog_messages,
                     'write_deadline_ms': 60000}
            with self.subTest(bytes=backlog_bytes, messages=backlog_messages):
                self.assert_error('resource_limit', v.semantic_resource_trace, trace)
                v.semantic_resource_trace(dict(trace, peer_reads=True))
        v.semantic_resource_trace({'trace_type': 'resource', 'peer_reads': False,
                                   'send_backlog_bytes': 8388607, 'send_backlog_messages': 1023,
                                   'write_deadline_ms': 60000})

    def test_error_action_id_is_limited_to_action_diagnostics(self):
        schemas = v.SchemaSet()
        error_schema = schemas.schemas[f'urn:yamai:schema:protocol:{v.PROTOCOL}:error']
        schema = {'$ref': error_schema['$id'] + '#/$defs/base'}
        for code in error_schema['$defs']['base']['properties']['code']['enum']:
            message = {'kind': 'error', 'code': code, 'message': 'diagnostic',
                       'severity': 'recoverable' if code in (
                           'sequence_gap', 'invalid_action', 'request_conflict') else 'fatal',
                       'request_id': 'r1', 'action_id': 'a1'}
            if code == 'sequence_gap':
                message.update(expected_seq=1, received_seq=2)
            with self.subTest(code=code):
                if code in ('invalid_action', 'invalid_message', 'request_conflict'):
                    schemas.validate(message, schema)
                else:
                    self.assert_error('invalid_message', schemas.validate, message, schema)
                    del message['action_id']
                    # request_id remains valid for every request-related error code.
                    schemas.validate(message, schema)

    def test_error_optional_action_diagnostics_keep_id_validation(self):
        schemas = v.SchemaSet()
        schema = {'$ref': f'urn:yamai:schema:protocol:{v.PROTOCOL}:error#/$defs/hostApplication'}
        for code, severity in (('invalid_action', 'recoverable'),
                               ('invalid_message', 'recoverable'), ('invalid_message', 'fatal')):
            message = {'yamai': v.PROTOCOL, 'kind': 'error', 'session_id': 's1',
                       'game_id': 'g1', 'seq': 1, 'code': code, 'severity': severity,
                       'message': 'diagnostic', 'request_id': 'r1'}
            schemas.validate(message, schema)
            for action_id in ('a1', '', None, 1, 'bad id'):
                candidate = dict(message, action_id=action_id)
                with self.subTest(code=code, severity=severity, action_id=action_id):
                    if action_id == 'a1':
                        schemas.validate(candidate, schema)
                    else:
                        self.assert_error('invalid_message', schemas.validate, candidate, schema)

    def test_error_dedicated_members_remain_code_specific(self):
        schemas = v.SchemaSet()
        schema = {'$ref': f'urn:yamai:schema:protocol:{v.PROTOCOL}:error#/$defs/base'}
        message = {'kind': 'error', 'code': 'request_conflict', 'severity': 'recoverable',
                   'message': 'diagnostic', 'request_id': 'r1', 'action_id': 'a1'}
        schemas.validate(message, schema)
        schemas.validate(dict(message, original_status='accepted'), schema)
        for member in ('request_id', 'action_id'):
            candidate = deepcopy(message)
            del candidate[member]
            with self.subTest(missing=member):
                self.assert_error('invalid_message', schemas.validate, candidate, schema)
        for code in ('invalid_action', 'invalid_message'):
            candidate = dict(message, code=code)
            schemas.validate(candidate, schema)
            for member, value in (('original_status', 'accepted'),
                                  ('expected_seq', 1), ('received_seq', 2)):
                with self.subTest(code=code, forbidden=member):
                    self.assert_error('invalid_message', schemas.validate,
                                      dict(candidate, **{member: value}), schema)

    def test_json_integer_spellings(self):
        for raw in (b'1', b'1.0', b'1e0'):
            with self.subTest(raw=raw):
                value = v.strict_load_bytes(b'{"seq":' + raw + b'}')
                self.assertEqual(type(value['seq']), int)
                self.assertEqual(value['seq'], 1)

    def test_json_finite_fraction_annotations_preserve_decimal_range(self):
        schema = {'$ref': f'urn:yamai:schema:protocol:{v.PROTOCOL}:action'}
        schemas = v.SchemaSet()
        prefix = (b'{"yamai":"1.0-draft.1","kind":"action","session_id":"s",'
                  b'"game_id":"g","request_id":"r","action_id":"a","x_test_note":')
        huge = b'1' + b'0' * 309 + b'.5'
        for raw in (huge, b'-' + huge, b'1e-1000', b'-1e-1000', b'0.5'):
            with self.subTest(raw=raw):
                message = v.strict_load_bytes(prefix + raw + b'}')
                number = message['x_test_note']
                self.assertIsInstance(number, Decimal)
                self.assertTrue(number.is_finite())
                self.assertEqual(number, Decimal(raw.decode('ascii')))
                schemas.validate(message, schema)

    def test_json_fraction_range_does_not_relax_integer_or_finite_checks(self):
        for raw in (b'9007199254740992.0', b'-9007199254740992e0',
                    b'1e309', b'-1e309', b'NaN', b'Infinity', b'-Infinity'):
            with self.subTest(raw=raw):
                self.assert_error('invalid_json', v.strict_load_bytes,
                                  b'{"x_test_note":' + raw + b'}')
        for value in (float('nan'), float('inf'), float('-inf'),
                      Decimal('NaN'), Decimal('Infinity'), Decimal('-Infinity')):
            with self.subTest(value=value):
                self.assert_error('invalid_json', v._walk_json, value)

    def test_multiple_of_is_exact_for_decimal_boundaries(self):
        schemas = v.SchemaSet()
        huge = '1' + '0' * 309 + '.5'
        cases = [('0.5', '1e-29', True), (huge, '0.5', True),
                 ('-' + huge, '0.5', True), (huge, '0.2', False),
                 ('0.50000000000000000000000000001', '0.5', False),
                 ('0.3', '0.1', True), ('0.31', '0.1', False),
                 ('0.5', '1e-999999999', True), ('0.5', '3e-999999999', False),
                 ('1e-999999999', '0.5', False), ('0', '1e-999999999', True),
                 ('100', '20', True), ('100', '30', False)]
        for raw, multiple, valid in cases:
            with self.subTest(raw=raw, multiple=multiple):
                value = v.strict_load_bytes(raw.encode('ascii'))
                schema = v.strict_load_bytes(('{"type":"number","multipleOf":' + multiple + '}').encode('ascii'))
                if valid:
                    schemas.validate(value, schema)
                else:
                    self.assert_error('invalid_message', schemas.validate, value, schema)
        # A long coefficient must not depend on Python's int(string) digit cap.
        coefficient = '1' * 5000 + '.5'
        schemas.validate(v.strict_load_bytes(coefficient.encode('ascii')),
                         {'type': 'number', 'multipleOf': Decimal('0.5')})

    def test_multiple_of_matches_independent_fraction_oracle(self):
        rng = random.Random(25733)
        with localcontext() as context:
            context.prec = 2
            for _ in range(500):
                numerator = Decimal((rng.randrange(2), tuple(map(int, str(rng.randrange(100000)))),
                                     rng.randrange(-30, 31)))
                divisor = Decimal((0, tuple(map(int, str(rng.randrange(1, 100000)))),
                                   rng.randrange(-30, 31)))
                expected = (Fraction(numerator) / Fraction(divisor)).denominator == 1
                self.assertEqual(v._is_multiple_of(numerator, divisor), expected,
                                 (numerator, divisor))

    def test_huge_exponent_zero_and_fraction_annotations(self):
        schema = {'$ref': f'urn:yamai:schema:protocol:{v.PROTOCOL}:action'}
        schemas = v.SchemaSet()
        prefix = (b'{"yamai":"1.0-draft.1","kind":"action","session_id":"s",'
                  b'"game_id":"g","request_id":"r","action_id":"a","x_probe_number":')
        for exponent in (b'1000000000000000000', b'9' * 5000):
            for sign in (b'', b'-'):
                for exponent_sign in (b'', b'+', b'-'):
                    raw = prefix + sign + b'0.000e' + exponent_sign + exponent + b'}'
                    with self.subTest(sign=sign, exponent_sign=exponent_sign, digits=len(exponent)):
                        message = v.strict_load_bytes(raw, max_bytes=1048576)
                        self.assertIs(type(message['x_probe_number']), int)
                        self.assertEqual(message['x_probe_number'], 0)
                        schemas.validate(message, schema)
                for coefficient in (b'1', b'1.2500', b'9007199254740992'):
                    raw = prefix + sign + coefficient + b'e-' + exponent + b'}'
                    message = v.strict_load_bytes(raw, max_bytes=1048576)
                    value = message['x_probe_number']
                    self.assertTrue(value < 0 if sign else value > 0)
                    self.assertTrue(-1 < value < 1)
                    schemas.validate(message, schema)
                self.assert_error('invalid_json', v.strict_load_bytes,
                                  sign + b'1e+' + exponent)
        # Exactly one MiB of legitimate exponent digits remains bounded by
        # the normal wire limit; exponent magnitude is never expanded.
        raw = prefix + b'1e-' + b'9' * (1048576 - len(prefix) - 4) + b'}'
        self.assertEqual(len(raw), 1048576)
        value = v.strict_load_bytes(raw, max_bytes=1048576)['x_probe_number']
        self.assertTrue(0 < value < 1)
        self.assert_error('resource_limit', v.strict_load_bytes, raw + b' ', max_bytes=1048576)

    def test_extreme_decimal_normalization_and_comparison(self):
        cases = [
            ('10e-10000000000000000000', '1e-9999999999999999999'),
            ('1.25e-10000000000000000000', '125e-10000000000000000002'),
            ('100e-' + '1' + '0' * 5000, '1e-' + '9' * 4999 + '8'),
        ]
        for left, right in cases:
            for sign in ('', '-'):
                a, b = (v.strict_load_bytes((sign + raw).encode()) for raw in (left, right))
                self.assertEqual(a, b)
                self.assertEqual(v.canonical_action({'x_test_value': a}, private_payload=True),
                                 v.canonical_action({'x_test_value': b}, private_payload=True))
                self.assertTrue(v._json_equal(a, b))
        values = [v.strict_load_bytes(raw.encode()) for raw in (
            '-1', '-1e-1000', '-2e-10000000000000000000',
            '-1e-10000000000000000000', '0', '1e-10000000000000000001',
            '1e-10000000000000000000', '1.01e-10000000000000000000',
            '2e-10000000000000000000', '1e-1000', '1')]
        for index, left in enumerate(values):
            for other, right in enumerate(values):
                self.assertEqual(left < right, index < other)
                self.assertEqual(left > right, index > other)
                self.assertEqual(left == right, index == other)
        with localcontext() as context:
            context.prec = 1
            context.traps[InvalidOperation] = False
            value = v.strict_load_bytes(b'1.2345e-10000000000000000000')
            self.assertEqual(value.canonical(), '12345e-10000000000000000004')

    def test_extreme_decimal_schema_comparison_and_divisibility(self):
        schemas = v.SchemaSet()
        x = v.strict_load_bytes(b'1.25e-10000000000000000000')
        equal = v.strict_load_bytes(b'125e-10000000000000000002')
        smaller = v.strict_load_bytes(b'1.24e-10000000000000000000')
        larger = v.strict_load_bytes(b'1.26e-10000000000000000000')
        schemas.validate(x, {'type': 'number', 'minimum': smaller, 'maximum': larger,
                             'const': equal, 'enum': [equal]})
        for schema in ({'type': 'integer'}, {'minimum': larger}, {'maximum': smaller},
                       {'const': smaller}, {'enum': [smaller, larger]}):
            self.assert_error('invalid_message', schemas.validate, x, schema)
        self.assert_error('invalid_message', schemas.validate, [x, equal], {'uniqueItems': True})
        schemas.validate([x, True, 0], {'uniqueItems': True})
        huge = '1' + '0' * 5000
        cases = [('1.25e-10000000000000000000', '25e-10000000000000000002', True),
                 ('1.25e-10000000000000000000', '3e-10000000000000000002', False),
                 ('1e-10000000000000000000', '1e-10000000000000000001', True),
                 ('1e-10000000000000000001', '1e-10000000000000000000', False),
                 ('0.5', '1e-' + huge, True), ('0.5', '3e-' + huge, False),
                 ('1e-' + huge, '0.5', False), ('0', '1e-' + huge, True)]
        for raw, divisor, expected in cases:
            for sign in ('', '-'):
                value = v.strict_load_bytes((sign + raw).encode())
                multiple = v.strict_load_bytes(divisor.encode())
                self.assertEqual(v._is_multiple_of(value, multiple), expected)
        self.assert_error('hash_error', v.canonical, {'x': x})

    def test_compact_exponent_helpers_match_integer_oracle(self):
        from exact_decimal import exponent_add_small, exponent_compare, bounded_exponent_difference
        rng = random.Random(8411)
        for _ in range(1000):
            a, b = (rng.randrange(-10**50, 10**50) for _ in range(2))
            offset = rng.randrange(-10000, 10001)
            self.assertEqual(exponent_add_small(str(a), offset), str(a + offset))
            self.assertEqual(exponent_compare(str(a), str(b)), (a > b) - (a < b))
            bound = rng.randrange(1, 10000)
            high, low = max(a, b), min(a, b)
            self.assertEqual(bounded_exponent_difference(str(high), str(low), bound), min(high-low, bound))
            self.assertEqual(bounded_exponent_difference(str(a + abs(offset)), str(a), bound), min(abs(offset), bound))
        for exponent in ('9'*5000, '-' + '9'*5000, '1' + '0'*5000, '-1' + '0'*5000):
            for offset in (-999, -1, 0, 1, 999):
                changed = exponent_add_small(exponent, offset)
                self.assertEqual(exponent_add_small(changed, -offset), exponent)

    def test_id_schema_matches_the_entire_decoded_string(self):
        schemas = v.SchemaSet()
        schema = {'$ref':f'urn:yamai:schema:protocol:{v.PROTOCOL}:common#/$defs/id'}
        for value in ('a', 'A0._:-', 'a'*64):
            schemas.validate(value, schema)
        for value in ('', 'a'*65, 'a\n', 'a\r\n', 'a ', 'a\u2028', 'a\u2029', 'あ'):
            with self.subTest(value=repr(value)):
                self.assert_error('invalid_message', schemas.validate, value, schema)

    def test_wire_extension_names_reject_trailing_line_terminators(self):
        schemas = v.SchemaSet()
        vectors = v.strict_load(v.ROOT / f'test-vectors/protocol/{v.PROTOCOL}/vectors.json')
        schema = schemas.schemas[f'urn:yamai:schema:protocol:{v.PROTOCOL}:message']
        for case in ('V18_snapshot_state', 'V104_wire_complete_game'):
            message = deepcopy(vectors[case]['positive'])
            if 'trace' in message:
                message = deepcopy(message['trace']['welcome'])
            for suffix in ('', '\n', '\r\n', '\u2028', '\u2029'):
                candidate = deepcopy(message)
                candidate['x_review_label' + suffix] = 'annotation'
                with self.subTest(case=case, suffix=repr(suffix)):
                    if suffix:
                        self.assert_error('invalid_message', schemas.validate, candidate, schema)
                    else:
                        schemas.validate(candidate, schema)

    def test_resume_token_rejects_trailing_line_terminators(self):
        schemas = v.SchemaSet()
        vectors = v.strict_load(v.ROOT / f'test-vectors/protocol/{v.PROTOCOL}/vectors.json')
        welcome = vectors['V104_wire_complete_game']['positive']['trace']['welcome']
        schema = schemas.schemas[f'urn:yamai:schema:protocol:{v.PROTOCOL}:welcome']
        for suffix in ('\n', '\r\n', '\u2028', '\u2029'):
            message = deepcopy(welcome)
            message['resume']['token'] += suffix
            self.assert_error('invalid_message', schemas.validate, message, schema)

    def test_negotiation_lexemes_match_the_entire_string(self):
        schemas = v.SchemaSet()
        examples = {'version': '1.0-draft.1', 'profileName': 'riichi-4p',
                    'profileHash': 'sha256:' + 'a' * 64, 'capabilityName': 'x-review-feature'}
        for name, value in examples.items():
            schema = {'$ref': f'urn:yamai:schema:protocol:{v.PROTOCOL}:common#/$defs/{name}'}
            schemas.validate(value, schema)
            for suffix in ('\n', '\r\n', '\u2028', '\u2029'):
                with self.subTest(name=name, suffix=repr(suffix)):
                    self.assert_error('invalid_message', schemas.validate, value + suffix, schema)

    def test_json_fraction_does_not_round_to_integer(self):
        value = v.strict_load_bytes(b'{"seq":1.00000000000000000000001}')
        self.assertEqual(value['seq'], Decimal('1.00000000000000000000001'))
        self.assert_error('invalid_message', v.SchemaSet().validate, value, {'properties': {'seq': {'type': 'integer'}}})

    def test_json_large_integer_in_exponent_notation(self):
        self.assert_error('invalid_json', v.strict_load_bytes, b'{"n":1e30}')

    def test_json_duplicate_key_after_unescaping(self):
        self.assert_error('invalid_json', v.strict_load_bytes, b'{"key":1,"k\\u0065y":2}')

    def test_json_unicode_pair_and_lone_surrogate(self):
        self.assertEqual(v.strict_load_bytes(b'{"x":"\\ud83d\\ude00"}'), {'x': '\U0001f600'})
        self.assert_error('invalid_json', v.strict_load_bytes, b'{"x":"\\ud83d"}')

    def test_container_depth_includes_empty_containers(self):
        for count in (63, 64, 65):
            raw = (b'{"x":' * (count - 1)) + b'{}' + (b'}' * (count - 1))
            if count <= 64:
                v.strict_load_bytes(raw)
            else:
                self.assert_error('resource_limit', v.strict_load_bytes, raw)

    def test_jsonl_bytewise_utf8_and_crlf(self):
        raw = '{"x":"麻雀"}\r\n{"n":1}\n'.encode()
        expected = [{'x': '麻雀'}, {'n': 1}]
        self.assertEqual(v.parse_jsonl_chunks([bytes([b]) for b in raw]), expected)
        self.assertEqual(v.parse_jsonl_chunks([raw]), expected)

    def test_jsonl_exact_maximum_payload_and_crlf(self):
        payload = b'{"x":"' + b'a' * (1048576 - 8) + b'"}'
        self.assertEqual(len(payload), 1048576)
        self.assertEqual(len(v.parse_jsonl_chunks([payload, b'\r', b'\n'])[0]['x']), 1048576 - 8)
        self.assert_error('resource_limit', v.parse_jsonl_chunks, [payload + b' \n'])

    def test_jsonl_multiple_lines_each_have_own_limit(self):
        self.assertEqual(v.parse_jsonl_chunks([b'{}\r\n{}\n'], max_bytes=2), [{}, {}])

    def test_jsonl_rejects_incomplete_and_structural_newline(self):
        for raw in (b'{}', b'{}\r', b'\n', b' {}\n', b'{\r"x":1}\n'):
            self.assert_error('invalid_frame', v.parse_jsonl_chunks, [raw])
        self.assert_error('invalid_json', v.parse_jsonl_chunks, [b'{\n"x":1}\n'])

    def test_websocket_empty_text_is_a_json_error(self):
        for payload in ('', ' ', '\r\n'):
            with self.subTest(payload=repr(payload)):
                trace = {'trace_type': 'transport', 'transport': 'websocket',
                         'message_type': 'text', 'message': payload, 'fragments': [payload]}
                self.assert_error('invalid_json', v.semantic_transport_trace, trace)
        self.assert_error('unsupported_frame', v.semantic_transport_trace,
                          {'trace_type': 'transport', 'transport': 'websocket', 'message_type': 'binary'})
        v.semantic_transport_trace({'trace_type': 'transport', 'transport': 'websocket',
                                    'message_type': 'text', 'message': '{}', 'fragments': ['', '{', '}']})

    def test_schema_bool_is_not_numeric_const_or_enum(self):
        for schema in ({'const': 1}, {'enum': [1]}, {'const': [1]}, {'enum': [[1]]}):
            value = [True] if isinstance(schema.get('const', schema.get('enum', [None])[0]), list) else True
            self.assert_error('invalid_message', v.SchemaSet().validate, value, schema)

    def test_schema_unique_items_uses_json_equality(self):
        schema = {'type': 'array', 'uniqueItems': True}
        v.SchemaSet().validate([True, 1], schema)
        self.assert_error('invalid_message', v.SchemaSet().validate, [1, Decimal('1.0')], schema)

    def test_jcs_utf16_order_and_unescaped_unicode(self):
        # RFC 8785 section 3.2.3: UTF-16 order differs from code-point order.
        value = {'\ufffd': 'replacement', '\U0001f600': 'emoji', '\r': 'control'}
        expected = '{"\\r":"control","\U0001f600":"emoji","\ufffd":"replacement"}'.encode()
        self.assertEqual(v.canonical(value), expected)

    def test_jcs_rejects_unsupported_artifact_numbers(self):
        for value in (0.1, Decimal('0.1'), 9007199254740992, float('nan')):
            self.assert_error('hash_error', v.canonical, {'x': value})

    def test_jcs_rejects_lone_surrogate_key_or_value(self):
        self.assert_error('hash_error', v.canonical, {'\ud800': 0})
        self.assert_error('hash_error', v.canonical, {'x': '\ud800'})

    def test_wire_hash_normalization_uses_identity_paths(self):
        digest = 'sha256:' + 'a' * 64
        zero = 'sha256:' + '0' * 64
        for kind in ('join', 'welcome'):
            wire = ('{ "kind": "' + kind + '", "x_test_note": "' + digest +
                    '", "profile_ha\\u0073h" : "' + digest +
                    '", "x_test_nested": [{"profile_hash":"' + digest + '"},true,3,null] }')
            expected = wire.replace('"profile_ha\\u0073h" : "' + digest + '"',
                                    '"profile_ha\\u0073h" : "' + zero + '"')
            self.assertEqual(v.normalize_wire_profile_hashes(wire), expected)
        wire = ('{ "kind":"hello", "x_test_note":"' + digest + '", "profiles":['
                '{"hashes":{"r1":"' + digest + '","r2":"invalid"},"x_test_note":"' + digest + '"},'
                '{"hashes":{"r3":"' + digest + '"}}] }')
        expected = wire.replace('"r1":"' + digest + '"', '"r1":"' + zero + '"')
        expected = expected.replace('"r3":"' + digest + '"', '"r3":"' + zero + '"')
        self.assertEqual(v.normalize_wire_profile_hashes(wire), expected)
        for wire in ('{"kind":"join","profile_hash":3}', '{"kind":[],"profile_hash":"' + digest + '"}',
                     '{"kind":"join","profile_hash":"' + digest + '","bad":}'):
            self.assertEqual(v.normalize_wire_profile_hashes(wire), wire)

    def test_registry_hash_must_be_present_and_well_formed(self):
        original_load = v.strict_load
        registry = (v.ROOT / f'registry/protocol/{v.PROTOCOL}/registry.json').resolve()
        schemas = v.SchemaSet()
        missing = object()
        for value in (missing, None, False, 42, [], {}, '', 'sha256:' + '0' * 63,
                      'sha256:' + 'A' * 64, 'sha256:' + 'a' * 64 + '\n'):
            def load(path):
                result = original_load(path)
                if Path(path).resolve() == registry:
                    if value is missing:
                        result['profiles'][0].pop('hash')
                    else:
                        result['profiles'][0]['hash'] = value
                return result
            with self.subTest(value=value), patch.object(v, 'strict_load', load):
                self.assert_error('registry_error', v.check_registry, schemas)

    def test_registry_hash_matches_computed_artifacts_at_full_gate(self):
        original_load = v.strict_load
        registry = (v.ROOT / f'registry/protocol/{v.PROTOCOL}/registry.json').resolve()
        schemas = v.SchemaSet()
        protocol, rules = v.check_registry(schemas)
        digest = v.profile_hash(protocol, rules)
        self.assertEqual(protocol['profiles'][0]['hash'], digest)
        v.check_manifest(schemas, protocol, rules)
        altered = deepcopy(protocol)
        altered['profiles'][0]['hash'] = 'sha256:' + '0' * 64
        # Hash exclusion must remain cycle-free, while the publication check
        # independently requires the excluded identity field to be correct.
        self.assertEqual(v.profile_hash(altered, rules), digest)
        self.assert_error('registry_error', v.check_manifest, schemas, altered, rules)
        def load(path):
            return deepcopy(altered) if Path(path).resolve() == registry else original_load(path)
        output = StringIO()
        with patch.object(v, 'strict_load', load), redirect_stderr(output):
            self.assertEqual(v.main(), 1)
        self.assertIn('FAIL [registry_error]', output.getvalue())

    def test_extension_action_actor_keeps_core_integer_seat_constraint(self):
        manifest = v.strict_load(v.ROOT / f'test-vectors/protocol/{v.PROTOCOL}/manifest.json')
        vectors = v.strict_load(v.ROOT / manifest['vectors'])
        base = vectors['V131_private_action_preserves_owner_binding']['positive']['trace']
        for actor in (0, False, True, None, -1, 4, '0', 1):
            trace = deepcopy(base)
            # A negotiated extension may use a permissive schema. The core
            # owner/seat assertion still applies to every action candidate.
            trace['definitions'][0]['action_types']['x-acme-policy']['schema']['properties']['actor'] = {}
            trace['message']['legal_actions'][-1]['action']['actor'] = actor
            trace['message'] = v.strict_load_bytes(json.dumps(trace['message']).encode())
            with self.subTest(actor=actor):
                if type(actor) is int and actor == 0:
                    v.semantic_session_trace(trace, manifest['profile_hash'])
                else:
                    self.assert_error('invalid_message', v.semantic_session_trace,
                                      trace, manifest['profile_hash'])
        for seat in range(4):
            v._check_action_object({'type': 'x-acme-policy', 'actor': seat},
                                   expected_actor=seat, extension_types={'x-acme-policy'})

    def test_anyof_does_not_skip_sibling_constraints(self):
        schema = {'anyOf':[{'type':'integer'},{'type':'string'}], 'const':3}
        v.SchemaSet().validate(3, schema)
        self.assert_error('invalid_message', v.SchemaSet().validate, 'wrong', schema)

    def test_boolean_schemas(self):
        v.SchemaSet().validate({'arbitrary':1}, True)
        self.assert_error('invalid_message', v.SchemaSet().validate, {'arbitrary':1}, False)

    def test_boolean_schema_references(self):
        schemas = v.SchemaSet()
        for allowed in (True, False):
            schema = {'$defs': {'gate': allowed}, '$ref': '#/$defs/gate'}
            schemas.schemas = {'urn:test': schema}
            schemas.check_refs()
            if allowed:
                schemas.validate(1, schema)
            else:
                self.assert_error('invalid_message', schemas.validate, 1, schema)

    def test_schema_errors_are_not_branch_mismatches(self):
        schemas = v.SchemaSet()
        for broken in ({'$ref': 'urn:missing'}, {'type': 'unknown'}):
            for schema, value in (
                ({'anyOf': [broken, True]}, 1),
                ({'oneOf': [True, broken]}, 1),
                ({'not': broken}, 1),
                ({'if': broken, 'else': True}, 1),
                ({'contains': broken, 'minContains': 0}, [1]),
            ):
                with self.subTest(schema=schema):
                    self.assert_error('schema_error', schemas.validate, value, schema)

    def test_strict_load_accepts_relative_and_external_paths(self):
        self.assertEqual(v.strict_load(Path('release-manifest.json')),
                         v.strict_load(v.ROOT / 'release-manifest.json'))
        with TemporaryDirectory() as directory:
            source = Path(directory) / 'input.json'
            source.write_text('{"value": 1}', encoding='utf-8')
            self.assertEqual(v.strict_load(source), {'value': 1})
            source.write_text('{"value": NaN}', encoding='utf-8')
            self.assert_error('invalid_json', v.strict_load, source)

    def test_schema_literal_members_are_not_schema_keywords(self):
        schemas = v.SchemaSet()
        schemas.schemas = {'urn:test':{'type':'object','properties':{'prefixItems':{'type':'integer'},'$ref':{'const':{'$ref':'not-a-schema'}}},'additionalProperties':False}}
        schemas.check_keyword_support()
        schemas.check_refs()
        schemas.validate({'prefixItems':1,'$ref':{'$ref':'not-a-schema'}}, schemas.schemas['urn:test'])

    def test_unknown_schema_type_is_rejected_before_use(self):
        schemas = v.SchemaSet()
        schemas.schemas = {'urn:test':{'type':'unrecognized'}}
        self.assert_error('schema_error', schemas.check_keyword_support)

    def test_depth_limit_precedes_decoder_recursion_failure(self):
        self.assert_error('resource_limit', v.strict_load_bytes, b'['*2000+b'0'+b']'*2000)
        self.assertEqual(v.strict_load_bytes(b'{"text":"[\\\"{}]"}'), {'text':'["{}]'})


class RequestPayloadBoundaries(unittest.TestCase):
    assert_error = ValidatorBoundaries.assert_error

    @classmethod
    def setUpClass(cls):
        cls.manifest = v.strict_load(v.ROOT / f'test-vectors/protocol/{v.PROTOCOL}/manifest.json')
        cls.vector_path = (v.ROOT / cls.manifest['vectors']).resolve()
        cls.vectors = v.strict_load(cls.vector_path)

    def trace(self, key='V38_chombo_cancels_offender'):
        return deepcopy(self.vectors[key]['positive']['trace'])

    def check_single_vector(self, key, trace, *, negative=False):
        case = deepcopy(self.vectors[key])
        case['negative' if negative else 'positive'] = {'trace': trace}
        manifest = dict(self.manifest, cases=[entry for entry in self.manifest['cases'] if entry['id'] == key])
        original_load = v.strict_load
        def load(path):
            return {key: case} if path.resolve() == self.vector_path else original_load(path)
        with patch.object(v, 'strict_load', load):
            return v.check_vectors(v.SchemaSet(), manifest)

    def test_group_descriptor_order_is_not_semantic(self):
        for order in permutations(range(3)):
            trace = self.trace()
            for request in trace['requests']:
                for member in request['decision_group_members']:
                    member['x_test_detail'] = {'seat_data': [member['seat'], True], 'label': 'kept'}
            members = trace['requests'][1]['decision_group_members']
            trace['requests'][1]['decision_group_members'] = [members[i] for i in order]
            v.semantic_lifecycle_trace(trace)
            self.assertEqual(self.check_single_vector('V38_chombo_cancels_offender', trace), 1)
        for change in ('metadata', 'duplicate', 'identity', 'nested_array'):
            trace = self.trace()
            for request in trace['requests']:
                for member in request['decision_group_members']:
                    member['x_test_detail'] = [True, 1]
            members = trace['requests'][1]['decision_group_members']
            if change == 'metadata':
                members[0]['x_test_detail'] = [1, 1]
            elif change == 'duplicate':
                members[0] = deepcopy(members[1])
            elif change == 'identity':
                members[0]['request_id'] = 'other'
            else:
                members[0]['x_test_detail'].reverse()
            self.assert_error('invalid_message', v.semantic_lifecycle_trace, trace)

    def test_snapshot_group_descriptor_order_preserves_all_metadata(self):
        from session_contract import Receiver, SessionError
        template = next(case['positive']['trace'] for key, case in self.vectors.items()
                        if key.endswith('_snapshot_group_remaining_cannot_increase'))
        for change in ('order', 'metadata', 'duplicate', 'identity', 'nested_array'):
            trace = deepcopy(template)
            for step in trace['steps']:
                for member in step['message']['state']['pending_requests'][0]['decision_group_members']:
                    member['x_test_detail'] = [True, 1]
            members = trace['steps'][1]['message']['state']['pending_requests'][0]['decision_group_members']
            members.reverse()
            if change == 'metadata':
                members[0]['x_test_detail'] = [1, 1]
            elif change == 'duplicate':
                members[0] = deepcopy(members[1])
            elif change == 'identity':
                members[0]['request_id'] = 'other'
            elif change == 'nested_array':
                members[0]['x_test_detail'].reverse()
            receiver = Receiver(trace['welcome'], v.strict_load_bytes,
                                v._session_schema_validator(v.SchemaSet(), self.manifest['profile_hash']))
            receiver.receive(json.dumps(trace['steps'][0]['message']).encode())
            raw = json.dumps(trace['steps'][1]['message']).encode()
            with self.subTest(change=change):
                if change == 'order':
                    self.assertEqual(receiver.receive(raw), 'applied')
                else:
                    with self.assertRaises((SessionError, v.ArtifactError)) as caught:
                        receiver.receive(raw)
                    self.assertEqual(caught.exception.code, 'invalid_message')

    def test_payload_schema_validates_every_request_field(self):
        schemas = v.SchemaSet()
        schema = v.request_payload_schema(schemas)
        request = self.trace()['requests'][0]
        schemas.validate(request, schema)
        for field in request:
            candidate = deepcopy(request)
            del candidate[field]
            self.assert_error('invalid_message', schemas.validate, candidate, schema)
        mutations = [
            ('request_id', 'bad id'), ('seat', True), ('seat', 4), ('caused_by_seq', 0),
            ('caused_by_seq', True), ('timeout_ms', -1), ('timeout_ms', 600001),
            ('timeout_ms', True), ('time_bank_ms', -1), ('time_bank_ms', 600001),
            ('time_bank_ms', True), ('decision_group_id', ''),
            ('decision_group_deadline_ms', -1), ('decision_group_deadline_ms', 1200001),
            ('decision_group_deadline_ms', True), ('decision_group_close', 'changed'),
            ('default_action_id', ''), ('legal_actions', []), ('unknown', 1),
            ('x_bad', 1), ('x_test_note\n', 1), ('yamai', 'wrong'), ('kind', 'ack'),
            ('session_id', ''), ('game_id', ''), ('seq', False), ('seq', 0),
        ]
        for field, value in mutations:
            with self.subTest(field=field, value=value):
                self.assert_error('invalid_message', schemas.validate, dict(request, **{field: value}), schema)
        for location in ('member', 'candidate', 'action'):
            candidate = deepcopy(request)
            target = (candidate['decision_group_members'][0] if location == 'member' else
                      candidate['legal_actions'][0] if location == 'candidate' else
                      candidate['legal_actions'][0]['action'])
            target['x_test_note'] = {'values': [True, 1]}
            schemas.validate(candidate, schema)
            target['unknown'] = 1
            self.assert_error('invalid_message', schemas.validate, candidate, schema)
        supplied_envelope = dict(request, yamai=v.PROTOCOL, kind='request', session_id='s', game_id='g', seq=2)
        schemas.validate(supplied_envelope, schema)
        schemas.validate(supplied_envelope, {'$ref': f'urn:yamai:schema:protocol:{v.PROTOCOL}:request'})

    def test_public_request_routes_reject_schema_invalid_payloads(self):
        from request_contract import evaluate
        for key in ('V38_chombo_cancels_offender', 'V31_group_grace_zero'):
            for field, value in (('timeout_ms', -1), ('time_bank_ms', True),
                                 ('decision_group_deadline_ms', 1200001)):
                trace = self.trace(key)
                for request in trace['requests'] if field == 'decision_group_deadline_ms' else trace['requests'][:1]:
                    request[field] = value
                if trace['trace_type'] == 'request_lifecycle':
                    trace['expected'] = evaluate(trace)[-1]
                    trace.pop('checkpoints', None)
                with self.subTest(key=key, field=field):
                    self.assert_error('invalid_message', self.check_single_vector, key, trace)
                    # The same malformed payload on the negative route must
                    # genuinely fail, even with its observations recomputed.
                    self.assertEqual(self.check_single_vector(key, trace, negative=True), 1)

    def test_lifecycle_schema_boundaries_remain_legal(self):
        from request_contract import evaluate
        for timeout, bank in ((0, 0), (600000, 0), (0, 600000), (600000, 600000)):
            trace = self.trace()
            trace['grace_ms'] = 0
            for request in trace['requests']:
                request.update(timeout_ms=timeout, time_bank_ms=bank, decision_group_deadline_ms=timeout + bank)
            trace['expected'] = evaluate(trace)[-1]
            trace.pop('checkpoints', None)
            v.semantic_lifecycle_trace(trace)
            self.assertEqual(self.check_single_vector('V38_chombo_cancels_offender', trace), 1)

    def test_independent_checker_reaches_fixture_request_payloads(self):
        try:
            import check_jsonschema
            from jsonschema import ValidationError
        except ImportError:
            self.skipTest('independent schema check requires optional jsonschema package')
        original_load = v.strict_load
        for key in ('V38_chombo_cancels_offender', 'V31_group_grace_zero'):
            case = deepcopy(self.vectors[key])
            def load(path):
                return {key: case} if path.resolve() == self.vector_path else original_load(path)
            for field, value in (('timeout_ms', -1), ('time_bank_ms', True),
                                 ('decision_group_deadline_ms', 1200001)):
                case = deepcopy(self.vectors[key])
                case['positive']['trace']['requests'][0][field] = value
                with patch.object(v, 'strict_load', load), redirect_stdout(StringIO()):
                    with self.assertRaises(ValidationError):
                        check_jsonschema.main()
                case = deepcopy(self.vectors[key])
                case['negative']['trace']['requests'][0][field] = value
                with patch.object(v, 'strict_load', load), redirect_stdout(StringIO()):
                    check_jsonschema.main()
                    case['negative_expect'] = 'invalid_action'
                    with self.assertRaises(AssertionError):
                        check_jsonschema.main()


if __name__ == '__main__':
    unittest.main()
