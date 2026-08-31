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

export function evaluationPercent(cp, isMate = false, orientation = "white") {
  const whitePercent = isMate
    ? cp > 0
      ? 100
      : 0
    : Math.max(3, Math.min(97, 50 + 50 * Math.tanh(cp / 300)));
  return orientation === "black" ? 100 - whitePercent : whitePercent;
}

export function classifyMoveQuality(previousCp, currentCp, moverWasWhite) {
  if (!Number.isFinite(previousCp) || !Number.isFinite(currentCp)) return "";
  const loss = Math.max(
    0,
    moverWasWhite ? previousCp - currentCp : currentCp - previousCp,
  );
  if (loss >= 250) return "blunder";
  if (loss >= 120) return "mistake";
  if (loss >= 60) return "inaccuracy";
  return "";
}
