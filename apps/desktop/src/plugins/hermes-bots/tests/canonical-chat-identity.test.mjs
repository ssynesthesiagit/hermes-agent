import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import vm from 'node:vm'

const source = readFileSync(new URL('../plugin.js', import.meta.url), 'utf8')

// ── canonical open-path harness (slice: createCanonicalChat + openBotCanonicalChat)
function loadOpenPath({ openSession, openSessionWindow, request }) {
  const start = source.indexOf('const canonicalCreations = new Map()')
  const end = source.indexOf('function displayName(', start)
  const saved = []
  const requests = []
  const context = {
    host: {
      openSession,
      openSessionWindow,
      request: async (method, params) => {
        requests.push({ method, params })
        return request(method, params)
      }
    },
    saveBotMeta: (name, patch) => saved.push({ name, patch: JSON.parse(JSON.stringify(patch)) }),
    $hideBotChats: { get: () => false },
    window: { setTimeout: callback => callback() }
  }
  const section = source
    .slice(start, end)
    .concat('\nglobalThis.__open = { openBotCanonicalChat };\n')

  assert.notEqual(start, -1, 'canonical creation section is missing')
  assert.notEqual(end, -1, 'canonical creation section delimiter is missing')
  vm.runInNewContext(section, context, { filename: 'canonical-open.js' })
  return { ...context.__open, saved, requests, host: context.host }
}

const HISTORY = { id: 'hist-1', title: 'Bot Chat', preview: 'history preview', last_active: 1000 }

// ── source-scoped canonical popout harness ──────────────────────────────────
// This slice keeps the source route and popout helper together while avoiding
// the full React/plugin boot. The fake source RPC returns distinct runtime and
// durable ids so the native window assertion cannot accidentally accept the
// volatile session handle.
function loadSourceScopedPath({ openSessionWindow, requestProfile }) {
  const start = source.indexOf('function botConnectionRoute(')
  const end = source.indexOf('function displayName(', start)
  const opened = []
  const routed = []
  const context = {
    host: {
      request: async () => ({}),
      requestProfile: async (...args) => {
        routed.push(args)
        return requestProfile(...args)
      },
      openSessionWindow: async (...args) => {
        opened.push(args)
        return openSessionWindow?.(...args)
      }
    }
  }
  const section = source
    .slice(start, end)
    .concat('\nglobalThis.__openSourceScopedBotChat = openSourceScopedBotChat;\n')

  assert.notEqual(start, -1, 'source routing section is missing')
  assert.notEqual(end, -1, 'source routing section delimiter is missing')
  vm.runInNewContext(section, context, { filename: 'source-scoped-open.js' })
  return { openSourceScopedBotChat: context.__openSourceScopedBotChat, opened, routed, host: context.host }
}

const SOURCE_BOT = {
  name: 'ops',
  connectionId: 'tailnet-a',
  remoteSource: true,
  sourceScoped: true
}

test('source-scoped popout: resume fallback opens durable session_key, not runtime session_id', async () => {
  const runtime = loadSourceScopedPath({
    openSessionWindow: async () => undefined,
    requestProfile: async (_route, method, params) => {
      if (method === 'profiles.list') throw new Error('legacy source')
      if (method === 'session.resume') {
        assert.equal(params.session_id, 'pin-root')
        return { session_id: 'runtime-adopt', session_key: 'stored-adopt' }
      }
      return {}
    }
  })

  const result = await runtime.openSourceScopedBotChat(SOURCE_BOT, 'pin-root', null)

  assert.equal(result, 'stored-adopt')
  assert.deepEqual(JSON.parse(JSON.stringify(runtime.opened)), [[
    'stored-adopt',
    { profile: 'ops', connectionId: 'tailnet-a' }
  ]])
  assert.notEqual(runtime.opened[0][0], 'runtime-adopt')
})

test('source-scoped popout: preview adoption pins and opens its durable session key', async () => {
  const configured = []
  const runtime = loadSourceScopedPath({
    openSessionWindow: async () => undefined,
    requestProfile: async (_route, method, params) => {
      if (method === 'profiles.list') return { profiles: [{ name: 'ops' }] }
      if (method === 'session.resume') {
        assert.equal(params.session_id, 'preview-root')
        return { session_id: 'runtime-preview', session_key: 'stored-preview' }
      }
      if (method === 'profiles.configure') configured.push(params)
      return {}
    }
  })

  const result = await runtime.openSourceScopedBotChat(SOURCE_BOT, null, { id: 'preview-root' })

  assert.equal(result, 'stored-preview')
  assert.deepEqual(JSON.parse(JSON.stringify(runtime.opened)), [[
    'stored-preview',
    { profile: 'ops', connectionId: 'tailnet-a' }
  ]])
  assert.equal(configured[0].ui_meta['hermes-bots'].chat, 'stored-preview')
  assert.notEqual(runtime.opened[0][0], 'runtime-preview')
})

test('source-scoped popout: compression lineage keeps the durable canonical pin as window id', async () => {
  const runtime = loadSourceScopedPath({
    openSessionWindow: async () => undefined,
    requestProfile: async (_route, method) => {
      if (method === 'profiles.list') {
        return {
          profiles: [{
            name: 'ops',
            preferred_session: { id: 'root-pin', resolved_id: 'tip-9', message_count: 12 }
          }]
        }
      }
      return {}
    }
  })

  const result = await runtime.openSourceScopedBotChat(SOURCE_BOT, 'root-pin', null)

  assert.equal(result, 'root-pin')
  assert.deepEqual(JSON.parse(JSON.stringify(runtime.opened)), [[
    'root-pin',
    { profile: 'ops', connectionId: 'tailnet-a' }
  ]])
})

test('source-scoped popout: new canonical chat submits with runtime id but opens durable id', async () => {
  const calls = []
  const runtime = loadSourceScopedPath({
    openSessionWindow: async () => undefined,
    requestProfile: async (_route, method, params) => {
      calls.push({ method, params })
      if (method === 'profiles.list') return { profiles: [{ name: 'ops' }] }
      if (method === 'session.list') return { sessions: [] }
      if (method === 'session.create') return { session_id: 'runtime-created', stored_session_id: 'stored-created' }
      if (method === 'session.resume') return { session_id: 'runtime-confirm', session_key: 'stored-created' }
      return {}
    }
  })

  const result = await runtime.openSourceScopedBotChat(SOURCE_BOT, null, null)

  assert.equal(result, 'stored-created')
  assert.deepEqual(JSON.parse(JSON.stringify(runtime.opened)), [[
    'stored-created',
    { profile: 'ops', connectionId: 'tailnet-a' }
  ]])
  assert.equal(calls.find(call => call.method === 'prompt.submit').params.session_id, 'runtime-created')
  assert.equal(calls.find(call => call.method === 'session.resume').params.session_id, 'stored-created')
  assert.notEqual(runtime.opened[0][0], 'runtime-created')
  assert.notEqual(runtime.opened[0][0], 'runtime-confirm')
})

// ── grandfather: no pin + existing history adopts the previewed session ────

test('grandfather: no pin + history opens and pins THAT session, no new chat', async () => {
  const runtime = loadOpenPath({
    openSession: async () => undefined,
    request: async () => ({})
  })

  const result = await runtime.openBotCanonicalChat('ops', null, HISTORY)

  assert.equal(result, 'hist-1')
  assert.deepEqual(runtime.saved, [{ name: 'ops', patch: { chat: 'hist-1' } }])
  assert.equal(runtime.requests.some(r => r.method === 'session.create'), false,
    'must not mint a new chat when the previewed session can be adopted')
})

test('safety: no pin + ordinary latest history creates a Bot Chat instead of claiming it', async () => {
  const runtime = loadOpenPath({
    openSession: async () => undefined,
    request: async method =>
      method === 'session.create'
        ? { stored_session_id: 'safe-bot-chat', session_id: 'safe-bot-chat-runtime' }
        : {}
  })

  const ordinary = { ...HISTORY, id: 'ordinary-1', title: '生产调度会优化' }
  const result = await runtime.openBotCanonicalChat('ops', null, ordinary)

  assert.equal(result, 'safe-bot-chat')
  assert.deepEqual(runtime.saved, [{ name: 'ops', patch: { chat: 'safe-bot-chat' } }])
  assert.equal(runtime.requests.some(r => r.method === 'session.create'), true)
})

test('grandfather: no pin + no history keeps the creation flow', async () => {
  const runtime = loadOpenPath({
    openSession: async () => undefined,
    request: async method =>
      method === 'session.create' ? { stored_session_id: 'stored-1', session_id: 'runtime-1' } : {}
  })

  const result = await runtime.openBotCanonicalChat('ops', null, null)

  assert.equal(result, 'stored-1')
  assert.equal(runtime.requests.some(r => r.method === 'session.create'), true)
})

test('window popout: a live canonical pin opens with its owner profile and never opens main', async () => {
  const opened = []
  const runtime = loadOpenPath({
    openSession: async () => { throw new Error('main session open must not run') },
    openSessionWindow: async (id, options) => opened.push({ id, options }),
    request: async method => {
      if (method === 'profiles.list') {
        return { profiles: [{ name: 'ops', preferred_session: { id: 'pin-1', resolved_id: 'tip-2', message_count: 4 } }] }
      }
      return {}
    }
  })

  const result = await runtime.openBotCanonicalChat('ops', 'pin-1', HISTORY, { window: true })

  assert.equal(result, 'pin-1')
  assert.deepEqual(JSON.parse(JSON.stringify(opened)), [{ id: 'tip-2', options: { profile: 'ops' } }])
  assert.equal(runtime.requests.some(r => r.method === 'session.create'), false)
})

test('window popout: a new canonical chat kicks off before opening the focused window', async () => {
  const opened = []
  const runtime = loadOpenPath({
    openSession: async () => { throw new Error('main session open must not run') },
    openSessionWindow: async (id, options) => opened.push({ id, options }),
    request: async method => {
      if (method === 'session.create') return { stored_session_id: 'stored-window', session_id: 'runtime-window' }
      return {}
    }
  })

  const result = await runtime.openBotCanonicalChat('ops', null, null, { window: true })

  assert.equal(result, 'stored-window')
  assert.deepEqual(JSON.parse(JSON.stringify(opened)), [{ id: 'stored-window', options: { profile: 'ops' } }])
  assert.deepEqual(runtime.requests.map(request => request.method), [
    'session.list',
    'session.create',
    'prompt.submit',
  ])
})

test('grandfather: adoption hydration failure surfaces without forking a replacement chat', async () => {
  const runtime = loadOpenPath({
    openSession: async id => {
      if (id === 'hist-1') throw new Error('session vanished')
    },
    request: async method =>
      method === 'session.create' ? { stored_session_id: 'stored-2', session_id: 'runtime-2' } : {}
  })

  await assert.rejects(runtime.openBotCanonicalChat('ops', null, HISTORY), /session vanished/)
  assert.equal(runtime.saved.some(s => s.patch?.chat === 'hist-1'), false,
    'a failed adoption must not persist the dead id as the pin')
  assert.equal(runtime.requests.some(r => r.method === 'session.create'), false,
    'a transient hydration failure must not fork the canonical chat')
})

// ── precise pin verification (no session.list pagination/hidden semantics) ─

test('pin: preferred_session present opens the resolved session and keeps the pin', async () => {
  const opened = []
  const runtime = loadOpenPath({
    openSession: async (id, options) => { opened.push({ id, options }) },
    request: async method => {
      if (method === 'profiles.list') {
        return {
          profiles: [{
            name: 'ops',
            preferred_session: {
              id: 'pin-1', resolved_id: 'pin-1', title: 'Bot Chat',
              preview: 'latest', started_at: 1, last_active: 2, message_count: 3
            }
          }]
        }
      }
      return {}
    }
  })

  const result = await runtime.openBotCanonicalChat('ops', 'pin-1', HISTORY)

  assert.equal(result, 'pin-1')
  assert.deepEqual(JSON.parse(JSON.stringify(opened)), [{
    id: 'pin-1',
    options: {
      profile: 'ops',
      intent: 'main',
      awaitHydration: true,
      expectHistory: true,
      // false: clicking a bot moves the WORKSPACE onto that bot, not just the
      // transcript. With true, `$activeGatewayProfile` stayed on the previously
      // active profile, so "New session" from inside any bot was created on
      // that other backend (measured: four new chats from different bots all
      // landed in `ops`).
      keepAllProfilesScope: false,
      retryHydrationTimeoutOnce: true
    }
  }])
  assert.equal(runtime.saved.length, 0, 'a live pin must not be rewritten')
  assert.equal(runtime.requests.some(r => r.method === 'session.create'), false)
  // The pin is verified through the precise resolver, never session.list.
  assert.equal(runtime.requests.some(r => r.method === 'session.list'), false)
})

test('safety: a pinned ordinary session is rejected and replaced with a Bot Chat', async () => {
  const runtime = loadOpenPath({
    openSession: async () => undefined,
    request: async method => {
      if (method === 'profiles.list') {
        return {
          profiles: [{
            name: 'ops',
            preferred_session: { id: 'ordinary-3', resolved_id: 'ordinary-3', title: '生产调度会优化' }
          }]
        }
      }
      if (method === 'session.create') return { stored_session_id: 'safe-pinned-chat', session_id: 'safe-pinned-runtime' }
      return {}
    }
  })

  const result = await runtime.openBotCanonicalChat('ops', 'ordinary-3', HISTORY)

  assert.equal(result, 'safe-pinned-chat')
  assert.deepEqual(runtime.saved, [
    { name: 'ops', patch: { chat: null } },
    { name: 'ops', patch: { chat: 'safe-pinned-chat' } }
  ])
})

test('pin: compression-rotated pin opens the live tip, keeps the durable pin', async () => {
  const opened = []
  const runtime = loadOpenPath({
    openSession: async id => { opened.push(id) },
    request: async method => {
      if (method === 'profiles.list') {
        return {
          profiles: [{
            name: 'ops',
            preferred_session: {
              id: 'root-1', resolved_id: 'tip-9', root_title: 'Bot Chat', title: 'Bot Chat (continued)',
              preview: 'post-compression', started_at: 1, last_active: 9, message_count: 42
            }
          }]
        }
      }
      return {}
    }
  })

  const result = await runtime.openBotCanonicalChat('ops', 'root-1', HISTORY)

  assert.deepEqual(opened, ['tip-9'])
  assert.equal(result, 'root-1', 'the stored pin keeps its durable identity')
  assert.equal(runtime.saved.length, 0)
})

test('pin: definitively gone pin re-pins to the previewed session, not rows[0]', async () => {
  const runtime = loadOpenPath({
    openSession: async () => undefined,
    request: async method => {
      if (method === 'profiles.list') {
        return { profiles: [{ name: 'ops', preferred_session: null }] }
      }
      return {}
    }
  })

  const result = await runtime.openBotCanonicalChat('ops', 'dead-pin', HISTORY)

  assert.equal(result, 'hist-1')
  assert.deepEqual(runtime.saved, [{ name: 'ops', patch: { chat: 'hist-1' } }])
  assert.equal(runtime.requests.some(r => r.method === 'session.create'), false)
})

test('safety: a dead pin does not re-anchor on an ordinary latest session', async () => {
  const runtime = loadOpenPath({
    openSession: async () => undefined,
    request: async method => {
      if (method === 'profiles.list') return { profiles: [{ name: 'ops', preferred_session: null }] }
      if (method === 'session.create') return { stored_session_id: 'safe-replacement', session_id: 'safe-replacement-runtime' }
      return {}
    }
  })

  const ordinary = { ...HISTORY, id: 'ordinary-2', title: '生产调度会优化' }
  const result = await runtime.openBotCanonicalChat('ops', 'dead-pin', ordinary)

  assert.equal(result, 'safe-replacement')
  assert.deepEqual(runtime.saved, [
    { name: 'ops', patch: { chat: null } },
    { name: 'ops', patch: { chat: 'safe-replacement' } }
  ])
  assert.equal(runtime.requests.some(r => r.method === 'session.create'), true)
})

test('pin: gone pin + no history clears the pin and creates', async () => {
  const runtime = loadOpenPath({
    openSession: async () => undefined,
    request: async method => {
      if (method === 'profiles.list') {
        return { profiles: [{ name: 'ops', preferred_session: null }] }
      }
      if (method === 'session.create') return { stored_session_id: 'stored-3', session_id: 'runtime-3' }
      return {}
    }
  })

  const result = await runtime.openBotCanonicalChat('ops', 'dead-pin', null)

  assert.equal(result, 'stored-3')
  // Pin cleared first (dead pin is provably unusable), then the freshly
  // created chat pins itself inside createCanonicalChat.
  assert.deepEqual(runtime.saved, [
    { name: 'ops', patch: { chat: null } },
    { name: 'ops', patch: { chat: 'stored-3' } }
  ])
})

test('pin: precise hit but failed hydration keeps the pin and surfaces the failure', async () => {
  const runtime = loadOpenPath({
    openSession: async () => { throw new Error('socket hiccup') },
    request: async method => {
      if (method === 'profiles.list') {
        return {
          profiles: [{
            name: 'ops',
            preferred_session: {
              id: 'pin-1', resolved_id: 'pin-1', title: 'Bot Chat',
              preview: 'latest', started_at: 1, last_active: 2, message_count: 3
            }
          }]
        }
      }
      return {}
    }
  })

  await assert.rejects(runtime.openBotCanonicalChat('ops', 'pin-1', HISTORY), /socket hiccup/)
  assert.equal(runtime.saved.length, 0, 'a confirmed-live pin must survive a transient open failure')
  assert.equal(runtime.requests.some(r => r.method === 'session.create'), false,
    'must not fork the forever-chat on a hiccup')
})

test('pin: a waking-backend hydration timeout asks the SDK to retry internally', async () => {
  // The internal retry-and-succeed behavior lives in host.openSession itself
  // (apps/desktop/src/sdk/index.ts) now, because only that layer sees the
  // $resumeExhaustedSessionId latch that the core stranded-session overlay
  // reads — a plugin-side retry can silently resolve while that overlay stays
  // latched (hermes-agent#89617). This harness stubs host.openSession with a
  // bare mock, so it can only prove the plugin ASKS for the retry, not that
  // the overlay never appears; see profile-routing.test.ts for that.
  const opts = []
  const runtime = loadOpenPath({
    openSession: async (id, options) => { opts.push(options) },
    request: async method => {
      if (method === 'profiles.list') {
        return {
          profiles: [{
            name: 'ops',
            preferred_session: {
              id: 'pin-1', resolved_id: 'pin-1', title: 'Bot Chat',
              preview: 'latest', started_at: 1, last_active: 2, message_count: 3
            }
          }]
        }
      }
      return {}
    }
  })

  const result = await runtime.openBotCanonicalChat('ops', 'pin-1', HISTORY)

  assert.equal(result, 'pin-1')
  assert.equal(opts.length, 1)
  assert.equal(opts[0].retryHydrationTimeoutOnce, true, 'the SDK must own the hydration-timeout retry')
})

test('pin: a persistent hydration timeout still surfaces the failure', async () => {
  const runtime = loadOpenPath({
    openSession: async () => { throw new Error("Timed out loading ops's session history.") },
    request: async method => {
      if (method === 'profiles.list') {
        return {
          profiles: [{
            name: 'ops',
            preferred_session: {
              id: 'pin-1', resolved_id: 'pin-1', title: 'Bot Chat',
              preview: 'latest', started_at: 1, last_active: 2, message_count: 3
            }
          }]
        }
      }
      return {}
    }
  })

  await assert.rejects(runtime.openBotCanonicalChat('ops', 'pin-1', HISTORY), /Timed out loading/)
  assert.equal(runtime.saved.length, 0, 'a confirmed-live pin must survive a persistent hydration timeout')
})

// ── transient failures must never destroy the pin ──────────────────────────

test('transient: profiles.list failure keeps the pin when the direct open works', async () => {
  const runtime = loadOpenPath({
    openSession: async () => undefined,
    request: async method => {
      if (method === 'profiles.list') throw new Error('gateway reconnecting')
      return {}
    }
  })

  const result = await runtime.openBotCanonicalChat('ops', 'pin-1', HISTORY)

  assert.equal(result, 'pin-1')
  assert.equal(runtime.saved.length, 0, 'a hiccup must not clear or rewrite the pin')
  assert.equal(runtime.requests.some(r => r.method === 'session.create'), false,
    'a hiccup must not mint a replacement chat')
})

test('transient: profiles.list failure + failed direct open preserves pin and surfaces Retry', async () => {
  const runtime = loadOpenPath({
    openSession: async id => {
      if (id === 'pin-1') throw new Error('resume rejected')
    },
    request: async method => {
      if (method === 'profiles.list') throw new Error('gateway reconnecting')
      if (method === 'session.create') return { stored_session_id: 'stored-4', session_id: 'runtime-4' }
      return {}
    }
  })

  await assert.rejects(runtime.openBotCanonicalChat('ops', 'pin-1', HISTORY), /resume rejected/)
  assert.deepEqual(runtime.saved, [], 'an inconclusive outage must never clear the canonical pin')
  assert.equal(runtime.requests.some(r => r.method === 'session.create'), false,
    'an inconclusive outage must never fork the canonical chat')
})

// ── preferred_session_ids request shaping (pure helper) ────────────────────

function loadHelpers() {
  const atom = value => ({ get: () => value, set: () => undefined })
  const jsx = (type, props = {}) => ({ type, props })
  const context = {
    atom,
    jsx,
    jsxs: jsx,
    useQuery: () => ({}),
    useValue: value => (value?.get ? value.get() : value),
    useState: value => [value, () => undefined],
    document: { getElementById: () => null, createElement: () => ({}), head: { appendChild: () => undefined } },
    host: { state: { profile: { get: () => 'ops', listen: () => undefined } }, request: () => undefined }
  }
  const code = source
    .replace(/^import\s+\*\s+as\s+sdk\s+from '@hermes\/plugin-sdk'\r?\n/m, '')
    .replace(/^import\s+\{[\s\S]*?\}\s+from '@hermes\/plugin-sdk'\r?\n/m, '')
    .replace(/^const \{ McpTab, ToolsetConfigPanel \} = sdk\r?\n/m, '')
    .replace(/^import .* from 'react'\r?\n/m, '')
    .replace(/^import .* from 'react\/jsx-runtime'\r?\n/m, '')
    .replace('export default {', 'globalThis.plugin = {')
    .concat('\nglobalThis.__preferredSessionIds = preferredSessionIds;')
  vm.runInNewContext(code, context)
  return context
}

test('preferredSessionIds: collects only live pins', () => {
  // vm-realm objects fail assert.deepEqual prototype checks — compare via JSON.
  const collect = meta => JSON.parse(JSON.stringify(loadHelpers().__preferredSessionIds(meta)))
  assert.deepEqual(
    collect({ ops: { chat: 'pin-1' }, scribe: { chat: null }, chef: { title: 'Chef' } }),
    { ops: 'pin-1' }
  )
  assert.deepEqual(collect({}), {})
  assert.deepEqual(collect(undefined), {})
})
