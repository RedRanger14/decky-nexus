# Decky store submission: the AI disclosure letter

Michael's letter to the Decky reviewers, to post alongside the plugin
addition PR (or in their Discord first, to get a policy read before
anyone spends a review cycle).

Background and the rest of the pre-PR checklist live in
[games-1.0-roadmap.md](games-1.0-roadmap.md).

## Refresh before sending

The letter quotes live numbers. Re-run these and correct the text:

```sh
grep -c "def test_" tests/test_backend.py   # backend tests
cat tests/*.mjs | grep -c "^test("          # frontend tests
```

| Claim in the letter | Value when written (2026-08-11) |
|---|---|
| backend tests | 342 |
| frontend tests | 52 |
| games supported | 10 (the 1.0 list, not the 14 in the registry) |
| months of work | "the last few months" |
| testing period | "roughly a month" going game by game |

Note the games number is the **1.0 shipping list**, not `grep -c
displayName src/games.ts` — the registry also holds the post-1.0 entries
(Silksong, Palworld, Bannerlord) which are built but never device-tested.

---

## The letter

Hi Decky reviewers,

I want to be straight with you before you put any time into this. I've
almost certainly broken the "Generative AI was NOT used to write a
majority of the code" rule, and I'd rather just say it myself than have
you discover it. Every commit already has a Co-Authored-By: Claude
trailer on it, so it wasn't exactly hidden anyway.

Quick context: I'm a QA person by trade, not a developer. I work at Nexus
Mods. This isn't an official company product, but it is a personal side
project that has their blessing. I've leaned on Claude pretty heavily to
write the actual code. Without it, this simply wouldn't exist. A proper
mod browser and mod manager is a lot of software for one person who
doesn't really write code for a living.

That said, I don't think that automatically makes it low quality. I've
sunk hundreds of hours into this over the last few months, and the large
majority of that time was testing, not typing. There are 342 automated
backend tests and 52 frontend ones running in CI, but most of my effort
has gone into manual testing on real hardware. I've spent roughly a month
going through the supported games one by one, installing real mods and
real collections. Some individual games took days/weeks to get working
properly. A lot of the code that's in there now only exists because
something broke on device and had to be fixed the hard way. Happy to put
together a written test report per game if that would help.

What it actually does: browse, download, install and manage Nexus mods
without ever leaving Gaming Mode, using a controller or touch. No desktop
mode, no file manager. Ten games supported so far, each one tested on
device.

On the "is this even needed" question: right now, modding on a Deck means
dropping into Desktop Mode and using a file manager or a desktop mod
manager. As far as I know, nobody else is building a controller-first
option that lives entirely in Gaming Mode. For a lot of Deck users,
having to leave Gaming Mode just to install a mod is the entire problem.
That's the gap I'm trying to fill.

Two things worth highlighting now so they don't come as a surprise in
review:

- It has a Python backend.
- For FromSoftware games it downloads me3, a third-party open-source mod
  loader, at runtime. I assume that puts me on the SteamOS Preview
  testing path.

I'd much rather work with you than try to work around you. If there's a
bar I need to meet, a review process I should go through, or specific
parts of the code you want me to dig into, just tell me and I'll do it.
And if the honest answer is that the policy simply doesn't allow this,
I'd genuinely rather hear that straight away than waste anyone's time.

Thanks for reading, and thanks for Decky. None of this would be possible
without it.

Best regards,
Michael

---

## If they ask for the test report

The offer of a per-game written report is the strongest card in the
letter, so it should be ready rather than promised. The git log is a
usable starting point: every version commit records what changed and
usually why, and a large share of them came from device findings. It will
need Michael's own history filled in on top, since the log only covers
what was worked on with Claude present.
