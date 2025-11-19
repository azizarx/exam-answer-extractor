import React, { useState, useEffect, useCallback } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { ArrowLeft, RefreshCw } from 'lucide-react';
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

  const handleStatusComplete = useCallback(async (statusData) => {
    if (statusData.status === 'completed' && !results) {
      // Fetch full results only if we don't have them yet
      setLoading(true);
      try {
        const data = await examAPI.getSubmission(submissionId);
        setResults(data);
      } catch (err) {
        setError('Failed to load results');
      } finally {
        setLoading(false);
      }
    }
  }, [submissionId, results]);

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
          <ResultsDisplay results={results} />
        )}
      </div>
    </div>
  );
};

export default TrackingPage;
