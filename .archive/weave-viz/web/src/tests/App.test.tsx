import { render, screen, waitFor } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import App from '../App'

const mockGraphData = {
  nodes: [
    { id: '1', name: 'Alice', email: '', org: 'Acme', occupation: 'Engineer', location_city: 'SF', location_country: 'US', confidence: 1, source_type: 'test', preference_count: 0, fact_count: 0, color: '#abc', x: 100, y: 100, vx: 0, vy: 0 },
    { id: '2', name: 'Bob', email: '', org: 'Acme', occupation: 'Designer', location_city: 'NYC', location_country: 'US', confidence: 1, source_type: 'test', preference_count: 0, fact_count: 0, color: '#def', x: 200, y: 200, vx: 0, vy: 0 },
  ],
  links: [
    { source: '1', target: '2', rel_type: 'colleague_of', strength: 0.8, context: '', confidence: 1 },
  ],
  stats: { total_nodes: 2, total_links: 1, total_preferences: 0, total_facts: 0 },
}

describe('Weave Visualizer', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    global.fetch = vi.fn((url: string) => {
      console.log('[TEST] fetch called:', url)
      if (url === '/api/graph') {
        return Promise.resolve({ ok: true, json: () => Promise.resolve(mockGraphData) } as Response)
      }
      if (url === '/api/health') {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({ status: 'ok' }) } as Response)
      }
      if (url.startsWith('/api/person/')) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({ person: {}, preferences: [], facts: [], connections_out: [], connections_in: [] }) } as Response)
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve({}) } as Response)
    }) as any
  })

  it('renders loading state initially', () => {
    render(<App />)
    expect(screen.getByText(/loading weave graph/i)).toBeInTheDocument()
  })

  it('transitions from loading to main view', async () => {
    const { container } = render(<App />)
    // Initially shows loading
    expect(screen.getByText(/loading weave graph/i)).toBeInTheDocument()
    
    // Wait for fetch to complete
    await waitFor(() => {
      // After loading, the header should be visible
      const header = document.querySelector('header')
      expect(header).toBeInTheDocument()
    }, { timeout: 10000 })
    
    console.log('[TEST] Container HTML after load:', container.innerHTML.substring(0, 500))
  })
})
