// 通用 Python 启动器 — 用完整路径启动真实 python3 执行脚本
const { spawn } = require("child_process");

const PY = "C:\\Users\\raymo\\AppData\\Local\\Microsoft\\WindowsApps\\python3.exe";
const script = process.argv[2];
const p = spawn(PY, [script], { stdio: ["ignore", "pipe", "pipe"], cwd: "F:\\DEEPCODE" });

p.stdout.on("data", (d) => process.stdout.write(d));
p.stderr.on("data", (d) => process.stderr.write(d));
p.on("close", (c) => process.exit(c));
p.on("error", (e) => { console.error("SPAWN_ERR " + e.message); process.exit(1); });
setTimeout(() => { console.error("TIMEOUT_KILL"); try { p.kill(); } catch {} process.exit(2); }, 90000);
