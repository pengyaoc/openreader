import type { RefreshReport } from '../api'

interface Props {
  report: RefreshReport
  onClose: () => void
}

export function RefreshToast({ report, onClose }: Props) {
  return (
    <div className="toast">
      <button className="toast__close" onClick={onClose}>
        ✕
      </button>
      <div className="toast__title">Refreshed in {report.elapsed_ms}ms</div>
      {report.sources.map((s) => (
        <div key={s.key} className={`toast__row ${s.status === 'error' ? 'toast__row--error' : ''}`}>
          <span>{s.key}</span>
          <span>
            {s.status === 'ok' && `+${s.new} new, ${s.filtered} filtered`}
            {s.status === 'not_modified' && 'unchanged'}
            {s.status === 'error' && (s.error?.slice(0, 40) ?? 'error')}
          </span>
        </div>
      ))}
    </div>
  )
}
