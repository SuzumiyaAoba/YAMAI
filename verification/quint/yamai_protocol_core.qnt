// Executable protocol core for YAMAI 1.0-draft.5.
//
// Unlike the historical control-flow models, this model has one canonical
// state.  Requests are four independent slots, messages are an append-only
// sequence ledger, and delivery/applied state is recorded separately.  The
// bounded values are deliberate: this is a finite refinement target for TLC,
// not a replacement for the unbounded wire implementation.
//
// Refinement boundary:
//   - a concrete session maps to `session`, `game`, and `turn`;
//   - every host message maps to exactly one immutable ledger entry;
//   - a concrete request/group maps to one occupied request slot;
//   - transport observations map to `delivery` and `applied_tags`;
//   - a concrete resume snapshot maps to the captured slot/id/member maps.
// See `refinement_*` predicates at the end of this file.

module yamai_protocol_core {
  type SessionState = Idle | Negotiating | Active | Ended
  type GameState = NoGame | GameReady | KyokuRunning | KyokuEnded | GameEnded
  type TurnState = NoTurn | AwaitingAction | Resolving | TurnComplete
  type TransportState = Connected | Disconnected
  type ResumeState = NoResume | ResumeReady | ReplayPending | SnapshotPending | ResumeComplete
  type RequestKind = NoKind | SingleKind | GroupKind
  type RequestState = EmptyRequest | OpenRequest | ClosedRequest | ResolvedRequest | SupersededRequest
  type GroupState = NoGroup | GroupOpen | GroupClosed | GroupResolved | GroupSuperseded
  type ActionKind = NoAction | PassAction | ReachAction | CallAction | InvalidAction | DefaultAction
  type AckResult = AckNone | AckAccepted | AckPassed | AckDefaulted | AckSuperseded | AckStale | AckRejected
  type ErrorCode = NoError | BadRequest | TooLate | DuplicateRequest | SequenceConflict | Superseded | Unauthorized
  type MessageKind = NoMessage | RequestMessage | ActionAckMessage | DefaultMessage | ResolveMessage | SnapshotMessage | ReplayMessage | ErrorMessage
  type PayloadTag = EmptyPayload | RequestPayload | AcceptedPayload | PassedPayload | DefaultedPayload | SupersededPayload | StalePayload | RejectedPayload | ResolvePayload | SnapshotPayload | ReplayPayload | ErrorPayload
  type DeliveryState = NotDelivered | Delivered | Applied | DuplicateDelivered | RejectedDelivery

  pure val SEATS: Set[int] = Set(0, 1, 2, 3)
  pure val SLOTS: Set[int] = Set(0, 1, 2, 3)
  pure val EMPTY_SET: Set[int] = Set()
  pure val SINGLE_SEAT: Set[int] = Set(0)
  pure val GROUP_SEATS: Set[int] = Set(1, 2)
  pure val MAX_SEQ: int = 24
  pure val MAX_REQUEST_ID: int = 16
  pure val MAX_GROUP_ID: int = 16
  pure val MAX_CLOCK: int = 32
  pure val DEADLINE_TICKS: int = 3
  pure val GRACE_TICKS: int = 1
  pure val TIMEBANK_TICKS: int = 3
  pure val MAX_TIMEBANK: int = 3
  pure val EMPTY_IDS: int -> int = Map(0 -> 0, 1 -> 0, 2 -> 0, 3 -> 0)
  pure val EMPTY_ACTIONS: int -> ActionKind = Map(0 -> NoAction, 1 -> NoAction, 2 -> NoAction, 3 -> NoAction)
  pure val EMPTY_ACKS: int -> AckResult = Map(0 -> AckNone, 1 -> AckNone, 2 -> AckNone, 3 -> AckNone)
  pure val EMPTY_DELIVERY: int -> DeliveryState = Map(
    0 -> NotDelivered, 1 -> NotDelivered, 2 -> NotDelivered, 3 -> NotDelivered,
    4 -> NotDelivered, 5 -> NotDelivered, 6 -> NotDelivered, 7 -> NotDelivered,
    8 -> NotDelivered, 9 -> NotDelivered, 10 -> NotDelivered, 11 -> NotDelivered,
    12 -> NotDelivered, 13 -> NotDelivered, 14 -> NotDelivered, 15 -> NotDelivered,
    16 -> NotDelivered, 17 -> NotDelivered, 18 -> NotDelivered, 19 -> NotDelivered,
    20 -> NotDelivered, 21 -> NotDelivered, 22 -> NotDelivered, 23 -> NotDelivered,
    24 -> NotDelivered
  )
  pure val EMPTY_TAGS: int -> int = Map(
    0 -> 0, 1 -> 0, 2 -> 0, 3 -> 0, 4 -> 0, 5 -> 0, 6 -> 0, 7 -> 0,
    8 -> 0, 9 -> 0, 10 -> 0, 11 -> 0, 12 -> 0, 13 -> 0, 14 -> 0, 15 -> 0,
    16 -> 0, 17 -> 0, 18 -> 0, 19 -> 0, 20 -> 0, 21 -> 0, 22 -> 0,
    23 -> 0, 24 -> 0
  )

  type RequestRecord = {
    occupied: bool,
    request_id: int,
    group_id: int,
    kind: RequestKind,
    request_state: RequestState,
    group_state: GroupState,
    members: Set[int],
    responded: Set[int],
    terminal: Set[int],
    defaulted: Set[int],
    actions: int -> ActionKind,
    acks: int -> AckResult,
    opened_seq: int,
    closed_seq: int,
    linearized_seq: int,
    deadline_at: int,
    grace_until: int,
    timebank_remaining: int,
  }

  type SeqEntry = {
    present: bool,
    seq: int,
    kind: MessageKind,
    payload: PayloadTag,
    bytes_tag: int,
    request_slot: int,
    request_id: int,
    group_id: int,
    member: int,
  }

  type SnapshotRequest = {
    captured: bool,
    request_id: int,
    group_id: int,
    members: Set[int],
    terminal: Set[int],
  }

  type ProtocolState = {
    session: SessionState,
    game: GameState,
    turn: TurnState,
    transport: TransportState,
    resume: ResumeState,
    game_id: int,
    kyoku: int,
    turn_seat: int,
    turn_index: int,
    clock: int,
    previous_clock: int,
    grace_ticks: int,
    timebank_ticks: int,
    kyoku_timebank_remaining: int,
    host_seq: int,
    previous_host_seq: int,
    applied_seq: int,
    wire_seq: int,
    wire_bytes_tag: int,
    wire_pending: bool,
    ledger: int -> SeqEntry,
    delivery: int -> DeliveryState,
    applied_tags: int -> int,
    requests: int -> RequestRecord,
    active_request_count: int,
    next_request_id: int,
    next_group_id: int,
    linearization_counter: int,
    linearized_groups: int -> int,
    snapshot_seq: int,
    snapshot_replaces_through: int,
    snapshot_active_count: int,
    snapshot_captured_slots: Set[int],
    snapshot_requests: int -> SnapshotRequest,
    last_error: ErrorCode,
    fatal: bool,
  }

  pure val EMPTY_REQUEST: RequestRecord = {
    occupied: false,
    request_id: 0,
    group_id: 0,
    kind: NoKind,
    request_state: EmptyRequest,
    group_state: NoGroup,
    members: Set(),
    responded: Set(),
    terminal: Set(),
    defaulted: Set(),
    actions: EMPTY_ACTIONS,
    acks: EMPTY_ACKS,
    opened_seq: 0,
    closed_seq: 0,
    linearized_seq: 0,
    deadline_at: 0,
    grace_until: 0,
    timebank_remaining: 0,
  }

  pure val EMPTY_REQUESTS: int -> RequestRecord = Map(
    0 -> EMPTY_REQUEST, 1 -> EMPTY_REQUEST, 2 -> EMPTY_REQUEST, 3 -> EMPTY_REQUEST
  )

  pure val EMPTY_ENTRY: SeqEntry = {
    present: false,
    seq: 0,
    kind: NoMessage,
    payload: EmptyPayload,
    bytes_tag: 0,
    request_slot: 0,
    request_id: 0,
    group_id: 0,
    member: 0,
  }

  pure val EMPTY_LEDGER: int -> SeqEntry = Map(
    0 -> EMPTY_ENTRY, 1 -> EMPTY_ENTRY, 2 -> EMPTY_ENTRY, 3 -> EMPTY_ENTRY,
    4 -> EMPTY_ENTRY, 5 -> EMPTY_ENTRY, 6 -> EMPTY_ENTRY, 7 -> EMPTY_ENTRY,
    8 -> EMPTY_ENTRY, 9 -> EMPTY_ENTRY, 10 -> EMPTY_ENTRY, 11 -> EMPTY_ENTRY,
    12 -> EMPTY_ENTRY, 13 -> EMPTY_ENTRY, 14 -> EMPTY_ENTRY, 15 -> EMPTY_ENTRY,
    16 -> EMPTY_ENTRY, 17 -> EMPTY_ENTRY, 18 -> EMPTY_ENTRY, 19 -> EMPTY_ENTRY,
    20 -> EMPTY_ENTRY, 21 -> EMPTY_ENTRY, 22 -> EMPTY_ENTRY, 23 -> EMPTY_ENTRY,
    24 -> EMPTY_ENTRY
  )

  pure val EMPTY_SNAPSHOT: SnapshotRequest = {
    captured: false,
    request_id: 0,
    group_id: 0,
    members: Set(),
    terminal: Set(),
  }

  pure val EMPTY_SNAPSHOTS: int -> SnapshotRequest = Map(
    0 -> EMPTY_SNAPSHOT, 1 -> EMPTY_SNAPSHOT, 2 -> EMPTY_SNAPSHOT, 3 -> EMPTY_SNAPSHOT
  )

  pure val EMPTY_LINEARIZATION: int -> int = Map(0 -> 0, 1 -> 0, 2 -> 0, 3 -> 0)

  var state: ProtocolState

  // ----------------------------------------------------------------------
  // Canonical-state invariants
  // ----------------------------------------------------------------------

  val type_ok: bool = and {
    state.session.in(Set(Idle, Negotiating, Active, Ended)),
    state.game.in(Set(NoGame, GameReady, KyokuRunning, KyokuEnded, GameEnded)),
    state.turn.in(Set(NoTurn, AwaitingAction, Resolving, TurnComplete)),
    state.transport.in(Set(Connected, Disconnected)),
    state.resume.in(Set(NoResume, ResumeReady, ReplayPending, SnapshotPending, ResumeComplete)),
    state.clock.in(0.to(MAX_CLOCK)),
    state.previous_clock.in(0.to(MAX_CLOCK)),
    state.clock >= state.previous_clock,
    state.grace_ticks == GRACE_TICKS,
    state.timebank_ticks == TIMEBANK_TICKS,
    state.kyoku_timebank_remaining.in(0.to(MAX_TIMEBANK)),
    state.host_seq.in(0.to(MAX_SEQ)),
    state.previous_host_seq.in(0.to(MAX_SEQ)),
    state.host_seq >= state.previous_host_seq,
    state.applied_seq.in(0.to(MAX_SEQ)),
    state.applied_seq <= state.host_seq,
    state.wire_seq.in(0.to(MAX_SEQ)),
    state.wire_bytes_tag.in(0.to(MAX_SEQ * 100)),
    state.ledger.keys().subseteq(Set(0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24)),
    state.delivery.keys().subseteq(Set(0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24)),
    state.applied_tags.keys().subseteq(Set(0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24)),
    state.requests.keys() == SLOTS,
    state.active_request_count.in(0.to(4)),
    state.next_request_id.in(0.to(MAX_REQUEST_ID)),
    state.next_group_id.in(0.to(MAX_GROUP_ID)),
    state.linearization_counter.in(0.to(MAX_SEQ)),
    state.linearized_groups.keys() == SLOTS,
    state.snapshot_requests.keys() == SLOTS,
    state.snapshot_captured_slots.subseteq(SLOTS),
    state.last_error.in(Set(NoError, BadRequest, TooLate, DuplicateRequest, SequenceConflict, Superseded, Unauthorized)),
    state.session == Ended implies state.fatal,
  }

  // A ledger entry is immutable by construction: only append actions write
  // `ledger[next_seq]`; every other transition carries the entire map with
  // `...state`.  This predicate checks the resulting canonical prefix.
  val seq_ledger_invariant: bool = and {
    state.host_seq == 0 or state.ledger.get(state.host_seq).present,
    state.host_seq == 0 or state.ledger.get(state.host_seq).seq == state.host_seq,
    state.ledger.keys().forall(seq => not(state.ledger.get(seq).present) or state.ledger.get(seq).seq == seq),
    state.ledger.keys().forall(seq => state.ledger.get(seq).present implies seq <= state.host_seq),
    state.wire_pending implies state.ledger.get(state.wire_seq).present,
    state.wire_pending implies state.wire_bytes_tag == state.ledger.get(state.wire_seq).bytes_tag,
  }

  val delivery_applied_invariant: bool = and {
    state.applied_seq == 0 or state.delivery.get(state.applied_seq) == Applied,
    state.applied_seq == 0 or state.applied_tags.get(state.applied_seq) == state.ledger.get(state.applied_seq).bytes_tag,
    state.applied_tags.keys().forall(seq => state.applied_tags.get(seq) == 0 or state.ledger.get(seq).present),
    state.delivery.keys().forall(seq => state.delivery.get(seq) == Applied implies state.ledger.get(seq).present),
    state.delivery.keys().forall(seq => state.delivery.get(seq) == DuplicateDelivered implies state.ledger.get(seq).present),
  }

  val request_map_invariant: bool = and {
    state.active_request_count <= 4,
    SLOTS.forall(slot => state.requests.get(slot).occupied implies state.requests.get(slot).request_id > 0),
    SLOTS.forall(slot => state.requests.get(slot).occupied implies state.requests.get(slot).group_id > 0),
    SLOTS.forall(slot => state.requests.get(slot).occupied implies state.requests.get(slot).members.subseteq(SEATS)),
    SLOTS.forall(slot => state.requests.get(slot).occupied implies state.requests.get(slot).members.size() > 0),
    SLOTS.forall(slot => state.requests.get(slot).responded.subseteq(state.requests.get(slot).members)),
    SLOTS.forall(slot => state.requests.get(slot).terminal.subseteq(state.requests.get(slot).responded)),
    SLOTS.forall(slot => state.requests.get(slot).defaulted.subseteq(state.requests.get(slot).terminal)),
    SLOTS.forall(slot => state.requests.get(slot).request_state == OpenRequest implies state.requests.get(slot).group_state == GroupOpen),
    SLOTS.forall(slot => state.requests.get(slot).request_state == ClosedRequest implies state.requests.get(slot).group_state == GroupClosed),
    SLOTS.forall(slot => state.requests.get(slot).request_state == ResolvedRequest implies state.requests.get(slot).group_state == GroupResolved),
    SLOTS.forall(slot => state.requests.get(slot).request_state == SupersededRequest implies state.requests.get(slot).group_state == GroupSuperseded),
    SLOTS.forall(slot => state.requests.get(slot).request_state == OpenRequest implies state.requests.get(slot).terminal != state.requests.get(slot).members),
    SLOTS.forall(slot => state.requests.get(slot).request_state == ClosedRequest implies state.requests.get(slot).terminal == state.requests.get(slot).members),
    SLOTS.forall(slot => state.requests.get(slot).deadline_at <= state.requests.get(slot).grace_until),
    SLOTS.forall(slot => state.requests.get(slot).timebank_remaining.in(0.to(MAX_TIMEBANK))),
  }

  val request_id_unique: bool = and {
    state.requests.get(0).request_id == 0 or state.requests.get(0).request_id != state.requests.get(1).request_id,
    state.requests.get(0).request_id == 0 or state.requests.get(0).request_id != state.requests.get(2).request_id,
    state.requests.get(0).request_id == 0 or state.requests.get(0).request_id != state.requests.get(3).request_id,
    state.requests.get(1).request_id == 0 or state.requests.get(1).request_id != state.requests.get(2).request_id,
    state.requests.get(1).request_id == 0 or state.requests.get(1).request_id != state.requests.get(3).request_id,
    state.requests.get(2).request_id == 0 or state.requests.get(2).request_id != state.requests.get(3).request_id,
  }

  val group_linearization_invariant: bool = and {
    state.linearized_groups.keys() == SLOTS,
    SLOTS.forall(slot => state.linearized_groups.get(slot) == 0 or state.linearized_groups.get(slot) <= state.host_seq),
    SLOTS.forall(slot => state.requests.get(slot).linearized_seq == 0 or state.requests.get(slot).linearized_seq == state.linearized_groups.get(slot)),
    SLOTS.forall(slot => state.requests.get(slot).request_state == ResolvedRequest implies state.requests.get(slot).linearized_seq > 0),
    SLOTS.forall(slot => state.requests.get(slot).group_state == GroupResolved implies state.requests.get(slot).terminal == state.requests.get(slot).members),
  }

  val clock_deadline_invariant: bool = and {
    state.clock >= state.previous_clock,
    SLOTS.forall(slot => not(state.requests.get(slot).occupied) or state.requests.get(slot).deadline_at >= 0),
    SLOTS.forall(slot => not(state.requests.get(slot).occupied) or state.requests.get(slot).grace_until == state.requests.get(slot).deadline_at + state.grace_ticks),
    SLOTS.forall(slot => not(state.requests.get(slot).occupied) or state.requests.get(slot).timebank_remaining <= state.timebank_ticks),
  }

  val resume_snapshot_invariant: bool = and {
    state.resume == SnapshotPending implies state.snapshot_seq > 0,
    state.snapshot_replaces_through <= state.snapshot_seq,
    state.snapshot_captured_slots.subseteq(SLOTS),
    state.snapshot_captured_slots.forall(slot => state.snapshot_requests.get(slot).captured),
    state.resume == ReplayPending implies state.transport == Connected,
    state.resume == SnapshotPending implies state.transport == Connected,
  }

  val ack_result_invariant: bool = and {
    SLOTS.forall(slot => state.requests.get(slot).acks.keys() == SEATS),
    SLOTS.forall(slot => state.requests.get(slot).actions.keys() == SEATS),
    SLOTS.forall(slot => state.requests.get(slot).defaulted.forall(seat => state.requests.get(slot).acks.get(seat) == AckDefaulted)),
    SLOTS.forall(slot => state.requests.get(slot).terminal.forall(seat => state.requests.get(slot).acks.get(seat) != AckNone)),
    SLOTS.forall(slot => state.requests.get(slot).responded.exclude(state.requests.get(slot).terminal).forall(seat => state.requests.get(slot).acks.get(seat) == AckNone)),
  }

  val protocol_invariant: bool = and {
    type_ok,
    seq_ledger_invariant,
    delivery_applied_invariant,
    request_map_invariant,
    request_id_unique,
    group_linearization_invariant,
    clock_deadline_invariant,
    resume_snapshot_invariant,
    ack_result_invariant,
  }

  // ----------------------------------------------------------------------
  // Initial state and negotiation/game lifecycle
  // ----------------------------------------------------------------------

  action init = state' = {
    session: Idle,
    game: NoGame,
    turn: NoTurn,
    transport: Connected,
    resume: NoResume,
    game_id: 0,
    kyoku: 0,
    turn_seat: 0,
    turn_index: 0,
    clock: 0,
    previous_clock: 0,
    grace_ticks: GRACE_TICKS,
    timebank_ticks: TIMEBANK_TICKS,
    kyoku_timebank_remaining: MAX_TIMEBANK,
    host_seq: 0,
    previous_host_seq: 0,
    applied_seq: 0,
    wire_seq: 0,
    wire_bytes_tag: 0,
    wire_pending: false,
    ledger: EMPTY_LEDGER,
    delivery: EMPTY_DELIVERY,
    applied_tags: EMPTY_TAGS,
    requests: EMPTY_REQUESTS,
    active_request_count: 0,
    next_request_id: 0,
    next_group_id: 0,
    linearization_counter: 0,
    linearized_groups: EMPTY_LINEARIZATION,
    snapshot_seq: 0,
    snapshot_replaces_through: 0,
    snapshot_active_count: 0,
    snapshot_captured_slots: Set(),
    snapshot_requests: EMPTY_SNAPSHOTS,
    last_error: NoError,
    fatal: false,
  }

  action negotiate = all {
    state.session == Idle,
    state' = { session: Active, game: GameReady, previous_host_seq: state.host_seq, ...state },
  }

  action start_kyoku = all {
    state.session == Active,
    state.game.in(Set(GameReady, KyokuEnded)),
    state' = {
      game: KyokuRunning,
      turn: AwaitingAction,
      kyoku: state.kyoku + 1,
      turn_index: 0,
      turn_seat: 0,
      kyoku_timebank_remaining: MAX_TIMEBANK,
      previous_host_seq: state.host_seq,
      ...state,
    },
  }

  action advance_turn = all {
    state.game == KyokuRunning,
    state.turn == TurnComplete,
    state' = {
      turn: AwaitingAction,
      turn_index: state.turn_index + 1,
      turn_seat: if (state.turn_seat == 3) 0 else state.turn_seat + 1,
      previous_host_seq: state.host_seq,
      ...state,
    },
  }

  // ----------------------------------------------------------------------
  // Append-only host ledger and delivery
  // ----------------------------------------------------------------------

  action emit_message(kind, payload, slot, request_id, group_id, member) = all {
    state.host_seq < MAX_SEQ,
    state.ledger.get(state.host_seq + 1).present == false,
    kind != NoMessage,
    state' = {
      host_seq: state.host_seq + 1,
      previous_host_seq: state.host_seq,
      ledger: state.ledger.set(state.host_seq + 1, {
        present: true,
        seq: state.host_seq + 1,
        kind: kind,
        payload: payload,
        bytes_tag: (state.host_seq + 1) * 100 + slot * 10 + member,
        request_slot: slot,
        request_id: request_id,
        group_id: group_id,
        member: member,
      }),
      ...state,
    },
  }

  action deliver_next = all {
    state.transport == Connected,
    not(state.wire_pending),
    state.applied_seq < state.host_seq,
    state.delivery.get(state.applied_seq + 1) == NotDelivered,
    state' = {
      wire_pending: true,
      wire_seq: state.applied_seq + 1,
      wire_bytes_tag: state.ledger.get(state.applied_seq + 1).bytes_tag,
      delivery: state.delivery.set(state.applied_seq + 1, Delivered),
      previous_host_seq: state.host_seq,
      ...state,
    },
  }

  action apply_wire = all {
    state.wire_pending,
    state.wire_seq == state.applied_seq + 1,
    state.wire_bytes_tag == state.ledger.get(state.wire_seq).bytes_tag,
    state' = {
      applied_seq: state.wire_seq,
      applied_tags: state.applied_tags.set(state.wire_seq, state.wire_bytes_tag),
      delivery: state.delivery.set(state.wire_seq, Applied),
      wire_pending: false,
      previous_host_seq: state.host_seq,
      ...state,
    },
  }

  action retransmit_exact(seq) = all {
    state.transport == Connected,
    not(state.wire_pending),
    seq.in(1.to(MAX_SEQ)),
    seq <= state.host_seq,
    state.ledger.get(seq).present,
    state.applied_tags.get(seq) == state.ledger.get(seq).bytes_tag,
    state' = {
      wire_pending: true,
      wire_seq: seq,
      wire_bytes_tag: state.ledger.get(seq).bytes_tag,
      delivery: state.delivery.set(seq, DuplicateDelivered),
      previous_host_seq: state.host_seq,
      ...state,
    },
  }

  action reject_retransmit_conflict(seq) = all {
    state.transport == Connected,
    not(state.wire_pending),
    seq.in(1.to(MAX_SEQ)),
    seq <= state.applied_seq,
    state.ledger.get(seq).present,
    state' = {
      session: Ended,
      game: GameEnded,
      transport: Disconnected,
      fatal: true,
      last_error: SequenceConflict,
      delivery: state.delivery.set(seq, RejectedDelivery),
      previous_host_seq: state.host_seq,
      ...state,
    },
  }

  // ----------------------------------------------------------------------
  // Four concurrent request/group slots
  // ----------------------------------------------------------------------

  action open_request(slot, kind, members) = all {
    state.session == Active,
    state.game == KyokuRunning,
    state.transport == Connected,
    slot.in(SLOTS),
    kind.in(Set(SingleKind, GroupKind)),
    members.subseteq(SEATS),
    members.size() > 0,
    not(state.requests.get(slot).occupied),
    state.active_request_count < 4,
    state.next_request_id < MAX_REQUEST_ID,
    state.next_group_id < MAX_GROUP_ID,
    state.host_seq < MAX_SEQ,
    state' = {
      requests: state.requests.set(slot, {
        occupied: true,
        request_id: state.next_request_id + 1,
        group_id: state.next_group_id + 1,
        kind: kind,
        request_state: OpenRequest,
        group_state: GroupOpen,
        members: members,
        responded: Set(),
        terminal: Set(),
        defaulted: Set(),
        actions: EMPTY_ACTIONS,
        acks: EMPTY_ACKS,
        opened_seq: state.host_seq + 1,
        closed_seq: 0,
        linearized_seq: 0,
        deadline_at: state.clock + DEADLINE_TICKS,
        grace_until: state.clock + DEADLINE_TICKS + state.grace_ticks,
        timebank_remaining: state.kyoku_timebank_remaining,
      }),
      active_request_count: state.active_request_count + 1,
      next_request_id: state.next_request_id + 1,
      next_group_id: state.next_group_id + 1,
      turn: AwaitingAction,
      ledger: state.ledger.set(state.host_seq + 1, {
        present: true,
        seq: state.host_seq + 1,
        kind: RequestMessage,
        payload: RequestPayload,
        bytes_tag: (state.host_seq + 1) * 100 + slot * 10,
        request_slot: slot,
        request_id: state.next_request_id + 1,
        group_id: state.next_group_id + 1,
        member: 0,
      }),
      host_seq: state.host_seq + 1,
      previous_host_seq: state.host_seq,
      last_error: NoError,
      ...state,
    },
  }

  action open_single_0 = open_request(0, SingleKind, SINGLE_SEAT)
  action open_single_1 = open_request(1, SingleKind, SINGLE_SEAT)
  action open_single_2 = open_request(2, SingleKind, SINGLE_SEAT)
  action open_single_3 = open_request(3, SingleKind, SINGLE_SEAT)
  action open_group_0 = open_request(0, GroupKind, GROUP_SEATS)
  action open_group_1 = open_request(1, GroupKind, GROUP_SEATS)
  action open_group_2 = open_request(2, GroupKind, GROUP_SEATS)
  action open_group_3 = open_request(3, GroupKind, GROUP_SEATS)

  action submit_action(slot, member, action_kind) = all {
    state.session == Active,
    slot.in(SLOTS),
    state.requests.get(slot).occupied,
    state.requests.get(slot).request_state == OpenRequest,
    state.requests.get(slot).members.contains(member),
    not(state.requests.get(slot).responded.contains(member)),
    action_kind.in(Set(PassAction, ReachAction, CallAction)),
    state.clock <= state.requests.get(slot).grace_until,
    state.clock <= state.requests.get(slot).deadline_at or state.requests.get(slot).timebank_remaining > 0,
    state' = {
      requests: state.requests.set(slot, {
        responded: state.requests.get(slot).responded.union(Set(member)),
        actions: state.requests.get(slot).actions.set(member, action_kind),
        ...state.requests.get(slot),
      }),
      previous_clock: state.clock,
      last_error: NoError,
      ...state,
    },
  }

  action submit_slot0_member0_pass = submit_action(0, 0, PassAction)
  action submit_slot1_member0_pass = submit_action(1, 0, PassAction)
  action submit_slot2_member0_pass = submit_action(2, 0, PassAction)
  action submit_slot3_member0_pass = submit_action(3, 0, PassAction)
  action submit_slot0_member1_call = submit_action(0, 1, CallAction)
  action submit_slot1_member1_call = submit_action(1, 1, CallAction)
  action submit_slot2_member1_call = submit_action(2, 1, CallAction)
  action submit_slot3_member1_call = submit_action(3, 1, CallAction)
  action submit_slot0_member2_call = submit_action(0, 2, CallAction)
  action submit_slot1_member2_call = submit_action(1, 2, CallAction)
  action submit_slot2_member2_call = submit_action(2, 2, CallAction)
  action submit_slot3_member2_call = submit_action(3, 2, CallAction)

  action acknowledge(slot, member) = all {
    state.session == Active,
    slot.in(SLOTS),
    state.requests.get(slot).occupied,
    state.requests.get(slot).request_state == OpenRequest,
    state.requests.get(slot).members.contains(member),
    state.requests.get(slot).responded.contains(member),
    not(state.requests.get(slot).terminal.contains(member)),
    state.host_seq < MAX_SEQ,
    state' = {
      requests: state.requests.set(slot, {
        terminal: state.requests.get(slot).terminal.union(Set(member)),
        acks: state.requests.get(slot).acks.set(member,
          if (state.requests.get(slot).actions.get(member) == PassAction) AckPassed else AckAccepted
        ),
        request_state: if (state.requests.get(slot).terminal.union(Set(member)) == state.requests.get(slot).members) ClosedRequest else OpenRequest,
        group_state: if (state.requests.get(slot).terminal.union(Set(member)) == state.requests.get(slot).members) GroupClosed else GroupOpen,
        ...state.requests.get(slot),
      }),
      ledger: state.ledger.set(state.host_seq + 1, {
        present: true,
        seq: state.host_seq + 1,
        kind: ActionAckMessage,
        payload: if (state.requests.get(slot).actions.get(member) == PassAction) PassedPayload else AcceptedPayload,
        bytes_tag: (state.host_seq + 1) * 100 + slot * 10 + member,
        request_slot: slot,
        request_id: state.requests.get(slot).request_id,
        group_id: state.requests.get(slot).group_id,
        member: member,
      }),
      host_seq: state.host_seq + 1,
      previous_host_seq: state.host_seq,
      last_error: NoError,
      ...state,
    },
  }

  action ack_slot0_member0 = acknowledge(0, 0)
  action ack_slot1_member0 = acknowledge(1, 0)
  action ack_slot2_member0 = acknowledge(2, 0)
  action ack_slot3_member0 = acknowledge(3, 0)
  action ack_slot0_member1 = acknowledge(0, 1)
  action ack_slot1_member1 = acknowledge(1, 1)
  action ack_slot2_member1 = acknowledge(2, 1)
  action ack_slot3_member1 = acknowledge(3, 1)
  action ack_slot0_member2 = acknowledge(0, 2)
  action ack_slot1_member2 = acknowledge(1, 2)
  action ack_slot2_member2 = acknowledge(2, 2)
  action ack_slot3_member2 = acknowledge(3, 2)

  action tick = all {
    state.clock < MAX_CLOCK,
    state' = {
      clock: state.clock + 1,
      previous_clock: state.clock,
      requests: state.requests
        .set(0, { timebank_remaining: if (state.requests.get(0).occupied and state.clock >= state.requests.get(0).grace_until and state.requests.get(0).timebank_remaining > 0) state.requests.get(0).timebank_remaining - 1 else state.requests.get(0).timebank_remaining, ...state.requests.get(0) })
        .set(1, { timebank_remaining: if (state.requests.get(1).occupied and state.clock >= state.requests.get(1).grace_until and state.requests.get(1).timebank_remaining > 0) state.requests.get(1).timebank_remaining - 1 else state.requests.get(1).timebank_remaining, ...state.requests.get(1) })
        .set(2, { timebank_remaining: if (state.requests.get(2).occupied and state.clock >= state.requests.get(2).grace_until and state.requests.get(2).timebank_remaining > 0) state.requests.get(2).timebank_remaining - 1 else state.requests.get(2).timebank_remaining, ...state.requests.get(2) })
        .set(3, { timebank_remaining: if (state.requests.get(3).occupied and state.clock >= state.requests.get(3).grace_until and state.requests.get(3).timebank_remaining > 0) state.requests.get(3).timebank_remaining - 1 else state.requests.get(3).timebank_remaining, ...state.requests.get(3) }),
      previous_host_seq: state.host_seq,
      ...state,
    },
  }

  action default_member(slot, member) = all {
    state.session == Active,
    slot.in(SLOTS),
    state.requests.get(slot).occupied,
    state.requests.get(slot).request_state == OpenRequest,
    state.requests.get(slot).members.contains(member),
    not(state.requests.get(slot).responded.contains(member)),
    state.clock >= state.requests.get(slot).grace_until,
    state.requests.get(slot).timebank_remaining == 0,
    state.host_seq < MAX_SEQ,
    state' = {
      requests: state.requests.set(slot, {
        responded: state.requests.get(slot).responded.union(Set(member)),
        terminal: state.requests.get(slot).terminal.union(Set(member)),
        defaulted: state.requests.get(slot).defaulted.union(Set(member)),
        actions: state.requests.get(slot).actions.set(member, DefaultAction),
        acks: state.requests.get(slot).acks.set(member, AckDefaulted),
        request_state: if (state.requests.get(slot).terminal.union(Set(member)) == state.requests.get(slot).members) ClosedRequest else OpenRequest,
        group_state: if (state.requests.get(slot).terminal.union(Set(member)) == state.requests.get(slot).members) GroupClosed else GroupOpen,
        ...state.requests.get(slot),
      }),
      ledger: state.ledger.set(state.host_seq + 1, {
        present: true,
        seq: state.host_seq + 1,
        kind: DefaultMessage,
        payload: DefaultedPayload,
        bytes_tag: (state.host_seq + 1) * 100 + slot * 10 + member,
        request_slot: slot,
        request_id: state.requests.get(slot).request_id,
        group_id: state.requests.get(slot).group_id,
        member: member,
      }),
      host_seq: state.host_seq + 1,
      previous_host_seq: state.host_seq,
      last_error: NoError,
      ...state,
    },
  }

  action default_slot0_member0 = default_member(0, 0)
  action default_slot1_member0 = default_member(1, 0)
  action default_slot2_member0 = default_member(2, 0)
  action default_slot3_member0 = default_member(3, 0)
  action default_slot0_member1 = default_member(0, 1)
  action default_slot1_member1 = default_member(1, 1)
  action default_slot2_member1 = default_member(2, 1)
  action default_slot3_member1 = default_member(3, 1)
  action default_slot0_member2 = default_member(0, 2)
  action default_slot1_member2 = default_member(1, 2)
  action default_slot2_member2 = default_member(2, 2)
  action default_slot3_member2 = default_member(3, 2)

  action late_action_after_default(slot, member) = all {
    state.session == Active,
    slot.in(SLOTS),
    state.requests.get(slot).occupied,
    state.requests.get(slot).defaulted.contains(member),
    state.host_seq < MAX_SEQ,
    state' = {
      requests: state.requests.set(slot, {
        acks: state.requests.get(slot).acks.set(member, AckStale),
        ...state.requests.get(slot),
      }),
      ledger: state.ledger.set(state.host_seq + 1, {
        present: true,
        seq: state.host_seq + 1,
        kind: ErrorMessage,
        payload: StalePayload,
        bytes_tag: (state.host_seq + 1) * 100 + slot * 10 + member,
        request_slot: slot,
        request_id: state.requests.get(slot).request_id,
        group_id: state.requests.get(slot).group_id,
        member: member,
      }),
      host_seq: state.host_seq + 1,
      previous_host_seq: state.host_seq,
      last_error: TooLate,
      ...state,
    },
  }

  action reject_invalid_action(slot, member) = all {
    state.session == Active,
    slot.in(SLOTS),
    state.requests.get(slot).occupied,
    state.host_seq < MAX_SEQ,
    state' = {
      ledger: state.ledger.set(state.host_seq + 1, {
        present: true,
        seq: state.host_seq + 1,
        kind: ErrorMessage,
        payload: RejectedPayload,
        bytes_tag: (state.host_seq + 1) * 100 + slot * 10 + member,
        request_slot: slot,
        request_id: state.requests.get(slot).request_id,
        group_id: state.requests.get(slot).group_id,
        member: member,
      }),
      host_seq: state.host_seq + 1,
      previous_host_seq: state.host_seq,
      last_error: BadRequest,
      ...state,
    },
  }

  action resolve_request(slot) = all {
    state.session == Active,
    slot.in(SLOTS),
    state.requests.get(slot).occupied,
    state.requests.get(slot).request_state == ClosedRequest,
    state.requests.get(slot).terminal == state.requests.get(slot).members,
    state.host_seq < MAX_SEQ,
    state' = {
      requests: state.requests.set(slot, {
        request_state: ResolvedRequest,
        group_state: GroupResolved,
        closed_seq: state.host_seq + 1,
        linearized_seq: state.host_seq + 1,
        ...state.requests.get(slot),
      }),
      active_request_count: state.active_request_count - 1,
      linearization_counter: state.linearization_counter + 1,
      linearized_groups: state.linearized_groups.set(slot, state.host_seq + 1),
      turn: TurnComplete,
      ledger: state.ledger.set(state.host_seq + 1, {
        present: true,
        seq: state.host_seq + 1,
        kind: ResolveMessage,
        payload: ResolvePayload,
        bytes_tag: (state.host_seq + 1) * 100 + slot * 10,
        request_slot: slot,
        request_id: state.requests.get(slot).request_id,
        group_id: state.requests.get(slot).group_id,
        member: 0,
      }),
      host_seq: state.host_seq + 1,
      previous_host_seq: state.host_seq,
      last_error: NoError,
      ...state,
    },
  }

  action resolve_slot0 = resolve_request(0)
  action resolve_slot1 = resolve_request(1)
  action resolve_slot2 = resolve_request(2)
  action resolve_slot3 = resolve_request(3)

  // Once a higher-priority group has linearized, lower-priority open groups
  // are closed atomically with Superseded ACK results.
  action supersede_request(slot) = all {
    state.session == Active,
    slot.in(SLOTS),
    state.requests.get(slot).occupied,
    state.requests.get(slot).request_state == OpenRequest,
    state.linearization_counter > 0,
    state.host_seq < MAX_SEQ,
    state' = {
      requests: state.requests.set(slot, {
        request_state: SupersededRequest,
        group_state: GroupSuperseded,
        terminal: state.requests.get(slot).members,
        responded: state.requests.get(slot).members,
        closed_seq: state.host_seq + 1,
        acks: state.requests.get(slot).acks
          .set(0, AckSuperseded)
          .set(1, AckSuperseded)
          .set(2, AckSuperseded)
          .set(3, AckSuperseded),
        ...state.requests.get(slot),
      }),
      active_request_count: state.active_request_count - 1,
      ledger: state.ledger.set(state.host_seq + 1, {
        present: true,
        seq: state.host_seq + 1,
        kind: ActionAckMessage,
        payload: SupersededPayload,
        bytes_tag: (state.host_seq + 1) * 100 + slot * 10,
        request_slot: slot,
        request_id: state.requests.get(slot).request_id,
        group_id: state.requests.get(slot).group_id,
        member: 0,
      }),
      host_seq: state.host_seq + 1,
      previous_host_seq: state.host_seq,
      last_error: Superseded,
      ...state,
    },
  }

  action begin_resume = all {
    state.session == Active,
    state.transport == Disconnected,
    state.resume.in(Set(NoResume, ResumeReady)),
    state' = { transport: Connected, resume: ResumeReady, previous_host_seq: state.host_seq, ...state },
  }

  action begin_replay = all {
    state.session == Active,
    state.transport == Connected,
    state.resume == ResumeReady,
    state.applied_seq < state.host_seq,
    state' = { resume: ReplayPending, previous_host_seq: state.host_seq, ...state },
  }

  action replay_one = all {
    state.resume == ReplayPending,
    state.applied_seq < state.host_seq,
    state.ledger.get(state.applied_seq + 1).present,
    state' = {
      applied_seq: state.applied_seq + 1,
      applied_tags: state.applied_tags.set(state.applied_seq + 1, state.ledger.get(state.applied_seq + 1).bytes_tag),
      delivery: state.delivery.set(state.applied_seq + 1, Applied),
      previous_host_seq: state.host_seq,
      ...state,
    },
  }

  action finish_replay = all {
    state.resume == ReplayPending,
    state.applied_seq == state.host_seq,
    state' = { resume: ResumeComplete, previous_host_seq: state.host_seq, ...state },
  }

  action begin_snapshot = all {
    state.session == Active,
    state.transport == Connected,
    state.resume == ResumeReady,
    state.host_seq < MAX_SEQ,
    state' = {
      resume: SnapshotPending,
      snapshot_seq: state.host_seq + 1,
      snapshot_replaces_through: state.applied_seq,
      snapshot_captured_slots:
        (if (state.requests.get(0).occupied) Set(0) else Set())
          .union(if (state.requests.get(1).occupied) Set(1) else Set())
          .union(if (state.requests.get(2).occupied) Set(2) else Set())
          .union(if (state.requests.get(3).occupied) Set(3) else Set()),
      snapshot_active_count: state.active_request_count,
      snapshot_requests: state.snapshot_requests
        .set(0, { captured: state.requests.get(0).occupied, request_id: state.requests.get(0).request_id, group_id: state.requests.get(0).group_id, members: state.requests.get(0).members, terminal: state.requests.get(0).terminal })
        .set(1, { captured: state.requests.get(1).occupied, request_id: state.requests.get(1).request_id, group_id: state.requests.get(1).group_id, members: state.requests.get(1).members, terminal: state.requests.get(1).terminal })
        .set(2, { captured: state.requests.get(2).occupied, request_id: state.requests.get(2).request_id, group_id: state.requests.get(2).group_id, members: state.requests.get(2).members, terminal: state.requests.get(2).terminal })
        .set(3, { captured: state.requests.get(3).occupied, request_id: state.requests.get(3).request_id, group_id: state.requests.get(3).group_id, members: state.requests.get(3).members, terminal: state.requests.get(3).terminal }),
      ledger: state.ledger.set(state.host_seq + 1, {
        present: true,
        seq: state.host_seq + 1,
        kind: SnapshotMessage,
        payload: SnapshotPayload,
        bytes_tag: (state.host_seq + 1) * 100,
        request_slot: 0,
        request_id: 0,
        group_id: 0,
        member: 0,
      }),
      host_seq: state.host_seq + 1,
      previous_host_seq: state.host_seq,
      ...state,
    },
  }

  action finish_snapshot = all {
    state.resume == SnapshotPending,
    state' = { resume: ResumeComplete, previous_host_seq: state.host_seq, ...state },
  }

  action disconnect = all {
    state.session == Active,
    state.transport == Connected,
    state' = { transport: Disconnected, resume: ResumeReady, previous_host_seq: state.host_seq, ...state },
  }

  action end_kyoku = all {
    state.session == Active,
    state.active_request_count == 0,
    state.game == KyokuRunning,
    state' = { game: KyokuEnded, turn: NoTurn, previous_host_seq: state.host_seq, ...state },
  }

  action end_game = all {
    state.session == Active,
    state.active_request_count == 0,
    state.game.in(Set(GameReady, KyokuEnded)),
    state' = { session: Ended, game: GameEnded, turn: NoTurn, fatal: true, previous_host_seq: state.host_seq, ...state },
  }

  action stutter = state' = { previous_clock: state.clock, previous_host_seq: state.host_seq, ...state }

  action step = any {
    negotiate,
    start_kyoku,
    advance_turn,
    open_single_0,
    open_single_1,
    open_single_2,
    open_single_3,
    open_group_0,
    open_group_1,
    open_group_2,
    open_group_3,
    submit_slot0_member0_pass,
    submit_slot1_member0_pass,
    submit_slot2_member0_pass,
    submit_slot3_member0_pass,
    submit_slot0_member1_call,
    submit_slot1_member1_call,
    submit_slot2_member1_call,
    submit_slot3_member1_call,
    submit_slot0_member2_call,
    submit_slot1_member2_call,
    submit_slot2_member2_call,
    submit_slot3_member2_call,
    ack_slot0_member0,
    ack_slot1_member0,
    ack_slot2_member0,
    ack_slot3_member0,
    ack_slot0_member1,
    ack_slot1_member1,
    ack_slot2_member1,
    ack_slot3_member1,
    ack_slot0_member2,
    ack_slot1_member2,
    ack_slot2_member2,
    ack_slot3_member2,
    default_slot0_member0,
    default_slot1_member0,
    default_slot2_member0,
    default_slot3_member0,
    default_slot0_member1,
    default_slot1_member1,
    default_slot2_member1,
    default_slot3_member1,
    default_slot0_member2,
    default_slot1_member2,
    default_slot2_member2,
    default_slot3_member2,
    late_action_after_default(0, 0),
    late_action_after_default(1, 0),
    late_action_after_default(2, 0),
    late_action_after_default(3, 0),
    late_action_after_default(0, 1),
    late_action_after_default(1, 1),
    late_action_after_default(2, 1),
    late_action_after_default(3, 1),
    late_action_after_default(0, 2),
    late_action_after_default(1, 2),
    late_action_after_default(2, 2),
    late_action_after_default(3, 2),
    reject_invalid_action(0, 0),
    reject_invalid_action(1, 0),
    reject_invalid_action(2, 0),
    reject_invalid_action(3, 0),
    resolve_slot0,
    resolve_slot1,
    resolve_slot2,
    resolve_slot3,
    supersede_request(0),
    supersede_request(1),
    supersede_request(2),
    supersede_request(3),
    tick,
    deliver_next,
    apply_wire,
    retransmit_exact(1),
    retransmit_exact(2),
    retransmit_exact(3),
    retransmit_exact(4),
    reject_retransmit_conflict(1),
    reject_retransmit_conflict(2),
    reject_retransmit_conflict(3),
    disconnect,
    begin_resume,
    begin_replay,
    replay_one,
    finish_replay,
    begin_snapshot,
    finish_snapshot,
    end_kyoku,
    end_game,
    stutter,
  }

  // ----------------------------------------------------------------------
  // Refinement mapping and executable obligations
  // ----------------------------------------------------------------------

  val refinement_session: bool = and {
    state.session == Idle implies state.game == NoGame,
    state.session == Active implies state.game.in(Set(GameReady, KyokuRunning, KyokuEnded)),
    state.game == KyokuRunning implies state.session == Active,
  }

  val refinement_requests: bool = and {
    state.active_request_count.in(0.to(4)),
    SLOTS.forall(slot => state.requests.get(slot).occupied implies state.requests.get(slot).opened_seq > 0),
    SLOTS.forall(slot => state.requests.get(slot).request_state == ResolvedRequest implies state.requests.get(slot).closed_seq > state.requests.get(slot).opened_seq),
  }

  val refinement_wire: bool = and {
    state.applied_seq <= state.host_seq,
    state.wire_pending implies state.wire_seq <= state.host_seq,
    state.wire_pending implies state.wire_bytes_tag == state.ledger.get(state.wire_seq).bytes_tag,
  }

  val refinement_resume: bool = and {
    state.resume == SnapshotPending implies state.snapshot_captured_slots.size() == state.snapshot_active_count,
    state.resume == SnapshotPending implies state.snapshot_seq == state.host_seq,
  }

  val refinement_mapping: bool = and {
    protocol_invariant,
    refinement_session,
    refinement_requests,
    refinement_wire,
    refinement_resume,
  }

  // Safety properties used by TLC; liveness is conditional on a fair clock,
  // scheduler, and eventual transport stability in the concrete system.
  temporal ledger_prefix_never_changes: bool = always(seq_ledger_invariant)
  temporal request_resolves_under_fairness: bool = weakFair(resolve_request(0), state).implies(
    always(state.requests.get(0).request_state == ClosedRequest implies eventually(state.requests.get(0).request_state == ResolvedRequest))
  )
  temporal replay_completes_under_fairness: bool = weakFair(replay_one, state).implies(
    always(state.resume == ReplayPending implies eventually(state.resume == ResumeComplete))
  )
  temporal timeout_progress_under_fairness: bool = weakFair(tick, state).implies(
    always(state.clock < MAX_CLOCK implies eventually(state.clock >= state.previous_clock))
  )

  val witness_four_requests: bool = state.active_request_count == 4
  val witness_group_linearized: bool = state.linearization_counter > 0
  val witness_grace: bool = state.clock > 0 and state.requests.get(0).occupied and state.clock >= state.requests.get(0).deadline_at
  val witness_timebank: bool = state.requests.get(0).occupied and state.requests.get(0).timebank_remaining < MAX_TIMEBANK
  val witness_stale_ack: bool = state.last_error == TooLate
  val witness_superseded: bool = state.last_error == Superseded
  val witness_snapshot: bool = state.resume == SnapshotPending
  val witness_replay: bool = state.resume == ReplayPending
}
