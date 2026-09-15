# Role Editor Redesign

## Summary

Redesign the role editor from a table-style permission matrix into a modern product-style editor.

The new experience uses a dual-pane layout:

- left: permission module navigation
- right: module-specific permission editor

The design is intentionally hybrid:

- `Skills` uses fine-grained per-skill authorization and enablement management
- all other modules use fixed CRUD-style permissions only

This keeps the editor scalable for growing skill inventories without overcomplicating stable admin modules.

## Goals

- remove the heavy spreadsheet-like matrix feel
- make the page feel like a role designer rather than a settings table
- support future growth in skill count
- keep non-skill modules simple and predictable
- show permission explanations by default without making the page visually noisy
- preserve a clean enterprise admin tone with a more modern product feel

## Non-Goals

- no per-resource fine-grained authorization for users, channels, tokens, or roles
- no nested inheritance editor in this phase
- no workflow builder or policy language
- no hover-only explanations as the primary information mechanism

## Information Architecture

### Top Bar

The page header contains:

- breadcrumb
- primary title: `Edit Role`
- right-aligned actions:
  - `Cancel`
  - `Save Role`

The actions remain visually stable and detached from the permission content area.

### Role Summary Card

Below the top bar, render a compact summary card instead of a large three-field form row.

Display:

- role name
- role description
- role identifier
- built-in badge or meta text for system roles

For built-in roles such as `admin`, do not render the identifier as a disabled input. Render it as metadata:

- `Built-in role`
- `Identifier: admin`

### Main Layout

Use a two-column layout.

#### Left Column: Module Navigation

The left column lists permission modules such as:

- Skills
- Channels
- Tokens
- Users
- Roles

Each item shows:

- icon
- module name
- enabled permission count
- optional short description

The active module is visually highlighted.

#### Right Column: Module Editor

The right column changes based on the selected module.

## Permission Model

### Skills Module

`Skills` is the only fine-grained module.

It has two layers:

1. module-level permissions
2. per-skill authorization and enablement

#### Module-Level Skill Permissions

Only these permissions are needed at module level:

- `View`
- `Enable / Disable`

Do not include:

- `Create`
- `Delete`

Reason:

Skill creation and removal are configured outside the role editor as part of manager/runtime setup, not normal role authorization.

#### Skill-Level Fine-Grained Control

Each skill entry shows:

- skill name
- short description
- authorization state
- enablement state
- relevant permission chips

The editor supports:

- search
- filter
- batch selection later if needed

But phase 1 only requires search and per-item management.

#### New Skill Behavior

When a new skill appears:

- if the role is authorized for it, it can start directly
- if it is not enabled, it remains off by default

This behavior must be explicit in the UI and in the implementation rules.

### Non-Skill Modules

The following modules use fixed CRUD-style permissions only:

- Users
- Tokens
- Channels
- Roles

No per-resource expansion is needed for these modules.

Each module editor only shows the small fixed permission set that belongs to that module.

Examples:

- `View`
- `Create`
- `Edit`
- `Delete`
- module-specific fixed actions such as `Reset Password`

## Visual Design

### Direction

The page should feel modern and productized, not decorative.

Visual characteristics:

- bright, soft background
- restrained blue accent
- light borders
- rounded cards
- strong typography hierarchy
- low-noise surfaces

Avoid:

- dense grid tables
- overly colorful icons
- purple-heavy default styling
- large disabled form fields for metadata

### Permission Presentation

Permission explanations are shown by default.

However, permission labels should not look like primary action buttons.

Use this visual hierarchy:

- a small neutral badge such as `Permission`
- a permission title
- one-line or two-line explanation

This makes the permission block informative rather than clickable-looking.

For selected states inside skill-level controls, chips may still be used, but they should clearly read as state indicators rather than page actions.

### Card Structure

For non-skill modules:

- render permission cards with title + explanation
- cards can be arranged in a compact responsive grid

For skills:

- top section: module-level permission explanation cards
- bottom section: searchable skill list with item-level status and permission chips

## Interaction Rules

### Module Navigation

- clicking a module updates the right-side editor
- the selected module remains highlighted
- the right side never collapses into a matrix

### Skill Search

- search matches skill name first
- optional later extension: description match
- the initial design only requires local client-side filtering

### Save Flow

- `Save Role` persists the entire role state
- the page does not auto-save during chip toggles in this phase

### Restore Defaults

- available at module level
- restores that module to the predefined default permission template

### Select All

- available at module level
- only applies to the currently active module

## Data and State Expectations

The UI should model two different permission shapes.

### Shape A: Fixed Module Permissions

Used by:

- Users
- Tokens
- Channels
- Roles

Structure:

- module id
- fixed permission flags

### Shape B: Skill Permissions

Used only by `Skills`

Structure:

- module-level flags:
  - view
  - enable_disable
- per-skill records:
  - skill id
  - skill name
  - description
  - authorized
  - enabled

The implementation must not force all modules into the same over-generalized structure if that makes the fixed modules harder to reason about.

## Responsive Behavior

Desktop-first is the main target, but mobile should remain usable.

Mobile behavior:

- left module navigation becomes a horizontal scroll list or stacked collapsible selector
- right panel remains card-based
- skill-level actions wrap naturally

No desktop-only table assumptions.

## Implementation Notes

- prefer a dedicated role-editor page or component structure instead of adapting a legacy matrix table
- keep the left navigation and right editor as separate focused units
- do not embed skill-specific growth logic into non-skill module renderers
- default-open explanations should use spacing and hierarchy, not visual clutter

## Testing

The eventual implementation should cover:

- module switching
- skill search
- skill authorization rendering
- skill enabled/disabled rendering
- non-skill CRUD permission rendering
- built-in role metadata display
- save / cancel / restore-default interactions
- responsive layout sanity checks

## Success Criteria

The redesign is successful when:

- the page no longer feels like a matrix table
- skill permissions scale cleanly as skills increase
- admins can understand permissions without hover-dependent help
- fixed modules remain simple
- the layout feels modern and deliberate without becoming flashy
