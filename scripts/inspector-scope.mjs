// inspector-scope.mjs — pause 后读取热循环栈帧的参数值，定位 tsx 解析中的模块
// 用法: node inspector-scope.mjs [port]
const port = Number(process.argv[2] ?? 9229)
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

async function evalOnFrame(callFrameId, expression) {
  try {
    const r = await send('Debugger.evaluateOnCallFrame', { callFrameId, expression, returnByValue: true })
    if (r.exceptionDetails) return `EXC: ${r.exceptionDetails.text}`
    return r.result?.value ?? r.result?.description ?? '(no value)'
  } catch (e) {
    return `ERR: ${e.message}`
  }
}

ws.onmessage = (ev) => {
  const msg = JSON.parse(ev.data)
  if (msg.id && pending.has(msg.id)) {
    const { resolve, reject } = pending.get(msg.id)
    pending.delete(msg.id)
    msg.error ? reject(new Error(msg.error.message)) : resolve(msg.result)
    return
  }
  if (msg.method === 'Debugger.paused') {
    const frames = (msg.params.callFrames ?? []).slice(0, 8)
    console.log(`=== paused, ${frames.length} frames ===`)
    for (let i = 0; i < frames.length; i++) {
      const f = frames[i]
      const loc = f.location ? `${f.location.lineNumber + 1}:${f.location.columnNumber + 1}` : '?'
      console.log(`\n#${i} ${f.functionName || '(anonymous)'} @ ${f.url}:${loc}`)
      const args = await evalOnFrame(f.callFrameId, '(() => { try { return JSON.stringify(Array.from(arguments)) } catch { return "n/a" } })()')
      if (args !== '[]' && !args.startsWith('ERR')) {
        console.log(`   args: ${String(args).slice(0, 400)}`)
      }
      // scope 变量名
      for (const scope of f.scopeChain ?? []) {
        if (scope.type === 'local' || scope.type === 'closure') {
          const names = await evalOnFrame(f.callFrameId, `Object.keys(${scope.type === 'local' ? 'this' : 'scope'})`).catch(() => '')
          if (names && names !== '[]' && names !== '(no value)') {
            console.log(`   ${scope.type} vars: ${String(names).slice(0, 300)}`)
          }
        }
      }
    }
    console.log('\n=== end ===')
    ws.send(JSON.stringify({ id: ++seq, method: 'Debugger.resume' }))
    process.exit(0)
  }
}
ws.onopen = async () => {
  await send('Debugger.enable')
  await send('Debugger.pause')
  setTimeout(() => {
    console.error('timeout: no pause received')
    process.exit(2)
  }, 6000)
}
