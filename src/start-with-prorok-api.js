import childProcess from "node:child_process";
import fs from "node:fs";
import path from "node:path";

const apiHost = process.env.PROROK_API_HOST?.trim() || "0.0.0.0";
const apiPort = process.env.PROROK_API_PORT?.trim() || "18880";
const collectorInterval =
  process.env.PROROK_REFRESH_COLLECTOR_INTERVAL_SECONDS?.trim() || "30";

const managedSkills = ["reel-analyzer"];
const workspaceSkillsDir = "/data/workspace/skills";

for (const skillName of managedSkills) {
  const source = path.join("/app/skills", skillName);
  const target = path.join(workspaceSkillsDir, skillName);
  if (!fs.existsSync(source)) {
    throw new Error(`Managed skill source missing: ${source}`);
  }
  fs.mkdirSync(workspaceSkillsDir, { recursive: true });
  fs.rmSync(target, { recursive: true, force: true });
  fs.cpSync(source, target, { recursive: true });
  console.log(`[skills] synced ${skillName} -> ${target}`);
}

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

spawnManaged(
  "python3",
  [
    "prorok/prorok_refresh_collector_v10.py",
    "--interval-seconds",
    collectorInterval,
  ],
  "PROROK refresh collector"
);
