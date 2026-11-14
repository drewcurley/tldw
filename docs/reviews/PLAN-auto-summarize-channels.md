# PLAN — auto-summarize videos from channels you choose

**Status: PASS.** Plan rev 6 was approved by the owner on 2026-11-14 and has been built and reviewed; see "Build review" below.
- All 8 agents have signed off over three rounds, and the 7 lenses are below.
- The open questions for the owner are listed in "Questions for the owner".

- **Branch:** `feat/auto-summarize-channels`.
- **Review scope:** a new feature, so the full cycle: all 8 agents and the 7 lenses.
- **Process gate:** the owner approves the reviewed plan before any code is written.
- **Depends on `fix/stale-page-metadata`** (`PLAN-stale-page-metadata.md`), which merges first.
  - Without it, an auto-run after an in-page navigation is saved with the previous video's title and channel, and possibly its transcript (measured at about 2.3 s of stale rows).
  - This plan relies on that fix's page-sync gate in `collectPageTranscript(videoId, isCurrent)` and on its `page_v` marker.

## Brief (owner, 2026-11-13)

> right now, you have to click the extension to get it started. let's allow an
> option (off by default) to have the extension auto execute in the background
> (modal closed) on video load on a list of channels. when you use the extension on
> a video, after loading, you're given an option to add the channel to the
> auto-execute list (can be removed via settings page).

## What happens today (verified 2026-11-13)

- **One way in: the toolbar click.**
  - `background.js:58` injects `content.js` via `scripting.executeScript` under `activeTab`, then sends `TLDW_INVOKE`.
  - No YouTube host permission and no declared content script, so nothing runs on YouTube until you click.
- **Closed-panel runs already exist.**
  - `startSummarize` (`content.js:565`) runs with the panel closed.
  - While `backgrounded`, `onPartial` (`content.js:907`) keeps state and draws nothing.
  - On finishing, `endBackground` → `toast(...)` (`content.js:745-780`).
  - The Port's 20 s ping keeps the MV3 worker alive. The ping is the *only* thing that does: `content.js:332-335` says so, and reading a streaming fetch body doesn't reset the worker's idle timer.
- **YouTube is a single-page app.** Measured live (muted):
  - `yt-navigate-finish` fires while `ytd-watch-flexy[video-id]` and the owner link still show the previous video.
  - About 0.3 s later, `ytd-watch-metadata[video-id]` changes to the new ID. At that moment, the title and owner link inside it are already the new video's, and the old transcript rows are gone. This was checked with a MutationObserver on 3 of 3 navigations.
  - Owner links come as `/@handle` or `/channel/UC…`.
- **Both manifests use classic scripts.**
  - Chrome: `"service_worker": "background.js"`, with no `"type": "module"`.
  - Firefox: an event page, `background.scripts`, which has no `importScripts`.
- **Existing toast defects** this feature relies on fixing:
  - `toast()` appends to `document.body`, so it's invisible in fullscreen (`content.js:777`), unlike `mount()` and the pill.
  - It adds "— click to read" to every non-error message (`content.js:765`).
  - Its click opens `pendingOffer` ahead of `lastPayload`, and `startSummarize` never clears `pendingOffer` (`content.js:768`).
  - It opens a fresh result titled "(cached)" (`showResult(lastPayload, true)`).

## The change

### 0. One shared implementation: `extension/channels.js` (Frontend B1, Backend B1)

- **The whole file is one IIFE.** It declares nothing at the top level and ends with an unconditional `globalThis.TLDW_CHANNELS = Object.freeze({ ORIGIN, MAX_CHANNELS, HOUR_LIMIT, DAY_LIMIT, channelKey, isValidKey, sanitizeChannels, sanitizeRunLog, autoRunDecision, budgetDecision, autoRowText })`. Everything in it is pure.
  - Re-running it is harmless. The toolbar re-injects it on every click, and a tab where the registered script has already run gets it again too.
  - It can't collide with `background.js`'s or `options.js`'s top-level `const`s.
  - It doesn't use `??=`, because a page element with `id="TLDW_CHANNELS"` is visible through named access and would be taken as the value.
- **Loaded in four places:**
  1. The content script: `files: ["channels.js", "content.js"]`, both in the toolbar's `executeScript` and in the registered script.
  2. `options.html`: a `<script>` before `options.js`.
  3. The Chrome worker. The **first line** of `background.js` is `if (typeof importScripts === "function") importScripts("channels.js");` (Firefox event pages have no `importScripts`).
  4. Firefox `background.scripts`: `["channels.js", "background.js"]`. `build-firefox.sh` copies it.
- **`content.js` captures it inside its own IIFE and fails closed.** If `TLDW_CHANNELS` or `channelKey` is missing, every auto feature is off and today's behaviour is unchanged.
- **The worker fails closed too** (DevOps W1).
  - `importScripts` is wrapped in `try/catch`, so a missing or broken `channels.js` can never take down the worker, and manual runs keep working.
  - If `TLDW_CHANNELS` is missing:
    - `syncAutoScript` unregisters;
    - `channels:add` and `channels:remove` reply `{ ok: false }`;
    - an `auto` summarize is refused with `autoPaused`, the same reply as a full budget, rather than throwing.

### 1. Permission: optional, requested only when the feature is turned on

- **Both manifests** gain `"optional_host_permissions": ["https://www.youtube.com/*"]`. The required `host_permissions` stay `127.0.0.1:8765` only, so there's no new warning at install, and with the option off the extension can't read YouTube pages.
- **The options checkbox `click` handler** (Architect W6, Frontend W8). By the time `click` fires, `checked` has already flipped. If it's now on:
  - set `box.checked = false; box.disabled = true;`;
  - call `permissions.request({ origins: [ORIGIN] })` **first, before any `await`**;
  - on resolve, `checked = granted`. If granted, save `autoRun: true` and announce "On. Open or reload a YouTube tab to start." If refused, announce the refused line.
  - A *rejection* (the origin can't be requested) shows "not supported", not "refused".
  - Turning it off saves `autoRun: false` and calls `permissions.remove`.
  - The page doesn't use `preventDefault()`.
- **Access revoked** outside the page, where `autoRun` is true but `permissions.contains` is false: the box shows unticked, with the revoked line, and ticking it again takes the same request path.
  - The options page also listens to `permissions.onAdded` and `permissions.onRemoved`, so a change made while it's open shows straight away (Frontend S14).
- **Unsupported:**
  - If `api.permissions?.request` or `api.scripting?.registerContentScripts` is missing, the section is replaced by the unsupported line.
  - Firefox 115 support for `optional_host_permissions` together with `registerContentScripts` is **unverified** (not checked by machine). The PR records it as an open item; the owner uses Chrome.
- **Registration is owned by the worker.** `syncAutoScript()` keeps one dynamic content script:
  - id `tldw-auto`, matches `[ORIGIN]`, js `["channels.js", "content.js"]`, `runAt: "document_idle"`, `allFrames: false` (which keeps it out of YouTube's live-chat frames), `persistAcrossSessions: true`.
  - It is present exactly when `autoRun && permissions.contains`.
  - **Serialized (Architect B1):** `syncChain = syncChain.then(doSync, doSync)`.
    - Each `doSync` reads both conditions fresh.
    - It compares against `scripting.getRegisteredContentScripts({ ids: ["tldw-auto"] })` and registers or unregisters only on a difference (Backend S1).
    - "Duplicate ID" and "nonexistent ID" errors are still caught. Any **other** register error is logged (`[TL;DW auto] register failed: …`) and leaves the script unregistered. A rejected step never stops later syncs: each step has its own `.catch` (DevOps W2).
  - **Listeners** are added synchronously at the top level, for:
    - `runtime.onInstalled` (all reasons): unregister, then sync, both on the chain;
    - `runtime.onStartup`;
    - `storage.onChanged` (for `autoRun`);
    - `permissions.onAdded`;
    - `permissions.onRemoved`.
- **The init guard** becomes `if (window.__tldwInit === true) return;` (Architect S4).
  - `TLDW_INVOKE` reaches the one instance.
  - **Re-attaching to an auto-run** (`content.js:1912`) makes it manual (`autoRunVid = null`), so a later navigation won't silently drop a run the user opened (Backend W6, Frontend W5).
  - A toolbar click while an auto check is **polling or in its dwell** cancels that check and adds the video ID to `autoHandled` (Backend S2).
- **Tabs already open** get the script on their next full load. A tab where TL;DW was already clicked has it, and its navigation listener works from then on.

### 2. Auto-run in `content.js`

- **State** (`autoRun`, `autoChannels`, `autoHandled`, `autoRunVid`, `autoGen`, `autoTimer`) is declared outside the regions the tests slice out, so tests can read it (Frontend S12).
- **`settingsReady`** is the promise returned by `storage.local.get({autoRun: false, autoChannels: []})`, run through `sanitizeChannels`. The first check, and the panel row, both wait for it (Frontend B2). `storage.onChanged` keeps it current.
- **`channelKey(href)`**:
  - Parse with `new URL(href, "https://www.youtube.com")`. The host must be `www.youtube.com`. Only the path is used.
  - `/@<h>[/…]` → `@` + `decodeURIComponent(h).normalize("NFC").toLowerCase()`. A decode error gives `null`.
  - `/channel/UC[A-Za-z0-9_-]{22}[/…]` → `channel/UC…`, with case kept.
  - Anything else gives `null`.
  - `isValidKey(k)` is `channelKey("/" + k) === k`.
- **`channelFromPage(videoId, doc)`** reuses the fix's `watchMetadataFor(videoId)`.
  - Inside that element, read `#owner ytd-channel-name a` with `getAttribute("href")` and pass it to `channelKey`. The name is the link's `textContent`, trimmed and capped at 100 characters.
  - It returns `{ key, name, href }` or `null`. A video with several channels gives `null`.
- **`initAutoRun()`** (sliceable) adds two permanent listeners:
  - `document` `yt-navigate-finish` → `onNavigated()`;
  - `document` `visibilitychange` → `onVisibility()`.
  - Then `settingsReady.then(() => scheduleAutoCheck())`.
- **`onNavigated()`**:
  1. If an *auto* run is in flight (`autoRunVid`) and the URL's `v` ≠ `autoRunVid`, **leave it**: see below. Navigation is detected on `yt-navigate-finish` against `location`. It never reads `e.detail`, which may not cross worlds (Frontend W3).
  2. If the URL's `v` differs from `pendingOffer`'s video, clear `pendingOffer` (Backend W3).
  3. Call `scheduleAutoCheck()`.
- **`scheduleAutoCheck()`**: `autoGen++`, `clearTimeout(autoTimer)`, then a chain of callback steps, not `async`/`sleep` loops, so a fake clock can drive it (Frontend W4). Every step first checks that `gen === autoGen`. The steps:
  1. Bail unless `autoRun` is on, `autoChannels` isn't empty, and `location` is `/watch?v=<11>`.
  2. Poll `channelFromPage(v)` every 250 ms for up to 15 s. Give up if `v` changes.
  3. **Visible only:** if the tab is hidden, stop. `onVisibility()` re-runs `scheduleAutoCheck()` when the tab becomes visible.
  4. **Dwell:** 3 s of the tab visible on the same `v`. Becoming hidden cancels it, and it starts again from 0 when the tab is visible again.
  5. **`autoRunDecision(state)`**: `"run"` or `"skip:<reason>"`. It skips when:
     - the channel isn't listed (by `key`);
     - the video is already in `autoHandled`;
     - `requestActive` is true;
     - the panel is open;
     - `lastPayload` is already this video.
  6. **Run:** `autoHandled.add(v)`, then `startSummarize(url, v, false, false, { auto: true })`.
- **`startSummarize` changes** (Backend W2, W3):
  - It now always resets `progressPct = 0` and `pendingOffer = null` itself.
  - With `{ auto: true }`:
    - no `showLoading()`;
    - `backgrounded = true` is set *after* the existing reset to false;
    - `autoRunVid = videoId`;
    - the `summarize` message carries `auto: true`.
  - The page read uses the fix's `collectPageTranscript(videoId, isCurrent)`.
  - The transcription-accept path calls it with no options, so that run is always manual.
- **The port's `onMessage` and `onDisconnect`** both first check `port === myPort` (Backend and Frontend, rev 4). A drained or superseded port that disconnects can't put "Lost connection" on the new run.
- **The port listener** handles a port that's no longer current only through the drain logic below. It also has an **`autoPaused` branch** (Backend W1, Frontend W6): `requestActive = false`, `clearTimers()`, disconnect, `autoRunVid = null`, then the paused notice (§3), shown at most once per page.
- **Leaving a video mid-run** (Backend B2):
  - **Before** the `summarize` message is sent (during the probe or the page read): drop it. Disconnect, `requestActive = false`, clear timers, no toast. The fix's `isCurrent` stops the page read.
  - **After** it's sent: **drain** (Backend and Frontend, rev 4):
    1. `clearTimers()` first. The old run's ping calls the global `port`, and its 160 s safety timer checks only `requestActive`, so either would land on the next run.
    2. Then move the port into `drains`, a `Set` of `{ port, ping, cap }`. Each drain has its own 20 s ping and a 160 s cap timer in its own closure.
    3. `port = null; requestActive = false; autoRunVid = null;`
    - **A drain ends on** a terminal message (`result`, `error`, `needsTranscription`, `autoPaused`), on its port disconnecting, or on the cap. Ending disconnects it, clears its two timers and removes it from `drains`.
    - It shows **no toast** and changes no panel state.
    - So the worker stays alive, the server finishes, and both caches fill. A later click is instant.
    - There can be several drains at once. The budget bounds them.
  - **A transcribing auto-run is never drained** (Backend, rev 4). If the run is transcribing when you leave (`txLive` is set), or a drain receives `transcribing`, the content script sends `stopTranscribe(url)` (`background.js:227`), then disconnects, with no toast. A drained transcription would be an invisible job of up to 40 minutes, with no Stop control, holding the server's only transcription slot.
  - **The cap matches the 160 s content-side safety timer**, which is above `CLIENT_TIMEOUT_MS` (150 s), so the worker always sends a terminal error first. There's one recorded edge case. Under `whisper: "always"`, the worker uses the 40-minute budget even for a captioned video (`background.js:144-145`), so a drained captioned summary that runs past 160 s is cut off uncached. That's rare, and accepted.
  - **A manual run** in the background keeps today's behaviour: it shows a toast wherever you are.
- **One trust guard for the panel** (Architect W3, Frontend S11): a capture-phase `click` listener on the shadow root, `if (!e.isTrusted) e.stopImmediatePropagation()`.
  - It covers the new button, Manage, the transcription accept, and the existing Regenerate, Listen and Ask buttons. `content.js` never calls `.click()` on shadow elements.
  - The toast lives in the page DOM, so it checks `e.isTrusted` itself.
- **Errors (Q2)** show a toast as backgrounded runs do today, with an auto prefix (§3). `whisper: "ask"` shows the "needs an answer" toast. `whisper: "always"` transcribes.
- **The transcript panel (Q7):** accepted. It opens and closes briefly, as it does for a click. `restoreTranscriptPanel` keeps a panel the user already had open.

### 3. Toast and panel UI (UX, Frontend)

- **`toast(message, kind)`**, where `kind` is `"ready"`, `"offer"`, `"error"` or `"notice"`. Its one caller, `endBackground(message, isError)`, becomes `endBackground(message, kind)`. Every call site is migrated (Frontend, rev 4):

  | Call site | What it is | Kind |
  |---|---|---|
  | `content.js:613` | Transcription safety timeout | `error` |
  | `content.js:626` | "Needs an answer" | `offer` (its click opens `pendingOffer`) |
  | `content.js:636` | Ready | `ready` (its click opens `lastPayload`, never `pendingOffer`) |
  | `content.js:639` | Server error | `error` |
  | `content.js:647` | Port disconnect | `error` |
  | `content.js:668` | Safety timeout | `error` |
  | new | Paused | `notice` |

- **The "auto" wording** (the auto ready copy and the "Automatic summary failed:" prefix) is chosen from a flag in the run's own closure (`opts.auto`), and only while `autoRunVid` still equals that run's video. A run the user re-attached to, which makes it manual, gets the manual wording (Frontend, rev 4).
- **Toast behaviour:**
  - `notice` has no "click to read" suffix and does nothing on click (UX B1).
  - It mounts on `document.fullscreenElement || document.body`, like the pill (UX B2).
  - Its click acts only on `e.isTrusted`.
  - `ready` opens `lastPayload` with `showResult(lastPayload, lastCached)`, so a fresh result isn't labelled "(cached)" (UX W7).
- **The panel row**:
  - **The slot:** an empty `<div class="autorow" hidden>` slot sits under the meta line, as `.sponsor` already does. `renderAutoRow()` fills it, and is called from `showResult`, from `settingsReady`, and from `storage.onChanged` while the panel is open (Frontend S10).
  - **The row is built with `createElement` and `textContent`, never `innerHTML`** (SDET B1). The fake DOM in the tests can then exercise focus, the persistent status node and the trust guard. The pure `autoRowText({ listed, name })` returns the strings (the label, the `title`, the listed text), and those are tested as strings. Being text, the channel name can't inject markup. The row contains:
    - a control: the not-listed button, or the listed text plus a **Manage `<button>` styled as a link** (UX W2, Frontend W9);
    - a **separate, persistent** `<span class="autostatus" role="status" aria-live="polite">` that is never re-rendered (Frontend W7).
  - **Clicking add:**
    - The button is disabled while the add is in flight. If the add fails, it's re-enabled and keeps focus, so "Try again" works from the keyboard (UX, rev 4).
    - The row flips only when the worker confirms the write.
    - Focus moves to Manage (UX W1).
    - A rejected `sendMessage` (the worker died) is announced as a failure (Backend W5).
  - **Styling:**
    - `.meta` colours (#666 light, #a8a8ad dark).
    - `overflow-wrap: anywhere` for long names, with the full name in `title` (UX W8, S4).
    - The styles go in `mount()`'s stylesheet.
- **Copy** (UX's final table; the UX Designer owns wording):

| Surface | Copy |
|---|---|
| Panel button (not listed) | ⚡ Always summarize {name} |
| Button `title` | Summarize new videos from this channel in the background when you open them |
| Listed row | ✓ {name} is summarized automatically · **Manage** |
| Live: ok | Added. {name}'s videos will be summarized when you open them. |
| Live: exists | {name} is already on your list. |
| Live: full | Your list is full (500 channels). Remove some in Settings. |
| Live: failed | Couldn't add {name}. Try again. |
| Auto ready toast | TL;DW summarized this video for you — click to read |
| Offer toast (all runs) | TL;DW needs your answer about this video — click to see it |
| Auto error toast | Automatic summary failed: {existing message} |
| Paused (hour), notice | TL;DW paused automatic summaries: 10 already this hour. Click the TL;DW button to summarize this one. |
| Paused (day), notice | TL;DW paused automatic summaries: 40 already today. Click the TL;DW button to summarize this one. |
| Section heading | Automatic summaries |
| Section note | Changes here apply immediately. |
| Checkbox | Summarize videos from chosen channels automatically |
| Hint | When you open a video from a channel on your list, TL;DW summarizes it in the background and shows a notice when it's ready. That uses your Claude plan, up to 10 videos an hour, and only for the tab you're looking at. Chrome will ask to let TL;DW read youtube.com pages; this applies to YouTube tabs you open or reload afterwards. |
| Toggle on | On. Open or reload a YouTube tab to start. |
| Refused | Automatic summaries need access to youtube.com, which wasn't given. Nothing changed. |
| Revoked | Automatic summaries are paused: TL;DW no longer has access to youtube.com. Tick the box to allow it again. |
| Unsupported | Automatic summaries aren't supported in this browser. |
| Empty list | No channels yet. Summarize a video with TL;DW, then choose ⚡ Always summarize. |
| Remove button | Remove (`aria-label="Remove {name}"`) |
| Removed | Removed {name}. |

### 4. One writer for the channel list: the worker

- **Messages.** `runtime.onMessage` handles `channels:add`, `channels:remove` and `openOptions`, and only from `sender.id === runtime.id`.
  - The listener is **not `async`**. For the three types it calls `sendResponse` and returns `true`. For anything else it returns `undefined` (Backend W5).
- **Serialized.** A promise chain, where every step:
  - has a `.catch`, so one failure can't jam the chain;
  - re-reads storage;
  - runs `sanitizeChannels`;
  - checks `isValidKey(key)`;
  - caps the name at 100 characters.
  - At 500 channels it returns `full`. Nothing is ever evicted.
- **Stored shape:** `{ key, name, href, addedAt }`. Readers keep unknown fields, so a later alias field needs no migration. This PR does no alias matching.
- **`sanitizeChannels(raw)`:**
  - a non-array gives `[]`;
  - each entry must be an object with a valid key and a string name, capped at 100 characters;
  - `href` is kept only if it's a string of at most 300 characters;
  - `addedAt` is kept only if it's a finite number;
  - duplicates are dropped by key, keeping the first;
  - the list is cut to 500;
  - it never writes back.
- **The options page never writes the list.** It re-renders on `storage.onChanged`.
- **Known limit:** a channel saved as `channel/UC…` that later gets a handle, or one whose handle is renamed, stops matching. The panel then offers to add it again.

### 5. The worker's budget (Architect B2, Data, Backend W4)

- **`budgetDecision(log, now)`** (pure) allows a run when there were fewer than **10** auto starts in the last hour and fewer than **40** in the last 24 hours. It reports which limit was hit.
- **The log** lives in `storage.local` under `autoRunLog`. It is read through `sanitizeRunLog`, which:
  - turns a non-array into `[]`;
  - drops non-finite entries;
  - drops entries in the future (Data, rev 3), so setting the clock back can't pause auto-runs.
  - Every write prunes the log to the last 24 hours.
- **Where the check goes:** in `handleSummarize`, **only when `msg.auto === true`**:
  - after the `cache.has` early return and after the token check;
  - immediately before the `fetch`.
  - So a cache hit in the worker, or a missing token, costs nothing.
  - Free probes aren't counted.
  - A probe that times out and then hits the server's cache *is* counted.
  - Runs that fail with the server down are counted too, **on purpose**: that also caps error toasts (Data, rev 3).
- **One chain** does the check and the record. Each step has a `.catch`. If storage fails, it **fails closed** and pauses.
- **Over budget:** it replies `{ type: "autoPaused", limit: "hour" | "day" }` and makes no fetch.
- **Manual runs** are never counted.

### 6. Options page: "Automatic summaries" (UX B3, Frontend S13)

- **Its own `<section>`** goes after the Save row and the sponsor line, with:
  - a top rule;
  - an `<h3>`;
  - the section note "Changes here apply immediately.";
  - its own status line: a persistent `role="status" aria-live="polite"` element that's never re-created (UX, rev 4).
- **Scoped styles** override the page's global `input`, `label` and `button` rules:
  - the label is inline: `<label><input type=checkbox> …</label>`;
  - Remove buttons are secondary (outlined).
- **Behaviour:**
  - The list renders whether the box is ticked or not, built with `createElement` and `textContent`.
  - Remove sends `channels:remove`, then moves focus to the next row's Remove button, or to the heading if the list is now empty, and announces "Removed {name}." (UX W3). The `<h3>` has `tabindex="-1"` so it can take that focus (UX, rev 4).
- **The code** lives in functions (`initAuto`, `onAutoToggle`, `renderChannels`) so tests can call them. `options.js` runs `load()` at import.
- **The existing Save handler** never writes `autoRun` or `autoChannels`.

### 7. Observability (DevOps S1)

- **The content script** logs with `console.info("[TL;DW auto] …")` once per video per decision: `run`, `skip:<reason>`, `timeout`, `paused (<limit>)`, `drained`, `dropped`, `stopped transcription`. It never logs per poll tick.
- **The worker** logs `autoPaused` (with the limit that was hit) and `register` / `unregister` / `register failed`.
- **The Setup note** says the run log (`autoRunLog`) can be inspected in the service worker's DevTools under Storage.
- **The server log can't tell auto-runs from clicks.** Labelling them (`trigger: "auto"`) is a server change, which is part of the Q5 follow-up.

### 8. Rollout and rollback (DevOps B1)

- **Rollout,** after the fix PR is merged:
  1. Pull.
  2. Reload the extension. Chrome doesn't prompt, because the new permission is optional.
  3. Reload open YouTube tabs.
  4. Firefox only: rerun `./build-firefox.sh`, since `dist-firefox/` is gitignored.
  - There's no server restart, because this PR has no server change.
  - **Old content scripts** in tabs that aren't reloaded lose their connection to the worker, so they can't send old messages to the new worker. That's Chrome's own behaviour when an extension reloads.
- **Rollback:**
  1. **First, untick "Summarize videos from chosen channels automatically"** while the new code is still loaded. That gives back the permission and unregisters `tldw-auto`.
  2. Revert.
  3. Reload the extension and the YouTube tabs.
  4. Leave the storage keys (`autoRun`, `autoChannels`, `autoRunLog`) alone. Old code ignores them, and its Save handler writes only named keys, so they survive if the feature comes back.
  - **What happens if step 1 is skipped** is checked once in the build review, on a scratch Chrome profile (DevOps B1). It's unverified until then.
- **Load:** up to 10 drains an hour can hold some of the server's 6 model slots, so a manual click just after a burst may wait briefly for a free slot. That's accepted.

### 9. Docs and accepted risks

- **Docs:**
  - `extension/README.md`:
    - Use;
    - a Setup note on the optional permission and Chrome's prompt;
    - the `autoRunLog` note;
    - the rollout and rollback steps from § 8, including rebuilding `dist-firefox` after a pull.
  - The root `README.md` feature list.
- **Token exposure (Architect W1)**, accepted:
  - With the feature on, content scripts run in every `www.youtube.com` tab and can read `storage.local`, which holds the token.
  - The threat is a compromised renderer.
  - The token authorizes only the loopback server on the owner's own machine.
  - Moving the token out of content scripts' reach is a follow-up.
- **The worker's in-memory `cache` Map** is unchanged. The Analyst cut the LRU cap.

## Acceptance criteria

1. **Off by default.**
   - A fresh install has `autoRun` false and registers nothing.
   - Required `host_permissions` are unchanged.
   - `optional_host_permissions` is exactly `[ORIGIN]` in both manifests.
2. **The checkbox:**
   - It calls `request` synchronously in `click`, with the box disabled and unchecked until the request resolves. The test asserts that `request` was recorded, and that `disabled` was true, **before the click dispatch returned** (SDET W6).
   - On grant it ends checked and saves `autoRun`; on refusal it ends unchecked with the refused line.
   - A rejection gives the unsupported line.
   - Turning it off removes the permission.
   - The revoked state shows unticked with its line.
   - It reacts to `permissions.onAdded` and `onRemoved` while open.
   - A missing API gives the unsupported line.
   - The Save handler never writes `autoRun` or `autoChannels`, and nothing in `options.js` ever calls `storage.set` with `autoChannels`.
3. **`syncAutoScript`:**
   - It registers if and only if both conditions hold, with `allFrames: false`.
   - Two syncs in flight leave one registration and throw nothing.
   - A register or unregister that rejects doesn't stop the next sync, and an unexpected register error is logged and leaves nothing registered.
   - `onStartup` actually runs a sync; it isn't only attached.
   - `onInstalled` unregisters, then syncs.
   - **All of `background.js`, run once with and once without `importScripts`,** attaches all five listeners plus `onConnect` and `onMessage`, and throws nothing (Backend B1).
4. **`channels.js`:**
   - Running it twice, and `channels.js` + `content.js` twice, in one context throws nothing (Frontend B1).
   - With `TLDW_CHANNELS` missing, `content.js` turns auto off and works as it does today.
   - It's named at every load point: the `executeScript` files, the registered script, `options.html`, the guarded `importScripts`, Firefox `background.scripts`, and `build-firefox.sh`.
   - Running it overwrites a page-defined `TLDW_CHANNELS`.
   - The `content.js` init guard requires `=== true`, and a page element named `__tldwInit` doesn't block init (moved here from AC14, SDET W5).
   - **The worker without `TLDW_CHANNELS`** (an `importScripts` that throws): it still attaches every listener, and a manual summarize works. `channels:add` replies `{ ok: false }`, an auto summarize gets `autoPaused` with no fetch, and `syncAutoScript` unregisters.
5. **`channelKey`, `isValidKey`, `channelFromPage`:**
   - Both link forms, NFC + lowercase, and query/hash are handled.
   - Foreign hosts, a malformed `%`, a short UC ID, `/c/` and `/user/` give `null`.
   - The round-trip rule holds.
   - `channelFromPage` gives `null` for a stale `video-id`, for an invalid `v`, and for a video with several channels (no single owner link).
   - The name is trimmed and capped at 100 characters.
6. **`autoRunDecision`:** one outcome per case: run / not listed / already handled / request active / panel open / already have this summary.
7. **End to end**, through the real `initAutoRun`, `scheduleAutoCheck` and `startSummarize`, with a fake clock and a fake YouTube DOM:
   - A listed channel's `/watch` page starts **one** quiet summarize with no click:
     - **on a full load**, including when settings load *after* init (Frontend B2);
     - **on `yt-navigate-finish`**.
   - Nothing starts for:
     - an unlisted channel;
     - an empty list;
     - `/shorts/`;
     - a URL other than `/watch`;
     - the 15 s timeout;
     - `v` changing during the poll.
   - Nothing starts if `v` changes, or the tab is hidden, before 3 s on the video.
   - Hiding the tab restarts the dwell.
   - A hidden tab starts only once it's visible.
   - Many navigations leave one live timer, with no stale steps.
   - A toolbar click during the poll or dwell cancels the check **and** adds the video to `autoHandled`. A later navigation back to it doesn't auto-run.
   - `onNavigated` clears `pendingOffer` when `v` changes.
   - Each decision is logged once (`[TL;DW auto] run` / `skip:<reason>`), not on every poll tick.
   - The auto-run's page read goes through the fix's `collectPageTranscript(videoId, isCurrent)`, so its page object carries `page_v: 2` (DevOps B2; the fix plan's rev 3 marker).
8. **Quiet `startSummarize`:**
   - It never mounts and is backgrounded from the start.
   - `progressPct` and `pendingOffer` are reset.
   - A cache hit or a result gives one `ready` toast, with the auto copy.
   - An error gives the prefixed error toast.
   - `whisper: "ask"` gives the "needs an answer" toast.
   - `auto: true` goes on summarize only, and never on the `transcribe: true` retry.
   - A message from a port that's no longer current is ignored, and so is its disconnect.
   - A re-attached (manual) run's error has no auto prefix.
9. **Leaving mid-run:**
   - **Before the summarize is sent,** the run is dropped with no toast and nothing sent.
   - **After it's sent,** it drains:
     - the drain port keeps pinging until a terminal message (`autoPaused` included), then disconnects;
     - two drains at once each keep and clear their own ping and cap;
     - the cap fires at 160 s and clears the ping;
     - no toast;
     - the next run's safety timer and ping belong to its own port;
     - a drained port disconnecting doesn't touch the new run.
   - **Leaving while transcribing,** or a drain receiving `transcribing`: `stopTranscribe` is sent **with the run's URL**, the port disconnects, and there's no toast.
   - A manual backgrounded run still shows its toast after navigation.
   - Clicking the toolbar **during** an auto-run re-attaches: no second request, progress shown, and the run is manual from then on (`autoRunVid` cleared).
   - Clicking **after** it shows `lastPayload` with no request.
10. **Budget:**
    - `budgetDecision` gives allowed / hour / day at the boundaries (the 10th and 11th runs in an hour; the 40th and 41st in a day).
    - `sanitizeRunLog` drops non-arrays, non-finite entries and future entries (the clock-back case).
    - Only `auto === true` counts (`"true"` and `1` are manual).
    - One run records one entry; a worker-cache hit or a missing token records none.
    - Two in flight at 9 leave one allowed: the fake storage holds the first `get` open so the two really interleave (SDET W7).
    - Every write prunes the log to 24 h.
    - A storage failure fails closed.
    - `autoPaused` makes no fetch, and the content script shows the `notice` toast once per page, naming the limit that was hit.
11. **Toast:**
    - `notice` has no suffix and no click action.
    - `offer` opens `pendingOffer`, and `ready` opens `lastPayload` even when a stale `pendingOffer` exists.
    - Every `endBackground` call site passes the kind in the table.
    - It mounts in the fullscreen element when there is one.
    - An untrusted click does nothing.
    - `ready` opens with the right cached flag.
12. **Panel row:**
    - Hidden when the option is off or the channel is unknown.
    - It's built with `createElement` and `textContent`. `autoRowText` gives the not-listed, listed and `title` strings. A channel named `<img src=x onerror=…>` comes out as text, with no element created, the `title` included.
    - The row re-renders on `storage.onChanged` while the panel is open (for example, the channel was added in another tab).
    - The status node survives the flip.
    - Disabled while an add is in flight.
    - `ok`, `exists`, `full` and a failed add are each announced. A rejected `sendMessage`, because the worker died, is announced as failed.
    - Focus moves to Manage after the add, and stays on the re-enabled button after a failed add.
    - Manage sends `openOptions`.
    - The shadow-root guard stops an untrusted `.click()` on the add, Manage and accept buttons, while a trusted click works.
13. **Single writer:**
    - Add and remove are serialized: an add and a remove at the same time leave the right list.
    - A step that rejects doesn't jam the chain.
    - An invalid key is rejected, the name is capped, and adds stop at 500.
    - A foreign `sender.id` and unknown message types are ignored.
    - `sendResponse` is used (the listener isn't async).
    - `sanitizeChannels` covers every field rule.
14. **Options list:**
    - It renders.
    - The status element is persistent and the heading takes focus (`tabindex="-1"`).
    - Remove sends `channels:remove` for that key only, moves focus as specified, and announces the removal.
    - The empty state appears when the list is empty.
    - A channel name containing markup renders as text.
    - It re-renders on `storage.onChanged`.
15. **Coverage:**
    - **Where the tests go** (SDET):
      - **`tests/test_extension_render.py`** (Python, reading files): the manifests and the load points.
      - **`tests/js/channels_checks.js`:** everything that's pure.
      - **`tests/js/worker_checks.js`:** all of `background.js`, run with and without `importScripts`, against a fake `api` that records listeners. Its fake `scripting` reflects what's registered and can be delayed, and `Date` and storage are injected.
      - **`tests/js/auto_run_checks.js`,** with:
        - a fake clock (`advance(ms)` and a count of pending timers);
        - a fake `document` that dispatches events and has settable visibility, `location` and fullscreen;
        - fake ports that record pings.
      - **`tests/js/options_checks.js`:** all of `options.js`, with a fake DOM, fetch and permissions.
    - **The shared `tests/js/fakedom.js`** is held to:
      - an element tree, attributes, `textContent`, `hidden`, `disabled`, `checked`, `focus` and `activeElement`;
      - `createElement`;
      - `addEventListener` with capture then bubble, `stopImmediatePropagation` and `isTrusted`.
      - It has **no HTML parser.** Setting `innerHTML` on a fake element throws, so any markup path fails loudly (SDET).
    - **Counting checks** (SDET B2): each JS script ends by printing `ok <n> checks`. Its pytest wrapper asserts `n >=` a floor that is recorded in the wrapper and in the PR, so deleting checks fails the suite. The suite passes 675 tests today, and each script adds only one test to that count.
    - **Mutations:** each mutation in the list below must fail a test. The SDET runs them on a scratch copy in the build review, and the results go in the PR.
    - The full suite passes locally. There is no CI, so the result is recorded in the PR.
16. **Docs** as in § 9.

## The mutation list (SDET, Round 3)

Each of these must fail a test:

1. The `isTrusted` guard on the shadow root is removed.
2. The toast's `isTrusted` check is removed.
3. `port === myPort` is dropped from `onMessage`.
4. `port === myPort` is dropped from `onDisconnect`.
5. The drain calls `clearTimers()` after the move, not first.
6. `drains` becomes a single variable instead of a `Set`.
7. The drain's ping or cap uses the global `port`.
8. The drain leaves its ping running when the cap fires.
9. `autoPaused` isn't terminal for a drain.
10. Leaving while `txLive` is set drains instead of calling `stopTranscribe`.
11. A drain that receives `transcribing` keeps draining.
12. A run that was left before its summarize went out still posts.
13. The budget check moves before `cache.has`.
14. The budget check moves before the token check.
15. `msg.auto === true` becomes a truthy check.
16. `sanitizeRunLog` keeps future entries.
17. `sanitizeRunLog` keeps non-finite entries.
18. The budget chain loses its `.catch`.
19. A storage failure fails open.
20. The hourly limit becomes `<= 10`.
21. The daily limit becomes `<= 40`.
22. The log isn't pruned on write.
23. `content.js` doesn't fail closed when `TLDW_CHANNELS` is missing.
24. The first check doesn't wait for `settingsReady`.
25. The `gen === autoGen` check is removed.
26. `clearTimeout(autoTimer)` is removed.
27. The dwell doesn't restart after the tab is hidden.
28. The visible-only check is removed.
29. The poll doesn't give up when `v` changes.
30. The 15 s timeout is removed.
31. `autoHandled.add` is removed.
32. Any one of `autoRunDecision`'s five skips is dropped (each separately).
33. The init guard becomes a truthy check.
34. `channels.js` gets a top-level `const`, or uses `??=`.
35. `importScripts` is called unguarded.
36. `allFrames` becomes true.
37. The `sender.id` check is removed.
38. `isValidKey` is skipped on add.
39. The 500 cap evicts instead of refusing.
40. The name cap is removed.
41. The `onMessage` listener becomes `async`.
42. The channel-list chain loses its `.catch`.
43. Sync registers without comparing against `getRegisteredContentScripts`.
44. `onInstalled` syncs without unregistering first.
45. `request` is called after an `await`.
46. The Save handler writes `autoRun`.
47. `ready` opens `pendingOffer`.
48. `notice` keeps the "click to read" suffix.
49. The toast mounts on `body` in fullscreen.
50. `showResult(lastPayload, true)` comes back.
51. The auto prefix comes from global state instead of the run's own closure.
52. Re-attaching doesn't clear `autoRunVid`.
53. `auto: true` is sent on the `transcribe: true` retry.
54. `startSummarize` stops resetting `pendingOffer` or `progressPct`.
55. `channelKey` skips the host check.
56. `channelKey` skips NFC or lowercasing.
57. `channelKey` accepts a short UC ID.
58. `channelFromPage` ignores `video-id`.
59. The panel row sets the name with `innerHTML` instead of `textContent`.
60. The `title` is set from something other than `autoRowText`'s string, or left unset.
61. The worker throws when `TLDW_CHANNELS` is missing, instead of failing closed.
62. A toolbar click during the poll or dwell doesn't add the video to `autoHandled`.
63. `onNavigated` doesn't clear `pendingOffer`.
64. A decision is logged on every poll tick, or not at all.
65. `syncChain.then(doSync)` loses its rejection handler.
66. `onStartup` is attached but doesn't sync.
67. `renderAutoRow` isn't called on `storage.onChanged`.
68. A rejected `sendMessage` leaves the button disabled, or isn't announced.
69. `stopTranscribe` is sent without the run's URL.

## Out of scope (Analyst)

- Auto-running Listen, key moments or questions.
- Matching on anything but the channel.
- Removing a channel from the panel.
- `storage.sync`.
- `m.youtube.com`, `music.youtube.com` and embeds.
- Legacy `/c/` and `/user/` links.
- Any server change.
- A tailored error-toast policy (Q2).
- Alias matching.
- An LRU cap on the worker cache.
- **Follow-ups, flagged to the owner:**
  - **Usage-stat provenance (Q5).** The Data Engineer dissents and wants it as the first follow-up.
  - **A toolbar badge on a ready auto summary.** UX: the durable signal for a missed toast.
  - **A keyboard route from the toast.** The toast `div` can't be focused. Today the toolbar click is the keyboard route.
  - **Moving the token out of content scripts' reach.**

## Seven lenses

1. **CEO:** low risk. It's opt-in and off by default, and there's no new install warning.
2. **Purchasing:**
   - The permission is optional, limited to one origin, and revocable. No new data leaves the machine.
   - Cost (two model calls per auto-run, because the server prepares key moments after every summary) is bounded by:
     - listed channels only;
     - the visible tab only;
     - a 3 s dwell;
     - once per video per page;
     - the cache checked first;
     - never on top of another run;
     - a cap the worker enforces: 10 an hour and 40 a day.
3. **Product/PM:** one PR on today's background path, after one prerequisite fix. Round 1 cut scope (`/c/` and `/user/`, the Grant button, the toast policy, aliases, the LRU cap). Round 2 added only robustness: the drain, the toast fixes, the trust guard.
4. **Daily user:**
   - It saves a click on the channels you always summarize, and it's offered at the moment of use.
   - It works in fullscreen.
   - A missed toast is recovered by clicking the toolbar. A badge is a follow-up.
   - Risk: an error toast per video while the server is down. That's bounded by the budget and marked "Automatic summary failed".
5. **Developer:** no surveillance. The list stays local, and the server sees only the URL.
6. **Investor:** "summaries are waiting" is proactive value.
7. **Marketing:** "Pick your channels; TL;DW has the summary waiting." The hint explains the permission prompt.

**Conflicts, resolved:**
- **Visible-only:** the Investor and Daily-user lenses would rather run hidden tabs too. Purchasing, Developer and Architect B2 would rather bound cost. The plan goes visible-only and the owner confirms Q3. That means videos middle-clicked out of a feed are summarized when you switch to them, not before.
- **Q5:** the Analyst's scope ruling wins over the Data Engineer; the owner decides on a follow-up.
- **Frontend S11, one trust guard for the whole panel:** wider than Architect W3, which asked only for the new controls. The Architect wins on security, and the wider guard is simpler and covers more, so it's adopted.

## Questions for the owner (at approval)

- **Q3:** visible tabs only. Background-tab videos are summarized when you switch to them.
- **Q5:** tag auto-runs in the usage stats as a follow-up?
- **Budget:** 10 an hour, 40 a day. OK?
- **Follow-ups:** a toolbar badge; moving the token out of content scripts' reach.

## Review log

- **Round 1 (rev 1):**
  - **Analyst: PASS WITH ITEMS.**
  - **Architect: PASS WITH ITEMS.** W2: stale transcript rows. Measured, real, and moved to the fix PR.
  - **Data Engineer: BLOCK.** Cache poisoning, moved to the fix PR. Single writer.
  - Addressed in rev 2.
- **Round 1 confirmation (rev 2):**
  - **Analyst: PASS.** Cut aliases and the LRU cap; reword the dwell AC.
  - **Architect: PASS WITH ITEMS.** `onInstalled` order; double counting; the budget race; `isTrusted` on toast/accept; strict `auto`.
  - **Data: PASS WITH ITEMS.** Double counting; `storage.local` log; `sanitizeChannels` fields; the round-trip key.
  - Addressed in rev 3.
  - **Data on rev 3: PASS.** Sanitize `autoRunLog` (clock-back); a failed run counts on purpose. Addressed in rev 4.
- **Round 2 (rev 3):**
  - **Backend: PASS WITH ITEMS.**
    - B1: Firefox has no `importScripts`.
    - B2: dropping a run mid-summarize lets the worker die, so nothing is cached. Fixed with the drain.
    - W1–W6: the `autoPaused` branch, `progressPct`, `pendingOffer`, where the budget check goes, `onMessage` replies, re-attach makes the run manual.
    - S1–S4.
  - **Frontend: PASS WITH ITEMS.**
    - B1: top-level `const` in a re-injected `channels.js`. Fixed with the IIFE.
    - B2: init runs before settings load. Fixed with `settingsReady`.
    - W3–W9: navigation detection, the timer and generation structure, re-attach, the toast `notice` kind, a stable live region, the checkbox sequence, Manage as a button.
    - S10–S14.
  - **UX: PASS WITH ITEMS.**
    - B1: the paused toast offered "click to read".
    - B2: the toast was invisible in fullscreen.
    - B3: the options CSS.
    - W1–W9: focus handling; Manage as a button; "Removed" announced; badge as a follow-up; distinct auto copy; a shorter hint; "(cached)" on fresh results; long names; preparing for Chrome's prompt.
    - The final copy table.
  - **All addressed in rev 4.**
- **Round 2 confirmation (rev 4):**
  - **UX: PASS.**
    - "Changes here apply immediately." and the "failed" copy are accepted.
    - Items: a persistent options status, `tabindex="-1"` on the heading, and focus after a failed add.
    - A follow-up: no keyboard route from the toast.
  - **Backend: PASS WITH ITEMS.**
    - Drop (and `stopTranscribe`) a transcribing auto-run rather than drain it; keep the 160 s cap; the `whisper: "always"` edge case.
    - `onDisconnect` checks `port === myPort`; drains go in a collection; ACs for the transcribing case.
  - **Frontend: PASS WITH ITEMS.**
    - The drain clears the old timers first, keeps a `Set` of drains, guards `onDisconnect`, and treats `autoPaused` as terminal.
    - The toast `offer` kind, with every call site migrated.
    - The auto prefix is chosen from the run's own closure.
  - **All addressed in rev 5.** The `offer` toast wording is new: UX owns wording, so it's confirmed at the build review.
- **Round 3 (rev 5):**
  - **SDET: BLOCK.**
    - B1: the panel row has to be built with `createElement`, because the fake DOM has no parser.
    - B2: the test count doesn't reflect the JS checks; the fix is "ok n checks" with a floor.
    - B3: 13 untested paths.
    - W4–W7.
    - The 60-item mutation list.
  - **DevOps: PASS WITH ITEMS.**
    - B1: a rollback section (untick first), plus a one-time check on a scratch profile.
    - B2: `page_v` against `synced`. **This came from reading the fix plan's rev 2.** The fix plan's rev 3 renamed the marker to `page_v: 2` (Backend against Data, resolved there), so the two plans agree. An AC now requires auto-runs to carry it.
    - W1: the worker's import is guarded and fails closed.
    - W2: other register errors are logged.
    - W3: rebuild `dist-firefox`.
    - S1: `[TL;DW auto]` logs.
    - The note on slots taken by drains.
  - **SDET W4** (the same `page_v`/`synced` mismatch) is answered the same way.
  - **All addressed in rev 6.**
- **Round 3 confirmation (rev 6):**
  - **SDET: PASS WITH ITEMS.** Items: `innerHTML` throws in `fakedom`; mutation 60 reworded; mutations 65–69 added. In the fix plan: AC8's wording, and a check that the VTT branch is gone. All applied.
  - **DevOps: PASS.** One item remains for the build review: on a scratch Chrome profile, record what happens when the untick step is skipped on rollback. If `tldw-auto` survives the revert, add a manual-cleanup line to the README.

## Sign-offs (plan)

- [x] Analyst: PASS (rev 2)
- [x] Architect: PASS WITH ITEMS (rev 2), items applied in rev 3
- [x] Data Engineer: PASS (rev 3)
- [x] Backend: PASS WITH ITEMS (rev 4), items applied in rev 5
- [x] Frontend: PASS WITH ITEMS (rev 4), items applied in rev 5
- [x] UX: PASS (rev 4)
- [x] SDET: PASS WITH ITEMS (rev 6), items applied
- [x] DevOps: PASS (rev 6)
- [x] Seven lenses: evaluated; the conflicts are resolved above
- [x] **Owner approval:** approved by the owner (Drew Curley) on 2026-11-14, as "Approve both".
  - **Q3:** visible tab only.
  - **Budget:** 10 an hour, 40 a day.
  - **Follow-ups to file after both PRs:** tag auto-runs in the usage stats (Q5), and a toolbar badge when a summary is ready. Not filed: moving the token out of reach, and a keyboard route from the toast.

## Build review (2026-11-14)

**Status: PASS.** All 8 agents reviewed the built code over three rounds, every item was applied or recorded, and every agent signed off.

### Round 1: Analysis
- **Analyst: PASS WITH ITEMS.**
  - AC1–AC16 are all delivered, nothing went beyond the plan, and the out-of-scope list held.
  - Two new strings, "Off." and "Couldn't remove {name}. Try again.", were ruled in scope (UX approved the wording).
  - Applied: a test for "Off."; the root README now says to turn the option on first; the toolbar re-open uses `lastCached`, so a fresh auto result isn't labelled "(cached)" (tested).
- **Architect: PASS WITH ITEMS.**
  - Applied: `onKey` ignores untrusted keys. Without that, a page script could type into the open shadow root and press Enter, sending an unbudgeted question. This predates the branch; it's fixed and tested.
  - Recorded: a compromised renderer can write `storage.local` (`autoRunLog`, `autoChannels`). That's the risk already accepted as Architect W1, not something the page's own scripts can do.
- **Data Engineer: PASS WITH ITEMS.**
  - Applied: tests for an encoded slash or space in a handle; an `href` over 300 characters dropped on the add path; `channelFromPage` only offers keys that pass `isValidKey`; a code comment that writes are sanitized, so a downgrade drops key forms it doesn't know.

### Round 2: Implementation
- **Backend: PASS** after its warning was fixed.
  - The warning: a run re-attached before its summarize went out was still sent as `auto`. It now uses `run.auto`.
  - Also applied: an `autoPaused` reply while you're watching shows in the panel; an empty name is refused.
  - Recorded: a drained captioned summary under `whisper: "always"` that runs past 160 s is cut off and not cached. That's the trade-off agreed in rev 4.
- **Frontend: PASS** after its items were fixed.
  - The row is no longer rebuilt for an unchanged key and listed state, and it refocuses after a real rebuild.
  - Applied: a note that the auto-check steps must stay synchronous; the auto prefix on the transcription timeout; Remove is disabled while its request is in flight.
  - It confirmed that removing `autoGen` is sound.
  - The fake DOM's `focus()` was made browser-strict at its request, so the focus tests mean something.
- **UX: PASS** after its blocker was fixed.
  - The blocker: a late storage update rebuilt the row and dropped focus from Manage.
  - Also applied: the revoked line clears when access returns; an unrequestable permission hides only the switch, so the list stays editable.

### Round 3: Verification
- **SDET: PASS WITH ITEMS, all applied:**
  - a pattern error now fails the mutation run;
  - the fake DOM focuses only what a browser can focus;
  - `test_the_options_test_page_matches_options_html` pins the IDs and attributes that the options test's hand-built copy of the page relies on;
  - a mutation that removes the heading's `tabindex`.
- **DevOps: PASS WITH ITEMS, all applied:**
  - The toolbar retries with `content.js` alone if injecting `channels.js` fails, so manual summaries never depend on the new file (tested).
  - Version 1.4.0, so `chrome://extensions` shows which build is loaded.
  - The README rollback has a fallback for a skipped untick.
  - Recorded as unverified: what happens on rollback if the untick is skipped. It can't be driven here, and the worst case judged harmless.

### Mutations
The SDET's list, run on a scratch copy against a passing baseline.
- **Dropped as unreachable:**
  - #17: `Number.isFinite` in `sanitizeRunLog`. The comparisons already reject NaN and ±Infinity.
  - #25: `autoGen`. Clearing the single `autoTimer` already retires a check; Frontend and SDET agreed.
- **Written stronger:** #22 and #65.
- **Added for guards introduced in review:** #70 to #86.
- **Final run: 91 of 91 killed**, with 0 survivors and 0 pattern errors.

### Suite
- **708 passed, 0 failed, 0 skipped**, run locally because the repo has no CI. Before this change: 695.
- **Node checks, with the minimum each enforces:** `channels_checks` 81, `worker_checks` 121, `options_checks` 64, `auto_run_checks` 187, `page_transcript_checks` 68, `probe_checks` 41.

### Follow-ups
- **Owner-approved, to be filed:**
  - tag auto-runs in the usage stats (Q5);
  - a toolbar badge when an auto summary is ready.
- **Recorded:**
  - a keyboard route from the toast;
  - moving the token out of content scripts' reach;
  - confirming Firefox 115 support for `optional_host_permissions` with `registerContentScripts`;
  - the rollback check with the untick skipped.

### Sign-offs (build)
- [x] Analyst
- [x] Architect
- [x] Data Engineer
- [x] Backend
- [x] Frontend
- [x] UX
- [x] SDET
- [x] DevOps
