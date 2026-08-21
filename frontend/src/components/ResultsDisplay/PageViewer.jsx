import { useEffect, useRef, useState } from 'react';
import { Loader2, Minus, Plus, RotateCcw } from 'lucide-react';
import examAPI from '../../services/api';

const MIN_ZOOM = 0.5;
const MAX_ZOOM = 6;
const ZOOM_STEP = 0.25;

const clampZoom = (value) => Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, value));

/**
 * The candidate's scanned page, with zoom and pan.
 *
 * The page is re-rendered on demand from the stored PDF — extraction deletes
 * its page images — so this owns one fetch per page and revokes the object URL
 * when it unmounts or the page changes.
 */
const PageViewer = ({ submissionId, pageNumber }) => {
  const [imageURL, setImageURL] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [zoom, setZoom] = useState(1);
  const [offset, setOffset] = useState({ x: 0, y: 0 });
  const dragRef = useRef(null);
  const surfaceRef = useRef(null);

  useEffect(() => {
    let revoked = false;
    let url = '';
    setLoading(true);
    setError('');
    setZoom(1);
    setOffset({ x: 0, y: 0 });

    examAPI
      .getPageImageURL(submissionId, pageNumber)
      .then((objectURL) => {
        if (revoked) {
          URL.revokeObjectURL(objectURL);
          return;
        }
        url = objectURL;
        setImageURL(objectURL);
      })
      .catch((err) => {
        const status = err?.response?.status;
        setError(
          status === 410
            ? 'The uploaded PDF for this submission is no longer stored.'
            : status === 404
              ? 'That page is not available for this submission.'
              : 'Could not load the exam paper.',
        );
      })
      .finally(() => {
        if (!revoked) setLoading(false);
      });

    return () => {
      revoked = true;
      if (url) URL.revokeObjectURL(url);
      setImageURL('');
    };
  }, [submissionId, pageNumber]);

  // React registers `wheel` passively, so an onWheel handler cannot
  // preventDefault — the zoom would apply AND the surrounding modal would
  // scroll, sliding the image the reviewer is zooming into out of view.
  // A non-passive listener has to be attached directly.
  useEffect(() => {
    const surface = surfaceRef.current;
    if (!surface) return undefined;
    const onWheel = (event) => {
      event.preventDefault();
      setZoom((current) => clampZoom(current + (event.deltaY < 0 ? ZOOM_STEP : -ZOOM_STEP)));
    };
    surface.addEventListener('wheel', onWheel, { passive: false });
    return () => surface.removeEventListener('wheel', onWheel);
  }, []);

  const handlePointerDown = (event) => {
    dragRef.current = {
      startX: event.clientX,
      startY: event.clientY,
      originX: offset.x,
      originY: offset.y,
    };
    event.currentTarget.setPointerCapture?.(event.pointerId);
  };

  const handlePointerMove = (event) => {
    const drag = dragRef.current;
    if (!drag) return;
    setOffset({
      x: drag.originX + (event.clientX - drag.startX),
      y: drag.originY + (event.clientY - drag.startY),
    });
  };

  const endDrag = (event) => {
    dragRef.current = null;
    event.currentTarget.releasePointerCapture?.(event.pointerId);
  };

  const reset = () => {
    setZoom(1);
    setOffset({ x: 0, y: 0 });
  };

  if (error) {
    return (
      <div className="rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">
        {error}
      </div>
    );
  }

  return (
    <div className="overflow-hidden rounded-xl border border-slate-200">
      <div className="flex items-center justify-between gap-2 border-b border-slate-200 bg-slate-50 px-3 py-2">
        <p className="text-xs font-semibold uppercase tracking-wide text-slate-600">
          Page {pageNumber}
        </p>
        <div className="flex items-center gap-1">
          <button
            type="button"
            onClick={() => setZoom((z) => clampZoom(z - ZOOM_STEP))}
            className="rounded p-1.5 text-slate-600 hover:bg-slate-200 focus:outline-none focus:ring-2 focus:ring-blue-500"
            aria-label="Zoom out"
          >
            <Minus className="h-4 w-4" aria-hidden="true" />
          </button>
          <span className="w-12 text-center text-xs font-medium text-slate-600">
            {Math.round(zoom * 100)}%
          </span>
          <button
            type="button"
            onClick={() => setZoom((z) => clampZoom(z + ZOOM_STEP))}
            className="rounded p-1.5 text-slate-600 hover:bg-slate-200 focus:outline-none focus:ring-2 focus:ring-blue-500"
            aria-label="Zoom in"
          >
            <Plus className="h-4 w-4" aria-hidden="true" />
          </button>
          <button
            type="button"
            onClick={reset}
            className="rounded p-1.5 text-slate-600 hover:bg-slate-200 focus:outline-none focus:ring-2 focus:ring-blue-500"
            aria-label="Reset zoom and position"
          >
            <RotateCcw className="h-4 w-4" aria-hidden="true" />
          </button>
        </div>
      </div>

      <div
        ref={surfaceRef}
        className="relative h-[60vh] touch-none overflow-hidden bg-slate-100"
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={endDrag}
        onPointerCancel={endDrag}
        style={{ cursor: dragRef.current ? 'grabbing' : 'grab' }}
      >
        {loading ? (
          <div className="flex h-full items-center justify-center text-slate-500">
            <Loader2 className="h-6 w-6 animate-spin" aria-hidden="true" />
            <span className="ml-2 text-sm">Rendering page…</span>
          </div>
        ) : (
          imageURL && (
            <img
              src={imageURL}
              alt={`Scanned exam paper, page ${pageNumber}`}
              draggable={false}
              className="mx-auto max-w-none select-none"
              style={{
                transform: `translate(${offset.x}px, ${offset.y}px) scale(${zoom})`,
                transformOrigin: 'top center',
                width: '100%',
              }}
            />
          )
        )}
      </div>
    </div>
  );
};

export default PageViewer;
