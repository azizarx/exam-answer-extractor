import { useEffect, useState } from 'react';
import examAPI from '../../services/api';

/**
 * The two images a diagram mark was decided from, side by side.
 *
 * A diagram question is marked by comparing the candidate's drawing with the
 * answer key's own drawing, so showing both is what makes the mark checkable
 * without opening the full page.
 */
const DiagramComparison = ({ submissionId, candidateId, templateId, question }) => {
  const [studentURL, setStudentURL] = useState('');
  const [referenceURL, setReferenceURL] = useState('');
  const [unavailable, setUnavailable] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const created = [];

    const load = async () => {
      const [student, reference] = await Promise.all([
        examAPI.getDiagramCropURL(submissionId, candidateId, question).catch(() => ''),
        templateId
          ? examAPI.getReferenceDiagramURL(templateId, question).catch(() => '')
          : Promise.resolve(''),
      ]);
      if (cancelled) {
        [student, reference].forEach((url) => url && URL.revokeObjectURL(url));
        return;
      }
      if (student) created.push(student);
      if (reference) created.push(reference);
      setStudentURL(student);
      setReferenceURL(reference);
      setUnavailable(!student && !reference);
    };

    load();
    return () => {
      cancelled = true;
      created.forEach((url) => URL.revokeObjectURL(url));
    };
  }, [submissionId, candidateId, templateId, question]);

  if (unavailable) {
    return (
      <p className="mt-2 text-xs italic text-slate-500">
        No diagram images stored for this candidate.
      </p>
    );
  }

  return (
    <div className="mt-1 flex flex-wrap items-start gap-4 rounded-lg bg-slate-50 p-3">
      {[
        { label: 'Candidate', url: studentURL },
        { label: 'Answer key', url: referenceURL },
      ].map(({ label, url }) => (
        <figure key={label} className="m-0">
          <figcaption className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-slate-500">
            {label}
          </figcaption>
          {url ? (
            <img
              src={url}
              alt={`${label} drawing for question ${question}`}
              className="h-32 w-auto rounded border border-slate-200 bg-white object-contain"
            />
          ) : (
            <div className="flex h-28 w-28 items-center justify-center rounded border border-dashed border-slate-300 text-[11px] text-slate-400">
              none
            </div>
          )}
        </figure>
      ))}
    </div>
  );
};

export default DiagramComparison;
