#!/usr/bin/env python3
"""Clean Frida launcher - test basic API"""
import frida, subprocess, sys, time, os

FRIDA_JS = "F:/DEEPCODE/scripts/frida_gconfig.js"
BUN_EXE = "F:/DEEPCODE/targets/bun.exe"
TARGET_TS = "F:/DEEPCODE/targets/debug_target.ts"

def find_bun_pid():
    result = subprocess.run(
        ['tasklist', '/fi', 'imagename eq bun.exe', '/nh', '/fo', 'CSV'],
        capture_output=True, text=True, timeout=5
    )
    pids = []
    for line in result.stdout.strip().split('\n'):
        line = line.strip()
        if not line: continue
        parts = line.split(',')
        if len(parts) >= 2:
            name = parts[0].strip('"')
            pid_str = parts[1].strip('"')
            if 'bun' in name.lower():
                try: pids.append(int(pid_str))
                except: pass
    return max(pids) if pids else None

# Cleanup
subprocess.run(['taskkill', '/F', '/IM', 'bun.exe'], capture_output=True)
time.sleep(2)

# Start bun
env = os.environ.copy()
env['PORT'] = '0'
bun_proc = subprocess.Popen(
    [BUN_EXE, TARGET_TS],
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    env=env, cwd="F:/DEEPCODE/targets"
)
time.sleep(3)

if bun_proc.poll() is not None:
    print(f"ERROR: bun exited")
    sys.exit(1)

bun_pid = find_bun_pid()
if not bun_pid:
    print("ERROR: Cannot find bun PID")
    bun_proc.kill()
    sys.exit(1)

print(f"bun PID: {bun_pid}")

# Attach Frida
try:
    session = frida.attach(bun_pid)
    print("Attached!")
    
    with open(FRIDA_JS, 'r') as f:
        script_code = f.read()
    
    script = session.create_script(script_code)
    
    output = []
    def on_message(msg, data):
        if msg['type'] == 'send':
            print(f"  {msg['payload']}")
            output.append(msg['payload'])
        elif msg['type'] == 'error':
            print(f"  ERR: {msg.get('description', '')}")
            output.append(f"ERR: {msg.get('description', '')}")
    
    script.on('message', on_message)
    script.load()
    
    time.sleep(5)
    session.detach()
    print("Detached!")
    
except Exception as e:
    print(f"Error: {e}")
    import traceback
    traceback.print_exc()

# Cleanup
try:
    bun_proc.terminate()
    bun_proc.wait(timeout=3)
except:
    bun_proc.kill()
subprocess.run(['taskkill', '/F', '/IM', 'bun.exe'], capture_output=True)
print("Done!")
