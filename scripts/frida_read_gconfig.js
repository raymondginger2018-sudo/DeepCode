// Frida script: Attach to bun process and read g_config + JSC::VM
// Usage: frida -n bun.exe -l frida_read_gconfig.js

'use strict';

// Configuration
const G_CONFIG_STRING_ADDR = "0x145ae342d";  // Ghidra confirmed
const BUN_IMAGE_BASE = "0x140000000";         // Ghidra confirmed

function readCString(addr, maxLen = 256) {
    const buf = addr.readByteArray(maxLen);
    if (buf === null) return null;
    const bytes = new Uint8Array(buf);
    let s = '';
    for (let i = 0; i < bytes.length; i++) {
        if (bytes[i] === 0) break;
        s += String.fromCharCode(bytes[i]);
    }
    return s;
}

function readPointer(addr) {
    const buf = addr.readByteArray(8);
    if (buf === null) return null;
    const dv = new DataView(buf);
    return ptr(dv.getBigUint64(0, true).toString(16));
}

function hexDump(addr, size) {
    const buf = addr.readByteArray(size);
    if (buf === null) return "Read failed";
    const bytes = new Uint8Array(buf);
    let out = '';
    for (let i = 0; i < bytes.length; i += 16) {
        const hex = Array.from(bytes.slice(i, i+16)).map(b => b.toString(16).padStart(2,'0')).join(' ');
        const ascii = Array.from(bytes.slice(i, i+16)).map(b => b >= 32 && b <= 126 ? String.fromCharCode(b) : '.').join('');
        out += addr.add(i).toString() + ': ' + hex + '  ' + ascii + '\n';
    }
    return out;
}

console.log('\n========================================');
console.log('  Frida - bun.exe g_config + JSC::VM Reader');
console.log('========================================\n');

// Step 1: Find the g_config string in memory
const gConfigStrAddr = BUN_IMAGE_BASE.add("0x5ae342d");
console.log(`[1] g_config string at: ${gConfigStrAddr}`);
const gConfigStr = readCString(gConfigStrAddr);
console.log(`    Value: "${gConfigStr}"`);

// Step 2: Read memory around g_config
console.log(`\n[2] Memory around g_config string:`);
console.log(hexDump(gConfigStrAddr, 128));

// Step 3: Try to locate the actual g_config global variable
// In WebKit, g_config is a global struct - let's search for it
// by looking for a known pattern in the .data/.bss section
console.log(`[3] Searching for g_config global variable...`);

// Scan the data section for pointers to known JSC structures
// The g_config struct typically starts with specific fields
const dataSection = Module.findBaseAddress('bun.exe');
console.log(`    Bun base: ${dataSection}`);

// Step 4: Search for JSC::VM references
console.log(`\n[4] JSC::VM exploration...`);
// JSC::VM is typically accessed through thread-local storage or global variables
// Let's enumerate all modules and look for JSC symbols
Process.enumerateModules({
    onMatch: function(mod) {
        if (mod.name.toLowerCase().includes('bun') || 
            mod.name.toLowerCase().includes('jsc')) {
            console.log(`    Module: ${mod.name} @ ${mod.base} size=${mod.size}`);
        }
    },
    onComplete: function() {
        console.log('    Module enumeration complete');
    }
});

// Step 5: Read process environment for debug info
console.log(`\n[5] Process info:`);
console.log(`    PID: ${Process.id}`);
console.log(`    Arch: ${Process.arch}`);
console.log(`    Platform: ${Process.platform}`);
console.log(`    Page size: ${Process.pageSize}`);
console.log(`    Pointer size: ${Process.pointerSize}`);

// Step 6: Scan for "JSC::VM" in process memory
console.log(`\n[6] Scanning memory for JSC::VM related strings...`);
const ranges = Process.enumerateRanges({
    protection: 'r--',
    coalesce: true
});

let foundCount = 0;
for (const range of ranges) {
    try {
        const buf = range.base.readByteArray(range.size > 65536 ? 65536 : range.size);
        if (buf === null) continue;
        const bytes = new Uint8Array(buf);
        for (let i = 0; i < bytes.length - 7; i++) {
            // Look for "JSC::VM"
            if (bytes[i] === 0x4a && bytes[i+1] === 0x53 && bytes[i+2] === 0x43 &&
                bytes[i+3] === 0x3a && bytes[i+4] === 0x3a && bytes[i+5] === 0x56 &&
                bytes[i+6] === 0x4d) {
                const addr = range.base.add(i);
                console.log(`    Found JSC::VM at ${addr}: ${readCString(addr, 64)}`);
                foundCount++;
                if (foundCount >= 10) break;
            }
        }
    } catch(e) {
        // skip unreadable ranges
    }
    if (foundCount >= 10) break;
}

console.log(`\n[7] Summary:`);
console.log(`    g_config string found: ${gConfigStr}`);
console.log(`    JSC::VM references: ${foundCount}`);

// Keep script alive
console.log('\nScript running. Press Ctrl+C to detach.');
setInterval(() => {}, 60000);
