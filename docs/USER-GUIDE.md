# User guide

For reading Venona in the browser. Running it has its own guide: if you are an
administrator, the switch at the top of this page in the app moves between the
two.

---

## Contents

1. [What Venona does](#1-what-venona-does)
2. [Signing in](#2-signing-in)
3. [Reading the brief](#3-reading-the-brief)
4. [The archive](#4-the-archive)
5. [Lookup](#5-lookup)
6. [The graph](#6-the-graph)
7. [Search and the command palette](#7-search-and-the-command-palette)
8. [Studying what you read](#8-studying-what-you-read)
9. [Making the reading yours](#9-making-the-reading-yours)
10. [Your account](#10-your-account)
11. [The browser extension](#11-the-browser-extension)
12. [Agent access](#12-agent-access)
13. [Keys and the shell](#13-keys-and-the-shell)
14. [What costs money](#14-what-costs-money)
15. [When something looks wrong](#15-when-something-looks-wrong)

---

## 1. What Venona does

Every morning Venona reads the press your domain follows, picks out the names,
finds a sentence to back every definition it offers, works out how things
connect, and writes one **brief**.

Everything else in the app reads that output, or helps you read it.

| Where | What it answers |
|---|---|
| **Brief** | What happened, as prose, with every program and acronym hoverable |
| **Graph** | How things connect, and which article said so |
| **Archive** | Every brief before this one, searchable by body text |
| **Lookup** | Every acronym Venona has seen, with or without a sourced definition |
| **Learn** | Flashcards drawn from what you have been reading |

Two rules explain most of what you will see.

> [!NOTE]
> **A definition exists only if Venona can quote a sentence for it.** An acronym
> with no sourced definition gets a dashed underline and says it is undefined.
> Venona never guesses.

> [!NOTE]
> **A name reaches the graph only after two different publishers have used
> it.** Before that it stays off the canvas, and a connection is held back
> until both of its ends clear that bar. A young domain therefore has a sparse
> graph and a long "waiting for a second source" count. It fills in.

### Before there is anything

A domain that has not read anything yet shows one screen explaining that the
first run takes about six minutes, and the four steps it will take. The first
run is started by an administrator; once it lands, the brief appears on the
Brief page without a reload.

---

## 2. Signing in

Sign in with Google. One account covers every domain you can reach, and the
sign-in page sends you straight there when you already have a session.

If your account is not known here yet, the page says so and offers **Request
access**. That records your request for the person who runs this domain; it
grants nothing on its own, and they will come back to you.

Some domains also accept a pasted token instead; the sign-in page shows the box
only where that is offered.

Sign out from the gear menu or from **Settings, Account**. It ends every
session on every device at once, not just the browser you are in.

---

## 3. Reading the brief

**Brief** is the latest one. Each brief has its own address, so you can link to
it.

The document is a dateline, a title, a summary line, then the standing
sections in reading order.

### Yours, and everyone's

Once you have told Venona what matters to you (section 9), the brief page gets
a toggle: **for you** and **shared**.

- **shared** is the day's brief as written for everyone on this domain.
- **for you** is your own reading of the same day, laid out the same way under
  the same headings, ordered by the emphasis you set, with the names you follow
  at the top. Once a day Venona writes it up for you in full, and it credits
  everything it read, not only the stories it wrote about.

You never have to ask for it. Venona writes your brief for the day the first
time you open the reading page after new reading has landed, and often before
that, so most mornings it is simply waiting for you.

If it is still being written when you arrive, the page says so and the **for
you** tab shows a small spinner. The shared brief is ready to read in the
meantime, which is where the page leaves you. Yours takes a minute or two, and
Venona tells you when it is ready rather than pulling you out of what you are
reading. Under the **for you** tab while you wait is your reading for the day,
ordered for you, which costs nothing and is there immediately.

You do not normally need to ask for a rewrite. If your brief was written before
you changed what matters to you, a notice says so and a **Rewrite** button
appears to catch it up; the rest of the time there is nothing to rewrite,
because the same settings over the same stories produce the same brief.

You get three writes a day in total, the automatic one included. After that the
button explains that the allowance resets tomorrow. Changing your settings
while a brief is being written means it will need writing again, and the
settings page says so at the time.

> [!TIP]
> If you have not seen the toggle, you have not been asked yet: the banner on
> the brief offers to take you there, and section 9 is the same trip.

### Hovering a mention

Every program, organisation and acronym Venona has read about is underlined.
Hover one and the card gives you the definition, the sentence that backs it,
where that sentence came from, recent sources, and a small map of its
connections. Click pins the card, `Esc` closes it.

Three controls sit in the card's footer:

- **Open** goes to that name's own page, described below.
- **Open in graph** focuses that name on the Graph page.
- **Follow** puts it on your watch list, the same list section 9 manages. Any
  story that mentions a followed name goes to the top of your reading.

Dashed underlines are acronyms with no sourced definition yet. Filling those
in is review work, done by whoever runs this domain.

### A name's own page

The card is a glance. **Open** gives you the whole of it at an address of its
own, so you can keep it in a second tab or send it to somebody.

It holds what it is and the published sentence that says so, everything it is
connected to with the publishers behind each connection, every article it
appeared in, and the briefs that named it. Every connection opens onto the
sentences that back it.

A connection only one publisher has reported is shown and labelled as one
publisher, rather than quietly left out. A name Venona has seen only once is
not connected to anything yet: it waits for a second publisher first, and the
page says so rather than looking empty.

Acronyms have a page too. It carries the definition and the articles the term
appeared in, but no connections, because an acronym is a term rather than one
of the things the map joins together.

Search results, the command palette and the graph's detail panel all lead
here, and so does the browser extension's card on somebody else's article.

### The side rail

**References** counts the names and definitions linked in this brief, and how
many acronyms are still undefined. The freshness meter shows how much of the
brief is under 24 hours old. The Top list is a contents list; clicking an
entry scrolls to that item. Sources sit at the foot of the article with date,
publisher and title, linking out.

### Moving between briefs

`←` older, `→` newer, or the arrow buttons in the bar. `A` opens the archive.

### The study nudge

When cards drawn from your recent reading are ready, a small toast offers to
take you to Learn. Dismiss it and it stays quiet for the rest of the day on
that device.

---

## 4. The archive

**Archive** lists every brief, newest first: date, time, title, summary, block
count. The row you were last reading is marked.

The filter box searches brief content: titles, summaries, item headlines, body
text. Matching rows show the snippet that matched and where, so you do not
have to open each one.

Dates are matched separately. Typing `2026-08-07` narrows by column, typing a
word searches the text, and you get both sets of rows.

Several briefs can share a day, which is why time has its own column.

The archive always shows the shared record of a day; your own reading lives on
the Brief page.

---

## 5. Lookup

**Lookup** is the glossary as a table: term, expansion, state. State reads
`undefined`, `rejected`, or the number of sources backing it.

Filter chips narrow it to everything, terms with a sourced definition, or
terms still undefined. The counts cover the whole glossary rather than the
page on screen, and the view says so when it is showing only the first page.

Clicking a term opens a panel with every reading of it Venona has considered,
including the ones it rejected and the reason. Defining terms and sending one
off to be researched are review controls, and they belong to whoever runs this
domain.

---

## 6. The graph

The graph is drawn from the record of what each article said, so it can always
be rebuilt from those records.

### Scope

The segmented control in the bar sets what the canvas covers:

| Scope | Shows |
|---|---|
| **all** | Everything on the canvas |
| **focus** | One name's neighbourhood, 1 to 3 hops. Needs a selection |
| **this brief** | The names one brief mentioned. Needs a brief as the way in |

The node count sits beside the depth chips, so you can see what the next depth
costs. Depth 3 from a well-connected name reaches most of the map.

### Getting around

Click a node to focus it, a neighbour to move the focus, the background to go
up a level. Click a **connection** for every article that stated it.

`N` and `P` step through names. `.` focuses the selection or leaves focus, `,`
goes up one level, `R` resets, `F` is full screen. `[` and `]` collapse the
two rails.

### The left rail: filters

- **hide unconnected**, on by default. A good share of what Venona has read
  about has no surviving connection, and drawing all of it spreads the layout
  to an unreadable zoom. The readout says how many are hidden and offers
  **show**.
- **spacing**: compact, normal, airy. Most visible in focus and brief scope.
- **types** and **relationships**: chips, filtering by kind of name and kind
  of connection.
- **minimum sources**: a slider from 1 to 5. Dashed connections rest on a
  single source.
- **Path finder**: two boxes and a depth. Answers "how is this program related
  to that agency" as a chain with arrow direction.

> [!NOTE]
> The readout above the canvas carries two separate undos. **show** restores
> the unconnected names. **reset** clears the chips and the slider.

### The right rail: detail

A selected node shows the same card the brief uses, plus how well sourced it
is. A selected connection shows where it came from instead.

### A blank canvas

It names which of three causes applies: the graph has not been built yet,
everything on it is unconnected, or the filters exclude everything. Each gets
the control that fixes it.

### Coverage

In **all** scope a note reads like `Showing 97 of 468 known relationships. 371
are hidden because one name is still waiting for a second source`. That number
moves as more articles come in and as the domain's reviewers tidy the names;
the reviewing itself is covered in the administrators' guide.

In brief scope the note describes that brief's own coverage instead.

---

## 7. Search and the command palette

`⌘K` opens the palette anywhere. It searches pages, names (ranked by how
connected they are, so a short common word surfaces the one with the most
links), and briefs by title, summary or date. Arrow keys move, Enter goes.

The palette only reaches a brief's title and summary. The row pinned at the
bottom, **Search everything, including brief text**, goes to the full search,
which reads the body.

A full search lives at its own address, so it can be bookmarked or pasted.
Name hits and brief hits stay in labelled groups rather than one merged list,
and brief hits carry a snippet per match.

The graph has its own search box on the canvas, which focuses the name you
pick.

---

## 8. Studying what you read

**Learn** turns what Venona has been reading into flashcards, spaced so each
one comes back just before you would forget it. Three sections sit under one
control: **study**, **progress**, **terms**.

### A study session

One card at a time: read the front, reveal, then grade yourself honestly.

| Key | Grade |
|---|---|
| `1` | Again. You did not know it; it comes back soon |
| `2` | Hard |
| `3` | Good |
| `4` | Easy. It will stay away longest |

`Space` or `Enter` reveals a card and moves to the next one. Multiple-choice
cards grade themselves: right counts as Good, wrong as Again.

Cards come in a few shapes: a term and its meaning, a sentence with the term
blanked out, a how-are-these-connected question, and multiple choice. Every
card is drawn from something Venona actually read and can show its source.

> [!NOTE]
> Questions written by the model reach your deck only after a person running
> this domain has read and approved them. Cards made directly from definitions
> and connections need no approval; they are the record itself, asked as a
> question.

### Progress

A day streak, what is ready now, what you reviewed today, and how many terms
you have studied. Below that, a bar chart of your reviews and a coverage view
by kind of name. Studied means you have seen it in a session; strong means it
is sticking.

### Terms

Three lists: what has not stuck yet and is worth another look, what is coming
back next, and what you know well. Click a well-known term to see it in the
graph.

What you study is yours alone. Venona records your grades to schedule your
own cards, shows the results only to you, and gives no one else a view of
them, administrators included.

---

## 9. Making the reading yours

The first time you sign in, Venona offers this on a welcome page. You can skip
it, everything works without it, and the same controls live under **Settings,
Relevance** whenever you want them.

Two parts:

- **What matters most to you.** For each kind of story this domain scores,
  say **less**, **normal** or **more**. Stories move up or down for you, but
  nothing ever disappears, and nobody else's reading changes.
- **What you want to watch.** Follow the programs, companies and people you
  want to keep an eye on. Search everything Venona has read about, start from
  the offered chips, or follow any name straight from its hover card. Any
  story that mentions a followed name goes to the top of your reading.

Emphasis applies when you press **Save**. Follows apply the moment you touch
them, here or on a card.

Together these feed the **for you** brief in section 3. **Reset to this
domain's defaults** puts everything back.

> [!NOTE]
> If how stories are scored here changes underneath you, your emphasis is
> reset rather than silently misread, and a notice on this page asks you to
> set it again.

---

## 10. Your account

The gear in the header, or `⌘,`. Three sections, and all of them are yours
rather than the domain's: **Account**, **Relevance** (section 9), and
**Agents** (section 12).

**Account** shows the address you are signed in as, how this session signed
in, your role, and your display name, which is yours to change. Your role is
set by an administrator.

The brief is written on the domain's schedule and read here. Under Settings,
Notifications you can also ask to be told when it lands, either by email or by
a web address from Slack or another app. Email needs whoever runs this domain
to set it up first, and that page says plainly whether they have.

Signing out ends every session, on every device.

**Subscription** appears here when this domain charges for access. It shows
what you are on and what you will be charged, and the date. Everything that
changes a payment method, switches between monthly and yearly, or cancels is
handled by our payment provider on their own secure pages, reached by the links
on that screen. Venona never sees or stores a card number.

**The trial takes a card.** Three days free, and on the third day it becomes a
subscription unless you cancel first. The date is on screen for the whole trial
so it does not arrive as a surprise, and cancelling during it costs nothing.

**If you stop subscribing, you keep what you paid for.** Every brief published
up to the day your subscription ends stays readable here, for as long as your
account exists. What stops is new briefs, and with them the graph, the study
cards, the extension and your agents. Subscribing again brings all of it back,
including the briefs published while you were away.

**If a payment fails**, a bar appears at the top of every page with the date we
will try again, and one button that goes straight to the card form. You keep
full access while that is happening. Occasionally the message is that your bank
wants to confirm the payment rather than that your card was declined: those are
different, and the bar says which. There is nothing wrong with your card in the
first case, and replacing it will not help.

---

## 11. The browser extension

Puts Venona's underlines on somebody else's article. Read a story on a site
you already follow and every program, acronym and organisation Venona has read
about is hoverable, with the definition and backing quote a brief would give
you. The extension never changes the text of the page it is on.

### Setting it up

It is loaded straight into Chrome rather than installed from a store: get the
built extension folder from whoever runs your domain, open
`chrome://extensions`, turn on Developer mode, and **Load unpacked**.

Its options page then needs three things:

- **Your domain's address**, ending in `/api`. One is the normal case;
  **Connect another domain** below it adds more, by hand or from a published
  directory, and each connects separately with its own credential.
- **A sign-in.** Press **Connect with Google** and the extension trades your
  ordinary sign-in for a credential of its own, scoped to drawing underlines
  and nothing more. It is stored locally in your browser and never syncs.
  Pasting a token by hand remains as the fallback, for domains that work
  that way.
- **Sites.** The extension offers the sites your domain has actually pulled
  articles from. Ticking one asks the browser for permission to read that
  site, and nothing is granted until you tick it. You can pause a site
  without giving the permission back, or untick it to withdraw it entirely.

### The toolbar button

With an administrator's credential it adds the page you are reading to
Venona's reading: fetch, store and analyse that one article, with the badge
showing a tick or a cross when it is done. With a reader's credential the
button opens Venona instead; adding articles is an administrator's control,
and it waits its turn behind a research run.

---

## 12. Agent access

Lets an assistant you already write with read what Venona knows. It gets the
same definitions the brief gives you, the same connections the graph holds,
and the same articles behind both, so a draft about a program uses the names
your sources use and can be checked against them.

> [!NOTE]
> **It can only read.** There is no way for an agent to start a run, spend
> money, or change anything. A connection that leaks reads your domain and can
> do nothing else to it.

### Connecting one

You sign in, the same way you signed in here. Nothing is copied or pasted, and
you do not need anyone else to set it up for you.

**Settings, Agents** has the address and the exact line for the common clients.
Give the client that address; it will send you to a page from this server that
names what is asking and what it will be able to read, and you approve it there.

The connection ends on its own after 30 days, and you can end it sooner from
the same page. Ending one takes effect on the assistant's next request, and you
can always connect again.

If an assistant cannot open a browser, it needs a key instead, and keys are made
in a terminal on the server. Ask whoever runs this domain.

### What an agent can ask for

Look a term up. Ask what a name is, and whether it is ambiguous. Ask what it
connects to and how many separate publishers back each link. Ask for the
articles behind a claim, with dates and the sentence quoted. Search the briefs
you have already been sent. Paste a draft and get back every name in it Venona
recognises, including the ones it deliberately cannot define.

That last one is the useful one: a term Venona reports as undefined is a term
your assistant should leave alone rather than guess at.

### When it does not work

Your client reports what went wrong, and the causes are few:

| What the client says | What it is |
|---|---|
| cannot reach the server | wrong address, or the domain is down |
| it needs to authenticate, and nothing happens | you are signed out in the browser it opened. Sign in to Venona there first, then try again |
| it asks you to sign in every time | the connection ended. They last 30 days, and connecting again is the fix |
| it connects but the tools are missing | you approved a different kind of connection. Disconnect it on **Settings, Agents** and add the address again |

If none of those fit, the address may be reachable while the pages that tell a
client where to sign in are not being served. That is a server problem rather
than anything you can fix from here, so tell whoever runs this domain: the
answer is in the deployment notes under *if a discovery fetch returns HTML*.

---

## 13. Keys and the shell

The footer carries the theme switch, which cycles light, dark and system, and
a **keys** button. In full screen the footer folds to a hairline whose colour
still tells you if something is running or wrong.

`?` opens the keyboard overlay, which lists exactly what is bound right now on
the page you are on.

### Global

| Key | Action |
|---|---|
| `⌘K` / `Ctrl K` | Search and jump |
| `?` | Keyboard help |
| `Esc` | Dismiss the topmost overlay |
| `Alt 1` | Brief |
| `Alt 2` | Graph |
| `Alt 3` | Archive |
| `Alt 4` | Lookup |
| `Alt 5` | Learn |
| `⌘,` / `Ctrl ,` | Your account |

The `Alt` keys follow the header nav, left to right. Administrators have three
more destinations after these; on a reader account those keys do nothing at
all rather than sending you somewhere and bouncing you back.

### Brief

`←` / `→` older / newer brief · `A` archive

### Archive, Lookup and Search

`/` focuses the filter box.

### Graph

| Key | Action |
|---|---|
| `[` / `]` | Toggle the filter / detail rail |
| `F` | Full screen |
| `R` | Reset the view |
| `,` | Up one level |
| `N` / `P` | Next / previous name |
| `.` | Focus the selection, or leave focus |

### Learn

`Space` / `Enter` reveal, then next · `1` to `4` grade the card, or pick an
answer on a multiple-choice card.

---

## 14. What costs money

For a reader: almost nothing, and never your money. The domain runs on its
administrators' subscription, and everything you read, search, study and
follow is free.

The one model call you can start yourself is **Rewrite** on your own brief,
three times a day. Everything an agent asks for over its connection is a read,
and free.

The controls that spend real money, like research runs and bulk lookups, live
with the administrators, and each one quotes its price before it starts.

---

## 15. When something looks wrong

- **The graph looks sparse.** Usually the two-source rule rather than weak
  reading. The coverage note on the canvas says how many connections are held
  back and why, and the number improves as more articles come in.
- **An acronym you need is undefined.** That is Venona refusing to guess. The
  people who run this domain can have it looked up; until then the dashed
  underline is telling you the truth.
- **A button is greyed with "the model is not answering".** The model behind
  Venona is having an outage. Reading is unaffected; the button comes back
  when the model does.
- **Your brief did not appear.** The shared brief may still be from yesterday
  if the morning run had trouble; an administrator can see exactly where it
  stopped.
- **A mention is underlined that should not be**, or one is missed. Both are
  naming problems that review fixes; tell whoever runs your domain.
