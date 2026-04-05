import { useState } from 'react';
import type { CandidateDTO } from '../api';

interface Props {
  candidates: CandidateDTO[];
  onApprove: (id: string) => void;
  onReject: (id: string) => void;
  onRegenerate: (id: string, feedback: string) => void;
}

export default function VideoReview({ candidates, onApprove, onReject, onRegenerate }: Props) {
  if (candidates.length === 0) {
    return <p className="empty">No candidates to review.</p>;
  }

  return (
    <div className="video-review">
      <h2>Review Candidates ({candidates.filter(c => c.approved === null).length} pending)</h2>
      <div className="card-list">
        {candidates.map(v => (
          <CandidateCard
            key={v.id}
            candidate={v}
            onApprove={() => onApprove(v.id)}
            onReject={() => onReject(v.id)}
            onRegenerate={(fb) => onRegenerate(v.id, fb)}
          />
        ))}
      </div>
    </div>
  );
}

function CandidateCard({ candidate: v, onApprove, onReject, onRegenerate }: {
  candidate: CandidateDTO;
  onApprove: () => void;
  onReject: () => void;
  onRegenerate: (feedback: string) => void;
}) {
  const [showFeedback, setShowFeedback] = useState(false);
  const [feedback, setFeedback] = useState('');
  const [regenerating, setRegenerating] = useState(false);

  const handleRegenerate = async () => {
    if (!feedback.trim()) return;
    setRegenerating(true);
    try {
      await onRegenerate(feedback);
      setFeedback('');
      setShowFeedback(false);
    } finally {
      setRegenerating(false);
    }
  };

  const mins = Math.floor(v.duration_seconds / 60);
  const secs = v.duration_seconds % 60;

  return (
    <div className={`card ${v.approved === true ? 'approved' : ''} ${v.approved === false ? 'rejected' : ''}`}>
      <div className="card-header">
        <span className="strategy-tag">{v.strategy}</span>
        <span className="score">
          Score: {v.score.toFixed(2)} | Tsundere: {v.tsundere_score}/10
        </span>
      </div>
      <h3>{v.title}</h3>
      <p className="meta">{v.channel} · {v.views.toLocaleString()} views · {mins}:{secs.toString().padStart(2, '0')}</p>
      <p className="description">{v.description}</p>

      <div className="card-actions">
        <button className="btn-approve" onClick={onApprove} disabled={v.approved === true}>
          Approve
        </button>
        <button className="btn-reject" onClick={onReject} disabled={v.approved === false}>
          Reject
        </button>
        <button onClick={() => setShowFeedback(!showFeedback)} disabled={v.approved !== null}>
          Revise
        </button>
      </div>

      {showFeedback && (
        <div className="feedback-row">
          <input
            type="text"
            placeholder="Revision feedback..."
            value={feedback}
            onChange={e => setFeedback(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && handleRegenerate()}
          />
          <button onClick={handleRegenerate} disabled={regenerating || !feedback.trim()}>
            {regenerating ? 'Regenerating...' : 'Send'}
          </button>
        </div>
      )}
    </div>
  );
}
