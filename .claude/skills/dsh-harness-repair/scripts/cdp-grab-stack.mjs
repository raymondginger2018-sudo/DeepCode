// cdp-grab-stack.mjs — DSH 事件循环死锁诊断工具
// 用法: node cdp-grab-stack.mjs <ws://inspector-url>
// 功能:
//   1. Debugger.pause 强停正在运行的 JS 死循环 (事件循环被占死时唯一可靠入口)
//   2. dump 调用栈 (函数名 + 行号 + 源码行)
//   3. 若命中 Entry._disabled 类遍历函数, 自动 evaluate parent 链, 标记 CYCLE/SELF
// 前置: 服务需带 inspector 启动:
//   NODE_OPTIONS="--inspect=0" node --import ./scripts/node-css-loader.mjs \
//     apps/cli/lib/bin.js --profile web --port 3081 --no-open
//   然后取 ws url: curl -s http://127.0.0.1:9229/json | jq -r '.[].webSocketDebuggerUrl'
const wsUrl = process.argv[2]
if (!wsUrl) { console.error('usage: node cdp-grab-stack.mjs <ws-url>'); process.exit(1) }

const ws = new WebSocket(wsUrl)
let id = 0
const pending = new Map()
const sources = new Map()

function send(method, params = {}) {
  return new Promise((resolve, reject) => {
    const msgId = ++id
    pending.set(msgId, { resolve, reject })
    ws.send(JSON.stringify({ id: msgId, method, params }))
    setTimeout(() => { if (pending.has(msgId)) { pending.delete(msgId); reject(new Error(`timeout ${method}`)) } }, 8000)
  })
}

async function sourceLine(scriptId, lineNumber) {
  if (!scriptId || lineNumber == null) return ''
  if (!sources.has(scriptId)) {
    try {
      const r = await send('Debugger.getScriptSource', { scriptId })
      sources.set(scriptId, r.scriptSource || '')
    } catch { sources.set(scriptId, '') }
  }
  const lines = (sources.get(scriptId) || '').split('\n')
  return lineNumber < lines.length ? lines[lineNumber].trim().slice(0, 150) : ''
}

ws.onmessage = async (ev) => {
  const msg = JSON.parse(ev.data)
  if (msg.id && pending.has(msg.id)) {
    const { resolve, reject } = pending.get(msg.id)
    pending.delete(msg.id)
    if (msg.error) reject(new Error(JSON.stringify(msg.error)))
    else resolve(msg.result)
  } else if (msg.method === 'Debugger.paused') {
    const frames = msg.params.callFrames
    console.log('=== PAUSED reason=' + msg.params.reason + ' frames=' + frames.length + ' ===')
    for (const f of frames.slice(0, 25)) {
      const fn = f.functionName || '(anonymous)'
      const src = await sourceLine(f.location?.scriptId, f.location?.lineNumber)
      console.log(`  ${fn}  @ L${(f.location?.lineNumber ?? -1) + 1} | ${src}`)
    }
    // 找疑似遍历/循环函数, evaluate 循环链
    const target = frames.find((f) => /_disabled|_find|_visit|while|iterate|traverse/.test(f.functionName || ''))
    if (target?.callFrameId) {
      try {
        const r = await send('Debugger.evaluateOnCallFrame', {
          callFrameId: target.callFrameId,
          expression: `(() => {
            if (typeof this === 'undefined' || this === null) {
              return { skipped: true, reason: 'frame has no this (arrow/global fn) - cannot probe parent chain' };
            }
            const seen = new Set();
            const chain = [];
            let entry = this;
            let guard = 0;
            while (entry && guard++ < 30) {
              if (seen.has(entry)) { chain.push({ CYCLE: true, backTo: entry.options?.id }); break; }
              seen.add(entry);
              const g = entry.parent;
              const next = g && g.ctx && g.ctx.fiber ? g.ctx.fiber.entry : null;
              chain.push({
                entryId: entry.options?.id,
                entryName: entry.options?.name,
                selfLoop: next === entry ? 'SELF' : (next ? 'next' : 'END'),
              });
              entry = next;
            }
            return { length: chain.length, chain };
          })()`,
          returnByValue: true,
        })
        console.log('\n=== parent-chain probe (循环链实体) ===')
        console.log(JSON.stringify(r?.result?.value ?? r, null, 1))
      } catch (e) { console.error('eval err:', e.message) }
    }
    try {
      await send('Debugger.resume')
      setTimeout(() => process.exit(0), 200)
    } catch (e) { console.error('resume failed:', e.message); process.exit(3) }
  }
}

ws.onopen = async () => {
  try {
    await send('Debugger.enable')
    await send('Debugger.pause')
    setTimeout(() => { console.error('no pause event — JS 可能空闲/awaiting, 非死循环'); process.exit(2) }, 9000)
  } catch (e) { console.error('err:', e.message); process.exit(1) }
}
ws.onerror = () => { console.error('ws error: connect failed - check inspector ws url (curl -s http://127.0.0.1:9229/json)'); process.exit(1) }
