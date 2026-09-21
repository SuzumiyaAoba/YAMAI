"""Regression checks for the artifact checker; run with Python's unittest."""

import unittest
from copy import deepcopy
from decimal import Decimal

import validate_artifacts as v


class ValidatorBoundaries(unittest.TestCase):
    def assert_error(self, code, operation, *args, **kwargs):
        with self.assertRaises(v.ArtifactError) as caught:
            operation(*args, **kwargs)
        self.assertEqual(caught.exception.code, code)

    def test_json_integer_spellings(self):
        for raw in (b'1', b'1.0', b'1e0'):
            with self.subTest(raw=raw):
                value = v.strict_load_bytes(b'{"seq":' + raw + b'}')
                self.assertEqual(type(value['seq']), int)
                self.assertEqual(value['seq'], 1)

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

    def test_anyof_does_not_skip_sibling_constraints(self):
        schema = {'anyOf':[{'type':'integer'},{'type':'string'}], 'const':3}
        v.SchemaSet().validate(3, schema)
        self.assert_error('invalid_message', v.SchemaSet().validate, 'wrong', schema)

    def test_boolean_schemas(self):
        v.SchemaSet().validate({'arbitrary':1}, True)
        self.assert_error('invalid_message', v.SchemaSet().validate, {'arbitrary':1}, False)

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


if __name__ == '__main__':
    unittest.main()
