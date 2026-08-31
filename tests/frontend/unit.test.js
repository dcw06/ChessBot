import assert from "node:assert/strict";
import test from "node:test";
import { api, CancelledRequest } from "../../static/js/api.js";
import {
  classifyResult,
  classifyMoveQuality,
  evaluationPercent,
  formatClock,
} from "../../static/js/chess-utils.js";

test("formats clocks without showing negative time", () => {
  assert.equal(formatClock(180), "3:00");
  assert.equal(formatClock(9.9), "0:09");
  assert.equal(formatClock(-4), "0:00");
});

test("move quality only penalizes evaluation lost by the mover", () => {
  assert.equal(classifyMoveQuality(0, -300, true), "blunder");
  assert.equal(classifyMoveQuality(0, 300, true), "");
  assert.equal(classifyMoveQuality(0, 150, false), "mistake");
  assert.equal(classifyMoveQuality(0, -150, false), "");
});

test("classifies distinct game results", () => {
  assert.equal(classifyResult("You win by checkmate!").kind, "checkmate");
  assert.equal(classifyResult("Draw — stalemate.").kind, "draw");
  assert.equal(classifyResult("You flagged").kind, "timeout");
  assert.equal(classifyResult("You resigned").kind, "resignation");
});

test("evaluation percentage remains bounded", () => {
  assert.equal(evaluationPercent(0), 50);
  assert.equal(evaluationPercent(100000), 97);
  assert.equal(evaluationPercent(-100000), 3);
  assert.equal(evaluationPercent(1, true), 100);
});

test("evaluation percentage follows the board orientation", () => {
  assert.equal(
    evaluationPercent(300, false, "black"),
    100 - evaluationPercent(300),
  );
  assert.equal(evaluationPercent(-1, true, "black"), 100);
  assert.equal(evaluationPercent(1, true, "black"), 0);
});

test("superseded requests are classified as cancellations", async () => {
  const originalFetch = globalThis.fetch;
  let requestCount = 0;
  globalThis.fetch = (_path, options) => {
    requestCount += 1;
    if (requestCount === 2)
      return Promise.resolve({
        ok: true,
        status: 200,
        json: async () => ({ ok: true }),
      });
    return new Promise((_resolve, reject) => {
      options.signal.addEventListener("abort", () =>
        reject(options.signal.reason),
      );
    });
  };

  try {
    const superseded = api("/state", { key: "state-test" });
    const replacement = api("/state", { key: "state-test" });
    await assert.rejects(superseded, CancelledRequest);
    assert.deepEqual(await replacement, { ok: true });
  } finally {
    globalThis.fetch = originalFetch;
  }
});
