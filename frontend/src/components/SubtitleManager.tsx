import { useState, useEffect, useCallback } from 'react';
import type { UploadJob } from '../api';
import { getUploadJobs, retrySubtitle, getSubtitlePreview, approveSubtitle, generateAnnotations, deleteUploadJob } from '../api';

interface SubtitlePreview {
  english_srt: string;
  chinese_srt: string;
  annotations: { from: number; to: number; content: string }[];
}

export default function SubtitleManager() {
  const [jobs, setJobs] = useState<UploadJob[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [preview, setPreview] = useState<{ jobId: number; data: SubtitlePreview } | null>(null);
  const [actionLoading, setActionLoading] = useState<number | null>(null);
  const [annotating, setAnnotating] = useState<Set<number>>(new Set());

  const refresh = useCallback(async () => {
    try {
      const data = await getUploadJobs(50);
      // Only show completed uploads that have a bvid
      setJobs(data.filter(j => j.status === 'completed' && j.bilibili_bvid));
      setError(null);

      // Check if any annotating jobs have finished by peeking at their preview
      setAnnotating(prev => {
        if (prev.size === 0) return prev;
        const still = new Set(prev);
        for (const id of prev) {
          getSubtitlePreview(id)
            .then(p => {
              if (p.annotations && p.annotations.length > 0) {
                setAnnotating(s => { const n = new Set(s); n.delete(id); return n; });
              }
            })
            .catch(() => {});
        }
        return still;
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, 5000);
    return () => clearInterval(timer);
  }, [refresh]);

  const handleRetry = async (jobId: number) => {
    setActionLoading(jobId);
    setError(null);
    try {
      await retrySubtitle(jobId);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setActionLoading(null);
    }
  };

  const handlePreview = async (jobId: number) => {
    setActionLoading(jobId);
    setError(null);
    try {
      const data = await getSubtitlePreview(jobId);
      setPreview({ jobId, data });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setActionLoading(null);
    }
  };

  const handleApprove = async (jobId: number) => {
    setActionLoading(jobId);
    setError(null);
    try {
      await approveSubtitle(jobId);
      setPreview(null);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setActionLoading(null);
    }
  };

  const handleAnnotate = async (jobId: number) => {
    setError(null);
    setAnnotating(prev => new Set(prev).add(jobId));
    try {
      await generateAnnotations(jobId);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setAnnotating(prev => { const s = new Set(prev); s.delete(jobId); return s; });
    }
    // stay in annotating state — polling refresh will clear it when preview has annotations
  };

  const handleBatchAnnotate = async () => {
    const eligible = jobs.filter(j => j.subtitle_status === 'review');
    if (eligible.length === 0) return;
    setError(null);
    const ids = eligible.map(j => j.job_id);
    setAnnotating(prev => { const s = new Set(prev); ids.forEach(id => s.add(id)); return s; });
    for (const id of ids) {
      try {
        await generateAnnotations(id);
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
        break;
      }
    }
  };

  const handleDelete = async (jobId: number) => {
    if (!confirm('确定删除这条记录？')) return;
    setError(null);
    try {
      await deleteUploadJob(jobId);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const statusLabel = (s: string) => {
    const map: Record<string, { text: string; cls: string }> = {
      pending: { text: '待生成', cls: 'sub-pending' },
      generating: { text: '生成中...', cls: 'sub-generating' },
      review: { text: '待审核', cls: 'sub-review' },
      completed: { text: '已发布', cls: 'sub-completed' },
      failed: { text: '失败', cls: 'sub-failed' },
    };
    return map[s] || { text: s, cls: '' };
  };

  if (loading) return <div className="empty">Loading...</div>;

  return (
    <div className="subtitle-manager">
      <div className="sub-header">
        <h2>字幕管理</h2>
        <div style={{ display: 'flex', gap: '0.5rem' }}>
          {jobs.some(j => j.subtitle_status === 'review') && (
            <button onClick={handleBatchAnnotate}>批量生成弹幕</button>
          )}
          <button onClick={refresh} className="btn-refresh">刷新</button>
        </div>
      </div>

      {error && <div className="error-banner">{error}</div>}

      {jobs.length === 0 ? (
        <div className="empty">没有已上传的视频</div>
      ) : (
        <div className="sub-list">
          {jobs.map(job => {
            const st = statusLabel(job.subtitle_status);
            const isLoading = actionLoading === job.job_id;
            const isAnnotating = annotating.has(job.job_id);
            return (
              <div key={job.job_id} className="sub-card">
                {isAnnotating && (
                  <div className="annotating-banner">弹幕生成中，请等待...</div>
                )}
                <div className="sub-card-top">
                  <div className="sub-info">
                    <span className="sub-title">{job.title || job.video_id}</span>
                    <span className="sub-meta">
                      <a href={`https://www.bilibili.com/video/${job.bilibili_bvid}`}
                         target="_blank" rel="noopener noreferrer">
                        {job.bilibili_bvid}
                      </a>
                      {' · #' + job.job_id}
                      {' · '}
                      <button className="btn-link btn-delete" onClick={() => handleDelete(job.job_id)}>删除</button>
                    </span>
                  </div>
                  <div className="sub-right">
                    <span className={`sub-badge ${st.cls}`}>{st.text}</span>
                    <div className="sub-actions">
                      {(job.subtitle_status === 'pending' || job.subtitle_status === 'failed') && (
                        <>
                          <button onClick={() => handleRetry(job.job_id)}
                                  disabled={isLoading}>
                            {isLoading ? '...' : '生成字幕'}
                          </button>
                        </>
                      )}
                      {job.subtitle_status === 'review' && (
                        <>
                          <button onClick={() => handleAnnotate(job.job_id)}
                                  disabled={isLoading || isAnnotating}>
                            {isAnnotating ? '生成中...' : '生成弹幕'}
                          </button>
                          <button onClick={() => handlePreview(job.job_id)}
                                  disabled={isLoading}>
                            预览
                          </button>
                          <button className="btn-approve"
                                  onClick={() => handleApprove(job.job_id)}
                                  disabled={isLoading}>
                            {isLoading ? '...' : '发布'}
                          </button>
                        </>
                      )}
                      {job.subtitle_status === 'generating' && (
                        <span className="sub-spin">处理中</span>
                      )}
                    </div>
                  </div>
                </div>
                {job.subtitle_status === 'failed' && job.error_message && (
                  <div className="sub-error-detail">{job.error_message}</div>
                )}

                {/* Preview panel */}
                {preview?.jobId === job.job_id && (
                  <div className="sub-preview">
                    <div className="sub-preview-header">
                      <h3>字幕预览</h3>
                      <button onClick={() => setPreview(null)} className="btn-link">关闭</button>
                    </div>
                    {(preview.data.annotations?.length ?? 0) > 0 && (
                      <div className="sub-section">
                        <h4>弹幕 ({preview.data.annotations.length})</h4>
                        <div className="sub-annotations">
                          {preview.data.annotations.map((a, i) => (
                            <div key={i} className="annotation-item">
                              <span className="ann-time">{formatTime(a.from)}</span>
                              <span className="ann-text">{a.content}</span>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}
                    <div className="sub-section">
                      <h4>中文字幕</h4>
                      <pre className="srt-block">{preview.data.chinese_srt.slice(0, 1500)}</pre>
                    </div>
                    <div className="sub-section">
                      <h4>英文字幕</h4>
                      <pre className="srt-block">{preview.data.english_srt.slice(0, 1500)}</pre>
                    </div>
                    <div className="sub-preview-actions">
                      <button className="btn-approve" onClick={() => handleApprove(job.job_id)}
                              disabled={actionLoading === job.job_id}>
                        确认发布字幕+弹幕
                      </button>
                    </div>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

function formatTime(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${s.toString().padStart(2, '0')}`;
}
