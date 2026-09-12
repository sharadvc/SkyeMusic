#!/usr/bin/env node
/**
 * Skye Player postinstall hook for npm install -g skyemusic
 */

const { execSync } = require("child_process");
const path = require("path");
const fs = require("fs");

const installSh = path.resolve(__dirname, "..", "install.sh");

if (fs.existsSync(installSh)) {
  try {
    fs.chmodSync(installSh, 0o755);
    execSync(`"${installSh}"`, { stdio: "inherit" });
  } catch (e) {
    // Ignore postinstall errors to allow fallback to Node launcher
  }
}
