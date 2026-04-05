import { useState } from "react";
import type { StrategyOption } from "../api";

interface Props {
  strategies: StrategyOption[];
  onConfirm: (selected: string[]) => void;
}

const LABELS: Record<string, string> = {
  gaming_deep_dive: "Gaming Deep Dive",
  educational_explainer: "Educational Explainer",
  tech_teardown: "Tech Teardown",
  chinese_brand_foreign_review: "Chinese Brand Foreign Review",
  social_commentary: "Social Commentary",
  geopolitics_hot_take: "Geopolitics Hot Take",
  challenge_experiment: "Challenge / Experiment",
  global_trending_chinese_angle: "Global Trending (CN Angle)",
  surveillance_dashcam: "Surveillance / Dashcam",
};

export default function StrategyPicker({ strategies, onConfirm }: Props) {
  const [selected, setSelected] = useState<Set<string>>(new Set());

  const toggle = (name: string) => {
    setSelected(prev => {
      const next = new Set(prev);
      next.has(name) ? next.delete(name) : next.add(name);
      return next;
    });
  };

  const toggleAll = () => {
    if (selected.size === strategies.length) {
      setSelected(new Set());
    } else {
      setSelected(new Set(strategies.map(s => s.name)));
    }
  };

  const allSelected = selected.size === strategies.length;

  return (
    <div className="strategy-picker">
      <div className="strategy-picker-header">
        <h2>Select Strategies</h2>
        <button className="btn-link" onClick={toggleAll}>
          {allSelected ? 'Deselect all' : 'Select all'}
        </button>
      </div>
      <div className="strategy-list">
        {strategies.map(s => (
          <label
            key={s.name}
            className={`strategy-item ${selected.has(s.name) ? 'selected' : ''}`}
          >
            <input
              type="checkbox"
              checked={selected.has(s.name)}
              onChange={() => toggle(s.name)}
            />
            <div className="strategy-info">
              <span className="strategy-name">{LABELS[s.name] || s.name}</span>
              <span className="strategy-key">{s.name}</span>
            </div>
            <span className="query-count">{s.query_count} queries</span>
          </label>
        ))}
      </div>
      <button
        className="btn-confirm"
        onClick={() => onConfirm([...selected])}
        disabled={selected.size === 0}
      >
        Confirm ({selected.size} selected)
      </button>
    </div>
  );
}
