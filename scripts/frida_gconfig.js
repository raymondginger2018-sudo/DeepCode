'use strict';

console.log('== Frida bun.exe: g_config + JSC::VM ==');
console.log('PID: ' + Process.id);
console.log('Arch: ' + Process.arch);

try {
    var bunMod = Process.getModuleByName('bun.exe');
    console.log('bun.exe base: ' + bunMod.base);
    console.log('bun.exe size: ' + bunMod.size);
    
    // Read g_config string at Ghidra offset 0x5ae342d
    var gConfigAddr = bunMod.base.add(0x5ae342d);
    console.log('g_config string addr: ' + gConfigAddr);
    
    var buf = gConfigAddr.readByteArray(64);
    if (buf) {
        var bytes = new Uint8Array(buf);
        var s = '';
        for (var i = 0; i < bytes.length; i++) {
            if (bytes[i] === 0) break;
            s += String.fromCharCode(bytes[i]);
        }
        console.log('g_config value: "' + s + '"');
    }
    
    // Hex dump around g_config string
    var dumpStart = bunMod.base.add(0x5ae3400);
    var dumpBuf = dumpStart.readByteArray(192);
    if (dumpBuf) {
        console.log('\nMemory dump [0x5ae3400 - 0x5ae34c0]:');
        var dumpBytes = new Uint8Array(dumpBuf);
        for (var row = 0; row < 192; row += 16) {
            var hex = '';
            var ascii = '';
            for (var col = 0; col < 16 && row+col < 192; col++) {
                hex += dumpBytes[row+col].toString(16).padStart(2,'0') + ' ';
                ascii += (dumpBytes[row+col] >= 32 && dumpBytes[row+col] <= 126)
                    ? String.fromCharCode(dumpBytes[row+col]) : '.';
            }
            console.log(hex + ' ' + ascii);
        }
    }
    
    // Scan .rdata section for JSC::VM strings
    var rdataStart = bunMod.base.add(0x3848000);
    var scanSize = 2 * 1024 * 1024; // first 2MB of .rdata
    console.log('\nScanning .rdata for JSC::VM (' + scanSize + ' bytes)...');
    
    var scanBuf = rdataStart.readByteArray(scanSize);
    if (scanBuf) {
        var scanBytes = new Uint8Array(scanBuf);
        var found = 0;
        for (var i = 0; i < scanBytes.length - 7; i++) {
            if (scanBytes[i] == 0x4a && scanBytes[i+1] == 0x53 && scanBytes[i+2] == 0x43 &&
                scanBytes[i+3] == 0x3a && scanBytes[i+4] == 0x3a && scanBytes[i+5] == 0x56 &&
                scanBytes[i+6] == 0x4d) {
                var strAddr = rdataStart.add(i);
                var strBuf = strAddr.readByteArray(128);
                if (strBuf) {
                    var strBytes = new Uint8Array(strBuf);
                    var str = '';
                    for (var j = 0; j < strBytes.length; j++) {
                        if (strBytes[j] === 0) break;
                        str += String.fromCharCode(strBytes[j]);
                    }
                    console.log('  JSC::VM @ ' + strAddr + ': ' + str);
                }
                found++;
                if (found >= 5) break;
            }
        }
        console.log('JSC::VM found: ' + found);
    }
    
    // List key modules via callback API
    console.log('\nLoaded modules (relevant):');
    Process.enumerateModules({
        onMatch: function(m) {
            var low = m.name.toLowerCase();
            if (low.indexOf('bun') !== -1 || low.indexOf('jsc') !== -1 ||
                low.indexOf('javascript') !== -1 || low.indexOf('webkit') !== -1 ||
                low.indexOf('v8') !== -1)
                console.log('  ' + m.name + ' @ ' + m.base + ' sz=' + m.size);
        },
        onComplete: function() {
            console.log('  (done)');
        }
    });
    
} catch(e) {
    console.log('ERROR: ' + e.message);
}

console.log('\n== Analysis Complete ==');
