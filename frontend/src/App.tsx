import { useState, useEffect, useCallback, useRef } from 'react';
import type { CandidateDTO, PipelineStatus, StrategyOption } from './api';
import {
  startPipeline,
  getPipelineStatus,
  reviewCandidate,
  regenerateCandidate,
  selectStrategies,
  getStrategies,
  subscribeToPipeline,
} from './api';
import ServiceControls from './components/ServiceControls';
import PipelineProgress from './components/PipelineProgress';
import VideoReview from './components/VideoReview';
import StrategyPicker from './components/StrategyPicker';
import SubtitleManager from './components/SubtitleManager';
import SkillEvolution from './components/SkillEvolution';
import './App.css';

type Step = PipelineStatus['step'];
type Page = 'pipeline' | 'subtitles' | 'evolution';

const SESSION_KEY = 'yt-transport-session';

function App() {
  const [page, setPage] = useState<Page>('pipeline');
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [step, setStep] = useState<Step>('idle');
  const [candidates, setCandidates] = useState<CandidateDTO[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [summary, setSummary] = useState<Record<string, unknown> | null>(null);
  const [strategies, setStrategies] = useState<StrategyOption[]>([]);
  const esRef = useRef<EventSource | null>(null);

  // Persist sessionId to localStorage
  useEffect(() => {
    if (sessionId) {
      localStorage.setItem(SESSION_KEY, sessionId);
    }
  }, [sessionId]);

  // On mount, try to restore session from localStorage
  useEffect(() => {
    const saved = localStorage.getItem(SESSION_KEY);
    if (!saved) return;

    getPipelineStatus(saved)
      .then((status) => {
        setSessionId(saved);
        setStep(status.step);
        setCandidates(status.candidates);
        setError(status.error);
        setSummary(status.summary);
      })
      .catch(() => {
        // Session no longer exists (404), clear it
        localStorage.removeItem(SESSION_KEY);
      });
  }, []);

  // SSE connection: connect when sessionId is set and pipeline is active
  useEffect(() => {
    if (!sessionId) return;
    if (step === 'idle' || step === 'done' || step === 'error') return;

    const es = subscribeToPipeline(sessionId, (data) => {
      setStep(data.step);
      setCandidates(data.candidates);
      setError(data.error);
      setSummary(data.summary);

      if (data.step === 'strategy_generation') {
        getStrategies(sessionId).then(setStrategies).catch(() => {});
      }

      if (data.step === 'done' || data.step === 'error') {
        es.close();
      }
    });

    es.onerror = () => {
      // Connection lost — will auto-reconnect via browser EventSource
    };

    esRef.current = es;

    return () => {
      es.close();
      esRef.current = null;
    };
  }, [sessionId, step]);

  const handleStart = useCallback(async (backend: string, dryRun: boolean) => {
    setError(null);
    setSummary(null);
    setCandidates([]);
    setStrategies([]);
    try {
      const result = await startPipeline({ backend, dry_run: dryRun });
      setSessionId(result.session_id);
      setStep('strategy_generation');
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  const handleApprove = useCallback(async (id: string) => {
    if (!sessionId) return;
    await reviewCandidate(sessionId, id, true);
    setCandidates(prev => prev.map(c => c.id === id ? { ...c, approved: true } : c));
  }, [sessionId]);

  const handleReject = useCallback(async (id: string) => {
    if (!sessionId) return;
    await reviewCandidate(sessionId, id, false);
    setCandidates(prev => prev.map(c => c.id === id ? { ...c, approved: false } : c));
  }, [sessionId]);

  const handleRegenerate = useCallback(async (id: string, feedback: string) => {
    if (!sessionId) return;
    const updated = await regenerateCandidate(sessionId, id, feedback);
    setCandidates(prev => prev.map(c => c.id === id ? updated : c));
  }, [sessionId]);

  const handleSelectStrategies = useCallback(async (names: string[]) => {
    if (!sessionId) return;
    await selectStrategies(sessionId, names);
    setStrategies([]);
  }, [sessionId]);

  const isRunning = step !== 'idle' && step !== 'done' && step !== 'error';

  return (
    <div className="app">
      <header>
        <h1>YT Transport</h1>
        <nav className="nav-tabs">
          <button className={page === 'pipeline' ? 'tab active' : 'tab'}
                  onClick={() => setPage('pipeline')}>Pipeline</button>
          <button className={page === 'subtitles' ? 'tab active' : 'tab'}
                  onClick={() => setPage('subtitles')}>字幕管理</button>
          <button className={page === 'evolution' ? 'tab active' : 'tab'}
                  onClick={() => setPage('evolution')}>AI进化</button>
        </nav>
      </header>

      {page === 'evolution' ? (
        <main><SkillEvolution /></main>
      ) : page === 'subtitles' ? (
        <main><SubtitleManager /></main>
      ) : (
        <main>
          <ServiceControls onPipelineStart={handleStart} disabled={isRunning} />

          {error && <div className="error-banner">{error}</div>}

          <PipelineProgress currentStep={step} />

          {step === 'strategy_generation' && strategies.length > 0 && (
            <StrategyPicker strategies={strategies}
                            onConfirm={handleSelectStrategies} />
          )}

          {step === 'review' && (
            <VideoReview
              candidates={candidates}
              onApprove={handleApprove}
              onReject={handleReject}
              onRegenerate={handleRegenerate}
            />
          )}

          {summary && (
            <div className="summary">
              <h2>Run Complete</h2>
              <ul>
                {Object.entries(summary).map(([k, v]) => (
                  <li key={k}><strong>{k}:</strong> {String(v)}</li>
                ))}
              </ul>
            </div>
          )}
        </main>
      )}
    </div>
  );
}

export default App;
