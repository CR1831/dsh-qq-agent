/**
 * Optional Host bridge: expose a tiny local HTTP surface that lets the QQ
 * service hand a task to a DSH session and read the reply back.
 *
 * This bundle is separate from dsh-qq-agent and is not installed by default.
 * It binds to the DSH web server (localhost); every handler rejects non-loopback
 * callers.  The Python side still enforces owner-only access.
 */
import { MessageId, freezeMessage } from '@deepseek-ai/dsh-llm'

const name = 'qq-agent-desktop'
const inject = ['webServer', 'agents', 'sessionController', 'sessionPersistence']

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
const submissions = new Map()

function isLocal(req) {
  const addr = String(req.socket?.remoteAddress || '')
  return addr === '127.0.0.1' || addr === '::1' || addr === '::ffff:127.0.0.1'
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    let raw = ''
    req.setEncoding('utf8')
    req.on('data', (chunk) => {
      raw += chunk
      if (raw.length > 1_000_000) {
        req.destroy()
        reject(new Error('request body too large'))
      }
    })
    req.on('end', () => resolve(raw))
    req.on('error', reject)
  })
}

async function ensureSession(ctx, sessionId, cwd, preset) {
  let resolved = await ctx.sessionController.resolveAgent(sessionId)
  if (resolved && resolved.error && resolved.error.code === 'session/not-found') {
    await ctx.sessionController.create({
      sessionId,
      cwd,
      agentPreset: preset || 'standard',
    })
    resolved = await ctx.sessionController.resolveAgent(sessionId)
  }
  if (resolved && resolved.error) throw new Error('session unavailable: ' + JSON.stringify(resolved.error))
  if (!resolved || !resolved.agent) throw new Error('resolveAgent returned no agent')
  return resolved.agent
}

async function readLastAssistant(ctx, sessionId) {
  const persistence = ctx.sessionPersistence
  if (!persistence) return ''
  await persistence.flush?.()
  const handle = await persistence.open(sessionId, 'read')
  let last = ''
  let seen = 0
  for (let offset = 0; ;) {
    const { events } = await handle.read(offset, 512)
    if (!events || !events.length) break
    for (const event of events) {
      if (event && event.type === 'assistant/message') {
        const text = (event.data?.message?.content ?? [])
          .filter((block) => block && block.type === 'text')
          .map((block) => block.text ?? '')
          .join('\n')
          .trim()
        if (text) last = text
      }
    }
    offset += events.length
    seen += events.length
    if (seen > 20000) break
  }
  return last
}

async function submit(ctx, text, opts) {
  const agent = await ensureSession(ctx, opts.sessionId, opts.cwd, opts.preset)
  const before = await readLastAssistant(ctx, opts.sessionId).catch(() => '')
  const at = Date.now()
  const message = freezeMessage({
    id: MessageId(`qq-agent-desktop:${at}`),
    role: 'user',
    content: [{ type: 'text', text }],
    source: { kind: 'qq-agent-desktop', role: 'user' },
  })
  if (agent.status === 'running') agent.steer(message)
  else agent.followup(message)
  submissions.set(opts.sessionId, { at, before })
  return { agent, before, at }
}

async function inspect(ctx, sessionId, cwd, preset) {
  const agent = await ensureSession(ctx, sessionId, cwd, preset)
  const entry = submissions.get(sessionId)
  const last = await readLastAssistant(ctx, sessionId).catch(() => '')
  const running = agent.status === 'running'
  const hasNew = Boolean(entry) && last !== entry.before
  return {
    ok: true,
    sessionId,
    running,
    finished: !running && hasNew,
    reply: hasNew ? last : '',
    waitedMs: entry ? Date.now() - entry.at : 0,
  }
}

function apply(ctx, config = {}) {
  const cwd = String(config.cwd || '').trim() || process.cwd()
  const defaultSession = String(config.sessionId || 'qq-agent')

  const json = (res, code, obj) => {
    res.statusCode = code
    res.setHeader('content-type', 'application/json; charset=utf-8')
    res.setHeader('cache-control', 'no-store')
    res.end(JSON.stringify(obj))
  }
  const guard = (req, res) => {
    if (isLocal(req)) return true
    json(res, 403, { ok: false, error: 'local requests only' })
    return false
  }

  ctx.effect(() =>
    ctx.webServer.register({
      kind: 'exact',
      path: '/desk/ping',
      async handler(req, res) {
        if (!guard(req, res)) return
        json(res, 200, {
          ok: true,
          plugin: name,
          services: {
            sessionController: Boolean(ctx.sessionController),
            agents: Boolean(ctx.agents),
            sessionPersistence: Boolean(ctx.sessionPersistence),
            webServer: Boolean(ctx.webServer),
          },
          sessionId: defaultSession,
          modes: ['sync', 'background'],
        })
      },
    }),
  )

  ctx.effect(() =>
    ctx.webServer.register({
      kind: 'exact',
      path: '/desk/result',
      async handler(req, res) {
        if (!guard(req, res)) return
        if (req.method !== 'GET') return json(res, 405, { ok: false, error: 'GET only' })
        try {
          const url = new URL(req.url || '/', 'http://127.0.0.1')
          const sessionId = String(url.searchParams.get('sessionId') || defaultSession)
          const preset = url.searchParams.get('preset') || undefined
          json(res, 200, await inspect(ctx, sessionId, cwd, preset))
        } catch (error) {
          ctx.logger?.warn?.('qq-agent-desktop result failed: %s', error?.message || error)
          json(res, 500, { ok: false, error: String(error?.message || error) })
        }
      },
    }),
  )

  ctx.effect(() =>
    ctx.webServer.register({
      kind: 'exact',
      path: '/desk/run',
      async handler(req, res) {
        if (!guard(req, res)) return
        if (req.method !== 'POST') return json(res, 405, { ok: false, error: 'POST only' })
        try {
          const raw = await readBody(req)
          const body = raw ? JSON.parse(raw) : {}
          const text = String(body.text || '').trim()
          if (!text) return json(res, 400, { ok: false, error: 'text is empty' })

          const sessionId = String(body.sessionId || defaultSession)
          const preset = body.agentPreset ? String(body.agentPreset) : undefined
          const sessionCwd = body.cwd ? String(body.cwd) : cwd
          const timeoutMs = Math.min(Math.max(Number(body.timeoutMs) || 90000, 5000), 300000)
          const started = await submit(ctx, text, { sessionId, cwd: sessionCwd, preset })

          if (body.background === true) {
            return json(res, 200, {
              ok: true,
              background: true,
              sessionId,
              submittedAt: started.at,
              hint: 'submitted; poll GET /desk/result?sessionId=…',
            })
          }

          let waited = 0
          let finished = false
          await sleep(1200)
          while (Date.now() - started.at < timeoutMs) {
            if (started.agent.status !== 'running') {
              finished = true
              break
            }
            await sleep(800)
            waited += 800
          }
          if (started.agent.status !== 'running') finished = true
          if (finished) await sleep(600)

          const after = await readLastAssistant(ctx, sessionId).catch(() => '')
          const hasNew = after && after !== started.before
          json(res, 200, {
            ok: true,
            sessionId,
            reply: hasNew ? after : '',
            finished,
            waitedMs: waited,
            ...(hasNew
              ? {}
              : {
                  hint: finished
                    ? 'finished without a new text reply (tool-only turn?)'
                    : `still running after ${Math.round(waited / 1000)}s; use background or poll later`,
                }),
          })
        } catch (error) {
          ctx.logger?.warn?.('qq-agent-desktop run failed: %s', error?.message || error)
          json(res, 500, { ok: false, error: String(error?.message || error) })
        }
      },
    }),
  )

  ctx.logger?.info?.('qq-agent-desktop ready: /desk/ping, /desk/run, /desk/result')
}

export { apply, inject, name }
