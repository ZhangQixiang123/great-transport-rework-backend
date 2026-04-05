const STEPS = [
  { key: 'strategy_generation', label: 'Strategy Generation' },
  { key: 'search', label: 'Video Search' },
  { key: 'scoring', label: 'Scoring' },
  { key: 'copy_generation', label: 'Copy Generation' },
  { key: 'review', label: 'Review' },
  { key: 'uploading', label: 'Uploading' },
  { key: 'done', label: 'Done' },
] as const;

export default function PipelineProgress({ currentStep }: { currentStep: string }) {
  if (currentStep === 'idle') return null;

  const currentIndex = STEPS.findIndex(s => s.key === currentStep);

  return (
    <div className="pipeline-progress">
      {STEPS.map((step, i) => (
        <div
          key={step.key}
          className={`step ${i < currentIndex ? 'completed' : ''} ${i === currentIndex ? 'active' : ''}`}
        >
          <div className="step-dot">{i < currentIndex ? '\u2713' : i + 1}</div>
          <span>{step.label}</span>
        </div>
      ))}
    </div>
  );
}
