// Finite scheduler model for YRC 0003 draft.8 sections 8 and 9.
// One single decision or one complete three-seat reaction group per run.
// Each peer has its own relative host sequence; wire delivery is a separate model.
module yamai_request_liveness {
  type Phase = Idle | Open | Closed | Decided | Acked | Resolved
  type Policy = Multiple | HeadBump | DoubleOnly
  type Choice = NoChoice | Discard | Reach | Pass | Call | Hora
  type AckStatus = AckNone | Accepted | Passed | Superseded | Defaulted

  pure val SEATS = Set(0, 1, 2, 3)
  pure val GROUP = Set(1, 2, 3)
  pure val EMPTY_CHOICES = Map(0 -> NoChoice, 1 -> NoChoice, 2 -> NoChoice, 3 -> NoChoice)
  pure val EMPTY_ACKS = Map(0 -> AckNone, 1 -> AckNone, 2 -> AckNone, 3 -> AckNone)
  pure val ZERO = Map(0 -> 0, 1 -> 0, 2 -> 0, 3 -> 0)

  type State = {
    phase: Phase,
    policy: Policy,
    initial_timeout: int,
    stable_connection: bool,
    members: Set[int],
    choices: int -> Choice,
    defaulted: Set[int],
    chosen: Set[int],
    acked: Set[int],
    statuses: int -> AckStatus,
    remaining: int -> int,
    original_deadlines: int -> int,
    elapsed: int,
    selected_at: int -> int,
    host_seq: int -> int,
    previous_host_seq: int -> int,
    published: Set[int],
    publish_count: int,
    late_ack_acked: bool,
  }

  var state: State

  val selected = state.members.filter(seat => state.choices.get(seat) != NoChoice)
  val hora_seats = state.members.filter(seat => state.choices.get(seat) == Hora)
  val call_seats = state.members.filter(seat => state.choices.get(seat) == Call)
  pure def nearest(seats: Set[int]): Set[int] =
    seats.filter(seat => seats.forall(other => seat <= other))
  val expected_chosen =
    if (state.members == Set(0)) Set(0)
    else if (hora_seats.size() > 0)
      if (state.policy == DoubleOnly and hora_seats.size() == 3) Set()
      else if (state.policy == HeadBump) nearest(hora_seats)
      else hora_seats
    else nearest(call_seats)

  def expected_ack(seat: int): AckStatus =
    if (state.defaulted.contains(seat)) Defaulted
    else if (state.choices.get(seat) == Pass) Passed
    else if (state.chosen.contains(seat)) Accepted
    else Superseded

  val type_ok = and {
    state.phase.in(Set(Idle, Open, Closed, Decided, Acked, Resolved)),
    state.policy.in(Set(Multiple, HeadBump, DoubleOnly)),
    state.initial_timeout.in(Set(0, 2)),
    state.stable_connection,
    state.members == Set() or state.members == Set(0) or state.members == GROUP,
    state.choices.keys() == SEATS,
    state.statuses.keys() == SEATS,
    state.remaining.keys() == SEATS,
    state.original_deadlines.keys() == SEATS,
    state.selected_at.keys() == SEATS,
    state.host_seq.keys() == SEATS,
    state.previous_host_seq.keys() == SEATS,
    SEATS.forall(seat => state.choices.get(seat).in(Set(NoChoice, Discard, Reach, Pass, Call, Hora))),
    SEATS.forall(seat => state.statuses.get(seat).in(Set(AckNone, Accepted, Passed, Superseded, Defaulted))),
    SEATS.forall(seat => state.remaining.get(seat).in(0.to(2))),
    SEATS.forall(seat => state.original_deadlines.get(seat).in(0.to(2))),
    SEATS.forall(seat => state.selected_at.get(seat).in(0.to(2))),
    SEATS.forall(seat => state.host_seq.get(seat).in(0.to(3))),
    SEATS.forall(seat => state.previous_host_seq.get(seat).in(0.to(3))),
    state.elapsed.in(0.to(2)),
    state.publish_count.in(0.to(1)),
    state.defaulted.subseteq(selected),
    state.acked.subseteq(selected),
    state.chosen.subseteq(state.members),
    state.published.subseteq(state.members),
  }

  val host_seq_non_decreasing = SEATS.forall(seat =>
    state.host_seq.get(seat) >= state.previous_host_seq.get(seat))

  val capacity_invariant = SEATS.forall(seat => state.host_seq.get(seat) ==
    (if (state.members.contains(seat)) 1 else 0)
    + (if (state.acked.contains(seat)) 1 else 0)
    + state.publish_count)

  val request_lifecycle_invariant = and {
    state.phase == Idle implies state.members == Set(),
    state.phase == Open implies selected != state.members,
    state.phase.in(Set(Closed, Decided, Acked, Resolved)) implies selected == state.members,
    state.phase.in(Set(Idle, Open, Closed)) implies state.acked == Set(),
    state.phase.in(Set(Idle, Open, Closed)) implies state.chosen == Set(),
    state.phase == Decided implies state.acked != state.members,
    state.phase.in(Set(Acked, Resolved)) implies state.acked == state.members,
    state.phase == Resolved implies state.publish_count == 1,
    state.phase != Resolved implies state.publish_count == 0,
  }

  val request_data_invariant = and {
    state.phase.in(Set(Decided, Acked, Resolved)) implies state.chosen == expected_chosen,
    state.acked.forall(seat => state.statuses.get(seat) == expected_ack(seat)),
    SEATS.exclude(state.acked).forall(seat => state.statuses.get(seat) == AckNone),
    state.members.exclude(selected).forall(seat => state.choices.get(seat) == NoChoice),
    selected.exclude(state.defaulted).forall(seat => state.selected_at.get(seat) < state.original_deadlines.get(seat)),
    state.defaulted.forall(seat => state.selected_at.get(seat) == state.original_deadlines.get(seat)),
    state.defaulted.forall(seat => state.choices.get(seat) == (if (seat == 0) Discard else Pass)),
    state.phase == Resolved implies state.published == state.chosen,
  }

  val protocol_invariant = and {
    type_ok, host_seq_non_decreasing, capacity_invariant,
    request_lifecycle_invariant, request_data_invariant,
  }

  val late_ack_pending = selected.exclude(state.defaulted).exclude(state.acked).exists(seat =>
    state.elapsed >= state.original_deadlines.get(seat))

  val witness_single_open = state.phase == Open and state.members == Set(0)
  val witness_group_open = state.phase == Open and state.members == GROUP
  val witness_group_closed = state.phase == Closed
  val witness_timeout_boundary = state.phase == Open and state.members.exclude(selected).exists(seat => state.remaining.get(seat) == 0)
  val witness_defaulted = state.defaulted.size() > 0
  val witness_late_ack_pending = late_ack_pending
  val witness_late_ack_acked = state.late_ack_acked
  val witness_single_resolved = state.phase == Resolved and state.members == Set(0)
  val witness_group_resolved = state.phase == Resolved and state.members == GROUP
  val witness_hora_priority = state.phase == Decided and hora_seats.size() > 0 and call_seats.size() > 0
  val witness_sanchaho = state.phase == Resolved and state.policy == DoubleOnly and hora_seats.size() == 3 and state.published == Set()
  val witness_zero_deadline = state.phase == Resolved and state.initial_timeout == 0

  action init_with(policy, timeout) = state' = {
    phase: Idle, policy: policy, initial_timeout: timeout, stable_connection: true,
    members: Set(), choices: EMPTY_CHOICES, defaulted: Set(), chosen: Set(),
    acked: Set(), statuses: EMPTY_ACKS, remaining: ZERO, original_deadlines: ZERO,
    elapsed: 0, selected_at: ZERO, host_seq: ZERO, previous_host_seq: ZERO,
    published: Set(), publish_count: 0, late_ack_acked: false,
  }

  action init = {
    nondet policy = Set(Multiple, HeadBump, DoubleOnly).oneOf()
    nondet timeout = Set(0, 2).oneOf()
    init_with(policy, timeout)
  }

  action open_decision(members) = {
    val deadlines = ZERO.set(0, state.initial_timeout)
      .set(1, if (state.initial_timeout == 0) 0 else 1)
      .set(2, state.initial_timeout).set(3, state.initial_timeout)
    all {
      state.phase == Idle,
      state' = { phase: Open, members: members, remaining: deadlines,
        original_deadlines: deadlines,
        host_seq: SEATS.mapBy(seat => if (members.contains(seat)) 1 else 0),
        previous_host_seq: state.host_seq, ...state },
    }
  }
  action open_single_request = open_decision(Set(0))
  action open_decision_group = open_decision(GROUP)

  action submit(seat, choice) = all {
    state.phase == Open, state.members.contains(seat),
    state.choices.get(seat) == NoChoice, state.remaining.get(seat) > 0,
    if (seat == 0) choice.in(Set(Discard, Reach, Hora)) else choice.in(Set(Pass, Call, Hora)),
    state' = {
      choices: state.choices.set(seat, choice),
      selected_at: state.selected_at.set(seat, state.elapsed),
      phase: if (selected.union(Set(seat)) == state.members) Closed else Open,
      previous_host_seq: state.host_seq, ...state,
    },
  }
  action submit_any = {
    nondet seat = SEATS.oneOf()
    nondet choice = Set(Discard, Reach, Pass, Call, Hora).oneOf()
    submit(seat, choice)
  }

  action tick = all {
    state.phase == Open,
    state.members.exclude(selected).exists(seat => state.remaining.get(seat) > 0),
    state' = { elapsed: state.elapsed + 1,
      remaining: SEATS.mapBy(seat => if (state.members.exclude(selected).contains(seat) and state.remaining.get(seat) > 0)
        state.remaining.get(seat) - 1 else state.remaining.get(seat)),
      previous_host_seq: state.host_seq, ...state },
  }

  action default_member(seat) = all {
    state.phase == Open, state.members.contains(seat),
    state.choices.get(seat) == NoChoice, state.remaining.get(seat) == 0,
    state' = { choices: state.choices.set(seat, if (seat == 0) Discard else Pass),
      selected_at: state.selected_at.set(seat, state.original_deadlines.get(seat)),
      defaulted: state.defaulted.union(Set(seat)),
      phase: if (selected.union(Set(seat)) == state.members) Closed else Open,
      previous_host_seq: state.host_seq, ...state },
  }

  action decide = all {
    state.phase == Closed,
    state' = { phase: Decided, chosen: expected_chosen, previous_host_seq: state.host_seq, ...state },
  }

  action ack_member(seat) = all {
    state.phase == Decided, state.members.contains(seat), not(state.acked.contains(seat)),
    state' = {
      phase: if (state.acked.union(Set(seat)) == state.members) Acked else Decided,
      acked: state.acked.union(Set(seat)), statuses: state.statuses.set(seat, expected_ack(seat)),
      host_seq: state.host_seq.set(seat, state.host_seq.get(seat) + 1),
      previous_host_seq: state.host_seq,
      late_ack_acked: state.late_ack_acked or (not(state.defaulted.contains(seat)) and state.elapsed >= state.original_deadlines.get(seat)),
      ...state,
    },
  }

  action resolve_group = all {
    state.phase == Acked,
    state' = { phase: Resolved, published: state.chosen, publish_count: state.publish_count + 1,
      host_seq: SEATS.mapBy(seat => state.host_seq.get(seat) + 1),
      previous_host_seq: state.host_seq, ...state },
  }

  action progress = any {
    default_member(0), default_member(1), default_member(2), default_member(3),
    decide, ack_member(0), ack_member(1), ack_member(2), ack_member(3), resolve_group,
  }
  action stutter = state' = {previous_host_seq: state.host_seq, ...state}
  action step = any {open_single_request, open_decision_group, submit_any, tick, progress, stutter}

  temporal stable_connection_is_preserved = always(state.stable_connection)
  temporal group_resolves_under_stable_connection =
    (weakFair(tick, state) and weakFair(progress, state)).implies(
      always(state.phase != Idle implies eventually(state.phase == Resolved)))
  temporal timeout_closes_under_stable_connection =
    (weakFair(tick, state) and weakFair(progress, state)).implies(always(witness_timeout_boundary implies eventually(state.phase != Open)))
  temporal late_ack_survives_default_under_stable_connection =
    (weakFair(tick, state) and weakFair(progress, state)).implies(always(late_ack_pending implies eventually(state.late_ack_acked)))

  run sanchahoTest = init_with(DoubleOnly, 2).then(open_decision_group)
    .then(submit(1, Hora)).then(submit(2, Hora)).then(submit(3, Hora)).then(decide)
    .then(ack_member(1)).then(ack_member(2)).then(ack_member(3)).then(resolve_group)
    .then(all {assert(witness_sanchaho), assert(protocol_invariant), state' = state})
  run selectionBeforeAckTest = init_with(Multiple, 2).then(open_decision_group)
    .then(submit(1, Call))
    .then(submit(2, Hora)).then(submit(3, Pass)).then(decide)
    .then(ack_member(1)).then(all {assert(state.statuses.get(1) == Superseded), assert(protocol_invariant), state' = state})
  run earlyAckFailsTest = init_with(Multiple, 2).then(open_decision_group)
    .then(submit(1, Call)).then(ack_member(1).fail())
  run zeroDeadlineTest = init_with(Multiple, 0).then(open_decision_group)
    .then(default_member(1)).then(default_member(2)).then(default_member(3)).then(decide)
    .then(ack_member(1)).then(ack_member(2)).then(ack_member(3)).then(resolve_group)
    .then(all {assert(witness_zero_deadline), assert(protocol_invariant), state' = state})
  run zeroDeadlineRejectsActionTest = init_with(Multiple, 0).then(open_decision_group)
    .then(submit(1, Hora).fail())
}
