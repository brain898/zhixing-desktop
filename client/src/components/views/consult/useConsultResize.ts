import { CSSProperties, KeyboardEvent, PointerEvent, useLayoutEffect, useRef, useState } from 'react';

const DEFAULT_SHARE = 0.9 / 2.2;
const HANDLE_WIDTH = 10;

/** Keep the history column fixed while dividing the remaining space between the two panes. */
export function useConsultResize(active: boolean) {
  const containerRef = useRef<HTMLDivElement>(null);
  const drag = useRef<{ pointerId: number; x: number; width: number } | null>(null);
  const [available, setAvailable] = useState(0);
  const [share, setShare] = useState(DEFAULT_SHARE);
  const [dragging, setDragging] = useState(false);
  const minimum = Math.min(280, available / 2);
  const maximum = available - Math.min(340, available / 2);
  const clamp = (width: number) => Math.max(minimum, Math.min(maximum, width));
  const width = clamp(available * share);
  const setWidth = (next: number) => { if (available > 0) setShare(clamp(next) / available); };

  useLayoutEffect(() => {
    const container = containerRef.current;
    if (!active || !container) return;
    const history = container.firstElementChild;
    const measure = () => setAvailable(Math.max(0,
      container.clientWidth - (history?.getBoundingClientRect().width || 0) - HANDLE_WIDTH));
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(container);
    if (history) observer.observe(history);
    return () => { observer.disconnect(); drag.current = null; setDragging(false); };
  }, [active]);

  const stop = () => { drag.current = null; setDragging(false); };
  const onPointerDown = (event: PointerEvent<HTMLDivElement>) => {
    if (event.button !== 0 || !event.isPrimary || !available) return;
    event.preventDefault();
    event.currentTarget.focus();
    event.currentTarget.setPointerCapture(event.pointerId);
    drag.current = { pointerId: event.pointerId, x: event.clientX, width };
    setDragging(true);
  };
  const onPointerMove = (event: PointerEvent<HTMLDivElement>) => {
    if (drag.current?.pointerId === event.pointerId) {
      setWidth(drag.current.width + event.clientX - drag.current.x);
    }
  };
  const onPointerUp = (event: PointerEvent<HTMLDivElement>) => {
    if (drag.current?.pointerId !== event.pointerId) return;
    stop();
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
  };
  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const next = { ArrowLeft: width - 16, ArrowRight: width + 16, Home: minimum, End: maximum }[event.key];
    if (next !== undefined) { event.preventDefault(); setWidth(next); }
  };
  const percent = (value: number) => available ? Math.round(value / available * 100) : 50;

  return {
    containerRef,
    dragging,
    style: (available ? { '--consult-dialog-width': `${width}px` } : {}) as CSSProperties,
    separatorProps: {
      role: 'separator', tabIndex: 0,
      'aria-label': '调整咨询与报告宽度', 'aria-orientation': 'vertical' as const,
      'aria-controls': 'consult-dialog consult-report-pane',
      'aria-valuemin': percent(minimum), 'aria-valuemax': percent(maximum), 'aria-valuenow': percent(width),
      title: '拖动调整宽度，双击恢复；也可使用左右方向键',
      onPointerDown, onPointerMove, onPointerUp, onPointerCancel: onPointerUp,
      onLostPointerCapture: stop, onKeyDown, onDoubleClick: () => setShare(DEFAULT_SHARE),
    },
  };
}
