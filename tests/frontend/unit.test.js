import assert from "node:assert/strict";
import test from "node:test";
import {
  classifyResult,
  evaluationPercent,
  formatClock,
} from "../../static/js/chess-utils.js";

test("formats clocks without showing negative time", () => {
  assert.equal(formatClock(180), "3:00");
  assert.equal(formatClock(9.9), "0:09");
  assert.equal(formatClock(-4), "0:00");
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
