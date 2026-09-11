// inspector-pause.mjs — 连接 node --inspect 调试端口，暂停并采样调用栈
// 用法: node inspector-pause.mjs [port] [frames]
const port = Number(process.argv[2] ?? 9229)
const maxFrames = Number(process.argv[3] ?? 40)

const targets = await (await fetch(`http://127.0.0.1:${port}/json`)).json()
if (!targets.length) {
  console.error('no inspect target')
  process.exit(1)
}
const ws = new WebSocket(targets[0].webSocketDebuggerUrl)
let seq = 0
const pending = new Map()
const send = (method, params = {}) =>
  new Promise((resolve, reject) => {
    const id = ++seq
    pending.set(id, { resolve, reject })
    ws.send(JSON.stringify({ id, method, params }))
  })

ws.onmessage = (ev) => {
  const msg = JSON.parse(ev.data)
  if (msg.id && pending.has(msg.id)) {
    const { resolve, reject } = pending.get(msg.id)
    pending.delete(msg.id)
    msg.error ? reject(new Error(msg.error.message)) : resolve(msg.result)
    return
  }
  if (msg.method === 'Debugger.paused') {
    const frames = (msg.params.callFrames ?? []).slice(0, maxFrames)
    console.log('=== paused call stack (top ' + frames.length + ' frames) ===')
    for (let i = 0; i < frames.length; i++) {
      const f = frames[i]
      const loc = f.location
        ? `${f.location.lineNumber + 1}:${f.location.columnNumber + 1}`
        : '?'
      console.log(`#${i} ${f.functionName || '(anonymous)'} @ ${f.url}:${loc}`)
    }
    console.log('=== end ===')
    ws.send(JSON.stringify({ id: ++seq, method: 'Debugger.resume' }))
    process.exit(0)
  }
}
ws.onopen = async () => {
  await send('Debugger.enable')
  await send('Debugger.pause')
  // 5 秒兜底退出（若 pause 未触发）
  setTimeout(() => {
    console.error('timeout: no pause received (loop may be in native code or idle)')
    process.exit(2)
  }, 5000)
}
