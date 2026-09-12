#!/usr/bin/env node
/**
 * Skye Player (tune / skye / skyemusic) — Node / NPM / NPX Entrypoint Launcher
 */

const { spawn } = require("child_process");
const path = require("path");
const fs = require("fs");

const rootDir = path.resolve(__dirname, "..");

// Ensure python3 binary is present
function findPython() {
  const candidates = ["python3", "python"];
  for (const cmd of candidates) {
    try {
      require("child_process").execSync(`${cmd} --version`, { stdio: "ignore" });
      return cmd;
    } catch (e) {}
  }
  return "python3";
}

const pythonCmd = findPython();
const args = process.argv.slice(2);

// Forward to python3 -m tune <args>
const env = { ...process.env, PYTHONPATH: rootDir };
const child = spawn(pythonCmd, ["-m", "tune", ...args], {
  cwd: rootDir,
  env,
  stdio: "inherit",
});

child.on("exit", (code) => {
  process.exit(code || 0);
});

child.on("error", (err) => {
  console.error("Failed to launch Skye Player:", err.message);
  console.error("Please ensure python3 is installed and on your PATH.");
  process.exit(1);
});
