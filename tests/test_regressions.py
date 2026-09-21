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

    def test_snapshot_conserves_scores_and_deposits_before_replacement(self):
        trace = VECTORS["V104_wire_complete_game"]["positive"]["trace"]
        for key in ("V18_snapshot_state", "V57_snapshot_between_kyoku_deposits", "V58_snapshot_ended_rankings"):
            for stick in (0, 1000, 2000):
                with self.subTest(snapshot=key, stick=stick):
                    snapshot = copy.deepcopy(VECTORS[key]["positive"])
                    welcome = copy.deepcopy(trace["welcome"])
                    rules = welcome["rules"]
                    rules["riichi_stick_value"] = stick
                    state = snapshot["state"]
                    state["scores"][0] += (1000 - stick) * state["kyotaku"]
                    if state["game_phase"] == "ended":
                        order = sorted(range(4), key=lambda s: (-state["scores"][s], s))
                        state["final_rankings"] = [order.index(s) + 1 for s in range(4)]
                    validator._check_snapshot(snapshot, rules=rules)
                    game = EventState(rules)
                    game.restore(state)
                    before = copy.deepcopy(vars(game))
                    welcome.update(resumed=True, replay_from_seq=1,
                                   replay_through_seq=snapshot["replaces_through_seq"], scores=state["scores"])
                    for delta in (-100, 100):
                        bad = copy.deepcopy(snapshot)
                        bad["state"]["scores"][0] += delta
                        if state["game_phase"] == "ended":
                            order = sorted(range(4), key=lambda s: (-bad["state"]["scores"][s], s))
                            bad["state"]["final_rankings"] = [order.index(s) + 1 for s in range(4)]
                        with self.assertRaisesRegex(validator.ArtifactError, "conserve"):
                            validator._check_snapshot(bad, rules=rules)
                        with self.assertRaisesRegex(GameError, "conserve"):
                            game.restore(bad["state"])
                        self.assertEqual(vars(game), before)
                        receiver = self.receiver(welcome)
                        with self.assertRaises(SessionError) as error:
                            receiver.receive(self.raw(bad))
                        self.assertEqual(error.exception.code, "invalid_message")
                        self.assertEqual((receiver.applied, receiver.game.game_phase), (0, "not_started"))

    def test_host_direction_precedes_duplicate_or_snapshot_floor(self):
        trace = VECTORS["V104_wire_complete_game"]["positive"]["trace"]
        for covered in (False, True):
            for kind in ("action", "join", "future_kind", None, []):
                with self.subTest(covered=covered, kind=kind):
                    welcome = copy.deepcopy(trace["welcome"])
                    if covered:
                        welcome.update(resumed=True, replay_from_seq=1, replay_through_seq=4)
                    receiver = self.receiver(welcome)
                    initial = VECTORS["V18_snapshot_state"]["positive"] if covered else trace["steps"][0]["message"]
                    receiver.receive(self.raw(initial))
                    bad = copy.deepcopy(trace["steps"][0]["message"])
                    bad["kind"] = kind
                    with self.assertRaises(SessionError) as error:
                        receiver.receive(self.raw(bad))
                    self.assertEqual(error.exception.code, "invalid_message")
                    self.assertEqual(receiver.applied, initial["seq"])

    def test_visible_hand_discard_excludes_the_drawn_physical_tile(self):
        trace = VECTORS["V104_wire_complete_game"]["positive"]["trace"]
        for drawn in ("9s", "5m", "5mr"):
            for duplicate in (False, True):
                for tsumogiri in (False, True):
                    with self.subTest(drawn=drawn, duplicate=duplicate, tsumogiri=tsumogiri):
                        events = [copy.deepcopy(s["message"]["event"]) for s in trace["steps"][:3]]
                        # Include an ordinary five when drawing a red five:
                        # it is not the same physical tile for tsumogiri.
                        events[0]["rules"]["red_fives"]["m"] = 2
                        hand = ["1m", "2m", "3m", "4m", "6m", "7m", "8m", "9m", "1p", "2p", "3p", "E", "5m"]
                        if drawn == "5m":
                            hand[-1] = "5mr"
                        if duplicate:
                            hand[-2] = drawn
                        events[1]["hands"][0] = {"tiles": hand}
                        events[2]["pai"] = drawn
                        state = EventState(events[0]["rules"])
                        for event in events:
                            state.apply(event)
                        discard = {"type": "dahai", "actor": 0, "pai": drawn, "tsumogiri": tsumogiri}
                        if tsumogiri or duplicate:
                            state.apply(discard)
                            self.assertEqual(len(state.round["hands"][0]["tiles"]), 13)
                        else:
                            before = copy.deepcopy(vars(state))
                            with self.assertRaisesRegex(GameError, "drawn tile"):
                                state.apply(discard)
                            self.assertEqual(vars(state), before)

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

    def _dealt(self, rules=None):
        state = EventState(rules or copy.deepcopy(SCORING["rules"]))
        state.apply({"type": "start_game", "scores": [25000] * 4, "rules": state.rules})
        state.apply({"type": "start_kyoku", "bakaze": "E", "kyoku": 1, "oya": 0, "honba": 0, "kyotaku": 0,
                     "extension_round": 0, "scores": [25000] * 4, "dora_marker": "1p", "hands": [{"count": 13}] * 4})
        return state

    @staticmethod
    def _open_reaction(state):
        state.apply({"type": "tsumo", "actor": 0, "pai": None})
        state.apply({"type": "dahai", "actor": 0, "pai": "9s", "tsumogiri": True})

    @staticmethod
    def _win(**overrides):
        win = {"actor": 1, "target": 0, "pai": "9s", "fu": 30, "han": 1,
               "yakus": [{"id": "riichi", "value": 1, "unit": "han"}], "bonuses": [],
               "hand_points": 1000, "deltas": [-1000, 1000, 0, 0], "ura_dora_markers": [], "pao": []}
        win.update(overrides)
        return win

    @staticmethod
    def _end_kyoku(result, deltas, scores, nxt=None):
        nxt = nxt or {"type": "rotate", "bakaze": "E", "kyoku": 2, "oya": 1, "honba": 0,
                      "kyotaku": 0, "extension_round": 0}
        return {"type": "end_kyoku", "result": result, "deltas": deltas, "scores": scores, "next": nxt}

    def test_fanpai_requires_canonical_noten_deltas(self):
        result = {"type": "ryukyoku", "reason": "fanpai", "tenpai": [True, False, False, False]}
        renchan = {"type": "renchan", "bakaze": "E", "kyoku": 1, "oya": 0, "honba": 1,
                   "kyotaku": 0, "extension_round": 0}
        state = self._dealt()
        self._open_reaction(state)
        state.round["wall_remaining"] = 0
        with self.assertRaises(GameError):
            state.apply(self._end_kyoku(result, [2000, -700, -700, -600], [27000, 24300, 24300, 24400], renchan))
        state = self._dealt()
        self._open_reaction(state)
        state.round["wall_remaining"] = 0
        state.apply(self._end_kyoku(result, [3000, -1000, -1000, -1000], [28000, 24000, 24000, 24000], renchan))

    def test_fanpai_tenpai_must_match_visible_hands(self):
        hand = [{"tiles": ["1m", "2m", "3m", "4m", "5m", "6m", "7m", "8m", "9m", "1p", "2p", "3p", "P"]},
                {"count": 13}, {"count": 13}, {"count": 13}]
        def dealt():
            rules = copy.deepcopy(SCORING["rules"])
            state = EventState(rules)
            state.apply({"type": "start_game", "scores": [25000] * 4, "rules": rules})
            state.apply({"type": "start_kyoku", "bakaze": "E", "kyoku": 1, "oya": 0, "honba": 0, "kyotaku": 0,
                         "extension_round": 0, "scores": [25000] * 4, "dora_marker": "1p",
                         "hands": copy.deepcopy(hand)})
            state.apply({"type": "tsumo", "actor": 0, "pai": "P"})
            state.apply({"type": "dahai", "actor": 0, "pai": "P", "tsumogiri": True})
            state.round["wall_remaining"] = 0
            return state
        result = {"type": "ryukyoku", "reason": "fanpai", "tenpai": [False, False, False, False]}
        with self.assertRaises(GameError):
            dealt().apply(self._end_kyoku(result, [0, 0, 0, 0], [25000] * 4,
                                          {"type": "rotate", "bakaze": "E", "kyoku": 2, "oya": 1, "honba": 0,
                                           "kyotaku": 0, "extension_round": 0}))
        result["tenpai"] = [True, False, False, False]
        dealt().apply(self._end_kyoku(result, [3000, -1000, -1000, -1000], [28000, 24000, 24000, 24000],
                                      {"type": "renchan", "bakaze": "E", "kyoku": 1, "oya": 0, "honba": 1,
                                       "kyotaku": 0, "extension_round": 0}))

    def test_chombo_payments_must_follow_distribution(self):
        renchan = {"type": "renchan", "bakaze": "E", "kyoku": 1, "oya": 0, "honba": 0,
                   "kyotaku": 0, "extension_round": 0}
        def penalty(payments, deltas, scores):
            result = {"type": "penalty", "offender": 0, "reason": "illegal_action",
                      "penalty": {"payments": payments}}
            return self._end_kyoku(result, deltas, scores, renchan)
        rules = copy.deepcopy(SCORING["rules"])
        rules["invalid_action_policy"] = "chombo"
        state = self._dealt(rules)
        self._open_reaction(state)
        wrong = [{"from": 0, "to": 1, "points": 2700}, {"from": 0, "to": 2, "points": 2700},
                 {"from": 0, "to": 3, "points": 2600}]
        with self.assertRaises(GameError):
            state.apply(penalty(wrong, [-8000, 2700, 2700, 2600], [17000, 27700, 27700, 27600]))
        state = self._dealt(rules)
        self._open_reaction(state)
        duplicated = [{"from": 0, "to": 1, "points": 2800}, {"from": 0, "to": 1, "points": 2600},
                      {"from": 0, "to": 3, "points": 2600}]
        with self.assertRaises(GameError):
            state.apply(penalty(duplicated, [-8000, 5400, 0, 2600], [17000, 30400, 25000, 27600]))
        state = self._dealt(rules)
        self._open_reaction(state)
        canonical = [{"from": 0, "to": 1, "points": 2800}, {"from": 0, "to": 2, "points": 2600},
                     {"from": 0, "to": 3, "points": 2600}]
        state.apply(penalty(canonical, [-8000, 2800, 2600, 2600], [17000, 27800, 27600, 27600]))

    def test_hora_deltas_must_aggregate_wins(self):
        state = self._dealt()
        self._open_reaction(state)
        with self.assertRaises(GameError):
            state.apply(self._end_kyoku({"type": "hora", "wins": [self._win()]},
                                        [-1000, 999, 0, 1], [24000, 25999, 25000, 25001]))
        state = self._dealt()
        self._open_reaction(state)
        state.apply(self._end_kyoku({"type": "hora", "wins": [self._win()]},
                                    [-1000, 1000, 0, 0], [24000, 26000, 25000, 25000]))

    def test_hora_must_stay_inside_its_decision_window(self):
        state = self._dealt()
        self._open_reaction(state)
        state.round["turn"]["phase"] = "awaiting_draw"
        with self.assertRaises(GameError):
            state.apply(self._end_kyoku({"type": "hora", "wins": [self._win()]},
                                        [-1000, 1000, 0, 0], [24000, 26000, 25000, 25000]))

    def test_ron_after_reach_accepted_is_rejected(self):
        state = self._dealt()
        state.apply({"type": "tsumo", "actor": 0, "pai": None})
        state.apply({"type": "reach", "actor": 0})
        state.apply({"type": "dahai", "actor": 0, "pai": "9s", "tsumogiri": True})
        state.apply({"type": "reach_accepted", "actor": 0, "deltas": [-1000, 0, 0, 0],
                     "scores": [24000, 25000, 25000, 25000], "kyotaku": 1})
        with self.assertRaises(GameError):
            state.apply(self._end_kyoku({"type": "hora", "wins": [self._win(deltas=[-1000, 2000, 0, 0])]},
                                        [-1000, 2000, 0, 0], [23000, 27000, 25000, 25000]))

    def test_ankan_rob_follows_ankan_chankan_rule(self):
        trace = VECTORS["V242_robbed_second_kan_clears_pending"]["positive"]["trace"]
        rules = copy.deepcopy(SCORING["rules"])
        rules.update(trace["rule_overrides"])
        state = EventState(rules)
        for event in trace["input"]["events"]:
            state.apply(copy.deepcopy(event))
        events = copy.deepcopy(trace["input"]["events"])
        events[0]["rules"]["ankan_chankan"] = "never"
        state = EventState({**rules, "ankan_chankan": "never"})
        for event in events[:-1]:
            state.apply(event)
        with self.assertRaises(GameError):
            state.apply(events[-1])
        state = EventState(rules)
        events = copy.deepcopy(trace["input"]["events"])
        for event in events[:-1]:
            state.apply(event)
        bad = copy.deepcopy(events[-1])
        bad["result"]["wins"][0].update(fu=30, han=1, hand_points=1000,
                                      yakus=[{"id": "tanyao", "value": 1, "unit": "han"}])
        with self.assertRaises(GameError):
            state.apply(bad)

    def test_ura_markers_follow_reach_acceptance(self):
        state = self._dealt()
        self._open_reaction(state)
        with self.assertRaises(GameError):
            state.apply(self._end_kyoku({"type": "hora", "wins": [self._win(ura_dora_markers=["5m"])]},
                                        [-1000, 1000, 0, 0], [24000, 26000, 25000, 25000]))
        state = self._dealt()
        self._open_reaction(state)
        state.round["reach_status"][1]["state"] = "accepted"
        for markers in ([], ["5m", "6m"]):
            with self.assertRaises(GameError):
                state.apply(self._end_kyoku({"type": "hora", "wins": [self._win(ura_dora_markers=markers)]},
                                            [-1000, 1000, 0, 0], [24000, 26000, 25000, 25000]))
        state = self._dealt()
        self._open_reaction(state)
        state.round["reach_status"][1]["state"] = "accepted"
        state.apply(self._end_kyoku({"type": "hora", "wins": [self._win(ura_dora_markers=["5m"])]},
                                    [-1000, 1000, 0, 0], [24000, 26000, 25000, 25000]))

    def _reach_window_snapshot(self):
        snapshot = copy.deepcopy(VECTORS["V18_snapshot_state"]["positive"])
        kyoku = snapshot["state"]["kyoku"]
        kyoku["turn"].update(phase="awaiting_responses",
                             last_event={"type": "dahai", "actor": 0, "pai": "9s", "tsumogiri": True})
        kyoku["hands"][0]["tiles"].remove("9s")
        kyoku["rivers"][0].append({"pai": "9s", "tsumogiri": True, "reach": True, "called_by": None})
        kyoku["reach_status"][0].update(state="declared", double=True)
        kyoku["first_turn_eligible"][0] = False
        snapshot["state"]["pending_requests"] = []
        return snapshot

    def test_snapshot_reach_status_must_follow_its_declaration_window(self):
        # A declared-but-unaccepted riichi exists only while the declaration
        # discard's own reaction window is open (§13.3, §10.4).
        validator._check_snapshot(self._reach_window_snapshot())

        def expect(pattern, mutate):
            bad = self._reach_window_snapshot()
            mutate(bad["state"]["kyoku"])
            with self.assertRaisesRegex(validator.ArtifactError, pattern):
                validator._check_snapshot(bad)

        def kan_cause(kyoku):
            kyoku["turn"]["last_event"] = {"type": "ankan_declared", "actor": 0, "consumed": ["1m"] * 4}
            kyoku["pending_kan"] = kyoku["turn"]["last_event"]
        expect("discard window", kan_cause)
        expect("unmarked", lambda k: k["rivers"][0][0].update(reach=False))
        expect("ippatsu", lambda k: k["reach_status"][0].update(ippatsu=True))
        expect("eligibility", lambda k: k["reach_status"][0].update(double=False))
        expect("precede any declaration", lambda k: k["reach_status"][1].update(double=True))
        expect("precede any declaration", lambda k: k["reach_status"][1].update(ippatsu=True))

    def _accepted_reach_snapshot(self, ippatsu_window):
        # An accepted reach can only appear at a later boundary: either the
        # declarer's next discard (tail unmarked, one-shot spent) or another
        # seat's decision while the one-shot window is still open. The
        # declaration tile keeps its reach mark in the river.
        snapshot = self._reach_window_snapshot()
        kyoku = snapshot["state"]["kyoku"]
        kyoku["reach_status"][0].update(state="accepted", ippatsu=ippatsu_window)
        kyoku["kyotaku"] = snapshot["state"]["kyotaku"] = 1
        if ippatsu_window:
            kyoku["turn"].update(actor=1, phase="awaiting_action",
                                 last_event={"type": "tsumo", "actor": 1, "pai": None})
            kyoku["hands"][1]["count"] = 14
        else:
            kyoku["turn"]["last_event"] = {"type": "dahai", "actor": 0, "pai": "5p", "tsumogiri": True}
            kyoku["rivers"][0].append({"pai": "5p", "tsumogiri": True, "reach": False, "called_by": None})
        kyoku["wall_remaining"] = 68
        return snapshot

    def test_snapshot_accepted_reach_needs_a_marked_discard(self):
        validator._check_snapshot(self._accepted_reach_snapshot(ippatsu_window=False))
        validator._check_snapshot(self._accepted_reach_snapshot(ippatsu_window=True))

        def expect(pattern, mutate, ippatsu_window=False):
            bad = self._accepted_reach_snapshot(ippatsu_window)
            mutate(bad["state"]["kyoku"])
            with self.assertRaisesRegex(validator.ArtifactError, pattern):
                validator._check_snapshot(bad)

        expect("marked discard", lambda k: k["rivers"][0][0].update(reach=False))
        expect("eligibility", lambda k: k["reach_status"][0].update(double=False))
        expect("post-reach discard", lambda k: k["reach_status"][0].update(ippatsu=True))
        expect("river tail", lambda k: k["rivers"][0][-1].update(pai="8p"))
        expect("deposit", lambda k: k.update(kyotaku=0))
        expect("open reach window", lambda k: k["reach_status"][0].update(ippatsu=False), ippatsu_window=True)

    def _called_meld_snapshot(self):
        # Seat 1 called pon on seat 0's "E" earlier; the current cause is
        # seat 0's fresh tsumogiri discard, so the called tile sits inside
        # seat 0's river rather than at its tail.
        snapshot = copy.deepcopy(VECTORS["V18_snapshot_state"]["positive"])
        kyoku = snapshot["state"]["kyoku"]
        kyoku["hands"][0]["tiles"].remove("E")
        kyoku["hands"][1] = {"count": 10}
        kyoku["rivers"][0] = [{"pai": "E", "tsumogiri": False, "reach": False, "called_by": 1},
                              {"pai": "5p", "tsumogiri": True, "reach": False, "called_by": None}]
        kyoku["rivers"][1] = [{"pai": "9m", "tsumogiri": False, "reach": False, "called_by": None}]
        kyoku["melds"][1] = [{"type": "pon", "actor": 1, "target": 0, "pai": "E", "consumed": ["E", "E"]}]
        kyoku["turn"].update(actor=0, phase="awaiting_responses",
                             last_event={"type": "dahai", "actor": 0, "pai": "5p", "tsumogiri": True})
        kyoku["first_turn_eligible"] = [False] * 4
        kyoku["wall_remaining"] = 68
        snapshot["state"]["pending_requests"] = []
        return snapshot

    def test_snapshot_called_tile_links_river_and_meld(self):
        # A called discard stays in the discarder's river with `called_by`
        # set; the caller's meld must name the same tile and target seat.
        validator._check_snapshot(self._called_meld_snapshot())

        def expect(pattern, mutate):
            bad = self._called_meld_snapshot()
            mutate(bad["state"]["kyoku"])
            with self.assertRaisesRegex(validator.ArtifactError, pattern):
                validator._check_snapshot(bad)

        expect("matching meld", lambda k: k["rivers"][0][0].update(called_by=2))
        expect("matching meld|lacks its river", lambda k: k["melds"][1][0].update(pai="P"))
        def phantom_meld(kyoku):
            kyoku["melds"][1].append({"type": "pon", "actor": 1, "target": 2,
                                      "pai": "P", "consumed": ["P", "P"]})
            kyoku["hands"][1] = {"count": 7}
        expect("lacks its river", phantom_meld)

        def bad_target(kyoku):
            kyoku["melds"][1][0]["target"] = 1            # self-call: out of range
            kyoku["rivers"][0][0]["called_by"] = None     # uncall so the river link passes
        expect("target seat", bad_target)
        expect("meld geometry", lambda k: k["melds"][1][0].update(consumed=["E", "F"]))

    def test_snapshot_meld_geometry_and_tile_inventory(self):
        # A phantom snapshot could carry a geometrically impossible meld or
        # five copies of a tile while the plain 136-count still balances.
        def expect(pattern, mutate):
            bad = self._called_meld_snapshot()
            mutate(bad["state"]["kyoku"])
            with self.assertRaisesRegex(validator.ArtifactError, pattern):
                validator._check_snapshot(bad)

        def bad_chi(kyoku):
            kyoku["melds"][1] = [{"type": "chi", "actor": 1, "target": 0, "pai": "E", "consumed": ["E", "E"]}]
        expect("chi meld geometry", bad_chi)

        def bad_ankan(kyoku):
            # Uncall the pon discard and restate it as a self-drawn quad with
            # mixed kinds: only the ankan geometry check may reject it.
            kyoku["rivers"][0][0]["called_by"] = None
            kyoku["melds"][1] = [{"type": "ankan", "actor": 1, "consumed": ["E", "E", "F", "F"]}]
            kyoku["kan_counts"][1] = 1
            kyoku["wall_remaining"] = 66
        expect("invalid ankan", bad_ankan)

        def five_of_a_kind(kyoku):
            kyoku["hands"][0]["tiles"][0] = "E"   # fourth E joins the pon triple
            kyoku["hands"][0]["tiles"][1] = "E"   # and a fifth is invented
        expect("four copies", five_of_a_kind)

        def fat_pon(kyoku):
            # Same-kind geometry still holds, but a pon cannot consume three
            # tiles; the extra "E" is balanced by shrinking the live wall.
            kyoku["melds"][1][0]["consumed"].append("E")
            kyoku["wall_remaining"] -= 1
        expect("consumed count", fat_pon)

    def test_snapshot_round_coordinates_must_be_reachable(self):
        # Round coordinates follow the absolute seat order and enter the
        # extension only past the scheduled last wind (§7.2, §13.3).
        rules = SCORING["rules"]

        def expect(pattern, mutate):
            bad = self._called_meld_snapshot()
            mutate(bad["state"]["kyoku"])
            with self.assertRaisesRegex(validator.ArtifactError, pattern):
                validator._check_snapshot(bad, rules=rules)

        expect("coordinates", lambda k: k.update(oya=1))          # E1 must deal from seat 0
        expect("coordinates", lambda k: k.update(extension_round=1))  # E1 cannot be an extension
        expect("coordinates", lambda k: k.update(bakaze="W"))     # W rounds exist only in extension
        expect("coordinates", lambda k: k.update(bakaze="W", extension_round=101))

        good = self._called_meld_snapshot()
        good["state"]["kyoku"].update(bakaze="S", kyoku=4, oya=3)
        validator._check_snapshot(good, rules=rules)   # a scheduled south round is reachable

        good = self._called_meld_snapshot()
        good["state"]["kyoku"].update(bakaze="W", kyoku=1, oya=0, extension_round=1)
        validator._check_snapshot(good, rules=rules)   # a west extension round is reachable

    def test_snapshot_next_kyoku_coordinates_must_be_reachable(self):
        rules = SCORING["rules"]
        snapshot = copy.deepcopy(VECTORS["V18_snapshot_state"]["positive"])
        state = snapshot["state"]
        state["game_phase"] = "between_kyoku"
        state["kyoku"] = None
        state["pending_requests"] = []
        state["next_kyoku"] = {"bakaze": "E", "kyoku": 2, "oya": 1, "honba": 0,
                               "kyotaku": 0, "extension_round": 0}
        validator._check_snapshot(snapshot, rules=rules)

        bad = copy.deepcopy(snapshot)
        bad["state"]["next_kyoku"]["oya"] = 0      # E2 must deal from seat 1
        with self.assertRaisesRegex(validator.ArtifactError, "next kyotaku"):
            validator._check_snapshot(bad, rules=rules)

        bad = copy.deepcopy(snapshot)
        bad["state"]["next_kyoku"].update(bakaze="W", extension_round=0)
        with self.assertRaisesRegex(validator.ArtifactError, "next kyotaku"):
            validator._check_snapshot(bad, rules=rules)

    def _public_kan_snapshot(self, called="5m", added="5mr"):
        snapshot = copy.deepcopy(VECTORS["V111_initial_observer_has_no_prior_seq"]["positive"])
        rules = copy.deepcopy(SCORING["rules"])
        state = EventState(rules)
        events = [
            {"type": "start_game", "players": snapshot["state"]["players"], "rules": rules, "scores": [25000] * 4},
            {"type": "start_kyoku", "bakaze": "E", "kyoku": 1, "oya": 0, "honba": 0, "kyotaku": 0,
             "extension_round": 0, "scores": [25000] * 4, "dora_marker": "9s", "hands": [{"count": 13} for _ in range(4)]},
            {"type": "tsumo", "actor": 0, "pai": None},
            {"type": "dahai", "actor": 0, "pai": called, "tsumogiri": False},
            {"type": "pon", "actor": 1, "target": 0, "pai": called, "consumed": ["5m", "5m"]},
            {"type": "dahai", "actor": 1, "pai": "9p", "tsumogiri": False},
            {"type": "tsumo", "actor": 2, "pai": None},
            {"type": "dahai", "actor": 2, "pai": "6p", "tsumogiri": False},
            {"type": "pon", "actor": 1, "target": 2, "pai": "6p", "consumed": ["6p", "6p"]},
            {"type": "dahai", "actor": 1, "pai": "8p", "tsumogiri": False},
        ]
        for actor, tile in [(2, "7p"), (3, "6s"), (0, "7s")]:
            events.extend([{"type": "tsumo", "actor": actor, "pai": None},
                           {"type": "dahai", "actor": actor, "pai": tile, "tsumogiri": True}])
        events.extend([
            {"type": "tsumo", "actor": 1, "pai": None},
            {"type": "kakan_declared", "actor": 1, "pai": added, "consumed": [called, "5m", "5m"]},
            {"type": "kakan", "actor": 1, "pai": added, "consumed": [called, "5m", "5m"]},
            {"type": "tsumo", "actor": 1, "pai": None},
        ])
        for event in events:
            state.apply(event)
        snapshot["state"]["kyoku"] = copy.deepcopy(state.round)
        snapshot["state"]["kyoku"]["turn"].update(last_event_seq=None, last_event=events[-1])
        return rules, state, snapshot

    def _check_public_snapshot_layers(self, rules, snapshot):
        validator._check_snapshot(snapshot, rules=rules)
        restored = EventState(rules)
        restored.restore(snapshot["state"])
        welcome = copy.deepcopy(VECTORS["V104_wire_complete_game"]["positive"]["trace"]["welcome"])
        welcome.update(mode="spectate", view="public", seat=None, capabilities=["snapshot"], rules=rules)
        welcome.pop("resume")
        receiver = self.receiver(welcome, initial_snapshot=True)
        self.assertEqual(receiver.receive(self.raw(snapshot)), "applied")
        return receiver

    def test_kakan_snapshot_preserves_original_called_tile(self):
        for called, added in [("5m", "5mr"), ("5mr", "5m")]:
            with self.subTest(called=called, added=added):
                _, state, _ = self._public_kan_snapshot(called, added)
                meld = state.round["melds"][1][0]
                self.assertEqual(meld["pai"], called)
                self.assertCountEqual(meld["consumed"], ["5m", "5m", added])
                self.assertEqual(meld["target"], 0)
                self.assertEqual(state.round["melds"][1][1]["type"], "pon")

    def test_nonfinal_kakan_snapshot_restores_and_continues(self):
        rules, _, snapshot = self._public_kan_snapshot()
        # Express the normative snapshot independently of event projection.
        snapshot["state"]["kyoku"]["melds"][1][0].update(pai="5m", consumed=["5m", "5m", "5mr"])
        receiver = self._check_public_snapshot_layers(rules, snapshot)
        for seq, event in enumerate([
            {"type": "dora", "dora_marker": "1p"},
            {"type": "dahai", "actor": 1, "pai": "8s", "tsumogiri": True},
        ], 2):
            message = {k: snapshot[k] for k in ("yamai", "session_id", "game_id")}
            message.update(kind="event", seq=seq, event=event)
            self.assertEqual(receiver.receive(self.raw(message)), "applied")
        self.assertEqual(receiver.game.round["turn"]["phase"], "awaiting_responses")

    def test_extension_wrap_snapshots_are_restorable_in_all_layers(self):
        for length, extension in [("tonnan", 9), ("tonpu", 13)]:
            with self.subTest(length=length):
                rules = copy.deepcopy(SCORING["rules"])
                rules.update(game_length=length, extension={"mode": "sudden_death", "target_points": 30000, "max_extra_rounds": 100})
                snapshot = copy.deepcopy(VECTORS["V111_initial_observer_has_no_prior_seq"]["positive"])
                snapshot["state"]["kyoku"].update(bakaze="E", extension_round=extension)
                self._check_public_snapshot_layers(rules, snapshot)
                # The same coordinates must also survive a between-round checkpoint.
                snapshot["state"].update(game_phase="between_kyoku", kyoku=None,
                    next_kyoku={"bakaze": "E", "kyoku": 1, "oya": 0, "honba": 0,
                                "kyotaku": 0, "extension_round": extension})
                self._check_public_snapshot_layers(rules, snapshot)

    def test_extension_snapshot_cannot_rotate_faster_than_deal_counter(self):
        rules = copy.deepcopy(SCORING["rules"])
        rules["extension"]["max_extra_rounds"] = 100
        for wind, kyoku, extra in [("W", 4, 1), ("N", 1, 4), ("E", 1, 8)]:
            snapshot = copy.deepcopy(VECTORS["V111_initial_observer_has_no_prior_seq"]["positive"])
            snapshot["state"]["kyoku"].update(bakaze=wind, kyoku=kyoku, oya=kyoku - 1, extension_round=extra)
            with self.subTest(wind=wind, kyoku=kyoku, extra=extra):
                with self.assertRaisesRegex(validator.ArtifactError, "coordinates"):
                    validator._check_snapshot(snapshot, rules=rules)
                with self.assertRaisesRegex(GameError, "coordinates"):
                    EventState(rules).restore(snapshot["state"])

    def test_snapshot_self_furiten_follows_public_state(self):
        # riichi_furiten implies an accepted declaration, and a seat's own
        # draw clears its temporary furiten (§10.4).
        snapshot = self._accepted_reach_snapshot(ippatsu_window=True)
        snapshot["state"]["kyoku"]["self_state"]["riichi_furiten"] = True
        validator._check_snapshot(snapshot)   # accepted reach + riichi furiten: consistent

        snapshot = self._reach_window_snapshot()
        snapshot["state"]["kyoku"]["self_state"]["temporary_furiten"] = True
        validator._check_snapshot(snapshot)   # seat 0 just discarded: flag may persist

        def expect(pattern, mutate):
            bad = copy.deepcopy(VECTORS["V18_snapshot_state"]["positive"])
            mutate(bad["state"]["kyoku"])
            with self.assertRaisesRegex(validator.ArtifactError, pattern):
                validator._check_snapshot(bad)

        expect("temporary furiten", lambda k: k["self_state"].update(temporary_furiten=True))
        expect("riichi furiten", lambda k: k["self_state"].update(riichi_furiten=True))

    def test_snapshot_phantom_reach_wedges_the_receiver(self):
        # Before the window check pinned the cause, a snapshot could carry a
        # declared riichi next to an unrelated cause; every later draw would
        # then be rejected as "draw before reach acceptance". The receiver
        # must reject it at restore time instead.
        trace = VECTORS["V104_wire_complete_game"]["positive"]["trace"]
        receiver = self.receiver(trace["welcome"])
        for step in trace["steps"][:4]:
            receiver.receive(self.raw(step["message"]))
        receiver.receive(self.raw(self._reach_window_snapshot()))
        self.assertEqual(receiver.game.round["reach_status"][0]["state"], "declared")
        receiver = self.receiver(trace["welcome"])
        for step in trace["steps"][:4]:
            receiver.receive(self.raw(step["message"]))
        snapshot = self._reach_window_snapshot()
        kyoku = snapshot["state"]["kyoku"]
        kyoku["turn"]["last_event"] = {"type": "ankan_declared", "actor": 0, "consumed": ["1m"] * 4}
        kyoku["pending_kan"] = kyoku["turn"]["last_event"]
        kyoku["hands"][0]["tiles"].insert(0, "1m")  # the pending quad still belongs to the hand
        kyoku["wall_remaining"] = 68
        with self.assertRaisesRegex(SessionError, "discard window"):
            receiver.receive(self.raw(snapshot))


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
