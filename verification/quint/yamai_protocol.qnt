// A small, finite Quint model of the YAMAI control-flow protocol.
//
// This is a limited control-flow abstraction, not a JSON parser, wire
// protocol implementation, or mahjong rules engine.  It models one live
// request transaction at a time, with either one member or a two-member
// decision group.  The score scale is deliberately small so that TLC can
// exhaustively enumerate the state space.
//
// The model covers the control properties of YRC 0003 that are useful here:
// session lifecycle, host seq allocation, per-member request/action/ack
// lifecycle, group closure and atomic resolution, timeout/default handling,
// resume transport state, snapshot sequence replacement, and the
// score/kyotaku conservation equation.

module yamai_protocol {
  type SessionState = Idle | Active | Ended
  type TransportState = Connected | Disconnected
  type RequestState = NoRequest | RequestOpen | RequestAcked
  type GroupState = NoGroup | GroupOpen | GroupClosed | GroupResolved

  // Only statuses that this finite model can reach are included.  Wire-level
  // superseded/stale/rejected handling is intentionally outside this model.
  type AckStatus = AckNone | Accepted | Passed | Defaulted
  type ActionId = NoAction | PassAction | ReachAction | CallAction | DefaultAction

  pure val SEATS: Set[int] = Set(0, 1, 2, 3)
  pure val SINGLE_SEAT: int = 0
  pure val GROUP_SEATS: Set[int] = Set(1, 2)
  pure val ACTION_IDS: Set[ActionId] = Set(NoAction, PassAction, ReachAction, CallAction, DefaultAction)

  // All domains are finite.  A real YAMAI host uses unbounded positive seq
  // and request IDs subject to the wire resource limits; these are bounded
  // only for model checking.
  pure val MAX_SEQ: int = 12
  pure val MAX_TIMEOUT: int = 2
  pure val MAX_REQUEST_ID: int = 4
  pure val MAX_TOKEN_VERSION: int = 3
  pure val TOTAL_POINTS: int = 100
  pure val STARTING_POINTS: int = 25
  pure val RIICHI_STICK: int = 1
  pure val SINGLE_RESERVATION: int = 3
  pure val GROUP_RESERVATION: int = 5

  pure val EMPTY_REQUEST_IDS: int -> int = Map(0 -> 0, 1 -> 0, 2 -> 0, 3 -> 0)
  pure val EMPTY_ACTIONS: int -> ActionId = Map(0 -> NoAction, 1 -> NoAction, 2 -> NoAction, 3 -> NoAction)
  pure val EMPTY_ACKS: int -> AckStatus = Map(0 -> AckNone, 1 -> AckNone, 2 -> AckNone, 3 -> AckNone)

  type ProtocolState = {
    session: SessionState,
    transport: TransportState,
    host_seq: int,
    previous_host_seq: int,
    applied_seq: int,
    request_state: RequestState,
    group_state: GroupState,
    pending_members: Set[int],
    responded_members: Set[int],
    terminal_members: Set[int],
    defaulted_members: Set[int],
    request_ids: int -> int,
    submitted_actions: int -> ActionId,
    ack_statuses: int -> AckStatus,
    late_ack_after_default: bool,
    deadline: int,
    next_request_id: int,
    seq_reservation_end: int,
    resume_token_version: int,
    resume_available: bool,
    resumed: bool,
    snapshot_seq: int,
    snapshot_replaces_through_seq: int,
    score_0: int,
    score_1: int,
    score_2: int,
    score_3: int,
    kyotaku: int,
  }

  var state: ProtocolState

  // ------------------------------------------------------------------------
  // State predicates and invariants
  // ------------------------------------------------------------------------

  val score_total: int = state.score_0 + state.score_1 + state.score_2 + state.score_3

  // YRC 0003's score/kyotaku conservation equation, using the abstract unit
  // scale above instead of real mahjong points.
  val score_conservation: bool =
    score_total + state.kyotaku * RIICHI_STICK == TOTAL_POINTS

  val type_ok: bool = and {
    state.session.in(Set(Idle, Active, Ended)),
    state.transport.in(Set(Connected, Disconnected)),
    state.host_seq.in(0.to(MAX_SEQ)),
    state.previous_host_seq.in(0.to(MAX_SEQ)),
    state.applied_seq.in(0.to(MAX_SEQ)),
    state.applied_seq <= state.host_seq,
    state.request_state.in(Set(NoRequest, RequestOpen, RequestAcked)),
    state.group_state.in(Set(NoGroup, GroupOpen, GroupClosed, GroupResolved)),
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
    state.seq_reservation_end.in(0.to(MAX_SEQ)),
    state.resume_token_version.in(0.to(MAX_TOKEN_VERSION)),
    state.snapshot_seq.in(0.to(MAX_SEQ)),
    state.snapshot_replaces_through_seq.in(0.to(MAX_SEQ)),
    state.score_0.in(0.to(TOTAL_POINTS)),
    state.score_1.in(0.to(TOTAL_POINTS)),
    state.score_2.in(0.to(TOTAL_POINTS)),
    state.score_3.in(0.to(TOTAL_POINTS)),
    state.kyotaku.in(0.to(TOTAL_POINTS)),
  }

  // This is an explicit non-decreasing relation for every transition.  Each
  // host-message action records the old seq in previous_host_seq, while a
  // player/clock/transport action records the current seq there.
  val host_seq_non_decreasing: bool = and {
    state.host_seq >= state.previous_host_seq,
    state.host_seq <= state.seq_reservation_end,
  }

  val lifecycle_invariant: bool = and {
    state.session == Idle implies state.request_state == NoRequest,
    state.session == Idle implies state.group_state == NoGroup,
    state.session == Ended implies state.request_state == NoRequest,
    state.session == Ended implies state.group_state == NoGroup,
  }

  val request_invariant: bool = and {
    state.request_state == RequestOpen implies state.group_state == GroupOpen,
    state.group_state == GroupOpen implies state.request_state == RequestOpen,
    state.group_state == GroupClosed implies state.request_state == RequestAcked,
    state.group_state == GroupResolved implies state.request_state == NoRequest,
    state.request_state == NoRequest implies state.pending_members == Set(),
    state.group_state == GroupClosed implies state.terminal_members == state.pending_members,
    state.group_state == GroupOpen implies state.pending_members.size() > 0,
    state.request_state != NoRequest implies state.host_seq < state.seq_reservation_end,
    state.pending_members.forall(seat => state.request_ids.get(seat) > 0),
    state.pending_members == GROUP_SEATS implies state.request_ids.get(1) != state.request_ids.get(2),
    state.pending_members.exclude(state.responded_members).forall(seat => state.submitted_actions.get(seat) == NoAction),
    // A response that is not terminal (for example, an ACK still in flight)
    // retains a real submitted action and cannot be replaced by DefaultAction.
    state.responded_members.exclude(state.terminal_members).forall(seat => state.submitted_actions.get(seat).in(Set(PassAction, ReachAction, CallAction))),
    state.responded_members.exclude(state.defaulted_members).forall(seat => state.submitted_actions.get(seat).in(Set(PassAction, ReachAction, CallAction))),
    state.defaulted_members.forall(seat => state.submitted_actions.get(seat) == DefaultAction),
    state.terminal_members.forall(seat => state.ack_statuses.get(seat) != AckNone),
    state.defaulted_members.forall(seat => state.ack_statuses.get(seat) == Defaulted),
    state.pending_members.exclude(state.terminal_members).forall(seat => state.ack_statuses.get(seat) == AckNone),
  }

  val snapshot_invariant: bool = and {
    state.snapshot_seq == 0 or
      state.snapshot_seq == state.snapshot_replaces_through_seq + 1,
    state.snapshot_replaces_through_seq <= state.host_seq,
    state.snapshot_seq <= MAX_SEQ,
  }

  val protocol_invariant: bool = and {
    type_ok,
    host_seq_non_decreasing,
    lifecycle_invariant,
    request_invariant,
    snapshot_invariant,
    score_conservation,
  }

  // Witness predicates are useful for checking that the liveness antecedents
  // are reachable; they are not invariants and are not part of protocol rules.
  val witness_request_open: bool = state.group_state == GroupOpen
  val witness_group_closed: bool = state.group_state == GroupClosed
  val witness_defaulted: bool = state.defaulted_members.size() > 0
  val witness_late_action_pending: bool =
    state.responded_members.exclude(state.terminal_members).size() > 0
  val witness_partial_default: bool = and {
    state.defaulted_members.size() > 0,
    state.responded_members.exclude(state.defaulted_members).size() > 0,
  }
  val witness_late_ack_after_default: bool = state.late_ack_after_default
  val witness_resumed: bool = state.resumed
  val witness_snapshot: bool = state.snapshot_seq > 0

  // ------------------------------------------------------------------------
  // Initial state and transitions
  // ------------------------------------------------------------------------

  action init = state' = {
    session: Idle,
    transport: Connected,
    host_seq: 0,
    previous_host_seq: 0,
    applied_seq: 0,
    request_state: NoRequest,
    group_state: NoGroup,
    pending_members: Set(),
    responded_members: Set(),
    terminal_members: Set(),
    defaulted_members: Set(),
    request_ids: EMPTY_REQUEST_IDS,
    submitted_actions: EMPTY_ACTIONS,
    ack_statuses: EMPTY_ACKS,
    late_ack_after_default: false,
    deadline: 0,
    next_request_id: 0,
    seq_reservation_end: 0,
    resume_token_version: 0,
    resume_available: true,
    resumed: false,
    snapshot_seq: 0,
    snapshot_replaces_through_seq: 0,
    score_0: STARTING_POINTS,
    score_1: STARTING_POINTS,
    score_2: STARTING_POINTS,
    score_3: STARTING_POINTS,
    kyotaku: 0,
  }

  // hello/join/welcome are negotiation messages and are intentionally not
  // modelled; this action represents the resulting active session.
  action open_session = all {
    state.session == Idle,
    state' = {
      session: Active,
      transport: Connected,
      previous_host_seq: state.host_seq,
      resumed: false,
      ...state,
    },
  }

  // One ordinary event message.  No state event can be emitted during an
  // unresolved request or a closed-but-not-yet-resolved group.
  action host_event = all {
    state.session == Active,
    state.transport == Connected,
    state.request_state == NoRequest,
    state.group_state.in(Set(NoGroup, GroupResolved)),
    state.host_seq < MAX_SEQ,
    state' = {
      host_seq: state.host_seq + 1,
      previous_host_seq: state.host_seq,
      applied_seq: state.host_seq + 1,
      group_state: NoGroup,
      seq_reservation_end: state.host_seq + 1,
      ...state,
    },
  }

  // Starting a request reserves the complete host-seq budget: open request,
  // one ack for each possible member, and the adopted resolve event.
  action open_single_request = all {
    state.session == Active,
    state.transport == Connected,
    state.request_state == NoRequest,
    state.group_state == NoGroup,
    state.next_request_id + 1 <= MAX_REQUEST_ID,
    state.host_seq + SINGLE_RESERVATION <= MAX_SEQ,
    state' = {
      host_seq: state.host_seq + 1,
      previous_host_seq: state.host_seq,
      applied_seq: state.host_seq + 1,
      request_state: RequestOpen,
      group_state: GroupOpen,
      pending_members: Set(SINGLE_SEAT),
      responded_members: Set(),
      terminal_members: Set(),
      defaulted_members: Set(),
      request_ids: EMPTY_REQUEST_IDS.set(SINGLE_SEAT, state.next_request_id + 1),
      submitted_actions: EMPTY_ACTIONS,
      ack_statuses: EMPTY_ACKS,
      late_ack_after_default: false,
      deadline: MAX_TIMEOUT,
      next_request_id: state.next_request_id + 1,
      seq_reservation_end: state.host_seq + SINGLE_RESERVATION,
      ...state,
    },
  }

  action open_decision_group = all {
    state.session == Active,
    state.transport == Connected,
    state.request_state == NoRequest,
    state.group_state == NoGroup,
    state.next_request_id + 2 <= MAX_REQUEST_ID,
    state.host_seq + GROUP_RESERVATION <= MAX_SEQ,
    state' = {
      host_seq: state.host_seq + 2,
      previous_host_seq: state.host_seq,
      applied_seq: state.host_seq + 2,
      request_state: RequestOpen,
      group_state: GroupOpen,
      pending_members: GROUP_SEATS,
      responded_members: Set(),
      terminal_members: Set(),
      defaulted_members: Set(),
      request_ids: EMPTY_REQUEST_IDS.set(1, state.next_request_id + 1).set(2, state.next_request_id + 2),
      submitted_actions: EMPTY_ACTIONS,
      ack_statuses: EMPTY_ACKS,
      late_ack_after_default: false,
      deadline: MAX_TIMEOUT,
      next_request_id: state.next_request_id + 2,
      seq_reservation_end: state.host_seq + GROUP_RESERVATION,
      ...state,
    },
  }

  // Player actions are stored per member.  A submitted action cannot be
  // changed by a later timeout default because default_action only touches
  // members that are not already terminal.
  action submit_action(seat, action_id) = all {
    state.session == Active,
    state.request_state == RequestOpen,
    state.group_state == GroupOpen,
    // Once the bounded deadline reaches zero, no new action may enter the
    // model; timeout/default processing has priority over late submissions.
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

  action submit_single_pass = submit_action(SINGLE_SEAT, PassAction)
  action submit_single_reach = submit_action(SINGLE_SEAT, ReachAction)
  action submit_group_one_pass = submit_action(1, PassAction)
  action submit_group_one_call = submit_action(1, CallAction)
  action submit_group_two_pass = submit_action(2, PassAction)
  action submit_group_two_call = submit_action(2, CallAction)

  // The ack is also per member.  The final ack closes the group; resolve_group
  // is the only action that can then adopt the chosen transaction.  These are
  // explicit finite actions rather than a parameterized action so that each
  // member's request and terminal status is visible in TLC traces.
  action ack_single = all {
    state.session == Active,
    state.request_state == RequestOpen,
    state.pending_members.contains(0),
    state.responded_members.contains(0),
    not(state.terminal_members.contains(0)),
    state.host_seq < state.seq_reservation_end,
    state' = {
      host_seq: state.host_seq + 1,
      previous_host_seq: state.host_seq,
      applied_seq: state.host_seq + 1,
      terminal_members: state.terminal_members.union(Set(0)),
      ack_statuses: state.ack_statuses.set(0, if (state.submitted_actions.get(0) == PassAction) Passed else Accepted),
      late_ack_after_default: state.late_ack_after_default or state.defaulted_members.size() > 0,
      request_state: if (state.terminal_members.union(Set(0)) == state.pending_members) RequestAcked else RequestOpen,
      group_state: if (state.terminal_members.union(Set(0)) == state.pending_members) GroupClosed else GroupOpen,
      ...state,
    },
  }

  action ack_group_one = all {
    state.session == Active,
    state.request_state == RequestOpen,
    state.pending_members.contains(1),
    state.responded_members.contains(1),
    not(state.terminal_members.contains(1)),
    state.host_seq < state.seq_reservation_end,
    state' = {
      host_seq: state.host_seq + 1,
      previous_host_seq: state.host_seq,
      applied_seq: state.host_seq + 1,
      terminal_members: state.terminal_members.union(Set(1)),
      ack_statuses: state.ack_statuses.set(1, if (state.submitted_actions.get(1) == PassAction) Passed else Accepted),
      late_ack_after_default: state.late_ack_after_default or state.defaulted_members.size() > 0,
      request_state: if (state.terminal_members.union(Set(1)) == state.pending_members) RequestAcked else RequestOpen,
      group_state: if (state.terminal_members.union(Set(1)) == state.pending_members) GroupClosed else GroupOpen,
      ...state,
    },
  }

  action ack_group_two = all {
    state.session == Active,
    state.request_state == RequestOpen,
    state.pending_members.contains(2),
    state.responded_members.contains(2),
    not(state.terminal_members.contains(2)),
    state.host_seq < state.seq_reservation_end,
    state' = {
      host_seq: state.host_seq + 1,
      previous_host_seq: state.host_seq,
      applied_seq: state.host_seq + 1,
      terminal_members: state.terminal_members.union(Set(2)),
      ack_statuses: state.ack_statuses.set(2, if (state.submitted_actions.get(2) == PassAction) Passed else Accepted),
      late_ack_after_default: state.late_ack_after_default or state.defaulted_members.size() > 0,
      request_state: if (state.terminal_members.union(Set(2)) == state.pending_members) RequestAcked else RequestOpen,
      group_state: if (state.terminal_members.union(Set(2)) == state.pending_members) GroupClosed else GroupOpen,
      ...state,
    },
  }

  // A bounded monotonic clock.  Host processing may continue while transport
  // is disconnected; the concrete protocol replays its resulting messages.
  action tick = all {
    state.session == Active,
    state.request_state == RequestOpen,
    state.deadline > 0,
    state' = {
      deadline: state.deadline - 1,
      previous_host_seq: state.host_seq,
      ...state,
    },
  }

  action default_action = {
    // Only members that have not sent an action are defaulted.  A submitted
    // action can have an ACK still in flight; it must remain available to its
    // later per-member ack action and must never be overwritten here.
    val missing_members = state.pending_members.exclude(state.responded_members)
    val next_actions_0 = if (missing_members.contains(0)) state.submitted_actions.set(0, DefaultAction) else state.submitted_actions
    val next_actions_1 = if (missing_members.contains(1)) next_actions_0.set(1, DefaultAction) else next_actions_0
    val next_actions_2 = if (missing_members.contains(2)) next_actions_1.set(2, DefaultAction) else next_actions_1
    val next_actions = if (missing_members.contains(3)) next_actions_2.set(3, DefaultAction) else next_actions_2
    val next_acks_0 = if (missing_members.contains(0)) state.ack_statuses.set(0, Defaulted) else state.ack_statuses
    val next_acks_1 = if (missing_members.contains(1)) next_acks_0.set(1, Defaulted) else next_acks_0
    val next_acks_2 = if (missing_members.contains(2)) next_acks_1.set(2, Defaulted) else next_acks_1
    val next_acks = if (missing_members.contains(3)) next_acks_2.set(3, Defaulted) else next_acks_2
    all {
      state.session == Active,
      state.request_state == RequestOpen,
      state.deadline == 0,
      missing_members.size() > 0,
      state.host_seq + missing_members.size() <= state.seq_reservation_end,
      state' = {
        host_seq: state.host_seq + missing_members.size(),
        previous_host_seq: state.host_seq,
        applied_seq: state.host_seq + missing_members.size(),
      responded_members: state.responded_members.union(missing_members),
      terminal_members: state.terminal_members.union(missing_members),
      defaulted_members: state.defaulted_members.union(missing_members),
      submitted_actions: next_actions,
      ack_statuses: next_acks,
      request_state: if (state.terminal_members.union(missing_members) == state.pending_members) RequestAcked else RequestOpen,
      group_state: if (state.terminal_members.union(missing_members) == state.pending_members) GroupClosed else GroupOpen,
      ...state,
      },
    }
  }

  // At deadline, either default an unresponded member or ack a response that
  // arrived before the deadline but whose ACK was not emitted yet.  Keeping
  // this union as one fairness target makes the timeout liveness property
  // cover both legal paths.
  action ack_or_default = any {
    ack_single,
    ack_group_one,
    ack_group_two,
    default_action,
  }

  // Resolve consumes the reserved final host seq.  The per-member maps are
  // cleared only after all action/ack/default states are terminal.
  action resolve_group = all {
    state.session == Active,
    state.group_state == GroupClosed,
    state.request_state == RequestAcked,
    state.terminal_members == state.pending_members,
    state.host_seq < state.seq_reservation_end,
    not(state.submitted_actions.get(0) == ReachAction) or state.score_0 >= RIICHI_STICK,
    state' = {
      host_seq: state.host_seq + 1,
      previous_host_seq: state.host_seq,
      applied_seq: state.host_seq + 1,
      request_state: NoRequest,
      group_state: GroupResolved,
      pending_members: Set(),
      responded_members: Set(),
      terminal_members: Set(),
      defaulted_members: Set(),
      request_ids: EMPTY_REQUEST_IDS,
      submitted_actions: EMPTY_ACTIONS,
      ack_statuses: EMPTY_ACKS,
      deadline: 0,
      seq_reservation_end: state.host_seq + 1,
      score_0: if (state.submitted_actions.get(0) == ReachAction) state.score_0 - RIICHI_STICK else state.score_0,
      kyotaku: if (state.submitted_actions.get(0) == ReachAction) state.kyotaku + 1 else state.kyotaku,
      ...state,
    },
  }

  // Abstract the later award of escrowed kyotaku to seat 1.
  action settle_kyotaku = all {
    state.session == Active,
    state.group_state == GroupResolved,
    state.kyotaku > 0,
    state.host_seq < MAX_SEQ,
    state' = {
      host_seq: state.host_seq + 1,
      previous_host_seq: state.host_seq,
      applied_seq: state.host_seq + 1,
      group_state: NoGroup,
      seq_reservation_end: state.host_seq + 1,
      score_1: state.score_1 + state.kyotaku * RIICHI_STICK,
      kyotaku: 0,
      ...state,
    },
  }

  // Transport loss does not change session or host seq.  resume_replay keeps
  // request maps untouched; replaying their contents is intentionally outside
  // this control-flow abstraction.
  action disconnect = all {
    state.session == Active,
    state.transport == Connected,
    state' = {
      transport: Disconnected,
      resume_available: true,
      previous_host_seq: state.host_seq,
      ...state,
    },
  }

  action resume_replay = all {
    state.session == Active,
    state.transport == Disconnected,
    state.resume_available,
    state' = {
      transport: Connected,
      resumed: true,
      resume_available: false,
      previous_host_seq: state.host_seq,
      resume_token_version: if (state.resume_token_version == MAX_TOKEN_VERSION) 0 else state.resume_token_version + 1,
      ...state,
    },
  }

  // Snapshot is modelled only between request transactions.  It allocates a
  // new host seq and replaces through the immediately preceding applied seq.
  action emit_snapshot = all {
    state.session == Active,
    state.transport == Connected,
    state.request_state == NoRequest,
    state.group_state.in(Set(NoGroup, GroupResolved)),
    state.snapshot_seq == 0,
    state.host_seq < MAX_SEQ,
    state' = {
      host_seq: state.host_seq + 1,
      previous_host_seq: state.host_seq,
      applied_seq: state.host_seq + 1,
      snapshot_seq: state.host_seq + 1,
      snapshot_replaces_through_seq: state.applied_seq,
      group_state: NoGroup,
      seq_reservation_end: state.host_seq + 1,
      ...state,
    },
  }

  action end_game = all {
    state.session == Active,
    state.request_state == NoRequest,
    state.group_state.in(Set(NoGroup, GroupResolved)),
    state.host_seq < MAX_SEQ,
    state' = {
      session: Ended,
      group_state: NoGroup,
      host_seq: state.host_seq + 1,
      previous_host_seq: state.host_seq,
      applied_seq: state.host_seq + 1,
      seq_reservation_end: state.host_seq + 1,
      ...state,
    },
  }

  // Stuttering is forbidden at the final reserved seq of an open request.
  // This makes the reservation observable: the model cannot park forever
  // after consuming the last seq needed by ack/resolve.
  action stutter = all {
    state.request_state == NoRequest or state.host_seq + 1 < state.seq_reservation_end,
    state' = { previous_host_seq: state.host_seq, ...state },
  }

  action step = any {
    open_session,
    host_event,
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
    settle_kyotaku,
    disconnect,
    resume_replay,
    emit_snapshot,
    end_game,
    stutter,
  }

  // ------------------------------------------------------------------------
  // Temporal properties
  // ------------------------------------------------------------------------

  // The action definitions and host_seq_non_decreasing invariant establish
  // per-transition monotonicity.  This temporal property checks the bound on
  // every state without using a next-state action inside TLC's [] predicate.
  temporal host_seq_bounded: bool =
    always(state.host_seq.in(0.to(MAX_SEQ)))

  temporal ended_state_is_quiescent: bool =
    always(
      state.session == Ended implies and {
        state.request_state == NoRequest,
        state.group_state == NoGroup,
      }
    )

  // The antecedent is intentionally not weakened by a seq-capacity clause:
  // opening a transaction reserves every seq needed to reach resolution.
  temporal group_resolves_under_weak_fairness: bool =
    weakFair(resolve_group, state).implies(
      always(state.group_state == GroupClosed implies eventually(state.group_state == GroupResolved))
    )

  temporal timeout_closes_under_weak_fairness: bool =
    weakFair(ack_or_default, state).implies(
      always(
        (state.request_state == RequestOpen and state.deadline == 0)
          implies eventually(state.group_state == GroupClosed)
      )
    )

  // If the session remains active while disconnected, a resumed connection
  // is eventually available under the weak-fair resume assumption.  A game
  // that ends while disconnected also discharges the obligation.
  temporal resume_returns_under_weak_fairness: bool =
    weakFair(resume_replay, state).implies(
      always(
        (state.session == Active and state.transport == Disconnected)
          implies eventually(state.transport == Connected or state.session == Ended)
      )
    )
}
