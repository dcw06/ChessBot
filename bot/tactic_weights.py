"""
Per-tactic awareness weights for yuandan (~2350 chess.com rapid).
All values are base rates for rapid; engine scales down for blitz/bullet.

Sources: game-history analysis, ChatGPT suggestions, Claude review.
Where we disagreed with the ChatGPT suggestions the reviewed value is used:
  wrong_color_bishop  0.87  (ChatGPT said 0.84)
  greek_gift          0.80  (ChatGPT said 0.76)
  quiet_move          0.70  (ChatGPT said 0.66)
  philidor            0.77  (ChatGPT said 0.74)
"""

WEIGHTS: dict[str, float] = {

    # ── Mating patterns ───────────────────────────────────────────────────
    "mate_in_1":              0.99,
    "back_rank":              0.95,
    "smothered_mate":         0.90,
    "lawn_mower":             0.90,
    "blind_swine":            0.85,
    "alekhine_gun":           0.83,
    "opera_mate":             0.82,
    "damiano_mate":           0.82,
    "legal_mate":             0.80,
    "epaulette_mate":         0.80,
    "anastasia_mate":         0.80,
    "hook_mate":              0.79,
    "dovetail_mate":          0.78,
    "cage_mate":              0.78,
    "boden_mate":             0.78,
    "windmill":               0.76,
    "pillsbury_mate":         0.72,
    "blackburne_mate":        0.70,
    "lolli_morphy_reti_mate": 0.70,

    # ── Core tactical motifs ──────────────────────────────────────────────
    "fork":                   0.92,   # weighted avg: knight 0.96, pawn 0.93, other 0.90
    "absolute_pin":           0.94,   # updated from 0.97
    "skewer":                 0.92,
    "discovery":              0.90,
    "double_check":           0.93,
    "removing_defender":      0.88,
    "overloading":            0.85,
    "deflection":             0.85,
    "decoy":                  0.83,
    "zwischenzug":            0.82,
    "battery":                0.86,   # updated from 0.90
    "quiet_move":             0.70,   # updated from 0.73
    "undermining":            0.85,
    "unpin":                  0.82,
    "counting":               0.88,
    "clearance":              0.80,
    "interference":           0.77,
    "x_ray":                  0.82,
    "desperado":              0.80,
    "mating_net":             0.80,
    "greek_gift":             0.80,   # updated from 0.83
    "fried_liver":            0.72,   # updated from 0.80
    "rook_lift":              0.83,
    "positional_exchange_sac":0.63,   # updated from 0.71
    "line_opening":           0.79,
    "line_closing":           0.72,
    "piece_trap":             0.87,   # raised from 0.84
    "counter_sacrifice":      0.78,
    "perpetual_pursuit":      0.79,
    "tempo_robbery":          0.80,
    "pawn_tension":           0.83,
    "ignoring_threat":        0.76,
    "combination_finish":     0.82,
    "piece_activity_over_material": 0.77,

    # ── Pawn tactics ──────────────────────────────────────────────────────
    "passed_pawn":            0.93,   # updated from 0.97
    "pawn_promotion":         0.95,   # updated from 0.97
    "en_passant":             0.86,   # updated from 0.91
    "pawn_fork":              0.91,
    "pawn_break":             0.86,
    "breakthrough":           0.83,
    "protected_passer":       0.87,
    "outside_passer":         0.82,
    "underpromotion":         0.64,   # updated from 0.71
    "blockade":               0.81,
    "minority_attack":        0.72,   # updated from 0.80
    "pawn_majority":          0.84,
    "backward_pawn":          0.88,
    "pawn_chain_attack":      0.83,
    "hanging_pawns":          0.82,
    "iqp_middlegame":         0.80,
    "iqp_endgame_target":     0.83,
    "pawn_islands":           0.80,

    # ── King safety ───────────────────────────────────────────────────────
    "mating_attack":          0.88,
    "king_hunt":              0.80,
    "perpetual_check":        0.93,   # raised from 0.91
    "stalemate_trick":        0.86,   # raised from 0.83
    "fortress":               0.80,   # raised from 0.76

    # ── Endgame technique ─────────────────────────────────────────────────
    "rook_behind_passer":     0.88,
    "wrong_color_bishop":     0.87,   # updated from 0.91 (reviewer: 0.84 too low)
    "direct_opposition":      0.90,
    "outflanking":            0.85,
    "distant_opposition":     0.77,
    "key_squares":            0.84,
    "rule_of_square":         0.87,
    "shouldering":            0.81,
    "seventh_rank":           0.88,
    "file_cutoff":            0.86,
    "rank_cutoff":            0.81,
    "lucena":                 0.72,   # updated from 0.79
    "philidor":               0.77,   # updated from 0.81 (reviewer: 0.74 too low)
    "philidor_third_rank":    0.67,
    "checking_distance":      0.72,
    "levenfish":              0.59,
    "opposite_bishops_attack":0.84,
    "opposite_bishops_draw":  0.81,
    "king_centralization":    0.84,
    "king_march":             0.82,
    "zugzwang":               0.83,
    "triangulation":          0.70,   # updated from 0.80
    "trebuchet":              0.70,
    "reserve_tempo":          0.71,
    "corresponding_squares":  0.52,   # updated from 0.62
    "reti_trick":             0.74,
    "saavedra":               0.63,
    "rook_bishop_vs_rook":    0.66,
    "queen_vs_rook":          0.67,
    "queen_vs_pawn":          0.78,
    "two_knights_pawn":       0.64,
    "knight_parity":          0.65,
    "vancura":                0.50,   # updated from 0.61
    "material_imbalance":     0.74,
    "knight_wrong_bishop_fortress": 0.63,
    "simplification":         0.87,   # raised from 0.84
    "connected_passers_vs_rook": 0.78,
    "mined_squares":          0.65,

    # ── Positional / strategic ────────────────────────────────────────────
    "open_file":              0.92,
    "knight_outpost":         0.90,
    "good_bad_bishop":        0.88,
    "weak_squares":           0.87,
    "space_advantage":        0.85,
    "piece_activity":         0.87,
    "prophylaxis":            0.81,
    "prophylaxis_endgame":    0.75,
    "overprotection":         0.76,
    "cramping":               0.81,
    "color_complex":          0.82,
    "initiative":             0.84,
    "central_pieces":         0.83,
    "overextension":          0.80,
    "zugzwang_middlegame":    0.68,
    "bishop_pair":            0.85,
    "restrict_bishop_pair":   0.79,
    "piece_coordination":     0.85,
    "transition":             0.82,
    "weak_color_complex":     0.82,
    "pawn_structure_sac":     0.70,

    # ── Rare / compositional ──────────────────────────────────────────────
    "bristol_clearance":      0.60,
    "novotny":                0.35,   # updated from 0.48
    "switchback":             0.63,
    "excelsior":              0.61,
}

# Rapid is the baseline. Blitz and bullet reduce noticing probability
# due to time pressure — but pattern recognition degrades less than deep
# calculation, so the scaling is gentler than the profile's temperature jump.
_TIME_SCALE: dict[str, float] = {
    "rapid":  1.00,
    "blitz":  0.96,
    "bullet": 0.88,
}


def get_weight(tactic: str, time_control: str = "blitz") -> float:
    """Return the probability that yuandan notices and plays this tactic."""
    base  = WEIGHTS.get(tactic, 0.80)
    scale = _TIME_SCALE.get(time_control, 1.0)
    return min(1.0, base * scale)
