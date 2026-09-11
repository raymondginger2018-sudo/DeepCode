'use strict';
console.log('1: ' + Process.id);
console.log('2: ' + Process.arch);
try { var m = Process.getModuleByName('bun.exe'); console.log('3: ' + m.base); } catch(e) { console.log('3 err: ' + e.message); }
try { console.log('4: ' + typeof Memory.readByteArray); } catch(e) { console.log('4 err: ' + e); }
try { var a = ptr('0x7ff724110000'); console.log('5: ' + a); } catch(e) { console.log('5 err: ' + e); }
try { var b = a.add(0x100); console.log('6: ' + b); } catch(e) { console.log('6 err: ' + e); }
try { var c = a.readByteArray(16); console.log('7: ' + (c ? 'ok' : 'null')); } catch(e) { console.log('7 err: ' + e.message); }
try { var buf = Memory.readByteArray(a, 16); console.log('8: ' + (buf ? 'ok' : 'null')); } catch(e) { console.log('8 err: ' + e.message); }
try { var d = Module.enumerateModules(); console.log('9: ' + d.length); } catch(e) { console.log('9 err: ' + e.message); }
try { var r = Process.enumerateRangesSync('r--'); console.log('10: ' + r.length); } catch(e) { console.log('10 err: ' + e.message); }
