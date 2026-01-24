import express from 'express'
import cors from 'cors'
import { spawn } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import { dirname, resolve, join } from 'node:path'
import { promises as fs } from 'node:fs'

const app = express()
const port = Number(process.env.PORT || 5174)
const __dirname = dirname(fileURLToPath(import.meta.url))
const tracksRoot = process.env.TRACKS_ROOT || resolve(__dirname, '..', '..', 'tracks')

app.use(cors())
app.use(express.json({ limit: '2mb' }))

const slugify = (value) => {
  return value
    .normalize('NFKD')
    .replace(/[^\x00-\x7F]/g, '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/-{2,}/g, '-')
    .replace(/^-|-$/g, '')
}

const runTf = (args) =>
  new Promise((resolvePromise) => {
    const child = spawn('tf', args, { shell: true })
    let stdout = ''
    let stderr = ''
    child.stdout.on('data', (data) => {
      stdout += data.toString()
    })
    child.stderr.on('data', (data) => {
      stderr += data.toString()
    })
    child.on('close', (code) => {
      resolvePromise({ code, stdout, stderr })
    })
  })

app.get('/api/health', (_req, res) => {
  res.json({ ok: true, tracksRoot })
})

app.post('/api/build', async (req, res) => {
  const { query, outSlug, config, verbose, variants } = req.body || {}
  if (!query) {
    res.status(400).json({ ok: false, error: 'query is required' })
    return
  }
  const args = [variants ? 'build-variants' : 'build', query]
  if (outSlug) args.push('--out-slug', outSlug)
  if (config && !variants) args.push('--config', config)
  if (verbose) args.push('--verbose')
  const result = await runTf(args)
  const trackId = slugify(outSlug || query)
  res.json({ ok: result.code === 0, trackId, config, ...result })
})

app.post('/api/qa', async (req, res) => {
  const { trackId, config = 'default' } = req.body || {}
  if (!trackId) {
    res.status(400).json({ ok: false, error: 'trackId is required' })
    return
  }
  const result = await runTf(['qa', trackId, '--config', config])
  res.json({ ok: result.code === 0, trackId, config, ...result })
})

app.post('/api/render', async (req, res) => {
  const { trackId, config = 'default' } = req.body || {}
  if (!trackId) {
    res.status(400).json({ ok: false, error: 'trackId is required' })
    return
  }
  const result = await runTf(['render', trackId, '--config', config])
  res.json({ ok: result.code === 0, trackId, config, ...result })
})

app.get('/api/tracks/:trackId/configs', async (req, res) => {
  const dir = join(tracksRoot, req.params.trackId)
  try {
    const entries = await fs.readdir(dir, { withFileTypes: true })
    const configs = entries.filter((entry) => entry.isDirectory()).map((entry) => entry.name)
    res.json({ ok: true, configs })
  } catch (error) {
    res.status(404).json({ ok: false, error: String(error) })
  }
})

app.get('/api/tracks', async (_req, res) => {
  try {
    const entries = await fs.readdir(tracksRoot, { withFileTypes: true })
    const tracks = entries.filter((entry) => entry.isDirectory()).map((entry) => entry.name)
    tracks.sort((a, b) => a.localeCompare(b))
    res.json({ ok: true, tracks })
  } catch (error) {
    res.status(500).json({ ok: false, error: String(error) })
  }
})

app.get('/api/tracks/:trackId/:config/outputs', async (req, res) => {
  const dir = join(tracksRoot, req.params.trackId, req.params.config)
  try {
    const entries = await fs.readdir(dir, { withFileTypes: true })
    const files = []
    for (const entry of entries) {
      if (!entry.isFile()) continue
      const filePath = join(dir, entry.name)
      const stats = await fs.stat(filePath)
      files.push({
        name: entry.name,
        size: stats.size,
        updatedAt: stats.mtimeMs,
      })
    }
    files.sort((a, b) => a.name.localeCompare(b.name))
    res.json({ ok: true, files })
  } catch (error) {
    res.status(404).json({ ok: false, error: String(error) })
  }
})

app.listen(port, () => {
  console.log(`TrackFactory API listening on http://localhost:${port}`)
  console.log(`Tracks root: ${tracksRoot}`)
})
