import { useEffect, useMemo, useRef, useState } from 'react'
import './App.css'

type Canonical = {
  centerline?: number[][]
}

type Label = {
  id: string
  x: number
  y: number
  text: string
}

type StyleSettings = {
  stroke: string
  strokeWidth: number
  fill: string
  labelSize: number
}

const DEFAULT_STYLE: StyleSettings = {
  stroke: '#101010',
  strokeWidth: 6,
  fill: 'none',
  labelSize: 18,
}

const DEFAULT_TRACK_ID = 'daytona-international-speedway'
const DEFAULT_CONFIG = 'default'

function App() {
  const [trackId, setTrackId] = useState(DEFAULT_TRACK_ID)
  const [config, setConfig] = useState(DEFAULT_CONFIG)
  const [baseUrl, setBaseUrl] = useState(() => localStorage.getItem('tf.baseUrl') ?? '/tracks')
  const [svgFileName, setSvgFileName] = useState('source_wiki.svg')
  const [mode, setMode] = useState<'canonical' | 'svg'>('canonical')
  const [canonical, setCanonical] = useState<Canonical | null>(null)
  const [canonicalText, setCanonicalText] = useState('')
  const [svgSource, setSvgSource] = useState('')
  const [labels, setLabels] = useState<Label[]>([])
  const [labelText, setLabelText] = useState('')
  const [labelMode, setLabelMode] = useState(false)
  const [style, setStyle] = useState<StyleSettings>(DEFAULT_STYLE)
  const [status, setStatus] = useState<string | null>(null)
  const svgRef = useRef<SVGSVGElement | null>(null)

  const basePath = useMemo(() => {
    const trimmed = baseUrl.trim()
    if (!trimmed) return '/tracks'
    return trimmed.endsWith('/') ? trimmed.slice(0, -1) : trimmed
  }, [baseUrl])

  const buildTrackUrl = (filename: string) => `${basePath}/${trackId}/${config}/${filename}`

  const points = useMemo(() => canonical?.centerline ?? [], [canonical])
  const viewBox = useMemo(() => {
    if (!points.length) return '0 0 100 100'
    const xs = points.map((p) => p[0])
    const ys = points.map((p) => p[1])
    const minX = Math.min(...xs)
    const maxX = Math.max(...xs)
    const minY = Math.min(...ys)
    const maxY = Math.max(...ys)
    const width = maxX - minX
    const height = maxY - minY
    const pad = Math.max(width, height) * 0.08 + 1
    return `${minX - pad} ${minY - pad} ${width + pad * 2} ${height + pad * 2}`
  }, [points])

  const pathD = useMemo(() => {
    if (!points.length) return ''
    const head = points[0]
    const segments = points.slice(1).map((p) => `L ${p[0]} ${p[1]}`)
    return `M ${head[0]} ${head[1]} ${segments.join(' ')} Z`
  }, [points])

  const setStatusLine = (message: string) => {
    setStatus(message)
    window.setTimeout(() => setStatus(null), 4000)
  }

  const loadCanonicalFromPath = async () => {
    try {
      const resp = await fetch(buildTrackUrl('canonical.json'))
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
      const text = await resp.text()
      const parsed = JSON.parse(text) as Canonical
      setCanonical(parsed)
      setCanonicalText(JSON.stringify(parsed, null, 2))
      setMode('canonical')
      setStatusLine(`Loaded canonical.json for ${trackId}/${config}`)
    } catch (err) {
      setStatusLine(`Failed to load canonical.json: ${String(err)}`)
    }
  }

  const loadLabelsFromPath = async () => {
    try {
      const resp = await fetch(buildTrackUrl('labels.json'))
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
      const parsed = (await resp.json()) as Label[]
      setLabels(parsed)
      setStatusLine(`Loaded labels.json for ${trackId}/${config}`)
    } catch (err) {
      setStatusLine(`Failed to load labels.json: ${String(err)}`)
    }
  }

  const loadSourceSvg = async () => {
    try {
      const resp = await fetch(buildTrackUrl(svgFileName))
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
      const text = await resp.text()
      setSvgSource(text)
      setMode('svg')
      setStatusLine(`Loaded ${svgFileName} for ${trackId}/${config}`)
    } catch (err) {
      setStatusLine(`Failed to load ${svgFileName}: ${String(err)}`)
    }
  }

  const handleCanonicalFile = async (file?: File) => {
    if (!file) return
    const text = await file.text()
    try {
      const parsed = JSON.parse(text) as Canonical
      setCanonical(parsed)
      setCanonicalText(JSON.stringify(parsed, null, 2))
      setMode('canonical')
      setStatusLine(`Loaded canonical from file: ${file.name}`)
    } catch (err) {
      setStatusLine(`Invalid canonical JSON: ${String(err)}`)
    }
  }

  const handleSvgFile = async (file?: File) => {
    if (!file) return
    const text = await file.text()
    setSvgSource(text)
    setMode('svg')
    setStatusLine(`Loaded SVG from file: ${file.name}`)
  }

  const applyCanonicalJson = () => {
    try {
      const parsed = JSON.parse(canonicalText) as Canonical
      setCanonical(parsed)
      setStatusLine('Applied canonical JSON')
    } catch (err) {
      setStatusLine(`Invalid JSON: ${String(err)}`)
    }
  }

  const downloadText = (filename: string, text: string) => {
    const blob = new Blob([text], { type: 'text/plain' })
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = filename
    anchor.click()
    URL.revokeObjectURL(url)
  }

  const exportSvg = () => {
    if (!pathD) {
      setStatusLine('No geometry to export')
      return
    }
    const labelMarkup = labels
      .map(
        (label) =>
          `<text x="${label.x}" y="${label.y}" font-size="${style.labelSize}" text-anchor="middle" fill="${style.stroke}">${label.text}</text>`,
      )
      .join('')
    const svg = `
<svg xmlns="http://www.w3.org/2000/svg" viewBox="${viewBox}" width="100%" height="100%">
  <path d="${pathD}" fill="${style.fill}" stroke="${style.stroke}" stroke-width="${style.strokeWidth}" stroke-linejoin="round" stroke-linecap="round"/>
  ${labelMarkup}
</svg>`.trim()
    downloadText(`${trackId}-${config}-export.svg`, svg)
  }

  const exportLabels = () => {
    downloadText(`${trackId}-${config}-labels.json`, JSON.stringify(labels, null, 2))
  }

  const onCanvasClick = (event: React.MouseEvent<SVGSVGElement>) => {
    if (!labelMode || mode !== 'canonical') return
    if (!svgRef.current) return
    const rect = svgRef.current.getBoundingClientRect()
    const [vbX, vbY, vbW, vbH] = viewBox.split(' ').map(Number)
    const scaleX = vbW / rect.width
    const scaleY = vbH / rect.height
    const x = vbX + (event.clientX - rect.left) * scaleX
    const y = vbY + (event.clientY - rect.top) * scaleY
    const text = labelText.trim() || `${labels.length + 1}`
    setLabels((prev) => [...prev, { id: `${Date.now()}`, x, y, text }])
  }

  useEffect(() => {
    localStorage.setItem('tf.baseUrl', baseUrl)
  }, [baseUrl])

  useEffect(() => {
    loadCanonicalFromPath()
  }, [])

  return (
    <div className="app">
      <header className="app__header">
        <div className="app__title">
          TrackFactory Viewer
          <span className="app__title__tag">TUI</span>
        </div>
        <div className="app__status">{status ?? 'ready'}</div>
      </header>
      <main className="app__main">
        <aside className="panel">
          <section className="panel__section">
            <h2>Load</h2>
            <div className="field">
              <label>Tracks base URL</label>
              <input
                value={baseUrl}
                onChange={(e) => setBaseUrl(e.target.value)}
                placeholder="/tracks or /@fs/C:/path/to/tracks"
              />
            </div>
            <div className="field">
              <label>Track</label>
              <input value={trackId} onChange={(e) => setTrackId(e.target.value)} />
            </div>
            <div className="field">
              <label>Variant</label>
              <input value={config} onChange={(e) => setConfig(e.target.value)} />
            </div>
            <div className="button-row">
              <button onClick={loadCanonicalFromPath}>Load canonical.json</button>
              <button onClick={loadSourceSvg}>Load SVG</button>
            </div>
            <div className="button-row">
              <button onClick={loadLabelsFromPath}>Load labels.json</button>
            </div>
            <div className="field">
              <label>SVG filename</label>
              <input value={svgFileName} onChange={(e) => setSvgFileName(e.target.value)} />
            </div>
            <div className="field">
              <label>Open canonical.json</label>
              <input type="file" accept="application/json" onChange={(e) => handleCanonicalFile(e.target.files?.[0])} />
            </div>
            <div className="field">
              <label>Open SVG</label>
              <input type="file" accept="image/svg+xml" onChange={(e) => handleSvgFile(e.target.files?.[0])} />
            </div>
          </section>

          <section className="panel__section">
            <h2>Style</h2>
            <div className="field">
              <label>Stroke</label>
              <input
                type="color"
                value={style.stroke}
                onChange={(e) => setStyle((prev) => ({ ...prev, stroke: e.target.value }))}
              />
            </div>
            <div className="field">
              <label>Stroke width</label>
              <input
                type="range"
                min={1}
                max={24}
                value={style.strokeWidth}
                onChange={(e) => setStyle((prev) => ({ ...prev, strokeWidth: Number(e.target.value) }))}
              />
            </div>
            <div className="field">
              <label>Fill</label>
              <input
                type="text"
                value={style.fill}
                onChange={(e) => setStyle((prev) => ({ ...prev, fill: e.target.value }))}
              />
            </div>
            <div className="field">
              <label>Label size</label>
              <input
                type="range"
                min={10}
                max={32}
                value={style.labelSize}
                onChange={(e) => setStyle((prev) => ({ ...prev, labelSize: Number(e.target.value) }))}
              />
            </div>
          </section>

          <section className="panel__section">
            <h2>Labels</h2>
            <div className="field">
              <label>Label text</label>
              <input value={labelText} onChange={(e) => setLabelText(e.target.value)} />
            </div>
            <label className="toggle">
              <input type="checkbox" checked={labelMode} onChange={(e) => setLabelMode(e.target.checked)} />
              <span>Add labels on click</span>
            </label>
            <div className="button-row">
              <button onClick={exportLabels}>Export labels.json</button>
              <button onClick={() => setLabels([])}>Clear labels</button>
            </div>
          </section>

          <section className="panel__section">
            <h2>Export</h2>
            <div className="button-row">
              <button onClick={exportSvg}>Export SVG</button>
              <button onClick={applyCanonicalJson}>Apply JSON</button>
            </div>
          </section>
        </aside>

        <section className="viewer">
          <div className="viewer__tabs">
            <button className={mode === 'canonical' ? 'active' : ''} onClick={() => setMode('canonical')}>
              Canonical
            </button>
            <button className={mode === 'svg' ? 'active' : ''} onClick={() => setMode('svg')}>
              Source SVG
            </button>
          </div>
          <div className="viewer__canvas">
            {mode === 'canonical' ? (
              <svg ref={svgRef} viewBox={viewBox} onClick={onCanvasClick}>
                {pathD && (
                  <path
                    d={pathD}
                    fill={style.fill}
                    stroke={style.stroke}
                    strokeWidth={style.strokeWidth}
                    strokeLinejoin="round"
                    strokeLinecap="round"
                  />
                )}
                {labels.map((label) => (
                  <text
                    key={label.id}
                    x={label.x}
                    y={label.y}
                    fontSize={style.labelSize}
                    textAnchor="middle"
                    fill={style.stroke}
                  >
                    {label.text}
                  </text>
                ))}
              </svg>
            ) : (
              <div className="viewer__svg" dangerouslySetInnerHTML={{ __html: svgSource || '<p>no svg loaded</p>' }} />
            )}
          </div>
          <div className="viewer__editor">
            <div className="editor__header">canonical.json</div>
            <textarea
              value={canonicalText}
              onChange={(e) => setCanonicalText(e.target.value)}
              placeholder="Load canonical.json or paste it here"
            />
          </div>
        </section>
      </main>
    </div>
  )
}

export default App
