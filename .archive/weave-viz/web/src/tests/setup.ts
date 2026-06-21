import '@testing-library/jest-dom'
import { expect, describe, it, vi, beforeEach } from 'vitest'

// Mock canvas and 2d context
const mockContext = {
  save: vi.fn(),
  restore: vi.fn(),
  setTransform: vi.fn(),
  clearRect: vi.fn(),
  translate: vi.fn(),
  scale: vi.fn(),
  beginPath: vi.fn(),
  moveTo: vi.fn(),
  lineTo: vi.fn(),
  stroke: vi.fn(),
  arc: vi.fn(),
  fill: vi.fn(),
  fillText: vi.fn(),
  strokeRect: vi.fn(),
  fillRect: vi.fn(),
  measureText: vi.fn(() => ({ width: 0 })),
  font: '',
  fillStyle: '',
  strokeStyle: '',
  lineWidth: '',
  globalAlpha: 1,
  textAlign: 'start',
} as unknown as CanvasRenderingContext2D

HTMLCanvasElement.prototype.getContext = vi.fn(() => mockContext) as any
HTMLCanvasElement.prototype.getBoundingClientRect = vi.fn(() => ({
  width: 800,
  height: 600,
  top: 0,
  left: 0,
  right: 800,
  bottom: 600,
  x: 0,
  y: 0,
  toJSON: () => {},
}))
