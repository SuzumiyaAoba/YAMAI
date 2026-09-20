"""Regressions carried across the draft.6 specification integration.

Official positive/negative traces cover the request and event contracts.
These additional checks vary inputs and keep independent expected outcomes.
"""

from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import score_oracle as oracle
import validate_artifacts as validator
from game_contract import EventState, GameError, next_kyoku
from scoring_reference import ScoringError, normal_payments, tile_index
from session_contract import Receiver, SessionError

VECTORS = json.loads((ROOT / "test-vectors/yrc-0003/1.0-draft.9/vectors.json").read_text())
SCORING = json.loads(oracle.DEFAULT_INPUT.read_text())
FIXTURES = {f["id"]: f for f in SCORING["fixtures"]}


class ProtocolRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schemas = validator.SchemaSet()

    def receiver(self, welcome, **options):
        return Receiver(welcome, validator.strict_load_bytes,
            validator._session_schema_validator(self.schemas, welcome["profile_hash"]), **options)

    @staticmethod
    def raw(message):
        return json.dumps(message, separators=(",", ":"), ensure_ascii=False).encode()

    def test_initial_kyoku_requires_game_but_no_previous_kyoku(self):
        trace = VECTORS["V104_wire_complete_game"]["positive"]["trace"]
        start_game, start_kyoku = [s["message"]["event"] for s in trace["steps"][:2]]
        state = EventState(trace["welcome"]["rules"])
        with self.assertRaises(GameError):
            state.apply(start_kyoku)
        state.apply(start_game)
        state.apply(start_kyoku)
        self.assertEqual(state.game_phase, "in_kyoku")
        with self.assertRaises(GameError):
            state.apply(start_kyoku)

    def test_session_prefixes_are_independent(self):
        trace = copy.deepcopy(VECTORS["V104_wire_complete_game"]["positive"]["trace"])
        one = self.receiver(trace["welcome"])
        welcome_two = copy.deepcopy(trace["welcome"])
        welcome_two["session_id"] = "independent-session"
        two = self.receiver(welcome_two)
        for step in trace["steps"][:3]:
            message = step["message"]
            one.receive(self.raw(message))
            other = {**message, "session_id": welcome_two["session_id"]}
            two.receive(self.raw(other))
        one.receive(self.raw(trace["steps"][3]["message"]))
        self.assertEqual((one.applied, two.applied), (4, 3))
        self.assertEqual(sorted(one.known), [1, 2, 3, 4])
        self.assertEqual(sorted(two.known), [1, 2, 3])
        self.assertEqual(two.active_requests, set())

    def test_head_equal_resume_preserves_game_request_and_prefix(self):
        trace = copy.deepcopy(VECTORS["V104_wire_complete_game"]["positive"]["trace"])
        receiver = self.receiver(trace["welcome"])
        for step in trace["steps"][:4]:
            receiver.receive(self.raw(step["message"]))
        before = copy.deepcopy((vars(receiver.game), receiver.requests, receiver.known, receiver.time_bank_ms))
        welcome = copy.deepcopy(trace["welcome"])
        welcome.update(resumed=True, replay_from_seq=5, replay_through_seq=4)
        welcome["resume"]["token"] = "rt_DDDDDDDDDDDDDDDDDDDDDD"
        receiver.begin_resume(welcome)
        self.assertIsNone(receiver.recovery)
        self.assertEqual(receiver.active_requests, {"r1"})
        self.assertEqual((vars(receiver.game), receiver.requests, receiver.known, receiver.time_bank_ms), before)
        for step in trace["steps"][4:]:
            receiver.receive(self.raw(step["message"]))
        self.assertTrue(receiver.ended)

    def test_replay_original_can_equal_new_seq(self):
        trace = VECTORS["V124_snapshot_restores_recording_cursor"]["positive"]["trace"]
        receiver = self.receiver(trace["welcome"])
        initial = trace["steps"][0]["message"]
        self.assertEqual((initial["seq"], initial["original_seq"]), (1, 1))
        self.assertEqual(receiver.receive(self.raw(initial)), "applied")
        self.assertEqual((receiver.applied, receiver.original_seq), (1, 1))
        self.assertEqual(receiver.receive(self.raw(initial)), "duplicate")

    def test_initial_spectate_snapshot_is_accepted_once(self):
        trace = VECTORS["V264_session_observer_snapshot_bootstrap"]["positive"]["trace"]
        welcome = next(s["message"] for s in trace["messages"] if s["message"]["kind"] == "welcome")
        snapshot = copy.deepcopy(VECTORS["V111_initial_observer_has_no_prior_seq"]["positive"])
        receiver = self.receiver(welcome, initial_snapshot=True)
        self.assertEqual(receiver.receive(self.raw(snapshot)), "applied")
        self.assertEqual(receiver.game.game_phase, "in_kyoku")
        self.assertEqual(receiver.active_requests, set())
        snapshot.update(seq=3, replaces_through_seq=2)
        with self.assertRaises(SessionError):
            receiver.receive(self.raw(snapshot))
        self.assertEqual(receiver.applied, 1)

    def test_agariyame_changes_only_final_dealer_win(self):
        rules = copy.deepcopy(SCORING["rules"])
        current = dict(bakaze="S", kyoku=4, oya=3, honba=0, extension_round=0)
        scores = [23000, 23000, 23000, 31000]
        win = dict(type="hora", wins=[dict(actor=3)])
        draw = dict(type="ryukyoku", reason="fanpai", tenpai=[False, False, False, True])
        self.assertEqual(next_kyoku(current, win, scores, 0, rules)["type"], "end_game")
        rules["agariyame"] = False
        self.assertEqual(next_kyoku(current, win, scores, 0, rules)["type"], "renchan")
        rules["agariyame"] = True
        self.assertEqual(next_kyoku(current, draw, scores, 0, rules)["type"], "renchan")
        current["extension_round"] = 1
        self.assertEqual(next_kyoku(current, draw, scores, 0, rules)["type"], "end_game")
        current["extension_round"] = 4
        self.assertEqual(next_kyoku(current, draw, [25000]*4, 0, rules)["type"], "end_game")


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
        with self.assertRaises(ScoringError) as error:
            oracle.compute_fixture(fixture, SCORING["rules"])
        self.assertEqual(error.exception.code, "no_yaku")

    def test_open_honitsu_is_two_han_in_schema(self):
        schemas = validator.SchemaSet()
        message = {"id": "honitsu", "unit": "han", "value": 2}
        result = validator.schema_by_id(schemas, "urn:yamai:schema:yrc-0005:1.0-draft.7:scoring-result")
        schemas._validate(message, result["$defs"]["yaku"], "yaku", result)

    def test_draw_tenpai_and_penalty_offender_are_inputs(self):
        fixture = copy.deepcopy(next(f for f in SCORING["fixtures"] if f["input"]["type"] == "ryukyoku" and sum(f["expected"]["tenpai"]) == 1))
        fixture["input"]["hands"][0], fixture["input"]["hands"][2] = fixture["input"]["hands"][2], fixture["input"]["hands"][0]
        self.assertEqual(oracle.compute_fixture(fixture, SCORING["rules"])["deltas"], [-1000, -1000, 3000, -1000])
        fixture = copy.deepcopy(next(f for f in SCORING["fixtures"] if f["input"]["type"] == "penalty"))
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
        self.assertEqual(normal_payments(1000, 1, 1, 2), {0: 1000, 2: 2000, 3: 1000})
        self.assertEqual(normal_payments(1000, 2, 0, 2), {0: 6000})

    def test_repeated_dora_markers_each_count(self):
        fixture = copy.deepcopy(FIXTURES["yaku_riichi"])
        fixture["input"]["dora_markers"] = ["9m", "9m"]
        fixture["input"]["ura_dora_markers"] *= 2
        fixture["state"]["kan_counts"][2] = 1
        fixture["state"]["pre_state"]["kan_counts"][2] = 1
        win = oracle.compute_fixture(fixture, SCORING["rules"])["wins"][0]
        physical = fixture["input"]["hand"]["concealed_tiles"] + [fixture["input"]["winning_tile"]]
        count = sum(tile_index(t) == tile_index("1m") for t in physical) * 2
        self.assertEqual(next(b["han"] for b in win["bonuses"] if b["id"] == "dora"), count)

    def test_table_sticks_are_not_paid_again_by_discarder(self):
        fixture = next(f for f in SCORING["fixtures"] if f["input"].get("other_winners") and f["state"]["kyotaku"] == 1)
        actual = oracle.compute_fixture(fixture, SCORING["rules"])
        self.assertEqual(sum(actual["deltas"]), 1000)
        for win in actual["wins"]:
            self.assertEqual(sum(p["points"] for p in win["payments"]) + win["kyotaku_points"], win["deltas"][win["actor"]])

    def test_pao_honba_rounding_conserves_hundreds(self):
        win = self.score("settlement_pao_partial_ron")["wins"][0]
        self.assertEqual(win["payments"], [dict(**{"from": 0, "to": 1, "points": 64200}), dict(**{"from": 2, "to": 1, "points": 32100})])
        self.assertEqual(sum(p["points"] for p in win["payments"]), win["hand_points"] + 300)


if __name__ == "__main__":
    unittest.main()
