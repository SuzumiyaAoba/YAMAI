"""Session isolation and atomic recovery checks beyond individual goldens."""
from copy import deepcopy
import json
import unittest

import validate_artifacts as v
from session_contract import Receiver, SessionError, classify_player_input, negotiate
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
                    receiver.request_clock_floor,
                    receiver.terminal_acks, receiver.expected_effects,
                    receiver.active_requests, receiver.known, receiver.event_seq_floor,
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


if __name__ == '__main__':
    unittest.main()
