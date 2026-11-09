/**
 * Host half of the QQ Agent panel.
 *
 * The Python service owns NapCat, SQLite and the persona card.  This plugin
 * only reads a small status file and (optionally) profile/history JSON, so the
 * page can show state even when the service is down.  It never parses the
 * persona card and never probes NapCat directly.
 */
import { readFile } from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'

export const name = 'qq-agent'
export const inject = ['webServer']

const ROUTE = '/qq-agent/state'
const STALE_MS = 90_000

function resolveHome(config = {}) {
  const raw = String(config.home || process.env.QQ_AGENT_HOME || '').trim()
  if (!raw) return path.join(os.homedir(), '.dsh', 'qq-agent')
  return path.resolve(raw.replace(/^~(?=$|[\\/])/, os.homedir()))
}

async function readJson(file) {
  try {
    const text = await readFile(file, 'utf8')
    return JSON.parse(text.replace(/^\uFEFF/, ''))
  } catch {
    return null
  }
}

async function readText(file) {
  try {
    return (await readFile(file, 'utf8')).replace(/^\uFEFF/, '')
  } catch {
    return ''
  }
}

async function readTail(file, lines) {
  const text = await readText(file)
  return text.split(/\r?\n/).filter(Boolean).slice(-lines)
}

function friendRows(profiles, showProfiles) {
  return Object.entries(profiles || {})
    .map(([channel, entry]) => ({
      channel,
      name: String(entry?.name || ''),
      nickname: String(entry?.nickname || ''),
      impression: showProfiles ? String(entry?.impression || '') : '',
      msgs: Number(entry?.stats?.msgs || 0),
      avgLen: Number(entry?.stats?.avg_len || 0),
    }))
    .sort((a, b) => b.msgs - a.msgs)
}

function conversationRows(history, showPreviews) {
  return Object.entries(history || {})
    .map(([channel, rows]) => {
      const items = Array.isArray(rows) ? rows : []
      const last = items[items.length - 1]
      return {
        channel,
        turns: items.length,
        lastAt: Number(last?.ts || 0),
        lastRole: String(last?.role || ''),
        preview: showPreviews ? String(last?.content || '').slice(0, 60) : '',
      }
    })
    .sort((a, b) => b.lastAt - a.lastAt)
    .slice(0, 12)
}

async function collect(home, config) {
  const status = await readJson(path.join(home, 'status.json'))
  const dataDir = path.join(home, 'data', 'qq')
  const [profiles, history] = await Promise.all([
    readJson(path.join(dataDir, 'profiles.json')),
    readJson(path.join(dataDir, 'history.json')),
  ])
  const log = config.showLogs
    ? await readTail(path.join(home, 'logs', 'service.log'), 40)
    : []

  const heartbeatAt = Number(status?.heartbeatAt || 0) * 1000
  const stale = !heartbeatAt || Date.now() - heartbeatAt > STALE_MS
  return {
    ok: true,
    home,
    running: Boolean(status) && !stale,
    stale,
    status: status || null,
    friends: friendRows(profiles, config.showProfiles !== false),
    conversations: conversationRows(history, config.showPreviews === true),
    log,
    generatedAt: Date.now(),
  }
}

export function apply(ctx, config = {}) {
  const home = resolveHome(config)
  const settings = {
    showLogs: config.showLogs !== false,
    showProfiles: config.showProfiles !== false,
    showPreviews: config.showPreviews === true,
  }

  ctx.effect(() => {
    try {
      return ctx.webServer.register({
        kind: 'exact',
        path: ROUTE,
        async handler(_req, res) {
          try {
            const payload = await collect(home, settings)
            res.setHeader('content-type', 'application/json; charset=utf-8')
            res.setHeader('cache-control', 'no-store')
            res.end(JSON.stringify(payload))
          } catch (error) {
            res.statusCode = 500
            res.setHeader('content-type', 'application/json; charset=utf-8')
            res.end(JSON.stringify({ ok: false, error: String(error?.message ?? error) }))
          }
        },
      })
    } catch (error) {
      // Two rows can load the same package in one profile.  The second
      // registration loses the route, but the UI must still load.
      if (/duplicate/i.test(String(error?.message ?? error))) return () => {}
      throw error
    }
  })
}
