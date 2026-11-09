/**
 * Client half of the QQ Agent panel.
 *
 * All state comes from the Host route /qq-agent/state.  The panel never reads
 * the persona card and never talks to NapCat.
 */
window.__ModuleLoader__.load({
  id: 'dsh-qq-agent',
  factory(require) {
    const React = require('react')
    const h = React.createElement
    const NS = 'dsh-qq-agent'

    const zh = {
      panel: 'QQ Agent',
      title: 'QQ Agent',
      refresh: '刷新',
      refreshing: '刷新中…',
      loading: '正在读取…',
      error: '读取失败',
      serviceOn: '服务运行中',
      serviceOff: '服务未运行',
      serviceStale: '心跳已过期',
      wsOn: '已连接',
      wsOff: '未连接',
      persona: '人格卡',
      personaMissing: '未配置',
      personaPath: '路径',
      personaWarn: '警告',
      dryRunOn: '只出草稿',
      dryRunOff: '会真实发送',
      whitelist: '白名单',
      friends: '好友画像',
      conversations: '最近会话',
      log: '服务日志',
      emptyFriends: '还没有画像数据。运行 qq-agent qq --profiles 生成。',
      emptyConvs: '还没有对话记录。',
      emptyLog: '（暂无日志）',
      noPreview: '（预览已关闭）',
      startHint: '启动服务：',
      home: '运行目录',
      version: '版本',
      turns: '条',
      msgs: '句',
      chars: '字',
      qq: 'QQ',
    }

    const en = {
      panel: 'QQ Agent',
      title: 'QQ Agent',
      refresh: 'Refresh',
      refreshing: 'Refreshing…',
      loading: 'Loading…',
      error: 'Read failed',
      serviceOn: 'Service running',
      serviceOff: 'Service stopped',
      serviceStale: 'Heartbeat expired',
      wsOn: 'Connected',
      wsOff: 'Disconnected',
      persona: 'Persona card',
      personaMissing: 'Not configured',
      personaPath: 'Path',
      personaWarn: 'Warnings',
      dryRunOn: 'Draft only',
      dryRunOff: 'Sending live',
      whitelist: 'Allowlist',
      friends: 'Friend profiles',
      conversations: 'Recent conversations',
      log: 'Service log',
      emptyFriends: 'No profiles yet. Run qq-agent qq --profiles.',
      emptyConvs: 'No conversations yet.',
      emptyLog: '(no log)',
      noPreview: '(preview disabled)',
      startHint: 'Start the service:',
      home: 'Home',
      version: 'Version',
      turns: 'turns',
      msgs: 'msgs',
      chars: 'chars',
      qq: 'QQ',
    }

    const T = {
      bg: 'var(--dsw-alias-bg-primary, #fff)',
      fg: 'var(--dsw-alias-text-primary, #1a1a1a)',
      dim: 'var(--dsw-alias-text-secondary, #6b7280)',
      border: 'var(--dsw-alias-border-primary, #e3e3e6)',
      card: 'var(--dsw-alias-bg-secondary, #f7f7f9)',
      accent: 'var(--dsw-alias-brand-primary, #247bbf)',
      ok: 'var(--dsw-alias-status-success, #1f9d55)',
      bad: 'var(--dsw-alias-status-danger, #d64545)',
      warn: 'var(--dsw-alias-status-warning, #b7791f)',
    }
    const mono = 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace'

    function Icon(props) {
      const size = props && props.size ? props.size : 20
      const active = !!(props && props.active)
      return h(
        'svg',
        {
          viewBox: '0 0 24 24',
          width: size,
          height: size,
          fill: 'none',
          'aria-hidden': true,
          style: { display: 'block', color: active ? T.accent : T.dim },
        },
        h('path', {
          d: 'M4 13.2c0-3.4 2.9-6.2 6.6-6.2 2.6 0 4.9 1.4 5.9 3.5l2.6-1.6c.4-.2.9.1.9.6v3.4c0 3.3-2.9 6-6.5 6h-.9c-1.1 0-2.1-.6-2.6-1.6l-.9-1.8H9.6',
          stroke: 'currentColor',
          strokeWidth: 1.6,
          strokeLinecap: 'round',
          strokeLinejoin: 'round',
        }),
        h('circle', { cx: 9.4, cy: 12, r: 0.9, fill: 'currentColor' }),
        h('path', {
          d: 'M14.6 15.6c.9 1.2 2.3 1.9 3.9 1.9',
          stroke: 'currentColor',
          strokeWidth: 1.4,
          strokeLinecap: 'round',
        }),
      )
    }

    function Card(props) {
      return h(
        'div',
        {
          style: {
            background: T.card,
            border: '1px solid ' + T.border,
            borderRadius: 10,
            padding: '12px 14px',
          },
        },
        props.children,
      )
    }

    function Stat(props) {
      return h(
        'div',
        { style: { minWidth: 88 } },
        h('div', { style: { fontSize: 12, color: T.dim, marginBottom: 4 } }, props.label),
        h(
          'div',
          {
            style: {
              fontSize: props.small ? 13 : 16,
              fontWeight: 600,
              color: props.color || T.fg,
              wordBreak: 'break-all',
            },
          },
          props.value,
        ),
      )
    }

    function Pill(props) {
      return h(
        'span',
        {
          style: {
            fontSize: 11,
            padding: '2px 7px',
            borderRadius: 999,
            border: '1px solid ' + (props.color || T.border),
            color: props.color || T.dim,
            whiteSpace: 'nowrap',
          },
        },
        props.children,
      )
    }

    function fmtTime(ts) {
      if (!ts) return '—'
      const ms = ts > 1e12 ? ts : ts * 1000
      const d = new Date(ms)
      const p = (n) => String(n).padStart(2, '0')
      return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
    }

    function Panel(props) {
      const t = props.t || ((key) => zh[key] || key)
      const [state, setState] = React.useState(null)
      const [error, setError] = React.useState('')
      const [busy, setBusy] = React.useState(false)

      const load = React.useCallback(async () => {
        setBusy(true)
        try {
          const res = await fetch('/qq-agent/state', { cache: 'no-store' })
          const data = await res.json()
          if (!data.ok) throw new Error(data.error || t('error'))
          setState(data)
          setError('')
        } catch (e) {
          setError(String((e && e.message) || e))
        } finally {
          setBusy(false)
        }
      }, [t])

      React.useEffect(() => {
        load()
        const timer = setInterval(load, 15000)
        return () => clearInterval(timer)
      }, [load])

      const status = (state && state.status) || {}
      const qq = status.qq || {}
      const persona = status.persona || {}
      const running = !!(state && state.running)
      const ws = !!(status && status.ws)
      const dryRun = !!(status && status.dryRun)
      const friends = (state && state.friends) || []
      const convs = (state && state.conversations) || []
      const log = (state && state.log) || []
      const whitelist = status.whitelist || {}
      const name = qq.displayName || qq.nickname || qq.uin || t('qq')

      const header = h(
        'div',
        {
          style: {
            display: 'flex',
            alignItems: 'center',
            gap: 12,
            padding: '0 0 16px',
            borderBottom: '1px solid ' + T.border,
            marginBottom: 16,
          },
        },
        h(
          'div',
          { style: { flex: 1, minWidth: 0 } },
          h('div', { style: { fontSize: 18, fontWeight: 600, color: T.fg } }, t('title')),
          h(
            'div',
            { style: { fontSize: 12, color: T.dim, marginTop: 2 } },
            name + ' · ' + (running ? t('serviceOn') : t('serviceOff')),
          ),
        ),
        h(
          'button',
          {
            type: 'button',
            onClick: load,
            disabled: busy,
            style: {
              padding: '6px 14px',
              borderRadius: 8,
              border: '1px solid ' + T.border,
              background: T.bg,
              color: T.fg,
              fontSize: 13,
              cursor: busy ? 'default' : 'pointer',
              opacity: busy ? 0.6 : 1,
            },
          },
          busy ? t('refreshing') : t('refresh'),
        ),
      )

      if (error) {
        return h(
          'div',
          { style: { padding: 24, color: T.fg } },
          header,
          h(Card, null, h('div', { style: { color: T.bad, fontSize: 13 } }, t('error') + '：' + error)),
        )
      }
      if (!state) {
        return h('div', { style: { padding: 24, color: T.dim, fontSize: 13 } }, t('loading'))
      }

      const statusCard = h(
        Card,
        null,
        h(
          'div',
          { style: { display: 'flex', gap: 22, flexWrap: 'wrap' } },
          h(Stat, {
            label: t('serviceOn'),
            value: running ? t('serviceOn') : t('serviceOff'),
            color: running ? T.ok : T.bad,
            small: true,
          }),
          h(Stat, {
            label: 'WS',
            value: ws ? t('wsOn') : t('wsOff'),
            color: ws ? T.ok : T.bad,
            small: true,
          }),
          h(Stat, {
            label: t('whitelist'),
            value: `${whitelist.friends || 0} / ${whitelist.groups || 0}`,
            small: true,
          }),
          h(Stat, {
            label: 'dry-run',
            value: running ? (dryRun ? t('dryRunOn') : t('dryRunOff')) : '—',
            color: running ? (dryRun ? T.warn : T.bad) : T.dim,
            small: true,
          }),
        ),
        !running
          ? h(
              'div',
              { style: { marginTop: 10, fontSize: 12, color: T.dim, lineHeight: 1.6 } },
              h('div', null, t('startHint') + ' qq-agent qq --listen'),
              h('div', { style: { fontFamily: mono, fontSize: 11, marginTop: 2 } }, state.home || ''),
            )
          : null,
      )

      const personaCard = h(
        Card,
        null,
        h(
          'div',
          { style: { display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' } },
          h('div', { style: { fontSize: 13, fontWeight: 600, color: T.fg } }, t('persona')),
          h(
            Pill,
            { color: persona.loaded ? T.ok : T.bad },
            persona.loaded
              ? `${persona.name || '—'} · ${persona.format || '—'} · ${persona.chars || 0} ${t('chars')}`
              : t('personaMissing'),
          ),
        ),
        persona.source
          ? h(
              'div',
              { style: { fontSize: 11, color: T.dim, marginTop: 8, fontFamily: mono, wordBreak: 'break-all' } },
              t('personaPath') + '：' + persona.source,
            )
          : null,
        (persona.warnings || []).length
          ? h(
              'div',
              { style: { fontSize: 12, color: T.warn, marginTop: 8 } },
              t('personaWarn') + '：' + persona.warnings.join('；'),
            )
          : null,
      )

      const friendList = h(
        'div',
        { style: { marginTop: 20 } },
        h('div', { style: { fontSize: 13, fontWeight: 600, color: T.fg, marginBottom: 10 } }, t('friends')),
        friends.length === 0
          ? h('div', { style: { fontSize: 13, color: T.dim } }, t('emptyFriends'))
          : friends.map((f) =>
              h(
                'div',
                {
                  key: f.channel,
                  style: {
                    display: 'flex',
                    alignItems: 'flex-start',
                    gap: 12,
                    padding: '10px 0',
                    borderBottom: '1px solid ' + T.border,
                  },
                },
                h(
                  'div',
                  { style: { flex: 1, minWidth: 0 } },
                  h(
                    'div',
                    { style: { fontSize: 14, fontWeight: 500, color: T.fg } },
                    f.name || f.channel,
                    f.nickname && f.nickname !== f.name
                      ? h('span', { style: { fontSize: 12, color: T.dim, marginLeft: 6 } }, f.nickname)
                      : null,
                  ),
                  f.impression
                    ? h(
                        'div',
                        { style: { fontSize: 12, color: T.dim, marginTop: 3, lineHeight: 1.5 } },
                        f.impression,
                      )
                    : null,
                ),
                h(
                  'div',
                  { style: { fontSize: 12, color: T.dim, whiteSpace: 'nowrap' } },
                  `${f.msgs} ${t('msgs')} · ${f.avgLen} ${t('chars')}`,
                ),
              ),
            ),
      )

      const convList = h(
        'div',
        { style: { marginTop: 20 } },
        h('div', { style: { fontSize: 13, fontWeight: 600, color: T.fg, marginBottom: 10 } }, t('conversations')),
        convs.length === 0
          ? h('div', { style: { fontSize: 13, color: T.dim } }, t('emptyConvs'))
          : convs.map((c) =>
              h(
                'div',
                {
                  key: c.channel,
                  style: { padding: '8px 0', borderBottom: '1px solid ' + T.border, fontSize: 13 },
                },
                h(
                  'div',
                  { style: { display: 'flex', gap: 8, color: T.dim, fontSize: 12 } },
                  h('span', null, c.channel),
                  h('span', null, '· ' + c.turns + ' ' + t('turns')),
                  h('span', null, '· ' + fmtTime(c.lastAt)),
                ),
                h(
                  'div',
                  {
                    style: {
                      color: T.fg,
                      marginTop: 3,
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                      whiteSpace: 'nowrap',
                    },
                  },
                  c.preview || t('noPreview'),
                ),
              ),
            ),
      )

      const logBox = h(
        'div',
        { style: { marginTop: 20 } },
        h('div', { style: { fontSize: 13, fontWeight: 600, color: T.fg, marginBottom: 10 } }, t('log')),
        h(
          'pre',
          {
            style: {
              margin: 0,
              padding: 12,
              background: T.card,
              border: '1px solid ' + T.border,
              borderRadius: 10,
              fontSize: 11,
              lineHeight: 1.7,
              fontFamily: mono,
              color: T.dim,
              maxHeight: 240,
              overflow: 'auto',
              whiteSpace: 'pre-wrap',
              wordBreak: 'break-all',
            },
          },
          log.length ? log.join('\n') : t('emptyLog'),
        ),
      )

      return h(
        'div',
        {
          style: {
            height: '100%',
            overflow: 'auto',
            padding: '20px 24px',
            background: T.bg,
            color: T.fg,
          },
        },
        header,
        statusCard,
        h('div', { style: { height: 12 } }),
        personaCard,
        friendList,
        convList,
        logBox,
      )
    }

    return {
      inject: ['slots', 'locale'],
      apply(ctx) {
        ctx.effect(() => ctx.locale.register(NS, { zh, en }), 'dsh-qq-agent:dictionaries')
        const fallback = ctx.locale.bind(NS)
        ctx.slots.inject('sidebar.panellist', () =>
          ctx.slots.register(
            { name: 'sidebar.panellist', id: 'qq-agent', order: 70, label: () => fallback('panel'), locale: NS },
            Icon,
          ),
        )
        ctx.slots.inject('main', () =>
          ctx.slots.register(
            { name: 'main', key: 'qq-agent', locale: NS, inject: () => ({}) },
            (props) => h(Panel, { t: props.t || fallback }),
          ),
        )
      },
    }
  },
})
