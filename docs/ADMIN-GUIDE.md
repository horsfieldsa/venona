# Admin guide

For the person who runs Venona: setup, the review queues, runs, tuning,
people, and what spends money. How to read what Venona writes is the reader
guide, and everything in it applies to you too.

---

## Contents

1. [Readers and administrators](#1-readers-and-administrators)
2. [Setting up a domain](#2-setting-up-a-domain)
3. [Curation](#3-curation)
4. [Runs](#4-runs)
5. [Tuning what Venona reads](#5-tuning-what-venona-reads)
6. [People and access](#6-people-and-access)
7. [The data](#7-the-data)
8. [Being told when something breaks](#8-being-told-when-something-breaks)
9. [Which model does the thinking](#9-which-model-does-the-thinking)
10. [What costs money](#10-what-costs-money)
11. [The extension for your readers](#11-the-extension-for-your-readers)
12. [Admin keyboard keys](#12-admin-keyboard-keys)
13. [When something looks wrong](#13-when-something-looks-wrong)

---

## 1. Readers and administrators

Two roles, and nothing in between.

| Role | What it can do |
|---|---|
| **reader** | Read everything meant for reading: the brief, the graph, the glossary, search, Learn. Change only what is theirs: display name, emphasis and follows, their own brief's rewrite, their own study record |
| **admin** | All of that, plus Curation, Runs, Admin, the configuration documents, and every control that spends |

The server refuses on role, not on hiding: anything not explicitly opened to
readers is refused for them, so a reader who follows a pasted admin link gets
a polite no rather than a page.

Readers see five destinations in the header; you see six. Everything else
yours is under **Admin**, on the rail down its left side. The same split
runs through smaller controls: looking a term up from a hover card, defining
one by hand, and starting any run are all yours, because they either spend or
write the shared record.

Demoting an administrator, suspending anyone and reinstating them are buttons
on **Admin, People** (section 6). Promotion is deliberately not a button:
making someone an administrator needs a terminal on the server.

```
venona users role reader@example.com admin
```

There is no delete. Suspending someone signs them out, and every assistant they
have connected stops working. Nothing of the record is lost.

---

## 2. Setting up a domain

A fresh domain sends its administrators to the setup wizard on first sign-in.
A configured one keeps the wizard reachable from the Admin sidebar as
**Domain setup**, for refining.

Three steps, and a reload lands you back on whichever one you are at.

1. **Describe.** Name the domain, fix its short name (set once, used in
   addresses), and say who the reader is: what you do, what you track, what
   this should help you decide. The more specific this is, the better the
   drafts. Optional lines seed what matters right now and the publications
   you already read.
2. **Research.** Press **Research this domain** and Venona drafts every
   configuration document from that description. It takes tens of minutes;
   documents become reviewable as they land, and you can start reading early
   drafts while the rest generate.
3. **Review.** Each draft opens in the config editor; Save applies it, and
   the banner names the run it came from.

> [!WARNING]
> Order matters at the end: save **Types and relationships** before
> **Anchors**, then load the anchors into the graph with the **Apply** button
> on the Anchors document once both are saved. Saving anchors is not loading
> them; nothing in a run re-reads that file.

> [!NOTE]
> Then expect the first research run to look alarming. Venona meets every name
> and acronym at once on its first reading, so some definitions are held over
> to later runs, and dozens of name conflicts land in one day. Both are by
> design: definitions fill in over the following runs, and the conflicts wait
> for you on the Curation page.

---

## 3. Curation

**Curation**, on the Admin rail, is where you review what Venona proposed.
Four queues under one
control, each carrying its open count: **merges**, **definitions**,
**questions**, **history**. `[` and `]` move between them.

### Merges

One conflicting pair at a time, with the evidence for both sides on screen.
The badge names what Venona noticed:

| Kind | What it means | Usually |
|---|---|---|
| two spellings | The same thing written two ways | **Merge** |
| shared acronym | Two things generating the same acronym | **Keep apart** |
| one name inside the other | One thing long and short, or a parent and a child | Read it: Merge, part of, or Keep apart |
| Venona got this wrong | A reading neither name supports | Not a judgement call. **Skip it** |
| proposed placement | A suggested parent for a name that connects to nothing | part of, or Keep apart. Never Merge |
| unclassified | No recorded reason | Read both sides on their own terms |

> [!CAUTION]
> Err toward **Keep apart**. A merge is reversible, but until you reverse it
> every statement about one side reads as a statement about the other. A wrong
> keep-apart just comes back next run.

`M` merge, `K` keep apart, `R` relate, `1` to `9` choose a listed
relationship, `X` swap which side survives, `S` or `→` skip, `←` back, `U`
undo.

### Definitions

One term at a time, not one reading: an acronym with five candidate readings
is one decision. `Enter` does whatever Venona suggested, which covers most
rows.

| Key | Action |
|---|---|
| `A` | Accept the definition |
| `B` | Keep every reading; the term is genuinely ambiguous |
| `S` | Same thing: fold into the strongest reading |
| `R` | Reject |
| `E` | Define by hand |
| `1` to `9` | Pick one reading when there are several |
| `L` | Look it up on the web, about $0.13 |
| `Shift+L` | Look again for other meanings |
| `Shift+E` | This term names a thing, not an acronym |
| `N` | Not an acronym: ignore it from now on |
| `→` / `←` | Skip / back |
| `U` | Undo the last verdict |

Two bulk actions sit in the bar on this tab:

- **Refresh definitions** works on whatever the latest brief still leaves
  undefined.
- **Look up all** works the entire undefined queue in one run. It can spend
  tens of dollars and take hours, so it opens a window that counts the real
  queue and quotes a price and duration measured from this domain's own past
  lookups. It runs in the background, can be cancelled from the Runs page,
  and keeps whatever has landed by then. Large queues go in batches, sized in
  Settings.

### Questions

Study questions the model proposed for readers' decks. A row shows the kind,
the prompt, the answer or the choices with the right one marked, and the
sentence that backs it. **Approve** or **Reject**; nothing reaches a
learner's deck until it is approved. **Generate questions** proposes more,
quoting its price first from your own past calls.

Cards made directly from definitions and connections skip this queue on
purpose: they are the record itself, asked as a question, so there is nothing
to vet.

### History

Every merge ever made, and the unmerge for each. Nothing is destroyed.

### The banner and Rebuild graph

A merge takes effect immediately in the record and reaches the graph on the
next rebuild. The banner counts pending changes and **Rebuild graph** applies
them. It is disabled during a run, which rebuilds at its end anyway.

> [!NOTE]
> `N curated relationships waiting for a second source` is a different number,
> and a rebuild will not surface those: a connection needs **both** of its
> names confirmed by a second publisher. **look for more now** starts a
> research run, which is what actually moves that count. Merging is the free
> half; consolidating two spellings consolidates their evidence, and sometimes
> that alone clears the bar.

---

## 4. Runs

**Runs** is on the Admin rail, under Curation.

### Lanes

Two, running independently, so you can define one acronym during the twenty
minutes a research run takes.

| Lane | At once | What runs in it |
|---|---|---|
| **research** | 1 | The nightly and on-demand research runs, name research, adding an article, domain setup, brief rewrites |
| **light** | 3 | Definition lookups, definitions refresh, the whole-queue sweep, question generation, readers' daily briefs |

A second research run is refused. A fourth light run queues rather than
failing, and two identical requests collapse into one.

### The page

Five figures at the top: total runs, total spend, total warnings, the last
run, and the next scheduled one.

**In progress** lists what is executing; clicking a row opens it. A strip
above it names anything queued in the light lane.

**History** is the past: started, kind, subject, status, warnings, docs,
calls, cost, outcome. A lookup shows `—` in the docs column rather than `0`,
because it never tried to fetch.

> [!NOTE]
> A run that degraded but still produced something useful reports as
> **partial**, never as failed. A mid-run rate limit ends that way, with the
> brief written from what it managed.

### Controls

- **Run now** starts a research run. It has no keyboard shortcut on purpose:
  it is the most expensive thing in the app, and the history's cost column
  tells you what it comes to on this domain.
### What each job is called

Every run carries a name, and it is the same name in the menu that starts it and
in the history that reports it. The KIND column shows it.

| Job | What it does |
|---|---|
| **Research** | Reads the morning's press and writes the brief. |
| **Deep read** | Reads what Venona has fetched and not yet got to, however old. Writes no brief. |
| **Name review** | Works through the names that might be two spellings of one thing, and settles what it can. |
| **Second source** | Goes looking for another publisher on the relationships only one source has reported, worst-blocked names first. |
| **Anchors** | Suggests where names with nothing attached to them belong. |
| **Definitions** | Looks again at the names a brief left unexplained. |
| **Glossary** | Finds meanings for every short form still waiting for one. |
| **Questions** | Writes study questions from what Venona has read. |
| **Definition** | Finds what one short form stands for, and where that was said. |
| **Reader brief** | Writes one reader's own brief from the day's reading. |
| **Ingest** | Reads one page you named and files what it says. |
| **Rewrite** | Writes a brief again from reading that is already in. |
| **Export** | Writes everything Venona has read into a folder you can open elsewhere. |
| **Setup** | Researches a new subject and writes the configuration for it. |

Every job in the top half of that table can be started from the menu beside
**Run now**. The rest happen on their own, or from somewhere the thing they are
about already lives.

- **Deep read**, in the menu beside Run now, reads without writing a brief.
  Venona keeps whatever it fetches, but only reads the last day and a half of
  it before a brief is written, so older articles pile up unread. A deep read
  works through that pile and turns it into names and relationships. It writes
  no brief and sends nothing to anybody.

  Three things you set when you start one: how far back to read, how much work
  it may do before it stops, and whether it goes looking on the web for older
  articles rather than reading what is already here. The last of those is the
  expensive one, and it costs the same whether it finds anything or not.

  Venona reads one thing at a time, so a deep read still running in the morning
  means no brief that day. Give it a limit, or stop it before then. Stopping is
  safe and so is running out: it keeps what it has read and the next deep read
  carries on from there.
- **Cancel** appears while a research run is going.
- **Clear history** hides every listed run. Nothing is deleted, and the
  hidden toggle brings them back. `×` on a row hides just that one.
- `L` loads more history.

### One run

Opening a run shows what happened: per-step status, counts, timings, errors,
what the run had to say for itself, and the live transcript while it is going.

What it has to say comes in three blocks, and only the first of them means
something went wrong. **Why** is the trouble: work the run set out to do and
could not, so you got less than you should have. **Needs you** is work waiting
on a decision, with a link to the screen where you make it; it comes back every
run until you do, and it is not a fault. **Notes** is everything else worth
saying, with nothing to do about it. A run that read the window and found
nothing new is a normal night, not a broken one, and reports itself that way.

Its **metrics** page shows where the time and money went: per-call cost and
latency, the step timeline including queue time, per-source yield, and the
settings it ran under. The profile hash there tells you whether the brief
changed because you changed the relevance profile or because the news did.

### The schedule

The nightly run is set in the **Settings** document, with its timezone and an
on/off switch. The Runs page shows the next firing.

---

## 5. Tuning what Venona reads

**Admin, Configuration.** The sidebar holds **Overview**, **People**,
**Agents**, **Data**, **Notifications**, **Configuration** and **Domain setup**.
Configuration is an index of every document, grouped by what you would be trying
to change, each with a sentence saying what it is and when it last changed.
Documents are edited as raw YAML and Markdown rather than forms, because their
comments matter and a form would destroy them on save.

`⌘K` from inside the editor lists every document without typing, which is how
you move from one to the next without going back to the index.

| Group | Document | What it is | Saving |
|---|---|---|---|
| What Venona reads | **Sources** | The publications it pulls from, and the sweeps that look past them | A feed switched off after repeated failures stays off until retried |
| What Venona reads | **Research prompt** | The themes it searches for beyond the publications it already follows | Next run |
| What counts as important | **Relevance profile** | What Venona thinks matters. The one that decides what gets in | Takes effect on the next run |
| What counts as important | **Scoring rubric** | The story kinds and their weights | A weight re-ranks stored results free; a description needs a fresh run |
| The names it recognises | **Types and relationships** | The kinds of names Venona recognises and the relationships it may record | Checked on save, applied at once; a retired relationship leaves the graph on the next rebuild and returns if re-added |
| The names it recognises | **Anchors** | The graph's fixed starting points | Saving is not loading; press **Apply** |
| The names it recognises | **Lexicon** | Abbreviation expansions and the acronym ignore list | Next run; the non-distinguishing list also decides which near-identical names may merge |
| How it writes | **Brief voice** | How the brief is written: structure and judgement, not selection | Next run |
| How it writes | **Brief sections** | The standing sections of the brief, in reading order | Renaming a section leaves the archive with both headings |
| How it writes | **Learning prompt** | How generated study questions are written | Next run |
| Who it reads for | **Domain** | Who Venona reads for here; the short name is frozen | Next run |
| How it runs | **Settings** | Run limits, model choices and the schedule | Most fields apply on save. No secrets here; this document renders in a browser |

A last group, **What it sends the model**, holds the eight assembled prompts.
They are built from the documents above and are read only.

> [!CAUTION]
> **Apply** on the Anchors document replaces the anchor connections: every one
> is deleted and rebuilt from the file. It previews what that means before
> writing anything.

### The profile is not the readers' emphasis

Two different things share the word relevance:

- The **Relevance profile** document is the domain's thesis, prose the model
  reads while it reads articles. It decides what gets into the shared brief,
  for everyone.
- Each reader's **emphasis and follows** (reader guide, section 9) re-rank the
  already-scored stories for that one reader, free, at read time. They never
  touch the profile, and yours does not touch theirs.

In the **Scoring rubric**, each story kind carries a description and a label
with a plain-words line. The description is the model's scoring contract; the
label and plain line are what readers see in their own emphasis form. Write
the labels for readers, and never assume the two say the same thing.

### The editor

`⌘S` saves. Drafts live in your browser until saved and survive a reload. The
editor warns rather than overwriting when the file changed underneath you,
and it flags a draft written against an older version of the file before
letting it clobber anything. Hover **Save** for what that document's change
costs.

Each file opens with its own explanatory header folded into a single line; click
the arrow in the left margin to read it. `⌘F` searches inside the document, and
the arrows beside a line fold the block under it.

**Saved versions** at the foot of the editor lists the last twenty versions of
that document, with when each was saved and how its size compares to the file as
it stands. **Restore** loads one into the editor; nothing is written until you
press Save, so you can look at an old version and walk away from it. If you have
unsaved edits or a draft from a run on screen, it asks before replacing them.

---

## 6. People and access

**Admin, Overview** is the domain at a glance: pending requests, user counts,
agent tokens, model spend over the last month, and recent authority changes.
It has no controls of its own; every row links to People.

**Admin, People** is where things happen:

- **Access requests.** Approving one creates a `reader` account and shows an
  invite line to send. Venona sends no mail, so sending it is the part done
  by hand. Declining closes the request quietly, and they can ask again.
- **Make reader** demotes an administrator. They keep their account and
  sessions; they lose the admin surfaces and every write.
- **Suspend** ends someone's access on their next request and refuses every
  agent token they own. **Reinstate** restores them with the role they had,
  and their tokens start working again too, so revoke any you do not
  recognise first.
- The **authority log** records every role change and by whom; changes made
  from the terminal show as `cli`.

Sign-in is through the hub, one account across every domain a reader can
reach. The role the hub holds for them lands here when they sign in; a local
suspension always wins over it.

**Subscriptions, when this domain charges.** People grows a subscription
column, so you can see who is in trouble with a payment without leaving the
screen. There are no controls on it, and that is deliberate: payments live at
the hub, and what you see here is a copy of what the hub last told this domain.
Editing this domain's database would be overwritten within minutes.

To act on a subscription, go to the hub. **Whether this domain needs a
subscription at all** is a switch there too, on its Settings page: off, everyone
who can sign in reads everything, which is right for a domain that takes no
money. Nothing on this screen can change it, and setting the column by hand in
the database will be overwritten within minutes.

**To let someone read without paying**, run `venona users comp --email ...` on
this domain rather than at the hub. That is the one thing about a subscription
which is set here, and it is set here because the hub sells: it has no way to
describe an account that pays nothing, so a complimentary account there reads as
somebody who never subscribed. Marked complimentary, nothing the hub sends can
take it away, and it carries no end date, so it does not expire quietly.
`venona users uncomp --email ...` withdraws it, and their next sign-in picks up
whatever they have actually paid for. `venona users ls` shows who is on what.

Three states look similar and mean different things, which matters when
somebody writes to you:

- **Not started.** They have an account and have never subscribed. They see the
  subscribe screen and nothing else, because they have no briefs to keep.
- **Ended.** They subscribed once. Every brief up to the day they stopped is
  still theirs and still readable; what they have lost is everything after it.
- **Payment failed.** They are still a subscriber and still have full access
  while the payment is retried. Nothing needs doing by you.

If somebody paying says their archive stops on an old date, the copy here is
stale rather than wrong, and it repairs itself on the next sync from the hub.
`docs/RUNBOOK.md` has the longer version, and the checklist for turning
charging on in the first place.

---

## 7. The data

**Admin, Data** shows what this domain is holding, in plain words and biggest
first: documents, names, relationships, definitions, briefs, runs, study
records, with a total. Below that is how to take a copy away with you, and
below that the one hard delete.

### Saving a copy

**Export for Obsidian** writes everything Venona has read and learned as a
folder of Markdown files, one for each name, and keeps the file so you can
download it again later. Each copy is named for the day it was made.

Open the folder in [Obsidian](https://obsidian.md) and choose *Open folder as
vault*. Every relationship is a link you can follow, the graph view is coloured
the way Venona colours it, and each brief reads as it does here with every
programme and short form linked. A name that nothing has defined says so, in
those words.

The folder carries the record of each article it learned from -- the headline,
who published it, when, the address, and the exact sentences that back a
definition or a relationship. It does not carry the articles themselves.

A short form Venona has seen is written where you read it, not only in the
note's properties: a note headed *Australian Defence Force* says it is also
written **ADF**, and if a sentence somewhere says where that short form comes
from, the note quotes it.

Venona keeps the few most recent copies and removes older ones to save room. A
copy that has been removed still shows in the table, so you can see that there
was one.

Writing a copy takes a few minutes and keeps going if you leave the page. You
are told when it starts and when it lands, and **Settings, Notifications** can
send that to wherever you already get alerts, so you can start one and walk
away. Both endings are reported, including the one where it could not finish.

### Delete all data

> [!CAUTION]
> **Delete all data** is not a hide: nothing in Venona brings it back. The
> confirmation states the real counts, names what survives, and requires you to
> type `delete everything`. It refuses while a research run is in flight,
> because that run holds results that would land after the delete, and while a
> copy is being written, because that copy would otherwise be a complete record
> of what you just deleted.
>
> Copies you have already saved are files on this machine and are left alone.
> Export first if you want one.

---

## 8. Being told when something breaks

Everything Venona knows about its own failures is on these pages, and that only
helps while somebody is looking. The run happens overnight. **Settings,
Notifications** under Admin is where you say how Venona reaches you when it
does not go well.

Add a destination: a web address from Slack, Discord, ntfy or healthchecks.io,
or an email address. A new one starts subscribed to every failure, and each
thing it gets told about has its own switch, so you can keep the ones you act
on and mute the ones you do not.

**The two quiet failures are the reason this exists.** A run can succeed, write
a brief, and look completely normal while something is wrong underneath it:

- **A source stopped answering.** A publication that fails three times running
  is dropped and stays dropped until you turn it back on. Nothing about the
  brief looks different; it is just thinner. Three quiet nights and the reading
  has hollowed out with nothing having said so.
- **Nothing new is landing.** Every publication answering, no failures anywhere,
  and Venona finding nothing worth writing about several days in a row. That is
  usually what you have told Venona matters, or how far back it reads, rather
  than the sources.

They are separate switches because they are fixed in different places.

**Silence as the alarm.** "Every run that finishes cleanly" sends a message on
success, which sounds backwards until you point it at healthchecks.io and tell
that service to alert you when the pings STOP. A machine that never came back
sends no alerts of its own; the absence of a ping is the only thing that
catches it.

**Send a test** before you rely on any of it, and read what comes back. If a
destination fails three times in a row Venona stops sending there and says so,
on this page and with a mark beside Notifications in the sidebar, because the
thing that broke is the telling and nothing else would mention it. **Try this
one again** starts it sending once you have fixed the address.

**Email is not set up yet.** Venona has nowhere to hand a message to, so an
email subscription is accepted and held rather than refused: what would have
been sent is kept and goes out once there is a way to send it. The page says
so rather than pretending. Readers see the same honest sentence on their own
Settings, Notifications page, where they can ask to be told when the shared
brief and their own brief are ready.

---

## 9. Which model does the thinking

**Admin, Model.** Everything Venona writes, reads and decides is done by a
model service, and this page is where you choose which one and how it signs in.

### Choosing one

The page lists every service Venona can use, whether it is installed on this
machine, and whether it has a way to sign in. **Change** moves the instance
from one to the other. It is a real change, not a preference: the next run
thinks with the service you picked, and it will not read exactly like the last
one.

You cannot change it while a run is going. The page says what is running and
links to it rather than making you find out by pressing the button.

Every tuning choice you have already made survives the move. The settings
document names model tiers, and each service resolves those into its own
models, so a tier you raised stays raised.

### Signing in

Two ways, and which you get depends on the service.

**A pasted credential.** Some services take a long-lived token you mint on your
own machine. Paste it into **Connect** and Venona puts it to work before
storing it, by asking the service a one word question. A credential that does
not work is refused here rather than by the next run. That takes a few seconds
and costs a fraction of a cent.

**The service's own sign-in.** Others keep their credential in their own files
and refresh it there, so there is nothing to paste. The page says so instead of
showing you an empty box, and you sign in with that service's own command on
the machine Venona runs on.

If a credential is already in the environment when Venona starts, the page says
so. Connecting one here replaces it: Venona stops passing the environment's
copy through, so the two cannot quietly disagree about which account is being
billed.

### Subscription or metered

**Subscription** means a flat rate, where the thing that stops you is a rate
limit rather than a bill. **Metered** means you pay per word, and Venona caps
what a single run may spend so one press cannot cost more than you meant.
Nothing else about the app changes; the same page moves between them.

A service that cannot do one of the two says so, and Venona refuses to start
at all if it finds a metered credential on an instance set to subscription.
That refusal is deliberate. Silently billing per word against a flat-rate plan
is the one mistake here that costs money without telling anyone.

### What it can reach

Some services can run code. Those get a filesystem of their own before they
start: their own working files and nothing else on this machine, so a run
cannot read your documents, your settings, or the credential it signs in with.

The page says which of three states this instance is in, including the one
where it worked. That is on purpose. The restriction is invisible when it
succeeds and invisible when it fails, because the work happens either way, so
the only way to know is to be told.

### When it is not answering

Every reading screen says the model is not answering, without naming a company
or asking a reader to fix anything. This page is where the difference lives:

- **Nothing connected.** A state you put the instance in. Nothing improves
  until you connect something, and the app says so rather than pretending to
  keep trying.
- **Waiting.** The service is having a bad morning. Research runs wait it out
  rather than failing; a lookup gives up quickly.
- **Signed in, but calls are failing.** The most confusing one, and the reason
  the page reports both. A sign-in check reports a live session for a
  credential that no longer works. What knows the difference is whether calls
  are landing, and that is reported separately.

---

## 10. What costs money

What this costs depends on how the model service is paid for (section 9).

**On a subscription** the rate limit binds before any bill, so spend is
measured and never capped. The figures below are what the work would have cost
at list price, which is useful for comparing one action against another and is
not a bill.

**Metered**, the dollars are real and Venona caps what one run may spend. A run
that reaches the cap stops asking for more and ends **partial** with what it
managed, the same way it does when it runs out of calls. It never ends failed
for this reason: a run that did most of its work and then stopped is not a run
that broke.

The Overview panel shows the last month either way, and it does not mix the two
kinds of number together.

| Action | Roughly |
|---|---|
| A research run | The largest spend in the app. The history's cost column is this domain's own number |
| One acronym lookup (`L`, or **Look this up** on a card) | About $0.13 |
| **Look up all** over the undefined queue | Tens of dollars and hours; quotes a measured price first |
| **Generate questions** | Priced from your own recent calls before it starts |
| A reader's brief rewrite | One light model call, capped at three per reader per day |
| **Refresh definitions** | A handful of lookups |
| **Rebuild graph**, re-scoring | Free |
| Anything an agent asks for | Free; it only reads |

**Look up all** is the only control that can spend tens of dollars from one
press, and it quotes a measured price first. That quote comes from this
instance's own recent lookups on the service it is using now, so it is a real
figure rather than an estimate from somewhere else. A run that hits the
ceiling, whichever kind, backs off and ends **partial** with what it managed.

---

## 11. The extension for your readers

The extension puts Venona's underlines on third-party articles. Readers set
it up themselves (reader guide, section 11); your half is the build.

Build it once and hand out the folder:

```
cd extension && npm install && npm run build
```

then Chrome's **Load unpacked** pointed at `extension/dist`.

Readers connect it themselves with **Connect with Google** on its options
page: the sign-in is exchanged for a credential scoped to exactly what the
extension does, the four paths that draw underlines and cards and list your
sites. A leaked extension credential cannot read briefs, search, or touch
anything under Admin. To hand-issue one instead, for a domain without Google
or as a break-glass:

```
uv run venona mcp token new --label "extension" --scope annotate
```

The same command without `--scope` makes an agent key instead (reader guide,
section 12), though most people connecting an assistant never need one: giving
the client the address is enough, and they sign in. Keys are for an assistant
that cannot open a browser. `venona mcp token revoke` takes either kind back,
and so does Disconnect on **Settings, Agents**. You see your own there, and
everyone's if you are an admin.

The sites the extension offers come from your own **Sources**: it lists the
places this domain has actually pulled articles from, and each reader grants
their browser permission site by site.

The toolbar's add-this-page action shows only under a credential that may
use it; it posts into the research lane, so it waits behind a research run.
Under a reader's credential the button opens the app instead.

---

## 12. Admin keyboard keys

The reader guide's section 13 has the global and reading keys. Yours, on top:

| Where | Keys |
|---|---|
| Global | `Alt 6` Admin, then Curation, Runs or Model on its rail |
| Curation | `[` / `]` previous / next queue |
| Merges | `M` merge · `K` keep apart · `R` relate · `1` to `9` choose a relationship · `X` swap survivor · `S` or `→` skip · `←` back · `U` undo |
| Definitions | `Enter` do the suggested thing · the full table in section 3 |
| Runs | `L` load more history |
| Config documents | `⌘S` save · `⌘F` find in the document · `Esc` close the find bar |

The bare letters live only inside their queues; no global shortcut is a bare
letter.

---

## 13. When something looks wrong

- **A feed went quiet.** The Sources document lists any feed switched off
  after three consecutive failures, with a **retry** button. Retry puts it
  back in the rotation without claiming it works; if it is still broken it
  earns three fresh strikes and switches itself off again.
- **"The model is not answering."** Every spending control disables with the
  reason in its tooltip, and the Runs page carries the notice. A short
  provider outage is waited out; runs resume on their own. **Admin, Model**
  tells you which of these it is, and it is worth checking before waiting:
  nothing connected does not recover on its own, and a service that reports
  itself signed in can still be failing every call.
- **A brief did not appear.** Open the run on the Runs page. Its step table
  says where it stopped and what the error was, and a **partial** run wrote
  what it could.
- **A reader reports a wrong underline**, or a missed one. Both are naming
  problems: the fix is a merge or a definition in Curation.
- **The graph disagrees with the record.** The graph is a drawing of the
  record and can always be redrawn: **Rebuild graph** on Curation, or
  `venona graph rebuild` from a terminal.
- **Which version am I running?** The foot of **Admin, Overview** says, as a
  release number and the exact build beside it: `Version 1.0.0 · build a1b2c3d`.
  It reads `build local` on a machine nobody deployed from. Worth checking
  first whenever a screen does not behave the way a note or a colleague says it
  should, because the answer is sometimes that the change has not reached this
  instance yet.
- **The guide pages are blank.** The two guide files ship inside the app's
  deployed image; a build that lost them renders "the guide is not installed"
  while everything else works.
