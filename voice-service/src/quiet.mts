// Import first. Baileys' signal layer prints whole session objects (including key
// bytes) to the console. Drop those lines so keys never reach logs or pasted output.
const NOISE = /^(Closing session|Opening session|Removing old closed session|Migrating session|Session already)/;
for (const m of ["log", "info", "warn"] as const) {
  const orig = console[m].bind(console);
  console[m] = (...args: unknown[]) => {
    if (typeof args[0] === "string" && NOISE.test(args[0])) return;
    orig(...args);
  };
}
