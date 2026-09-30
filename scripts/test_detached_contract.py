"""Sequential decisions and exact clock boundaries after permanent seat closure."""
from copy import deepcopy
import unittest

from detached_contract import DetachedTable


def request(seat, rid, timeout=2, reaction=False):
    actions = [{"action_id": "default", "action": {"type": "none" if reaction else "dahai", "actor": seat}}]
    actions.append({"action_id": "chosen", "action": {"type": "hora" if reaction else "dahai", "actor": seat}})
    return {"request_id": rid, "seat": seat, "timeout_ms": timeout,
            "legal_actions": actions, "default_action_id": "default"}


class DetachedContractTests(unittest.TestCase):
    def table(self, **kwargs):
        return DetachedTable(grace_ms=1, bank_ms=5, **kwargs)

    def group(self, table, prefix="a", at=0):
        table.issue([request(s, prefix + str(s), reaction=True) for s in (1, 2, 3)], target=0, at_us=at)

    def ready(self, table, seats, at=0):
        for seat in seats:
            table.prepare(seat, enqueue_ready=True, at_us=at)

    def test_close_open_preserves_deadline_and_future_turns_use_remaining_bank(self):
        t = self.table()
        t.issue([request(1, "a")], target=1, at_us=0)
        self.ready(t, [1])
        t.fatal_close(1, at_us=4000)
        frozen = deepcopy(t.ledger[1])
        self.assertEqual(t.banks[1], 5)
        t.advance(7999)
        self.assertEqual(t.decision["result"]["requests"]["a"]["phase"], "OPEN")
        self.assertEqual(t.submit(1, "a", "chosen", at_us=8000), "ignored")
        self.assertEqual(t.last_result["requests"]["a"]["elapsed_ms"], 8)
        self.assertEqual(t.last_result["requests"]["a"]["source"], "default")
        self.assertEqual(t.banks[1], 0)
        t.issue([request(1, "b")], target=1, at_us=10000)
        self.assertEqual(t.decision["start_us"], 10000)
        self.assertEqual(t.decision["requests"][0]["time_bank_ms"], 0)
        t.advance(12999)
        self.assertIsNotNone(t.decision)
        t.advance(13000)
        self.assertEqual(t.last_result["requests"]["b"]["elapsed_ms"], 3)
        self.assertEqual(t.ledger[1], frozen)
        self.assertEqual(len(t.effects), 2)
        self.assertTrue(all(len(t.ledger[s]) == 2 for s in (0, 2, 3)))

    def test_close_selected_keeps_explicit_choice_and_frozen_clock(self):
        t = self.table()
        self.group(t)
        self.ready(t, [1, 2, 3])
        t.submit(1, "a1", "chosen", at_us=4999)
        chosen = deepcopy(t.decision["result"]["requests"]["a1"])
        self.assertEqual(chosen["elapsed_ms"], 4)
        self.assertEqual(t.banks[1], 4)
        t.fatal_close(1, at_us=5000)
        frozen = deepcopy(t.ledger[1])
        t.submit(1, "a1", "default", at_us=5001)
        t.advance(8000)
        final = t.last_result["requests"]["a1"]
        for key in ("action_id", "source", "elapsed_ms", "time_bank_ms"):
            self.assertEqual(final[key], chosen[key])
        self.assertEqual(final["status"], "accepted")
        self.assertEqual(t.effects[-1], {"type": "hora", "actors": [1]})
        self.assertEqual(t.ledger[1], frozen)
        self.group(t, "b", at=9000)
        self.ready(t, [2, 3], at=9000)
        self.assertEqual(t.decision["requests"][0]["time_bank_ms"], 4)
        t.advance(15999)
        self.assertEqual(t.decision["result"]["requests"]["b1"]["phase"], "OPEN")
        t.advance(16000)
        self.assertEqual(t.last_result["requests"]["b1"]["source"], "default")
        t.advance(17000)
        self.assertEqual(t.last_result["phase"], "TERMINAL")
        self.assertEqual(t.ledger[1], frozen)

    def test_closure_during_preparation_does_not_wait_for_closed_queue(self):
        t = self.table()
        self.group(t)
        self.assertFalse(t.prepare(1, enqueue_ready=False, at_us=0))
        self.ready(t, [2, 3], at=1000)
        self.assertIsNone(t.decision["start_us"])
        t.fatal_close(1, at_us=5000)
        self.assertEqual(t.decision["start_us"], 5000)
        self.assertEqual(t.ledger[1], [])
        for seat in (2, 3):
            descriptor = t.ledger[seat][0]["decision_group_members"]
            self.assertEqual(descriptor, [{"request_id": "a" + str(s), "seat": s} for s in (1, 2, 3)])
        t.advance(12999)
        self.assertIsNotNone(t.decision)
        t.advance(13000)
        self.assertEqual(set(t.last_result["requests"]), {"a1", "a2", "a3"})

    def test_closed_seat_internal_readiness_does_not_bypass_live_admission(self):
        t = self.table()
        t.fatal_close(1, at_us=0)
        self.group(t)
        self.assertTrue(t.prepare(1, enqueue_ready=False, at_us=0))
        self.assertFalse(t.prepare(2, enqueue_ready=False, at_us=0))
        self.ready(t, [3])
        self.assertIsNone(t.decision["start_us"])
        self.ready(t, [2], at=5000)
        self.assertEqual(t.decision["start_us"], 5000)
        self.assertEqual(t.ledger[1], [])

    def test_close_after_own_preparation_keeps_committed_request(self):
        t = self.table()
        self.group(t)
        self.ready(t, [1])
        self.assertEqual([m["kind"] for m in t.ledger[1]], ["request"])
        t.fatal_close(1, at_us=1000)
        frozen = deepcopy(t.ledger[1])
        self.ready(t, [2, 3], at=5000)
        self.assertEqual(t.decision["start_us"], 5000)
        t.advance(13000)
        self.assertEqual(t.ledger[1], frozen)
        self.assertEqual(t.last_result["requests"]["a1"]["elapsed_ms"], 8)

    def test_ordinary_disconnect_is_resumable_and_keeps_ledger(self):
        t = self.table()
        t.disconnect(1, at_us=0)
        t.issue([request(1, "a")], target=1, at_us=0)
        self.assertFalse(t.prepare(1, enqueue_ready=False, at_us=0))
        self.ready(t, [1], at=1000)
        self.assertEqual(t.submit(1, "a", "chosen", at_us=2000), "ignored")
        self.assertTrue(t.resume(1, at_us=2000))
        t.submit(1, "a", "chosen", at_us=3000)
        self.assertEqual(t.last_result["requests"]["a"]["source"], "user")
        self.assertEqual([m["kind"] for m in t.ledger[1]], ["request", "ack", "effect"])

    def test_fatal_token_never_resurrects_and_seat_stays_reserved(self):
        t = self.table()
        t.fatal_close(1, at_us=0)
        self.assertFalse(t.resume(1, at_us=1))
        self.assertFalse(t.can_replace(1))
        self.assertEqual(t.submit(1, "invented", "chosen", at_us=2), "ignored")
        self.assertEqual(t.ledger[1], [])
        self.assertEqual(t.used_requests, set())
        t.end_game(at_us=3)
        self.assertTrue(t.can_replace(1))
        self.assertFalse(t.resume(1, at_us=4))

    def test_only_normal_kyoku_boundary_replenishes_kyoku_bank(self):
        for scope in ("game", "kyoku"):
            t = self.table(bank_scope=scope)
            t.fatal_close(1, at_us=0)
            t.issue([request(1, "a")], target=1, at_us=0)
            t.advance(8000)
            self.assertEqual(t.banks[1], 0)
            t.start_kyoku(at_us=9000)
            self.assertEqual(t.banks[1], 5 if scope == "kyoku" else 0)
            self.assertFalse(t.resume(1, at_us=9000))

    def test_detached_zero_budget_defaults_exactly_at_start(self):
        t = DetachedTable(grace_ms=0, bank_ms=0)
        t.fatal_close(1, at_us=0)
        t.issue([request(1, "a", timeout=0)], target=1, at_us=1000)
        self.assertIsNone(t.decision)
        self.assertEqual(t.last_result["requests"]["a"]["elapsed_ms"], 0)
        self.assertEqual(t.ledger[1], [])

    def test_closure_at_deadline_runs_timer_without_new_closed_wire(self):
        t = self.table()
        t.issue([request(1, "a")], target=1, at_us=0)
        self.ready(t, [1])
        frozen = deepcopy(t.ledger[1])
        t.fatal_close(1, at_us=8000)
        self.assertIsNone(t.decision)
        self.assertEqual(t.last_result["requests"]["a"]["source"], "default")
        self.assertEqual(t.last_result["requests"]["a"]["elapsed_ms"], 8)
        self.assertEqual(t.ledger[1], frozen)

    def test_all_closed_seats_progress_without_any_wire_or_live_queue(self):
        t = self.table()
        for seat in range(4):
            t.fatal_close(seat, at_us=0)
        frozen = deepcopy(t.ledger)
        self.group(t)
        self.assertEqual(t.decision["start_us"], 0)
        t.advance(8000)
        self.assertEqual(t.last_result["phase"], "TERMINAL")
        t.issue([request(1, "solo")], target=1, at_us=9000)
        t.advance(12000)
        self.assertEqual(t.last_result["requests"]["solo"]["elapsed_ms"], 3)
        t.issue([request(seat, "next" + str(seat), reaction=True) for seat in (0, 2, 3)],
                target=1, at_us=13000)
        t.advance(16000)
        self.assertIsNotNone(t.decision)  # Seat0 still has its original bank.
        t.advance(21000)
        self.assertEqual(t.last_result["phase"], "TERMINAL")
        self.assertEqual(t.ledger, frozen)
        self.assertEqual(len(t.effects), 3)
        t.end_game(at_us=21000)
        self.assertTrue(t.can_replace(1))  # Only a new game may assign the seat.
        self.assertFalse(t.resume(1, at_us=21000))

    def test_generated_group_deadline_limit_is_atomic(self):
        for timeout, allowed in ((0, True), (1, False)):
            t = DetachedTable(grace_ms=600000, bank_ms=600000)
            requests = [request(s, str(s), timeout=timeout, reaction=True) for s in (1, 2, 3)]
            if allowed:
                t.issue(requests, target=0, at_us=0)
                self.assertEqual(t.decision["requests"][0]["decision_group_deadline_ms"], 1200000)
            else:
                with self.assertRaises(ValueError):
                    t.issue(requests, target=0, at_us=0)
                self.assertIsNone(t.decision)
                self.assertEqual(t.used_requests, set())
                self.assertEqual(t.next_group, 1)
                self.assertTrue(all(not messages for messages in t.ledger.values()))

    def test_generated_group_id_stays_bounded_for_long_request_ids(self):
        t = self.table()
        requests = [request(s, str(s) * 64, reaction=True) for s in (1, 2, 3)]
        t.issue(requests, target=0, at_us=0)
        ids = {r["decision_group_id"] for r in t.decision["requests"]}
        self.assertEqual(len(ids), 1)
        self.assertLessEqual(len(next(iter(ids))), 64)
        self.ready(t, [1, 2, 3])
        t.advance(8000)
        self.group(t, prefix="next", at=9000)
        self.assertNotIn(t.decision["requests"][0]["decision_group_id"], ids)

    def test_live_exact_deadline_gets_late_stale_after_effect(self):
        t = self.table()
        t.issue([request(1, "a")], target=1, at_us=0)
        self.ready(t, [1])
        self.assertEqual(t.submit(1, "a", "chosen", at_us=8000), "stale")
        self.assertEqual([m["kind"] for m in t.ledger[1]], ["request", "ack", "effect", "ack"])
        self.assertEqual(t.ledger[1][-1]["status"], "stale")
        self.assertEqual(t.ledger[1][-1]["elapsed_ms"], 8)
        self.assertEqual(t.ledger[1][-1]["time_bank_ms"], 0)
        frozen = deepcopy((t.banks, t.effects, t.ledger))
        self.assertEqual(t.submit(1, "a", "chosen", at_us=8001), "ignored")
        self.assertEqual((t.banks, t.effects, t.ledger), frozen)
        t.issue([request(1, "b")], target=1, at_us=9000)
        self.ready(t, [1], at=9000)
        before = deepcopy((t.banks, t.effects, t.decision["result"]))
        self.assertEqual(t.submit(1, "a", "other", at_us=9001), "stale")
        self.assertEqual((t.banks, t.effects, t.decision["result"]), before)
        t.fatal_close(1, at_us=9002)
        ledger = deepcopy(t.ledger[1])
        self.assertEqual(t.submit(1, "a", "third", at_us=9003), "ignored")
        self.assertEqual(t.ledger[1], ledger)

    def test_explicit_terminal_retry_is_silent_or_conflict_without_effects(self):
        t = self.table()
        t.issue([request(1, "a")], target=1, at_us=0)
        self.ready(t, [1])
        t.submit(1, "a", "chosen", at_us=1000)
        original = deepcopy((t.banks, t.effects, t.ledger))
        self.assertEqual(t.submit(1, "a", "chosen", at_us=1001), "ignored")
        self.assertEqual((t.banks, t.effects, t.ledger), original)
        self.assertEqual(t.submit(1, "a", "default", at_us=1002), "request_conflict")
        self.assertEqual(t.ledger[1][-1]["original_status"], "accepted")
        self.assertEqual((t.banks, t.effects), original[:2])
        self.assertEqual(t.submit(2, "a", "chosen", at_us=1003), "invalid_action")
        self.assertEqual(t.submit(1, "unknown", "chosen", at_us=1004), "invalid_action")
        self.assertEqual(set(t.terminal_requests), {"a"})
        self.assertEqual(t.used_requests, {"a"})
        t.end_game(at_us=1005)
        ledger = deepcopy(t.ledger)
        self.assertEqual(t.submit(1, "a", "default", at_us=1006), "ignored")
        self.assertEqual(t.ledger, ledger)

    def test_late_attempt_during_group_wait_is_not_reissued_after_resolution(self):
        t = self.table()
        t.issue([request(1, "a1", timeout=0, reaction=True),
                 request(2, "a2", reaction=True), request(3, "a3", reaction=True)], target=0, at_us=0)
        self.ready(t, [1, 2, 3])
        t.submit(1, "a1", "chosen", at_us=6000)
        t.advance(8000)
        self.assertEqual([m["kind"] for m in t.ledger[1]], ["request", "ack", "effect", "ack"])
        self.assertEqual([m["status"] for m in t.ledger[1] if m["kind"] == "ack"], ["defaulted", "stale"])
        ledger = deepcopy(t.ledger[1])
        self.assertEqual(t.submit(1, "a1", "chosen", at_us=8001), "ignored")
        self.assertEqual(t.ledger[1], ledger)

    def test_cancelled_terminal_late_action_keeps_original_clock(self):
        t = self.table(invalid_action_policy="chombo")
        t.issue([request(1, "a")], target=1, at_us=0)
        self.ready(t, [1])
        t.submit(1, "a", "bad", at_us=4000)
        self.assertEqual([m["kind"] for m in t.ledger[1]], ["request", "ack", "ack", "effect"])
        self.assertEqual([m["status"] for m in t.ledger[1] if m["kind"] == "ack"], ["rejected", "stale"])
        self.assertEqual(t.ledger[1][-1]["type"], "penalty")
        before = deepcopy((t.banks, t.effects))
        self.assertEqual(t.submit(1, "a", "chosen", at_us=5000), "stale")
        self.assertEqual(t.ledger[1][-1]["elapsed_ms"], 4)
        self.assertEqual(t.ledger[1][-1]["time_bank_ms"], 4)
        self.assertEqual((t.banks, t.effects), before)

    def test_queued_late_stale_follows_group_effect_with_closed_member(self):
        t = self.table()
        t.fatal_close(3, at_us=0)
        t.issue([request(1, "a1", timeout=1, reaction=True),
                 request(2, "a2", timeout=3, reaction=True),
                 request(3, "a3", timeout=3, reaction=True)], target=0, at_us=0)
        self.ready(t, [1, 2])
        t.submit(1, "a1", "chosen", at_us=7500)
        self.assertEqual([m["kind"] for m in t.ledger[1]], ["request"])
        t.advance(9000)
        self.assertEqual([m["kind"] for m in t.ledger[1]], ["request", "ack", "effect", "ack"])
        self.assertEqual([m["status"] for m in t.ledger[1] if m["kind"] == "ack"], ["defaulted", "stale"])
        self.assertEqual(t.ledger[1][-1]["elapsed_ms"], 7)
        self.assertEqual(t.ledger[3], [])

    def test_reaction_cannot_drop_closed_seat_from_members(self):
        t = self.table()
        t.fatal_close(1, at_us=0)
        with self.assertRaises(ValueError):
            t.issue([request(s, str(s), reaction=True) for s in (2, 3)], target=0, at_us=0)
        self.assertEqual(t.used_requests, set())


if __name__ == "__main__":
    unittest.main()
