"""Cross-cutting properties for the complete decision and event contracts."""
import json
import unittest
from copy import deepcopy

import validate_artifacts as v
from game_contract import EventState, canonical_action, legal_actions, next_kyoku
from session_contract import Receiver, SessionError


class GameContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.vectors = v.strict_load(v.ROOT / f"test-vectors/yrc-0003/{v.PROTOCOL}/vectors.json")
        cls.rules = v.strict_load(v.ROOT / f"test-vectors/yrc-0005/{v.PROFILE_REVISION}/scoring.json")["rules"]

    def test_complete_choices_ignore_hand_and_consumed_order(self):
        trace = self.vectors["V193_red_consumed_tile_and_compound_discard"]["positive"]["trace"]
        p = deepcopy(trace["input"])
        actual = {canonical_action(a) for a in legal_actions(p, self.rules)}
        p["hand"]["concealed_tiles"].reverse()
        self.assertEqual(actual, {canonical_action(a) for a in legal_actions(p, self.rules)})
        self.assertEqual(actual, {canonical_action(a) for a in trace["expected"]})

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


if __name__ == "__main__":
    unittest.main()
