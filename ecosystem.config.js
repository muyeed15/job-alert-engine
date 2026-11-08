const fs = require("fs");
const path = require("path");

const venvPython = path.join(__dirname, ".venv", "bin", "python");
const interpreter = fs.existsSync(venvPython) ? venvPython : "python3";

module.exports = {
  apps: [
    {
      name: "job-notifier",
      cwd: __dirname,
      script: path.join(__dirname, "main.py"),
      args: "--run",
      interpreter: interpreter,
      exec_mode: "fork",
      instances: 1,
      autorestart: true,
      watch: false,
      max_memory_restart: "80M",
      min_uptime: "10s",
      max_restarts: 50,
      restart_delay: 5000,
      exp_backoff_restart_delay: 100,
      kill_timeout: 30000,
      merge_logs: true,
      time: false,
      env: {
        PYTHONUNBUFFERED: "1",
        PYTHONDONTWRITEBYTECODE: "1",
        MALLOC_ARENA_MAX: "2",
        TZ: "Asia/Dhaka",
        LOG_LEVEL: "INFO",
      },
    },
  ],
};
