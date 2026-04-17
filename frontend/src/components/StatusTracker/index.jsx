import React, { useEffect, useMemo, useRef, useState } from 'react';
import { AlertCircle, CheckCircle2, Clock, Loader2 } from 'lucide-react';
import { Card, Badge, LoadingSpinner } from '../common';
import examAPI from '../../services/api';
import clsx from 'clsx';

const ACTION_LABELS = {
  upload: 'Uploaded',
  extract_start: 'Extraction started',
  page_progress: 'Progress update',
  extract_complete: 'Extraction complete',
  extract_error: 'Extraction failed',
};

const toFiniteNumber = (value) => {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
};

const clampPercent = (value) => Math.max(0, Math.min(100, Math.round(value)));

const sortLogsAscending = (logs = []) =>
  [...logs].sort((a, b) => {
    const aTime = a?.created_at ? new Date(a.created_at).getTime() : 0;
    const bTime = b?.created_at ? new Date(b.created_at).getTime() : 0;

    if (aTime !== bTime) return aTime - bTime;
    return (a?.id || 0) - (b?.id || 0);
  });

const getLatestProgressLog = (logs = []) => {
  for (let i = logs.length - 1; i >= 0; i -= 1) {
    if (logs[i]?.action === 'page_progress') {
      return logs[i];
    }
  }
  return null;
};

const formatTime = (value) => {
  if (!value) return '';
  try {
    return new Date(value).toLocaleTimeString([], {
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    });
  } catch {
    return '';
  }
};

const formatLogMessage = (log) => {
  if (!log) return '';

  const extraData = log.extra_data && typeof log.extra_data === 'object' ? log.extra_data : {};
  const page = toFiniteNumber(extraData.page);
  const current = toFiniteNumber(extraData.current);
  const total = toFiniteNumber(extraData.total);
  const candidate = (extraData.label || extraData.candidate_name || '').toString().trim();

  if (log.action === 'page_progress') {
    if (page) {
      return `Processed page ${page}${candidate ? ` - ${candidate}` : ''}`;
    }
    if (current && total) {
      return `Step ${current} of ${total}`;
    }
  }

  return log.message || ACTION_LABELS[log.action] || log.action || 'Log event';
};

const computeProgress = (status, logs) => {
  if (!status) {
    return { percent: 0, current: null, total: null, label: 'Waiting for status...' };
  }

  const pagesTotal = toFiniteNumber(status.pages_count);
  const currentPage = toFiniteNumber(status.current_page);
  const latestProgressLog = getLatestProgressLog(logs);
  const latestExtra = latestProgressLog?.extra_data && typeof latestProgressLog.extra_data === 'object'
    ? latestProgressLog.extra_data
    : {};
  const logProgress = toFiniteNumber(latestExtra.progress);
  const logCurrent = toFiniteNumber(latestExtra.current) ?? toFiniteNumber(latestExtra.page);
  const logTotal = toFiniteNumber(latestExtra.total);
  const latestLabel = formatLogMessage(latestProgressLog);

  if (status.status === 'completed') {
    const total = pagesTotal ?? logTotal ?? logCurrent;
    return {
      percent: 100,
      current: total,
      total,
      label: 'Processing complete',
    };
  }

  if (status.status === 'failed') {
    const inferredTotal = logTotal ?? pagesTotal;
    const inferredCurrent = logCurrent ?? currentPage;
    let failedPercent = 0;

    if (logProgress != null) {
      failedPercent = logProgress <= 1 ? logProgress * 100 : logProgress;
    } else if (inferredCurrent && inferredTotal) {
      failedPercent = (inferredCurrent / inferredTotal) * 100;
    }

    return {
      percent: clampPercent(failedPercent),
      current: inferredCurrent,
      total: inferredTotal,
      label: status.error_message || 'Processing failed',
    };
  }

  if (status.status === 'pending') {
    return {
      percent: 5,
      current: null,
      total: pagesTotal,
      label: 'Queued for processing',
    };
  }

  if (pagesTotal && currentPage) {
    return {
      percent: clampPercent((currentPage / pagesTotal) * 100),
      current: currentPage,
      total: pagesTotal,
      label: `Processing page ${currentPage} of ${pagesTotal}`,
    };
  }

  if (logProgress != null) {
    const normalized = logProgress <= 1 ? logProgress * 100 : logProgress;
    return {
      percent: clampPercent(normalized),
      current: logCurrent ?? currentPage,
      total: logTotal ?? pagesTotal,
      label: latestLabel || 'Processing...',
    };
  }

  const inferredTotal = logTotal ?? pagesTotal;
  if (logCurrent && inferredTotal) {
    return {
      percent: clampPercent((logCurrent / inferredTotal) * 100),
      current: logCurrent,
      total: inferredTotal,
      label: latestLabel || 'Processing...',
    };
  }

  const hasStarted = logs.some((log) => log.action === 'extract_start' || log.action === 'page_progress');
  return {
    percent: hasStarted ? 12 : 8,
    current: null,
    total: inferredTotal,
    label: hasStarted ? 'Extraction started' : 'Preparing extraction...',
  };
};

/**
 * StatusTracker Component
 * Real-time tracking of PDF processing status with dynamic stats
 */
const StatusTracker = ({ submissionId, onComplete }) => {
  const [status, setStatus] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [progressPercent, setProgressPercent] = useState(0);
  const completedRef = useRef(false);
  const hasStatusRef = useRef(false);
  const prevPageRef = useRef(null);
  const [pageMessage, setPageMessage] = useState('');
  const [debugOpen, setDebugOpen] = useState(false);
  const [logsOpen, setLogsOpen] = useState(false);
  const [logs, setLogs] = useState([]);

  const orderedLogs = useMemo(() => sortLogsAscending(logs), [logs]);
  const latestLog = orderedLogs.length ? orderedLogs[orderedLogs.length - 1] : null;
  const displayLogs = useMemo(() => [...orderedLogs].reverse(), [orderedLogs]);
  const progressInfo = useMemo(() => computeProgress(status, orderedLogs), [status, orderedLogs]);

  useEffect(() => {
    setProgressPercent(0);
    setLogs([]);
    setPageMessage('');
    prevPageRef.current = null;
    completedRef.current = false;
    hasStatusRef.current = false;
  }, [submissionId]);

  useEffect(() => {
    if (!status) return;

    if (status.status === 'completed') {
      setProgressPercent(100);
      return;
    }

    if (status.status === 'processing' || status.status === 'failed') {
      setProgressPercent((previous) => Math.max(previous, progressInfo.percent));
      return;
    }

    setProgressPercent(progressInfo.percent);
  }, [status?.status, progressInfo.percent]);

  useEffect(() => {
    if (!submissionId) return;

    let interval;
    let isMounted = true;

    const fetchStatus = async () => {
      try {
        const data = await examAPI.getStatus(submissionId);
        if (!isMounted) return;

        setStatus(data);
        setLoading(false);
        setError('');
        hasStatusRef.current = true;

        const currentPage = data.current_page != null ? Number(data.current_page) : null;
        if (currentPage && prevPageRef.current !== currentPage) {
          prevPageRef.current = currentPage;
          const candidate = data.current_candidate_name?.trim() || null;
          setPageMessage(`Processed page ${currentPage}${candidate ? ` - ${candidate}` : ''}`);
          setTimeout(() => setPageMessage(''), 4000);
        }

        try {
          const latestLogs = await examAPI.getSubmissionLogs(submissionId, 50);
          if (!isMounted) return;

          const normalizedLogs = sortLogsAscending(latestLogs);
          setLogs(normalizedLogs);

          if (!currentPage) {
            const latestProgress = getLatestProgressLog(normalizedLogs);
            const extraData = latestProgress?.extra_data && typeof latestProgress.extra_data === 'object'
              ? latestProgress.extra_data
              : {};
            const logPage = toFiniteNumber(extraData.page) ?? toFiniteNumber(extraData.current);
            if (logPage && prevPageRef.current !== logPage) {
              prevPageRef.current = logPage;
              setPageMessage(formatLogMessage(latestProgress));
              setTimeout(() => setPageMessage(''), 4000);
            }
          }
        } catch (logErr) {
          if (!isMounted) return;
          if (logErr?.code !== 'ECONNABORTED') {
            console.error('Failed to fetch submission logs, continuing with status only.', logErr);
          }
        }

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
        const isTimeout = err?.code === 'ECONNABORTED';
        if (!isTimeout) {
          console.error('Status check failed, retrying...', err);
        }
        setLoading(false);

        if (isTimeout) {
          if (hasStatusRef.current) {
            setPageMessage('Backend is busy, retrying status...');
            setTimeout(() => setPageMessage(''), 2500);
            return;
          }
          setError('Backend is taking longer than expected. Retrying...');
          return;
        }

        const detail = err?.response?.data?.detail || err?.message;
        if (detail) {
          setError(detail);
        }
      }
    };

    fetchStatus();
    interval = setInterval(fetchStatus, 3000);

    return () => {
      isMounted = false;
      if (interval) clearInterval(interval);
    };
  }, [submissionId, onComplete]);

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
            <p className="text-blue-600">Your PDF is being processed. This may take 3-5 minutes for large files.</p>
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
  const statusCurrent = toFiniteNumber(status.current_page);
  const statusTotal = toFiniteNumber(status.pages_count);
  const progressCurrent = statusCurrent ?? progressInfo.current;
  const progressTotal = statusTotal && statusTotal > 0 ? statusTotal : progressInfo.total;
  const showProgress = ['pending', 'processing', 'completed'].includes(status.status);
  const visibleProgress = status.status === 'completed'
    ? 100
    : Math.max(progressPercent, status.status === 'pending' ? 5 : 10);
  const latestActivity = latestLog ? formatLogMessage(latestLog) : null;

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
            <div className="mb-2 bg-blue-50 border border-blue-100 rounded p-2 text-sm text-blue-700">
              {pageMessage}
            </div>
          )}

          <p className="text-slate-600 mb-4 text-sm">{config.description}</p>

          {latestActivity && status.status !== 'completed' && (
            <div className="mb-3 bg-slate-50 border border-slate-200 rounded p-2 text-xs text-slate-600">
              <span className="font-semibold text-slate-700">Latest:</span> {latestActivity}
            </div>
          )}

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
                {status.answers_count || 0}
                {(status.drawing_count || 0) > 0 && (
                  <span className="text-xs text-slate-400 ml-1">(+{status.drawing_count} drawing)</span>
                )}
              </p>
            </div>
          </div>

          {showProgress && (
            <div className="mb-4">
              <div className="w-full bg-gray-200 rounded-full h-2.5 overflow-hidden mb-2">
                <div
                  className={clsx(
                    'h-full rounded-full transition-all duration-300',
                    status.status === 'completed'
                      ? 'bg-gradient-to-r from-green-500 to-green-600'
                      : 'bg-gradient-to-r from-blue-500 to-blue-600'
                  )}
                  style={{ width: `${visibleProgress}%` }}
                />
              </div>
              <div className="flex items-center justify-between text-xs text-slate-600">
                <span>
                  {progressCurrent && progressTotal
                    ? <>Step <strong>{progressCurrent}</strong> of <strong>{progressTotal}</strong></>
                    : <>{progressInfo.label}</>}
                  <span className="ml-2 text-slate-400">{visibleProgress}%</span>
                </span>
                {status.current_candidate_name?.trim() && (
                  <span className="text-blue-600">Current: {status.current_candidate_name}</span>
                )}
              </div>
            </div>
          )}

          {error && (
            <div className="bg-red-50 border border-red-200 rounded-lg p-3 mb-4">
              <p className="text-sm text-red-700">{error}</p>
            </div>
          )}

          <div className="flex items-center gap-3 mt-2">
            <button
              onClick={() => setLogsOpen((open) => !open)}
              className="text-xs text-slate-500 hover:text-blue-600 hover:underline transition"
            >
              {logsOpen ? 'Hide' : 'Show'} logs ({orderedLogs.length})
            </button>
            <button
              onClick={() => setDebugOpen((open) => !open)}
              className="text-xs text-slate-500 hover:text-blue-600 hover:underline transition"
            >
              {debugOpen ? 'Hide' : 'Show'} raw status
            </button>
          </div>

          {logsOpen && (
            <div className="mt-3 bg-slate-50 border border-slate-200 rounded-lg p-3">
              <div className="text-xs font-semibold text-slate-600 mb-2">Recent Logs (auto-refresh)</div>
              {displayLogs.length === 0 ? (
                <p className="text-xs text-slate-500">No logs yet. They will appear as soon as processing starts.</p>
              ) : (
                <div className="space-y-2 max-h-56 overflow-auto">
                  {displayLogs.slice(0, 30).map((log, index) => {
                    const extraData = log.extra_data && typeof log.extra_data === 'object' ? log.extra_data : {};
                    const page = toFiniteNumber(extraData.page);
                    const current = toFiniteNumber(extraData.current);
                    const total = toFiniteNumber(extraData.total);
                    const answersCount = toFiniteNumber(extraData.answers_count);
                    const drawingCount = toFiniteNumber(extraData.drawing_count);
                    const progressValue = toFiniteNumber(extraData.progress);
                    const progressFromLog = progressValue != null
                      ? clampPercent(progressValue <= 1 ? progressValue * 100 : progressValue)
                      : null;

                    return (
                      <div key={log.id || index} className="border border-slate-200 rounded-md p-2 bg-white text-xs">
                        <div className="flex items-start gap-2">
                          <span className={clsx(
                            'px-1.5 py-0.5 rounded font-medium whitespace-nowrap',
                            log.status === 'error' ? 'bg-red-100 text-red-700' :
                            log.status === 'success' ? 'bg-green-100 text-green-700' :
                            'bg-blue-100 text-blue-700'
                          )}>
                            {ACTION_LABELS[log.action] || log.action || 'event'}
                          </span>
                          <span className="text-slate-700 flex-1">{formatLogMessage(log)}</span>
                          {log.created_at && (
                            <span className="text-slate-400 whitespace-nowrap">{formatTime(log.created_at)}</span>
                          )}
                        </div>

                        <div className="flex flex-wrap gap-1 mt-1.5">
                          {page && <span className="px-1.5 py-0.5 rounded bg-slate-100 text-slate-600">Page {page}</span>}
                          {!page && current && total && (
                            <span className="px-1.5 py-0.5 rounded bg-slate-100 text-slate-600">
                              Step {current}/{total}
                            </span>
                          )}
                          {progressFromLog != null && (
                            <span className="px-1.5 py-0.5 rounded bg-blue-50 text-blue-700">{progressFromLog}%</span>
                          )}
                          {answersCount != null && (
                            <span className="px-1.5 py-0.5 rounded bg-green-50 text-green-700">
                              {answersCount} answers
                            </span>
                          )}
                          {drawingCount != null && (
                            <span className="px-1.5 py-0.5 rounded bg-purple-50 text-purple-700">
                              {drawingCount} drawing
                            </span>
                          )}
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          )}

          {debugOpen && (
            <pre className="mt-3 text-xs text-slate-700 bg-slate-50 p-3 rounded-lg max-h-56 overflow-auto whitespace-pre-wrap border border-slate-200">
              {JSON.stringify({ status, progressInfo, logs: displayLogs.slice(0, 10) }, null, 2)}
            </pre>
          )}
        </div>
      </div>
    </Card>
  );
};

export default StatusTracker;
