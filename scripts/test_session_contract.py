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

    def test_lost_state_resume_replays_initial_then_current_scores(self):
        trace = self.trace('lost_state_replay_uses_initial_scores')
        receiver = self.receiver(trace['welcome'])
        first = trace['steps'][0]['message']
        self.assertNotEqual(first['event']['scores'], trace['welcome']['scores'])
        receiver.receive(self.raw(first))
        self.assertEqual(receiver.game.scores, [25000] * 4)
        for step in trace['steps'][1:]:
            receiver.receive(self.raw(step['message']))
        self.assertTrue(receiver.ended)
        self.assertEqual(receiver.game.scores, trace['welcome']['scores'])

    def test_between_round_snapshot_allows_next_round_and_draw(self):
        trace = self.trace('wire_complete_game')
        for kyoku, oya, honba in [(1, 0, 3), (2, 1, 0)]:
            with self.subTest(kyoku=kyoku):
                snapshot = deepcopy(self.vectors['V57_snapshot_between_kyoku_deposits']['positive'])
                snapshot['state']['next_kyoku'].update(kyoku=kyoku, oya=oya, honba=honba)
                self.assertNotIn('type', snapshot['state']['next_kyoku'])
                welcome = deepcopy(trace['welcome'])
                welcome.update(resumed=True, replay_from_seq=1, replay_through_seq=3,
                               scores=snapshot['state']['scores'])
                receiver = self.receiver(welcome)
                receiver.receive(self.raw(snapshot))
                start = deepcopy(trace['steps'][1]['message'])
                start['seq'] = 5
                start['event'].update(snapshot['state']['next_kyoku'], scores=snapshot['state']['scores'])
                receiver.receive(self.raw(start))
                draw = deepcopy(trace['steps'][2]['message'])
                draw['seq'] = 6
                draw['event'].update(actor=oya, pai='9s' if oya == 0 else None)
                receiver.receive(self.raw(draw))
                self.assertEqual((receiver.applied, receiver.game.game_phase), (6, 'in_kyoku'))
                self.assertEqual(receiver.game.round['wall_remaining'], 69)

    def test_between_round_snapshot_rejects_premature_end_game(self):
        trace = self.trace('wire_complete_game')
        snapshot = deepcopy(self.vectors['V57_snapshot_between_kyoku_deposits']['positive'])
        welcome = deepcopy(trace['welcome'])
        welcome.update(resumed=True, replay_from_seq=1, replay_through_seq=3,
                       scores=snapshot['state']['scores'])
        receiver = self.receiver(welcome)
        receiver.receive(self.raw(snapshot))
        end = deepcopy(trace['steps'][-1]['message'])
        end['seq'] = 5
        end['event'].update(scores=snapshot['state']['scores'], kyotaku=3, rankings=[4, 1, 2, 3])
        with self.assertRaises(SessionError) as error:
            receiver.receive(self.raw(end))
        self.assertEqual(error.exception.code, 'invalid_message')
        self.assertEqual((receiver.applied, receiver.game.game_phase), (4, 'between_kyoku'))

    def test_duplicate_payload_conflict_precedes_schema_but_new_seq_does_not(self):
        trace = self.trace('wire_complete_game')
        initial = trace['steps'][0]['message']
        for seq, code in [(1, 'sequence_conflict'), (2, 'invalid_message'), (3, 'invalid_message')]:
            with self.subTest(seq=seq):
                receiver = self.receiver(trace['welcome'])
                receiver.receive(self.raw(initial))
                self.assertEqual(receiver.receive(self.raw(initial)), 'duplicate')
                bad = deepcopy(initial)
                bad['seq'] = seq
                bad['event']['unknown_standard_member'] = True
                with self.assertRaises(SessionError) as error:
                    receiver.receive(self.raw(bad))
                self.assertEqual(error.exception.code, code)
                self.assertEqual(receiver.applied, 1)
                self.assertEqual(receiver.game.scores, [25000] * 4)

    def test_ankan_never_still_requires_each_other_seats_request_and_ack(self):
        for seat in (1, 2, 3):
            for skip_reaction in (False, True):
                with self.subTest(seat=seat, skip_reaction=skip_reaction):
                    trace = self.trace('wire_complete_game')
                    welcome = trace['welcome']
                    welcome['seat'] = seat
                    welcome['rules']['ankan_chankan'] = 'never'
                    receiver = self.receiver(welcome)
                    start, deal, draw = [step['message'] for step in trace['steps'][:3]]
                    start['event']['rules'] = deepcopy(welcome['rules'])
                    hands = deal['event']['hands']
                    hands[0], hands[seat] = hands[seat], hands[0]
                    draw['event']['pai'] = None
                    for message in (start, deal, draw):
                        receiver.receive(self.raw(message))
                    envelope = {key: draw[key] for key in ('yamai', 'session_id', 'game_id')}
                    declared = {**envelope, 'kind':'event', 'seq':4,
                                'event':{'type':'ankan_declared', 'actor':0, 'consumed':['P']*4}}
                    committed = deepcopy(declared)
                    committed['event']['type'] = 'ankan'
                    receiver.receive(self.raw(declared))
                    if skip_reaction:
                        committed['seq'] = 5
                        with self.assertRaises(SessionError) as error:
                            receiver.receive(self.raw(committed))
                        self.assertEqual(error.exception.code, 'invalid_message')
                        self.assertEqual(receiver.game.round['kan_counts'], [0]*4)
                        continue
                    rid = f'kan-r{seat}'
                    request = {**envelope, 'kind':'request', 'seq':5, 'request_id':rid, 'seat':seat,
                               'caused_by_seq':4, 'timeout_ms':1000, 'time_bank_ms':15000,
                               'legal_actions':[{'action_id':'pass', 'action':{'type':'none'}}],
                               'default_action_id':'pass', 'decision_group_id':'kan-group',
                               'decision_group_members':[{'request_id':f'kan-r{s}', 'seat':s} for s in (1,2,3)],
                               'decision_group_deadline_ms':19000, 'decision_group_close':'all_selected_or_deadline'}
                    ack = {**envelope, 'kind':'ack', 'seq':6, 'request_id':rid, 'action_id':'pass',
                           'status':'passed', 'elapsed_ms':10, 'time_bank_ms':15000}
                    committed['seq'] = 7
                    dora = {**envelope, 'kind':'event', 'seq':8, 'event':{'type':'dora', 'dora_marker':'8m'}}
                    rinshan = {**envelope, 'kind':'event', 'seq':9, 'event':{'type':'tsumo', 'actor':0, 'pai':None}}
                    for message in (request, ack, committed, dora, rinshan):
                        receiver.receive(self.raw(message))
                    self.assertEqual(receiver.game.round['kan_counts'], [1,0,0,0])
                    self.assertEqual(receiver.game.round['wall_remaining'], 68)
                    self.assertFalse(receiver.active_requests)

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
