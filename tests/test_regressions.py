"""Regression tests with independently specified outcomes, including invalid twins."""

from __future__ import annotations

import copy
import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import score_oracle as oracle
import validate_artifacts as validator
from protocol_state import Projection, next_round

VECTORS = json.loads((ROOT / "test-vectors/yrc-0003/1.0-draft.6/vectors.json").read_text())
SCORING = json.loads(oracle.DEFAULT_INPUT.read_text())
FIXTURES = {f["id"]: f for f in SCORING["fixtures"]}


def trace(number: int) -> dict:
    return copy.deepcopy(next(v["positive"]["trace"] for k, v in VECTORS.items() if k.startswith(f"V{number:02}_")))


def sync(value: dict) -> dict:
    """Rebuild exact wire bytes after an intentional semantic mutation."""
    value["ledger"] = []
    for step in value["messages"]:
        step["wire"] = json.dumps(step["message"], separators=(",", ":"), ensure_ascii=False)
        if step["direction"] == "out" and "seq" in step["message"]:
            value["ledger"].append({k: copy.deepcopy(step[k]) for k in
                ("message", "wire", "transaction_id", "operation_id", "game_event_index") if k in step}
                | {"seq": step["message"]["seq"]})
    return value


def spectate_trace() -> dict:
    value = trace(35)
    value.pop("visibility", None)
    value["clients"][0].update(mode="spectate", view="public", seat=None)
    value["messages"] = value["messages"][:3]
    for step in value["messages"]:
        message = step["message"]
        if message["kind"] in {"hello", "join"}:
            message["capabilities"]["optional"] = ["snapshot"]
        if message["kind"] in {"join", "welcome"}:
            message.update(mode="spectate", view="public")
            message.pop("resume", None)
            if message["kind"] == "join":
                message.pop("seat", None)
                message["target"] = {"type": "game", "id": value["game_id"]}
            else:
                message["seat"] = None
    message = copy.deepcopy(VECTORS["V18_snapshot_state"]["positive"])
    message.update(seq=1, replaces_through_seq=0,
                   session_id=value["clients"][0]["session_id"], game_id=value["game_id"])
    message = validator._stateful_project_message(message, "spectate", "public", None)
    value["messages"].append(dict(at_ms=3, direction="out", client_id=value["clients"][0]["client_id"],
        transaction_id="initial-snapshot", operation_id="initial-snapshot", message=message))
    return sync(value)


def resume_trace() -> dict:
    value = trace(35)
    for step in value["messages"][:2]:
        step["message"]["capabilities"]["optional"] = ["resume"]
    suffix = copy.deepcopy(value["messages"][:3])
    for i, step in enumerate(suffix):
        step.update(at_ms=20 + i, connection_id="reconnected")
        message = step["message"]
        if message["kind"] == "join":
            message.pop("seat", None)
            message["resume"] = dict(token="abcdefghijklmnopqrstuv", last_seq=6)
        elif message["kind"] == "welcome":
            message.update(resumed=True, replay_from_seq=7, resume=dict(token="zyxwvutsrqponmlkjihgfed", expires_in_ms=60000))
    value["messages"].extend(suffix)
    # A continuing game event proves welcome did not reset the projection.
    event = copy.deepcopy(value["messages"][5])
    event.update(at_ms=23, connection_id="reconnected")
    event["message"].update(seq=7, event=dict(type="tsumo", actor=1, pai=None))
    value["messages"].append(event)
    return sync(value)


def cancelled_trace() -> dict:
    value = trace(36)
    for step in value["messages"]:
        m = step["message"]
        rules = m.get("rules", m.get("event", {}).get("rules"))
        if rules:
            rules["invalid_action_policy"] = "chombo"
    value["messages"] = [s for s in value["messages"] if s["message"]["kind"] not in {"action", "ack"}]
    p0, p1 = value["clients"]
    env = dict(yamai=validator.PROTOCOL, game_id=value["game_id"])
    value["messages"].append(dict(at_ms=22, direction="in", client_id=p0["client_id"], arrival_ticket=1,
        message=env | dict(kind="action", session_id=p0["session_id"], request_id="r36", action_id="invalid")))
    for cl, seq, status in ((p0, 6, "rejected"), (p0, 7, "cancelled"), (p1, 6, "cancelled")):
        req = next(s["message"] for s in value["messages"] if s["message"]["kind"] == "request" and s["client_id"] == cl["client_id"])
        value["messages"].append(dict(at_ms=22, direction="out", client_id=cl["client_id"],
            transaction_id="chombo", operation_id="d36", message=env | dict(kind="ack",
            session_id=cl["session_id"], seq=seq, request_id=req["request_id"],
            action_id="invalid" if status == "rejected" else None, status=status,
            elapsed_ms=10, time_bank_ms=req["time_bank_ms"])))
    for cl, seq in ((p0, 8), (p1, 7)):
        event = copy.deepcopy(VECTORS["V26_penalty_terminal"]["positive"]["event"])
        value["messages"].append(dict(at_ms=22, direction="out", client_id=cl["client_id"],
            transaction_id="chombo", operation_id="d36", message=env | dict(kind="event",
            session_id=cl["session_id"], seq=seq, event=event)))
    return sync(value)


class ProtocolRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schemas = validator.SchemaSet()
        cls.root = validator.schema_by_id(cls.schemas, "urn:yamai:schema:yrc-0003:1.0-draft.6:message")

    def validate(self, value):
        self.schemas.validate(value, validator.schema_by_id(self.schemas, validator.STATEFUL_TRACE_SCHEMA_ID))
        validator.semantic_session_trace(value, schemas=self.schemas, root_schema=self.root)

    def test_initial_and_subsequent_kyoku(self):
        p = Projection()
        with self.assertRaises(ValueError): p.apply({"type": "start_kyoku"})
        p.apply({"type": "start_game"})
        p.apply({"type": "start_kyoku"})
        with self.assertRaises(ValueError): p.apply({"type": "start_kyoku"})
        p.apply({"type": "end_kyoku", "next": {"type": "renchan"}})
        p.apply({"type": "start_kyoku"})
        p.apply({"type": "end_kyoku", "next": {"type": "end_game"}})
        with self.assertRaises(ValueError): p.apply({"type": "start_kyoku"})
        p.apply({"type": "end_game"})
        self.assertEqual(p.game, "ENDED")

    def test_reach_then_discard_is_one_transaction(self):
        p = Projection(game="IN_KYOKU", turn="awaiting_action", actor=0)
        p.apply(dict(type="reach", actor=0), "compound")
        with self.assertRaises(ValueError): p.request(0)
        with self.assertRaises(ValueError): p.apply(dict(type="dahai", actor=0), "different")
        p.apply(dict(type="dahai", actor=0), "compound")
        with self.assertRaises(ValueError): p.apply(dict(type="reach", actor=0), "compound")

    def test_chi_and_pon_do_not_issue_another_discard_request(self):
        for call in ("chi", "pon"):
            p = Projection(game="IN_KYOKU", turn="awaiting_responses", actor=0)
            p.apply(dict(type=call, actor=1), "call")
            with self.assertRaises(ValueError): p.request(1)
            p.apply(dict(type="dahai", actor=1), "call")
            self.assertEqual(p.turn, "awaiting_responses")

    def test_ankan_declaration_then_immediate_commit(self):
        p = Projection(game="IN_KYOKU", turn="awaiting_action", actor=0)
        with self.assertRaises(ValueError): p.apply(dict(type="ankan", actor=0))
        # ankan_chankan=never uses this same sequence without any request.
        p.apply(dict(type="ankan_declared", actor=0), "kan")
        p.apply(dict(type="ankan", actor=0), "kan")
        p.apply(dict(type="tsumo", actor=0), "kan")
        self.assertIsNone(p.pending_kan)

    def test_session_ledgers_are_independent(self):
        value = trace(36)
        self.validate(value)
        for cl in value["clients"]:
            seqs = [s["message"]["seq"] for s in value["messages"] if s["client_id"] == cl["client_id"] and "seq" in s["message"]]
            self.assertEqual(seqs, [1, 2, 3, 4, 5, 6])
        bad = copy.deepcopy(value)
        bad["clients"][1]["session_id"] = bad["clients"][0]["session_id"]
        with self.assertRaises(validator.ArtifactError): self.validate(bad)

    def test_group_clock_freezes_at_each_response(self):
        value = trace(36)
        first = value["clients"][0]["client_id"]
        for step in value["messages"]:
            if step["client_id"] == first and step["message"]["kind"] == "action":
                step["at_ms"] = 13
            if step["client_id"] == first and step["message"]["kind"] == "ack":
                step["message"]["elapsed_ms"] = 1
        self.validate(sync(value))
        for step in value["messages"]:
            if step["client_id"] == first and step["message"]["kind"] == "ack":
                step["message"]["elapsed_ms"] = 10
        with self.assertRaisesRegex(validator.ArtifactError, "fixed response"):
            self.validate(sync(value))

    def test_replay_original_can_equal_new_seq(self):
        value = trace(40)
        self.validate(value)
        events = [s["message"] for s in value["messages"] if s["message"]["kind"] == "event"]
        self.assertEqual([(e["seq"], e["original_seq"]) for e in events], [(1, 1), (2, 2), (3, 3)])
        events[1]["original_seq"] = 1
        with self.assertRaises(validator.ArtifactError): self.validate(sync(value))

    def test_resume_at_head_preserves_game_and_prefix(self):
        self.validate(resume_trace())

    def test_resume_does_not_restart_pending_clock(self):
        value = trace(35)
        value.pop("visibility", None)
        responses = value["messages"][7:]
        value["messages"] = value["messages"][:7]
        handshake = copy.deepcopy(value["messages"][:3])
        for i, step in enumerate(handshake):
            step.update(at_ms=10+i, connection_id="resume-pending")
            m = step["message"]
            if m["kind"] in {"hello", "join"}:
                m["capabilities"]["optional"] = ["resume"]
            if m["kind"] == "join":
                m.pop("seat", None)
                m["resume"] = dict(token="abcdefghijklmnopqrstuv", last_seq=4)
            elif m["kind"] == "welcome":
                m.update(resumed=True, replay_from_seq=5,
                    resume=dict(token="zyxwvutsrqponmlkjihgfed", expires_in_ms=60000))
        value["messages"].extend(handshake)
        for step in responses:
            step["connection_id"] = "resume-pending"
        value["messages"].extend(responses)
        self.validate(sync(value))

    def test_initial_spectate_snapshot(self):
        value = spectate_trace()
        self.validate(value)
        sn = copy.deepcopy(value["messages"][-1])
        sn["message"].update(seq=2, replaces_through_seq=1)
        value["messages"].append(sn)
        with self.assertRaisesRegex(validator.ArtifactError, "snapshot outside"):
            self.validate(sync(value))

    def test_snapshot_other_seat_and_public_have_no_pending(self):
        message = copy.deepcopy(VECTORS["V18_snapshot_state"]["positive"])
        for mode, view, seat in (("play", "seat", 1), ("spectate", "public", None)):
            source = copy.deepcopy(message)
            if mode == "play":
                # A complete seat-1 state must come from the host; seat 0's
                # private snapshot cannot supply seat 1's furiten/bank/hand.
                with self.assertRaises(validator.ArtifactError):
                    validator._stateful_project_message(source, mode, view, seat)
                source["state"]["seat"] = seat
                source["state"]["kyoku"]["self_state"] = dict(temporary_furiten=False, riichi_furiten=False, kuikae_forbidden=[], time_bank_ms=15000)
                source["state"]["kyoku"]["hands"][seat] = {"tiles": ["1m", "2m", "3m", "4m", "5m", "6m", "7p", "8p", "9p", "2s", "3s", "4s", "E"]}
            projected = validator._stateful_project_message(source, mode, view, seat)
            self.schemas.validate(projected, self.root)
            validator.semantic_message(projected, "projection")
            self.assertFalse(projected["state"].get("pending_requests"))

    def test_normative_snapshot_example_matches_schema(self):
        text = (ROOT / "docs/yamai-protocol.md").read_text()
        examples = [json.loads(s) for s in re.findall(r"```json\n(.*?)\n```", text, re.S) if '"kind": "snapshot"' in s]
        self.assertTrue(examples)
        for message in examples:
            self.schemas.validate(message, self.root)
            validator.semantic_message(message, "normative example")

    def test_all_complete_normative_json_messages_match_schema(self):
        text = (ROOT / "docs/yamai-protocol.md").read_text()
        checked = 0
        for raw in re.findall(r"```json\n(.*?)\n```", text, re.S):
            value = json.loads(raw)
            if not isinstance(value, dict):
                continue
            if "kind" in value:
                message = value
            elif value.get("type") == "end_kyoku":
                message = dict(kind="event", yamai=validator.PROTOCOL,
                    session_id="example", game_id="example", seq=1, event=value)
            else:
                continue  # Deliberately partial examples, e.g. action candidates.
            with self.subTest(message.get("kind")):
                self.schemas.validate(message, self.root)
                validator.semantic_message(message, "normative example")
            checked += 1
        self.assertGreaterEqual(checked, 10)

    def test_cancelled_is_terminal_and_stale_is_not(self):
        value = cancelled_trace()
        self.validate(value)
        bad = copy.deepcopy(value)
        for step in bad["messages"]:
            if step["message"].get("status") == "cancelled":
                step["message"].update(status="stale", action_id="invalid")
        with self.assertRaises(validator.ArtifactError): self.validate(sync(bad))
        self.assertNotIn("stale", validator.TERMINAL_ACK_STATUSES)
        self.assertIn("cancelled", validator.TERMINAL_ACK_STATUSES)

    def test_agariyame_changes_only_final_dealer_win(self):
        rules = copy.deepcopy(SCORING["rules"])
        kyoku = dict(bakaze="S", kyoku=4, oya=3, extension_round=0)
        scores = [23000, 23000, 23000, 31000]
        self.assertEqual(next_round(rules, kyoku, scores, dealer_continues=True, dealer_won=True), ("end_game", 0))
        rules["agariyame"] = False
        self.assertEqual(next_round(rules, kyoku, scores, dealer_continues=True, dealer_won=True), ("renchan", 0))
        rules["agariyame"] = True
        self.assertEqual(next_round(rules, kyoku, scores, dealer_continues=True, dealer_won=False), ("renchan", 0))
        self.assertEqual(next_round(rules, kyoku, scores, dealer_continues=False, dealer_won=False), ("end_game", 0))
        kyoku["extension_round"] = 1
        self.assertEqual(next_round(rules, kyoku, scores, dealer_continues=True, dealer_won=False), ("end_game", 1))
        kyoku["extension_round"] = 4
        self.assertEqual(next_round(rules, kyoku, [25000]*4, dealer_continues=True, dealer_won=False), ("end_game", 4))


class ScoringRegressionTests(unittest.TestCase):
    def score(self, identifier, mutate=None):
        fixture = copy.deepcopy(FIXTURES[identifier])
        if mutate: mutate(fixture)
        return oracle.compute_fixture(fixture, SCORING["rules"])

    def test_metadata_and_expected_are_not_scoring_inputs(self):
        for original in SCORING["fixtures"]:
            with self.subTest(original["id"]):
                fixture = copy.deepcopy(original)
                expected = fixture.pop("expected")
                fixture.pop("id")
                fixture.pop("description", None)
                self.assertEqual(oracle.compute_fixture(fixture, SCORING["rules"]), expected)
                fixture.update(id="same-name-for-every-fixture", expected={"deliberately": "wrong"})
                self.assertEqual(oracle.compute_fixture(fixture, SCORING["rules"]), expected)

    def test_independent_fu_han_points(self):
        # Literal values hand-calculated from the normalized inputs.
        cases = {"yaku_tanyao": (30, 2, 2000), "yaku_pinfu": (20, 2, 1500),
                 "yaku_iipeikou": (40, 1, 1300), "yaku_junchan": (50, 3, 6400),
                 "yaku_chiitoitsu": (25, 2, 1600), "yaku_sankantsu": (70, 2, 4500)}
        for identifier, expected in cases.items():
            with self.subTest(identifier):
                win = self.score(identifier)["wins"][0]
                self.assertEqual((win["fu"], win["han"], win["hand_points"]), expected)

    def test_fixture_name_cannot_make_invalid_junchan_a_yaku(self):
        fixture = copy.deepcopy(FIXTURES["yaku_junchan"])
        fixture["input"]["hand"]["concealed_tiles"][-1] = "2m"
        fixture["input"]["winning_tile"] = "2m"
        fixture["input"]["dora_markers"] = ["1m"]
        with self.assertRaisesRegex(ValueError, "no legal winning decomposition with a yaku"):
            oracle.compute_fixture(fixture, SCORING["rules"])

    def test_open_honitsu_is_two_han_in_schema(self):
        schemas = validator.SchemaSet()
        message = {"id": "honitsu", "unit": "han", "value": 2}
        result = validator.schema_by_id(schemas, "urn:yamai:schema:yrc-0005:1.0-draft.4:scoring-result")
        schemas._validate(message, result["$defs"]["yaku"], "yaku", result)

    def test_draw_tenpai_and_penalty_offender_are_inputs(self):
        fixture = copy.deepcopy(next(f for f in SCORING["fixtures"] if f["input"]["result_type"] == "ryukyoku" and sum(f["expected"]["tenpai"]) == 1))
        fixture["input"]["hands"][0], fixture["input"]["hands"][2] = fixture["input"]["hands"][2], fixture["input"]["hands"][0]
        self.assertEqual(oracle.compute_fixture(fixture, SCORING["rules"])["deltas"], [-1000, -1000, 3000, -1000])
        fixture = copy.deepcopy(next(f for f in SCORING["fixtures"] if f["input"]["result_type"] == "penalty"))
        fixture["input"]["offender"] = 2
        self.assertEqual(oracle.compute_fixture(fixture, SCORING["rules"])["deltas"], [2800, 2600, -8000, 2600])

    def test_south_is_not_green(self):
        fixture = copy.deepcopy(FIXTURES["yakuman_ryuuiisou"])
        def replace_green(value):
            if isinstance(value, list): return [replace_green(v) for v in value]
            if isinstance(value, dict): return {k: replace_green(v) for k, v in value.items()}
            return "S" if value == "F" else value
        fixture["input"] = replace_green(fixture["input"])
        wins = oracle.compute_fixture(fixture, SCORING["rules"])["wins"]
        self.assertNotIn("ryuuiisou", [y["id"] for y in wins[0]["yakus"]])

    def test_dealer_seat_is_not_hardcoded_zero(self):
        self.assertEqual(oracle.normal_payments(1, 1, "tsumo", 1000, 2), {0: 1000, 2: 2000, 3: 1000})
        self.assertEqual(oracle.normal_payments(2, 0, "ron", 1000, 2), {0: 6000})

    def test_repeated_dora_markers_each_count(self):
        fixture = copy.deepcopy(FIXTURES["yaku_riichi"])
        fixture["input"]["dora_markers"] = ["9m", "9m"]
        win = oracle.compute_fixture(fixture, SCORING["rules"])["wins"][0]
        physical = oracle.raw_tiles(fixture["input"]["hand"], fixture["input"]["winning_tile"])
        count = sum(oracle.norm(t) == "1m" for t in physical) * 2
        self.assertEqual(next(b["han"] for b in win["bonuses"] if b["id"] == "dora"), count)

    def test_winning_tile_location_changes_concealed_triplet_fu(self):
        closed = oracle.Group("triplet", ("9m",)*3, False, True)
        self.assertFalse(oracle.concealed_group(closed, "ron"))
        self.assertTrue(oracle.concealed_group(closed, "tsumo"))
        self.assertEqual(oracle.pair_fu("E", 2, dict(oya=2, bakaze="E")), 4)

    def test_table_sticks_are_not_paid_again_by_discarder(self):
        fixture = next(f for f in SCORING["fixtures"] if f["input"].get("other_winners"))
        actual = oracle.compute_fixture(fixture, SCORING["rules"])
        self.assertEqual(actual["deltas"], [-3800, 2900, 1900, 0])
        self.assertEqual(sum(actual["deltas"]), 1000)
        for win in actual["wins"]:
            self.assertEqual(sum(p["points"] for p in win["payments"]) + win["kyotaku_points"], win["deltas"][win["actor"]])

    def test_pao_honba_rounding_conserves_hundreds(self):
        win = dict(hand_points=32000, payments=[dict(**{"from": 0, "to": 1, "points": 16000}), dict(**{"from": 2, "to": 1, "points": 16000})])
        oracle.allocate_honba(win, 300)
        self.assertEqual([p["points"] for p in win["payments"]], [16200, 16100])


if __name__ == "__main__":
    unittest.main()
