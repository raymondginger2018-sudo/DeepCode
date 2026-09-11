// 验证: settings.json 语法 + deepcode-agent-sdk MCP server 真实启动
const fs = require("fs");
const { spawn } = require("child_process");
const readline = require("readline");

// 1. settings.json 语法校验
const settingsPath = "F:\\DEEPCODE\\.deepcode\\settings.json";
try {
  const cfg = JSON.parse(fs.readFileSync(settingsPath, "utf8"));
  const sdk = cfg.mcpServers && cfg.mcpServers["deepcode-agent-sdk"];
  console.log("SETTINGS_JSON: OK");
  console.log("SDK_SERVER_CONFIG:", JSON.stringify({ command: sdk && sdk.command, args: sdk && sdk.args }));
  if (!sdk) { console.error("SDK_SERVER_MISSING"); process.exit(1); }
} catch (e) {
  console.error("SETTINGS_JSON_BROKEN:", e.message);
  process.exit(1);
}

// 2. 真实启动 MCP server (node spawn → WindowsApps alias 直接解析为真实 python)
const PY = "C:\\Users\\raymo\\AppData\\Local\\Microsoft\\WindowsApps\\python3.exe";
const SCRIPT = "F:/DEEPCODE/.deepcode/skills/deepcode-agent-sdk/agent_sdk_server.py";

const child = spawn(PY, [SCRIPT, "--mcp"], {
  cwd: "F:\\DEEPCODE",
  env: { ...process.env, DEEPSEEK_API_KEY: "test-key", PYTHONIOENCODING: "utf-8" },
});

const rl = readline.createInterface({ input: child.stdout });
const pending = {};
let serverInfo = null;

function send(id, method, params) {
  return new Promise((resolve) => {
    pending[id] = resolve;
    child.stdin.write(JSON.stringify({ jsonrpc: "2.0", id, method, params: params || {} }) + "\n");
  });
}

// 标准 MCP 握手: 等 initialize 响应后 (被动模式，无主动推送)
async function run() {
  // 1. initialize → 标准握手
  const initResp = await send(1, "initialize", { protocolVersion: "2024-11-05", capabilities: {}, clientInfo: { name: "verify_mcp", version: "1.0" } });
  const res = initResp.result;
  if (!res || !res.serverInfo) { console.error("INIT_FAIL:", JSON.stringify(initResp)); process.exit(1); }
  serverInfo = res.serverInfo;
  console.log("SERVER_INFO:", serverInfo.name, "v" + serverInfo.version, "| proto:", res.protocolVersion);

  // 2. notifications/initialized → 服务器应静默 (不回错误)
  child.stdin.write(JSON.stringify({ jsonrpc: "2.0", method: "notifications/initialized" }) + "\n");

  // 3. tools/list → 应包含 agent_run
  const toolsResp = await send(2, "tools/list", {});
  const tools = toolsResp.result.tools;
  const names = tools.map((t) => t.name);
  console.log("TOOLS_COUNT:", names.length);
  console.log("TOOLS:", names.join(","));
  console.log("HAS_AGENT_RUN:", names.includes("agent_run"));

  const ar = tools.find((t) => t.name === "agent_run");
  console.log("AGENT_RUN_REQUIRED:", ar ? ar.inputSchema.required : "N/A");

  // 4. ping → result 应为空对象
  const pong = await send(3, "ping", {});
  console.log("PING:", JSON.stringify(pong.result));

  child.kill();
  console.log("MCP_SMOKE_OK");
  process.exit(0);
}

rl.on("line", (line) => {
  try {
    const msg = JSON.parse(line);
    if (msg.id && pending[msg.id]) {
      pending[msg.id](msg);
      delete pending[msg.id];
    }
  } catch (e) { console.error("BAD_LINE:", line.slice(0, 120)); }
});

child.stderr.on("data", (d) => process.stderr.write(d));
child.on("error", (e) => { console.error("SPAWN_ERR", e.message); process.exit(1); });
setTimeout(() => { console.error("TIMEOUT_KILL"); child.kill(); process.exit(2); }, 30000);

run().catch((e) => { console.error("RUN_ERR", e); process.exit(1); });
