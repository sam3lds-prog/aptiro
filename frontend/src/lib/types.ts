/**
 * Hand-maintained TypeScript types matching the FastAPI schemas in
 * backend/app.py. Kept narrow on purpose — only the fields the UI uses
 * are typed. Anything else is permitted via `& Record<string, unknown>`.
 */

export type ProvenanceColor = "blue" | "purple" | "green" | "orange" | "red";

export type ApprovalStatus = "pending" | "approved" | "rejected" | "do_not_use" | "edited";

export type BulletStatus = "drafted" | "accepted" | "rejected" | "rewritten" | "locked";

export type WorkMode = "any" | "remote" | "hybrid" | "onsite";

export type Aggressiveness = "conservative" | "balanced" | "aggressive";

export type SourceType = "resume" | "linkedin" | "portfolio" | "public_article" | "manual_note";

export type ApplicationStatus =
  | "drafted" | "exported" | "submitted_by_user" | "interviewing"
  | "offer" | "rejected" | "withdrawn";

export interface SourceRefRow {
  section: string;
  snippet: string;
  page?: number | null;
  confidence: number;
}

export interface Claim {
  id: string;
  source_id: string;
  claim_text: string;
  provenance_color: ProvenanceColor;
  approval_status: ApprovalStatus;
  confidence: number;
  company?: string | null;
  role?: string | null;
  date_range?: string | null;
  metrics?: string[];
  skills?: string[];
  source_refs?: SourceRefRow[];
}

export interface SourceRow {
  id: string;
  source_type: SourceType;
  label: string;
  filename?: string | null;
  parse_meta?: { format?: string; pages?: number } & Record<string, unknown>;
  claim_count: number;
  created_at: string;
}

export interface Strategy {
  id: string;
  name: string;
  target_roles: string[];
  region?: string | null;
  work_mode: WorkMode;
  salary_min?: number | null;
  salary_max?: number | null;
  aggressiveness: Aggressiveness;
  weights: Record<string, number>;
  include_companies: string[];
  exclude_companies: string[];
  targeting_notes: string;
  is_active: boolean;
  updated_at: string;
}

export interface Job {
  id: string;
  title: string;
  company: string;
  location?: string | null;
  work_mode: WorkMode;
  salary_min?: number | null;
  salary_max?: number | null;
  source: string;
  source_url?: string | null;
  description_text: string;
  requirements: string[];
  structured_requirements?: Record<string, unknown>;
  is_archived: boolean;
  posted_at?: string | null;
  imported_at: string;
}

export interface ScoreComponent {
  key: string;
  label: string;
  weight: number;
  earned: number;
  detail: string;
  evidence?: { claim_id: string; snippet: string }[];
}

export interface SemanticSignal {
  provider: string;
  similarity: number;
  affects_score: boolean;
  agreement: string;
  note: string;
}

export interface Match {
  job: Job;
  score: number;
  earned_points: number;
  max_points: number;
  components: ScoreComponent[];
  matched_skills: string[];
  missing_requirements: string[];
  excluded: boolean;
  summary: string;
  semantic?: SemanticSignal | null;
}

export interface PackageBullet {
  id: string;
  section: string;
  current_text: string;
  original_text?: string | null;
  order_index: number;
  status: BulletStatus;
  provenance_color: ProvenanceColor;
  provenance_label?: string;
  claim_id?: string | null;
  source_snippet?: string | null;
  flagged?: string[];
}

export interface PackageDetail {
  id: string;
  title: string;
  company: string;
  job_id: string;
  score_snapshot: number;
  status: string;
  summary?: string;
  bullets: PackageBullet[];
  created_at?: string;
}

export interface PackageListItem {
  id: string;
  title: string;
  company: string;
  score_snapshot: number;
  status: string;
}

export interface ExportPreview {
  title: string;
  company: string;
  job_title: string;
  score: number;
  summary?: string;
  generated_at: string;
  include_unsupported: boolean;
  sections: Record<string, {
    text: string;
    provenance: string;
    color: ProvenanceColor;
    status: string;
    flagged?: string[];
  }[]>;
  excluded: { text: string; section: string; reasons: string[] }[];
}

export interface Application {
  id: string;
  package_id: string;
  job_title: string;
  company: string;
  status: ApplicationStatus;
  applied_at?: string | null;
  submitted_at?: string | null;
  snapshot_sha?: string | null;
  history: { status: string; at: string; note?: string }[];
  reminders: { id: string; due_at: string; offset_days: number; kind: string; message: string; done: boolean }[];
  notes?: string;
}

export interface ApplySession {
  id: string;
  package_id: string;
  job_title: string;
  company: string;
  state: string;
  requires_handoff: boolean;
  note: string;
  plan: { step: number; field: string; value: string; source: string; needs_user: boolean; note?: string }[];
  history: { state: string; note: string; at: string }[];
  allowed_actions: string[];
  guardrails: string[];
  created_at: string;
}

export interface OnboardingStatus {
  completed: number;
  total: number;
  steps: { key: string; label: string; done: boolean }[];
  next_step?: string | null;
}

export interface Health {
  status: string;
  app: string;
  slice?: string;
  phase?: number;
  phases_shipped?: number[];
  ingestion_formats?: string[];
  export_formats?: string[];
  providers?: { ai?: string; embedding?: string; job?: string; search?: string; notification?: string };
  auth?: { enabled: boolean };
  ai_assist?: { grounding_gate: boolean; auto_apply: boolean };
  application_tracker?: { auto_submit: boolean; immutable_snapshot: boolean };
}

export interface AuditEvent {
  id: string;
  method: string;
  path: string;
  status: number;
  duration_ms: number;
  at: string;
  request_id: string;
}

export interface Me {
  id: string;
  email: string;
  name: string;
  is_default: boolean;
  auth_enabled: boolean;
}

export interface AuthSuccess {
  id: string;
  email: string;
  name: string;
  token: string;
}
