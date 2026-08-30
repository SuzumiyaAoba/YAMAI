// Deterministic, finite conformance projection of yamai_protocol_core.qnt.
//
// This is deliberately not a second canonical model.  It is one complete,
// phase-driven trace through the Protocol Core.  The four request slots are
// scalar fields so TLC can exhaust the projection without expanding maps.

module yamai_protocol_core_bounded {
  type Session = Idle | Active
  type Transport = Connected | Disconnected
  type Resume = NoResume | ResumeReady | ReplayPending | ResumeComplete | SnapshotPending
  type Request = Empty | Open | Defaulted | Acked | Resolved
  type Group = NoGroup | GroupOpen | GroupClosed | GroupResolved

  pure val MAX_SEQ: int = 12
  pure val END: int = 21
  pure val SEQS: Set[int] = Set(1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12)

  type State = {
    phase: int,
    session: Session,
    transport: Transport,
    resume: Resume,
    clock: int,
    previous_clock: int,
    host_seq: int,
    previous_host_seq: int,
    ledger: Set[int],
    delivered_seq: int,
    applied_seq: int,
    wire_pending: bool,
    r0: Request, r1: Request, r2: Request, r3: Request,
    g0: Group, g1: Group, g2: Group, g3: Group,
    default_mask: int,
    ack_mask: int,
    linearized_group: int,
    active_requests: int,
    snapshot_seq: int,
    snapshot_replaces_through: int,
  }

  var state: State

  val type_ok: bool = and {
    state.phase.in(0.to(END)),
    state.session == Active,
    state.transport.in(Set(Connected, Disconnected)),
    state.resume.in(Set(NoResume, ResumeReady, ReplayPending, ResumeComplete, SnapshotPending)),
    state.clock.in(0.to(3)),
    state.previous_clock.in(0.to(3)),
    state.host_seq.in(0.to(MAX_SEQ)),
    state.previous_host_seq.in(0.to(MAX_SEQ)),
    state.delivered_seq.in(0.to(MAX_SEQ)),
    state.applied_seq.in(0.to(MAX_SEQ)),
    state.default_mask.in(0.to(15)),
    state.ack_mask.in(0.to(15)),
    state.linearized_group.in(0.to(MAX_SEQ)),
    state.active_requests.in(0.to(4)),
    state.snapshot_seq.in(0.to(MAX_SEQ)),
    state.snapshot_replaces_through.in(0.to(MAX_SEQ)),
  }

  val seq_ledger_invariant: bool = and {
    state.ledger.subseteq(SEQS),
    state.ledger.size() == state.host_seq,
    state.host_seq >= state.previous_host_seq,
    state.host_seq == 0 or state.host_seq.in(state.ledger),
  }

  val delivery_applied_invariant: bool = and {
    state.applied_seq <= state.delivered_seq,
    state.delivered_seq <= state.host_seq,
    state.applied_seq == 0 or state.applied_seq.in(state.ledger),
    state.wire_pending implies state.delivered_seq == state.applied_seq + 1,
  }

  // r0..r3 are the four canonical request slots.  Slot 3 is the simultaneous
  // group request; group closure is linearized exactly once at host_seq 10.
  val request_slots_invariant: bool = and {
    state.r0.in(Set(Empty, Open, Defaulted, Acked, Resolved)),
    state.r1.in(Set(Empty, Open, Defaulted, Acked, Resolved)),
    state.r2.in(Set(Empty, Open, Defaulted, Acked, Resolved)),
    state.r3.in(Set(Empty, Open, Defaulted, Acked, Resolved)),
    state.g0.in(Set(NoGroup, GroupOpen, GroupClosed, GroupResolved)),
    state.g1.in(Set(NoGroup, GroupOpen, GroupClosed, GroupResolved)),
    state.g2.in(Set(NoGroup, GroupOpen, GroupClosed, GroupResolved)),
    state.g3.in(Set(NoGroup, GroupOpen, GroupClosed, GroupResolved)),
    state.r0 == Open implies state.g0 == GroupOpen,
    state.r1 == Open implies state.g1 == GroupOpen,
    state.r2 == Open implies state.g2 == GroupOpen,
    state.r3 == Open implies state.g3 == GroupOpen,
    state.r0 == Resolved implies state.g0 == GroupResolved,
    state.r3 == Resolved implies state.g3 == GroupResolved,
    state.phase == 4 implies and { state.r0 == Open, state.r1 == Open, state.r2 == Open, state.r3 == Open },
  }

  val deadline_ack_invariant: bool = and {
    state.clock >= state.previous_clock,
    state.default_mask == 0 or state.r0.in(Set(Defaulted, Resolved)),
    state.ack_mask == 0 or state.r1.in(Set(Acked, Resolved)) or state.r2.in(Set(Acked, Resolved)) or state.r3.in(Set(Acked, Resolved)),
    state.phase >= 7 implies state.default_mask == 1,
  }

  val group_linearization_invariant: bool = and {
    state.linearized_group <= state.host_seq,
    state.r3 == Resolved implies state.linearized_group == 10,
    state.linearized_group > 0 implies state.g3 == GroupResolved,
  }

  val resume_snapshot_invariant: bool = and {
    state.resume == ReplayPending implies state.transport == Connected,
    state.resume == SnapshotPending implies state.transport == Connected,
    state.snapshot_seq <= state.host_seq,
    state.snapshot_replaces_through <= state.snapshot_seq,
    state.resume == SnapshotPending implies state.snapshot_seq == state.host_seq,
  }

  val protocol_invariant: bool = and {
    type_ok,
    seq_ledger_invariant,
    delivery_applied_invariant,
    request_slots_invariant,
    deadline_ack_invariant,
    group_linearization_invariant,
    resume_snapshot_invariant,
  }

  // Bounded refinement relation: the scalar projection satisfies the key
  // canonical state constraints (four slots, append-only wire, ordered apply,
  // linearized group, and resume/snapshot replacement boundary).
  val refinement_mapping: bool = and {
    protocol_invariant,
    state.active_requests.in(0.to(4)),
    state.ledger.subseteq(SEQS),
    state.ledger.size() == state.host_seq,
    state.applied_seq <= state.host_seq,
    state.r3 == Resolved implies state.linearized_group > 0,
    state.resume == SnapshotPending implies state.snapshot_replaces_through <= state.applied_seq,
  }

  action init = state' = {
    phase: 0, session: Active, transport: Connected, resume: NoResume,
    clock: 0, previous_clock: 0, host_seq: 0, previous_host_seq: 0,
    ledger: Set(), delivered_seq: 0, applied_seq: 0, wire_pending: false,
    r0: Empty, r1: Empty, r2: Empty, r3: Empty,
    g0: NoGroup, g1: NoGroup, g2: NoGroup, g3: NoGroup,
    default_mask: 0, ack_mask: 0, linearized_group: 0, active_requests: 0,
    snapshot_seq: 0, snapshot_replaces_through: 0,
  }

  action open0 = all {
    state.phase == 0, state.host_seq < MAX_SEQ,
    state' = { phase: 1, host_seq: state.host_seq + 1, previous_host_seq: state.host_seq,
      ledger: state.ledger.union(Set(state.host_seq + 1)), r0: Open, g0: GroupOpen,
      active_requests: 1, ...state },
  }
  action open1 = all {
    state.phase == 1, state.host_seq < MAX_SEQ,
    state' = { phase: 2, host_seq: state.host_seq + 1, previous_host_seq: state.host_seq,
      ledger: state.ledger.union(Set(state.host_seq + 1)), r1: Open, g1: GroupOpen,
      active_requests: 2, ...state },
  }
  action open2 = all {
    state.phase == 2, state.host_seq < MAX_SEQ,
    state' = { phase: 3, host_seq: state.host_seq + 1, previous_host_seq: state.host_seq,
      ledger: state.ledger.union(Set(state.host_seq + 1)), r2: Open, g2: GroupOpen,
      active_requests: 3, ...state },
  }
  action open3 = all {
    state.phase == 3, state.host_seq < MAX_SEQ,
    state' = { phase: 4, host_seq: state.host_seq + 1, previous_host_seq: state.host_seq,
      ledger: state.ledger.union(Set(state.host_seq + 1)), r3: Open, g3: GroupOpen,
      active_requests: 4, ...state },
  }

  action deadline = all { state.phase == 4, state' = { phase: 5, clock: 2, previous_clock: state.clock, ...state } }
  action default0 = all {
    state.phase == 5, state.clock == 2, state.host_seq < MAX_SEQ,
    state' = { phase: 6, host_seq: state.host_seq + 1, previous_host_seq: state.host_seq,
      ledger: state.ledger.union(Set(state.host_seq + 1)), r0: Defaulted, g0: GroupClosed,
      default_mask: 1, ...state },
  }
  action ack1 = all {
    state.phase == 6, state.host_seq < MAX_SEQ,
    state' = { phase: 7, host_seq: state.host_seq + 1, previous_host_seq: state.host_seq,
      ledger: state.ledger.union(Set(state.host_seq + 1)), r1: Acked, g1: GroupClosed,
      ack_mask: 1, ...state },
  }
  action ack2 = all {
    state.phase == 7, state.host_seq < MAX_SEQ,
    state' = { phase: 8, host_seq: state.host_seq + 1, previous_host_seq: state.host_seq,
      ledger: state.ledger.union(Set(state.host_seq + 1)), r2: Acked, g2: GroupClosed,
      ack_mask: 3, ...state },
  }
  action ack_group_first = all {
    state.phase == 8, state.host_seq < MAX_SEQ,
    state' = { phase: 9, host_seq: state.host_seq + 1, previous_host_seq: state.host_seq,
      ledger: state.ledger.union(Set(state.host_seq + 1)), ack_mask: 7, ...state },
  }
  action ack_group_second = all {
    state.phase == 9, state.host_seq < MAX_SEQ,
    state' = { phase: 10, host_seq: state.host_seq + 1, previous_host_seq: state.host_seq,
      ledger: state.ledger.union(Set(state.host_seq + 1)), r3: Acked, g3: GroupClosed,
      ack_mask: 15, ...state },
  }
  action linearize_group = all {
    state.phase == 10, state.host_seq < MAX_SEQ,
    state' = { phase: 11, host_seq: state.host_seq + 1, previous_host_seq: state.host_seq,
      ledger: state.ledger.union(Set(state.host_seq + 1)), r3: Resolved, g3: GroupResolved,
      linearized_group: state.host_seq + 1, active_requests: 3, ...state },
  }
  action resolve_default = all {
    state.phase == 11, state.host_seq < MAX_SEQ,
    state' = { phase: 12, host_seq: state.host_seq + 1, previous_host_seq: state.host_seq,
      ledger: state.ledger.union(Set(state.host_seq + 1)), r0: Resolved, g0: GroupResolved,
      active_requests: 2, ...state },
  }

  action deliver = all {
    state.phase == 12, state.applied_seq < state.host_seq,
    state' = { phase: 13, delivered_seq: state.applied_seq + 1, wire_pending: true, ...state },
  }
  action apply = all {
    state.phase == 13, state.wire_pending,
    state' = { phase: 14, applied_seq: state.delivered_seq, wire_pending: false, ...state },
  }
  action disconnect = all { state.phase == 14, state' = { phase: 15, transport: Disconnected, resume: ResumeReady, ...state } }
  action reconnect = all { state.phase == 15, state' = { phase: 16, transport: Connected, resume: ReplayPending, ...state } }
  action replay = all {
    state.phase == 16, state.applied_seq < state.host_seq,
    state' = { phase: 17, delivered_seq: state.host_seq, applied_seq: state.host_seq,
      resume: ResumeComplete, ...state },
  }
  action snapshot = all {
    state.phase == 17, state.host_seq < MAX_SEQ,
    state' = { phase: 18, host_seq: state.host_seq + 1, previous_host_seq: state.host_seq,
      ledger: state.ledger.union(Set(state.host_seq + 1)), resume: SnapshotPending,
      snapshot_seq: state.host_seq + 1, snapshot_replaces_through: state.applied_seq, ...state },
  }
  action snapshot_complete = all { state.phase == 18, state' = { phase: END, resume: ResumeComplete, ...state } }
  action done = all { state.phase == END, state' = state }

  // Exactly one transition is enabled in each phase; only replay repeats.
  action step = any {
    open0, open1, open2, open3, deadline, default0, ack1, ack2,
    ack_group_first, ack_group_second, linearize_group, resolve_default,
    deliver, apply, disconnect, reconnect, replay, snapshot, snapshot_complete, done,
  }

  val witness_complete: bool = state.phase == END
}
