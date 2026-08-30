// Small exhaustive model for resume delivery and host-internal request work.
//
// This model intentionally omits negotiation, JSON, scoring, and the full
// event history.  It keeps one request and one optional disconnect so TLC can
// exhaustively check the boundary between host-side progress and peer
// delivery.  Host tick/default/ACK/resolve actions have no transport or
// resume-phase guard; unavailable output is represented by a finite backlog
// range and is consumed exactly once by replay or snapshot delivery.

module yamai_resume_delivery {
  type TransportState = Connected | Disconnected
  type RequestState = NoRequest | RequestOpen | RequestAcked
  type GroupState = NoGroup | GroupOpen | GroupClosed | GroupResolved
  type ActionId = NoAction | PassAction | DefaultAction
  type AckStatus = AckNone | Passed | Accepted | Defaulted
  type SeqDisposition = NoSeq | Gap | Replayed
  type ResumePhase = NoResume | ResumeReady | ReplayPending | SnapshotPending | ResumeComplete
  type DeliveryKind = NoDelivery | GapDelivery | PendingReplayDelivery | SnapshotDelivery
  type WirePayload = EventPayload | SnapshotPayload

  pure val MAX_SEQ: int = 6
  pure val MAX_TIMEOUT: int = 2
  pure val REQUEST_RESERVATION: int = 4
  pure val REQUEST_ID: int = 1

  type DeliveryState = {
    transport: TransportState,
    disconnect_used: bool,
    host_seq: int,
    previous_host_seq: int,
    applied_seq: int,
    wire_available: bool,
    wire_seq: int,
    wire_payload: WirePayload,
    applied_payload: WirePayload,
    seq_disposition: SeqDisposition,
    gap_pending: bool,
    gap_expected: int,
    gap_received: int,
    replay_in_progress: bool,
    replay_from_seq: int,
    replay_to_seq: int,
    gap_recovered: bool,
    request_started: bool,
    request_state: RequestState,
    group_state: GroupState,
    request_id: int,
    submitted_action: ActionId,
    ack_status: AckStatus,
    responded: bool,
    terminal: bool,
    defaulted: bool,
    deadline: int,
    seq_reservation_end: int,
    resume_phase: ResumePhase,
    resume_available: bool,
    resume_saved_pending: bool,
    resume_saved_request_id: int,
    resume_saved_action: ActionId,
    resume_saved_deadline: int,
    snapshot_saved_pending: bool,
    snapshot_saved_request_id: int,
    snapshot_saved_action: ActionId,
    snapshot_saved_deadline: int,
    snapshot_seq: int,
    snapshot_replaces_through_seq: int,
    backlog_pending: bool,
    backlog_from_seq: int,
    backlog_to_seq: int,
    delivery_kind: DeliveryKind,
    resume_delivery_count: int,
    tick_while_disconnected: bool,
    defaulted_while_disconnected: bool,
    acked_while_disconnected: bool,
    resolved_while_disconnected: bool,
  }

  var state: DeliveryState

  // Any host message produced while disconnected or while a resume transfer
  // is pending is retained in one finite contiguous backlog range.
  val backlog_capture_active: bool = or {
    state.transport == Disconnected,
    state.resume_phase == ReplayPending,
    state.resume_phase == SnapshotPending,
  }

  val backlog_next_from_seq: int =
    if (state.backlog_pending) state.backlog_from_seq else state.host_seq + 1

  // ------------------------------------------------------------------------
  // Safety and witnesses
  // ------------------------------------------------------------------------

  val type_ok: bool = and {
    state.transport.in(Set(Connected, Disconnected)),
    state.host_seq.in(0.to(MAX_SEQ)),
    state.previous_host_seq.in(0.to(MAX_SEQ)),
    state.applied_seq.in(0.to(MAX_SEQ)),
    state.applied_seq <= state.host_seq,
    state.wire_seq.in(0.to(MAX_SEQ)),
    state.gap_expected.in(0.to(MAX_SEQ)),
    state.gap_received.in(0.to(MAX_SEQ)),
    state.replay_from_seq.in(0.to(MAX_SEQ)),
    state.replay_to_seq.in(0.to(MAX_SEQ)),
    state.request_state.in(Set(NoRequest, RequestOpen, RequestAcked)),
    state.group_state.in(Set(NoGroup, GroupOpen, GroupClosed, GroupResolved)),
    state.request_id.in(0.to(REQUEST_ID)),
    state.submitted_action.in(Set(NoAction, PassAction, DefaultAction)),
    state.ack_status.in(Set(AckNone, Passed, Accepted, Defaulted)),
    state.deadline.in(0.to(MAX_TIMEOUT)),
    state.seq_reservation_end.in(0.to(MAX_SEQ)),
    state.resume_phase.in(Set(NoResume, ResumeReady, ReplayPending, SnapshotPending, ResumeComplete)),
    state.resume_saved_request_id.in(0.to(REQUEST_ID)),
    state.snapshot_saved_request_id.in(0.to(REQUEST_ID)),
    state.snapshot_saved_action.in(Set(NoAction, PassAction, DefaultAction)),
    state.snapshot_seq.in(0.to(MAX_SEQ)),
    state.snapshot_replaces_through_seq.in(0.to(MAX_SEQ)),
    state.backlog_from_seq.in(0.to(MAX_SEQ)),
    state.backlog_to_seq.in(0.to(MAX_SEQ)),
    state.backlog_to_seq <= state.host_seq,
    state.resume_delivery_count.in(Set(0, 1)),
  }

  val host_seq_non_decreasing: bool = and {
    state.host_seq >= state.previous_host_seq,
    state.host_seq <= state.seq_reservation_end or state.request_state == NoRequest,
  }

  val backlog_invariant: bool = and {
    state.backlog_pending implies state.backlog_from_seq > 0,
    state.backlog_pending implies state.backlog_from_seq <= state.backlog_to_seq,
    not(state.backlog_pending) implies state.backlog_from_seq == 0,
    not(state.backlog_pending) implies state.backlog_to_seq == 0,
    state.resume_phase.in(Set(ReplayPending, SnapshotPending)) implies state.backlog_pending,
    state.resume_delivery_count == 1 implies not(state.backlog_pending),
  }

  val gap_invariant: bool = and {
    state.gap_pending implies not(state.wire_available),
    state.gap_pending implies state.seq_disposition == Gap,
    state.gap_pending implies state.gap_received > state.gap_expected,
    state.replay_in_progress implies not(state.wire_available),
    state.replay_in_progress implies not(state.gap_pending),
    state.replay_in_progress implies state.replay_to_seq >= state.replay_from_seq,
    state.gap_recovered implies not(state.gap_pending),
    state.gap_recovered implies not(state.replay_in_progress),
  }

  val request_invariant: bool = and {
    state.request_state == RequestOpen implies state.group_state == GroupOpen,
    state.group_state == GroupOpen implies state.request_state == RequestOpen,
    state.group_state == GroupClosed implies state.request_state == RequestAcked,
    state.group_state == GroupResolved implies state.request_state == NoRequest,
    state.request_state == NoRequest implies not(state.responded),
    state.request_state == NoRequest implies not(state.terminal),
    state.request_state == NoRequest implies state.submitted_action == NoAction,
    state.defaulted implies state.terminal,
    state.defaulted implies state.submitted_action == DefaultAction,
    state.defaulted implies state.ack_status == Defaulted,
    state.terminal implies state.ack_status != AckNone,
    state.responded and not(state.terminal) implies state.submitted_action == PassAction,
    state.request_state == RequestOpen and not(state.terminal) implies state.ack_status == AckNone,
  }

  val pending_saved_invariant: bool = and {
    state.resume_saved_pending implies state.resume_saved_request_id == REQUEST_ID,
    state.resume_saved_pending implies state.resume_saved_action.in(Set(NoAction, PassAction, DefaultAction)),
    state.snapshot_saved_pending implies state.snapshot_saved_request_id == REQUEST_ID,
    state.snapshot_saved_pending implies state.snapshot_seq > 0,
    state.snapshot_saved_pending implies state.snapshot_seq == state.snapshot_replaces_through_seq + 1,
    state.snapshot_saved_pending implies state.snapshot_seq <= state.host_seq,
    state.resume_phase == ReplayPending implies state.delivery_kind == PendingReplayDelivery,
    state.resume_phase == SnapshotPending implies state.delivery_kind == SnapshotDelivery,
    state.resume_phase.in(Set(ReplayPending, SnapshotPending)) implies state.resume_delivery_count == 0,
    state.resume_delivery_count == 1 implies state.resume_phase == ResumeComplete,
  }

  // These flags are monotonic witnesses that the host continued internal
  // work while disconnected; they are also safety checks on those paths.
  val disconnected_internal_invariant: bool = and {
    state.tick_while_disconnected implies state.disconnect_used,
    state.defaulted_while_disconnected implies state.disconnect_used,
    state.acked_while_disconnected implies state.disconnect_used,
    state.resolved_while_disconnected implies state.disconnect_used,
    state.resolved_while_disconnected implies state.group_state.in(Set(GroupResolved, NoGroup)),
  }

  val protocol_invariant: bool = and {
    type_ok,
    host_seq_non_decreasing,
    backlog_invariant,
    gap_invariant,
    request_invariant,
    pending_saved_invariant,
    disconnected_internal_invariant,
  }

  val witness_disconnect_default: bool = state.defaulted_while_disconnected
  val witness_disconnect_resolve: bool = state.resolved_while_disconnected
  val witness_reconnect_replay: bool = state.resume_delivery_count == 1 and state.delivery_kind == NoDelivery
  val witness_snapshot_replacement: bool = state.snapshot_seq > 0 and state.resume_delivery_count == 1
  val witness_gap_recovery: bool = state.gap_recovered

  // ------------------------------------------------------------------------
  // Initial state and host/request transitions
  // ------------------------------------------------------------------------

  action init = state' = {
    transport: Connected,
    disconnect_used: false,
    host_seq: 0,
    previous_host_seq: 0,
    applied_seq: 0,
    wire_available: false,
    wire_seq: 0,
    wire_payload: EventPayload,
    applied_payload: EventPayload,
    seq_disposition: NoSeq,
    gap_pending: false,
    gap_expected: 0,
    gap_received: 0,
    replay_in_progress: false,
    replay_from_seq: 0,
    replay_to_seq: 0,
    gap_recovered: false,
    request_started: false,
    request_state: NoRequest,
    group_state: NoGroup,
    request_id: 0,
    submitted_action: NoAction,
    ack_status: AckNone,
    responded: false,
    terminal: false,
    defaulted: false,
    deadline: 0,
    seq_reservation_end: 0,
    resume_phase: NoResume,
    resume_available: false,
    resume_saved_pending: false,
    resume_saved_request_id: 0,
    resume_saved_action: NoAction,
    resume_saved_deadline: 0,
    snapshot_saved_pending: false,
    snapshot_saved_request_id: 0,
    snapshot_saved_action: NoAction,
    snapshot_saved_deadline: 0,
    snapshot_seq: 0,
    snapshot_replaces_through_seq: 0,
    backlog_pending: false,
    backlog_from_seq: 0,
    backlog_to_seq: 0,
    delivery_kind: NoDelivery,
    resume_delivery_count: 0,
    tick_while_disconnected: false,
    defaulted_while_disconnected: false,
    acked_while_disconnected: false,
    resolved_while_disconnected: false,
  }

  action open_request = all {
    state.transport == Connected,
    not(state.request_started),
    state.request_state == NoRequest,
    state.group_state == NoGroup,
    not(state.wire_available),
    not(state.gap_pending),
    not(state.replay_in_progress),
    state.host_seq + REQUEST_RESERVATION <= MAX_SEQ,
    state' = {
      request_started: true,
      request_state: RequestOpen,
      group_state: GroupOpen,
      request_id: REQUEST_ID,
      host_seq: state.host_seq + 1,
      previous_host_seq: state.host_seq,
      applied_seq: state.applied_seq + 1,
      submitted_action: NoAction,
      ack_status: AckNone,
      responded: false,
      terminal: false,
      defaulted: false,
      deadline: MAX_TIMEOUT,
      seq_reservation_end: state.host_seq + REQUEST_RESERVATION,
      ...state,
    },
  }

  action submit_pass = all {
    state.request_state == RequestOpen,
    state.group_state == GroupOpen,
    state.deadline > 0,
    not(state.responded),
    state' = {
      responded: true,
      submitted_action: PassAction,
      previous_host_seq: state.host_seq,
      ...state,
    },
  }

  action tick = all {
    state.request_state == RequestOpen,
    state.deadline > 0,
    state' = {
      deadline: state.deadline - 1,
      previous_host_seq: state.host_seq,
      tick_while_disconnected: state.tick_while_disconnected or state.transport == Disconnected,
      ...state,
    },
  }

  action ack_action = all {
    state.request_state == RequestOpen,
    state.responded,
    not(state.terminal),
    state.host_seq < state.seq_reservation_end,
    state' = {
      host_seq: state.host_seq + 1,
      previous_host_seq: state.host_seq,
      applied_seq: state.applied_seq + 1,
      terminal: true,
      ack_status: Passed,
      request_state: RequestAcked,
      group_state: GroupClosed,
      acked_while_disconnected: state.acked_while_disconnected or state.transport == Disconnected,
      backlog_pending: if (backlog_capture_active) true else state.backlog_pending,
      backlog_from_seq: if (backlog_capture_active) backlog_next_from_seq else state.backlog_from_seq,
      backlog_to_seq: if (backlog_capture_active) state.host_seq + 1 else state.backlog_to_seq,
      ...state,
    },
  }

  action default_action = all {
    state.request_state == RequestOpen,
    state.deadline == 0,
    not(state.responded),
    state.host_seq < state.seq_reservation_end,
    state' = {
      host_seq: state.host_seq + 1,
      previous_host_seq: state.host_seq,
      applied_seq: state.applied_seq + 1,
      responded: true,
      terminal: true,
      defaulted: true,
      submitted_action: DefaultAction,
      ack_status: Defaulted,
      request_state: RequestAcked,
      group_state: GroupClosed,
      defaulted_while_disconnected: state.defaulted_while_disconnected or state.transport == Disconnected,
      backlog_pending: if (backlog_capture_active) true else state.backlog_pending,
      backlog_from_seq: if (backlog_capture_active) backlog_next_from_seq else state.backlog_from_seq,
      backlog_to_seq: if (backlog_capture_active) state.host_seq + 1 else state.backlog_to_seq,
      ...state,
    },
  }

  action resolve_group = all {
    state.request_state == RequestAcked,
    state.group_state == GroupClosed,
    state.terminal,
    state.host_seq < state.seq_reservation_end,
    state' = {
      host_seq: state.host_seq + 1,
      previous_host_seq: state.host_seq,
      applied_seq: state.applied_seq + 1,
      request_state: NoRequest,
      group_state: GroupResolved,
      request_id: 0,
      submitted_action: NoAction,
      ack_status: AckNone,
      responded: false,
      terminal: false,
      defaulted: false,
      deadline: 0,
      seq_reservation_end: state.host_seq + 1,
      resolved_while_disconnected: state.resolved_while_disconnected or state.transport == Disconnected,
      backlog_pending: if (backlog_capture_active) true else state.backlog_pending,
      backlog_from_seq: if (backlog_capture_active) backlog_next_from_seq else state.backlog_from_seq,
      backlog_to_seq: if (backlog_capture_active) state.host_seq + 1 else state.backlog_to_seq,
      ...state,
    },
  }

  action internal_progress = any {
    tick,
    ack_action,
    default_action,
    resolve_group,
  }

  // ------------------------------------------------------------------------
  // Disconnect/resume and backlog delivery
  // ------------------------------------------------------------------------

  action disconnect = all {
    state.transport == Connected,
    not(state.disconnect_used),
    not(state.gap_pending),
    not(state.replay_in_progress),
    state.resume_phase.in(Set(NoResume, ResumeComplete)),
    state' = {
      transport: Disconnected,
      disconnect_used: true,
      resume_available: true,
      resume_phase: ResumeReady,
      resume_saved_pending: state.request_state == RequestOpen,
      resume_saved_request_id: if (state.request_state == RequestOpen) state.request_id else 0,
      resume_saved_action: if (state.request_state == RequestOpen) state.submitted_action else NoAction,
      resume_saved_deadline: if (state.request_state == RequestOpen) state.deadline else 0,
      backlog_pending: if (state.request_state == RequestOpen) true else state.backlog_pending,
      backlog_from_seq: if (state.request_state == RequestOpen)
        (if (state.backlog_pending) state.backlog_from_seq else state.host_seq)
        else state.backlog_from_seq,
      backlog_to_seq: if (state.request_state == RequestOpen) state.host_seq else state.backlog_to_seq,
      previous_host_seq: state.host_seq,
      ...state,
    },
  }

  action resume = all {
    state.transport == Disconnected,
    state.resume_available,
    state' = {
      transport: Connected,
      resume_available: false,
      previous_host_seq: state.host_seq,
      ...state,
    },
  }

  action begin_pending_replay = all {
    state.transport == Connected,
    state.resume_phase == ResumeReady,
    state.resume_saved_pending or state.backlog_pending,
    not(state.gap_pending),
    not(state.replay_in_progress),
    state.resume_delivery_count == 0,
    state' = {
      resume_phase: ReplayPending,
      delivery_kind: PendingReplayDelivery,
      previous_host_seq: state.host_seq,
      ...state,
    },
  }

  action finish_pending_replay = all {
    state.transport == Connected,
    state.resume_phase == ReplayPending,
    state.delivery_kind == PendingReplayDelivery,
    state.resume_delivery_count == 0,
    state' = {
      resume_phase: ResumeComplete,
      delivery_kind: NoDelivery,
      resume_delivery_count: 1,
      resume_saved_pending: false,
      backlog_pending: false,
      backlog_from_seq: 0,
      backlog_to_seq: 0,
      previous_host_seq: state.host_seq,
      ...state,
    },
  }

  action emit_pending_snapshot = all {
    state.transport == Connected,
    state.resume_phase == ResumeReady,
    state.resume_saved_pending,
    not(state.gap_pending),
    not(state.replay_in_progress),
    state.resume_delivery_count == 0,
    state.snapshot_seq == 0,
    state.request_state == RequestOpen,
    state.host_seq + 1 < state.seq_reservation_end,
    state' = {
      host_seq: state.host_seq + 1,
      previous_host_seq: state.host_seq,
      applied_seq: state.applied_seq + 1,
      resume_phase: SnapshotPending,
      delivery_kind: SnapshotDelivery,
      snapshot_seq: state.host_seq + 1,
      snapshot_replaces_through_seq: state.applied_seq,
      snapshot_saved_pending: true,
      snapshot_saved_request_id: state.resume_saved_request_id,
      snapshot_saved_action: state.resume_saved_action,
      snapshot_saved_deadline: state.resume_saved_deadline,
      backlog_pending: true,
      backlog_from_seq: if (state.backlog_pending) state.backlog_from_seq else state.host_seq + 1,
      backlog_to_seq: state.host_seq + 1,
      ...state,
    },
  }

  action finish_pending_snapshot = all {
    state.transport == Connected,
    state.resume_phase == SnapshotPending,
    state.delivery_kind == SnapshotDelivery,
    state.resume_delivery_count == 0,
    state' = {
      resume_phase: ResumeComplete,
      delivery_kind: NoDelivery,
      resume_delivery_count: 1,
      resume_saved_pending: false,
      snapshot_saved_pending: false,
      backlog_pending: false,
      backlog_from_seq: 0,
      backlog_to_seq: 0,
      previous_host_seq: state.host_seq,
      ...state,
    },
  }

  action resume_delivery_progress = any {
    resume,
    finish_pending_replay,
    finish_pending_snapshot,
  }

  // ------------------------------------------------------------------------
  // Sequence gap/replay
  // ------------------------------------------------------------------------

  action host_emit_gap = all {
    state.transport == Connected,
    state.request_state == NoRequest,
    state.group_state.in(Set(NoGroup, GroupResolved)),
    not(state.wire_available),
    not(state.gap_pending),
    not(state.replay_in_progress),
    not(state.gap_recovered),
    state.resume_phase != ReplayPending,
    state.resume_phase != SnapshotPending,
    state.host_seq == state.applied_seq,
    state.host_seq + 2 <= MAX_SEQ,
    state' = {
      host_seq: state.host_seq + 2,
      previous_host_seq: state.host_seq,
      wire_available: true,
      wire_seq: state.host_seq + 2,
      wire_payload: EventPayload,
      seq_disposition: NoSeq,
      ...state,
    },
  }

  action player_detect_gap = all {
    state.transport == Connected,
    state.resume_phase != ReplayPending,
    state.resume_phase != SnapshotPending,
    state.wire_available,
    state.wire_seq > state.applied_seq + 1,
    state' = {
      wire_available: false,
      gap_pending: true,
      gap_expected: state.applied_seq + 1,
      gap_received: state.wire_seq,
      delivery_kind: GapDelivery,
      seq_disposition: Gap,
      previous_host_seq: state.host_seq,
      ...state,
    },
  }

  action start_gap_replay = all {
    state.transport == Connected,
    state.resume_phase != ReplayPending,
    state.resume_phase != SnapshotPending,
    state.gap_pending,
    not(state.wire_available),
    state' = {
      gap_pending: false,
      replay_in_progress: true,
      replay_from_seq: state.gap_expected,
      replay_to_seq: state.host_seq,
      previous_host_seq: state.host_seq,
      ...state,
    },
  }

  action finish_gap_replay = all {
    state.transport == Connected,
    state.resume_phase != ReplayPending,
    state.resume_phase != SnapshotPending,
    state.replay_in_progress,
    state' = {
      replay_in_progress: false,
      applied_seq: state.replay_to_seq,
      seq_disposition: Replayed,
      gap_recovered: true,
      delivery_kind: NoDelivery,
      previous_host_seq: state.host_seq,
      ...state,
    },
  }

  action gap_progress = any {
    host_emit_gap,
    player_detect_gap,
    start_gap_replay,
    finish_gap_replay,
  }

  // No stutter at the last reserved request sequence: internal terminal
  // transitions must remain available even when the peer is disconnected.
  action stutter = all {
    state.request_state == NoRequest or state.host_seq + 1 < state.seq_reservation_end,
    state' = { previous_host_seq: state.host_seq, ...state },
  }

  action step = any {
    open_request,
    submit_pass,
    internal_progress,
    disconnect,
    resume_delivery_progress,
    emit_pending_snapshot,
    begin_pending_replay,
    finish_pending_replay,
    finish_pending_snapshot,
    gap_progress,
    stutter,
  }

  // ------------------------------------------------------------------------
  // Temporal properties
  // ------------------------------------------------------------------------

  temporal internal_terminalization_under_fairness: bool =
    weakFair(internal_progress, state).implies(
      always(
        (state.request_state == RequestOpen and state.deadline == 0)
          implies eventually(state.group_state == GroupClosed or state.group_state == GroupResolved)
      )
    )

  temporal gap_replay_under_eventual_stable_connection: bool =
    (and {
      weakFair(resume, state),
      weakFair(gap_progress, state),
      eventually(always(state.transport == Connected)),
    }).implies(
      always(
        (state.gap_pending or state.replay_in_progress)
          implies eventually(state.gap_recovered)
      )
    )

  temporal pending_delivery_under_eventual_stable_connection: bool =
    (and {
      weakFair(resume, state),
      weakFair(resume_delivery_progress, state),
      eventually(always(state.transport == Connected)),
    }).implies(
      always(
        (state.resume_phase == ReplayPending or state.resume_phase == SnapshotPending)
          implies eventually(state.resume_phase == ResumeComplete)
      )
    )
}
