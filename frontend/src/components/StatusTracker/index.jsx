import React, { useEffect, useState, useRef } from 'react';
import { CheckCircle2, Clock, AlertCircle, Loader2 } from 'lucide-react';
import { Card, Badge, LoadingSpinner } from '../common';
import examAPI from '../../services/api';
import clsx from 'clsx';

/**
 * StatusTracker Component
 * Real-time tracking of PDF processing status with dynamic stats
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
        const data = await examAPI.getStatus(submissionId);
        if (!isMounted) return;

        setStatus(data);

        // Transient page message
        const cp = data.current_page != null ? Number(data.current_page) : undefined;
        if (cp !== undefined && prevPageRef.current !== cp) {
          prevPageRef.current = cp;
          const candidate = data.current_candidate_name?.trim() || null;
          setPageMessage(`Processed page ${cp}${candidate ? ` \u2014 ${candidate}` : ''}`);
          setTimeout(() => setPageMessage(''), 4000);
        }

        setLoading(false);
        setError('');

        if (data.status === 'completed') {
          if (interval) clearInterval(interval);
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
        console.error('Status check failed, retrying...', err);
        setLoading(false);
      }
    };

    fetchStatus();
    interval = setInterval(fetchStatus, 3000);

    return () => {
      isMounted = false;
      if (interval) clearInterval(interval);
    };
  }, [submissionId]);

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

  if (!status && !loading) {
    return (
      <Card className="border-blue-200 bg-blue-50">
        <div className="flex items-start gap-3">
          <Loader2 className="w-6 h-6 text-blue-600 flex-shrink-0 mt-1 animate-spin" />
          <div>
            <h3 className="font-semibold text-blue-800 mb-1">Processing in Background</h3>
            <p className="text-blue-600">Your PDF is being processed. This may take 3\u20135 minutes for large files.</p>
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
      description: status.error_message || 'Processing failed. Please try again.',
    },
  };

  const config = statusConfig[status.status] || statusConfig.pending;
  const StatusIcon = config.icon;
  const progress = status.current_page && status.pages_count > 0
    ? Math.min(100, Math.round((Number(status.current_page) / Number(status.pages_count)) * 100))
    : 0;

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

          {/* Transient page message */}
          {pageMessage && (
            <div className="mb-2 bg-blue-50 border border-blue-100 rounded p-2 text-sm text-blue-700">
              {pageMessage}
            </div>
          )}

          <p className="text-slate-600 mb-4 text-sm">{config.description}</p>

          {/* Stats grid \u2014 flexible labels based on what the AI found */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-sm mb-4">
            <div>
              <p className="text-slate-500 text-xs mb-0.5">Filename</p>
              <p className="font-semibold text-slate-800 truncate">{status.filename}</p>
            </div>
            <div>
              <p className="text-slate-500 text-xs mb-0.5">Pages</p>
              <p className="font-semibold text-slate-800">{status.pages_count || 0}</p>
            </div>
            <div>
              <p className="text-slate-500 text-xs mb-0.5">Candidates</p>
              <p className="font-semibold text-slate-800">{status.candidates_count || 0}</p>
            </div>
            <div>
              <p className="text-slate-500 text-xs mb-0.5">Answers</p>
              <p className="font-semibold text-slate-800">
                {(status.answers_count || 0)}
                {(status.drawing_count || 0) > 0 && (
                  <span className="text-xs text-slate-400 ml-1">
                    (+{status.drawing_count} drawing)
                  </span>
                )}
              </p>
            </div>
          </div>

          {/* Progress bar */}
          {(status.status === 'processing' || (status.current_page != null && status.pages_count > 0)) && (
            <div className="mb-4">
              <div className="w-full bg-gray-200 rounded-full h-2.5 overflow-hidden mb-2">
                {progress > 0 ? (
                  <div
                    className="h-full bg-gradient-to-r from-blue-500 to-blue-600 rounded-full transition-all duration-300"
                    style={{ width: `${progress}%` }}
                  />
                ) : (
                  <div className="h-full bg-gradient-to-r from-blue-500 to-blue-600 animate-pulse rounded-full" style={{ width: '15%' }} />
                )}
              </div>
              <div className="flex items-center justify-between text-xs text-slate-600">
                <span>
                  Page <strong>{status.current_page || '...'}</strong> of <strong>{status.pages_count || '...'}</strong>
                  {progress > 0 && <span className="ml-2 text-slate-400">{progress}%</span>}
                </span>
                {status.current_candidate_name?.trim() && (
                  <span className="text-blue-600">Current: {status.current_candidate_name}</span>
                )}
              </div>
            </div>
          )}

          {/* Error message */}
          {error && (
            <div className="bg-red-50 border border-red-200 rounded-lg p-3 mb-4">
              <p className="text-sm text-red-700">{error}</p>
            </div>
          )}

          {/* Action buttons */}
          <div className="flex items-center gap-3 mt-2">
            <button
              onClick={async () => {
                try {
                  const data = await examAPI.getSubmissionLogs(submissionId);
                  setLogs(data);
                  setLogsOpen(!logsOpen);
                } catch (err) {
                  console.error('Failed to fetch logs', err);
                }
              }}
              className="text-xs text-slate-500 hover:text-blue-600 hover:underline transition"
            >
              {logsOpen ? 'Hide' : 'Show'} logs
            </button>
            <button
              onClick={() => setDebugOpen(!debugOpen)}
              className="text-xs text-slate-500 hover:text-blue-600 hover:underline transition"
            >
              {debugOpen ? 'Hide' : 'Show'} raw status
            </button>
          </div>

          {/* Logs */}
          {logsOpen && logs.length > 0 && (
            <div className="mt-3 bg-slate-50 border border-slate-200 rounded-lg p-3">
              <div className="text-xs font-semibold text-slate-600 mb-2">Recent Logs ({logs.length})</div>
              <div className="space-y-1 max-h-48 overflow-auto">
                {logs.map((log, i) => (
                  <div key={log.id || i} className="flex items-start gap-2 text-xs">
                    <span className={clsx(
                      'px-1.5 py-0.5 rounded font-medium',
                      log.status === 'error' ? 'bg-red-100 text-red-700' :
                      log.status === 'success' ? 'bg-green-100 text-green-700' :
                      'bg-blue-100 text-blue-700'
                    )}>
                      {log.action}
                    </span>
                    <span className="text-slate-600 flex-1">{log.message}</span>
                    {log.created_at && (
                      <span className="text-slate-400 whitespace-nowrap">
                        {new Date(log.created_at).toLocaleTimeString()}
                      </span>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Debug JSON */}
          {debugOpen && (
            <pre className="mt-3 text-xs text-slate-700 bg-slate-50 p-3 rounded-lg max-h-40 overflow-auto whitespace-pre-wrap border border-slate-200">
              {JSON.stringify(status, null, 2)}
            </pre>
          )}
        </div>
      </div>
    </Card>
  );
};

export default StatusTracker;
