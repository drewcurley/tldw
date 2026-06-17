# Plan — coherent, sentence-complete cuts

## Problem
Video TL;DW cuts land mid-phrase/mid-thought, losing context. Two causes:
1. Claude selects cue ranges that don't bound a complete sentence/thought.
2. The crossfade overlaps audio (`acrossfade=d=0.4s`), so the last word of clip A
   and first word of clip B are dissolved/clipped even when the text boundary is
   clean — both ends of every seam lose ~0.4s of speech.

The user accepts longer output in exchange for coherence.

## Fix (two parts)

### 1. Transcript-analysis selection (primary)
Rewrite the video-selection prompt so Claude must:
- begin each segment at the START of a complete sentence/thought and end at the END
  of one — never mid-sentence/mid-clause/mid-word;
- treat auto-captions as unpunctuated/lowercase and use meaning + grammar + natural
  pauses to find boundaries;
- include all sentences a key point spans (full, self-contained thought);
- prefer slightly longer, coherent ranges; choppy mid-thought cuts are unacceptable;
- pick first_cue = cue with the opening sentence's first word, last_cue = cue with
  the closing sentence's final word.
Schema unchanged (cue-index ranges), so all downstream logic is unaffected.

### 2. Boundary-protect padding (mechanical safety net)
The crossfade dissolves ~0.4s at each seam. Pad every selected span outward by
`BOUNDARY_PAD_MS` (default 600ms, > xfade) on BOTH ends, clamp to [0, duration],
then merge overlaps. This pushes the dissolve onto the natural pause/breath around
the sentence instead of onto words. `spans.pad_spans(spans, pad_ms, duration_ms)`.

Order in cli: select → spans_from_cue_ranges (merge) → pad_spans → enforce_max_length
→ prepend intro → merge. (Pad before max-length so the cap still bounds final length;
intro still protected as first span.)

## Why cue-granularity is enough (not word-level cutting)
YouTube auto-caption cues are short (2-5 words); selecting whole cue ranges at
sentence boundaries + padding keeps words intact at seams. Word-level sub-cue
cutting (using inline `<ts>` tags) is a larger change deferred unless still choppy.

## Config / scope
- `BOUNDARY_PAD_MS = 600` constant; optional `--pad SECONDS` later if needed.
- Output gets longer (intended). `{new_length}` is still the true probed length.
- Timestamp overlay still shows each segment's (now padded) source start.

## Risks for review
- Padding start earlier can include a few words of the previous sentence's tail
  (and trailing pad the next sentence's head). Is pad=600ms the right balance vs
  bleeding adjacent content? Should pad be asymmetric or snapped to cue gaps?
- Interaction with merge: heavy padding may merge originally-distinct segments
  (acceptable — fewer, longer clips = more coherent).
- Does padding undermine `--ratio`/`--max-length`? (cap applied after padding.)
- Is the prompt change sufficient for unpunctuated auto-captions, or is a
  sentence-segmentation pass / word-level cut warranted?

## Review resolutions (authoritative)
- **B1 — gap-aware padding (not blind).** `pad_spans(spans, cues, pad_ms, duration)`
  pads each side ONLY into real silence: head = min(pad, span.start − prev_cue.end),
  tail = min(pad, next_cue.start − span.end). Continuous speech → 0 pad (never
  dissolves over adjacent words). The prompt remains the primary fix; the natural
  pause at a true sentence end means the crossfade overlaps low-energy audio anyway.
- **B2 — display timestamp = original start.** Add `Span.label_ms`
  (`field(compare=False)`, so equality is unchanged); `display_start_ms` falls back
  to `start_ms`. `pad_spans` records the pre-pad start; `merge_spans` keeps the min
  original. `_extract_clip` shows `format_clock(span.display_start_ms)`.
- **W1/W2 noted:** captions stay time-aligned (computed from the same padded spans);
  the intro span (0..intro) is deliberately NOT padded; cap applied after padding.
- **S1 — defer word-level cutting.** Cuts are already cue-boundary (never mid-word);
  the defect is mid-sentence, fixed by prompt + gap padding. Word-level is a
  different (intra-sentence) concern, deferred.
- **S3 — behavioral test:** mock a Claude selection and assert select→pad→spans flow
  yields sentence-bounded, gap-padded spans with correct display labels.

## Tests
- `pad_spans`: expands both ends, clamps to [0,dur], merges overlaps, no-op at 0.
- prompt contains the sentence-boundary instructions.
- cli wires padding into the pipeline (order; intro still first; cap still bounds).
