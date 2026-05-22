import chess
from typing import Optional, Tuple

PIECE_VALUE = {
    chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3,
    chess.ROOK: 5, chess.QUEEN: 9, chess.KING: 0,
}

# Type alias: every public finder returns (move, tactic_key) or None.
# tactic_key must match a key in bot/tactic_weights.py.
TacticResult = Optional[Tuple[chess.Move, str]]


def find_strategic_move(board: chess.Board) -> TacticResult:
    """
    Positional patterns derived from yuandan's game data:
      1. Knight to central outpost (d5/e5/c5/f5 as White; d4/e4/c4/f4 as Black) — 33% of games
      2. Rook to open or semi-open file — 80% of games
    Only suggests moves where the piece is not immediately hanging after.
    """
    m = _knight_to_outpost(board)
    if m:
        return (m, "knight_outpost")
    m = _rook_to_open_file(board)
    if m:
        return (m, "open_file")
    return None


def _is_outpost(board: chess.Board, sq: int) -> bool:
    """True if sq is not attacked by any opponent pawn."""
    opponent = not board.turn
    return not any(
        board.piece_type_at(s) == chess.PAWN
        for s in board.attackers(opponent, sq)
    )


def _knight_to_outpost(board: chess.Board) -> Optional[chess.Move]:
    """Move a knight to a safe central square not attacked by any enemy pawn."""
    targets = (
        [chess.D5, chess.E5, chess.C5, chess.F5]
        if board.turn == chess.WHITE
        else [chess.D4, chess.E4, chess.C4, chess.F4]
    )
    for target in targets:
        if not _is_outpost(board, target):
            continue
        for move in board.legal_moves:
            if move.to_square != target:
                continue
            if board.piece_type_at(move.from_square) != chess.KNIGHT:
                continue
            board.push(move)
            safe = not board.is_attacked_by(board.turn, target)
            board.pop()
            if safe:
                return move
    return None


def _file_openness(board: chess.Board, file_idx: int) -> int:
    """0 = closed, 1 = semi-open (no friendly pawn), 2 = fully open (no pawns)."""
    has_friendly = has_enemy = False
    for rank in range(8):
        piece = board.piece_at(chess.square(file_idx, rank))
        if piece and piece.piece_type == chess.PAWN:
            if piece.color == board.turn:
                has_friendly = True
            else:
                has_enemy = True
    if has_friendly:
        return 0
    return 2 if not has_enemy else 1


def _rook_to_open_file(board: chess.Board) -> Optional[chess.Move]:
    """Slide a rook onto an open or semi-open file if it isn't already on one."""
    best_move = None
    best_score = 0
    for move in board.legal_moves:
        if board.piece_type_at(move.from_square) != chess.ROOK:
            continue
        from_file = chess.square_file(move.from_square)
        to_file = chess.square_file(move.to_square)
        if from_file == to_file:
            continue  # already on this file — not a lateral activation
        from_open = _file_openness(board, from_file)
        to_open = _file_openness(board, to_file)
        if to_open <= from_open or to_open == 0:
            continue  # no improvement
        board.push(move)
        safe = not board.is_attacked_by(board.turn, move.to_square)
        board.pop()
        if safe and to_open > best_score:
            best_score = to_open
            best_move = move
    return best_move


def find_rescue_move(board: chess.Board) -> Optional[chess.Move]:
    """
    If the side to move has a piece worth ≥ 3 that is currently hanging
    (undefended OR attacked by a cheaper piece), return a legal move that
    relocates the most valuable such piece to a safe square.
    """
    opponent = not board.turn
    best_sq, best_val = None, 0

    for sq, piece in board.piece_map().items():
        if piece.color != board.turn or piece.piece_type == chess.KING:
            continue
        pval = PIECE_VALUE[piece.piece_type]
        if pval < 3:
            continue  # ignore pawn hangs — often not worth the detour
        if not board.is_attacked_by(opponent, sq):
            continue
        if board.is_attacked_by(board.turn, sq):
            min_att = min(
                PIECE_VALUE[board.piece_at(a).piece_type]
                for a in board.attackers(opponent, sq)
                if board.piece_at(a)
            )
            if min_att >= pval:
                continue  # not a losing exchange
        if pval > best_val:
            best_val, best_sq = pval, sq

    if best_sq is None:
        return None

    for move in board.legal_moves:
        if move.from_square != best_sq:
            continue
        board.push(move)
        safe = not board.is_attacked_by(board.turn, move.to_square)
        board.pop()
        if safe:
            return move

    return None


def find_pawn_rescue(board: chess.Board) -> Optional[chess.Move]:
    """
    If the side to move has an undefended pawn that is being attacked,
    return a legal pawn push that moves it to a safe square.
    Only fires when the pawn is truly hanging (no defender at all).
    """
    opponent = not board.turn
    target_sq = None

    for sq, piece in board.piece_map().items():
        if piece.color != board.turn or piece.piece_type != chess.PAWN:
            continue
        if not board.is_attacked_by(opponent, sq):
            continue
        if board.is_attacked_by(board.turn, sq):
            continue  # defended — not truly hanging
        target_sq = sq
        break  # rescue the first (only) hanging pawn found

    if target_sq is None:
        return None

    for move in board.legal_moves:
        if move.from_square != target_sq:
            continue
        board.push(move)
        safe = not board.is_attacked_by(board.turn, move.to_square)
        board.pop()
        if safe:
            return move

    return None


def find_recapture(board: chess.Board) -> Optional[chess.Move]:
    """
    If the opponent's last move was a capture, return our cheapest safe recapture
    to that square.  "Safe" means our piece isn't immediately hanging after landing
    there (undefended while attacked, or attacked by a cheaper piece).
    Falls back to any recapture if none are safe (let the Stockfish gate decide).
    """
    if not board.move_stack:
        return None
    last_move = board.peek()
    target_sq = last_move.to_square
    target = board.piece_at(target_sq)
    if target is None or target.color == board.turn:
        return None
    recaptures = [m for m in board.legal_moves if m.to_square == target_sq]
    if not recaptures:
        return None

    def _is_safe_recapture(move: chess.Move) -> bool:
        our_val = PIECE_VALUE.get(board.piece_type_at(move.from_square), 0)
        board.push(move)
        opponent = board.turn
        us = not board.turn
        attacked = board.is_attacked_by(opponent, move.to_square)
        if not attacked:
            board.pop()
            return True
        if not board.is_attacked_by(us, move.to_square):
            board.pop()
            return False  # undefended and attacked
        min_att = min(
            PIECE_VALUE.get(board.piece_type_at(a), 0)
            for a in board.attackers(opponent, move.to_square)
            if board.piece_at(a)
        )
        board.pop()
        return min_att >= our_val  # losing exchange only if cheaper piece attacks us

    safe = [m for m in recaptures if _is_safe_recapture(m)]
    pool = safe if safe else recaptures
    # Among safe options, prefer the cheapest recapturing piece.
    return min(pool, key=lambda m: PIECE_VALUE.get(board.piece_type_at(m.from_square), 99))


def find_winning_capture(board: chess.Board) -> Optional[chess.Move]:
    """
    Return the most valuable clearly-winning capture:
      - Their piece is undefended after we take (free material), OR
      - Their piece value exceeds ours (winning exchange even if recaptured).
    Called unconditionally — no probability gate — so hanging queens are never ignored.
    """
    best_move = None
    best_gain = 0
    for move in board.legal_moves:
        target = board.piece_at(move.to_square)
        if target is None or target.color == board.turn:
            continue
        their_val = PIECE_VALUE[target.piece_type]
        if their_val == 0:
            continue
        our_val = PIECE_VALUE[board.piece_type_at(move.from_square)]
        board.push(move)
        defended = board.is_attacked_by(board.turn, move.to_square)
        board.pop()
        if not defended:
            gain = their_val          # completely free piece
        elif their_val > our_val:
            gain = their_val - our_val  # winning even if recaptured
        else:
            continue
        if gain > best_gain:
            best_gain = gain
            best_move = move
    return best_move


def find_tactical_move(board: chess.Board) -> TacticResult:
    """
    Check for high-priority tactics in order:
      1. Mate in 1
      2. Back-rank pressure (check against a trapped king with few escapes)
      3. Fork winning material (any piece, including piece can be taken if net positive)
      4. Pawn fork (pawn push attacking two pieces worth ≥ 3)
      5. Skewer (attack high-value piece; capture what hides behind it)
      6. Discovered attack (reveal a slider's attack on a high-value piece)
      7. Capture a pinned piece (only when exchange is favorable)
    Returns (move, tactic_key) so the engine can look up the per-tactic weight.
    """
    for finder, name in (
        (_mate_in_1,            "mate_in_1"),
        (_back_rank_threat,     "back_rank"),
        (_fork,                 "fork"),
        (_pawn_fork,            "pawn_fork"),
        (_skewer,               "skewer"),
        (_discovered_attack,    "discovery"),
        (_capture_pinned_piece, "absolute_pin"),
    ):
        m = finder(board)
        if m is not None:
            return (m, name)
    return None


def _mate_in_1(board: chess.Board) -> Optional[chess.Move]:
    for move in board.legal_moves:
        board.push(move)
        mate = board.is_checkmate()
        board.pop()
        if mate:
            return move
    return None


def _back_rank_threat(board: chess.Board) -> Optional[chess.Move]:
    """
    Slide a rook or queen to the opponent's back rank when their king is trapped there.
    _mate_in_1 already handles the checkmate case; this catches the serious-threat case
    where the king has very few escapes and we can give check safely.
    """
    opponent = not board.turn
    opp_king_sq = board.king(opponent)
    if opp_king_sq is None:
        return None

    back_rank = 7 if opponent == chess.BLACK else 0
    if chess.square_rank(opp_king_sq) != back_rank:
        return None  # king not on back rank, pattern doesn't apply

    for move in board.legal_moves:
        piece = board.piece_at(move.from_square)
        if piece is None or piece.piece_type not in (chess.ROOK, chess.QUEEN):
            continue
        if chess.square_rank(move.to_square) != back_rank:
            continue
        board.push(move)
        if board.is_check():
            # board.turn is now the opponent — check safety and whether they can only move the king
            # (no blocks or captures available means the back-rank mating net is real)
            is_safe = not board.is_attacked_by(board.turn, move.to_square)
            king_sq = board.king(board.turn)
            only_king_moves = all(m.from_square == king_sq for m in board.legal_moves)
            board.pop()
            if is_safe and only_king_moves:
                return move
        else:
            board.pop()

    return None


def _fork(board: chess.Board) -> Optional[chess.Move]:
    """
    Find a move that attacks 2+ opponent pieces worth ≥ 3 with a positive net gain.

    Guaranteed gain is NOT the sum of attacked values — the opponent saves their best
    piece.  Correct logic:
      • No check: opponent saves their most valuable piece → we capture the least.
        guaranteed = min(attacked_values)
      • King also forked (check): king must move, we pick the best non-king piece.
        guaranteed = max(attacked_non_king_values)
    Net gain then subtracts our piece cost when the forking piece can be taken.
    """
    opponent = not board.turn
    best_move = None
    best_gain = 0

    for move in board.legal_moves:
        if board.piece_at(move.from_square) is None:
            continue

        board.push(move)

        king_forked  = False
        attacked_vals = []
        for sq in board.attacks(move.to_square):
            target = board.piece_at(sq)
            if not (target and target.color == opponent):
                continue
            if target.piece_type == chess.KING:
                king_forked = True
            else:
                val = PIECE_VALUE.get(target.piece_type, 0)
                if val >= 3:
                    attacked_vals.append(val)

        guaranteed = 0
        valid = False
        if king_forked and attacked_vals:
            guaranteed = max(attacked_vals)   # king must move → take best other piece
            valid = True
        elif len(attacked_vals) >= 2:
            guaranteed = min(attacked_vals)   # opponent saves best → we take least
            valid = True

        net_gain = 0
        if valid and guaranteed >= 3:
            our_piece = board.piece_at(move.to_square)
            our_val   = PIECE_VALUE.get(our_piece.piece_type, 0) if our_piece else 0
            is_attacked = board.is_attacked_by(board.turn, move.to_square)
            if is_attacked:
                if not board.is_attacked_by(not board.turn, move.to_square):
                    net_gain = guaranteed - our_val            # undefended: we lose it
                else:
                    min_att = min(
                        PIECE_VALUE.get(board.piece_type_at(a), 99)
                        for a in board.attackers(board.turn, move.to_square)
                        if board.piece_at(a)
                    )
                    net_gain = guaranteed - our_val if min_att < our_val else guaranteed
            else:
                net_gain = guaranteed

        board.pop()
        if valid and net_gain > 0 and net_gain > best_gain:
            best_gain = net_gain
            best_move = move

    return best_move


def _skewer(board: chess.Board) -> Optional[chess.Move]:
    """
    Give check with a sliding piece where a valuable enemy piece hides behind the
    king on the same ray.  The king MUST move (it's in check), letting us then
    capture the piece that was sheltering behind it.
    """
    best_move = None
    best_gain = 0
    SLIDING = {chess.BISHOP, chess.ROOK, chess.QUEEN}

    for move in board.legal_moves:
        if board.piece_type_at(move.from_square) not in SLIDING:
            continue

        board.push(move)
        if not board.is_check():
            board.pop()
            continue

        # King is in check — find it (board.turn is now the side in check)
        king_sq = board.king(board.turn)
        if king_sq is None:
            board.pop()
            continue

        pt_at_dest = board.piece_type_at(move.to_square)
        ff, fr = chess.square_file(move.to_square), chess.square_rank(move.to_square)
        kf, kr = chess.square_file(king_sq), chess.square_rank(king_sq)
        df = (1 if kf > ff else -1 if kf < ff else 0)
        dr = (1 if kr > fr else -1 if kr < fr else 0)
        if pt_at_dest == chess.BISHOP and df * dr == 0:
            board.pop(); continue
        if pt_at_dest == chess.ROOK and df * dr != 0:
            board.pop(); continue

        # Walk behind the king along the same ray
        f, r = kf + df, kr + dr
        behind_val = 0
        while 0 <= f < 8 and 0 <= r < 8:
            sq = chess.square(f, r)
            p = board.piece_at(sq)
            if p is not None:
                if p.color == board.turn:  # opponent's piece — capturable
                    behind_val = PIECE_VALUE.get(p.piece_type, 0)
                break
            f += df; r += dr

        # Only valid when the king can't simply take our checking piece for free.
        # (If our piece is adjacent to the king and undefended, the king takes it.)
        piece_defended = board.is_attacked_by(not board.turn, move.to_square)
        king_adjacent  = chess.square_distance(king_sq, move.to_square) == 1
        if king_adjacent and not piece_defended:
            board.pop()
            continue

        board.pop()
        if behind_val > best_gain:
            best_gain = behind_val
            best_move = move

    return best_move


def _sq_between(a: int, c: int, b: int) -> bool:
    """True if square c lies strictly between a and b on the same rank/file/diagonal."""
    af, ar = chess.square_file(a), chess.square_rank(a)
    bf, br = chess.square_file(b), chess.square_rank(b)
    cf, cr = chess.square_file(c), chess.square_rank(c)
    if ar == br == cr:               # same rank
        return min(af, bf) < cf < max(af, bf)
    if af == bf == cf:               # same file
        return min(ar, br) < cr < max(ar, br)
    if abs(af - bf) == abs(ar - br) and abs(af - cf) == abs(ar - cr):  # same diagonal
        return min(af, bf) < cf < max(af, bf)
    return False


def _discovered_attack(board: chess.Board) -> Optional[chess.Move]:
    """
    Move a piece that was blocking one of our sliding pieces, revealing an attack
    on a high-value enemy piece.  Only counts as a discovery if the revealed slider
    now attacks a target worth ≥ 5 that it couldn't reach before (because the moving
    piece was on the line between them).
    """
    best_move = None
    best_gain = 0
    SLIDING = {chess.BISHOP, chess.ROOK, chess.QUEEN}

    for move in board.legal_moves:
        board.push(move)
        us = not board.turn
        them = board.turn

        discovered_gain = 0
        for slider_sq, slider in board.piece_map().items():
            if slider.color != us or slider.piece_type not in SLIDING:
                continue
            if slider_sq == move.to_square:
                continue  # skip the piece that actually moved
            for target_sq in board.attacks(slider_sq):
                target = board.piece_at(target_sq)
                if target is None or target.color != them:
                    continue
                val = 999 if target.piece_type == chess.KING else PIECE_VALUE.get(target.piece_type, 0)
                if val < 5:
                    continue
                # Was the moving piece blocking this line before the move?
                if _sq_between(slider_sq, move.from_square, target_sq):
                    discovered_gain = max(discovered_gain, val)

        board.pop()

        if discovered_gain > best_gain:
            best_gain = discovered_gain
            best_move = move

    return best_move


def _pawn_fork(board: chess.Board) -> Optional[chess.Move]:
    """
    Push a pawn to a square where it attacks two opponent pieces worth ≥ 3.
    The pawn itself must not be immediately capturable after the push.
    """
    opponent = not board.turn
    best_move = None
    best_gain = 0

    for move in board.legal_moves:
        if board.piece_type_at(move.from_square) != chess.PAWN:
            continue
        if move.to_square == move.from_square:
            continue

        board.push(move)
        us = not board.turn
        # After the pawn push, collect what it attacks
        forked_value = 0
        forked_count = 0
        for sq in board.attacks(move.to_square):
            target = board.piece_at(sq)
            if not (target and target.color == opponent):
                continue
            val = 999 if target.piece_type == chess.KING else PIECE_VALUE.get(target.piece_type, 0)
            if val >= 3:
                forked_value += val
                forked_count += 1
        is_hanging = board.is_attacked_by(board.turn, move.to_square)
        board.pop()

        if forked_count >= 2 and not is_hanging and forked_value > best_gain:
            best_gain = forked_value
            best_move = move

    return best_move


def _capture_pinned_piece(board: chess.Board) -> Optional[chess.Move]:
    """
    Capture an absolutely pinned piece only when the net exchange is favorable.
    Accounts for recapture: if our piece can be taken back, we only proceed if
    we still come out ahead on material.
    """
    opponent = not board.turn
    opp_king_sq = board.king(opponent)
    if opp_king_sq is None:
        return None

    best_move = None
    best_gain = 0

    for move in board.legal_moves:
        target = board.piece_at(move.to_square)
        if target is None or target.color != opponent:
            continue

        # Is the target absolutely pinned? Remove it and see if king is exposed.
        board_copy = board.copy()
        board_copy.remove_piece_at(move.to_square)
        if not board_copy.is_attacked_by(board.turn, opp_king_sq):
            continue

        our_val   = PIECE_VALUE[board.piece_type_at(move.from_square)]
        their_val = PIECE_VALUE[target.piece_type]

        # Would our capturing piece be recaptured?
        board.push(move)
        recaptured = board.is_attacked_by(board.turn, move.to_square)
        board.pop()

        net_gain = their_val - (our_val if recaptured else 0)
        if net_gain > best_gain:
            best_gain = net_gain
            best_move = move

    return best_move
