const WINDOW_EVENTS = ["mousemove", "mouseup", "touchmove", "touchend"];

function windowHandlers() {
  const events = window.jQuery?._data?.(window, "events") || {};
  return new Map(
    WINDOW_EVENTS.map((type) => [
      type,
      new Set((events[type] || []).map((entry) => entry.handler)),
    ]),
  );
}

export function createManagedBoard(element, options) {
  const before = windowHandlers();
  const board = window.Chessboard(element, options);
  const after = window.jQuery?._data?.(window, "events") || {};
  const added = WINDOW_EVENTS.flatMap((type) =>
    (after[type] || [])
      .map((entry) => entry.handler)
      .filter((handler) => !before.get(type)?.has(handler))
      .map((handler) => ({ type, handler })),
  );
  const destroy = board.destroy.bind(board);
  board.destroy = () => {
    added.forEach(({ type, handler }) =>
      window.jQuery(window).off(type, handler),
    );
    destroy();
  };
  return board;
}
