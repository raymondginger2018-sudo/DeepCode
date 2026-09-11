#!/usr/bin/env python3
"""
Frida launcher: Start bun with debug_target.ts, attach Frida, read g_config + JSC::VM
"""
import subprocess
import time
import sys
import os
import signal

BUN_EXE = "F:/DEEPCODE/targets/bun.exe"
TARGET_TS = "F:/DEEPCODE/targets/debug_target.ts"
FRIDA_SCRIPT = "F:/DEEPCODE/scripts/frida_read_gconfig.js"
OUTPUT_FILE = "F:/DEEPCODE/targets/frida_gconfig_output.txt"

print("=" * 60)
print("Frida + Bun: g_config + JSC::VM Runtime Reader")
print("=" * 60)

# Step 1: Start bun process in background
print("\n[1] Starting bun with debug_target.ts...")
bun_proc = subprocess.Popen(
    [BUN_EXE, TARGET_TS],
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    cwd="F:/DEEPCODE/targets",
    text=True
)

# Wait for bun to start
time.sleep(2)

# Check if bun is running
if bun_proc.poll() is not None:
    print(f"ERROR: bun exited early with code {bun_proc.returncode}")
    stdout, stderr = bun_proc.communicate()
    print(f"stdout: {stdout}")
    print(f"stderr: {stderr}")
    sys.exit(1)

print(f"  bun PID: {bun_proc.pid}")

# Step 2: Read initial output from bun
try:
    import select
    # Non-blocking read
    stdout_bun = bun_proc.stdout.read1(4096) if hasattr(bun_proc.stdout, 'read1') else bun_proc.stdout.read(1024)
    print(f"  bun says: {stdout_bun.decode('utf-8', errors='replace')[:200]}")
except:
    pass

# Step 3: Run Frida
print(f"\n[2] Running Frida on PID {bun_proc.pid}...")
print(f"  Script: {FRIDA_SCRIPT}")

frida_cmd = [
    "python3", "-m", "frida-tools",
    "-n", str(bun_proc.pid),
    "-l", FRIDA_SCRIPT,
    "-o", OUTPUT_FILE
]

# Alternative: use frida.exe directly
# Check if frida CLI is available
import shutil
frida_cli = shutil.which("frida")
if frida_cli:
    print(f"  Using frida CLI: {frida_cli}")
    frida_result = subprocess.run(
        ["frida", "-p", str(bun_proc.pid), "-l", FRIDA_SCRIPT, "-o", OUTPUT_FILE],
        timeout=30,
        capture_output=True,
        text=True
    )
else:
    # Try using python frida module
    print("  Using Python frida module directly")
    import frida
    
    try:
        session = frida.attach(bun_proc.pid)
        print("  Attached to bun process!")
        
        # Read the script
        with open(FRIDA_SCRIPT, 'r') as f:
            script_code = f.read()
        
        # Create and load script
        script = session.create_script(script_code)
        
        # Set up output capture
        output_lines = []
        def on_message(message, data):
            if message['type'] == 'send':
                output_lines.append(message['payload'])
            elif message['type'] == 'error':
                output_lines.append(f"ERROR: {message['description']}")
        
        script.on('message', on_message)
        script.load()
        
        # Give script time to execute
        time.sleep(5)
        
        # Detach
        session.detach()
        
        # Save output
        with open(OUTPUT_FILE, 'w') as f:
            f.write('\n'.join(output_lines))
        
        print(f"\n  Output saved to {OUTPUT_FILE}")
        print("\n  Output:")
        for line in output_lines:
            print(f"  {line}")
            
    except Exception as e:
        print(f"  Frida error: {e}")

# Step 4: Cleanup
print("\n[3] Cleaning up...")
try:
    bun_proc.terminate()
    bun_proc.wait(timeout=5)
    print("  bun process terminated")
except:
    bun_proc.kill()
    print("  bun process killed")

print("\nDone!")
