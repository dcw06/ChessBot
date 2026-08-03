import { expect, test } from "@playwright/test";

const initialState = {
  fen: "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
  bot_color: "black",
  bot_clock: 180,
  human_clock: 180,
  over: false,
  result: null,
  turn: "white",
  moves: [],
  last_move: null,
  in_check: false,
  version: 0,
  bot_busy: false,
  opening_name: "Starting position",
  decision_source: "—",
};

test("starts an accessible game and prevents duplicate starts", async ({
  page,
}) => {
  let starts = 0;
  await page.route("**/new_game", async (route) => {
    starts += 1;
    await new Promise((resolve) => setTimeout(resolve, 200));
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(initialState),
    });
  });
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Alan Dai" })).toBeVisible();
  await expect(page.locator("#model-chip")).toContainText("ready", {
    ignoreCase: true,
  });
  const start = page.getByRole("button", { name: "Start game" });
  await start.click();
  await expect(start).toBeDisabled();
  await start.dispatchEvent("click");
  await expect(page.locator("#game-panel")).toBeVisible();
  await expect(page.locator("#status")).toContainText("Your turn");
  await expect(page.getByRole("button", { name: "Resign" })).toBeEnabled();
  expect(starts).toBe(1);
  await page.locator("#game-return-btn").click();
  await page
    .locator("#confirm-dialog")
    .getByRole("button", { name: "Return" })
    .click();
  await expect(page.locator("#setup-panel")).toBeVisible();
});

test("missing Stockfish reports degraded service without disconnecting gameplay", async ({
  page,
}) => {
  await page.route("**/health/ready", (route) =>
    route.fulfill({
      status: 503,
      contentType: "application/json",
      body: JSON.stringify({
        status: "not_ready",
        model: "ready",
        stockfish: "unavailable",
      }),
    }),
  );
  await page.route("**/new_game", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(initialState),
    }),
  );
  await page.route("**/api/eval", (route) =>
    route.fulfill({
      status: 503,
      contentType: "application/json",
      body: JSON.stringify({ error: "Stockfish unavailable" }),
    }),
  );
  await page.goto("/");
  await expect(page.locator("#connection-label")).toHaveText("Degraded");
  await expect(page.locator("#connection-banner")).toBeHidden();
  await page.getByRole("button", { name: "Start game" }).click();
  await page.locator("#live-eval-toggle").click();
  await expect(page.locator("#live-eval-toggle")).not.toBeChecked();
  await expect(page.locator("#live-eval-wrap")).toBeHidden();
  await expect(page.locator("#toast-region")).toContainText(
    "Gameplay is still connected",
  );
  await expect(page.locator("#connection-label")).not.toHaveText("Offline");
});

test("analysis controls and empty states are keyboard reachable", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByRole("tab", { name: "Analyze" }).click();
  await expect(
    page.getByRole("heading", { name: "Saved games" }),
  ).toBeVisible();
  await expect(page.locator("#analysis-context-controls")).toBeHidden();
  await expect(page.locator("#model-chip")).toBeVisible();
  await expect(page.locator("#stockfish-chip")).toBeVisible();
  const toolbarHeight = await page
    .locator(".analysis-toolbar")
    .evaluate((element) => element.getBoundingClientRect().height);
  expect(toolbarHeight).toBeLessThan(180);
  await page.keyboard.press("Tab");
  await expect(page.locator(":focus")).toBeVisible();
  await expect(page.getByRole("tab", { name: "Analyze" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
});

test("analysis can start without saved games or Stockfish", async ({
  page,
}) => {
  await page.route("**/api/local-games", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ games: [] }),
    }),
  );
  await page.route("**/api/lines", (route) =>
    route.fulfill({
      status: 503,
      contentType: "application/json",
      body: JSON.stringify({ error: "Stockfish unavailable" }),
    }),
  );
  await page.goto("/");
  await page.getByRole("tab", { name: "Analyze" }).click();
  await page.getByRole("button", { name: "Analyze starting position" }).click();
  await expect(page.locator("#av-board [data-square]")).toHaveCount(64);
  await page.locator("#av-board .square-e2").click();
  await page.locator("#av-board .square-e4").click();
  await expect(
    page.locator('#av-board .square-e4 img[data-piece="wP"]'),
  ).toBeVisible();
  await expect(page.locator("#av-lines-panel")).toContainText(
    "You can still move pieces",
  );
  await expect(page.locator("#toast-region")).not.toContainText(
    "Stockfish unavailable",
  );
});

test("saved history uses only local bot games", async ({ page }) => {
  let chessComRequests = 0;
  await page.route("**/api/local-games", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        games: [
          {
            source: "local",
            result: "W",
            opponent: "Alan Dai",
            opening: "Italian Game",
            time_class: "blitz",
            date: "Jul 30",
            user_color: "white",
            pgn: "1. e4 e5 *",
          },
        ],
      }),
    }),
  );
  await page.route("**/api/games", (route) => {
    chessComRequests += 1;
    route.abort();
  });
  await page.route("**/api/lines", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ lines: [], eval_cp: 0, depth: 1 }),
    }),
  );
  await page.goto("/");
  await page.getByRole("tab", { name: "Analyze" }).click();
  await page.getByRole("button", { name: /vs Alan Dai/ }).click();
  await expect(page.locator("#analysis-context-controls")).toBeVisible();
  await expect(page.locator("#av-board [data-square]")).toHaveCount(64);
  await page
    .locator("#av-board .square-e7")
    .dragTo(page.locator("#av-board .square-e5"));
  await expect(
    page.locator('#av-board .square-e7 img[data-piece="bP"]'),
  ).toBeVisible();
  await page.locator("#av-board .square-e2").click();
  await expect(page.locator("#av-board .square-e2")).toHaveClass(/sq-selected/);
  await expect(page.locator("#av-board .square-e4 .legal-dot")).toBeVisible();
  await page.locator("#av-board .square-e4").click();
  await expect(
    page.locator('#av-board .square-e4 img[data-piece="wP"]'),
  ).toBeVisible();
  await page.locator('[data-analysis-nav="start"]').click();
  await page
    .locator("#av-board .square-e2")
    .dragTo(page.locator("#av-board .square-e4"));
  await expect(
    page.locator('#av-board .square-e4 img[data-piece="wP"]'),
  ).toBeVisible();
  const boardSize = await page
    .locator("#av-board")
    .evaluate((element) => element.getBoundingClientRect().width);
  expect(boardSize).toBeGreaterThan(300);
  await page.locator("#analysis-return-btn").click();
  await expect(page.locator("#play-section")).toBeVisible();
  expect(chessComRequests).toBe(0);
});

test("analysis supports underpromotion", async ({ page }) => {
  await page.route("**/api/local-games", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ games: [] }),
    }),
  );
  await page.route("**/api/lines", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ lines: [], eval_cp: 0, depth: 1 }),
    }),
  );
  await page.goto("/");
  await page.getByRole("tab", { name: "Analyze" }).click();
  await page.getByRole("button", { name: "Import FEN" }).click();
  await page.locator("#input-value").fill("7k/P7/8/8/8/8/8/7K w - - 0 1");
  await page.locator("#input-dialog button[value='submit']").click();
  await page.locator("#av-board .square-a7").click();
  await page.locator("#av-board .square-a8").click();
  await expect(page.locator("#promotion-dialog")).toBeVisible();
  await page.getByRole("button", { name: "Promote to knight" }).click();
  await expect(
    page.locator('#av-board .square-a8 img[data-piece="wN"]'),
  ).toBeVisible();
});

test("end game and home terminates the game and returns to setup", async ({
  page,
}) => {
  let ended = 0;
  await page.route("**/new_game", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(initialState),
    }),
  );
  await page.route("**/end_game", (route) => {
    ended += 1;
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ ended: true }),
    });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "Start game" }).click();
  await page.getByRole("button", { name: "End game & home" }).click();
  await page
    .locator("#confirm-dialog")
    .getByRole("button", { name: "End game", exact: true })
    .click();
  await expect(page.locator("#setup-panel")).toBeVisible();
  await expect(page.getByRole("tab", { name: "Play" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  expect(ended).toBe(1);
});

test("dragging a legal move keeps the piece on its destination", async ({
  page,
}) => {
  let submittedMove = "";
  await page.route("**/new_game", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(initialState),
    }),
  );
  await page.route("**/move", async (route) => {
    submittedMove = route.request().postDataJSON().uci;
    await new Promise((resolve) => setTimeout(resolve, 150));
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        ...initialState,
        fen: "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1",
        turn: "black",
        moves: ["e4"],
        version: 1,
      }),
    });
  });
  await page.route("**/bot_move", (route) => route.abort());
  await page.goto("/");
  await page.getByRole("button", { name: "Start game" }).click();
  await page
    .locator("#board .square-e2 img")
    .dragTo(page.locator("#board .square-e4"));
  await expect.poll(() => submittedMove).toBe("e2e4");
  await expect(page.locator("#board .square-e4 img")).toHaveAttribute(
    "data-piece",
    "wP",
  );
});

test("an in-flight stale state poll cannot roll back an optimistic move", async ({
  page,
}) => {
  let stateRequestStarted = false;
  await page.route("**/new_game", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(initialState),
    }),
  );
  await page.route("**/state", async (route) => {
    stateRequestStarted = true;
    await new Promise((resolve) => setTimeout(resolve, 1000));
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(initialState),
    });
  });
  await page.route("**/move", async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 300));
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        ...initialState,
        fen: "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1",
        turn: "black",
        moves: ["e4"],
        version: 1,
      }),
    });
  });
  await page.route("**/bot_move", (route) => route.abort());
  await page.goto("/");
  await page.getByRole("button", { name: "Start game" }).click();
  await expect.poll(() => stateRequestStarted, { timeout: 2500 }).toBe(true);
  await page.locator("#board .square-e2").click();
  await page.locator("#board .square-e4").click();
  await expect(
    page.locator('#board .square-e4 img[data-piece="wP"]'),
  ).toBeVisible();
  await page.waitForTimeout(1100);
  await expect(
    page.locator('#board .square-e4 img[data-piece="wP"]'),
  ).toBeVisible();
});

test("an uncertain move is not rolled back by an immediate old state", async ({
  page,
}) => {
  await page.route("**/new_game", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(initialState),
    }),
  );
  await page.route("**/move", (route) => route.abort("failed"));
  await page.route("**/state", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(initialState),
    }),
  );
  await page.goto("/");
  await page.getByRole("button", { name: "Start game" }).click();
  await page.locator("#board .square-e2").click();
  await page.locator("#board .square-e4").click();
  await expect(
    page.locator('#board .square-e4 img[data-piece="wP"]'),
  ).toBeVisible();
  await page.waitForTimeout(1000);
  await expect(
    page.locator('#board .square-e4 img[data-piece="wP"]'),
  ).toBeVisible();
});

test("an uncertain move response is reconciled before rollback", async ({
  page,
}) => {
  const accepted = {
    ...initialState,
    fen: "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1",
    turn: "black",
    moves: ["e4"],
    version: 1,
  };
  await page.route("**/new_game", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(initialState),
    }),
  );
  await page.route("**/move", (route) => route.abort("failed"));
  await page.route("**/state", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(accepted),
    }),
  );
  await page.route("**/bot_move", (route) => route.abort("failed"));
  await page.goto("/");
  await page.getByRole("button", { name: "Start game" }).click();
  await page.locator("#board .square-e2").click();
  await page.locator("#board .square-e4").click();
  await expect(
    page.locator('#board .square-e4 img[data-piece="wP"]'),
  ).toBeVisible();
  await expect(page.locator("#toast-region")).not.toContainText(
    "Unable to reach",
  );
});

test("black board arrow keys follow the visual orientation", async ({
  page,
}) => {
  const blackToMove = {
    ...initialState,
    fen: "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR b KQkq - 0 1",
    bot_color: "white",
    turn: "black",
  };
  await page.route("**/new_game", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(blackToMove),
    }),
  );
  await page.goto("/");
  await page.getByRole("button", { name: "Black" }).click();
  await page.getByRole("button", { name: "Start game" }).click();
  await page.locator("#board .square-e7").focus();
  await page.locator("#board .square-e7").press("ArrowUp");
  await expect(page.locator("#board .square-e6")).toBeFocused();
});

test("keyboard board moves and thinking status stays outside the board", async ({
  page,
}) => {
  let submittedMove = "";
  await page.route("**/new_game", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(initialState),
    }),
  );
  await page.route("**/move", (route) => {
    submittedMove = route.request().postDataJSON().uci;
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        ...initialState,
        fen: "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1",
        turn: "black",
        moves: ["e4"],
        version: 1,
      }),
    });
  });
  await page.route("**/bot_move", async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 300));
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        ...initialState,
        fen: "rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2",
        moves: ["e4", "e5"],
        version: 2,
      }),
    });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "Start game" }).click();
  await page.locator("#board .square-e2").click();
  await expect(page.locator("#board .square-e2")).toHaveClass(/sq-selected/);
  await expect(page.locator("#board .square-e4 .legal-dot")).toBeVisible();
  await expect(page.locator("#board .square-e4 .legal-dot")).toHaveCSS(
    "animation-name",
    "legal-marker-in",
  );
  await page.locator("#board .square-e4").click();
  await expect.poll(() => submittedMove).toBe("e2e4");
  await expect(page.locator("#status")).toHaveText("Alan Dai is thinking");
  await expect(page.locator("#board")).not.toContainText(
    "Alan Dai is thinking",
  );
});

test("underpromotion choice is sent to the server", async ({ page }) => {
  const state = {
    ...initialState,
    fen: "7k/P7/8/8/8/8/8/7K w - - 0 1",
    opening_name: "Custom position",
  };
  let submittedMove = "";
  await page.route("**/new_game", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(state),
    }),
  );
  await page.route("**/move", async (route) => {
    submittedMove = route.request().postDataJSON().uci;
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        ...state,
        fen: "N6k/8/8/8/8/8/8/7K b - - 0 1",
        turn: "black",
        moves: ["a8=N"],
        version: 1,
      }),
    });
  });
  await page.route("**/bot_move", (route) => route.abort());
  await page.goto("/");
  await page.getByRole("button", { name: "Start game" }).click();
  await page.locator("#board .square-a7").click();
  await page.locator("#board .square-a8").click();
  await expect(
    page.getByRole("heading", { name: "Choose promotion" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Promote to knight" }).click();
  await expect.poll(() => submittedMove).toBe("a7a8n");
});

test("mobile layout has no horizontal overflow", async ({ page, isMobile }) => {
  test.skip(!isMobile, "Mobile visual baseline only");
  await page.route("**/health/ready", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        status: "ready",
        model: "ready",
        stockfish: "ready",
      }),
    }),
  );
  await page.goto("/");
  await expect(page.locator("#stockfish-chip")).toHaveText("Stockfish ready");
  const overflow = await page.evaluate(
    () =>
      document.documentElement.scrollWidth -
      document.documentElement.clientWidth,
  );
  expect(overflow).toBeLessThanOrEqual(1);
});

test("desktop landing page renders its core controls", async ({
  page,
  isMobile,
}) => {
  test.skip(isMobile, "Desktop visual baseline only");
  await page.route("**/health/ready", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        status: "ready",
        model: "ready",
        stockfish: "ready",
      }),
    }),
  );
  await page.goto("/");
  await expect(page.locator("#stockfish-chip")).toHaveText("Stockfish ready");
  await expect(page.getByRole("button", { name: "Start game" })).toBeVisible();
  await expect(page.getByRole("tab", { name: "Analyze" })).toBeVisible();
});
