export function classifyResult(result = "") {
  if (/checkmate/i.test(result))
    return { kind: "checkmate", icon: "♚", title: "Checkmate" };
  if (/flag|time/i.test(result))
    return { kind: "timeout", icon: "⏱", title: "Time expired" };
  if (/draw|stalemate|repetition|material/i.test(result))
    return { kind: "draw", icon: "½", title: "Draw" };
  if (/resign/i.test(result))
    return { kind: "resignation", icon: "⚑", title: "Resignation" };
  if (/abort/i.test(result))
    return { kind: "aborted", icon: "×", title: "Game aborted" };
  return { kind: "finished", icon: "♟", title: "Game over" };
}

export function formatClock(seconds) {
  const safe = Math.max(0, Number(seconds) || 0);
  return `${Math.floor(safe / 60)}:${Math.floor(safe % 60)
    .toString()
    .padStart(2, "0")}`;
}

export function evaluationPercent(cp, isMate = false) {
  if (isMate) return cp > 0 ? 100 : 0;
  return Math.max(3, Math.min(97, 50 + 50 * Math.tanh(cp / 300)));
}
