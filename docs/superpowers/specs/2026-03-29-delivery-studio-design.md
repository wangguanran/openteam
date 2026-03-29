# Delivery Studio Design

**Status:** Draft approved in interactive design review  
**Date:** 2026-03-29  
**Primary User:** repository owner acting as PM  
**Primary Goal:** build a terminal-first multi-agent delivery team that can take a new product requirement from discussion to merge-ready delivery with strict review and CI gates.

## 1. Problem Statement

The user does not want a single coding agent. The user wants a true delivery team with:

- a front-stage panel that can discuss requirements directly with the user
- different agents using different default models
- explicit product approval before implementation
- mandatory documentation, changelog, design, contract, and planning artifacts before coding
- a scalable multi-agent implementation stage across mobile, admin web, and backend
- a reviewer panel with veto power
- mandatory tests and test code as part of delivery
- final GitHub CI success before merge
- a terminal cockpit to communicate with agents and watch them work

This team is optimized for **new project / new requirement delivery** in V1, not legacy migration-first scenarios.

## 2. Product Shape

The system has four coordinated surfaces:

1. `Terminal Cockpit`
   The primary control surface. The user talks to the team here, watches progress here, and makes approval decisions here.

2. `GitHub Projects`
   The PM-facing board. One requirement or one change request maps to one main card.

3. `GitHub Checks + GitHub Actions`
   The hard engineering gates. Reviewer output and CI output must be visible as formal checks.

4. `Branch Protection`
   The final merge barrier. No merge is allowed unless required review and CI checks are green.

## 3. V1 Scope

V1 must fully support this end-to-end loop:

1. Create a new requirement from the terminal cockpit
2. Discuss the requirement with a front-stage panel
3. When UI is involved, have designers present 2-3 options and let the user pick one
4. Lock the approved requirement
5. Update documentation and changelog
6. Freeze design package and contract baseline
7. Generate a master plan
8. Execute parallel workstreams for:
   - `mobile` via `uni-app`
   - `admin web`
   - `backend service`
9. Run a 3-agent review panel with veto power
10. Run CI and test gates
11. Merge only after review checks and CI checks succeed

V1 is explicitly optimized for **new requirements**. It does not prioritize production deployment orchestration, large-scale multi-repo control, or full legacy migration automation.

## 4. Operating Principles

The team must follow these non-negotiable rules:

1. No implementation before explicit user approval.
2. No coding before docs, changelog, design package, contract baseline, and master plan are ready.
3. Any post-lock requirement change becomes a new `change request`.
4. Review is a formal gate, not advisory commentary.
5. Tests and test code are required deliverables, not optional follow-up work.
6. CI success is required before merge.
7. The user acts primarily as PM, not as technical reviewer.

## 5. Front-Stage Team Model

### 5.1 Default Core Panel

The default front-stage panel is:

- `Moderator`
- `Product/Architect`
- `Senior Engineer/Skeptic`

### 5.2 Elastic Expansion Rules

The panel expands automatically based on requirement shape:

- if App UI is involved, add `App UI Designer`
- if Admin Web is involved, add `Admin Web Designer`
- if backend complexity is high, add `Backend Architect`

### 5.3 Interaction Style

The front-stage panel uses a `Moderator-led panel` model:

- all agent opinions can appear directly in the main discussion thread
- the moderator controls pacing and summarizes
- the user can directly `@agent` a specific participant for deeper discussion

### 5.4 Model Assignment Policy

Roles use **fixed default model profiles**, with optional per-session override later.

Recommended defaults for V1:

- `Moderator` -> `GPT-5.4`
- `Product/Architect` -> `Claude Opus 4.6`
- `Senior Engineer/Skeptic` -> `GPT-5.4`
- `App UI Designer` -> `Claude Opus 4.6`
- `Admin Web Designer` -> `Claude Opus 4.6`
- `Backend Architect` -> `GPT-5.4`

The exact provider wiring is implementation-configurable, but the product contract is that roles have stable default model identities.

## 6. Requirement Lifecycle

The main requirement state machine is:

- `Discussing`
- `Awaiting Approval`
- `Locked`
- `Docs Updating`
- `Plan Ready`
- `Implementing`
- `In Review`
- `Changes Requested`
- `CI Running`
- `Ready to Merge`
- `Merged`

### 6.1 Discussing

Purpose:
- clarify scope
- surface risks
- compare options
- identify required workstreams

Required outputs:
- `Raw Requirement Record`
- `Discussion Notes`
- early scope and risks

### 6.2 Awaiting Approval

Purpose:
- convert discussion into an approval-ready version

Required outputs:
- `Decision Log`
- `Final Proposal`
- `Approval Draft`

At this state:
- `Needs You = Yes`

### 6.3 Locked

Purpose:
- freeze what execution is allowed to follow

Required outputs:
- `Approval Record`
- `Frozen Scope`
- selected UI option if applicable
- `Frozen Design Package`
- `Frozen Contract Baseline`

After this point:
- any new user request becomes a new `change request`

## 7. Terminal Cockpit Design

The terminal cockpit is the primary interface. It is not just a multi-tab chat client. It is a team control surface.

### 7.1 Layout

Three-pane layout:

- `Left Pane`: team and agent roster
- `Center Pane`: panel thread / conversation
- `Right Pane`: workflow and execution status

### 7.2 Left Pane

Displays:
- current team
- active panel
- agent name
- role
- default model
- current stage
- status such as `idle`, `discussing`, `planning`, `implementing`, `reviewing`, `blocked`, `waiting_you`
- current task summary
- last active time

### 7.3 Center Pane

The center pane uses a unified `Panel Thread`.

Each message must show:
- agent name
- role
- model
- current stage

Message categories:
- `Discussion`
- `Decision`
- `Action`
- `Alert`

This allows the user to see different agent opinions directly in one thread, with moderator summaries interleaved.

### 7.4 Right Pane

Displays:
- `Request ID`
- `Stage`
- `Needs You`
- `Blocked`
- `Blocked Reason`
- `Review Gate`
- `CI`
- `PR`
- linked GitHub Projects state
- workstream status for `mobile`, `admin`, `backend`

### 7.5 Communication Model

The primary interaction model is:

- default communication goes to the main panel thread
- the user can call specific agents with `@agent`
- a small set of explicit commands exists for high-importance actions

Recommended V1 commands:

- `/new`
- `/approve`
- `/plan`
- `/start`
- `/review`
- `/status`
- `/blockers`
- `/cr`
- `/watch`
- `/panel`

### 7.6 Observability Depth

Default observability is:

- task-level visibility
- key-operation visibility

The cockpit should show what each agent is doing, what key files or checks changed, and what stage is active, without dumping full internal reasoning traces into the main UI.

## 8. Artifacts Required Before Coding

No coding may begin until the following artifacts exist:

1. `Raw Requirement Record`
2. `Decision Log`
3. `Approval Record`
4. updated requirement documentation
5. updated `CHANGELOG`
6. `Design Package` where UI is relevant
7. `Contract Baseline`
8. `Master Implementation Plan`

This is the main mechanism that prevents implementation drift across multiple workstreams.

## 9. Design Package Policy

When UI is involved, the relevant designer must join the front-stage discussion and propose `2-3` options.

The user picks one.

The chosen option must then be frozen into a complete design package.

### 9.1 App Design Package

Stored under a dedicated design package location and should include:

- chosen concept
- key pages
- navigation structure
- component rules
- imagery/assets notes
- token mapping
- key interaction notes

### 9.2 Admin Design Package

Must be separate from App design and include:

- admin workflow pages
- form/list/detail structures
- operations and permissions flow
- component rules
- token mapping

### 9.3 Shared Design Tokens

App and Admin Web share:
- brand tokens
- semantic colors
- typography base
- spacing/radius/shadow primitives

They do **not** share the same component system.

## 10. Monorepo Structure

Recommended repository layout:

```text
apps/
  mobile/
  admin/
services/
  api/
packages/
  contracts/
  design-tokens/
docs/
  requirements/
  decisions/
  approvals/
  design-packages/
  plans/
  changelog/
.github/
  workflows/
```

Responsibilities:

- `apps/mobile/`
  `uni-app` client and platform-facing mobile workstream

- `apps/admin/`
  independent management backend web application

- `services/api/`
  backend services and business logic

- `packages/contracts/`
  API schemas, error models, permissions, state transitions, shared field rules

- `packages/design-tokens/`
  shared foundational tokens for App and Admin

- `docs/*`
  formal requirement, decision, approval, design, plan, and changelog artifacts

## 11. Planning Model

One requirement maps to one master plan.

The master plan must split work into at least:

- `mobile`
- `admin`
- `backend`

Optional support workstreams:

- `design`
- `contracts`
- `integration`
- `release`

Each workstream must include:

- implementation tasks
- test tasks
- verification tasks
- dependencies
- risks
- delivery conditions

## 12. Implementing Stage

### 12.1 Default Implementing Roster

- `Delivery Lead`
- `uni-app Lead Developer`
- `Backend Service Engineer`
- `Admin Web Developer`
- `Shared API / Contract Engineer`

### 12.2 Elastic Expansion

Add on demand:

- `Platform Integration Engineer`
- `App UI Designer`
- `Admin Web Designer`
- `Backend Architect`

### 12.3 Execution Principle

Implementation is **parallel**, but not uncontrolled.

The rule is:

shared boundaries first, then parallel workstreams under one delivery lead.

Required workstreams:

- `mobile`
- `admin`
- `backend`
- `contracts/integration`

The `Delivery Lead` is responsible for:

- sequencing shared dependencies
- coordinating workstream timing
- running integration checkpoints
- routing rework correctly

## 13. Review Panel Design

### 13.1 Default Review Roster

- `Review Moderator`
- `Reviewer-A`
- `Reviewer-B`
- `Reviewer-C`

Recommended viewpoints:

- `Reviewer-A`: requirement and workstream closure
- `Reviewer-B`: engineering quality, architecture, contracts
- `Reviewer-C`: UX consistency and cross-surface coherence

### 13.2 Review Output Schema

Each reviewer must produce:

- `Summary`
- `Blocking Issues`
- `Non-blocking Issues`
- `Risk Notes`
- `Decision` where value is only `PASS` or `BLOCK`

### 13.3 Veto Rule

Any single reviewer may block.

Formal rule:

- if any reviewer returns `BLOCK`, the requirement cannot proceed
- the review gate becomes blocked
- the flow returns to `Changes Requested`

### 13.4 Moderator Output

The `Review Moderator` must:

- aggregate findings
- classify blocking vs non-blocking issues
- produce `blocking-gate`
- generate a structured rework ticket when needed

### 13.5 GitHub Checks

Suggested checks:

- `panel-review/reviewer-a`
- `panel-review/reviewer-b`
- `panel-review/reviewer-c`
- `panel-review/moderator-summary`
- `panel-review/blocking-gate`

`panel-review/blocking-gate` is the main required review check.

## 14. Test and Verification Line

### 14.1 Additional Roles

Add these roles to the core delivery design:

- `Test Architect`
- `QA / Verifier`

### 14.2 Test Architect

Responsibilities:

- participate in planning by default
- turn requirement acceptance criteria into a test strategy
- specify required tests for each workstream
- define coverage and completeness thresholds

Blocking power:

- may block plan readiness if testing strategy is incomplete

### 14.3 QA / Verifier

Responsibilities:

- participate in verification and CI stages by default
- confirm test execution and key validation outputs
- verify merge readiness

Blocking power:

- may block when required tests or validations are incomplete or failing

### 14.4 Testing Policy

Tests and test code are mandatory deliverables.

Every workstream must deliver:

- test cases
- test code
- key path validation
- failure-path validation
- requirement-to-test traceability

### 14.5 Coverage Policy

Do not use a naive full-repository `100% coverage` rule as the only gate.

Instead:

- require near or full `100% coverage` for:
  - contracts
  - permissions
  - state machines
  - core backend domain logic

- require high coverage plus complete critical-path and integration validation for:
  - mobile
  - admin web
  - UI-heavy code

### 14.6 Reviewer Test Obligations

Reviewers must explicitly check:

- whether tests exist
- whether test code exists
- whether tests cover acceptance criteria
- whether key failure paths are covered
- whether obvious gaps remain
- whether cross-workstream validation exists

Insufficient testing is a valid blocking issue.

## 15. CI and Merge Gates

Recommended CI checks:

- `ci/contracts`
- `ci/backend-tests`
- `ci/admin-tests`
- `ci/mobile-tests`
- `ci/integration-tests`
- `ci/coverage`

Branch protection must require at least:

- `panel-review/blocking-gate`
- required CI checks relevant to the repository

The merge gate must satisfy:

- `Spec Approved = Yes`
- `Docs Updated = Yes`
- `Changelog Updated = Yes`
- `Plan Ready = Yes`
- `Review Gate = Passed`
- `CI = Passed`

Only then does the requirement enter `Ready to Merge`.

## 16. GitHub Projects Design

One requirement or one change request equals one main card.

### 16.1 Required Fields

Control fields:

- `Request ID`
- `Title`
- `Project`
- `Priority`
- `Stage`
- `Change Request`

Traceability fields:

- `Raw Record`
- `Decision Log`
- `Approval Record`
- `Design Package`
- `Contract Baseline`

Preparation fields:

- `Spec Approved`
- `Docs Updated`
- `Changelog Updated`
- `Plan Ready`

Execution fields:

- `PR`
- `Review Gate`
- `CI`
- `Release Ready`

Responsibility and blocker fields:

- `Owner`
- `Blocked`
- `Blocked Reason`
- `Needs You`

### 16.2 Needs You Policy

`Needs You = Yes` only when:

1. the user must approve the requirement
2. the user must make a product decision between alternatives

Technical blocks, review failures, and CI failures should not automatically route back to the user.

## 17. Change Request Policy

After a requirement reaches `Locked`, any new requirement change must become a new `change request`.

After a requirement reaches `Merged`, any follow-up adjustment must also become a new main card, not a reuse of the old one.

This preserves auditability and prevents scope contamination.

## 18. Recommended Team Spec Skeleton

```text
Team Name: delivery-studio
Mission: Turn approved product requirements into merge-ready delivery through multi-agent discussion, planning, implementation, review, testing, and CI gating.
Primary Inputs: New requirement, change request, design preference, product decision, review blocker, CI blocker
Primary Outputs: Requirement records, design package, contract baseline, plan, implementation, review checks, test results, merge-ready delivery
Roles: Moderator, Product/Architect, Senior Engineer/Skeptic, App UI Designer, Admin Web Designer, Documentation Steward, Planner, Delivery Lead, uni-app Lead Developer, Platform Integration Engineer, Backend Service Engineer, Backend Architect, Admin Web Developer, Shared API / Contract Engineer, Test Architect, Review Moderator, Reviewer-A/B/C, QA / Verifier, Release / Docs Closer
Workflow Stages: Discussing, Awaiting Approval, Locked, Docs Updating, Plan Ready, Implementing, In Review, Changes Requested, CI Running, Ready to Merge, Merged
Approval Gates: Spec approval gate, product decision gate
Runtime Surfaces: Terminal Cockpit, GitHub Projects, GitHub Checks, GitHub Actions, Branch Protection
Metrics: Lead time, rework count, review block rate, CI failure rate, change request rate, merge-ready conversion rate
Escalation / Human-in-the-loop: User approves specs and product tradeoffs only
Knowledge / Audit Outputs: Raw requirement, decision log, approval record, design package, contract baseline, master plan, rework tickets, final delivery summary
```

## 19. Open Decisions Resolved In This Design

This design locks the following choices:

- new requirement delivery is V1 priority
- terminal cockpit is the primary interface
- panel thread is the primary discussion surface
- one requirement maps to one GitHub Projects main card
- one requirement maps to one master plan
- `uni-app` is the mobile route for V1
- App and Admin share design tokens but not component systems
- every stage supports elastic multi-agent scaling
- review is a veto-based formal gate
- tests are mandatory and test completeness is reviewable

## 20. Recommended Next Step

After the user reviews and approves this design spec, the next step is to create an implementation plan that turns the design into:

- team specification files
- workflow definitions
- terminal cockpit information architecture
- GitHub Projects schema and sync rules
- review check emitters
- CI and branch protection setup
