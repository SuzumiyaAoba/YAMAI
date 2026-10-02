"""Session isolation and atomic recovery checks beyond individual goldens."""
from copy import deepcopy
from decimal import Decimal
import json
import unittest
from unittest.mock import patch

import validate_artifacts as v
from session_contract import Receiver, SessionError, check_token_trace, classify_player_input, negotiate, replay_plan
from scoring_reference import basic_points, normal_payments, NORMAL_YAKU_HAN


class SessionInvariants(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schemas = v.SchemaSet()
        manifest = v.strict_load(v.ROOT / f'test-vectors/protocol/{v.PROTOCOL}/manifest.json')
        cls.digest = manifest['profile_hash']
        cls.vectors = v.strict_load(v.ROOT / manifest['vectors'])

    def trace(self, suffix):
        return deepcopy(next(case['positive']['trace'] for key,case in self.vectors.items() if key.endswith('_'+suffix)))

    def receiver(self, welcome, **options):
        return Receiver(welcome, v.strict_load_bytes, v._session_schema_validator(self.schemas, self.digest), **options)

    @staticmethod
    def raw(message):
        return json.dumps(message, ensure_ascii=False, separators=(',',':')).encode()

    def test_resume_immutable_facts_use_json_value_equality(self):
        for field in ('rules', 'players'):
            for old, new, valid in ((True, 1, False), (False, 0, False),
                                    (1, Decimal('1.0'), True), (True, True, True)):
                trace = self.trace('resume_preserves_rules')
                previous = trace['context']['resume_state']['welcome']
                old_object = previous['rules'] if field == 'rules' else previous['players'][0]
                new_object = trace['welcome']['rules'] if field == 'rules' else trace['welcome']['players'][0]
                old_object['x_acme_data'] = {'values': [old], 'label': 'unchanged'}
                new_object['x_acme_data'] = {'label': 'unchanged', 'values': [new]}
                validate = v._session_schema_validator(self.schemas, self.digest)
                with self.subTest(field=field, old=old, new=new):
                    receiver = self.receiver(previous, last_seq=trace['join']['resume']['last_seq'])
                    if valid:
                        negotiate(trace['hello'], trace['join'], trace['welcome'], trace['context'],
                                  validate, v.PROTOCOL, v.PROFILE_REVISION, self.digest)
                        receiver.begin_resume(trace['welcome'])
                    else:
                        with self.assertRaises(SessionError) as caught:
                            negotiate(trace['hello'], trace['join'], trace['welcome'], trace['context'],
                                      validate, v.PROTOCOL, v.PROFILE_REVISION, self.digest)
                        self.assertEqual(caught.exception.code, 'invalid_message')
                        with self.assertRaises(SessionError) as caught:
                            receiver.begin_resume(trace['welcome'])
                        self.assertEqual(caught.exception.code, 'invalid_message')

    def test_start_game_immutable_facts_preserve_private_json_types(self):
        for field in ('rules', 'players'):
            for old, new, valid in ((True, 1, False), (False, 0, False), (1, 1.0, True), (True, True, True)):
                welcome = deepcopy(self.vectors['V104_wire_complete_game']['positive']['trace']['welcome'])
                target = welcome['rules'] if field == 'rules' else welcome['players'][0]
                target['x_acme_data'] = {'values': [old]}
                event = {'type': 'start_game', 'players': deepcopy(welcome['players']),
                         'rules': deepcopy(welcome['rules']), 'scores': deepcopy(welcome['scores'])}
                changed = event['rules'] if field == 'rules' else event['players'][0]
                changed['x_acme_data']['values'][0] = new
                message = {'yamai': welcome['yamai'], 'kind': 'event', 'session_id': welcome['session_id'],
                           'game_id': welcome['game_id'], 'seq': 1, 'event': event}
                receiver = self.receiver(welcome)
                with self.subTest(field=field, old=old, new=new):
                    if valid:
                        self.assertEqual(receiver.receive(self.raw(message)), 'applied')
                    else:
                        with self.assertRaises(SessionError) as caught:
                            receiver.receive(self.raw(message))
                        self.assertEqual(caught.exception.code, 'invalid_message')

    def test_declared_support_uses_exact_json_values(self):
        for boundary in ('rules', 'view'):
            for supported, offered, valid in ((True, 1, False), (False, 0, False),
                                              (1, Decimal('1.0'), True), (True, True, True)):
                if boundary == 'rules':
                    trace = self.trace('resume_preserves_rules')
                    trace['context']['resume_state']['welcome']['rules']['x_acme_rule'] = offered
                    trace['welcome']['rules']['x_acme_rule'] = offered
                    trace['context']['supported_rules'] = {'x_acme_rule': [supported]}
                    code = 'unsupported_rules'
                else:
                    trace = self.trace('replay_recording_target')
                    trace['join']['view']['x_acme_option'] = offered
                    trace['welcome']['view']['x_acme_option'] = offered
                    allowed = deepcopy(trace['join']['view'])
                    allowed['x_acme_option'] = supported
                    trace['context']['supported_views'] = {'replay': [allowed]}
                    code = 'unsupported_view'
                validate = v._session_schema_validator(self.schemas, self.digest)
                with self.subTest(boundary=boundary, supported=supported, offered=offered):
                    if valid:
                        negotiate(trace['hello'], trace['join'], trace['welcome'], trace['context'],
                                  validate, v.PROTOCOL, v.PROFILE_REVISION, self.digest)
                    else:
                        with self.assertRaises(SessionError) as caught:
                            negotiate(trace['hello'], trace['join'], trace['welcome'], trace['context'],
                                      validate, v.PROTOCOL, v.PROFILE_REVISION, self.digest)
                        self.assertEqual(caught.exception.code, code)

    def test_snapshot_issued_candidate_preserves_private_json_types(self):
        for old, new, valid in ((True, 1, False), (False, 0, False), (1, 1.0, True), (True, True, True)):
            welcome, messages = self.snapshot_history()
            issued = messages[3]
            self.assertEqual(issued['kind'], 'request')
            issued['legal_actions'][0]['action']['x_acme_data'] = {'value': old}
            receiver = self.receiver(welcome)
            for message in messages[:4]:
                receiver.receive(self.raw(message))
            snapshot = messages[4]
            snapshot['state']['pending_requests'][0]['legal_actions'][0]['action']['x_acme_data'] = {'value': new}
            with self.subTest(old=old, new=new):
                if valid:
                    self.assertEqual(receiver.receive(self.raw(snapshot)), 'applied')
                else:
                    with self.assertRaisesRegex(SessionError, 'changed an issued request'):
                        receiver.receive(self.raw(snapshot))

    def test_final_round_requires_immediate_end_game_continuation(self):
        trace = self.trace('wire_complete_game')
        final_index = next(i for i, step in enumerate(trace['steps'])
                           if step['message'].get('event', {}).get('type') == 'end_game')

        def waiting():
            receiver = self.receiver(trace['welcome'])
            for step in trace['steps'][:final_index]:
                receiver.receive(self.raw(step['message']))
            self.assertTrue(receiver.end_game_due)
            return receiver

        final = trace['steps'][final_index]['message']
        receiver = waiting()
        prior = trace['steps'][final_index - 1]['message']
        self.assertEqual(receiver.receive(self.raw(prior)), 'duplicate')
        self.assertTrue(receiver.end_game_due)
        self.assertEqual(receiver.receive(self.raw(final)), 'applied')
        self.assertTrue(receiver.ended)
        self.assertFalse(receiver.end_game_due)

        identity = {key: final[key] for key in ('yamai', 'session_id', 'game_id', 'seq')}
        error = dict(identity, kind='error', code='invalid_action', severity='recoverable', message='diagnosis')
        receiver = waiting()
        self.assert_rejected_atomically(receiver, error)
        receiver = waiting()
        fatal = {**error, 'code': 'internal_error', 'severity': 'fatal'}
        self.assertEqual(receiver.receive(self.raw(fatal)), 'applied')
        self.assertTrue(receiver.closed)

        # Only a recovery jump covering the missing final event may replace
        # this continuation, and it must preserve the terminal game outcome.
        snapshot = deepcopy(self.vectors['V58_snapshot_ended_rankings']['positive'])
        snapshot.update(identity, seq=final['seq'], replaces_through_seq=final['seq'] - 1)
        snapshot['state'].update(players=trace['welcome']['players'], scores=final['event']['scores'],
                                 kyotaku=final['event']['kyotaku'], final_rankings=final['event']['rankings'],
                                 time_bank_ms=waiting().time_bank_ms)
        receiver = waiting()
        self.assert_rejected_atomically(receiver, snapshot)
        receiver = waiting()
        self.assertEqual(receiver.receive(self.raw({**error, 'seq': final['seq'] + 1})), 'sequence_gap')
        snapshot.update(seq=final['seq'] + 2, replaces_through_seq=final['seq'] + 1)
        altered = deepcopy(snapshot)
        altered['state']['time_bank_ms'] = 0 if snapshot['state']['time_bank_ms'] else 1
        self.assert_rejected_atomically(receiver, altered)
        receiver = waiting()
        self.assertEqual(receiver.receive(self.raw({**error, 'seq': final['seq'] + 1})), 'sequence_gap')
        altered = deepcopy(snapshot)
        altered['state']['scores'][0] += 100
        altered['state']['scores'][1] -= 100
        order = sorted(range(4), key=lambda seat: (-altered['state']['scores'][seat], seat))
        altered['state']['final_rankings'] = [order.index(seat) + 1 for seat in range(4)]
        self.assert_rejected_atomically(receiver, altered)
        receiver = waiting()
        self.assertEqual(receiver.receive(self.raw({**error, 'seq': final['seq'] + 1})), 'sequence_gap')
        self.assertEqual(receiver.receive(self.raw(snapshot)), 'applied')
        self.assertTrue(receiver.ended)
        self.assertFalse(receiver.end_game_due)

    def test_future_fatal_error_closes_without_applying_missing_prefix(self):
        for suffix in ('visibility_play_self', 'visibility_spectate_public',
                       'visibility_replay_public', 'visibility_replay_full',
                       'visibility_replay_seat_0', 'visibility_replay_seat_1',
                       'visibility_replay_seat_2', 'visibility_replay_seat_3'):
            case = next(c for key, c in self.vectors.items() if key.endswith('_' + suffix))
            welcome = deepcopy(case['positive']['trace']['welcome'])
            for code in ('resume_unavailable', 'internal_error', 'resource_limit'):
                with self.subTest(view=suffix, code=code):
                    receiver = self.receiver(welcome, last_seq=1)
                    before = deepcopy({k: value for k, value in vars(receiver).items()
                                       if k not in {'welcome', 'decode', 'validate', 'game'}})
                    game_before = deepcopy(vars(receiver.game))
                    fatal = {key: welcome[key] for key in ('yamai', 'session_id', 'game_id')}
                    fatal.update(kind='error', seq=4, code=code, severity='fatal', message='Recovery failed')
                    self.assertEqual(receiver.receive(self.raw(fatal)), 'fatal')
                    self.assertTrue(receiver.closed)
                    self.assertEqual(receiver.fatal_error, fatal)
                    self.assertEqual(vars(receiver.game), game_before)
                    for key, value in before.items():
                        if key not in {'closed', 'fatal_error'}:
                            self.assertEqual(getattr(receiver, key), value, key)
                    self.assertEqual(receiver.receive(self.raw(fatal)), 'closed')
                    with self.assertRaises(SessionError) as error:
                        receiver.begin_resume(welcome)
                    self.assertEqual(error.exception.code, 'resume_unavailable')

    def test_live_group_deadline_includes_negotiated_grace_before_gap(self):
        trace = deepcopy(self.vectors['V272_ankan_never_requires_reactions']['positive']['trace'])
        request = trace['steps'][4]['message']
        floor = (trace['welcome']['rules']['time_control']['grace_ms']
                 + request['timeout_ms'] + request['time_bank_ms'])
        for offset in (0, 2):
            for deadline in (floor, floor - 1, floor - 3000):
                with self.subTest(offset=offset, deadline=deadline):
                    receiver = self.receiver(trace['welcome'])
                    for step in trace['steps'][:4]:
                        receiver.receive(self.raw(step['message']))
                    before = deepcopy(vars(receiver.game))
                    candidate = {**request, 'seq': request['seq'] + offset,
                                 'decision_group_deadline_ms': deadline}
                    if deadline == floor:
                        expected = 'applied' if offset == 0 else 'sequence_gap'
                        self.assertEqual(receiver.receive(self.raw(candidate)), expected)
                    else:
                        with self.assertRaises(SessionError) as error:
                            receiver.receive(self.raw(candidate))
                        self.assertEqual(error.exception.code, 'invalid_message')
                        self.assertEqual(receiver.applied, request['seq'] - 1)
                        self.assertEqual(vars(receiver.game), before)
                        self.assertEqual(receiver.active_requests, set())
                        self.assertTrue(receiver.closed)

    def test_session_validator_checks_grace_for_live_and_pending_requests(self):
        trace = deepcopy(self.vectors['V272_ankan_never_requires_reactions']['positive']['trace'])
        request = trace['steps'][4]['message']
        validate = v._session_schema_validator(self.schemas, self.digest, rules=trace['welcome']['rules'])
        floor = (trace['welcome']['rules']['time_control']['grace_ms']
                 + request['timeout_ms'] + request['time_bank_ms'])
        for kind in ('host-application', 'pending-request'):
            validate(kind, {**request, 'decision_group_deadline_ms': floor})
            with self.subTest(kind=kind), self.assertRaises(SessionError) as error:
                validate(kind, {**request, 'decision_group_deadline_ms': floor - 1})
            self.assertEqual(error.exception.code, 'invalid_message')

    def test_future_fatal_error_preserves_validation_priority(self):
        welcome = self.trace('wire_complete_game')['welcome']
        fatal = {key: welcome[key] for key in ('yamai', 'session_id', 'game_id')}
        fatal.update(kind='error', seq=4, code='resume_unavailable', severity='fatal', message='Recovery failed')
        for changes in ({'session_id': 'wrong'}, {'game_id': 'wrong'}, {'yamai': 'wrong'},
                        {'seq': 0}, {'code': 'sequence_conflict'}, {'code': 'unknown_error'},
                        {'original_seq': 1}, {'extra': True}, {'message': ''}):
            with self.subTest(changes=changes):
                receiver = self.receiver(welcome, last_seq=1)
                with self.assertRaises(SessionError) as error:
                    receiver.receive(self.raw({**fatal, **changes}))
                self.assertEqual(error.exception.code, 'invalid_message')
                self.assertTrue(receiver.closed)
                self.assertIsNone(receiver.fatal_error)
                self.assertEqual(receiver.applied, 1)
                self.assertEqual(receiver.known, {})
        receiver = self.receiver(welcome, last_seq=1)
        recoverable = {**fatal, 'code': 'invalid_action', 'severity': 'recoverable'}
        self.assertEqual(receiver.receive(self.raw(recoverable)), 'sequence_gap')
        self.assertFalse(receiver.closed)
        self.assertIsNone(receiver.fatal_error)

    def test_fatal_error_does_not_bypass_known_bytes_or_snapshot_floor(self):
        trace = self.trace('wire_complete_game')
        welcome = trace['welcome']
        receiver = self.receiver(welcome)
        start = trace['steps'][0]['message']
        receiver.receive(self.raw(start))
        fatal = {key: welcome[key] for key in ('yamai', 'session_id', 'game_id')}
        fatal.update(kind='error', seq=1, code='resume_unavailable', severity='fatal', message='Recovery failed')
        with self.assertRaises(SessionError) as error:
            receiver.receive(self.raw(fatal))
        self.assertEqual(error.exception.code, 'sequence_conflict')
        self.assertIsNone(receiver.fatal_error)
        receiver = self.receiver(welcome, last_seq=3)
        receiver.floor = 3
        self.assertEqual(receiver.receive(self.raw(fatal)), 'duplicate')
        self.assertFalse(receiver.closed)
        self.assertIsNone(receiver.fatal_error)

    def test_contiguous_fatal_error_records_diagnosis_and_prefix(self):
        welcome = self.trace('wire_complete_game')['welcome']
        receiver = self.receiver(welcome)
        fatal = {key: welcome[key] for key in ('yamai', 'session_id', 'game_id')}
        fatal.update(kind='error', seq=1, code='internal_error', severity='fatal', message='Initialization failed')
        raw = self.raw(fatal)
        self.assertEqual(receiver.receive(raw), 'applied')
        self.assertEqual(receiver.applied, 1)
        self.assertEqual(receiver.known[1], raw)
        self.assertEqual(receiver.fatal_error, fatal)
        self.assertTrue(receiver.closed)

    def test_resume_missing_history_uses_retained_snapshot(self):
        trace = self.trace('wire_complete_game')
        welcome = trace['welcome']
        first = trace['steps'][0]['message']
        receiver = self.receiver(welcome)
        receiver.receive(self.raw(first))
        resumed = deepcopy(welcome)
        resumed.update(resumed=True, replay_from_seq=2, replay_through_seq=4)
        resumed['resume']['token'] = 'rt_' + 'Z' * 22
        receiver.begin_resume(resumed)
        history = [self.raw(first), None, None, None]
        self.assertEqual(replay_plan(history, 2, 4, snapshot=True), {'strategy': 'snapshot', 'replaces_through_seq': 4, 'seq': 5})
        snapshot = deepcopy(self.vectors['V18_snapshot_state']['positive'])
        self.assertEqual(receiver.receive(self.raw(snapshot)), 'applied')
        self.assertEqual(receiver.applied, 5)
        self.assertEqual(receiver.floor, 4)
        self.assertEqual(receiver.active_requests, {'r1'})
        self.assertEqual(receiver.welcome['session_id'], welcome['session_id'])
        self.assertIsNone(receiver.recovery)
        with self.assertRaises(SessionError) as error:
            replay_plan(history, 2, 4, snapshot=False)
        self.assertEqual(error.exception.code, 'resume_unavailable')

    def test_cross_transport_replay_keeps_compatible_payload_bytes(self):
        payloads = [b'{"seq":1}', b'{ "seq" : 2, "x_test_note":"escaped\\nline\\rreturn" }\t ',
                    b'{"seq":3}']
        for transport in ('jsonl', 'websocket'):
            plan = replay_plan(payloads, 2, 3, target_transport=transport)
            self.assertEqual(plan, {'strategy': 'replay', 'from': 2, 'through': 3,
                                    'payloads': payloads[1:]})
            self.assertIs(plan['payloads'][0], payloads[1])
            if transport == 'jsonl':
                self.assertEqual(len(v.parse_jsonl_chunks([b'\n'.join(plan['payloads']) + b'\n'])), 2)

    def test_cross_transport_replay_requires_snapshot_for_incompatible_jsonl(self):
        for incompatible in (b' {"seq":2}', b'\t{"seq":2}', b'{\n"seq":2\n}',
                             b'{\r"seq":2}', b'{"seq":2}\n', b'{"seq":2}\r'):
            with self.subTest(payload=incompatible):
                self.assertEqual(v.strict_load_bytes(incompatible), {'seq': 2})
                history = [b'{"seq":1}', incompatible, b'{"seq":3}']
                original = history.copy()
                plan = replay_plan(history, 2, 3, target_transport='websocket')
                self.assertEqual(plan['payloads'], history[1:])
                with self.assertRaises(SessionError) as error:
                    replay_plan(history, 2, 3, target_transport='jsonl')
                self.assertEqual(error.exception.code, 'resume_unavailable')
                self.assertEqual(replay_plan(history, 2, 3, snapshot=True, target_transport='jsonl'),
                                 {'strategy': 'snapshot', 'replaces_through_seq': 3, 'seq': 4})
                self.assertEqual(history, original)

    def test_cross_transport_replay_checks_only_required_retained_range(self):
        history = [b' {"seq":1}', b'{"seq":2}', b'{"seq":3}']
        self.assertEqual(replay_plan(history, 2, 3, target_transport='jsonl')['strategy'], 'replay')
        history[2] = None
        self.assertEqual(replay_plan(history, 2, 3, snapshot=True, target_transport='jsonl')['strategy'], 'snapshot')
        with self.assertRaises(SessionError) as error:
            replay_plan(history, 2, 3, snapshot=True, target_transport='unknown')
        self.assertEqual(error.exception.code, 'invalid_message')

    def test_future_fatal_error_preserves_active_request_and_bank(self):
        trace = self.trace('wire_complete_game')
        receiver = self.receiver(trace['welcome'])
        for step in trace['steps'][:4]:
            receiver.receive(self.raw(step['message']))
        before = deepcopy({key: value for key, value in vars(receiver).items()
                           if key not in {'welcome', 'decode', 'validate', 'game'}})
        game_before = deepcopy(vars(receiver.game))
        fatal = {key: trace['welcome'][key] for key in ('yamai', 'session_id', 'game_id')}
        fatal.update(kind='error', seq=9, code='resume_unavailable', severity='fatal', message='Recovery failed')
        self.assertEqual(receiver.receive(self.raw(fatal)), 'fatal')
        self.assertEqual(receiver.active_requests, {'r1'})
        self.assertEqual(vars(receiver.game), game_before)
        for key, value in before.items():
            if key not in {'closed', 'fatal_error'}:
                self.assertEqual(getattr(receiver, key), value, key)

    def test_extension_activation_does_not_leak_between_sessions(self):
        trace = self.trace('private_event_requires_negotiation')
        active = v._session_schema_validator(self.schemas, self.digest, trace['definitions'], trace['enabled_capabilities'])
        active('host-application', trace['message'])
        plain = v._session_schema_validator(self.schemas, self.digest)
        with self.assertRaises(SessionError):
            plain('host-application', trace['message'])

    def test_new_replay_welcome_requires_initial_scores(self):
        trace = self.trace('replay_recording_target')
        validate = v._session_schema_validator(self.schemas, self.digest)
        def check():
            return negotiate(trace['hello'], trace['join'], trace['welcome'], trace['context'],
                             validate, v.PROTOCOL, v.PROFILE_REVISION, self.digest)
        self.assertEqual(check(), trace['expected'])
        trace['welcome']['scores'] = [26000, 24000, 25000, 25000]
        with self.assertRaises(SessionError) as error:
            check()
        self.assertEqual(error.exception.code, 'invalid_message')

    def test_resume_trust_boundary_is_host_controlled(self):
        trace = self.trace('one_use_token_single_owner')
        validate = v._session_schema_validator(self.schemas, self.digest)
        for secure, trusted_local, accepted in ((True, False, True), (False, True, True),
                                                (False, False, False), (False, 'true', False)):
            with self.subTest(secure=secure, trusted_local=trusted_local):
                candidate = deepcopy(trace)
                candidate['context'] = {**candidate.get('context', {}),
                                        'trusted_local_transport': trusted_local}
                candidate['steps'] = candidate['steps'][:1]
                step = candidate['steps'][0]
                step['secure_transport'] = secure
                # Peer-supplied metadata cannot establish the local trust boundary.
                step['join']['x_review_trusted_local_transport'] = True
                actual = check_token_trace(candidate, validate, v.PROTOCOL, v.PROFILE_REVISION, self.digest)
                self.assertEqual(actual[0]['outcome'], 'accepted' if accepted else 'resume_unavailable')
                expected = step['welcome']['resume']['token'] if accepted else candidate['initial']['token']
                self.assertEqual(actual[0]['token'], expected)
                if not accepted:
                    self.assertEqual(actual[0]['active_connection'], candidate['initial']['active_connection'])
                    self.assertEqual(actual[0]['expires_at_ms'], candidate['initial']['expires_at_ms'])

    def test_local_resume_still_checks_token_lifetime_and_session(self):
        trace = self.trace('resume_token_rotation')
        validate = v._session_schema_validator(self.schemas, self.digest)
        for defect in ('expired', 'consumed', 'fatal', 'wrong_token', 'future_seq'):
            with self.subTest(defect=defect):
                candidate = deepcopy(trace)
                context = candidate['context']
                context.update(secure_transport=False, trusted_local_transport=True)
                previous = context['resume_state']
                if defect == 'expired':
                    context['now_ms'] = previous['expires_at_ms']
                elif defect in ('consumed', 'fatal'):
                    previous[defect] = True
                elif defect == 'wrong_token':
                    candidate['join']['resume']['token'] = 'rt_' + 'Z' * 22
                else:
                    candidate['join']['resume']['last_seq'] = previous['highest_seq'] + 1
                before = deepcopy(context)
                with self.assertRaises(SessionError) as error:
                    negotiate(candidate['hello'], candidate['join'], candidate['welcome'], context,
                              validate, v.PROTOCOL, v.PROFILE_REVISION, self.digest)
                self.assertEqual(error.exception.code, 'resume_unavailable')
                self.assertEqual(context, before)

    def test_event_visibility_rejection_precedes_sequence_gap(self):
        for suffix in ('visibility_play_self', 'visibility_spectate_public',
                       'visibility_replay_public', 'visibility_replay_full',
                       'visibility_replay_seat_0', 'visibility_replay_seat_1',
                       'visibility_replay_seat_2', 'visibility_replay_seat_3'):
            case = next(c for key, c in self.vectors.items() if key.endswith('_' + suffix))
            for event_type in ('start_kyoku', 'tsumo'):
                with self.subTest(view=suffix, event=event_type):
                    trace = deepcopy(case['positive']['trace'])
                    receiver = self.receiver(trace['welcome'])
                    event = next(s['message'] for s in trace['steps']
                                 if s['message'].get('event', {}).get('type') == event_type)
                    event['seq'] += 100
                    self.assertEqual(receiver.receive(self.raw(event)), 'sequence_gap')
                    receiver = self.receiver(trace['welcome'])
                    if event_type == 'start_kyoku':
                        hand = event['event']['hands'][0]
                        event['event']['hands'][0] = ({'count': len(hand['tiles'])} if 'tiles' in hand
                                                    else {'tiles': ['1m'] * 4 + ['2m'] * 4 + ['3m'] * 4 + ['E']})
                    else:
                        event['event']['pai'] = None if event['event']['pai'] is not None else '1m'
                    self.assert_rejected_atomically(receiver, event)

    def test_visibility_does_not_override_retained_byte_conflict(self):
        trace = self.trace('visibility_spectate_public')
        receiver = self.receiver(trace['welcome'])
        for step in trace['steps']:
            receiver.receive(self.raw(step['message']))
        last = trace['steps'][-1]['message']
        self.assertEqual(receiver.receive(self.raw(last)), 'duplicate')
        last['event']['pai'] = '1m'
        with self.assertRaises(SessionError) as error:
            receiver.receive(self.raw(last))
        self.assertEqual(error.exception.code, 'sequence_conflict')

    def test_action_host_envelope_fields_are_fatal(self):
        trace = self.trace('wire_complete_game')
        identity = {key: trace['welcome'][key] for key in ('yamai', 'session_id', 'game_id')}
        validate = v._session_schema_validator(self.schemas, self.digest)
        for ended in (False, True):
            for field in ('seq', 'original_seq'):
                for value in (1, None, '1'):
                    with self.subTest(ended=ended, field=field, value=value):
                        message = dict(identity, kind='action', request_id='r1', action_id='a1')
                        message[field] = value
                        context = {'identity': identity, 'known_request_ids': ['r1'], 'game_ended': ended}
                        self.assertEqual(classify_player_input(message, context, validate),
                                         {'code': 'invalid_message', 'severity': 'fatal'})

    def test_spectate_capability_rejection_precedes_limits(self):
        trace = self.trace('spectate_requires_snapshot')
        trace['join']['receive_limits']['max_json_depth'] = 16
        validate = v._session_schema_validator(self.schemas, self.digest)
        for snapshot, expected in ((False, 'unsupported_capability'), (True, 'unsupported_limit')):
            with self.subTest(snapshot=snapshot):
                trace['join']['capabilities']['optional'] = ['snapshot'] if snapshot else []
                with self.assertRaises(SessionError) as error:
                    negotiate(trace['hello'], trace['join'], trace['welcome'], trace['context'],
                              validate, v.PROTOCOL, v.PROFILE_REVISION, self.digest)
                self.assertEqual(error.exception.code, expected)

    def test_request_cause_distinguishes_identical_events(self):
        for stale in (False, True):
            with self.subTest(stale=stale):
                trace = self.trace('wire_complete_game')
                receiver = self.receiver(trace['welcome'])
                for step in trace['steps'][:6]:
                    receiver.receive(self.raw(step['message']))

                def send(kind, **fields):
                    identity = {k: trace['welcome'][k] for k in ('yamai', 'session_id', 'game_id')}
                    receiver.receive(self.raw(dict(identity, kind=kind, seq=receiver.applied + 1, **fields)))

                for actor, pai in ((1, '8s'), (2, '7s'), (3, '6s')):
                    send('event', event={'type': 'tsumo', 'actor': actor, 'pai': None})
                    send('event', event={'type': 'dahai', 'actor': actor, 'pai': pai, 'tsumogiri': True})
                    rid = 'reaction' + str(actor)
                    send('request', request_id=rid, seat=0, caused_by_seq=receiver.applied,
                         timeout_ms=3000, time_bank_ms=15000,
                         legal_actions=[{'action_id': 'n', 'action': {'type': 'none'}}], default_action_id='n',
                         decision_group_id='g' + rid, decision_group_close='all_selected_or_deadline',
                         decision_group_deadline_ms=21000,
                         decision_group_members=[{'seat': s, 'request_id': rid if s == 0 else rid + str(s)}
                                                 for s in range(4) if s != actor])
                    send('ack', request_id=rid, action_id='n', status='passed', elapsed_ms=1, time_bank_ms=15000)
                send('event', event={'type': 'tsumo', 'actor': 0, 'pai': '9s'})
                request = deepcopy(trace['steps'][3]['message'])
                request.update(seq=receiver.applied + 1, request_id='r2',
                               caused_by_seq=3 if stale else receiver.applied)
                if stale:
                    before = deepcopy((vars(receiver.game), receiver.known, receiver.requests, receiver.applied))
                    with self.assertRaises(SessionError) as error:
                        receiver.receive(self.raw(request))
                    self.assertEqual(error.exception.code, 'invalid_message')
                    self.assertEqual((vars(receiver.game), receiver.known, receiver.requests, receiver.applied), before)
                else:
                    self.assertEqual(receiver.receive(self.raw(request)), 'applied')

    def test_public_yaku_claims_cannot_inflate_settlement(self):
        for variant in ('duplicate', 'disabled_double', 'unregistered', 'unaccepted_riichi', 'exclusive'):
            with self.subTest(variant=variant):
                trace = self.trace('public_hora_hand_points')
                event = trace['steps'][-1]['message']['event']
                win = event['result']['wins'][0]
                amount = 64000
                if variant == 'duplicate':
                    win['yakus'].append({**win['yakus'][0], 'x_review_note': 'second'})
                elif variant == 'disabled_double':
                    trace['welcome']['rules']['double_yakuman'] = []
                    trace['steps'][0]['message']['event']['rules']['double_yakuman'] = []
                    win['yakus'][0]['value'] = 2
                elif variant == 'unregistered':
                    win['yakus'][0]['id'] = 'x_review_unregistered'
                    amount = 32000
                elif variant == 'exclusive':
                    win.update(fu=30, han=9, yakus=[{'id': 'chinitsu', 'value': 6, 'unit': 'han'},
                                                  {'id': 'honitsu', 'value': 3, 'unit': 'han'}])
                    amount = 16000
                else:
                    win.update(fu=30, han=1, yakus=[{'id': 'riichi', 'value': 1, 'unit': 'han'}])
                    amount = 1000
                win.update(hand_points=amount, deltas=[-amount, amount, 0, 0])
                event.update(deltas=win['deltas'], scores=[25000 - amount, 25000 + amount, 25000, 25000])
                if amount < 25000:
                    event['next'] = dict(type='rotate', bakaze='E', kyoku=2, oya=1, honba=0, kyotaku=0, extension_round=0)
                receiver = self.receiver(trace['welcome'])
                for step in trace['steps'][:-1]:
                    receiver.receive(self.raw(step['message']))
                before = deepcopy((vars(receiver.game), receiver.known, receiver.applied))
                with self.assertRaises(SessionError) as error:
                    receiver.receive(self.raw(trace['steps'][-1]['message']))
                self.assertEqual(error.exception.code, 'invalid_message')
                self.assertEqual((vars(receiver.game), receiver.known, receiver.applied), before)

    def test_disclosed_scoring_contradictions_reject_atomically(self):
        for number in range(395, 409):
            name = next(k for k in self.vectors if k.startswith(f'V{number}_'))
            with self.subTest(case=name):
                case = self.vectors[name]
                for side in ('positive', 'negative'):
                    trace = deepcopy(case[side]['trace'])
                    receiver = self.receiver(trace['welcome'])
                    for step in trace['steps'][:-1]:
                        self.assertEqual(receiver.receive(self.raw(step['message'])), 'applied')
                    before = deepcopy((vars(receiver.game), receiver.known, receiver.requests,
                                       receiver.applied, receiver.time_bank_ms))
                    final = self.raw(trace['steps'][-1]['message'])
                    if side == 'positive':
                        self.assertEqual(receiver.receive(final), 'applied')
                        self.assertEqual(receiver.game.game_phase, 'between_kyoku')
                    else:
                        with self.assertRaises(SessionError) as error:
                            receiver.receive(final)
                        self.assertEqual(error.exception.code, 'invalid_message')
                        self.assertEqual((vars(receiver.game), receiver.known, receiver.requests,
                                          receiver.applied, receiver.time_bank_ms), before)

    def test_public_yaku_cannot_require_incompatible_shapes(self):
        combinations = (
            ('pinfu', 'toitoi'), ('chiitoitsu', 'toitoi'), ('iipeikou', 'sanankou'),
            ('honroutou', 'iipeikou'), ('honroutou', 'tanyao'), ('chinitsu', 'round_wind'),
            ('honitsu', 'sanshoku_doujun'), ('ikkitsuukan', 'ryanpeikou'),
            ('ikkitsuukan', 'sanshoku_doujun'), ('chanta', 'ikkitsuukan'),
            ('sanshoku_doujun', 'yakuhai_haku', 'yakuhai_hatsu'),
            ('sanshoku_doukou', 'shousangen'),
        )
        for names in combinations:
            with self.subTest(yakus=names):
                trace = self.trace('public_hora_hand_points')
                receiver = self.receiver(trace['welcome'])
                for step in trace['steps'][:-1]:
                    receiver.receive(self.raw(step['message']))
                message = trace['steps'][-1]['message']
                event = message['event']
                win = event['result']['wins'][0]
                yakus = [dict(id=name, unit='han', value=NORMAL_YAKU_HAN[name][0]) for name in sorted(names)]
                fu = 25 if 'chiitoitsu' in names else 30 if 'pinfu' in names else 40
                han = sum(y['value'] for y in yakus)
                amount = sum(normal_payments(basic_points(fu, han, 0, trace['welcome']['rules']), 1, 0, 0).values())
                win.update(fu=fu, han=han, yakus=yakus, hand_points=amount, deltas=[-amount, amount, 0, 0])
                event.update(deltas=win['deltas'], scores=[25000 - amount, 25000 + amount, 25000, 25000],
                             next=dict(type='rotate', bakaze='E', kyoku=2, oya=1, honba=0, kyotaku=0, extension_round=0))
                self.assert_rejected_atomically(receiver, message)

    def test_unadopted_reaction_cannot_generate_own_call(self):
        for status, aid in (('passed', 'n'), ('superseded', 'p1'), ('defaulted', 'n')):
            with self.subTest(status=status):
                trace = self.trace('wire_call_compound_flow')
                request = trace['steps'][4]['message']
                ack = trace['steps'][5]['message']
                ack.update(status=status, action_id=aid)
                if status == 'defaulted':
                    ack.update(elapsed_ms=trace['welcome']['rules']['time_control']['grace_ms']
                               + request['timeout_ms'] + request['time_bank_ms'], time_bank_ms=0)
                receiver = self.receiver(trace['welcome'])
                for step in trace['steps'][:6]:
                    receiver.receive(self.raw(step['message']))
                before = deepcopy((vars(receiver.game), receiver.known, receiver.time_bank_ms))
                with self.assertRaises(SessionError) as error:
                    receiver.receive(self.raw(trace['steps'][6]['message']))
                self.assertEqual(error.exception.code, 'invalid_message')
                self.assertEqual(receiver.applied, 6)
                self.assertEqual((vars(receiver.game), receiver.known, receiver.time_bank_ms), before)

    def test_pass_allows_the_next_players_draw(self):
        trace = self.trace('wire_call_compound_flow')
        trace['steps'][5]['message'].update(status='passed', action_id='n')
        receiver = self.receiver(trace['welcome'])
        for step in trace['steps'][:6]:
            receiver.receive(self.raw(step['message']))
        draw = deepcopy(trace['steps'][6]['message'])
        draw['event'] = {'type':'tsumo', 'actor':1, 'pai':'9s'}
        self.assertEqual(receiver.receive(self.raw(draw)), 'applied')
        self.assertTrue(receiver.awaiting_request)

    def test_superseded_core_choice_requires_a_higher_priority_result(self):
        for suffix, aid in (('wire_call_compound_flow', 'p1'), ('pass_cannot_generate_own_hora', 'h')):
            for outcome in ('draw', 'farther_pon'):
                with self.subTest(choice=aid, outcome=outcome):
                    trace = self.trace(suffix)
                    trace['steps'][5]['message'].update(status='superseded', action_id=aid)
                    receiver = self.receiver(trace['welcome'])
                    for step in trace['steps'][:6]:
                        receiver.receive(self.raw(step['message']))
                    message = trace['steps'][6]['message']
                    message['event'] = ({'type':'tsumo', 'actor':1, 'pai':'9s'} if outcome == 'draw'
                                        else {'type':'pon', 'actor':2, 'target':0, 'pai':'3m', 'consumed':['3m','3m']})
                    with self.assertRaises(SessionError) as error:
                        receiver.receive(self.raw(message))
                    self.assertEqual(error.exception.code, 'invalid_message')

    def test_unadopted_reaction_cannot_become_a_win_or_three_ron_draw(self):
        for suffix in ('pass_cannot_generate_own_hora', 'sanchaho_requires_own_hora_selection'):
            with self.subTest(case=suffix):
                case = next(c for key, c in self.vectors.items() if key.endswith('_' + suffix))
                for valid in (True, False):
                    trace = deepcopy(case['positive' if valid else 'negative']['trace'])
                    receiver = self.receiver(trace['welcome'])
                    for step in trace['steps'][:-1]:
                        receiver.receive(self.raw(step['message']))
                    if valid:
                        self.assertEqual(receiver.receive(self.raw(trace['steps'][-1]['message'])), 'applied')
                    else:
                        before = deepcopy((vars(receiver.game), receiver.known, receiver.unadopted_reaction))
                        with self.assertRaises(SessionError) as error:
                            receiver.receive(self.raw(trace['steps'][-1]['message']))
                        self.assertEqual(error.exception.code, 'invalid_message')
                        self.assertEqual((vars(receiver.game), receiver.known, receiver.unadopted_reaction), before)

    def test_cancellation_cannot_apply_the_cancelled_discard(self):
        trace = self.trace('cancellation_candidate_id')
        receiver = self.receiver(trace['welcome'])
        for step in trace['steps']:
            receiver.receive(self.raw(step['message']))
        discard = deepcopy(self.trace('wire_complete_game')['steps'][5]['message'])
        discard['seq'] = receiver.applied + 1
        before = deepcopy((vars(receiver.game), receiver.known, receiver.time_bank_ms))
        with self.assertRaises(SessionError) as error:
            receiver.receive(self.raw(discard))
        self.assertEqual(error.exception.code, 'invalid_message')
        self.assertEqual((vars(receiver.game), receiver.known, receiver.time_bank_ms), before)

    def test_cancellation_requires_chombo_policy(self):
        for policy in ('reject', 'default'):
            with self.subTest(policy=policy):
                trace = self.trace('wire_complete_game')
                trace['welcome']['rules']['invalid_action_policy'] = policy
                trace['steps'][0]['message']['event']['rules']['invalid_action_policy'] = policy
                receiver = self.receiver(trace['welcome'])
                for step in trace['steps'][:4]:
                    receiver.receive(self.raw(step['message']))
                ack = trace['steps'][4]['message']
                ack['status'] = 'stale'
                with self.assertRaises(SessionError) as error:
                    receiver.receive(self.raw(ack))
                self.assertEqual(error.exception.code, 'invalid_message')

    def test_penalty_requires_cancellation_instead_of_pass(self):
        for status in ('passed', 'defaulted'):
            with self.subTest(status=status):
                trace = self.trace('passed_cannot_generate_own_call')
                trace['welcome']['rules']['invalid_action_policy'] = 'chombo'
                trace['steps'][0]['message']['event']['rules']['invalid_action_policy'] = 'chombo'
                ack = trace['steps'][5]['message']
                ack['status'] = status
                if status == 'defaulted':
                    ack.update(elapsed_ms=21000, time_bank_ms=0)
                receiver = self.receiver(trace['welcome'])
                for step in trace['steps'][:-1]:
                    receiver.receive(self.raw(step['message']))
                event = deepcopy(self.trace('cancellation_requires_penalty_result')['steps'][-1]['message']['event'])
                event['result']['offender'] = 2
                event['result']['penalty']['payments'] = [
                    {'from':2, 'to':0, 'points':2800}, {'from':2, 'to':1, 'points':2600},
                    {'from':2, 'to':3, 'points':2600}]
                event.update(deltas=[2800,2600,-8000,2600], scores=[27800,27600,17000,27600])
                message = trace['steps'][-1]['message']
                message['event'] = event
                with self.assertRaises(SessionError) as error:
                    receiver.receive(self.raw(message))
                self.assertEqual(error.exception.code, 'invalid_message')

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

    def test_empty_resume_consumes_permission_to_jump(self):
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
        snapshot.update(seq=7, replaces_through_seq=6)
        with self.assertRaises(SessionError):
            receiver.receive(self.raw(snapshot))
        self.assertEqual(receiver.applied, 5)

    def request_identity_snapshot(self, receiver):
        """Project an already validated live prefix without changing its IDs."""
        state = {key: deepcopy(receiver.welcome[key]) for key in ('mode', 'view', 'seat', 'players')}
        kyoku = deepcopy(receiver.game.round)
        kyoku['turn'].update(last_event_seq=receiver.last_event_seq,
                             last_event=deepcopy(receiver.game.last_cause))
        pending = []
        for rid in receiver.active_requests:
            request = deepcopy(receiver.requests[rid])
            for key in ('yamai', 'kind', 'session_id', 'game_id', 'seq'):
                request.pop(key, None)
            request.update(selection=None, remaining_ms=(receiver.welcome['rules']['time_control']['grace_ms']
                                                        + request['timeout_ms'] + request['time_bank_ms']))
            if 'decision_group_id' in request:
                request['decision_group_remaining_ms'] = request['decision_group_deadline_ms']
            pending.append(request)
        state.update(game_phase=receiver.game.game_phase, scores=receiver.game.scores.copy(),
                     kyotaku=receiver.game.kyotaku, kyoku=kyoku, next_kyoku=None, final_rankings=None,
                     time_bank_ms=receiver.time_bank_ms, pending_requests=pending)
        identity = {key: receiver.welcome[key] for key in ('yamai', 'session_id', 'game_id')}
        return dict(identity, kind='snapshot', seq=receiver.applied + 1,
                    replaces_through_seq=receiver.applied, state=state)

    def test_observed_group_request_ids_are_distinct_from_own_ack_scope(self):
        trace = self.trace('request_cause_uses_current_event_sequence')
        receiver = self.receiver(trace['welcome'])
        for step in trace['steps']:
            self.assertEqual(receiver.receive(self.raw(step['message'])), 'applied')
        self.assertEqual(receiver.observed_request_ids['reaction12'], (2, 8, 'greaction1'))
        self.assertNotIn('reaction12', receiver.request_ids)
        self.assertIn('r2', receiver.request_ids)

    def test_live_request_rejects_observed_cross_seat_or_cross_decision_id_reuse(self):
        for variant in ('old_own_as_peer', 'old_peer_as_own', 'old_peer_same_seat', 'old_peer_new_seat'):
            with self.subTest(variant=variant):
                trace = self.trace('request_cause_uses_current_event_sequence')
                index = 8 if variant == 'old_own_as_peer' else 12
                message = trace['steps'][index]['message']
                members = message['decision_group_members']
                if variant == 'old_own_as_peer':
                    members[1]['request_id'] = 'r1'
                elif variant == 'old_peer_as_own':
                    message['request_id'] = members[0]['request_id'] = 'reaction12'
                elif variant == 'old_peer_same_seat':
                    members[2]['request_id'] = 'reaction13'
                else:
                    members[1]['request_id'] = 'reaction12'
                receiver = self.receiver(trace['welcome'])
                for step in trace['steps'][:index]:
                    receiver.receive(self.raw(step['message']))
                self.assert_rejected_atomically(receiver, message)

    def test_request_identity_scope_is_one_game_not_the_group_label(self):
        trace = self.trace('request_cause_uses_current_event_sequence')
        receivers = []
        for index in range(2):
            welcome = dict(trace['welcome'], session_id=f'identity-session-{index}', game_id=f'identity-game-{index}')
            receiver = self.receiver(welcome)
            for step in trace['steps']:
                message = deepcopy(step['message'])
                message.update(session_id=welcome['session_id'], game_id=welcome['game_id'])
                if 'decision_group_id' in message:
                    # Only request IDs have a game-wide uniqueness contract.
                    message['decision_group_id'] = 'same-group-label'
                self.assertEqual(receiver.receive(self.raw(message)), 'applied')
            receivers.append(receiver)
        self.assertEqual(receivers[0].observed_request_ids, receivers[1].observed_request_ids)
        self.assertIsNot(receivers[0].observed_request_ids, receivers[1].observed_request_ids)

    def test_public_receiver_trace_rejects_cross_seat_id_reuse(self):
        trace = self.trace('request_cause_uses_current_event_sequence')
        v.semantic_session_trace(trace, self.digest)
        trace['steps'][8]['message']['decision_group_members'][1]['request_id'] = 'r1'
        with self.assertRaises(v.ArtifactError):
            v.semantic_session_trace(trace, self.digest)

    def test_snapshot_request_provenance_survives_replay_and_reordering(self):
        trace = self.trace('snapshot_group_remaining_cannot_increase')
        receiver = self.receiver(trace['welcome'])
        first, repeated = (step['message'] for step in trace['steps'])
        self.assertEqual(receiver.receive(self.raw(first)), 'applied')
        observed = deepcopy(receiver.observed_request_ids)
        self.assertEqual(len(observed), 3)
        self.assertEqual(receiver.receive(self.raw(first)), 'duplicate')
        repeated['state']['pending_requests'][0]['decision_group_members'].reverse()
        receiver.begin_resume(dict(trace['welcome'], resumed=True, replay_from_seq=7, replay_through_seq=7))
        self.assertEqual(receiver.receive(self.raw(repeated)), 'applied')
        self.assertEqual(receiver.observed_request_ids, observed)
        self.assertEqual(receiver.request_ids, {'r1'})

    def test_gap_snapshot_rejects_observed_request_id_reuse(self):
        for collision in ('none', 'old_own_as_peer', 'old_peer_as_own', 'old_peer_same_seat'):
            with self.subTest(collision=collision):
                trace = self.trace('request_cause_uses_current_event_sequence')
                truth, receiver = self.receiver(trace['welcome']), self.receiver(trace['welcome'])
                for step in trace['steps'][:13]:
                    truth.receive(self.raw(step['message']))
                for step in trace['steps'][:9]:
                    receiver.receive(self.raw(step['message']))
                snapshot = self.request_identity_snapshot(truth)
                request = snapshot['state']['pending_requests'][0]
                if collision == 'old_own_as_peer':
                    request['decision_group_members'][1]['request_id'] = 'r1'
                elif collision == 'old_peer_as_own':
                    request['request_id'] = request['decision_group_members'][0]['request_id'] = 'reaction12'
                elif collision == 'old_peer_same_seat':
                    request['decision_group_members'][2]['request_id'] = 'reaction13'
                receiver.begin_resume(dict(trace['welcome'], resumed=True, replay_from_seq=10, replay_through_seq=13))
                if collision == 'none':
                    self.assertEqual(receiver.receive(self.raw(snapshot)), 'applied')
                    self.assertEqual(receiver.observed_request_ids['reaction12'], (2, 8, 'greaction1'))
                    self.assertEqual(receiver.observed_request_ids['reaction2'], (0, 12, 'greaction2'))
                else:
                    self.assert_rejected_atomically(receiver, snapshot)

    def test_known_peer_id_never_authorizes_late_stale_ack(self):
        for with_snapshot in (False, True):
            with self.subTest(with_snapshot=with_snapshot):
                trace = self.trace('request_cause_uses_current_event_sequence')
                receiver = self.receiver(trace['welcome'])
                for step in trace['steps'][:9]:
                    receiver.receive(self.raw(step['message']))
                if with_snapshot:
                    receiver.receive(self.raw(self.request_identity_snapshot(receiver)))
                ack = deepcopy(trace['steps'][9]['message'])
                ack.update(seq=receiver.applied + 1, request_id='reaction12', status='stale')
                self.assert_rejected_atomically(receiver, ack)

    def test_snapshot_compacted_stale_remembers_terminal_own_identity(self):
        trace = self.trace('request_cause_uses_current_event_sequence')
        receiver = self.receiver(trace['welcome'])
        for step in trace['steps'][:9]:
            receiver.receive(self.raw(step['message']))
        receiver.receive(self.raw(self.request_identity_snapshot(receiver)))
        ack = deepcopy(trace['steps'][9]['message'])
        ack.update(seq=receiver.applied + 1, request_id='hidden-prior', status='stale')
        self.assertEqual(receiver.receive(self.raw(ack)), 'applied')
        self.assertEqual(receiver.observed_request_ids['hidden-prior'], (0, None, None))
        self.assertEqual(receiver.active_requests, {'reaction1'})
        # The known terminal identity cannot later be introduced as a new
        # pending request, even though its original request was compacted.
        truth = self.receiver(trace['welcome'])
        for step in trace['steps']:
            truth.receive(self.raw(step['message']))
        snapshot = self.request_identity_snapshot(truth)
        snapshot['state']['pending_requests'][0]['request_id'] = 'hidden-prior'
        receiver.begin_resume(dict(trace['welcome'], resumed=True,
                                   replay_from_seq=receiver.applied + 1, replay_through_seq=20))
        self.assert_rejected_atomically(receiver, snapshot)

    def test_compacted_late_ack_clock_is_frozen_across_recovery(self):
        for checkpoint in ('none', 'resume', 'snapshot'):
            for change in ({}, {'elapsed_ms': 7001}, {'time_bank_ms': 13999}):
                with self.subTest(checkpoint=checkpoint, change=change):
                    trace = self.trace('request_cause_uses_current_event_sequence')
                    receiver = self.receiver(trace['welcome'])
                    for step in trace['steps'][:9]:
                        receiver.receive(self.raw(step['message']))
                    receiver.receive(self.raw(self.request_identity_snapshot(receiver)))
                    first = deepcopy(trace['steps'][9]['message'])
                    first.update(seq=receiver.applied + 1, request_id='hidden-prior',
                                 action_id='first-late', status='stale',
                                 elapsed_ms=7000, time_bank_ms=14000)
                    self.assertEqual(receiver.receive(self.raw(first)), 'applied')
                    self.assertEqual(receiver.receive(self.raw(first)), 'duplicate')
                    if checkpoint == 'resume':
                        receiver.begin_resume(dict(trace['welcome'], resumed=True,
                                                   replay_from_seq=receiver.applied + 1,
                                                   replay_through_seq=receiver.applied))
                    elif checkpoint == 'snapshot':
                        receiver.receive(self.raw(self.request_identity_snapshot(receiver)))
                    before = deepcopy((vars(receiver.game), receiver.active_requests,
                                       receiver.time_bank_ms, receiver.terminal_acks))
                    later = dict(first, seq=receiver.applied + 1, action_id='different-late', **change)
                    if change:
                        self.assert_rejected_atomically(receiver, later)
                    else:
                        self.assertEqual(receiver.receive(self.raw(later)), 'applied')
                    self.assertEqual((vars(receiver.game), receiver.active_requests,
                                      receiver.time_bank_ms, receiver.terminal_acks), before)

    def test_compacted_terminal_ack_keeps_observed_request_clock(self):
        cases = [
            ('reject', None, {'elapsed_ms': 21000, 'time_bank_ms': 0}, True),
            ('reject', None, {'elapsed_ms': 7000, 'time_bank_ms': 14000}, False),
            ('reject', 'default', {'elapsed_ms': 21000, 'time_bank_ms': 0}, True),
            ('reject', 'default', {'elapsed_ms': 20000, 'time_bank_ms': 1000}, False),
            ('default', 'default', {'elapsed_ms': 7000, 'time_bank_ms': 14000}, True),
            ('default', 'default', {'elapsed_ms': 7001, 'time_bank_ms': 13999}, False),
            ('chombo', 'user', {'elapsed_ms': 7000, 'time_bank_ms': 14000}, True),
            ('chombo', 'user', {'elapsed_ms': 7001, 'time_bank_ms': 13999}, False),
            ('reject', 'user', {'elapsed_ms': 7000, 'time_bank_ms': 14000}, False),
        ]
        for policy, source, clock, valid in cases:
            with self.subTest(policy=policy, source=source, clock=clock):
                trace = self.trace('request_cause_uses_current_event_sequence')
                trace['welcome']['rules']['invalid_action_policy'] = policy
                trace['steps'][0]['message']['event']['rules']['invalid_action_policy'] = policy
                receiver = self.receiver(trace['welcome'])
                for step in trace['steps'][:9]:
                    receiver.receive(self.raw(step['message']))
                if source is not None:
                    selected = self.request_identity_snapshot(receiver)
                    request = selected['state']['pending_requests'][0]
                    elapsed, bank = (21000, 0) if source == 'default' and policy != 'default' else (7000, 14000)
                    request.update(remaining_ms=0, decision_group_remaining_ms=0,
                                   selection=dict(action_id=request['default_action_id'], source=source,
                                                  elapsed_ms=elapsed, time_bank_ms=bank))
                    selected['state']['time_bank_ms'] = selected['state']['kyoku']['self_state']['time_bank_ms'] = bank
                    receiver.receive(self.raw(selected))
                # A recovery jump may cover the terminal ACK and all subsequent
                # events, but must not erase facts learned before the gap.
                snapshot = deepcopy(self.vectors['V58_snapshot_ended_rankings']['positive'])
                snapshot.update(seq=22, replaces_through_seq=21)
                snapshot['state'].update(players=trace['welcome']['players'], time_bank_ms=0)
                receiver.begin_resume(dict(trace['welcome'], resumed=True,
                                           replay_from_seq=receiver.applied + 1, replay_through_seq=21))
                self.assertEqual(receiver.receive(self.raw(snapshot)), 'applied')
                self.assertNotIn('reaction1', receiver.terminal_acks)
                late = {key: trace['welcome'][key] for key in ('yamai', 'session_id', 'game_id')}
                late.update(kind='ack', seq=23, request_id='reaction1', action_id='late', status='stale', **clock)
                if valid:
                    self.assertEqual(receiver.receive(self.raw(late)), 'applied')
                    self.assertEqual(receiver.time_bank_ms, 0)
                else:
                    self.assert_rejected_atomically(receiver, late)

    def snapshot_history(self):
        trace = self.trace('wire_complete_game')
        messages = [step['message'] for step in trace['steps'][:4]]
        messages.append(deepcopy(self.vectors['V18_snapshot_state']['positive']))
        for step in trace['steps'][4:]:
            message = step['message']
            message['seq'] += 1
            if message['kind'] == 'ack':
                # The OPEN checkpoint has already elapsed 3000 ms.
                message['elapsed_ms'] = 3000
            messages.append(message)
        return trace['welcome'], messages

    def test_resume_replays_historical_snapshot_without_finishing_early(self):
        welcome, messages = self.snapshot_history()
        # First deliver this exact ledger through an authorized live recovery.
        live = self.receiver(welcome)
        for message in messages[:4]:
            live.receive(self.raw(message))
        resumed = deepcopy(welcome)
        resumed.update(resumed=True, replay_from_seq=5, replay_through_seq=4)
        live.begin_resume(resumed)
        for message in messages[4:]:
            live.receive(self.raw(message))
        self.assertTrue(live.ended)
        resumed.update(replay_from_seq=1, replay_through_seq=9, scores=live.game.scores)
        receiver = self.receiver(resumed)
        for message in messages:
            self.assertEqual(receiver.receive(self.raw(message)), 'applied')
            self.assertEqual(receiver.recovery, 'resume' if message['seq'] < 9 else None)
            if message['kind'] == 'snapshot':
                self.assertEqual(receiver.receive(self.raw(message)), 'duplicate')
                self.assertEqual(receiver.active_requests, {'r1'})
        self.assertEqual(receiver.game.scores, live.game.scores)
        self.assertEqual(receiver.applications, list(range(1, 10)))

    def test_gap_replays_snapshot_before_or_after_observed_gap_frontier(self):
        welcome, messages = self.snapshot_history()
        for frontier in (4, 8):
            with self.subTest(frontier=frontier):
                receiver = self.receiver(welcome)
                receiver.receive(self.raw(messages[0]))
                self.assertEqual(receiver.receive(self.raw(messages[frontier-1])), 'sequence_gap')
                for message in messages[1:]:
                    self.assertEqual(receiver.receive(self.raw(message)), 'applied')
                    self.assertEqual(receiver.recovery, 'gap' if message['seq'] < frontier else None)
                self.assertTrue(receiver.ended)

    def test_snapshot_jump_must_cover_entire_resume_frontier(self):
        welcome, messages = self.snapshot_history()
        welcome.update(resumed=True, replay_from_seq=1, replay_through_seq=9)
        receiver = self.receiver(welcome)
        with self.assertRaises(SessionError):
            receiver.receive(self.raw(messages[4]))
        self.assertEqual(receiver.applied, 0)

    def test_gap_during_resume_preserves_both_recovery_frontiers(self):
        for through, received in ((9, 4), (6, 8)):
            with self.subTest(through=through, received=received):
                welcome, messages = self.snapshot_history()
                welcome.update(resumed=True, replay_from_seq=1, replay_through_seq=through)
                receiver = self.receiver(welcome)
                receiver.receive(self.raw(messages[0]))
                self.assertEqual(receiver.receive(self.raw(messages[received-1])), 'sequence_gap')
                for message in messages[1:]:
                    self.assertEqual(receiver.receive(self.raw(message)), 'applied')
                    self.assertEqual(receiver.recovery is not None, message['seq'] < max(through, received))
                self.assertTrue(receiver.ended)

    def test_contiguous_snapshot_still_requires_capability(self):
        welcome, messages = self.snapshot_history()
        welcome['capabilities'].remove('snapshot')
        receiver = self.receiver(welcome)
        for message in messages[:4]:
            receiver.receive(self.raw(message))
        with self.assertRaises(SessionError):
            receiver.receive(self.raw(messages[4]))
        self.assertEqual(receiver.applied, 4)

    def test_negotiated_extension_is_received_between_and_during_rounds(self):
        extension = self.trace('active_extension_needs_schema')
        trace = self.trace('wire_complete_game')
        welcome = trace['welcome']
        welcome['capabilities'] = sorted(welcome['capabilities'] + extension['enabled_capabilities'])
        validate = v._session_schema_validator(self.schemas, self.digest, extension['definitions'], welcome['capabilities'])
        for during_round in (False, True):
            with self.subTest(during_round=during_round):
                receiver = Receiver(welcome, v.strict_load_bytes, validate)
                for step in trace['steps'][:2 if during_round else 1]:
                    receiver.receive(self.raw(step['message']))
                before = deepcopy(vars(receiver.game))
                message = deepcopy(extension['message'])
                message['seq'] = receiver.applied + 1
                self.assertEqual(receiver.receive(self.raw(message)), 'applied')
                self.assertEqual(vars(receiver.game), before)
                self.assertEqual(receiver.receive(self.raw(message)), 'duplicate')
        # The same wire type is still fatal without negotiation.
        plain_welcome = deepcopy(trace['welcome'])
        plain_welcome['capabilities'] = ['resume', 'snapshot']
        plain = self.receiver(plain_welcome)
        plain.receive(self.raw(trace['steps'][0]['message']))
        with self.assertRaises(SessionError):
            plain.receive(self.raw(extension['message']))

    def test_invalid_id_is_a_schema_error_under_every_action_policy(self):
        trace = self.trace('wire_complete_game')
        identity = {key:trace['welcome'][key] for key in ('yamai', 'session_id', 'game_id')}
        for policy in ('reject', 'default', 'chombo'):
            for suffix in ('\n', '\r\n', ' ', '\t', '\u2028', '\u2029', '\u0000', 'あ'):
                with self.subTest(policy=policy, suffix=repr(suffix)):
                    trace['welcome']['rules']['invalid_action_policy'] = policy
                    validate = v._session_schema_validator(self.schemas, self.digest)
                    message = dict(identity, kind='action', request_id='r1', action_id='a1'+suffix)
                    result = classify_player_input(message, {'identity':identity,'known_request_ids':['r1']}, validate)
                    self.assertEqual(result, {'code':'invalid_message','severity':'recoverable'})

    def test_queued_stale_after_end_game_does_not_change_final_state(self):
        trace = self.trace('wire_complete_game')
        trace['steps'][4]['message'].update(status='defaulted', elapsed_ms=21000, time_bank_ms=0)
        receiver = self.receiver(trace['welcome'])
        for step in trace['steps']:
            receiver.receive(self.raw(step['message']))
        before = deepcopy((vars(receiver.game), receiver.time_bank_ms, receiver.terminal_acks))
        late = deepcopy(trace['steps'][4]['message'])
        late.update(seq=9, status='stale', action_id='d0')
        self.assertEqual(receiver.receive(self.raw(late)), 'applied')
        self.assertEqual(receiver.receive(self.raw(late)), 'duplicate')
        self.assertEqual((vars(receiver.game), receiver.time_bank_ms, receiver.terminal_acks), before)
        identity = {key:trace['welcome'][key] for key in ('yamai','session_id','game_id')}
        validate = v._session_schema_validator(self.schemas, self.digest)
        for rid in ('r1', 'unknown'):
            action = dict(identity, kind='action', request_id=rid, action_id='a1')
            result = classify_player_input(action, {'identity':identity,'known_request_ids':['r1'],'game_ended':True}, validate)
            self.assertEqual(result, {'code':'ignored','severity':None})
        malformed = dict(identity, kind='action', request_id='r1', action_id='a1\n')
        result = classify_player_input(malformed, {'identity':identity,'known_request_ids':['r1'],'game_ended':True}, validate)
        self.assertEqual(result, {'code':'invalid_message','severity':'recoverable'})
        diagnostic = dict(identity, kind='error', seq=10, message='invalid action ID', **result)
        self.assertEqual(receiver.receive(self.raw(diagnostic)), 'applied')
        self.assertEqual((vars(receiver.game), receiver.time_bank_ms, receiver.terminal_acks), before)
        forbidden = deepcopy(trace['steps'][1]['message'])
        forbidden['seq'] = 11
        with self.assertRaises(SessionError):
            receiver.receive(self.raw(forbidden))

    def test_initial_message_precedes_recoverable_diagnostics(self):
        for mode in ('play', 'spectate', 'replay'):
            for first in ('start_game', 'recoverable', 'fatal'):
                with self.subTest(mode=mode, first=first):
                    trace = self.trace('wire_complete_game')
                    welcome = trace['welcome']
                    if mode != 'play':
                        welcome.update(mode=mode, view='public', seat=None, capabilities=['snapshot'])
                        welcome.pop('resume', None)
                    receiver = self.receiver(welcome)
                    identity = {key: welcome[key] for key in ('yamai', 'session_id', 'game_id')}
                    error = dict(identity, kind='error', seq=1, code='invalid_message',
                                 severity='recoverable', message='invalid input')
                    if first == 'recoverable':
                        self.assert_rejected_atomically(receiver, error)
                    elif first == 'fatal':
                        error.update(severity='fatal')
                        self.assertEqual(receiver.receive(self.raw(error)), 'applied')
                        self.assertTrue(receiver.closed)
                    else:
                        start = trace['steps'][0]['message']
                        if mode == 'replay':
                            start['original_seq'] = 1
                        receiver.receive(self.raw(start))
                        error['seq'] = 2
                        self.assertEqual(receiver.receive(self.raw(error)), 'applied')

    def test_observer_request_and_ack_rejection_precedes_sequence_gap(self):
        for mode in ('spectate', 'replay'):
            for index in (3, 4):
                for seq in (1, 99):
                    with self.subTest(mode=mode, kind=index, seq=seq):
                        trace = self.trace('wire_complete_game')
                        welcome = trace['welcome']
                        welcome.update(mode=mode, view='public', seat=None, capabilities=['snapshot'])
                        welcome.pop('resume', None)
                        receiver = self.receiver(welcome)
                        message = trace['steps'][index]['message']
                        message['seq'] = seq
                        self.assert_rejected_atomically(receiver, message)
                        self.assertIsNone(receiver.recovery)

    def test_late_attempt_is_not_reissued_across_resume_or_snapshot(self):
        for checkpoint in ('none', 'resume', 'snapshot'):
            with self.subTest(checkpoint=checkpoint):
                trace = self.trace('queued_stale_after_end_game')
                receiver = self.receiver(trace['welcome'])
                for step in trace['steps']:
                    receiver.receive(self.raw(step['message']))
                late = deepcopy(trace['steps'][-1]['message'])
                self.assertEqual(receiver.receive(self.raw(late)), 'duplicate')
                distinct = dict(late, action_id='different-late-attempt', seq=receiver.applied + 1)
                self.assertEqual(receiver.receive(self.raw(distinct)), 'applied')
                if checkpoint == 'resume':
                    welcome = deepcopy(trace['welcome'])
                    welcome.update(resumed=True, replay_from_seq=receiver.applied + 1,
                                   replay_through_seq=receiver.applied)
                    receiver.begin_resume(welcome)
                elif checkpoint == 'snapshot':
                    identity = {key: trace['welcome'][key] for key in ('yamai', 'session_id', 'game_id')}
                    state = {key: trace['welcome'][key] for key in ('mode', 'view', 'seat', 'players')}
                    state.update(game_phase='ended', scores=receiver.game.scores.copy(),
                                 kyotaku=receiver.game.kyotaku, kyoku=None, next_kyoku=None,
                                 final_rankings=trace['steps'][-2]['message']['event']['rankings'],
                                 time_bank_ms=receiver.time_bank_ms, pending_requests=[])
                    snapshot = dict(identity, kind='snapshot', seq=receiver.applied + 1,
                                    replaces_through_seq=receiver.applied, state=state)
                    self.assertEqual(receiver.receive(self.raw(snapshot)), 'applied')
                late['seq'] = receiver.applied + 1
                self.assert_rejected_atomically(receiver, late)

    def test_observer_initial_snapshot_matches_welcome_and_first_sequence(self):
        for defect in ('none', 'scores', 'sequence'):
            with self.subTest(defect=defect):
                trace = self.trace('observer_bootstrap_authorization')
                receiver = self.receiver(trace['welcome'], initial_snapshot=True)
                snapshot = trace['steps'][0]['message']
                if defect == 'scores':
                    snapshot['state']['scores'][0] += 100
                    snapshot['state']['scores'][1] -= 100
                elif defect == 'sequence':
                    snapshot.update(seq=2, replaces_through_seq=1)
                if defect == 'none':
                    self.assertEqual(receiver.receive(self.raw(snapshot)), 'applied')
                else:
                    self.assert_rejected_atomically(receiver, snapshot)

    def test_observer_initial_snapshot_can_be_replayed_after_a_gap(self):
        trace = self.trace('observer_bootstrap_authorization')
        receiver = self.receiver(trace['welcome'], initial_snapshot=True)
        snapshot = trace['steps'][0]['message']
        identity = {key: snapshot[key] for key in ('yamai', 'session_id', 'game_id')}
        event = dict(identity, kind='event', seq=2,
                     event=dict(type='dahai', actor=0, pai='9s', tsumogiri=True))
        self.assertEqual(receiver.receive(self.raw(event)), 'sequence_gap')
        self.assertEqual(receiver.receive(self.raw(snapshot)), 'applied')
        self.assertEqual(receiver.receive(self.raw(event)), 'applied')
        self.assertIsNone(receiver.recovery)

    def test_unsupported_mode_or_view_has_a_distinct_error(self):
        trace = self.trace('unsupported_mode')
        for context in ({'supported_modes':[]}, {'supported_views':{trace['join']['mode']:[]}}):
            with self.subTest(context=context):
                validate = v._session_schema_validator(self.schemas, self.digest)
                with self.assertRaises(SessionError) as caught:
                    negotiate(trace['hello'], trace['join'], trace['welcome'], context,
                              validate, v.PROTOCOL, v.PROFILE_REVISION, self.digest)
                self.assertEqual(caught.exception.code, 'unsupported_view')

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
        protocol = v.strict_load(v.ROOT / f'registry/protocol/{v.PROTOCOL}/registry.json')
        rules = v.strict_load(v.ROOT / f'registry/riichi-4p/{v.PROFILE_REVISION}/registry.json')
        one, two = deepcopy(protocol), deepcopy(protocol)
        one['x_test_message'] = {'kind':'join','profile_hash':'sha256:'+'a'*64}
        two['x_test_message'] = {'kind':'join','profile_hash':'sha256:'+'b'*64}
        self.assertEqual(v.profile_hash(one, rules), v.profile_hash(two, rules))
        one['x_test_message'].pop('kind')
        two['x_test_message'].pop('kind')
        self.assertNotEqual(v.profile_hash(one, rules), v.profile_hash(two, rules))

    def assert_rejected_atomically(self, receiver, message):
        def state():
            return (receiver.applied, receiver.time_bank_ms, receiver.requests,
                    receiver.request_clock_floor, receiver.late_ack_clocks, receiver.late_attempts,
                    receiver.terminal_acks, receiver.expected_effects,
                    receiver.active_requests, receiver.known, receiver.event_seq_floor,
                    receiver.request_ids, receiver.observed_request_ids,
                    vars(receiver.game))
        before = deepcopy(state())
        with self.assertRaises(SessionError) as caught:
            receiver.receive(self.raw(message))
        self.assertEqual(caught.exception.code, 'invalid_message')
        self.assertEqual(state(), before)
        self.assertTrue(receiver.closed)

    def test_observer_checkpoint_keeps_null_until_the_first_session_event(self):
        trace = self.trace('observer_bootstrap_authorization')
        receiver = self.receiver(trace['welcome'], initial_snapshot=True)
        snapshot = trace['steps'][0]['message']
        for seq in (1, 2, 3):
            snapshot.update(seq=seq, replaces_through_seq=seq - 1)
            self.assertEqual(receiver.receive(self.raw(snapshot)), 'applied')
            self.assertIsNone(receiver.last_event_seq)
            self.assertEqual(receiver.event_seq_floor, 0)

    def test_rinshan_snapshot_cannot_restore_any_seats_ippatsu(self):
        for number in (409, 410):
            name = next(k for k in self.vectors if k.startswith(f'V{number}_'))
            for side in ('positive', 'negative'):
                with self.subTest(case=name, side=side):
                    snapshot = deepcopy(self.vectors[name][side])
                    welcome = self.trace('observer_bootstrap_authorization')['welcome']
                    welcome['scores'] = snapshot['state']['scores'].copy()
                    receiver = self.receiver(welcome, initial_snapshot=True)
                    if side == 'positive':
                        self.assertEqual(receiver.receive(self.raw(snapshot)), 'applied')
                        self.assertFalse(any(s['ippatsu'] for s in receiver.game.round['reach_status']))
                    else:
                        self.assert_rejected_atomically(receiver, snapshot)

    def observer_after_discard_gap(self):
        trace = self.trace('observer_bootstrap_authorization')
        receiver = self.receiver(trace['welcome'], initial_snapshot=True)
        snapshot = trace['steps'][0]['message']
        receiver.receive(self.raw(snapshot))
        identity = {key: snapshot[key] for key in ('yamai', 'session_id', 'game_id')}
        discard = dict(type='dahai', actor=0, pai='9s', tsumogiri=True)
        receiver.receive(self.raw(dict(identity, kind='event', seq=2, event=discard)))
        future = dict(identity, kind='event', seq=4, event=dict(type='tsumo', actor=1, pai=None))
        self.assertEqual(receiver.receive(self.raw(future)), 'sequence_gap')
        state = snapshot['state']
        state['kyoku'] = deepcopy(receiver.game.round)
        state['kyoku']['turn'].update(last_event_seq=2, last_event=discard)
        snapshot.update(seq=5, replaces_through_seq=4)
        return receiver, snapshot

    def test_gap_snapshot_cannot_erase_or_rewind_the_last_event(self):
        for cause_seq in (None, 1, 2):
            with self.subTest(cause_seq=cause_seq):
                receiver, snapshot = self.observer_after_discard_gap()
                snapshot['state']['kyoku']['turn']['last_event_seq'] = cause_seq
                if cause_seq == 2:
                    self.assertEqual(receiver.receive(self.raw(snapshot)), 'applied')
                else:
                    self.assert_rejected_atomically(receiver, snapshot)

    def test_gap_snapshot_cause_must_match_its_retained_payload(self):
        receiver, snapshot = self.observer_after_discard_gap()
        snapshot['state']['kyoku']['turn']['last_event']['pai'] = '8s'
        snapshot['state']['kyoku']['rivers'][0][-1]['pai'] = '8s'
        self.assert_rejected_atomically(receiver, snapshot)

    def test_gap_snapshot_cause_cannot_name_a_snapshot_entry(self):
        trace = self.trace('observer_bootstrap_authorization')
        receiver = self.receiver(trace['welcome'], initial_snapshot=True)
        snapshot = trace['steps'][0]['message']
        receiver.receive(self.raw(snapshot))
        identity = {key: snapshot[key] for key in ('yamai', 'session_id', 'game_id')}
        future = dict(identity, kind='event', seq=3, event=dict(type='dahai', actor=0, pai='9s', tsumogiri=True))
        self.assertEqual(receiver.receive(self.raw(future)), 'sequence_gap')
        snapshot.update(seq=4, replaces_through_seq=3)
        snapshot['state']['kyoku']['turn']['last_event_seq'] = 1
        self.assert_rejected_atomically(receiver, snapshot)

    def test_snapshot_group_clocks_share_one_instant(self):
        for extra_group_time in (0, 2000):
            for clock_error in (-1, 0, 1):
                with self.subTest(extra=extra_group_time, error=clock_error):
                    trace = self.trace('snapshot_group_remaining_cannot_increase')
                    receiver = self.receiver(trace['welcome'])
                    snapshot = trace['steps'][0]['message']
                    request = snapshot['state']['pending_requests'][0]
                    request['decision_group_deadline_ms'] += extra_group_time
                    request['decision_group_remaining_ms'] += extra_group_time + clock_error
                    if clock_error == 0:
                        self.assertEqual(receiver.receive(self.raw(snapshot)), 'applied')
                    else:
                        self.assert_rejected_atomically(receiver, snapshot)
                        with self.assertRaises(v.ArtifactError) as caught:
                            v._check_snapshot(snapshot, rules=trace['welcome']['rules'])
                        self.assertEqual(caught.exception.code, 'invalid_message')

    def test_snapshot_selection_cannot_occur_after_its_group_clock(self):
        for remaining, elapsed in ((4500, 499), (4500, 500), (4500, 501), (0, 1000)):
            with self.subTest(remaining=remaining, elapsed=elapsed):
                trace = self.trace('snapshot_group_remaining_cannot_increase')
                receiver = self.receiver(trace['welcome'])
                snapshot = trace['steps'][0]['message']
                request = snapshot['state']['pending_requests'][0]
                request.update(remaining_ms=0, decision_group_remaining_ms=remaining,
                               selection=dict(action_id='n', source='user', elapsed_ms=elapsed, time_bank_ms=1000))
                if remaining == 0 or elapsed <= 500:
                    self.assertEqual(receiver.receive(self.raw(snapshot)), 'applied')
                else:
                    self.assert_rejected_atomically(receiver, snapshot)
                    with self.assertRaises(v.ArtifactError):
                        v._check_snapshot(snapshot, rules=trace['welcome']['rules'])

    def test_contiguous_snapshot_preserves_committed_state_and_requests(self):
        for variant in ('scores', 'tiles', 'request_id', 'phase'):
            with self.subTest(variant=variant):
                trace = self.trace('historical_snapshot_replay')
                receiver = self.receiver(trace['welcome'])
                for step in trace['steps'][:4]:
                    receiver.receive(self.raw(step['message']))
                snapshot = trace['steps'][4]['message']
                state = snapshot['state']
                if variant == 'scores':
                    state['scores'] = [26000, 24000, 25000, 25000]
                elif variant == 'tiles':
                    state['kyoku']['hands'][0]['tiles'][0] = 'E'
                elif variant == 'request_id':
                    state['pending_requests'][0]['request_id'] = 'new-request'
                else:
                    state.update(game_phase='between_kyoku', kyoku=None, pending_requests=[],
                                 next_kyoku=dict(bakaze='E', kyoku=2, oya=1, honba=0,
                                                 kyotaku=0, extension_round=0))
                self.assert_rejected_atomically(receiver, snapshot)

    def test_contiguous_snapshot_allows_reordered_hand_and_new_selection(self):
        trace = self.trace('historical_snapshot_replay')
        receiver = self.receiver(trace['welcome'])
        for step in trace['steps'][:4]:
            receiver.receive(self.raw(step['message']))
        snapshot = trace['steps'][4]['message']
        state = snapshot['state']
        state['kyoku']['hands'][0]['tiles'].reverse()
        request = state['pending_requests'][0]
        request.update(remaining_ms=0, selection=dict(action_id=request['default_action_id'],
                       source='user', elapsed_ms=6500, time_bank_ms=14500))
        state['time_bank_ms'] = state['kyoku']['self_state']['time_bank_ms'] = 14500
        self.assertEqual(receiver.receive(self.raw(snapshot)), 'applied')
        self.assertEqual(receiver.time_bank_ms, 14500)

    def test_gap_without_new_event_preserves_state_and_issued_request(self):
        for variant in ('scores', 'tiles', 'request_id', 'ack_without_result'):
            with self.subTest(variant=variant):
                trace = self.trace('historical_snapshot_replay')
                receiver = self.receiver(trace['welcome'])
                for step in trace['steps'][:4]:
                    receiver.receive(self.raw(step['message']))
                snapshot = trace['steps'][4]['message']
                state = snapshot['state']
                if variant == 'scores':
                    state['scores'] = [26000, 24000, 25000, 25000]
                elif variant == 'tiles':
                    state['kyoku']['hands'][0]['tiles'][0] = 'E'
                elif variant == 'request_id':
                    state['pending_requests'][0]['request_id'] = 'replacement'
                else:
                    ack = deepcopy(trace['steps'][5]['message'])
                    ack['seq'] = 5
                    receiver.receive(self.raw(ack))
                    state['pending_requests'] = []
                    state['kyoku']['turn']['phase'] = 'resolving'
                receiver.begin_resume(dict(trace['welcome'], resumed=True, replay_from_seq=receiver.applied + 1,
                                           replay_through_seq=8))
                snapshot.update(seq=9, replaces_through_seq=8)
                self.assert_rejected_atomically(receiver, snapshot)

    def test_gap_without_new_event_can_restore_missing_request_and_selection(self):
        for received_request in (False, True):
            for selected in (False, True):
                with self.subTest(received_request=received_request, selected=selected):
                    trace = self.trace('historical_snapshot_replay')
                    receiver = self.receiver(trace['welcome'])
                    for step in trace['steps'][:4 if received_request else 3]:
                        receiver.receive(self.raw(step['message']))
                    snapshot = trace['steps'][4]['message']
                    state = snapshot['state']
                    state['kyoku']['hands'][0]['tiles'].reverse()
                    if selected:
                        request = state['pending_requests'][0]
                        request.update(remaining_ms=0, selection=dict(action_id=request['default_action_id'],
                                       source='user', elapsed_ms=6500, time_bank_ms=14500))
                        state['time_bank_ms'] = state['kyoku']['self_state']['time_bank_ms'] = 14500
                    receiver.begin_resume(dict(trace['welcome'], resumed=True, replay_from_seq=receiver.applied + 1,
                                               replay_through_seq=6))
                    snapshot.update(seq=7, replaces_through_seq=6)
                    self.assertEqual(receiver.receive(self.raw(snapshot)), 'applied')
                    self.assertEqual(receiver.active_requests, {'r1'})
                    self.assertEqual(receiver.time_bank_ms, 14500 if selected else 15000)

    def test_game_scoped_bank_cannot_increase_across_missing_rounds(self):
        for scope in ('game', 'kyoku'):
            for bank in (500, 1000, 1001, 15000):
                with self.subTest(scope=scope, bank=bank):
                    trace = self.trace('wire_complete_game')
                    trace['welcome']['rules']['time_control']['bank_scope'] = scope
                    trace['steps'][0]['message']['event']['rules']['time_control']['bank_scope'] = scope
                    trace['steps'][4]['message'].update(elapsed_ms=20000, time_bank_ms=1000)
                    receiver = self.receiver(trace['welcome'])
                    for step in trace['steps'][:6]:
                        receiver.receive(self.raw(step['message']))
                    receiver.begin_resume(dict(trace['welcome'], resumed=True, replay_from_seq=7,
                                               replay_through_seq=121))
                    snapshot = deepcopy(self.vectors['V57_snapshot_between_kyoku_deposits']['positive'])
                    snapshot.update(seq=122, replaces_through_seq=121)
                    snapshot['state']['time_bank_ms'] = bank
                    if scope == 'game' and bank > 1000:
                        self.assert_rejected_atomically(receiver, snapshot)
                    else:
                        self.assertEqual(receiver.receive(self.raw(snapshot)), 'applied')
                        self.assertEqual(receiver.time_bank_ms, bank)

    def test_snapshot_clocks_cannot_rewind(self):
        for variant in ('remaining', 'selection', 'ack'):
            for valid in (False, True):
                with self.subTest(variant=variant, valid=valid):
                    trace = self.trace('historical_snapshot_replay')
                    receiver = self.receiver(trace['welcome'])
                    for step in trace['steps'][:4]:
                        receiver.receive(self.raw(step['message']))
                    snapshot = trace['steps'][4]['message']
                    snapshot['state']['pending_requests'][0]['remaining_ms'] = 1000
                    receiver.receive(self.raw(snapshot))
                    snapshot.update(seq=6, replaces_through_seq=5)
                    elapsed = 20000 if valid else 19999
                    bank = 21000 - elapsed
                    request = snapshot['state']['pending_requests'][0]
                    if variant == 'remaining':
                        request['remaining_ms'] = 1000 if valid else 1001
                    elif variant == 'selection':
                        request.update(remaining_ms=0, selection=dict(action_id=request['default_action_id'],
                                       source='user', elapsed_ms=elapsed, time_bank_ms=bank))
                        snapshot['state']['time_bank_ms'] = snapshot['state']['kyoku']['self_state']['time_bank_ms'] = bank
                    else:
                        snapshot = deepcopy(trace['steps'][5]['message'])
                        snapshot.update(seq=6, elapsed_ms=elapsed, time_bank_ms=bank)
                    if valid:
                        self.assertEqual(receiver.receive(self.raw(snapshot)), 'applied')
                    else:
                        self.assert_rejected_atomically(receiver, snapshot)

    def test_contiguous_snapshot_preserves_next_round_and_bank(self):
        for variant in ('next_round', 'time_bank'):
            with self.subTest(variant=variant):
                trace = self.trace('snapshot_between_kyoku_score_conservation')
                receiver = self.receiver(trace['welcome'])
                snapshot = trace['steps'][0]['message']
                receiver.receive(self.raw(snapshot))
                snapshot.update(seq=receiver.applied + 1, replaces_through_seq=receiver.applied)
                if variant == 'next_round':
                    snapshot['state']['next_kyoku']['honba'] += 1
                else:
                    snapshot['state']['time_bank_ms'] -= 1
                self.assert_rejected_atomically(receiver, snapshot)

    def _receiver_after_rejected_ack(self):
        trace = self.trace('wire_complete_game')
        receiver = self.receiver(trace['welcome'])
        for step in trace['steps'][:4]:
            receiver.receive(self.raw(step['message']))
        rejected = deepcopy(trace['steps'][4]['message'])
        rejected.update(status='rejected', action_id='bad', elapsed_ms=7000, time_bank_ms=14000)
        receiver.receive(self.raw(rejected))
        error = {key: rejected[key] for key in ('yamai', 'session_id', 'game_id')}
        error.update(kind='error', seq=6, code='invalid_action', severity='recoverable',
                     request_id='r1', message='not a legal candidate')
        receiver.receive(self.raw(error))
        self.assertEqual(receiver.time_bank_ms, 15000)
        return receiver, trace

    def test_rejected_ack_clock_is_a_lower_bound_for_retry(self):
        for valid in (False, True):
            with self.subTest(valid=valid):
                receiver, trace = self._receiver_after_rejected_ack()
                ack = trace['steps'][4]['message']
                elapsed = 7000 if valid else 6999
                ack.update(seq=7, elapsed_ms=elapsed, time_bank_ms=21000 - elapsed)
                if valid:
                    self.assertEqual(receiver.receive(self.raw(ack)), 'applied')
                    self.assertEqual(receiver.time_bank_ms, 14000)
                else:
                    self.assert_rejected_atomically(receiver, ack)

    def test_rejected_ack_clock_is_a_lower_bound_for_snapshot(self):
        for valid in (False, True):
            with self.subTest(valid=valid):
                receiver, trace = self._receiver_after_rejected_ack()
                snapshot = self.trace('historical_snapshot_replay')['steps'][4]['message']
                snapshot.update(seq=7, replaces_through_seq=6)
                snapshot['state']['pending_requests'][0]['remaining_ms'] = 14000 if valid else 14001
                if valid:
                    self.assertEqual(receiver.receive(self.raw(snapshot)), 'applied')
                else:
                    self.assert_rejected_atomically(receiver, snapshot)

    def test_snapshot_group_time_cannot_increase_across_recovery(self):
        for jump in (False, True):
            with self.subTest(jump=jump):
                snapshot = deepcopy(self.vectors['V60_snapshot_group_clock']['positive'])
                welcome = self.trace('wire_complete_game')['welcome']
                welcome.update(seat=1, resumed=True, replay_from_seq=1, replay_through_seq=5)
                receiver = self.receiver(welcome)
                receiver.receive(self.raw(snapshot))
                through = receiver.applied
                if jump:
                    through += 2
                    receiver.begin_resume(dict(welcome, replay_from_seq=receiver.applied + 1,
                                               replay_through_seq=through))
                snapshot.update(seq=through + 1, replaces_through_seq=through)
                snapshot['state']['pending_requests'][0]['decision_group_remaining_ms'] += 1
                self.assert_rejected_atomically(receiver, snapshot)

    def test_contiguous_replay_snapshot_cannot_skip_recorded_events(self):
        trace = self.trace('snapshot_restores_recording_cursor')
        receiver = self.receiver(trace['welcome'])
        for step in trace['steps'][:3]:
            receiver.receive(self.raw(step['message']))
        snapshot = deepcopy(trace['steps'][2]['message'])
        snapshot.update(seq=receiver.applied + 1, replaces_through_seq=receiver.applied)
        snapshot['state']['original_seq'] += 1
        self.assert_rejected_atomically(receiver, snapshot)

    def test_ended_snapshot_preserves_scores_even_when_covering_a_gap(self):
        for jump in (False, True):
            with self.subTest(jump=jump):
                trace = self.trace('snapshot_ended_score_conservation')
                receiver = self.receiver(trace['welcome'])
                snapshot = trace['steps'][0]['message']
                receiver.receive(self.raw(snapshot))
                if jump:
                    receiver.begin_resume(dict(trace['welcome'], replay_from_seq=receiver.applied + 1,
                                               replay_through_seq=receiver.applied + 2))
                through = receiver.applied + (2 if jump else 0)
                snapshot.update(seq=through + 1, replaces_through_seq=through)
                snapshot['state']['scores'] = [26000, 24000, 25000, 25000]
                snapshot['state']['final_rankings'] = [1, 4, 2, 3]
                self.assert_rejected_atomically(receiver, snapshot)

    def test_public_fu_exceptions_are_bidirectional(self):
        for fu in (20, 25):
            with self.subTest(fu=fu):
                trace = self.trace('public_hora_hand_points')
                event = trace['steps'][-1]['message']['event']
                win = event['result']['wins'][0]
                amount = ((fu * 8 * 4 + 99) // 100) * 100
                win.update(fu=fu, han=1, yakus=[dict(id='tanyao', value=1, unit='han')],
                           hand_points=amount, deltas=[-amount, amount, 0, 0])
                event.update(deltas=win['deltas'], scores=[25000 - amount, 25000 + amount, 25000, 25000],
                             next=dict(type='rotate', bakaze='E', kyoku=2, oya=1, honba=0,
                                       kyotaku=0, extension_round=0))
                receiver = self.receiver(trace['welcome'])
                for step in trace['steps'][:-1]:
                    receiver.receive(self.raw(step['message']))
                self.assert_rejected_atomically(receiver, trace['steps'][-1]['message'])

    def test_ack_rejects_another_legal_discard_without_applying_it(self):
        for status in ('accepted', 'defaulted'):
            with self.subTest(status=status):
                trace = self.trace('wire_complete_game')
                if status == 'defaulted':
                    trace['steps'][4]['message'].update(status=status, elapsed_ms=21000, time_bank_ms=0)
                receiver = self.receiver(trace['welcome'])
                for step in trace['steps'][:5]:
                    receiver.receive(self.raw(step['message']))
                bad = trace['steps'][5]['message']
                bad['event'].update(pai='1m', tsumogiri=False)
                self.assert_rejected_atomically(receiver, bad)

    def test_compound_reach_binds_its_nested_discard(self):
        for valid in (True, False):
            trace = self.trace('ack_binds_compound_discard')
            receiver = self.receiver(trace['welcome'])
            for step in trace['steps'][:-1]:
                receiver.receive(self.raw(step['message']))
            message = trace['steps'][-1]['message']
            if valid:
                message['event']['x_test_annotation'] = 'non-state annotation'
                receiver.receive(self.raw(message))
                self.assertEqual(receiver.expected_effects, [])
            else:
                message['event'].update(pai='E', tsumogiri=False)
                self.assert_rejected_atomically(receiver, message)

    def test_snapshot_cannot_change_any_frozen_selection_field_or_reopen_it(self):
        changes = ({'action_id':'d0'}, {'source':'default'}, {'elapsed_ms':2},
                   {'elapsed_ms':20000, 'time_bank_ms':1000}, None)
        for change in changes:
            with self.subTest(change=change):
                trace = self.trace('snapshot_preserves_frozen_selection')
                receiver = self.receiver(trace['welcome'])
                for step in trace['steps'][:-1]:
                    receiver.receive(self.raw(step['message']))
                message = trace['steps'][-1]['message']
                state = message['state']
                request = state['pending_requests'][0]
                if change is None:
                    request.update(selection=None, remaining_ms=1000)
                else:
                    request['selection'].update(change)
                    state['time_bank_ms'] = request['selection']['time_bank_ms']
                    state['kyoku']['self_state']['time_bank_ms'] = state['time_bank_ms']
                self.assert_rejected_atomically(receiver, message)

    def test_snapshot_and_following_ack_use_the_same_bank_formula(self):
        trace = self.trace('snapshot_validates_selection_clock')
        receiver = self.receiver(trace['welcome'])
        for step in trace['steps']:
            receiver.receive(self.raw(step['message']))
        self.assertEqual(receiver.time_bank_ms, 1000)
        self.assertEqual(receiver.active_requests, set())
        receiver = self.receiver(trace['welcome'])
        for step in trace['steps'][:4]:
            receiver.receive(self.raw(step['message']))
        message = trace['steps'][4]['message']
        message['state']['pending_requests'][0]['selection']['time_bank_ms'] = 15000
        message['state']['time_bank_ms'] = 15000
        message['state']['kyoku']['self_state']['time_bank_ms'] = 15000
        self.assert_rejected_atomically(receiver, message)

    def test_snapshot_deadline_boundary_is_reserved_for_default(self):
        for source in ('user', 'default'):
            with self.subTest(source=source):
                trace = self.trace('snapshot_validates_selection_clock')
                receiver = self.receiver(trace['welcome'])
                for step in trace['steps'][:4]:
                    receiver.receive(self.raw(step['message']))
                message = trace['steps'][4]['message']
                message['state']['pending_requests'][0]['selection'].update(source=source, elapsed_ms=21000, time_bank_ms=0)
                message['state']['time_bank_ms'] = 0
                message['state']['kyoku']['self_state']['time_bank_ms'] = 0
                if source == 'user':
                    self.assert_rejected_atomically(receiver, message)
                else:
                    receiver.receive(self.raw(message))
                    ack = trace['steps'][5]['message']
                    ack.update(status='defaulted', elapsed_ms=21000, time_bank_ms=0)
                    receiver.receive(self.raw(ack))
                    receiver.receive(self.raw(trace['steps'][6]['message']))
                    self.assertEqual(receiver.time_bank_ms, 0)

    def test_ack_preserves_frozen_selection_source_and_decision_kind(self):
        for source, status, valid in (
            ('user', 'accepted', True), ('user', 'defaulted', False),
            ('user', 'superseded', False), ('user', 'stale', True),
            ('default', 'defaulted', True), ('default', 'accepted', False),
            ('default', 'superseded', False), ('default', 'stale', True),
        ):
            with self.subTest(source=source, status=status):
                trace = self.trace('snapshot_validates_selection_clock')
                # Early default is legal only under this policy; the source
                # must still survive the snapshot and its terminal ACK.
                trace['welcome']['rules']['invalid_action_policy'] = 'default'
                trace['steps'][0]['message']['event']['rules']['invalid_action_policy'] = 'default'
                trace['steps'][4]['message']['state']['pending_requests'][0]['selection']['source'] = source
                trace['steps'][5]['message']['status'] = status
                if status == 'stale':
                    trace['welcome']['rules']['invalid_action_policy'] = 'chombo'
                    trace['steps'][0]['message']['event']['rules']['invalid_action_policy'] = 'chombo'
                    if source == 'default':
                        state = trace['steps'][4]['message']['state']
                        state['time_bank_ms'] = state['kyoku']['self_state']['time_bank_ms'] = 0
                        state['pending_requests'][0]['selection'].update(elapsed_ms=21000, time_bank_ms=0)
                        trace['steps'][5]['message'].update(elapsed_ms=21000, time_bank_ms=0)
                receiver = self.receiver(trace['welcome'])
                for step in trace['steps'][:5]:
                    receiver.receive(self.raw(step['message']))
                ack = trace['steps'][5]['message']
                if valid:
                    receiver.receive(self.raw(ack))
                    self.assertFalse(receiver.active_requests)
                else:
                    self.assert_rejected_atomically(receiver, ack)

    def test_early_default_requires_default_policy_in_snapshot_and_ack(self):
        for policy in ('reject', 'chombo', 'default'):
            for snapshot in (False, True):
                with self.subTest(policy=policy, snapshot=snapshot):
                    trace = self.trace('snapshot_validates_selection_clock')
                    trace['welcome']['rules']['invalid_action_policy'] = policy
                    trace['steps'][0]['message']['event']['rules']['invalid_action_policy'] = policy
                    receiver = self.receiver(trace['welcome'])
                    for step in trace['steps'][:4]:
                        receiver.receive(self.raw(step['message']))
                    if snapshot:
                        message = trace['steps'][4]['message']
                        message['state']['pending_requests'][0]['selection']['source'] = 'default'
                    else:
                        message = trace['steps'][5]['message']
                        message.update(seq=5, status='defaulted')
                    if policy == 'default':
                        receiver.receive(self.raw(message))
                    else:
                        self.assert_rejected_atomically(receiver, message)

    def test_cancellation_uses_a_candidate_and_late_stale_needs_auto_selection(self):
        trace = self.trace('wire_complete_game')
        receiver = self.receiver(trace['welcome'])
        for step in trace['steps'][:4]:
            receiver.receive(self.raw(step['message']))
        ack = deepcopy(trace['steps'][4]['message'])
        ack.update(status='stale', action_id='unknown')
        self.assert_rejected_atomically(receiver, ack)
        receiver = self.receiver(trace['welcome'])
        for step in trace['steps'][:6]:
            receiver.receive(self.raw(step['message']))
        late = deepcopy(trace['steps'][4]['message'])
        late.update(seq=7, status='stale')
        self.assert_rejected_atomically(receiver, late)

    def test_rejected_ack_requires_a_rejecting_policy_and_predeadline_clock(self):
        for policy in ('reject', 'chombo', 'default'):
            for elapsed in (20000, 21000):
                with self.subTest(policy=policy, elapsed=elapsed):
                    trace = self.trace('wire_complete_game')
                    trace['welcome']['rules']['invalid_action_policy'] = policy
                    trace['steps'][0]['message']['event']['rules']['invalid_action_policy'] = policy
                    receiver = self.receiver(trace['welcome'])
                    for step in trace['steps'][:4]:
                        receiver.receive(self.raw(step['message']))
                    ack = deepcopy(trace['steps'][4]['message'])
                    ack.update(status='rejected', action_id='unknown', elapsed_ms=elapsed,
                               time_bank_ms=21000 - elapsed)
                    if policy != 'default' and elapsed < 21000:
                        receiver.receive(self.raw(ack))
                        self.assertEqual(receiver.active_requests, {'r1'})
                        self.assertEqual(receiver.time_bank_ms, 15000)
                    else:
                        self.assert_rejected_atomically(receiver, ack)

    def test_snapshot_cannot_reopen_a_terminal_request(self):
        trace = self.trace('wire_complete_game')
        receiver = self.receiver(trace['welcome'])
        for step in trace['steps'][:6]:
            receiver.receive(self.raw(step['message']))
        snapshot = deepcopy(self.vectors['V55_snapshot_frozen_selection']['positive'])
        snapshot.update(seq=7, replaces_through_seq=6)
        self.assert_rejected_atomically(receiver, snapshot)

    def test_contiguous_snapshot_cannot_interrupt_ack_result(self):
        trace = self.trace('wire_complete_game')
        receiver = self.receiver(trace['welcome'])
        for step in trace['steps'][:5]:
            receiver.receive(self.raw(step['message']))
        snapshot = deepcopy(self.vectors['V55_snapshot_frozen_selection']['positive'])
        snapshot.update(seq=6, replaces_through_seq=5)
        self.assert_rejected_atomically(receiver, snapshot)

    def test_ledger_cannot_end_after_ack_without_its_adopted_event(self):
        trace = self.trace('session_immutable_wire_ledger')
        trace['messages'] = [row for row in trace['messages'] if row['message'].get('seq', 0) <= 5]
        trace['ledger'] = [row for row in trace['ledger'] if row['message']['seq'] <= 5]
        with self.assertRaises(v.ArtifactError) as caught:
            v.semantic_ledger_trace(trace, self.digest)
        self.assertEqual(caught.exception.code, 'invalid_message')

    def test_hash_preserves_schema_property_definitions(self):
        protocol = v.strict_load(v.ROOT / f'registry/protocol/{v.PROTOCOL}/registry.json')
        rules = v.strict_load(v.ROOT / f'registry/riichi-4p/{v.PROFILE_REVISION}/registry.json')
        protocol['x_test_schema'] = {'type':'object','properties':{'kind':{'const':'join'},'profile_hash':{'type':'string'}}}
        before = v.profile_hash(protocol, rules)
        protocol['x_test_schema']['properties']['profile_hash'] = {'type':'integer'}
        self.assertNotEqual(before, v.profile_hash(protocol, rules))

    def test_wire_hash_normalization_preserves_other_bytes(self):
        protocol = v.strict_load(v.ROOT / f'registry/protocol/{v.PROTOCOL}/registry.json')
        rules = v.strict_load(v.ROOT / f'registry/riichi-4p/{v.PROFILE_REVISION}/registry.json')
        one, two = deepcopy(protocol), deepcopy(protocol)
        one['x_test_capture'] = {'wire':json.dumps({'kind':'join','profile_hash':'sha256:'+'a'*64})}
        two['x_test_capture'] = {'wire':json.dumps({'kind':'join','profile_hash':'sha256:'+'b'*64})}
        self.assertEqual(v.profile_hash(one,rules),v.profile_hash(two,rules))
        two['x_test_capture']['wire'] += ' '
        self.assertNotEqual(v.profile_hash(one,rules),v.profile_hash(two,rules))

    def test_snapshot_must_carry_this_seats_open_request(self):
        welcome, messages = self.snapshot_history()
        receiver = self.receiver(welcome)
        for message in messages[:4]:
            receiver.receive(self.raw(message))
        snapshot = deepcopy(self.vectors['V18_snapshot_state']['positive'])
        snapshot['state']['pending_requests'] = []
        with self.assertRaisesRegex(SessionError, 'pending requests'):
            receiver.receive(self.raw(snapshot))

    def test_resolving_snapshot_must_not_carry_requests(self):
        welcome, messages = self.snapshot_history()
        receiver = self.receiver(welcome)
        for message in messages[:4]:
            receiver.receive(self.raw(message))
        snapshot = deepcopy(self.vectors['V18_snapshot_state']['positive'])
        snapshot['state']['kyoku']['turn']['phase'] = 'resolving'
        with self.assertRaisesRegex(SessionError, 'pending requests'):
            receiver.receive(self.raw(snapshot))

    def _snapshot_receiver(self):
        welcome, messages = self.snapshot_history()
        receiver = self.receiver(welcome)
        for message in messages[:4]:
            receiver.receive(self.raw(message))
        return receiver

    def test_snapshot_request_must_bind_this_seat_and_cause(self):
        variants = [
            ('seat', lambda s: s['state']['pending_requests'][0].update(seat=1)),
            ('owner/cause', lambda s: s['state']['pending_requests'][0].update(caused_by_seq=4)),
            ('owner/cause', lambda s: s['state']['kyoku']['turn'].update(last_event_seq=4)),
        ]
        for pattern, mutate in variants:
            with self.subTest(pattern=pattern):
                receiver = self._snapshot_receiver()
                snapshot = deepcopy(self.vectors['V18_snapshot_state']['positive'])
                mutate(snapshot)
                with self.assertRaisesRegex(SessionError, pattern):
                    receiver.receive(self.raw(snapshot))

    def test_snapshot_boundary_metadata_is_checked(self):
        variants = [
            ('replacement range', lambda s: s['state']['kyoku']['turn'].update(last_event_seq=None)),
            ('replacement range', lambda s: s['state']['kyoku']['turn'].update(last_event_seq=9)),
            ('visibility', lambda s: s['state']['kyoku']['turn'].update(
                last_event={'type': 'tsumo', 'actor': 1, 'pai': '9s'})),
            ('kyotaku', lambda s: s['state']['kyoku'].update(kyotaku=1)),
            ('time bank', lambda s: s['state']['kyoku']['self_state'].update(time_bank_ms=14000)),
            ('compound discard', lambda s: s['state']['kyoku']['self_state'].update(kuikae_forbidden=['9s'])),
        ]
        for pattern, mutate in variants:
            with self.subTest(pattern=pattern):
                receiver = self._snapshot_receiver()
                snapshot = deepcopy(self.vectors['V18_snapshot_state']['positive'])
                mutate(snapshot)
                with self.assertRaisesRegex(SessionError, pattern):
                    receiver.receive(self.raw(snapshot))

    def test_snapshot_selection_must_share_the_bank(self):
        receiver = self._snapshot_receiver()
        snapshot = deepcopy(self.vectors['V18_snapshot_state']['positive'])
        snapshot['state']['pending_requests'][0].update(
            remaining_ms=0,
            selection={'action_id': 'a1', 'source': 'user', 'elapsed_ms': 100, 'time_bank_ms': 14000})
        with self.assertRaisesRegex(SessionError, 'shared balance'):
            receiver.receive(self.raw(snapshot))

    def test_snapshot_derived_round_state_is_checked(self):
        variants = [
            ('discard window', lambda s: s['state']['kyoku']['reach_status'][1].update(state='declared')),
            ('committed kan', lambda s: s['state']['kyoku'].update(
                pending_dora={'kan_type': 'daiminkan', 'timing': 'after_rinshan_discard'})),
            ('live wall', lambda s: s['state']['kyoku'].update(haitei=True)),
            ('deposit count', lambda s: (
                s['state']['kyoku']['reach_status'][0].update(state='accepted', double=True, ippatsu=True),
                s['state']['kyoku']['rivers'][0].append({'pai': '1s', 'tsumogiri': False, 'reach': True, 'called_by': None}),
                s['state']['kyoku'].update(wall_remaining=s['state']['kyoku']['wall_remaining'] - 1),
                s['state']['kyoku']['first_turn_eligible'].__setitem__(0, False))),
            ('first-turn eligibility', lambda s: s['state']['kyoku']['first_turn_eligible'].__setitem__(0, False)),
            ('committed kan', lambda s: s['state']['kyoku'].update(rinshan=True)),
        ]
        for pattern, mutate in variants:
            with self.subTest(pattern=pattern):
                receiver = self._snapshot_receiver()
                snapshot = deepcopy(self.vectors['V18_snapshot_state']['positive'])
                mutate(snapshot)
                with self.assertRaisesRegex(SessionError, pattern):
                    receiver.receive(self.raw(snapshot))


class ResourceOutputTimers(unittest.TestCase):
    @staticmethod
    def run_trace(steps, **initial):
        from session_contract import resource_trace
        return resource_trace({'steps': steps, **initial})

    def assert_limit(self, steps, **initial):
        with self.assertRaises(SessionError) as caught:
            self.run_trace(steps, **initial)
        self.assertEqual(caught.exception.code, 'resource_limit')

    def test_request_reservation_requires_coherent_positive_capacity(self):
        for reserved_bytes, reserved_messages in ((0, 0), (100, 0), (0, 1), (1, 2)):
            for initial in ({}, {'backlog_bytes': 8388608, 'backlog_messages': 1024}):
                with self.subTest(bytes=reserved_bytes, messages=reserved_messages, initial=initial):
                    with self.assertRaises(SessionError) as caught:
                        self.run_trace([{'op': 'open_request', 'at_ms': 0,
                                         'reserved_bytes': reserved_bytes,
                                         'reserved_messages': reserved_messages}], **initial)
                    self.assertEqual(caught.exception.code, 'invalid_message')
        step = {'op': 'open_request', 'at_ms': 0, 'reserved_bytes': 3, 'reserved_messages': 3}
        self.assertEqual(self.run_trace([step])[-1]['unresolved'], 1)
        self.assert_limit([step], backlog_bytes=8388608, backlog_messages=1024)

    def test_enqueue_enforces_one_message_payload_limit(self):
        for cap in (65536, 1048576):
            for reserved in (False, True):
                prefix = ([{'op': 'open_request', 'at_ms': 0, 'reserved_bytes': cap + 1,
                            'reserved_messages': 1}] if reserved else [])
                for size in (1, cap):
                    with self.subTest(cap=cap, reserved=reserved, size=size):
                        steps = prefix + [{'op': 'enqueue', 'at_ms': 1, 'bytes': size,
                                           'uses_reservation': reserved}]
                        self.assertEqual(self.run_trace(steps, max_message_bytes=cap)[-1]['outcome'], 'ok')
                self.assert_limit(prefix + [{'op': 'enqueue', 'at_ms': 1, 'bytes': cap + 1,
                                             'uses_reservation': reserved}], max_message_bytes=cap)
                with self.assertRaises(SessionError) as caught:
                    self.run_trace(prefix + [{'op': 'enqueue', 'at_ms': 1, 'bytes': 0,
                                              'uses_reservation': reserved}], max_message_bytes=cap)
                self.assertEqual(caught.exception.code, 'invalid_message')
        self.assert_limit([{'op': 'enqueue', 'at_ms': 0, 'bytes': 1048577}],
                          backlog_bytes=8388608, backlog_messages=1024)

    def test_message_limit_configuration_cannot_expand_protocol_cap(self):
        for cap in (0, -1, True, 65536.5, 1048577):
            with self.subTest(cap=cap):
                with self.assertRaises(SessionError) as caught:
                    self.run_trace([], max_message_bytes=cap)
                self.assertEqual(caught.exception.code, 'invalid_message')

    def test_zero_byte_chunk_does_not_start_or_extend_frame_timer(self):
        idle = [{'op': 'chunk', 'at_ms': 0, 'bytes': 0},
                {'op': 'tick', 'at_ms': 60000}]
        self.assertEqual(self.run_trace(idle)[-1]['outcome'], 'ok')
        self.assert_limit([{'op': 'chunk', 'at_ms': 0, 'bytes': 1},
                           {'op': 'chunk', 'at_ms': 59999, 'bytes': 0},
                           {'op': 'tick', 'at_ms': 60000}])
        with self.assertRaises(SessionError) as caught:
            self.run_trace([{'op': 'chunk', 'at_ms': 0, 'bytes': 0},
                            {'op': 'frame_complete', 'at_ms': 1}])
        self.assertEqual(caught.exception.code, 'invalid_message')

    def test_partial_drain_cannot_refresh_initial_full_queue(self):
        for initial, drain in (({'backlog_bytes': 8388608, 'backlog_messages': 1},
                                {'bytes': 1, 'messages': 0}),
                               ({'backlog_bytes': 1024, 'backlog_messages': 1024},
                                {'bytes': 1, 'messages': 1})):
            with self.subTest(initial=initial):
                self.assert_limit([{'op': 'drain', 'at_ms': 59999, **drain},
                                   {'op': 'tick', 'at_ms': 60000}], **initial)

    def test_pressure_deadline_precedes_drain_at_exact_boundary(self):
        self.assert_limit([{'op': 'drain', 'at_ms': 60000, 'bytes': 8388608, 'messages': 1}],
                          backlog_bytes=8388608, backlog_messages=1)

    def test_both_bytes_and_messages_must_discharge(self):
        for drain in ({'bytes': 8388608, 'messages': 0}, {'bytes': 0, 'messages': 1}):
            with self.subTest(drain=drain):
                self.assert_limit([{'op': 'drain', 'at_ms': 1, **drain},
                                   {'op': 'tick', 'at_ms': 60000}],
                                  backlog_bytes=8388608, backlog_messages=1)

    def test_blocked_output_remains_required_after_queue_drains(self):
        steps = [{'op': 'enqueue', 'at_ms': 10, 'bytes': 9},
                 {'op': 'drain', 'at_ms': 20, 'bytes': 8388600, 'messages': 1}]
        self.assert_limit(steps + [{'op': 'tick', 'at_ms': 60010}],
                          backlog_bytes=8388600, backlog_messages=1)
        completed = steps + [{'op': 'enqueue', 'at_ms': 21, 'bytes': 9},
                             {'op': 'drain', 'at_ms': 22, 'bytes': 9, 'messages': 1},
                             {'op': 'tick', 'at_ms': 60010}]
        self.assertEqual(self.run_trace(completed, backlog_bytes=8388600, backlog_messages=1)[-1]['outcome'], 'ok')
        self.assert_limit(steps + [{'op': 'enqueue', 'at_ms': 59999, 'bytes': 9},
                                   {'op': 'drain', 'at_ms': 60000, 'bytes': 1, 'messages': 0},
                                   {'op': 'tick', 'at_ms': 60010}],
                          backlog_bytes=8388600, backlog_messages=1)

    def test_blocked_output_cannot_be_replaced_with_another_size(self):
        with self.assertRaises(SessionError) as caught:
            self.run_trace([{'op': 'enqueue', 'at_ms': 0, 'bytes': 9},
                            {'op': 'drain', 'at_ms': 1, 'bytes': 8388600, 'messages': 1},
                            {'op': 'enqueue', 'at_ms': 2, 'bytes': 1}],
                           backlog_bytes=8388600, backlog_messages=1)
        self.assertEqual(caught.exception.code, 'invalid_message')

    def test_completed_pressure_cohort_does_not_age_later_output(self):
        steps = [{'op': 'drain', 'at_ms': 1, 'bytes': 1, 'messages': 0},
                 {'op': 'enqueue', 'at_ms': 2, 'bytes': 1},
                 {'op': 'drain', 'at_ms': 3, 'bytes': 8388607, 'messages': 1},
                 {'op': 'tick', 'at_ms': 60000}]
        self.assertEqual(self.run_trace(steps, backlog_bytes=8388608, backlog_messages=1)[-1]['outcome'], 'ok')

    def test_unpressured_initial_backlog_has_no_write_timer(self):
        self.assertEqual(self.run_trace([{'op': 'tick', 'at_ms': 120000}],
                                        backlog_bytes=8388607, backlog_messages=1023)[-1]['outcome'], 'ok')

    def test_reservation_timer_starts_at_resolution_not_open(self):
        steps = [{'op': 'open_request', 'at_ms': 0, 'reserved_bytes': 100, 'reserved_messages': 1},
                 {'op': 'tick', 'at_ms': 1800000},
                 {'op': 'terminalize', 'at_ms': 1800000}]
        self.assertEqual(self.run_trace(steps + [{'op': 'tick', 'at_ms': 1859999}])[-1]['outcome'], 'ok')
        self.assert_limit(steps + [{'op': 'tick', 'at_ms': 1860000}])
        self.assert_limit(steps + [{'op': 'enqueue', 'at_ms': 1859998, 'bytes': 100, 'uses_reservation': True},
                                   {'op': 'drain', 'at_ms': 1859999, 'bytes': 1, 'messages': 0},
                                   {'op': 'tick', 'at_ms': 1860000}])
        delivered = steps + [{'op': 'enqueue', 'at_ms': 1800001, 'bytes': 100, 'uses_reservation': True},
                             {'op': 'drain', 'at_ms': 1800002, 'bytes': 100, 'messages': 1},
                             {'op': 'tick', 'at_ms': 1860000}]
        self.assertEqual(self.run_trace(delivered)[-1]['outcome'], 'ok')

    def test_reserved_results_keep_existing_backpressure_boundary(self):
        vectors = v.strict_load(v.ROOT / f'test-vectors/protocol/{v.PROTOCOL}/vectors.json')
        case = vectors['V120_reserved_output_survives_backpressure']
        from session_contract import resource_trace
        self.assertEqual(resource_trace(case['positive']['trace']), case['positive']['trace']['expected'])
        with self.assertRaises(SessionError) as caught:
            resource_trace(case['negative']['trace'])
        self.assertEqual(caught.exception.code, 'resource_limit')


class _LedgerTraceFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schemas = v.SchemaSet()
        manifest = v.strict_load(v.ROOT / f'test-vectors/protocol/{v.PROTOCOL}/manifest.json')
        cls.digest = manifest['profile_hash']
        cls.vectors = v.strict_load(v.ROOT / manifest['vectors'])

    def receiver(self, welcome):
        return Receiver(welcome, v.strict_load_bytes, v._session_schema_validator(self.schemas, self.digest))

    @staticmethod
    def raw(message):
        return json.dumps(message, ensure_ascii=False, separators=(',', ':')).encode()

    def check_ledger(self, trace):
        for step in trace['messages']:
            step['wire'] = self.raw(step['message']).decode()
        trace['ledger'] = list({
            step['message']['seq']: {key: deepcopy(step[key]) for key in ('transaction_id', 'operation_id', 'message', 'wire')}
            | {'seq': step['message']['seq']}
            for step in trace['messages'] if 'seq' in step['message'] and step['direction'] == 'out'
        }.values())
        v.semantic_ledger_trace(trace, self.digest)


class LedgerStartClockTests(_LedgerTraceFixture):
    def buffered_turn(self, start):
        trace = deepcopy(self.vectors['V261_session_immutable_wire_ledger']['positive']['trace'])
        trace['messages'][6]['group_start'] = start
        ack = trace['messages'][8]['message']
        ack['elapsed_ms'] = 0
        ack['time_bank_ms'] = trace['messages'][6]['message']['time_bank_ms']
        return trace

    def test_buffered_turn_ack_waits_for_clock_start(self):
        # Input at 819 is buffered until the implicit decision starts at 820.
        self.check_ledger(self.buffered_turn(820))
        with self.assertRaisesRegex(v.ArtifactError, 'ACK precedes its request clock start'):
            self.check_ledger(self.buffered_turn(821))

    def test_early_default_policy_ack_waits_for_clock_start(self):
        for start, valid in ((820, True), (821, False)):
            with self.subTest(start=start):
                trace = self.buffered_turn(start)
                for step in trace['messages']:
                    message = step['message']
                    if message['kind'] == 'welcome':
                        message['rules']['invalid_action_policy'] = 'default'
                    elif message.get('event', {}).get('type') == 'start_game':
                        message['event']['rules']['invalid_action_policy'] = 'default'
                trace['messages'][7]['message']['action_id'] = 'unknown'
                trace['messages'][8]['message']['status'] = 'defaulted'
                if valid:
                    self.check_ledger(trace)
                else:
                    with self.assertRaisesRegex(v.ArtifactError, 'ACK precedes its request clock start'):
                        self.check_ledger(trace)

    def test_rejected_buffered_input_waits_for_clock_start(self):
        for start, valid in ((820, True), (821, False)):
            with self.subTest(start=start):
                trace = self.buffered_turn(start)
                trace['messages'] = trace['messages'][:9]
                trace['allow_open_requests'] = True
                trace['messages'][7]['message']['action_id'] = 'unknown'
                ack_step = trace['messages'][8]
                ack_step['message'].update(action_id='unknown', status='rejected')
                error = deepcopy(ack_step)
                error['message'] = {key: ack_step['message'][key]
                                    for key in ('yamai', 'session_id', 'game_id')}
                error['message'].update(kind='error', seq=6, code='invalid_action',
                                        severity='recoverable', message='Unknown action',
                                        request_id='r1', action_id='unknown')
                trace['messages'].append(error)
                if valid:
                    self.check_ledger(trace)
                else:
                    with self.assertRaisesRegex(v.ArtifactError, 'ACK precedes its request clock start'):
                        self.check_ledger(trace)


class LedgerPlayerFatalTests(_LedgerTraceFixture):
    def player_fatal_ledger(self, *, after_ack=False):
        trace = deepcopy(self.vectors["V261_session_immutable_wire_ledger"]["positive"]["trace"])
        trace["messages"] = trace["messages"][:9 if after_ack else 7]
        welcome = trace["messages"][2]["message"]
        fatal = {key: welcome[key] for key in ("yamai", "session_id", "game_id")}
        fatal.update(kind="error", code="invalid_message", severity="fatal", message="Peer protocol violation")
        trace["messages"].append({"at_ms": 821 if after_ack else 100, "direction": "in",
                                  "client_id": "peer", "message": fatal})
        return trace

    def test_ledger_player_fatal_closes_without_changing_game_request_or_clock(self):
        for after_ack in (False, True):
            with self.subTest(after_ack=after_ack):
                trace = self.player_fatal_ledger(after_ack=after_ack)
                expected = self.receiver(trace["messages"][2]["message"])
                for step in trace["messages"][3:-1]:
                    if step["direction"] == "out":
                        expected.receive(self.raw(step["message"]))
                receivers = []

                def capture(*args, **kwargs):
                    receiver = Receiver(*args, **kwargs)
                    receivers.append(receiver)
                    return receiver

                with patch.object(v, "Receiver", side_effect=capture):
                    self.check_ledger(trace)
                actual = receivers[0]
                self.assertTrue(actual.closed)
                self.assertEqual(actual.fatal_error, trace["messages"][-1]["message"])
                self.assertEqual(vars(actual.game), vars(expected.game))
                for key, value in vars(expected).items():
                    if key not in {"game", "decode", "validate", "closed", "fatal_error"}:
                        self.assertEqual(getattr(actual, key), value, key)
                resumed = deepcopy(actual.welcome)
                resumed.update(resumed=True, replay_from_seq=actual.applied + 1,
                               replay_through_seq=actual.applied)
                with self.assertRaisesRegex(SessionError, "fatal receiver"):
                    actual.begin_resume(resumed)

    def test_ledger_player_fatal_discards_repeated_and_buffered_input(self):
        trace = self.player_fatal_ledger()
        fatal = deepcopy(trace["messages"][-1])
        action = deepcopy(self.vectors["V261_session_immutable_wire_ledger"]["positive"]["trace"]["messages"][7])
        action["message"]["request_id"] = "unknown_after_player_fatal"
        trace["messages"].extend([fatal, action, deepcopy(action)])
        self.check_ledger(trace)

    def test_ledger_player_fatal_rejects_postclosure_ack_event_and_retransmission(self):
        original = self.vectors["V261_session_immutable_wire_ledger"]["positive"]["trace"]
        for after_ack, output in ((False, original["messages"][8]), (True, original["messages"][9]),
                                  (False, original["messages"][3]), (False, original["messages"][6])):
            with self.subTest(kind=output["message"]["kind"], after_ack=after_ack):
                trace = self.player_fatal_ledger(after_ack=after_ack)
                after = deepcopy(output)
                after["at_ms"] = 822
                trace["messages"].append(after)
                with self.assertRaises(v.ArtifactError):
                    self.check_ledger(trace)
        # A fatal received before ingress cannot be treated as permission to
        # accept the later user choice and finish its original live transcript.
        trace = deepcopy(original)
        trace["messages"].insert(7, self.player_fatal_ledger()["messages"][-1])
        with self.assertRaises(v.ArtifactError):
            self.check_ledger(trace)

    def test_ledger_player_fatal_requires_valid_direction_schema_and_identity(self):
        changes = ({"code": "resume_unavailable"}, {"code": "invalid_action"},
                   {"seq": 5}, {"session_id": "other"}, {"game_id": "other"},
                   {"message": ""}, {"severity": "recoverable"})
        for change in changes:
            with self.subTest(change=change):
                trace = self.player_fatal_ledger()
                trace["messages"][-1]["message"].update(change)
                receivers = []

                def capture(*args, **kwargs):
                    receiver = Receiver(*args, **kwargs)
                    receivers.append(receiver)
                    return receiver

                with patch.object(v, "Receiver", side_effect=capture):
                    with self.assertRaises(v.ArtifactError):
                        self.check_ledger(trace)
                # Schema rejection may occur before receiver construction.
                # A parsed error must pass direction and identity validation
                # before it can change the session's fatal state.
                for receiver in receivers:
                    self.assertFalse(receiver.closed)
                    self.assertIsNone(receiver.fatal_error)
                    self.assertEqual(receiver.applied, 4)
                    self.assertEqual(receiver.active_requests, {"r1"})
                    self.assertEqual(receiver.time_bank_ms, 15000)


class LedgerLifecycleBindingTests(_LedgerTraceFixture):
    def group_trace(self):
        return deepcopy(self.vectors['V267_session_three_member_group_is_atomic']['positive']['trace'])

    @staticmethod
    def refresh_lifecycle(trace):
        lifecycle = trace['request_lifecycles'][0]
        lifecycle['expected'] = v.evaluate_request_contract(lifecycle)[-1]
        return lifecycle

    @staticmethod
    def renumber_outputs(trace):
        # Use only for fixtures without replayed output; replay tests preserve
        # both the original sequence and immutable transaction identity.
        seq = 0
        for step in trace['messages']:
            if step['direction'] == 'out' and 'seq' in step['message']:
                seq += 1
                step['message']['seq'] = seq

    def fatal_step(self, trace, direction, at_ms):
        message = {key: trace['messages'][2]['message'][key] for key in ('yamai', 'session_id', 'game_id')}
        message.update(kind='error', severity='fatal', code='invalid_message', message='Close this peer')
        step = {'at_ms': at_ms, 'direction': direction, 'client_id': 'peer', 'message': message}
        if direction == 'out':
            message['seq'] = 1 + max(s['message'].get('seq', 0) for s in trace['messages'])
            step.update(transaction_id='fatal_tx', operation_id='fatal_op')
        return step

    def rejected_group(self):
        trace = self.group_trace()
        lifecycle = trace['request_lifecycles'][0]
        lifecycle['steps'].insert(0, {'op': 'submit', 'at_us': 0, 'request_id': 'r1', 'action_id': 'bad'})
        self.refresh_lifecycle(trace)
        action = deepcopy(trace['messages'][8])
        action['at_ms'] = 7
        action['message']['action_id'] = 'bad'
        ack = deepcopy(trace['messages'][9])
        ack.update(at_ms=7, transaction_id='reject_tx', operation_id='reject_op')
        ack['message'].update(action_id='bad', status='rejected', elapsed_ms=0)
        error = deepcopy(ack)
        error['message'] = {key: ack['message'][key] for key in ('yamai', 'session_id', 'game_id', 'seq')}
        error['message'].update(kind='error', code='invalid_action', severity='recoverable',
                                message='Unknown action', request_id='r1', action_id='bad')
        trace['messages'][8:8] = [action, ack, error]
        self.renumber_outputs(trace)
        return trace

    def conflict_group(self, after_terminal):
        trace = self.group_trace()
        lifecycle = trace['request_lifecycles'][0]
        action = deepcopy(trace['messages'][8])
        action['at_ms'] = 11 if after_terminal else 8
        action['message']['action_id'] = 'n'
        error = deepcopy(trace['messages'][9])
        error.update(at_ms=action['at_ms'], transaction_id='conflict_tx', operation_id='conflict_op')
        error['message'] = {key: error['message'][key] for key in ('yamai', 'session_id', 'game_id', 'seq')}
        error['message'].update(kind='error', code='request_conflict', severity='recoverable',
                                message='Choice already fixed', request_id='r1', action_id='n')
        submission = {'op': 'submit', 'at_us': 4000 if after_terminal else 1000,
                      'request_id': 'r1', 'action_id': 'n'}
        if after_terminal:
            error['message']['original_status'] = 'superseded'
            lifecycle['steps'].append(submission)
            trace['messages'].extend([action, error])
        else:
            lifecycle['steps'].insert(1, submission)
            trace['messages'][9:9] = [action, error]
        self.refresh_lifecycle(trace)
        self.renumber_outputs(trace)
        return trace

    def test_live_group_binds_logical_outputs_once(self):
        for rejected in (False, True):
            trace = self.rejected_group() if rejected else self.group_trace()
            self.check_ledger(trace)
            for output_index in ((9, 10, 12) if rejected else (9,)):
                with self.subTest(rejected=rejected, output_index=output_index):
                    repeated = deepcopy(trace)
                    replay = deepcopy(repeated['messages'][output_index])
                    replay['at_ms'] = repeated['messages'][-1]['at_ms']
                    repeated['messages'].append(replay)
                    self.check_ledger(repeated)

    def test_group_ack_waits_for_start_without_lifecycle_annotation(self):
        for start, valid in ((9, True), (10, False)):
            with self.subTest(start=start):
                trace = self.group_trace()
                trace.pop('request_lifecycles')
                trace['messages'][7]['group_start'] = start
                trace['messages'][9]['message']['elapsed_ms'] = 0
                if valid:
                    self.check_ledger(trace)
                else:
                    with self.assertRaisesRegex(v.ArtifactError, 'ACK precedes its request clock start'):
                        self.check_ledger(trace)

    def test_live_group_reject_diagnostics_allow_optional_ids(self):
        for omitted in ((), ('request_id',), ('action_id',), ('request_id', 'action_id')):
            with self.subTest(omitted=omitted):
                trace = self.rejected_group()
                for key in omitted:
                    trace['messages'][10]['message'].pop(key)
                self.check_ledger(trace)

    def test_live_group_rejects_wrong_missing_extra_and_reordered_diagnostics(self):
        mutations = ({'action_id': 'WRONG'}, {'request_id': 'WRONG'}, {'code': 'request_conflict'})
        for change in mutations:
            with self.subTest(change=change):
                trace = self.rejected_group()
                trace['messages'][10]['message'].update(change)
                with self.assertRaises(v.ArtifactError):
                    self.check_ledger(trace)
        for operation in ('missing', 'extra', 'reordered'):
            with self.subTest(operation=operation):
                trace = self.rejected_group()
                if operation == 'missing':
                    trace['messages'].pop(10)
                elif operation == 'extra':
                    trace['messages'].insert(11, deepcopy(trace['messages'][10]))
                else:
                    trace['messages'][9], trace['messages'][10] = trace['messages'][10], trace['messages'][9]
                self.renumber_outputs(trace)
                with self.assertRaises(v.ArtifactError):
                    self.check_ledger(trace)

    def test_lifecycle_outputs_cannot_precede_local_input_or_group_resolution(self):
        for omitted in (False, True):
            with self.subTest(omitted=omitted):
                trace = self.rejected_group()
                if omitted:
                    trace['messages'][10]['message'].pop('request_id')
                    trace['messages'][10]['message'].pop('action_id')
                trace['messages'][8:11] = [trace['messages'][9], trace['messages'][10], trace['messages'][8]]
                with self.assertRaises(v.ArtifactError):
                    self.check_ledger(trace)
        trace = self.group_trace()
        # The selected seat's elapsed clock is unchanged, but the barrier must
        # wait for the later hidden survivors before producing its ACK.
        for step in trace['request_lifecycles'][0]['steps'][1:]:
            step['at_us'] = 2999
        self.refresh_lifecycle(trace)
        self.check_ledger(trace)  # at_ms=9 can represent 2.999ms after group start.
        trace['request_lifecycles'][0]['steps'][-1]['at_us'] = 3000
        self.refresh_lifecycle(trace)
        with self.assertRaises(v.ArtifactError):
            self.check_ledger(trace)  # ACK at_ms=9 precedes resolution at_ms=10.

    def test_group_conflicts_preserve_pre_and_postterminal_order_and_status(self):
        for after_terminal in (False, True):
            trace = self.conflict_group(after_terminal)
            self.check_ledger(trace)
            error_index = -1 if after_terminal else 10
            for change in ({'action_id': 'bad'}, {'original_status': 'accepted'}):
                with self.subTest(after_terminal=after_terminal, change=change):
                    invalid = deepcopy(trace)
                    invalid['messages'][error_index]['message'].update(change)
                    with self.assertRaises(v.ArtifactError):
                        self.check_ledger(invalid)

    def test_fatal_preserves_selected_or_unselected_seat_and_later_survivors(self):
        for direction in ('in', 'out'):
            for selected in (False, True):
                with self.subTest(direction=direction, selected=selected):
                    trace = self.group_trace()
                    lifecycle = trace['request_lifecycles'][0]
                    if not selected:
                        lifecycle['steps'].pop(0)
                    for step in lifecycle['steps'][1 if selected else 0:]:
                        step['at_us'] = 2000 if step['op'] == 'submit' else 3000
                    self.refresh_lifecycle(trace)
                    trace['messages'] = trace['messages'][:9 if selected else 8]
                    trace['messages'].append(self.fatal_step(trace, direction, 8 if selected else 7))
                    expected = self.receiver(trace['messages'][2]['message'])
                    for step in trace['messages'][3:-1]:
                        if step['direction'] == 'out':
                            expected.receive(self.raw(step['message']))
                    receivers = []

                    def capture(*args, **kwargs):
                        receiver = Receiver(*args, **kwargs)
                        receivers.append(receiver)
                        return receiver

                    with patch.object(v, 'Receiver', side_effect=capture):
                        self.check_ledger(trace)
                    receiver = receivers[0]
                    self.assertTrue(receiver.closed)
                    self.assertEqual(vars(receiver.game), vars(expected.game))
                    self.assertEqual(receiver.requests, expected.requests)
                    self.assertEqual(receiver.active_requests, {'r1'})
                    self.assertEqual(receiver.terminal_acks, {})
                    self.assertEqual(receiver.time_bank_ms, 1)
                    state = lifecycle['expected']['requests']['r1']
                    self.assertEqual((state['action_id'], state['source'], state['elapsed_ms']),
                                     ('h', 'user', 1) if selected else ('n', 'default', 3))
                    self.assertEqual(state['time_bank_ms'], 1 if selected else 0)
                    for rid in ('r2', 'r3'):
                        self.assertEqual(lifecycle['expected']['requests'][rid]['elapsed_ms'], 2)
                    buffered = deepcopy(self.group_trace()['messages'][8])
                    buffered['at_ms'] = 9
                    buffered['message']['action_id'] = 'n'
                    trace['messages'].append(buffered)
                    self.check_ledger(trace)
                    lifecycle['steps'].insert(1 if selected else 0,
                        {'op': 'submit', 'at_us': 2000, 'request_id': 'r1', 'action_id': 'n'})
                    self.refresh_lifecycle(trace)
                    with self.assertRaises(v.ArtifactError):
                        self.check_ledger(trace)

    def test_fatal_suppresses_only_unemitted_output_suffix(self):
        for direction in ('in', 'out'):
            for prefix in (9, 10, 11):
                with self.subTest(direction=direction, prefix=prefix):
                    trace = self.rejected_group()
                    # Retain the rejected attempt and zero, one or both of its
                    # outputs, then resolve the other seats after fatal closure.
                    lifecycle = trace['request_lifecycles'][0]
                    lifecycle['steps'].pop(1)  # The later r1=h input was not received.
                    for step in lifecycle['steps'][1:]:
                        step['at_us'] = 2000 if step['op'] == 'submit' else 3000
                    self.refresh_lifecycle(trace)
                    trace['messages'] = trace['messages'][:prefix]
                    trace['messages'].append(self.fatal_step(trace, direction, 7))
                    self.check_ledger(trace)
                    if prefix == 11:
                        trace['messages'][10]['message']['action_id'] = 'WRONG'
                        with self.assertRaises(v.ArtifactError):
                            self.check_ledger(trace)
        trace = self.rejected_group()
        trace['messages'].pop(9)  # Keep the error but omit the preceding rejected ACK.
        trace['messages'] = trace['messages'][:10]
        self.renumber_outputs(trace)
        trace['messages'].append(self.fatal_step(trace, 'in', 7))
        lifecycle = trace['request_lifecycles'][0]
        lifecycle['steps'].pop(1)
        for step in lifecycle['steps'][1:]:
            step['at_us'] = 3000
        self.refresh_lifecycle(trace)
        with self.assertRaises(v.ArtifactError):
            self.check_ledger(trace)

    def test_guessed_request_id_before_issue_stays_a_generic_diagnostic(self):
        trace = self.group_trace()
        action = deepcopy(trace['messages'][8])
        action['at_ms'] = 6
        action['message']['action_id'] = 'bad'
        error = deepcopy(self.rejected_group()['messages'][10])
        error['at_ms'] = 6
        trace['messages'][7:7] = [action, error]
        self.renumber_outputs(trace)
        self.check_ledger(trace)
        trace['messages'].pop(8)
        self.renumber_outputs(trace)
        with self.assertRaises(v.ArtifactError):
            self.check_ledger(trace)

    def test_optional_diagnostics_match_generic_and_lifecycle_inputs_jointly(self):
        for final_rid in ('r1', 'unknown'):
            with self.subTest(final_rid=final_rid):
                trace = self.rejected_group()
                unknown = deepcopy(trace['messages'][8])
                unknown['message']['request_id'] = 'unknown'
                trace['messages'].insert(8, unknown)
                explicit = deepcopy(trace['messages'][11])
                explicit['message']['request_id'] = final_rid
                for key in ('request_id', 'action_id'):
                    trace['messages'][11]['message'].pop(key)
                trace['messages'].insert(12, explicit)
                self.renumber_outputs(trace)
                self.check_ledger(trace)
                # One response cannot satisfy both the unknown request and
                # the lifecycle's rejected-attempt diagnostic.
                trace['messages'].pop(12)
                self.renumber_outputs(trace)
                with self.assertRaises(v.ArtifactError):
                    self.check_ledger(trace)

    def test_live_capture_still_requires_exact_lifecycle_ack(self):
        trace = self.group_trace()
        trace['messages'] = trace['messages'][:9]
        trace['allow_open_requests'] = True
        with self.assertRaisesRegex(v.ArtifactError, 'omits lifecycle output'):
            self.check_ledger(trace)
        for change in ({'elapsed_ms': 0}, {'action_id': 'n', 'status': 'passed'},
                       {'status': 'accepted'}, {'time_bank_ms': 0}):
            with self.subTest(change=change):
                trace = self.group_trace()
                trace['messages'][9]['message'].update(change)
                with self.assertRaises(v.ArtifactError):
                    self.check_ledger(trace)

    def test_fatal_discards_only_not_yet_effective_group_buffered_input(self):
        for direction in ('in', 'out'):
            for at_ms in (9, 10, 11):
                with self.subTest(direction=direction, at_ms=at_ms):
                    trace = self.group_trace()
                    trace['messages'][7]['group_start'] = 10
                    trace['messages'] = trace['messages'][:9]
                    trace['messages'].append(self.fatal_step(trace, direction, at_ms))
                    lifecycle = trace['request_lifecycles'][0]
                    selected = at_ms >= 10
                    if selected:
                        lifecycle['steps'][0]['at_us'] = 0
                    else:
                        lifecycle['steps'].pop(0)
                    for step in lifecycle['steps'][1 if selected else 0:]:
                        step['at_us'] = 2000 if step['op'] == 'submit' else 3000
                    self.refresh_lifecycle(trace)
                    self.check_ledger(trace)
                    if not selected:
                        lifecycle['steps'].insert(0, {'op': 'submit', 'at_us': 0,
                                                     'request_id': 'r1', 'action_id': 'h'})
                        self.refresh_lifecycle(trace)
                        with self.assertRaises(v.ArtifactError):
                            self.check_ledger(trace)

    def test_postgame_input_is_not_a_lifecycle_submission(self):
        trace = deepcopy(self.vectors['V261_session_immutable_wire_ledger']['positive']['trace'])
        request = {key: value for key, value in trace['messages'][6]['message'].items()
                   if key not in ('yamai', 'kind', 'session_id', 'game_id', 'seq')}
        rules = trace['messages'][2]['message']['rules']
        lifecycle = {'trace_type': 'request_lifecycle', 'grace_ms': rules['time_control']['grace_ms'],
                     'invalid_action_policy': rules['invalid_action_policy'], 'ron_policy': rules['ron_policy'],
                     'requests': [request], 'steps': [
                         {'op': 'submit', 'at_us': 812000, 'request_id': 'r1', 'action_id': 'a1'},
                         {'op': 'resolve', 'at_us': 812000}]}
        trace['request_lifecycles'] = [lifecycle]
        self.refresh_lifecycle(trace)
        self.check_ledger(trace)
        action = deepcopy(trace['messages'][7])
        action['at_ms'] = 824
        action['message']['action_id'] = 'postgame'
        trace['messages'].append(action)
        self.check_ledger(trace)
        lifecycle['steps'].append({'op': 'submit', 'at_us': 817000, 'request_id': 'r1', 'action_id': 'postgame'})
        self.refresh_lifecycle(trace)
        with self.assertRaises(v.ArtifactError):
            self.check_ledger(trace)


class LedgerResumeCaptureTests(_LedgerTraceFixture):
    def live_trace(self, default=False):
        name = 'V263_session_timeout_keeps_original_deadline' if default else 'V261_session_immutable_wire_ledger'
        return deepcopy(self.vectors[name]['positive']['trace'])

    def resume_trace(self, source=None, *, through=8, offset=100000, keep_actions=False, compact=True):
        trace = deepcopy(source) if source is not None else self.live_trace()
        old = deepcopy(trace['messages'][2]['message'])
        join, welcome = trace['messages'][1]['message'], trace['messages'][2]['message']
        join.pop('seat', None)
        join.pop('room', None)
        join['resume'] = {'token': old['resume']['token'], 'last_seq': 0}
        welcome.update(resumed=True, replay_from_seq=1, replay_through_seq=through)
        welcome['resume']['token'] = 'rt_BBBBBBBBBBBBBBBBBBBBBB'
        ended = next((s for s in trace['messages'] if s['message'].get('event', {}).get('type') == 'end_game'), None)
        if ended is not None and ended['message']['seq'] <= through:
            welcome['scores'] = deepcopy(ended['message']['event']['scores'])
        trace['context'] = {'now_ms': offset + 2, 'secure_transport': True, 'resume_state': {
            'token': old['resume']['token'], 'expires_at_ms': 600000,
            'highest_seq': through, 'welcome': old, 'scores': deepcopy(welcome['scores'])}}
        if not keep_actions:
            trace['messages'] = [s for s in trace['messages'] if s['message']['kind'] != 'action']
        for index, step in enumerate(trace['messages']):
            step['at_ms'] = offset + (index if compact else step['at_ms'])
        return trace

    @staticmethod
    def retain(trace, selection=None, issued_at_ms=7):
        trace['context']['resume_state']['request_states'] = {
            'r1': {'issued_at_ms': issued_at_ms, 'selection': deepcopy(selection)}}

    @staticmethod
    def selected(elapsed=812, source='user', action_id='a1'):
        return {'action_id': action_id, 'source': source, 'elapsed_ms': elapsed,
                'time_bank_ms': 15000 - min(max(0, elapsed - 6000), 15000)}

    def original_clock_trace(self):
        trace = self.resume_trace(through=4, offset=10000, keep_actions=True, compact=False)
        self.retain(trace)
        next(s['message'] for s in trace['messages'] if s['message']['kind'] == 'ack').update(
            elapsed_ms=10812, time_bank_ms=10188)
        return trace

    def test_historical_accepted_and_defaulted_ack_replay_needs_no_new_input(self):
        for default in (False, True):
            with self.subTest(default=default):
                original = self.live_trace(default)
                self.check_ledger(original)
                trace = self.resume_trace(original)
                self.check_ledger(trace)
                self.assertEqual(trace['ledger'], original['ledger'])
                replay = deepcopy(next(s for s in trace['messages'] if s['message']['kind'] == 'ack'))
                replay['at_ms'] = trace['messages'][-1]['at_ms']
                trace['messages'].append(replay)
                self.check_ledger(trace)

    def test_resumed_open_request_preserves_original_elapsed_and_bank(self):
        trace = self.original_clock_trace()
        self.check_ledger(trace)
        # This exact formerly accepted counterexample reset issuance to the
        # request's delivery at 10007 instead of its original issue at 7.
        next(s['message'] for s in trace['messages'] if s['message']['kind'] == 'ack').update(
            elapsed_ms=812, time_bank_ms=15000)
        with self.assertRaisesRegex(v.ArtifactError, 'elapsed time differs'):
            self.check_ledger(trace)

    def test_resumed_open_input_requires_original_state_even_with_group_start(self):
        for original_hint in (False, True):
            with self.subTest(original_hint=original_hint):
                trace = self.original_clock_trace()
                del trace['context']['resume_state']['request_states']
                if original_hint:
                    next(s for s in trace['messages'] if s['message']['kind'] == 'request')['group_start'] = 7
                with self.assertRaisesRegex(v.ArtifactError, 'lacks original request state'):
                    self.check_ledger(trace)

    def test_action_waits_for_entire_replay_frontier(self):
        for unknown in (False, True):
            with self.subTest(unknown=unknown):
                trace = self.resume_trace(keep_actions=True)
                if unknown:
                    next(s['message'] for s in trace['messages'] if s['message']['kind'] == 'action')['request_id'] = 'unknown'
                with self.assertRaisesRegex(v.ArtifactError, 'completion of resume replay'):
                    self.check_ledger(trace)
        self.check_ledger(self.original_clock_trace())  # seq=4 has now applied.

    def test_request_newly_issued_after_replay_uses_its_new_clock(self):
        trace = self.resume_trace(through=3, offset=10000, keep_actions=True, compact=False)
        self.check_ledger(trace)
        # A duplicate request delivery on the new connection also cannot reset
        # its clock or regrant G/T/B.
        repeat = deepcopy(trace['messages'][6])
        repeat['at_ms'] += 100
        trace['messages'].insert(7, repeat)
        self.check_ledger(trace)

    def test_original_timeout_is_not_delayed_by_redelivery(self):
        trace = self.resume_trace(self.live_trace(default=True), through=4, offset=10000, compact=False)
        self.retain(trace)
        for step in trace['messages']:
            if step['message'].get('seq', 0) >= 5:
                step['at_ms'] -= 10000
        self.check_ledger(trace)  # Original deadline remains 21007.
        trace['messages'][7]['at_ms'] -= 1
        with self.assertRaisesRegex(v.ArtifactError, 'original deadline'):
            self.check_ledger(trace)
        del trace['context']['resume_state']['request_states']
        with self.assertRaisesRegex(v.ArtifactError, 'lacks original request state'):
            self.check_ledger(trace)

    def test_fixed_pre_disconnect_selection_survives_delayed_ack_and_retry(self):
        for retry in (False, True):
            with self.subTest(retry=retry):
                trace = self.resume_trace(through=4, offset=10000, keep_actions=retry, compact=False)
                self.retain(trace, self.selected())
                self.check_ledger(trace)
                ack = next(s['message'] for s in trace['messages'] if s['message']['kind'] == 'ack')
                ack.update(elapsed_ms=10812, time_bank_ms=10188)
                with self.assertRaisesRegex(v.ArtifactError, 'elapsed time differs'):
                    self.check_ledger(trace)

    def test_retained_metadata_is_strict_and_matches_historical_ack(self):
        changes = ({'issued_at_ms': True}, {'issued_at_ms': -1}, {'issued_at_ms': 100003},
                   {'issued_at_ms': 0.5}, {'selection': {}}, {'selection': []},
                   {'selection': self.selected(action_id='never-issued')},
                   {'selection': self.selected(source='cancelled')},
                   {'selection': dict(self.selected(), source=[])},
                   {'selection': dict(self.selected(), elapsed_ms=True)},
                   {'selection': dict(self.selected(), time_bank_ms=15001)},
                   {'selection': self.selected(elapsed=813)}, {'selection': None})
        for change in changes:
            with self.subTest(change=change):
                trace = self.resume_trace()
                self.retain(trace, self.selected())
                trace['context']['resume_state']['request_states']['r1'].update(change)
                with self.assertRaises(v.ArtifactError):
                    self.check_ledger(trace)
        trace = self.resume_trace()
        self.retain(trace, self.selected())
        self.check_ledger(trace)

    def test_retained_state_requires_same_clock_axis_and_known_request(self):
        for checkpoint in (0, 100003, True, 100001.5):
            with self.subTest(checkpoint=checkpoint):
                trace = self.resume_trace()
                self.retain(trace, self.selected())
                trace['context']['now_ms'] = checkpoint
                with self.assertRaises(v.ArtifactError):
                    self.check_ledger(trace)
        trace = self.resume_trace()
        self.retain(trace, self.selected())
        trace['context']['resume_state']['request_states']['unknown'] = trace['context']['resume_state']['request_states'].pop('r1')
        with self.assertRaisesRegex(v.ArtifactError, 'no restored request'):
            self.check_ledger(trace)

    def test_fatal_during_replay_does_not_require_retained_request_redelivery(self):
        trace = self.original_clock_trace()
        trace['messages'] = trace['messages'][:4]
        message = {k: trace['messages'][2]['message'][k] for k in ('yamai', 'session_id', 'game_id')}
        message.update(kind='error', severity='fatal', code='invalid_message', message='Stop this session')
        trace['messages'].append({'at_ms': 10005, 'direction': 'in', 'client_id': 'peer', 'message': message})
        self.check_ledger(trace)
        # Fatal closure suppresses future replay, not metadata validation.
        trace['context']['resume_state']['request_states']['r1']['issued_at_ms'] = True
        with self.assertRaisesRegex(v.ArtifactError, 'starts after resume checkpoint'):
            self.check_ledger(trace)

    def test_historical_open_snapshot_can_precede_latest_fixed_selection(self):
        original = self.live_trace()
        snapshot = deepcopy(self.vectors['V18_snapshot_state']['positive'])
        # V18's OPEN observation was made 3000ms after issuance. The later
        # original selection is fixed at 4000ms before this new connection.
        row = {'at_ms': 3007, 'direction': 'out', 'client_id': 'peer',
               'transaction_id': 'old_snapshot', 'operation_id': 'old_snapshot', 'message': snapshot}
        original['messages'].insert(7, row)
        for step in original['messages'][8:]:
            step['at_ms'] += 4000
            if 'seq' in step['message']:
                step['message']['seq'] += 1
            if step['message']['kind'] == 'ack':
                step['message']['elapsed_ms'] = 4000
        trace = self.resume_trace(original, through=9)
        self.retain(trace, self.selected(elapsed=4000))
        self.check_ledger(trace)
        # The historical ACK cannot disagree with the same retained selection.
        trace['context']['resume_state']['request_states']['r1']['selection']['elapsed_ms'] = 4001
        with self.assertRaisesRegex(v.ArtifactError, 'historical ACK differs'):
            self.check_ledger(trace)

    def test_historical_group_lifecycle_keeps_original_inputs_off_new_connection(self):
        source = deepcopy(self.vectors['V267_session_three_member_group_is_atomic']['positive']['trace'])
        through = max(s['message'].get('seq', 0) for s in source['messages'])
        trace = self.resume_trace(source, through=through)
        self.check_ledger(trace)
        # The same immutable bytes remain constrained by the supplied complete
        # lifecycle, even though old player actions are not replayed.
        trace['request_lifecycles'][0]['steps'][0]['action_id'] = 'n'
        trace['request_lifecycles'][0]['expected'] = v.evaluate_request_contract(trace['request_lifecycles'][0])[-1]
        with self.assertRaises(v.ArtifactError):
            self.check_ledger(trace)

    def test_historical_optional_diagnostic_ids_do_not_waive_new_obligations(self):
        helper = LedgerLifecycleBindingTests()
        helper.vectors = self.vectors
        for lifecycle in (False, True):
            for omitted in ((), ('request_id',), ('action_id',), ('request_id', 'action_id')):
                with self.subTest(lifecycle=lifecycle, omitted=omitted):
                    original = helper.rejected_group()
                    if not lifecycle:
                        original.pop('request_lifecycles')
                    error = next(s for s in original['messages'] if s['message']['kind'] == 'error')
                    for key in omitted:
                        error['message'].pop(key)
                    through = max(s['message'].get('seq', 0) for s in original['messages'])
                    trace = self.resume_trace(original, through=through)
                    self.check_ledger(trace)
                    unsolicited = deepcopy(error)
                    unsolicited.update(at_ms=trace['messages'][-1]['at_ms'], transaction_id='new_error', operation_id='new_error')
                    unsolicited['message']['seq'] = through + 1
                    trace['messages'].append(unsolicited)
                    with self.assertRaisesRegex(v.ArtifactError, 'diagnostic count'):
                        self.check_ledger(trace)

    def insert_live_snapshot(self, trace, snapshot, at_ms):
        trace['messages'].insert(7, {'at_ms': at_ms, 'direction': 'out', 'client_id': 'peer',
                                   'transaction_id': 'current_snapshot', 'operation_id': 'current_snapshot',
                                   'message': snapshot})
        for step in trace['messages'][8:]:
            if 'seq' in step['message']:
                step['message']['seq'] += 1

    def test_new_open_snapshot_clock_is_bounded_by_checkpoint_and_capture(self):
        # 10999 is current; 11005 was fixed at the checkpoint then queued.
        for remaining, valid in ((10999, True), (11005, True), (18000, False), (10998, False)):
            with self.subTest(remaining=remaining):
                trace = self.original_clock_trace()
                snapshot = deepcopy(self.vectors['V18_snapshot_state']['positive'])
                snapshot['state']['pending_requests'][0]['remaining_ms'] = remaining
                self.insert_live_snapshot(trace, snapshot, 10008)
                if valid:
                    self.check_ledger(trace)
                else:
                    with self.assertRaisesRegex(v.ArtifactError, 'snapshot OPEN clock'):
                        self.check_ledger(trace)

    def test_new_snapshot_cannot_select_a_future_timeout_or_charge_bank_early(self):
        for snapshot_at, valid in ((10008, False), (21008, True)):
            with self.subTest(snapshot_at=snapshot_at):
                trace = self.original_clock_trace()
                trace['messages'] = [s for s in trace['messages'] if s['message']['kind'] != 'action']
                snapshot = deepcopy(self.vectors['V18_snapshot_state']['positive'])
                snapshot['state']['pending_requests'][0].update(
                    remaining_ms=0, selection=self.selected(elapsed=21000, source='default'))
                snapshot['state']['time_bank_ms'] = snapshot['state']['kyoku']['self_state']['time_bank_ms'] = 0
                self.insert_live_snapshot(trace, snapshot, snapshot_at)
                for index, step in enumerate(trace['messages'][8:]):
                    step['at_ms'] = snapshot_at + 1 + index
                    if step['message']['kind'] == 'ack':
                        step['message'].update(status='defaulted', elapsed_ms=21000, time_bank_ms=0)
                if valid:
                    self.check_ledger(trace)
                else:
                    with self.assertRaisesRegex(v.ArtifactError, 'selection occurs after its captured clock'):
                        self.check_ledger(trace)

    def test_new_snapshot_selection_requires_post_resume_input(self):
        for captured in (False, True):
            with self.subTest(captured=captured):
                trace = self.original_clock_trace()
                selected = self.selected(elapsed=10000)
                if captured:
                    trace['messages'][7]['at_ms'] = 10007
                else:
                    trace['messages'] = [s for s in trace['messages'] if s['message']['kind'] != 'action']
                snapshot = deepcopy(self.vectors['V18_snapshot_state']['positive'])
                snapshot['state']['pending_requests'][0].update(remaining_ms=0, selection=selected)
                snapshot['state']['time_bank_ms'] = snapshot['state']['kyoku']['self_state']['time_bank_ms'] = selected['time_bank_ms']
                position = 8 if captured else 7
                trace['messages'].insert(position, {'at_ms': 10008, 'direction': 'out', 'client_id': 'peer',
                                                   'transaction_id': 'current_snapshot', 'operation_id': 'current_snapshot',
                                                   'message': snapshot})
                for step in trace['messages'][position + 1:]:
                    step['message']['seq'] += 1
                    if step['message']['kind'] == 'ack':
                        step['message'].update(elapsed_ms=10000, time_bank_ms=selected['time_bank_ms'])
                if captured:
                    self.check_ledger(trace)
                else:
                    with self.assertRaisesRegex(v.ArtifactError, 'no captured input'):
                        self.check_ledger(trace)

    def test_new_snapshot_cannot_supply_its_own_missing_original_state(self):
        trace = self.original_clock_trace()
        del trace['context']['resume_state']['request_states']
        trace['messages'] = [s for s in trace['messages'] if s['message']['kind'] != 'action']
        snapshot = deepcopy(self.vectors['V18_snapshot_state']['positive'])
        snapshot['state']['pending_requests'][0].update(remaining_ms=0, selection=self.selected())
        self.insert_live_snapshot(trace, snapshot, 10008)
        next(s['message'] for s in trace['messages'] if s['message']['kind'] == 'ack').update(
            elapsed_ms=812, time_bank_ms=15000)
        with self.assertRaisesRegex(v.ArtifactError, 'new snapshot selection lacks original request state'):
            self.check_ledger(trace)
        # The identical selected snapshot in the immutable replay prefix is
        # legitimate historical evidence, without a new action on this link.
        trace['context']['resume_state']['highest_seq'] = 5
        trace['messages'][2]['message']['replay_through_seq'] = 5
        self.check_ledger(trace)

    def test_retained_snapshot_request_must_exist_before_resume_frontier(self):
        trace = self.original_clock_trace()
        trace['messages'][2]['message']['replay_through_seq'] = 2
        trace['context']['resume_state']['highest_seq'] = 2
        snapshot = deepcopy(self.vectors['V18_snapshot_state']['positive'])
        snapshot['state']['pending_requests'][0]['remaining_ms'] = 10999
        # Its cause is seq=3, which does not yet exist at frontier=2; request
        # issuance therefore cannot be retained at that older checkpoint.
        trace['messages'][5:7] = [{'at_ms': 10008, 'direction': 'out', 'client_id': 'peer',
                                  'transaction_id': 'replacement', 'operation_id': 'replacement',
                                  'message': snapshot}]
        for step in trace['messages'][6:]:
            if 'seq' in step['message']:
                step['message']['seq'] += 1
        with self.assertRaisesRegex(v.ArtifactError, 'did not exist at the resume frontier'):
            self.check_ledger(trace)

    def test_partial_resume_lifecycle_binds_only_new_ingress_suffix(self):
        for selected in (False, True):
            with self.subTest(selected=selected):
                trace = self.original_clock_trace()
                if selected:
                    self.retain(trace, self.selected())
                    next(s['message'] for s in trace['messages'] if s['message']['kind'] == 'ack').update(
                        elapsed_ms=812, time_bank_ms=15000)
                request = {k: value for k, value in trace['messages'][6]['message'].items()
                           if k not in ('yamai', 'kind', 'session_id', 'game_id', 'seq')}
                rules = trace['messages'][2]['message']['rules']
                steps = ([{'op': 'submit', 'at_us': 812000, 'request_id': 'r1', 'action_id': 'a1'}] if selected else [])
                steps += [{'op': 'submit', 'at_us': 10812000, 'request_id': 'r1', 'action_id': 'a1'},
                          {'op': 'resolve', 'at_us': 10812000}]
                lifecycle = {'trace_type': 'request_lifecycle', 'grace_ms': rules['time_control']['grace_ms'],
                             'invalid_action_policy': rules['invalid_action_policy'], 'ron_policy': rules['ron_policy'],
                             'requests': [request], 'steps': steps}
                lifecycle['expected'] = v.evaluate_request_contract(lifecycle)[-1]
                trace['request_lifecycles'] = [lifecycle]
                self.check_ledger(trace)
                trace['messages'] = [s for s in trace['messages'] if s['message']['kind'] != 'action']
                with self.assertRaises(v.ArtifactError):
                    self.check_ledger(trace)

    def test_new_live_acks_still_require_captured_user_or_original_timeout(self):
        trace = self.live_trace()
        trace['messages'] = [s for s in trace['messages'] if s['message']['kind'] != 'action']
        with self.assertRaisesRegex(v.ArtifactError, 'no user choice was captured'):
            self.check_ledger(trace)
        trace = self.live_trace(default=True)
        trace['messages'][7]['at_ms'] -= 1
        with self.assertRaisesRegex(v.ArtifactError, 'original deadline'):
            self.check_ledger(trace)

    def test_retained_group_selection_binds_new_ack_without_lifecycle(self):
        source = deepcopy(self.vectors['V267_session_three_member_group_is_atomic']['positive']['trace'])
        source.pop('request_lifecycles')
        for change in ({}, {'elapsed_ms': 0}, {'action_id': 'n'}, {'source': 'default'}, {'time_bank_ms': 0}):
            with self.subTest(change=change):
                trace = self.resume_trace(source, through=5, offset=10000, compact=False)
                selected = {'action_id': 'h', 'source': 'user', 'elapsed_ms': 1, 'time_bank_ms': 1}
                selected.update(change)
                self.retain(trace, selected)
                if change:
                    with self.assertRaises(v.ArtifactError):
                        self.check_ledger(trace)
                else:
                    self.check_ledger(trace)

    def test_retained_open_group_new_ack_needs_own_input_or_original_timeout(self):
        for captured, default, valid in ((True, False, True), (False, False, False), (False, True, True)):
            with self.subTest(captured=captured, default=default):
                original = deepcopy(self.vectors['V267_session_three_member_group_is_atomic']['positive']['trace'])
                original.pop('request_lifecycles')
                original['allow_open_requests'] = True
                original['messages'] = [s for s in original['messages'] if s['message'].get('seq', 0) <= 6]
                trace = self.resume_trace(original, through=5, offset=5, keep_actions=captured)
                self.retain(trace)
                for step in trace['messages'][3:]:
                    message = step['message']
                    step['at_ms'] = 8 if message['kind'] == 'action' else 9 if message['kind'] == 'ack' else 7
                    if default and message['kind'] == 'ack':
                        step['at_ms'] = 10
                        message.update(status='defaulted', action_id='n', elapsed_ms=3, time_bank_ms=0)
                if valid:
                    self.check_ledger(trace)
                else:
                    with self.assertRaisesRegex(v.ArtifactError, 'new ACK has no captured input'):
                        self.check_ledger(trace)

    def test_retained_group_user_or_default_selection_survives_other_seat_chombo(self):
        for source, action_id, elapsed, bank in (('user', 'h', 1, 1), ('default', 'n', 3, 0)):
            with self.subTest(source=source):
                original = deepcopy(self.vectors['V267_session_three_member_group_is_atomic']['positive']['trace'])
                original.pop('request_lifecycles')
                original['allow_open_requests'] = True  # Valid prefix before the penalty result.
                original['messages'] = [s for s in original['messages'] if s['message'].get('seq', 0) <= 6]
                for step in original['messages']:
                    message = step['message']
                    if message['kind'] == 'welcome':
                        message['rules']['invalid_action_policy'] = 'chombo'
                    elif message.get('event', {}).get('type') == 'start_game':
                        message['event']['rules']['invalid_action_policy'] = 'chombo'
                    elif message['kind'] == 'ack':
                        message.update(status='stale', action_id=action_id, elapsed_ms=elapsed, time_bank_ms=bank)
                # The peer may be selected while other members still wait;
                # their longer common deadline has not expired at checkpoint.
                next(s['message'] for s in original['messages'] if s['message']['kind'] == 'request')['decision_group_deadline_ms'] = 5
                trace = self.resume_trace(original, through=5, offset=8)
                self.retain(trace, {'action_id': action_id, 'source': source, 'elapsed_ms': elapsed, 'time_bank_ms': bank})
                self.check_ledger(trace)
                next(s['message'] for s in trace['messages'] if s['message']['kind'] == 'ack')['elapsed_ms'] -= 1
                with self.assertRaises(v.ArtifactError):
                    self.check_ledger(trace)

    def cancellation_trace(self):
        receiver = deepcopy(self.vectors['V338_cancellation_requires_penalty_result']['positive']['trace'])
        trace = self.live_trace()
        trace['messages'] = trace['messages'][:3]
        trace['messages'][2]['message'] = receiver['welcome']
        for step in receiver['steps']:
            message = step['message']
            seq = message['seq']
            trace['messages'].append({'at_ms': seq + 3 if seq < 5 else 820 + seq - 5,
                                      'direction': 'out', 'client_id': 'peer', 'message': message,
                                      'transaction_id': 'tx_' + str(seq) if seq < 6 else 'cancel_tx',
                                      'operation_id': 'op_' + str(seq) if seq < 6 else 'cancel_op'})
        return trace

    def test_chombo_history_and_new_invalid_input_preserve_original_clock(self):
        trace = self.resume_trace(self.cancellation_trace(), through=7)
        self.check_ledger(trace)
        self.retain(trace, self.selected(source='cancelled'))
        with self.assertRaisesRegex(v.ArtifactError, 'invalid retained selection'):
            self.check_ledger(trace)

        original = self.cancellation_trace()
        invalid = deepcopy(self.live_trace()['messages'][7])
        invalid['message']['action_id'] = 'bad'
        original['messages'].insert(7, invalid)
        trace = self.resume_trace(original, through=4, offset=10000, compact=False, keep_actions=True)
        self.retain(trace)
        for step in trace['messages']:
            if step['message']['kind'] == 'ack':
                step['message'].update(elapsed_ms=10812, time_bank_ms=10188)
        self.check_ledger(trace)
        stale = next(s['message'] for s in trace['messages'] if s['message'].get('status') == 'stale')
        stale['elapsed_ms'] -= 1
        with self.assertRaises(v.ArtifactError):
            self.check_ledger(trace)

    def test_negotiation_hello_and_join_cannot_overwrite_prior_steps(self):
        self.check_ledger(self.live_trace())
        for source_index, insertion_index in ((0, 1), (0, 2), (1, 2), (0, 3), (1, 3)):
            with self.subTest(source=source_index, insertion=insertion_index):
                trace = self.live_trace()
                repeated = deepcopy(trace['messages'][source_index])
                repeated['at_ms'] = trace['messages'][insertion_index]['at_ms']
                trace['messages'].insert(insertion_index, repeated)
                with self.assertRaisesRegex(v.ArtifactError, 'unexpected hello|unexpected join'):
                    self.check_ledger(trace)


if __name__ == '__main__':
    unittest.main()
