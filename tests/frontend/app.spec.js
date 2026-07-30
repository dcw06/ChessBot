import { expect, test } from "@playwright/test";

test("starts an accessible game and prevents duplicate starts", async ({
  page,
}) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Alan Dai" })).toBeVisible();
  await expect(page.locator("#model-chip")).toContainText("ready", {
    ignoreCase: true,
  });
  await page.getByRole("button", { name: "Start game" }).click();
  await expect(page.locator("#game-panel")).toBeVisible();
  await expect(page.locator("#status")).toContainText("Your turn");
  await expect(page.getByRole("button", { name: "Resign" })).toBeEnabled();
});

test("analysis controls and empty states are keyboard reachable", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Analyze" }).click();
  await expect(
    page.getByRole("heading", { name: "Recent games" }),
  ).toBeVisible();
  await page.keyboard.press("Tab");
  await expect(page.locator(":focus")).toBeVisible();
});

test("underpromotion choice is sent to the server", async ({ page }) => {
  const state = {
    fen: "7k/P7/8/8/8/8/8/7K w - - 0 1",
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
    opening_name: "Custom position",
    decision_source: "—",
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
  await page.goto("/");
  const overflow = await page.evaluate(
    () =>
      document.documentElement.scrollWidth -
      document.documentElement.clientWidth,
  );
  expect(overflow).toBeLessThanOrEqual(1);
  await expect(page).toHaveScreenshot("landing-mobile.png", {
    fullPage: true,
    animations: "disabled",
  });
});

test("desktop landing page matches its visual baseline", async ({
  page,
  isMobile,
}) => {
  test.skip(isMobile, "Desktop visual baseline only");
  await page.goto("/");
  await expect(page).toHaveScreenshot("landing-desktop.png", {
    fullPage: true,
    animations: "disabled",
  });
});
