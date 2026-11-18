"""One definition of 'good' and 'bad' move, used by step 2 (Lichess evals) AND step 3 (Stockfish)."""
import math
import config as C


def win_pct(cp):
    """Lichess win-probability formula (0-100, mover's view)."""
    return 50 + 50 * (2 / (1 + math.exp(-0.00368208 * cp)) - 1)


def classify(before_cp, after_cp):
    """before_cp / after_cp: evaluation from the MOVER's point of view, before and after the played move.
    Returns ('good'|'bad'|None, cp_loss, wp_loss). Caller must already have excluded mate scores."""
    cp_loss = max(0, before_cp - after_cp)
    wp_loss = max(0.0, win_pct(before_cp) - win_pct(after_cp))
    if cp_loss <= C.GOOD_MAX_CP_LOSS:
        return "good", cp_loss, wp_loss
    bad = cp_loss >= C.BAD_MIN_CP_LOSS if C.METRIC == "cp" else wp_loss >= C.BAD_MIN_WP_LOSS
    return ("bad" if bad else None), cp_loss, wp_loss
