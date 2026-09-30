"""Cross-cutting properties for the complete decision and event contracts."""
import json
import unittest
from copy import deepcopy
from decimal import Decimal, localcontext

import validate_artifacts as v
from game_contract import EventState, GameError, canonical_action, check_hora_payments, json_equal, legal_actions, next_kyoku
from session_contract import Receiver, SessionError


class GameContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.vectors = v.strict_load(v.ROOT / f"test-vectors/protocol/{v.PROTOCOL}/vectors.json")
        cls.rules = v.strict_load(v.ROOT / f"test-vectors/riichi-4p/{v.PROFILE_REVISION}/scoring.json")["rules"]

    def test_complete_choices_ignore_hand_and_consumed_order(self):
        trace = self.vectors["V193_red_consumed_tile_and_compound_discard"]["positive"]["trace"]
        p = deepcopy(trace["input"])
        actual = {canonical_action(a) for a in legal_actions(p, self.rules)}
        p["hand"]["concealed_tiles"].reverse()
        self.assertEqual(actual, {canonical_action(a) for a in legal_actions(p, self.rules)})
        self.assertEqual(actual, {canonical_action(a) for a in trace["expected"]})

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
                if tile == '1m':
                    with self.assertRaises(GameError):
                        candidate.apply(event)
                else:
                    candidate.apply(event)
                    self.assertEqual(candidate.round['pending_kan'], event)

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
