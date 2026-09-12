// Draft.6: one peer, one decision, bounded immutable message history.
// Other group members are summarized by others_ready; the three-seat
// priority scheduler is checked in yamai_request_liveness.qnt.
module yamai_resume_delivery {
  type Transport = Connected | Disconnected
  type Phase = Idle | Open | Selected | Decided | Acked | Resolved
  type Choice = NoChoice | UserChoice | DefaultChoice
  type PeerPhase = PeerIdle | PeerOpen | PeerSelected | PeerAcked | PeerResolved
  type Sync = Live | ResumeNeeded | GapNeeded | Replaying | SnapshotPending
  type Message = Empty | Request | UserAck | DefaultAck | Result | Notice | Snapshot

  pure val MAX_SEQ = 6
  pure val SEQS = 1.to(MAX_SEQ)
  pure val EMPTY_HISTORY = SEQS.mapBy(i => Empty)
  pure val ZERO_COUNTS = SEQS.mapBy(i => 0)

  type State = {
    transport: Transport,
    sync: Sync,
    grouped: bool,
    disconnect_used: bool,
    gap_used: bool,
    gap_expected: int,
    gap_received: int,
    gap_recovered: bool,
    resume_completed: bool,
    phase: Phase,
    choice: Choice,
    others_ready: bool,
    deadline: int,
    host_seq: int,
    previous_host_seq: int,
    applied_seq: int,
    previous_applied_seq: int,
    last_delivery: bool,
    history: int -> Message,
    apply_counts: int -> int,
    peer_phase: PeerPhase,
    peer_choice: Choice,
    request_seq: int,
    ack_seq: int,
    result_seq: int,
    notices: int,
    replay_from: int,
    replay_cursor: int,
    replay_through: int,
    frozen_frontier: int,
    replay_tail_created: bool,
    snapshot_seq: int,
    snapshot_replaces: int,
    snapshot_phase: PeerPhase,
    snapshot_choice: Choice,
    snapshot_while_selected: bool,
    tick_while_disconnected: bool,
    defaulted_while_disconnected: bool,
    acked_while_disconnected: bool,
    resolved_while_disconnected: bool,
  }
  var state: State

  val next_base = { previous_host_seq: state.host_seq,
    previous_applied_seq: state.applied_seq, last_delivery: false, ...state }
  val backlog_pending = state.applied_seq < state.host_seq
  val backlog_from_seq = if (backlog_pending) state.applied_seq + 1 else 0
  val backlog_to_seq = if (backlog_pending) state.host_seq else 0
  val host_ready_for_welcome = or {
    state.phase == Idle, state.phase == Resolved,
    state.phase == Open and state.deadline > 0,
    state.phase == Selected and not(state.others_ready) and state.deadline > 0,
  }
  val recovery_ready = state.sync == GapNeeded or
    (state.sync == ResumeNeeded and host_ready_for_welcome)
  val host_peer_phase =
    if (state.phase == Idle) PeerIdle
    else if (state.phase == Open) PeerOpen
    else if (state.phase.in(Set(Selected, Decided))) PeerSelected
    else if (state.phase == Acked) PeerAcked
    else PeerResolved

  val type_ok = and {
    state.transport.in(Set(Connected, Disconnected)),
    state.sync.in(Set(Live, ResumeNeeded, GapNeeded, Replaying, SnapshotPending)),
    state.phase.in(Set(Idle, Open, Selected, Decided, Acked, Resolved)),
    state.choice.in(Set(NoChoice, UserChoice, DefaultChoice)),
    state.peer_choice.in(Set(NoChoice, UserChoice, DefaultChoice)),
    state.snapshot_choice.in(Set(NoChoice, UserChoice, DefaultChoice)),
    state.peer_phase.in(Set(PeerIdle, PeerOpen, PeerSelected, PeerAcked, PeerResolved)),
    state.snapshot_phase.in(Set(PeerIdle, PeerOpen, PeerSelected, PeerAcked, PeerResolved)),
    state.host_seq.in(0.to(MAX_SEQ)), state.previous_host_seq.in(0.to(MAX_SEQ)),
    state.applied_seq.in(0.to(MAX_SEQ)), state.previous_applied_seq.in(0.to(MAX_SEQ)),
    state.request_seq.in(0.to(MAX_SEQ)), state.ack_seq.in(0.to(MAX_SEQ)), state.result_seq.in(0.to(MAX_SEQ)),
    state.replay_from.in(0.to(MAX_SEQ + 1)), state.replay_cursor.in(0.to(MAX_SEQ + 1)),
    state.replay_through.in(0.to(MAX_SEQ)), state.frozen_frontier.in(0.to(MAX_SEQ)),
    state.snapshot_seq.in(0.to(MAX_SEQ)), state.snapshot_replaces.in(0.to(MAX_SEQ)),
    state.gap_expected.in(0.to(MAX_SEQ)), state.gap_received.in(0.to(MAX_SEQ)),
    state.deadline.in(0.to(2)), state.notices.in(0.to(2)),
    state.history.keys() == SEQS, state.apply_counts.keys() == SEQS,
    SEQS.forall(i => state.history.get(i).in(Set(Empty, Request, UserAck, DefaultAck, Result, Notice, Snapshot))),
    SEQS.forall(i => state.apply_counts.get(i).in(Set(0, 1))),
  }
  val host_seq_non_decreasing = and {
    state.host_seq >= state.previous_host_seq,
    state.applied_seq >= state.previous_applied_seq,
    state.applied_seq <= state.host_seq,
    not(state.last_delivery) implies state.applied_seq == state.previous_applied_seq,
    state.last_delivery implies state.transport == Connected,
  }
  val backlog_invariant = and {
    SEQS.forall(i => (i <= state.host_seq) == (state.history.get(i) != Empty)),
    backlog_pending iff backlog_from_seq > 0,
    backlog_pending implies backlog_from_seq <= backlog_to_seq,
    backlog_to_seq == 0 or backlog_to_seq == state.host_seq,
    SEQS.forall(i => state.apply_counts.get(i) == 1 implies i <= state.applied_seq),
    SEQS.forall(i => i <= state.applied_seq implies
      state.apply_counts.get(i) == 1 or i <= state.snapshot_replaces),
    state.sync == Replaying implies state.replay_through == state.frozen_frontier,
    state.replay_through <= state.host_seq,
  }
  val gap_invariant = and {
    state.gap_used implies state.gap_expected > 0,
    state.gap_used implies state.gap_received > state.gap_expected,
    state.sync == Replaying implies state.replay_cursor >= state.replay_from,
    state.sync == Replaying implies state.replay_cursor <= state.replay_through + 1,
    state.sync == Replaying implies state.applied_seq + 1 == state.replay_cursor,
  }
  val request_invariant = and {
    state.phase == Idle iff state.request_seq == 0,
    state.phase.in(Set(Idle, Open)) iff state.choice == NoChoice,
    state.phase.in(Set(Acked, Resolved)) iff state.ack_seq > 0,
    state.phase == Resolved iff state.result_seq > 0,
    state.ack_seq > 0 implies state.ack_seq > state.request_seq,
    state.result_seq > 0 implies state.result_seq == state.ack_seq + 1,
    state.ack_seq > 0 implies state.history.get(state.ack_seq) ==
      (if (state.choice == UserChoice) UserAck else DefaultAck),
    state.phase.in(Set(Decided, Acked, Resolved)) implies state.others_ready,
    state.peer_phase.in(Set(PeerAcked, PeerResolved)) implies state.peer_choice == state.choice,
    state.peer_phase == PeerResolved implies state.phase == Resolved,
  }
  val pending_saved_invariant = and {
    state.snapshot_seq > 0 implies state.snapshot_seq == state.snapshot_replaces + 1,
    state.snapshot_seq > 0 implies state.snapshot_seq <= state.host_seq,
    state.snapshot_seq > 0 implies state.history.get(state.snapshot_seq) == Snapshot,
    state.snapshot_while_selected implies state.snapshot_choice != NoChoice,
    state.sync == SnapshotPending implies state.snapshot_seq > 0,
  }
  val disconnected_internal_invariant = and {
    state.tick_while_disconnected implies state.disconnect_used,
    state.defaulted_while_disconnected implies state.disconnect_used,
    state.acked_while_disconnected implies state.disconnect_used,
    state.resolved_while_disconnected implies state.disconnect_used,
  }
  val protocol_invariant = and {
    type_ok, host_seq_non_decreasing, backlog_invariant, gap_invariant,
    request_invariant, pending_saved_invariant, disconnected_internal_invariant,
  }

  val witness_disconnect_default = state.defaulted_while_disconnected
  val witness_disconnect_resolve = state.resolved_while_disconnected
  val witness_reconnect_replay = state.resume_completed
  val witness_snapshot_replacement = state.snapshot_seq > 0 and state.apply_counts.get(state.snapshot_seq) == 1
  val witness_gap_recovery = state.gap_recovered
  val witness_frozen_frontier_tail = state.replay_tail_created and not(backlog_pending) and state.sync == Live
  val witness_selected_snapshot = state.snapshot_while_selected and witness_snapshot_replacement

  action init_with(grouped) = state' = {
    transport: Connected, sync: Live, grouped: grouped, disconnect_used: false,
    gap_used: false, gap_expected: 0, gap_received: 0, gap_recovered: false, resume_completed: false,
    phase: Idle, choice: NoChoice, others_ready: not(grouped), deadline: 0,
    host_seq: 0, previous_host_seq: 0, applied_seq: 0, previous_applied_seq: 0,
    last_delivery: false, history: EMPTY_HISTORY, apply_counts: ZERO_COUNTS,
    peer_phase: PeerIdle, peer_choice: NoChoice, request_seq: 0, ack_seq: 0, result_seq: 0,
    notices: 0, replay_from: 0, replay_cursor: 0, replay_through: 0, frozen_frontier: 0,
    replay_tail_created: false, snapshot_seq: 0, snapshot_replaces: 0,
    snapshot_phase: PeerIdle, snapshot_choice: NoChoice, snapshot_while_selected: false,
    tick_while_disconnected: false, defaulted_while_disconnected: false,
    acked_while_disconnected: false, resolved_while_disconnected: false,
  }
  action init = { nondet grouped = Set(false, true).oneOf(); init_with(grouped) }

  action open_request = all {
    state.phase == Idle, state.transport == Connected, state.sync == Live,
    state.host_seq < MAX_SEQ,
    state' = { phase: Open, deadline: 2, request_seq: state.host_seq + 1,
      history: state.history.set(state.host_seq + 1, Request), host_seq: state.host_seq + 1, ...next_base },
  }
  action submit_action = all {
    state.transport == Connected, state.sync == Live, state.peer_phase == PeerOpen,
    state.phase == Open, state.deadline > 0,
    state' = { phase: Selected, choice: UserChoice, peer_phase: PeerSelected, peer_choice: UserChoice, ...next_base },
  }
  action tick = all {
    state.phase.in(Set(Open, Selected)), state.deadline > 0,
    state.phase == Open or not(state.others_ready),
    state' = { deadline: state.deadline - 1,
      tick_while_disconnected: state.tick_while_disconnected or state.transport == Disconnected, ...next_base },
  }
  action default_action = all {
    state.phase == Open, state.deadline == 0,
    state' = { phase: Selected, choice: DefaultChoice,
      defaulted_while_disconnected: state.defaulted_while_disconnected or state.transport == Disconnected, ...next_base },
  }
  action finish_others = all {
    state.grouped, not(state.others_ready), state.phase.in(Set(Open, Selected)),
    state' = {others_ready: true, ...next_base},
  }
  action decide = all {
    state.phase == Selected, state.others_ready,
    state' = {phase: Decided, ...next_base},
  }
  action ack_action = all {
    state.phase == Decided, state.host_seq < MAX_SEQ,
    state' = { phase: Acked, ack_seq: state.host_seq + 1,
      history: state.history.set(state.host_seq + 1, if (state.choice == UserChoice) UserAck else DefaultAck),
      host_seq: state.host_seq + 1,
      replay_tail_created: state.replay_tail_created or state.sync == Replaying,
      acked_while_disconnected: state.acked_while_disconnected or state.transport == Disconnected, ...next_base },
  }
  action resolve_group = all {
    state.phase == Acked, state.host_seq < MAX_SEQ,
    state' = { phase: Resolved, result_seq: state.host_seq + 1,
      history: state.history.set(state.host_seq + 1, Result), host_seq: state.host_seq + 1,
      replay_tail_created: state.replay_tail_created or state.sync == Replaying,
      resolved_while_disconnected: state.resolved_while_disconnected or state.transport == Disconnected, ...next_base },
  }
  action emit_notice = all {
    state.notices < 2, state.host_seq < MAX_SEQ,
    not(state.phase.in(Set(Decided, Acked))),
    state' = { notices: state.notices + 1, host_seq: state.host_seq + 1,
      history: state.history.set(state.host_seq + 1, Notice),
      replay_tail_created: state.replay_tail_created or state.sync == Replaying, ...next_base },
  }

  action apply_message(seq) = {
    val message = state.history.get(seq)
    val snapshot = message == Snapshot
    all {
      state.transport == Connected, seq > state.applied_seq, seq <= state.host_seq,
      seq == state.applied_seq + 1 or snapshot,
      state.apply_counts.get(seq) == 0,
      state' = { applied_seq: seq, last_delivery: true,
        apply_counts: state.apply_counts.set(seq, 1),
        peer_phase: if (snapshot) state.snapshot_phase
          else if (message == Request) PeerOpen
          else if (message.in(Set(UserAck, DefaultAck))) PeerAcked
          else if (message == Result) PeerResolved else state.peer_phase,
        peer_choice: if (snapshot) state.snapshot_choice
          else if (message == UserAck) UserChoice else if (message == DefaultAck) DefaultChoice
          else state.peer_choice,
        replay_cursor: if (state.sync == Replaying) seq + 1 else state.replay_cursor,
        sync: if (state.sync == SnapshotPending) Live else state.sync,
        gap_recovered: state.gap_recovered or (snapshot and state.gap_used),
        resume_completed: state.resume_completed or (snapshot and state.disconnect_used),
        ...next_base },
    }
  }
  action deliver_next = all {
    state.transport == Connected, state.sync == Live, backlog_pending,
    apply_message(state.applied_seq + 1),
  }
  action duplicate = all {
    state.transport == Connected, state.applied_seq > 0,
    state' = next_base,
  }
  action detect_gap = all {
    state.transport == Connected, state.sync == Live, not(state.gap_used),
    state.host_seq >= state.applied_seq + 2,
    state' = { sync: GapNeeded, gap_used: true,
      gap_expected: state.applied_seq + 1, gap_received: state.applied_seq + 2, ...next_base },
  }
  action disconnect = all {
    state.transport == Connected, not(state.disconnect_used),
    state' = {transport: Disconnected, disconnect_used: true, sync: ResumeNeeded, ...next_base},
  }
  action resume = all {
    state.transport == Disconnected,
    state' = {transport: Connected, sync: ResumeNeeded, ...next_base},
  }
  action begin_replay = all {
    state.transport == Connected, recovery_ready,
    state' = { sync: Replaying, replay_from: state.applied_seq + 1,
      replay_cursor: state.applied_seq + 1, replay_through: state.host_seq,
      frozen_frontier: state.host_seq, ...next_base },
  }
  action replay_next = all {
    state.transport == Connected, state.sync == Replaying,
    state.replay_cursor <= state.replay_through,
    apply_message(state.replay_cursor),
  }
  action finish_replay = all {
    state.transport == Connected, state.sync == Replaying,
    state.replay_cursor == state.replay_through + 1,
    state' = { sync: Live, gap_recovered: state.gap_recovered or state.gap_used,
      resume_completed: state.resume_completed or state.disconnect_used, ...next_base },
  }
  action emit_snapshot = all {
    state.transport == Connected, recovery_ready, state.snapshot_seq == 0,
    not(state.phase.in(Set(Decided, Acked))), state.host_seq < MAX_SEQ,
    state' = { sync: SnapshotPending, snapshot_seq: state.host_seq + 1,
      snapshot_replaces: state.host_seq, snapshot_phase: host_peer_phase,
      snapshot_choice: state.choice, snapshot_while_selected: state.phase == Selected,
      history: state.history.set(state.host_seq + 1, Snapshot), host_seq: state.host_seq + 1, ...next_base },
  }
  action deliver_snapshot = all {
    state.transport == Connected, state.sync == SnapshotPending,
    apply_message(state.snapshot_seq),
  }

  action internal_progress = any {tick, default_action, finish_others, decide, ack_action, resolve_group}
  action delivery_progress = any {begin_replay, replay_next, finish_replay, emit_snapshot, deliver_snapshot, deliver_next}
  action stutter = state' = next_base
  action step = any {open_request, submit_action, emit_notice, internal_progress,
    delivery_progress, duplicate, detect_gap, disconnect, resume, stutter}

  temporal internal_terminalization_under_fairness = weakFair(internal_progress, state).implies(
    always(state.phase != Idle implies eventually(state.phase == Resolved)))
  temporal gap_replay_under_eventual_stable_connection =
    (weakFair(internal_progress, state) and weakFair(delivery_progress, state)
      and eventually(always(state.transport == Connected))).implies(
        always(state.gap_used implies eventually(state.gap_recovered)))
  temporal pending_delivery_under_eventual_stable_connection =
    (weakFair(internal_progress, state) and weakFair(delivery_progress, state)
      and eventually(always(state.transport == Connected))).implies(
        always(backlog_pending implies eventually(not(backlog_pending))))

  run frozenFrontierTailTest = init_with(false).then(open_request).then(deliver_next)
    .then(disconnect).then(resume).then(begin_replay).then(tick).then(tick)
    .then(default_action).then(decide).then(ack_action).then(resolve_group)
    .then(finish_replay).then(all {assert(backlog_pending), assert(state.applied_seq == 1), state' = state})
    .then(deliver_next).then(deliver_next)
    .then(all {assert(witness_frozen_frontier_tail), assert(protocol_invariant), state' = state})
  run selectedSnapshotTest = init_with(true).then(open_request).then(deliver_next)
    .then(submit_action).then(disconnect).then(resume).then(emit_snapshot).then(deliver_snapshot)
    .then(all {assert(state.peer_choice == UserChoice), assert(witness_selected_snapshot), assert(protocol_invariant), state' = state})
  run offlineProgressTest = init_with(false).then(open_request).then(disconnect)
    .then(tick).then(tick).then(default_action).then(decide).then(ack_action).then(resolve_group)
    .then(all {assert(state.applied_seq == 0), assert(state.host_seq == 3), assert(witness_disconnect_resolve), assert(protocol_invariant), state' = state})
}
