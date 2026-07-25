import {
  AlertCircle,
  CheckCircle2,
  Clock3,
  RefreshCw,
  ShieldAlert,
} from 'lucide-react';
import { Button } from '../common';

const STATUS_CONTENT = {
  completed: {
    label: 'Marking completed',
    description: 'Weighted scores and question outcomes are ready.',
    icon: CheckCircle2,
    classes: 'border-emerald-200 bg-emerald-50 text-emerald-950',
    iconClasses: 'text-emerald-700',
  },
  unavailable: {
    label: 'Marking unavailable',
    description: 'No compatible active answer key is currently available.',
    icon: ShieldAlert,
    classes: 'border-amber-200 bg-amber-50 text-amber-950',
    iconClasses: 'text-amber-700',
  },
  failed: {
    label: 'Marking failed',
    description: 'The extracted responses are safe, but marking did not complete.',
    icon: AlertCircle,
    classes: 'border-red-200 bg-red-50 text-red-950',
    iconClasses: 'text-red-700',
  },
  processing: {
    label: 'Marking in progress',
    description: 'Scores will appear when marking completes.',
    icon: Clock3,
    classes: 'border-blue-200 bg-blue-50 text-blue-950',
    iconClasses: 'text-blue-700',
  },
  unmarked: {
    label: 'Not marked',
    description: 'The extraction is complete and can be marked without processing the PDF again.',
    icon: Clock3,
    classes: 'border-slate-200 bg-slate-50 text-slate-900',
    iconClasses: 'text-slate-600',
  },
};

const safeMessage = (message) => {
  if (typeof message !== 'string') return '';
  // eslint-disable-next-line no-control-regex -- Remove non-printable backend text before display.
  return message.replace(/[\u0000-\u001f\u007f]/g, ' ').trim().slice(0, 240);
};

const MarkingStatusBanner = ({ latestMarking, onMark, markingRequest }) => {
  const status = latestMarking?.status || 'unmarked';
  const content = STATUS_CONTENT[status] || STATUS_CONTENT.unmarked;
  const StatusIcon = content.icon;
  const isProcessing = status === 'processing';
  const actionLabel = status === 'completed'
    ? 'Re-mark results'
    : status === 'failed' || status === 'unavailable'
      ? 'Retry marking'
      : 'Mark results';
  const backendMessage = safeMessage(latestMarking?.error_message);
  const requestError = safeMessage(markingRequest?.error);

  return (
    <section
      className={`rounded-xl border p-4 ${content.classes}`}
      aria-labelledby="marking-status-title"
      aria-live="polite"
      aria-busy={Boolean(markingRequest?.loading)}
    >
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex min-w-0 items-start gap-3">
          <StatusIcon
            className={`mt-0.5 h-5 w-5 flex-none ${content.iconClasses}`}
            aria-hidden="true"
          />
          <div>
            <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
              <h3 id="marking-status-title" className="font-semibold">
                {content.label}
              </h3>
              {latestMarking?.provenance?.version != null && (
                <span className="rounded bg-white/70 px-2 py-0.5 text-xs font-medium">
                  Key version {latestMarking.provenance.version}
                </span>
              )}
            </div>
            <p className="mt-1 text-sm opacity-80">
              {backendMessage || content.description}
            </p>
          </div>
        </div>

        <Button
          type="button"
          variant="secondary"
          onClick={onMark}
          loading={Boolean(markingRequest?.loading)}
          disabled={isProcessing || Boolean(markingRequest?.loading)}
          className="w-full flex-none bg-white sm:w-auto"
        >
          {!markingRequest?.loading && <RefreshCw className="h-4 w-4" aria-hidden="true" />}
          {markingRequest?.loading ? 'Marking…' : actionLabel}
        </Button>
      </div>

      {requestError && (
        <p className="mt-3 border-t border-current/10 pt-3 text-sm font-medium" role="alert">
          {requestError}
        </p>
      )}
    </section>
  );
};

export default MarkingStatusBanner;
