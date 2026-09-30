"""Session budgets remain bounded even when transport output is drained."""
import unittest
from resource_contract import SessionBudget, SessionLimits
from session_contract import SessionError


class SessionBudgetTests(unittest.TestCase):
    def test_limits_reject_nonfinite_or_unbounded_configuration(self):
        for value in (0, -1, True, 1.5, 2**53):
            with self.subTest(value=value), self.assertRaises(ValueError):
                SessionLimits(control_messages=value)

    def test_exact_limits_and_atomic_overflow(self):
        budget = SessionBudget(SessionLimits(2, 8, 12, 16))
        budget.charge(control_messages=2, control_bytes=8, ledger_bytes=12, replay_bytes=16)
        before = budget.used.copy()
        with self.assertRaises(SessionError) as caught:
            budget.charge(control_messages=1, ledger_bytes=1)
        self.assertEqual(caught.exception.code, 'resource_limit')
        self.assertEqual(budget.used, before)
        self.assertTrue(budget.closed)
        with self.assertRaises(SessionError):
            budget.charge()

    def test_late_ids_and_fully_drained_ack_cannot_reset_budget(self):
        budget = SessionBudget(SessionLimits(128, 100_000, 100_000, 100_000))
        attempts = set()
        for index in range(129):
            try:
                budget.charge(control_messages=1, control_bytes=160, ledger_bytes=180)
            except SessionError:
                break
            # Host registers the distinct attempt only after the atomic charge.
            attempts.add(f'late-{index}')
            # Delivery/drain does not refund session accounting.
        self.assertEqual(len(attempts), 128)
        self.assertNotIn('late-128', attempts)
        self.assertTrue(budget.closed)

    def test_duplicate_input_charged_even_without_new_ack(self):
        budget = SessionBudget(SessionLimits(2, 1000, 1000, 1000))
        for _ in range(2):
            budget.charge(control_messages=1, control_bytes=100)
        with self.assertRaises(SessionError):
            budget.charge(control_messages=1, control_bytes=100)
        self.assertEqual(budget.used['ledger_bytes'], 0)

    def test_each_byte_dimension_is_independently_enforced(self):
        for name in ('control_bytes', 'ledger_bytes', 'replay_bytes'):
            with self.subTest(name=name):
                budget = SessionBudget(SessionLimits(10, 20, 30, 40))
                limit = getattr(budget.limits, name)
                budget.charge(**{name: limit})
                with self.assertRaises(SessionError):
                    budget.charge(**{name: 1})
                self.assertEqual(budget.used[name], limit)

    def test_replay_does_not_duplicate_ledger_but_cannot_run_forever(self):
        budget = SessionBudget(SessionLimits(10, 1000, 1000, 300))
        budget.charge(ledger_bytes=100)
        for _ in range(3):
            budget.charge(control_messages=1, control_bytes=10, replay_bytes=100)
        with self.assertRaises(SessionError):
            budget.charge(control_messages=1, control_bytes=10, replay_bytes=100)
        self.assertEqual(budget.used['ledger_bytes'], 100)
        self.assertEqual(budget.used['control_messages'], 3)

    def test_same_session_recovery_keeps_counters_and_other_seat_is_independent(self):
        session_a = SessionBudget(SessionLimits(3, 1000, 300, 400))
        session_b = SessionBudget()
        session_a.charge(control_messages=1, control_bytes=50, ledger_bytes=100)
        # Resume uses the existing session budget; snapshot is a fresh entry.
        session_a.charge(control_messages=1, control_bytes=50, ledger_bytes=100)
        session_a.charge(control_messages=1, control_bytes=50, replay_bytes=100)
        with self.assertRaises(SessionError):
            session_a.charge(control_messages=1, control_bytes=50)
        self.assertEqual(session_a.used['ledger_bytes'], 200)
        self.assertEqual(session_a.used['replay_bytes'], 100)
        self.assertFalse(session_b.closed)
        session_b.charge(ledger_bytes=100)
        self.assertEqual(session_b.used['ledger_bytes'], 100)

    def test_invalid_increment_does_not_mutate_or_close(self):
        budget = SessionBudget()
        for value in (-1, True, 2**53, 0.25):
            with self.assertRaises(ValueError):
                budget.charge(control_bytes=value)
        self.assertFalse(budget.closed)
        self.assertEqual(sum(budget.used.values()), 0)


if __name__ == '__main__':
    unittest.main()
