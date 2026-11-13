# Permissions

This is the reference for what AURA may do, who decides, and what nobody can
change. It is written for people who use or run AURA.

For the short version: **you** choose a permission mode and your own rules.
**One gate** checks every action AURA wants to take. AURA cannot change the
mode, the rules, the systems, the schedules or its own settings.

- [Who decides](#who-decides)
- [Modes](#modes)
- [Rules and their order](#rules-and-their-order)
- [What always needs you](#what-always-needs-you)
- [Hard limits](#hard-limits)
- [Approval cards](#approval-cards)
- [Systems](#systems)
- [Schedules and automations](#schedules-and-automations)
- [Inbound channels](#inbound-channels)
- [The Auto reviewer](#the-auto-reviewer)
- [The permission change log](#the-permission-change-log)
- [The sign-in key on local installs](#the-sign-in-key-on-local-installs)
- [What this does not protect against](#what-this-does-not-protect-against)

---

## Who decides

Only the signed-in owner sets the mode, the rules, the limits for Auto, the
assigned systems, schedules and automations (including their approval mode),
connected folders, delete permission, app access, the blocked-apps list and
AURA's settings. Every one of these is a screen or an API call that needs your
session. None of them is a tool AURA can call, and a test fails if one is added.

On a local install the same calls also need a session that was opened with the
[sign-in key](#the-sign-in-key-on-local-installs), made from the computer
itself, by a program AURA did not start. A paired phone cannot change them.

## Modes

The mode is your choice for chat and for messages from your own verified
channels. New accounts start on **Ask first**.

| Mode | What happens to an action that is not a read |
|---|---|
| **Ask first** (default) | AURA shows an approval card and waits. |
| **Auto** | A separate safety reviewer (a second model call) decides. If it blocks, AURA is told why and tries another way or asks you. After 3 blocks in a row, or 20 in one run, AURA asks you instead. |
| **Skip approvals** | The action runs without a card. |

In every mode:

- Reads run on their own. This covers reading mail, calendar, files, the
  screen and the clipboard.
- AURA's own workspace writes run on their own: creating or completing a to-do,
  saving a memory, notifying you, delegating a task or drafting a chase. Add an
  Ask rule on a tool if you want a card for it.
- Deleting something in AURA's workspace (a to-do, for example) is not in that
  group. It asks in Ask first.
- Block rules, Ask rules, [what always needs you](#what-always-needs-you) and
  the [hard limits](#hard-limits) apply, whatever the mode.

The gate reads the mode each time, so a change applies to the next action.

## Rules and their order

A rule says `block`, `ask` or `allow` for a system or a single tool, and can be
limited to a target:

| Target | The rule is limited to |
|---|---|
| path | A file or folder. `**` matches any depth. |
| command | A shell command. `git status` matches that command and anything that continues it after a space. `=git status` matches exactly that. `~needle` matches any command containing the text, and can only block or ask. |
| recipient | An email address. |
| domain | A web address's host. |
| app | An application. |

A rule can also be limited to one schedule or one automation, and can expire
(the "10 minutes" answer on a card makes a rule that does).

For each action the gate looks at rules in this order and stops at the first
that matches:

1. **Block** rules. The action does not run, in any mode.
2. **Ask** rules. A card is shown, in any mode, including Skip approvals.
3. **Allow** rules. The action runs.
4. Then the default for that kind of action, and the mode.

Being more specific does not change the order: a broad block beats a narrow
allow. An allow rule with a target only counts if it covers every target of the
action. A command that chains several commands needs each one covered. A
command that sets environment variables first, or hides what it runs, cannot be
covered by an allow rule.

Block and Ask rules on paths also apply to the paths a command names. A file
search or folder listing leaves out the folders a block rule covers and says so.

You can see, edit and revoke every rule in Settings, Permissions. Revoking keeps
the record for the log. Rules the upgrade made from your 1.0 settings are
recorded as coming from the upgrade. A command denylist set when AURA was
installed shows there too and cannot be edited there.

## What always needs you

These ask every time, in every mode, and no rule can turn the card off:

- Permanently deleting a file (`delete_file`).
- A command that deletes files, or that names a protected location. The card
  says so.
- Writing to a tool's own configuration file (for example a git, npm, pip or
  cargo settings file).
- Connecting a folder, allowing an app, and allowing deletes in a folder.
- Anything that changes permissions, rules, systems, schedules, automations or
  AURA's settings. AURA cannot do these at all; only you can, in the app.

Only "once" and "No" are offered on these cards.

## Hard limits

These are checked before any rule or mode. A rule, a mode or an earlier approval
does not lift them.

- **AURA's own places**: its data folder, database, settings files, sign-in key
  and its install folder. Commands that name them are refused too.
- **Protected locations**: credential folders, shell profiles, start-up items
  and git's own configuration and hooks. See [DEVICE.md](../DEVICE.md).
- **AURA's own API.** Commands may not talk to AURA's address or to other
  services on the computer's loopback.
- **Fetches to private networks.** `fetch_url` refuses loopback, private,
  link-local and similar addresses, checks every redirect, and connects only to
  the address it checked. A redirect to a different site is also judged against
  your address rules: without an allow rule for that site it is refused, and the
  message names the site. A web link given to `open_app` is treated the same way
  as `fetch_url`: same limits, same rules, same checks.
- **AURA's own process** and AURA's own window under screen control.
- **Blocked apps** (the default list covers banking, trading, crypto wallets and
  password managers; you can edit it).
- **The install's command denylist**, if the person who installed AURA set one.
- **Tools that are not catalogued.** Every tool has an entry saying which system
  it belongs to and what kind of action it is. A tool without one is not offered
  to the model and is refused if called.
- **Device control off.** On the hosted site, and on any install where device
  control is off, the computer tools do not exist.

A refused action goes back to AURA with the reason, so it can tell you or
choose another way.

## Approval cards

When the gate says "ask", the action is parked and a card shows what it would
do: the actual command, path, recipient or address. Your choices:

| Choice | Effect |
|---|---|
| **Allow once** | Runs this one action. Nothing is stored. |
| **Allow for 10 minutes** | Runs it and stores an allow rule that expires in 10 minutes. |
| **Always allow** | Runs it and stores an allow rule that does not expire. |
| **Don't** | Does not run it. AURA is told you said no. |

The card says in words what "for 10 minutes" and "always" would allow. For a
command, "always" proposes the exact command; a prefix is offered only for a
short list of read-only commands. Some cards offer only "once" and "Don't":
the always-needs-you actions, and a few actions for which no safe rule can be
written (such as typing into a running program).

Folder, app and delete-permission cards ask yes or no. A card never connects a
whole drive or your home folder; connect those deliberately in This computer
(a whole drive cannot be connected at all).

Things to know:

- Answering is one-shot. A second answer to the same card changes nothing.
- The gate looks again after you answer. If something changed since the card
  was shown (a block rule you added, a folder you disconnected, edited
  arguments), the action is refused.
- A paired phone can answer "once" or "No". Longer allowances, folder, app and
  delete cards, and always-needs-you cards must be answered on the computer.
- On a local install, a card cannot be answered by a click AURA itself made, or
  by a program AURA started.
- An unanswered card expires: 24 hours for chat, inbound and automation
  actions, 12 hours for a schedule, 30 minutes for a folder, app or delete card.
  The task sees the expiry and does not run the action.
- Approving continues the same task. The action runs when you answer, and AURA
  carries on from the result.
- Once a card has its final answer, the arguments and preview kept with it are
  scrubbed of secrets.

## Systems

A system is a plugin: Inbox, Calendar, Tasks, Memory and Planning are always
assigned. Research, Documents, Delegation, Focus Guard and, on a local install,
This computer (device control) are added or removed in Systems.

A tool runs only if its system is assigned. That is checked at the gate, not
just by hiding the tool from the model. Removing a system keeps its history.

## Schedules and automations

Both run while you are away, so each has its own approval mode, **Ask** or
**Auto**, and the default is Ask. Your own mode does not apply to them, so
Skip approvals never makes unattended work skip cards.

- In Ask, an action that needs approval parks the run, notifies you, and expires
  (12 hours for a schedule, 24 for an automation). An identical waiting card is
  not made twice.
- In Auto, the [reviewer](#the-auto-reviewer) decides. Auto needs a connected
  model.
- Allow rules can be limited to one schedule or automation, so one standing job
  can be permitted to do one particular thing.
- An automation fills values from the message that triggered it (a subject, a
  sender's address, the body) only into text fields such as a subject, body or
  title. A path, address, recipient or command is never filled in from a message,
  and an action that asks for that is refused.
- A schedule cannot use the screen, the clipboard or open apps. An automation or
  an inbound message also cannot run commands or delete files.
- Screen control (mouse and keyboard) happens only in chat, with the app open and
  you present.
- AURA's own background checks (the heartbeat) read, and write to AURA's own
  workspace. They never ask and never touch the computer; an action that would
  have asked is refused.

Changing a schedule's or automation's approval mode is a permission change and
is logged.

## Inbound channels

Email, Telegram, Slack and the CLI webhook send messages to AURA. A channel
token proves the channel, not the person, so a message starts a task only when
it comes from the **owner**:

- You confirm the owner on each channel in Channels: the address or ID that
  counts as you. A private, owner-only chat bridge can be declared instead
  (not for email).
- Until you do, nothing arriving on that channel is acted on. It is kept as a
  notification for you and starts no task, calls no tool and teaches AURA nothing.
- Text from anyone else is data, never an instruction.
- An owner message runs the same task loop and the same gate as chat, with your
  mode. Inbound tasks cannot run commands, use the screen or clipboard, open
  apps or delete files.
- **Email is capped at Ask first, whatever your mode,** because a mail's sender
  cannot be proved. Email is matched on the address alone, never on a display
  name, and an owner-only bridge is not available for it.
- Changing who counts as the owner is a permission change and is logged.

Upgrading from 1.0 does not carry an owner over, so existing channels need
their owner confirmed before inbound messages start tasks.

## The Auto reviewer

In Auto, each action that would have asked goes to a separate model call that
answers "would the user want this done, given what they asked?".

What it sees: your request for this task, the limits you wrote in Settings,
Permissions, and the proposed action (tool, targets, arguments). It never sees
tool results, email bodies or file contents, so text AURA read cannot talk it
into anything.

It fails closed. An unusable answer, an error or a reached spend cap is a
block. Some actions are blocked without asking a model: commands too hard to
check or dangerous, an email or invite to someone you did not name (by full
address) and who is not a contact, a fetch or link whose address carries a long
or encoded payload anywhere in it, an action whose arguments are too long for the
reviewer to read in full, and writing a script. A block sends the action to you
as a card once the fall-back is reached.

The reviewer's model is `REVIEWER_MODEL`, then `BACKGROUND_MODEL`, then the main
model. Its calls count toward the daily spend cap.

**When it is unavailable.** With the built-in demo model (no model key), nothing
can review. Auto cannot be chosen, and if it was chosen earlier the mode acts as
Ask first and is labelled "Auto (acting as Ask first)". Connecting a model
brings it back.

## The permission change log

Every decision other than a plain read, every review, every card and answer, and
every change to the mode, rules, folders, apps, blocklist, systems, schedule
modes, channel owners and Auto limits is written to a log you can read in
Settings, Permissions.

The log is hash-chained and signed with a key derived from the server's
`SECRET_KEY`. Editing, deleting or reordering a row breaks the chain, and
`GET /api/permissions/events/verify` says where. If an action cannot be logged
it is not run.

Each change you make also raises a notification. It is marked urgent when the
change gives AURA more room, so a change you did not make is noticeable. On a
local install a desktop notification appears too. If a program AURA started
tries to change permissions, the attempt is refused, logged and flagged urgent.

The upgrade from 1.0 writes one entry to your log saying what it mapped.

## The sign-in key on local installs

A local install has one user. To keep other accounts on the same computer from
signing in as you, signing in needs a key:

- The desktop app makes it on first start and keeps it in `aura.env` as
  `AURA_LOCAL_LOGIN_KEY`. The link the app opens carries it.
- From source, AURA makes `local-login.key` in its home folder (`~/.aura`) the
  first time it starts, readable only by you, and the terminal client reads it
  from there.
- The key is never written to the log and is hidden from the programs AURA runs.
- A session opened without the key cannot change permissions.

Do not share the key or the link.

## What this does not protect against

- **Commands are not sandboxed.** A command runs as your operating-system user
  and can reach whatever that user can. AURA's own settings on such a machine
  are protected by blocks, review and the log, not by an operating-system wall.
  Read [DEVICE.md](../DEVICE.md).
- **A person or program already running as you** can read what you can,
  including a signed-in browser session. AURA cannot stop that.
- **Rules are only as good as you write them.** A broad allow rule allows a lot.
  Start narrow.
- **Auto is a second model's judgement,** not a guarantee. Use Ask first for
  work where a wrong action would be costly.
- **The log shows what happened; it does not undo it.**
