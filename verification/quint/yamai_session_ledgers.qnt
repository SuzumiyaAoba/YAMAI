// Draft.6 regression model: two independent session ledgers, one game.
// The finite state space includes a rejected attempt, atomic cancellation,
// a late stale response (including one queued before end_game), head-equal
// resume, initial spectate, and replay 1->1.
// Payload bytes, scoring, wall state and timers remain outside this model.
// Cancelled is an internal terminal state: draft.8 sends status=stale for
// cancellation. A later stale diagnostic leaves that terminal state unchanged.
// A and B project two peer ledgers; the third reaction member is abstracted out.
module yamai_session_ledgers {
  type Game = NotStarted | Ready | InKyoku | Ended
  type Terminal = Empty | Open | Cancelled
  type State = {
    stage: int, game: Game,
    headA: int, headB: int,
    ledgerA: Set[int], ledgerB: Set[int],
    previousA: Set[int], previousB: Set[int],
    requestA: Terminal, requestB: Terminal,
    resumed: bool, resumeGame: Game, resumeHead: int,
    stale: bool, pendingStale: bool,
    spectateSeq: int, spectateReplaces: int, spectatePending: int,
    replaySeq: int, originalSeq: int,
  }
  var s: State

  action init = s' = {
    stage: 0, game: NotStarted, headA: 0, headB: 0,
    ledgerA: Set(), ledgerB: Set(), previousA: Set(), previousB: Set(),
    requestA: Empty, requestB: Empty,
    resumed: false, resumeGame: NotStarted, resumeHead: 0,
    stale: false, pendingStale: false, spectateSeq: 0, spectateReplaces: 0, spectatePending: 0,
    replaySeq: 0, originalSeq: 0,
  }

  action startGame = all {
    s.stage == 0,
    s' = { stage: 1, game: Ready, headA: 1, headB: 1,
      ledgerA: Set(1), ledgerB: Set(1), ...s },
  }
  action firstKyoku = all {
    s.stage == 1, s.game == Ready,
    s' = { stage: 2, game: InKyoku, headA: 2, headB: 2,
      previousA: s.ledgerA, previousB: s.ledgerB,
      ledgerA: s.ledgerA.union(Set(2)), ledgerB: s.ledgerB.union(Set(2)), ...s },
  }
  action issueGroup = all {
    s.stage == 2,
    s' = { stage: 3, headA: 3, headB: 3, requestA: Open, requestB: Open,
      previousA: s.ledgerA, previousB: s.ledgerB,
      ledgerA: s.ledgerA.union(Set(3)), ledgerB: s.ledgerB.union(Set(3)), ...s },
  }
  action rejectAttempt = all {
    s.stage == 3,
    s' = { stage: 4, headA: 4, previousA: s.ledgerA,
      ledgerA: s.ledgerA.union(Set(4)), ...s },
  }
  action cancelGroup = all {
    s.stage == 4,
    s' = { stage: 5, headA: 5, headB: 4,
      requestA: Cancelled, requestB: Cancelled,
      previousA: s.ledgerA, previousB: s.ledgerB,
      ledgerA: s.ledgerA.union(Set(5)), ledgerB: s.ledgerB.union(Set(4)), ...s },
  }
  action penalty = all {
    s.stage == 5,
    s' = { stage: 6, game: Ready, headA: 6, headB: 5,
      previousA: s.ledgerA, previousB: s.ledgerB,
      ledgerA: s.ledgerA.union(Set(6)), ledgerB: s.ledgerB.union(Set(5)), ...s },
  }
  action resumeAtHead = all {
    s.stage == 6, not(s.resumed),
    s' = { resumed: true, resumeGame: s.game, resumeHead: s.headA, ...s },
  }
  action staleAttempt = all {
    s.stage == 6, s.resumed, not(s.stale), not(s.pendingStale),
    s' = { stale: true, headA: 7, previousA: s.ledgerA,
      ledgerA: s.ledgerA.union(Set(7)), ...s },
  }
  action queueLateAttempt = all {
    s.stage == 6, s.resumed, not(s.stale), not(s.pendingStale),
    s' = { pendingStale: true, ...s },
  }
  action flushLateAttempt = all {
    s.stage == 7, s.game == Ended, s.pendingStale, not(s.stale),
    s' = { pendingStale: false, stale: true, headA: s.headA + 1,
      previousA: s.ledgerA, ledgerA: s.ledgerA.union(Set(s.headA + 1)), ...s },
  }
  action initialSpectate = all {
    s.stage.in(2.to(6)), s.spectateSeq == 0,
    s' = { spectateSeq: 1, spectateReplaces: 0, spectatePending: 0, ...s },
  }
  action firstReplay = all {
    s.stage > 0, s.replaySeq == 0,
    s' = { replaySeq: 1, originalSeq: 1, ...s },
  }
  action endGame = all {
    s.stage == 6, s.stale or s.pendingStale,
    s' = { stage: 7, game: Ended, headA: s.headA + 1, headB: 6,
      previousA: s.ledgerA, previousB: s.ledgerB,
      ledgerA: s.ledgerA.union(Set(s.headA + 1)), ledgerB: s.ledgerB.union(Set(6)), ...s },
  }
  action step = any {
    startGame, firstKyoku, issueGroup, rejectAttempt, cancelGroup, penalty,
    resumeAtHead, staleAttempt, queueLateAttempt, flushLateAttempt, initialSpectate, firstReplay, endGame,
    s' = s,
  }

  val protocol_invariant = and {
    s.stage.in(0.to(7)),
    s.headA.in(0.to(8)), s.headB.in(0.to(6)),
    s.ledgerA == (if (s.headA == 0) Set() else 1.to(s.headA)),
    s.ledgerB == (if (s.headB == 0) Set() else 1.to(s.headB)),
    s.previousA.subseteq(s.ledgerA), s.previousB.subseteq(s.ledgerB),
    s.stage == 4 implies and { s.headA == 4, s.headB == 3, s.requestA == Open },
    s.stage >= 5 implies and { s.requestA == Cancelled, s.requestB == Cancelled },
    s.resumed implies and { s.resumeGame == Ready, s.resumeHead == 6 },
    s.spectateSeq == 0 or s.spectateSeq == s.spectateReplaces + 1,
    s.spectatePending == 0,
    s.replaySeq == s.originalSeq,
    s.stale implies s.requestA == Cancelled,
    s.pendingStale implies and { not(s.stale), s.requestA == Cancelled },
    s.game == Ended implies and { s.stage == 7, s.headB == 6,
      s.headA == (if (s.stale) 8 else 7) },
  }
  val witness_complete = and {
    s.stage == 7, s.resumed, s.stale, s.spectateSeq == 1, s.replaySeq == 1,
  }

  run queuedStaleAfterEndTest = init.then(startGame).then(firstKyoku)
    .then(issueGroup).then(rejectAttempt).then(cancelGroup).then(penalty)
    .then(resumeAtHead).then(queueLateAttempt).then(endGame)
    .then(all { assert(s.game == Ended), assert(s.pendingStale), assert(s.headA == 7), s' = s })
    .then(flushLateAttempt)
    .then(all { assert(s.game == Ended), assert(s.stale), assert(s.headA == 8), assert(protocol_invariant), s' = s })
}
