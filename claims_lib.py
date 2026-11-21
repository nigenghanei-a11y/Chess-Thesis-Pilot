"""Step 4 library: claim segmentation, taxonomy (adapted from Hebbar et al.'s ACT-Eval) and programmatic pre-checks.

Deterministic: no randomness and no model calls. Same text + same position -> same output.
Every decision records the rule that made it, so it can be audited and changed. Bump LIB_VERSION when you change a rule.
Pre-check verdicts are PROPOSALS for the manual review, never final labels.
"""
import re
import chess

LIB_VERSION = "claims-v1"
CATEGORIES = ("Board comprehension", "Legality", "Quality evaluation", "Tactical reasoning", "Other")
SQ = r"[a-h][1-8]"
PIECE = r"(king|queen|rook|bishop|knight|pawn)"
PIECE_TYPES = {"king": chess.KING, "queen": chess.QUEEN, "rook": chess.ROOK, "bishop": chess.BISHOP,
               "knight": chess.KNIGHT, "pawn": chess.PAWN}
COLORS = {"white": chess.WHITE, "black": chess.BLACK}

# ---------------------------------------------------------------- segmentation
_ABBREV = re.compile(r"\b(e\.g|i\.e|vs|etc|cf)\.", re.I)
_LIST_MARK = re.compile(r"(?m)^\s*(?:\d+[.)]|[-•*])\s+")
_MD = re.compile(r"[*_`#]+")
_FRAMING = re.compile(r"^(?:a|the)\s+(?:human|player|human player)\s+(?:might|may|could|would|can)?\s*(?:have\s+)?"
                      r"(?:choose|chosen|play|played|opt for|prefer)s?\s+(?:this move|\S+)\s+(?:because|since|as)\s+(?:that\s+)?", re.I)
_LEAD = re.compile(r"^(?:overall|additionally|furthermore|moreover|also|however|finally|lastly|in addition|thus|therefore|"
                   r"and|but|then),?\s+", re.I)
_SPLIT_PUNCT = re.compile(r"\s*;\s*|\s+[—–]\s+|\s+-\s+")
_SPLIT_CONN = re.compile(r",\s+(?=(?:but|while|whereas|which|so that)\b|[a-z]+ing\b)", re.I)   # ', which ...', ', capturing ...'
_BY_ING = re.compile(r"^(by\s+[a-z]+ing\b[^,]*?),\s+(.+)$", re.I)                              # 'By moving the bishop to g6, Black ...'
_SPLIT_REASON = re.compile(r"\s+(?=(?:because|since|so that|whereas|indicates?|suggests?|shows?|means|implies)\b)", re.I)
_AND = re.compile(r"(,?\s+and\s+)", re.I)
_VERBS = re.compile(r"\b(?:is|are|was|were|has|have|had|can|could|will|would|might|may|does|do|opens?|attacks?|threatens?|puts?|"
                    r"controls?|creates?|prepares?|allows?|forces?|gains?|wins?|loses?|captures?|takes?|exchanges?|trades?|"
                    r"develops?|defends?|protects?|pins?|forks?|improves?|weakens?|strengthens?|activates?|centralizes?|"
                    r"removes?|eliminates?|gives?|blocks?|supports?|pressures?|targets?|moves?|leaves?|keeps?|maintains?|"
                    r"increases?|reduces?|exposes?|restricts?|secures?|aims?|seeks?|intends?|ensures?|enables?|gets?|makes?|"
                    r"forms?|sets?|leads?|provides?|offers?|brings?|places?|plays?|threatening|attacking)\b", re.I)
_HEDGE = re.compile(r"\b(might|may|could|potentially|possibly|perhaps|likely|aims? to|seeks? to|intends?|hopes?|plans? to)\b", re.I)


def split_sentences(text):
    t = _MD.sub("", _LIST_MARK.sub("", text.replace("’", "'")))
    t = re.sub(r"\s+", " ", t).strip()
    t = _ABBREV.sub(lambda m: m.group(0).replace(".", "§"), t)
    return [p.replace("§", ".").strip() for p in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])", t) if p.strip()]


def _split_and(clause):
    """split at 'and' only when both sides contain a verb (so 'knight and bishop' stays together)."""
    parts = _AND.split(clause)
    out, cur = [], parts[0]
    for i in range(1, len(parts), 2):
        right = parts[i + 1]
        if _VERBS.search(cur) and _VERBS.search(right):
            out.append(cur); cur = right
        else:
            cur += parts[i] + right
    out.append(cur)
    return out


def split_clauses(sentence):
    s = _FRAMING.sub("", sentence)
    s = _LEAD.sub("", s)
    m = _BY_ING.match(s)
    pieces = [m.group(1), m.group(2)] if m else [s]
    for rx in (_SPLIT_PUNCT, _SPLIT_CONN, _SPLIT_REASON):
        pieces = [q for p in pieces for q in rx.split(p)]
    pieces = [q for p in pieces for q in _split_and(p)]
    out = []
    for p in pieces:
        p = _LEAD.sub("", p.strip(" ,.;:"))
        if len(p.split()) >= 2:
            out.append(p)
    return out


def extract_claims(text):
    """-> list of dict(sentence_idx, clause_idx, sentence, claim_text, hedged)"""
    rows = []
    for si, sent in enumerate(split_sentences(text)):
        for ci, clause in enumerate(split_clauses(sent)):
            rows.append(dict(sentence_idx=si, clause_idx=ci, sentence=sent, claim_text=clause,
                             hedged=bool(_HEDGE.search(clause))))
    return rows


# ---------------------------------------------------------------- taxonomy
_ON = rf"(?:the\s+)?(?:(white|black)(?:'s)?\s+)?{PIECE}s?\s+(?:currently\s+|now\s+)?(?:(?:is|are|sits|stands|remains)\s+)?(?:on|at)\s+({SQ})\b"
PIECE_ON_SQ = re.compile(_ON, re.I)
PRECEDENCE = ("Legality", "Tactical reasoning", "Quality evaluation", "Board comprehension")
RULES = [
    ("Legality", "L1", r"\b(illegal|legal|not allowed|allowed to|cannot|can't|can castle|can move|possible move)\b"),
    ("Tactical reasoning", "T1", r"\b(forks?|forking|pins?|pinned|pinning|skewer\w*|discovered|x-ray)\b"),
    ("Tactical reasoning", "T2", r"\b(attack\w*|threat\w*|defen[cd]\w*|protect\w*|hanging|undefended|en prise)\b"),
    ("Tactical reasoning", "T3", r"\b(captur\w*|takes|taking|recaptur\w*|exchang\w*|trad\w*|sacrific\w*|material|check\w*|mate|forc\w*"
                                  r"|wins? (?:a|an|the)|loses? (?:a|an|the))\b"),
    ("Quality evaluation", "Q1", r"\b(good|bad|strong|weak|best|worst|blunder\w*|mistake\w*|inaccura\w*|excellent|poor|dubious|winning|"
                                  r"losing|decisive|advantage\w*|disadvantage\w*|better|worse|improv\w*|weaken\w*|strengthen\w*|solid|"
                                  r"risky|sound|favou?rable|unfavou?rable|equal|balanced|evaluat\w*|centipawns?|cp|stockfish|engine)\b"),
    ("Board comprehension", "B1", _ON),
    ("Board comprehension", "B2", r"\b(doubled|isolated|passed|backward) pawns?\b|\bpawn (structure|chain)\b|"
                                   r"\b(sits|stands|remains|located|positioned|placed|occupi\w*) (on|at)\b"),
    ("Board comprehension", "B3", rf"\b{SQ}\b"),
]
_RULES_C = [(c, i, re.compile(p, re.I)) for c, i, p in RULES]
_STRATEGIC = re.compile(r"\b(control\w*|cent(?:er|re)\w*|activ\w*|develop\w*|initiative|space|coordinat\w*|king safety|pressure|"
                        r"mobility|tempo|plan\w*|strateg\w*|weakness\w*|outpost\w*|open\w*|file|diagonal|flexib\w*)\b", re.I)
_ENGINE_WORDS = re.compile(r"\b(stockfish|engine|centipawns?|cp|principal variation|evaluation)\b", re.I)


def classify(claim):
    hits = [(c, i) for c, i, rx in _RULES_C if rx.search(claim)]
    cats = {c for c, _ in hits}
    cat = next((c for c in PRECEDENCE if c in cats), "Other")
    rule = next((i for c, i in hits if c == cat), "default")
    strategic = bool(_STRATEGIC.search(claim))
    review = len(cats) > 1 or (cat == "Other" and not strategic)
    return dict(category=cat, category_rule=rule, also_matches="|".join(sorted(cats - {cat})),
                is_strategic=strategic, review_category=review, mentions_engine=bool(_ENGINE_WORDS.search(claim)))


# ---------------------------------------------------------------- programmatic pre-checks (proposals)
_NEG = re.compile(r"\b(not|no|never|without|neither|nor|fails?|cannot|can't|doesn't|isn't|won't)\b|n't", re.I)
_MODAL = re.compile(r"(threat\w*|could|can|will|would|might|may|allow\w*|prevent\w*|to|aim\w*|seek\w*|able|before|if)\s+(?:\w+\s+){0,2}$", re.I)
_CAPTURE = re.compile(rf"\b(?:captur\w+|takes?|taking)\s+(?:the\s+|a\s+|an\s+)?(?:(white|black)(?:'s)?\s+)?{PIECE}\b(?:\s+(?:on|at)\s+({SQ}))?", re.I)
_MOVES = re.compile(rf"\b(?:mov(?:es?|ing)|plac(?:es?|ing)|putting|puts?|develop(?:s|ing)?|retreat(?:s|ing)?|bring(?:s|ing)?|advanc(?:es?|ing))\s+(?:the\s+)?(?:(white|black)(?:'s)?\s+)?{PIECE}\s+(?:\w+\s+)?(?:to|on)\s+({SQ})\b", re.I)
_CHECK = re.compile(r"\b(?:gives?|delivers?|puts?|places?)\b[^.]{0,25}\bcheck\b|\bwith check\b|\bchecks the\b", re.I)
_INTERPRET = re.compile(r"\b(indicat\w*|mean\w*|suggest\w*|show\w*|impl\w*|reflect\w*|therefore|because)\b", re.I)
_CPNUM = re.compile(r"([-+−]?\d+(?:\.\d+)?)\s*(?:cp|centipawns?)", re.I)
_POS = re.compile(r"\b(good|strong|best|excellent|solid|sound|improv\w*|strengthen\w*|advantage|better|winning|favou?rable|effective|accurate)\b", re.I)
_NEGW = re.compile(r"\b(bad|weak|poor|blunder\w*|mistake\w*|inaccura\w*|dubious|worse|losing|weaken\w*|risky|disadvantage|unfavou?rable|questionable|loses)\b", re.I)


def _res(verdict, rule, detail=""):
    return dict(auto_verdict=verdict, auto_rule=rule, auto_detail=detail)


def precheck(claim, board, move, label=None, provided_evals=()):
    """board = position BEFORE the move, move = the played move. verdict in correct / incorrect / unknown (+ rule, detail)."""
    c = claim.replace("’", "'")
    if _NEG.search(c):
        return _res("unknown", "negation_unsupported")
    after = board.copy(); after.push(move)
    mover = board.turn

    m = _CAPTURE.search(c)
    if m and not _MODAL.search(c[:m.start()][-30:]):
        color, piece, sq = m.group(1), m.group(2).lower(), m.group(3)
        if not board.is_capture(move):
            return _res("incorrect", "capture_identity", "the played move is not a capture")
        victim = chess.Piece(chess.PAWN, not mover) if board.is_en_passant(move) else board.piece_at(move.to_square)
        if sq and chess.parse_square(sq.lower()) != move.to_square:
            return _res("incorrect", "capture_identity", f"capture happens on {chess.square_name(move.to_square)}, not {sq}")
        if PIECE_TYPES[piece] != victim.piece_type:
            return _res("incorrect", "capture_identity", f"captured piece is a {chess.piece_name(victim.piece_type)}")
        if color and COLORS[color.lower()] != victim.color:
            return _res("incorrect", "capture_identity", "captured piece has the other colour")
        return _res("correct", "capture_identity", f"captures a {piece}")

    m = _MOVES.search(c)
    if m:
        piece, sq = m.group(2).lower(), chess.parse_square(m.group(3).lower())
        moved = board.piece_at(move.from_square)
        ok = moved.piece_type == PIECE_TYPES[piece] and move.to_square == sq
        return _res("correct" if ok else "incorrect", "moved_piece",
                    f"played: {chess.piece_name(moved.piece_type)} to {chess.square_name(move.to_square)}")

    m = PIECE_ON_SQ.search(c)
    if m:
        color, piece, sq = m.group(1), m.group(2).lower(), chess.parse_square(m.group(3).lower())
        for name, b in (("before", board), ("after", after)):
            p = b.piece_at(sq)
            if p and p.piece_type == PIECE_TYPES[piece] and (not color or p.color == COLORS[color.lower()]):
                return _res("correct", "piece_on_square", f"matches the board {name} the move")
        p = board.piece_at(sq)
        return _res("incorrect", "piece_on_square", f"{chess.square_name(sq)} holds {p.symbol() if p else 'nothing'} before the move")

    if _CHECK.search(c):
        return _res("correct" if board.gives_check(move) else "incorrect", "gives_check")

    nums = [float(x.replace("−", "-")) for x in _CPNUM.findall(c)]
    if nums and provided_evals:
        if _INTERPRET.search(c):
            return _res("unknown", "echo_plus_interpretation")
        allowed = {float(v) for v in provided_evals}
        if all(n in allowed for n in nums):
            return _res("correct", "echo_numbers", "numbers equal the evaluations given in the prompt")
        if all(n in allowed or -n in allowed for n in nums):
            return _res("unknown", "echo_numbers", "sign differs from the White-perspective input")
        return _res("incorrect", "echo_numbers", "number not in the prompt")

    if label in ("good", "bad") and classify(c)["category"] == "Quality evaluation" and re.search(r"\b(move|it|this|that)\b", c, re.I):
        pos, neg = bool(_POS.search(c)), bool(_NEGW.search(c))
        named = {x.lower() for x in re.findall(r"\b(white|black)(?:'s)?\b", c, re.I)}
        if named == {"black" if mover == chess.WHITE else "white"}:      # claim is about the opponent: polarity flips
            pos, neg = neg, pos
        if pos != neg:
            ok = (pos and label == "good") or (neg and label == "bad")
            return _res("correct" if ok else "incorrect", "polarity_vs_label", f"claim {'positive' if pos else 'negative'}, Stockfish label {label}")
    return _res("unknown", "no_check")
