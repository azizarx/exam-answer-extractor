import { useEffect, useMemo, useRef, useState } from 'react';
import {
  AlertTriangle,
  Check,
  CircleSlash2,
  Download,
  Globe,
  Hash,
  HelpCircle,
  X,
} from 'lucide-react';
import examAPI from '../../services/api';

const OUTCOME_STYLES = {
  correct: {
    label: 'Correct',
    icon: Check,
    classes: 'border-emerald-200 bg-emerald-50 text-emerald-900',
  },
  incorrect: {
    label: 'Incorrect',
    icon: X,
    classes: 'border-red-200 bg-red-50 text-red-900',
  },
  blank: {
    label: 'Blank',
    icon: CircleSlash2,
    classes: 'border-slate-200 bg-slate-50 text-slate-700',
  },
  invalid: {
    label: 'Invalid',
    icon: AlertTriangle,
    classes: 'border-amber-200 bg-amber-50 text-amber-900',
  },
  needs_review: {
    label: 'Needs review',
    icon: HelpCircle,
    classes: 'border-amber-200 bg-amber-50 text-amber-900',
  },
};

const displayValue = (value) => {
  if (value === null || value === undefined || value === '') return '—';
  return String(value);
};

const displayMarks = (value) => {
  if (value === null || value === undefined || value === '') return '—';
  const number = Number(value);
  return Number.isFinite(number) ? number.toLocaleString() : '—';
};

const sortQuestions = ([a], [b]) => {
  const first = Number(a);
  const second = Number(b);
  if (Number.isFinite(first) && Number.isFinite(second)) return first - second;
  return String(a).localeCompare(String(b));
};

const CandidateDetailModal = ({
  selection,
  onClose,
  onExport,
  submissionId,
  onReviewConfirmed,
}) => {
  const dialogRef = useRef(null);
  const closeButtonRef = useRef(null);
  const { candidate, index } = selection;
  const marking = candidate.marking;
  const outcomes = Array.isArray(marking?.outcomes) ? marking.outcomes : [];
  const reviewQs = useMemo(
    () => (candidate.extra_fields?.needs_review_questions || []).map(String),
    [candidate.extra_fields],
  );
  const [drafts, setDrafts] = useState(() => {
    const init = {};
    for (const q of reviewQs) {
      init[q] = candidate.answers?.[q] ?? '';
    }
    return init;
  });
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState('');

  useEffect(() => {
    const previouslyFocused = document.activeElement;
    closeButtonRef.current?.focus();
    const handleKeyDown = (event) => {
      if (event.key === 'Escape') onClose();
      if (event.key === 'Tab') {
        const focusable = dialogRef.current?.querySelectorAll(
          'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
        );
        if (!focusable?.length) return;
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first.focus();
        }
      }
    };
    document.addEventListener('keydown', handleKeyDown);
    return () => {
      document.removeEventListener('keydown', handleKeyDown);
      previouslyFocused?.focus?.();
    };
  }, [onClose]);

  const handleConfirmReview = async () => {
    if (!submissionId || !candidate.id) return;
    setSaving(true);
    setSaveError('');
    try {
      await examAPI.confirmCandidateReview(submissionId, candidate.id, {
        answers: drafts,
      });
      onReviewConfirmed?.();
    } catch (err) {
      setSaveError(err?.response?.data?.detail || err?.message || 'Confirm failed');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/60 p-3 sm:p-6"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <section
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="candidate-detail-title"
        className="flex max-h-[92vh] w-full max-w-4xl flex-col overflow-hidden rounded-2xl bg-white shadow-2xl"
      >
        <header className="flex items-start justify-between gap-4 border-b border-slate-200 p-5 sm:p-6">
          <div className="min-w-0">
            <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">
              Candidate {index + 1}
            </p>
            <h3 id="candidate-detail-title" className="truncate text-xl font-bold text-slate-900">
              {candidate.candidate_name || 'Unknown candidate'}
            </h3>
            <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-sm text-slate-600">
              {candidate.candidate_number && (
                <span className="inline-flex items-center gap-1">
                  <Hash className="h-3.5 w-3.5" aria-hidden="true" />
                  {candidate.candidate_number}
                </span>
              )}
              {candidate.country && (
                <span className="inline-flex items-center gap-1">
                  <Globe className="h-3.5 w-3.5" aria-hidden="true" />
                  {candidate.country}
                </span>
              )}
              {candidate.paper_type && <span>Paper {candidate.paper_type}</span>}
              {candidate.extra_fields?.mcq_warning && (
                <span className="text-amber-700">MCQ: {candidate.extra_fields.mcq_warning}</span>
              )}
            </div>
          </div>
          <div className="flex flex-none items-center gap-1">
            <button
              type="button"
              onClick={() => onExport(candidate, index)}
              className="rounded-lg p-2 text-blue-700 hover:bg-blue-50 focus:outline-none focus:ring-2 focus:ring-blue-500"
              aria-label={`Download raw JSON for ${candidate.candidate_name || `candidate ${index + 1}`}`}
            >
              <Download className="h-5 w-5" aria-hidden="true" />
            </button>
            <button
              ref={closeButtonRef}
              type="button"
              onClick={onClose}
              className="rounded-lg p-2 text-slate-500 hover:bg-slate-100 hover:text-slate-800 focus:outline-none focus:ring-2 focus:ring-blue-500"
              aria-label="Close candidate details"
            >
              <X className="h-6 w-6" aria-hidden="true" />
            </button>
          </div>
        </header>

        <div className="flex-1 overflow-y-auto p-5 sm:p-6">
          {reviewQs.length > 0 && submissionId && (
            <div className="mb-6 rounded-xl border border-amber-200 bg-amber-50 p-4">
              <h4 className="font-semibold text-amber-950">
                Extraction review queue ({reviewQs.length})
              </h4>
              <p className="mt-1 text-sm text-amber-900">
                Confirm or correct these answers, then re-mark the submission.
              </p>
              <div className="mt-3 grid gap-2 sm:grid-cols-2">
                {reviewQs.map((q) => (
                  <label key={q} className="flex items-center gap-2 text-sm">
                    <span className="w-10 font-semibold text-slate-700">Q{q}</span>
                    <input
                      className="flex-1 rounded border border-amber-300 bg-white px-2 py-1 font-mono"
                      value={drafts[q] ?? ''}
                      onChange={(e) =>
                        setDrafts((prev) => ({ ...prev, [q]: e.target.value }))
                      }
                    />
                  </label>
                ))}
              </div>
              {saveError ? <p className="mt-2 text-sm text-red-700">{saveError}</p> : null}
              <button
                type="button"
                disabled={saving}
                onClick={handleConfirmReview}
                className="mt-3 rounded-lg bg-amber-800 px-3 py-1.5 text-sm font-semibold text-white hover:bg-amber-900 disabled:opacity-60"
              >
                {saving ? 'Saving…' : 'Confirm reviewed answers'}
              </button>
            </div>
          )}

          {marking ? (
            <>
              <div className="mb-6 grid grid-cols-2 gap-3 rounded-xl border border-slate-200 bg-slate-50 p-4 sm:max-w-md">
                <div>
                  <p className="text-xs font-medium uppercase tracking-wide text-slate-500">Weighted score</p>
                  <p className="mt-1 text-2xl font-bold text-slate-900">
                    {displayMarks(marking.awarded_marks)}
                    <span className="text-base font-medium text-slate-500">
                      {' '}/ {displayMarks(marking.max_marks)}
                    </span>
                  </p>
                </div>
                <div>
                  <p className="text-xs font-medium uppercase tracking-wide text-slate-500">Percentage</p>
                  <p className="mt-1 text-2xl font-bold text-slate-900">
                    {Number(marking.max_marks) > 0 && Number.isFinite(Number(marking.percentage))
                      ? `${Number(marking.percentage).toFixed(1)}%`
                      : '—'}
                  </p>
                </div>
              </div>

              <h4 className="mb-3 font-semibold text-slate-800">
                Question outcomes ({outcomes.length})
              </h4>
              {outcomes.length > 0 ? (
                <div className="overflow-hidden rounded-xl border border-slate-200">
                  <div className="hidden grid-cols-[0.65fr_1.4fr_1fr_0.9fr] gap-3 bg-slate-100 px-4 py-2 text-xs font-semibold uppercase tracking-wide text-slate-600 sm:grid">
                    <span>Question</span>
                    <span>Response</span>
                    <span>Outcome</span>
                    <span className="text-right">Marks</span>
                  </div>
                  <ul className="divide-y divide-slate-200">
                    {outcomes.map((outcome, outcomeIndex) => {
                      const style = OUTCOME_STYLES[outcome?.status] || {
                        label: 'Unknown',
                        icon: HelpCircle,
                        classes: 'border-slate-200 bg-slate-50 text-slate-700',
                      };
                      const OutcomeIcon = style.icon;
                      return (
                        <li
                          key={`${outcome?.question_number ?? 'unknown'}-${outcomeIndex}`}
                          className="grid gap-3 px-4 py-3 text-sm sm:grid-cols-[0.65fr_1.4fr_1fr_0.9fr] sm:items-center"
                          aria-label={`Question ${displayValue(outcome?.question_number)}: ${style.label}, ${displayMarks(outcome?.awarded_marks)} of ${displayMarks(outcome?.max_marks)} marks`}
                        >
                          <div>
                            <span className="mr-2 text-xs font-medium text-slate-500 sm:hidden">Question</span>
                            <span className="font-semibold text-slate-900">Q{displayValue(outcome?.question_number)}</span>
                          </div>
                          <div className="min-w-0">
                            <span className="mr-2 text-xs font-medium text-slate-500 sm:hidden">Response</span>
                            <span className="break-words font-mono text-slate-800">{displayValue(outcome?.response)}</span>
                          </div>
                          <div>
                            <span className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-semibold ${style.classes}`}>
                              <OutcomeIcon className="h-3.5 w-3.5" aria-hidden="true" />
                              {style.label}
                            </span>
                            {outcome?.judge_reason ? (
                              <p className="mt-1 text-xs text-slate-600">{outcome.judge_reason}</p>
                            ) : null}
                          </div>
                          <div className="font-semibold text-slate-800 sm:text-right">
                            <span className="mr-2 text-xs font-medium text-slate-500 sm:hidden">Marks</span>
                            {displayMarks(outcome?.awarded_marks)} / {displayMarks(outcome?.max_marks)}
                          </div>
                        </li>
                      );
                    })}
                  </ul>
                </div>
              ) : (
                <p className="rounded-lg bg-slate-50 p-4 text-sm text-slate-600">
                  No per-question outcomes are available for this candidate.
                </p>
              )}
            </>
          ) : (
            <div className="mb-6 rounded-xl border border-slate-200 bg-slate-50 p-4">
              <p className="font-semibold text-slate-800">No marking data</p>
              <p className="mt-1 text-sm text-slate-600">
                Extracted responses remain available below.
              </p>
            </div>
          )}

          {!marking && Object.keys(candidate.answers || {}).length > 0 && (
            <div className="mt-6">
              <h4 className="mb-3 font-semibold text-slate-800">Extracted responses</h4>
              <div className="grid grid-cols-3 gap-2 sm:grid-cols-6 md:grid-cols-8">
                {Object.entries(candidate.answers).sort(sortQuestions).map(([question, answer]) => (
                  <div
                    key={question}
                    className={`rounded-lg border p-2 text-center ${
                      reviewQs.includes(String(question))
                        ? 'border-amber-300 bg-amber-50'
                        : 'border-slate-200 bg-slate-50'
                    }`}
                  >
                    <p className="text-[11px] font-medium text-slate-500">Q{question}</p>
                    <p className="break-words text-sm font-bold text-slate-900">{displayValue(answer)}</p>
                  </div>
                ))}
              </div>
            </div>
          )}

          {Object.keys(candidate.drawing_questions || {}).length > 0 && (
            <div className="mt-6">
              <h4 className="mb-3 font-semibold text-slate-800">Drawing / free response</h4>
              <div className="space-y-3">
                {Object.entries(candidate.drawing_questions).sort(sortQuestions).map(([question, answer]) => (
                  <div key={question} className="rounded-lg border border-slate-200 bg-slate-50 p-3">
                    <p className="text-xs font-semibold text-slate-500">Question {question}</p>
                    <p className="mt-1 whitespace-pre-wrap break-words text-sm text-slate-800">
                      {displayValue(answer)}
                    </p>
                  </div>
                ))}
              </div>
            </div>
          )}

          {!marking &&
            Object.keys(candidate.answers || {}).length === 0 &&
            Object.keys(candidate.drawing_questions || {}).length === 0 && (
              <p className="py-8 text-center text-slate-500">No responses were extracted.</p>
            )}
        </div>
      </section>
    </div>
  );
};

export default CandidateDetailModal;
