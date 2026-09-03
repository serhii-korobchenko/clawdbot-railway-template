import childProcess from "node:child_process";

const apiHost = process.env.PROROK_API_HOST?.trim() || "0.0.0.0";
const apiPort = process.env.PROROK_API_PORT?.trim() || "18880";

const children = new Set();
let shuttingDown = false;
let forcedExitTimer = null;

function spawnManaged(command, args, label) {
  const child = childProcess.spawn(command, args, {
    stdio: "inherit",
    env: process.env,
  });

  children.add(child);

  child.on("error", (err) => {
    console.error(`[supervisor] ${label} spawn error: ${String(err)}`);
  });

  child.on("exit", (code, signal) => {
    children.delete(child);
    console.error(
      `[supervisor] ${label} exited code=${code} signal=${signal}`
    );

    if (!shuttingDown) {
      shutdown("SIGTERM", typeof code === "number" ? code : 1);
    }
  });

  return child;
}

function shutdown(signal = "SIGTERM", exitCode = 0) {
  if (shuttingDown) return;
  shuttingDown = true;

  for (const child of children) {
    try {
      child.kill(signal);
    } catch {
      // Best-effort shutdown.
    }
  }

  if (children.size === 0) {
    process.exit(exitCode);
  }

  for (const child of children) {
    child.once("exit", () => {
      if (children.size === 0) {
        if (forcedExitTimer) clearTimeout(forcedExitTimer);
        process.exit(exitCode);
      }
    });
  }

  forcedExitTimer = setTimeout(() => process.exit(exitCode || 1), 5_000);
  forcedExitTimer.unref?.();
}

process.on("SIGTERM", () => shutdown("SIGTERM", 0));
process.on("SIGINT", () => shutdown("SIGINT", 0));

spawnManaged(process.execPath, ["src/server.js"], "OpenClaw wrapper");

spawnManaged(
  "python3",
  [
    "-m",
    "uvicorn",
    "prorok_api.app:create_app",
    "--factory",
    "--host",
    apiHost,
    "--port",
    apiPort,
    "--workers",
    "1",
    "--no-access-log",
  ],
  "PROROK read-only API"
);
