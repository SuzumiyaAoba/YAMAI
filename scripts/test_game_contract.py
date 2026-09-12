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
