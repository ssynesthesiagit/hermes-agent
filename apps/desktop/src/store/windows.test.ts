import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  canOpenBrowserWindow,
  canOpenNewWindow,
  canOpenSessionWindow,
  isPeerInstanceWindow,
  openBrowserInNewWindow,
  openNewWindow,
  openSessionInNewWindow,
  primarySessionIdForWindow,
  windowConnectionOverride,
  windowSessionOverride
} from './windows'

const desktopWindow = window as unknown as { hermesDesktop?: Window['hermesDesktop'] }
const initialHermesDesktop = desktopWindow.hermesDesktop

const notifyError = vi.fn()

vi.mock('./notifications', () => ({
  notifyError: (...args: unknown[]) => notifyError(...args)
}))

function installBridge(
  openSessionWindow?: Window['hermesDesktop']['openSessionWindow'],
  openWindow?: Window['hermesDesktop']['openWindow'],
  openBrowserWindow?: Window['hermesDesktop']['openBrowserWindow']
) {
  desktopWindow.hermesDesktop = {
    ...(openSessionWindow ? { openSessionWindow } : {}),
    ...(openWindow ? { openWindow } : {}),
    ...(openBrowserWindow ? { openBrowserWindow } : {})
  } as unknown as Window['hermesDesktop']
}

beforeEach(() => {
  notifyError.mockClear()
})

afterEach(() => {
  if (initialHermesDesktop) {
    desktopWindow.hermesDesktop = initialHermesDesktop
  } else {
    delete desktopWindow.hermesDesktop
  }
})

describe('canOpenSessionWindow', () => {
  it('is false when the desktop bridge is absent', () => {
    delete desktopWindow.hermesDesktop
    expect(canOpenSessionWindow()).toBe(false)
  })

  it('is false when the bridge lacks openSessionWindow', () => {
    installBridge(undefined)
    expect(canOpenSessionWindow()).toBe(false)
  })

  it('is true when the bridge exposes openSessionWindow', () => {
    installBridge(vi.fn().mockResolvedValue({ ok: true }))
    expect(canOpenSessionWindow()).toBe(true)
  })
})

describe('isPeerInstanceWindow', () => {
  it('recognizes only the full peer marker', () => {
    expect(isPeerInstanceWindow('?peer=1')).toBe(true)
    expect(isPeerInstanceWindow('?peer=0')).toBe(false)
    expect(isPeerInstanceWindow('?win=secondary')).toBe(false)
    expect(isPeerInstanceWindow('')).toBe(false)
  })
})

describe('openSessionInNewWindow', () => {
  it('no-ops without a session id', async () => {
    const open = vi.fn().mockResolvedValue({ ok: true })
    installBridge(open)

    await openSessionInNewWindow('')

    expect(open).not.toHaveBeenCalled()
    expect(notifyError).not.toHaveBeenCalled()
  })

  it('no-ops gracefully when the bridge is absent (web fallback)', async () => {
    delete desktopWindow.hermesDesktop

    await openSessionInNewWindow('s1')

    expect(notifyError).not.toHaveBeenCalled()
  })

  it('invokes the bridge with the session id', async () => {
    const open = vi.fn().mockResolvedValue({ ok: true })
    installBridge(open)

    await openSessionInNewWindow('s1')

    expect(open).toHaveBeenCalledWith('s1', undefined)
    expect(notifyError).not.toHaveBeenCalled()
  })

  it('forwards the watch flag for spectator (subagent) windows', async () => {
    const open = vi.fn().mockResolvedValue({ ok: true })
    installBridge(open)

    await openSessionInNewWindow('s1', { watch: true })

    expect(open).toHaveBeenCalledWith('s1', { watch: true })
    expect(notifyError).not.toHaveBeenCalled()
  })

  it('forwards the owning profile for a focused Bot Chat window', async () => {
    const open = vi.fn().mockResolvedValue({ ok: true })
    installBridge(open)

    await openSessionInNewWindow('s1', { profile: 'ops', watch: true })

    expect(open).toHaveBeenCalledWith('s1', { profile: 'ops', watch: true })
    expect(notifyError).not.toHaveBeenCalled()
  })

  it('forwards the owning source connection for a focused Bot Chat window', async () => {
    const open = vi.fn().mockResolvedValue({ ok: true })
    installBridge(open)

    await openSessionInNewWindow('s1', { profile: 'ops', connectionId: 'tailnet-a' })

    expect(open).toHaveBeenCalledWith('s1', { profile: 'ops', connectionId: 'tailnet-a' })
    expect(notifyError).not.toHaveBeenCalled()
  })

  it('notifies on an ok:false result', async () => {
    installBridge(vi.fn().mockResolvedValue({ ok: false, error: 'invalid-session-id' }))

    await openSessionInNewWindow('s1')

    expect(notifyError).toHaveBeenCalledTimes(1)
  })

  it('notifies when the bridge throws', async () => {
    installBridge(vi.fn().mockRejectedValue(new Error('boom')))

    await openSessionInNewWindow('s1')

    expect(notifyError).toHaveBeenCalledTimes(1)
  })
})

describe('windowConnectionOverride', () => {
  it('reads the source connection from the pre-hash query', () => {
    window.history.replaceState({}, '', '/?win=secondary&connectionId=tailnet-a#/s1')
    expect(windowConnectionOverride()).toBe('tailnet-a')
    window.history.replaceState({}, '', '/')
  })
})

describe('primarySessionIdForWindow', () => {
  it('pins a root secondary route to the durable pre-hash session query', () => {
    expect(primarySessionIdForWindow('/', null, '?win=secondary&session=chat%20one')).toBe('chat one')
  })

  it('keeps a parsed hash route authoritative over the fallback query', () => {
    expect(primarySessionIdForWindow('/', 'hash-session', '?win=secondary&session=query-session')).toBe('hash-session')
  })

  it('does not apply the fallback to ordinary, HUD, peer, or watch windows', () => {
    const query = '?win=secondary&session=chat-session'

    expect(primarySessionIdForWindow('/', null, '?session=chat-session')).toBeNull()
    expect(primarySessionIdForWindow('/', null, '?win=hud&session=chat-session')).toBeNull()
    expect(primarySessionIdForWindow('/', null, '?peer=1&session=chat-session')).toBeNull()
    expect(primarySessionIdForWindow('/', null, `${query}&watch=1`)).toBeNull()
    expect(primarySessionIdForWindow('/settings', null, query)).toBeNull()
  })
})

describe('windowSessionOverride', () => {
  it('reads and trims an encoded session from the pre-hash query', () => {
    expect(windowSessionOverride('?win=secondary&session=%20chat%20one%20')).toBe('chat one')
  })

  it('returns null for malformed query input instead of throwing', () => {
    expect(windowSessionOverride(Symbol('invalid-search') as unknown as string)).toBeNull()
    expect(windowSessionOverride('?win=secondary&session=%00')).toBe('\0')
  })
})

describe('canOpenNewWindow', () => {
  it('is false when the desktop bridge is absent', () => {
    delete desktopWindow.hermesDesktop
    expect(canOpenNewWindow()).toBe(false)
  })

  it('is false when the bridge lacks openWindow', () => {
    installBridge(vi.fn().mockResolvedValue({ ok: true }))
    expect(canOpenNewWindow()).toBe(false)
  })

  it('is true when the bridge exposes openWindow', () => {
    installBridge(undefined, vi.fn().mockResolvedValue({ ok: true }))
    expect(canOpenNewWindow()).toBe(true)
  })
})

describe('openNewWindow', () => {
  it('no-ops gracefully when the bridge is absent (web fallback)', async () => {
    delete desktopWindow.hermesDesktop

    await openNewWindow()

    expect(notifyError).not.toHaveBeenCalled()
  })

  it('no-ops when openWindow is missing', async () => {
    installBridge(vi.fn().mockResolvedValue({ ok: true }))

    await openNewWindow()

    expect(notifyError).not.toHaveBeenCalled()
  })

  it('invokes the bridge', async () => {
    const openWindow = vi.fn().mockResolvedValue({ ok: true })
    installBridge(undefined, openWindow)

    await openNewWindow()

    expect(openWindow).toHaveBeenCalledTimes(1)
    expect(notifyError).not.toHaveBeenCalled()
  })

  it('notifies on an ok:false result', async () => {
    installBridge(undefined, vi.fn().mockResolvedValue({ ok: false, error: 'nope' }))

    await openNewWindow()

    expect(notifyError).toHaveBeenCalledTimes(1)
  })
})

describe('canOpenBrowserWindow', () => {
  it('is false when the desktop bridge is absent', () => {
    delete desktopWindow.hermesDesktop
    expect(canOpenBrowserWindow()).toBe(false)
  })

  it('is false when the bridge lacks openBrowserWindow', () => {
    installBridge(vi.fn().mockResolvedValue({ ok: true }))
    expect(canOpenBrowserWindow()).toBe(false)
  })

  it('is true when the bridge exposes openBrowserWindow', () => {
    installBridge(undefined, undefined, vi.fn().mockResolvedValue({ ok: true }))
    expect(canOpenBrowserWindow()).toBe(true)
  })
})

describe('openBrowserInNewWindow', () => {
  it('returns false without a tab id', async () => {
    const open = vi.fn().mockResolvedValue({ ok: true })
    installBridge(undefined, undefined, open)

    expect(await openBrowserInNewWindow('')).toBe(false)
    expect(open).not.toHaveBeenCalled()
    expect(notifyError).not.toHaveBeenCalled()
  })

  it('returns false when the bridge is absent', async () => {
    delete desktopWindow.hermesDesktop

    expect(await openBrowserInNewWindow('tab-1')).toBe(false)
    expect(notifyError).not.toHaveBeenCalled()
  })

  it('invokes the bridge with the tab id', async () => {
    const open = vi.fn().mockResolvedValue({ ok: true })
    installBridge(undefined, undefined, open)

    expect(await openBrowserInNewWindow('tab-1')).toBe(true)
    expect(open).toHaveBeenCalledWith('tab-1')
    expect(notifyError).not.toHaveBeenCalled()
  })

  it('returns false and notifies on an ok:false result', async () => {
    installBridge(undefined, undefined, vi.fn().mockResolvedValue({ ok: false, error: 'invalid-tab-id' }))

    expect(await openBrowserInNewWindow('tab-1')).toBe(false)
    expect(notifyError).toHaveBeenCalledTimes(1)
  })

  it('returns false and notifies when the bridge throws', async () => {
    installBridge(undefined, undefined, vi.fn().mockRejectedValue(new Error('boom')))

    expect(await openBrowserInNewWindow('tab-1')).toBe(false)
    expect(notifyError).toHaveBeenCalledTimes(1)
  })
})
