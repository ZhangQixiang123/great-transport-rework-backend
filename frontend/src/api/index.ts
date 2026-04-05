const API_BASE = '/api';

// ── Health check ─────────────────────────────────────────

export async function checkHealth(): Promise<boolean> {
  try {
    const res = await fetch(`${API_BASE}/uploads?limit=1`);
    return res.ok;
  } catch {
    return false;
  }
}

// ── Pipeline ─────────────────────────────────────────────

export interface StartOptions {
  backend: string;
  persona?: string;
  dry_run?: boolean;
  no_upload?: boolean;
  quota_budget?: number;
}

export async function startPipeline(opts: StartOptions): Promise<{ session_id: string }> {
  const res = await fetch(`${API_BASE}/pipeline/start`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(opts),
  });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`Start failed (${res.status}): ${body}`);
  }
  return res.json();
}

export interface CandidateDTO {
  id: string;
  video_id: string;
  title: string;
  title_zh: string;
  description: string;
  channel: string;
  strategy: string;
  score: number;
  tsundere_score: number;
  views: number;
  duration_seconds: number;
  approved: boolean | null;
}

export interface PipelineStatus {
  step: string;
  candidates: CandidateDTO[];
  error: string | null;
  summary: Record<string, unknown> | null;
}

export async function getPipelineStatus(sessionId: string): Promise<PipelineStatus> {
  const res = await fetch(`${API_BASE}/pipeline/${sessionId}/status`);
  if (!res.ok) throw new Error(`Status error: ${res.status}`);
  return res.json();
}

export function subscribeToPipeline(
  sessionId: string,
  onUpdate: (status: PipelineStatus & { session_id: string }) => void,
): EventSource {
  const es = new EventSource(`${API_BASE}/pipeline/${sessionId}/events`);
  es.onmessage = (event) => {
    const data = JSON.parse(event.data);
    onUpdate(data);
  };
  return es;
}

export async function reviewCandidate(sessionId: string, id: string, approved: boolean, feedback?: string) {
  const res = await fetch(`${API_BASE}/pipeline/${sessionId}/review/${id}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ approved, feedback }),
  });
  if (!res.ok) throw new Error(`Review error: ${res.status}`);
  return res.json();
}

export async function regenerateCandidate(sessionId: string, id: string, feedback: string): Promise<CandidateDTO> {
  const res = await fetch(`${API_BASE}/pipeline/${sessionId}/regenerate/${id}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ approved: false, feedback }),
  });
  if (!res.ok) throw new Error(`Regenerate error: ${res.status}`);
  return res.json();
}

export interface StrategyOption {
  name: string;
  query_count: number;
}

export async function getStrategies(sessionId: string): Promise<StrategyOption[]> {
  const res = await fetch(`${API_BASE}/pipeline/${sessionId}/strategies`);
  if (!res.ok) throw new Error(`Strategies error: ${res.status}`);
  return res.json();
}

export async function selectStrategies(sessionId: string, names: string[]) {
  const res = await fetch(`${API_BASE}/pipeline/${sessionId}/select-strategies`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ names }),
  });
  if (!res.ok) throw new Error(`Selected error: ${res.status}`);
  return res.json();
}

// ── Sessions ─────────────────────────────────────────────

export async function listSessions(): Promise<{ session_id: string; step: string; created_at: string }[]> {
  const res = await fetch(`${API_BASE}/sessions`);
  if (!res.ok) throw new Error(`Sessions error: ${res.status}`);
  return res.json();
}

export async function deleteSession(sessionId: string) {
  const res = await fetch(`${API_BASE}/pipeline/${sessionId}`, { method: 'DELETE' });
  if (!res.ok) throw new Error(`Delete session error: ${res.status}`);
  return res.json();
}

// ── Skill Evolution ──────────────────────────────────────

export interface SkillInfo {
  name: string;
  version: number;
  system_prompt: string;
  prompt_template: string;
}

export interface SkillVersion {
  version: number;
  system_prompt: string;
  prompt_template: string;
  changed_by: string;
  change_reason: string;
  created_at: string;
}

export async function listSkills(): Promise<SkillInfo[]> {
  const res = await fetch(`${API_BASE}/skills`);
  if (!res.ok) throw new Error(`Skills error: ${res.status}`);
  return res.json();
}

export async function getSkillVersions(name: string): Promise<SkillVersion[]> {
  const res = await fetch(`${API_BASE}/skills/${name}/versions`);
  if (!res.ok) throw new Error(`Versions error: ${res.status}`);
  return res.json();
}

export async function triggerReflection(skillName: string, type: string) {
  const res = await fetch(`${API_BASE}/skills/reflect`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ skill_name: skillName, reflection_type: type }),
  });
  if (!res.ok) throw new Error(`Reflect error: ${res.status}`);
  return res.json();
}

export async function rollbackSkill(name: string, targetVersion: number) {
  const res = await fetch(`${API_BASE}/skills/${name}/rollback`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ target_version: targetVersion }),
  });
  if (!res.ok) throw new Error(`Rollback error: ${res.status}`);
  return res.json();
}

export async function applyReflection(skillName: string, systemPrompt: string, reason: string) {
  const res = await fetch(`${API_BASE}/skills/${skillName}/apply-reflection`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ system_prompt: systemPrompt, reason }),
  });
  if (!res.ok) throw new Error(`Apply error: ${res.status}`);
  return res.json();
}

export async function updateSkill(name: string, systemPrompt?: string, promptTemplate?: string) {
  const res = await fetch(`${API_BASE}/skills/${name}/update`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ system_prompt: systemPrompt, prompt_template: promptTemplate }),
  });
  if (!res.ok) throw new Error(`Update error: ${res.status}`);
  return res.json();
}

// ── Loop 2 / Review Stats ────────────────────────────────

export async function collectLoop2() {
  const res = await fetch(`${API_BASE}/loop2/collect`, {
    method: 'POST',
  });
  if (!res.ok) throw new Error(`Collect error: ${res.status}`);
  return res.json();
}

export async function getLoop2Stats() {
  const res = await fetch(`${API_BASE}/loop2/stats`);
  if (!res.ok) throw new Error(`Stats error: ${res.status}`);
  return res.json();
}

export interface ReviewStat {
  strategy_name: string;
  total: number;
  approved: number;
  rejected: number;
  approval_rate: number;
}

export async function getReviewStats(): Promise<ReviewStat[]> {
  const res = await fetch(`${API_BASE}/review-stats`);
  if (!res.ok) throw new Error(`Review stats error: ${res.status}`);
  return res.json();
}

export async function submitAnnotationFeedback(feedback: {
  video_title: string;
  kept: { time: number; comment: string }[];
  deleted: { time: number; comment: string }[];
  edited: { original: string; edited: string }[];
}) {
  const res = await fetch(`${API_BASE}/annotation/feedback`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(feedback),
  });
  if (!res.ok) throw new Error(`Feedback error: ${res.status}`);
  return res.json();
}

// ── Upload / Subtitle Management (via Python proxy) ──────

export interface UploadJob {
  job_id: number;
  video_id: string;
  status: string;
  title: string;
  bilibili_bvid: string;
  download_files: string;
  subtitle_status: string;
  error_message: string;
  persona_id: string;
  strategy_name: string;
  created_at: string;
  updated_at: string;
}

export async function getUploadJobs(limit = 50): Promise<UploadJob[]> {
  const res = await fetch(`${API_BASE}/uploads?limit=${limit}`);
  if (!res.ok) throw new Error(`Jobs error: ${res.status}`);
  return res.json();
}

export async function retrySubtitle(jobId: number) {
  const res = await fetch(`${API_BASE}/uploads/${jobId}/retry-subtitle`, { method: 'POST' });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`Retry failed: ${body}`);
  }
  return res.json();
}

export async function getSubtitlePreview(jobId: number) {
  const res = await fetch(`${API_BASE}/uploads/${jobId}/subtitle-preview`);
  if (!res.ok) {
    const body = await res.text();
    throw new Error(body);
  }
  return res.json();
}

export async function generateAnnotations(jobId: number) {
  const res = await fetch(`${API_BASE}/uploads/${jobId}/annotate`, { method: 'POST' });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`Annotate failed: ${body}`);
  }
  return res.json();
}

export async function deleteUploadJob(jobId: number) {
  const res = await fetch(`${API_BASE}/uploads/${jobId}`, { method: 'DELETE' });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`Delete failed: ${body}`);
  }
  return res.json();
}

export async function approveSubtitle(jobId: number) {
  const res = await fetch(`${API_BASE}/uploads/${jobId}/approve-subtitle`, { method: 'POST' });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`Approve failed: ${body}`);
  }
  return res.json();
}
