# memory_demo.md — Persistent Preferences (R4)

InboxHero stores user preferences in `memory/preferences.json`. A preference
stated in one process survives a full exit and changes behavior in a later,
completely separate process — no framework, just JSON on disk.

## The preference used
`m015` (Priya): *"from now on, please make sure I'm CC'd on anything that comes
in from our lawyers at Hartwell & Cho … Applies to all of it."*

## Run 1 — state the preference, then exit
```
$ python demo.py --cap R4
==================== R4 ====================
RUN 1: stored preference from m015 -> 'CC Priya on anything from Hartwell & Cho (our lawyers).' in memory/preferences.json, then exiting.
```
The process exits. `memory/preferences.json` now contains:
```json
{
  "cc_legal_on_hartwell_cho": {
    "key": "cc_legal_on_hartwell_cho",
    "value": { "cc": "priya@paperjet.io", "domain": "hartwellcho.com" },
    "source_message_id": "m015",
    "stated": "CC Priya on anything from Hartwell & Cho (our lawyers).",
    "created_at": "run-1"
  }
}
```

## Run 2 — fresh process, behavior changes without being told again
```
$ python demo.py --cap R4
==================== R4 ====================
RUN 2: loaded preference 'CC Priya on anything from Hartwell & Cho (our lawyers).' (source m015).
  applying -> m018: adding CC priya@paperjet.io (from m.cho@hartwellcho.com)
  applying -> m048: adding CC priya@paperjet.io (from j.hartwell@hartwellcho.com)
  applying -> m055: adding CC priya@paperjet.io (from m.cho@hartwellcho.com)
```

## What this proves
- **Persistence across restarts:** the only thing shared between the two
  processes is the file on disk. Run 2 is a brand-new Python process.
- **Behavior change:** the three real legal messages in the inbox
  (`m018`, `m048`, `m055`, all from `hartwellcho.com`) now automatically get
  Priya CC'd; on run 1 nothing was applied.
- **Auditability:** the preference records its `source_message_id` (m015), so
  every applied CC can be traced back to the request that authorized it.
- **Security tie-in:** preferences are only ever learned through this sanctioned
  path. The self-spoofed "autonomous mode" request in **m039** is flagged by the
  SecurityScanner and is therefore **never** written to `preferences.json`.

## Reset (to re-run the demo from scratch)
```
Remove-Item -Recurse -Force memory
python demo.py --cap R4   # run 1
python demo.py --cap R4   # run 2
```

Trace evidence: `trace.jsonl` events tagged `cap=R4` — a `store_preference`
event on run 1 and `apply_preference:add_cc` events on run 2.
