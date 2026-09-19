# questions.json — author's notes, predictions and review

DRAFT evaluation set for the plain-vector vs. vector+graph-walk retrieval experiment.
24 questions, ids contiguous `q01`–`q24`, single-hop first: 10 `single-hop` (control),
14 `multi-hop`.

Mechanical check (revision 2): **all 45 gold passages pass**, 0 failures — every
whitespace-normalised quote occurs in the whitespace-normalised text of exactly the named
page of the named document, every quote is 80–400 characters, and no gold passage sits on
`mco-mib-project-management` pages 58–105.

All 14 multi-hop questions cross documents. Seven (q11, q12, q15, q16, q17, q18, q20)
carry two bridge passages and need two hops.

Single-hop covers all seven documents: `mars-climate-orbiter-mib-phase-i` (q01, q02),
`mars-polar-lander-ds2-loss` (q03, q04), `genesis-mib-vol-i` (q05, q10), `contour-mib`
(q06), `noaa-n-prime-mishap` (q07), `mco-mib-project-management` (q08),
`mars-program-independent-assessment` (q09).

---

## Answer-clause classification

Each multi-hop question's **final clause** — the thing it actually asks for — is either
`specific` (it describes the answer passage's own subject, so the words of the question
resemble the words of the target page) or `generic` (it names an attribute every mishap
report in the corpus has, so the clause alone cannot pick a document; only the hop
identifies which mission is meant).

| id | label | final clause |
|---|---|---|
| q11 | `specific` | asks for the deceleration threshold an avionics box had to detect — the answer page's own subject. |
| q12 | `generic` | asks for a proximate cause; every report in the corpus states one. |
| q13 | `specific` | asks which mission phase the missing telemetry covered — described on the answer page. |
| q14 | `generic` | asks for the probable cause of a mission's loss; every report states one. |
| q15 | `specific` | asks for a worst-case plume heating figure — a phrase that occurs nowhere else. |
| q16 | `generic` | asks for a board's root causes; every report has a root-cause list. |
| q17 | `specific` | asks which programmes moved into a plant and from where — the answer sentence's own content. |
| q18 | `generic` | asks who chaired a board; every report has a chair. |
| q19 | `specific` | asks what a classification system is called — the answer page is about that system. |
| q20 | `specific` | asks which test was dropped and who inspected in its place. |
| q21 | `specific` | asks which telemetry assets a project failed to consider. |
| q22 | `specific` | asks for a named software application and the units it should have used. |
| q23 | `generic` | asks who chaired a board, and from which centre. |
| q24 | `specific` | names the motto, and the answer page's heading repeats it. Relabelled in review; see the end of this file. |

Split: **5 generic (q12, q14, q16, q18, q23), 9 specific.**

### Four matched pairs

The set now contains four pairs that run the **identical bridge chain** and differ only in
whether the final clause is specific or generic. That turns the specific/generic
distinction from an observation into a controlled measurement — if the graph arm's
advantage is real, it should show up inside these pairs, not just across the set.

| pair | bridge chain | specific | generic |
|---|---|---|---|
| A | MCO p9 → Genesis p216 (Lockheed Martin, Denver) | q11 | q12 |
| B | CONTOUR p24 (the borrowed telemetry lesson) | q13 | q14 |
| C | MCO p4 → CONTOUR p8 (Peter Sharer → APL) | q15 | q16 |
| D | MCO p9 → NOAA p27 (Lockheed Martin, Denver → Sunnyvale) | q17 | q18 |

q12's answer page (`genesis-mib-vol-i` p39) is the same page q05 reaches directly, giving
one clean direct-vs-indirect comparison on an identical target. The other three generics
land on pages no single-hop uses (`contour-mib` p9, `noaa-n-prime-mishap` p24,
`mars-polar-lander-ds2-loss` p13), each a different page of a document a single-hop
question already covers.

---

## What changed in this revision

**Dropped (instructed):**

- **old q12** (Delta-7425 → Mars Climate Orbiter launch mass). Agreed — the answer lived
  in figure-caption text and nobody asks a library for a launch mass by way of a rocket
  model.
- **old q23** (NPG 8621 draft → NPR 8621.1A root-cause definition). **Dropped rather than
  reworded.** The reason is that the corpus contains three *different* definitions of
  "root cause" — MCO Phase I p16 (NPG 8621 Draft 1), CONTOUR p21 (NPG 8621.1 as modified
  by OSMA) and Genesis p44 (NPR 8621.1A). Any wording natural enough for a person to ask
  ("how did NASA's definition of root cause change?") is a comparison, not a short
  checkable answer; any wording with one answer has to pin the target so hard that the
  hop stops doing work. The policy-lineage link is real and is documented below, but it
  does not make a fair question.

**Dropped (my call, to make room for generics without exceeding 14):**

- **old q15** (MPL/DS2 board chair → 43 years at JPL, Voyager/Galileo/Cassini). Biography
  trivia, and MPIAT p5 independently records that Casani chaired those reviews, so the
  hop was already half-free.
- **old q19** (Denver operations for four spacecraft → MPIAT resourcing finding). Its
  answer page paraphrases the question almost word for word, which makes it the worst
  question in the set for measuring anything.

**Kept and confirmed:**

- **q23 (formerly q16)** — I checked what `genesis-mib-vol-i` p11 actually says beside the
  name, as instructed. It reads: *"Frank Bauer Systems Engineering NASA Goddard Space
  Flight Center, Greenbelt, MD"*. `mars-climate-orbiter-mib-phase-i` p3 reads *"Frank H.
  Bauer ... Chief, Guidance, Navigation and Control Center ... Goddard Space Flight
  Center"*. **Name and centre both match, both stated in the corpus text**, so the hop is
  supported and the question stays. The identification is still name-plus-affiliation
  rather than an explicit "these are the same person" statement — true of every
  roster-intersection question in any corpus — but nothing here rests on outside
  knowledge.

**Added (4 new generic-clause multi-hops):** q12, q14, q16, q18, described above.

---

## Honest prediction, by subtype

Recorded before any retrieval code exists.

**Specific clauses (9): I expect plain vector search to reach the answer passage in 8 of
9.** The exception is **q11** — `genesis-mib-vol-i` p169 contains no Mars, Denver or
contractor vocabulary at all, so every strong surface term in the question pulls toward
the MCO report instead. For the other eight the answer page repeats the question's own
words ("plume heating", "centrifuge test", "classification system", "English units"), and
I expect plain retrieval to succeed.

**Generic clauses (5): I expect plain vector search to reach the answer passage in 2 of
5.** Predicted successes: **q12** and **q16**, because the natural disambiguators rule 5
requires — "the sample-return capsule", "the comet mission" — inevitably leak the target
mission to a plain retriever. That leak is a real tension in the design and I would rather
name it than pretend the generics are clean. Predicted failures: **q14** (search terms
"comet mission / telemetry / probable cause" should return CONTOUR's *own* probable cause,
the wrong mission's answer, which is the most informative failure mode in the set),
**q18** and **q23** (board-roster pages share no vocabulary with the questions).

**Net: 10 of 14** — split **8/9 specific against 2/5 generic**, which is a contrast the run can actually measure, and which lives inside
four matched pairs on identical bridges. If the graph arm does not beat the vector arm on
q14, q18 and q23, the experiment has found nothing and should say so.

---

## A scoring hazard the scorer must handle

`mco-mib-project-management` reprints the whole of the Phase I report as Appendix B at an
offset of **+57 pages** (Phase I p3 → p60, p4 → p61, p14 → p71, p16 → p73). Six gold
quotes therefore appear **verbatim** in two places:

| question | gold cited | identical text also at |
|---|---|---|
| q01, q22 | `mars-climate-orbiter-mib-phase-i` p16 | `mco-mib-project-management` p73 |
| q02 | `mars-climate-orbiter-mib-phase-i` p14 | `mco-mib-project-management` p71 |
| q15, q16 | `mars-climate-orbiter-mib-phase-i` p4 | `mco-mib-project-management` p61 |
| q23 | `mars-climate-orbiter-mib-phase-i` p3 | `mco-mib-project-management` p60 |

The q11/q12/q17/q18 bridge quotes from Phase I p9 do not match p66 byte-for-byte (the
reprint's line breaks differ), but p66 carries the same paragraph, so a near-duplicate
exists there too.

A retriever that returns the reprint page has found the right *text* at the wrong
*document and page*. The scorer should decide this explicitly — either accept the reprint
page as an equivalent hit, or exclude pp. 58–105 from the index before the run. Silently
marking it wrong would penalise both arms arbitrarily.

---

## Leads I could not verify, or that do not hold as stated

- **"AFSPC colonel genesis:11 + noaa:24" — does not hold.** Two different officers:
  `genesis-mib-vol-i` p11 lists *Col. Michael Coolidge*, Air Force Space Command;
  `noaa-n-prime-mishap` p24 lists *Colonel James R. Horejsi*, Chief Engineer, Space and
  Missile Systems Center, Air Force Space Command. The shared entity is the organisation,
  not a person. No question written — "which officer served on both boards" has no answer
  in this corpus.

- **"Reason/HFACS noaa:77 ← genesis:97,154" — holds only in weakened form.**
  `genesis-mib-vol-i` contains no occurrence of "HFACS" or "Swiss cheese" anywhere
  (confirmed across all 231 pages). Pages 97 and 154 are bibliography entries listing
  Reason (1990, 1997) and Shappell & Wiegmann. The substantive link is Genesis p126, where
  the human factors appendix cites "(Reason, 1990; see Shappell and Wiegmann (2001)...)"
  in narrative prose. q19 uses p126, not p97/p154.

- **"DMSP: noaa:55,58 + contour:54,55" — verified as text, unusable as a link.** In the
  CONTOUR report DMSP appears only in the evidence-index tables (an ATK report on a DMSP
  STAR37A flight anomaly, and a DMSP F-10 mishap investigation report) and in the acronym
  list on p62 — appendix furniture with no narrative discussion. Not used.

- **"one operations room (mpl-ds2:19; mpiat:27)" — the phrase does not appear.** No report
  says the projects shared an operations room. What is stated:
  `mars-polar-lander-ds2-loss` p19 — LMA performed spacecraft operations from Denver for
  MCO and MPL "as they have been doing for MGS and Stardust";
  `mars-program-independent-assessment` p27 — the operations team was "managing four
  spacecraft (MGS, MCO, MPL, and Stardust) simultaneously with limited resources". The
  question built on this was dropped in revision 2 (see above), but the link itself is
  sound if it is wanted later.

- **"Delta II 7425: mco-phase-i:9,11 + contour:14,31" — page references partly wrong.**
  `mars-climate-orbiter-mib-phase-i` p9 says "identical Delta II launch vehicles" without
  the 7425 designation; the 7425 is on p11 (MCO) and p12 (MPL). In `contour-mib` it is on
  **p12**, not p14 or p31 — p14 says only "a Delta II launch vehicle". Also present, not
  in the lead: `mars-polar-lander-ds2-loss` p19, "Delta 11-7425" (OCR of Delta II-7425).
  No question now uses this link.

- **"Test like you fly: mco-pm:114,118 + genesis:19,55" — page references partly wrong,
  and the two documents use different wording.** `mco-mib-project-management` p114 is a
  cross-reference table of themes and p118 carries "Test Like You Fly" inside a quoted LMA
  finding; the four-line motto the lead is really about is on **p30**, which the lead does
  not list. Genesis consistently writes "**Test as You Fly**" (pp. 19, 35, 46, 55, 64, 67,
  168) — a near-match, not the same string. q24 uses mco-pm:30 and genesis:55 and is built
  on that wording difference.

- **"Don Savage contour:6,34 + genesis:13" — p6 not confirmed.** Savage is on `contour-mib`
  p34 (NASA Headquarters Public Affairs, under Advisors) and in `genesis-mib-vol-i` p13 and
  p16. No occurrence found on contour-mib p6. Not used.

- **"Lockheed Martin: Genesis Denver (genesis:17,21)" — p17 does not say Denver.** Genesis
  p17 and p21 identify Lockheed Martin Corporation acting through Lockheed Martin Space
  Systems as the industrial partner but do not name the site; Denver appears on pp. 13, 83,
  216 and 217, and the Waterton, Colorado facility on p223. q11 and q12 use p216.

- **Sackheim (mco-phase-i:3, mpl-ds2:13, mpiat:64–65) — verified, not used.** The clearest
  three-document person link in the corpus, but the only distinctive biographical fact
  (seven patents, 120+ papers) sits on `mars-program-independent-assessment` p65 in
  word-glued OCR — "Mr. Sackheimholdssevenpatentsandhaspublishedover120technicalpaperson"
  — too fragile for a fair gold passage. Worth revisiting if extraction is ever re-run.

- **Norvig (mco-phase-i:3 + mpiat:64) and Patrick Martin (contour:34 + genesis:11) — both
  verified, neither used.** Both are sound bridges, left out only to keep roster-based
  questions to two in fourteen.

- **NPG/NPR 8621.1 lineage (mco-phase-i:16,37; contour:8,18,21; noaa:113; genesis:17,39,44)
  — fully verified, deliberately unused.** The chain is real, but the corpus carries three
  non-identical root-cause definitions, which is why the question built on it was dropped
  (see above).

Verified leads used as given: the CONTOUR→MPL "defensible project decision" quote
(contour:24 ← mpl-ds2:22), Genesis naming MCO/MPL/TIMED/CONTOUR (genesis:19, 58, 62),
Stardust heritage in Genesis (genesis:40, 48, 50, 54, 164, 169), MPL IMU heritage
(mpl-ds2:121), Peter Sharer/APL (mco-phase-i:4 + contour:8/10), Frank Bauer
(mco-phase-i:3 + genesis:11), and the Lockheed Martin site chain (mco-phase-i:9;
genesis:216; noaa:27, 34).

---

## Remaining judgement calls

1. **Bridge concentration.** Four of the fourteen multi-hops (q11, q12, q17, q18) hop
   through `mars-climate-orbiter-mib-phase-i` p9, the Lockheed Martin hub. That is
   deliberate — it is the densest real link in the corpus and it yields two of the four
   matched pairs — but it does mean a graph that only learns one edge would score well on
   four questions. Worth watching in the results.

2. **The disambiguation leak.** Rule 5's requirement to disambiguate naturally ("the
   sample-return capsule", "the comet mission") is in tension with the generic-clause
   design, because the disambiguator identifies the target mission without the hop. q12
   and q16 are the two affected; I predicted plain search will solve both, and if it does
   that is a property of the question design, not evidence about the retriever.

3. **Roster identity.** q18 and q23 rest on matching a person or an organisation across
   two signature pages. Both are stated in the corpus, but a scorer should be aware that
   this is the corpus's own structure rather than an explicit cross-reference.

---

## Review before the freeze

Two corrections made by the reviewer, before any retrieval code existed:

1. **q24 was reworded and relabelled.** The draft described the motto without quoting it
   ("a four-line testing motto… the last line"), and its own note said the text was
   "deliberately withheld". That breaks the set's rule that no vocabulary is stripped to make
   plain search fail: someone who has read that report would simply name the motto. The
   question now quotes it, which makes its final clause `specific`, and the prediction for it
   flips to "plain search finds it".
2. **The generic prediction was miscounted.** The draft named two expected successes out of
   six and called it three. With q24 moved, the counts are 2 of 5 generic and 8 of 9
   specific, 10 of 14 overall.
