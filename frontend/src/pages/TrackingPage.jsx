import { useState, useEffect, useCallback, useRef } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { ArrowLeft } from 'lucide-react';
import StatusTracker from '../components/StatusTracker';
import ResultsDisplay from '../components/ResultsDisplay';
import { Button, LoadingSpinner, Alert } from '../components/common';
import examAPI from '../services/api';

/**
 * TrackingPage
 * Track submission status and view results
 */
const TrackingPage = () => {
  const { submissionId } = useParams();
  const navigate = useNavigate();
  const [results, setResults] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [marking, setMarking] = useState({ loading: false, error: '' });
  const loadRequestRef = useRef(0);
  const requestedResultsSubmissionRef = useRef(null);
  const markingInFlightRef = useRef(false);
  const markingRequestIdRef = useRef(0);
  const currentSubmissionIdRef = useRef(submissionId);
  currentSubmissionIdRef.current = submissionId;

  const loadResults = useCallback(async (expectedSubmissionId, statusData = null) => {
    if (expectedSubmissionId !== currentSubmissionIdRef.current) return null;
    const requestId = ++loadRequestRef.current;
    setLoading(true);
    try {
      const data = await examAPI.getSubmission(expectedSubmissionId);
      if (
        requestId !== loadRequestRef.current ||
        expectedSubmissionId !== currentSubmissionIdRef.current
      ) return null;
      if (!data.processed_at && statusData?.processed_at) {
        data.processed_at = statusData.processed_at;
      }
      setResults(data);
      setError('');
      return data;
    } catch (err) {
      if (
        requestId !== loadRequestRef.current ||
        expectedSubmissionId !== currentSubmissionIdRef.current
      ) return null;
      console.error('Failed to load results:', err);
      setError(err?.response?.data?.detail || 'Failed to load results');
      return null;
    } finally {
      if (
        requestId === loadRequestRef.current &&
        expectedSubmissionId === currentSubmissionIdRef.current
      ) {
        setLoading(false);
      }
    }
  }, []);

  useEffect(() => {
    requestedResultsSubmissionRef.current = null;
    markingInFlightRef.current = false;
    markingRequestIdRef.current += 1;
    loadRequestRef.current += 1;
    setResults(null);
    setLoading(false);
    setError('');
    setMarking({ loading: false, error: '' });
  }, [submissionId]);

  const handleStatusComplete = useCallback(async (statusData) => {
    if (
      submissionId === currentSubmissionIdRef.current &&
      statusData.status === 'completed' &&
      requestedResultsSubmissionRef.current !== submissionId
    ) {
      requestedResultsSubmissionRef.current = submissionId;
      const data = await loadResults(submissionId, statusData);
      if (!data && requestedResultsSubmissionRef.current === submissionId) {
        requestedResultsSubmissionRef.current = null;
      }
    }
  }, [loadResults, submissionId]);

  const handleMark = useCallback(async () => {
    if (markingInFlightRef.current) return;
    markingInFlightRef.current = true;
    const requestId = ++markingRequestIdRef.current;
    const expectedSubmissionId = submissionId;
    setMarking({ loading: true, error: '' });
    try {
      await examAPI.markSubmission(expectedSubmissionId);
      if (
        requestId !== markingRequestIdRef.current ||
        expectedSubmissionId !== currentSubmissionIdRef.current
      ) return;
      const refreshed = await loadResults(expectedSubmissionId);
      if (
        requestId !== markingRequestIdRef.current ||
        expectedSubmissionId !== currentSubmissionIdRef.current
      ) return;
      if (!refreshed) {
        throw new Error('Marking finished, but the refreshed results could not be loaded.');
      }
      setMarking({ loading: false, error: '' });
    } catch (err) {
      if (
        requestId !== markingRequestIdRef.current ||
        expectedSubmissionId !== currentSubmissionIdRef.current
      ) return;
      console.error('Failed to mark submission:', err);
      const detail = err?.response?.data?.detail;
      setMarking({
        loading: false,
        error: typeof detail === 'string'
          ? detail
          : err?.message || 'Marking could not be completed.',
      });
    } finally {
      if (
        requestId === markingRequestIdRef.current &&
        expectedSubmissionId === currentSubmissionIdRef.current
      ) {
        markingInFlightRef.current = false;
      }
    }
  }, [loadResults, submissionId]);

  const handleBackToUpload = () => {
    navigate('/');
  };

  return (
    <div className="min-h-screen py-12 px-4">
      <div className="max-w-6xl mx-auto">
        {/* Header */}
        <div className="flex items-center justify-between mb-8">
          <Button variant="ghost" onClick={handleBackToUpload}>
            <ArrowLeft className="w-5 h-5" />
            Upload Another
          </Button>
          
          <div className="text-right">
            <p className="text-sm text-slate-500">Submission ID</p>
            <p className="text-lg font-mono font-semibold text-slate-800">#{submissionId}</p>
          </div>
        </div>

        {/* Status Tracker */}
        <div className="mb-8">
          <StatusTracker 
            submissionId={submissionId} 
            onComplete={handleStatusComplete}
          />
        </div>

        {/* Loading State */}
        {loading && (
          <div className="text-center py-12">
            <LoadingSpinner size="lg" text="Loading results..." />
          </div>
        )}

        {/* Error State */}
        {error && (
          <Alert type="error" className="mb-6">
            {error}
          </Alert>
        )}

        {/* Results */}
        {results && !loading && (
          <ResultsDisplay
            results={results}
            onMark={handleMark}
            markingRequest={marking}
            onRefresh={() => loadResults(submissionId)}
          />
        )}
      </div>
    </div>
  );
};

export default TrackingPage;
