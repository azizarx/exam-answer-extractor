import React, { useState } from 'react';
import { FileText, Download, Calendar } from 'lucide-react';
import { Card, Badge, Button } from '../common';
import examAPI from '../../services/api';

/**
 * ResultsDisplay Component
 * Shows extracted MCQ and free response answers
 */
const ResultsDisplay = ({ results }) => {
  if (!results) return null;

  const { filename, submission_id, multiple_choice = [], free_response = [], created_at, processed_at } = results;
  const [structuredJSON, setStructuredJSON] = useState(null);
  const [showCombined, setShowCombined] = useState(false);
  const [loadingJSON, setLoadingJSON] = useState(false);
  const [jsonError, setJsonError] = useState(null);

  const handleExportJSON = async () => {
    try {
      // Use the new endpoint to get the real structured JSON
      const json = await examAPI.downloadSubmissionJSON(submission_id);
      console.debug('Downloaded JSON:', json);
      const parsed = typeof json === 'string' ? JSON.parse(json) : json;
      const dataStr = JSON.stringify(parsed, null, 2);
      const dataUri = 'data:application/json;charset=utf-8,' + encodeURIComponent(dataStr);
      const exportFileDefaultName = `${filename.replace('.pdf', '')}_results.json`;
      const linkElement = document.createElement('a');
      linkElement.setAttribute('href', dataUri);
      linkElement.setAttribute('download', exportFileDefaultName);
      linkElement.click();
    } catch (err) {
      const msg = err?.response?.data?.detail || err?.response?.data || err?.message || 'Failed to download structured JSON.';
      console.error('Download JSON error:', err);
      alert(msg);
    }
  };

  const formatDate = (dateString) => {
    return new Date(dateString).toLocaleString('en-US', {
      month: 'short',
      day: 'numeric',
      year: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    });
  };

  return (
    <div className="max-w-6xl mx-auto space-y-6">
      {/* Header Card */}
      <Card>
        <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
          <div className="flex-1">
            <h2 className="text-2xl font-bold text-slate-800 mb-2">Extraction Results</h2>
            <p className="text-slate-600">{filename}</p>
            {processed_at && (
              <div className="flex items-center gap-2 text-sm text-slate-500 mt-2">
                <Calendar className="w-4 h-4" />
                <span>Processed: {formatDate(processed_at)}</span>
              </div>
            )}
          </div>
          
          <div className="flex flex-col sm:flex-row gap-3">
            <Button variant="secondary" onClick={handleExportJSON}>
              <Download className="w-5 h-5" />
              Export JSON
            </Button>
            <Button variant="secondary" onClick={async () => {
              setLoadingJSON(true);
              setJsonError(null);
              try {
                const json = await examAPI.downloadSubmissionJSON(submission_id);
                console.log('Downloaded structured JSON:', json);
                let parsed = json;
                if (typeof json === 'string') {
                  // If server returns a raw JSON string, try to parse it
                  try {
                    parsed = JSON.parse(json);
                  } catch (parseErr) {
                    console.warn('downloadSubmissionJSON returned string, but parsing failed', parseErr);
                    parsed = { raw: json };
                  }
                }

                // If server returned wrapper { raw: '...' }, attempt to parse the raw
                if (parsed && parsed.raw && typeof parsed.raw === 'string') {
                  try {
                    parsed = JSON.parse(parsed.raw);
                  } catch (parseErr) {
                    console.warn('Server returned raw JSON string that could not be parsed', parseErr);
                  }
                }

                // set structured JSON to state
                setStructuredJSON(parsed);
                if (!parsed || !Array.isArray(parsed.submissions)) {
                  setJsonError('No per-submission data (submissions) found in structured JSON');
                }
              } catch (err) {
                console.error('Failed to fetch structured JSON', err);
                setJsonError(err?.response?.data?.detail || err?.message || 'Failed to fetch structured JSON');
              } finally {
                setLoadingJSON(false);
              }
            }}>
              View Candidates
            </Button>
          </div>
        </div>

        <div className="grid grid-cols-2 md:grid-cols-3 gap-4 mt-6">
          <div className="bg-blue-50 rounded-lg p-4">
            <p className="text-sm text-blue-600 mb-1">Total Answers</p>
            <p className="text-3xl font-bold text-blue-700">
              {multiple_choice.length + free_response.length}
            </p>
          </div>
          <div className="bg-green-50 rounded-lg p-4">
            <p className="text-sm text-green-600 mb-1">Multiple Choice</p>
            <p className="text-3xl font-bold text-green-700">{multiple_choice.length}</p>
          </div>
          <div className="bg-purple-50 rounded-lg p-4">
            <p className="text-sm text-purple-600 mb-1">Free Response</p>
            <p className="text-3xl font-bold text-purple-700">{free_response.length}</p>
          </div>
        </div>
      </Card>

      {/* Per-candidate structured view */}
      {loadingJSON && (
        <Card className="mb-4">
          <div className="text-center text-slate-600">Fetching structured JSON...</div>
        </Card>
      )}

      {jsonError && (
        <Card className="mb-4 border-red-100 bg-red-50">
          <div className="text-red-700">{jsonError}</div>
        </Card>
      )}

  {structuredJSON && structuredJSON.submissions && (
        <Card>
          <h3 className="text-lg font-bold mb-4">Candidates ({structuredJSON.submissions.length})</h3>
          <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-4">
            {structuredJSON.submissions.map((sub) => (
              <div key={`${sub.page_number}-${sub.candidate_information.candidate_number || 'unknown'}`} className="bg-white p-4 rounded-lg shadow hover:shadow-md transition-shadow cursor-pointer" onClick={() => setStructuredJSON({ ...structuredJSON, _selected: sub })}>
                <div className="flex items-start justify-between gap-4">
                  <div>
                    <p className="text-xs text-slate-500">Page {sub.page_number}</p>
                    <h4 className="text-md font-semibold text-slate-800">{sub.candidate_information.candidate_name || 'Unknown'}</h4>
                    <p className="text-xs text-slate-500 mt-1">ID: {sub.candidate_information.candidate_number || 'N/A'}</p>
                    <p className="text-xs text-slate-500">{sub.candidate_information.country || '—'} · Level: {sub.candidate_information.level || '—'}</p>
                  </div>
                  <div className="text-right">
                    <div className="text-sm text-slate-500">Total Answers</div>
                    <div className="text-lg font-bold text-slate-800">{sub.summary.multiple_choice_count + sub.summary.free_response_count}</div>
                    <div className="text-xs text-slate-400 mt-2">MCQ: {sub.summary.multiple_choice_count} • FR: {sub.summary.free_response_count}</div>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </Card>
      )}

  {/* Candidate details modal */}
  <div>
    {structuredJSON && structuredJSON._selected && (
      <div className="fixed inset-0 z-40 flex items-center justify-center p-4">
        <div className="absolute inset-0 bg-black opacity-40" onClick={() => setStructuredJSON({ ...structuredJSON, _selected: null })}></div>
        <div className="relative z-50 max-w-3xl w-full bg-white rounded-lg shadow-lg p-6 overflow-auto max-h-[80vh]">
          <div className="flex items-start justify-between mb-4">
            <div>
              <h3 className="text-lg font-bold">{structuredJSON._selected.candidate_information.candidate_name || 'Unknown'}</h3>
              <p className="text-xs text-slate-500">Page {structuredJSON._selected.page_number} • ID: {structuredJSON._selected.candidate_information.candidate_number || 'N/A'}</p>
            </div>
            <div className="flex items-center gap-2">
              <button className="text-xs px-3 py-1 bg-slate-100 rounded text-slate-700" onClick={() => setStructuredJSON({ ...structuredJSON, _selected: null })}>Close</button>
              <button className="text-xs px-3 py-1 bg-blue-600 text-white rounded" onClick={() => {
                const dataStr = JSON.stringify(structuredJSON._selected, null, 2);
                const dataUri = 'data:application/json;charset=utf-8,' + encodeURIComponent(dataStr);
                const exportFileDefaultName = `${filename.replace('.pdf', '')}_candidate_p${structuredJSON._selected.page_number}.json`;
                const linkElement = document.createElement('a');
                linkElement.setAttribute('href', dataUri);
                linkElement.setAttribute('download', exportFileDefaultName);
                linkElement.click();
              }}>Export Candidate</button>
            </div>
          </div>

          <div className="flex items-center justify-between mb-2">
            <div className="text-sm text-slate-600">Preview</div>
            <div className="flex items-center gap-2">
              <button className={`text-xs px-2 py-1 rounded ${showCombined ? 'bg-blue-600 text-white' : 'bg-slate-100 text-slate-700'}`} onClick={() => setShowCombined(!showCombined)}>{showCombined ? 'Hide Combined' : 'Show Combined'}</button>
            </div>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
              <h4 className="text-sm font-semibold text-slate-700 mb-2">Multiple Choice</h4>
              <div className="grid grid-cols-1 gap-2">
                {structuredJSON._selected.multiple_choice.map((mc, idx) => (
                  <div key={`mc-${mc.question}-${idx}`} className="flex items-center justify-between bg-slate-50 p-2 rounded border">
                    <div className="text-sm">Q{mc.question}</div>
                    <div className="text-sm font-semibold text-green-700">{mc.answer}</div>
                  </div>
                ))}
              </div>
            </div>

            <div>
              <h4 className="text-sm font-semibold text-slate-700 mb-2">Free Responses</h4>
              <div className="space-y-2">
                {structuredJSON._selected.free_response.map((fr, idx) => (
                  <div key={`fr-${fr.question}-${idx}`} className="bg-slate-50 p-3 rounded border">
                    <div className="text-sm font-medium mb-1">Q{fr.question}</div>
                    <div className="text-sm text-slate-700 whitespace-pre-wrap">{fr.response}</div>
                  </div>
                ))}
              </div>
            </div>
          </div>

          {showCombined && (
            <div className="mt-4">
              <h4 className="text-sm font-semibold text-slate-700 mb-2">All Answers (combined)</h4>
              <div className="grid grid-cols-1 gap-2">
                {structuredJSON._selected.answers && structuredJSON._selected.answers.map((ans, idx) => (
                  <div key={`ans-${ans.type}-${ans.question}-${idx}`} className={`p-2 rounded border ${ans.type === 'mcq' ? 'bg-slate-50' : 'bg-white'}`}>
                    {ans.type === 'mcq' ? (
                      <div className="flex justify-between"><div>Q{ans.question}</div><div className="font-semibold text-green-700">{ans.answer}</div></div>
                    ) : (
                      <div>
                        <div className="text-sm font-medium mb-1">Q{ans.question}</div>
                        <div className="text-sm text-slate-700 whitespace-pre-wrap">{ans.response}</div>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>
    )}

    {structuredJSON && !structuredJSON.submissions && (
        <Card className="mt-4">
          <h3 className="text-lg font-bold mb-2">Structured JSON (raw)</h3>
          <pre className="text-xs text-slate-700 bg-slate-50 p-2 rounded max-h-80 overflow-auto whitespace-pre-wrap">
            {JSON.stringify(structuredJSON, null, 2)}
          </pre>
        </Card>
  )}
  </div>

      {/* Removed detailed MCQ and Free Response lists - summary and per-candidate views remain */}

      {/* Empty State */}
      {multiple_choice.length === 0 && free_response.length === 0 && (
        <Card className="text-center py-12">
          <div className="text-slate-400 mb-4">
            <FileText className="w-16 h-16 mx-auto" />
          </div>
          <h3 className="text-xl font-semibold text-slate-600 mb-2">No Answers Found</h3>
          <p className="text-slate-500">
            No answers were extracted from this exam sheet.
          </p>
        </Card>
      )}
    </div>
  );
};

export default ResultsDisplay;
