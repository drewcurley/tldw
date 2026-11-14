# PLAN — a toolbar badge when an automatic summary is ready

**Status: PASS.** Plan rev 5 was approved by the owner on 2026-11-14 and has been built and reviewed; see "Build review". Rounds 1, 2 and 3 are done; rev 5 folds in Round 3.

- **Branch:** `feat/toolbar-badge`.
- **Issue:** drewcurley/tldw#21, a follow-up to #19 that the owner approved (UX W4).
- **Review scope:** a new feature, so the full cycle: all 8 agents and the 7 lenses. The owner approves the reviewed plan before any code.

## The problem

When an automatic summary is ready, the only signal is a notice that disappears after 20 seconds (`toast(…, "ready")`, `content.js` `endBackground`). Miss it, because you looked away or switched tabs, and nothing says a summary is waiting. Clicking the toolbar button does show it instantly, from `lastPayload`, but only if you already know to click.

## The change (rev 2)

1. **What it looks like.**
   - The toolbar button gets a per-tab badge, `✓` on green, and a per-tab tooltip: "TL;DW — summary ready for this video (click to read)".
   - Both are set with `tabId`, so other tabs are untouched.
2. **When it's set** (Analyst B2).
   - The badge is set only when a result arrives while the panel is closed **and the run is still automatic** (`autoWording()` is true). That's the place the "TL;DW summarized this video for you" notice fires, and it includes a cache hit served to an automatic run.
   - A run that a toolbar click made manual (`content.js:2414-2415`) never badges.
   - The content script records `badgeVid = videoId` and sends `{ type: "badge:set" }`. **No video ID is sent**: the worker has no use for it (Architect W1).
3. **When it's cleared.** The content script sends `{ type: "badge:clear" }`. It also sets `badgeVid = null` on the first two triggers below, but not on `pagehide` (Data, rev 2):
   - **When that summary is shown:** `showResult` for `badgeVid` (from the notice, the toolbar, or any re-open).
   - **When the page stops being that video:** in `onNavigated`, if `badgeVid` is set and `urlVideoId() !== badgeVid`. That includes in-page navigation to a page that isn't a video, where the ID is `""` (Data W1).
   - **When the page goes away:** on `pagehide`, if `badgeVid` is set. Chrome documents a per-tab reset only when the tab *closes*, not when it navigates. Without this, a tab that left YouTube for another site would keep a stale badge, and the content script never runs there (Architect W2).
     - It's best effort: a message sent while the page unloads may not arrive.
     - `badgeVid` is **kept**, so a back/forward-cache restore can set the badge again.
     - **A known gap,** recorded in the PR: if this clear never arrives on a tab that has left YouTube, the badge stays until the tab closes. The content script doesn't run on the other site.
     - Both handlers act only on `e.isTrusted` (Architect, rev 2), so the page can't fire a fake `pageshow` to set the badge again.
     - If the page is restored from the back/forward cache (`pageshow` with `persisted`) and `badgeVid` is still set, the badge is set again (Data W1).
   - **Once at start,** on a full page load, as a backstop for a `pagehide` clear that didn't arrive. This is the only send when `badgeVid` is unset (Data S1, B3).
4. **The worker keeps no state** (Data B1).
   - Rev 1's Set of badged tabs, and "clear them all when `autoRun` turns off", are **removed**. MV3 stops the worker after about 30 s idle, so the Set would be lost in normal use. The Analyst also ruled the clear out of scope: a badge left after turning the option off is still true, because `lastPayload` still opens with a toolbar click.
   - **Which tab:** the handler takes the tab only from `sender.tab.id`. It needs `sender.id === runtime.id`, a `sender.tab`, and `sender.frameId === 0` (Architect W1, S1). It ignores every field of the message except `type`.
   - **Set:** it calls `setBadgeText({ tabId, text: "✓" })`, `setBadgeBackgroundColor`, `setBadgeTextColor` (only if it exists; it's in Chrome 110+ and Firefox) and `setTitle`.
   - **Clear:** it sets the text to `""` and the title to `api.runtime.getManifest().action.default_title` (Architect S2).
   - Each call is wrapped, so a tab that has closed can't throw out of the listener.
   - The handler returns `undefined` and sends no reply.
5. **The content script's sends** are wrapped in `try/catch` plus `.catch` (Architect W3). After an extension reload, `sendMessage` throws synchronously; with no reply, the promise rejects.
6. **No new permissions.** `action.*` needs none, and `sender.tab` and `frameId` are given to content scripts' messages.
7. **Docs:** `extension/README.md` (the automatic-summaries section) and the root README bullet.

## Rev 4: Round 2's decisions (these take precedence where the sections above differ)

- **Look** (UX: B1 closed):
  - **Badge:** `"✓"` on `#137333` with white text (`#FFFFFF`).
    - White text on it is about 6.0:1.
    - The badge against a dark toolbar is about 3.1:1, which meets the 3:1 minimum.
    - It isn't a dot: the `✓` shape carries the meaning for red-green colour-blind users.
  - **Tooltip when set:** "TL;DW summarized this video for you — click to read", the same words as the notice. The title is the only thing a screen reader announces, so it changes whenever the badge does.
  - **Tooltip when cleared:** the manifest's `default_title`.
- **Worker** (Backend):
  - The tab and frame checks apply **only inside the `badge:*` branch**. The shared guard stays `sender.id`-only, because `options.js` sends `channels:remove` from an extension page, which has no tab.
  - It requires `Number.isInteger(sender.tab.id) && sender.tab.id >= 0` (Chrome uses -1 for "no tab") and `frameId === 0`.
  - It checks that `setBadgeTextColor` is a function before calling it.
  - Each call is wrapped as `try { Promise.resolve(call).catch(noop) } catch {}`. Chrome throws synchronously on a bad argument and rejects for a closed tab.
  - The branch goes after `openOptions`, never calls `sendResponse`, and returns `undefined`. A test confirms `channels:add` still replies.
- **Content** (Frontend):
  - **The clear on viewing** lives in a helper, `badgeShown(p)`, placed in a range the tests already slice (near `endBackground`). `showResult` calls it first, and a source check confirms that.
  - **What it compares:** `p.video_id`, the field `lastPayload` carries.
  - **Where the badge is set:** `badgeVid = videoId` and `badge:set` happen only inside `if (backgrounded)` and only when the run is automatic, in the result branch.
  - **`pagehide`** clears whether or not `persisted` is set, and keeps `badgeVid`.
  - **`pageshow`** sets the badge again only when `persisted` is set, since it also fires on the first load with `persisted` false.
  - Both act only on `isTrusted`.
  - The listeners and the start clear are registered in `initAutoRun`, after `if (!CH) return`, once each.
  - The start clear also fires on a toolbar-click injection. That's harmless, and the "exactly once" test covers it.
- **B3 answered:** one message per full YouTube page load while the feature is on is acceptable (Backend).

## Rev 5: Round 3's decisions

- **Version:** 1.5.0 in both manifests, so `chrome://extensions` shows which build is loaded. A check confirms both manifests carry the same `action.default_title`.
- **Observability:** setting the badge logs once per video through `logAuto(videoId, "badge")`, the existing `[TL;DW auto]` line. Clears aren't logged, since one goes out on every page load, and the worker doesn't log.
- **Rollout:**
  1. Reload the extension and your YouTube tabs. An old content script's sends throw after a reload, which AC4 covers.
  2. Firefox: re-run `build-firefox.sh`; the local `dist-firefox/` is stale.
- **Rollback:** revert and reload. There's no stored state, and a leftover badge ends when the tab closes or the page reloads.
- **Test plan** (SDET):
  - **`auto_run_checks.js`:**
    - `badgeVid` added to the mirrored state;
    - a fake `window` event target (`fire(type, { persisted, trusted })`);
    - the `showResult` stub calls `badgeShown(p)`, plus a source check that the real `showResult` starts with `badgeShown(p)`;
    - the whole-file loads count the start clear (exactly once on a toolbar injection, and none on a second injection);
    - a forced `backgrounded = false` case for the open-panel gate;
    - send failures, both a synchronous throw and a rejection, with `unhandledRejection` counted as a failure.
  - **`worker_checks.js`:**
    - a fake `chrome.action` whose four setters record calls and can throw, reject, or have `setBadgeTextColor` missing;
    - `getManifest()` returns a sentinel title;
    - sender variants (tab, `frameId`, -1, 1.5, a `tabId` in the message);
    - a check that `channels:remove` from a sender with no tab still works.
  - **Minimums** in `test_extension_render.py` are raised to the new counts.
- **Mutations:** each must fail a test. The list is in the next section.

### Mutation list (SDET, Round 3)

**content.js:**
- c1. Set without the `autoWording()` gate.
- c2. Set outside `if (backgrounded)`.
- c3. Set on an error, on `needsTranscription`, or after a drain (each separately).
- c4. Don't record `badgeVid`.
- c5. Don't replace `badgeVid` on a second result.
- c6. `badgeShown` compares `p.title`.
- c7. `badgeShown` drops the `badgeVid` check.
- c8. `badgeShown` doesn't null `badgeVid`.
- c9. `onNavigated` doesn't null `badgeVid`.
- c10. `onNavigated` uses `v && v !== badgeVid`.
- c11. `pagehide` nulls `badgeVid`.
- c12. `pageshow` drops the `persisted` check.
- c13. `pagehide` requires `persisted`.
- c14. `pagehide` drops `isTrusted`.
- c15. `pageshow` drops `isTrusted`.
- c16. The start clear is removed.
- c17. The start clear is sent twice.
- c18. The listeners are registered twice.
- c19. A send goes out while `badgeVid` is null (`showResult`, `onNavigated`, `pagehide`).
- c20. The send loses its `try`.
- c21. The send loses its `.catch`.
- c22. Registration moves above `if (!CH) return`.
- c23. `showResult` doesn't call `badgeShown`.

**background.js:**
- w1. Drop `frameId === 0`.
- w2. Drop the `sender.tab` check.
- w3. Drop `Number.isInteger`.
- w4. Use `>= -1`.
- w5. Use `msg.tabId`.
- w6. The tab check is hoisted into the shared guard.
- w7. Drop the `typeof setBadgeTextColor` check.
- w8. Remove the `try` or the `.catch` on each call.
- w9. Return `true`, or call `sendResponse`.
- w10. Hard-code the cleared title.
- w11. Clear with `"✓"`, or set with `""`.
- w12. Change the colours.
- w13. Drop the `sender.id` check for `badge:*`.

## Acceptance criteria (rev 2, amended by revs 4 and 5 above)

1. **Set.**
   - An automatic run's `result` while backgrounded sends `badge:set` exactly once and records `badgeVid`.
   - So does a cache hit served to an automatic run.
   - A second automatic result, for another video after navigating, replaces `badgeVid`.
2. **Not set.** None of these send `badge:set`:
   - a manual backgrounded result;
   - a run re-attached by a toolbar click, even if it's backgrounded again;
   - a result shown in an open panel;
   - an error, including the transcription safety timeout and a lost worker connection;
   - `needsTranscription`;
   - `autoPaused`;
   - a drained or dropped run.
3. **Cleared.**
   - `badge:clear` is sent and `badgeVid` nulled:
     - by `showResult` for `badgeVid`, from the notice and from `TLDW_INVOKE`;
     - by `onNavigated` when `urlVideoId() !== badgeVid`, including `""`.
   - `pagehide` with `badgeVid` set sends `badge:clear` and keeps `badgeVid`.
   - `pagehide` then `pageshow` (both `persisted`) sends `badge:set` again.
   - Untrusted `pagehide` and `pageshow` events do nothing.
   - At start, `badge:clear` is sent exactly once.
   - **Nothing is sent when `badgeVid` is null:** not by `showResult`, `onNavigated` or `pagehide`.
   - `showResult` for a *different* video doesn't clear.
4. **Sends never throw:** not a synchronous throw from `sendMessage`, and not a rejected promise.
5. **The worker.**
   - `badge:set` from a top-frame content script calls the four APIs, each with `sender.tab.id`. With `setBadgeTextColor` missing, the other three are still called.
   - `badge:clear` sets the text to `""` and the title to the manifest's `default_title`.
   - These are ignored, with no API call:
     - no `sender.tab`;
     - `frameId` not 0;
     - a foreign `sender.id`.
   - **A `tabId` in the message is never used:** the message is still handled, using `sender.tab.id` (Analyst, rev 2).
   - A rejecting API doesn't throw.
   - The listener returns `undefined` for `badge:*` and stays synchronous.
6. **Coverage.**
   - Every guard has a mutation-checked test.
   - `worker_checks.js` and `auto_run_checks.js` are extended, and their minimums raised.
   - The full suite passes locally; the repo has no CI.
   - **Not checked by machine, recorded in the PR:** how Chrome and Firefox actually reset per-tab badges on navigation (Analyst S1, Architect W2). The design no longer depends on it.
7. **Docs** as in item 7 of "The change".

## Out of scope

- A badge for manual runs, errors or transcription offers.
- A count across tabs.
- Clearing the badge from the worker side (`tabs.onUpdated`, `tabs.query`). That would wake the worker on every tab load in the browser. The content script's clears (on `pagehide`, at start, on navigation) cover it.
- Clearing badges when `autoRun` is turned off (Analyst; Data B1). A badge left behind is still true.
- A keyboard route from the notice, which is a separate recorded follow-up.

## Seven lenses

1. **CEO:** trivial risk. It's a cosmetic signal on an opt-in feature.
2. **Purchasing:** no new permission, no data, no cost.
3. **Product/PM:** small and contained, and the issue was approved by the owner.
4. **Daily user:** it fixes the "missed the notice" gap at a glance, with no homework. The risk is a stale badge; it's cleared on open, on navigation and on reload.
5. **Developer:** no surveillance, all local.
6. **Investor:** it polishes the "summaries are waiting" story.
7. **Marketing:** it demos well. There's no landmine.

**Conflicts:** none.

## Open questions for reviewers

- **B1 (UX).** Is `✓` on green the right badge? The alternatives are `1`, or a dot `●`. Which colour, and what about text contrast?
- **B2:** answered by the Analyst. Automatic runs only, and only while the run is still automatic.
- **B3 (Frontend/Backend).** Is the content script's clear at start (a message on every YouTube page load while the feature is on) acceptable for how often it wakes the worker?

## Review log

How it was reviewed: one fresh agent per round, playing that round's roles. Each role has its own brief and verdict, and the rounds run in order.

- **Round 1 (rev 1):**
  - **Analyst: PASS WITH ITEMS.**
    - B2: automatic runs only, keyed on `autoWording()`.
    - Dropping the clear-all when `autoRun` turns off.
    - W1: `badgeVid`. W2: more error cases in AC2. S1: the browser check is recorded.
  - **Architect: PASS WITH ITEMS.**
    - A page can't set or spoof the badge. No permission change.
    - W1: the tab comes only from `sender.tab.id`, and the video ID is ignored.
    - W2: Chrome documents a reset only on tab close; navigation is unverified.
    - W3: sends are wrapped.
    - S1: `frameId` 0. S2: the title comes from the manifest. S3: the APIs are available.
  - **Data Engineer: BLOCK.**
    - B1: the in-memory Set is lost when the MV3 worker stops, so the worker is now stateless.
    - W1: leaving the site, the back/forward cache, and `""` on pages that aren't videos are handled by `pagehide`/`pageshow` and `!== badgeVid`.
    - W2: a clear after a set is handled.
    - S1: no send when `badgeVid` is null.
  - **All addressed in rev 2.**
- **Round 1 confirmation (rev 2):**
  - **Analyst: PASS WITH ITEMS.** W1: AC5's wording about a `tabId` in the message.
  - **Architect: PASS.** S1: `isTrusted` on `pagehide`/`pageshow`.
  - **Data Engineer: PASS WITH ITEMS.**
    - W1: `pagehide` must keep `badgeVid`, or the `pageshow` re-set can never run.
    - S1: the lost-`pagehide` case is recorded as a known gap.
  - **All addressed in rev 3.**
- **Round 2 (rev 3):**
  - **Backend: PASS WITH ITEMS.**
    - The tab check goes only in the badge branch, with `tab.id >= 0`.
    - Wrapped calls; the branch is placed after `openOptions`.
    - A harness for the fake `api.action`.
    - B3 is acceptable.
  - **Frontend: PASS WITH ITEMS.**
    - `badgeShown(p)` goes in a sliced range, comparing `p.video_id`.
    - `pagehide` and `pageshow` are gated differently.
    - Registered in `initAutoRun`.
    - The start clear on a toolbar injection.
  - **UX: PASS WITH ITEMS.**
    - `✓` on `#137333` with white text.
    - The tooltip uses the notice's words.
    - Keep the title in step with the badge.
    - The stale gap is acceptable.
  - **All addressed in rev 4.**
- **Round 3 (rev 4):**
  - **SDET: PASS WITH ITEMS.** The harness work, the floors, the mutation list, and a manifest title check.
  - **DevOps: PASS WITH ITEMS.**
    - The version bump to 1.5.0.
    - Rebuilding the Firefox bundle.
    - Log once on set.
    - Known gaps for the PR: a lost `pagehide` clear; navigation resets not checked by machine; no CI.
  - **All addressed in rev 5. Plan review complete.**

## Sign-offs (plan)
- [x] Analyst
- [x] Architect
- [x] Data Engineer
- [x] Backend
- [x] Frontend
- [x] UX
- [x] SDET
- [x] DevOps
- [x] Seven lenses: no conflicts
- [x] **Owner approval:** approved by the owner (Drew Curley) on 2026-11-14.

## Build review (2026-11-14)

**Status: PASS.** All 8 roles reviewed the built code over three rounds, run in order with one agent per round, and every item was applied.

- **Round 1:**
  - **Analyst: PASS WITH ITEMS.** Applied: a "no badge" test for a run dropped before its summarize was sent, and an `extension/README.md` note that leaving YouTube clears the badge on a best-effort basis.
  - **Architect: PASS.**
  - **Data Engineer: PASS.**
- **Round 2:**
  - **Backend: PASS.**
  - **Frontend: PASS.**
  - **UX: PASS.**
  - The reviewer also broke 8 guards on its own scratch copy, and all 8 were caught.
- **Round 3:**
  - **SDET: PASS WITH ITEMS, applied.**
    - A test that kills c5.
    - An `unhandledRejection` hook in `worker_checks.js`.
    - w2 first survived because the `frameId` check masked it. It's killed by a new "`frameId` 0 but no tab" case.
  - **DevOps: PASS.**

**Mutations:** 43 of 43 killed (c1–c23 and w1–w13, some split into variants), with 0 pattern errors, from a passing baseline.

**Suite:** 709 passed, 0 failed, run locally; the repo has no CI. The node checks report `worker_checks` 139 and `auto_run_checks` 227.

**Known gaps, recorded:**
- A `pagehide` clear lost while the tab leaves YouTube leaves the badge until the tab closes.
- How Chrome and Firefox reset per-tab badges on navigation isn't checked by machine.

### Sign-offs (build)
- [x] Analyst
- [x] Architect
- [x] Data Engineer
- [x] Backend
- [x] Frontend
- [x] UX
- [x] SDET
- [x] DevOps
