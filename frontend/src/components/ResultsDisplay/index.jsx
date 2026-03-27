import React, { useState, useMemo } from 'react';
import { FileText, Download, Calendar, Search, ChevronDown, ChevronUp, X, User, Hash, Globe, BookOpen } from 'lucide-react';
import { Card, Badge, Button } from '../common';
import examAPI from '../../services/api';

/**
 * ResultsDisplay Component
 * Shows extracted candidate results in the new format:
 *   { candidates: [ { candidate_name, candidate_number, answers: {...}, drawing_questions: {...}, extra_fields: {...} } ] }
 */
const ResultsDisplay = ({ results }) => {
  if (!results) return null;

  const { filename, submission_id, candidates = [], created_at, processed_at } = results;
  const [searchTerm, setSearchTerm] = useState('');
  const [selectedCandidate, setSelectedCandidate] = useState(null);
  const [showRawJSON, setShowRawJSON] = useState(false);
  const [rawJSON, setRawJSON] = useState(null);
  const [loadingJSON, setLoadingJSON] = useState(false);

  // Compute totals
  const totalAnswers = useMemo(() =>
    candidates.reduce((sum, c) => sum + Object.keys(c.answers || {}).length, 0),
    [candidates]
  );
  const totalDrawing = useMemo(() =>
    candidates.reduce((sum, c) => sum + Object.keys(c.drawing_questions || {}).length, 0),
    [candidates]
  );

  // Filter candidates by search
  const filteredCandidates = useMemo(() => {
    if (!searchTerm.trim()) return candidates;
    const q = searchTerm.toLowerCase();
    return candidates.filter(c =>
      (c.candidate_name || '').toLowerCase().includes(q) ||
      (c.candidate_number || '').toLowerCase().includes(q) ||
      (c.country || '').toLowerCase().includes(q) ||
      (c.paper_type || '').toLowerCase().includes(q) ||
      Object.values(c.extra_fields || {}).some(v => String(v).toLowerCase().includes(q))
    );
  }, [candidates, searchTerm]);

  const handleExportJSON = async () => {
    try {
      const json = await examAPI.downloadSubmissionJSON(submission_id);
      const parsed = typeof json === 'string' ? JSON.parse(json) : json;
      const dataStr = JSON.stringify(parsed, null, 2);
      const dataUri = 'data:application/json;charset=utf-8,' + encodeURIComponent(dataStr);
      const link = document.createElement('a');
      link.setAttribute('href', dataUri);
      link.setAttribute('download', `${(filename || 'results').replace('.pdf', '')}_results.json`);
      link.click();
    } catch (err) {
      console.error('Download JSON error:', err);
      alert(err?.response?.data?.detail || err?.message || 'Failed to download JSON');
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
      alert(err?.response?.data?.detail || err?.message || 'Failed to fetch JSON');
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

  const exportCandidate = (candidate, index) => {
    const dataStr = JSON.stringify(candidate, null, 2);
    const dataUri = 'data:application/json;charset=utf-8,' + encodeURIComponent(dataStr);
    const link = document.createElement('a');
    link.setAttribute('href', dataUri);
    link.setAttribute('download', `${(filename || 'results').replace('.pdf', '')}_candidate_${index + 1}.json`);
    link.click();
  };

  return (
    <div className="max-w-6xl mx-auto space-y-6">
      {/* Header Card */}
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
            <Button variant="secondary" onClick={handleExportJSON}>
              <Download className="w-4 h-4" /> Export JSON
            </Button>
            <Button variant="secondary" onClick={handleViewRawJSON}>
              <FileText className="w-4 h-4" /> {showRawJSON ? 'Hide' : 'View'} Raw JSON
            </Button>
          </div>
        </div>

        {/* Stats */}
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
        </div>
      </Card>

      {/* Raw JSON */}
      {showRawJSON && rawJSON && (
        <Card>
          <div className="flex items-center justify-between mb-2">
            <h3 className="text-lg font-bold text-slate-800">Raw JSON</h3>
            <button onClick={() => setShowRawJSON(false)} className="text-slate-400 hover:text-slate-600">
              <X className="w-5 h-5" />
            </button>
          </div>
          <pre className="text-xs bg-slate-50 p-4 rounded-lg max-h-96 overflow-auto whitespace-pre-wrap border border-slate-200">
            {JSON.stringify(rawJSON, null, 2)}
          </pre>
        </Card>
      )}

      {/* Search */}
      {candidates.length > 0 && (
        <div className="relative">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-5 h-5 text-slate-400" />
          <input
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

      {/* Candidate Grid */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
        {filteredCandidates.map((candidate, index) => {
          const answerCount = Object.keys(candidate.answers || {}).length;
          const drawingCount = Object.keys(candidate.drawing_questions || {}).length;
          const displayName = candidate.candidate_name || 'Unknown';
          const displayNumber = candidate.candidate_number || '';
          const extra = candidate.extra_fields || {};

          return (
            <div
              key={`${index}-${displayNumber}`}
              onClick={() => setSelectedCandidate({ ...candidate, _index: index })}
              className="bg-white p-4 rounded-xl shadow-sm border border-slate-200 hover:shadow-md hover:border-blue-300 transition-all cursor-pointer"
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

              {/* Extra fields */}
              {Object.keys(extra).length > 0 && (
                <div className="flex flex-wrap gap-1 mb-2">
                  {Object.entries(extra).map(([k, v]) => (
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
            </div>
          );
        })}
      </div>

      {filteredCandidates.length === 0 && candidates.length > 0 && (
        <Card className="text-center py-8">
          <p className="text-slate-500">No candidates match your search.</p>
        </Card>
      )}

      {/* Candidate Detail Modal */}
      {selectedCandidate && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4" onClick={() => setSelectedCandidate(null)}>
          <div
            className="bg-white rounded-2xl shadow-2xl max-w-3xl w-full max-h-[90vh] overflow-hidden flex flex-col"
            onClick={(e) => e.stopPropagation()}
          >
            {/* Modal Header */}
            <div className="flex items-center justify-between p-6 border-b">
              <div>
                <h3 className="text-xl font-bold text-slate-800">
                  {selectedCandidate.candidate_name || 'Unknown'}
                </h3>
                <div className="flex items-center gap-3 text-sm text-slate-500 mt-1">
                  {selectedCandidate.candidate_number && (
                    <span className="flex items-center gap-1"><Hash className="w-3 h-3" /> {selectedCandidate.candidate_number}</span>
                  )}
                  {selectedCandidate.country && (
                    <span className="flex items-center gap-1"><Globe className="w-3 h-3" /> {selectedCandidate.country}</span>
                  )}
                  {selectedCandidate.paper_type && (
                    <span className="flex items-center gap-1"><BookOpen className="w-3 h-3" /> {selectedCandidate.paper_type}</span>
                  )}
                </div>
                {/* Extra fields */}
                {selectedCandidate.extra_fields && Object.keys(selectedCandidate.extra_fields).length > 0 && (
                  <div className="flex flex-wrap gap-1.5 mt-2">
                    {Object.entries(selectedCandidate.extra_fields).map(([k, v]) => (
                      <span key={k} className="text-xs bg-slate-100 text-slate-600 px-2 py-0.5 rounded">
                        {k}: {v}
                      </span>
                    ))}
                  </div>
                )}
              </div>
              <div className="flex items-center gap-2">
                <button
                  onClick={() => exportCandidate(selectedCandidate, selectedCandidate._index)}
                  className="text-sm text-blue-600 hover:text-blue-800"
                >
                  <Download className="w-4 h-4" />
                </button>
                <button onClick={() => setSelectedCandidate(null)} className="text-slate-400 hover:text-slate-600">
                  <X className="w-6 h-6" />
                </button>
              </div>
            </div>

            {/* Modal Body */}
            <div className="p-6 overflow-auto flex-1">
              {/* Answers Table */}
              {Object.keys(selectedCandidate.answers || {}).length > 0 && (
                <div className="mb-6">
                  <h4 className="font-semibold text-slate-700 mb-3">
                    Answers ({Object.keys(selectedCandidate.answers).length})
                  </h4>
                  <div className="grid grid-cols-5 sm:grid-cols-8 md:grid-cols-10 gap-2">
                    {Object.entries(selectedCandidate.answers)
                      .sort(([a], [b]) => {
                        const na = parseInt(a), nb = parseInt(b);
                        if (!isNaN(na) && !isNaN(nb)) return na - nb;
                        return a.localeCompare(b);
                      })
                      .map(([q, a]) => (
                        <div key={q} className="bg-slate-50 rounded-lg p-2 text-center border border-slate-200">
                          <p className="text-[10px] text-slate-500 mb-0.5">Q{q}</p>
                          <p className="font-bold text-slate-800 text-sm">{a || '\u2014'}</p>
                        </div>
                      ))
                    }
                  </div>
                </div>
              )}

              {/* Drawing Questions */}
              {Object.keys(selectedCandidate.drawing_questions || {}).length > 0 && (
                <div>
                  <h4 className="font-semibold text-slate-700 mb-3">
                    Drawing / Free Response ({Object.keys(selectedCandidate.drawing_questions).length})
                  </h4>
                  <div className="space-y-3">
                    {Object.entries(selectedCandidate.drawing_questions)
                      .sort(([a], [b]) => {
                        const na = parseInt(a), nb = parseInt(b);
                        if (!isNaN(na) && !isNaN(nb)) return na - nb;
                        return a.localeCompare(b);
                      })
                      .map(([q, a]) => (
                        <div key={q} className="bg-slate-50 rounded-lg p-3 border border-slate-200">
                          <p className="text-xs font-semibold text-slate-500 mb-1">Question {q}</p>
                          <p className="text-sm text-slate-800 whitespace-pre-wrap">{a || '\u2014'}</p>
                        </div>
                      ))
                    }
                  </div>
                </div>
              )}

              {Object.keys(selectedCandidate.answers || {}).length === 0 &&
               Object.keys(selectedCandidate.drawing_questions || {}).length === 0 && (
                <p className="text-slate-500 text-center py-8">No answers extracted for this candidate.</p>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default ResultsDisplay;
