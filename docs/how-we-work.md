# Who this is for, and how to work on it

**Date:** 14 August 2026. Written by Michael, recorded here because it kept
having to be said out loud.

This is the first document a new session should read. It is not style
guidance — every rule below exists because breaking it cost real testing
time on real hardware.

---

## The audience

**Someone holding a handheld who has never heard of a load order.**

Not a Vortex user. Not someone who reads mod pages. Somebody who bought a
Steam Deck, saw that Skyrim can be modded, and wants the mods people talk
about — without learning what a script extender is, what an ESM is, or why
one file in the wrong folder stops the game starting.

They are on a sofa, using a controller, with no keyboard and no file
manager. They cannot read a stack trace and should never be asked to. If
something can only be fixed in Desktop Mode, that is a failure of the
plugin, not of the user.

Two things follow from that, and they drive most design decisions here:

- **Nothing may require typing.** No paths, no config edits, no INI keys.
- **Nothing may require knowing modding.** If a mod needs another mod, the
  plugin finds out and handles it. The user chose a mod; they did not sign up
  to learn a dependency graph.

---

## If we can do it for the user, we do it

**A button the user has to press is a failure. Instructions are a worse
failure.**

The test is simple: *could the plugin have known?* If yes, it should have
acted. Detecting a problem and reporting it is only acceptable when there is
genuinely nothing to be done.

This has been got wrong repeatedly, and every instance looked reasonable at
the time:

| What shipped | Why it was wrong |
|---|---|
| A button offering to switch off mods that crashed the game | The log named the mod that threw 1,041 exceptions. Nothing needed asking. It switches them off now. |
| "Install BaseLib to make these mods work" | Slay the Spire 2 mods declare their dependencies. The plugin reads the manifest and installs them. |
| A collection installing 168 mods "with nothing left hanging", then crashing | It silently skipped the script extender the whole collection ran on, because that source type was unhandled. Silence is the worst possible report. |
| Step 3 reporting "(1)" forever | One of its two tools was a GUI installer that could never complete headlessly. A step that cannot be finished is not a step. |
| Two stale `.reds` files surviving every reset | Reset only cleaned one of five directories mods write into. One bad script disabled every script mod, invisibly, for weeks. |

The pattern in all of them: **the information existed and the plugin did not
act on it.** That is the bug, more than whatever else was broken.

### Corollary: silence is a bug

A collection that reports success and then crashes is worse than one that
reports a failure. If something was skipped, say what and why. If nothing
can be done — a mod needs DLC the user does not own, or an author has not
updated it — say that plainly and stop offering fixes that do not exist.

### Corollary: never cry wolf

The health check once reported all 77 installed mods as missing SMAPI,
because SMAPI arrives through Step 1 rather than the mod list. Two real
findings were buried in 77 false ones. A check that is wrong most of the
time trains the user to ignore it, which is worse than not having it.

Before reporting a problem, ask what would make it a false positive:
- Is the requirement a mod *manager* (Fluffy, Vortex, MO2)? This plugin is
  the manager. Not a dependency.
- Is it a framework installed by a Step rather than the mod list?
- Did the collection's curator deliberately omit it? A 283-mod set that
  boots is evidence.
- Does the game's own log actually complain? **That is the discriminator** —
  ask the game, do not infer.

### Handheld-first is not a slogan

Five endorse buttons once shared one focus target: with a mouse it looked
fine, and with a controller four of five authors could not be thanked at
all. Then they became individually selectable with no focus styling, which
was worse — five invisible stops before the cursor reappeared.

Anything interactive must be reachable with a D-pad and must visibly show
focus. Test the gamepad path, not the pointer path.

---

## How to give Michael instructions

**Every response that ends in something for me to do closes with a numbered
list. Not prose.**

I am the one holding the device. Buried instructions mean reverse-engineering
what to press out of a wall of reasoning, and a missed step has cost whole
test cycles — once because a step was mentioned only in passing and I never
saw it.

The format:

1. One action per line.
2. Name the exact screen and the exact label to press.
3. Then: **what to report back**, and what each possible answer would mean.

That last part matters as much as the steps. "Tell me if it boots" is much
less useful than "boots → the mismatch was the cause; crashes → the patcher
genuinely does not work here, and I will stop trying to make it". It tells me
what I am actually testing and lets me spot a wrong theory before spending
twenty minutes on it.

If there is nothing for me to do, say so explicitly.

### Other things about working with me

- **Toasts truncate.** Do not put diagnostic information in one and ask me to
  read it. Log it and read it off the device yourself.
- **Read the documentation before changing behaviour.** Two days went on
  inference where a mod page or a tool's readme had the answer in one line:
  the Anniversary Patcher's page says it loads FOSE itself; the ESM Patcher's
  page says it is a GUI installer with two path fields. Both were changed
  wrongly first and read afterwards.
- **Do not suggest stopping for the day.** I will decide when to stop.
- **Correct mistakes plainly and move on.** State what was wrong in a
  sentence and fix it. No repeated apologising, no dwelling — it costs
  reading time and helps nothing.
- **Do not tell me something is fixed unless it has been verified**, and say
  which parts are reasoned rather than tested.
- **Ask the device, not me**, whenever the answer is on it. Settings, logs,
  file listings and the API are all reachable over SSH.

---

## Testing costs more than coding

A test cycle is a reset, a download, an install and a boot on real hardware —
minutes to hours. Ten games, so a regression found by me instead of by a test
is expensive twice: the cycle it wastes, and the cycle it takes to confirm
the fix.

Which means:

- **A regression in an old game caused by work on a new one is the most
  expensive bug there is.** `tests/gameConfig.test.mjs` freezes what every
  game's config resolves to and fails on any unintended change; it has
  already caught one.
- **Config, launch strings and step logic need tests too.** They live outside
  functions, so unit tests never reached them — and every failure in one bad
  week was in exactly that layer. `tests/nav.test.mjs` covers launch
  templates, uncompletable steps, unreachable focus targets and missing tab
  wiring.
- **Never deploy over work in progress.** Deploying restarts Decky and kills
  whatever the plugin was doing. `deploy.ps1` refuses when a tool or download
  is running; it has already stopped one bad deploy. Same rule by hand: no
  timeouts on anything writing to a prefix or a game file.
- **Bump the version every deploy** so the QAM badge says which build is on
  the device, run `pnpm run check`, and **push** as part of the same step.

---

## The shortest version

Act, do not instruct. Say what you did. Ask the game, not the user. Test the
controller path. Number the steps. Read the page before you change the code.
