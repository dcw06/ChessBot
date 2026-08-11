(() => {
  const payload = window.MODEL_REVIEW_DATA;
  const positions = payload.positions;
  const storageKey = `chessbot-review:${payload.metadata.modelSha256}:${payload.metadata.sampleSeed}`;
  let reviews = JSON.parse(localStorage.getItem(storageKey) || "{}");
  let current = Number(localStorage.getItem(`${storageKey}:cursor`) || 0);
  const $ = (id) => document.getElementById(id);
  const pieceNames = {p:"P",n:"N",b:"B",r:"R",q:"Q",k:"K"};

  function parseFen(fen) {
    const rows = fen.split(" ")[0].split("/");
    const board = {};
    rows.forEach((row, rowIndex) => {
      let file = 0;
      for (const token of row) {
        if (/\d/.test(token)) { file += Number(token); continue; }
        const rank = 8 - rowIndex;
        board[`${"abcdefgh"[file]}${rank}`] = token;
        file += 1;
      }
    });
    return board;
  }

  function renderBoard(position) {
    const orientation = position.userColor;
    const files = orientation === "white" ? "abcdefgh" : "hgfedcba";
    const ranks = orientation === "white" ? [8,7,6,5,4,3,2,1] : [1,2,3,4,5,6,7,8];
    const pieces = parseFen(position.fen);
    const from = position.modelMoveUci.slice(0,2);
    const to = position.modelMoveUci.slice(2,4);
    const board = $("board");
    board.textContent = "";
    ranks.forEach((rank, rankIndex) => files.split("").forEach((file, fileIndex) => {
      const squareName = `${file}${rank}`;
      const square = document.createElement("div");
      square.className = `square ${(file.charCodeAt(0)-97+rank)%2 ? "light" : "dark"}`;
      if (squareName === from) square.classList.add("from");
      if (squareName === to) square.classList.add("to");
      const token = pieces[squareName];
      if (token) {
        const image = document.createElement("img");
        image.className = "piece";
        image.alt = token === token.toUpperCase() ? `White ${pieceNames[token.toLowerCase()]}` : `Black ${pieceNames[token]}`;
        image.src = `pieces/${token === token.toUpperCase() ? "w" : "b"}${pieceNames[token.toLowerCase()]}.png`;
        square.appendChild(image);
      }
      if (fileIndex === 0) square.insertAdjacentHTML("beforeend", `<span class="coord rank">${rank}</span>`);
      if (rankIndex === 7) square.insertAdjacentHTML("beforeend", `<span class="coord file">${file}</span>`);
      board.appendChild(square);
    }));
  }

  function save() {
    localStorage.setItem(storageKey, JSON.stringify(reviews));
    localStorage.setItem(`${storageKey}:cursor`, String(current));
  }

  function counts() {
    const values = Object.values(reviews);
    return {
      agree: values.filter(v => v.decision === "agree").length,
      disagree: values.filter(v => v.decision === "disagree").length,
      skip: values.filter(v => v.decision === "skip").length,
      reviewed: values.filter(v => v.decision).length,
    };
  }

  function render() {
    current = Math.max(0, Math.min(positions.length - 1, current));
    const position = positions[current];
    const review = reviews[position.id] || {};
    renderBoard(position);
    $("players").textContent = `${position.white} vs ${position.black}`;
    $("game-date").textContent = position.date || "Date unavailable";
    $("game-result").textContent = position.result;
    $("move-context").textContent = `${position.sideToMove} to move · move ${position.fullmove}`;
    $("model-move").textContent = position.modelMoveSan;
    $("model-detail").textContent = `${position.modelMoveUci} · ${(position.modelConfidence*100).toFixed(1)}% legal-policy confidence`;
    $("orientation").textContent = `${position.userColor} orientation`;
    $("position-id").textContent = `Position ${current+1} of ${positions.length}`;
    $("note").value = review.note || "";
    ["agree","disagree","skip"].forEach(name => $(name).classList.toggle("active", review.decision === name));
    $("previous").disabled = current === 0;
    $("next").disabled = current === positions.length - 1;
    const summary = counts();
    $("agree-count").textContent = summary.agree;
    $("disagree-count").textContent = summary.disagree;
    $("skip-count").textContent = summary.skip;
    $("progress-label").textContent = `${summary.reviewed} / ${positions.length} reviewed`;
    $("remaining-label").textContent = `${positions.length-summary.reviewed} remaining`;
    $("progress-fill").style.width = `${summary.reviewed/positions.length*100}%`;
    save();
  }

  function decide(decision) {
    const id = positions[current].id;
    reviews[id] = {...(reviews[id] || {}), decision, note: $("note").value, reviewedAt: new Date().toISOString()};
    save();
    if (current < positions.length - 1) current += 1;
    render();
  }

  ["agree","disagree","skip"].forEach(name => $(name).addEventListener("click", () => decide(name)));
  $("previous").addEventListener("click", () => { current -= 1; render(); });
  $("next").addEventListener("click", () => { current += 1; render(); });
  $("note").addEventListener("change", () => {
    const id = positions[current].id;
    reviews[id] = {...(reviews[id] || {}), note: $("note").value};
    save();
  });
  $("reset").addEventListener("click", () => {
    if (confirm("Clear all review decisions and notes?")) { reviews = {}; current = 0; save(); render(); }
  });
  $("export").addEventListener("click", () => {
    const output = {metadata: payload.metadata, exportedAt: new Date().toISOString(), reviews};
    const blob = new Blob([JSON.stringify(output, null, 2)], {type:"application/json"});
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = "chessbot-model-review.json";
    link.click();
    URL.revokeObjectURL(link.href);
  });
  render();
})();

