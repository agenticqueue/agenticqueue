"use client";

type JobDetailPanelTone = "ok" | "info" | "warn" | "danger" | "mute";

type JobDetailPanelState =
  | "running"
  | "failed"
  | "review"
  | "queued"
  | "blocked"
  | "done";

type JobDetailPanelRelation = {
  ref: string;
};

export type JobDetailPanelJob = {
  ref: string;
  title: string;
  task_type: string;
  status: JobDetailPanelState;
  priority: number;
  labels: string[];
  description: string | null;
  claimed_by_actor_id: string | null;
  updated_at: string;
  parent_ref: string | null;
  child_refs: string[];
  depends_on: JobDetailPanelRelation[];
  blocked_by: JobDetailPanelRelation[];
  blocks: JobDetailPanelRelation[];
};

type JobDetailPanelProps = {
  job: JobDetailPanelJob;
  onSelect: (ref: string) => void;
  pipelineName: string;
};

export function JobDetailPanel({
  job,
  onSelect,
  pipelineName,
}: JobDetailPanelProps) {
  return (
    <article className="aq-job-detail">
      <div className="aq-job-detail-head">
        <div>
          <p className="aq-auth-kicker">Selected job</p>
          <h3 className="aq-job-detail-title">{job.title}</h3>
        </div>
        <div className="aq-job-detail-status">
          <ToneChip label={job.status} tone={jobStateTone(job.status)} />
          <span className="aq-mono aq-job-detail-ref">{job.ref}</span>
        </div>
      </div>

      <p className="aq-job-detail-copy">
        {job.description?.trim() ||
          `This job belongs to ${pipelineName} and is visible here strictly for execution-chain inspection.`}
      </p>

      <div className="aq-job-detail-grid">
        <div className="aq-job-detail-prop">
          <span className="aq-job-detail-key">task type</span>
          <span className="aq-job-detail-value aq-mono">{job.task_type}</span>
        </div>
        <div className="aq-job-detail-prop">
          <span className="aq-job-detail-key">claimed by</span>
          <span className="aq-job-detail-value aq-mono">
            {formatActor(job.claimed_by_actor_id)}
          </span>
        </div>
        <div className="aq-job-detail-prop">
          <span className="aq-job-detail-key">updated</span>
          <span className="aq-job-detail-value aq-mono">
            {formatTimestamp(job.updated_at)}
          </span>
        </div>
        <div className="aq-job-detail-prop">
          <span className="aq-job-detail-key">priority</span>
          <span className="aq-job-detail-value aq-mono">{job.priority}</span>
        </div>
      </div>

      <div className="aq-job-detail-relations">
        <RelationRow
          label="parent"
          onSelect={onSelect}
          refs={job.parent_ref ? [job.parent_ref] : []}
        />
        <RelationRow
          label="depends on"
          onSelect={onSelect}
          refs={job.depends_on.map((relation) => relation.ref)}
        />
        <RelationRow
          label="blocked by"
          onSelect={onSelect}
          refs={job.blocked_by.map((relation) => relation.ref)}
        />
        <RelationRow
          label="blocks"
          onSelect={onSelect}
          refs={job.blocks.map((relation) => relation.ref)}
        />
        <RelationRow
          label="children"
          onSelect={onSelect}
          refs={job.child_refs}
        />
      </div>

      {job.labels.length > 0 ? (
        <div className="aq-job-detail-labels">
          {job.labels.map((label) => (
            <ToneChip key={label} label={label} tone="mute" />
          ))}
        </div>
      ) : null}

      <div className="aq-job-detail-callout">
        <span className="aq-mono aq-mute">
          Writes are disabled here. Use `aq` or MCP for task actions.
        </span>
      </div>
    </article>
  );
}

type RelationRowProps = {
  label: string;
  onSelect: (ref: string) => void;
  refs: string[];
};

function RelationRow({ label, onSelect, refs }: RelationRowProps) {
  if (refs.length === 0) {
    return null;
  }

  return (
    <div className="aq-job-detail-relrow">
      <span className="aq-job-detail-key">{label}</span>
      <div className="aq-job-detail-relbuttons">
        {refs.map((ref) => (
          <button
            className="aq-prop-link aq-mono"
            key={`${label}-${ref}`}
            onClick={() => onSelect(ref)}
            type="button"
          >
            {ref}
          </button>
        ))}
      </div>
    </div>
  );
}

function ToneChip({
  label,
  tone,
}: {
  label: string;
  tone: JobDetailPanelTone;
}) {
  return <span className={`aq-tone aq-tone-${tone}`}>{label}</span>;
}

function formatTimestamp(value: string) {
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(new Date(value));
}

function formatActor(actorId: string | null) {
  return actorId ? `@${actorId.slice(0, 8)}` : "unclaimed";
}

function jobStateTone(state: JobDetailPanelState): JobDetailPanelTone {
  if (state === "done") {
    return "ok";
  }
  if (state === "failed") {
    return "danger";
  }
  if (state === "review" || state === "blocked") {
    return "warn";
  }
  if (state === "running") {
    return "info";
  }
  return "mute";
}
