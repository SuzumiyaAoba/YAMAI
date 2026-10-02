"""Cross-cutting properties for the complete decision and event contracts."""
import json
import unittest
from collections import Counter
from copy import deepcopy
from decimal import Decimal, localcontext

import validate_artifacts as v
from game_contract import EventState, GameError, canonical_action, check_hora_payments, furiten_step, json_equal, legal_actions, next_kyoku
from session_contract import Receiver, SessionError
from scoring_reference import ORPHANS, TILES, ScoringError, inventory, score_hand


class GameContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.vectors = v.strict_load(v.ROOT / f"test-vectors/protocol/{v.PROTOCOL}/vectors.json")
        cls.rules = v.strict_load(v.ROOT / f"test-vectors/riichi-4p/{v.PROFILE_REVISION}/scoring.json")["rules"]

    def test_hidden_accepted_riichi_remains_tenpai_at_exhaustive_draw(self):
        import random
        from scoring_reference import waits
        for declared, first in ((True, '9s'), (True, '5s'), (False, '9s')):
            dealer = ['1m','2m','3m','4m','5m','6m','7p','8p','9p','E','E','E','5s']
            if not declared:
                dealer.remove('E')
                dealer.append('2s')
            self.assertEqual(bool(waits({'concealed_tiles': dealer, 'melds': []}, self.rules)), declared)
            deck = self._physical_tiles()
            for tile in dealer + ['C', first]:
                deck.remove(tile)
            for seed in range(20261001, 20262001):
                remaining = deck.copy()
                random.Random(seed).shuffle(remaining)
                others = [{'concealed_tiles': remaining[69+i*13:82+i*13], 'melds': []}
                          for i in range(3)]
                if all(not waits(hand, self.rules) for hand in others):
                    break
            else:
                self.fail('could not construct physical noten controls')
            self.assertEqual(len(remaining[108:]), 13)
            receiver = self._observer_receiver()
            self._send_event(receiver, dict(type='start_game', players=receiver.welcome['players'],
                                           rules=self.rules, scores=[25000]*4))
            self._send_event(receiver, dict(type='start_kyoku', bakaze='E', kyoku=1, oya=0,
                                           honba=0, kyotaku=0, extension_round=0, scores=[25000]*4,
                                           dora_marker='C', hands=[{'count':13} for _ in range(4)]))
            for i, tile in enumerate([first] + remaining[:69]):
                actor = i % 4
                self._send_event(receiver, dict(type='tsumo', actor=actor, pai=None))
                if i == 0 and declared:
                    self._send_event(receiver, dict(type='reach', actor=0))
                self._send_event(receiver, dict(type='dahai', actor=actor, pai=tile, tsumogiri=True))
                if i == 0 and declared:
                    self._send_event(receiver, dict(type='reach_accepted', actor=0,
                                                   deltas=[-1000,0,0,0], scores=[24000,25000,25000,25000], kyotaku=1))
            for restored in (False, True):
                for ready in ((True, False) if declared else (False,)):
                    with self.subTest(declared=declared, first=first, restored=restored, ready=ready):
                        current = deepcopy(receiver)
                        if restored:
                            snapshot = self._public_snapshot(receiver.game)
                            current = self._observer_receiver(initial_snapshot=True)
                            current.welcome['scores'] = receiver.game.scores.copy()
                            self.assertEqual(current.receive(json.dumps(snapshot).encode()), 'applied')
                        deltas = [3000,-1000,-1000,-1000] if ready else [0]*4
                        event = dict(type='end_kyoku', result=dict(type='ryukyoku', reason='fanpai',
                                     tenpai=[ready,False,False,False]), deltas=deltas,
                                     scores=[score+delta for score,delta in zip(receiver.game.scores,deltas)],
                                     next=dict(type='renchan' if ready else 'rotate', bakaze='E',
                                               kyoku=1 if ready else 2, oya=0 if ready else 1,
                                               honba=1, kyotaku=int(declared), extension_round=0))
                        if declared and not ready:
                            self._assert_receiver_rejects_atomically(current, self._event_message(current,event),
                                                                   'accepted riichi.*tenpai')
                        else:
                            self._send_event(current,event)

    def test_complete_choices_ignore_hand_and_consumed_order(self):
        trace = self.vectors["V193_red_consumed_tile_and_compound_discard"]["positive"]["trace"]
        p = deepcopy(trace["input"])
        actual = {canonical_action(a) for a in legal_actions(p, self.rules)}
        p["hand"]["concealed_tiles"].reverse()
        self.assertEqual(actual, {canonical_action(a) for a in legal_actions(p, self.rules)})
        self.assertEqual(actual, {canonical_action(a) for a in trace["expected"]})

    def test_furiten_transitions_only_count_permitted_ankan_robbery(self):
        position = deepcopy(self.vectors['V204_no_yaku_pass_still_temporary_furiten']['positive']['trace']['input']['position'])
        ordinary = deepcopy(position['hand'])
        kokushi = {'concealed_tiles': [TILES[t] for t in sorted(ORPHANS) if TILES[t] != 'E'] + ['S'],
                   'melds': []}
        for hand, tile in ((ordinary, '2s'), (kokushi, 'E')):
            for cause_type in ('dahai', 'kakan_declared', 'ankan_declared'):
                for ankan_rule in ('never', 'kokushi_only'):
                    for riichi in (False, True):
                        with self.subTest(kokushi=hand == kokushi, cause=cause_type,
                                          ankan_rule=ankan_rule, riichi=riichi):
                            rules = deepcopy(self.rules)
                            rules['ankan_chankan'] = ankan_rule
                            p = deepcopy(position)
                            cause = {'type': cause_type, 'actor': 2}
                            if cause_type == 'ankan_declared':
                                cause['consumed'] = [tile] * 4
                            elif cause_type == 'kakan_declared':
                                cause.update(pai=tile, consumed=[tile] * 3)
                            else:
                                cause.update(pai=tile, tsumogiri=False)
                            p.update(hand=deepcopy(hand), reach_accepted=riichi, cause=cause)
                            eligible = cause_type != 'ankan_declared' or (ankan_rule == 'kokushi_only' and hand == kokushi)
                            expected = {'temporary_furiten': eligible and not riichi,
                                        'riichi_furiten': eligible and riichi}
                            self.assertEqual(furiten_step(p, {'type': 'reaction', 'pai': tile, 'selected': 'none'}, rules), expected)
                            self.assertEqual(furiten_step(p, {'type': 'reaction', 'pai': tile, 'selected': 'hora'}, rules),
                                             {'temporary_furiten': False, 'riichi_furiten': False})
                            # The standalone and wire ACK paths must agree.
                            state = EventState(rules, self_seat=p['seat'])
                            state.round = {'hands': [{'tiles': hand['concealed_tiles']} for _ in range(4)],
                                           'melds': [[] for _ in range(4)],
                                           'reach_status': [{'state': 'accepted' if riichi else 'none'} for _ in range(4)],
                                           'self_state': {'temporary_furiten': False, 'riichi_furiten': False}}
                            state.last_cause = p['cause']
                            state.acknowledge({'legal_actions': [{'action_id': 'pass', 'action': {'type': 'none'}}]},
                                              {'status': 'passed', 'action_id': 'pass'})
                            self.assertEqual(state.round['self_state'], expected)

    def test_immutable_json_equality_preserves_types_and_private_members(self):
        for left, right in ((True, 1), (False, 0), ('1', 1), (None, False),
                            ({'x_acme_rule': [True]}, {'x_acme_rule': [1]}),
                            ({'consumed': [1, 2]}, {'consumed': [2, 1]}),
                            ({'type': 'none', 'actor': 0}, {'type': 'none'})):
            with self.subTest(left=left, right=right):
                self.assertFalse(json_equal(left, right))
        for left, right in ((1, 1.0), (1, Decimal('1.00')), (0, Decimal('-0.0')),
                            ({'x_acme_rule': [True], 'amount': 1},
                             {'amount': Decimal('1.0'), 'x_acme_rule': [True]})):
            with self.subTest(left=left, right=right):
                self.assertTrue(json_equal(left, right))

    def test_start_game_rules_do_not_coerce_private_booleans_to_numbers(self):
        for old, new, valid in ((True, 1, False), (False, 0, False),
                                (1, Decimal('1.0'), True), (True, True, True)):
            rules = deepcopy(self.rules)
            rules['x_acme_rule'] = {'values': [old]}
            event_rules = deepcopy(rules)
            event_rules['x_acme_rule']['values'][0] = new
            event = {'type': 'start_game', 'players': [{'seat': i, 'name': str(i)} for i in range(4)],
                     'rules': event_rules, 'scores': [rules['starting_points']] * 4}
            state = EventState(rules)
            with self.subTest(old=old, new=new):
                if valid:
                    state.apply(event)
                else:
                    with self.assertRaisesRegex(GameError, 'negotiated rules'):
                        state.apply(event)

    def test_canonical_action_preserves_exact_decimal_values(self):
        for spellings in (("0.5", "0.50", "5e-1"), ("1", "1.00", "10e-1"),
                          ("0", "-0.000", "0e1000"), ("100", "1e2", "100.00")):
            keys = {canonical_action({'weight': Decimal(raw)}) for raw in spellings}
            self.assertEqual(len(keys), 1)
        self.assertEqual(canonical_action({'weight': 100}),
                         canonical_action({'weight': Decimal('1e2')}))
        self.assertNotEqual(canonical_action({'weight': Decimal('0.5')}),
                            canonical_action({'weight': '5e-1'}))
        self.assertNotEqual(canonical_action({'weight': 1}), canonical_action({'weight': True}))
        with localcontext() as context:
            context.prec = 3
            left = Decimal('0.1234567890123456789012345678901')
            right = Decimal('0.1234567890123456789012345678902')
            self.assertNotEqual(canonical_action({'weight': left}), canonical_action({'weight': right}))
            self.assertEqual(canonical_action({'weight': left}),
                             canonical_action({'weight': Decimal(str(left) + '00')}))
        for raw in ('1e-999999', '1' + '0' * 309 + '.5'):
            key = canonical_action({'weight': Decimal(raw)})
            self.assertEqual(json.loads(key, parse_float=Decimal)['weight'], Decimal(raw))

    def test_private_action_accepts_fractional_numeric_argument(self):
        trace = deepcopy(self.vectors['V131_private_action_preserves_owner_binding']['positive']['trace'])
        descriptor = trace['definitions'][0]['action_types']['x-acme-policy']
        descriptor['schema']['properties']['weight']['type'] = 'number'
        check = v._session_schema_validator(v.SchemaSet(), 'unused', trace['definitions'],
                                           trace['enabled_capabilities'])
        for raw in ('0.5', '0.50', '5e-1'):
            message = deepcopy(trace['message'])
            # Exercise the strict wire reader, which preserves fractions as Decimal.
            message['legal_actions'][-1]['action']['weight'] = 0.5
            wire = json.dumps(message).replace('"weight": 0.5', '"weight": ' + raw).encode()
            decoded = v.strict_load_bytes(wire)
            check('host-application', decoded)
            duplicate = deepcopy(decoded['legal_actions'][-1])
            duplicate['action_id'] = 'duplicate'
            duplicate['action']['weight'] = Decimal('0.500')
            decoded['legal_actions'].append(duplicate)
            with self.assertRaises(SessionError) as caught:
                check('host-application', decoded)
            self.assertEqual(caught.exception.code, 'invalid_message')

    def test_private_action_preserves_owned_namespaced_arguments(self):
        trace = deepcopy(self.vectors['V131_private_action_preserves_owner_binding']['positive']['trace'])
        body = trace['definitions'][0]['action_types']['x-acme-policy']['schema']
        body['required'].remove('weight')
        body['required'].append('x_acme_weight')
        body['properties']['x_acme_weight'] = body['properties'].pop('weight')
        candidate = trace['message']['legal_actions'][-1]
        candidate['action']['x_acme_weight'] = candidate['action'].pop('weight')
        second = deepcopy(candidate)
        second['action_id'] = 'custom2'
        second['action']['x_acme_weight'] = 2
        trace['message']['legal_actions'].append(second)
        check = v._session_schema_validator(v.SchemaSet(), 'unused', trace['definitions'],
                                           trace['enabled_capabilities'])
        check('host-application', trace['message'])
        second['action']['x_acme_weight'] = 1
        with self.assertRaises(SessionError) as caught:
            check('host-application', trace['message'])
        self.assertEqual(caught.exception.code, 'invalid_message')

    def test_private_payload_bypasses_every_core_projection(self):
        private = {'type': 'x-acme-policy', 'actor': 0,
                   'consumed': ['1m', '2m'], 'nested': {'type': 'none', 'actor': 1},
                   'x_acme_weight': 1}
        for change in ('ordered_consumed', 'nested_none_actor', 'owned_member'):
            modified = deepcopy(private)
            if change == 'ordered_consumed':
                modified['consumed'].reverse()
            elif change == 'nested_none_actor':
                modified['nested']['actor'] = 2
            else:
                modified['x_acme_weight'] = 2
            with self.subTest(change=change):
                self.assertNotEqual(canonical_action(private, private_payload=True),
                                    canonical_action(modified, private_payload=True))
                # The default retains the existing core-specific normalization.
                self.assertEqual(canonical_action(private), canonical_action(modified))
        core = {'type': 'pon', 'actor': 0, 'consumed': ['1m', '2m']}
        annotated = dict(core, x_acme_note={'type': 'none', 'actor': 3})
        self.assertEqual(canonical_action(core), canonical_action(annotated))
        self.assertEqual(canonical_action({'weight': Decimal('0.50')}, private_payload=True),
                         canonical_action({'weight': Decimal('5e-1')}, private_payload=True))

    def test_rotated_seats_preserve_relative_call_choices(self):
        p = deepcopy(self.vectors["V192_red_called_tile_and_compound_discard"]["positive"]["trace"]["input"])
        actions = legal_actions(p, self.rules)
        def rotate(action):
            action = deepcopy(action)
            for key in ("actor", "target"):
                if key in action:
                    action[key] = (action[key] + 2) % 4
            if "dahai" in action:
                action["dahai"] = rotate(action["dahai"])
            return action
        p["seat"], p["oya"] = (p["seat"] + 2) % 4, (p["oya"] + 2) % 4
        p["cause"] = rotate(p["cause"])
        p["scores"] = p["scores"][2:] + p["scores"][:2]
        p["kan_counts"] = p["kan_counts"][2:] + p["kan_counts"][:2]
        self.assertEqual({canonical_action(rotate(a)) for a in actions}, {canonical_action(a) for a in legal_actions(p, self.rules)})

    def test_riichi_ankan_must_consume_the_drawn_tile_kind(self):
        from game_contract import validate_snapshot_state

        state = self._dealt(seat=0)
        state.round['hands'][0] = {'tiles': ['E'] * 3 + ['1m'] * 4 + ['2m', '3p', '4p', '5p', '9s', '9s']}
        state.apply({'type': 'tsumo', 'actor': 0, 'pai': 'N'})
        state.apply({'type': 'reach', 'actor': 0})
        state.apply({'type': 'dahai', 'actor': 0, 'pai': 'N', 'tsumogiri': True})
        state.apply({'type': 'reach_accepted', 'actor': 0, 'deltas': [-1000, 0, 0, 0],
                     'scores': [24000, 25000, 25000, 25000], 'kyotaku': 1})
        for actor, tile in enumerate(('6s', '7s', '8s'), 1):
            state.apply({'type': 'tsumo', 'actor': actor, 'pai': None})
            state.apply({'type': 'dahai', 'actor': actor, 'pai': tile, 'tsumogiri': True})
        state.apply({'type': 'tsumo', 'actor': 0, 'pai': 'E'})
        # East forms the unchanged triplet; the four 1m belong to both a
        # triplet and a sequence, so declaring that quad changes the hand.
        for tile in ('1m', 'E'):
            candidate = deepcopy(state)
            event = {'type': 'ankan_declared', 'actor': 0, 'consumed': [tile] * 4}
            with self.subTest(tile=tile):
                snapshot = self._snapshot(state, dict(actor=0, phase='awaiting_responses',
                                                       last_event_seq=1, last_event=event), seat=0)
                snapshot['kyotaku'] = state.kyotaku
                snapshot['kyoku']['pending_kan'] = event
                if tile == '1m':
                    with self.assertRaises(GameError):
                        candidate.apply(event)
                    with self.assertRaisesRegex(GameError, 'riichi kan changes'):
                        validate_snapshot_state(snapshot, self.rules)
                    restored = EventState(self.rules)
                    before = deepcopy(vars(restored))
                    with self.assertRaisesRegex(GameError, 'riichi kan changes'):
                        restored.restore(snapshot)
                    self.assertEqual(vars(restored), before)
                else:
                    candidate.apply(event)
                    self.assertEqual(candidate.round['pending_kan'], event)
                    validate_snapshot_state(snapshot, self.rules)
                    EventState(self.rules).restore(snapshot)
                # A public view cannot infer the concealed shape; ownership
                # and physical inventory checks continue to apply instead.
                snapshot.update(mode='spectate', view='public', seat=None)
                snapshot['kyoku'].pop('self_state')
                snapshot['kyoku']['hands'][0] = {'count': 14}
                validate_snapshot_state(snapshot, self.rules)

    def test_pao_event_allows_namespaced_annotations(self):
        trace = self.vectors['V250_pao_between_third_pon_and_discard']['positive']['trace']
        rules = {**self.rules, **trace['rule_overrides']}
        ordinary, annotated = EventState(rules), EventState(rules)
        for event in trace['input']['events']:
            ordinary.apply(event)
            annotated.apply({**event, 'x_review_note': 'public annotation'} if event['type'] == 'pao' else event)
        self.assertEqual(vars(ordinary), vars(annotated))

    def test_snapshot_game_facts_allow_namespaced_annotations(self):
        for field, vector in (
            ('pao', 'V292_snapshot_pao_must_be_complete'),
            ('pending_kan', 'V293_snapshot_kan_declaration_cause'),
        ):
            with self.subTest(field=field):
                snapshot = deepcopy(self.vectors[vector]['positive']['state'])
                ordinary, annotated = EventState(self.rules), EventState(self.rules)
                ordinary.restore(snapshot)
                target = snapshot['kyoku'][field]
                if field == 'pao':
                    target = target[0]
                target['x_review_note'] = 'public annotation'
                annotated.restore(snapshot)
                self.assertEqual(canonical_action(ordinary.round), canonical_action(annotated.round))
                key = 'liable_seat' if field == 'pao' else 'actor'
                target[key] = (target[key] + 1) % 4
                with self.assertRaises(GameError):
                    annotated.restore(snapshot)

    def test_round_progression_allows_namespaced_annotations(self):
        for vector in ('V242_robbed_second_kan_clears_pending', 'V246_mixed_four_kans_abort_after_discard'):
            with self.subTest(vector=vector):
                trace = self.vectors[vector]['positive']['trace']
                rules = {**self.rules, **trace['rule_overrides']}
                ordinary, annotated = EventState(rules), EventState(rules)
                for event in trace['input']['events']:
                    ordinary.apply(event)
                    changed = deepcopy(event)
                    if changed['type'] == 'end_kyoku':
                        changed['next']['x_review_note'] = 'public annotation'
                    annotated.apply(changed)
                self.assertEqual(vars(ordinary), vars(annotated))

    def test_public_pao_payments_allow_namespaced_annotations(self):
        scoring = v.strict_load(v.ROOT / f'test-vectors/riichi-4p/{v.PROFILE_REVISION}/scoring.json')
        fixture = next(f for f in scoring['fixtures'] if f['id'] == 'settlement_pao_split')
        rules = {**self.rules, **fixture['rule_overrides']}
        win = deepcopy(fixture['expected']['wins'][0])
        win['pai'] = win['winning_tile']
        kyoku = deepcopy(fixture['state'])
        kyoku['melds'] = [[] for _ in range(4)]
        for meld in fixture['input']['hand']['melds']:
            kyoku['melds'][win['actor']].append({'type': meld['kind'], 'actor': win['actor'],
                                               'target': meld['source'], 'pai': meld['tiles'][0],
                                               'consumed': meld['tiles'][1:]})
        check_hora_payments([win], kyoku, rules)
        win['pao'][0]['x_review_note'] = 'public annotation'
        check_hora_payments([win], kyoku, rules)
        win['pao'][0]['liable_seat'] = (win['pao'][0]['liable_seat'] + 1) % 4
        with self.assertRaises(GameError):
            check_hora_payments([win], kyoku, rules)

    def test_tonpu_and_tonnan_have_different_scheduled_final_rounds(self):
        current = {"bakaze":"E","kyoku":4,"oya":3,"honba":0,"extension_round":0}
        result = {"type":"hora","wins":[{"actor":1}]}
        for length, extra in (("tonpu", 1), ("tonnan", 0)):
            rules = {**self.rules,"game_length":length}
            self.assertEqual(next_kyoku(current,result,[25000]*4,0,rules),
                             {"type":"rotate","bakaze":"S","kyoku":1,"oya":0,"honba":0,"kyotaku":0,"extension_round":extra})

    def test_physical_inventory_is_conserved_across_every_event(self):
        transitions = 0
        for case in self.vectors.values():
            trace = case.get("positive", {}).get("trace", {})
            if trace.get("operation") != "event_state" or "snapshot" in trace["input"]:
                continue
            rules = {**self.rules, **trace.get("rule_overrides", {})}
            state = EventState(rules)
            for event in trace["input"]["events"]:
                state.apply(event)
                r = state.round
                if r is None:
                    continue
                concealed = sum(len(h["tiles"]) if "tiles" in h else h["count"] for h in r["hands"])
                melds = sum(len(m["consumed"]) + int(m["type"] != "ankan") for row in r["melds"] for m in row)
                uncalled = sum(t["called_by"] is None for river in r["rivers"] for t in river)
                # A committed kan temporarily has 15 dead-wall tiles until
                # its rinshan draw; indicators remain inside that dead wall.
                dead_wall = 14 + int(r["rinshan"] and r["turn"]["phase"] == "awaiting_draw")
                self.assertEqual(concealed + melds + uncalled + r["wall_remaining"] + dead_wall, 136, event)
                transitions += 1
        self.assertGreater(transitions, 150)

    def test_ack_replay_does_not_charge_bank_twice(self):
        trace = deepcopy(self.vectors["V104_wire_complete_game"]["positive"]["trace"])
        welcome = trace["welcome"]
        receiver = Receiver(welcome, v.strict_load_bytes, v._session_schema_validator(v.SchemaSet(), welcome["profile_hash"]))
        messages = [s["message"] for s in trace["steps"] if "message" in s]
        request = next(m for m in messages if m["kind"] == "request")
        ack = next(m for m in messages if m["kind"] == "ack")
        ack["elapsed_ms"] = welcome["rules"]["time_control"]["grace_ms"] + request["timeout_ms"] + 500
        ack["time_bank_ms"] = request["time_bank_ms"] - 500
        for message in messages:
            raw = json.dumps(message, separators=(",", ":")).encode()
            receiver.receive(raw)
            if message["kind"] == "ack":
                self.assertEqual(receiver.time_bank_ms, ack["time_bank_ms"])
                self.assertEqual(receiver.receive(raw), "duplicate")
                self.assertEqual(receiver.time_bank_ms, ack["time_bank_ms"])
                break

    def test_adopted_actions_allow_required_dora_reach_deposit_and_pao(self):
        cases = [
            ('V241_consecutive_kan_reveals_previous_marker', 0, None),
            ('V247_called_reach_still_pays_deposit', 1,
             ['N','N','9m','4m','5m','6m','4p','5p','6p','4s','5s','6s','P']),
            ('V250_pao_between_third_pon_and_discard', 1,
             ['P','P','F','F','C','C','9m','8m','7m','4m','5m','6m','N']),
        ]
        schemas = v.SchemaSet()
        for key, seat, hand in cases:
            with self.subTest(case=key):
                trace = deepcopy(self.vectors[key]['positive']['trace'])
                events = trace['input']['events']
                welcome = deepcopy(self.vectors['V104_wire_complete_game']['positive']['trace']['welcome'])
                welcome['seat'] = seat
                welcome['rules'] = {**welcome['rules'], **trace['rule_overrides']}
                events[0]['rules'] = welcome['rules']
                if hand is not None:
                    events[1]['hands'] = [{'count':13} for _ in range(4)]
                    events[1]['hands'][seat] = {'tiles':hand}
                receiver = Receiver(welcome, v.strict_load_bytes, v._session_schema_validator(schemas, welcome['profile_hash']))
                identity = {k:welcome[k] for k in ('yamai','session_id','game_id')}
                def send(kind, **fields):
                    message = dict(identity, kind=kind, seq=receiver.applied+1, **fields)
                    receiver.receive(json.dumps(message, separators=(',', ':')).encode())
                for index, event in enumerate(events):
                    if receiver.awaiting_request:
                        r = receiver.game.round
                        cause = receiver.game.last_cause
                        reach = r['reach_status'][seat]
                        position = dict(seat=seat, hand=receiver.game._scoring_hand(seat), cause=cause,
                                        scores=receiver.game.scores, bakaze=r['bakaze'], oya=r['oya'], kyotaku=r['kyotaku'],
                                        wall_remaining=r['wall_remaining'], kan_counts=r['kan_counts'],
                                        reach_accepted=reach['state']=='accepted', double_riichi=reach['double'],
                                        ippatsu=reach['ippatsu'], first_turn=r['first_turn_eligible'][seat],
                                        rinshan=r['rinshan'], last_tile=r['haitei'],
                                        temporary_furiten=r['self_state']['temporary_furiten'],
                                        riichi_furiten=r['self_state']['riichi_furiten'],
                                        river=[tile['pai'] for tile in r['rivers'][seat]],
                                        dora_markers=r['dora_markers'], ura_dora_markers=[])
                        actions = legal_actions(position, welcome['rules'])
                        # Derived events precede the chosen core event. Find
                        # the intended choice in the independent event fixture.
                        core = next(e for e in events[index:] if e['type'] not in {'dora','reach_accepted','pao'})
                        wanted = deepcopy(core)
                        wanted['type'] = wanted['type'].removesuffix('_declared')
                        if wanted['type'] in {'chi','pon','reach'}:
                            wanted['dahai'] = next(e for e in events[index:] if e['type']=='dahai' and e['actor']==seat)
                        chosen = next(i for i,a in enumerate(actions) if canonical_action(a)==canonical_action(wanted))
                        default = next(i for i,a in enumerate(actions) if a['type']=='none' or
                                       (a['type']=='dahai' and a['tsumogiri']))
                        rid = 'r'+str(receiver.applied+1)
                        request = dict(request_id=rid, seat=seat, caused_by_seq=receiver.applied,
                                       timeout_ms=3000, time_bank_ms=receiver.time_bank_ms,
                                       legal_actions=[{'action_id':'a'+str(i),'action':a} for i,a in enumerate(actions)],
                                       default_action_id='a'+str(default))
                        if cause['type']!='tsumo':
                            members = [{'seat':s,'request_id':rid if s==seat else rid+'s'+str(s)}
                                       for s in range(4) if s!=cause['actor']]
                            request.update(decision_group_id='g'+rid, decision_group_members=members,
                                           decision_group_deadline_ms=21000, decision_group_close='all_selected_or_deadline')
                        send('request', **request)
                        send('ack', request_id=rid, action_id='a'+str(chosen), status='accepted',
                             elapsed_ms=1, time_bank_ms=receiver.time_bank_ms)
                    if event['type']=='tsumo' and event['actor']!=seat:
                        event['pai'] = None
                    send('event', event=event)
                self.assertEqual(receiver.expected_effects, [])
                if key.startswith('V241'):
                    self.assertEqual(receiver.game.round['dora_markers'], ['9p','8p'])
                elif key.startswith('V247'):
                    self.assertEqual(receiver.game.scores, [24000,25000,25000,25000])
                else:
                    self.assertEqual(receiver.game.round['pao'], [{'actor':1,'yaku_id':'daisangen','liable_seat':2}])

    def test_invalid_event_restores_game_state_and_wire_prefix(self):
        trace = deepcopy(self.vectors["V104_wire_complete_game"]["positive"]["trace"])
        welcome = trace["welcome"]
        receiver = Receiver(welcome, v.strict_load_bytes, v._session_schema_validator(v.SchemaSet(), welcome["profile_hash"]))
        for step in trace["steps"]:
            message = deepcopy(step["message"])
            if message["kind"] == "event" and message["event"]["type"] == "dahai":
                before = deepcopy(vars(receiver.game))
                seq = receiver.applied
                message["event"].update(pai="F", tsumogiri=False)
                with self.assertRaises(SessionError):
                    receiver.receive(json.dumps(message).encode())
                self.assertEqual(vars(receiver.game), before)
                self.assertEqual(receiver.applied, seq)
                self.assertTrue(receiver.closed)
                return
            receiver.receive(json.dumps(message).encode())
        self.fail("fixture did not reach a discard")

    def test_time_bank_reset_scope_at_next_round(self):
        for scope, expected in (("game", 14500), ("kyoku", 15000)):
            with self.subTest(scope=scope):
                trace = deepcopy(self.vectors["V104_wire_complete_game"]["positive"]["trace"])
                welcome = trace["welcome"]
                welcome["rules"]["bankruptcy"] = "continue"
                welcome["rules"]["time_control"]["bank_scope"] = scope
                receiver = Receiver(welcome, v.strict_load_bytes, v._session_schema_validator(v.SchemaSet(), welcome["profile_hash"]))
                messages = [s["message"] for s in trace["steps"] if s.get("message",{}).get("event",{}).get("type") != "end_game"]
                request = next(m for m in messages if m["kind"] == "request")
                for message in messages:
                    event = message.get("event", {})
                    if event.get("type") == "start_game":
                        event["rules"] = deepcopy(welcome["rules"])
                    elif event.get("type") == "end_kyoku":
                        event["next"] = {"type":"rotate","bakaze":"E","kyoku":2,"oya":1,"honba":0,"kyotaku":0,"extension_round":0}
                    elif message["kind"] == "ack":
                        message["elapsed_ms"] = welcome["rules"]["time_control"]["grace_ms"] + request["timeout_ms"] + 500
                        message["time_bank_ms"] = 14500
                    receiver.receive(json.dumps(message).encode())
                begin = deepcopy(messages[1])
                begin["seq"] = receiver.applied + 1
                begin["event"].update(kyoku=2,oya=1,scores=receiver.game.scores.copy())
                receiver.receive(json.dumps(begin).encode())
                self.assertEqual(receiver.time_bank_ms, expected)
                self.assertEqual(receiver.game.round["self_state"]["time_bank_ms"], expected)

    def test_required_request_cannot_be_omitted(self):
        trace = deepcopy(self.vectors["V104_wire_complete_game"]["positive"]["trace"])
        welcome = trace["welcome"]
        receiver = Receiver(welcome, v.strict_load_bytes, v._session_schema_validator(v.SchemaSet(), welcome["profile_hash"]))
        messages = [s["message"] for s in trace["steps"]]
        for message in messages[:3]:
            receiver.receive(json.dumps(message).encode())
        discard = deepcopy(next(m for m in messages if m.get("event",{}).get("type") == "dahai"))
        discard["seq"] = receiver.applied + 1
        with self.assertRaisesRegex(SessionError, "required decision"):
            receiver.receive(json.dumps(discard).encode())

    def _dealt(self, seat=1):
        state = EventState(self.rules)
        state.self_seat = seat
        state.apply({"type":"start_game","scores":[25000]*4,"rules":self.rules})
        state.apply({"type":"start_kyoku","bakaze":"E","kyoku":1,"oya":0,"honba":0,"kyotaku":0,
                     "extension_round":0,"scores":[25000]*4,"dora_marker":"1p","hands":[{"count":13} for _ in range(4)]})
        return state

    def _snapshot(self, state, turn, seat=1):
        kyoku = deepcopy(state.round)
        kyoku["self_state"] = {"temporary_furiten":False,"riichi_furiten":False,"kuikae_forbidden":[],"time_bank_ms":15000}
        kyoku["turn"] = turn
        return {"mode":"play","seat":seat,"view":"seat","players":[{"seat":i,"name":n} for i,n in enumerate("ABCD")],
                "scores":state.scores.copy(),"game_phase":"in_kyoku","kyotaku":0,"next_kyoku":None,
                "final_rankings":None,"time_bank_ms":15000,"pending_requests":[],"kyoku":kyoku}

    def _observer_receiver(self, *, rules=None, mode='spectate', view='public', initial_snapshot=False):
        welcome = deepcopy(self.vectors['V79_spectate_requires_snapshot']['positive']['trace']['welcome'])
        welcome.update(mode=mode, view=view, rules=deepcopy(self.rules if rules is None else rules))
        return Receiver(welcome, v.strict_load_bytes,
                        v._session_schema_validator(v.SchemaSet(), welcome['profile_hash']),
                        initial_snapshot=initial_snapshot)

    @staticmethod
    def _event_message(receiver, event):
        message = {key: receiver.welcome[key] for key in ('yamai', 'session_id', 'game_id')}
        message.update(kind='event', seq=receiver.applied + 1, event=event)
        if receiver.welcome['mode'] == 'replay':
            message['original_seq'] = receiver.original_seq + 1
        return message

    def _send_event(self, receiver, event):
        self.assertEqual(receiver.receive(json.dumps(self._event_message(receiver, event)).encode()), 'applied')

    def _assert_receiver_rejects_atomically(self, receiver, message, pattern):
        before_game = deepcopy(vars(receiver.game))
        before = deepcopy({key: value for key, value in vars(receiver).items()
                           if key not in {'game', 'closed', 'decode', 'validate'}})
        with self.assertRaisesRegex(SessionError, pattern) as caught:
            receiver.receive(json.dumps(message).encode())
        self.assertEqual(caught.exception.code, 'invalid_message')
        self.assertEqual(vars(receiver.game), before_game)
        self.assertEqual({key: value for key, value in vars(receiver).items()
                          if key not in {'game', 'closed', 'decode', 'validate'}}, before)
        self.assertTrue(receiver.closed)

    def _public_snapshot(self, state):
        snapshot = deepcopy(self.vectors['V111_initial_observer_has_no_prior_seq']['positive'])
        snapshot['state'].update(scores=state.scores.copy(), kyotaku=state.kyotaku,
                                 kyoku=deepcopy(state.round))
        kyoku = snapshot['state']['kyoku']
        kyoku['hands'] = [{'count': len(hand['tiles']) if 'tiles' in hand else hand['count']}
                          for hand in kyoku['hands']]
        cause = deepcopy(state.last_cause)
        if cause['type'] == 'tsumo':
            cause['pai'] = None
        kyoku['turn'].update(last_event_seq=None, last_event=cause)
        return snapshot

    def _restore_public_snapshot(self, snapshot, rules=None):
        rules = self.rules if rules is None else rules
        v._check_snapshot(snapshot, rules=rules)
        EventState(rules).restore(snapshot['state'])
        receiver = self._observer_receiver(rules=rules, initial_snapshot=True)
        self.assertEqual(receiver.receive(json.dumps(snapshot).encode()), 'applied')
        return receiver

    def _physical_tiles(self):
        tiles = [tile for tile in TILES for _ in range(4)]
        for suit, count in self.rules['red_fives'].items():
            for _ in range(count):
                tiles.remove('5' + suit)
                tiles.append('5' + suit + 'r')
        return tiles

    def _final_live_draw_witness(self):
        # Allocate every physical tile: four initial hands, seventy live
        # draws and fourteen dead-wall tiles. Each earlier draw is discarded.
        full_hand = ['1m', '2m', '3m', '4m', '5m', '6m', '7p', '8p', '9p',
                     '2s', '3s', '4s', '5s', '5s']
        tiles = self._physical_tiles()
        for tile in ['C', *full_hand]:
            tiles.remove(tile)
        draws, rest = tiles[:69] + ['4s'], tiles[69:]
        before_win = full_hand.copy()
        before_win.remove('4s')
        hands = [rest[:13], before_win, rest[13:26], rest[26:39]]
        dead_wall = ['C', *rest[39:]]
        self.assertEqual(len(dead_wall), 14)
        self.assertEqual(Counter([tile for hand in hands for tile in hand] + draws + dead_wall),
                         Counter(self._physical_tiles()))
        state = EventState(self.rules)
        state.apply({'type': 'start_game', 'scores': [25000] * 4, 'rules': self.rules})
        state.apply({'type': 'start_kyoku', 'bakaze': 'E', 'kyoku': 1, 'oya': 0,
                     'honba': 0, 'kyotaku': 0, 'extension_round': 0, 'scores': [25000] * 4,
                     'dora_marker': 'C', 'hands': [{'tiles': hand} for hand in hands]})
        for turn, tile in enumerate(draws):
            state.apply({'type': 'tsumo', 'actor': turn % 4, 'pai': tile})
            if turn < 69:
                state.apply({'type': 'dahai', 'actor': turn % 4, 'pai': tile, 'tsumogiri': True})
        self.assertCountEqual(state.round['hands'][1]['tiles'], full_hand)
        data = {'actor': 1, 'target': 1, 'win_method': 'tsumo', 'winning_tile': '4s',
                'hand': {'concealed_tiles': before_win, 'melds': []},
                'dora_markers': ['C'], 'ura_dora_markers': []}
        context = {'bakaze': 'E', 'oya': 0, 'kyotaku': 0, 'wall_remaining': 0,
                   'kan_counts': [0] * 4, 'reach_accepted': False, 'double_riichi': False,
                   'ippatsu': False, 'first_turn': False, 'rinshan': False,
                   'last_tile': True, 'pending_kan': None, 'furiten': False, 'events': []}
        score = score_hand(data, context, self.rules)
        self.assertEqual((score['fu'], score['han'], score['hand_points']), (20, 3, 2700))
        win = {key: score[key] for key in ('fu', 'han', 'yakus', 'bonuses', 'hand_points')}
        win.update(actor=1, target=1, pai='4s', pao=[], ura_dora_markers=[],
                   deltas=[-1300, 2700, -700, -700])
        return state, win

    def test_final_live_draw_snapshot_requires_haitei_in_every_validation_layer(self):
        state, _ = self._final_live_draw_witness()
        for phase in ('awaiting_action', 'resolving'):
            with self.subTest(phase=phase):
                snapshot = self._public_snapshot(state)
                snapshot['state']['kyoku']['turn']['phase'] = phase
                self._restore_public_snapshot(snapshot)
                snapshot['state']['kyoku']['haitei'] = False
                before_snapshot = deepcopy(snapshot)
                # Standalone validation, negotiated validation and restoration
                # must agree before Receiver even considers the new wire prefix.
                for rules in (None, self.rules):
                    with self.assertRaisesRegex(Exception, 'final live-wall draw'):
                        v._check_snapshot(snapshot, rules=rules)
                restored = self._dealt()
                before = deepcopy(vars(restored))
                with self.assertRaisesRegex(GameError, 'final live-wall draw'):
                    restored.restore(snapshot['state'])
                self.assertEqual(vars(restored), before)
                receiver = self._observer_receiver(initial_snapshot=True)
                self._assert_receiver_rejects_atomically(receiver, snapshot, 'final live-wall draw')
                self.assertEqual(snapshot, before_snapshot)

    def test_final_live_draw_snapshot_preserves_2700_point_win(self):
        state, win = self._final_live_draw_witness()
        snapshot = self._public_snapshot(state)
        for include_haitei in (True, False):
            with self.subTest(include_haitei=include_haitei):
                receiver = self._restore_public_snapshot(snapshot)
                claim = deepcopy(win)
                if not include_haitei:
                    claim.update(han=2, hand_points=1500, deltas=[-700, 1500, -400, -400])
                    claim['yakus'] = [y for y in claim['yakus'] if y['id'] != 'haitei']
                event = {'type': 'end_kyoku', 'result': {'type': 'hora', 'wins': [claim]},
                         'deltas': claim['deltas'], 'scores': [25000 + d for d in claim['deltas']],
                         'next': {'type': 'rotate', 'bakaze': 'E', 'kyoku': 2, 'oya': 1,
                                  'honba': 0, 'kyotaku': 0, 'extension_round': 0}}
                if include_haitei:
                    self._send_event(receiver, event)
                    self.assertEqual(receiver.game.scores, [23700, 27700, 24300, 24300])
                else:
                    self._assert_receiver_rejects_atomically(
                        receiver, self._event_message(receiver, event), 'situational yaku')

    def test_final_live_discard_snapshot_preserves_last_tile_flag(self):
        state, _ = self._final_live_draw_witness()
        state.apply({'type': 'dahai', 'actor': 1, 'pai': '4s', 'tsumogiri': True})
        self.assertEqual(state.round['kan_counts'], [0] * 4)
        for phase in ('awaiting_responses', 'resolving'):
            snapshot = self._public_snapshot(state)
            snapshot['state']['kyoku']['turn']['phase'] = phase
            with self.subTest(phase=phase):
                self._restore_public_snapshot(snapshot)
                snapshot['state']['kyoku']['haitei'] = False
                for rules in (None, self.rules):
                    with self.assertRaisesRegex(Exception, 'final live-wall discard'):
                        v._check_snapshot(snapshot, rules=rules)
                with self.assertRaisesRegex(GameError, 'final live-wall discard'):
                    EventState(self.rules).restore(snapshot['state'])
                receiver = self._observer_receiver(initial_snapshot=True)
                self._assert_receiver_rejects_atomically(receiver, snapshot, 'final live-wall discard')

    def test_snapshot_deal_cannot_bypass_mandatory_game_end(self):
        cases = [('bankruptcy boundary', [0, 47000, 25000, 25000], False, 'end_game', True),
                 ('bankruptcy ending', [-100, 47100, 25000, 25000], False, 'end_game', False),
                 ('bankruptcy continues', [-100, 47100, 25000, 25000], False, 'continue', True),
                 ('below extension target', [29900, 17100, 25000, 25000], True, 'end_game', True),
                 ('at extension target', [30000, 17000, 25000, 25000], True, 'end_game', False)]
        for label, scores, extension, bankruptcy, valid in cases:
            with self.subTest(case=label):
                trace = deepcopy(self.vectors['V271_snapshot_between_round_continuation']['positive']['trace'])
                welcome = trace['welcome']
                welcome.update(scores=scores)
                welcome['rules']['bankruptcy'] = bankruptcy
                snapshot = trace['steps'][0]['message']
                snapshot['state']['scores'] = scores
                for step in trace['steps'][1:]:
                    event = step['message']['event']
                    if 'scores' in event:
                        event['scores'] = scores
                if extension:
                    coords = dict(bakaze='W', kyoku=1, oya=0, extension_round=1)
                    snapshot['state']['next_kyoku'].update(coords)
                    trace['steps'][1]['message']['event'].update(coords)
                receiver = Receiver(welcome, v.strict_load_bytes,
                                    v._session_schema_validator(v.SchemaSet(), welcome['profile_hash']))
                if not valid:
                    pattern = 'mandatory bankruptcy' if not extension else 'target was reached'
                    self._assert_receiver_rejects_atomically(receiver, snapshot, pattern)
                else:
                    for step in trace['steps']:
                        self.assertEqual(receiver.receive(json.dumps(step['message']).encode()), 'applied')
                    self.assertEqual(receiver.game.game_phase, 'in_kyoku')

    def test_snapshot_round_entry_allows_initial_deal_and_riichi_deposits(self):
        # A configured positive bankruptcy threshold can be crossed by an
        # in-round riichi payment. It applies only at the next settlement.
        trace = deepcopy(self.vectors['V271_snapshot_between_round_continuation']['positive']['trace'])
        snapshot = trace['steps'][0]['message']
        snapshot['state'].update(scores=[25000] * 4, kyotaku=0)
        snapshot['state']['next_kyoku'].update(honba=0, kyotaku=0)
        rules = deepcopy(self.rules)
        rules['bankruptcy_threshold'] = 30000
        EventState(rules).restore(snapshot['state'])
        # The same initial-deal exception remains valid within the first
        # round, and it is not available for a later coordinate.
        state, _ = self._final_live_draw_witness()
        public = self._public_snapshot(state)['state']
        EventState(rules).restore(public)
        later = deepcopy(public)
        later['kyoku'].update(kyoku=2, oya=1)
        with self.assertRaisesRegex(GameError, 'mandatory bankruptcy'):
            EventState(rules).restore(later)

        state = EventState(self.rules)
        starts = self.vectors['V104_wire_complete_game']['positive']['trace']['steps'][:2]
        state.apply(deepcopy(starts[0]['message']['event']))
        dealt = deepcopy(starts[1]['message']['event'])
        dealt['hands'] = [{'count': 13} for _ in range(4)]
        state.apply(dealt)
        for event in ({'type': 'tsumo', 'actor': 0, 'pai': None},
                      {'type': 'reach', 'actor': 0},
                      {'type': 'dahai', 'actor': 0, 'pai': '9s', 'tsumogiri': True},
                      {'type': 'reach_accepted', 'actor': 0, 'deltas': [-1000, 0, 0, 0],
                       'scores': [24000, 25000, 25000, 25000], 'kyotaku': 1},
                      {'type': 'tsumo', 'actor': 1, 'pai': None}):
            state.apply(event)
        rules['bankruptcy_threshold'] = 25000
        EventState(rules).restore(self._public_snapshot(state)['state'])

    def _furiten_reaction(self, *, first_tile='5s', riichi=False, discard='9s',
                          winning='5s', hand=None):
        welcome = deepcopy(self.vectors['V104_wire_complete_game']['positive']['trace']['welcome'])
        receiver = Receiver(welcome, v.strict_load_bytes,
                            v._session_schema_validator(v.SchemaSet(), welcome['profile_hash']))
        identity = {key: welcome[key] for key in ('yamai', 'session_id', 'game_id')}
        events = []
        def send(kind, **fields):
            message = dict(identity, kind=kind, seq=receiver.applied + 1, **fields)
            self.assertEqual(receiver.receive(json.dumps(message).encode()), 'applied')
            if kind == 'event':
                events.append(deepcopy(fields['event']))
        def request(chosen, *, inject_hora=False):
            r, cause = receiver.game.round, receiver.game.last_cause
            reach = r['reach_status'][0]
            position = dict(seat=0, hand=receiver.game._scoring_hand(0), cause=cause,
                            scores=receiver.game.scores, bakaze=r['bakaze'], oya=r['oya'],
                            kyotaku=r['kyotaku'], wall_remaining=r['wall_remaining'], kan_counts=r['kan_counts'],
                            reach_accepted=reach['state'] == 'accepted', double_riichi=reach['double'],
                            ippatsu=reach['ippatsu'], first_turn=r['first_turn_eligible'][0],
                            rinshan=r['rinshan'], last_tile=r['haitei'],
                            temporary_furiten=r['self_state']['temporary_furiten'],
                            riichi_furiten=r['self_state']['riichi_furiten'],
                            river=[t['pai'] for t in r['rivers'][0]], dora_markers=r['dora_markers'],
                            ura_dora_markers=['F'] if reach['state'] == 'accepted' else [])
            actions = legal_actions(position, welcome['rules'])
            if inject_hora and not any(a['type'] == 'hora' for a in actions):
                actions.append({'type': 'hora', 'actor': 0})
            rid = 'r' + str(receiver.applied + 1)
            default = next(i for i, a in enumerate(actions) if a['type'] == 'none'
                           or a['type'] == 'dahai' and a['tsumogiri'])
            req = dict(request_id=rid, seat=0, caused_by_seq=receiver.applied, timeout_ms=3000,
                       time_bank_ms=receiver.time_bank_ms, default_action_id='a' + str(default),
                       legal_actions=[dict(action_id='a' + str(i), action=a) for i, a in enumerate(actions)])
            if cause['type'] != 'tsumo':
                req.update(decision_group_id='g' + rid,
                           decision_group_members=[dict(seat=s, request_id=rid if s == 0 else rid + str(s))
                                                   for s in range(4) if s != cause['actor']],
                           decision_group_deadline_ms=welcome['rules']['time_control']['grace_ms'] + 3000 + receiver.time_bank_ms,
                           decision_group_close='all_selected_or_deadline')
            send('request', **req)
            selected = next(i for i, a in enumerate(actions) if a['type'] == chosen
                            and (chosen not in {'dahai', 'reach'} or a.get('dahai', a)['tsumogiri']))
            ack = dict(identity, kind='ack', seq=receiver.applied + 1, request_id=rid,
                       action_id='a' + str(selected), status='passed' if chosen == 'none' else 'accepted',
                       elapsed_ms=1, time_bank_ms=receiver.time_bank_ms)
            if not inject_hora:
                self.assertEqual(receiver.receive(json.dumps(ack).encode()), 'applied')
            return ack
        send('event', event=dict(type='start_game', players=welcome['players'], rules=welcome['rules'], scores=[25000] * 4))
        hand = hand or ['1m', '2m', '3m', '4m', '5m', '6m', '7p', '8p', '9p', 'E', 'E', 'E', '5s']
        send('event', event=dict(type='start_kyoku', bakaze='E', kyoku=1, oya=0, honba=0, kyotaku=0,
                                extension_round=0, scores=[25000] * 4, dora_marker='C',
                                hands=[{'tiles': hand}, *({'count': 13} for _ in range(3))]))
        send('event', event=dict(type='tsumo', actor=0, pai=discard))
        request('reach' if riichi else 'dahai')
        if riichi:
            send('event', event=dict(type='reach', actor=0))
        send('event', event=dict(type='dahai', actor=0, pai=discard, tsumogiri=True))
        if riichi:
            send('event', event=dict(type='reach_accepted', actor=0, deltas=[-1000, 0, 0, 0],
                                    scores=[24000, 25000, 25000, 25000], kyotaku=1))
        send('event', event=dict(type='tsumo', actor=1, pai=None))
        send('event', event=dict(type='dahai', actor=1, pai=first_tile, tsumogiri=True))
        request('none')
        send('event', event=dict(type='tsumo', actor=2, pai=None))
        send('event', event=dict(type='dahai', actor=2, pai=winning, tsumogiri=True))
        ack = request('hora', inject_hora=True)
        return receiver, ack, events

    def test_known_furiten_rejects_ron_ack_without_partial_application(self):
        for riichi in (False, True):
            with self.subTest(riichi=riichi):
                receiver, ack, _ = self._furiten_reaction(riichi=riichi)
                flag = 'riichi_furiten' if riichi else 'temporary_furiten'
                self.assertTrue(receiver.game.round['self_state'][flag])
                self._assert_receiver_rejects_atomically(receiver, ack, 'known furiten')
        receiver, ack, _ = self._furiten_reaction(first_tile='6s')
        self.assertFalse(receiver.game.round['self_state']['temporary_furiten'])
        self.assertEqual(receiver.receive(json.dumps(ack).encode()), 'applied')

    def test_riichi_snapshot_distinguishes_persistent_and_temporary_furiten(self):
        source, _, _ = self._furiten_reaction(riichi=True)
        self.assertTrue(source.game.round['self_state']['riichi_furiten'])
        snapshot = deepcopy(self.vectors['V18_snapshot_state']['positive'])
        snapshot.update(seq=source.applied + 1, replaces_through_seq=source.applied)
        kyoku = deepcopy(source.game.round)
        kyoku['turn'].update(last_event_seq=source.last_event_seq,
                             last_event=deepcopy(source.game.last_cause))
        rid = next(iter(source.active_requests))
        request = {key: deepcopy(value) for key, value in source.requests[rid].items()
                   if key not in {'kind', 'yamai', 'session_id', 'game_id', 'seq'}}
        request['legal_actions'] = [a for a in request['legal_actions'] if a['action']['type'] == 'none']
        remaining = source.welcome['rules']['time_control']['grace_ms'] + request['timeout_ms'] + request['time_bank_ms'] - 1
        request.update(remaining_ms=remaining, decision_group_remaining_ms=remaining, selection=None)
        snapshot['state'].update(scores=source.game.scores.copy(), kyotaku=source.game.kyotaku,
                                 kyoku=kyoku, pending_requests=[request])
        welcome = deepcopy(source.welcome)
        welcome.update(resumed=True, replay_from_seq=1, replay_through_seq=source.applied,
                       scores=source.game.scores.copy())
        for temporary in (False, True):
            with self.subTest(temporary=temporary):
                message = deepcopy(snapshot)
                message['state']['kyoku']['self_state']['temporary_furiten'] = temporary
                receiver = Receiver(welcome, v.strict_load_bytes,
                                    v._session_schema_validator(v.SchemaSet(), welcome['profile_hash']))
                if temporary:
                    self._assert_receiver_rejects_atomically(receiver, message, 'temporary furiten survives')
                else:
                    self.assertEqual(receiver.receive(json.dumps(message).encode()), 'applied')
                    self.assertTrue(receiver.game.round['self_state']['riichi_furiten'])

    def test_observable_discard_furiten_rejects_ron_in_play_and_replay(self):
        for other_wait in (False, True):
            options = dict(first_tile='9s', discard='3s', winning='6s',
                           hand=['1m', '2m', '3m', '1p', '2p', '3p', '4s', '5s', '7p', '7p', 'E', 'E', 'E']) if other_wait else dict(first_tile='6s', discard='5s')
            receiver, ack, events = self._furiten_reaction(**options)
            self._assert_receiver_rejects_atomically(receiver, ack,
                                                   'visible hand is discard-furiten' if other_wait else 'previously discarded')
            for visible in (False, True):
                with self.subTest(other_wait=other_wait, visible=visible):
                    replay = self._observer_receiver(mode='replay', view={'seat': 0} if visible else 'public')
                    for event in deepcopy(events):
                        if not visible and event['type'] == 'start_kyoku':
                            event['hands'] = [{'count': 13} for _ in range(4)]
                        if not visible and event['type'] == 'tsumo':
                            event['pai'] = None
                        self._send_event(replay, event)
                    win = dict(actor=0, target=2, pai=options.get('winning', '5s'), fu=40, han=2,
                               yakus=[dict(id='round_wind', value=1, unit='han'), dict(id='seat_wind', value=1, unit='han')],
                               bonuses=[], hand_points=3900, deltas=[3900, 0, -3900, 0], ura_dora_markers=[], pao=[])
                    event = dict(type='end_kyoku', result=dict(type='hora', wins=[win]), deltas=win['deltas'],
                                 scores=[28900, 25000, 21100, 25000],
                                 next=dict(type='renchan', bakaze='E', kyoku=1, oya=0, honba=1, kyotaku=0, extension_round=0))
                    if other_wait and not visible:
                        self._send_event(replay, event)  # An unseen wait cannot be inferred.
                    else:
                        self._assert_receiver_rejects_atomically(replay, self._event_message(replay, event),
                                                               'visible hand is discard-furiten' if other_wait else 'previously discarded')

    def test_three_ron_draw_preserves_observable_furiten_rules(self):
        for discarded, valid in (('9s', True), ('5s', False)):
            with self.subTest(discarded=discarded):
                _, _, events = self._furiten_reaction(first_tile='6s', discard=discarded)
                rules = deepcopy(self.rules)
                rules['ron_policy'] = 'double_only'
                rules['abortive_draws'] = sorted(set(rules['abortive_draws']) | {'sanchaho'})
                receiver = self._observer_receiver(rules=rules)
                for event in deepcopy(events):
                    if event['type'] == 'start_game':
                        event['rules'] = rules
                    if event['type'] == 'start_kyoku':
                        event['hands'] = [{'count': 13} for _ in range(4)]
                    if event['type'] == 'tsumo':
                        event['pai'] = None
                    self._send_event(receiver, event)
                event = dict(type='end_kyoku', result=dict(type='ryukyoku', reason='sanchaho', tenpai=None),
                             deltas=[0] * 4, scores=[25000] * 4,
                             next=dict(type='renchan', bakaze='E', kyoku=1, oya=0, honba=1, kyotaku=0, extension_round=0))
                if valid:
                    self._send_event(receiver, event)
                else:
                    self._assert_receiver_rejects_atomically(receiver, self._event_message(receiver, event),
                                                           'previously discarded')

    def test_three_ron_on_ankan_requires_kokushi_compatible_melds(self):
        rules = deepcopy(self.rules)
        rules['ron_policy'] = 'double_only'
        rules['abortive_draws'] = sorted(set(rules['abortive_draws']) | {'sanchaho'})
        rules['ankan_chankan'] = 'kokushi_only'
        rules['kan_dora_timing'] = dict.fromkeys(('ankan', 'daiminkan', 'kakan'), 'before_rinshan')
        for kind in ('chi', 'pon', 'daiminkan', 'ankan', 'kakan'):
            for owner_declares in (False, True):
                with self.subTest(kind=kind, owner_declares=owner_declares):
                    receiver = self._observer_receiver(rules=rules)
                    self._send_event(receiver, dict(type='start_game', players=receiver.welcome['players'],
                                                   rules=rules, scores=[25000] * 4))
                    self._send_event(receiver, dict(type='start_kyoku', bakaze='E', kyoku=1, oya=0,
                                                   honba=0, kyotaku=0, extension_round=0, scores=[25000] * 4,
                                                   dora_marker='2p', hands=[{'count': 13} for _ in range(4)]))
                    def draw(actor):
                        self._send_event(receiver, dict(type='tsumo', actor=actor, pai=None))
                    def discard(actor, pai, *, tsumogiri=True):
                        self._send_event(receiver, dict(type='dahai', actor=actor, pai=pai, tsumogiri=tsumogiri))
                    def rinshan(actor):
                        self._send_event(receiver, dict(type='dora', dora_marker='3p'))
                        draw(actor)
                    draw(0)
                    if kind == 'ankan':
                        owner = 0
                        self._send_event(receiver, dict(type='ankan_declared', actor=owner, consumed=['6p'] * 4))
                        self._send_event(receiver, dict(type='ankan', actor=owner, consumed=['6p'] * 4))
                        rinshan(owner)
                    else:
                        owner = 1
                        tile = '3p' if kind == 'chi' else '6p'
                        discard(0, tile)
                        consumed = ['1p', '2p'] if kind == 'chi' else ['6p'] * (3 if kind == 'daiminkan' else 2)
                        self._send_event(receiver, dict(type='pon' if kind == 'kakan' else kind,
                                                       actor=owner, target=0, pai=tile, consumed=consumed))
                        if kind == 'daiminkan':
                            rinshan(owner)
                    discard(owner, '7p', tsumogiri=kind in {'ankan', 'daiminkan'})
                    if kind == 'kakan':
                        for actor, tile in ((2, '2s'), (3, '3s'), (0, '4s')):
                            draw(actor)
                            discard(actor, tile)
                        draw(owner)
                        self._send_event(receiver, dict(type='kakan_declared', actor=owner, pai='6p', consumed=['6p'] * 3))
                        self._send_event(receiver, dict(type='kakan', actor=owner, pai='6p', consumed=['6p'] * 3))
                        rinshan(owner)
                        discard(owner, '8p')
                    actor = (owner + 1) % 4
                    if owner_declares:
                        for tile in ('6s', '7s', '8s'):
                            draw(actor)
                            discard(actor, tile)
                            actor = (actor + 1) % 4
                    draw(actor)
                    self._send_event(receiver, dict(type='ankan_declared', actor=actor, consumed=['9m'] * 4))
                    result = dict(type='end_kyoku', result=dict(type='ryukyoku', reason='sanchaho', tenpai=None),
                                  deltas=[0] * 4, scores=[25000] * 4,
                                  next=dict(type='renchan', bakaze='E', kyoku=1, oya=0, honba=1, kyotaku=0, extension_round=0))
                    if owner_declares:
                        # The declarer may retain these simple-tile melds;
                        # only the three potential Kokushi winners must lack melds.
                        self._send_event(receiver, result)
                    else:
                        self._assert_receiver_rejects_atomically(receiver, self._event_message(receiver, result),
                                                               'sanchaho ankan winner has a committed meld')

    def test_three_ron_on_ankan_checks_visible_orphan_kinds(self):
        rules = deepcopy(self.rules)
        rules['ron_policy'] = 'double_only'
        rules['abortive_draws'] = sorted(set(rules['abortive_draws']) | {'sanchaho'})
        rules['ankan_chankan'] = 'kokushi_only'
        orphans = [TILES[tile] for tile in sorted(ORPHANS) if TILES[tile] != '9m']
        dealt = [['9m'] * 3 + ['3p'] * 3 + ['4p'] * 3 + ['6p'] * 3 + ['7p'],
                 *(orphans + [pair] for pair in ('1m', '1p', '1s'))]
        # All three hands can simultaneously rob the same physical 9m.
        inventory([*sum(dealt, []), '9m', '2p'], rules)
        for seat in (1, 2, 3):
            context = dict(bakaze='E', oya=0, kyotaku=0, wall_remaining=69, kan_counts=[0] * 4,
                           reach_accepted=False, double_riichi=False, ippatsu=False, first_turn=True,
                           rinshan=False, last_tile=False, pending_kan=dict(kind='ankan', actor=0, pai='9m'),
                           furiten=False, events=[])
            score = score_hand(dict(actor=seat, target=0, win_method='ron', winning_tile='9m',
                                    hand=dict(concealed_tiles=dealt[seat], melds=[]),
                                    dora_markers=['2p'], ura_dora_markers=[]), context, rules)
            self.assertEqual(score['yakus'], [dict(id='kokushi_musou', value=1, unit='yakuman')])
        for view in ('public', 'full', {'seat': 1}, {'seat': 2}, {'seat': 3}):
            for invalid_seat in (None, 1, 2, 3):
                with self.subTest(view=view, invalid_seat=invalid_seat):
                    receiver = self._observer_receiver(rules=rules, mode='replay', view=view)
                    hands = deepcopy(dealt)
                    if invalid_seat is not None:
                        hands[invalid_seat][hands[invalid_seat].index('9p')] = '2m'
                    visible = [view == 'full' or view == {'seat': seat} for seat in range(4)]
                    self._send_event(receiver, dict(type='start_game', players=receiver.welcome['players'],
                                                   rules=rules, scores=[25000] * 4))
                    self._send_event(receiver, dict(type='start_kyoku', bakaze='E', kyoku=1, oya=0,
                                                   honba=0, kyotaku=0, extension_round=0, scores=[25000] * 4,
                                                   dora_marker='2p', hands=[{'tiles': hand} if shown else {'count': 13}
                                                                          for hand, shown in zip(hands, visible)]))
                    self._send_event(receiver, dict(type='tsumo', actor=0, pai='9m' if visible[0] else None))
                    self._send_event(receiver, dict(type='ankan_declared', actor=0, consumed=['9m'] * 4))
                    result = dict(type='end_kyoku', result=dict(type='ryukyoku', reason='sanchaho', tenpai=None),
                                  deltas=[0] * 4, scores=[25000] * 4,
                                  next=dict(type='renchan', bakaze='E', kyoku=1, oya=0, honba=1, kyotaku=0, extension_round=0))
                    if invalid_seat is not None and visible[invalid_seat]:
                        self._assert_receiver_rejects_atomically(receiver, self._event_message(receiver, result),
                                                               'sanchaho ankan winner lacks a kokushi shape')
                    else:
                        self._send_event(receiver, result)  # Never infer hidden tile kinds.

    def test_final_rinshan_draw_and_discard_do_not_require_haitei(self):
        state = EventState(self.rules)
        state.apply({'type': 'start_game', 'scores': [25000] * 4, 'rules': self.rules})
        state.apply({'type': 'start_kyoku', 'bakaze': 'E', 'kyoku': 1, 'oya': 0,
                     'honba': 0, 'kyotaku': 0, 'extension_round': 0, 'scores': [25000] * 4,
                     'dora_marker': 'P', 'hands': [{'count': 13} for _ in range(4)]})
        tiles = self._physical_tiles()
        for tile in ['C'] * 4 + ['P', 'F', '9s']:
            tiles.remove(tile)
        for turn, tile in enumerate(tiles[:68]):
            state.apply({'type': 'tsumo', 'actor': turn % 4, 'pai': None})
            state.apply({'type': 'dahai', 'actor': turn % 4, 'pai': tile, 'tsumogiri': True})
        for event in ({'type': 'tsumo', 'actor': 0, 'pai': None},
                      {'type': 'ankan_declared', 'actor': 0, 'consumed': ['C'] * 4},
                      {'type': 'ankan', 'actor': 0, 'consumed': ['C'] * 4},
                      {'type': 'dora', 'dora_marker': 'F'}):
            state.apply(event)
        for event in ({'type': 'tsumo', 'actor': 0, 'pai': None},
                      {'type': 'dahai', 'actor': 0, 'pai': '9s', 'tsumogiri': True}):
            state.apply(event)
            self.assertEqual(state.round['wall_remaining'], 0)
            self.assertFalse(state.round['haitei'])
            for resolving in (False, True):
                with self.subTest(cause=event['type'], resolving=resolving):
                    snapshot = self._public_snapshot(state)
                    if resolving:
                        snapshot['state']['kyoku']['turn']['phase'] = 'resolving'
                    self._restore_public_snapshot(snapshot)

    def test_penalty_cancels_only_the_unresolved_reach_declaration(self):
        rules = deepcopy(self.rules)
        rules['invalid_action_policy'] = 'chombo'
        acceptance = {'type': 'reach_accepted', 'actor': 0, 'deltas': [-1000, 0, 0, 0],
                      'scores': [24000, 25000, 25000, 25000], 'kyotaku': 1}
        draw = {'type': 'tsumo', 'actor': 1, 'pai': None}
        discard = {'type': 'dahai', 'actor': 1, 'pai': 'E', 'tsumogiri': False}
        continuations = {
            'unaccepted': [],
            'same_reaction': [acceptance],
            'next_draw': [acceptance, draw],
            'next_discard': [acceptance, draw, {**discard, 'tsumogiri': True}],
            'chi_discard': [acceptance, {'type': 'chi', 'actor': 1, 'target': 0,
                                        'pai': '9s', 'consumed': ['7s', '8s']}, discard],
            'pon_discard': [acceptance, {'type': 'pon', 'actor': 1, 'target': 0,
                                        'pai': '9s', 'consumed': ['9s', '9s']}, discard],
            # A completed daiminkan has advanced out of the declaration
            # reaction even before drawing rinshan or publishing delayed dora.
            'daiminkan': [acceptance, {'type': 'daiminkan', 'actor': 1, 'target': 0,
                                      'pai': '9s', 'consumed': ['9s'] * 3}],
        }
        for mode in ('spectate', 'replay'):
            for name, continuation in continuations.items():
                with self.subTest(mode=mode, continuation=name):
                    receiver = self._observer_receiver(rules=rules, mode=mode)
                    starts = deepcopy(self.vectors['V104_wire_complete_game']['positive']['trace']['steps'][:2])
                    starts[0]['message']['event']['rules'] = rules
                    starts[1]['message']['event']['hands'] = [{'count': 13} for _ in range(4)]
                    for step in starts:
                        self._send_event(receiver, step['message']['event'])
                    for event in [{'type': 'tsumo', 'actor': 0, 'pai': None},
                                  {'type': 'reach', 'actor': 0},
                                  {'type': 'dahai', 'actor': 0, 'pai': '9s', 'tsumogiri': True},
                                  *continuation]:
                        self._send_event(receiver, event)
                    sticks = int(name != 'unaccepted')
                    deltas = [2800, -8000, 2600, 2600]
                    event = {'type': 'end_kyoku', 'result': {
                                'type': 'penalty', 'reason': 'illegal_action', 'offender': 1,
                                'penalty': {'payments': [
                                    {'from': 1, 'to': 0, 'points': 2800},
                                    {'from': 1, 'to': 2, 'points': 2600},
                                    {'from': 1, 'to': 3, 'points': 2600}]}},
                             'deltas': deltas,
                             'scores': [25000 + d - (1000 * sticks if seat == 0 else 0)
                                        for seat, d in enumerate(deltas)],
                             'next': {'type': 'renchan', 'bakaze': 'E', 'kyoku': 1, 'oya': 0,
                                      'honba': 0, 'kyotaku': sticks, 'extension_round': 0}}
                    if name == 'same_reaction':
                        self._assert_receiver_rejects_atomically(
                            receiver, self._event_message(receiver, event),
                            'penalty after the reach discard was accepted')
                    else:
                        self._send_event(receiver, event)
                        self.assertEqual(receiver.game.scores, event['scores'])
                        self.assertEqual(receiver.game.kyotaku, sticks)

    def test_visible_kokushi_requires_every_orphan_kind_without_filling_hidden_hands(self):
        before_win = ['1m', '9m', '1p', '9p', '1s', 'E', 'S', 'W', 'N', 'P', 'F', 'C', 'E']
        orphan_kinds = [TILES[tile] for tile in sorted(ORPHANS) if TILES[tile] != '9s']
        for tsumo in (False, True):
            for missing in (None, *orphan_kinds, 'hidden'):
                with self.subTest(tsumo=tsumo, missing=missing):
                    visible = missing != 'hidden'
                    hand = before_win.copy()
                    if missing in orphan_kinds:
                        replacement = 'S' if missing == 'E' else 'E'
                        hand = [replacement if tile == missing else tile for tile in hand]
                    # Both the genuine hand and every counterexample fit the
                    # physical inventory. The negative is shape, not a fifth tile.
                    inventory([*hand, '9s', '2p'], self.rules)
                    receiver = self._observer_receiver(mode='replay', view={'seat': 1} if visible else 'public')
                    trace = deepcopy(self.vectors['V104_wire_complete_game']['positive']['trace'])
                    starts = [step['message']['event'] for step in trace['steps'][:2]]
                    starts[1]['hands'] = [{'count': 13} for _ in range(4)]
                    if visible:
                        starts[1]['hands'][1] = {'tiles': hand.copy()}
                    for event in starts:
                        self._send_event(receiver, event)
                    if tsumo:
                        # Discard once before the winning draw so no first-draw
                        # yakuman masks the Kokushi claim being tested.
                        for actor, tile in ((0, '2m'), (1, '2p'), (2, '3m'), (3, '4m'), (0, '6m')):
                            self._send_event(receiver, {'type': 'tsumo', 'actor': actor,
                                                       'pai': tile if actor == 1 and visible else None})
                            self._send_event(receiver, {'type': 'dahai', 'actor': actor,
                                                       'pai': tile, 'tsumogiri': True})
                        self._send_event(receiver, {'type': 'tsumo', 'actor': 1,
                                                   'pai': '9s' if visible else None})
                    else:
                        self._send_event(receiver, {'type': 'tsumo', 'actor': 0, 'pai': None})
                        self._send_event(receiver, {'type': 'dahai', 'actor': 0,
                                                   'pai': '9s', 'tsumogiri': True})
                    data = {'actor': 1, 'target': 1 if tsumo else 0,
                            'win_method': 'tsumo' if tsumo else 'ron', 'winning_tile': '9s',
                            'hand': {'concealed_tiles': hand, 'melds': []},
                            'dora_markers': ['2p'], 'ura_dora_markers': []}
                    context = {'bakaze': 'E', 'oya': 0, 'kyotaku': 0,
                               'wall_remaining': 64 if tsumo else 69, 'kan_counts': [0] * 4,
                               'reach_accepted': False, 'double_riichi': False, 'ippatsu': False,
                               'first_turn': not tsumo, 'rinshan': False, 'last_tile': False,
                               'pending_kan': None, 'furiten': False, 'events': []}
                    invalid = missing in orphan_kinds
                    if invalid:
                        with self.assertRaises(ScoringError):
                            score_hand(data, context, self.rules)
                    else:
                        score = score_hand(data, context, self.rules)
                        self.assertEqual(score['hand_points'], 32000)
                        self.assertEqual(score['yakus'], [{'id': 'kokushi_musou', 'value': 1, 'unit': 'yakuman'}])
                    event = deepcopy(next(step['message']['event'] for step in trace['steps']
                                          if step['message'].get('event', {}).get('type') == 'end_kyoku'))
                    if tsumo:
                        win = event['result']['wins'][0]
                        win.update(target=1, deltas=[-16000, 32000, -8000, -8000])
                        event.update(deltas=win['deltas'], scores=[9000, 57000, 17000, 17000],
                                     next={'type': 'rotate', 'bakaze': 'E', 'kyoku': 2, 'oya': 1,
                                           'honba': 0, 'kyotaku': 0, 'extension_round': 0})
                    if invalid:
                        self._assert_receiver_rejects_atomically(
                            receiver, self._event_message(receiver, event),
                            'thirteen orphans lacks a required terminal or honor kind')
                    else:
                        self._send_event(receiver, event)
                        self.assertEqual(receiver.game.scores, event['scores'])

    def test_resolving_snapshot_continues_reaction_result(self):
        source = self._dealt()
        source.apply({"type":"tsumo","actor":0,"pai":None})
        dahai = {"type":"dahai","actor":0,"pai":"9s","tsumogiri":True}
        source.apply(dahai)
        state = self._dealt()
        state.restore(self._snapshot(source, {"actor":0,"phase":"resolving","last_event_seq":5,"last_event":dahai}))
        state.apply({"type":"pon","actor":1,"target":0,"pai":"9s","consumed":["9s","9s"]})
        self.assertEqual(state.round["turn"], {"actor":1,"phase":"awaiting_action"})
        state.apply({"type":"dahai","actor":1,"pai":"E","tsumogiri":False})
        self.assertEqual(state.round["turn"]["phase"], "awaiting_responses")

    def test_resolving_snapshot_continues_turn_result(self):
        source = self._dealt()
        tsumo = {"type":"tsumo","actor":0,"pai":None}
        source.apply(tsumo)
        state = self._dealt()
        state.restore(self._snapshot(source, {"actor":0,"phase":"resolving","last_event_seq":4,"last_event":tsumo}))
        state.apply({"type":"dahai","actor":0,"pai":"9s","tsumogiri":True})
        self.assertEqual(state.round["turn"]["phase"], "awaiting_responses")

    def test_mid_transaction_snapshot_is_rejected(self):
        source = self._dealt()
        source.apply({"type":"tsumo","actor":0,"pai":None})
        source.apply({"type":"dahai","actor":0,"pai":"9s","tsumogiri":True})
        interiors = [
            {"type":"pon","actor":1,"target":0,"pai":"9s","consumed":["9s","9s"]},
            {"type":"reach_accepted","actor":0,"deltas":[0,0,0,0],"scores":[25000]*4,"kyotaku":0},
            {"type":"dora","dora_marker":"2p"},
            {"type":"pao","actor":1,"yaku_id":"daisangen","liable_seat":0},
        ]
        for event in interiors:
            for phase in ("awaiting_action", "awaiting_responses", "resolving"):
                with self.subTest(last_event=event["type"], phase=phase):
                    snapshot = self._snapshot(source, {"actor":0,"phase":phase,"last_event_seq":6,"last_event":event})
                    if phase == "awaiting_action":
                        snapshot["pending_requests"] = [{"request_id":"r1","seat":0,"caused_by_seq":6,"timeout_ms":3000,
                                                        "time_bank_ms":15000,"legal_actions":[],"default_action_id":"a0",
                                                        "remaining_ms":3000,"selection":None}]
                        snapshot["seat"] = 0
                    with self.assertRaisesRegex(Exception, "last committed event"):
                        self._dealt().restore(snapshot)

    def test_public_complete_shape_rejects_missing_required_tile_classes(self):
        from game_contract import check_hora_visible_tiles

        def public_hand(groups, pai, role, concealed=None):
            melds = [{"type": "chi", "actor": 1, "target": 0,
                      "pai": group[0], "consumed": group[1:]} for group in groups]
            win = {"actor": 1, "target": 0, "pai": pai, "fu": 30,
                   "yakus": [{"id": role, "value": 1, "unit": "han"}]}
            hand = {"count": 13 - 3 * len(groups)} if concealed is None else {"tiles": concealed}
            kyoku = {"melds": [[], melds, [], []], "hands": [{}, hand, {}, {}],
                     "oya": 0, "bakaze": "E"}
            return win, kyoku

        groups = [["1m", "2m", "3m"], ["7m", "8m", "9m"],
                  ["1p", "2p", "3p"], ["7p", "8p", "9p"]]
        for role, suited_groups in (("chanta", groups), ("honitsu", groups[:2] * 2)):
            with self.subTest(role=role):
                win, kyoku = public_hand(suited_groups, "9m", role)
                with self.assertRaises(GameError):
                    check_hora_visible_tiles(win, kyoku)
                # An honor pair makes both claimed tile-class conditions valid.
                win["pai"] = "N"
                check_hora_visible_tiles(win, kyoku)
                # Three public melds do not disclose the remaining meld/pair.
                win, kyoku = public_hand(suited_groups[:3], "9m", role)
                check_hora_visible_tiles(win, kyoku)
                # A fully visible number-only concealed part proves absence.
                kyoku["hands"][1] = {"tiles": ["7m", "8m", "9m", "9m"]}
                with self.assertRaises(GameError):
                    check_hora_visible_tiles(win, kyoku)

        win, kyoku = public_hand([], "N", "honitsu", concealed=["E"] * 3 + ["S"] * 3 + ["W"] * 3 + ["P"] * 3 + ["N"])
        with self.assertRaises(GameError):
            check_hora_visible_tiles(win, kyoku)

    def test_public_sequence_patterns_match_four_fixed_melds(self):
        from game_contract import check_hora_visible_tiles

        patterns = {
            "sanshoku_doujun": [["1m", "2m", "3m"], ["1p", "2p", "3p"],
                                ["1s", "2s", "3s"], ["7m", "8m", "9m"]],
            "ikkitsuukan": [["1m", "2m", "3m"], ["4m", "5mr", "6m"],
                            ["7m", "8m", "9m"], ["1p", "2p", "3p"]],
        }
        for role, groups in patterns.items():
            with self.subTest(role=role):
                melds = [{"type": "chi", "actor": 1, "target": 0,
                          "pai": group[0], "consumed": group[1:]} for group in groups]
                win = {"actor": 1, "target": 0, "pai": "9s", "fu": 30,
                       "yakus": [{"id": role, "value": 1, "unit": "han"}]}
                kyoku = {"melds": [[], melds, [], []], "oya": 0, "bakaze": "E"}
                check_hora_visible_tiles(win, kyoku)
                melds[1] = {"type": "chi", "actor": 1, "target": 0,
                            "pai": "2p", "consumed": ["3p", "4p"]}
                with self.assertRaises(GameError):
                    check_hora_visible_tiles(win, kyoku)
                # One unresolved hidden meld may supply the missing sequence.
                melds.pop(1)
                check_hora_visible_tiles(win, kyoku)

    def test_public_three_color_triplets_match_four_fixed_melds(self):
        from game_contract import check_hora_visible_tiles

        melds = [{"type": "pon", "actor": 1, "target": 0,
                  "pai": tile, "consumed": [tile, tile]}
                 for tile in ("1m", "1p", "1s", "4m")]
        win = {"actor": 1, "target": 0, "pai": "9s", "fu": 40,
               "yakus": [{"id": "sanshoku_doukou", "value": 2, "unit": "han"}]}
        kyoku = {"melds": [[], melds, [], []], "oya": 0, "bakaze": "E"}
        check_hora_visible_tiles(win, kyoku)
        melds[1] = {"type": "daiminkan", "actor": 1, "target": 0,
                    "pai": "1p", "consumed": ["1p"] * 3}
        win["fu"] = 50
        check_hora_visible_tiles(win, kyoku)
        melds[1] = {"type": "pon", "actor": 1, "target": 0,
                    "pai": "2p", "consumed": ["2p", "2p"]}
        win["fu"] = 40
        with self.assertRaises(GameError):
            check_hora_visible_tiles(win, kyoku)
        melds.pop(1)
        check_hora_visible_tiles(win, kyoku)

    def test_end_kyoku_rejects_chanta_on_four_number_sequences(self):
        state = EventState(self.rules)
        state.apply({"type": "start_game", "scores": [25000] * 4, "rules": self.rules})
        state.apply({"type": "start_kyoku", "bakaze": "E", "kyoku": 1, "oya": 0,
                     "honba": 0, "kyotaku": 0, "extension_round": 0, "scores": [25000] * 4,
                     "dora_marker": "C", "hands": [{"count": 13} for _ in range(4)]})
        def draw_discard(actor, tile):
            state.apply({"type": "tsumo", "actor": actor, "pai": None})
            state.apply({"type": "dahai", "actor": actor, "pai": tile, "tsumogiri": True})
        groups = [["1m", "2m", "3m"], ["7m", "8m", "9m"],
                  ["1p", "2p", "3p"], ["7p", "8p", "9p"]]
        for group, discard in zip(groups, ["2s", "3s", "4s", "5s"]):
            draw_discard(0, group[0])
            state.apply({"type": "chi", "actor": 1, "target": 0,
                         "pai": group[0], "consumed": group[1:]})
            state.apply({"type": "dahai", "actor": 1, "pai": discard, "tsumogiri": False})
            draw_discard(2, "E")
            draw_discard(3, "S")
        draw_discard(0, "9s")
        win = {"actor": 1, "target": 0, "pai": "9s", "fu": 30, "han": 1,
               "yakus": [{"id": "chanta", "value": 1, "unit": "han"}],
               "bonuses": [], "pao": [], "ura_dora_markers": [],
               "hand_points": 1000, "deltas": [-1000, 1000, 0, 0]}
        event = {"type": "end_kyoku", "result": {"type": "hora", "wins": [win]},
                 "deltas": win["deltas"], "scores": [24000, 26000, 25000, 25000],
                 "next": {"type": "rotate", "bakaze": "E", "kyoku": 2, "oya": 1,
                          "honba": 0, "kyotaku": 0, "extension_round": 0}}
        for role in ("chanta", "sanshoku_doujun", "ikkitsuukan"):
            win["yakus"] = [{"id": role, "value": 1, "unit": "han"}]
            with self.subTest(role=role), self.assertRaises(GameError):
                state.apply(event)
        # The same actual hand is a two-han junchan, worth 2000 by child ron.
        win.update(han=2, yakus=[{"id": "junchan", "value": 2, "unit": "han"}],
                   hand_points=2000, deltas=[-2000, 2000, 0, 0])
        event.update(deltas=win["deltas"], scores=[23000, 27000, 25000, 25000])
        state.apply(event)
        self.assertEqual(state.scores, [23000, 27000, 25000, 25000])

    def test_kan_declaration_preserves_hidden_count_and_visible_ownership(self):
        from game_contract import check_kan_declaration_hand

        ankan = {"type": "ankan_declared", "actor": 1, "consumed": ["E"] * 4}
        kakan = {"type": "kakan_declared", "actor": 1, "pai": "E", "consumed": ["E"] * 3}
        for declaration, minimum in ((ankan, 4), (kakan, 1)):
            for count in (0, 1, 2, 3, 4, 5):
                with self.subTest(kind=declaration["type"], count=count):
                    if count < minimum:
                        with self.assertRaises(GameError):
                            check_kan_declaration_hand({"count": count}, declaration)
                    else:
                        check_kan_declaration_hand({"count": count}, declaration)
            check_kan_declaration_hand({"tiles": ["E"] * minimum}, declaration)
            with self.assertRaises(GameError):
                check_kan_declaration_hand({"tiles": ["E"] * (minimum - 1) + ["S"]}, declaration)

    def test_hidden_ankan_count_is_checked_in_events_and_snapshots(self):
        from game_contract import validate_snapshot_state

        def position(calls):
            state = EventState(self.rules)
            state.apply({"type": "start_game", "scores": [25000] * 4, "rules": self.rules})
            state.apply({"type": "start_kyoku", "bakaze": "E", "kyoku": 1, "oya": 0,
                         "honba": 0, "kyotaku": 0, "extension_round": 0, "scores": [25000] * 4,
                         "dora_marker": "C", "hands": [{"count": 13} for _ in range(4)]})
            def draw_discard(actor, tile):
                state.apply({"type": "tsumo", "actor": actor, "pai": None})
                state.apply({"type": "dahai", "actor": actor, "pai": tile, "tsumogiri": True})
            groups = [["1m", "2m", "3m"], ["7m", "8m", "9m"],
                      ["1p", "2p", "3p"], ["7p", "8p", "9p"]]
            for group, discard in zip(groups[:calls], ["2s", "3s", "4s", "5s"]):
                draw_discard(0, group[0])
                state.apply({"type": "chi", "actor": 1, "target": 0,
                             "pai": group[0], "consumed": group[1:]})
                state.apply({"type": "dahai", "actor": 1, "pai": discard, "tsumogiri": False})
                draw_discard(2, "P")
                draw_discard(3, "F")
            draw_discard(0, "9s")
            state.apply({"type": "tsumo", "actor": 1, "pai": None})
            return state

        declaration = {"type": "ankan_declared", "actor": 1, "consumed": ["E"] * 4}
        for calls, count in ((3, 5), (4, 2)):
            with self.subTest(calls=calls):
                state = position(calls)
                self.assertEqual(state.round["hands"][1], {"count": count})
                kyoku = deepcopy(state.round)
                kyoku["turn"].update(phase="awaiting_responses", last_event=deepcopy(declaration))
                kyoku["pending_kan"] = deepcopy(declaration)
                snapshot = {"game_phase": "in_kyoku", "kyoku": kyoku, "next_kyoku": None,
                            "kyotaku": 0, "scores": [25000] * 4}
                if calls == 3:
                    validate_snapshot_state(snapshot, self.rules)
                    state.apply(declaration)
                else:
                    with self.assertRaises(GameError):
                        validate_snapshot_state(snapshot, self.rules)
                    before = deepcopy(state.round)
                    with self.assertRaises(GameError):
                        state.apply(declaration)
                    self.assertEqual(state.round, before)
                    state.apply({"type": "dahai", "actor": 1, "pai": "9s", "tsumogiri": True})
                    self.assertEqual(state.round["hands"][1], {"count": 1})

    def test_mixed_fourth_kan_rejects_reach_without_changing_scores(self):
        from game_contract import validate_snapshot_state

        events = self.vectors["V246_mixed_four_kans_abort_after_discard"]["positive"]["trace"]["input"]["events"]
        for enabled in (True, False):
            with self.subTest(abort_enabled=enabled):
                rules = deepcopy(self.rules)
                if not enabled:
                    rules["abortive_draws"].remove("suukan_sanra")
                state = EventState(rules)
                for event in events[:-2]:
                    state.apply({**event, "rules": rules} if event["type"] == "start_game" else event)
                before = deepcopy(vars(state))
                if enabled:
                    with self.assertRaises(GameError):
                        state.apply({"type": "reach", "actor": 2})
                    self.assertEqual(vars(state), before)
                    state.apply(events[-2])
                    # Simulate a standalone checkpoint claiming that forbidden
                    # declaration; the discard remains otherwise unchanged.
                    state.round["reach_status"][2]["state"] = "declared"
                    state.round["rivers"][2][-1]["reach"] = True
                else:
                    state.apply({"type": "reach", "actor": 2})
                    state.apply(events[-2])
                kyoku = deepcopy(state.round)
                kyoku["turn"]["last_event"] = deepcopy(state.last_cause)
                snapshot = {"game_phase": "in_kyoku", "kyoku": kyoku, "next_kyoku": None,
                            "kyotaku": 0, "scores": [25000] * 4}
                if enabled:
                    with self.assertRaises(GameError):
                        validate_snapshot_state(snapshot, rules)
                else:
                    validate_snapshot_state(snapshot, rules)
                    state.apply({"type": "reach_accepted", "actor": 2, "deltas": [0, 0, -1000, 0],
                                 "scores": [25000, 25000, 24000, 25000], "kyotaku": 1})
                    self.assertEqual(state.kyotaku, 1)

    def test_one_players_four_kans_still_allow_reach(self):
        from game_contract import validate_snapshot_state

        state = EventState(self.rules)
        state.apply({"type": "start_game", "scores": [25000] * 4, "rules": self.rules})
        state.apply({"type": "start_kyoku", "bakaze": "E", "kyoku": 1, "oya": 0,
                     "honba": 0, "kyotaku": 0, "extension_round": 0, "scores": [25000] * 4,
                     "dora_marker": "1m", "hands": [{"tiles": [t for t in "ESWN" for _ in range(3)] + ["P"]},
                                                       *[{"count": 13} for _ in range(3)]]})
        for tile, marker in zip("ESWN", ["2m", "3m", "4m", "5m"]):
            state.apply({"type": "tsumo", "actor": 0, "pai": tile})
            state.apply({"type": "ankan_declared", "actor": 0, "consumed": [tile] * 4})
            state.apply({"type": "ankan", "actor": 0, "consumed": [tile] * 4})
            state.apply({"type": "dora", "dora_marker": marker})
        state.apply({"type": "tsumo", "actor": 0, "pai": "P"})
        state.apply({"type": "reach", "actor": 0})
        state.apply({"type": "dahai", "actor": 0, "pai": "P", "tsumogiri": True})
        kyoku = deepcopy(state.round)
        kyoku["turn"]["last_event"] = deepcopy(state.last_cause)
        validate_snapshot_state({"game_phase": "in_kyoku", "kyoku": kyoku, "next_kyoku": None,
                                 "kyotaku": 0, "scores": [25000] * 4}, self.rules)
        self.assertEqual(state.round["reach_status"][0]["state"], "declared")

    def _fourth_kan_checkpoint(self, rules, *, empty_wall=False, discard=False):
        events = deepcopy(self.vectors['V246_mixed_four_kans_abort_after_discard']['positive']['trace']['input']['events'][:-1])
        events[0]['rules'] = rules
        events[1]['hands'] = [{'count': 13} for _ in range(4)]
        if rules['kan_dora_timing']['ankan'] == 'after_rinshan_discard':
            for i, event in enumerate(events.copy()):
                if event['type'] == 'dora':
                    events[i:i + 2] = [events[i + 1], event]
        state = EventState(rules)
        for event in events[:2]:
            state.apply(event)
        if empty_wall:
            deck = [str(n) + suit for suit in 'mps' for n in range(1, 10)
                    for _ in range(3 if n == 5 else 4)] + ['5mr', '5pr', '5sr']
            deck += [tile for tile in 'ESWNPFC' for _ in range(4)]
            for tile in [t for t in 'ESWC' for _ in range(4)] + ['9p', '8p', '7p', '6p', '5p', '9s', '9m', '8m']:
                deck.remove(tile)
            # 63 earlier normal draws plus the witness's three normal draws
            # and four kan replacements consume exactly 70 live tiles.
            for i, tile in enumerate(deck[:63]):
                state.apply(dict(type='tsumo', actor=i % 4, pai=None))
                state.apply(dict(type='dahai', actor=i % 4, pai=tile, tsumogiri=True))
        stop = len(events) if discard else max(i for i, e in enumerate(events) if e['type'] == 'tsumo') + 1
        for event in events[2:stop]:
            if 'actor' in event and empty_wall:
                event['actor'] = (event['actor'] + 3) % 4
            if event['type'] == 'tsumo':
                event['pai'] = None
            state.apply(event)
        return state

    def test_fourth_kan_snapshots_preserve_forced_rinshan_turn(self):
        for timing in ('before_rinshan', 'after_rinshan_discard'):
            rules = deepcopy(self.rules)
            rules['kan_dora_timing']['ankan'] = timing
            for empty_wall in (False, True):
                for discard in (False, True):
                    state = self._fourth_kan_checkpoint(rules, empty_wall=empty_wall, discard=discard)
                    for resolving in (False, True):
                        with self.subTest(timing=timing, empty_wall=empty_wall, discard=discard, resolving=resolving):
                            snapshot = self._public_snapshot(state)
                            if resolving:
                                snapshot['state']['kyoku']['turn']['phase'] = 'resolving'
                            self._restore_public_snapshot(snapshot, rules)
                            bad = deepcopy(snapshot)
                            kyoku = bad['state']['kyoku']
                            if discard:
                                kyoku['haitei'] = True
                            else:
                                kyoku['rinshan'] = False
                            with self.assertRaisesRegex(GameError, 'fourth-kan terminal'):
                                EventState(rules).restore(bad['state'])
                            receiver = self._observer_receiver(rules=rules, initial_snapshot=True)
                            self._assert_receiver_rejects_atomically(
                                receiver, bad, 'fourth-kan terminal|last-tile flag|deferred dora marker')
                            if discard:
                                # An otherwise consistent river projection
                                # cannot transfer the terminal turn to a seat
                                # that never made a kan.
                                bad = deepcopy(snapshot)
                                kyoku = bad['state']['kyoku']
                                actor = kyoku['turn']['actor']
                                other = next(a for a, n in enumerate(kyoku['kan_counts']) if n == 0)
                                kyoku['rivers'][other].append(kyoku['rivers'][actor].pop())
                                kyoku['turn']['actor'] = kyoku['turn']['last_event']['actor'] = other
                                receiver = self._observer_receiver(rules=rules, initial_snapshot=True)
                                self._assert_receiver_rejects_atomically(receiver, bad, 'no preceding kan|last-tile flag')

    def test_fourth_kan_snapshot_guard_preserves_normal_draw_exceptions(self):
        rules = deepcopy(self.rules)
        rules['abortive_draws'].remove('suukan_sanra')
        state = self._fourth_kan_checkpoint(rules, discard=True)
        state.apply(dict(type='tsumo', actor=3, pai=None))
        self.assertFalse(state.round['rinshan'])
        self._restore_public_snapshot(self._public_snapshot(state), rules)

        state = EventState(self.rules)
        state.apply(dict(type='start_game', rules=self.rules, scores=[25000] * 4))
        state.apply(dict(type='start_kyoku', bakaze='E', kyoku=1, oya=0, honba=0, kyotaku=0,
                         extension_round=0, scores=[25000] * 4, dora_marker='1m',
                         hands=[{'tiles': [t for t in 'ESWN' for _ in range(3)] + ['P']},
                                *[{'count': 13} for _ in range(3)]]))
        for tile, marker in zip('ESWN', ('2m', '3m', '4m', '5m')):
            state.apply(dict(type='tsumo', actor=0, pai=tile))
            state.apply(dict(type='ankan_declared', actor=0, consumed=[tile] * 4))
            state.apply(dict(type='ankan', actor=0, consumed=[tile] * 4))
            state.apply(dict(type='dora', dora_marker=marker))
        state.apply(dict(type='tsumo', actor=0, pai='P'))
        state.apply(dict(type='dahai', actor=0, pai='P', tsumogiri=True))
        state.apply(dict(type='tsumo', actor=1, pai=None))
        self.assertFalse(state.round['rinshan'])
        self._restore_public_snapshot(self._public_snapshot(state), self.rules)

    def test_failed_restore_leaves_state_untouched(self):
        source = self._dealt()
        source.apply({"type":"tsumo","actor":0,"pai":None})
        state = self._dealt()
        before = deepcopy({k: getattr(state, k) for k in ("self_seat","game_phase","scores","kyotaku","next","round","last_cause")})
        snapshot = self._snapshot(source, {"actor":0,"phase":"awaiting_draw","last_event_seq":4,
                                         "last_event":{"type":"tsumo","actor":0,"pai":None}})
        with self.assertRaises(Exception):
            state.restore(snapshot)
        after = {k: getattr(state, k) for k in ("self_seat","game_phase","scores","kyotaku","next","round","last_cause")}
        self.assertEqual(before, after)

    def _four_winds_reach_events(self, previous_reaches=0):
        events = deepcopy(self.vectors['V247_called_reach_still_pays_deposit']['positive']['trace']['input']['events'][:2])
        events[1]['hands'] = [{'count': 13} for _ in range(4)]
        scores = [25000] * 4
        for actor in range(4):
            events.append({'type': 'tsumo', 'actor': actor, 'pai': None})
            declares = actor < previous_reaches or actor == 3
            if declares:
                events.append({'type': 'reach', 'actor': actor})
            events.append({'type': 'dahai', 'actor': actor, 'pai': 'N', 'tsumogiri': True})
            if actor < previous_reaches:
                scores[actor] -= 1000
                events.append({'type': 'reach_accepted', 'actor': actor,
                               'deltas': [-1000 if seat == actor else 0 for seat in range(4)],
                               'scores': scores.copy(), 'kyotaku': actor + 1})
        return events

    @staticmethod
    def _automatic_draw_event(state, reason):
        result = {'type': 'ryukyoku', 'reason': reason, 'tenpai': None}
        current = {key: state.round[key] for key in ('bakaze', 'kyoku', 'oya', 'honba', 'extension_round')}
        return {'type': 'end_kyoku', 'result': result, 'deltas': [0] * 4,
                'scores': state.scores.copy(),
                'next': next_kyoku(current, result, state.scores, state.kyotaku, state.rules)}

    def test_automatic_draw_requires_reach_acceptance_in_receiver(self):
        for previous_reaches in (0, 3):
            for accept_last in (False, True):
                with self.subTest(previous_reaches=previous_reaches, accept_last=accept_last):
                    welcome = deepcopy(self.vectors['V79_spectate_requires_snapshot']['positive']['trace']['welcome'])
                    welcome['mode'] = 'replay'
                    receiver = Receiver(welcome, v.strict_load_bytes,
                                        v._session_schema_validator(v.SchemaSet(), welcome['profile_hash']))
                    def send(event):
                        message = {key: welcome[key] for key in ('yamai', 'session_id', 'game_id')}
                        message.update(kind='event', seq=receiver.applied + 1,
                                       original_seq=receiver.applied + 1, event=event)
                        receiver.receive(json.dumps(message).encode())
                    for event in self._four_winds_reach_events(previous_reaches):
                        send(event)
                    if accept_last:
                        scores = receiver.game.scores.copy()
                        scores[3] -= 1000
                        send({'type': 'reach_accepted', 'actor': 3, 'deltas': [0, 0, 0, -1000],
                              'scores': scores, 'kyotaku': previous_reaches + 1})
                    reason = 'suucha_riichi' if previous_reaches == 3 and accept_last else 'suufon_renda'
                    event = self._automatic_draw_event(receiver.game, reason)
                    if accept_last:
                        send(event)
                        self.assertEqual(receiver.game.kyotaku, previous_reaches + 1)
                        self.assertEqual(receiver.game.scores[3], 24000)
                    else:
                        before = deepcopy(vars(receiver.game))
                        applied, known = receiver.applied, deepcopy(receiver.known)
                        with self.assertRaisesRegex(SessionError, 'automatic draw before reach acceptance'):
                            send(event)
                        self.assertEqual(vars(receiver.game), before)
                        self.assertEqual((receiver.applied, receiver.known), (applied, known))

    def test_restored_declared_reach_still_requires_acceptance_before_draw(self):
        source = EventState(self.rules)
        for event in self._four_winds_reach_events():
            source.apply(event)
        kyoku = deepcopy(source.round)
        kyoku['turn']['last_event'] = deepcopy(source.last_cause)
        snapshot = {'mode': 'replay', 'seat': None, 'game_phase': 'in_kyoku',
                    'kyoku': kyoku, 'next_kyoku': None, 'scores': source.scores.copy(), 'kyotaku': 0}
        state = EventState(self.rules)
        state.restore(snapshot)
        before = deepcopy(vars(state))
        with self.assertRaisesRegex(GameError, 'automatic draw before reach acceptance'):
            state.apply(self._automatic_draw_event(state, 'suufon_renda'))
        self.assertEqual(vars(state), before)
        state.apply({'type': 'reach_accepted', 'actor': 3, 'deltas': [0, 0, 0, -1000],
                     'scores': [25000, 25000, 25000, 24000], 'kyotaku': 1})
        state.apply(self._automatic_draw_event(state, 'suufon_renda'))
        self.assertEqual(state.kyotaku, 1)

    def test_automatic_draw_reach_guard_preserves_other_round_endings(self):
        for key in ('V246_mixed_four_kans_abort_after_discard', 'V337_sanchaho_precedes_reach_acceptance'):
            with self.subTest(vector=key):
                v.semantic_game_trace(deepcopy(self.vectors[key]['positive']['trace']))

    def test_inventory_failure_leaves_state_untouched(self):
        source = self._dealt()
        source.apply({"type":"tsumo","actor":0,"pai":None})
        state = self._dealt()
        before = deepcopy({k: getattr(state, k) for k in ("self_seat","game_phase","scores","kyotaku","next","round","last_cause")})
        snapshot = self._snapshot(source, {"actor":0,"phase":"awaiting_action","last_event_seq":4,
                                         "last_event":{"type":"tsumo","actor":0,"pai":None}})
        snapshot["kyoku"]["hands"][0] = {"tiles":["1m"]*5+["2m"]*9}
        snapshot["kyoku"]["wall_remaining"] = 69
        with self.assertRaises(Exception):
            state.restore(snapshot)
        after = {k: getattr(state, k) for k in ("self_seat","game_phase","scores","kyotaku","next","round","last_cause")}
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
