import React, { useEffect, useState, useRef } from 'react';
import { CheckCircle2, Clock, AlertCircle, Loader2, FileCheck } from 'lucide-react';
import { Card, Badge, LoadingSpinner } from '../common';
import examAPI from '../../services/api';
import clsx from 'clsx';

/**
 * StatusTracker Component
 * Real-time tracking of PDF processing status
 */
const StatusTracker = ({ submissionId, onComplete }) => {
  const [status, setStatus] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const completedRef = useRef(false);
  const prevPageRef = useRef(null);
  const [pageMessage, setPageMessage] = useState('');
  const [debugOpen, setDebugOpen] = useState(false);
  const [logsOpen, setLogsOpen] = useState(false);
  const [logs, setLogs] = useState([]);

  useEffect(() => {
    if (!submissionId) return;

    let interval;
    let isMounted = true;
    
    const fetchStatus = async () => {
      try {
        console.log('Fetching status for submission', submissionId);
        const data = await examAPI.getStatus(submissionId);
        console.log('Status API response:', data);
        
        if (!isMounted) return;
        
        setStatus(data);
        // Show transient page message when page increments
        const cp = data.current_page !== undefined ? Number(data.current_page) : undefined;
        if (cp !== undefined && prevPageRef.current !== cp) {
          prevPageRef.current = cp;
          const candidate = data.current_candidate_name && data.current_candidate_name.trim() ? data.current_candidate_name : null;
          setPageMessage(`Processed page ${cp}${candidate ? ` — ${candidate}` : ''}`);
          setTimeout(() => setPageMessage(''), 4000);
        }
        setLoading(false);
        setError(''); // Clear any previous errors

        // Stop polling if completed or failed
        if (data.status === 'completed') {
          if (interval) clearInterval(interval);
          // Only call onComplete once
          if (onComplete && !completedRef.current) {
            completedRef.current = true;
            onComplete(data);
          }
        } else if (data.status === 'failed') {
          if (interval) clearInterval(interval);
          setError(data.error_message || 'Processing failed');
        }
      } catch (err) {
        if (!isMounted) return;
        // Don't stop polling on timeout - backend is still processing
        // Just show a warning but keep trying
        console.error('Status check timed out or failed, backend still processing...', err);
        setLoading(false);
        // Don't clear interval - keep retrying
      }
    };

  // Initial fetch
    fetchStatus();

    // Poll every 3 seconds (increased from 2 to reduce server load)
    interval = setInterval(fetchStatus, 3000);

    return () => {
      isMounted = false;
      if (interval) clearInterval(interval);
    };
  }, [submissionId]); // Removed onComplete from dependencies to prevent re-polling

  if (loading) {
    return (
      <Card className="text-center py-8">
        <LoadingSpinner size="lg" text="Loading status..." />
      </Card>
    );
  }

  if (error && !status) {
    return (
      <Card className="border-red-200">
        <div className="flex items-start gap-3">
          <AlertCircle className="w-6 h-6 text-red-600 flex-shrink-0 mt-1" />
          <div>
            <h3 className="font-semibold text-red-800 mb-1">Error</h3>
            <p className="text-red-600">{error}</p>
          </div>
        </div>
      </Card>
    );
  }
  
  // Show processing message if we have status even with errors
  if (!status && !loading) {
    return (
      <Card className="border-blue-200 bg-blue-50">
        <div className="flex items-start gap-3">
          <Loader2 className="w-6 h-6 text-blue-600 flex-shrink-0 mt-1 animate-spin" />
          <div>
            <h3 className="font-semibold text-blue-800 mb-1">Processing in Background</h3>
            <p className="text-blue-600">Your PDF is being processed. This may take 3-5 minutes for large files.</p>
            <p className="text-blue-500 text-sm mt-2">Status updates will appear when available...</p>
          </div>
        </div>
      </Card>
    );
  }

  if (!status) return null;

  const statusConfig = {
    pending: {
      icon: Clock,
      color: 'text-gray-600',
      bgColor: 'bg-gray-100',
      badge: 'pending',
      title: 'Waiting in Queue',
      description: 'Your submission is waiting to be processed',
    },
    processing: {
      icon: Loader2,
      color: 'text-blue-600',
      bgColor: 'bg-blue-100',
      badge: 'info',
      title: 'Processing',
      description: 'Extracting answers from your exam sheet',
      animate: 'animate-spin',
    },
    completed: {
      icon: CheckCircle2,
      color: 'text-green-600',
      bgColor: 'bg-green-100',
      badge: 'success',
      title: 'Completed',
      description: 'All answers have been successfully extracted',
    },
    failed: {
      icon: AlertCircle,
      color: 'text-red-600',
      bgColor: 'bg-red-100',
      badge: 'error',
      title: 'Failed',
      description: 'Processing failed. Please try again.',
    },
  };

  const config = statusConfig[status.status] || statusConfig.pending;
  const StatusIcon = config.icon;

  return (
    <Card className="max-w-2xl mx-auto">
      <div className="flex items-start gap-4">
        <div className={clsx('w-14 h-14 rounded-full flex items-center justify-center flex-shrink-0', config.bgColor)}>
          <StatusIcon className={clsx('w-7 h-7', config.color, config.animate)} />
        </div>

        <div className="flex-1">
          <div className="flex items-center justify-between mb-2">
            <h3 className="text-xl font-bold text-slate-800">{config.title}</h3>
            <Badge variant={config.badge}>{status.status}</Badge>
          </div>
          {pageMessage && (
            <div className="mb-2 bg-blue-50 border border-blue-100 rounded p-2 text-sm text-blue-700">{pageMessage}</div>
          )}
          <div className="mt-2">
            <button className="text-xs text-slate-500 hover:underline mr-4" onClick={async () => {
              try {
                const data = await examAPI.getSubmissionLogs(submissionId);
                console.log('Fetched logs:', data);
                setLogs(data);
                setLogsOpen(true);
              } catch (err) {
                console.error('Failed to fetch logs', err);
                alert('Failed to fetch logs');
              }
            }}>Show logs</button>
          </div>

          {/* Current page and candidate summary (visible at all times if available) */}
          <div className="flex items-center gap-6 mb-4 text-sm text-slate-600">
            <div>
              <p className="text-xs text-slate-500">Current Page</p>
              <p className="font-semibold text-slate-800">{status.current_page !== undefined ? Number(status.current_page) : '—'}</p>
            </div>
            <div>
              <p className="text-xs text-slate-500">Candidate</p>
              <p className="font-semibold text-slate-800">{status.current_candidate_name && status.current_candidate_name.trim() ? status.current_candidate_name : 'No name detected'}</p>
            </div>
          </div>

          <p className="text-slate-600 mb-4">{config.description}</p>

          <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
            <div>
              <p className="text-slate-500 mb-1">Filename</p>
              <p className="font-semibold text-slate-800 truncate">{status.filename}</p>
            </div>
            
            <div>
              <p className="text-slate-500 mb-1">Pages</p>
              <p className="font-semibold text-slate-800">{status.pages_count || 0}</p>
            </div>
            
            <div>
              <p className="text-slate-500 mb-1">MCQ Answers</p>
              <p className="font-semibold text-slate-800">{status.mcq_count || 0}</p>
            </div>
            
            <div>
              <p className="text-slate-500 mb-1">Free Response</p>
              <p className="font-semibold text-slate-800">{status.free_response_count || 0}</p>
            </div>
          </div>

          { (status.current_page !== undefined || (status.current_candidate_name && status.current_candidate_name.trim())) && (
            <div className="mt-4">
              {/* Progress bar with dynamic width based on page / pages_count */}
              <div className="w-full bg-gray-200 rounded-full h-3 overflow-hidden mb-3">
                {status.current_page !== undefined && status.pages_count > 0 ? (
                  <div
                    className="h-full bg-gradient-to-r from-blue-500 to-blue-600 rounded-full"
                    style={{ width: `${Math.min(100, Math.round((Number(status.current_page) / Math.max(1, Number(status.pages_count))) * 100))}%` }}
                    aria-hidden
                  />
                ) : (
                  <div className="h-full bg-gradient-to-r from-blue-500 to-blue-600 animate-pulse rounded-full" style={{ width: '30%' }} />
                )}
              </div>

              <div className="flex items-center justify-between gap-4 mb-2">
                <div className="text-blue-700 text-sm">
                  {status.current_page !== undefined && Number(status.pages_count) > 0 ? (
                    <div>
                      Processing Page <span className="font-semibold">{Number(status.current_page)}</span> of <span className="font-semibold">{Number(status.pages_count)}</span> — <span className="text-xs text-slate-500">{Math.min(100, Math.round((Number(status.current_page) / Math.max(1, Number(status.pages_count))) * 100))}%</span>
                    </div>
                  ) : (
                    <div>Processing page <span className="font-semibold">{status.current_page || '...'}</span></div>
                  )}
                </div>
                <div className="text-sm text-slate-500">
                  {status.processed_at ? `Started at ${new Date(status.processed_at).toLocaleTimeString()}` : ''}
                </div>
              </div>

              <div className="flex flex-col md:flex-row md:items-center md:gap-6 md:justify-between text-blue-700 text-sm">
                <div className="flex items-center gap-3">
                  <div className="text-sm text-slate-500">Candidate</div>
                    <div className="text-blue-700 font-semibold">{status.current_candidate_name && status.current_candidate_name.trim() ? status.current_candidate_name : 'Unknown'}</div>
                </div>
                <div className="flex items-center gap-3 text-sm text-slate-600">
                  <div>Page: <span className="font-semibold">{status.current_page || '—'}</span></div>
                  <div>of <span className="font-semibold">{status.pages_count || '—'}</span></div>
                </div>
              </div>
            </div>
          )}

          <div className="mt-4">
            <button
              onClick={() => setDebugOpen(!debugOpen)}
              className="text-xs text-slate-500 hover:underline"
            >
              {debugOpen ? 'Hide' : 'Show'} raw status JSON
            </button>
            {debugOpen && (
                <pre className="mt-2 text-xs text-slate-700 bg-slate-50 p-2 rounded max-h-40 overflow-auto whitespace-pre-wrap">
                  {JSON.stringify(status, null, 2)}
                </pre>
              )}

            {logsOpen && (
              <div className="mt-2 bg-slate-50 p-2 rounded border">
                <div className="text-sm font-semibold mb-2">Recent Logs</div>
                <pre className="text-xs text-slate-700 max-h-44 overflow-auto whitespace-pre-wrap">{JSON.stringify(logs, null, 2)}</pre>
              </div>
            )}
          </div>
        </div>
      </div>
    </Card>
  );
};

export default StatusTracker;
