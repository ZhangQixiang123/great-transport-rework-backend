import { useState, useEffect, useCallback } from 'react';
import type { SkillInfo, SkillVersion, ReviewStat } from '../api';
import {
  listSkills, getSkillVersions, triggerReflection,
  rollbackSkill, updateSkill, collectLoop2, getLoop2Stats,
  getReviewStats, applyReflection,
} from '../api';

export default function SkillEvolution() {
  const [skills, setSkills] = useState<SkillInfo[]>([]);
  const [versions, setVersions] = useState<Record<string, SkillVersion[]>>({});
  const [expanded, setExpanded] = useState<string | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [editPrompt, setEditPrompt] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState<string | null>(null);
  const [reflectionResult, setReflectionResult] = useState<any>(null);
  const [reflectEditPrompt, setReflectEditPrompt] = useState('');
  const [editingReflection, setEditingReflection] = useState(false);
  const [loop2Stats, setLoop2Stats] = useState<any[]>([]);
  const [loop2Loading, setLoop2Loading] = useState(false);
  const [reviewStats, setReviewStats] = useState<ReviewStat[]>([]);

  const refresh = useCallback(async () => {
    try {
      const data = await listSkills();
      setSkills(data);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  const loadVersions = async (name: string) => {
    if (expanded === name) {
      setExpanded(null);
      return;
    }
    try {
      const v = await getSkillVersions(name);
      setVersions(prev => ({ ...prev, [name]: v }));
      setExpanded(name);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const handleReflect = async (name: string, type: string) => {
    setLoading(`reflect-${name}`);
    setError(null);
    setReflectionResult(null);
    setEditingReflection(false);
    try {
      const result = await triggerReflection(name, type);
      setReflectionResult(result);
      // Pre-fill edit prompt with proposed prompt
      const proposed = result?.result?.proposed_system_prompt;
      if (proposed) setReflectEditPrompt(proposed);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(null);
    }
  };

  const handleApproveReflection = async () => {
    if (!reflectionResult) return;
    const skillName = reflectionResult.skill_name;
    const prompt = editingReflection ? reflectEditPrompt : reflectionResult.result?.proposed_system_prompt;
    const reason = reflectionResult.result?.analysis || 'Approved reflection';
    if (!prompt || !skillName) return;
    setError(null);
    try {
      await applyReflection(skillName, prompt, typeof reason === 'string' ? reason : JSON.stringify(reason));
      setReflectionResult(null);
      setEditingReflection(false);
      await refresh();
      await loadReviewStats();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const handleDiscardReflection = () => {
    setReflectionResult(null);
    setEditingReflection(false);
  };

  const handleRollback = async (name: string, version: number) => {
    if (!confirm(`回滚到版本 ${version}？`)) return;
    setError(null);
    try {
      await rollbackSkill(name, version);
      await refresh();
      const v = await getSkillVersions(name);
      setVersions(prev => ({ ...prev, [name]: v }));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const handleEdit = (skill: SkillInfo) => {
    setEditing(skill.name);
    setEditPrompt(skill.system_prompt);
  };

  const handleSaveEdit = async (name: string) => {
    setError(null);
    try {
      await updateSkill(name, editPrompt);
      setEditing(null);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const handleCollectLoop2 = async () => {
    setLoop2Loading(true);
    setError(null);
    try {
      const result = await collectLoop2();
      setReflectionResult(result);
      await loadLoop2Stats();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoop2Loading(false);
    }
  };

  const loadLoop2Stats = async () => {
    try {
      const data = await getLoop2Stats();
      setLoop2Stats(data);
    } catch { /* ignore */ }
  };

  const loadReviewStats = async () => {
    try {
      const data = await getReviewStats();
      setReviewStats(data);
    } catch { /* ignore */ }
  };

  useEffect(() => { loadLoop2Stats(); loadReviewStats(); }, []);

  const SKILL_LABELS: Record<string, string> = {
    strategy_generation: '策略生成',
    annotation: '弹幕生成',
    market_analysis: '市场分析',
    gaming_deep_dive: '游戏深度',
    educational_explainer: '科普教育',
    tech_teardown: '科技拆解',
    chinese_brand_foreign_review: '国产品牌海外评测',
    social_commentary: '社会评论',
    geopolitics_hot_take: '地缘政治',
    challenge_experiment: '挑战实验',
    global_trending_chinese_angle: '全球热点中国角度',
    surveillance_dashcam: '监控/行车记录仪',
  };

  return (
    <div className="skill-evolution">
      <div className="se-header">
        <h2>AI 进化</h2>
      </div>

      {error && <div className="error-banner">{error}</div>}

      {/* Reflection result — structured display */}
      {reflectionResult && (
        <div className="reflection-result">
          <div className="reflection-header">
            <h3>
              {reflectionResult.collected != null
                ? '数据采集完成'
                : reflectionResult.result?.proposed_system_prompt
                  ? `反思提议 (${SKILL_LABELS[reflectionResult.skill_name] || reflectionResult.skill_name})`
                  : '反思结果'}
            </h3>
            <button className="btn-link" onClick={handleDiscardReflection}>关闭</button>
          </div>

          {/* Loop2 collect result */}
          {reflectionResult.collected != null && (
            <div className="reflect-section">
              <div className="reflect-stats">
                <span className="reflect-stat">采集 <strong>{reflectionResult.collected}</strong></span>
                <span className="reflect-stat">更新 <strong>{reflectionResult.updated}</strong></span>
                {reflectionResult.errors > 0 && (
                  <span className="reflect-stat reflect-stat-err">错误 <strong>{reflectionResult.errors}</strong></span>
                )}
              </div>
              {reflectionResult.details?.length > 0 && (
                <ul className="reflect-details">
                  {reflectionResult.details.map((d: string, i: number) => (
                    <li key={i}>{d}</li>
                  ))}
                </ul>
              )}
            </div>
          )}

          {/* Skill reflection result */}
          {reflectionResult.result && typeof reflectionResult.result === 'object' && (() => {
            const r = reflectionResult.result;
            const analysis = typeof r.analysis === 'string' ? r.analysis
              : r.analysis ? JSON.stringify(r.analysis) : null;
            const ytPrinciples = typeof r.updated_youtube_principles === 'string'
              ? r.updated_youtube_principles
              : r.updated_youtube_principles ? JSON.stringify(r.updated_youtube_principles, null, 2) : null;
            const biliPrinciples = typeof r.updated_bilibili_principles === 'string'
              ? r.updated_bilibili_principles
              : r.updated_bilibili_principles ? JSON.stringify(r.updated_bilibili_principles, null, 2) : null;
            const scoringInsights = typeof r.scoring_insights === 'string'
              ? r.scoring_insights
              : r.scoring_insights ? JSON.stringify(r.scoring_insights) : null;
            const updatedPrompt = typeof r.updated_system_prompt === 'string'
              ? r.updated_system_prompt
              : r.updated_system_prompt ? JSON.stringify(r.updated_system_prompt, null, 2) : null;
            const newStrats = Array.isArray(r.new_strategies) ? r.new_strategies : [];
            const retireStrats = Array.isArray(r.retire) ? r.retire : [];
            const channels = Array.isArray(r.channels_to_follow) ? r.channels_to_follow : [];

            return (
              <div className="reflect-section">
                {analysis && (
                  <div className="reflect-block">
                    <h4>分析</h4>
                    <p className="reflect-text">{analysis}</p>
                  </div>
                )}

                {ytPrinciples && (
                  <div className="reflect-block">
                    <h4>更新后的 YouTube 搜索原则</h4>
                    <pre className="reflect-pre">{ytPrinciples}</pre>
                  </div>
                )}

                {newStrats.length > 0 && (
                  <div className="reflect-block">
                    <h4>建议新增策略</h4>
                    <ul className="reflect-list">
                      {newStrats.map((s: any, i: number) => (
                        <li key={i}>{typeof s === 'string' ? s : JSON.stringify(s)}</li>
                      ))}
                    </ul>
                  </div>
                )}
                {retireStrats.length > 0 && (
                  <div className="reflect-block">
                    <h4>建议淘汰策略</h4>
                    <ul className="reflect-list reflect-list-warn">
                      {retireStrats.map((s: any, i: number) => (
                        <li key={i}>{typeof s === 'string' ? s : JSON.stringify(s)}</li>
                      ))}
                    </ul>
                  </div>
                )}
                {channels.length > 0 && (
                  <div className="reflect-block">
                    <h4>建议关注频道</h4>
                    <ul className="reflect-list">
                      {channels.map((c: any, i: number) => (
                        <li key={i}>{typeof c === 'string' ? c : JSON.stringify(c)}</li>
                      ))}
                    </ul>
                  </div>
                )}

                {biliPrinciples && (
                  <div className="reflect-block">
                    <h4>更新后的 B站受众原则</h4>
                    <pre className="reflect-pre">{biliPrinciples}</pre>
                  </div>
                )}
                {scoringInsights && (
                  <div className="reflect-block">
                    <h4>评分洞察</h4>
                    <p className="reflect-text">{scoringInsights}</p>
                  </div>
                )}

                {updatedPrompt && (
                  <div className="reflect-block">
                    <h4>更新后的弹幕提示词</h4>
                    <pre className="reflect-pre">{updatedPrompt}</pre>
                  </div>
                )}

                {/* Fallback: show raw JSON if no known fields matched */}
                {!analysis && !ytPrinciples && !biliPrinciples && !updatedPrompt && (
                  <div className="reflect-block">
                    <h4>原始结果</h4>
                    <pre className="reflect-pre">{JSON.stringify(r, null, 2)}</pre>
                  </div>
                )}
              </div>
            );
          })()}

          {/* Proposed prompt change + approve/edit/discard */}
          {reflectionResult.result?.proposed_system_prompt && (
            <div className="reflect-proposal">
              <h4>提议的 Prompt 变更</h4>
              {editingReflection ? (
                <textarea
                  className="reflect-edit-area"
                  value={reflectEditPrompt}
                  onChange={e => setReflectEditPrompt(e.target.value)}
                  rows={12}
                />
              ) : (
                <pre className="reflect-pre">{reflectionResult.result.proposed_system_prompt}</pre>
              )}
              <div className="reflect-actions">
                <button className="btn-approve-reflect" onClick={handleApproveReflection}>
                  {editingReflection ? '保存并应用' : '批准'}
                </button>
                {!editingReflection && (
                  <button onClick={() => setEditingReflection(true)}>编辑后应用</button>
                )}
                <button className="btn-discard" onClick={handleDiscardReflection}>丢弃</button>
              </div>
            </div>
          )}
        </div>
      )}

      {/* Skills */}
      <div className="se-section">
        <h3>Skills</h3>
        {skills.length === 0 ? (
          <div className="empty">没有已注册的 Skill</div>
        ) : (
          <div className="skill-list">
            {skills.map(skill => (
              <div key={skill.name} className="skill-card">
                <div className="skill-card-header">
                  <div>
                    <span className="skill-name">{SKILL_LABELS[skill.name] || skill.name}</span>
                    <span className="skill-version">v{skill.version}</span>
                    <span className="skill-key">{skill.name}</span>
                  </div>
                  <div className="skill-actions">
                    {skill.name === 'strategy_generation' && (
                      <>
                        <button onClick={() => handleReflect(skill.name, 'yield')}
                                disabled={loading !== null}>
                          {loading === `reflect-${skill.name}` ? '反思中...' : 'Yield反思'}
                        </button>
                        <button onClick={() => handleReflect(skill.name, 'outcome')}
                                disabled={loading !== null}>
                          Outcome反思
                        </button>
                      </>
                    )}
                    {skill.name === 'annotation' && (
                      <button onClick={() => handleReflect(skill.name, 'feedback')}
                              disabled={loading !== null}>
                        {loading === `reflect-${skill.name}` ? '反思中...' : '弹幕反思'}
                      </button>
                    )}
                    <button onClick={() => handleEdit(skill)}>编辑</button>
                    <button onClick={() => loadVersions(skill.name)}>
                      {expanded === skill.name ? '收起历史' : '版本历史'}
                    </button>
                  </div>
                </div>

                {/* Edit mode */}
                {editing === skill.name && (
                  <div className="skill-edit">
                    <h4>System Prompt</h4>
                    <textarea
                      value={editPrompt}
                      onChange={e => setEditPrompt(e.target.value)}
                      rows={8}
                    />
                    <div className="skill-edit-actions">
                      <button onClick={() => handleSaveEdit(skill.name)}>保存</button>
                      <button onClick={() => setEditing(null)}>取消</button>
                    </div>
                  </div>
                )}

                {/* Current prompt preview */}
                {editing !== skill.name && (
                  <div className="skill-prompt-preview">
                    <pre>{skill.system_prompt.slice(0, 300)}{skill.system_prompt.length > 300 ? '...' : ''}</pre>
                  </div>
                )}

                {/* Version history */}
                {expanded === skill.name && (
                  <div className="version-list">
                    <h4>版本历史</h4>
                    {(versions[skill.name] || []).length === 0 ? (
                      <div className="empty">没有历史版本</div>
                    ) : (
                      (versions[skill.name] || []).map(v => (
                        <div key={v.version} className="version-item">
                          <div className="version-info">
                            <span className="version-num">v{v.version}</span>
                            <span className="version-by">{v.changed_by}</span>
                            <span className="version-time">{v.created_at}</span>
                          </div>
                          <div className="version-reason">{v.change_reason?.slice(0, 200)}</div>
                          <button className="btn-link" onClick={() => handleRollback(skill.name, v.version)}>
                            回滚到此版本
                          </button>
                        </div>
                      ))
                    )}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Review Stats */}
      <div className="se-section">
        <h3>策略通过率</h3>
        {reviewStats.length === 0 ? (
          <div className="empty">暂无 review 数据</div>
        ) : (
          <div className="loop2-table">
            <table>
              <thead>
                <tr>
                  <th>策略</th>
                  <th>总数</th>
                  <th>通过</th>
                  <th>拒绝</th>
                  <th>通过率</th>
                </tr>
              </thead>
              <tbody>
                {reviewStats.map((row) => (
                  <tr key={row.strategy_name}>
                    <td>{SKILL_LABELS[row.strategy_name] || row.strategy_name}</td>
                    <td>{row.total}</td>
                    <td>{row.approved}</td>
                    <td>{row.rejected}</td>
                    <td>
                      <span className={`outcome-badge ${row.approval_rate >= 60 ? 'success' : row.approval_rate >= 30 ? 'pending' : 'failure'}`}>
                        {row.approval_rate}%
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Loop 2 Stats */}
      <div className="se-section">
        <div className="se-section-header">
          <h3>B站数据回流 (Loop 2)</h3>
          <button onClick={handleCollectLoop2} disabled={loop2Loading}>
            {loop2Loading ? '采集中...' : '采集数据'}
          </button>
        </div>
        {loop2Stats.length === 0 ? (
          <div className="empty">暂无 outcome 数据</div>
        ) : (
          <div className="loop2-table">
            <table>
              <thead>
                <tr>
                  <th>策略</th>
                  <th>YouTube标题</th>
                  <th>B站BV号</th>
                  <th>B站播放量</th>
                  <th>结果</th>
                </tr>
              </thead>
              <tbody>
                {loop2Stats.map((row, i) => (
                  <tr key={i}>
                    <td>{row.strategy_name || '-'}</td>
                    <td className="td-title">{row.youtube_title || '-'}</td>
                    <td>
                      {row.bilibili_bvid ? (
                        <a href={`https://www.bilibili.com/video/${row.bilibili_bvid}`}
                           target="_blank" rel="noopener noreferrer">
                          {row.bilibili_bvid}
                        </a>
                      ) : '-'}
                    </td>
                    <td>{row.bilibili_views != null ? row.bilibili_views.toLocaleString() : '待采集'}</td>
                    <td>
                      <span className={`outcome-badge ${row.outcome || 'pending'}`}>
                        {row.outcome === 'success' ? '成功' : row.outcome === 'failure' ? '失败' : '待定'}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
