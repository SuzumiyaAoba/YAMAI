// Exhaustive finite request/group model for liveness under a stable
// connection.
//
// This deliberately removes negotiation, wire delivery, replay, and session
// teardown from the state space.  `stable_connection` is fixed to true and
// there is no disconnect action; consequently the temporal claims below are
// conditional on the environment assumption that the real connection is
// eventually stable.  The extended model checks the separate disconnect /
// resume safety obligations.

module yamai_request_liveness {
  type RequestKind = NoKind | SingleKind | GroupKind
  type RequestState = NoRequest | RequestOpen | RequestAcked
  type GroupState = NoGroup | GroupOpen | GroupClosed | GroupResolved
  type AckStatus = AckNone | Accepted | Passed | Defaulted
  type ActionId = NoAction | PassAction | ReachAction | CallAction | DefaultAction

  pure val SEATS: Set[int] = Set(0, 1, 2, 3)
  pure val SINGLE_SEAT: int = 0
  pure val GROUP_SEATS: Set[int] = Set(1, 2)
  pure val ACTION_IDS: Set[ActionId] = Set(NoAction, PassAction, ReachAction, CallAction, DefaultAction)

  // The bounds are intentionally smaller than a real protocol.  A single
  // request reserves open + ack/default + resolve; a group reserves the
  // corresponding two-member path.  There is only one request per run, so
  // both single and group initial branches are exhaustively finite.
  pure val MAX_SEQ: int = 6
  pure val MAX_TIMEOUT: int = 2
  pure val MAX_REQUEST_ID: int = 2
  pure val SINGLE_RESERVATION: int = 4
  pure val GROUP_RESERVATION: int = 5

  pure val EMPTY_REQUEST_IDS: int -> int = Map(0 -> 0, 1 -> 0, 2 -> 0, 3 -> 0)
  pure val EMPTY_ACTIONS: int -> ActionId = Map(0 -> NoAction, 1 -> NoAction, 2 -> NoAction, 3 -> NoAction)
  pure val EMPTY_ACKS: int -> AckStatus = Map(0 -> AckNone, 1 -> AckNone, 2 -> AckNone, 3 -> AckNone)

  type RequestStateRecord = {
    request_kind: RequestKind,
    request_state: RequestState,
    group_state: GroupState,
    stable_connection: bool,
    host_seq: int,
    previous_host_seq: int,
    applied_seq: int,
    seq_reservation_end: int,
    pending_members: Set[int],
    responded_members: Set[int],
    terminal_members: Set[int],
    defaulted_members: Set[int],
    request_ids: int -> int,
    submitted_actions: int -> ActionId,
    ack_statuses: int -> AckStatus,
    deadline: int,
    next_request_id: int,
    late_ack_acked: bool,
  }

  var state: RequestStateRecord

  // ------------------------------------------------------------------------
  // Safety predicates and reachability witnesses
  // ------------------------------------------------------------------------

  val type_ok: bool = and {
    state.request_kind.in(Set(NoKind, SingleKind, GroupKind)),
    state.request_state.in(Set(NoRequest, RequestOpen, RequestAcked)),
    state.group_state.in(Set(NoGroup, GroupOpen, GroupClosed, GroupResolved)),
    state.stable_connection,
    state.host_seq.in(0.to(MAX_SEQ)),
    state.previous_host_seq.in(0.to(MAX_SEQ)),
    state.applied_seq.in(0.to(MAX_SEQ)),
    state.applied_seq <= state.host_seq,
    state.seq_reservation_end.in(0.to(MAX_SEQ)),
    state.pending_members.subseteq(SEATS),
    state.responded_members.subseteq(state.pending_members),
    state.terminal_members.subseteq(state.responded_members),
    state.defaulted_members.subseteq(state.terminal_members),
    state.defaulted_members.subseteq(state.pending_members),
    state.request_ids.keys() == SEATS,
    state.submitted_actions.keys() == SEATS,
    state.ack_statuses.keys() == SEATS,
    state.request_ids.keys().forall(seat => state.request_ids.get(seat).in(0.to(MAX_REQUEST_ID))),
    state.submitted_actions.keys().forall(seat => state.submitted_actions.get(seat).in(ACTION_IDS)),
    state.ack_statuses.keys().forall(seat => state.ack_statuses.get(seat).in(Set(AckNone, Accepted, Passed, Defaulted))),
    state.deadline.in(0.to(MAX_TIMEOUT)),
    state.next_request_id.in(0.to(MAX_REQUEST_ID)),
  }

  val host_seq_non_decreasing: bool = and {
    state.host_seq >= state.previous_host_seq,
    state.host_seq <= state.seq_reservation_end,
  }

  val capacity_invariant: bool =
    state.request_state == NoRequest or state.host_seq < state.seq_reservation_end

  val request_lifecycle_invariant: bool = and {
    state.request_state == RequestOpen implies state.group_state == GroupOpen,
    state.group_state == GroupOpen implies state.request_state == RequestOpen,
    state.group_state == GroupClosed implies state.request_state == RequestAcked,
    state.group_state == GroupResolved implies state.request_state == NoRequest,
    state.request_state == NoRequest implies state.pending_members == Set(),
    state.request_state == NoRequest implies state.responded_members == Set(),
    state.request_state == NoRequest implies state.terminal_members == Set(),
    state.group_state == GroupClosed implies state.terminal_members == state.pending_members,
    state.group_state == GroupOpen implies state.pending_members.size() > 0,
  }

  val request_data_invariant: bool = and {
    state.pending_members.forall(seat => state.request_ids.get(seat) > 0),
    state.pending_members == GROUP_SEATS implies state.request_ids.get(1) != state.request_ids.get(2),
    state.pending_members.exclude(state.responded_members).forall(seat => state.submitted_actions.get(seat) == NoAction),
    state.responded_members.exclude(state.terminal_members).forall(seat =>
      state.submitted_actions.get(seat).in(Set(PassAction, ReachAction, CallAction))
    ),
    state.defaulted_members.forall(seat => state.submitted_actions.get(seat) == DefaultAction),
    state.terminal_members.forall(seat => state.ack_statuses.get(seat) != AckNone),
    state.defaulted_members.forall(seat => state.ack_statuses.get(seat) == Defaulted),
    state.pending_members.exclude(state.terminal_members).forall(seat => state.ack_statuses.get(seat) == AckNone),
    state.late_ack_acked implies state.defaulted_members.size() > 0,
  }

  val protocol_invariant: bool = and {
    type_ok,
    host_seq_non_decreasing,
    capacity_invariant,
    request_lifecycle_invariant,
    request_data_invariant,
  }

  val late_ack_pending: bool = and {
    state.defaulted_members.size() > 0,
    state.responded_members.exclude(state.terminal_members).size() > 0,
  }

  // Witnesses are checked separately from invariants so that temporal
  // antecedents cannot pass merely because their interesting states are
  // unreachable.
  val witness_single_open: bool = state.request_kind == SingleKind and state.group_state == GroupOpen
  val witness_group_open: bool = state.request_kind == GroupKind and state.group_state == GroupOpen
  val witness_group_closed: bool = state.group_state == GroupClosed
  val witness_timeout_boundary: bool = state.request_state == RequestOpen and state.deadline == 0
  val witness_defaulted: bool = state.defaulted_members.size() > 0
  val witness_late_ack_pending: bool = late_ack_pending
  val witness_late_ack_acked: bool = state.late_ack_acked
  val witness_single_resolved: bool = state.request_kind == SingleKind and state.group_state == GroupResolved
  val witness_group_resolved: bool = state.request_kind == GroupKind and state.group_state == GroupResolved

  // ------------------------------------------------------------------------
  // Initial state and request transitions
  // ------------------------------------------------------------------------

  action init = state' = {
    request_kind: NoKind,
    request_state: NoRequest,
    group_state: NoGroup,
    stable_connection: true,
    host_seq: 0,
    previous_host_seq: 0,
    applied_seq: 0,
    seq_reservation_end: 0,
    pending_members: Set(),
    responded_members: Set(),
    terminal_members: Set(),
    defaulted_members: Set(),
    request_ids: EMPTY_REQUEST_IDS,
    submitted_actions: EMPTY_ACTIONS,
    ack_statuses: EMPTY_ACKS,
    deadline: 0,
    next_request_id: 0,
    late_ack_acked: false,
  }

  action open_single_request = all {
    state.request_kind == NoKind,
    state.request_state == NoRequest,
    state.group_state == NoGroup,
    state.host_seq + SINGLE_RESERVATION <= MAX_SEQ,
    state.next_request_id + 1 <= MAX_REQUEST_ID,
    state' = {
      request_kind: SingleKind,
      request_state: RequestOpen,
      group_state: GroupOpen,
      host_seq: state.host_seq + 1,
      previous_host_seq: state.host_seq,
      applied_seq: state.applied_seq + 1,
      seq_reservation_end: state.host_seq + SINGLE_RESERVATION,
      pending_members: Set(SINGLE_SEAT),
      responded_members: Set(),
      terminal_members: Set(),
      defaulted_members: Set(),
      request_ids: EMPTY_REQUEST_IDS.set(SINGLE_SEAT, state.next_request_id + 1),
      submitted_actions: EMPTY_ACTIONS,
      ack_statuses: EMPTY_ACKS,
      deadline: MAX_TIMEOUT,
      next_request_id: state.next_request_id + 1,
      late_ack_acked: false,
      ...state,
    },
  }

  action open_decision_group = all {
    state.request_kind == NoKind,
    state.request_state == NoRequest,
    state.group_state == NoGroup,
    state.host_seq + GROUP_RESERVATION <= MAX_SEQ,
    state.next_request_id + 2 <= MAX_REQUEST_ID,
    state' = {
      request_kind: GroupKind,
      request_state: RequestOpen,
      group_state: GroupOpen,
      host_seq: state.host_seq + 2,
      previous_host_seq: state.host_seq,
      applied_seq: state.applied_seq + 2,
      seq_reservation_end: state.host_seq + GROUP_RESERVATION,
      pending_members: GROUP_SEATS,
      responded_members: Set(),
      terminal_members: Set(),
      defaulted_members: Set(),
      request_ids: EMPTY_REQUEST_IDS.set(1, state.next_request_id + 1).set(2, state.next_request_id + 2),
      submitted_actions: EMPTY_ACTIONS,
      ack_statuses: EMPTY_ACKS,
      deadline: MAX_TIMEOUT,
      next_request_id: state.next_request_id + 2,
      late_ack_acked: false,
      ...state,
    },
  }

  action submit_action(seat, action_id) = all {
    state.request_state == RequestOpen,
    state.group_state == GroupOpen,
    state.stable_connection,
    // deadline == 0 gives timeout/default priority; a new action is barred.
    state.deadline > 0,
    state.pending_members.contains(seat),
    not(state.responded_members.contains(seat)),
    action_id.in(Set(PassAction, ReachAction, CallAction)),
    state' = {
      responded_members: state.responded_members.union(Set(seat)),
      submitted_actions: state.submitted_actions.set(seat, action_id),
      previous_host_seq: state.host_seq,
      ...state,
    },
  }

  action submit_single_pass = submit_action(0, PassAction)
  action submit_single_reach = submit_action(0, ReachAction)
  action submit_group_one_pass = submit_action(1, PassAction)
  action submit_group_one_call = submit_action(1, CallAction)
  action submit_group_two_pass = submit_action(2, PassAction)
  action submit_group_two_call = submit_action(2, CallAction)

  action ack_member(seat) = all {
    state.request_state == RequestOpen,
    state.group_state == GroupOpen,
    state.stable_connection,
    state.pending_members.contains(seat),
    state.responded_members.contains(seat),
    not(state.terminal_members.contains(seat)),
    state.host_seq < state.seq_reservation_end,
    state' = {
      host_seq: state.host_seq + 1,
      previous_host_seq: state.host_seq,
      applied_seq: state.applied_seq + 1,
      terminal_members: state.terminal_members.union(Set(seat)),
      ack_statuses: state.ack_statuses.set(seat,
        if (state.submitted_actions.get(seat) == PassAction) Passed else Accepted
      ),
      late_ack_acked: state.late_ack_acked or state.defaulted_members.size() > 0,
      request_state: if (state.terminal_members.union(Set(seat)) == state.pending_members) RequestAcked else RequestOpen,
      group_state: if (state.terminal_members.union(Set(seat)) == state.pending_members) GroupClosed else GroupOpen,
      ...state,
    },
  }

  action ack_single = ack_member(0)
  action ack_group_one = ack_member(1)
  action ack_group_two = ack_member(2)

  action tick = all {
    state.request_state == RequestOpen,
    state.stable_connection,
    state.deadline > 0,
    state' = { deadline: state.deadline - 1, previous_host_seq: state.host_seq, ...state },
  }

  action default_action = {
    // Only unresponded members are defaulted.  An action received before the
    // deadline remains in submitted_actions and can still be ACKed later.
    val missing_members = state.pending_members.exclude(state.responded_members)
    val next_actions_0 = if (missing_members.contains(0)) state.submitted_actions.set(0, DefaultAction) else state.submitted_actions
    val next_actions_1 = if (missing_members.contains(1)) next_actions_0.set(1, DefaultAction) else next_actions_0
    val next_actions_2 = if (missing_members.contains(2)) next_actions_1.set(2, DefaultAction) else next_actions_1
    val next_actions = if (missing_members.contains(3)) next_actions_2.set(3, DefaultAction) else next_actions_2
    val next_acks_0 = if (missing_members.contains(0)) state.ack_statuses.set(0, Defaulted) else state.ack_statuses
    val next_acks_1 = if (missing_members.contains(1)) next_acks_0.set(1, Defaulted) else next_acks_0
    val next_acks_2 = if (missing_members.contains(2)) next_acks_1.set(2, Defaulted) else next_acks_1
    val next_acks = if (missing_members.contains(3)) next_acks_2.set(3, Defaulted) else next_acks_2
    val next_terminal = state.terminal_members.union(missing_members)
    all {
      state.request_state == RequestOpen,
      state.group_state == GroupOpen,
      state.stable_connection,
      state.deadline == 0,
      missing_members.size() > 0,
      state.host_seq + missing_members.size() <= state.seq_reservation_end,
      state' = {
        host_seq: state.host_seq + missing_members.size(),
        previous_host_seq: state.host_seq,
        applied_seq: state.applied_seq + missing_members.size(),
        responded_members: state.responded_members.union(missing_members),
        terminal_members: next_terminal,
        defaulted_members: state.defaulted_members.union(missing_members),
        submitted_actions: next_actions,
        ack_statuses: next_acks,
        request_state: if (next_terminal == state.pending_members) RequestAcked else RequestOpen,
        group_state: if (next_terminal == state.pending_members) GroupClosed else GroupOpen,
        ...state,
      },
    }
  }

  action ack_or_default = any {
    ack_single,
    ack_group_one,
    ack_group_two,
    default_action,
  }

  action resolve_group = all {
    state.request_state == RequestAcked,
    state.group_state == GroupClosed,
    state.stable_connection,
    state.terminal_members == state.pending_members,
    state.host_seq < state.seq_reservation_end,
    state' = {
      request_state: NoRequest,
      group_state: GroupResolved,
      host_seq: state.host_seq + 1,
      previous_host_seq: state.host_seq,
      applied_seq: state.applied_seq + 1,
      seq_reservation_end: state.host_seq + 1,
      pending_members: Set(),
      responded_members: Set(),
      terminal_members: Set(),
      defaulted_members: Set(),
      request_ids: EMPTY_REQUEST_IDS,
      submitted_actions: EMPTY_ACTIONS,
      ack_statuses: EMPTY_ACKS,
      deadline: 0,
      late_ack_acked: false,
      ...state,
    },
  }

  // Stuttering is disallowed at the final reserved seq while a request is
  // open, so a finite request cannot park before its ACK/default/resolve path.
  action stutter = all {
    state.request_state == NoRequest or state.host_seq + 1 < state.seq_reservation_end,
    state' = { previous_host_seq: state.host_seq, ...state },
  }

  action step = any {
    open_single_request,
    open_decision_group,
    submit_single_pass,
    submit_single_reach,
    submit_group_one_pass,
    submit_group_one_call,
    submit_group_two_pass,
    submit_group_two_call,
    ack_or_default,
    tick,
    resolve_group,
    stutter,
  }

  // ------------------------------------------------------------------------
  // Temporal properties under the stable-connection assumption
  // ------------------------------------------------------------------------

  temporal stable_connection_is_preserved: bool = always(state.stable_connection)

  temporal group_resolves_under_stable_connection: bool =
    weakFair(resolve_group, state).implies(
      always(state.group_state == GroupClosed implies eventually(state.group_state == GroupResolved))
    )

  temporal timeout_closes_under_stable_connection: bool =
    weakFair(ack_or_default, state).implies(
      always(
        (state.request_state == RequestOpen and state.deadline == 0)
          implies eventually(state.group_state == GroupClosed or state.group_state == GroupResolved)
      )
    )

  temporal late_ack_survives_default_under_stable_connection: bool =
    weakFair(ack_or_default, state).implies(
      always(late_ack_pending implies eventually(state.late_ack_acked))
    )
}
