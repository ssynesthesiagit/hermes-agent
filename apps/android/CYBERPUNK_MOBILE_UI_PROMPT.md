# Implementation prompt: cyberpunk AI chat and bot command center for Hermes

## Objective

Give the existing responsive Hermes web dashboard a polished, futuristic cyberpunk experience that works naturally inside the Android WebView client. Replace the terminal-first presentation with a conventional, structured AI conversation UI for everyday use, while keeping the terminal available as an optional **Advanced Console** so no expert capability is lost.

This is a **responsive frontend refinement**, not a new mobile app, a desktop clone, or a Hermes runtime rewrite.

## Important existing capabilities to reuse

Inspect the current Hermes web frontend before editing. The existing implementation already provides the necessary foundations:

- `web/src/themes/presets.ts` contains an existing `cyberpunkTheme`; extend or refine its tokens instead of creating a separate styling system.
- `web/src/App.tsx` already contains responsive mobile navigation/drawer behavior and routes for `/chat`, `/sessions`, `/profiles`, and `/profiles/new`.
- `web/src/pages/ProfilesPage.tsx` already loads profiles and supports creating, cloning, activating, renaming, editing, and deleting them.
- `web/src/pages/ProfileBuilderPage.tsx` already manages model/provider, skills, MCP servers, environment values, description, and SOUL setup.
- `web/src/contexts/ProfileProvider.tsx` already supports profile-scoped deep links through `?profile=<name>`.
- `web/src/pages/ChatPage.tsx` already keeps the terminal-backed chat process mounted across dashboard navigation.
- `web/src/lib/gatewayClient.ts` and the existing TUI gateway already provide a structured WebSocket/RPC path. Reuse existing methods and event contracts such as `session.create`, `session.resume`, `session.list`, `session.history`, `prompt.submit`, `session.interrupt`, `command.dispatch`, `image.attach_bytes`, and `file.attach`, plus streamed events including `message.start`, `message.delta`, `message.complete`, and existing tool/status events.
- `web/src/components/ChatSidebar.tsx` already demonstrates authenticated gateway connection, event subscription, profile-aware session creation, model controls, and reconnect handling.

In the mobile interface, present Hermes **Profiles** to the user as **Bots**, while retaining the existing route names, data structures, APIs, and internal terminology in code. A bot is a friendly UI representation of an existing Hermes profile—not a new backend entity.

## Product direction

Create a mobile-first “network bot command center” that feels like a compact cyberpunk control surface:

- dark near-black/teal background;
- restrained cyan primary accent and magenta secondary accent;
- thin luminous borders and subtle status glows;
- compact technical typography using bundled or system fonts;
- clear online, active, connecting, and error states;
- subtle grid/noise texture using lightweight CSS only;
- fast, readable, touch-friendly interactions.

The result should feel futuristic, not noisy. Text readability, touch targets, and hierarchy take priority over decoration.

Do not squeeze the desktop sidebar and multi-column layout onto a phone. Use stacked cards, sheets/drawers, and bottom navigation designed for one-handed use.

## Design influences

Use interaction patterns—not copied branding or assets—from familiar applications:

- ChatGPT and Claude: readable conversation rhythm, inline streaming, a large multiline composer, attachment affordances, and a clear Stop action;
- Discord and Slack: understandable bot identity, compact status treatments, channel/session drawers, and mobile navigation;
- Linear and Raycast: fast command access, restrained information density, crisp keyboard/touch states, and minimal chrome;
- Cyberpunk 2077 and Deus Ex interfaces: cyan/magenta highlights, technical labels, segmented panels, and subtle scan/grid texture.

Do not copy logos, proprietary illustrations, exact layouts, or trademarked visual assets. The goal is a familiar modern AI chat interaction model with an original Hermes cyberpunk skin.

## Required mobile information architecture

At phone widths, add a persistent bottom navigation bar with four destinations:

1. **Chat** — `/chat`
2. **Bots** — `/profiles`
3. **Sessions** — `/sessions`
4. **More** — opens the existing remaining dashboard destinations in a sheet or drawer

Requirements:

- Respect Android safe-area insets.
- Use at least 44×44 CSS-pixel touch targets.
- Clearly indicate the active destination.
- Preserve the current desktop navigation at desktop breakpoints.
- Do not unnecessarily restart or duplicate the active structured chat session or Advanced Console when switching destinations.
- Do not add a new routing library or duplicate the existing navigation model.

## Bots UI

Restyle the existing `/profiles` page as a mobile-friendly **Bots** screen. Reuse `ProfilesPage` data and actions rather than creating new endpoints or parallel state.

Each bot card should show only information already available from the existing profile response:

- bot/profile name;
- active/default badge where applicable;
- short description;
- configured model;
- skill count;
- concise status treatment based on existing known state—do not invent live presence data.

Provide these prominent actions:

- **Chat** — navigate to `/chat?profile=<encoded profile name>` using the existing profile-scoping behavior;
- **Manage** — expose the profile’s existing edit/configuration actions;
- **More** — reuse existing activate, rename, clone, terminal command, skills/tools, and delete actions where they already exist.

Keep the existing create flow and label its mobile call-to-action **New bot** while routing to `/profiles/new`. The builder should remain the existing profile builder, visually restyled for mobile rather than rewritten.

Do not duplicate the model picker, skills editor, MCP configuration, environment editor, SOUL editor, or destructive-action confirmation logic. Reuse the current components and API calls.

## Primary AI chat UI

Make a structured conversation view the default Chat experience on both phone and desktop-responsive layouts. Use the existing gateway client and TUI gateway RPC/event protocol; do not scrape or parse ANSI terminal output.

The default Chat screen should include:

- a compact header with the selected bot/profile, model when already available, and real connection state;
- a scrollable conversation timeline loaded from `session.history`;
- visually distinct user and assistant messages;
- assistant text that updates in place as existing `message.delta` events arrive;
- Markdown and code rendering by reusing the existing Markdown component;
- compact, collapsible reasoning/tool activity cards only when corresponding structured events already exist;
- a multiline composer with attach, send, and stop controls;
- file/photo attachment through the existing `file.attach`, `image.attach`, or `image.attach_bytes` paths;
- Android system-keyboard dictation through the normal text field—do not add a bespoke speech service;
- a Sessions drawer/sheet using existing session list, resume, history, title, and delete behavior;
- reconnect/resume behavior based on existing gateway/session state rather than local mock state.

Sending a normal message must call the existing `prompt.submit` method for the selected session. Stop must use `session.interrupt`. Stream completion and failure must follow the existing structured events and error frames.

Keep **Advanced Console** accessible from the Chat overflow menu or More screen. It may reuse the current xterm-backed `ChatPage` with minimal visual restyling. Preserve slash/CLI access there, and optionally expose the existing slash-command catalog through a lightweight command palette. Do not try to turn every CLI flag or slash command into a button.

Do not maintain two independent session engines. The structured chat and Advanced Console must use the existing Hermes session/gateway contracts and clearly indicate which session is active. If sharing one live session safely is not already supported, keep the console in an explicitly separate advanced session rather than inventing synchronization.

## Visual implementation constraints

- Build on the existing theme tokens and `cyberpunkTheme`.
- Prefer CSS variables, Tailwind utilities already used by the project, and existing shared components.
- Use CSS gradients/pseudo-elements for the subtle grid or scan-line treatment.
- Keep animation limited to short opacity/transform/status transitions.
- Honor `prefers-reduced-motion`.
- Maintain WCAG AA contrast for normal text.
- Do not load fonts, images, scripts, analytics, or other assets from public CDNs. The Android client intentionally blocks non-Tailscale network egress.
- Do not add Canvas/WebGL backgrounds, video, large image assets, a component framework, or a new animation dependency.
- Do not use constant flicker, aggressive glitch effects, or large neon text shadows.

## Preserve these boundaries

- No Hermes backend, gateway, authentication, session, or streaming protocol changes.
- No native Android redesign; the Android project remains a secure WebView shell.
- No local models, offline mode, push notifications, background bot runtime, or phone-as-node behavior.
- No new “Bot” database model or bot API. “Bots” is mobile-facing copy for existing profiles.
- No ANSI scraping, terminal-output heuristics, or duplicated streaming protocol. Build the default chat on the existing structured gateway methods/events.
- No changes that weaken Tailscale-only networking or secure gateway storage.
- No desktop regression. Desktop can receive the refined cyberpunk theme, but its information architecture should remain intact.
- Do not edit an installed runtime copy directly. Work in the authorized Hermes source checkout and rebuild/deploy the normal web frontend.

## Suggested implementation order

1. Add a thin typed frontend adapter over the existing gateway methods/events; do not change the protocol.
2. Build the structured conversation timeline and composer using session history, prompt submission, streaming events, interrupt, and attachment methods.
3. Keep the current xterm experience available as Advanced Console.
4. Add/refine reusable cyberpunk theme tokens and small shared surface/status styles.
5. Add the phone-only bottom navigation and More sheet while retaining the desktop shell.
6. Restyle `ProfilesPage` into the Bots card layout at phone widths and wire Chat links with the existing `?profile=` scope.
7. Refine the existing profile builder and editor layouts for narrow screens without changing their data flow.
8. Add focused protocol/component/responsive tests and perform real browser plus Android WebView verification.

## Acceptance criteria

- At 360×800 and 390×844, Chat, Bots, Sessions, and More are reachable without horizontal scrolling.
- The UI remains usable with Android display/font scaling and keyboard open.
- The Bots screen lists real existing profiles and never substitutes mock data.
- Tapping Chat on a bot opens `/chat?profile=<name>` and the existing chat process uses that profile.
- Create, edit, activate, rename, clone, skills/tools navigation, and delete continue through existing actions with existing confirmations.
- The default Chat screen looks and behaves like a modern AI phone app rather than a terminal: structured messages, multiline composer, attachments, inline streaming, and Stop.
- A normal send uses `prompt.submit`; visible incremental response text comes from the existing message stream; Stop uses `session.interrupt`.
- Existing sessions load through structured session history/resume behavior and survive navigation/reopen according to the current gateway contract.
- File/photo selection, Android IME dictation, reconnect behavior, and authentication continue through existing mechanisms.
- Advanced Console remains available for CLI/slash-command workflows without dominating the normal phone experience.
- Navigating away from and back to Chat does not unnecessarily restart or duplicate the active structured session.
- The existing cyberpunk theme is recognizably futuristic but readable, responsive, and reduced-motion safe.
- Existing desktop layouts continue to work at 1024px and 1440px widths.
- No new backend route, persistence format, network origin, runtime dependency, or Android permission is introduced.
- Relevant existing tests pass, and new tests cover mobile navigation state, bot-card routing, empty/loading/error states, and narrow-screen overflow.

## Deliverables

- Focused frontend changes in the existing Hermes web source.
- Updated or new tests for the structured chat adapter, streamed-delta assembly, reconnect/resume, mobile navigation, and Bots presentation.
- Screenshots at 390×844 for structured Chat, Bots, Sessions, More, the profile builder, and Advanced Console.
- One desktop screenshot confirming there is no desktop navigation regression.
- A short verification note listing tests run and confirming that backend/auth/streaming contracts were not changed.
