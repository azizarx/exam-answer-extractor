import { useCallback, useMemo, useState } from 'react';
import {
  Calendar,
  Download,
  FileCheck2,
  FileText,
  Search,
  X,
} from 'lucide-react';
import { Card, Badge, Button } from '../common';
import examAPI from '../../services/api';
import CandidateDetailModal from './CandidateDetailModal';
import MarkingStatusBanner from './MarkingStatusBanner';

const safeFilenameStem = (filename) => {
  const leaf = String(filename || 'results').replace(/\\/g, '/').split('/').pop();
  return leaf.replace(/\.pdf$/i, '').replace(/[^\w.-]+/g, '_') || 'results';
};

const triggerBlobDownload = (blob, filename) => {
  const objectUrl = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = objectUrl;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(objectUrl), 0);
};

const displayMarks = (value) => {
  if (value === null || value === undefined || value === '') return '—';
  const number = Number(value);
  return Number.isFinite(number) ? number.toLocaleString() : '—';
};

const FORBIDDEN_DISPLAY_KEYS = new Set([
  'accepted_answers',
  'question_spec',
  'correct_answer',
  'correct_answers',
  'expected_answer',
  'answer_key',
]);

const safeExtraEntries = (extraFields) =>
  Object.entries(extraFields || {}).filter(
    ([key]) => !FORBIDDEN_DISPLAY_KEYS.has(String(key).toLowerCase())
  );

const ResultsDisplay = ({
  results,
  onMark,
  markingRequest = { loading: false, error: '' },
  onRefresh,
}) => {
  const [searchTerm, setSearchTerm] = useState('');
  const [selectedCandidate, setSelectedCandidate] = useState(null);
  const [showRawJSON, setShowRawJSON] = useState(false);
  const [rawJSON, setRawJSON] = useState(null);
  const [loadingJSON, setLoadingJSON] = useState(false);
  const [downloadState, setDownloadState] = useState({ raw: false, marked: false, error: '' });

  const {
    filename,
    submission_id,
    candidates = [],
    processed_at,
    latest_marking: latestMarking,
  } = results || {};

  const totalAnswers = useMemo(() =>
    candidates.reduce((sum, c) => sum + Object.keys(c.answers || {}).length, 0),
    [candidates]
  );
  const totalDrawing = useMemo(() =>
    candidates.reduce((sum, c) => sum + Object.keys(c.drawing_questions || {}).length, 0),
    [candidates]
  );
  const reviewCount = useMemo(
    () =>
      candidates.reduce(
        (sum, c) => sum + (c.extra_fields?.needs_review_questions || []).length,
        0
      ),
    [candidates]
  );

  const filteredCandidates = useMemo(() => {
    if (!searchTerm.trim()) return candidates;
    const q = searchTerm.toLowerCase();
    return candidates.filter(c =>
      (c.candidate_name || '').toLowerCase().includes(q) ||
      (c.candidate_number || '').toLowerCase().includes(q) ||
      (c.country || '').toLowerCase().includes(q) ||
      (c.paper_type || '').toLowerCase().includes(q) ||
      (c.template_id || '').toLowerCase().includes(q) ||
      Object.values(c.extra_fields || {}).some(v => String(v).toLowerCase().includes(q))
    );
  }, [candidates, searchTerm]);

  const handleExportJSON = async () => {
    setDownloadState((state) => ({ ...state, raw: true, error: '' }));
    try {
      const json = await examAPI.downloadSubmissionJSON(submission_id);
      const parsed = typeof json === 'string' ? JSON.parse(json) : json;
      const blob = new Blob([JSON.stringify(parsed, null, 2)], {
        type: 'application/json;charset=utf-8',
      });
      triggerBlobDownload(blob, `${safeFilenameStem(filename)}_results.json`);
    } catch (err) {
      console.error('Download JSON error:', err);
      setDownloadState((state) => ({
        ...state,
        error: err?.response?.data?.detail || err?.message || 'Failed to download raw JSON.',
      }));
    } finally {
      setDownloadState((state) => ({ ...state, raw: false }));
    }
  };

  const handleExportMarkedJSON = async () => {
    setDownloadState((state) => ({ ...state, marked: true, error: '' }));
    try {
      const fallbackName = `${safeFilenameStem(filename)}.marked.json`;
      const download = await examAPI.getMarkedJSONDownload(submission_id, fallbackName);
      triggerBlobDownload(download.blob, download.filename);
    } catch (err) {
      console.error('Download marked JSON error:', err);
      let detail = err?.response?.data?.detail;
      if (err?.response?.data instanceof Blob) {
        try {
          const payload = JSON.parse(await err.response.data.text());
          detail = payload?.detail;
        } catch {
          detail = '';
        }
      }
      setDownloadState((state) => ({
        ...state,
        error: typeof detail === 'string' ? detail : 'Failed to download marked JSON.',
      }));
    } finally {
      setDownloadState((state) => ({ ...state, marked: false }));
    }
  };

  const handleViewRawJSON = async () => {
    if (rawJSON) {
      setShowRawJSON(!showRawJSON);
      return;
    }
    setLoadingJSON(true);
    try {
      const json = await examAPI.downloadSubmissionJSON(submission_id);
      let parsed = typeof json === 'string' ? JSON.parse(json) : json;
      if (parsed?.raw && typeof parsed.raw === 'string') {
        try { parsed = JSON.parse(parsed.raw); } catch {}
      }
      setRawJSON(parsed);
      setShowRawJSON(true);
    } catch (err) {
      console.error('Fetch JSON error:', err);
      setDownloadState((state) => ({
        ...state,
        error: err?.response?.data?.detail || err?.message || 'Failed to fetch raw JSON.',
      }));
    } finally {
      setLoadingJSON(false);
    }
  };

  const formatDate = (dateString) => {
    if (!dateString) return '\u2014';
    return new Date(dateString).toLocaleString('en-US', {
      month: 'short', day: 'numeric', year: 'numeric',
      hour: '2-digit', minute: '2-digit',
    });
  };

  const exportCandidate = useCallback((candidate, index) => {
    const exportableCandidate = { ...candidate };
    delete exportableCandidate._index;
    delete exportableCandidate.marking;
    if (exportableCandidate.extra_fields) {
      exportableCandidate.extra_fields = Object.fromEntries(
        safeExtraEntries(exportableCandidate.extra_fields)
      );
    }
    const blob = new Blob([JSON.stringify(exportableCandidate, null, 2)], {
      type: 'application/json;charset=utf-8',
    });
    triggerBlobDownload(
      blob,
      `${safeFilenameStem(filename)}_candidate_${index + 1}.json`
    );
  }, [filename]);

  const closeCandidate = useCallback(() => setSelectedCandidate(null), []);

  if (!results) return null;

  return (
    <div className="max-w-6xl mx-auto space-y-6">
      <Card>
        <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
          <div className="flex-1">
            <h2 className="text-2xl font-bold text-slate-800 mb-1">Extraction Results</h2>
            <p className="text-slate-600 text-sm">{filename}</p>
            {processed_at && (
              <div className="flex items-center gap-2 text-xs text-slate-500 mt-1">
                <Calendar className="w-3 h-3" />
                <span>Processed: {formatDate(processed_at)}</span>
              </div>
            )}
          </div>
          <div className="flex flex-col sm:flex-row gap-2">
            <Button
              type="button"
              variant="secondary"
              onClick={handleExportJSON}
              loading={downloadState.raw}
              disabled={downloadState.raw}
            >
              {!downloadState.raw && <Download className="w-4 h-4" aria-hidden="true" />}
              Raw JSON
            </Button>
            {latestMarking?.status === 'completed' && (
              <Button
                type="button"
                variant="primary"
                onClick={handleExportMarkedJSON}
                loading={downloadState.marked}
                disabled={downloadState.marked}
              >
                {!downloadState.marked && <FileCheck2 className="w-4 h-4" aria-hidden="true" />}
                Marked JSON
              </Button>
            )}
            <Button
              type="button"
              variant="ghost"
              onClick={handleViewRawJSON}
              loading={loadingJSON}
              disabled={loadingJSON}
            >
              {!loadingJSON && <FileText className="w-4 h-4" aria-hidden="true" />}
              {showRawJSON ? 'Hide' : 'View'} raw
            </Button>
          </div>
        </div>

        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mt-6">
          <div className="bg-blue-50 rounded-lg p-4">
            <p className="text-xs text-blue-600 mb-1">Candidates</p>
            <p className="text-3xl font-bold text-blue-700">{candidates.length}</p>
          </div>
          <div className="bg-green-50 rounded-lg p-4">
            <p className="text-xs text-green-600 mb-1">Total Answers</p>
            <p className="text-3xl font-bold text-green-700">{totalAnswers}</p>
          </div>
          <div className="bg-purple-50 rounded-lg p-4">
            <p className="text-xs text-purple-600 mb-1">Drawing / FR</p>
            <p className="text-3xl font-bold text-purple-700">{totalDrawing}</p>
          </div>
          <div className="bg-amber-50 rounded-lg p-4">
            <p className="text-xs text-amber-600 mb-1">Avg Answers</p>
            <p className="text-3xl font-bold text-amber-700">
              {candidates.length > 0 ? Math.round(totalAnswers / candidates.length) : 0}
            </p>
          </div>
          <div className="bg-orange-50 rounded-lg p-4">
            <p className="text-xs text-orange-600 mb-1">Needs review (Q)</p>
            <p className="text-3xl font-bold text-orange-700">{reviewCount}</p>
          </div>
        </div>
      </Card>

      <MarkingStatusBanner
        latestMarking={latestMarking}
        onMark={onMark}
        markingRequest={markingRequest}
      />

      {downloadState.error && (
        <div
          className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800"
          role="alert"
        >
          {String(downloadState.error).slice(0, 240)}
        </div>
      )}

      {showRawJSON && rawJSON && (
        <Card>
          <div className="flex items-center justify-between mb-2">
            <h3 className="text-lg font-bold text-slate-800">Raw JSON</h3>
            <button
              type="button"
              onClick={() => setShowRawJSON(false)}
              className="rounded p-1 text-slate-400 hover:text-slate-600 focus:outline-none focus:ring-2 focus:ring-blue-500"
              aria-label="Close raw JSON viewer"
            >
              <X className="w-5 h-5" aria-hidden="true" />
            </button>
          </div>
          <pre className="text-xs bg-slate-50 p-4 rounded-lg max-h-96 overflow-auto whitespace-pre-wrap border border-slate-200">
            {JSON.stringify(rawJSON, null, 2)}
          </pre>
        </Card>
      )}

      {candidates.length > 0 && (
        <div className="relative">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-5 h-5 text-slate-400" aria-hidden="true" />
          <label htmlFor="candidate-search" className="sr-only">Search candidates</label>
          <input
            id="candidate-search"
            type="text"
            placeholder="Search candidates by name, number, country..."
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            className="w-full pl-10 pr-4 py-3 rounded-xl border border-slate-300 focus:ring-2 focus:ring-blue-500 focus:border-blue-500 text-sm"
          />
          {searchTerm && (
            <span className="absolute right-3 top-1/2 -translate-y-1/2 text-xs text-slate-400">
              {filteredCandidates.length} of {candidates.length}
            </span>
          )}
        </div>
      )}

      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
        {filteredCandidates.map((candidate, index) => {
          const answerCount = Object.keys(candidate.answers || {}).length;
          const drawingCount = Object.keys(candidate.drawing_questions || {}).length;
          const displayName = candidate.candidate_name || 'Unknown';
          const displayNumber = candidate.candidate_number || '';
          const extraEntries = safeExtraEntries(candidate.extra_fields);
          const candidateMarking = candidate.marking;
          const hasValidPercentage =
            Number(candidateMarking?.max_marks) > 0 &&
            Number.isFinite(Number(candidateMarking?.percentage));

          return (
            <button
              type="button"
              key={candidate.id ?? `${index}-${displayNumber}`}
              onClick={() => setSelectedCandidate({ candidate, index })}
              className="w-full rounded-xl border border-slate-200 bg-white p-4 text-left shadow-sm transition-all hover:border-blue-300 hover:shadow-md focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2"
              aria-label={`View details for ${displayName}${candidateMarking ? `, score ${displayMarks(candidateMarking.awarded_marks)} of ${displayMarks(candidateMarking.max_marks)}` : ', not marked'}`}
            >
              <div className="flex items-start justify-between mb-2">
                <div className="flex-1 min-w-0">
                  <p className="font-semibold text-slate-800 truncate">{displayName}</p>
                  {displayNumber && (
                    <p className="text-xs text-slate-500 font-mono">{displayNumber}</p>
                  )}
                </div>
                <Badge variant="info" className="ml-2 flex-shrink-0">
                  #{index + 1}
                </Badge>
              </div>

              {candidateMarking ? (
                <div className="mb-3 grid grid-cols-[1fr_auto] items-end gap-3 rounded-lg border border-slate-200 bg-slate-50 px-3 py-2.5">
                  <div>
                    <p className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">Weighted score</p>
                    <p className="mt-0.5 text-lg font-bold text-slate-900">
                      {displayMarks(candidateMarking.awarded_marks)}
                      <span className="text-sm font-medium text-slate-500">
                        {' '}/ {displayMarks(candidateMarking.max_marks)}
                      </span>
                    </p>
                  </div>
                  <p className="text-lg font-bold text-slate-800">
                    {hasValidPercentage ? `${Number(candidateMarking.percentage).toFixed(1)}%` : '—'}
                  </p>
                </div>
              ) : (
                <div className="mb-3 rounded-lg border border-dashed border-slate-300 px-3 py-2 text-xs font-medium text-slate-600">
                  {candidate.template_id
                    ? `No answer key for ${candidate.template_id}`
                    : 'No candidate marking available'}
                </div>
              )}

              {candidate.template_id && (
                <p className="mb-2 text-[11px] font-mono text-slate-500 truncate" title={candidate.template_id}>
                  Layout: {candidate.template_id}
                </p>
              )}
              {candidate.detection?.warning && (
                <p className="mb-2 text-[11px] text-amber-700 truncate" title={candidate.detection.warning}>
                  Detect: {candidate.detection.warning}
                </p>
              )}

              {extraEntries.length > 0 && (
                <div className="flex flex-wrap gap-1 mb-2">
                  {extraEntries.map(([k, v]) => (
                    <span key={k} className="text-[10px] bg-slate-100 text-slate-600 px-1.5 py-0.5 rounded">
                      {k}: {v}
                    </span>
                  ))}
                </div>
              )}

              <div className="flex items-center gap-3 text-xs text-slate-500 mt-2">
                <span>{answerCount} answers</span>
                {drawingCount > 0 && <span>{drawingCount} drawing</span>}
                {candidate.country && <span>{candidate.country}</span>}
              </div>
            </button>
          );
        })}
      </div>

      {filteredCandidates.length === 0 && candidates.length > 0 && (
        <Card className="text-center py-8">
          <p className="text-slate-500">No candidates match your search.</p>
        </Card>
      )}

      {selectedCandidate && (
        <CandidateDetailModal
          selection={selectedCandidate}
          onClose={closeCandidate}
          onExport={exportCandidate}
          submissionId={submission_id}
          onReviewConfirmed={() => {
            closeCandidate();
            onRefresh?.();
          }}
        />
      )}
    </div>
  );
};

export default ResultsDisplay;
