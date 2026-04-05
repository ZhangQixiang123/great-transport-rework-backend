import { useState } from 'react';

interface Props {
  onPipelineStart: (backend: string, dryRun: boolean) => void;
  disabled: boolean;
}

export default function ServiceControls({ onPipelineStart, disabled }: Props) {
  const [backend, setBackend] = useState('ollama');
  const [dryRun, setDryRun] = useState(false);

  return (
    <div className="service-controls">
      <div className="control-row">
        <select value={backend} onChange={e => setBackend(e.target.value)}>
          <option value="ollama">Ollama</option>
          <option value="openai">OpenAI</option>
          <option value="anthropic">Anthropic</option>
        </select>

        <label className="checkbox-label">
          <input type="checkbox" checked={dryRun} onChange={e => setDryRun(e.target.checked)} />
          Dry run
        </label>

        <button
          onClick={() => onPipelineStart(backend, dryRun)}
          disabled={disabled}
        >
          {disabled ? 'Running...' : 'Start Run'}
        </button>
      </div>
    </div>
  );
}
