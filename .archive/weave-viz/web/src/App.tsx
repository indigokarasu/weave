import React, { useState, useEffect, useCallback, useRef, Component } from 'react';
import ForceGraph2D, { GraphData as ForceGraphData, NodeObject, ForceGraphNodeObject } from 'react-force-graph-2d';
import { Sigma, loadGraph, useLoadGraph } from 'graphology';
import { random as layoutRandom } from 'graphology-layout/random';
import forceAtlas2 from 'graphology-layout-forceatlas2';
import './styles/App.css';

// ── Error Boundary ──
interface ErrorBoundaryState { hasError: boolean; error: string }
export class ErrorBoundary extends Component<{ children: React.ReactNode }, ErrorBoundaryState> {
  state: ErrorBoundaryState = { hasError: false, error: '' }
  static getDerivedStateFromError(error: Error) {
    return { hasError: true, error: error.message }
  }
  render() {
    if (this.state.hasError) {
      return (
        <div style={{ padding: 40, fontFamily: 'sans-serif' }}>
          <h1>Something went wrong</h1>
          <pre style={{ background: '#f5f5f5', padding: 16, borderRadius: 8, overflow: 'auto' }}>
            {this.state.error}
          </pre>
        </div>
      )
    }
    return this.props.children
  }
}

// ── Types ──
interface GraphNode {
  id: string; name: string; email: string; org: string; occupation: string;
  location_city: string; location_country: string; confidence: number;
  source_type: string; preference_count: number; fact_count: number;
  color?: string; x?: number; y?: number; vx?: number; vy?: number;
  fx?: number | null; fy?: number | null;
}

interface GraphLink {
  source: string | GraphNode | ForceGraphNodeObject;
  target: string | GraphNode | ForceGraphNodeObject;
  rel_type: string; strength: number; context: string; confidence: number;
}

interface GraphData {
  nodes: GraphNode[]; links: GraphLink[];
  stats: { total_nodes: number; total_links: number; total_preferences: number; total_facts: number; };
}

interface PersonDetail {
  person: Record<string, any>;
  preferences: Record<string, any>[];
  facts: Record<string, any>[];
  connections_out: { name: string; id: string; rel_type: string; strength: number; context: string; }[];
  connections_in: { name: string; id: string; rel_type: string; strength: number; context: string; }[];
}

interface Toast { id: string; type: 'success' | 'error'; message: string; }

interface UndoEntry {
  type: 'create_person' | 'create_relationship' | 'create_preference' | 'delete_person' | 'delete_relationship' | 'edit_person';
  description: string; undo: () => Promise<boolean>;
}

type GroupMode = 'default' | 'company' | 'education' | 'city' | 'tag';
type Renderer = 'force' | 'sigma';

// ── Color helpers ──
const PALETTE = ['#4e79a7','#f28e2c','#e15759','#76b7b2','#59a14f','#edc949','#af7aa1','#ff9da7','#9c755f','#bab0ab'];
const REL_COLORS: Record<string, string> = {
  colleague_of: '#2563EB', friend_of: '#16A34A', family_of: '#D97706',
  coauthor: '#7C3AED', shared_field: '#0891B2', former_colleague: '#F59E0B',
  colleague: '#3B82F6', knows: '#6B7280',
};

function hashString(s: string): number {
  let h = 5381;
  for (let i = 0; i < s.length; i++) h = ((h << 5) + h) + s.charCodeAt(i);
  return h & 0xFFFFFFFF;
}

function getRelColor(relType: string): string {
  return REL_COLORS[relType] || '#6B7280';
}

function nodeColor(key: string): string {
  return PALETTE[hashString(key) % PALETTE.length];
}

function groupColor(key: string): string {
  const h = hashString(key);
  return `hsl(${h % 360}, 55%, 55%)`;
}

function getGroupKey(node: GraphNode, mode: GroupMode): string {
  switch (mode) {
    case 'company': return node.org || '(no org)';
    case 'education': return node.occupation || '(no role)';
    case 'city': return node.location_city || '(unknown)';
    case 'tag': return [node.org, node.location_city].filter(Boolean).join(' · ') || '(ungrouped)';
    default: return '';
  }
}

// ── API helpers ──
async function apiPost(path: string, body: any): Promise<any> {
  const res = await fetch(path, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || `Error ${res.status}`);
  return data;
}

function getEndpointId(ep: string | NodeObject | ForceGraphNodeObject): string {
  return typeof ep === 'object' ? String((ep as any).id ?? ep) : ep;
}

// ── Toast component ──
function Toaster({ toasts, dismiss }: { toasts: Toast[]; dismiss: (id: string) => void }) {
  useEffect(() => {
    if (toasts.length === 0) return;
    const timer = setTimeout(() => dismiss(toasts[0].id), 4000);
    return () => clearTimeout(timer);
  }, [toasts, dismiss]);

  return (
    <div className="toast-container">
      {toasts.map(t => (
        <div key={t.id} className={`toast toast-${t.type}`} onClick={() => dismiss(t.id)}>
          {t.type === 'success' ? '✓' : '✗'} {t.message}
        </div>
      ))}
    </div>
  );
}

// ── Edit forms (kept as-is) ──
function EditPersonForm({ onClose, onSubmit, initial }: {
  onClose: () => void; onSubmit: (data: any) => Promise<void>;
  initial?: Partial<GraphNode>;
}) {
  const [name, setName] = useState(initial?.name || '');
  const [email, setEmail] = useState(initial?.email || '');
  const [org, setOrg] = useState(initial?.org || '');
  const [occupation, setOccupation] = useState(initial?.occupation || '');
  const [location, setLocation] = useState(initial?.location_city || '');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');

  const handleSubmit = async () => {
    if (!name.trim()) { setError('Name is required'); return; }
    setSubmitting(true); setError('');
    try {
      await onSubmit({ ...initial, name: name.trim(), email: email.trim(), org: org.trim(), occupation: occupation.trim(), location_city: location.trim() });
    } catch (e: any) {
      setError(e.message);
      setSubmitting(false);
    }
  };

  return (
    <div className="edit-form">
      <div className="edit-form-header">
        <h3>{initial?.id ? 'Edit Person' : 'Add Person'}</h3>
        <button className="close-btn" onClick={onClose}>×</button>
      </div>
      <div className="edit-form-body">
        {error && <div className="error">{error}</div>}
        <input placeholder="Name *" value={name} onChange={e => setName(e.target.value)} />
        <input placeholder="Email" value={email} onChange={e => setEmail(e.target.value)} />
        <input placeholder="Organization" value={org} onChange={e => setOrg(e.target.value)} />
        <input placeholder="Occupation" value={occupation} onChange={e => setOccupation(e.target.value)} />
        <input placeholder="Location (City, State)" value={location} onChange={e => setLocation(e.target.value)} />
        <button className="btn-primary" onClick={handleSubmit} disabled={!name.trim() || submitting}>
          {submitting ? 'Saving…' : (initial?.id ? 'Update' : 'Add Person')}
        </button>
      </div>
    </div>
  );
}

function EditRelationshipForm({ nodes, onClose, onSubmit }: {
  nodes: GraphNode[]; onClose: () => void; onSubmit: (data: any) => Promise<void>;
}) {
  const [sourceQuery, setSourceQuery] = useState('');
  const [targetQuery, setTargetQuery] = useState('');
  const [relType, setRelType] = useState('colleague_of');
  const [context, setContext] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');
  const [sourceId, setSourceId] = useState('');
  const [targetId, setTargetId] = useState('');
  const [showSource, setShowSource] = useState(false);
  const [showTarget, setShowTarget] = useState(false);

  const filteredSource = sourceQuery ? nodes.filter(n => n.name.toLowerCase().includes(sourceQuery.toLowerCase())).slice(0, 8) : [];
  const filteredTarget = targetQuery ? nodes.filter(n => n.name.toLowerCase().includes(targetQuery.toLowerCase())).slice(0, 8) : [];

  const handleSubmit = async () => {
    if (!sourceId || !targetId) { setError('Select both people'); return; }
    if (sourceId === targetId) { setError('Source and target must be different'); return; }
    setSubmitting(true); setError('');
    try {
      await onSubmit({ source_id: sourceId, target_id: targetId, rel_type: relType, context });
    } catch (e: any) { setError(e.message); setSubmitting(false); }
  };

  return (
    <div className="edit-form">
      <div className="edit-form-header">
        <h3>Add Relationship</h3>
        <button className="close-btn" onClick={onClose}>×</button>
      </div>
      <div className="edit-form-body">
        {error && <div className="error">{error}</div>}
        <div className="edit-form-autocomplete">
          <input placeholder="From person…" value={showSource ? sourceQuery : nodes.find(n => n.id === sourceId)?.name || ''}
            onChange={e => { setSourceQuery(e.target.value); setShowSource(true); }} onFocus={() => setShowSource(true)} onBlur={() => setTimeout(() => setShowSource(false), 200)} />
          {showSource && filteredSource.length > 0 && (
            <div className="edit-form-dropdown">
              {filteredSource.map(n => <div key={n.id} className="edit-form-option" onMouseDown={() => { setSourceId(n.id); setSourceQuery(n.name); setShowSource(false); }}>{n.name}</div>)}
            </div>
          )}
        </div>
        <div className="edit-form-autocomplete">
          <input placeholder="To person…" value={showTarget ? targetQuery : nodes.find(n => n.id === targetId)?.name || ''}
            onChange={e => { setTargetQuery(e.target.value); setShowTarget(true); }} onFocus={() => setShowTarget(true)} onBlur={() => setTimeout(() => setShowTarget(false), 200)} />
          {showTarget && filteredTarget.length > 0 && (
            <div className="edit-form-dropdown">
              {filteredTarget.map(n => <div key={n.id} className="edit-form-option" onMouseDown={() => { setTargetId(n.id); setTargetQuery(n.name); setShowTarget(false); }}>{n.name}</div>)}
            </div>
          )}
        </div>
        <select value={relType} onChange={e => setRelType(e.target.value)}>
          {Object.keys(REL_COLORS).map(t => <option key={t} value={t}>{t.replace(/_/g, ' ')}</option>)}
        </select>
        <input placeholder="Context (optional)" value={context} onChange={e => setContext(e.target.value)} />
        <button className="btn-primary" onClick={handleSubmit} disabled={!sourceId || !targetId || submitting || sourceId === targetId}>
          {submitting ? 'Saving…' : 'Add Relationship'}
        </button>
      </div>
    </div>
  );
}

function EditPreferenceForm({ nodes, onClose, onSubmit, initial }: {
  nodes: GraphNode[]; onClose: () => void; onSubmit: (data: any) => Promise<void>;
  initial?: { person_id?: string; value?: string; category?: string };
}) {
  const [personQuery, setPersonQuery] = useState('');
  const [value, setValue] = useState(initial?.value || '');
  const [category, setCategory] = useState(initial?.category || 'interest');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');
  const [personId, setPersonId] = useState(initial?.person_id || '');
  const [showList, setShowList] = useState(false);

  const filtered = personQuery ? nodes.filter(n => n.name.toLowerCase().includes(personQuery.toLowerCase())).slice(0, 8) : [];

  const handleSubmit = async () => {
    if (!personId) { setError('Select a person'); return; }
    if (!value.trim()) { setError('Enter a preference'); return; }
    setSubmitting(true); setError('');
    try {
      await onSubmit({ person_id: personId, value: value.trim(), category });
    } catch (e: any) { setError(e.message); setSubmitting(false); }
  };

  return (
    <div className="edit-form">
      <div className="edit-form-header">
        <h3>{initial?.value ? 'Edit Preference' : 'Add Preference'}</h3>
        <button className="close-btn" onClick={onClose}>×</button>
      </div>
      <div className="edit-form-body">
        {error && <div className="error">{error}</div>}
        <div className="edit-form-autocomplete">
          <input placeholder="Person…" value={showList ? personQuery : nodes.find(n => n.id === personId)?.name || ''}
            onChange={e => { setPersonQuery(e.target.value); setShowList(true); }} onFocus={() => setShowList(true)} onBlur={() => setTimeout(() => setShowList(false), 200)} />
          {showList && filtered.length > 0 && (
            <div className="edit-form-dropdown">
              {filtered.map(n => <div key={n.id} className="edit-form-option" onMouseDown={() => { setPersonId(n.id); setPersonQuery(n.name); setShowList(false); }}>{n.name}</div>)}
            </div>
          )}
        </div>
        <input placeholder="What do they like/dislike?" value={value} onChange={e => setValue(e.target.value)} />
        <select value={category} onChange={e => setCategory(e.target.value)}>
          <option value="interest">Interest</option><option value="topic">Topic</option>
          <option value="skill">Skill</option><option value="food">Food</option>
          <option value="media">Media</option><option value="general">General</option>
        </select>
        <button className="btn-primary" onClick={handleSubmit} disabled={!personId || !value.trim() || submitting}>
          {submitting ? 'Saving…' : (initial?.value ? 'Update' : 'Add Preference')}
        </button>
      </div>
    </div>
  );
}

function DeleteConfirm({ message, onClose, onConfirm }: { message: string; onClose: () => void; onConfirm: () => Promise<void> }) {
  const [submitting, setSubmitting] = useState(false);
  return (
    <div className="edit-form">
      <div className="edit-form-header"><h3>Confirm</h3></div>
      <div className="edit-form-body">
        <p className="delete-message">{message}</p>
        <div className="delete-actions">
          <button className="btn-secondary" onClick={onClose} disabled={submitting}>Cancel</button>
          <button className="btn-danger" onClick={async () => { setSubmitting(true); await onConfirm(); }} disabled={submitting}>
            {submitting ? 'Deleting…' : 'Delete'}
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Group cluster computation ──
interface GroupCenter { x: number; y: number; count: number; r: number; }

function computeGroupBounds(nodes: GraphNode[], mode: GroupMode, w: number, h: number, nodeMap: Map<string, number>): GroupCenter[] {
  if (mode === 'default' || nodes.length === 0) return [];
  const groups = new Map<string, { cx: number; cy: number; count: number; maxDist: number }>();

  // First pass: compute centroids
  nodes.forEach(n => {
    if (n.x == null || n.y == null) return;
    const key = getGroupKey(n, mode);
    if (!groups.has(key)) groups.set(key, { cx: 0, cy: 0, count: 0, maxDist: -1 });
    const g = groups.get(key)!;
    g.cx += n.x;
    g.cy += n.y;
    g.count++;
  });

  // Normalize centroids
  groups.forEach(g => {
    if (g.count > 0) { g.cx /= g.count; g.cy /= g.count; }
  });

  // Second pass: compute bounding radius
  nodes.forEach(n => {
    if (n.x == null || n.y == null) return;
    const key = getGroupKey(n, mode);
    const g = groups.get(key); if (!g) return;
    const dx = n.x - g.cx, dy = n.y - g.cy;
    g.maxDist = Math.max(g.maxDist, Math.sqrt(dx * dx + dy * dy));
  });

  return Array.from(groups.entries()).map(([name, g]) => ({
    x: g.cx, y: g.cy, count: g.count, r: Math.max(50, g.maxDist + 30)
  }));
}

// ── Sigma Renderer Component ──
function SigmaGraphView({ graphData, onNodeClick, darkMode, getNodeColor, getNodeSize }: {
  graphData: ForceGraphData;
  onNodeClick: (nodeId: string) => void;
  darkMode: boolean;
  getNodeColor: (node: any) => string;
  getNodeSize: (node: any) => number;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const sigmaRef = useRef<any>(null);

  useEffect(() => {
    if (!containerRef.current || !graphData.nodes.length) return;

    const graph = new (window as any).sigma.Graph();
    graphData.nodes.forEach(n => {
      graph.addNode(n.id, {
        label: n.name,
        size: getNodeSize(n),
        color: n.color || getNodeColor(n),
        x: (n.x || 0.5) * 1000,
        y: (n.y || 0.5) * 1000,
      });
    });
    graphData.links.forEach((l: any) => {
      const sid = typeof l.source === 'object' ? l.source.id : l.source;
      const tid = typeof l.target === 'object' ? l.target.id : l.target;
      graph.addEdge(sid, tid, { label: l.rel_type || '' });
    });

    const instance = new (window as any).sigma.Sigma(graph, containerRef.current, {
      labelSize: 12,
      labelColor: { color: darkMode ? '#e0e0e0' : '#333' },
      edgeColor: { color: darkMode ? '#555' : '#999' },
      defaultNodeColor: '#6366f1',
      defaultEdgeColor: '#999',
      allowInvalidContainer: true,
      minEdgeThickness: 0.5,
      maxEdgeThickness: 2,
    });

    sigmaRef.current = instance;
    return () => { instance.kill(); };
  }, [graphData.nodes.length]); // Re-init on data length change

  return <div ref={containerRef} style={{ width: '100%', height: '100%' }} />;
}

// ── Main App ──
const App: React.FC = () => {
  const [graphData, setGraphData] = useState<GraphData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedNode, setSelectedNode] = useState<GraphNode | null>(null);
  const [personDetail, setPersonDetail] = useState<PersonDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const [relTypeFilter, setRelTypeFilter] = useState<string>('all');
  const [darkMode, setDarkMode] = useState(false);
  const [showStats, setShowStats] = useState(true);
  const [snapshotAge, setSnapshotAge] = useState(0);
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [undoStack, setUndoStack] = useState<UndoEntry[]>([]);
  const [groupMode, setGroupMode] = useState<GroupMode>('default');
  const [renderer, setRenderer] = useState<Renderer>('force');

  // Edit state
  const [editMode, setEditMode] = useState(false);
  const [editPanel, setEditPanel] = useState<string | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<{ type: string; id?: string; link?: GraphLink; label: string; pref?: Record<string, any>; person_id?: string } | null>(null);
  const [clickPosition, setClickPosition] = useState<{ x: number; y: number } | null>(null);
  const [groupModeForRender, setGroupModeForRender] = useState<GroupMode>('default');

  const graphRef = useRef<any>(null);
  const graphDataRef = useRef<GraphData | null>(null);

  const addToast = useCallback((type: 'success' | 'error', message: string) => {
    const id = `${Date.now()}-${Math.random()}`;
    setToasts(prev => [...prev, { id, type, message }]);
  }, []);

  const dismissToast = useCallback((id: string) => {
    setToasts(prev => prev.filter(t => t.id !== id));
  }, []);

  const pushUndo = useCallback((entry: UndoEntry) => {
    setUndoStack(prev => [...prev.slice(-19), entry]);
  }, []);

  const handleUndo = useCallback(async () => {
    const stack = [...undoStack];
    const entry = stack.pop();
    if (!entry) return;
    const ok = await entry.undo();
    if (ok) {
      setUndoStack(stack);
      addToast('success', `Undone: ${entry.description}`);
      try {
        const res = await fetch('/api/graph');
        const data = await res.json();
        setGraphData(data);
        graphDataRef.current = data;
      } catch { /* ignore */ }
    } else {
      addToast('error', `Could not undo: ${entry.description}`);
    }
  }, [undoStack, addToast]);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === 'z' && !e.shiftKey) { e.preventDefault(); handleUndo(); }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [handleUndo]);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { setEditPanel(null); setDeleteTarget(null); }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, []);

  // ── Fetch graph data ──
  useEffect(() => {
    console.log('[Weave] Starting graph fetch...');
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch('/api/graph');
        if (!res.ok) throw new Error(`API error: ${res.status}`);
        const data = await res.json();
        if (cancelled) return;
        // Normalize API response: edges → links with rel_type/strength defaults
        const normalized: GraphData = {
          nodes: data.nodes || [],
          links: (data.edges || []).map((e: any) => ({
            source: e.source,
            target: e.target,
            rel_type: e.rel_type || 'knows',
            strength: e.strength || 0.5,
            context: e.context || '',
            confidence: e.confidence || 1,
          })),
          stats: data.stats || { total_nodes: 0, total_links: 0, total_preferences: 0, total_facts: 0 },
        };
        setGraphData(normalized);
        graphDataRef.current = normalized;
        setError(null);
      } catch (err: any) {
        if (cancelled) return;
        setError(err.message || 'Failed to load graph data');
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    const healthInterval = setInterval(async () => {
      try {
        const res = await fetch('/api/health');
        const data = await res.json();
        setSnapshotAge(data.snapshot_age_seconds || 0);
      } catch { /* ignore */ }
    }, 60000);
    return () => { cancelled = true; clearInterval(healthInterval); };
  }, []);

  // ── Fetch person detail ──
  const fetchPersonDetail = useCallback(async (personId: string) => {
    if (!personId) { setPersonDetail(null); setSelectedNode(null); return; }
    setDetailLoading(true);
    try {
      const res = await fetch(`/api/person/${encodeURIComponent(personId)}`);
      if (!res.ok) throw new Error('Failed to load person');
      const data = await res.json();
      const p = data.person;
      // Build connections from graph links so we get rel_type and direction
      const allLinks = graphDataRef.current?.links || [];
      const connections_out: any[] = [];
      const connections_in: any[] = [];
      allLinks.forEach(l => {
        const sid = typeof l.source === 'object' ? (l.source as any).id : l.source;
        const tid = typeof l.target === 'object' ? (l.target as any).id : l.target;
        if (sid === personId) {
          const targetNode = graphDataRef.current?.nodes.find(n => n.id === tid);
          connections_out.push({ id: tid, name: targetNode?.name || tid, rel_type: l.rel_type || '', strength: l.strength || 0, context: l.context || '' });
        }
        if (tid === personId) {
          const sourceNode = graphDataRef.current?.nodes.find(n => n.id === sid);
          connections_in.push({ id: sid, name: sourceNode?.name || sid, rel_type: l.rel_type || '', strength: l.strength || 0, context: l.context || '' });
        }
      });
      setPersonDetail({
        ...p,
        connections_out,
        connections_in,
      });
      setSelectedNode(graphDataRef.current?.nodes.find(n => n.id === personId) || null);
    } catch (err: any) {
      addToast('error', err.message);
    } finally {
      setDetailLoading(false);
    }
  }, [addToast]);

  // ── Node click handler ──
  const handleNodeClick = useCallback((node: any) => {
    const nodeId = typeof node === 'object' ? node.id : node;
    console.log('[Weave] node clicked:', nodeId);
    fetchPersonDetail(nodeId);
  }, [fetchPersonDetail]);

  // ── Refresh snapshot ──
  const handleRefresh = useCallback(async () => {
    try {
      await fetch('/api/refresh', { method: 'POST' });
      addToast('success', 'Snapshot refreshed');
      setTimeout(() => window.location.reload(), 500);
    } catch { addToast('error', 'Refresh failed'); }
  }, [addToast]);

  // ── Apply grouping (re-fetches graph with group mode) ──
  const handleGroupModeChange = useCallback((mode: GroupMode) => {
    setGroupMode(mode);
    // The group mode is visual-only: we re-color nodes locally
    // No API call needed — just re-render with new colors
  }, []);

  // ── Filtered data for the force graph ──
  const filteredGraphData = React.useMemo(() => {
    if (!graphData) return { nodes: [], links: [] };
    let nodes = [...graphData.nodes];
    let links = [...graphData.links];

    if (searchQuery) {
      const q = searchQuery.toLowerCase();
      nodes = nodes.filter(n =>
        n.name.toLowerCase().includes(q) ||
        (n.org || '').toLowerCase().includes(q) ||
        (n.occupation || '').toLowerCase().includes(q) ||
        (n.location_city || '').toLowerCase().includes(q)
      );
    }
    const nodeIds = new Set(nodes.map(n => n.id));
    if (relTypeFilter !== 'all') {
      links = links.filter(l => l.rel_type === relTypeFilter);
    }
    links = links.filter(l => nodeIds.has(getEndpointId(l.source)) && nodeIds.has(getEndpointId(l.target)));

    return { nodes, links };
  }, [graphData, searchQuery, relTypeFilter]);

  // ── Node/edge visual properties ──
  const nodeColorMap = React.useRef<Record<string, string>>({});
  const edgeColorMap = React.useRef<Record<string, string>>({});

  const getNodeColor = useCallback((node: any) => {
    if (groupMode !== 'default') {
      const key = getGroupKey(node, groupMode);
      if (!nodeColorMap.current[key]) nodeColorMap.current[key] = groupColor(key);
      return nodeColorMap.current[key];
    }
    const key = node.org || node.id;
    if (!nodeColorMap.current[key]) nodeColorMap.current[key] = nodeColor(key);
    return nodeColorMap.current[key];
  }, [groupMode]);

  const getEdgeColor = useCallback((link: any) => {
    const label = link.rel_type || link.label || '';
    if (!edgeColorMap.current[label]) edgeColorMap.current[label] = getRelColor(label);
    return edgeColorMap.current[label];
  }, []);

  const getNodeSize = useCallback((node: any) => {
    const prefs = node.preference_count || 0;
    const facts = node.fact_count || 0;
    return Math.max(4, Math.min(14, 4 + Math.sqrt((prefs + facts + 1) * 2)));
  }, []);

  // ── Paint node for ForceGraph2D canvas ──
  const paintNode = useCallback((node: any, ctx: CanvasRenderingContext2D) => {
    const size = getNodeSize(node);
    const color = node.color || getNodeColor(node);
    const isSelected = selectedNode?.id === node.id;

    // Glow for selected
    if (isSelected) {
      ctx.save();
      ctx.shadowColor = '#f59e0b';
      ctx.shadowBlur = 16;
      ctx.fillStyle = '#f59e0b';
      ctx.beginPath();
      ctx.arc(node.x, node.y, size + 4, 0, 2 * Math.PI);
      ctx.fill();
      ctx.restore();
    }

    // Node circle
    ctx.fillStyle = color;
    ctx.beginPath();
    ctx.arc(node.x, node.y, size, 0, 2 * Math.PI);
    ctx.fill();

    // Border
    ctx.strokeStyle = isSelected ? '#f59e0b' : (darkMode ? '#1a1a2e' : '#fff');
    ctx.lineWidth = isSelected ? 3 : 1.5;
    ctx.stroke();

    // Label — always show
    if (node.name) {
      const fontSize = Math.max(9, 11);
      ctx.font = `500 ${fontSize}px system-ui, sans-serif`;
      ctx.textAlign = 'left';
      ctx.textBaseline = 'middle';
      const label = node.name.length > 22 ? node.name.slice(0, 20) + '…' : node.name;
      const textWidth = ctx.measureText(label).width;
      const padding = 3;
      const bgX = node.x + size + 3;
      const bgY = node.y - 7;
      ctx.fillStyle = darkMode ? 'rgba(10,10,15,0.75)' : 'rgba(255,255,255,0.8)';
      ctx.fillRect(bgX, bgY, textWidth + padding * 2, 14);
      ctx.fillStyle = isSelected ? '#f59e0b' : (darkMode ? '#e0e0e0' : '#333');
      ctx.fillText(label, node.x + size + 3 + padding, node.y);
    }
  }, [getNodeColor, getNodeSize, selectedNode, darkMode]);

  // ── Stats ──
  const stats = graphData?.stats;
  const relTypes = React.useMemo(() => {
    if (!graphData) return [];
    return [...new Set(graphData.links.map(l => l.rel_type).filter(Boolean))];
  }, [graphData]);

  // ── Relationship filter options ──
  useEffect(() => {
    if (!graphData) return;
    setRelTypeFilter('all');
  }, [graphData]);

  return (
    <ErrorBoundary>
      <div className="app">
        <Toaster toasts={toasts} dismiss={dismissToast} />

        {/* Header */}
        <header className="header">
          <div className="header-left">
            <h1 className="logo">Weave</h1>
            <span className="header-divider" />
            <span className="header-subtitle">Social Graph</span>
            {showStats && stats && (
              <span style={{ fontSize: 12, color: '#888', marginLeft: 8 }}>
                {stats.total_nodes} people · {stats.total_links} connections
              </span>
            )}
          </div>

          <div className="header-center">
            <div className="search-box">
              <svg className="search-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <circle cx="11" cy="11" r="8" /><path d="m21 21-4.35-4.35" />
              </svg>
              <input type="text" placeholder="Search people, orgs, locations…" value={searchQuery} onChange={e => setSearchQuery(e.target.value)} />
            </div>
            {relTypes.length > 0 && (
              <select className="filter-select" value={relTypeFilter} onChange={e => setRelTypeFilter(e.target.value)}>
                <option value="all">All relationships</option>
                {relTypes.map(t => <option key={t} value={t}>{t.replace(/_/g, ' ')}</option>)}
              </select>
            )}
            <select className="filter-select" value={groupMode} onChange={e => handleGroupModeChange(e.target.value as GroupMode)} title="Group nodes by…">
              <option value="default">Ungrouped</option>
              <option value="company">Company</option>
              <option value="education">Education / Role</option>
              <option value="city">City</option>
              <option value="tag">Tag</option>
            </select>
          </div>

          <div className="header-right">
            <div className="renderer-toggle" aria-label="Renderer">
              <button className={renderer === 'force' ? 'active' : ''} onClick={() => setRenderer('force')}>Force</button>
              <button className={renderer === 'sigma' ? 'active' : ''} onClick={() => setRenderer('sigma')}>Sigma</button>
            </div>
            {undoStack.length > 0 && (
              <button className="icon-btn" onClick={handleUndo} title={`Undo: ${undoStack[undoStack.length - 1].description} (Ctrl+Z)`}>
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M3 7v6h6" /><path d="M21 17a9 9 0 0 0-9-9 9 9 0 0 0-6 2.3L3 13" />
                </svg>
              </button>
            )}
            <button className="icon-btn" onClick={handleRefresh} title={`Snapshot: ${snapshotAge}s ago`}>
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M23 4v6h-6M1 20v-6h6" /><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15" />
              </svg>
            </button>
            <button className={`icon-btn ${editMode ? 'active' : ''}`} onClick={() => { setEditMode(!editMode); setEditPanel(null); }} title="Edit mode">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7" /><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z" />
              </svg>
            </button>
            <button className="icon-btn" onClick={() => setShowStats(!showStats)} title="Toggle stats">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M18 20V10M12 20V4M6 20v-6" />
              </svg>
            </button>
            <button className="icon-btn" onClick={() => setDarkMode(!darkMode)} title="Toggle dark mode">
              {darkMode ? (
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <circle cx="12" cy="12" r="5" /><path d="M12 1v2M12 21v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M1 12h2M21 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42" />
                </svg>
              ) : (
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" />
                </svg>
              )}
            </button>
          </div>
        </header>

        {/* Main content */}
        <div className="main">
          {/* Graph area */}
          <div className="graph-container" style={{ position: 'relative' }}>
            {loading ? (
              <div className="loading-screen">
                <div className="loading-spinner" />
                <p>Loading Weave graph…</p>
              </div>
            ) : error ? (
              <div className="loading-screen">
                <div className="error-message">
                  <h2>Unable to load graph</h2>
                  <p>{error}</p>
                  <button onClick={() => window.location.reload()}>Retry</button>
                </div>
              </div>
            ) : filteredGraphData.nodes.length === 0 ? (
              <div className="loading-screen"><p>No nodes to display</p></div>
            ) : renderer === 'force' ? (
              <ForceGraph2D
                ref={graphRef}
                graphData={filteredGraphData}
                nodeCanvasObject={paintNode}
                onNodeClick={handleNodeClick}
                nodePointerAreaPaint={(node: any, color: string, ctx: CanvasRenderingContext2D) => {
                  ctx.fillStyle = color;
                  ctx.beginPath();
                  ctx.arc(node.x, node.y, getNodeSize(node) + 3, 0, 2 * Math.PI);
                  ctx.fill();
                }}
                nodeVal={node => 1}
                nodeRelSize={1}
                linkColor={getEdgeColor}
                linkWidth={1.5}
                linkDirectionalArrowLength={4}
                linkDirectionalArrowRelPos={1}
                linkDirectionalArrowColor={getEdgeColor}
                cooldownTicks={120}
                d3AlphaDecay={0.02}
                d3VelocityDecay={0.3}
                enableNodeDrag
                onEngineStop={() => {
                  // Fit view after simulation settles
                  if (graphRef.current) {
                    const nodeCount = filteredGraphData.nodes.length;
                    const padding = Math.min(80, nodeCount * 0.5);
                    graphRef.current.zoomToFit(400, padding);
                  }
                }}
                backgroundColor={darkMode ? '#0a0a0f' : '#f8f9fa'}
              />
            ) : (
              <SigmaGraphView
                graphData={filteredGraphData}
                onNodeClick={(id) => handleNodeClick({ id })}
                darkMode={darkMode}
                getNodeColor={getNodeColor}
                getNodeSize={getNodeSize}
              />
            )}

            {/* Edit toolbar overlay */}
            {editMode && (
              <div className="edit-toolbar">
                <button className={`edit-tool-btn ${editPanel === 'person' ? 'active' : ''}`} onClick={() => setEditPanel(editPanel === 'person' ? null : 'person')}>+ Person</button>
                <button className={`edit-tool-btn ${editPanel === 'relationship' ? 'active' : ''}`} onClick={() => setEditPanel(editPanel === 'relationship' ? null : 'relationship')}>+ Relationship</button>
                <button className={`edit-tool-btn ${editPanel === 'preference' ? 'active' : ''}`} onClick={() => setEditPanel(editPanel === 'preference' ? null : 'preference')}>+ Preference</button>
                <span className="edit-toolbar-hint">Click empty space to add • Esc to close</span>
              </div>
            )}

            {/* Stats overlay */}
            {showStats && stats && (
              <div className="stats-overlay">
                { [['People', stats.total_nodes], ['Connections', graphData!.links.length], ['Preferences', stats.total_preferences], ['Facts', stats.total_facts]].map(([label, value]) => (
                  <div key={String(label)} className="stat-item">
                    <span className="stat-value">{value as number}</span>
                    <span className="stat-label">{label as string}</span>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Detail panel */}
          {selectedNode && (
            <aside className={`detail-panel ${personDetail ? 'open' : ''}`}>
              <div className="detail-header">
                <h2>{selectedNode.name}</h2>
                <div className="detail-header-actions">
                  {editMode && (
                    <>
                      <button className="icon-btn" onClick={() => setEditPanel('edit-person')} title="Edit person">
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                          <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7" /><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z" />
                        </svg>
                      </button>
                      <button className="icon-btn danger" onClick={() => setDeleteTarget({ type: 'person', label: `Delete "${selectedNode.name}" and all their connections?` })} title="Delete person">
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                          <path d="M3 6h18M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
                        </svg>
                      </button>
                    </>
                  )}
                  <button className="close-btn" onClick={() => { setSelectedNode(null); setPersonDetail(null); setEditPanel(null); }}>
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M18 6 6 18M6 6l12 12" /></svg>
                  </button>
                </div>
              </div>

              {detailLoading ? (
                <div className="detail-loading"><div className="loading-spinner small" /></div>
              ) : personDetail ? (
                <div className="detail-content">
                  {/* Person fields */}
                  <div className="detail-section">
                    {[['Email', personDetail.email], ['Phone', personDetail.phone], ['Location', personDetail.location_city], ['Organization', personDetail.org], ['Role', personDetail.occupation]].map(([label, value]) => value ? (
                      <div key={String(label)} className="detail-row">
                        <span className="detail-label">{label as string}</span>
                        <span className="detail-value">{value as string}</span>
                      </div>
                    ) : null)}
                  </div>

                  {/* Connections */}
                  {(personDetail.connections_out?.length > 0 || personDetail.connections_in?.length > 0) && (
                    <div className="detail-section">
                      <h3>Connections ({personDetail.connections_out.length + personDetail.connections_in.length})</h3>
                      {personDetail.connections_out.map((c, i) => (
                        <div key={`out-${i}`} className="connection-item">
                          <span className="connection-arrow">→</span>
                          <span className="connection-name" style={{ cursor: 'pointer', color: '#a78bfa' }} onClick={() => fetchPersonDetail(c.id)}>{c.name}</span>
                          <span className="connection-type" style={{ color: getRelColor(c.rel_type) }}>{c.rel_type.replace(/_/g, ' ')}</span>
                        </div>
                      ))}
                      {personDetail.connections_in.map((c, i) => (
                        <div key={`in-${i}`} className="connection-item">
                          <span className="connection-arrow">←</span>
                          <span className="connection-name" style={{ cursor: 'pointer', color: '#a78bfa' }} onClick={() => fetchPersonDetail(c.id)}>{c.name}</span>
                          <span className="connection-type" style={{ color: getRelColor(c.rel_type) }}>{c.rel_type.replace(/_/g, ' ')}</span>
                        </div>
                      ))}
                    </div>
                  )}

                  {/* Preferences */}
                  {personDetail.preferences?.length > 0 && (
                    <div className="detail-section">
                      <h3>Preferences ({personDetail.preferences.length})</h3>
                      {personDetail.preferences.map((p, i) => (
                        <div key={i} className="preference-item">
                          <span className="preference-value">{p.value}</span>
                          {p.category && <span className="preference-category">{p.category}</span>}
                        </div>
                      ))}
                    </div>
                  )}

                  {/* Facts */}
                  {personDetail.facts?.length > 0 && (
                    <div className="detail-section">
                      <h3>Facts ({personDetail.facts.length})</h3>
                      {personDetail.facts.slice(0, 10).map((f, i) => (
                        <div key={i} className="fact-item">
                          <span className="fact-predicate">{f.predicate}</span>
                          <span className="fact-value">{f.value}</span>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              ) : null}
            </aside>
          )}
        </div>

        {/* Edit forms */}
        {editMode && editPanel === 'person' && (
          <EditPersonForm onClose={() => setEditPanel(null)} onSubmit={async (data) => {
            await apiPost('/api/person', data);
            addToast('success', `${data.name} added`);
            setEditPanel(null);
            const res = await fetch('/api/graph');
            const gd = await res.json();
            setGraphData(gd);
            graphDataRef.current = gd;
          }} />
        )}
        {editMode && editPanel === 'relationship' && graphData && (
          <EditRelationshipForm nodes={graphData.nodes} onClose={() => setEditPanel(null)} onSubmit={async (data) => {
            await apiPost('/api/relationship', data);
            addToast('success', 'Relationship added');
            setEditPanel(null);
            const res = await fetch('/api/graph');
            const gd = await res.json();
            setGraphData(gd);
            graphDataRef.current = gd;
          }} />
        )}
        {editMode && editPanel === 'preference' && graphData && (
          <EditPreferenceForm nodes={graphData.nodes} onClose={() => setEditPanel(null)} onSubmit={async (data) => {
            await apiPost('/api/preference', data);
            addToast('success', 'Preference added');
            setEditPanel(null);
            const res = await fetch('/api/graph');
            const gd = await res.json();
            setGraphData(gd);
            graphDataRef.current = gd;
          }} />
        )}
        {deleteTarget && (
          <DeleteConfirm message={deleteTarget.label} onClose={() => setDeleteTarget(null)} onConfirm={async () => {
            if (deleteTarget.type === 'person' && selectedNode) {
              await apiPost('/api/delete-person', { id: selectedNode.id });
              addToast('success', `${selectedNode.name} deleted`);
              setDeleteTarget(null);
              setSelectedNode(null);
              setPersonDetail(null);
              const res = await fetch('/api/graph');
              const gd = await res.json();
              setGraphData(gd);
              graphDataRef.current = gd;
            }
          }} />
        )}
      </div>
    </ErrorBoundary>
  );
};

export default App;
