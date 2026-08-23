package com.hermes.gatewayclient;

/**
 * The document-start network guard installed before hosted Hermes JavaScript runs.
 *
 * <p>This is deliberately a small, self-contained script. The native WebView client and
 * service-worker client remain the authoritative network boundary for HTTP(S); this script
 * closes the WebSocket and browser-API gaps that those callbacks cannot observe.</p>
 */
final class TailscaleEgressGuard {

    private static final String SCRIPT =
        "(function () {\n" +
        "  'use strict';\n" +
        "  var root = typeof globalThis !== 'undefined' ? globalThis : window;\n" +
        "  if (root.__hermesTailscaleEgressGuardInstalled === true) return;\n" +
        "\n" +
        "  function defineGuard(target, name, value) {\n" +
        "    if (!target) return false;\n" +
        "    try {\n" +
        "      Object.defineProperty(target, name, { value: value, writable: false, enumerable: false, configurable: false });\n" +
        "      return target[name] === value;\n" +
        "    } catch (_) { return false; }\n" +
        "  }\n" +
        "\n" +
        "  if (!defineGuard(root, '__hermesTailscaleEgressGuardInstalled', true)) return;\n" +
        "  var NativeURL = root.URL;\n" +
        "  // Numeric allowlist: Tailscale IPv4 100.64.0.0/10; IPv6 fd7a:115c:a1e0::/48; DNS *.ts.net.\n" +
        "  var blocked = function (name) { return new TypeError('Blocked by Hermes Tailscale egress policy: ' + name); };\n" +
        "\n" +
        "  function isDecimalIPv4(host) {\n" +
        "    var parts = String(host).split('.');\n" +
        "    if (parts.length !== 4) return false;\n" +
        "    for (var i = 0; i < parts.length; i++) {\n" +
        "      if (!/^[0-9]+$/.test(parts[i]) || (parts[i].length > 1 && parts[i].charAt(0) === '0')) return false;\n" +
        "      var octet = Number(parts[i]);\n" +
        "      if (!Number.isInteger(octet) || octet < 0 || octet > 255) return false;\n" +
        "    }\n" +
        "    return true;\n" +
        "  }\n" +
        "\n" +
        "  function isTailnetIPv4(host) {\n" +
        "    if (!isDecimalIPv4(host)) return false;\n" +
        "    var parts = String(host).split('.');\n" +
        "    var first = Number(parts[0]);\n" +
        "    var second = Number(parts[1]);\n" +
        "    return first === 100 && second >= 64 && second <= 127;\n" +
        "  }\n" +
        "\n" +
        "  function parseHextets(part) {\n" +
        "    if (part === '') return [];\n" +
        "    var pieces = part.split(':');\n" +
        "    var result = [];\n" +
        "    for (var i = 0; i < pieces.length; i++) {\n" +
        "      if (!/^[0-9a-f]{1,4}$/i.test(pieces[i])) return null;\n" +
        "      result.push(parseInt(pieces[i], 16));\n" +
        "    }\n" +
        "    return result;\n" +
        "  }\n" +
        "\n" +
        "  function isTailnetIPv6(host) {\n" +
        "    var value = String(host);\n" +
        "    if (value.charAt(0) === '[' && value.charAt(value.length - 1) === ']') value = value.slice(1, -1);\n" +
        "    if (value.indexOf(':') < 0 || value.indexOf('.') >= 0) return false;\n" +
        "    var marker = value.indexOf('::');\n" +
        "    var groups;\n" +
        "    if (marker >= 0) {\n" +
        "      if (value.indexOf('::', marker + 2) >= 0) return false;\n" +
        "      var left = parseHextets(value.slice(0, marker));\n" +
        "      var right = parseHextets(value.slice(marker + 2));\n" +
        "      if (left === null || right === null || left.length + right.length >= 8) return false;\n" +
        "      groups = left.concat(new Array(8 - left.length - right.length).fill(0), right);\n" +
        "    } else {\n" +
        "      groups = parseHextets(value);\n" +
        "      if (groups === null || groups.length !== 8) return false;\n" +
        "    }\n" +
        "    return groups.length === 8 && groups[0] === 0xfd7a && groups[1] === 0x115c && groups[2] === 0xa1e0;\n" +
        "  }\n" +
        "\n" +
        "  function isTsNetName(host) {\n" +
        "    var value = String(host).toLowerCase();\n" +
        "    if (value.length <= 7 || value.slice(-7) !== '.ts.net' || value.charAt(value.length - 1) === '.') return false;\n" +
        "    var labels = value.split('.');\n" +
        "    if (labels.length < 3) return false;\n" +
        "    for (var i = 0; i < labels.length; i++) {\n" +
        "      if (!/^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/.test(labels[i])) return false;\n" +
        "    }\n" +
        "    return true;\n" +
        "  }\n" +
        "\n" +
        "  function isAllowedHost(host) {\n" +
        "    var value = String(host);\n" +
        "    if (value.charAt(0) === '[' && value.charAt(value.length - 1) === ']') value = value.slice(1, -1);\n" +
        "    return isTailnetIPv4(value)\n" +
        "      || isTailnetIPv6(value) || isTsNetName(value);\n" +
        "  }\n" +
        "\n" +
        "  function hasUnambiguousAuthority(value, parsed) {\n" +
        "    var text = String(value);\n" +
        "    var match = /^(?:[a-z][a-z0-9+.-]*:)?\\/\\/([^/?#]*)/i.exec(text);\n" +
        "    if (!match) return true;\n" +
        "    var authority = match[1];\n" +
        "    if (authority.indexOf('@') >= 0 || /[%\\s]/.test(authority)) return false;\n" +
        "    var rawHost;\n" +
        "    if (authority.charAt(0) === '[') {\n" +
        "      var close = authority.indexOf(']');\n" +
        "      if (close < 0) return false;\n" +
        "      rawHost = authority.slice(1, close);\n" +
        "    } else {\n" +
        "      if (authority.indexOf(':') !== authority.lastIndexOf(':')) return false;\n" +
        "      rawHost = authority.split(':')[0];\n" +
        "    }\n" +
        "    if (!rawHost || rawHost.charAt(rawHost.length - 1) === '.') return false;\n" +
        "    var normalized = String(parsed.hostname).toLowerCase();\n" +
        "    if (normalized.charAt(0) === '[' && normalized.charAt(normalized.length - 1) === ']') normalized = normalized.slice(1, -1);\n" +
        "    if (isDecimalIPv4(normalized)) return rawHost === normalized;\n" +
        "    if (isTailnetIPv6(normalized)) return authority.charAt(0) === '[' && isTailnetIPv6(rawHost);\n" +
        "    return rawHost.toLowerCase() === normalized;\n" +
        "  }\n" +
        "\n" +
        "  function resolve(value) {\n" +
        "    try {\n" +
        "      var candidate = value && typeof value.url === 'string' ? value.url : String(value);\n" +
        "      return { input: candidate, parsed: new NativeURL(candidate, root.location.href) };\n" +
        "    } catch (_) { return null; }\n" +
        "  }\n" +
        "\n" +
        "  function isAllowedNetworkUrl(value, protocols) {\n" +
        "    var resolved = resolve(value);\n" +
        "    if (!resolved) return false;\n" +
        "    var parsed = resolved.parsed;\n" +
        "    var protocol = String(parsed.protocol).toLowerCase();\n" +
        "    return protocols.indexOf(protocol) >= 0 && parsed.username === '' && parsed.password === ''\n" +
        "      && isAllowedHost(parsed.hostname) && hasUnambiguousAuthority(resolved.input, parsed);\n" +
        "  }\n" +
        "\n" +
        "  function wrapConstructor(Native, protocols, name, hasOptions) {\n" +
        "    var Wrapped = function (url, options) {\n" +
        "      if (!isAllowedNetworkUrl(url, protocols)) throw blocked(name);\n" +
        "      return hasOptions && arguments.length > 1 ? new Native(url, options) : new Native(url);\n" +
        "    };\n" +
        "    try {\n" +
        "      Wrapped.prototype = Native.prototype;\n" +
        "      if (Object.setPrototypeOf) Object.setPrototypeOf(Wrapped, Native);\n" +
        "      ['CONNECTING', 'OPEN', 'CLOSING', 'CLOSED'].forEach(function (key) {\n" +
        "        if (key in Native) Object.defineProperty(Wrapped, key, { value: Native[key], writable: false, configurable: false });\n" +
        "      });\n" +
        "    } catch (_) { }\n" +
        "    return Wrapped;\n" +
        "  }\n" +
        "\n" +
        "  if (typeof root.fetch === 'function') {\n" +
        "    var nativeFetch = root.fetch;\n" +
        "    defineGuard(root, 'fetch', function (input, init) {\n" +
        "      if (!isAllowedNetworkUrl(input, ['http:', 'https:'])) return Promise.reject(blocked('fetch'));\n" +
        "      return nativeFetch.call(root, input, init);\n" +
        "    });\n" +
        "  }\n" +
        "\n" +
        "  if (root.XMLHttpRequest && root.XMLHttpRequest.prototype && typeof root.XMLHttpRequest.prototype.open === 'function') {\n" +
        "    var nativeOpen = root.XMLHttpRequest.prototype.open;\n" +
        "    defineGuard(root.XMLHttpRequest.prototype, 'open', function (method, url) {\n" +
        "      if (!isAllowedNetworkUrl(url, ['http:', 'https:'])) throw blocked('XMLHttpRequest');\n" +
        "      return nativeOpen.apply(this, arguments);\n" +
        "    });\n" +
        "  }\n" +
        "\n" +
        "  if (typeof root.WebSocket === 'function') defineGuard(root, 'WebSocket', wrapConstructor(root.WebSocket, ['ws:', 'wss:'], 'WebSocket', true));\n" +
        "  if (typeof root.EventSource === 'function') defineGuard(root, 'EventSource', wrapConstructor(root.EventSource, ['http:', 'https:'], 'EventSource', true));\n" +
        "  if (typeof root.WebTransport === 'function') defineGuard(root, 'WebTransport', wrapConstructor(root.WebTransport, ['https:'], 'WebTransport', true));\n" +
        "\n" +
        "  var navigatorObject = root.navigator;\n" +
        "  if (navigatorObject && typeof navigatorObject.sendBeacon === 'function') {\n" +
        "    var nativeBeacon = navigatorObject.sendBeacon;\n" +
        "    defineGuard(navigatorObject, 'sendBeacon', function (url, data) {\n" +
        "      if (!isAllowedNetworkUrl(url, ['http:', 'https:'])) return false;\n" +
        "      return nativeBeacon.call(navigatorObject, url, data);\n" +
        "    });\n" +
        "  }\n" +
        "\n" +
        "  var blockedConstructor = function () { throw blocked('worker or peer transport'); };\n" +
        "  if (typeof root.Worker === 'function') defineGuard(root, 'Worker', blockedConstructor);\n" +
        "  if (typeof root.SharedWorker === 'function') defineGuard(root, 'SharedWorker', blockedConstructor);\n" +
        "  if (typeof root.RTCPeerConnection === 'function') defineGuard(root, 'RTCPeerConnection', blockedConstructor);\n" +
        "\n" +
        "  if (navigatorObject && navigatorObject.serviceWorker) {\n" +
        "    var blockedRegister = function () { return Promise.reject(blocked('serviceWorker.register')); };\n" +
        "    if (!defineGuard(navigatorObject.serviceWorker, 'register', blockedRegister)) {\n" +
        "      var serviceWorkerPrototype = Object.getPrototypeOf(navigatorObject.serviceWorker);\n" +
        "      defineGuard(serviceWorkerPrototype, 'register', blockedRegister);\n" +
        "    }\n" +
        "  }\n" +
        "})();\n";

    private TailscaleEgressGuard() {
    }

    static String script() {
        return SCRIPT;
    }

    static boolean canLoadGateway(boolean documentStartScriptSupported) {
        return documentStartScriptSupported;
    }
}
