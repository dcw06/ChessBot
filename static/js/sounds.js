let context;
let enabled = true;
try {
  enabled = localStorage.getItem("chessbot:sound") !== "off";
} catch {
  /* Sound remains enabled. */
}

const notes = {
  move: [420, 0.045],
  capture: [260, 0.08],
  check: [620, 0.11],
  castle: [360, 0.13],
  end: [180, 0.3],
  notify: [520, 0.07],
};

export function soundEnabled() {
  return enabled;
}
export function setSoundEnabled(value) {
  enabled = Boolean(value);
  try {
    localStorage.setItem("chessbot:sound", enabled ? "on" : "off");
  } catch {
    /* Preference remains in memory. */
  }
}

export function playSound(kind = "move") {
  if (!enabled) return;
  try {
    context ||= new (window.AudioContext || window.webkitAudioContext)();
    const [frequency, duration] = notes[kind] || notes.move;
    const oscillator = context.createOscillator();
    const gain = context.createGain();
    oscillator.type = "sine";
    oscillator.frequency.value = frequency;
    gain.gain.setValueAtTime(0.0001, context.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.11, context.currentTime + 0.008);
    gain.gain.exponentialRampToValueAtTime(
      0.0001,
      context.currentTime + duration,
    );
    oscillator.connect(gain).connect(context.destination);
    oscillator.start();
    oscillator.stop(context.currentTime + duration + 0.02);
  } catch {
    /* Audio is an optional enhancement. */
  }
}
