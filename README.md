# Sales Process Audit

Generates a scored dealership sales audit from DMS extracts plus a physical
showroom inspection. Produces a Word audit report and an Excel findings
workbook with exception lists that can be handed to a named owner unmodified.

---

## Quick start

```bash
pip install -r requirements.txt

# 1. drop the DMS extracts into input/
# 2. generate the blank Physical Audit Sheet and walk the showroom
python run_audit.py --make-sheet

# 3. save the completed sheet as input/Physical_Audit_Sheet.xlsx
# 4. run the audit
python run_audit.py
```

Or walk the showroom on a phone instead of on paper — see
[Walking the audit on a phone](#walking-the-audit-on-a-phone).

Outputs land in `output/`:

| File | What it is |
|---|---|
| `Sales_Process_Audit.docx` | The audit report — scorecard, findings, corrective actions, sign-off |
| `Sales_Process_Audit_Findings.xlsx` | Working paper — 13 tabs of evidence and exception lists |
| `Physical_Audit_Sheet_BLANK.xlsx` | Branch-wise Physical Audit Sheet (from `--make-sheet`) |

---

## Command line

```
python run_audit.py [options]

  -i, --input DIR        folder holding the DMS extracts     (default: input)
  -o, --output DIR       folder for generated reports        (default: output)
  -c, --config FILE      norms and weightage                 (default: config/norms.yaml)
      --physical FILE    completed sheet (default: <input>/Physical_Audit_Sheet.xlsx)
      --make-sheet       write a blank Physical Audit Sheet and exit
                         (add --branch NAME for that outlet's tab only)
      --prefix NAME      output filename prefix
  -v, --verbose          debug logging
```

---

## Input files

### Filename matching

Matching is deliberately strict, because a wrongly bound file produces a
plausible-looking audit built on the wrong numbers.

- **Case and separators don't matter.** `Test_Drive.xlsx`, `test drive.xlsx`
  and `Test-Drive.xlsx` are equivalent.
- **A trailing date or version is fine.** `Retails_29-08-2026.xlsx` matches
  `Retails.xlsx`.
- **Extra words are not.** `AD_Lost_Enquiry.xlsx` does **not** match
  `Enquiry.xlsx`, and `Test_Drive_Concerns.xlsx` binds to the complaints
  slot, never to the test drive extract.
- **Content is verified.** Each file must carry the columns the audit expects
  before it is accepted. A Booking export renamed `Enquiry.xlsx` is rejected
  with a warning naming the missing columns, not silently scored.
- **Unmatched files are reported**, so a typo in a filename is visible rather
  than causing a mysteriously missing pillar.

| Ref | File | Required | Feeds |
|---|---|---|---|
| F3 | `Enquiry.xlsx` | yes | Pillars A, B, H |
| F4 | `Booking.xlsx` | yes | Lines 2.2, 2.3, 3.2 |
| F5 | `Test_Drive.xlsx` | yes | Lines 2.1, 2.2, 9.1 |
| F6 | `Retails.xlsx` | yes | Pillars C, D, E, F, G |
| F7 | `Enquiry_Concerns.xlsx` | no | Line 7.2 |
| F8 | `Test_Drive_Concerns.xlsx` | no | Line 7.2 |
| F9 | `New_Vehicle_Delivery_Experience.xlsx` | no | Line 7.2 |
| F10 | `New_Vehicle_Delivery_Experience_30_Days.xlsx` | no | Line 7.2 |
| F11 | `Physical_Audit_Sheet.xlsx` | no | Pillar J |

A missing optional file does not stop the run. Its lines are marked **not
scored** with the missing file named, and are excluded from the index
denominator rather than scored zero — so a data gap never masquerades as a
failing branch.

---

## Configuration

Everything tunable lives in `config/norms.yaml`. Nothing else needs editing.

- **`dealership.branches`** — outlets in scope. Complaint extracts often carry
  other outlets of the same group; those rows are reported but excluded from
  the complaints-per-1000 rates.
- **`targets`** — enquiry target, retail target, sanctioned headcount, market
  share norm. Leave `null` if unavailable; the line stays unscored and says so.
- **`norms`** — conversion and process benchmarks. Set `rsa_penetration_pct`
  and `ew_penetration_pct` to your OEM norms to score those lines against a
  target rather than only reporting the observed rate.
- **`owners`** — who owns the corrective action for each pillar. These names appear
  in the Owner column of the action plan in both outputs. Change them here, not in
  code.
- **`signoff`** — the roles listed in the sign-off table of the report.
- **`weights`** — pillar weightage. **Must total 100**; the run aborts otherwise.
- **`grading`** — index bands and the governance response each triggers.

---

## Pillars

| | Pillar | Default wt |
|---|---|---|
| A | Demand generation and enquiry health | 11 |
| B | Funnel conversion health | 14 |
| C | Volume, order bank and market share | 13 |
| D | Manpower health and productivity | 9 |
| E | Stock and inventory health | 11 |
| F | Financial and profitability health (incl. SHIELD and RSA) | 14 |
| G | Customer experience health | 11 |
| H | Systems and process compliance | 5 |
| I | Demo fleet traceability | 2 |
| J | **Physical Audit Sheet** | 10 |

---

## The Physical Audit Sheet

The DMS cannot tell you whether the display cars are clean, whether
consultants carry a current price list, or whether the walk-in register is
being maintained. Those have to be walked and seen.

Two ways to do it: on a phone at `/m`, which is usually easier on a showroom
floor, or on the Excel sheet below if you would rather. They produce the same
file and score identically.

Either walk it on a phone at `/m` and export the sheet at the end, or fill in the
blank Excel sheet by hand. Both produce the same file and score identically.

**Everything is per branch.** One walk covers one outlet, and the blank sheet can
be generated for one outlet too:

| Where | How |
|---|---|
| Upload page | Pick a branch beside **Download blank sheet** |
| Branch page | **Blank sheet** on that branch's row |
| Phone | Choose the branch on the first screen; the export is named after it |
| Command line | `python run_audit.py --make-sheet --branch "AMRAVATI"` |

A sheet carrying all eleven tabs is awkward to work from on a single visit, and
it invites filling in the wrong outlet.

`--make-sheet` writes one tab per branch covering **62 items across 12
sections**:

1. **Exterior and ambience** — glow sign, frontage, parking, floor cleanliness, odour and noise
2. **Display vehicles** — count against norm, model coverage, cleanliness, price card on every car, VAP and RSA offer display, battery and readiness
3. **Demo and test drive fleet** — availability per model, insurance/PUC/fitness validity, condition, TD route and consent, TD register
4. **Uniform and grooming** — consultants, name badges, grooming, support staff turnout
5. **Walk-in register** — maintained at entrance, fields complete, **reconciled to DMS same day**, rostered allocation, non-converting walk-ins logged
6. **Follow-up discipline** — follow-up calendar in use, substantive call remarks, morning meeting records, lost-enquiry review, aged enquiry ownership
7. **Sales consultant kit** — current price list, brochures, finance scheme sheet, **RSA and extended warranty tariff sheet**, accessory catalogue, exchange evaluation form, quotation format, product knowledge spot check
8. **Customer amenities** — seating, water, washroom, kids corner and Wi-Fi
9. **Mandatory displays** — public price list, RSA/EW/VAP offer board, finance and EMI board, grievance officer and complaint box, OEM statutory displays
10. **Delivery experience** — dedicated bay, ceremonial setup, HOTO station, **RSA and EW certificates in the delivery folder**, feature explanation at handover
11. **Systems and safety** — DMS terminals, EV charger, fire extinguishers and first aid, CCTV, statutory licences
12. **Performance visibility** — target board, funnel board, **VAP and RSA/EW penetration board**, previous audit actions displayed

**Scoring:** Yes = 2, Partial = 1, No = 0. `NA` is excluded from both numerator
and denominator, so an item that genuinely doesn't apply at a small outlet
doesn't penalise it. Items flagged `CRITICAL` raise a corrective action
automatically when answered No, even if their section scores well overall.

---

## Walking the audit on a phone

The Physical Audit Sheet has to be walked, not exported. Filling a spreadsheet
while standing in a delivery bay is awkward, so the same 62 items are available
on a phone.

Open the deployed URL with `/m` on the end — for example
`https://sales-audit-process.onrender.com/m` — on whatever device the auditor
carries round the showroom. No app to install, no login.

### How it works

1. **Pick the branch**, put your name in, start the walk.
2. **Tap through the items.** Yes / Partial / No / N/A, four large buttons per
   row. Sections are tabbed across the top so you can walk the showroom in
   whatever order the floor allows rather than in the order the sheet lists.
   "What to look for" opens under each item when you need it.
3. **Record what you saw.** The evidence box opens by itself when you fail a
   CRITICAL item, because that raises a corrective action with a named owner
   and the branch will want to argue about it.
4. **Submit.** The audit is then held on the server against that branch.

The running score and the count of failed critical items update as you tap, so
the branch manager can be told the position before you leave the building.

### It keeps working without signal

Every answer is written to the phone before it is sent anywhere. If the
connection drops — a basement bay, a steel-roofed shed, a dead patch behind the
workshop — answers queue on the device and sync by themselves when the signal
returns. A banner says so while it lasts, and submitting is blocked until
everything has actually reached the server, so an audit is never half-delivered.

An interrupted audit can be resumed from the mobile home page. A submitted one
can be reopened if an answer needs correcting.

### Then, export the sheet and add it to the main audit

At the end of the walk, tap **Export as Excel sheet**. That file is the record of
the audit. Send it to the laptop however suits — the Share button offers WhatsApp,
email or Drive directly from the phone where the browser supports it.

On the laptop, upload it **together with the DMS extracts, in the same drop box**,
and run the audit. Pillar J is then scored from it.

One branch, one sheet. Walk three outlets, export three sheets, upload all three
at once — they are merged into a single workbook automatically, so nobody has to
paste tabs together in Excel.

Filenames are matched loosely, because a file coming off a phone picks up
decorations on the way. All of these are accepted:

```
Physical_Audit_Sheet.xlsx
Physical_Audit_Sheet_YAVATMAL.xlsx        <- what the phone exports
Physical Audit Sheet (1).xlsx             <- what a browser download makes of it
physical-audit-sheet-12-09-2026.xlsx
```

Anything starting with "physical audit" once case and punctuation are ignored.
Keep that much of the name and the audit will find it.

**If an answer was wrong**, reopen the audit on the phone, correct it and export
again. Where two uploaded sheets carry the same branch, the later one wins.

**A note on the automatic fallback.** If a run has no sheet attached at all, the
app falls back to whatever was captured on phones and still held on the server,
and the results page says so. That is a safety net, not the intended route — if
you would rather the audit only ever scored a file someone deliberately attached,
set `AUDIT_MOBILE_AUTOPICK=0` and the fallback is off. An uploaded sheet always
wins either way.

### What is stored

One small JSON file per branch visit, holding the responses, the observations,
the auditor's name and the date. No customer data and no photographs. The latest
**submitted** audit for a branch is the one that scores; drafts are ignored until
they are submitted.

Captures are not swept away with the run directories — a branch walked on Tuesday
is still there when Friday's extracts are pulled. On Render's free plan the
filesystem is ephemeral, so set `AUDIT_PHYSICAL_DIR` to a path on a persistent
disk if a walk needs to survive a restart; `render.yaml` has the block to
uncomment.

---

## The branch audit facility

A network audit tells you how the dealership is doing. A branch audit tells a
branch manager what to fix on Monday morning, and that is the one that gets
acted on.

`/branches` is the page for it. Pick an outlet, upload the extracts, run it.
Every file is filtered to that branch — including the complaint extracts — so a
branch report only ever sees its own records, and pillar J is scored on that
branch's showroom rather than on the network's.

The same page lists all the outlets in `config/norms.yaml` with the state of
their physical audit: walked or not, by whom, when, the physical score and the
count of critical failures, with links to view, export or correct each one. It
is the quickest way to see which branches are still owed a visit this month.

Each scope keeps its own pair of output files, so downloading a branch never
replaces the network report. From a results page you can switch between branches
without uploading again — the audit is computed on first click from the files
already uploaded and cached afterwards.

On the command line the equivalent is `--branch`, `--per-branch` and
`--list-branches`; see [Auditing one branch at a time](#auditing-one-branch-at-a-time).

---

## How the index works

```
line score      = weight × achievement %          (achievement capped at 100%)
pillar score    = Σ line scores
index           = Σ pillar scores ÷ Σ scorable weight × 100
```

Only lines with **both** an actual and a norm are scorable. The report always
states how many of the 100 weightage points were auditable, so a high index
from thin data can't be mistaken for a healthy business.

One deliberate exception: when a VAP field such as `RSA Scheme Reg ID` is
populated on **zero** invoices, that scores 0% rather than going unscored. A
nil is a finding, not a missing measurement.

---

## Project layout

```
<repo root>/
├── run_audit.py              command line entry point
├── START_AUDIT.bat           double-click to run the app on Windows
├── start_audit.sh            the same, for macOS and Linux
├── requirements.txt
├── Procfile                  gunicorn command for Render
├── render.yaml               Render blueprint (repo root, flat layout)
├── webapp/
│   ├── app.py                Flask routes, run isolation, cleanup
│   ├── mobile.py             the phone audit — blueprint mounted at /m
│   └── templates/            upload, branches, results, history, mobile pages
├── config/
│   └── norms.yaml            norms, weights, branches, grading bands
├── input/                    DMS extracts go here
├── output/                   generated reports
└── sales_audit/
    ├── config.py             config loading, input file registry
    ├── loaders.py            file discovery, reading, column normalisation
    ├── scoring.py            Line / Pillar / AuditResult primitives
    ├── metrics.py            pillars A–I computed from the extracts
    ├── physical.py           pillar J: checklist, sheet writer, scorer
    ├── mobile_store.py       phone captures: storage, live score, sheet build
    ├── exceptions.py         exception tables for the workbook
    ├── report_docx.py        Word report writer
    ├── report_xlsx.py        Excel findings writer
    ├── audit.py              orchestration, findings, corrective actions
    ├── history.py            run history, movement, trend
    └── cli.py                argument parsing
```

---

## Extending it

**Add an audit line** — append a `Line(...)` inside the relevant
`build_pillar_*` in `metrics.py`, adjust the sibling weight fractions so they
still sum to 1.0, and add a remediation string to `SUGGESTIONS` in `audit.py`.

**Add a physical check** — append a `CheckItem` to `CHECKLIST` in
`physical.py`. Section weights re-derive automatically from item weights.

**Add an input file** — add an `InputSpec` to `INPUT_FILES` in `config.py` and
any derived columns to `_normalise()` in `loaders.py`.

---

## Web version (deploy to Render)

The same audit runs as a web app: upload the extracts in a browser, see the index
and every finding on screen, download the report and workbook.

### Run it locally

**Windows — double-click `START_AUDIT.bat`.** That is the start option. It sets
itself up the first time (a minute or two), then opens the app in your browser
and prints two addresses:

```
  On this laptop : http://localhost:5000
  On your phone  : http://192.168.1.7:5000/m
```

Leave the black window open while you work; closing it stops the app.

The second address is the one that matters for the showroom walk. Type it into
the phone's browser — the phone must be on the **same Wi-Fi** as the laptop, and
Windows will ask once whether to allow Python through the firewall. Say yes on
private networks, or the phone cannot reach the laptop. The address changes if
the laptop moves to a different network, so read it from the window each time.

macOS and Linux: `./start_audit.sh` (run `chmod +x start_audit.sh` once).

Manually, if you prefer:

```bash
pip install -r requirements.txt
python webapp/app.py
# open http://localhost:5000
```

### Laptop or Render?

Both work; they suit different situations.

| | Laptop (`START_AUDIT.bat`) | Render |
|---|---|---|
| Phone must be | on the same Wi-Fi as the laptop | anywhere with internet |
| Laptop must be | switched on and running the app | off, irrelevant |
| Data goes | nowhere — it stays on the machine | to your Render instance |
| Captures survive | yes, kept in `output/physical/` | only with a persistent disk |
| Setup | double-click | push to GitHub, deploy once |

A branch walked on a phone while the laptop is closed needs Render. A walk done
with the laptop sitting in the branch office is fine locally, and nothing leaves
the building.

### Deploy to Render

1. Push this project to a GitHub repository.
2. In Render: **New → Blueprint**, point it at the repo, leave Blueprint Path empty.
   `render.yaml` sits at the repo root and defines runtime, build command, start
   command and health check.

   This assumes the project files are at the **repo root** — `requirements.txt`,
   `render.yaml`, `webapp/` and `sales_audit/` all at the top level. If you instead
   put everything inside a subfolder, add `rootDir: <subfolder>` to `render.yaml`
   under `plan:`, or the build will fail looking for `requirements.txt`.
3. Alternatively **New → Web Service** with:
   - Build: `pip install -r requirements.txt`
   - Start: `gunicorn --chdir webapp app:app --bind 0.0.0.0:$PORT --workers 2 --threads 4 --timeout 180`
   - Health check path: `/healthz`

### Pages

| Route | What it does |
|---|---|
| `/` | Upload form with the expected-file table |
| `/branches` | **Branch audit facility** — run one outlet, and the capture status of all of them |
| `/m` | **Physical audit on a phone** — pick a branch and walk the showroom |
| `/m/<id>` | The 62-item checklist itself |
| `/m/<id>/sheet.xlsx` | One captured audit as a Physical Audit Sheet |
| `/physical/sheet.xlsx` | Every captured audit as one sheet, for the command line |
| `/run` | Runs the audit, redirects to results |
| `/results/<id>` | Index, pillar bars, funnel, findings, corrective actions, full scorecard |
| `/download/<id>/report` | The Word audit |
| `/download/<id>/findings` | The Excel workbook |
| `/download/<id>/bundle` | Both, zipped |
| `/history` | Index trend across runs, run table, pillar movement |
| `/physical-audit-sheet` | Blank Physical Audit Sheet |
| `/healthz` | Health check for Render |

### Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `SECRET_KEY` | random per boot | Flask session signing — set it on Render so sessions survive a restart |
| `AUDIT_RUN_TTL_HOURS` | `6` | How long a run's files are kept before automatic deletion |
| `AUDIT_MAX_UPLOAD_MB` | `80` | Maximum total upload size |
| `AUDIT_CONFIG` | `config/norms.yaml` | Alternate norms file |
| `AUDIT_RUNS_DIR` | system temp | Where run directories are written |
| `AUDIT_HISTORY_DIR` | under runs dir | Where `history.json` lives — point at a persistent disk to keep the trend |
| `AUDIT_PHYSICAL_DIR` | under history dir | Where phone captures live — point at a persistent disk so a walk survives a restart |
| `AUDIT_MOBILE_AUTOPICK` | `1` | Set to `0` to require the exported sheet, instead of falling back to captures held on the server |

### Two things to know before sharing the link

**Uploads are dealership data.** Each run lands in its own directory and a background
sweeper deletes it after `AUDIT_RUN_TTL_HOURS`. Nothing is retained beyond that, and
nothing is written to a database. But there is no login: anyone with the URL can
upload, and anyone with a `/results/<id>` link can see that run until it expires. If
this goes beyond your own team, put it behind authentication first.

That matters more once the mobile audit is in use, because the URL goes out to
everyone who walks a showroom. Physical captures are not swept on the run TTL, so
anyone with the link can see and edit them. Put the service behind authentication
before handing the link to auditors outside your own team.

**Render's free plan sleeps.** After 15 minutes idle the service spins down, and the
next request takes 30–60 seconds to wake it. Free instances also have limited memory;
if a very large extract causes the worker to be killed, move to a paid instance or
reduce `--workers` to 1.

---

## Running it daily

Daily or month-to-date extracts work without any change to how you run it, but three
things need handling and the tool does all three automatically.

### 1. Partial periods are pro-rated

Conversion percentages are rates — a 12% enquiry-to-retail is 12% whether the extract
covers 5 days or 30. Volume norms are not. Judging 6 days of retails against "8 retails
per consultant per month" would make every branch look like it is collapsing.

So the audit reads the period from the data and scales monthly volume norms to the days
covered. Retail target, productivity per consultant and order bank cover are affected;
the line states the pro-rated figure it used. Below `min_days_to_score_volume` (default
7 days) those lines are reported but not scored at all, because a two-day sample cannot
fairly evidence a monthly target.

Live-enquiry ageing switches from a 30-day to a 15-day threshold when the extract spans
less than 45 days, so a single month's data does not report a false pass.

Tune all of this under `period:` in `config/norms.yaml`.

### 2. Runs do not overwrite each other

```bash
python run_audit.py --archive --label "MTD 24-Aug"
```

`--archive` writes the reports into `output/<period-end>/` so each day's files sit side
by side. `--label` tags the run in the history.

### 3. Every run is recorded, so you get a trend

Each run appends to `output/history.json`: index, per-pillar achievement, funnel counts
and open action count. Re-running the same period replaces that row rather than adding a
duplicate, so an accidental double run does not distort the trend.

```bash
python run_audit.py --history
```

```
Period end  Label           Index  Band  Enquiries  Test drives  Bookings  Retails  Actions
2026-08-10  MTD 2026-08-10  42.0   Red   2,167      701          258       82       17
2026-08-17  MTD 2026-08-17  43.2   Red   3,349      1,271        446       169      18
2026-08-24  MTD 2026-08-24  43.5   Red   4,554      1,813        621       353      19
2026-08-29  MTD 2026-08-29  44.9   Red   5,417      2,150        787       483      17
Index trend: ▁▃▄█
```

Each run also prints its movement against the previous one, and names the pillar that
moved most in each direction:

```
Sales Process Index : 43.2%  (Red — Critical)   +1.2 vs previous run
Biggest fall        : Systems and process compliance -5.7 pts
Biggest gain        : Volume, order bank and market share +22.4 pts
```

In the web app the same history drives the **Trend** page: an index chart across runs,
the full run table, and pillar achievement over time. The results page shows a movement
badge against the previous run.

Add `--no-history` to leave a one-off or test run out of the trend.

### Auditing one branch at a time

```bash
python run_audit.py --list-branches           # names as they appear in the data
python run_audit.py --branch "YAVATMAL"       # one branch only
python run_audit.py --branch "YAVATMAL" --branch "AMRAVATI"
python run_audit.py --per-branch              # network report + one per branch
```

A single-branch run writes to `output/<BRANCH>/`. `--per-branch` writes the network
report as usual and additionally one folder per branch under `output/branches/`, so
nothing overwrites anything. Filtering applies to every extract including the complaint
files, so a branch audit only ever sees its own records.

In the web app there are two ways in: pick a branch from the **dropdown on the upload
form** before running, or run the network audit and then use the branch bar on the
results page. Either way: click a branch to audit that
outlet on its own. Each scope has its own download block — the branch files are separate
from the network files and downloading one does not replace the other. Branch audits are
computed on first click from the files already uploaded and cached afterwards.

### A suggested rhythm

Daily runs are useful for the exception lists — aged enquiries, parked test drives,
invoices with no delivery note are all worth working the same day. The index itself is
better read weekly. One reading tells you where you stand; four tell you whether the
corrective actions are working.

### Persistent history on Render

The free plan has an ephemeral filesystem, so history resets when the instance restarts.
Attach a persistent disk and set `AUDIT_HISTORY_DIR` to a path on it to keep the trend
across deploys.

---

## Monthly use

Keep the previous month's `Findings.xlsx` — tab 12 (corrective action plan)
becomes next month's line 8.3, closure of previous audit findings.
