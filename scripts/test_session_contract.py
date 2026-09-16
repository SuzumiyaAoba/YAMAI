"""Session isolation and atomic recovery checks beyond individual goldens."""
from copy import deepcopy
import json
import unittest

import validate_artifacts as v
from session_contract import Receiver, SessionError


class SessionInvariants(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schemas = v.SchemaSet()
        manifest = v.strict_load(v.ROOT / f'test-vectors/yrc-0003/{v.PROTOCOL}/manifest.json')
        cls.digest = manifest['profile_hash']
        cls.vectors = v.strict_load(v.ROOT / manifest['vectors'])

    def trace(self, suffix):
        return deepcopy(next(case['positive']['trace'] for key,case in self.vectors.items() if key.endswith('_'+suffix)))

    def receiver(self, welcome, **options):
        return Receiver(welcome, v.strict_load_bytes, v._session_schema_validator(self.schemas, self.digest), **options)

    @staticmethod
    def raw(message):
        return json.dumps(message, ensure_ascii=False, separators=(',',':')).encode()

    def test_extension_activation_does_not_leak_between_sessions(self):
        trace = self.trace('private_event_requires_negotiation')
        active = v._session_schema_validator(self.schemas, self.digest, trace['definitions'], trace['enabled_capabilities'])
        active('host-application', trace['message'])
        plain = v._session_schema_validator(self.schemas, self.digest)
        with self.assertRaises(SessionError):
            plain('host-application', trace['message'])

    def test_failed_event_does_not_advance_recording_cursor(self):
        trace = self.trace('snapshot_restores_recording_cursor')
        receiver = self.receiver(trace['welcome'])
        initial = trace['steps'][0]['message']
        receiver.receive(self.raw(initial))
        repeated_start = deepcopy(initial)
        repeated_start.update(seq=2, original_seq=2)
        with self.assertRaises(SessionError):
            receiver.receive(self.raw(repeated_start))
        self.assertEqual((receiver.applied, receiver.original_seq, sorted(receiver.known)), (1,1,[1]))

    def test_empty_resume_accepts_one_snapshot_without_waiting_forever(self):
        trace = self.trace('wire_complete_game')
        receiver = self.receiver(trace['welcome'])
        for step in trace['steps'][:4]:
            receiver.receive(self.raw(step['message']))
        welcome = deepcopy(trace['welcome'])
        welcome.update(resumed=True, replay_from_seq=5, replay_through_seq=4)
        welcome['resume']['token'] = 'rt_DDDDDDDDDDDDDDDDDDDDDD'
        receiver.begin_resume(welcome)
        self.assertIsNone(receiver.recovery)
        self.assertEqual(receiver.active_requests, {'r1'})
        snapshot = deepcopy(self.vectors['V18_snapshot_state']['positive'])
        self.assertEqual(receiver.receive(self.raw(snapshot)), 'applied')
        self.assertEqual(receiver.receive(self.raw(snapshot)), 'duplicate')
        snapshot.update(seq=6, replaces_through_seq=5)
        with self.assertRaises(SessionError):
            receiver.receive(self.raw(snapshot))
        self.assertEqual(receiver.applied, 5)

    def test_fatal_session_cannot_be_reopened_by_resume(self):
        trace = self.trace('fatal_error_stops_buffered_messages')
        receiver = self.receiver(trace['welcome'])
        for step in trace['steps'][:2]:
            receiver.receive(self.raw(step['message']))
        welcome = deepcopy(trace['welcome'])
        welcome.update(resumed=True, replay_from_seq=3, replay_through_seq=2)
        with self.assertRaises(SessionError) as error:
            receiver.begin_resume(welcome)
        self.assertEqual(error.exception.code, 'resume_unavailable')
        self.assertEqual(receiver.applied, 2)

    def test_hash_normalizes_only_wire_identity_values(self):
        protocol = v.strict_load(v.ROOT / f'registry/yrc-0003/{v.PROTOCOL}/registry.json')
        rules = v.strict_load(v.ROOT / f'registry/yrc-0005/{v.PROFILE_REVISION}/registry.json')
        one, two = deepcopy(protocol), deepcopy(protocol)
        one['x_test_message'] = {'kind':'join','profile_hash':'sha256:'+'a'*64}
        two['x_test_message'] = {'kind':'join','profile_hash':'sha256:'+'b'*64}
        self.assertEqual(v.profile_hash(one, rules), v.profile_hash(two, rules))
        one['x_test_message'].pop('kind')
        two['x_test_message'].pop('kind')
        self.assertNotEqual(v.profile_hash(one, rules), v.profile_hash(two, rules))

    def test_hash_preserves_schema_property_definitions(self):
        protocol = v.strict_load(v.ROOT / f'registry/yrc-0003/{v.PROTOCOL}/registry.json')
        rules = v.strict_load(v.ROOT / f'registry/yrc-0005/{v.PROFILE_REVISION}/registry.json')
        protocol['x_test_schema'] = {'type':'object','properties':{'kind':{'const':'join'},'profile_hash':{'type':'string'}}}
        before = v.profile_hash(protocol, rules)
        protocol['x_test_schema']['properties']['profile_hash'] = {'type':'integer'}
        self.assertNotEqual(before, v.profile_hash(protocol, rules))

    def test_wire_hash_normalization_preserves_other_bytes(self):
        protocol = v.strict_load(v.ROOT / f'registry/yrc-0003/{v.PROTOCOL}/registry.json')
        rules = v.strict_load(v.ROOT / f'registry/yrc-0005/{v.PROFILE_REVISION}/registry.json')
        one, two = deepcopy(protocol), deepcopy(protocol)
        one['x_test_capture'] = {'wire':json.dumps({'kind':'join','profile_hash':'sha256:'+'a'*64})}
        two['x_test_capture'] = {'wire':json.dumps({'kind':'join','profile_hash':'sha256:'+'b'*64})}
        self.assertEqual(v.profile_hash(one,rules),v.profile_hash(two,rules))
        two['x_test_capture']['wire'] += ' '
        self.assertNotEqual(v.profile_hash(one,rules),v.profile_hash(two,rules))


if __name__ == '__main__':
    unittest.main()
