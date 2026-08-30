// Finite extension model for the YAMAI control-flow protocol.
//
// This file deliberately stays abstract: it does not parse JSON or implement
// mahjong scoring.  It adds finite models for the protocol obligations that
// are outside the small baseline model in yamai_protocol.qnt:
// negotiation, sequence gap/duplicate/conflict/replay, pending resume and
// snapshot replacement, request-group timeout boundaries, and terminalization
// before end_kyoku/end_game.

module yamai_protocol_extended {
  type SessionState = Idle | Negotiating | Active | Ended
  type TransportState = Connected | Disconnected
  type NegotiationState = NoNegotiation | HelloSent | JoinSent | NegotiationAccepted | Rejected
  type Version = NoVersion | Draft5 | UnsupportedVersion
  type Profile = NoProfile | Riichi4p | UnsupportedProfile
  type ProfileHash = NoProfileHash | GoodProfileHash | BadProfileHash
  type Capability = ResumeCap | SnapshotCap | UnknownCap
  type SeqDisposition = NoSeq | Fresh | Duplicate | Gap | Conflict | Replayed
  type WirePayload = PayloadA | PayloadB | SnapshotPayload
  type ResumePhase = NoResume | ResumeReady | ReplayPending | SnapshotPending | ResumeComplete
  type RequestState = NoRequest | RequestOpen | RequestAcked
  type GroupState = NoGroup | GroupOpen | GroupClosed | GroupResolved
  type AckStatus = AckNone | Accepted | Passed | Defaulted
  type ActionId = NoAction | PassAction | ReachAction | CallAction | DefaultAction

  pure val SEATS: Set[int] = Set(0, 1, 2, 3)
  pure val SINGLE_SEAT: int = 0
  pure val GROUP_SEATS: Set[int] = Set(1, 2)
  pure val SUPPORTED_CAPS: Set[Capability] = Set(ResumeCap, SnapshotCap)
  pure val ALL_CAPS: Set[Capability] = Set(ResumeCap, SnapshotCap, UnknownCap)
  pure val ACTION_IDS: Set[ActionId] = Set(NoAction, PassAction, ReachAction, CallAction, DefaultAction)

  // Bounds are intentionally small.  A group reserves six host messages:
  // two requests, two member ACKs, one optional pending snapshot, and resolve.
  pure val MAX_SEQ: int = 8
  pure val MAX_TIMEOUT: int = 2
  pure val MAX_REQUEST_ID: int = 3
  pure val MAX_TOKEN_VERSION: int = 3
  pure val TOTAL_POINTS: int = 100
  pure val STARTING_POINTS: int = 25
  pure val RIICHI_STICK: int = 1
  pure val SINGLE_RESERVATION: int = 4
  pure val GROUP_RESERVATION: int = 6

  pure val EMPTY_REQUEST_IDS: int -> int = Map(0 -> 0, 1 -> 0, 2 -> 0, 3 -> 0)
  pure val EMPTY_ACTIONS: int -> ActionId = Map(0 -> NoAction, 1 -> NoAction, 2 -> NoAction, 3 -> NoAction)
  pure val EMPTY_ACKS: int -> AckStatus = Map(0 -> AckNone, 1 -> AckNone, 2 -> AckNone, 3 -> AckNone)

  type ProtocolState = {
    session: SessionState,
    transport: TransportState,
    negotiation: NegotiationState,
    hello_version: Version,
    hello_profile: Profile,
    hello_hash: ProfileHash,
    hello_required: Set[Capability],
    hello_optional: Set[Capability],
    join_version: Version,
    join_profile: Profile,
    join_hash: ProfileHash,
    join_required: Set[Capability],
    join_optional: Set[Capability],
    negotiated_caps: Set[Capability],
    host_seq: int,
    previous_host_seq: int,
    applied_seq: int,
    wire_available: bool,
    wire_seq: int,
    wire_payload: WirePayload,
    applied_payload: WirePayload,
    seq_disposition: SeqDisposition,
    gap_expected: int,
    gap_received: int,
    replay_pending: bool,
    replay_in_progress: bool,
    replay_from_seq: int,
    replay_to_seq: int,
    // Host-generated messages that cannot currently be delivered are kept as
    // a finite contiguous backlog range.  Resume replay/snapshot completion
    // consumes this range; host_seq remains the authoritative history bound.
    backlog_pending: bool,
    backlog_from_seq: int,
    backlog_to_seq: int,
    request_state: RequestState,
    group_state: GroupState,
    pending_members: Set[int],
    responded_members: Set[int],
    terminal_members: Set[int],
    defaulted_members: Set[int],
    request_ids: int -> int,
    submitted_actions: int -> ActionId,
    ack_statuses: int -> AckStatus,
    deadline: int,
    next_request_id: int,
    seq_reservation_end: int,
    resume_phase: ResumePhase,
    resume_token_version: int,
    resume_available: bool,
    resumed: bool,
    snapshot_seq: int,
    snapshot_replaces_through_seq: int,
    snapshot_preserved_pending: bool,
    snapshot_saved_members: Set[int],
    snapshot_saved_request_ids: int -> int,
    snapshot_saved_actions: int -> ActionId,
    snapshot_saved_terminal: Set[int],
    snapshot_saved_defaulted: Set[int],
    end_kyoku_sent: bool,
    end_game_sent: bool,
    fatal: bool,
    score_0: int,
    score_1: int,
    score_2: int,
    score_3: int,
    kyotaku: int,
  }

  var state: ProtocolState

  // Host-generated application messages must remain in the host history when
  // the peer is disconnected or a resume/snapshot transfer is still pending.
  // The helper predicates are used by every host-seq-advancing action.
  val backlog_capture_active: bool = or {
    state.transport == Disconnected,
    state.resume_phase == ReplayPending,
    state.resume_phase == SnapshotPending,
  }

  val backlog_next_from_seq: int =
    if (state.backlog_pending) state.backlog_from_seq else state.host_seq + 1

  // ------------------------------------------------------------------------
  // Invariants and witnesses
  // ------------------------------------------------------------------------

  val score_total: int = state.score_0 + state.score_1 + state.score_2 + state.score_3

  val score_conservation: bool =
    score_total + state.kyotaku * RIICHI_STICK == TOTAL_POINTS

  val type_ok: bool = and {
    state.session.in(Set(Idle, Negotiating, Active, Ended)),
    state.transport.in(Set(Connected, Disconnected)),
    state.negotiation.in(Set(NoNegotiation, HelloSent, JoinSent, NegotiationAccepted, Rejected)),
    state.hello_version.in(Set(NoVersion, Draft5, UnsupportedVersion)),
    state.join_version.in(Set(NoVersion, Draft5, UnsupportedVersion)),
    state.hello_profile.in(Set(NoProfile, Riichi4p, UnsupportedProfile)),
    state.join_profile.in(Set(NoProfile, Riichi4p, UnsupportedProfile)),
    state.hello_hash.in(Set(NoProfileHash, GoodProfileHash, BadProfileHash)),
    state.join_hash.in(Set(NoProfileHash, GoodProfileHash, BadProfileHash)),
    state.hello_required.subseteq(ALL_CAPS),
    state.hello_optional.subseteq(ALL_CAPS),
    state.join_required.subseteq(ALL_CAPS),
    state.join_optional.subseteq(ALL_CAPS),
    state.negotiated_caps.subseteq(SUPPORTED_CAPS),
    state.host_seq.in(0.to(MAX_SEQ)),
    state.previous_host_seq.in(0.to(MAX_SEQ)),
    state.applied_seq.in(0.to(MAX_SEQ)),
    state.applied_seq <= state.host_seq,
    state.wire_seq.in(0.to(MAX_SEQ)),
    state.gap_expected.in(0.to(MAX_SEQ)),
    state.gap_received.in(0.to(MAX_SEQ)),
    state.replay_from_seq.in(0.to(MAX_SEQ)),
    state.replay_to_seq.in(0.to(MAX_SEQ)),
    state.backlog_from_seq.in(0.to(MAX_SEQ)),
    state.backlog_to_seq.in(0.to(MAX_SEQ)),
    state.backlog_to_seq <= state.host_seq,
    state.backlog_pending implies state.backlog_from_seq > 0,
    state.backlog_pending implies state.backlog_from_seq <= state.backlog_to_seq,
    not(state.backlog_pending) implies state.backlog_from_seq == 0,
    not(state.backlog_pending) implies state.backlog_to_seq == 0,
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
    state.snapshot_saved_request_ids.keys() == SEATS,
    state.snapshot_saved_actions.keys() == SEATS,
    state.request_ids.keys().forall(seat => state.request_ids.get(seat).in(0.to(MAX_REQUEST_ID))),
    state.submitted_actions.keys().forall(seat => state.submitted_actions.get(seat).in(ACTION_IDS)),
    state.ack_statuses.keys().forall(seat => state.ack_statuses.get(seat).in(Set(AckNone, Accepted, Passed, Defaulted))),
    state.deadline.in(0.to(MAX_TIMEOUT)),
    state.next_request_id.in(0.to(MAX_REQUEST_ID)),
    state.seq_reservation_end.in(0.to(MAX_SEQ)),
    state.resume_phase.in(Set(NoResume, ResumeReady, ReplayPending, SnapshotPending, ResumeComplete)),
    state.resume_token_version.in(0.to(MAX_TOKEN_VERSION)),
    state.snapshot_seq.in(0.to(MAX_SEQ)),
    state.snapshot_replaces_through_seq.in(0.to(MAX_SEQ)),
    state.snapshot_saved_members.subseteq(SEATS),
    state.snapshot_saved_terminal.subseteq(SEATS),
    state.snapshot_saved_defaulted.subseteq(SEATS),
    state.score_0.in(0.to(TOTAL_POINTS)),
    state.score_1.in(0.to(TOTAL_POINTS)),
    state.score_2.in(0.to(TOTAL_POINTS)),
    state.score_3.in(0.to(TOTAL_POINTS)),
    state.kyotaku.in(0.to(TOTAL_POINTS)),
  }

  // Every transition records a prior host seq; host-message actions advance
  // it, and player/clock/transport actions keep it.  This is an explicit
  // state invariant rather than an uncheckable comment about monotonicity.
  val host_seq_non_decreasing: bool = and {
    state.host_seq >= state.previous_host_seq,
    state.host_seq <= state.seq_reservation_end,
  }

  val negotiation_invariant: bool = and {
    state.session == Active implies state.negotiation == NegotiationAccepted,
    state.session == Active implies state.hello_version == Draft5,
    state.session == Active implies state.join_version == Draft5,
    state.session == Active implies state.hello_profile == Riichi4p,
    state.session == Active implies state.join_profile == Riichi4p,
    state.session == Active implies state.hello_hash == GoodProfileHash,
    state.session == Active implies state.join_hash == GoodProfileHash,
    state.session == Active implies state.hello_required.subseteq(SUPPORTED_CAPS),
    state.session == Active implies state.join_required.subseteq(SUPPORTED_CAPS),
    state.negotiation == Rejected implies state.session == Ended,
    state.session == Idle implies state.host_seq == 0,
  }

  val wire_invariant: bool = and {
    state.wire_available implies state.wire_seq <= state.host_seq,
    state.replay_pending implies not(state.wire_available),
    state.replay_in_progress implies not(state.wire_available),
    state.replay_pending implies state.gap_received > state.gap_expected,
    state.replay_pending implies state.seq_disposition == Gap,
    state.replay_in_progress implies not(state.replay_pending),
    state.replay_in_progress implies state.replay_to_seq >= state.replay_from_seq,
    state.backlog_pending implies state.backlog_to_seq <= state.host_seq,
    state.fatal implies state.session == Ended,
    state.fatal implies state.transport == Disconnected,
  }

  val request_lifecycle_invariant: bool = and {
    state.request_state == RequestOpen implies state.group_state == GroupOpen,
    state.group_state == GroupOpen implies state.request_state == RequestOpen,
    state.group_state == GroupClosed implies state.request_state == RequestAcked,
    state.group_state == GroupResolved implies state.request_state == NoRequest,
    state.request_state == NoRequest implies state.pending_members == Set(),
    state.group_state == GroupClosed implies state.terminal_members == state.pending_members,
    state.group_state == GroupOpen implies state.pending_members.size() > 0,
  }

  val request_capacity_invariant: bool =
    state.request_state == NoRequest or state.host_seq < state.seq_reservation_end

  val request_id_invariant: bool = and {
    state.pending_members.forall(seat => state.request_ids.get(seat) > 0),
    state.pending_members == GROUP_SEATS implies state.request_ids.get(1) != state.request_ids.get(2),
  }

  val request_action_invariant: bool = and {
    state.pending_members.exclude(state.responded_members).forall(seat => state.submitted_actions.get(seat) == NoAction),
    state.responded_members.exclude(state.terminal_members).forall(seat => state.submitted_actions.get(seat).in(Set(PassAction, ReachAction, CallAction))),
  }

  val request_status_invariant: bool = and {
    state.defaulted_members.forall(seat => state.submitted_actions.get(seat) == DefaultAction),
    state.terminal_members.forall(seat => state.ack_statuses.get(seat) != AckNone),
    state.defaulted_members.forall(seat => state.ack_statuses.get(seat) == Defaulted),
    state.pending_members.exclude(state.terminal_members).forall(seat => state.ack_statuses.get(seat) == AckNone),
  }

  val request_invariant: bool = and {
    request_lifecycle_invariant,
    request_capacity_invariant,
    request_id_invariant,
    request_action_invariant,
    request_status_invariant,
  }

  val resume_snapshot_invariant: bool = and {
    state.snapshot_seq == 0 or state.snapshot_seq == state.snapshot_replaces_through_seq + 1,
    state.snapshot_replaces_through_seq <= state.host_seq,
    state.snapshot_preserved_pending implies state.snapshot_seq > 0,
    state.snapshot_preserved_pending implies state.snapshot_seq <= state.host_seq,
    state.snapshot_saved_terminal.subseteq(state.snapshot_saved_members),
    state.snapshot_saved_defaulted.subseteq(state.snapshot_saved_terminal),
    state.resume_phase == SnapshotPending implies state.snapshot_preserved_pending,
  }

  // A disconnect/resume path never discards an open request.  ReplayPending
  // is entered only after reconnection and remains processable because all
  // request/ACK/default/resolve actions are guarded until replay completes.
  // SnapshotPending additionally records the exact pending member/action
  // maps in the snapshot_saved_* fields above.
  val resume_pending_invariant: bool = and {
    state.resume_phase == SnapshotPending implies state.snapshot_preserved_pending,
    state.snapshot_preserved_pending implies state.snapshot_seq > 0,
    state.snapshot_preserved_pending implies state.backlog_pending,
  }

  val terminalization_invariant: bool = and {
    state.end_kyoku_sent implies state.request_state == NoRequest,
    state.end_kyoku_sent implies state.pending_members == Set(),
    state.end_game_sent implies state.request_state == NoRequest,
    state.end_game_sent implies state.pending_members == Set(),
    state.end_game_sent implies state.terminal_members == Set(),
  }

  val protocol_invariant: bool = and {
    type_ok,
    host_seq_non_decreasing,
    negotiation_invariant,
    wire_invariant,
    request_invariant,
    resume_snapshot_invariant,
    resume_pending_invariant,
    terminalization_invariant,
    score_conservation,
  }

  // Reachability witnesses make each extended branch observable.  Liveness
  // is checked in the smaller request and resume-delivery models, so this
  // full model is used for safety and witness checking only.
  val witness_negotiated_active: bool = state.session == Active and state.negotiation == NegotiationAccepted
  val witness_rejected_version: bool = state.negotiation == Rejected and state.join_version == UnsupportedVersion
  val witness_rejected_profile: bool = state.negotiation == Rejected and state.join_profile == UnsupportedProfile
  val witness_rejected_capability: bool = state.negotiation == Rejected and state.join_required.contains(UnknownCap)
  val witness_gap: bool = state.seq_disposition == Gap or state.replay_pending
  val witness_duplicate: bool = state.seq_disposition == Duplicate
  val witness_conflict: bool = state.seq_disposition == Conflict or state.fatal
  val witness_replayed: bool = state.seq_disposition == Replayed
  val witness_request_group: bool = state.group_state == GroupOpen
  val witness_deadline_boundary: bool = state.request_state == RequestOpen and state.deadline == 0
  val witness_pending_resume: bool = state.resume_phase == ReplayPending
  val witness_pending_snapshot: bool = state.resume_phase == SnapshotPending
  val witness_terminalized_end: bool = state.end_kyoku_sent or state.end_game_sent

  // ------------------------------------------------------------------------
  // Initial state and negotiation
  // ------------------------------------------------------------------------

  action init = state' = {
    session: Idle,
    transport: Connected,
    negotiation: NoNegotiation,
    hello_version: NoVersion,
    hello_profile: NoProfile,
    hello_hash: NoProfileHash,
    hello_required: Set(),
    hello_optional: Set(),
    join_version: NoVersion,
    join_profile: NoProfile,
    join_hash: NoProfileHash,
    join_required: Set(),
    join_optional: Set(),
    negotiated_caps: Set(),
    host_seq: 0,
    previous_host_seq: 0,
    applied_seq: 0,
    wire_available: false,
    wire_seq: 0,
    wire_payload: PayloadA,
    applied_payload: PayloadA,
    seq_disposition: NoSeq,
    gap_expected: 0,
    gap_received: 0,
    replay_pending: false,
    replay_in_progress: false,
    replay_from_seq: 0,
    replay_to_seq: 0,
    backlog_pending: false,
    backlog_from_seq: 0,
    backlog_to_seq: 0,
    request_state: NoRequest,
    group_state: NoGroup,
    pending_members: Set(),
    responded_members: Set(),
    terminal_members: Set(),
    defaulted_members: Set(),
    request_ids: EMPTY_REQUEST_IDS,
    submitted_actions: EMPTY_ACTIONS,
    ack_statuses: EMPTY_ACKS,
    deadline: 0,
    next_request_id: 0,
    seq_reservation_end: 0,
    resume_phase: NoResume,
    resume_token_version: 0,
    resume_available: true,
    resumed: false,
    snapshot_seq: 0,
    snapshot_replaces_through_seq: 0,
    snapshot_preserved_pending: false,
    snapshot_saved_members: Set(),
    snapshot_saved_request_ids: EMPTY_REQUEST_IDS,
    snapshot_saved_actions: EMPTY_ACTIONS,
    snapshot_saved_terminal: Set(),
    snapshot_saved_defaulted: Set(),
    end_kyoku_sent: false,
    end_game_sent: false,
    fatal: false,
    score_0: STARTING_POINTS,
    score_1: STARTING_POINTS,
    score_2: STARTING_POINTS,
    score_3: STARTING_POINTS,
    kyotaku: 0,
  }

  action send_hello = all {
    state.session == Idle,
    state' = {
      session: Negotiating,
      negotiation: HelloSent,
      hello_version: Draft5,
      hello_profile: Riichi4p,
      hello_hash: GoodProfileHash,
      hello_required: Set(),
      hello_optional: Set(ResumeCap, SnapshotCap),
      previous_host_seq: state.host_seq,
      ...state,
    },
  }

  action send_join_good = all {
    state.session == Negotiating,
    state.negotiation == HelloSent,
    state' = {
      negotiation: JoinSent,
      join_version: Draft5,
      join_profile: Riichi4p,
      join_hash: GoodProfileHash,
      join_required: Set(),
      join_optional: Set(ResumeCap, SnapshotCap),
      previous_host_seq: state.host_seq,
      ...state,
    },
  }

  action send_join_bad_version = all {
    state.session == Negotiating,
    state.negotiation == HelloSent,
    state' = {
      negotiation: JoinSent,
      join_version: UnsupportedVersion,
      join_profile: Riichi4p,
      join_hash: GoodProfileHash,
      join_required: Set(),
      join_optional: Set(),
      previous_host_seq: state.host_seq,
      ...state,
    },
  }

  action send_join_bad_profile = all {
    state.session == Negotiating,
    state.negotiation == HelloSent,
    state' = {
      negotiation: JoinSent,
      join_version: Draft5,
      join_profile: UnsupportedProfile,
      join_hash: BadProfileHash,
      join_required: Set(),
      join_optional: Set(),
      previous_host_seq: state.host_seq,
      ...state,
    },
  }

  action send_join_bad_capability = all {
    state.session == Negotiating,
    state.negotiation == HelloSent,
    state' = {
      negotiation: JoinSent,
      join_version: Draft5,
      join_profile: Riichi4p,
      join_hash: GoodProfileHash,
      join_required: Set(UnknownCap),
      join_optional: Set(),
      previous_host_seq: state.host_seq,
      ...state,
    },
  }

  action reject_join = all {
    state.session == Negotiating,
    state.negotiation == JoinSent,
    state.join_version != Draft5 or state.join_profile != Riichi4p or state.join_hash != GoodProfileHash or not(state.join_required.subseteq(SUPPORTED_CAPS)),
    state' = {
      session: Ended,
      negotiation: Rejected,
      previous_host_seq: state.host_seq,
      ...state,
    },
  }

  action send_welcome = all {
    state.session == Negotiating,
    state.negotiation == JoinSent,
    state.join_version == Draft5,
    state.join_profile == Riichi4p,
    state.join_hash == GoodProfileHash,
    state.hello_required.subseteq(SUPPORTED_CAPS),
    state.join_required.subseteq(SUPPORTED_CAPS),
    state' = {
      session: Active,
      negotiation: NegotiationAccepted,
      negotiated_caps: state.hello_required.union(state.join_required).union(state.hello_optional.intersect(state.join_optional)),
      previous_host_seq: state.host_seq,
      ...state,
    },
  }

  // ------------------------------------------------------------------------
  // Wire sequence handling
  // ------------------------------------------------------------------------

  action host_emit_event = all {
    state.session == Active,
    state.transport == Connected,
    state.request_state == NoRequest,
    state.group_state.in(Set(NoGroup, GroupResolved)),
    not(state.wire_available),
    state.host_seq == state.applied_seq,
    state.host_seq < MAX_SEQ,
    state' = {
      host_seq: state.host_seq + 1,
      previous_host_seq: state.host_seq,
      wire_available: true,
      wire_seq: state.host_seq + 1,
      wire_payload: PayloadA,
      seq_disposition: NoSeq,
      seq_reservation_end: state.host_seq + 1,
      group_state: NoGroup,
      backlog_pending: if (backlog_capture_active) true else state.backlog_pending,
      backlog_from_seq: if (backlog_capture_active) backlog_next_from_seq else state.backlog_from_seq,
      backlog_to_seq: if (backlog_capture_active) state.host_seq + 1 else state.backlog_to_seq,
      ...state,
    },
  }

  // Deliberately omit one sent sequence to create a finite sequence_gap case.
  action host_emit_gap_message = all {
    state.session == Active,
    state.transport == Connected,
    state.request_state == NoRequest,
    state.group_state.in(Set(NoGroup, GroupResolved)),
    not(state.wire_available),
    state.host_seq == state.applied_seq,
    state.host_seq + 2 <= MAX_SEQ,
    state' = {
      host_seq: state.host_seq + 2,
      previous_host_seq: state.host_seq,
      wire_available: true,
      wire_seq: state.host_seq + 2,
      wire_payload: PayloadA,
      seq_disposition: NoSeq,
      seq_reservation_end: state.host_seq + 2,
      group_state: NoGroup,
      backlog_pending: if (backlog_capture_active) true else state.backlog_pending,
      backlog_from_seq: if (backlog_capture_active) backlog_next_from_seq else state.backlog_from_seq,
      backlog_to_seq: if (backlog_capture_active) state.host_seq + 2 else state.backlog_to_seq,
      ...state,
    },
  }

  action player_apply_fresh = all {
    state.session == Active,
    state.wire_available,
    state.wire_seq == state.applied_seq + 1,
    state' = {
      applied_seq: state.wire_seq,
      previous_host_seq: state.host_seq,
      wire_available: false,
      applied_payload: state.wire_payload,
      seq_disposition: Fresh,
      ...state,
    },
  }

  action player_detect_gap = all {
    state.session == Active,
    state.wire_available,
    state.wire_seq > state.applied_seq + 1,
    state' = {
      previous_host_seq: state.host_seq,
      wire_available: false,
      gap_expected: state.applied_seq + 1,
      gap_received: state.wire_seq,
      replay_pending: true,
      seq_disposition: Gap,
      ...state,
    },
  }

  action host_retransmit_exact = all {
    state.session == Active,
    state.applied_seq > 0,
    not(state.wire_available),
    not(state.replay_pending),
    not(state.replay_in_progress),
    state' = {
      previous_host_seq: state.host_seq,
      wire_available: true,
      wire_seq: state.applied_seq,
      wire_payload: state.applied_payload,
      ...state,
    },
  }

  action host_retransmit_conflict = all {
    state.session == Active,
    state.applied_seq > 0,
    not(state.wire_available),
    not(state.replay_pending),
    not(state.replay_in_progress),
    state' = {
      previous_host_seq: state.host_seq,
      wire_available: true,
      wire_seq: state.applied_seq,
      wire_payload: PayloadB,
      ...state,
    },
  }

  action player_accept_duplicate = all {
    state.session == Active,
    state.wire_available,
    state.wire_seq <= state.applied_seq,
    state.wire_payload == state.applied_payload,
    state' = {
      previous_host_seq: state.host_seq,
      wire_available: false,
      seq_disposition: Duplicate,
      ...state,
    },
  }

  action player_reject_conflict = all {
    state.session == Active,
    state.wire_available,
    state.wire_seq == state.applied_seq,
    state.wire_payload != state.applied_payload,
    state' = {
      session: Ended,
      transport: Disconnected,
      previous_host_seq: state.host_seq,
      wire_available: false,
      // A fatal sequence conflict terminates the resume attempt as well; a
      // terminal session must not retain a replay-pending marker that would
      // claim a disconnected peer can still make progress.
      replay_pending: false,
      replay_in_progress: false,
      resume_phase: NoResume,
      snapshot_preserved_pending: false,
      backlog_pending: false,
      backlog_from_seq: 0,
      backlog_to_seq: 0,
      seq_disposition: Conflict,
      fatal: true,
      ...state,
    },
  }

  action host_replay_range = all {
    state.session == Active,
    state.replay_pending,
    not(state.wire_available),
    state' = {
      previous_host_seq: state.host_seq,
      replay_pending: false,
      replay_in_progress: true,
      replay_from_seq: state.gap_expected,
      replay_to_seq: state.host_seq,
      ...state,
    },
  }

  action apply_replay_range = all {
    state.session == Active,
    state.replay_in_progress,
    state' = {
      previous_host_seq: state.host_seq,
      applied_seq: state.replay_to_seq,
      replay_in_progress: false,
      seq_disposition: Replayed,
      ...state,
    },
  }

  action replay_progress = any {
    host_replay_range,
    apply_replay_range,
  }

  // ------------------------------------------------------------------------
  // Request groups, timeout/default, and terminalization
  // ------------------------------------------------------------------------

  // These are host-internal transitions.  They deliberately have no
  // transport or resume-phase guard: disconnect, ReplayPending, and
  // SnapshotPending do not stop the host clock or request transaction.  Any
  // resulting host messages are recorded in the bounded backlog below.

  action open_single_request = all {
    state.session == Active,
    state.negotiation == NegotiationAccepted,
    state.transport == Connected,
    state.request_state == NoRequest,
    state.group_state == NoGroup,
    not(state.end_kyoku_sent),
    not(state.wire_available),
    not(state.replay_pending),
    not(state.replay_in_progress),
    state.resume_phase != SnapshotPending,
    state.resume_phase != ReplayPending,
    state.host_seq == state.applied_seq,
    state.next_request_id + 1 <= MAX_REQUEST_ID,
    state.host_seq + SINGLE_RESERVATION <= MAX_SEQ,
    state' = {
      host_seq: state.host_seq + 1,
      previous_host_seq: state.host_seq,
      applied_seq: state.applied_seq + 1,
      request_state: RequestOpen,
      group_state: GroupOpen,
      pending_members: Set(SINGLE_SEAT),
      responded_members: Set(),
      terminal_members: Set(),
      defaulted_members: Set(),
      request_ids: EMPTY_REQUEST_IDS.set(0, state.next_request_id + 1),
      submitted_actions: EMPTY_ACTIONS,
      ack_statuses: EMPTY_ACKS,
      deadline: MAX_TIMEOUT,
      next_request_id: state.next_request_id + 1,
      seq_reservation_end: state.host_seq + SINGLE_RESERVATION,
      resume_phase: NoResume,
      snapshot_preserved_pending: false,
      backlog_pending: if (backlog_capture_active) true else state.backlog_pending,
      backlog_from_seq: if (backlog_capture_active) backlog_next_from_seq else state.backlog_from_seq,
      backlog_to_seq: if (backlog_capture_active) state.host_seq + 1 else state.backlog_to_seq,
      ...state,
    },
  }

  action open_decision_group = all {
    state.session == Active,
    state.negotiation == NegotiationAccepted,
    state.transport == Connected,
    state.request_state == NoRequest,
    state.group_state == NoGroup,
    not(state.end_kyoku_sent),
    not(state.wire_available),
    not(state.replay_pending),
    not(state.replay_in_progress),
    state.resume_phase != SnapshotPending,
    state.resume_phase != ReplayPending,
    state.host_seq == state.applied_seq,
    state.next_request_id + 2 <= MAX_REQUEST_ID,
    state.host_seq + GROUP_RESERVATION <= MAX_SEQ,
    state' = {
      host_seq: state.host_seq + 2,
      previous_host_seq: state.host_seq,
      applied_seq: state.applied_seq + 2,
      request_state: RequestOpen,
      group_state: GroupOpen,
      pending_members: GROUP_SEATS,
      responded_members: Set(),
      terminal_members: Set(),
      defaulted_members: Set(),
      request_ids: EMPTY_REQUEST_IDS.set(1, state.next_request_id + 1).set(2, state.next_request_id + 2),
      submitted_actions: EMPTY_ACTIONS,
      ack_statuses: EMPTY_ACKS,
      deadline: MAX_TIMEOUT,
      next_request_id: state.next_request_id + 2,
      seq_reservation_end: state.host_seq + GROUP_RESERVATION,
      resume_phase: NoResume,
      snapshot_preserved_pending: false,
      backlog_pending: if (backlog_capture_active) true else state.backlog_pending,
      backlog_from_seq: if (backlog_capture_active) backlog_next_from_seq else state.backlog_from_seq,
      backlog_to_seq: if (backlog_capture_active) state.host_seq + 2 else state.backlog_to_seq,
      ...state,
    },
  }

  action submit_action(seat, action_id) = all {
    state.session == Active,
    state.request_state == RequestOpen,
    state.group_state == GroupOpen,
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
      applied_seq: state.applied_seq + 1,
      terminal_members: state.terminal_members.union(Set(0)),
      ack_statuses: state.ack_statuses.set(0, if (state.submitted_actions.get(0) == PassAction) Passed else Accepted),
      request_state: if (state.terminal_members.union(Set(0)) == state.pending_members) RequestAcked else RequestOpen,
      group_state: if (state.terminal_members.union(Set(0)) == state.pending_members) GroupClosed else GroupOpen,
      backlog_pending: if (backlog_capture_active) true else state.backlog_pending,
      backlog_from_seq: if (backlog_capture_active) backlog_next_from_seq else state.backlog_from_seq,
      backlog_to_seq: if (backlog_capture_active) state.host_seq + 1 else state.backlog_to_seq,
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
      applied_seq: state.applied_seq + 1,
      terminal_members: state.terminal_members.union(Set(1)),
      ack_statuses: state.ack_statuses.set(1, if (state.submitted_actions.get(1) == PassAction) Passed else Accepted),
      request_state: if (state.terminal_members.union(Set(1)) == state.pending_members) RequestAcked else RequestOpen,
      group_state: if (state.terminal_members.union(Set(1)) == state.pending_members) GroupClosed else GroupOpen,
      backlog_pending: if (backlog_capture_active) true else state.backlog_pending,
      backlog_from_seq: if (backlog_capture_active) backlog_next_from_seq else state.backlog_from_seq,
      backlog_to_seq: if (backlog_capture_active) state.host_seq + 1 else state.backlog_to_seq,
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
      applied_seq: state.applied_seq + 1,
      terminal_members: state.terminal_members.union(Set(2)),
      ack_statuses: state.ack_statuses.set(2, if (state.submitted_actions.get(2) == PassAction) Passed else Accepted),
      request_state: if (state.terminal_members.union(Set(2)) == state.pending_members) RequestAcked else RequestOpen,
      group_state: if (state.terminal_members.union(Set(2)) == state.pending_members) GroupClosed else GroupOpen,
      backlog_pending: if (backlog_capture_active) true else state.backlog_pending,
      backlog_from_seq: if (backlog_capture_active) backlog_next_from_seq else state.backlog_from_seq,
      backlog_to_seq: if (backlog_capture_active) state.host_seq + 1 else state.backlog_to_seq,
      ...state,
    },
  }

  action tick = all {
    state.session == Active,
    state.request_state == RequestOpen,
    state.deadline > 0,
    state' = { deadline: state.deadline - 1, previous_host_seq: state.host_seq, ...state },
  }

  action default_action = {
    // Deadline defaults only a member that did not submit an action.  A
    // submitted-but-unACKed action remains in its map and can still be ACKed.
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
      state.session == Active,
      state.request_state == RequestOpen,
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
        backlog_pending: if (backlog_capture_active) true else state.backlog_pending,
        backlog_from_seq: if (backlog_capture_active) backlog_next_from_seq else state.backlog_from_seq,
        backlog_to_seq: if (backlog_capture_active) state.host_seq + missing_members.size() else state.backlog_to_seq,
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
    state.session == Active,
    state.group_state == GroupClosed,
    state.request_state == RequestAcked,
    state.terminal_members == state.pending_members,
    state.host_seq < state.seq_reservation_end,
    not(state.submitted_actions.get(0) == ReachAction) or state.score_0 >= RIICHI_STICK,
    state' = {
      host_seq: state.host_seq + 1,
      previous_host_seq: state.host_seq,
      applied_seq: state.applied_seq + 1,
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
      backlog_pending: if (backlog_capture_active) true else state.backlog_pending,
      backlog_from_seq: if (backlog_capture_active) backlog_next_from_seq else state.backlog_from_seq,
      backlog_to_seq: if (backlog_capture_active) state.host_seq + 1 else state.backlog_to_seq,
      score_0: if (state.submitted_actions.get(0) == ReachAction) state.score_0 - RIICHI_STICK else state.score_0,
      kyotaku: if (state.submitted_actions.get(0) == ReachAction) state.kyotaku + 1 else state.kyotaku,
      ...state,
    },
  }

  action settle_kyotaku = all {
    state.session == Active,
    state.group_state == GroupResolved,
    state.kyotaku > 0,
    state.host_seq < MAX_SEQ,
    state' = {
      host_seq: state.host_seq + 1,
      previous_host_seq: state.host_seq,
      applied_seq: state.applied_seq + 1,
      group_state: NoGroup,
      seq_reservation_end: state.host_seq + 1,
      backlog_pending: if (backlog_capture_active) true else state.backlog_pending,
      backlog_from_seq: if (backlog_capture_active) backlog_next_from_seq else state.backlog_from_seq,
      backlog_to_seq: if (backlog_capture_active) state.host_seq + 1 else state.backlog_to_seq,
      score_1: state.score_1 + state.kyotaku * RIICHI_STICK,
      kyotaku: 0,
      ...state,
    },
  }

  action end_kyoku = all {
    state.session == Active,
    state.request_state == NoRequest,
    state.group_state.in(Set(NoGroup, GroupResolved)),
    not(state.wire_available),
    not(state.replay_pending),
    not(state.replay_in_progress),
    state.resume_phase != SnapshotPending,
    state.resume_phase != ReplayPending,
    state.host_seq == state.applied_seq,
    state.host_seq < MAX_SEQ,
    state' = {
      host_seq: state.host_seq + 1,
      previous_host_seq: state.host_seq,
      applied_seq: state.applied_seq + 1,
      group_state: NoGroup,
      seq_reservation_end: state.host_seq + 1,
      end_kyoku_sent: true,
      backlog_pending: if (backlog_capture_active) true else state.backlog_pending,
      backlog_from_seq: if (backlog_capture_active) backlog_next_from_seq else state.backlog_from_seq,
      backlog_to_seq: if (backlog_capture_active) state.host_seq + 1 else state.backlog_to_seq,
      ...state,
    },
  }

  action end_game = all {
    state.session == Active,
    state.request_state == NoRequest,
    state.group_state.in(Set(NoGroup, GroupResolved)),
    not(state.wire_available),
    not(state.replay_pending),
    not(state.replay_in_progress),
    state.resume_phase != SnapshotPending,
    state.resume_phase != ReplayPending,
    state.host_seq == state.applied_seq,
    state.host_seq < MAX_SEQ,
    state' = {
      session: Ended,
      group_state: NoGroup,
      host_seq: state.host_seq + 1,
      previous_host_seq: state.host_seq,
      applied_seq: state.applied_seq + 1,
      seq_reservation_end: state.host_seq + 1,
      end_game_sent: true,
      backlog_pending: if (backlog_capture_active) true else state.backlog_pending,
      backlog_from_seq: if (backlog_capture_active) backlog_next_from_seq else state.backlog_from_seq,
      backlog_to_seq: if (backlog_capture_active) state.host_seq + 1 else state.backlog_to_seq,
      ...state,
    },
  }

  // ------------------------------------------------------------------------
  // Disconnect/resume/replay/snapshot
  // ------------------------------------------------------------------------

  action disconnect = all {
    state.session == Active,
    state.transport == Connected,
    state' = {
      transport: Disconnected,
      resume_available: true,
      // Keep a pending replay/snapshot marker across a transport loss so the
      // host can finish that transfer after the next resume.  A normal
      // connection loss starts a fresh ResumeReady phase.
      resume_phase: if (state.resume_phase == ReplayPending or state.resume_phase == SnapshotPending)
        state.resume_phase
      else ResumeReady,
      previous_host_seq: state.host_seq,
      ...state,
    },
  }

  action resume = all {
    state.session == Active,
    state.transport == Disconnected,
    state.resume_available,
    state.negotiated_caps.contains(ResumeCap),
    state' = {
      transport: Connected,
      resumed: true,
      resume_available: false,
      resume_phase: if (state.resume_phase == ReplayPending or state.resume_phase == SnapshotPending)
        state.resume_phase
      else ResumeReady,
      previous_host_seq: state.host_seq,
      resume_token_version: if (state.resume_token_version == MAX_TOKEN_VERSION) 0 else state.resume_token_version + 1,
      ...state,
    },
  }

  action begin_resume_replay = all {
    state.session == Active,
    state.transport == Connected,
    state.resume_phase == ResumeReady,
    state.request_state != NoRequest,
    state' = {
      resume_phase: ReplayPending,
      replay_from_seq: if (state.backlog_pending) state.backlog_from_seq else state.replay_from_seq,
      replay_to_seq: if (state.backlog_pending) state.backlog_to_seq else state.host_seq,
      previous_host_seq: state.host_seq,
      ...state,
    },
  }

  action finish_resume_replay = all {
    state.session == Active,
    state.transport == Connected,
    state.resume_phase == ReplayPending,
    state' = {
      resume_phase: ResumeComplete,
      backlog_pending: false,
      backlog_from_seq: 0,
      backlog_to_seq: 0,
      previous_host_seq: state.host_seq,
      ...state,
    },
  }

  action emit_pending_snapshot = all {
    state.session == Active,
    state.transport == Connected,
    state.resume_phase == ResumeReady,
    state.request_state == RequestOpen,
    state.negotiated_caps.contains(SnapshotCap),
    not(state.wire_available),
    state.deadline > 0,
    // The finite reservation includes one optional pending snapshot; a
    // concrete implementation may send more than one over a long resume.
    state.snapshot_seq == 0,
    state.host_seq + 1 < state.seq_reservation_end,
    state' = {
      host_seq: state.host_seq + 1,
      previous_host_seq: state.host_seq,
      applied_seq: state.applied_seq + 1,
      resume_phase: SnapshotPending,
      snapshot_seq: state.host_seq + 1,
      snapshot_replaces_through_seq: state.applied_seq,
      snapshot_preserved_pending: true,
      snapshot_saved_members: state.pending_members,
      snapshot_saved_request_ids: state.request_ids,
      snapshot_saved_actions: state.submitted_actions,
      snapshot_saved_terminal: state.terminal_members,
      snapshot_saved_defaulted: state.defaulted_members,
      backlog_pending: true,
      backlog_from_seq: if (state.backlog_pending) state.backlog_from_seq else state.host_seq + 1,
      backlog_to_seq: state.host_seq + 1,
      ...state,
    },
  }

  action finish_snapshot_resume = all {
    state.session == Active,
    state.transport == Connected,
    state.resume_phase == SnapshotPending,
    state' = {
      resume_phase: ResumeComplete,
      snapshot_preserved_pending: false,
      backlog_pending: false,
      backlog_from_seq: 0,
      backlog_to_seq: 0,
      previous_host_seq: state.host_seq,
      ...state,
    },
  }

  action finish_resume_empty = all {
    state.session == Active,
    state.transport == Connected,
    state.resume_phase == ResumeReady,
    state.request_state == NoRequest,
    state' = {
      resume_phase: ResumeComplete,
      backlog_pending: false,
      backlog_from_seq: 0,
      backlog_to_seq: 0,
      previous_host_seq: state.host_seq,
      ...state,
    },
  }

  action resume_progress = any {
    finish_resume_replay,
    finish_snapshot_resume,
    finish_resume_empty,
  }

  // Stuttering is disallowed at the final reserved seq of an open request.
  action stutter = all {
    state.request_state == NoRequest or state.host_seq + 1 < state.seq_reservation_end,
    state' = { previous_host_seq: state.host_seq, ...state },
  }

  action step = any {
    send_hello,
    send_join_good,
    send_join_bad_version,
    send_join_bad_profile,
    send_join_bad_capability,
    reject_join,
    send_welcome,
    host_emit_event,
    host_emit_gap_message,
    player_apply_fresh,
    player_detect_gap,
    host_retransmit_exact,
    host_retransmit_conflict,
    player_accept_duplicate,
    player_reject_conflict,
    replay_progress,
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
    end_kyoku,
    end_game,
    disconnect,
    resume,
    begin_resume_replay,
    emit_pending_snapshot,
    resume_progress,
    stutter,
  }

}
