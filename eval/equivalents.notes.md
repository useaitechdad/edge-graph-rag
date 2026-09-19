# Amendment 1 — how each equivalent was found

The audit trail behind `eval/equivalents.json`. The rule it applies, and why the amendment
exists at all, are in `eval/DESIGN.md` under "Amendment 1 — equivalent answer passages"; this
file is the working: for every one of the 24 questions, the answer in a few words, the terms
searched for, what was accepted, and what was looked at and left out.

**This was written blind.** No retrieval output of any kind was consulted — no `/search` call,
no dev server, no eval or ingest receipt, no prediction about what an embedding model would
find. The only inputs were the question, its own gold answer passage, and the corpus text. The
method was plain lexical search of `corpus/text/*.txt`, over indexed pages only
(`mco-mib-project-management` 58–105 is the Phase I reprint and is not indexed, so nothing on
those pages was eligible), for the answer's distinctive values, names and phrases plus the
variants this corpus forces: spelled-out numbers, the synonyms the reports use for themselves,
and the OCR damage — `mars-polar-lander-ds2-loss` breaks words apart, `mars-program-independent-assessment`
glues them together (`isprematureshutdownof thelanderengines`), `genesis-mib-vol-i` carries
soft hyphens inside words.

**The bar, applied the same way every time:** a reader given this passage alone, and the
question, could give the full answer the question asks for. For a multi-hop question the
passage has to state the final attribute for the correct target entity. Half an answer is not
an answer; when it was arguable, the passage was left out and the reason recorded below.

32 passages were accepted, across 12 of the 24 questions.

---

### q01 — 4.45, because ground software reported impulse in pound-seconds where newton-seconds were specified

- **Terms:** `4.45`, `Newton-sec`, `Newtons`, `pound`, `impulse bit`, `small forces`, `underestimat`, `AMD`, `low by a factor`.
- **Accepted:** `mars-climate-orbiter-mib-phase-i` p14 (the ∆V's were low by a factor of 4.45 because they were in English units), p17 (the impulse bit was reported in pounds where newton-seconds were expected, 1 pound force = 4.45 Newtons).
- **Rejected, borderline:** `mars-program-independent-assessment` p19 — English rather than metric units, but no factor and no statement of which quantity was wrong.

### q02 — a periapsis altitude of about 57 km

- **Terms:** `periapsis`, `57`, `km`, `after the fact`, `loss of signal`, `corrected`.
- **Accepted:** none. The corrected after-the-fact estimate is stated once. Other pages give the planned or predicted periapsis, which is a different number answering a different question.

### q03 — a spurious touchdown signal from the Hall Effect sensors at landing-leg deployment, latched by the software

- **Terms:** `Hall Effect`, `touchdown`, `spurious`, `leg deployment`, `premature shutdown`, `40 meters`, `transient`.
- **Accepted:** `mars-program-independent-assessment` p21 (premature shutdown due to spurious signals generated at leg deployment), `mars-polar-lander-ds2-loss` p134 (the software recorded the spurious leg-deployment signals as valid touchdown events and shut the engines down).
- **Rejected, borderline:** `mars-polar-lander-ds2-loss` p13 — "vulnerability of the software to transient signals" names the software half but not the leg-deployment/Hall Effect mechanism the question asks for.

### q04 — about 200 m/s; the aft-body designed for about 60,000 g, the penetrator for about 30,000 g

- **Terms:** `200 meters per second`, `impact velocity`, `aft-body`, `forebody`, `shock`, `g`, `withstand`.
- **Accepted:** none.
- **Rejected, borderline:** `mars-climate-orbiter-mib-phase-i` p10 — describes the microprobes crashing at about 200 m/s, but gives no shock loads, so half the question.

### q05 — the G-switch sensors were installed inverted and never fired the drogue mortar

- **Terms:** `G-switch`, `inverted`, `inversion`, `orientation`, `EST`, `drogue`, `proximate cause`.
- **Accepted:** `genesis-mib-vol-i` p18, p42, p45, p159. p45 and p159 are the same paragraph printed twice (summary and body), which the amendment treats as two passages because a chunk can only hold one of them.

### q06 — overheating of the spacecraft structure by the solid rocket motor exhaust plume

- **Terms:** `probable proximate cause`, `overheating`, `plume`, `solid rocket motor`, `structural failure`.
- **Accepted:** `contour-mib` p9 (the findings list, which states the probable proximate cause in full).

### q07 — the adapter plate was not bolted to the turn-over cart: the team did not verify the bolts were installed

- **Terms:** `TIROS adapter plate`, `TOC`, `24 bolts`, `fell`, `13 degrees`, `failed to follow procedures`, `proximate cause`.
- **Accepted:** `noaa-n-prime-mishap` p30, p39, p84.
- **Rejected, borderline:** `noaa-n-prime-mishap` p43 and p44 — the sentence that would answer it is split across the page break, so neither page states it whole.

### q08 — "Mission Success First"

- **Terms:** `Mission Success First`, `culture`, `way of life`, `vision`, `Faster, Better, Cheaper`.
- **Accepted:** `mco-mib-project-management` p9, p12, p14, p24, p32 — each states the name *and* that it is the culture change being proposed.
- **Rejected, borderline:** the same document's p13, p19, p20, p27, p44, p45, p46, p47 — the phrase appears in passing, as a heading or a label, without saying it is the culture change the board recommends. A reader shown one of those alone would be guessing.

### q09 — roughly 30% underfunded

- **Terms:** `underfunded`, `30 percent`, `Mars '98`, `price of a Pathfinder`, `cost`.
- **Accepted:** none. The assessment states its funding judgement once; elsewhere the report discusses cost pressure without a figure.

### q10 — 193 mph (311 kph) into the desert floor

- **Terms:** `struck the desert floor`, `193 mph`, `311`, `kph`, `high rate of speed`, `impact`.
- **Accepted:** none. Other mentions of the impact give the damage or the crater, not the speed.

### q11 — 3.0 g, per the SRC avionics requirements document

- **Terms:** `3.0 g`, `deceleration`, `Drogue Parachute Release Trigger`, `3.2.1.4.2.2`, `Avionics Subsystem Requirements`.
- **Accepted:** none. The requirement is quoted once, and paraphrases elsewhere give the sequence without the threshold.

### q12 — same as q05 (the multi-hop route to the same fact)

- **Terms:** as q05, plus `Lockheed Martin`, `Denver`, `Mars Surveyor '98` to confirm the passage is about the right capsule.
- **Accepted:** `genesis-mib-vol-i` p18, p42, p45, p159 — the same four as q05. Each states the proximate cause for the Genesis SRC by name, which is the final attribute for the correct target entity; the contractor hop is what the bridges are for, and the amendment adds no bridges.

### q13 — entry, descent and landing (EDL)

- **Terms:** `EDL telemetry`, `defensible`, `indefensible`, `entry, descent and landing`, `telemetry during EDL`.
- **Accepted:** `mars-polar-lander-ds2-loss` p31 (the project decided not to provide EDL telemetry), p138 (the lack of telemetry during EDL). Both name the phase in the report where the quoted line originated, which is what the question asks for.

### q14 — premature shutdown of the descent engines, from a software vulnerability to transient signals

- **Terms:** `probable cause`, `premature shutdown`, `descent engines`, `transient`, `Themostprobablecauseof` (the glued-together OCR variant).
- **Accepted:** `mars-program-independent-assessment` p21 — states the probable cause of the MPL loss with both halves, shutdown and the spurious signals behind it.
- **Rejected, borderline:** `mars-polar-lander-ds2-loss` p37 — "premature shutdown … was the cause of the loss of MPL" gives the outcome but not the software vulnerability, half of what the gold passage states.

### q15 — a worst-case combined radiative-convective plume heating environment of 50 suns

- **Terms:** `50 suns`, `1358`, `plume heating`, `radiative-convective`, `worst-case`, `APL`.
- **Accepted:** none. The figure appears once; other discussion of the plume analysis does not carry the number.

### q16 — reliance on analysis by similarity, inadequate systems engineering, and inadequate review

- **Terms:** `Root Causes`, `analysis by similarity`, `systems engineering`, `review process`, `Apply to one or more`.
- **Accepted:** none.
- **Rejected, borderline:** `contour-mib` p21, p22 and p23 — each discusses one root cause at length. The question asks what root causes the board identified, plural, and no one of those pages lists them all.

### q17 — TIROS and DMSP, moved from East Windsor, New Jersey to Sunnyvale, California

- **Terms:** `East Windsor`, `Sunnyvale`, `1998`, `consolidation`, `TIROS`, `DMSP`, `relocated`.
- **Accepted:** none.
- **Rejected, borderline:** `noaa-n-prime-mishap` p35 — names both programmes and the Sunnyvale plant, but not the 1998 consolidation or where they moved from.

### q18 — Christopher Scolese, Deputy Associate Administrator for Space Science at NASA Headquarters

- **Terms:** `Scolese`, `Chairman`, `Deputy Associate Administrator`, `Space Science`, `Mishap Investigation Board`.
- **Accepted:** none. The board roster states it once; the chairman's signature elsewhere carries the name without the post.

### q19 — the Human Factors Analysis and Classification System (HFACS)

- **Terms:** `HFACS`, `Human Factors Analysis and Classification`, `Reason`, `1990`, `latent`, `human error`.
- **Accepted:** `noaa-n-prime-mishap` p27, p41, p79 — each expands the acronym and says it is the system that board's analysis was built on.
- **Rejected, borderline:** the same document's p30 (the acronym alone, in a list of methods) and p113 (a bibliography entry) — neither would let a reader answer without already knowing.

### q20 — the centrifuge test, replaced by a drawing inspection performed by the SRC-AU PIE, an electrical engineer

- **Terms:** `centrifuge`, `Stardust drawings`, `verification by inspection`, `deleted`, `PIE`, `Electrical Engineer`.
- **Accepted:** none.
- **Rejected, borderline:** `genesis-mib-vol-i` p48, p171 and p179 — each gives the dropped centrifuge test or the inspection that replaced it, never both, and the question asks for both. p50 mentions the substitution without naming the test.

### q21 — airborne P-3 assets, USAF facilities at Diego Garcia, and other possible assets

- **Terms:** `SRM burn`, `telemetry coverage`, `viable options`, `unconvinced`, `P-3`, `Diego Garcia`.
- **Accepted:** none. The board's judgement on the alternatives appears once; elsewhere the report records that there was no coverage, not what could have been done instead.

### q22 — SM_FORCES, whose output should have been in newton-seconds

- **Terms:** `SM_FORCES`, `small forces`, `software application`, `English units`, `metric`, `Newton-sec`.
- **Accepted:** none.
- **Rejected, borderline:** `mars-climate-orbiter-mib-phase-i` p6 — names SM_FORCES and says the units were wrong, but says only "metric units", never newton-seconds, and the question asks which units the output should have been in.

### q23 — Dr. Michael Ryschkewitsch, of NASA Goddard Space Flight Center

- **Terms:** `Ryschkewitsch`, `Chairman`, `Goddard`, `Applied Engineering`, `Diaz`, `designated`.
- **Accepted:** `genesis-mib-vol-i` p34 (the appointment, naming the chairman and his centre), p215 (the announcement, same two facts).
- **Rejected, borderline:** the same document's p217 and p222 — name the chairman but not a NASA centre, which is half of what the question asks for.

### q24 — Root Cause 5.1: the G-switch sensor was not identified as having a critical alignment

- **Terms:** `Test as You Fly`, `test as you fly`, `Root Cause 5.1`, `critical alignment`, `Pointing and Alignment`, `Phasing Plan`.
- **Accepted:** `genesis-mib-vol-i` p46, p66 (the root-cause list, heading and 5.1 together), p180 (the root cause stated in full under its own heading).
- **Rejected, borderline:** `genesis-mib-vol-i` p179 — the "Test as You Fly" heading and the root cause it covers cannot both fit inside a 400-character quote on that page, so no admissible span states the pair. p162 and p164 state the alignment finding without tying it to the heading the question asks under.
