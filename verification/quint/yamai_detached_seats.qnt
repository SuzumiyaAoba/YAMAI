// Bounded projection of permanent fatal-seat detachment, not a complete host.
// Four seats; seat 1 closes in decision 1. Decisions 1 and 2 are three-seat
// reactions; decision 3 is seat 1's draw turn. G=T=1; game-scoped bank is 0..1.
// Each tick is one model time unit. Legal-candidate enumeration, mahjong
// priority/scoring, authentication, transport queues and survivor failures are
// outside this model. Live peers have sufficient output capacity. Seat 1's
// exhausted closed-session quota cannot gate internal preparation/resolution.
// Decision 1 is constrained to include the fatal close before resolution;
// that scenario restriction is not an extra production-host scheduling guard.
module yamai_detached_seats {
  type Phase = Preparing | Open | Ready | Between | Finished
  type Choice = NoChoice | Pass | Call | Discard
  pure val SEATS = Set(0, 1, 2, 3)
  pure val SURVIVORS = Set(0, 2, 3)
  pure val ZERO = SEATS.mapBy(_seat => 0)
  pure val ONE = SEATS.mapBy(_seat => 1)
  pure val EMPTY = SEATS.mapBy(_seat => NoChoice)
  pure val UNSELECTED = SEATS.mapBy(_seat => -1)
  pure def members(decision: int): Set[int] =
    if (decision < 3) Set(1, 2, 3) else Set(1)
  pure def default_choice(decision: int): Choice =
    if (decision < 3) Pass else Discard
  pure def spent(elapsed: int, bank: int): int =
    if (elapsed <= 2) 0 else if (elapsed - 2 < bank) elapsed - 2 else bank

  type State = {
    phase: Phase, decision: int, resolved: int, prepared: Set[int],
    detached: bool, token_valid: bool, replacement_allowed: bool,
    closed_quota: int, closed_seq: int, closed_wire: int,
    closed_elapsed: int, closed_deadline: int, closed_started: bool, closed_choice: Choice,
    closed_selected_at: int, closed_bank: int,
    elapsed: int, bank: int -> int, prior_bank: int -> int,
    deadline: int -> int, choice: int -> Choice, selected_at: int -> int,
    defaulted: Set[int], ledger: int -> int, wire: int -> int,
    internal_prepared: Set[int], defaulted_decisions: Set[int],
    seat1_results: int -> Choice, survivor_results: int,
  }
  var state: State
  val current_members = members(state.decision)
  val selected = current_members.filter(seat => state.choice.get(seat) != NoChoice)
  pure def live(seat: int, detached: bool): bool = seat != 1 or not(detached)

  action init = state' = {
    phase: Preparing, decision: 1, resolved: 0, prepared: Set(),
    detached: false, token_valid: true, replacement_allowed: false,
    closed_quota: 1, closed_seq: 0, closed_wire: 0,
    closed_elapsed: 0, closed_deadline: 0, closed_started: false, closed_choice: NoChoice,
    closed_selected_at: -1, closed_bank: 1,
    elapsed: 0, bank: ONE, prior_bank: ONE, deadline: ZERO,
    choice: EMPTY, selected_at: UNSELECTED, defaulted: Set(),
    ledger: ZERO, wire: ZERO, internal_prepared: Set(),
    defaulted_decisions: Set(), seat1_results: Map(1 -> NoChoice, 2 -> NoChoice, 3 -> NoChoice),
    survivor_results: 0,
  }

  action prepare(seat) = all {
    state.phase == Preparing, current_members.contains(seat),
    not(state.prepared.contains(seat)),
    // Closed seat readiness is internal work, not a session enqueue/reservation.
    state' = {
      prepared: state.prepared.union(Set(seat)),
      internal_prepared: if (seat == 1 and state.detached)
        state.internal_prepared.union(Set(state.decision)) else state.internal_prepared,
      ledger: state.ledger.set(seat, state.ledger.get(seat) + (if (live(seat, state.detached)) 1 else 0)),
      wire: state.wire.set(seat, state.wire.get(seat) + (if (live(seat, state.detached)) 1 else 0)),
      ...state,
    },
  }
  action prepare_any = {
    nondet seat = SEATS.oneOf()
    prepare(seat)
  }
  action start = all {
    state.phase == Preparing, state.prepared == current_members,
    state' = { phase: Open, elapsed: 0, prior_bank: state.bank,
      deadline: SEATS.mapBy(seat => if (current_members.contains(seat)) 2 + state.bank.get(seat) else 0),
      ...state },
  }

  action submit(seat, choice) = all {
    state.phase == Open, current_members.contains(seat),
    live(seat, state.detached), state.choice.get(seat) == NoChoice,
    state.elapsed < state.deadline.get(seat),
    if (state.decision < 3) choice.in(Set(Pass, Call)) else choice == Discard,
    state' = {
      choice: state.choice.set(seat, choice),
      selected_at: state.selected_at.set(seat, state.elapsed),
      bank: state.bank.set(seat, state.prior_bank.get(seat) - spent(state.elapsed, state.prior_bank.get(seat))),
      phase: if (selected.union(Set(seat)) == current_members) Ready else Open,
      ...state,
    },
  }
  action submit_any = {
    nondet seat = SEATS.oneOf()
    nondet choice = Set(Pass, Call, Discard).oneOf()
    submit(seat, choice)
  }
  action tick = all {
    state.phase == Open,
    current_members.exclude(selected).exists(seat => state.elapsed < state.deadline.get(seat)),
    state' = { elapsed: state.elapsed + 1, ...state },
  }
  action default_member(seat) = all {
    state.phase == Open, current_members.contains(seat),
    state.choice.get(seat) == NoChoice, state.elapsed >= state.deadline.get(seat),
    state' = {
      choice: state.choice.set(seat, default_choice(state.decision)),
      selected_at: state.selected_at.set(seat, state.deadline.get(seat)),
      bank: state.bank.set(seat, 0), defaulted: state.defaulted.union(Set(seat)),
      phase: if (selected.union(Set(seat)) == current_members) Ready else Open,
      ...state,
    },
  }
  action default_any = {
    nondet seat = SEATS.oneOf()
    default_member(seat)
  }

  action close_seat = all {
    state.decision == 1, state.phase.in(Set(Preparing, Open, Ready)), not(state.detached),
    state' = { detached: true, token_valid: false, closed_quota: 0,
      closed_seq: state.ledger.get(1), closed_wire: state.wire.get(1),
      closed_elapsed: state.elapsed, closed_deadline: state.deadline.get(1),
      closed_started: state.phase != Preparing,
      closed_choice: state.choice.get(1), closed_selected_at: state.selected_at.get(1),
      closed_bank: state.bank.get(1), ...state },
  }
  // Explicit forbidden paths: neither token replay nor a replacement grants a
  // new controller for the permanently detached seat in this game.
  action resume_closed = all {
    state.detached, state.token_valid,
    state' = { detached: false, ...state },
  }
  action replace_closed = all {
    state.detached, state.replacement_allowed,
    state' = { detached: false, ...state },
  }
  action resolve = all {
    state.phase == Ready, state.detached,
    state' = {
      phase: if (state.decision == 3) Finished else Between,
      resolved: state.resolved + 1, survivor_results: state.survivor_results + 1,
      seat1_results: state.seat1_results.set(state.decision, state.choice.get(1)),
      defaulted_decisions: if (state.defaulted.contains(1))
        state.defaulted_decisions.union(Set(state.decision)) else state.defaulted_decisions,
      ledger: SEATS.mapBy(seat => state.ledger.get(seat) +
        (if (live(seat, state.detached)) 1 + (if (current_members.contains(seat)) 1 else 0) else 0)),
      wire: SEATS.mapBy(seat => state.wire.get(seat) +
        (if (live(seat, state.detached)) 1 + (if (current_members.contains(seat)) 1 else 0) else 0)),
      ...state,
    },
  }
  action next_decision = all {
    state.phase == Between, state.decision < 3,
    state' = { phase: Preparing, decision: state.decision + 1, prepared: Set(),
      elapsed: 0, prior_bank: state.bank, deadline: ZERO, choice: EMPTY,
      selected_at: UNSELECTED, defaulted: Set(), ...state },
  }
  action stutter = state' = state
  action step = any {
    prepare_any, start, submit_any, tick, default_any, close_seat,
    resolve, next_decision, resume_closed, replace_closed, stutter,
  }

  val type_ok = and {
    state.phase.in(Set(Preparing, Open, Ready, Between, Finished)),
    state.decision.in(1.to(3)), state.resolved.in(0.to(3)), state.elapsed.in(0.to(3)),
    state.prepared.subseteq(current_members), state.defaulted.subseteq(selected),
    state.internal_prepared.subseteq(Set(1, 2, 3)), state.defaulted_decisions.subseteq(Set(1, 2, 3)),
    SEATS.forall(seat => and {
      state.bank.get(seat).in(0.to(1)), state.prior_bank.get(seat).in(0.to(1)),
      state.deadline.get(seat).in(0.to(3)), state.selected_at.get(seat).in((-1).to(3)),
      state.ledger.get(seat).in(0.to(7)), state.wire.get(seat) == state.ledger.get(seat),
    }),
  }
  val closed_session_is_permanent = and {
    not(state.replacement_allowed), state.token_valid == not(state.detached),
    state.detached implies and {
      state.closed_quota == 0, state.ledger.get(1) == state.closed_seq,
      state.wire.get(1) == state.closed_wire,
    },
  }
  val clocks_and_selection = and {
    state.phase != Preparing implies state.prepared == current_members,
    state.phase == Open implies selected != current_members,
    state.phase == Ready implies selected == current_members,
    state.phase != Preparing implies current_members.forall(seat => state.deadline.get(seat) == 2 + state.prior_bank.get(seat)),
    current_members.exclude(selected).forall(seat => state.bank.get(seat) == state.prior_bank.get(seat)),
    selected.forall(seat => and {
      state.selected_at.get(seat) <= state.elapsed,
      state.bank.get(seat) == state.prior_bank.get(seat) - spent(state.selected_at.get(seat), state.prior_bank.get(seat)),
      if (state.defaulted.contains(seat)) and {
        state.selected_at.get(seat) == state.deadline.get(seat),
        state.choice.get(seat) == default_choice(state.decision),
      } else state.selected_at.get(seat) < state.deadline.get(seat),
    }),
    state.detached and state.decision == 1 implies and {
      state.closed_started implies state.deadline.get(1) == state.closed_deadline,
      state.elapsed >= state.closed_elapsed,
      state.closed_choice != NoChoice implies and {
        state.choice.get(1) == state.closed_choice,
        state.selected_at.get(1) == state.closed_selected_at, state.bank.get(1) == state.closed_bank,
      },
    },
    state.decision > 1 and selected.contains(1) implies state.defaulted.contains(1),
  }
  val progress_accounting = and {
    state.survivor_results == state.resolved,
    state.phase == Finished implies and {
      state.resolved == 3, Set(2, 3).subseteq(state.defaulted_decisions),
      Set(2, 3).subseteq(state.internal_prepared), state.seat1_results.get(2) == Pass,
      state.seat1_results.get(3) == Discard,
    },
  }
  val protocol_invariant = and {type_ok, closed_session_is_permanent, clocks_and_selection, progress_accounting}
  val witness_closed_open = state.detached and state.phase == Open and state.choice.get(1) == NoChoice
  val witness_closed_selected = state.detached and state.closed_choice == Call
  val witness_closed_prestart = state.detached and state.phase == Preparing and state.closed_seq == 0
  val witness_future_group = state.decision == 2 and state.phase == Open and state.internal_prepared.contains(2)
  val witness_two_future_decisions = state.phase == Finished
  temporal detached_seat_does_not_block_progress =
    (weakFair(prepare_any, state) and weakFair(start, state) and weakFair(tick, state)
      and weakFair(default_any, state) and weakFair(resolve, state) and weakFair(next_decision, state))
      .implies(always(state.detached implies eventually(state.phase == Finished)))

  action prepare_group = prepare(1).then(prepare(2)).then(prepare(3)).then(start)
  action expire_group = tick.then(tick).then(tick)
    .then(default_member(1)).then(default_member(2)).then(default_member(3))
  run closedOpenKeepsClockTest = init.then(prepare_group).then(tick).then(close_seat)
    .then(all {assert(state.elapsed < state.deadline.get(1)), state' = state}).then(tick).then(all {assert(state.elapsed < state.deadline.get(1)), state' = state})
    .then(tick).then(default_member(1))
    .then(all {assert(state.selected_at.get(1) == 3), assert(state.bank.get(1) == 0), assert(protocol_invariant), state' = state})
  run selectedBeforeCloseSurvivesTest = init.then(prepare_group).then(submit(1, Call)).then(close_seat)
    .then(tick).then(tick).then(tick).then(default_member(2)).then(default_member(3)).then(resolve)
    .then(all {assert(state.seat1_results.get(1) == Call), assert(state.bank.get(1) == 1), assert(protocol_invariant), state' = state})
  run detachedResumeDeniedTest = init.then(prepare_group).then(close_seat).then(resume_closed.fail())
  run detachedReplacementDeniedTest = init.then(prepare_group).then(close_seat).then(replace_closed.fail())
  run detachedInputDeniedTest = init.then(prepare_group).then(close_seat).then(submit(1, Call).fail())
  run closedDoesNotDefaultEarlyTest = init.then(prepare_group).then(tick).then(close_seat)
    .then(tick).then(default_member(1).fail())
  run incompleteGroupCannotStartTest = init.then(prepare(2)).then(prepare(3)).then(close_seat).then(start.fail())
  run twoFutureDecisionsTest = init.then(prepare_group).then(close_seat).then(expire_group).then(resolve)
    .then(next_decision).then(prepare_group).then(tick).then(all {assert(state.elapsed < state.deadline.get(1)), state' = state}).then(tick)
    .then(default_member(1)).then(default_member(2)).then(default_member(3)).then(resolve)
    .then(next_decision).then(prepare(1)).then(start).then(tick).then(all {assert(state.elapsed < state.deadline.get(1)), state' = state})
    .then(tick).then(default_member(1)).then(resolve)
    .then(all {assert(witness_two_future_decisions), assert(state.ledger.get(1) == 1),
      assert(state.survivor_results == 3), assert(protocol_invariant), state' = state})
  run allThreeMustBeReadyTest = init.then(prepare_group).then(close_seat).then(expire_group).then(resolve)
    .then(next_decision).then(prepare(2)).then(prepare(3)).then(all {assert(state.prepared != current_members), state' = state})
    .then(prepare(1)).then(start)
    .then(all {assert(witness_future_group), assert(state.ledger.get(1) == 1), assert(protocol_invariant), state' = state})
  run closeBeforeAdmissionTest = init.then(prepare(2)).then(prepare(3)).then(close_seat)
    .then(all {assert(witness_closed_prestart), state' = state})
    .then(all {assert(state.prepared != current_members), state' = state}).then(prepare(1)).then(start)
    .then(all {assert(state.deadline.get(1) == 3), assert(state.ledger.get(1) == 0),
      assert(state.internal_prepared.contains(1)), assert(protocol_invariant), state' = state})
}
