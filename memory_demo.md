# memory_demo.md — Persistent Preferences (R4)

InboxHero stores user preferences in `memory/preferences.json`. A preference
stated in one process survives a full exit and changes behavior in a later,
completely separate process — no framework, just JSON on disk.

Nothing about the preferences is written into the code. `memory.PreferenceLearner`
reads the inbox and extracts them from the sentences that state them, so a new
standing request in a new message is picked up without an edit.

## The preferences in this inbox, and how they were found

`m015` (Priya): *"Standing request: from now on, please make sure I'm CC'd on
anything that comes in from our lawyers at Hartwell & Cho."*

The learner matches the CC request, lifts the scope (`Hartwell & Cho`) out of the
sentence, and then resolves that firm name to a **domain by looking at the inbox's
own senders** — `m.cho@hartwellcho.com` and `j.hartwell@hartwellcho.com` make
`hartwellcho.com` the match. The CC address is the sender of the request.

`m041` (Sam): *"Note for the assistant: I do not take meetings before 11:00am,
ever."*

The learner matches the "no meetings before <time>" shape and stores `11:00am` as
a cutoff. X5 later uses it to counter-propose against any parsed commitment that
falls earlier.

## Run 1 — learn from the inbox, persist, exit

```
$ python demo.py --cap R4
==================== R4 ====================
RUN 1: learned 'earliest_meeting_time' from m041 -> "Note for the assistant: I do not take meetings before 11:00am, ever."
RUN 1: learned 'cc_rule:hartwellcho.com' from m015 -> "Standing request: from now on, please make sure I'm CC'd on anything that comes in from our lawyers at Hartwell & Cho."
RUN 1: wrote 2 preference(s) to memory/preferences.json, then exiting.
```

The process exits. `memory/preferences.json` now contains:

```json
{
  "earliest_meeting_time": {
    "key": "earliest_meeting_time",
    "value": { "earliest": "11:00am" },
    "source_message_id": "m041",
    "stated": "Note for the assistant: I do not take meetings before 11:00am, ever.",
    "created_at": "2026-09-04T08:05:00"
  },
  "cc_rule:hartwellcho.com": {
    "key": "cc_rule:hartwellcho.com",
    "value": { "cc": "priya@paperjet.io", "domain": "hartwellcho.com", "scope": "Hartwell & Cho" },
    "source_message_id": "m015",
    "stated": "Standing request: from now on, please make sure I'm CC'd on anything that comes in from our lawyers at Hartwell & Cho.",
    "created_at": "2026-09-04T11:03:00"
  }
}
```

`created_at` is the timestamp of the message that stated the preference, not the
wall clock, so the file is byte-identical on every run.

## Run 2 — fresh process, the drafts themselves change

```
$ python demo.py --cap R4
==================== R4 ====================
RUN 2: loaded 2 preference(s) from disk: ['cc_rule:hartwellcho.com', 'earliest_meeting_time']
  rule from m015: CC priya@paperjet.io on mail from hartwellcho.com (scope "Hartwell & Cho")
    applied -> m018 (m.cho@hartwellcho.com): draft cc=['priya@paperjet.io'], cited=['m015']
    applied -> m048 (j.hartwell@hartwellcho.com): draft cc=['priya@paperjet.io'], cited=['m015']
    applied -> m055 (m.cho@hartwellcho.com): draft cc=['priya@paperjet.io'], cited=['m015']
  rule from m041: no meetings before 11:00am (enforced on drafts by X5 / DraftBuilder)

preferences applied to 3 message(s) without restating them: ['m018', 'm048', 'm055']
```

## What this proves

- **Persistence across restarts:** the only thing shared between the two
  processes is the file on disk. Run 2 is a brand-new Python process.
- **Learned, not configured:** no message id, address, firm name or time appears
  in `r4.py`. `test_preferences_are_learned_from_message_text` asserts that the
  stored `stated` string is a real sentence from the cited message, and that the
  resolved domain is a domain that actually appears in the inbox.
- **It changes the artifact, not a log line:** the CC is attached to the real
  `Draft` object produced by `inboxhero/drafting.py` — the same object R3 would
  put in front of a human, so `as_payload()["cc"]` carries it into `outbox/`.
  `test_learned_cc_preference_lands_on_a_real_draft` checks exactly that.
- **Auditability:** each preference records its `source_message_id`, so every
  applied CC traces back to the request that authorized it, and the
  counter-proposal X5 sends quotes `m041` verbatim.
- **Security tie-in:** preferences are only ever learned through this sanctioned
  path. The self-spoofed "autonomous mode" request in **m039** is flagged by the
  SecurityScanner and is therefore **never** written to `preferences.json`.

## Reset (to re-run the demo from scratch)

```
Remove-Item -Recurse -Force memory
python demo.py --cap R4   # run 1 — learns and exits
python demo.py --cap R4   # run 2 — applies
```

Trace evidence: `trace.jsonl` events tagged `cap=R4` — a `store_preference`
event per learned preference on run 1, and `apply_preference:add_cc` events on
run 2 that name the draft each CC landed on.
