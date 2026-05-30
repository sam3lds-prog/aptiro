# Aptiro Simpler Workflow and Design Guide

Version: 1.0
Audience: product, design, frontend, backend
Scope: application workflow, navigation, onboarding, key page redesigns, and visual style rules

This document translates the whole-application UX review into a buildable plan. It preserves Aptiro's core promise: evidence-backed application materials, no fabrication, and no external submission on the user's behalf. The goal is to make the product feel like one guided journey instead of a collection of powerful but disconnected tools.

Primary journey:

1. Set up profile
2. Find jobs
3. Review matches
4. Build and export package
5. Track application

Secondary utilities:

- Public Profile Research
- Notifications
- Activity
- Privacy and account controls

<div class="page-break"></div>

## Page 1 of 10: Product direction and success criteria

### Current issue

The application has the right functional pieces, but the user must infer the order. The sidebar exposes many destinations at once, and several pages duplicate job intake, export, or status concepts. The result is a workflow that feels wider than it needs to be.

### New product principle

Every screen should answer one of these questions:

1. What stage am I in?
2. What evidence backs this action?
3. What is the next safest step?
4. What will Aptiro never do automatically?

### Experience goals

- Make the first successful export possible through one obvious path.
- Keep trust and provenance visible without repeating long safety copy everywhere.
- Reduce the number of primary navigation choices.
- Turn Dashboard into a command center, not a static status page.
- Make secondary tools discoverable only after they are relevant.
- Keep power-user details available through expansion, drawers, or "view raw" controls.

### Product success metrics

Use these as build validation signals after implementation:

- New users can identify the next step from Dashboard without using the sidebar.
- A Match Inbox "Build package" action opens Packages with the correct job selected.
- Users see export preview and tracking as required parts of the happy path.
- Users can import jobs from one primary intake area.
- Users can scan claim and bullet lists without seeing every destructive action inline.
- Raw JSON is never the default view for non-developer user workflows.

### Non-goals

- Do not remove provenance safeguards.
- Do not auto-submit applications.
- Do not hide compliance, deletion, or export controls.
- Do not replace detailed power tools; progressively disclose them.

<div class="page-break"></div>

## Page 2 of 10: New information architecture

### Target navigation model

Replace the flat 13-link sidebar with four groups:

#### Primary journey

1. Dashboard
2. Profile
3. Jobs
4. Matches
5. Packages
6. Tracker

#### Assist

7. Research
8. Notifications

#### System

9. Activity
10. Privacy

#### Advanced

11. Apply Session Lab
12. Saved Searches

The Advanced group should be visually collapsed by default or placed behind a "More" section. "Apply" should not appear as a normal step in the main Apply group because it is scaffolding, not the main user workflow.

### Exact implementation steps

1. Update `frontend/src/layouts/Nav.tsx`.
2. Replace the current `NAV` array with grouped data:
   - `Primary journey`: `/`, `/vault`, `/jobs`, `/matches`, `/packages`, `/tracker`
   - `Assist`: `/research`, `/notifications`
   - `System`: `/activity`, `/privacy`
   - `Advanced`: `/saved-searches`, `/apply`
3. Rename labels:
   - `Profile Vault` -> `Profile`
   - `Match Inbox` -> `Matches`
   - `Package Workspace` nav label -> `Packages`
   - `Apply` -> `Apply Session Lab`
4. Add stage numbers to the primary journey links:
   - `1 Profile`, `2 Jobs`, `3 Matches`, `4 Packages`, `5 Tracker`
   - Dashboard remains unnumbered as the command center.
5. Keep unread notification badge behavior unchanged.
6. Add short helper text under the brand:
   - "Evidence-backed job applications"
7. Make collapsed groups visually lighter than primary journey links.

### Sidebar visual rules

- Primary journey links use a numbered pill.
- Active primary stage uses accent background and white text.
- Secondary links use neutral text and no number.
- Advanced links use smaller text and should never compete with the primary journey.

### Acceptance criteria

- The first visible navigation group communicates the full journey.
- Saved Searches and Apply Session Lab no longer appear as primary workflow steps.
- Notifications badge still appears when unread messages exist.
- Keyboard navigation and active route styles still work.

<div class="page-break"></div>

## Page 3 of 10: Dashboard command center

### Current issue

Dashboard explains the product, but it does not strongly drive the next action. The checklist ends before export and tracking, and the "Flow" card has only a few quick links.

### Target Dashboard layout

#### Top area: next best action

Show one large card:

- Title: "Next step"
- Body: dynamic explanation from onboarding state
- Primary CTA: link to the exact page needed
- Secondary CTA: "View full journey"

Example states:

- No source: "Add your resume or profile source" -> Profile
- Source added, no approved claim: "Approve evidence claims" -> Profile
- No strategy: "Set search strategy" -> Profile or Jobs depending on final IA choice
- No jobs: "Import your first job" -> Jobs
- Jobs but no packages: "Review matches and build a package" -> Matches
- Package but no export preview: "Preview the export gate" -> Packages
- Export done but no tracked application: "Track the application" -> Tracker

#### Middle area: journey progress

Show a horizontal stage tracker:

1. Profile
2. Jobs
3. Matches
4. Package
5. Export
6. Track

Each stage shows:

- Done, current, or locked state
- Count where useful: approved claims, jobs, strong matches, packages, tracked apps
- Click action when available

#### Lower area: recent work

Show three compact cards:

- Recent package
- Top match
- Next reminder

If no data exists, each card should route users to the setup step.

### Exact implementation steps

1. Extend backend onboarding in `backend/app/legacy.py`.
2. Add data checks for:
   - At least one package export preview or export event
   - At least one tracked application
3. If export events are already represented in audit logs, derive the export step from package export audit events. If not, add a small persisted package field such as `last_exported_at` only if the existing data model supports this cleanly.
4. Update `OnboardingStatus` types in `frontend/src/lib/types.ts`.
5. Refactor `frontend/src/pages/Dashboard.tsx` into:
   - `NextStepCard`
   - `JourneyProgress`
   - `RecentWorkGrid`
6. Keep health and provider status but move it into a compact "System status" disclosure.
7. Replace the current "Flow" card with the new stage tracker.

### Copy rules

- Use action-first labels: "Import a job", not "No jobs yet".
- Keep trust copy short: "Aptiro drafts only from approved evidence."
- Put provider and system diagnostics behind a disclosure unless the system is down.

### Acceptance criteria

- Dashboard has one dominant CTA.
- The next CTA changes as user data changes.
- Export and tracking appear in the guided journey.
- System information no longer dominates the first screen.

<div class="page-break"></div>

## Page 4 of 10: Profile setup flow

### Current issue

Profile Vault combines source management, claim extraction, claim review, edits, rejection, and do-not-use actions. It is powerful, but every claim row exposes too many choices at once.

### Target profile workflow

Rename the page to "Profile" and frame it as step 1:

1. Add source
2. Extract claims
3. Approve evidence
4. Continue to Jobs

### Page structure

#### Header

- Title: "Profile"
- Subtitle: "Build the approved evidence Aptiro can use."
- Primary action: "Add source"
- Secondary action: "Review pending claims" when pending claims exist

#### Source panel

Show source cards with:

- File/name
- Type
- Claim count
- Last updated
- "View claims" action

Move delete source into a card menu or confirmation-only secondary action.

#### Claims panel

Default claim row should show:

- Claim text
- Confidence
- Provenance color
- Source snippet preview
- One primary action: `Approve`
- One secondary action: `Review`

Expanded claim details should show:

- Edit
- Reject
- Do not use
- Full source refs
- Metrics and skills

### Exact implementation steps

1. Update route copy in `frontend/src/pages/Vault.tsx`.
2. Rename user-facing labels from "Profile Vault" to "Profile" while keeping route `/vault` to avoid breaking links.
3. Add a selected claim or expanded claim state.
4. Replace inline action cluster with:
   - `Approve` visible by default
   - `Review` toggles expanded details
5. Move `Edit`, `Reject`, and `Do-not-use` inside expanded details.
6. Add filters above claims:
   - All
   - Pending
   - Approved
   - Rejected
   - Do not use
7. Add search by claim text/source snippet if claim counts justify it.
8. After at least one approved claim, show "Continue to Jobs" CTA.

### Empty states

- No sources: "Add a resume, profile, or notes document to start."
- Sources but no claims: "Extract claims from this source."
- No approved claims: "Approve at least one claim so Aptiro can build grounded materials."

### Acceptance criteria

- A new user can complete profile setup with one primary action visible at a time.
- Destructive actions are not visually equal to approval.
- The user always sees how claims connect to source evidence.

<div class="page-break"></div>

## Page 5 of 10: Unified job intake

### Current issue

Jobs, Matches, and Saved Searches all provide ways to fetch or import jobs. This creates uncertainty about where job discovery starts.

### Target workflow

Make `Jobs` the single intake hub:

1. Paste job description
2. Import public URL
3. Fetch from provider
4. Manage saved searches

`Matches` should only rank, filter, and prioritize imported jobs.

### Jobs page redesign

#### Header

- Title: "Jobs"
- Subtitle: "Add roles you want Aptiro to score against your approved evidence."
- Primary action: "Add job"

#### Intake tabs

Use tabs or segmented controls:

- Paste description
- Import URL
- Provider fetch
- Saved search

Only one intake method is visible at a time.

#### Job list

Each job card shows:

- Title and company
- Work mode/location
- Salary range
- Import source
- Match status: not scored, weak, stretch, moderate, strong
- Primary CTA: "Review match"
- Secondary menu: archive, refresh, view source

### Saved Searches integration

Move the core Saved Searches creation form into Jobs under a "Saved search" tab. Keep the existing route as an advanced deep link or redirect to `/jobs?tab=saved-searches`.

### Exact implementation steps

1. Update `frontend/src/pages/Jobs.tsx`.
2. Add `tab` state driven by `useSearchParams`:
   - `paste`
   - `url`
   - `provider`
   - `saved-searches`
3. Move or reuse Saved Searches UI from `frontend/src/pages/SavedSearches.tsx`.
4. Change Match Inbox "Fetch latest jobs" action into a link to `/jobs?tab=provider`.
5. Keep backend endpoints unchanged.
6. Change Saved Searches nav item to Advanced or redirect.

### Acceptance criteria

- Users can identify Jobs as the single place to add roles.
- Matches no longer imports jobs directly.
- Saved Searches are available without requiring a separate top-level mental model.

<div class="page-break"></div>

## Page 6 of 10: Match review and package handoff

### Current issue

Match cards are dense and the "Build Package" link includes a job query param that Packages does not read. Users may land on Packages with the wrong job selected.

### Target workflow

Matches should be the prioritization screen:

1. See ranked roles
2. Understand why a role fits
3. Decide whether to build a package
4. Land in Packages with the selected job ready

### Match card redesign

Default card content:

- Match score and label
- Job title, company, work mode
- Top 3 strengths
- Top 2 gaps
- Salary if available
- Primary CTA: "Build package"
- Secondary CTA: "Details"

Expanded details:

- Full scoring breakdown
- Missing requirements
- Evidence-backed strengths
- Archive action

### Exact implementation steps

1. Update `frontend/src/pages/Matches.tsx`.
2. Replace "Fetch latest jobs" with "Add jobs" linking to `/jobs?tab=provider`.
3. Keep filter chips but group them:
   - Fit: Strong, Moderate, Stretch
   - Logistics: Remote, Salary, New
   - Attention: Has gaps, Stale
4. Make all filter chips show counts, not only active chips.
5. Keep `Build package` link as `/packages?job=${job.id}`.
6. Update `frontend/src/pages/Packages.tsx` to read `useSearchParams`.
7. On load:
   - If `job` query param exists and matches an available job, set `jid` to that job id.
   - If a package already exists for that job, open it or show "Existing package found."
   - If no package exists, keep the job selected and show "Build package" as the primary CTA.
8. Preserve the current fallback to the first job only when no query param exists.

### Acceptance criteria

- Clicking "Build package" from any match selects the same job in Packages.
- Users see fewer default details per card.
- Scoring evidence remains available through expansion.

<div class="page-break"></div>

## Page 7 of 10: Package review, export, and tracking

### Current issue

Packages is the highest-value page, but review actions, AI actions, agent council, export preview, and export all sit in one long workspace. Export is treated as a panel rather than a guided final step.

### Target package workflow

Use a stepper inside Packages:

1. Select job
2. Review bullets
3. Preview gate
4. Export
5. Track

### Page structure

#### Package header

Show:

- Job title and company
- Fit score
- Package status
- Current step
- Primary CTA for the current step

#### Bullet review

Default bullet row:

- Current text
- Provenance badge
- Status badge
- Primary action: Accept
- Secondary action: Review

Expanded bullet detail:

- Rewrite
- Reject
- Lock
- AI suggest
- Source references
- Flags

#### Export gate

Make "Preview gate" required before export:

- Show included content on left.
- Show excluded content and reasons on right.
- Keep "include unsupported" behind an explicit advanced disclosure.
- After successful export, show "Track this application" CTA.

### Exact implementation steps

1. Update `frontend/src/pages/Packages.tsx`.
2. Add local `step` derived from state:
   - No selected job/package: `select`
   - Package selected but preview not loaded: `review`
   - Preview loaded: `preview`
   - Export triggered: `exported`
3. Use `useSearchParams` to handle `job`.
4. Replace bullet action cluster with primary action plus expanded details.
5. Move "Run agent council" and "AI cover letter" into an "Improve draft" panel.
6. Disable or visually de-emphasize Export until preview is loaded.
7. After export, show:
   - "Download opened in a new tab"
   - "Track application"
   - "Back to matches"
8. Link "Track application" to `/tracker` and, if the backend creates application records on export, open the relevant application.

### Acceptance criteria

- Export preview feels like a required trust gate.
- Users understand what was included and excluded before download.
- Tracking appears immediately after export.
- Bullet review is calmer by default.

<div class="page-break"></div>

## Page 8 of 10: Tracker, Apply Session Lab, and raw data views

### Current issue

Tracker is the real post-export lifecycle tool. Apply is scaffolding and can be misunderstood as automated submission. Tracker snapshots and privacy exports expose raw JSON by default.

### Tracker redesign

Tracker should present applications as a pipeline:

- Drafted
- Exported
- Submitted by user
- Interviewing
- Offer
- Rejected
- Withdrawn

Each application card should show:

- Role and company
- Current status
- Last action
- Next recommended action
- Follow-up reminders
- Primary transition button
- "View package snapshot" as a secondary detail action

Snapshot default view:

- Resume version summary
- Cover letter version summary
- Included bullet count
- Excluded bullet count
- Snapshot hash
- Download raw snapshot

### Apply Session Lab redesign

Rename "Apply" to "Apply Session Lab" everywhere user-facing. Add a banner:

"Experimental planning tool. Aptiro does not submit applications, control browsers, bypass CAPTCHA, or complete employer forms for you."

Keep it in Advanced, not the main journey.

### Privacy redesign

Privacy export should default to a summary:

- Sources count
- Claims count
- Jobs count
- Packages count
- Applications count
- Research findings count
- Generated timestamp
- Download JSON
- View raw JSON disclosure

### Exact implementation steps

1. Update `frontend/src/pages/Tracker.tsx`.
2. Replace raw snapshot `<pre>` with `SnapshotSummary`.
3. Add "View raw JSON" disclosure below the readable summary.
4. Update `frontend/src/pages/Apply.tsx` title and copy to "Apply Session Lab".
5. Update nav labels in `frontend/src/layouts/Nav.tsx`.
6. Update `frontend/src/pages/Privacy.tsx`.
7. Replace raw export preview with `PrivacyExportSummary`.
8. Keep raw JSON available behind a disclosure and download action.

### Acceptance criteria

- Tracker communicates next action, not just status.
- Apply Session Lab is clearly experimental and secondary.
- Raw JSON is available but never the default reading experience.

<div class="page-break"></div>

## Page 9 of 10: Design and style guide

### Brand position

Aptiro should feel:

- Trustworthy
- Calm
- Editorial
- Precise
- Human-in-the-loop

It should not feel:

- Like a scraper
- Like an automation bot
- Like a developer console
- Like a generic job board

### Typography

Keep the current editorial pairing:

- Display: Fraunces
- Body: IBM Plex Sans
- Monospace: system mono for hashes, request ids, and code-like values only

Rules:

- Page titles: display font, 24 to 30 px, semibold or bold.
- Card titles: 15 to 18 px, semibold.
- Body copy: 13 to 15 px.
- Helper text: 12 to 13 px, muted.
- Avoid long uppercase text except small eyebrow labels.

### Color system

Keep provenance colors meaningful and reserved:

- Blue: grounded resume truth
- Purple: profile-derived
- Green: public context or success
- Orange: AI-suggested, warning, needs review
- Red: unsupported, rejected, destructive

Rules:

- Do not use red for ordinary emphasis.
- Do not use provenance colors for unrelated decoration.
- Use accent color for primary navigation and primary CTAs.
- Use neutral panels for density control.

### Layout rules

- One dominant CTA per screen.
- Secondary actions should be visually grouped or hidden behind "More".
- Destructive actions should never sit beside the primary action without separation.
- Use two-pane layouts only when the selection relationship is obvious.
- Use three-pane layouts only for advanced review screens.
- Keep Dashboard and primary journey pages within a consistent max width.

### Component rules

#### Buttons

- Primary: next safe step
- Secondary: useful alternative
- Ghost: low-priority local action
- Danger: destructive action only

#### Cards

- Use cards to group a decision or object, not every small text block.
- Each card should have at most one primary action visible.

#### Badges

- Use badges for status, provenance, and counts.
- Avoid using badges as decorative tags when they compete with status.

#### Empty states

Every empty state needs:

1. Plain-language reason
2. Primary CTA
3. Link to prerequisite if blocked

Template:

Title: "No packages yet"
Body: "Build a package from a matched job after importing roles."
Primary CTA: "Review matches"
Secondary CTA: "Import jobs"

#### Progressive disclosure

Use expansion for:

- Full score breakdown
- Raw JSON
- Destructive actions
- AI assist details
- Source reference lists

Do not hide:

- Provenance status
- Unsupported content warnings
- Export gate exclusions

### Copy style

Use:

- "Aptiro drafts from approved evidence."
- "You submit applications yourself."
- "Preview what will be included before export."
- "Unsupported content is excluded by default."

Avoid:

- "Automated application"
- "Scrape"
- "Bot"
- "One-click apply"
- "Guaranteed match"

<div class="page-break"></div>

## Page 10 of 10: Build sequence, QA checklist, and rollout

### Recommended build sequence

#### Phase A: Navigation and routing

1. Update sidebar grouping and labels.
2. Rename Apply to Apply Session Lab.
3. Move Saved Searches and Apply Session Lab into Advanced.
4. Verify every route remains reachable.

#### Phase B: Dashboard and onboarding

1. Extend onboarding backend with export and tracking steps.
2. Update frontend types.
3. Build Dashboard next-step card.
4. Build journey progress tracker.
5. Move system status into compact disclosure.

#### Phase C: Job intake and matches

1. Make Jobs the primary intake hub.
2. Move Saved Searches into Jobs or link to the tab.
3. Remove fetch action from Matches and replace with Add jobs link.
4. Simplify match cards.
5. Fix `/packages?job=` handoff.

#### Phase D: Profile and package review

1. Rename Profile Vault to Profile in user-facing copy.
2. Collapse secondary claim actions behind Review.
3. Add claim filters.
4. Add package stepper.
5. Collapse secondary bullet actions behind Review.
6. Require preview before export.

#### Phase E: Tracker and raw data polish

1. Add tracker pipeline presentation.
2. Replace snapshot JSON default with readable summary.
3. Replace privacy export JSON default with readable summary.
4. Keep raw JSON behind disclosure and downloads.

### QA checklist

#### Navigation

- Dashboard, Profile, Jobs, Matches, Packages, Tracker, Research, Notifications, Activity, Privacy, Saved Searches, and Apply Session Lab are reachable.
- Active state is correct for each route.
- Notification badge still updates.

#### Guided workflow

- New user sees a single next step on Dashboard.
- Adding a source advances onboarding.
- Approving a claim advances onboarding.
- Importing a job advances onboarding.
- Building a package advances onboarding.
- Previewing/exporting a package advances onboarding.
- Tracking an application completes onboarding.

#### Job and match flow

- Jobs can be pasted, imported by URL, and fetched from provider.
- Saved search creation and running still work.
- Matches rank imported jobs.
- Match filters show correct counts.
- Build package from a match selects the correct job in Packages.

#### Export gate

- Preview gate shows included content.
- Preview gate shows excluded content and reasons.
- Export is unavailable or discouraged until preview is loaded.
- Unsupported content remains excluded by default.
- Include unsupported requires explicit override.

#### Progressive disclosure

- Claim rows show only primary actions by default.
- Bullet rows show only primary actions by default.
- Raw snapshots and privacy export JSON are hidden by default but available.

#### Accessibility

- All buttons and links are keyboard reachable.
- Focus states are visible.
- Badges are not the only source of meaning.
- Color contrast remains readable in dark palette.
- Disclosures announce expanded/collapsed state where possible.

### Release notes draft

"Aptiro now guides users through one clearer journey: set up profile, add jobs, review matches, build and export an evidence-backed package, then track the application. The update simplifies navigation, focuses Dashboard on the next best action, makes Jobs the single intake hub, clarifies the export gate, and moves advanced tools into secondary areas."

### Definition of done

The workflow upgrade is complete when a first-time user can complete the full journey from source upload to tracked application using Dashboard and primary CTAs, without needing to infer page order from the sidebar.
