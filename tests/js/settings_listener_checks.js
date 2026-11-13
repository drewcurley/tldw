// T-020 rev 5.6: the REAL storage.onChanged listener out of extension/content.js
// (between its "settings listener" BEGIN/END markers), with a fake api. It must
// stay the only listener, react only in the local area, keep closeAction working,
// and pass "Cover listed words" only the two newValues it uses -- never the token
// or anything else in `changes`.
//
// Usage: node settings_listener_checks.js <content.js>   (prints checks=<n>)
const fs = require("fs");
const vm = require("vm");
const src = fs.readFileSync(process.argv[2], "utf8");
const BEGIN = "// --- settings listener (BEGIN)";
const END = "// --- settings listener (END)";
const i = src.indexOf(BEGIN), j = src.indexOf(END);
if (i < 0 || j < 0) { console.error("listener markers not found"); process.exit(2); }

let n = 0;
const failures = [];
const check = (name, got, want) => {
  n++;
  if (JSON.stringify(got) !== JSON.stringify(want)) failures.push(`${name} -> ${JSON.stringify(got)}`);
};

check("exactly one onChanged listener in content.js",
  (src.match(/onChanged\.addListener/g) || []).length, 1);

const fns = [];
const ctx = vm.createContext({
  api: { storage: { onChanged: { addListener: (fn) => fns.push(fn) } } },
});
vm.runInContext(`let closeAction = "continue";\n` + src.slice(i, j) + `
  globalThis.__setMute = (f) => { onMuteSettings = f; };
  globalThis.__closeAction = () => closeAction;`, ctx);
check("one listener registered", fns.length, 1);
const fire = (changes, area) => fns[0](changes, area);

// Before "Cover listed words" exists: a change is a no-op, not a throw.
let threw = false;
try { fire({ muteSound: { newValue: "moo" } }, "local"); } catch (_) { threw = true; }
check("a change before the cover exists doesn't throw", threw, false);

const calls = [];
ctx.__setMute((live) => calls.push(live));
fire({ muteSound: { oldValue: "bleep", newValue: "moo" } }, "local");
check("muteSound passes only its newValue", calls.pop(), { sound: { newValue: "moo" } });
fire({ muteWords: { oldValue: "a", newValue: "zarf" } }, "local");
check("muteWords passes only its newValue", calls.pop(), { words: { newValue: "zarf" } });
fire({ muteWords: { oldValue: "a" } }, "local");
check("a removed key passes newValue undefined", JSON.stringify(calls.pop()), JSON.stringify({ words: {} }));
fire({ muteSound: { newValue: "mute" }, muteWords: { newValue: "zarf" },
  token: { newValue: "secret-token" }, serverUrl: { newValue: "x" } }, "local");
const both = calls.pop();
check("both keys in one call", Object.keys(both).sort(), ["sound", "words"]);
check("nothing else is passed on", JSON.stringify(both).includes("secret-token")
  || JSON.stringify(both).includes("serverUrl"), false);
fire({ token: { newValue: "secret-token" } }, "local");
check("token alone: no call", calls.length, 0);
fire({ muteSound: { newValue: "moo" } }, "sync");
check("another area: no call", calls.length, 0);
fire({ closeAction: { newValue: "abort" } }, "local");
check("closeAction still works", [ctx.__closeAction(), calls.length], ["abort", 0]);
fire({ closeAction: { newValue: "abort" } }, "sync");
fire({ closeAction: { newValue: "nonsense" } }, "local");
check("closeAction other values read continue", ctx.__closeAction(), "continue");

console.log("checks=" + n);
if (failures.length) { console.error(failures.join("\n")); process.exit(1); }
