# Tasks and memory

How AURA carries out an assignment, what it remembers about the work, and how
you control that. Permissions are in [PERMISSIONS.md](./PERMISSIONS.md).

- [The task loop](#the-task-loop)
- [What a work item records](#what-a-work-item-records)
- [Stop and continue](#stop-and-continue)
- [Memory](#memory)
- [Past work](#past-work)
- [Preferences](#preferences)
- [Facts](#facts)
- [Pausing, turning off and resetting memory](#pausing-turning-off-and-resetting-memory)
- [What is never stored](#what-is-never-stored)

---

## The task loop

AURA works from the first message. There is no setup phase to get through. When
you give it something to do, it runs a loop:

1. **Recall.** Before the first model call, it looks up similar past work and
   the preferences you have for this kind of task. This is a database lookup, not
   a model call.
2. **Plan.** For anything with more than one step it writes a short plan. You see
   it in the task panel.
3. **Act.** Each action goes through the permission gate. It runs, or waits for
   your approval, or is refused with a reason that AURA reads and acts on.
4. **Verify.** After a change, AURA's code reads back what it changed where it
   can: the to-do exists, the event is on the calendar, the file has the bytes
   it was meant to have, the sent mail is in Sent. AURA is also asked to check
   other changes itself and record the check.
5. **Report.** The final message says what was done, what was checked and what
   needs you. The report is not marked done unless the work and the checks agree.

AURA acts on clear tasks at once and asks only when a task is ambiguous or risky.

A plain question gets a plain answer and leaves no work item. A work item is
created when AURA first uses a real tool or writes a plan.

**The result of a task** is one of:

| Status | Meaning |
|---|---|
| Working | Running now. |
| Waiting on you | An approval card is open. |
| Ready to carry on | Paused (after an approval, a step budget or an interruption) and will continue. |
| Done | The work happened and every check passed. |
| Partly done | Some of it happened, or a check failed, or something you declined or let expire was part of it. |
| Didn't finish | Nothing useful happened, or every read failed. |
| Stopped | You stopped it. |

Limits on one task, all settings (see [DEPLOY.md](../DEPLOY.md)):

- `MAX_AGENT_STEPS`: model calls in one stretch of work (8; 16 in the desktop app).
- `MAX_TASK_STEPS`: model calls in the whole task (40; 60 in the desktop app).
  At the limit AURA stops and says where it stands.
- `SEGMENT_SECONDS`: after this long in one stretch it starts no new model call
  (200; 600 in the desktop app).
- `DAILY_SPEND_CAP_USD`: checked before every model call. At the cap AURA stops
  and says so.

## What a work item records

Each task is kept as a work item, listed under Memory, Past work.

| Field | What it holds |
|---|---|
| Instruction | Your words, with secrets removed (up to 4,000 characters). |
| Title and type | A short title and one of: Email, Calendar and scheduling, To-dos, Research, Writing and drafting, Documents, Files and folders, Commands and code, Apps and screen, People and contacts, Briefings and summaries, Delegation and follow-ups, Other. |
| Status | As in the table above. |
| Plan | The steps, each marked to do, doing, done, skipped or failed. |
| Steps | Each tool call, approval and check, in order, with a one-line summary and the arguments with secrets removed. |
| Systems and tools used | Which systems and tools it touched. |
| How it was done | A short account of the approach (up to 1,200 characters). |
| Outcome and report | A one-line result and the final message (up to 4,000 characters). |
| Verification | The checks that ran, and whether they passed. |
| Your feedback | A thumbs up or down, with a note. |
| Recall used | The past work, preferences and facts that were shown to AURA for this task. |
| Cost | Model calls, tokens and estimated cost. |

You can edit the title, type and "how it was done", and you can delete an item.
A thumbs-down with a note also becomes a preference.

Task memory is never compacted, merged or faded. An item goes only when you
delete it or reset past work.

## Stop and continue

**Stop.** The Stop button ends a task. If AURA is mid-step it finishes the call
it is in and starts nothing else; calls not yet run are skipped. If the task is
waiting for you, its open cards are rejected and the task ends as Stopped with
"Stopped. Nothing further was done."

**Approving continues the same task.** When you answer a card, the approved
action runs, and AURA picks up from the result. Declining or letting a card
expire also continues: AURA is told, and does not retry the action.

**Where it continues.**

- On the hosted site the page continues the task while it is open. If the page
  is closed, the next background run picks it up. Background runs on the hosted
  site happen three times a day (see Known limitations in the README), so a
  task left waiting overnight may not move until then.
- In the desktop app, the background worker continues tasks while the app is
  running.

A task that was running when AURA stopped (a crash, a restart) is marked ready
and continues. The call that was open is reported to AURA as "may or may not
have run" so it checks before repeating it.

Only one run works on a conversation at a time. Sending a message while AURA is
still working on that conversation is refused until it finishes or you stop it.

## Memory

AURA keeps three things: two stores new in 2.0 and the facts it always had.

| Store | What it is | Used for |
|---|---|---|
| **Past work** | The work items above. | Showing AURA how similar tasks went. |
| **Preferences** | Short lines about how you want each kind of task done. | Telling AURA before it acts. |
| **Facts** | People, projects, decisions and plain facts. | General context. |

Before it acts, AURA is given the closest past work (up to three), the
preferences for this type of task (and general ones), and up to six facts. The
whole block is capped in size; if it would be too long, facts go first, then
past work, then general preferences.

## Past work

Recall looks at your 300 most recent finished tasks that can be recalled and
picks the best matches by similarity. Matching uses meaning when an embedding
model is configured and shared words otherwise. Tasks that went well, and tasks
of the same type, score a little higher; tasks you gave a thumbs-down score lower.

A task is **not** kept for recall when:

- it was a lookup: every action only read, no plan, no approval, at most three
  tool calls, such as "what is on my calendar";
- memory was paused, or memory was off for that chat, when it finished;
- you marked that item as not recallable.

## Preferences

A preference is one instruction line, up to 300 characters, filed under a task
type (or "general") and optionally one system. Examples: "Keep email replies to
three sentences." "Never schedule meetings before 10."

**How they are captured.** Only from your own typed messages in chat, never from
anything AURA reads:

- **A statement.** A sentence in your message such as "always...", "never...",
  "from now on...", "I prefer..." is kept.
- **A correction.** When you say "no, that's wrong..." right after a task, the
  correction is kept against that task type, and the task is marked down.
- **Rewording.** With a real model connected, a short model call tidies the
  sentence and picks the type. If it fails, your plain words are kept.
- **A note from AURA.** AURA can ask to keep a preference, but only a sentence
  you wrote in the same message, word for word. It cannot reword it, and it
  cannot replace an existing line.
- **By hand.** You can add one in Memory, Preferences. Teaching a skill and a
  thumbs-down note also make preferences.

Messages from channels, tool results, emails, web pages, files and anyone other
than you never create a preference. Inbound channel messages cannot either.

**Conflicts.** A line that says nearly the same as an existing one is not added
twice. A line that closely rewrites an existing one replaces it; the old line is
kept, turned off, and linked to the new one. Lines from AURA's note tool never
replace.

**Editing.** In Memory, Preferences you can edit the text, change its type or
system, **turn it off** (it stays listed, but is not used) or delete it. When
AURA keeps one from a message, a "Noted" chip appears so you see it at once.

## Facts

Facts are what AURA saves about people, projects and decisions. You can add,
edit, pin and delete them in Memory, Facts. Facts you edit by hand are never
merged or faded by the daily memory compaction. On upgrade from 1.0, facts of kind preference, style and habit, the notes you
taught skills, and a non-default tone setting were copied into Preferences.

## Pausing, turning off and resetting memory

| Control | Where | Effect |
|---|---|---|
| **Pause memory** | Memory page | No new preferences or facts are captured from chat, finished tasks are not kept for recall, and AURA's save-a-fact tool declines. What is already stored is still used. |
| **Don't use memory in this chat** | The chat | No recall, no capture, and the chat's tasks are not kept for recall. |
| **Turn off a preference** | Memory, Preferences | That line is not used. |
| **Delete** an item, preference or fact | Memory | Removes that one thing. |
| **Reset** | Memory | Wipes preferences, past work, facts or all of them. You must type RESET. It cannot be undone. Tasks that are running or waiting are not reset. |

## What is never stored

- **Secrets.** Before anything is written to past work or preferences, values in
  your vault are replaced by their `{{vault:name}}` placeholder, and anything that
  looks like a credential (API keys, tokens, private keys, passwords after
  `password:` and similar, long encoded blobs near the word key or token) becomes
  `[redacted]`. Argument fields named like a secret are blanked.
- **Screenshots.** A screenshot goes to the model for that step only. It is not
  kept in memory; if a paused task is resumed, AURA is told to take a new one.
- **Tool results as memory.** The record keeps a one-line summary of each step,
  not the full result. While a task is running or waiting, its working state
  (including recent tool results) is held so it can resume; that state is
  cleared when the task finishes.
- **Preferences from anything but you.** See above.
- **Anything while memory is paused or off for the chat.**

Redaction is pattern-based and best effort. Do not paste secrets into chat.
